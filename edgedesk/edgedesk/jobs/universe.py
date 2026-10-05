"""Who gets looked at, and how closely.

Three tiers, and the difference between them is attention, not quality of
analysis. Owned and watchlist names get the full run including the written
analysis. The large-cap list gets the deterministic score only, which is free
and fast, and exists so a name can earn its way onto the watchlist by hand
rather than by an automatic promotion nobody reviewed.

The file is created on first use with a comment explaining each list, because a
config file that appears empty is a config file nobody fills in.
"""

from __future__ import annotations

import logging

import yaml

from edgedesk import paths
from edgedesk.paths import ensure

logger = logging.getLogger(__name__)

_TEMPLATE = """# Which names the engine looks at.
#
# owned      positions you hold. Full analysis. The action wording changes for
#            these (trim rather than avoid), but the rating never does. Give a
#            purchase date to anchor thesis health on it:
#              - NVDA
#              - {ticker: AVGO, since: 2026-03-02}
# watchlist  candidates. Full analysis, same depth as owned.
# large_cap  a wide list scored deterministically, no written analysis. Free to
#            run. Promote anything interesting into watchlist by hand.

owned: []

watchlist: []

large_cap: []
"""


def load() -> dict:
    """The universe, creating the file on first use."""
    path = paths.UNIVERSE_PATH
    if not path.exists():
        ensure(path).write_text(_TEMPLATE, encoding="utf-8")
        logger.info("created %s", path)
        return {"owned": [], "watchlist": [], "large_cap": [], "owned_since": {}}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"could not read {path}: {exc}") from exc
    out = {key: _clean(data.get(key)) for key in ("owned", "watchlist", "large_cap")}
    out["owned_since"] = _since(data.get("owned"))
    return out


def _since(values) -> dict[str, str]:
    """Purchase dates for owned names written as {ticker: X, since: YYYY-MM-DD}."""
    out = {}
    for v in values or []:
        if isinstance(v, dict) and v.get("ticker") and v.get("since"):
            out[str(v["ticker"]).strip().upper()] = str(v["since"])[:10]
    return out


def _clean(values) -> list[str]:
    if not values:
        return []
    out, seen = [], set()
    for v in values:
        if isinstance(v, dict):
            v = v.get("ticker") or ""
        ticker = str(v).strip().upper()
        if ticker and ticker not in seen:
            seen.add(ticker)
            out.append(ticker)
    return out


def researched(universe: dict | None = None) -> list[str]:
    """Owned plus watchlist: everything that gets the full treatment.

    Owned first, and de-duplicated, so a name on both lists is analyzed once and
    treated as held.
    """
    universe = universe or load()
    seen, out = set(), []
    for ticker in [*universe["owned"], *universe["watchlist"]]:
        if ticker not in seen:
            seen.add(ticker)
            out.append(ticker)
    return out


def describe(universe: dict | None = None) -> str:
    universe = universe or load()
    return (f"{len(universe['owned'])} owned, {len(universe['watchlist'])} on the "
            f"watchlist, {len(universe['large_cap'])} large caps scored")
