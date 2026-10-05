"""What a run is allowed to publish, by horizon.

The swing score is still computed and stored, because the calibration measures
it. It is a research object, not a rating: nothing that a reader, a summary, a
scan, a change tracker or a language model sees may carry it. Every consumer
goes through `published`, so there is one place that decides, and old run files
that still hold a swing rating are covered too.
"""

from __future__ import annotations

# Horizons whose formula score is an accepted, published rating.
RATED_HORIZONS = ("long_term",)

_KEPT = ("label", "question", "quality_state", "reasons", "data_quality")


def published(run: dict, horizon: str) -> dict:
    """The verdict for *horizon* as it may be shown. Swing loses its score."""
    verdict = (run.get("verdicts") or {}).get(horizon) or {}
    if horizon in RATED_HORIZONS:
        return verdict
    return {k: verdict[k] for k in _KEPT if k in verdict}


def published_verdicts(run: dict) -> dict:
    return {h: published(run, h) for h in (run.get("verdicts") or {})}
