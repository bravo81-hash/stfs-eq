"""
tws_data.py — IBKR TWS data source for STFS-EQ
Primary data source when TWS is running on port 7496 (live account).
All public functions return None/False when TWS is not connected so
battle_card.py falls back to the existing yfinance/Finnhub path.

clientId=15 — change if this conflicts with another API client.
"""

import math
from datetime import date

import numpy as np
import pandas as pd

import config as C

# ── connection ────────────────────────────────────────────────────────────────

_ib = None
_connected = False

TWS_HOST        = "127.0.0.1"
TWS_PORT        = 7496
TWS_CLIENT      = 15     # change if another client is already using 15
CONNECT_TIMEOUT = 5      # seconds

# yfinance uses hyphens; TWS requires spaces for some tickers
_TWS_TICKER = {"BRK-B": "BRK B", "BRK-A": "BRK A"}


def _try_connect() -> bool:
    global _ib, _connected
    try:
        from ib_insync import IB
    except ImportError:
        print("  ℹ  TWS: ib_insync not installed — using yfinance/Finnhub fallback")
        return False
    try:
        ib = IB()
        ib.connect(TWS_HOST, TWS_PORT, clientId=TWS_CLIENT,
                   timeout=CONNECT_TIMEOUT, readonly=True)
        _ib = ib
        _connected = True
        print(f"  ✓ TWS connected (live data mode — {TWS_HOST}:{TWS_PORT} clientId={TWS_CLIENT})")
        return True
    except Exception as e:
        _ib = None
        _connected = False
        print(f"  ℹ  TWS: not connected ({type(e).__name__}: {e}) — using yfinance/Finnhub fallback")
        return False


_try_connect()   # non-blocking at import time; ConnectionRefused fails in <1s


def tws_connected() -> bool:
    """Return True if TWS is currently connected and ready."""
    return _connected and _ib is not None and _ib.isConnected()


# ── internal utilities ────────────────────────────────────────────────────────
# Mirrors battle_card.py helpers — kept here to avoid circular imports.

