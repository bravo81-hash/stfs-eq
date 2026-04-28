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
            return True, "+150% gain (debit target)"
        if unrealized <= -C.OPT_PNL_STOP_PCT:
            return True, f"{unrealized*100:.0f}% loss (stop)"
        return False, ""

    if structure in _CREDIT_STRUCTURES and net_credit and net_credit > 0:
        max_credit   = net_credit * 100 * contracts
        current_cost = mark * 100 * contracts      # cost to close
        profit_taken = max_credit - current_cost
        if profit_taken >= C.CREDIT_TARGET_PCT * max_credit:
            return True, f"{C.CREDIT_TARGET_PCT*100:.0f}% of credit captured"
        # Stop: current mark cost >= OPT_PNL_STOP_PCT of max loss
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

def _connect():
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
        from ib_insync import Stock
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
    """Match live options positions to journal entries by ticker + account."""
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
            continue
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
        elif any("stop" in s.lower() or "loss" in s.lower() for s in signals):
            signal_str = "⛔ CLOSE (" + signals[0] + ")"
        else:
            signal_str = "⚠  CLOSE (" + signals[0] + ")"

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
