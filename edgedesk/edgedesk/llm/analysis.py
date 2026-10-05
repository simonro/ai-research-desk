"""The LLM layer: lenses, an independent bull and bear, then a synthesis.

What this layer is for, and what it is not for. It is for reading one evidence
package the way four different investors would, arguing both sides of it, and
writing down what survives. It is not for producing numbers, choosing ratings,
or fetching anything.

Every reply is validated twice: against the shape asked for, and against the
evidence, because a figure in generated prose has to be a figure the run holds.
A call that fails both attempts is dropped and the section is marked missing.
The run's numbers are identical whether this layer ran or not, and a test
asserts exactly that.

Cost: nothing, if it is wired correctly. These go through Claude Code on the Max
subscription, with the API credentials stripped from the child environment.
"""

from __future__ import annotations

from edgedesk.verdict.published import published
import logging
from datetime import datetime, timezone

from edgedesk.llm import citations, headless, prompts, research, schema

logger = logging.getLogger(__name__)

LLM_VERSION = "1.0.0"

# Which fields are prose a reader would take a number from. Enum and boolean
# fields are excluded, so "would_own: no" never trips the citation check.
_PROSE = {"read", "strongest_point", "biggest_worry", "case", "points",
          "what_would_disprove_it", "thesis", "strongest_counterargument",
          "what_would_change_it", "unresolved", "reason"}


class Section:
    """One call's result, successful or not, with why."""

    def __init__(self, name: str, data=None, error: str | None = None,
                 attempts: int = 0, uncited: list[str] | None = None) -> None:
        self.name, self.data, self.error = name, data, error
        self.attempts, self.uncited = attempts, uncited or []

    @property
    def ok(self) -> bool:
        return self.data is not None

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "data": self.data,
                "error": self.error, "attempts": self.attempts,
                "uncited": self.uncited}


def _dissent_is_complete(data: dict) -> str | None:
    """A dissent without a reason is not a dissent, it is a shrug.

    The schema language cannot say "required only when another field is true",
    so this says it here. Left unchecked, the model records disagreement and
    then explains nothing, which is worse than agreeing: it puts a warning on
    the report that no reader can act on.
    """
    d = (data or {}).get("dissent") or {}
    if not d.get("disagrees"):
        return None
    missing = [k for k in ("direction", "reason") if not d.get(k)]
    if missing:
        return ("the dissent says it disagrees but leaves "
                + " and ".join(missing)
                + " empty. Either fill them in, or set disagrees to false.")
    return None


def _call(prompt: str, spec: dict, run: dict, name: str,
          timeout: int = headless.DEFAULT_TIMEOUT, extra_check=None) -> Section:
    """One request, validated, with exactly one repair attempt.

    One retry, not three. A model that produced an uncitable number twice is not
    going to be argued into the evidence on the third pass, and a silent loop of
    retries is how a batch job turns into an hour.
    """
    attempt, complaint, current = 0, None, prompt
    while attempt < 2:
        attempt += 1
        ok, text = headless.ask(current, timeout=timeout)
        if not ok:
            complaint = f"the call failed: {text}"
            current = prompts.repair_prompt(prompt, "Answer the original question.")
            continue
        try:
            data = schema.parse(text, spec)
        except schema.SchemaError as exc:
            complaint = f"the reply did not match the shape asked for: {exc}"
            current = prompts.repair_prompt(prompt, complaint)
            continue
        semantic = extra_check(data) if extra_check else None
        if semantic:
            complaint = semantic
            current = prompts.repair_prompt(prompt, complaint)
            continue
        bad = citations.audit(data, run, fields=list(_PROSE))
        if bad:
            complaint = ("these figures do not appear in the evidence: "
                         + ", ".join(bad)
                         + ". Rewrite using only figures from the evidence, or say the "
                           "evidence does not contain the figure.")
            current = prompts.repair_prompt(prompt, complaint)
            logger.info("%s: uncited figures %s, retrying", name, bad)
            continue
        return Section(name, data=data, attempts=attempt)
    return Section(name, error=complaint or "unknown failure", attempts=attempt)


def analyze(run: dict, lenses: tuple[str, ...] | None = None,
            say=lambda *_: None, filings: list[dict] | None = None,
            filings_error: str | None = None, on_section=lambda *_, **__: None) -> dict:
    """Run the whole layer over one finished deterministic run.

    Takes the run as input and returns the block to attach to it. It never
    mutates the run's numbers, because it is not given the chance to: only the
    rendered brief goes into a prompt.

    *on_section(name, ok, error, text=...)* is called as each section finishes, with its real outcome
    and the opening of what it wrote (for a live view), so
    a watcher never has to infer "done" from the next section starting (audit R2-17).
    """
    ok, note = headless.available()
    if not ok:
        return {"ok": False, "error": note, "version": LLM_VERSION}

    brief = prompts.evidence_brief(run)
    keys = lenses if lenses is not None else tuple(prompts.LENSES)

    sections: dict[str, Section] = {}
    extra: dict[str, dict] = {}
    limited: str | None = None
    lens_results: dict[str, dict] = {}
    try:
        for key in keys:
            say(f"    lens: {key}")
            section = _call(prompts.lens_prompt(brief, key), prompts.LENS_SCHEMA, run,
                            f"lens:{key}")
            sections[f"lens:{key}"] = section
            on_section(f"lens:{key}", section.ok, section.error, text=_opening(section.data))
            if section.ok:
                lens_results[key] = section.data

        # Independent on purpose: neither case sees the other before it is made.
        say("    bull")
        bull = _call(prompts.bull_prompt(brief), prompts.CASE_SCHEMA, run, "bull")
        sections["bull"] = bull
        on_section("bull", bull.ok, bull.error, text=_opening(bull.data))
        say("    bear")
        bear = _call(prompts.bear_prompt(brief), prompts.CASE_SCHEMA, run, "bear")
        sections["bear"] = bear
        on_section("bear", bear.ok, bear.error, text=_opening(bear.data))

        synthesis = Section("synthesis", error="skipped: both cases failed")
        if bull.ok or bear.ok:
            say("    synthesis")
            synthesis = _call(
                prompts.synthesis_prompt(brief, bull.data or {}, bear.data or {},
                                         lens_results,
                                         missing=[s.name for s in (bull, bear) if not s.ok]),
                prompts.SYNTHESIS_SCHEMA, run, "synthesis",
                extra_check=_dissent_is_complete)
        sections["synthesis"] = synthesis
        on_section("synthesis", synthesis.ok, synthesis.error, text=_opening(synthesis.data))

        # The research layer: a second look at the swing call, a read of the
        # headlines, and management's own words from the filings.
        say("    swing second look")
        extra["second_look"] = research.second_look(run, brief)
        _report(on_section, "second_look", extra["second_look"])
        say("    headline read")
        extra["headline_read"] = research.headline_read(run)
        _report(on_section, "headline_read", extra["headline_read"])
        say("    filing research")
        if filings is None and filings_error is None:
            filings, filings_error = _load_filings(run)
        extra["filing_research"] = research.filing_research(run, filings or [], filings_error)
        _report(on_section, "filing_research", extra["filing_research"])
    except headless.UsageLimit as exc:
        # Stop immediately. Every remaining call would fail the same way, and a
        # batch that keeps going turns one clear cause into forty confusing ones.
        limited = str(exc)
        say(f"    stopped: the subscription is out of capacity ({limited[:80]})")
        bull = sections.get("bull") or Section("bull", error="not attempted")
        bear = sections.get("bear") or Section("bear", error="not attempted")
        synthesis = sections.get("synthesis") or Section("synthesis",
                                                         error="not attempted")

    missing = [s.name for s in (bull, bear) if not s.ok]
    return {
        "ok": synthesis.ok,
        # R2-02: a synthesis written with one case missing is one-sided, and says so.
        "completeness": ("failed" if not synthesis.ok else "partial" if missing else "complete"),
        "missing_cases": missing,
        "usage_limited": limited,
        "version": LLM_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "transport": note,
        "lenses": lens_results,
        "bull": bull.data,
        "bear": bear.data,
        "synthesis": synthesis.data,
        "dissent": _dissent(synthesis, run),
        "second_look": extra.get("second_look"),
        "headline_read": extra.get("headline_read"),
        "filing_research": extra.get("filing_research"),
        "sections": {k: v.to_dict() for k, v in sections.items()},
    }


