"""The research layer: the model reads, the code checks, the numbers stay put."""

from __future__ import annotations

import json
from datetime import date

import pytest

from edgedesk import run as run_mod
from edgedesk.llm import headless, research
from edgedesk.providers import filings
from tests.conftest import FakeClient, make_metrics

AS_OF = date(2026, 9, 15)
MDNA = ("Item 7. Management's Discussion and Analysis of Financial Condition. "
        "Revenue increased 18% year over year, driven primarily by growth in Mobility gross "
        "bookings. We repurchased $1.5 billion of our common stock during the quarter. "
        "Operating margin expanded as a result of improved cost leverage in our marketplace. "
        + "Filler sentence about nothing in particular. " * 40
        + "Item 7A. Quantitative and Qualitative Disclosures About Market Risk.")
FILING = {"form": "10-K", "filed": "2026-02-13", "period": "2025-12-31", "url": "u",
          "accession": "a", "text": "Table of contents Item 7. Management 12 Item 7A. 30 " + MDNA}


def _rows():
    base = dict(revenue=40e9, net_income=6e9, operating_income=5e9, pretax_income=5e9,
                income_tax=1e9, operating_cash_flow=9e9, capex=1e9, free_cash_flow=8e9,
                stock_compensation=1.5e9, ebitda=6e9, total_debt=10e9, cash=6e9, equity=20e9,
                assets=50e9, shares=2.1e9)
    quarters = [("2026-06-30", "2026-07-29"), ("2026-03-31", "2026-04-29"), ("2025-12-31", "2026-02-10"),
                ("2025-09-30", "2025-10-29"), ("2025-06-30", "2025-07-29"), ("2025-03-31", "2025-04-29"),
                ("2024-12-31", "2025-02-10"), ("2024-09-30", "2024-10-29")]
    return [make_metrics(report_period=period, filing_date=filed, **base)
            for period, filed in quarters]


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr("edgedesk.paths.RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr("edgedesk.paths.ESTIMATES_DIR", tmp_path / "estimates")
    return run_mod.analyze("TEST", AS_OF, client=FakeClient(metrics=_rows()), save=False)


def test_the_real_section_is_cut_out_not_its_table_of_contents_entry():
    got = filings.section(FILING["text"], "10-K", "mdna")
    assert "Revenue increased 18%" in got and len(got) > 1000
    assert filings.to_text("<p>Revenue&nbsp;rose</p><script>x()</script><div>2%</div>") == "Revenue rose\n2%"


def test_a_finding_survives_only_if_its_quote_is_in_the_filing(run, monkeypatch):
    reply = {"findings": [
        {"claim": "Revenue grew 18% on Mobility bookings", "bearing": "growth", "direction": "positive",
         "quote": "Revenue increased 18% year over year, driven primarily by growth in Mobility gross bookings"},
        {"claim": "Management expects margins to double", "bearing": "margin", "direction": "positive",
         "quote": "We expect operating margins to double over the next two years as scale builds"},
        {"claim": "Buybacks of $9.9 billion", "bearing": "capital", "direction": "positive",
         "quote": "We repurchased $1.5 billion of our common stock during the quarter"}],
        "adjustments": {"growth": 1, "margin": 1, "multiple": 0, "reason": "r"}, "summary": "s"}
    monkeypatch.setattr(headless, "ask", lambda *a, **k: (True, json.dumps(reply)))
    out = research.filing_research(run, [FILING])
    assert [f["bearing"] for f in out["findings"]] == ["growth"]
    why = {d["claim"][:10]: d["why_dropped"] for d in out["dropped"]}
    assert "not in the filing" in why["Management"] and "9.9" in why["Buybacks o"]
    # The margin nudge lost its only finding, so it is void; the growth nudge stands.
    assert out["adjustments"]["margin"] == 0 and out["adjustments"]["growth"] == 1
    assert out["researched"]["expected_annual_return"] > out["formula_expected_return"]
    assert out["researched"]["steps"] == {"growth": 1, "margin": 0, "multiple": 0}


def test_research_never_changes_the_formula_case_or_the_hash(run, monkeypatch):
    before, case = run["content_hash"], json.dumps(run["long_term"], sort_keys=True, default=str)
    research.researched_case(run, {"growth": 1, "margin": 1, "multiple": 1})
    assert run["content_hash"] == before
    assert json.dumps(run["long_term"], sort_keys=True, default=str) == case


def test_an_unreadable_archive_says_how_to_fix_it(run):
    out = research.filing_research(run, [], "set SEC_USER_AGENT to 'Your Name you@example.com'")
    assert out["ok"] is False and "SEC_USER_AGENT" in out["error"]


def test_the_headline_tally_is_arithmetic_on_the_models_labels(run, monkeypatch):
    run["evidence"]["news"] = [{"date": "2026-09-14T12:00:00Z", "title": t} for t in
                               ("Test Corp beats a lawsuit from rival", "Market wrap: stocks mixed",
                                "Test Corp cuts guidance on weak demand", "Test Corp wins large contract")]
    reply = {"items": [
        {"n": 1, "about_company": True, "direction": "positive", "material": True, "why": "w"},
        {"n": 2, "about_company": False, "direction": "neutral", "material": False, "why": "w"},
        {"n": 3, "about_company": True, "direction": "negative", "material": True, "why": "w"},
        {"n": 4, "about_company": True, "direction": "positive", "material": True, "why": "w"},
        {"n": 99, "about_company": True, "direction": "positive", "material": True, "why": "invented"}]}
    monkeypatch.setattr(headless, "ask", lambda *a, **k: (True, json.dumps(reply)))
    out = research.headline_read(run)
    assert (out["positive"], out["negative"], out["material"]) == (2, 1, 3)
    assert out["tone"] == pytest.approx((2 - 1) / (3 + 2), abs=1e-3)
    assert all(i["n"] != 99 for i in out["items"]), "a headline the model invented is ignored"


def test_the_second_look_is_recorded_beside_the_call(run, monkeypatch):
    reply = {"stance": "wait", "concerns": [{"concern": "c", "based_on": "headline 1"}],
             "what_to_check": "x", "reason": "r"}
    monkeypatch.setattr(headless, "ask", lambda *a, **k: (True, json.dumps(reply)))
    out = research.second_look(run, "brief")
    assert out["stance"] == "wait" and out["against_call"] == run["swing"]["call"]
    assert "came from the formula" in out["note"]


def test_the_reports_print_the_research_layer_with_its_fences(run):
    from edgedesk import reports
    run["llm"] = {"ok": True, "synthesis": None,
                  "second_look": {"ok": True, "stance": "wait", "against_call": "Sell", "reason": "r",
                                  "concerns": [{"concern": "lawsuit pending", "based_on": "headline 1"}],
                                  "what_to_check": "the ruling date", "note": "n"},
                  "headline_read": {"ok": True, "headlines": 4, "material": 3, "positive": 2,
                                    "negative": 1, "tone": 0.2, "keyword_tone": 0.0, "note": "n",
                                    "items": [{"direction": "negative", "date": "2026-09-14",
                                               "title": "Test Corp cuts guidance", "why": "w"}]},
                  "filing_research": {"ok": False, "error": "set SEC_USER_AGENT to 'Your Name you@example.com'"}}
    for fmt in ("condensed", "full"):
        text = reports.render(run, fmt)
        assert "Research layer" in text and "WAIT" in text and "lawsuit pending" in text
        assert "Test Corp cuts guidance" in text and "SEC_USER_AGENT" in text
        assert "Nothing in this section changed a number above" in text


def test_a_reply_that_writes_plus_one_as_text_is_still_read(run, monkeypatch):
    reply = {"findings": [{"claim": "Revenue grew on Mobility bookings", "bearing": "growth",
                           "direction": "positive",
                           "quote": "driven primarily by growth in Mobility gross bookings"}],
             "adjustments": {"growth": "+1", "margin": "0", "multiple": "-1", "reason": "r"},
             "summary": "s"}
    monkeypatch.setattr(headless, "ask", lambda *a, **k: (True, json.dumps(reply)))
    out = research.filing_research(run, [FILING])
    assert out["ok"] and out["adjustments"]["growth"] == 1
    # "-1" was read as -1, then voided: a positive growth finding cannot lower the multiple (C5).
    assert out["adjustments"]["multiple"] == 0 and out["voided_adjustments"] == ["multiple"]


def test_a_year_at_the_end_of_a_sentence_is_not_an_unverified_number():
    excerpt = [{"text": "The presentation change anniversaries in the first quarter and revenue "
                        "comparisons normalize thereafter for the Mobility segment."}]
    data = {"findings": [{"claim": "The UK change anniversaries in 2027.", "bearing": "growth",
                          "direction": "positive",
                          "quote": "The presentation change anniversaries in the first quarter"}]}
    kept, dropped = research.verify_findings(data, excerpt)
    assert len(kept) == 1 and not dropped


# ---------------------------------------------------------------------------
# Stage B5 (R2-04, R2-13): the second look, the filing summary and the tally are checked
# ---------------------------------------------------------------------------

def _replies(monkeypatch, *replies):
    calls = []

    def ask(prompt, **k):
        calls.append(prompt)
        return True, json.dumps(replies[min(len(calls) - 1, len(replies) - 1)])

    monkeypatch.setattr(headless, "ask", ask)
    return calls


def _news(run, *titles):
    run["evidence"]["news"] = [{"date": "2026-09-14T12:00:00Z", "title": t} for t in titles]


def test_an_invented_figure_in_the_second_look_is_refused_after_one_repair(run, monkeypatch):
    """Codex's probe: 'Invented $987654 target' came back ok: true."""
    _news(run, "Test Corp wins large contract")
    bad = {"stance": "take", "concerns": [{"concern": "Invented $987654 target", "based_on": "headline 1"}],
           "what_to_check": "x", "reason": "r"}
    calls = _replies(monkeypatch, bad)
    out = research.second_look(run, "brief")
    assert out["ok"] is False and "987654" in out["error"] and len(calls) == 2
    assert "REJECTED" in calls[1]


def test_a_concern_must_cite_a_real_headline_or_the_evidence(run, monkeypatch):
    _news(run, "Test Corp wins large contract")
    for basis in ("a feeling", "headline 7"):
        bad = {"stance": "wait", "concerns": [{"concern": "c", "based_on": basis}],
               "what_to_check": "x", "reason": "r"}
        _replies(monkeypatch, bad)
        assert research.second_look(run, "brief")["ok"] is False, basis
    good = {"stance": "wait", "concerns": [{"concern": "c", "based_on": "evidence: RSI"}],
            "what_to_check": "x", "reason": "r"}
    calls = _replies(monkeypatch, bad, good)
    assert research.second_look(run, "brief")["ok"] is True and len(calls) == 2


def test_a_figure_from_a_headline_may_be_quoted(run, monkeypatch):
    _news(run, "Test Corp wins $750 million defense contract")
    reply = {"stance": "wait", "concerns": [{"concern": "The $750 million award may be priced in",
                                             "based_on": "headline 1"}],
             "what_to_check": "x", "reason": "r"}
    _replies(monkeypatch, reply)
    assert research.second_look(run, "brief")["ok"] is True


def test_a_filing_summary_with_an_unverified_figure_is_dropped(run, monkeypatch):
    finding = {"claim": "Revenue grew 18% on Mobility bookings", "bearing": "growth", "direction": "positive",
               "quote": "Revenue increased 18% year over year, driven primarily by growth in Mobility gross bookings"}
    adj = {"growth": 0, "margin": 0, "multiple": 0, "reason": "r"}
    _replies(monkeypatch, {"findings": [finding], "adjustments": adj,
                           "summary": "Revenue rose 18% and margins will reach 45%."})
    out = research.filing_research(run, [FILING])
    assert out["summary"] is None and "45%" in out["summary_dropped"]
    _replies(monkeypatch, {"findings": [finding], "adjustments": adj, "summary": "Revenue rose 18%."})
    out = research.filing_research(run, [FILING])
    assert out["summary"] == "Revenue rose 18%." and out["summary_dropped"] is None


def test_a_headline_labelled_twice_counts_once(run, monkeypatch):
    _news(run, "Test Corp cuts guidance", "Test Corp wins contract")
    item = {"n": 1, "about_company": True, "direction": "negative", "material": True, "why": "w"}
    _replies(monkeypatch, {"items": [item, item, item,
                                     {**item, "n": 2, "direction": "positive"}]})
    out = research.headline_read(run)
    assert (out["negative"], out["positive"], out["material"]) == (1, 1, 2)


def test_the_filing_error_names_what_actually_happened():
    """Stage B8: the message used to blame the contact email whatever the archive said."""
    from edgedesk.llm.analysis import filing_error
    run = {"ticker": "V", "as_of": "2026-09-24"}
    assert "HTTP 403" in filing_error([{"url": "u", "status": 403}], run)
    got = filing_error([{"url": "u", "status": 503}], run)
    assert "HTTP 503" in got and "User-Agent" not in got
    assert "timed out" in filing_error([{"url": "u", "why": "the request failed: timed out"}], run)
    assert "no 10-Q or 10-K is listed" in filing_error([], run)


def test_the_fetch_records_the_status_it_got(tmp_path, monkeypatch):
    from edgedesk.providers import edgar as edgar_mod

    class Resp:
        status_code, text = 503, ""

    class Edgar:
        _dir, _user_agent = tmp_path, "x"
        _session = type("S", (), {"get": lambda self, *a, **k: Resp()})()

    monkeypatch.setattr(edgar_mod._THROTTLE, "wait", lambda: None)
    problems = []
    assert filings._fetch_text(Edgar(), "http://u", "f.txt", problems) is None
    assert problems == [{"url": "http://u", "status": 503}]


def test_a_nudge_must_point_the_way_its_findings_do(run, monkeypatch):
    """Stage C5 (R2-14): a nudge only needed a surviving finding on the same topic, so a negative
    growth finding could license raising growth."""
    growth = {"claim": "Revenue grew 18% on Mobility bookings", "bearing": "growth",
              "quote": "Revenue increased 18% year over year, driven primarily by growth in Mobility gross bookings"}
    def research_with(direction, step):
        reply = {"findings": [{**growth, "direction": direction}],
                 "adjustments": {"growth": step, "margin": 0, "multiple": 0, "reason": "r"}, "summary": "s"}
        monkeypatch.setattr(headless, "ask", lambda *a, **k: (True, json.dumps(reply)))
        return research.filing_research(run, [FILING])
    out = research_with("negative", 1)
    assert out["adjustments"]["growth"] == 0 and out["voided_adjustments"] == ["growth"]
    assert research_with("positive", 1)["adjustments"]["growth"] == 1
    assert research_with("negative", -1)["adjustments"]["growth"] == -1
    assert research_with("neutral", 1)["adjustments"]["growth"] == 0



def test_a_reply_missing_a_field_gets_one_repair(run, monkeypatch):
    """AKAM 2026-10-05: the filing research lost its step to a reply missing `summary`."""
    finding = {"claim": "Revenue grew 18% on Mobility bookings", "bearing": "growth", "direction": "positive",
               "quote": "Revenue increased 18% year over year, driven primarily by growth in Mobility gross bookings"}
    adj = {"growth": 0, "margin": 0, "multiple": 0, "reason": "r"}
    calls = _replies(monkeypatch, {"findings": [finding], "adjustments": adj},
                     {"findings": [finding], "adjustments": adj, "summary": "Revenue rose 18%."})
    out = research.filing_research(run, [FILING])
    assert out["ok"] and out["summary"] == "Revenue rose 18%." and len(calls) == 2
    assert "REJECTED" in calls[1] and "summary" in calls[1]
