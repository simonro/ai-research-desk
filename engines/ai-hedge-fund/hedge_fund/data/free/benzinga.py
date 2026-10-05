"""Earnings surprises from Benzinga Newsdesk headlines on Alpaca's news feed.

Benzinga posts one templated headline per earnings release, seconds after it
crosses the wire:

    Microsoft Q4 Adj. EPS $4.74 Beats $4.24 Estimate, Sales $90.007B Beat $87.621B Estimate
    Microsoft Reports Q2 Adj. EPS $0.96 vs $0.86 Est.            (older template)

That gives actual vs consensus EPS and revenue with a to-the-second public
timestamp, on a feed that is already paid for and not rate limited like
Yahoo. Parsing is strict: a headline that does not match the template, or
whose GAAP figure "May Not Compare" to an adjusted estimate, is ignored rather
than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable

_NUMBER = r"\(?-?\$?\(?-?[\d,]*\.?\d+\)?"
_UNIT = r"[KMBT]?"

_EPS_RE = re.compile(
    r"\bQ[1-4]\b.*?"
    r"(?P<kind>Adj(?:usted)?\.?|Non-GAAP|Core|GAAP)?\s*EPS\s+"
    r"(?P<eps>" + _NUMBER + r")\s+"
    r"(?P<verb>Beats|Beat|Misses|Miss|In-Line With|Inline With|Meets|Matches|vs\.?|May Not Compare To)\s+"
    r"(?P<est>" + _NUMBER + r")\s+(?:Estimate|Est\b\.?|Consensus)",
    re.IGNORECASE,
)
_SALES_RE = re.compile(
    r"\b(?:Sales|Revenue)\s+(?P<rev>" + _NUMBER + r")(?P<rev_unit>" + _UNIT + r")\s+"
    r"(?:Beats|Beat|Misses|Miss|In-Line With|Inline With|Meets|Matches|vs\.?)\s+"
    r"(?P<est>" + _NUMBER + r")(?P<est_unit>" + _UNIT + r")\s+(?:Estimate|Est\b\.?|Consensus)",
    re.IGNORECASE,
)
_UNITS = {"": 1.0, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}

# Adjusted EPS is what the Street estimates; plain "EPS" next; GAAP last.
_KIND_RANK = {"adj": 0, "adj.": 0, "adjusted": 0, "adjusted.": 0, "non-gaap": 0, "core": 0,
              "": 1, "gaap": 2}


@dataclass(frozen=True)
class Release:
    """One earnings release as Benzinga reported it."""

    published: datetime
    eps: float
    eps_estimate: float
    revenue: float | None = None
    revenue_estimate: float | None = None
    kind_rank: int = 1

    @property
    def day(self) -> date:
        return self.published.date()


def parse_headline(headline: str, published: datetime) -> Release | None:
    m = _EPS_RE.search(headline)
    if m is None or m.group("verb").lower().startswith("may not"):
        return None
    eps, est = _to_number(m.group("eps")), _to_number(m.group("est"))
    if eps is None or est is None:
        return None
    revenue = revenue_estimate = None
    s = _SALES_RE.search(headline)
    if s is not None:
        revenue = _scaled(s.group("rev"), s.group("rev_unit"))
        revenue_estimate = _scaled(s.group("est"), s.group("est_unit"))
    kind = (m.group("kind") or "").lower()
    return Release(published=published, eps=eps, eps_estimate=est, revenue=revenue,
                   revenue_estimate=revenue_estimate, kind_rank=_KIND_RANK.get(kind, 1))


def first_release(items: Iterable[dict], ticker: str) -> Release | None:
    """The release in a newest-first stream of news items, or None.

    Stops reading as soon as the stream moves a day past a match, so a caller
    paging through a busy name's news pays for a page or two, not a quarter.
    Several templated headlines can land for one release (adjusted and GAAP);
    the adjusted one wins.
    """
    symbol = ticker.upper().replace("-", ".")
    found: list[Release] = []
    for item in items:
        published = _parse_ts(item.get("created_at"))
        if published is None:
            continue
        if found and published.date() < found[0].day - timedelta(days=1):
            break
        symbols = [s.upper() for s in item.get("symbols") or []]
        # A roundup naming many tickers is not this company's release.
        if symbol not in symbols or len(symbols) > 3:
            continue
        release = parse_headline(item.get("headline") or "", published)
        if release is not None:
            found.append(release)
    if not found:
        return None
    return min(found, key=lambda r: (r.kind_rank, r.published))


def _to_number(text: str) -> float | None:
    negative = "(" in text or "-" in text
    digits = re.sub(r"[^\d.]", "", text)
    if not digits or digits == ".":
        return None
    value = float(digits)
    return -value if negative else value


def _scaled(text: str, unit: str) -> float | None:
    value = _to_number(text)
    return None if value is None else value * _UNITS.get(unit.upper(), 1.0)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