def _sf(v, d: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except Exception:
        return d


def _si(v, d: int = 0) -> int:
    try:
        x = float(v)
        return int(x) if math.isfinite(x) else d
    except Exception:
        return d


def _iso(tws_date: str) -> str:
    """YYYYMMDD → YYYY-MM-DD"""
    return f"{tws_date[:4]}-{tws_date[4:6]}-{tws_date[6:]}"


def _find_expiry(tws_exps: list, target_dte: int):
    """Return (tws_str, iso_str, actual_dte) from sorted TWS expiration list."""
    today = date.today()

    def _d(s):
        return date(int(s[:4]), int(s[4:6]), int(s[6:]))

    best = min(tws_exps, key=lambda e: abs((_d(e) - today).days - target_dte))
    return best, _iso(best), (_d(best) - today).days


def _atm_row(chain_df: pd.DataFrame, price: float):
    idx = int((chain_df["strike"] - price).abs().argsort().iloc[0])
    return chain_df.iloc[idx]


# ── options structure builders (same logic as battle_card.py) ─────────────────

def _build_long_call(calls, price, dte, expiry_iso):
    atm = _atm_row(calls, price)
    ask = _sf(atm["ask"])
    if ask <= 0:
        return None
    return {
        "structure": "long_call", "label": "Long Call",
        "expiry": expiry_iso, "dte": dte,
        "long_strike": _sf(atm["strike"]), "short_strike": None,
        "net_debit": ask, "net_credit": None,
        "max_loss_per_contract": ask * 100,
        "target_label": "2× debit (100% gain)",
        "target_value": ask * C.LONG_CALL_TARGET_MULT,
        "oi": _si(atm["openInterest"]),
    }


def _build_debit_spread(calls, price, dte, expiry_iso, width):
    atm = _atm_row(calls, price)
    long_strike = _sf(atm["strike"])
    long_ask = _sf(atm["ask"])
    higher = calls[calls["strike"] > long_strike]
    if higher.empty:
        return None
    short_row = _atm_row(higher, price + width)
    short_strike = _sf(short_row["strike"])
    short_bid = _sf(short_row["bid"])
    net_debit = max(0.05, long_ask - short_bid)
    actual_width = short_strike - long_strike
    if actual_width <= 0 or net_debit <= 0:
        return None
    return {
        "structure": "debit_spread",
        "label": f"Bull Call Spread ${actual_width:.0f}w",
        "expiry": expiry_iso, "dte": dte,
        "long_strike": long_strike, "short_strike": short_strike,
        "net_debit": net_debit, "net_credit": None,
        "spread_width": actual_width,
        "max_loss_per_contract": net_debit * 100,
        "max_profit_per_contract": (actual_width - net_debit) * 100,
        "target_label": "2× debit (100% gain)",
        "target_value": net_debit * C.DEBIT_SPREAD_TARGET_MULT,
        "oi": _si(atm["openInterest"]),
    }


def _build_credit_spread(puts, price, dte, expiry_iso, width):
    atm = _atm_row(puts, price)
    short_strike = _sf(atm["strike"])
    short_bid = _sf(atm["bid"])
    lower = puts[puts["strike"] < short_strike]
    if lower.empty:
        return None
    long_row = _atm_row(lower, price - width)
    long_strike = _sf(long_row["strike"])
    long_ask = _sf(long_row["ask"])
    net_credit = max(0.05, short_bid - long_ask)
    actual_width = short_strike - long_strike
    if actual_width <= 0 or net_credit <= 0:
        return None
    return {
        "structure": "credit_spread",
        "label": f"Bull Put Spread ${actual_width:.0f}w",
        "expiry": expiry_iso, "dte": dte,
        "long_strike": long_strike, "short_strike": short_strike,
        "net_debit": None, "net_credit": net_credit,
        "spread_width": actual_width,
        "max_loss_per_contract": (actual_width - net_credit) * 100,
        "max_profit_per_contract": net_credit * 100,
        "target_label": f"50% of credit (${net_credit * C.CREDIT_TARGET_PCT:.2f})",
        "target_value": net_credit * C.CREDIT_TARGET_PCT,
        "oi": _si(atm["openInterest"]),
    }


def _build_diagonal(calls_near, calls_far, price, dte_front, dte_back, exp_front_iso, exp_back_iso):
    short_row = _atm_row(calls_near, price)
    long_row  = _atm_row(calls_far,  price)
    net_debit = max(0.05, _sf(long_row["ask"]) - _sf(short_row["bid"]))
    if net_debit <= 0:
        return None
    return {
        "structure": "diagonal", "label": "Call Diagonal",
        "expiry": exp_back_iso, "dte": dte_back,
        "expiry_front": exp_front_iso, "dte_front": dte_front,
        "long_strike":  _sf(long_row["strike"]),
        "short_strike": _sf(short_row["strike"]),
        "net_debit": net_debit, "net_credit": None,
        "max_loss_per_contract": net_debit * 100,
        "target_label": "1.5× debit (50% gain)",
        "target_value": net_debit * C.DIAGONAL_TARGET_MULT,
        "oi": _si(long_row["openInterest"]),
    }


def _contracts_for_account(plan, acc):
    if plan is None:
        return 0, 0.0
    max_loss = plan.get("max_loss_per_contract", 0)
    if max_loss <= 0:
        return 0, 0.0
    risk_dollars  = acc["equity"] * acc["risk_pct"] / 100.0
    max_notional  = acc["equity"] * acc["max_notional_pct"] / 100.0
    cts_by_risk   = int(risk_dollars / max_loss)
    debit_or_width = plan.get("net_debit") or plan.get("spread_width") or 0
    if debit_or_width > 0 and cts_by_risk > 0:
        cts = min(cts_by_risk, int(max_notional / (debit_or_width * 100)))
    else:
        cts = cts_by_risk
    return cts, max_loss * cts


def _size_options_account(primary_plan, fallback_plans, acc):
    cts, risk = _contracts_for_account(primary_plan, acc)
    used_plan  = primary_plan
    downgraded = False
    if cts == 0:
        for fb in fallback_plans:
            cts, risk = _contracts_for_account(fb, acc)
            if cts > 0:
                used_plan  = fb
                downgraded = True
                break
    min_hint = None
    if cts == 0 and primary_plan:
        ml = primary_plan.get("max_loss_per_contract", 0)
        if ml > 0:
            min_hint = int(math.ceil((ml / (acc["risk_pct"] / 100.0)) / 1000) * 1000)
    notional = ((used_plan.get("net_debit") or used_plan.get("spread_width") or 0)
                * 100 * cts) if cts > 0 else 0
    return {
        "account":      acc["name"],
        "equity":       acc["equity"],
        "contracts":    cts,
        "risk_dollars": risk,
        "notional":     notional,
        "label":        used_plan["label"] if used_plan else "—",
        "downgraded":   downgraded,
        "min_hint":     min_hint,
    }


# ── options chain snapshot fetcher ────────────────────────────────────────────

_STRIKES_EACH_SIDE = 8   # ATM ± this many strikes per chain request


def _fetch_chain_df(ticker: str, exp_tws: str, right: str,
                    all_strikes: list, price: float) -> "pd.DataFrame | None":
    """
    Request snapshot market data for ATM ±_STRIKES_EACH_SIDE strikes.
    Returns DataFrame(strike, bid, ask, impliedVolatility, openInterest) or None.
    Uses reqMktData snapshot=True + ib.sleep(2) to stay within IBKR pacing limits.
    """
    from ib_insync import Option

    sorted_strikes = sorted(all_strikes)
    atm_idx = min(range(len(sorted_strikes)), key=lambda i: abs(sorted_strikes[i] - price))
    lo = max(0, atm_idx - _STRIKES_EACH_SIDE)
    hi = min(len(sorted_strikes), atm_idx + _STRIKES_EACH_SIDE + 1)
    target_strikes = sorted_strikes[lo:hi]

    contracts = [Option(ticker, exp_tws, s, right, "SMART") for s in target_strikes]
    try:
        qualified = _ib.qualifyContracts(*contracts)
    except Exception:
        return None
    if not qualified:
        return None

    ticker_objs = []
    for contract in qualified:
        td = _ib.reqMktData(contract, "", snapshot=True, regulatorySnapshot=False)
        ticker_objs.append((contract, td))

    _ib.sleep(2)   # allow snapshots to arrive within IBKR pacing limits

    rows = []
    for contract, td in ticker_objs:
        _ib.cancelMktData(contract)
        iv = 0.0
        if td.modelGreeks and hasattr(td.modelGreeks, "impliedVol"):
            iv = _sf(td.modelGreeks.impliedVol)
        oi_attr = "callOpenInterest" if right == "C" else "putOpenInterest"
        vol_attr = "callVolume" if right == "C" else "putVolume"
        
        oi = _si(getattr(td, oi_attr, 0) or 0)
        if oi <= 0:
            oi = _si(getattr(td, vol_attr, 0) or 0)
            
        bid = _sf(td.bid) if (td.bid is not None and td.bid > 0) else 0.0
        ask = _sf(td.ask) if (td.ask is not None and td.ask > 0) else 0.0
        
        if bid <= 0 and ask <= 0:
            last = _sf(td.close) if (td.close is not None and td.close > 0) else 0.0
            if last <= 0:
                last = _sf(td.last) if (td.last is not None and td.last > 0) else 0.0
            if last > 0:
                bid, ask = last * 0.95, last * 1.05
        rows.append({
            "strike":            float(contract.strike),
            "bid":               bid,
            "ask":               ask,
            "impliedVolatility": iv,
            "openInterest":      oi,
        })

    if not rows:
        return None
    df = pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)
    return df if not df.empty else None


