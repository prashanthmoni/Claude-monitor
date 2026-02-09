#!/bin/bash
# ──────────────────────────────────────────────────────────────────────
# Pangolin — Claude Usage Monitor — Installation Script
# ──────────────────────────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.pangolin.claude-monitor"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$PLIST_NAME.plist"

echo "╔══════════════════════════════════════════════════════╗"
echo "║        🦎 Pangolin — Setup                           ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── 1. Check Python ──────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Please install Python 3.8+."
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "✓ Python $PY_VERSION detected"

# ── 2. Install dependencies ─────────────────────────────────────────
echo ""
echo "Installing Python dependencies..."
pip3 install -r "$SCRIPT_DIR/requirements.txt"
echo "✓ Dependencies installed"

# ── 3. Run credential setup (browser-based wizard) ──────────────────
echo ""
echo "Opening setup wizard in your browser..."
echo "(If the browser doesn't open, visit http://localhost:17834)"
echo ""
python3 "$SCRIPT_DIR/main.py" --setup

# ── 4. Auto-launch (optional) ───────────────────────────────────────
echo ""
read -rp "Auto-launch on login? (y/n): " launch

if [ "$launch" = "y" ] || [ "$launch" = "Y" ]; then
    mkdir -p "$PLIST_DIR"

    cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_NAME</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>$SCRIPT_DIR/main.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>$HOME/.claude_usage_monitor_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/.claude_usage_monitor_stderr.log</string>
</dict>
</plist>
PLIST

    launchctl load "$PLIST_PATH" 2>/dev/null || true
    echo "✓ Auto-launch configured ($PLIST_PATH)"
else
    echo "Skipping auto-launch."
fi

# ── Done ─────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  Setup complete!                                     ║"
echo "║  Run the monitor: python3 $SCRIPT_DIR/main.py       ║"
echo "╚══════════════════════════════════════════════════════╝"
