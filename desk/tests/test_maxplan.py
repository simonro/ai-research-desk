"""The Max-plan layer (audit B05-B07): what `claude -p` hands back is checked before anyone uses it.

A fake CLI returns crafted replies, so no model is called:
    cd desk && .venv/Scripts/python.exe -m pytest tests -q
"""

import json
import subprocess
from pathlib import Path

import pytest

from desk import maxplan
from desk.debate import TURN_SCHEMA

RATING = {"type": "object", "properties": {"rating": {"type": "string", "enum": ["Buy", "Hold", "Sell"]}},
          "required": ["rating"], "additionalProperties": False}


class FakeCLI:
    """Stands in for claude.exe: returns the scripted replies in order and counts the calls."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    def __call__(self, cmd, **kw):
        self.calls += 1
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        out = reply.get("stdout", json.dumps(reply.get("payload", {})))
        return subprocess.CompletedProcess(cmd, reply.get("code", 0), out.encode(), reply.get("stderr", "").encode())


def answer(result="", structured=None, is_error=False):
    payload = {"type": "result", "is_error": is_error, "result": result, "usage": {}, "total_cost_usd": 0}
    if structured is not None:
        payload["structured_output"] = structured
    return {"payload": payload}


@pytest.fixture
def cli(monkeypatch):
    def install(*replies):
        fake = FakeCLI(*replies)
        monkeypatch.setattr(maxplan, "claude_exe", lambda: ["claude"])
        monkeypatch.setattr(maxplan.subprocess, "run", fake)
        monkeypatch.setattr(maxplan.time, "sleep", lambda s: None)
        return fake
    return install


# ---- B06: every structured answer is checked against its schema -------------------------------

def test_a_text_fallback_that_breaks_the_schema_is_retried_then_refused(cli):
    """Codex reproduced this one: the fallback that reads JSON out of the text accepted Potato."""
    fake = cli(answer(result='{"rating": "Potato"}'))
    with pytest.raises(maxplan.MaxCallError, match="Potato"):
        maxplan.ask("rate it", "sys", schema=RATING)
    assert fake.calls == 2                                  # one retry, then a clear failure


def test_structured_output_is_checked_too_not_just_the_fallback(cli):
    fake = cli(answer(structured={"rating": "Buy", "extra": 1}), answer(structured={"rating": "Buy"}))
    reply = maxplan.ask("rate it", "sys", schema=RATING)
    assert reply.data == {"rating": "Buy"} and fake.calls == 2


def test_a_valid_answer_passes_through_untouched(cli):
    fake = cli(answer(result='{"rating": "Hold"}'))
    assert maxplan.ask("rate it", "sys", schema=RATING).data == {"rating": "Hold"} and fake.calls == 1


# ---- B07: only a real limit notice stops the run ------------------------------------------------

def test_an_answer_that_mentions_a_rate_limit_is_an_answer(cli):
    """The old detector stopped the run on any short reply containing "rate limit"."""
    fake = cli(answer(result="Reddit hit a rate limit, so social sentiment is thin this week. Neutral."))
    assert "Reddit" in maxplan.ask("sentiment?", "sys").text and fake.calls == 1


@pytest.mark.parametrize("notice", [
    "You've hit your session limit · resets 3pm",
    "Claude AI usage limit reached|1759512000",
    "5-hour limit reached ∙ resets 3pm",
    "You've reached your weekly limit. Upgrade to increase your usage.",
])
def test_a_real_limit_notice_stops_at_once_without_a_retry(cli, notice):
    fake = cli(answer(result=notice))
    with pytest.raises(maxplan.UsageLimit):
        maxplan.ask("anything", "sys")
    assert fake.calls == 1


def test_a_long_limit_notice_in_a_failed_call_is_still_caught(cli):
    """The old detector ignored anything over 800 characters."""
    long = "API Error. " + "context " * 150 + "Claude AI usage limit reached|1759512000"
    fake = cli(answer(result=long, is_error=True))
    with pytest.raises(maxplan.UsageLimit):
        maxplan.ask("anything", "sys", schema=RATING)
    assert fake.calls == 1


def test_a_passing_throttle_is_retried_not_treated_as_exhaustion(cli):
    fake = cli(answer(result="API Error: 429 rate limit exceeded, too many requests", is_error=True),
               answer(structured={"rating": "Sell"}))
    assert maxplan.ask("rate it", "sys", schema=RATING).data == {"rating": "Sell"} and fake.calls == 2


def test_unreadable_output_and_a_nonzero_exit_fail_cleanly(cli):
    fake = cli({"stdout": "segfault", "code": 3})
    with pytest.raises(maxplan.MaxCallError):
        maxplan.ask("anything", "sys", schema=RATING)
    assert fake.calls == 2


# ---- the validator on the schemas that actually flow through ----------------------------------

def test_the_validator_on_the_desks_debate_turn_schema():
    turn = {"decision": "concede", "rating": "Hold", "holes_in_other_report": [], "new_evidence": ["x"],
            "argument": "a", "what_changed_my_mind": "b", "gives_up": []}
    assert maxplan.schema_problem(turn, TURN_SCHEMA) is None
    assert "rating" in maxplan.schema_problem({**turn, "rating": "Strong Buy"}, TURN_SCHEMA)
    assert "missing argument" in maxplan.schema_problem({k: v for k, v in turn.items() if k != "argument"}, TURN_SCHEMA)
    assert "new_evidence[0]" in maxplan.schema_problem({**turn, "new_evidence": [3]}, TURN_SCHEMA)


def test_the_validator_on_the_shapes_tradingagents_uses():
    """TradingAgents' output models use anyOf (optional fields), enum and minimum / maximum."""
    schema = {"type": "object", "required": ["action"], "properties": {
        "action": {"type": "string", "enum": ["Buy", "Hold", "Sell"]},
        "confidence": {"anyOf": [{"type": "number", "minimum": 0, "maximum": 1}, {"type": "null"}]}}}
    assert maxplan.schema_problem({"action": "Buy", "confidence": None}, schema) is None
    assert maxplan.schema_problem({"action": "Buy", "confidence": 0.7}, schema) is None
    assert maxplan.schema_problem({"action": "Buy", "confidence": 7}, schema)
    assert maxplan.schema_problem({"action": "Buy", "confidence": True}, schema)     # a bool is not a number
    assert maxplan.schema_problem({"x": 1}, {"type": "object", "unknownKeyword": 5}) is None


