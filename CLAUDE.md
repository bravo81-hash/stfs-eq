# Inherits: ~/CLAUDE.md principles
# STFS-EQ — Claude Code Reference

This file is the authoritative context document for AI-assisted development on this project.
Read it in full at the start of every session before touching any code.

---

## What this system does

STFS-EQ is a **systematic equity swing trading tool** for one user trading 3 accounts
(Borg $20k, SMSF $50k, Family $50k) via Interactive Brokers TWS.

1. **Launcher** (`launcher.py`) — Tkinter GUI. Selects regime, runs battle card generation,
   starts the order server, opens the HTML output in a browser.
2. **Battle Card** (`battle_card.py`) — scores stocks in the selected regime watchlist using
   8 technical factors, sizes positions per account, builds options plans, outputs a single
   self-contained HTML file with a Push-to-TWS modal.
3. **TWS Data** (`tws_data.py`) — ib_insync wrapper for live TWS data: OHLC, live stock price,
   options chain, IV Percentile (IVP). Falls back to yfinance on TWS failure.
4. **Order Server** (`order_server.py`) — local HTTP API on localhost:5001. Receives JSON from
   the battle card modal and places HELD bracket/options orders via TWS clientId=16.

Orders are always placed with `transmit=False` (HELD in TWS). The user manually reviews
and right-click → Transmits in TWS — no auto-execution ever.

---

## File map

| File | Purpose |
|------|---------|
| `config.py` | **Single source of truth for all parameters.** Edit only here. |
| `launcher.py` | Tkinter GUI entry point. Starts order server + battle card subprocess. |
| `battle_card.py` | Core scoring, trade sizing, options plan building, HTML generation. |
| `tws_data.py` | TWS API calls: OHLC, IVP, live price, options chain. clientId=15 (readonly). |
| `order_server.py` | HTTP order API. clientId=16 (read-write). localhost:5001. |
| `backtest.py` | Walk-forward mini-backtest embedded in each battle card entry. |
| `optimizer.py` | Parameter sweep: finds optimal ENTRY/STOP/TARGET ATR multiples. |
| `expectancy_optimizer.py` | Factor analysis: tests all 256 combinations of the 8 scoring factors. |
| `stfs_eq_dashboard.pine` | TradingView PineScript v2.1 — kept in sync with Python config. |
| `setup.command` | macOS double-click installer (pip installs, first-run check). |
| `test_tws.py` | TWS connection and data fetch diagnostics. |
| `test_rr.py` | R:R calculation sanity checks. |
| `test_finnhub.py` | Finnhub earnings/market-cap gate diagnostics. |

**Never add:** planning docs, session status files, audit prompts, or any file that describes
what was done rather than what the system does. Those rot immediately and mislead future AI.

---

## Key design decisions (do not reverse without understanding why)

### TWS client IDs
- `clientId=15` — tws_data.py, readonly, data only
- `clientId=16` — order_server.py, read-write, order placement

These must not clash. If you add another IB connection anywhere, pick a different ID.

### IVP not IVR
The options structure selector uses **IV Percentile** (IVP = % of past days where IV < today).
This matches TWS's "52IVP" watchlist column. We do NOT use IV Rank (min-max formula).
The key name in all dicts is `"ivp"`, the display label is "IVP".

### Current IV from 5-min intraday bars
The 365-day daily IV bar for today is not finalized until well after market close.
We fetch `durationStr="7200 S", barSizeSetting="5 mins", whatToShow="OPTION_IMPLIED_VOLATILITY"`
to get the last settled 5-min bar as `current_iv`. This matches TWS immediately.

### Live underlying price for strike selection
`get_options_data()` fetches a 1-min TRADES bar (`durationStr="300 S"`) after qualifying
the contract to get today's live price for ATM strike selection. Falls back to df close.

### All orders HELD (transmit=False)
Every order — parent, take-profit, stop-loss, options — has `transmit=False`.
The user manually transmits in TWS. Do not change this.

