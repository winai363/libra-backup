import json
import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS kdp_snapshots (
  id INTEGER PRIMARY KEY,
  observed_at TEXT NOT NULL UNIQUE,
  month TEXT NOT NULL,
  royalties_usd REAL NOT NULL,
  orders_all_types INTEGER NOT NULL,
  kenp INTEGER NOT NULL,
  raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kdp_title_attribution (
  snapshot_id INTEGER NOT NULL,
  asin TEXT NOT NULL,
  royalties_usd REAL NOT NULL,
  orders_count INTEGER NOT NULL,
  kenp INTEGER NOT NULL,
  PRIMARY KEY (snapshot_id, asin),
  FOREIGN KEY(snapshot_id) REFERENCES kdp_snapshots(id)
);
CREATE TABLE IF NOT EXISTS direct_costs (
  id INTEGER PRIMARY KEY,
  incurred_at TEXT NOT NULL,
  slug TEXT,
  category TEXT NOT NULL,
  amount_usd REAL NOT NULL,
  source_key TEXT NOT NULL UNIQUE
);
"""


def init_ledger(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)


def record_kdp_snapshot(path: Path, snapshot: dict) -> int:
    init_ledger(path)
    overview = snapshot["overview"]
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO kdp_snapshots (
                observed_at, month, royalties_usd, orders_all_types, kenp, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(observed_at) DO UPDATE SET
                month = excluded.month,
                royalties_usd = excluded.royalties_usd,
                orders_all_types = excluded.orders_all_types,
                kenp = excluded.kenp,
                raw_json = excluded.raw_json
            """,
            (
                snapshot["observed_at"],
                snapshot["month"],
                overview["royalties_usd"],
                overview["orders_all_types"],
                overview["kenp"],
                json.dumps(snapshot, sort_keys=True),
            ),
        )
        row = connection.execute(
            "SELECT id FROM kdp_snapshots WHERE observed_at = ?",
            (snapshot["observed_at"],),
        ).fetchone()
        snapshot_id = row[0]
        connection.execute(
            "DELETE FROM kdp_title_attribution WHERE snapshot_id = ?",
            (snapshot_id,),
        )
        connection.executemany(
            """
            INSERT INTO kdp_title_attribution (
                snapshot_id, asin, royalties_usd, orders_count, kenp
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    snapshot_id,
                    title["asin"],
                    title["royalties_usd"],
                    title["orders"],
                    title["kenp"],
                )
                for title in snapshot["titles"]
            ],
        )
    return snapshot_id


def record_direct_cost(
    path: Path,
    *,
    incurred_at: str,
    slug: str | None,
    category: str,
    amount_usd: float,
    source_key: str,
) -> int:
    init_ledger(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO direct_costs (
                incurred_at, slug, category, amount_usd, source_key
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET
                incurred_at = excluded.incurred_at,
                slug = excluded.slug,
                category = excluded.category,
                amount_usd = excluded.amount_usd
            """,
            (incurred_at, slug, category, amount_usd, source_key),
        )
        return connection.execute(
            "SELECT id FROM direct_costs WHERE source_key = ?", (source_key,)
        ).fetchone()[0]


def portfolio_financials(path: Path, month: str, overhead: dict | None = None) -> dict:
    init_ledger(path)
    with sqlite3.connect(path) as connection:
        snapshot_count = connection.execute(
            "SELECT COUNT(*) FROM kdp_snapshots WHERE month = ?", (month,)
        ).fetchone()[0]
        snapshot = connection.execute(
            """
            SELECT id, royalties_usd
            FROM kdp_snapshots
            WHERE month = ?
            ORDER BY observed_at DESC
            LIMIT 1
            """,
            (month,),
        ).fetchone()

        if snapshot is None:
            verified_royalties = 0.0
            attributed_royalties = 0.0
        else:
            snapshot_id, verified_royalties = snapshot
            attributed_royalties = connection.execute(
                """
                SELECT COALESCE(SUM(royalties_usd), 0)
                FROM kdp_title_attribution
                WHERE snapshot_id = ?
                """,
                (snapshot_id,),
            ).fetchone()[0]

        direct_costs = connection.execute(
            """
            SELECT COALESCE(SUM(amount_usd), 0)
            FROM direct_costs
            WHERE substr(incurred_at, 1, 7) = ?
            """,
            (month,),
        ).fetchone()[0]

    contribution_profit = round(verified_royalties - direct_costs, 2)
    overhead_keys = {"newton_server_usd", "ai_subscription_usd", "other_usd"}
    overhead_complete = overhead is not None and overhead_keys.issubset(overhead)
    fully_loaded_net_profit = None
    if overhead_complete:
        overhead_total = sum(overhead[key] for key in overhead_keys)
        fully_loaded_net_profit = round(contribution_profit - overhead_total, 2)

    return {
        "verified_royalties_usd": verified_royalties,
        "attributed_royalties_usd": attributed_royalties,
        "unattributed_royalties_usd": round(
            verified_royalties - attributed_royalties, 2
        ),
        "snapshot_count": snapshot_count,
        "direct_costs_usd": direct_costs,
        "contribution_profit_usd": contribution_profit,
        "fully_loaded_net_profit_usd": fully_loaded_net_profit,
        "overhead_complete": overhead_complete,
    }
