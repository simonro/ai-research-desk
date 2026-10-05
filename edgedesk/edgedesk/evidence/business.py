"""The long-term case: what the business earns for its owners, where it is heading,
what the price already assumes, and what an owner might make from here.

A multiple says what the market pays. It does not say whether that is a good
price, because the answer depends on what the business does next. So this module
asks four questions in order, each from filed numbers, each with its arithmetic
shown:

  1. Owner economics. Of the profit reported, how much is cash the owners could
     take out, after the stock handed to employees and after reinvestment? Is
     the share count rising or falling? What does capital earn?
  2. Trajectory. Are growth and margins improving or fading? Eight filings of
     slope, not one reading of level.
  3. Expectations. What growth does today's price need? A reverse DCF, solved for
     the growth rate, set beside what the company has actually delivered.
  4. Scenario return. Bear, base and bull five-year paths, every assumption
     stated, turned into an annual return at today's price against a hurdle.

Owner earnings are operating cash flow, less stock compensation, less the capital
spending needed to stand still. Spending above depreciation is treated as growth
investment and not charged, because charging all of it makes every company in a
build-out look worthless in exactly the years it is investing; the strict figure
(all capex charged) is reported beside it, and heavy investment is flagged, because
the generous reading is only right if the new capital earns its keep. For banks
and insurers cash flow means nothing (deposits and claims run through operations),
so net income stands in and the cash tests are skipped.

Every assumption is a named constant with its reason. None was fitted to returns.
"""

from __future__ import annotations

HURDLE = 0.10            # what the index has paid over long periods; the bar to beat
DISCOUNT = 0.10
TERMINAL_GROWTH = 0.03   # about nominal GDP; nothing grows faster than that forever
MARKET_MULTIPLE = 22.0   # a long-run price to owner-earnings for the index, to fade toward
FINANCIAL_MULTIPLE = 13.0  # banks and insurers have never been paid the market's multiple
UPWARD_FADE = 0.25       # a cheap stock is assumed to close a quarter of the gap, not half:
                         # cheap is often cheap for a reason, and the asymmetry is deliberate
BUY_MARGIN = 0.03        # a Buy needs the hurdle plus this
# Growth companies. A business growing 35% does not become a 6% grower in five years
# the way a mature one settles there, and the market pays more for the one still
# growing in year five. So the year-five growth rate keeps a share of today's excess
# growth, and the exit multiple rises with it (and with a high return on capital,
# which is what makes growth worth paying for). Version 1.0 faded everyone to 6% and
# capped every exit at 30 times, which priced every fast grower as a Sell.
MATURE_GROWTH = 0.06
GROWTH_KEPT = {"bear": 0.10, "base": 0.30, "bull": 0.40}      # share of excess growth left in year 5
END_GROWTH_CAP = {"bear": 0.06, "base": 0.15, "bull": 0.20}
MULTIPLE_PER_GROWTH = 2.0   # +2% on the anchor multiple per point of year-five growth above 6%
HIGH_ROIC = 0.30            # at or above this, the anchor multiple is 10% higher
MULTIPLE_CAP = 35.0
YEARS = 5
_WEIGHTS = {"bear": 0.25, "base": 0.50, "bull": 0.25}


STANDING = (
    "What is known about this call. Version 1.1 lets a fast grower keep part of its growth "
    "in year five and sell at a higher multiple; it has NOT been re-measured since that "
    "change. Version 1.0, which faded everyone to 6% growth, was measured: on 24 large caps over 2024 to 2025 (1,896 completed "
    "twelve-month readings, weekly and overlapping), higher expected return went with better "
    "results in order: 13% or more beat SPY by a median 18 points, 7 to 13% by 4, under 7% "
    "lagged by 3. But the top group was two companies (JPM and CVX), so that is a direction, "
    "not proof. The discipline also has a known cost: it called Sell on expensive leaders "
    "that kept running, and that group's AVERAGE result was still ahead of SPY because a few "
    "of them soared. It avoids overpaying; it will miss some of the biggest winners. The "
    "scenario assumptions are judgement written down, not a fitted model. Use the expected "
    "return to compare companies on the same rules, not as a forecast.")


