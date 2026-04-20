"""
config.py — STFS-EQ Battle Card Generator settings.
Edit this file only. Do not rename.
"""

# =====================================================================
# ACCOUNTS — add/remove/rename as needed
# =====================================================================
ACCOUNTS = [
    {"name": "Borg",   "equity": 20_000, "risk_pct": 1.0, "max_notional_pct": 10.0},
    {"name": "SMSF",   "equity": 50_000, "risk_pct": 1.0, "max_notional_pct": 10.0},
    {"name": "Family", "equity": 50_000, "risk_pct": 1.0, "max_notional_pct": 10.0},
]

# =====================================================================
# STFS-EQ SCORING — keep in sync with the PineScript indicator
# =====================================================================
EMA_FAST           = 8
EMA_MID            = 21
EMA_SLOW           = 34
HMA_LEN            = 15
RSI_LEN            = 14
RSI_LOWER_BAND     = 50.0
RSI_UPPER_BAND     = 75.0
ADX_LEN            = 14
ADX_THRESHOLD      = 20.0
OBV_EMA_LEN        = 21
OBV_SLOPE_LOOKBACK = 20
ATR_LEN            = 14
ATR_PCT_MIN        = 1.5
ATR_PCT_MAX        = 5.0
RS_LOOKBACK        = 20
WEEKLY_EMA_FAST    = 10
WEEKLY_EMA_SLOW    = 30
BENCHMARK          = "SPY"
STRONG_SCORE_MIN   = 6       # of 8
WATCH_SCORE_MIN    = 5
BREAKOUT_LOOKBACK  = 20      # 20-day high → MOO entry

# =====================================================================
# UNDERLYING TRADE CONSTRUCTION
# =====================================================================
ENTRY_ATR_MULT  = 1.5    # limit entry = close - 1.5×ATR
STOP_ATR_MULT   = 2.5    # stop = entry - 2.5×ATR
TARGET_ATR_MULT = 4.0    # target = entry + 4.0×ATR (+1.6R)

# =====================================================================
# OPTIONS PARAMETERS
# =====================================================================

# IV/HV ratio thresholds → structure selection
IV_HV_CHEAP   = 0.90    # < 0.90 → Long Call (vol cheap, buy premium)
IV_HV_NEUTRAL = 1.30    # 0.90–1.30 → Bull Call Debit Spread
IV_HV_RICH    = 1.60    # 1.30–1.60 → Bull Put Credit Spread
                         # > 1.60 → Call Diagonal

# Target DTE per structure (days to expiry)
DTE_LONG_CALL     = 50
DTE_DEBIT_SPREAD  = 40
DTE_CREDIT_SPREAD = 28
DTE_DIAG_FRONT    = 17   # short leg
DTE_DIAG_BACK     = 50   # long leg

# Spread widths (Dynamic via ATR instead of fixed dollar)
SPREAD_ATR_MULT     = 2.0    # Spread width = 2.0 × ATR

# Profit targets
LONG_CALL_TARGET_MULT   = 2.5   # 2.5× debit (150% gain)
DEBIT_SPREAD_TARGET_MULT = 2.5  # 2.5× debit (150% gain)
CREDIT_TARGET_PCT        = 0.50  # close at 50% of max credit
DIAGONAL_TARGET_MULT     = 1.50  # 1.5× debit (50% gain)

# Options liquidity gates
OPT_MIN_ATM_OI      = 100    # min open interest at ATM strike (relaxed)
OPT_MAX_SPREAD_PCT  = 30.0   # max bid-ask as % of mid (relaxed for off-hours)

# =====================================================================
# STRUCTURAL GATES — Stage 1
# =====================================================================
MIN_ADV_USD            = 50_000_000
MIN_PRICE              = 15.0
MIN_MARKET_CAP_M       = 1_000      # $1B in Finnhub millions-USD
EARNINGS_BLACKOUT_DAYS = 5

# =====================================================================
# REGIME WATCHLISTS — expanded universe
# =====================================================================
WATCHLISTS = {
    "GOLDILOCKS": [
        # ETFs
        "XLK", "XLY", "SMH", "QQQ", "IWF",
        # Mega-cap tech
        "MSFT", "NVDA", "AAPL", "AMZN", "META", "AVGO", "GOOGL",
        # Growth / software / semis
        "ORCL", "AMD", "CRM", "PANW", "NOW", "NFLX", "UBER", "APP", 
        "CRWD", "DDOG", "NET", "SNOW", "ZS", "MDB", "TEAM", "ADBE", 
        "INTU", "WDAY", "TTD", "SHOP", "SPOT", "MELI", "ASML", "LRCX", 
        "KLAC", "AMAT", "MU", "ARM", "QCOM", "TXN", "CDNS", "SNPS", 
        "PLTR", "FSLR", "ENPH"
    ],
    "LIQUIDITY": [
        # ETFs
        "QQQ", "SMH", "XLK", "ARKK", "IBIT",
        # High-beta / crypto proxies
        "NVDA", "AVGO", "TSLA", "MSTR", "COIN",
        # Semi + Speculative + High Short Interest
        "AMD", "MU", "PLTR", "SQ", "HOOD", "RIOT", "MARA",
        "AFRM", "RBLX", "UPST", "CVNA", "DKNG", "ROKU", "SOFI", 
        "TOST", "PINS", "RDDT", "CELH", "CLSK", "HUT", "BITF", "WOLF", "IONQ"
    ],
    "REFLATION": [
        # ETFs
        "XLE", "XLF", "XLI", "XME", "IWM",
        # Energy
        "XOM", "CVX", "SLB", "HAL", "COP", "EOG", "OXY", "MPC", "VLO", "PSX",
        # Banks / financials
        "JPM", "BAC", "GS", "MS", "WFC", "C", "AXP", "BLK", "BX", "KKR", "APO",
        # Industrials / materials
        "CAT", "DE", "NUE", "FCX", "URI", "PCAR", "ETN", "PWR", "GE", 
        "RTX", "LMT", "GD", "NOC", "BA", "STLD", "AA", "CLF"
    ],
    "NEUTRAL": [
        # ETFs
        "SPY", "IWM", "XLV",
        # Quality / large cap
        "AAPL", "MSFT", "JPM", "WMT", "BRK-B", "JNJ", "PG", "MRK", 
        "TMO", "DHR", "MCD", "SBUX", "PEP", "KO",
        # Healthcare
        "UNH", "LLY", "ABBV", "ABT", "ISRG", "SYK",
        # Consumer / financials
        "V", "MA", "COST", "HD", "NKE"
    ],
    "RISK_OFF": [
        # Defensive ETFs
        "XLU", "XLP", "XLV", "GLD", "TLT", "SHV",
        # Staples
        "PG", "KO", "WMT", "K", "GIS", "CPB", "SJM", "KMB", "CL", "CLX", "CHD",
        # Healthcare
        "UNH", "JNJ", "MRK", "PFE", "BMY", "GILD", "ABT", "AMGN", "HCA",
        # Utilities / Gold
        "DUK", "NEE", "AWK", "ED", "SO", "AEP", "SRE", "XEL", "NEM", "GOLD"
    ],
    "CRASH": [],  # cash only — handled separately
}

# =====================================================================
# OUTPUT
# =====================================================================
OUTPUT_DIR          = "output"
AUTO_OPEN_IN_BROWSER = True
