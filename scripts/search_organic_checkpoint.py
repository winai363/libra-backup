#!/usr/bin/env python3
"""Collect Libra Search Console evidence and send the owner one Day-7 checkpoint."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
from contextlib import contextmanager
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
MEASUREMENT_END = date(2026, 11, 5)
CHECKPOINT_DATE = date(2026, 11, 8)


def load_state(state_path: Path) -> dict:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return state if isinstance(state, dict) else {}


def _write_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_name(f".{state_path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, state_path)


@contextmanager
def exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


def checkpoint_due(today: date, target: date, state_path: Path) -> bool:
    if today < target:
        return False
    checkpoint = load_state(state_path).get("checkpoint") or {}
    return checkpoint.get("state") not in {"SENDING", "SENT"}


def claim_delivery(state_path: Path, target: date, digest: str) -> None:
    state = load_state(state_path)
    state["schema"] = "libra-search-checkpoint/2"
    state["checkpoint"] = {
        "state": "SENDING",
        "for": target.isoformat(),
        "report_digest": digest,
    }
    _write_state(state_path, state)


def mark_delivery(state_path: Path, delivery_state: str) -> None:
    if delivery_state not in {"SENT", "RETRYABLE"}:
        raise ValueError("unsupported delivery state")
    state = load_state(state_path)
    checkpoint = state.get("checkpoint")
    if not isinstance(checkpoint, dict):
        raise ValueError("delivery was not claimed")
    checkpoint["state"] = delivery_state
    _write_state(state_path, state)


def _client(credentials_path: Path):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    credentials = service_account.Credentials.from_service_account_file(
        str(credentials_path), scopes=["https://www.googleapis.com/auth/webmasters"],
    )
    return build("searchconsole", "v1", credentials=credentials, cache_discovery=False)


def collect_live(credentials_path: Path, *, today: date, state_path: Path) -> dict:
    if not credentials_path.exists():
        return {"status": "UNKNOWN", "reason": "credentials_missing", "property": SITE}
    try:
        state = load_state(state_path)
        submission = state.setdefault("sitemap", {})

        def claim_submission():
            submission["state"] = "ATTEMPTED"
            submission["attempted_on"] = today.isoformat()
            _write_state(state_path, state)

        result = collect_search_console(
            _client(credentials_path), SITE, SITEMAP,
            start=RELEASE_DATE,
            end=MEASUREMENT_END,
            ensure_sitemap=True,
            submission_state=submission,
            before_submit=claim_submission,
            query_metrics=today >= CHECKPOINT_DATE,
        )
        if result.get("sitemap") == "SUBMITTED":
            submission["state"] = "SUBMITTED"
            submission["submitted_on"] = today.isoformat()
            _write_state(state_path, state)
        elif result.get("sitemap") == "PRESENT":
            submission["state"] = "CONFIRMED"
            submission["confirmed_on"] = today.isoformat()
            _write_state(state_path, state)
        return result
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
    impressions = metrics.get("impressions")
    clicks = metrics.get("clicks")
    traffic = report["traffic"]
    show = lambda value: "UNKNOWN" if value is None else value
    return "\n".join([
        "🔎 Libra organic — checkpoint 7 วัน",
        f"Search Console: {gsc.get('status', 'UNKNOWN')}",
        f"Impressions / clicks: {show(impressions)} / {show(clicks)}",
        f"Qualified views / Amazon outbound: {show(traffic.get('qualified_views'))} / {show(traffic.get('amazon_outbound'))}",
        f"KDP ล่าสุด: USD {show(report['goal'].get('latest_royalties_usd'))}",
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
    parser.add_argument("--lock", type=Path, default=ROOT / "data/.search-console-monitor.lock")
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args(argv)

    if args.as_of < RELEASE_DATE:
        print(json.dumps({"status": "NOOP", "as_of": args.as_of.isoformat()}))
        return 0

    with exclusive_lock(args.lock):
        state = load_state(args.state)
        sitemap_done = (state.get("sitemap") or {}).get("state") in {"SUBMITTED", "CONFIRMED"}
        checkpoint_done = (state.get("checkpoint") or {}).get("state") in {"SENDING", "SENT"}
        if sitemap_done and checkpoint_done:
            print(json.dumps({"status": "NOOP", "reason": "monitor_complete"}))
            return 0

        gsc = collect_live(args.credentials, today=args.as_of, state_path=args.state)
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
            digest = hashlib.sha256(message.encode("utf-8")).hexdigest()
            claim_delivery(args.state, CHECKPOINT_DATE, digest)
            try:
                sent = send_telegram(message)
            except Exception:
                sent = False
            mark_delivery(args.state, "SENT" if sent else "RETRYABLE")
            if not sent:
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
