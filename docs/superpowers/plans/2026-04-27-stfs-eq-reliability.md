# STFS-EQ Reliability & Account Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the system's backtest statistically credible, signal values verifiable against TradingView, and account parameters editable from the launcher GUI.

**Architecture:** All changes are self-contained in existing files. `indicators.py` exposes more data. `battle_card.py` gets a more robust simulation engine, rolling walk-forward, and richer HTML output. `launcher.py` gains an account-settings panel that writes back to `config.py`. No new modules, no schema changes.

**Tech Stack:** Python 3.10+, pandas, numpy, tkinter, re, pathlib. Tests: pytest (run with `python -m pytest test_backtest.py -v`).

---

## File Map

| File | What changes |
|---|---|
| `config.py` | Add `BACKTEST_FOLDS`, `BACKTEST_RECENT_BARS`; fix `COMMISSION_PER_TRADE` default + comment |
| `indicators.py` | Return `ema_fast/mid/slow`, `obv_raw`, `obv_ema`, `wema_fast`, `wema_slow` series from `compute_factors` |
| `battle_card.py` | Fix `_simulate` (commission + gap-aware stops); rolling WFO in `run_mini_backtest`; `_attach_quality` thin-history fold check; `score_ticker` exposes `raw_indicators`; `fetch_daily_ohlc` returns `sources`; `render_card` gets source badge + indicator panel + new BT HTML |
| `launcher.py` | Collapsible account-settings section that writes `ACCOUNTS` block back to `config.py` |
| `test_backtest.py` | New file — pytest tests for simulation and backtest changes |

---

## Task 1: Config knobs

**Files:**
- Modify: `config.py:227-231`

- [ ] **Step 1: Edit config.py**

Replace the existing backtest block:

```python
# =====================================================================
# BACKTEST FRICTION + WALK-FORWARD
# =====================================================================
BACKTEST_TRAIN_PCT      = 0.70    # oldest 70% = train; newest 30% = test (test feeds quality)
SLIPPAGE_PCT            = 0.05    # ±0.05% per leg (entry + exit) widens fills realistically
COMMISSION_PER_TRADE    = 1.0     # $ per share-trade leg; subtracted from realised R
```

with:

```python
# =====================================================================
# BACKTEST FRICTION + WALK-FORWARD
# =====================================================================
BACKTEST_FOLDS          = 5       # anchored walk-forward folds (test windows)
BACKTEST_RECENT_BARS    = 252     # ~1 year; "recent era" stats window
SLIPPAGE_PCT            = 0.05    # ±0.05% per leg (entry + exit)
COMMISSION_PER_TRADE    = 0.001   # fraction of notional per leg (0.001 = 0.1%); 0.2% round-trip
```

- [ ] **Step 2: Verify Python loads cleanly**

```bash
cd /Users/bhaviksarvaiya/stfs-eq && python -c "import config as C; print(C.BACKTEST_FOLDS, C.BACKTEST_RECENT_BARS, C.COMMISSION_PER_TRADE)"
```

Expected output: `5 252 0.001`

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "config: add BACKTEST_FOLDS, BACKTEST_RECENT_BARS; fix COMMISSION_PER_TRADE to fraction"
```

---

## Task 2: Commission fix in `_simulate`

**Files:**
- Create: `test_backtest.py`
- Modify: `battle_card.py` — `_simulate._close` inner function (~line 563)

- [ ] **Step 1: Create test file with commission test**

```python
# test_backtest.py
"""Pytest tests for backtest simulation correctness."""
import numpy as np
import pandas as pd
import pytest
import config as C
from battle_card import _simulate, _stats


def _arrays(n, price, atr_val, sb_indices, lo_override=None, hi_override=None, op_override=None):
    """Build minimal arrays for _simulate. Limit-order entry (not breakout)."""
    sb_a  = np.zeros(n, dtype=bool)
    for i in sb_indices:
        sb_a[i] = True
    brk_a = np.zeros(n, dtype=bool)          # all limit entries
    cl_a  = np.full(n, price, dtype=float)
    at_a  = np.full(n, atr_val, dtype=float)
    op_a  = np.full(n, price, dtype=float)
    hi_a  = np.full(n, price * 1.02, dtype=float)
    lo_a  = np.full(n, price * 0.98, dtype=float)
    if lo_override:
        for idx, val in lo_override.items():
            lo_a[idx] = val
    if hi_override:
        for idx, val in hi_override.items():
            hi_a[idx] = val
    if op_override:
        for idx, val in op_override.items():
            op_a[idx] = val
    return sb_a, brk_a, cl_a, at_a, op_a, hi_a, lo_a


def test_commission_is_fraction_of_notional():
    """Commission should be 2 * COMMISSION_PER_TRADE, not divided by price*100."""
    price, atr_val = 50.0, 2.0
    # signal at bar 0; limit = 50 - 1.5*2 = 47.0
    # bar 1: low=46 → enters at 47*(1+slip)
    # bar 2: high=56 > target → exits at target*(1-slip)
    n = 5
    limit = price - C.ENTRY_ATR_MULT * atr_val  # 47.0
    slip = C.SLIPPAGE_PCT / 100.0
    entry = limit * (1 + slip)
    stop_d  = C.STOP_ATR_MULT * atr_val   # 5.0
    target_d = C.TARGET_ATR_MULT * atr_val  # 8.0
    target = entry + target_d

    sb_a, brk_a, cl_a, at_a, op_a, hi_a, lo_a = _arrays(
        n, price, atr_val, sb_indices=[0],
        lo_override={1: limit - 0.5},          # bar 1 low triggers limit
        hi_override={2: target + 0.5},          # bar 2 high triggers target
    )

    trades = _simulate(None, sb_a, brk_a, cl_a, at_a, op_a, hi_a, lo_a, 0, n)
    assert len(trades) == 1, f"Expected 1 trade, got {len(trades)}"

    exit_eff = target * (1 - slip)             # exit is a win → slip worsens fill
    gross = (exit_eff - entry) / entry
    expected = gross - 2 * C.COMMISSION_PER_TRADE
    assert abs(trades[0] - expected) < 1e-9, (
        f"Commission bug: got {trades[0]:.6f}, expected {expected:.6f}"
    )
