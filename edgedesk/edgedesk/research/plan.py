"""A complete trade plan, simulated: entry order, invalidation, size, exits.

The order of decisions is the one a discretionary swing trader uses:

1. the chart gives an INVALIDATION price (a doji low, a retest wick, a swing low)
2. entry minus invalidation is the risk per share
3. shares = the dollar risk budget / risk per share
4. targets are set in multiples of that risk (R), or from structure

Exit schemes are declared up front and ALL are reported for every setup, so the
best-looking cell cannot be quietly picked:

  half_1R_BE_2R   half off at +1R, stop to breakeven, rest at +2R
  all_2R          everything at +2R
  all_1_5R        everything at +1.5R
  structure       everything at the prior swing high if it is at least 1R away, else +2R
  topswing        a third at +4%, stop to breakeven at +10%, the rest at +20%
  stop_and_time   no target: the stop or the time limit
  time_only       no stop and no target: just hold to the time limit

Daily bars cannot say what happened first inside a session, so every ambiguity is
resolved against the trade:

* a session that touches both the stop and a target counts as the stop
* no target can fill on the entry session
* a buy-stop entry that triggers on a session whose low also reaches the
  invalidation counts as entered and stopped
* an open through the stop fills at the open; an open through a target fills at
  the open as well (that one is real: a resting limit order gets the better price)
"""

from __future__ import annotations

from dataclasses import dataclass, field

RISK_DOLLARS = 1000.0
MAX_HOLD = 40
COST_BPS = 5.0
SCHEMES = ("half_1R_BE_2R", "all_2R", "all_1_5R", "structure", "topswing",
           "stop_and_time", "time_only")


@dataclass(frozen=True)
class Plan:
    ticker: str
    setup: str
    signal_date: str
    stop: float
    entry_level: float | None = None     # None: market order at the next open
    valid_sessions: int = 1              # how long a buy-stop order rests
    structure_target: float | None = None
    meta: dict = field(default_factory=dict)


def _targets(scheme: str, entry: float, risk: float, plan: Plan) -> list[tuple[float, float]]:
    """[(fraction of the position, price)]"""
    if scheme == "half_1R_BE_2R":
        return [(0.5, entry + risk), (0.5, entry + 2 * risk)]
    if scheme == "all_2R":
        return [(1.0, entry + 2 * risk)]
    if scheme == "all_1_5R":
        return [(1.0, entry + 1.5 * risk)]
    if scheme == "structure":
        t = plan.structure_target
        return [(1.0, t if t and t >= entry + risk else entry + 2 * risk)]
    if scheme == "topswing":
        return [(1 / 3, entry * 1.04), (2 / 3, entry * 1.20)]
    return []


def simulate_plan(plan: Plan, bars: list[dict], at: int, scheme: str,
                  risk_dollars: float = RISK_DOLLARS, max_hold: int = MAX_HOLD) -> dict | None:
    """*at* is the index of the signal session in *bars*."""
    e, entry = None, None
    if plan.entry_level is None:
        if at + 1 >= len(bars):
            return None
        e, entry = at + 1, bars[at + 1]["open"]
    else:
        for i in range(at + 1, min(at + 1 + plan.valid_sessions, len(bars))):
            if bars[i]["high"] >= plan.entry_level:
                e, entry = i, max(bars[i]["open"], plan.entry_level)
                break
    if e is None or not entry or entry <= plan.stop:
        return None

    planned_entry = plan.entry_level or entry
    risk = planned_entry - plan.stop
    if risk <= 0:
        return None
    shares = int(risk_dollars // risk)
    if shares < 1:
        return None

    use_stop = scheme != "time_only"
    stop = plan.stop
    targets = _targets(scheme, planned_entry, risk, plan)
    left, cash, x, reason = 1.0, 0.0, e, "time"
    c = COST_BPS / 10_000.0
    low_seen = entry

    for i in range(e, len(bars)):
        bar, x = bars[i], i
        low_seen = min(low_seen, bar["low"])
        if use_stop and i > e and bar["open"] <= stop:
            cash += left * bar["open"]; left = 0.0; reason = "gap_stop"; break
        if use_stop and bar["low"] <= stop:
            cash += left * stop; left = 0.0
            reason = "breakeven" if stop >= planned_entry else "stop"; break
        if i > e:
            for k, (fraction, price) in enumerate(list(targets)):
                if bar["high"] >= price:
                    fill = max(bar["open"], price)
                    cash += fraction * fill; left -= fraction
                    targets = [t for t in targets if t[1] != price]
                    if scheme == "half_1R_BE_2R" and k == 0:
                        stop = max(stop, planned_entry)
            if scheme == "topswing" and bar["high"] >= planned_entry * 1.10:
                stop = max(stop, planned_entry)
            if left <= 1e-9:
                left = 0.0; reason = "target"; break
        if i - e + 1 >= max_hold:
            cash += left * bar["close"]; left = 0.0; reason = "time"; break
    if left > 0:
        cash += left * bars[x]["close"]; reason = "data_end"

    avg_exit = cash                                   # per share, fractions sum to 1
    pnl = shares * (avg_exit * (1 - c) - entry * (1 + c))
    return {"ticker": plan.ticker, "setup": plan.setup, "scheme": scheme,
            "signal_date": plan.signal_date, "entry_date": bars[e]["date"],
            "exit_date": bars[x]["date"], "entry": round(entry, 4), "stop": round(plan.stop, 4),
            "shares": shares, "position": round(shares * entry, 0),
            "pnl": round(pnl, 2), "r": round(pnl / risk_dollars, 4),
            "ret": round(avg_exit / entry - 1, 6), "sessions": x - e + 1, "reason": reason,
            "mae_r": round((low_seen - entry) / risk, 3), "meta": plan.meta}
