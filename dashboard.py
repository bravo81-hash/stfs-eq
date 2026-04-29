"""
dashboard.py — STFS-EQ Web Dashboard Backend
Replaces the old Tkinter launcher and bare-bones HTTP server.
Serves a modern web UI on localhost:5000.
"""

import atexit
import concurrent.futures
import json
import logging
import subprocess
import threading
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory

import config as C
import manual_portfolio
import order_server
import portfolio_manager

app = Flask(__name__, static_folder="web", static_url_path="")
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)  # Reduce console noise

# ── Tracking the trailing stop daemon ──────────────────────────────────────────
_trailing_stop_process = None

# ── Web UI Routes ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return app.send_static_file("index.html")

@app.route("/output/<path:filename>")
def serve_output(filename):
    """Serve generated battle cards from the output directory."""
    return send_from_directory(C.OUTPUT_DIR, filename)

# ── API: Order Server (delegated to order_server.py) ──────────────────────────

@app.route("/api/status", methods=["GET"])
def api_status():
    result = order_server._executor.submit(order_server._do_status).result(timeout=10)
    return jsonify(result)

@app.route("/api/order", methods=["POST"])
def api_order():
    try:
        data = request.json
        result = order_server._executor.submit(order_server._do_order, data).result(timeout=30)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ── API: Battle Card Generation ───────────────────────────────────────────────

@app.route("/api/generate", methods=["POST"])
def api_generate():
    """Run battle_card.py as a subprocess with the given regime."""
    data = request.json
    regime = data.get("regime", "AUTO")
    
    try:
        # We pass --no-open so it doesn't try to open the browser tab from the script
        cmd = ["python3.11", "battle_card.py", regime, "--no-open"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            # Find the newly generated card
            pattern = f"battle_card_*.html"
            output_dir = Path("output")
            files = sorted(output_dir.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
            if files:
                latest_card = files[0].name
                return jsonify({"ok": True, "output_file": latest_card})
            else:
                return jsonify({"ok": False, "error": "Could not find generated HTML file"}), 500
        else:
            return jsonify({"ok": False, "error": result.stderr}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ── API: Portfolio Manager ────────────────────────────────────────────────────

_portfolio_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
_combos_executor    = concurrent.futures.ThreadPoolExecutor(max_workers=1)

@app.route("/api/portfolio", methods=["GET"])
def api_portfolio():
    future = _portfolio_executor.submit(portfolio_manager.get_portfolio_data)
    try:
        return jsonify(future.result(timeout=60))
    except concurrent.futures.TimeoutError:
        return jsonify({"ok": False, "error": "Portfolio fetch timed out"}), 504

@app.route("/api/manual_combos", methods=["GET"])
def api_manual_combos():
    future = _combos_executor.submit(manual_portfolio.get_combo_data)
    try:
        return jsonify(future.result(timeout=60))
    except concurrent.futures.TimeoutError:
        return jsonify({"ok": False, "error": "Combo fetch timed out"}), 504
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/raw_positions", methods=["GET"])
def api_raw_positions():
    future = _combos_executor.submit(manual_portfolio.get_raw_positions)
    try:
        return jsonify(future.result(timeout=60))
    except concurrent.futures.TimeoutError:
        return jsonify({"ok": False, "error": "Raw positions fetch timed out"}), 504
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/save_combo", methods=["POST"])
def api_save_combo():
    data = request.json
    try:
        manual_portfolio.save_combo(data)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ── API: Trailing Stop Daemon Control ─────────────────────────────────────────

@app.route("/api/daemon/status", methods=["GET"])
def api_daemon_status():
    global _trailing_stop_process
    is_running = _trailing_stop_process is not None and _trailing_stop_process.poll() is None
    return jsonify({"active": is_running})

@app.route("/api/daemon/toggle", methods=["POST"])
def api_daemon_toggle():
    global _trailing_stop_process
    data = request.json
    action = data.get("action") # "start" or "stop"
    
    if action == "start":
        if _trailing_stop_process is None or _trailing_stop_process.poll() is not None:
            # Start the daemon
            _trailing_stop_process = subprocess.Popen(["python3.11", "trailing_stop_manager.py"])
        return jsonify({"ok": True, "active": True})
    elif action == "stop":
        if _trailing_stop_process is not None:
            _trailing_stop_process.terminate()
            _trailing_stop_process.wait(timeout=5)
            _trailing_stop_process = None
        return jsonify({"ok": True, "active": False})
    
    return jsonify({"ok": False, "error": "Invalid action"}), 400

# ── API: Account Settings ─────────────────────────────────────────────────────

import importlib
import re

@app.route("/api/accounts", methods=["GET"])
def get_accounts():
    importlib.reload(C)
    return jsonify({"ok": True, "accounts": C.ACCOUNTS})

@app.route("/api/accounts", methods=["POST"])
def update_accounts():
    data = request.json
    accounts = data.get("accounts", [])
    
    cfg_path = Path("config.py")
    if not cfg_path.exists():
        return jsonify({"ok": False, "error": "config.py not found"}), 500
        
    rows = []
    for acc in accounts:
        try:
            name = acc["name"]
            eq = float(acc["equity"])
            rp = float(acc["risk_pct"])
            mn = float(acc["max_notional_pct"])
            rows.append(f'    {{"name": "{name}", "equity": {eq:.0f}, "risk_pct": {rp}, "max_notional_pct": {mn}}}')
        except Exception as e:
            return jsonify({"ok": False, "error": f"Invalid data for {acc.get('name')}"}), 400
        
    new_block = "ACCOUNTS = [\n" + ",\n".join(rows) + ",\n]"
    
    text = cfg_path.read_text()
    new_text = re.sub(r"^ACCOUNTS\s*=\s*\[.*?^\]", new_block, text, flags=re.MULTILINE | re.DOTALL)
    
    cfg_path.write_text(new_text)
    importlib.reload(C)
    return jsonify({"ok": True, "message": "Accounts updated successfully"})

# ── API: Tools & Scripts ──────────────────────────────────────────────────────

@app.route("/api/run_script", methods=["POST"])
def api_run_script():
    data = request.json
    script_name = data.get("script")
    args = data.get("args", [])
    
    allowed_scripts = ["backtest.py", "log_outcome.py", "analyze_journal.py"]
    if script_name not in allowed_scripts:
        return jsonify({"ok": False, "error": "Unauthorized script"}), 403
        
    try:
        cmd = ["python3.11", script_name] + [str(a) for a in args]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return jsonify({"ok": True, "stdout": result.stdout, "stderr": result.stderr, "code": result.returncode})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ── Main ──────────────────────────────────────────────────────────────────────

def _stop_daemon():
    global _trailing_stop_process
    if _trailing_stop_process is not None and _trailing_stop_process.poll() is None:
        _trailing_stop_process.terminate()
        try:
            _trailing_stop_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _trailing_stop_process.kill()
        _trailing_stop_process = None

atexit.register(_stop_daemon)

if __name__ == "__main__":
    # Ensure TWS connects first for order server
    try:
        order_server._executor.submit(order_server._connect_ib).result(timeout=10)
    except Exception as e:
        print(f"Warning: TWS connection failed on startup: {e}")

    # Auto-start trailing stop daemon
    _trailing_stop_process = subprocess.Popen(["python3.11", "trailing_stop_manager.py"])
    print("  ✓ Trailing stop daemon started")

    print("=====================================================")
    print(" 🚀 STFS-EQ Web Dashboard is live!")
    print(" 🌐 http://127.0.0.1:5001")
    print("=====================================================")

    app.run(host="127.0.0.1", port=5001, debug=False)
