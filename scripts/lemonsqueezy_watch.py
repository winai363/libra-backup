#!/usr/bin/env python3
"""Watch the Lemon Squeezy store for the state changes we are waiting on and
alert on Telegram when something actually moves.

We poll the LS API (the LS website 403s from this server) and compare a small
fingerprint of the facts that matter: store plan/sales and the status of every
product and variant. Anything that changes is reported verbatim -- the script
never claims to know which field means "approved", it just reports the move.

Usage:
    python3 scripts/lemonsqueezy_watch.py            # poll, alert on change
    python3 scripts/lemonsqueezy_watch.py --status   # print current, no alert
    python3 scripts/lemonsqueezy_watch.py --test-alert  # prove Telegram works
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "data" / "lemonsqueezy-watch-state.json"
LOOM_ENV = Path("/root/loom/.env")
API = "https://api.lemonsqueezy.com/v1/"
# Alert only after this many consecutive failures, so one flaky poll is quiet.
FAIL_STREAK_ALERT = 3


def load_env(path: Path) -> dict:
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def api_get(path: str, key: str) -> dict:
    request = urllib.request.Request(API + path, headers={
        "Accept": "application/vnd.api+json",
        "Authorization": f"Bearer {key}",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def fetch_fingerprint(key: str) -> dict:
    """The facts we are waiting on, flattened to plain scalars."""
    fingerprint = {}
    for store in api_get("stores", key)["data"]:
        attributes = store["attributes"]
        prefix = f"store {attributes.get('name')}"
        fingerprint[f"{prefix}: plan"] = attributes.get("plan")
        fingerprint[f"{prefix}: total sales"] = attributes.get("total_sales")
        fingerprint[f"{prefix}: total revenue"] = attributes.get("total_revenue")
    for product in api_get("products", key)["data"]:
        attributes = product["attributes"]
        fingerprint[f"product {attributes.get('name')}: status"] = attributes.get("status")
    for variant in api_get("variants", key)["data"]:
        attributes = variant["attributes"]
        fingerprint[f"variant {variant['id']}: status"] = attributes.get("status")
    return fingerprint


def diff_fingerprint(previous: dict, current: dict) -> list:
    """(field, before, after) for every field that appeared, vanished or moved."""
    changes = []
    for field in sorted(set(previous) | set(current)):
        before = previous.get(field, "—")
        after = current.get(field, "หายไป")
        if before != after:
            changes.append((field, before, after))
    return changes


def send_telegram(message: str) -> bool:
    env = load_env(LOOM_ENV)
    # TELEGRAM_BOT_TOKEN was revoked in July 2026; HQ_BOT_TOKEN is the live one.
    token = env.get("HQ_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("HQ_CHAT_ID") or env.get("TELEGRAM_NOTIFY_CHAT_ID")
    if not token or not chat_id:
        print("no telegram credentials -- alert not sent", file=sys.stderr)
        return False
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data), timeout=30
        ) as response:
            return json.load(response).get("ok", False)
    except urllib.error.URLError as error:
        print(f"telegram failed: {error}", file=sys.stderr)
        return False


def change_message(changes: list) -> str:
    lines = ["🍋 Lemon Squeezy: สถานะร้านเปลี่ยนแล้ว", ""]
    lines += [f"• {field}: {before} → {after}" for field, before, after in changes]
    lines += [
        "",
        "ถ้าร้านเปิดรับเงินจริงได้แล้ว อย่าลืมลบลิงก์รีวิว:",
        "rm -rf /var/www/ls-review  + ลบ location /ls-review/ ใน nginx",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true", help="print current state, never alert")
    parser.add_argument("--test-alert", action="store_true", help="send a test Telegram message")
    args = parser.parse_args()

    if args.test_alert:
        return 0 if send_telegram("🍋 ทดสอบ: ตัวเฝ้าร้าน Lemon Squeezy ส่งข้อความได้") else 1

    key = load_env(ROOT / ".env").get("LEMONSQUEEZY_API_KEY")
    if not key:
        print("LEMONSQUEEZY_API_KEY missing", file=sys.stderr)
        return 1

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    try:
        current = fetch_fingerprint(key)
    except (urllib.error.URLError, ValueError, KeyError) as error:
        streak = state.get("fail_streak", 0) + 1
        state["fail_streak"] = streak
        state["last_error"] = f"{type(error).__name__}: {error}"
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        if streak == FAIL_STREAK_ALERT:
            send_telegram(f"⚠️ เช็คสถานะร้าน Lemon Squeezy ไม่ได้ {streak} ครั้งติด: {error}")
        print(f"poll failed ({streak} in a row): {error}", file=sys.stderr)
        return 1

    if args.status:
        print(json.dumps(current, ensure_ascii=False, indent=2))
        return 0

    previous = state.get("fingerprint", {})
    changes = diff_fingerprint(previous, current) if previous else []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if changes:
        # Only record the new state as reported once the alert actually left,
        # so a Telegram outage cannot swallow the one notification we care about.
        if not send_telegram(change_message(changes)):
            print("change detected but alert failed -- keeping old state", file=sys.stderr)
            return 1
        print(f"{now} changed: {changes}")
    elif not previous:
        print(f"{now} baseline recorded: {len(current)} fields")
    state.update({"fingerprint": current, "checked_at": now, "fail_streak": 0})
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
