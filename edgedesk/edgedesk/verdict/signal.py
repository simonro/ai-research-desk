"""The swing signal: green or red, and nothing dressed up as a forecast.

The swing score was measured and it does not work. Across about 3,000 swing observations the
families carrying two thirds of its weight (relative strength, momentum,
technical) produced no separation at 10 and 20 days and negative separation at
30, which is short-term reversal behaving exactly as the literature says it
does. Publishing a Buy / Overweight / Hold ladder on top of that would be
dressing up noise in the vocabulary of conviction.

So the swing horizon stops predicting and starts describing. Green means the
setup is intact right now; red means it is broken right now. Both are statements
about today's chart that anyone can verify by looking at it, not claims about
what happens next. The levels underneath carry the actual usefulness: where the
setup would be entered, where it stops being true, and what is reachable if it
works.

The score is still computed and still stored in the run file, because the
calibration needs it to measure whether a future version separates. It is simply
not published, and a number nobody acts on is not a rating.
"""

from __future__ import annotations

SIGNAL_VERSION = "1.0.0"

GREEN, RED = "GREEN", "RED"


def swing_signal(anchors: dict, levels: dict) -> dict:
    """Is the swing setup intact or broken, and why, in one sentence.

    Two rules, both descriptive:

    * price below the structural invalidation means the setup has already failed
    * price below the 50-day average means the intermediate trend is against it

    Either one is red. The 50-day is the line that separates "pulling back
    inside an uptrend" from "in a downtrend", which is the distinction a two to
    six week holder actually needs.
    """
    close = (anchors or {}).get("last_close")
    sma_20, sma_50 = (anchors or {}).get("sma_20"), (anchors or {}).get("sma_50")
    invalidation = ((levels or {}).get("invalidation") or {}).get("price")

    if close is None or sma_50 is None:
        return {"signal": None, "version": SIGNAL_VERSION,
                "why": "Not enough price history to describe the setup.",
                "checks": []}

    checks = [
        {"name": "Above the structural invalidation",
         "pass": invalidation is None or close > invalidation,
         "detail": (f"{close:,.2f} against {invalidation:,.2f}"
                    if invalidation is not None else "no invalidation level")},
        {"name": "Above the 50-day average", "pass": close > sma_50,
         "detail": f"{close:,.2f} against {sma_50:,.2f}"},
        {"name": "Above the 20-day average",
         "pass": sma_20 is not None and close > sma_20,
         "detail": (f"{close:,.2f} against {sma_20:,.2f}"
                    if sma_20 is not None else "no 20-day average")},
    ]
    broken = [c for c in checks[:2] if not c["pass"]]
    signal = RED if broken else GREEN

    if signal == RED:
        why = "Setup broken: " + "; ".join(
            f"{c['name'].lower().replace('above the', 'price is below the')} "
            f"({c['detail']})" for c in broken) + "."
    elif checks[2]["pass"]:
        why = (f"Setup intact: price is above the 20-day, the 50-day and the "
               f"invalidation at {invalidation:,.2f}."
               if invalidation is not None else
               "Setup intact: price is above the 20-day and the 50-day.")
    else:
        why = (f"Setup intact but pulling back: price is under the 20-day "
               f"({checks[2]['detail']}) while holding the 50-day and the invalidation.")

    return {"signal": signal, "version": SIGNAL_VERSION, "why": why, "checks": checks,
            "note": ("This describes the setup as it stands today. It is not a forecast: "
                     "the swing score was measured against forward returns and did not "
                     "separate them, so no rating is published for this horizon.")}


def action_for(signal: str | None, owns: bool) -> str:
    """What to do about a setup, given whether it is already held.

    Deliberately modest. A green setup is a candidate for the entry zone, not an
    instruction, and a red one is a reason to stand aside rather than a short.
    """
    if signal is None:
        return "No action: the setup could not be described"
    if signal == GREEN:
        return ("Hold; add only in the entry zone" if owns
                else "Candidate: wait for the entry zone")
    return ("Setup broken: manage the position against the invalidation" if owns
            else "Stand aside until the setup repairs")
