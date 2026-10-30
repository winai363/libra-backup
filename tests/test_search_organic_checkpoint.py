import json
from datetime import date

import scripts.search_organic_checkpoint as checkpoint
from scripts.search_organic_checkpoint import (
    checkpoint_due,
    claim_delivery,
    load_state,
    mark_delivery,
)


def test_checkpoint_is_due_on_or_after_target_until_successfully_sent(tmp_path):
    state = tmp_path / "state.json"

    assert checkpoint_due(date(2026, 11, 5), date(2026, 11, 6), state) is False
    assert checkpoint_due(date(2026, 11, 6), date(2026, 11, 6), state) is True
    assert checkpoint_due(date(2026, 11, 7), date(2026, 11, 6), state) is True

    claim_delivery(state, date(2026, 11, 6), "digest-1")

    assert checkpoint_due(date(2026, 11, 7), date(2026, 11, 6), state) is False
    assert json.loads(state.read_text())["checkpoint"]["state"] == "SENDING"

    mark_delivery(state, "SENT")

    assert json.loads(state.read_text())["checkpoint"]["state"] == "SENT"


def test_uncertain_delivery_is_never_retried_automatically(tmp_path):
    state = tmp_path / "state.json"
    claim_delivery(state, date(2026, 11, 8), "digest-1")
    mark_delivery(state, "DELIVERY_UNCERTAIN")
    assert checkpoint_due(date(2026, 11, 9), date(2026, 11, 8), state) is False


def test_non_object_state_fails_safe(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("[]")
    assert load_state(state) == {}
    assert checkpoint_due(date(2026, 11, 8), date(2026, 11, 8), state) is True


def test_monitor_keeps_polling_until_final_metrics_are_stored(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "sitemap": {"state": "SUBMITTED"},
        "checkpoint": {"state": "SENT"},
    }))
    responses = [
        {"status": "CONNECTED", "metrics": {"status": "PENDING_FINAL_DATA"}},
        {"status": "CONNECTED", "metrics": {
            "clicks": 1, "impressions": 20, "ctr": 0.05, "position": 12.0,
        }},
    ]
    calls = []

    def collect(*args, **kwargs):
        calls.append(1)
        return responses.pop(0)

    monkeypatch.setattr(checkpoint, "collect_live", collect)
    monkeypatch.setattr(checkpoint, "build_report", lambda *a, **k: {
        "traffic": {"qualified_views": 10, "amazon_outbound": 0},
        "goal": {"latest_royalties_usd": 3.71},
    })
    common = [
        "--as-of", "2026-11-09",
        "--state", str(state),
        "--lock", str(tmp_path / "lock"),
        "--snapshots", str(tmp_path / "snapshots.jsonl"),
        "--ledger", str(tmp_path / "ledger.db"),
        "--portfolio", str(tmp_path / "portfolio.json"),
    ]

    assert checkpoint.main(common) == 0
    assert checkpoint.main(common) == 0
    assert len(calls) == 2
    assert load_state(state)["metrics"]["state"] == "FINAL"

    assert checkpoint.main(common) == 0
    assert len(calls) == 2
