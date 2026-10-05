"""Turn a signal into the trade a person could actually have taken.

The calibration measures close-to-close returns from the signal bar. Nobody can
trade that: the signal is only known once the bar has closed. This module is the
other half, and every rule in it errs against the strategy:

* the signal is read at a close and the entry is the NEXT session's open
* a stop is frozen when the signal fires and never moves
* an open below the stop fills at the open, not at the stop (a gap costs what it costs)
* a bar whose low touches the stop fills at the stop, even on the entry day
* an exit rule is read at a close and acted on at the next open
* the time exit is the close of the last allowed session
* costs are charged on both sides

Prices are the adjusted series, so dividends are in the return. Position sizing
is deliberately absent: a trade is one unit, and its result is a return and an R
multiple. Sizing is a later layer and must not be able to flatter a setup here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable

COST_BPS = 5.0          # per side: commission-free, so this is spread and slippage

# (bars, entry_index, current_index) -> True to exit at the next open
ExitRule = Callable[[list[dict], int, int], bool]


@dataclass(frozen=True)
class Signal:
    ticker: str
    date: str                       # the session whose close produced the signal
    stop: float                     # frozen here, in adjusted-price terms
    max_hold: int                   # sessions, counting the entry session as 1
    setup: str
    exit_before: str | None = None  # flat by the close before this date (next report)
    meta: dict = field(default_factory=dict)


@dataclass
class Trade:
    ticker: str
    setup: str
    signal_date: str
    entry_date: str
    entry: float
    stop: float
    exit_date: str
    exit: float
    exit_reason: str                # stop | gap_stop | rule | time | event | data_end
    sessions: int
    gross: float
    net: float
    r_multiple: float | None
    mae: float
    mfe: float
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Rejected:
    ticker: str
    setup: str
    signal_date: str
    why: str


def index_of(bars: list[dict], day: str) -> int | None:
    lo, hi = 0, len(bars) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        d = bars[mid]["date"]
        if d == day:
            return mid
        if d < day:
            lo = mid + 1
        else:
            hi = mid - 1
    return None


def simulate(signal: Signal, bars: list[dict], exit_rule: ExitRule | None = None,
             cost_bps: float = COST_BPS) -> Trade | Rejected:
    """One signal against one ticker's bars, oldest first."""
    def no(why: str) -> Rejected:
        return Rejected(signal.ticker, signal.setup, signal.date, why)

    at = index_of(bars, signal.date)
    if at is None:
        return no("signal date is not a session in the bars")
    if at + 1 >= len(bars):
        return no("no session after the signal to enter on")
    e = at + 1
    entry = bars[e]["open"]
    if not entry or entry <= 0:
        return no("no opening price on the entry session")
    if entry <= signal.stop:
        return no("opened at or below the stop, so the setup was dead before entry")
    if signal.exit_before and bars[e]["date"] >= signal.exit_before:
        return no("the entry session is on or after the exit deadline")

    exit_price, exit_reason, x = None, None, e
    pending_rule_exit = False
    low_seen, high_seen = entry, entry
    for i in range(e, len(bars)):
        bar = bars[i]
        x = i
        if i > e and bar["open"] <= signal.stop:
            exit_price, exit_reason = bar["open"], "gap_stop"
            low_seen = min(low_seen, bar["open"])
            break
        if pending_rule_exit:
            exit_price, exit_reason = bar["open"], "rule"
            break
        low_seen, high_seen = min(low_seen, bar["low"]), max(high_seen, bar["high"])
        if bar["low"] <= signal.stop:
            exit_price, exit_reason = signal.stop, "stop"
            break
        held = i - e + 1
        last_by_event = (signal.exit_before is not None and i + 1 < len(bars)
                         and bars[i + 1]["date"] >= signal.exit_before)
        if last_by_event:
            exit_price, exit_reason = bar["close"], "event"
            break
        if held >= signal.max_hold:
            exit_price, exit_reason = bar["close"], "time"
            break
        if exit_rule is not None and exit_rule(bars, e, i):
            pending_rule_exit = True
    if exit_price is None:
        exit_price, exit_reason = bars[x]["close"], "data_end"

    c = cost_bps / 10_000.0
    gross = exit_price / entry - 1.0
    net = (exit_price * (1 - c)) / (entry * (1 + c)) - 1.0
    risk = entry - signal.stop
    return Trade(
        ticker=signal.ticker, setup=signal.setup, signal_date=signal.date,
        entry_date=bars[e]["date"], entry=round(entry, 4), stop=round(signal.stop, 4),
        exit_date=bars[x]["date"], exit=round(exit_price, 4), exit_reason=exit_reason,
        sessions=x - e + 1, gross=round(gross, 6), net=round(net, 6),
        r_multiple=round((exit_price - entry) / risk, 4) if risk > 0 else None,
        mae=round(low_seen / entry - 1.0, 6), mfe=round(high_seen / entry - 1.0, 6),
        meta=dict(signal.meta))


def benchmark_return(trade: Trade, bench: list[dict]) -> float | None:
    """The benchmark held over the same sessions: its open on the entry day to
    its open or close on the exit day, matching how the trade left."""
    a, b = index_of(bench, trade.entry_date), index_of(bench, trade.exit_date)
    if a is None or b is None or not bench[a]["open"]:
        return None
    out = bench[b]["open"] if trade.exit_reason in ("gap_stop", "rule") else bench[b]["close"]
    return round(out / bench[a]["open"] - 1.0, 6)