```

- [ ] **Step 2: Run test — verify it FAILS**

```bash
cd /Users/bhaviksarvaiya/stfs-eq && python -m pytest test_backtest.py::test_commission_is_fraction_of_notional -v
```

Expected: `FAILED` — the old formula produces a different commission value.

- [ ] **Step 3: Fix `_close` in `_simulate`**

In `battle_card.py`, find the `_close` inner function inside `_simulate` (~line 562):

```python
    def _close(exit_px):
        nonlocal in_trade
        # Slippage tightens the exit fill (we get worse than the level).
        exit_eff = exit_px * (1 - slip) if exit_px >= entry_price else exit_px * (1 + slip)
        gross = (exit_eff - entry_price) / entry_price
        # Round-trip commission as fraction of notional (entry+exit, ~1 share basis).
        comm_pct = (2 * comm) / (entry_price * 100.0) if entry_price > 0 else 0.0
        trades.append(gross - comm_pct)
        in_trade = False
```

Replace with:

```python
    def _close(exit_px):
        nonlocal in_trade
        exit_eff = exit_px * (1 - slip) if exit_px >= entry_price else exit_px * (1 + slip)
        gross = (exit_eff - entry_price) / entry_price
        trades.append(gross - 2 * C.COMMISSION_PER_TRADE)
        in_trade = False
```

Also remove the now-unused `comm = C.COMMISSION_PER_TRADE` line at the top of `_simulate`.

Also update the docstring in `_simulate` to reflect the new model:

```python
    """Replay strong_buy signals from start_i..end_i (exclusive). Returns list of
    net fractional P/L per trade after friction.

    Friction model:
      entry  = limit * (1 + slip)        # pay up on entry
      exit   = level * (1 ± slip)        # cross spread on close
      comm   = 2 * COMMISSION_PER_TRADE  # fraction of notional, round-trip
    """
```

- [ ] **Step 4: Run test — verify PASS**

```bash
python -m pytest test_backtest.py::test_commission_is_fraction_of_notional -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add test_backtest.py battle_card.py
git commit -m "fix: commission in _simulate is now fraction of notional, not dollar/price*100"
```

---

## Task 3: Gap-aware stop simulation

**Files:**
- Modify: `test_backtest.py` — add gap tests
- Modify: `battle_card.py` — `_simulate` in-trade block (~line 575)

- [ ] **Step 1: Add gap tests to `test_backtest.py`**

Append to `test_backtest.py`:

```python
def test_gap_down_stop_fills_at_open():
    """When next open gaps below stop, fill at open — not at stop price."""
    price, atr_val = 50.0, 2.0
    slip = C.SLIPPAGE_PCT / 100.0
    n = 6
    limit = price - C.ENTRY_ATR_MULT * atr_val   # 47.0
    entry = limit * (1 + slip)                    # ~47.024
    stop = entry - C.STOP_ATR_MULT * atr_val      # ~42.024
    gap_open = stop - 2.0                          # 40.024 — clearly below stop

    sb_a, brk_a, cl_a, at_a, op_a, hi_a, lo_a = _arrays(
        n, price, atr_val, sb_indices=[0],
        lo_override={1: limit - 0.5},    # bar 1: enters via limit
        op_override={2: gap_open},        # bar 2: gaps below stop
        lo_override_extra={2: gap_open - 0.5},  # bar 2 low also below (confirms gap)
    )

    trades = _simulate(None, sb_a, brk_a, cl_a, at_a, op_a, hi_a, lo_a, 0, n)
    assert len(trades) == 1

    expected_exit = gap_open * (1 + slip)   # exit < entry → adverse slip
    expected_gross = (expected_exit - entry) / entry
    expected = expected_gross - 2 * C.COMMISSION_PER_TRADE
    assert abs(trades[0] - expected) < 1e-9, (
        f"Gap fill wrong: got {trades[0]:.6f}, expected {expected:.6f} (open fill)"
    )


def test_gap_up_target_fills_at_open():
    """When next open gaps above target, fill at open — not at target price."""
    price, atr_val = 50.0, 2.0
    slip = C.SLIPPAGE_PCT / 100.0
    n = 6
    limit = price - C.ENTRY_ATR_MULT * atr_val   # 47.0
    entry = limit * (1 + slip)
    target = entry + C.TARGET_ATR_MULT * atr_val  # ~55.024
    gap_open = target + 3.0                        # 58.024 — above target

    sb_a, brk_a, cl_a, at_a, op_a, hi_a, lo_a = _arrays(
        n, price, atr_val, sb_indices=[0],
        lo_override={1: limit - 0.5},
        op_override={2: gap_open},
        hi_override={2: gap_open + 0.5},
    )

    trades = _simulate(None, sb_a, brk_a, cl_a, at_a, op_a, hi_a, lo_a, 0, n)
    assert len(trades) == 1

    expected_exit = gap_open * (1 - slip)   # exit > entry → favourable, still slip costs
    expected_gross = (expected_exit - entry) / entry
    expected = expected_gross - 2 * C.COMMISSION_PER_TRADE
    assert abs(trades[0] - expected) < 1e-9
```

Also update `_arrays` helper to support `lo_override_extra` (merge with `lo_override`):

```python
def _arrays(n, price, atr_val, sb_indices, lo_override=None, hi_override=None,
            op_override=None, lo_override_extra=None):
    """Build minimal arrays for _simulate. Limit-order entry (not breakout)."""
    sb_a  = np.zeros(n, dtype=bool)
    for i in sb_indices:
        sb_a[i] = True
    brk_a = np.zeros(n, dtype=bool)
    cl_a  = np.full(n, price, dtype=float)
    at_a  = np.full(n, atr_val, dtype=float)
    op_a  = np.full(n, price, dtype=float)
    hi_a  = np.full(n, price * 1.02, dtype=float)
    lo_a  = np.full(n, price * 0.98, dtype=float)
    for override in (lo_override, lo_override_extra):
        if override:
            for idx, val in override.items():
                lo_a[idx] = val
    if hi_override:
        for idx, val in hi_override.items():
            hi_a[idx] = val
    if op_override:
        for idx, val in op_override.items():
            op_a[idx] = val
    return sb_a, brk_a, cl_a, at_a, op_a, hi_a, lo_a
```

- [ ] **Step 2: Run gap tests — verify FAIL**

```bash
python -m pytest test_backtest.py::test_gap_down_stop_fills_at_open test_backtest.py::test_gap_up_target_fills_at_open -v
```

Expected: both `FAILED`

- [ ] **Step 3: Fix in-trade block in `_simulate`**

Find the in-trade loop body in `_simulate` (~line 575):

```python
        if in_trade:
            if lo_a[i + 1] <= stop_loss:
                _close(stop_loss)
            elif hi_a[i + 1] >= take_profit:
                _close(take_profit)
            continue
