"""What a debate showed to be wrong in a team's own report.

After a debate, one call reads the transcript against the reports and lists the specific
statements that did not survive it. Two kinds, and the difference matters:

    withdrawn     the report's OWN manager conceded in the round the item points at. Shown struck
                  through in that report. This is a debate annotation, not a fact check: it
                  records that the team gave the point up, not that the point was proven wrong.
    challenged    another manager disputed it and the owner held. Nobody proved it wrong, so it
                  is flagged with the objection, never struck.

Nothing here is trusted on the model's word:
  - the quote must appear character for character in that desk's report, on one line, or it
    is dropped (a paraphrase cannot be struck through in a document);
  - "withdrawn" is kept only if that desk's own turn in the round the model points at is a
    concession; otherwise it is downgraded to "challenged". Speaking in that round proves nothing
    (every manager speaks every round), and a concession in one round does not withdraw what the
    team said about something argued in another.

    python -m desk.corrections NVDA 2026-09-18      backfill a finished run's memo
"""

from __future__ import annotations

import json
import re
import sys

from desk.config import CALLS, MEMOS_DIR
from desk.llm import DeskLLM, cached_text, text

DESK_NAMES = {"A": "TradingAgents", "B": "ai-hedge-fund", "C": "Edge Desk"}

SCHEMA = {
    "type": "object",
    "properties": {"corrections": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "desk": {"type": "string", "enum": ["A", "B", "C"]},
            "quote": {"type": "string"},
            "status": {"type": "string", "enum": ["withdrawn", "challenged"]},
            "reason": {"type": "string"},
            "raised_by": {"type": "string", "enum": ["A", "B", "C"]},
            "round": {"type": "integer"},
        },
        "required": ["desk", "quote", "status", "reason", "raised_by", "round"],
        "additionalProperties": False}}},
    "required": ["corrections"],
    "additionalProperties": False,
}

PROMPT = """ROLE: you are the desk's fact checker. Below is a finished debate between the desks'
managers. List the specific statements in a desk's OWN REPORT that the debate showed to be wrong,
stale, misread or unsupported. Only statements that were actually contested in the debate.

For each one:
- desk: whose report contains the statement.
- quote: copy it from that desk's report CHARACTER FOR CHARACTER, including any markdown
  symbols, at most one sentence, all from a single line. If you cannot quote it exactly, leave
  the item out. Never paraphrase.
- status: "withdrawn" only if that desk's own manager CONCEDED in the round where the point was
  settled AND listed this statement among what it gives up. A concession on a different point,
  or an admission inside a turn where the manager still defended, is "challenged". Otherwise
  "challenged".
- reason: one plain sentence, at most 35 words, saying what is wrong with it, with the figure
  that corrects it when the debate gave one.
- raised_by: the desk that raised the objection. round: the round in which the owner accepted
  it (withdrawn) or in which it was raised (challenged).

At most 8 items, the most material first. An empty list is a fine answer. JSON only."""


def sources(reports_by_desk: dict[str, dict]) -> dict[str, str]:
    """The text each desk can be quoted from."""
    out: dict[str, str] = {}
    if reports_by_desk.get("A"):
        out["A"] = "\n".join((reports_by_desk["A"].get("reports") or {}).values())
    if reports_by_desk.get("B"):
        b = reports_by_desk["B"]
        v = b.get("verdict") or {}
        out["B"] = "\n".join([v.get("summary") or "", v.get("thesis") or "", *(v.get("bull_case") or []),
                              *(v.get("bear_case") or []), *(v.get("what_would_change") or []),
                              *[p.get("reasoning") or "" for p in b.get("personas") or []]])
    if reports_by_desk.get("C"):
        out["C"] = reports_by_desk["C"].get("report_md") or ""
    return out


def _plain(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%$.]+", " ", (s or "").lower())).strip()


def _same_claim(quote: str, given: str) -> bool:
    """The fact check's quote and a sentence the owner gave up name the same statement: one
    contains the other, ignoring case, punctuation and spacing (a model may quote part of it)."""
    a, b = _plain(quote), _plain(given)
    return len(min(a, b, key=len)) >= 12 and (a in b or b in a)


def verify(items: list[dict], texts: dict[str, str], turns: list[dict]) -> list[dict]:
    kept = []
    for it in items or []:
        desk, quote = it.get("desk"), (it.get("quote") or "").strip()
        if desk in texts and quote not in texts[desk]:
            # Models like to hand a list item back with its bullet, or wrapped in quote marks.
            trimmed = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", quote).strip().strip('"“”').strip()
            quote = trimmed if trimmed in texts[desk] else quote
        if desk not in texts or len(quote) < 12 or "\n" in quote or quote not in texts[desk]:
            continue
        status = it.get("status")
        # R2-08: a concession in that round is not enough; the owner must have named this
        # statement among what it gave up. Otherwise the claim is disputed, not disowned.
        given_up = [g for t in turns if t.get("desk") == desk and t.get("decision") == "concede"
                    and t.get("round") == it.get("round") for g in t.get("gives_up") or []]
        if status == "withdrawn" and not any(_same_claim(quote, g) for g in given_up):
            status = "challenged"
        if status == "challenged" and it.get("raised_by") == desk:
            continue                                   # a desk does not challenge itself
        kept.append({"desk": desk, "quote": quote, "status": status, "reason": (it.get("reason") or "").strip(),
                     "raised_by": it.get("raised_by"), "round": it.get("round")})
    return kept


def find(llm: DeskLLM, reports_text: str, reports_by_desk: dict[str, dict], horizon_label: str,
         debate: dict, transcript_text: str) -> list[dict]:
    model, effort = CALLS["corrections"]
    data = llm.json([cached_text(reports_text),
                     text(f"{PROMPT}\n\nHORIZON: {horizon_label}\n\nDEBATE TRANSCRIPT:\n{transcript_text}")],
                    SCHEMA, max_tokens=6000, model=model, effort=effort)
    proposed = data.get("corrections") or []
    kept = verify(proposed, sources(reports_by_desk), debate.get("turns") or [])
    print(f"    Fact check: {len(proposed)} proposed, {len(kept)} kept (exact quotes only)")
    return kept


def backfill(ticker: str, as_of: str) -> int:
    """Add corrections to a memo written before this existed. One call per debated horizon."""
    from desk.debate import Turn, reports_block, transcript
    from desk.views import VIEWS
    path = MEMOS_DIR / f"{ticker.upper()}-{as_of}.json"
    bundle = json.loads(path.read_text(encoding="utf-8"))
    by_desk = {d: bundle.get(k) for d, k in (("A", "tradingagents"), ("B", "ai_hedge_fund"), ("C", "edge_desk")) if bundle.get(k)}
    reports_text, llm, n = reports_block(bundle["ticker"], as_of, by_desk), DeskLLM(), 0
    for key, h in (bundle.get("horizons") or {}).items():
        if not h.get("debate") or key not in VIEWS:        # "as_they_ran" was retired 2026-09-24
            continue
        turns = [Turn(**{k: t[k] for k in Turn.__dataclass_fields__ if k in t}) for t in h["debate"]["turns"]]
        h["corrections"] = find(llm, reports_text, by_desk, VIEWS[key]["label"], h["debate"], transcript(turns))
        n += len(h["corrections"])
        print(f"  {key}: {len(h['corrections'])} correction(s)")
    path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    return n


if __name__ == "__main__":
    from dotenv import load_dotenv
    from desk.config import KEY_ENV_FILES
    for env_file in KEY_ENV_FILES:
        load_dotenv(env_file, override=False)
    print(f"{backfill(sys.argv[1], sys.argv[2])} correction(s) saved")
