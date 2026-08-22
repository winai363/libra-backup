import hashlib
import json
import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS kdp_snapshots (
  id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL UNIQUE, month TEXT NOT NULL,
  royalties_usd REAL NOT NULL, orders_all_types INTEGER NOT NULL, kenp INTEGER NOT NULL,
  raw_json TEXT NOT NULL, source_key TEXT, content_hash TEXT
);
CREATE TABLE IF NOT EXISTS kdp_title_attribution (
  snapshot_id INTEGER NOT NULL, asin TEXT NOT NULL, royalties_usd REAL NOT NULL,
  orders_count INTEGER NOT NULL, kenp INTEGER NOT NULL,
  PRIMARY KEY (snapshot_id, asin), FOREIGN KEY(snapshot_id) REFERENCES kdp_snapshots(id)
);
CREATE TABLE IF NOT EXISTS snapshot_conflicts (
  id INTEGER PRIMARY KEY, source_key TEXT NOT NULL, original_snapshot_id INTEGER NOT NULL,
  conflicting_hash TEXT NOT NULL, conflicting_json TEXT NOT NULL,
  recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source_key, conflicting_hash)
);
CREATE TABLE IF NOT EXISTS direct_costs (
  id INTEGER PRIMARY KEY, incurred_at TEXT NOT NULL, slug TEXT, category TEXT NOT NULL,
  amount_usd REAL NOT NULL, source_key TEXT NOT NULL UNIQUE, content_hash TEXT
);
CREATE TABLE IF NOT EXISTS cost_inventory (
  slug TEXT PRIMARY KEY, status TEXT NOT NULL, source_key TEXT, checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cost_report_versions (
  id INTEGER PRIMARY KEY, logical_key TEXT NOT NULL, slug TEXT NOT NULL,
  observed_at TEXT NOT NULL, semantic_hash TEXT NOT NULL, cumulative_amount_usd REAL NOT NULL,
  raw_json TEXT NOT NULL, UNIQUE(logical_key, semantic_hash)
);
"""

GROWTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS growth_evidence (
  id INTEGER PRIMARY KEY, source_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
  slug TEXT, observed_at TEXT NOT NULL, fresh_until TEXT NOT NULL,
  confidence REAL NOT NULL, payload_json TEXT NOT NULL, content_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS growth_plans (
  id INTEGER PRIMARY KEY, action_key TEXT NOT NULL UNIQUE, planned_at TEXT NOT NULL,
  phase TEXT NOT NULL, status TEXT NOT NULL, plan_json TEXT NOT NULL,
  content_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hub_events (
  id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE, occurred_at TEXT NOT NULL,
  slug TEXT NOT NULL, campaign TEXT NOT NULL, event_kind TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS growth_incidents (
  id INTEGER PRIMARY KEY, incident_key TEXT NOT NULL UNIQUE, opened_at TEXT NOT NULL,
  severity TEXT NOT NULL, scope TEXT NOT NULL, detail_json TEXT NOT NULL,
  resolved_at TEXT
);
"""


COMMERCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS commerce_events (
  id INTEGER PRIMARY KEY,
  provider TEXT NOT NULL,
  event_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  received_at TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN ('test','live')),
  verification_state TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  processing_state TEXT NOT NULL DEFAULT 'received',
  error_code TEXT,
  sanitized_payload_json TEXT NOT NULL,
  UNIQUE(provider, event_id)
);
CREATE TABLE IF NOT EXISTS commerce_event_conflicts (
  id INTEGER PRIMARY KEY,
  provider TEXT NOT NULL,
  event_id TEXT NOT NULL,
  original_event_id INTEGER NOT NULL,
  conflicting_hash TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  UNIQUE(provider, event_id, conflicting_hash)
);
CREATE TABLE IF NOT EXISTS commerce_products (
  slug TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  provider_product_id TEXT NOT NULL,
  status TEXT NOT NULL,
  currency TEXT NOT NULL,
  price_minor INTEGER NOT NULL CHECK(price_minor >= 0),
  updated_at TEXT NOT NULL,
  UNIQUE(provider, provider_product_id)
);
CREATE TABLE IF NOT EXISTS commerce_orders (
  provider TEXT NOT NULL,
  provider_order_id TEXT NOT NULL,
  slug TEXT,
  status TEXT NOT NULL,
  currency TEXT NOT NULL,
  gross_minor INTEGER NOT NULL CHECK(gross_minor >= 0),
  discount_minor INTEGER NOT NULL DEFAULT 0 CHECK(discount_minor >= 0),
  tax_minor INTEGER DEFAULT 0 CHECK(tax_minor >= 0),
  payhip_fee_minor INTEGER,
  stripe_fee_minor INTEGER,
  net_minor INTEGER,
  provider_payment_id TEXT,
  customer_country TEXT,
  attribution_key TEXT,
  ordered_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(provider, provider_order_id)
);
CREATE TABLE IF NOT EXISTS commerce_refunds (
  provider TEXT NOT NULL,
  provider_refund_id TEXT NOT NULL,
  provider_order_id TEXT,
  provider_payment_id TEXT,
  amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
  currency TEXT NOT NULL,
  status TEXT NOT NULL,
  reason_code TEXT,
  occurred_at TEXT NOT NULL,
  PRIMARY KEY(provider, provider_refund_id)
);
CREATE TABLE IF NOT EXISTS stripe_balance_transactions (
  balance_transaction_id TEXT PRIMARY KEY,
  source_id TEXT,
  type TEXT NOT NULL,
  amount_minor INTEGER NOT NULL,
  fee_minor INTEGER NOT NULL,
  net_minor INTEGER NOT NULL,
  currency TEXT NOT NULL,
  available_on TEXT
);
CREATE TABLE IF NOT EXISTS commerce_payouts (
  provider_payout_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  currency TEXT NOT NULL,
  amount_minor INTEGER NOT NULL,
  arrival_date TEXT,
  error_code TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commerce_payout_items (
  provider_payout_id TEXT NOT NULL,
  balance_transaction_id TEXT NOT NULL,
  PRIMARY KEY(provider_payout_id, balance_transaction_id)
);
CREATE TABLE IF NOT EXISTS commerce_incidents (
  incident_key TEXT PRIMARY KEY,
  opened_at TEXT NOT NULL,
  severity TEXT NOT NULL,
  scope TEXT NOT NULL,
  error_code TEXT NOT NULL,
  detail_json TEXT NOT NULL,
  resolved_at TEXT
);
"""


def _canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _hash(value: dict) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def init_ledger(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.executescript(GROWTH_SCHEMA)
        connection.executescript(COMMERCE_SCHEMA)
        columns = {r[1] for r in connection.execute("PRAGMA table_info(kdp_snapshots)")}
        for name in ("source_key", "content_hash"):
            if name not in columns:
                connection.execute(f"ALTER TABLE kdp_snapshots ADD COLUMN {name} TEXT")
        cost_columns = {r[1] for r in connection.execute("PRAGMA table_info(direct_costs)")}
        if "content_hash" not in cost_columns:
            connection.execute("ALTER TABLE direct_costs ADD COLUMN content_hash TEXT")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshot_source ON kdp_snapshots(source_key) WHERE source_key IS NOT NULL")


def record_kdp_snapshot(path: Path, snapshot: dict) -> int:
    init_ledger(path)
    payload = _canonical(snapshot)
    content_hash = _hash(snapshot)
    source_key = snapshot.get("source_key") or f"kdp-observation:{snapshot['observed_at']}"
    conflict = False
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT id, content_hash, raw_json FROM kdp_snapshots WHERE source_key = ? OR observed_at = ? ORDER BY id LIMIT 1",
            (source_key, snapshot["observed_at"]),
        ).fetchone()
        if row:
            original_hash = row[1] or hashlib.sha256(_canonical(json.loads(row[2])).encode()).hexdigest()
            if original_hash == content_hash:
                return row[0]
            connection.execute(
                "INSERT OR IGNORE INTO snapshot_conflicts(source_key, original_snapshot_id, conflicting_hash, conflicting_json) VALUES (?, ?, ?, ?)",
                (source_key, row[0], content_hash, payload),
            )
            conflict = True
        else:
            overview = snapshot["overview"]
            cursor = connection.execute(
                "INSERT INTO kdp_snapshots(observed_at, month, royalties_usd, orders_all_types, kenp, raw_json, source_key, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (snapshot["observed_at"], snapshot["month"], overview["royalties_usd"], overview["orders_all_types"], overview["kenp"], payload, source_key, content_hash),
            )
            snapshot_id = cursor.lastrowid
            connection.executemany(
                "INSERT INTO kdp_title_attribution(snapshot_id, asin, royalties_usd, orders_count, kenp) VALUES (?, ?, ?, ?, ?)",
                [(snapshot_id, t["asin"], t["royalties_usd"], t["orders"], t["kenp"]) for t in snapshot["titles"]],
            )
    if conflict:
        raise ValueError(f"conflicting snapshot replay for {source_key}")
    return snapshot_id


def record_direct_cost(path: Path, *, incurred_at: str, slug: str | None, category: str,
                       amount_usd: float, source_key: str) -> int:
    init_ledger(path)
    value = {"incurred_at": incurred_at, "slug": slug, "category": category, "amount_usd": float(amount_usd)}
    digest = _hash(value)
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT id, content_hash, amount_usd, incurred_at, slug, category FROM direct_costs WHERE source_key = ?", (source_key,)).fetchone()
        if row:
            if ((row[1] and row[1] != digest) or float(row[2]) != float(amount_usd)
                    or row[3] != incurred_at or row[4] != slug or row[5] != category):
                raise ValueError(f"conflicting direct cost replay for {source_key}")
            return row[0]
        cursor = connection.execute(
            "INSERT INTO direct_costs(incurred_at, slug, category, amount_usd, source_key, content_hash) VALUES (?, ?, ?, ?, ?, ?)",
            (incurred_at, slug, category, amount_usd, source_key, digest),
        )
        return cursor.lastrowid


def record_growth_evidence(path: Path, evidence: dict) -> int:
    init_ledger(path)
    confidence = float(evidence["confidence"])
    if not (0.0 <= confidence <= 1.0):
        raise ValueError(f"confidence must be between 0.0 and 1.0, got {confidence}")
    source_key = evidence["source_key"]
    value = {
        "kind": evidence["kind"], "slug": evidence.get("slug"),
        "observed_at": evidence["observed_at"], "fresh_until": evidence["fresh_until"],
        "confidence": confidence, "payload": evidence.get("payload", {}),
    }
    content_hash = _hash(value)
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT id, content_hash FROM growth_evidence WHERE source_key = ?", (source_key,)
        ).fetchone()
        if row:
            if row[1] != content_hash:
                raise ValueError(f"conflicting growth evidence for {source_key}")
            return row[0]
        cursor = connection.execute(
            "INSERT INTO growth_evidence(source_key, kind, slug, observed_at, fresh_until, confidence, payload_json, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (source_key, evidence["kind"], evidence.get("slug"), evidence["observed_at"], evidence["fresh_until"],
             confidence, _canonical(evidence.get("payload", {})), content_hash),
        )
        return cursor.lastrowid


def record_growth_plan(path: Path, plan: dict) -> int:
    init_ledger(path)
    action_key = plan["action_key"]
    value = {
        "planned_at": plan["planned_at"], "phase": plan["phase"], "status": plan["status"],
        "plan_json": _canonical(plan),
    }
    content_hash = _hash(value)
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT id, content_hash FROM growth_plans WHERE action_key = ?", (action_key,)
        ).fetchone()
        if row:
            if row[1] != content_hash:
                raise ValueError(f"conflicting growth plan for {action_key}")
            return row[0]
        cursor = connection.execute(
            "INSERT INTO growth_plans(action_key, planned_at, phase, status, plan_json, content_hash) VALUES (?, ?, ?, ?, ?, ?)",
            (action_key, plan["planned_at"], plan["phase"], plan["status"], _canonical(plan), content_hash),
        )
        return cursor.lastrowid


def record_hub_event(path: Path, event: dict) -> int:
    init_ledger(path)
    event_key = event["event_key"]
    value = {
        "occurred_at": event["occurred_at"], "slug": event["slug"], "campaign": event["campaign"],
        "event_kind": event["event_kind"], "payload": event.get("payload", {}),
    }
    content_hash = _hash(value)
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT id, occurred_at, slug, campaign, event_kind, payload_json FROM hub_events WHERE event_key = ?",
            (event_key,),
        ).fetchone()
        if row:
            stored_value = {
                "occurred_at": row[1], "slug": row[2], "campaign": row[3],
                "event_kind": row[4], "payload": json.loads(row[5]),
            }
            if _hash(stored_value) != content_hash:
                raise ValueError(f"conflicting hub event for {event_key}")
            return row[0]
        cursor = connection.execute(
            "INSERT INTO hub_events(event_key, occurred_at, slug, campaign, event_kind, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
            (event_key, event["occurred_at"], event["slug"], event["campaign"], event["event_kind"],
             _canonical(event.get("payload", {}))),
        )
        return cursor.lastrowid


def growth_evidence(path: Path, *, slug: str | None = None, kind: str | None = None) -> list[dict]:
    init_ledger(path)
    query = "SELECT source_key, kind, slug, observed_at, fresh_until, confidence, payload_json FROM growth_evidence WHERE 1=1"
    params: list[str] = []
    if slug is not None:
        query += " AND slug = ?"
        params.append(slug)
    if kind is not None:
        query += " AND kind = ?"
        params.append(kind)
    query += " ORDER BY id"
    with sqlite3.connect(path) as connection:
        rows = connection.execute(query, params).fetchall()
    return [
        {
            "source_key": r[0], "kind": r[1], "slug": r[2], "observed_at": r[3],
            "fresh_until": r[4], "confidence": r[5], "payload": json.loads(r[6]),
        }
        for r in rows
    ]


def ingest_uploaded_title_costs(path: Path, books_dir: Path, *, checked_at: str = "1970-01-01T00:00:00+00:00") -> dict:
    init_ledger(path)
    uploaded = []
    missing = []
    estimated = []
    for listing_path in sorted(books_dir.glob("*/listing.json")):
        try:
            listing = json.loads(listing_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if listing.get("status") != "uploaded":
            continue
        slug = listing_path.parent.name
        uploaded.append(slug)
        report = None
        source_kind = None
        # cost-report.json = actual generation-time cost (verified);
        # cost-estimate.json = explicit estimate for legacy titles with no
        # generation log — counted as a cost but never treated as verified.
        for filename, kind in (("cost-report.json", "cost-report"), ("cost-estimate.json", "cost-estimate")):
            try:
                candidate = json.loads((listing_path.parent / filename).read_text(encoding="utf-8"))
                amount = float(candidate["total_usd"])
                if amount < 0:
                    raise ValueError
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            report, source_kind = candidate, kind
            break
        if report is None:
            missing.append(slug)
            with sqlite3.connect(path) as connection:
                connection.execute("INSERT INTO cost_inventory VALUES (?, 'missing', NULL, ?) ON CONFLICT(slug) DO UPDATE SET status='missing', source_key=NULL, checked_at=excluded.checked_at", (slug, checked_at))
            continue
        if source_kind == "cost-estimate":
            estimated.append(slug)
        status = "verified" if source_kind == "cost-report" else "estimated"
        normalized = {"total_usd": amount}
        digest = _hash(normalized)
        source_key = f"{source_kind}:{slug}"
        with sqlite3.connect(path) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO cost_report_versions(logical_key, slug, observed_at, semantic_hash, cumulative_amount_usd, raw_json) VALUES (?, ?, ?, ?, ?, ?)",
                (source_key, slug, checked_at, digest, amount, _canonical(report)),
            )
            connection.execute("INSERT INTO cost_inventory VALUES (?, ?, ?, ?) ON CONFLICT(slug) DO UPDATE SET status=excluded.status, source_key=excluded.source_key, checked_at=excluded.checked_at", (slug, status, source_key, checked_at))
    return {"uploaded_titles": len(uploaded), "verified_titles": len(uploaded) - len(missing) - len(estimated), "missing_slugs": missing, "estimated_slugs": estimated, "complete": bool(uploaded) and not missing and not estimated}


def direct_costs_for_slug(path: Path, slug: str) -> float:
    init_ledger(path)
    with sqlite3.connect(path) as connection:
        manual = connection.execute("SELECT COALESCE(SUM(amount_usd), 0) FROM direct_costs WHERE slug = ? AND source_key NOT LIKE 'cost-report:%'", (slug,)).fetchone()[0]
        report = connection.execute("SELECT cumulative_amount_usd FROM cost_report_versions WHERE slug=? ORDER BY observed_at DESC, id DESC LIMIT 1", (slug,)).fetchone()
        legacy = connection.execute("SELECT amount_usd FROM direct_costs WHERE slug=? AND source_key LIKE 'cost-report:%' ORDER BY id DESC LIMIT 1", (slug,)).fetchone()
        total = float(manual) + (float(report[0]) if report else float(legacy[0]) if legacy else 0)
    return round(float(total), 2)


def title_contribution(path: Path, slug: str, royalty_usd: float) -> dict:
    init_ledger(path)
    cost = direct_costs_for_slug(path, slug)
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT status FROM cost_inventory WHERE slug = ?", (slug,)).fetchone()
    status = row[0] if row else None
    complete = status == "verified" or (status != "estimated" and cost > 0)
    return {"period": "lifetime", "royalties_usd": round(float(royalty_usd), 2), "direct_costs_usd": cost,
            "contribution_profit_usd": round(float(royalty_usd) - cost, 2), "cost_complete": complete,
            "positive_contribution_proven": complete and float(royalty_usd) - cost > 0}


def portfolio_financials(path: Path, month: str, overhead: dict | None = None) -> dict:
    init_ledger(path)
    with sqlite3.connect(path) as connection:
        snapshot_count = connection.execute("SELECT COUNT(*) FROM kdp_snapshots WHERE month = ?", (month,)).fetchone()[0]
        snapshot = connection.execute("SELECT id, royalties_usd FROM kdp_snapshots WHERE month = ? ORDER BY observed_at DESC, id DESC LIMIT 1", (month,)).fetchone()
        verified = float(snapshot[1]) if snapshot else 0.0
        lifetime_royalties = float(connection.execute(
            "SELECT COALESCE(SUM(royalties_usd), 0) FROM kdp_snapshots WHERE id IN (SELECT id FROM kdp_snapshots s WHERE id = (SELECT id FROM kdp_snapshots WHERE month=s.month ORDER BY observed_at DESC, id DESC LIMIT 1))"
        ).fetchone()[0])
        attributed = float(connection.execute("SELECT COALESCE(SUM(royalties_usd), 0) FROM kdp_title_attribution WHERE snapshot_id = ?", (snapshot[0],)).fetchone()[0]) if snapshot else 0.0
        manual_costs = float(connection.execute("SELECT COALESCE(SUM(amount_usd), 0) FROM direct_costs WHERE source_key NOT LIKE 'cost-report:%'").fetchone()[0])
        # An estimate is superseded once a verified cost-report exists for the
        # same slug — never count both.
        report_costs = float(connection.execute("SELECT COALESCE(SUM(cumulative_amount_usd), 0) FROM cost_report_versions v WHERE id=(SELECT id FROM cost_report_versions WHERE logical_key=v.logical_key ORDER BY observed_at DESC, id DESC LIMIT 1) AND NOT (v.logical_key LIKE 'cost-estimate:%' AND EXISTS (SELECT 1 FROM cost_report_versions r WHERE r.slug=v.slug AND r.logical_key LIKE 'cost-report:%'))").fetchone()[0])
        legacy_costs = float(connection.execute("SELECT COALESCE(SUM(amount_usd), 0) FROM direct_costs d WHERE source_key LIKE 'cost-report:%' AND NOT EXISTS (SELECT 1 FROM cost_report_versions v WHERE v.slug=d.slug) AND id=(SELECT MAX(id) FROM direct_costs WHERE slug=d.slug AND source_key LIKE 'cost-report:%')").fetchone()[0])
        costs = manual_costs + report_costs + legacy_costs
        inventory = connection.execute("SELECT COUNT(*), SUM(status = 'verified') FROM cost_inventory").fetchone()
    cost_complete = bool(inventory[0] and inventory[0] == (inventory[1] or 0))
    contribution = round(lifetime_royalties - costs, 2)
    keys = {"newton_server_usd", "ai_subscription_usd", "other_usd"}
    overhead_complete = overhead is not None and keys.issubset(overhead)
    net = round(contribution - sum(float(overhead[k]) for k in keys), 2) if overhead_complete else None
    return {"verified_royalties_usd": verified, "attributed_royalties_usd": attributed,
            "unattributed_royalties_usd": round(verified - attributed, 2), "snapshot_count": snapshot_count,
            "overview_ingestion_complete": snapshot is not None, "title_attribution_complete": snapshot is not None and abs(verified-attributed) <= .01,
            "lifetime_royalties_usd": round(lifetime_royalties, 2), "direct_costs_usd": round(costs, 2), "cost_period": "lifetime", "contribution_period": "lifetime", "cost_complete": cost_complete,
            "contribution_profit_usd": contribution, "positive_contribution_proven": cost_complete and contribution > 0,
            "fully_loaded_net_profit_usd": net, "overhead_complete": overhead_complete}
