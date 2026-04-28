"""
test_phase4.py — Unit tests for Phase 4 trailing stop + portfolio manager logic.
No TWS connection required — all pure logic.
"""
import sys
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, ".")


# ── Task 2: indicators ────────────────────────────────────────────────────────

def _make_ohlcv(n=200, start=100.0, drift=0.001):
    """Synthetic OHLCV DataFrame for testing."""
    import random
    random.seed(42)
    prices = [start]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + drift + random.uniform(-0.01, 0.01)))
    closes = pd.Series(prices)
    df = pd.DataFrame({
        "Open":   closes * 0.999,
        "High":   closes * 1.005,
        "Low":    closes * 0.995,
        "Close":  closes,
        "Volume": pd.Series([1_000_000] * n),
    })
    df.index = pd.date_range("2023-01-01", periods=n, freq="B")
    return df


def test_trail_ma_key_present_in_compute_factors():
    """compute_factors() must return 'trail_ma' key after Task 2."""
    from indicators import compute_factors
    df = _make_ohlcv(200)
    bench = _make_ohlcv(200)
    fac = compute_factors(df, bench)
    assert "trail_ma" in fac, "trail_ma missing from compute_factors output"
    assert len(fac["trail_ma"]) == len(df)
    assert fac["trail_ma"].notna().sum() > 0


def test_trail_ma_ema_type():
    """With TRAIL_MA_TYPE='EMA', trail_ma should match ema(close, TRAIL_MA_LEN)."""
    import config as C
    from indicators import compute_factors, ema
    df = _make_ohlcv(200)
    bench = _make_ohlcv(200)
    fac = compute_factors(df, bench)
    if C.TRAIL_MA_TYPE == "EMA":
        expected = ema(df["Close"], C.TRAIL_MA_LEN)
        pd.testing.assert_series_equal(
            fac["trail_ma"].reset_index(drop=True),
            expected.reset_index(drop=True),
            check_names=False,
        )


# ── Task 3: orderRef in journal ───────────────────────────────────────────────

def test_orderref_format():
    """orderRef must start with STFS_ORDER_REF_PREFIX and end with a timestamp int."""
    import config as C
    import time
    before = int(time.time())
    ref = f"{C.STFS_ORDER_REF_PREFIX}{int(time.time())}"
    after = int(time.time())
    assert ref.startswith(C.STFS_ORDER_REF_PREFIX)
    ts = int(ref[len(C.STFS_ORDER_REF_PREFIX):])
    assert before <= ts <= after


def test_journal_accepts_orderref_fields(tmp_path, monkeypatch):
    """journal.append_entry must write orderRef and stop_order_id in order dict."""
    import json
    journal_file = tmp_path / "trade_journal.jsonl"
    monkeypatch.setattr("journal._JOURNAL_PATH", journal_file)
    import journal
    journal.append_entry(
        event="entry",
        ticker="AAPL",
        account="Borg",
        signal={"score": 7},
        order={
            "type": "shares",
            "shares": 10,
            "entry": 150.0,
            "stop": 145.0,
            "target": 162.0,
            "order_ids": [100, 101, 102],
            "orderRef": "STFS-EQ-1234567890",
            "stop_order_id": 102,
            "atr": 2.5,
            "entry_price": 150.0,
        },
    )
    lines = journal_file.read_text().strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["order"]["orderRef"] == "STFS-EQ-1234567890"
    assert entry["order"]["stop_order_id"] == 102
    assert entry["order"]["atr"] == 2.5


# ── Task 4: _simulate trailing stop ──────────────────────────────────────────

