"""
battle_card.py — STFS-EQ Battle Card Generator v2.0

USAGE:
    python3.11 battle_card.py <REGIME>

EXAMPLE:
    python3.11 battle_card.py GOLDILOCKS

PREREQUISITES:
    pip3 install --user yfinance pandas numpy requests

    export FINNHUB_API_KEY="your_key"
"""

import argparse
import html
import json
import math
import os
import sys
import time
import warnings
import webbrowser
from datetime import datetime, timedelta, date
from pathlib import Path

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
except ImportError:
    print("ERROR: yfinance not installed. Run: pip3 install --user yfinance")
    sys.exit(1)

import config as C

# ── TWS integration (optional — falls back gracefully when not connected) ──────
try:
    from tws_data import (
        get_ohlc      as _tws_ohlc,
        get_options_data as _tws_options,
        get_positions  as _tws_positions,
        tws_connected  as _tws_connected,
    )
    _TWS_MODULE = True
except ImportError:
    _TWS_MODULE = False
    def _tws_connected(): return False
    def _tws_ohlc(*a, **kw): return None
    def _tws_options(*a, **kw): return None
    def _tws_positions(): return None


# ============================================================================
# TECHNICAL INDICATORS  (match PineScript v5 semantics)
# ============================================================================

def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def wilder(s, n): return s.ewm(alpha=1/n, adjust=False).mean()

def wma(s, n):
    w = np.arange(1, n+1)
    return s.rolling(n).apply(lambda x: np.dot(x, w)/w.sum(), raw=True)

def hma(s, n):
    return wma(2*wma(s, int(n/2)) - wma(s, n), int(math.sqrt(n)))

def rsi(s, n=14):
    d = s.diff()
    up, dn = d.clip(lower=0), -d.clip(upper=0)
    rs = wilder(up, n) / wilder(dn, n).replace(0, np.nan)
    return 100 - 100/(1+rs)

def atr(hi, lo, cl, n=14):
    tr = pd.concat([hi-lo, (hi-cl.shift()).abs(), (lo-cl.shift()).abs()], axis=1).max(axis=1)
    return wilder(tr, n)

def adx_dmi(hi, lo, cl, n=14):
    um, dm = hi.diff(), -lo.diff()
    pdm = np.where((um>dm)&(um>0), um, 0.)
    mdm = np.where((dm>um)&(dm>0), dm, 0.)
    pdm, mdm = pd.Series(pdm, index=hi.index), pd.Series(mdm, index=hi.index)
    tr = pd.concat([hi-lo,(hi-cl.shift()).abs(),(lo-cl.shift()).abs()],axis=1).max(axis=1)
    av = wilder(tr, n)
    pdi = 100*wilder(pdm,n)/av.replace(0,np.nan)
    mdi = 100*wilder(mdm,n)/av.replace(0,np.nan)
    dx  = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    return pdi, mdi, wilder(dx, n)

def obv(cl, vol):
    return (np.sign(cl.diff()).fillna(0)*vol).cumsum()


# ============================================================================
# UTILITY HELPERS
# ============================================================================

def sf(v, d=0.0):
    """safe float — returns d on NaN/None/error"""
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except Exception:
        return d

def si(v, d=0):
    """safe int"""
    try:
        x = float(v)
        return int(x) if math.isfinite(x) else d
    except Exception:
        return d

def find_expiry(exps, target_dte):
    """Return (expiry_str, actual_dte) closest to target."""
    today = date.today()
    best = min(exps, key=lambda e: abs((date.fromisoformat(e)-today).days - target_dte))
    return best, (date.fromisoformat(best)-today).days

def atm_row(chain_df, price):
    """Return the row from an options chain DataFrame closest to ATM."""
    idx = int((chain_df["strike"] - price).abs().argsort().iloc[0])
    return chain_df.iloc[idx]


# ============================================================================
# DATA SOURCES
# ============================================================================

FINNHUB_BASE = "https://finnhub.io/api/v1"

def get_finnhub_key():
    k = os.environ.get("FINNHUB_API_KEY")
    if not k:
        print("ERROR: FINNHUB_API_KEY not set. Run: export FINNHUB_API_KEY=\"your_key\"")
        sys.exit(1)
    return k

def fetch_earnings_calendar(days_ahead):
    key = get_finnhub_key()
    try:
        r = requests.get(f"{FINNHUB_BASE}/calendar/earnings",
                         params={"from": date.today().isoformat(),
                                 "to": (date.today()+timedelta(days=days_ahead)).isoformat(),
                                 "token": key}, timeout=10)
        r.raise_for_status()
        cal = r.json().get("earningsCalendar") or []
        return {e.get("symbol","").upper() for e in cal if e.get("symbol")}
    except Exception as e:
        print(f"  ⚠  Earnings calendar: {e}")
        return set()

def fetch_profile(ticker):
    key = get_finnhub_key()
    try:
        r = requests.get(f"{FINNHUB_BASE}/stock/profile2",
                         params={"symbol": ticker, "token": key}, timeout=10)
        r.raise_for_status()
        d = r.json()
        return {"name": d.get("name",""), "industry": d.get("finnhubIndustry",""),
                "marketCap_M": sf(d.get("marketCapitalization",0))}
    except Exception:
        return {}

def fetch_daily_ohlc(tickers, lookback_days=300):
    if not tickers: return {}

    # TWS path: try first, patch any missing tickers from yfinance below
    tws_data = {}
    if _tws_connected():
        tws_data = _tws_ohlc(tickers, lookback_days) or {}
        if tws_data:
            print(f"  ✓ OHLC via TWS ({len(tws_data)}/{len(tickers)} tickers)")

    missing = [t for t in tickers if t not in tws_data]
    if not missing:
        return tws_data

    # yfinance fallback for missing tickers (or all, when TWS is off)
    start = (date.today()-timedelta(days=lookback_days)).isoformat()
    try:
        data = yf.download(tickers=missing, start=start, interval="1d",
                           group_by="ticker", auto_adjust=True,
                           progress=False, threads=True)
    except Exception as e:
        print(f"  ⚠  yfinance: {e}")
        return tws_data
    out = dict(tws_data)
    if len(missing) == 1:
        df = data.dropna()
        if not df.empty: out[missing[0]] = df
    else:
        for t in missing:
            try:
                df = data[t].dropna()
                if not df.empty: out[t] = df
            except Exception: pass
    return out


# ============================================================================
# OPTIONS PLAN BUILDERS
# ============================================================================

def _liquidity_ok(row, chain_df):
    """Check OI and spread of the ATM row."""
    oi = si(row.get("openInterest", row["openInterest"] if "openInterest" in row.index else 0))
    bid, ask = sf(row["bid"]), sf(row["ask"])
    mid = (bid+ask)/2
    spread_pct = (ask-bid)/mid*100 if mid > 0 else 999
    return oi >= C.OPT_MIN_ATM_OI, spread_pct <= C.OPT_MAX_SPREAD_PCT, oi, spread_pct

def build_long_call(calls, price, dte, expiry):
    atm = atm_row(calls, price)
    ask, bid = sf(atm["ask"]), sf(atm["bid"])
    if ask <= 0: return None
    return {
        "structure": "long_call", "label": "Long Call",
        "expiry": expiry, "dte": dte,
        "long_strike": sf(atm["strike"]), "short_strike": None,
        "net_debit": ask, "net_credit": None,
        "max_loss_per_contract": ask * 100,
        "target_label": "2× debit (100% gain)",
        "target_value": ask * C.LONG_CALL_TARGET_MULT,
        "oi": si(atm["openInterest"]),
    }

def build_debit_spread(calls, price, dte, expiry, width):
    atm = atm_row(calls, price)
    long_strike = sf(atm["strike"])
    long_ask = sf(atm["ask"])
    short_target = long_strike + width
    short_row = atm_row(calls[calls["strike"] >= long_strike + 1], price + width) \
        if len(calls[calls["strike"] > long_strike]) > 0 else None
    if short_row is None: return None
    short_strike = sf(short_row["strike"])
    short_bid = sf(short_row["bid"])
    net_debit = max(0.05, long_ask - short_bid)
    actual_width = short_strike - long_strike
    if actual_width <= 0 or net_debit <= 0: return None
    return {
        "structure": "debit_spread",
        "label": f"Bull Call Spread ${actual_width:.0f}w",
        "expiry": expiry, "dte": dte,
        "long_strike": long_strike, "short_strike": short_strike,
        "net_debit": net_debit, "net_credit": None,
        "spread_width": actual_width,
        "max_loss_per_contract": net_debit * 100,
        "max_profit_per_contract": (actual_width - net_debit) * 100,
        "target_label": "2× debit (100% gain)",
        "target_value": net_debit * C.DEBIT_SPREAD_TARGET_MULT,
        "oi": si(atm["openInterest"]),
    }

