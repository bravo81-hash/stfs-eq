"""
regime.py — Auto-regime detection for STFS-EQ.

Ports STFS v2.5.pine context engine + MacroNexus_sector rotation.pine RRG logic.
TWS-first via tws_data.get_index(); yfinance fallback for any feed TWS can't serve.

Public API:
    detect_regime() -> dict
        {
          "regime":      "GOLDILOCKS" | "LIQUIDITY" | "REFLATION" |
                         "NEUTRAL"   | "RISK_OFF"  | "CRASH",
          "confidence":  "HIGH" | "MED" | "LOW",
          "states":      {drift, vol, term, skew, credit, event},
          "macro":       {is_risk_off, is_reflation, is_liquidity},
          "rrg":         {sector: {x, y, quad}},
          "evidence":    [(label, value, source, age_days), ...],
          "warnings":    [str, ...],
        }
"""

from __future__ import annotations

import math
import warnings
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

import config as C

try:
    import yfinance as yf
    _YF = True
except ImportError:
    _YF = False

try:
    from tws_data import get_index as _tws_index, tws_connected as _tws_connected
    _TWS = True
except ImportError:
    _TWS = False
    def _tws_connected(): return False
    def _tws_index(*a, **kw): return None


# ── feed fetching ────────────────────────────────────────────────────────────

