import scripts.kdp_auto_manager as manager


def test_free_post_without_external_evidence_is_not_executed(monkeypatch):
    logged = []
    monkeypatch.setattr(manager, "_append_action_log", logged.append)
    state = {"agent": {"free_growth_engine": {"decisions": [{
        "action": "free_post", "channel": "Pinterest/Reddit", "execute": True,
    }]}}}

    results = manager.execute_free_actions(state)

    assert results[0]["status"] == "manual_required"
    assert logged[0]["result"]["status"] == "manual_required"


def test_free_post_with_external_evidence_is_executed(monkeypatch):
    monkeypatch.setattr(manager, "_append_action_log", lambda row: None)
    state = {"agent": {"free_growth_engine": {"decisions": [{
        "action": "free_post", "execute": True,
        "external_url": "https://example.com/posts/1",
    }]}}}

    assert manager.execute_free_actions(state)[0]["status"] == "executed"
