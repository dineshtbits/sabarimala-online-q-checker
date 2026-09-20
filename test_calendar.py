#!/usr/bin/env python3
"""
Standalone login + calendar-check smoke test for sabarimala_monitor.py.

Exercises login() -> open_virtual_q_calendar() -> check_calendar() -- no
email is sent, so GMAIL_* env vars are not required for this test.

Usage:
    export SABARIMALA_USERNAME="..."
    export SABARIMALA_PASSWORD="..."
    HEADLESS=false python3 test_calendar.py
"""

import sys
from datetime import datetime

from playwright.sync_api import sync_playwright

from sabarimala_monitor import (
    HEADLESS,
    LOG_DIR,
    TARGET_DAYS,
    TARGET_MONTH_NAME,
    TARGET_YEAR,
    USER_AGENT,
    VIEWPORT,
    check_calendar,
    login,
    open_virtual_q_calendar,
    setup_logging,
)


def main() -> int:
    logger = setup_logging()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    screenshot_path = LOG_DIR / f"test_calendar_{timestamp}.png"

    exit_code = 1
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        context = browser.new_context(user_agent=USER_AGENT, viewport=VIEWPORT)
        page = context.new_page()

        try:
            login(page, logger)
            open_virtual_q_calendar(page, logger)
            result = check_calendar(page, logger)

            print("\n" + "=" * 60)
            print(f"Target: {TARGET_MONTH_NAME} {TARGET_DAYS}, {TARGET_YEAR}")
            print(f"Month reached: {result['month_reached']}")
            print(f"Day statuses: {result['day_statuses']}")
            print("=" * 60 + "\n")

            exit_code = 0
        except Exception as exc:  # noqa: BLE001
            logger.error("CALENDAR TEST: FAILED -> %s", exc)

        try:
            page.screenshot(path=str(screenshot_path), full_page=True)
            logger.info("Saved screenshot -> %s", screenshot_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not capture screenshot: %s", exc)

        context.close()
        browser.close()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
