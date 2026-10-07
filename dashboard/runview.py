"""Turn a finished desk run into the one JSON document the dashboard draws.

Inputs, all written by `python -m desk TICKER`:
    memos/TICKER-DATE.json                  the day's latest memo bundle (horizons, debate, teams)
    memos/runs/TICKER-DATE-RUN/memo.json    each run's own bundle, kept when a later run that day
                                            becomes the latest (runs before 2026-10-07 have no RUN
                                            and their folder is memos/runs/TICKER-DATE)
    memos/runs/<run folder>/events.jsonl    timings, when the run was live (absent on old runs)
    memos/runs/<run folder>/*.json          each engine's own report
Bars for the chart come from Alpaca REST once and are cached in the run folder.

Nothing here writes to the desk's files except that bars cache. Text is the desk's own, with
one substitution: the memo calls the teams "Desk A" and "Desk B", the dashboard calls them the
Quant desk and the Veterans.
"""

from __future__ import annotations

import json
import re
import os
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desk"))
from desk.gist import gist  # noqa: E402  (one summary rule, live and afterwards)
import validate  # noqa: E402

MEMOS = Path(os.environ.get("DESK_MEMOS_DIR") or ROOT / "memos")
HEDGE_ENV = Path.home() / ".hedge-fund" / ".env"
DESK_ENV = Path.home() / ".hedge-desk" / ".env"

# "as_they_ran" was retired on 2026-09-24; it stays here so runs saved before then still open.
# The subtitles are what those runs were asked (swing was 2 to 6 weeks); a run saved since then
# carries the window it was asked for as `span`, and that wins.
HORIZON_ORDER = ["as_they_ran", "swing", "long_term"]
HORIZON = {"as_they_ran": ("As they ran", "Each team's own call, on its own clock"),
           "swing": ("Swing", "2 to 6 weeks"),
           "long_term": ("Long term", "1 year or more")}
TEAM = {"A": "quant", "B": "vets", "C": "edge"}

# TradingAgents, in the order its graph runs. `step` is what the runner's events carry.
QUANT = [
    ("market", "Market analyst", "Analysts", "market_report", "market_report"),
    ("sentiment", "Sentiment analyst", "Analysts", "sentiment_report", "sentiment_report"),
    ("news", "News analyst", "Analysts", "news_report", "news_report"),
    ("fund", "Fundamentals analyst", "Analysts", "fundamentals_report", "fundamentals_report"),
    ("bull", "Bull researcher", "Research", "bull_report", "bull_history"),
    ("bear", "Bear researcher", "Research", "bear_report", "bear_history"),
    ("rm", "Research manager", "Research", "investment_plan", "investment_plan"),
    ("trader", "Trader", "Trading", "trader_investment_plan", "trader_investment_plan"),
    ("agg", "Aggressive analyst", "Risk", "risk_aggressive", "current_aggressive_response"),
    ("con", "Conservative analyst", "Risk", "risk_conservative", "current_conservative_response"),
    ("neu", "Neutral analyst", "Risk", "risk_neutral", "current_neutral_response"),
    ("pm", "Portfolio manager", "Decision", "final_trade_decision", "final_trade_decision"),
]
# ai-hedge-fund's members, keyed by the model name its runner puts in `step`.
VETS = [
    ("pead", "Earnings drift", "Signal models"),
    ("analyst_consensus", "Analyst consensus", "Signal models"),
    ("buffett", "Buffett", "Investors"),
    ("munger", "Munger", "Investors"),
    ("graham", "Graham", "Investors"),
    ("lynch", "Lynch", "Investors"),
    ("druckenmiller", "Druckenmiller", "Investors"),
    ("research_manager", "Research manager", "Decision"),
]
LEVEL_LABEL = {"entry_zone": "Entry", "stop": "Stop", "first_target": "First target", "trim": "Trim"}



