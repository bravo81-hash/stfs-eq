# STFS-EQ Phase 4 Design: Trailing Stop Manager + Portfolio Manager

**Date:** 2026-04-28  
**Status:** Approved  
**Scope:** Two standalone tools + coordinated changes to config, indicators, order_server, journal, and both sim loops

---

## 1. Overview

Two independent features that share a common foundation: a journal-linked `orderRef` tagging scheme applied to every order placed by `order_server.py`.

| Feature | File | clientId | Role |
|---------|------|----------|------|
| Trailing Stop Manager | `trailing_stop_manager.py` | 17 (read-write) | Polls open equity positions, trails stops, auto-transmits |
| Portfolio Manager | `portfolio_manager.py` | 18 (read-only) | Fetches options positions, cross-refs journal, flags exits |

**Exception to HELD discipline:** Trailing stop updates are the one case where `transmit=True` is set automatically. A stale stop at the wrong price is worse than no stop. All other orders remain HELD.

---

## 2. Shared Foundation: orderRef Tagging

### order_server.py
Every bracket order leg (parent, stop-loss child, take-profit child) receives:
```python
ref = f"STFS-EQ-{int(time.time())}"
parent_order.orderRef = ref
stop_order.orderRef   = ref
target_order.orderRef = ref
```

### journal.py — new fields per entry
```python
"orderRef":      ref,                          # "STFS-EQ-{timestamp}"
"stop_order_id": stop_trade.order.orderId,     # int — used by trailing manager
"atr":           score["atr"],                 # ATR at signal bar — trail trigger calc
"entry_price":   entry_price,                  # actual limit or MOO estimate
```

`STFS_ORDER_REF_PREFIX = "STFS-EQ-"` added to `config.py`. Both tools filter by this prefix.

---

## 3. Config Additions (config.py)

```python
# ── Trailing Stop Manager ─────────────────────────────────────────────────────
TRAIL_MA_TYPE         = "EMA"   # "EMA" or "HMA"
TRAIL_MA_LEN          = 10      # lookback bars for trail MA
TRAIL_ACTIVATE_R      = 1.0     # R-multiples profit before trailing activates
TRAIL_POLL_INTERVAL   = 300     # seconds between polls (5 min)
TWS_TRAIL_CLIENT      = 17      # clientId — must not clash with 15/16/18

# ── Portfolio Manager ─────────────────────────────────────────────────────────
OPT_DTE_EXIT_CREDIT   = 21      # flag credit spreads at or below this DTE
OPT_DTE_EXIT_DEBIT    = 14      # flag long premium at or below this DTE
OPT_PNL_STOP_PCT      = 0.80    # flag if position down >= 80% of max loss
TWS_PORTFOLIO_CLIENT  = 18      # clientId — read-only
STFS_ORDER_REF_PREFIX = "STFS-EQ-"
```

---

## 4. indicators.py

One new key added to `compute_factors()` return dict — no existing keys changed:

```python
if C.TRAIL_MA_TYPE == "HMA":
    trail_ma = hma(cl, C.TRAIL_MA_LEN)
else:
    trail_ma = ema(cl, C.TRAIL_MA_LEN)
# returned dict gains:
"trail_ma": trail_ma
```

---

## 5. Backtest Trailing Stop Logic

Applied identically to both `battle_card._simulate()` and `backtest.py` sim loop.  
`trail_ma_a` = `factors["trail_ma"].values` (numpy array, passed into sim).

```
Per-trade state:
  trailing_active = False
  trail_trigger   = TRAIL_ACTIVATE_R × STOP_ATR_MULT × atr_at_entry

Each bar while in_trade, before stop/target check:
  if not trailing_active and nxt_hi >= entry_price + trail_trigger:
      trailing_active = True

  if trailing_active:
      new_stop = trail_ma_a[i + 1]       # next bar MA — no lookahead
      if new_stop > stop_loss:
          stop_loss = new_stop            # ratchet up only, never lower
```

**Invariants:**
- Trail MA evaluated at `i+1` — same bar used for execution, no lookahead bias
- Stop only ever moves up — MA dip below current stop is ignored
- If trailing never activates, original static stop holds for full trade life
- Stop/target exit check runs after trail update each bar

---

## 6. trailing_stop_manager.py

### Startup
```
1. Connect TWS clientId=17
2. Load trade_journal.jsonl → index by stop_order_id
3. reqOpenOrders() → {orderId: Order} lookup
4. reqPositions() → {ticker: position} lookup
5. Enter poll loop
```

### Per-poll logic
```
for each journal entry where stop_order_id is in open orders:
    entry_price  = journal["entry_price"]
    atr_at_entry = journal["atr"]
    stop_order   = open_orders[journal["stop_order_id"]]
    current_stop = stop_order.auxPrice

    fetch last (TRAIL_MA_LEN + 5) OHLC bars for ticker
    compute trail MA on that slice

    trail_trigger = TRAIL_ACTIVATE_R × STOP_ATR_MULT × atr_at_entry
    live_price    = last close bar

    if live_price < entry_price + trail_trigger:
        log SKIP "not yet {TRAIL_ACTIVATE_R}R profit"
        continue

    new_stop = trail_ma[-1]
    if new_stop <= current_stop:
        log SKIP "MA ({new_stop:.2f}) ≤ current stop ({current_stop:.2f})"
        continue

    stop_order.auxPrice = round(new_stop, 2)
    stop_order.transmit = True
    ib.placeOrder(contract, stop_order)
    log UPDATE "{ticker}: {current_stop:.2f} → {new_stop:.2f}"
```