def _get(row: dict | None, key: str) -> float | None:
    v = (row or {}).get(key)
    return float(v) if isinstance(v, (int, float)) else None


def _div(a: float | None, b: float | None) -> float | None:
    return None if a is None or not b else a / b


def _liquid(row: dict | None) -> float | None:
    """Cash plus marketable securities when the filing reports them, else cash."""
    both = _get(row, "cash_and_investments")
    return both if both is not None else _get(row, "cash")


def _cagr(now: float | None, then: float | None, years: float) -> float | None:
    if not now or not then or now <= 0 or then <= 0 or years <= 0:
        return None
    return (now / then) ** (1 / years) - 1


def _slope(values: list[float | None]) -> float | None:
    """Least-squares slope per YEAR of a newest-first quarterly series."""
    pts = [(i, v) for i, v in enumerate(reversed(values)) if v is not None]
    if len(pts) < 5:
        return None
    n = len(pts)
    mx, my = sum(x for x, _ in pts) / n, sum(y for _, y in pts) / n
    den = sum((x - mx) ** 2 for x, _ in pts)
    return None if not den else 4.0 * sum((x - mx) * (y - my) for x, y in pts) / den


def is_financial(sic: str | int | None) -> bool:
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return False
    return 6000 <= code <= 6499


# ---------------------------------------------------------------------------
# 1. Owner economics
# ---------------------------------------------------------------------------

OWNER_INPUTS = {"operating_cash_flow": "operating cash flow", "capex": "capital spending",
                "stock_compensation": "stock compensation"}


def owner_inputs_missing(row: dict, financial: bool) -> list[str]:
    """The owner-earnings inputs this row does not report. A missing figure is unknown, not
    zero: charging nothing for capex or stock compensation overstates owner earnings (R2-06)."""
    if financial:
        return [] if _get(row, "net_income") is not None else ["net income"]
    return [label for key, label in OWNER_INPUTS.items() if _get(row, key) is None]


def owner_earnings_of(row: dict, financial: bool) -> tuple[float | None, float | None]:
    """(owner earnings, the strict version with all capex charged). None when an input is missing."""
    if financial:
        ni = _get(row, "net_income")
        return ni, ni
    if owner_inputs_missing(row, financial):
        return None, None
    ocf, capex, sbc = _get(row, "operating_cash_flow"), _get(row, "capex"), _get(row, "stock_compensation")
    ebitda, op = _get(row, "ebitda"), _get(row, "operating_income")
    depreciation = ebitda - op if ebitda is not None and op is not None else None
    maintenance = min(capex, depreciation) if depreciation and depreciation > 0 else capex
    return ocf - sbc - maintenance, ocf - sbc - capex


