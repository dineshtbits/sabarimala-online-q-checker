#!/usr/bin/env python3
"""
Standalone SMTP smoke test for sabarimala_monitor.py's send_email_notification().

No browser, no portal login -- just verifies Gmail SMTP auth and delivery.

Usage:
    export SMTP_USER="you@gmail.com"
    export SMTP_PASS="your-gmail-app-password"
    export MAIL_TO="you@gmail.com"
    python3 test_email.py
"""

import sys
from datetime import datetime

from sabarimala_monitor import LOG_DIR, send_email_notification, setup_logging


def main() -> int:
    logger = setup_logging()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        send_email_notification(
            subject="[Sabarimala Monitor] SMTP test",
            body_text=(
                f"This is a test email from test_email.py.\n\n"
                f"Sent: {timestamp}\n\n"
                f"If you received this, SMTP_USER/SMTP_PASS/MAIL_TO are wired up correctly."
            ),
            screenshot_path=None,
            report_path=None,
            logger=logger,
            high_priority=False,
        )
        print("EMAIL TEST: SUCCESS")
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.error("EMAIL TEST: FAILED -> %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
