"""A synthetic run for screenshots and development: a fictitious company, invented numbers.

    python scripts/demo_seed.py --memos <folder>             write DEMO into that memo folder
    python scripts/demo_seed.py --memos <folder> --replace   overwrite an existing DEMO run

Never part of an install: a new install starts empty. Point the dashboard at the same folder with
DESK_MEMOS_DIR to see it. Nothing here is a real company, price, rating or analysis.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TICKER, DAY, CLOSE = "DEMO", "2026-10-02", 84.20


def bars() -> list[dict]:
    out, price, d = [], 70.0, date(2026, 4, 1)
    i = 0
    while d <= date.fromisoformat(DAY):
        if d.weekday() < 5:
            i += 1
            price = 70 + 0.075 * i + 6 * math.sin(i / 11) + 2.5 * math.sin(i / 3.7)
            out.append({"d": d.isoformat(), "c": round(price, 2)})
        d += timedelta(days=1)
    gap = CLOSE - out[-1]["c"]
    for k in range(1, 21):                       # ease the last 20 bars onto the close
        out[-k]["c"] = round(out[-k]["c"] + gap * (21 - k) / 20, 2)
    return out


def level(price: str, reason: str) -> dict:
    return {"price": price, "reason": reason}


def memo(headline, summary, action_note, reasons, risks, levels, watch, crux="") -> dict:
    return {"headline": headline, "summary": summary, "action_note": action_note,
            "valuation_view": "The price sits inside the own-history range and below the Street mean.",
            "key_reasons": reasons, "key_risks": risks, "levels": levels, "what_to_watch": watch,
            "disagreement_crux": crux, "super_manager_view": ""}


def bundle() -> dict:
    swing_levels = {"entry_zone": level("81.50 to 83.00", "the 20-day average and last week's base"),
                    "stop": level("78.40", "below the September low"),
                    "first_target": level("89.00", "the August high"),
                    "trim": level("92.50", "the top of the six-month range")}
    lt_levels = {"entry_zone": level("74 to 80", "the own-history fair value range"),
                 "stop": level("68", "a close under it breaks the long-term case"),
                 "first_target": level("96", "the Street mean target"),
                 "trim": level("110", "the high Street target")}
    horizons = {
        "swing": {
            "span": "2 days to 8 weeks", "voters": ["A", "B"],
            "restated": {"A": {"rating": "Overweight", "own": False, "rationale": "Price reclaimed the 50-day average on rising volume."},
                         "B": {"rating": "Hold", "own": False, "rationale": "Momentum is fine, but the move is already a month old."}},
            "debate": {"outcome": "agreed", "conceded": ["B"], "conceded_by": "B", "agreed_rating": "Overweight",
                       "ratings": {"A": "Overweight", "B": "Overweight"},
                       "turns": [
                           {"round": 1, "desk": "A", "decision": "defend", "rating": "Overweight", "argument":
                            "Volume on up days is 1.4x down days over 20 sessions, and the 50-day average turned up this week.",
                            "holes_in_other_report": ["Its Hold rests on the move being old, not on any sign of distribution."],
                            "new_evidence": [], "what_changed_my_mind": "", "note": "", "gives_up": []},
                           {"round": 1, "desk": "B", "decision": "concede", "rating": "Overweight", "argument":
                            "The volume evidence answers my concern; a month-old move with rising volume is not exhausted.",
                            "holes_in_other_report": [], "new_evidence": [],
                            "what_changed_my_mind": "Up-day volume at 1.4x down-day volume with the 50-day turning up.",
                            "note": "", "gives_up": ["The move is already a month old, so most of it is priced."]}]},
            "corrections": [{"desk": "B", "quote": "The move is already a month old, so most of it is priced.",
                             "status": "withdrawn", "reason": "Volume kept rising through the move.", "raised_by": "A", "round": 1}],
            "outcome": {"status": "agreed", "rating": "Overweight", "conviction": "Agreed after debate",
                        "how": "The Veterans conceded to the Quant desk after one round"},
            "action": "Start a starter position",
            "memo": memo("Demo Corp: a starter position on the reclaim of the 50-day",
                         "Synthetic example. The swing case rests on volume confirming the reclaim of the 50-day average.",
                         "Buy a third of a full position in the entry zone; add on a close above 89.",
                         ["Price reclaimed the 50-day average on rising volume", "Up-day volume is 1.4x down-day volume"],
                         ["Earnings fall inside the window", "A close under 78.40 voids the setup"],
                         swing_levels, ["A close under the 20-day average"])},
        "long_term": {
            "span": "1 year or more", "voters": ["A", "B", "C"],
            "restated": {"A": {"rating": "Hold", "own": False, "rationale": "Fair value, steady growth, nothing cheap about it."},
                         "B": {"rating": "Hold", "own": False, "rationale": "Quality is high; the price already reflects it."},
                         "C": {"rating": "Hold", "own": True, "timeframe": "1 year or more",
                               "rationale": "Edge Desk's business case: expected +8.6% a year against a 10% hurdle."}},
            "debate": None, "corrections": [],
            "outcome": {"status": "agreed", "rating": "Hold", "conviction": "Agreed before debate",
                        "how": "All three teams agreed at the first reading"},
            "action": "Stay out; keep on the watchlist with an entry level",
            "memo": memo("Demo Corp: a good business at a fair price",
                         "Synthetic example. All three teams see steady growth already priced in.",
                         "Wait for the entry zone; the business case improves below 80.",
                         ["Revenue compounding near 9% a year", "Expected return of 8.6% a year, under the 10% hurdle"],
                         ["Margins slipped two quarters running", "A richer multiple than its own history"],
                         lt_levels, ["Operating margin in the next two quarters"])},
    }
    valuation = {"pe_now": 21.4, "own_history": {"fair_low": 72.0, "fair_mid": 80.5, "fair_high": 91.0, "median_pe": 20.5},
                 "peg": {"fair_value": 88.0, "eps_cagr": 0.11, "capped": False},
                 "street": {"analysts": 12, "target_low": 70.0, "target_mean": 96.0, "target_high": 110.0}}
    anchors = {"as_of": DAY, "last_close": CLOSE, "sma_20": 82.1, "sma_50": 80.7, "high_52w": 92.5, "low_52w": 66.3}
    steps = [{"id": i, "name": n, "stage": s, "saved": True, "gist": g} for i, n, s, g in [
        ("evidence", "Evidence package", "Evidence", "250 daily bars, 12 filed quarters, data quality 94/100."),
        ("rating", "Factors and rating", "Rating", "Long-term business case Hold, expected +8.6% a year."),
        ("lens:quality_compounder", "Quality lens", "Written analysis", "High returns on capital, steady."),
        ("lens:deep_value", "Deep value lens", "Written analysis", "Not cheap on any measure."),
        ("lens:growth_at_reasonable_price", "Growth lens", "Written analysis", "Growth is priced fairly."),
        ("lens:trend_and_flow", "Trend lens", "Written analysis", "Trend turned up this week."),
        ("bull", "Bull case", "Written analysis", "Compounding at fair value."),
        ("bear", "Bear case", "Written analysis", "Margins are slipping."),
        ("synthesis", "Synthesis", "Written analysis", "Hold: a good business, fairly priced."),
        ("second_look", "Swing second look", "Research", "WAIT. Earnings fall inside the window."),
        ("headline_read", "Headline read", "Research", "3 material headlines: 2 positive, 1 negative."),
        ("filing_research", "Filing research", "Research", "Management cites pricing power.")]]
    return {
        "ticker": TICKER, "date": DAY, "generated_at": f"{DAY}T21:05:00Z", "owns": False,
        "engines": ["quant", "vets", "edge"], "mode": "debate", "model": "claude-opus-5-5",
        "price_session": DAY, "price_check": {"session": DAY, "consistent": True, "problem": None, "teams": {}},
        "quality": {"version": 1, "findings": [], "withheld": {"long_term": None, "swing": None}},
        "plan": {"name": "claude", "models": ["claude-opus-5-5", "claude-sonnet-5"]},
        "native": {"A": {"rating": "Overweight", "timeframe": "3-6 months"}, "B": {"rating": "Hold", "timeframe": None},
                   "C": {"rating": "Hold", "timeframe": "1 year or more"}},
        "horizons": horizons,
        "tradingagents": {"engine": "tradingagents", "rating": "Overweight", "reports": {
            "final_trade_decision": "**Rating**: Overweight\n\n**Executive Summary**: Synthetic example. Add on the reclaim of the 50-day.\n\n**Time Horizon**: 3-6 months",
            "investment_plan": "Synthetic plan: build in thirds.", "trader_investment_plan": "Synthetic: buy 81.50 to 83.00.",
            "market_report": "Synthetic market report.", "fundamentals_report": "Synthetic fundamentals report.",
            "news_report": "Synthetic news report.", "sentiment_report": "Synthetic sentiment report."}},
        "ai_hedge_fund": {"engine": "ai-hedge-fund", "rating": "Hold", "last_close": CLOSE, "anchors": anchors,
                          "valuation": valuation, "fundamentals": "Company: Demo Corp (synthetic)",
                          "verdict": {"rating": "Hold", "confidence": 62, "summary": "Synthetic: a good business at a fair price.",
                                      "thesis": "Synthetic thesis.", "bull_case": ["Steady growth"], "bear_case": ["Slipping margins"],
                                      "what_would_change": ["A price under 80"], "data_caveats": [], "headlines": [],
                                      "desk_score": 0.1, "desk_rating": "Hold"},
                          "personas": [{"model": m, "call": c, "confidence": 60, "reasoning": "Synthetic."}
                                       for m, c in (("buffett", "bullish"), ("graham", "neutral"), ("munger", "bullish"),
                                                    ("lynch", "neutral"), ("druckenmiller", "bearish"))]},
        "edge_desk": {"engine": "edge-desk", "rating": "Hold", "rating_basis": "business case",
                      "expected_annual_return": 0.086, "screen_rating": "Hold", "score": 58.0, "last_close": CLOSE,
                      "anchors": anchors, "valuation": valuation, "steps": steps, "caveats": [],
                      "swing": {"signal": "Buy", "why": "Synthetic: reclaim of the 50-day average."},
                      "report_md": "# Demo Corp (synthetic)\n\nEvery figure in this report is invented."},
        "costs": {"total": 0.0, "billing": "max", "notional": 3.10, "desk": 0.0},
    }


def earlier_demo() -> dict:
    """DEMO a week earlier, so the history table and the weekly arrows have something to show."""
    b = bundle()
    b["date"], b["generated_at"] = "2026-09-25", "2026-09-25T21:10:00Z"
    b["ai_hedge_fund"]["last_close"] = b["edge_desk"]["last_close"] = 79.10
    sw = b["horizons"]["swing"]
    sw["debate"], sw["corrections"] = None, []
    sw["restated"]["A"]["rating"] = "Hold"
    sw["outcome"] = {"status": "agreed", "rating": "Hold", "conviction": "Agreed before debate",
                     "how": "Both teams agreed at the first reading"}
    sw["action"] = "Stay out; keep on the watchlist with an entry level"
    return b


def stale_sample() -> dict:
    """A second company whose long term is withheld on a stale filing, so the warnings show."""
    b = bundle()
    b["ticker"], b["date"], b["generated_at"] = "SMPL", "2026-10-01", "2026-10-01T21:20:00Z"
    reason = ("the latest filing is not in SEC's financial-data feed yet, so the fundamentals are a "
              "quarter stale")
    b["quality"] = {"version": 1, "findings": [{"team": "C", "code": "companyfacts_stale", "text": "synthetic"}],
                    "withheld": {"long_term": {"codes": ["companyfacts_stale"], "teams": ["B", "C"],
                                               "reason": reason}, "swing": None}}
    lt = b["horizons"]["long_term"]
    lt.update({"restated": {}, "voters": [], "debate": None, "corrections": [],
               "outcome": {"status": "withheld", "rating": None, "conviction": "Withheld",
                           "how": f"Withheld for the whole desk: {reason}"},
               "action": "Withheld: no new position on this horizon until the filing reaches the data feed",
               "memo": memo("Long term (1+ years): no rating, the data is a quarter stale",
                            "Synthetic example. Every team still ran, but no combined long-term rating is "
                            "published on superseded fundamentals.", "", [], [], {},
                            ["SEC's data feed picking up the latest filing"])})
    b["edge_desk"]["rating"] = None
    b["edge_desk"]["caveats"] = [{"code": "companyfacts_stale", "severity": "withhold", "message": "synthetic"}]
    b["ai_hedge_fund"]["verdict"]["data_caveats"] = ["Fundamentals are a quarter stale (synthetic)."]
    return b


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--memos", required=True, help="memo folder to write into (use a throwaway one)")
    ap.add_argument("--replace", action="store_true", help="overwrite an existing DEMO run")
    args = ap.parse_args()
    memos = Path(args.memos)
    target = memos / f"{TICKER}-{DAY}.json"
    if target.exists() and not args.replace:
        sys.exit(f"{target} exists; pass --replace to overwrite it")
    for run in (bundle(), earlier_demo(), stale_sample()):
        stem = f"{run['ticker']}-{run['date']}"
        run_dir = memos / "runs" / stem
        run_dir.mkdir(parents=True, exist_ok=True)
        (memos / f"{stem}.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
        (memos / f"{stem}.md").write_text(f"# {run['ticker']} desk memo (synthetic)\n\nEvery figure here is invented.\n",
                                          encoding="utf-8")
        (run_dir / "bars.json").write_text(json.dumps([x for x in bars() if x["d"] <= run["date"]]),
                                           encoding="utf-8")
    profile = ROOT / "dashboard" / "cache" / "profiles" / f"{TICKER}.json"
    profile.parent.mkdir(parents=True, exist_ok=True)
    (profile.parent / "SMPL.json").write_text(json.dumps({
        "ticker": "SMPL", "name": "Sample Industries (synthetic)",
        "summary": "A fictitious company used for screenshots.", "fetched_at": "2099-01-01T00:00:00Z"}),
        encoding="utf-8")
    profile.write_text(json.dumps({
        "ticker": TICKER, "name": "Demo Corp (synthetic)", "sector": "Technology", "industry": "Software",
        "hq": "Example City", "employees": 4200, "website": "https://example.com",
        "summary": "A fictitious company used for screenshots. Every figure on this page is invented.",
        "market_cap": 18.4e9, "pe_trailing": 21.4, "pe_forward": 19.0, "dividend_yield": None,
        "beta": 1.05, "avg_volume": 2.1e6, "high_52w": 92.5, "low_52w": 66.3, "price": CLOSE,
        "next_earnings": "2026-10-29", "earnings_estimated": False, "exchange": "NASDAQ",
        "fetched_at": "2099-01-01T00:00:00Z"}), encoding="utf-8")
    print(f"Synthetic runs written to {memos}: DEMO (two dates) and SMPL")


if __name__ == "__main__":
    main()
