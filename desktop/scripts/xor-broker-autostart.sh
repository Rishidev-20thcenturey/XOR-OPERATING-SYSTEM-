#!/bin/sh
# Session autostart for the XOR permission broker.
# Creates demo data + starter grants on first run (never overwrites),
# then serves broker requests on the per-user Unix socket.  Write
# confirmations use the GTK dialog (Escape/unfocused Enter = deny).
set -e
xor-broker init >/dev/null 2>&1 || true
exec xor-broker serve --confirm gtk
