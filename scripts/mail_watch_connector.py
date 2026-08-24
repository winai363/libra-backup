#!/usr/bin/env python3
"""Watch the inbox for replies we are waiting on, using the Gmail connector
through a headless `claude -p` run, and push new mail to Telegram.

No mailbox credential is needed: the connector is already authenticated for
this account and, unlike what the docs warn about, it does work in a headless
run on this box (verified 24 Aug 2026). The model is used only to read the
mailbox -- deciding what is new and whether to alert stays in this script.

Config: /root/.config/mail-watch/imap.env
    WATCH_SENDERS=lemonsqueezy,other-sender    # substrings of the From address

Usage:
    python3 scripts/mail_watch_connector.py           # poll, alert on new mail
    python3 scripts/mail_watch_connector.py --dry-run  # print, never alert
"""

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CONFIG_FILE = Path("/root/.config/mail-watch/imap.env")
STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "mail-watch-connector-state.json"
LOOM_ENV = Path("/root/loom/.env")
CLAUDE = "/usr/bin/claude"
GMAIL_TOOLS = "mcp__claude_ai_Gmail__search_threads,mcp__claude_ai_Gmail__get_thread"
TIMEOUT_SECONDS = 300
SNIPPET_CHARS = 500
SEEN_KEPT = 200
FAIL_STREAK_ALERT = 3
# Our own address: mail we sent is not a reply we are waiting for.
OWN_ADDRESS = "winai363@gmail.com"


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


def build_prompt(senders: list) -> str:
    query = " OR ".join(f"from:{sender}" for sender in senders)
    return (
        f"ใช้ mcp__claude_ai_Gmail__search_threads ค้นหา query: ({query}) newer_than:7d "
        "pageSize 10 แล้วสำหรับแต่ละข้อความที่พบ ให้คืนค่าเป็น JSON array เท่านั้น "
        "แต่ละ element มีคีย์: id, date, sender, subject, snippet "
        "(snippet = ข้อความ 400 ตัวอักษรแรก ไม่ต้องรวมส่วนที่ quote อีเมลเก่า). "
        "ถ้าจำเป็นต้องอ่านเนื้อความให้ใช้ mcp__claude_ai_Gmail__get_thread ด้วย messageFormat PLAIN_TEXT. "
        "ห้ามเขียนคำอธิบายใดๆ นอกจาก JSON array. ถ้าไม่พบอะไรเลยให้ตอบ []"
    )


def extract_json_array(text: str) -> list:
    """The model's answer, tolerant of code fences and stray prose around it."""
    if not text:
        return []
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("["), candidate.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(candidate[start:end + 1])
    except json.JSONDecodeError:
        return []
    return [item for item in parsed if isinstance(item, dict) and item.get("id")]


def read_mailbox(senders: list) -> list:
    result = subprocess.run(
        [CLAUDE, "-p", build_prompt(senders), "--allowedTools", GMAIL_TOOLS,
         "--max-turns", "8", "--output-format", "json"],
        capture_output=True, text=True, timeout=TIMEOUT_SECONDS, cwd="/root",
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude exited {result.returncode}: {result.stderr[-300:]}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"unreadable claude output: {error}")
    if payload.get("is_error"):
        raise RuntimeError(f"claude reported an error: {str(payload.get('result'))[:300]}")
    return extract_json_array(str(payload.get("result", "")))


def unseen_incoming(messages: list, seen: list, senders: list) -> list:
    """New mail from a watched sender that is not our own outgoing copy."""
    fresh = []
    for message in messages:
        sender = str(message.get("sender", "")).lower()
        if message["id"] in seen:
            continue
        if OWN_ADDRESS in sender:
            continue
        if not any(term.lower() in sender for term in senders if term.strip()):
            continue
        fresh.append(message)
    return fresh


def alert_message(message: dict) -> str:
    snippet = str(message.get("snippet", "")).strip()[:SNIPPET_CHARS]
    return "\n".join([
        "📬 อีเมลใหม่ที่รออยู่",
        "",
        f"จาก: {message.get('sender', '?')}",
        f"เรื่อง: {message.get('subject', '(ไม่มีหัวข้อ)')}",
        f"เวลา: {message.get('date', '?')}",
        "",
        snippet,
        "",
        "บอกผมใน Newton Chat ได้เลยถ้าจะให้ตอบ",
    ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print findings, never alert")
    args = parser.parse_args()

    config = load_env(CONFIG_FILE)
    senders = [s.strip() for s in config.get("WATCH_SENDERS", "lemonsqueezy").split(",") if s.strip()]
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    seen = state.get("seen_ids", [])

    try:
        messages = read_mailbox(senders)
    except (subprocess.TimeoutExpired, RuntimeError, OSError) as error:
        streak = state.get("fail_streak", 0) + 1
        state["fail_streak"] = streak
        state["last_error"] = f"{type(error).__name__}: {str(error)[:300]}"
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        if streak == FAIL_STREAK_ALERT:
            send_telegram(f"⚠️ อ่านกล่องอีเมลไม่ได้ {streak} ครั้งติด: {str(error)[:200]}")
        print(f"poll failed ({streak} in a row): {error}", file=sys.stderr)
        return 1

    fresh = unseen_incoming(messages, seen, senders)
    if args.dry_run:
        print(json.dumps({"found": messages, "new": fresh}, ensure_ascii=False, indent=2))
        return 0

    first_run = not state.get("seen_ids")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if first_run:
        # Learn what is already in the mailbox instead of replaying it.
        print(f"{now} baseline: {len(messages)} messages marked as seen")
    else:
        for message in fresh:
            if not send_telegram(alert_message(message)):
                # Leave this id unseen so the next run tries again.
                state["seen_ids"] = seen
                STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
                print("new mail found but alert failed -- keeping it unseen", file=sys.stderr)
                return 1
            seen.append(message["id"])
            print(f"{now} alerted {message['id']} from {message.get('sender')}")

    for message in messages:
        if message["id"] not in seen:
            seen.append(message["id"])
    state.update({"seen_ids": seen[-SEEN_KEPT:], "checked_at": now, "fail_streak": 0})
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
