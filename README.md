# Sabarimala Virtual-Q calendar monitor

Logs into sabarimalaonline.org and checks whether December 2/3/4 are open
for Virtual-Q booking, emailing a status report. Sends a high-priority
alert the moment any of those days flips from disabled to enabled.

There are two ways this runs, covering different needs:

1. **Scheduled GitHub Actions workflows** (`sabarimala-monitor.yml`,
   `marquee-monitor.yml`) — once-daily checks, guaranteed to run
   regardless of this script's own health, on a self-hosted runner.
2. **`continuous_monitor.py`** — a long-running daemon that logs in
   **once** and then loops forever (reload + re-check every 15 min)
   instead of logging in fresh every check, to minimize how often the
   portal sees an automated login. Runs as its own background process,
   not through Actions (see below for why).

## Files
- `sabarimala_monitor.py` — the full pipeline: login, calendar check, email.
  Also where all shared selectors/helpers live.
- `continuous_monitor.py` — the self-updating daemon (one login, infinite
  reload-and-check loop). See its module docstring for the full design.
- `run_daemon.sh` — wrapper launchd keeps alive; pulls latest code, compile
  -checks it, falls back to the last known-good commit if it's broken, then
  runs `continuous_monitor.py`. This is what makes the daemon self-updating.
- `com.dineshtbits.sabarimala-daemon.plist` — launchd LaunchAgent template
  for running `run_daemon.sh` persistently (survives reboots, no logged-in
  terminal needed). Edit the `/Users/CHANGEME/...` paths before installing.
- `marquee_monitor.py` — lightweight, login-free poll of the portal's
  notice banner; emails only if the text changes. **Known limitation:**
  confirmed (Oct 2026) that the site can release new booking dates without
  updating this banner at all, so it's a supplementary signal only, not a
  substitute for the real calendar check.
- `test_login.py` / `test_calendar.py` / `test_email.py` — isolate one
  stage each, for debugging without running (or emailing from) the full
  pipeline.
- `test_session_persistence.py` — diagnostic probe (not used in
  production) that proved session-replay via a fresh browser context
  doesn't work against this site — the app detects it and wipes its own
  auth state. This is *why* `continuous_monitor.py` keeps one browser
  session alive for its whole run instead of trying to save/restore login
  state between separate process runs.

## Run the one-shot pipeline locally
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
Both workflows currently run once daily at 8:00 AM IST, or on-demand via
Actions → (workflow name) → Run workflow. They need a **self-hosted**
runner — GitHub-hosted runners get blocked by the portal's bot detection
before even reaching the login page (confirmed).

### Self-hosted runner notes (macOS 13 / Ventura specifically)
If your runner machine is stuck on an old macOS version, check
`.github/workflows/*.yml` for a Playwright version pin — Chromium support
for macOS 13 was removed in Playwright 1.62.0, so those workflows pin
`playwright==1.61.0`, the last version that still ships a mac13 Chromium
build. Bump this only after the runner machine is upgraded/replaced.

## Setting up the continuous daemon
The daemon needs its **own separate git clone** — not the Actions
runner's working directory, which gets wiped and freshly checked out by
every scheduled workflow run and would race with the daemon's own git
operations.

```bash
# 1. A dedicated clone, separate from the Actions runner's checkout
git clone https://github.com/dineshtbits/sabarimala-online-q-checker.git ~/sabarimala-daemon
cd ~/sabarimala-daemon
pip3 install "playwright==1.61.0"   # match whatever your runner's workflows use
playwright install chromium

# 2. Credentials -- launchd does NOT source ~/.zshrc, so these need their
#    own file, which is never committed (lives outside the repo anyway):
cat > ~/.sabarimala_daemon_env << 'EOF'
export SABARIMALA_USERNAME="..."
export SABARIMALA_PASSWORD="..."
export SMTP_USER="..."
export SMTP_PASS="..."
export MAIL_TO="..."
export HEADLESS=true
EOF
chmod 600 ~/.sabarimala_daemon_env

# 3. Edit the plist's /Users/CHANGEME/... paths to match ~/sabarimala-daemon,
#    then install it:
cp com.dineshtbits.sabarimala-daemon.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.dineshtbits.sabarimala-daemon.plist

# Check it's alive:
launchctl list | grep sabarimala-daemon
tail -f ~/sabarimala-daemon/logs/daemon_stdout.log
```

**To update the daemon**: just `git push` to `main` from anywhere. The
daemon checks for new commits once per cycle (≤15 min) and restarts
itself into the new code automatically — no access to the machine needed.
If a push breaks compilation, the wrapper reverts to the last known-good
commit instead of crash-looping on broken code.

## How state persists between runs
`logs/last_state.json` (calendar check) and `logs/last_marquee.json`
(banner poll) track last-seen status. The scheduled workflows commit these
back to the repo after each run (`[skip ci]`), so the diff history doubles
as an audit log of exactly when something changed. The daemon's own state
(`logs/.daemon_state.json`) is local-only and not committed — it restarts
rarely enough that this doesn't need the same treatment.

## Notes
- This repo is public: the code is visible to anyone, but no credentials
  live in it — everything sensitive is a GitHub Actions secret or, for the
  daemon, a local file outside the repo (`~/.sabarimala_daemon_env`).
- Selectors in `sabarimala_monitor.py` target the site's real DOM (an
  Angular Material datepicker), verified against the live site. If the site
  changes its markup, the `SELECTOR_*` constants are centralized near the
  top of the file.
