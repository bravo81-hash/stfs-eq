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
        "TOST", "PINS", "RDDT", "CELH", "IONQ"
        # Removed: CLSK, HUT, BITF (micro-cap crypto miners, thin options)
        # Removed: WOLF (sub-$1, fails MIN_PRICE gate but wastes a fetch)
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
# AUTO-REGIME DETECTION (regime.py)
# Thresholds ported from STFS v2.5.pine + MacroNexus_sector rotation.pine
# =====================================================================

# Drift state: 10-day SPY pct return
DRIFT_STRONG_UP = 3.0    # > 3%
DRIFT_MILD_UP   = 1.0    # > 1%
DRIFT_MILD_DN   = -1.0   # < -1%
DRIFT_STRONG_DN = -3.0   # < -3%

# Vol state: ATR%(10) / ATR%(60) on SPY
VOL_EXPANDING  = 1.30    # > 1.30
VOL_COMPRESSED = 0.80    # < 0.80

# Term structure: VIX / VIX3M
TERM_BACKWARDATION = 1.00   # > 1.00
TERM_CONTANGO      = 0.95   # < 0.95

# SKEW (CBOE)
SKEW_CRASH_FEAR = 140.0     # > 140
SKEW_COMPLACENT = 120.0     # < 120

# Credit stress: HYG 5d %
CREDIT_STRESSED = -1.0      # < -1%
CREDIT_BID      =  1.0      # >  1%

# Event premium: VIX9D / VIX
EVENT_PRICED_IN = 1.05      # > 1.05

# Sector RRG lookback
RRG_TREND_LOOKBACK    = 20
RRG_MOMENTUM_LOOKBACK = 5

# Sectors / macro feeds (yfinance symbols; TWS uses bare symbol w/ index secType)
REGIME_FEEDS = {
    "SPY":   "SPY",
    "QQQ":   "QQQ",
    "IWM":   "IWM",
    "XLU":   "XLU",
    "XLP":   "XLP",
    "XLK":   "XLK",
    "XLE":   "XLE",
    "XLF":   "XLF",
    "SMH":   "SMH",
    "BTC":   "BTC-USD",
    "VIX":   "^VIX",
    "VIX3M": "^VIX3M",
    "VIX9D": "^VIX9D",
    "SKEW":  "^SKEW",
    "HYG":   "HYG",
}

STALENESS_BARS_WARN = 2     # ≥2 bars stale → confidence LOW

# =====================================================================
# RANKING — composite quality score (battle_card.py)
# =====================================================================
RANKING_WEIGHTS = {
    "score":      0.35,    # 8-factor score / 8
    "win_rate":   0.25,    # historical mini-backtest win%
    "expectancy": 0.20,    # avg R per trade
    "n_trades":   0.10,    # robustness (capped at 20)
    "rs_pct":     0.10,    # current RS vs benchmark
}
THIN_HISTORY_TRADES   = 5     # < this many bt trades → penalize
THIN_HISTORY_PENALTY  = 0.15  # 15% composite haircut
N_TRADES_CAP          = 20    # for normalization

# =====================================================================
# BONUS FACTORS (Pine Momentum Panel v3 — additive, not part of core 8)
# =====================================================================
BONUS_RSI_SLOPE_LOOKBACK = 3      # RSI[0] > RSI[3]
BONUS_ATR_FAST           = 10
BONUS_ATR_SLOW           = 60
BONUS_ATR_EXPANSION_MIN  = 1.10   # ATR%(10) / ATR%(60) > 1.10

# =====================================================================
# BACKTEST FRICTION + WALK-FORWARD
# =====================================================================
BACKTEST_FOLDS          = 5       # anchored walk-forward folds (test windows)
BACKTEST_RECENT_BARS    = 252     # ~1 year; "recent era" stats window
SLIPPAGE_PCT            = 0.05    # ±0.05% per leg (entry + exit)
COMMISSION_PER_TRADE    = 0.001   # fraction of notional per leg (0.001 = 0.1%); 0.2% round-trip

# =====================================================================
# REGIME HYSTERESIS
# =====================================================================
REGIME_FLIP_CONFIRMATIONS = 2     # require N consecutive runs in new state before flipping

# =====================================================================
# SESSION RISK GATE
# =====================================================================
MAX_SESSION_RISK_PCT      = 2.0   # warn if total new-trade risk on any account exceeds this

# =====================================================================
# IV CRUSH SENSITIVITY (long calls + diagonals)
# =====================================================================
VEGA_DROP_TEST            = 10.0  # vega points; "BE @ -10v" assumed shock for break-even calc

# =====================================================================
# EARNINGS PROXIMITY BADGE
# =====================================================================
EARNINGS_WARN_DAYS        = 14    # show amber badge if earnings within N days

# =====================================================================
# OUTPUT
# =====================================================================
OUTPUT_DIR          = "output"
AUTO_OPEN_IN_BROWSER = True