### Market hours gate
```python
def _market_open() -> bool:
    now = datetime.now(ZoneInfo("America/New_York"))
    return now.weekday() < 5 and time(9, 30) <= now.time() <= time(16, 0)
```

### Error handling
- TWS disconnect → retry connect 3× with 30s backoff, then exit
- Ticker OHLC fetch fails → log SKIP, continue loop
- `placeOrder` exception → log ERROR, continue — next poll retries
- Never crashes on individual ticker failure

### CLI flags
```
python3 trailing_stop_manager.py            # daemon mode
python3 trailing_stop_manager.py --once     # single pass, exit (testing)
python3 trailing_stop_manager.py --dry-run  # compute, log, do not transmit
```

### Log format (`output/trailing_stop.log`)
```
2026-04-28 10:32:15 ET  AAPL   UPDATE  stop 182.40 → 186.15  (trail EMA10=186.15)
2026-04-28 10:32:15 ET  MSFT   SKIP    not yet 1.0R profit (price=412.10, trigger=418.50)
2026-04-28 10:32:15 ET  NVDA   SKIP    MA(591.20) ≤ current stop(594.00)
```

---

## 7. portfolio_manager.py

### Position fetch + filter
```
reqPositions() → keep secType == "OPT" AND orderRef.startswith("STFS-EQ-")
cross-ref journal by orderRef → get: structure, max_loss_per_contract,
    target_value, net_debit/net_credit, expiry, entry_date, ticker, account
```

### Live mark fetch
```
reqMktData(snapshot=True) per option contract leg
mark = (bid + ask) / 2   if both > 0
     else last            if last > 0
     else None            → flag STALE DATA in output
```
Multi-leg positions: fetch each leg, sum marks with sign (long=+, short=−).

### Exit signals (all three run independently per position)

**Signal 1 — Underlying price**
```
fetch live underlying price
if underlying >= journal["target_value"]  → CLOSE (target)
if underlying <= journal["stop_price"]    → CLOSE (stop)
  stop_price = entry_price - STOP_ATR_MULT × atr_at_entry
```

**Signal 2 — Position P&L%**
```
cost_basis = net_debit × 100 × contracts                (debit structures)
           = max_loss_per_contract × contracts           (credit structures)
unrealized = (mark × 100 × contracts - cost_basis) / cost_basis

debit:   unrealized >= 1.50           → CLOSE (150% gain)
credit:  profit_taken >= CREDIT_TARGET_PCT × max_credit → CLOSE (50% profit)
any:     unrealized <= -OPT_PNL_STOP_PCT               → CLOSE (80% loss)
```

**Signal 3 — DTE gate**
```
dte = (expiry_date - today).days
credit_spread / diagonal: dte <= OPT_DTE_EXIT_CREDIT (21) → CLOSE (time)
long_call / debit_spread: dte <= OPT_DTE_EXIT_DEBIT  (14) → CLOSE (time)
```

### Output format
```
STFS-EQ Portfolio  2026-04-28 14:32 ET          TWS: connected
════════════════════════════════════════════════════════════════════════════
TICKER  ACCT   STRUCTURE        ENTRY      MARK    P&L%    DTE  SIGNAL
────────────────────────────────────────────────────────────────────────────
AAPL    Borg   Long Call        $4.20      $9.10  +116%     22  HOLD
MSFT    SMSF   Bull Put Spread  $1.85      $0.65   +65%     18  ⚠ CLOSE (50% profit)
NVDA    Family Debit Spread     $3.10      $0.62   -80%     31  ⛔ CLOSE (stop 80% loss)
GOOGL   Borg   Call Diagonal    $6.40      $5.10   -20%     12  ⚠ CLOSE (DTE ≤ 14)
════════════════════════════════════════════════════════════════════════════
```

Signal legend: `HOLD` = nothing triggered · `⚠ CLOSE` = soft (profit/time) · `⛔ CLOSE` = hard (stop)

### What it never does
- Places, modifies, or cancels orders
- Writes to the journal
- Auto-closes anything — purely advisory

### CLI flags
```
python3 portfolio_manager.py               # single run, print, exit
python3 portfolio_manager.py --watch 60    # refresh every 60s
python3 portfolio_manager.py --account Borg  # filter to one account
```

---

## 8. File Change Summary

| File | Change type | What changes |
|------|-------------|--------------|
| `config.py` | Add | 10 new constants |
| `order_server.py` | Modify | Set orderRef on all legs; store stop_order_id + atr + entry_price in journal call |
| `journal.py` | Modify | Accept + store 4 new fields: orderRef, stop_order_id, atr, entry_price |
| `indicators.py` | Modify | Add trail_ma key to compute_factors() output |
| `battle_card.py` | Modify | `_simulate()`: add trailing_active state + ratchet logic |
| `backtest.py` | Modify | Sim loop: same trailing stop logic as battle_card._simulate() |
| `trailing_stop_manager.py` | New | Full daemon — see Section 6 |
| `portfolio_manager.py` | New | Full advisory tool — see Section 7 |

---

## 9. TWS Client ID Registry

| clientId | File | Access | Role |
|----------|------|--------|------|
| 15 | `tws_data.py` | read-only | OHLC + options data |
| 16 | `order_server.py` | read-write | Order placement |
| 17 | `trailing_stop_manager.py` | read-write | Stop modification |
| 18 | `portfolio_manager.py` | read-only | Portfolio monitoring |
