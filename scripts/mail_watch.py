#!/usr/bin/env python3
"""Watch a Gmail inbox over IMAP for replies from senders we are waiting on and
push them to Telegram.

The Gmail connector only exists inside a chat session, so a cron job cannot use
it. This reads the mailbox directly with an app password instead.

Config: /root/.config/mail-watch/imap.env
    IMAP_USER=you@gmail.com
    IMAP_APP_PASSWORD=xxxxxxxxxxxxxxxx      # 16 chars from Google App passwords
    WATCH_SENDERS=lemonsqueezy               # comma separated substrings

Usage:
    python3 scripts/mail_watch.py            # poll, alert on new mail
    python3 scripts/mail_watch.py --check     # verify login + config only
"""

import argparse
import email
import email.header
import imaplib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from pathlib import Path

CONFIG_FILE = Path("/root/.config/mail-watch/imap.env")
STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "mail-watch-state.json"
LOOM_ENV = Path("/root/loom/.env")
IMAP_HOST = "imap.gmail.com"
SNIPPET_CHARS = 500
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


def decode_header(raw) -> str:
    """MIME-encoded headers ("=?UTF-8?B?...") back to readable text."""
    if raw is None:
        return ""
    parts = []
    for chunk, charset in email.header.decode_header(str(raw)):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts).strip()


def plain_body(message: Message) -> str:
    """First text/plain part, falling back to whatever text the mail carries."""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    payload = message.get_payload(decode=True) or b""
    return payload.decode(message.get_content_charset() or "utf-8", errors="replace")


def quoted_lines_removed(body: str) -> str:
    """Drop the quoted history so the alert shows what was actually written."""
    kept = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(">") or stripped.startswith("--"):
            break
        kept.append(line)
    return "\n".join(kept).strip() or body.strip()


def alert_message(sender: str, subject: str, body: str) -> str:
    snippet = quoted_lines_removed(body)[:SNIPPET_CHARS]
    return "\n".join([
        "📬 อีเมลใหม่ที่รออยู่",
        "",
        f"จาก: {sender}",
        f"เรื่อง: {subject}",
        "",
        snippet,
        "",
        "ตอบผ่าน Newton Chat ได้เลย (บอกให้ผมอ่านเธรดนี้)",
    ])


def matches_watchlist(sender: str, senders: list) -> bool:
    lowered = sender.lower()
    return any(term.lower() in lowered for term in senders if term.strip())


def fetch_new_mail(config: dict, last_uid: int) -> list:
    """[(uid, sender, subject, body)] for watched senders newer than last_uid."""
    senders = [s.strip() for s in config.get("WATCH_SENDERS", "lemonsqueezy").split(",")]
    found = []
    connection = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        connection.login(config["IMAP_USER"], config["IMAP_APP_PASSWORD"])
        connection.select("INBOX", readonly=True)
        status, data = connection.uid("search", None, f"UID {last_uid + 1}:*")
        if status != "OK":
            raise RuntimeError(f"IMAP search failed: {status}")
        for raw_uid in (data[0] or b"").split():
            uid = int(raw_uid)
            if uid <= last_uid:
                continue  # Gmail answers "N:*" with the last message when N is past the end
            status, payload = connection.uid("fetch", raw_uid, "(RFC822)")
            if status != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            message = email.message_from_bytes(payload[0][1])
            sender = decode_header(message.get("From"))
            if not matches_watchlist(sender, senders):
                found.append((uid, None, None, None))  # still advances the cursor
                continue
            found.append((uid, sender, decode_header(message.get("Subject")), plain_body(message)))
    finally:
        try:
            connection.logout()
        except Exception:  # a broken socket on logout must not lose the results
            pass
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify config and login only")
    args = parser.parse_args()

    config = load_env(CONFIG_FILE)
    missing = [k for k in ("IMAP_USER", "IMAP_APP_PASSWORD") if not config.get(k)]
    if missing:
        print(f"ยังไม่ได้ตั้งค่า {', '.join(missing)} ใน {CONFIG_FILE}", file=sys.stderr)
        return 2

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    last_uid = int(state.get("last_uid", 0))

    if args.check:
        connection = imaplib.IMAP4_SSL(IMAP_HOST)
        connection.login(config["IMAP_USER"], config["IMAP_APP_PASSWORD"])
        connection.select("INBOX", readonly=True)
        connection.logout()
        print(f"login ok: {config['IMAP_USER']} (last_uid={last_uid})")
        return 0

    try:
        messages = fetch_new_mail(config, last_uid)
    except (imaplib.IMAP4.error, OSError, RuntimeError) as error:
        streak = state.get("fail_streak", 0) + 1
        state["fail_streak"] = streak
        state["last_error"] = f"{type(error).__name__}: {error}"
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        if streak == FAIL_STREAK_ALERT:
            send_telegram(f"⚠️ อ่านกล่องอีเมลไม่ได้ {streak} ครั้งติด: {error}")
        print(f"poll failed ({streak} in a row): {error}", file=sys.stderr)
        return 1

    highest = last_uid
    for uid, sender, subject, body in messages:
        if sender is None:  # not a watched sender, nothing to say
            highest = max(highest, uid)
            continue
        if last_uid == 0:
            # First run: learn where the mailbox is without replaying old mail.
            highest = max(highest, uid)
            continue
        if not send_telegram(alert_message(sender, subject, body)):
            print("new mail found but alert failed -- keeping cursor", file=sys.stderr)
            state["fail_streak"] = 0
            state["last_uid"] = highest
            STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
            return 1
        print(f"alerted uid={uid} from={sender}")
        highest = max(highest, uid)

    state.update({"last_uid": highest, "fail_streak": 0})
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    if last_uid == 0:
        print(f"baseline recorded at uid={highest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