def owner_economics(rows: list[dict], financial: bool) -> dict:
    now = rows[0]
    old = rows[-1] if len(rows) >= 5 else None
    years = (len(rows) - 1) / 4.0
    ni, fcf, rev = _get(now, "net_income"), _get(now, "free_cash_flow"), _get(now, "revenue")
    sbc = _get(now, "stock_compensation")
    owner, owner_strict = owner_earnings_of(now, financial)
    share_change, share_basis = None, None
    if old:
        share_change = _cagr(_get(now, "diluted_shares"), _get(old, "diluted_shares"), years)
        share_basis = "diluted" if share_change is not None else None
        if share_change is None:
            share_change = _cagr(_get(now, "shares"), _get(old, "shares"), years)
            share_basis = "outstanding" if share_change is not None else None
    missing = owner_inputs_missing(now, financial)

    tax_rate = _div(_get(now, "income_tax"), _get(now, "pretax_income"))
    tax_rate = min(max(tax_rate, 0.0), 0.35) if tax_rate is not None else 0.21

    def invested(row):
        e, d, c = _get(row, "equity"), _get(row, "total_debt"), _liquid(row)
        return None if e is None else e + (d or 0.0) - (c or 0.0)

    def nopat(row):
        op = _get(row, "operating_income")
        return None if op is None else op * (1 - tax_rate)

    ic_now, ic_old = invested(now), invested(old) if old else None
    roic = _div(nopat(now), ic_now) if ic_now and ic_now > 0 else None
    inc_roic = None
    if old and ic_now and ic_old and ic_now - ic_old > 0.05 * abs(ic_old):
        a, b = nopat(now), nopat(old)
        if a is not None and b is not None:
            inc_roic = (a - b) / (ic_now - ic_old)
    debt, cash, ebitda = _get(now, "total_debt"), _liquid(now), _get(now, "ebitda")
    out = {
        "basis": ("net income (financial company)" if financial else
                  "operating cash flow less stock compensation and maintenance capital spending"),
        "owner_earnings": owner,
        "owner_earnings_strict": owner_strict,
        "owner_inputs_missing": missing,
        "owner_margin": _div(owner, rev),
        "cash_conversion": None if financial else _div(fcf, ni) if ni and ni > 0 else None,
        "accruals_to_assets": None if financial else _div(
            (ni - _get(now, "operating_cash_flow")) if ni is not None
            and _get(now, "operating_cash_flow") is not None else None, _get(now, "assets")),
        "stock_comp_to_revenue": _div(sbc, rev),
        "capex_to_revenue": _div(_get(now, "capex"), rev),
        "share_change_per_year": share_change,
        "share_basis": share_basis,
        "return_on_invested_capital": roic,
        "incremental_roic": inc_roic,
        "net_debt_to_ebitda": _div((debt or 0.0) - (cash or 0.0), ebitda) if ebitda and ebitda > 0 else None,
        "years_of_history": round(years, 2),
    }
    flags, cautions = [], []
    cc = out["cash_conversion"]
    capex_, ebitda__, op__ = _get(now, "capex"), _get(now, "ebitda"), _get(now, "operating_income")
    dep_ = ebitda__ - op__ if ebitda__ is not None and op__ is not None else None
    investing = bool(capex_ and dep_ and dep_ > 0 and capex_ > 1.5 * dep_
                     and rev and capex_ / rev >= 0.05)
    # Weak cash conversion during a build-out is the build-out, flagged once below.
    if cc is not None and cc < 0.7 and not investing:
        flags.append(f"only {cc:.0%} of reported profit arrived as free cash flow")
    acc = out["accruals_to_assets"]
    if acc is not None and acc > 0.08:
        # Weak cash conversion and high accruals are one fact seen twice, so one flag.
        if flags and "arrived as free cash flow" in flags[-1]:
            flags[-1] += f", and accruals are {acc:.0%} of assets: profit is running ahead of cash"
        else:
            flags.append(f"accruals are {acc:.0%} of assets: profit is running well ahead of cash")
    sc = out["stock_comp_to_revenue"]
    if sc is not None and sc > 0.10:
        cautions.append(f"stock compensation is {sc:.0%} of revenue (already charged against "
                        "owner earnings)")
    # A multi-year rate can hide a recent jump (AKAM: diluted -0.3% a year over the history, but
    # +5.8% in the last year), so the last year is checked on its own.
    year_ago = rows[4] if len(rows) >= 5 else None
    recent = _div(_get(now, "diluted_shares"), _get(year_ago, "diluted_shares"))
    out["diluted_change_1y"] = recent - 1 if recent else None
    if out["diluted_change_1y"] is not None and out["diluted_change_1y"] > 0.03:
        flags.append(f"diluted shares rose {out['diluted_change_1y']:.1%} in the last year")
    dil = out["share_change_per_year"]
    if dil is not None and dil > 0.02:
        flags.append(f"the {out['share_basis']} share count is growing {dil:.1%} a year")
    capex, ebitda_, op_ = _get(now, "capex"), _get(now, "ebitda"), _get(now, "operating_income")
    dep = ebitda_ - op_ if ebitda_ is not None and op_ is not None else None
    if not financial and investing:
        cautions.append(f"heavy investment: capital spending is {capex / dep:.1f} times depreciation "
                     f"({capex / rev:.0%} of revenue). Owner earnings here assume the excess is "
                     "growth that will earn its keep; with all of it charged they would be "
                     f"{owner_strict / 1e9:,.1f}B against {owner / 1e9:,.1f}B"
                     if owner is not None and owner_strict is not None and rev else
                     "heavy investment: capital spending far exceeds depreciation")
    if owner is not None and owner <= 0:
        flags.append("no positive owner earnings: after stock compensation the business "
                     "does not yet pay its owners")
    nd = out["net_debt_to_ebitda"]
    if nd is not None and nd > 3.0 and not financial:
        flags.append(f"net debt is {nd:.1f} times EBITDA")
    if roic is not None and roic < 0.08 and not financial:
        flags.append(f"return on invested capital is {roic:.0%}, under a typical cost of capital")
    strengths = []
    if roic is not None and roic >= 0.20:
        strengths.append(f"return on invested capital {roic:.0%}")
    if inc_roic is not None and inc_roic >= 0.20:
        strengths.append(f"new capital is earning {inc_roic:.0%}")
    if cc is not None and cc >= 1.0:
        strengths.append(f"free cash flow is {cc:.0%} of reported profit")
    if dil is not None and dil <= -0.01:
        strengths.append(f"the {out['share_basis']} share count is shrinking {abs(dil):.1%} a year")
    out["red_flags"], out["cautions"], out["strengths"] = flags, cautions, strengths
    return out