def build_credit_spread(puts, price, dte, expiry, width):
    atm = atm_row(puts, price)
    short_strike = sf(atm["strike"])
    short_bid = sf(atm["bid"])
    long_puts = puts[puts["strike"] < short_strike]
    if long_puts.empty: return None
    long_row = atm_row(long_puts, price - width)
    long_strike = sf(long_row["strike"])
    long_ask = sf(long_row["ask"])
    net_credit = max(0.05, short_bid - long_ask)
    actual_width = short_strike - long_strike
    if actual_width <= 0 or net_credit <= 0: return None
    max_loss = (actual_width - net_credit) * 100
    return {
        "structure": "credit_spread",
        "label": f"Bull Put Spread ${actual_width:.0f}w",
        "expiry": expiry, "dte": dte,
        "long_strike": long_strike, "short_strike": short_strike,
        "net_debit": None, "net_credit": net_credit,
        "spread_width": actual_width,
        "max_loss_per_contract": max_loss,
        "max_profit_per_contract": net_credit * 100,
        "target_label": f"50% of credit (${net_credit*C.CREDIT_TARGET_PCT:.2f})",
        "target_value": net_credit * C.CREDIT_TARGET_PCT,
        "oi": si(atm["openInterest"]),
    }

def build_diagonal(calls_near, calls_far, price, dte_front, dte_back, exp_front, exp_back):
    short_row = atm_row(calls_near, price)
    long_row  = atm_row(calls_far,  price)
    short_bid = sf(short_row["bid"])
    long_ask  = sf(long_row["ask"])
    net_debit = max(0.05, long_ask - short_bid)
    if net_debit <= 0: return None
    return {
        "structure": "diagonal", "label": "Call Diagonal",
        "expiry": exp_back, "dte": dte_back,
        "expiry_front": exp_front, "dte_front": dte_front,
        "long_strike": sf(long_row["strike"]),
        "short_strike": sf(short_row["strike"]),
        "net_debit": net_debit, "net_credit": None,
        "max_loss_per_contract": net_debit * 100,
        "target_label": "1.5× debit (50% gain)",
        "target_value": net_debit * C.DIAGONAL_TARGET_MULT,
        "oi": si(long_row["openInterest"]),
    }


# ============================================================================
# PER-ACCOUNT OPTIONS SIZING WITH AUTO-DOWNGRADE
# ============================================================================

def _contracts_for_account(plan, acc):
    """Return (contracts, actual_risk) for one account, 0 if too small."""
    if plan is None: return 0, 0.0
    max_loss = plan.get("max_loss_per_contract", 0)
    if max_loss <= 0: return 0, 0.0
    risk_dollars = acc["equity"] * acc["risk_pct"] / 100.0
    max_notional = acc["equity"] * acc["max_notional_pct"] / 100.0
    cts_by_risk = int(risk_dollars / max_loss)
    debit_or_width = plan.get("net_debit") or plan.get("spread_width") or 0
    if debit_or_width > 0 and cts_by_risk > 0:
        cts_by_notional = int(max_notional / (debit_or_width * 100))
        cts = min(cts_by_risk, cts_by_notional)
    else:
        cts = cts_by_risk
    return cts, max_loss * cts

def size_options_account(primary_plan, fallback_plans, acc):
    """Return sizing dict for one account with auto-downgrade (Option B)."""
    cts, risk = _contracts_for_account(primary_plan, acc)
    used_plan = primary_plan
    downgraded = False

    if cts == 0:
        for fb in fallback_plans:
            cts, risk = _contracts_for_account(fb, acc)
            if cts > 0:
                used_plan = fb
                downgraded = True
                break

    min_hint = None
    if cts == 0 and primary_plan:
        ml = primary_plan.get("max_loss_per_contract", 0)
        if ml > 0:
            need = ml / (acc["risk_pct"] / 100.0)
            min_hint = int(math.ceil(need / 1000) * 1000)

    notional = ((used_plan.get("net_debit") or used_plan.get("spread_width") or 0)
                * 100 * cts) if cts > 0 else 0

    return {
        "account": acc["name"],
        "equity": acc["equity"],
        "contracts": cts,
        "risk_dollars": risk,
        "notional": notional,
        "label": used_plan["label"] if used_plan else "—",
        "downgraded": downgraded,
        "min_hint": min_hint,
    }


# ============================================================================
# MAIN OPTIONS DATA FETCH
# ============================================================================

def fetch_options_data(ticker, df):
    """
    Fetch options chain, compute IV/HV ratio, select structure, size per account.
    Tries TWS first (true IVR + live quotes); falls back to yfinance on failure.
    Returns dict or {"error": ...}. Called ONLY for STRONG BUY candidates.
    """
    if _tws_connected():
        result = _tws_options(ticker, df)
        if result is not None:
            return result
    try:
        close = df["Close"]
        price = float(close.iloc[-1])

        # HV30 — 30-day annualised historical vol from OHLC
        log_ret = np.log(close / close.shift(1)).dropna()
        hv30 = float(log_ret.tail(30).std() * np.sqrt(252))
        if hv30 <= 0:
            return {"error": "HV30 = 0"}

        t = yf.Ticker(ticker)
        exps = t.options
        if not exps or len(exps) < 2:
            return {"error": "no options available"}

        # Get ATM IV at ~45 DTE for structure decision
        exp_ref, dte_ref = find_expiry(exps, 45)
        chain_ref = t.option_chain(exp_ref)
        calls_ref, puts_ref = chain_ref.calls, chain_ref.puts
        if calls_ref.empty:
            return {"error": "empty chain"}

        atm = atm_row(calls_ref, price)
        atm_iv = sf(atm["impliedVolatility"])
        atm_bid, atm_ask = sf(atm["bid"]), sf(atm["ask"])
        atm_mid = (atm_bid + atm_ask) / 2
        spread_pct = (atm_ask - atm_bid) / atm_mid * 100 if atm_mid > 0 else 999
        oi = si(atm["openInterest"])

        # Liquidity gate
        if oi < C.OPT_MIN_ATM_OI:
            return {"error": f"low OI ({oi})"}
        if spread_pct > C.OPT_MAX_SPREAD_PCT:
            return {"error": f"wide spread ({spread_pct:.0f}%)"}
        if atm_iv <= 0:
            return {"error": "no IV data"}

        iv_hv = atm_iv / hv30

        # Select primary structure
        if iv_hv < C.IV_HV_CHEAP:
            p_struct, p_dte = "long_call",     C.DTE_LONG_CALL
        elif iv_hv < C.IV_HV_NEUTRAL:
            p_struct, p_dte = "debit_spread",  C.DTE_DEBIT_SPREAD
        elif iv_hv < C.IV_HV_RICH:
            p_struct, p_dte = "credit_spread", C.DTE_CREDIT_SPREAD
        else:
            p_struct, p_dte = "diagonal",      C.DTE_DIAG_BACK

        # Fetch chain at primary target DTE
        exp_p, dte_p = find_expiry(exps, p_dte)
        if exp_p == exp_ref:
            calls_p, puts_p = calls_ref, puts_ref
        else:
            ch = t.option_chain(exp_p)
            calls_p, puts_p = ch.calls, ch.puts

        # Build primary plan
        if p_struct == "long_call":
            primary = build_long_call(calls_p, price, dte_p, exp_p)
        elif p_struct == "debit_spread":
            primary = build_debit_spread(calls_p, price, dte_p, exp_p, C.DEBIT_SPREAD_WIDTH)
        elif p_struct == "credit_spread":
            primary = build_credit_spread(puts_p, price, dte_p, exp_p, C.CREDIT_SPREAD_WIDTH)
        else:  # diagonal
            exp_f, dte_f = find_expiry(exps, C.DTE_DIAG_FRONT)
            ch_f = t.option_chain(exp_f)
            primary = build_diagonal(ch_f.calls, calls_p, price, dte_f, dte_p, exp_f, exp_p)

        if primary is None:
            return {"error": "could not build plan"}

        # Build fallback plans (for small accounts)
        fallbacks = []
        if p_struct in ("long_call", "diagonal"):
            exp_ds, dte_ds = find_expiry(exps, C.DTE_DEBIT_SPREAD)
            ch_ds = t.option_chain(exp_ds) if exp_ds != exp_p else None
            c_ds = ch_ds.calls if ch_ds else calls_p
            d_ds = dte_ds if ch_ds else dte_p
            fb1 = build_debit_spread(c_ds, price, d_ds, exp_ds, C.DEBIT_SPREAD_WIDTH)
            fb2 = build_debit_spread(c_ds, price, d_ds, exp_ds, C.DEBIT_SPREAD_WIDTH / 2)
            if fb1: fallbacks.append(fb1)
            if fb2: fallbacks.append(fb2)
        elif p_struct == "debit_spread":
            fb = build_debit_spread(calls_p, price, dte_p, exp_p, C.DEBIT_SPREAD_WIDTH / 2)
            if fb: fallbacks.append(fb)
        elif p_struct == "credit_spread":
            fb = build_credit_spread(puts_p, price, dte_p, exp_p, C.CREDIT_SPREAD_WIDTH / 2)
            if fb: fallbacks.append(fb)

        # Size per account
        account_sizing = [
            size_options_account(primary, fallbacks, acc) for acc in C.ACCOUNTS
        ]

        return {
            "ok": True,
            "hv30": hv30,
            "atm_iv": atm_iv,
            "iv_hv": iv_hv,
            "primary": primary,
            "account_sizing": account_sizing,
        }

    except Exception as e:
        return {"error": str(e)[:120]}


