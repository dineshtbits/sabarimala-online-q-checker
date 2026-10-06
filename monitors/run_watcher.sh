#!/bin/bash
# Run by launchd on an interval (see the plist). Each invocation:
#   1. Pulls the latest code, so pushed changes take effect on the next run
#      -- no service restart needed, since every run is a fresh process.
#   2. Loads credentials from a file outside the repo (launchd does not
#      source ~/.zshrc).
#   3. Runs one watcher check.

set -uo pipefail
cd "$(dirname "$0")/.."

ENV_FILE="$HOME/.sabarimala_watcher_env"
if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    source "$ENV_FILE"
else
    echo "[run_watcher] WARNING: $ENV_FILE not found; credentials unset"
fi

mkdir -p logs
git fetch origin main --quiet || echo "[run_watcher] fetch failed; running current code"
git pull --ff-only origin main --quiet || echo "[run_watcher] pull failed; running current code"

# Per-run logs accumulate fast at this interval; keep a week.
find logs -name 'run_*.log' -mtime +7 -delete 2>/dev/null

export HEADLESS=true
exec python3 monitors/watcher.py
