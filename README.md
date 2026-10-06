# Sabarimala Virtual-Q calendar monitor

Logs into sabarimalaonline.org and checks whether December 1/2/3 are open
for Virtual-Q booking, emailing a status report.

**Current setup**: `sabarimala-monitor.yml` runs the full login+calendar
flow **hourly** on a self-hosted GitHub Actions runner, using a
secondary/throwaway portal account (not a real one) specifically so that
frequency is an acceptable risk. It only emails when something's actually
worth seeing — see "Email gating" below — not on all 24 hourly runs.

`monitors/marquee_monitor.py` separately polls the portal's public notice
banner (no login) once daily; see its own section below.

## Files
- `monitors/sabarimala_monitor.py` — the full pipeline: login, calendar
  check, email gating. Also where all shared selectors/helpers live.
- `monitors/marquee_monitor.py` — lightweight, login-free poll of the
  portal's notice banner; emails only if the text changes. **Known limitation:**
  confirmed (Oct 2026) that the site can release new booking dates without
  updating this banner at all, so it's a supplementary signal only, not a
  substitute for the real calendar check.
- `tests/test_login.py` / `tests/test_calendar.py` / `tests/test_email.py`
  — isolate one stage each, for debugging without running (or emailing
  from) the full pipeline. Run from the repo root, e.g.
  `python3 tests/test_login.py`.

## Email gating
`monitors/sabarimala_monitor.py` runs every hour at the top of the IST hour
and sends an email only when:
- Any watched day is **currently** open (every hour it stays open), or
- It's the 8 AM IST run (`DIGEST_HOUR_IST` in the CONFIG section) — the
  daily status digest, sent whether or not anything is open.

Run failures are **not** emailed at other hours; they show up in the 8 AM
digest and in the Actions run history.

## Run the one-shot pipeline locally
```
pip install playwright
playwright install chromium
export SABARIMALA_USERNAME="your_portal_username"
export SABARIMALA_PASSWORD="your_portal_password"
export SMTP_USER="you@gmail.com"
export SMTP_PASS="your-gmail-app-password"   # NOT your login password
export MAIL_TO="you@gmail.com"
python3 monitors/sabarimala_monitor.py
```
Watch it run with `HEADLESS=false python3 monitors/sabarimala_monitor.py`.

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
`sabarimala-monitor.yml` runs hourly (`:30` UTC = top of the IST hour); `marquee-monitor.yml` runs once daily
at 8:00 AM IST. Either can be run on-demand via Actions → (workflow name)
→ Run workflow. They need a **self-hosted** runner — GitHub-hosted runners
get blocked by the portal's bot detection before even reaching the login
page (confirmed, both for the full login flow and the login-free marquee
check).

### Self-hosted runner notes (macOS 13 / Ventura specifically)
If your runner machine is stuck on an old macOS version, check
`.github/workflows/*.yml` for a Playwright version pin — Chromium support
for macOS 13 was removed in Playwright 1.62.0, so those workflows pin
`playwright==1.61.0`, the last version that still ships a mac13 Chromium
build. Bump this only after the runner machine is upgraded/replaced.

## How state persists between runs
`logs/last_state.json` (calendar check) and `logs/last_marquee.json`
(banner poll) track last-seen status. The scheduled workflows commit these
back to the repo after each run (`[skip ci]`), so the diff history doubles
as an audit log of exactly when something changed.

## Notes
- This repo is public: the code is visible to anyone, but no credentials
  live in it — everything sensitive is a GitHub Actions secret.
- Selectors in `monitors/sabarimala_monitor.py` target the site's real DOM (an
  Angular Material datepicker), verified against the live site. If the site
  changes its markup, the `SELECTOR_*` constants are centralized near the
  top of the file.
