#!/usr/bin/env python3
"""Local KDP category health manager.

This is the daily guard around the category resolver/uploader work:
- validates LIVE / IN_REVIEW / queued listing categories against the real KDP tree
- flags localized taxonomy before it jams the upload queue
- catches "valid but wrong" medical drift such as PTSD / Prostate Health
- tracks the open-items restore state for juvenile books
- writes machine-readable and human-readable reports
- optionally sends Telegram only when the status changes

It does not submit metadata to KDP. Production updates remain in explicit
scripts such as restore_open_items.py.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LIBRA = Path(__file__).parent
KDP = Path("/root/kdp")
TREE = LIBRA / "data" / "kdp_category_tree.json"
OUT_JSON = LIBRA / "data" / "category_health.json"
OUT_MD = LIBRA / "data" / "category_health.md"
STATE_FILE = LIBRA / "data" / "category_health_state.json"
INCIDENTS_FILE = LIBRA / "data" / "kdp_metadata_incidents.json"
QUEUE_FILE = LIBRA / "queue.txt"

sys.path.insert(0, str(LIBRA))
from category_language_scan import scan as scan_language  # noqa: E402
from category_resolver import resolve_paths  # noqa: E402

OPEN_ITEM_SLUGS = ("teen-anxiety-workbook-french", "bilingual-english-spanish-kids-vocab")
OPEN_ITEM_STAMP = "open_items_categories_restored_at"

LOCAL_STATUS = {"LIVE", "IN_REVIEW"}

BAD_SPECIFIC_RULES = (
    {
        "name": "ptsd_without_trauma_context",
        "needle": "Post-Traumatic Stress Disorder (PTSD)",
        "context": {"ptsd", "trauma", "traumatic", "posttraumatic", "post-traumatic"},
    },
    {
        "name": "prostate_without_prostate_context",
        "needle": "Prostate Health",
        "context": {"prostate", "prostatic"},
    },
)


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def queued_slugs() -> set[str]:
    if not QUEUE_FILE.exists():
        return set()
    return {line.strip() for line in QUEUE_FILE.read_text(encoding="utf-8").splitlines() if line.strip()}


def load_tree() -> set[str]:
    try:
        return set(json.loads(TREE.read_text(encoding="utf-8")).get("leaves", []))
    except Exception:
        return set()


def load_metadata_incidents() -> list[dict]:
    try:
        payload = json.loads(INCIDENTS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [item for item in payload.get("incidents", []) if isinstance(item, dict)]


def listing_files() -> list[Path]:
    queued = queued_slugs()
    files: list[Path] = []
    for path in sorted(KDP.glob("*/listing.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("live_status") in LOCAL_STATUS or path.parent.name in queued:
            files.append(path)
    return files


def text_context(listing: dict) -> set[str]:
    parts = [
        listing.get("title", ""),
        listing.get("subtitle", ""),
        listing.get("description", ""),
        " ".join(listing.get("keywords") or []),
        " ".join(listing.get("categories") or []),
    ]
    return {w for w in re.split(r"[^a-z0-9]+", " ".join(parts).lower()) if w}


def juvenile_age_issue(categories: list[str], listing: dict) -> str | None:
    joined = " | ".join(categories).lower()
    juvenile = "children's ebooks" in joined or "teen & young adult" in joined
    if not juvenile:
        return None
    age = listing.get("reading_interest_age")
    if not isinstance(age, dict) or age.get("min") is None or age.get("max") is None:
        return "juvenile category without reading_interest_age"
    return None


def category_issues(leaves: set[str]) -> list[dict]:
    findings: list[dict] = []
    for file in listing_files():
        slug = file.parent.name
        listing = json.loads(file.read_text(encoding="utf-8"))
        categories = [str(c).strip() for c in listing.get("categories") or [] if str(c).strip()]
        ctx = text_context(listing)
        issues: list[dict] = []

        resolved = resolve_paths(categories)
        for cat in categories:
            if cat not in leaves:
                issues.append({
                    "severity": "warning",
                    "kind": "not_real_kdp_leaf_resolver_will_snap_on_submit",
                    "category": cat,
                    "resolved_suggestion": resolved,
                })
            for rule in BAD_SPECIFIC_RULES:
                if rule["needle"].lower() in cat.lower() and not (ctx & rule["context"]):
                    issues.append({"severity": "blocker", "kind": rule["name"], "category": cat})

        age_issue = juvenile_age_issue(categories, listing)
        if age_issue:
            issues.append({"severity": "blocker", "kind": age_issue, "category": ""})

        if len(categories) < 2:
            issues.append({"severity": "warning", "kind": "fewer_than_2_categories", "category": ""})

        if issues:
            findings.append({
                "slug": slug,
                "title": listing.get("title", ""),
                "live_status": listing.get("live_status"),
                "asin": listing.get("asin", ""),
                "issues": issues,
            })
    return findings


def open_item_status() -> list[dict]:
    out: list[dict] = []
    for slug in OPEN_ITEM_SLUGS:
        file = KDP / slug / "listing.json"
        if not file.exists():
            out.append({"slug": slug, "state": "missing_listing"})
            continue
        listing = json.loads(file.read_text(encoding="utf-8"))
        cats = listing.get("categories") or []
        age = listing.get("reading_interest_age")
        stamped = bool(listing.get(OPEN_ITEM_STAMP))
        ready_for_restore = listing.get("live_status") == "LIVE" and not stamped
        out.append({
            "slug": slug,
            "live_status": listing.get("live_status"),
            "category_count": len(cats),
            "has_reading_age": isinstance(age, dict) and age.get("min") is not None and age.get("max") is not None,
            "restored": stamped,
            "ready_for_restore": ready_for_restore,
            "stamp": listing.get(OPEN_ITEM_STAMP),
        })
    return out


def build_report() -> dict:
    leaves = load_tree()
    language_findings = scan_language(include_review=True, include_unpublished=False)
    local_findings = category_issues(leaves)
    blockers = []
    warnings = []
    for finding in local_findings:
        for issue in finding["issues"]:
            row = {"slug": finding["slug"], **issue}
            if issue["severity"] == "blocker":
                blockers.append(row)
            else:
                warnings.append(row)
    for finding in language_findings:
        blockers.append({"slug": finding["slug"], "kind": "localized_taxonomy", "category": ""})

    open_items = open_item_status()
    ready_restore = [item for item in open_items if item.get("ready_for_restore")]
    for item in ready_restore:
        warnings.append({"slug": item["slug"], "kind": "open_item_ready_for_restore_cron", "category": ""})

    incidents = load_metadata_incidents()
    active_incidents = [item for item in incidents if not item.get("resolved")]
    blacklist: dict[str, list[str]] = {}
    for item in incidents:
        asin = str(item.get("asin") or "").strip()
        category = str(item.get("category") or "").strip()
        if asin and category:
            blacklist.setdefault(asin, []).append(category)
    for asin in blacklist:
        blacklist[asin] = sorted(set(blacklist[asin]))

    status = "blocker" if blockers else "metadata_risk" if active_incidents else "ok"
    return {
        "checked_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "status": status,
        "tree_leaf_count": len(leaves),
        "language_findings": language_findings,
        "category_findings": local_findings,
        "open_items": open_items,
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "blockers": blockers,
        "warnings": warnings,
        "metadata_risk": bool(active_incidents),
        "metadata_incidents": active_incidents,
        "removed_category_blacklist": blacklist,
    }


def write_reports(report: dict) -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        "# Libra KDP Category Health",
        "",
        f"- checked_at: {report['checked_at']}",
        f"- status: {report['status']}",
        f"- tree_leaf_count: {report['tree_leaf_count']}",
        f"- blockers: {report['blocker_count']}",
        f"- warnings: {report['warning_count']}",
        f"- metadata_risk: {report['metadata_risk']}",
        "",
        "## Blockers",
    ]
    if report["blockers"]:
        for item in report["blockers"]:
            lines.append(f"- {item['slug']}: {item['kind']} {item.get('category', '')}".rstrip())
    else:
        lines.append("- none")
    lines.extend(["", "## KDP Metadata Incidents"])
    if report["metadata_incidents"]:
        for item in report["metadata_incidents"]:
            lines.append(f"- {item.get('asin')}: removed {item.get('category')} ({item.get('noticed_at')})")
    else:
        lines.append("- none")
    lines.extend(["", "## Warnings"])
    if report["warnings"]:
        for item in report["warnings"]:
            lines.append(f"- {item['slug']}: {item['kind']} {item.get('category', '')}".rstrip())
    else:
        lines.append("- none")
    lines.extend(["", "## Open Items"])
    for item in report["open_items"]:
        lines.append(
            f"- {item['slug']}: status={item.get('live_status')} "
            f"cats={item.get('category_count')} age={item.get('has_reading_age')} "
            f"restored={item.get('restored')} ready_for_restore={item.get('ready_for_restore')}"
        )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def send_telegram(message: str) -> None:
    env = load_env(LIBRA / ".env")
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat = env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": message, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    with urllib.request.urlopen(req, timeout=10) as response:
        response.read()


def maybe_notify(report: dict, force: bool = False) -> None:
    previous = {}
    if STATE_FILE.exists():
        try:
            previous = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            previous = {}
    signature = {
        "status": report["status"],
        "blocker_count": report["blocker_count"],
        "warning_count": report["warning_count"],
        "blockers": sorted((b["slug"], b["kind"], b.get("category", "")) for b in report["blockers"]),
        "metadata_incidents": sorted(
            (item.get("asin", ""), item.get("category", ""), item.get("noticed_at", ""))
            for item in report["metadata_incidents"]
        ),
    }
    changed = previous.get("signature") != signature
    STATE_FILE.write_text(json.dumps({"signature": signature, "updated_at": report["checked_at"]}, indent=2), encoding="utf-8")
    if not (force or changed):
        return
    if report["status"] == "ok":
        message = "✅ <b>Libra KDP category health OK</b>\nNo blockers."
    elif report["status"] == "metadata_risk":
        lines = [
            "⚠️ <b>Libra KDP metadata risk</b>",
            f"KDP notices: {len(report['metadata_incidents'])} | No live metadata changes allowed.",
        ]
        for item in report["metadata_incidents"][:8]:
            lines.append(f"- {item.get('asin')}: removed {item.get('category')}")
        message = "\n".join(lines)
    else:
        lines = [
            "⚠️ <b>Libra KDP category health BLOCKED</b>",
            f"Blockers: {report['blocker_count']} | Warnings: {report['warning_count']}",
        ]
        for item in report["blockers"][:8]:
            lines.append(f"- {item['slug']}: {item['kind']}")
        lines.append(f"Report: {OUT_MD}")
        message = "\n".join(lines)
    try:
        send_telegram(message)
    except Exception as exc:
        print(f"telegram_notify_failed: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--notify", action="store_true", help="Send Telegram when status/signature changes")
    parser.add_argument("--force-notify", action="store_true", help="Send Telegram even when unchanged")
    parser.add_argument("--fail-on-blockers", action="store_true")
    args = parser.parse_args()

    report = build_report()
    write_reports(report)
    if args.notify or args.force_notify:
        maybe_notify(report, force=args.force_notify)

    print(f"Libra category health: {report['status']} blockers={report['blocker_count']} warnings={report['warning_count']}")
    print(f"JSON: {OUT_JSON}")
    print(f"Report: {OUT_MD}")
    return 1 if args.fail_on_blockers and report["blocker_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
