"""Session timing: the live daily bar is never treated as a close."""

from datetime import datetime

from hedge_fund.data.models import Price
from hedge_fund.data.session import ET, last_settled_day, settled_since


def _et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def test_last_settled_day_is_yesterday_until_the_close_settles():
    assert str(last_settled_day(_et(2026, 9, 15, 9, 0))) == "2026-09-14"
    assert str(last_settled_day(_et(2026, 9, 15, 14, 25))) == "2026-09-14"
    assert str(last_settled_day(_et(2026, 9, 15, 16, 15))) == "2026-09-15"


def test_cache_written_before_the_close_goes_stale_at_the_close():
    written_midday = _et(2026, 9, 15, 13, 0).timestamp()
    assert not settled_since(written_midday, now=_et(2026, 9, 15, 15, 0))
    assert settled_since(written_midday, now=_et(2026, 9, 15, 17, 0))
    written_after = _et(2026, 9, 15, 16, 30).timestamp()
    assert not settled_since(written_after, now=_et(2026, 9, 15, 17, 0))


def test_free_client_drops_todays_live_daily_bar(monkeypatch):
    from datetime import date

    from hedge_fund.data.free import client as client_module
    from hedge_fund.data.free.client import FreeDataClient

    class Bars:
        def bars(self, *args):
            return [Price(open=1, close=c, high=1, low=1, volume=1, time=f"{d}T04:00:00Z")
                    for d, c in (("2026-09-14", 100.0), ("2026-09-15", 97.0))]

        def close(self):
            pass

    monkeypatch.setattr(client_module, "last_settled_day", lambda: date(2026, 9, 14))
    client = FreeDataClient(alpaca=Bars(), edgar=object(), yahoo=object())
    assert [b.close for b in client.get_prices("ECG", "2026-09-10", "2026-09-15")] == [100.0]
