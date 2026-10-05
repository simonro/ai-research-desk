"""The weekly rhythm: memory between runs, the universe, and the event check."""

from __future__ import annotations

from datetime import date

import pytest

from edgedesk import changes, run as run_mod
from edgedesk.jobs import events, universe, week
from tests.conftest import FakeClient, make_bars, make_metrics

AS_OF = date(2026, 9, 15)
EARLIER = date(2026, 9, 8)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("edgedesk.paths.RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr("edgedesk.paths.ESTIMATES_DIR", tmp_path / "estimates")
    monkeypatch.setattr("edgedesk.paths.UNIVERSE_PATH", tmp_path / "universe.yaml")
    return tmp_path


# ---------------------------------------------------------------------------
# Memory between runs
# ---------------------------------------------------------------------------

def test_the_first_run_says_so_instead_of_showing_an_empty_comparison(home):
    run = run_mod.analyze("TEST", AS_OF, client=FakeClient())
    changed = changes.what_changed(run)
    assert changed["first_run"] is True
    assert "starts next week" in changed["summary"]


def test_a_rating_change_is_reported_with_both_ends(home):
    run_mod.analyze("TEST", EARLIER, client=FakeClient())
    weak = FakeClient(metrics=[make_metrics(operating_margin=0.01, net_margin=0.002,
                                            return_on_equity=0.01, revenue_growth=-0.2,
                                            earnings_growth=-0.4,
                                            free_cash_flow_growth=-0.5)])
    run = run_mod.analyze("TEST", AS_OF, client=weak)
    changed = changes.what_changed(run)
    assert changed["first_run"] is False
    assert changed["since"] == EARLIER.isoformat()
    moved = {r["horizon"] for r in changed["ratings"]}
    assert moved, "a large fundamental deterioration should move at least one rating"
    for row in changed["ratings"]:
        assert row["from"] and row["to"] and row["from"] != row["to"]


def test_a_quiet_week_moves_no_rating(home):
    """Both dates historical, so neither sees current consensus and the only
    difference is a week of bars. A rating that moves on that is too twitchy."""
    run_mod.analyze("TEST", date(2026, 9, 1), client=FakeClient(consensus=False))
    run = run_mod.analyze("TEST", date(2026, 9, 8), client=FakeClient(consensus=False))
    changed = changes.what_changed(run)
    assert changed["ratings"] == []


def test_a_new_data_caveat_is_reported(home):
    run_mod.analyze("TEST", EARLIER, client=FakeClient())
    run = run_mod.analyze("TEST", AS_OF, client=FakeClient(consensus=False))
    changed = changes.what_changed(run)
    assert any("no_consensus" in note for note in changed["notes"])


# ---------------------------------------------------------------------------
# Thesis health
# ---------------------------------------------------------------------------

def test_thesis_health_waits_for_a_second_run(home):
    run = run_mod.analyze("TEST", AS_OF, client=FakeClient())
    health = changes.thesis_health(run)
    assert health["first_run"] is True
    assert "starts from the next run" in health["summary"]


def _quarters(*overrides):
    """Newest first, one metrics row per filing, as the client returns them."""
    return FakeClient(metrics=[make_metrics(**o) for o in overrides])


def test_conditions_that_still_hold_are_reported_as_holding(home):
    run_mod.analyze("TEST", EARLIER, client=_quarters({}, {}))
    run = run_mod.analyze("TEST", AS_OF, client=_quarters({}, {}))
    health = changes.thesis_health(run)
    assert health["first_run"] is False
    assert health["broken"] == 0
    assert "still hold" in health["summary"]
    assert all(c["status"] == "holding" for c in health["conditions"])


def test_a_broken_condition_is_caught_against_the_entry_snapshot(home):
    """The conditions were written from the entry run's own numbers, so a real
    deterioration has to trip them."""
    run_mod.analyze("TEST", EARLIER, client=_quarters({}, {}))
    bad = dict(operating_margin=0.01, revenue_growth=-0.15, return_on_equity=0.02)
    run = run_mod.analyze("TEST", AS_OF, client=_quarters(bad, bad))
    health = changes.thesis_health(run)
    assert health["broken"] >= 2
    broken = {c["metric"] for c in health["conditions"] if c["status"] == "broken"}
    assert "Operating margin" in broken and "Revenue growth" in broken
    assert "conditions broken" in health["summary"]


def test_one_weak_quarter_is_a_warning_not_a_break(home):
    """The condition says two consecutive quarters. One is a warning."""
    run_mod.analyze("TEST", EARLIER, client=_quarters({}, {}))
    run = run_mod.analyze("TEST", AS_OF, client=_quarters(
        dict(operating_margin=0.01), dict(operating_margin=0.25)))
    health = changes.thesis_health(run)
    margin = next(c for c in health["conditions"] if c["metric"] == "Operating margin")
    assert margin["status"] == "warning"
    assert margin["quarters_required"] == 2
    assert health["broken"] == 0 and health["warnings"] >= 1


def test_a_missing_reading_is_unknown_and_never_summarised_as_healthy(home):
    run_mod.analyze("TEST", EARLIER, client=_quarters({}, {}))
    run = run_mod.analyze("TEST", AS_OF, client=_quarters(
        dict(operating_margin=None), dict(operating_margin=None)))
    health = changes.thesis_health(run)
    margin = next(c for c in health["conditions"] if c["metric"] == "Operating margin")
    assert margin["status"] == "unknown"
    assert health["unknown"] >= 1
    assert "still hold" not in health["summary"]
    assert "could not be checked" in health["summary"]


def test_the_entry_anchor_can_be_a_recorded_purchase_date_not_the_first_run(home):
    run_mod.analyze("TEST", date(2026, 8, 1), client=_quarters({}, {}))     # a watchlist scan
    run_mod.analyze("TEST", EARLIER, client=_quarters({}, {}))
    run = run_mod.analyze("TEST", AS_OF, client=_quarters({}, {}))
    assert changes.thesis_health(run)["entry_date"] == "2026-08-01"
    bought = changes.thesis_health(run, since=EARLIER.isoformat())
    assert bought["entry_date"] == EARLIER.isoformat()
    assert "owned since" in bought["entry_basis"]


def test_thesis_health_measures_from_the_entry_price(home):
    run_mod.analyze("TEST", EARLIER, client=FakeClient())
    run = run_mod.analyze("TEST", AS_OF, client=FakeClient())
    health = changes.thesis_health(run)
    assert health["entry_date"] == EARLIER.isoformat()
    assert health["since_entry"] is not None


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------

def test_the_universe_file_is_created_with_an_explanation(home):
    loaded = universe.load()
    assert loaded == {"owned": [], "watchlist": [], "large_cap": [], "owned_since": {}}
    text = (home / "universe.yaml").read_text(encoding="utf-8")
    assert "owned" in text and "watchlist" in text and "large_cap" in text
    assert text.lstrip().startswith("#"), "the file should explain itself"


def test_a_name_on_both_lists_is_researched_once_and_counted_as_held(home):
    (home / "universe.yaml").write_text(
        "owned: [MSFT]\nwatchlist: [MSFT, NVDA]\nlarge_cap: []\n", encoding="utf-8")
    loaded = universe.load()
    assert universe.researched(loaded) == ["MSFT", "NVDA"]
    assert loaded["owned"] == ["MSFT"]


def test_tickers_are_normalized(home):
    (home / "universe.yaml").write_text(
        "owned: [' msft ', nvda, MSFT]\nwatchlist: []\nlarge_cap: []\n", encoding="utf-8")
    assert universe.load()["owned"] == ["MSFT", "NVDA"]


# ---------------------------------------------------------------------------
# Event check
# ---------------------------------------------------------------------------

def test_a_name_never_analyzed_is_flagged(home):
    flagged = events.check(["TEST"], AS_OF, client=FakeClient(), say=None)
    assert flagged[0]["reasons"] == ["never analyzed"]


def test_nothing_is_flagged_when_the_last_run_is_current(home):
    run_mod.analyze("TEST", AS_OF, client=FakeClient())
    assert events.check(["TEST"], AS_OF, client=FakeClient(), say=None) == []


def test_a_new_earnings_release_is_a_reason(home):
    """The stored run has no release; the client reports one in the window."""
    run_mod.analyze("TEST", date(2026, 6, 30), client=FakeClient(release=False))
    stored = run_mod.read("TEST", "2026-06-30")
    reasons = events._reasons("TEST", stored, AS_OF, FakeClient())
    assert any("earnings were reported" in r for r in reasons)


def test_an_ordinary_price_move_is_not_a_reason(home):
    """A week of normal drift is not news, and the bar scales with the stock's
    own volatility so a quiet name and a violent one are judged differently."""
    run_mod.analyze("TEST", EARLIER, client=FakeClient())
    stored = run_mod.read("TEST", EARLIER.isoformat())
    reasons = events._price_reason("TEST", stored, EARLIER, AS_OF, FakeClient())
    assert reasons == []


def test_a_violent_price_move_is_a_reason(home):
    run_mod.analyze("TEST", EARLIER, client=FakeClient())
    stored = run_mod.read("TEST", EARLIER.isoformat())
    crashed = FakeClient(bars=make_bars(400, start_price=100.0, drift=-0.02))
    reasons = events._price_reason("TEST", stored, EARLIER, AS_OF, crashed)
    assert reasons and "price moved" in reasons[0]


def test_the_headline_filter_is_narrow_enough_to_mean_something():
    noise = ["Broadcom Announces Quarterly Dividend",
             "Analyst Maintains Buy Rating On Broadcom",
             "Broadcom Shares Are Trading Higher Today"]
    signal = ["Broadcom Cuts Outlook For The Year",
              "Broadcom CEO Steps Down",
              "SEC Opens Investigation Into Broadcom Accounting",
              "Broadcom To Acquire Rival For $10B"]
    assert not any(events._HEADLINES.search(h) for h in noise)
    assert all(events._HEADLINES.search(h) for h in signal)


# ---------------------------------------------------------------------------
# The weekly summary
# ---------------------------------------------------------------------------

def test_the_summary_names_what_is_actionable_and_what_moved(home):
    runs = {}
    for ticker in ("TEST", "OTHER"):
        run = run_mod.analyze(ticker, AS_OF, client=FakeClient(), save=False)
        run["changes"] = changes.what_changed(run)
        runs[ticker] = run
    text = week.render_summary(runs, AS_OF, owned={"TEST"})
    assert "# Week of 2026-09-15" in text
    assert "TEST (held)" in text
    assert "## Actionable now" in text
    assert "## Rating changes" in text
    assert "Research only" in text


def test_a_withheld_name_is_listed_separately_with_its_reason(home):
    run = run_mod.analyze("TEST", AS_OF, client=FakeClient(metrics=[]), save=False)
    text = week.render_summary({"TEST": run}, AS_OF, owned=set())
    assert "## Withheld" in text
    assert "US GAAP" in text


# ---------------------------------------------------------------------------
# The retired swing rating must not reach any published path
# ---------------------------------------------------------------------------

def test_a_stored_swing_rating_never_reaches_a_summary_a_brief_or_a_change_list(home):
    from edgedesk.llm import prompts
    from edgedesk.reports import vault as vault_mod

    def planted(as_of_label):
        run = run_mod.analyze("TEST", AS_OF, client=FakeClient(), save=False)
        run["verdicts"]["swing"].update(rating="ZZSWINGRATING", score=12.34,
                                        conviction="ZZSWINGCONV", action="ZZSWINGACTION")
        run["as_of"] = as_of_label
        return run

    now, before = planted("2026-09-15"), planted("2026-09-08")
    before["verdicts"]["swing"]["rating"] = "ZZOTHERRATING"     # a change that must be invisible
    now["changes"] = changes.what_changed(now, previous=before)
    assert not [c for c in now["changes"]["ratings"] if c["horizon"] == "swing"]

    surfaces = [
        week.render_summary({"TEST": now}, AS_OF, owned=set()),
        week._one_line(now),
        prompts.evidence_brief(now),
        vault_mod.history_row(now),
        vault_mod.frontmatter(now),
    ]
    for text in surfaces:
        for marker in ("ZZSWING", "12.34", "ZZOTHER"):
            assert marker not in text, marker
