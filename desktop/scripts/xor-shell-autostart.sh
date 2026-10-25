#!/bin/sh
# Start one XOR Shell instance for the XFCE session.  XFCE's own panel
# remains available during the Aurora preview, so a shell failure never
# leaves the user without a desktop.
set -eu

[ -n "${DISPLAY:-}" ] || exit 0
if command -v pgrep >/dev/null 2>&1; then
    if pgrep -u "$(id -u)" -f "python3 -m xor_shell" >/dev/null 2>&1; then
        exit 0
    fi
fi

# Give XFWM/XFCE a moment to own the session before placing dock windows.
sleep 2
exec xor-shell
