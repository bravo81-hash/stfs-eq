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
    # bar 1: low <= 47 → enters at 47*(1+slip)
    # bar 2: high >= target → exits at target*(1-slip)
    n = 5
    limit = price - C.ENTRY_ATR_MULT * atr_val  # 47.0
    slip = C.SLIPPAGE_PCT / 100.0
    entry = limit * (1 + slip)
    stop_d  = C.STOP_ATR_MULT * atr_val   # 5.0
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
