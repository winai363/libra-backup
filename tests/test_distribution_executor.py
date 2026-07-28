import pytest

from business_ledger import growth_evidence
from distribution_executor import execute_distribution


class FakeAdapter:
    """Test double standing in for a real posting adapter (Reddit, a KDP
    promo page, etc). ``execute_distribution`` calls ``adapter.publish(action)``
    and treats the returned dict as the adapter's reported after-state —
    never as a success signal on its own."""

    def __init__(self, response):
        self.response = response

    def publish(self, action):
        return self.response


class RaisingAdapter:
    """Test double whose publish() blows up — simulates a network error,
    a crashed browser session, or any unexpected adapter failure."""

    def publish(self, action):
        raise RuntimeError("connection reset")


def test_distribution_requires_external_after_state():
    result = execute_distribution(
        {"action_key": "post:1", "slug": "book-a", "language": "es"},
        adapter=FakeAdapter({"clicked": True, "post_url": None}),
    )
    assert result["status"] == "manual_required"
    assert result["evidence"] == {}


def test_distribution_executed_with_post_url_and_readable_after_state():
    result = execute_distribution(
        {"action_key": "post:2", "slug": "book-a"},
        adapter=FakeAdapter({
            "post_url": "https://reddit.com/r/books/comments/abc123",
            "after_state": {"title": "book-a discussion", "visible": True},
        }),
    )
    assert result["status"] == "executed"
    assert result["evidence"]["post_url"] == "https://reddit.com/r/books/comments/abc123"
    assert result["evidence"]["after_state"] == {"title": "book-a discussion", "visible": True}


def test_distribution_executed_with_post_id_and_readable_after_state():
    result = execute_distribution(
        {"action_key": "post:3", "slug": "book-a"},
        adapter=FakeAdapter({"post_id": "t3_abc123xyz", "after_state": {"status": "live"}}),
    )
    assert result["status"] == "executed"
    assert result["evidence"]["post_id"] == "t3_abc123xyz"


def test_distribution_post_url_without_readable_after_state_is_manual_required():
    # A URL alone, with nothing confirming it actually resolves to the
    # published post, is not enough — this is the exact "browser click
    # counted as success" trap the project has been burned by before.
    result = execute_distribution(
        {"action_key": "post:4", "slug": "book-a"},
        adapter=FakeAdapter({"post_url": "https://reddit.com/r/books/comments/abc123"}),
    )
    assert result["status"] == "manual_required"
    assert result["evidence"] == {}


def test_distribution_placeholder_post_id_does_not_count_as_proof():
    result = execute_distribution(
        {"action_key": "post:5", "slug": "book-a"},
        adapter=FakeAdapter({"post_id": "pending", "after_state": {"status": "queued"}}),
    )
    assert result["status"] == "manual_required"
    assert result["evidence"] == {}


def test_distribution_otp_barrier_is_manual_required():
    result = execute_distribution(
        {"action_key": "post:6", "slug": "book-a"},
        adapter=FakeAdapter({"otp_required": True}),
    )
    assert result["status"] == "manual_required"
    assert result["evidence"] == {}


def test_distribution_captcha_barrier_is_manual_required():
    result = execute_distribution(
        {"action_key": "post:7", "slug": "book-a"},
        adapter=FakeAdapter({"captcha_required": True}),
    )
    assert result["status"] == "manual_required"


def test_distribution_login_required_barrier_is_manual_required():
    result = execute_distribution(
        {"action_key": "post:8", "slug": "book-a"},
        adapter=FakeAdapter({"login_required": True}),
    )
    assert result["status"] == "manual_required"


def test_distribution_session_expired_barrier_is_manual_required():
    result = execute_distribution(
        {"action_key": "post:9", "slug": "book-a"},
        adapter=FakeAdapter({"session_expired": True}),
    )
    assert result["status"] == "manual_required"


def test_distribution_policy_rejection_is_blocked():
    result = execute_distribution(
        {"action_key": "post:10", "slug": "book-a"},
        adapter=FakeAdapter({"policy_rejected": True, "reason": "spam"}),
    )
    assert result["status"] == "blocked"
    assert result["evidence"] == {}


def test_distribution_adapter_exception_is_manual_required_with_adapter_error_reason():
    result = execute_distribution(
        {"action_key": "post:15", "slug": "book-a"},
        adapter=RaisingAdapter(),
    )
    assert result["status"] == "manual_required"
    assert result["evidence"] == {}
    assert result["reason"] == "adapter_error"


