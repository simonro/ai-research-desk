"""Estimate snapshots: today's consensus, written down so tomorrow has history.

Nothing in Phase A consumes these. They are captured anyway, from the very
first run, because consensus is current-only: no free provider keeps a history
of it, so a snapshot not taken today can never be recovered. Revisions,
pre-earnings expectations and "the Street cut its target twice this month" all
become possible later purely because these files accumulate now.

This is the one place the engine writes data it does not yet read. It is also
the one place where skipping the work would be irreversible.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

from edgedesk.paths import ensure, estimates_path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def snapshot(ticker: str, as_of: date, consensus: dict | None,
             metrics: dict | None = None, breakdown: dict | None = None,
             calendar: dict | None = None, forward: dict | None = None) -> dict | None:
    """Build the snapshot record. Returns None when there is nothing to record.

    The analyst split and the forward EPS estimate are captured alongside the
    targets, because those are what make revisions legible later: a mean target
    drifting up while the bull count falls is a different story from both rising
    together, and neither can be reconstructed after the fact.
    """
    if not consensus:
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "ticker": ticker.upper(),
        "as_of": as_of.isoformat(),
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "yahoo",
        "fiscal_period": (metrics or {}).get("report_period"),
        "eps_ttm": (metrics or {}).get("earnings_per_share"),
        "target_mean": consensus.get("target_mean_price"),
        "target_high": consensus.get("target_high_price"),
        "target_low": consensus.get("target_low_price"),
        "recommendation_mean": consensus.get("recommendation_mean"),
        "recommendation_key": consensus.get("recommendation_key"),
        "analyst_count": consensus.get("analyst_count"),
        "price_at_capture": consensus.get("current_price"),
        "bullish": (breakdown or {}).get("bullish"),
        "neutral": (breakdown or {}).get("neutral"),
        "bearish": (breakdown or {}).get("bearish"),
        "breakdown_history": (breakdown or {}).get("history"),
        "forward": forward,
        "next_earnings": (calendar or {}).get("next_earnings"),
        "next_eps_estimate": (calendar or {}).get("eps_estimate"),
        "next_revenue_estimate": (calendar or {}).get("revenue_estimate"),
        "raw": consensus,
    }


def write(record: dict | None) -> str | None:
    """Persist one snapshot as `estimates/YYYY-MM-DD/TICKER.json`. Never raises:
    a failed snapshot must not cost the run its rating."""
    if not record:
        return None
    path = ensure(estimates_path(record["ticker"], record["as_of"]))
    try:
        path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        logger.warning("could not write estimate snapshot %s: %s", path, exc)
        return None
    return str(path)


def history(ticker: str) -> list[dict]:
    """Every snapshot held for *ticker*, oldest first.

    Returns an empty list on run one, and callers must read that as "history
    starts today", never as "the Street has no view".
    """
    from edgedesk.paths import ESTIMATES_DIR

    out: list[dict] = []
    if not ESTIMATES_DIR.exists():
        return out
    for day_dir in sorted(ESTIMATES_DIR.iterdir()):
        path = day_dir / f"{ticker.upper()}.json"
        if path.exists():
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("skipping unreadable snapshot %s: %s", path, exc)
    return out
