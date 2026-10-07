"""Every run of a day is kept and can be opened; levels show whether code computed them.
Synthetic runs only, so this runs anywhere (no recorded run, no network)."""

import json
from pathlib import Path

import pytest

import runview
import validate


@pytest.fixture
def memos(tmp_path, monkeypatch):
    monkeypatch.setattr(runview, "MEMOS", tmp_path)
    (tmp_path / "runs").mkdir()
    return tmp_path


def _bundle(run_id, at, rating, plan="claude"):
    return {"ticker": "DEMO", "date": "2026-10-07", "run_id": run_id, "generated_at": f"2026-10-07T{at}:00",
            "engines": ["edge"], "mode": "reports", "plan": {"name": plan, "models": []},
            "edge_desk": {"rating": rating, "last_close": 50.0, "anchors": {"as_of": "2026-10-06"},
                          "inputs": {"plan": plan}, "reused_from": None},
            "horizons": {}}


def _save_run(memos: Path, bundle: dict, pointer: bool) -> None:
    stem = f"DEMO-{bundle['date']}"
    folder = memos / "runs" / (f"{stem}-{bundle['run_id']}" if bundle.get("run_id") else stem)
    folder.mkdir()
    (folder / "bars.json").write_text("[]", encoding="utf-8")              # no network in tests
    if bundle.get("run_id"):
        (folder / "memo.json").write_text(json.dumps(bundle), encoding="utf-8")
    if pointer:
        (memos / f"{stem}.json").write_text(json.dumps(bundle), encoding="utf-8")


def test_two_runs_of_one_day_are_both_kept_and_the_latest_opens_by_default(memos):
    first, second = _bundle("101500-aaaaaa", "10:15", "Hold"), _bundle("143000-bbbbbb", "14:30", "Sell", "chatgpt")
    _save_run(memos, first, pointer=False)                   # replaced as the day's result by the second
    _save_run(memos, second, pointer=True)
    latest = runview.build("DEMO", "2026-10-07")
    assert latest["run_id"] == "143000-bbbbbb" and latest["latest"] and latest["plan"]["name"] == "chatgpt"
    assert [(h["run_id"], h["latest"]) for h in latest["history"]] == [("101500-aaaaaa", False)]
    assert not any(f["kind"] == "earlier_run" for f in latest["flags"])

    earlier = runview.build("DEMO", "2026-10-07", "101500-aaaaaa")
    assert earlier["run_id"] == "101500-aaaaaa" and not earlier["latest"] and earlier["edge"]["rating"] == "Hold"
    assert earlier["flags"][0]["kind"] == "earlier_run"
    assert [(h["run_id"], h["latest"]) for h in earlier["history"]] == [("143000-bbbbbb", True)]
    assert earlier["provenance"]["edge"]["inputs"] == {"plan": "claude"}


def test_a_run_saved_before_run_ids_still_opens(memos):
    legacy = _bundle(None, "09:00", "Hold")
    del legacy["run_id"]
    _save_run(memos, legacy, pointer=True)
    view = runview.build("DEMO", "2026-10-07")
    assert view["run_id"] is None and view["latest"] and view["history"] == []
    assert runview.same_day("DEMO", "2026-10-07")[0]["latest"]


def test_a_run_id_is_validated_before_it_becomes_a_path(memos):
    for bad in ("../x", "101500-AAAAAA", "1015-aaaaaa", "101500-aaaaaa/..", ""):
        with pytest.raises(validate.BadInput):
            validate.run_id(bad)
    with pytest.raises(FileNotFoundError):
        runview.build("DEMO", "2026-10-07", "101500-cccccc")


def test_levels_show_computed_withheld_and_unchecked():
    h = {"memo": {"levels": {
        "entry_zone": {"price": "95.00 to 110.00", "reason": "r", "status": "checked", "low": 95.0, "high": 110.0,
                       "how": "20-day high, to the 20-day low"},
        "stop": {"price": "Withheld", "reason": "r", "status": "withheld", "why": "This run has no ATR"},
        "first_target": {"price": "120", "reason": "old model text"}}}, "outcome": {}}
    lv = {x["key"]: x for x in runview.horizon("swing", h)["memo"]["levels"]}
    assert lv["entry_zone"]["status"] == "checked" and lv["entry_zone"]["values"] == [95.0, 110.0]
    assert lv["stop"]["values"] == [] and lv["stop"]["reason"].startswith("This run has no ATR")
    assert lv["first_target"]["status"] == "unchecked" and lv["first_target"]["values"] == [120.0]
    flags = runview.run_flags({"horizons": {"swing": h}}, None, None, [])
    assert any(f["kind"] == "levels_unchecked" for f in flags)
    del h["memo"]["levels"]["first_target"]
    assert not any(f["kind"] == "levels_unchecked" for f in runview.run_flags({"horizons": {"swing": h}}, None, None, []))
