"""
journal.py — Append-only trade journal (JSONL) for the STFS-EQ
self-improvement loop.

Each successful order placement appends one entry capturing the signal
context that produced the trade (regime, score, quality, factors, IVP, BT
stats) plus the order outcome (order_ids, ticker, structure, account).
A later analysis pass can compare the journal's actual outcomes against the
backtest's expected expectancy_R to detect signal drift.

Path: <repo>/output/trade_journal.jsonl
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import config as C

_JOURNAL_PATH = Path(__file__).parent / C.OUTPUT_DIR / "trade_journal.jsonl"


def append_entry(event: str, ticker: str, account: str,
                 signal: dict | None, order: dict) -> None:
    """Append a single JSONL line. Best-effort — never raises so order placement
    is not blocked by journal I/O issues."""
    try:
        _JOURNAL_PATH.parent.mkdir(exist_ok=True)
        line = {
            "ts":      datetime.now().isoformat(timespec="seconds"),
            "event":   event,           # "entry" for now; "close" later
            "ticker":  ticker,
            "account": account,
            "signal":  signal or {},
            "order":   order,
        }
        with _JOURNAL_PATH.open("a") as f:
            f.write(json.dumps(line, default=str) + "\n")
    except Exception:
        # Never let journaling fail the order. Silent on purpose.
        pass
