"""What actually happened after a date, measured from bars alone.

Every figure here is computed from price series the caller already holds, which
is what keeps the calibration honest: there is no path from this module to a
provider, so there is no way for it to reach forward for something the engine
could not have known, and no way for it to reach sideways for something the
engine did not use.

The two horizons are measured separately and never share a target. A swing
signal is judged over ten to thirty trading days; a long-term signal over three
to twelve months. Judging both against one window would grade one of them on a
question it was not asked.
"""

from __future__ import annotations

from dataclasses import dataclass

# Trading days, not calendar days: the windows a two-to-six-week hold lives in.
SWING_WINDOWS = {"10d": 10, "20d": 20, "30d": 30}
# Roughly three, six and twelve months of trading days.
LONG_WINDOWS = {"3m": 63, "6m": 126, "12m": 252}
WINDOWS = {"swing": SWING_WINDOWS, "long_term": LONG_WINDOWS}


@dataclass(frozen=True)
class Outcome:
    """One horizon's result at one window, for one (ticker, as_of)."""

    window: str
    bars_available: int
    ret: float | None
    bench_ret: float | None
    excess: float | None
    sector_ret: float | None
    sector_excess: float | None
    mfe: float | None            # best unrealized gain reached inside the window
    mae: float | None            # worst unrealized loss reached inside the window
    drawdown: float | None       # worst peak-to-trough inside the window

    def to_dict(self) -> dict:
        return {
            "window": self.window, "bars": self.bars_available, "ret": self.ret,
            "bench_ret": self.bench_ret, "excess": self.excess,
            "sector_ret": self.sector_ret, "sector_excess": self.sector_excess,
            "mfe": self.mfe, "mae": self.mae, "drawdown": self.drawdown,
        }


def forward_slice(closes: list[float], start_index: int, n: int) -> list[float]:
    """The n bars after *start_index*, or fewer near the end of the history."""
    return closes[start_index + 1: start_index + 1 + n]


def _ret(path: list[float], base: float) -> float | None:
    if not path or not base:
        return None
    return round(path[-1] / base - 1, 6)


def _mfe(path: list[float], base: float) -> float | None:
    if not path or not base:
        return None
    return round(max(path) / base - 1, 6)


def _mae(path: list[float], base: float) -> float | None:
    if not path or not base:
        return None
    return round(min(path) / base - 1, 6)


def _drawdown(path: list[float], base: float) -> float | None:
    if not path or not base:
        return None
    peak, worst = base, 0.0
    for p in path:
        peak = max(peak, p)
        worst = min(worst, p / peak - 1)
    return round(worst, 6)


def measure(horizon: str, closes: list[float], index: int,
            bench: list[float] | None = None,
            sector: list[float] | None = None) -> list[Outcome]:
    """Every window for one horizon, from the bar at *index* forward.

    `closes`, `bench` and `sector` must be aligned to the same trading days, so
    `index` means the same bar in all three. A window with fewer bars than it
    needs is reported with what it had rather than silently truncated, and the
    report drops incomplete windows rather than averaging them in.
    """
    if index < 0 or index >= len(closes):
        return []
    base = closes[index]
    bench_base = bench[index] if bench and index < len(bench) else None
    sector_base = sector[index] if sector and index < len(sector) else None

    out: list[Outcome] = []
    for label, n in WINDOWS[horizon].items():
        path = forward_slice(closes, index, n)
        ret = _ret(path, base)
        bench_path = forward_slice(bench, index, n) if bench else []
        sector_path = forward_slice(sector, index, n) if sector else []
        bench_ret = _ret(bench_path, bench_base) if bench_base else None
        sector_ret = _ret(sector_path, sector_base) if sector_base else None
        out.append(Outcome(
            window=label,
            bars_available=len(path),
            ret=ret,
            bench_ret=bench_ret,
            excess=(round(ret - bench_ret, 6)
                    if ret is not None and bench_ret is not None else None),
            sector_ret=sector_ret,
            sector_excess=(round(ret - sector_ret, 6)
                           if ret is not None and sector_ret is not None else None),
            mfe=_mfe(path, base),
            mae=_mae(path, base),
            drawdown=_drawdown(path, base),
        ))
    return out


def complete(outcome: Outcome, horizon: str) -> bool:
    """Did this window actually get the bars it needed?

    A 12-month window measured over four months is not a 12-month result, and
    letting one into the sample would quietly bias the longest horizon toward
    whatever the most recent months did.
    """
    return outcome.bars_available >= WINDOWS[horizon][outcome.window]
