"""The trade-plan simulator: sizing from the invalidation, and every tie broken against the trade."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from edgedesk.research.plan import Plan, simulate_plan


def bars(rows, start=date(2025, 1, 6)):
    return [{"date": (start + timedelta(days=i)).isoformat(), "open": o, "high": h,
             "low": lo, "close": c, "volume": 1000} for i, (o, h, lo, c) in enumerate(rows)]


FLAT = (100, 100.5, 99.5, 100)


def test_shares_come_from_the_dollar_risk_over_the_distance_to_invalidation():
    b = bars([FLAT, (100, 101, 99.5, 100.5), (100.5, 101, 100, 100.8)])
    t = simulate_plan(Plan("X", "s", b[0]["date"], stop=96.0, entry_level=100.0), b, 0,
                      "stop_and_time", max_hold=2)
    assert t["shares"] == 250 and t["position"] == 25000          # $1,000 / $4


def test_a_buy_stop_only_fills_if_price_trades_through_it_and_expires_otherwise():
    b = bars([FLAT, (99, 99.8, 98.5, 99), (99, 99.9, 98.7, 99.5), (99.5, 103, 99.4, 102)])
    plan = Plan("X", "s", b[0]["date"], stop=97.0, entry_level=100.01, valid_sessions=2)
    assert simulate_plan(plan, b, 0, "stop_and_time") is None        # expired before day 3
    live = Plan("X", "s", b[0]["date"], stop=97.0, entry_level=100.01, valid_sessions=3)
    assert simulate_plan(live, b, 0, "stop_and_time", max_hold=1)["entry"] == 100.01


def test_a_gap_over_the_buy_stop_fills_at_the_open_and_costs_the_difference():
    b = bars([FLAT, (103, 104, 102.5, 103.5)])
    t = simulate_plan(Plan("X", "s", b[0]["date"], stop=97.0, entry_level=100.0), b, 0,
                      "stop_and_time", max_hold=1)
    assert t["entry"] == 103


def test_half_at_one_r_then_breakeven_on_the_rest():
    b = bars([FLAT, (100, 100.5, 99, 100), (100, 104.5, 100, 104), (104, 104, 99.5, 100)])
    t = simulate_plan(Plan("X", "s", b[0]["date"], stop=96.0), b, 0, "half_1R_BE_2R")
    # half out at 104 (+1R), the rest stopped at the 100 entry: +0.5R before costs
    assert t["reason"] == "breakeven"
    assert t["r"] == pytest.approx(0.5, abs=0.03)


def test_a_session_touching_both_the_stop_and_the_target_counts_as_the_stop():
    b = bars([FLAT, (100, 100.5, 99.5, 100), (100, 109, 95, 104)])
    t = simulate_plan(Plan("X", "s", b[0]["date"], stop=96.0), b, 0, "all_2R")
    assert t["reason"] == "stop" and t["r"] == pytest.approx(-1.0, abs=0.03)


def test_no_target_fills_on_the_entry_session():
    b = bars([FLAT, (100, 110, 99.5, 100), (100, 100.5, 99.5, 100)])
    t = simulate_plan(Plan("X", "s", b[0]["date"], stop=96.0), b, 0, "all_2R", max_hold=2)
    assert t["reason"] == "time"


def test_a_gap_through_the_stop_loses_more_than_one_r():
    b = bars([FLAT, (100, 100.5, 99.5, 100), (90, 91, 89, 90)])
    t = simulate_plan(Plan("X", "s", b[0]["date"], stop=96.0), b, 0, "all_2R")
    assert t["reason"] == "gap_stop" and t["r"] < -2.4


def test_time_only_ignores_the_stop_entirely():
    b = bars([FLAT, (100, 100.5, 90, 95), (95, 101, 94, 101)])
    t = simulate_plan(Plan("X", "s", b[0]["date"], stop=96.0), b, 0, "time_only", max_hold=2)
    assert t["reason"] == "time" and t["ret"] == pytest.approx(0.01)


def test_pivots_are_dated_to_when_they_were_confirmed_not_when_they_happened():
    from edgedesk.research import technical
    lows = [10, 9, 8, 7, 6, 7, 8, 9, 10, 11]
    b = bars([(x + 0.5, x + 1, x, x + 0.5) for x in lows])
    p = technical.pivots(technical.frame(b), span=3)
    assert p["last_pivot_low"].iloc[6] != 6          # the low printed on row 4; not known yet
    assert p["last_pivot_low"].iloc[7] == 6          # three bars later it is
