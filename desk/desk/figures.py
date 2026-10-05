"""Are the figures a debate turn quotes anywhere in the reports the managers share?

A manager can only weigh a figure it can check. In AKAM 2026-10-04 every figure that moved the
debate was in some team's report (Edge conceded to numbers TradingAgents had filed), but a figure
a manager computes or invents is invisible to the others. A turn's figures are now looked up in
the reports block. Writing varies ("$6.08B", "6,084,000,000", "10.5%", "0.105"), so values are
normalised and matched within the precision the figure was written to, or 1%. Measured on the AKAM
and COST debates: 7 of about 430 figure mentions unmatched, among them a derived trailing free cash flow.

Small counts, years and round numbers are not checked: "Round 2", "the 50-day", "3 to 6 months"
are not claims about the company.
"""

from __future__ import annotations

import re

_NUMBER = re.compile(r"""
    (?<![\w.])(?P<sign>-)?\$?
    (?P<value>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)
    (?:\s?(?P<unit>%|x\b|bn\b|billion\b|million\b|trillion\b|[BMTK]\b))?
""", re.X)
_SCALE = {"bn": 1e9, "billion": 1e9, "b": 1e9, "million": 1e6, "m": 1e6, "trillion": 1e12, "t": 1e12,
          "k": 1e3}
TOLERANCE = 0.01


def figures(text: str) -> list[tuple[str, float, float, str]]:
    """(as written, value, half of the last digit written in the value's units, unit) per figure."""
    out = []
    for m in _NUMBER.finditer(text or ""):
        digits = m.group("value").replace(",", "")
        try:
            value = float(digits)
        except ValueError:
            continue
        unit = (m.group("unit") or "").lower()
        scale = _SCALE.get(unit, 1.0)
        decimals = len(digits.split(".", 1)[1]) if "." in digits else 0
        half = 0.5 * 10 ** -decimals * scale
        value *= scale
        if m.group("sign"):
            value = -value
        out.append((m.group(0).strip(), value, half, unit))
    return out


def known(text: str) -> set[float]:
    """Every figure in the reports, with a percentage also filed as a fraction and back."""
    vals: set[float] = set()
    for _raw, value, _half, unit in figures(text):
        for v in (value, abs(value)):
            vals.add(v)
            if unit == "%":
                vals.add(v / 100)
            elif unit == "" and abs(v) < 1.5:
                vals.add(v * 100)           # a fraction printed raw, quoted as a percentage
    return vals


def _checked(raw: str, value: float, unit: str) -> bool:
    if unit:
        return True
    if float(value).is_integer() and (abs(value) < 25 or 1990 <= value <= 2100
                                      or (abs(value) < 10_000 and value % 10 == 0)):
        return False
    return True


def unverified(text: str, known_values: set[float]) -> list[str]:
    """The figures in *text* that appear in no report, as written."""
    bad = []
    for raw, value, half, unit in figures(text):
        if not _checked(raw, value, unit):
            continue
        target = value / 100 if unit == "%" else value
        ok = any(abs(k - v) <= max(h, TOLERANCE * max(abs(k), abs(v)))
                 for v, h in ((value, half), (abs(value), half), (target, half / 100 if unit == "%" else half))
                 for k in known_values)
        if not ok:
            bad.append(raw)
    return sorted(set(bad))
