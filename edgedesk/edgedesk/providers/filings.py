"""The words in a filing, not only its numbers.

The XBRL feed carries what a company reported. It does not carry what management
said about it: which segment is growing, what is squeezing margins, what changed
in the risk factors. That is in the 10-K and 10-Q themselves, so this fetches the
newest of each from EDGAR, strips the markup, and cuts out the sections a reader
goes to first.

Only filings public by the as-of date are used, and each is cached on disk by
accession number: a filing never changes once filed.
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from datetime import date

from edgedesk.providers.edgar import SUBMISSIONS_URL

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"</(p|div|tr|li|h\d|table)>|<br\s*/?>", re.I)
_HIDDEN = re.compile(r"<(script|style|ix:header)[^>]*>.*?</\1>", re.I | re.S)
_SPACE = re.compile(r"[ \t\xa0]+")

# Section boundaries. A filing's table of contents repeats every heading, so the
# LONGEST span between a start and the next end is taken: the contents entry is a
# few words long and the real section is thousands.
_SECTIONS = {
    "10-K": {"mdna": (r"item\s*7\.?\s*management", r"item\s*7a\.?|item\s*8\.?\s*financial"),
             "risk_factors": (r"item\s*1a\.?\s*risk\s*factors", r"item\s*1b\.?|item\s*2\.?\s*properties")},
    "10-Q": {"mdna": (r"item\s*2\.?\s*management", r"item\s*3\.?\s*quantitative|item\s*4\.?\s*controls"),
             "risk_factors": (r"item\s*1a\.?\s*risk\s*factors", r"item\s*2\.?\s*unregistered|item\s*5\.?|item\s*6\.?")},
}


def to_text(markup: str) -> str:
    body = _HIDDEN.sub(" ", markup)
    body = _BLOCK.sub("\n", body)
    body = html_lib.unescape(_TAG.sub(" ", body))
    lines = [_SPACE.sub(" ", line).strip() for line in body.splitlines()]
    return "\n".join(line for line in lines if line)


def section(text: str, form: str, name: str) -> str:
    start_re, end_re = _SECTIONS[form][name]
    best = ""
    for m in re.finditer(start_re, text, re.I):
        end = re.search(end_re, text[m.end():], re.I)
        span = text[m.start(): m.end() + end.start()] if end else text[m.start(): m.start() + 120_000]
        if len(span) > len(best):
            best = span
    return best


def latest(edgar, ticker: str, as_of: date, forms=("10-Q", "10-K"),
           problems: list | None = None) -> list[dict]:
    """The newest filing of each form public by *as_of*: form, dates, url, text. What went
    wrong on the way, if anything, is appended to *problems* so the caller can say it."""
    problems = problems if problems is not None else []
    cik = edgar.cik(ticker)
    if cik is None:
        problems.append({"why": f"{ticker} is not in SEC's ticker list"})
        return []
    body = edgar._cached_json(SUBMISSIONS_URL.format(cik=cik),
                              f"submissions-{cik:010d}.json", 12 * 3600.0)
    recent = ((body or {}).get("filings") or {}).get("recent") or {}
    out = []
    for want in forms:
        for form, filed, period, accession, document in zip(
                recent.get("form", []), recent.get("filingDate", []),
                recent.get("reportDate", []), recent.get("accessionNumber", []),
                recent.get("primaryDocument", [])):
            if form != want or filed > as_of.isoformat() or not document:
                continue
            url = ARCHIVE_URL.format(cik=cik, accession=accession.replace("-", ""),
                                     document=document)
            text = _fetch_text(edgar, url, f"filing-{accession}.txt", problems)
            if text:
                out.append({"form": form, "filed": filed, "period": period, "url": url,
                            "accession": accession, "text": text})
            break
    return out


def _fetch_text(edgar, url: str, filename: str, problems: list | None = None) -> str | None:
    path = edgar._dir / filename
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            pass
    from edgedesk.providers.edgar import _THROTTLE
    _THROTTLE.wait()
    try:
        resp = edgar._session.get(url, timeout=60, headers={"User-Agent": edgar._user_agent})
    except Exception as exc:                        # noqa: BLE001
        logger.info("filing fetch failed %s: %s", url, exc)
        if problems is not None:
            problems.append({"url": url, "why": f"the request failed: {exc}"})
        return None
    if resp.status_code != 200:
        logger.info("filing fetch %s returned %s", url, resp.status_code)
        if problems is not None:
            problems.append({"url": url, "status": resp.status_code})
        return None
    text = to_text(resp.text)
    edgar._dir.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text
