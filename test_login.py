#!/usr/bin/env python3
"""
Standalone login-only smoke test for sabarimala_monitor.py.

Exercises just login() -- no calendar check, no email -- so you can verify
the SELECTOR_LOGIN_BUTTON / SELECTOR_USERNAME_FIELD / SELECTOR_PASSWORD_FIELD
/ SELECTOR_SUBMIT_BUTTON / SELECTOR_DASHBOARD_READY selectors before wiring
up Gmail.

Usage:
    export SABARIMALA_USERNAME="..."
    export SABARIMALA_PASSWORD="..."
    HEADLESS=false python3 test_login.py

On success it leaves the browser open for a few seconds (headed mode) and
saves ./logs/test_login_<timestamp>.png so you can see exactly what
"logged in" looked like.
"""

import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from sabarimala_monitor import (
    HEADLESS,
    LOG_DIR,
    USER_AGENT,
    VIEWPORT,
    login,
    setup_logging,
)


def main() -> int:
    logger = setup_logging()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    screenshot_path = LOG_DIR / f"test_login_{timestamp}.png"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        context = browser.new_context(user_agent=USER_AGENT, viewport=VIEWPORT)
        page = context.new_page()

        try:
            login(page, logger)
            logger.info("LOGIN TEST: SUCCESS")
            exit_code = 0
        except Exception as exc:  # noqa: BLE001
            logger.error("LOGIN TEST: FAILED -> %s", exc)
            exit_code = 1

        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
            logger.info("Saved screenshot -> %s", screenshot_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not capture screenshot: %s", exc)

        if not HEADLESS:
            logger.info("Leaving browser open for 5s so you can inspect it...")
            time.sleep(5)

        context.close()
        browser.close()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