# ---------------------------------------------------------------------------
# 2. Trajectory
# ---------------------------------------------------------------------------

def trajectory(rows: list[dict]) -> dict:
    recent = rows[:8]
    growth = [_get(r, "revenue_growth") for r in recent]
    gm = [_get(r, "gross_margin") for r in recent]
    om = [_get(r, "operating_margin") for r in recent]
    g_slope, gm_slope, om_slope = _slope(growth), _slope(gm), _slope(om)

    def word(slope, up, down, flat="steady"):
        if slope is None:
            return "unknown"
        return up if slope > 0.02 else down if slope < -0.02 else flat

    growth_word = word(g_slope, "accelerating", "decelerating")
    margin_word = word(om_slope, "expanding", "contracting")
    # A fixed two points a year missed AKAM's margin falling from 14.4% to 10.5% over eight filings
    # (a tenth of the margin a year). So losing a tenth of the margin a year also counts as
    # contracting. Only the label: "contracting" changes no arithmetic, while "expanding" raises
    # the base-case margin, so expanding keeps the stricter absolute bar.
    level = [m for m in om if m is not None]
    level = sum(level) / len(level) if level else None
    if margin_word == "steady" and om_slope is not None and level and level > 0 \
            and om_slope / level <= -0.10:
        margin_word = "contracting"
    return {"revenue_growth_now": growth[0], "revenue_growth_slope_per_year": g_slope,
            "gross_margin_slope_per_year": gm_slope, "operating_margin_slope_per_year": om_slope,
            "growth": growth_word, "margins": margin_word, "filings_used": len(recent),
            "summary": f"Revenue growth is {growth_word} and operating margins are {margin_word} "
                       f"over the last {len(recent)} filings."}


# ---------------------------------------------------------------------------
# 3. What the price assumes
# ---------------------------------------------------------------------------

def _value(owner: float, g: float) -> float:
    """Ten years: growth g for five, fading in a line to the terminal rate, then a
    perpetuity. Returns enterprise value."""
    total, cash, growth = 0.0, owner, g
    for year in range(1, 11):
        if year > YEARS:
            growth = g + (TERMINAL_GROWTH - g) * (year - YEARS) / 5.0
        cash *= 1 + growth
        total += cash / (1 + DISCOUNT) ** year
    terminal = cash * (1 + TERMINAL_GROWTH) / (DISCOUNT - TERMINAL_GROWTH)
    return total + terminal / (1 + DISCOUNT) ** 10


