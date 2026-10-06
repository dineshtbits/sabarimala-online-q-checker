#!/bin/bash
# launchd keeps this script alive (KeepAlive in the plist). Each start:
#   1. Pulls the latest code from origin/main.
#   2. Compile-checks it. If it's broken, reverts to the last commit that
#      compiled, instead of crash-looping on bad code with nobody to fix it.
#   3. Runs monitors/watcher.py. The watcher exits itself when origin/main
#      moves, and launchd relaunches this script, which pulls the update.
# So a git push from anywhere reaches the running watcher within one
# check interval, with no access to this machine.

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
GOOD_COMMIT_FILE="logs/.last_good_commit"
COMPILE_LOG="logs/.last_compile_error.log"

git fetch origin main --quiet || echo "[run_watcher] fetch failed; running current code"
git pull --ff-only origin main --quiet || echo "[run_watcher] pull failed; running current code"

if python3 -m py_compile monitors/sabarimala_monitor.py monitors/watcher.py 2>"$COMPILE_LOG"; then
    git rev-parse HEAD > "$GOOD_COMMIT_FILE"
else
    echo "[run_watcher] pulled code failed to compile:"
    cat "$COMPILE_LOG"
    if [ -f "$GOOD_COMMIT_FILE" ]; then
        echo "[run_watcher] reverting to last good commit $(cat "$GOOD_COMMIT_FILE")"
        git checkout "$(cat "$GOOD_COMMIT_FILE")" -- . --quiet
    fi
fi

# Per-run logs accumulate; keep a week.
find logs -name 'run_*.log' -mtime +7 -delete 2>/dev/null

export HEADLESS=true
exec python3 monitors/watcher.py
