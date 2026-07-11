import json
import hashlib
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
ZERO_COST_ACTION_KINDS = {
    "start_experiment", "metadata_update", "category_update", "free_promo",
    "free_post", "evaluate_experiment", "internal_transition",
}

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
        "action": {"kind": "metadata_update", "field": "title", "proposed_value": "ADHD en Adultos: Guia Practica de Autoayuda", "cost_usd": 0},
    },
    {
        "slug": "ai-augmented-productivity-toolkit",
        "hypothesis": "Separating free activity will clarify verified commercial demand.",
        "variable": "promotion",
        "evaluation_kind": "commercial",
        "action": {"kind": "free_promo", "field": "promotion_window", "proposed_value": "2-day KDP Select free promotion", "cost_usd": 0},
    },
    {
        "slug": "acuarela-para-principiantes-guia-paso-a-paso",
        "hypothesis": "A category change can improve positioning and read-through.",
        "variable": "category",
        "evaluation_kind": "category",
        "action": {"kind": "category_update", "field": "category", "proposed_value": "Watercolor Painting", "cost_usd": 0},
    },
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS policy_modes (
  name TEXT PRIMARY KEY, started_at TEXT NOT NULL, ends_at TEXT NOT NULL,
  enabled INTEGER NOT NULL, allowed_action_kinds_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiments (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL,
  asin TEXT,
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
CREATE TABLE IF NOT EXISTS action_conflicts (
  id INTEGER PRIMARY KEY, action_key TEXT NOT NULL, attempt INTEGER NOT NULL,
  existing_action_id INTEGER NOT NULL, conflicting_action_json TEXT NOT NULL,
  conflicting_result_json TEXT NOT NULL, recorded_at TEXT NOT NULL,
  UNIQUE(action_key, attempt, conflicting_action_json, conflicting_result_json)
);
"""


def ensure_no_spend_mode(db_path: Path, started_at: datetime, days: int = 90) -> dict:
    _init_schema(db_path)
    ends_at = started_at + timedelta(days=days)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO policy_modes VALUES ('organic_90_day', ?, ?, 1, ?)",
            (started_at.isoformat(), ends_at.isoformat(), json.dumps(sorted(ZERO_COST_ACTION_KINDS))),
        )
        row = connection.execute("SELECT started_at, ends_at, enabled, allowed_action_kinds_json FROM policy_modes WHERE name='organic_90_day'").fetchone()
    return {"name": "organic_90_day", "started_at": row[0], "ends_at": row[1], "enabled": bool(row[2]), "allowed_action_kinds": json.loads(row[3]), "paid_spend_allowed": False}


def read_policy_mode(db_path: Path) -> dict | None:
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(db_path) as connection:
            row = connection.execute("SELECT started_at, ends_at, enabled, allowed_action_kinds_json FROM policy_modes WHERE name='organic_90_day'").fetchone()
    except sqlite3.Error:
        return None
    return None if not row else {"name": "organic_90_day", "started_at": row[0], "ends_at": row[1], "enabled": bool(row[2]), "allowed_action_kinds": json.loads(row[3]), "paid_spend_allowed": False}


def _init_schema(path: Path) -> None:
    init_ledger(path)
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        indexes = connection.execute("PRAGMA index_list(experiments)").fetchall()
        if any(row[2] and row[1].startswith("sqlite_autoindex") for row in indexes):
            connection.execute("ALTER TABLE experiments RENAME TO experiments_legacy_unique")
            connection.executescript(SCHEMA.split("CREATE TABLE IF NOT EXISTS agent_actions")[0])
            old_cols = {r[1] for r in connection.execute("PRAGMA table_info(experiments_legacy_unique)")}
            new_cols = [r[1] for r in connection.execute("PRAGMA table_info(experiments)")]
            common = [c for c in new_cols if c in old_cols]
            columns = ",".join(common)
            connection.execute(f"INSERT INTO experiments({columns}) SELECT {columns} FROM experiments_legacy_unique")
            connection.execute("DROP TABLE experiments_legacy_unique")
        experiment_columns = {row[1] for row in connection.execute("PRAGMA table_info(experiments)")}
        for name, sql_type in (
            ("action_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("baseline_snapshot_id", "INTEGER"),
            ("cycle_key", "TEXT"),
            ("asin", "TEXT"),
            ("action_executed_at", "TEXT"),
        ):
            if name not in experiment_columns:
                connection.execute(f"ALTER TABLE experiments ADD COLUMN {name} {sql_type}")
        action_columns = {row[1] for row in connection.execute("PRAGMA table_info(agent_actions)")}
        for name, sql_type in (
            ("experiment_id", "INTEGER"), ("action_key", "TEXT"),
            ("attempt", "INTEGER NOT NULL DEFAULT 1"), ("terminal", "INTEGER NOT NULL DEFAULT 0"),
            ("action_hash", "TEXT"), ("result_hash", "TEXT"),
        ):
            if name not in action_columns:
                connection.execute(f"ALTER TABLE agent_actions ADD COLUMN {name} {sql_type}")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_action_key_attempt ON agent_actions(action_key, attempt) WHERE action_key IS NOT NULL")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_experiment_cycle_key ON experiments(cycle_key) WHERE cycle_key IS NOT NULL")


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
        "action": json.loads(row["action_json"]) if "action_json" in row.keys() else {},
        "baseline_snapshot_id": row["baseline_snapshot_id"] if "baseline_snapshot_id" in row.keys() else None,
        "cycle_key": row["cycle_key"] if "cycle_key" in row.keys() else None,
        "asin": row["asin"] if "asin" in row.keys() else None,
        "action_executed_at": row["action_executed_at"] if "action_executed_at" in row.keys() else None,
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
                max_direct_cost_usd, status, action_json, success_threshold_json,
                stop_threshold_json, cycle_key
            ) VALUES (?, ?, ?, ?, ?, 0, 'planned', ?, ?, ?, ?)
            """,
            [
                (
                    item["slug"],
                    item["hypothesis"],
                    item["variable"],
                    item["evaluation_kind"],
                    started_at,
                    json.dumps(item["action"], sort_keys=True),
                    json.dumps({"min_contribution_delta_usd": 0.01}),
                    json.dumps({"max_contribution_delta_usd": -0.01}),
                    f"initial:{item['slug']}",
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


def create_experiment(db_path: Path, *, slug: str, asin: str, variable: str,
                      action: dict, now: datetime, hypothesis: str = "Controlled one-variable cycle") -> dict:
    _init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        overlap = connection.execute(
            f"SELECT 1 FROM experiments WHERE slug=? AND status IN ({','.join('?' for _ in ACTIVE_STATUSES)})",
            (slug, *sorted(ACTIVE_STATUSES)),
        ).fetchone()
        if overlap:
            raise ValueError(f"active experiment already exists for {slug}")
        sequence = connection.execute("SELECT COUNT(*) FROM experiments WHERE slug=?", (slug,)).fetchone()[0] + 1
        cycle_key = f"{slug}:cycle:{sequence}"
        cursor = connection.execute(
            "INSERT INTO experiments(slug, asin, hypothesis, variable, evaluation_kind, started_at, max_direct_cost_usd, status, action_json, success_threshold_json, stop_threshold_json, cycle_key) VALUES (?, ?, ?, ?, ?, ?, 0, 'planned', ?, ?, ?, ?)",
            (slug, asin, hypothesis, variable, variable, now.isoformat(), json.dumps(action, sort_keys=True),
             json.dumps({"min_contribution_delta_usd": .01}), json.dumps({"max_contribution_delta_usd": -.01}), cycle_key),
        )
        row = connection.execute("SELECT * FROM experiments WHERE id=?", (cursor.lastrowid,)).fetchone()
    return _experiment_from_row(row)


def title_financial_boundary(db_path: Path, asin: str, snapshot_id: int) -> dict:
    """Return cumulative title royalties and costs as-of an immutable snapshot."""
    _init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        boundary = connection.execute("SELECT observed_at FROM kdp_snapshots WHERE id=?", (snapshot_id,)).fetchone()
        if not boundary:
            raise ValueError("unknown snapshot boundary")
        observed_at = boundary[0]
        monthly = connection.execute(
            "SELECT s.id, a.royalties_usd FROM kdp_snapshots s LEFT JOIN kdp_title_attribution a ON a.snapshot_id=s.id AND a.asin=? WHERE s.observed_at<=? AND s.id=(SELECT id FROM kdp_snapshots WHERE month=s.month AND observed_at<=? ORDER BY observed_at DESC,id DESC LIMIT 1)",
            (asin, observed_at, observed_at),
        ).fetchall()
        complete = bool(monthly) and all(row[1] is not None for row in monthly)
        royalties = sum(float(row[1] or 0) for row in monthly)
        slug_row = connection.execute("SELECT slug FROM experiments WHERE asin=? ORDER BY id DESC LIMIT 1", (asin,)).fetchone()
        slug = slug_row[0] if slug_row else None
        manual = connection.execute("SELECT COALESCE(SUM(amount_usd),0) FROM direct_costs WHERE slug=? AND incurred_at<=? AND source_key NOT LIKE 'cost-report:%'", (slug, observed_at)).fetchone()[0] if slug else 0
        # A production cost report is cumulative lifetime cost incurred before the
        # title went live. Apply the latest verified total to every observation
        # boundary rather than pretending a later ingestion date is a new cost.
        report = connection.execute("SELECT cumulative_amount_usd FROM cost_report_versions WHERE slug=? ORDER BY observed_at DESC,id DESC LIMIT 1", (slug,)).fetchone() if slug else None
        legacy = connection.execute("SELECT amount_usd FROM direct_costs WHERE slug=? AND incurred_at<=? AND source_key LIKE 'cost-report:%' ORDER BY id DESC LIMIT 1", (slug, observed_at)).fetchone() if slug and not report else None
        cost = float(manual) + (float(report[0]) if report else float(legacy[0]) if legacy else 0)
        inventory = connection.execute("SELECT status FROM cost_inventory WHERE slug=?", (slug,)).fetchone() if slug else None
        cost_complete = bool(inventory and inventory[0] == "verified") or cost > 0
    return {"snapshot_id": snapshot_id, "observed_at": observed_at, "asin": asin,
            "royalties_usd": round(royalties, 2), "direct_costs_usd": round(cost, 2),
            "contribution_profit_usd": round(royalties-cost, 2),
            "attribution_complete": complete, "cost_complete": cost_complete,
            "complete": complete and cost_complete}


def check_policy(action: dict, context: dict) -> tuple[bool, str]:
    policy = context.get("policy")
    if policy is not None:
        now = context.get("now")
        if not policy.get("enabled"):
            return False, "persisted organic policy is disabled"
        if not isinstance(now, datetime):
            return False, "policy evaluation time is required"
        if now < datetime.fromisoformat(policy["started_at"]) or now >= datetime.fromisoformat(policy["ends_at"]):
            return False, "persisted organic policy is outside its active window"
        allowed_kinds = set(policy.get("allowed_action_kinds") or [])
        if action.get("kind") not in allowed_kinds:
            return False, "action type is not in persisted policy allowlist"
        no_spend = True
    else:
        no_spend = context.get("no_spend")
    if no_spend:
        if action.get("kind") in PAID_ACTION_KINDS:
            return False, "paid actions disabled during 90-day organic mode"
        if policy is None and action.get("kind") not in ZERO_COST_ACTION_KINDS:
            return False, "action type is not allowed during 90-day organic mode"
        if "cost_usd" not in action:
            return False, "explicit zero cost is required during 90-day organic mode"
        try:
            cost = float(action["cost_usd"])
        except (TypeError, ValueError):
            return False, "valid zero cost is required during 90-day organic mode"
        if cost != 0 or action.get("kind") in PAID_ACTION_KINDS:
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
            if not experiment.get("action"):
                return experiment.copy()
            target = "executing"
        elif status == "executing":
            evidence = financials.get("external_action") or {}
            if not (
                evidence.get("status") == "executed"
                and evidence.get("before_snapshot_id") is not None
                and evidence.get("after_snapshot_id") is not None
                and isinstance(evidence.get("before"), dict)
                and isinstance(evidence.get("after"), dict)
                and evidence["before"] != evidence["after"]
            ):
                return experiment.copy()
            target = "cooldown"
        elif status == "cooldown":
            target = "evaluating"
        elif status == "evaluating":
            target = financials.get("outcome")
            if target is None:
                return experiment.copy()
        else:
            return experiment.copy()

    if status == "cooldown" and target == "evaluating":
        earliest = experiment.get("earliest_evaluation_at")
        if earliest is None or now < datetime.fromisoformat(earliest):
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


def evaluate_experiment(experiment: dict, observation: dict, snapshot_ids: list[int], *, observed_at: datetime | None = None) -> dict:
    """Evaluate one frozen lifetime-contribution observation deterministically."""
    baseline_data = experiment.get("baseline") or {}
    baseline = float(baseline_data.get("contribution_profit_usd", 0))
    current = float(observation.get("contribution_profit_usd", 0))
    delta = round(current - baseline, 2)
    complete = bool(baseline_data.get("complete", baseline_data.get("attribution_complete", True)) and observation.get("complete", observation.get("attribution_complete")) and observation.get("cost_complete", True))
    success = float((experiment.get("success_threshold") or {}).get("min_contribution_delta_usd", 0.01))
    stop = float((experiment.get("stop_threshold") or {}).get("max_contribution_delta_usd", -0.01))
    if not complete:
        outcome = "inconclusive"
    elif delta >= success:
        outcome = "won"
    elif delta <= stop:
        outcome = "lost"
    else:
        outcome = "inconclusive"
    end = observed_at.isoformat() if isinstance(observed_at, datetime) else observation.get("observed_at")
    start = experiment.get("action_executed_at") or baseline_data.get("observed_at")
    window = {"start_at": start, "end_at": end, "royalties_usd": round(float(observation.get("royalties_usd", 0))-float(baseline_data.get("royalties_usd", 0)), 2),
              "direct_costs_usd": round(float(observation.get("direct_costs_usd", 0))-float(baseline_data.get("direct_costs_usd", 0)), 2),
              "contribution_profit_usd": delta, "complete": complete, "source_snapshot_ids": list(snapshot_ids)}
    positive_windows = 1 if complete and window["contribution_profit_usd"] > 0 and start and end and start < end else 0
    return {
        "outcome": outcome, "period": "lifetime", "baseline_contribution_profit_usd": baseline,
        "observed_contribution_profit_usd": current, "contribution_delta_usd": delta,
        "source_snapshot_ids": list(snapshot_ids), "attribution_complete": complete,
        "observation_windows": [window], "positive_contribution_windows": positive_windows,
    }


def classify_action_result(result: dict) -> tuple[str, dict]:
    evidence = {}
    for key in ("confirmation_id", "external_url"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            evidence[key] = value
    state_change = result.get("verified_state_change")
    if (
        isinstance(state_change, dict)
        and isinstance(state_change.get("before"), dict)
        and isinstance(state_change.get("after"), dict)
        and state_change["before"] != state_change["after"]
        and state_change.get("before_snapshot_id") is not None
        and state_change.get("after_snapshot_id") is not None
    ):
        evidence["verified_state_change"] = state_change
    if result.get("returncode", 0) != 0:
        status = "failed"
    elif evidence:
        status = "executed"
    else:
        status = "manual_required"
    return status, evidence


def create_pending_action(db_path: Path, experiment: dict, now: datetime) -> dict:
    _init_schema(db_path)
    action = {**experiment["action"], "slug": experiment["slug"], "experiment_id": experiment["id"],
              "action_key": f"experiment:{experiment['id']}:{experiment['cycle_key']}", "attempt": 1}
    payload = json.dumps(action, sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("SELECT id,status FROM agent_actions WHERE action_key=? AND attempt=1", (action["action_key"],)).fetchone()
        if row:
            return {"id": row[0], "status": row[1], **action}
        cursor = connection.execute(
            "INSERT INTO agent_actions(recorded_at,kind,slug,status,action_json,result_json,evidence_json,experiment_id,action_key,attempt,terminal,action_hash,result_hash) VALUES (?,?,?,'pending',?,'{}','{}',?,?,?,0,?,NULL)",
            (now.isoformat(), action["kind"], action["slug"], payload, experiment["id"], action["action_key"], 1, digest),
        )
    return {"database_id": cursor.lastrowid, "status": "pending", **action}


def latest_experiment_action(db_path: Path, experiment_id: int) -> dict | None:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM agent_actions WHERE experiment_id=? AND kind!='internal_transition' ORDER BY attempt DESC,id DESC LIMIT 1", (experiment_id,)).fetchone()
    if not row:
        return None
    return {**dict(row), "action": json.loads(row["action_json"]), "result": json.loads(row["result_json"]), "evidence": json.loads(row["evidence_json"])}


def record_action_result(db_path: Path, action: dict, result: dict) -> dict:
    _init_schema(db_path)
    status, evidence = classify_action_result(result)
    if action.get("kind") == "internal_transition" and isinstance(result.get("internal_transition"), dict):
        status = "recorded"
        evidence = {"internal_transition": result["internal_transition"]}

    recorded_at = result.get("observed_at") or datetime.now(timezone.utc).isoformat()
    action_key = action.get("action_key") or f"{action.get('experiment_id', 'none')}:{action['kind']}:{action.get('slug', '')}"
    attempt = int(action.get("attempt", 1))
    if attempt < 1 or attempt > 3:
        raise ValueError("action attempt must be between 1 and 3")
    # Database wrapper fields are not part of the immutable action identity.
    action_payload = {k: v for k, v in action.items() if k not in {"id", "database_id", "status"}}
    action_json = json.dumps(action_payload, sort_keys=True)
    result_json = json.dumps(result, sort_keys=True)
    action_hash = hashlib.sha256(action_json.encode()).hexdigest()
    result_hash = hashlib.sha256(result_json.encode()).hexdigest()
    conflict = False
    with sqlite3.connect(db_path) as connection:
        terminal = connection.execute("SELECT id FROM agent_actions WHERE action_key=? AND terminal=1 AND attempt<?", (action_key, attempt)).fetchone()
        if terminal:
            raise ValueError("cannot retry terminal action")
        existing = connection.execute(
            "SELECT id, recorded_at, kind, slug, status, evidence_json, action_hash, result_hash FROM agent_actions WHERE action_key = ? AND attempt = ?",
            (action_key, attempt),
        ).fetchone()
        if existing:
            if existing[4] == "pending" and (existing[6] is None or existing[6] == action_hash):
                connection.execute("UPDATE agent_actions SET recorded_at=?,status=?,result_json=?,evidence_json=?,terminal=?,action_hash=?,result_hash=? WHERE id=?",
                    (recorded_at,status,result_json,json.dumps(evidence,sort_keys=True),int(status in {"executed","manual_required"} or attempt>=3),action_hash,result_hash,existing[0]))
                action_id = existing[0]
            elif existing[6] == action_hash and existing[7] == result_hash:
                return {"id": existing[0], "recorded_at": existing[1], "kind": existing[2], "slug": existing[3], "status": existing[4], "evidence": json.loads(existing[5])}
            else:
                connection.execute("INSERT OR IGNORE INTO action_conflicts(action_key,attempt,existing_action_id,conflicting_action_json,conflicting_result_json,recorded_at) VALUES (?,?,?,?,?,?)",
                    (action_key,attempt,existing[0],action_json,result_json,recorded_at))
                conflict = True
        else:
            cursor = connection.execute(
                """
                INSERT INTO agent_actions (
                    recorded_at, kind, slug, status, action_json, result_json, evidence_json,
                    experiment_id, action_key, attempt, terminal, action_hash, result_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (recorded_at, action["kind"], action.get("slug"), status, action_json, result_json,
                 json.dumps(evidence, sort_keys=True), action.get("experiment_id"), action_key, attempt,
                 int(status in {"executed", "manual_required", "recorded"} or attempt >= 3), action_hash, result_hash),
            )
            action_id = cursor.lastrowid
    if conflict:
        raise ValueError("conflicting action replay")

    return {
        "id": action_id,
        "recorded_at": recorded_at,
        "kind": action["kind"],
        "slug": action.get("slug"),
        "status": status,
        "evidence": evidence,
    }
