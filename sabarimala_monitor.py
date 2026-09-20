#!/usr/bin/env python3
"""
Sabarimala Virtual-Q Calendar Monitor
======================================
Logs into the Sabarimala Online Portal, opens the Virtual-Q booking calendar,
inspects December 2nd, 3rd, and 4th for open/available slots, and emails a
status report to a Gmail address. Detects disabled -> enabled transitions on
any of those three days and sends a high-priority alert the moment one
opens up.

--------------------------------------------------------------------------
INSTALL
--------------------------------------------------------------------------
    pip install playwright
    playwright install chromium

--------------------------------------------------------------------------
ENVIRONMENT VARIABLES (set these before running -- never hardcode secrets)
--------------------------------------------------------------------------
    SABARIMALA_USERNAME   Portal login username / mobile / email
    SABARIMALA_PASSWORD   Portal login password

    SMTP_USER              The Gmail address the report is sent FROM
    SMTP_PASS              A Gmail "App Password" (NOT your normal password)
    MAIL_TO                Where the report/alert should be sent TO --
                            comma-separated for multiple recipients
                            (defaults to SMTP_USER if unset)

    TARGET_YEAR            Optional, defaults to current year
    TARGET_DAYS            Optional, comma-separated days to watch in
                            TARGET_MONTH_NAME, defaults to "2,3,4"
    HEADLESS                Optional, "true"/"false", defaults to "true"

    Example (zsh), add to ~/.zshrc or a local .env you `source`:
        export SABARIMALA_USERNAME="your_username"
        export SABARIMALA_PASSWORD="your_password"
        export SMTP_USER="you@gmail.com"
        export SMTP_PASS="your-gmail-app-password"
        export MAIL_TO="you@gmail.com"

--------------------------------------------------------------------------
HOW TO CREATE A GOOGLE "APP PASSWORD" (required for SMTP with Gmail)
--------------------------------------------------------------------------
    1. Enable 2-Step Verification on your Google account:
       https://myaccount.google.com/security
    2. Go to https://myaccount.google.com/apppasswords
    3. Create a new app password (name it e.g. "sabarimala-monitor")
    4. Google shows a 16-character password ONCE -- copy it into
       GMAIL_APP_PASSWORD above. Your normal Gmail password will NOT work
       for SMTP once 2FA is enabled.

--------------------------------------------------------------------------
SITE-SPECIFIC SELECTORS
--------------------------------------------------------------------------
Verified live against sabarimalaonline.org: login form fields are already
on the landing page (id=email / id=password, submit button id=regi_continue
which Angular enables once both fields validate); post-login you land on
#/home and click the "Virtual-Q" tile to reach #/darshan; its "Select Date"
field opens a genuine Angular Material datepicker (.mat-calendar), whose day
cells carry an authoritative aria-disabled="true|false" and a parseable
aria-label date (e.g. "Wed Dec 02 2026") -- no class-name guessing needed.
If the site's markup changes, all selectors are centralized in the CONFIG
section below.
"""

