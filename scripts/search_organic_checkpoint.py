#!/usr/bin/env python3
"""Collect Libra Search Console evidence and send the owner one Day-7 checkpoint."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from libra_search_console import collect_search_console, recommendation_for
from scripts.search_organic_report import build_report

SITE = "https://newton-winai-klinprasom.incomeinclick.in.th/libra/"
SITEMAP = f"{SITE}sitemap.xml"
RELEASE_DATE = date(2026, 10, 30)
CHECKPOINT_DATE = date(2026, 11, 6)


def checkpoint_due(today: date, target: date, state_path: Path) -> bool:
    if today < target:
        return False
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    return state.get("sent_for") != target.isoformat()


def record_sent(state_path: Path, target: date, digest: str) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "schema": "libra-search-checkpoint/1",
        "sent_for": target.isoformat(),
        "report_digest": digest,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _client(credentials_path: Path):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    credentials = service_account.Credentials.from_service_account_file(
        str(credentials_path), scopes=["https://www.googleapis.com/auth/webmasters"],
    )
    return build("searchconsole", "v1", credentials=credentials, cache_discovery=False)


def collect_live(credentials_path: Path, *, today: date) -> dict:
    if not credentials_path.exists():
        return {"status": "UNKNOWN", "reason": "credentials_missing", "property": SITE}
    try:
        return collect_search_console(
            _client(credentials_path), SITE, SITEMAP,
            start=RELEASE_DATE,
            end=max(RELEASE_DATE, today - timedelta(days=2)),
            ensure_sitemap=True,
        )
    except Exception as error:
        return {
            "status": "UNKNOWN",
            "reason": "provider_error",
            "error_class": error.__class__.__name__,
            "property": SITE,
        }


def _append_snapshot(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _message(report: dict, gsc: dict, recommendation: str) -> str:
    metrics = gsc.get("metrics") or {}
    impressions = metrics.get("impressions", "UNKNOWN")
    clicks = metrics.get("clicks", "UNKNOWN")
    traffic = report["traffic"]
    return "\n".join([
        "🔎 Libra organic — checkpoint 7 วัน",
        f"Search Console: {gsc.get('status', 'UNKNOWN')}",
        f"Impressions / clicks: {impressions} / {clicks}",
        f"Qualified views / Amazon outbound: {traffic.get('qualified_views')} / {traffic.get('amazon_outbound')}",
        f"KDP ล่าสุด: USD {report['goal'].get('latest_royalties_usd')}",
        f"คำแนะนำถัดไป: {recommendation}",
        "หมายเหตุ: Search Console อาจหน่วงข้อมูล และยังไม่ใช่หลักฐาน purchase attribution",
    ])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--credentials", type=Path, default=Path(os.environ.get(
        "LIBRA_SEARCH_CONSOLE_CREDENTIALS", "/root/.config/duduang19/search-console.json"
    )))
    parser.add_argument("--ledger", type=Path, default=ROOT / "data/libra-business.db")
    parser.add_argument("--portfolio", type=Path, default=ROOT / "data/search_portfolio.json")
    parser.add_argument("--snapshots", type=Path, default=ROOT / "data/search_console_snapshots.jsonl")
    parser.add_argument("--state", type=Path, default=ROOT / "data/search_console_checkpoint_state.json")
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args(argv)

    if not RELEASE_DATE <= args.as_of <= CHECKPOINT_DATE + timedelta(days=2):
        print(json.dumps({"status": "NOOP", "as_of": args.as_of.isoformat()}))
        return 0

    gsc = collect_live(args.credentials, today=args.as_of)
    report = build_report(args.ledger, args.portfolio, as_of=args.as_of)
    recommendation = recommendation_for(gsc, report["traffic"])
    snapshot = {
        "schema": "libra-search-console-snapshot/1",
        "observed_on": args.as_of.isoformat(),
        "search_console": gsc,
        "traffic": report["traffic"],
        "goal": report["goal"],
        "recommendation": recommendation,
    }
    _append_snapshot(args.snapshots, snapshot)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))

    if args.send and checkpoint_due(args.as_of, CHECKPOINT_DATE, args.state):
        from scripts.organic_experiment_report import send_telegram

        message = _message(report, gsc, recommendation)
        if not send_telegram(message):
            return 1
        digest = hashlib.sha256(message.encode("utf-8")).hexdigest()
        record_sent(args.state, CHECKPOINT_DATE, digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
