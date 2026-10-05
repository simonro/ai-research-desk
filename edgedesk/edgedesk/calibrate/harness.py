"""Running the engine repeatedly over history, without cheating and without
hammering the provider.

Two problems have to be solved at once. The calibration needs hundreds of runs,
which naively means hundreds of identical price fetches against an API key the
live trading bot shares. And every one of those runs has to see only what was
public on its own date, or the whole exercise is worthless.

`HistoricalClient` solves both with the same trick: fetch each symbol's full
history once, then serve every as-of date a slice of it. Slicing is cheap, and a
slice cannot contain a bar that had not printed yet. The class also refuses
outright to answer the current-only questions (analyst consensus, the earnings
calendar, dividend yield), so a future change to the evidence layer that started
asking for them during a historical run would fail loudly instead of quietly
back-dating today's opinion.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from edgedesk.evidence.package import collect
from edgedesk.models import CompanyNews
from edgedesk.providers._common import dash_symbol
from edgedesk.providers.benzinga import first_release
from edgedesk.providers.client import DataClient
from edgedesk.providers.edgar import build_metrics

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
MARKET_OPEN, MARKET_CLOSE = time(9, 30), time(16, 0)

# Matches the evidence layer's own lookbacks, so a sliced history contains
# everything a live run would have had.
_BAR_LOOKBACK_DAYS = 800
_NEWS_LOOKBACK_DAYS = 120
_PRICE_LOOKBACK_DAYS = 10
_FLOAT_LOOKBACK_DAYS = 500
_SPLIT_START = date(2000, 1, 1)


class CurrentOnlyRequested(RuntimeError):
    """A historical run asked for something that only describes today."""


class HistoricalClient:
    """A DataClient that can only look backwards.

    Holds one full pull per symbol and answers every date from it. Deliberately
    not a subclass: inheriting would mean a new method on DataClient silently
    became a live fetch inside a calibration.
    """

    def __init__(self, live: DataClient, start: date, end: date) -> None:
        self._live = live
        self._start = start
        self._end = end
        self._bars: dict[str, list[dict]] = {}
        self._raw_closes: dict[str, dict[date, float]] = {}
        self._splits: dict[str, list[tuple[date, float]]] = {}
        self._news: dict[str, list[dict]] = {}
        self._profiles: dict[str, dict | None] = {}
        self.fetches = 0

    # -- prefetch ------------------------------------------------------

    def warm(self, ticker: str) -> None:
        """One pull per symbol, covering the whole calibration period."""
        symbol = ticker.upper()
        if symbol in self._bars:
            return
        first = self._start - timedelta(days=_BAR_LOOKBACK_DAYS)
        self._bars[symbol] = self._live.daily_closes(symbol, first, self._end)
        self.fetches += 1

    def warm_fundamentals(self, ticker: str) -> None:
        symbol = ticker.upper()
        if symbol in self._raw_closes:
            return
        first = self._start - timedelta(days=_BAR_LOOKBACK_DAYS + _FLOAT_LOOKBACK_DAYS)
        self._raw_closes[symbol] = self._live.alpaca.raw_closes(symbol, first, self._end)
        self._splits[symbol] = self._live.alpaca.splits(symbol, _SPLIT_START, self._end)
        self.fetches += 2

    def warm_news(self, ticker: str) -> None:
        symbol = ticker.upper()
        if symbol in self._news:
            return
        first = self._start - timedelta(days=_NEWS_LOOKBACK_DAYS)
        self._news[symbol] = list(self._live.alpaca.iter_news(
            symbol, self._end.isoformat(), first.isoformat()))
        self.fetches += 1

    # -- the DataClient surface ---------------------------------------

    def daily_closes(self, ticker: str, start: date, end: date) -> list[dict]:
        symbol = ticker.upper()
        self.warm(symbol)
        lo, hi = start.isoformat(), end.isoformat()
        return [b for b in self._bars[symbol] if lo <= b["date"] <= hi]

    def full_closes(self, ticker: str) -> list[dict]:
        """The whole cached series, for measuring what happened next."""
        self.warm(ticker.upper())
        return self._bars[ticker.upper()]

    def profile(self, ticker: str) -> dict | None:
        symbol = ticker.upper()
        if symbol not in self._profiles:
            self._profiles[symbol] = self._live.profile(symbol)
            self.fetches += 1
        return self._profiles[symbol]

    def metrics(self, ticker: str, as_of: date, limit: int = 12):
        """Point-in-time TTM rows, priced from the cached unadjusted closes."""
        symbol = ticker.upper()
        company = self._live.edgar.company(symbol)
        if company is None:
            return []
        visible = [p for p in company.periods if p.filed <= as_of]
        if not visible:
            return []
        self.warm_fundamentals(symbol)
        closes = self._raw_closes[symbol]
        splits = self._splits[symbol]

        def price_on(day: date) -> float | None:
            for back in range(_PRICE_LOOKBACK_DAYS + 1):
                close = closes.get(day - timedelta(days=back))
                if close is not None:
                    return close
            return None

        def split_factor(after: date, through: date) -> float:
            factor = 1.0
            for ex_date, ratio in splits:
                if after < ex_date <= through:
                    factor *= ratio
            return factor

        return build_metrics(symbol, company, as_of, limit, price_on, split_factor)

    def raw_close(self, ticker: str, as_of: date) -> float | None:
        """The unadjusted close on or just before *as_of*, from the cached series.

        The adjusted series cannot stand in here: it was fetched through today,
        so every bar before a later split is divided by that split, and a price
        on that basis against EPS on the as-of share basis is wrong by the ratio.
        """
        symbol = ticker.upper()
        self.warm_fundamentals(symbol)
        closes = self._raw_closes[symbol]
        for back in range(_PRICE_LOOKBACK_DAYS + 1):
            close = closes.get(as_of - timedelta(days=back))
            if close is not None:
                return close
        return None

    def news(self, ticker: str, end: date, start: date | None = None,
             limit: int = 40) -> list[CompanyNews]:
        symbol = ticker.upper()
        self.warm_news(symbol)
        lo = start.isoformat() if start else ""
        hi = end.isoformat()
        out: list[CompanyNews] = []
        for item in self._news[symbol]:
            when = (item.get("created_at") or "")[:10]
            if not (lo <= when <= hi):
                continue
            if len((item.get("symbols") or [])) > 10:
                continue
            out.append(CompanyNews(ticker=symbol, title=item.get("headline") or "",
                                   source=item.get("source") or "Benzinga",
                                   date=item.get("created_at"), url=item.get("url")))
            if len(out) >= limit:
                break
        return out

    def latest_release(self, ticker: str, since: date, until: date):
        symbol = ticker.upper()
        self.warm_news(symbol)
        lo, hi = since.isoformat(), until.isoformat()
        window = [i for i in self._news[symbol]
                  if lo <= (i.get("created_at") or "")[:10] <= hi]
        return first_release(window, symbol)

    # -- refusals ------------------------------------------------------

    def consensus(self, ticker: str):
        raise CurrentOnlyRequested(
            "analyst consensus has no history and must never enter a calibration")

    def analyst_breakdown(self, ticker: str):
        raise CurrentOnlyRequested(
            "the analyst split has no usable history and must never enter a calibration")

    def forward_estimates(self, ticker: str):
        raise CurrentOnlyRequested(
            "forward estimates describe today's expectations and must never enter a calibration")

    def calendar(self, ticker: str):
        raise CurrentOnlyRequested(
            "the earnings calendar describes the future and must never enter a calibration")

    def dividend_yield(self, ticker: str):
        raise CurrentOnlyRequested(
            "Yahoo's dividend yield is current-only; the filing-derived one is used instead")

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

@dataclass
class Observation:
    """One (ticker, as_of) run, reduced to what the calibration needs."""

    ticker: str
    as_of: str
    horizon: str
    score: float
    rating: str
    conviction: str
    coverage: float
    risk_score: float | None
    quality_state: str
    factors: dict = field(default_factory=dict)
    outcomes: list[dict] = field(default_factory=list)
    # The published calls for this run, so they can be measured like the score is.
    calls: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker, "as_of": self.as_of, "horizon": self.horizon,
            "score": self.score, "rating": self.rating, "conviction": self.conviction,
            "coverage": self.coverage, "risk_score": self.risk_score,
            "quality_state": self.quality_state, "factors": self.factors,
            "outcomes": self.outcomes, "calls": self.calls,
        }


def market_is_open(now: datetime | None = None) -> bool:
    """The hard rule: wide jobs stay out of the session, because this key is
    shared with the live trading bot's rate limit."""
    now_et = (now or datetime.now(ET)).astimezone(ET)
    if now_et.weekday() >= 5:
        return False
    return MARKET_OPEN <= now_et.time() <= MARKET_CLOSE


