"""What the model is told, and what it is allowed to say back.

Three design choices carry most of the value here.

**One brief, many readers.** Every role reads the same rendered evidence, so a
disagreement between the bull and the bear is a disagreement about meaning, not
about numbers. This is the thing the two-engine desk could never do.

**Bull and bear do not see each other.** Both are asked for their strongest
honest case from the same facts, independently. A bear that has already read the
bull's case argues with the bull; a bear that has not argues with the evidence.

**Nobody is asked what the rating should be.** The rating already exists and
came from a formula. The synthesis may record that it disagrees, with a reason,
and that dissent is published. It cannot change the number, and no prompt here
invites it to try.
"""

from __future__ import annotations

from edgedesk.evidence.decision import decision_row
from edgedesk.reports import longterm_block
from edgedesk.reports.common import (
    FAMILY_ORDER, money, num, pct, pct0, price, signal_value,
)
from edgedesk.verdict.published import RATED_HORIZONS, published_verdicts

# Short enough that a persona stays a lens on the evidence rather than becoming
# a character doing an impression.
LENSES = {
    "quality_compounder": (
        "a long-horizon owner of high-quality businesses",
        "Durable advantage, returns on capital, pricing power, and whether the "
        "business would still be worth owning if the market shut for five years. "
        "Sceptical of paying any price for quality."),
    "deep_value": (
        "a balance-sheet value investor",
        "What is actually being paid for the assets and earnings in hand. "
        "Distrustful of growth extrapolation and of multiples that depend on "
        "the future behaving."),
    "growth_at_reasonable_price": (
        "a growth-at-a-reasonable-price investor",
        "Whether growth is real, funded and durable, and whether the multiple "
        "is sane relative to it. Attentive to the gap between earnings growth "
        "and cash generation."),
    "trend_and_flow": (
        "a trend-following allocator",
        "What price and relative strength are saying about positioning, and "
        "whether the tape agrees with the fundamental case. Treats a falling "
        "stock in a rising sector as information, not as a discount."),
}

_RULES = """
RULES, which matter more than style:

1. Every number you write must appear in the evidence above. Do not compute new
   ones, do not round to a rounder number, and do not estimate. If a figure you
   want is not there, say the evidence does not contain it.
2. Do not state or imply a rating, a price target, or an action. Those come from
   a formula and from computed levels, not from you.
3. Where the evidence is thin or flagged, say so rather than writing around it.
4. Plain language. No hedging padding, no "it is important to note", no summary
   of what you were asked.
5. Never use an em dash or an en dash. Use a comma, a colon, or a full stop.
6. Respect the length limits in the shape below. A reply that runs over is
   rejected, so write to the limit rather than past it.
7. Return only the JSON described. No prose outside it, no code fence needed.
""".strip()


