"""Valuation at the moment of decision, kept apart from valuation at filing.

Every fundamentals row is priced on the day its filing became public. That is
the right history: a P/E series built that way is a genuine record of what the
market paid each quarter. It is the wrong "now". A filing can be three months
old, and a stock that has moved 25% since is not trading on the multiple the
filing-date row reports.

So the rows stay as they are, and this module restates the newest row's
price-dependent figures at the decision price. One scale factor does it:

    k = decision price / (filing-date price, moved onto the as-of share basis)

Equity multiples scale by k, yields by 1/k, and enterprise value is rebuilt from
the restated market cap plus the same net debt. Everything that scores, prints or
briefs a current valuation reads the restated row; the history percentile still
compares it against the filing-date series it belongs with.
"""

from __future__ import annotations

# Fields that move one-for-one with price, and the ones that move inversely.
_SCALES_UP = ("market_cap", "price_to_earnings_ratio", "price_to_book_ratio",
              "price_to_sales_ratio", "peg_ratio")
_SCALES_DOWN = ("free_cash_flow_yield",)


def scale_factor(latest: dict, price: float | None) -> tuple[float | None, str]:
    """k, and how it was obtained."""
    if not price or not latest:
        return None, "no decision price"
    filed_price = latest.get("price_at_filing")
    split = latest.get("split_factor_to_as_of") or 1.0
    if filed_price:
        return price / (filed_price / split), "filing-date price and split factor"
    # Rows written before the filing price was recorded: P/E is market cap over
    # net income and EPS is on the as-of share basis, so price / EPS over the
    # filing-date P/E is the same ratio whenever earnings are positive.
    pe, eps = latest.get("price_to_earnings_ratio"), latest.get("earnings_per_share")
    if pe and eps and eps > 0:
        return (price / eps) / pe, "price over EPS against the filing-date P/E"
    return None, "no filing-date price and no positive earnings to infer it from"


def valuation_now(latest: dict | None, price: float | None, price_basis: str,
                  as_of: str) -> dict | None:
    """The newest row's price-dependent figures, restated at the decision price."""
    if not latest:
        return None
    k, method = scale_factor(latest, price)
    if k is None or k <= 0:
        return {"available": False, "why": method, "price": price, "as_of": as_of}

    out: dict = {"available": True, "as_of": as_of, "price": round(price, 4),
                 "price_basis": price_basis, "scale": round(k, 6), "method": method,
                 "filing_date": latest.get("filing_date")}
    for key in _SCALES_UP:
        value = latest.get(key)
        out[key] = None if value is None else round(value * k, 4)
    for key in _SCALES_DOWN:
        value = latest.get(key)
        out[key] = None if value is None else round(value / k, 6)

    cap_then, ev_then = latest.get("market_cap"), latest.get("enterprise_value")
    if cap_then is not None and ev_then is not None and out["market_cap"] is not None:
        ev_now = out["market_cap"] + (ev_then - cap_then)        # same net debt
        out["enterprise_value"] = round(ev_now, 0)
        for key in ("enterprise_value_to_ebitda_ratio", "enterprise_value_to_revenue_ratio"):
            then = latest.get(key)
            out[key] = (round(then * ev_now / ev_then, 4)
                        if then is not None and ev_then else None)
    out["at_filing"] = {key: latest.get(key) for key in (*_SCALES_UP, *_SCALES_DOWN)}
    return out


def decision_row(latest: dict | None, now: dict | None) -> dict:
    """The newest metrics row with current valuation laid over it.

    Falls back to the filing-date row untouched when nothing could be restated,
    and the caller is expected to have warned about that.
    """
    row = dict(latest or {})
    if now and now.get("available"):
        for key in (*_SCALES_UP, *_SCALES_DOWN, "enterprise_value",
                    "enterprise_value_to_ebitda_ratio", "enterprise_value_to_revenue_ratio"):
            if key in now:
                row[key] = now[key]
    return row
