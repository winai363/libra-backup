import json

import category_health_manager as health


def test_kdp_category_notice_prevents_false_green(tmp_path, monkeypatch):
    incidents = tmp_path / "incidents.json"
    incidents.write_text(json.dumps({"incidents": [{
        "asin": "NOTICE-ASIN",
        "category": "Career Counseling eBooks",
        "noticed_at": "2026-07-22",
        "source": "KDP category quality email",
        "action": "category_removed_by_kdp",
        "resolved": False,
    }]}))
    monkeypatch.setattr(health, "INCIDENTS_FILE", incidents)
    monkeypatch.setattr(health, "load_tree", lambda: {"Valid > Leaf"})
    monkeypatch.setattr(health, "scan_language", lambda **kwargs: [])
    monkeypatch.setattr(health, "category_issues", lambda leaves: [])
    monkeypatch.setattr(health, "open_item_status", lambda: [])

    report = health.build_report()

    assert report["status"] == "metadata_risk"
    assert report["metadata_risk"] is True
    assert report["metadata_incidents"][0]["asin"] == "NOTICE-ASIN"
    assert report["removed_category_blacklist"]["NOTICE-ASIN"] == ["Career Counseling eBooks"]


def test_resolved_notice_remains_blacklisted_without_active_risk(tmp_path, monkeypatch):
    incidents = tmp_path / "incidents.json"
    incidents.write_text(json.dumps({"incidents": [{
        "asin": "NOTICE-ASIN",
        "category": "AI & Semantics",
        "noticed_at": "2026-07-22",
        "source": "KDP category quality email",
        "action": "category_removed_by_kdp",
        "resolved": True,
    }]}))
    monkeypatch.setattr(health, "INCIDENTS_FILE", incidents)
    monkeypatch.setattr(health, "load_tree", lambda: set())
    monkeypatch.setattr(health, "scan_language", lambda **kwargs: [])
    monkeypatch.setattr(health, "category_issues", lambda leaves: [])
    monkeypatch.setattr(health, "open_item_status", lambda: [])

    report = health.build_report()

    assert report["status"] == "ok"
    assert report["metadata_risk"] is False
    assert report["removed_category_blacklist"]["NOTICE-ASIN"] == ["AI & Semantics"]


def _risk_report(warning_count, incidents):
    return {
        "checked_at": "2026-09-13T08:55:00+07:00",
        "status": "metadata_risk",
        "blocker_count": 0,
        "warning_count": warning_count,
        "blockers": [],
        "metadata_incidents": incidents,
    }


NOTICE = {"asin": "B0H5C6PCBL", "category": "AI & Semantics", "noticed_at": "2026-07-22"}


def _capture_notify(tmp_path, monkeypatch, previous_state):
    state = tmp_path / "state.json"
    if previous_state is not None:
        state.write_text(json.dumps(previous_state))
    sent = []
    monkeypatch.setattr(health, "STATE_FILE", state)
    monkeypatch.setattr(health, "send_telegram", sent.append)
    return sent


def test_unchanged_notice_does_not_realert_when_only_warning_count_moves(tmp_path, monkeypatch):
    sent = _capture_notify(tmp_path, monkeypatch, None)
    health.maybe_notify(_risk_report(26, [NOTICE]))
    health.maybe_notify(_risk_report(25, [NOTICE]))
    health.maybe_notify(_risk_report(27, [NOTICE]))
    assert len(sent) == 1


def test_legacy_state_with_warning_count_does_not_realert(tmp_path, monkeypatch):
    legacy = {"signature": {
        "status": "metadata_risk", "blocker_count": 0, "warning_count": 26, "blockers": [],
        "metadata_incidents": [["B0H5C6PCBL", "AI & Semantics", "2026-07-22"]],
    }}
    sent = _capture_notify(tmp_path, monkeypatch, legacy)
    health.maybe_notify(_risk_report(25, [NOTICE]))
    assert sent == []


def test_new_notice_still_alerts(tmp_path, monkeypatch):
    sent = _capture_notify(tmp_path, monkeypatch, None)
    health.maybe_notify(_risk_report(25, [NOTICE]))
    new = {"asin": "B0H4KT12GV", "category": "Business Intelligence Software", "noticed_at": "2026-09-13"}
    health.maybe_notify(_risk_report(25, [NOTICE, new]))
    assert len(sent) == 2
    assert "B0H4KT12GV" in sent[1]
