"""
indicators.py — Shared technical indicators + vectorized factor computation.

Single source of truth for the 8-factor STFS-EQ scoring rules. Both
battle_card.score_ticker (last bar only, live signal) and
battle_card.run_mini_backtest (every bar, walk-forward) MUST call
compute_factors() so they cannot drift apart.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import config as C


# ── primitives ───────────────────────────────────────────────────────────────

def ema(s, n):    return s.ewm(span=n, adjust=False).mean()
def wilder(s, n): return s.ewm(alpha=1 / n, adjust=False).mean()

def wma(s, n):
    w = np.arange(1, n + 1)
    return s.rolling(n).apply(lambda x: np.dot(x, w) / w.sum(), raw=True)

def hma(s, n):
    return wma(2 * wma(s, int(n / 2)) - wma(s, n), int(math.sqrt(n)))

def rsi(s, n=14):
    d = s.diff()
    up, dn = d.clip(lower=0), -d.clip(upper=0)
    rs = wilder(up, n) / wilder(dn, n).replace(0, np.nan)
    return 100 - 100 / (1 + rs)

def atr(hi, lo, cl, n=14):
    tr = pd.concat([hi - lo, (hi - cl.shift()).abs(), (lo - cl.shift()).abs()], axis=1).max(axis=1)
    return wilder(tr, n)

def adx_dmi(hi, lo, cl, n=14):
    um, dm = hi.diff(), -lo.diff()
    pdm = np.where((um > dm) & (um > 0), um, 0.)
    mdm = np.where((dm > um) & (dm > 0), dm, 0.)
    pdm, mdm = pd.Series(pdm, index=hi.index), pd.Series(mdm, index=hi.index)
    tr = pd.concat([hi - lo, (hi - cl.shift()).abs(), (lo - cl.shift()).abs()], axis=1).max(axis=1)
    av = wilder(tr, n)
    pdi = 100 * wilder(pdm, n) / av.replace(0, np.nan)
    mdi = 100 * wilder(mdm, n) / av.replace(0, np.nan)
    dx  = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return pdi, mdi, wilder(dx, n)

def obv(cl, vol):
    return (np.sign(cl.diff()).fillna(0) * vol).cumsum()


# ── unified factor computation (vectorized over full DataFrame) ──────────────

def compute_factors(df: pd.DataFrame, bench_df: pd.DataFrame, is_benchmark: bool = False) -> dict:
    """Compute the 8 STFS-EQ factors plus ATR / score / trio for every bar of df.

    Returns a dict with pandas Series aligned to df.index:
        f1..f8 (bool), score (int), trio (bool), strong_buy (bool),
        rs_pct (float, latest-bar only matters for live; else 0), atr (float),
        atr_pct, rsi, adx, hma, bonus_rsi_slope (bool), bonus_atr_expansion (bool),
        momentum_bonus (int 0..2)

    For live scoring, take the .iloc[-1] of any series. For backtesting, iterate.
    The same factor logic is therefore guaranteed identical across both paths.
    """
    hi, lo, cl, vol = df["High"], df["Low"], df["Close"], df["Volume"]

    ef = ema(cl, C.EMA_FAST)
    em = ema(cl, C.EMA_MID)
    es = ema(cl, C.EMA_SLOW)
    hm = hma(cl, C.HMA_LEN)
    rs_s = rsi(cl, C.RSI_LEN)
    at = atr(hi, lo, cl, C.ATR_LEN)
    _, _, adx_s = adx_dmi(hi, lo, cl, C.ADX_LEN)
    ob = obv(cl, vol)
    oe = ema(ob, C.OBV_EMA_LEN)
    atr_pct = (at / cl) * 100

    # F1: daily EMA stack
    f1 = (ef > em) & (em > es)

    # F2: weekly trend (resample W-FRI matches TradingView weekly bar convention)
    df_weekly = df.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna()
    wema_fast_d = pd.Series(np.nan, index=df.index)
    wema_slow_d = pd.Series(np.nan, index=df.index)
    if len(df_weekly) >= C.WEEKLY_EMA_SLOW + 2:
        wf = ema(df_weekly["Close"], C.WEEKLY_EMA_FAST)
        ws = ema(df_weekly["Close"], C.WEEKLY_EMA_SLOW)
        df_weekly["f2"] = (df_weekly["Close"] > ws) & (wf > ws)
        f2 = df_weekly["f2"].reindex(df.index).ffill().fillna(False)
        wema_fast_d = wf.reindex(df.index).ffill()
        wema_slow_d = ws.reindex(df.index).ffill()
    else:
        f2 = pd.Series(False, index=df.index)

    f3 = hm > hm.shift(1)
    f4 = (adx_s > C.ADX_THRESHOLD) & (adx_s > adx_s.shift(1)) & (adx_s.shift(1) > adx_s.shift(2))
    f5 = (rs_s >= C.RSI_LOWER_BAND) & (rs_s <= C.RSI_UPPER_BAND)

    # F6: relative strength vs benchmark over RS_LOOKBACK
    if is_benchmark or bench_df is None:
        f6 = pd.Series(True, index=df.index)
        rs_pct = pd.Series(0.0, index=df.index)
    else:
        tr = cl / cl.shift(C.RS_LOOKBACK) - 1
        br = bench_df["Close"] / bench_df["Close"].shift(C.RS_LOOKBACK) - 1
        br_aligned = br.reindex(df.index).ffill()
        f6 = tr > br_aligned
        rs_pct = (tr - br_aligned) * 100

    f7 = (ob > oe) & (ob > ob.shift(C.OBV_SLOPE_LOOKBACK))
    f8 = (atr_pct >= C.ATR_PCT_MIN) & (atr_pct <= C.ATR_PCT_MAX)

    score = (f1.astype(int) + f2.astype(int) + f3.astype(int) + f4.astype(int)
             + f5.astype(int) + f6.astype(int) + f7.astype(int) + f8.astype(int))
    trio = f1 & f2 & f8
    strong_buy = (score >= C.STRONG_SCORE_MIN) & trio

    # Bonus momentum factors (Pine v3 — additive)
    bonus_rsi_slope = rs_s > rs_s.shift(C.BONUS_RSI_SLOPE_LOOKBACK)
    atr_fast = atr(hi, lo, cl, C.BONUS_ATR_FAST)
    atr_slow = atr(hi, lo, cl, C.BONUS_ATR_SLOW)
    af_pct = (atr_fast / cl) * 100
    as_pct = (atr_slow / cl) * 100
    bonus_atr_expansion = (af_pct / as_pct.replace(0, np.nan)) > C.BONUS_ATR_EXPANSION_MIN
    momentum_bonus = bonus_rsi_slope.fillna(False).astype(int) + bonus_atr_expansion.fillna(False).astype(int)

    return {
        "f1": f1, "f2": f2, "f3": f3, "f4": f4,
        "f5": f5, "f6": f6, "f7": f7, "f8": f8,
        "score": score, "trio": trio, "strong_buy": strong_buy,
        "rs_pct": rs_pct, "atr": at, "atr_pct": atr_pct,
        "rsi": rs_s, "adx": adx_s, "hma": hm,
        "bonus_rsi_slope": bonus_rsi_slope.fillna(False),
        "bonus_atr_expansion": bonus_atr_expansion.fillna(False),
        "momentum_bonus": momentum_bonus,
        # Raw indicator series — for indicator verification panel in HTML
        "ema_fast": ef,
        "ema_mid": em,
        "ema_slow": es,
        "obv_raw": ob,
        "obv_ema": oe,
        "wema_fast": wema_fast_d,
        "wema_slow": wema_slow_d,
    }
