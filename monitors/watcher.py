#!/usr/bin/env python3
"""
Long-running watcher: logs in once, then every CHECK_INTERVAL_SECONDS reloads
the booking page in the same session and checks the watched days. Pushes an
urgent ntfy.sh alert whenever a watched day is open.

Logs in again only if a reload shows the session is gone. Exits when
origin/main moves past the commit it started from, so run_watcher.sh
(kept alive by launchd) pulls the update and relaunches this process.

Required env: SABARIMALA_USERNAME, SABARIMALA_PASSWORD.
Optional env: NTFY_TOPIC (no alerts are pushed without it), HEADLESS.
"""

import logging
import os
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

from sabarimala_monitor import (
    HEADLESS,
    SELECTOR_USERNAME_FIELD,
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

CHECK_INTERVAL_SECONDS = 10 * 60
NTFY_BASE = "https://ntfy.sh"


def push(topic: str, title: str, message: str, priority: str, logger: logging.Logger) -> None:
    if not topic:
        logger.warning("NTFY_TOPIC not set; not pushing: %s", title)
        return
    req = urllib.request.Request(
        f"{NTFY_BASE}/{topic}",
        data=message.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": priority,
            "Tags": "rotating_light" if priority == "urgent" else "warning",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        logger.info("ntfy push sent (%s), HTTP %s", priority, resp.status)


def current_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=10
    ).stdout.strip()


def upstream_moved(started_commit: str, logger: logging.Logger) -> bool:
    try:
        subprocess.run(["git", "fetch", "origin", "main", "--quiet"], check=True, timeout=30)
        remote = subprocess.run(
            ["git", "rev-parse", "origin/main"],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
        return bool(remote) and remote != started_commit
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not check for upstream updates: %s", exc)
        return False


def still_logged_in(page) -> bool:
    try:
        page.locator(SELECTOR_USERNAME_FIELD).first.wait_for(state="visible", timeout=3_000)
        return False
    except Exception:  # noqa: BLE001
        return True


def main() -> int:
    logger = setup_logging()
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    started_commit = current_commit()
    logger.info("Watcher starting at commit %s", started_commit[:12])

    consecutive_failures = 0
    need_login = True

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        context = browser.new_context(user_agent=USER_AGENT, viewport=VIEWPORT)
        page = context.new_page()

        while True:
            try:
                if need_login:
                    login(page, logger)
                    navigate_to_virtual_q(page, logger)
                    need_login = False
                else:
                    page.reload(wait_until="domcontentloaded", timeout=30_000)
                    if not still_logged_in(page):
                        logger.warning("Session lost; logging in again")
                        login(page, logger)
                        navigate_to_virtual_q(page, logger)

                open_date_picker(page, logger)
                result = check_calendar(page, logger)
                statuses = result.get("day_statuses", {})
                open_days = [str(d) for d in TARGET_DAYS if statuses.get(str(d)) == "enabled"]
                logger.info("Statuses: %s", statuses)

                if open_days:
                    push(topic, f"{TARGET_MONTH_NAME} {', '.join(open_days)} OPEN - book now",
                         f"Virtual-Q open for {TARGET_MONTH_NAME} {', '.join(open_days)}. Book immediately.",
                         "urgent", logger)
                consecutive_failures = 0

            except Exception as exc:  # noqa: BLE001 - one bad cycle must not kill the watcher
                consecutive_failures += 1
                need_login = True
                logger.error("Check failed (%d in a row): %s", consecutive_failures, exc)
                if consecutive_failures == 1:
                    push(topic, "Sabarimala watcher check FAILED",
                         f"Check failed: {exc}. Retrying every {CHECK_INTERVAL_SECONDS // 60} min.",
                         "high", logger)

            if upstream_moved(started_commit, logger):
                logger.info("New code on origin/main; exiting so run_watcher.sh relaunches it")
                break

            time.sleep(CHECK_INTERVAL_SECONDS)

        context.close()
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
