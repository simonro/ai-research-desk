"""S1: post-earnings drift, read from the market's own reaction.

The hypothesis. Prices under-react to earnings news, so a strong report keeps
paying for weeks. It is the best-documented anomaly at the 2-day to 8-week
horizon. The surprise is read from the reaction itself (the announcement return,
after Brandt, Kishore, Santa-Clara and Venkatachalam), which needs no analyst
estimates and cannot be restated later.

The rule, fixed before any result was seen:

* the reaction session is the first one that could trade on the 8-K (item 2.02)
* abnormal return that session (stock minus SPY, close to close) of at least 3%
* a close in the top third of the session's range: buyers held it into the close
* volume at least twice the prior 20-session average: the move was real money
* enter at the next open; stop frozen at the reaction session's low
* flat at 40 sessions, or by the close before the next report, whichever is first

How it could be wrong, and what each failure would look like:

* the drift is gone in liquid large caps (mean excess near zero, interval spans it)
* the edge is just "these stocks went up" (random entries in the same names match it)
* one or two names carry it (leave-one-ticker-out turns negative)
* it worked once (development passes, the held-out period does not)

The variants below were declared with the rule. All are logged; only PRIMARY is
judged. Anything added after seeing results is a new experiment with a new name.
"""

from __future__ import annotations

from edgedesk.research.data import reaction_index
from edgedesk.research.simulate import Signal

SETUP = "S1-pead"
PRIMARY = {"name": "primary", "min_abnormal": 0.03, "min_close_loc": 0.66,
           "min_rvol": 2.0, "max_hold": 40}
VARIANTS = [
    PRIMARY,
    {**PRIMARY, "name": "strong-reaction", "min_abnormal": 0.05},
    {**PRIMARY, "name": "no-volume-filter", "min_rvol": 0.0},
    {**PRIMARY, "name": "short-hold", "max_hold": 10},
]
_VOLUME_WINDOW = 20


def reaction(bars: list[dict], spy_close: dict[str, float], r: int) -> dict | None:
    """What the market did on the reaction session."""
    if r < _VOLUME_WINDOW + 1 or r >= len(bars):
        return None
    bar, prev = bars[r], bars[r - 1]
    spy_now, spy_prev = spy_close.get(bar["date"]), spy_close.get(prev["date"])
    if not (prev["close"] and spy_now and spy_prev and bar["high"] > bar["low"]):
        return None
    avg_volume = sum(b["volume"] for b in bars[r - _VOLUME_WINDOW:r]) / _VOLUME_WINDOW
    if avg_volume <= 0:
        return None
    ret = bar["close"] / prev["close"] - 1.0
    return {"ret": round(ret, 6),
            "abnormal": round(ret - (spy_now / spy_prev - 1.0), 6),
            "close_loc": round((bar["close"] - bar["low"]) / (bar["high"] - bar["low"]), 4),
            "rvol": round(bar["volume"] / avg_volume, 3),
            "gap": round(bar["open"] / prev["close"] - 1.0, 6)}


def signals(ticker: str, bars: list[dict], spy_close: dict[str, float],
            events: list[dict], params: dict = PRIMARY) -> list[Signal]:
    out = []
    for k, event in enumerate(events):
        r = reaction_index(bars, event)
        if r is None:
            continue
        seen = reaction(bars, spy_close, r)
        if seen is None:
            continue
        if (seen["abnormal"] < params["min_abnormal"]
                or seen["close_loc"] < params["min_close_loc"]
                or seen["rvol"] < params["min_rvol"]):
            continue
        nxt = events[k + 1]["filed"] if k + 1 < len(events) else None
        out.append(Signal(ticker=ticker, date=bars[r]["date"], stop=bars[r]["low"],
                          max_hold=params["max_hold"], setup=SETUP, exit_before=nxt,
                          meta={**seen, "accepted": event["accepted"]}))
    return out
