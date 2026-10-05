"""The long-term business case, rendered once for every format.

The call and its reasons first, then the four pieces of working behind it: what
the business earns for owners, where it is heading, what the price assumes, and
the three five-year paths with every assumption printed. The factor screen keeps
its own block; this one is the argument, and the two are allowed to disagree.
"""

from __future__ import annotations

from html import escape

TITLE = "Long-term business case (1 to 5 years)"


def _pct(x, digits: int = 1) -> str:
    return "n/a" if x is None else f"{x * 100:+.{digits}f}%"


def _pct0(x) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def _money(x) -> str:
    if x is None:
        return "n/a"
    for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(x) >= size:
            return f"${x / size:,.1f}{unit}"
    return f"${x:,.0f}"


def _scenario_rows(b: dict) -> list[tuple]:
    s = (b.get("scenario_return") or {}).get("scenarios") or {}
    rows = []
    for name in ("bear", "base", "bull"):
        a = s.get(name)
        if a:
            path = a["revenue_growth_path"]
            rows.append((name.title(), f"{path[0] * 100:.0f}% fading to {path[-1] * 100:.0f}%",
                         _pct0(a["owner_margin"]), f"{a['exit_multiple']:.0f}x",
                         _pct(a["share_change_per_year"]), f"${a['price_in_5y']:,.2f}",
                         _pct(a["annual_return"])))
    return rows


def facts(b: dict) -> list[str]:
    o, e = b.get("owner_economics") or {}, b.get("expectations") or {}
    out = [f"Owner earnings {_money(o.get('owner_earnings'))} "
           f"({_pct0(o.get('owner_margin'))} of revenue; {_money(o.get('owner_earnings_strict'))} "
           f"with all capital spending charged). Basis: {o.get('basis')}.",
           f"Return on invested capital {_pct0(o.get('return_on_invested_capital'))}, on new "
           f"capital {_pct0(o.get('incremental_roic'))}. Cash conversion "
           f"{_pct0(o.get('cash_conversion'))}. Stock compensation "
           f"{_pct0(o.get('stock_comp_to_revenue'))} of revenue. Share count "
           f"{_pct(o.get('share_change_per_year'))} a year. Net debt to EBITDA "
           f"{'n/a' if o.get('net_debt_to_ebitda') is None else format(o['net_debt_to_ebitda'], '.1f')}."]
    if e.get("available"):
        out.append(f"Price is {e['owner_earnings_multiple']:.0f} times owner earnings. Reverse DCF "
                   f"at a {e['discount_rate']:.0%} discount rate: {e['verdict']}")
    elif e.get("why"):
        out.append(e["why"])
    return out


def plain(run: dict, indent: str = "  ") -> list[str]:
    b = run.get("long_term") or {}
    if not b.get("call"):
        return [TITLE.upper(), indent + (b.get("why") or "No business case could be built.")]
    s = b.get("scenario_return") or {}
    head = f"{TITLE.upper()}   {b['call'].upper()}"
    if s.get("available"):
        head += (f"   expected {_pct(s['expected_annual_return'])} a year against a "
                 f"{s['hurdle']:.0%} hurdle")
    out = [head] + [indent + r for r in b.get("reasons") or []]
    out += [indent + f for f in facts(b)]
    for row in _scenario_rows(b):
        out.append(indent + f"{row[0]:5} growth {row[1]}, margin {row[2]}, exit {row[3]}, shares "
                            f"{row[4]} a year: {row[5]} in 5 years, {row[6]} a year")
    out.append(indent + b.get("standing", ""))
    return out


def markdown(run: dict, heading: str = "###") -> list[str]:
    b = run.get("long_term") or {}
    out = [f"{heading} {TITLE}", ""]
    if not b.get("call"):
        return out + [b.get("why") or "No business case could be built.", ""]
    s = b.get("scenario_return") or {}
    lead = f"**{b['call'].upper()}**"
    if s.get("available"):
        lead += (f", expected return {_pct(s['expected_annual_return'])} a year against a "
                 f"{s['hurdle']:.0%} hurdle")
    out += [lead + ".", ""] + [f"- {r}" for r in b.get("reasons") or []] + [""]
    out += ["**The working**", ""] + [f"- {f}" for f in facts(b)] + [""]
    rows = _scenario_rows(b)
    if rows:
        out += ["| Scenario | Revenue growth | Owner margin | Exit multiple | Shares a year | "
                "Price in 5 years | Annual return |", "|---|---|---:|---:|---:|---:|---:|"]
        out += ["| " + " | ".join(r) + " |" for r in rows]
        out += ["", f"*{(s.get('assumptions') or {}).get('note', '')} Weights: bear 25%, base 50%, "
                    "bull 25%.*", ""]
    out += [f"*{b.get('standing', '')}*", ""]
    return out


def html(run: dict) -> str:
    b = run.get("long_term") or {}
    if not b.get("call"):
        return (f'<div class="hz"><div class="hzt">{escape(TITLE)}</div><div class="st muted">'
                f'{escape(b.get("why") or "No business case could be built.")}</div></div>')
    s = b.get("scenario_return") or {}
    tone = {"Buy": "green", "Hold": "amber", "Sell": "red"}.get(b["call"], "amber")
    sub = (f'expected {_pct(s["expected_annual_return"])} a year, hurdle {s["hurdle"]:.0%}'
           if s.get("available") else "")
    reasons = "".join(f'<div class="st">{escape(r)}</div>' for r in b.get("reasons") or [])
    working = "".join(f'<div class="st muted">{escape(f)}</div>' for f in facts(b))
    rows = "".join(
        f'<div class="st"><b>{escape(r[0])}</b>: growth {escape(r[1])}, margin {escape(r[2])}, '
        f'exit {escape(r[3])}: {escape(r[5])} in 5 years, <b>{escape(r[6])}</b> a year</div>'
        for r in _scenario_rows(b))
    return (f'<div class="hz"><div class="hzt">{escape(TITLE)}</div>'
            f'<div class="row"><span class="pill sig-{tone}">{escape(b["call"].upper())}</span>'
            f'<span class="sigstate">{escape(sub)}</span></div>{reasons}{working}{rows}'
            f'<div class="st stand">{escape(b.get("standing", ""))}</div></div>')
