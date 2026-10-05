"""Token and dollar accounting for every Anthropic call in a process.

Imported by the engine runners inside each engine's own virtualenv, so it
depends only on the standard library and the `anthropic` SDK. It patches the
SDK's Messages.create (stable and beta, plain and raw-response paths), which
works under LangChain/LangGraph without touching the engines' code.
"""

from __future__ import annotations

import threading
from collections import defaultdict

# $ per million tokens (input, output); thinking bills as output.
# Cache writes are 1.25x input (5-minute TTL), cache reads 0.1x input except where CACHE_READ says.
# Lookup takes the LONGEST matching prefix: "claude-opus-5" also prefixes "claude-opus-5-5".
PRICES = {
    "claude-fable-5": (10.00, 50.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

_lock = threading.Lock()
CALLS: list[dict] = []


CACHE_READ = {"claude-opus-5-5": 0.05}           # Opus 5.5 reads cache at $0.20, 0.05x input


def _prefix(model: str) -> str | None:
    return max((p for p in PRICES if model.startswith(p)), key=len, default=None)


def price_for(model: str) -> tuple[float, float] | None:
    prefix = _prefix(model)
    return PRICES[prefix] if prefix else None


def record(model: str, usage) -> None:
    if usage is None:
        return

    def get(key: str) -> int:
        value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
        return value or 0

    row = {"model": model, "input": get("input_tokens"),
           "cache_write": get("cache_creation_input_tokens"),
           "cache_read": get("cache_read_input_tokens"), "output": get("output_tokens")}
    with _lock:
        CALLS.append(row)


def cost(rows: list[dict]) -> float:
    total = 0.0
    for r in rows:
        prices = price_for(r["model"])
        if prices is None:
            continue
        pin, pout = prices
        total += (r["input"] * pin + r["cache_write"] * pin * 1.25
                  + r["cache_read"] * pin * CACHE_READ.get(_prefix(r["model"]), 0.10)
                  + r["output"] * pout) / 1e6
    return round(total, 4)


def summary() -> dict:
    by_model: dict[str, dict] = defaultdict(lambda: {"calls": 0, "input": 0, "cache_write": 0,
                                                     "cache_read": 0, "output": 0})
    with _lock:
        rows = list(CALLS)
    for r in rows:
        m = by_model[r["model"]]
        m["calls"] += 1
        for k in ("input", "cache_write", "cache_read", "output"):
            m[k] += r[k]
    return {"calls": len(rows), "usd": cost(rows), "by_model": dict(by_model)}


def install() -> None:
    import anthropic
    import anthropic.resources as stable
    import anthropic.resources.beta as beta

    class _StreamProxy:
        def __init__(self, stream, model):
            self._stream, self._model, self._usage = stream, model, {}

        def __getattr__(self, name):
            return getattr(self._stream, name)

        def __iter__(self):
            for event in self._stream:
                kind = getattr(event, "type", None)
                if kind == "message_start":
                    u = event.message.usage
                    self._usage.update({k: getattr(u, k, 0) or 0 for k in (
                        "input_tokens", "cache_creation_input_tokens",
                        "cache_read_input_tokens", "output_tokens")})
                elif kind == "message_delta" and getattr(event, "usage", None) is not None:
                    self._usage["output_tokens"] = getattr(event.usage, "output_tokens", 0) or 0
                elif kind == "message_stop":
                    record(self._model, self._usage)
                yield event

    def wrap(cls):
        original = cls.create

        def create(self, *args, **kwargs):
            result = original(self, *args, **kwargs)
            model = kwargs.get("model", "?")
            if isinstance(result, anthropic.Stream):
                return _StreamProxy(result, model)
            if hasattr(result, "usage"):
                record(getattr(result, "model", model), result.usage)
            elif hasattr(result, "parse") and not kwargs.get("stream"):
                parsed = result.parse()  # raw-response envelope; parse() is cached
                if hasattr(parsed, "usage"):
                    record(getattr(parsed, "model", model), parsed.usage)
            return result

        cls.create = create

    wrap(stable.Messages)
    wrap(beta.Messages)
