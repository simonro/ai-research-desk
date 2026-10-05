"""Paths and knobs for the desk. Every value says why it is what it is."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

# Settings files, read here so every value below can come from them. The first is the desk's own;
# the second is the one ai-hedge-fund and older installs use. Neither overrides the environment,
# and a blank value (a template line left empty) never hides a real one set further down.
for _env in (Path.home() / ".hedge-desk" / ".env", Path.home() / ".hedge-fund" / ".env"):
    for _key, _value in dotenv_values(_env).items():
        if _value and _key not in os.environ:
            os.environ[_key] = _value

ROOT = Path(__file__).resolve().parents[2]          # the repo (or the author's "Hedge Fund" folder)
DESK_DIR = ROOT / "desk"
RUNNERS_DIR = DESK_DIR / "desk" / "runners"
MEMOS_DIR = Path(os.environ.get("DESK_MEMOS_DIR") or ROOT / "memos")   # memo + JSON bundle per run


def venv_python(folder: Path) -> Path:
    """An engine's own interpreter: .venv/Scripts/python.exe on Windows, .venv/bin/python elsewhere."""
    return folder / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


# Each engine keeps its own virtualenv: their LangChain/Anthropic SDK versions
# conflict, so the desk drives them as subprocesses and trades JSON files. In the public repo the
# engines live under engines/; in the author's original layout they sit beside desk/.
_ENGINES = ROOT / "engines" if (ROOT / "engines").exists() else ROOT
TA_DIR = Path(os.environ.get("DESK_TRADINGAGENTS_DIR") or _ENGINES / "TradingAgents")
TA_PYTHON = venv_python(TA_DIR)
AIHF_DIR = Path(os.environ.get("DESK_AIHF_DIR") or _ENGINES / "ai-hedge-fund")
AIHF_PYTHON = venv_python(AIHF_DIR)
# Edge Desk is run as it is, never imported: inside the repo, or beside it in the original layout.
EDGE_DIR = Path(os.environ.get("EDGE_DESK_DIR")
                or (ROOT / "edgedesk" if (ROOT / "edgedesk" / "pyproject.toml").exists() else ROOT.parent / "EdgeDesk"))
EDGE_PYTHON = venv_python(EDGE_DIR)

# Reports the TradingAgents CLI saves; a same-day CLI run is reused, not repaid.
TA_LOGS = Path.home() / ".tradingagents" / "logs"
TA_REPORT_FILES = ("final_trade_decision", "investment_plan", "trader_investment_plan",
                   "market_report", "fundamentals_report", "news_report", "sentiment_report")

# ai-hedge-fund fund whose analysts and research manager run for the desk.
DEFAULT_MANDATE = os.environ.get("DESK_MANDATE", "test-2")
MANDATES_DIR = Path.home() / ".hedge-fund" / "mandates"

# One model for both debaters and the Super Manager: the debate is judgment
# work over ~15k tokens of reports, and the reports are cached across turns.
MODEL = os.environ.get("DESK_MODEL", "claude-opus-5-5")
MAX_ROUNDS = 3              # each round = one turn per manager; enough to rebut twice
MAX_ROUNDS_THREE = 2        # three managers: 6 turns already lets each answer both others

# Model and effort per call type, all on the Max plan (desk/maxplan.py). Opus where one call
# decides a published rating or writes the memo; Sonnet where the job is mechanical. Effort
# trades thinking tokens (usage-limit share) for depth. Every Opus call is `medium` since the
# move to Opus 5.5 (2026-09-24): in Anthropic's testing Opus 5.5 at medium beats Opus 5 at high,
# and at any given level it thinks more per call than Opus 5, so carrying the debate's old `high`
# over would mostly buy longer turns. Raise one only after a real run shows it pays.
CALLS = {
    "horizon_rating": (MODEL, "medium"),            # decides agree vs debate for each horizon
    "debate_turn": (MODEL, "medium"),               # defend or concede against the other report
    "memo": (MODEL, "medium"),                      # the published write-up
    "corrections": ("claude-sonnet-5", "medium"),   # quote what the debate disproved; quotes are verified in code
}

# The Anthropic key is read where it already lives; it is never copied here.
KEY_ENV_FILES = (Path.home() / ".hedge-fund" / ".env", TA_DIR / ".env")

# Optional: a copy of each memo as a note in a markdown vault (e.g. Obsidian), and a search
# indexer to refresh afterwards. Both off unless set.
VAULT_MEMOS_DIR = Path(os.environ["DESK_VAULT_DIR"]) if os.environ.get("DESK_VAULT_DIR") else None
QMD = Path(os.environ["DESK_QMD"]) if os.environ.get("DESK_QMD") else None

TA_TIMEOUT_SECONDS = 60 * 60     # API ~8 min; on Max each call is a fresh claude -p, so allow more
AIHF_TIMEOUT_SECONDS = 10 * 60
EDGE_TIMEOUT_SECONDS = 40 * 60    # 10 model calls on Max (7 written sections, 3 research reads), each with one retry
