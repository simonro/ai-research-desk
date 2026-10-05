"""The research layer's three reads, rendered for the markdown formats.

Each is labelled as a model's reading and printed beside the formula's output,
with what the code checked: quotes against the filing, the headline tally as
arithmetic, and the second look as an opinion that changed nothing.
"""

from __future__ import annotations


def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def markdown(run: dict, heading: str = "##") -> list[str]:
    llm = run.get("llm") or {}
    look, news, filing = llm.get("second_look"), llm.get("headline_read"), llm.get("filing_research")
    if not (look or news or filing):
        return []
    out = [f"{heading} Research layer", "",
           "Read by a model, fenced by code. Nothing in this section changed a number above.", ""]

    if look:
        out += [f"{heading}# Second look at the swing call", ""]
        if look.get("ok"):
            out += [f"**{look['stance'].upper()}** on the {look['against_call']}. {look.get('reason', '')}", ""]
            for c in look.get("concerns") or []:
                out.append(f"- {c.get('concern')} *(based on: {c.get('based_on')})*")
            if look.get("what_to_check"):
                out += ["", f"**Check before acting:** {look['what_to_check']}"]
            out += ["", f"*{look.get('note')}*", ""]
        else:
            out += [f"Not available: {look.get('error')}.", ""]

    if news:
        out += [f"{heading}# What the headlines say", ""]
        if news.get("ok"):
            kw = news.get("keyword_tone")
            out += [f"Of {news['headlines']} headlines in 14 days, the model judged "
                    f"{news['material']} to be about the company and material: "
                    f"{news['positive']} positive, {news['negative']} negative "
                    f"(tone {news['tone']:+.2f}; the keyword count the score uses says "
                    f"{'n/a' if kw is None else format(kw, '+.2f')}).", ""]
            for i in news.get("items") or []:
                out.append(f"- **{i['direction']}**, {i['date']}: {i['title']} *({i.get('why')})*")
            out += ["", f"*{news.get('note')}*", ""]
        else:
            out += [f"Not available: {news.get('error')}.", ""]

    if filing:
        out += [f"{heading}# What the filings say", ""]
        if filing.get("ok"):
            src = "; ".join(f"{s['form']} filed {s['filed']}" for s in
                            {s["form"] + s["filed"]: s for s in filing.get("sources") or []}.values())
            out += [f"Sources: {src}. {filing.get('summary') or ''}", ""]
            for f in filing.get("findings") or []:
                out += [f"- **{f['bearing']}, {f['direction']}.** {f['claim']}",
                        "  > \"" + f["quote"] + "\""]
            dropped = filing.get("dropped") or []
            if dropped:
                out += ["", f"*{len(dropped)} finding(s) were deleted because the quote was not in "
                            "the filing or a number was not in the quote.*"]
            r, adj = filing.get("researched"), filing.get("adjustments") or {}
            if r:
                steps = ", ".join(f"{k} {'+' if v > 0 else ''}{v} step" for k, v in r["steps"].items() if v)
                out += ["", f"**Researched case: {r['call']}, expected "
                            f"{_pct(r['expected_annual_return'])} a year**, against the formula's "
                            f"{filing.get('formula_call')} at {_pct(filing.get('formula_expected_return'))}. "
                            f"Nudges: {steps} (one step is {r['step_sizes']['growth']:.0%} on growth, "
                            f"{r['step_sizes']['margin']:.0%} on margin and multiple). {adj.get('reason') or ''}"]
            else:
                out += ["", "The filings gave no verified reason to move a scenario assumption."]
            out += ["", f"*{filing.get('note')}*", ""]
        else:
            out += [f"Not available: {filing.get('error')}", ""]
    return out
