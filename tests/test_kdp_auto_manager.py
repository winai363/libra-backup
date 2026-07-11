import scripts.kdp_auto_manager as manager


class _ProcessResult:
    def __init__(self, *, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


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


def test_legacy_free_promo_is_disabled(monkeypatch):
    monkeypatch.setattr(manager, "_append_action_log", lambda row: None)
    monkeypatch.setattr(
        manager.subprocess,
        "run",
        lambda *args, **kwargs: _ProcessResult(stdout="DONE: 1/1\n"),
    )
    state = {"agent": {"free_growth_engine": {"decisions": [{
        "action": "free_promo", "slug": "book-a", "execute": True,
    }]}}}

    result = manager.execute_free_actions(state)[0]

    assert result["status"] == "disabled"
    assert result["evidence"] == {}


def test_legacy_free_promo_does_not_accept_synthetic_evidence(monkeypatch):
    monkeypatch.setattr(manager, "_append_action_log", lambda row: None)
    monkeypatch.setattr(
        manager.subprocess,
        "run",
        lambda *args, **kwargs: _ProcessResult(
            stdout='scheduled\n{"verified_state_change": true}\n'
        ),
    )
    state = {"agent": {"free_growth_engine": {"decisions": [{
        "action": "free_promo", "slug": "book-a", "execute": True,
    }]}}}

    result = manager.execute_free_actions(state)[0]

    assert result["status"] == "disabled"
    assert result["evidence"] == {}
