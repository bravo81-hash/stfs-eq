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

def _connect(retry: int = _MAX_RETRIES):
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


def _fetch_closes(ib, ticker: str):
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
              trailing_state: dict,
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

    trailing_state: dict = {}

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
