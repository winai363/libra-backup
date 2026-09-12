#!/usr/bin/env python3
"""organic_experiment_report.py — read-only status of the organic-traffic
experiment: tracked clicks out of our own hub pages next to the royalties KDP
reports for the same books.

What it is NOT: it decides nothing, proposes nothing, and touches neither KDP
nor any provider. Libra is in PASSIVE MODE and its decision agents were
cancelled on 2026-08-30; this is a report, so it must stay one.

Attribution honesty: Amazon tells us nothing about where a buyer came from. A
click on our hub link and a royalty on the same book are two separate
observations, reported side by side and never multiplied into a conversion
rate. `purchase_attribution` says so in every payload.

Monthly KDP figures are cumulative, so each month uses its latest observation
rather than a sum of daily rows (a sum would double-count, and a later downward
correction would never show).

Usage:
    python3 scripts/organic_experiment_report.py             # human summary
    python3 scripts/organic_experiment_report.py --json      # machine payload
    python3 scripts/organic_experiment_report.py --send      # also Telegram

Kill switch: set "active": false in data/organic_experiment.json (or delete the
file) and this reports `inactive` without reading anything else.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

LIBRA_DIR = Path(__file__).resolve().parent.parent
KDP_DIR = LIBRA_DIR.parent / "kdp"
LEDGER_FILE = LIBRA_DIR / "data" / "libra-business.db"
EXPERIMENT_FILE = LIBRA_DIR / "data" / "organic_experiment.json"
LOOM_ENV = Path("/root/loom/.env")


def _load_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _connect_read_only(ledger: Path) -> sqlite3.Connection:
    """Open the ledger read-only so a report can never create or alter schema."""
    connection = sqlite3.connect(f"file:{Path(ledger).resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def slug_to_asin(kdp_dir: Path) -> dict:
    mapping = {}
    for listing_file in sorted(Path(kdp_dir).glob("*/listing.json")):
        listing = _load_json(listing_file, {})
        asin = listing.get("asin")
        if asin:
            mapping[listing_file.parent.name] = asin
    return mapping


def clicks_by_campaign(connection: sqlite3.Connection, slug: str, since: str) -> dict:
    rows = connection.execute(
        "SELECT campaign, event_kind, COUNT(*) AS n FROM hub_events "
        "WHERE slug = ? AND occurred_at >= ? GROUP BY campaign, event_kind",
        (slug, since),
    ).fetchall()
    by_campaign: dict = {}
    for row in rows:
        by_campaign.setdefault(row["campaign"], {})[row["event_kind"]] = row["n"]
    return by_campaign


def royalties_by_month(connection: sqlite3.Connection, asin: str) -> dict:
    """Latest observation per month for one ASIN — never a sum of rows."""
    rows = connection.execute(
        "SELECT s.month AS month, t.royalties_usd AS royalties_usd, "
        "       t.orders_count AS orders_count, t.kenp AS kenp "
        "FROM kdp_title_attribution t JOIN kdp_snapshots s ON s.id = t.snapshot_id "
        "WHERE t.asin = ? AND s.observed_at = ("
        "   SELECT MAX(k.observed_at) FROM kdp_snapshots k WHERE k.month = s.month) "
        "ORDER BY s.month",
        (asin,),
    ).fetchall()
    return {
        row["month"]: {
            "royalties_usd": row["royalties_usd"],
            "orders": row["orders_count"],
            "kenp": row["kenp"],
        }
        for row in rows
    }


def account_royalties_by_month(connection: sqlite3.Connection) -> dict:
    rows = connection.execute(
        "SELECT month, royalties_usd, orders_all_types, kenp, observed_at FROM kdp_snapshots s "
        "WHERE s.observed_at = (SELECT MAX(k.observed_at) FROM kdp_snapshots k WHERE k.month = s.month) "
        "ORDER BY month"
    ).fetchall()
    return {
        row["month"]: {
            "royalties_usd": row["royalties_usd"],
            "orders_all_types": row["orders_all_types"],
            "kenp": row["kenp"],
            "observed_at": row["observed_at"],
        }
        for row in rows
    }


def build_report(*, experiment_file: Path = EXPERIMENT_FILE, ledger: Path = LEDGER_FILE,
                 kdp_dir: Path = KDP_DIR, today: date | None = None) -> dict:
    experiment = _load_json(experiment_file, None)
    if not isinstance(experiment, dict) or not experiment.get("active"):
        return {"state": "inactive", "reason": "no active experiment declared"}
    if not Path(ledger).exists():
        return {"state": "blocked", "reason": f"ledger_not_found: {ledger}"}

    today = today or datetime.now(timezone.utc).date()
    started_on = experiment.get("started_on")
    ends_on = experiment.get("ends_on")
    asins = slug_to_asin(kdp_dir)

    books = []
    with _connect_read_only(Path(ledger)) as connection:
        account = account_royalties_by_month(connection)
        for slug in experiment.get("books", []):
            asin = asins.get(slug)
            books.append({
                "slug": slug,
                "asin": asin,
                # No ASIN means no way to read this book's royalties: unknown, not zero.
                "royalties_by_month": royalties_by_month(connection, asin) if asin else None,
                "clicks_by_campaign": clicks_by_campaign(connection, slug, started_on or "")
                if started_on else {},
                "baseline": (experiment.get("baseline", {}).get("books", {}) or {}).get(slug),
                "purchase_attribution": "not_attributable",
            })

    days_elapsed = None
    if started_on:
        try:
            days_elapsed = (today - date.fromisoformat(started_on)).days
        except ValueError:
            days_elapsed = None

    total_clicks = sum(
        count
        for book in books
        for kinds in book["clicks_by_campaign"].values()
        for count in kinds.values()
    )
    return {
        "state": "active",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment": experiment.get("name"),
        "started_on": started_on,
        "ends_on": ends_on,
        "days_elapsed": days_elapsed,
        "window_closed": bool(ends_on and str(today) >= str(ends_on)),
        "primary_metric": experiment.get("primary_metric"),
        "continue_if": experiment.get("continue_if"),
        "stop_if": experiment.get("stop_if"),
        "tracked_clicks_total": total_clicks,
        # A click count of zero while no link has been posted is "not started",
        # not "the channel failed" — the reader of this report must see which.
        "verdict": "INCONCLUSIVE" if total_clicks == 0 else "HAS_CLICK_DATA",
        "account_royalties_by_month": account,
        "books": books,
        "attribution_note": (
            "Amazon reports no referrer. Clicks and royalties are separate "
            "observations; no purchase may be attributed to a click."
        ),
    }


def format_lines(report: dict) -> str:
    if report.get("state") != "active":
        return f"organic experiment: {report.get('state')} — {report.get('reason', '')}".strip()
    lines = [
        f"📊 Libra organic experiment: {report['experiment']}",
        f"window {report['started_on']} → {report['ends_on']} "
        f"(day {report['days_elapsed']}{', closed' if report['window_closed'] else ''})",
        f"tracked clicks: {report['tracked_clicks_total']} → {report['verdict']}",
    ]
    for month, figures in report["account_royalties_by_month"].items():
        lines.append(f"  account {month}: ${figures['royalties_usd']} "
                     f"(kenp {figures['kenp']}, observed {figures['observed_at'][:10]})")
    for book in report["books"]:
        clicks = sum(n for kinds in book["clicks_by_campaign"].values() for n in kinds.values())
        months = book["royalties_by_month"]
        latest = f"${list(months.values())[-1]['royalties_usd']}" if months else "unknown"
        lines.append(f"  {book['slug']}: clicks {clicks} | latest month royalties {latest} "
                     f"| attribution {book['purchase_attribution']}")
        for campaign, kinds in sorted(book["clicks_by_campaign"].items()):
            lines.append(f"      {campaign}: {kinds}")
    lines.append("  " + report["attribution_note"])
    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    env = {}
    try:
        for line in LOOM_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return False
    # TELEGRAM_BOT_TOKEN was revoked in July 2026; HQ_BOT_TOKEN is the live one.
    token = env.get("HQ_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("HQ_CHAT_ID") or env.get("TELEGRAM_NOTIFY_CHAT_ID")
    if not token or not chat_id:
        print("no telegram credentials — not sent", file=sys.stderr)
        return False
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data), timeout=30
        ) as response:
            return json.load(response).get("ok", False)
    except urllib.error.URLError as error:
        print(f"telegram failed: {error}", file=sys.stderr)
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print the machine payload")
    parser.add_argument("--send", action="store_true", help="also push the summary to Telegram")
    args = parser.parse_args(argv)

    report = build_report()
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else format_lines(report))
    if args.send and report.get("state") == "active":
        send_telegram(format_lines(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
