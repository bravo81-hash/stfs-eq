# STFS-EQ Reliability & Account Settings — Design Spec
Date: 2026-04-27

## Scope

Three independent improvements to gain real-money trading confidence:

1. **Account Settings GUI** — edit equity/risk per account from the launcher
2. **Signal ↔ TradingView Alignment** — weekly bar fix + data source badge + indicator panel
3. **Backtest Robustness** — rolling WFO, gap-aware stops, commission fix, recent-era stats

---

## Area 1: Account Settings GUI (`launcher.py`)

### What
Collapsible section in the launcher GUI between the Finnhub API key row and the regime selector. Lets the user update any account's equity, risk_pct, or max_notional_pct without opening a text editor.

### Layout
Toggle button "▶ ACCOUNT SETTINGS" expands a frame with one row per account:

```
[Borg  ]  Equity: [_20000_]  Risk%: [_1.0_]  MaxNotional%: [_10.0_]
[SMSF  ]  Equity: [_50000_]  Risk%: [_1.0_]  MaxNotional%: [_10.0_]
[Family]  Equity: [_50000_]  Risk%: [_1.0_]  MaxNotional%: [_10.0_]
                                                           [Save Accounts]
```

Account name labels are read-only (names don't change at runtime).

### Behaviour
- Fields populate from `config.ACCOUNTS` on launch
- Validation on Save:
  - equity > 0
  - 0 < risk_pct ≤ 10
  - 0 < max_notional_pct ≤ 50
- On valid Save: replace the `ACCOUNTS = [...]` block in `config.py` via regex, write file atomically
- On invalid Save: show red error label inline, do not write
- On success: show green "Saved ✓" label for 3 seconds
- No launcher restart needed — battle card subprocess re-imports `config` fresh each run
- Section collapsed by default

### Implementation notes
- Regex target: `ACCOUNTS = [\n    ...multiline...\n]` — replace entire block
- Write using a temp file + `os.replace()` for atomicity
- Fields are `tk.Entry` widgets bound to `tk.StringVar`

---

## Area 2: Signal ↔ TradingView Alignment

### 2a — Weekly bar resampling fix (`indicators.py`)

**Problem:** Weekly EMA(10) and EMA(30) are computed by resampling daily bars. Default pandas resample uses Monday-labelled weeks. TradingView uses **Friday-close weekly bars** (Mon–Fri window, bar labelled on Friday close).

**Fix:** Change resample call to `rule='W-FRI', label='right', closed='right'`. This anchors weekly bars to Friday close, matching TradingView's default weekly chart. One-line change.

**Files:** `indicators.py` — wherever weekly OHLC is resampled for F2.

### 2b — Data source badge

**Problem:** When TWS is down, yfinance data silently replaces TWS data. User has no visibility into which source each ticker used.

**Fix:**
- `fetch_daily_ohlc()` in `battle_card.py` already tracks which tickers came from TWS vs yfinance. Build a `sources: dict[str, str]` mapping `{ticker: "TWS" | "yf"}` and include it in the returned data structure.
- Pass `source` field through `score_ticker()` result dict.
- HTML card header: small pill badge — green `TWS` or amber `yf`. Amber signals "cross-check TradingView before acting."

### 2c — Raw indicator values panel (HTML)

**Problem:** When signal disagrees with TradingView, there's no quick way to see which factor is diverging.

**Fix:** Add a collapsed `▶ Raw Indicators` section inside each STRONG BUY card (and optionally WATCH cards) showing:

```
EMA 8/21/34:  182.4 / 178.1 / 171.3    RSI(14): 63.2    ADX(14): 28.7
wEMA 10/30:   185.0 / 172.6             OBV vs EMA: +12.3%
ATR%: 2.4%    RS vs SPY: +4.1%          Source: TWS
```

All values already computed in `score_ticker()` / `compute_factors()` — this is display-only. No logic change.

**Files:** `battle_card.py` — HTML template section per card.

---

## Area 3: Backtest Robustness

### 3a — Rolling walk-forward (`battle_card.py` + `config.py`)

**Problem:** Single 70/30 split produces unreliable stats. A lucky or unlucky test window dominates the quality ranking.

**Fix:** 5-fold **anchored** walk-forward. Train always starts at bar 0 (expanding window). Test window slides forward. Each fold's test ≈ 20% of total bars (~300 bars on 1500-day data).

**Stats reported per ticker:**
- Mean win rate ± std dev across folds
- Mean expectancy_R ± std dev
- Consistency score: folds where expectancy_R > 0 / total folds (e.g. `4/5`)

**HTML display:**
```
BT: 58±8% WR · +0.31±0.12R · 4/5 folds profitable
```

**Composite quality ranker:** uses mean expectancy_R from fold stats (replaces single test expectancy).

**Thin-history penalty:** triggers if *any* fold has < `THIN_HISTORY_TRADES` signals (not just overall test count).

**New config knob:** `BACKTEST_FOLDS = 5`

**Files:** `battle_card.py` — `run_mini_backtest()`, `_attach_quality()`, HTML template. `config.py` — add `BACKTEST_FOLDS`.

### 3b — Gap-aware stop simulation (`battle_card.py`)

**Problem:** `_simulate()` fills stops at the stop price even when next bar opens below it. Overstates exit quality on gap-down moves.

**Fix:** In `_simulate()`, check `open[i+1]` before `low[i+1]`:

```python
# Before checking low, check if open already gaps through stop
if op_a[i+1] < stop_loss:
    _close(op_a[i+1])  # gap-down: fill at open with slippage
elif lo_a[i+1] <= stop_loss:
    _close(stop_loss)   # intraday: fill at stop with slippage
elif hi_a[i+1] >= take_profit:
    _close(take_profit)
```

Same gap logic applies symmetrically to take-profit: if `open[i+1] > take_profit`, fill at `open[i+1]` (gap-up favours us but we don't assume we hit the exact target). This makes wins slightly larger on gap-ups and losses larger on gap-downs — more realistic than assuming perfect fills at order levels.

**Files:** `battle_card.py` — `_simulate()`.

### 3c — Commission formula fix (`battle_card.py` + `config.py`)

**Problem:** Current formula `2 * COMMISSION_PER_TRADE / (entry_price * 100)` undercharges commission by ~100×. The `* 100` denominator was treating $1 as per-100-share lot, but the result is used as a unitless % — so it's 100× too small.

**Fix:** Express commission as **flat fraction of notional per leg** (unitless). New formula:

```python
trades.append(gross - 2 * C.COMMISSION_PER_TRADE)
```

**New default:** `COMMISSION_PER_TRADE = 0.001` (0.1% per leg = 0.2% round-trip). For a $5,000 notional trade: ~$10 round-trip. Realistic for IBKR tiered pricing at typical swing trade size.

**Config comment update:** change description from "$ per share-trade leg" to "fraction of notional per leg (0.001 = 0.1%)".

**Files:** `battle_card.py` — `_simulate()`. `config.py` — `COMMISSION_PER_TRADE` default + comment.

### 3d — Recent-era stats (`battle_card.py` + `config.py`)

**Problem:** Full-history backtest averages across multiple market regimes. How the signal behaves *now* (current regime/conditions) is more actionable.

**Fix:** Run a separate `_simulate()` pass on the most recent `BACKTEST_RECENT_BARS` bars (default 252 = ~1 year). Report as a second stats block alongside rolling WFO stats.

**HTML display (two rows):**
```
BT-all: 58±8% WR · +0.31R · 4/5 folds
BT-1yr: 62% WR · +0.38R · n=6
```

Significant divergence between BT-all and BT-1yr flags a regime change in signal behaviour — user should weight BT-1yr more heavily.

**New config knob:** `BACKTEST_RECENT_BARS = 252`

**Files:** `battle_card.py` — `run_mini_backtest()`, HTML template. `config.py` — add `BACKTEST_RECENT_BARS`.

---

## Files Changed Summary

| File | Changes |
|------|---------|
| `launcher.py` | Add account settings collapsible section |
| `indicators.py` | Weekly resample → `W-FRI` |
| `battle_card.py` | Data source tracking, indicator panel HTML, rolling WFO, gap-aware stops, commission fix, recent-era stats |
| `config.py` | Add `BACKTEST_FOLDS`, `BACKTEST_RECENT_BARS`; update `COMMISSION_PER_TRADE` default + comment |

No new files. No schema changes. No changes to `order_server.py`, `tws_data.py`, `regime.py`, or `journal.py`.

---

## What This Does NOT Fix

- **Survivorship bias** — watchlists contain today's survivors; historical constituent data not available
- **Options backtest** — options P/L simulation is out of scope; underlying equity backtest only
- **True regime-conditional BT** — historical per-bar regime detection requires VIX/HYG/SKEW history per bar; too expensive; replaced by BT-1yr proxy
