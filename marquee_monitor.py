#!/usr/bin/env python3
"""
Lightweight, login-free monitor for the Sabarimala portal's scrolling
notice marquee (the "!! Swamy Saranam !! PLEASE NOTE :: ..." banner visible
on the landing page before login).

This is intentionally dumb: it does NOT try to parse or understand the
banner text. It just diffs it against the last-seen version and emails you
when it changes at all -- which is how it catches both an advance notice
("Virtual-Q for December opens on <date> <time>") and the actual opening
announcement, without needing to guess wording in advance.

Meant to run frequently (e.g. every 15 minutes) since it's much cheaper
than the full login+calendar flow in sabarimala_monitor.py (no login, no
calendar navigation) -- see that file for the heavier, exact-day check,
which this script intentionally does NOT trigger. That wiring is for later.

Usage:
    export SMTP_USER="you@gmail.com"
    export SMTP_PASS="your-gmail-app-password"
    export MAIL_TO="you@gmail.com"
    python3 marquee_monitor.py

Note: like sabarimala_monitor.py, this must run from a residential/home IP
-- GitHub-hosted Actions runners get blocked by the portal's bot detection
before the page even finishes loading (confirmed separately). Scheduling
this frequently is a separate, not-yet-decided piece.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from sabarimala_monitor import (
    BASE_URL,
    HEADLESS,
    LOG_DIR,
    USER_AGENT,
    VIEWPORT,
    send_email_notification,
    setup_logging,
)

SELECTOR_MARQUEE = "span.scroll-text"
MARQUEE_STATE_FILE = LOG_DIR / "last_marquee.json"


def fetch_marquee_text(logger) -> str:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        context = browser.new_context(user_agent=USER_AGENT, viewport=VIEWPORT)
        page = context.new_page()
        try:
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
            page.locator(SELECTOR_MARQUEE).first.wait_for(state="visible", timeout=15_000)
            text = page.locator(SELECTOR_MARQUEE).first.inner_text(timeout=5_000).strip()
            logger.info("Fetched marquee text (%d chars)", len(text))
            return text
        finally:
            context.close()
            browser.close()


def load_previous_marquee() -> str | None:
    if MARQUEE_STATE_FILE.exists():
        try:
            data = json.loads(MARQUEE_STATE_FILE.read_text(encoding="utf-8"))
            return data.get("text")
        except (json.JSONDecodeError, OSError):
            return None
    return None


def save_marquee(text: str, timestamp: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    MARQUEE_STATE_FILE.write_text(
        json.dumps({"text": text, "last_checked": timestamp}, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    logger = setup_logging()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        current_text = fetch_marquee_text(logger)
    except Exception as exc:  # noqa: BLE001
        # Deliberately silent (no email) on transient fetch failures --
        # this runs every 15 min and a blip shouldn't page anyone. Check
        # logs/run_*.log if you suspect it's failing repeatedly.
        logger.error("Failed to fetch marquee text: %s", exc)
        return 1

    previous_text = load_previous_marquee()

    if previous_text is None:
        logger.info("No previous marquee text on record -- capturing baseline, no email sent")
        save_marquee(current_text, timestamp)
        return 0

    if current_text == previous_text:
        logger.info("Marquee unchanged")
        return 0

    logger.info("Marquee TEXT CHANGED")
    logger.info("Previous: %s", previous_text)
    logger.info("Current:  %s", current_text)

    save_marquee(current_text, timestamp)

    subject = "[Sabarimala Monitor] Notice banner changed"
    body = (
        "The Sabarimala portal's notice banner changed.\n\n"
        f"PREVIOUS:\n{previous_text}\n\n"
        f"CURRENT:\n{current_text}\n\n"
        f"Checked: {timestamp}\n\n"
        "This is a raw diff -- no interpretation. If this mentions December "
        "Virtual-Q, go check the calendar / set a reminder for any date "
        "and time mentioned above."
    )

    try:
        send_email_notification(
            subject=subject,
            body_text=body,
            screenshot_path=None,
            report_path=None,
            logger=logger,
            high_priority=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to send change notification email: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
