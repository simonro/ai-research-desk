"""Data models for the free provider stack.

Ported from ai-hedge-fund's `hedge_fund/data/models.py` (the field names match
the shapes its EDGAR/Alpaca/Yahoo providers already build) so the providers
port across unchanged. Only the models this engine actually uses are kept.
"""

from __future__ import annotations

from pydantic import BaseModel


_IGNORE = {"extra": "ignore"}
# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------

class Price(BaseModel):
    """Single OHLCV bar from /prices."""

    model_config = _IGNORE

    open: float
    close: float
    high: float
    low: float
    volume: int
    time: str


# ---------------------------------------------------------------------------
# Financial Metrics
# ---------------------------------------------------------------------------

class FinancialMetrics(BaseModel):
    """Financial ratios and per-share metrics from /financial-metrics.

    Only ticker, report_period, and period are guaranteed non-null.
    All ratio/metric fields are nullable (NaN/Inf sanitised to null).
    """

    model_config = _IGNORE

    ticker: str
    report_period: str
    period: str
    currency: str | None = None

    # Point-in-time filing metadata (Eastern Time) - when this data became
    # public. Null on rows without a dated SEC filing (deep-history archive).
    filing_date: str | None = None
    filing_datetime: str | None = None

    # The unadjusted close the valuation fields below were priced at, and the
    # product of share splits between that day and the as-of date. Together they
    # let a current valuation be restated without touching the filing-date row.
    price_at_filing: float | None = None
    split_factor_to_as_of: float | None = None

    # Valuation
    market_cap: float | None = None
    enterprise_value: float | None = None
    price_to_earnings_ratio: float | None = None
    price_to_book_ratio: float | None = None
    price_to_sales_ratio: float | None = None
    enterprise_value_to_ebitda_ratio: float | None = None
    enterprise_value_to_revenue_ratio: float | None = None
    free_cash_flow_yield: float | None = None
    peg_ratio: float | None = None

    # Profitability
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    return_on_equity: float | None = None
    return_on_assets: float | None = None
    return_on_invested_capital: float | None = None

    # Efficiency
    asset_turnover: float | None = None
    inventory_turnover: float | None = None
    receivables_turnover: float | None = None
    days_sales_outstanding: float | None = None
    operating_cycle: float | None = None
    working_capital_turnover: float | None = None

    # Liquidity
    current_ratio: float | None = None
    quick_ratio: float | None = None
    cash_ratio: float | None = None
    operating_cash_flow_ratio: float | None = None

    # Leverage
    debt_to_equity: float | None = None
    debt_to_assets: float | None = None
    interest_coverage: float | None = None

    # Growth
    revenue_growth: float | None = None
    earnings_growth: float | None = None
    book_value_growth: float | None = None
    earnings_per_share_growth: float | None = None
    free_cash_flow_growth: float | None = None
    operating_income_growth: float | None = None
    ebitda_growth: float | None = None

    # Per-share
    payout_ratio: float | None = None
    earnings_per_share: float | None = None
    book_value_per_share: float | None = None
    free_cash_flow_per_share: float | None = None

    # The trailing-twelve-month figures themselves, not only their ratios. The
    # business analysis (owner earnings, reinvestment, dilution, scenarios) is
    # arithmetic on these, and a ratio cannot be un-divided.
    revenue: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    pretax_income: float | None = None
    income_tax: float | None = None
    operating_cash_flow: float | None = None
    capex: float | None = None
    free_cash_flow: float | None = None
    stock_compensation: float | None = None
    buybacks: float | None = None
    dividends_paid: float | None = None
    total_debt: float | None = None
    cash: float | None = None
    equity: float | None = None
    assets: float | None = None
    ebitda: float | None = None
    shares: float | None = None          # on the as-of share basis, so rows compare
    diluted_shares: float | None = None  # weighted diluted, same basis; measures dilution
    debt_unresolved: bool = False        # interest expense but no readable debt figure
    cash_and_investments: float | None = None   # cash plus marketable securities, for net debt


# ---------------------------------------------------------------------------
# Insider Trades
# ---------------------------------------------------------------------------

class InsiderTrade(BaseModel):
    """Single insider transaction from /insider-trades."""

    model_config = _IGNORE

    ticker: str
    name: str
    filing_date: str
    is_board_director: bool = False
    issuer: str | None = None
    title: str | None = None
    transaction_date: str | None = None
    transaction_type: str | None = None
    transaction_shares: float | None = None
    transaction_price_per_share: float | None = None
    transaction_value: float | None = None
    shares_owned_before_transaction: float | None = None
    shares_owned_after_transaction: float | None = None
    security_title: str | None = None


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------

class CompanyNews(BaseModel):
    """Single news article from /news."""

    model_config = _IGNORE

    ticker: str
    title: str
    source: str
    date: str | None = None
    url: str | None = None


# ---------------------------------------------------------------------------
# Company Facts
# ---------------------------------------------------------------------------
# Analyst Consensus
# ---------------------------------------------------------------------------

class AnalystConsensus(BaseModel):
    """Sell-side consensus as of the moment it was fetched.

    Current-only: no provider in the free stack keeps history, so this must
    never feed a backtest date (AnalystConsensusModel enforces that).
    """

    model_config = _IGNORE

    ticker: str
    fetched_on: str
    recommendation_mean: float | None = None  # 1 = strong buy ... 5 = strong sell
    recommendation_key: str | None = None
    analyst_count: int | None = None
    target_mean_price: float | None = None
    target_high_price: float | None = None
    target_low_price: float | None = None
    current_price: float | None = None
