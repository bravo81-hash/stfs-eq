# test_backtest.py
"""Pytest tests for backtest simulation correctness."""
import numpy as np
import pandas as pd
import pytest
import config as C
from battle_card import _simulate, _stats


def _arrays(n, price, atr_val, sb_indices, lo_override=None, hi_override=None,
            op_override=None, lo_override_extra=None):
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


def test_commission_is_fraction_of_notional():
    """Commission should be 2 * COMMISSION_PER_TRADE, not divided by price*100."""
    price, atr_val = 50.0, 2.0
    # signal at bar 0; limit = 50 - 1.5*2 = 47.0
    # bar 1: low <= 47 → enters at 47*(1+slip)
    # bar 2: high >= target → exits at target*(1-slip)
    n = 5
    limit = price - C.ENTRY_ATR_MULT * atr_val  # 47.0
    slip = C.SLIPPAGE_PCT / 100.0
    entry = limit * (1 + slip)
    target_d = C.TARGET_ATR_MULT * atr_val  # 8.0
    target = entry + target_d

    sb_a, brk_a, cl_a, at_a, op_a, hi_a, lo_a = _arrays(
        n + 1, price, atr_val, sb_indices=[0],
        lo_override={2: limit - 0.5},          # bar 2 low triggers limit (checked when i=1)
        hi_override={3: target + 0.5},         # bar 3 high triggers target (checked when i=2)
    )

    df = pd.DataFrame(index=range(n + 1))  # +1 because loop checks len(df)-1
    trades = _simulate(df, sb_a, brk_a, cl_a, at_a, op_a, hi_a, lo_a, 0, n)
    assert len(trades) == 1, f"Expected 1 trade, got {len(trades)}"

    exit_eff = target * (1 - slip)             # exit is a win → slip worsens fill
    gross = (exit_eff - entry) / entry
    expected = gross - 2 * C.COMMISSION_PER_TRADE
    assert abs(trades[0] - expected) < 1e-9, (
        f"Commission bug: got {trades[0]:.6f}, expected {expected:.6f}"
    )


def test_gap_down_stop_fills_at_open():
    """When bar opens below stop-loss, fill at open price (not at stop price)."""
    price, atr_val = 100.0, 2.0
    # Signal at bar 0
    # Limit = 100 - 1.5*2 = 97.0
    # Entry at bar 2 (checked when i=1): enters at 97*(1+slip)
    # Bar 3 (checked when i=2): opens way below stop → fill at gap_open
    slip = C.SLIPPAGE_PCT / 100.0

    limit = price - C.ENTRY_ATR_MULT * atr_val  # 97.0
    entry = limit * (1 + slip)
    stop_d = C.STOP_ATR_MULT * atr_val  # 5.0
    stop_loss = entry - stop_d

    gap_open = 90.0  # Opens below stop
    n = 5

    sb_a, brk_a, cl_a, at_a, op_a, hi_a, lo_a = _arrays(
        n, price, atr_val, sb_indices=[0],
        lo_override={2: limit - 0.5},       # Bar 2 low triggers limit (checked when i=1)
        op_override={3: gap_open},          # Bar 3 opens below stop
        lo_override_extra={3: gap_open - 0.5},  # Ensure low is also below
    )

    df = pd.DataFrame(index=range(n))
    trades = _simulate(df, sb_a, brk_a, cl_a, at_a, op_a, hi_a, lo_a, 0, n - 1)
    assert len(trades) == 1, f"Expected 1 trade, got {len(trades)}"

    # Gap-down exit: fill at gap_open with adverse slippage
    exit_eff = gap_open * (1 + slip)
    gross = (exit_eff - entry) / entry
    expected = gross - 2 * C.COMMISSION_PER_TRADE
    assert abs(trades[0] - expected) < 1e-9, (
        f"Gap-down: got {trades[0]:.6f}, expected {expected:.6f}"
    )


def test_gap_up_target_fills_at_open():
    """When bar opens above take-profit, fill at open price (not at target price)."""
    price, atr_val = 100.0, 2.0
    # Signal at bar 0
    # Limit = 100 - 1.5*2 = 97.0
    # Entry at bar 2 (checked when i=1): enters at 97*(1+slip)
    # Bar 3 (checked when i=2): opens way above target → fill at gap_open
    slip = C.SLIPPAGE_PCT / 100.0

    limit = price - C.ENTRY_ATR_MULT * atr_val  # 97.0
    entry = limit * (1 + slip)
    target_d = C.TARGET_ATR_MULT * atr_val  # 8.0
    take_profit = entry + target_d

    gap_open = 110.0  # Opens above target
    n = 5

    sb_a, brk_a, cl_a, at_a, op_a, hi_a, lo_a = _arrays(
        n, price, atr_val, sb_indices=[0],
        lo_override={2: limit - 0.5},       # Bar 2 low triggers limit (checked when i=1)
        op_override={3: gap_open},          # Bar 3 opens above target
        hi_override={3: gap_open + 0.5},    # Ensure high is also above
    )

    df = pd.DataFrame(index=range(n))
    trades = _simulate(df, sb_a, brk_a, cl_a, at_a, op_a, hi_a, lo_a, 0, n - 1)
    assert len(trades) == 1, f"Expected 1 trade, got {len(trades)}"

    # Gap-up exit: fill at gap_open with favorable slippage
    exit_eff = gap_open * (1 - slip)
    gross = (exit_eff - entry) / entry
    expected = gross - 2 * C.COMMISSION_PER_TRADE
    assert abs(trades[0] - expected) < 1e-9, (
        f"Gap-up: got {trades[0]:.6f}, expected {expected:.6f}"
    )


def test_run_mini_backtest_returns_fold_structure():
    """run_mini_backtest must return folds list + mean stats + recent window."""
    import yfinance as yf
    from datetime import date, timedelta
    from battle_card import run_mini_backtest

    start = (date.today() - timedelta(days=800)).isoformat()
    df = yf.download("SPY", start=start, interval="1d", auto_adjust=True, progress=False)

    # Handle MultiIndex columns from yfinance
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)

    df = df.dropna()

    if len(df) < 200:
        pytest.skip("Insufficient data for WFO test")

    result = run_mini_backtest(df, df)

    # New keys
    assert "folds" in result, "Missing 'folds' key"
    assert "mean_wr" in result, "Missing 'mean_wr' key"
    assert "std_wr" in result, "Missing 'std_wr' key"
    assert "mean_expR" in result, "Missing 'mean_expR' key"
    assert "std_expR" in result, "Missing 'std_expR' key"
    assert "consistent_folds" in result, "Missing 'consistent_folds' key"
    assert "n_folds_with_data" in result, "Missing 'n_folds_with_data' key"
    assert "recent" in result, "Missing 'recent' key"

    # Backward-compat keys
    assert "trades" in result
    assert "win_rate" in result
    assert "expectancy_R" in result
    assert "compounded" in result

    # Fold count matches config
    import config as C
    assert len(result["folds"]) == C.BACKTEST_FOLDS

    # Each fold has required keys
    for fold in result["folds"]:
        assert "trades" in fold
        assert "win_rate" in fold
        assert "expectancy_R" in fold

    # Recent window structure
    assert "trades" in result["recent"]
    assert "win_rate" in result["recent"]
