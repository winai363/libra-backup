#!/usr/bin/env python3
"""Watch one or more IMAP mailboxes for mail from senders we are waiting on and
push it to Telegram.

Why more than one mailbox: KDP sends its notices — including the "disappointing
customer experience" rejections — to the Apple ID that owns the KDP account,
not to the Gmail address this watcher started with. A scan of the watched Gmail
inbox back to 1 June 2026 found zero mail from amazon or kdp, so a second
mailbox is the only way those notices reach an alert at all.

Config: every `*.env` file in /root/.config/mail-watch/ is one mailbox.
    IMAP_HOST=imap.mail.me.com        # optional; defaults to imap.gmail.com
    IMAP_USER=you@icloud.com
    IMAP_APP_PASSWORD=xxxxxxxxxxxxxxxx    # app-specific password, never a login password
    WATCH_SENDERS=kdp,lemonsqueezy        # comma separated substrings of the From address

Secrets stay in those files: nothing here prints or logs a password, and an
error is reported as its type and message only.

Each mailbox keeps its own UID cursor, and a shared Message-ID ledger stops one
notice from alerting twice when a mailbox forwards to another watched mailbox.

Usage:
    python3 scripts/mail_watch.py                  # poll every configured mailbox
    python3 scripts/mail_watch.py --check          # verify config + login only
    python3 scripts/mail_watch.py --account icloud # one mailbox (file stem)
"""

import argparse
import email
import email.header
import hashlib
import imaplib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from pathlib import Path

CONFIG_DIR = Path("/root/.config/mail-watch")
# The original single-mailbox config keeps the original state file, so the Gmail
# cursor survives this change untouched.
LEGACY_ACCOUNT = "imap"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_FILE = DATA_DIR / "mail-watch-state.json"
SEEN_FILE = DATA_DIR / "mail-watch-seen.json"
LOOM_ENV = Path("/root/loom/.env")
DEFAULT_IMAP_HOST = "imap.gmail.com"
SNIPPET_CHARS = 500
FAIL_STREAK_ALERT = 3
SEEN_KEPT = 500

# Swapped for a fake in tests: a transport test must never need a real mailbox.
IMAP_FACTORY = imaplib.IMAP4_SSL


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


def state_file_for(account: str) -> Path:
    return STATE_FILE if account == LEGACY_ACCOUNT else DATA_DIR / f"mail-watch-state-{account}.json"


def load_accounts(config_dir: Path = CONFIG_DIR, only: str | None = None) -> list:
    """[(account_name, config)] for every configured mailbox, skipping the
    example file and any mailbox missing its credentials."""
    accounts = []
    for env_file in sorted(Path(config_dir).glob("*.env")):
        name = env_file.stem
        if only and name != only:
            continue
        config = load_env(env_file)
        missing = [k for k in ("IMAP_USER", "IMAP_APP_PASSWORD") if not config.get(k)]
        accounts.append({"name": name, "config": config, "missing": missing,
                         "state_file": state_file_for(name)})
    return accounts


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


