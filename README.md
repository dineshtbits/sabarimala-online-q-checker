# Sabarimala Virtual-Q calendar monitor

Logs into sabarimalaonline.org, opens the Virtual-Q booking calendar, checks
whether December 2/3/4 are open for booking, and emails a status report.
Sends a high-priority alert the moment any of those days flips from
disabled to enabled.

## Files
- `sabarimala_monitor.py` — the full pipeline: login, calendar check, email.
- `test_login.py` — login only, no calendar/email. Good for debugging auth.
- `test_calendar.py` — login + calendar check, no email required.
- `test_email.py` — email only, no browser/login required.
- `.github/workflows/sabarimala-monitor.yml` — runs it twice daily on GitHub Actions.

## Run it locally
```
pip install playwright
playwright install chromium
export SABARIMALA_USERNAME="your_portal_username"
export SABARIMALA_PASSWORD="your_portal_password"
export SMTP_USER="you@gmail.com"
export SMTP_PASS="your-gmail-app-password"   # NOT your login password
export MAIL_TO="you@gmail.com"
python3 sabarimala_monitor.py
```
Watch it run with `HEADLESS=false python3 sabarimala_monitor.py`.

## Gmail app password
Google Account → Security → 2-Step Verification → App passwords. Use that
16-char password as `SMTP_PASS`.

## GitHub Actions setup
```
gh secret set SABARIMALA_USERNAME
gh secret set SABARIMALA_PASSWORD
gh secret set SMTP_USER
gh secret set SMTP_PASS
gh secret set MAIL_TO
```
Each prompts for the value interactively so it never touches shell history.
The workflow runs at 8:00 AM and 8:00 PM IST daily, or on-demand via
Actions → Sabarimala Virtual-Q Monitor → Run workflow.

## How state persists between runs
`logs/last_state.json` tracks the last-seen status per watched day. The
workflow commits it back to the repo after each run (`[skip ci]`, so it
doesn't retrigger anything) — that diff history doubles as an audit log of
exactly when a day's status changed.

## Notes
- This repo is public: the code is visible to anyone, but no credentials
  live in it — everything sensitive is a GitHub Actions secret.
- Selectors in `sabarimala_monitor.py` target the site's real DOM (an
  Angular Material datepicker), verified against the live site. If the site
  changes its markup, the `SELECTOR_*` constants are centralized near the
  top of the file.
