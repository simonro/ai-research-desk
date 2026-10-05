"""S0: does leadership pay at all here? A plain momentum rotation.

S1 and S2 both timed an entry and both came out level with SPY. Before a third
timing rule is tried, the thing S2 rested on is tested by itself: that names with
high 12-month return, skipping the latest month, keep outperforming over the next
month. If that is not true in this universe and period, no entry rule built on it
can work, and the search should move elsewhere.

The rule, fixed before any result was seen: every 21 sessions, rank the universe
on 12-1 momentum, buy the chosen slice at the next open, hold 20 sessions, no
stop, 5 bps a side.

The comparison that matters is NOT SPY and NOT random entries. Today's universe
is a list of survivors, so holding all of it already beats SPY, and a name that
ranked high at some point in a year had a good year by construction, so random
dates in that year flatter it. The fair control is the `everything` variant: the
same dates, the same holding period, every name. Top third must beat that, and
bottom third should trail it, or the ranking carries no information.
"""

from __future__ import annotations

from bisect import bisect_right

from edgedesk.research.pullback import _LOOKBACK, _SKIP, momentum_table
from edgedesk.research.simulate import Signal

SETUP = "S0-rotation"
_STEP = 21
PRIMARY = {"name": "top-third", "low": 2 / 3, "high": 1.0, "max_hold": 20}
VARIANTS = [
    PRIMARY,
    {"name": "everything", "low": 0.0, "high": 1.0, "max_hold": 20},
    {"name": "top-decile", "low": 0.9, "high": 1.0, "max_hold": 20},
    {"name": "middle-third", "low": 1 / 3, "high": 2 / 3, "max_hold": 20},
    {"name": "bottom-third", "low": 0.0, "high": 1 / 3, "max_hold": 20},
]


def make_signal_fn(bars_by_ticker: dict[str, list[dict]], calendar: list[str]):
    table = momentum_table(bars_by_ticker)
    rebalance = set(calendar[_LOOKBACK::_STEP])

    def signals(ticker, bars, spy_close, events, params):
        out = []
        for i in range(_LOOKBACK, len(bars)):
            day = bars[i]["date"]
            if day not in rebalance:
                continue
            then, recent = bars[i - _LOOKBACK]["close"], bars[i - _SKIP]["close"]
            ranks = table.get(day) or []
            if not then or not recent or len(ranks) < 30:
                continue
            mom = recent / then - 1.0
            pct = (bisect_right(ranks, mom) - 0.5) / len(ranks)
            if params["low"] <= pct <= params["high"]:
                out.append(Signal(ticker, day, stop=0.0, max_hold=params["max_hold"],
                                  setup=SETUP, meta={"momentum_12_1": round(mom, 4),
                                                     "pct": round(pct, 3)}))
        return out

    return signals
