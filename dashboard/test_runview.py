"""python -m pytest dashboard   (from the Hedge Fund folder, with the desk venv)"""

import runview


def test_team_names_replace_desk_letters_with_grammar():
    assert runview.teams("Desk B is arguing. Desk A's report") == "The Veterans are arguing. The Quant desk's report"
    assert runview.teams("the gap in Desk B's case") == "the gap in the Veterans' case"
    assert runview.teams(["Desk A has it"]) == ["The Quant desk has it"]


def test_prices_reads_ranges_and_single_levels():
    assert runview.prices("110.00 to 112.00") == [110.0, 112.0]
    assert runview.prices("Weekly close below 103") == [103.0]
    assert runview.prices("1,234.5") == [1234.5]


def test_gist_skips_headings_and_tables():
    text = "## MSFT Technical Report\n| a | b |\n1. A growth story\nRevenue grew 17.8% in the quarter to $90B, again."
    assert runview.gist(text) == "Revenue grew 17.8% in the quarter to $90B, again."


def test_labelled_pulls_the_executive_summary():
    text = "**Rating**: Hold\n\n**Executive Summary**: Hold at current weight. More after that."
    assert runview.labelled(text, "Executive Summary") == "Hold at current weight."


def test_build_on_recorded_runs_when_present():
    """Every run on file must build, whichever teams it used and whether or not it was debated."""
    for r in runview.index():
        view = runview.build(r["ticker"], r["date"])
        assert view["engines"] and all(view[e] for e in view["engines"])
        assert "Desk A" not in view["memo_md"] and "Desk B" not in view["memo_md"]
        if view["mode"] == "reports":
            assert view["horizons"] == []
        else:
            assert view["horizons"] and all(h["memo"]["headline"] for h in view["horizons"])
            assert all(set(h["voters"]) <= set(view["engines"]) for h in view["horizons"])
        if view["quant"]:
            assert len(view["quant"]["agents"]) == 12
        if view["edge"]:
            # 9 steps before Edge Desk 2; 12 since (swing second look, headline read, filing research)
            assert len(view["edge"]["agents"]) in (9, 12) and view["edge"]["report_md"]


# ---- Phase 2 (2026-09-24): "as they ran" retired, agreement relabelled, the memo's own reports first

import json
import shutil
from pathlib import Path

import pytest

NVDA = runview.MEMOS / "NVDA-2026-09-18.json"


@pytest.fixture
def memos(tmp_path, monkeypatch):
    if not NVDA.exists():
        pytest.skip("the NVDA 2026-09-18 run is not on this machine")
    monkeypatch.setattr(runview, "MEMOS", tmp_path)
    (tmp_path / "runs").mkdir()
    return tmp_path


def _save(memos: Path, stem: str, bundle: dict, loose_edge: dict | None = None) -> None:
    (memos / f"{stem}.json").write_text(json.dumps(bundle), encoding="utf-8")
    run_dir = memos / "runs" / stem
    run_dir.mkdir()
    (run_dir / "bars.json").write_text("[]", encoding="utf-8")        # no network in tests
    if loose_edge:
        (run_dir / "edge-desk.json").write_text(json.dumps(loose_edge), encoding="utf-8")


def test_a_run_saved_now_shows_its_own_window_and_each_team_s_clock(memos):
    b = json.loads(NVDA.read_text(encoding="utf-8"))
    b["date"] = "2026-09-24"
    b["horizons"].pop("as_they_ran")
    b["native"] = {"A": {"rating": "Overweight", "timeframe": "3-6 months"},
                   "B": {"rating": "Overweight", "timeframe": None},
                   "C": {"rating": "Buy", "timeframe": "1 year or more"}}
    for key, span in (("swing", "2 days to 8 weeks"), ("long_term", "1 year or more")):
        b["horizons"][key]["span"] = span
        b["horizons"][key]["outcome"]["conviction"] = "Agreed before debate"
    _save(memos, "NVDA-2026-09-24", b)
    view = runview.build("NVDA", "2026-09-24")
    assert [h["key"] for h in view["horizons"]] == ["swing", "long_term"]
    assert view["horizons"][0]["sub"] == "2 days to 8 weeks"
    assert view["horizons"][0]["conviction"] == "Agreed before debate"
    assert view["quant"]["timeframe"] == "3-6 months" and view["vets"]["timeframe"] is None


def test_an_old_run_still_opens_as_it_was_asked(memos):
    shutil.copy(NVDA, memos / NVDA.name)
    (memos / "runs" / NVDA.stem).mkdir()
    (memos / "runs" / NVDA.stem / "bars.json").write_text("[]", encoding="utf-8")
    view = runview.build("NVDA", "2026-09-18")
    keys = [h["key"] for h in view["horizons"]]
    assert keys == ["as_they_ran", "swing", "long_term"]
    assert view["horizons"][1]["sub"] == "2 to 6 weeks"                 # what that run was asked
    assert view["quant"]["timeframe"] == "3-6 months"                   # from its "as they ran" block


