"""S2: a pullback inside leadership.

The hypothesis. Intermediate momentum persists: the 12-month return that skips
the most recent month (Jegadeesh and Titman; the Fama-French momentum factor is
built the same way). The most recent month does the opposite and mean-reverts.
The withdrawn swing score added the two together, which is why it went negative
at 30 days. This setup separates them: REQUIRE the first, BUY the second.

The rule, fixed before any result was seen:

* leadership: 12-1 momentum in the top third of the universe that day, and the
  stock ahead of its sector ETF over the last 63 sessions
* trend: the 50-session average is higher than it was 10 sessions ago
* pullback: the low since the 20-session closing high is 1 to 2.5 ATR(14) under
  that high, and never closed below the 50-session average
* quiet: average volume over the pullback is below the 20-session average
* trigger: a close above the prior session's high, within 10 sessions of the high
* enter at the next open; stop frozen just under the pullback low
* out on a close below the 50-session average, or at 30 sessions

How it could be wrong: leadership alone explains it (the control variant with no
pullback requirement does as well), random entries in the same names match it,
one name carries it, or it dies in the held-out period.

Variants are declared here with the rule. Only PRIMARY is judged.
"""

from __future__ import annotations

from bisect import bisect_right

from edgedesk.research.simulate import Signal

SETUP = "S2-pullback"
PRIMARY = {"name": "primary", "top_fraction": 1 / 3, "min_atr": 1.0, "max_atr": 2.5,
           "max_hold": 30, "require_leadership": True}
VARIANTS = [
    PRIMARY,
    {**PRIMARY, "name": "no-leadership-control", "require_leadership": False},
    {**PRIMARY, "name": "deeper-pullback", "min_atr": 1.5, "max_atr": 3.5},
    {**PRIMARY, "name": "short-hold", "max_hold": 15},
]
_SKIP, _LOOKBACK, _SECTOR_WINDOW = 21, 252, 63
_HIGH_WINDOW, _TRIGGER_WINDOW, _SLOPE = 20, 10, 10
_STOP_BUFFER_ATR = 0.1


def momentum_table(bars_by_ticker: dict[str, list[dict]]) -> dict[str, list[float]]:
    """date -> sorted 12-1 momentum of every name that has one that day."""
    table: dict[str, list[float]] = {}
    for bars in bars_by_ticker.values():
        for i in range(_LOOKBACK, len(bars)):
            then, recent = bars[i - _LOOKBACK]["close"], bars[i - _SKIP]["close"]
            if then and recent:
                table.setdefault(bars[i]["date"], []).append(recent / then - 1.0)
    for values in table.values():
        values.sort()
    return table


def _atr(bars: list[dict], i: int, n: int = 14) -> float | None:
    if i < n:
        return None
    total = 0.0
    for k in range(i - n + 1, i + 1):
        prev = bars[k - 1]["close"]
        total += max(bars[k]["high"] - bars[k]["low"], abs(bars[k]["high"] - prev),
                     abs(bars[k]["low"] - prev))
    return total / n


def exit_rule(bars: list[dict], entry_i: int, i: int) -> bool:
    if i < 50:
        return False
    sma50 = sum(b["close"] for b in bars[i - 49:i + 1]) / 50
    return bars[i]["close"] < sma50


def make_signal_fn(bars_by_ticker: dict[str, list[dict]], sector_of: dict[str, str],
                   sector_close: dict[str, dict[str, float]]):
    table = momentum_table(bars_by_ticker)

    def signals(ticker, bars, spy_close, events, params):
        out = []
        closes = [b["close"] for b in bars]
        sector = sector_close.get(sector_of.get(ticker, ""), {})
        sums = [0.0]
        for c in closes:
            sums.append(sums[-1] + c)

        def sma(i, n):
            return (sums[i + 1] - sums[i + 1 - n]) / n if i + 1 >= n else None

        for i in range(_LOOKBACK + 1, len(bars)):
            bar, prev = bars[i], bars[i - 1]
            if bar["close"] <= prev["high"]:
                continue                                        # the trigger
            sma_now, sma_then = sma(i, 50), sma(i - _SLOPE, 50)
            if not sma_now or not sma_then or sma_now <= sma_then:
                continue                                        # rising 50-session average
            window = range(i - _HIGH_WINDOW, i)
            peak = max(window, key=lambda k: closes[k])
            if i - peak > _TRIGGER_WINDOW or i - peak < 2:
                continue                                        # a pullback, and a recent one
            atr = _atr(bars, peak)
            if not atr:
                continue
            leg = bars[peak + 1:i]
            low = min(b["low"] for b in leg)
            depth = (closes[peak] - low) / atr
            if not (params["min_atr"] <= depth <= params["max_atr"]):
                continue
            if any(b["close"] < (sma(k, 50) or 0) for k, b in enumerate(leg, peak + 1)):
                continue                                        # it held the 50
            avg20 = sum(b["volume"] for b in bars[peak - 19:peak + 1]) / 20
            if not avg20 or sum(b["volume"] for b in leg) / len(leg) >= avg20:
                continue                                        # a quiet pullback
            mom = closes[i - _SKIP] / closes[i - _LOOKBACK] - 1.0
            ranks = table.get(bar["date"]) or []
            pct = bisect_right(ranks, mom) / len(ranks) if ranks else None
            s_now, s_then = sector.get(bar["date"]), sector.get(bars[i - _SECTOR_WINDOW]["date"])
            vs_sector = ((closes[i] / closes[i - _SECTOR_WINDOW])
                         - (s_now / s_then)) if s_now and s_then else None
            if params["require_leadership"]:
                if pct is None or pct < 1.0 - params["top_fraction"]:
                    continue
                if vs_sector is None or vs_sector <= 0:
                    continue
            out.append(Signal(ticker=ticker, date=bar["date"],
                              stop=low - _STOP_BUFFER_ATR * atr, max_hold=params["max_hold"],
                              setup=SETUP,
                              meta={"momentum_12_1": round(mom, 4),
                                    "momentum_pct": None if pct is None else round(pct, 3),
                                    "vs_sector_63": None if vs_sector is None else round(vs_sector, 4),
                                    "depth_atr": round(depth, 2)}))
        return out

    return signals