```

Replace with:

```python
        if in_trade:
            nxt_op = op_a[i + 1]
            nxt_lo = lo_a[i + 1]
            nxt_hi = hi_a[i + 1]
            if nxt_op <= stop_loss:
                _close(nxt_op)                  # gap-down past stop: fill at open
            elif nxt_op >= take_profit:
                _close(nxt_op)                  # gap-up past target: fill at open
            elif nxt_lo <= stop_loss:
                _close(stop_loss)               # intraday stop touch
            elif nxt_hi >= take_profit:
                _close(take_profit)             # intraday target touch
            continue
```

- [ ] **Step 4: Run all tests — verify PASS**

```bash
python -m pytest test_backtest.py -v
```

Expected: all 3 tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add test_backtest.py battle_card.py
git commit -m "fix: gap-aware stop/target fills in _simulate; fill at open when bar gaps through level"
```

---

## Task 4: Rolling walk-forward + recent-era stats in `run_mini_backtest`

**Files:**
- Modify: `test_backtest.py` — add WFO structure test
- Modify: `battle_card.py` — replace `run_mini_backtest` body

- [ ] **Step 1: Add WFO structure test to `test_backtest.py`**

Append:

```python
def test_run_mini_backtest_returns_fold_structure():
    """run_mini_backtest must return folds list + mean stats + recent window."""
    import yfinance as yf
    from datetime import date, timedelta
    from battle_card import run_mini_backtest
    from indicators import compute_factors

    # Use SPY as a stock and benchmark (simple — always has data)
    start = (date.today() - timedelta(days=800)).isoformat()
    df = yf.download("SPY", start=start, interval="1d", auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df = df["SPY"]
    df = df.dropna()

    if len(df) < 200:
        pytest.skip("Insufficient data for WFO test")

    result = run_mini_backtest(df, df)

    # Must return new keys
    assert "folds" in result, "Missing 'folds' key"
    assert "mean_wr" in result, "Missing 'mean_wr' key"
    assert "std_wr" in result, "Missing 'std_wr' key"
    assert "mean_expR" in result, "Missing 'mean_expR' key"
    assert "std_expR" in result, "Missing 'std_expR' key"
    assert "consistent_folds" in result, "Missing 'consistent_folds' key"
    assert "n_folds_with_data" in result, "Missing 'n_folds_with_data' key"
    assert "recent" in result, "Missing 'recent' key"

    # Backward-compat keys still present
    assert "trades" in result
    assert "win_rate" in result
    assert "expectancy_R" in result
    assert "compounded" in result

    # Folds list length matches BACKTEST_FOLDS
    import config as C
    assert len(result["folds"]) == C.BACKTEST_FOLDS

    # Each fold has trade stats keys
    for fold in result["folds"]:
        assert "trades" in fold
        assert "win_rate" in fold
        assert "expectancy_R" in fold

    # Recent window has correct structure
    assert "trades" in result["recent"]
    assert "win_rate" in result["recent"]
```

- [ ] **Step 2: Run WFO test — verify FAIL**

```bash
python -m pytest test_backtest.py::test_run_mini_backtest_returns_fold_structure -v
```

Expected: `FAILED` — `folds` key not in result

- [ ] **Step 3: Replace `run_mini_backtest` body in `battle_card.py`**

Find the entire `run_mini_backtest` function and replace its body (keep the function signature and the docstring replacement below):

```python
def run_mini_backtest(df, bench_df, factors=None):
    """Anchored walk-forward backtest with BACKTEST_FOLDS test windows.

    Each fold: train [0, test_start), test window slides forward across the
    newest half of the data. Also computes a 'recent' window (BACKTEST_RECENT_BARS)
    so the user can see how the signal behaves in current market conditions.

    Returns backward-compatible top-level keys (win_rate = mean across folds,
    expectancy_R = mean, trades = total) plus new fold/recent detail keys.
    """
    empty_stats = {"trades": 0, "wins": 0, "win_rate": 0.0, "compounded": 0.0, "expectancy_R": 0.0}
    empty = {
        **empty_stats,
        "folds": [], "mean_wr": 0.0, "std_wr": 0.0,
        "mean_expR": 0.0, "std_expR": 0.0,
        "consistent_folds": 0, "n_folds_with_data": 0,
        "recent": empty_stats,
        "train": empty_stats, "test": empty_stats,
    }

    if df is None or bench_df is None or len(df) < max(C.EMA_SLOW, C.WEEKLY_EMA_SLOW * 5, 50):
        return empty

    if factors is None:
        factors = compute_factors(df, bench_df)

    cl, hi, lo, op, at = df["Close"], df["High"], df["Low"], df["Open"], factors["atr"]
    is_breakout = (cl >= cl.rolling(C.BREAKOUT_LOOKBACK).max())
    sb_a  = factors["strong_buy"].values
    brk_a = is_breakout.values
    cl_a, at_a, op_a, hi_a, lo_a = cl.values, at.values, op.values, hi.values, lo.values

    n = len(df)
    n_folds = C.BACKTEST_FOLDS

    # Anchored WFO: anchor covers first half; test windows slide across second half.
    anchor = n // 2
    remaining = n - anchor
    test_size = max(remaining // n_folds, 1)

    fold_stats = []
    all_fold_trades = []
    for k in range(n_folds):
        test_start = anchor + k * test_size
        test_end = (test_start + test_size) if k < n_folds - 1 else n
        if test_start >= n:
            break
        trades = _simulate(df, sb_a, brk_a, cl_a, at_a, op_a, hi_a, lo_a, test_start, test_end)
        fold_stats.append(_stats(trades))
        all_fold_trades.extend(trades)

    # Recent-era window (independent of folds — may overlap)
    recent_start = max(0, n - C.BACKTEST_RECENT_BARS)
    recent_trades = _simulate(df, sb_a, brk_a, cl_a, at_a, op_a, hi_a, lo_a, recent_start, n)
    recent_stats = _stats(recent_trades)

    folds_with_data = [s for s in fold_stats if s["trades"] > 0]
    if folds_with_data:
        wrs   = [s["win_rate"]    for s in folds_with_data]
        exp_rs = [s["expectancy_R"] for s in folds_with_data]
        mean_wr   = float(np.mean(wrs))
        std_wr    = float(np.std(wrs))
        mean_expR = float(np.mean(exp_rs))
        std_expR  = float(np.std(exp_rs))
        consistent = sum(1 for e in exp_rs if e > 0)
    else:
        mean_wr = std_wr = mean_expR = std_expR = 0.0
        consistent = 0

    total_trades = sum(s["trades"] for s in fold_stats)
    total_wins   = sum(s["wins"]   for s in fold_stats)
    comp = float(((1 + pd.Series(all_fold_trades)).prod() - 1) * 100) if all_fold_trades else 0.0

    return {
        # Backward-compat keys — callers that read these still work unchanged
        "trades": total_trades,
        "wins": total_wins,
        "win_rate": mean_wr,
        "compounded": comp,
        "expectancy_R": mean_expR,
        # New detail keys
        "folds": fold_stats,
        "mean_wr": mean_wr,
        "std_wr": std_wr,
        "mean_expR": mean_expR,
        "std_expR": std_expR,
        "consistent_folds": consistent,
        "n_folds_with_data": len(folds_with_data),
        "recent": recent_stats,
        # Kept for any direct callers of .get("train"/.get("test")
        "train": empty_stats,
        "test": {"trades": total_trades, "wins": total_wins,
                 "win_rate": mean_wr, "compounded": comp, "expectancy_R": mean_expR},
    }
```

