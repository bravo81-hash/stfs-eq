import argparse
import sys
import warnings
import pandas as pd
import numpy as np
from datetime import date, timedelta
import yfinance as yf

warnings.filterwarnings("ignore")

import config as C
from indicators import ema, wilder, wma, hma, rsi, atr, adx_dmi, obv, compute_factors

def run_backtest(tickers, days=1000):
    start = (date.today() - timedelta(days=days)).isoformat()
    
    print(f"Downloading {days} days of historical data...")
    if C.BENCHMARK not in tickers:
        tickers = [C.BENCHMARK] + tickers
        
    try:
        data = yf.download(tickers=tickers, start=start, interval="1d", group_by="ticker", auto_adjust=True, progress=False)
    except Exception as e:
        print(f"Error fetching data: {e}")
        return
        
    if isinstance(data.columns, pd.MultiIndex):
        # Multiple tickers downloaded
        bench_df = data[C.BENCHMARK].dropna()
    else:
        # Only one ticker downloaded and it is the benchmark
        bench_df = data.dropna()
        
    trades = []
    
    for ticker in tickers:
        if ticker == C.BENCHMARK and len(tickers) > 1:
            pass
            
        print(f"\nProcessing {ticker}...")
        if isinstance(data.columns, pd.MultiIndex):
            df = data[ticker].dropna()
        else:
            df = data.dropna()
            
        if len(df) < max(C.EMA_SLOW, C.WEEKLY_EMA_SLOW*5, 50):
            print(f"  Insufficient data for {ticker}")
            continue
            
        # --- Pre-calculate all Indicators ---
        hi, lo, cl, vol = df["High"], df["Low"], df["Close"], df["Volume"]
        op = df["Open"]
        
        ef = ema(cl, C.EMA_FAST)
        em = ema(cl, C.EMA_MID)
        es = ema(cl, C.EMA_SLOW)
        hm = hma(cl, C.HMA_LEN)
        rs = rsi(cl, C.RSI_LEN)
        at = atr(hi, lo, cl, C.ATR_LEN)
        pdi, mdi, adx_s = adx_dmi(hi, lo, cl, C.ADX_LEN)
        ob = obv(cl, vol)
        oe = ema(ob, C.OBV_EMA_LEN)
        
        atr_pct = (at / cl) * 100
        
        # --- Pre-calculate Factors Vectorized ---
        f1 = (ef > em) & (em > es)
        
        # F2: Weekly Trend
        df_weekly = df.resample("W-FRI").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()
        if len(df_weekly) >= C.WEEKLY_EMA_SLOW + 2:
            wf = ema(df_weekly["Close"], C.WEEKLY_EMA_FAST)
            ws = ema(df_weekly["Close"], C.WEEKLY_EMA_SLOW)
            df_weekly["f2"] = (df_weekly["Close"] > ws) & (wf > ws)
            # Reindex aligned to daily
            f2 = df_weekly["f2"].reindex(df.index).ffill().fillna(False)
        else:
            f2 = pd.Series(False, index=df.index)
            
        f3 = hm > hm.shift(1)
        f4 = (adx_s > C.ADX_THRESHOLD) & (adx_s > adx_s.shift(1)) & (adx_s.shift(1) > adx_s.shift(2))
        f5 = (rs >= C.RSI_LOWER_BAND) & (rs <= C.RSI_UPPER_BAND)
        
        # F6: RS vs Benchmark
        tr = cl / cl.shift(C.RS_LOOKBACK) - 1
        br = bench_df["Close"] / bench_df["Close"].shift(C.RS_LOOKBACK) - 1
        br_aligned = br.reindex(df.index).ffill()
        f6 = tr > br_aligned
        
        f7 = (ob > oe) & (ob > ob.shift(C.OBV_SLOPE_LOOKBACK))
        f8 = (atr_pct >= C.ATR_PCT_MIN) & (atr_pct <= C.ATR_PCT_MAX)
        
        score = f1.astype(int) + f2.astype(int) + f3.astype(int) + f4.astype(int) + f5.astype(int) + f6.astype(int) + f7.astype(int) + f8.astype(int)
        trio = f1 & f2 & f8
        
        strong_buy = (score >= C.STRONG_SCORE_MIN) & trio
        is_breakout = (cl >= cl.rolling(C.BREAKOUT_LOOKBACK).max())
        
        # --- Simulate Trades ---
        in_trade = False
        limit_order_active = False 
        
        entry_price = 0.0
        stop_loss = 0.0
        take_profit = 0.0
        trade_entry_date = None
        trade_mae = 0.0 # Maximum Adverse Excursion tracking
        
        pending_limit_price = 0.0
        pending_stop_dist = 0.0
        pending_target_dist = 0.0
        pending_breakout = False
        
        stats = {"wins": 0, "losses": 0, "total_return": 1.0}
        
        for i in range(len(df) - 1):
            date_today = df.index[i]
            date_tmrw = df.index[i+1]
            
            c_today = float(cl.iloc[i])
            a_today = float(at.iloc[i])
            
            o_tmrw = float(op.iloc[i+1])
            h_tmrw = float(hi.iloc[i+1])
            l_tmrw = float(lo.iloc[i+1])
            c_tmrw = float(cl.iloc[i+1])
            
            if in_trade:
                # Track Unrealized Drawdown (MAE) before checking stops
                current_unrealized = (l_tmrw - entry_price) / entry_price
                trade_mae = min(trade_mae, current_unrealized)

                if l_tmrw <= stop_loss:
                    pl = (stop_loss - entry_price) / entry_price
                    trades.append({
                        "ticker": ticker, "entry_date": trade_entry_date, "exit_date": date_tmrw,
                        "type": "STOP", "pnl": pl, "entry": entry_price, "exit": stop_loss, "mae": trade_mae
                    })
                    if pl > 0: stats["wins"] += 1
                    else: stats["losses"] += 1
                    in_trade = False
                elif h_tmrw >= take_profit:
                    pl = (take_profit - entry_price) / entry_price
                    trades.append({
                        "ticker": ticker, "entry_date": trade_entry_date, "exit_date": date_tmrw,
                        "type": "TARGET", "pnl": pl, "entry": entry_price, "exit": take_profit, "mae": trade_mae
                    })
                    if pl > 0: stats["wins"] += 1
                    else: stats["losses"] += 1
                    in_trade = False
                continue
                
            if limit_order_active:
                if pending_breakout:
                    entry_price = o_tmrw
                    stop_loss = entry_price - pending_stop_dist
                    take_profit = entry_price + pending_target_dist
                    limit_order_active = False
                    in_trade = True
                    trade_entry_date = date_tmrw
                    trade_mae = min(0.0, (l_tmrw - entry_price) / entry_price)
                    
                    if l_tmrw <= stop_loss:
                        pl = (stop_loss - entry_price) / entry_price
                        trades.append({"ticker": ticker, "entry_date": trade_entry_date, "exit_date": date_tmrw, "type": "STOP (Same Day)", "pnl": pl, "entry": entry_price, "exit": stop_loss, "mae": trade_mae})
                        stats["losses"] += 1
                        in_trade = False
                    elif h_tmrw >= take_profit:
                        pl = (take_profit - entry_price) / entry_price
                        trades.append({"ticker": ticker, "entry_date": trade_entry_date, "exit_date": date_tmrw, "type": "TARGET (Same Day)", "pnl": pl, "entry": entry_price, "exit": take_profit, "mae": trade_mae})
                        stats["wins"] += 1
                        in_trade = False
                        
                else:
                    if l_tmrw <= pending_limit_price:
                        entry_price = pending_limit_price
                        stop_loss = entry_price - pending_stop_dist
                        take_profit = entry_price + pending_target_dist
                        limit_order_active = False
                        in_trade = True
                        trade_entry_date = date_tmrw
                        trade_mae = min(0.0, (l_tmrw - entry_price) / entry_price)
                        
                        if l_tmrw <= stop_loss:
                            pl = (stop_loss - entry_price) / entry_price
                            trades.append({"ticker": ticker, "entry_date": trade_entry_date, "exit_date": date_tmrw, "type": "STOP (Same Day)", "pnl": pl, "entry": entry_price, "exit": stop_loss, "mae": trade_mae})
                            stats["losses"] += 1
                            in_trade = False
                    else:
                         limit_order_active = False

            if not in_trade and not limit_order_active:
                if strong_buy.iloc[i]:
                    is_brk = is_breakout.iloc[i]
                    pending_breakout = is_brk
                    
                    if is_brk:
                        pending_stop_dist = C.STOP_ATR_MULT * a_today
                        pending_target_dist = C.TARGET_ATR_MULT * a_today
                    else:
                        pending_limit_price = c_today - (C.ENTRY_ATR_MULT * a_today)
                        pending_stop_dist = C.STOP_ATR_MULT * a_today
                        pending_target_dist = C.TARGET_ATR_MULT * a_today
                    
                    limit_order_active = True
                    
        res = f"  Trades: {stats['wins']+stats['losses']}  Wins: {stats['wins']}  Losses: {stats['losses']}"
        if stats['wins']+stats['losses'] > 0:
            win_rate = stats['wins'] / float(stats['wins']+stats['losses']) * 100
            res += f"  WinRate: {win_rate:.1f}%"
        print(res)
        
    print("\n==============================")
    print("      BACKTEST SUMMARY")
    print("==============================")
    if not trades:
        print("No trades triggered over this period.")
        return
        
    trades_df = pd.DataFrame(trades)
    
    # Calculate Equity Curve and Drawdown
    trades_df['cumulative_return'] = (1 + trades_df['pnl']).cumprod()
    trades_df['peak'] = trades_df['cumulative_return'].cummax()
    trades_df['drawdown'] = (trades_df['cumulative_return'] - trades_df['peak']) / trades_df['peak']
    
    wins = len(trades_df[trades_df["pnl"] > 0])
    losses = len(trades_df[trades_df["pnl"] < 0])
    total = wins + losses
    win_rate = (wins / total) * 100
    avg_win = trades_df[trades_df["pnl"] > 0]["pnl"].mean() * 100
    avg_loss = trades_df[trades_df["pnl"] < 0]["pnl"].mean() * 100
    
    max_drawdown = trades_df['drawdown'].min() * 100
    avg_mae = trades_df['mae'].mean() * 100
    compounded_return = (trades_df['cumulative_return'].iloc[-1] - 1) * 100
    
    print(f"Total Trades : {total}")
    print(f"Win Rate     : {win_rate:.1f}%")
    print(f"Avg Win      : {avg_win:.1f}% (unleveraged)")
    print(f"Avg Loss     : {avg_loss:.1f}% (unleveraged)")
    print(f"Net Return   : {compounded_return:.1f}% (cumulative uncompounded sum: {trades_df['pnl'].sum()*100:.1f}%)")
    print(f"Max Drawdown : {max_drawdown:.1f}%")
    print(f"Average MAE  : {avg_mae:.1f}% (Intra-trade adverse excursion)")
    
    print("\n------------------------------")
    print("  PROJECTED OPTIONS PROFILE   ")
    print("  (illustrative upper bound)  ")
    print("------------------------------")
    opt_wins = wins * 1.5
    opt_loss = losses * -1.0
    opt_net_r = opt_wins + opt_loss
    wr_break_even = 1.0 / (1.0 + 1.5) * 100
    print(f"Assumed win payout : +150% of debit (best case, target hit)")
    print(f"Assumed max loss   : -100% of debit (stop hit)")
    print(f"Break-even win rate: {wr_break_even:.0f}% (actual: {win_rate:.1f}%)")
    if opt_net_r > 0:
        print(f"Illustrative yield : +{opt_net_r:.1f} R-units  ⚠  upper bound only")
    else:
        print(f"Illustrative yield : {opt_net_r:.1f} R-units")
    
if __name__ == "__main__":
    print("====================================")
    print("      STFS-EQ Backtest Engine       ")
    print("====================================")
    
    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(description="STFS-EQ Backtester")
        parser.add_argument("tickers", nargs="+", help="Tickers to backtest (e.g. QQQ SPY AAPL)")
        parser.add_argument("--days", type=int, default=1500, help="Number of historical days to fetch (default: 1500)")
        args = parser.parse_args()
        
        tickers = [t.upper() for t in args.tickers]
        days = args.days
    else:
        user_input = input("\nEnter ticker(s) separated by space (e.g. AAPL MSFT): ")
        tickers = [t.strip().upper() for t in user_input.split() if t.strip()]
        if not tickers:
            print("No tickers entered. Defaulting to QQQ, SPY.")
            tickers = ["QQQ", "SPY"]
            
        days_input = input("Enter lookback horizon in days [press Enter for 1500]: ")
        try:
            days = int(days_input.strip())
        except ValueError:
            days = 1500

    print(f"\n[Target Tickers: {', '.join(tickers)}]")
    print(f"[Lookback: {days} calendar days]")
    
    run_backtest(tickers, days)