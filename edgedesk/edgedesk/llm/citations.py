"""The rule that keeps the narrative honest: every number must be cited.

The failure this prevents is specific and common. A model writing about a stock
will produce a sentence like "operating margins near 30% support the multiple"
when the filing says 24%, not because it is lying but because 30 is a plausible
number for that sentence. Prose is generative; that is what it is for.

So any figure appearing in generated text has to match a figure the run actually
holds, within a tolerance for rounding. A sentence that quotes something else is
rejected and the call is retried with the offending numbers named.

**Matching is by kind, not just by value.** A run holds hundreds of numbers, and
on a single number line almost any two-digit figure lands within a percent of
something. So a dollar figure may only match a dollar figure the run holds, a
percentage may only match a percentage, and a multiple may only match a
multiple. That is what turns the check from a formality into a filter: an
invented price of $412 no longer passes because some unrelated ratio happened to
be 4.12.

Known limits, so nobody mistakes this for more than it is. It cannot catch a
fabricated figure that lands within a percent of a real one of the same kind, it
does not check that a number was used to mean the right thing, and it ignores
bare small integers and years because demanding a citation for "three risks"
would make it noise. It is a floor, not a proof.
"""

from __future__ import annotations

from edgedesk.verdict.published import published_verdicts
import re

# Numbers as they appear in prose: $1,234.56, 24.3%, 1.7x, $1.72T, 12 analysts.
#
# Case matters in the suffix, and the whole pattern is therefore case-sensitive.
# "$1.72T" is a trillion, while "12m" is this engine's own label for a
# twelve-month window: reading that as twelve million made the citation check
# reject the vocabulary its own evidence brief is written in.
_NUMBER = re.compile(r"""
    (?<![\w.])
    (?P<sign>-)?
    \$?(?P<value>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)
    (?P<suffix>%|[xX]|[Bb][Nn]|[BMT](?![A-Za-z])|\s*(?:billion|million|trillion))?
    (?!\d)
""", re.X)
_DOLLAR = re.compile(r"^\$")

# A bare number carries no claim about its kind, so it is only treated as a
# financial figure when it looks like one. Counts and durations ("2 to 6 weeks",
# "12 quarters") and round rhetorical figures ("a $500 million business") are
# not what this check is for, and flagging them turns it into noise.
_IGNORE_BELOW = 25
_ROUND_LIMIT = 10_000
_YEARS = set(range(1990, 2101))
_TOLERANCE = 0.01

MONEY, PRICE, FRACTION, RATIO, COUNT, OTHER = (
    "money", "price", "fraction", "ratio", "count", "other")

# Key-name fragments that say what kind of number a field holds. Checked in
# order, so a narrow fragment can sit in front of a wider one.
_KINDS: tuple[tuple[str, str], ...] = (
    # Order matters, because a name can contain more than one of these. A
    # fraction-flavoured word wins over a price-flavoured one, which is what
    # puts "pct_from_52w_high" in fractions rather than in prices, and
    # "revenue_growth" in fractions rather than in money.
    ("pct", FRACTION), ("margin", FRACTION), ("growth", FRACTION),
    ("return", FRACTION), ("yield", FRACTION), ("surprise", FRACTION),
    ("drawdown", FRACTION), ("volatility", FRACTION), ("excess", FRACTION),
    ("payout", FRACTION), ("roe", FRACTION), ("roa", FRACTION),
    ("_vs_", FRACTION), ("upside", FRACTION), ("spread", FRACTION),
    ("cagr", FRACTION), ("ret_", FRACTION), ("position", FRACTION),
    # Multiples next, so "price_to_sales_ratio" is a multiple, not a price.
    ("ratio", RATIO), ("coverage", RATIO), ("turnover", RATIO),
    ("ebitda", RATIO), ("multiple", RATIO), ("reward_to_risk", RATIO),
    ("trend", RATIO), ("peg", RATIO),
    # Counts, before money, so "analyst_count" is not a dollar figure.
    ("volume", COUNT), ("analyst", COUNT), ("count", COUNT), ("periods", COUNT),
    ("bullish", COUNT), ("bearish", COUNT), ("neutral", COUNT), ("total", COUNT),
    ("strongbuy", COUNT), ("strongsell", COUNT), ("buy", COUNT), ("sell", COUNT),
    ("hold", COUNT), ("bars", COUNT),
    ("market_cap", MONEY), ("enterprise_value", MONEY), ("revenue", MONEY),
    ("per_share", PRICE), ("eps", PRICE), ("close", PRICE), ("sma_", PRICE),
    ("high", PRICE), ("low", PRICE), ("atr", PRICE), ("price", PRICE),
    ("target", PRICE), ("fair", PRICE), ("band_", PRICE), ("marker", PRICE),
    ("trim", PRICE), ("value", PRICE),
)


def kind_of(key: str) -> str:
    name = (key or "").lower()
    for fragment, kind in _KINDS:
        if fragment in name:
            return kind
    return OTHER


