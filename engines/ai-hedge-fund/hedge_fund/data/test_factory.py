"""Data provider selection and the volatile-cache expiry rule."""

import os
import time
from datetime import date

import pytest

from hedge_fund.data import factory
from hedge_fund.data.cached import CachedDataClient
from hedge_fund.data.models import Price

_ENV = ("HEDGE_FUND_DATA_PROVIDER", "FINANCIAL_DATASETS_API_KEY", "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "ALPACA_TRADING_KEY",
        "ALPACA_TRADING_SECRET", "ALPACA_ENV_FILE", "SEC_USER_AGENT")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in _ENV:
        monkeypatch.delenv(var, raising=False)


def test_provider_follows_keys_unless_explicit(monkeypatch):
    assert factory.data_provider() == "free"
    monkeypatch.setenv("FINANCIAL_DATASETS_API_KEY", "fd")
    assert factory.data_provider() == "financialdatasets"
    monkeypatch.setenv("HEDGE_FUND_DATA_PROVIDER", "free")
    assert factory.data_provider() == "free"
    monkeypatch.setenv("HEDGE_FUND_DATA_PROVIDER", "bloomberg")
    with pytest.raises(ValueError):
        factory.data_provider()


def test_missing_credentials_for_free_stack(monkeypatch):
    assert [env for _, env in factory.missing_data_credentials()] == [
        "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "SEC_USER_AGENT"]
    monkeypatch.setenv("APCA_API_KEY_ID", "k")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "s")
    monkeypatch.setenv("SEC_USER_AGENT", "Name you@example.com")
    assert factory.missing_data_credentials() == []


class Counting:
    def __init__(self):
        self.calls = 0

    def get_prices(self, ticker, start_date, end_date, interval="day", interval_multiplier=1):
        self.calls += 1
        return [Price(open=1, close=1, high=1, low=1, volume=1, time=f"{end_date}T00:00:00Z")]

    def get_earnings_history(self, ticker, limit=12):
        self.calls += 1
        return []


def _age_all(cache_dir, seconds):
    old = time.time() - seconds
    for path in cache_dir.iterdir():
        os.utime(path, (old, old))


def test_requests_reaching_today_expire_but_history_does_not(tmp_path):
    inner = Counting()
    cached = CachedDataClient(inner, cache_dir=tmp_path)
    today = date.today().isoformat()

    cached.get_prices("MSFT", "2020-01-01", "2020-12-31")
    cached.get_prices("MSFT", "2026-01-01", today)
    cached.get_earnings_history("MSFT")
    _age_all(tmp_path, 7 * 3600)

    cached.get_prices("MSFT", "2020-01-01", "2020-12-31")  # history: still a hit
    assert inner.calls == 3
    cached.get_prices("MSFT", "2026-01-01", today)  # reaches today: refetched
    cached.get_earnings_history("MSFT")  # undated: refetched
    assert inner.calls == 5


def test_alpaca_keys_can_come_from_another_projects_env_file(monkeypatch, tmp_path):
    shared = tmp_path / "bot.env"
    shared.write_text("ALPACA_TRADING_KEY=k\nALPACA_TRADING_SECRET=s\n")
    monkeypatch.setenv("ALPACA_ENV_FILE", str(shared))
    monkeypatch.setenv("SEC_USER_AGENT", "Name you@example.com")
    assert factory.missing_data_credentials() == []
