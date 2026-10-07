"""A team report saved earlier the same day stands in for a new run only when its inputs match."""

import json
import sys
from pathlib import Path

import pytest

import desk.provenance as prov
from desk import engines

REAL_DIGEST = prov.source_digest.__wrapped__     # the fixture below stubs the cached one


@pytest.fixture(autouse=True)
def fixed_world(tmp_path, monkeypatch):
    # No git calls and no real settings files: the inputs depend only on what each test sets.
    monkeypatch.setattr(prov, "source_digest", lambda folder: "abc123")
    monkeypatch.setattr(prov, "EDGE_ENV", tmp_path / "no-edge.env")
    monkeypatch.setattr(prov, "MANDATES", tmp_path / "mandates")
    (tmp_path / "mandates").mkdir()
    (tmp_path / "mandates" / "test-2.yaml").write_text("pods: [a]\n", encoding="utf-8")
    for k in ("HEDGE_FUND_LLM_MODEL", "DESK_CHATGPT_STRONG", "DESK_CHATGPT_FAST", "EDGE_DESK_MODEL"):
        monkeypatch.delenv(k, raising=False)


def _saved(runs: Path, folder: str, name: str, inputs: dict | None, rating="Hold") -> None:
    (runs / folder).mkdir(parents=True, exist_ok=True)
    payload = {"engine": "x", "rating": rating, **({"inputs": inputs} if inputs is not None else {})}
    (runs / folder / name).write_text(json.dumps(payload), encoding="utf-8")


def test_same_inputs_are_reused_and_copied_into_the_new_run(tmp_path):
    runs = tmp_path / "runs"
    wanted = prov.expected("vets", "claude", "2026-10-06", "test-2")
    _saved(runs, "ECG-2026-10-07-101500-aaaaaa", "ai-hedge-fund.json", wanted)
    new = runs / "ECG-2026-10-07-143000-bbbbbb"
    new.mkdir()
    got = engines.reusable(new, "ai-hedge-fund.json", wanted, "ai-hedge-fund", say=lambda *_: None)
    assert got["reused"] and got["reused_from"] == "ECG-2026-10-07-101500-aaaaaa" and got["rating"] == "Hold"
    assert json.loads((new / "ai-hedge-fund.json").read_text())["reused_from"] == "ECG-2026-10-07-101500-aaaaaa"


@pytest.mark.parametrize("change", ["plan", "session", "model", "mandate", "legacy"])
def test_different_or_unknown_inputs_run_the_team_again(tmp_path, monkeypatch, change):
    runs = tmp_path / "runs"
    saved = prov.expected("vets", "claude", "2026-10-06", "test-2")
    if change == "legacy":
        saved = None                                   # a report from before reports carried inputs
    _saved(runs, "ECG-2026-10-07", "ai-hedge-fund.json", saved)
    if change == "model":
        monkeypatch.setenv("HEDGE_FUND_LLM_MODEL", "claude-other")
    if change == "mandate":
        (tmp_path / "mandates" / "test-2.yaml").write_text("pods: [a, b]\n", encoding="utf-8")
    wanted = prov.expected("vets", "chatgpt" if change == "plan" else "claude",
                           "2026-10-07" if change == "session" else "2026-10-06", "test-2")
    new = runs / "ECG-2026-10-07-143000-bbbbbb"
    new.mkdir()
    said = []
    assert engines.reusable(new, "ai-hedge-fund.json", wanted, "ai-hedge-fund", say=said.append) is None
    assert said and "running it again" in said[0]
    assert not (new / "ai-hedge-fund.json").exists()


def test_another_symbol_or_day_is_never_a_candidate(tmp_path):
    runs = tmp_path / "runs"
    wanted = prov.expected("edge", "claude", "2026-10-06")
    _saved(runs, "ECGX-2026-10-07-101500-aaaaaa", "edge-desk.json", wanted)
    _saved(runs, "ECG-2026-10-06-101500-aaaaaa", "edge-desk.json", wanted)
    new = runs / "ECG-2026-10-07-143000-bbbbbb"
    new.mkdir()
    assert engines.reusable(new, "edge-desk.json", wanted, "Edge Desk", say=lambda *_: None) is None


def test_chatgpt_inputs_name_the_chatgpt_models(monkeypatch):
    monkeypatch.setenv("DESK_CHATGPT_STRONG", "gpt-strong")
    a = prov.expected("quant", "chatgpt", "2026-10-06")
    assert a["settings"]["DESK_CHATGPT_STRONG"] == "gpt-strong"
    assert "DESK_CHATGPT_STRONG" not in prov.expected("quant", "claude", "2026-10-06")["settings"]


