#!/usr/bin/env bash
# ---------------------------------------------------------------------
#  Try Ghost-Vision with no hardware at all (macOS / Linux).
#  Run:   bash run_simulation.sh
#  The first run downloads what it needs (a few minutes). Later runs
#  start straight away.
# ---------------------------------------------------------------------
set -e
cd "$(dirname "$0")/console"

# run_ghost_vision.sh calls this file with --live to use real boards.
MODE="--sim"; LABEL="simulation mode"
if [ "${1:-}" = "--live" ]; then shift; MODE=""; LABEL="live mode - using your boards"; fi

if ! command -v python3 >/dev/null 2>&1; then
    echo
    echo "  Python 3 was not found."
    echo "  macOS: install it from https://www.python.org/downloads/"
    echo "  Linux: sudo apt install python3 python3-venv   (Debian/Ubuntu)"
    echo
    exit 1
fi

# A private folder for Ghost-Vision's add-ons, so nothing else is touched.
if [ ! -x ".venv/bin/python" ]; then
    echo "  First run: setting things up. This happens only once..."
    python3 -m venv .venv || {
        echo
        echo "  Could not create the private Python folder."
        echo "  On Debian/Ubuntu, run:  sudo apt install python3-venv"
        echo
        exit 1
    }
fi

echo "  Checking add-ons..."
.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt

echo "  Starting Ghost-Vision in $LABEL..."
exec .venv/bin/python ghost_vision_ops.py $MODE "$@"