def _kind_of_written(raw: str, value: float) -> str:
    """What kind a figure in prose claims to be, from how it is written."""
    suffix = raw.strip().lower()
    if suffix.endswith("%"):
        return FRACTION
    if suffix.endswith("x"):
        return RATIO
    if (raw.strip().endswith(("B", "M", "T", "bn", "BN"))
            or any(suffix.endswith(s) for s in ("billion", "million", "trillion"))):
        return MONEY
    if _DOLLAR.match(raw.strip()):
        return MONEY if abs(value) >= 1e6 else PRICE
    return OTHER


# Words that say which way a figure moved (R2-03). "grew 24%" claims +24%, so it may not cite a
# stored -0.24; "fell 24%" claims -24%. Only percentages are held to this, where the sign so often
# lives in the words; without such a word the magnitude match below still applies.
_UP = re.compile(r"\b(?:gr[eo]w(?:s|n|ing)?|r[io]s(?:e|es|en|ing)|increas(?:e|ed|es|ing)|gain(?:s|ed)?"
                 r"|up|higher|expand(?:s|ed|ing)?|climb(?:s|ed)?|jump(?:s|ed)?|surg(?:e|ed|es))\b", re.I)
_DOWN = re.compile(r"\b(?:f[ae]ll(?:s|en|ing)?|declin(?:e|ed|es|ing)|drop(?:s|ped)?|down|lower|shr[ai]nk(?:s)?"
                   r"|shrunk|contract(?:s|ed|ing)?|lost|los(?:e|es|ing)|slid|slump(?:s|ed)?"
                   r"|decreas(?:e|ed|es|ing)|lag(?:s|ged)?)\b", re.I)


def _direction(text: str, start: int, end: int) -> str | None:
    """'up', 'down' or None: the nearest direction word just before the figure ("revenue grew
    24%"), else just after it ("a 24% decline")."""
    before = text[max(0, start - 40):start]
    hits = [(m.end(), "up") for m in _UP.finditer(before)] + [(m.end(), "down") for m in _DOWN.finditer(before)]
    if hits:
        return max(hits)[1]
    after = text[end:end + 25]
    hits = [(m.start(), "up") for m in _UP.finditer(after)] + [(m.start(), "down") for m in _DOWN.finditer(after)]
    return min(hits)[1] if hits else None


def _numbers_in(text: str) -> list[tuple[str, float, str]]:
    return [(raw, value, kind) for raw, value, kind, _d in _figures(text)]


def _figures(text: str) -> list[tuple[str, float, str, str | None]]:
    """(raw, value, kind, direction) for every figure in *text*."""
    found: list[tuple[str, float, str, str | None]] = []
    for match in _NUMBER.finditer(text or ""):
        raw = match.group(0)
        try:
            value = float(match.group("value").replace(",", ""))
        except ValueError:
            continue
        if match.group("sign"):
            value = -value
        suffix = (match.group("suffix") or "").strip()
        upper = suffix.upper()
        suffix = suffix.lower()
        if upper in ("BN", "B") or suffix == "billion":
            value *= 1e9
        elif upper == "M" or suffix == "million":
            value *= 1e6
        elif upper == "T" or suffix == "trillion":
            value *= 1e12
        elif suffix == "%":
            value /= 100.0
        direction = None if match.group("sign") else _direction(text, match.start(), match.end())
        found.append((raw, value, _kind_of_written(raw, value), direction))
    return found


