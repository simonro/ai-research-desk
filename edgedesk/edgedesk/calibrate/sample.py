"""Building the sample: run the engine across tickers and dates, record what
the score said and what the price did next.

One observation is one (ticker, as_of, horizon): the score the deterministic
engine produced that day, and the forward returns measured from bars after it.
Nothing else is kept, because nothing else is needed to answer the only question
the calibration asks, which is whether a higher score was followed by a better
outcome.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from edgedesk import run as run_mod
from edgedesk.calibrate import outcomes as out_mod
from edgedesk.calibrate.harness import (
    HistoricalClient, Observation, assert_no_leak, trading_dates,
)
from edgedesk.evidence import sectors
from edgedesk.evidence.anchors import align_closes
from edgedesk.paths import USER_DIR, ensure
from edgedesk.providers.client import DataClient
from edgedesk.verdict.rating import HORIZONS, WITHHELD

logger = logging.getLogger(__name__)

SAMPLES_DIR = USER_DIR / "calibration"


def build(tickers: list[str], start: date, end: date, step: int = 5,
          client: DataClient | None = None, say=print) -> list[Observation]:
    """Every observation for the given tickers and window.

    `step` is in trading days. Five means one observation a week, which is the
    cadence the engine is actually meant to run at; a daily sample would mostly
    measure the same setup five times and make a thin sample look thick.
    """
    own_client = client is None
    client = client or DataClient()
    hist = HistoricalClient(client, start, end)
    observations: list[Observation] = []
    try:
        hist.warm(sectors.SPY)
        spy_bars = hist.full_closes(sectors.SPY)

        for ticker in tickers:
            ticker = ticker.upper()
            try:
                bars = hist.full_closes(ticker)
            except Exception as exc:                        # noqa: BLE001
                say(f"  {ticker}: no price history ({exc})")
                continue
            profile = hist.profile(ticker) or {}
            _, etf = sectors.classify(profile.get("sic"), ticker)
            sector_bars = hist.full_closes(etf) if etf else []

            dates = trading_dates(bars, start, end, step)
            say(f"  {ticker}: {len(dates)} sample dates, sector {etf or 'none'}")
            made = 0
            for as_of in dates:
                made += _observe(hist, ticker, as_of, bars, spy_bars, sector_bars,
                                 observations)
            say(f"    {made} observations")
    finally:
        if own_client:
            client.close()
    say(f"  {hist.fetches} provider fetches for {len(tickers)} tickers")
    return observations


def _observe(hist, ticker, as_of, bars, spy_bars, sector_bars, observations) -> int:
    try:
        run = run_mod.analyze(ticker, as_of, client=hist, save=False)
    except Exception as exc:                                # noqa: BLE001
        logger.info("%s %s: run failed (%s)", ticker, as_of, exc)
        return 0
    assert_no_leak(run, as_of)

    index = _index_of(bars, as_of)
    if index is None:
        return 0
    closes = [b["close"] for b in bars]
    spy_aligned, sector_aligned = _benchmarks(bars, spy_bars, sector_bars)

    case, swing = run.get("long_term") or {}, run.get("swing") or {}
    calls = {"long_term_case": case.get("call"),
             "expected_return": (case.get("scenario_return") or {}).get("expected_annual_return"),
             "quality_flags": len((case.get("owner_economics") or {}).get("red_flags") or []),
             "swing_call": swing.get("call"), "swing_score": swing.get("score"),
             "swing_pullback": not any(b.startswith("No setup") for b in swing.get("blockers") or [])}
    made = 0
    for horizon in HORIZONS:
        verdict = (run.get("verdicts") or {}).get(horizon) or {}
        if verdict.get("quality_state") == WITHHELD or verdict.get("score") is None:
            continue
        measured = out_mod.measure(horizon, closes, index, spy_aligned, sector_aligned)
        usable = [o for o in measured if out_mod.complete(o, horizon)]
        # The date each window's outcome is known, so the split can keep a development
        # outcome only if it ended before the holdout (audit R2-18).
        ends = {o.window: bars[min(index + out_mod.WINDOWS[horizon][o.window], len(bars) - 1)]["date"]
                for o in usable}
        if not usable:
            continue
        observations.append(Observation(
            ticker=ticker,
            as_of=as_of.isoformat(),
            horizon=horizon,
            score=verdict["score"],
            rating=verdict["rating"],
            conviction=verdict["conviction"],
            coverage=verdict["coverage"],
            risk_score=verdict.get("risk_score"),
            quality_state=verdict["quality_state"],
            factors={k: (v or {}).get("score")
                     for k, v in (run.get("factors") or {}).items()},
            outcomes=[{**o.to_dict(), "end": ends[o.window]} for o in usable],
            calls=calls,
        ))
        made += 1
    return made


def _index_of(bars: list[dict], as_of: date) -> int | None:
    stamp = as_of.isoformat()
    for i in range(len(bars) - 1, -1, -1):
        if bars[i]["date"] == stamp:
            return i
    return None


def _benchmarks(bars, spy_bars, sector_bars):
    """Benchmark closes aligned to the stock's own trading days.

    Positional comparison against an unaligned series would measure different
    periods whenever one of them missed a day, which is the quiet way a
    relative-return study goes wrong.
    """
    dates = {b["date"] for b in bars}
    spy = _project(bars, spy_bars) if spy_bars else None
    sector = _project(bars, sector_bars) if sector_bars else None
    return spy, sector


def _project(bars: list[dict], other: list[dict]) -> list[float] | None:
    """`other`'s closes, one per bar in `bars`, carrying the last known value
    forward across a day the benchmark did not trade."""
    by_date = {b["date"]: b["close"] for b in other}
    out: list[float] = []
    last = None
    for b in bars:
        last = by_date.get(b["date"], last)
        out.append(last if last is not None else float("nan"))
    return out if any(v == v for v in out) else None       # nan check


# ---------------------------------------------------------------------------

def save(observations: list[Observation], name: str) -> str:
    path = ensure(SAMPLES_DIR / f"{name}.json")
    path.write_text(json.dumps([o.to_dict() for o in observations], indent=1,
                               sort_keys=True), encoding="utf-8")
    return str(path)


def load(name: str) -> list[dict]:
    path = SAMPLES_DIR / f"{name}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))
