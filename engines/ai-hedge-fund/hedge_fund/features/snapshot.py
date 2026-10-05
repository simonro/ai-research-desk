"""Point-in-time fundamentals snapshot — the shared input for LLM analysts.

A `FundamentalsSnapshot` is everything an investor agent is allowed to know
about a company as of a given date: a history of financial metrics (each row
provably public by `as_of` — the data layer filters on filing_date, not
report_period) plus a few derived aggregates computed here in Python so the
LLM reasons over facts instead of re-deriving arithmetic.

The snapshot is pure data: build it once, hash it, feed it to any persona.
`content_hash` is the cache key for LLM calls — an agent only re-reasons
when a new filing changes its snapshot. Both the hash and `render()` exclude
`as_of`: two dates between filings see identical data, and identical data
must produce an identical prompt (a cache hit), not two paid LLM calls.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

from pydantic import BaseModel

from hedge_fund.data.protocol import DataClient

# An agent can't say anything defensible about a company with less history
# than this (one year of ttm rows).
MIN_PERIODS = 4
# How far back to look for a close: weekends and holiday clusters.
_PRICE_LOOKBACK_DAYS = 7


class InsufficientData(ValueError):
    """Not enough point-in-time history to build a snapshot."""


class PeriodFundamentals(BaseModel):
    """One reporting period's key metrics, compacted for prompting."""

    report_period: str
    filing_date: str | None = None
    market_cap: float | None = None
    price_to_earnings_ratio: float | None = None
    return_on_equity: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    debt_to_equity: float | None = None
    current_ratio: float | None = None
    revenue_growth: float | None = None
    earnings_per_share: float | None = None
    book_value_per_share: float | None = None
    free_cash_flow_per_share: float | None = None


class FundamentalsSnapshot(BaseModel):
    """What an analyst may know about *ticker* as of *as_of*. Newest first."""

    ticker: str
    as_of: str
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    periods: list[PeriodFundamentals]

    # Derived aggregates (computed in build_snapshot, not by the LLM)
    roe_avg: float | None = None
    net_margin_avg: float | None = None
    gross_margin_trend: float | None = None  # latest minus oldest
    bvps_cagr: float | None = None
    debt_to_equity_latest: float | None = None
    market_cap_latest: float | None = None

    # Valuation at the latest close on or before as_of. The history rows are
    # priced on each filing date, which can be months stale: a stock that fell
    # 20% since its last 10-Q is 20% cheaper than its filed P/E says. The
    # multiples are the filed ones scaled by the price move since that filing,
    # which keeps them split-safe and provider-agnostic.
    price: float | None = None
    price_date: str | None = None
    price_change_since_filing: float | None = None
    market_cap_now: float | None = None
    pe_now: float | None = None
    pb_now: float | None = None
    fcf_yield_now: float | None = None

    @property
    def content_hash(self) -> str:
        """Stable hash of the fundamentals content — the LLM cache key.

        Excludes `as_of`, but the latest close is part of the content, so a
        new trading day is a new prompt while same-day reruns stay free.
        """
        canonical = self.model_dump_json(exclude={"as_of"})
        return hashlib.sha256(canonical.encode()).hexdigest()[:24]

    def render(self) -> str:
        """Compact text block for the LLM prompt.

        Deliberately date-free (no `as_of`): the prompt cache keys on exact
        prompt text, so the same fundamentals must render identically on any
        date. It also keeps the LLM from anchoring on a calendar date it
        could associate with post-date world events.
        """
        lines = [
            f"Company: {self.ticker}"
            + (f" ({self.name})" if self.name else "")
            + (f"  |  Sector: {self.sector}" if self.sector else "")
            + (f"  |  Industry: {self.industry}" if self.industry else ""),
            (f"Financial figures are as publicly filed. The present day is {self.price_date}, "
             "the date of the latest valuation below."
             if self.price_date else
             "All figures below were publicly filed by their filing dates. "
             "Treat the most recent filing shown as the present."),
            "",
            "Summary:",
            f"  Market cap (latest filed): {_fmt(self.market_cap_latest)}",
            f"  ROE avg: {_fmt(self.roe_avg)}  |  Net margin avg: {_fmt(self.net_margin_avg)}",
            f"  Gross margin trend (latest-oldest): {_fmt(self.gross_margin_trend)}",
            f"  Book value/share CAGR: {_fmt(self.bvps_cagr)}",
            f"  Debt/equity (latest): {_fmt(self.debt_to_equity_latest)}",
            *self._valuation_lines(),
            "",
            "History (trailing-twelve-month periods, newest first):",
            "period | filed | mktcap | P/E | ROE | gross_m | op_m | net_m | D/E "
            "| curr | rev_gr | EPS | BVPS | FCF/sh",
        ]
        for p in self.periods:
            lines.append(
                f"{p.report_period} | {p.filing_date or '?'} | {_fmt(p.market_cap)} "
                f"| {_fmt(p.price_to_earnings_ratio)} | {_fmt(p.return_on_equity)} "
                f"| {_fmt(p.gross_margin)} | {_fmt(p.operating_margin)} "
                f"| {_fmt(p.net_margin)} | {_fmt(p.debt_to_equity)} "
                f"| {_fmt(p.current_ratio)} | {_fmt(p.revenue_growth)} "
                f"| {_fmt(p.earnings_per_share)} | {_fmt(p.book_value_per_share)} "
                f"| {_fmt(p.free_cash_flow_per_share)}"
            )
        return "\n".join(lines)

    def _valuation_lines(self) -> list[str]:
        if self.price is None:
            return []
        lines = ["", f"Valuation at the latest close ({self.price_date}, price {self.price:,.2f}):"]
        if self.market_cap_now is None and self.pe_now is None:
            lines.append("  (no filed valuation to scale; use the history below)")
            return lines
        change = (f"  |  Price since last filing: {self.price_change_since_filing:+.1%}"
                  if self.price_change_since_filing is not None else "")
        lines.append(f"  Market cap: {_fmt(self.market_cap_now)}  |  P/E: {_fmt(self.pe_now)}  |  "
                     f"P/B: {_fmt(self.pb_now)}  |  FCF yield: {_fmt(self.fcf_yield_now)}{change}")
        lines.append("  (History rows below are valued at each filing date's price.)")
        return lines