def labelled(text: str, label: str) -> str:
    """The first sentence after a **Label**: line, e.g. TradingAgents' Executive Summary."""
    m = re.search(rf"\*\*{label}\*\*:?\s*(.+)", text or "", re.I)
    return first_sentence(m.group(1).replace("**", "")) if m else ""


def first_sentence(text: str) -> str:
    m = re.match(r"(.+?[.!?])(\s|$)", (text or "").strip())
    return m.group(1) if m else (text or "").strip()


def teams(text):
    """Desk A / Desk B -> the Quant desk / the Veterans, capitalised at a sentence start."""
    if isinstance(text, list):
        return [teams(t) for t in text]
    if not isinstance(text, str):
        return text
    text = re.sub(r"\bDesk B's\b", "Desk B'", text)          # "the Veterans'" once renamed
    text = text.replace("TradingAgents manager", "Quant desk manager").replace("ai-hedge-fund manager", "Veterans manager")
    for code, name in (("A", "Quant desk"), ("B", "Veterans"), ("C", "Edge Desk")):
        text = re.sub(rf"(^|[.!?:]\s+|\n)(?:the\s+)?Desk {code}\b", lambda m: f"{m.group(1)}The {name}", text)
        text = re.sub(rf"\b(?:the\s+)?Desk {code}\b", f"the {name}", text)
        text = re.sub(rf"\bDESK {code}\b", name, text)
    for one, many in (("is", "are"), ("was", "were"), ("has", "have"), ("does", "do"), ("argues", "argue"),
                      ("says", "say"), ("thinks", "think"), ("rates", "rate"), ("concedes", "concede"),
                      ("holds", "hold"), ("sees", "see"), ("treats", "treat"), ("relies", "rely")):
        text = re.sub(rf"\bVeterans {one}\b", f"Veterans {many}", text)
    return text


def prices(s: str) -> list[float]:
    return [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.?\d*", s or "") if float(x.replace(",", "")) > 0.5]


# ------------------------------------------------------------------ inputs
def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def events(run_dir: Path) -> list[dict]:
    path = run_dir / "events.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                out[m.group(1)] = m.group(2).strip("\"'")
    return out


def _repair_missed_splits(rows: list[dict], ticker: str, end: date, pair: tuple) -> list[dict]:
    """Alpaca's adjusted bars leave some splits unadjusted (AVGO 2024-07-15; XLK, XLE,
    XLY 2025-12-05), which draws a cliff that never happened. Each split on the
    corporate-actions list is checked against the closes: a move across the ex-date
    that matches the ratio means the feed missed it, and the earlier closes are
    restated. Same rule as ai-hedge-fund's data/free/splits.py."""
    if len(rows) < 2:
        return rows
    url = ("https://data.alpaca.markets/v1/corporate-actions?types=forward_split,reverse_split"
           f"&symbols={ticker}&start={rows[0]['d']}&end={end}")
    req = urllib.request.Request(url, headers={"APCA-API-KEY-ID": pair[0], "APCA-API-SECRET-KEY": pair[1]})
    try:
        actions = (json.load(urllib.request.urlopen(req, timeout=30)).get("corporate_actions") or {})
    except Exception:
        return rows
    splits = sorted(((a["ex_date"], a["new_rate"] / a["old_rate"])
                     for a in (*actions.get("forward_splits", []), *actions.get("reverse_splits", []))
                     if a.get("old_rate")), reverse=True)
    for ex, ratio in splits:
        i = next((k for k, r in enumerate(rows) if r["d"] >= ex), None)
        if not i or abs(ratio - 1.0) < 0.2 or not rows[i]["c"]:
            continue
        if abs((rows[i - 1]["c"] / rows[i]["c"]) / ratio - 1.0) <= 0.25:
            for r in rows[:i]:
                r["c"] = r["c"] / ratio
    return rows


