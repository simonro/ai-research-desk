"""FreeDataClient: the DataClient protocol on free (or already-paid) sources.

    prices, splits, news     Alpaca REST (SIP with a paid data plan, else IEX)
    earnings surprises       Benzinga Newsdesk headlines on Alpaca's feed,
                             Yahoo as the fallback for names Benzinga skips
    fundamentals, profile    SEC EDGAR, point-in-time by filing date
    analyst consensus,       Yahoo Finance (current-only)
    insider trades

A drop-in for FDClient: same methods, same models, same fail-loud contract.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from hedge_fund.data.free._common import MISS, FreeDataError, TTLMemo, dash_symbol
from hedge_fund.data.free.alpaca import AlpacaClient
from hedge_fund.data.free.benzinga import Release, first_release
from hedge_fund.data.free.splits import repair_unadjusted_splits
from hedge_fund.data.free.edgar import EdgarClient, ReportPeriod, build_metrics
from hedge_fund.data.free.yahoo import YahooClient
from hedge_fund.data.session import last_settled_day
from hedge_fund.data.models import (
    AnalystConsensus,
    CompanyFacts,
    CompanyNews,
    Earnings,
    EarningsData,
    EarningsRecord,
    FinancialMetrics,
    InsiderTrade,
    Price,
)

logger = logging.getLogger(__name__)

# How far back to look for a close when dating a market cap: covers weekends
# and holiday clusters around a filing date.
_PRICE_LOOKBACK_DAYS = 10
_FLOAT_LOOKBACK_DAYS = 480
_STALE_GRACE_DAYS = 3
# An earnings release belongs to the latest fiscal period that ended within
# this many days before it.
_RELEASE_WINDOW_DAYS = 100
# The shortest fiscal quarter a 52/53-week calendar produces (12 weeks).
_MIN_QUARTER_DAYS = 84
_SPLIT_HISTORY_START = date(2005, 1, 1)
# A headline tagged with more tickers than this is a roundup ("Top stocks with
# earnings this week"), not news about the company.
_MAX_NEWS_SYMBOLS = 3
_SPLITS = TTLMemo(12 * 3600)

# SIC divisions, the SEC's own top-level grouping of its industry codes.
_SIC_DIVISIONS = (
    (100, 999, "Agriculture, Forestry & Fishing"),
    (1000, 1499, "Mining"),
    (1500, 1799, "Construction"),
    (2000, 3999, "Manufacturing"),
    (4000, 4999, "Transportation, Communications & Utilities"),
    (5000, 5199, "Wholesale Trade"),
    (5200, 5999, "Retail Trade"),
    (6000, 6799, "Finance, Insurance & Real Estate"),
    (7000, 8999, "Services"),
    (9100, 9729, "Public Administration"),
)


class FreeDataClient:
    # Bump when a change alters what any method returns for the same request.
    # 2: share-count fallbacks for multi-class filers (market cap, P/E, per-share).
    # 3: company facts prefer Yahoo's industry over SEC's SIC label, add description.
    # 4: news drops multi-ticker roundups.
    # 5: daily bars stop at the last settled session (no live intraday "close").
    CACHE_VERSION = 7   # 7: convertible debt, securities as cash, missing capex is unknown
                        # 6: missed splits are repaired, so cached bars before it may be wrong

    def __init__(
        self,
        alpaca: AlpacaClient | None = None,
        edgar: EdgarClient | None = None,
        yahoo: YahooClient | None = None,
    ) -> None:
        self._alpaca = alpaca or AlpacaClient()
        self._edgar = edgar or EdgarClient()
        self._yahoo = yahoo or YahooClient()

    def __enter__(self) -> FreeDataClient:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def close(self) -> None:
        self._alpaca.close()
        self._edgar.close()

    # ------------------------------------------------------------------
    # DataClient protocol
    # ------------------------------------------------------------------

    def get_prices(self, ticker: str, start_date: str, end_date: str,
                   interval: str = "day", interval_multiplier: int = 1) -> list[Price]:
        """Bars from Alpaca. Daily bars stop at the last settled session: during
        market hours today's bar is a live price, not a close (see data/session.py)."""
        bars = self._alpaca.bars(ticker, start_date, end_date, interval, interval_multiplier)
        bars = self._repair_splits(ticker, bars)
        if interval != "day":
            return bars
        settled = last_settled_day().isoformat()
        return [b for b in bars if b.time[:10] <= settled]

    def _repair_splits(self, ticker: str, bars: list[Price]) -> list[Price]:
        """Alpaca's adjusted bars leave some splits unadjusted (AVGO 2024-07-15;
        XLK, XLE, XLY 2025-12-05), which reads as a 50 to 90 percent one-day crash.
        Checked against the corporate-actions list and restated when missed."""
        if not bars:
            return bars
        try:
            known = self._splits(ticker)
        except Exception as exc:                   # noqa: BLE001
            logger.info("splits for %s unavailable, bars left as fetched: %s", ticker, exc)
            return bars
        fixed, _ = repair_unadjusted_splits(bars, known, ticker)
        return fixed

    def get_financial_metrics(self, ticker: str, end_date: str, period: str = "ttm",
                              limit: int = 10) -> list[FinancialMetrics]:
        """TTM metrics PUBLIC as of *end_date* (filtered on filing date)."""
        if period != "ttm":
            raise ValueError(f"FreeDataClient serves period='ttm' only, got {period!r}")
        company = self._edgar.company(ticker)
        if company is None:
            return []
        as_of = date.fromisoformat(end_date[:10])
        visible = [p for p in company.periods if p.filed <= as_of][:limit]
        if not visible:
            return []

        closes = self._alpaca.raw_closes(
            ticker,
            # reaches back far enough to price a public float (share-count fallback)
            min(p.filed for p in visible) - timedelta(days=_FLOAT_LOOKBACK_DAYS),
            max(p.filed for p in visible),
        )
        splits = self._splits(ticker)

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

        return build_metrics(ticker, company, as_of, limit, price_on, split_factor)

    def get_news(self, ticker: str, end_date: str, start_date: str | None = None,
                 limit: int = 1000) -> list[CompanyNews]:
        """Benzinga headlines about *ticker*, newest first, roundups excluded."""
        out: list[CompanyNews] = []
        for item in self._alpaca.iter_news(ticker, end_date, start_date):
            if len(item.get("symbols") or []) > _MAX_NEWS_SYMBOLS:
                continue
            out.append(CompanyNews(
                ticker=ticker,
                title=item.get("headline") or "",
                source=item.get("source") or item.get("author") or "Benzinga",
                date=item.get("created_at"),
                url=item.get("url"),
            ))
            if len(out) >= limit:
                break
        return out

    def get_insider_trades(self, ticker: str, end_date: str, start_date: str | None = None,
                           limit: int = 1000) -> list[InsiderTrade]:
        return self._yahoo.insider_trades(ticker, end_date, start_date, limit)

    def get_company_facts(self, ticker: str) -> CompanyFacts | None:
        """Identity from SEC; sector, industry and description from Yahoo when it
        answers. SEC's SIC codes are coarse and dated (Everus, an electrical and
        mechanical contractor, is filed as "Operative Builders", i.e. a
        homebuilder), and an analyst reasoning from the wrong industry gets the
        risks wrong. Yahoo is enrichment only: if it fails, SIC stands in."""
        profile = self._edgar.profile(ticker)
        if profile is None:
            return None
        try:
            info = self._yahoo.info(ticker)
        except FreeDataError as exc:
            logger.info("%s: Yahoo profile unavailable, using SEC SIC labels: %s", ticker, exc)
            info = {}
        sic_sector = _sic_division(profile["sic"])
        return CompanyFacts(
            ticker=ticker,
            name=profile["name"],
            cik=str(profile["cik"]),
            sector=info.get("sector") or sic_sector,
            industry=info.get("industry") or profile["sic_description"],
            description=info.get("longBusinessSummary"),
            exchange=profile["exchanges"][0] if profile["exchanges"] else None,
            location=profile["location"],
            sec_filings_url=(f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                             f"&CIK={profile['cik']}"),
            sic_code=profile["sic"],
            sic_industry=profile["sic_description"],
            sic_sector=sic_sector,
        )

    def get_earnings_history(self, ticker: str, limit: int = 12) -> list[EarningsRecord]:
        """One record per earnings release, newest first.

        Each fiscal period's release lands between the period end and its
        10-Q/10-K, so the search for its Benzinga headline is confined to that
        window (plus one open window for a quarter that has closed but not
        yet been filed). report_period is the SEC period the release covers,
        so non-calendar fiscal years (NVDA, AAPL) date correctly for PEAD's
        report-to-release lag filter.
        """
        company = self._edgar.company(ticker)
        periods = company.periods if company else []
        ends = [p.end for p in periods]

        releases, gaps = self._benzinga_releases(ticker, periods, limit) if periods else ([], 0)
        records = [_record(ticker, r.published.isoformat(), _period_for(r.day, ends),
                           r.eps, r.eps_estimate, r.revenue, r.revenue_estimate)
                   for r in releases]
        records = [r for r in records if r is not None]

        if not records:  # not covered by Benzinga Newsdesk: Yahoo is the source
            yahoo = self._yahoo.earnings_events(ticker, limit)
        elif gaps:
            # The feed occasionally drops a Newsdesk headline (JPM, Jan 2026).
            # Filling the hole is best-effort: a Yahoo outage must not sink a
            # history that is otherwise complete.
            try:
                yahoo = self._yahoo.earnings_events(ticker, limit)
            except FreeDataError as exc:
                logger.warning("%s: %d quarter(s) missing from Benzinga, Yahoo fill failed: %s",
                               ticker, gaps, exc)
                yahoo = []
        else:
            yahoo = []

        covered = {r.report_period for r in records}
        for e in yahoo:
            rec = _record(ticker, e.announced.isoformat(), _period_for(e.announced.date(), ends),
                          e.reported, e.estimate, None, None)
            if rec is not None and rec.report_period not in covered:
                records.append(rec)
                covered.add(rec.report_period)
        records.sort(key=lambda r: r.filing_datetime or r.filing_date, reverse=True)
        return records[:limit]

    def get_earnings(self, ticker: str) -> Earnings | None:
        history = self.get_earnings_history(ticker, limit=1)
        if not history:
            return None
        latest = history[0]
        return Earnings(ticker=ticker, report_period=latest.report_period,
                        currency=latest.currency, quarterly=latest.quarterly)

    def get_market_cap(self, ticker: str, end_date: str) -> float | None:
        metrics = self.get_financial_metrics(ticker, end_date, limit=1)
        return metrics[0].market_cap if metrics else None

    # ------------------------------------------------------------------
    # Beyond the protocol
    # ------------------------------------------------------------------

    def get_data_caveats(self, ticker: str, as_of: str) -> list[str]:
        """Plain-language warnings about the data behind a live view, for the
        research manager (and the human) to weigh. Current-state only."""
        company = self._edgar.company(ticker)
        if company is None or not company.periods:
            return [f"No SEC XBRL fundamentals for {ticker} (not a US GAAP filer, or an ETF/fund): "
                    "the investor agents had no financial statements to judge."]
        caveats: list[str] = []
        as_of_day = date.fromisoformat(as_of[:10])
        visible = [p for p in company.periods if p.filed <= as_of_day]
        latest = (self._edgar.profile(ticker) or {}).get("latest_report")
        if visible and latest:
            index_filed = date.fromisoformat(latest["filed"])
            if (index_filed <= as_of_day
                    and (index_filed - visible[0].filed).days > _STALE_GRACE_DAYS):
                caveats.append(
                    f"Fundamentals are a quarter stale: SEC's financial-data feed ends at the "
                    f"period ending {visible[0].end} (filed {visible[0].filed}), but a newer "
                    f"{latest['form']} for {latest['period']} was filed {latest['filed']} and is "
                    "not in the feed yet.")
        return caveats

    def get_analyst_consensus(self, ticker: str) -> AnalystConsensus | None:
        """Current sell-side consensus. Not point-in-time: callers must not
        use it for past dates (see AnalystConsensusModel)."""
        return self._yahoo.analyst_consensus(ticker)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _splits(self, ticker: str) -> list[tuple[date, float]]:
        symbol = dash_symbol(ticker)
        hit = _SPLITS.get(symbol)
        if hit is MISS:
            hit = self._alpaca.splits(ticker, _SPLIT_HISTORY_START, date.today())
            _SPLITS.put(symbol, hit)
        return hit

    def _benzinga_releases(self, ticker: str, periods: list[ReportPeriod],
                           limit: int) -> tuple[list[Release], int]:
        """Releases found, newest first, and how many filed quarters had none."""
        windows: list[tuple[date, date, bool]] = []
        next_close = periods[0].end + timedelta(days=_MIN_QUARTER_DAYS)
        today = date.today()
        if today > next_close:  # a quarter may have closed and reported, unfiled
            windows.append((next_close, today, False))
        windows.extend((p.end + timedelta(days=1), p.filed + timedelta(days=1), True)
                       for p in periods)

        releases: list[Release] = []
        gaps = searched = 0
        for start, end, filed in windows:
            if searched >= limit:
                break
            items = self._alpaca.iter_news(ticker, end.isoformat(), start.isoformat())
            release = first_release(items, ticker)
            if release is not None and all(release.day != r.day for r in releases):
                releases.append(release)
            elif release is None and filed:
                gaps += 1
            if filed:
                searched += 1
        return releases, gaps


