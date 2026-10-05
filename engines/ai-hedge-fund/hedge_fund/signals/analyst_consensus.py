"""Analyst consensus alpha model: the sell side's rating and price target.

Two components, averaged:

    rating  = (2.5 - mean rating) / 1.5, clamped   1.0 strong buy -> +1, 4.0 -> -1
    upside  = (mean target / price - 1) / 30%, clamped

Centered at 2.5, not the scale's 3.0 midpoint, because the Street almost never
says sell: a raw 1-5 scale would make every stock a buy.

The data is current-only (no free source keeps consensus history), so the
model abstains on any date more than `max_staleness_days` old. That keeps it
out of backtests entirely instead of leaking today's opinion into the past.
It also abstains when the provider has no consensus (FDClient) or fails to
fetch one: unlike fundamentals, a missing Street view cannot corrupt a book,
and one flaky scrape should not sink a whole cycle.
"""

from __future__ import annotations

import logging
from datetime import date

from hedge_fund.data.protocol import DataClient
from hedge_fund.models import Signal
from hedge_fund.signals.base import QuantModel

logger = logging.getLogger(__name__)


class AnalystConsensusModel(QuantModel):
    def __init__(
        self,
        *,
        min_analysts: int = 3,
        max_staleness_days: int = 7,
        full_upside: float = 0.30,
    ) -> None:
        self._min_analysts = min_analysts
        self._max_staleness_days = max_staleness_days
        self._full_upside = full_upside

    @property
    def name(self) -> str:
        return "analyst_consensus"

    def predict(self, ticker: str, date_str: str, data_client: DataClient) -> Signal:
        age = (date.today() - date.fromisoformat(date_str[:10])).days
        if age > self._max_staleness_days:
            return self._abstain(ticker, date_str,
                                 "consensus is current-only; no point-in-time history")

        fetch = getattr(data_client, "get_analyst_consensus", None)
        if fetch is None:
            return self._abstain(ticker, date_str, "data provider has no analyst consensus")
        try:
            consensus = fetch(ticker)
        except AttributeError:
            return self._abstain(ticker, date_str, "data provider has no analyst consensus")
        except Exception as exc:
            logger.warning("analyst consensus for %s failed: %s", ticker, exc)
            return self._abstain(ticker, date_str, f"consensus fetch failed: {exc}")

        count = consensus.analyst_count if consensus else None
        if consensus is None or not count or count < self._min_analysts:
            return self._abstain(ticker, date_str, f"fewer than {self._min_analysts} analysts")

        components: dict[str, float] = {}
        parts: list[str] = [f"{count} analysts"]
        mean = consensus.recommendation_mean
        if mean is not None:
            components["rating"] = self._normalize_to_signal((2.5 - mean) / 1.5)
            parts.append(f"mean rating {mean:.2f} ({consensus.recommendation_key or '?'})")
        target, price = consensus.target_mean_price, consensus.current_price
        if target and price:
            upside = target / price - 1
            components["upside"] = self._normalize_to_signal(upside / self._full_upside)
            parts.append(f"mean target ${target:,.2f} vs ${price:,.2f} ({upside:+.1%})")
        if not components:
            return self._abstain(ticker, date_str, "no rating or price target")

        value = round(sum(components.values()) / len(components), 4)
        return Signal(
            model_name=self.name,
            ticker=ticker,
            date=date_str,
            value=value,
            reasoning="; ".join(parts),
            components={k: round(v, 4) for k, v in components.items()},
            metadata={"analyst_count": count, "fetched_on": consensus.fetched_on},
        )

    def _abstain(self, ticker: str, date_str: str, reason: str) -> Signal:
        return Signal(model_name=self.name, ticker=ticker, date=date_str, value=0.0,
                      reasoning=reason, metadata={"abstained": True})
