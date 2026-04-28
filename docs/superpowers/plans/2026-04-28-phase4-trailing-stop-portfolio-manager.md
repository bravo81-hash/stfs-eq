# Phase 4: Trailing Stop Manager + Portfolio Manager — Implementation Plan

## Current Status (2026-04-28)

- [x] **Web Dashboard Core**: Flask server running on port 5001.
- [x] **Launchpad**: Battle Card generation and viewing integrated.
- [x] **Portfolio Manager**: Live position monitoring with advisory signals.
- [x] **Tools & Settings**: Backtesting, Journaling, and Account Sizing integrated into Web UI.
- [x] **Journal Syncing**: Moved `trade_journal.jsonl` to `data/` to ensure cross-machine syncing.
- [x] **Task 1: config.py — New Constants**
- [x] **Task 2: indicators.py — Trail MA in compute_factors()**

### Outstanding Issues
- **Live Portfolio Polling**: Some users report "STALE" data even during market hours. Investigation into `reqMktData` snapshots and qualification is ongoing.
- **Analytics Visualization**: The Analytics tab currently runs a script but doesn't yet render charts (e.g., Chart.js).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automated trailing stop management for live IBKR equity positions and a read-only portfolio manager that identifies and flags STFS-EQ options positions for exit.

**Architecture:** Two standalone tools (`trailing_stop_manager.py` clientId=17, `portfolio_manager.py` clientId=18) sharing a journal-linked orderRef tagging scheme applied to every order placed by `order_server.py`. Trailing stop logic is also integrated into both backtest sim loops so it can be backtested before going live.

**Tech Stack:** Python 3.11, ib_insync, pandas/numpy, zoneinfo (stdlib), existing indicators.py primitives.

---

## File Map

| File | Action | Change |
|------|--------|--------|
| `config.py` | Modify | 10 new constants at end of file |
| `indicators.py` | Modify | Add `trail_ma` key to `compute_factors()` return dict |
| `journal.py` | Modify | No signature change — new fields flow through `order` dict |
| `order_server.py` | Modify | Set orderRef on all legs; pass stop_order_id + atr + entry_price in journal call |
| `battle_card.py` | Modify | `_simulate()`: new `trail_ma_a` param + trailing state; update both call sites |
| `backtest.py` | Modify | Compute trail MA after indicators; add trailing state to sim loop |
| `trailing_stop_manager.py` | Create | Daemon: poll → compute trail MA → modify+transmit stop orders |
| `portfolio_manager.py` | Create | Advisory: fetch options positions → cross-ref journal → print exit signals |
| `test_phase4.py` | Create | Unit tests for all pure logic (no TWS required) |

---

## Task 1: config.py — New Constants

**Files:**
- Modify: `config.py` (after line 258, end of file)

- [ ] **Step 1: Add constants**

Append to the end of `config.py`:

```python
# =====================================================================
# TRAILING STOP MANAGER (trailing_stop_manager.py — clientId=17)
# =====================================================================
TRAIL_MA_TYPE         = "EMA"   # "EMA" or "HMA" — which MA the stop trails
TRAIL_MA_LEN          = 10      # lookback bars for trail MA
TRAIL_ACTIVATE_R      = 1.0     # profit in R-multiples before trailing activates
                                 # 1.0 = full risk distance (STOP_ATR_MULT × ATR)
TRAIL_POLL_INTERVAL   = 300     # seconds between polls (5 min)
TWS_TRAIL_CLIENT      = 17      # clientId — must not clash with 15/16/18

# =====================================================================
# PORTFOLIO MANAGER (portfolio_manager.py — clientId=18)
# =====================================================================
OPT_DTE_EXIT_CREDIT   = 21      # flag credit spreads / diagonal front at or below this DTE
OPT_DTE_EXIT_DEBIT    = 14      # flag long calls / debit spreads at or below this DTE
OPT_PNL_STOP_PCT      = 0.80    # flag if position is down >= 80% of max loss
TWS_PORTFOLIO_CLIENT  = 18      # clientId — read-only
STFS_ORDER_REF_PREFIX = "STFS-EQ-"  # prefix on orderRef for all placed orders
```

- [ ] **Step 2: Verify import**

```bash
cd /Users/macb/stfs-eq && python3 -c "import config as C; print(C.TRAIL_MA_LEN, C.OPT_DTE_EXIT_CREDIT, C.STFS_ORDER_REF_PREFIX)"
```

