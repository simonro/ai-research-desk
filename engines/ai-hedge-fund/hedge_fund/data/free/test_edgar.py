"""EDGAR TTM + point-in-time tests on a synthetic companyfacts document."""

from datetime import date

import pytest

from hedge_fund.data.free import FreeDataError
from hedge_fund.data.free import edgar as edgar_module
from hedge_fund.data.free.edgar import EdgarClient, build_metrics, parse_companyfacts, pit_values, ttm


def _row(start, end, val, filed, form="10-Q", accn=None):
    row = {"end": end, "val": val, "filed": filed, "form": form,
           "accn": accn or f"{form}-{filed}"}
    if start:
        row["start"] = start
    return row


def _payload(extra_net_income=(), cover_shares=None):
    """A July-June fiscal year filer (like MSFT), numbers in plain units."""
    net_income = [
        _row("2023-07-01", "2024-06-30", 80, "2024-07-30", "10-K"),
        _row("2024-07-01", "2024-09-30", 20, "2024-10-30"),
        _row("2024-07-01", "2025-06-30", 100, "2025-07-30", "10-K"),
        # Q1 FY26 10-Q: current YTD plus the prior-year comparative
        _row("2025-07-01", "2025-09-30", 30, "2025-10-29"),
        _row("2024-07-01", "2024-09-30", 20, "2025-10-29"),
        # A proxy statement repeating the annual number months later
        _row("2024-07-01", "2025-06-30", 100, "2025-11-15", "DEF 14A"),
        *extra_net_income,
    ]
    assets = [
        _row(None, "2024-06-30", 500, "2024-07-30", "10-K"),
        _row(None, "2024-09-30", 510, "2024-10-30"),
        _row(None, "2025-06-30", 600, "2025-07-30", "10-K"),
        _row(None, "2025-09-30", 620, "2025-10-29"),
    ]
    revenue_old = [_row("2023-07-01", "2024-06-30", 400, "2024-07-30", "10-K")]
    revenue_new = [
        _row("2024-07-01", "2025-06-30", 450, "2025-07-30", "10-K"),
        _row("2024-07-01", "2024-09-30", 100, "2025-10-29"),
        _row("2025-07-01", "2025-09-30", 130, "2025-10-29"),
    ]
    equity = [_row(None, "2025-09-30", 300, "2025-10-29"), _row(None, "2025-06-30", 280, "2025-07-30", "10-K")]
    weighted = [_row("2025-07-01", "2025-09-30", 10, "2025-10-29"),
                _row("2024-07-01", "2025-06-30", 10, "2025-07-30", "10-K")]
    facts = {
        "NetIncomeLoss": {"units": {"USD": net_income}},
        "Assets": {"units": {"USD": assets}},
        "SalesRevenueNet": {"units": {"USD": revenue_old}},
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": revenue_new}},
        "StockholdersEquity": {"units": {"USD": equity}},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": weighted}},
    }
    payload = {"cik": 1, "facts": {"us-gaap": facts}}
    if cover_shares:
        payload["facts"]["dei"] = {"EntityCommonStockSharesOutstanding": {"units": {"shares": cover_shares}}}
    return payload


def test_ttm_from_year_to_date_identity():
    company = parse_companyfacts(_payload())
    values = pit_values(company, ["NetIncomeLoss"], date(2025, 11, 1))
    # YTD 30 + prior fiscal year 100 - prior-year YTD 20
    assert ttm(values, date(2025, 9, 30)) == 110
    # a fiscal year end is the 10-K figure itself
    assert ttm(values, date(2025, 6, 30)) == 100


def test_periods_only_exist_once_filed():
    company = parse_companyfacts(_payload())
    rows = build_metrics("X", company, date(2025, 10, 1), 10, lambda d: None, lambda a, b: 1.0)
    assert rows[0].report_period == "2025-06-30"  # the Sept 10-Q is not public yet
    assert rows[0].filing_date == "2025-07-30"
    rows = build_metrics("X", company, date(2025, 10, 29), 10, lambda d: None, lambda a, b: 1.0)
    assert rows[0].report_period == "2025-09-30"


def test_proxy_statements_neither_create_periods_nor_redate_values():
    company = parse_companyfacts(_payload())
    assert [p.end for p in company.periods] == [
        date(2025, 9, 30), date(2025, 6, 30), date(2024, 9, 30), date(2024, 6, 30)]
    assert all(p.accn.startswith("10-") for p in company.periods)


def test_restatement_counts_only_after_it_is_filed():
    restated = _row("2024-07-01", "2025-06-30", 104, "2025-12-01", "10-K/A")
    company = parse_companyfacts(_payload(extra_net_income=[restated]))
    before = pit_values(company, ["NetIncomeLoss"], date(2025, 11, 1))
    after = pit_values(company, ["NetIncomeLoss"], date(2025, 12, 15))
    assert ttm(before, date(2025, 9, 30)) == 110
    assert ttm(after, date(2025, 9, 30)) == 114


def test_concept_fallback_merges_renamed_revenue_tags():
    company = parse_companyfacts(_payload())
    rows = build_metrics("X", company, date(2025, 11, 1), 10, lambda d: None, lambda a, b: 1.0)
    by_period = {r.report_period: r for r in rows}
    # FY25 revenue (new tag) 450 vs FY24 revenue (old tag) 400
    assert by_period["2025-06-30"].revenue_growth == 0.125


