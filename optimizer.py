import warnings; import pandas as pd; import numpy as np; from datetime import date, timedelta; import yfinance as yf; import itertools
warnings.filterwarnings("ignore")
import config as C; from battle_card import ema, wilder, wma, hma, rsi, atr, adx_dmi, obv

tickers = ["QQQ", "SPY", "TSLA", "AAPL", "MSFT", "NVDA"]
start = (date.today() - timedelta(days=1500)).isoformat()
data = yf.download(tickers=tickers, start=start, interval="1d", group_by="ticker", auto_adjust=True, progress=False)
bench_df = data["SPY"].dropna()

pre_data = {}
for t in tickers:
    df = data[t].dropna()
    if len(df) < 200: continue
    hi, lo, cl, vol = df["High"], df["Low"], df["Close"], df["Volume"]
    op = df["Open"]
    ef = ema(cl, C.EMA_FAST); em = ema(cl, C.EMA_MID); es = ema(cl, C.EMA_SLOW)
    hm = hma(cl, C.HMA_LEN); rs = rsi(cl, C.RSI_LEN); at = atr(hi, lo, cl, C.ATR_LEN)
    pdi, mdi, adx_s = adx_dmi(hi, lo, cl, C.ADX_LEN)
    ob = obv(cl, vol); oe = ema(ob, C.OBV_EMA_LEN)
    atr_pct = (at / cl) * 100
    
    f1 = (ef > em) & (em > es)
    df_weekly = df.resample("W-FRI").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()
    if len(df_weekly) >= C.WEEKLY_EMA_SLOW + 2:
        wf = ema(df_weekly["Close"], C.WEEKLY_EMA_FAST)
        ws = ema(df_weekly["Close"], C.WEEKLY_EMA_SLOW)
        df_weekly["f2"] = (df_weekly["Close"] > ws) & (wf > ws)
        f2 = df_weekly["f2"].reindex(df.index).ffill().fillna(False)
    else: f2 = pd.Series(False, index=df.index)
    f3 = hm > hm.shift(1)
    f4 = (adx_s > C.ADX_THRESHOLD) & (adx_s > adx_s.shift(1)) & (adx_s.shift(1) > adx_s.shift(2))
    f5 = (rs >= 40) & (rs <= 80)
    tr = cl / cl.shift(C.RS_LOOKBACK) - 1
    br = bench_df["Close"] / bench_df["Close"].shift(C.RS_LOOKBACK) - 1
    f6 = tr > br.reindex(df.index).ffill()
    f7 = (ob > oe) & (ob > ob.shift(C.OBV_SLOPE_LOOKBACK))
    f8 = (atr_pct >= C.ATR_PCT_MIN) & (atr_pct <= C.ATR_PCT_MAX)
    
    score = f1.astype(int) + f2.astype(int) + f3.astype(int) + f4.astype(int) + f5.astype(int) + f6.astype(int) + f7.astype(int) + f8.astype(int)
    trio = f1 & f2 & f8
    is_brk = (cl >= cl.rolling(C.BREAKOUT_LOOKBACK).max())
    
    pre_data[t] = {"score": score.values, "trio": trio.values, "is_brk": is_brk.values,
                   "cl": cl.values, "at": at.values, "op": op.values, "hi": hi.values, "lo": lo.values, "len": len(df)}

def sim(data, p_min_score, p_entry_mult, p_stop_mult, p_target_mult):
    all_trades = []
    for t, d in data.items():
        sb_a = (d["score"] >= p_min_score) & d["trio"]
        in_trade = False; limit_order_active = False 
        entry_price = stop_loss = take_profit = p_limit = p_stop_d = p_tar_d = 0.0; pending_brk = False
        cl_a = d["cl"]; at_a = d["at"]; op_a = d["op"]; hi_a = d["hi"]; lo_a = d["lo"]; brk_a = d["is_brk"]
        for i in range(d["len"] - 1):
            if in_trade:
                if lo_a[i+1] <= stop_loss:
                    all_trades.append((stop_loss - entry_price) / entry_price); in_trade = False
                elif hi_a[i+1] >= take_profit:
                    all_trades.append((take_profit - entry_price) / entry_price); in_trade = False
                continue
            if limit_order_active:
                if pending_brk:
                    entry_price = op_a[i+1]; stop_loss = entry_price - p_stop_d; take_profit = entry_price + p_tar_d
                    limit_order_active = False; in_trade = True
                    if lo_a[i+1] <= stop_loss:
                        all_trades.append((stop_loss - entry_price) / entry_price); in_trade = False
                    elif hi_a[i+1] >= take_profit:
                        all_trades.append((take_profit - entry_price) / entry_price); in_trade = False
                else:
                    if lo_a[i+1] <= p_limit:
                        entry_price = p_limit; stop_loss = entry_price - p_stop_d; take_profit = entry_price + p_tar_d
                        limit_order_active = False; in_trade = True
                        if lo_a[i+1] <= stop_loss:
                            all_trades.append((stop_loss - entry_price) / entry_price); in_trade = False
                    else: limit_order_active = False
            if not in_trade and not limit_order_active:
                if sb_a[i]:
                    pending_brk = brk_a[i]
                    if pending_brk:
                        p_stop_d = p_stop_mult * at_a[i]; p_tar_d = p_target_mult * at_a[i]
                    else:
                        p_limit = cl_a[i] - (p_entry_mult * at_a[i])
                        p_stop_d = p_stop_mult * at_a[i]; p_tar_d = p_target_mult * at_a[i]
                    limit_order_active = True
    return all_trades

best_comp = -999; best_params = None; best_stats = None
for min_score in [6, 7]:
    for entry_mult in [0.5, 1.0, 1.5]:
        for stop_mult in [2.0, 3.0, 4.0]:
            for target_mult in [1.5, 2.0, 3.0, 4.0]:
                trades = sim(pre_data, min_score, entry_mult, stop_mult, target_mult)
                if len(trades) < 10: continue
                wins = sum(1 for t in trades if t > 0)
                wr = wins / len(trades) * 100
                comp = ((1 + pd.Series(trades)).prod() - 1) * 100
                if wr >= 60 and comp > best_comp:
                    best_comp = comp; best_params = (min_score, entry_mult, stop_mult, target_mult)
                    best_stats = (len(trades), wr)

if best_params:
    print(f"Optimal Setup >= 60% WR:\nSCORE: {best_params[0]}\nENTRY: {best_params[1]}\nSTOP: {best_params[2]}\nTARGET: {best_params[3]}")
    print(f"Trades: {best_stats[0]}\nWR: {best_stats[1]:.1f}%\nReturn: {best_comp:.1f}%")
else:
    print("Could not find configuration with >60% win rate.")

