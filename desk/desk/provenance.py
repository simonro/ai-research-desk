"""What produced a team's report, so a saved report is reused only for the same inputs.

Every team report the desk saves carries `inputs`: the plan, the settled session the team priced
from, the model settings the team runs with, the ai-hedge-fund fund's contents, and the code
revision. A later run that day reuses a saved report only when its inputs are identical; a
Claude report is never reused for a ChatGPT run, a before-close report never for an after-close
run, and a report saved before this existed (no `inputs`) is never reused at all.

Values recorded are model names, plan names, dates and hashes: no keys or tokens.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from functools import lru_cache
from pathlib import Path

from desk.config import AIHF_DIR, EDGE_DIR, ROOT, TA_DIR

VERSION = 1
EDGE_ENV = Path.home() / ".edge-desk" / ".env"
MANDATES = Path.home() / ".hedge-fund" / "mandates"

# The settings that change what each team writes, as each team reads them.
SETTINGS = {
    "quant": ("TRADINGAGENTS_LLM_PROVIDER", "TRADINGAGENTS_DEEP_THINK_LLM", "TRADINGAGENTS_QUICK_THINK_LLM",
              "TRADINGAGENTS_ANTHROPIC_EFFORT", "TRADINGAGENTS_OPENAI_REASONING_EFFORT"),
    "vets": ("HEDGE_FUND_LLM_MODEL", "HEDGE_FUND_MANAGER_MODEL", "HEDGE_FUND_DATA_PROVIDER"),
    "edge": ("EDGE_DESK_MODEL",),
}
CHATGPT = ("DESK_CHATGPT_STRONG", "DESK_CHATGPT_FAST")
ENGINE_DIR = {"quant": TA_DIR, "vets": AIHF_DIR, "edge": EDGE_DIR}


def expected(engine: str, plan: str, session: str, mandate: str | None = None, env=None) -> dict:
    """The inputs a run of `engine` would have right now."""
    env = os.environ if env is None else env
    names = SETTINGS[engine] + (CHATGPT if plan == "chatgpt" else ())
    values = {k: env.get(k) or None for k in names}
    if engine == "edge":
        # Edge Desk's own settings file wins over inherited ones (edge_runner.edge_settings_win).
        values.update({k: v for k, v in _dotenv(EDGE_ENV).items() if k in names and v})
    out = {"v": VERSION, "plan": plan, "session": session, "settings": values,
           "code": {"desk": revision(ROOT), "engine": revision(ENGINE_DIR[engine])}}
    if engine == "vets":
        out["mandate"] = {"name": mandate, "sha256": mandate_hash(mandate)}
    return out


def differs(saved: dict | None, wanted: dict) -> str | None:
    """Why a saved report cannot stand in for a new run, or None when it can."""
    if not saved:
        return "it records no inputs (saved before reports carried them)"
    for key, label in (("plan", "plan"), ("session", "price session"), ("settings", "model settings"),
                       ("mandate", "fund"), ("code", "code revision"), ("v", "provenance version")):
        if saved.get(key) != wanted.get(key):
            return f"its {label} differs"
    return None


def mandate_hash(name: str | None) -> str | None:
    path = MANDATES / f"{name}.yaml" if name else None
    if not path or not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=None)
def revision(folder: Path) -> str | None:
    """The git commit the folder is at, or None (a ZIP install has no git history)."""
    try:
        out = subprocess.run(["git", "-C", str(folder), "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return (out.stdout.strip() or None) if out.returncode == 0 else None


def _dotenv(path: Path) -> dict:
    if not path.exists():
        return {}
    from dotenv import dotenv_values
    return dotenv_values(path)
