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