Expected output: `10 21 STFS-EQ-`

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "feat(config): add Phase 4 constants for trailing stop and portfolio manager"
```

---

## Task 2: indicators.py — Trail MA in compute_factors()

**Files:**
- Modify: `indicators.py:126-152`
- Create: `test_phase4.py` (initial skeleton)

- [ ] **Step 1: Write failing test**

Create `test_phase4.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /Users/macb/stfs-eq && python3 -m pytest test_phase4.py::test_trail_ma_key_present_in_compute_factors -v
```

Expected: `FAILED` — `AssertionError: trail_ma missing from compute_factors output`

- [ ] **Step 3: Add trail_ma to compute_factors()**

In `indicators.py`, after line 133 (after `momentum_bonus` calculation) and before the `return {` at line 135, add:

```python
    # Trail MA — used by trailing_stop_manager.py and both sim loops
    if C.TRAIL_MA_TYPE == "HMA":
        trail_ma = hma(cl, C.TRAIL_MA_LEN)
    else:
        trail_ma = ema(cl, C.TRAIL_MA_LEN)
```

Then add `"trail_ma": trail_ma,` to the return dict at line 135. The full return dict becomes:

```python
    return {
        "f1": f1, "f2": f2, "f3": f3, "f4": f4,
        "f5": f5, "f6": f6, "f7": f7, "f8": f8,
        "score": score, "trio": trio, "strong_buy": strong_buy,
        "rs_pct": rs_pct, "atr": at, "atr_pct": atr_pct,
        "rsi": rs_s, "adx": adx_s, "hma": hm,
        "trail_ma": trail_ma,
        "bonus_rsi_slope": bonus_rsi_slope.fillna(False),
        "bonus_atr_expansion": bonus_atr_expansion.fillna(False),
        "momentum_bonus": momentum_bonus,
        # Raw indicator series — for indicator verification panel in HTML
        "ema_fast": ef,
        "ema_mid": em,
        "ema_slow": es,
        "obv_raw": ob,
        "obv_ema": oe,
        "wema_fast": wema_fast_d,
        "wema_slow": wema_slow_d,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/macb/stfs-eq && python3 -m pytest test_phase4.py::test_trail_ma_key_present_in_compute_factors test_phase4.py::test_trail_ma_ema_type -v
```

Expected: both `PASSED`

- [ ] **Step 5: Commit**

```bash
git add indicators.py test_phase4.py
git commit -m "feat(indicators): add trail_ma to compute_factors() output"
```

---

## Task 3: order_server.py + journal.py — orderRef Tagging

**Files:**
- Modify: `order_server.py:1-130` (shares bracket), `order_server.py:135-258` (options)
- No signature change to `journal.py` — new fields go inside the `order` dict

- [ ] **Step 1: Add failing test for orderRef in journal**

Append to `test_phase4.py`:

```python
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
```

- [ ] **Step 2: Run to verify tests pass (journal already accepts any dict — these should pass)**

```bash
cd /Users/macb/stfs-eq && python3 -m pytest test_phase4.py::test_orderref_format test_phase4.py::test_journal_accepts_orderref_fields -v
```

Expected: both `PASSED` (journal is pass-through — no code change needed to journal.py itself)

- [ ] **Step 3: Modify order_server.py — add `import time` and orderRef to shares bracket**

At the top of `order_server.py`, add `import time` after `import json`:

```python
import json
import time
import threading
import concurrent.futures
```

Replace `_place_shares()` with the following (changes: generate `ref`, set `orderRef` on all three orders, add `orderRef`/`stop_order_id`/`atr`/`entry_price` to journal call):

```python
def _place_shares(data: dict) -> dict:
    from ib_insync import Stock, LimitOrder, StopOrder, MarketOrder
    import config as C

    ticker  = data["ticker"]
    account = data["account"]
    shares  = int(data["shares"])
    entry   = float(data["entry"])
    stop    = float(data["stop"])
    target  = float(data["target"])
    is_moo  = str(data.get("entry_type", "LMT")).upper() == "MOO"

    if shares < 1:
        return {"ok": False, "error": "Shares must be ≥ 1"}

    ref = f"{C.STFS_ORDER_REF_PREFIX}{int(time.time())}"

    contract = Stock(_TWS_TICKER.get(ticker, ticker), "SMART", "USD")
    _ib.qualifyContracts(contract)

    p_id  = _ib.client.getReqId()
    tp_id = _ib.client.getReqId()
    sl_id = _ib.client.getReqId()

    if is_moo:
        parent = MarketOrder("BUY", shares)
        parent.tif = "OPG"
    else:
        parent = LimitOrder("BUY", shares, round(entry, 2))
        parent.tif = "GTC"
    parent.orderId  = p_id
    parent.account  = account
    parent.orderRef = ref
    parent.transmit = False

    take_profit = LimitOrder("SELL", shares, round(target, 2))
    take_profit.orderId  = tp_id
    take_profit.parentId = p_id
    take_profit.tif      = "GTC"
    take_profit.account  = account
    take_profit.orderRef = ref
    take_profit.transmit = False

    stop_loss = StopOrder("SELL", shares, round(stop, 2))
    stop_loss.orderId  = sl_id
    stop_loss.parentId = p_id
    stop_loss.tif      = "GTC"
    stop_loss.account  = account
    stop_loss.orderRef = ref
    stop_loss.transmit = False

    for order in [parent, take_profit, stop_loss]:
        _ib.placeOrder(contract, order)

    atr_at_entry = float((data.get("signal") or {}).get("atr", 0))
    entry_str = "MOO" if is_moo else f"LMT ${entry:.2f} DAY"
    _journal_append(
        event="entry",
        ticker=ticker,
        account=account,
        signal=data.get("signal"),
        order={
            "type": "shares", "shares": shares,
            "entry": entry, "stop": stop, "target": target,
            "entry_type": "MOO" if is_moo else "LMT",
            "order_ids": [p_id, tp_id, sl_id],
            "orderRef":      ref,
            "stop_order_id": sl_id,
            "atr":           atr_at_entry,
            "entry_price":   entry,
        },
    )
    return {
        "ok": True,
        "message": (
            f"Bracket placed (HELD) — {shares} {ticker}  "
            f"Entry {entry_str}  ·  Stop ${stop:.2f}  ·  Target ${target:.2f}  "
            f"·  IDs: {p_id} / {tp_id} / {sl_id}  ·  ref: {ref}"
        ),
        "order_ids": [p_id, tp_id, sl_id],
    }
```

- [ ] **Step 4: Add orderRef to options orders in `_place_options()`**

In `_place_options()`, add `import config as C` at the top of the function body (after the existing `from ib_insync import ...` line) and generate ref after the validation block. Replace the single-leg long call section and the BAG order section:

```python
def _place_options(data: dict) -> dict:
    from ib_insync import Option, Contract, ComboLeg, LimitOrder
    import config as C

    ticker       = data["ticker"]
    account      = data["account"]
    contracts    = int(data["contracts"])
    structure    = data["structure"]
    expiry       = data["expiry"].replace("-", "")
    long_strike  = float(data["long_strike"])
    short_strike = data.get("short_strike")
    limit_price  = round(float(data["limit_price"]), 2)

    if contracts < 1:
        return {"ok": False, "error": "Contracts must be ≥ 1"}
    if limit_price <= 0:
        return {"ok": False, "error": "Limit price must be > 0"}
    if structure != "long_call" and not short_strike:
        return {"ok": False, "error": f"{structure} requires a short_strike"}
    if short_strike:
        short_strike = float(short_strike)

    ref = f"{C.STFS_ORDER_REF_PREFIX}{int(time.time())}"

    if structure == "long_call":
        opt = Option(ticker, expiry, long_strike, "C", "SMART")
        _ib.qualifyContracts(opt)
        order = LimitOrder("BUY", contracts, limit_price)
        order.account  = account
        order.tif      = "DAY"
        order.orderRef = ref
        order.transmit = False
        trade = _ib.placeOrder(opt, order)
        _journal_append(
            event="entry", ticker=ticker, account=account,
            signal=data.get("signal"),
            order={
                "type": "options", "structure": "long_call",
                "contracts": contracts, "expiry": data["expiry"],
                "long_strike": long_strike, "limit_price": limit_price,
                "order_ids": [trade.order.orderId],
                "orderRef": ref,
            },
        )
        return {
            "ok": True,
            "message": (
                f"Long call placed (HELD) — {contracts}×  "
                f"{ticker} {long_strike:.0f}C  exp {data['expiry']}  "
                f"@ ${limit_price:.2f}  ·  ID: {trade.order.orderId}  ·  ref: {ref}"
            ),
            "order_ids": [trade.order.orderId],
        }

    if structure == "debit_spread":
        legs = [
            (long_strike,  "C", expiry, "BUY"),
            (short_strike, "C", expiry, "SELL"),
        ]
        combo_action = "BUY"

    elif structure == "credit_spread":
        legs = [
            (short_strike, "P", expiry, "SELL"),
            (long_strike,  "P", expiry, "BUY"),
        ]
        combo_action = "SELL"

    elif structure == "diagonal":
        near_exp = (data.get("expiry_front") or data["expiry"]).replace("-", "")
        legs = [
            (short_strike, "C", near_exp, "SELL"),
            (long_strike,  "C", expiry,   "BUY"),
        ]
        combo_action = "BUY"

    else:
        return {"ok": False, "error": f"Unknown structure: {structure}"}

    opt_contracts = [
        Option(ticker, exp, strike, right, "SMART")
        for strike, right, exp, _ in legs
    ]
    _ib.qualifyContracts(*opt_contracts)

    bag = Contract()
    bag.symbol    = ticker
    bag.secType   = "BAG"
    bag.currency  = "USD"
    bag.exchange  = "SMART"
    bag.comboLegs = [
        ComboLeg(conId=opt.conId, ratio=1, action=action, exchange="SMART")
        for opt, (_, _, _, action) in zip(opt_contracts, legs)
    ]

    order = LimitOrder(combo_action, contracts, limit_price)
    order.account  = account
    order.tif      = "DAY"
    order.orderRef = ref
    order.transmit = False
    trade = _ib.placeOrder(bag, order)
    _journal_append(
        event="entry", ticker=ticker, account=account,
        signal=data.get("signal"),
        order={
            "type": "options", "structure": structure,
            "contracts": contracts, "expiry": data["expiry"],
            "expiry_front": data.get("expiry_front"),
            "long_strike": long_strike, "short_strike": short_strike,
            "limit_price": limit_price,
            "order_ids": [trade.order.orderId],
            "orderRef": ref,
        },
    )

    return {
        "ok": True,
        "message": (
            f"{structure.replace('_', ' ').title()} placed (HELD) — "
            f"{contracts}×  {ticker}  @ ${limit_price:.2f} net  "
            f"·  ID: {trade.order.orderId}  ·  ref: {ref}"
        ),
        "order_ids": [trade.order.orderId],
    }
```

- [ ] **Step 5: Verify syntax**

```bash
cd /Users/macb/stfs-eq && python3 -c "import order_server; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add order_server.py test_phase4.py
git commit -m "feat(order_server): tag all orders with STFS-EQ orderRef; store stop_order_id+atr in journal"
```

---

## Task 4: battle_card._simulate() — Trailing Stop Logic

**Files:**
- Modify: `battle_card.py:550-621` (`_simulate`), `battle_card.py:686` and `battle_card.py:692` (two call sites in `run_mini_backtest`)

- [ ] **Step 1: Write failing test**

Append to `test_phase4.py`:

```python
# ── Task 4: _simulate trailing stop ──────────────────────────────────────────

def test_trailing_stop_raises_stop_before_pullback():
    """
    Scenario:
      - Signal at bar 0, limit = 97 (close=100, ATR=2, ENTRY_MULT=1.5)
      - Entry fills at bar 1 at 97 (slip=0 for clarity via monkeypatch)
      - stop_dist = 2.5*2 = 5, initial stop = 92, trail_trigger = 1.0*5 = 5
      - Bar 2: hi=103 (< 97+5=102, no activation yet)
      - Bar 3: hi=103.5 (< 102, still no activation)
      - Bar 4: hi=103 (no)
      - Bar 5: hi=103.5 (no)
      - Bar 6: hi=102.5 (no)
      - We use a scenario where trail activates and stop moves:
        After entry at 97, trail_trigger=5, price needs hi >= 102.
        Bar 2: hi=103 >= 102 → activates! trail_ma[2]=95 < 92? No. trail_ma[2]=93 < 92? No.
        Let's set trail_ma to start low then rise above 92 after activation.

    Concrete test: trailing raises stop from 92 to 95 after activation.
    Trade then pulled back: lo drops to 94, which is > 92 (old stop) but > 95? No wait.

    Revised: stop moves to 95 after trail_ma rises to 95. Then lo=93 < 95 → stop hit at 95.
    P&L = (95 - 97) / 97 ≈ -2.06%  (vs -5.15% at initial stop of 92)
    """
    import config as C
    import monkeypatch  # not real — use pytest fixture below
```

Actually rewrite this as a proper pytest test using monkeypatch fixture:

```python
def test_trailing_stop_raises_stop_before_pullback(monkeypatch):
    """
    Trailing stop raises stop_loss before a pullback, improving exit vs static stop.

    Setup (slip=0, commission=0 for exact arithmetic):
      close[0]=100, ATR[0]=2 → p_limit=97, p_stop_d=5, p_tar_d=8, trail_trigger=5
      Entry at bar 1 (lo[1]=96 <= 97): entry_price=97, stop=92, target=105
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
    # Build arrays: signal at bar 0; limit fills bar 1; trail activates bar 2; stop hit bar 4
    cl  = np.array([100.0, 97.0, 103.0, 103.0,  97.0,  97.0,  97.0,  97.0,  97.0,  97.0])
    hi  = np.array([101.0, 98.0, 104.0, 104.0,  98.0,  98.0,  98.0,  98.0,  98.0,  98.0])
    lo  = np.array([ 96.0, 96.0, 102.5, 102.5,  94.0,  94.0,  94.0,  94.0,  94.0,  94.0])
    op  = np.array([100.0, 97.0, 103.0, 103.0,  97.0,  97.0,  97.0,  97.0,  97.0,  97.0])
    at  = np.array([  2.0,  2.0,   2.0,   2.0,   2.0,   2.0,   2.0,   2.0,   2.0,   2.0])
    # trail_ma: starts below initial stop (92), rises to 96 at bar 3
    tm  = np.array([ 88.0, 90.0,  94.0,  96.0,  96.0,  96.0,  96.0,  96.0,  96.0,  96.0])

    sb  = np.array([True] + [False] * (n - 1))
    brk = np.array([False] * n)

    df = pd.DataFrame({"Close": cl, "High": hi, "Low": lo, "Open": op})

    trades = _simulate(df, sb, brk, cl, at, op, hi, lo, tm, 0, n)

    assert len(trades) == 1, f"Expected 1 trade, got {len(trades)}"
    # Static stop at 92 → P&L = (92-97)/97 = -5.15%
    # Trail raised stop to 96 → P&L = (96-97)/97 = -1.03%
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
    # Entry at 97, stop=92, trail_trigger=5 (need hi>=102 to activate)
    # Price never exceeds 101.9 → trail never activates → stop stays at 92
    # lo drops to 91.5 at bar 4 → stop hit at 92
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
    # Stop stayed at 92 → exit at 92, P&L = (92-97)/97 ≈ -5.15%
    expected = (92.0 - 97.0) / 97.0
    assert abs(trades[0] - expected) < 0.001, f"Expected ~{expected:.4f}, got {trades[0]:.4f}"
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd /Users/macb/stfs-eq && python3 -m pytest test_phase4.py::test_trailing_stop_raises_stop_before_pullback test_phase4.py::test_trailing_does_not_activate_before_1r -v
```

Expected: `FAILED` — `TypeError: _simulate() takes 9 positional arguments but 10 were given`

- [ ] **Step 3: Modify `_simulate()` in battle_card.py**

Replace the entire `_simulate` function (lines 550–621) with:

```python
def _simulate(df, sb_a, brk_a, cl_a, at_a, op_a, hi_a, lo_a, trail_ma_a, start_i, end_i):
    """Replay strong_buy signals from start_i..end_i (exclusive). Returns list of
    net fractional P/L per trade after friction.

    Friction model:
      entry  = limit * (1 + slip)        # pay up on entry
      exit   = level * (1 ± slip)        # cross spread on close
      comm   = 2 * COMMISSION_PER_TRADE  # fraction of notional, round-trip

    Trailing stop: activates when price reaches entry + TRAIL_ACTIVATE_R × stop_dist,
    then ratchets stop_loss up to trail_ma_a[i+1] when that value exceeds current stop.
    Stop never moves down. Auto-transmit in live mode; sim loop uses same logic.
    """
    slip = C.SLIPPAGE_PCT / 100.0

    in_trade = False
    limit_order_active = False
    entry_price = stop_loss = take_profit = p_limit = p_stop_d = p_tar_d = 0.0
    pending_brk = False
    trailing_active = False
    trail_trigger = 0.0
    trades = []

    def _close(exit_px):
        nonlocal in_trade, trailing_active
        exit_eff = exit_px * (1 - slip) if exit_px >= entry_price else exit_px * (1 + slip)
        gross = (exit_eff - entry_price) / entry_price
        trades.append(gross - 2 * C.COMMISSION_PER_TRADE)
        in_trade = False
        trailing_active = False

    for i in range(start_i, min(end_i, len(df) - 1)):
        if in_trade:
            nxt_op = op_a[i + 1]
            nxt_lo = lo_a[i + 1]
            nxt_hi = hi_a[i + 1]

            # Trailing stop: activate at 1R profit, then ratchet up only
            if not trailing_active and nxt_hi >= entry_price + trail_trigger:
                trailing_active = True
            if trailing_active:
                new_stop = trail_ma_a[i + 1]
                if new_stop > stop_loss:
                    stop_loss = new_stop

            if nxt_op <= stop_loss:
                _close(nxt_op)
            elif nxt_op >= take_profit:
                _close(nxt_op)
            elif nxt_lo <= stop_loss:
                _close(stop_loss)
            elif nxt_hi >= take_profit:
                _close(take_profit)
            continue

        if limit_order_active:
            if pending_brk:
                entry_price = op_a[i + 1] * (1 + slip)
                stop_loss = entry_price - p_stop_d
                take_profit = entry_price + p_tar_d
                trail_trigger = C.TRAIL_ACTIVATE_R * p_stop_d
                limit_order_active = False
                in_trade = True
                if lo_a[i + 1] <= stop_loss:   _close(stop_loss)
                elif hi_a[i + 1] >= take_profit: _close(take_profit)
            else:
                if lo_a[i + 1] <= p_limit:
                    entry_price = p_limit * (1 + slip)
                    stop_loss = entry_price - p_stop_d
                    take_profit = entry_price + p_tar_d
                    trail_trigger = C.TRAIL_ACTIVATE_R * p_stop_d
                    limit_order_active = False
                    in_trade = True
                    if lo_a[i + 1] <= stop_loss: _close(stop_loss)
                else:
                    limit_order_active = False

        if not in_trade and not limit_order_active and sb_a[i]:
            pending_brk = bool(brk_a[i])
            if pending_brk:
                p_stop_d = C.STOP_ATR_MULT * at_a[i]
                p_tar_d  = C.TARGET_ATR_MULT * at_a[i]
            else:
                p_limit  = cl_a[i] - (C.ENTRY_ATR_MULT * at_a[i])
                p_stop_d = C.STOP_ATR_MULT * at_a[i]
                p_tar_d  = C.TARGET_ATR_MULT * at_a[i]
            limit_order_active = True

    return trades
```

- [ ] **Step 4: Update both call sites in `run_mini_backtest()`**

At line 669 in `run_mini_backtest`, after `cl_a, at_a, op_a, hi_a, lo_a = ...`, add:

```python
    trail_ma_a = factors["trail_ma"].values
```

Replace line 686:
```python
        trades = _simulate(df, sb_a, brk_a, cl_a, at_a, op_a, hi_a, lo_a, trail_ma_a, test_start, test_end)
```

Replace line 692:
```python
    recent_trades = _simulate(df, sb_a, brk_a, cl_a, at_a, op_a, hi_a, lo_a, trail_ma_a, recent_start, n)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/macb/stfs-eq && python3 -m pytest test_phase4.py::test_trailing_stop_raises_stop_before_pullback test_phase4.py::test_trailing_does_not_activate_before_1r -v
```

Expected: both `PASSED`

- [ ] **Step 6: Verify battle_card import still works**

```bash
cd /Users/macb/stfs-eq && python3 -c "from battle_card import _simulate, run_mini_backtest; print('OK')"
```

Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add battle_card.py test_phase4.py
git commit -m "feat(battle_card): add trailing stop ratchet to _simulate() with trail_ma_a param"
```

---

## Task 5: backtest.py — Trailing Stop Logic

**Files:**
- Modify: `backtest.py:51-65` (add trail MA after indicator block), `backtest.py:100-206` (sim loop)

- [ ] **Step 1: Add trail MA computation to backtest.py**

In `backtest.py`, after line 65 (`atr_pct = (at / cl) * 100`), add:

```python
        # Trail MA — matches compute_factors() trail_ma logic
        if C.TRAIL_MA_TYPE == "HMA":
            trail_ma = hma(cl, C.TRAIL_MA_LEN)
        else:
            trail_ma = ema(cl, C.TRAIL_MA_LEN)
```

Ensure `import config as C` is present at top of file (it already is: `import config as C` at line 11).

- [ ] **Step 2: Add trailing state to the sim loop**

In `backtest.py`, after line 108 (`trade_mae = 0.0`), add two new state variables:

```python
        trailing_active = False
        trail_trigger = 0.0
```

Replace the `if in_trade:` block (lines 128–151) with:

```python
            if in_trade:
                # MAE: intra-trade adverse excursion before exit checks
                current_unrealized = (l_tmrw - entry_price) / entry_price
                trade_mae = min(trade_mae, current_unrealized)

                # Trailing stop: activate at 1R profit, ratchet stop up only
                if not trailing_active and h_tmrw >= entry_price + trail_trigger:
                    trailing_active = True
                if trailing_active:
                    new_stop = float(trail_ma.iloc[i + 1])
                    if new_stop > stop_loss:
                        stop_loss = new_stop

                if l_tmrw <= stop_loss:
                    pl = (stop_loss - entry_price) / entry_price
                    trades.append({
                        "ticker": ticker, "entry_date": trade_entry_date, "exit_date": date_tmrw,
                        "type": "STOP", "pnl": pl, "entry": entry_price, "exit": stop_loss, "mae": trade_mae
                    })
                    if pl > 0: stats["wins"] += 1
                    else: stats["losses"] += 1
                    in_trade = False
                    trailing_active = False
                elif h_tmrw >= take_profit:
                    pl = (take_profit - entry_price) / entry_price
                    trades.append({
                        "ticker": ticker, "entry_date": trade_entry_date, "exit_date": date_tmrw,
                        "type": "TARGET", "pnl": pl, "entry": entry_price, "exit": take_profit, "mae": trade_mae
                    })
                    if pl > 0: stats["wins"] += 1
                    else: stats["losses"] += 1
                    in_trade = False
                    trailing_active = False
                continue
```

In the `limit_order_active` fill block (both `pending_breakout=True` and `pending_breakout=False` branches), add `trail_trigger` assignment immediately after `take_profit` is set. Find these two patterns and add the line after each:

```python
                        take_profit = entry_price + pending_target_dist
                        trail_trigger = C.TRAIL_ACTIVATE_R * pending_stop_dist  # ADD THIS
```

(Two locations: breakout path line ~157, limit path line ~177)

- [ ] **Step 3: Verify backtest.py syntax**

```bash
cd /Users/macb/stfs-eq && python3 -c "import backtest; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Smoke-test backtest with a known ticker**

```bash
cd /Users/macb/stfs-eq && python3 backtest.py QQQ --days 500
```

Expected: runs without error, prints trade stats table

- [ ] **Step 5: Commit**

```bash
git add backtest.py
git commit -m "feat(backtest): add trailing stop ratchet to standalone sim loop"
```

---

## Task 6: trailing_stop_manager.py

**Files:**
- Create: `trailing_stop_manager.py`

- [ ] **Step 1: Write pure-logic unit tests first**

Append to `test_phase4.py`:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd /Users/macb/stfs-eq && python3 -m pytest test_phase4.py::test_market_open_weekday_inside_hours test_phase4.py::test_market_closed_on_weekend test_phase4.py::test_compute_trail_stop_ratchets_up -v
```

Expected: `ERROR` / `ModuleNotFoundError: No module named 'trailing_stop_manager'`

- [ ] **Step 3: Create trailing_stop_manager.py**

Create `/Users/macb/stfs-eq/trailing_stop_manager.py`:

```python
"""
trailing_stop_manager.py — STFS-EQ Trailing Stop Manager
clientId=17 (read-write). Run alongside TWS and order_server.py.

Polls open equity positions every TRAIL_POLL_INTERVAL seconds during market
hours (9:30–16:00 ET). When a STFS-EQ position has reached TRAIL_ACTIVATE_R
in profit, trails the stop-loss order to the configured moving average.

Stop-loss updates are the ONE exception to the HELD discipline: they auto-
transmit because a stale stop is worse than no stop.

Usage:
    python3 trailing_stop_manager.py            # daemon
    python3 trailing_stop_manager.py --once     # single pass, exit
    python3 trailing_stop_manager.py --dry-run  # compute, log, do not transmit
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import time
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import config as C
from indicators import ema, hma

# ── constants ─────────────────────────────────────────────────────────────────

TWS_HOST = "127.0.0.1"
TWS_PORT = 7496
ET       = ZoneInfo("America/New_York")

_LOG_PATH     = Path(C.OUTPUT_DIR) / "trailing_stop.log"
_JOURNAL_PATH = Path(C.OUTPUT_DIR) / "trade_journal.jsonl"

_MAX_RETRIES    = 3
_RETRY_DELAY_S  = 30
_MA_FETCH_BARS  = C.TRAIL_MA_LEN + 10   # extra bars for warmup

# ── logging ───────────────────────────────────────────────────────────────────

_LOG_PATH.parent.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s ET  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(_LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ── pure helpers (testable without TWS) ───────────────────────────────────────

def _market_open() -> bool:
    """Return True if current ET time is within regular trading hours Mon–Fri."""
    now = datetime.now(ET)
    return now.weekday() < 5 and dtime(9, 30) <= now.time() <= dtime(16, 0)


def _compute_trail_stop(
    price: float,
    entry_price: float,
    trail_trigger: float,
    current_stop: float,
    trail_ma: float,
    trailing_active: bool,
) -> tuple[float, bool]:
    """Compute new stop price and updated activation state.

    Returns (new_stop_price, trailing_active).
    Stop only moves up — never down.
    """
    if not trailing_active:
        if price >= entry_price + trail_trigger:
            trailing_active = True
        else:
            return current_stop, False

    # Active: ratchet up only
    new_stop = trail_ma if trail_ma > current_stop else current_stop
    return new_stop, True


def _compute_ma(closes: pd.Series) -> float:
    """Compute the last value of the configured trail MA."""
    if C.TRAIL_MA_TYPE == "HMA":
        ma = hma(closes, C.TRAIL_MA_LEN)
    else:
        ma = ema(closes, C.TRAIL_MA_LEN)
    val = float(ma.iloc[-1])
    return val if math.isfinite(val) else 0.0


# ── journal loader ─────────────────────────────────────────────────────────────

def _load_journal_equity() -> list[dict]:
    """Return all journal entries for equity (shares) bracket orders that have
    stop_order_id recorded — these are candidates for trail management."""
    if not _JOURNAL_PATH.exists():
        return []
    entries = []
    with _JOURNAL_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            order = rec.get("order", {})
            if (
                order.get("type") == "shares"
                and order.get("orderRef", "").startswith(C.STFS_ORDER_REF_PREFIX)
                and order.get("stop_order_id")
                and order.get("atr", 0) > 0
                and order.get("entry_price", 0) > 0
            ):
                entries.append({
                    "ticker":        rec.get("ticker"),
                    "account":       rec.get("account"),
                    "orderRef":      order["orderRef"],
                    "stop_order_id": int(order["stop_order_id"]),
                    "atr_at_entry":  float(order["atr"]),
                    "entry_price":   float(order["entry_price"]),
                })
    return entries


# ── TWS helpers ───────────────────────────────────────────────────────────────

def _connect(retry: int = _MAX_RETRIES) -> "IB | None":
    try:
        from ib_insync import IB
    except ImportError:
        log.error("ib_insync not installed")
        return None
    for attempt in range(1, retry + 1):
        try:
            ib = IB()
            ib.connect(TWS_HOST, TWS_PORT, clientId=C.TWS_TRAIL_CLIENT,
                       timeout=5, readonly=False)
            log.info(f"Connected to TWS (clientId={C.TWS_TRAIL_CLIENT})")
            return ib
        except Exception as e:
            log.warning(f"Connect attempt {attempt}/{retry} failed: {e}")
            if attempt < retry:
                time.sleep(_RETRY_DELAY_S)
    log.error("Could not connect to TWS — exiting")
    return None


def _fetch_closes(ib, ticker: str) -> pd.Series | None:
    """Fetch last _MA_FETCH_BARS daily closes for trail MA computation."""
    try:
        from ib_insync import Stock, util
        tws_sym  = {"BRK-B": "BRK B", "BRK-A": "BRK A"}.get(ticker, ticker)
        contract = Stock(tws_sym, "SMART", "USD")
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=f"{min(_MA_FETCH_BARS + 5, 365)} D",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
            keepUpToDate=False,
        )
        if not bars:
            return None
        df = util.df(bars)
        closes = pd.to_numeric(df["close"], errors="coerce").dropna()
        return closes if len(closes) >= C.TRAIL_MA_LEN else None
    except Exception as e:
        log.warning(f"  {ticker}: OHLC fetch failed — {e}")
        return None


# ── main poll loop ─────────────────────────────────────────────────────────────

def _run_pass(ib, journal_entries: list[dict],
              trailing_state: dict[int, bool],
              dry_run: bool) -> None:
    """One poll pass: check each journal entry against open orders."""
    try:
        open_orders = {t.order.orderId: t for t in ib.reqOpenOrders()}
    except Exception as e:
        log.error(f"reqOpenOrders failed: {e}")
        return

    for entry in journal_entries:
        stop_id = entry["stop_order_id"]
        ticker  = entry["ticker"]

        stop_trade = open_orders.get(stop_id)
        if stop_trade is None:
            continue   # order no longer open (filled, cancelled, or already closed)

        current_stop = float(stop_trade.order.auxPrice)
        entry_price  = entry["entry_price"]
        atr_at_entry = entry["atr_at_entry"]
        trail_trigger = C.TRAIL_ACTIVATE_R * C.STOP_ATR_MULT * atr_at_entry

        closes = _fetch_closes(ib, ticker)
        if closes is None:
            log.info(f"  {ticker:<6}  SKIP  OHLC unavailable")
            continue

        live_price = float(closes.iloc[-1])
        trail_ma_val = _compute_ma(closes)
        if trail_ma_val <= 0:
            log.info(f"  {ticker:<6}  SKIP  trail MA invalid ({trail_ma_val})")
            continue

        currently_active = trailing_state.get(stop_id, False)
        new_stop, now_active = _compute_trail_stop(
            price=live_price,
            entry_price=entry_price,
            trail_trigger=trail_trigger,
            current_stop=current_stop,
            trail_ma=trail_ma_val,
            trailing_active=currently_active,
        )
        trailing_state[stop_id] = now_active

        if not now_active:
            log.info(
                f"  {ticker:<6}  SKIP  not yet {C.TRAIL_ACTIVATE_R}R profit "
                f"(price={live_price:.2f}, trigger={entry_price + trail_trigger:.2f})"
            )
            continue

        if new_stop <= current_stop:
            log.info(
                f"  {ticker:<6}  SKIP  MA({trail_ma_val:.2f}) ≤ stop({current_stop:.2f})"
            )
            continue

        # Update stop
        new_stop_rounded = round(new_stop, 2)
        log.info(
            f"  {ticker:<6}  {'DRY-RUN ' if dry_run else ''}UPDATE  "
            f"stop {current_stop:.2f} → {new_stop_rounded:.2f}  "
            f"(trail {C.TRAIL_MA_TYPE}{C.TRAIL_MA_LEN}={trail_ma_val:.2f})"
        )
        if dry_run:
            continue

        try:
            stop_trade.order.auxPrice = new_stop_rounded
            stop_trade.order.transmit = True   # EXCEPTION to HELD rule
            ib.placeOrder(stop_trade.contract, stop_trade.order)
        except Exception as e:
            log.error(f"  {ticker:<6}  ERROR updating stop: {e}")


def run(once: bool = False, dry_run: bool = False) -> None:
    ib = _connect()
    if ib is None:
        return

    trailing_state: dict[int, bool] = {}

    try:
        while True:
            if not _market_open():
                log.info("Market closed — sleeping 60s")
                time.sleep(60)
                continue

            journal_entries = _load_journal_equity()
            if not journal_entries:
                log.info("No STFS-EQ equity entries in journal")
            else:
                log.info(f"Pass: {len(journal_entries)} journal entries")
                _run_pass(ib, journal_entries, trailing_state, dry_run)

            if once:
                break
            time.sleep(C.TRAIL_POLL_INTERVAL)
    except KeyboardInterrupt:
        log.info("Interrupted — disconnecting")
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="STFS-EQ Trailing Stop Manager")
    parser.add_argument("--once",    action="store_true", help="Single pass then exit")
    parser.add_argument("--dry-run", action="store_true", help="Compute but do not transmit")
    args = parser.parse_args()
    run(once=args.once, dry_run=args.dry_run)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/macb/stfs-eq && python3 -m pytest test_phase4.py::test_market_open_weekday_inside_hours test_phase4.py::test_market_closed_on_weekend test_phase4.py::test_compute_trail_stop_ratchets_up -v
```

Expected: all three `PASSED`

- [ ] **Step 5: Verify import (no TWS needed)**

```bash
cd /Users/macb/stfs-eq && python3 -c "from trailing_stop_manager import _market_open, _compute_trail_stop, _compute_ma; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add trailing_stop_manager.py test_phase4.py
git commit -m "feat: add trailing_stop_manager.py — daemon trails equity stop-loss orders via TWS clientId=17"
```

---

## Task 7: portfolio_manager.py

**Files:**
- Create: `portfolio_manager.py`

- [ ] **Step 1: Write unit tests for pure exit signal helpers**

Append to `test_phase4.py`:

```python
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
    import config as C
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
    import config as C
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
    """Credit spread at DTE=20 (≤21) → CLOSE."""
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
    """Long call at DTE=14 (≤14) → CLOSE."""
    import portfolio_manager as pm
    triggered, reason = pm._signal_dte(structure="long_call", dte=14)
    assert triggered is True
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd /Users/macb/stfs-eq && python3 -m pytest test_phase4.py -k "signal" -v
```

Expected: `ERROR` — `ModuleNotFoundError: No module named 'portfolio_manager'`

- [ ] **Step 3: Create portfolio_manager.py**

Create `/Users/macb/stfs-eq/portfolio_manager.py`:

```python
"""
portfolio_manager.py — STFS-EQ Portfolio Manager
clientId=18 (read-only). Advisory only — never places, modifies, or cancels orders.

Fetches open options positions, identifies STFS-EQ trades by cross-referencing
trade_journal.jsonl, then evaluates three independent exit signals per position:
  [1] Underlying price vs target_value / stop from journal entry
  [2] Position P&L% vs configured thresholds
  [3] DTE vs time-based exit thresholds

Usage:
    python3 portfolio_manager.py                  # single run, print table, exit
    python3 portfolio_manager.py --watch 60       # refresh every N seconds
    python3 portfolio_manager.py --account Borg   # filter to one account
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import config as C

# ── constants ─────────────────────────────────────────────────────────────────

TWS_HOST = "127.0.0.1"
TWS_PORT = 7496
ET       = ZoneInfo("America/New_York")

_JOURNAL_PATH = Path(C.OUTPUT_DIR) / "trade_journal.jsonl"

# Structures that use long-premium exit DTE threshold
_DEBIT_STRUCTURES = {"long_call", "debit_spread"}
# Structures that use credit DTE threshold
_CREDIT_STRUCTURES = {"credit_spread", "diagonal"}


# ── pure exit signal helpers (testable without TWS) ───────────────────────────

def _signal_price(underlying: float, target: float, stop: float) -> tuple[bool, str]:
    """Signal 1: underlying price vs target/stop from journal."""
    if underlying >= target:
        return True, f"underlying {underlying:.2f} ≥ target {target:.2f}"
    if underlying <= stop:
        return True, f"underlying {underlying:.2f} ≤ stop {stop:.2f}"
    return False, ""


def _signal_pnl(
    structure: str,
    mark: float,
    net_debit: float | None,
    net_credit: float | None,
    max_loss_per_contract: float,
    contracts: int,
) -> tuple[bool, str]:
    """Signal 2: position P&L% vs configured thresholds.

    For debit structures: cost_basis = net_debit * 100 * contracts
    For credit structures: cost_basis = max_loss_per_contract * contracts
    """
    if mark is None or not math.isfinite(mark):
        return False, ""

    if structure in _DEBIT_STRUCTURES and net_debit and net_debit > 0:
        cost_basis  = net_debit * 100 * contracts
        current_val = mark * 100 * contracts
        unrealized  = (current_val - cost_basis) / cost_basis
        if unrealized >= 1.50:
            return True, f"+150% gain (debit target)"
        if unrealized <= -C.OPT_PNL_STOP_PCT:
            return True, f"{unrealized*100:.0f}% loss (stop)"
        return False, ""

    if structure in _CREDIT_STRUCTURES and net_credit and net_credit > 0:
        max_credit   = net_credit * 100 * contracts
        current_cost = mark * 100 * contracts      # cost to close
        profit_taken = max_credit - current_cost
        if profit_taken >= C.CREDIT_TARGET_PCT * max_credit:
            return True, f"{C.CREDIT_TARGET_PCT*100:.0f}% of credit captured"
        # Stop: current mark cost > OPT_PNL_STOP_PCT of max loss
        if current_cost >= C.OPT_PNL_STOP_PCT * max_loss_per_contract * contracts:
            return True, f"P&L stop (≥{C.OPT_PNL_STOP_PCT*100:.0f}% of max loss)"
        return False, ""

    return False, ""


def _signal_dte(structure: str, dte: int) -> tuple[bool, str]:
    """Signal 3: DTE-based time exit."""
    if structure in _CREDIT_STRUCTURES and dte <= C.OPT_DTE_EXIT_CREDIT:
        return True, f"DTE {dte} ≤ {C.OPT_DTE_EXIT_CREDIT} (credit/time)"
    if structure in _DEBIT_STRUCTURES and dte <= C.OPT_DTE_EXIT_DEBIT:
        return True, f"DTE {dte} ≤ {C.OPT_DTE_EXIT_DEBIT} (debit/time)"
    return False, ""


# ── journal loader ─────────────────────────────────────────────────────────────

def _load_journal_options(account_filter: str | None = None) -> dict[str, dict]:
    """Return {orderRef: journal_entry} for all STFS-EQ options entries.
    Most recent entry wins if duplicate orderRefs exist."""
    if not _JOURNAL_PATH.exists():
        return {}
    result: dict[str, dict] = {}
    with _JOURNAL_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            order = rec.get("order", {})
            ref = order.get("orderRef", "")
            if (
                order.get("type") == "options"
                and ref.startswith(C.STFS_ORDER_REF_PREFIX)
                and (account_filter is None or rec.get("account") == account_filter)
            ):
                result[ref] = rec
    return result


# ── TWS helpers ───────────────────────────────────────────────────────────────

def _connect() -> "IB | None":
    try:
        from ib_insync import IB
    except ImportError:
        print("ERROR: ib_insync not installed")
        return None
    try:
        ib = IB()
        ib.connect(TWS_HOST, TWS_PORT, clientId=C.TWS_PORTFOLIO_CLIENT,
                   timeout=5, readonly=True)
        return ib
    except Exception as e:
        print(f"ERROR: Could not connect to TWS: {e}")
        return None


def _get_mark(ib, contract) -> float | None:
    """Fetch bid/ask snapshot; fall back to last price."""
    try:
        td = ib.reqMktData(contract, "", snapshot=True, regulatorySnapshot=False)
        ib.sleep(2)
        ib.cancelMktData(contract)
        bid = float(td.bid) if td.bid and td.bid > 0 else 0.0
        ask = float(td.ask) if td.ask and td.ask > 0 else 0.0
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
        last = float(td.last) if td.last and td.last > 0 else 0.0
        return last if last > 0 else None
    except Exception:
        return None


def _underlying_price(ib, ticker: str) -> float | None:
    """Live underlying price via 1-min bar."""
    try:
        from ib_insync import Stock, util
        tws_sym  = {"BRK-B": "BRK B", "BRK-A": "BRK A"}.get(ticker, ticker)
        contract = Stock(tws_sym, "SMART", "USD")
        bars = ib.reqHistoricalData(
            contract, endDateTime="", durationStr="300 S",
            barSizeSetting="1 min", whatToShow="TRADES",
            useRTH=False, formatDate=1, keepUpToDate=False,
        )
        if bars:
            return float(bars[-1].close)
        return None
    except Exception:
        return None


# ── position matching ─────────────────────────────────────────────────────────

def _match_positions_to_journal(positions, journal: dict[str, dict]) -> list[dict]:
    """Match live options positions to journal entries by ticker + account.
    Returns list of rows ready for display."""
    # Build lookup: (ticker, account) → journal entry (most recent wins — already ordered)
    by_ticker_acct: dict[tuple, dict] = {}
    for ref, rec in journal.items():
        key = (rec.get("ticker", ""), rec.get("account", ""))
        by_ticker_acct[key] = rec

    rows = []
    for pos in positions:
        if pos.contract.secType not in ("OPT", "BAG"):
            continue
        ticker  = pos.contract.symbol
        account = pos.account
        key     = (ticker, account)
        rec     = by_ticker_acct.get(key)
        if rec is None:
            continue   # not a STFS-EQ trade
        rows.append({"position": pos, "journal": rec})
    return rows


# ── display ───────────────────────────────────────────────────────────────────

def _render_table(rows: list[dict], ib) -> None:
    now_et = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
    print(f"\nSTFS-EQ Portfolio  {now_et}          TWS: connected")
    print("═" * 78)
    print(f"{'TICKER':<8} {'ACCT':<8} {'STRUCTURE':<20} {'ENTRY':>8} {'MARK':>7} {'P&L%':>7} {'DTE':>5}  SIGNAL")
    print("─" * 78)

    if not rows:
        print("  No STFS-EQ options positions found.")
        print("═" * 78)
        return

    today = date.today()
    for row in rows:
        pos = row["position"]
        rec = row["journal"]
        order = rec.get("order", {})

        ticker    = rec.get("ticker", pos.contract.symbol)
        account   = rec.get("account", pos.account)
        structure = order.get("structure", "?")
        net_debit = order.get("net_debit")
        net_credit = order.get("net_credit")
        max_loss  = order.get("max_loss_per_contract", 0)
        contracts = order.get("contracts", int(abs(pos.position)))
        expiry_str = order.get("expiry", "")
        limit_px  = order.get("limit_price", 0)

        # DTE
        try:
            exp_date = date.fromisoformat(expiry_str)
            dte = (exp_date - today).days
        except Exception:
            dte = -1

        # Live mark
        mark = _get_mark(ib, pos.contract)
        mark_str = f"${mark:.2f}" if mark else "STALE"

        # P&L% display
        pnl_str = "?"
        if mark is not None and (net_debit or net_credit):
            if net_debit and net_debit > 0:
                pnl = (mark * 100 * contracts - net_debit * 100 * contracts) / (net_debit * 100 * contracts) * 100
            else:
                pnl = (net_credit * 100 * contracts - mark * 100 * contracts) / (max_loss * contracts) * 100
            pnl_str = f"{pnl:+.0f}%"

        # Exit signals
        signals = []

        # Signal 1: underlying price
        target_val = order.get("target_value") or order.get("target")
        stop_val   = order.get("stop") or (order.get("entry_price", 0) - C.STOP_ATR_MULT * order.get("atr", 0))
        if target_val and stop_val:
            underlying = _underlying_price(ib, ticker)
            if underlying:
                trig, reason = _signal_price(underlying, float(target_val), float(stop_val))
                if trig:
                    signals.append(f"price: {reason}")

        # Signal 2: P&L
        if mark is not None:
            trig, reason = _signal_pnl(
                structure=structure, mark=mark,
                net_debit=net_debit, net_credit=net_credit,
                max_loss_per_contract=max_loss, contracts=contracts,
            )
            if trig:
                signals.append(reason)

        # Signal 3: DTE
        if dte >= 0:
            trig, reason = _signal_dte(structure=structure, dte=dte)
            if trig:
                signals.append(reason)

        if not signals:
            signal_str = "HOLD"
            icon = " "
        elif any("stop" in s.lower() or "loss" in s.lower() for s in signals):
            signal_str = "⛔ CLOSE (" + signals[0] + ")"
            icon = "⛔"
        else:
            signal_str = "⚠  CLOSE (" + signals[0] + ")"
            icon = "⚠ "

        entry_str = f"${limit_px:.2f}" if limit_px else "?"
        print(
            f"{ticker:<8} {account:<8} {structure:<20} {entry_str:>8} "
            f"{mark_str:>7} {pnl_str:>7} {dte:>5}  {signal_str}"
        )

    print("═" * 78)


# ── main ──────────────────────────────────────────────────────────────────────

def run(watch_interval: int | None = None, account_filter: str | None = None) -> None:
    ib = _connect()
    if ib is None:
        return

    try:
        while True:
            journal = _load_journal_options(account_filter)
            if not journal:
                print("No STFS-EQ options entries in journal — nothing to display")
            else:
                positions = ib.positions()
                rows = _match_positions_to_journal(positions, journal)
                _render_table(rows, ib)

            if watch_interval is None:
                break
            time.sleep(watch_interval)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="STFS-EQ Portfolio Manager")
    parser.add_argument("--watch",   type=int, default=None,
                        metavar="N", help="Refresh every N seconds (default: run once)")
    parser.add_argument("--account", type=str, default=None,
                        help="Filter to one account name (e.g. Borg)")
    args = parser.parse_args()
    run(watch_interval=args.watch, account_filter=args.account)
```

- [ ] **Step 4: Run all signal tests**

```bash
cd /Users/macb/stfs-eq && python3 -m pytest test_phase4.py -k "signal" -v
```

Expected: all signal tests `PASSED`

- [ ] **Step 5: Verify import**

```bash
cd /Users/macb/stfs-eq && python3 -c "from portfolio_manager import _signal_price, _signal_pnl, _signal_dte; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add portfolio_manager.py test_phase4.py
git commit -m "feat: add portfolio_manager.py — read-only advisory tool for STFS-EQ options positions (clientId=18)"
```

---

## Task 8: Run Full Test Suite + Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (TWS client ID registry)

- [ ] **Step 1: Run full test suite**

```bash
cd /Users/macb/stfs-eq && python3 -m pytest test_phase4.py -v
```

Expected: all tests `PASSED`. If any fail, fix before continuing.

- [ ] **Step 2: Run existing tests to check for regressions**

```bash
cd /Users/macb/stfs-eq && python3 -m pytest test_rr.py -v
```

Expected: all existing tests still `PASSED`

- [ ] **Step 3: Update CLAUDE.md TWS client ID registry**

Find the section in `CLAUDE.md` that documents clientId=15 and clientId=16. Update to add 17 and 18:

```
### TWS client IDs
- `clientId=15` — tws_data.py, readonly, data only
- `clientId=16` — order_server.py, read-write, order placement
- `clientId=17` — trailing_stop_manager.py, read-write, stop modification (auto-transmit)
- `clientId=18` — portfolio_manager.py, read-only, portfolio monitoring

These must not clash. If you add another IB connection anywhere, pick a different ID.
```

- [ ] **Step 4: Final commit**

```bash
git add CLAUDE.md test_phase4.py
git commit -m "docs: update TWS clientId registry for Phase 4 tools (17=trail, 18=portfolio)"
```

- [ ] **Step 5: Push**

```bash
git push origin main
```

---

## Self-Review

**Spec coverage check:**
- ✅ orderRef on all bracket legs (Task 3)
- ✅ stop_order_id + atr + entry_price in journal (Task 3)
- ✅ trail_ma in compute_factors (Task 2)
- ✅ trailing stop in _simulate (Task 4) and backtest.py (Task 5)
- ✅ TRAIL_ACTIVATE_R, TRAIL_MA_TYPE, TRAIL_MA_LEN configurable (Task 1)
- ✅ _market_open() gate (Task 6)
- ✅ auto-transmit for stop updates only (Task 6)
- ✅ Three exit signals in portfolio_manager (Task 7)
- ✅ DTE gate for credit/debit separately (Task 7)
- ✅ Advisory only — no order placement in portfolio_manager (Task 7)
- ✅ CLI flags: --once, --dry-run, --watch, --account (Tasks 6, 7)
- ✅ clientId 17/18 added to CLAUDE.md registry (Task 8)

**Type consistency:**
- `_simulate(df, sb_a, brk_a, cl_a, at_a, op_a, hi_a, lo_a, trail_ma_a, start_i, end_i)` — consistent across Task 4 definition and test calls
- `_compute_trail_stop(price, entry_price, trail_trigger, current_stop, trail_ma, trailing_active)` — consistent between Task 6 definition and tests
- `_signal_price(underlying, target, stop)` → `tuple[bool, str]` — consistent across Task 7 definition and tests
- `_signal_pnl(structure, mark, net_debit, net_credit, max_loss_per_contract, contracts)` → `tuple[bool, str]` — consistent
- `_signal_dte(structure, dte)` → `tuple[bool, str]` — consistent
