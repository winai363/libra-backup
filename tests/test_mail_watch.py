"""Tests for scripts/mail_watch.py — the notice pipeline.

These are transport and parsing tests against a fake IMAP server: they prove the
mailbox → message → deduplicated alert path works, and they prove nothing at all
about whether a real KDP notice has ever arrived. Real-notice evidence can only
come from a live mailbox that actually holds one.
"""
import email.message
import imaplib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import mail_watch  # noqa: E402

PASSWORD = "appspecificpw123"


def raw_mail(sender: str, subject: str, body: str, *, message_id: str | None = None,
             date: str = "Sat, 12 Sep 2026 19:52:00 +0000", multipart: bool = False) -> bytes:
    if multipart:
        message = email.message.EmailMessage()
        message.set_content(body)
        message.add_alternative(f"<p>{body}</p>", subtype="html")
    else:
        message = email.message.EmailMessage()
        message.set_content(body)
    message["From"] = sender
    message["Subject"] = subject
    message["Date"] = date
    if message_id:
        message["Message-ID"] = message_id
    return message.as_bytes()


class FakeIMAP:
    """Minimal IMAP4_SSL stand-in: UID search over a dict of {uid: raw bytes}."""
    instances: list = []

    def __init__(self, host, mailbox=None, login_error=None):
        self.host = host
        self.mailbox = mailbox or {}
        self.login_error = login_error
        self.logged_in_as = None
        self.readonly = None
        self.logged_out = False
        FakeIMAP.instances.append(self)

    def login(self, user, password):
        if self.login_error:
            raise self.login_error
        assert password == PASSWORD
        self.logged_in_as = user
        return "OK", [b"logged in"]

    def select(self, folder, readonly=False):
        self.readonly = readonly
        return "OK", [b"1"]

    def uid(self, command, *args):
        if command == "search":
            return "OK", [" ".join(str(u) for u in sorted(self.mailbox)).encode()]
        if command == "fetch":
            uid = int(args[0])
            return "OK", [(b"1 (RFC822 {})", self.mailbox[uid])]
        raise AssertionError(f"unexpected IMAP command {command}")

    def logout(self):
        self.logged_out = True
        return "BYE", [b"bye"]


@pytest.fixture(autouse=True)
def fake_transport(monkeypatch, tmp_path):
    FakeIMAP.instances = []
    monkeypatch.setattr(mail_watch, "SEEN_FILE", tmp_path / "seen.json")
    monkeypatch.setattr(mail_watch, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mail_watch, "STATE_FILE", tmp_path / "mail-watch-state.json")
    return tmp_path


def install_mailbox(monkeypatch, mailbox, *, login_error=None):
    monkeypatch.setattr(mail_watch, "IMAP_FACTORY",
                        lambda host: FakeIMAP(host, mailbox, login_error))


def account(tmp_path, name="icloud", *, host="imap.mail.me.com", senders="kdp,lemonsqueezy",
            user="owner@icloud.com", password=PASSWORD) -> dict:
    config = {"IMAP_HOST": host, "IMAP_USER": user, "WATCH_SENDERS": senders}
    if password:
        config["IMAP_APP_PASSWORD"] = password
    return {"name": name, "config": config,
            "missing": [] if password else ["IMAP_APP_PASSWORD"],
            "state_file": tmp_path / f"mail-watch-state-{name}.json"}


def seeded(state_file: Path, last_uid: int) -> None:
    state_file.write_text(json.dumps({"last_uid": last_uid}))


class Sink:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    def __call__(self, message):
        self.sent.append(message)
        return self.ok


# ── config discovery ────────────────────────────────────────────────────────

def test_each_env_file_is_one_mailbox(tmp_path):
    (tmp_path / "imap.env").write_text(f"IMAP_USER=a@gmail.com\nIMAP_APP_PASSWORD={PASSWORD}\n")
    (tmp_path / "icloud.env").write_text(f"IMAP_USER=b@icloud.com\nIMAP_APP_PASSWORD={PASSWORD}\n")

    accounts = mail_watch.load_accounts(tmp_path)

    assert [a["name"] for a in accounts] == ["icloud", "imap"]
    assert all(a["missing"] == [] for a in accounts)


def test_example_config_without_credentials_is_reported_not_polled(tmp_path):
    (tmp_path / "icloud.env").write_text("IMAP_USER=\nIMAP_APP_PASSWORD=\n")

    accounts = mail_watch.load_accounts(tmp_path)

    assert accounts[0]["missing"] == ["IMAP_USER", "IMAP_APP_PASSWORD"]