def trading_dates(bars: list[dict], start: date, end: date, step: int) -> list[date]:
    """Every *step*-th trading day in the window, taken from real bars so a
    sample date is always a day the market was actually open."""
    days = [date.fromisoformat(b["date"]) for b in bars
            if start.isoformat() <= b["date"] <= end.isoformat()]
    return days[::step]


def assert_no_leak(run: dict, as_of: date) -> None:
    """The guard that makes the whole exercise mean something.

    Checked on every observation rather than once at the start, because a leak
    that appears only for some tickers or some dates is exactly the kind that
    survives a spot check.
    """
    ev = run.get("evidence") or {}
    if not ev.get("is_historical"):
        raise AssertionError(f"{run['ticker']} {as_of}: run was not marked historical")
    if ev.get("consensus") is not None:
        raise AssertionError(f"{run['ticker']} {as_of}: current consensus entered the run")
    if ev.get("calendar") is not None:
        raise AssertionError(f"{run['ticker']} {as_of}: the earnings calendar entered the run")
    stamp = as_of.isoformat()
    last_bar = (ev.get("anchors") or {}).get("as_of")
    if last_bar and last_bar > stamp:
        raise AssertionError(f"{run['ticker']} {as_of}: bar dated {last_bar} is in the future")
    for m in ev.get("metrics") or []:
        if (m.get("filing_date") or "") > stamp:
            raise AssertionError(
                f"{run['ticker']} {as_of}: filing dated {m['filing_date']} is in the future")
    for n in ev.get("news") or []:
        if (n.get("date") or "")[:10] > stamp:
            raise AssertionError(f"{run['ticker']} {as_of}: news dated {n['date']} is ahead")
    for fid, fact in (ev.get("facts") or {}).items():
        if fid.startswith("est.") or fid == "evt.next_earnings":
            raise AssertionError(f"{run['ticker']} {as_of}: current-only fact {fid} present")
    report = (ev.get("profile") or {}).get("latest_report") or {}
    if (report.get("filed") or "") > stamp:
        raise AssertionError(
            f"{run['ticker']} {as_of}: the SEC index entry filed {report['filed']} is ahead")
