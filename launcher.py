"""
launcher.py — STFS-EQ Battle Card Generator GUI
Double-click to open (after running setup.command once).
No terminal needed for daily use.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont

# ── paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
BATTLE_CARD = SCRIPT_DIR / "battle_card.py"
OUTPUT_DIR  = SCRIPT_DIR / "output"
KEY_FILE    = SCRIPT_DIR / ".api_key"   # stores API key locally

# ── colours (match TCR design system) ──────────────────────────────────────
BG        = "#080c12"
BG1       = "#0e1420"
BG2       = "#141c2e"
BG3       = "#1b2540"
BORDER    = "#232f4a"
TEXT      = "#d4dff5"
MUTED     = "#5a7090"
GREEN     = "#00e5a0"
RED       = "#ff4560"
AMBER     = "#ffb020"
BLUE      = "#4090ff"
PURPLE    = "#a060ff"
CYAN      = "#00d4ff"

REGIME_CONFIG = {
    "GOLDILOCKS": {"color": GREEN,  "label": "GOLDILOCKS",  "desc": "Low vol · Growth / Tech"},
    "LIQUIDITY":  {"color": PURPLE, "label": "LIQUIDITY",   "desc": "USD weak · High-beta"},
    "REFLATION":  {"color": AMBER,  "label": "REFLATION",   "desc": "Rates up · Cyclicals"},
    "NEUTRAL":    {"color": BLUE,   "label": "NEUTRAL",     "desc": "No regime · Quality"},
    "RISK_OFF":   {"color": RED,    "label": "RISK OFF",    "desc": "Stress · Defensives"},
    "CRASH":      {"color": RED,    "label": "CRASH",       "desc": "Cash only · No trades"},
}

# ── helper ─────────────────────────────────────────────────────────────────
def load_api_key():
    if KEY_FILE.exists():
        return KEY_FILE.read_text().strip()
    return os.environ.get("FINNHUB_API_KEY", "")

def save_api_key(key):
    KEY_FILE.write_text(key.strip())

def find_latest_card(regime=None):
    if not OUTPUT_DIR.exists(): return None
    pattern = f"battle_card_{regime}_*.html" if regime else "battle_card_*.html"
    files = sorted(OUTPUT_DIR.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
    return files[0] if files else None

def find_python():
    """Find the best available Python 3 with yfinance installed."""
    candidates = [sys.executable, "python3.11", "python3.12", "python3.10", "python3"]
    for py in candidates:
        try:
            result = subprocess.run(
                [py, "-c", "import yfinance, pandas, numpy, requests; print('ok')"],
                capture_output=True, text=True, timeout=8
            )
            if result.stdout.strip() == "ok":
                return py
        except Exception:
            pass
    return None


# ── main application ───────────────────────────────────────────────────────
class STFSApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("⚡ STFS-EQ  Battle Card Generator")
        self.configure(bg=BG)
        self.resizable(False, False)

        # State
        self.selected_regime = tk.StringVar(value="")
        self.api_key_var     = tk.StringVar(value=load_api_key())
        self.show_key        = False
        self.running         = False
        self.python_exe      = None

        self._build_ui()
        self._check_python()
        self.after(100, self._center_window)
        self.after(500, self._check_tws)   # first TWS probe shortly after startup
        threading.Thread(target=self._start_order_server, daemon=True).start()

    def _center_window(self):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"+{(sw-w)//2}+{(sh-h)//2}")

    # ── UI construction ─────────────────────────────────────────────────────
    def _build_ui(self):
        pad = {"padx": 16, "pady": 8}

        # ── header ──────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=BG1, bd=0)
        hdr.pack(fill="x", padx=0, pady=0)
        tk.Frame(hdr, bg=CYAN, height=3).pack(fill="x")
        inner_hdr = tk.Frame(hdr, bg=BG1)
        inner_hdr.pack(fill="x", padx=16, pady=12)
        tk.Label(inner_hdr, text="⚡ STFS-EQ", bg=BG1, fg=CYAN,
                 font=("Helvetica Neue", 20, "bold")).pack(side="left")
        tk.Label(inner_hdr, text="Battle Card Generator  v2.0",
                 bg=BG1, fg=MUTED, font=("Helvetica Neue", 12)).pack(side="left", padx=10)

        # TWS status indicator — polled every 30 s via socket check on port 7496
        self.tws_lbl = tk.Label(inner_hdr, text="TWS: checking…",
                                 bg=BG1, fg=MUTED, font=("Courier", 10))
        self.tws_lbl.pack(side="right")
        self.tws_dot = tk.Label(inner_hdr, text="●", bg=BG1, fg=MUTED,
                                 font=("Courier", 13))
        self.tws_dot.pack(side="right", padx=(0, 4))

        # ── API key ──────────────────────────────────────────────────────────
        key_frame = tk.Frame(self, bg=BG, bd=0)
        key_frame.pack(fill="x", padx=16, pady=(12, 4))
        tk.Label(key_frame, text="Finnhub API Key", bg=BG, fg=MUTED,
                 font=("Courier", 10)).pack(anchor="w")
        key_row = tk.Frame(key_frame, bg=BG)
        key_row.pack(fill="x", pady=4)
        self.key_entry = tk.Entry(key_row, textvariable=self.api_key_var,
                                  show="•", width=46,
                                  bg=BG2, fg=TEXT, insertbackground=TEXT,
                                  bd=0, highlightthickness=1,
                                  highlightcolor=BORDER, highlightbackground=BORDER,
                                  font=("Courier", 12), relief="flat")
        self.key_entry.pack(side="left", ipady=6, padx=(0, 8))
        self.show_btn = tk.Button(key_row, text="show",
                                  command=self._toggle_key,
                                  bg=BG2, fg=MUTED, bd=0, cursor="hand2",
                                  activebackground=BG3, activeforeground=TEXT,
                                  font=("Courier", 10), padx=8, pady=4)
        self.show_btn.pack(side="left")
        tk.Button(key_row, text="save",
                  command=self._save_key_clicked,
                  bg=BG2, fg=GREEN, bd=0, cursor="hand2",
                  activebackground=BG3, activeforeground=GREEN,
                  font=("Courier", 10), padx=8, pady=4).pack(side="left", padx=4)

        # ── divider ──────────────────────────────────────────────────────────
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=8)

        # ── regime selector ──────────────────────────────────────────────────
        tk.Label(self, text="SELECT REGIME", bg=BG, fg=MUTED,
                 font=("Courier", 10)).pack(anchor="w", padx=16)

        self.regime_buttons = {}
        reg_frame = tk.Frame(self, bg=BG)
        reg_frame.pack(fill="x", padx=16, pady=6)

        for i, (key, cfg) in enumerate(REGIME_CONFIG.items()):
            col = i % 3
            row = i // 3
            btn = tk.Button(
                reg_frame,
                text=f"{cfg['label']}\n{cfg['desc']}",
                command=lambda k=key: self._select_regime(k),
                bg=BG2, fg=MUTED, bd=0, cursor="hand2",
                activebackground=BG3,
                font=("Courier", 10, "bold"),
                width=18, height=3,
                wraplength=140,
                relief="flat", padx=4, pady=4
            )
            btn.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")
            self.regime_buttons[key] = btn
            reg_frame.columnconfigure(col, weight=1)

        # ── divider ──────────────────────────────────────────────────────────
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=8)

        # ── individual backtest ──────────────────────────────────────────────
        tk.Label(self, text="INDIVIDUAL BACKTEST", bg=BG, fg=MUTED,
                 font=("Courier", 10)).pack(anchor="w", padx=16)

        bt_frame = tk.Frame(self, bg=BG)
        bt_frame.pack(fill="x", padx=16, pady=6)
        
        tk.Label(bt_frame, text="Ticker(s):", bg=BG, fg=MUTED, font=("Courier", 10)).pack(side="left")
        self.bt_tickers_var = tk.StringVar()
        self.bt_tickers_entry = tk.Entry(bt_frame, textvariable=self.bt_tickers_var, width=18, bg=BG2, fg=TEXT, insertbackground=TEXT, bd=0, highlightthickness=1, highlightcolor=BORDER, highlightbackground=BORDER, font=("Courier", 12))
        self.bt_tickers_entry.pack(side="left", padx=(4, 12), ipady=5)
        
        tk.Label(bt_frame, text="Days:", bg=BG, fg=MUTED, font=("Courier", 10)).pack(side="left")
        self.bt_days_var = tk.StringVar(value="1500")
        self.bt_days_entry = tk.Entry(bt_frame, textvariable=self.bt_days_var, width=5, bg=BG2, fg=TEXT, insertbackground=TEXT, bd=0, highlightthickness=1, highlightcolor=BORDER, highlightbackground=BORDER, font=("Courier", 12))
        self.bt_days_entry.pack(side="left", padx=(4, 12), ipady=5)
        
        self.bt_run_btn = tk.Button(
            bt_frame, text="▶ RUN BACKTEST", command=self._run_backtest,
            bg=BG2, fg=TEXT, font=("Courier", 10, "bold"), bd=0, cursor="hand2", padx=12, pady=4,
            activebackground=BG3, activeforeground=TEXT, relief="flat"
        )
        self.bt_run_btn.pack(side="left")

        # ── divider ──────────────────────────────────────────────────────────
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=8)

        # ── progress log ─────────────────────────────────────────────────────
        tk.Label(self, text="OUTPUT", bg=BG, fg=MUTED,
                 font=("Courier", 10)).pack(anchor="w", padx=16)
        log_frame = tk.Frame(self, bg=BG2, bd=1, relief="flat",
                             highlightthickness=1, highlightbackground=BORDER)
        log_frame.pack(fill="both", expand=True, padx=16, pady=4)

        self.log = tk.Text(log_frame, height=12, width=72,
                           bg=BG2, fg=TEXT, insertbackground=TEXT,
                           font=("Courier", 11), bd=0, relief="flat",
                           wrap="word", state="disabled",
                           selectbackground=BG3)
        scrollbar = tk.Scrollbar(log_frame, command=self.log.yview,
                                 bg=BG2, troughcolor=BG2, bd=0)
        self.log.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True, padx=8, pady=8)

        # tag colours for output
        self.log.tag_config("ok",     foreground=GREEN)
        self.log.tag_config("warn",   foreground=AMBER)
        self.log.tag_config("err",    foreground=RED)
        self.log.tag_config("info",   foreground=CYAN)
        self.log.tag_config("muted",  foreground=MUTED)

        # ── action buttons ────────────────────────────────────────────────────
        btn_frame = tk.Frame(self, bg=BG)
        btn_frame.pack(fill="x", padx=16, pady=(8, 16))

        self.run_btn = tk.Button(
            btn_frame,
            text="▶  GENERATE BATTLE CARD",
            command=self._run,
            bg=GREEN, fg=BG, font=("Courier", 13, "bold"),
            bd=0, cursor="hand2", padx=20, pady=12,
            activebackground=GREEN, activeforeground=BG,
            relief="flat"
        )
        self.run_btn.pack(side="left", expand=True, fill="x", padx=(0, 6))

        self.open_btn = tk.Button(
            btn_frame,
            text="⎋  OPEN LAST CARD",
            command=self._open_last,
            bg=BG2, fg=TEXT, font=("Courier", 13),
            bd=0, cursor="hand2", padx=20, pady=12,
            activebackground=BG3, activeforeground=TEXT,
            relief="flat"
        )
        self.open_btn.pack(side="left", expand=True, fill="x", padx=(6, 0))

        # ── status bar ────────────────────────────────────────────────────────
        self.status_var = tk.StringVar(value="Ready — select a regime and click Generate")
        tk.Label(self, textvariable=self.status_var, bg=BG1, fg=MUTED,
                 font=("Courier", 10), anchor="w", padx=16, pady=6).pack(
                     fill="x", side="bottom")

    # ── order server ────────────────────────────────────────────────────────
    def _start_order_server(self):
        """Start order_server in background (blocks up to 10 s for TWS handshake)."""
        try:
            import order_server
            port = order_server.start()
            self.after(0, lambda: self._log(f"✓ Order server ready on localhost:{port}\n", "ok"))
        except Exception as e:
            self.after(0, lambda: self._log(f"⚠ Order server unavailable: {e}\n", "warn"))

    # ── TWS status ──────────────────────────────────────────────────────────
    def _check_tws(self):
        """Quick TCP probe to port 7496; no ib_insync needed here."""
        try:
            s = socket.create_connection(("127.0.0.1", 7496), timeout=1)
            s.close()
            connected = True
        except OSError:
            connected = False

        if connected:
            self.tws_dot.config(fg=GREEN)
            self.tws_lbl.config(text="TWS: LIVE", fg=GREEN)
        else:
            self.tws_dot.config(fg=MUTED)
            self.tws_lbl.config(text="TWS: FALLBACK", fg=MUTED)
        self.after(30_000, self._check_tws)   # re-check every 30 s

    # ── interactions ────────────────────────────────────────────────────────
    def _toggle_key(self):
        self.show_key = not self.show_key
        self.key_entry.config(show="" if self.show_key else "•")
        self.show_btn.config(text="hide" if self.show_key else "show")

    def _save_key_clicked(self):
        k = self.api_key_var.get().strip()
        if k:
            save_api_key(k)
            self._log("API key saved.\n", "ok")
        else:
            self._log("Key field is empty — nothing saved.\n", "warn")

    def _select_regime(self, key):
        if self.running: return
        self.selected_regime.set(key)
        cfg = REGIME_CONFIG[key]
        # Reset all buttons
        for k, btn in self.regime_buttons.items():
            btn.config(bg=BG2, fg=MUTED)
        # Highlight selected
        self.regime_buttons[key].config(bg=cfg["color"], fg=BG)
        self.status_var.set(f"Regime selected: {cfg['label']} — {cfg['desc']}")

    def _log(self, msg, tag=""):
        self.log.configure(state="normal")
        if tag:
            self.log.insert("end", msg, tag)
        else:
            self.log.insert("end", msg)
        self.log.see("end")
        self.log.configure(state="disabled")
        self.update_idletasks()

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _check_python(self):
        self._log("Checking Python environment...\n", "muted")
        self.python_exe = find_python()
        if self.python_exe:
            self._log(f"✓ Python found: {self.python_exe}\n", "ok")
        else:
            self._log("✗ Python 3 with required packages not found.\n", "err")
            self._log("  Run in Terminal:  pip3 install --user yfinance pandas numpy requests\n", "warn")

    def _run(self):
        if self.running:
            return
        regime = self.selected_regime.get()
        if not regime:
            self._log("No regime selected. Click a regime button first.\n", "warn")
            return
        key = self.api_key_var.get().strip()
        if not key:
            self._log("Finnhub API key is empty. Enter and save your key first.\n", "warn")
            return
        if not self.python_exe:
            self._log("Python environment not ready. See message above.\n", "err")
            return
        if not BATTLE_CARD.exists():
            self._log(f"battle_card.py not found at:\n  {BATTLE_CARD}\n", "err")
            return

        save_api_key(key)
        self.running = True
        self.run_btn.config(state="disabled", text="⏳  RUNNING...", bg=MUTED)
        self.status_var.set(f"Generating {regime} battle card...")
        self._clear_log()
        self._log(f"▶  {regime}\n\n", "info")

        env = os.environ.copy()
        env["FINNHUB_API_KEY"] = key

        def worker():
            try:
                proc = subprocess.Popen(
                    [self.python_exe, str(BATTLE_CARD), regime, "--no-open"],
                    cwd=str(SCRIPT_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=env,
                    bufsize=1,
                )
                for line in proc.stdout:
                    # colour-code output lines
                    line_s = line.strip()
                    if any(x in line_s for x in ["STRONG BUY", "🎯"]):
                        self.after(0, lambda l=line: self._log(l, "ok"))
                    elif any(x in line_s for x in ["WATCH", "⏳"]):
                        self.after(0, lambda l=line: self._log(l, "warn"))
                    elif any(x in line_s for x in ["✗", "ERROR", "error"]):
                        self.after(0, lambda l=line: self._log(l, "err"))
                    elif any(x in line_s for x in ["⚠", "warn"]):
                        self.after(0, lambda l=line: self._log(l, "warn"))
                    elif any(x in line_s for x in ["▸", "Battle card"]):
                        self.after(0, lambda l=line: self._log(l, "info"))
                    else:
                        self.after(0, lambda l=line: self._log(l))
                proc.wait()
                success = proc.returncode == 0
                self.after(0, lambda: self._on_done(regime, success))
            except Exception as e:
                self.after(0, lambda: self._log(f"\n✗ Error: {e}\n", "err"))
                self.after(0, lambda: self._on_done(regime, False))

        threading.Thread(target=worker, daemon=True).start()

    def _on_done(self, regime, success):
        self.running = False
        self.run_btn.config(state="normal", text="▶  GENERATE BATTLE CARD", bg=GREEN)
        if success:
            self.status_var.set(f"✓ {regime} battle card ready")
            self._log("\n✓ Done. Opening in browser...\n", "ok")
            card = find_latest_card(regime)
            if card:
                webbrowser.open(f"file://{card}")
        else:
            self.status_var.set("✗ Generation failed — see output above")
            self._log("\n✗ Failed. Check output above for details.\n", "err")

    def _open_last(self):
        regime = self.selected_regime.get() or None
        card = find_latest_card(regime)
        if card:
            webbrowser.open(f"file://{card}")
            self.status_var.set(f"Opening: {card.name}")
        else:
            self._log("No battle card found. Generate one first.\n", "warn")

    def _run_backtest(self):
        if self.running:
            return
        tickers_str = self.bt_tickers_var.get().strip()
        if not tickers_str:
            self._log("Enter at least one ticker to backtest.\n", "warn")
            return
            
        days_str = self.bt_days_var.get().strip()
        try:
            days = int(days_str)
        except ValueError:
            self._log("Invalid lookback days. Using 1500.\n", "warn")
            days = 1500
            
        if not self.python_exe:
            self._log("Python environment not ready. See message above.\n", "err")
            return
            
        BACKTEST_SCRIPT = SCRIPT_DIR / "backtest.py"
        if not BACKTEST_SCRIPT.exists():
            self._log(f"backtest.py not found at:\n  {BACKTEST_SCRIPT}\n", "err")
            return

        tickers_list = tickers_str.split()

        self.running = True
        self.bt_run_btn.config(state="disabled", text="⏳ RUNNING...", bg=MUTED)
        self.status_var.set(f"Running backtest for {', '.join(tickers_list)}...")
        self._clear_log()
        self._log(f"▶  BACKTEST: {', '.join(tickers_list)} ({days} days)\n\n", "info")

        env = os.environ.copy()
        key = self.api_key_var.get().strip()
        if key: env["FINNHUB_API_KEY"] = key

        def worker():
            try:
                cmd = [self.python_exe, str(BACKTEST_SCRIPT)] + tickers_list + ["--days", str(days)]
                proc = subprocess.Popen(
                    cmd, cwd=str(SCRIPT_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, env=env, bufsize=1
                )
                for line in proc.stdout:
                    line_s = line.strip()
                    if "Net Return" in line_s or "Avg Win" in line_s:
                        self.after(0, lambda l=line: self._log(l, "ok"))
                    elif "WinRate:" in line_s and not ("0.0%" in line_s):
                        self.after(0, lambda l=line: self._log(l, "ok"))
                    elif "Losses" in line_s or "Avg Loss" in line_s:
                        self.after(0, lambda l=line: self._log(l, "warn"))
                    elif "Error" in line_s or "Insufficient" in line_s or "No trades" in line_s:
                        self.after(0, lambda l=line: self._log(l, "err"))
                    elif "=====" in line_s or "SUMMARY" in line_s:
                        self.after(0, lambda l=line: self._log(l, "info"))
                    else:
                        self.after(0, lambda l=line: self._log(l))
                proc.wait()
                self.after(0, lambda: self._on_backtest_done())
            except Exception as e:
                self.after(0, lambda: self._log(f"\n✗ Error: {e}\n", "err"))
                self.after(0, lambda: self._on_backtest_done())

        threading.Thread(target=worker, daemon=True).start()
        
    def _on_backtest_done(self):
        self.running = False
        self.bt_run_btn.config(state="normal", text="▶ RUN BACKTEST", bg=BG2)
        self.status_var.set("✓ Backtest complete")
        self._log("\n✓ Done.\n", "ok")


# ── entry point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = STFSApp()
    app.mainloop()