# ── public API ────────────────────────────────────────────────────────────────

def get_ohlc(tickers: list, lookback_days: int = 300) -> "dict | None":
    """
    Return {ticker: DataFrame(Open,High,Low,Close,Volume)} via TWS reqHistoricalData.
    Returns None if not connected — battle_card.py falls back to yfinance.
    Individual ticker failures are silently skipped so the caller can patch gaps.
    """
    if not tws_connected():
        return None
    try:
        from ib_insync import Stock, util
    except ImportError:
        return None

    out = {}
    for ticker in tickers:
        try:
            tws_sym = _TWS_TICKER.get(ticker, ticker)
            contract = Stock(tws_sym, "SMART", "USD")
            bars = _ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=f"{lookback_days} D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
                keepUpToDate=False,
            )
            if not bars:
                continue
            df = util.df(bars)
            df.index = pd.to_datetime(df["date"])
            df.index.name = None
            df = df.rename(columns={
                "open": "Open", "high": "High", "low": "Low",
                "close": "Close", "volume": "Volume",
            })[["Open", "High", "Low", "Close", "Volume"]].dropna()
            if not df.empty:
                out[ticker] = df
        except Exception:
            pass   # fall through; battle_card fills gap from yfinance

    return out if out else None


def get_positions() -> "list | None":
    """
    Return list of {ticker, shares, avg_cost, account} for all open stock positions.
    Returns None if not connected.
    """
    if not tws_connected():
        return None
    try:
        positions = _ib.positions()
        return [
            {
                "ticker":   p.contract.symbol,
                "shares":   int(p.position),
                "avg_cost": float(p.avgCost),
                "account":  p.account,
            }
            for p in positions
            if p.contract.secType == "STK" and int(p.position) != 0
        ]
    except Exception:
        return None