def evidence_brief(run: dict) -> str:
    """Everything the model is allowed to know, rendered once."""
    ev = run.get("evidence") or {}
    profile = ev.get("profile") or {}
    anchors = ev.get("anchors") or {}
    filed = (ev.get("metrics") or [{}])[0]
    latest = decision_row(filed, ev.get("valuation_now"))
    restated = bool((ev.get("valuation_now") or {}).get("available"))
    lines = [
        f"COMPANY: {run['ticker']}, {profile.get('name')}",
        f"SECTOR: {profile.get('sector')}, industry {profile.get('sic_description')}",
        f"AS OF: {run['as_of']} (last settled close; nothing later is known)",
        f"PRICE: {price(anchors.get('last_close'))}, market cap "
        f"{money(latest.get('market_cap'))}",
        "",
        "FUNDAMENTALS, trailing twelve months, from the filing public on "
        f"{latest.get('filing_date')} for the period ending {latest.get('report_period')}"
        + (f" (price-based ratios restated at the {run['as_of']} close):" if restated
           else " (price-based ratios are as of the filing date):"),
    ]
    for label, key, fmt in (
        ("Revenue growth year on year", "revenue_growth", pct),
        ("Earnings growth year on year", "earnings_growth", pct),
        ("Free cash flow growth year on year", "free_cash_flow_growth", pct),
        ("Gross margin", "gross_margin", pct0),
        ("Operating margin", "operating_margin", pct0),
        ("Net margin", "net_margin", pct0),
        ("Return on equity", "return_on_equity", pct0),
        ("Return on assets", "return_on_assets", pct0),
        ("Free cash flow yield", "free_cash_flow_yield", pct0),
        ("Price to earnings", "price_to_earnings_ratio", num),
        ("PEG", "peg_ratio", num),
        ("Price to sales", "price_to_sales_ratio", num),
        ("Debt to equity", "debt_to_equity", num),
        ("Interest coverage", "interest_coverage", num),
        ("Current ratio", "current_ratio", num),
    ):
        if latest.get(key) is not None:
            lines.append(f"  {label}: {fmt(latest[key])}")

    lines += ["", "PRICE STRUCTURE:"]
    for label, key, fmt in (
        ("20-day average", "sma_20", price),
        ("50-day average", "sma_50", price),
        ("200-day average", "sma_200", price),
        ("52-week high", "high_52w", price),
        ("52-week low", "low_52w", price),
        ("Distance from the 52-week high", "pct_from_52w_high", pct),
        ("Annualized volatility", "volatility_annual", pct0),
        ("Worst drawdown in the last year", "max_drawdown_1y", pct),
    ):
        if anchors.get(key) is not None:
            lines.append(f"  {label}: {fmt(anchors[key])}")
    returns = anchors.get("returns") or {}
    if returns:
        lines.append("  Returns: " + ", ".join(
            f"{w} {pct(v)}" for w, v in returns.items() if v is not None))

    rel = ev.get("relative") or {}
    if rel.get("vs_spy"):
        lines += ["", "RELATIVE STRENGTH, excess return so zero means it kept pace:"]
        lines.append("  vs SPY: " + ", ".join(
            f"{w} {pct(v)}" for w, v in rel["vs_spy"].items() if v is not None))
        if rel.get("vs_sector"):
            lines.append(f"  vs {rel.get('sector_etf')}: " + ", ".join(
                f"{w} {pct(v)}" for w, v in rel["vs_sector"].items() if v is not None))
            lines.append(f"  {rel.get('sector_etf')} vs SPY: " + ", ".join(
                f"{w} {pct(v)}" for w, v in rel["sector_vs_spy"].items() if v is not None))

    lines += _business_lines(run)
    lines += _valuation_lines(run)
    lines += _street_lines(ev)
    lines += _event_lines(ev)
    lines += _factor_lines(run)
    lines += _quality_lines(ev)
    return "\n".join(lines)


def _business_lines(run: dict) -> list[str]:
    """The long-term business case, placed before any formula verdict on purpose:
    a reader who sees the rating first argues toward it."""
    b = run.get("long_term") or {}
    if not b.get("available"):
        return []
    out = ["", "BUSINESS CASE, computed from filings. The scenario assumptions are judgement "
               "written down; say where you think one is wrong and why:"]
    out += ["  " + f for f in longterm_block.facts(b)]
    t = b.get("trajectory") or {}
    if t.get("summary"):
        out.append("  " + t["summary"])
    o = b.get("owner_economics") or {}
    for flag in o.get("red_flags") or []:
        out.append("  Flag: " + flag)
    for good in o.get("strengths") or []:
        out.append("  Strength: " + good)
    s = b.get("scenario_return") or {}
    for name, a in (s.get("scenarios") or {}).items():
        path = a["revenue_growth_path"]
        out.append(f"  {name.title()} case: revenue growth {pct0(path[0])} fading to "
                   f"{pct0(path[-1])}, owner margin {pct0(a['owner_margin'])}, exit at "
                   f"{num(a['exit_multiple'], 0)} times owner earnings, {pct(a['annual_return'])} a year")
    if s.get("available"):
        out.append(f"  Expected {pct(s['expected_annual_return'])} a year against a "
                   f"{pct0(s['hurdle'])} hurdle. Business-case call: {b.get('call')}.")
    return out


def _valuation_lines(run: dict) -> list[str]:
    panel = run.get("valuation") or {}
    if not panel:
        return []
    out = ["", "VALUATION, computed, not opinion:"]
    fv = panel.get("fair_value") or {}
    if fv:
        out.append(f"  Fair value {price(fv['value'])}, {pct(fv.get('upside'))} from price, "
                   f"the median of {fv['method_count']} methods")
        for m in fv["methods"]:
            out.append(f"    {m['name']}: {price(m['value'])}")
        if fv.get("excluded_own_history"):
            out.append("    The own-history multiple was excluded: this stock's multiple "
                       "re-rated, so its historical average is not a fair value.")
    own = panel.get("own_history") or {}
    if own:
        out.append(f"  Own-history P/E: median {num(own['median_pe'], 1)} over "
                   f"{own['periods']} quarters, range {num(own['pe_q1'], 1)} to "
                   f"{num(own['pe_q3'], 1)}")
    return out


