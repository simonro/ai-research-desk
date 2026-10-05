"""Rating bands, the quality state, and the conditions that withhold a rating."""

from __future__ import annotations

from datetime import date

import pytest

from edgedesk.evidence.package import DEGRADE, WITHHOLD, collect
from edgedesk.factors.families import compute as compute_families
from edgedesk.verdict.rating import (
    DEGRADED, HORIZONS, MIN_COVERAGE, VALID, WITHHELD, action_for, band_for, rate,
)
from tests.conftest import FakeClient, make_bars, make_metrics

AS_OF = date(2026, 9, 15)


def _rate(ev, horizon="long_term", owns=False):
    return rate(ev, compute_families(ev), horizon, owns=owns)


# ---------------------------------------------------------------------------
# The band table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("score, expected", [
    (100.0, "Buy"), (75.0, "Buy"), (74.9, "Overweight"), (60.0, "Overweight"),
    (59.9, "Hold"), (42.0, "Hold"), (41.9, "Underweight"), (28.0, "Underweight"),
    (27.9, "Sell"), (0.0, "Sell"),
])
def test_bands_are_exactly_as_written(score, expected):
    assert band_for(score) == expected


def test_both_horizons_are_rated_and_may_differ():
    """They are separate questions, not one score read twice."""
    ev = collect("TEST", AS_OF, FakeClient())
    fams = compute_families(ev)
    swing, long_term = rate(ev, fams, "swing"), rate(ev, fams, "long_term")
    assert swing.label != long_term.label
    assert set(HORIZONS) == {"swing", "long_term"}
    # Different weights over the same families must be able to produce different
    # scores; identical scores would mean the horizons are not really separate.
    assert swing.score != long_term.score


def test_risk_is_reported_but_never_summed_into_the_score():
    ev = collect("TEST", AS_OF, FakeClient())
    fams = compute_families(ev)
    verdict = rate(ev, fams, "long_term")
    assert verdict.risk_score is not None
    assert "risk" not in HORIZONS["long_term"]["weights"]
    assert all(c["family"] != "risk" for c in verdict.contributions)


# ---------------------------------------------------------------------------
# Withholding
# ---------------------------------------------------------------------------

def test_ifrs_filer_with_no_gaap_facts_is_withheld():
    ev = collect("TEST", AS_OF, FakeClient(metrics=[]))
    verdict = _rate(ev)
    assert verdict.quality_state == WITHHELD
    assert verdict.rating is None
    assert any("US GAAP" in r for r in verdict.reasons)


def test_stale_companyfacts_feed_withholds_rather_than_rating_old_numbers():
    """A newer 10-Q is on the filing index than the fact feed carries: rating on
    the superseded quarter is the silent error this engine exists to stop."""
    client = FakeClient(metrics=[make_metrics(report_period="2026-03-31",
                                              filing_date="2026-04-25")])
    ev = collect("TEST", AS_OF, client)   # profile says 2026-06-30 was filed 2026-07-29
    assert any(c.code == "companyfacts_stale" and c.severity == WITHHOLD
               for c in ev.caveats)
    assert _rate(ev).quality_state == WITHHELD


def test_unresolvable_share_count_withholds():
    """Without shares, market cap and every per-share valuation are invalid."""
    ev = collect("TEST", AS_OF, FakeClient(metrics=[make_metrics(market_cap=None)]))
    assert any(c.code == "shares_unresolved" for c in ev.caveats)
    assert _rate(ev).quality_state == WITHHELD


def test_short_price_history_withholds():
    ev = collect("TEST", AS_OF, FakeClient(bars=make_bars(30)))
    assert any(c.code == "short_history" for c in ev.caveats)
    assert _rate(ev, "swing").quality_state == WITHHELD


def test_withheld_verdict_publishes_no_rating_and_no_action():
    ev = collect("TEST", AS_OF, FakeClient(metrics=[]))
    verdict = _rate(ev)
    assert verdict.rating is None
    assert verdict.conviction == "None"
    assert "withheld" in (verdict.action or "").lower()


def test_thin_evidence_withholds_even_without_a_blocking_caveat():
    """Nothing is broken, there is simply not enough to judge on."""
    ev = collect("TEST", AS_OF, FakeClient(metrics=[]))
    ev.caveats = [c for c in ev.caveats if c.severity != WITHHOLD]   # drop the blocker
    verdict = _rate(ev)
    assert verdict.coverage < MIN_COVERAGE
    assert verdict.quality_state == WITHHELD
    assert any("Insufficient data" in r for r in verdict.reasons)


# ---------------------------------------------------------------------------
# Degraded and valid
# ---------------------------------------------------------------------------

def test_missing_consensus_degrades_but_still_rates():
    ev = collect("TEST", AS_OF, FakeClient(consensus=False))
    assert any(c.code == "no_consensus" and c.severity == DEGRADE for c in ev.caveats)
    verdict = _rate(ev)
    assert verdict.quality_state == DEGRADED
    assert verdict.rating is not None


def test_clean_evidence_rates_valid():
    ev = collect("TEST", AS_OF, FakeClient())
    ev.caveats = [c for c in ev.caveats if c.severity not in (WITHHOLD, DEGRADE)]
    verdict = _rate(ev)
    assert verdict.quality_state == VALID


# ---------------------------------------------------------------------------
# Action
# ---------------------------------------------------------------------------

def test_action_depends_on_ownership_and_never_on_a_model():
    assert action_for("Buy", owns=False) != action_for("Buy", owns=True)
    assert "Exit" == action_for("Sell", owns=True)
    assert "Avoid" == action_for("Sell", owns=False)


def test_ownership_changes_the_action_but_not_the_rating():
    ev = collect("TEST", AS_OF, FakeClient())
    held, fresh = _rate(ev, owns=True), _rate(ev, owns=False)
    assert held.rating == fresh.rating
    assert held.score == fresh.score
    assert held.action != fresh.action
