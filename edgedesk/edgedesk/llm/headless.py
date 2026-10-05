"""Calling Claude through Claude Code, on the Max subscription.

Ported from a headless Claude pattern proven in an earlier project of the author's.
Two paths behind one function:

* **warm** - a persistent `ClaudeSDKClient`. The CLI process stays alive between
  calls, which matters when a weekly run makes forty of them. Measured faster,
  but observed to stop mid-reply on long JSON while still reporting success, so
  it is used for first attempts only.
* **cold** - a one-shot `claude -p` subprocess with the prompt on stdin. Slower
  and reliable, which is why every retry uses it.

**The credentials rule.** `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` must be
absent from the CHILD environment, or Claude Code prefers the metered API and
these calls quietly cost money. The cold path is safe because
`create_subprocess_exec` replaces the environment outright. The warm path is
not: the SDK builds the child environment as `{**os.environ, **options.env}`, so
a dict that merely omits the keys leaves the inherited ones in place. Both are
handled below, and a test asserts it.

**No tools.** These calls reason over evidence that is handed to them in the
prompt. They are given no file, shell or web access, because a model that can go
and look things up can go and find a number the run never saw, and the whole
point of this layer is that it explains the evidence rather than adding to it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_HOME = str(Path.home())

# What the model may reach, on both call paths: nothing but the prompt. No tools, no user or project
# settings (so no CLAUDE.md and no hooks), no MCP servers, nothing left behind in session history.
# The README promises this. Until 2026-09-25 the cold path, the one every batch call takes, ran with
# every tool available and permissions bypassed (audit R2-05).
ISOLATION = ["--tools", "", "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence"]

# Pinned, not inherited. Without --model, `claude -p` runs on the user's own Claude Code default
# (settings.json said "sonnet" on 2026-09-24), so changing that for everyday coding would
# silently change the model behind every report in the middle of a forward test.
MODEL = os.environ.get("EDGE_DESK_MODEL", "claude-sonnet-5")

try:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    _SDK_OK = True
except Exception:                                          # noqa: BLE001
    _SDK_OK = False

# Their presence in the child makes Claude Code bill the metered API.
CRED_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

DEFAULT_TIMEOUT = 240

# How Claude Code says the subscription is spent. Worth recognizing precisely:
# the reply is plain prose, so a caller that only knows how to parse JSON reports
# "no JSON object could be parsed", which sends whoever reads the log looking at
# the schema instead of the clock.
#
# Specific on purpose, and the same patterns as the desk's own layer (desk/maxplan.py): "You've hit
# your session limit · resets 7:20pm", "Claude AI usage limit reached|<time>", "5-hour limit
# reached ∙ resets 3pm". A bare "rate limit", "too many requests" or "please try again later" is
# prose about something else, or a passing throttle, and used to count as the subscription spent.
_LIMIT = re.compile(
    r"\b(?:hit|reached) your (?:(?:session|weekly|daily|monthly|usage|5-hour|opus) )?limit\b"
    r"|\busage limit reached\b|\blimit reached\s*[·∙|:-]\s*resets\b|\blimit will reset\b"
    r"|\bupgrade to increase your usage\b"
    # An expired login stops the batch the same way (AKAM 2026-10-04: all ten written sections
    # failed with it and the report was still saved). Retrying will not help until a new login.
    r"|\bfailed to authenticate\b|\boauth (?:session|token) (?:has )?expired\b|\bplease run /login\b"
    # The ChatGPT plan's wording for the same two cases.
    r"|\bnot logged in\b|\brun `?codex login\b|\b401 unauthorized\b|\btoken (?:has )?expired\b", re.I)


class UsageLimit(RuntimeError):
    """The subscription is out of capacity. Not a bug, and not worth retrying."""


def looks_like_a_limit(text: str, failed: bool = False) -> bool:
    """A limit notice. From a call that failed, any length counts; from one that answered, only a
    short reply that IS the notice, so a report that mentions a limit is never mistaken for one."""
    if not text or not _LIMIT.search(text):
        return False
    return failed or len(text) < 600


def child_env() -> dict:
    env = dict(os.environ)
    for key in CRED_VARS:
        env.pop(key, None)
    return env


# ---------------------------------------------------------------- warm path

_client = None
_lock = asyncio.Lock()


async def _get_client():
    global _client
    if _client is None:
        if not _SDK_OK:
            raise RuntimeError("claude_agent_sdk is not installed")
        stashed = {k: os.environ.pop(k) for k in CRED_VARS if k in os.environ}
        try:
            client = ClaudeSDKClient(options=ClaudeAgentOptions(
                cwd=_HOME,
                env={**child_env(), **{k: "" for k in CRED_VARS}},
                allowed_tools=[],
                tools=[],
                setting_sources=[],
                mcp_servers={},
                strict_mcp_config=True,
                model=MODEL,
            ))
            await client.connect()
        finally:
            os.environ.update(stashed)
        _client = client
        logger.info("warm Claude session connected, credentials withheld")
    return _client


async def _drop_client() -> None:
    """A query that did not reach its result leaves unread messages in the shared
    pipe, so any error tears the session down rather than handing leftovers to
    the next caller."""
    global _client
    client, _client = _client, None
    if client is not None:
        try:
            await client.disconnect()
        except Exception:                                  # noqa: BLE001
            pass


async def _ask_warm(prompt: str, timeout: int) -> str:
    client = await _get_client()
    await client.query(prompt)
    texts: list[str] = []

    async def collect() -> None:
        async for msg in client.receive_response():
            kind = type(msg).__name__
            if kind == "AssistantMessage":
                for block in getattr(msg, "content", None) or []:
                    text = getattr(block, "text", None)
                    if text:
                        texts.append(text)
            elif kind == "ResultMessage":
                break

    await asyncio.wait_for(collect(), timeout)
    return "\n".join(texts).strip()


# ---------------------------------------------------------------- cold path

def _invocation() -> list[str]:
    """The native claude.exe when it is installed, as the desk does (desk/maxplan.py). The npm .cmd
    shim needs cmd.exe, which mangles arguments, and ISOLATION includes an empty one."""
    claude = shutil.which("claude") or "claude"
    native = Path(claude).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    if native.exists():
        return [str(native)]
    # `claude` is an npm .cmd shim, which CreateProcess cannot launch directly.
    if sys.platform == "win32" and claude.lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c", claude]
    return [claude]


async def _ask_cold(prompt: str, timeout: int, model: str | None) -> tuple[bool, str]:
    """The prompt goes in on stdin, never as an argument.

    Passing a few thousand characters of multi-line prompt as argv through
    `cmd.exe /c` silently mangles it: the observed failure was the model replying
    "Which this? No ticker came through with the message", because the argument
    had been chopped at the first thing cmd decided to interpret. Piping is both
    safe and unlimited.
    """
    cmd = [*_invocation(), "-p", *ISOLATION, "--output-format", "json", "--model", model or MODEL]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=_HOME, env=child_env(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except OSError as exc:
        return False, f"could not launch the claude CLI ({exc})"
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(prompt.encode("utf-8")), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return False, "timed out"
    return read_reply(proc.returncode, out, err)


def read_reply(code: int | None, out: bytes | None, err: bytes | None) -> tuple[bool, str]:
    """(ok, text) from the CLI's JSON envelope. An answer needs a clean exit, no error flag and some
    text. The exit code used to be ignored, so a failed process's output passed as a reply (R2-11);
    a failure now comes back with ok False, which is what lets a long limit notice be caught."""
    raw = (out or b"").decode("utf-8", "replace").strip()
    note = (err or b"").decode("utf-8", "replace").strip()
    try:
        payload = json.loads(raw[raw.index("{"):]) if "{" in raw else {}
    except json.JSONDecodeError:
        payload = {}
    text = payload.get("result").strip() if isinstance(payload.get("result"), str) else ""
    if code == 0 and payload and not payload.get("is_error") and text:
        return True, text
    return False, text or note or raw or f"the claude CLI exited {code} with no output"


# ---------------------------------------------------------------- public

async def ask_async(prompt: str, timeout: int = DEFAULT_TIMEOUT,
                    model: str | None = None, warm: bool = True) -> tuple[bool, str]:
    """(ok, text), or UsageLimit when the reply is the subscription saying it is spent.

    Raised here, where every call passes, because until 2026-09-24 nothing raised it at all: the
    analysis had a handler that stops the batch, and it never fired. A spent allowance showed up
    as ten "no JSON could be parsed" sections, and that half-written report was saved and reused."""
    ok, text = await _ask_any(prompt, timeout, model, warm)
    if looks_like_a_limit(text, failed=not ok):
        raise UsageLimit(text[:300])
    return ok, text


def plan() -> str:
    """claude (default) or chatgpt: EDGE_DESK_PLAN, else the desk's DESK_PLAN."""
    p = (os.environ.get("EDGE_DESK_PLAN") or os.environ.get("DESK_PLAN") or "claude").strip().lower()
    return "chatgpt" if p in ("chatgpt", "codex", "openai") else "claude"


