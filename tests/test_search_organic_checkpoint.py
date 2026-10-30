import json
from datetime import date

from scripts.search_organic_checkpoint import checkpoint_due, record_sent


def test_checkpoint_is_due_on_or_after_target_until_successfully_sent(tmp_path):
    state = tmp_path / "state.json"

    assert checkpoint_due(date(2026, 11, 5), date(2026, 11, 6), state) is False
    assert checkpoint_due(date(2026, 11, 6), date(2026, 11, 6), state) is True
    assert checkpoint_due(date(2026, 11, 7), date(2026, 11, 6), state) is True

    record_sent(state, date(2026, 11, 6), "digest-1")

    assert checkpoint_due(date(2026, 11, 7), date(2026, 11, 6), state) is False
    assert json.loads(state.read_text())["sent_for"] == "2026-11-06"
