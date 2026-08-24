"""The Lemon Squeezy store watcher: what counts as a change worth waking us."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import lemonsqueezy_watch as watch


def test_no_change_reports_nothing():
    state = {"variant 1: status": "pending"}
    assert watch.diff_fingerprint(state, dict(state)) == []


def test_a_moved_field_is_reported_with_both_values():
    changes = watch.diff_fingerprint(
        {"variant 1: status": "pending"}, {"variant 1: status": "published"}
    )
    assert changes == [("variant 1: status", "pending", "published")]


def test_new_and_vanished_fields_are_both_reported():
    changes = watch.diff_fingerprint(
        {"product A: status": "published"}, {"product B: status": "draft"}
    )
    assert ("product A: status", "published", "หายไป") in changes
    assert ("product B: status", "—", "draft") in changes


def test_first_sale_is_a_change(tmp_path):
    changes = watch.diff_fingerprint(
        {"store WKBUI: total sales": 0}, {"store WKBUI: total sales": 1}
    )
    assert changes == [("store WKBUI: total sales", 0, 1)]


def test_a_failed_alert_keeps_the_old_state_so_the_change_is_not_lost(tmp_path, monkeypatch, capsys):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"fingerprint": {"variant 1: status": "pending"}}))
    monkeypatch.setattr(watch, "STATE_FILE", state_file)
    monkeypatch.setattr(watch, "load_env", lambda path: {"LEMONSQUEEZY_API_KEY": "k"})
    monkeypatch.setattr(watch, "fetch_fingerprint", lambda key: {"variant 1: status": "published"})
    monkeypatch.setattr(watch, "send_telegram", lambda message: False)
    monkeypatch.setattr(sys, "argv", ["lemonsqueezy_watch.py"])

    assert watch.main() == 1
    assert json.loads(state_file.read_text())["fingerprint"] == {"variant 1: status": "pending"}


def test_a_delivered_alert_records_the_new_state(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"fingerprint": {"variant 1: status": "pending"}}))
    sent = []
    monkeypatch.setattr(watch, "STATE_FILE", state_file)
    monkeypatch.setattr(watch, "load_env", lambda path: {"LEMONSQUEEZY_API_KEY": "k"})
    monkeypatch.setattr(watch, "fetch_fingerprint", lambda key: {"variant 1: status": "published"})
    monkeypatch.setattr(watch, "send_telegram", lambda message: sent.append(message) or True)
    monkeypatch.setattr(sys, "argv", ["lemonsqueezy_watch.py"])

    assert watch.main() == 0
    assert json.loads(state_file.read_text())["fingerprint"] == {"variant 1: status": "published"}
    assert "pending → published" in sent[0]
    assert "/var/www/ls-review" in sent[0]  # the cleanup reminder rides along


def test_repeated_poll_failures_alert_once_at_the_threshold(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"fingerprint": {"a": 1}, "fail_streak": 1}))
    alerts = []
    monkeypatch.setattr(watch, "STATE_FILE", state_file)
    monkeypatch.setattr(watch, "load_env", lambda path: {"LEMONSQUEEZY_API_KEY": "k"})
    monkeypatch.setattr(watch, "send_telegram", lambda message: alerts.append(message) or True)
    monkeypatch.setattr(sys, "argv", ["lemonsqueezy_watch.py"])

    def boom(key):
        raise ValueError("api down")

    monkeypatch.setattr(watch, "fetch_fingerprint", boom)
    assert watch.main() == 1  # second failure: still quiet
    assert alerts == []
    assert watch.main() == 1  # third failure: one alert
    assert len(alerts) == 1
    assert watch.main() == 1  # fourth: no repeat
    assert len(alerts) == 1
    assert json.loads(state_file.read_text())["fingerprint"] == {"a": 1}