def _record(ticker: str, published: str, period_end: date | None, eps: float,
            eps_estimate: float | None, revenue: float | None,
            revenue_estimate: float | None) -> EarningsRecord | None:
    if period_end is None:
        return None
    return EarningsRecord(
        ticker=ticker,
        report_period=period_end.isoformat(),
        source_type="8-K",  # the press release; PEAD ranks it as the first announcement
        filing_date=published[:10],
        filing_datetime=published,
        currency="USD",
        quarterly=EarningsData(
            earnings_per_share=eps,
            estimated_earnings_per_share=eps_estimate,
            eps_surprise=_surprise(eps, eps_estimate, digits=2),
            revenue=revenue,
            estimated_revenue=revenue_estimate,
            revenue_surprise=_surprise(revenue, revenue_estimate, digits=-6),
        ),
    )


def _surprise(actual: float | None, estimate: float | None, digits: int) -> str | None:
    """BEAT / MISS / MEET at reporting precision (cents for EPS)."""
    if actual is None or estimate is None:
        return None
    a, e = round(actual, digits), round(estimate, digits)
    return "BEAT" if a > e else "MISS" if a < e else "MEET"


def _period_for(released: date, ends: list[date]) -> date | None:
    """The fiscal period end an earnings release reports on.

    A release always covers the latest quarter that closed before it, so the
    answer is the latest known-or-projected period end before the release.
    Projections (each known end + 1 year) stand in for quarters whose 10-Q
    has not been filed yet.
    """
    # 52/53-week calendars drift a day or so per year; a projection that lands
    # next to a real filed period end defers to the real one.
    projected = {p for p in (_shift_year(end, 1) for end in ends)
                 if all(abs((p - end).days) > 15 for end in ends)}
    candidates = set(ends) | projected
    before = [end for end in candidates
              if end < released and (released - end).days <= _RELEASE_WINDOW_DAYS]
    return max(before) if before else None


def _shift_year(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # Feb 29
        return d.replace(year=d.year + years, day=28)


def _sic_division(sic: str | None) -> str | None:
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return None
    return next((name for lo, hi, name in _SIC_DIVISIONS if lo <= code <= hi), None)