def test_distribution_top_level_stale_flag_is_manual_required():
    result = execute_distribution(
        {"action_key": "post:16", "slug": "book-a"},
        adapter=FakeAdapter({
            "post_url": "https://reddit.com/r/books/comments/stale1",
            "after_state": {"visible": True},
            "stale": True,
        }),
    )
    assert result["status"] == "manual_required"
    assert result["evidence"] == {}
    assert result["reason"] == "stale_data"


def test_distribution_after_state_stale_flag_is_manual_required():
    result = execute_distribution(
        {"action_key": "post:17", "slug": "book-a"},
        adapter=FakeAdapter({
            "post_url": "https://reddit.com/r/books/comments/stale2",
            "after_state": {"visible": True, "stale": True},
        }),
    )
    assert result["status"] == "manual_required"
    assert result["evidence"] == {}
    assert result["reason"] == "stale_data"


def test_distribution_accepts_numeric_post_id():
    result = execute_distribution(
        {"action_key": "post:18", "slug": "book-a"},
        adapter=FakeAdapter({"post_id": 123456789, "after_state": {"status": "live"}}),
    )
    assert result["status"] == "executed"
    assert result["evidence"]["post_id"] == "123456789"


def test_distribution_missing_action_identity_is_manual_required_and_not_recorded(tmp_path):
    db = tmp_path / "ledger.db"
    result = execute_distribution(
        {"language": "es"},  # no action_key, no slug
        adapter=FakeAdapter({
            "post_url": "https://reddit.com/r/books/comments/noid",
            "after_state": {"visible": True},
        }),
        ledger_path=db,
    )
    assert result["status"] == "manual_required"
    assert result["evidence"] == {}
    assert result["reason"] == "missing_action_identity"
    assert not db.exists()


def test_distribution_does_not_record_when_ledger_path_omitted(tmp_path):
    db = tmp_path / "ledger.db"
    execute_distribution(
        {"action_key": "post:11", "slug": "book-a"},
        adapter=FakeAdapter({"post_url": "https://reddit.com/r/books/comments/xyz", "after_state": {"visible": True}}),
    )
    assert not db.exists()


def test_distribution_records_growth_evidence_only_on_executed(tmp_path):
    db = tmp_path / "ledger.db"
    execute_distribution(
        {"action_key": "post:12", "slug": "book-a"},
        adapter=FakeAdapter({"clicked": True, "post_url": None}),
        ledger_path=db,
    )
    assert growth_evidence(db) == []

    execute_distribution(
        {"action_key": "post:13", "slug": "book-a"},
        adapter=FakeAdapter({"post_url": "https://reddit.com/r/books/comments/xyz", "after_state": {"visible": True}}),
        ledger_path=db,
    )
    rows = growth_evidence(db)
    assert len(rows) == 1
    assert rows[0]["slug"] == "book-a"
    assert rows[0]["payload"]["post_url"] == "https://reddit.com/r/books/comments/xyz"


def test_distribution_replay_with_same_action_key_is_idempotent_in_ledger(tmp_path):
    db = tmp_path / "ledger.db"
    action = {"action_key": "post:14", "slug": "book-a"}
    adapter = FakeAdapter({"post_url": "https://reddit.com/r/books/comments/rep", "after_state": {"visible": True}})
    execute_distribution(action, adapter=adapter, ledger_path=db)
    execute_distribution(action, adapter=adapter, ledger_path=db)
    assert len(growth_evidence(db)) == 1


def test_distribution_replay_with_different_evidence_raises_genuine_conflict(tmp_path):
    # Same action_key, but a second call reports a DIFFERENT post_url — this
    # is real data drift, not a harmless replay, and must still surface as
    # an error rather than be silently swallowed by the idempotency guard.
    db = tmp_path / "ledger.db"
    action = {"action_key": "post:19", "slug": "book-a"}
    execute_distribution(
        action,
        adapter=FakeAdapter({"post_url": "https://reddit.com/r/books/comments/first", "after_state": {"visible": True}}),
        ledger_path=db,
    )
    with pytest.raises(ValueError, match="conflicting growth evidence"):
        execute_distribution(
            action,
            adapter=FakeAdapter({"post_url": "https://reddit.com/r/books/comments/second", "after_state": {"visible": True}}),
            ledger_path=db,
        )