def get_options_data(ticker: str, df: "pd.DataFrame") -> "dict | None":
    """
    Fetch options chain via TWS and compute IVR + structure selection.
    Returns the same dict shape as battle_card.fetch_options_data, or None
    (signals battle_card to fall back to the yfinance path).

    Improvements over the yfinance path:
    - True 52-week IV Rank (IVR) from reqHistoricalData OPTION_IMPLIED_VOLATILITY
    - Live bid/ask quotes from reqMktData snapshots
    - Real open interest from TWS
    """
    if not tws_connected():
        return None
    try:
        from ib_insync import Stock
    except ImportError:
        return None

    try:
        close  = df["Close"]
        price  = float(close.iloc[-1])

        log_ret = np.log(close / close.shift(1)).dropna()
        hv30 = float(log_ret.tail(30).std() * np.sqrt(252))
        if hv30 <= 0:
            return {"error": "HV30 = 0"}
            
        # Dynamically compute Options Width using ATR
        tr1 = df['High'] - df['Low']
        tr2 = (df['High'] - df['Close'].shift()).abs()
        tr3 = (df['Low'] - df['Close'].shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        at = tr.rolling(14).mean()
        atr_val = float(at.iloc[-1])
        spread_width = atr_val * C.SPREAD_ATR_MULT

        # Qualify stock contract (needed for conId in reqSecDefOptParams)
        tws_sym = _TWS_TICKER.get(ticker, ticker)
        stock = Stock(tws_sym, "SMART", "USD")
        qualified = _ib.qualifyContracts(stock)
        if not qualified:
            return None
        stock = qualified[0]

        # 52-week daily series → min/max range for IV Rank denominator
        iv_bars = _ib.reqHistoricalData(
            stock,
            endDateTime="",
            durationStr="365 D",
            barSizeSetting="1 day",
            whatToShow="OPTION_IMPLIED_VOLATILITY",
            useRTH=True,
            formatDate=1,
            keepUpToDate=False,
        )
        ivr = None
        current_iv = None
        if iv_bars and len(iv_bars) >= 30:
            iv_vals = [b.close for b in iv_bars if b.close and b.close > 0]
            if iv_vals:
                lo_iv, hi_iv = min(iv_vals), max(iv_vals)

                # Current IV via 5-min intraday bars.
                # The 365-day daily bar for *today* is often not yet finalized
                # at or shortly after the 16:00 ET close, so iv_vals[-1] is
                # yesterday's value — causing IVR to read ~20 pts below TWS.
                # The last 5-min bar ending at 16:00 ET is settled immediately.
                # reqMktData tick-106 also fails post-close (options not trading).
                try:
                    intra = _ib.reqHistoricalData(
                        stock,
                        endDateTime="",
                        durationStr="7200 S",   # last 2 hours of 5-min bars
                        barSizeSetting="5 mins",
                        whatToShow="OPTION_IMPLIED_VOLATILITY",
                        useRTH=True,
                        formatDate=1,
                        keepUpToDate=False,
                    )
                    if intra:
                        intra_vals = [b.close for b in intra if b.close and b.close > 0]
                        if intra_vals:
                            current_iv = intra_vals[-1]
                except Exception:
                    pass

                if current_iv is None:
                    current_iv = iv_vals[-1]   # last daily bar as fallback

                if hi_iv > lo_iv:
                    ivr = (current_iv - lo_iv) / (hi_iv - lo_iv) * 100

        # Options chain structure: available expirations + strikes
        chains = _ib.reqSecDefOptParams(ticker, "", "STK", stock.conId)
        if not chains:
            return None
        chain = next((c for c in chains if c.exchange == "SMART"), chains[0])
        tws_exps   = sorted(chain.expirations)
        all_strikes = sorted(chain.strikes)
        if not tws_exps or not all_strikes:
            return None

        # Filter to expirations with ≥7 DTE
        today = date.today()
        def _dte(s): return (date(int(s[:4]), int(s[4:6]), int(s[6:])) - today).days
        tws_exps = [e for e in tws_exps if _dte(e) >= 7]
        if not tws_exps:
            return None

        # ATM call chain at ~45 DTE for liquidity gate + IV reading
        exp_ref_tws, exp_ref_iso, dte_ref = _find_expiry(tws_exps, 45)
        calls_ref = _fetch_chain_df(ticker, exp_ref_tws, "C", all_strikes, price)
        if calls_ref is None or calls_ref.empty:
            return None

        atm      = _atm_row(calls_ref, price)
        atm_iv   = _sf(atm["impliedVolatility"])
        atm_mid  = (_sf(atm["bid"]) + _sf(atm["ask"])) / 2
        spread_pct = ((_sf(atm["ask"]) - _sf(atm["bid"])) / atm_mid * 100
                      if atm_mid > 0 else 999)
        oi = _si(atm["openInterest"])

        # Liquidity gate - only fail if we get a positive but horribly low OI
        if 0 < oi < C.OPT_MIN_ATM_OI:
            return {"error": f"low OI ({oi})"}
        if spread_pct > C.OPT_MAX_SPREAD_PCT:
            return {"error": f"wide spread ({spread_pct:.0f}%)"}

        # Structure selection: prefer IVR when available, else IV/HV ratio
        if ivr is not None:
            # IVR quartiles → same four structures as IV/HV path
            if ivr < 25:
                p_struct, p_dte = "long_call",     C.DTE_LONG_CALL
            elif ivr < 50:
                p_struct, p_dte = "debit_spread",  C.DTE_DEBIT_SPREAD
            elif ivr < 75:
                p_struct, p_dte = "credit_spread", C.DTE_CREDIT_SPREAD
            else:
                p_struct, p_dte = "diagonal",      C.DTE_DIAG_BACK
            eff_iv = current_iv if current_iv else atm_iv
            iv_hv  = eff_iv / hv30 if hv30 > 0 else 0.0
        else:
            if atm_iv <= 0:
                return {"error": "no IV data"}
            iv_hv = atm_iv / hv30
            if iv_hv < C.IV_HV_CHEAP:
                p_struct, p_dte = "long_call",     C.DTE_LONG_CALL
            elif iv_hv < C.IV_HV_NEUTRAL:
                p_struct, p_dte = "debit_spread",  C.DTE_DEBIT_SPREAD
            elif iv_hv < C.IV_HV_RICH:
                p_struct, p_dte = "credit_spread", C.DTE_CREDIT_SPREAD
            else:
                p_struct, p_dte = "diagonal",      C.DTE_DIAG_BACK

        # Fetch chain at primary target DTE
        exp_p_tws, exp_p_iso, dte_p = _find_expiry(tws_exps, p_dte)
        if exp_p_tws == exp_ref_tws:
            calls_p = calls_ref
        else:
            calls_p = _fetch_chain_df(ticker, exp_p_tws, "C", all_strikes, price)
            if calls_p is None or calls_p.empty:
                return None

        # Build primary plan
        if p_struct == "long_call":
            primary = _build_long_call(calls_p, price, dte_p, exp_p_iso)

        elif p_struct == "debit_spread":
            primary = _build_debit_spread(calls_p, price, dte_p, exp_p_iso, spread_width)

        elif p_struct == "credit_spread":
            puts_p = _fetch_chain_df(ticker, exp_p_tws, "P", all_strikes, price)
            if puts_p is None or puts_p.empty:
                return None
            primary = _build_credit_spread(puts_p, price, dte_p, exp_p_iso, spread_width)

        else:  # diagonal
            exp_f_tws, exp_f_iso, dte_f = _find_expiry(tws_exps, C.DTE_DIAG_FRONT)
            calls_front = _fetch_chain_df(ticker, exp_f_tws, "C", all_strikes, price)
            if calls_front is None or calls_front.empty:
                return None
            primary = _build_diagonal(calls_front, calls_p, price,
                                      dte_f, dte_p, exp_f_iso, exp_p_iso)

        if primary is None:
            return {"error": "could not build plan"}

        # Fallback plans for small accounts (same downgrade ladder as yfinance path)
        fallbacks = []
        if p_struct in ("long_call", "diagonal"):
            exp_ds_tws, exp_ds_iso, dte_ds = _find_expiry(tws_exps, C.DTE_DEBIT_SPREAD)
            c_ds = (calls_p if exp_ds_tws == exp_p_tws else
                    _fetch_chain_df(ticker, exp_ds_tws, "C", all_strikes, price))
            if c_ds is not None:
                fb1 = _build_debit_spread(c_ds, price, dte_ds, exp_ds_iso, spread_width)
                fb2 = _build_debit_spread(c_ds, price, dte_ds, exp_ds_iso, spread_width / 2)
                if fb1: fallbacks.append(fb1)
                if fb2: fallbacks.append(fb2)

        elif p_struct == "debit_spread":
            fb = _build_debit_spread(calls_p, price, dte_p, exp_p_iso, spread_width / 2)
            if fb: fallbacks.append(fb)

        elif p_struct == "credit_spread":
            fb = _build_credit_spread(puts_p, price, dte_p, exp_p_iso, spread_width / 2)
            if fb: fallbacks.append(fb)

        account_sizing = [_size_options_account(primary, fallbacks, acc) for acc in C.ACCOUNTS]

        result = {
            "ok":             True,
            "hv30":           hv30,
            "atm_iv":         current_iv if current_iv else atm_iv,
            "iv_hv":          iv_hv,
            "primary":        primary,
            "account_sizing": account_sizing,
            "data_source":    "TWS",
        }
        if ivr is not None:
            result["ivr"] = round(ivr, 1)
        return result

    except Exception:
        return None   # any failure → fall back to yfinance silently
