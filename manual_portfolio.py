"""
manual_portfolio.py — Discretionary options combo tracker
Tracks manual trades (SPX, RUT, ES, etc.) outside the STFS-EQ system.

Cost basis is read from manual_combos.yaml — never from TWS averageCost.
TWS clientId=19, read-only. Isolated: never touches trade_journal.jsonl or portfolio_manager.py.

Usage:
    python3.11 manual_portfolio.py                    # single run, all combos
    python3.11 manual_portfolio.py --watch 60         # refresh every 60 s
    python3.11 manual_portfolio.py --combo "SPX MAY"  # filter by name substring
"""

from __future__ import annotations

import argparse
import asyncio
import math
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

TWS_HOST    = "127.0.0.1"
TWS_PORT    = 7496
TWS_CLIENT  = 19
COMBOS_FILE = Path("manual_combos.yaml")
ET          = ZoneInfo("America/New_York")
POLL_SECS   = 4   # seconds to wait for market data after batch request


# ── config loader ─────────────────────────────────────────────────────────────

def _load_combos(combo_filter: str | None = None) -> list[dict]:
    if not COMBOS_FILE.exists():
        raise FileNotFoundError(
            f"{COMBOS_FILE} not found. "
            "Copy the template and fill in your trades."
        )
    with COMBOS_FILE.open() as f:
        data = yaml.safe_load(f)
    combos = data.get("combos", [])
    if combo_filter:
        combos = [c for c in combos if combo_filter.lower() in c["name"].lower()]
    return combos


# ── contract factory ──────────────────────────────────────────────────────────

def _make_contract(leg: dict):
    from ib_insync import Option, FuturesOption

    expiry   = leg["expiry"].replace("-", "")   # YYYYMMDD
    symbol   = leg["symbol"].upper()
    right    = leg["right"].upper()
    strike   = float(leg["strike"])
    exchange = leg.get("exchange", "SMART").upper()
    sectype  = leg.get("sectype",  "OPT").upper()
    mult     = str(int(leg.get("multiplier", 100)))
    tc       = leg.get("trading_class", "")

    if sectype == "FOP":
        c = FuturesOption(symbol, expiry, strike, right, exchange)
    else:
        c = Option(symbol, expiry, strike, right, exchange)

    c.multiplier = mult
    if tc:
        c.tradingClass = tc
    return c


# ── TWS connection ────────────────────────────────────────────────────────────

def _connect():
    try:
        from ib_insync import IB
    except ImportError:
        print("ERROR: ib_insync not installed")
        return None
    try:
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        ib = IB()
        ib.connect(TWS_HOST, TWS_PORT, clientId=TWS_CLIENT, timeout=5, readonly=True)
        print(f"  ✓ Connected to TWS (clientId={TWS_CLIENT})")
        return ib
    except Exception as e:
        print(f"ERROR: Could not connect to TWS: {e}")
        return None


# ── market data ───────────────────────────────────────────────────────────────

def _fetch_marks(ib, contracts: list) -> list[dict]:
    """
    Batch-request market data for all contracts. One POLL_SECS wait covers all.
    Returns list[{mark, delta, gamma, theta, vega}] aligned with contracts.
    """
    tickers = [
        ib.reqMktData(c, genericTickList="", snapshot=False, regulatorySnapshot=False)
        for c in contracts
    ]
    ib.sleep(POLL_SECS)

    results = []
    for contract, ticker in zip(contracts, tickers):
        ib.cancelMktData(contract)

        bid  = ticker.bid  if ticker.bid  and math.isfinite(ticker.bid)  and ticker.bid  > 0 else None
        ask  = ticker.ask  if ticker.ask  and math.isfinite(ticker.ask)  and ticker.ask  > 0 else None
        last = ticker.last if ticker.last and math.isfinite(ticker.last) and ticker.last > 0 else None

        g = ticker.modelGreeks
        model_px = g.optPrice if g and g.optPrice is not None and math.isfinite(g.optPrice) else None

        if bid and ask:
            mark = (bid + ask) / 2
        elif last:
            mark = last
        elif model_px:
            mark = model_px
        else:
            mark = None

        def _greek(attr):
            v = getattr(g, attr, None) if g else None
            return v if v is not None and math.isfinite(v) else None

        results.append({
            "mark":  mark,
            "delta": _greek("delta"),
            "gamma": _greek("gamma"),
            "theta": _greek("theta"),
            "vega":  _greek("vega"),
        })

    return results


