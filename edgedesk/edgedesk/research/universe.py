"""The research universe: about two hundred liquid US names across every sector.

Chosen for liquidity and sector spread, not for how they did. It is still
today's list, so it carries survivorship bias: companies that failed or were
acquired between 2019 and now are absent, which flatters any long-only result.
That is why a setup is never judged on its raw return here, only against SPY
and against random entries in these same names, which share the same bias.
"""

from __future__ import annotations

BENCHMARK = "SPY"

_BY_SECTOR = {
    "technology": """AAPL MSFT NVDA AVGO ORCL CRM ADBE AMD INTC CSCO QCOM TXN IBM NOW INTU AMAT
        MU LRCX KLAC ADI PANW SNPS CDNS FTNT MRVL ANET MSI APH NXPI MCHP ON HPQ DELL WDAY TEAM
        SHOP SNOW DDOG CRWD ZS NET OKTA TWLO AKAM GLW TER SWKS""",
    "communication": "GOOGL META NFLX DIS CMCSA T VZ TMUS EA TTWO SPOT PINS SNAP ROKU WBD",
    "consumer_discretionary": """AMZN TSLA HD MCD NKE LOW SBUX TJX BKNG ABNB MAR HLT CMG ORLY AZO
        ROST YUM DHI LEN GM F EBAY ETSY LULU ULTA DPZ BBY RCL CCL EXPE DECK""",
    "consumer_staples": "WMT COST PG KO PEP PM MO MDLZ CL TGT KMB GIS STZ KR SYY HSY EL DG DLTR",
    "health_care": """LLY UNH JNJ ABBV MRK TMO ABT DHR PFE AMGN ISRG VRTX REGN GILD BMY CVS CI ELV
        MDT SYK BSX ZTS HCA MCK IDXX DXCM EW BIIB MRNA ALGN""",
    "financials": """JPM BAC WFC GS MS C BLK SCHW AXP V MA PYPL SPGI MCO ICE CME CB PGR AIG MET
        USB PNC TFC COF BX KKR""",
    "industrials": """CAT DE BA HON UNP UPS FDX RTX LMT GD NOC GE MMM ETN EMR ITW PH CSX NSC WM
        CTAS FAST URI PCAR DAL UAL LUV""",
    "energy": "XOM CVX COP EOG SLB OXY MPC PSX VLO KMI WMB HAL DVN FANG",
    "materials": "LIN SHW APD FCX NEM NUE DOW DD ECL",
    "utilities_real_estate": "NEE DUK SO AEP D PLD AMT EQIX CCI SPG O",
}

SECTOR_ETF = {
    "technology": "XLK", "communication": "XLC", "consumer_discretionary": "XLY",
    "consumer_staples": "XLP", "health_care": "XLV", "financials": "XLF",
    "industrials": "XLI", "energy": "XLE", "materials": "XLB",
    "utilities_real_estate": "XLU",
}

SECTOR_OF = {t: sector for sector, names in _BY_SECTOR.items() for t in names.split()}
TICKERS = sorted(SECTOR_OF)
