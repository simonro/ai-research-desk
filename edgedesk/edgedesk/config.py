"""Environment loading, in one place.

Secrets are never copied between files. `~/.edge-desk/.env` holds settings
and pointers: Alpaca credentials are reached through `ALPACA_ENV_FILE`, which
points at the file that already has them, so there is exactly one copy of those
keys on this machine and rotating them is a single edit.

`load()` is safe to call repeatedly and never overwrites a variable that is
already set, so an explicit export always wins over the file.
"""

from __future__ import annotations

import os

from dotenv import dotenv_values

from edgedesk.paths import ENV_PATH

_loaded = False


def load(force: bool = False) -> None:
    global _loaded
    if _loaded and not force:
        return
    if ENV_PATH.exists():
        for key, value in dotenv_values(ENV_PATH).items():
            if value is not None and key not in os.environ:
                os.environ[key] = value
    _loaded = True


def missing() -> list[str]:
    """Settings the engine needs that are not resolvable, for a clear error
    instead of a provider failure three layers down."""
    load()
    gaps: list[str] = []
    if not os.environ.get("SEC_USER_AGENT"):
        gaps.append("SEC_USER_AGENT (SEC EDGAR requires a contact string; free, no key)")
    # Alpaca keys are optional since 2026-10-05: without them prices come from Yahoo and there is
    # no Benzinga news, which the run reports as a data caveat rather than refusing to start.
    return gaps
