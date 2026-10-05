"""The one price date every team uses (decision 2, 2026-09-25: close-only prices).

A run can start while the market is open. Before this, each team picked its own price date: the
Veterans' data client stopped at the last settled close, while Edge Desk and the Quant desk took
today's live bar, so one run mixed Sep 23's close with Sep 24's midday price. Now the desk decides
the session once, before any team starts, and passes it to all three as their as-of date.
TradingAgents already clamps every data tool to that date; Edge Desk clamps too.

New York time comes from zoneinfo. (Git Bash on Windows can ignore TZ and print UTC.)
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SETTLED_AT = time(16, 15)      # the 16:00 close auction prints within minutes; the same rule as ai-hedge-fund


def last_settled_day(now: datetime | None = None) -> date:
    """The latest calendar day whose daily bar is final. Non-trading days simply have no bar."""
    now_et = (now or datetime.now(ET)).astimezone(ET)
    return now_et.date() if now_et.time() >= SETTLED_AT else now_et.date() - timedelta(days=1)


def price_session(run_date: str, now: datetime | None = None) -> str:
    """The session whose close every team prices from: the run's date, or the last settled
    session if that is earlier (a run dated today, started before 16:15 ET)."""
    return min(date.fromisoformat(run_date[:10]), last_settled_day(now)).isoformat()


def price_check(session: str, reports: dict[str, dict]) -> dict:
    """What each team actually priced from, against the session it was given. Teams report the
    date of their last bar; a holiday or weekend makes that earlier than the session, which is
    fine, but the teams must agree and none may be later."""
    seen = {}
    for code, name in (("B", "veterans"), ("C", "edge")):
        payload = reports.get(code) or {}
        day = (payload.get("anchors") or {}).get("as_of")
        if day:
            seen[name] = str(day)[:10]
    later = sorted(n for n, d in seen.items() if d > session)
    consistent = not later and len(set(seen.values())) <= 1
    return {"session": session, "teams": seen, "consistent": consistent,
            "problem": None if consistent else (
                f"{', '.join(later)} priced after the session" if later else
                "teams priced from different sessions: " + ", ".join(f"{n} {d}" for n, d in seen.items()))}
