"""
order_server.py — Local HTTP order API for STFS-EQ
Listens on localhost:5001. Called by the battle card HTML via JavaScript fetch().
All orders use transmit=False so they sit as HELD in TWS — no auto-execution.
Right-click the parent order in TWS → Transmit to activate a bracket.

Uses clientId=16 (separate from tws_data.py which uses 15).
"""

import json
import time
import threading
import concurrent.futures
from http.server import HTTPServer, BaseHTTPRequestHandler

from journal import append_entry as _journal_append

PORT       = 5001
TWS_HOST   = "127.0.0.1"
TWS_PORT   = 7496
TWS_CLIENT = 16          # change if this clashes with another API client

# yfinance ticker → TWS symbol (TWS uses spaces, not hyphens)
_TWS_TICKER = {"BRK-B": "BRK B", "BRK-A": "BRK A"}

_ib       = None
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)  # serialises all IB calls


# ── TWS connection (runs inside _executor thread) ──────────────────────────────

def _connect_ib() -> bool:
    global _ib
    try:
        import asyncio
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
            
        from ib_insync import IB
        ib = IB()
        ib.connect(TWS_HOST, TWS_PORT, clientId=TWS_CLIENT, timeout=5, readonly=False)
        _ib = ib
        print(f"  ✓ Order server: TWS connected (clientId={TWS_CLIENT})")
        return True
    except Exception as e:
        print(f"  ℹ  Order server: TWS not available ({type(e).__name__}: {e})")
        _ib = None
        return False


def _ib_ok() -> bool:
    return _ib is not None and _ib.isConnected()


# ── shares bracket (runs inside _executor thread) ─────────────────────────────

def _place_shares(data: dict) -> dict:
    from ib_insync import Stock, LimitOrder, StopOrder, MarketOrder
    import config as C

    ticker  = data["ticker"]
    account = data["account"]
    shares  = int(data["shares"])
    entry   = float(data["entry"])
    stop    = float(data["stop"])
    target  = float(data["target"])
    is_moo  = str(data.get("entry_type", "LMT")).upper() == "MOO"

    if shares < 1:
        return {"ok": False, "error": "Shares must be ≥ 1"}

    ref = f"{C.STFS_ORDER_REF_PREFIX}{int(time.time())}"

    contract = Stock(_TWS_TICKER.get(ticker, ticker), "SMART", "USD")
    _ib.qualifyContracts(contract)

    p_id  = _ib.client.getReqId()
    tp_id = _ib.client.getReqId()
    sl_id = _ib.client.getReqId()

    # Parent entry order
    if is_moo:
        parent = MarketOrder("BUY", shares)
        parent.tif = "OPG"           # Market-on-Open
    else:
        parent = LimitOrder("BUY", shares, round(entry, 2))
        parent.tif = "GTC"           # Good-till-cancelled — survives pre-market setup workflow
    parent.orderId  = p_id
    parent.account  = account
    parent.orderRef = ref
    parent.transmit = False          # HELD — no auto-execution

    # Take-profit leg
    take_profit = LimitOrder("SELL", shares, round(target, 2))
    take_profit.orderId  = tp_id
    take_profit.parentId = p_id
    take_profit.tif      = "GTC"
    take_profit.account  = account
    take_profit.orderRef = ref
    take_profit.transmit = False

    # Stop-loss leg
    stop_loss = StopOrder("SELL", shares, round(stop, 2))
    stop_loss.orderId  = sl_id
    stop_loss.parentId = p_id
    stop_loss.tif      = "GTC"
    stop_loss.account  = account
    stop_loss.orderRef = ref
    stop_loss.transmit = False

    for order in [parent, take_profit, stop_loss]:
        _ib.placeOrder(contract, order)

    atr_at_entry = float((data.get("signal") or {}).get("atr", 0))
    entry_str = "MOO" if is_moo else f"LMT ${entry:.2f} DAY"
    _journal_append(
        event="entry",
        ticker=ticker,
        account=account,
        signal=data.get("signal"),
        order={
            "type": "shares", "shares": shares,
            "entry": entry, "stop": stop, "target": target,
            "entry_type": "MOO" if is_moo else "LMT",
            "order_ids": [p_id, tp_id, sl_id],
            "orderRef":      ref,
            "stop_order_id": sl_id,
            "atr":           atr_at_entry,
            "entry_price":   entry,
        },
    )
    return {
        "ok": True,
        "message": (
            f"Bracket placed (HELD) — {shares} {ticker}  "
            f"Entry {entry_str}  ·  Stop ${stop:.2f}  ·  Target ${target:.2f}  "
            f"·  IDs: {p_id} / {tp_id} / {sl_id}  ·  ref: {ref}"
        ),
        "order_ids": [p_id, tp_id, sl_id],
    }


# ── options order (runs inside _executor thread) ──────────────────────────────

