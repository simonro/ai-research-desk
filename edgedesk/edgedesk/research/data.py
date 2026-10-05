"""Bars and earnings dates for a wide, long research sample, fetched once.

Two hundred names over seven years is a few hundred requests, made outside
market hours on the key the live trading bot shares, REST only. Everything lands
on disk under ~/.edge-desk/research/ so a second experiment costs nothing.

Bars go through `DataClient.bars`, so a split the feed forgot to adjust is
repaired here exactly as it is in a live run.

Earnings dates come from the SEC, not from a news feed: an 8-K carrying item 2.02
("Results of Operations") is the legal announcement, and the submissions index
records the second it was accepted. That second decides which session was the
market's first chance to react: accepted before the close means that same
session, accepted after it means the next one.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

from edgedesk import paths
from edgedesk.providers.client import DataClient
from edgedesk.providers.edgar import SUBMISSIONS_URL

logger = logging.getLogger(__name__)

RESEARCH_DIR = "research"
_ET = ZoneInfo("America/New_York")
_CLOSE = dtime(16, 0)
_ARCHIVE_URL = "https://data.sec.gov/submissions/{name}"
_LONG_TTL = 7 * 24 * 3600.0


def _dir(kind: str):
    return paths.USER_DIR / RESEARCH_DIR / kind


def load_bars(client: DataClient, ticker: str, start: date, end: date) -> list[dict]:
    """Adjusted daily bars, oldest first, from disk when the range is covered."""
    path = _dir("bars") / f"{ticker.upper()}.json"
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved["start"] <= start.isoformat() and saved["end"] >= end.isoformat():
                return [b for b in saved["bars"]
                        if start.isoformat() <= b["date"] <= end.isoformat()]
        except (OSError, ValueError, KeyError):
            pass
    bars = client.daily_closes(ticker, start, end)
    paths.ensure(path).write_text(json.dumps(
        {"ticker": ticker.upper(), "start": start.isoformat(), "end": end.isoformat(),
         "repairs": client.split_repairs.get(ticker.upper(), []), "bars": bars}),
        encoding="utf-8")
    return bars


def earnings_filings(client: DataClient, ticker: str, start: date) -> list[dict]:
    """Every 8-K with item 2.02 since *start*, oldest first:
    `{"accepted": ISO UTC, "filed": date, "after_close": bool}`."""
    path = _dir("earnings") / f"{ticker.upper()}.json"
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved["start"] <= start.isoformat() and saved["fetched"] == date.today().isoformat():
                return saved["events"]
        except (OSError, ValueError, KeyError):
            pass

    edgar = client.edgar
    cik = edgar.cik(ticker)
    if cik is None:
        return []
    body = edgar._cached_json(SUBMISSIONS_URL.format(cik=cik),
                              f"submissions-{cik:010d}.json", _LONG_TTL)
    if body is None:
        return []
    filings = body.get("filings") or {}
    pages = [filings.get("recent") or {}]
    for extra in filings.get("files") or []:
        if (extra.get("filingTo") or "") >= start.isoformat():
            page = edgar._cached_json(_ARCHIVE_URL.format(name=extra["name"]),
                                      extra["name"], 365 * 24 * 3600.0)
            if page:
                pages.append(page)

    events = {}
    for page in pages:
        for form, items, filed, accepted in zip(
                page.get("form", []), page.get("items", []), page.get("filingDate", []),
                page.get("acceptanceDateTime", [])):
            if form != "8-K" or "2.02" not in (items or "") or filed < start.isoformat():
                continue
            try:
                when = datetime.fromisoformat(accepted.replace("Z", "+00:00")).astimezone(_ET)
            except (ValueError, AttributeError):
                continue
            events[accepted] = {"accepted": accepted, "filed": when.date().isoformat(),
                                "after_close": when.time() >= _CLOSE}
    out = sorted(events.values(), key=lambda e: e["accepted"])
    paths.ensure(path).write_text(json.dumps(
        {"ticker": ticker.upper(), "start": start.isoformat(),
         "fetched": date.today().isoformat(), "events": out}), encoding="utf-8")
    return out


def reaction_index(bars: list[dict], event: dict) -> int | None:
    """The first session in which the market could trade on the announcement."""
    day = event["filed"]
    for i, b in enumerate(bars):
        if b["date"] > day or (b["date"] == day and not event["after_close"]):
            return i
    return None
