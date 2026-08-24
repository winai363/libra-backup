"""The inbox watcher: what gets reported, and what must never be lost."""

import email
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import mail_watch as watch


def test_only_watched_senders_are_reported():
    assert watch.matches_watchlist("Suhasini <hello@lemonsqueezy.com>", ["lemonsqueezy"])
    assert watch.matches_watchlist("hello@lemonsqueezy-mail.com", ["lemonsqueezy"])
    assert not watch.matches_watchlist("noreply@amazon.com", ["lemonsqueezy"])


def test_encoded_subject_is_decoded():
    assert watch.decode_header("=?UTF-8?B?4Lij4Liw4Lia4Lia?=") == "ระบบ"


def test_quoted_history_is_stripped_from_the_snippet():
    body = "Could you send the files?\n\n> On Sat, Aug 22, someone wrote:\n> old text"
    assert watch.quoted_lines_removed(body) == "Could you send the files?"


def test_body_without_quotes_survives():
    assert watch.quoted_lines_removed("just this") == "just this"


def test_plain_body_prefers_the_text_part():
    message = email.message_from_string(
        "MIME-Version: 1.0\n"
        'Content-Type: multipart/alternative; boundary="b"\n\n'
        "--b\nContent-Type: text/plain\n\nhello plain\n"
        "--b\nContent-Type: text/html\n\n<p>hello html</p>\n--b--\n"
    )
    assert "hello plain" in watch.plain_body(message)


def test_alert_names_sender_and_subject():
    message = watch.alert_message("hello@lemonsqueezy.com", "Re: WKBUI", "please send files")
    assert "hello@lemonsqueezy.com" in message
    assert "Re: WKBUI" in message
    assert "please send files" in message


def _world(tmp_path, monkeypatch, messages, last_uid, telegram_ok=True):
    state_file = tmp_path / "state.json"
    if last_uid:
        state_file.write_text(json.dumps({"last_uid": last_uid}))
    sent = []
    monkeypatch.setattr(watch, "STATE_FILE", state_file)
    monkeypatch.setattr(watch, "CONFIG_FILE", tmp_path / "imap.env")
    monkeypatch.setattr(watch, "load_env", lambda path: {
        "IMAP_USER": "u@gmail.com", "IMAP_APP_PASSWORD": "p"})
    monkeypatch.setattr(watch, "fetch_new_mail", lambda config, uid: messages)
    monkeypatch.setattr(watch, "send_telegram",
                        lambda m: (sent.append(m), telegram_ok)[1])
    monkeypatch.setattr(sys, "argv", ["mail_watch.py"])
    return state_file, sent


def test_first_run_records_a_baseline_without_replaying_old_mail(tmp_path, monkeypatch):
    state_file, sent = _world(
        tmp_path, monkeypatch,
        [(41, "hello@lemonsqueezy.com", "old", "old body")], last_uid=0)

    assert watch.main() == 0
    assert sent == []
    assert json.loads(state_file.read_text())["last_uid"] == 41


def test_new_watched_mail_is_alerted_and_the_cursor_advances(tmp_path, monkeypatch):
    state_file, sent = _world(
        tmp_path, monkeypatch,
        [(42, "hello@lemonsqueezy.com", "Re: WKBUI", "send the files")], last_uid=41)

    assert watch.main() == 0
    assert len(sent) == 1 and "Re: WKBUI" in sent[0]
    assert json.loads(state_file.read_text())["last_uid"] == 42


def test_unwatched_mail_advances_the_cursor_silently(tmp_path, monkeypatch):
    state_file, sent = _world(
        tmp_path, monkeypatch, [(43, None, None, None)], last_uid=41)

    assert watch.main() == 0
    assert sent == []
    assert json.loads(state_file.read_text())["last_uid"] == 43


def test_a_failed_alert_keeps_the_mail_unread_by_the_cursor(tmp_path, monkeypatch):
    state_file, sent = _world(
        tmp_path, monkeypatch,
        [(42, "hello@lemonsqueezy.com", "Re: WKBUI", "send the files")],
        last_uid=41, telegram_ok=False)

    assert watch.main() == 1
    assert json.loads(state_file.read_text())["last_uid"] == 41  # retried next run
