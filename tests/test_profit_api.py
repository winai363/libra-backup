import json
import sqlite3
from datetime import datetime

from fastapi.testclient import TestClient

import app as libra_app
import profit_tracker
from business_ledger import record_kdp_snapshot
from profit_agent import create_initial_experiments


NOW = datetime.fromisoformat("2026-07-11T09:15:09+07:00")
STARTED_AT = datetime.fromisoformat("2026-06-01T09:15:09+07:00")


def _client(tmp_path, monkeypatch):
    ledger = tmp_path / "libra-business.db"
    state = tmp_path / "profit-agent-state.json"
    kdp = tmp_path / "kdp"
    kdp.mkdir()
    record_kdp_snapshot(ledger, {
        "observed_at": NOW.isoformat(),
        "month": "2026-07",
        "overview": {"royalties_usd": 7.63, "orders_all_types": 252, "kenp": 361},
        "titles": [{"asin": "A", "royalties_usd": 7.63, "orders": 60, "kenp": 173}],
    })
    experiments = create_initial_experiments(ledger, STARTED_AT)
    state.write_text(json.dumps({
        "generated_at": NOW.isoformat(),
        "mode_started_at": STARTED_AT.isoformat(),
        "mode": "live",
        "gates": {"financial_data": "open", "reconciliation": "open", "policy": "open"},
        "gate_reason": "allowed",
        "experiments": experiments,
        "session_token": "must-not-leak",
        "raw_session_data": {"cookie": "must-not-leak"},
    }))
    monkeypatch.setattr(libra_app, "PROFIT_LEDGER_FILE", ledger)
    monkeypatch.setattr(libra_app, "PROFIT_AGENT_STATE_FILE", state)
    monkeypatch.setattr(libra_app, "KDP_DIR", kdp)
    monkeypatch.setattr(libra_app, "_profit_now", lambda: NOW)
    monkeypatch.setattr(profit_tracker, "KDP_DIR", kdp)
    monkeypatch.setattr(profit_tracker, "LEDGER_FILE", ledger)
    return TestClient(libra_app.app)


