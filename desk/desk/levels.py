"""Price anchors computed from daily bars, so no memo level is an invented number.

Pure standard library on purpose: the ai-hedge-fund runner imports this file
from inside that engine's own virtualenv.
"""

from __future__ import annotations


def compute_anchors(bars: list[dict]) -> dict:
    """bars: [{"date", "high", "low", "close"}], oldest first. Returns rounded anchors."""
    if not bars:
        return {}
    closes = [b["close"] for b in bars]
    last = bars[-1]

    def sma(n: int) -> float | None:
        return round(sum(closes[-n:]) / n, 2) if len(closes) >= n else None

    true_ranges = []
    for prev, cur in zip(bars, bars[1:]):
        true_ranges.append(max(cur["high"] - cur["low"], abs(cur["high"] - prev["close"]),
                               abs(cur["low"] - prev["close"])))
    atr = round(sum(true_ranges[-14:]) / min(14, len(true_ranges)), 2) if true_ranges else None
    year = bars[-252:]
    month = bars[-20:]
    high_52w = max(b["high"] for b in year)
    low_52w = min(b["low"] for b in year)
    return {
        "as_of": last["date"],
        "last_close": round(last["close"], 2),
        "sma_20": sma(20),
        "sma_50": sma(50),
        "sma_200": sma(200),
        "high_20d": round(max(b["high"] for b in month), 2),
        "low_20d": round(min(b["low"] for b in month), 2),
        "high_52w": round(high_52w, 2),
        "low_52w": round(low_52w, 2),
        "atr_14": atr,
        "pct_from_52w_high": round(last["close"] / high_52w - 1, 4) if high_52w else None,
    }