### TWS ticker aliases
`_TWS_TICKER = {"BRK-B": "BRK B", "BRK-A": "BRK A"}` exists in both `tws_data.py`
and `order_server.py`. yfinance uses hyphens; TWS Stock() requires spaces.

### No double browser open
`launcher.py` passes `--no-open` to the battle_card subprocess and opens the browser
itself via `webbrowser.open()` in `_on_done()`. `battle_card.py` respects `--no-open`
and skips its own `webbrowser.open()` call.

### Notional for options = max_loss_per_contract × contracts
For all structures including credit spreads, `notional = max_loss_per_contract * cts`.
Do NOT use `spread_width * 100 * cts` for credit spreads — it inflates displayed risk.

---

## Scoring system (8 factors, must match PineScript v2.1)

| Factor | Condition |
|--------|-----------|
| F1 | EMA(8) > EMA(21) > EMA(34) — daily EMA stack |
| F2 | Weekly close > EMA(30w) AND EMA(10w) > EMA(30w) |
| F3 | HMA(15) rising bar-over-bar |
| F4 | ADX(14) > 20 AND strictly rising two consecutive bars |
| F5 | RSI(14) in [50, 75] |
| F6 | 20-day RS > SPY 20-day RS |
| F7 | OBV > OBV_EMA(21) AND OBV > OBV 20 bars ago |
| F8 | ATR% in [1.5%, 5.0%] |

- `STRONG BUY`: score ≥ 6 AND F1+F2+F8 all true (trio) → generates options plan
- `WATCH`: score ≥ 5 AND F1 true → no options plan
- `SKIP`: everything else

F4 note: "strictly rising" means `adx > adx[1] > adx[2]` — not just `ta.rising(adx, 2)`.

---

## Options structure selection (IVP quartiles)

| IVP | Structure | DTE |
|-----|-----------|-----|
| < 25 | Long Call | 50 |
| 25–50 | Bull Call Debit Spread | 40 |
| 50–75 | Bull Put Credit Spread | 28 |
| > 75 | Call Diagonal | back=50, front=17 |

Fallback (when IVP unavailable): uses IV/HV ratio with thresholds from config.

---

## Trade parameters (from config.py — keep in sync with PineScript)

```
ENTRY_ATR_MULT  = 1.5   # limit entry = close - 1.5×ATR
STOP_ATR_MULT   = 2.5   # stop = entry - 2.5×ATR
TARGET_ATR_MULT = 4.0   # target = entry + 4.0×ATR (+1.6R)
STRONG_SCORE_MIN = 6
SPREAD_ATR_MULT  = 2.0  # options spread width = 2×ATR
```

---

## What NOT to change without full context

- `transmit=False` on all orders
- clientId assignments (15/16)
- The IVP formula (`sum(v < current_iv) / len(iv_vals) * 100`)
- The `--no-open` flag pattern in launcher.py subprocess call
- The `_TWS_TICKER` alias maps in both tws_data.py and order_server.py

---

## PineScript

`stfs_eq_dashboard.pine` is v2.1. Default values match config.py exactly.
The user pastes this manually into TradingView Pine Editor — it cannot be auto-deployed.
When config.py parameters change, update the Pine defaults to match.

---

## Accounts

Three accounts in config.py: Borg (equity $20k), SMSF ($50k), Family ($50k).
All at 1% risk per trade, 10% max notional. Do not hardcode account names anywhere
outside config.py — they flow through from `C.ACCOUNTS`.

---

## Two-computer workflow

The user works from **Mac Mini** (home) and **MacBook Air** (on the go / live trading).
Both use the Claude Mac app against this git repo.

- Always `git pull` before starting any session on either machine.
- Always commit and push before switching machines.
- `.api_key` (Finnhub) is gitignored — must exist on both machines manually.
- TWS runs on whichever machine is being used for live trading that day.
- Only one TWS instance should be connected at a time.
