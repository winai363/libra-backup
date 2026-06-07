#!/bin/bash
# Libra Watchdog — runs every 5 minutes via cron
# Checks:
#   1. Is libra API responding? If not → fix port conflict + restart service
#   2. Are any books stuck in kdp_uploading=true for >2 hours? → reset them

LOG="/root/kdp/logs/watchdog.log"
mkdir -p "$(dirname "$LOG")"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

# ── 1. Health check ──────────────────────────────────────────────
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:8200/)

if [ "$HTTP_STATUS" != "200" ]; then
    echo "[$(ts)] WARN: Libra not responding (HTTP $HTTP_STATUS) — fixing..." >> "$LOG"

    # Kill any stale process on port 8200
    fuser -k 8200/tcp 2>/dev/null
    sleep 2

    # Restart via systemd
    systemctl restart libra.service
    sleep 5

    # Verify
    HTTP_STATUS2=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:8200/)
    if [ "$HTTP_STATUS2" == "200" ]; then
        echo "[$(ts)] OK: Libra recovered (HTTP $HTTP_STATUS2)" >> "$LOG"
    else
        echo "[$(ts)] ERROR: Libra still not responding after restart (HTTP $HTTP_STATUS2)" >> "$LOG"
    fi
else
    : # Healthy — stay quiet (no log spam)
fi

# ── 2. Reset books stuck in kdp_uploading=true for >2 hours ──────
python3 - <<'PYEOF'
import json, os, time
from pathlib import Path

KDP_DIR = Path("/root/kdp")
now = time.time()
TWO_HOURS = 2 * 3600
LOG = "/root/kdp/logs/watchdog.log"

def ts():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

for listing_file in KDP_DIR.glob("*/listing.json"):
    try:
        data = json.loads(listing_file.read_text())
    except Exception:
        continue

    if not data.get("kdp_uploading"):
        continue

    # Check mtime of listing.json — if modified >2h ago and still uploading, reset
    mtime = listing_file.stat().st_mtime
    if (now - mtime) > TWO_HOURS:
        slug = listing_file.parent.name
        data["kdp_uploading"] = False
        data["status"] = "ready"  # reset to ready so it can be re-triggered
        listing_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        with open(LOG, "a") as f:
            f.write(f"[{ts()}] RESET: {slug} stuck kdp_uploading → reset to ready\n")
PYEOF
