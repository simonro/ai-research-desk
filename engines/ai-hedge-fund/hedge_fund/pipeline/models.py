"""Pipeline records — the serialized truth of every cycle.

A CycleRecord captures one tick of the fund end to end: what the analysts
saw, what they said, how views became weights, what risk clamped, what was
ordered and filled, and what the book looks like after. The ledger persists
these; `fund why AAPL` will answer from them alone.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from hedge_fund.brokers.models import Fill, Order
from hedge_fund.fund.spec import FundSpec
from hedge_fund.models import Signal
from hedge_fund.risk.limits import ClampEvent


class TickerSkip(BaseModel):
    """A requested name that could not be traded this cycle, and why."""

    ticker: str
    reason: str


class StrategyRecord(BaseModel):
    """One strategy's slice of a cycle: its analysts' views and its sleeve."""

    name: str
    slice: float                        # normalized capital slice of the fund
    signals: list[Signal]               # this strategy's analysts x tradeable tickers
    convictions: dict[str, float]       # blended views, pre-scaling
    weights: dict[str, float]           # the sleeve, before netting across strategies


class TickerVerdict(BaseModel):
    """The desk's bottom line on one name: a mechanical rating from the
    analysts' calls, and the research manager's written verdict on top.

    Commentary, not mechanics: nothing here changes a weight or an order.
    """

    ticker: str
    desk_score: float | None = None      # mean call of the distinct analysts with a view
    desk_rating: str | None = None       # the 5-tier band of desk_score
    votes: dict[str, int] = Field(default_factory=dict)  # bullish/bearish/neutral/no_view
    rating: str | None = None            # the research manager's call
    confidence: float | None = None      # 0-100
    summary: str | None = None
    thesis: str | None = None
    bull_case: list[str] = Field(default_factory=list)
    bear_case: list[str] = Field(default_factory=list)
    what_would_change: list[str] = Field(default_factory=list)
    data_caveats: list[str] = Field(default_factory=list)
    headlines: list[str] = Field(default_factory=list)  # recent news the manager read
    model: str | None = None
    cached: bool = False
    error: str | None = None             # why there is no manager verdict, if not


class CycleRecord(BaseModel):
    """One tick of the fund, fully serialized — every stage's inputs and
    outputs. `model_dump_json()` round-trips; nothing about a decision
    lives anywhere else."""

    fund: str
    as_of: str
    spec: FundSpec                      # self-contained audit copy
    universe: list[str]                 # the tickers this cycle was asked to trade
    marks: dict[str, float]             # ticker -> close used for sizing and NAV
    skipped: list[TickerSkip]
    strategies: list[StrategyRecord]    # every sleeve, incl. each thesis
    target_weights: dict[str, float]    # the NETTED book, pre-risk
    clamps: list[ClampEvent]
    final_weights: dict[str, float]     # post-risk
    equity_before: float
    cash_before: float
    orders: list[Order]
    fills: list[Fill]
    positions: dict[str, int]           # signed shares after fills
    cash: float
    nav: float                          # cash + sum(shares * mark)
    # Added after the run by hedge_fund.verdict; empty on backtest cycles and
    # on receipts written before verdicts existed.
    verdicts: dict[str, TickerVerdict] = Field(default_factory=dict)
