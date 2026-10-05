"""Price anchors and return series computed from daily bars.

Extends the desk's `desk/desk/levels.py` (moving averages, 52-week range, ATR)
with the return, volatility and volume measures the factor engine needs. Pure
standard library and pure function: given the same bars it returns the same
numbers, which is what makes a run reproducible.

Everything here is derived from bars the caller already fetched point-in-time,
so nothing in this module can leak a future price.
"""

from __future__ import annotations

import math

# A year of trading days, and the windows the horizons care about.
YEAR = 252
WINDOWS = {"1w": 5, "1m": 21, "3m": 63, "6m": 126, "12m": 252}


def _sma(closes: list[float], n: int) -> float | None:
    return round(sum(closes[-n:]) / n, 4) if len(closes) >= n else None


def _ret(closes: list[float], n: int) -> float | None:
    """Total return over the last *n* trading days, as a fraction."""
    if len(closes) <= n or closes[-n - 1] == 0:
        return None
    return round(closes[-1] / closes[-n - 1] - 1, 6)


def true_ranges(bars: list[dict]) -> list[float]:
    return [max(cur["high"] - cur["low"],
                abs(cur["high"] - prev["close"]),
                abs(cur["low"] - prev["close"]))
            for prev, cur in zip(bars, bars[1:])]


def compute_anchors(bars: list[dict]) -> dict:
    """Price structure from `[{"date","open","high","low","close","volume"}]`,
    oldest first. Missing windows come back None rather than being faked from a
    shorter history: a 60-day-old listing has no 200-day average and the factor
    that wants one must abstain."""
    if not bars:
        return {}
    closes = [b["close"] for b in bars]
    last = bars[-1]
    tr = true_ranges(bars)
    atr = round(sum(tr[-14:]) / min(14, len(tr)), 4) if tr else None
    year = bars[-YEAR:]
    month = bars[-21:]
    quarter = bars[-63:]
    high_52w = max(b["high"] for b in year)
    low_52w = min(b["low"] for b in year)
    close = last["close"]

    volumes = [b["volume"] for b in bars]
    avg_vol_20 = round(sum(volumes[-20:]) / min(20, len(volumes)), 0) if volumes else None
    avg_vol_60 = round(sum(volumes[-60:]) / min(60, len(volumes)), 0) if volumes else None

    return {
        "as_of": last["date"],
        "bars_available": len(bars),
        "last_close": round(close, 4),
        "sma_20": _sma(closes, 20),
        "sma_50": _sma(closes, 50),
        "sma_200": _sma(closes, 200),
        "high_20d": round(max(b["high"] for b in month), 4),
        "low_20d": round(min(b["low"] for b in month), 4),
        "high_63d": round(max(b["high"] for b in quarter), 4),
        "low_63d": round(min(b["low"] for b in quarter), 4),
        "high_52w": round(high_52w, 4),
        "low_52w": round(low_52w, 4),
        "atr_14": atr,
        "atr_pct": round(atr / close, 6) if atr and close else None,
        "pct_from_52w_high": round(close / high_52w - 1, 6) if high_52w else None,
        "pct_from_52w_low": round(close / low_52w - 1, 6) if low_52w else None,
        "avg_volume_20": avg_vol_20,
        "avg_volume_60": avg_vol_60,
        "volume_trend": (round(avg_vol_20 / avg_vol_60, 4)
                         if avg_vol_20 and avg_vol_60 else None),
        "returns": {k: _ret(closes, n) for k, n in WINDOWS.items()},
        "volatility_annual": annual_volatility(closes),
        "max_drawdown_1y": max_drawdown(closes[-YEAR:]),
    }


def annual_volatility(closes: list[float], window: int = 63) -> float | None:
    """Annualized standard deviation of daily log returns over *window* days."""
    series = closes[-(window + 1):]
    if len(series) < 21:
        return None
    rets = [math.log(b / a) for a, b in zip(series, series[1:]) if a > 0 and b > 0]
    if len(rets) < 20:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return round(math.sqrt(var) * math.sqrt(YEAR), 6)


def max_drawdown(closes: list[float]) -> float | None:
    """Worst peak-to-trough fall in the series, as a negative fraction."""
    if len(closes) < 2:
        return None
    peak, worst = closes[0], 0.0
    for c in closes:
        peak = max(peak, c)
        if peak > 0:
            worst = min(worst, c / peak - 1)
    return round(worst, 6)


def relative_strength(stock: list[float], bench: list[float]) -> dict[str, float | None]:
    """Excess return of *stock* over *bench* per window.

    Both series must be closes for the same trading days, oldest last. They are
    compared by position from the end, so a benchmark missing a day the stock
    traded would silently shift the comparison: `align_closes` exists to stop
    that happening.
    """
    out: dict[str, float | None] = {}
    for label, n in WINDOWS.items():
        a, b = _ret(stock, n), _ret(bench, n)
        out[label] = round(a - b, 6) if a is not None and b is not None else None
    return out


def align_closes(a: list[dict], b: list[dict]) -> tuple[list[float], list[float]]:
    """Closes for the dates both series share, oldest first.

    Relative strength compares two price series day by day; if one has a
    trading day the other lacks (a halt, a late listing, a benchmark gap), a
    positional comparison quietly measures different periods. Intersecting the
    dates first makes that impossible.
    """
    by_date = {row["date"]: row["close"] for row in b}
    shared = [(row["date"], row["close"]) for row in a if row["date"] in by_date]
    return [c for _, c in shared], [by_date[d] for d, _ in shared]
