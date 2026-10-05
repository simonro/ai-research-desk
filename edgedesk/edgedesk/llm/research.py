"""Three places where a reader beats a formula, each with a fence around it.

1. FILING RESEARCH. The model reads management's own words in the newest 10-Q and
   10-K and reports what bears on growth, margins, risk and capital allocation. Every
   finding must carry a verbatim quote, and the quote is checked against the filing
   text here, in code: a finding whose quote is not in the filing is dropped, however
   plausible it sounds. It may then nudge three scenario assumptions (growth, margin,
   exit multiple) by one bounded step each, with the finding that justifies it. The
   result is a second, "researched" expected return printed BESIDE the formula's, never
   instead of it.

2. HEADLINE READ. A keyword count cannot tell "beats estimates" from "beats a lawsuit".
   The model classifies each recent headline (about this company or not, good or bad
   for the stock over weeks, material or noise). The tally is arithmetic done here. The
   swing report shows what the sentiment part WOULD score on that read.

3. SWING SECOND LOOK. Given the call, the plan and the news, the model says take, pass
   or wait, and names up to three concerns the numbers cannot see. Each concern must
   point at a headline or a fact in the evidence.

None of this touches the run's numbers: it is attached under run["llm"] like the rest
of the written layer, and the content hash still has to match afterwards.
"""

from __future__ import annotations

import re

from edgedesk.evidence import business
from edgedesk.llm import citations, headless, prompts, schema

_WS = re.compile(r"\s+")
_NUMBER = re.compile(r"\d[\d,]*\.?\d*")
_NOT_ALNUM = re.compile(r"[^a-z0-9%$. ]")

MDNA_BUDGET, RISK_BUDGET = 45_000, 15_000
GROWTH_STEP, MARGIN_STEP, MULTIPLE_STEP = 0.15, 0.10, 0.10     # one step, as a fraction

FILING_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {"type": "array", "max_items": 10, "items": {"type": "object",
                     "required": ["claim", "quote", "bearing", "direction"], "properties": {
            "claim": {"type": "string", "max_length": 320},
            "quote": {"type": "string", "max_length": 400},
            "bearing": {"type": "string", "enum": ["growth", "margin", "risk", "capital", "quality"]},
            "direction": {"type": "string", "enum": ["positive", "negative", "neutral"]},
        }}},
        "adjustments": {"type": "object", "required": ["growth", "margin", "multiple", "reason"],
                        "properties": {
            "growth": {"type": "integer", "enum": [-1, 0, 1]},
            "margin": {"type": "integer", "enum": [-1, 0, 1]},
            "multiple": {"type": "integer", "enum": [-1, 0, 1]},
            "reason": {"type": "string", "max_length": 500},
        }},
        "summary": {"type": "string", "max_length": 700},
    },
    "required": ["findings", "adjustments", "summary"],
}
NEWS_SCHEMA = {
    "type": "object",
    "required": ["items"],
    "properties": {"items": {"type": "array", "max_items": 30, "items": {"type": "object",
                   "required": ["n", "about_company", "direction", "material", "why"], "properties": {
        "n": {"type": "integer"},
        "about_company": {"type": "boolean"},
        "direction": {"type": "string", "enum": ["positive", "negative", "neutral"]},
        "material": {"type": "boolean"},
        "why": {"type": "string", "max_length": 160},
    }}}},
}
SECOND_LOOK_SCHEMA = {
    "type": "object",
    "properties": {
        "stance": {"type": "string", "enum": ["take", "pass", "wait"]},
        "concerns": {"type": "array", "max_items": 3, "items": {"type": "object",
                     "required": ["concern", "based_on"], "properties": {
            "concern": {"type": "string", "max_length": 260},
            "based_on": {"type": "string", "max_length": 200},
        }}},
        "what_to_check": {"type": "string", "max_length": 300},
        "reason": {"type": "string", "max_length": 500},
    },
    "required": ["stance", "concerns", "what_to_check", "reason"],
}


