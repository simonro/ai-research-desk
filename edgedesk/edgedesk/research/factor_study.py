"""Which readings of the chart were followed by better returns, and for how long.

For every feature, on every sampled date, the universe is ranked and cut into
fifths. The number that matters is the spread: what the top fifth did over the
next 1, 2, 4, 8 and 13 weeks minus what the bottom fifth did. Ranking across the
universe on the same date removes the market, so a feature cannot look good just
because stocks went up.

Returns run from the NEXT session's open, because the reading is only known at the
close. Dates are sampled one holding period apart, so the observations do not
overlap and the t-statistic means what it says. Development is everything whose
outcome finished before the holdout starts; the holdout is never used to choose
anything.

With forty-odd features, a few will look significant by luck. A feature is only
believed if the sign and rough size hold in BOTH samples.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from edgedesk.research import technical
from edgedesk.research.data import reaction_index

HORIZONS = {"1w": 5, "2w": 10, "4w": 20, "2m": 42, "3m": 63}
_MIN_NAMES = 60


def _earnings_features(bars: list[dict], events: list[dict], index: pd.DatetimeIndex,
                       spy_close: pd.Series) -> pd.DataFrame:
    """Days since the last report and how the market took it (stock minus SPY on
    the reaction session), carried forward until the next report."""
    out = pd.DataFrame(index=index, columns=["earn_reaction", "days_since_earn"], dtype=float)
    closes = pd.Series([b["close"] for b in bars], index=index)
    for e in events:
        r = reaction_index(bars, e)
        if r is None or r < 1 or r >= len(bars):
            continue
        day, prev = index[r], index[r - 1]
        if day in spy_close.index and prev in spy_close.index:
            out.loc[day, "earn_reaction"] = ((closes.iloc[r] / closes.iloc[r - 1])
                                             - (spy_close[day] / spy_close[prev]))
            out.loc[day, "days_since_earn"] = 0.0
    seen = out["days_since_earn"].notna().cumsum()
    out["days_since_earn"] = out.groupby(seen).cumcount().where(seen > 0)
    out["earn_reaction"] = out["earn_reaction"].ffill()
    # A reaction only informs for about a quarter.
    out.loc[out["days_since_earn"] > 63, "earn_reaction"] = np.nan
    return out


def build_panel(sample: dict, sector_of: dict[str, str]) -> pd.DataFrame:
    spy = technical.frame(sample["bench"])
    frames = []
    for ticker, bars in sample["bars"].items():
        if len(bars) < 300:
            continue
        df = technical.frame(bars)
        f = technical.features(df)
        f = f.join(_earnings_features(bars, sample["events"].get(ticker, []), df.index,
                                      spy["close"]))
        sector = sample["sector_close"].get(sector_of.get(ticker, ""))
        if sector:
            s = pd.Series(sector)
            s.index = pd.to_datetime(s.index)
            s = s.reindex(df.index)
            f["rs_sector_63"] = (df["close"] / df["close"].shift(63)) - (s / s.shift(63))
            f["rs_sector_21"] = (df["close"] / df["close"].shift(21)) - (s / s.shift(21))
        spy_c = spy["close"].reindex(df.index)
        f["rs_spy_63"] = (df["close"] / df["close"].shift(63)) - (spy_c / spy_c.shift(63))
        entry = df["open"].shift(-1)
        spy_entry = spy["open"].reindex(df.index).shift(-1)
        for label, n in HORIZONS.items():
            ret = df["close"].shift(-n) / entry - 1
            bench = spy_c.shift(-n) / spy_entry - 1
            f[f"ret_{label}"] = ret
            f[f"spy_{label}"] = bench
            f[f"fwd_{label}"] = ret - bench
        f["ticker"] = ticker
        f["close"] = df["close"]
        frames.append(f.reset_index())
    return pd.concat(frames, ignore_index=True)


def feature_columns(panel: pd.DataFrame) -> list[str]:
    skip = {"date", "ticker", "close", "atr", "ema21", "ema50", "sma100", "sma200",
            "last_pivot_low", "last_pivot_high", "prev_pivot_low", "prev_pivot_high"}
    return [c for c in panel.columns
            if c not in skip and not c.startswith(("fwd_", "ret_", "spy_"))]


def _spread_series(panel: pd.DataFrame, feature: str, label: str, dates) -> pd.Series:
    sub = panel[panel["date"].isin(dates)][["date", feature, f"fwd_{label}"]].dropna()
    rows = {}
    for day, g in sub.groupby("date"):
        if len(g) < _MIN_NAMES or g[feature].nunique() < 3:
            continue
        if g[feature].nunique() <= 2:                       # a flag, not a scale
            hi, lo = g[g[feature] > 0], g[g[feature] <= 0]
        else:
            rank = g[feature].rank(pct=True)
            hi, lo = g[rank >= 0.8], g[rank <= 0.2]
        if len(hi) >= 5 and len(lo) >= 5:
            rows[day] = hi[f"fwd_{label}"].mean() - lo[f"fwd_{label}"].mean()
    return pd.Series(rows)


def _flag_series(panel: pd.DataFrame, feature: str, label: str, dates) -> pd.Series:
    sub = panel[panel["date"].isin(dates)][["date", feature, f"fwd_{label}"]].dropna()
    rows = {}
    for day, g in sub.groupby("date"):
        on, off = g[g[feature] > 0], g[g[feature] <= 0]
        if len(on) >= 3 and len(off) >= 20:
            rows[day] = on[f"fwd_{label}"].mean() - off[f"fwd_{label}"].mean()
    return pd.Series(rows)


def study(panel: pd.DataFrame, holdout_start: str = "2024-01-01") -> pd.DataFrame:
    """One row per feature and horizon: the top-minus-bottom spread in forward
    excess return, in development and held out, with a t-statistic on
    non-overlapping dates."""
    all_dates = np.sort(panel["date"].unique())
    cut = np.datetime64(holdout_start)
    out = []
    for feature in feature_columns(panel):
        flag = panel[feature].dropna().nunique() <= 2
        for label, n in HORIZONS.items():
            sampled = all_dates[::n]
            finished = {d: all_dates[min(i * n + n, len(all_dates) - 1)]
                        for i, d in enumerate(sampled)}
            dev = [d for d in sampled if finished[d] < cut]
            held = [d for d in sampled if d >= cut]
            row = {"feature": feature, "horizon": label, "kind": "flag" if flag else "rank"}
            for name, dates in (("dev", dev), ("held", held)):
                s = (_flag_series if flag else _spread_series)(panel, feature, label, dates)
                if len(s) >= 8:
                    row[f"{name}_spread"] = s.mean()
                    row[f"{name}_t"] = s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))
                    row[f"{name}_n"] = len(s)
                    row[f"{name}_pos"] = (s > 0).mean()
            out.append(row)
    return pd.DataFrame(out)
