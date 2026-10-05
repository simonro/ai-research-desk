"""The lightweight event check, so a material event does not wait for Monday.

The weekly cadence is right for research and wrong for surprises. This runs in
seconds, uses no model, and answers one question per name: has anything happened
since the last run that would change the answer if the engine looked again?

Four triggers, all cheap:

* a new 10-Q or 10-K on the SEC filing index
* an earnings release the last run did not have
* a price move beyond what this stock normally does in the elapsed time
* a headline matching the handful of patterns that actually move a thesis

It flags names for a targeted rerun. It never reruns them itself, because a job
that quietly does expensive work when it finds something is a job nobody runs.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from edgedesk import run as run_mod
from edgedesk.jobs import universe as universe_mod
from edgedesk.providers.client import DataClient, settled_as_of

logger = logging.getLogger(__name__)

# A move this many times the stock's own daily volatility is not ordinary.
_MOVE_SIGMAS = 2.5
# Patterns worth waking up for. Deliberately short: a long list would match
# every day and the check would stop meaning anything.
_HEADLINES = re.compile(
    r"\b(guidance|guides|cuts? outlook|raises? outlook|withdraw|profit warning|"
    r"acquire|acquisition|merger|takeover|bid for|investigation|subpoena|probe|"
    r"recall|halt(?:ed|s)? trading|bankrupt|chapter 11|delisting|"
    r"(?:ceo|cfo) (?:steps down|resigns|departs|to retire)|"
    r"short seller|accounting)\b", re.I)


def check(tickers: list[str] | None = None, as_of: date | None = None,
          client: DataClient | None = None, say=print) -> list[dict]:
    """Every flagged ticker, with what tripped it."""
    tickers = tickers or universe_mod.researched()
    as_of = as_of or settled_as_of(None)
    own_client = client is None
    client = client or DataClient()
    flagged: list[dict] = []
    try:
        for ticker in tickers:
            previous = run_mod.previous(ticker, (as_of + timedelta(days=1)).isoformat())
            if previous is None:
                flagged.append({"ticker": ticker, "reasons": ["never analyzed"],
                                "since": None})
                continue
            reasons = _reasons(ticker, previous, as_of, client)
            if reasons:
                flagged.append({"ticker": ticker, "reasons": reasons,
                                "since": previous["as_of"]})
    finally:
        if own_client:
            client.close()

    if say:
        _report(flagged, tickers, as_of, say)
    return flagged


def _reasons(ticker: str, previous: dict, as_of: date, client: DataClient) -> list[str]:
    since = date.fromisoformat(previous["as_of"])
    if since >= as_of:
        return []
    reasons: list[str] = []
    ev = previous.get("evidence") or {}

    try:
        profile = client.profile(ticker) or {}
    except Exception as exc:                                # noqa: BLE001
        logger.info("%s: profile check failed (%s)", ticker, exc)
        profile = {}
    filed = (profile.get("latest_report") or {}).get("filed")
    known = ((ev.get("profile") or {}).get("latest_report") or {}).get("filed")
    if filed and filed > (known or ""):
        reasons.append(f"a {profile['latest_report'].get('form')} was filed on {filed}")

    try:
        release = client.latest_release(ticker, since, as_of)
    except Exception as exc:                                # noqa: BLE001
        logger.info("%s: release check failed (%s)", ticker, exc)
        release = None
    known_release = (ev.get("release") or {}).get("date")
    if release and release.day.isoformat() > (known_release or ""):
        reasons.append(f"earnings were reported on {release.day.isoformat()}")

    reasons += _price_reason(ticker, previous, since, as_of, client)

    try:
        news = client.news(ticker, as_of, since, limit=40)
    except Exception as exc:                                # noqa: BLE001
        logger.info("%s: news check failed (%s)", ticker, exc)
        news = []
    for item in news:
        if _HEADLINES.search(item.title or ""):
            reasons.append(f"headline: {item.title}")
            break
    return reasons


def _price_reason(ticker, previous, since, as_of, client) -> list[str]:
    """A move large enough to be news, measured against this stock's own noise.

    Scaled by the stock's own volatility and by how long has passed, so a quiet
    utility and a volatile semiconductor are held to different bars and a
    fortnight is not judged like a day.
    """
    anchors = (previous.get("evidence") or {}).get("anchors") or {}
    before = anchors.get("last_close")
    annual_vol = anchors.get("volatility_annual")
    if not before or not annual_vol:
        return []
    try:
        bars = client.daily_closes(ticker, since, as_of)
    except Exception as exc:                                # noqa: BLE001
        logger.info("%s: price check failed (%s)", ticker, exc)
        return []
    if not bars:
        return []
    now = bars[-1]["close"]
    move = now / before - 1
    sessions = max(len(bars), 1)
    expected = annual_vol * (sessions / 252) ** 0.5
    if expected and abs(move) >= _MOVE_SIGMAS * expected:
        return [f"price moved {move:+.1%} in {sessions} sessions, against the "
                f"{expected:.1%} this stock normally covers in that time"]
    return []


def _report(flagged, tickers, as_of, say) -> None:
    say(f"Event check for {len(tickers)} names, as of {as_of}.")
    if not flagged:
        say("Nothing material since the last run. Next full look is the weekly job.")
        return
    say(f"{len(flagged)} flagged for a targeted rerun:")
    for item in flagged:
        say(f"  {item['ticker']} (last analyzed {item['since'] or 'never'})")
        for reason in item["reasons"]:
            say(f"    - {reason}")
    names = " ".join(item["ticker"] for item in flagged)
    say("")
    say(f"To act on it: engine analyze {names} --llm")
