"""Turning a raw figure into a 0-100 signal, explicitly.

Every score in this engine comes from a stated curve, not from a model's sense
of what looks good. A curve is a list of `(raw value, score)` breakpoints in
ascending order, interpolated linearly between them and clamped outside them:

    band(0.23, [(0.0, 0), (0.10, 40), (0.25, 70), (0.50, 100)])  ->  67.1

That makes three things true that matter more than sophistication. The curve is
readable, so a disagreement is about where the breakpoints sit rather than about
what the engine did. It is versioned, so a change to a breakpoint is a change
anyone can see in a diff. And `None` in means `None` out, all the way through:
a missing input never becomes a zero, and a zero is a real, bad value.
"""

from __future__ import annotations

Curve = list[tuple[float, float]]


def band(value: float | None, curve: Curve) -> float | None:
    """Score *value* against *curve*, or None when there is nothing to score."""
    if value is None:
        return None
    lo_x, lo_y = curve[0]
    if value <= lo_x:
        return float(lo_y)
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        if value <= x1:
            if x1 == x0:
                return float(y1)
            return round(y0 + (y1 - y0) * (value - x0) / (x1 - x0), 2)
    return float(curve[-1][1])


def inverse(value: float | None, curve: Curve) -> float | None:
    """For measures where lower is better (leverage, valuation multiples).

    The curve is still written lowest-raw-value first; this flips the result so
    a small multiple scores high.
    """
    scored = band(value, curve)
    return None if scored is None else round(100.0 - scored, 2)


def percentile(value: float | None, history: list[float]) -> float | None:
    """Where *value* sits inside its own history, 0-100.

    Used for valuation against the stock's own multiple range, which is the only
    honest comparison available before peer data exists: a 40x software company
    is not expensive because 40 is a big number, it is expensive relative to the
    35x it has usually traded at.
    """
    if value is None:
        return None
    clean = sorted(v for v in history if v is not None)
    if len(clean) < 4:
        return None
    below = sum(1 for v in clean if v < value)
    equal = sum(1 for v in clean if v == value)
    return round(100.0 * (below + 0.5 * equal) / len(clean), 2)


def blend(parts: list[tuple[float | None, float]]) -> tuple[float | None, float]:
    """Weighted mean of the signals that exist, plus the coverage that produced it.

    Weights are renormalized over the available signals, so a family with one
    missing input is still scored on the rest rather than being dragged toward
    zero. Coverage is returned alongside so the caller can decide whether what
    remained was enough: a "quality" score built from one of seven inputs is a
    number, but it is not a judgement.
    """
    live = [(s, w) for s, w in parts if s is not None and w > 0]
    total_weight = sum(w for _, w in parts if w > 0)
    if not live or total_weight <= 0:
        return None, 0.0
    weight = sum(w for _, w in live)
    score = sum(s * w for s, w in live) / weight
    return round(score, 2), round(weight / total_weight, 4)