# ---- B05: a model failure is not a reason to run TradingAgents twice ----------------------------

def test_a_model_failure_is_recognised_through_wrapping():
    try:
        try:
            raise maxplan.UsageLimit("You've hit your session limit")
        except maxplan.UsageLimit as inner:
            raise RuntimeError("graph node failed") from inner
    except RuntimeError as outer:
        assert maxplan.from_a_model_call(outer)
    assert not maxplan.from_a_model_call(KeyError("final_trade_decision"))


def test_the_tradingagents_runner_asks_before_it_reruns_the_graph():
    """The runner imports TradingAgents, so it cannot load in this venv; the order is checked here and
    the behaviour in TradingAgents' own venv."""
    src = (Path(__file__).parents[1] / "desk" / "runners" / "ta_runner.py").read_text(encoding="utf-8")
    body = src[src.index("def main()"):]
    assert body.index("maxplan.from_a_model_call(exc)") < body.index("graph.propagate(")


# ---- Stage A (audit 2): exit codes, oneOf, and no silently skipped rules ------------------------

def test_a_success_shaped_answer_from_a_failed_process_is_refused(cli):
    """Codex's probe (R2-20): exit 1 with a valid-looking payload used to return a rating."""
    fake = cli({**answer(structured={"rating": "Buy"}), "code": 1})
    with pytest.raises(maxplan.MaxCallError, match="exit 1"):
        maxplan.ask("rate it", "sys", schema=RATING)
    assert fake.calls == 2


def test_one_of_means_exactly_one():
    """R2-12: oneOf was read as anyOf."""
    both = {"oneOf": [{"type": "number"}, {"type": "integer"}]}
    assert maxplan.schema_problem(4, both)                 # fits both shapes: not allowed
    assert maxplan.schema_problem(4.5, both) is None       # fits exactly one
    assert maxplan.schema_problem("x", both)               # fits none