def known_values(run: dict) -> dict[str, set[float]]:
    """Every number the run holds, filed by what kind of number it is.

    The 0-100 factor and verdict scores are deliberately left out. They are
    dense integers across the whole two-digit range, so admitting them would let
    almost any bare two-digit figure match something. The prompts do not ask for
    scores to be quoted, and the run's own reports print them.
    """
    buckets: dict[str, set[float]] = {k: set() for k in
                                      (MONEY, PRICE, FRACTION, RATIO, COUNT, OTHER)}

    def add(key: str, value) -> None:
        if value is None or isinstance(value, bool):
            return
        if not isinstance(value, (int, float)):
            return
        kind = kind_of(key)
        if kind is OTHER:
            # Place it by shape when the name says nothing: a fraction looks
            # like a fraction whatever it is called.
            kind = FRACTION if abs(value) < 5 else PRICE if abs(value) < 1e6 else MONEY
        buckets[kind].add(float(value))
        buckets[OTHER].add(float(value))

    def walk(node, key: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        else:
            add(key, node)

    ev = run.get("evidence") or {}
    for fact in (ev.get("facts") or {}).values():
        add(fact.get("id") or fact.get("label") or "", fact.get("value"))
    walk(ev.get("metrics") or [])
    for block in ("anchors", "consensus", "breakdown", "release", "dividend", "calendar",
                  "relative"):
        walk(ev.get(block) or {})
    for family in (run.get("factors") or {}).values():
        for signal in family.get("signals") or []:
            add(signal.get("key", ""), signal.get("raw"))
    # The 0-100 scores are admitted to the bare bucket only. They are dense
    # integers, so letting them into the typed buckets would make any
    # percentage or price match something; but the brief shows them to the
    # model, so quoting one plainly has to be allowed.
    for family in (run.get("factors") or {}).values():
        _bare(buckets, family.get("score"))
    for verdict in published_verdicts(run).values():
        _bare(buckets, verdict.get("score"))
        _bare(buckets, verdict.get("risk_score"))
    walk(run.get("levels") or {})
    walk(run.get("valuation") or {})
    # The business case and the swing call are shown to the model, so their
    # figures are quotable.
    walk(run.get("long_term") or {})
    walk(run.get("swing") or {})
    return buckets


def _bare(buckets: dict[str, set[float]], value) -> None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        buckets[OTHER].add(float(value))


def _half_step(raw: str, value: float) -> float:
    """Half of the last digit written, in the figure's own units.

    A figure is a rounding of the run's value, and how far it may sit from that value depends
    on how it was written, not on its size: "-3.6%" stands for anything from -3.65% to -3.55%.
    A flat relative tolerance got this wrong for small figures (it rejected -3.6% for a stored
    -3.639%, a 1.07% relative gap), so the written precision is allowed as well."""
    match = _NUMBER.search(raw)
    if not match:
        return 0.0
    digits = match.group("value").replace(",", "")
    written = float(digits)
    decimals = len(digits.split(".", 1)[1]) if "." in digits else 0
    factor = abs(value) / written if written else 1.0
    return 0.5 * 10 ** -decimals * factor


def _matches(value: float, kind: str, known: dict[str, set[float]], half_step: float = 0.0,
             direction: str | None = None) -> bool:
    if float(value).is_integer() and int(value) in _YEARS and kind is OTHER:
        return True
    if kind is OTHER and float(value).is_integer():
        if abs(value) < _IGNORE_BELOW:
            return True
        if abs(value) < _ROUND_LIMIT and value % 10 == 0:
            return True
    candidates = known.get(kind) or set()
    if kind is MONEY:
        candidates = candidates | (known.get(PRICE) or set())
    # A bare number says nothing about what it is, so it is checked against
    # everything. Strictness belongs where the writing makes a claim: a figure
    # written with a dollar sign, a percent or an x is claiming a kind, and is
    # held to it.
    scaled = kind in (FRACTION, OTHER)
    if kind is OTHER:
        candidates = known.get(OTHER) or set()
    for candidate in candidates:
        if kind is FRACTION and direction and candidate and (candidate < 0) != (direction == "down"):
            continue                              # "grew 24%" cannot cite a stored -0.24
        if candidate == value:
            return True
        if half_step:
            slack = half_step * 1.0001
            if abs(candidate - value) <= slack:
                return True
            if kind is FRACTION and abs(abs(candidate) - abs(value)) <= slack:
                return True
        scale = max(abs(candidate), abs(value), 1e-9)
        if abs(candidate - value) / scale <= _TOLERANCE:
            return True
        # Sign is carried by the sentence as often as by the number: "a 29.4%
        # drawdown" and "-29.4%" are the same fact, and so are "lagged by 11.2%"
        # and "-11.2% versus SPY". Magnitudes are compared for fractions only,
        # where the direction is nearly always in the words around it.
        if kind is FRACTION and abs(abs(candidate) - abs(value)) / scale <= _TOLERANCE:
            return True
        # A percentage stored as 0.24 and written as a bare 24, or a figure stored in percentage
        # points (24.0) and written as 24%. A figure written with % is already divided by 100,
        # so it may not be scaled again: "2400%" is 24.0 and must not cite a stored 0.24 (R2-03).
        if scaled and candidate:
            pairs = ([(abs(candidate), abs(value) * 100)] if kind is FRACTION else
                     [(abs(candidate) * 100, abs(value)), (abs(candidate), abs(value) * 100)])
            for a, b in pairs:
                if abs(a - b) / max(b, 1e-9) <= _TOLERANCE:
                    return True
    return False


def add_text(known: dict[str, set[float]], text: str) -> dict[str, set[float]]:
    """File every figure written in *text* (a headline, a verified filing quote) as citable."""
    for _raw, value, kind in _numbers_in(text):
        known.setdefault(kind, set()).add(value)
    return known


def uncited(text: str, run: dict, known: dict[str, set[float]] | None = None) -> list[str]:
    """The figures in *text* that the run does not contain."""
    known = known if known is not None else known_values(run)
    bad = [raw for raw, value, kind, direction in _figures(text)
           if not _matches(value, kind, known, _half_step(raw, value), direction)]
    return sorted(set(bad))


def audit(payload, run: dict, fields: list[str] | None = None) -> list[str]:
    """Every uncited figure across a reply's text fields."""
    known = known_values(run)
    found: list[str] = []

    def visit(node, key: str | None = None) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                visit(v, k)
        elif isinstance(node, list):
            for v in node:
                visit(v, key)
        elif isinstance(node, str):
            if fields and key not in fields:
                return
            found.extend(uncited(node, run, known))

    visit(payload)
    return sorted(set(found))
