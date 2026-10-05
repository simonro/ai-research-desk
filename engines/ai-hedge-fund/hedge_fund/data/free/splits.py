"""Repair a split the price feed forgot to adjust.

Ported from Edge Desk (edgedesk/providers/splits.py, 2026-09-19); keep the two in step.

Alpaca's `adjustment=all` is supposed to restate every bar before a split onto
the post-split basis. It does not always. Broadcom's ten-for-one on 2024-07-15
comes back unadjusted: 1,669 one day, 168 the next, a 90% one-day "crash" that
never happened. Left alone, that poisons every return, average, drawdown, ATR and
forward outcome whose window touches the date.

The corporate-actions feed does know the split. So each known split is checked
against the bars: if the close-to-close move across the ex-date matches the split
ratio, the feed missed it, and the bars before it are restated here. If the move
is ordinary, the feed already adjusted and nothing is touched. The check is on
the data, not on a list of tickers, so the next missed split repairs itself.
"""

from __future__ import annotations

import logging
from datetime import date

from hedge_fund.data.models import Price

logger = logging.getLogger(__name__)

# How close the overnight move must be to the split ratio to count as a miss.
# A real 10:1 split lands within a few percent of 10; a genuine one-day move of
# 40% is extraordinary, so a quarter either way cannot confuse the two for any
# split of 3:2 or larger.
_MATCH = 0.25
_SMALLEST_RATIO = 1.2


def repair_unadjusted_splits(bars: list[Price], splits: list[tuple[date, float]],
                             ticker: str = "") -> tuple[list[Price], list[dict]]:
    """Bars with any missed split restated, and a note of what was repaired."""
    if not bars or not splits:
        return bars, []
    repaired: list[dict] = []
    out = list(bars)
    # Newest split first, so an older split is judged on already-restated bars.
    for ex_date, ratio in sorted(splits, reverse=True):
        if not ratio or ratio <= 0 or abs(ratio - 1.0) < (_SMALLEST_RATIO - 1.0):
            continue
        ex = ex_date.isoformat()
        first_after = next((i for i, b in enumerate(out) if b.time[:10] >= ex), None)
        if first_after is None or first_after == 0:
            continue                      # the window does not straddle the split
        before, after = out[first_after - 1].close, out[first_after].close
        if not before or not after:
            continue
        move = before / after
        if abs(move / ratio - 1.0) > _MATCH:
            continue                      # an ordinary move: the feed adjusted it
        for i in range(first_after):
            b = out[i]
            out[i] = b.model_copy(update={
                "open": b.open / ratio, "high": b.high / ratio, "low": b.low / ratio,
                "close": b.close / ratio, "volume": int(round(b.volume * ratio))})
        repaired.append({"ex_date": ex, "ratio": ratio, "bars_restated": first_after})
        logger.warning("%s: the feed left the %s %g-for-1 split unadjusted; restated %d bars",
                       ticker, ex, ratio, first_after)
    return out, repaired
