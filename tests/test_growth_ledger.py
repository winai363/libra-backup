import pytest

from business_ledger import record_growth_evidence


def test_growth_evidence_is_idempotent_and_conflicts_fail(tmp_path):
    db = tmp_path / "ledger.db"
    item = {
        "source_key": "hub-click:abc", "kind": "hub_click", "slug": "book-a",
        "observed_at": "2026-07-29T09:00:00+00:00",
        "fresh_until": "2026-07-30T09:00:00+00:00",
        "confidence": 1.0, "payload": {"campaign": "organic-1"},
    }
    assert record_growth_evidence(db, item) == record_growth_evidence(db, item)
    with pytest.raises(ValueError, match="conflicting growth evidence"):
        record_growth_evidence(db, {**item, "payload": {"campaign": "changed"}})