def test_edge_s_own_settings_file_counts_for_its_model(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_DESK_MODEL", "inherited")
    prov.EDGE_ENV.write_text("EDGE_DESK_MODEL=edge-own\n", encoding="utf-8")
    assert prov.expected("edge", "claude", "2026-10-06")["settings"]["EDGE_DESK_MODEL"] == "edge-own"


def test_a_team_runner_is_told_the_run_s_plan_and_a_stale_output_never_passes(tmp_path):
    script = tmp_path / "runner.py"
    script.write_text("import json, os, sys\nfrom pathlib import Path\n"
                      "Path(sys.argv[1]).write_text(json.dumps({'engine': 'x', 'rating': 'Hold', "
                      "'plan': os.environ.get('DESK_PLAN')}))\n", encoding="utf-8")
    out = tmp_path / "out.json"
    got = engines._run(Path(sys.executable), tmp_path, [script, out], out, tmp_path / "log.txt", 60,
                       "x", lambda *_: None, plan="chatgpt")
    assert got["plan"] == "chatgpt"
    failing = tmp_path / "fail.py"
    failing.write_text("raise SystemExit(3)\n", encoding="utf-8")
    from desk import DeskError
    with pytest.raises(DeskError):                    # out.json from before must not count as this run's
        engines._run(Path(sys.executable), tmp_path, [failing], out, tmp_path / "log.txt", 60, "x", lambda *_: None)


@pytest.mark.parametrize("name,value", [("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "3"),
                                        ("TRADINGAGENTS_MAX_RISK_DISCUSS_ROUNDS", "2"),
                                        ("TRADINGAGENTS_OUTPUT_LANGUAGE", "Spanish")])
def test_any_research_setting_change_reruns_the_quant_desk(monkeypatch, name, value):
    # Codex F2: these were outside a hand-kept list, so a deeper run silently reused a shallow one.
    monkeypatch.delenv(name, raising=False)
    before = prov.expected("quant", "claude", "2026-10-06")
    monkeypatch.setenv(name, value)
    assert prov.differs(before, prov.expected("quant", "claude", "2026-10-06")) == "its research settings differs"


def test_secrets_and_output_locations_are_not_part_of_the_inputs(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_RESULTS_DIR", "/tmp/a")
    monkeypatch.setenv("HEDGE_FUND_API_KEY", "sk-not-recorded")
    monkeypatch.setenv("EDGE_DESK_VAULT_DIR", "/vault")
    recorded = str([prov.expected(e, "claude", "2026-10-06", "test-2") for e in ("quant", "vets", "edge")])
    assert "sk-not-recorded" not in recorded and "/tmp/a" not in recorded and "/vault" not in recorded


def test_an_edge_setting_blanked_in_its_own_file_is_not_inherited(monkeypatch):
    monkeypatch.setenv("EDGE_DESK_EFFORT", "high")
    prov.EDGE_ENV.write_text("EDGE_DESK_EFFORT=\n", encoding="utf-8")
    assert "EDGE_DESK_EFFORT" not in prov.expected("edge", "claude", "2026-10-06")["settings"]


def test_source_identity_is_the_content_not_git(tmp_path):
    # Codex F3: a ZIP install has no git history, so HEAD was None on both sides and always "matched".
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n", encoding="utf-8")
    (pkg / "tests").mkdir()
    (pkg / "tests" / "t.py").write_text("ignored\n", encoding="utf-8")
    one = REAL_DIGEST(pkg)
    (pkg / "tests" / "t.py").write_text("still ignored\n", encoding="utf-8")
    assert REAL_DIGEST(pkg) == one
    (pkg / "a.py").write_bytes(b"x = 1\r\n")                  # a CRLF checkout is the same code
    assert REAL_DIGEST(pkg) == one
    (pkg / "a.py").write_text("x = 2\n", encoding="utf-8")     # an uncommitted edit is not
    assert REAL_DIGEST(pkg) != one
    assert REAL_DIGEST(tmp_path / "missing") is None


def test_unknown_source_refuses_reuse_even_when_both_sides_are_unknown(monkeypatch):
    monkeypatch.setattr(prov, "source_digest", lambda folder: None)
    a = prov.expected("edge", "claude", "2026-10-06")
    assert "cannot be read" in prov.differs(a, prov.expected("edge", "claude", "2026-10-06"))
