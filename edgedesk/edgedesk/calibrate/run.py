"""The calibration job: build the sample, split it, report on it.

The split is by time, never at random. A random split would put a stock's
September observation in the development set and its October one in the
held-out set, which are not independent facts: they share a filing, a chart and
a market. Splitting by date is the only split that answers "would this have
worked on data I had not seen".
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

from edgedesk.calibrate import report as report_mod, sample as sample_mod
from edgedesk.calibrate.harness import market_is_open
from edgedesk.factors.families import FACTORS_VERSION
from edgedesk.paths import ensure
from edgedesk.providers.client import DataClient
from edgedesk.verdict.rating import RATING_VERSION


class MarketOpen(RuntimeError):
    """Wide jobs share a rate limit with the live trading bot."""


def outcome_end(row: dict, outcome: dict) -> str:
    """The date an outcome window closed. Samples saved before 2026-10-05 recorded only how many
    trading days it ran; for those it is estimated late on purpose (eleven extra weekdays a year
    for market holidays, which fall on weekdays, then three more days), so the purge errs toward
    dropping an outcome, never toward keeping one."""
    if outcome.get("end"):
        return outcome["end"]
    bars = int(outcome.get("bars") or 0)
    day, left = date.fromisoformat(row["as_of"]), bars + math.ceil(bars / 252 * 11)
    while left > 0:
        day += timedelta(days=1)
        if day.weekday() < 5:
            left -= 1
    return (day + timedelta(days=3)).isoformat()


def split(rows: list[dict], cut: str) -> tuple[list[dict], list[dict], dict]:
    """(development, held-out, what the purge removed).

    Splitting on the decision date alone let an observation made just before the holdout carry a
    twelve-month outcome measured almost entirely inside it (audit R2-18). A development row now
    keeps only the windows that closed before the holdout starts, and is dropped when none did.
    Held-out rows start on or after it, so nothing they measure was seen during development."""
    dev, holdout, dropped_windows, dropped_rows = [], [], 0, 0
    for row in rows:
        if row["as_of"] >= cut:
            holdout.append(row)
            continue
        kept = [o for o in row.get("outcomes") or [] if outcome_end(row, o) < cut]
        dropped_windows += len(row.get("outcomes") or []) - len(kept)
        if kept:
            dev.append({**row, "outcomes": kept})
        else:
            dropped_rows += 1
    effective = {
        "dev_tickers": len({r["ticker"] for r in dev}), "dev_dates": len({r["as_of"] for r in dev}),
        "holdout_tickers": len({r["ticker"] for r in holdout}),
        "holdout_dates": len({r["as_of"] for r in holdout}),
    }
    return dev, holdout, {"dropped_windows": dropped_windows, "dropped_rows": dropped_rows, **effective}


def rereport(name: str, holdout_start: date, end: date, start: date | None = None,
             step: int = 5, ticker_count: int | None = None, say=print) -> dict:
    """Split and report an already saved sample again, with no fetching. Used to apply the
    R2-18 purge to samples built before it existed."""
    rows = sample_mod.load(name)
    if not rows:
        raise FileNotFoundError(f"no saved sample called {name}")
    # Written beside the original, never over it: the first report stays as the record.
    return _report(rows, f"{name}-purged", start or date.fromisoformat(min(r["as_of"] for r in rows)), end,
                   holdout_start, step, ticker_count or len({r["ticker"] for r in rows}), say)


def calibrate(tickers: list[str], start: date, end: date, holdout_start: date,
              step: int = 5, name: str = "latest", force: bool = False,
              say=print) -> dict:
    """Run the whole calibration and write the report.

    `holdout_start` splits development from held-out. Everything on or after it
    is untouched while anything is decided.
    """
    if market_is_open() and not force:
        raise MarketOpen(
            "The market is open. This job makes hundreds of requests on the key the live "
            "trading bot shares, so it runs outside 09:30-16:00 ET. Use --force only if "
            "the bot is not running.")
    if not (start < holdout_start <= end):
        raise ValueError("holdout_start must fall inside the calibration window")

    say(f"Sampling {len(tickers)} tickers from {start} to {end}, every {step} trading days.")
    client = DataClient()
    try:
        observations = sample_mod.build(tickers, start, end, step, client, say=say)
    finally:
        client.close()

    rows = [o.to_dict() if hasattr(o, "to_dict") else o for o in observations]
    path = sample_mod.save(observations, name)
    say(f"{len(rows)} observations saved to {path}")

    return _report(rows, name, start, end, holdout_start, step, len(tickers), say,
                   observations=len(rows), sample=path)


def _report(rows: list[dict], name: str, start: date, end: date, holdout_start: date, step: int,
            ticker_count: int, say=print, observations: int | None = None, sample: str | None = None) -> dict:
    cut = holdout_start.isoformat()
    dev, holdout, purge = split(rows, cut)
    meta = {
        "ticker_count": ticker_count, "purge": purge,
        "dev_start": start.isoformat(),
        "dev_end": holdout_start.isoformat(),
        "holdout_start": cut,
        "holdout_end": end.isoformat(),
        "step": step,
        "factors_version": FACTORS_VERSION,
        "rating_version": RATING_VERSION,
    }
    text = report_mod.render(dev, holdout, meta)
    out = ensure(sample_mod.SAMPLES_DIR / f"{name}-report.md")
    out.write_text(text, encoding="utf-8")
    meta_path = ensure(sample_mod.SAMPLES_DIR / f"{name}-meta.json")
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    say(f"Report written to {out}")
    return {"observations": observations or len(rows), "dev": len(dev), "holdout": len(holdout),
            "report": str(out), "sample": sample, "meta": meta, "text": text}