def _latest_close(data_client: DataClient, ticker: str, day: str | None) -> tuple[float, str] | None:
    """(close, date) of the last bar on or before *day*, or None."""
    fetch = getattr(data_client, "get_prices", None)
    if fetch is None or not day:
        return None
    start = (date.fromisoformat(day[:10]) - timedelta(days=_PRICE_LOOKBACK_DAYS)).isoformat()
    bars = [b for b in fetch(ticker, start, day[:10]) if b.time[:10] <= day[:10]]
    if not bars:
        return None
    last = max(bars, key=lambda b: b.time)
    return last.close, last.time[:10]


def build_snapshot(
    ticker: str,
    as_of: str,
    data_client: DataClient,
    periods: int = 20,
) -> FundamentalsSnapshot:
    """Build the point-in-time snapshot for (ticker, as_of).

    Raises InsufficientData if fewer than MIN_PERIODS filed periods exist.
    Data-layer failures propagate (fail loud) — a broken snapshot must never
    silently become a neutral view.
    """
    metrics = data_client.get_financial_metrics(
        ticker, as_of, period="ttm", limit=periods,
    )
    if len(metrics) < MIN_PERIODS:
        raise InsufficientData(
            f"{ticker} as of {as_of}: only {len(metrics)} filed periods "
            f"(need {MIN_PERIODS})"
        )

    # Market cap comes from the most recent FILED metrics row. Deliberately
    # NOT data_client.get_market_cap(): that prefers company_facts.market_cap,
    # which is latest-only — lookahead in a backtest.
    facts = data_client.get_company_facts(ticker)

    rows = [
        PeriodFundamentals(**m.model_dump(include=set(PeriodFundamentals.model_fields)))
        for m in metrics
    ]

    latest = metrics[0]
    now = _latest_close(data_client, ticker, as_of)
    at_filing = _latest_close(data_client, ticker, latest.filing_date)
    move = now[0] / at_filing[0] if now and at_filing and at_filing[0] else None

    def scaled(value: float | None, by: float | None) -> float | None:
        return None if value is None or by is None else round(value * by, 4)

    return FundamentalsSnapshot(
        price=now[0] if now else None,
        price_date=now[1] if now else None,
        price_change_since_filing=round(move - 1, 4) if move else None,
        market_cap_now=scaled(latest.market_cap, move),
        pe_now=scaled(latest.price_to_earnings_ratio, move),
        pb_now=scaled(latest.price_to_book_ratio, move),
        fcf_yield_now=scaled(latest.free_cash_flow_yield, 1 / move if move else None),
        ticker=ticker,
        as_of=as_of,
        # Sector/industry are slow-moving company attributes; using latest
        # facts here is an accepted, documented PIT approximation.
        name=facts.name if facts else None,
        sector=facts.sector if facts else None,
        industry=facts.industry if facts else None,
        periods=rows,
        roe_avg=_avg([m.return_on_equity for m in metrics]),
        net_margin_avg=_avg([m.net_margin for m in metrics]),
        gross_margin_trend=_trend([m.gross_margin for m in metrics]),
        bvps_cagr=_cagr([m.book_value_per_share for m in metrics]),
        debt_to_equity_latest=metrics[0].debt_to_equity,
        market_cap_latest=metrics[0].market_cap,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _fmt(v: float | None) -> str:
    if v is None:
        return "-"
    if abs(v) >= 1e9:
        return f"{v / 1e9:.1f}B"
    if abs(v) >= 1e6:
        return f"{v / 1e6:.1f}M"
    return f"{v:.2f}"


def _avg(values: list[float | None]) -> float | None:
    xs = [v for v in values if v is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def _trend(values: list[float | None]) -> float | None:
    """Latest minus oldest (values arrive newest first)."""
    xs = [v for v in values if v is not None]
    return round(xs[0] - xs[-1], 4) if len(xs) >= 2 else None


def _cagr(values: list[float | None]) -> float | None:
    """Annualized growth from oldest to latest (ttm rows are quarter-spaced)."""
    xs = [v for v in values if v is not None]
    if len(xs) < 2 or xs[-1] is None or xs[-1] <= 0 or xs[0] <= 0:
        return None
    years = (len(xs) - 1) / 4  # quarter-spaced ttm periods
    if years <= 0:
        return None
    return round((xs[0] / xs[-1]) ** (1 / years) - 1, 4)
