#!/bin/bash
# ============================================================
# STFS-EQ Setup — run this ONE TIME to install the desktop app
# After this, just double-click "STFS-EQ.command" on your Desktop
# ============================================================

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
DESKTOP="$HOME/Desktop"
LAUNCHER_NAME="STFS-EQ.command"
DESKTOP_LAUNCHER="$DESKTOP/$LAUNCHER_NAME"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   STFS-EQ  One-Time Setup                ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# 1. Find Python 3
echo "▸ Looking for Python 3..."
if [ -x "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3" ]; then
    PY="/Library/Frameworks/Python.framework/Versions/3.13/bin/python3"
    echo "  ✓ Found Python 3.13 Framework"
elif command -v python3.11 &>/dev/null; then
    PY="python3.11"
    echo "  ✓ Found: $PY"
else
    echo "  ✗ python3.11 not found — trying python3..."
    if command -v python3 &>/dev/null; then
        PY="python3"
        echo "  ✓ Using: $PY"
    else
        echo "  ✗ No Python 3 found. Install it from python.org"
        read -p "Press Enter to close..."
        exit 1
    fi
fi

# 2. Check required packages
echo ""
echo "▸ Checking packages..."
$PY -c "import yfinance, pandas, numpy, requests, tkinter" 2>/dev/null && {
    echo "  ✓ All packages present"
} || {
    echo "  ▸ Installing missing packages..."
    $PY -m pip install --user yfinance pandas numpy requests 2>&1 | tail -3
    echo "  ✓ Done"
}

# 3. Make launcher.py executable
echo ""
echo "▸ Setting permissions..."
chmod +x "$SCRIPT_DIR/launcher.py"
echo "  ✓ launcher.py is executable"

# 4. Create a Desktop shortcut (.command file)
echo ""
echo "▸ Creating Desktop shortcut..."
cat > "$DESKTOP_LAUNCHER" << EOF
#!/bin/bash
cd "$SCRIPT_DIR"
$PY "$SCRIPT_DIR/launcher.py"
EOF
chmod +x "$DESKTOP_LAUNCHER"
echo "  ✓ Created: $DESKTOP_LAUNCHER"

# 5. Done
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   ✓ Setup complete!                      ║"
echo "║                                          ║"
echo "║   Your Desktop now has:                  ║"
echo "║   📄 STFS-EQ.command                     ║"
echo "║                                          ║"
echo "║   Double-click it anytime to open the   ║"
echo "║   Battle Card Generator.                 ║"
echo "║                                          ║"
echo "║   First run: macOS may ask you to allow  ║"
echo "║   it. Right-click → Open → Open.         ║"
echo "╚══════════════════════════════════════════╝"
echo ""
read -p "Press Enter to close this window..."