def test_market_cap_uses_filing_day_price_and_splits_rebase_per_share():
    company = parse_companyfacts(_payload())
    rows = build_metrics("X", company, date(2025, 11, 1), 1,
                         price_on=lambda d: 50.0 if d == date(2025, 10, 29) else None,
                         split_factor=lambda after, through: 2.0)
    m = rows[0]
    assert m.market_cap == 500  # 10 shares as filed x $50 the day it went public
    assert m.earnings_per_share == 5.5  # 110 / (10 shares x 2-for-1 since filing)
    assert m.price_to_earnings_ratio == round(500 / 110, 4)
    assert m.return_on_equity == round(110 / 300, 4)


def _without_share_counts(payload):
    payload["facts"]["us-gaap"].pop("WeightedAverageNumberOfDilutedSharesOutstanding")
    return payload


def test_multi_class_filer_gets_shares_from_net_income_over_eps():
    payload = _without_share_counts(_payload())
    payload["facts"]["us-gaap"]["EarningsPerShareDiluted"] = {"units": {"USD/shares": [
        _row("2025-07-01", "2025-09-30", 3.0, "2025-10-29")]}}  # quarter NI 30 / EPS 3.0
    company = parse_companyfacts(payload)
    rows = build_metrics("X", company, date(2025, 11, 1), 1, lambda d: 50.0, lambda a, b: 1.0)
    assert rows[0].market_cap == 500  # 10 derived shares x $50


def test_public_float_is_the_last_resort_share_count():
    payload = _without_share_counts(_payload())
    payload["facts"]["dei"] = {"EntityPublicFloat": {"units": {"USD": [
        _row(None, "2024-12-31", 800.0, "2025-07-30", "10-K")]}}}
    company = parse_companyfacts(payload)
    prices = {date(2024, 12, 31): 40.0, date(2025, 10, 29): 50.0}
    rows = build_metrics("X", company, date(2025, 11, 1), 1, lambda d: prices.get(d), lambda a, b: 1.0)
    assert rows[0].market_cap == 1000  # $800 float / $40 = 20 shares, x $50 at filing


def test_cover_page_share_count_wins_when_single_valued():
    cover = [_row(None, "2025-10-20", 12, "2025-10-29", "10-Q", accn="10-Q-2025-10-29")]
    company = parse_companyfacts(_payload(cover_shares=cover))
    rows = build_metrics("X", company, date(2025, 11, 1), 1, lambda d: 50.0, lambda a, b: 1.0)
    assert rows[0].market_cap == 600


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code, self._payload, self.text = status, payload or {}, ""

    def json(self):
        return self._payload


def _search_hit(cik, tickers):
    return {"hits": {"hits": [{"_id": str(cik), "_source": {"tickers": tickers}}]}}


def test_sec_500s_are_retried_and_ciks_are_remembered_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar_module, "_RETRY_DELAYS", (0, 0, 0))
    edgar_module._CIKS.clear()
    responses = [_Resp(500), _Resp(200, _search_hit(1403161, "V"))]
    client = EdgarClient(user_agent="Test test@example.com", cache_dir=tmp_path)
    client._session.get = lambda *a, **kw: responses.pop(0)
    assert client.cik("V") == 1403161

    edgar_module._CIKS.clear()  # a new process: memory gone, disk remains
    fresh = EdgarClient(user_agent="Test test@example.com", cache_dir=tmp_path)
    fresh._session.get = lambda *a, **kw: pytest.fail("should not hit SEC again")
    assert fresh.cik("V") == 1403161


def test_sec_persistent_failure_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(edgar_module, "_RETRY_DELAYS", (0, 0, 0))
    edgar_module._CIKS.clear()
    client = EdgarClient(user_agent="Test test@example.com", cache_dir=tmp_path)
    client._session.get = lambda *a, **kw: _Resp(500)
    with pytest.raises(FreeDataError):
        client.cik("ZZZZ")


def _with(payload, **tags):
    for tag, val in tags.items():
        payload["facts"]["us-gaap"][tag] = {"units": {"USD": [_row(None, "2025-09-30", val, "2025-10-29")]}}
    return payload


def _metrics(payload):
    return build_metrics("X", parse_companyfacts(payload), date(2025, 11, 1), 1,
                         price_on=lambda d: 50.0 if d == date(2025, 10, 29) else None,
                         split_factor=lambda after, through: 1.0)[0]


def test_convertible_notes_are_debt_and_securities_are_cash():
    """AKAM 2026-10-04: $7.56B of convertibles under their own tags was read as no debt."""
    m = _metrics(_with(_payload(), ConvertibleLongTermNotesPayable=60, ConvertibleNotesPayableCurrent=20,
                       CashAndCashEquivalentsAtCarryingValue=15, ShortTermInvestments=25))
    assert m.enterprise_value == 500 + 80 - 40


def test_interest_with_no_readable_debt_leaves_enterprise_value_unknown():
    p = _with(_payload(), CashAndCashEquivalentsAtCarryingValue=15)
    p["facts"]["us-gaap"]["InterestExpense"] = {"units": {"USD": [
        _row("2025-07-01", "2025-09-30", 2, "2025-10-29"), _row("2024-07-01", "2025-06-30", 8, "2025-07-30", "10-K"),
        _row("2024-07-01", "2024-09-30", 2, "2025-10-29")]}}
    assert _metrics(p).enterprise_value is None
    assert _metrics(_with(_payload(), CashAndCashEquivalentsAtCarryingValue=15)).enterprise_value == 485
