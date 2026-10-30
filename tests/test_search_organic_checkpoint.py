import json
from datetime import date

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