- [ ] **Step 4: Run all tests — verify PASS**

```bash
python -m pytest test_backtest.py -v
```

Expected: all 4 tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add test_backtest.py battle_card.py
git commit -m "feat: rolling 5-fold walk-forward + recent-era stats in run_mini_backtest"
```

---

## Task 5: Update `_attach_quality` thin-history check

**Files:**
- Modify: `battle_card.py` — `_attach_quality` function (~line 1744)

- [ ] **Step 1: Update thin-history check to use fold stats**

Find in `_attach_quality`:

```python
        thin = r["backtest"]["trades"] < C.THIN_HISTORY_TRADES
```

Replace with:

```python
        folds = r["backtest"].get("folds", [])
        thin = (not folds or
                any(s["trades"] < C.THIN_HISTORY_TRADES for s in folds))
```

- [ ] **Step 2: Smoke-test pipeline runs without error**

```bash
cd /Users/bhaviksarvaiya/stfs-eq && python -c "
import pandas as pd, config as C
from battle_card import run_mini_backtest, _attach_quality
import yfinance as yf
from datetime import date, timedelta
df = yf.download('AAPL', start=(date.today()-timedelta(days=500)).isoformat(), interval='1d', auto_adjust=True, progress=False)
if hasattr(df.columns, 'levels'): df = df['AAPL']
spy = yf.download('SPY', start=(date.today()-timedelta(days=500)).isoformat(), interval='1d', auto_adjust=True, progress=False)
if hasattr(spy.columns, 'levels'): spy = spy['SPY']
from battle_card import score_ticker
r = score_ticker(df.dropna(), spy.dropna())
r['ticker'] = 'AAPL'
_attach_quality([r])
print('quality:', r['quality'], 'thin:', r['thin_history'])
print('folds:', len(r['backtest']['folds']), 'recent trades:', r['backtest']['recent']['trades'])
"
```

Expected: prints quality score, thin flag, fold count = 5, recent trade count.

- [ ] **Step 3: Commit**

```bash
git add battle_card.py
git commit -m "fix: _attach_quality thin-history checks all folds, not just total trades"
```

---

## Task 6: Expose raw indicator values from `compute_factors`

**Files:**
- Modify: `indicators.py` — `compute_factors` (body and return dict)

- [ ] **Step 1: Capture wema series and add to return dict**

In `indicators.py`, find the F2 block:

```python
    # F2: weekly trend (resample, compute, reindex back to daily)
    df_weekly = df.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna()
    if len(df_weekly) >= C.WEEKLY_EMA_SLOW + 2:
        wf = ema(df_weekly["Close"], C.WEEKLY_EMA_FAST)
        ws = ema(df_weekly["Close"], C.WEEKLY_EMA_SLOW)
        df_weekly["f2"] = (df_weekly["Close"] > ws) & (wf > ws)
        f2 = df_weekly["f2"].reindex(df.index).ffill().fillna(False)
    else:
        f2 = pd.Series(False, index=df.index)
```

Replace with:

```python
    # F2: weekly trend (resample W-FRI matches TradingView weekly bar convention)
    df_weekly = df.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna()
    wema_fast_d = pd.Series(np.nan, index=df.index)
    wema_slow_d = pd.Series(np.nan, index=df.index)
    if len(df_weekly) >= C.WEEKLY_EMA_SLOW + 2:
        wf = ema(df_weekly["Close"], C.WEEKLY_EMA_FAST)
        ws = ema(df_weekly["Close"], C.WEEKLY_EMA_SLOW)
        df_weekly["f2"] = (df_weekly["Close"] > ws) & (wf > ws)
        f2 = df_weekly["f2"].reindex(df.index).ffill().fillna(False)
        wema_fast_d = wf.reindex(df.index).ffill()
        wema_slow_d = ws.reindex(df.index).ffill()
    else:
        f2 = pd.Series(False, index=df.index)
