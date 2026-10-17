#!/usr/bin/env bash
# Start Ghost-Vision with your real boards (macOS / Linux).
# No boards yet? Use: bash run_simulation.sh
exec bash "$(dirname "$0")/run_simulation.sh" --live "$@"
