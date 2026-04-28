# Phase 5: Web Dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate terminal interactions by replacing the Tkinter `launcher.py` and raw terminal outputs with a sleek, modern, live-updating Web Dashboard served locally.

**Architecture:** A single Flask web server (`dashboard.py`) replaces the lightweight `order_server.py` HTTP server. It serves a beautifully designed frontend (HTML/CSS/JS) and provides REST APIs to trigger battle card generation, view the portfolio manager, control the trailing stop daemon, and place trades.

**Aesthetics:** The UI MUST follow strict modern web design principles: sleek dark mode, glassmorphism panels, Google Fonts (e.g., Inter/Outfit), subtle hover micro-animations, and premium color palettes (HSL tailored). Do not use generic colors.

---

## Task 1: Environment & Dependencies
- [ ] Add `flask` to the required packages list in `setup.command` so it is automatically installed if missing.
- [ ] Create a `requirements.txt` containing `flask`, `yfinance`, `pandas`, `numpy`, `requests`, `ib_insync` to formalize the environment setup for future use.

## Task 2: Backend Foundation (`dashboard.py`)
- [ ] Create `dashboard.py` using Flask to serve static files (HTML/CSS/JS) and handle API routes.
- [ ] Migrate the existing `order_server.py` logic (trade placement, TWS connection) into `dashboard.py` Flask routes (`/api/order`, `/api/status`).
- [ ] Set `dashboard.py` to run on `localhost:5000`.

## Task 3: Frontend Foundation (Web UI)
- [ ] Create a `web/` directory.
- [ ] Create `web/index.html`, `web/style.css`, and `web/app.js`.
- [ ] Design a premium layout with a persistent sidebar containing tabs: "Launchpad", "Portfolio", "Analytics", "Settings".
- [ ] Implement a rich dark mode aesthetic (glassmorphism cards, Inter font, smooth transitions).

## Task 4: The Launchpad Tab
- [ ] Build a UI form to select the Regime (AUTO or manual overrides) and click "Generate Battle Card".
- [ ] Create an API route in `dashboard.py` that executes `battle_card.py` as a subprocess.
- [ ] Once generated, load the `output/battle_card.html` directly into an iframe or inline view within the dashboard so the user stays in one window.

## Task 5: The Portfolio Manager Tab
- [ ] Create an API route `/api/portfolio` in `dashboard.py` that calls the logic from `portfolio_manager.py` to return JSON of live positions and exit signals.
- [ ] Build a live-updating data grid in the frontend to display positions, P&L, DTE, and Signals with clear visual badging (Red for Close, Yellow for Warning, Green for Hold).

## Task 6: Trailing Stop Daemon Control
- [ ] Add an API route in `dashboard.py` to start/stop `trailing_stop_manager.py` as a background process.
- [ ] Add a visual status indicator and toggle switch in the dashboard UI showing whether the daemon is "Active" or "Offline".

## Task 7: Desktop Shortcut Integration
- [ ] Update `launcher.py` so that it:
  1. Starts `dashboard.py` in the background (if not already running).
  2. Automatically opens `http://localhost:5000` in the user's default web browser.
- [ ] This ensures the user's existing `STFS-EQ.command` desktop shortcut seamlessly transitions to the new Web Dashboard without needing to recreate it.

---

**Completion Criteria:** The user can double-click their desktop shortcut, see a beautiful web interface, generate cards, monitor portfolio exit signals, and manage trailing stops without ever opening a terminal window.
