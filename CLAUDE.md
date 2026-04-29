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
| `launcher.py` | Desktop entry point (boots Flask + browser) |
| `dashboard.py` | Core Web Dashboard server (Port 5001) |
| `web/` | UI (HTML/CSS/JS) |
| `battle_card.py` | Core scoring, composite-quality ranking, trade sizing, options plans, HTML. |
| `regime.py` | Auto-regime detection (drift/vol/term/skew/credit/event + sector RRG). |
| `tws_data.py` | TWS API calls: OHLC, IVP, live price, options chain, index feeds. clientId=15 (readonly). |
| `order_server.py` | HTTP order API. clientId=16 (read-write). localhost:5001. |
| `portfolio_manager.py` | Portfolio monitor (clientId=18) — STFS-EQ positions only |
| `trailing_stop_manager.py` | Stop daemon (clientId=17) |
| `manual_portfolio.py` | Discretionary combo tracker (clientId=19) — SPX/RUT/ES; reads `manual_combos.yaml` |
| `manual_combos.yaml` | Combo definitions: legs with exact fill prices. Edit this to add/remove trades. |
| `backtest.py` | Standalone CLI backtester (in-sample diagnostic). Imports indicators from `indicators.py`. |
| `indicators.py` | **Single source of truth** for the 8-factor scoring rules. `compute_factors()` is called by both live signal (`score_ticker`) and the walk-forward mini-backtest — eliminates drift. |
| `journal.py` | Journal writer. |
| `data/trade_journal.jsonl` | Persistent trade history (Synced via git). Append-only. Never blocks orders. |
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
- `clientId=17` — trailing_stop_manager.py, read-write, stop modification (auto-transmit)
- `clientId=18` — portfolio_manager.py, read-only, portfolio monitoring

- `clientId=19` — manual_portfolio.py, read-only, discretionary combo monitoring

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

### Auto-regime detection (`regime.py`)
`detect_regime()` returns `{regime, confidence, states, macro, rrg, evidence, warnings}`.
- Six context states from STFS v2.5.pine (drift/vol/term/skew/credit/event).
- Sector RRG quadrants from MacroNexus_sector rotation.pine (XLU/XLP/XLK/XLE/XLF/SMH/QQQ/IWM vs SPY).
- Routes to one of the existing `WATCHLISTS` keys (CRASH/RISK_OFF/LIQUIDITY/REFLATION/GOLDILOCKS/NEUTRAL).
- TWS-first via `tws_data.get_index()`; yfinance fallback per feed.
- Staleness: each feed records `age_days`; ≥`STALENESS_BARS_WARN` → confidence MED/LOW.
- Invoked when battle_card receives `--regime AUTO` (the launcher default).

### Composite quality ranking
`battle_card._attach_quality()` produces a 0–1 `quality` score combining:
`0.35 * score + 0.25 * win_rate + 0.20 * expectancy_R + 0.10 * n_trades + 0.10 * rs_pct`
Weights live in `config.RANKING_WEIGHTS`. Tickers with `bt_n_trades < THIN_HISTORY_TRADES`
take a `THIN_HISTORY_PENALTY` haircut (default 15%) and show a `(thin)` tag in HTML.
Sort order is now `quality DESC, score DESC, rs_pct DESC` — score-only sort is retired.

### Walk-forward mini-backtest with friction
`battle_card.run_mini_backtest()` uses anchored walk-forward optimisation (WFO):
anchor = first 50% of bars (fixed train); `BACKTEST_FOLDS` (default 5) test windows
slide across the remaining 50%. The composite quality ranker uses **mean test-fold**
stats (out-of-sample). A separate `BACKTEST_RECENT_BARS` (default 252) window gives
a "recent era" slice independent of the fold structure. HTML displays fold mean ±std
for win-rate and expectancy_R so train→test degradation is visible. Friction model:
`SLIPPAGE_PCT` (default 0.05% per leg, both entry and exit) and a flat
`COMMISSION_PER_TRADE` (0.001 per leg = 0.2% round-trip, as fraction of notional).
Both knobs in `config.py`.

### Hysteresis on auto-regime
`regime.detect_regime()` persists state to `output/.regime_state.json`. A flip
to a new regime requires `REGIME_FLIP_CONFIRMATIONS` (default 2) consecutive runs
showing the same new state. While pending, the previously confirmed regime is
served. The HTML evidence panel surfaces the pending state and counter. Manual
`--regime <NAME>` override skips hysteresis entirely.

