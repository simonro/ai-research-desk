"""What produced a team's report, so a saved report is reused only for the same inputs.

Every team report the desk saves carries `inputs`: the plan, the settled session the team priced
from, every research setting the team reads, the ai-hedge-fund fund's contents, and a digest of
the source code that ran. A later run that day reuses a saved report only when its inputs are
identical; a Claude report is never reused for a ChatGPT run, a before-close report never for an
after-close run, and a report saved before this existed (no `inputs`) is never reused at all.

Settings are taken by prefix (TRADINGAGENTS_*, HEDGE_FUND_*, EDGE_DESK_*), not from a hand-kept
list, so a new knob counts the day the engine adds it; output locations and anything secret are
left out. A setting that is unset and one set to its default value count as different: that
reruns a team once, which is the safe mistake. Source identity is a content digest of each
package, so a ZIP install or an uncommitted edit is seen too; when it cannot be computed, reuse
is refused rather than assumed safe.

Values recorded are model names, plan names, dates, settings and hashes: no keys or tokens.
"""

from __future__ import annotations

import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path

from desk.config import AIHF_DIR, EDGE_DIR, TA_DIR

VERSION = 2
EDGE_ENV = Path.home() / ".edge-desk" / ".env"
MANDATES = Path.home() / ".hedge-fund" / "mandates"
DESK_PACKAGE = Path(__file__).resolve().parent

PREFIX = {"quant": "TRADINGAGENTS_", "vets": "HEDGE_FUND_", "edge": "EDGE_DESK_"}
SHARED = ("ALPACA_DATA_FEED", "DESK_LLM")                    # every team's data feed and call path
CHATGPT = ("DESK_CHATGPT_STRONG", "DESK_CHATGPT_FAST")
# Where output goes, and secrets: neither changes what a team concludes.
_SKIP = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|_DIR|_PATH|_FILE|VAULT)", re.I)
SOURCE = {"quant": lambda: TA_DIR / "tradingagents", "vets": lambda: AIHF_DIR / "hedge_fund",
          "edge": lambda: EDGE_DIR / "edgedesk"}
_SOURCE_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".toml", ".md", ".txt", ".j2", ".jinja"}


def expected(engine: str, plan: str, session: str, mandate: str | None = None, env=None) -> dict:
    """The inputs a run of `engine` would have right now."""
    out = {"v": VERSION, "plan": plan, "session": session, "settings": settings(engine, plan, env),
           "code": {"desk": source_digest(DESK_PACKAGE), "engine": source_digest(SOURCE[engine]())}}
    if engine == "vets":
        out["mandate"] = {"name": mandate, "sha256": mandate_hash(mandate)}
    return out


def settings(engine: str, plan: str, env=None) -> dict:
    """Every setting the team reads that can change what it writes, as the team will see it."""
    env = os.environ if env is None else env

    def wanted(k: str) -> bool:
        return ((k.startswith(PREFIX[engine]) or k in SHARED or (plan == "chatgpt" and k in CHATGPT))
                and not _SKIP.search(k))

    values = {k: v for k, v in env.items() if v and wanted(k)}
    if engine == "edge":
        # Edge Desk's own settings file wins over inherited values (edge_runner.edge_settings_win).
        for k, v in _dotenv(EDGE_ENV).items():
            if wanted(k):
                if v:
                    values[k] = v
                else:
                    values.pop(k, None)
    return dict(sorted(values.items()))


def differs(saved: dict | None, wanted: dict) -> str | None:
    """Why a saved report cannot stand in for a new run, or None when it can."""
    if not saved:
        return "it records no inputs (saved before reports carried them)"
    if None in (wanted.get("code") or {"desk": None}).values():
        return "this install's source version cannot be read, so nothing is reused"
    for key, label in (("v", "provenance version"), ("plan", "plan"), ("session", "price session"),
                       ("settings", "research settings"), ("mandate", "fund"), ("code", "source code")):
        if saved.get(key) != wanted.get(key):
            return f"its {label} differs"
    return None


def mandate_hash(name: str | None) -> str | None:
    path = MANDATES / f"{name}.yaml" if name else None
    if not path or not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=None)
def source_digest(folder: Path) -> str | None:
    """A sha256 over the package's source files (paths and contents), or None if it is missing.
    Content, not git: a ZIP install has no history and an uncommitted edit leaves HEAD alone."""
    folder = Path(folder)
    if not folder.is_dir():
        return None
    h = hashlib.sha256()
    files = sorted(p for p in folder.rglob("*")
                   if p.is_file() and p.suffix.lower() in _SOURCE_SUFFIXES
                   and not {"__pycache__", "tests", ".venv", "node_modules"} & set(p.relative_to(folder).parts))
    for p in files:
        h.update(p.relative_to(folder).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))      # a CRLF checkout is the same code
        h.update(b"\0")
    return h.hexdigest() if files else None


def _dotenv(path: Path) -> dict:
    if not path.exists():
        return {}
    from dotenv import dotenv_values
    return dotenv_values(path)