# ============================================================================
# SCORING
# ============================================================================

def score_ticker(df, bench_df, is_benchmark=False):
    min_bars = max(C.EMA_SLOW, C.WEEKLY_EMA_SLOW*5, C.RS_LOOKBACK+5, 50)
    if df is None or len(df) < min_bars:
        return {"error": f"insufficient data ({len(df) if df is not None else 0} bars)"}

    hi, lo, cl, vol = df["High"], df["Low"], df["Close"], df["Volume"]
    ef  = ema(cl, C.EMA_FAST); em = ema(cl, C.EMA_MID); es = ema(cl, C.EMA_SLOW)
    hm  = hma(cl, C.HMA_LEN)
    rs  = rsi(cl, C.RSI_LEN)
    at  = atr(hi, lo, cl, C.ATR_LEN)
    _,_,adx_s = adx_dmi(hi, lo, cl, C.ADX_LEN)
    ob  = obv(cl, vol)
    oe  = ema(ob, C.OBV_EMA_LEN)

    c = float(cl.iloc[-1]); a = float(at.iloc[-1])
    atr_pct = (a/c)*100

    f1 = ef.iloc[-1]>em.iloc[-1] and em.iloc[-1]>es.iloc[-1]
    wk = df.resample("W-FRI").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()
    if len(wk) >= C.WEEKLY_EMA_SLOW+2:
        wc = wk["Close"]
        wf = ema(wc, C.WEEKLY_EMA_FAST).iloc[-1]; ws = ema(wc, C.WEEKLY_EMA_SLOW).iloc[-1]
        f2 = wc.iloc[-1]>ws and wf>ws
    else:
        f2 = False
    f3 = hm.iloc[-1]>hm.iloc[-2]
    adx_val = float(adx_s.iloc[-1])
    f4 = adx_val>C.ADX_THRESHOLD and adx_s.iloc[-1]>adx_s.iloc[-2]>adx_s.iloc[-3]
    rsi_val = float(rs.iloc[-1])
    f5 = C.RSI_LOWER_BAND<=rsi_val<=C.RSI_UPPER_BAND

    if is_benchmark:
        f6, rs_val = True, 0.0
    else:
        tr = cl.iloc[-1]/cl.iloc[-1-C.RS_LOOKBACK]-1
        bc = bench_df["Close"]
        bca = bc[bc.index<=cl.index[-1]]
        if len(bca)>C.RS_LOOKBACK:
            br = bca.iloc[-1]/bca.iloc[-1-C.RS_LOOKBACK]-1
            f6 = tr>br; rs_val = float((tr-br)*100)
        else:
            f6 = False; rs_val = 0.0

    f7 = ob.iloc[-1]>oe.iloc[-1] and ob.iloc[-1]>ob.iloc[-1-C.OBV_SLOPE_LOOKBACK]
    f8 = C.ATR_PCT_MIN<=atr_pct<=C.ATR_PCT_MAX

    factors = {"F1 Daily Stack":bool(f1),"F2 Weekly Trend":bool(f2),
               "F3 HMA Rising":bool(f3),"F4 ADX+Rising":bool(f4),
               "F5 RSI Band":bool(f5),"F6 RS vs Bench":bool(f6),
               "F7 OBV+Slope":bool(f7),"F8 ATR% Band":bool(f8)}
    score = sum(factors.values())
    trio  = factors["F1 Daily Stack"] and factors["F2 Weekly Trend"] and factors["F8 ATR% Band"]

    if score >= C.STRONG_SCORE_MIN and trio: action = "STRONG BUY"
    elif score >= C.WATCH_SCORE_MIN and factors["F1 Daily Stack"]: action = "WATCH"
    else: action = "SKIP"

    return {"close":c,"atr":a,"atr_pct":float(atr_pct),"rsi":rsi_val,
            "adx":adx_val,"rs_pct":rs_val,"factors":factors,
            "score":int(score),"trio_pass":bool(trio),"action":action,
            "is_breakout": bool(c>=cl.iloc[-C.BREAKOUT_LOOKBACK:].max())}


# ============================================================================
# UNDERLYING PLAN (per account)
# ============================================================================

def underlying_account_sizing(entry, stop, acc):
    rk = acc["equity"] * acc["risk_pct"] / 100.0
    rps = entry - stop
    if rps <= 0: return {"account":acc["name"],"shares":0,"notional":0,"risk_dollars":0,"capped":False}
    sh_r = int(rk/rps)
    max_n = acc["equity"] * acc["max_notional_pct"] / 100.0
    sh_n = int(max_n/entry) if entry>0 else 0
    sh = min(sh_r, sh_n)
    return {"account":acc["name"],"shares":sh,"notional":sh*entry,"risk_dollars":sh*rps,"capped":sh<sh_r}

def compute_underlying_plan(score_info):
    c, a = score_info["close"], score_info["atr"]
    is_brk = score_info["is_breakout"]
    entry  = c if is_brk else (c - C.ENTRY_ATR_MULT*a)
    stop   = entry - C.STOP_ATR_MULT*a
    target = entry + C.TARGET_ATR_MULT*a
    return {
        "entry": entry, "stop": stop, "target": target,
        "entry_type": "MOO" if is_brk else "Limit GTC 1d",
        "is_breakout": is_brk,
        "account_sizing": [underlying_account_sizing(entry, stop, acc) for acc in C.ACCOUNTS],
    }


# ============================================================================
# HTML  — matching your existing TCR design language
# ============================================================================

