"""The valuation panel: fair value ranges and the Street, computed in code.

Asking a model for "a fair value" gets a confident number with no provenance.
Every figure here comes from a stated method over data the investor can check:

    own-history multiple   TTM EPS x the stock's own median P/E (quartiles for the range)
    PEG = 1 (Lynch)        TTM EPS x its EPS growth rate in percent, capped at 30
    Street targets         analysts' low / mean / high price targets
    target changes         recent raises and cuts, parsed from Benzinga headlines

Ported from the desk (`desk/desk/valuation.py`) unchanged in method. Pure standard
library and pure function, so the same evidence always produces the same panel.
"""

from __future__ import annotations

import re
from statistics import median

_PEG_CAP = 30.0             # Lynch rarely paid more than ~30x for any growth rate
_MIN_PE_PERIODS = 4         # a median multiple needs at least a year of quarters
_GROWTH_YEARS = 3           # EPS CAGR window, in years of trailing-twelve-month rows

_TARGET_RE = re.compile(
    r"^(?P<firm>.+?)\s+(?P<action>Maintains|Reiterates|Upgrades|Downgrades|Initiates Coverage On|"
    r"Assumes|Resumes|Reinstates)\b(?P<middle>.*?)"
    r"(?:(?P<direction>Raises|Lowers|Maintains|Announces|Keeps)\s+)?Price Target\s+(?:to|of|at)\s+\$(?P<pt>[\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
_RATING_RE = re.compile(r"(?:to|with|Maintains|Reiterates)\s+(?P<rating>Strong Buy|Buy|Outperform|Overweight|"
                        r"Neutral|Hold|Equal[- ]Weight|Market Perform|Sector Perform|Peer Perform|In-Line|"
                        r"Underweight|Underperform|Sell)\b", re.IGNORECASE)


def build_valuation(price: float, rows: list[dict], consensus: dict | None,
                    headlines: list[tuple[str, str]]) -> dict:
    """rows: newest first, {"period", "pe", "eps"}; headlines: (iso date, title)."""
    eps = rows[0]["eps"] if rows else None
    panel: dict = {
        "price": round(price, 2),
        "eps_ttm": eps,
        "pe_now": round(price / eps, 2) if eps and eps > 0 else None,
        "own_history": _own_history(price, eps, rows),
        "peg": _peg(price, eps, rows),
        "street": _street(price, consensus),
        "target_changes": parse_target_changes(headlines),
    }
    return panel


def _own_history(price: float, eps: float | None, rows: list[dict]) -> dict | None:
    pes = sorted(r["pe"] for r in rows if r.get("pe") and r["pe"] > 0)
    if not eps or eps <= 0 or len(pes) < _MIN_PE_PERIODS:
        return None
    q1, mid, q3 = _quantile(pes, 0.25), median(pes), _quantile(pes, 0.75)
    fair_low, fair_mid, fair_high = eps * q1, eps * mid, eps * q3
    return {"median_pe": round(mid, 1), "pe_q1": round(q1, 1), "pe_q3": round(q3, 1),
            "periods": len(pes), "fair_low": round(fair_low, 2), "fair_mid": round(fair_mid, 2),
            "fair_high": round(fair_high, 2), "price_vs_mid": round(price / fair_mid - 1, 4)}


def _peg(price: float, eps: float | None, rows: list[dict]) -> dict | None:
    span = min(len(rows) - 1, _GROWTH_YEARS * 4)
    if not eps or eps <= 0 or span < 4:
        return None
    old = rows[span].get("eps")
    if not old or old <= 0:
        return None
    years = span / 4
    growth = (eps / old) ** (1 / years) - 1
    if growth <= 0:
        return {"eps_cagr": round(growth, 4), "years": years, "fair_value": None,
                "note": "EPS is not growing, so PEG gives no fair value"}
    fair_pe = min(growth * 100, _PEG_CAP)
    fair = eps * fair_pe
    return {"eps_cagr": round(growth, 4), "years": years, "fair_pe": round(fair_pe, 1),
            "capped": growth * 100 > _PEG_CAP, "fair_value": round(fair, 2),
            "price_vs_fair": round(price / fair - 1, 4)}


def _street(price: float, consensus: dict | None) -> dict | None:
    if not consensus or not consensus.get("target_mean_price"):
        return None
    mean = consensus["target_mean_price"]
    return {"analysts": consensus.get("analyst_count"),
            "rating": consensus.get("recommendation_key"),
            "rating_mean": consensus.get("recommendation_mean"),
            "target_low": consensus.get("target_low_price"), "target_mean": mean,
            "target_high": consensus.get("target_high_price"),
            "upside_to_mean": round(mean / price - 1, 4)}


def parse_target_changes(headlines: list[tuple[str, str]]) -> dict:
    changes = []
    for day, title in headlines:
        m = _TARGET_RE.search(title or "")
        if not m:
            continue
        rating = _RATING_RE.search(m.group("action") + m.group("middle"))
        direction = (m.group("direction") or "").lower()
        changes.append({
            "date": (day or "")[:10],
            "firm": m.group("firm").strip(),
            "action": m.group("action").title(),
            "rating": rating.group("rating").title() if rating else None,
            "target": float(m.group("pt").replace(",", "")),
            "direction": {"raises": "raised", "lowers": "lowered", "announces": "new",
                          "maintains": "kept", "keeps": "kept"}.get(direction, "set"),
        })
    changes.sort(key=lambda c: c["date"], reverse=True)
    return {
        "count": len(changes),
        "raised": sum(1 for c in changes if c["direction"] == "raised"),
        "lowered": sum(1 for c in changes if c["direction"] == "lowered"),
        "latest": changes[:8],
    }


def _quantile(sorted_values: list[float], q: float) -> float:
    pos = (len(sorted_values) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def render_text(panel: dict) -> str:
    """The panel as plain text for the managers' prompts."""
    if not panel:
        return "(valuation panel unavailable)"
    lines = [f"Price (last settled close): {panel['price']}  |  TTM EPS: {panel['eps_ttm']}  |  "
             f"P/E now: {panel['pe_now']}"]
    h = panel.get("own_history")
    lines.append(
        f"Own-history multiple: median P/E {h['median_pe']} over {h['periods']} quarters (IQR {h['pe_q1']}-"
        f"{h['pe_q3']}) -> fair value {h['fair_low']} / {h['fair_mid']} / {h['fair_high']}; price is "
        f"{h['price_vs_mid']:+.1%} vs the mid" if h else "Own-history multiple: not enough positive-P/E history")
    p = panel.get("peg")
    if p and p.get("fair_value"):
        lines.append(f"PEG = 1: EPS CAGR {p['eps_cagr']:.1%} over {p['years']:.1f} years -> fair P/E {p['fair_pe']}"
                     f"{' (capped)' if p['capped'] else ''} -> fair value {p['fair_value']}; price is "
                     f"{p['price_vs_fair']:+.1%} vs it")
    else:
        lines.append(f"PEG = 1: {p['note'] if p else 'not enough EPS history'}")
    s = panel.get("street")
    lines.append(
        f"Street: {s['analysts']} analysts, consensus {s['rating']} ({s['rating_mean']}), targets low "
        f"{s['target_low']} / mean {s['target_mean']} / high {s['target_high']}; mean is "
        f"{s['upside_to_mean']:+.1%} from price" if s else "Street: consensus unavailable")
    t = panel.get("target_changes") or {}
    if t.get("count"):
        lines.append(f"Recent price-target actions (Benzinga, ~120 days): {t['count']} ({t['raised']} raised, "
                     f"{t['lowered']} lowered)")
        lines += [f"  {c['date']} {c['firm']}: {c['action']}{' ' + c['rating'] if c['rating'] else ''}, "
                  f"target {c['direction']} ({c['target']:g})" for c in t["latest"]]
    return "\n".join(lines)


def rows_from_metrics(metrics: list[dict]) -> list[dict]:
    """The `{"period", "pe", "eps"}` rows this module wants, from evidence metrics.

    Each row's P/E was computed against the price on the day that filing became
    public, so the median is a genuine own-history multiple and not today's
    price divided by old earnings.
    """
    return [{"period": m.get("report_period"),
             "pe": m.get("price_to_earnings_ratio"),
             "eps": m.get("earnings_per_share")}
            for m in metrics]


def fair_value(panel: dict | None, rerated: bool = False) -> dict | None:
    """One fair-value number, and the honest range around it.

    The panel deliberately keeps its three methods apart, because averaging an
    own-history multiple, a PEG target and the Street's mean into a single
    figure hides how much they disagree. But a card needs a number, so this
    reports the MEDIAN of the methods that are available, alongside the spread
    and the count, so a wide spread is visible rather than smoothed away.

    A re-rated own-history multiple is dropped, for the same reason it is
    dropped from the trim and accumulation levels: it describes a market that
    has already changed its mind.
    """
    if not panel:
        return None
    methods: list[tuple[str, float]] = []
    own = (panel.get("own_history") or {}).get("fair_mid")
    if own and not rerated:
        methods.append(("Own-history multiple", own))
    peg = (panel.get("peg") or {}).get("fair_value")
    if peg:
        methods.append(("PEG at 1", peg))
    street = (panel.get("street") or {}).get("target_mean")
    if street:
        methods.append(("Street mean target", street))
    if not methods:
        return None

    values = sorted(v for _, v in methods)
    mid = median(values)
    price = panel.get("price")
    return {
        "value": round(mid, 2),
        "low": round(values[0], 2),
        "high": round(values[-1], 2),
        "methods": [{"name": n, "value": round(v, 2)} for n, v in methods],
        "method_count": len(methods),
        "spread": round(values[-1] / values[0] - 1, 4) if values[0] else None,
        "upside": round(mid / price - 1, 4) if price else None,
        "excluded_own_history": bool(own and rerated),
        "rule": ("The median of the methods that could be computed. The spread between them "
                 "is reported rather than smoothed: three methods that agree and three that "
                 "disagree by half are not the same answer."),
    }