def expectations(rows: list[dict], market_cap: float | None, owner: float | None,
                 financial: bool) -> dict:
    now = rows[0]
    if not market_cap or not owner or owner <= 0:
        return {"available": False,
                "why": "No positive owner earnings, so the price cannot be expressed as a "
                       "growth rate. It is a claim on profits that do not exist yet."}
    debt, cash = _get(now, "total_debt") or 0.0, _liquid(now) or 0.0
    ev = market_cap if financial else market_cap + debt - cash
    lo, hi = -0.20, 0.80
    for _ in range(60):
        mid = (lo + hi) / 2
        if _value(owner, mid) < ev:
            lo = mid
        else:
            hi = mid
    implied = (lo + hi) / 2
    old = rows[-1] if len(rows) >= 5 else None
    years = (len(rows) - 1) / 4.0
    delivered = _cagr(_get(now, "revenue"), _get(old, "revenue"), years) if old else None
    gap = None if delivered is None else implied - delivered
    if implied >= 0.79:
        verdict = "the price needs growth above 80% a year for five years: it is priced on hope"
    elif gap is None:
        verdict = f"the price needs owner earnings to grow {implied:.0%} a year for five years"
    elif gap > 0.05:
        verdict = (f"the price needs {implied:.0%} a year; the company has delivered "
                   f"{delivered:.0%}. It is priced for more than it has done.")
    elif gap < -0.05:
        verdict = (f"the price needs only {implied:.0%} a year against {delivered:.0%} "
                   "delivered. Expectations are low.")
    else:
        verdict = (f"the price needs {implied:.0%} a year, close to the {delivered:.0%} "
                   "delivered. It is priced for more of the same.")
    return {"available": True, "implied_growth_5y": round(implied, 4),
            "delivered_revenue_cagr": None if delivered is None else round(delivered, 4),
            "gap": None if gap is None else round(gap, 4), "enterprise_value": ev,
            "owner_earnings_multiple": round(market_cap / owner, 1),
            "discount_rate": DISCOUNT, "terminal_growth": TERMINAL_GROWTH, "verdict": verdict}


# ---------------------------------------------------------------------------
# 4. Scenario return
# ---------------------------------------------------------------------------

