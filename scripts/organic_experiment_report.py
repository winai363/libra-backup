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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

LIBRA_DIR = Path(__file__).resolve().parent.parent
KDP_DIR = LIBRA_DIR.parent / "kdp"
LEDGER_FILE = LIBRA_DIR / "data" / "libra-business.db"
EXPERIMENT_FILE = LIBRA_DIR / "data" / "organic_experiment.json"
ROSTER_FILE = KDP_DIR / "bookshelf-roster.json"
LOOM_ENV = Path("/root/loom/.env")

# Our own verification clicks are registered in the shared synthetic-event
# registry and subtracted here. Raw rows stay as written; the correction lives in
# the reader, the same way the storefronts do it.
sys.path.insert(0, "/root/shared")
try:
    import synthetic_events
except ImportError:  # a missing registry must degrade the report, not break it
    synthetic_events = None

# A book whose shelf row is one of these is out of the campaign until it is Live
# again. "Live - Updates in review" is still selling, so it is watched, not paused.
PAUSE_STATUSES = ("IN_REVIEW", "BLOCKED", "UNPUBLISHED", "DRAFT")
WATCH_STATUSES = ("LIVE_UPDATES_IN_REVIEW",)


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


def synthetic_exclusion(ledger: Path) -> tuple:
    """(sql, params) excluding hub_events rows this system wrote while verifying
    itself. Empty when the registry is missing: a reader that cannot load the
    registry reports more clicks, never fewer, and says so."""
    if synthetic_events is None:
        return "", ()
    try:
        return synthetic_events.sql_exclusion(str(ledger), table="hub_events", column="event_key")
    except Exception:
        return "", ()


def clicks_by_campaign(connection: sqlite3.Connection, slug: str, since: str,
                       exclusion: tuple = ("", ())) -> dict:
    clause, params = exclusion
    rows = connection.execute(
        "SELECT campaign, event_kind, COUNT(*) AS n FROM hub_events "
        "WHERE slug = ? AND occurred_at >= ?" + clause + " GROUP BY campaign, event_kind",
        (slug, since, *params),
    ).fetchall()
    by_campaign: dict = {}
    for row in rows:
        by_campaign.setdefault(row["campaign"], {})[row["event_kind"]] = row["n"]
    return by_campaign


def shelf_status(slug: str, roster_file: Path = ROSTER_FILE) -> dict:
    """What the last bookshelf scrape saw for this book, per format. Unknown when
    the roster is missing or the book is absent from it — never assumed Live."""
    roster = _load_json(roster_file, {})
    entries = roster.get("entries") if isinstance(roster, dict) else None
    if not isinstance(entries, list):
        return {"fetched_at": None, "formats": {}, "known": False}
    formats = {e.get("format", "ebook"): e.get("status")
               for e in entries if isinstance(e, dict) and e.get("slug") == slug}
    return {"fetched_at": roster.get("fetched_at"), "formats": formats,
            "known": bool(formats)}


def campaign_disposition(statuses: dict) -> dict:
    """Proportionate response to what the shelf shows: pause the book only when a
    format is out of sale or back in review on its own; a Live book with an edit
    in review is watched, because it is still selling."""
    values = [v for v in statuses.get("formats", {}).values() if v]
    if not values:
        return {"action": "hold", "reason": "shelf status unknown — not verified Live"}
    if any(v in PAUSE_STATUSES for v in values):
        return {"action": "pause", "reason": f"shelf shows {sorted(set(values))}"}
    if any(v in WATCH_STATUSES for v in values):
        return {"action": "watch", "reason": "still selling with an edit in review"}
    return {"action": "run", "reason": "all shelf rows Live"}


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