```

- [ ] **Step 2: Add new keys to return dict**

Find the `return {` at the bottom of `compute_factors`:

```python
    return {
        "f1": f1, "f2": f2, "f3": f3, "f4": f4,
        "f5": f5, "f6": f6, "f7": f7, "f8": f8,
        "score": score, "trio": trio, "strong_buy": strong_buy,
        "rs_pct": rs_pct, "atr": at, "atr_pct": atr_pct,
        "rsi": rs_s, "adx": adx_s, "hma": hm,
        "bonus_rsi_slope": bonus_rsi_slope.fillna(False),
        "bonus_atr_expansion": bonus_atr_expansion.fillna(False),
        "momentum_bonus": momentum_bonus,
    }
```

Replace with:

```python
    return {
        "f1": f1, "f2": f2, "f3": f3, "f4": f4,
        "f5": f5, "f6": f6, "f7": f7, "f8": f8,
        "score": score, "trio": trio, "strong_buy": strong_buy,
        "rs_pct": rs_pct, "atr": at, "atr_pct": atr_pct,
        "rsi": rs_s, "adx": adx_s, "hma": hm,
        "bonus_rsi_slope": bonus_rsi_slope.fillna(False),
        "bonus_atr_expansion": bonus_atr_expansion.fillna(False),
        "momentum_bonus": momentum_bonus,
        # Raw indicator series — for indicator verification panel in HTML
        "ema_fast": ef, "ema_mid": em, "ema_slow": es,
        "obv_raw": ob, "obv_ema": oe,
        "wema_fast": wema_fast_d, "wema_slow": wema_slow_d,
    }
```

- [ ] **Step 3: Verify `compute_factors` still works**

```bash
python -c "
import yfinance as yf; import pandas as pd
from datetime import date, timedelta
from indicators import compute_factors
df = yf.download('AAPL', start=(date.today()-timedelta(days=400)).isoformat(), interval='1d', auto_adjust=True, progress=False)
if hasattr(df.columns,'levels'): df=df['AAPL']
fac = compute_factors(df.dropna(), df.dropna())
print('ema_fast last:', round(float(fac['ema_fast'].iloc[-1]),2))
print('wema_slow last:', round(float(fac['wema_slow'].iloc[-1]),2))
print('obv_ema last:', round(float(fac['obv_ema'].iloc[-1]),0))
"
```

Expected: three numeric values printed, no errors.

- [ ] **Step 4: Commit**

```bash
git add indicators.py
git commit -m "feat: compute_factors exposes ema/wema/obv series for indicator verification panel"
```

---

## Task 7: Add `raw_indicators` to `score_ticker` return

**Files:**
- Modify: `battle_card.py` — `score_ticker` return dict (~line 711)

- [ ] **Step 1: Extract raw values and add to return**

In `score_ticker`, find the final `return {` statement. Before it, add:

```python
    def _last(series):
        v = series.iloc[-1]
        return None if (v != v) else float(v)   # None for NaN

    raw_indicators = {
        "ema_fast":  _last(fac["ema_fast"]),
        "ema_mid":   _last(fac["ema_mid"]),
        "ema_slow":  _last(fac["ema_slow"]),
        "wema_fast": _last(fac["wema_fast"]),
        "wema_slow": _last(fac["wema_slow"]),
        "obv":       _last(fac["obv_raw"]),
        "obv_ema":   _last(fac["obv_ema"]),
    }
```

Then in the return dict, add `"raw_indicators": raw_indicators,`.

The full return block becomes:

```python
    return {"close": c, "atr": a, "atr_pct": atr_pct, "rsi": rsi_val,
            "adx": adx_val, "rs_pct": rs_val, "factors": factors,
            "score": int(score), "trio_pass": bool(trio), "action": action,
            "is_breakout": bool(c >= cl.iloc[-C.BREAKOUT_LOOKBACK:].max()),
            "backtest": bt_stats,
            "bonus_rsi_slope": bonus_rsi_slope,
            "bonus_atr_expansion": bonus_atr_expansion,
            "momentum_bonus": momentum_bonus,
            "raw_indicators": raw_indicators}
```

- [ ] **Step 2: Smoke-test**

```bash
python -c "
import yfinance as yf; import pandas as pd
from datetime import date, timedelta
from battle_card import score_ticker
df = yf.download('MSFT', start=(date.today()-timedelta(days=400)).isoformat(), interval='1d', auto_adjust=True, progress=False)
if hasattr(df.columns,'levels'): df=df['MSFT']
spy = yf.download('SPY', start=(date.today()-timedelta(days=400)).isoformat(), interval='1d', auto_adjust=True, progress=False)
if hasattr(spy.columns,'levels'): spy=spy['SPY']
r = score_ticker(df.dropna(), spy.dropna())
ri = r['raw_indicators']
print('EMA 8/21/34:', round(ri['ema_fast'],2), '/', round(ri['ema_mid'],2), '/', round(ri['ema_slow'],2))
print('wEMA 10/30:', ri['wema_fast'] and round(ri['wema_fast'],2), '/', ri['wema_slow'] and round(ri['wema_slow'],2))
"
```

Expected: three EMA values and two weekly EMA values printed.

- [ ] **Step 3: Commit**

```bash
git add battle_card.py
git commit -m "feat: score_ticker returns raw_indicators dict for TradingView cross-check panel"
```

---

## Task 8: Data source tracking through `fetch_daily_ohlc` → `main()`

**Files:**
- Modify: `battle_card.py` — `fetch_daily_ohlc`, `main()`

- [ ] **Step 1: Modify `fetch_daily_ohlc` to return `(ohlc, sources)`**

Find `def fetch_daily_ohlc(tickers, lookback_days=1500):` and replace the full function:

```python
def fetch_daily_ohlc(tickers, lookback_days=1500):
    """Returns (ohlc_dict, sources_dict) where sources maps ticker → 'TWS' | 'yf'."""
    if not tickers:
        return {}, {}

    tws_result = {}
    if _tws_connected():
        tws_result = _tws_ohlc(tickers, lookback_days) or {}
        if tws_result:
            print(f"  ✓ OHLC via TWS ({len(tws_result)}/{len(tickers)} tickers)")

    sources = {t: "TWS" for t in tws_result}
    missing = [t for t in tickers if t not in tws_result]
    if not missing:
        return tws_result, sources

    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    try:
        data = yf.download(tickers=missing, start=start, interval="1d",
                           group_by="ticker", auto_adjust=True,
                           progress=False, threads=True)
    except Exception as e:
        print(f"  ⚠  yfinance: {e}")
        return tws_result, sources

    out = dict(tws_result)
    if len(missing) == 1:
        df = data.dropna()
        if not df.empty:
            out[missing[0]] = df
            sources[missing[0]] = "yf"
    else:
        for t in missing:
            try:
                df = data[t].dropna()
                if not df.empty:
                    out[t] = df
                    sources[t] = "yf"
            except Exception:
                pass

    return out, sources
```

- [ ] **Step 2: Update `main()` to unpack the tuple**

In `main()`, find:

```python
    ohlc = fetch_daily_ohlc(all_tickers)
```

Replace with:

```python
    ohlc, data_sources = fetch_daily_ohlc(all_tickers)
```

Then find the Stage 2 scoring loop:

```python
        info = score_ticker(ohlc[t], bench_df, is_benchmark=(t==C.BENCHMARK))
        if "error" in info:
            dropped.append({"ticker":t,"reason":info["error"]}); continue
        info["ticker"] = t
        info["industry"] = profiles.get(t,{}).get("industry","")
        info["earnings_date"] = earnings_map.get(t)
        info["regime"] = regime
        results.append(info)
```

Replace with:

```python
        info = score_ticker(ohlc[t], bench_df, is_benchmark=(t==C.BENCHMARK))
        if "error" in info:
            dropped.append({"ticker":t,"reason":info["error"]}); continue
        info["ticker"] = t
        info["industry"] = profiles.get(t,{}).get("industry","")
        info["earnings_date"] = earnings_map.get(t)
        info["regime"] = regime
        info["data_source"] = data_sources.get(t, "yf")
        results.append(info)
```

- [ ] **Step 3: Smoke-test import**

```bash
python -c "from battle_card import fetch_daily_ohlc; ohlc, src = fetch_daily_ohlc(['SPY'], lookback_days=30); print('source:', src)"
```

Expected: `source: {'SPY': 'yf'}` (or `TWS` if connected)

- [ ] **Step 4: Commit**

```bash
git add battle_card.py
git commit -m "feat: fetch_daily_ohlc returns (ohlc, sources) dict; source threaded through to score results"
```

---

## Task 9: HTML — source badge, indicator panel, new BT display

**Files:**
- Modify: `battle_card.py` — `render_card` function (~line 1268)

- [ ] **Step 1: Add source badge to card header**

In `render_card`, find where `earn_html` is built (the last HTML variable before `header = ...`). After it, add:

```python
    src = r.get("data_source", "yf")
    src_col  = "var(--green)" if src == "TWS" else "var(--amber)"
    src_html = (f"  ·  <span style='background:{src_col};color:#080c12;"
                f"padding:1px 5px;border-radius:3px;font-size:10px;"
                f"font-weight:700'>{src}</span>")
```

Then insert `{src_html}` into the `header` f-string right after `{earn_html}`:

```python
      <span class="tmeta">  {r.get('industry','')}  ·  ${r['close']:.2f}
        · ATR {r['atr']:.2f} ({r['atr_pct']:.1f}%)  · RSI {r['rsi']:.0f}  · ADX {r['adx']:.0f}{bt_html}{q_html}{mb_html}{earn_html}{src_html}</span>
```

- [ ] **Step 2: Replace BT HTML block with new rolling-WFO display**

Find the BT HTML block in `render_card`:

```python
    bt = r.get("backtest", {"trades": 0, "win_rate": 0.0, "compounded": 0.0, "expectancy_R": 0.0})
    if bt["trades"] > 0:
        bt_col = "var(--green)" if bt["win_rate"] >= 60 else ("var(--amber)" if bt["win_rate"] >= 40 else "var(--red)")
        exp_R = bt.get("expectancy_R", 0.0)
        thin_tag = " <span style='color:var(--amber)'>(thin)</span>" if r.get("thin_history") else ""
        train = bt.get("train", {})
        train_tag = ""
        if train.get("trades", 0) > 0:
            # Show train side-by-side so user can spot train→test degradation
            train_tag = (f" <span style='color:var(--muted);font-size:11px'>"
                         f"(train {train['win_rate']:.0f}%/{train.get('expectancy_R',0):+.2f}R "
                         f"n={train['trades']})</span>")
        bt_html = (f"  ·  <span style='color:{bt_col};font-weight:700'>BT-test: "
                   f"{bt['win_rate']:.1f}% Win · {exp_R:+.2f}R · {bt['trades']} trades · "
                   f"{bt['compounded']:.0f}% Ret</span>{thin_tag}{train_tag}")
    else:
        bt_html = "  ·  <span style='color:var(--muted)'>BT: N/A</span>"
```

Replace with:

```python
    bt = r.get("backtest", {})
    thin_tag = " <span style='color:var(--amber)'>(thin)</span>" if r.get("thin_history") else ""
    nf  = bt.get("n_folds_with_data", 0)
    if nf > 0:
        mwr  = bt["mean_wr"]
        swr  = bt.get("std_wr", 0.0)
        mexp = bt["mean_expR"]
        sexp = bt.get("std_expR", 0.0)
        con  = bt.get("consistent_folds", 0)
        bt_col = "var(--green)" if mwr >= 60 else ("var(--amber)" if mwr >= 40 else "var(--red)")
        swr_s  = f"±{swr:.0f}"  if swr  >= 0.5  else ""
        sexp_s = f"±{sexp:.2f}" if sexp >= 0.01 else ""
        bt_html = (f"  ·  <span style='color:{bt_col};font-weight:700'>"
                   f"BT: {mwr:.0f}{swr_s}% WR · {mexp:+.2f}{sexp_s}R · "
                   f"{con}/{nf} folds</span>{thin_tag}")
        rec = bt.get("recent", {})
        if rec.get("trades", 0) > 0:
            rc = "var(--green)" if rec["win_rate"] >= 60 else ("var(--amber)" if rec["win_rate"] >= 40 else "var(--red)")
            bt_html += (f"  ·  <span style='color:{rc};font-size:11px'>"
                        f"1yr: {rec['win_rate']:.0f}% WR · {rec.get('expectancy_R',0):+.2f}R"
                        f" n={rec['trades']}</span>")
    else:
        bt_html = "  ·  <span style='color:var(--muted)'>BT: N/A</span>"
```

- [ ] **Step 3: Add indicator values panel after `fgrid`**

In `render_card`, find the `header` variable that contains `</div>` at its end followed by `<div class="fgrid">{fgrid}</div>`. After `<div class="fgrid">{fgrid}</div>`, add a raw indicator section.

The `header` string ends with:

```python
  <div class="fgrid">{fgrid}</div>"""
```

Replace with:

```python
  <div class="fgrid">{fgrid}</div>
  {_raw_indicators_html(r)}"""
```

Then add this helper function **before** `render_card`:

```python
def _raw_indicators_html(r: dict) -> str:
    """Collapsed indicator values panel for TradingView cross-check."""
    ri = r.get("raw_indicators")
    if not ri:
        return ""
    def _fmt(v, decimals=2):
        return f"{v:.{decimals}f}" if v is not None else "—"
    obv_pct = None
    if ri.get("obv") is not None and ri.get("obv_ema") is not None and ri["obv_ema"] != 0:
        obv_pct = (ri["obv"] - ri["obv_ema"]) / abs(ri["obv_ema"]) * 100
    obv_str = f"{obv_pct:+.1f}%" if obv_pct is not None else "—"
    uid = f"ri-{r.get('ticker','x')}"
    return (
        f'<details style="margin:4px 0;font-size:11px;color:var(--muted)">'
        f'<summary style="cursor:pointer;list-style:none;color:var(--muted)">▶ Raw Indicators</summary>'
        f'<div style="padding:6px 0;display:grid;grid-template-columns:1fr 1fr;gap:2px 16px;'
        f'font-family:var(--font-body);font-size:11px">'
        f'<span>EMA 8/21/34: {_fmt(ri["ema_fast"])} / {_fmt(ri["ema_mid"])} / {_fmt(ri["ema_slow"])}</span>'
        f'<span>RSI(14): {_fmt(r.get("rsi"),1)}</span>'
        f'<span>wEMA 10/30: {_fmt(ri["wema_fast"])} / {_fmt(ri["wema_slow"])}</span>'
        f'<span>ADX(14): {_fmt(r.get("adx"),1)}</span>'
        f'<span>OBV vs EMA: {obv_str}</span>'
        f'<span>ATR%: {_fmt(r.get("atr_pct"),2)}%  RS: {_fmt(r.get("rs_pct"),2)}%</span>'
        f'</div></details>'
    )
```

- [ ] **Step 4: Also update `_order_json` signal block to use new BT keys**

Find in `_order_json`:

```python
        "bt_test_winrate":  bt.get("win_rate"),
        "bt_test_expR":     bt.get("expectancy_R"),
        "bt_test_trades":   bt.get("trades"),
```

Replace with:

```python
        "bt_mean_winrate":  bt.get("mean_wr"),
        "bt_mean_expR":     bt.get("mean_expR"),
        "bt_total_trades":  bt.get("trades"),
        "bt_consistent_folds": bt.get("consistent_folds"),
        "bt_n_folds":       bt.get("n_folds_with_data"),
        "bt_recent_winrate": bt.get("recent", {}).get("win_rate"),
        "bt_recent_expR":    bt.get("recent", {}).get("expectancy_R"),
```

- [ ] **Step 5: Run battle card on a small watchlist to verify HTML renders**

```bash
cd /Users/bhaviksarvaiya/stfs-eq && python battle_card.py NEUTRAL --no-open 2>&1 | tail -20
```

Expected: completes without exception; output file created in `output/`.

- [ ] **Step 6: Open output HTML and visually verify**

- Source badge (green TWS or amber yf) appears in card header
- BT line shows `BT: XX±Xpp% WR · +X.XXR · X/5 folds`  
- 1yr line appears if recent window has trades
- `▶ Raw Indicators` section is present and expands on click

- [ ] **Step 7: Commit**

```bash
git add battle_card.py
git commit -m "feat: HTML source badge, indicator verification panel, rolling-WFO BT display"
```

---

## Task 10: Account settings GUI in launcher

**Files:**
- Modify: `launcher.py` — `_build_ui`, `__init__`, add `_save_accounts` method

- [ ] **Step 1: Add imports to `launcher.py`**

Add `import re` to the imports block (near the top with other stdlib imports):

```python
import re
```

Also add `import config as C` if not already present (check — it may already be imported).

Check:

```bash
grep "^import config\|^from config\|import config" /Users/bhaviksarvaiya/stfs-eq/launcher.py
```

If absent, add `import config as C` after the existing imports.

- [ ] **Step 2: Add account vars to `__init__`**

In `STFSApp.__init__`, after existing `self.xxx_var` declarations, add:

```python
        # Account settings vars — populated in _build_ui
        self.acc_equity_vars   = []
        self.acc_risk_vars     = []
        self.acc_notional_vars = []
        self.acc_status_var    = tk.StringVar(value="")
```

- [ ] **Step 3: Add collapsible account section to `_build_ui`**

In `_build_ui`, find the divider immediately before the regime selector:

```python
        # ── divider ──────────────────────────────────────────────────────────
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=8)

        # ── regime selector ──────────────────────────────────────────────────
```

Insert the account settings section **before** that divider:

```python
        # ── account settings (collapsible) ───────────────────────────────────
        self._acc_expanded = False
        acc_hdr = tk.Frame(self, bg=BG)
        acc_hdr.pack(fill="x", padx=16, pady=(8, 0))
        self._acc_toggle_lbl = tk.Label(
            acc_hdr, text="▶ ACCOUNT SETTINGS", bg=BG, fg=MUTED,
            font=("Courier", 10), cursor="hand2")
        self._acc_toggle_lbl.pack(side="left")
        self._acc_toggle_lbl.bind("<Button-1>", lambda e: self._toggle_accounts())

        self._acc_frame = tk.Frame(self, bg=BG)
        # Not packed initially — toggled on click

        for i, acc in enumerate(C.ACCOUNTS):
            row = tk.Frame(self._acc_frame, bg=BG)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=f"{acc['name']:<8}", bg=BG, fg=TEXT,
                     font=("Courier", 10), width=8).pack(side="left")
            tk.Label(row, text="Equity $", bg=BG, fg=MUTED,
                     font=("Courier", 10)).pack(side="left", padx=(8, 2))
            eq_var = tk.StringVar(value=str(int(acc["equity"])))
            tk.Entry(row, textvariable=eq_var, width=8, bg=BG2, fg=TEXT,
                     insertbackground=TEXT, bd=0, highlightthickness=1,
                     highlightcolor=BORDER, highlightbackground=BORDER,
                     font=("Courier", 11)).pack(side="left")
            tk.Label(row, text="Risk%", bg=BG, fg=MUTED,
                     font=("Courier", 10)).pack(side="left", padx=(8, 2))
            rp_var = tk.StringVar(value=str(acc["risk_pct"]))
            tk.Entry(row, textvariable=rp_var, width=5, bg=BG2, fg=TEXT,
                     insertbackground=TEXT, bd=0, highlightthickness=1,
                     highlightcolor=BORDER, highlightbackground=BORDER,
                     font=("Courier", 11)).pack(side="left")
            tk.Label(row, text="MaxNot%", bg=BG, fg=MUTED,
                     font=("Courier", 10)).pack(side="left", padx=(8, 2))
            mn_var = tk.StringVar(value=str(acc["max_notional_pct"]))
            tk.Entry(row, textvariable=mn_var, width=5, bg=BG2, fg=TEXT,
                     insertbackground=TEXT, bd=0, highlightthickness=1,
                     highlightcolor=BORDER, highlightbackground=BORDER,
                     font=("Courier", 11)).pack(side="left")
            self.acc_equity_vars.append(eq_var)
            self.acc_risk_vars.append(rp_var)
            self.acc_notional_vars.append(mn_var)

        btn_row = tk.Frame(self._acc_frame, bg=BG)
        btn_row.pack(fill="x", pady=(6, 2))
        tk.Button(btn_row, text="Save Accounts", command=self._save_accounts,
                  bg=BG2, fg=CYAN, bd=0, cursor="hand2",
                  activebackground=BG3, activeforeground=CYAN,
                  font=("Courier", 10), padx=8, pady=4).pack(side="right")
        self._acc_status_lbl = tk.Label(btn_row, textvariable=self.acc_status_var,
                                        bg=BG, fg=GREEN, font=("Courier", 10))
        self._acc_status_lbl.pack(side="right", padx=8)
