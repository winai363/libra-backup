"""The reconciliation CLI is offline by construction — it never calls a provider."""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_commerce_reconciliation import (
    _ingest,
    _seed_verified_order,
    payhip_paid,
    stripe_payment,
)
from commerce_ledger import record_provider_event

LIBRA = Path(__file__).resolve().parent.parent
SCRIPT = LIBRA / "scripts" / "libra_commerce_reconcile.py"


def _run(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env={**os.environ, "PYTHONPATH": str(LIBRA)},
        capture_output=True,
        text=True,
    )


def _pending(db):
    """A Stripe payment with no Payhip order yet — waits for its counterpart."""
    record_provider_event(db, stripe_payment())
    return db


def test_dry_run_is_read_only_and_an_unknown_mode_is_refused(tmp_path):
    db = tmp_path / "ledger.db"
    _ingest(db, payhip_paid())
    _pending(db)
    before = db.read_bytes()

    dry = _run("--ledger", str(db), "--mode", "test", "--dry-run")

    assert dry.returncode == 0, dry.stderr
    payload = json.loads(dry.stdout)
    assert payload["external_calls"] == 0
    assert payload["mode"] == "test"
    assert payload["pending"] == 1
    assert db.read_bytes() == before

    # live is a permitted mode now (Payhip has no sandbox), but it must be
    # spelled out — and anything that is not a real mode is still refused.
    bogus = _run("--ledger", str(db), "--mode", "sandbox", "--apply")
    assert bogus.returncode == 2
    assert "unknown_mode" in bogus.stderr

    live = _run("--ledger", str(db), "--mode", "live", "--dry-run")
    assert live.returncode == 0
    assert json.loads(live.stdout)["mode"] == "live"


def test_apply_reconciles_pending_events_and_is_idempotent(tmp_path):
    db = tmp_path / "ledger.db"
    _ingest(db, payhip_paid())
    _pending(db)

    first = _run("--ledger", str(db), "--mode", "test", "--apply")
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["reconciled"] == 1

    second = _run("--ledger", str(db), "--mode", "test", "--apply")
    assert second.returncode == 0
    assert json.loads(second.stdout)["reconciled"] == 0


def test_conflicts_and_manual_required_are_reported_with_a_nonzero_exit(tmp_path):
    db = tmp_path / "ledger.db"
    _ingest(db, payhip_paid(gross_minor=1290))
    _ingest(db, stripe_payment(amount_minor=990))  # amount mismatch → incident

    result = _run("--ledger", str(db), "--mode", "test", "--dry-run")

    assert result.returncode == 3
    payload = json.loads(result.stdout)
    assert payload["open_incidents"] >= 1


def test_missing_ledger_fails_cleanly(tmp_path):
    result = _run("--ledger", str(tmp_path / "nope.db"), "--mode", "test", "--dry-run")

    assert result.returncode == 2
    assert "ledger_not_found" in result.stderr


def test_output_is_one_json_object_with_no_payload_or_customer_data(tmp_path):
    db = tmp_path / "ledger.db"
    _seed_verified_order(db)

    result = _run("--ledger", str(db), "--mode", "test", "--dry-run")

    payload = json.loads(result.stdout)  # raises if more than one object
    text = result.stdout.lower()
    for leak in ("whsec", "email", "@", "signature", "payload_hash"):
        assert leak not in text
    assert set(payload) >= {
        "mode", "events_seen", "reconciled", "pending", "conflicts",
        "manual_required", "open_incidents", "external_calls",
    }


def test_cli_imports_no_network_client():
    import ast

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint({"httpx", "requests", "urllib", "stripe", "openai"})