def verified_publications(experiment: dict) -> list:
    """Publication records that name what was published, where, and the evidence
    for it. A scheduled task, a prepared post or a written RSS file is not a
    publication: only a record carrying a live url and an observation time counts."""
    out = []
    for record in experiment.get("publications", []) or []:
        if not isinstance(record, dict):
            continue
        url = str(record.get("url") or "").strip()
        observed_at = str(record.get("observed_at") or "").strip()
        evidence = str(record.get("evidence") or "").strip()
        if url.startswith("https://") and observed_at and evidence:
            out.append({"channel": record.get("channel"), "slug": record.get("slug"),
                        "url": url, "observed_at": observed_at, "evidence": evidence})
    out.sort(key=lambda r: r["observed_at"])
    return out


def build_report(*, experiment_file: Path = EXPERIMENT_FILE, ledger: Path = LEDGER_FILE,
                 kdp_dir: Path = KDP_DIR, roster_file: Path = ROSTER_FILE,
                 today: date | None = None) -> dict:
    experiment = _load_json(experiment_file, None)
    if not isinstance(experiment, dict) or not experiment.get("active"):
        return {"state": "inactive", "reason": "no active experiment declared"}
    if not Path(ledger).exists():
        return {"state": "blocked", "reason": f"ledger_not_found: {ledger}"}

    today = today or datetime.now(timezone.utc).date()
    publications = verified_publications(experiment)
    # The window starts at the first verified publication, never at a date someone
    # typed and never at the first click.
    started_on = publications[0]["observed_at"][:10] if publications else None
    window_days = int(experiment.get("planned_window_days") or 30)
    ends_on = None
    days_elapsed = None
    if started_on:
        try:
            first_day = date.fromisoformat(started_on)
            ends_on = str(first_day + timedelta(days=window_days))
            days_elapsed = (today - first_day).days
        except ValueError:
            started_on = None

    asins = slug_to_asin(kdp_dir)
    exclusion = synthetic_exclusion(Path(ledger))
    books = []
    with _connect_read_only(Path(ledger)) as connection:
        account = account_royalties_by_month(connection)
        for slug in experiment.get("books", []):
            asin = asins.get(slug)
            statuses = shelf_status(slug, roster_file)
            months = royalties_by_month(connection, asin) if asin else None
            books.append({
                "slug": slug,
                # Exact identity of what a link points at: ASIN per format, as the
                # last shelf scrape saw it.
                "asin": asin,
                "shelf": statuses,
                "campaign": campaign_disposition(statuses),
                # No ASIN means no way to read this book's royalties: unknown, not zero.
                "royalties_by_month": months,
                "clicks_by_campaign": clicks_by_campaign(connection, slug, started_on, exclusion)
                if started_on else {},
                "publications": [r for r in publications if r.get("slug") == slug],
                "baseline": (experiment.get("baseline", {}).get("books", {}) or {}).get(slug),
                "purchase_attribution": "not_attributable",
            })

    qualified_clicks = sum(
        count
        for book in books
        for kinds in book["clicks_by_campaign"].values()
        for count in kinds.values()
    )
    window_months = sorted({m for book in books for m in (book["royalties_by_month"] or {})})
    tracks = {
        # Each track is counted on its own. None means not measurable here, which
        # is not the same as zero.
        "content_published": len(publications),
        "exposure": experiment.get("exposure_source") or None,
        "qualified_outbound_clicks": qualified_clicks,
        "paid_orders": {
            m: sum((book["royalties_by_month"] or {}).get(m, {}).get("orders") or 0
                   for book in books)
            for m in window_months
        },
        "kenp": {
            m: sum((book["royalties_by_month"] or {}).get(m, {}).get("kenp") or 0
                   for book in books)
            for m in window_months
        },
        "royalties_usd": {
            m: round(sum((book["royalties_by_month"] or {}).get(m, {}).get("royalties_usd") or 0
                         for book in books), 2)
            for m in window_months
        },
        "costs_usd": {"paid_spend": 0, "note": "no paid channel is authorized for this experiment"},
        "human_interventions": len(experiment.get("interventions", []) or []),
        "synthetic_clicks_excluded": bool(exclusion[0]),
    }

    threshold = int(experiment.get("acquisition_threshold_clicks") or 25)
    if not publications:
        state, verdict, action = "not_started", "INCONCLUSIVE", \
            "no verified publication yet — nothing has been published, so nothing is being measured"
    elif qualified_clicks == 0 and (days_elapsed or 0) >= 14:
        state, verdict, action = "active", "INCONCLUSIVE", \
            "zero qualified clicks by day 14 — diagnose distribution and tracking " \
            "(feed/pin live? link reachable? events recorded?); do not retire a book on this"
    elif qualified_clicks >= threshold:
        state, verdict, action = "active", "ACQUISITION_THRESHOLD_MET", \
            f"{qualified_clicks} qualified clicks reached the provisional acquisition " \
            "threshold; this is reach, not sales validation"
    else:
        state, verdict, action = "active", "INCONCLUSIVE", \
            f"{qualified_clicks}/{threshold} qualified clicks so far"

    paused = [b["slug"] for b in books if b["campaign"]["action"] == "pause"]
    return {
        "state": state,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment": experiment.get("name"),
        "started_on": started_on,
        "started_from": "first verified publication" if started_on else None,
        "ends_on": ends_on,
        "days_elapsed": days_elapsed,
        "window_closed": bool(ends_on and str(today) >= str(ends_on)),
        "primary_metric": experiment.get("primary_metric"),
        "acquisition_threshold_clicks": threshold,
        "threshold_meaning": "provisional acquisition signal (reach). Not sales validation.",
        "verdict": verdict,
        "action": action,
        "tracks": tracks,
        "paused_books": paused,
        "account_royalties_by_month": account,
        "books": books,
        "publications": publications,
        "attribution_note": (
            "Amazon reports no referrer. Clicks and royalties are separate "
            "observations; no purchase may be attributed to a click, and no "
            "read-through rate may be derived from KENP."
        ),
    }