def test_profit_api_separates_business_truth_and_checkpoints(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    payload = client.get("/api/profit/portfolio").json()

    assert payload["financials"] == {
        "verified_royalties_usd": 7.63,
        "direct_costs_usd": 0.0,
        "contribution_profit_usd": 7.63,
        "fully_loaded_net_profit_usd": None,
        "overhead_complete": False,
    }
    assert payload["reconciliation"]["unattributed_royalties_usd"] == 0.0
    assert payload["reconciliation"]["data_age_hours"] == 0.0
    assert payload["policy"]["paid_spend_allowed"] is False
    assert payload["policy"]["active_experiment_limit"] == 3
    assert len(payload["experiments"]) == 3
    assert payload["operations"]["status"] == "ready"
    assert payload["commercial"]["status"] == "positive_contribution"
    assert [item["day"] for item in payload["checkpoints"]] == [30, 60, 90]
    assert payload["checkpoints"][0]["outcome"] == "passed"
    assert payload["checkpoints"][1]["outcome"] == "pending"
    assert payload["checkpoints"][2]["outcome"] == "pending"


def test_profit_agent_api_returns_sanitized_latest_state(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    payload = client.get("/api/profit/agent").json()

    assert set(payload) == {
        "generated_at", "mode_started_at", "mode", "gates", "gate_reason", "experiments"
    }
    assert "must-not-leak" not in json.dumps(payload)


def test_primary_dashboard_exposes_verified_royalties_not_estimated_revenue(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    sales = client.get("/api/dashboard/overview").json()["sales"]

    assert sales["verified_royalties_mtd_usd"] == 7.63
    assert "revenue_30d_usd" not in sales
    assert "estimated_revenue_30d_usd" not in sales


def test_due_checkpoints_require_repeatable_contribution_evidence():
    checkpoints = libra_app._checkpoint_outcomes(
        datetime.fromisoformat("2026-06-01T09:15:09+07:00"),
        datetime.fromisoformat("2026-09-10T09:15:09+07:00"),
        {
            "contribution_profit_usd": 7.63,
            "overhead_complete": False,
            "fully_loaded_net_profit_usd": None,
        },
        {"snapshot_count": 3, "unattributed_royalties_usd": 0.79},
        [{"result": {"positive_contribution_windows": 1}}],
    )

    assert checkpoints[1]["outcome"] == "missed"
    assert checkpoints[2]["outcome"] == "missed"
    assert checkpoints[2]["missing_plan_inputs"] == [
        "conversion_rate", "royalty_per_paid_order", "production_capacity", "complete_overhead"
    ]


def test_day_30_requires_fresh_reconciliation_within_one_cent():
    financials = {
        "contribution_profit_usd": 7.63,
        "overhead_complete": False,
        "fully_loaded_net_profit_usd": None,
    }
    started = datetime.fromisoformat("2026-06-01T09:15:09+07:00")
    due = datetime.fromisoformat("2026-07-02T09:15:09+07:00")

    gap = libra_app._checkpoint_outcomes(
        started, due, financials,
        {"snapshot_count": 1, "unattributed_royalties_usd": 0.02, "fresh": True},
        [],
    )
    stale = libra_app._checkpoint_outcomes(
        started, due, financials,
        {"snapshot_count": 1, "unattributed_royalties_usd": 0.0, "fresh": False},
        [],
    )
    within_tolerance = libra_app._checkpoint_outcomes(
        started, due, financials,
        {"snapshot_count": 1, "unattributed_royalties_usd": 0.01, "fresh": True},
        [],
    )

    assert gap[0]["outcome"] == "missed"
    assert stale[0]["outcome"] == "missed"
    assert within_tolerance[0]["outcome"] == "passed"


def test_profit_api_surfaces_four_active_experiments_without_concealing_violation(
    tmp_path, monkeypatch
):
    client = _client(tmp_path, monkeypatch)
    with sqlite3.connect(libra_app.PROFIT_LEDGER_FILE) as connection:
        connection.execute("UPDATE experiments SET status = 'won' WHERE id = 1")
        for suffix in range(2):
            connection.execute(
                """
                INSERT INTO experiments (
                    slug, hypothesis, variable, evaluation_kind, started_at,
                    max_direct_cost_usd, status
                ) VALUES (?, 'extra', 'metadata', 'metadata', ?, 0, 'ready')
                """,
                (f"extra-{suffix}", NOW.isoformat()),
            )

    payload = client.get("/api/profit/portfolio").json()

    assert len(payload["experiments"]) == 3
    assert all(item["status"] != "won" for item in payload["experiments"])
    assert payload["operations"]["active_experiment_count"] == 4
    assert payload["policy"]["active_experiment_limit_violated"] is True


def test_checkpoint_anchor_is_stable_without_experiment_registry(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.db"
    state = tmp_path / "state.json"
    kdp = tmp_path / "kdp"
    kdp.mkdir()
    record_kdp_snapshot(ledger, {
        "observed_at": NOW.isoformat(),
        "month": "2026-07",
        "overview": {"royalties_usd": 0, "orders_all_types": 0, "kenp": 0},
        "titles": [],
    })
    state.write_text(json.dumps({
        "generated_at": NOW.isoformat(),
        "mode_started_at": STARTED_AT.isoformat(),
        "mode": "live",
        "gates": {},
        "experiments": [],
    }))
    monkeypatch.setattr(libra_app, "PROFIT_LEDGER_FILE", ledger)
    monkeypatch.setattr(libra_app, "PROFIT_AGENT_STATE_FILE", state)
    monkeypatch.setattr(libra_app, "KDP_DIR", kdp)
    monkeypatch.setattr(profit_tracker, "KDP_DIR", kdp)
    monkeypatch.setattr(profit_tracker, "LEDGER_FILE", ledger)

    monkeypatch.setattr(libra_app, "_profit_now", lambda: NOW)
    first = libra_app.build_profit_dashboard()["checkpoints"]
    monkeypatch.setattr(libra_app, "_profit_now", lambda: NOW.replace(day=20))
    second = libra_app.build_profit_dashboard()["checkpoints"]

    assert [item["date"] for item in first] == [item["date"] for item in second]
    assert first[0]["date"] == "2026-07-01"


def test_missing_mode_anchor_is_explicit_instead_of_using_request_date(
    tmp_path, monkeypatch
):
    ledger = tmp_path / "ledger.db"
    state = tmp_path / "missing-state.json"
    kdp = tmp_path / "kdp"
    kdp.mkdir()
    record_kdp_snapshot(ledger, {
        "observed_at": NOW.isoformat(),
        "month": "2026-07",
        "overview": {"royalties_usd": 0, "orders_all_types": 0, "kenp": 0},
        "titles": [],
    })
    monkeypatch.setattr(libra_app, "PROFIT_LEDGER_FILE", ledger)
    monkeypatch.setattr(libra_app, "PROFIT_AGENT_STATE_FILE", state)
    monkeypatch.setattr(libra_app, "KDP_DIR", kdp)
    monkeypatch.setattr(profit_tracker, "KDP_DIR", kdp)
    monkeypatch.setattr(profit_tracker, "LEDGER_FILE", ledger)

    monkeypatch.setattr(libra_app, "_profit_now", lambda: NOW)
    first = libra_app.build_profit_dashboard()["checkpoints"]
    monkeypatch.setattr(libra_app, "_profit_now", lambda: NOW.replace(day=20))
    second = libra_app.build_profit_dashboard()["checkpoints"]

    assert first == second
    assert all(item["date"] is None and item["outcome"] == "not_started" for item in first)


def test_terminal_history_drives_day_60_evidence_and_anchor(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with sqlite3.connect(libra_app.PROFIT_LEDGER_FILE) as connection:
        connection.execute(
            "UPDATE experiments SET status = 'won', result_json = ?",
            (json.dumps({"positive_contribution_windows": 2}),),
        )
    state = json.loads(libra_app.PROFIT_AGENT_STATE_FILE.read_text())
    state.pop("mode_started_at")
    libra_app.PROFIT_AGENT_STATE_FILE.write_text(json.dumps(state))

    monkeypatch.setattr(
        libra_app,
        "_profit_now",
        lambda: datetime.fromisoformat("2026-08-05T09:15:09+07:00"),
    )
    first = client.get("/api/profit/portfolio").json()
    monkeypatch.setattr(
        libra_app,
        "_profit_now",
        lambda: datetime.fromisoformat("2026-08-10T09:15:09+07:00"),
    )
    second = client.get("/api/profit/portfolio").json()

    assert first["experiments"] == []
    assert first["operations"]["active_experiment_count"] == 0
    assert first["checkpoints"][1]["outcome"] == "passed"
    assert first["checkpoints"][1]["date"] == "2026-07-31"
    assert [item["date"] for item in first["checkpoints"]] == [
        item["date"] for item in second["checkpoints"]
    ]