def scenarios(rows: list[dict], market_cap: float | None, owner_now: dict, traj: dict,
              dividend_yield: float | None, forward: dict | None = None,
              nudges: dict | None = None) -> dict:
    """*nudges* are fractional shifts to starting growth, base margin and the anchor
    multiple, e.g. {"growth": 0.15}. Only the research layer passes them, one bounded
    step each, and the result is published beside the un-nudged case."""
    nudges = nudges or {}
    now = rows[0]
    rev, owner, shares = _get(now, "revenue"), owner_now.get("owner_earnings"), _get(now, "shares")
    if not market_cap or not rev or not shares or not owner or owner <= 0:
        return {"available": False,
                "why": "Needs revenue, a share count and positive owner earnings."}
    price = market_cap / shares
    trailing = traj.get("revenue_growth_now")
    g0 = trailing if trailing is not None else 0.05
    # Trailing growth is stale by construction. Where the Street publishes next year's
    # revenue growth, start from halfway between the two, with the estimate haircut
    # because analysts' growth a year out has tended to come in high.
    street = (forward or {}).get("revenue_growth_next_year")
    street_used = None
    if street is not None:
        street_used = street * (forward.get("haircut") or 0.85)
        g0 = 0.5 * g0 + 0.5 * street_used
    g0 = min(max(g0 * (1 + nudges.get("growth", 0.0)), -0.05), 0.40)
    if traj.get("growth") == "decelerating":
        g0 *= 0.85
    financial = "net income" in owner_now["basis"]
    margins = [m for m in (_div(owner_earnings_of(r, financial)[0], _get(r, "revenue"))
                           for r in rows) if m is not None]
    margin_now = owner / rev
    margin_avg = sum(margins) / len(margins) if margins else margin_now
    base_margin = (margin_now + margin_avg) / 2
    dilution = owner_now.get("share_change_per_year")
    dilution = min(max(dilution if dilution is not None else 0.0, -0.04), 0.04)
    multiple_now = market_cap / owner
    excess = max(g0 - MATURE_GROWTH, 0.0)
    end_growth = {k: min((0.03 if k == "bear" else MATURE_GROWTH if k == "base" else 0.08)
                         + GROWTH_KEPT[k] * excess, END_GROWTH_CAP[k]) for k in GROWTH_KEPT}
    anchor = FINANCIAL_MULTIPLE if financial else MARKET_MULTIPLE
    if not financial:
        anchor *= 1 + MULTIPLE_PER_GROWTH * (end_growth["base"] - MATURE_GROWTH)
        roic = owner_now.get("return_on_invested_capital")
        if roic is not None and roic >= HIGH_ROIC:
            anchor *= 1.10
    cap = 18.0 if financial else MULTIPLE_CAP
    if traj.get("margins") == "expanding":
        # Operating leverage that is already showing up is not averaged away.
        base_margin = max(margin_now, base_margin)
    base_margin *= 1 + nudges.get("margin", 0.0)
    anchor *= 1 + nudges.get("multiple", 0.0)
    floor = 5.0
    if multiple_now > anchor:
        base_multiple = min(multiple_now + 0.5 * (anchor - multiple_now), cap)
    else:
        # No floor on the way up: lifting a 6x stock to 12x would assume the very
        # re-rating the quarter-gap rule exists to refuse.
        base_multiple = multiple_now + UPWARD_FADE * (anchor - multiple_now)
    dy = dividend_yield or 0.0

    specs = {
        "bear": {"start": g0 * 0.5 if g0 > 0 else g0 - 0.03, "end": end_growth["bear"],
                 "margin": base_margin * 0.75, "multiple": max(base_multiple * 0.7, floor),
                 "shares": dilution + 0.01},
        "base": {"start": g0, "end": end_growth["base"], "margin": base_margin,
                 "multiple": base_multiple, "shares": dilution},
        "bull": {"start": min(g0 * 1.25, 0.50) if g0 > 0 else 0.05, "end": end_growth["bull"],
                 "margin": min(base_margin * 1.15, 0.45), "multiple": min(base_multiple * 1.2, cap * 1.15),
                 "shares": dilution - 0.01},
    }
    out = {}
    for name, a in specs.items():
        r, growth_path = rev, []
        for year in range(1, YEARS + 1):
            g = a["start"] + (a["end"] - a["start"]) * (year - 1) / (YEARS - 1)
            r *= 1 + g
            growth_path.append(round(g, 4))
        owner_5 = r * a["margin"]
        shares_5 = shares * (1 + a["shares"]) ** YEARS
        price_5 = owner_5 * a["multiple"] / shares_5
        cagr = (price_5 / price) ** (1 / YEARS) - 1 if price_5 > 0 else -1.0
        out[name] = {"revenue_growth_path": growth_path, "owner_margin": round(a["margin"], 4),
                     "exit_multiple": round(a["multiple"], 1),
                     "share_change_per_year": round(a["shares"], 4),
                     "price_in_5y": round(price_5, 2),
                     "annual_return": round(cagr + dy, 4)}
    expected = sum(_WEIGHTS[k] * out[k]["annual_return"] for k in out)
    return {"available": True, "price_now": round(price, 2), "scenarios": out,
            "expected_annual_return": round(expected, 4), "hurdle": HURDLE,
            "dividend_yield": round(dy, 4), "weights": _WEIGHTS,
            "assumptions": {
                "owner_margin_now": round(margin_now, 4), "owner_margin_average": round(margin_avg, 4),
                "multiple_now": round(multiple_now, 1), "multiple_anchor": round(anchor, 1),
                "year_five_growth": {k: round(v, 4) for k, v in end_growth.items()},
                "starting_growth": {"trailing": trailing, "street_next_year": street,
                                    "street_after_haircut": street_used, "used": round(g0, 4)},
                "note": ("Growth fades in a line from its starting rate to the year-five rate. A "
                         "mature company ends at 6%; a fast grower keeps 30% of its growth above "
                         "6% (capped at 15%), and the multiple it is assumed to sell at rises "
                         "with that and with a return on capital of 30% or more. The base margin is halfway between today's and the average of "
                         "the filings on hand. An expensive stock's exit multiple is halfway "
                         "between today's and the anchor (22 for the market, 13 for financials): "
                         "a great company bought at 60 times is assumed to be sold nearer 40. A "
                         "cheap stock closes only a quarter of the gap upward, because cheap is "
                         "often cheap for a reason.")}}


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------

