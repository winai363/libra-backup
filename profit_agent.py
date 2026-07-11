import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from business_ledger import init_ledger


ACTIVE_STATUSES = {
    "planned",
    "ready",
    "executing",
    "cooldown",
    "evaluating",
    "manual_required",
}

PAID_ACTION_KINDS = {"amazon_ads", "paid_promotion", "paid_acquisition"}

TRANSITIONS = {
    "planned": {"ready"},
    "ready": {"executing", "manual_required"},
    "executing": {"cooldown", "failed", "manual_required"},
    "cooldown": {"evaluating"},
    "evaluating": {"won", "lost", "inconclusive"},
}

APPROVED_EXPERIMENTS = (
    {
        "slug": "adhd-self-help-adults-es",
        "hypothesis": "A focused metadata change can improve verified commercial results.",
        "variable": "metadata",
        "evaluation_kind": "metadata",
    },
    {
        "slug": "ai-augmented-productivity-toolkit",
        "hypothesis": "Separating free activity will clarify verified commercial demand.",
        "variable": "promotion",
        "evaluation_kind": "commercial",
    },
    {
        "slug": "acuarela-para-principiantes-guia-paso-a-paso",
        "hypothesis": "A category change can improve positioning and read-through.",
        "variable": "category",
        "evaluation_kind": "category",
    },
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  hypothesis TEXT NOT NULL,
  variable TEXT NOT NULL,
  evaluation_kind TEXT NOT NULL,
  baseline_json TEXT NOT NULL DEFAULT '{}',
  started_at TEXT NOT NULL,
  earliest_evaluation_at TEXT,
  success_threshold_json TEXT NOT NULL DEFAULT '{}',
  stop_threshold_json TEXT NOT NULL DEFAULT '{}',
  max_direct_cost_usd REAL NOT NULL,
  status TEXT NOT NULL,
  result_json TEXT
);
CREATE TABLE IF NOT EXISTS agent_actions (
  id INTEGER PRIMARY KEY,
  recorded_at TEXT NOT NULL,
  kind TEXT NOT NULL,
  slug TEXT,
  status TEXT NOT NULL,
  action_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  evidence_json TEXT NOT NULL
);
"""


def _init_schema(path: Path) -> None:
    init_ledger(path)
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)


def _experiment_from_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "slug": row["slug"],
        "hypothesis": row["hypothesis"],
        "variable": row["variable"],
        "evaluation_kind": row["evaluation_kind"],
        "baseline": json.loads(row["baseline_json"]),
        "started_at": row["started_at"],
        "earliest_evaluation_at": row["earliest_evaluation_at"],
        "success_threshold": json.loads(row["success_threshold_json"]),
        "stop_threshold": json.loads(row["stop_threshold_json"]),
        "max_direct_cost_usd": row["max_direct_cost_usd"],
        "status": row["status"],
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
    }


def create_initial_experiments(db_path: Path, now: datetime) -> list[dict]:
    _init_schema(db_path)
    started_at = now.isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.executemany(
            """
            INSERT OR IGNORE INTO experiments (
                slug, hypothesis, variable, evaluation_kind, started_at,
                max_direct_cost_usd, status
            ) VALUES (?, ?, ?, ?, ?, 0, 'planned')
            """,
            [
                (
                    item["slug"],
                    item["hypothesis"],
                    item["variable"],
                    item["evaluation_kind"],
                    started_at,
                )
                for item in APPROVED_EXPERIMENTS
            ],
        )
        approved_slugs = [item["slug"] for item in APPROVED_EXPERIMENTS]
        placeholders = ",".join("?" for _ in approved_slugs)
        rows = connection.execute(
            f"SELECT * FROM experiments WHERE slug IN ({placeholders}) ORDER BY id",
            approved_slugs,
        ).fetchall()
    return [_experiment_from_row(row) for row in rows]


def check_policy(action: dict, context: dict) -> tuple[bool, str]:
    if context.get("no_spend") and (
        float(action.get("cost_usd", 0)) > 0
        or action.get("kind") in PAID_ACTION_KINDS
    ):
        return False, "paid actions disabled during 90-day organic mode"

    if (
        action.get("kind") == "start_experiment"
        and int(context.get("active_experiments", 0)) >= 3
    ):
        return False, "active experiment limit reached"

    variables = action.get("variables")
    if variables is not None and len(set(variables)) != 1:
        return False, "experiments may change only one variable"
    active_variable = context.get("active_variable")
    if active_variable and action.get("variable") not in (None, active_variable):
        return False, "experiments may change only one variable"

    if context.get("title_in_cooldown") or action.get("slug") in set(
        context.get("cooldown_slugs", [])
    ):
        return False, "title is already in cooldown"

    return True, "allowed"


def propose_transition(experiment: dict, financials: dict, now: datetime) -> dict:
    status = experiment["status"]
    target = financials.get("target_status")

    if target is None:
        if status == "planned":
            target = "ready"
        elif status == "ready":
            target = "executing"
        elif status == "executing":
            target = "cooldown"
        elif status == "cooldown":
            earliest = experiment.get("earliest_evaluation_at")
            if earliest is None or now < datetime.fromisoformat(earliest):
                return experiment.copy()
            target = "evaluating"
        elif status == "evaluating":
            target = financials.get("outcome")
            if target is None:
                return experiment.copy()
        else:
            return experiment.copy()

    if target not in TRANSITIONS.get(status, set()):
        raise ValueError(f"invalid experiment transition: {status} -> {target}")

    changed = experiment.copy()
    changed["status"] = target
    if status == "executing" and target == "cooldown":
        delay = (
            timedelta(days=14)
            if experiment.get("evaluation_kind") == "commercial"
            else timedelta(hours=72)
        )
        changed["earliest_evaluation_at"] = (now + delay).isoformat()
    return changed


def record_action_result(db_path: Path, action: dict, result: dict) -> dict:
    _init_schema(db_path)
    evidence_keys = (
        "confirmation_id",
        "confirmation_identifier",
        "confirmation_url",
        "url",
        "verified_state_change",
    )
    evidence = {key: result[key] for key in evidence_keys if result.get(key)}
    if result.get("returncode", 0) != 0:
        status = "failed"
    elif evidence:
        status = "executed"
    else:
        status = "manual_required"

    recorded_at = result.get("observed_at") or datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO agent_actions (
                recorded_at, kind, slug, status, action_json, result_json, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recorded_at,
                action["kind"],
                action.get("slug"),
                status,
                json.dumps(action, sort_keys=True),
                json.dumps(result, sort_keys=True),
                json.dumps(evidence, sort_keys=True),
            ),
        )
        action_id = cursor.lastrowid

    return {
        "id": action_id,
        "recorded_at": recorded_at,
        "kind": action["kind"],
        "slug": action.get("slug"),
        "status": status,
        "evidence": evidence,
    }