def test_string_lengths_are_checked():
    assert maxplan.schema_problem("ab", {"type": "string", "minLength": 3})
    assert maxplan.schema_problem("abcd", {"type": "string", "maxLength": 3})
    assert maxplan.schema_problem("abc", {"type": "string", "minLength": 3, "maxLength": 3}) is None


def test_a_schema_with_a_rule_the_desk_cannot_check_is_refused_before_any_call(cli):
    """R2-12: a $ref (or pattern, not, ...) used to be skipped silently, weakening the check."""
    fake = cli(answer(structured={"rating": "Potato"}))
    ref = {"type": "object", "properties": {"rating": {"$ref": "#/$defs/rating"}},
           "$defs": {"rating": {"enum": ["Buy", "Sell"]}}}
    with pytest.raises(maxplan.SchemaUnsupported, match=r"\$ref"):
        maxplan.ask("rate it", "sys", schema=ref)
    assert fake.calls == 0                                 # refused before spending anything
    assert maxplan.unsupported_keywords({"type": "string", "pattern": "^[A-Z]+$"}) == ["$: pattern"]
    assert maxplan.from_a_model_call(maxplan.SchemaUnsupported("x"))   # TradingAgents will not rerun


def test_descriptions_and_defaults_are_annotations_not_rules():
    """TradingAgents' output models carry description and default; they constrain nothing."""
    schema = {"type": "object", "title": "T", "properties": {
        "action": {"type": "string", "description": "d", "default": "Hold", "enum": ["Buy", "Hold"]}}}
    assert maxplan.unsupported_keywords(schema) == []


def test_every_desk_schema_passes_the_check():
    from desk import corrections, debate, memo
    for schema in (debate.HORIZON_RATING_SCHEMA, debate.TURN_SCHEMA, memo.MEMO_SCHEMA, corrections.SCHEMA):
        assert maxplan.unsupported_keywords(schema) == []


def test_an_expired_login_stops_the_run_like_a_usage_limit():
    """AKAM 2026-10-04: every call failed with this, and the run carried on and saved."""
    assert maxplan._looks_like_limit("Failed to authenticate: OAuth session expired and could not be refreshed", failed=True)
    assert not maxplan._looks_like_limit("The analyst failed to authenticate the revenue figure in a long report. " * 10, failed=False)


def test_a_swallowed_login_failure_is_still_found():
    """ai-hedge-fund's personas abstain on any failure; the runner looks for the notice in their text."""
    reasons = ["moat is wide", "abstained: LLM call failed: claude -p failed (exit 1): Failed to "
               "authenticate: OAuth session expired and could not be refreshed"]
    assert "OAuth" in maxplan.first_limit_notice(reasons)
    assert maxplan.first_limit_notice(["moat is wide", "abstained: no data", ""]) is None



def test_the_api_path_sends_each_calls_model_and_effort():
    """Audit R2-19: DESK_LLM=api sent the default model for every call and dropped the effort."""
    from types import SimpleNamespace
    from desk.llm import DeskLLM
    sent = {}

    class Fake:
        class beta:
            class messages:
                @staticmethod
                def create(**kw):
                    sent.update(kw)
                    return SimpleNamespace(model=kw["model"], stop_reason="end_turn",
                                           usage=SimpleNamespace(input_tokens=1, output_tokens=1,
                                                                 cache_creation_input_tokens=0, cache_read_input_tokens=0),
                                           content=[SimpleNamespace(type="text", text='{"ok": true}')])

    llm = DeskLLM("claude-sonnet-5", client=Fake())
    assert llm.json([{"type": "text", "text": "q"}], {"type": "object"}, model="opus", effort="medium") == {"ok": True}
    assert sent["model"] == maxplan.MODELS["opus"] and sent["output_config"]["effort"] == "medium"
    assert llm.calls[-1]["requested"] == maxplan.MODELS["opus"]
    llm.json([{"type": "text", "text": "q"}], {"type": "object"})
    assert sent["model"] == "claude-sonnet-5" and "effort" not in sent["output_config"]
