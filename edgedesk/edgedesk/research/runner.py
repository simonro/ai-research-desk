"""Run one setup across the research universe and write down what happened.

The split is by time and is declared before the run: development trades must be
CLOSED before the holdout starts, held-out trades must be OPENED on or after it,
and anything straddling the line belongs to neither. A setup passes only if the
development sample passes every hostile check AND the held-out sample still beats
both the benchmark and the random entries. Failing is a normal, useful outcome.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Callable

from edgedesk import paths
from edgedesk.providers.client import DataClient
from edgedesk.research import data, evaluate
from edgedesk.research.simulate import ExitRule, Signal
from edgedesk.research.universe import BENCHMARK, SECTOR_ETF

_WARMUP_DAYS = 420
_EVENT_GAP_DAYS = 20        # a second 2.02 filing this soon is the same report, amended

SignalFn = Callable[[str, list[dict], dict[str, float], list[dict], dict], list[Signal]]


def distinct_reports(events: list[dict]) -> list[dict]:
    out: list[dict] = []
    for e in events:
        if out and (date.fromisoformat(e["filed"])
                    - date.fromisoformat(out[-1]["filed"])).days < _EVENT_GAP_DAYS:
            continue
        out.append(e)
    return out


def load(tickers: list[str], start: date, end: date, say=print) -> dict:
    client = DataClient()
    first = start - timedelta(days=_WARMUP_DAYS)
    bars, events, failed, sectors = {}, {}, [], {}
    try:
        bench = data.load_bars(client, BENCHMARK, first, end)
        for sector, etf in SECTOR_ETF.items():
            try:
                sectors[sector] = {b["date"]: b["close"]
                                   for b in data.load_bars(client, etf, first, end)}
            except Exception as exc:                # noqa: BLE001
                failed.append((etf, str(exc)[:120]))
        for n, t in enumerate(tickers, 1):
            try:
                bars[t] = data.load_bars(client, t, first, end)
                events[t] = distinct_reports(data.earnings_filings(client, t, start))
            except Exception as exc:                # noqa: BLE001
                failed.append((t, str(exc)[:120]))
            if n % 25 == 0:
                say(f"  loaded {n} of {len(tickers)}")
    finally:
        client.close()
    return {"bars": bars, "events": events, "bench": bench, "failed": failed,
            "sector_close": sectors}


def _block(rows, sample, max_hold, exit_rule=None):
    summary = evaluate.summarize(rows)
    # Random entries get the same exit rule, or the comparison flatters the setup.
    base = evaluate.summarize(evaluate.random_baseline(
        rows, sample["bars"], sample["bench"], max_hold, draws=5 if len(rows) > 2000 else 20,
        exit_rule=exit_rule)) if rows else {"trades": 0}
    loo = evaluate.leave_one_ticker_out(rows) if rows else {}
    return {"summary": summary, "random": base, "leave_one_out": loo}


def evaluate_setup(name: str, signal_fn: SignalFn, variants: list[dict], sample: dict,
                   holdout_start: str, say=print, exit_rule: ExitRule | None = None) -> dict:
    spy_close = {b["date"]: b["close"] for b in sample["bench"]}
    results = {}
    for params in variants:
        sigs = [s for t, b in sample["bars"].items()
                for s in signal_fn(t, b, spy_close, sample["events"].get(t, []), params)]
        rows, rejected = evaluate.run(sigs, sample["bars"], sample["bench"], exit_rule)
        dev, held = evaluate.split(rows, holdout_start)
        out = {"params": params, "signals": len(sigs), "rejected": len(rejected),
               "all": _block(rows, sample, params["max_hold"], exit_rule),
               "development": _block(dev, sample, params["max_hold"], exit_rule),
               "holdout": _block(held, sample, params["max_hold"], exit_rule),
               "by_year": evaluate.by_year(rows)}
        d, h = out["development"], out["holdout"]
        out["verdict"] = evaluate.verdict(d["summary"], d["random"], d["leave_one_out"])
        hs, hr = h["summary"], h["random"]
        out["verdict"]["checks"]["held-out mean excess is positive"] = (
            (hs.get("mean_excess") or 0) > 0)
        out["verdict"]["checks"]["held-out beats random entries"] = (
            (hs.get("mean_excess") or 0) > (hr.get("mean_excess") or 0))
        out["verdict"]["passed"] = all(out["verdict"]["checks"].values())
        results[params["name"]] = out
        evaluate.log_experiment(f"{name}/{params['name']}",
                                {**params, "holdout_start": holdout_start,
                                 "tickers": len(sample["bars"])},
                                {"development": d["summary"], "holdout": hs,
                                 "random_dev": d["random"], "random_holdout": hr,
                                 "leave_one_out": d["leave_one_out"],
                                 "passed": out["verdict"]["passed"]})
        say(f"  {params['name']}: {len(rows)} trades, development mean excess "
            f"{d['summary'].get('mean_excess')}, held-out {hs.get('mean_excess')}, "
            f"{'PASS' if out['verdict']['passed'] else 'fail'}")
        trades_path = paths.ensure(paths.USER_DIR / data.RESEARCH_DIR / "trades"
                                   / f"{name}-{params['name']}.json")
        trades_path.write_text(json.dumps(rows), encoding="utf-8")
    return results


def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:+.2f}%"


def render(name: str, results: dict, meta: dict) -> str:
    out = [f"# {name}", "",
           f"{meta['tickers']} tickers, {meta['start']} to {meta['end']}. Development trades "
           f"closed before {meta['holdout_start']}; held-out trades opened on or after it. "
           "Next-open entries, frozen stops, gaps filled at the open, 5 bps a side.", ""]
    for variant, r in results.items():
        v = r["verdict"]
        out += [f"## {variant}: {'PASS' if v['passed'] else 'FAIL'}", "",
                f"`{r['params']}`", "",
                "| Sample | Trades | Tickers | Months | Win rate | Mean net | Mean excess vs SPY | 95% interval | Random entries, mean excess | Mean R | Avg sessions |",
                "|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|"]
        for label in ("development", "holdout", "all"):
            s, b = r[label]["summary"], r[label]["random"]
            if not s.get("trades"):
                out.append(f"| {label} | 0 | | | | | | | | | |")
                continue
            ci = s.get("mean_excess_ci95")
            out.append(
                f"| {label} | {s['trades']} | {s['tickers']} | {s['entry_months']} | "
                f"{s['win_rate']:.0%} | {_pct(s['mean_net'])} | {_pct(s['mean_excess'])} | "
                f"{(_pct(ci[0]) + ' to ' + _pct(ci[1])) if ci else 'n/a'} | "
                f"{_pct(b.get('mean_excess'))} | {s['mean_r']} | {s['mean_sessions']} |")
        loo = r["development"]["leave_one_out"]
        if loo:
            out += ["", f"Leave one ticker out (development): mean excess from "
                        f"{_pct(loo['min'])} (without {loo['min_without']}) to "
                        f"{_pct(loo['max'])} (without {loo['max_without']})."]
        out += ["", "Exits (all): " + ", ".join(
            f"{k} {n}" for k, n in sorted(r["all"]["summary"].get("exit_reasons", {}).items())),
            "", "| Year | Trades | Win rate | Mean net | Mean excess |", "|---|---:|---:|---:|---:|"]
        for y, s in r["by_year"].items():
            out.append(f"| {y} | {s['trades']} | {s['win_rate']:.0%} | {_pct(s['mean_net'])} | "
                       f"{_pct(s['mean_excess'])} |")
        out += ["", "Checks:"] + [f"- [{'x' if ok else ' '}] {c}" for c, ok in v["checks"].items()]
        out.append("")
    return "\n".join(out)
