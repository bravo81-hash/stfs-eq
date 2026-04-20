import warnings; import pandas as pd; import numpy as np; from datetime import date, timedelta; import yfinance as yf
warnings.filterwarnings("ignore")
import config as C; from battle_card import ema, wilder, wma, hma, rsi, atr, adx_dmi, obv

tickers = ["QQQ", "SPY", "TSLA", "AAPL", "MSFT", "NVDA", "AMD", "META"]
start = (date.today() - timedelta(days=1500)).isoformat()
data = yf.download(tickers=tickers, start=start, interval="1d", group_by="ticker", auto_adjust=True, progress=False)
bench_df = data["SPY"].dropna()

trades = []

# Use the same parameters as deployed in config.py — do not override here.
# (Previous session had STOP=4.0 which mismatched production; fixed in audit.)

for t in tickers:
    df = data[t].dropna()
    if len(df) < 200: continue
    hi, lo, cl, vol = df["High"].values, df["Low"].values, df["Close"].values, df["Volume"].values
    
    ef = ema(df["Close"], C.EMA_FAST).values; em = ema(df["Close"], C.EMA_MID).values; es = ema(df["Close"], C.EMA_SLOW).values
    hm = hma(df["Close"], C.HMA_LEN).values
    rs = rsi(df["Close"], C.RSI_LEN).values
    at = atr(df["High"], df["Low"], df["Close"], C.ATR_LEN).values
    _, _, adx_s = adx_dmi(df["High"], df["Low"], df["Close"], C.ADX_LEN)
    adx_s = adx_s.values
    ob = obv(df["Close"], df["Volume"])
    oe = ema(ob, C.OBV_EMA_LEN).values
    ob = ob.values
    atr_pct = (at / cl) * 100
    
    f1 = (ef > em) & (em > es)
    df_wk = df.resample("W-FRI").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()
    if len(df_wk) > C.WEEKLY_EMA_SLOW:
        wf = ema(df_wk["Close"], C.WEEKLY_EMA_FAST)
        ws = ema(df_wk["Close"], C.WEEKLY_EMA_SLOW)
        f2 = ((df_wk["Close"] > ws) & (wf > ws)).reindex(df.index).ffill().fillna(False).values
    else: f2 = np.zeros(len(df), dtype=bool)
    
    f3 = hm > np.roll(hm, 1); f3[0] = False
    f4 = (adx_s > C.ADX_THRESHOLD) & (adx_s > np.roll(adx_s, 1)) & (np.roll(adx_s,1) > np.roll(adx_s,2))
    f4[:2] = False
    f5 = (rs >= 40) & (rs <= 80)
    
    tr = cl / np.roll(cl, C.RS_LOOKBACK) - 1
    bench_cl = bench_df["Close"].reindex(df.index).ffill().values
    br = bench_cl / np.roll(bench_cl, C.RS_LOOKBACK) - 1
    f6 = tr > br; f6[:C.RS_LOOKBACK] = False
    
    f7 = (ob > oe) & (ob > np.roll(ob, C.OBV_SLOPE_LOOKBACK)); f7[:C.OBV_SLOPE_LOOKBACK] = False
    f8 = (atr_pct >= C.ATR_PCT_MIN) & (atr_pct <= C.ATR_PCT_MAX)
    
    for i in range(100, len(df)-1):
        entry = cl[i] - (C.ENTRY_ATR_MULT * at[i])
        stop = entry - (C.STOP_ATR_MULT * at[i])
        target = entry + (C.TARGET_ATR_MULT * at[i])
        if lo[i+1] <= entry:
            outcome = 0
            for j in range(i+1, min(i+40, len(df))): # limit holding period somewhat
                if lo[j] <= stop: outcome = -1; break
                if hi[j] >= target: outcome = 1; break
            if outcome != 0:
                trades.append({
                    "f1": int(f1[i]), "f2": int(f2[i]), "f3": int(f3[i]), 
                    "f4": int(f4[i]), "f5": int(f5[i]), "f6": int(f6[i]), 
                    "f7": int(f7[i]), "f8": int(f8[i]), 
                    "outcome": outcome, "pl": (target-entry)/entry if outcome==1 else (stop-entry)/entry
                })

import itertools
tdf = pd.DataFrame(trades)

masks = list(itertools.product([0, 1], repeat=8))
res = []
for m in masks:
    cond = pd.Series(True, index=tdf.index)
    for idx, bit in enumerate(m):
        if bit == 1:
            cond = cond & (tdf[f"f{idx+1}"] == 1)
    
    subset = tdf[cond]
    if len(subset) >= 50:
        wins = subset[subset["outcome"]==1]
        losses = subset[subset["outcome"]==-1]
        wr = len(wins) / len(subset)
        avg_w = wins['pl'].mean() if len(wins)>0 else 0
        avg_l = losses['pl'].mean() if len(losses)>0 else 0
        exp = (wr * avg_w) + ((1-wr) * avg_l)
        res.append({
            "mask": "".join(map(str, m)), 
            "trades": len(subset), "win_rate": wr*100, "expectancy": exp*100
        })

rdf = pd.DataFrame(res).sort_values("expectancy", ascending=False)
print("TOP 5 FACTOR SETUPS (Expectancy per Trade)")
print("Each bit corresponds to F1..F8. Example '11000001' means strictly requiring F1, F2, F8.")
print(rdf.head(5).to_string(index=False))

print("\nISOLATED FACTOR EDGE")
b_exp = tdf['pl'].mean() * 100
b_wr = len(tdf[tdf["outcome"]==1]) / len(tdf) * 100
print(f"Blind Limit Entry Baseline: Exp {b_exp:.2f}% | WinRate {b_wr:.1f}%")
for f in [f"f{i}" for i in range(1, 9)]:
    s = tdf[tdf[f]==1]
    w = len(s[s["outcome"]==1]) / len(s) * 100 if len(s)>0 else 0
    e = s['pl'].mean() * 100
    print(f"  {f} REQUIRED: Exp {e:.2f}% (+{e-b_exp:.2f}%)  |  WinRate {w:.1f}% (+{w-b_wr:.1f}%)")