```

- [ ] **Step 4: Add `_toggle_accounts` and `_save_accounts` methods to `STFSApp`**

Add these two methods to the `STFSApp` class (anywhere after `_build_ui`):

```python
    def _toggle_accounts(self):
        if self._acc_expanded:
            self._acc_frame.pack_forget()
            self._acc_toggle_lbl.config(text="▶ ACCOUNT SETTINGS")
        else:
            self._acc_frame.pack(fill="x", padx=16, pady=(0, 4),
                                 before=self._acc_toggle_lbl.master.winfo_children()[0]
                                 if False else None)
            # Simple: pack after the header row
            self._acc_frame.pack(fill="x", padx=16, pady=(0, 4))
            self._acc_toggle_lbl.config(text="▼ ACCOUNT SETTINGS")
        self._acc_expanded = not self._acc_expanded

    def _save_accounts(self):
        cfg_path = SCRIPT_DIR / "config.py"
        values = []
        for i, acc in enumerate(C.ACCOUNTS):
            name = acc["name"]
            try:
                eq = float(self.acc_equity_vars[i].get())
                rp = float(self.acc_risk_vars[i].get())
                mn = float(self.acc_notional_vars[i].get())
            except ValueError:
                self.acc_status_var.set(f"{name}: invalid number")
                self._acc_status_lbl.config(fg=RED)
                return
            if eq <= 0:
                self.acc_status_var.set(f"{name}: equity must be > 0")
                self._acc_status_lbl.config(fg=RED)
                return
            if not (0 < rp <= 10):
                self.acc_status_var.set(f"{name}: risk% must be 0–10")
                self._acc_status_lbl.config(fg=RED)
                return
            if not (0 < mn <= 50):
                self.acc_status_var.set(f"{name}: max notional% must be 0–50")
                self._acc_status_lbl.config(fg=RED)
                return
            values.append((name, eq, rp, mn))

        rows = ",\n".join(
            f'    {{"name": "{name}", "equity": {eq:.0f}, "risk_pct": {rp}, "max_notional_pct": {mn}}}'
            for name, eq, rp, mn in values
        )
        new_block = f"ACCOUNTS = [\n{rows},\n]"

        text = cfg_path.read_text()
        new_text = re.sub(
            r"^ACCOUNTS\s*=\s*\[.*?^]",
            new_block,
            text,
            flags=re.MULTILINE | re.DOTALL,
        )
        if new_text == text:
            self.acc_status_var.set("No change detected")
            self._acc_status_lbl.config(fg=MUTED)
            return

        tmp = cfg_path.with_suffix(".py.tmp")
        tmp.write_text(new_text)
        tmp.replace(cfg_path)

        self.acc_status_var.set("Saved ✓")
        self._acc_status_lbl.config(fg=GREEN)
        self.after(3000, lambda: self.acc_status_var.set(""))
