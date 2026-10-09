"""The mechanical rating: one vote per distinct analyst, banded into five tiers.

    desk score = mean conviction of the distinct analysts that stated a view
    Buy >= +0.50 > Overweight >= +0.20 > Hold > -0.20 >= Underweight > -0.50 >= Sell

Distinct, because a persona staffed into three strategies is one opinion, not
three. "Stated a view" excludes abstentions and quant models that simply had
nothing to say (post-earnings drift outside its window returns 0.0 with no
call); an LLM analyst that says NEUTRAL is a real vote and stays in.

The bands are uncalibrated round numbers. The forward test is what should move
them, not intuition.
"""

from __future__ import annotations

from hedge_fund.models import Signal
from hedge_fund.pipeline.models import CycleRecord, TickerVerdict

# Same five tiers as TradingAgents, so the two desks' calls compare directly.
RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")

def desk_rating(score: float) -> str:
    if score >= 0.50:
        return "Buy"
    if score >= 0.20:
        return "Overweight"
    if score > -0.20:
        return "Hold"
    if score > -0.50:
        return "Underweight"
    return "Sell"


def has_view(signal: Signal) -> bool:
    if signal.metadata.get("abstained") is True:
        return False
    return signal.value != 0 or signal.metadata.get("signal") == "neutral"


def distinct_signals(record: CycleRecord, ticker: str) -> list[Signal]:
    """Each analyst's call on *ticker* once, in the order the report lists them. When the same
    analyst appears in several strategies, a call with a view wins over an abstention, so a
    failure in one strategy cannot hide the answer the analyst gave in another."""
    seen: dict[str, Signal] = {}
    for sr in record.strategies:
        for s in sr.signals:
            if s.ticker != ticker:
                continue
            if s.model_name not in seen or (not has_view(seen[s.model_name]) and has_view(s)):
                seen[s.model_name] = s
    return list(seen.values())


def desk_verdict(record: CycleRecord, ticker: str) -> TickerVerdict:
    signals = distinct_signals(record, ticker)
    voting = [s for s in signals if has_view(s)]
    votes = {
        "bullish": sum(1 for s in voting if s.value > 0),
        "bearish": sum(1 for s in voting if s.value < 0),
        "neutral": sum(1 for s in voting if s.value == 0),
        "no_view": len(signals) - len(voting),
    }
    if not voting:
        return TickerVerdict(ticker=ticker, votes=votes)
    score = round(sum(s.value for s in voting) / len(voting), 4)
    return TickerVerdict(ticker=ticker, desk_score=score, desk_rating=desk_rating(score), votes=votes)
