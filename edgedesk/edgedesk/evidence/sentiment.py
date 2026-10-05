"""Sentiment from what the package already holds, with no model in the path.

Three readings, all deterministic so the same run always gives the same answer:

* the Street: upside to the mean price target, and the buy/hold/sell split
* analyst actions in the last 30 days: targets raised against targets lowered
* headline tone over the last 14 days: a plain keyword count

None of this has been tested against forward returns, because none of it has
free history. It is included because the author asked for it and because it is how a
person reads a name; its weight in the swing call is small and the report says
"untested" beside it. It earns more weight only by being right going forward.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

_POSITIVE = ("beats", "beat", "raises", "raised", "upgrade", "upgrades", "upgraded", "record",
             "surges", "soars", "jumps", "rallies", "strong", "tops", "outperform", "buyback",
             "wins", "approval", "approves", "expands", "accelerat", "breakthrough", "bullish",
             "initiates coverage with buy", "overweight", "guidance above", "lifts")
_NEGATIVE = ("misses", "miss", "cuts", "cut", "lowers", "lowered", "downgrade", "downgrades",
             "downgraded", "plunges", "falls", "slumps", "tumbles", "weak", "probe", "lawsuit",
             "investigation", "recall", "layoffs", "warns", "warning", "bearish", "underperform",
             "underweight", "guidance below", "delays", "halts", "fraud", "short seller",
             "sec charges", "resigns")
_WORD = re.compile(r"[a-z][a-z ]+")


def headline_tone(news: list[dict], as_of: str, days: int = 14) -> dict:
    cutoff = (date.fromisoformat(as_of) - timedelta(days=days)).isoformat()
    recent = [n for n in news or [] if cutoff <= (n.get("date") or "")[:10] <= as_of]
    pos = neg = 0
    hits: list[str] = []
    for n in recent:
        title = (n.get("title") or "").lower()
        p = sum(1 for w in _POSITIVE if w in title)
        q = sum(1 for w in _NEGATIVE if w in title)
        pos += 1 if p > q else 0
        neg += 1 if q > p else 0
        if p != q and len(hits) < 4:
            hits.append(("+ " if p > q else "- ") + (n.get("title") or "")[:110])
    total = pos + neg
    return {"headlines": len(recent), "positive": pos, "negative": neg,
            "tone": round((pos - neg) / (total + 2), 3) if recent else None,
            "examples": hits}


def read(ev_dict: dict, panel: dict | None) -> dict:
    """Everything the swing call needs about sentiment, in one place."""
    consensus = ev_dict.get("consensus") or {}
    street = (panel or {}).get("street") or {}
    changes = (panel or {}).get("target_changes") or {}
    as_of = ev_dict["as_of"]
    cutoff = (date.fromisoformat(as_of) - timedelta(days=30)).isoformat()
    recent = [c for c in changes.get("latest") or [] if (c.get("date") or "") >= cutoff]
    return {
        "upside_to_mean_target": street.get("upside_to_mean"),
        "recommendation": consensus.get("recommendation_key") or consensus.get("recommendation"),
        "targets_raised_30d": sum(1 for c in recent if c["direction"] == "raised"),
        "targets_lowered_30d": sum(1 for c in recent if c["direction"] == "lowered"),
        "news": headline_tone(ev_dict.get("news") or [], as_of),
    }
