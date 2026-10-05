"""Yahoo Finance (via yfinance): analyst consensus, insider transactions, and
the fallback source of earnings surprises.

Yahoo is scraped, unversioned, and rate limits hard, so it is kept off the
critical path: fundamentals come from SEC, prices/splits/surprises from
Alpaca. What remains retries on rate limits, then fails loud (FreeDataError)
rather than returning a quiet empty, and responses are memoized per ticker.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from hedge_fund.data.free._common import MISS, FreeDataError, TTLMemo, dash_symbol
from hedge_fund.data.models import AnalystConsensus, InsiderTrade

logger = logging.getLogger(__name__)

_MEMO = TTLMemo(6 * 3600)
_RATE_LIMIT_DELAYS = (5, 20, 45)
_FORM4_DEADLINE_DAYS = 2  # Yahoo drops the filing date; Form 4 is due within 2 business days


@dataclass(frozen=True)
class EarningsEvent:
    announced: datetime
    estimate: float | None
    reported: float

    @property
    def surprise(self) -> str | None:
        """BEAT / MISS / MEET at cent precision, the way EPS is reported."""
        if self.estimate is None:
            return None
        reported, estimate = round(self.reported, 2), round(self.estimate, 2)
        if reported > estimate:
            return "BEAT"
        if reported < estimate:
            return "MISS"
        return "MEET"


def _default_ticker(symbol: str):
    import yfinance  # heavy import, paid only when Yahoo is actually used

    return yfinance.Ticker(symbol)


class YahooClient:
    def __init__(self, ticker_factory: Callable[[str], Any] | None = None) -> None:
        self._ticker = ticker_factory or _default_ticker

    def info(self, ticker: str) -> dict:
        """Yahoo's quote summary; {} when Yahoo does not know the symbol."""
        info = self._call(ticker, "info", lambda t: t.info)
        return info if isinstance(info, dict) and info.get("quoteType") else {}

    def earnings_events(self, ticker: str, limit: int) -> list[EarningsEvent]:
        """Reported quarters, newest first (scheduled future dates excluded)."""
        df = self._call(ticker, f"earnings:{limit}",
                        lambda t: t.get_earnings_dates(limit=limit))
        if df is None or len(df) == 0:
            return []
        events = []
        for ts, row in df.iterrows():
            reported = _num(row.get("Reported EPS"))
            if reported is None:
                continue
            events.append(EarningsEvent(
                announced=ts.to_pydatetime(),
                estimate=_num(row.get("EPS Estimate")),
                reported=reported,
            ))
        return sorted(events, key=lambda e: e.announced, reverse=True)

    def analyst_consensus(self, ticker: str) -> AnalystConsensus | None:
        info = self.info(ticker)
        if not info:
            return None
        count = info.get("numberOfAnalystOpinions")
        return AnalystConsensus(
            ticker=ticker,
            fetched_on=date.today().isoformat(),
            recommendation_mean=_num(info.get("recommendationMean")),
            recommendation_key=info.get("recommendationKey"),
            analyst_count=int(count) if count else None,
            target_mean_price=_num(info.get("targetMeanPrice")),
            target_high_price=_num(info.get("targetHighPrice")),
            target_low_price=_num(info.get("targetLowPrice")),
            current_price=_num(info.get("currentPrice") or info.get("regularMarketPrice")),
        )

    def insider_trades(self, ticker: str, end_date: str, start_date: str | None,
                       limit: int) -> list[InsiderTrade]:
        """Recent insider transactions (Yahoo keeps roughly the last year).

        filing_date is estimated as transaction date + the Form 4 deadline,
        so a backtest never sees a trade before it could have been public.
        """
        df = self._call(ticker, "insiders", lambda t: t.insider_transactions)
        if df is None or len(df) == 0:
            return []
        out: list[InsiderTrade] = []
        for _, row in df.iterrows():
            traded = _to_date(row.get("Start Date"))
            if traded is None:
                continue
            filed = (traded + timedelta(days=_FORM4_DEADLINE_DAYS)).isoformat()
            if filed > end_date[:10] or (start_date and filed < start_date[:10]):
                continue
            position = str(row.get("Position") or "")
            out.append(InsiderTrade(
                ticker=ticker,
                name=str(row.get("Insider") or ""),
                filing_date=filed,
                is_board_director="director" in position.lower(),
                title=position or None,
                transaction_date=traded.isoformat(),
                transaction_type=str(row.get("Text") or row.get("Transaction") or "") or None,
                transaction_shares=_num(row.get("Shares")),
                transaction_value=_num(row.get("Value")),
            ))
        out.sort(key=lambda t: t.filing_date, reverse=True)
        return out[:limit]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _call(self, ticker: str, what: str, fetch: Callable[[Any], Any]):
        symbol = dash_symbol(ticker)
        key = (symbol, what)
        hit = _MEMO.get(key)
        if hit is not MISS:
            return hit
        for delay in (*_RATE_LIMIT_DELAYS, None):
            try:
                value = fetch(self._ticker(symbol))
                break
            except Exception as exc:  # yfinance raises a zoo of types; all are infrastructure
                limited = any(s in str(exc).lower() for s in ("too many requests", "rate limit", "429"))
                if limited and delay is not None:
                    logger.info("Yahoo rate limited on %s %s, retrying in %ds", what, symbol, delay)
                    time.sleep(delay)
                    continue
                raise FreeDataError(f"Yahoo {what} for {symbol} failed: {exc}") from exc
        _MEMO.put(key, value)
        return value


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _to_date(v) -> date | None:
    if v is None:
        return None
    if hasattr(v, "date") and callable(v.date):
        try:
            return v.date()
        except (TypeError, ValueError):
            return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None
