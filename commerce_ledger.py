"""Durable, append-only inbox for provider commerce events.

Rules this module enforces:
- Test mode only. A `live` event is refused outright.
- Same provider event ID + identical content = no-op (`duplicate`).
- Same ID + different content = `conflict`: a conflict row plus a critical
  incident, and the original event is never rewritten.
- Only a sanitized projection is stored — never the raw provider body.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from business_ledger import init_ledger

REQUIRED_EVENT_FIELDS = (
    "provider",
    "event_id",
    "event_type",
    "occurred_at",
    "received_at",
    "mode",
    "verification_state",
    "payload_hash",
    "sanitized_payload",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate(event: dict) -> None:
    missing = [field for field in REQUIRED_EVENT_FIELDS if field not in event]
    if missing:
        raise ValueError("missing event field(s): " + ",".join(missing))
    if event["mode"] != "test":
        raise ValueError("refused: commerce mode must be 'test'")


def record_provider_event(path: Path, event: dict) -> dict:
    """Persist one provider event exactly once. Returns a receipt dict."""
    _validate(event)
    path = Path(path)
    init_ledger(path)
    provider, event_id = event["provider"], event["event_id"]

    connection = sqlite3.connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT id, payload_hash FROM commerce_events WHERE provider=? AND event_id=?",
            (provider, event_id),
        ).fetchone()
        if row is not None:
            if row[1] == event["payload_hash"]:
                connection.commit()
                return {"status": "duplicate", "provider": provider, "event_id": event_id}
            recorded_at = _now()
            connection.execute(
                "INSERT OR IGNORE INTO commerce_event_conflicts"
                "(provider, event_id, original_event_id, conflicting_hash, recorded_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (provider, event_id, row[0], event["payload_hash"], recorded_at),
            )
            connection.execute(
                "INSERT OR IGNORE INTO commerce_incidents"
                "(incident_key, opened_at, severity, scope, error_code, detail_json)"
                " VALUES (?, ?, 'critical', ?, 'event_content_conflict', ?)",
                (
                    f"conflict:{provider}:{event_id}",
                    recorded_at,
                    f"{provider}:{event_id}",
                    json.dumps({"original_event_id": row[0], "conflicting_hash": event["payload_hash"]}),
                ),
            )
            connection.commit()
            return {"status": "conflict", "provider": provider, "event_id": event_id}

        connection.execute(
            "INSERT INTO commerce_events"
            "(provider, event_id, event_type, occurred_at, received_at, mode,"
            " verification_state, payload_hash, sanitized_payload_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                provider,
                event_id,
                event["event_type"],
                event["occurred_at"],
                event["received_at"],
                event["mode"],
                event["verification_state"],
                event["payload_hash"],
                json.dumps(event["sanitized_payload"], ensure_ascii=False, sort_keys=True),
            ),
        )
        connection.commit()
        return {"status": "inserted", "provider": provider, "event_id": event_id}
    finally:
        connection.close()


def mark_provider_event(path: Path, provider: str, event_id: str, status: str,
                        *, error_code: str | None = None) -> None:
    with sqlite3.connect(Path(path)) as connection:
        connection.execute(
            "UPDATE commerce_events SET processing_state=?, error_code=?"
            " WHERE provider=? AND event_id=?",
            (status, error_code, provider, event_id),
        )


def commerce_event(path: Path, provider: str, event_id: str) -> dict | None:
    with sqlite3.connect(Path(path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM commerce_events WHERE provider=? AND event_id=?",
            (provider, event_id),
        ).fetchone()
    if row is None:
        return None
    event = dict(row)
    event["sanitized_payload"] = json.loads(event.pop("sanitized_payload_json"))
    return event


def open_incident(path: Path, *, incident_key: str, severity: str, scope: str,
                  error_code: str, detail: dict) -> None:
    with sqlite3.connect(Path(path)) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO commerce_incidents"
            "(incident_key, opened_at, severity, scope, error_code, detail_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (incident_key, _now(), severity, scope, error_code,
             json.dumps(detail, ensure_ascii=False, sort_keys=True)),
        )


def open_incidents(path: Path) -> list:
    path = Path(path)
    if not path.exists():
        return []
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM commerce_incidents WHERE resolved_at IS NULL ORDER BY opened_at"
        ).fetchall()
    return [dict(row) for row in rows]
