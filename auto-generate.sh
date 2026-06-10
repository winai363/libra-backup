#!/bin/bash
# KDP Auto-Generate — runs every day at 10:00 ICT (03:00 UTC)
# Creates 1 quality-gated book per run
# Primary: GPT-4.1 (Claude path disabled)

export PATH="/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export HOME="/root"

LOG_DIR="/root/kdp/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y-%m-%d).log"

echo "=== KDP Auto-Generate $(date) — 1 quality-gated book per run ===" >> "$LOG_FILE"

BOOKS_PER_RUN=1
TOKEN=$(grep SESSION_TOKEN /root/libra/.env | cut -d= -f2)
TG_TOKEN=$(grep TELEGRAM_BOT_TOKEN /root/libra/.env | cut -d= -f2)
TG_CHAT=$(grep TELEGRAM_CHAT_ID /root/libra/.env | cut -d= -f2)

tg_notify() {
  curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
    -H "Content-Type: application/json" \
    -d "{\"chat_id\": \"${TG_CHAT}\", \"text\": \"$1\", \"parse_mode\": \"HTML\"}" > /dev/null 2>&1
}

# Send the actual cover image so the user can eyeball the new cover on Telegram.
# $1 = image path, $2 = caption (HTML). No-op if the file is missing.
tg_photo() {
  [ -f "$1" ] || return 0
  curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendPhoto" \
    -F "chat_id=${TG_CHAT}" \
    -F "photo=@$1" \
    -F "caption=$2" \
    -F "parse_mode=HTML" > /dev/null 2>&1
}

tg_notify "🚀 <b>Libra 2.0 SmartPublisher: เริ่มสร้างหนังสือ</b>\n$(date '+%Y-%m-%d %H:%M') ICT"
TODAY=$(date +%Y-%m-%d)

