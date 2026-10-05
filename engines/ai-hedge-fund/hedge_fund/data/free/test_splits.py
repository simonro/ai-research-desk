"""A split the feed forgot to adjust is restated; an adjusted one is left alone."""

from datetime import date, timedelta

from hedge_fund.data.free.splits import repair_unadjusted_splits
from hedge_fund.data.models import Price


def _bars(closes, start=date(2024, 7, 8)):
    return [Price(open=c, high=c * 1.01, low=c * 0.99, close=c, volume=1000,
                  time=(start + timedelta(days=i)).isoformat() + "T04:00:00Z")
            for i, c in enumerate(closes)]


def test_a_missed_split_is_restated():
    fixed, notes = repair_unadjusted_splits(
        _bars([1700.0, 1712.0, 1669.0, 168.0, 166.0]), [(date(2024, 7, 11), 10.0)], "AVGO")
    assert [round(b.close, 1) for b in fixed] == [170.0, 171.2, 166.9, 168.0, 166.0]
    assert fixed[0].volume == 10000 and notes[0]["bars_restated"] == 3


def test_an_adjusted_split_is_left_alone():
    bars = _bars([170.0, 171.2, 166.9, 168.0, 166.0])
    fixed, notes = repair_unadjusted_splits(bars, [(date(2024, 7, 11), 10.0)], "AVGO")
    assert fixed == bars and notes == []


def test_a_real_crash_is_not_mistaken_for_a_missed_split():
    bars = _bars([170.0, 171.0, 169.0, 110.0, 108.0])
    fixed, notes = repair_unadjusted_splits(bars, [(date(2024, 7, 11), 10.0)], "X")
    assert fixed == bars and notes == []
