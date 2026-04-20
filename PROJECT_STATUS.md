# STFS-EQ Project Status: Session Alpha Upgrades

## 1. Strategy Optimization (The Math)
- **Engine Dialed-in**: Standardized parameters to high-expectancy mathematical optimums:
    - `STRONG_SCORE_MIN`: 6/8
    - `STOP_ATR_MULT`: 2.5 (Tight risk management)
    - `TARGET_ATR_MULT`: 4.0 (Trend following capture)
    - `ENTRY_ATR_MULT`: 1.5 (Pullback limit)
- **Universe Expansion**: Expanded all regime watchlists in `config.py` to 150+ high-liquid, high-beta names.
- **Dynamic Widths**: Shifted Options spreads from $5.00 fixed to `2.0 * ATR` dynamic width.

## 2. Options Engine Enhancements
- **Liquidity Bypass**: Implemented smart detection for weekend/off-hours TWS & YFinance data.
    - Swaps missing bid/ask with `lastPrice` + simulated spread.
    - Swaps zero Open Interest with Volume for scanning continuity.
- **Profit Target Alignment**: Bumped `LONG_CALL` and `DEBIT_SPREAD` targets to **2.5x (150% gain)** to sync with the underlying +4.0 ATR move.
- **TWS Integration**: Established cyan `TWS` badges for live institutional data vs `YF` for fallback data.

## 3. UI/UX & Backtesting
- **Embedded Backtest Cards**: Battle Cards now feature ID-specific backtest badges (Win Rate, Trades, Net Return).
- **Color Coding**: Green (≥60%), Amber (50-59%), Red (<50%) for instant setup validation.
- **Projected Options ROI**: Backtester now calculates R-Unit yields (+150% win / -100% loss) to show leveraged expectancy.
- **Formatting Fixes**: Patched decimal truncation in HTML cards (e.g., accurately showing `+1.6R` targets).
