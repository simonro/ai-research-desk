"""The weekly job: the whole watchlist, one run each, one summary at the end.

Ordering matters here. Every deterministic run happens first and finishes fast,
so if the written analysis is slow or the CLI falls over, the week still has its
ratings, levels and reports. The analysis layer is then added on top, name by
name, and a failure there costs a paragraph rather than the run.

The summary at the end is the thing actually read on a Monday: what changed,
what is actionable now, whose thesis broke, and what was withheld and why.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from edgedesk import changes, reports, run as run_mod
from edgedesk.calibrate.harness import market_is_open
from edgedesk.jobs import universe as universe_mod
from edgedesk.paths import ensure
from edgedesk.providers.client import DataClient, settled_as_of
from edgedesk.reports.common import pct, price
from edgedesk.verdict.published import RATED_HORIZONS, published

logger = logging.getLogger(__name__)

# The analysis layer spawns one CLI process per call. A few at once is a good
# use of a weekly window; more starts competing for the same subscription.
_LLM_WORKERS = 3


class MarketOpen(RuntimeError):
    """Wide jobs share a rate limit with the live trading bot."""


def run_week(as_of: date | None = None, llm: bool = True, force: bool = False,
             out_dir=None, vault: bool = False, say=print) -> dict:
    universe = universe_mod.load()
    tickers = universe_mod.researched(universe)
    if not tickers:
        say(f"Nothing to do: {universe_mod.describe(universe)}.")
        say("Add tickers to owned or watchlist in ~/.edge-desk/universe.yaml.")
        return {"tickers": 0}
    if market_is_open() and not force:
        raise MarketOpen(
            "The market is open. This job makes a lot of requests on the key the live "
            "trading bot shares, so it runs outside 09:30-16:00 ET. Use --force if the "
            "bot is not running.")

    as_of = as_of or settled_as_of(None)
    owned = set(universe["owned"])
    say(f"Week of {as_of}: {universe_mod.describe(universe)}")

    runs: dict[str, dict] = {}
    client = DataClient()
    try:
        for ticker in tickers:
            try:
                run = run_mod.analyze(ticker, as_of, client=client,
                                      owns=ticker in owned, save=True)
            except Exception as exc:                        # noqa: BLE001
                say(f"  {ticker}: failed ({exc})")
                continue
            run["changes"] = changes.what_changed(run)
            if ticker in owned:
                run["thesis_health"] = changes.thesis_health(
                    run, since=universe.get("owned_since", {}).get(ticker))
            run_mod.write(run)
            runs[ticker] = run
            say(f"  {ticker}: {_one_line(run)}")
    finally:
        client.close()

    if llm and runs:
        say(f"Writing the analysis for {len(runs)} names...")
        _add_analysis(runs, say)
        for run in runs.values():
            run_mod.write(run)

    if out_dir or vault:
        _publish(runs, out_dir, vault, say)

    summary = render_summary(runs, as_of, owned)
    if out_dir:
        path = ensure(out_dir / f"week-{as_of}.md")
        path.write_text(summary, encoding="utf-8")
        say(f"Summary written to {path}")
    return {"tickers": len(runs), "as_of": as_of.isoformat(), "runs": runs,
            "summary": summary}


def _add_analysis(runs: dict[str, dict], say) -> None:
    """The written layer, a few names at a time.

    Each call is its own process, so concurrency here is real rather than
    interleaved. A failure is logged and the name keeps its numbers.
    """
    from edgedesk.llm.analysis import attach

    def one(item):
        ticker, run = item
        try:
            attach(run)
            state = "written" if (run.get("llm") or {}).get("ok") else "not written"
        except Exception as exc:                            # noqa: BLE001
            run["llm"] = {"ok": False, "error": str(exc)}
            state = f"failed ({exc})"
        return ticker, state

    with ThreadPoolExecutor(max_workers=_LLM_WORKERS) as pool:
        for ticker, state in pool.map(one, list(runs.items())):
            say(f"  {ticker}: {state}")


def _publish(runs: dict[str, dict], out_dir, vault: bool, say) -> None:
    from edgedesk.reports import vault as vault_mod

    for ticker, run in runs.items():
        if out_dir:
            for fmt in ("scorecard", "condensed", "full", "card"):
                reports.write(run, fmt, out_dir)
        if vault:
            written = vault_mod.write(run)
            if written is None:
                say("  no Obsidian vault on this machine; skipping notes")
                vault = False


def _one_line(run: dict) -> str:
    sig = (run.get("swing") or {}).get("call") or "no call"
    lt = published(run, "long_term").get("rating") or "withheld"
    case = (run.get("long_term") or {}).get("call") or "none"
    changed = (run.get("changes") or {}).get("summary", "")
    return f"swing {sig}, long-term case {case} (screen {lt}). {changed}"


# ---------------------------------------------------------------------------

def render_summary(runs: dict[str, dict], as_of: date, owned: set[str]) -> str:
    out = [f"# Week of {as_of}", "",
           f"{len(runs)} names analyzed. Research only: no orders, no advice.", ""]

    withheld = [t for t, r in runs.items()
                if all(published(r, h).get("quality_state") == "WITHHELD"
                       for h in ("swing", "long_term"))]
    actionable = [(t, r) for t, r in runs.items()
                  if any((r.get("opportunity") or {}).get(h, {}).get("state")
                         == "Actionable now" for h in ("swing", "long_term"))
                  and t not in withheld]
    moved = [(t, r) for t, r in runs.items() if (r.get("changes") or {}).get("ratings")]
    broken = [(t, r) for t, r in runs.items()
              if (r.get("thesis_health") or {}).get("broken")]

    buys = sorted((t, r) for t, r in runs.items() if (r.get("swing") or {}).get("call") == "Buy")
    out += ["## Swing buys", ""]
    if buys:
        for ticker, run in buys:
            s, p = run["swing"], run["swing"].get("plan") or {}
            out.append(f"- **{ticker}** score {s['score']:.0f}: entry {p.get('entry')}, "
                       f"invalidation {p.get('invalidation')}, targets {p.get('target_1')} and "
                       f"{p.get('target_2')}, {p.get('shares')} shares for $1,000 of risk. "
                       f"{s['why']}")
    else:
        out.append("- No swing Buy this week.")
    out.append("")

    out += ["## Actionable now", ""]
    if actionable:
        for ticker, run in sorted(actionable):
            for horizon in ("swing", "long_term"):
                opp = (run.get("opportunity") or {}).get(horizon) or {}
                if opp.get("state") != "Actionable now":
                    continue
                verdict = published(run, horizon)
                lead = (verdict.get("rating") if horizon in RATED_HORIZONS
                        else (run.get("signal") or {}).get("signal"))
                action = (verdict.get("action") or opp.get("action") or "").lower()
                out.append(f"- **{ticker}** {horizon.replace('_', ' ')}: "
                           f"{lead}, {action}. {opp['why']}")
    else:
        out.append("- Nothing is at an actionable price this week.")
    out.append("")

    out += ["## Rating changes", ""]
    if moved:
        for ticker, run in sorted(moved):
            for change in run["changes"]["ratings"]:
                out.append(f"- **{ticker}** {change['horizon'].replace('_', ' ')}: "
                           f"{change['from']} to {change['to']} "
                           f"(score {change['score_from']} to {change['score_to']})")
    else:
        out.append("- No rating moved this week.")
    out.append("")

    if broken:
        out += ["## Thesis conditions broken", "",
                "These were written down when the position was first analyzed, and are "
                "checked against today's filings. A broken condition is a reason to look, "
                "not an instruction.", ""]
        for ticker, run in sorted(broken):
            health = run["thesis_health"]
            out.append(f"- **{ticker}**: {health['summary']}")
            for condition in health["conditions"]:
                if condition["status"] == "broken":
                    out.append(f"  - {condition['metric']}: {condition['detail']}")
        out.append("")

    out += ["## Everything", "",
            "| Ticker | Swing | LT case | LT screen | Price | Week | Fair value | State |",
            "|---|---|---|---|---:|---:|---:|---|"]
    for ticker, run in sorted(runs.items()):
        sw = (run.get("swing") or {}).get("call") or "no call"
        lt = published(run, "long_term").get("rating") or "withheld"
        close = ((run.get("evidence") or {}).get("anchors") or {}).get("last_close")
        week = (run.get("changes") or {}).get("price", {}).get("change")
        fv = ((run.get("valuation") or {}).get("fair_value") or {}).get("value")
        state = (run.get("opportunity") or {}).get("long_term", {}).get("state", "")
        held = " (held)" if ticker in owned else ""
        case = run.get("long_term") or {}
        exp = (case.get("scenario_return") or {}).get("expected_annual_return")
        case_cell = (f"{case.get('call')} ({exp * 100:+.0f}%/yr)" if case.get("call") and exp is not None
                     else case.get("call") or "none")
        out.append(f"| {ticker}{held} | {sw} | {case_cell} | {lt} | {price(close)} | "
                   f"{pct(week) if week is not None else 'n/a'} | {price(fv)} | {state} |")
    out.append("")

    if withheld:
        out += ["## Withheld", "",
                "No rating was published for these, and the reason is a data problem "
                "rather than a view.", ""]
        for ticker in sorted(withheld):
            reasons = (published(runs[ticker], "long_term")).get("reasons") or []
            out.append(f"- **{ticker}**: {reasons[0] if reasons else 'see the run file'}")
        out.append("")
    return "\n".join(out)
