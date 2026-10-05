"""Run TradingAgents on one ticker and write its reports as JSON.

Executed by the desk with TradingAgents' own interpreter:
    <TradingAgents>\\.venv\\Scripts\\python.exe ta_runner.py TICKER YYYY-MM-DD OUT.json

The graph is streamed rather than invoked so each agent reports the moment it finishes:
the desk's event log gets an entry per analyst, per debater and per risk take, which is what
the dashboard's live view shows. The merged final state is identical to what invoke() returns.
"""

import json
import re
import sys
import time
from pathlib import Path

DESK_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(DESK_DIR))

from desk import usage_meter  # noqa: E402
from desk.events import EventLog  # noqa: E402
from desk.gist import gist  # noqa: E402

usage_meter.install()

from dotenv import load_dotenv  # noqa: E402

TA_DIR = DESK_DIR.parent / "TradingAgents"
sys.path.insert(0, str(TA_DIR))     # the checkout wins over any older copy installed in the venv
load_dotenv(TA_DIR / ".env", override=False)

from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: E402
import tradingagents.graph.trading_graph as _ta_graph  # noqa: E402

from desk import maxplan  # noqa: E402

if maxplan.enabled():
    # TradingAgents builds both of its models (deep: Research Manager + Portfolio Manager,
    # quick: everyone else) through this one factory. Swapping it puts every agent on the Max
    # plan with TradingAgents' own code untouched; the model ids and effort still come from
    # TradingAgents/.env. DESK_LLM=api restores the metered client.
    from desk.max_chat import MaxClient  # noqa: E402
    _ta_graph.create_llm_client = lambda provider, model, base_url=None, **kw: MaxClient(model, base_url, **kw)

REPORT_KEYS = ("final_trade_decision", "investment_plan", "trader_investment_plan",
               "market_report", "fundamentals_report", "news_report", "sentiment_report")
# The debate drafts live inside two sub-states; the desk keeps them so the whole round is readable.
DEBATE_KEYS = {"bull_report": ("investment_debate_state", "bull_history"),
               "bear_report": ("investment_debate_state", "bear_history"),
               "risk_aggressive": ("risk_debate_state", "aggressive_history"),
               "risk_conservative": ("risk_debate_state", "conservative_history"),
               "risk_neutral": ("risk_debate_state", "neutral_history")}


def debate_reports(state: dict) -> dict:
    out = {}
    for name, (parent, field) in DEBATE_KEYS.items():
        blob = (state.get(parent) or {})
        out[name] = (blob.get(field) if isinstance(blob, dict) else "") or ""
    return out

# Who owns each piece of graph state, in the order the graph produces them.
AGENTS = [
    ("market_report", "Chartzilla", "Trend, moving averages, MACD, RSI"),
    ("sentiment_report", "Vibe Checker", "StockTwits and Reddit crowd"),
    ("news_report", "Scoop", "Company news, rates, macro"),
    ("fundamentals_report", "Abacus Abby", "Income statement, balance sheet, cash flow"),
    ("investment_plan", "Referee Rex", "Judges bull vs bear"),
    ("trader_investment_plan", "Fast Eddie", "Turns the plan into an action and a stop"),
    ("final_trade_decision", "Captain Candlestick", "The desk's final rating"),
]
DEBATERS = [("investment_debate_state", "bull_history", "Rally Randy", "Builds the bull case"),
            ("investment_debate_state", "bear_history", "Grizz", "Builds the bear case"),
            ("risk_debate_state", "current_aggressive_response", "Yolo Yuki", "Aggressive risk take"),
            ("risk_debate_state", "current_conservative_response", "Safety Sal", "Conservative risk take"),
            ("risk_debate_state", "current_neutral_response", "Middle Mel", "Neutral risk take")]
_HEAD = re.compile(r"^\s*(#{1,6}|\*\*[^*]+\*\*:?)\s*", re.M)


def stream(graph: TradingAgentsGraph, ticker: str, as_of: str, events: EventLog) -> dict:
    """Run the graph chunk by chunk, emitting an event per agent as its output appears."""
    for _, name, role in AGENTS:
        events.emit("agent_queued", engine="tape", who=name, role=role)
    for _, _, name, role in DEBATERS:
        events.emit("agent_queued", engine="tape", who=name, role=role)

    graph.ticker = ticker            # propagate() sets this; the memory log reads it back
    init_state = graph.create_run_state(ticker, as_of)
    args = graph.propagator.get_graph_args()
    thread_id = graph.begin_checkpoint(ticker, as_of)
    if thread_id is not None:
        args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = thread_id

    final: dict = {}
    seen: set[str] = set()
    try:
        for chunk in graph.graph.stream(graph.checkpoint_input(init_state), **args):
            final.update(chunk)
            for key, name, role in AGENTS:
                if key not in seen and (chunk.get(key) or "").strip():
                    seen.add(key)
                    events.emit("agent_done", engine="tape", who=name, role=role,
                                text=gist(chunk[key]), full=chunk[key], step=key)
            for state_key, field, name, role in DEBATERS:
                blob = (chunk.get(state_key) or {}).get(field) if isinstance(chunk.get(state_key), dict) else None
                if name not in seen and (blob or "").strip():
                    seen.add(name)
                    events.emit("agent_done", engine="tape", who=name, role=role,
                                text=gist(blob), full=blob, step=field)
    finally:
        graph.end_checkpoint()

    graph.curr_state = final
    graph._log_state(as_of, final)
    graph.record_decision(ticker, as_of, final)
    graph.clear_checkpoint_on_success(ticker, as_of)
    return final


def main() -> None:
    ticker, as_of, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    config = DEFAULT_CONFIG.copy()
    events = EventLog.from_env()
    started = time.time()
    graph = TradingAgentsGraph(debug=False, config=config)
    try:
        state = stream(graph, ticker, as_of, events)
    except Exception as exc:                     # streaming is for the show; the report still matters
        if maxplan.from_a_model_call(exc):
            # A model call failed, not the streaming. propagate() would redo every agent already
            # finished, spending the allowance again, and a usage limit would only fail again.
            raise
        events.emit("engine_note", engine="tape",
                    text=f"Live streaming unavailable ({exc.__class__.__name__}), running the graph in one go")
        print(f"streaming failed ({exc!r}); falling back to propagate()", flush=True)
        state, _ = graph.propagate(ticker, as_of)
    rating = graph.process_signal(state["final_trade_decision"])
    payload = {
        "engine": "tradingagents",
        "ticker": ticker,
        "date": as_of,
        "rating": rating,
        "models": {"deep": config["deep_think_llm"], "quick": config["quick_think_llm"]},
        "reports": {**{key: state.get(key) or "" for key in REPORT_KEYS}, **debate_reports(state)},
        "seconds": round(time.time() - started),
        "usage": maxplan.summary() if maxplan.enabled() else usage_meter.summary(),
        "reused": False,
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
