#!/usr/bin/env python3
"""
One fast check for the local launchd watcher: log in, read the Virtual-Q
calendar, and push an ntfy.sh alert if any watched day is open. Runs once
per invocation -- launchd (see run_watcher.sh) invokes it on an interval,
so each run is a fresh process and picks up pulled code automatically.

Required env: SABARIMALA_USERNAME, SABARIMALA_PASSWORD.
Optional env: NTFY_TOPIC (no alerts are pushed without it), HEADLESS.
"""

import json
import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright

from sabarimala_monitor import (
    HEADLESS,
    TARGET_DAYS,
    TARGET_MONTH_NAME,
    USER_AGENT,
    VIEWPORT,
    check_calendar,
    login,
    navigate_to_virtual_q,
    open_date_picker,
    setup_logging,
)

NTFY_BASE = "https://ntfy.sh"


def push(topic: str, title: str, message: str, priority: str, logger) -> None:
    if not topic:
        logger.warning("NTFY_TOPIC not set; not pushing: %s", title)
        return
    req = urllib.request.Request(
        f"{NTFY_BASE}/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority, "Tags": "rotating_light" if priority == "urgent" else "warning"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        logger.info("ntfy push sent (%s), HTTP %s", priority, resp.status)


def main() -> int:
    logger = setup_logging()
    topic = os.environ.get("NTFY_TOPIC", "").strip()

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=HEADLESS)
            context = browser.new_context(user_agent=USER_AGENT, viewport=VIEWPORT)
            page = context.new_page()
            try:
                login(page, logger)
                navigate_to_virtual_q(page, logger)
                open_date_picker(page, logger)
                result = check_calendar(page, logger)
            finally:
                context.close()
                browser.close()
    except Exception as exc:  # noqa: BLE001
        logger.error("Watcher check failed: %s", exc)
        push(topic, "Sabarimala watcher FAILED",
             f"Check failed: {exc}. Booking may be unmonitored until this clears.",
             "high", logger)
        return 1

    statuses = result.get("day_statuses", {})
    open_days = [str(d) for d in TARGET_DAYS if statuses.get(str(d)) == "enabled"]
    logger.info("Statuses: %s", json.dumps(statuses))

    if open_days:
        push(topic, f"{TARGET_MONTH_NAME} {', '.join(open_days)} OPEN - book now",
             f"Virtual-Q open for {TARGET_MONTH_NAME} {', '.join(open_days)}. Book immediately.",
             "urgent", logger)
    return 0


if __name__ == "__main__":
    sys.exit(main())
