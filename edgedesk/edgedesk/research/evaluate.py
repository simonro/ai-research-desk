"""Judge a setup the way it would be judged by someone hoping it fails.

Three comparisons, all over the same sessions each trade was actually held:

* the benchmark (SPY), because beating cash is not the mandate
* random entries in the same ticker and the same year, run through the same stop
  distance and the same time exit, because a bull market and a good stock list
  make almost any long-only rule look clever
* itself with each ticker removed, because one company has carried a result in
  this project before

The interval on the mean is a bootstrap over entry MONTHS, not over trades.
Trades that open in the same month share a market, so resampling them singly
would claim far more independent evidence than exists.

Every evaluation is appended to the experiment log, including the failures.
Choosing the best of many unlogged variants is how a backtest lies.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean, median

from edgedesk import paths
from edgedesk.research.simulate import (ExitRule, Rejected, Signal, Trade, benchmark_return,
                                        index_of, simulate)

EXPERIMENT_LOG = "experiments.jsonl"
_SEED = 20260919


def run(signals: list[Signal], bars_by_ticker: dict[str, list[dict]], bench: list[dict],
        exit_rule: ExitRule | None = None) -> tuple[list[dict], list[Rejected]]:
    """Simulate every signal. One open trade per ticker: a second signal while the
    first is still open is the same idea twice, not a second trade."""
    rows, rejected = [], []
    open_until: dict[str, str] = {}
    for s in sorted(signals, key=lambda s: (s.date, s.ticker)):
        if open_until.get(s.ticker, "") >= s.date:
            rejected.append(Rejected(s.ticker, s.setup, s.date, "already in a trade"))
            continue
        result = simulate(s, bars_by_ticker.get(s.ticker) or [], exit_rule)
        if isinstance(result, Rejected):
            rejected.append(result)
            continue
        open_until[s.ticker] = result.exit_date
        row = result.to_dict()
        row["bench"] = benchmark_return(result, bench)
        row["excess"] = None if row["bench"] is None else round(row["net"] - row["bench"], 6)
        rows.append(row)
    return rows, rejected


def random_baseline(trades: list[dict], bars_by_ticker: dict[str, list[dict]],
                    bench: list[dict], max_hold: int, draws: int = 20,
                    exit_rule: ExitRule | None = None) -> list[dict]:
    """For each trade, *draws* random signal dates in the same ticker and year,
    with the same stop distance and time exit."""
    rng = random.Random(_SEED)
    out = []
    for t in trades:
        bars = bars_by_ticker.get(t["ticker"]) or []
        year = t["signal_date"][:4]
        pool = [i for i, b in enumerate(bars[:-1]) if b["date"][:4] == year]
        if not pool:
            continue
        stop_pct = 1.0 - t["stop"] / t["entry"]
        for _ in range(draws):
            i = rng.choice(pool)
            ref = bars[i]["close"]
            sig = Signal(t["ticker"], bars[i]["date"], ref * (1.0 - stop_pct), max_hold,
                         "random")
            r = simulate(sig, bars, exit_rule)
            if isinstance(r, Rejected):
                continue
            row = r.to_dict()
            row["bench"] = benchmark_return(r, bench)
            row["excess"] = None if row["bench"] is None else round(row["net"] - row["bench"], 6)
            out.append(row)
    return out


def _month_bootstrap(rows: list[dict], key: str, rounds: int = 2000) -> tuple[float, float] | None:
    by_month: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.get(key) is not None:
            by_month[r["entry_date"][:7]].append(r[key])
    months = list(by_month)
    if len(months) < 6:
        return None
    rng = random.Random(_SEED)
    means = []
    for _ in range(rounds):
        sample = [v for m in rng.choices(months, k=len(months)) for v in by_month[m]]
        means.append(mean(sample))
    means.sort()
    return round(means[int(0.025 * rounds)], 5), round(means[int(0.975 * rounds)], 5)


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"trades": 0}
    net = [r["net"] for r in rows]
    exc = [r["excess"] for r in rows if r.get("excess") is not None]
    rs = [r["r_multiple"] for r in rows if r.get("r_multiple") is not None]
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x <= 0]
    reasons: dict[str, int] = defaultdict(int)
    for r in rows:
        reasons[r["exit_reason"]] += 1
    return {
        "trades": len(rows),
        "tickers": len({r["ticker"] for r in rows}),
        "entry_months": len({r["entry_date"][:7] for r in rows}),
        "win_rate": round(len(wins) / len(net), 4),
        "mean_net": round(mean(net), 5),
        "median_net": round(median(net), 5),
        "avg_win": round(mean(wins), 5) if wins else None,
        "avg_loss": round(mean(losses), 5) if losses else None,
        "mean_r": round(mean(rs), 4) if rs else None,
        "mean_excess": round(mean(exc), 5) if exc else None,
        "median_excess": round(median(exc), 5) if exc else None,
        "beat_bench": round(mean(x > 0 for x in exc), 4) if exc else None,
        "mean_excess_ci95": _month_bootstrap(rows, "excess"),
        "mean_sessions": round(mean(r["sessions"] for r in rows), 1),
        "worst_trade": round(min(net), 5),
        "exit_reasons": dict(reasons),
    }


def leave_one_ticker_out(rows: list[dict]) -> dict:
    """The range of mean excess when each ticker is removed in turn."""
    tickers = sorted({r["ticker"] for r in rows})
    if len(tickers) < 3:
        return {}
    out = {}
    for t in tickers:
        rest = [r["excess"] for r in rows if r["ticker"] != t and r.get("excess") is not None]
        if rest:
            out[t] = mean(rest)
    low, high = min(out, key=out.get), max(out, key=out.get)
    return {"min": round(out[low], 5), "min_without": low,
            "max": round(out[high], 5), "max_without": high}


def by_year(rows: list[dict]) -> dict:
    years: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        years[r["entry_date"][:4]].append(r)
    return {y: {"trades": len(v), "mean_net": round(mean(r["net"] for r in v), 5),
                "mean_excess": round(mean(r["excess"] for r in v if r["excess"] is not None), 5)
                if any(r["excess"] is not None for r in v) else None,
                "win_rate": round(mean(r["net"] > 0 for r in v), 3)}
            for y, v in sorted(years.items())}


def split(rows: list[dict], holdout_start: str) -> tuple[list[dict], list[dict]]:
    """Development and held-out trades. A trade belongs to development only if it
    was CLOSED before the holdout starts, so no development outcome reaches into
    the held-out period."""
    dev = [r for r in rows if r["exit_date"] < holdout_start]
    held = [r for r in rows if r["entry_date"] >= holdout_start]
    return dev, held


def verdict(setup: dict, baseline: dict, loo: dict) -> dict:
    """Pass only if every hostile reading passes. Deliberately hard."""
    checks = {
        "enough trades (100+)": setup.get("trades", 0) >= 100,
        "enough distinct months (24+)": setup.get("entry_months", 0) >= 24,
        "mean excess over the benchmark is positive": (setup.get("mean_excess") or 0) > 0,
        "the 95% interval on mean excess is above zero": bool(
            setup.get("mean_excess_ci95") and setup["mean_excess_ci95"][0] > 0),
        "beats random entries in the same names and years": (
            (setup.get("mean_excess") or 0) > (baseline.get("mean_excess") or 0)),
        "survives removing any one ticker": bool(loo and loo.get("min", -1) > 0),
    }
    return {"passed": all(checks.values()), "checks": checks}


def log_experiment(name: str, params: dict, result: dict) -> None:
    path = paths.ensure(paths.USER_DIR / EXPERIMENT_LOG)
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "name": name, "params": params, "result": result}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True, default=str) + "\n")


__all__ = ["run", "random_baseline", "summarize", "leave_one_ticker_out", "by_year", "split",
           "verdict", "log_experiment", "index_of"]