for BOOK_NUM in $(seq 1 $BOOKS_PER_RUN); do
  echo "" >> "$LOG_FILE"
  echo ">>> Book $BOOK_NUM of $BOOKS_PER_RUN ===" >> "$LOG_FILE"

  # --- Phase 0: Market-driven topic discovery ---
  TOPIC_FILE="/tmp/libra-topic-$(date +%Y%m%d-%H%M%S).json"
  CANDIDATES_FILE="/root/kdp/logs/topic-candidates-$(date +%Y%m%d).json"
  echo ">>> [topic_scout] Discovering best topic..." >> "$LOG_FILE"
  mkdir -p /root/kdp/logs

  python3 /root/libra/topic_scout.py \
    --save "$TOPIC_FILE" \
    --candidates "$CANDIDATES_FILE" >> "$LOG_FILE" 2>&1
  SCOUT_EXIT=$?

  if [ $SCOUT_EXIT -ne 0 ] || [ ! -f "$TOPIC_FILE" ]; then
    echo ">>> [topic_scout] Discovery failed (exit=$SCOUT_EXIT) — falling back to writer's built-in research" >> "$LOG_FILE"
    TOPIC_ARG=""
  else
    TOPIC_TITLE=$(python3 -c "import json; d=json.load(open('$TOPIC_FILE')); print(d.get('title','?'))" 2>/dev/null)
    TOPIC_LANG=$(python3 -c "import json; d=json.load(open('$TOPIC_FILE')); print(d.get('language','?'))" 2>/dev/null)
    echo ">>> [topic_scout] Best topic: $TOPIC_TITLE ($TOPIC_LANG)" >> "$LOG_FILE"
    tg_notify "🔍 <b>Libra: พบ topic น่าสนใจ</b>\n${TOPIC_TITLE} (${TOPIC_LANG})\nกำลังเขียน..."
    TOPIC_ARG="--topic-file $TOPIC_FILE"
  fi

  # Count books before (exclude logs/ directory)
  BOOKS_BEFORE=$(ls -d /root/kdp/*/ 2>/dev/null | grep -v '/logs/$' | wc -l)

  # --- GPT-4.1 (primary) ---
  echo ">>> Running GPT-4.1 (book $BOOK_NUM)..." >> "$LOG_FILE"
  python3 /root/libra/gpt_fallback_writer.py $TOPIC_ARG >> "$LOG_FILE" 2>&1
  GPT_EXIT=$?
  if [ $GPT_EXIT -ne 0 ]; then
    echo ">>> GPT-4.1 failed (exit=$GPT_EXIT) for book $BOOK_NUM — retrying once..." >> "$LOG_FILE"
    sleep 10
    python3 /root/libra/gpt_fallback_writer.py >> "$LOG_FILE" 2>&1
    GPT_EXIT=$?
    if [ $GPT_EXIT -ne 0 ]; then
      echo ">>> GPT-4.1 retry also failed (exit=$GPT_EXIT)" >> "$LOG_FILE"
      tg_notify "❌ <b>Libra 2.0 SmartPublisher: เขียนหนังสือล้มเหลว 2 ครั้ง</b>\nวันที่ $(date '+%Y-%m-%d') — ข้ามไปพรุ่งนี้"
    fi
  fi

  # If a new book passed quality today, generate its PDF and add it to the queue.
  LATEST_BOOK=$(ls -dt /root/kdp/*/ 2>/dev/null | grep -v '/logs/$' | head -1 | xargs -I {} basename {})
  if [ ! -z "$LATEST_BOOK" ]; then
    LISTING_FILE="/root/kdp/$LATEST_BOOK/listing.json"
    if [ -f "$LISTING_FILE" ]; then
      CREATED_DATE=$(python3 -c "import json; d=json.load(open('$LISTING_FILE')); print(d.get('created_at','')[:10])" 2>/dev/null)
      BOOK_STATUS=$(python3 -c "import json; d=json.load(open('$LISTING_FILE')); print(d.get('status',''))" 2>/dev/null)
      if [[ "$CREATED_DATE" == "$TODAY" && "$BOOK_STATUS" == "ready" ]]; then
        echo "=== Building paperback and validating: $LATEST_BOOK ===" >> "$LOG_FILE"
        # Auto-remove dead reference links (404) before validating, so a single
        # stale URL can't block the whole book from publishing.
        python3 /root/libra/repair_links.py "$LATEST_BOOK" >> "$LOG_FILE" 2>&1
        curl -fsS -X POST "http://127.0.0.1:8200/api/books/$LATEST_BOOK/generate-pdf" \
          -H "Cookie: libra_token=$TOKEN" >> "$LOG_FILE" 2>&1
        PDF_EXIT=$?
        python3 /root/libra/quality_gate.py "$LATEST_BOOK" --require-pdf --check-urls --require-editorial >> "$LOG_FILE" 2>&1
        QA_EXIT=$?
        if [ $PDF_EXIT -eq 0 ] && [ $QA_EXIT -eq 0 ]; then
          touch /root/libra/queue.txt
          if ! grep -Fxq "$LATEST_BOOK" /root/libra/queue.txt; then
            echo "$LATEST_BOOK" >> /root/libra/queue.txt
          fi
          BOOK_TITLE=$(python3 -c "import json; d=json.load(open('/root/kdp/$LATEST_BOOK/listing.json')); print(d.get('title',''))" 2>/dev/null)
          tg_notify "✅ <b>Libra 2.0 SmartPublisher: ผ่าน QA และเข้าคิว KDP</b>\n${BOOK_TITLE}\n\nPaperback 40+ หน้าและ SEO ผ่านครบ"
          tg_photo "/root/kdp/$LATEST_BOOK/cover.jpg" "🎨 ปกเล่มใหม่: ${BOOK_TITLE}"
        else
          python3 - "$LISTING_FILE" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["status"] = "quality_failed"
json.dump(d, open(p, "w"), ensure_ascii=False, indent=2)
PY
          tg_notify "❌ <b>Libra 2.0 SmartPublisher: ไม่ผ่าน Quality Gate</b>\n${LATEST_BOOK}\n\nระบบจะไม่ส่งขึ้น KDP"
        fi
      fi
    fi
  fi

done

echo "=== Finished $(date) ===" >> "$LOG_FILE"
