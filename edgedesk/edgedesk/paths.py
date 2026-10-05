"""Where Edge Desk keeps its data: ~/.edge-desk/.

Runs, estimate snapshots and provider caches live under one home directory,
outside the package, so the checkout stays read-only code. Import-light on
purpose: every layer anchors its paths here and nothing here imports back.

`runs/` is the source of truth. One JSON per ticker per run date, plus a small
index. No database: see docs/ENGINE-HANDOFF.md.
"""

from __future__ import annotations

import os
from pathlib import Path

USER_DIR = Path(os.environ.get("EDGE_DESK_HOME") or (Path.home() / ".edge-desk"))
RUNS_DIR = USER_DIR / "runs"
ESTIMATES_DIR = USER_DIR / "estimates"
CACHE_DIR = USER_DIR / "cache"
ENV_PATH = USER_DIR / ".env"
UNIVERSE_PATH = USER_DIR / "universe.yaml"


def run_path(ticker: str, as_of: str) -> Path:
    """runs/YYYY-MM-DD/TICKER.json"""
    return RUNS_DIR / as_of / f"{ticker.upper()}.json"


def estimates_path(ticker: str, as_of: str) -> Path:
    """estimates/YYYY-MM-DD/TICKER.json"""
    return ESTIMATES_DIR / as_of / f"{ticker.upper()}.json"


def ensure(path: Path) -> Path:
    """Create a file's parent directory and hand the path back."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