def _street_lines(ev: dict) -> list[str]:
    c = ev.get("consensus") or {}
    if not c:
        return ["", "STREET: no analyst consensus was available for this run."]
    out = ["", f"STREET: targets {price(c.get('target_low_price'))} to "
               f"{price(c.get('target_high_price'))}, mean "
               f"{price(c.get('target_mean_price'))}"]
    b = ev.get("breakdown") or {}
    if b.get("total"):
        out.append(f"  {b['total']} analysts: {b['bullish']} bullish, {b['neutral']} "
                   f"neutral, {b['bearish']} bearish")
    return out


def _event_lines(ev: dict) -> list[str]:
    out: list[str] = []
    rel = ev.get("release") or {}
    if rel:
        out += ["", f"LAST EARNINGS {rel['date']}, {rel.get('days_since')} days ago: EPS "
                    f"{num(rel.get('eps'))} against {num(rel.get('eps_estimate'))} expected, "
                    f"a surprise of {pct(rel.get('eps_surprise'))}"]
        if rel.get("revenue_surprise") is not None:
            out.append(f"  Revenue surprise {pct(rel['revenue_surprise'])}")
    cal = ev.get("calendar") or {}
    if cal.get("next_earnings"):
        out.append(f"NEXT EARNINGS: {cal['next_earnings']}")
    news = ev.get("news") or []
    if news:
        out += ["", "RECENT HEADLINES:"]
        out += [f"  {(n.get('date') or '')[:10]} {n.get('title')}" for n in news[:10]]
    return out


def _factor_lines(run: dict) -> list[str]:
    out = ["", "COMPUTED FACTOR SCORES, 0 to 100, higher is better except risk:"]
    families = run.get("factors") or {}
    for key in FAMILY_ORDER:
        fam = families.get(key)
        if not fam:
            continue
        if fam.get("score") is None:
            out.append(f"  {fam['label']}: abstained, no inputs available")
            continue
        out.append(f"  {fam['label']}: {num(fam['score'], 0)}")
    out += ["", "VERDICTS, from the formula, which you may not change:"]
    for key, v in published_verdicts(run).items():
        if key not in RATED_HORIZONS:
            call = run.get("swing") or {}
            if call.get("call"):
                out.append(f"  {v.get('label')}: {call['call']} at {num(call.get('score'), 0)} of "
                           f"100 (Buy at {num(call.get('buy_at'), 0)}). {call.get('why')}")
                for c in call.get("components") or []:
                    if c.get("points") is not None:
                        out.append(f"    {c['key']}: {c['points']:+.1f} points, "
                                   f"{'; '.join(c['notes'])}")
            else:
                out.append(f"  {v.get('label')}: no call. {call.get('why') or ''}".rstrip())
        elif v.get("quality_state") == "WITHHELD":
            out.append(f"  {v.get('label')}: withheld, {' '.join(v.get('reasons') or [])}")
        else:
            out.append(f"  {v.get('label')}: {v.get('rating')} at {num(v.get('score'), 1)} "
                       f"of 100, {v.get('conviction')} conviction")
    return out


def _quality_lines(ev: dict) -> list[str]:
    caveats = ev.get("caveats") or []
    out = ["", f"DATA QUALITY: {ev.get('quality_score')} of 100"]
    for c in caveats:
        out.append(f"  [{c['severity']}] {c['message']}")
    return out


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

LENS_SCHEMA = {
    "type": "object",
    "required": ["read", "strongest_point", "biggest_worry", "would_own"],
    "properties": {
        "read": {"type": "string", "max_length": 1200},
        "strongest_point": {"type": "string", "max_length": 400},
        "biggest_worry": {"type": "string", "max_length": 400},
        "would_own": {"type": "string", "enum": ["yes", "no", "not at this price",
                                                 "not enough evidence"]},
    },
}

CASE_SCHEMA = {
    "type": "object",
    "required": ["case", "points", "what_would_disprove_it"],
    "properties": {
        "case": {"type": "string", "max_length": 2200},
        "points": {"type": "array", "min_items": 2, "max_items": 5,
                   "items": {"type": "string", "max_length": 400}},
        "what_would_disprove_it": {"type": "string", "max_length": 500},
    },
}

