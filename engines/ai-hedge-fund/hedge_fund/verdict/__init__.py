"""Verdicts: the desk's bottom line per ticker, added to a finished cycle.

    rating.py    mechanical Buy / Overweight / Hold / Underweight / Sell from
                 the analysts' calls (free, deterministic)
    manager.py   a research manager LLM that reads every thesis and writes the
                 verdict (one call per ticker, run-today only)

Verdicts are commentary on the cycle: they never change weights or orders,
which stay the deterministic pipeline's job ("the LLM never touches the trade").
"""

from hedge_fund.verdict.manager import MANAGER_MODEL_ENV, ResearchManager, add_verdicts, manager_model
from hedge_fund.verdict.rating import RATINGS, desk_rating, desk_verdict

__all__ = [
    "MANAGER_MODEL_ENV",
    "RATINGS",
    "ResearchManager",
    "add_verdicts",
    "desk_rating",
    "desk_verdict",
    "manager_model",
]
