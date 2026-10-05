"""One symbol through the desk, for every requested horizon, given the selected engines' reports.

Who votes on a horizon:
    swing         TradingAgents and ai-hedge-fund. Edge Desk never votes here: its swing call was
                  measured and not shown to pick winners, so it is evidence for the others (it is
                  in the reports block) and publishes no swing rating.
    long_term     every desk with a rating. Edge Desk votes with its own long-term rating as it
                  is; the other two restate theirs for the period.
A desk whose rating was withheld (Edge Desk on bad data) votes nowhere.

Each team's own rating on its own clock is kept as provenance (`native_views`), never compared
or debated: that was the "as they ran" horizon, retired 2026-09-24 (see desk/views.py).
"""

from __future__ import annotations

from desk.config import MAX_ROUNDS_THREE
from desk.corrections import find as find_corrections
from desk.debate import DESKS, own_block, rate_for_horizon, reports_block, run_debate, transcript
from desk.events import EventLog
from desk.horizons import action_for
from desk.views import VIEWS, stated_horizon
from desk.llm import DeskLLM
from desk.memo import outcome, write_memo
from desk.ratings import side


TEAM = {"A": "tape", "B": "value", "C": "edge"}
EDGE_TIMEFRAME = "1 year or more"


def native_views(reports_by_desk: dict[str, dict]) -> dict[str, dict]:
    """Each team's own rating and the clock it stated, exactly as the team produced them. No model
    call: TradingAgents writes its horizon into its decision, Edge Desk's long-term rating is
    defined for 1 year or more, and ai-hedge-fund never states one (None, shown as not stated)."""
    out: dict[str, dict] = {}
    for d, p in reports_by_desk.items():
        clock = (stated_horizon(p.get("reports") or {}) if d == "A"
                 else EDGE_TIMEFRAME if d == "C" else None)
        out[d] = {"rating": p.get("rating"), "timeframe": clock}
    return out


def withheld_horizon(key: str, blocked: dict, owns: bool) -> dict:
    """A horizon the data does not support (desk/eligibility.py). No restatement, no debate and
    no model-written memo: the stub says why, in the same shape every renderer already reads."""
    label = VIEWS[key]["label"]
    how = f"Withheld for the whole desk: {blocked['reason']}"
    action = ("Withheld: keep current exposure unchanged until the filing reaches the data feed" if owns
              else "Withheld: no new position on this horizon until the filing reaches the data feed")
    memo = {"headline": f"{label}: no rating, the data is a quarter stale",
            "summary": (f"{how}. Every team still ran and its report is kept below, but no combined "
                        f"{key.replace('_', '-')} rating is published on superseded fundamentals. The feed "
                        "usually catches up within a few weeks; run the desk again then."),
            "action_note": "", "valuation_view": "", "key_reasons": [], "key_risks": [],
            "levels": {}, "what_to_watch": ["SEC's financial-data feed picking up the latest filing"],
            "disagreement_crux": "", "super_manager_view": ""}
    return {"span": VIEWS[key]["span"], "restated": {}, "voters": [], "debate": None, "corrections": [],
            "outcome": {"status": "withheld", "rating": None, "conviction": "Withheld", "how": how,
                        "withheld": blocked},
            "action": action, "memo": memo}


