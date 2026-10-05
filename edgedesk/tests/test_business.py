"""The long-term business case: arithmetic from filings, every judgement pinned."""

from __future__ import annotations

import pytest

from edgedesk.evidence import business


def rows(n=12, revenue=100e9, growth=0.10, margin=0.25, capex=5e9, dep=5e9, sbc=2e9,
         shares=1e9, shrink=0.0, **latest):
    """Newest first. Revenue compounds backwards at *growth* a year."""
    out = []
    for i in range(n):
        rev = revenue / (1 + growth) ** (i / 4)
        ni = rev * margin
        out.append({"revenue": rev, "net_income": ni, "operating_income": ni * 1.25,
                    "pretax_income": ni * 1.25, "income_tax": ni * 0.25,
                    "operating_cash_flow": ni + dep, "capex": capex,
                    "free_cash_flow": ni + dep - capex, "stock_compensation": sbc,
                    "ebitda": ni * 1.25 + dep, "total_debt": 10e9, "cash": 20e9,
                    "equity": 100e9, "assets": 250e9, "shares": shares * (1 + shrink) ** (i / 4),
                    "revenue_growth": growth, "gross_margin": 0.6, "operating_margin": margin * 1.25,
                    "market_cap": 500e9})
    out[0].update(latest)
    return out


NOW = {"available": True, "market_cap": 500e9}


def test_growth_capex_is_not_charged_but_the_strict_figure_is_kept_beside_it():
    r = rows(capex=30e9, dep=5e9)
    owner, strict = business.owner_earnings_of(r[0], financial=False)
    assert owner == pytest.approx(25e9 + 5e9 - 2e9 - 5e9)          # only depreciation charged
    assert strict == pytest.approx(25e9 + 5e9 - 2e9 - 30e9)
    econ = business.owner_economics(r, False)
    assert any("heavy investment" in f for f in econ["cautions"])
    assert not any("arrived as free cash flow" in f for f in econ["red_flags"]), "one cause, one flag"


def test_a_bank_is_valued_on_net_income_and_never_faded_to_the_market_multiple():
    assert business.is_financial(6021) and not business.is_financial(3674)
    case = business.analyze(rows(), {"available": True, "market_cap": 250e9}, 6021, 0.02)
    assert "financial" in case["owner_economics"]["basis"]
    base = case["scenario_return"]["scenarios"]["base"]["exit_multiple"]
    assert base <= 18.0


def test_the_reverse_dcf_recovers_the_growth_it_was_priced_on():
    owner = 10e9
    ev = business._value(owner, 0.12)
    r = rows()
    r[0].update(total_debt=0.0, cash=0.0)
    got = business.expectations(r, ev, owner, financial=False)
    assert got["implied_growth_5y"] == pytest.approx(0.12, abs=0.002)


def test_an_expensive_stock_is_assumed_to_sell_for_less_and_a_cheap_one_only_a_little_more():
    dear = business.analyze(rows(), {"available": True, "market_cap": 1500e9}, 3674, 0.0)
    cheap = business.analyze(rows(), {"available": True, "market_cap": 150e9}, 3674, 0.0)
    d = dear["scenario_return"]; c = cheap["scenario_return"]
    assert d["scenarios"]["base"]["exit_multiple"] < d["assumptions"]["multiple_now"]
    gap = c["assumptions"]["multiple_anchor"] - c["assumptions"]["multiple_now"]
    assert c["scenarios"]["base"]["exit_multiple"] == pytest.approx(
        c["assumptions"]["multiple_now"] + 0.25 * gap, abs=0.11)
    assert c["expected_annual_return"] > d["expected_annual_return"]
    assert cheap["call"] == "Buy" and dear["call"] == "Sell"


def test_a_fast_grower_keeps_some_growth_and_earns_a_higher_exit_multiple():
    slow = business.analyze(rows(growth=0.06), {"available": True, "market_cap": 900e9}, 3674, 0.0)
    fast = business.analyze(rows(growth=0.35), {"available": True, "market_cap": 900e9}, 3674, 0.0)
    s, f = slow["scenario_return"], fast["scenario_return"]
    assert s["scenarios"]["base"]["revenue_growth_path"][-1] == pytest.approx(0.06)
    assert f["scenarios"]["base"]["revenue_growth_path"][-1] == pytest.approx(0.06 + 0.3 * 0.29, abs=1e-3)
    assert f["assumptions"]["multiple_anchor"] > s["assumptions"]["multiple_anchor"]
    assert f["scenarios"]["base"]["exit_multiple"] <= business.MULTIPLE_CAP


def test_two_quality_flags_hold_back_a_buy():
    flagged = rows(shrink=-0.05)                    # a rising share count and weak cash conversion
    for row in flagged:
        row["operating_cash_flow"] = row["net_income"] * 0.6
        row["free_cash_flow"] = row["operating_cash_flow"] - row["capex"]
        row["total_debt"] = 250e9                   # and heavy debt
    case = business.analyze(flagged, {"available": True, "market_cap": 40e9}, 3674, 0.0)
    assert len(case["owner_economics"]["red_flags"]) >= 2
    assert case["call"] != "Buy"
    assert any("Held back from Buy" in r for r in case["reasons"])


def test_no_owner_earnings_means_no_business_case_and_says_so():
    case = business.analyze(rows(margin=-0.05), NOW, 3674, 0.0)
    assert case["call"] == "Sell"
    assert not case["scenario_return"]["available"]
    assert "profits that do not exist yet" in case["expectations"]["why"]


def test_trajectory_reads_slope_not_level():
    r = rows()
    for i, row in enumerate(r):
        row["revenue_growth"] = 0.30 - 0.03 * (7 - i) if i < 8 else 0.30     # falling toward now
    t = business.trajectory(r)
    assert t["growth"] == "decelerating" and t["revenue_growth_slope_per_year"] < 0


