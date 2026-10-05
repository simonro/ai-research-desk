"""The position-aware action vocabulary: what a rating means for someone who owns the stock, and
for someone who does not. The horizons themselves live in desk/views.py.
"""

from __future__ import annotations

_ACTIONS = {
    True: {"Buy": "Add to the position", "Overweight": "Hold above normal size; add on weakness",
           "Hold": "Hold; no new money", "Underweight": "Trim the position", "Sell": "Exit the position"},
    False: {"Buy": "Open a position", "Overweight": "Start a starter position",
            "Hold": "Stay out; keep on the watchlist with an entry level", "Underweight": "Avoid",
            "Sell": "Avoid"},
}


def action_for(rating: str | None, owns: bool) -> str:
    """The action for the investor's position. Set in code, so it always matches the rating."""
    if rating is None:
        return ("No consensus: keep current exposure unchanged until the crux resolves" if owns
                else "No consensus: stay out until the crux resolves")
    return _ACTIONS[owns][rating]