def analyze(rows: list[dict], valuation_now: dict | None, sic, dividend_yield: float | None,
            forward: dict | None = None, nudges: dict | None = None) -> dict:
    """Newest-first metrics rows in, the whole long-term case out."""
    rows = [r for r in rows or [] if r]
    if not rows or _get(rows[0], "revenue") is None:
        return {"available": False, "call": None,
                "why": "The filings on hand do not carry the raw figures this analysis needs."}
    financial = is_financial(sic)
    cap = _get(valuation_now, "market_cap") if (valuation_now or {}).get("available") \
        else _get(rows[0], "market_cap")
    owners = owner_economics(rows, financial)
    traj = trajectory(rows)
    if (rows[0] or {}).get("debt_unresolved") and not financial:
        return {"available": False, "call": None, "version": "1.1.1",
                "why": ("The filings show interest expense but no debt figure Edge can read, so net "
                        "debt and enterprise value are unknown. No long-term rating rather than one "
                        "that counts the debt as zero."),
                "financial_company": financial, "owner_economics": owners, "trajectory": traj}
    if owners["owner_inputs_missing"]:
        # Unknown is not the same as negative: withhold rather than fall through to the Sell
        # that genuinely absent profits get.
        return {"available": False, "call": None, "version": "1.1.1",
                "why": ("Owner earnings cannot be computed: the latest filing does not report "
                        + " or ".join(owners["owner_inputs_missing"])
                        + ". No long-term rating rather than one built on a zero."),
                "financial_company": financial, "owner_economics": owners, "trajectory": traj}
    expect = expectations(rows, cap, owners["owner_earnings"], financial)
    scen = scenarios(rows, cap, owners, traj, dividend_yield, forward, nudges)

    flags = owners["red_flags"]
    reasons, call = [], None
    if scen.get("available"):
        exp, bear = scen["expected_annual_return"], scen["scenarios"]["bear"]["annual_return"]
        if exp >= HURDLE + BUY_MARGIN:
            call = "Buy"
        elif exp >= HURDLE - 0.03:
            call = "Hold"
        else:
            call = "Sell"
        reasons.append(f"Expected return {exp:.1%} a year against a {HURDLE:.0%} hurdle "
                       f"(bear {bear:.1%}, base {scen['scenarios']['base']['annual_return']:.1%}, "
                       f"bull {scen['scenarios']['bull']['annual_return']:.1%}).")
        if call == "Buy" and bear < -0.10:
            call = "Hold"
            reasons.append(f"Held back from Buy: the bear case loses {abs(bear):.0%} a year.")
        if call == "Buy" and len(flags) >= 2:
            call = "Hold"
            reasons.append(f"Held back from Buy: {len(flags)} earnings-quality flags.")
        if call == "Buy" and expect.get("gap") is not None and expect["gap"] > 0.05 \
                and traj.get("growth") == "decelerating":
            call = "Hold"
            reasons.append("Held back from Buy: the price needs more growth than the company "
                           "has delivered, and growth is slowing.")
    else:
        call = "Sell"
        reasons.append("No positive owner earnings, so there is no business case to value: "
                       "owning it is a bet on a turn that the filings do not yet show.")
    if expect.get("available"):
        reasons.append("What the price assumes: " + expect["verdict"])
    if forward and forward.get("forward_pe"):
        multiple = expect.get("owner_earnings_multiple")
        reasons.append(
            f"Forward P/E {forward['forward_pe']:.0f} on next year's adjusted EPS"
            + (f", against {multiple:.0f} times owner earnings. The gap is mostly stock "
               "compensation, which adjusted EPS leaves out, plus a year of expected growth."
               if multiple else ".")
            + (f" The Street expects revenue to grow {forward['revenue_growth_next_year']:.0%} "
               "next year." if forward.get("revenue_growth_next_year") is not None else ""))
    reasons.append(traj["summary"])
    if owners["strengths"]:
        reasons.append("Strengths: " + "; ".join(owners["strengths"]) + ".")
    if flags:
        reasons.append("Flags: " + "; ".join(flags) + ".")
    if owners.get("cautions"):
        reasons.append("Worth knowing: " + "; ".join(owners["cautions"]) + ".")
    return {"available": True, "version": "1.1.1", "call": call, "reasons": reasons,
            "financial_company": financial, "owner_economics": owners, "trajectory": traj,
            "expectations": expect, "scenario_return": scen, "forward": forward,
            "standing": STANDING}
