"""The shared rating scale and the agreement rule.

Both engines speak the same five tiers. Agreement is "same side": what matters
to the investor is add vs don't add vs reduce, so Buy and Overweight agree,
while Overweight and Hold do not.
"""

from __future__ import annotations

RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")
_SIDE = {"Buy": "bullish", "Overweight": "bullish", "Hold": "neutral",
         "Underweight": "bearish", "Sell": "bearish"}


def normalize(rating: str | None) -> str | None:
    if not rating:
        return None
    value = str(rating).strip().strip("*").strip().title()
    return value if value in RATINGS else None


def side(rating: str) -> str:
    return _SIDE[rating]


def agree(a: str, b: str) -> bool:
    return side(a) == side(b)


def more_conservative(a: str, b: str) -> str:
    """Of two same-side ratings, the one closer to Hold (the smaller bet)."""
    hold = RATINGS.index("Hold")
    return a if abs(RATINGS.index(a) - hold) <= abs(RATINGS.index(b) - hold) else b


def most_conservative(ratings: list[str]) -> str:
    """Of several same-side ratings, the one closest to Hold."""
    out = ratings[0]
    for r in ratings[1:]:
        out = more_conservative(out, r)
    return out
