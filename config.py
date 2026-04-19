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
STRONG_SCORE_MIN   = 7       # of 8
WATCH_SCORE_MIN    = 5
BREAKOUT_LOOKBACK  = 20      # 20-day high → MOO entry

# =====================================================================
# UNDERLYING TRADE CONSTRUCTION
# =====================================================================
ENTRY_ATR_MULT  = 1.0    # limit entry = close - 1×ATR
STOP_ATR_MULT   = 2.0    # stop = entry - 2×ATR
TARGET_ATR_MULT = 2.0    # target = entry + 2×ATR (+2R)

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

# Spread widths in dollars
DEBIT_SPREAD_WIDTH  = 5.0
CREDIT_SPREAD_WIDTH = 5.0

# Profit targets
LONG_CALL_TARGET_MULT   = 2.0   # 2× debit (100% gain)
DEBIT_SPREAD_TARGET_MULT = 2.0  # 2× debit
CREDIT_TARGET_PCT        = 0.50  # close at 50% of max credit
DIAGONAL_TARGET_MULT     = 1.50  # 1.5× debit (50% gain)

# Options liquidity gates
OPT_MIN_ATM_OI      = 500    # min open interest at ATM strike
OPT_MAX_SPREAD_PCT  = 15.0   # max bid-ask as % of mid

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
        # Growth / software
        "ORCL", "AMD", "CRM", "PANW", "NOW", "NFLX", "UBER", "APP",
    ],
    "LIQUIDITY": [
        # ETFs
        "QQQ", "SMH", "XLK", "ARKK", "IBIT",
        # High-beta / crypto proxies
        "NVDA", "AVGO", "TSLA", "MSTR", "COIN",
        # Semi + speculative
        "AMD", "MU", "PLTR", "SQ", "HOOD", "RIOT", "MARA",
    ],
    "REFLATION": [
        # ETFs
        "XLE", "XLF", "XLI", "XME", "IWM",
        # Energy
        "XOM", "CVX", "SLB", "HAL",
        # Banks / financials
        "JPM", "BAC", "GS", "MS",
        # Industrials / materials
        "CAT", "DE", "NUE", "FCX",
    ],
    "NEUTRAL": [
        # ETFs
        "SPY", "IWM", "XLV",
        # Quality / large cap
        "AAPL", "MSFT", "JPM",
        # Healthcare
        "UNH", "LLY", "ABBV",
        # Consumer / financials
        "V", "MA", "COST", "HD",
    ],
    "RISK_OFF": [
        # Defensive ETFs
        "XLU", "XLP", "XLV", "GLD", "TLT", "SHV",
        # Staples
        "PG", "KO", "WMT",
        # Healthcare
        "UNH", "JNJ", "MRK",
        # Utilities
        "DUK", "NEE", "AWK",
    ],
    "CRASH": [],  # cash only — handled separately
}

# =====================================================================
# OUTPUT
# =====================================================================
OUTPUT_DIR          = "output"
AUTO_OPEN_IN_BROWSER = True
