"""Level maths, and the rule that the two horizons answer different questions."""

from __future__ import annotations

from edgedesk.evidence.anchors import compute_anchors
from edgedesk.verdict.levels import compute, long_term_levels, swing_levels
from tests.conftest import make_bars, make_metrics

PANEL = {
    "own_history": {"fair_low": 90.0, "fair_mid": 120.0, "fair_high": 150.0,
                    "price_vs_mid": -0.1},
    "peg": {"fair_value": 130.0},
    "street": {"target_low": 95.0, "target_mean": 135.0, "target_high": 165.0},
}
RELEASE = {"date": "2026-08-20", "days_since": 26}


def _anchors(**kw):
    return compute_anchors(make_bars(300, **kw))


# ---------------------------------------------------------------------------
# Swing
# ---------------------------------------------------------------------------

def test_swing_levels_are_ordered():
    lv = swing_levels(_anchors())
    assert lv["available"]
    entry = lv["entry_zone"]
    assert lv["invalidation"]["price"] < entry["band_low"] <= entry["band_high"]
    assert lv["target_1"]["price"] > entry["band_high"]


def test_swing_targets_are_two_distinct_prices():
    """The 20-day, 3-month and 52-week highs are often the same bar, and two
    targets at one price is not two targets."""
    lv = swing_levels(_anchors())
    assert lv["target_2"]["price"] > lv["target_1"]["price"]


def test_reward_to_risk_matches_the_levels_it_is_derived_from():
    lv = swing_levels(_anchors())
    entry = lv["entry_zone"]
    mid = (entry["band_low"] + entry["band_high"]) / 2
    expected = (lv["target_1"]["price"] - mid) / (mid - lv["invalidation"]["price"])
    assert abs(lv["reward_to_risk"] - round(expected, 2)) < 0.02


def test_swing_abstains_without_enough_price_history():
    lv = swing_levels(compute_anchors(make_bars(1)))
    assert lv["available"] is False
    assert "too short" in lv["why"]


def test_every_swing_level_states_the_rule_that_produced_it():
    lv = swing_levels(_anchors())
    for key in ("entry_zone", "invalidation", "target_1", "target_2"):
        assert lv[key]["rule"], f"{key} has no stated rule"


# ---------------------------------------------------------------------------
# Long term
# ---------------------------------------------------------------------------

def test_accumulation_zone_never_sits_above_the_current_price():
    """A zone that tops out above where the stock already trades would tell you
    to pay up and call it accumulation."""
    anchors = _anchors()
    lv = long_term_levels(anchors, PANEL, make_metrics().model_dump(), RELEASE)
    assert lv["accumulation_zone"]["band_high"] <= anchors["last_close"]
    assert lv["accumulation_zone"]["band_low"] < lv["accumulation_zone"]["band_high"]


def test_valuation_range_keeps_three_methods_separate():
    lv = long_term_levels(_anchors(), PANEL, make_metrics().model_dump(), RELEASE)
    vr = lv["valuation_range"]
    assert vr["own_history_mid"] == 120.0
    assert vr["peg_fair_value"] == 130.0
    assert vr["street_mean"] == 135.0


def test_thesis_invalidation_is_business_conditions_not_a_chart_level():
    """A moving-average break ends a swing trade and means nothing to a
    multi-year thesis, so the long-term break is written as conditions."""
    lv = long_term_levels(_anchors(), PANEL, make_metrics().model_dump(), RELEASE)
    inval = lv["thesis_invalidation"]
    metrics = {c["metric"] for c in inval["conditions"]}
    assert {"Operating margin", "Revenue growth", "Debt to equity"} <= metrics
    assert inval["price_marker"] < _anchors()["last_close"]
    assert "not a stop" in inval["price_rule"].lower()


def test_thesis_conditions_are_anchored_to_this_company_not_to_a_universal_number():
    fat = long_term_levels(_anchors(), PANEL,
                           make_metrics(operating_margin=0.40).model_dump(), RELEASE)
    thin = long_term_levels(_anchors(), PANEL,
                            make_metrics(operating_margin=0.06).model_dump(), RELEASE)

    def floor_for(lv):
        return next(c["breaks_below"] for c in lv["thesis_invalidation"]["conditions"]
                    if c["metric"] == "Operating margin")

    assert floor_for(fat) > floor_for(thin)


def test_next_review_is_marked_as_an_estimate():
    lv = long_term_levels(_anchors(), PANEL, make_metrics().model_dump(), RELEASE)
    assert lv["next_review"]["approximate"] is True
    assert lv["next_review"]["when"] > RELEASE["date"]


def test_long_term_handles_missing_valuation_history():
    lv = long_term_levels(_anchors(), None, make_metrics().model_dump(), None)
    assert lv["available"]
    assert lv["trim_zone"]["price"] is None
    assert lv["next_review"]["when"] is None


# ---------------------------------------------------------------------------
# Shared primitives, different answers
# ---------------------------------------------------------------------------

def test_the_two_horizons_answer_different_questions_from_one_set_of_primitives():
    both = compute(_anchors(), PANEL, make_metrics().model_dump(), RELEASE)
    assert set(both) == {"swing", "long_term"}
    assert "invalidation" in both["swing"] and "entry_zone" in both["swing"]
    assert "thesis_invalidation" in both["long_term"]
    # The swing stop is a chart level near price; the long-term marker is far below.
    assert (both["swing"]["invalidation"]["price"]
            > both["long_term"]["thesis_invalidation"]["price_marker"])
