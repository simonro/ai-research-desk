"""The trade simulator: every rule errs against the strategy, and each is pinned here."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from edgedesk.research import evaluate
from edgedesk.research.simulate import Rejected, Signal, Trade, benchmark_return, simulate


def bars(rows, start=date(2025, 1, 6)):
    """rows: (open, high, low, close). Consecutive calendar days are fine here."""
    return [{"date": (start + timedelta(days=i)).isoformat(), "open": o, "high": h,
             "low": lo, "close": c, "volume": 1000} for i, (o, h, lo, c) in enumerate(rows)]


FLAT = (100, 101, 99, 100)


def test_entry_is_the_next_open_never_the_signal_close():
    b = bars([FLAT, (104, 106, 103, 105), (105, 107, 104, 106)])
    t = simulate(Signal("X", b[0]["date"], stop=90, max_hold=2, setup="s"), b, cost_bps=0)
    assert isinstance(t, Trade)
    assert t.entry == 104 and t.entry_date == b[1]["date"]
    assert t.exit == 106 and t.exit_reason == "time" and t.sessions == 2


def test_a_gap_through_the_stop_fills_at_the_open_not_at_the_stop():
    b = bars([FLAT, (100, 101, 99, 100), (80, 82, 78, 81)])
    t = simulate(Signal("X", b[0]["date"], stop=95, max_hold=10, setup="s"), b, cost_bps=0)
    assert t.exit_reason == "gap_stop" and t.exit == 80
    assert t.r_multiple == pytest.approx(-4.0)        # four times the planned loss


def test_an_intraday_touch_fills_at_the_stop_even_on_the_entry_day():
    b = bars([FLAT, (100, 101, 94, 99)])
    t = simulate(Signal("X", b[0]["date"], stop=95, max_hold=10, setup="s"), b, cost_bps=0)
    assert t.exit_reason == "stop" and t.exit == 95 and t.sessions == 1
    assert t.r_multiple == pytest.approx(-1.0)


def test_an_open_below_the_stop_means_no_trade():
    b = bars([FLAT, (94, 96, 93, 95)])
    r = simulate(Signal("X", b[0]["date"], stop=95, max_hold=10, setup="s"), b)
    assert isinstance(r, Rejected) and "dead before entry" in r.why


def test_a_rule_exit_is_read_at_the_close_and_taken_at_the_next_open():
    b = bars([FLAT, (100, 102, 99, 101), (101, 103, 100, 102), (97, 98, 96, 97)])
    rule = lambda bs, entry_i, i: bs[i]["close"] >= 102        # noqa: E731
    t = simulate(Signal("X", b[0]["date"], stop=90, max_hold=10, setup="s"), b, rule, cost_bps=0)
    assert t.exit_reason == "rule" and t.exit == 97 and t.exit_date == b[3]["date"]


def test_the_trade_is_flat_by_the_close_before_the_next_report():
    b = bars([FLAT, FLAT, (100, 105, 99, 104), (120, 125, 119, 124)])
    t = simulate(Signal("X", b[0]["date"], stop=90, max_hold=10, setup="s",
                        exit_before=b[3]["date"]), b, cost_bps=0)
    assert t.exit_reason == "event" and t.exit == 104 and t.exit_date == b[2]["date"]


def test_costs_are_charged_on_both_sides():
    b = bars([FLAT, (100, 101, 99, 100), (100, 101, 99, 100)])
    t = simulate(Signal("X", b[0]["date"], stop=90, max_hold=2, setup="s"), b, cost_bps=5)
    assert t.gross == 0 and t.net == pytest.approx(-0.001, abs=1e-5)


def test_the_benchmark_is_held_over_the_same_sessions():
    b = bars([FLAT, (100, 101, 99, 100), (100, 111, 99, 110)])
    spy = bars([(50, 50, 50, 50), (50, 51, 50, 51), (51, 53, 51, 52)])
    t = simulate(Signal("X", b[0]["date"], stop=90, max_hold=2, setup="s"), b, cost_bps=0)
    assert benchmark_return(t, spy) == pytest.approx(52 / 50 - 1)


def test_a_second_signal_while_the_first_is_open_is_not_a_second_trade():
    b = bars([FLAT] * 8)
    sigs = [Signal("X", b[0]["date"], 90, 5, "s"), Signal("X", b[2]["date"], 90, 5, "s")]
    rows, rejected = evaluate.run(sigs, {"X": b}, b)
    assert len(rows) == 1 and rejected[0].why == "already in a trade"


def test_development_trades_must_close_before_the_holdout_begins():
    rows = [{"entry_date": "2024-11-01", "exit_date": "2024-12-20"},
            {"entry_date": "2024-12-15", "exit_date": "2025-01-20"},     # straddles: neither
            {"entry_date": "2025-01-10", "exit_date": "2025-02-10"}]
    dev, held = evaluate.split(rows, "2025-01-01")
    assert len(dev) == 1 and len(held) == 1


def test_a_setup_carried_by_one_ticker_fails_the_verdict():
    def row(ticker, month, excess):
        return {"ticker": ticker, "entry_date": f"{month}-05", "exit_date": f"{month}-25",
                "net": excess, "excess": excess, "r_multiple": 1.0, "sessions": 10,
                "exit_reason": "time"}
    months = [f"{y}-{m:02d}" for y in (2022, 2023, 2024) for m in range(1, 13)]
    rows = [row("STAR", m, 0.30) for m in months]
    for t in ("A", "B", "C"):
        rows += [row(t, m, -0.02) for m in months]
    summary = evaluate.summarize(rows)
    loo = evaluate.leave_one_ticker_out(rows)
    assert summary["mean_excess"] > 0
    assert loo["min"] < 0 and loo["min_without"] == "STAR"
    assert not evaluate.verdict(summary, {"mean_excess": 0.0}, loo)["passed"]


# ---------------------------------------------------------------------------
# S1: post-earnings drift
# ---------------------------------------------------------------------------

def _series(n=30):
    rows = [(100, 101, 99, 100)] * n
    b = bars(rows)
    for x in b:
        x["volume"] = 1000
    return b


def test_a_strong_reaction_fires_with_the_stop_frozen_at_its_low():
    from edgedesk.research import pead
    b = _series()
    b[25].update(open=104, high=110, low=103, close=109, volume=5000)
    spy = {x["date"]: 50.0 for x in b}
    events = [{"accepted": "a", "filed": b[24]["date"], "after_close": True},
              {"accepted": "b", "filed": b[29]["date"], "after_close": True}]
    [sig] = pead.signals("X", b, spy, events[:1] + events[1:])
    assert sig.date == b[25]["date"] and sig.stop == 103
    assert sig.exit_before == b[29]["date"]
    assert sig.meta["abnormal"] == pytest.approx(0.09) and sig.meta["rvol"] == 5.0


def test_a_report_filed_before_the_close_reacts_the_same_session():
    from edgedesk.research.data import reaction_index
    b = _series()
    same = reaction_index(b, {"filed": b[10]["date"], "after_close": False})
    nxt = reaction_index(b, {"filed": b[10]["date"], "after_close": True})
    assert (same, nxt) == (10, 11)


@pytest.mark.parametrize("change", [
    dict(close=101.5, high=110, low=100),              # abnormal return too small
    dict(close=104, high=112, low=103),                # closed in the bottom of its range
    dict(close=109, high=110, low=103, volume=1500),   # no real volume behind it
])
def test_a_weak_reaction_does_not_fire(change):
    from edgedesk.research import pead
    b = _series()
    b[25].update({"open": 104, "volume": 5000, **change})
    spy = {x["date"]: 50.0 for x in b}
    events = [{"accepted": "a", "filed": b[24]["date"], "after_close": True}]
    assert pead.signals("X", b, spy, events) == []


def test_an_amended_report_days_later_is_not_a_second_report():
    from edgedesk.research.runner import distinct_reports
    events = [{"filed": "2025-01-28"}, {"filed": "2025-02-03"}, {"filed": "2025-04-29"}]
    assert [e["filed"] for e in distinct_reports(events)] == ["2025-01-28", "2025-04-29"]


# ---------------------------------------------------------------------------
# S2: a pullback inside leadership
# ---------------------------------------------------------------------------

def _uptrend_with_pullback(n=300):
    """A steady climb, a quiet three-session dip that holds the 50, then a reclaim."""
    rows, price = [], 50.0
    for i in range(n):
        price *= 1.003
        rows.append((price * 0.998, price * 1.006, price * 0.994, price))
    b = bars(rows, start=date(2023, 1, 2))
    for x in b:
        x["volume"] = 1000
    peak = b[-6]["close"]
    for k, c in zip(range(-5, -1), (peak * 0.992, peak * 0.984, peak * 0.979, peak * 0.981)):
        b[k].update(open=c * 1.002, high=c * 1.004, low=c * 0.996, close=c, volume=600)
    b[-1].update(open=peak * 0.985, high=peak * 1.0, low=peak * 0.984, close=peak * 0.998,
                 volume=900)
    return b


def test_a_quiet_pullback_in_a_leader_fires_on_the_reclaim_with_the_stop_under_the_low():
    from edgedesk.research import pullback
    b = _uptrend_with_pullback()
    laggard = bars([(100, 101, 99, 100)] * 300, start=date(2023, 1, 2))
    sector = {x["date"]: 100.0 for x in b}
    fn = pullback.make_signal_fn({"LEAD": b, "LAG1": laggard, "LAG2": laggard},
                                 {"LEAD": "tech"}, {"tech": sector})
    sigs = [s for s in fn("LEAD", b, {}, [], pullback.PRIMARY) if s.date == b[-1]["date"]]
    assert len(sigs) == 1
    low = min(x["low"] for x in b[-5:-1])
    assert sigs[0].stop < low and sigs[0].meta["momentum_pct"] == 1.0
    assert 1.0 <= sigs[0].meta["depth_atr"] <= 2.5


def test_the_same_pullback_in_a_laggard_does_not_fire_but_the_control_does():
    from edgedesk.research import pullback
    b = _uptrend_with_pullback()
    leader = bars([(p, p * 1.01, p * 0.99, p) for p in (50 * 1.01 ** i for i in range(300))],
                  start=date(2023, 1, 2))
    sector = {x["date"]: 100.0 for x in b}
    fn = pullback.make_signal_fn({"X": b, "L1": leader, "L2": leader, "L3": leader},
                                 {"X": "tech"}, {"tech": sector})
    last = b[-1]["date"]
    assert not [s for s in fn("X", b, {}, [], pullback.PRIMARY) if s.date == last]
    control = pullback.VARIANTS[1]
    assert [s for s in fn("X", b, {}, [], control) if s.date == last]


def test_the_exit_rule_is_a_close_under_the_fifty_session_average():
    from edgedesk.research import pullback
    b = bars([(100, 101, 99, 100)] * 60)
    assert not pullback.exit_rule(b, 50, 59)
    b[59]["close"] = 95
    assert pullback.exit_rule(b, 50, 59)


# ---------------------------------------------------------------------------
# R2-18: a development outcome must have closed before the holdout begins
# ---------------------------------------------------------------------------

def test_development_keeps_only_outcomes_that_closed_before_the_holdout():
    from edgedesk.calibrate.run import split
    rows = [
        {"ticker": "A", "as_of": "2025-12-15", "horizon": "long_term",
         "outcomes": [{"window": "3m", "bars": 63, "end": "2026-03-17"}]},          # crosses: dropped
        {"ticker": "A", "as_of": "2025-06-02", "horizon": "long_term",
         "outcomes": [{"window": "3m", "bars": 63, "end": "2025-08-29"},              # kept
                      {"window": "12m", "bars": 252, "end": "2026-06-01"}]},         # crosses: dropped
        {"ticker": "B", "as_of": "2026-01-05", "horizon": "swing",
         "outcomes": [{"window": "10d", "bars": 10, "end": "2026-01-20"}]},          # held out
    ]
    dev, held, purge = split(rows, "2026-01-02")
    assert [(r["as_of"], [o["window"] for o in r["outcomes"]]) for r in dev] == [("2025-06-02", ["3m"])]
    assert [r["as_of"] for r in held] == ["2026-01-05"]
    assert purge["dropped_windows"] == 2 and purge["dropped_rows"] == 1
    assert purge["dev_tickers"] == 1 and purge["holdout_tickers"] == 1


def test_a_saved_sample_without_end_dates_is_purged_late_not_early():
    """Samples saved before the fix recorded only the bar count; the estimate errs late."""
    from edgedesk.calibrate.run import outcome_end
    row = {"as_of": "2025-01-02"}
    end = outcome_end(row, {"window": "12m", "bars": 252})
    assert end >= "2026-01-02"          # 252 trading days from Jan 2 2025 really ends early Jan 2026
    assert outcome_end(row, {"window": "10d", "bars": 10, "end": "2025-01-16"}) == "2025-01-16"