# ── display ───────────────────────────────────────────────────────────────────

_W = 95   # table width

def _dte(expiry_iso: str) -> int:
    try:
        return (date.fromisoformat(expiry_iso) - date.today()).days
    except Exception:
        return -1


def _exp_label(expiry_iso: str) -> str:
    try:
        return date.fromisoformat(expiry_iso).strftime("%d%b").upper()
    except Exception:
        return expiry_iso


def _fmt_greek(v: float | None, fmt: str = "+.3f") -> str:
    return f"{v:{fmt}}" if v is not None else "   ?"


def _print_combo(combo: dict, market: list[dict]) -> None:
    legs = combo["legs"]
    name = combo["name"]
    dte  = _dte(legs[0]["expiry"]) if legs else -1
    dte_str = f"DTE: {dte}" if dte >= 0 else "DTE: ?"

    print("═" * _W)
    print(f"  {name:<70} {dte_str:>10}")
    print("─" * _W)
    print(
        f"  {'Leg':<24} {'Qty':>4}  {'Fill':>7}  {'Mark':>7}  {'P&L':>9}"
        f"  {'Delta':>7}  {'Gamma':>7}  {'Theta':>7}  {'Vega':>7}"
    )
    print("─" * _W)

    total_pnl = total_delta = total_gamma = total_theta = total_vega = 0.0
    any_missing = False
    greek_complete = True

    for i, leg in enumerate(legs):
        qty  = int(leg["qty"])
        fill = float(leg["fill"])
        mult = float(leg.get("multiplier", 100))
        mk   = market[i] if i < len(market) else {}
        mark = mk.get("mark")

        # P&L: (mark - fill) × qty × multiplier
        if mark is not None:
            pnl = (mark - fill) * qty * mult
            pnl_str = f"${pnl:>+9.0f}"
            total_pnl += pnl
        else:
            pnl_str = "        ?"
            any_missing = True

        # Position greeks = contract greek × qty
        def pos(key):
            v = mk.get(key)
            return v * qty if v is not None else None

        pd, pg, pt, pv = pos("delta"), pos("gamma"), pos("theta"), pos("vega")
        if pd is not None: total_delta += pd
        if pg is not None: total_gamma += pg
        if pt is not None: total_theta += pt
        if pv is not None: total_vega  += pv
        if None in (pd, pg, pt, pv): greek_complete = False

        leg_label = (
            f"{leg['symbol']} {leg['strike']}{leg['right'].upper()} {_exp_label(leg['expiry'])}"
        )
        mark_str = f"${mark:>6.2f}" if mark is not None else "      ?"

        print(
            f"  {leg_label:<24} {qty:>+4}  ${fill:>6.2f}  {mark_str}  {pnl_str}"
            f"  {_fmt_greek(pd):>7}  {_fmt_greek(pg, '.4f'):>7}"
            f"  {_fmt_greek(pt):>7}  {_fmt_greek(pv):>7}"
        )

    print("─" * _W)
    pnl_total = f"${total_pnl:>+9.0f}" + ("*" if any_missing else " ")
    g_flag = "" if greek_complete else "*"
    print(
        f"  {'TOTAL':<24} {'':>4}  {'':>7}  {'':>7}  {pnl_total}"
        f"  {total_delta:>+7.3f}  {total_gamma:>7.4f}"
        f"  {total_theta:>+7.3f}  {total_vega:>+7.3f}{g_flag}"
    )
    if any_missing:
        print("  * Mark unavailable for ≥1 leg — P&L is partial")
    if not greek_complete:
        print("  * Greeks unavailable for ≥1 leg — totals are partial")
    print()


# ── dashboard API ─────────────────────────────────────────────────────────────

