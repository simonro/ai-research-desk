"""Daily-chart long setups, written as rules a machine can check.

Four come from the TizyCharts strategy notes the author follows (the ones that are
long, daily and need no intraday data). Each keeps its author's structure:
the pattern defines the activation level and the invalidation level, and the
setup is only live in a trending context.

  doji_breakout     a doji in an uptrend; buy-stop above its high for three
                    sessions; invalidation below its low
  doji_inside       a strong up candle, then a doji inside its range; buy-stop
                    above the doji high; invalidation below the doji low
  breakout_retest   a close above the 55-session high on 1.5x volume, then within
                    seven sessions a dip back to that level that closes above it on
                    a bullish candle; enter next open; invalidation below the
                    retest wick
  ema21_reversal    a stacked uptrend pulls back to the 21-session average and
                    prints a hammer or bullish engulfing; enter next open;
                    invalidation below that candle's low

Two controls, because a setup has to beat something:

  trend_control     any session in the same stacked uptrend, entered next open,
                    invalidation at the last confirmed swing low
  any_day_control   any session at all, invalidation two ATR below

Every plan needs an invalidation between 0.5 and 3 ATR from entry. Tighter is not
a real stop (the $1,000 budget would buy an absurd position) and wider is not a
swing trade.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from edgedesk.research import technical
from edgedesk.research.plan import Plan

TICK = 0.01
_MIN_RISK_ATR, _MAX_RISK_ATR = 0.5, 3.0
SETUPS = ("doji_breakout", "doji_inside", "breakout_retest", "ema21_reversal",
          "selloff_in_uptrend", "selloff_swing_low", "momentum_accumulation", "range_breakout",
          "dip_confirmed", "dip_unconfirmed", "slide", "trend_control", "any_day_control")


def _uptrend(f: pd.DataFrame, c: pd.Series) -> pd.Series:
    return (c > f["ema50"]) & (f["slope_50"] > 0) & (c > f["sma200"])


def plans_for(ticker: str, bars: list[dict], control_step: int = 10,
              only: set[str] | None = None) -> list[tuple[int, Plan]]:
    """[(index of the signal session, plan)] for one ticker."""
    df = technical.frame(bars)
    f = technical.features(df)
    c, h, l, o = df["close"], df["high"], df["low"], df["open"]
    atr = f["atr"]
    up = _uptrend(f, c)
    dates = [b["date"] for b in bars]
    out: list[tuple[int, Plan]] = []

    def add(i: int, setup: str, stop: float, entry_level: float | None, valid: int = 1,
            **meta) -> None:
        if only is not None and setup not in only:
            return
        ref = entry_level if entry_level is not None else c.iloc[i]
        a = atr.iloc[i]
        if not np.isfinite(a) or a <= 0 or not np.isfinite(stop):
            return
        risk_atr = (ref - stop) / a
        if not (_MIN_RISK_ATR <= risk_atr <= _MAX_RISK_ATR):
            return
        target = f["last_pivot_high"].iloc[i]
        high_252 = h.iloc[max(0, i - 251):i + 1].max()
        structure = target if np.isfinite(target) and target > ref else high_252
        out.append((i, Plan(ticker, setup, dates[i], round(float(stop), 4),
                            None if entry_level is None else round(float(entry_level), 4),
                            valid, float(structure) if np.isfinite(structure) else None,
                            {"risk_atr": round(float(risk_atr), 2), **meta})))

    doji = f["doji"] > 0
    inside = f["inside_day"] > 0
    rng = h - l
    prior_high_55 = h.shift(1).rolling(55).max()
    breakout = (c > prior_high_55) & (f["rvol"] >= 1.5)
    stacked = f["ma_stack"] >= 0.8

    # S4, declared 2026-09-19 after the factor study: a fast, deep drop inside a
    # long-term uptrend. Two invalidations are compared: 2 ATR, and the lower of
    # that and the last confirmed swing low.
    lt_up = (c > f["sma200"]) & (f["slope_200"] > 0)
    selloff = lt_up & (f["roc_5"] <= -0.08)

    # Three setups the author asked for on 2026-09-20, rules fixed before any result:
    #
    # momentum_accumulation  a leader (12-1 momentum 25%+, up 5%+ over 3 months, long-term
    #   uptrend) under accumulation (up-day volume 1.3x down-day volume over 20 sessions,
    #   4+ accumulation days, at least 2 more than distribution days), not stretched
    #   (within 2 ATR of the 21-day). Fires the first session all of that is true.
    # range_breakout  coiled yesterday (Bollinger width in the bottom 30% of six months),
    #   more volume on up days than down days inside the base, then a close above the
    #   prior 20-session high on 1.5x volume, closing in the top 40% of the bar.
    # dip_confirmed  a 5%+ five-session drop inside a long-term uptrend, then within five
    #   sessions a session that closes above the prior high, up on the day, in the top 40%
    #   of its range: buyers took over. Invalidation under the dip low.
    # dip_unconfirmed  the same dip bought the first day it appears, for comparison.
    leader = lt_up & (f["mom_12_1"] >= 0.25) & (f["roc_63"] >= 0.05)
    accumulating = ((f["updown_vol_20"] >= 1.3) & (f["accum_days_20"] >= 4)
                    & (f["accum_days_20"] - f["distrib_days_20"] >= 2))
    mom_acc = leader & accumulating & (f["ext_ema21_atr"] <= 2.0)
    mom_acc_first = mom_acc & ~mom_acc.shift(1, fill_value=False)
    prior_high_20 = h.shift(1).rolling(20).max()
    range_break = (lt_up & (f["bb_width_pct"].shift(1) <= 0.30) & (f["updown_vol_20"].shift(1) >= 1.1)
                   & (c > prior_high_20) & (f["rvol"] >= 1.5) & (f["close_loc"] >= 0.6))
    dip = lt_up & (f["roc_5"] <= -0.05)
    dip_first = dip & ~dip.shift(1, fill_value=False)
    dip_recent = dip.rolling(5).max().shift(1).fillna(0) > 0
    confirm = (lt_up & dip_recent & (c > h.shift(1)) & (c > o) & (f["close_loc"] >= 0.6))
    confirm_first = confirm & ~confirm.shift(1, fill_value=False)
    low_7 = l.rolling(7).min()
    # slide: 3 ATR or more under the 20-session high in a long-term uptrend, without a
    # 5% five-session drop (that is the dip setups' ground). First session it is true.
    slide = lt_up & ((h.rolling(20).max() - c) / atr >= 3.0) & (f["roc_5"] > -0.05)
    slide_first = slide & ~slide.shift(1, fill_value=False)

    for i in range(260, len(bars) - 1):
        a = atr.iloc[i]
        if bool(mom_acc_first.iloc[i]):
            add(i, "momentum_accumulation", c.iloc[i] - 2.0 * a, None)
        if bool(slide_first.iloc[i]):
            add(i, "slide", c.iloc[i] - 2.0 * a, None)
        if bool(range_break.iloc[i]):
            add(i, "range_breakout", c.iloc[i] - 2.0 * a, None)
        if bool(dip_first.iloc[i]):
            add(i, "dip_unconfirmed", c.iloc[i] - 2.0 * a, None)
        if bool(confirm_first.iloc[i]):
            stop = min(low_7.iloc[i] - 0.1 * a, c.iloc[i] - 1.5 * a)
            add(i, "dip_confirmed", max(stop, c.iloc[i] - 3.0 * a), None)
        if bool(selloff.iloc[i]):
            add(i, "selloff_in_uptrend", c.iloc[i] - 2.0 * a, None)
            swing = f["last_pivot_low"].iloc[i]
            wide = min(c.iloc[i] - 2.0 * a, swing - TICK) if np.isfinite(swing) else np.nan
            add(i, "selloff_swing_low", max(wide, c.iloc[i] - 3.0 * a), None)
        if not bool(up.iloc[i]) and not (i % control_step == 0):
            continue
        if bool(up.iloc[i]):
            if bool(doji.iloc[i]) and rng.iloc[i] <= 1.5 * a:
                if bool(inside.iloc[i]) and f["strong_up"].iloc[i - 1] > 0:
                    add(i, "doji_inside", l.iloc[i] - TICK, h.iloc[i] + TICK, 3)
                else:
                    add(i, "doji_breakout", l.iloc[i] - TICK, h.iloc[i] + TICK, 3)
            # breakout then retest
            for back in range(1, 8):
                j = i - back
                if j < 0 or not bool(breakout.iloc[j]):
                    continue
                level = prior_high_55.iloc[j]
                if (l.iloc[i] <= level + 0.5 * a and l.iloc[i] >= level - 0.5 * a
                        and c.iloc[i] > level and c.iloc[i] > o.iloc[i]
                        and f["close_loc"].iloc[i] >= 0.5):
                    add(i, "breakout_retest", l.iloc[i] - 0.1 * a, None,
                        breakout_level=round(float(level), 2), sessions_since=back)
                break
            if (bool(stacked.iloc[i]) and l.iloc[i] <= f["ema21"].iloc[i] <= c.iloc[i]
                    and (f["hammer"].iloc[i] > 0 or f["bull_engulf"].iloc[i] > 0)):
                add(i, "ema21_reversal", l.iloc[i] - 0.1 * a, None)
            if i % control_step == 0 and bool(stacked.iloc[i]):
                add(i, "trend_control", f["last_pivot_low"].iloc[i] - TICK, None)
        if i % control_step == 0:
            add(i, "any_day_control", c.iloc[i] - 2.0 * a, None)
    return out