def _place_options(data: dict) -> dict:
    from ib_insync import Option, Contract, ComboLeg, LimitOrder
    import config as C

    ticker       = data["ticker"]
    account      = data["account"]
    contracts    = int(data["contracts"])
    structure    = data["structure"]
    expiry       = data["expiry"].replace("-", "")        # YYYYMMDD
    long_strike  = float(data["long_strike"])
    short_strike = data.get("short_strike")
    limit_price  = round(float(data["limit_price"]), 2)

    if contracts < 1:
        return {"ok": False, "error": "Contracts must be ≥ 1"}
    if limit_price <= 0:
        return {"ok": False, "error": "Limit price must be > 0"}
    if structure != "long_call" and not short_strike:
        return {"ok": False, "error": f"{structure} requires a short_strike"}
    if short_strike:
        short_strike = float(short_strike)

    ref = f"{C.STFS_ORDER_REF_PREFIX}{int(time.time())}"

    # ── Long call — single-leg order ─────────────────────────────────────────
    if structure == "long_call":
        opt = Option(ticker, expiry, long_strike, "C", "SMART")
        _ib.qualifyContracts(opt)
        order = LimitOrder("BUY", contracts, limit_price)
        order.account  = account
        order.tif      = "DAY"
        order.orderRef = ref
        order.transmit = False
        trade = _ib.placeOrder(opt, order)
        _journal_append(
            event="entry", ticker=ticker, account=account,
            signal=data.get("signal"),
            order={
                "type": "options", "structure": "long_call",
                "contracts": contracts, "expiry": data["expiry"],
                "long_strike": long_strike, "limit_price": limit_price,
                "order_ids": [trade.order.orderId],
                "orderRef": ref,
            },
        )
        return {
            "ok": True,
            "message": (
                f"Long call placed (HELD) — {contracts}×  "
                f"{ticker} {long_strike:.0f}C  exp {data['expiry']}  "
                f"@ ${limit_price:.2f}  ·  ID: {trade.order.orderId}  ·  ref: {ref}"
            ),
            "order_ids": [trade.order.orderId],
        }

    # ── Multi-leg combo (BAG) orders ─────────────────────────────────────────
    if structure == "debit_spread":
        # Bull call spread: buy lower C, sell higher C — net debit, action BUY
        legs = [
            (long_strike,  "C", expiry, "BUY"),
            (short_strike, "C", expiry, "SELL"),
        ]
        combo_action = "BUY"

    elif structure == "credit_spread":
        # Bull put spread: sell higher P, buy lower P — net credit, action SELL
        legs = [
            (short_strike, "P", expiry, "SELL"),
            (long_strike,  "P", expiry, "BUY"),
        ]
        combo_action = "SELL"

    elif structure == "diagonal":
        # Call diagonal: sell near C, buy far C — net debit, action BUY
        near_exp = (data.get("expiry_front") or data["expiry"]).replace("-", "")
        legs = [
            (short_strike, "C", near_exp, "SELL"),
            (long_strike,  "C", expiry,   "BUY"),
        ]
        combo_action = "BUY"

    else:
        return {"ok": False, "error": f"Unknown structure: {structure}"}

    # Qualify each leg to get IBKR conId
    opt_contracts = [
        Option(ticker, exp, strike, right, "SMART")
        for strike, right, exp, _ in legs
    ]
    _ib.qualifyContracts(*opt_contracts)

    # Build BAG (combo) contract
    bag = Contract()
    bag.symbol    = ticker
    bag.secType   = "BAG"
    bag.currency  = "USD"
    bag.exchange  = "SMART"
    bag.comboLegs = [
        ComboLeg(conId=opt.conId, ratio=1, action=action, exchange="SMART")
        for opt, (_, _, _, action) in zip(opt_contracts, legs)
    ]

    order = LimitOrder(combo_action, contracts, limit_price)
    order.account  = account
    order.tif      = "DAY"
    order.orderRef = ref
    order.transmit = False
    trade = _ib.placeOrder(bag, order)
    _journal_append(
        event="entry", ticker=ticker, account=account,
        signal=data.get("signal"),
        order={
            "type": "options", "structure": structure,
            "contracts": contracts, "expiry": data["expiry"],
            "expiry_front": data.get("expiry_front"),
            "long_strike": long_strike, "short_strike": short_strike,
            "limit_price": limit_price,
            "order_ids": [trade.order.orderId],
            "orderRef": ref,
        },
    )

    return {
        "ok": True,
        "message": (
            f"{structure.replace('_', ' ').title()} placed (HELD) — "
            f"{contracts}×  {ticker}  @ ${limit_price:.2f} net  "
            f"·  ID: {trade.order.orderId}  ·  ref: {ref}"
        ),
        "order_ids": [trade.order.orderId],
    }


# ── request dispatch (runs inside _executor thread) ───────────────────────────

def _do_status() -> dict:
    if not _ib_ok():
        _connect_ib()
    if _ib_ok():
        try:
            return {"connected": True, "accounts": _ib.managedAccounts()}
        except Exception as e:
            return {"connected": False, "accounts": [], "error": str(e)}
    return {"connected": False, "accounts": []}


def _do_order(data: dict) -> dict:
    if not _ib_ok():
        _connect_ib()
    if not _ib_ok():
        return {"ok": False, "error": "TWS not connected — check order server log"}
    kind = data.get("type")
    if kind == "shares":
        return _place_shares(data)
    if kind == "options":
        return _place_options(data)
    return {"ok": False, "error": f"Unknown order type: {kind!r}"}


# ── HTTP handler ──────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass   # suppress console noise

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data, code: int = 200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/status":
            result = _executor.submit(_do_status).result(timeout=10)
            self._json(result)
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path == "/api/order":
            try:
                length = int(self.headers.get("Content-Length", 0))
                data   = json.loads(self.rfile.read(length))
                result = _executor.submit(_do_order, data).result(timeout=30)
                self._json(result)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
        else:
            self.send_response(404); self.end_headers()


# ── public API ────────────────────────────────────────────────────────────────

_server_instance = None


def start(port: int = PORT) -> int:
    """
    Connect to TWS (clientId=16, readonly=False) then start the HTTP server.
    Blocks up to 10 s for the TWS handshake; HTTP server runs as a daemon thread.
    Returns the port number.
    """
    global _server_instance
    _executor.submit(_connect_ib).result(timeout=10)
    _server_instance = HTTPServer(("127.0.0.1", port), _Handler)
    t = threading.Thread(target=_server_instance.serve_forever, daemon=True)
    t.start()
    return port


def stop():
    global _server_instance
    if _server_instance:
        _server_instance.shutdown()
        _server_instance = None
