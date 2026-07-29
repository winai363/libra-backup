"""Tests for Task 10 — Growth Dashboard and Operating Reports: GET /growth
(HTML), GET /api/growth/state (JSON), and the plain-language Telegram
digest builder. Follows the same tmp-ledger + monkeypatched-app-module
pattern as tests/test_growth_routes.py and tests/test_profit_api.py."""
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import app as libra_app
from business_ledger import record_kdp_snapshot


NOW = datetime.fromisoformat("2026-07-29T09:00:00+07:00")

FULL_STATE = {
    "generated_at": "2026-07-29T09:00:00+07:00",
    "mode": "shadow",
    "locked": False,
    "phase": "organic",
    "started_at": "2026-07-01T09:00:00+07:00",
    "readiness": {
        "mutation_allowed": True,
        "reason": "ready",
        "open_incidents": 0,
        "blocked_slugs": [],
    },
    "observations_collected": 4,
    "scored_titles": [
        {"slug": "book-a", "score": 0.6, "classification": "scale", "evidence_fresh": True,
         "components": {}, "reasons": []},
        {"slug": "book-b", "score": 0.1, "classification": "test", "evidence_fresh": False,
         "components": {}, "reasons": []},
    ],
    "plan": {
        "action_key": "abc123",
        "phase": "organic_test",
        "portfolio": {"active": [{"slug": "book-b", "classification": "test", "score": 0.1}]},
        "actions": [{"slug": "book-b", "kind": "organic_test", "variable": "price"}],
    },
    "executed": [
        {"slug": "book-a", "kind": "free_promo", "status": "executed", "reason": "verified_after_state",
         "evidence": {"confirmation_id": "conf-1", "verified_state_change": {"before": "off", "after": "on"}}},
    ],
    "blocked": [
        {"slug": "book-c", "kind": "price_update", "reason": "title_incident_blocked"},
    ],
}


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    ledger_path = tmp_path / "libra-business.db"
    monkeypatch.setattr(libra_app, "PROFIT_LEDGER_FILE", ledger_path)
    return ledger_path


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    path = tmp_path / "growth-autopilot-state.json"
    monkeypatch.setattr(libra_app, "GROWTH_AUTOPILOT_STATE_FILE", path)
    return path


@pytest.fixture
def client(tmp_path, monkeypatch, ledger, state_file):
    monkeypatch.setattr(libra_app, "_profit_now", lambda: NOW)
    return TestClient(libra_app.app)


# ── Verbatim test from the task brief ──────────────────────────────────────

def test_growth_dashboard_separates_plans_from_verified_outcomes(client):
    response = client.get("/growth")
    assert response.status_code == 200
    for label in (
        "Verified revenue", "Portfolio state", "Traffic sources",
        "Verified actions", "Spend and contribution", "Blocked actions",
    ):
        assert label in response.text
    assert "Planned" in response.text
    assert "Executed with evidence" in response.text


# ── GET /growth — honest empty state, real state, no control surface ──────

def test_growth_dashboard_missing_state_file_shows_honest_message(client):
    response = client.get("/growth")
    assert response.status_code == 200
    assert "No Growth Autopilot run recorded yet" in response.text


def test_growth_dashboard_renders_full_state_without_500(client, state_file):
    state_file.write_text(json.dumps(FULL_STATE))
    response = client.get("/growth")
    assert response.status_code == 200
    assert "book-a" in response.text
    assert "book-b" in response.text
    assert "book-c" in response.text
    assert "No Growth Autopilot run recorded yet" not in response.text


def test_growth_dashboard_never_renders_a_control_form(client, state_file):
    state_file.write_text(json.dumps(FULL_STATE))
    response = client.get("/growth")
    body_lower = response.text.lower()
    assert "<form" not in body_lower
    assert "<button" not in body_lower
    assert 'type="submit"' not in body_lower
    assert "run now" not in body_lower
    assert "execute now" not in body_lower


# ── GET /api/growth/state — JSON contract ──────────────────────────────────

def test_api_growth_state_missing_file_returns_honest_empty_state(client):
    response = client.get("/api/growth/state")
    assert response.status_code == 200
    data = response.json()
    assert data["data_available"] is False
    assert data["executed"] == []
    assert data["blocked"] == []
    assert data["plan"]["actions"] == []


def test_api_growth_state_json_contract_separates_plan_from_executed(client, state_file):
    state_file.write_text(json.dumps(FULL_STATE))

    response = client.get("/api/growth/state")

    assert response.status_code == 200
    data = response.json()
    assert data["data_available"] is True
    assert data["phase"] == "organic"
    assert data["day"] == 29

    slugs = {row["slug"]: row["classification"] for row in data["portfolio"]}
    assert slugs == {"book-a": "scale", "book-b": "test"}

    # Plan vs executed are DISTINCT sets — a planned action must never show
    # up as executed, and an executed action carries adapter evidence a
    # plan never has.
    assert data["plan"]["actions"] == [{"slug": "book-b", "kind": "organic_test", "variable": "price"}]
    assert [e["slug"] for e in data["executed"]] == ["book-a"]
    assert data["executed"][0]["evidence"]["confirmation_id"] == "conf-1"
    assert [b["slug"] for b in data["blocked"]] == ["book-c"]

    assert data["readiness"]["mutation_allowed"] is True
    assert data["caps_thb"] == {"daily": 100, "monthly": 3000, "initial_title": 50}
    assert "verified_royalties_usd" in data["verified_revenue"]
    assert "total_events" in data["traffic"]


def test_api_growth_state_reflects_verified_revenue_from_ledger(client, ledger, state_file):
    state_file.write_text(json.dumps(FULL_STATE))
    record_kdp_snapshot(ledger, {
        "observed_at": "2026-07-29T09:00:00+07:00",
        "month": "2026-07",
        "overview": {"royalties_usd": 12.5, "orders_all_types": 40, "kenp": 100},
        "titles": [],
    })

    response = client.get("/api/growth/state")

    assert response.json()["verified_revenue"]["verified_royalties_usd"] == 12.5


# ── Digest pure function: state dict -> plain-language text ───────────────

def test_growth_digest_separates_planned_from_executed_in_plain_language():
    text = libra_app.build_growth_digest(FULL_STATE)

    assert "Planned" in text
    assert "Executed with evidence" in text
    assert "book-b" in text  # the planned action's slug
    assert "book-a" in text  # the executed action's slug
    assert text.index("Planned") < text.index("book-b")
    assert text.index("Executed with evidence") < text.index("book-a")


def test_growth_digest_handles_no_run_yet():
    text = libra_app.build_growth_digest({})
    assert "No" in text
    assert "run" in text.lower()


def test_growth_digest_flags_blocked_mutation_plainly():
    state = {
        **FULL_STATE,
        "readiness": {
            "mutation_allowed": False, "reason": "account_critical_incident",
            "open_incidents": 1, "blocked_slugs": ["book-a"],
        },
    }
    text = libra_app.build_growth_digest(state)
    assert "account_critical_incident" in text
    assert "book-c" in text  # still reports the blocked action


# ── templates/profit.html gains a link to the growth dashboard ────────────

def test_profit_page_links_to_growth_dashboard(client):
    response = client.get("/profit")
    assert response.status_code == 200
    assert "/libra/growth" in response.text or 'href="/growth"' in response.text
