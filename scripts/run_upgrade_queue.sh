#!/bin/bash
# Sequential upgrade queue — runs one at a time to avoid TPM collisions
# Usage: bash scripts/run_upgrade_queue.sh
set -e
cd /root/libra

QUEUE=(
  "senior-tech-guide-german"
  "ai-workflows-accountants-pt"
  "ai-productivity-remote-workers"
  "ai-productivity-spanish"
)

for slug in "${QUEUE[@]}"; do
  status=$(python3 -c "import json; print(json.load(open('/root/kdp/$slug/listing.json')).get('status','?'))" 2>/dev/null)
  if [ "$status" = "ready" ]; then
    echo "[SKIP] $slug already ready"
    continue
  fi
  echo ""
  echo "========================================"
  echo "[START] $slug"
  echo "========================================"
  python3 upgrade_existing_books.py "$slug" || echo "[WARN] $slug ended with non-zero exit"
  # Brief pause between books to let TPM reset
  sleep 10
done

echo ""
echo "=== Queue complete ==="
for slug in "${QUEUE[@]}"; do
  status=$(python3 -c "import json; print(json.load(open('/root/kdp/$slug/listing.json')).get('status','?'))" 2>/dev/null)
  echo "  $status  $slug"
done
