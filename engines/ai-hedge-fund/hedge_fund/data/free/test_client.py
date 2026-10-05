"""FreeDataClient composition tests: fakes for every provider, no network."""

from datetime import date, datetime, timezone

import pytest

from hedge_fund.data.free import FreeDataError
from hedge_fund.data.free.alpaca import AlpacaClient
from hedge_fund.data.free.benzinga import Release
from hedge_fund.data.free.client import FreeDataClient, _period_for
from hedge_fund.data.free.edgar import Company, ReportPeriod
from hedge_fund.data.free.yahoo import EarningsEvent


class FakeEdgar:
    def __init__(self, periods):
        self._company = Company(cik=1, periods=periods)

    def company(self, ticker):
        return self._company

    def profile(self, ticker):
        return {"cik": 1, "name": "NVIDIA CORP", "sic": "3674",
                "sic_description": "Semiconductors & Related Devices",
                "exchanges": ["Nasdaq"], "location": "SANTA CLARA, CA"}

    def cik(self, ticker):
        return 1

    def close(self):
        pass


class FakeAlpaca:
    def __init__(self, headlines):
        self._headlines = headlines  # created_at -> headline

    def iter_news(self, ticker, end_date, start_date=None):
        for ts in sorted(self._headlines, reverse=True):
            if start_date <= ts[:10] <= end_date:
                yield {"created_at": ts, "headline": self._headlines[ts], "symbols": [ticker]}

    def close(self):
        pass


class FakeYahoo:
    def __init__(self, events=(), error=None, info=None):
        self._events, self._error, self._info = list(events), error, info or {}

    def info(self, ticker):
        if self._error:
            raise self._error
        return self._info

    def earnings_events(self, ticker, limit):
        if self._error:
            raise self._error
        return self._events


def _p(end, filed):
    return ReportPeriod(end=date.fromisoformat(end), filed=date.fromisoformat(filed), accn=filed)


PERIODS = [_p("2026-03-31", "2026-05-01"), _p("2025-12-31", "2026-02-13"), _p("2025-09-30", "2025-11-04")]


def test_period_for_projects_an_unfiled_quarter_from_a_year_earlier():
    ends = [date(2026, 6, 26), date(2025, 9, 26), date(2025, 6, 27)]
    # The Sept 2026 quarter has no 10-Q yet; the release still maps to it.
    assert _period_for(date(2026, 10, 21), ends) == date(2026, 9, 26)
    # A projection next to a real filed end defers to the real one.
    assert _period_for(date(2026, 7, 28), [date(2026, 6, 26), date(2025, 6, 27)]) == date(2026, 6, 26)


def test_gap_in_benzinga_is_filled_from_yahoo():
    alpaca = FakeAlpaca({
        "2026-04-14T10:46:00Z": "JPMorgan Chase Q1 Adj. EPS $5.94 Beats $5.45 Estimate",
        "2025-10-14T10:46:00Z": "JPMorgan Chase Q3 Adj. EPS $5.07 Beats $4.84 Estimate",
    })
    yahoo = FakeYahoo([EarningsEvent(datetime(2026, 1, 13, 11, tzinfo=timezone.utc), 4.85, 5.23)])
    client = FreeDataClient(alpaca=alpaca, edgar=FakeEdgar(PERIODS), yahoo=yahoo)
    history = client.get_earnings_history("JPM", limit=3)
    assert [(r.filing_date, r.report_period, r.quarterly.eps_surprise) for r in history] == [
        ("2026-04-14", "2026-03-31", "BEAT"),
        ("2026-01-13", "2025-12-31", "BEAT"),
        ("2025-10-14", "2025-09-30", "BEAT"),
    ]


def test_yahoo_outage_during_gap_fill_keeps_the_benzinga_history():
    alpaca = FakeAlpaca({"2026-04-14T10:46:00Z": "JPMorgan Chase Q1 Adj. EPS $5.94 Beats $5.45 Estimate"})
    yahoo = FakeYahoo(error=FreeDataError("rate limited"))
    client = FreeDataClient(alpaca=alpaca, edgar=FakeEdgar(PERIODS), yahoo=yahoo)
    assert len(client.get_earnings_history("JPM", limit=3)) == 1


def test_no_benzinga_coverage_falls_back_to_yahoo_and_fails_loud():
    client = FreeDataClient(alpaca=FakeAlpaca({}), edgar=FakeEdgar(PERIODS),
                            yahoo=FakeYahoo(error=FreeDataError("rate limited")))
    with pytest.raises(FreeDataError):
        client.get_earnings_history("SMALL", limit=3)


def test_news_skips_multi_ticker_roundups():
    class NewsAlpaca(FakeAlpaca):
        def iter_news(self, ticker, end_date, start_date=None):
            yield {"created_at": "2026-09-10T12:00:00Z", "headline": "Top stocks with earnings this week",
                   "symbols": ["ECG", "AAPL", "MSFT", "NVDA", "JPM"]}
            yield {"created_at": "2026-09-09T12:00:00Z", "headline": "Everus wins data center contract",
                   "symbols": ["ECG"]}

    client = FreeDataClient(alpaca=NewsAlpaca({}), edgar=FakeEdgar(PERIODS), yahoo=FakeYahoo())
    assert [n.title for n in client.get_news("ECG", "2026-09-15")] == ["Everus wins data center contract"]


