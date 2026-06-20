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
CREATED_DATE="2026-06-08"
MAX_DAYS=14
cd /root/libra || exit 1

TG_TOKEN=$(grep TELEGRAM_BOT_TOKEN /root/libra/.env | cut -d= -f2)
TG_CHAT=$(grep TELEGRAM_CHAT_ID /root/libra/.env | cut -d= -f2)

tg_notify() {
  curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
    -H "Content-Type: application/json" \
    -d "{\"chat_id\": \"${TG_CHAT}\", \"text\": \"$1\", \"parse_mode\": \"HTML\"}" > /dev/null 2>&1
}

remove_cron() {
  crontab -l 2>/dev/null | grep -v "retry_jp_reupload.sh" | crontab -
}

echo "=== $(date -u) attempt ===" >> "$LOG"

# Single-flight: don't stack browsers if a previous attempt is still running.
exec 9>"$DIR/.jp_reupload.lock"
flock -n 9 || { echo "already running, skip" >> "$LOG"; exit 0; }

# --- Give-up check: if created more than MAX_DAYS ago, stop retrying ---
DAYS_ELAPSED=$(( ( $(date -d "$(date +%Y-%m-%d)" +%s) - $(date -d "$CREATED_DATE" +%s) ) / 86400 ))
if [ "$DAYS_ELAPSED" -ge "$MAX_DAYS" ]; then
    echo "GIVE UP: ${DAYS_ELAPSED} days elapsed (max ${MAX_DAYS}). Book may be permanently stuck." >> "$LOG"
    tg_notify "⚠️ <b>Libra JP Book ค้างนาน ${DAYS_ELAPSED} วัน</b>%0A%0Aหนังสือ <b>副業を始めたい人のための予算管理ワークブック</b> ยังไม่ผ่าน Amazon review%0A%0AKดp Book ID: A2R6SL4SX9L4KI%0A%0Aกรุณาตรวจสอบที่ KDP Dashboard ว่าสถานะเป็นอะไร%0A(อาจถูก Reject หรือต้องดำเนินการเพิ่มเติม)%0A%0Aหยุดรีทรายอัตโนมัติแล้ว — ถ้าต้องการรีทรายต่อให้แจ้ง"
    remove_cron
    exit 0
fi

timeout -k 30s 25m python3 kdp_upload.py "$SLUG" --update >> "$LOG" 2>&1
UPDATE_EXIT=$?

if [ $UPDATE_EXIT -eq 0 ]; then
    echo "content update OK, uploading cover" >> "$LOG"
    timeout -k 30s 25m python3 kdp_upload.py "$SLUG" --cover >> "$LOG" 2>&1
    COVER_EXIT=$?
    if [ $COVER_EXIT -eq 0 ]; then
        tg_notify "✅ <b>Libra JP Book อัปเดตสำเร็จ!</b>%0A%0Aหนังสือ <b>副業を始めたい人のための予算管理ワークブック</b>%0AContent + Cover อัปโหลดใหม่เรียบร้อย (fixed version)"
    else
        tg_notify "⚠️ <b>Libra JP Book: content OK แต่ cover ล้มเหลว</b>%0ABook slug: ${SLUG}"
    fi
    echo "DONE $(date -u)" >> "$LOG"
    touch "$DIR/.jp_reupload_done"
    remove_cron
else
    # Check if still in review or a harder failure
    LAST_LINE=$(tail -1 "$LOG")
    if echo "$LAST_LINE" | grep -q "still in review"; then
        echo "still in review (day ${DAYS_ELAPSED}/${MAX_DAYS}) — will retry next tick" >> "$LOG"
    else
        echo "update failed with unexpected error (day ${DAYS_ELAPSED}/${MAX_DAYS})" >> "$LOG"
        # Notify on unexpected failures (not "in review")
        if ! grep -q "Redirected to bookshelf" "$LOG" 2>/dev/null || [ "$DAYS_ELAPSED" -ge 10 ]; then
            tg_notify "⚠️ <b>Libra JP Retry: ล้มเหลว (วันที่ ${DAYS_ELAPSED})</b>%0A%0Aหนังสือ <b>副業を始めたい人のための予算管理ワークブック</b>%0Aอาจถูก Amazon Reject หรือมีปัญหาอื่น%0ABook ID: A2R6SL4SX9L4KI%0A%0Aตรวจสอบที่ KDP Dashboard"
        fi
    fi
fi
