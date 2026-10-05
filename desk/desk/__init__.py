"""The desk: a Super Manager over TradingAgents and ai-hedge-fund.

    engines.py   run both desks for a symbol (subprocesses, one venv each)
    ratings.py   the shared 5-tier scale and the "same side" agreement rule
    debate.py    when the desks disagree, their managers debate until one concedes
    memo.py      the Super Manager's memo (it reports the outcome, never decides it)
    render.py    markdown memo, JSON bundle, Obsidian note
"""


class DeskError(RuntimeError):
    """A desk run cannot continue (an engine failed, a model refused, bad output)."""