def test_rows_without_raw_figures_produce_no_call():
    case = business.analyze([{"operating_margin": 0.2}], NOW, 3674, None)
    assert case["available"] is False and case["call"] is None


@pytest.mark.parametrize("gap", ["capex", "stock_compensation", "operating_cash_flow"])
def test_a_missing_owner_input_is_unknown_not_zero_and_withholds_the_rating(gap):
    """Stage B2 (R2-06): a row with no capex used to be charged nothing, so owner earnings
    came out as operating cash flow less SBC. Missing is unknown, and unknown withholds."""
    r = rows()
    r[0][gap] = None
    assert business.owner_earnings_of(r[0], financial=False) == (None, None)
    case = business.analyze(r, NOW, 3674, 0.0)
    assert case["call"] is None and not case["available"]
    assert "does not report" in case["why"] and "Sell" not in case["why"]
    assert case["owner_economics"]["owner_inputs_missing"]


def test_missing_stock_compensation_is_not_reported_as_zero_percent_of_revenue():
    r = rows()
    r[0]["stock_compensation"] = None
    assert business.owner_economics(r, False)["stock_comp_to_revenue"] is None


def test_a_bank_needs_no_capex_or_stock_compensation():
    r = rows()
    for row in r:
        row.update(capex=None, stock_compensation=None)
    assert business.owner_earnings_of(r[0], financial=True)[0] == pytest.approx(r[0]["net_income"])
    assert business.analyze(r, {"available": True, "market_cap": 250e9}, 6021, 0.02)["call"]


# ---------------------------------------------------------------------------
# AKAM 2026-10-04: debt read as zero, cash without securities, dilution on basic shares,
# a falling margin called steady. Desk A caught the first three in the debate.
# ---------------------------------------------------------------------------

def _raw(**over):
    base = {k: None for k in ("debt_total_long_term", "debt_noncurrent", "debt_current",
                              "short_term_borrowings", "convertible_debt_total",
                              "convertible_debt_noncurrent", "convertible_debt_current",
                              "investments_current", "investments_noncurrent", "interest_expense")}
    return {**base, "cash": 1.48e9, **over}


def test_convertible_notes_count_as_debt_and_securities_as_cash():
    from edgedesk.providers.edgar import derive_debt
    r = _raw(convertible_debt_noncurrent=5.857e9, convertible_debt_current=1.706e9,
             investments_current=1.868e9, investments_noncurrent=1.232e9, interest_expense=17e6)
    derive_debt(r)
    assert r["total_debt"] == pytest.approx(7.563e9) and not r["debt_unresolved"]
    assert r["cash_and_investments"] == pytest.approx(1.48e9 + 3.1e9)
    # LongTermDebt, when filed, is the total and is not added to the convertibles again.
    r = _raw(debt_total_long_term=4e9, convertible_debt_noncurrent=1e9)
    derive_debt(r)
    assert r["total_debt"] == 4e9


def test_interest_without_a_readable_debt_figure_is_unresolved_not_zero():
    from edgedesk.providers.edgar import derive_debt
    r = _raw(interest_expense=17e6)
    derive_debt(r)
    assert r["total_debt"] is None and r["debt_unresolved"]
    r = _raw()                                          # no debt tag and no interest: debt-free
    derive_debt(r)
    assert r["total_debt"] is None and not r["debt_unresolved"]
    case = business.analyze(rows(debt_unresolved=True), NOW, 3674, 0.0)
    assert case["call"] is None and "interest expense but no debt figure" in case["why"]


def test_net_debt_subtracts_marketable_securities():
    r = rows(total_debt=7.56e9, cash=1.48e9, cash_and_investments=4.62e9)
    econ = business.owner_economics(r, False)
    ebitda = r[0]["ebitda"]
    assert econ["net_debt_to_ebitda"] == pytest.approx((7.56e9 - 4.62e9) / ebitda)


def test_dilution_is_measured_on_diluted_shares_when_filed():
    r = rows(shrink=-0.017)                          # outstanding count shrinking 1.7% a year
    for i, row in enumerate(r):
        row["diluted_shares"] = 1.0e9 * (1 - 0.025) ** (i / 4)    # diluted growing 2.5% a year
    econ = business.owner_economics(r, False)
    assert econ["share_basis"] == "diluted" and econ["share_change_per_year"] == pytest.approx(0.025, abs=1e-3)
    assert any("diluted share count is growing" in f for f in econ["red_flags"])
    plain = business.owner_economics(rows(shrink=-0.017), False)
    assert plain["share_basis"] == "outstanding"


def test_losing_a_tenth_of_the_margin_a_year_is_contracting():
    r = rows()
    for row, m in zip(r, [0.105, 0.123, 0.135, 0.150, 0.129, 0.130, 0.134, 0.144]):   # AKAM, newest first
        row["operating_margin"] = m
    assert business.trajectory(r)["margins"] == "contracting"
    for row in r[:8]:
        row["operating_margin"] = 0.30
    assert business.trajectory(r)["margins"] == "steady"


def test_a_recent_jump_in_diluted_shares_is_flagged_even_when_the_long_rate_is_flat():
    r = rows()
    for i, row in enumerate(r):
        row["diluted_shares"] = 1.0e9 if i < 4 else 0.945e9 if i == 4 else 1.0e9   # +5.8% over the year
    econ = business.owner_economics(r, False)
    assert econ["diluted_change_1y"] == pytest.approx(0.058, abs=1e-3)
    assert any("diluted shares rose 5.8% in the last year" in f for f in econ["red_flags"])
