"""Which horizons the desk may rate at all, decided once per run before any horizon is rated.

Decision 1 (2026-09-25, audit R2-01): a data problem is the desk's call, not one team's. On V
(2026-09-24) Edge Desk withheld its rating because SEC's financial-data feed still stopped a
quarter back, while the Veterans voted Overweight on the very same feed. Now, when any team's code
finds the latest filing missing from the feed, the long-term horizon publishes no combined rating
for the whole desk. Swing is unaffected: it rests on prices, not on the quarter's filing.

Version 1 works at horizon level from findings the teams already compute deterministically (no
model reads anything here). Claim-level gating and fetching the missing filing come later.
"""

from __future__ import annotations

import re

VERSION = 1
STALE = "companyfacts_stale"
WITHHOLDS = {"long_term": {STALE}, "swing": set()}

# The Veterans' data client (ai-hedge-fund free/client.py get_data_caveats) writes this sentence in
# code when the filing index is ahead of the companyfacts feed.
_VETS_STALE = "Fundamentals are a quarter stale"
# Edge payloads saved before the runner passed its caveat codes: its report's data-quality table.
_EDGE_TABLE_ROW = re.compile(r"\|\s*withhold\s*\|\s*`([^`]+)`\s*\|\s*([^|]+)\|")

REASON = {STALE: "the latest filing is not in SEC's financial-data feed yet, so the fundamentals "
                 "are a quarter stale"}


def findings(reports_by_desk: dict[str, dict]) -> list[dict]:
    """Every withhold-class data finding a team's code reported, as {team, code, text}."""
    out = []
    for c in ((reports_by_desk.get("B") or {}).get("verdict") or {}).get("data_caveats") or []:
        if str(c).startswith(_VETS_STALE):
            out.append({"team": "B", "code": STALE, "text": str(c)})
    edge = reports_by_desk.get("C") or {}
    caveats = edge.get("caveats")
    if caveats is None:
        caveats = [{"code": code, "severity": "withhold", "message": text.strip()}
                   for code, text in _EDGE_TABLE_ROW.findall(edge.get("report_md") or "")]
    for c in caveats:
        if c.get("severity") == "withhold":
            out.append({"team": "C", "code": c.get("code"), "text": c.get("message")})
    return out


def manifest(reports_by_desk: dict[str, dict]) -> dict:
    """The run's quality manifest: what was found, and which horizons that withholds."""
    found = findings(reports_by_desk)
    withheld = {}
    for horizon, codes in WITHHOLDS.items():
        hits = [f for f in found if f["code"] in codes]
        withheld[horizon] = ({"codes": sorted({f["code"] for f in hits}),
                              "teams": sorted({f["team"] for f in hits}),
                              "reason": "; ".join(sorted({REASON[f["code"]] for f in hits}))}
                             if hits else None)
    return {"version": VERSION, "findings": found, "withheld": withheld}