def test_the_snapshot_shows_the_reports_the_memo_was_written_from(memos):
    """The audit's A08: a later run that day rewrote NVDA's loose Edge report under a finished memo."""
    b = json.loads(NVDA.read_text(encoding="utf-8"))
    rewritten = {**b["edge_desk"], "report_md": "# A report written after the memo", "score": 1.0}
    _save(memos, NVDA.stem, b, loose_edge=rewritten)
    view = runview.build("NVDA", "2026-09-18")
    assert view["edge"]["report_md"] == b["edge_desk"]["report_md"]
    assert view["edge"]["score"] == b["edge_desk"]["score"]


def test_a_withheld_horizon_opens_with_its_reason_and_no_levels(memos):
    """Stage C1: a stale filing withholds long term desk-wide; the snapshot must still open."""
    from desk.pipeline import withheld_horizon
    b = json.loads(NVDA.read_text(encoding="utf-8"))
    b["date"] = "2026-09-24"
    b["horizons"].pop("as_they_ran")
    b["horizons"]["long_term"] = withheld_horizon(
        "long_term", {"codes": ["companyfacts_stale"], "teams": ["B", "C"],
                      "reason": "the latest filing is not in SEC's financial-data feed yet"}, False)
    _save(memos, "NVDA-2026-09-24", b)
    lt = next(h for h in runview.build("NVDA", "2026-09-24")["horizons"] if h["key"] == "long_term")
    assert lt["status"] == "withheld" and lt["rating"] is None and lt["memo"]["levels"] == []
    assert "financial-data feed" in lt["how"] and lt["action"].startswith("Withheld")


# ---------------------------------------------------------------------------
# The first screen (2026-10-05): warnings computed from the run, earlier runs, Edge step states
# ---------------------------------------------------------------------------

def test_warnings_are_computed_from_the_saved_run():
    stale = {"verdict": {"data_caveats": ["Fundamentals are a quarter stale: the feed ends at 2026-03-31."]},
             "anchors": {"as_of": "2026-09-23"}}
    edge = {"rating": None, "anchors": {"as_of": "2026-09-24"}, "caveats": [{"code": "companyfacts_stale"}],
            "steps": [{"id": "bull", "name": "Bull case", "saved": True}, {"id": "bear", "name": "Bear case", "saved": False, "error": "timed out"},
                      {"id": "synthesis", "name": "Synthesis", "saved": True},
                      {"id": "filing_research", "name": "Filing research", "saved": False, "error": "the SEC archive refused (HTTP 403)"}]}
    bundle = {"horizons": {"long_term": {"debate": {"turns": [{"note": "figures found in no report: 9.9%"}]}}}}
    kinds = {f["kind"]: f for f in runview.run_flags(bundle, stale, edge, ["quant", "vets", "edge"])}
    assert set(kinds) == {"stale", "prices", "edge_withheld", "partial", "unavailable", "figures"}
    assert kinds["stale"]["level"] == "high" and "without the bear case" in kinds["partial"]["text"]
    # A withheld long term replaces the stale warning, with the desk's reason.
    bundle["quality"] = {"withheld": {"long_term": {"reason": "the latest filing is not in SEC's data feed"}}}
    bundle["price_check"] = {"consistent": True}
    kinds = {f["kind"] for f in runview.run_flags(bundle, stale, edge, ["quant", "vets", "edge"])}
    assert "withheld_lt" in kinds and "stale" not in kinds and "prices" not in kinds


def test_edge_steps_are_counted_apart():
    assert runview.step_state(True, None) == "ok"
    assert runview.step_state(False, "skipped: both cases failed") == "skipped"
    assert runview.step_state(False, "the SEC archive refused the filing request (HTTP 403)") == "unavailable"
    assert runview.step_state(False, "the reply did not match the shape asked for") == "failed"


def test_earlier_runs_of_the_symbol_come_newest_first(memos):
    for day, rating, close in (("2026-09-10", "Hold", 100.0), ("2026-09-20", "Overweight", 110.0)):
        (memos / f"NVDA-{day}.json").write_text(json.dumps({
            "horizons": {"long_term": {"outcome": {"rating": rating, "status": "agreed"}}},
            "ai_hedge_fund": {"last_close": close}}), encoding="utf-8")
    hs = runview.history("NVDA", "2026-09-24")
    assert [h["date"] for h in hs] == ["2026-09-20", "2026-09-10"]
    assert hs[0]["ratings"]["long_term"] == "Overweight" and hs[0]["close"] == 110.0
    assert runview.history("NVDA", "2026-09-10") == []
