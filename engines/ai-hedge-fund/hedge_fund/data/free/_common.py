"""Shared plumbing for the free data providers."""

from __future__ import annotations

import threading
import time


class FreeDataError(Exception):
    """A free provider failed for infrastructure reasons (auth, rate limit,
    network, server error). Same contract as FDClientError: "no data exists"
    returns empty; a failure raises, because a silent empty poisons a backtest.
    """


def alpaca_symbol(ticker: str) -> str:
    """Alpaca spells share classes with a dot: BRK.B."""
    return ticker.upper().replace("-", ".")


def dash_symbol(ticker: str) -> str:
    """Yahoo and SEC spell share classes with a dash: BRK-B."""
    return ticker.upper().replace(".", "-")


class Throttle:
    """Minimum gap between requests, shared across threads (the TUI prefetches
    with a thread pool, and SEC's fair-access limit is per client, not per
    thread)."""

    def __init__(self, min_gap_seconds: float) -> None:
        self._gap = min_gap_seconds
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            delay = self._last + self._gap - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


MISS = object()


class TTLMemo:
    """Thread-safe in-process memo with expiry, for responses that are fetched
    once per ticker and reused across many dates (companyfacts, Yahoo info).
    `get` returns MISS (not None) on a miss, so a remembered None still hits."""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._data: dict = {}

    def get(self, key):
        with self._lock:
            hit = self._data.get(key)
        if hit is None or time.monotonic() - hit[0] > self._ttl:
            return MISS
        return hit[1]

    def put(self, key, value) -> None:
        with self._lock:
            self._data[key] = (time.monotonic(), value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
