"""The swing call, rendered once for every format.

Buy or Sell, the plan when it is a Buy, then how the call was reached: each
component with its weight, its reading, the facts behind it and whether that
kind of evidence has been tested. The standing paragraph closes every version,
so the call is never shown without what is known about it.
"""

from __future__ import annotations

from html import escape

TITLE = "Swing (2 days to 8 weeks)"


def _p(x) -> str:
    return "n/a" if x is None else f"${x:,.2f}"


def _component_line(c: dict) -> str:
    if c["points"] is None:
        return f"{c['key']} (weight {c['weight']}): no data"
    return (f"{c['key']} (weight {c['weight']}): {c['points']:+.1f} points. "
            f"{'; '.join(c['notes'])}. [{c['evidence']}]")


def plan_lines(plan: dict) -> list[str]:
    return [
        f"Entry         {_p(plan['entry'])}   {plan['entry_rule']}",
        f"Invalidation  {_p(plan['invalidation'])}   {plan['invalidation_rule']}",
        f"Size          {plan['shares']:,} shares, about ${plan['position_dollars']:,.0f}, "
        f"risking ${plan['risk_dollars']:,.0f} (${plan['risk_per_share']:,.2f} a share)",
        f"Target 1      {_p(plan['target_1'])}   {plan['target_1_r']:.1f}R, {plan['target_1_rule']}",
        f"Target 2      {_p(plan['target_2'])}   {plan['target_2_r']:.1f}R, {plan['target_2_rule']}",
        f"Time limit    {plan['time_limit_sessions']} sessions",
        f"Managing it   {plan['management']}",
    ]


def plain(run: dict, indent: str = "  ") -> list[str]:
    s = run.get("swing") or {}
    if not s.get("call"):
        return [f"{TITLE.upper()}", indent + (s.get("why") or "No swing call.")]
    out = [f"{TITLE.upper()}   {s['call'].upper()}   score {s['score']:.0f}/100 "
           f"(Buy at {s['buy_at']:.0f})",
           indent + s["why"], indent + "Action: " + s["action"]]
    for b in s.get("blockers") or []:
        out.append(indent + "Blocked: " + b)
    if s.get("plan"):
        out += [indent + line for line in plan_lines(s["plan"])]
    elif s.get("reference_invalidation"):
        out.append(indent + f"If held, the structure fails below {_p(s['reference_invalidation'])}.")
    out.append(indent + "How it was reached:")
    out += [indent + "  " + _component_line(c) for c in s.get("components") or []]
    out.append(indent + s.get("standing", ""))
    return out


def markdown(run: dict, heading: str = "###") -> list[str]:
    s = run.get("swing") or {}
    out = [f"{heading} {TITLE}", ""]
    if not s.get("call"):
        return out + [s.get("why") or "No swing call.", ""]
    out += [f"**{s['call'].upper()}**, score {s['score']:.0f} of 100 (Buy at {s['buy_at']:.0f}). "
            f"{s['why']}", "", f"- **Action:** {s['action']}"]
    out += [f"- **Blocked:** {b}" for b in s.get("blockers") or []]
    if s.get("plan"):
        p = s["plan"]
        out += ["", "| | Price | Basis |", "|---|---:|---|",
                f"| Entry | {_p(p['entry'])} | {p['entry_rule']} |",
                f"| Invalidation | {_p(p['invalidation'])} | {p['invalidation_rule']} |",
                f"| Target 1 | {_p(p['target_1'])} | {p['target_1_r']:.1f}R, {p['target_1_rule']} |",
                f"| Target 2 | {_p(p['target_2'])} | {p['target_2_r']:.1f}R, {p['target_2_rule']} |",
                "",
                f"**Size:** {p['shares']:,} shares, about ${p['position_dollars']:,.0f}, risking "
                f"${p['risk_dollars']:,.0f} at ${p['risk_per_share']:,.2f} a share. "
                f"**Time limit:** {p['time_limit_sessions']} sessions.", "",
                f"**Managing it.** {p['management']}"]
    elif s.get("reference_invalidation"):
        out += ["", f"If held, the structure fails below {_p(s['reference_invalidation'])}."]
    out += ["", "**How the call was reached**", "",
            "| Component | Weight | Points | What it saw | Evidence |", "|---|---:|---:|---|---|"]
    for c in s.get("components") or []:
        pts = "n/a" if c["points"] is None else f"{c['points']:+.1f}"
        out.append(f"| {c['key']} | {c['weight']} | {pts} | {'; '.join(c['notes'])} | "
                   f"{c['evidence']} |")
    out += ["", f"*{s.get('standing', '')}*", ""]
    return out


def html(run: dict) -> str:
    s = run.get("swing") or {}
    if not s.get("call"):
        return (f'<div class="hz"><div class="hzt">{escape(TITLE)}</div>'
                f'<div class="st muted">{escape(s.get("why") or "No swing call.")}</div></div>')
    tone = "green" if s["call"] == "Buy" else "red"
    rows = "".join(
        f'<div class="st"><b>{escape(c["key"])}</b> '
        f'({c["weight"]}): {"n/a" if c["points"] is None else format(c["points"], "+.1f")} '
        f'<span class="muted">{escape("; ".join(c["notes"]))}</span></div>'
        for c in s.get("components") or [])
    plan = ""
    if s.get("plan"):
        p = s["plan"]
        plan = (f'<div class="lv"><div><span>Entry</span><b>{_p(p["entry"])}</b></div>'
                f'<div><span>Invalidation</span><b>{_p(p["invalidation"])}</b></div>'
                f'<div><span>Target 1 ({p["target_1_r"]:.1f}R)</span><b>{_p(p["target_1"])}</b></div>'
                f'<div><span>Target 2 ({p["target_2_r"]:.1f}R)</span><b>{_p(p["target_2"])}</b></div></div>'
                f'<div class="st"><b>Size:</b> {p["shares"]:,} shares, about '
                f'${p["position_dollars"]:,.0f}, risking ${p["risk_dollars"]:,.0f}. '
                f'<b>Time limit:</b> {p["time_limit_sessions"]} sessions.</div>'
                f'<div class="st muted">{escape(p["management"])}</div>')
    blockers = "".join(f'<div class="st"><b>Blocked:</b> {escape(b)}</div>'
                       for b in s.get("blockers") or [])
    return (f'<div class="hz"><div class="hzt">{escape(TITLE)}</div>'
            f'<div class="row"><span class="pill sig-{tone}">{escape(s["call"].upper())}</span>'
            f'<span class="sigstate">score {s["score"]:.0f} of 100, Buy at {s["buy_at"]:.0f}</span></div>'
            f'<div class="st">{escape(s["why"])}</div>'
            f'<div class="st"><b>Action:</b> {escape(s["action"])}</div>{blockers}{plan}{rows}'
            f'<div class="st stand">{escape(s.get("standing", ""))}</div></div>')