def _opening(data) -> str:
    """The main prose of a written section: a lens's read, a case, or the thesis."""
    data = data or {}
    return str(data.get("read") or data.get("case") or data.get("thesis") or "").strip()


def _report(on_section, name: str, part: dict | None) -> None:
    part = part or {}
    text = part.get("reason") or part.get("summary") or ""
    if name == "second_look" and part.get("stance"):
        text = f"{str(part['stance']).upper()}. {text}"
    if name == "headline_read" and part.get("ok"):
        text = (f"{part.get('material')} material headlines: {part.get('positive')} positive, "
                f"{part.get('negative')} negative.")
    on_section(name, bool(part.get("ok")), part.get("error"), text=str(text).strip())


def _dissent(synthesis: Section, run: dict) -> dict | None:
    """The model's recorded disagreement with the formula, if it has one.

    Published beside the rating and never instead of it. The rating in the run
    file is untouched by anything in this module, which is the whole point: a
    disagreement is information, and a silently edited number is not.
    """
    if not synthesis.ok:
        return None
    d = (synthesis.data or {}).get("dissent") or {}
    if not d.get("disagrees") or not d.get("reason"):
        return None
    horizon = d.get("horizon") or "both"
    against = {}
    for key in (("swing", "long_term") if horizon == "both" else (horizon,)):
        verdict = published(run, key)
        against[key] = verdict.get("rating") or (run.get("signal") or {}).get("signal")
    return {
        "horizon": horizon,
        "direction": d.get("direction"),
        "reason": d.get("reason"),
        "against_rating": against,
        "note": "Recorded, not applied. The rating above came from the formula.",
    }


def _load_filings(run: dict) -> tuple[list[dict], str | None]:
    """The newest 10-Q and 10-K public by the run's date, or why they could not be read."""
    from datetime import date

    from edgedesk.providers import filings as filings_mod
    from edgedesk.providers.edgar import EdgarClient
    edgar = EdgarClient()
    problems: list[dict] = []
    try:
        found = filings_mod.latest(edgar, run["ticker"], date.fromisoformat(run["as_of"]),
                                   problems=problems)
    except Exception as exc:                        # noqa: BLE001
        return [], f"the filings could not be fetched: {exc}"
    finally:
        edgar.close()
    if not found:
        return [], filing_error(problems, run)
    return found, None


def filing_error(problems: list[dict], run: dict) -> str:
    """What actually stopped the filing text, from what the fetch recorded (audit B8). The old
    message blamed the contact email whatever happened."""
    for p in problems:
        if p.get("status") == 403:
            return ("the SEC archive refused the filing request (HTTP 403). It refuses requests "
                    "whose User-Agent has no contact email: check SEC_USER_AGENT in "
                    "~/.edge-desk/.env reads 'Your Name you@example.com'.")
    for p in problems:
        if p.get("status"):
            return f"the SEC archive answered HTTP {p['status']} for {p.get('url')}"
        if p.get("why"):
            return f"the filing text could not be read: {p['why']}"
    return f"no 10-Q or 10-K is listed for {run.get('ticker')} on or before {run.get('as_of')}"


def attach(run: dict, **kwargs) -> dict:
    """Run the layer and attach it, leaving every number as it was."""
    before = run.get("content_hash")
    run["llm"] = analyze(run, **kwargs)
    if run.get("content_hash") != before:
        raise AssertionError("the LLM layer changed the run's content hash")
    return run
