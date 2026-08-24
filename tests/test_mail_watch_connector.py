"""The connector-based inbox watcher: what counts as new mail worth alerting."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import mail_watch_connector as watch


def test_json_array_is_read_through_code_fences_and_prose():
    text = 'Here you go:\n```json\n[{"id": "a1", "sender": "x@y.com"}]\n```\nthat is all'
    assert watch.extract_json_array(text) == [{"id": "a1", "sender": "x@y.com"}]


def test_empty_or_unparseable_answer_is_no_messages():
    assert watch.extract_json_array("") == []
    assert watch.extract_json_array("ไม่พบอีเมล") == []
    assert watch.extract_json_array("[not json]") == []
    assert watch.extract_json_array('[{"no_id": 1}]') == []  # unusable entry dropped


def test_our_own_sent_mail_is_never_an_alert():
    messages = [{"id": "m1", "sender": "Winai Klinprasom <winai363@gmail.com>"}]
    assert watch.unseen_incoming(messages, [], ["lemonsqueezy"]) == []


def test_already_seen_mail_is_not_alerted_twice():
    messages = [{"id": "m1", "sender": "hello@lemonsqueezy.com"}]
    assert watch.unseen_incoming(messages, ["m1"], ["lemonsqueezy"]) == []


def test_unwatched_sender_is_ignored():
    messages = [{"id": "m1", "sender": "noreply@amazon.com"}]
    assert watch.unseen_incoming(messages, [], ["lemonsqueezy"]) == []


def test_new_mail_from_a_watched_sender_is_returned():
    messages = [{"id": "m2", "sender": "Suhasini <hello@lemonsqueezy.com>"}]
    assert watch.unseen_incoming(messages, ["m1"], ["lemonsqueezy"]) == messages


def test_alert_carries_sender_subject_and_snippet():
    text = watch.alert_message({
        "id": "m2", "sender": "hello@lemonsqueezy.com",
        "subject": "Re: WKBUI", "date": "2026-08-25", "snippet": "your store is approved",
    })
    assert "hello@lemonsqueezy.com" in text and "Re: WKBUI" in text
    assert "your store is approved" in text


def _world(tmp_path, monkeypatch, mailbox, state=None, telegram_ok=True):
    state_file = tmp_path / "state.json"
    if state is not None:
        state_file.write_text(json.dumps(state))
    sent = []
    monkeypatch.setattr(watch, "STATE_FILE", state_file)
    monkeypatch.setattr(watch, "CONFIG_FILE", tmp_path / "absent.env")
    monkeypatch.setattr(watch, "read_mailbox", lambda senders: mailbox)
    monkeypatch.setattr(watch, "send_telegram", lambda m: (sent.append(m), telegram_ok)[1])
    monkeypatch.setattr(sys, "argv", ["mail_watch_connector.py"])
    return state_file, sent


def test_first_run_records_a_baseline_without_replaying_old_mail(tmp_path, monkeypatch):
    state_file, sent = _world(tmp_path, monkeypatch, [
        {"id": "old1", "sender": "hello@lemonsqueezy.com", "subject": "s", "snippet": "b"}])

    assert watch.main() == 0
    assert sent == []
    assert json.loads(state_file.read_text())["seen_ids"] == ["old1"]


def test_a_new_reply_is_alerted_once(tmp_path, monkeypatch):
    state_file, sent = _world(
        tmp_path, monkeypatch,
        [{"id": "new1", "sender": "hello@lemonsqueezy.com", "subject": "Re", "snippet": "hi"}],
        state={"seen_ids": ["old1"]})

    assert watch.main() == 0
    assert len(sent) == 1 and "Re" in sent[0]
    assert set(json.loads(state_file.read_text())["seen_ids"]) == {"old1", "new1"}


def test_a_failed_alert_leaves_the_mail_unseen_for_the_next_run(tmp_path, monkeypatch):
    state_file, sent = _world(
        tmp_path, monkeypatch,
        [{"id": "new1", "sender": "hello@lemonsqueezy.com", "subject": "Re", "snippet": "hi"}],
        state={"seen_ids": ["old1"]}, telegram_ok=False)

    assert watch.main() == 1
    assert json.loads(state_file.read_text())["seen_ids"] == ["old1"]


def test_repeated_read_failures_alert_once_at_the_threshold(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"seen_ids": ["old1"], "fail_streak": 1}))
    alerts = []
    monkeypatch.setattr(watch, "STATE_FILE", state_file)
    monkeypatch.setattr(watch, "CONFIG_FILE", tmp_path / "absent.env")
    monkeypatch.setattr(watch, "send_telegram", lambda m: alerts.append(m) or True)
    monkeypatch.setattr(sys, "argv", ["mail_watch_connector.py"])

    def boom(senders):
        raise RuntimeError("claude exited 1")

    monkeypatch.setattr(watch, "read_mailbox", boom)
    assert watch.main() == 1 and alerts == []
    assert watch.main() == 1 and len(alerts) == 1
    assert watch.main() == 1 and len(alerts) == 1
    assert json.loads(state_file.read_text())["seen_ids"] == ["old1"]