def get_combo_data(combo_filter: str | None = None) -> dict:
    """Return structured combo data for the web dashboard."""
    try:
        combos = _load_combos(combo_filter)
    except FileNotFoundError as e:
        return {"ok": False, "error": str(e)}

    if not combos:
        return {"ok": True, "combos": []}

    ib = _connect()
    if ib is None:
        return {"ok": False, "error": "Could not connect to TWS"}

    try:
        all_contracts: list[list] = []
        for combo in combos:
            leg_contracts = [_make_contract(leg) for leg in combo["legs"]]
            try:
                ib.qualifyContracts(*leg_contracts)
            except Exception:
                pass
            all_contracts.append(leg_contracts)

        flat_contracts = [c for leg_cs in all_contracts for c in leg_cs]
        flat_market    = _fetch_marks(ib, flat_contracts)

        result_combos = []
        idx = 0
        for combo, leg_contracts in zip(combos, all_contracts):
            n      = len(leg_contracts)
            market = flat_market[idx : idx + n]
            idx   += n

            legs_out: list[dict] = []
            total_pnl = total_delta = total_gamma = total_theta = total_vega = 0.0
            partial = greek_partial = False

            for i, leg in enumerate(combo["legs"]):
                qty  = int(leg["qty"])
                fill = float(leg["fill"])
                mult = float(leg.get("multiplier", 100))
                mk   = market[i] if i < len(market) else {}
                mark = mk.get("mark")

                pnl = (mark - fill) * qty * mult if mark is not None else None
                if pnl is not None:
                    total_pnl += pnl
                else:
                    partial = True

                def pos(key):
                    v = mk.get(key)
                    return v * qty if v is not None else None

                pd, pg, pt, pv = pos("delta"), pos("gamma"), pos("theta"), pos("vega")
                if pd is not None: total_delta += pd
                if pg is not None: total_gamma += pg
                if pt is not None: total_theta += pt
                if pv is not None: total_vega  += pv
                if None in (pd, pg, pt, pv): greek_partial = True

                label = (
                    f"{leg['symbol']} {leg['strike']}{leg['right'].upper()}"
                    f" {_exp_label(leg['expiry'])}"
                )
                legs_out.append({
                    "label": label,
                    "qty":   qty,
                    "fill":  fill,
                    "mark":  round(mark, 2) if mark is not None else None,
                    "pnl":   round(pnl,  0) if pnl  is not None else None,
                    "delta": round(pd,   3) if pd    is not None else None,
                    "gamma": round(pg,   4) if pg    is not None else None,
                    "theta": round(pt,   3) if pt    is not None else None,
                    "vega":  round(pv,   3) if pv    is not None else None,
                })

            dte = _dte(combo["legs"][0]["expiry"]) if combo["legs"] else -1
            result_combos.append({
                "name": combo["name"],
                "dte":  dte,
                "legs": legs_out,
                "total": {
                    "pnl":   round(total_pnl,   0),
                    "delta": round(total_delta,  3),
                    "gamma": round(total_gamma,  4),
                    "theta": round(total_theta,  3),
                    "vega":  round(total_vega,   3),
                },
                "partial":      partial,
                "greek_partial": greek_partial,
            })

        return {"ok": True, "combos": result_combos}
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


# ── main ──────────────────────────────────────────────────────────────────────

def run(combo_filter: str | None = None, watch_interval: int | None = None) -> None:
    combos = _load_combos(combo_filter)
    if not combos:
        msg = f" matching '{combo_filter}'" if combo_filter else ""
        print(f"No combos found{msg} in {COMBOS_FILE}.")
        return

    ib = _connect()
    if ib is None:
        return

    try:
        # Build and qualify all contracts once — reused across watch iterations
        all_contracts: list[list] = []
        for combo in combos:
            leg_contracts = [_make_contract(leg) for leg in combo["legs"]]
            try:
                ib.qualifyContracts(*leg_contracts)
            except Exception as e:
                print(f"  WARNING: could not qualify some legs in '{combo['name']}': {e}")
            all_contracts.append(leg_contracts)

        while True:
            ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
            print(f"\n  Live as of {ts}\n")

            # One batch fetch covers all combos — single POLL_SECS wait
            flat_contracts = [c for leg_cs in all_contracts for c in leg_cs]
            flat_market    = _fetch_marks(ib, flat_contracts)

            # Re-slice per combo
            idx = 0
            for combo, leg_contracts in zip(combos, all_contracts):
                n = len(leg_contracts)
                _print_combo(combo, flat_market[idx : idx + n])
                idx += n

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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manual options combo tracker")
    parser.add_argument(
        "--watch", type=int, default=None, metavar="N",
        help="Refresh every N seconds (default: run once)",
    )
    parser.add_argument(
        "--combo", type=str, default=None,
        help="Filter to combos whose name contains this string (case-insensitive)",
    )
    args = parser.parse_args()
    run(combo_filter=args.combo, watch_interval=args.watch)
