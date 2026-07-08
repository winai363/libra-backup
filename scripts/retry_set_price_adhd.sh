#!/bin/bash
# One-shot-until-success retry for the adhd-self-help-adults-es price drop to $2.99.
# The 2026-07-02→06 Free Promo locked KDP's royalty binding at 35%, so set_price
# correctly refused to publish (would have halved royalty). KDP re-enables the
# binding some hours-to-a-day after a promo ends. This runs daily until the price
# actually lands at 2.99, then removes its own cron line.
set -e
SLUG=adhd-self-help-adults-es
TARGET=2.99
LISTING=/root/kdp/$SLUG/listing.json

# Already at target? Nothing to do — clean up the cron and exit.
cur=$(python3 -c "import json;print(json.load(open('$LISTING')).get('price'))" 2>/dev/null || echo "")
if [ "$cur" = "$TARGET" ] || [ "$cur" = "2.99" ]; then
  python3 -c "
import subprocess
r=subprocess.run(['crontab','-l'],capture_output=True,text=True)
lines=[l for l in r.stdout.splitlines() if 'retry_set_price_adhd.sh' not in l]
subprocess.run(['crontab','-'],input='\n'.join(lines)+'\n',text=True)
"
  echo "price already $cur — retry cron removed"
  exit 0
fi

cd /root/libra
if python3 scripts/set_price.py "$SLUG" "$TARGET"; then
  # Success — set_price wrote the new price. Remove our cron line so it stops.
  python3 -c "
import subprocess
r=subprocess.run(['crontab','-l'],capture_output=True,text=True)
lines=[l for l in r.stdout.splitlines() if 'retry_set_price_adhd.sh' not in l]
subprocess.run(['crontab','-'],input='\n'.join(lines)+'\n',text=True)
"
  echo "set_price succeeded — retry cron removed"
fi