```

Note: `_toggle_accounts` packs `_acc_frame` after the header row. Because tkinter `pack` ordering is positional, this works correctly as long as `_acc_frame` is packed and unpacked via `pack_forget`. Simplify the method body:

```python
    def _toggle_accounts(self):
        if self._acc_expanded:
            self._acc_frame.pack_forget()
            self._acc_toggle_lbl.config(text="▶ ACCOUNT SETTINGS")
        else:
            self._acc_frame.pack(fill="x", padx=16, pady=(0, 4))
            self._acc_toggle_lbl.config(text="▼ ACCOUNT SETTINGS")
        self._acc_expanded = not self._acc_expanded
```

- [ ] **Step 5: Launch the GUI and test**

```bash
cd /Users/bhaviksarvaiya/stfs-eq && python launcher.py
```

Manual checks:
1. Click "▶ ACCOUNT SETTINGS" — panel expands showing 3 rows of fields
2. Click again — panel collapses
3. Expand, change Borg equity to 25000, click Save Accounts
4. Verify "Saved ✓" appears then fades
5. Open `config.py` — verify Borg equity is now `25000`
6. Change it back to 20000 in the GUI and Save

- [ ] **Step 6: Run all tests to confirm no regressions**

```bash
python -m pytest test_backtest.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 7: Commit**