def analyze(llm: DeskLLM, ticker: str, as_of: str, reports_by_desk: dict[str, dict],
            horizons: list[str], owns: bool, max_rounds: int, say=print,
            events: EventLog | None = None, quality: dict | None = None) -> dict:
    unknown = [k for k in horizons if k not in VIEWS]
    if unknown:
        raise ValueError(f"Unknown horizon {unknown}; the desk rates {', '.join(VIEWS)}")
    reports = reports_block(ticker, as_of, reports_by_desk)       # the debate, fact check and memo see all
    own_blocks = {d: own_block(ticker, as_of, reports_by_desk, d) for d in reports_by_desk}
    engine = {d: p.get("rating") for d, p in reports_by_desk.items()}
    rated = [d for d in engine if engine[d]]
    events = events or EventLog(None)
    results: dict[str, dict] = {}
    for key in horizons:
        say(f"  [{VIEWS[key]['label']}]")
        events.emit("horizon_started", horizon=key, text=VIEWS[key]["label"])
        blocked = ((quality or {}).get("withheld") or {}).get(key)
        if blocked:
            results[key] = withheld_horizon(key, blocked, owns)
            say(f"    Withheld for the whole desk: {blocked['reason']}.")
            events.emit("horizon_outcome", horizon=key, rating=None, status="withheld",
                        conviction="Withheld", action=results[key]["action"],
                        text=results[key]["outcome"]["how"])
            continue
        restated: dict[str, dict] = {}
        note = None
        voters = [d for d in rated if not (d == "C" and key == "swing")]
        for d in voters:
            if d == "C":
                edge = reports_by_desk["C"]
                exp = edge.get("expected_annual_return")
                if edge.get("rating_basis") == "business case" and isinstance(exp, (int, float)):
                    why = (f"Edge Desk's long-term business case: expected {exp * 100:+.1f}% a year "
                           f"against a 10% hurdle, from owner earnings, a reverse DCF and three "
                           f"five-year scenarios (its factor screen reads "
                           f"{edge.get('screen_rating') or 'n/a'})")
                else:
                    why = ("Edge Desk's factor screen, computed by its formula"
                           + (f" (score {edge['score']:.1f} of 100)"
                              if isinstance(edge.get("score"), (int, float)) else ""))
                restated[d] = {"rating": engine[d], "own": True, "timeframe": EDGE_TIMEFRAME,
                               "rationale": why}
            else:
                restated[d] = {**rate_for_horizon(llm, own_blocks[d], d, key, engine[d]), "own": False}
        if "C" in rated and key == "swing":
            sig = (reports_by_desk["C"].get("swing") or {})
            note = (f"Edge Desk does not vote on the swing horizon: its swing call "
                    f"({sig.get('signal') or 'none'}) gives an entry, an invalidation and a size, "
                    f"but was measured and not shown to pick winners, so it is evidence for the others")
        for d in restated:
            events.emit("horizon_rating", horizon=key, engine=TEAM[d], desk=DESKS[d],
                        rating=restated[d]["rating"], timeframe=restated[d].get("timeframe"),
                        was=None if restated[d]["own"] else engine[d],
                        voting=d in voters and bool(restated[d]["rating"]),
                        text=restated[d]["rationale"])
        voters = [d for d in voters if restated[d]["rating"]]          # No view votes nowhere
        start = {d: restated[d]["rating"] for d in voters}
        say("    Ratings: " + ", ".join(f"{DESKS[d]} {r['rating'] or 'no view'}" for d, r in restated.items()))

        debate, corrections = None, []
        if len(start) < 2:
            events.emit("debate_skipped", horizon=key, text="Only one desk rates this horizon")
            result = outcome(start, None) if start else {"status": "no_view", "rating": None,
                                                         "conviction": "No view", "how": "No desk rated this horizon"}
        elif len({side(r) for r in start.values()}) == 1:
            say("    Same side: no debate needed.")
            events.emit("debate_skipped", horizon=key, text="Same side: no debate needed")
            result = outcome(start, None)
        else:
            rounds = min(max_rounds, MAX_ROUNDS_THREE) if len(start) >= 3 else max_rounds
            say(f"    Different sides: debate (up to {rounds} rounds).")
            events.emit("debate_started", horizon=key, text=f"Different sides: up to {rounds} rounds")
            debate = run_debate(llm, reports, key, start, rounds, say,
                                on_turn=lambda turn: events.emit(
                                    "debate_turn", horizon=key, engine=TEAM[turn.desk],
                                    desk=DESKS[turn.desk], decision=turn.decision,
                                    rating=turn.rating, round=turn.round, text=turn.argument,
                                    changed=turn.what_changed_my_mind))
            result = outcome(start, debate)
            events.emit("corrections_started", horizon=key, text="Checking which statements did not survive the debate")
            corrections = find_corrections(llm, reports, reports_by_desk, VIEWS[key]["label"],
                                           debate.to_dict(), transcript(debate.turns))
            events.emit("corrections_found", horizon=key, count=len(corrections),
                        text=f"{len(corrections)} statement(s) marked in the teams' reports")
        if note:
            result = {**result, "note": note}
        action = action_for(result["rating"], owns)
        say(f"    Outcome: {result['how']}. Action: {action}.")
        events.emit("horizon_outcome", horizon=key, rating=result["rating"], status=result["status"],
                    conviction=result["conviction"], action=action, text=result["how"])
        events.emit("memo_started", horizon=key, text=f"Writing the {VIEWS[key]['label']} memo")
        memo = write_memo(llm, reports, key, restated, debate, result, owns, action)
        events.emit("memo_written", horizon=key, rating=result["rating"],
                    conviction=result["conviction"], action=action, text=memo["headline"])
        # `span` records the question as it was asked, so a later change to the window does not
        # relabel this run.
        results[key] = {"span": VIEWS[key]["span"], "restated": restated, "voters": voters,
                        "debate": debate.to_dict() if debate else None, "corrections": corrections,
                        "outcome": result, "action": action, "memo": memo}
    return results
