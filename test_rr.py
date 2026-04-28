import warnings
import pandas as pd
import numpy as np
import itertools
from datetime import date, timedelta
import yfinance as yf
warnings.filterwarnings("ignore")

import config as C
from battle_card import ema, hma, rsi, atr, adx_dmi, obv

def _sweep_params(tdicts, entry_m, stop_m, target_m):
    all_pl = []
    for d in tdicts:
        cl, hi, lo, at, strong = d["cl"], d["hi"], d["lo"], d["at"], d["strong"]
        for i in range(100, len(cl)-1):
            if not strong[i]: continue
            
            entry = cl[i] - (entry_m * at[i])
            stop = entry - (stop_m * at[i])
            target = entry + (target_m * at[i])
            
            if lo[i+1] <= entry:
                # hold limited to 60 days
                outcome = 0
                for j in range(i+1, min(i+60, len(cl))):
                    if lo[j] <= stop: outcome = -1; break
                    if hi[j] >= target: outcome = 1; break
                if outcome == 0:
                    # closed at 60 days
                    outcome = 1 if cl[min(i+59, len(cl)-1)] > entry else -1
                    all_pl.append((cl[min(i+59, len(cl)-1)] - entry) / entry)
                else:
                    all_pl.append((target-entry)/entry if outcome==1 else (stop-entry)/entry)
    
    if len(all_pl) < 5: return None
    wins = [pl for pl in all_pl if pl > 0]
    wr = len(wins) / len(all_pl) * 100
    comp = ((1 + pd.Series(all_pl)).prod() - 1) * 100
    avg_l = np.mean([pl for pl in all_pl if pl < 0]) * 100
    avg_w = np.mean(wins) * 100
    return {"ENTRY": entry_m, "STOP": stop_m, "TARGET": target_m, 
            "TRADES": len(all_pl), "WIN%": wr, "C_RET%": comp, "AVG_L": avg_l, "AVG_W": avg_w}

if __name__ == "__main__":
    # Test a broader universe combining GOLDILOCKS and LIQUIDITY
    tickers = list(set(C.WATCHLISTS["GOLDILOCKS"] + C.WATCHLISTS["LIQUIDITY"]))
    start = (date.today() - timedelta(days=1500)).isoformat()
    print(f"Testing {len(tickers)} high-beta/growth tickers...")
    data = yf.download(tickers=tickers, start=start, interval="1d", group_by="ticker", auto_adjust=True, progress=False)

    try:
        bench_df = yf.download(tickers="SPY", start=start, interval="1d", auto_adjust=True, progress=False).dropna()
    except Exception:
        bench_df = data["QQQ"].dropna()

    tdicts = []
    for t in tickers:
        try:
            df = data[t].dropna()
        except KeyError:
            continue
        if len(df) < 200: continue
        hi, lo, cl, vol = df["High"].values, df["Low"].values, df["Close"].values, df["Volume"].values
        
        ef = ema(df["Close"], C.EMA_FAST).values; em = ema(df["Close"], C.EMA_MID).values; es = ema(df["Close"], C.EMA_SLOW).values
        hm = hma(df["Close"], C.HMA_LEN).values
        rs = rsi(df["Close"], C.RSI_LEN).values
        at = atr(df["High"], df["Low"], df["Close"], C.ATR_LEN).values
        _, _, adx_s = adx_dmi(df["High"], df["Low"], df["Close"], C.ADX_LEN)
        adx_s = adx_s.values
        ob = obv(df["Close"], df["Volume"]).values
        oe = ema(pd.Series(ob), C.OBV_EMA_LEN).values
        atr_pct = (at / cl) * 100
        
        f1 = (ef > em) & (em > es)
        df_wk = df.resample("W-FRI").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()
        if len(df_wk) > C.WEEKLY_EMA_SLOW:
            wf = ema(df_wk["Close"], C.WEEKLY_EMA_FAST)
            ws = ema(df_wk["Close"], C.WEEKLY_EMA_SLOW)
            f2 = ((df_wk["Close"] > ws) & (wf > ws)).reindex(df.index).ffill().fillna(False).values
        else: f2 = np.zeros(len(df), dtype=bool)
        f3 = hm > np.roll(hm, 1); f3[0] = False
        f4 = (adx_s > C.ADX_THRESHOLD) & (adx_s > np.roll(adx_s, 1)) & (np.roll(adx_s,1) > np.roll(adx_s,2)); f4[:2]=False
        f5 = (rs >= 50) & (rs <= 75)
        tr = cl / np.roll(cl, C.RS_LOOKBACK) - 1
        bench_cl = np.asarray(bench_df["Close"].reindex(df.index).ffill()).ravel()
        br = bench_cl / np.roll(bench_cl, C.RS_LOOKBACK) - 1
        f6 = tr > br; f6[:C.RS_LOOKBACK] = False
        f7 = (ob > oe) & (ob > np.roll(ob, C.OBV_SLOPE_LOOKBACK)); f7[:C.OBV_SLOPE_LOOKBACK] = False
        f8 = (atr_pct >= 1.0) & (atr_pct <= C.ATR_PCT_MAX)
        
        score = f1.astype(int) + f2.astype(int) + f3.astype(int) + f4.astype(int) + f5.astype(int) + f6.astype(int) + f7.astype(int) + f8.astype(int)
        trio = f1 & f2 & f8
        strong = (score >= 7) & trio
        
        tdicts.append({
            "t": t, "df": df, "cl": cl, "hi": hi, "lo": lo, "at": at, "strong": strong
        })

    results = []
    for em_val in [0.5, 1.0, 1.5, 2.0]:
        for sm in [1.5, 2.0, 2.5, 3.0, 4.0]:
            for tm in [2.0, 3.0, 4.0, 5.0, 6.0]:
                r = _sweep_params(tdicts, em_val, sm, tm)
                if r: results.append(r)

    dfr = pd.DataFrame(results).sort_values("C_RET%", ascending=False)
    print("\nTop 10 parameters by Compounded Return (Score=7):")
    print(dfr.head(10).to_string(index=False))

    dfr_shallow = dfr[(dfr["AVG_L"] > -5.0) & (dfr["WIN%"] >= 50.0)]
    print("\nTop 5 Setups prioritizing Shallower Max Losses (Avg Loss better than -5%) & WR > 50%:")
    print(dfr_shallow.head(5).to_string(index=False))

