"""
log_outcome.py — Record a trade exit against a journal entry.

USAGE:
    python3 log_outcome.py <TICKER> <EXIT_DATE> <EXIT_PRICE> [options]

EXAMPLES:
    # Closed ROKU shares at $121.40 on 2026-05-15
    python3 log_outcome.py ROKU 2026-05-15 121.40

    # Hit stop-loss on MSFT options
    python3 log_outcome.py MSFT 2026-05-20 4.20 --result stop

    # Skipped (didn't take the trade, want to track for signal accuracy)
    python3 log_outcome.py NVDA 2026-05-01 0 --result skipped

    # List open (un-exited) journal entries
    python3 log_outcome.py --list

    # Dry run — shows what would be matched without writing
    python3 log_outcome.py ROKU 2026-05-15 121.40 --dry-run

Matches the MOST RECENT open entry for the given ticker unless --entry-date
is specified. Appends a "close" event to the journal.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import config as C

JOURNAL = Path(__file__).parent / C.OUTPUT_DIR / "trade_journal.jsonl"


# ─── journal helpers ────────────────────────────────────────────────────────

def _load_entries() -> list[dict]:
    if not JOURNAL.exists():
        return []
    lines = []
    with JOURNAL.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    lines.append(json.loads(line))
                except Exception:
                    pass
    return lines


def _write_line(entry: dict) -> None:
    JOURNAL.parent.mkdir(exist_ok=True)
    with JOURNAL.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ─── matching ───────────────────────────────────────────────────────────────

def _open_entries(entries: list[dict]) -> list[dict]:
    """Return entries that have no matching close event."""
    closed_keys: set[str] = set()
    for e in entries:
        if e.get("event") == "close":
            ref = e.get("entry_ts", "")
            closed_keys.add(ref)
    return [
        e for e in entries
        if e.get("event") == "entry" and e.get("ts", "") not in closed_keys
    ]


def _find_match(open_entries: list[dict], ticker: str,
                entry_date: str | None) -> dict | None:
    """Find the best matching open entry for ticker + optional date."""
    candidates = [e for e in open_entries
                  if e.get("ticker", "").upper() == ticker.upper()]
    if not candidates:
        return None
    if entry_date:
        exact = [e for e in candidates if e.get("ts", "").startswith(entry_date)]
        if exact:
            return exact[-1]
        print(f"  ⚠  No entry on {entry_date}. Nearest candidates:")
        for e in candidates[-5:]:
            print(f"     {e['ts']}  {e['ticker']}  {e.get('account','?')}")
        return None
    return candidates[-1]   # most recent


# ─── realized R calculation ─────────────────────────────────────────────────

def _realized_r(entry: dict, exit_price: float, result: str) -> float | None:
    """
    Calculate realized R from the entry's stored entry/stop/target prices.
    R = (exit - entry_px) / (entry_px - stop_px)
    Returns None when prices can't be determined.
    """
    order = entry.get("order", {})
    entry_px = order.get("entry") or order.get("limit_price")
    stop_px  = order.get("stop")

    if not entry_px or not stop_px:
        return None
    try:
        entry_px = float(entry_px)
        stop_px  = float(stop_px)
        risk = entry_px - stop_px
        if risk <= 0 or exit_price <= 0:
            return None
        return round((exit_price - entry_px) / risk, 3)
    except Exception:
        return None


def _hit_result(exit_price: float, entry: dict, forced: str | None) -> str:
    """Auto-classify result as target/stop/partial unless forced by user."""
    if forced:
        return forced
    order = entry.get("order", {})
    target = order.get("target")
    stop   = order.get("stop")
    if target and exit_price >= float(target) * 0.97:
        return "target"
    if stop and exit_price <= float(stop) * 1.03:
        return "stop"
    return "partial"


# ─── list view ──────────────────────────────────────────────────────────────

def _cmd_list():
    entries = _load_entries()
    opens   = _open_entries(entries)
    if not opens:
        print("No open journal entries.")
        return
    print(f"{'DATE':20}  {'TICKER':6}  {'ACCT':8}  {'TYPE':10}  {'ENTRY':>8}  {'STOP':>8}  {'TARGET':>8}")
    print("-" * 80)
    for e in opens:
        o = e.get("order", {})
        sig = e.get("signal", {})
        print(
            f"{e.get('ts','?'):20}  "
            f"{e.get('ticker','?'):6}  "
            f"{e.get('account','?'):8}  "
            f"{o.get('type','?'):10}  "
            f"${o.get('entry') or o.get('limit_price') or '?':>7}  "
            f"${o.get('stop','?'):>7}  "
            f"${o.get('target','?'):>7}"
        )


# ─── main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Record a trade exit outcome in the STFS-EQ trade journal."
    )
    parser.add_argument("ticker",       nargs="?", help="Ticker symbol (e.g. ROKU)")
    parser.add_argument("exit_date",    nargs="?", help="Exit date ISO (e.g. 2026-05-15)")
    parser.add_argument("exit_price",   nargs="?", type=float, help="Exit price (0 for skipped)")
    parser.add_argument("--result",     choices=["target", "stop", "partial", "skipped"],
                        help="Override result classification")
    parser.add_argument("--entry-date", help="Match specific entry date (YYYY-MM-DD)")
    parser.add_argument("--notes",      default="", help="Optional freeform notes")
    parser.add_argument("--list",       action="store_true", help="List open entries and exit")
    parser.add_argument("--dry-run",    action="store_true", help="Show match without writing")

    args = parser.parse_args()

    if args.list:
        _cmd_list()
        return

    if not args.ticker or not args.exit_date or args.exit_price is None:
        parser.print_help()
        sys.exit(1)

    ticker     = args.ticker.upper()
    exit_date  = args.exit_date
    exit_price = args.exit_price
    result_override = args.result

    # Validate date
    try:
        date.fromisoformat(exit_date)
    except ValueError:
        print(f"✗ Invalid exit date: {exit_date}. Use YYYY-MM-DD format.")
        sys.exit(1)

    entries     = _load_entries()
    open_entries = _open_entries(entries)
    match = _find_match(open_entries, ticker, args.entry_date)

    if not match:
        print(f"✗ No open journal entry found for {ticker}.")
        print("  Use --list to see open entries, or --entry-date YYYY-MM-DD to specify one.")
        sys.exit(1)

    # Determine result
    result = _hit_result(exit_price, match, result_override) if exit_price > 0 else "skipped"
    r_val  = _realized_r(match, exit_price, result) if exit_price > 0 else None

    # Preview
    order = match.get("order", {})
    print()
    print(f"  Matched entry:  {match['ts']}  {match.get('ticker')}  {match.get('account')}")
    print(f"  Type:           {order.get('type','?')} / {order.get('structure', order.get('type',''))}")
    if order.get("entry"):
        print(f"  Entry price:    ${order['entry']:.2f}  Stop: ${order.get('stop','?')}  Target: ${order.get('target','?')}")
    print(f"  Exit:           {exit_date}  @${exit_price:.2f}  → {result.upper()}")
    if r_val is not None:
        color = "\033[32m" if r_val > 0 else "\033[31m"
        print(f"  Realized R:     {color}{r_val:+.2f}R\033[0m")
    print()

    if args.dry_run:
        print("  [dry-run] Nothing written.")
        return

    # Write close event
    close_entry = {
        "ts":         datetime.now().isoformat(timespec="seconds"),
        "event":      "close",
        "ticker":     ticker,
        "account":    match.get("account"),
        "entry_ts":   match.get("ts"),           # links back to the entry
        "exit_date":  exit_date,
        "exit_price": exit_price,
        "result":     result,
        "realized_R": r_val,
        "notes":      args.notes,
        # Carry forward signal context for analysis
        "signal":     match.get("signal", {}),
        "order":      match.get("order", {}),
    }

    _write_line(close_entry)
    print(f"  ✓ Outcome recorded in {JOURNAL}")


if __name__ == "__main__":
    main()