def test_legacy_account_keeps_its_original_state_file():
    """The Gmail mailbox's UID cursor must not be reset by this change."""
    assert mail_watch.state_file_for("imap") == mail_watch.STATE_FILE
    assert mail_watch.state_file_for("icloud") != mail_watch.STATE_FILE


# ── parsing and alerting ────────────────────────────────────────────────────

def test_watched_sender_alerts_and_unwatched_mail_only_moves_the_cursor(tmp_path, monkeypatch):
    install_mailbox(monkeypatch, {
        7: raw_mail("KDP <kdp-support@amazon.com>", "Book review result", "We won't be accepting"),
        8: raw_mail("News <news@example.com>", "Newsletter", "ignore me"),
    })
    acct = account(tmp_path)
    seeded(acct["state_file"], 6)
    sink = Sink()

    result = mail_watch.poll_account(acct, [], send=sink)

    assert result["alerted"] == 1
    assert result["last_uid"] == 8
    assert "kdp-support@amazon.com" in sink.sent[0]
    assert "Book review result" in sink.sent[0]
    assert "(icloud)" in sink.sent[0]


def test_first_run_records_a_baseline_without_replaying_old_mail(tmp_path, monkeypatch):
    install_mailbox(monkeypatch, {
        3: raw_mail("kdp@amazon.com", "Old notice", "from before we watched"),
    })
    acct = account(tmp_path)
    sink = Sink()

    result = mail_watch.poll_account(acct, [], send=sink)

    assert result["state"] == "baseline"
    assert sink.sent == []
    assert json.loads(acct["state_file"].read_text())["last_uid"] == 3


def test_mime_encoded_subject_and_multipart_body_are_readable(tmp_path, monkeypatch):
    install_mailbox(monkeypatch, {
        2: raw_mail("kdp@amazon.com", "=?UTF-8?B?SGVsbG8gS0RQ?=", "plain text part",
                    multipart=True),
    })
    acct = account(tmp_path)
    seeded(acct["state_file"], 1)
    sink = Sink()

    mail_watch.poll_account(acct, [], send=sink)

    assert "Hello KDP" in sink.sent[0]
    assert "plain text part" in sink.sent[0]


def test_icloud_host_from_config_is_used(tmp_path, monkeypatch):
    install_mailbox(monkeypatch, {})
    acct = account(tmp_path, host="imap.mail.me.com")
    seeded(acct["state_file"], 1)

    mail_watch.poll_account(acct, [], send=Sink())

    assert FakeIMAP.instances[-1].host == "imap.mail.me.com"
    assert FakeIMAP.instances[-1].readonly is True


def test_mailbox_is_opened_read_only_and_logged_out(tmp_path, monkeypatch):
    install_mailbox(monkeypatch, {4: raw_mail("kdp@amazon.com", "s", "b")})
    acct = account(tmp_path)
    seeded(acct["state_file"], 3)

    mail_watch.poll_account(acct, [], send=Sink())

    assert FakeIMAP.instances[-1].readonly is True
    assert FakeIMAP.instances[-1].logged_out is True


# ── deduplication ───────────────────────────────────────────────────────────

def test_the_same_notice_in_two_mailboxes_alerts_once(tmp_path, monkeypatch):
    """Forwarding iCloud → Gmail means both mailboxes hold the same notice."""
    notice = raw_mail("kdp@amazon.com", "Review result", "body", message_id="<abc@amazon.com>")
    seen: list = []
    sink = Sink()

    install_mailbox(monkeypatch, {5: notice})
    first = account(tmp_path, "icloud")
    seeded(first["state_file"], 4)
    mail_watch.poll_account(first, seen, send=sink)

    install_mailbox(monkeypatch, {9: notice})
    second = account(tmp_path, "imap", host="imap.gmail.com")
    seeded(second["state_file"], 8)
    result = mail_watch.poll_account(second, seen, send=sink)

    assert len(sink.sent) == 1
    assert result["duplicates"] == 1
    assert result["last_uid"] == 9


def test_mail_without_message_id_is_deduplicated_by_digest(tmp_path, monkeypatch):
    notice = raw_mail("kdp@amazon.com", "Review result", "body")
    seen: list = []
    sink = Sink()

    install_mailbox(monkeypatch, {5: notice})
    first = account(tmp_path, "icloud")
    seeded(first["state_file"], 4)
    mail_watch.poll_account(first, seen, send=sink)

    install_mailbox(monkeypatch, {6: notice})
    second = account(tmp_path, "imap")
    seeded(second["state_file"], 5)
    mail_watch.poll_account(second, seen, send=sink)

    assert len(sink.sent) == 1
    assert seen[0].startswith("digest:")