```bash
git add launcher.py test_backtest.py
git commit -m "feat: account settings collapsible panel in launcher; saves equity/risk_pct/max_notional_pct to config.py"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| Account settings GUI | Task 10 |
| Saves to config.py atomically | Task 10 `_save_accounts` |
| Weekly bar W-FRI (already correct) | Noted in Task 6 — no change needed |
| Data source badge TWS/yf | Task 8 + Task 9 |
| Raw indicator values panel | Task 7 + Task 9 |
| Commission formula fix | Task 2 |
| Gap-aware stop/target | Task 3 |
| Rolling 5-fold WFO | Task 4 |
| Recent-era 252-bar stats | Task 4 |
| Thin-history uses folds | Task 5 |
| Config knobs | Task 1 |

All spec requirements covered.

**Placeholder scan:** No TBD, TODO, or vague steps found.

**Type consistency:**
- `_simulate` signature unchanged — tasks 2 and 3 only modify internals
- `fetch_daily_ohlc` return changes from `dict` to `(dict, dict)` — Task 8 updates the sole caller in `main()`
- `run_mini_backtest` backward-compat keys preserved — `_attach_quality` and HTML callers continue to work; new keys additive
- `compute_factors` return dict gets new keys — additive, no existing key renamed
- `raw_indicators` dict key names match exactly between Task 7 (built in `score_ticker`) and Task 9 (read in `_raw_indicators_html`)