def message_identity(message: Message, sender: str, subject: str) -> str:
    """A stable id for one piece of mail across mailboxes. Message-ID survives
    forwarding as a header of the forwarded copy's body but not as its own
    header, so fall back to a digest of sender + subject + date: two mailboxes
    holding the same notice must still alert once."""
    message_id = (message.get("Message-ID") or "").strip()
    if message_id:
        return message_id
    raw = "|".join([sender, subject, decode_header(message.get("Date"))])
    return "digest:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def alert_message(sender: str, subject: str, body: str, account: str = "") -> str:
    snippet = quoted_lines_removed(body)[:SNIPPET_CHARS]
    where = f" ({account})" if account else ""
    return "\n".join([
        f"📬 อีเมลใหม่ที่รออยู่{where}",
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


def load_seen(seen_file: Path = SEEN_FILE) -> list:
    data = {}
    try:
        data = json.loads(Path(seen_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    ids = data.get("message_ids")
    return [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else []


def save_seen(ids: list, seen_file: Path = SEEN_FILE) -> None:
    Path(seen_file).write_text(
        json.dumps({"message_ids": ids[-SEEN_KEPT:]}, ensure_ascii=False, indent=2),
        encoding="utf-8")


def redact(text: str, config: dict) -> str:
    """Strip the mailbox credentials out of anything we are about to write or
    print. A server's error text is data we do not control: it must never carry
    a password into a state file, a cron log, or a Telegram message."""
    cleaned = str(text)
    for key in ("IMAP_APP_PASSWORD", "IMAP_USER"):
        secret = (config.get(key) or "").strip()
        if len(secret) >= 6:
            cleaned = cleaned.replace(secret, "***")
    return cleaned


def fetch_new_mail(config: dict, last_uid: int) -> list:
    """[(uid, sender, subject, body, identity)] for watched senders newer than
    last_uid. Unwatched mail comes back with None fields so the cursor still
    advances past it."""
    senders = [s.strip() for s in config.get("WATCH_SENDERS", "lemonsqueezy").split(",")]
    host = config.get("IMAP_HOST") or DEFAULT_IMAP_HOST
    found = []
    connection = IMAP_FACTORY(host)
    try:
        connection.login(config["IMAP_USER"], config["IMAP_APP_PASSWORD"])
        connection.select("INBOX", readonly=True)
        status, data = connection.uid("search", None, f"UID {last_uid + 1}:*")
        if status != "OK":
            raise RuntimeError(f"IMAP search failed: {status}")
        for raw_uid in (data[0] or b"").split():
            uid = int(raw_uid)
            if uid <= last_uid:
                continue  # a server answers "N:*" with the last message when N is past the end
            status, payload = connection.uid("fetch", raw_uid, "(RFC822)")
            if status != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            message = email.message_from_bytes(payload[0][1])
            sender = decode_header(message.get("From"))
            if not matches_watchlist(sender, senders):
                found.append((uid, None, None, None, None))
                continue
            subject = decode_header(message.get("Subject"))
            found.append((uid, sender, subject, plain_body(message),
                          message_identity(message, sender, subject)))
    finally:
        try:
            connection.logout()
        except Exception:  # a broken socket on logout must not lose the results
            pass
    return found


def poll_account(account: dict, seen: list, *, send=send_telegram) -> dict:
    """Poll one mailbox. Returns a result dict; never raises for a mailbox
    problem, so one unreachable mailbox cannot stop the others."""
    name = account["name"]
    state_file = Path(account["state_file"])
    if account["missing"]:
        return {"account": name, "state": "unconfigured", "missing": account["missing"],
                "alerted": 0}

    state = {}
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    last_uid = int(state.get("last_uid", 0))

    try:
        messages = fetch_new_mail(account["config"], last_uid)
    except (imaplib.IMAP4.error, OSError, RuntimeError) as error:
        streak = int(state.get("fail_streak", 0)) + 1
        state["fail_streak"] = streak
        state["last_error"] = redact(f"{type(error).__name__}: {error}", account["config"])
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        if streak == FAIL_STREAK_ALERT:
            send(f"⚠️ อ่านกล่องอีเมล {name} ไม่ได้ {streak} ครั้งติด: {type(error).__name__}")
        return {"account": name, "state": "failed", "fail_streak": streak, "alerted": 0,
                "error": f"{type(error).__name__}"}

    highest, alerted, duplicates = last_uid, 0, 0
    for uid, sender, subject, body, identity in messages:
        if sender is None:
            highest = max(highest, uid)
            continue
        if last_uid == 0:
            # First run on a mailbox: learn where it is without replaying old mail.
            highest = max(highest, uid)
            continue
        if identity in seen:
            duplicates += 1
            highest = max(highest, uid)
            continue
        if not send(alert_message(sender, subject, body, account=name)):
            state.update({"fail_streak": 0, "last_uid": highest})
            state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"account": name, "state": "alert_failed", "alerted": alerted,
                    "duplicates": duplicates}
        seen.append(identity)
        alerted += 1
        highest = max(highest, uid)

    baseline = last_uid == 0
    state.update({"last_uid": highest, "fail_streak": 0})
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"account": name, "state": "baseline" if baseline else "ok",
            "last_uid": highest, "alerted": alerted, "duplicates": duplicates}


def check_account(account: dict) -> dict:
    if account["missing"]:
        return {"account": account["name"], "state": "unconfigured",
                "missing": account["missing"]}
    config = account["config"]
    try:
        connection = IMAP_FACTORY(config.get("IMAP_HOST") or DEFAULT_IMAP_HOST)
        connection.login(config["IMAP_USER"], config["IMAP_APP_PASSWORD"])
        connection.select("INBOX", readonly=True)
        connection.logout()
    except (imaplib.IMAP4.error, OSError) as error:
        return {"account": account["name"], "state": "login_failed",
                "error": f"{type(error).__name__}"}
    return {"account": account["name"], "state": "ok", "user": config["IMAP_USER"],
            "senders": config.get("WATCH_SENDERS", "")}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify config and login only")
    parser.add_argument("--account", help="only this mailbox (the config file stem)")
    args = parser.parse_args(argv)

    accounts = load_accounts(only=args.account)
    if not accounts:
        print(f"ไม่พบไฟล์ตั้งค่ากล่องอีเมลใน {CONFIG_DIR}", file=sys.stderr)
        return 2

    if args.check:
        failed = False
        for account in accounts:
            result = check_account(account)
            failed = failed or result["state"] != "ok"
            print(json.dumps(result, ensure_ascii=False))
        return 0 if not failed else 1

    seen = load_seen()
    before = len(seen)
    results = [poll_account(account, seen) for account in accounts]
    if len(seen) != before:
        save_seen(seen)
    for result in results:
        print(json.dumps(result, ensure_ascii=False))
    return 0 if all(r["state"] in ("ok", "baseline") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
