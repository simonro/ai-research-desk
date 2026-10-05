"""The symbol card: who the company is, and the handful of numbers you check before reading a call.

Yahoo is the only free source for most of this (description, sector and industry, next
earnings date, dividend, 52-week range), so it is fetched once a day per symbol and cached
beside the dashboard. It is current data, not point-in-time: the card says when it was
fetched, and the run's own settled close and 52-week range come from the run, not from here.

Logos: Financial Modeling Prep's public image endpoint first, the company website's icon
second, and the page draws a letter tile when neither exists.
"""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

import validate

CACHE = Path(__file__).resolve().parent / "cache" / "profiles"
MAX_AGE = 24 * 3600
UA = {"User-Agent": "Mozilla/5.0 (desk dashboard)"}


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _day(v) -> str | None:
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    if isinstance(v, (int, float)) and v > 0:
        return datetime.fromtimestamp(v).date().isoformat()
    return None


def fetch(ticker: str) -> dict:
    import yfinance as yf

    t = yf.Ticker(ticker)
    info = t.info or {}
    try:
        cal = t.calendar or {}
    except Exception:
        cal = {}
    dates = cal.get("Earnings Date") or []
    if not isinstance(dates, (list, tuple)):
        dates = [dates]
    upcoming = [d for d in (_day(x) for x in dates) if d and d >= date.today().isoformat()]
    frac = _num(info.get("trailingAnnualDividendYield"))
    pct = _num(info.get("dividendYield"))
    dividend = frac if frac else (pct / 100.0 if pct else (0.0 if frac == 0 else None))
    first_trade = info.get("firstTradeDateMilliseconds")
    return {
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName") or ticker,
        "summary": info.get("longBusinessSummary") or "",
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "website": info.get("website"),
        "hq": ", ".join(x for x in (info.get("city"), info.get("state") or info.get("country")) if x) or None,
        "employees": info.get("fullTimeEmployees"),
        "exchange": info.get("fullExchangeName") or info.get("exchange"),
        "market_cap": _num(info.get("marketCap")),
        "pe_trailing": _num(info.get("trailingPE")),
        "pe_forward": _num(info.get("forwardPE")),
        "beta": _num(info.get("beta")),
        "avg_volume": _num(info.get("averageVolume")),
        "low_52w": _num(info.get("fiftyTwoWeekLow")),
        "high_52w": _num(info.get("fiftyTwoWeekHigh")),
        "price": _num(info.get("regularMarketPrice")),
        "dividend_yield": dividend,
        "dividend_rate": _num(info.get("dividendRate")),
        "ex_dividend": _day(info.get("exDividendDate")),
        "next_earnings": upcoming[0] if upcoming else None,
        "earnings_estimated": bool(info.get("isEarningsDateEstimate")),
        "eps_estimate": _num(cal.get("Earnings Average")),
        "listed_since": _day(first_trade / 1000) if first_trade else None,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def get(ticker: str, refresh: bool = False) -> dict:
    ticker = validate.ticker(ticker)             # this path is written as well as read
    CACHE.mkdir(parents=True, exist_ok=True)
    path = validate.inside(CACHE, f"{ticker}.json")
    if path.exists() and not refresh and time.time() - path.stat().st_mtime < MAX_AGE:
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        data = fetch(ticker)
    except Exception as exc:                       # stale beats nothing; nothing beats a crash
        if path.exists():
            return {**json.loads(path.read_text(encoding="utf-8")), "stale": str(exc)}
        return {"ticker": ticker, "name": ticker, "error": f"Yahoo profile unavailable: {exc}"}
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def logo(ticker: str, website: str | None) -> bytes | None:
    """PNG bytes, or None when no source has one (the page then draws a letter tile)."""
    ticker = validate.ticker(ticker)
    CACHE.mkdir(parents=True, exist_ok=True)
    path, miss = validate.inside(CACHE, f"{ticker}.png"), validate.inside(CACHE, f"{ticker}.nologo")
    if path.exists():
        return path.read_bytes()
    if miss.exists() and time.time() - miss.stat().st_mtime < MAX_AGE * 7:
        return None
    sources = [f"https://financialmodelingprep.com/image-stock/{ticker}.png"]
    if website:
        sources.append(f"https://www.google.com/s2/favicons?domain={urlparse(website).netloc}&sz=128")
    for url in sources:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=10) as r:
                body = r.read()
                if r.status == 200 and body[:8] == b"\x89PNG\r\n\x1a\n" and len(body) > 300:
                    path.write_bytes(body)
                    return body
        except Exception:
            continue
    miss.write_text("", encoding="utf-8")
    return None
