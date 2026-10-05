"""Factor behaviour: direction, abstention, and the rule that missing is missing."""

from __future__ import annotations

import pytest

from edgedesk.evidence.package import collect
from edgedesk.factors import families as F
from edgedesk.factors.scale import band, blend, inverse, percentile
from tests.conftest import FakeClient, make_metrics


# ---------------------------------------------------------------------------
# The scale itself
# ---------------------------------------------------------------------------

CURVE = [(0.0, 0.0), (0.10, 40.0), (0.25, 70.0), (0.50, 100.0)]


def test_band_interpolates_and_clamps():
    assert band(0.0, CURVE) == 0
    assert band(-5.0, CURVE) == 0                 # clamped, not extrapolated
    assert band(0.10, CURVE) == 40
    assert band(9.0, CURVE) == 100
    assert 40 < band(0.15, CURVE) < 70


def test_band_returns_none_for_missing_input():
    """The single most important line in the factor layer: a gap must not
    become a zero, because zero is a real and very bad value."""
    assert band(None, CURVE) is None
    assert inverse(None, CURVE) is None
    assert percentile(None, [1, 2, 3, 4]) is None


def test_inverse_flips_direction():
    assert inverse(0.0, CURVE) == 100
    assert inverse(0.50, CURVE) == 0


def test_percentile_needs_enough_history():
    assert percentile(10, [5, 20]) is None         # two points is not a range
    assert percentile(10, [5, 8, 12, 20]) == 50.0


def test_blend_renormalizes_over_available_signals():
    score, coverage = blend([(80.0, 0.5), (None, 0.3), (40.0, 0.2)])
    assert score == pytest.approx((80 * 0.5 + 40 * 0.2) / 0.7, abs=0.01)
    assert coverage == pytest.approx(0.7, abs=0.001)


def test_blend_abstains_when_nothing_is_available():
    assert blend([(None, 0.5), (None, 0.5)]) == (None, 0.0)


# ---------------------------------------------------------------------------
# Direction: better inputs must produce better scores
# ---------------------------------------------------------------------------

def _evidence(**metric_overrides):
    return collect("TEST", __import__("datetime").date(2026, 9, 15),
                   FakeClient(metrics=[make_metrics(**metric_overrides)]))


def test_quality_rises_with_margins_and_returns():
    weak = F.quality(_evidence(operating_margin=0.01, net_margin=0.005,
                               return_on_equity=0.03, return_on_assets=0.01))
    strong = F.quality(_evidence(operating_margin=0.32, net_margin=0.26,
                                 return_on_equity=0.40, return_on_assets=0.22))
    assert strong.score > weak.score


def test_growth_rises_with_growth():
    shrinking = F.growth(_evidence(revenue_growth=-0.12, earnings_growth=-0.30,
                                   free_cash_flow_growth=-0.35))
    growing = F.growth(_evidence(revenue_growth=0.35, earnings_growth=0.55,
                                 free_cash_flow_growth=0.65))
    assert growing.score > shrinking.score


def test_risk_rises_with_leverage_the_right_way_round():
    """Risk is the one family where higher means worse, everywhere, always."""
    safe = F.risk(_evidence(debt_to_equity=0.15, interest_coverage=25.0))
    levered = F.risk(_evidence(debt_to_equity=3.5, interest_coverage=1.2))
    assert levered.score > safe.score
    assert safe.direction == "higher_riskier"


def test_valuation_prefers_a_lower_multiple_against_its_own_history():
    history = [make_metrics(report_period=f"2025-0{i}-30", filing_date=f"2025-0{i}-28",
                            price_to_earnings_ratio=pe)
               for i, pe in zip(range(1, 7), (30.0, 32.0, 35.0, 33.0, 31.0, 34.0))]
    import datetime
    cheap = collect("TEST", datetime.date(2026, 9, 15),
                    FakeClient(metrics=[make_metrics(price_to_earnings_ratio=18.0)] + history))
    dear = collect("TEST", datetime.date(2026, 9, 15),
                   FakeClient(metrics=[make_metrics(price_to_earnings_ratio=45.0)] + history))
    assert F.valuation(cheap).score > F.valuation(dear).score


def test_family_abstains_rather_than_scoring_zero_when_inputs_are_absent():
    ev = collect("TEST", __import__("datetime").date(2026, 9, 15), FakeClient(metrics=[]))
    fam = F.quality(ev)
    assert fam.score is None
    assert fam.coverage == 0.0
    assert not fam.available


