"""One rule for every ticker and date that reaches a file path.

Tickers and dates arrive in query strings and become file names under memos/ and the profile
cache. Unchecked, a value like `../../memos/X` walks out of those folders, and the profile
endpoint writes as well as reads, so a bad value is refused before it gets near a path.
ASCII only on purpose: str.isalpha() accepts any Unicode letter and a regex digit class any Unicode digit.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

_TICKER = re.compile(r"[A-Z][A-Z.]{0,5}")          # ECG, BRK.B; what the run form has always taken
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class BadInput(ValueError):
    """A ticker, date or path the dashboard refuses to touch. The server answers 400."""


def ticker(value) -> str:
    t = str(value or "").strip().upper()
    if not _TICKER.fullmatch(t):
        raise BadInput("Enter a plain ticker symbol, like ECG.")
    return t


def day(value) -> str:
    d = str(value or "").strip()
    if not _DATE.fullmatch(d):
        raise BadInput("Dates look like 2026-09-18.")
    try:
        date.fromisoformat(d)
    except ValueError:
        raise BadInput(f"{d} is not a real date.") from None
    return d


def inside(root: Path, name: str) -> Path:
    """root/name, refused unless it resolves to a file directly inside root. The ticker rule
    already makes an escape impossible; this is the second lock, at the point of use."""
    # A separator of either kind is refused outright: on Linux a backslash is an ordinary
    # character, so "..\a.json" would otherwise pass as a file name.
    if "/" in name or "\\" in name:
        raise BadInput("That path is outside the folder it belongs in.")
    base = root.resolve()
    path = (base / name).resolve()
    if path.parent != base:
        raise BadInput("That path is outside the folder it belongs in.")
    return path
