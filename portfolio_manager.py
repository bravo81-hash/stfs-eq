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

_JOURNAL = Path(C.JOURNAL_PATH)

# Structures that use long-premium exit DTE threshold
_DEBIT_STRUCTURES    = {"long_call", "debit_spread"}
# Structures that use credit DTE threshold
_CREDIT_STRUCTURES   = {"credit_spread"}
# Diagonal: debit upfront, but exits on back-leg DTE like credit (21-day threshold)
_DIAGONAL_STRUCTURES = {"diagonal"}


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

    if structure in _DIAGONAL_STRUCTURES and net_debit and net_debit > 0:
        cost_basis  = net_debit * 100 * contracts
        current_val = mark * 100 * contracts
        unrealized  = (current_val - cost_basis) / cost_basis
        gain_target = C.DIAGONAL_TARGET_MULT - 1.0  # 1.50 - 1.0 = 0.50 = 50%
        if unrealized >= gain_target:
            return True, f"+{gain_target*100:.0f}% gain (diagonal target)"
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
    if structure in _DIAGONAL_STRUCTURES and dte <= C.OPT_DTE_EXIT_CREDIT:
        return True, f"DTE {dte} ≤ {C.OPT_DTE_EXIT_CREDIT} (diagonal/time)"
    if structure in _DEBIT_STRUCTURES and dte <= C.OPT_DTE_EXIT_DEBIT:
        return True, f"DTE {dte} ≤ {C.OPT_DTE_EXIT_DEBIT} (debit/time)"
    return False, ""


# ── journal loader ─────────────────────────────────────────────────────────────

def _load_journal_options(account_filter: str | None = None) -> dict[str, dict]:
    """Return {orderRef: journal_entry} for all STFS-EQ options entries.
    Most recent entry wins if duplicate orderRefs exist."""
    if not _JOURNAL.exists():
        return {}
    result: dict[str, dict] = {}
    with _JOURNAL.open() as f:
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
                order.get("type") in ("options", "shares")
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
        import asyncio
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        ib = IB()
        ib.connect(TWS_HOST, TWS_PORT, clientId=C.TWS_PORTFOLIO_CLIENT,
                   timeout=5, readonly=True)
        return ib
    except Exception as e:
        print(f"ERROR: Could not connect to TWS: {e}")
        return None


def _get_mark(ib, contract) -> float | None:
    """Fetch bid/ask snapshot; fall back to last price or historical close."""
    try:
        # Ensure we have conId etc
        ib.qualifyContracts(contract)
        
        # 1. Try a faster ticker update (since we might already be receiving data)
        ticker = ib.reqMktData(contract, "", snapshot=True, regulatorySnapshot=False)
        
        # Poll for up to 3 seconds
        for _ in range(6):
            ib.sleep(0.5)
            bid = float(ticker.bid) if ticker.bid and ticker.bid > 0 else 0.0
            ask = float(ticker.ask) if ticker.ask and ticker.ask > 0 else 0.0
            if bid > 0 and ask > 0:
                ib.cancelMktData(contract)
                return (bid + ask) / 2
            
            last = float(ticker.last) if ticker.last and ticker.last > 0 else 0.0
            if last > 0:
                ib.cancelMktData(contract)
                return last
        
        ib.cancelMktData(contract)

        # 2. If market closed/no data, try historical last bar
        bars = ib.reqHistoricalData(
            contract, endDateTime="", durationStr="1 D",
            barSizeSetting="1 min", whatToShow="MIDPOINT" if contract.secType == "OPT" else "TRADES",
            useRTH=False, formatDate=1, keepUpToDate=False
        )
        if bars:
            return float(bars[-1].close)
            
        return None
    except Exception:
        return None


def _expiry_key(expiry: str | None) -> str:
    if not expiry:
        return ""
    return expiry.replace("-", "")


def _leg_key(right: str, expiry: str | None, strike: float | int | None) -> tuple[str, str, float]:
    return (str(right), _expiry_key(expiry), float(strike or 0.0))