CSS = """
<style>
:root{--bg:#080c12;--bg1:#0e1420;--bg2:#141c2e;--bg3:#1b2540;
  --border:#232f4a;--border2:#2c3d5c;--text:#d4dff5;--muted:#5a7090;
  --green:#00e5a0;--green2:#00b87a;--green-bg:rgba(0,229,160,.08);
  --red:#ff4560;--red-bg:rgba(255,69,96,.08);
  --amber:#ffb020;--amber-bg:rgba(255,176,32,.08);
  --blue:#4090ff;--blue-bg:rgba(64,144,255,.08);
  --purple:#a060ff;--cyan:#00d4ff;--cyan-bg:rgba(0,212,255,.08);
  --font-body:'JetBrains Mono',monospace;--font-head:'Syne',sans-serif;--r:8px}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--font-body);background:var(--bg);color:var(--text);
  font-size:13px;line-height:1.6;padding:20px}
.wrap{max-width:1340px;margin:0 auto}
h1{font-family:var(--font-head);font-size:20px;color:var(--cyan);letter-spacing:2px}
h2{font-family:var(--font-head);font-size:16px;margin:20px 0 12px}
/* HEADER */
.hdr{background:var(--bg1);border:1px solid var(--border);border-top:3px solid var(--cyan);
  border-radius:10px;padding:16px 20px;margin-bottom:14px}
.hdr-row{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}
.hdr-meta{display:flex;gap:24px;font-size:11px;color:var(--muted);margin-top:10px}
.hdr-meta b{color:var(--text)}
/* REGIME BADGE */
.rbadge{display:inline-block;padding:4px 12px;border-radius:4px;
  font-weight:700;font-size:12px;letter-spacing:1px}
.r-GOLDILOCKS{background:rgba(0,229,160,.12);color:var(--green);border:1px solid var(--green)}
.r-LIQUIDITY{background:rgba(160,96,255,.12);color:var(--purple);border:1px solid var(--purple)}
.r-REFLATION{background:rgba(255,176,32,.12);color:var(--amber);border:1px solid var(--amber)}
.r-NEUTRAL{background:rgba(90,112,144,.12);color:var(--muted);border:1px solid var(--muted)}
.r-RISK_OFF{background:rgba(255,69,96,.12);color:var(--red);border:1px solid var(--red)}
.r-CRASH{background:rgba(255,69,96,.3);color:#fff;border:1px solid var(--red)}
/* SUMMARY */
.sum{display:flex;gap:28px;flex-wrap:wrap;background:var(--bg1);
  border:1px solid var(--border);border-radius:10px;padding:12px 20px;margin-bottom:14px}
.sum .st{display:flex;flex-direction:column}
.st-lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:1px}
.st-val{font-size:20px;font-weight:700}
/* CARDS */
.card{background:var(--bg1);border:1px solid var(--border);border-radius:12px;
  padding:16px 20px;margin-bottom:12px}
.c-strong{border-left:3px solid var(--green)}
.c-watch{border-left:3px solid var(--amber)}
.c-skip{border-left:3px solid var(--muted);opacity:.6}
.c-cash{border-left:3px solid var(--red)}
.card-hdr{display:flex;justify-content:space-between;align-items:baseline;
  margin-bottom:10px;flex-wrap:wrap;gap:8px}
.ticker{font-size:18px;font-weight:700;letter-spacing:1px}
.tmeta{font-size:11px;color:var(--muted)}
/* SCORE PILL */
.spill{display:inline-block;padding:2px 10px;border-radius:10px;font-weight:700;font-size:12px}
.s8,.s7{background:var(--green);color:#000}
.s6,.s5{background:var(--amber);color:#000}
.slow{background:var(--bg3);color:var(--muted)}
/* FACTOR GRID */
.fgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;margin:10px 0}
.f{padding:5px 8px;border-radius:3px;font-size:11px;
  display:flex;justify-content:space-between;align-items:center}
.fp{background:rgba(0,229,160,.1);color:var(--green)}
.ff{background:rgba(255,69,96,.08);color:var(--red)}
.fmark{font-weight:700}
/* TRADE SECTION */
.trade-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}
@media(max-width:800px){.trade-grid{grid-template-columns:1fr}}
.tblock{background:#0b0d12;border:1px solid var(--border);border-radius:6px;padding:12px 14px}
.tblock-hdr{font-size:11px;font-weight:700;letter-spacing:.08em;color:var(--amber);margin-bottom:8px}
.trow{display:flex;justify-content:space-between;padding:3px 0;
  border-bottom:1px dotted var(--border);font-size:12px}
.trow:last-child{border-bottom:none}
.tl{color:var(--muted)}
.tv{font-weight:600}
.tv-entry{color:var(--amber)} .tv-stop{color:var(--red)} .tv-target{color:var(--green)}
.tv-struct{color:var(--cyan)}
/* ACCOUNT TABLE */
.atbl{width:100%;border-collapse:collapse;margin-top:10px;font-size:11px}
.atbl th{padding:3px 6px;text-align:left;color:var(--muted);
  font-size:9px;text-transform:uppercase;letter-spacing:.08em;border-bottom:1px solid var(--border)}
.atbl td{padding:4px 6px;border-bottom:1px solid var(--border)}
.atbl tr:last-child td{border-bottom:none}
.atbl .acc-name{color:var(--text);font-weight:700}
.atbl .acc-0{color:var(--muted);font-style:italic}
.atbl .dg-note{color:var(--purple);font-size:10px}
.atbl .hint{color:var(--muted);font-size:10px}
/* CHIPS */
.chip{display:inline-flex;align-items:center;padding:1px 6px;border-radius:3px;
  font-size:10px;font-weight:700;letter-spacing:.04em;text-transform:uppercase}
.chip-g{background:var(--green-bg);color:var(--green)}
.chip-r{background:var(--red-bg);color:var(--red)}
.chip-a{background:var(--amber-bg);color:var(--amber)}
.chip-b{background:var(--blue-bg);color:var(--blue)}
.chip-m{background:var(--bg3);color:var(--muted)}
/* IV/HV badge */
.ivhv{font-size:11px;font-weight:600;padding:2px 8px;border-radius:4px}
.ivhv-cheap{background:rgba(0,229,160,.12);color:var(--green)}
.ivhv-neut{background:rgba(64,144,255,.12);color:var(--blue)}
.ivhv-rich{background:rgba(255,176,32,.12);color:var(--amber)}
.ivhv-vrich{background:rgba(255,69,96,.12);color:var(--red)}
/* ALERTS */
.alert{display:flex;gap:10px;align-items:flex-start;padding:10px 14px;
  border-radius:var(--r);font-size:12px;margin:8px 0}
.alert-a{background:var(--amber-bg);border-left:3px solid var(--amber)}
.alert-r{background:var(--red-bg);border-left:3px solid var(--red)}
.alert-g{background:var(--green-bg);border-left:3px solid var(--green)}
/* GATE SECTION */
.gate{background:var(--bg1);border:1px solid var(--amber);
  border-radius:10px;padding:16px 20px;margin-top:20px}
.gate-row{padding:6px 0;font-size:13px;display:flex;align-items:center;gap:10px}
.cb{width:14px;height:14px;border:1.5px solid var(--muted);border-radius:2px;
  display:inline-block;flex-shrink:0}
/* MISC */
.foot{text-align:center;color:var(--muted);font-size:10px;margin-top:24px}
table.drop{width:100%;border-collapse:collapse;font-size:11px;margin-top:8px}
table.drop th{padding:4px 8px;text-align:left;color:var(--muted);font-size:10px}
table.drop td{padding:4px 8px;border-bottom:1px solid var(--border)}
@media print{body{background:#fff;color:#000}
  .card,.hdr,.gate,.sum{background:#fff;border:1px solid #ccc;break-inside:avoid}}
</style>
"""

# ── TWS order modal — CSS, HTML, JS ──────────────────────────────────────────

MODAL_CSS = """
.push-tws-btn{background:rgba(0,212,255,.08);color:var(--cyan);
  border:1px solid var(--cyan);border-radius:4px;padding:6px 14px;
  font-family:var(--font-body);font-size:11px;font-weight:700;
  cursor:pointer;letter-spacing:.04em}
.push-tws-btn:hover{background:rgba(0,212,255,.2)}
#tws-modal{display:none;position:fixed;top:0;left:0;width:100%;height:100%;
  background:rgba(0,0,0,.78);z-index:9999;align-items:center;justify-content:center}
.mbox{background:var(--bg1);border:1px solid var(--border2);border-top:3px solid var(--cyan);
  border-radius:12px;padding:24px;width:510px;max-width:96vw;max-height:92vh;overflow-y:auto}
.mhdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}
.mtitle{font-size:15px;font-weight:700;color:var(--cyan);font-family:var(--font-head)}
.mclose{background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer;line-height:1;padding:0}
.mclose:hover{color:var(--text)}
.mtabs{display:flex;gap:6px;margin-bottom:14px}
.mtab-btn{padding:5px 14px;border-radius:4px;border:1px solid var(--border);
  background:var(--bg2);color:var(--muted);cursor:pointer;font-size:11px;
  font-family:var(--font-body);font-weight:700;letter-spacing:.04em}
.mtab-btn.on{background:var(--cyan);color:#000;border-color:var(--cyan)}
.mrow{display:flex;align-items:center;gap:10px;margin:5px 0;font-size:12px}
.mlbl{color:var(--muted);width:76px;flex-shrink:0;font-size:11px}
.minp{background:var(--bg2);border:1px solid var(--border);color:var(--text);
  border-radius:4px;padding:4px 8px;font-family:var(--font-body);font-size:12px;width:110px}
.minp:focus{outline:none;border-color:var(--cyan)}
.macct{background:var(--bg2);border:1px solid var(--border);color:var(--text);
  border-radius:4px;padding:5px 8px;font-family:var(--font-body);font-size:12px;
  width:100%;margin-bottom:14px}
.macct:focus{outline:none;border-color:var(--cyan)}
.msz{width:100%;border-collapse:collapse;font-size:11px;margin:6px 0 10px}
.msz th{padding:3px 6px;color:var(--muted);text-align:left;font-size:9px;
  text-transform:uppercase;letter-spacing:.06em}
.msz td{padding:4px 6px;border-bottom:1px solid var(--border)}
.msz tr:last-child td{border-bottom:none}
.msz tbody tr:hover td{background:var(--bg2);cursor:pointer}
.mwarn{background:rgba(255,176,32,.07);border-left:3px solid var(--amber);
  padding:8px 12px;border-radius:4px;font-size:11px;color:var(--amber);margin:10px 0}
.mstat{margin-top:10px;padding:8px 12px;border-radius:4px;font-size:11px;display:none}
.mstat.ok{background:var(--green-bg);color:var(--green);display:block}
.mstat.err{background:var(--red-bg);color:var(--red);display:block}
.mstat.busy{background:var(--blue-bg);color:var(--blue);display:block}
.mfoot{display:flex;justify-content:flex-end;gap:8px;margin-top:14px}
.mbtn{padding:8px 18px;border-radius:4px;border:none;font-family:var(--font-body);
  font-size:11px;font-weight:700;cursor:pointer;letter-spacing:.04em}
.mbtn-cancel{background:var(--bg2);color:var(--muted)}.mbtn-cancel:hover{color:var(--text)}
.mbtn-push{background:var(--cyan);color:#000}.mbtn-push:hover{background:#00b5d9}
.mopt-info{background:var(--bg2);border:1px solid var(--border);border-radius:6px;
  padding:10px 12px;margin-bottom:10px;font-size:12px}
.mopt-row{display:flex;margin:3px 0}
.mopt-lbl{color:var(--muted);width:76px;font-size:11px;flex-shrink:0}
.mopt-val{color:var(--text);font-weight:600}
"""

MODAL_HTML = """
<div id="tws-modal">
 <div class="mbox">
  <div class="mhdr">
   <span class="mtitle">&#x1F4E4; Push to TWS &mdash; <span id="m-ticker"></span></span>
   <button class="mclose" onclick="mClose()">&#x2715;</button>
  </div>

  <div style="font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px">TWS Account</div>
  <select id="m-acct" class="macct"></select>

  <div class="mtabs">
   <button id="mtab-sh"  class="mtab-btn on" onclick="mTab('sh')">Shares Bracket</button>
   <button id="mtab-opt" class="mtab-btn"    onclick="mTab('opt')">Options</button>
  </div>

  <!-- SHARES panel -->
  <div id="mpanel-sh">
   <div class="mrow">
    <span class="mlbl">Entry</span>
    <input id="m-sh-entry"  class="minp" type="number" step="0.01">
    <span id="m-sh-etype" style="color:var(--amber);font-size:11px;font-weight:700"></span>
   </div>
   <div class="mrow">
    <span class="mlbl">Stop</span>
    <input id="m-sh-stop"   class="minp" type="number" step="0.01">
    <span style="font-size:10px;color:var(--muted)">&minus;2&times;ATR</span>
   </div>
   <div class="mrow">
    <span class="mlbl">Target</span>
    <input id="m-sh-target" class="minp" type="number" step="0.01">
    <span style="font-size:10px;color:var(--muted)">+2R</span>
   </div>
   <div class="mrow">
    <span class="mlbl">Shares</span>
    <input id="m-sh-qty" class="minp" type="number" step="1" min="1">
    <span style="font-size:10px;color:var(--muted)">&#x2193; click row to fill</span>
   </div>
   <div style="font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin:10px 0 2px">Suggested sizing</div>
   <table class="msz"><thead><tr><th>Account</th><th>Shares</th><th>Notional</th></tr></thead>
    <tbody id="m-sh-sz"></tbody></table>
   <div class="mwarn">&#x26A0; Orders placed as HELD &mdash; right-click parent in TWS &rarr; Transmit to activate bracket.</div>
   <div class="mfoot">
    <button class="mbtn mbtn-cancel" onclick="mClose()">Cancel</button>
    <button class="mbtn mbtn-push"   onclick="mPush('shares')">Push Bracket &rarr;</button>
   </div>
  </div>

  <!-- OPTIONS panel -->
  <div id="mpanel-opt" style="display:none">
   <div class="mopt-info">
    <div class="mopt-row"><span class="mopt-lbl">Structure</span><span id="m-opt-struct" class="mopt-val" style="color:var(--cyan)"></span></div>
    <div class="mopt-row"><span class="mopt-lbl">Expiry</span>   <span id="m-opt-expiry" class="mopt-val"></span></div>
    <div class="mopt-row"><span class="mopt-lbl">Legs</span>     <span id="m-opt-legs"   class="mopt-val"></span></div>
   </div>
   <div class="mrow">
    <span class="mlbl">Limit $</span>
    <input id="m-opt-limit" class="minp" type="number" step="0.01">
    <span style="font-size:10px;color:var(--muted)">net debit / credit</span>
   </div>
   <div class="mrow">
    <span class="mlbl">Contracts</span>
    <input id="m-opt-qty" class="minp" type="number" step="1" min="1">
    <span style="font-size:10px;color:var(--muted)">&#x2193; click row to fill</span>
   </div>
   <div style="font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin:10px 0 2px">Suggested sizing</div>
   <table class="msz"><thead><tr><th>Account</th><th>Contracts</th><th>Structure</th></tr></thead>
    <tbody id="m-opt-sz"></tbody></table>
   <div id="m-opt-na" style="display:none;color:var(--muted);font-size:12px;padding:16px 0;text-align:center">No options data for this ticker.</div>
   <div class="mwarn">&#x26A0; Option order placed as HELD &mdash; manual transmit required in TWS.</div>
   <div class="mfoot">
    <button class="mbtn mbtn-cancel" onclick="mClose()">Cancel</button>
    <button id="m-opt-push" class="mbtn mbtn-push" onclick="mPush('options')">Push Options &rarr;</button>
   </div>
  </div>

  <div id="m-status" class="mstat"></div>
 </div>
</div>
"""

MODAL_JS = """
const TWS_API = 'http://127.0.0.1:5001';
let _mdata = null;

async function mOpen(btn) {
  _mdata = JSON.parse(btn.dataset.order);
  mSetStat('', '');
  mTab('sh');
  document.getElementById('m-ticker').textContent = _mdata.ticker;
  document.getElementById('tws-modal').style.display = 'flex';

  mSetStat('Connecting to TWS\u2026', 'busy');
  try {
    const r = await fetch(TWS_API + '/api/status');
    const s = await r.json();
    if (!s.connected) { mSetStat('TWS not connected \u2014 start TWS and relaunch the app.', 'err'); return; }
    const sel = document.getElementById('m-acct');
    sel.innerHTML = s.accounts.map(a => '<option value="' + a + '">' + a + '</option>').join('');
    mSetStat('', '');
  } catch(e) { mSetStat('Order server unreachable \u2014 is the launcher running?', 'err'); return; }

  // Shares tab
  const sh = _mdata.shares;
  document.getElementById('m-sh-entry').value  = sh.entry.toFixed(2);
  document.getElementById('m-sh-stop').value   = sh.stop.toFixed(2);
  document.getElementById('m-sh-target').value = sh.target.toFixed(2);
  document.getElementById('m-sh-etype').textContent = sh.entry_type === 'MOO' ? 'MOO' : 'LMT \u00b7 DAY';
  const firstSh = sh.sizing.find(s => s.shares > 0) || sh.sizing[0];
  document.getElementById('m-sh-qty').value = firstSh ? firstSh.shares : 1;
  document.getElementById('m-sh-sz').innerHTML = sh.sizing.map(s =>
    `<tr onclick="document.getElementById('m-sh-qty').value=${Math.max(1,s.shares)}">` +
    `<td><b>${s.account}</b></td>` +
    `<td>${s.shares > 0 ? s.shares + ' sh' : '\u2014'}</td>` +
    `<td>${s.notional > 0 ? '$' + Math.round(s.notional).toLocaleString() : '\u2014'}</td>` +
    `</tr>`
  ).join('');

  // Options tab
  const opt = _mdata.options;
  const hasOpt = opt && opt.limit_price > 0;
  const optBtn = document.getElementById('mtab-opt');
  optBtn.style.opacity       = hasOpt ? '1' : '0.35';
  optBtn.style.pointerEvents = hasOpt ? ''  : 'none';
  if (hasOpt) {
    document.getElementById('m-opt-struct').textContent = opt.label;
    document.getElementById('m-opt-expiry').textContent = opt.expiry + '  (' + opt.dte + 'd)';
    document.getElementById('m-opt-legs').textContent   = mLegs(opt);
    document.getElementById('m-opt-limit').value = opt.limit_price.toFixed(2);
    const firstOpt = opt.sizing.find(s => s.contracts > 0) || opt.sizing[0];
    document.getElementById('m-opt-qty').value = firstOpt ? Math.max(1, firstOpt.contracts) : 1;
    document.getElementById('m-opt-sz').innerHTML = opt.sizing.map(s =>
      `<tr onclick="document.getElementById('m-opt-qty').value=${Math.max(1,s.contracts)}">` +
      `<td><b>${s.account}</b></td>` +
      `<td>${s.contracts > 0 ? s.contracts + ' ct' + (s.contracts !== 1 ? 's' : '') : '\u2014'}</td>` +
      `<td style="color:${s.label !== opt.label ? 'var(--purple)' : 'var(--muted)'}">${s.label}</td>` +
      `</tr>`
    ).join('');
    document.getElementById('m-opt-na').style.display   = 'none';
    document.getElementById('m-opt-push').style.display = '';
  } else {
    document.getElementById('m-opt-na').style.display   = '';
    document.getElementById('m-opt-push').style.display = 'none';
  }
}

function mLegs(opt) {
  const ls = opt.long_strike, ss = opt.short_strike;
  if (opt.structure === 'long_call')     return 'Long ' + ls + 'C';
  if (opt.structure === 'debit_spread')  return 'Long ' + ls + 'C / Short ' + ss + 'C';
  if (opt.structure === 'credit_spread') return 'Long ' + ls + 'P / Short ' + ss + 'P';
  if (opt.structure === 'diagonal')      return 'Short ' + ss + 'C (' + opt.dte_front + 'd) / Long ' + ls + 'C (' + opt.dte + 'd)';
  return '';
}

function mClose() { document.getElementById('tws-modal').style.display = 'none'; }

function mTab(t) {
  document.getElementById('mpanel-sh').style.display  = t === 'sh'  ? '' : 'none';
  document.getElementById('mpanel-opt').style.display = t === 'opt' ? '' : 'none';
  document.getElementById('mtab-sh').className  = 'mtab-btn' + (t === 'sh'  ? ' on' : '');
  document.getElementById('mtab-opt').className = 'mtab-btn' + (t === 'opt' ? ' on' : '');
  mSetStat('', '');
}

async function mPush(type) {
  const account = document.getElementById('m-acct').value;
  if (!account) { mSetStat('Select a TWS account first.', 'err'); return; }

  let payload = { type: type, ticker: _mdata.ticker, account: account };

  if (type === 'shares') {
    const qty = parseInt(document.getElementById('m-sh-qty').value);
    if (isNaN(qty) || qty < 1) { mSetStat('Shares must be \u2265 1.', 'err'); return; }
    payload.shares     = qty;
    payload.entry      = parseFloat(document.getElementById('m-sh-entry').value);
    payload.stop       = parseFloat(document.getElementById('m-sh-stop').value);
    payload.target     = parseFloat(document.getElementById('m-sh-target').value);
    payload.entry_type = _mdata.shares.entry_type;
  } else {
    const qty = parseInt(document.getElementById('m-opt-qty').value);
    if (isNaN(qty) || qty < 1) { mSetStat('Contracts must be \u2265 1.', 'err'); return; }
    const opt = _mdata.options;
    payload.contracts    = qty;
    payload.structure    = opt.structure;
    payload.expiry       = opt.expiry;
    payload.expiry_front = opt.expiry_front;
    payload.long_strike  = opt.long_strike;
    payload.short_strike = opt.short_strike;
    payload.limit_price  = parseFloat(document.getElementById('m-opt-limit').value);
  }

  mSetStat('Sending to TWS\u2026', 'busy');
  try {
    const r = await fetch(TWS_API + '/api/order', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const res = await r.json();
    mSetStat((res.ok ? '\u2713 ' : '\u2717 ') + (res.message || res.error), res.ok ? 'ok' : 'err');
  } catch(e) { mSetStat('\u2717 ' + e.message, 'err'); }
}

function mSetStat(msg, cls) {
  const el = document.getElementById('m-status');
  el.textContent = msg;
  el.className   = 'mstat' + (cls ? ' ' + cls : '');
  el.style.display = msg ? 'block' : 'none';
}

document.getElementById('tws-modal').addEventListener('click', function(e) {
  if (e.target === this) mClose();
});
"""