def _bar_age_days(df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 999
    last = df.index[-1]
    if hasattr(last, "to_pydatetime"):
        last = last.to_pydatetime().date()
    elif hasattr(last, "date"):
        last = last.date()
    return max(0, (date.today() - last).days)


def _fetch_yf(symbol: str, days: int) -> Optional[pd.DataFrame]:
    if not _YF:
        return None
    start = (date.today() - timedelta(days=days)).isoformat()
    try:
        df = yf.download(symbol, start=start, interval="1d",
                         auto_adjust=True, progress=False, threads=False)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df.dropna(subset=["Close"])
    except Exception:
        return None


# Symbols TWS can't serve via Stock/Index secType — skip straight to yfinance.
_YF_ONLY = {"BTC", "BTC-USD"}


def _fetch_feed(key: str, days: int = 120) -> tuple[Optional[pd.DataFrame], str, int]:
    """Return (df, source, age_days). Source: 'TWS', 'yfinance', or 'NONE'."""
    yf_sym = C.REGIME_FEEDS.get(key, key)
    # TWS path — index/etf via tws_data.get_index (skip for known-incompatible symbols)
    if _TWS and _tws_connected() and key not in _YF_ONLY:
        df = _tws_index(yf_sym.lstrip("^"), lookback_days=days)
        if df is not None and not df.empty:
            return df, "TWS", _bar_age_days(df)
    # yfinance fallback
    df = _fetch_yf(yf_sym, days)
    if df is not None and not df.empty:
        return df, "yfinance", _bar_age_days(df)
    return None, "NONE", 999


# ── derived state classifiers ────────────────────────────────────────────────

def _drift_state(spy: pd.DataFrame) -> str:
    if spy is None or len(spy) < 12:
        return "FLAT"
    ret_10d = (spy["Close"].iloc[-1] / spy["Close"].iloc[-11] - 1.0) * 100.0
    if ret_10d > C.DRIFT_STRONG_UP: return "STRONG_UP"
    if ret_10d > C.DRIFT_MILD_UP:   return "MILD_UP"
    if ret_10d < C.DRIFT_STRONG_DN: return "STRONG_DN"
    if ret_10d < C.DRIFT_MILD_DN:   return "MILD_DN"
    return "FLAT"


def _atr_pct(df: pd.DataFrame, n: int) -> Optional[float]:
    if df is None or len(df) < n + 1:
        return None
    hi, lo, cl = df["High"], df["Low"], df["Close"]
    tr = pd.concat([hi - lo, (hi - cl.shift()).abs(), (lo - cl.shift()).abs()],
                   axis=1).max(axis=1)
    atr = tr.rolling(n).mean().iloc[-1]
    px = cl.iloc[-1]
    if not (atr and px and math.isfinite(atr) and math.isfinite(px) and px > 0):
        return None
    return float(atr / px * 100.0)


def _vol_state(spy: pd.DataFrame) -> str:
    fast = _atr_pct(spy, C.BONUS_ATR_FAST)
    slow = _atr_pct(spy, C.BONUS_ATR_SLOW)
    if fast is None or slow is None or slow <= 0:
        return "NORMAL"
    ratio = fast / slow
    if ratio > C.VOL_EXPANDING:  return "EXPANDING"
    if ratio < C.VOL_COMPRESSED: return "COMPRESSED"
    return "NORMAL"


def _term_state(vix: pd.DataFrame, vix3m: pd.DataFrame) -> str:
    if vix is None or vix3m is None or vix.empty or vix3m.empty:
        return "FLAT"
    v, v3 = float(vix["Close"].iloc[-1]), float(vix3m["Close"].iloc[-1])
    if v3 <= 0: return "FLAT"
    r = v / v3
    if r > C.TERM_BACKWARDATION: return "BACKWARDATION"
    if r < C.TERM_CONTANGO:      return "CONTANGO"
    return "FLAT"


def _skew_state(skew: pd.DataFrame) -> str:
    if skew is None or skew.empty:
        return "NORMAL"
    s = float(skew["Close"].iloc[-1])
    if s > C.SKEW_CRASH_FEAR:  return "CRASH_FEAR"
    if s < C.SKEW_COMPLACENT:  return "COMPLACENT"
    return "NORMAL"


def _credit_state(hyg: pd.DataFrame) -> str:
    if hyg is None or len(hyg) < 6:
        return "NEUTRAL"
    pct5 = (hyg["Close"].iloc[-1] / hyg["Close"].iloc[-6] - 1.0) * 100.0
    if pct5 < C.CREDIT_STRESSED: return "STRESSED"
    if pct5 > C.CREDIT_BID:      return "BID"
    return "NEUTRAL"


def _event_state(vix9d: pd.DataFrame, vix: pd.DataFrame) -> str:
    if vix9d is None or vix is None or vix9d.empty or vix.empty:
        return "NORMAL"
    v9, v = float(vix9d["Close"].iloc[-1]), float(vix["Close"].iloc[-1])
    if v <= 0: return "NORMAL"
    if v9 / v > C.EVENT_PRICED_IN: return "EVENT_PRICED_IN"
    return "NORMAL"


# ── RRG quadrant per sector ──────────────────────────────────────────────────

def _rrg(sector: pd.DataFrame, bench: pd.DataFrame) -> Optional[dict]:
    """RRG x/y on relative-strength line vs benchmark.
    x = trend (20d) deviation, y = momentum (5d) deviation, both *100."""
    if sector is None or bench is None:
        return None
    n = max(C.RRG_TREND_LOOKBACK, C.RRG_MOMENTUM_LOOKBACK) + 2
    if len(sector) < n or len(bench) < n:
        return None
    rs = sector["Close"] / bench["Close"].reindex(sector.index).ffill()
    rs = rs.dropna()
    if len(rs) < n:
        return None
    now   = float(rs.iloc[-1])
    trend = float(rs.iloc[-1 - C.RRG_TREND_LOOKBACK])
    mom   = float(rs.iloc[-1 - C.RRG_MOMENTUM_LOOKBACK])
    if trend <= 0 or mom <= 0:
        return None
    x = (now - trend) / trend * 100.0
    y = (now - mom)   / mom   * 100.0
    if   x > 0 and y > 0: quad = "LEADING"
    elif x < 0 and y > 0: quad = "IMPROVING"
    elif x > 0 and y < 0: quad = "WEAKENING"
    else:                  quad = "LAGGING"
    return {"x": round(x, 2), "y": round(y, 2), "quad": quad}


# ── regime mapping ───────────────────────────────────────────────────────────

def _map_regime(states: dict, macro: dict) -> str:
    drift, vol  = states["drift"], states["vol"]
    term, skew  = states["term"],  states["skew"]
    credit      = states["credit"]

    # CRASH: backwardated + credit stress + crash fear
    if term == "BACKWARDATION" and credit == "STRESSED" and skew == "CRASH_FEAR":
        return "CRASH"

    # RISK_OFF: defensive rotation OR strong-down + expanding vol
    if macro["is_risk_off"] or (drift == "STRONG_DN" and vol == "EXPANDING"):
        return "RISK_OFF"

    # LIQUIDITY: tech+BTC up, drift positive, vol not blowing out
    if macro["is_liquidity"] and drift in ("MILD_UP", "STRONG_UP") and vol != "EXPANDING":
        return "LIQUIDITY"

    # REFLATION: energy+banks rotating, drift positive
    if macro["is_reflation"] and drift in ("MILD_UP", "STRONG_UP"):
        return "REFLATION"

    # GOLDILOCKS: strong-up trend, compressed vol, complacent skew
    if drift == "STRONG_UP" and vol == "COMPRESSED" and skew == "COMPLACENT":
        return "GOLDILOCKS"

    return "NEUTRAL"


# ── public API ───────────────────────────────────────────────────────────────

def detect_regime() -> dict:
    feeds = {}
    evidence = []
    warnings_list = []
    stale_count = 0

    for key in C.REGIME_FEEDS:
        df, src, age = _fetch_feed(key, days=120)
        feeds[key] = df
        if df is None or df.empty:
            evidence.append((key, None, "NONE", 999))
            warnings_list.append(f"{key}: no data")
            stale_count += 1
            continue
        last_close = float(df["Close"].iloc[-1])
        evidence.append((key, round(last_close, 2), src, age))
        if age >= C.STALENESS_BARS_WARN:
            stale_count += 1
            warnings_list.append(f"{key}: {age} bars stale ({src})")

    spy   = feeds.get("SPY")
    vix   = feeds.get("VIX")
    vix3m = feeds.get("VIX3M")
    vix9d = feeds.get("VIX9D")
    skew  = feeds.get("SKEW")
    hyg   = feeds.get("HYG")

    states = {
        "drift":  _drift_state(spy),
        "vol":    _vol_state(spy),
        "term":   _term_state(vix, vix3m),
        "skew":   _skew_state(skew),
        "credit": _credit_state(hyg),
        "event":  _event_state(vix9d, vix),
    }

    # Sector RRG vs SPY
    rrg = {}
    sector_keys = ["XLU", "XLP", "XLK", "XLE", "XLF", "SMH", "QQQ", "IWM"]
    for s in sector_keys:
        r = _rrg(feeds.get(s), spy)
        if r is not None:
            rrg[s] = r

    def _up(sym):   # leading or improving relative to SPY
        return sym in rrg and rrg[sym]["quad"] in ("LEADING", "IMPROVING")

    def _down(sym):
        return sym in rrg and rrg[sym]["quad"] in ("LAGGING", "WEAKENING")

    macro = {
        "is_risk_off":  _up("XLU") and _up("XLP") and _down("XLK"),
        "is_reflation": _up("XLE") and _up("XLF"),
        "is_liquidity": _up("XLK") and _up("SMH"),  # BTC RRG vs SPY skipped — different beta
    }

    regime = _map_regime(states, macro)

    if stale_count >= 3:
        confidence = "LOW"
    elif stale_count >= 1:
        confidence = "MED"
    else:
        confidence = "HIGH"

    return {
        "regime":     regime,
        "confidence": confidence,
        "states":     states,
        "macro":      macro,
        "rrg":        rrg,
        "evidence":   evidence,
        "warnings":   warnings_list,
    }


if __name__ == "__main__":
    r = detect_regime()
    print(f"Regime: {r['regime']} (confidence {r['confidence']})")
    print(f"States: {r['states']}")
    print(f"Macro:  {r['macro']}")
    print(f"RRG:    {r['rrg']}")
    if r["warnings"]:
        print(f"Warnings: {r['warnings']}")
