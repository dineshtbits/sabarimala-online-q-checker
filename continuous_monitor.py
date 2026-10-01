#!/usr/bin/env python3
"""
Long-running daemon: logs in ONCE, then loops indefinitely -- wait, reload
the SAME live page, re-check December (TARGET_DAYS), repeat. This exists
specifically to minimize logins: one per process lifetime (barring an
unexpected logout mid-run), instead of one per check like the scheduled
sabarimala_monitor.py.

Why this is safe: a hard page.reload() within an already-authenticated,
continuously-running session preserves the auth token fine (verified
empirically). What does NOT work is restoring a saved session into a
*fresh* browser context later -- the app detects that as a replayed/cold
session and wipes its own auth state. So this script deliberately never
closes and reopens the browser; it keeps one page alive for its entire
run.

Not run via GitHub Actions: a self-hosted runner only processes one job
at a time, so an infinite job here would permanently block the daily
scheduled workflows (sabarimala-monitor.yml, marquee-monitor.yml) from
ever running. Instead this is meant to run as its own persistent
background process -- see run_daemon.sh and the accompanying launchd
plist, which is how it survives reboots and needs no logged-in terminal.

Self-updating: once per cycle, checks whether origin/main has moved past
the commit this process started from. If so, it exits cleanly (does NOT
run git itself) so the wrapper script (run_daemon.sh), which launchd
re-invokes on exit, can pull the new code and relaunch. A code update
therefore takes effect within one cycle interval of being pushed, with no
manual steps on the machine running this.

Usage:
    export SABARIMALA_USERNAME="..."
    export SABARIMALA_PASSWORD="..."
    export SMTP_USER="..."
    export SMTP_PASS="..."
    export MAIL_TO="..."
    HEADLESS=true python3 continuous_monitor.py

Normally started via run_daemon.sh (see that file), not directly.
"""

import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from sabarimala_monitor import (
    HEADLESS,
    LOG_DIR,
    SELECTOR_DASHBOARD_READY,
    TARGET_DAYS,
    TARGET_MONTH_NAME,
    USER_AGENT,
    VIEWPORT,
    build_email_body,
    check_calendar,
    login,
    navigate_to_virtual_q,
    open_date_picker,
    send_email_notification,
    setup_logging,
    write_status_report,
)

CHECK_INTERVAL_SECONDS = 15 * 60  # 15 minutes

# Local-only, not committed: excluded by the existing `logs/*` gitignore
# rule (only last_state.json and last_marquee.json are whitelisted). This
# daemon restarts fairly rarely (on code updates or crashes) so a local
# file is enough -- no need for the git-commit-back dance used by the
# scheduled workflows' state files.
DAEMON_STATE_FILE = LOG_DIR / ".daemon_state.json"


def get_current_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()
    except Exception:
        return ""


def remote_has_update(started_commit: str, logger: logging.Logger) -> bool:
    if not started_commit:
        return False
    try:
        subprocess.run(
            ["git", "fetch", "origin", "main", "--quiet"], check=True, timeout=30
        )
        remote_commit = subprocess.run(
            ["git", "rev-parse", "origin/main"],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
        return bool(remote_commit) and remote_commit != started_commit
    except Exception as exc:
        logger.warning("Could not check for upstream updates: %s", exc)
        return False


def load_daemon_state() -> dict:
    if DAEMON_STATE_FILE.exists():
        try:
            return json.loads(DAEMON_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_daemon_state(day_statuses: dict, timestamp: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    DAEMON_STATE_FILE.write_text(
        json.dumps({"day_statuses": day_statuses, "last_run": timestamp}, indent=2),
        encoding="utf-8",
    )


def is_still_logged_in(page) -> bool:
    try:
        page.locator(SELECTOR_DASHBOARD_READY).first.wait_for(state="visible", timeout=5_000)
        return True
    except Exception:
        return False


def run_cycle(page, logger: logging.Logger) -> dict:
    """Reload in place, re-open the date picker, re-check the target days."""
    page.reload(wait_until="domcontentloaded", timeout=30_000)

    if not is_still_logged_in(page):
        logger.warning("Session appears to have been logged out -- re-logging in")
        login(page, logger)
        navigate_to_virtual_q(page, logger)

    open_date_picker(page, logger)
    return check_calendar(page, logger)


def send_alert(result: dict, newly_open_days: list[str], page, logger: logging.Logger) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_path = LOG_DIR / f"daemon_screenshot_{timestamp}.png"
    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
    except Exception as exc:
        logger.error("Could not capture screenshot: %s", exc)
        screenshot_path = None

    report_path = write_status_report(result, newly_open_days, timestamp)

    try:
        send_email_notification(
            subject=f"[Sabarimala Monitor] ALERT: {TARGET_MONTH_NAME} {', '.join(newly_open_days)} now OPEN",
            body_text=build_email_body(result, newly_open_days, timestamp),
            screenshot_path=screenshot_path,
            report_path=report_path,
            logger=logger,
            high_priority=True,
        )
    except Exception as exc:
        logger.error("Failed to send alert email: %s", exc)


def main() -> int:
    logger = setup_logging()
    started_commit = get_current_commit()
    logger.info("Daemon starting at commit %s", started_commit[:12] if started_commit else "unknown")

    previous_day_statuses = load_daemon_state().get("day_statuses", {})

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        context = browser.new_context(user_agent=USER_AGENT, viewport=VIEWPORT)
        page = context.new_page()

        logger.info("Initial login (the only one this process should need)")
        login(page, logger)
        navigate_to_virtual_q(page, logger)

        while True:
            try:
                result = run_cycle(page, logger)
                day_statuses = result.get("day_statuses", {})

                newly_open_days = [
                    str(day)
                    for day in TARGET_DAYS
                    if previous_day_statuses.get(str(day)) == "disabled"
                    and day_statuses.get(str(day)) == "enabled"
                ]

                if day_statuses:
                    save_daemon_state(day_statuses, datetime.now().strftime("%Y%m%d_%H%M%S"))
                    previous_day_statuses = day_statuses

                if newly_open_days:
                    logger.info("ALERT: newly open day(s): %s", newly_open_days)
                    send_alert(result, newly_open_days, page, logger)
                else:
                    logger.info("No change. Day statuses: %s", day_statuses)

            except Exception as exc:  # noqa: BLE001 - one bad cycle must not kill the daemon
                logger.error("Cycle failed, will retry next interval: %s", exc)

            if remote_has_update(started_commit, logger):
                logger.info("New code detected upstream -- exiting so the wrapper can restart with it")
                break

            time.sleep(CHECK_INTERVAL_SECONDS)

        context.close()
        browser.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
