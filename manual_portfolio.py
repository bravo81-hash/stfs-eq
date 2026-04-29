"""
manual_portfolio.py — Discretionary options combo tracker
Tracks manual trades (SPX, RUT, ES, etc.) outside the STFS-EQ system.

ARCHITECTURE: JSON is the sole source of truth.
  - Legs come from data/manual_combos.json (your exact fills, quantities, conIds).
  - TWS is used ONLY to fetch live mark prices and greeks for those specific contracts.
  - P&L = (live mark - costBasis) × qty × multiplier. Never uses TWS cost basis.

TWS clientId=19, read-only. Isolated from trade_journal.jsonl and portfolio_manager.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TWS_HOST    = "127.0.0.1"
TWS_PORT    = 7496
TWS_CLIENT  = 19
COMBOS_FILE = Path("data/manual_combos.json")
ET          = ZoneInfo("America/New_York")
POLL_SECS   = 6

def _load_combos(combo_filter: str | None = None) -> list[dict]:
    if not COMBOS_FILE.exists():
        return []
    try:
        with COMBOS_FILE.open() as f:
            combos = json.load(f)
    except Exception:
        return []
        
    if combo_filter:
        combos = [c for c in combos if combo_filter.lower() in c.get("name", "").lower()]
    return combos

def save_combo(data: dict):
    combos = _load_combos()
    combos.append(data)
    COMBOS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with COMBOS_FILE.open("w") as f:
        json.dump(combos, f, indent=2)

def _make_contract(leg: dict):
    from ib_insync import Contract
    return Contract(conId=int(leg["conId"]))

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
        return ib
    except Exception as e:
        print(f"ERROR: Could not connect to TWS: {e}")
        return None

def _qualify_leg(ib, contract) -> str | None:
    try:
        ib.qualifyContracts(contract)
    except Exception as e:
        return f"qualifyContracts failed: {e}"

    if not contract.conId or contract.conId == 0:
        return "conId=0 after qualify — contract not found."
    return None

def _fetch_marks(ib, qualified: list[tuple]) -> list[dict]:
    tickers: dict[int, object] = {}
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
            "error": None if mark is not None else "no market data",
        })

    return results

def _exp_label(expiry_iso: str) -> str:
    if not expiry_iso: return "?"
    try:
        if len(expiry_iso) == 8 and "-" not in expiry_iso:
            return f"{expiry_iso[6:]}{date(int(expiry_iso[:4]), int(expiry_iso[4:6]), int(expiry_iso[6:])).strftime('%b').upper()}"
        return date.fromisoformat(expiry_iso).strftime("%d%b").upper()
    except Exception:
        return expiry_iso

def _dte(expiry_iso: str) -> int:
    if not expiry_iso: return -1
    try:
        if len(expiry_iso) == 8 and "-" not in expiry_iso:
            d = date(int(expiry_iso[:4]), int(expiry_iso[4:6]), int(expiry_iso[6:]))
        else:
            d = date.fromisoformat(expiry_iso)
        return (d - date.today()).days
    except Exception:
        return -1

def _aggregate(combo: dict, market: list[dict], qualified: list[tuple]) -> tuple[list[dict], dict, bool, bool]:
    legs_out: list[dict] = []
    total_pnl = total_delta = total_gamma = total_theta = total_vega = 0.0
    any_partial = any_error = False

    for i, leg in enumerate(combo["legs"]):
        qty  = int(leg.get("qty", 0))
        cb   = float(leg.get("costBasis", 0))
        mult = float(leg.get("multiplier", 100))
        mk   = market[i] if i < len(market) else {}
        mark = mk.get("mark")
        err  = mk.get("error")

        contract, q_err = qualified[i] if i < len(qualified) else (None, "missing")
        
        if contract and contract.multiplier:
            try: mult = float(contract.multiplier)
            except: pass

        pnl = (mark - cb) * qty * mult if mark is not None else None
        if pnl is not None:
            total_pnl += pnl
        else:
            any_partial = True
        if err:
            any_error = True

        def pos_orig(key, q=qty):
            v = mk.get(key)
            return v * q * mult if v is not None else None

        pd, pg, pt, pv = pos_orig("delta"), pos_orig("gamma"), pos_orig("theta"), pos_orig("vega")
        if pd is not None: total_delta += pd
        if pg is not None: total_gamma += pg
        if pt is not None: total_theta += pt
        if pv is not None: total_vega  += pv

        if contract:
            sym = contract.symbol or "?"
            strike = contract.strike or "?"
            right = contract.right or "?"
            expiry = contract.lastTradeDateOrContractMonth or ""
            exp_label = _exp_label(expiry)
            label = f"{sym} {strike}{right} {exp_label}"
        else:
            label = f"ConId: {leg.get('conId')}"
            expiry = ""

        legs_out.append({
            "label": label,
            "expiry": expiry,
            "qty":   qty,
            "fill":  cb,
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

def _build_and_fetch(ib, combos: list[dict]) -> tuple[list[list[dict]], list[list[tuple]]]:
    all_qualified: list[list[tuple]] = []
    for combo in combos:
        leg_qualified = []
        for leg in combo["legs"]:
            contract = _make_contract(leg)
            err = _qualify_leg(ib, contract)
            leg_qualified.append((contract, err))
        all_qualified.append(leg_qualified)

    flat_qualified = [item for legs in all_qualified for item in legs]
    flat_market    = _fetch_marks(ib, flat_qualified)

    result_market: list[list[dict]] = []
    idx = 0
    for leg_qualified in all_qualified:
        n = len(leg_qualified)
        result_market.append(flat_market[idx : idx + n])
        idx += n
    return result_market, all_qualified

def get_combo_data(combo_filter: str | None = None) -> dict:
    combos = _load_combos(combo_filter)
    if not combos:
        return {"ok": True, "combos": []}

    ib = _connect()
    if ib is None:
        return {"ok": False, "error": "Could not connect to TWS"}

    try:
        all_market, all_qualified = _build_and_fetch(ib, combos)
        result_combos = []
        for combo, market, qualified in zip(combos, all_market, all_qualified):
            legs_out, total, any_partial, any_error = _aggregate(combo, market, qualified)
            dtes = [_dte(l["expiry"]) for l in legs_out if l["expiry"]]
            dte = min(dtes) if dtes else -1
            
            result_combos.append({
                "name":         combo["name"],
                "group":        combo.get("group", "Default"),
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

def get_raw_positions() -> dict:
    ib = _connect()
    if ib is None:
        return {"ok": False, "error": "Could not connect to TWS"}
    try:
        positions = ib.positions()
        opts = []
        for p in positions:
            c = p.contract
            if c.secType in ("OPT", "FOP"):
                opts.append({
                    "conId": c.conId,
                    "symbol": c.symbol,
                    "strike": c.strike,
                    "right": c.right,
                    "expiry": c.lastTradeDateOrContractMonth,
                    "position": p.position,
                    "account": p.account
                })
        return {"ok": True, "positions": opts}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass
