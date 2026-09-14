#!/bin/bash
# Restarts the GitHub Actions runner listener when its connection to GitHub's
# broker gets silently stuck.
#
# Why this exists: the runner's long-poll connection to
# broker.actions.githubusercontent.com can die (SocketException / TaskCanceled
# / "Socket Error: TimedOut") without the process crashing or exiting. It
# stays alive, launchd sees nothing wrong, but it never reconnects on its own
# again -- sometimes for 20+ hours. This is a confirmed, still-open upstream
# bug in actions/runner (see e.g. github.com/actions/runner issues #3904,
# #4668, #4703), not something fixable from this repo. Measured against this
# runner's own logs on 2026-09-14: median gap between runs was 4.2 hours
# against an hourly schedule, with two 20+ hour outliers, all while the
# machine itself never slept (caffeinate confirmed continuously holding).
#
# The fix: if nothing has happened in a while, restart the listener. A fresh
# process means a fresh TCP connection, which reliably clears the stuck
# state -- confirmed by hand on 2026-09-14 (kickstart -> "Connected to
# GitHub" / "Listening for Jobs" within 3 seconds, after 2+ hours stuck).
#
# IMPORTANT: `svc.sh stop && svc.sh start` does NOT reliably restart it in
# this environment. `svc.sh stop` (launchctl unload) leaves the label
# registered but stopped, and the follow-up `svc.sh start` (launchctl load)
# then fails with "Load failed: 5: Input/output error" against an
# already-registered label -- confirmed by hand. `launchctl kickstart -k` is
# the command that actually works against an already-registered LaunchAgent,
# so that's what this script uses.
#
# STALE_MINUTES is deliberately short relative to the historical multi-hour
# gaps: worst case with this watchdog is a job starting ~STALE_MINUTES +
# CHECK_INTERVAL late, not 20 hours late.

set -euo pipefail

LABEL="actions.runner.adrieljavier-Youtube-to-Spotify-Podcasts.MacBook_Pro"
STDOUT_LOG="$HOME/Library/Logs/actions.runner.adrieljavier-Youtube-to-Spotify-Podcasts.MacBook_Pro/stdout.log"
WATCHDOG_LOG="$HOME/actions-runner/_diag/watchdog.log"
STALE_MINUTES=20

log() {
  echo "$(date -u +"%Y-%m-%d %H:%M:%S UTC") $1" >>"$WATCHDOG_LOG"
}

if [ ! -f "$STDOUT_LOG" ]; then
  log "stdout log not found at $STDOUT_LOG -- nothing to check, skipping."
  exit 0
fi

# The runner writes plain lines like:
#   2026-09-14 21:30:17Z: Listening for Jobs
#   2026-09-14 19:28:42Z: Running job: publish
#   2026-09-14 19:31:22Z: Job publish completed with result: Succeeded
# Any of these proves the connection was healthy at that moment.
LAST_ACTIVITY_LINE=$(grep -E "Listening for Jobs|Running job:|Job .* completed" "$STDOUT_LOG" | tail -1 || true)

if [ -z "$LAST_ACTIVITY_LINE" ]; then
  log "No activity line found yet in stdout log -- runner may still be starting up, skipping."
  exit 0
fi

LAST_TS=$(echo "$LAST_ACTIVITY_LINE" | grep -oE "^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}Z")
LAST_EPOCH=$(date -u -j -f "%Y-%m-%d %H:%M:%SZ" "$LAST_TS" +%s 2>/dev/null || echo 0)
NOW_EPOCH=$(date -u +%s)
AGE_MIN=$(( (NOW_EPOCH - LAST_EPOCH) / 60 ))

# Never restart mid-job: a "Running job:" with no later "Job ... completed"
# after it means work is actively in flight. Interrupting it would fail a
# real episode for no reason -- let it finish or time out on its own.
LAST_RUN_LINE=$(grep "Running job:" "$STDOUT_LOG" | tail -1 || true)
LAST_DONE_LINE=$(grep "Job .* completed" "$STDOUT_LOG" | tail -1 || true)
LAST_RUN_TS=$(echo "$LAST_RUN_LINE" | grep -oE "^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}Z" || true)
LAST_DONE_TS=$(echo "$LAST_DONE_LINE" | grep -oE "^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}Z" || true)

if [ -n "$LAST_RUN_TS" ] && { [ -z "$LAST_DONE_TS" ] || [ "$LAST_RUN_TS" \> "$LAST_DONE_TS" ]; }; then
  log "A job looks like it's actively running (started $LAST_RUN_TS, no completion after it) -- skipping, even though last activity was ${AGE_MIN}min ago."
  exit 0
fi

if [ "$AGE_MIN" -lt "$STALE_MINUTES" ]; then
  exit 0  # healthy, nothing to log -- keep the log file to real events only
fi

log "Stale: last confirmed activity was ${AGE_MIN}min ago (>${STALE_MINUTES}min threshold, last line: '$LAST_ACTIVITY_LINE'). Restarting listener."
if launchctl kickstart -k "gui/$(id -u)/$LABEL" >>"$WATCHDOG_LOG" 2>&1; then
  log "kickstart succeeded."
else
  log "kickstart FAILED (exit $?) -- runner may need a human to look at it."
fi
