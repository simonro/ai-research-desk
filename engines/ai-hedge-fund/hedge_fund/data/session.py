"""US equity session timing: when today's daily bar is final.

During market hours a daily bar for today exists but moves every minute, so a
"latest close" taken from it is really the live price: two runs an hour apart
see different valuations, the agents are asked again, and their calls wobble.
Until the session has settled, "latest close" means the previous session's.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
# The 16:00 close auction prints within minutes; 16:15 leaves room for the
# consolidated daily bar to settle. Early-close days just wait a little longer.
SETTLED_AT = time(16, 15)


def last_settled_day(now: datetime | None = None) -> date:
    """The latest calendar day whose daily bar is final (non-trading days simply have no bar)."""
    now_et = (now or datetime.now(ET)).astimezone(ET)
    return now_et.date() if now_et.time() >= SETTLED_AT else now_et.date() - timedelta(days=1)


def settled_since(written_at: float, now: datetime | None = None) -> bool:
    """True if today's session settled after *written_at* (a POSIX timestamp): anything
    cached before the close that reaches today must be refetched."""
    now_et = (now or datetime.now(ET)).astimezone(ET)
    settled = datetime.combine(now_et.date(), SETTLED_AT, tzinfo=ET)
    return now_et >= settled > datetime.fromtimestamp(written_at, ET)
