"""
test_tws.py — TWS connection diagnostics for STFS-EQ
Run:  python3.11 test_tws.py
"""

import socket
import sys

HOST = "127.0.0.1"
PORT = 7496
CLIENT_ID = 15

print(f"\n{'='*55}")
print(f"  STFS-EQ TWS Diagnostics")
print(f"{'='*55}\n")

# Step 1: raw TCP
print(f"[1] TCP probe {HOST}:{PORT} ...")
try:
    s = socket.create_connection((HOST, PORT), timeout=3)
    s.close()
    print(f"    ✓ Port {PORT} is open — TWS is listening\n")
except OSError as e:
    print(f"    ✗ Cannot reach port {PORT}: {e}")
    print(f"\n    Fix: make sure TWS is running and API is enabled.")
    print(f"    TWS → Edit → Global Configuration → API → Settings")
    print(f"    ☑ Enable ActiveX and Socket Clients")
    print(f"    Socket port = 7496\n")
    sys.exit(1)

# Step 2: ib_insync import
print("[2] ib_insync import ...")
try:
    from ib_insync import IB, util
    import ib_insync
    print(f"    ✓ ib_insync {ib_insync.__version__} installed\n")
except ImportError as e:
    print(f"    ✗ ib_insync not installed: {e}")
    print(f"\n    Fix:  pip3 install --user ib_insync\n")
    sys.exit(1)

# Step 3: IB connect
print(f"[3] IB.connect(clientId={CLIENT_ID}, readonly=True, timeout=5) ...")
ib = IB()
try:
    ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=5, readonly=True)
    print(f"    ✓ Connected!\n")
except Exception as e:
    print(f"    ✗ Connection failed: {type(e).__name__}: {e}")
    print()
    if "clientId" in str(e) or "already" in str(e).lower():
        print(f"    Fix: clientId {CLIENT_ID} may be in use by another API client.")
        print(f"    Change TWS_CLIENT in tws_data.py to a different number (e.g. 16).")
    elif "handshake" in str(e).lower() or "version" in str(e).lower():
        print(f"    Fix: TWS API version mismatch — update ib_insync or TWS.")
    else:
        print(f"    Check TWS → API → Settings:")
        print(f"    ☑  Enable ActiveX and Socket Clients")
        print(f"    ☑  Allow connections from localhost only  (or uncheck if needed)")
        print(f"    ☐  Read-Only API  (leave unchecked — tws_data.py uses readonly=True)")
    sys.exit(1)

# Step 4: basic queries
print("[4] Checking server version ...")
print(f"    Server version: {ib.client.serverVersion()}\n")

print("[5] reqPositions ...")
try:
    positions = ib.positions()
    if positions:
        print(f"    ✓ {len(positions)} open position(s):")
        for p in positions:
            if p.contract.secType == "STK":
                print(f"       {p.account}: {p.contract.symbol} {int(p.position)}sh @ ${p.avgCost:.2f}")
    else:
        print(f"    ✓ No open positions (or paper account with no trades)")
except Exception as e:
    print(f"    ✗ reqPositions failed: {e}")

print()
print("[6] reqHistoricalData (SPY, 5 days) ...")
try:
    from ib_insync import Stock
    spy = Stock("SPY", "SMART", "USD")
    bars = ib.reqHistoricalData(spy, endDateTime="", durationStr="5 D",
                                barSizeSetting="1 day", whatToShow="TRADES",
                                useRTH=True, formatDate=1, keepUpToDate=False)
    if bars:
        print(f"    ✓ {len(bars)} bars — last close: ${bars[-1].close:.2f}")
    else:
        print(f"    ✗ No bars returned — check market data subscriptions")
except Exception as e:
    print(f"    ✗ Historical data failed: {e}")

print()
ib.disconnect()
print("  All checks done. TWS is ready for STFS-EQ.\n")
