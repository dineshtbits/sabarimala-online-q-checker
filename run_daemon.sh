#!/bin/bash
# Wrapper that gives continuous_monitor.py its self-update behavior.
#
# launchd keeps THIS script alive (not the Python process directly, via
# KeepAlive in the plist). Every time it starts -- the very first time, or
# any time continuous_monitor.py exits (it exits on its own after
# detecting a new commit upstream, or if it crashes) -- this:
#   1. Pulls the latest code from origin/main.
#   2. Compile-checks it before trusting it.
#   3. If the new code is broken, reverts the working tree to the last
#      commit that was known to compile, instead of looping forever on
#      broken code with nobody there to fix it.
#   4. Runs continuous_monitor.py.
#
# This is what makes "git push from anywhere" enough to update the daemon
# with no manual steps on the machine running this.

set -uo pipefail
cd "$(dirname "$0")"

# launchd does not source ~/.zshrc or ~/.bash_profile, so credentials
# exported there are invisible to this process. Keep them in a dedicated,
# NEVER-committed file instead (see the setup instructions) -- not in this
# repo and not in the launchd plist itself.
ENV_FILE="$HOME/.sabarimala_daemon_env"
if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    source "$ENV_FILE"
else
    echo "[run_daemon] WARNING: $ENV_FILE not found -- required env vars (SABARIMALA_USERNAME etc.) are unset"
fi

mkdir -p logs
GOOD_COMMIT_FILE="logs/.last_good_commit"
COMPILE_LOG="logs/.last_compile_error.log"

echo "[run_daemon] $(date): pulling latest code"
git fetch origin main --quiet
git pull --ff-only origin main --quiet || echo "[run_daemon] pull failed (offline? conflict?) -- running existing code"

if python3 -m py_compile sabarimala_monitor.py continuous_monitor.py marquee_monitor.py 2>"$COMPILE_LOG"; then
    git rev-parse HEAD > "$GOOD_COMMIT_FILE"
    echo "[run_daemon] new code compiles cleanly, proceeding"
else
    echo "[run_daemon] pulled code FAILED to compile -- see $COMPILE_LOG"
    cat "$COMPILE_LOG"
    if [ -f "$GOOD_COMMIT_FILE" ]; then
        GOOD_COMMIT="$(cat "$GOOD_COMMIT_FILE")"
        echo "[run_daemon] reverting working tree to last known-good commit $GOOD_COMMIT"
        git checkout "$GOOD_COMMIT" -- . --quiet
    else
        echo "[run_daemon] no known-good commit on record -- running current (possibly broken) code anyway"
    fi
fi

exec python3 continuous_monitor.py
