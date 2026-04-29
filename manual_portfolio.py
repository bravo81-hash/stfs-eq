"""
manual_portfolio.py — Discretionary options combo tracker
Tracks manual trades (SPX, RUT, ES, etc.) outside the STFS-EQ system.

ARCHITECTURE: YAML is the sole source of truth.
  - ib.positions() is NEVER called. TWS account data is ignored entirely.
  - Legs come from manual_combos.yaml (your exact fills, quantities, strikes).
  - TWS is used ONLY to fetch live mark prices and greeks for those specific contracts.
  - P&L = (live mark - yaml fill) × yaml qty × multiplier. Never uses TWS cost basis.

TWS clientId=19, read-only. Isolated from trade_journal.jsonl and portfolio_manager.py.

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
POLL_SECS   = 6   # seconds to wait for market data after batch request


# ── config loader ─────────────────────────────────────────────────────────────

def _load_combos(combo_filter: str | None = None) -> list[dict]:
    if not COMBOS_FILE.exists():
        raise FileNotFoundError(
            f"{COMBOS_FILE} not found — fill in your real trades first."
        )
    with COMBOS_FILE.open() as f:
        data = yaml.safe_load(f)
    combos = data.get("combos", [])
    if combo_filter:
        combos = [c for c in combos if combo_filter.lower() in c["name"].lower()]
    return combos


# ── contract factory ──────────────────────────────────────────────────────────

def _make_contract(leg: dict):
    """
    Build a TWS contract from a YAML leg definition.
    Never reads ib.positions() — only the leg dict is used.

    For SPX/RUT index options:
      - exchange defaults to CBOE (cash-settled index options don't route via SMART)
      - set trading_class: SPXW for weekly expirations, SPX for monthly (3rd Friday)
      - multiplier is intentionally NOT pre-set so qualifyContracts can resolve it
    For ES/NQ futures options:
      - set sectype: FOP, exchange: CME, multiplier: 50
    """
    from ib_insync import Option, FuturesOption

    expiry  = leg["expiry"].replace("-", "")   # YYYYMMDD
    symbol  = leg["symbol"].upper()
    right   = leg["right"].upper()
    strike  = float(leg["strike"])
    sectype = leg.get("sectype", "OPT").upper()
    tc      = leg.get("trading_class", "")

    # Default exchange: CBOE for cash-settled index options, SMART for equities
    exchange = leg.get("exchange", "CBOE" if symbol in ("SPX", "RUT", "NDX", "VIX") else "SMART").upper()

    if sectype == "FOP":
        c = FuturesOption(symbol, expiry, strike, right, exchange)
    else:
        c = Option(symbol, expiry, strike, right, exchange)

    if tc:
        c.tradingClass = tc

    # Only set multiplier for FOP where TWS needs a hint; OPT multiplier resolved by qualifyContracts
    if sectype == "FOP" and "multiplier" in leg:
        c.multiplier = str(int(leg["multiplier"]))

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


# ── contract qualification ────────────────────────────────────────────────────

def _qualify_leg(ib, contract, leg_label: str) -> str | None:
    """
    Qualify a single contract. Returns None on success, error string on failure.
    Checks conId > 0 after qualification — unqualified contracts silently produce no data.
    """
    try:
        ib.qualifyContracts(contract)
    except Exception as e:
        return f"qualifyContracts failed: {e}"

    if not contract.conId or contract.conId == 0:
        return (
            f"conId=0 after qualify — contract not found. "
            f"Check exchange/trading_class in YAML. "
            f"For SPX weeklies set trading_class: SPXW; for monthlies: SPX."
        )
    return None


# ── market data ───────────────────────────────────────────────────────────────

def _fetch_marks(ib, qualified: list[tuple]) -> list[dict]:
    """
    Batch-request market data for all qualified contracts.
    qualified: list of (contract, error_str_or_None)
    Returns list[{mark, delta, gamma, theta, vega, error}] aligned with input.

    Only requests data for contracts that qualified successfully (conId > 0).
    One POLL_SECS wait covers the entire batch — much faster than sequential.
    ib.positions() is not called anywhere in this function.
    """
    # Subscribe only qualified contracts
    tickers: dict[int, object] = {}   # index → Ticker
    for i, (contract, err) in enumerate(qualified):
        if err is None:
            tickers[i] = ib.reqMktData(
                contract, genericTickList="", snapshot=False, regulatorySnapshot=False
            )

    ib.sleep(POLL_SECS)

    results = []
    for i, (contract, err) in enumerate(qualified):
        if err is not None:
            results.append({"mark": None, "delta": None, "gamma": None,
                            "theta": None, "vega": None, "error": err})
            continue

        ib.cancelMktData(contract)
        ticker = tickers[i]

        bid  = ticker.bid  if ticker.bid  and math.isfinite(ticker.bid)  and ticker.bid  > 0 else None
        ask  = ticker.ask  if ticker.ask  and math.isfinite(ticker.ask)  and ticker.ask  > 0 else None
        last = ticker.last if ticker.last and math.isfinite(ticker.last) and ticker.last > 0 else None

        g        = ticker.modelGreeks
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
            "error": None if mark is not None else "no market data (market closed?)",
        })

    return results


# ── P&L and greek aggregation ─────────────────────────────────────────────────

def _aggregate(combo: dict, market: list[dict]) -> tuple[list[dict], dict, bool, bool]:
    """
    Pure function — no TWS calls. Aggregates P&L and greeks from YAML fills + live marks.
    Returns (legs_out, total, any_partial, any_error).
    """
    legs_out: list[dict] = []
    total_pnl = total_delta = total_gamma = total_theta = total_vega = 0.0
    any_partial = any_error = False

    for i, leg in enumerate(combo["legs"]):
        qty  = int(leg["qty"])
        fill = float(leg["fill"])
        mult = float(leg.get("multiplier", 100))
        mk   = market[i] if i < len(market) else {}
        mark = mk.get("mark")
        err  = mk.get("error")

        pnl = (mark - fill) * qty * mult if mark is not None else None
        if pnl is not None:
            total_pnl += pnl
        else:
            any_partial = True
        if err:
            any_error = True

        # Position greek = contract greek × signed qty
        def pos(key, q=qty):
            v = mk.get(key)
            return v * q if v is not None else None

        pd, pg, pt, pv = pos("delta"), pos("gamma"), pos("theta"), pos("vega")
        if pd is not None: total_delta += pd
        if pg is not None: total_gamma += pg
        if pt is not None: total_theta += pt
        if pv is not None: total_vega  += pv

        exp_label = _exp_label(leg["expiry"])
        legs_out.append({
            "label": f"{leg['symbol']} {leg['strike']}{leg['right'].upper()} {exp_label}",
            "qty":   qty,
            "fill":  fill,
            "mark":  round(mark, 2) if mark is not None else None,
            "pnl":   round(pnl,  0) if pnl  is not None else None,
            "delta": round(pd,   3) if pd    is not None else None,
            "gamma": round(pg,   4) if pg    is not None else None,
            "theta": round(pt,   3) if pt    is not None else None,
            "vega":  round(pv,   3) if pv    is not None else None,
            "error": err,
        })

    total = {
        "pnl":   round(total_pnl,   0),
        "delta": round(total_delta,  3),
        "gamma": round(total_gamma,  4),
        "theta": round(total_theta,  3),
        "vega":  round(total_vega,   3),
    }
    return legs_out, total, any_partial, any_error


# ── display helpers ───────────────────────────────────────────────────────────

_W = 95

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
    legs      = combo["legs"]
    dte_str   = f"DTE: {_dte(legs[0]['expiry'])}" if legs else "DTE: ?"
    legs_out, total, any_partial, any_error = _aggregate(combo, market)

    print("═" * _W)
    print(f"  {combo['name']:<70} {dte_str:>10}")
    print("─" * _W)
    print(
        f"  {'Leg':<24} {'Qty':>4}  {'Fill':>7}  {'Mark':>7}  {'P&L':>9}"
        f"  {'Delta':>7}  {'Gamma':>7}  {'Theta':>7}  {'Vega':>7}"
    )
    print("─" * _W)

    for lo in legs_out:
        mark_str = f"${lo['mark']:>6.2f}" if lo["mark"] is not None else "      ?"
        pnl_str  = f"${lo['pnl']:>+9.0f}" if lo["pnl"] is not None else "        ?"
        err_tag  = f"  ← {lo['error']}" if lo["error"] else ""
        print(
            f"  {lo['label']:<24} {lo['qty']:>+4}  ${lo['fill']:>6.2f}  {mark_str}  {pnl_str}"
            f"  {_fmt_greek(lo['delta']):>7}  {_fmt_greek(lo['gamma'], '.4f'):>7}"
            f"  {_fmt_greek(lo['theta']):>7}  {_fmt_greek(lo['vega']):>7}{err_tag}"
        )

    t = total
    print("─" * _W)
    pnl_total = f"${t['pnl']:>+9.0f}" + ("*" if any_partial else " ")
    print(
        f"  {'TOTAL':<24} {'':>4}  {'':>7}  {'':>7}  {pnl_total}"
        f"  {t['delta']:>+7.3f}  {t['gamma']:>7.4f}  {t['theta']:>+7.3f}  {t['vega']:>+7.3f}"
    )
    if any_error:
        print("  * One or more legs failed — check error messages above")
    print()


# ── shared TWS fetch logic ────────────────────────────────────────────────────

def _build_and_fetch(ib, combos: list[dict]) -> list[list[dict]]:
    """
    Qualify all legs from all combos, then batch-fetch market data.
    Returns list[list[dict]] — market data per combo, per leg.
    Never calls ib.positions().
    """
    # Build contracts from YAML legs
    all_qualified: list[list[tuple]] = []   # [combo_idx][leg_idx] = (contract, err)
    for combo in combos:
        leg_qualified = []
        for leg in combo["legs"]:
            contract = _make_contract(leg)
            label    = f"{leg['symbol']} {leg['strike']}{leg['right'].upper()} {leg['expiry']}"
            err      = _qualify_leg(ib, contract, label)
            if err:
                print(f"  WARNING [{combo['name']}] {label}: {err}")
            leg_qualified.append((contract, err))
        all_qualified.append(leg_qualified)

    # Flatten and batch-fetch all qualified contracts in one pass
    flat_qualified = [item for legs in all_qualified for item in legs]
    flat_market    = _fetch_marks(ib, flat_qualified)

    # Re-slice per combo
    result: list[list[dict]] = []
    idx = 0
    for leg_qualified in all_qualified:
        n = len(leg_qualified)
        result.append(flat_market[idx : idx + n])
        idx += n
    return result


# ── dashboard API ─────────────────────────────────────────────────────────────

def get_combo_data(combo_filter: str | None = None) -> dict:
    """Structured combo data for the web dashboard. Never calls ib.positions()."""
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
        all_market = _build_and_fetch(ib, combos)

        result_combos = []
        for combo, market in zip(combos, all_market):
            legs_out, total, any_partial, any_error = _aggregate(combo, market)
            dte = _dte(combo["legs"][0]["expiry"]) if combo["legs"] else -1
            result_combos.append({
                "name":         combo["name"],
                "dte":          dte,
                "legs":         legs_out,
                "total":        total,
                "partial":      any_partial,
                "has_error":    any_error,
            })

        return {"ok": True, "combos": result_combos}
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


# ── CLI entry point ───────────────────────────────────────────────────────────

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
        while True:
            ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
            print(f"\n  Live as of {ts}\n")
            all_market = _build_and_fetch(ib, combos)
            for combo, market in zip(combos, all_market):
                _print_combo(combo, market)
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
    parser.add_argument("--watch", type=int, default=None, metavar="N",
                        help="Refresh every N seconds (default: run once)")
    parser.add_argument("--combo", type=str, default=None,
                        help="Filter to combos whose name contains this string")
    args = parser.parse_args()
    run(combo_filter=args.combo, watch_interval=args.watch)