def _net_option_mark(order: dict, leg_marks: dict[tuple[str, str, float], float]) -> float | None:
    """Return net option/spread mark in order-server price units.

    Debit structures use current liquidation value. Credit spreads use current
    cost to close, matching _signal_pnl's mark convention.
    """
    structure = order.get("structure")
    expiry = order.get("expiry")
    expiry_front = order.get("expiry_front")
    long_strike = order.get("long_strike")
    short_strike = order.get("short_strike")

    if structure == "long_call":
        return leg_marks.get(_leg_key("C", expiry, long_strike))

    if structure == "debit_spread":
        long_mark = leg_marks.get(_leg_key("C", expiry, long_strike))
        short_mark = leg_marks.get(_leg_key("C", expiry, short_strike))
        if long_mark is None or short_mark is None:
            return None
        return round(long_mark - short_mark, 4)

    if structure == "credit_spread":
        long_mark = leg_marks.get(_leg_key("P", expiry, long_strike))
        short_mark = leg_marks.get(_leg_key("P", expiry, short_strike))
        if long_mark is None or short_mark is None:
            return None
        return round(short_mark - long_mark, 4)

    if structure == "diagonal":
        long_mark = leg_marks.get(_leg_key("C", expiry, long_strike))
        short_mark = leg_marks.get(_leg_key("C", expiry_front, short_strike))
        if long_mark is None or short_mark is None:
            return None
        return round(long_mark - short_mark, 4)

    return None


def _row_contracts(row: dict) -> int:
    positions = row.get("positions") or [row["position"]]
    vals = [int(abs(getattr(pos, "position", 0))) for pos in positions]
    return max(vals) if vals else 0


def _row_mark(ib, row: dict) -> float | None:
    order = row["journal"].get("order", {})
    positions = row.get("positions") or [row["position"]]

    if order.get("type") == "shares" or order.get("structure") == "long_call":
        return _get_mark(ib, positions[0].contract)

    leg_marks = {}
    for pos in positions:
        contract = pos.contract
        mark = _get_mark(ib, contract)
        if mark is None:
            continue
        leg_marks[_leg_key(
            contract.right,
            contract.lastTradeDateOrContractMonth,
            contract.strike,
        )] = mark

    return _net_option_mark(order, leg_marks)


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
    """Match live positions to journal entries by ticker + account + leg details.
    One row per orderRef — deduplicates multi-leg structures (e.g. diagonal with 2 OPT legs).
    """
    rows_by_ref: dict[str, dict] = {}
    for pos in positions:
        if pos.contract.secType not in ("OPT", "BAG", "STK"):
            continue

        ticker  = pos.contract.symbol
        account = pos.account

        # Find best matching journal record
        best_rec = None
        best_ref = ""
        for ref, rec in journal.items():
            if rec.get("ticker") != ticker or rec.get("account") != account:
                continue

            # If it's an option, try to verify it belongs to this trade's strikes/expiry
            if pos.contract.secType == "OPT":
                order = rec.get("order", {})
                p_strike = pos.contract.strike
                p_expiry = pos.contract.lastTradeDateOrContractMonth # YYYYMMDD

                j_long_s = order.get("long_strike")
                j_short_s = order.get("short_strike")
                j_exp = (order.get("expiry") or "").replace("-", "")
                j_exp_f = (order.get("expiry_front") or "").replace("-", "")

                # Match one of this journaled trade's exact option legs.
                if (p_strike in (j_long_s, j_short_s)) and (p_expiry in (j_exp, j_exp_f)):
                    best_rec = rec
                    best_ref = ref
                    break
            else:
                # For STK, ticker+account is sufficient
                best_rec = rec
                best_ref = ref
                break

        if best_rec:
            key = best_ref or f"{ticker}:{account}:{id(best_rec)}"
            if key not in rows_by_ref:
                rows_by_ref[key] = {"position": pos, "positions": [pos], "journal": best_rec}
            else:
                rows_by_ref[key]["positions"].append(pos)
    return list(rows_by_ref.values())


# ── display ───────────────────────────────────────────────────────────────────

# ── dashboard helper ─────────────────────────────────────────────────────────