def format_lines(report: dict) -> str:
    state = report.get("state")
    if state in ("inactive", "blocked"):
        return f"organic experiment: {state} — {report.get('reason', '')}".strip()
    tracks = report["tracks"]
    lines = [
        f"📊 Libra organic experiment: {report['experiment']} [{state}]",
        f"published (verified): {tracks['content_published']} | "
        f"qualified clicks: {tracks['qualified_outbound_clicks']} | "
        f"verdict {report['verdict']}",
        f"→ {report['action']}",
    ]
    if report["started_on"]:
        lines.append(f"window {report['started_on']} → {report['ends_on']} "
                     f"(day {report['days_elapsed']}"
                     f"{', closed' if report['window_closed'] else ''}, "
                     f"start = {report['started_from']})")
    lines.append(f"tracks: orders {tracks['paid_orders']} | kenp {tracks['kenp']} | "
                 f"royalties {tracks['royalties_usd']} | paid spend "
                 f"${tracks['costs_usd']['paid_spend']} | exposure "
                 f"{tracks['exposure'] or 'unavailable'} | human steps "
                 f"{tracks['human_interventions']}")
    for month, figures in report["account_royalties_by_month"].items():
        lines.append(f"  account {month}: ${figures['royalties_usd']} "
                     f"(kenp {figures['kenp']}, observed {figures['observed_at'][:10]})")
    for book in report["books"]:
        clicks = sum(n for kinds in book["clicks_by_campaign"].values() for n in kinds.values())
        shelf = book["shelf"]["formats"] or "unknown"
        lines.append(f"  {book['slug']} [{book['campaign']['action']}]: clicks {clicks} | "
                     f"shelf {shelf} | published {len(book['publications'])} | "
                     f"attribution {book['purchase_attribution']}")
        for campaign, kinds in sorted(book["clicks_by_campaign"].items()):
            lines.append(f"      {campaign}: {kinds}")
    if report["paused_books"]:
        lines.append(f"  paused: {', '.join(report['paused_books'])}")
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
