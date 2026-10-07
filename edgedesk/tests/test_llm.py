"""The LLM layer, tested without calling a model.

Three things matter here and all three are checked offline: the layer never
changes a number, the child process never carries API credentials, and prose
containing an invented figure is rejected rather than published.
"""

from __future__ import annotations

import json
import os
from datetime import date

import pytest

from edgedesk import run as run_mod
from edgedesk.llm import analysis, citations, headless, prompts, schema
from tests.conftest import FakeClient

AS_OF = date(2026, 9, 15)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("edgedesk.paths.RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr("edgedesk.paths.ESTIMATES_DIR", tmp_path / "estimates")
    return tmp_path


@pytest.fixture
def run(home):
    return run_mod.analyze("TEST", AS_OF, client=FakeClient(), save=False)


# ---------------------------------------------------------------------------
# Billing: the rule that makes this free
# ---------------------------------------------------------------------------

def test_the_child_environment_carries_no_anthropic_credentials(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-survive")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "should-not-survive-either")
    env = headless.child_env()
    for key in headless.CRED_VARS:
        assert key not in env, f"{key} would reach the child and bill the metered API"
    # The parent keeps its own environment: only the child is stripped.
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-should-not-survive"


def test_the_warm_path_blanks_credentials_rather_than_only_omitting_them():
    """The SDK merges {**os.environ, **options.env}, so omission is not enough:
    a key that is merely absent from the dict is inherited anyway."""
    import inspect

    source = inspect.getsource(headless._get_client)
    assert 'k: "" for k in CRED_VARS' in source
    assert "os.environ.pop" in source


def test_the_model_is_pinned_not_inherited_from_the_users_claude_default(monkeypatch):
    """Without --model, `claude -p` uses whatever the user set as their Claude Code default, so
    changing it for everyday coding would change the model behind every report."""
    import asyncio
    import inspect

    seen = {}

    async def fake_exec(*cmd, **kw):
        seen["cmd"] = list(cmd)
        raise OSError("stop here: only the command matters")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.delenv("EDGE_DESK_MODEL", raising=False)
    asyncio.run(headless._ask_cold("hi", 5, None))
    assert seen["cmd"][seen["cmd"].index("--model") + 1] == headless.MODEL == "claude-sonnet-5"
    asyncio.run(headless._ask_cold("hi", 5, "claude-opus-5-5"))
    assert seen["cmd"][seen["cmd"].index("--model") + 1] == "claude-opus-5-5"   # a caller's choice wins
    assert "model=current_model()" in inspect.getsource(headless._get_client)  # the warm path too


def test_a_model_set_after_import_is_the_one_called(monkeypatch):
    """The desk's runner imports this module, then loads Edge's own settings file. The model
    named there must be the one on the command line, as the report records it (Codex F4)."""
    import asyncio

    seen = {}

    async def fake_exec(*cmd, **kw):
        seen["cmd"] = list(cmd)
        raise OSError("stop here: only the command matters")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setenv("EDGE_DESK_MODEL", "claude-own-file-model")
    asyncio.run(headless._ask_cold("hi", 5, None))
    assert seen["cmd"][seen["cmd"].index("--model") + 1] == "claude-own-file-model" == headless.current_model()


def test_every_cold_call_is_isolated_from_tools_settings_and_mcp(monkeypatch):
    """Audit R2-05: the cold path, which every batch call takes, ran with every tool available and
    permissions bypassed, and loaded the user's own settings, hooks and CLAUDE.md."""
    import asyncio
    import inspect

    seen = {}

    async def fake_exec(*cmd, **kw):
        seen["cmd"] = list(cmd)
        raise OSError("stop here: only the command matters")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(headless._ask_cold("hi", 5, None))
    cmd = seen["cmd"]
    assert cmd[cmd.index("--tools") + 1] == "" and cmd[cmd.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in cmd and "--no-session-persistence" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert "--permission-mode" not in cmd and "bypassPermissions" not in cmd
    warm = inspect.getsource(headless._get_client)
    assert "tools=[]" in warm and "setting_sources=[]" in warm and "mcp_servers={}" in warm
    assert "bypassPermissions" not in warm


@pytest.mark.parametrize("code, out, ok", [
    (0, b'{"type":"result","is_error":false,"result":"an answer"}', True),
    (1, b'{"type":"result","is_error":false,"result":"an answer"}', False),   # the exit code counts now
    (0, b'{"type":"result","is_error":true,"result":"API Error"}', False),
    (0, b'{"type":"result","is_error":false,"result":""}', False),
    (0, b"not json at all", False),
])
def test_an_answer_needs_a_clean_exit_no_error_flag_and_text(code, out, ok):
    assert headless.read_reply(code, out, b"")[0] is ok


def test_a_failed_process_with_a_long_limit_notice_stops_the_batch(monkeypatch):
    """Codex's probe (audit R2-11): exit 1 with a 721-character notice used to pass as a reply."""
    import asyncio

    class Proc:
        returncode = 1

        async def communicate(self, data):
            return b"Usage limit reached. " + b"x" * 700, b""

    async def fake_exec(*cmd, **kw):
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(headless.UsageLimit):
        asyncio.run(headless.ask_async("hi", warm=False))


def test_no_tools_are_offered_to_the_model():
    """A model that can fetch can find a number the run never saw."""
    import inspect

    assert "allowed_tools=[]" in inspect.getsource(headless._get_client)


# ---------------------------------------------------------------------------
# The layer never changes a number
# ---------------------------------------------------------------------------

def test_attach_refuses_to_change_the_run_hash(run, monkeypatch):
    monkeypatch.setattr(headless, "available", lambda: (False, "no CLI in tests"))
    before = json.dumps({k: v for k, v in run.items() if k != "llm"}, sort_keys=True,
                        default=str)
    analysis.attach(run)
    after = json.dumps({k: v for k, v in run.items() if k != "llm"}, sort_keys=True,
                       default=str)
    assert before == after


def test_a_missing_cli_degrades_instead_of_failing(run, monkeypatch):
    monkeypatch.setattr(headless, "available", lambda: (False, "no CLI"))
    result = analysis.analyze(run)
    assert result["ok"] is False
    assert "no CLI" in result["error"]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_json_is_found_inside_a_code_fence():
    data = schema.extract('Sure, here you go:\n```json\n{"a": 1}\n```\nHope that helps.')
    assert data == {"a": 1}


def test_json_is_found_without_a_fence():
    assert schema.extract('prelude {"a": 2} trailing') == {"a": 2}


def test_a_missing_required_field_is_rejected():
    with pytest.raises(schema.SchemaError, match="missing"):
        schema.check({"case": "x"}, prompts.CASE_SCHEMA)


def test_an_out_of_range_enum_is_rejected():
    with pytest.raises(schema.SchemaError, match="one of"):
        schema.check({"read": "a", "strongest_point": "b", "biggest_worry": "c",
                      "would_own": "maybe"}, prompts.LENS_SCHEMA)


def test_too_many_list_items_are_rejected():
    with pytest.raises(schema.SchemaError, match="more than"):
        schema.check({"case": "a", "points": ["1"] * 9,
                      "what_would_disprove_it": "b"}, prompts.CASE_SCHEMA)


def test_the_prompt_describes_the_schema_it_will_be_checked_against():
    described = schema.describe(prompts.SYNTHESIS_SCHEMA)
    for field in prompts.SYNTHESIS_SCHEMA["required"]:
        assert field in described


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------

def test_a_figure_from_the_evidence_passes(run):
    close = run["evidence"]["anchors"]["last_close"]
    assert citations.uncited(f"It trades at ${close:,.2f}.", run) == []


def test_an_invented_price_is_caught(run):
    """Far from anything the run holds, so this cannot pass by coincidence.

    A near miss (a price one percent off a real one) is deliberately allowed:
    the check is a floor against invention, not a proof of accuracy.
    """
    assert citations.uncited("It trades at $91,234.56.", run)


def test_an_invented_percentage_is_caught(run):
    assert citations.uncited("Operating margin is 61.4%.", run)


def test_a_real_percentage_passes(run):
    margin = run["evidence"]["metrics"][0]["operating_margin"]
    assert citations.uncited(f"Operating margin is {margin * 100:.1f}%.", run) == []


def test_rounding_is_not_treated_as_invention(run):
    margin = run["evidence"]["metrics"][0]["operating_margin"]
    assert citations.uncited(f"Operating margin is about {margin * 100:.0f}%.", run) == []


def test_years_and_small_counts_do_not_need_citations(run):
    assert citations.uncited("In 2026 there were 3 clear risks.", run) == []


def test_audit_only_reads_the_prose_fields(run):
    payload = {"case": "Margin is 61.4%.", "would_own": "no", "score": 93.7}
    assert citations.audit(payload, run, fields=["case"]) == ["61.4%"]


# ---------------------------------------------------------------------------
# The call loop
# ---------------------------------------------------------------------------

def _stub(replies):
    calls = {"n": 0, "prompts": []}

    def ask(prompt, timeout=0, model=None, warm=True):
        calls["prompts"].append(prompt)
        reply = replies[min(calls["n"], len(replies) - 1)]
        calls["n"] += 1
        return True, reply

    return ask, calls


GOOD_CASE = json.dumps({"case": "Margins are strong.",
                        "points": ["Point one.", "Point two."],
                        "what_would_disprove_it": "Margins falling."})


def test_a_good_reply_is_accepted_first_time(run, monkeypatch):
    ask, calls = _stub([GOOD_CASE])
    monkeypatch.setattr(headless, "ask", ask)
    section = analysis._call("prompt", prompts.CASE_SCHEMA, run, "bull")
    assert section.ok and section.attempts == 1


def test_a_bad_shape_is_retried_once_then_accepted(run, monkeypatch):
    ask, calls = _stub(['{"case": "no points here"}', GOOD_CASE])
    monkeypatch.setattr(headless, "ask", ask)
    section = analysis._call("prompt", prompts.CASE_SCHEMA, run, "bull")
    assert section.ok and section.attempts == 2
    assert "REJECTED" in calls["prompts"][1]


def test_an_uncited_figure_is_retried_with_the_figure_named(run, monkeypatch):
    bad = json.dumps({"case": "Margins reached 61.4%.",
                      "points": ["a", "b"], "what_would_disprove_it": "x"})
    ask, calls = _stub([bad, GOOD_CASE])
    monkeypatch.setattr(headless, "ask", ask)
    section = analysis._call("prompt", prompts.CASE_SCHEMA, run, "bull")
    assert section.ok
    assert "61.4%" in calls["prompts"][1]


def test_two_failures_drop_the_section_rather_than_publishing_it(run, monkeypatch):
    ask, _ = _stub(['{"nope": 1}'])
    monkeypatch.setattr(headless, "ask", ask)
    section = analysis._call("prompt", prompts.CASE_SCHEMA, run, "bull")
    assert not section.ok
    assert section.attempts == 2
    assert section.data is None


# ---------------------------------------------------------------------------
# The brief
# ---------------------------------------------------------------------------

def test_the_brief_never_contains_a_dash_instruction_violation(run):
    assert "—" not in prompts.evidence_brief(run)


def test_the_brief_states_the_verdicts_but_forbids_changing_them(run):
    brief = prompts.evidence_brief(run)
    assert "which you may not change" in brief
    assert run["verdicts"]["long_term"]["rating"] in brief


def test_bull_and_bear_are_not_shown_each_other(run):
    brief = prompts.evidence_brief(run)
    bull, bear = prompts.bull_prompt(brief), prompts.bear_prompt(brief)
    assert "have not seen anyone else's view" in bull
    assert "have not seen anyone else's view" in bear


def test_dissent_is_recorded_against_the_formula_not_applied(run):
    section = analysis.Section("synthesis", data={
        "thesis": "t", "strongest_counterargument": "c",
        "what_would_change_it": ["a", "b"], "unresolved": "u",
        "dissent": {"disagrees": True, "horizon": "long_term",
                    "direction": "more negative", "reason": "r"}})
    dissent = analysis._dissent(section, run)
    assert dissent["against_rating"]["long_term"] == run["verdicts"]["long_term"]["rating"]
    assert "not applied" in dissent["note"]


def test_no_dissent_recorded_when_the_model_agrees(run):
    section = analysis.Section("synthesis", data={
        "thesis": "t", "strongest_counterargument": "c",
        "what_would_change_it": ["a", "b"], "unresolved": "u",
        "dissent": {"disagrees": False}})
    assert analysis._dissent(section, run) is None


def test_a_dissent_without_a_reason_is_rejected():
    """Recording disagreement and explaining nothing is worse than agreeing."""
    complaint = analysis._dissent_is_complete({"dissent": {"disagrees": True}})
    assert complaint and "reason" in complaint


def test_a_complete_dissent_passes():
    assert analysis._dissent_is_complete(
        {"dissent": {"disagrees": True, "direction": "more negative",
                     "reason": "the tape disagrees"}}) is None


def test_agreement_needs_no_reason():
    assert analysis._dissent_is_complete({"dissent": {"disagrees": False}}) is None


def test_an_unexplained_dissent_is_not_published(run):
    section = analysis.Section("synthesis", data={
        "thesis": "t", "strongest_counterargument": "c",
        "what_would_change_it": ["a", "b"], "unresolved": "u",
        "dissent": {"disagrees": True}})
    assert analysis._dissent(section, run) is None


def test_magnitude_suffixes_are_case_sensitive(run):
    """"$1.72T" is a trillion; "12m" is this engine's own window label, and
    reading it as twelve million made the check reject its own vocabulary."""
    cap = run["evidence"]["metrics"][0]["market_cap"]
    assert citations.uncited(f"Market cap ${cap / 1e9:.2f}B.", run) == []
    assert citations.uncited("The 12m return was flat.", run) == []


def test_an_invented_magnitude_is_still_caught(run):
    assert citations.uncited("Revenue of $9,400B.", run)


def test_the_dissent_fields_are_required_unconditionally():
    """A conditional requirement is one the model can forget, and it did."""
    spec = prompts.SYNTHESIS_SCHEMA["properties"]["dissent"]
    assert set(spec["required"]) == {"disagrees", "direction", "reason"}
    assert "none" in spec["properties"]["direction"]["enum"]


def test_a_usage_limit_reply_is_recognized_rather_than_blamed_on_the_schema():
    """The reply is prose, so a JSON-only caller reports "no JSON could be
    parsed" and sends the reader to look at the schema instead of the clock."""
    assert headless.looks_like_a_limit(
        "Claude usage limit reached. Your limit will reset at 3pm.")
    assert not headless.looks_like_a_limit('{"alive": true}')


def test_a_short_reply_about_a_rate_limit_is_not_a_usage_limit():
    """The old phrase list counted any short reply saying "rate limit" as the subscription spent."""
    assert not headless.looks_like_a_limit("The rate limit on new capacity is the constraint.")
    assert not headless.looks_like_a_limit("Too many requests to the SEC; please try again later.")
    assert headless.looks_like_a_limit("You've hit your session limit · resets 7:20pm")


def test_a_limit_notice_from_the_cli_raises_so_the_batch_actually_stops(monkeypatch):
    """Nothing used to raise UsageLimit, so the stop-the-batch handler never ran."""
    import asyncio

    async def cold(prompt, timeout, model):
        return replies.pop(0)

    monkeypatch.setattr(headless, "_ask_cold", cold)
    replies = [(True, "Claude AI usage limit reached|1759512000"),
               (False, "API Error. " + "context " * 150 + "You've hit your session limit"),
               (True, '{"case": "a normal answer"}')]
    for _ in range(2):
        with pytest.raises(headless.UsageLimit):
            asyncio.run(headless.ask_async("hi", warm=False))
    assert asyncio.run(headless.ask_async("hi", warm=False)) == (True, '{"case": "a normal answer"}')


def test_a_long_reply_mentioning_a_rate_limit_is_not_a_usage_limit():
    """A thesis discussing rate limits is not the API refusing to answer."""
    prose = '{"case": "the rate limit on new capacity is the constraint"}' * 20
    assert not headless.looks_like_a_limit(prose)


def test_the_batch_stops_on_a_usage_limit_instead_of_burning_every_call(run, monkeypatch):
    calls = {"n": 0}

    def ask(prompt, timeout=0, model=None, warm=False):
        calls["n"] += 1
        raise headless.UsageLimit("Claude usage limit reached")

    monkeypatch.setattr(headless, "ask", ask)
    monkeypatch.setattr(headless, "available", lambda: (True, "stub"))
    result = analysis.analyze(run)
    assert result["usage_limited"]
    assert result["ok"] is False
    assert calls["n"] == 1, "it should stop on the first refusal, not try them all"


def test_a_fraction_matches_regardless_of_sign(run):
    """The direction usually lives in the sentence: "a 29.4% drawdown" and
    "-29.4%" are the same fact."""
    drawdown = run["evidence"]["anchors"]["max_drawdown_1y"]
    assert drawdown < 0
    assert citations.uncited(f"A {abs(drawdown) * 100:.1f}% drawdown.", run) == []


def test_sign_flipping_does_not_excuse_an_invented_fraction(run):
    assert citations.uncited("A 61.4% drawdown.", run)


def test_a_figure_rounded_to_its_written_precision_is_cited():
    """-3.6% stands for -3.639% (a 1.07% relative gap); TSLA's trend lens was rejected for it."""
    from edgedesk.llm import citations
    run = {"evidence": {"facts": {"rel.sector_spy_1m": {"id": "rel.sector_spy_1m", "value": -0.03639}}}}
    assert citations.uncited("the sector lagged by -3.6% over a month", run) == []
    assert citations.uncited("the sector lagged by -7.7% over a month", run) == ["-7.7%"]


def test_over_long_prose_is_cut_at_a_sentence_not_thrown_away():
    from edgedesk.llm import schema
    spec = {"type": "object", "properties": {"case": {"type": "string", "max_length": 60}}}
    text = "First sentence is here. Second sentence is here too. Third one runs well past the cap."
    assert schema.fit_lengths({"case": text}, spec) == {"case": "First sentence is here. Second sentence is here too."}
    one = "One long sentence with no break that simply keeps on going well past the cap"
    assert schema.fit_lengths({"case": one}, spec) == {"case": one}


def test_an_explicit_date_is_clamped_to_the_last_settled_session(monkeypatch):
    """Audit 2: the desk passed the run's own date, and an explicit date came back unchanged, so a
    run mid-session analyzed today's live bar while the other teams used yesterday's close."""
    from datetime import date as _date

    from edgedesk.providers import client
    monkeypatch.setattr(client, "last_settled_day", lambda *a, **k: _date(2026, 9, 23))
    assert client.settled_as_of("2026-09-24") == _date(2026, 9, 23)      # today, mid-session
    assert client.settled_as_of("2026-09-15") == _date(2026, 9, 15)      # a past date stands
    assert client.settled_as_of(None) == _date(2026, 9, 23)


def test_a_synthesis_without_the_bear_case_is_labelled_partial_everywhere(run, monkeypatch):
    """Stage B3 (R2-02). NVDA 2026-09-18: bull and bear failed, and the synthesis still read as
    a finished two-sided view. Now the state is recorded, the prompt says a side is missing,
    and every report format carries the label."""
    from edgedesk import reports

    synth = {"thesis": "Strong business at a full price.", "strongest_counterargument": "Growth slows.",
             "what_would_change_it": ["x"], "unresolved": "y",
             "dissent": {"disagrees": False, "direction": "none", "horizon": "none", "reason": "Fits."}}
    seen = {}

    def fake_call(prompt, spec, run_, name, extra_check=None):
        seen[name] = prompt
        if name == "bear":
            return analysis.Section("bear", error="failed validation twice")
        data = synth if name == "synthesis" else json.loads(GOOD_CASE) if name == "bull" else None
        return analysis.Section(name, data=data, error=None if data else "skip")

    monkeypatch.setattr(headless, "available", lambda: (True, "stub"))
    monkeypatch.setattr(analysis, "_call", fake_call)
    for fn in ("second_look", "headline_read"):
        monkeypatch.setattr(analysis.research, fn, lambda *a, **k: None)
    monkeypatch.setattr(analysis.research, "filing_research", lambda *a, **k: None)
    result = analysis.analyze(run, lenses=(), filings=[], filings_error="none")
    assert result["completeness"] == "partial" and result["missing_cases"] == ["bear"]
    assert "could not produce one" in seen["synthesis"]
    run["llm"] = result
    for fmt in ("full", "condensed", "card"):
        assert "Partial: the bear case was unavailable" in reports.render(run, fmt), fmt


def test_each_section_reports_its_real_outcome_as_it_finishes(run, monkeypatch):
    """Stage B4 (R2-17): the desk marked a section "Written" when the next one started, so V's
    failed bear case showed as written. Now each section reports its own outcome."""
    def fake_call(prompt, spec, run_, name, extra_check=None):
        if name == "bear":
            return analysis.Section("bear", error="failed validation twice")
        return analysis.Section(name, data=json.loads(GOOD_CASE))

    monkeypatch.setattr(headless, "available", lambda: (True, "stub"))
    monkeypatch.setattr(analysis, "_call", fake_call)
    monkeypatch.setattr(analysis.research, "second_look", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(analysis.research, "headline_read", lambda *a, **k: {"ok": False, "error": "no headlines"})
    monkeypatch.setattr(analysis.research, "filing_research", lambda *a, **k: {"ok": True})
    seen, texts = [], []
    analysis.analyze(run, lenses=("deep_value",), filings=[], filings_error="none",
                     on_section=lambda name, ok, error, text="": seen.append((name, ok, error)) or texts.append(text))
    assert seen == [("lens:deep_value", True, None), ("bull", True, None),
                    ("bear", False, "failed validation twice"), ("synthesis", True, None),
                    ("second_look", True, None), ("headline_read", False, "no headlines"),
                    ("filing_research", True, None)]
    assert texts[0] == "Margins are strong." and texts[2] == ""      # a lens's prose; a failed bear has none


# ---------------------------------------------------------------------------
# Stage C4 (R2-03): units are normalised once, and a stated direction must match the sign
# ---------------------------------------------------------------------------

def _known(*fractions):
    return {citations.FRACTION: set(fractions), citations.OTHER: set(fractions)}


def test_a_percentage_is_not_scaled_twice():
    """Codex's probe: "2400%" matched a stored 0.24, because the % was divided out and then the
    figure was scaled by 100 again."""
    k = _known(0.24)
    assert citations.uncited("margins of 2400%", {}, k) == ["2400%"]
    assert citations.uncited("margins of 24%", {}, k) == []
    assert citations.uncited("margins of 24", {}, k) == []            # a bare 24 may mean 24%
    assert citations.uncited("margins of 24%", {}, _known(24.0)) == []  # stored in percentage points


def test_a_stated_direction_must_match_the_sign():
    k = _known(-0.24)
    assert citations.uncited("revenue grew 24% last year", {}, k) == ["24%"]
    assert citations.uncited("revenue fell 24% last year", {}, k) == []
    assert citations.uncited("a 24% decline in revenue", {}, k) == []
    assert citations.uncited("revenue moved 24%", {}, k) == []          # no direction word: magnitude
    assert citations.uncited("revenue fell 24%", {}, _known(0.24)) == ["24%"]
    assert citations.uncited("lagged SPY by 11.2%", {}, _known(-0.112)) == []


def test_an_expired_login_stops_the_batch_like_a_usage_limit():
    """AKAM 2026-10-04: all ten written sections failed with this and the report was still saved."""
    assert headless.looks_like_a_limit("Failed to authenticate: OAuth session expired and could not be refreshed", failed=True)


# ---------------------------------------------------------------------------
# The ChatGPT plan (2026-10-05)
# ---------------------------------------------------------------------------

def test_the_chatgpt_plan_routes_through_codex_and_keeps_the_stops(monkeypatch):
    from edgedesk.llm import chatgpt
    monkeypatch.setenv("EDGE_DESK_PLAN", "chatgpt")
    base = {"text": '{"a": 1}', "data": None, "model": "gpt-6-luna", "seconds": 1, "failed": False,
            "error": "", "stray": [], "usage": {}}
    monkeypatch.setattr(chatgpt, "run", lambda *a, **k: base)
    assert headless.ask("q") == (True, '{"a": 1}')
    monkeypatch.setattr(chatgpt, "run", lambda *a, **k: {**base, "stray": ["command_execution"]})
    ok, text = headless.ask("q")
    assert not ok and "may only answer" in text
    monkeypatch.setattr(chatgpt, "run", lambda *a, **k: {**base, "failed": True, "text": "", "error": "Not logged in. Run codex login."})
    with pytest.raises(headless.UsageLimit):
        headless.ask("q")
    monkeypatch.delenv("EDGE_DESK_PLAN")
    monkeypatch.delenv("DESK_PLAN", raising=False)
    assert headless.plan() == "claude"
