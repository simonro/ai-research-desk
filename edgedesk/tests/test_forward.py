"""Forward estimates: the level feeds the long-term case, the change feeds the swing call."""

from __future__ import annotations

import pytest

from edgedesk.evidence import business, forward
from edgedesk.verdict import swing

RAW = {"fetched_on": "2026-09-19", "periods": {
    "this_year": {"eps": 9.0, "revenue_growth": 0.50, "eps_30d_ago": 8.8},
    "next_year": {"eps": 15.0, "eps_growth": 0.66, "revenue_growth": 0.40, "eps_30d_ago": 14.0,
                  "eps_90d_ago": 12.5, "up_30d": 40, "down_30d": 2, "analysts": 50}}}


def test_the_forward_block_is_read_at_the_decision_price():
    f = forward.read(RAW, {"price": 225.0}, {"last_close": 999.0})
    assert f["forward_pe"] == pytest.approx(15.0)
    assert f["revision_30d"] == pytest.approx(15.0 / 14.0 - 1, abs=1e-5)
    assert f["revision_90d"] == pytest.approx(0.2)
    assert (f["raised_30d"], f["cut_30d"]) == (40, 2)
    assert forward.read(None, {"price": 1.0}, {}) is None


def test_rising_estimates_score_for_a_swing_and_falling_ones_against():
    up = swing._revisions({"forward": forward.read(RAW, {"price": 225.0}, {})})
    assert up["reading"] > 0.8 and "40 analysts raised and 2 cut" in " ".join(up["notes"])
    cut = {"periods": {"next_year": {"eps": 10.0, "eps_30d_ago": 10.5, "eps_90d_ago": 11.5,
                                     "up_30d": 1, "down_30d": 30}}}
    down = swing._revisions({"forward": forward.read(cut, {"price": 100.0}, {})})
    assert down["reading"] < -0.8
    assert swing._revisions({})["reading"] is None
    assert sum(swing.WEIGHTS.values()) == 100 and swing.WEIGHTS["revisions"] == 10


def test_the_street_growth_is_haircut_and_blended_with_trailing_growth():
    from tests.test_business import rows
    r = rows(growth=0.10)
    plain = business.analyze(r, {"available": True, "market_cap": 400e9}, 3674, 0.0)
    told = business.analyze(r, {"available": True, "market_cap": 400e9}, 3674, 0.0,
                            forward.read(RAW, {"price": 225.0}, {}))
    start = told["scenario_return"]["assumptions"]["starting_growth"]
    assert start["street_after_haircut"] == pytest.approx(0.40 * 0.85)
    assert start["used"] == pytest.approx(0.5 * 0.10 + 0.5 * 0.34)
    assert (told["scenario_return"]["expected_annual_return"]
            > plain["scenario_return"]["expected_annual_return"])
    assert any("Forward P/E 15" in x and "stock" in x for x in told["reasons"])


def test_a_historical_run_never_asks_for_forward_estimates():
    from datetime import date
    from edgedesk.calibrate.harness import CurrentOnlyRequested, HistoricalClient
    hist = HistoricalClient.__new__(HistoricalClient)
    with pytest.raises(CurrentOnlyRequested):
        hist.forward_estimates("NVDA")