def test_two_different_notices_both_alert(tmp_path, monkeypatch):
    install_mailbox(monkeypatch, {
        5: raw_mail("kdp@amazon.com", "First", "a", message_id="<1@amazon.com>"),
        6: raw_mail("kdp@amazon.com", "Second", "b", message_id="<2@amazon.com>"),
    })
    acct = account(tmp_path)
    seeded(acct["state_file"], 4)
    sink = Sink()

    result = mail_watch.poll_account(acct, [], send=sink)

    assert result["alerted"] == 2
    assert len(sink.sent) == 2


def test_seen_ledger_round_trips_and_is_capped(tmp_path):
    ids = [f"<{n}@amazon.com>" for n in range(mail_watch.SEEN_KEPT + 10)]
    mail_watch.save_seen(ids, tmp_path / "seen.json")

    loaded = mail_watch.load_seen(tmp_path / "seen.json")

    assert len(loaded) == mail_watch.SEEN_KEPT
    assert loaded[-1] == ids[-1]


def test_unreadable_seen_ledger_degrades_to_empty(tmp_path):
    broken = tmp_path / "seen.json"
    broken.write_text("{not json")

    assert mail_watch.load_seen(broken) == []


# ── failures ────────────────────────────────────────────────────────────────

def test_login_failure_counts_a_streak_and_alerts_only_on_the_third(tmp_path, monkeypatch):
    install_mailbox(monkeypatch, {}, login_error=imaplib.IMAP4.error("AUTHENTICATIONFAILED"))
    acct = account(tmp_path)
    seeded(acct["state_file"], 4)
    sink = Sink()

    for expected in (1, 2, 3):
        result = mail_watch.poll_account(acct, [], send=sink)
        assert result["fail_streak"] == expected
    assert len(sink.sent) == 1
    assert "3 ครั้งติด" in sink.sent[0]


def test_failure_never_writes_the_password_anywhere(tmp_path, monkeypatch, capsys):
    install_mailbox(monkeypatch, {}, login_error=imaplib.IMAP4.error(f"bad login {PASSWORD}"))
    acct = account(tmp_path)
    seeded(acct["state_file"], 4)
    sink = Sink()

    mail_watch.poll_account(acct, [], send=sink)

    state = acct["state_file"].read_text()
    printed = capsys.readouterr()
    assert PASSWORD not in state
    assert PASSWORD not in printed.out + printed.err
    assert PASSWORD not in "".join(sink.sent)


def test_one_broken_mailbox_does_not_stop_another(tmp_path, monkeypatch):
    broken = account(tmp_path, "icloud")
    seeded(broken["state_file"], 4)
    working = account(tmp_path, "imap")
    seeded(working["state_file"], 4)
    sink = Sink()

    install_mailbox(monkeypatch, {}, login_error=OSError("network down"))
    first = mail_watch.poll_account(broken, [], send=sink)
    install_mailbox(monkeypatch, {5: raw_mail("kdp@amazon.com", "Notice", "body")})
    second = mail_watch.poll_account(working, [], send=sink)

    assert first["state"] == "failed"
    assert second["state"] == "ok" and second["alerted"] == 1


def test_unconfigured_mailbox_is_skipped_not_failed(tmp_path):
    acct = account(tmp_path, "icloud", password=None)

    result = mail_watch.poll_account(acct, [], send=Sink())

    assert result["state"] == "unconfigured"
    assert result["missing"] == ["IMAP_APP_PASSWORD"]


def test_failed_alert_keeps_the_cursor_for_a_retry(tmp_path, monkeypatch):
    install_mailbox(monkeypatch, {5: raw_mail("kdp@amazon.com", "Notice", "body")})
    acct = account(tmp_path)
    seeded(acct["state_file"], 4)
    sink = Sink(ok=False)

    result = mail_watch.poll_account(acct, [], send=sink)

    assert result["state"] == "alert_failed"
    assert json.loads(acct["state_file"].read_text())["last_uid"] == 4


# ── check mode ──────────────────────────────────────────────────────────────

def test_check_reports_ok_for_a_reachable_mailbox(tmp_path, monkeypatch):
    install_mailbox(monkeypatch, {})

    result = mail_watch.check_account(account(tmp_path))

    assert result["state"] == "ok"
    assert result["user"] == "owner@icloud.com"
    assert PASSWORD not in json.dumps(result)


def test_check_reports_login_failure_without_the_password(tmp_path, monkeypatch):
    install_mailbox(monkeypatch, {}, login_error=imaplib.IMAP4.error(f"bad {PASSWORD}"))

    result = mail_watch.check_account(account(tmp_path))

    assert result["state"] == "login_failed"
    assert PASSWORD not in json.dumps(result)