def test_trailing_stop_raises_stop_before_pullback(monkeypatch):
    """
    Trailing stop raises stop_loss before a pullback, improving exit vs static stop.

    Setup (slip=0, commission=0 for exact arithmetic):
      close[0]=100, ATR[0]=2 → p_limit=97 (ENTRY_MULT=1.5), p_stop_d=5 (STOP_MULT=2.5),
      trail_trigger=1.0*5=5, initial stop=92, target=105
      Entry fills bar 1: lo[1]=96 <= 97
      Bar 2: hi[2]=103 >= 97+5=102 → trailing activates; trail_ma[2]=94 < 92? No → stop stays 92
      Bar 3: hi[3]=103; trail_ma[3]=96 > 92 → stop moves to 96
      Bar 4: lo[4]=94 <= 96 → stop hit at 96
      P&L = (96 - 97) / 97 ≈ -1.03%   (static stop would give (92-97)/97 = -5.15%)
    """
    import config as C
    monkeypatch.setattr(C, "SLIPPAGE_PCT", 0.0)
    monkeypatch.setattr(C, "COMMISSION_PER_TRADE", 0.0)
    monkeypatch.setattr(C, "TRAIL_ACTIVATE_R", 1.0)

    from battle_card import _simulate

    n = 10
    cl  = np.array([100.0, 97.0, 103.0, 103.0,  97.0,  97.0,  97.0,  97.0,  97.0,  97.0])
    hi  = np.array([101.0, 98.0, 104.0, 104.0,  98.0,  98.0,  98.0,  98.0,  98.0,  98.0])
    lo  = np.array([ 96.0, 96.0, 102.5, 102.5,  94.0,  94.0,  94.0,  94.0,  94.0,  94.0])
    op  = np.array([100.0, 97.0, 103.0, 103.0,  97.0,  97.0,  97.0,  97.0,  97.0,  97.0])
    at  = np.array([  2.0,  2.0,   2.0,   2.0,   2.0,   2.0,   2.0,   2.0,   2.0,   2.0])
    tm  = np.array([ 88.0, 90.0,  94.0,  96.0,  96.0,  96.0,  96.0,  96.0,  96.0,  96.0])

    sb  = np.array([True] + [False] * (n - 1))
    brk = np.array([False] * n)

    df = pd.DataFrame({"Close": cl, "High": hi, "Low": lo, "Open": op})

    trades = _simulate(df, sb, brk, cl, at, op, hi, lo, tm, 0, n)

    assert len(trades) == 1, f"Expected 1 trade, got {len(trades)}"
    assert trades[0] > -0.02, f"Expected P&L > -2% (trail-stopped), got {trades[0]:.4f}"
    assert trades[0] < 0.0,   f"Expected small loss (trade stopped out), got {trades[0]:.4f}"


def test_trailing_does_not_activate_before_1r(monkeypatch):
    """If price never reaches entry + trail_trigger, stop stays at initial stop."""
    import config as C
    monkeypatch.setattr(C, "SLIPPAGE_PCT", 0.0)
    monkeypatch.setattr(C, "COMMISSION_PER_TRADE", 0.0)
    monkeypatch.setattr(C, "TRAIL_ACTIVATE_R", 1.0)

    from battle_card import _simulate

    n = 10
    cl  = np.array([100.0, 97.0, 101.0, 101.0,  91.0,  91.0,  91.0,  91.0,  91.0,  91.0])
    hi  = np.array([101.0, 98.0, 101.9, 101.9,  92.5,  92.5,  92.5,  92.5,  92.5,  92.5])
    lo  = np.array([ 96.0, 96.0, 100.5, 100.5,  91.5,  91.5,  91.5,  91.5,  91.5,  91.5])
    op  = np.array([100.0, 97.0, 101.0, 101.0,  91.0,  91.0,  91.0,  91.0,  91.0,  91.0])
    at  = np.array([  2.0,  2.0,   2.0,   2.0,   2.0,   2.0,   2.0,   2.0,   2.0,   2.0])
    tm  = np.array([ 88.0, 90.0,  97.0,  98.0,  99.0,  99.0,  99.0,  99.0,  99.0,  99.0])

    sb  = np.array([True] + [False] * (n - 1))
    brk = np.array([False] * n)

    df = pd.DataFrame({"Close": cl, "High": hi, "Low": lo, "Open": op})

    trades = _simulate(df, sb, brk, cl, at, op, hi, lo, tm, 0, n)

    assert len(trades) == 1
    expected = (92.0 - 97.0) / 97.0
    assert abs(trades[0] - expected) < 0.001, f"Expected ~{expected:.4f}, got {trades[0]:.4f}"


# ── Task 6: trailing_stop_manager pure helpers ────────────────────────────────

def test_market_open_weekday_inside_hours(monkeypatch):
    """Market should be open on a Tuesday at 10:00 ET."""
    from zoneinfo import ZoneInfo
    import datetime as dt
    ET = ZoneInfo("America/New_York")

    class _FakeDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 4, 28, 10, 0, 0, tzinfo=ET)  # Tuesday

    monkeypatch.setattr("trailing_stop_manager.datetime", _FakeDatetime)
    import trailing_stop_manager as tsm
    assert tsm._market_open() is True


def test_market_closed_on_weekend(monkeypatch):
    from zoneinfo import ZoneInfo
    import datetime as dt
    ET = ZoneInfo("America/New_York")

    class _FakeDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 4, 26, 10, 0, 0, tzinfo=ET)  # Sunday

    monkeypatch.setattr("trailing_stop_manager.datetime", _FakeDatetime)
    import trailing_stop_manager as tsm
    assert tsm._market_open() is False


