"""Parsing and checking what came back.

Claude Code headless has no server-side schema enforcement, so every reply is
treated as untrusted text: found, parsed, checked against a small local schema,
and retried once with the specific complaint if it fails. A reply that fails
twice is dropped, and the run continues without that section rather than
printing something unvalidated.

The schema language is deliberately tiny, covering only what these prompts ask
for. A full JSON Schema implementation would be a dependency and a second thing
to be wrong.
"""

from __future__ import annotations

import json
import re

_FENCE = re.compile(r"```(?:json)?\s*(.+?)```", re.S)


class SchemaError(ValueError):
    """The reply parsed but did not match what was asked for."""


def extract(text: str) -> dict | list:
    """Find the JSON in a reply that may be wrapped in prose or a code fence."""
    if not text or not text.strip():
        raise SchemaError("the reply was empty")
    candidates: list[str] = []
    for match in _FENCE.finditer(text):
        candidates.append(match.group(1).strip())
    stripped = text.strip()
    candidates.append(stripped)
    # Last resort: the outermost braces or brackets in the whole reply.
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = stripped.find(opener), stripped.rfind(closer)
        if 0 <= start < end:
            candidates.append(stripped[start:end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
    raise SchemaError("no JSON object could be parsed from the reply")


_TYPES = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def check(data, spec: dict, path: str = "") -> None:
    """Validate *data* against a small spec.

    Supported: `type`, `required`, `properties`, `items`, `enum`, `max_items`,
    `min_items`, `max_length`. Anything else in a spec is ignored rather than
    silently treated as a constraint that passed.
    """
    kind = spec.get("type")
    if kind and not isinstance(data, _TYPES[kind]):
        # bool is an int in Python, which would let True pass as a number.
        raise SchemaError(f"{path or 'value'} should be {kind}, got "
                          f"{type(data).__name__}")
    if kind in ("number", "integer") and isinstance(data, bool):
        raise SchemaError(f"{path or 'value'} should be {kind}, got boolean")

    if spec.get("enum") is not None and data not in spec["enum"]:
        raise SchemaError(f"{path or 'value'} should be one of {spec['enum']}, "
                          f"got {data!r}")
    if isinstance(data, str) and spec.get("max_length") and len(data) > spec["max_length"]:
        raise SchemaError(f"{path or 'value'} is longer than {spec['max_length']} characters")

    if isinstance(data, list):
        if spec.get("min_items") and len(data) < spec["min_items"]:
            raise SchemaError(f"{path or 'list'} needs at least {spec['min_items']} items")
        if spec.get("max_items") and len(data) > spec["max_items"]:
            raise SchemaError(f"{path or 'list'} has more than {spec['max_items']} items")
        if spec.get("items"):
            for i, item in enumerate(data):
                check(item, spec["items"], f"{path}[{i}]")

    if isinstance(data, dict):
        for key in spec.get("required", []):
            if key not in data:
                raise SchemaError(f"{path or 'object'} is missing {key!r}")
        for key, sub in (spec.get("properties") or {}).items():
            if key in data and data[key] is not None:
                check(data[key], sub, f"{path}.{key}" if path else key)


def fit_lengths(data, spec: dict):
    """Trim over-long prose to the last whole sentence that fits, instead of failing it.

    Models are poor at counting characters: on TSLA the bear case and one lens both ran past
    their caps twice and were thrown away whole, which lost a section the run had paid for. A
    string is trimmed only when a whole sentence ending leaves at least 60% of the allowance;
    otherwise it is left long and the check below rejects it as before. Trimming removes
    text, never adds any, so the citation check still sees only what the model wrote."""
    if isinstance(data, str):
        limit = spec.get("max_length")
        if limit and len(data) > limit:
            cut = max(data.rfind(p, 0, limit) for p in (". ", "! ", "? ", ".\n"))
            if cut >= 0.6 * limit:
                return data[:cut + 1].rstrip()
        return data
    if isinstance(data, list) and spec.get("items"):
        return [fit_lengths(x, spec["items"]) for x in data]
    if isinstance(data, dict):
        props = spec.get("properties") or {}
        return {k: fit_lengths(v, props[k]) if k in props else v for k, v in data.items()}
    return data


def parse(text: str, spec: dict):
    """Extract, trim over-long prose at a sentence, and validate, raising SchemaError."""
    data = fit_lengths(extract(text), spec)
    check(data, spec)
    return data


def describe(spec: dict) -> str:
    """The schema as an instruction, so the prompt and the check cannot drift."""
    return json.dumps(_shape(spec), indent=2)


def _shape(spec: dict):
    kind = spec.get("type")
    if kind == "object":
        out = {}
        for key, sub in (spec.get("properties") or {}).items():
            marker = _shape(sub)
            if key in spec.get("required", []):
                out[key] = marker
            else:
                out[f"{key} (optional)"] = marker
        return out
    if kind == "array":
        limit = spec.get("max_items")
        inner = _shape(spec.get("items") or {"type": "string"})
        return [inner, f"... up to {limit}" if limit else "..."]
    if spec.get("enum"):
        return " | ".join(str(v) for v in spec["enum"])
    if spec.get("max_length"):
        return f"<{kind}, at most {spec['max_length']} characters>"
    return f"<{kind or 'value'}>"
