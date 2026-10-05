"""SIC code to sector and sector ETF.

The free stack's only sector signal is the SEC's SIC code, which is an
industrial classification from the 1980s, not GICS. Mapping it onto the eleven
SPDR sector ETFs is an approximation, and it is an approximation the engine
makes explicitly: the ETF picked here is what relative strength is measured
against, so every run records which one it used and a wrong-looking sector is
visible in the report instead of buried in a score.

Ranges are checked in order, so a narrow rule (3674 semiconductors) can sit in
front of the wide one it lives inside (3600-3699 electronics).
"""

from __future__ import annotations

SPY = "SPY"

# (low, high, sector label, ETF). Inclusive bounds, first match wins.
_RANGES: tuple[tuple[int, int, str, str], ...] = (
    # Narrow rules first.
    (2830, 2836, "Health Care", "XLV"),          # pharma and biotech
    (2840, 2844, "Consumer Staples", "XLP"),     # soap, cosmetics
    (3570, 3579, "Technology", "XLK"),           # computer hardware
    (3661, 3669, "Technology", "XLK"),           # communications equipment
    (3670, 3679, "Technology", "XLK"),           # semiconductors and components
    (3711, 3716, "Consumer Discretionary", "XLY"),   # motor vehicles
    (3841, 3851, "Health Care", "XLV"),          # medical devices
    (5400, 5412, "Consumer Staples", "XLP"),     # food stores
    (5912, 5912, "Consumer Staples", "XLP"),     # drug stores
    (6798, 6798, "Real Estate", "XLRE"),         # REITs
    (7310, 7319, "Communication Services", "XLC"),   # advertising
    (7370, 7379, "Technology", "XLK"),           # software and computer services
    # Wide rules.
    (100, 999, "Consumer Staples", "XLP"),
    (1000, 1099, "Materials", "XLB"),
    (1200, 1299, "Energy", "XLE"),
    (1300, 1399, "Energy", "XLE"),
    (1400, 1499, "Materials", "XLB"),
    (1520, 1599, "Consumer Discretionary", "XLY"),   # homebuilders
    (1600, 1799, "Industrials", "XLI"),
    (2000, 2199, "Consumer Staples", "XLP"),
    (2200, 2399, "Consumer Discretionary", "XLY"),
    (2400, 2599, "Industrials", "XLI"),
    (2600, 2699, "Materials", "XLB"),
    (2700, 2799, "Communication Services", "XLC"),
    (2800, 2899, "Materials", "XLB"),
    (2900, 2999, "Energy", "XLE"),
    (3000, 3099, "Materials", "XLB"),
    (3100, 3199, "Consumer Discretionary", "XLY"),
    (3200, 3399, "Materials", "XLB"),
    (3400, 3499, "Industrials", "XLI"),
    (3500, 3569, "Industrials", "XLI"),
    (3580, 3599, "Industrials", "XLI"),
    (3600, 3660, "Technology", "XLK"),
    (3680, 3699, "Technology", "XLK"),
    (3700, 3710, "Industrials", "XLI"),
    (3717, 3799, "Industrials", "XLI"),
    (3800, 3840, "Technology", "XLK"),
    (3852, 3899, "Technology", "XLK"),
    (3900, 3999, "Consumer Discretionary", "XLY"),
    (4000, 4799, "Industrials", "XLI"),
    (4800, 4899, "Communication Services", "XLC"),
    (4900, 4999, "Utilities", "XLU"),
    (5000, 5099, "Industrials", "XLI"),
    (5100, 5199, "Consumer Staples", "XLP"),
    (5200, 5999, "Consumer Discretionary", "XLY"),
    (6000, 6499, "Financials", "XLF"),
    (6500, 6599, "Real Estate", "XLRE"),
    (6600, 6799, "Financials", "XLF"),
    (7000, 7099, "Consumer Discretionary", "XLY"),
    (7100, 7299, "Consumer Discretionary", "XLY"),
    (7320, 7369, "Industrials", "XLI"),
    (7380, 7399, "Industrials", "XLI"),
    (7400, 7799, "Industrials", "XLI"),
    (7800, 7999, "Communication Services", "XLC"),
    (8000, 8099, "Health Care", "XLV"),
    (8100, 8199, "Financials", "XLF"),
    (8200, 8399, "Consumer Discretionary", "XLY"),
    (8400, 8699, "Consumer Discretionary", "XLY"),
    (8700, 8799, "Industrials", "XLI"),
    (8800, 8999, "Financials", "XLF"),
)


# Where SIC and the way the market actually trades a name disagree badly enough
# to put relative strength against the wrong benchmark. Kept deliberately short
# and explicit: every entry is a judgement call that someone can challenge, and
# an override is always reported in the run as an override.
OVERRIDES: dict[str, tuple[str, str]] = {
    # SIC 7389 "business services" for the card networks, which trade as financials.
    "V": ("Financials", "XLF"),
    "MA": ("Financials", "XLF"),
    "AXP": ("Financials", "XLF"),
    "PYPL": ("Financials", "XLF"),
    "FI": ("Financials", "XLF"),
    "FIS": ("Financials", "XLF"),
    "COIN": ("Financials", "XLF"),
    # SIC 7370 "computer services" for the ad-funded platforms, which GICS and
    # the market both treat as communication services.
    "GOOGL": ("Communication Services", "XLC"),
    "GOOG": ("Communication Services", "XLC"),
    "META": ("Communication Services", "XLC"),
    "NFLX": ("Communication Services", "XLC"),
    # SIC 5961 catalogue retail understates how much of these are cloud and ads,
    # but the equity still trades with consumer discretionary, so they stay XLY.
}


def classify(sic: str | int | None,
             ticker: str | None = None) -> tuple[str | None, str | None]:
    """(sector label, sector ETF) for a SIC code, (None, None) when unmappable.

    An unmappable code is not an error: relative strength simply falls back to
    the market benchmark and the run records that it did.
    """
    if ticker and ticker.upper() in OVERRIDES:
        return OVERRIDES[ticker.upper()]
    if sic in (None, ""):
        return None, None
    try:
        code = int(str(sic).strip())
    except ValueError:
        return None, None
    for low, high, sector, etf in _RANGES:
        if low <= code <= high:
            return sector, etf
    return None, None
