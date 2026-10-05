"""Command line.

    engine analyze TICKER [...]         run one or more names now
    engine week                         the weekly job over owned plus watchlist
    engine events                       has anything material happened since?
    engine scan                         deterministic scores over the large-cap list
    engine report TICKER --format full  re-render a saved run, no refetch
    engine calibrate TICKER [...]       measure whether the scores separate outcomes

Re-rendering is a separate verb on purpose. A report is a view of a run, and
producing a different view must never mean fetching data again, because a run
that changes when you look at it differently is not a record of anything.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from edgedesk import reports, run as run_mod
from edgedesk.config import load as load_env, missing as missing_settings
from edgedesk.providers.client import DataClient, settled_as_of
from edgedesk.reports import vault


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="engine",
        description="Deterministic stock research: buy / hold / sell with actionable levels.")
    sub = parser.add_subparsers(dest="command", required=True)

    one = sub.add_parser("analyze", help="analyze one or more tickers")
    one.add_argument("tickers", nargs="+")
    one.add_argument("--as-of", default=None,
                     help="ISO date; defaults to the last settled session")
    one.add_argument("--own", nargs="*", default=[],
                     help="tickers already held (changes the action, never the rating)")
    one.add_argument("--format", default="scorecard",
                     choices=[*sorted(reports.FORMATS), "none"],
                     help="what to print (default scorecard)")
    one.add_argument("--out", metavar="DIR", default=None,
                     help="also write the report into DIR")
    one.add_argument("--also", nargs="*", default=[], choices=sorted(reports.FORMATS),
                     help="extra formats to write into --out")
    one.add_argument("--llm", action="store_true",
                     help="add the written analysis (runs on the Max subscription)")
    one.add_argument("--vault", action="store_true",
                     help="write or update the ticker's Obsidian note")
    one.add_argument("--no-save", action="store_true", help="do not write the run file")
    one.add_argument("--json", action="store_true", help="print the run JSON instead")
    one.add_argument("-v", "--verbose", action="store_true")

    wk = sub.add_parser("week", help="the weekly job over owned plus watchlist")
    wk.add_argument("--as-of", default=None)
    wk.add_argument("--no-llm", action="store_true",
                    help="numbers only, no written analysis")
    wk.add_argument("--out", metavar="DIR", default=None, help="write every report here")
    wk.add_argument("--vault", action="store_true", help="update the Obsidian notes")
    wk.add_argument("--force", action="store_true",
                    help="run during market hours anyway (the live bot shares this key)")
    wk.add_argument("-v", "--verbose", action="store_true")

    ev = sub.add_parser("events", help="check for material events since the last run")
    ev.add_argument("tickers", nargs="*", default=[])
    ev.add_argument("--as-of", default=None)
    ev.add_argument("-v", "--verbose", action="store_true")

    sc = sub.add_parser("scan", help="deterministic scores over the large-cap list")
    sc.add_argument("tickers", nargs="*", default=[])
    sc.add_argument("--as-of", default=None)
    sc.add_argument("--horizon", default="long_term", choices=("swing", "long_term"))
    sc.add_argument("--top", type=int, default=25)
    sc.add_argument("--out", metavar="DIR", default=None)
    sc.add_argument("--force", action="store_true")
    sc.add_argument("-v", "--verbose", action="store_true")

    cal = sub.add_parser("calibrate",
                         help="measure whether higher scores preceded better outcomes")
    cal.add_argument("tickers", nargs="+")
    cal.add_argument("--start", required=True, help="first sample date, ISO")
    cal.add_argument("--end", required=True, help="last sample date, ISO")
    cal.add_argument("--holdout-start", required=True,
                     help="everything from this date on is held out")
    cal.add_argument("--step", type=int, default=5,
                     help="trading days between samples (default 5, a weekly cadence)")
    cal.add_argument("--name", default="latest")
    cal.add_argument("--force", action="store_true")
    cal.add_argument("-v", "--verbose", action="store_true")

    res = sub.add_parser("research",
                         help="test a swing setup as executable trades on the wide universe")
    res.add_argument("setup", choices=["pead", "pullback"])
    res.add_argument("--tickers", nargs="*", default=None, help="default: the research universe")
    res.add_argument("--start", default="2019-01-01")
    res.add_argument("--end", default=None, help="default: the last settled session")
    res.add_argument("--holdout-start", default="2024-01-01")
    res.add_argument("--force", action="store_true")
    res.add_argument("-v", "--verbose", action="store_true")

    rep = sub.add_parser("report", help="re-render a saved run without refetching")
    rep.add_argument("tickers", nargs="+")
    rep.add_argument("--as-of", default=None, help="ISO date of the saved run")
    rep.add_argument("--format", default="condensed", choices=sorted(reports.FORMATS))
    rep.add_argument("--out", metavar="DIR", default=None)
    rep.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if getattr(args, "verbose", False) else
                        logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    handlers = {"analyze": _analyze, "week": _week, "events": _events, "scan": _scan,
                "calibrate": _calibrate, "report": _report, "research": _research}
    return handlers[args.command](args)


def _require_settings() -> int:
    load_env()
    gaps = missing_settings()
    if not gaps:
        return 0
    print("Missing settings in ~/.edge-desk/.env:", file=sys.stderr)
    for gap in gaps:
        print(f"  - {gap}", file=sys.stderr)
    return 2


def _as_of(args):
    return date.fromisoformat(args.as_of) if getattr(args, "as_of", None) else None


def _analyze(args) -> int:
    code = _require_settings()
    if code:
        return code

    as_of = settled_as_of(args.as_of)
    owned = {t.upper() for t in args.own}
    client = DataClient()
    failures = 0
    try:
        for ticker in args.tickers:
            try:
                run = run_mod.analyze(ticker, as_of, client=client,
                                      owns=ticker.upper() in owned, save=not args.no_save)
            except Exception as exc:                        # noqa: BLE001
                failures += 1
                print(f"{ticker.upper()}: run failed: {exc}", file=sys.stderr)
                continue
            if args.llm:
                from edgedesk.llm.analysis import attach
                print(f"{ticker.upper()}: writing the analysis...", file=sys.stderr)
                attach(run, say=lambda m: print(m, file=sys.stderr))
                if not args.no_save:
                    run_mod.write(run)
            _emit(run, args)
    finally:
        client.close()
    return 1 if failures else 0


def _week(args) -> int:
    code = _require_settings()
    if code:
        return code
    from edgedesk.jobs.week import MarketOpen, run_week

    try:
        result = run_week(as_of=_as_of(args), llm=not args.no_llm, force=args.force,
                          out_dir=Path(args.out) if args.out else None,
                          vault=args.vault)
    except MarketOpen as exc:
        print(str(exc), file=sys.stderr)
        return 3
    if result.get("summary"):
        print()
        print(result["summary"])
    return 0


def _events(args) -> int:
    code = _require_settings()
    if code:
        return code
    from edgedesk.jobs.events import check

    check([t.upper() for t in args.tickers] or None, _as_of(args))
    return 0


def _scan(args) -> int:
    code = _require_settings()
    if code:
        return code
    from edgedesk.jobs.scan import render, scan
    from edgedesk.jobs.week import MarketOpen

    try:
        rows = scan([t.upper() for t in args.tickers] or None, _as_of(args),
                    horizon=args.horizon, top=args.top, force=args.force)
    except MarketOpen as exc:
        print(str(exc), file=sys.stderr)
        return 3
    if args.out and rows:
        as_of = settled_as_of(args.as_of)
        path = Path(args.out) / f"scan-{as_of}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(rows, as_of, args.horizon, args.top), encoding="utf-8")
        print(f"written: {path}", file=sys.stderr)
    return 0


def _calibrate(args) -> int:
    code = _require_settings()
    if code:
        return code
    from edgedesk.calibrate.run import MarketOpen, calibrate

    try:
        result = calibrate(
            args.tickers, date.fromisoformat(args.start), date.fromisoformat(args.end),
            date.fromisoformat(args.holdout_start), step=args.step, name=args.name,
            force=args.force)
    except MarketOpen as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print()
    print(result["text"])
    return 0


def _research(args) -> int:
    code = _require_settings()
    if code:
        return code
    from edgedesk.calibrate.harness import market_is_open
    from edgedesk.paths import USER_DIR, ensure
    from edgedesk.research import pead, pullback, runner
    from edgedesk.research.universe import SECTOR_OF, TICKERS

    if market_is_open() and not args.force:
        print("The market is open. This makes a few hundred requests on the key the live "
              "trading bot shares, so it runs outside 09:30-16:00 ET. Use --force if the "
              "bot is not running.", file=sys.stderr)
        return 3
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else settled_as_of(None)
    tickers = [t.upper() for t in (args.tickers or TICKERS)]
    print(f"Loading {len(tickers)} tickers, {start} to {end}.")
    sample = runner.load(tickers, start, end)
    for ticker, why in sample["failed"]:
        print(f"  {ticker}: not loaded ({why})")
    if args.setup == "pead":
        name, fn, variants, rule = pead.SETUP, pead.signals, pead.VARIANTS, None
    else:
        name, variants, rule = pullback.SETUP, pullback.VARIANTS, pullback.exit_rule
        fn = pullback.make_signal_fn(sample["bars"], SECTOR_OF, sample["sector_close"])
    results = runner.evaluate_setup(name, fn, variants, sample, args.holdout_start,
                                    exit_rule=rule)
    text = runner.render(name, results, {
        "tickers": len(sample["bars"]), "start": start, "end": end,
        "holdout_start": args.holdout_start})
    out = ensure(USER_DIR / "research" / "reports" / f"{name}-{date.today()}.md")
    out.write_text(text, encoding="utf-8")
    print()
    print(text)
    print(f"Report written to {out}")
    return 0


def _report(args) -> int:
    as_of = (args.as_of or settled_as_of(None).isoformat())[:10]
    failures = 0
    for ticker in args.tickers:
        run = run_mod.read(ticker, as_of)
        if run is None:
            failures += 1
            print(f"{ticker.upper()}: no saved run for {as_of}. Run `engine analyze "
                  f"{ticker.upper()} --as-of {as_of}` first.", file=sys.stderr)
            continue
        print(reports.render(run, args.format))
        if args.out:
            print(f"written: {reports.write(run, args.format, args.out)}", file=sys.stderr)
    return 1 if failures else 0


def _emit(run: dict, args) -> None:
    if args.json:
        print(json.dumps(run, indent=2, sort_keys=True, default=str))
    elif args.format != "none":
        print(reports.render(run, args.format))
        print()
    if args.out:
        for fmt in sorted({args.format, *args.also} - {"none"}):
            print(f"written: {reports.write(run, fmt, args.out)}", file=sys.stderr)
    if getattr(args, "vault", False):
        written = vault.write(run)
        print(f"vault: {written or 'no Obsidian vault found on this machine'}",
              file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
