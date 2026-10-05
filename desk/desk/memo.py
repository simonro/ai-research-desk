"""The Super Manager's memo, one per horizon. It reports the outcome; it never picks a winner.

Set in code from what the managers did, never by the model:
    rating + agreement    agreed outright -> "Agreed before debate" (the more conservative same-side rating)
                          agreed after a debate -> "Agreed after debate" (the rating conceded to)
                          no agreement -> Gridlock or Split (no rating; memo explains the crux)
The agreement label says what happened, not how likely the rating is to be right: nothing has
measured that, and the teams share models and some data, so their agreement is not independent
confirmation. The field is still called `conviction` in saved bundles; before 2026-09-24 it said
High and Medium, which read as a measured confidence.
    action                the rating translated for the investor's position (desk/horizons.py)
The model writes the prose, the action note, and turns computed data into price levels.
The investor's position is shown only here, after the analysis is done.
"""

from __future__ import annotations

from desk.debate import DESKS, DebateResult, transcript
from desk.views import VIEWS
from desk.config import CALLS
from desk.llm import DeskLLM, cached_text, text
from desk.ratings import most_conservative

MEMO_RULES = """ROLE: you are the Super Manager. For the horizon below, the desks' managers either
agreed, reached agreement through a debate, or could not agree (a gridlock is two against one
with nobody moving; a split is no majority at all). You write the memo the
investor reads before acting.

You do not change the outcome, the rating, or the action. When the managers agreed, present
their consensus and the reasons that carried it. When they did not, explain the crux of the
disagreement and add your own reasoning about what would resolve it, without declaring a winner.
The action is fixed by the rating and the investor's position; explain it in action_note.

Valuation: say plainly whether the stock trades at a discount or a premium to the fair value
methods in the shared computed data, and whether the discount looks deserved.

Price levels: use only the shared computed data (valuation panel, price anchors) and levels
explicitly stated in the desk reports. Every level gives a price (or a tight range) and a
one-line reason tied to that data. Keep it simple: a guide, not a trading system.

Agreement between the desks is not evidence the rating is right: nothing has measured how often
agreed ratings work, and the desks share models and some data. Never call the outcome high, strong
or firm conviction, and never present the desks as independent confirmations of each other.

Style: plain and direct. Summary at most 90 words; action_note at most 50 words; 3 to 5 items
per list, one sentence each."""

_LEVEL = {
    "type": "object",
    "properties": {"price": {"type": "string"}, "reason": {"type": "string"}},
    "required": ["price", "reason"],
    "additionalProperties": False,
}

MEMO_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "action_note": {"type": "string"},
        "valuation_view": {"type": "string"},
        "key_reasons": {"type": "array", "items": {"type": "string"}},
        "key_risks": {"type": "array", "items": {"type": "string"}},
        "levels": {
            "type": "object",
            "properties": {"entry_zone": _LEVEL, "stop": _LEVEL, "first_target": _LEVEL, "trim": _LEVEL},
            "required": ["entry_zone", "stop", "first_target", "trim"],
            "additionalProperties": False,
        },
        "what_to_watch": {"type": "array", "items": {"type": "string"}},
        "disagreement_crux": {"type": "string"},
        "super_manager_view": {"type": "string"},
    },
    "required": ["headline", "summary", "action_note", "valuation_view", "key_reasons", "key_risks",
                 "levels", "what_to_watch", "disagreement_crux", "super_manager_view"],
    "additionalProperties": False,
}


def outcome(start: dict[str, str], debate: DebateResult | None) -> dict:
    """What the managers' ratings add up to. `start` maps desk code to its rating for the horizon.

        agreed                same side at first read        Agreed before debate
        agreed_after_debate   same side after a debate       Agreed after debate
        gridlock              two against one, nobody moved  no rating
        no_agreement          no majority, nobody moved      no rating
        single                only one desk had a view       that desk's rating, flagged
    """
    names = [DESKS[d] for d in start]
    if len(start) == 1:
        (d, rating), = start.items()
        return {"status": "single", "rating": rating, "conviction": "Single view",
                "how": f"Only {DESKS[d]} rates this horizon, so there is nothing to compare it with"}
    if debate is None:
        ratings = list(start.values())
        rating = most_conservative(ratings)
        how = (f"{_all(names)} agreed" if len(set(ratings)) == 1 else
               f"{_all(names)} are on the same side ({', '.join(ratings)}); the most "
               f"conservative rating is used")
        return {"status": "agreed", "rating": rating, "conviction": "Agreed before debate", "how": how}
    turns = len(debate.turns)
    if debate.outcome == "agreed":
        losers = [DESKS[d] for d in (debate.conceded or [debate.conceded_by])]
        keepers = [DESKS[d] for d in start if DESKS[d] not in losers]
        return {"status": "agreed_after_debate", "rating": debate.agreed_rating, "conviction": "Agreed after debate",
                "how": f"The {' and '.join(losers)} manager{'s' if len(losers) > 1 else ''} conceded to "
                       f"{' and '.join(keepers) or 'the other side'} after {turns} debate turn"
                       f"{'s' if turns != 1 else ''}"}
    stands = ", ".join(f"{DESKS[d]} {r}" for d, r in debate.ratings.items())
    if debate.outcome == "gridlock":
        return {"status": "gridlock", "rating": None, "conviction": "Gridlock",
                "how": f"Gridlock after {turns} debate turns, two against one: {stands}"}
    return {"status": "no_agreement", "rating": None, "conviction": "Split",
            "how": f"No agreement after {turns} debate turns: {stands}"}


def _all(names: list[str]) -> str:
    return "Both desks" if len(names) == 2 else "All three desks"


def write_memo(llm: DeskLLM, reports: str, horizon: str, restated: dict[str, dict],
               debate: DebateResult | None, result: dict, owns: bool, action: str) -> dict:
    h = VIEWS[horizon]
    lines = [
        MEMO_RULES,
        "",
        f"HORIZON: {h['label']}. {h['weighting']}",
        f"LEVELS FOR THIS HORIZON: {h['levels']}",
        "",
        "INVESTOR POSITION: " + ("already owns this stock." if owns else
                                 "does not own this stock and is considering a new position."),
        f"ACTION (fixed): {action}",
        "",
        "HORIZON RATINGS (each manager restated its desk's rating for this horizon):",
        *[f"- {DESKS[d]}: {r['rating']}. {r['rationale']}" for d, r in restated.items()],
        "",
        "OUTCOME:",
        f"- {result['how']}.",
        (f"- Final rating: {result['rating']} ({result['conviction'].lower()})." if result["rating"]
         else "- No final rating: the managers did not agree. Write disagreement_crux and "
              "super_manager_view; do not pick a side."),
        "",
        "DEBATE TRANSCRIPT:",
        transcript(debate.turns) if debate else "(no debate: the managers agreed for this horizon)",
        "",
        ("If there was a debate, write disagreement_crux as ONE plain sentence of at most 25 words "
         "naming the question the desks disagreed on, phrased as a question, without saying "
         "who won. Otherwise leave it empty. " if debate else
         "Leave disagreement_crux empty. ")
        + "Leave super_manager_view as an empty string unless the managers did not agree. "
          "Respond with JSON only.",
    ]
    return llm.json([cached_text(reports), text("\n".join(lines))], MEMO_SCHEMA, max_tokens=12000, model=CALLS["memo"][0], effort=CALLS["memo"][1])
