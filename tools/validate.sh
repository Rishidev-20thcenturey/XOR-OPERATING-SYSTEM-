#!/bin/sh
# Static validation wrapper - see tools/validate.py for the checks.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
exec python3 "$REPO/tools/validate.py" "$@"
