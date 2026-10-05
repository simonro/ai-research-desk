"""The wide scan: deterministic scores over a long list, no model, no cost.

This is how a name earns its way onto the watchlist. It runs the same evidence
and factor layers as everything else, prints a ranked table, and stops. Nothing
is promoted automatically, because a list that promotes itself is a list nobody
reads before acting on.

It also writes its runs like any other, so a name promoted next month already
has weeks of history and an entry snapshot waiting for it.
"""

from __future__ import annotations

import logging
from datetime import date

from edgedesk import run as run_mod
from edgedesk.calibrate.harness import market_is_open
from edgedesk.jobs import universe as universe_mod
from edgedesk.jobs.week import MarketOpen
from edgedesk.reports.common import pct, price
from edgedesk.providers.client import DataClient, settled_as_of
from edgedesk.verdict.rating import WITHHELD
from edgedesk.verdict.published import published

logger = logging.getLogger(__name__)


def scan(tickers: list[str] | None = None, as_of: date | None = None,
         horizon: str = "long_term", top: int = 25, force: bool = False,
         save: bool = True, say=print) -> list[dict]:
    universe = universe_mod.load()
    tickers = tickers or universe["large_cap"]
    if not tickers:
        say("No large-cap list configured. Add tickers under large_cap in "
            "~/.edge-desk/universe.yaml.")
        return []
    if market_is_open() and not force:
        raise MarketOpen(
            "The market is open. A wide scan makes a lot of requests on the key the live "
            "trading bot shares, so it runs outside 09:30-16:00 ET. Use --force if the "
            "bot is not running.")

    as_of = as_of or settled_as_of(None)
    already = set(universe_mod.researched(universe))
    rows: list[dict] = []
    client = DataClient()
    try:
        for ticker in tickers:
            try:
                run = run_mod.analyze(ticker, as_of, client=client, save=save)
            except Exception as exc:                        # noqa: BLE001
                say(f"  {ticker}: failed ({exc})")
                continue
            verdict = published(run, horizon)
            rows.append({
                "ticker": ticker,
                "rating": verdict.get("rating"),
                "score": verdict.get("score"),
                "state": (run.get("opportunity") or {}).get(horizon, {}).get("state"),
                "quality_state": verdict.get("quality_state"),
                "price": ((run.get("evidence") or {}).get("anchors") or {})
                .get("last_close"),
                "fair_value": ((run.get("valuation") or {})
                               .get("fair_value") or {}).get("value"),
                "upside": ((run.get("valuation") or {})
                           .get("fair_value") or {}).get("upside"),
                "risk": (run.get("factors") or {}).get("risk", {}).get("score"),
                "already_covered": ticker in already,
            })
    finally:
        client.close()

    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    if say:
        say(render(rows, as_of, horizon, top))
    return rows


def render(rows: list[dict], as_of: date, horizon: str, top: int) -> str:
    label = horizon.replace("_", " ")
    scored = [r for r in rows if r["quality_state"] != WITHHELD and r["score"] is not None]
    withheld = [r for r in rows if r["quality_state"] == WITHHELD or r["score"] is None]

    out = [f"# Scan, {label}, {as_of}", "",
           f"{len(scored)} scored, {len(withheld)} withheld. Deterministic only: no "
           "written analysis was produced and nothing here cost anything to run.", "",
           "Nothing is promoted automatically. Move a name into the watchlist by hand if "
           "it is worth the attention.", "",
           "| # | Ticker | Rating | Score | Price | Fair value | Upside | Risk | State |",
           "|---:|---|---|---:|---:|---:|---:|---:|---|"]
    for i, row in enumerate(scored[:top], start=1):
        mark = " *" if row["already_covered"] else ""
        out.append(
            f"| {i} | {row['ticker']}{mark} | {row['rating']} | {row['score']:.1f} | "
            f"{price(row['price'])} | {price(row['fair_value'])} | "
            f"{pct(row['upside']) if row['upside'] is not None else 'n/a'} | "
            f"{row['risk']:.0f} | {row['state']} |" if row["risk"] is not None else
            f"| {i} | {row['ticker']}{mark} | {row['rating']} | {row['score']:.1f} | "
            f"{price(row['price'])} | {price(row['fair_value'])} | "
            f"{pct(row['upside']) if row['upside'] is not None else 'n/a'} | n/a | "
            f"{row['state']} |")
    out += ["", "`*` already owned or on the watchlist.", ""]
    if withheld:
        out += ["Withheld: " + ", ".join(r["ticker"] for r in withheld)
                + ". Run one of them directly to see why.", ""]
    out.append("The score is a consistent screen, not a measured forecast. Between 50 and 79 "
               "it showed no ordering at all, so treat the middle of this table as unranked.")
    return "\n".join(out)
