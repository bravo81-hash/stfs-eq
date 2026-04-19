"""
test_finnhub.py — Verify which Finnhub endpoints are accessible on your tier.

Run this ONCE before we build the Battle Card Generator. It will tell us:
  1. Which endpoints work on your current plan
  2. Whether we can rely on Finnhub for historical OHLC or need yfinance

USAGE:
  1. Set env variable first:
     macOS/Linux:  export FINNHUB_API_KEY="your_new_key"
     Windows PS:   $env:FINNHUB_API_KEY="your_new_key"

  2. Install requests if you don't have it:
     pip install requests

  3. Run:
     python test_finnhub.py
"""

import os
import sys
import time
from datetime import datetime, timedelta

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
API_KEY = os.environ.get("FINNHUB_API_KEY")
if not API_KEY:
    print("ERROR: FINNHUB_API_KEY environment variable not set.")
    print("  macOS/Linux: export FINNHUB_API_KEY=\"your_key\"")
    print("  Windows PS:  $env:FINNHUB_API_KEY=\"your_key\"")
    sys.exit(1)

BASE = "https://finnhub.io/api/v1"
TEST_SYMBOL = "AAPL"     # liquid US mega-cap — safest test case
TEST_ETF = "XLK"         # sector ETF — also in our universe


def banner(title):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def test_endpoint(name, url, params, expected_keys=None, critical=False):
    """Call an endpoint and report status."""
    params = {**params, "token": API_KEY}
    print(f"\n[{name}]")
    print(f"  URL: {url}")

    try:
        r = requests.get(url, params=params, timeout=10)
    except requests.RequestException as e:
        print(f"  ✗ NETWORK ERROR: {e}")
        return False

    print(f"  Status: {r.status_code}")

    if r.status_code == 200:
        data = r.json()
        # Finnhub often returns 200 with an error field
        if isinstance(data, dict) and data.get("error"):
            print(f"  ✗ API ERROR: {data['error']}")
            return False
        if expected_keys:
            missing = [k for k in expected_keys if k not in (data if isinstance(data, dict) else {})]
            if missing:
                print(f"  ⚠  Missing expected keys: {missing}")
        # Print a snippet of what we got
        if isinstance(data, dict):
            preview = {k: (str(v)[:80] + "..." if len(str(v)) > 80 else v)
                       for k, v in list(data.items())[:5]}
            print(f"  ✓ OK — sample: {preview}")
        elif isinstance(data, list):
            print(f"  ✓ OK — list of {len(data)} items")
            if data:
                print(f"    sample[0]: {data[0]}")
        return True

    if r.status_code == 401:
        print(f"  ✗ UNAUTHORIZED (bad key or endpoint requires premium)")
    elif r.status_code == 403:
        print(f"  ✗ FORBIDDEN (premium endpoint)")
    elif r.status_code == 429:
        print(f"  ✗ RATE LIMITED (60/min free tier — slow down)")
    else:
        print(f"  ✗ UNEXPECTED: {r.text[:200]}")
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
results = {}

banner("TEST 1 — Quote (real-time snapshot)")
results["quote"] = test_endpoint(
    "Quote",
    f"{BASE}/quote",
    {"symbol": TEST_SYMBOL},
    expected_keys=["c", "h", "l", "o", "pc"],
)

time.sleep(1.1)  # respect rate limit

banner("TEST 2 — Company Profile (sector / industry / market cap)")
results["profile"] = test_endpoint(
    "Profile2",
    f"{BASE}/stock/profile2",
    {"symbol": TEST_SYMBOL},
    expected_keys=["name", "finnhubIndustry", "marketCapitalization"],
)

time.sleep(1.1)

banner("TEST 3 — Earnings Calendar (Stage 1 gate)")
# Window: next 10 trading days
frm = datetime.utcnow().date().isoformat()
to  = (datetime.utcnow().date() + timedelta(days=14)).isoformat()
results["earnings_cal"] = test_endpoint(
    "Calendar/Earnings",
    f"{BASE}/calendar/earnings",
    {"from": frm, "to": to},
    expected_keys=["earningsCalendar"],
)

time.sleep(1.1)

banner("TEST 4 — Stock Candle (historical OHLC — THE KEY QUESTION)")
# 90 days back of daily bars
to_ts   = int(datetime.utcnow().timestamp())
from_ts = int((datetime.utcnow() - timedelta(days=90)).timestamp())
results["candle_stock"] = test_endpoint(
    "Stock Candle (AAPL)",
    f"{BASE}/stock/candle",
    {"symbol": TEST_SYMBOL, "resolution": "D", "from": from_ts, "to": to_ts},
    expected_keys=["c", "h", "l", "o", "v", "t", "s"],
)

time.sleep(1.1)

banner("TEST 5 — Stock Candle on ETF")
results["candle_etf"] = test_endpoint(
    "Stock Candle (XLK)",
    f"{BASE}/stock/candle",
    {"symbol": TEST_ETF, "resolution": "D", "from": from_ts, "to": to_ts},
    expected_keys=["c", "h", "l", "o", "v", "t", "s"],
)

time.sleep(1.1)

banner("TEST 6 — Company News (recent headlines)")
news_from = (datetime.utcnow().date() - timedelta(days=7)).isoformat()
news_to   = datetime.utcnow().date().isoformat()
results["news"] = test_endpoint(
    "Company News",
    f"{BASE}/company-news",
    {"symbol": TEST_SYMBOL, "from": news_from, "to": news_to},
)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
banner("SUMMARY — what to tell Claude")
print()
print(f"  {'Endpoint':<25} {'Status':<10} {'Needed for':<40}")
print(f"  {'-'*25} {'-'*10} {'-'*40}")
rows = [
    ("quote",         "Stage 4 real-time verification (optional)"),
    ("profile",       "Stage 4 Gate 4 (sector concentration)"),
    ("earnings_cal",  "Stage 1 earnings-within-5-days gate"),
    ("candle_stock",  "OHLC history (stocks) — if ✓, can drop yfinance"),
    ("candle_etf",    "OHLC history (ETFs) — same"),
    ("news",          "Optional — last-hour news check"),
]
for name, purpose in rows:
    ok = results.get(name, False)
    status = "✓ WORKS" if ok else "✗ BLOCKED"
    print(f"  {name:<25} {status:<10} {purpose:<40}")

print()
print("  ARCHITECTURE DECISION:")
if results.get("candle_stock") and results.get("candle_etf"):
    print("    → Finnhub candle works. We CAN drop yfinance — Finnhub-only is cleaner.")
else:
    print("    → Finnhub candle blocked for free tier. Hybrid (yfinance + Finnhub) it is.")

if not results.get("earnings_cal"):
    print("    ⚠  Earnings calendar blocked — Stage 1 gate will need a fallback.")

if not results.get("profile"):
    print("    ⚠  Profile blocked — Gate 4 sector check needs a fallback.")

print()
print("  Copy this summary block into the chat and we'll proceed.")
