"""Every setup against every exit scheme, in R and in dollars at a fixed risk budget.

One open trade per ticker per setup and scheme. Development trades are closed
before the holdout starts; held-out trades are opened on or after it. The interval
on mean R is a bootstrap over entry months. The market-regime column splits trades
by whether SPY was above its 200-session average on the signal date, because every
one of these setups is described by its author as a trending-market setup.
"""

from __future__ import annotations

import random
from collections import defaultdict
from statistics import mean, median

from edgedesk.research.plan import SCHEMES, simulate_plan
from edgedesk.research.setups import plans_for

_SEED = 20260919


def run(sample: dict, say=print, only: set[str] | None = None) -> list[dict]:
    bench = sample["bench"]
    closes = [b["close"] for b in bench]
    risk_on = {}
    for i, b in enumerate(bench):
        if i >= 199:
            risk_on[b["date"]] = closes[i] > sum(closes[i - 199:i + 1]) / 200
    rows = []
    for n, (ticker, bars) in enumerate(sample["bars"].items(), 1):
        if len(bars) < 300:
            continue
        plans = plans_for(ticker, bars, only=only)
        busy: dict[tuple[str, str], str] = {}
        for at, plan in plans:
            for scheme in SCHEMES:
                key = (plan.setup, scheme)
                if busy.get(key, "") >= plan.signal_date:
                    continue
                t = simulate_plan(plan, bars, at, scheme)
                if t is None:
                    continue
                busy[key] = t["exit_date"]
                t["risk_on"] = risk_on.get(plan.signal_date)
                rows.append(t)
        if n % 50 == 0:
            say(f"  {n} tickers")
    return rows


def _ci(rows: list[dict], rounds: int = 1000) -> tuple[float, float] | None:
    by_month = defaultdict(list)
    for r in rows:
        by_month[r["entry_date"][:7]].append(r["r"])
    months = list(by_month)
    if len(months) < 6:
        return None
    rng = random.Random(_SEED)
    means = sorted(mean(v for m in rng.choices(months, k=len(months)) for v in by_month[m])
                   for _ in range(rounds))
    return round(means[int(0.025 * rounds)], 3), round(means[int(0.975 * rounds)], 3)


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    rs = [r["r"] for r in rows]
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x <= 0]
    return {"n": len(rows), "win": round(len(wins) / len(rs), 3),
            "mean_r": round(mean(rs), 3), "median_r": round(median(rs), 3),
            "avg_win_r": round(mean(wins), 2) if wins else None,
            "avg_loss_r": round(mean(losses), 2) if losses else None,
            "ci": _ci(rows), "total_dollars": round(sum(r["pnl"] for r in rows)),
            "sessions": round(mean(r["sessions"] for r in rows), 1),
            "position": round(mean(r["position"] for r in rows))}


def table(rows: list[dict], holdout_start: str = "2024-01-01") -> list[dict]:
    groups = defaultdict(list)
    for r in rows:
        groups[(r["setup"], r["scheme"])].append(r)
    out = []
    for (setup, scheme), g in sorted(groups.items()):
        dev = [r for r in g if r["exit_date"] < holdout_start]
        held = [r for r in g if r["entry_date"] >= holdout_start]
        out.append({"setup": setup, "scheme": scheme, "dev": summarize(dev),
                    "held": summarize(held),
                    "held_risk_on": summarize([r for r in held if r["risk_on"]]),
                    "held_risk_off": summarize([r for r in held if r["risk_on"] is False])})
    return out


def render(results: list[dict]) -> str:
    out = ["| Setup | Exit scheme | Dev trades | Dev win | Dev mean R | Dev 95% | Held trades | Held win | Held mean R | Held 95% | Held $ at $1,000 risk | Avg sessions | Avg position |",
           "|---|---|---:|---:|---:|---|---:|---:|---:|---|---:|---:|---:|"]
    for r in results:
        d, h = r["dev"], r["held"]
        if not d.get("n") or not h.get("n"):
            continue
        out.append(f"| {r['setup']} | {r['scheme']} | {d['n']} | {d['win']:.0%} | {d['mean_r']:+.3f} | "
                   f"{d['ci']} | {h['n']} | {h['win']:.0%} | {h['mean_r']:+.3f} | {h['ci']} | "
                   f"{h['total_dollars']:+,} | {h['sessions']} | ${h['position']:,} |")
    return "\n".join(out)
