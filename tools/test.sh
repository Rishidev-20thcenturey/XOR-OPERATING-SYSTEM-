#!/bin/sh
# Run the full XOR baseline test suite:
#   1. broker unit tests (pytest if available, else unittest)
#   2. 200-scenario broker security corpus + acceptance criteria
#   3. assistant-disabled and activity-viewer tests
# Exits non-zero at the FIRST failing stage. No root or display needed.
set -eu

REPO=$(cd "$(dirname "$0")/.." && pwd)
PY=${PYTHON:-python3}
cd "$REPO"

step() { printf '\n== %s ==\n' "$*"; }

if $PY -c "import pytest" 2>/dev/null; then
    step "Unit tests (pytest)"
    (cd broker && $PY -m pytest -q)
    PYTHONPATH=broker/src:assistant/src:desktop $PY -m pytest -q assistant/tests desktop/tests
else
    echo "NOTE: pytest not installed; using unittest (pip install pytest for nicer output)"
    step "Unit tests (unittest)"
    PYTHONPATH=broker/src $PY -m unittest discover -s broker/tests -v
    PYTHONPATH=broker/src:assistant/src $PY -m unittest discover -s assistant/tests -v
    PYTHONPATH=broker/src:desktop $PY -m unittest discover -s desktop/tests -v
fi

step "Security corpus (200 scenarios + 50 injection twins)"
$PY security/run_corpus.py

step "All test stages passed"