def test_company_facts_fall_back_to_sec_sic_labels():
    for yahoo in (FakeYahoo(), FakeYahoo(error=FreeDataError("rate limited"))):
        client = FreeDataClient(alpaca=FakeAlpaca({}), edgar=FakeEdgar(PERIODS), yahoo=yahoo)
        facts = client.get_company_facts("NVDA")
        assert facts.sector == "Manufacturing"
        assert facts.industry == "Semiconductors & Related Devices"


def test_company_facts_prefer_yahoo_industry_and_description():
    yahoo = FakeYahoo(info={"quoteType": "EQUITY", "sector": "Technology", "industry": "Semiconductors",
                            "longBusinessSummary": "Designs GPUs."})
    facts = FreeDataClient(alpaca=FakeAlpaca({}), edgar=FakeEdgar(PERIODS), yahoo=yahoo).get_company_facts("NVDA")
    assert (facts.name, facts.sector, facts.industry) == ("NVIDIA CORP", "Technology", "Semiconductors")
    assert facts.description == "Designs GPUs." and facts.sic_industry == "Semiconductors & Related Devices"


# ---------------------------------------------------------------------------
# Alpaca fail-loud contract
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload or {}, text

    def json(self):
        return self._payload


def _alpaca(responses):
    client = AlpacaClient(api_key="k", secret_key="s")
    client._session.get = lambda *a, **kw: responses.pop(0)
    return client


def test_alpaca_server_error_raises():
    with pytest.raises(FreeDataError):
        _alpaca([_Resp(500, text="boom")]).bars("MSFT", "2026-01-01", "2026-01-31")


def test_alpaca_unknown_symbol_is_empty_but_other_422s_raise():
    assert _alpaca([_Resp(422, text='{"message":"invalid symbol: ZZZZ"}')]).bars(
        "ZZZZ", "2026-01-01", "2026-01-31") == []
    with pytest.raises(FreeDataError):
        _alpaca([_Resp(422, text='{"message":"invalid start"}')]).bars("MSFT", "x", "y")


def test_alpaca_follows_pagination():
    bar = {"o": 1, "h": 2, "l": 1, "c": 2, "v": 10, "t": "2026-01-02T05:00:00Z"}
    client = _alpaca([_Resp(200, {"bars": [bar], "next_page_token": "p2"}),
                      _Resp(200, {"bars": [bar], "next_page_token": None})])
    assert len(client.bars("MSFT", "2026-01-01", "2026-01-31")) == 2


def _no_keys(monkeypatch):
    for var in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY",
                "ALPACA_TRADING_KEY", "ALPACA_TRADING_SECRET", "ALPACA_ENV_FILE"):
        monkeypatch.delenv(var, raising=False)


def test_without_alpaca_keys_prices_come_from_yahoo_and_news_is_empty(monkeypatch):
    """The data chain's last link (2026-10-05): no Alpaca keys, no request to Alpaca at all."""
    from hedge_fund.data.free import alpaca as alpaca_mod
    _no_keys(monkeypatch)
    seen = []
    monkeypatch.setattr(alpaca_mod, "yahoo_bars", lambda *a, **k: seen.append(a) or ["bar"])
    monkeypatch.setattr(alpaca_mod.requests.Session, "get", lambda *a, **k: pytest.fail("Alpaca was called"))
    client = AlpacaClient()
    assert client.source == "yahoo" and client.bars("MSFT", "2026-01-01", "2026-01-31") == ["bar"]
    assert seen[0][0] == "MSFT" and list(client.iter_news("MSFT", "2026-01-31")) == []


def test_an_account_without_sip_falls_back_to_iex(monkeypatch):
    from hedge_fund.data.free import alpaca as alpaca_mod
    calls = []

    class Resp:
        def __init__(self, code, body):
            self.status_code, self._body, self.text = code, body, str(body)

        def json(self):
            return self._body

    def get(self, url, params=None, headers=None, timeout=None):
        calls.append(params["feed"])
        if params["feed"] == "sip":
            return Resp(403, {"message": "subscription does not permit querying recent SIP data"})
        return Resp(200, {"bars": [{"o": 1, "c": 2, "h": 3, "l": 1, "v": 10, "t": "2026-01-02T05:00:00Z"}]})

    monkeypatch.setattr(alpaca_mod.requests.Session, "get", get)
    monkeypatch.delenv("ALPACA_DATA_FEED", raising=False)
    client = AlpacaClient(api_key="k", secret_key="s")
    bars = client.bars("MSFT", "2026-01-01", "2026-01-31")
    assert calls == ["sip", "iex"] and bars[0].close == 2 and client.source == "alpaca-iex"
    client.bars("MSFT", "2026-01-01", "2026-01-31")
    assert calls[-1] == "iex"                       # remembered for the session


def test_release_day_helper():
    r = Release(published=datetime(2026, 7, 29, 20, 6, tzinfo=timezone.utc), eps=1.0, eps_estimate=0.9)
    assert r.day == date(2026, 7, 29)
