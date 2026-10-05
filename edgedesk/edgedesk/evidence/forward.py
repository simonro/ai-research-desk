"""Forward earnings, read into the few numbers the engine uses.

Two uses, and they are different questions:

* the LEVEL (forward P/E, next year's expected growth) informs the long-term case,
  where trailing figures are stale by construction for a fast grower
* the CHANGE (how next year's EPS estimate has moved in 30 and 90 days, and how many
  analysts raised against how many cut) informs the swing call. Estimate revisions are
  among the best-documented signals at a horizon of weeks; the level of a P/E is not.

What to keep in mind. Forward EPS is the adjusted figure: it leaves out stock
compensation, the largest real cost at most technology companies, so a forward P/E is
always lower than a multiple of owner earnings and must not replace it. Analyst
estimates also run high and get cut, so growth taken from them is haircut. And none of
this has free history, so it is labelled forward-test only until the snapshots this
engine saves have built a record.
"""

from __future__ import annotations

ESTIMATE_HAIRCUT = 0.85     # analysts' growth a year out has tended to come in high


def _change(now, then):
    if now is None or not then or then <= 0 or now <= 0:
        return None
    return round(now / then - 1.0, 6)


def read(raw: dict | None, valuation_now: dict | None, anchors: dict | None) -> dict | None:
    if not raw:
        return None
    this_year = (raw.get("periods") or {}).get("this_year") or {}
    next_year = (raw.get("periods") or {}).get("next_year") or {}
    price = (valuation_now or {}).get("price") or (anchors or {}).get("last_close")
    eps_next, eps_this = next_year.get("eps"), this_year.get("eps")
    up, down = next_year.get("up_30d"), next_year.get("down_30d")
    return {
        "fetched_on": raw.get("fetched_on"),
        "eps_this_year": eps_this, "eps_next_year": eps_next,
        "eps_growth_next_year": next_year.get("eps_growth"),
        "revenue_growth_this_year": this_year.get("revenue_growth"),
        "revenue_growth_next_year": next_year.get("revenue_growth"),
        "forward_pe": round(price / eps_next, 2) if price and eps_next and eps_next > 0 else None,
        "forward_pe_this_year": round(price / eps_this, 2) if price and eps_this and eps_this > 0 else None,
        "revision_30d": _change(eps_next, next_year.get("eps_30d_ago")),
        "revision_90d": _change(eps_next, next_year.get("eps_90d_ago")),
        "revision_30d_this_year": _change(eps_this, this_year.get("eps_30d_ago")),
        "raised_30d": None if up is None else int(up),
        "cut_30d": None if down is None else int(down),
        "analysts": next_year.get("analysts"),
        "haircut": ESTIMATE_HAIRCUT,
        "note": ("Adjusted EPS, which leaves out stock compensation; analyst growth is "
                 "haircut 15% wherever it is used. No free history: forward test only."),
    }
