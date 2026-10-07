"""Memo price levels are computed from the run's shared data, or withheld; never the model's number."""

import sys
from pathlib import Path

from desk.levels import resolve_level, resolve_levels
from desk.pipeline import analyze
from desk.render import markdown

sys.path.insert(0, str(Path(__file__).parent))
from test_desk import ScriptedLLM, _engines, _memo, quiet  # noqa: E402

SHARED = {"anchors": {"last_close": 100.0, "low_20d": 95.0, "high_20d": 110.0, "atr_14": 2.0},
          "valuation": {"street": {"target_low": 90.0, "target_mean": 120.0, "target_high": 140.0}}}


def lv(basis, atr_offset=0, range_to="none", reason="why"):
    return {"basis": basis, "atr_offset": atr_offset, "range_to": range_to, "reason": reason}


def test_a_level_is_computed_from_its_basis_and_atr_offset():
    out = resolve_level(lv("low_20d", -1.5), SHARED)
    assert out["status"] == "checked" and out["price"] == "92.00" and out["low"] == out["high"] == 92.0
    assert out["how"] == "20-day low minus 1.5 x ATR(14)"


def test_a_range_spans_two_bases_in_price_order():
    out = resolve_level(lv("high_20d", range_to="low_20d"), SHARED)
    assert out["price"] == "95.00 to 110.00" and (out["low"], out["high"]) == (95.0, 110.0)


def test_unsupported_levels_are_withheld_with_a_reason():
    assert resolve_level(lv("none"), SHARED)["status"] == "withheld"
    missing = resolve_level(lv("fair_mid"), SHARED)                 # no own-history panel in this run
    assert missing["status"] == "withheld" and "own-history fair value (mid)" in missing["why"]
    assert resolve_level(lv("street_mean", range_to="peg_fair"), SHARED)["status"] == "withheld"
    assert resolve_level(lv("low_20d", 7), SHARED)["status"] == "withheld"      # beyond +/-3 ATR
    assert resolve_level(lv("low_20d", -1), {"anchors": {"low_20d": 95.0}})["status"] == "withheld"  # no ATR
    assert resolve_level(lv("invented_basis"), SHARED)["status"] == "withheld"
    assert resolve_level(lv("low_20d", -3), {"anchors": {"low_20d": 4.0, "atr_14": 2.0}})["status"] == "withheld"


def test_a_free_text_price_from_the_model_never_survives():
    # The review's case: a schema-valid memo carrying 987654.32, found in no report or anchor.
    out = resolve_levels({"entry_zone": {"price": "987654.32", "reason": "trust me"}}, SHARED)
    assert all(v["status"] == "withheld" for v in out.values())
    assert "987654" not in str(out)


def test_no_shared_data_means_every_level_is_withheld():
    out = resolve_levels({k: lv("last_close") for k in ("entry_zone", "stop", "first_target", "trim")}, {})
    assert {v["status"] for v in out.values()} == {"withheld"}


def _memo_with(levels):
    return {**_memo(), "levels": levels}


def test_the_pipeline_stores_computed_levels_and_markdown_shows_withheld_ones():
    ta, aihf = _engines(ta="Overweight", aihf="Overweight")
    aihf["anchors"] = {"last_close": 111.24, "sma_50": 127.66, "low_20d": 105.0, "atr_14": 2.5}
    llm = ScriptedLLM([
        {"rating": "Overweight", "rationale": "x"}, {"rating": "Overweight", "rationale": "y"},
        _memo_with({"entry_zone": lv("low_20d", range_to="last_close"), "stop": lv("low_20d", -1.5),
                    "first_target": lv("sma_50"), "trim": {"price": "987654.32", "reason": "made up"}}),
    ])
    res = analyze(llm, "ECG", "2026-09-15", {"A": ta, "B": aihf}, ["swing"], owns=False, max_rounds=3, say=quiet)
    levels = res["swing"]["memo"]["levels"]
    assert levels["entry_zone"]["price"] == "105.00 to 111.24"
    assert levels["stop"]["price"] == "101.25" and levels["first_target"]["price"] == "127.66"
    assert levels["trim"]["status"] == "withheld"
    prompt = llm.prompts[2][1]["text"]
    assert "LEVEL BASES" in prompt and "low_20d: 20-day low = 105.00" in prompt and "ATR (14 days) = 2.50" in prompt
    bundle = {"ticker": "ECG", "date": "2026-09-15", "generated_at": "x", "model": "m", "mandate": "test-2",
              "owns": False, "horizons": res, "tradingagents": ta, "ai_hedge_fund": aihf,
              "costs": {"tradingagents": None, "ai_hedge_fund": 0.1, "desk": 0.07, "total": 0.17},
              "files": {"json": "ECG-2026-09-15.json"}}
    md = markdown(bundle)
    assert "| Trim / take profit | Withheld | No computed level supports it. made up |" in md
    assert "| Stop / thesis break | 101.25 |" in md and "987654" not in md
