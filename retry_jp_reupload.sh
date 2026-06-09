#!/bin/bash
# Retry the side-hustle-budget-jp re-upload until Amazon lets us edit it.
# The book is "In Review"; KDP redirects edits to the bookshelf until review
# clears. This pushes the FIXED content (no garbled language, no repeated
# chapters) and the FIXED cover, then removes itself from cron. Installed by
# cron 0 */3 * * * after the JP regeneration (2026-06-09).
set -u
SLUG="side-hustle-budget-jp"
DIR="/root/kdp/$SLUG"
LOG="$DIR/retry_jp_reupload.log"
cd /root/libra || exit 1

echo "=== $(date -u) attempt ===" >> "$LOG"

# Single-flight: don't stack browsers if a previous attempt is still running.
exec 9>"$DIR/.jp_reupload.lock"
flock -n 9 || { echo "already running, skip" >> "$LOG"; exit 0; }

timeout -k 30s 25m python3 kdp_upload.py "$SLUG" --update >> "$LOG" 2>&1
if [ $? -eq 0 ]; then
    echo "content update OK, uploading cover" >> "$LOG"
    timeout -k 30s 25m python3 kdp_upload.py "$SLUG" --cover >> "$LOG" 2>&1
    echo "DONE $(date -u)" >> "$LOG"
    touch "$DIR/.jp_reupload_done"
    # Remove self from crontab (running under real cron, crontab is writable).
    crontab -l 2>/dev/null | grep -v "retry_jp_reupload.sh" | crontab -
else
    echo "still in review or failed — will retry next tick" >> "$LOG"
fi
