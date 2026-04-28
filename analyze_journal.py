"""
analyze_journal.py — Signal quality drift analysis for STFS-EQ.

Reads output/trade_journal.jsonl and produces a plain-text report showing:
  1. Overall realized vs. backtest-predicted performance
  2. Performance by regime
  3. Performance by signal quality tier
  4. Signal reliability by score
  5. Monthly trend (is performance improving or drifting?)
  6. Open positions still needing outcomes

USAGE:
    python3 analyze_journal.py
    python3 analyze_journal.py --min-trades 3      # only show rows with ≥3 trades
    python3 analyze_journal.py --since 2026-01-01  # filter by entry date
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import config as C

JOURNAL = Path(C.JOURNAL_PATH)

# ─── ANSI colours (gracefully disabled if not a tty) ────────────────────────
_IS_TTY = sys.stdout.isatty()

def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _IS_TTY else text

def green(t):  return _c(t, "32")
def amber(t):  return _c(t, "33")
def red(t):    return _c(t, "31")
def cyan(t):   return _c(t, "36")
def bold(t):   return _c(t, "1")
def muted(t):  return _c(t, "2")


# ─── data loading ────────────────────────────────────────────────────────────

def _load() -> tuple[list[dict], list[dict]]:
    """Return (closed_trades, open_entries)."""
    if not JOURNAL.exists():
        return [], []

    raw: list[dict] = []
    with JOURNAL.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    raw.append(json.loads(line))
                except Exception:
                    pass

    entries = {e["ts"]: e for e in raw if e.get("event") == "entry"}
    closes  = [e for e in raw if e.get("event") == "close"]

    closed_trade_tss = {c["entry_ts"] for c in closes}
    open_entries     = [e for ts, e in entries.items() if ts not in closed_trade_tss]

    # Build closed trade records — merge entry + close
    closed_trades = []
    for c in closes:
        entry = entries.get(c.get("entry_ts"), {})
        merged = {
            "entry_ts":     entry.get("ts"),
            "close_ts":     c.get("ts"),
            "ticker":       c.get("ticker") or entry.get("ticker"),
            "account":      c.get("account") or entry.get("account"),
            "exit_date":    c.get("exit_date"),
            "exit_price":   c.get("exit_price"),
            "result":       c.get("result", "unknown"),
            "realized_R":   c.get("realized_R"),
            "notes":        c.get("notes", ""),
            "signal":       c.get("signal") or entry.get("signal") or {},
            "order":        c.get("order") or entry.get("order") or {},
        }
        closed_trades.append(merged)

    return closed_trades, open_entries


# ─── stat helpers ────────────────────────────────────────────────────────────

def _stats(trades: list[dict]) -> dict:
    rs    = [t["realized_R"] for t in trades if t.get("realized_R") is not None]
    n     = len(rs)
    wins  = sum(1 for r in rs if r > 0)
    stops = sum(1 for t in trades if t.get("result") == "stop")
    tgts  = sum(1 for t in trades if t.get("result") == "target")
    wr    = wins / n * 100 if n > 0 else 0.0
    avg_r = sum(rs) / n if n > 0 else 0.0
    return {
        "n": n, "wins": wins, "stops": stops, "targets": tgts,
        "win_rate": wr, "avg_R": avg_r,
        "rs": rs,
    }


def _colour_wr(wr: float) -> str:
    s = f"{wr:.0f}%"
    if wr >= 60:  return green(s)
    if wr >= 40:  return amber(s)
    return red(s)

def _colour_r(r: float) -> str:
    s = f"{r:+.2f}R"
    if r > 0.2:  return green(s)
    if r >= 0:   return amber(s)
    return red(s)


# ─── sections ────────────────────────────────────────────────────────────────

def _section_overall(trades: list[dict]) -> None:
    skipped = [t for t in trades if t.get("result") == "skipped"]
    actual  = [t for t in trades if t.get("result") != "skipped"]
    s = _stats(actual)
    print(bold("━━━  OVERALL REALIZED PERFORMANCE  ━━━"))
    print(f"  Closed trades:  {s['n']}   (+ {len(skipped)} skipped / not taken)")
    if s["n"] == 0:
        print(amber("  No completed trades with outcomes yet."))
        return
    print(f"  Win rate:       {_colour_wr(s['win_rate'])}  ({s['wins']} wins / {s['stops']} stops / {s['targets']} targets)")
    print(f"  Avg realized R: {_colour_r(s['avg_R'])}")

    # Predicted vs. realized
    pred_wrs = [t["signal"].get("bt_mean_winrate") for t in actual
                if t["signal"].get("bt_mean_winrate") is not None]
    pred_rs  = [t["signal"].get("bt_mean_expR") for t in actual
                if t["signal"].get("bt_mean_expR") is not None]
    if pred_wrs:
        pw = sum(pred_wrs) / len(pred_wrs)
        pr = sum(pred_rs) / len(pred_rs) if pred_rs else None
        print()
        print(muted("  Backtest had predicted:"))
        print(f"    Predicted win rate:   {pw:.0f}%   →  actual {_colour_wr(s['win_rate'])}")
        if pr is not None:
            print(f"    Predicted expectancy: {pr:+.2f}R  →  actual {_colour_r(s['avg_R'])}")
        diff = s["win_rate"] - pw
        if abs(diff) >= 5:
            arrow = "↑" if diff > 0 else "↓"
            col   = green if diff > 0 else red
            print(f"    Drift: {col(f'{arrow} {abs(diff):.0f}pp')}")
    print()


def _section_by_group(trades: list[dict], key_fn, title: str,
                      min_trades: int) -> None:
    actual = [t for t in trades if t.get("result") != "skipped"]
    groups: dict[str, list] = defaultdict(list)
    for t in actual:
        k = key_fn(t)
        if k is not None:
            groups[k].append(t)

    if not any(len(v) >= min_trades for v in groups.values()):
        return

    print(bold(f"━━━  BY {title.upper()}  ━━━"))
    rows = sorted(groups.items(), key=lambda kv: -_stats(kv[1])["avg_R"])
    col_w = max(len(str(k)) for k in groups) + 2
    print(f"  {'':>{col_w}}  {'N':>4}  {'WR':>6}  {'AvgR':>7}  {'BT pred WR':>10}  {'BT pred R':>10}")
    print("  " + "─" * (col_w + 48))
    for k, group in rows:
        if len(group) < min_trades:
            continue
        s   = _stats(group)
        pwr = [t["signal"].get("bt_mean_winrate") for t in group
               if t["signal"].get("bt_mean_winrate") is not None]
        pr  = [t["signal"].get("bt_mean_expR") for t in group
               if t["signal"].get("bt_mean_expR") is not None]
        pwr_s = f"{sum(pwr)/len(pwr):.0f}%" if pwr else "—"
        pr_s  = f"{sum(pr)/len(pr):+.2f}R"  if pr  else "—"
        print(f"  {str(k):>{col_w}}  {s['n']:>4}  "
              f"{_colour_wr(s['win_rate']):>15}  {_colour_r(s['avg_R']):>16}  "
              f"{pwr_s:>10}  {pr_s:>10}")
    print()


def _section_monthly(trades: list[dict]) -> None:
    actual = [t for t in trades if t.get("result") != "skipped" and t.get("exit_date")]
    if not actual:
        return
    months: dict[str, list] = defaultdict(list)
    for t in actual:
        try:
            m = t["exit_date"][:7]   # YYYY-MM
            months[m].append(t)
        except Exception:
            pass
    if len(months) < 2:
        return

    print(bold("━━━  MONTHLY TREND  ━━━"))
    print(f"  {'MONTH':>7}  {'N':>4}  {'WR':>6}  {'AvgR':>7}")
    print("  " + "─" * 30)
    for month in sorted(months):
        s = _stats(months[month])
        bar = "█" * max(1, int(s["avg_R"] * 5)) if s["avg_R"] > 0 else red("▼")
        print(f"  {month}  {s['n']:>4}  {_colour_wr(s['win_rate']):>15}  "
              f"{_colour_r(s['avg_R']):>16}  {bar}")
    print()


def _section_open(open_entries: list[dict]) -> None:
    if not open_entries:
        return
    print(bold(f"━━━  OPEN (NO OUTCOME YET)  ━━━"))
    print(f"  {len(open_entries)} open entr{'y' if len(open_entries)==1 else 'ies'} — log with:  python3 log_outcome.py --list")
    for e in open_entries[:10]:
        sig = e.get("signal", {})
        score = sig.get("score", "?")
        regime = sig.get("regime", "?")
        print(f"    {e.get('ts','?')[:10]}  {e.get('ticker','?'):6}  "
              f"{e.get('account','?'):8}  score {score}/8  {regime}")
    if len(open_entries) > 10:
        print(f"    … and {len(open_entries)-10} more.")
    print()


def _section_quality_calibration(trades: list[dict]) -> None:
    """Is the quality score actually predictive?"""
    actual = [t for t in trades
              if t.get("result") != "skipped"
              and t["signal"].get("quality") is not None
              and t.get("realized_R") is not None]
    if len(actual) < 5:
        return

    tiers: dict[str, list] = {"High (≥70%)": [], "Mid (40-69%)": [], "Low (<40%)": []}
    for t in actual:
        q = t["signal"]["quality"]
        if q >= 0.7:   tiers["High (≥70%)"].append(t)
        elif q >= 0.4: tiers["Mid (40-69%)"].append(t)
        else:          tiers["Low (<40%)"].append(t)

    print(bold("━━━  QUALITY SCORE CALIBRATION  ━━━"))
    print("  (Is the Q score actually predicting better outcomes?)")
    for label, group in tiers.items():
        if not group:
            continue
        s = _stats(group)
        print(f"  {label:15}  n={s['n']:>3}  WR {_colour_wr(s['win_rate'])}  AvgR {_colour_r(s['avg_R'])}")
    print()


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="STFS-EQ trade journal analysis")
    parser.add_argument("--min-trades", type=int, default=1,
                        help="Minimum trades per group to show (default 1)")
    parser.add_argument("--since", default=None,
                        help="Filter: only entries on or after this date (YYYY-MM-DD)")
    args = parser.parse_args()

    closed, opens = _load()

    if args.since:
        closed = [t for t in closed
                  if (t.get("exit_date") or "") >= args.since]

    print()
    print(bold(cyan("  ╔══════════════════════════════════════╗")))
    print(bold(cyan("  ║   STFS-EQ  Signal Quality Report    ║")))
    print(bold(cyan(f"  ╚══════════════════════════════════════╝")))
    print(f"  Journal: {JOURNAL}")
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print()

    if not closed and not opens:
        print(amber("  Journal is empty. Start logging outcomes with:"))
        print("    python3 log_outcome.py TICKER YYYY-MM-DD EXIT_PRICE")
        print()
        return

    mt = args.min_trades
    _section_overall(closed)
    _section_by_group(closed, lambda t: t["signal"].get("regime"),
                      "regime", mt)
    _section_by_group(closed, lambda t: str(t["signal"].get("score")),
                      "signal score", mt)
    _section_quality_calibration(closed)
    _section_monthly(closed)
    _section_open(opens)

    skipped = [t for t in closed if t.get("result") == "skipped"]
    if skipped:
        actual = [t for t in closed if t.get("result") != "skipped"]
        print(muted(f"  Note: {len(skipped)} skipped trade(s) excluded from stats above. "
                    f"{len(actual)} with realized outcomes."))
        print()


if __name__ == "__main__":
    main()
