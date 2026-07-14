import json
import sqlite3
from datetime import datetime, timedelta

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
        "gates": {"policy": "open", "freshness": "open", "overview_ingestion": "open",
                  "title_attribution": "open", "cost_completeness": "closed"},
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
        "cost_complete": False,
        "cost_period": "lifetime",
    }
    assert payload["reconciliation"]["unattributed_royalties_usd"] == 0.0
    assert payload["reconciliation"]["data_age_hours"] == 0.0
    assert payload["policy"]["paid_spend_allowed"] is False
    assert payload["policy"]["active_experiment_limit"] == 3
    assert len(payload["experiments"]) == 3
    assert payload["operations"]["status"] == "blocked"
    assert payload["commercial"]["status"] == "not_proven"
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


def test_day_30_requires_fresh_overview_ingestion_not_complete_title_attribution():
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

    assert gap[0]["outcome"] == "passed"
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


def test_profit_dashboard_reports_kpi_actual_vs_plan_periods(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    kpi = client.get("/api/profit/portfolio").json()["kpi_plan"]

    periods = {period["key"]: period for period in kpi["periods"]}
    assert set(periods) == {"daily", "month", "mode"}
    for period in periods.values():
        names = [metric["name"] for metric in period["metrics"]]
        assert names == ["Verified royalties", "Orders / downloads", "KENP", "Profit A · break-even"]
        for metric in period["metrics"]:
            assert set(metric) >= {"actual", "plan", "actual_label", "plan_label", "percent", "status"}
            assert 0 <= metric["percent"] <= 100
    # 90-day plan totals scale down: month = total/3, daily = total/90.
    # The first snapshot is the mode's entry meter-reading: KDP "This Month"
    # is calendar-cumulative, so pre-mode revenue must not count as progress.
    mode = periods["mode"]["metrics"]
    assert mode[0]["actual"] == 0.0 and mode[0]["plan"] == 75.0
    assert mode[1]["plan"] == 360
    month = periods["month"]["metrics"]
    assert month[0]["actual"] == 7.63 and month[0]["plan"] == 25.0
    assert month[1]["actual"] == 252
    # Single snapshot in the month → the daily delta IS the MTD value.
    daily = periods["daily"]["metrics"]
    assert daily[0]["actual"] == 7.63
    assert daily[0]["plan"] < 1.0  # 90-day target averaged per day


def test_mode_kpi_counts_only_revenue_earned_inside_the_window(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    record_kdp_snapshot(tmp_path / "libra-business.db", {
        "observed_at": (NOW + timedelta(days=3)).isoformat(),
        "month": "2026-07",
        "overview": {"royalties_usd": 10.0, "orders_all_types": 291, "kenp": 405},
        "titles": [{"asin": "A", "royalties_usd": 8.49, "orders": 54, "kenp": 26}],
    })

    kpi = client.get("/api/profit/portfolio").json()["kpi_plan"]

    mode = {p["key"]: p for p in kpi["periods"]}["mode"]["metrics"]
    assert mode[0]["actual"] == 2.37   # 10.00 MTD − 7.63 pre-mode baseline
    assert mode[1]["actual"] == 39     # 291 − 252
    assert mode[2]["actual"] == 44     # 405 − 361


def test_daily_kpi_uses_day_over_day_snapshot_delta(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    record_kdp_snapshot(libra_app.PROFIT_LEDGER_FILE, {
        "observed_at": "2026-07-12T09:15:00+07:00",
        "month": "2026-07",
        "overview": {"royalties_usd": 9.13, "orders_all_types": 260, "kenp": 400},
        "titles": [{"asin": "A", "royalties_usd": 9.13, "orders": 64, "kenp": 200}],
    })

    kpi = client.get("/api/profit/portfolio").json()["kpi_plan"]

    daily = {m["name"]: m for m in next(
        p for p in kpi["periods"] if p["key"] == "daily")["metrics"]}
    assert daily["Verified royalties"]["actual"] == 1.50  # 9.13 - 7.63
    assert daily["Orders / downloads"]["actual"] == 8
    assert daily["KENP"]["actual"] == 39
    assert daily["Profit A · break-even"]["status"] == "on_plan"