import json
import logging
import os
import random
import smtplib
import sys
import time
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from playwright.sync_api import (
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

# =============================================================================
# CONFIG -- edit selectors here after inspecting the live dashboard
# =============================================================================

BASE_URL = "https://sabarimalaonline.org"

LOG_DIR = Path("./logs")
STATE_FILE = LOG_DIR / "last_state.json"

TARGET_MONTH_NAME = "December"
TARGET_DAYS = [
    int(d.strip()) for d in os.environ.get("TARGET_DAYS", "2,3,4").split(",") if d.strip()
]
TARGET_YEAR = os.environ.get("TARGET_YEAR", str(datetime.now().year))

HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() != "false"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
VIEWPORT = {"width": 1440, "height": 900}

# --- Login flow -------------------------------------------------------------
# The landing page is an Angular SPA that auto-routes to #/login and renders
# the login form directly -- there is no separate "click Login to reveal the
# form" step. The submit button (id="regi_continue") starts disabled and is
# enabled by Angular once both fields pass validation, so we fill first and
# then click it -- Playwright's click() already waits for it to become
# enabled.
SELECTOR_USERNAME_FIELD = "input#email"
SELECTOR_PASSWORD_FIELD = "input#password"
SELECTOR_SUBMIT_BUTTON = "#regi_continue"
# Something only present once the dashboard has actually loaded. Confirmed
# against the real site: after login you land on #/home with a "Dashboard"
# heading and tile cards (Virtual-Q, Kanikka, Pilgrim Guide, Bus Booking).
SELECTOR_DASHBOARD_READY = "text=Dashboard"

# --- Virtual-Q / calendar navigation ----------------------------------------
# Confirmed against the real site: the dashboard tile's exact visible text is
# "Virtual-Q" and it routes to #/darshan, a booking form with a "Select Date"
# field. That field is backed by a genuine Angular Material datepicker
# (<mat-datepicker-toggle> / .mat-calendar), not a custom widget -- so these
# selectors are Material's own standard DOM, not a guess.
VIRTUAL_Q_TILE_TEXT = "Virtual-Q"
# The <mat-datepicker-toggle> wrapper itself reports as not-visible to
# Playwright even though its inner <button> renders fine, so target the
# button directly and click with force=True.
SELECTOR_DATE_FIELD_TOGGLE = "button[aria-label='Open calendar']"
SELECTOR_CALENDAR_CONTAINER = ".mat-calendar"
SELECTOR_CALENDAR_NEXT_BUTTON = "button.mat-calendar-next-button"
# Each cell's aria-label is a full parseable date, e.g. "Wed Sep 02 2026",
# and aria-disabled is an authoritative true/false -- no class-name
# heuristics needed.
SELECTOR_CALENDAR_DAY_CELLS = "td.mat-calendar-body-cell"
CALENDAR_CELL_DATE_FORMAT = "%a %b %d %Y"

MAX_MONTH_ADVANCE_CLICKS = 12


# =============================================================================
# LOGGING
# =============================================================================

def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"run_{timestamp}.log"

    logger = logging.getLogger("sabarimala_monitor")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    logger.info("Logging initialized -> %s", log_path)
    return logger


def human_delay(min_ms: int = 150, max_ms: int = 550) -> None:
    """Sleep for a randomized interval to avoid robotic, uniform timing."""
    time.sleep(random.uniform(min_ms, max_ms) / 1000.0)


def type_like_human(page: Page, selector: str, text: str, logger: logging.Logger) -> None:
    field = page.locator(selector).first
    field.click()
    for char in text:
        field.type(char, delay=random.uniform(60, 180))
    logger.debug("Typed into %s", selector)


# =============================================================================
# STATE (for disabled -> enabled transition detection across runs)
# =============================================================================

def load_previous_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_current_state(state: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# =============================================================================
# LOGIN
# =============================================================================

def login(page: Page, logger: logging.Logger) -> None:
    username = os.environ.get("SABARIMALA_USERNAME")
    password = os.environ.get("SABARIMALA_PASSWORD")
    if not username or not password:
        raise RuntimeError(
            "SABARIMALA_USERNAME / SABARIMALA_PASSWORD environment variables "
            "are not set. See the module docstring for setup instructions."
        )

    logger.info("Navigating to landing page: %s", BASE_URL)
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
    human_delay()

    logger.info("Waiting for login form fields to become visible")
    page.locator(SELECTOR_USERNAME_FIELD).first.wait_for(state="visible", timeout=15_000)
    page.locator(SELECTOR_PASSWORD_FIELD).first.wait_for(state="visible", timeout=15_000)

    logger.info("Entering credentials with human-like typing delays")
    type_like_human(page, SELECTOR_USERNAME_FIELD, username, logger)
    human_delay()
    type_like_human(page, SELECTOR_PASSWORD_FIELD, password, logger)
    human_delay(200, 700)

    logger.info("Submitting login form")
    page.locator(SELECTOR_SUBMIT_BUTTON).first.click(timeout=15_000)

    logger.info("Waiting for dashboard to confirm successful login")
    page.locator(SELECTOR_DASHBOARD_READY).first.wait_for(state="visible", timeout=30_000)
    logger.info("Login successful, dashboard loaded")


# =============================================================================
# CALENDAR
# =============================================================================

def open_virtual_q_calendar(page: Page, logger: logging.Logger) -> None:
    """Click the 'Virtual-Q' dashboard tile (routes to #/darshan), then open
    the Select Date field's Material datepicker overlay."""
    logger.info("Navigating to Virtual-Q booking section")
    page.get_by_text(VIRTUAL_Q_TILE_TEXT, exact=True).first.click(timeout=20_000)

    # The booking form (#/darshan) renders its fields, including the date
    # toggle's click handler, asynchronously after route change -- clicking
    # the toggle immediately can hit it before Angular has wired it up.
    page.locator(SELECTOR_DATE_FIELD_TOGGLE).first.wait_for(state="visible", timeout=20_000)
    human_delay(800, 1500)

    logger.info("Opening the Select Date calendar popup")
    # The <mat-datepicker-toggle> wrapper element itself fails Playwright's
    # visibility check (zero-size custom element box) even though its inner
    # <button> is genuinely visible and clickable -- force=True bypasses that
    # check rather than the underlying element being hidden.
    page.locator(SELECTOR_DATE_FIELD_TOGGLE).first.click(timeout=15_000, force=True)
    page.locator(SELECTOR_CALENDAR_CONTAINER).first.wait_for(state="visible", timeout=15_000)
    logger.info("Calendar widget is visible")


def parse_cell_date(aria_label: str):
    try:
        return datetime.strptime(aria_label, CALENDAR_CELL_DATE_FORMAT).date()
    except (ValueError, TypeError):
        return None


def advance_to_month(page: Page, target_month_name: str, target_year: str, logger: logging.Logger) -> bool:
    """Click the calendar's 'next month' control until its day cells' own
    aria-label dates fall in the target month/year, or give up after
    MAX_MONTH_ADVANCE_CLICKS attempts."""
    for attempt in range(1, MAX_MONTH_ADVANCE_CLICKS + 1):
        first_cell = page.locator(SELECTOR_CALENDAR_DAY_CELLS).first
        try:
            aria_label = first_cell.get_attribute("aria-label", timeout=5_000)
        except PlaywrightTimeoutError:
            aria_label = None

        cell_date = parse_cell_date(aria_label) if aria_label else None
        logger.debug("Calendar currently showing cell date: %r", cell_date)

        if cell_date and cell_date.strftime("%B") == target_month_name and str(cell_date.year) == target_year:
            logger.info("Reached %s %s after %d click(s)", target_month_name, target_year, attempt - 1)
            return True

        page.locator(SELECTOR_CALENDAR_NEXT_BUTTON).first.click(timeout=10_000)
        human_delay(250, 600)

    logger.warning(
        "Could not confirm calendar reached %s %s after %d clicks",
        target_month_name, target_year, MAX_MONTH_ADVANCE_CLICKS,
    )
    return False


def check_calendar(page: Page, logger: logging.Logger) -> dict:
    """Steps the calendar forward to the target month and inspects only the
    TARGET_DAYS cells, returning a structured result dict."""
    result = {
        "month_reached": False,
        "day_statuses": {},  # "2" -> "disabled" | "enabled", only for TARGET_DAYS
    }

    reached = advance_to_month(page, TARGET_MONTH_NAME, TARGET_YEAR, logger)
    result["month_reached"] = reached

    day_cells = page.locator(SELECTOR_CALENDAR_DAY_CELLS)
    count = day_cells.count()
    remaining_days = set(TARGET_DAYS)
    logger.info("Scanning calendar for day(s) %s among %d cell(s)", sorted(remaining_days), count)

    for i in range(count):
        if not remaining_days:
            break
        cell = day_cells.nth(i)
        aria_label = cell.get_attribute("aria-label")
        cell_date = parse_cell_date(aria_label) if aria_label else None
        if cell_date is None:
            continue
        # Cells from an adjacent month can appear as grayed-out fillers;
        # their own aria-label always reflects their real date, so this
        # naturally scopes the scan to the target month.
        if cell_date.strftime("%B") != TARGET_MONTH_NAME or str(cell_date.year) != TARGET_YEAR:
            continue
        if cell_date.day not in remaining_days:
            continue

        aria_disabled = cell.get_attribute("aria-disabled")
        status = "disabled" if (aria_disabled or "").lower() == "true" else "enabled"
        result["day_statuses"][str(cell_date.day)] = status
        remaining_days.discard(cell_date.day)
        logger.info(
            "Day %s -> status=%s (aria-label=%r, aria-disabled=%r)",
            cell_date.day, status, aria_label, aria_disabled,
        )

    for missing_day in sorted(remaining_days):
        logger.warning("Day %d was not found among inspected calendar cells", missing_day)

    return result


# =============================================================================
# EMAIL
# =============================================================================

def send_email_notification(
    subject: str,
    body_text: str,
    screenshot_path: Path | None,
    report_path: Path | None,
    logger: logging.Logger,
    high_priority: bool = False,
) -> None:
    sender = os.environ.get("SMTP_USER")
    # App passwords are often copy-pasted with spaces ("xxxx xxxx xxxx xxxx")
    # or a stray non-breaking space from a secrets UI -- strip both so a
    # cosmetic copy/paste doesn't break auth.
    app_password = (os.environ.get("SMTP_PASS") or "").replace(" ", "").replace("\xa0", "")
    mail_to = os.environ.get("MAIL_TO", sender)

    if not sender or not app_password or not mail_to:
        raise RuntimeError(
            "SMTP_USER / SMTP_PASS / MAIL_TO environment variables are not "
            "fully set. See the module docstring for setup instructions "
            "(Google App Password required)."
        )

    recipients = [r.strip() for r in mail_to.split(",") if r.strip()]

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    if high_priority:
        msg["X-Priority"] = "1"
        msg["X-MSMail-Priority"] = "High"
        msg["Importance"] = "High"

    msg.attach(MIMEText(body_text, "plain"))

    for path in (screenshot_path, report_path):
        if path and path.exists():
            with open(path, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition", f"attachment; filename={path.name}"
            )
            msg.attach(part)

    logger.info("Connecting to Gmail SMTP (smtp.gmail.com:587)")
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.starttls()
        server.login(sender, app_password)
        server.sendmail(sender, recipients, msg.as_string())
    logger.info("Email sent to %s (high_priority=%s)", recipients, high_priority)


# =============================================================================
# REPORTING
# =============================================================================

def write_status_report(result: dict, newly_open_days: list[str], timestamp: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    report_path = LOG_DIR / f"status_{timestamp}.txt"

    lines = [
        f"Sabarimala Virtual-Q Calendar Status Report",
        f"Generated: {timestamp}",
        f"Target month: {TARGET_MONTH_NAME} {TARGET_YEAR}",
        f"Watched days: {', '.join(str(d) for d in TARGET_DAYS)}",
        "",
        f"Month reached successfully: {result['month_reached']}",
        f"Newly opened since last run: {', '.join(newly_open_days) if newly_open_days else 'none'}",
        "",
        "Watched day statuses:",
    ]
    for day in TARGET_DAYS:
        status = result["day_statuses"].get(str(day), "not found")
        lines.append(f"  {TARGET_MONTH_NAME} {day}: {status}")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def build_email_body(result: dict, newly_open_days: list[str], timestamp: str) -> str:
    if newly_open_days:
        header = f"ALERT: Booking just opened for {TARGET_MONTH_NAME} {', '.join(newly_open_days)}!"
    else:
        header = "Daily status digest"

    status_lines = "\n".join(
        f"  {TARGET_MONTH_NAME} {day}: {result['day_statuses'].get(str(day), 'not found').upper()}"
        for day in TARGET_DAYS
    )

    return (
        f"{header}\n\n"
        f"{status_lines}\n\n"
        f"Calendar reached target month: {result['month_reached']}\n\n"
        f"Run time: {timestamp}\n\n"
        f"Full report and screenshot attached."
    )


# =============================================================================
# MAIN
# =============================================================================

def main() -> int:
    logger = setup_logging()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_path = LOG_DIR / f"screenshot_{timestamp}.png"
    result: dict = {
        "month_reached": False,
        "day_statuses": {},
    }
    fatal_error: str | None = None

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        context = browser.new_context(user_agent=USER_AGENT, viewport=VIEWPORT)
        page = context.new_page()

        try:
            login(page, logger)
        except Exception as exc:  # noqa: BLE001 - top-level milestone guard
            fatal_error = f"Login failed: {exc}"
            logger.error(fatal_error)

        if not fatal_error:
            try:
                open_virtual_q_calendar(page, logger)
                result = check_calendar(page, logger)
            except Exception as exc:  # noqa: BLE001
                fatal_error = f"Calendar check failed: {exc}"
                logger.error(fatal_error)

        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot_path), full_page=True)
            logger.info("Saved screenshot -> %s", screenshot_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to capture screenshot: %s", exc)
            screenshot_path = None  # type: ignore[assignment]

        context.close()
        browser.close()

    previous_state = load_previous_state()
    previous_day_statuses = previous_state.get("day_statuses", {})

    newly_open_days = [
        str(day)
        for day in TARGET_DAYS
        if previous_day_statuses.get(str(day)) == "disabled"
        and result["day_statuses"].get(str(day)) == "enabled"
    ]

    save_current_state(
        {
            "day_statuses": result["day_statuses"],
            "last_run": timestamp,
        }
    )

    report_path = write_status_report(result, newly_open_days, timestamp)

    if fatal_error:
        subject = "[Sabarimala Monitor] Run FAILED"
        body = f"The monitoring run failed before completing.\n\nError: {fatal_error}\n\nSee attached log/screenshot."
        high_priority = True
    elif newly_open_days:
        subject = f"[Sabarimala Monitor] ALERT: {TARGET_MONTH_NAME} {', '.join(newly_open_days)} now OPEN"
        body = build_email_body(result, newly_open_days, timestamp)
        high_priority = True
    else:
        statuses_summary = ", ".join(
            f"{day}={result['day_statuses'].get(str(day), '?')}" for day in TARGET_DAYS
        )
        subject = f"[Sabarimala Monitor] Daily digest - {statuses_summary}"
        body = build_email_body(result, newly_open_days, timestamp)
        high_priority = False

    try:
        send_email_notification(
            subject=subject,
            body_text=body,
            screenshot_path=screenshot_path,
            report_path=report_path,
            logger=logger,
            high_priority=high_priority,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to send notification email: %s", exc)
        return 1

    return 0 if not fatal_error else 1


if __name__ == "__main__":
    sys.exit(main())
