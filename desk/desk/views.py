"""The two holding periods the desk rates, and the timeframe a team states for its own call.

A team's own rating answers a question nobody asked out loud: TradingAgents states a horizon in
its decision ("3-6 months"), ai-hedge-fund's manager never says one. So every verdict is asked
for a stated holding period, and a team's own call is kept beside them as provenance, never
compared or debated:

    swing         each voter answers "rate it for holding 2 days to 8 weeks".
    long_term     each voter answers "rate it for holding 1 year or more".

Each result stands on its own and gets its own memo. A debate happens only where the voters
give different sides of the same question.

Retired 2026-09-24: "as_they_ran", which compared each team's own call on its own clock. It was
not a holding period anyone trades, it usually restated the long-term question, and it was the
only reason the desk parsed timeframes. Bundles saved before then still carry it; the dashboard
renders them, the desk no longer produces it.
"""

from __future__ import annotations

import re

VIEWS = {
    "swing": {
        "label": "Swing (2 days to 8 weeks)",
        "span": "2 days to 8 weeks",
        "weighting": (
            "Weigh trend and momentum, support and resistance, catalysts and news flow, sentiment, "
            "and near-term earnings timing most. Fundamentals matter mainly as catalysts or gap "
            "risk: a great business in a broken downtrend can be a swing Hold or Underweight. The "
            "shortest holds, a few days, are timing calls that none of the desks' evidence resolves: "
            "judge the rating for the 2 to 8 week end of the window, and say so rather than implying "
            "a view on the next few days."),
        "levels": ("Entry near support (20-day low, a moving average below price). Stop about 1 to "
                   "1.5 ATR beyond that support. First target at the nearest resistance (20-day high "
                   "or a moving average above price); trim at the next resistance."),
    },
    "long_term": {
        "label": "Long term (1+ years)",
        "span": "1 year or more",
        "weighting": (
            "Weigh business quality, earnings and cash-flow growth, balance sheet, competitive "
            "position, and valuation against fair value most. Price action, momentum and sentiment "
            "matter mainly for entry timing, not for the rating."),
        "levels": ("Entry zone relative to the fair value ranges (at or below the own-history mid is "
                   "attractive). The stop is a thesis-break level well below support, and name the "
                   "fundamental condition that would break the thesis. First target near the fair "
                   "value mid or the Street mean; trim near the top of the range or the Street high."),
    },
}
DEFAULT_VIEWS = ("swing", "long_term")

_TA_HORIZON = re.compile(r"\*\*Time Horizon\*\*:\s*(.+)", re.I)


def stated_horizon(ta_reports: dict) -> str | None:
    """TradingAgents writes its own holding period into the final decision when it has one."""
    match = _TA_HORIZON.search(ta_reports.get("final_trade_decision", "") or "")
    return match.group(1).strip().rstrip(".") if match else None