def get_portfolio_data() -> dict:
    """Consolidated logic for the Web Dashboard."""
    ib = _connect()
    if not ib:
        return {"ok": False, "error": "Could not connect to TWS"}
        
    try:
        journal = _load_journal_options(None)
        positions = ib.positions()
        rows = _match_positions_to_journal(positions, journal)
        
        data = []
        today = date.today()
        
        for row in rows:
            pos = row["position"]
            rec = row["journal"]
            order = rec.get("order", {})
            
            ticker = rec.get("ticker", pos.contract.symbol)
            account = rec.get("account", pos.account)
            structure = order.get("structure", "?")
            
            # Fetch mark. Multi-leg option rows use net spread/diagonal mark.
            mark = _row_mark(ib, row)
            
            # DTE
            expiry_str = order.get("expiry", "")
            try:
                exp_date = date.fromisoformat(expiry_str)
                dte = (exp_date - today).days
            except:
                dte = -1
                
            # Signals
            signals = []
            target_val = order.get("target_value") or order.get("target")
            stop_val = order.get("stop") or (order.get("entry_price", 0) - C.STOP_ATR_MULT * order.get("atr", 0))
            
            if target_val and stop_val:
                underlying = _underlying_price(ib, ticker)
                if underlying:
                    trig, reason = _signal_price(underlying, float(target_val), float(stop_val))
                    if trig: signals.append(f"price: {reason}")
            
            if mark is not None:
                trig, reason = _signal_pnl(
                    structure=structure, mark=mark,
                    net_debit=order.get("net_debit"), net_credit=order.get("net_credit"),
                    max_loss_per_contract=order.get("max_loss_per_contract", 0),
                    contracts=order.get("contracts", _row_contracts(row))
                )
                if trig: signals.append(reason)
                
            if dte >= 0:
                trig, reason = _signal_dte(structure=structure, dte=dte)
                if trig: signals.append(reason)
                
            # Formatting
            if not signals:
                signal_state, signal_text = "HOLD", "Holding steady"
            elif any("stop" in s.lower() or "loss" in s.lower() for s in signals):
                signal_state, signal_text = "CLOSE_DANGER", "⛔ " + signals[0]
            else:
                signal_state, signal_text = "CLOSE_WARN", "⚠ " + signals[0]
                
            # PnL%
            pnl_str = "?"
            if mark is not None:
                if order.get("type") == "shares" and (entry_px := order.get("entry")):
                    pnl = (mark - entry_px) / entry_px * 100
                    pnl_str = f"{pnl:+.1f}%"
                elif (net_debit := order.get("net_debit")) and net_debit > 0:
                    contracts = _row_contracts(row)
                    pnl = (mark * 100 * contracts - net_debit * 100 * contracts) / (net_debit * 100 * contracts) * 100
                    pnl_str = f"{pnl:+.0f}%"
                elif (net_credit := order.get("net_credit")) and net_credit > 0:
                    max_loss = order.get("max_loss_per_contract", 0)
                    contracts = _row_contracts(row)
                    pnl = (net_credit * 100 * contracts - mark * 100 * contracts) / (max_loss * contracts) * 100
                    pnl_str = f"{pnl:+.0f}%"

            data.append({
                "ticker": ticker,
                "account": account,
                "structure": structure,
                "mark": round(mark, 2) if mark else None,
                "pnl_str": pnl_str,
                "dte": dte,
                "signal_state": signal_state,
                "signal_text": signal_text
            })
            
        return {"ok": True, "positions": data}
    finally:
        ib.disconnect()


def _render_table(rows, ib):
    """CLI table renderer — called by run()."""
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
        contracts = order.get("contracts", _row_contracts(row))
        expiry_str = order.get("expiry", "")
        limit_px  = order.get("limit_price", 0)

        try:
            exp_date = date.fromisoformat(expiry_str)
            dte = (exp_date - today).days
        except Exception:
            dte = -1

        mark = _row_mark(ib, row)
        mark_str = f"${mark:.2f}" if mark else "STALE"

        pnl_str = "?"
        if mark is not None:
            if order.get("type") == "shares" and (entry_px := order.get("entry")):
                pnl = (mark - entry_px) / entry_px * 100
                pnl_str = f"{pnl:+.1f}%"
            elif net_debit and net_debit > 0:
                pnl = (mark * 100 * contracts - net_debit * 100 * contracts) / (net_debit * 100 * contracts) * 100
                pnl_str = f"{pnl:+.0f}%"
            elif net_credit and net_credit > 0:
                pnl = (net_credit * 100 * contracts - mark * 100 * contracts) / (max_loss * contracts) * 100
                pnl_str = f"{pnl:+.0f}%"

        signals = []
        target_val = order.get("target_value") or order.get("target")
        stop_val   = order.get("stop") or (order.get("entry_price", 0) - C.STOP_ATR_MULT * order.get("atr", 0))
        if target_val and stop_val:
            underlying = _underlying_price(ib, ticker)
            if underlying:
                trig, reason = _signal_price(underlying, float(target_val), float(stop_val))
                if trig:
                    signals.append(f"price: {reason}")
        if mark is not None:
            trig, reason = _signal_pnl(
                structure=structure, mark=mark,
                net_debit=net_debit, net_credit=net_credit,
                max_loss_per_contract=max_loss, contracts=contracts,
            )
            if trig:
                signals.append(reason)
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
