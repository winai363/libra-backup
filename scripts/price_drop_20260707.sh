#!/bin/bash
# One-shot: drop adhd-self-help-adults-es to $2.99 + force 70% royalty,
# scheduled for 2026-07-07 15:00 (after the free promo ends 2026-07-06 23:59 PT
# = 2026-07-07 13:59 Thai). Removes its own cron line afterwards so it never
# re-runs. If set_price aborts (royalty gate), it Telegrams — handle manually.
cd /root/libra || exit 1
python3 scripts/set_price.py adhd-self-help-adults-es 2.99 >> logs/set_price.log 2>&1
# Self-remove regardless of outcome — exactly one attempt; failures alert via
# the script's own Telegram message.
python3 - << 'EOF'
import subprocess
r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
lines = [l for l in (r.stdout or "").splitlines()
         if "price_drop_20260707.sh" not in l]
subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)
EOF