SYNTHESIS_SCHEMA = {
    "type": "object",
    "required": ["thesis", "strongest_counterargument", "what_would_change_it",
                 "unresolved", "dissent"],
    "properties": {
        "thesis": {"type": "string", "max_length": 2000},
        "strongest_counterargument": {"type": "string", "max_length": 800},
        "what_would_change_it": {"type": "array", "min_items": 2, "max_items": 5,
                                 "items": {"type": "string", "max_length": 350}},
        "unresolved": {"type": "string", "max_length": 700},
        "dissent": {
            "type": "object",
            # reason and direction are required unconditionally, with "none" as a
            # valid direction. A conditional requirement is one the model can
            # forget, and it did: it repeatedly set disagrees and then left the
            # explanation blank, which is worse than agreeing.
            "required": ["disagrees", "direction", "reason"],
            "properties": {
                "disagrees": {"type": "boolean"},
                "horizon": {"type": "string",
                            "enum": ["swing", "long_term", "both", "none"]},
                "direction": {"type": "string",
                              "enum": ["more positive", "more negative", "none"]},
                "reason": {"type": "string", "max_length": 500},
            },
        },
    },
}


def _envelope(brief: str, role: str, task: str, spec: dict) -> str:
    from edgedesk.llm.schema import describe

    return "\n\n".join([
        f"You are {role}.",
        "EVIDENCE. This is everything you know about this company. It was gathered "
        "point-in-time and you may not add to it:",
        brief,
        task,
        _RULES,
        "Return JSON in exactly this shape:",
        describe(spec),
    ])


def lens_prompt(brief: str, key: str) -> str:
    role, focus = LENSES[key]
    return _envelope(
        brief, role,
        f"Read this evidence through your own lens. What you care about: {focus}\n\n"
        "Give your reading of this company as it stands, the single strongest point in "
        "its favour on your terms, and the thing that worries you most. You are not "
        "voting and you are not setting a rating: you are saying what this evidence "
        "looks like to someone with your priorities.",
        LENS_SCHEMA)


def bull_prompt(brief: str) -> str:
    return _envelope(
        brief, "an analyst asked to make the strongest honest case FOR owning this",
        "Build the best case the evidence supports. Use only what is above. A case that "
        "overstates what the evidence says is worse than a weak one, because it will be "
        "checked. Then say plainly what would disprove your own case.\n\n"
        "You have not seen anyone else's view and should not guess at one.",
        CASE_SCHEMA)


def bear_prompt(brief: str) -> str:
    return _envelope(
        brief, "an analyst asked to make the strongest honest case AGAINST owning this",
        "Build the best case the evidence supports. Use only what is above. A case that "
        "overstates what the evidence says is worse than a weak one, because it will be "
        "checked. Then say plainly what would disprove your own case.\n\n"
        "You have not seen anyone else's view and should not guess at one.",
        CASE_SCHEMA)


def synthesis_prompt(brief: str, bull: dict, bear: dict, lenses: dict,
                     missing: list[str] | tuple = ()) -> str:
    import json

    views = json.dumps({"bull": bull, "bear": bear, "lenses": lenses}, indent=1)
    opening = ("Two analysts argued this independently, and four investors read the same "
               "evidence through their own lenses:\n\n")
    if missing:
        # R2-02: say so rather than let an empty case read as a weak one.
        opening = (f"One analyst was asked for the {' and '.join(missing)} case and could not "
                   "produce one, so you have only one side of the argument, plus four investors "
                   "reading the same evidence through their own lenses. Say plainly in the "
                   "thesis that the other side was not made, and do not write it yourself:\n\n")
    return _envelope(
        brief,
        "the analyst who has to write down what this company is actually worth thinking",
        opening + views + "\n\n"
        "Write the thesis: what is true about this business and this price, in the "
        "light of both cases. Then the strongest argument against your own thesis, what "
        "would change your mind, and what genuinely cannot be resolved from the evidence "
        "available.\n\n"
        "Finally, the dissent. The ratings above came from a formula. If the evidence "
        "leads you somewhere the formula did not go, say so: set disagrees to true, name "
        "the horizon and the direction, and give the reason. If you do not disagree, set "
        "disagrees to false, direction and horizon to none, and use reason to say in one "
        "sentence why the formula's reading looks right to you.\n\n"
        "Always fill in reason. A dissent with no reason is worse than no dissent, because "
        "it puts a warning on the report that nobody can act on. And disagree only when you "
        "actually do: recording one you do not hold makes the ones that matter worthless.",
        SYNTHESIS_SCHEMA)


def repair_prompt(original: str, complaint: str) -> str:
    return "\n\n".join([
        original,
        "YOUR PREVIOUS REPLY WAS REJECTED.",
        complaint,
        "Return the corrected JSON only. Do not explain the correction.",
    ])