def yahoo_closes(ticker: str, start: date, end: date) -> list[dict]:
    """Split-adjusted daily closes from Yahoo, the chart's last resort."""
    try:
        import yfinance as yf
        frame = yf.Ticker(ticker.replace(".", "-")).history(start=start.isoformat(),
                                                            end=(end + timedelta(days=1)).isoformat(),
                                                            interval="1d", auto_adjust=False)
    except Exception:
        return []
    return [{"d": d.date().isoformat(), "c": round(float(r["Close"]), 4)} for d, r in frame.iterrows()
            if d.date() <= end]


def bars(ticker: str, as_of: str, run_dir: Path) -> list[dict]:
    """Six months of settled daily closes, REST only (never a websocket), cached per run."""
    cache = run_dir / "bars.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    # Keys from the environment, then the desk's settings, then ai-hedge-fund's, then the file
    # ALPACA_ENV_FILE points at. None of them is copied anywhere.
    cfg = {**_env(HEDGE_ENV), **_env(DESK_ENV), **{k: v for k, v in os.environ.items() if k.startswith(("ALPACA", "APCA"))}}
    keys = {**cfg, **(_env(Path(cfg["ALPACA_ENV_FILE"])) if cfg.get("ALPACA_ENV_FILE") else {})}
    pair = next(((keys[k], keys[s]) for k, s in (("ALPACA_API_KEY", "ALPACA_SECRET_KEY"), ("ALPACA_TRADING_KEY", "ALPACA_TRADING_SECRET"),
                 ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY")) if keys.get(k) and keys.get(s)), None)
    end = date.fromisoformat(as_of)
    # The data chain (2026-10-05): Alpaca SIP, then IEX (an account without SIP gets a 403), then
    # Yahoo when there are no Alpaca keys or Alpaca answers nothing.
    out = []
    for feed in (("sip", "iex") if pair else ()):
        url = (f"https://data.alpaca.markets/v2/stocks/{ticker}/bars?timeframe=1Day&adjustment=split"
               f"&start={end - timedelta(days=190)}&end={end}&limit=10000&feed={feed}")
        req = urllib.request.Request(url, headers={"APCA-API-KEY-ID": pair[0], "APCA-API-SECRET-KEY": pair[1]})
        try:
            data = json.load(urllib.request.urlopen(req, timeout=30))
        except Exception:
            continue
        out = [{"d": b["t"][:10], "c": b["c"]} for b in (data.get("bars") or []) if b["t"][:10] <= as_of]
        if out:
            out = _repair_missed_splits(out, ticker, end, pair)
            break
    if not out:
        out = yahoo_closes(ticker, end - timedelta(days=190), end)
    if not out:
        return []
    run_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out), encoding="utf-8")
    return out


# ------------------------------------------------------------------ build
def timings(evts: list[dict]) -> dict[tuple[str, str], dict]:
    """(engine, step) -> {at, took}: when each agent finished and how long since the previous one."""
    out, last = {}, {}
    for e in evts:
        eng = e.get("engine")
        if e.get("type") == "engine_started":
            last[eng] = e.get("at", 0)
        if e.get("type") == "agent_done" and e.get("step"):
            at = e.get("at", 0)
            out[(eng, e["step"])] = {"at": at, "took": round(at - last.get(eng, 0), 1)}
            if eng in ("tape", "edge"):
                last[eng] = at
    return out


def quant_team(ta: dict, t: dict, restated: dict) -> dict:
    reports = ta.get("reports") or {}
    agents = []
    for aid, name, stage, key, step in QUANT:
        text = reports.get(key) or ""
        tm = t.get(("tape", step)) or {}
        line = labelled(text, "Executive Summary") if aid == "pm" else labelled(text, "Recommendation") if aid == "rm" else ""
        agents.append({"id": aid, "name": name, "stage": stage, "gist": line if len(line) > 40 else gist(text),
                       "words": len(text.split()), "at": tm.get("at"), "took": tm.get("took"),
                       "saved": bool(text.strip())})
    return {"rating": ta.get("rating"), "reused": bool(ta.get("reused")), "seconds": ta.get("seconds"),
            "timeframe": (restated.get("A") or {}).get("timeframe"),
            "case": labelled(reports.get("final_trade_decision"), "Executive Summary") or gist(reports.get("final_trade_decision") or ""),
            "agents": agents,
            "reports": {a[0]: (reports.get(a[3]) or "").replace("�", "-") for a in QUANT},
            "models": ta.get("models"), "usage": ta.get("usage")}


def vets_team(aihf: dict, t: dict, restated: dict) -> dict:
    v = aihf.get("verdict") or {}
    by = {p["model"]: p for p in aihf.get("personas") or []}
    agents = []
    for aid, name, stage in VETS:
        tm = t.get(("value", aid)) or {}
        if aid == "research_manager":
            agents.append({"id": aid, "name": name, "stage": stage, "gist": first_sentence(v.get("summary")),
                           "call": (v.get("rating") or "").lower(), "confidence": v.get("confidence"),
                           "at": tm.get("at"), "saved": True})
            continue
        p = by.get(aid)
        if not p:
            continue
        agents.append({"id": aid, "name": name, "stage": stage, "call": p.get("call"),
                       "confidence": p.get("confidence"), "value": p.get("value"),
                       "gist": gist(p.get("reasoning") or "") or "No view.",
                       "text": (p.get("reasoning") or "").replace("�", "-"),
                       "at": tm.get("at"), "saved": True})
    return {"rating": aihf.get("rating"), "reused": bool(aihf.get("reused")), "seconds": aihf.get("seconds"),
            "timeframe": (restated.get("B") or {}).get("timeframe"), "votes": v.get("votes"),
            "score": v.get("desk_score"), "desk_rating": v.get("desk_rating"),
            "case": first_sentence(v.get("summary")), "agents": agents,
            "verdict": {k: v.get(k) for k in ("rating", "confidence", "summary", "thesis", "bull_case",
                                               "bear_case", "what_would_change", "headlines", "model")},
            "fundamentals": aihf.get("fundamentals"), "usage": aihf.get("usage"),
            "caveats": v.get("data_caveats") or []}


def step_state(saved: bool, error: str | None) -> str:
    """ok, failed, unavailable (a source could not be read) or skipped (nothing to do). Counted
    apart on the first screen, never lumped together."""
    if saved:
        return "ok"
    e = (error or "").lower()
    if "no swing call" in e or "skipped" in e or "not attempted" in e or "no headlines" in e:
        return "skipped"
    if "sec archive" in e or "could not be fetched" in e or "could not be read" in e \
            or "no management discussion" in e or "no 10-q or 10-k" in e:
        return "unavailable"
    return "failed"


def run_flags(bundle: dict, aihf: dict | None, edge: dict | None, engines: list[str]) -> list[dict]:
    """The warnings on a run's first screen, every one computed from the saved run, never written
    by hand. level: high (the evidence is compromised), warn (check before acting), info."""
    out = []
    withheld = ((bundle.get("quality") or {}).get("withheld") or {}).get("long_term")
    caveats = " ".join(((aihf or {}).get("verdict") or {}).get("data_caveats") or []).lower()
    edge_codes = {c.get("code") for c in (edge or {}).get("caveats") or []}
    if withheld:
        out.append({"kind": "withheld_lt", "level": "high",
                    "text": f"Long term withheld for the whole desk: {withheld.get('reason')}."})
    elif "quarter stale" in caveats or "companyfacts_stale" in edge_codes:
        out.append({"kind": "stale", "level": "high",
                    "text": "Fundamentals are a quarter stale: the latest filing is not in SEC's data feed yet."})
    check = bundle.get("price_check") or {}
    pv, pe = ((aihf or {}).get("anchors") or {}).get("as_of"), ((edge or {}).get("anchors") or {}).get("as_of")
    if check.get("consistent") is False or (not check and pv and pe and str(pv)[:10] != str(pe)[:10]):
        out.append({"kind": "prices", "level": "warn",
                    "text": check.get("problem") and f"Teams priced from different sessions ({check['problem']})."
                            or f"Teams priced from different sessions: Veterans {str(pv)[:10]}, Edge Desk {str(pe)[:10]}."})
    if edge is not None and "edge" in engines and not edge.get("rating"):
        out.append({"kind": "edge_withheld", "level": "info",
                    "text": "Edge Desk withheld its rating, so it votes nowhere."})
    steps = [(s.get("id"), s.get("name") or s.get("id"), step_state(bool(s.get("saved")), s.get("error")))
             for s in (edge or {}).get("steps") or []]
    failed = [n for _i, n, st in steps if st == "failed"]
    if failed:
        missing = [i for i, _n, st in steps if i in ("bull", "bear") and st != "ok"]
        synth = any(i == "synthesis" and st == "ok" for i, _n, st in steps)
        out.append({"kind": "partial", "level": "warn",
                    "text": f"Edge Desk's written analysis is partial: {', '.join(failed).lower()} failed."
                            + (f" Its synthesis ran without the {' and '.join(missing)} case." if missing and synth else "")})
    gone = [n for _i, n, st in steps if st == "unavailable"]
    if gone:
        out.append({"kind": "unavailable", "level": "info",
                    "text": f"Edge Desk could not read a source for: {', '.join(gone).lower()}."})
    unchecked = any(isinstance(lv, dict) and "status" not in lv
                    for h in (bundle.get("horizons") or {}).values()
                    for lv in ((h.get("memo") or {}).get("levels") or {}).values())
    if unchecked:
        out.append({"kind": "levels_unchecked", "level": "info",
                    "text": "Price levels in this run were written by the model before levels were computed "
                            "in code (2026-10-07): treat them as unchecked."})
    flagged = sum(1 for h in (bundle.get("horizons") or {}).values()
                  for t in ((h.get("debate") or {}).get("turns") or []) if "found in no report" in (t.get("note") or ""))
    if flagged:
        out.append({"kind": "figures", "level": "warn",
                    "text": f"{flagged} debate turn{'s' if flagged > 1 else ''} quoted figures found in no report; "
                            "they are marked in the debate."})
    return out


def _history_row(day: str, b: dict, latest_id: str | None) -> dict:
    hz = b.get("horizons") or {}
    shared = b.get("ai_hedge_fund") or b.get("edge_desk") or {}
    rid = b.get("run_id")
    return {"date": day, "run_id": rid, "latest": rid == latest_id, "time": (b.get("generated_at") or "")[11:16],
            "mode": b.get("mode") or "debate", "close": shared.get("last_close"),
            "plan": (b.get("plan") or {}).get("name"),
            "ratings": {k: (hz.get(k) or {}).get("outcome", {}).get("rating") for k in ("swing", "long_term") if k in hz},
            "status": {k: (hz.get(k) or {}).get("outcome", {}).get("status") for k in ("swing", "long_term") if k in hz}}


def same_day(ticker: str, as_of: str) -> list[dict]:
    """Every kept run of the symbol on that day, newest first (one per day before 2026-10-07)."""
    pointer = _json(MEMOS / f"{ticker}-{as_of}.json") or {}
    rows = [_history_row(as_of, b, pointer.get("run_id"))
            for b in (_json(p) for p in (MEMOS / "runs").glob(f"{ticker}-{as_of}-*/memo.json")) if b]
    if pointer and not pointer.get("run_id"):
        rows.append(_history_row(as_of, pointer, None) | {"latest": True})
    return sorted(rows, key=lambda r: r["time"], reverse=True)


def history(ticker: str, as_of: str, shown: str | None = None) -> list[dict]:
    """Other runs of the same symbol, newest first: the ratings then and the close then. Earlier
    days by their latest run, and every other run of the shown day."""
    # `shown` is the run on screen; None means the day's latest is.
    out = [r for r in same_day(ticker, as_of) if not (r["run_id"] == shown if shown else r["latest"])]
    for path in MEMOS.glob(f"{ticker}-????-??-??.json"):
        day = path.stem[len(ticker) + 1:]
        if day >= as_of:
            continue
        b = _json(path)
        if b:
            out.append(_history_row(day, b, b.get("run_id")))
    return sorted(out, key=lambda r: (r["date"], r["time"]), reverse=True)


def edge_team(edge: dict, t: dict) -> dict:
    sig = edge.get("swing") or {}
    agents = []
    for st in edge.get("steps") or []:
        tm = t.get(("edge", st["id"])) or {}
        agents.append({"id": st["id"], "name": st["name"], "stage": st["stage"], "gist": st.get("gist") or "",
                       "text": st.get("text") or "", "saved": bool(st.get("saved")), "error": st.get("error"),
                       "state": step_state(bool(st.get("saved")), st.get("error")),
                       "at": tm.get("at"), "took": tm.get("took")})
    score = edge.get("score")
    return {"rating": edge.get("rating"), "reused": bool(edge.get("reused")), "seconds": edge.get("seconds"),
            "timeframe": "1 year or more", "score": score, "conviction": edge.get("conviction"),
            "quality_state": edge.get("quality_state"), "swing": sig,
            "case": (f"Score {score:.1f} of 100, {edge.get('conviction') or 'no'} conviction. " if isinstance(score, (int, float)) else "")
                    + f"Swing setup {sig.get('signal') or 'n/a'}: {sig.get('why') or ''}",
            "agents": agents, "report_md": edge.get("report_md") or "", "levels": edge.get("levels"),
            "basis": edge.get("rating_basis"), "expected_return": edge.get("expected_annual_return"),
            "close": edge.get("last_close"), "price_day": (edge.get("anchors") or {}).get("as_of")}


def horizon(key: str, h: dict) -> dict:
    memo, out, deb = h.get("memo") or {}, h.get("outcome") or {}, h.get("debate")
    levels = []
    for k in ("entry_zone", "stop", "first_target", "trim"):
        lv = (memo.get("levels") or {}).get(k)
        if lv:
            status = lv.get("status") or "unchecked"          # unchecked: written before 2026-10-07
            values = ([] if status == "withheld" else
                      sorted({lv["low"], lv["high"]}) if status == "checked" else prices(lv.get("price")))
            levels.append({"key": k, "label": LEVEL_LABEL[k], "price": lv.get("price"), "status": status,
                           "reason": teams(lv.get("reason")) if status != "withheld"
                           else teams(f"{lv.get('why')}. {lv.get('reason') or ''}".strip()),
                           "how": lv.get("how"), "values": values})
    turns = []
    if deb:
        for tr in deb.get("turns") or []:
            turns.append({"round": tr.get("round"), "team": TEAM.get(tr.get("desk"), "quant"),
                          "decision": tr.get("decision"), "rating": tr.get("rating"),
                          "argument": teams(tr.get("argument")), "holes": teams(tr.get("holes_in_other_report") or []),
                          "evidence": teams(tr.get("new_evidence") or []),
                          "changed": teams(tr.get("what_changed_my_mind") or ""), "note": teams(tr.get("note") or "")})
    label, sub = HORIZON[key][0], h.get("span") or HORIZON[key][1]
    restated = {TEAM[d]: {"rating": r.get("rating"), "timeframe": r.get("timeframe"),
                          "rationale": teams(r.get("rationale")), "own": r.get("own")}
                for d, r in (h.get("restated") or {}).items()}
    conceded = [TEAM[d] for d in (deb or {}).get("conceded") or ([deb["conceded_by"]] if deb and deb.get("conceded_by") else [])]
    stands = {TEAM[d]: r for d, r in ((deb or {}).get("ratings") or {}).items()}
    return {"key": key, "label": label, "sub": sub, "rating": out.get("rating"),
            "conviction": out.get("conviction"), "status": out.get("status"), "how": teams(out.get("how")),
            "note": teams(out.get("note") or ""), "voters": [TEAM[d] for d in (h["voters"] if "voters" in h else list(h.get("restated") or {}))],
            "action": h.get("action"), "restated": restated,
            "debate": {"turns": turns, "conceded_by": conceded[-1] if conceded else None, "conceded": conceded,
                       "stands": stands, "outcome": deb.get("outcome")} if deb else None,
            "memo": {"headline": teams(memo.get("headline")), "summary": teams(memo.get("summary")),
                     "action_note": teams(memo.get("action_note")), "valuation_view": teams(memo.get("valuation_view")),
                     "reasons": teams(memo.get("key_reasons") or []), "risks": teams(memo.get("key_risks") or []),
                     "watch": teams(memo.get("what_to_watch") or []), "levels": levels,
                     "crux": teams(memo.get("disagreement_crux") or ""),
                     "manager_view": teams(memo.get("super_manager_view") or "")}}


def build(ticker: str, as_of: str, run: str | None = None) -> dict:
    """The day's latest run, or with `run` one particular run of that day."""
    # Both become memos/ paths, and the ticker also goes into an Alpaca URL that carries the keys.
    ticker, as_of = validate.ticker(ticker), validate.day(as_of)
    pointer = _json(MEMOS / f"{ticker}-{as_of}.json")
    if run:
        run = validate.run_id(run)
        bundle = _json(validate.inside(MEMOS / "runs", f"{ticker}-{as_of}-{run}") / "memo.json")
    else:
        bundle = pointer
    if not bundle:
        raise FileNotFoundError(f"no memo for {ticker} on {as_of}" + (f" (run {run})" if run else ""))
    rid = bundle.get("run_id")
    latest = not pointer or pointer.get("run_id") == rid
    run_dir = MEMOS / "runs" / (f"{ticker}-{as_of}-{rid}" if rid else f"{ticker}-{as_of}")
    engines = bundle.get("engines") or ["quant", "vets"]
    # The reports the memo was written from, embedded in the bundle, come first. The loose files in
    # the run folder are rewritten by any later run that day, and NVDA 2026-09-18 showed an Edge
    # report written eight minutes after its memo. Loose files are only a fallback.
    ta = (bundle.get("tradingagents") or _json(run_dir / "tradingagents.json")) if "quant" in engines else None
    aihf = (bundle.get("ai_hedge_fund") or _json(run_dir / "ai-hedge-fund.json")) if "vets" in engines else None
    edge = (bundle.get("edge_desk") or _json(run_dir / "edge-desk.json")) if "edge" in engines else None
    shared = aihf or edge or {}
    evts = events(run_dir)
    t = timings(evts)
    hz = bundle.get("horizons") or {}
    # Each team's own clock: `native` since 2026-09-24, the retired "as they ran" block before that.
    own_restated = bundle.get("native") or (hz.get("as_they_ran") or {}).get("restated") or {}
    memo_md = run_dir / "memo.md" if rid and (run_dir / "memo.md").exists() else MEMOS / f"{ticker}-{as_of}.md"
    finished = next((e for e in reversed(evts) if e.get("type") == "run_finished"), None)
    anchor_day = (shared.get("anchors") or {}).get("as_of") or as_of
    return {
        "ticker": ticker, "date": as_of, "run_id": rid, "latest": latest,
        "owns": bundle.get("owns"), "generated_at": bundle.get("generated_at"),
        "engines": [e for e in engines if {"quant": ta, "vets": aihf, "edge": edge}[e]],
        "mode": bundle.get("mode") or "debate",
        "close": shared.get("last_close"), "anchors": shared.get("anchors"), "valuation": shared.get("valuation"),
        "costs": bundle.get("costs", {}).get("total"), "billing": bundle.get("costs", {}).get("billing") or "api",
        "notional": bundle.get("costs", {}).get("notional"), "seconds": finished.get("at") if finished else None,
        "horizons": [horizon(k, hz[k]) for k in HORIZON_ORDER if k in hz],
        "quant": quant_team(ta, t, own_restated) if ta else None,
        "vets": vets_team(aihf, t, own_restated) if aihf else None,
        "edge": edge_team(edge, t) if edge else None,
        "memo_md": teams(memo_md.read_text(encoding="utf-8")) if memo_md.exists() else "",
        # Statements a team gave up or had disputed in the debate. Quotes stay exactly as the report wrote them (they are
        # matched against its text); only the explanation is reworded to the dashboard's team names.
        "corrections": [{"team": TEAM[c["desk"]], "quote": c["quote"].replace("\ufffd", "-"), "status": c["status"],
                         "reason": teams(c.get("reason") or ""), "by": TEAM.get(c.get("raised_by")),
                         "round": c.get("round"), "horizon": HORIZON[k][0]}
                        for k in HORIZON_ORDER if k in hz for c in (hz[k].get("corrections") or [])],
        "bars": [b for b in bars(ticker, as_of, run_dir) if b["d"] <= anchor_day],   # settled closes only
        "price_day": anchor_day if anchor_day != as_of else None,
        # Which subscription answered. Runs from before the ChatGPT plan existed were all Claude.
        "plan": bundle.get("plan") or {"name": "claude" if (bundle.get("costs") or {}).get("billing") == "max" else "api",
                                       "models": [bundle.get("model")] if bundle.get("model") else []},
        "flags": ([] if latest else [{"kind": "earlier_run", "level": "info",
                                      "text": f"An earlier run of {ticker} from this day ({(bundle.get('generated_at') or '')[11:16]}); "
                                              "a later run is the day's result."}]) + run_flags(bundle, aihf, edge, engines),
        "history": history(ticker, as_of, rid if not latest else None),
        "provenance": {TEAM[d]: {"reused_from": (p or {}).get("reused_from"), "inputs": (p or {}).get("inputs")}
                       for d, p in (("A", ta), ("B", aihf), ("C", edge)) if p},
    }


def index() -> list[dict]:
    """Every finished run, newest first, with its horizon ratings for the sidebar."""
    out = []
    for path in MEMOS.glob("*-????-??-??.json"):
        m = re.match(r"(.+)-(\d{4}-\d{2}-\d{2})$", path.stem)
        if not m:
            continue
        try:
            b = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        hz = b.get("horizons") or {}
        engines = b.get("engines") or ["quant", "vets"]
        own = {e: (b.get(k) or {}).get("rating") for e, k in (("quant", "tradingagents"), ("vets", "ai_hedge_fund"),
                                                               ("edge", "edge_desk")) if e in engines and b.get(k)}
        out.append({"ticker": m.group(1), "date": m.group(2), "owns": b.get("owns"),
                    "mode": b.get("mode") or "debate", "engines": engines, "own_ratings": own,
                    "status": {k: (hz.get(k) or {}).get("outcome", {}).get("status") for k in HORIZON_ORDER if k in hz},
                    "generated_at": b.get("generated_at") or "",
                    "close": (b.get("ai_hedge_fund") or b.get("edge_desk") or {}).get("last_close"),
                    "plan": (b.get("plan") or {}).get("name") or ("claude" if (b.get("costs") or {}).get("billing") == "max" else "api"),
                    "flags": [{"kind": f["kind"], "level": f["level"]} for f in
                              run_flags(b, b.get("ai_hedge_fund"), b.get("edge_desk"), engines)],
                    "ratings": {k: (hz.get(k) or {}).get("outcome", {}).get("rating") for k in HORIZON_ORDER if k in hz}})
    return sorted(out, key=lambda r: (r["date"], r["generated_at"]), reverse=True)