def test_compute_trail_stop_ratchets_up():
    """_compute_trail_stop raises stop when MA > current_stop after activation."""
    import trailing_stop_manager as tsm
    # Not yet activated: price < entry + trigger
    new_stop, activated = tsm._compute_trail_stop(
        price=101.0, entry_price=97.0, trail_trigger=5.0,
        current_stop=92.0, trail_ma=95.0, trailing_active=False,
    )
    assert activated is False
    assert new_stop == 92.0

    # Activated: price >= entry + trigger, MA > current_stop
    new_stop, activated = tsm._compute_trail_stop(
        price=103.0, entry_price=97.0, trail_trigger=5.0,
        current_stop=92.0, trail_ma=95.0, trailing_active=False,
    )
    assert activated is True
    assert new_stop == 95.0

    # Already active, MA rose further
    new_stop, activated = tsm._compute_trail_stop(
        price=105.0, entry_price=97.0, trail_trigger=5.0,
        current_stop=95.0, trail_ma=97.0, trailing_active=True,
    )
    assert activated is True
    assert new_stop == 97.0

    # Already active, MA dipped below current stop — do NOT lower stop
    new_stop, activated = tsm._compute_trail_stop(
        price=104.0, entry_price=97.0, trail_trigger=5.0,
        current_stop=97.0, trail_ma=94.0, trailing_active=True,
    )
    assert activated is True
    assert new_stop == 97.0  # unchanged


# ── Task 7: portfolio_manager pure helpers ────────────────────────────────────

def test_exit_signal_price_target_hit():
    import portfolio_manager as pm
    triggered, reason = pm._signal_price(underlying=196.0, target=195.0, stop=180.0)
    assert triggered is True
    assert "target" in reason.lower()


def test_exit_signal_price_stop_hit():
    import portfolio_manager as pm
    triggered, reason = pm._signal_price(underlying=179.0, target=195.0, stop=180.0)
    assert triggered is True
    assert "stop" in reason.lower()


def test_exit_signal_price_hold():
    import portfolio_manager as pm
    triggered, reason = pm._signal_price(underlying=188.0, target=195.0, stop=180.0)
    assert triggered is False


def test_exit_signal_pnl_debit_at_target():
    """Long debit: up 150% → CLOSE."""
    import portfolio_manager as pm
    # cost_basis = net_debit * 100 * contracts = 4.0 * 100 * 2 = 800
    # current_val = mark * 100 * contracts = 10.0 * 100 * 2 = 2000
    # unrealized = (2000 - 800) / 800 = 1.50 → triggered
    triggered, reason = pm._signal_pnl(
        structure="long_call",
        mark=10.0, net_debit=4.0, net_credit=None,
        max_loss_per_contract=400.0, contracts=2,
    )
    assert triggered is True
    assert "150%" in reason or "gain" in reason.lower()


def test_exit_signal_pnl_credit_at_target():
    """Credit spread: 50% profit → CLOSE."""
    import portfolio_manager as pm
    # max_credit = net_credit * 100 * contracts = 1.80 * 100 * 1 = 180
    # current mark (net) = 0.90 → current cost = 0.90 * 100 = 90
    # profit_taken = 180 - 90 = 90 >= 0.5 * 180 = 90 → triggered
    triggered, reason = pm._signal_pnl(
        structure="credit_spread",
        mark=0.90, net_debit=None, net_credit=1.80,
        max_loss_per_contract=320.0, contracts=1,
    )
    assert triggered is True


def test_exit_signal_pnl_stop_hit():
    """Any structure down >= OPT_PNL_STOP_PCT → CLOSE."""
    import portfolio_manager as pm
    # cost_basis = 4.0 * 100 * 1 = 400; current = 0.80 * 100 = 80
    # unrealized = (80 - 400) / 400 = -0.80 == -OPT_PNL_STOP_PCT → triggered
    triggered, reason = pm._signal_pnl(
        structure="long_call",
        mark=0.80, net_debit=4.0, net_credit=None,
        max_loss_per_contract=400.0, contracts=1,
    )
    assert triggered is True
    assert "stop" in reason.lower() or "loss" in reason.lower()


def test_exit_signal_dte_credit_below_threshold():
    """Credit spread at DTE=20 (<=21) → CLOSE."""
    import portfolio_manager as pm
    triggered, reason = pm._signal_dte(structure="credit_spread", dte=20)
    assert triggered is True
    assert "DTE" in reason


def test_exit_signal_dte_debit_hold():
    """Debit spread at DTE=15 (>14) → HOLD."""
    import portfolio_manager as pm
    triggered, reason = pm._signal_dte(structure="debit_spread", dte=15)
    assert triggered is False


def test_exit_signal_dte_debit_at_threshold():
    """Long call at DTE=14 (<=14) → CLOSE."""
    import portfolio_manager as pm
    triggered, reason = pm._signal_dte(structure="long_call", dte=14)
    assert triggered is True
