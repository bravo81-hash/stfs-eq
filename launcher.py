"""
launcher.py — STFS-EQ Web Dashboard Launcher
Double-click to open (via setup.command desktop shortcut).
Starts the Flask server in dashboard.py and opens the browser.
"""

import sys
import time
import socket
import threading
import subprocess
import webbrowser
from pathlib import Path

def is_port_open(port=5001):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

if __name__ == "__main__":
    print("Starting STFS-EQ Web Dashboard...")
    
    if not is_port_open(5001):
        dashboard_script = Path(__file__).parent / "dashboard.py"
        
        def open_browser():
            # Poll until server is up
            for _ in range(20):
                time.sleep(0.5)
                if is_port_open(5001):
                    print("Opening browser...")
                    webbrowser.open("http://127.0.0.1:5001")
                    break
                    
        threading.Thread(target=open_browser, daemon=True).start()
        
        # Run dashboard in this terminal so it stays open for logs
        # This replaces the old Tkinter GUI
        subprocess.run([sys.executable, str(dashboard_script)])
    else:
        print("Dashboard is already running! Opening browser...")
        webbrowser.open("http://127.0.0.1:5001")
