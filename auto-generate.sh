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

tg_notify "🚀 <b>Libra 2.0 SmartPublisher: เริ่มสร้างหนังสือ</b>\n$(date '+%Y-%m-%d %H:%M') ICT"
TODAY=$(date +%Y-%m-%d)

for BOOK_NUM in $(seq 1 $BOOKS_PER_RUN); do
  echo "" >> "$LOG_FILE"
  echo ">>> Book $BOOK_NUM of $BOOKS_PER_RUN ===" >> "$LOG_FILE"

  # Build the prompt: check existing books first, then create new one
  EXISTING=$(for f in /root/kdp/*/listing.json; do
    [ -f "$f" ] && python3 -c "import sys,json; d=json.load(open('$f')); print(f\"- {d.get('title','')} ({d.get('language','')})\")" 2>/dev/null
  done)

  PROMPT="I need you to create a new KDP ebook using the kdp-writer skill.

Here are the ebooks already created (DO NOT duplicate these topics):
$EXISTING

=== PHASE 1: MARKET RESEARCH (mandatory — use WebSearch) ===
BEFORE choosing a topic, you MUST do real market research:
1. Check which languages are overrepresented in existing books above — pick a DIFFERENT one
2. Use WebSearch to search for: 'best selling kindle ebooks [marketplace] 2026', 'trending ebook niches [language]', 'amazon kindle bestseller categories [marketplace]'
3. Search in BOTH English AND the target language for broader results
4. Look for: trending topics, underserved niches, high-demand categories with low competition
5. Pick a topic that has REAL market demand based on your research findings
6. Save your market research as 'market-research.md' in the book directory

=== PHASE 2: CONTENT RESEARCH (mandatory — use WebSearch + WebFetch) ===
BEFORE writing the book, you MUST research real sources:
1. Use WebSearch to find 8-12 real sources about your chosen topic: expert articles, research papers, official guidelines, statistics
2. Use WebFetch to read the most important 3-5 sources in detail
3. Collect: real statistics/data, expert quotes, verified facts, practical methods from real sources
4. Compile a list of real references with actual URLs — these will become your Vancouver-style citations
5. Save your content research as 'content-research.md' in the book directory

=== PHASE 3: WRITE THE BOOK ===
1. Follow the full pipeline: write → listing → cover → EPUB → quality gate → queue
2. Use the researched facts, statistics, and sources from Phase 2 when writing
3. All references [1]-[n] must be REAL sources with REAL URLs from your research — never hallucinate references
4. Make sure the ebook appears in Libra queue with status 'ready'

CONTENT QUALITY GUIDELINES: Read and follow ALL guidelines in /root/libra/kdp-writing-guidelines.md BEFORE writing the book.

Go!"

  # Count books before (exclude logs/ directory)
  BOOKS_BEFORE=$(ls -d /root/kdp/*/ 2>/dev/null | grep -v '/logs/$' | wc -l)

  # --- GPT-4.1 (primary) ---
  echo ">>> Running GPT-4.1 (book $BOOK_NUM)..." >> "$LOG_FILE"
  python3 /root/libra/gpt_fallback_writer.py >> "$LOG_FILE" 2>&1
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