def ivhv_class(iv_hv):
    if iv_hv < C.IV_HV_CHEAP: return "ivhv-cheap", "CHEAP"
    if iv_hv < C.IV_HV_NEUTRAL: return "ivhv-neut", "NEUTRAL"
    if iv_hv < C.IV_HV_RICH: return "ivhv-rich", "ELEVATED"
    return "ivhv-vrich", "RICH"

def render_underlying_block(u_plan):
    p = u_plan
    brk = " · MOO (fresh 20d high)" if p["is_breakout"] else ""
    rows = "\n".join(
        f"""<tr>
          <td class="acc-name">{s['account']}</td>
          <td>{s['shares'] if s['shares']>0 else '—'}</td>
          <td class="{'acc-0' if s['shares']==0 else ''}">${s['risk_dollars']:,.0f}</td>
          <td>${s['notional']:,.0f}</td>
          {'<td class="dg-note">notional cap</td>' if s['capped'] else '<td></td>'}
        </tr>""" for s in p["account_sizing"])
    return f"""
<div class="tblock">
  <div class="tblock-hdr">UNDERLYING — SHARES</div>
  <div class="trow"><span class="tl">Entry</span>
    <span class="tv tv-entry">${p['entry']:.2f}{brk}</span></div>
  <div class="trow"><span class="tl">Stop</span>
    <span class="tv tv-stop">${p['stop']:.2f} · −{C.STOP_ATR_MULT}×ATR</span></div>
  <div class="trow"><span class="tl">Target</span>
    <span class="tv tv-target">${p['target']:.2f} · +{int(C.TARGET_ATR_MULT/C.STOP_ATR_MULT)}R</span></div>
  <div class="trow"><span class="tl">Time stop</span>
    <span class="tv">Day 10 MOC</span></div>
  <table class="atbl">
    <thead><tr><th>Account</th><th>Shares</th><th>Risk</th><th>Notional</th><th></th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""

def render_options_block(opt):
    if opt is None or "error" in opt:
        err = opt["error"] if opt else "fetch failed"
        return f"""
<div class="tblock">
  <div class="tblock-hdr">OPTIONS</div>
  <div style="color:var(--muted);font-size:12px;margin-top:8px">
    ⚠ Options data unavailable: {err}
  </div>
</div>"""

    pp = opt["primary"]
    iv_hv = opt["iv_hv"]
    ivc, ivl = ivhv_class(iv_hv)
    ivr_html = (f' · IVR <b>{opt["ivr"]:.0f}</b>' if "ivr" in opt else "")
    src_badge = (' <span style="font-size:9px;color:var(--cyan);font-weight:700">TWS</span>'
                 if opt.get("data_source") == "TWS" else "")

    # Legs display
    ls, ss = pp["long_strike"], pp.get("short_strike")
    struct = pp["structure"]
    if struct == "long_call":
        legs = f"Long {ls:.0f}C"
        debit_lbl = f"${pp['net_debit']:.2f} debit"
    elif struct == "debit_spread":
        legs = f"Long {ls:.0f}C / Short {ss:.0f}C"
        debit_lbl = f"${pp['net_debit']:.2f} debit · max ${pp['spread_width']:.0f}"
    elif struct == "credit_spread":
        legs = f"Long {ls:.0f}P / Short {ss:.0f}P"
        debit_lbl = f"${pp['net_credit']:.2f} credit · max ${pp['spread_width']:.0f}"
    else:  # diagonal
        legs = f"Short {ss:.0f}C ({pp.get('dte_front','?')}d) / Long {ls:.0f}C ({pp['dte']}d)"
        debit_lbl = f"${pp['net_debit']:.2f} debit"

    acc_rows = []
    for s in opt["account_sizing"]:
        if s["contracts"] == 0:
            hint = f" · min ~${s['min_hint']:,}" if s.get("min_hint") else ""
            acc_rows.append(f"""<tr>
              <td class="acc-name">{s['account']}</td>
              <td class="acc-0">0 cts</td>
              <td class="acc-0">—</td>
              <td class="hint">account too small{hint}</td>
            </tr>""")
        else:
            dg = f" <span class='dg-note'>↓{s['label']}</span>" if s["downgraded"] else ""
            acc_rows.append(f"""<tr>
              <td class="acc-name">{s['account']}</td>
              <td>{s['contracts']} ct{'s' if s['contracts']>1 else ''}</td>
              <td>${s['risk_dollars']:,.0f}</td>
              <td>${s['notional']:,.0f}{dg}</td>
            </tr>""")

    return f"""
<div class="tblock">
  <div class="tblock-hdr">OPTIONS{src_badge}
    <span class="ivhv {ivc}" style="margin-left:8px">
      IV/HV {iv_hv:.2f} · {ivl}{ivr_html}
    </span>
  </div>
  <div class="trow"><span class="tl">Structure</span>
    <span class="tv tv-struct">{pp['label']}</span></div>
  <div class="trow"><span class="tl">Expiry</span>
    <span class="tv">{pp['expiry']} ({pp['dte']}d)</span></div>
  <div class="trow"><span class="tl">Legs</span>
    <span class="tv">{legs}</span></div>
  <div class="trow"><span class="tl">Premium</span>
    <span class="tv">{debit_lbl}</span></div>
  <div class="trow"><span class="tl">Target</span>
    <span class="tv tv-target">{pp['target_label']}</span></div>
  <div class="trow"><span class="tl">Max loss/ct</span>
    <span class="tv tv-stop">${pp['max_loss_per_contract']:.0f}</span></div>
  <table class="atbl">
    <thead><tr><th>Account</th><th>Contracts</th><th>Risk</th><th>Notional / Note</th></tr></thead>
    <tbody>{''.join(acc_rows)}</tbody>
  </table>