def test_partial_family_reports_its_coverage():
    ev = _evidence(return_on_equity=None, return_on_assets=None, interest_coverage=None)
    fam = F.quality(ev)
    assert fam.available
    assert 0 < fam.coverage < 1


def test_earnings_surprise_decays_with_age():
    """A beat last week is a signal; the same beat two months ago is history."""
    import datetime
    fresh = collect("TEST", datetime.date(2026, 8, 25), FakeClient())
    stale = collect("TEST", datetime.date(2026, 11, 1), FakeClient())
    fresh_score = F.earnings(fresh).score
    stale_score = F.earnings(stale).score
    assert fresh_score > stale_score > 50      # still a beat, just a fading one


def test_relative_strength_is_its_own_family_not_folded_into_momentum(evidence):
    fams = F.compute(evidence)
    assert "relative_strength" in fams
    assert fams["relative_strength"] is not fams["momentum"]
    assert any(s.fact_id and s.fact_id.startswith("rel.")
               for s in fams["relative_strength"].signals)


# ---------------------------------------------------------------------------
# Valuation at the decision price, not the filing price
# ---------------------------------------------------------------------------

def test_a_price_move_since_the_filing_reaches_every_current_multiple():
    """The filing was priced at 50 and the stock closed at about 150, so every
    current multiple is three times the filing-date one and the yield a third."""
    import datetime
    client = FakeClient(metrics=[make_metrics(
        price_at_filing=50.0, split_factor_to_as_of=1.0, price_to_earnings_ratio=10.0,
        peg_ratio=1.0, free_cash_flow_yield=0.09, market_cap=1.0e11,
        enterprise_value=1.2e11, enterprise_value_to_ebitda_ratio=12.0)])
    ev = collect("TEST", datetime.date(2026, 9, 15), client)
    now, k = ev.valuation_now, ev.valuation_now["scale"]
    assert now["available"] and k == pytest.approx(ev.anchors["last_close"] / 50.0, rel=1e-4)
    assert now["price_to_earnings_ratio"] == pytest.approx(10.0 * k, rel=1e-3)
    assert now["free_cash_flow_yield"] == pytest.approx(0.09 / k, rel=1e-3)
    # Net debt is unchanged, so EV moves by the market cap change only.
    assert now["enterprise_value"] == pytest.approx(1.0e11 * k + 0.2e11, rel=1e-3)

    # The filing-date row is history and must not be rewritten.
    assert ev.metrics[0]["price_to_earnings_ratio"] == 10.0
    assert ev.value("fnd.pe") == pytest.approx(10.0 * k, rel=1e-3)
    assert ev.value("fnd.pe_at_filing") == 10.0

    pe_signal = next(s for s in F.valuation(ev).signals if s.key == "pe_vs_own")
    assert pe_signal.raw == pytest.approx(10.0 * k, rel=1e-3)


def test_a_split_since_the_filing_does_not_read_as_a_price_collapse():
    """Filed at 1,500 before a ten-for-one split, trading at about 150 after it.
    Nothing happened to the valuation, so the scale is one."""
    import datetime
    client = FakeClient(metrics=[make_metrics(
        price_at_filing=1500.0, split_factor_to_as_of=10.0, price_to_earnings_ratio=30.0)])
    ev = collect("TEST", datetime.date(2026, 9, 15), client)
    assert ev.valuation_now["scale"] == pytest.approx(ev.anchors["last_close"] / 150.0, rel=1e-4)


def test_the_panel_and_the_factor_agree_on_the_current_multiple():
    import datetime
    from edgedesk import run as run_mod
    client = FakeClient(metrics=[make_metrics(price_at_filing=60.0,
                                              split_factor_to_as_of=1.0)])
    run = run_mod.analyze("TEST", datetime.date(2026, 9, 15), client=client, save=False)
    price = run["evidence"]["valuation_now"]["price"]
    assert run["valuation"]["price"] == pytest.approx(price, abs=0.01)
    assert run["valuation"]["pe_now"] == pytest.approx(price / 4.0, abs=0.01)


def test_a_valuation_that_cannot_be_restated_says_so():
    import datetime
    client = FakeClient(metrics=[make_metrics(price_to_earnings_ratio=None,
                                              earnings_per_share=-1.0)])
    client.raw_close = lambda ticker, as_of: None
    client._bars = []
    ev = collect("TEST", datetime.date(2026, 9, 15), client)
    assert not (ev.valuation_now or {}).get("available")
    assert any(c.code == "valuation_at_filing_price" for c in ev.caveats)