async def _ask_chatgpt(prompt: str, timeout: int, model: str | None) -> tuple[bool, str]:
    from edgedesk.llm import chatgpt
    try:
        r = await asyncio.to_thread(chatgpt.run, prompt, "Answer from the material given, in the shape asked.",
                                    chatgpt.model_for(model or MODEL), None, None, timeout)
    except chatgpt.ChatGPTError as exc:
        return False, str(exc)
    if r["stray"]:
        return False, f"codex exec used {', '.join(sorted(set(r['stray'])))}; an Edge call may only answer"
    if r["failed"]:
        return False, r["error"] or r["text"] or "codex exec returned no answer"
    return True, r["text"]


async def _ask_any(prompt: str, timeout: int, model: str | None, warm: bool) -> tuple[bool, str]:
    if plan() == "chatgpt":
        return await _ask_chatgpt(prompt, timeout, model)
    if warm and _SDK_OK:
        async with _lock:
            try:
                text = await _ask_warm(prompt, timeout)
                if text:
                    return True, text
                logger.info("warm call returned nothing, falling back to a fresh process")
            except Exception as exc:                       # noqa: BLE001
                logger.info("warm call failed (%s), falling back", exc)
            await _drop_client()
    return await _ask_cold(prompt, timeout, model)


def ask(prompt: str, timeout: int = DEFAULT_TIMEOUT, model: str | None = None,
        warm: bool = False) -> tuple[bool, str]:
    """Blocking wrapper. Returns (ok, text).

    Cold by default, and that is deliberate. `asyncio.run` builds a new event
    loop per call, while the warm client belongs to the loop that created it, so
    a module-level warm session reused across calls is talking to a closed loop
    from the second call onward. The observed symptom was a first reply that
    stopped mid-JSON while reporting success, then a stream of "event loop is
    closed" errors. A caller that owns a long-lived loop can still have the warm
    path by awaiting `ask_async(..., warm=True)` itself; nothing in a batch job
    does, so nothing in a batch job asks for it.
    """
    return asyncio.run(ask_async(prompt, timeout, model, warm))


def shutdown() -> None:
    """Close the warm session at the end of a batch."""
    if _client is not None:
        try:
            asyncio.run(_drop_client())
        except Exception:                                  # noqa: BLE001
            pass


def available() -> tuple[bool, str]:
    """Is there a CLI for the chosen plan to call, and would it bill the subscription?"""
    if plan() == "chatgpt":
        from edgedesk.llm import chatgpt
        return chatgpt.available()
    claude = shutil.which("claude")
    if not claude:
        return False, ("the claude CLI is not on PATH; install Claude Code or run the "
                       "engine without the LLM layer")
    leaked = [k for k in CRED_VARS if os.environ.get(k)]
    note = (f"{claude} (stripping {', '.join(leaked)} from the child)" if leaked
            else f"{claude}")
    return True, note
