"""A team report saved earlier the same day stands in for a new run only when its inputs match."""

import json
import sys
from pathlib import Path

import pytest

import desk.provenance as prov
from desk import engines


@pytest.fixture(autouse=True)
def fixed_world(tmp_path, monkeypatch):
    # No git calls and no real settings files: the inputs depend only on what each test sets.
    monkeypatch.setattr(prov, "revision", lambda folder: "abc123")
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
