"""Price anchors computed from daily bars, so no memo level is an invented number.

Pure standard library on purpose: the ai-hedge-fund runner imports this file
from inside that engine's own virtualenv.
"""

from __future__ import annotations


def compute_anchors(bars: list[dict]) -> dict:
    """bars: [{"date", "high", "low", "close"}], oldest first. Returns rounded anchors."""
    if not bars:
        return {}
    closes = [b["close"] for b in bars]
    last = bars[-1]

    def sma(n: int) -> float | None:
        return round(sum(closes[-n:]) / n, 2) if len(closes) >= n else None

    true_ranges = []
    for prev, cur in zip(bars, bars[1:]):
        true_ranges.append(max(cur["high"] - cur["low"], abs(cur["high"] - prev["close"]),
                               abs(cur["low"] - prev["close"])))
    atr = round(sum(true_ranges[-14:]) / min(14, len(true_ranges)), 2) if true_ranges else None
    year = bars[-252:]
    month = bars[-20:]
    high_52w = max(b["high"] for b in year)
    low_52w = min(b["low"] for b in year)
    return {
        "as_of": last["date"],
        "last_close": round(last["close"], 2),
        "sma_20": sma(20),
        "sma_50": sma(50),
        "sma_200": sma(200),
        "high_20d": round(max(b["high"] for b in month), 2),
        "low_20d": round(min(b["low"] for b in month), 2),
        "high_52w": round(high_52w, 2),
        "low_52w": round(low_52w, 2),
        "atr_14": atr,
        "pct_from_52w_high": round(last["close"] / high_52w - 1, 4) if high_52w else None,
    }


# ---------------------------------------------------------------- memo levels (audit 3, R3)
# The memo writer never types a price. For each level it names a basis below (and optionally an
# ATR offset and a second basis for a range); code computes the number from this run's shared
# data. A basis the run lacks, or "none", withholds that level with the reason shown.
LEVEL_KEYS = ("entry_zone", "stop", "first_target", "trim")
BASES = {
    "last_close": "last settled close", "sma_20": "20-day average", "sma_50": "50-day average",
    "sma_200": "200-day average", "high_20d": "20-day high", "low_20d": "20-day low",
    "high_52w": "52-week high", "low_52w": "52-week low",
    "fair_low": "own-history fair value (low)", "fair_mid": "own-history fair value (mid)",
    "fair_high": "own-history fair value (high)", "peg_fair": "PEG = 1 fair value",
    "street_low": "Street low target", "street_mean": "Street mean target", "street_high": "Street high target",
}
MAX_ATR_OFFSET = 3.0


def basis_values(shared: dict) -> dict[str, float]:
    """The bases this run actually has, from the shared computed data (anchors, valuation panel)."""
    a, v = shared.get("anchors") or {}, shared.get("valuation") or {}
    h, p, s = v.get("own_history") or {}, v.get("peg") or {}, v.get("street") or {}
    raw = {**{k: a.get(k) for k in ("last_close", "sma_20", "sma_50", "sma_200", "high_20d", "low_20d",
                                    "high_52w", "low_52w")},
           "fair_low": h.get("fair_low"), "fair_mid": h.get("fair_mid"), "fair_high": h.get("fair_high"),
           "peg_fair": p.get("fair_value"), "street_low": s.get("target_low"),
           "street_mean": s.get("target_mean"), "street_high": s.get("target_high")}
    return {k: float(x) for k, x in raw.items() if isinstance(x, (int, float)) and x > 0}


def bases_text(shared: dict) -> str:
    """The list the memo prompt shows: id, what it is, and this run's value."""
    have, atr = basis_values(shared), (shared.get("anchors") or {}).get("atr_14")
    rows = [f"- {k}: {BASES[k]} = {have[k]:.2f}" for k in BASES if k in have]
    rows.append(f"- ATR (14 days) = {atr:.2f}" if isinstance(atr, (int, float)) and atr > 0
                else "- ATR (14 days): not available, so atr_offset must be 0")
    return "\n".join(rows) if have else "(no computed bases in this run: set every level to none)"


def level_schema() -> dict:
    ids = [*BASES, "none"]
    return {"type": "object",
            "properties": {"basis": {"type": "string", "enum": ids},
                           "atr_offset": {"type": "number"},
                           "range_to": {"type": "string", "enum": ids},
                           "reason": {"type": "string"}},
            "required": ["basis", "atr_offset", "range_to", "reason"], "additionalProperties": False}


def resolve_level(raw: dict, shared: dict) -> dict:
    """One model-chosen level -> a price computed here, or a withheld level saying why.

    Shape every renderer reads: price (text), reason, plus status "checked" or "withheld",
    low/high as numbers when checked, how (the computation) and why (when withheld)."""
    have, atr = basis_values(shared), (shared.get("anchors") or {}).get("atr_14")
    reason = str(raw.get("reason") or "").strip()
    basis, to = raw.get("basis") or "none", raw.get("range_to") or "none"
    offset = raw.get("atr_offset") or 0

    def withheld(why: str) -> dict:
        return {"price": "Withheld", "reason": reason, "status": "withheld", "why": why}

    if basis == "none":
        return withheld("No computed level supports it")
    for b in (basis, to):
        if b != "none" and b not in BASES:
            return withheld(f"Unknown basis {b!r}")
        if b != "none" and b not in have:
            return withheld(f"This run has no {BASES[b]}")
    if not isinstance(offset, (int, float)) or abs(offset) > MAX_ATR_OFFSET:
        return withheld(f"ATR offset {offset!r} is outside +/-{MAX_ATR_OFFSET:g}")
    if offset and not (isinstance(atr, (int, float)) and atr > 0):
        return withheld("An ATR offset was asked for but this run has no ATR")
    value = have[basis] + (offset * atr if offset else 0)
    how = BASES[basis] + (f" {'plus' if offset > 0 else 'minus'} {abs(offset):g} x ATR(14)" if offset else "")
    low = high = round(value, 2)
    if to != "none" and to != basis:
        low, high = sorted((round(value, 2), round(have[to], 2)))
        how += f", to the {BASES[to]}"
    if low <= 0:
        return withheld("The computed price is not positive")
    price = f"{low:.2f}" if low == high else f"{low:.2f} to {high:.2f}"
    return {"price": price, "reason": reason, "status": "checked", "low": low, "high": high, "how": how}


def resolve_levels(raw_levels: dict, shared: dict) -> dict:
    return {k: resolve_level((raw_levels or {}).get(k) or {"basis": "none"}, shared) for k in LEVEL_KEYS}
