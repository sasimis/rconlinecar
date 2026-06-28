#!/data/data/com.termux/files/usr/bin/sh
set -eu

: "${RC_SERVER:=https://100.70.113.90:5002}"

UPDATE_URL="${BRIDGE_UPDATE_URL:-$RC_SERVER/bridge/phone_bridge.py}"

echo "Updating phone_bridge.py from $UPDATE_URL"
curl -k -fsSL "$UPDATE_URL" -o phone_bridge.py
python phone_bridge.py