</div>"""

def render_card(r, detailed):
    ticker, score = r["ticker"], r["score"]
    trio   = "PASS" if r["trio_pass"] else "FAIL"
    tc     = "var(--green)" if r["trio_pass"] else "var(--red)"
    sc     = f"s{score}" if score>=5 else "slow"
    cc     = "c-strong" if r["action"]=="STRONG BUY" else ("c-watch" if r["action"]=="WATCH" else "c-skip")

    fgrid = "".join(
        f'<div class="f {"fp" if ok else "ff"}"><span>{k}</span>'
        f'<span class="fmark">{"✓" if ok else "✗"}</span></div>'
        for k,ok in r["factors"].items())

    header = f"""
<div class="card {cc}">
  <div class="card-hdr">
    <div>
      <span class="ticker">{ticker}</span>
      <span class="tmeta">  {r.get('industry','')}  ·  ${r['close']:.2f}
        · ATR {r['atr']:.2f} ({r['atr_pct']:.1f}%)  · RSI {r['rsi']:.0f}  · ADX {r['adx']:.0f}</span>
    </div>
    <div>
      <span class="spill {sc}">Score {score}/8</span>
      <span style="color:{tc};margin-left:8px;font-size:11px;font-weight:700">TRIO {trio}</span>
    </div>
  </div>
  <div class="fgrid">{fgrid}</div>"""

    trade = ""
    btn   = ""
    if detailed and "u_plan" in r:
        trade = f"""
  <div class="trade-grid">
    {render_underlying_block(r['u_plan'])}
    {render_options_block(r.get('opt'))}
  </div>"""
        btn = (
            '\n  <div style="text-align:right;margin-top:10px">'
            f'<button class="push-tws-btn" data-order="{html.escape(_order_json(r))}"'
            ' onclick="mOpen(this)">&#x1F4E4; Push to TWS</button></div>'
        )

    return header + trade + btn + "</div>"


def _order_json(r: dict) -> str:
    """Serialise trade plan data for embedding in the HTML button data-order attribute."""
    u   = r["u_plan"]
    opt = r.get("opt")
    data: dict = {
        "ticker": r["ticker"],
        "shares": {
            "entry":      round(u["entry"],  4),
            "stop":       round(u["stop"],   4),
            "target":     round(u["target"], 4),
            "entry_type": "MOO" if u["is_breakout"] else "LMT",
            "sizing": [
                {"account": s["account"], "shares": s["shares"], "notional": s["notional"]}
                for s in u["account_sizing"]
            ],
        },
    }
    if opt and "ok" in opt:
        pp = opt["primary"]
        data["options"] = {
            "structure":    pp["structure"],
            "label":        pp["label"],
            "expiry":       pp["expiry"],
            "expiry_front": pp.get("expiry_front"),
            "long_strike":  pp["long_strike"],
            "short_strike": pp.get("short_strike"),
            "limit_price":  round(pp.get("net_debit") or pp.get("net_credit") or 0, 4),
            "dte":          pp["dte"],
            "dte_front":    pp.get("dte_front"),
            "sizing": [
                {"account": s["account"], "contracts": s["contracts"], "label": s["label"]}
                for s in opt["account_sizing"]
            ],
        }
    return json.dumps(data)

def render_html(ctx):
    regime = ctx["regime"]; ts = ctx["timestamp"]
    strong, watch, skip, dropped = ctx["strong"],ctx["watch"],ctx["skip"],ctx["dropped"]

    # Account summary row
    acc_meta = "  ".join(
        f"<b>{a['name']}</b> ${a['equity']:,} @ {a['risk_pct']}%"
        for a in C.ACCOUNTS)

    tws_badge_html = ""
    if ctx.get("tws_active"):
        pos_count = len(ctx.get("tws_positions") or [])
        pos_note = f"  {pos_count} open pos" if pos_count else ""
        tws_badge_html = (
            f'<span style="font-size:10px;font-weight:700;color:var(--cyan);'
            f'border:1px solid var(--cyan);border-radius:3px;padding:1px 6px;margin-left:8px">'
            f'TWS LIVE{pos_note}</span>'
        )
    elif _TWS_MODULE:
        tws_badge_html = (
            '<span style="font-size:10px;color:var(--muted);border:1px solid var(--border);'
            'border-radius:3px;padding:1px 6px;margin-left:8px">TWS FALLBACK</span>'
        )

    header = f"""
<div class="hdr">
  <div class="hdr-row">
    <div><h1>STFS-EQ BATTLE CARD</h1>
      <div style="color:var(--muted);font-size:11px;margin-top:2px">Session: {ts}</div>
    </div>
    <div style="display:flex;align-items:center;gap:8px">
      <span class="rbadge r-{regime}">{regime.replace('_',' ')}</span>
      {tws_badge_html}
    </div>
  </div>
  <div class="hdr-meta">{acc_meta}</div>
</div>"""

    if ctx.get("cash_only"):
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>STFS-EQ CRASH</title>{CSS}</head><body><div class="wrap">
{header}
<div class="card c-cash"><h2 style="color:var(--red)">⛔ CRASH — CASH ONLY</h2>
<p>No equity trades. Go to SGOV / BIL. Session complete.</p></div>
</div></body></html>"""

    summary = f"""
<div class="sum">
  <div class="st"><span class="st-lbl">Universe</span>
    <span class="st-val">{ctx['universe_size']}</span></div>
  <div class="st"><span class="st-lbl">Dropped</span>
    <span class="st-val" style="color:var(--muted)">{len(dropped)}</span></div>
  <div class="st"><span class="st-lbl">Strong Buy</span>
    <span class="st-val" style="color:var(--green)">{len(strong)}</span></div>
  <div class="st"><span class="st-lbl">Watch</span>
    <span class="st-val" style="color:var(--amber)">{len(watch)}</span></div>
  <div class="st"><span class="st-lbl">Skip</span>
    <span class="st-val" style="color:var(--muted)">{len(skip)}</span></div>
</div>"""

    strong_html = (
        f'<h2 style="color:var(--green)">🎯 STRONG BUY ({len(strong)})</h2>' +
        "".join(render_card(r,True) for r in strong)
    ) if strong else '<div class="card"><h2 style="color:var(--muted)">No STRONG BUY today — regret-free session.</h2></div>'

    watch_html = ""
    if watch:
        watch_html = f'<h2 style="color:var(--amber)">⏳ WATCH ({len(watch)})</h2>' + \
                     "".join(render_card(r,False) for r in watch)

    skip_rows = "".join(
        f"<tr><td><b>{r['ticker']}</b></td><td>{r['score']}/8</td>"
        f"<td>{'✓' if r['trio_pass'] else '✗'}</td><td>${r['close']:.2f}</td></tr>"
        for r in skip)
    skip_html = f"""
<details class="card c-skip" style="padding:8px 20px">
  <summary style="cursor:pointer;font-weight:600;color:var(--muted)">
    ▸ Skip ({len(skip)}) — click to expand</summary>
  <table class="drop"><thead>
    <tr><th>Ticker</th><th>Score</th><th>Trio</th><th>Close</th></tr>
  </thead><tbody>{skip_rows}</tbody></table>
</details>""" if skip else ""

    drop_rows = "".join(
        f"<tr><td><b>{d['ticker']}</b></td><td>{d['reason']}</td></tr>"
        for d in dropped)
    drop_html = f"""
<details class="card c-skip" style="padding:8px 20px">
  <summary style="cursor:pointer;font-weight:600;color:var(--muted)">
    ▸ Dropped by structural gates ({len(dropped)})</summary>
  <table class="drop"><thead>
    <tr><th>Ticker</th><th>Reason</th></tr>
  </thead><tbody>{drop_rows}</tbody></table>
</details>""" if dropped else ""

    gate_html = """
<div class="gate">
  <h2 style="color:var(--amber);margin-top:0">🚦 Last-Hour 5-Gate — ALL must be YES</h2>
  <div class="gate-row"><span class="cb"></span>
    <span><b>1.</b> Regime alignment — sector is in regime's favored list</span></div>
  <div class="gate-row"><span class="cb"></span>
    <span><b>2.</b> Score ≥7 + Mandatory Trio passes</span></div>
  <div class="gate-row"><span class="cb"></span>
    <span><b>3.</b> Risk sizing — within per-trade AND ≤2% total new risk across all books today</span></div>
  <div class="gate-row"><span class="cb"></span>
    <span><b>4.</b> Sector concentration — &lt;2 open positions in same GICS sector</span></div>
  <div class="gate-row"><span class="cb"></span>
    <span><b>5.</b> Sector rotation — LEADING or IMPROVING on MacroNexus RRG</span></div>
  <div class="alert alert-r" style="margin-top:14px">
    <span>⛔</span><span><strong>Any NO = skip. No exceptions.</strong></span></div>
</div>"""

    # Open positions panel — grouped by account (only when TWS supplied them)
    positions_html = ""
    tws_pos = ctx.get("tws_positions")
    if tws_pos:
        # Collect unique accounts preserving order of first appearance
        seen, accounts = set(), []
        for p in tws_pos:
            if p["account"] not in seen:
                seen.add(p["account"])
                accounts.append(p["account"])

        acc_blocks = []
        for acc_id in accounts:
            acc_pos = [p for p in tws_pos if p["account"] == acc_id]
            rows = "".join(
                f"<tr><td><b>{p['ticker']}</b></td>"
                f"<td>{p['shares']}sh</td>"
                f"<td>${p['avg_cost']:.2f}</td>"
                f"<td style='color:var(--muted)'>${p['shares']*p['avg_cost']:,.0f}</td></tr>"
                for p in acc_pos
            )
            acc_blocks.append(f"""
  <div style="margin-top:10px">
    <div style="font-size:10px;font-weight:700;color:var(--cyan);
                letter-spacing:.08em;text-transform:uppercase;margin-bottom:4px">
      {acc_id} &nbsp;·&nbsp; {len(acc_pos)} position{'s' if len(acc_pos)!=1 else ''}
    </div>
    <table class="drop"><thead>
      <tr><th>Ticker</th><th>Shares</th><th>Avg Cost</th><th>Notional</th></tr>
    </thead><tbody>{rows}</tbody></table>
  </div>""")

        positions_html = f"""
<details class="card" style="padding:8px 20px;border-left:3px solid var(--cyan)">
  <summary style="cursor:pointer;font-weight:600;color:var(--cyan)">
    ▸ TWS Open Positions — {len(accounts)} account{'s' if len(accounts)!=1 else ''} · {len(tws_pos)} total
  </summary>
  {''.join(acc_blocks)}
</details>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>STFS-EQ · {regime} · {ts}</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Syne:wght@700;800&display=swap" rel="stylesheet">
{CSS}
<style>{MODAL_CSS}</style>
</head><body><div class="wrap">
{header}{summary}{positions_html}
{strong_html}{watch_html}{skip_html}{drop_html}{gate_html}
<div class="foot">STFS-EQ v2.0 · Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
</div>
{MODAL_HTML}
<script>{MODAL_JS}</script>
</body></html>"""


