#!/bin/bash
# Retry --update for books still "In review" on KDP.  
# Runs every 4h via cron; exits quietly when all books are updated.

BOOKS=("ai-creative-workbook-italian" "anxiety-workbook-young-women-de")
LOG="/root/kdp/logs/kdp-update-$(date +%Y-%m-%d).log"

echo "=== KDP Update Retry $(date) ===" >> "$LOG"
pending=0

for slug in "${BOOKS[@]}"; do
    updated=$(python3 -c "import json; d=json.load(open('/root/kdp/$slug/listing.json')); print(d.get('content_updated_at',''))" 2>/dev/null)
    if [ -n "$updated" ]; then
        echo "  $slug: already updated ($updated) — skip" >> "$LOG"
        continue
    fi

    echo "  $slug: attempting update..." >> "$LOG"
    out=$(python3 /root/libra/kdp_upload.py "$slug" --update 2>&1)
    echo "$out" >> "$LOG"

    if echo "$out" | grep -q "Update complete"; then
        echo "  ✅ $slug updated" >> "$LOG"
    else
        echo "  ⏳ $slug not ready yet" >> "$LOG"
        pending=$((pending + 1))
    fi
    sleep 30
done

echo "=== Done (pending=$pending) $(date) ===" >> "$LOG"
