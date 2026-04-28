"""
test_tws.py — TWS connection diagnostics for STFS-EQ

Run as pytest:   pytest test_tws.py -v
Run standalone:  python3.11 test_tws.py
"""

import asyncio
import socket
import sys

import pytest
import pytest_asyncio

from ib_insync import IB, Stock, util

# ---------------------------------------------------------------------------
# Connection constants
# ---------------------------------------------------------------------------
HOST = "127.0.0.1"
PORT = 7496
CLIENT_ID = 16
TIMEOUT = 20          # seconds — TWS on macOS can be slow on first handshake


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures  (async — plays nicely with pytest-asyncio's event loop)
# ═══════════════════════════════════════════════════════════════════════════

@pytest_asyncio.fixture(scope="module")
async def ib():
    """Connect to TWS once for the whole test module, disconnect at teardown."""
    _ib = IB()
    await _ib.connectAsync(HOST, PORT, clientId=CLIENT_ID, timeout=TIMEOUT, readonly=True)
    yield _ib
    _ib.disconnect()


# ═══════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════

def test_tcp_port_open():
    """[1] Raw TCP probe — TWS is listening on the expected port."""
    s = socket.create_connection((HOST, PORT), timeout=5)
    s.close()


@pytest.mark.asyncio
async def test_ib_connect(ib):
    """[2] IB.connect() succeeds and we are connected."""
    assert ib.isConnected(), "IB.connect() returned but isConnected() is False"


@pytest.mark.asyncio
async def test_server_version(ib):
    """[3] Server version is sane (>= 100 for any modern TWS)."""
    ver = ib.client.serverVersion()
    assert ver >= 100, f"Unexpected server version: {ver}"


@pytest.mark.asyncio
async def test_positions(ib):
    """[4] reqPositions completes without error."""
    positions = ib.positions()
    # positions may legitimately be empty — just make sure the call works
    assert isinstance(positions, list)


@pytest.mark.asyncio
async def test_historical_data(ib):
    """[5] reqHistoricalData returns bars for SPY."""
    # Capture errors to see why it times out
    errors = []
    def onError(reqId, errorCode, errorString, contract):
        errors.append(f"Error {errorCode}: {errorString}")
    
    ib.errorEvent += onError
    try:
        spy = Stock("SPY", "SMART", "USD")
        
        # Qualify the contract first (ensures conId is filled, which helps with SMART routing)
        await ib.qualifyContractsAsync(spy)
        
        try:
            bars = await ib.reqHistoricalDataAsync(
                spy,
                endDateTime="",
                durationStr="5 D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=False,
                formatDate=1,
                keepUpToDate=False,
            )
        except asyncio.TimeoutError:
            bars = []
            print(f"\nRequest timed out.")
            
        if not bars:
            print(f"\nCaught TWS Errors during wait: {errors}")
        assert bars, "No historical bars returned — check market-data subscriptions"
    finally:
        ib.errorEvent -= onError


# ═══════════════════════════════════════════════════════════════════════════
# Standalone mode  (python3.11 test_tws.py)
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()

    print(f"\n{'='*55}")
    print(f"  STFS-EQ TWS Diagnostics")
    print(f"{'='*55}\n")

    # Step 1: raw TCP
    print(f"[1] TCP probe {HOST}:{PORT} ...")
    try:
        s = socket.create_connection((HOST, PORT), timeout=5)
        s.close()
        print(f"    ✓ Port {PORT} is open — TWS is listening\n")
    except OSError as e:
        print(f"    ✗ Cannot reach port {PORT}: {e}")
        print(f"\n    Fix: make sure TWS is running and API is enabled.")
        print(f"    TWS → Edit → Global Configuration → API → Settings")
        print(f"    ☑ Enable ActiveX and Socket Clients")
        print(f"    Socket port = 7496\n")
        sys.exit(1)

    # Step 2: connect
    print(f"[2] IB.connect(clientId={CLIENT_ID}, readonly=True, timeout={TIMEOUT}) ...")
    ib_conn = IB()
    try:
        ib_conn.connect(HOST, PORT, clientId=CLIENT_ID, timeout=TIMEOUT, readonly=True)
        print(f"    ✓ Connected!\n")
    except Exception as e:
        print(f"    ✗ Connection failed: {type(e).__name__}: {e}")
        print()
        if "clientId" in str(e) or "already" in str(e).lower():
            print(f"    Fix: clientId {CLIENT_ID} may be in use by another API client.")
            print(f"    Change CLIENT_ID to a different number (e.g. 16).")
        elif "handshake" in str(e).lower() or "version" in str(e).lower():
            print(f"    Fix: TWS API version mismatch — update ib_insync or TWS.")
        else:
            print(f"    Check TWS → API → Settings:")
            print(f"    ☑  Enable ActiveX and Socket Clients")
            print(f"    ☑  Allow connections from localhost only  (or uncheck if needed)")
            print(f"    ☐  Read-Only API  (leave unchecked — tws_data.py uses readonly=True)")
        sys.exit(1)

    # Step 3: server version
    print("[3] Checking server version ...")
    print(f"    Server version: {ib_conn.client.serverVersion()}\n")

    # Step 4: positions
    print("[4] reqPositions ...")
    try:
        positions = ib_conn.positions()
        if positions:
            print(f"    ✓ {len(positions)} open position(s):")
            for p in positions:
                if p.contract.secType == "STK":
                    print(f"       {p.account}: {p.contract.symbol} {int(p.position)}sh @ ${p.avgCost:.2f}")
        else:
            print(f"    ✓ No open positions (or paper account with no trades)")
    except Exception as e:
        print(f"    ✗ reqPositions failed: {e}")

    # Step 5: historical data
    print()
    print("[5] reqHistoricalData (SPY, 5 days) ...")
    try:
        spy = Stock("SPY", "SMART", "USD")
        bars = ib_conn.reqHistoricalData(spy, endDateTime="", durationStr="5 D",
                                    barSizeSetting="1 day", whatToShow="TRADES",
                                    useRTH=True, formatDate=1, keepUpToDate=False)
        if bars:
            print(f"    ✓ {len(bars)} bars — last close: ${bars[-1].close:.2f}")
        else:
            print(f"    ✗ No bars returned — check market data subscriptions")
    except Exception as e:
        print(f"    ✗ Historical data failed: {e}")

    print()
    ib_conn.disconnect()
    print("  All checks done. TWS is ready for STFS-EQ.\n")