### IV-crush sensitivity (long-premium structures)
For `long_call` and `diagonal`, `_vega_shock_breakeven()` uses Black-Scholes to
solve for the underlying price needed to recover the debit if IV drops by
`VEGA_DROP_TEST` points (default 10v). HTML row "BE @ -10v" shows that price;
if it exceeds the underlying target, "⚠ VEGA RISK" badge appears. Informational
only — does not change sizing or block the trade.

### Earnings proximity badge
`fetch_earnings_calendar` now returns `{symbol: ISO_date}` (still works as a set
for blackout membership). The blackout window is `EARNINGS_BLACKOUT_DAYS` (5);
the warn window is `EARNINGS_WARN_DAYS` (14). Tickers with earnings in 6–14 days
land in STRONG BUY but show an amber `EARN <date> (Nd)` badge in the card header.

### Session risk audit
The HTML "Session Risk Audit" panel (above the 5-Gate) sums per-account underlying
risk + options risk across all STRONG BUYs and compares to
`MAX_SESSION_RISK_PCT × equity` (default 2.0%). Surfaces breach with red ⛔ but
does NOT block — consistent with the manual-transmit discipline.

### Trade journal (self-improvement loop seed)
`order_server` calls `journal.append_entry` on every successful order placement,
writing to `data/trade_journal.jsonl`. Each line captures: signal context
(regime, score, quality, factors, IVP, BT test stats, earnings_date) +
order details (account, structure, IDs, **net_debit/net_credit/max_loss_per_contract/target_value**).
Financial fields must be present in the journal for portfolio_manager exit signals to work.
Full chain: `battle_card._order_json()` → JS modal payload → `order_server._place_options()` journal save.
A future analysis pass can compare realized outcomes against the backtest's expected expectancy_R
to detect signal drift. The journal is best-effort — never blocks order placement on I/O failure.

### Portfolio manager exit signals
`portfolio_manager.get_portfolio_data()` evaluates three independent signals per position:
1. **Price**: underlying vs `target_value` / `stop` from journal
2. **P&L%**: structure-specific thresholds (see below)
3. **DTE**: time exit when option runs short

Structure sets for exit logic:
- `_DEBIT_STRUCTURES = {"long_call", "debit_spread"}` — P&L target +150%, stop at -80%, DTE exit at 14
- `_CREDIT_STRUCTURES = {"credit_spread"}` — target 50% of max credit, stop at 80% of max loss, DTE exit at 21
- `_DIAGONAL_STRUCTURES = {"diagonal"}` — P&L target +50% (`DIAGONAL_TARGET_MULT - 1.0`), stop at -80%, DTE exit at 21

Position deduplication: `_match_positions_to_journal` tracks `seen_refs` (orderRef set) — a diagonal
with two OPT legs in TWS produces two position objects but only one row in the portfolio table.

`_connect()` must call `asyncio.set_event_loop(asyncio.new_event_loop())` before `IB()` when
running from a ThreadPoolExecutor thread (no event loop in worker threads by default).

### Trailing stop daemon lifecycle
Dashboard auto-starts `trailing_stop_manager.py` (clientId=17) on launch via `subprocess.Popen`.
`atexit.register(_stop_daemon)` ensures it terminates cleanly when dashboard.py exits — no orphaned
processes. The daemon is harmless pre-market (TRAIL_ACTIVATE_R=1.0 means no stops are moved until
≥1R profit; no live prices pre-market means no triggers fire).

### Bonus momentum factors (Pine v3 — additive, not part of core 8)
`bonus_rsi_slope` (RSI[0] > RSI[3]) and `bonus_atr_expansion`
(ATR%(10) / ATR%(60) > 1.10) are stored on every score result as `momentum_bonus` (0..2).
Displayed as `+MB N` in HTML. They do NOT change the 8-factor score / STRONG-BUY gate.

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
- clientId assignments (15/16/17/18)
- The IVP formula (`sum(v < current_iv) / len(iv_vals) * 100`)
- The `--no-open` flag pattern in launcher.py subprocess call
- The `_TWS_TICKER` alias maps in both tws_data.py and order_server.py
- `_DIAGONAL_STRUCTURES` separate from `_CREDIT_STRUCTURES` — diagonal uses net_debit (not net_credit), 50% gain target (not credit-capture logic)
- Financial fields in journal (net_debit/net_credit/max_loss_per_contract/target_value) — removing breaks portfolio P&L signals
- `manual_portfolio.py` is fully isolated — never import from it in dashboard/order_server/portfolio_manager. It owns its own TWS connection (clientId=19) and reads only `manual_combos.yaml`.

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
