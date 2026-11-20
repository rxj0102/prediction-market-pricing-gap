#!/bin/sh
# Install the Mac collector as a launchd job that starts every 10 minutes.
# Run from anywhere inside the repo: sh mac/install.sh
# Remove it with: sh mac/install.sh uninstall
set -e
LABEL=com.rxj0102.pmgap.collect
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
REPO=$(cd "$(dirname "$0")/.." && pwd)
PYTHON=$(command -v python3)
LOG="$HOME/Library/Logs/pmgap-collect.log"

if [ "$1" = "uninstall" ]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed $LABEL"
  exit 0
fi

if [ ! -s "$HOME/.config/pmgap/tradier_token" ]; then
  echo "note: no Tradier token at ~/.config/pmgap/tradier_token. Save it there with:"
  echo "  mkdir -p ~/.config/pmgap && pbpaste > ~/.config/pmgap/tradier_token && chmod 600 ~/.config/pmgap/tradier_token"
fi
"$PYTHON" -c "import requests" 2>/dev/null || "$PYTHON" -m pip install --user requests
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$REPO/mac/mac_collect.py</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL, running every 10 minutes from $REPO"
echo "log: $LOG"
"$PYTHON" "$REPO/mac/mac_collect.py" --dry-run || true
