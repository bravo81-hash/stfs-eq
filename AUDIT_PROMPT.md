# AUDIT REQUEST: STFS-EQ Optimized Trading Engine

**Context:** 
We have just updated the STFS-EQ backend (Python) and frontend (HTML/CSS) to a mathematically optimized 8-factor scoring system. The system now uses vectorized backtesting to validate setups before display.

**Task for Claude Code:**
Please perform a deep structural and mathematical audit of the following components:

1.  **`battle_card.py` & `tws_data.py` (Options Logic):**
    - Audit the new `spread_width = atr_val * C.SPREAD_ATR_MULT` dynamic sizing. Ensure it handles edge cases where high-priced stocks might not have a strike exactly at the 2.0x ATR boundary.
    - Check the "weekend fallback" logic in `fetch_options_data` (lines ~410-430). Does the `lastPrice * 0.95/1.05` simulation provide realistic enough spreads for off-hours planning?

2.  **`backtest.py` (The Engine):**
    - Scrutinize the loop for "Look-ahead Bias". Ensure the entry (tomorrow's Limit/MOO) is strictly determined by today's technical closing data only.
    - Validate the `Projected Options Profile` calculation. Is the assumption of `1.5R` win vs `1.0R` loss fundamentally sound given the `+4.0ATR` underlying target and `2.5ATR` stop?

3.  **`config.py` (Core Constants):**
    - Review the expanded watchlists. Are there any tickers with known low volume that might break the `reqMktData` snapshot for TWS?

**Goal:**
Identify any logic gaps where the "Theoretical math" (Backtest) might differ from "Real-world execution" (Slippage, Spread, and IBKR API Pacing).
