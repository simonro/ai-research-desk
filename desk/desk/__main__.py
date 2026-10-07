"""Run the desk on one or more symbols.

    python -m desk ECG                     asks whether you own ECG; long term + swing verdicts
    python -m desk ECG V --own ECG         you own ECG, not V (no questions asked)
    python -m desk ECG --horizon swing     one horizon only (long, swing, or both)
    python -m desk ECG --fresh             rerun the engines even if today's reports exist
    python -m desk ECG --engines quant,vets,edge     which engines run (default quant,vets)
    python -m desk ECG --engines edge --no-debate    just run them and keep their reports
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from dotenv import load_dotenv

from desk import DeskError
from desk.config import DEFAULT_MANDATE, KEY_ENV_FILES, MAX_ROUNDS, MEMOS_DIR, MODEL
from desk.views import DEFAULT_VIEWS

# "all" and "both" are the same since "as they ran" was retired (2026-09-24); the dashboard sends "all".
_HORIZON_CHOICES = {"all": list(DEFAULT_VIEWS), "both": list(DEFAULT_VIEWS),
                    "long": ["long_term"], "swing": ["swing"]}


def main() -> int:
    parser = argparse.ArgumentParser(prog="desk", description="Super Manager over TradingAgents and ai-hedge-fund")
    parser.add_argument("tickers", nargs="+", help="one or more symbols, e.g. ECG V")
    parser.add_argument("--own", nargs="*", default=None, metavar="TICKER",
                        help="symbols you already own; others are treated as new positions. "
                             "Omit to be asked for each symbol.")
    parser.add_argument("--horizon", choices=sorted(_HORIZON_CHOICES), default="all",
                        help="both horizons (default; 'all' means the same), or just: swing, long")
    parser.add_argument("--date", default=date.today().isoformat(), help="as-of date (default today)")
    parser.add_argument("--mandate", default=DEFAULT_MANDATE, help=f"ai-hedge-fund fund (default {DEFAULT_MANDATE})")
    parser.add_argument("--rounds", type=int, default=MAX_ROUNDS, help=f"max debate rounds (default {MAX_ROUNDS})")
    parser.add_argument("--fresh", action="store_true", help="rerun both engines even if today's reports exist")
    parser.add_argument("--no-vault", action="store_true", help="skip the Obsidian note")
    parser.add_argument("--engines", default="quant,vets",
                        help="comma list of quant (TradingAgents), vets (ai-hedge-fund), edge (Edge Desk)")
    parser.add_argument("--no-debate", action="store_true",
                        help="run the engines and keep their reports: no horizon ratings, debate or memo")
    parser.add_argument("--run-id", default=None, help=argparse.SUPPRESS)   # the dashboard names its runs
    args = parser.parse_args()

    if args.run_id and not __import__("re").fullmatch(r"\d{6}-[0-9a-f]{6}", args.run_id):
        raise SystemExit("--run-id must look like 153012-a1b2c3")
    for env_file in KEY_ENV_FILES:
        load_dotenv(env_file, override=False)

    from desk.engines import ENGINES
    args.engines = [e for e in ENGINES if e in {x.strip().lower() for x in args.engines.split(",")}]
    if not args.engines:
        raise SystemExit(f"--engines needs at least one of: {', '.join(ENGINES)}")
    if len(args.engines) < 2:
        args.no_debate = True                       # one opinion has nobody to debate
    tickers = [t.strip().upper() for raw in args.tickers for t in raw.replace(",", " ").split()]
    owned = _owned(tickers, args.own)
    if len(tickers) > 1:
        args.run_id = None                          # one id names one run

    failures = 0
    for ticker in tickers:
        try:
            run_one(ticker, owned[ticker], args)
        except DeskError as exc:
            failures += 1
            print(f"\n{ticker}: desk run failed. {exc}", file=sys.stderr)
    return 1 if failures else 0


def new_run_id() -> str:
    """HHMMSS plus six random hex digits: sorts by time, and two runs in one second still differ."""
    import secrets
    from datetime import datetime
    return f"{datetime.now():%H%M%S}-{secrets.token_hex(3)}"


def _owned(tickers: list[str], own_flag: list[str] | None) -> dict[str, bool]:
    if own_flag is not None:
        mine = {t.strip().upper() for raw in own_flag for t in raw.replace(",", " ").split()}
        return {t: t in mine for t in tickers}
    if not sys.stdin.isatty():
        raise SystemExit("Say which symbols you own with --own (e.g. --own ECG), or --own alone for none.")
    return {t: input(f"Do you already own {t}? [y/N] ").strip().lower() in ("y", "yes") for t in tickers}


def run_one(ticker: str, owns: bool, args) -> None:
    from desk.procs import stop_runners_on_term
    stop_runners_on_term()                  # a cancelled desk stops its teams before it exits
    from desk.debate import DESKS
    from desk.engines import run_selected
    from desk.events import EventLog
    from desk import maxplan
    from desk.llm import DeskLLM
    from desk.pipeline import analyze, native_views
    from desk.render import stamp, write_all
    from desk.session import price_check, price_session

    print(f"\n=== {ticker} ({args.date}, {'owned' if owns else 'new position'}) ===")
    # One price date for every team, decided before any starts (decision 2: close-only).
    session = price_session(args.date)
    print(f"  Priced from the {session} settled close" + ("" if session == args.date else
                                                             " (today's session has not settled)"))
    # Every run keeps its own folder; TICKER-DATE.json is only the pointer to the day's latest.
    run_id = getattr(args, "run_id", None) or new_run_id()
    run_dir = MEMOS_DIR / "runs" / f"{ticker}-{args.date}-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    debate = not args.no_debate
    horizon_keys = _HORIZON_CHOICES[args.horizon] if debate else []
    events = EventLog.start(run_dir / "events.jsonl", ticker=ticker, date=args.date, owns=owns,
                            horizons=horizon_keys, engines=args.engines, debate=debate,
                            text=f"{ticker} dispatched to {len(args.engines)} desk"
                                 f"{'s' if len(args.engines) != 1 else ''}")
    reports = run_selected(args.engines, ticker, session, args.mandate, run_dir, args.fresh, events=events)
    print("  Desk ratings: " + ", ".join(f"{DESKS[d]} {p.get('rating') or 'withheld'}" for d, p in reports.items()))
    checked = price_check(session, reports)
    if not checked["consistent"]:
        print(f"  WARNING: {checked['problem']}. A report reused from earlier today can cause this; "
              f"--fresh reruns it on the {session} close.")
    # Each team's own call on its own clock: provenance beside the verdicts, never compared.
    native = native_views(reports)
    clocks = "Own timeframes: " + ", ".join(f"{DESKS[d]} {v['timeframe'] or 'not stated'}" for d, v in native.items())
    print("  " + clocks)
    events.emit("timeframes", tape=(native.get("A") or {}).get("timeframe"),
                value=(native.get("B") or {}).get("timeframe"),
                edge=(native.get("C") or {}).get("timeframe"), text=clocks)

    # Decision 1 (audit R2-01): what the data supports is decided once, for every team.
    from desk import eligibility
    quality = eligibility.manifest(reports)
    for key, w in quality["withheld"].items():
        if w and key in horizon_keys:
            print(f"  {key.replace('_', ' ').capitalize()} withheld for the whole desk: {w['reason']}")
    llm = DeskLLM(MODEL)
    horizons = (analyze(llm, ticker, args.date, reports, horizon_keys, owns, args.rounds, events=events,
                        quality=quality)
                if debate else {})

    def billed(p):
        return (p.get("usage") or {}).get("usd") if p and not p.get("reused") else None

    def notional(p):
        return (p.get("usage") or {}).get("notional_usd") if p and not p.get("reused") else 0

    ta, aihf, edge = reports.get("A"), reports.get("B"), reports.get("C")
    desk_cost = llm.cost()
    bundle = {
        "ticker": ticker, "date": args.date, "run_id": run_id, "generated_at": stamp(), "model": MODEL,
        "mandate": args.mandate, "owns": owns, "horizons": horizons, "native": native,
        "price_session": session, "price_check": checked, "quality": quality,
        # Which subscription and models answered: a run on another plan is a different desk.
        "plan": {"name": maxplan.plan() if llm.max else "api",
                 "models": sorted({c["model"] for c in maxplan.CALLS} | {c.get("model") for c in llm.calls if c.get("model")})},
        "engines": args.engines, "mode": "debate" if debate else "reports",
        "tradingagents": ta, "ai_hedge_fund": aihf, "edge_desk": edge,
        "costs": {"tradingagents": billed(ta), "ai_hedge_fund": billed(aihf), "edge_desk": billed(edge),
                  "desk": desk_cost,
                  "total": round(sum(c for c in (billed(ta), billed(aihf), billed(edge), desk_cost) if c), 4),
                  "billing": (maxplan.plan() == "chatgpt" and "chatgpt" or "max") if llm.max else "api",
                  # what the same calls would have cost on the API (list price); Max bills nothing
                  "notional": round(sum(x for x in (notional(ta), notional(aihf), llm.notional()) if x), 4),
                  "desk_calls": llm.calls},
    }
    paths = write_all(bundle, MEMOS_DIR, to_vault=not args.no_vault and debate, run_dir=run_dir)
    events.emit("run_finished", ticker=ticker, cost=bundle["costs"]["total"],
                memo=str(paths["markdown"]), json=str(paths["json"]),
                text=f"Memo saved: {paths['markdown'].name}" if debate else "Reports saved")

    print()
    for key, h in horizons.items():
        rating = h["outcome"]["rating"] or h["outcome"]["conviction"]
        print(f"  {ticker} {key.replace('_', ' ')}: {rating.upper()} ({h['outcome']['conviction']}) -> {h['action']}")
    print(f"  Memo: {paths['markdown']}")
    if "vault" in paths:
        print(f"  Vault: {paths['vault']}")
    if bundle["costs"]["billing"] == "max":
        print(f"  Billed: $0.00 on the Max plan (the API would have charged about "
              f"${bundle['costs']['notional']:.2f})")
    elif bundle["costs"]["billing"] == "chatgpt":
        print("  Billed: $0.00 on the ChatGPT plan")
    print(f"  Cost: ${bundle['costs']['total']:.2f}"
          + ("  (reused engine reports are not re-billed)" if any(p.get("reused") for p in reports.values()) else ""))


if __name__ == "__main__":
    sys.exit(main())
