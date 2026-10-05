"""Model calls on a subscription plan: Claude (Max) through `claude -p`, or ChatGPT through
`codex exec` (desk/chatgpt.py) when DESK_PLAN=chatgpt. One plan per run.

The Claude path:

Every model call in a desk run goes through here: the desk's own calls, TradingAgents (via
desk/max_chat.py) and ai-hedge-fund (via aihf_runner). Nothing is billed to an API key.

How a call is made, and why:
  - the native claude.exe is launched directly, not the npm .cmd shim: cmd.exe mangles long or
    quote-heavy arguments, and the JSON schema travels as an argument;
  - the prompt goes in on stdin, never as an argument, for the same reason;
  - ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN are removed from the child's environment, or
    Claude Code would prefer the metered API and quietly bill it;
  - `--tools ""`, `--setting-sources ""` and `--strict-mcp-config`: no tools, no CLAUDE.md, no
    MCP servers. The model reasons over what the prompt hands it and nothing else, and the
    call carries about 1.4k tokens of overhead instead of a full coding-agent context;
  - `--system-prompt-file` replaces Claude Code's own system prompt with the caller's. It is
    the one part of a `-p` call that is cached, so stable bulk (the reports) belongs there;
  - `--json-schema` makes Claude Code validate structured output before returning it, and the
    answer is checked again here (`schema_problem`), because the fallback that reads JSON out of
    the text had no check at all and once accepted `rating: Potato`;
  - `--no-session-persistence`: these calls leave nothing behind in ~/.claude.

`total_cost_usd` in the reply is the list price the call would have cost on the API. It is
recorded as a notional figure so a run can say what the subscription saved; nothing is charged.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

CRED_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
DEFAULT_TIMEOUT = 900
# A subscription limit, as Claude Code words it: "You've hit your session limit · resets 3pm",
# "Claude AI usage limit reached|<time>", "5-hour limit reached ∙ resets 3pm", "You've reached your
# weekly limit". Specific on purpose. A bare "rate limit" or "too many requests" is either prose
# (an analyst noting that Reddit throttled it) or a passing API throttle worth a retry, and reading
# it as exhaustion used to stop whole runs.
_LIMIT = re.compile(
    r"\b(?:hit|reached) your (?:(?:session|weekly|daily|monthly|usage|5-hour|opus) )?limit\b"
    r"|\busage limit reached\b|\blimit reached\s*[·∙|:-]\s*resets\b|\blimit will reset\b"
    r"|\bupgrade to increase your usage\b"
    # An expired login stops a run the same way (AKAM 2026-10-04: every call in all three teams
    # failed with it, and Edge saved a report whose written sections were all empty). Retrying
    # will not help until the user logs in again.
    r"|\bfailed to authenticate\b|\boauth (?:session|token) (?:has )?expired\b|\bplease run /login\b"
    # The ChatGPT plan's wording for the same two cases.
    r"|\bnot logged in\b|\brun `?codex login\b|\b401 unauthorized\b|\btoken (?:has )?expired\b", re.I)

# Friendly names -> ids the CLI accepts. Anything else is passed through unchanged.
MODELS = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5", "haiku": "claude-haiku-4-5-20251001"}


class UsageLimit(RuntimeError):
    """The subscription is out of capacity for now. Retrying will not help."""


class MaxCallError(RuntimeError):
    """The call did not produce a usable answer after its retry."""


class SchemaUnsupported(ValueError):
    """A schema uses a rule schema_problem cannot check. Raised before any call is made: silently
    skipping the rule would weaken the check without anyone noticing (audit R2-12)."""


@dataclass
class Reply:
    text: str
    data: dict | None = None
    model: str = ""
    seconds: float = 0.0
    usage: dict = field(default_factory=dict)
    notional_usd: float = 0.0


# One tally per process, so a runner can report calls, tokens and notional cost at the end.
CALLS: list[dict] = []
_LOCK = threading.Lock()


def claude_exe() -> list[str]:
    shim = shutil.which("claude")
    if shim:
        native = Path(shim).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if native.exists():
            return [str(native)]
        if sys.platform == "win32" and shim.lower().endswith((".cmd", ".bat")):
            return ["cmd.exe", "/c", shim]
        return [shim]
    raise MaxCallError("The claude CLI is not on PATH. Install Claude Code, or set DESK_LLM=api.")


def child_env() -> dict:
    env = dict(os.environ)
    for key in CRED_VARS:
        env.pop(key, None)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def resolve(model: str) -> str:
    return MODELS.get(model, model)


def _looks_like_limit(text: str, failed: bool) -> bool:
    """A limit notice. In a call that failed, any length counts (long notices used to slip past
    an 800-character cap). In a call that returned an answer, only a short reply that IS the notice
    counts, so an answer that merely mentions a limit is never mistaken for one."""
    if not text or not _LIMIT.search(text):
        return False
    return failed or len(text) < 400


def first_limit_notice(texts) -> str | None:
    """The first usage-limit or expired-login notice among failure messages, or None. For callers
    whose own code swallows model errors (ai-hedge-fund's personas abstain on any failure), so the
    runner can stop instead of saving a rating built without its model calls."""
    for t in texts:
        if t and _looks_like_limit(t, failed=True):
            return t
    return None


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is(value, kind: str) -> bool:
    if kind == "integer":
        return (isinstance(value, int) and not isinstance(value, bool)) or (
            isinstance(value, float) and value.is_integer())
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    py = _TYPES.get(kind)
    return True if py is None else isinstance(value, py)


def schema_problem(value, schema: dict, path: str = "$") -> str | None:
    """Why `value` does not fit `schema`, or None.

    Covers the rules in VALIDATION, which is what the desk's schemas, TradingAgents' output models
    and max_chat's tool-call envelope use. A schema with any other rule never reaches this point:
    ask() refuses it first (unsupported_keywords), so nothing is silently left unchecked. Written
    here rather than imported: this module runs in three virtualenvs, one of them TradingAgents' own,
    and none of them has jsonschema."""
    if not isinstance(schema, dict):
        return None
    if schema.get("anyOf") and all(schema_problem(value, s, path) for s in schema["anyOf"]):
        return f"{path} matches none of its allowed shapes"
    if schema.get("oneOf"):
        fits = sum(1 for s in schema["oneOf"] if not schema_problem(value, s, path))
        if fits != 1:
            return f"{path} matches {fits} of its oneOf shapes; exactly one is required"
    for sub in schema.get("allOf") or []:
        problem = schema_problem(value, sub, path)
        if problem:
            return problem
    if "const" in schema and value != schema["const"]:
        return f"{path} must be {schema['const']!r}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path} is {value!r}, not one of {schema['enum']}"
    kinds = schema.get("type")
    if kinds is not None:
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if not any(_is(value, k) for k in kinds):
            return f"{path} should be {' or '.join(kinds)}, not {type(value).__name__}"
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            return f"{path} is shorter than {schema['minLength']} characters"
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return f"{path} is longer than {schema['maxLength']} characters"
    if _is(value, "number"):
        if "minimum" in schema and value < schema["minimum"]:
            return f"{path} is {value}, below {schema['minimum']}"
        if "maximum" in schema and value > schema["maximum"]:
            return f"{path} is {value}, above {schema['maximum']}"
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        missing = [k for k in schema.get("required") or [] if k not in value]
        if missing:
            return f"{path} is missing {', '.join(missing)}"
        extra = schema.get("additionalProperties", True)
        for k, v in value.items():
            if k in props:
                problem = schema_problem(v, props[k], f"{path}.{k}")
            elif extra is False:
                problem = f"{path} has an unexpected field {k!r}"
            else:
                problem = schema_problem(v, extra, f"{path}.{k}") if isinstance(extra, dict) else None
            if problem:
                return problem
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            return f"{path} has {len(value)} items, fewer than {schema['minItems']}"
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return f"{path} has {len(value)} items, more than {schema['maxItems']}"
        if isinstance(schema.get("items"), dict):
            for i, v in enumerate(value):
                problem = schema_problem(v, schema["items"], f"{path}[{i}]")
                if problem:
                    return problem
    return None


VALIDATION = {"type", "properties", "required", "additionalProperties", "items", "enum", "const",
              "anyOf", "oneOf", "allOf", "minimum", "maximum", "minItems", "maxItems",
              "minLength", "maxLength"}
ANNOTATION = {"title", "description", "default", "examples", "format", "deprecated", "readOnly",
              "writeOnly", "$schema", "$comment"}          # describe a value; never constrain it


def unsupported_keywords(schema, path: str = "$") -> list[str]:
    """Every rule in `schema` that schema_problem cannot check, as 'path: keyword'."""
    if not isinstance(schema, dict):
        return []
    out = [f"{path}: {k}" for k in schema if k not in VALIDATION and k not in ANNOTATION]
    for name, sub in (schema.get("properties") or {}).items():
        out += unsupported_keywords(sub, f"{path}.{name}")
    for key in ("items", "additionalProperties"):
        if isinstance(schema.get(key), dict):
            out += unsupported_keywords(schema[key], f"{path}.{key}")
    for key in ("anyOf", "oneOf", "allOf"):
        for i, sub in enumerate(schema.get(key) or []):
            out += unsupported_keywords(sub, f"{path}.{key}[{i}]")
    return out


def from_a_model_call(exc: BaseException | None) -> bool:
    """True when `exc`, or anything it wraps, is a model call failing: a usage limit, a call that
    failed after its retry, or an API error on the metered path. A runner that reruns its work on
    any error would repeat every call already made, and a limit would only fail again."""
    kinds: tuple = (UsageLimit, MaxCallError, SchemaUnsupported)   # a rerun would fail the same way
    try:
        import anthropic
        kinds += (anthropic.APIError,)
    except ImportError:
        pass
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, kinds):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def _once(user: str, system: str, model: str, effort: str | None, schema: dict | None,
          timeout: int) -> Reply:
    # The system prompt goes through a file: it can carry ~30k tokens of reports (it is the part
    # Claude Code caches, see DeskLLM), and Windows caps a command line at 32k characters.
    fd, sys_path = tempfile.mkstemp(prefix="desk-system-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(system)
    cmd = [*claude_exe(), "-p", "--model", model, "--system-prompt-file", sys_path,
           "--tools", "", "--setting-sources", "", "--strict-mcp-config",
           "--no-session-persistence", "--output-format", "json"]
    if effort:
        cmd += ["--effort", effort]
    if schema:
        cmd += ["--json-schema", json.dumps(schema, separators=(",", ":"))]
    started = time.time()
    try:
        proc = subprocess.run(cmd, input=user.encode("utf-8"), capture_output=True,
                              env=child_env(), cwd=str(Path.home()), timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise MaxCallError(f"claude -p timed out after {timeout}s") from exc
    finally:
        try:
            os.unlink(sys_path)
        except OSError:
            pass
    out = proc.stdout.decode("utf-8", "replace").strip()
    err = proc.stderr.decode("utf-8", "replace").strip()
    try:
        payload = json.loads(out[out.index("{"):]) if "{" in out else {}
    except json.JSONDecodeError:
        payload = {}
    text = payload.get("result") if isinstance(payload.get("result"), str) else ""
    data = payload.get("structured_output")
    if schema and not isinstance(data, dict):
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            data = None
        data = data if isinstance(data, dict) else None
    failed = (not payload or bool(payload.get("is_error")) or proc.returncode != 0
              or (schema is not None and data is None))
    if _looks_like_limit(text, failed) or (failed and (_looks_like_limit(err, True)
                                                      or (not payload and _looks_like_limit(out, True)))):
        raise UsageLimit((text or err or out)[:300])
    # A clean exit is required as well (audit R2-20): a success-shaped payload from a failed process
    # used to be accepted.
    if not payload or payload.get("is_error") or proc.returncode != 0:
        raise MaxCallError(f"claude -p failed (exit {proc.returncode}): {(text or err or out)[:400]}")
    if schema is not None:
        problem = "no structured output" if data is None else schema_problem(data, schema)
        if problem:
            raise MaxCallError(f"claude -p answer failed its schema: {problem}")
    usage = payload.get("usage") or {}
    reply = Reply(text=text or "", data=data, model=model, seconds=round(time.time() - started, 1),
                  usage={"input": usage.get("input_tokens", 0),
                         "cache_write": usage.get("cache_creation_input_tokens", 0),
                         "cache_read": usage.get("cache_read_input_tokens", 0),
                         "output": usage.get("output_tokens", 0)},
                  notional_usd=float(payload.get("total_cost_usd") or 0))
    with _LOCK:
        CALLS.append({"model": model, "seconds": reply.seconds, "notional_usd": reply.notional_usd,
                      **reply.usage})
    return reply


def plan() -> str:
    """claude (default) or chatgpt. One plan for the whole run."""
    p = os.environ.get("DESK_PLAN", "claude").strip().lower()
    return "chatgpt" if p in ("chatgpt", "codex", "openai") else "claude"


def _once_chatgpt(user: str, system: str, model: str, effort: str | None, schema: dict | None,
                  timeout: int) -> Reply:
    from desk import chatgpt
    try:
        r = chatgpt.run(user, system, chatgpt.model_for(model), effort, schema, timeout)
    except chatgpt.ChatGPTError as exc:
        raise MaxCallError(str(exc)) from exc
    if r["stray"]:
        raise MaxCallError(f"codex exec used {', '.join(sorted(set(r['stray'])))}; a desk call may only answer")
    if _looks_like_limit(r["text"], r["failed"]) or (r["failed"] and _looks_like_limit(r["error"], True)):
        raise UsageLimit((r["error"] or r["text"])[:300])
    if r["failed"]:
        raise MaxCallError(f"codex exec failed: {(r['error'] or r['text'] or 'no answer')[:400]}")
    if schema is not None:
        problem = schema_problem(r["data"], schema)
        if problem:
            raise MaxCallError(f"codex exec answer failed its schema: {problem}")
    reply = Reply(text=r["text"], data=r["data"], model=r["model"], seconds=r["seconds"],
                  usage={k: r["usage"].get(k, 0) for k in ("input", "cache_write", "cache_read", "output")})
    with _LOCK:
        CALLS.append({"model": r["model"], "seconds": reply.seconds, "notional_usd": 0.0, **reply.usage})
    return reply


def ask(user: str, system: str, model: str = "sonnet", effort: str | None = None,
        schema: dict | None = None, timeout: int = DEFAULT_TIMEOUT) -> Reply:
    """One call, retried once on anything but a usage limit."""
    bad = unsupported_keywords(schema) if schema else []
    if bad:
        raise SchemaUnsupported(f"the output schema uses rules the desk cannot check: {', '.join(bad)}")
    once = _once_chatgpt if plan() == "chatgpt" else _once
    model = model if plan() == "chatgpt" else resolve(model)
    try:
        return once(user, system, model, effort, schema, timeout)
    except UsageLimit:
        raise
    except MaxCallError:
        time.sleep(3)
        return once(user, system, model, effort, schema, timeout)


def summary() -> dict:
    """Calls, tokens and notional list price for everything this process sent."""
    with _LOCK:
        calls = list(CALLS)
    by: dict[str, dict] = {}
    for c in calls:
        m = by.setdefault(c["model"], {"calls": 0, "input": 0, "cache_write": 0, "cache_read": 0,
                                        "output": 0, "seconds": 0.0, "notional_usd": 0.0})
        m["calls"] += 1
        for k in ("input", "cache_write", "cache_read", "output", "seconds", "notional_usd"):
            m[k] += c.get(k) or 0
    return {"billing": "chatgpt" if plan() == "chatgpt" else "max", "calls": len(calls), "usd": 0.0,
            "notional_usd": round(sum(c["notional_usd"] for c in calls), 4), "by_model": by}


def enabled() -> bool:
    """Max is the default. DESK_LLM=api puts every call back on the metered API key."""
    return os.environ.get("DESK_LLM", "max").lower() != "api"