# ============================================================================
# MAIN PIPELINE
# ============================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("regime", choices=list(C.WATCHLISTS.keys()))
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    regime = args.regime; ts = date.today().isoformat()
    tws_live = _tws_connected()
    data_src = "TWS+yfinance" if tws_live else "yfinance/Finnhub"
    print(f"\n{'='*60}\n  STFS-EQ v2.0  ·  {regime}  ·  {ts}  ·  {data_src}\n{'='*60}\n")

    # Open positions from TWS (shown in HTML; also useful for concentration gate)
    tws_positions = None
    if tws_live:
        tws_positions = _tws_positions()
        if tws_positions:
            print(f"  ✓ TWS open positions: {len(tws_positions)}")
            for p in tws_positions:
                print(f"    {p['account']}: {p['ticker']} {p['shares']}sh @ ${p['avg_cost']:.2f}")

    out_dir = Path(__file__).parent / C.OUTPUT_DIR
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"battle_card_{regime}_{ts}.html"

    if regime == "CRASH":
        ctx = {"regime":regime,"timestamp":ts,"cash_only":True,
               "strong":[],"watch":[],"skip":[],"dropped":[],"universe_size":0,
               "tws_active":tws_live,"tws_positions":tws_positions}
        out_path.write_text(render_html(ctx))
        print("  ⛔ CRASH — cash only. No trades.")
        if C.AUTO_OPEN_IN_BROWSER and not args.no_open:
            webbrowser.open(f"file://{out_path.resolve()}")
        return

    watchlist = C.WATCHLISTS.get(regime, [])
    if not watchlist:
        print(f"  No watchlist for {regime}."); sys.exit(1)

    print(f"  Stage 1: {len(watchlist)} names in universe")

    print("  ▸ Earnings calendar...")
    blackout = fetch_earnings_calendar(C.EARNINGS_BLACKOUT_DAYS)
    print(f"    {len(blackout)} companies reporting in next {C.EARNINGS_BLACKOUT_DAYS} days")

    print("  ▸ Profiles (Finnhub)...")
    profiles = {}
    for t in watchlist:
        profiles[t] = fetch_profile(t)
        time.sleep(0.05)  # 60/min rate limit safety

    print(f"  ▸ OHLC download ({len(watchlist)} tickers + benchmark)...")
    all_tickers = list(set(watchlist + [C.BENCHMARK]))
    ohlc = fetch_daily_ohlc(all_tickers)
    print(f"    Got {len(ohlc)}/{len(all_tickers)} tickers")

    bench_df = ohlc.get(C.BENCHMARK)
    if bench_df is None:
        print(f"  ✗ Benchmark {C.BENCHMARK} missing."); sys.exit(1)

    # Stage 1 gates
    dropped, passed = [], []
    for t in watchlist:
        reason = None
        if t in blackout:
            reason = f"earnings ≤{C.EARNINGS_BLACKOUT_DAYS}d"
        elif t not in ohlc:
            reason = "no OHLC"
        else:
            df = ohlc[t]
            prc = float(df["Close"].iloc[-1])
            if prc < C.MIN_PRICE:
                reason = f"price ${prc:.2f} < ${C.MIN_PRICE}"
            else:
                adv = float((df["Close"]*df["Volume"]).tail(20).mean())
                if adv < C.MIN_ADV_USD:
                    reason = f"ADV ${adv/1e6:.0f}M < ${C.MIN_ADV_USD/1e6:.0f}M"
                else:
                    mcap = profiles.get(t,{}).get("marketCap_M",0)
                    if mcap and mcap < C.MIN_MARKET_CAP_M:
                        reason = f"mcap ${mcap:.0f}M < ${C.MIN_MARKET_CAP_M:.0f}M"
        if reason: dropped.append({"ticker":t,"reason":reason})
        else: passed.append(t)
    print(f"  ▸ Structural gates: {len(passed)} pass, {len(dropped)} dropped")

    # Stage 2 scoring
    print(f"  Stage 2: Scoring {len(passed)} candidates...")
    results = []
    for t in passed:
        info = score_ticker(ohlc[t], bench_df, is_benchmark=(t==C.BENCHMARK))
        if "error" in info:
            dropped.append({"ticker":t,"reason":info["error"]}); continue
        info["ticker"] = t
        info["industry"] = profiles.get(t,{}).get("industry","")
        results.append(info)

    strong = sorted([r for r in results if r["action"]=="STRONG BUY"],
                    key=lambda x:(-x["score"],-x["rs_pct"]))
    watch  = sorted([r for r in results if r["action"]=="WATCH"],  key=lambda x:-x["score"])
    skip   = sorted([r for r in results if r["action"]=="SKIP"],   key=lambda x:-x["score"])

    # Stage 3: trade construction — only for STRONG BUY
    print(f"\n  Results: {len(strong)} STRONG BUY · {len(watch)} WATCH · {len(skip)} SKIP")
    if strong:
        print(f"  Stage 3: Building trade plans + options data for {len(strong)} STRONG BUYs...")
    for r in strong:
        r["u_plan"] = compute_underlying_plan(r)
        print(f"    ▸ {r['ticker']:6s} score {r['score']}/8 — fetching options...", end=" ", flush=True)
        r["opt"] = fetch_options_data(r["ticker"], ohlc[r["ticker"]])
        if "ok" in r["opt"]:
            pp   = r["opt"]["primary"]
            src  = " [TWS]" if r["opt"].get("data_source") == "TWS" else ""
            ivr_str = f" · IVR {r['opt']['ivr']:.0f}" if "ivr" in r["opt"] else ""
            print(f"{pp['label']} · IV/HV {r['opt']['iv_hv']:.2f}{ivr_str}{src}")
        else:
            print(f"options n/a ({r['opt'].get('error','?')})")

    # Print underlying summary to terminal
    print()
    for r in strong:
        u = r["u_plan"]
        print(f"    🎯 {r['ticker']:6s}  close ${r['close']:.2f}"
              f"  entry ${u['entry']:.2f}  stop ${u['stop']:.2f}  target ${u['target']:.2f}")
        for s in u["account_sizing"]:
            print(f"         {s['account']}: {s['shares']}sh · ${s['notional']:.0f} notional")
    for r in watch:
        print(f"    ⏳ {r['ticker']:6s}  score {r['score']}/8  ${r['close']:.2f}")

    ctx = {"regime":regime,"timestamp":ts,"universe_size":len(watchlist),
           "strong":strong,"watch":watch,"skip":skip,"dropped":dropped,
           "tws_active":tws_live,"tws_positions":tws_positions}
    out_path.write_text(render_html(ctx))
    print(f"\n  ▸ Battle card: {out_path}")

    if C.AUTO_OPEN_IN_BROWSER and not args.no_open:
        webbrowser.open(f"file://{out_path.resolve()}")
        print("  ▸ Opened in browser")

if __name__ == "__main__":
    main()