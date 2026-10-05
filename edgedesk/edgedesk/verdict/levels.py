"""Actionable levels, computed. The model never invents a price.

Both horizons draw on the same primitives: support and resistance from the bar
history, ATR for how far this stock normally travels, the moving averages, the
52-week range, and the valuation panel. What differs is the question each one
answers, and forcing them to share one set of rules would make both worse.

    Swing          entry zone, structural invalidation, first target,
                   second target, reward to risk
    Long term      accumulation zone, valuation range, thesis invalidation,
                   trim and re-evaluate zone, next review

The difference matters most at the stop. A close below the 50-day average can
end a three-week trade and mean nothing at all to a five-year thesis, so the
long-term invalidation is written as business conditions plus a price far below,
not as a chart level. Getting that wrong is how a good long-term holding gets
sold on a bad month.

Every level carries the rule that produced it, so a number in a report can
always be argued with on its method.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

# Quarters are about 91 days; earnings land a few weeks after the quarter ends.
_QUARTER_DAYS = 91
_REPORT_LAG_DAYS = 25
# How far below support a swing stop sits, in ATRs: enough room for ordinary
# noise, close enough that the trade is invalidated rather than merely painful.
_STOP_ATR = 0.6
# A long-term thesis is not broken by a bad month. This is the drawdown from
# today that says the market is pricing something the thesis does not contain.
_LT_BREAK_DRAWDOWN = 0.35
# How far a swing target may sit above price, in ATRs. Six weeks of ordinary
# movement, not the furthest line on the chart.
_MAX_TARGET_ATR = 6.0


@dataclass(frozen=True)
class Level:
    name: str
    price: float | None
    rule: str
    band_low: float | None = None
    band_high: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _r(v: float | None, digits: int = 2) -> float | None:
    return None if v is None else round(v, digits)


def swing_levels(anchors: dict) -> dict:
    """Entry, invalidation and targets for a two-to-six-week hold."""
    close = anchors.get("last_close")
    atr = anchors.get("atr_14")
    if not close or not atr:
        return {"available": False,
                "why": "Needs a last close and a 14-day ATR; the price history is too short."}

    sma20, sma50 = anchors.get("sma_20"), anchors.get("sma_50")
    low_20, high_20 = anchors.get("low_20d"), anchors.get("high_20d")
    high_63, high_52w = anchors.get("high_63d"), anchors.get("high_52w")

    # Entry: the nearest real support under price, not a round number.
    supports = [s for s in (sma20, sma50, low_20) if s and s < close]
    if supports:
        support = max(supports)
        entry_low, entry_high = support, min(close, support + 0.5 * atr)
        entry_rule = ("The nearest support beneath price (higher of the 20-day average, "
                      "50-day average and 20-day low), up to half an ATR above it")
    else:
        # Price is below every anchor: the pullback already happened.
        entry_low, entry_high = (low_20 or close - atr), min(close, (sma20 or close))
        entry_rule = ("Price is under every moving average, so the zone runs from the "
                      "20-day low up to the 20-day average")

    stop_base = min(x for x in (low_20, sma50, entry_low) if x)
    stop = stop_base - _STOP_ATR * atr

    # Deduplicated: the 20-day, 3-month and 52-week highs are often the same
    # bar, and two targets at one price is not two targets.
    resistances = sorted({round(r, 2) for r in (high_20, high_63, high_52w)
                          if r and r > close})
    # Capped in ATRs. A level twenty ATRs overhead is a real level and a useless
    # target: the stock does not travel that far in six weeks, so quoting it
    # produces a flattering reward-to-risk for a move that will not happen.
    reach = close + _MAX_TARGET_ATR * atr
    reachable = [r for r in resistances if r <= reach]
    if reachable:
        target1, target1_rule = reachable[0], (
            "The nearest overhead level from the 20-day, 3-month and 52-week highs")
    else:
        target1 = reach
        target1_rule = (
            f"{_MAX_TARGET_ATR} ATRs above price. "
            + (f"The nearest real resistance ({resistances[0]:g}) is further than this stock "
               "travels in six weeks, so it is not quoted as a target."
               if resistances else "There is no overhead resistance inside the year."))
    if len(reachable) > 1:
        target2, target2_rule = reachable[1], "The next distinct level above that"
    else:
        target2 = target1 + 2 * atr
        target2_rule = ("Two ATRs beyond the first target: no second reachable level "
                        "sits overhead")

    entry_mid = (entry_low + entry_high) / 2
    reward = target1 - entry_mid
    risk = entry_mid - stop
    rr = round(reward / risk, 2) if risk > 0 else None

    return {
        "available": True,
        "entry_zone": Level("Entry zone", _r(entry_mid), entry_rule,
                            _r(entry_low), _r(entry_high)).to_dict(),
        "invalidation": Level(
            "Structural invalidation", _r(stop),
            f"{_STOP_ATR} ATR below the lower of the 20-day low and the 50-day average. "
            "A close beneath it means the setup that justified the trade is gone."
        ).to_dict(),
        "target_1": Level("First target", _r(target1), target1_rule).to_dict(),
        "target_2": Level("Second target", _r(target2), target2_rule).to_dict(),
        "reward_to_risk": rr,
        "atr_14": _r(atr),
        "note": ("Reward to risk is measured from the middle of the entry zone. Entering "
                 "above the zone lowers it; the level to change is the entry, not the stop."),
    }


def long_term_levels(anchors: dict, panel: dict | None, metrics: dict | None,
                     release: dict | None, rerated: bool = False,
                     calendar: dict | None = None) -> dict:
    """Accumulation, valuation range, thesis break and review for a multi-year hold."""
    close = anchors.get("last_close")
    if not close:
        return {"available": False, "why": "No settled close is available."}

    sma200 = anchors.get("sma_200")
    own = (panel or {}).get("own_history") or {}
    peg = (panel or {}).get("peg") or {}
    street = (panel or {}).get("street") or {}

    fair_low, fair_mid, fair_high = own.get("fair_low"), own.get("fair_mid"), own.get("fair_high")

    # Accumulation is a CEILING, not a band: a price at or under it qualifies,
    # and cheaper is better. That is the opposite of the swing entry zone, where
    # under the zone means the setup broke, and the two must not be read alike.
    #
    # The ceiling takes the LOWER of the available fair-value anchors rather than
    # the higher one, so the level errs toward paying less. An own-history mid
    # that the run has flagged as re-rated is dropped from the candidates, for
    # the same reason it is dropped from the trim level.
    acc_low = acc_high = None
    anchors_for_ceiling = [v for v in ((None if rerated else fair_mid),
                                       street.get("target_mean")) if v]
    if anchors_for_ceiling:
        acc_high = min(anchors_for_ceiling)
        acc_rule = ("The lower of the own-history fair-value mid and the Street mean target"
                    if len(anchors_for_ceiling) > 1 else
                    "The one fair-value anchor available")
        if rerated:
            acc_rule += ("; the own-history mid is excluded because the multiple re-rated")
        acc_rule += ". Buying at or under this qualifies, and further under is better."
    elif sma200:
        acc_high = sma200
        acc_rule = ("No usable fair-value anchor, so the 200-day average stands in as the "
                    "level below which the price is at least not extended")
    else:
        acc_rule = "Not enough valuation or price history to place an accumulation level"

    if acc_high is not None:
        # The floor is context: the support that would make it a clear bargain
        # rather than merely acceptable.
        floors = [v for v in (sma200, fair_low) if v and v < acc_high]
        acc_low = max(floors) if floors else round(acc_high * 0.85, 4)

    # When the multiple has re-rated, the own-history ceiling is a memory of a
    # market that no longer exists, and using it as a trim target contradicts the
    # caveat the same run prints. It is dropped from the candidates rather than
    # quietly kept.
    tops = [v for v in (None if rerated else fair_high,
                        peg.get("fair_value"), street.get("target_high")) if v]
    if tops:
        trim = max(tops)
        trim_rule = ("The highest of "
                     + ("the PEG fair value and the Street high target, with the "
                        "own-history ceiling excluded because the multiple re-rated"
                        if rerated else
                        "the own-history fair-value ceiling, the PEG fair value and the "
                        "Street high target")
                     + ": above it the thesis is being paid for in full")
    else:
        trim = None
        trim_rule = "Not enough valuation history to place a trim level"

    price_break = close * (1 - _LT_BREAK_DRAWDOWN)
    conditions = _thesis_conditions(metrics)

    return {
        "available": True,
        "accumulation_zone": Level("Accumulate at or below", _r(acc_high),
                                   acc_rule, _r(acc_low), _r(acc_high)).to_dict(),
        "valuation_range": {
            "own_history_low": _r(fair_low),
            "own_history_mid": _r(fair_mid),
            "own_history_high": _r(fair_high),
            "peg_fair_value": _r(peg.get("fair_value")),
            "street_low": _r(street.get("target_low")),
            "street_mean": _r(street.get("target_mean")),
            "street_high": _r(street.get("target_high")),
            "rule": ("Own-history multiple (median P/E over the quarters on file, times TTM "
                     "EPS), PEG at 1, and the Street's published targets. Three methods, "
                     "shown separately rather than averaged into one false number."),
        },
        "trim_zone": Level("Trim and re-evaluate", _r(trim), trim_rule).to_dict(),
        "thesis_invalidation": {
            "price_marker": _r(price_break),
            "price_rule": (f"{_LT_BREAK_DRAWDOWN:.0%} below today's close. Not a stop: a "
                           "marker that the market is pricing something this thesis does "
                           "not contain, and the business conditions below should be "
                           "checked rather than the position sold on the chart."),
            "conditions": conditions,
        },
        "next_review": _next_review(release, calendar),
        "note": ("A break of a moving average ends a swing trade and means nothing here. "
                 "What ends a long-term thesis is the business changing, which is why the "
                 "invalidation is written as conditions."),
    }


def _thesis_conditions(metrics: dict | None) -> list[dict]:
    """What would have to become true for the long-term case to be wrong.

    Anchored to this company's current numbers rather than to universal
    thresholds: a 6% operating margin is normal for a distributor and alarming
    for a software company, so the test is deterioration from where it is now.
    """
    if not metrics:
        return [{"metric": "fundamentals",
                 "condition": "No fundamentals were available to anchor thesis conditions."}]
    out: list[dict] = []
    om = metrics.get("operating_margin")
    if om is not None:
        floor = round(max(om - 0.04, om * 0.70), 4)
        out.append({"metric": "Operating margin", "now": round(om, 4), "breaks_below": floor,
                    "condition": f"Operating margin falls below {floor:.1%} for two "
                                 f"consecutive quarters (it is {om:.1%} now)."})
    rg = metrics.get("revenue_growth")
    if rg is not None:
        out.append({"metric": "Revenue growth", "now": round(rg, 4), "breaks_below": 0.0,
                    "condition": ("Year-on-year revenue growth turns negative for two "
                                  f"consecutive quarters (it is {rg:+.1%} now).")})
    de = metrics.get("debt_to_equity")
    if de is not None:
        ceiling = round(max(de * 1.6, de + 0.5), 2)
        out.append({"metric": "Debt to equity", "now": round(de, 2), "breaks_above": ceiling,
                    "condition": f"Debt to equity rises above {ceiling:.2f} without an "
                                 f"acquisition explaining it (it is {de:.2f} now)."})
    roe = metrics.get("return_on_equity")
    if roe is not None:
        floor = round(max(roe - 0.05, roe * 0.6), 4)
        out.append({"metric": "Return on equity", "now": round(roe, 4), "breaks_below": floor,
                    "condition": f"Return on equity falls below {floor:.1%} (it is "
                                 f"{roe:.1%} now)."})
    return out


def _next_review(release: dict | None, calendar: dict | None = None) -> dict:
    """When to look again. Earnings is the event that most often changes a thesis.

    An announced date is used when there is one, and the fallback estimate from
    the reporting rhythm is labelled as an estimate, so the card never shows a
    guess and a confirmed date in the same typeface.
    """
    if calendar and calendar.get("next_earnings"):
        return {"when": calendar["next_earnings"], "approximate": False,
                "eps_estimate": calendar.get("eps_estimate"),
                "rule": "The next earnings date as announced."}
    if not release or not release.get("date"):
        return {"when": None,
                "rule": "No recent earnings release was found, so the next date is unknown."}
    last = date.fromisoformat(release["date"])
    expected = last + timedelta(days=_QUARTER_DAYS)
    return {
        "when": expected.isoformat(),
        "approximate": True,
        "rule": (f"About one quarter after the last release on {release['date']}. "
                 "Estimated from the reporting rhythm, not from a confirmed calendar."),
    }


def zone_position(close: float | None, low: float | None,
                  high: float | None) -> float | None:
    """Where price sits inside a zone, 0 at the floor and 1 at the ceiling.

    "Inside the zone" covers both the best price in the range and the worst one,
    and a card that treats them the same is telling you to buy the top of the
    zone as cheerfully as the bottom.
    """
    if close is None or low is None or high is None or high <= low:
        return None
    return round(min(1.0, max(0.0, (close - low) / (high - low))), 4)


def compute(anchors: dict, panel: dict | None, metrics: dict | None,
            release: dict | None, rerated: bool = False,
            calendar: dict | None = None) -> dict:
    """Both horizons' levels from one set of primitives."""
    return {
        "swing": swing_levels(anchors or {}),
        "long_term": long_term_levels(anchors or {}, panel, metrics, release, rerated,
                                      calendar),
    }
