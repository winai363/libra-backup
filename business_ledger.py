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
"""


def _canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _hash(value: dict) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def init_ledger(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
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


def ingest_uploaded_title_costs(path: Path, books_dir: Path, *, checked_at: str = "1970-01-01T00:00:00+00:00") -> dict:
    init_ledger(path)
    uploaded = []
    missing = []
    for listing_path in sorted(books_dir.glob("*/listing.json")):
        try:
            listing = json.loads(listing_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if listing.get("status") != "uploaded":
            continue
        slug = listing_path.parent.name
        uploaded.append(slug)
        report_path = listing_path.parent / "cost-report.json"
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            amount = float(report["total_usd"])
            if amount < 0:
                raise ValueError
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            missing.append(slug)
            with sqlite3.connect(path) as connection:
                connection.execute("INSERT INTO cost_inventory VALUES (?, 'missing', NULL, ?) ON CONFLICT(slug) DO UPDATE SET status='missing', source_key=NULL, checked_at=excluded.checked_at", (slug, checked_at))
            continue
        digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
        source_key = f"cost-report:{slug}:{digest}"
        record_direct_cost(path, incurred_at=listing.get("uploaded_at") or listing.get("created_at") or checked_at,
                           slug=slug, category="production", amount_usd=amount, source_key=source_key)
        with sqlite3.connect(path) as connection:
            connection.execute("INSERT INTO cost_inventory VALUES (?, 'verified', ?, ?) ON CONFLICT(slug) DO UPDATE SET status='verified', source_key=excluded.source_key, checked_at=excluded.checked_at", (slug, source_key, checked_at))
    return {"uploaded_titles": len(uploaded), "verified_titles": len(uploaded) - len(missing), "missing_slugs": missing, "complete": bool(uploaded) and not missing}


def direct_costs_for_slug(path: Path, slug: str) -> float:
    init_ledger(path)
    with sqlite3.connect(path) as connection:
        total = connection.execute("SELECT COALESCE(SUM(amount_usd), 0) FROM direct_costs WHERE slug = ?", (slug,)).fetchone()[0]
    return round(float(total), 2)


def title_contribution(path: Path, slug: str, royalty_usd: float) -> dict:
    init_ledger(path)
    cost = direct_costs_for_slug(path, slug)
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT status FROM cost_inventory WHERE slug = ?", (slug,)).fetchone()
    complete = bool((row and row[0] == "verified") or cost > 0)
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
        costs = float(connection.execute("SELECT COALESCE(SUM(amount_usd), 0) FROM direct_costs").fetchone()[0])
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
