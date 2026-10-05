"""The chart, read the way a technician reads it, one row per session.

Everything here is causal: a value on row t uses bars up to and including t and
nothing later. Swing pivots are the one place that is easy to get wrong, because
a pivot low is only known to be one after the bars to its right have printed; here
a pivot is dated to the session it was CONFIRMED, not the session it occurred.

Six groups, because a technician asks six different questions:

  trend        is it going up, in order, and is the move mature or young
  momentum     is the push speeding up or fading
  volatility   is it coiled or stretched
  volume       is money coming in or leaving
  structure    where are the swing highs and lows, and are they rising
  price action what did the last few candles say

The groups overlap on purpose at this stage. The factor study decides which
columns carry information that the others do not.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PIVOT_SPAN = 3          # bars each side that must be higher (lower) to make a pivot


def frame(bars: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")[["open", "high", "low", "close", "volume"]].astype(float)


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / down.replace(0, np.nan))


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(),
                      (df["low"] - prev).abs()], axis=1).max(axis=1)


def _adx(df: pd.DataFrame, n: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    up, down = df["high"].diff(), -df["low"].diff()
    plus = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    atr = _true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    pdi = 100 * plus.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / atr
    mdi = 100 * minus.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean(), pdi, mdi


def pivots(df: pd.DataFrame, span: int = PIVOT_SPAN) -> pd.DataFrame:
    """Confirmed swing lows and highs, carried forward from the session each was
    confirmed. `last_pivot_low` on row t is the most recent swing low a trader
    could have known about at the close of t."""
    low, high = df["low"], df["high"]
    window = 2 * span + 1
    is_low = low == low.rolling(window, center=True).min()
    is_high = high == high.rolling(window, center=True).max()
    # Known only `span` sessions later.
    low_known = low.where(is_low).shift(span)
    high_known = high.where(is_high).shift(span)
    out = pd.DataFrame(index=df.index)
    out["last_pivot_low"] = low_known.ffill()
    out["last_pivot_high"] = high_known.ffill()
    out["prev_pivot_low"] = low_known.dropna().shift(1).reindex(df.index).ffill()
    out["prev_pivot_high"] = high_known.dropna().shift(1).reindex(df.index).ffill()
    return out


def features(df: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c, v = (df[k] for k in ("open", "high", "low", "close", "volume"))
    f = pd.DataFrame(index=df.index)
    tr = _true_range(df)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    f["atr"] = atr
    f["atr_pct"] = atr / c

    # -- trend ---------------------------------------------------------
    ema10, ema21, ema50 = (c.ewm(span=n, adjust=False, min_periods=n).mean() for n in (10, 21, 50))
    sma100, sma200 = c.rolling(100).mean(), c.rolling(200).mean()
    f["ema21"], f["ema50"], f["sma100"], f["sma200"] = ema21, ema50, sma100, sma200
    f["ma_stack"] = ((c > ema21).astype(float) + (ema10 > ema21) + (ema21 > ema50)
                     + (ema50 > sma100) + (sma100 > sma200)) / 5
    f.loc[sma200.isna(), "ma_stack"] = np.nan
    f["slope_50"] = ema50 / ema50.shift(10) - 1
    f["slope_200"] = sma200 / sma200.shift(21) - 1
    f["ext_ema21_atr"] = (c - ema21) / atr                # how stretched above the 21
    f["ext_sma200"] = c / sma200 - 1
    adx, pdi, mdi = _adx(df)
    f["adx"] = adx
    f["di_spread"] = pdi - mdi
    f["from_high_252"] = c / h.rolling(252).max() - 1
    f["range_pos_63"] = (c - l.rolling(63).min()) / (h.rolling(63).max() - l.rolling(63).min())

    # -- momentum ------------------------------------------------------
    f["rsi"] = _rsi(c)
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    hist = macd - macd.ewm(span=9, adjust=False).mean()
    f["macd_hist_atr"] = hist / atr
    f["macd_hist_rising"] = (hist > hist.shift(1)).astype(float).rolling(3).sum() / 3
    f["roc_5"], f["roc_21"], f["roc_63"] = c / c.shift(5) - 1, c / c.shift(21) - 1, c / c.shift(63) - 1
    f["mom_12_1"] = c.shift(21) / c.shift(252) - 1
    f["mom_6_1"] = c.shift(21) / c.shift(126) - 1

    # -- volatility and compression -----------------------------------
    std20 = c.rolling(20).std()
    width = 4 * std20 / c.rolling(20).mean()
    f["bb_width_pct"] = width.rolling(126).rank(pct=True)   # low = coiled
    f["atr_ratio_5_20"] = tr.rolling(5).mean() / tr.rolling(20).mean()
    rng = h - l
    f["nr7"] = (rng == rng.rolling(7).min()).astype(float)
    f["inside_day"] = ((h <= h.shift(1)) & (l >= l.shift(1))).astype(float)
    f["vol_63"] = np.log(c / c.shift(1)).rolling(63).std() * np.sqrt(252)

    # -- volume --------------------------------------------------------
    avg20 = v.rolling(20).mean()
    f["rvol"] = v / avg20.shift(1)
    up_day = c > c.shift(1)
    f["updown_vol_20"] = (v.where(up_day, 0).rolling(20).sum()
                          / v.where(~up_day, 0).rolling(20).sum().replace(0, np.nan))
    obv = (np.sign(c.diff()).fillna(0) * v).cumsum()
    f["obv_slope_20"] = (obv - obv.shift(20)) / (avg20 * 20)
    f["accum_days_20"] = (up_day & (v > avg20.shift(1) * 1.25)).astype(float).rolling(20).sum()
    f["distrib_days_20"] = ((~up_day) & (v > avg20.shift(1) * 1.25)).astype(float).rolling(20).sum()
    f["vol_trend"] = avg20 / v.rolling(60).mean()

    # -- structure -----------------------------------------------------
    p = pivots(df)
    f = f.join(p)
    f["higher_lows"] = (p["last_pivot_low"] > p["prev_pivot_low"]).astype(float)
    f["higher_highs"] = (p["last_pivot_high"] > p["prev_pivot_high"]).astype(float)
    f["above_pivot_high"] = (c > p["last_pivot_high"]).astype(float)
    f["dist_pivot_low_atr"] = (c - p["last_pivot_low"]) / atr

    # -- price action --------------------------------------------------
    body = (c - o).abs()
    f["close_loc"] = ((c - l) / rng.replace(0, np.nan))
    f["body_frac"] = body / rng.replace(0, np.nan)
    f["doji"] = (f["body_frac"] <= 0.15).astype(float)
    f["strong_up"] = ((c > o) & (f["body_frac"] >= 0.6) & (rng >= atr.shift(1))).astype(float)
    lower_wick = np.minimum(o, c) - l
    f["hammer"] = ((lower_wick >= 2 * body) & (f["close_loc"] >= 0.6)
                   & (rng >= 0.7 * atr.shift(1))).astype(float)
    f["bull_engulf"] = ((c > o) & (c.shift(1) < o.shift(1)) & (c >= o.shift(1))
                        & (o <= c.shift(1))).astype(float)
    f["gap_pct"] = o / c.shift(1) - 1
    f["new_high_55"] = (c > h.shift(1).rolling(55).max()).astype(float)
    return f
