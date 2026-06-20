#!/bin/bash
set -u

LIBRA_DIR="${LIBRA_DIR:-/root/libra}"
KDP_DIR="${KDP_DIR:-/root/kdp}"
QUEUE_FILE="${QUEUE_FILE:-$LIBRA_DIR/queue.txt}"
LOCK_FILE="${LOCK_FILE:-$LIBRA_DIR/.queue.lock}"
LOG="${LOG:-$LIBRA_DIR/queue_log.txt}"
FAILED_FILE="${FAILED_FILE:-$LIBRA_DIR/queue_failed.txt}"
TITLE_LIMIT_STATE="${TITLE_LIMIT_STATE:-$LIBRA_DIR/data/kdp-title-limit.json}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
QUALITY_GATE="${QUALITY_GATE:-$LIBRA_DIR/quality_gate.py}"
KDP_UPLOAD="${KDP_UPLOAD:-$LIBRA_DIR/kdp_upload.py}"
UPLOAD_TIMEOUT="${UPLOAD_TIMEOUT:-25m}"
cd "$LIBRA_DIR"

# Send the book cover to Telegram (used on successful publish, so manual/recovery
# publishes show the cover too — not just the auto-generate QA-pass path).
TG_TOKEN=$(grep TELEGRAM_BOT_TOKEN "$LIBRA_DIR/.env" 2>/dev/null | cut -d= -f2)
TG_CHAT=$(grep TELEGRAM_CHAT_ID "$LIBRA_DIR/.env" 2>/dev/null | cut -d= -f2)
tg_cover() {  # $1=slug
    local cover="$KDP_DIR/$1/cover.jpg"
    [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ] && [ -f "$cover" ] || return 0
    local title
    title=$("$PYTHON_BIN" -c "import json,sys; print(json.load(open(sys.argv[1])).get('title',''))" "$KDP_DIR/$1/listing.json" 2>/dev/null)
    curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendPhoto" \
        -F chat_id="${TG_CHAT}" \
        -F photo=@"$cover" \
        -F caption="🎉 ขึ้นขายบน KDP แล้ว: ${title}" \
        -o /dev/null 2>/dev/null
}

# ── ล็อก: รันได้ทีละตัวเท่านั้น (กันคิวรันซ้อนจนแรมแตก) ──
# ถ้าตัวเก่ายังทำงานอยู่ ตัวใหม่ที่ cron ยิงมาจะเห็นว่าล็อกอยู่ → ข้ามไปเลย
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "[$(date)] คิวกำลังทำงานอยู่ — ข้าม tick นี้ (กันรันซ้อน)" >> "$LOG"
    exit 0
fi

if [ ! -f "$QUEUE_FILE" ]; then
    exit 0
fi

# Respect KDP's temporary account-level new-title cap. Reopening the same form
# cannot succeed before the cap resets and risks duplicate drafts.
if [ -f "$TITLE_LIMIT_STATE" ] && "$PYTHON_BIN" - "$TITLE_LIMIT_STATE" <<'PY'
import json, sys
from datetime import datetime
from pathlib import Path
try:
    state = json.loads(Path(sys.argv[1]).read_text())
    retry_after = datetime.fromisoformat(state.get("retry_after", ""))
    active = state.get("active", False) and datetime.now() < retry_after
except Exception:
    active = False
raise SystemExit(0 if active else 1)
PY
then
    echo "[$(date)] DEFERRED: KDP title creation limit active — queue preserved" >> "$LOG"
    exit 0
fi

SLUG=$(head -n 1 "$QUEUE_FILE")
if [ -z "$SLUG" ]; then
    rm -f "$QUEUE_FILE"
    exit 0
fi

# Copy the corrected EPUB if it exists
if [ -f "$KDP_DIR/$SLUG/audit/corrected/corrected.epub" ]; then
    cp "$KDP_DIR/$SLUG/audit/corrected/corrected.epub" "$KDP_DIR/$SLUG/ebook.epub"
fi

echo "Processing $SLUG from queue at $(date)" >> "$LOG"

# Auto-remove dead reference links (404) before the gate, so a single stale URL
# can't block an otherwise-good book from publishing.
"$PYTHON_BIN" "$LIBRA_DIR/repair_links.py" "$SLUG" >> "$LOG" 2>&1

# Quality and SEO gate is mandatory for both new titles and updates.
if ! "$PYTHON_BIN" "$QUALITY_GATE" "$SLUG" --require-pdf --check-urls --require-editorial >> "$LOG" 2>&1; then
    echo "[$(date)] QUALITY_BLOCKED: $SLUG" >> "$LOG"
    printf '%s\t%s\t%s\n' "$(date -Is)" "$SLUG" "quality_gate_failed" >> "$FAILED_FILE"
    # Keep the book in the queue, but rotate it behind other work.
    if [ "$(wc -l < "$QUEUE_FILE")" -gt 1 ]; then
        sed -i '1d' "$QUEUE_FILE"
        printf '%s\n' "$SLUG" >> "$QUEUE_FILE"
    fi
    "$PYTHON_BIN" - "$SLUG" "$KDP_DIR" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[2]) / sys.argv[1] / "listing.json"
if p.exists():
    data = json.loads(p.read_text())
    data["status"] = "quality_failed"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
PY
    exit 2
fi

MODE=()
if "$PYTHON_BIN" - "$SLUG" "$KDP_DIR" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[2]) / sys.argv[1] / "listing.json"
d = json.loads(p.read_text())
raise SystemExit(0 if d.get("kdp_book_id") or d.get("status") in {"uploaded", "live"} else 1)
PY
then
    MODE=(--update)
fi

# ── Timeout: ถ้าเล่มไหนค้างเกิน 25 นาที ส่ง SIGTERM แล้ว SIGKILL ตามใน 30 วิ ──
timeout -k 30s "$UPLOAD_TIMEOUT" "$PYTHON_BIN" "$KDP_UPLOAD" "$SLUG" "${MODE[@]}" >> "$LOG" 2>&1
RC=$?

if [ $RC -eq 42 ]; then
    echo "[$(date)] DEFERRED: KDP title creation limit detected — queue preserved for retry" >> "$LOG"
    exit 0
fi

if [ $RC -eq 0 ]; then
    sed -i '1d' "$QUEUE_FILE"
    rm -f "$TITLE_LIMIT_STATE"
    echo "[$(date)] SUCCESS: $SLUG removed from queue" >> "$LOG"
    tg_cover "$SLUG"
    exit 0
fi

# ── ถ้า timeout (124=หมดเวลา, 137=ถูก kill) → เก็บกวาด browser ของ libra ที่ค้าง ──
# (เจาะจง path ของ libra เท่านั้น ไม่แตะ playwright ของ tiktok-trends)
if [ $RC -eq 124 ] || [ $RC -eq 137 ]; then
    echo "[$(date)] TIMEOUT: $SLUG เกิน 25 นาที — ฆ่าทิ้งแล้วไปเล่มถัดไป (watchdog จะ reset สถานะให้)" >> "$LOG"
    pkill -9 -f "/usr/local/lib/python3.12/dist-packages/playwright/driver" 2>/dev/null
fi

echo "[$(date)] RETAINED: $SLUG remains first in queue (exit=$RC)" >> "$LOG"
exit "$RC"
