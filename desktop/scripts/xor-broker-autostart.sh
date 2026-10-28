#!/bin/sh
# Session autostart for the XOR permission broker.
# Creates demo data + starter grants on first run (never overwrites),
# then serves broker requests on the per-user Unix socket.  Write
# confirmations use the GTK dialog (Escape/unfocused Enter = deny).
set -e
xor-broker init >/dev/null 2>&1 || true
TOKEN_DIR="${XOR_TOKEN_DIR:-$HOME/.local/state/xor/broker/tokens}"
export XOR_TOKEN_DIR="$TOKEN_DIR"
xor-broker token issue demo-reader --token-dir "$TOKEN_DIR" --quiet || true
xor-broker token issue demo-writer --token-dir "$TOKEN_DIR" --quiet || true
exec xor-broker serve --confirm gtk --token-dir "$TOKEN_DIR" --require-tokens