def _norm(text: str) -> str:
    return _WS.sub(" ", _NOT_ALNUM.sub(" ", text.lower())).strip()


def quote_is_in(quote: str, source_norm: str) -> bool:
    q = _norm(quote)
    return len(q) >= 25 and q in source_norm


def _clean_keys(node):
    if isinstance(node, dict):
        return {k.replace(" (optional)", "").strip(): _clean_keys(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_clean_keys(v) for v in node]
    return node


def _coerce(node, spec: dict):
    """A model that writes "+1" for 1 or "true" for true meant the right thing. The
    shape check stays strict about everything else."""
    kind = (spec or {}).get("type")
    if kind == "integer" and isinstance(node, str) and re.fullmatch(r"\s*[+-]?\d+\s*", node):
        return int(node)
    if kind == "boolean" and isinstance(node, str) and node.strip().lower() in ("true", "false"):
        return node.strip().lower() == "true"
    if isinstance(node, list) and spec.get("items"):
        return [_coerce(v, spec["items"]) for v in node]
    if isinstance(node, dict):
        props = spec.get("properties") or {}
        return {k: _coerce(v, props[k]) if k in props else v for k, v in node.items()}
    return node


def _ask(prompt: str, spec: dict, timeout: int = 300, repair: bool = True):
    """One call, and one repair if the reply has the wrong shape (AKAM 2026-10-05: the filing
    research lost its whole step to a reply missing `summary`). second_look runs its own loop,
    so it asks with repair=False."""
    ok, text = headless.ask(prompt, timeout=timeout)
    if not ok:
        return None, text
    try:
        data = schema.fit_lengths(_coerce(_clean_keys(schema.extract(text)), spec), spec)
        schema.check(data, spec)
        return data, None
    except schema.SchemaError as exc:
        problem = f"the reply did not match the shape asked for: {exc}"
    if not repair:
        return None, problem
    return _ask(prompts.repair_prompt(prompt, problem), spec, timeout, repair=False)


# ---------------------------------------------------------------------------
# 1. Filing research
# ---------------------------------------------------------------------------

def filing_prompt(run: dict, excerpts: list[dict]) -> str:
    b = run.get("long_term") or {}
    base = ((b.get("scenario_return") or {}).get("scenarios") or {}).get("base") or {}
    path = base.get("revenue_growth_path") or [None, None]
    parts = [
        "You are a buy-side analyst reading a company's own filings. You report only what "
        "the filing says, in management's words.",
        f"COMPANY: {run['ticker']}. The engine's base case assumes revenue growth starting at "
        f"{prompts.pct0(path[0])} and fading to {prompts.pct0(path[-1])}, an owner-earnings "
        f"margin of {prompts.pct0(base.get('owner_margin'))}, and an exit multiple of "
        f"{prompts.num(base.get('exit_multiple'), 0)} times.",
        "TASK. Find up to 10 things in the text below that bear on whether those assumptions "
        "are too high or too low: segment or product trends, what management says is driving "
        "growth and margins, one-time items in the results, risks that are specific rather "
        "than boilerplate, and capital allocation (buybacks, capital spending plans, deals).",
        "RULES. Every finding needs a QUOTE copied exactly from the text, 25 to 400 "
        "characters, no ellipsis, no paraphrase: it will be checked by a program and the "
        "finding is deleted if the quote is not found. Any number in your claim must appear "
        "in its quote. Then set each adjustment to -1, 0 or +1: +1 only if the filing gives a "
        "concrete reason the engine's assumption is too LOW, -1 only if too HIGH, else 0. "
        "When in doubt, 0.",
    ]
    for e in excerpts:
        parts.append(f"=== {e['form']} filed {e['filed']}, period {e['period']}: {e['name']} ===\n{e['text']}")
    parts += ["Return JSON in exactly this shape:", schema.describe(FILING_SCHEMA)]
    return "\n\n".join(parts)


def excerpts_from(filings_list: list[dict]) -> list[dict]:
    from edgedesk.providers import filings as filings_mod
    out = []
    for f in filings_list:
        mdna = filings_mod.section(f["text"], f["form"], "mdna")
        if mdna:
            out.append({**{k: f[k] for k in ("form", "filed", "period", "url")},
                        "name": "Management's discussion", "text": mdna[:MDNA_BUDGET]})
        if f["form"] == "10-K":
            risk = filings_mod.section(f["text"], f["form"], "risk_factors")
            if risk:
                out.append({**{k: f[k] for k in ("form", "filed", "period", "url")},
                            "name": "Risk factors (opening)", "text": risk[:RISK_BUDGET]})
    return out


def verify_findings(data: dict, excerpts: list[dict]) -> tuple[list[dict], list[dict]]:
    """(kept, dropped). A finding survives only if its quote is in the filing and
    every number in its claim is in that quote."""
    source = _norm(" ".join(e["text"] for e in excerpts))
    kept, dropped = [], []
    for f in data.get("findings") or []:
        quote, claim = f.get("quote") or "", f.get("claim") or ""
        if not quote_is_in(quote, source):
            dropped.append({**f, "why_dropped": "the quote is not in the filing text"})
            continue
        quote_numbers = {n.replace(",", "").rstrip(".") for n in _NUMBER.findall(quote)}
        def is_year(n: str) -> bool:
            bare = n.strip(".,")
            return bare.isdigit() and 1990 <= int(bare) <= 2100

        loose = [n.strip(".,") for n in _NUMBER.findall(claim)
                 if n.replace(",", "").rstrip(".") not in quote_numbers
                 and not is_year(n) and len(n.strip(".,")) > 1]
        if loose:
            dropped.append({**f, "why_dropped": f"numbers not in the quote: {', '.join(loose)}"})
            continue
        kept.append(f)
    return kept, dropped


def researched_case(run: dict, adjustments: dict) -> dict | None:
    """The business case re-run with the bounded nudges. Same arithmetic, same code."""
    ev = run.get("evidence") or {}
    g, m, x = (int(adjustments.get(k) or 0) for k in ("growth", "margin", "multiple"))
    if not ev.get("metrics") or not (g or m or x):
        return None
    case = business.analyze(
        ev["metrics"], ev.get("valuation_now"), (ev.get("profile") or {}).get("sic"),
        (ev.get("dividend") or {}).get("yield"), ev.get("forward"),
        nudges={"growth": g * GROWTH_STEP, "margin": m * MARGIN_STEP, "multiple": x * MULTIPLE_STEP})
    s = case.get("scenario_return") or {}
    if not s.get("available"):
        return None
    return {"call": case.get("call"), "expected_annual_return": s["expected_annual_return"],
            "scenarios": {k: v["annual_return"] for k, v in s["scenarios"].items()},
            "steps": {"growth": g, "margin": m, "multiple": x},
            "step_sizes": {"growth": GROWTH_STEP, "margin": MARGIN_STEP, "multiple": MULTIPLE_STEP}}


def filing_research(run: dict, filings_list: list[dict], unavailable: str | None = None) -> dict:
    if unavailable:
        return {"ok": False, "error": unavailable}
    excerpts = excerpts_from(filings_list)
    if not excerpts:
        return {"ok": False, "error": "no management discussion could be cut from the filings"}
    data, error = _ask(filing_prompt(run, excerpts), FILING_SCHEMA, timeout=420)
    if data is None:
        return {"ok": False, "error": error}
    kept, dropped = verify_findings(data, excerpts)
    adjustments = dict(data.get("adjustments") or {})
    voided = []
    for key, needs in (("growth", {"growth"}), ("margin", {"margin", "quality"}),
                       ("multiple", {"risk", "capital", "quality", "growth"})):
        step = adjustments.get(key)
        if not step:
            continue
        # R2-14: the surviving findings behind a nudge must point its way. None, the other way, or
        # both ways (contradictory) voids it: a negative growth finding cannot raise growth.
        directions = {f["direction"] for f in kept if f["bearing"] in needs} - {"neutral"}
        if directions != {"positive" if step > 0 else "negative"}:
            adjustments[key] = 0
            voided.append(key)
    formula = ((run.get("long_term") or {}).get("scenario_return") or {})
    # R2-04: the summary is prose a reader takes numbers from, so it is held to the verified
    # quotes and the run like everything else. It is dropped, not repaired.
    summary, summary_dropped = data.get("summary"), None
    known = citations.add_text(citations.known_values(run), " ".join(f["quote"] for f in kept))
    loose = citations.uncited(summary or "", run, known)
    if loose:
        summary, summary_dropped = None, f"numbers in no verified quote or the run: {', '.join(loose)}"
    return {"ok": True, "findings": kept, "dropped": dropped, "adjustments": adjustments,
            "voided_adjustments": voided,
            "summary": summary, "summary_dropped": summary_dropped,
            "sources": [{k: e[k] for k in ("form", "filed", "period", "name", "url")} for e in excerpts],
            "formula_expected_return": formula.get("expected_annual_return"),
            "formula_call": (run.get("long_term") or {}).get("call"),
            "researched": researched_case(run, adjustments) if kept else None,
            "note": ("Printed beside the formula's business case, never instead of it. Each "
                     "nudge is one bounded step and needs a verified quote behind it.")}


# ---------------------------------------------------------------------------
# 2. Headline read
# ---------------------------------------------------------------------------

def recent_headlines(run: dict, days: int = 14, limit: int = 25) -> list[dict]:
    from datetime import date, timedelta
    ev = run.get("evidence") or {}
    cutoff = (date.fromisoformat(ev["as_of"]) - timedelta(days=days)).isoformat()
    rows = [n for n in ev.get("news") or [] if (n.get("date") or "")[:10] >= cutoff]
    return [{"n": i + 1, "date": (n.get("date") or "")[:10], "title": n.get("title") or ""}
            for i, n in enumerate(rows[:limit])]


def headline_read(run: dict) -> dict:
    heads = recent_headlines(run)
    if not heads:
        return {"ok": False, "error": "no headlines in the last 14 days"}
    prompt = "\n\n".join([
        f"You are reading news headlines about {run['ticker']} "
        f"({((run.get('evidence') or {}).get('profile') or {}).get('name')}) for a swing trader "
        "holding 2 days to 8 weeks.",
        "For EACH numbered headline say: is it actually about this company (not a roundup or "
        "another company); is it positive, negative or neutral for the stock over the next few "
        "weeks; and is it material (would a professional care) or noise. Judge the headline as "
        "written. Do not add facts.",
        "\n".join(f"{h['n']}. [{h['date']}] {h['title']}" for h in heads),
        "Return JSON in exactly this shape:", schema.describe(NEWS_SCHEMA)])
    data, error = _ask(prompt, NEWS_SCHEMA, timeout=420)   # 240 timed out on AKAM 2026-10-04 (25 headlines)
    if data is None:
        return {"ok": False, "error": error}
    by_n = {h["n"]: h for h in heads}
    items, seen = [], set()
    for i in data.get("items") or []:
        if i.get("n") in by_n and i["n"] not in seen:       # R2-13: one vote per headline
            seen.add(i["n"])
            items.append({**i, "title": by_n[i["n"]]["title"], "date": by_n[i["n"]]["date"]})
    counted = [i for i in items if i.get("about_company") and i.get("material")]
    pos = sum(i["direction"] == "positive" for i in counted)
    neg = sum(i["direction"] == "negative" for i in counted)
    tone = round((pos - neg) / (pos + neg + 2), 3)
    return {"ok": True, "headlines": len(heads), "material": len(counted), "positive": pos,
            "negative": neg, "tone": tone,
            "keyword_tone": (((run.get("swing") or {}).get("sentiment") or {}).get("news") or {}).get("tone"),
            "items": [i for i in items if i.get("about_company") and i.get("material")][:8],
            "note": ("The model classified each headline; the tally is arithmetic. Shown beside "
                     "the keyword count the score uses, not instead of it.")}


# ---------------------------------------------------------------------------
# 3. Swing second look
# ---------------------------------------------------------------------------

_HEADLINE_REF = re.compile(r"headlines?\s*#?\s*(\d+)", re.I)


def second_look_problem(data: dict, headline_ns: set[int], run: dict, known: dict) -> str | None:
    """Why a second look cannot be published, or None. Every concern cites a real numbered
    headline or the evidence, and every number is in the run or a headline."""
    for c in data.get("concerns") or []:
        basis = (c.get("based_on") or "").strip()
        refs = [int(n) for n in _HEADLINE_REF.findall(basis)]
        if refs:
            missing = [n for n in refs if n not in headline_ns]
            if missing:
                return f"based_on names headline {missing[0]}, which is not in the list"
        elif not basis.lower().startswith("evidence"):
            return (f"based_on {basis[:60]!r} is neither \"headline N\" nor \"evidence:\" and "
                    "a figure's name")
    texts = [data.get("what_to_check") or "", data.get("reason") or ""]
    texts += [f"{c.get('concern') or ''} {_HEADLINE_REF.sub('', c.get('based_on') or '')}"
              for c in data.get("concerns") or []]
    loose = sorted({n for t in texts for n in citations.uncited(t, run, known)})
    if loose:
        return ("these figures are in neither the evidence nor a headline: " + ", ".join(loose)
                + ". Rewrite using only figures you were given.")
    return None


def second_look(run: dict, brief: str) -> dict:
    call = run.get("swing") or {}
    if not call.get("call"):
        return {"ok": False, "error": "there is no swing call to look at"}
    plan = call.get("plan") or {}
    heads = recent_headlines(run)
    prompt = "\n\n".join([
        "You are an experienced swing trader reviewing a colleague's trade idea before money "
        "goes in. Holding period: 2 days to 8 weeks. Long only.",
        "EVIDENCE:", brief,
        "RECENT HEADLINES:\n" + ("\n".join(f"{h['n']}. [{h['date']}] {h['title']}" for h in heads) or "none"),
        f"THE CALL: {call['call']} at {call.get('score')} of 100. {call.get('why')}"
        + (f" Plan: entry {plan.get('entry')}, invalidation {plan.get('invalidation')}, targets "
           f"{plan.get('target_1')} and {plan.get('target_2')}, {plan.get('shares')} shares for "
           f"${plan.get('risk_dollars'):,.0f} of risk." if plan else ""),
        "TASK. Would you take this trade, pass, or wait? Name up to three concerns the numbers "
        "cannot see (a pending event, a story in the headlines, a reason the setup is a trap). "
        "Each concern must say what it is based on, in based_on: \"headline N\" for a numbered "
        "headline above, or \"evidence:\" and the figure's name. Every number you write must be "
        "in the evidence or a headline. Do not invent facts. If the call is Sell, say whether you "
        "agree it is not a buy and what would change your mind.",
        prompts._RULES,
        "Return JSON in exactly this shape:", schema.describe(SECOND_LOOK_SCHEMA)])
    # R2-04: checked like the other written steps, with one repair attempt.
    known = citations.add_text(citations.known_values(run), " ".join(h["title"] for h in heads))
    ask, data, error = prompt, None, None
    for _ in range(2):
        data, error = _ask(ask, SECOND_LOOK_SCHEMA, timeout=240, repair=False)
        if data is not None:
            error = second_look_problem(data, {h["n"] for h in heads}, run, known)
            if not error:
                break
            data = None
        ask = prompts.repair_prompt(prompt, error or "Answer the original question.")
    if data is None:
        return {"ok": False, "error": error}
    return {"ok": True, **data, "against_call": call["call"],
            "note": "An opinion recorded beside the call. The call itself came from the formula."}
