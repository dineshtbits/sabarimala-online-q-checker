#!/usr/bin/env python3
"""
Empirical test: how long does a saved Playwright session (storage_state --
cookies + localStorage) stay authenticated against the Sabarimala portal?

This does NOT touch sabarimala_monitor.py or marquee_monitor.py -- it's a
standalone probe to learn the portal's session lifetime before deciding
whether to build session-reuse into the production scripts (to reduce how
often we hit the actual login endpoint, out of concern for triggering
account lockout/rate-limiting from frequent automated logins).

The saved session file lives under logs/ but is NOT one of the two
filenames whitelisted in .gitignore (last_state.json, last_marquee.json),
so it's excluded by the existing `logs/*` rule automatically -- this file
contains live session credentials and must never be committed.

Usage:
    python3 test_session_persistence.py init          # fresh login, save session, record a check
    python3 test_session_persistence.py check <label>  # try reusing the saved session, record result

Results accumulate in logs/.session_test_log.json so the history can be
reviewed at any point.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from sabarimala_monitor import (
    BASE_URL,
    HEADLESS,
    SELECTOR_DASHBOARD_READY,
    USER_AGENT,
    VIEWPORT,
    login,
    setup_logging,
)

SESSION_STATE_PATH = Path("logs/.session_state_test.json")
HISTORY_PATH = Path("logs/.session_test_log.json")


def check_session_valid(logger) -> bool:
    if not SESSION_STATE_PATH.exists():
        logger.error("No saved session state found at %s", SESSION_STATE_PATH)
        return False

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport=VIEWPORT,
            storage_state=str(SESSION_STATE_PATH),
        )
        page = context.new_page()
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        try:
            page.locator(SELECTOR_DASHBOARD_READY).first.wait_for(state="visible", timeout=8_000)
            valid = True
        except Exception:
            valid = False
        context.close()
        browser.close()
    return valid


def create_fresh_session(logger) -> None:
    Path("logs").mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        context = browser.new_context(user_agent=USER_AGENT, viewport=VIEWPORT)
        page = context.new_page()
        login(page, logger)
        context.storage_state(path=str(SESSION_STATE_PATH))
        logger.info("Saved fresh session state -> %s", SESSION_STATE_PATH)
        context.close()
        browser.close()


def record_check(logger, label: str) -> bool:
    valid = check_session_valid(logger)
    history = []
    if HISTORY_PATH.exists():
        try:
            history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            history = []
    history.append({"label": label, "time": datetime.now().isoformat(), "valid": valid})
    HISTORY_PATH.write_text(json.dumps(history, indent=2), encoding="utf-8")
    logger.info("[%s] session valid=%s", label, valid)
    return valid


if __name__ == "__main__":
    logger = setup_logging()
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"

    if mode == "init":
        create_fresh_session(logger)
        record_check(logger, "t+0 (immediate sanity check)")
    elif mode == "check":
        label = sys.argv[2] if len(sys.argv) > 2 else "unlabeled"
        record_check(logger, label)
    else:
        print("Usage: test_session_persistence.py [init | check <label>]")
        sys.exit(1)
