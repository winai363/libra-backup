import json
import os
import glob
import httpx
import logging
import secrets
import time
import re
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from business_ledger import record_hub_event
from kdp_freeze import KDPFrozenError, assert_kdp_mutation_allowed
from settings import CommerceConfigError, CommerceSettings, load_env_file
from content_hub import (
    TrackingConfigError,
    build_outbound_event,
    growth_summary,
    make_tracking_token,
    render_hub_page,
    escape_text,
    paragraphs_html,
    resolve_tracking_token,
)

logger = logging.getLogger("libra")
logging.getLogger("httpx").setLevel(logging.WARNING)

# ── Config from .env ──
ENV_FILE = Path(__file__).parent / ".env"
ENV = load_env_file(ENV_FILE)
# content_hub signs tracking links from the process environment; the service
# unit has no EnvironmentFile, so hand it the value from .env without
# overriding one that was set explicitly. Never a default.
if ENV.get("LIBRA_GROWTH_TRACKING_SECRET"):
    os.environ.setdefault("LIBRA_GROWTH_TRACKING_SECRET", ENV["LIBRA_GROWTH_TRACKING_SECRET"])

app = FastAPI(title="Libra")

KDP_DIR = Path(ENV.get("KDP_DIR", "/root/kdp"))
PROFIT_LEDGER_FILE = Path(__file__).parent / "data" / "libra-business.db"
GROWTH_ARTICLES_DIR = Path(__file__).parent / "data" / "growth_articles"
PROFIT_AGENT_STATE_FILE = Path(__file__).parent / "data" / "profit-agent-state.json"
GROWTH_AUTOPILOT_STATE_FILE = Path(__file__).parent / "data" / "growth-autopilot-state.json"
USERNAME = ENV.get("USERNAME", "")
PASSWORD = ENV.get("PASSWORD", "")
TOKEN = ENV.get("SESSION_TOKEN", "")
LOGIN_ATTEMPTS = {}
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_ATTEMPTS = 5

TELEGRAM_BOT_TOKEN = ENV.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = ENV.get("TELEGRAM_CHAT_ID", "")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline' "
        "https://fonts.googleapis.com https://cdn.tailwindcss.com; "
        "font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline' "
        "https://cdn.tailwindcss.com https://cdn.jsdelivr.net; connect-src 'self'"
    )
    return response


async def translate_th(text: str) -> str:
    """Translate text to Thai using Google Translate."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://translate.googleapis.com/translate_a/single",
                params={"client": "gtx", "sl": "auto", "tl": "th", "dt": "t", "q": text},
                timeout=5,
            )
            return resp.json()[0][0][0]
    except Exception:
        return ""


async def notify(message: str):
    """Send Telegram notification to Pond's personal chat."""
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
    except Exception as e:
        logger.error(f"Telegram notify failed: {e}")


def get_books():
    books = []
    for listing_file in sorted(KDP_DIR.glob("*/listing.json"), reverse=True):
        slug = listing_file.parent.name
        try:
            data = json.loads(listing_file.read_text())
            data["slug"] = slug
            # Find epub file
            epubs = list(listing_file.parent.glob("*.epub"))
            data["has_epub"] = len(epubs) > 0
            data["epub_name"] = epubs[0].name if epubs else None
            # Find paperback PDF
            pdfs = [p for p in listing_file.parent.glob("*paperback*.pdf")]
            data["has_pdf"] = len(pdfs) > 0
            data["pdf_name"] = pdfs[0].name if pdfs else None
            # Find cover
            cover = listing_file.parent / "cover.jpg"
            data["has_cover"] = cover.exists()
            books.append(data)
        except Exception:
            continue
    # Sort by created_at descending
    books.sort(key=lambda b: b.get("created_at", ""), reverse=True)
    return books


def _profit_now() -> datetime:
    return datetime.now().astimezone()


def _load_profit_agent_state() -> dict:
    try:
        payload = json.loads(PROFIT_AGENT_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    allowed = (
        "generated_at", "mode_started_at", "mode", "gates", "gate_reason", "experiments"
    )
    return {key: payload[key] for key in allowed if key in payload}


def _ledger_experiment_views() -> tuple[list[dict], int, list[dict]]:
    if not PROFIT_LEDGER_FILE.exists():
        return [], 0, []
    try:
        with sqlite3.connect(PROFIT_LEDGER_FILE) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT * FROM experiments ORDER BY started_at, id
                """
            ).fetchall()
    except sqlite3.Error:
        return [], 0, []
    history = []
    for row in rows:
        history.append({
            "id": row["id"],
            "slug": row["slug"],
            "hypothesis": row["hypothesis"],
            "variable": row["variable"],
            "evaluation_kind": row["evaluation_kind"],
            "started_at": row["started_at"],
            "earliest_evaluation_at": row["earliest_evaluation_at"],
            "max_direct_cost_usd": row["max_direct_cost_usd"],
            "status": row["status"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
        })
    active = [
        item for item in history
        if item["status"] in {
            "planned", "ready", "executing", "cooldown", "evaluating", "manual_required"
        }
    ]
    return active[:3], len(active), history


def _checkpoint_outcomes(
    started_at: datetime | None,
    now: datetime,
    financials: dict,
    reconciliation: dict,
    experiments: list[dict],
) -> list[dict]:
    definitions = (
        (30, "ledger_truth", "Ledger reconciles and free activity cannot create revenue."),
        (60, "repeatable_contribution", "Positive contribution is proven in two observation windows."),
        (90, "portfolio_objective", "Sustained contribution and fully loaded economics are decided."),
    )
    windows_by_slug: dict[str, int] = {}
    for item in experiments:
        windows_by_slug[item.get("slug", "")] = windows_by_slug.get(item.get("slug", ""), 0) + int(
            (item.get("result") or {}).get("positive_contribution_windows", 0)
        )
    positive_windows = max(windows_by_slug.values(), default=0)
    checkpoints = []
    for day, key, label in definitions:
        if started_at is None:
            checkpoints.append({
                "day": day,
                "key": key,
                "date": None,
                "outcome": "not_started",
                "detail": "The 90-day mode activation date is not persisted.",
                **({"missing_plan_inputs": []} if day == 90 else {}),
            })
            continue
        due_at = started_at + timedelta(days=day)
        outcome = "pending"
        detail = "Checkpoint has not been reached."
        if now >= due_at:
            if day == 30:
                ledger_verified = bool(
                    reconciliation["snapshot_count"] > 0
                    and reconciliation.get("fresh") is True
                    and reconciliation.get("overview_ingestion_complete", True)
                )
                outcome = "passed" if ledger_verified else "missed"
                detail = (
                    "Fresh KDP overview data reconciles within one cent."
                    if ledger_verified
                    else "Fresh KDP overview data does not reconcile within one cent."
                )
            elif day == 60:
                outcome = "passed" if positive_windows >= 2 else "missed"
                detail = (
                    "Positive contribution is recorded in at least two observation windows."
                    if positive_windows >= 2 else "No title has two positive contribution windows."
                )
            else:
                profit = financials["contribution_profit_usd"]
                achieved = profit > 0 and positive_windows >= 2
                outcome = "achieved" if achieved else "missed"
                detail = (
                    "Contribution objective achieved; overhead inputs still required for Profit B."
                    if achieved and not financials["overhead_complete"]
                    else "Contribution objective achieved."
                    if achieved else "Positive sustained contribution was not proven."
                )
        checkpoint = {
            "day": day,
            "key": key,
            "date": due_at.date().isoformat(),
            "outcome": outcome,
            "detail": detail,
        }
        if day == 90:
            checkpoint["missing_plan_inputs"] = [
                key for key, missing in (
                    ("conversion_rate", True),
                    ("royalty_per_paid_order", True),
                    ("production_capacity", True),
                    ("complete_overhead", not financials["overhead_complete"]),
                ) if missing
            ]
        checkpoints.append(checkpoint)
    return checkpoints


def _profit_kpi_plan(ledger: dict) -> dict:
    """Actual vs Plan bars for the /profit page, aligned to the ONE operating
    plan: the 90-day organic mode (window read from the persisted policy).
    DEFAULT_PLAN_TARGETS are full-window (90-day) totals — daily bars compare
    the day-over-day snapshot delta to target/90, monthly bars compare MTD to
    target/3, and the mode view compares the cumulative window total to the
    full target. Actuals come from the verified ledger only."""
    from distribution_report import DEFAULT_PLAN_TARGETS, _metric
    from profit_agent import read_policy_mode

    latest = previous = None
    manual_costs_today = 0.0
    window_rows = []
    window_baseline = None
    if PROFIT_LEDGER_FILE.exists():
        try:
            with sqlite3.connect(PROFIT_LEDGER_FILE) as connection:
                latest = connection.execute(
                    "SELECT observed_at, month, royalties_usd, orders_all_types, kenp "
                    "FROM kdp_snapshots ORDER BY observed_at DESC, id DESC LIMIT 1"
                ).fetchone()
                if latest:
                    previous = connection.execute(
                        "SELECT royalties_usd, orders_all_types, kenp FROM kdp_snapshots "
                        "WHERE month = ? AND date(observed_at) < date(?) "
                        "ORDER BY observed_at DESC, id DESC LIMIT 1",
                        (latest[1], latest[0]),
                    ).fetchone()
                    # Operational spend recorded today only — cost-report/estimate
                    # ingestion is bookkeeping of past production, not a daily cost.
                    manual_costs_today = float(connection.execute(
                        "SELECT COALESCE(SUM(amount_usd), 0) FROM direct_costs "
                        "WHERE source_key NOT LIKE 'cost-report:%' AND source_key NOT LIKE 'cost-estimate:%' "
                        "AND date(incurred_at) = date(?)",
                        (latest[0],),
                    ).fetchone()[0])
                # Latest snapshot of each month = that month's total; the mode
                # window sums them (snapshots only exist from mode start).
                window_rows = connection.execute(
                    "SELECT royalties_usd, orders_all_types, kenp FROM kdp_snapshots s "
                    "WHERE id = (SELECT id FROM kdp_snapshots WHERE month = s.month "
                    "ORDER BY observed_at DESC, id DESC LIMIT 1)"
                ).fetchall()
                # The starting month's first snapshot is the entry meter-reading:
                # KDP "This Month" is calendar-cumulative, so it already contains
                # revenue earned BEFORE the mode began. Subtract it or the window
                # bar overstates progress by that pre-mode amount.
                window_baseline = connection.execute(
                    "SELECT royalties_usd, orders_all_types, kenp FROM kdp_snapshots "
                    "ORDER BY observed_at ASC, id ASC LIMIT 1"
                ).fetchone()
        except sqlite3.Error:
            pass

    policy = read_policy_mode(PROFIT_LEDGER_FILE) or {}
    window_start = (policy.get("started_at") or "")[:10]
    window_end = (policy.get("ends_at") or "")[:10]
    try:
        window_days = max(1, (datetime.fromisoformat(policy["ends_at"])
                              - datetime.fromisoformat(policy["started_at"])).days)
    except (KeyError, ValueError):
        window_days = 90
    window_months = max(1, round(window_days / 30))

    def bars(royalties, orders, kenp, profit_a, plan_scale):
        plan_rev = round(DEFAULT_PLAN_TARGETS["revenue_usd"] * plan_scale, 2)
        plan_orders = max(1, round(DEFAULT_PLAN_TARGETS["orders_downloads"] * plan_scale))
        plan_kenp = max(1, round(DEFAULT_PLAN_TARGETS["kenp"] * plan_scale))
        return [
            _metric("Verified royalties", royalties, plan_rev,
                    f"${royalties:.2f}", f"${plan_rev:.2f}"),
            _metric("Orders / downloads", orders, plan_orders, str(orders), str(plan_orders)),
            _metric("KENP", kenp, plan_kenp, str(kenp), str(plan_kenp)),
            _metric("Profit A · break-even", profit_a, 0.0,
                    f"${profit_a:.2f}", "$0.00",
                    "on_plan" if profit_a > 0 else "behind"),
        ]

    base = previous or (0.0, 0, 0)
    day_royalties = round(float(latest[2]) - float(base[0]), 2) if latest else 0.0
    day_orders = (int(latest[3]) - int(base[1])) if latest else 0
    day_kenp = (int(latest[4]) - int(base[2])) if latest else 0
    day_profit = round(day_royalties - manual_costs_today, 2)
    day_label = f"วันนี้ · {latest[0][:10]}" if latest else "วันนี้"
    if latest and previous is None:
        day_label += " (ยังไม่มี snapshot วันก่อนหน้าในเดือนนี้ = ยอดสะสมเดือน)"

    profit_a = float(ledger["contribution_profit_usd"])
    mode_royalties = round(sum(float(r[0]) for r in window_rows), 2)
    mode_orders = sum(int(r[1]) for r in window_rows)
    mode_kenp = sum(int(r[2]) for r in window_rows)
    if window_baseline is not None:
        mode_royalties = round(mode_royalties - float(window_baseline[0]), 2)
        mode_orders -= int(window_baseline[1])
        mode_kenp -= int(window_baseline[2])

    return {
        "checkpoint": window_end,
        "periods": [
            {"key": "daily", "label": day_label,
             "metrics": bars(day_royalties, day_orders, day_kenp, day_profit, 1 / window_days)},
            {"key": "month",
             "label": f"เดือนนี้ (MTD) · เป้าเฉลี่ยต่อเดือนของรอบ 90 วัน",
             "metrics": bars(float(ledger["verified_royalties_usd"]),
                             int(latest[3]) if latest else 0,
                             int(latest[4]) if latest else 0,
                             profit_a, 1 / window_months)},
            {"key": "mode",
             "label": f"ทั้งรอบ 90 วัน · {window_start} → {window_end}" if window_start else "ทั้งรอบ 90 วัน",
             "metrics": bars(mode_royalties, mode_orders, mode_kenp, profit_a, 1)},
        ],
    }


def build_profit_dashboard() -> dict:
    from business_ledger import portfolio_financials
    from profit_agent import read_policy_mode
    from profit_tracker import build_portfolio
    from profit_pace import (
        build_pace_controller, classify_portfolio, rank_opportunities,
        snapshot_revenue_windows,
    )

    now = _profit_now()
    ledger = portfolio_financials(PROFIT_LEDGER_FILE, now.strftime("%Y-%m"))
    portfolio = build_portfolio(today=now.date())
    experiments, active_experiment_count, experiment_history = _ledger_experiment_views()
    state = _load_profit_agent_state()
    latest_observed_at = None
    if PROFIT_LEDGER_FILE.exists():
        try:
            with sqlite3.connect(PROFIT_LEDGER_FILE) as connection:
                row = connection.execute(
                    "SELECT observed_at FROM kdp_snapshots ORDER BY observed_at DESC LIMIT 1"
                ).fetchone()
            latest_observed_at = row[0] if row else None
        except sqlite3.Error:
            pass
    observed = datetime.fromisoformat(latest_observed_at) if latest_observed_at else None
    data_age_hours = None
    if observed:
        data_age_hours = round(max(0, (now - observed).total_seconds()) / 3600, 1)

    financials = {
        "verified_royalties_usd": ledger["verified_royalties_usd"],
        "direct_costs_usd": ledger["direct_costs_usd"],
        "contribution_profit_usd": ledger["contribution_profit_usd"],
        "fully_loaded_net_profit_usd": ledger["fully_loaded_net_profit_usd"],
        "overhead_complete": ledger["overhead_complete"],
        "cost_complete": ledger["cost_complete"],
        "cost_period": ledger["cost_period"],
    }
    reconciliation = {
        "attributed_royalties_usd": ledger["attributed_royalties_usd"],
        "unattributed_royalties_usd": ledger["unattributed_royalties_usd"],
        "snapshot_count": ledger["snapshot_count"],
        "latest_observed_at": latest_observed_at,
        "data_age_hours": data_age_hours,
        "fresh": data_age_hours is not None and data_age_hours <= 48,
        "overview_ingestion_complete": ledger["overview_ingestion_complete"],
        "title_attribution_complete": ledger["title_attribution_complete"],
    }
    gates = state.get("gates", {})
    blocking_gate_names = {"policy", "freshness", "overview_ingestion", "cost_completeness"}
    operations_ready = bool(gates) and all(
        gates.get(name) == "open" for name in blocking_gate_names
    )
    persisted_start = state.get("mode_started_at")
    start_candidates = [
        datetime.fromisoformat(item["started_at"]) for item in experiment_history
    ]
    if persisted_start:
        start_candidates.append(datetime.fromisoformat(persisted_start))
    started_at = min(start_candidates) if start_candidates else None
    kpi_plan = _profit_kpi_plan(ledger)
    mode_period = next(
        (period for period in kpi_plan["periods"] if period["key"] == "mode"),
        {"metrics": []},
    )
    mode_revenue = next(
        (float(metric["actual"]) for metric in mode_period["metrics"]
         if metric["name"] == "Verified royalties"),
        0.0,
    )
    policy_mode = read_policy_mode(PROFIT_LEDGER_FILE) or {
        "paid_spend_allowed": False, "enabled": False
    }
    pace_start = datetime.fromisoformat(policy_mode["started_at"]) if policy_mode.get("started_at") else now
    pace_end = datetime.fromisoformat(policy_mode["ends_at"]) if policy_mode.get("ends_at") else pace_start + timedelta(days=90)
    try:
        with sqlite3.connect(PROFIT_LEDGER_FILE) as connection:
            revenue_rows = connection.execute(
                "SELECT observed_at, month, royalties_usd FROM kdp_snapshots "
                "ORDER BY observed_at ASC, id ASC"
            ).fetchall()
    except sqlite3.Error:
        revenue_rows = []
    revenue_windows = snapshot_revenue_windows(revenue_rows, now, started_at=pace_start)
    pace = build_pace_controller(
        mode_revenue, 75.0, pace_start, pace_end, now,
        revenue_windows["days_7"], revenue_windows["days_14"],
        data_fresh=reconciliation["fresh"],
    )
    decision_books = []
    for book in portfolio["books"]:
        try:
            listing = json.loads(
                (KDP_DIR / book["slug"] / "listing.json").read_text(encoding="utf-8")
            )
            live_status = listing.get("live_status")
        except (OSError, json.JSONDecodeError):
            live_status = None
        latest_date = (book.get("latest_snapshot") or {}).get("date")
        try:
            title_fresh = (now.date() - datetime.fromisoformat(latest_date).date()).days <= 2
        except (TypeError, ValueError):
            title_fresh = False
        decision_books.append({
            **book, "live_status": live_status, "data_fresh": title_fresh
        })
    allocation = classify_portfolio(decision_books)
    opportunities = rank_opportunities(decision_books)
    return {
        "generated_at": now.isoformat(),
        "financials": financials,
        "kpi_plan": kpi_plan,
        "pace": pace,
        "allocation": allocation,
        "opportunities": opportunities,
        "winner_watch": [item for item in opportunities if item["lane"] == "winner_watch"],
        "reconciliation": reconciliation,
        "policy": {
            **policy_mode,
            "active_experiment_limit": 3,
            "active_experiment_limit_violated": active_experiment_count > 3,
        },
        "experiments": experiments,
        "operations": {
            "status": "ready" if operations_ready else "blocked",
            "gates": gates,
            "active_experiment_count": active_experiment_count,
        },
        "commercial": {
            "status": "positive_contribution" if ledger["positive_contribution_proven"] else "not_proven",
            "repeatable_positive_contribution": False,
        },
        "checkpoints": _checkpoint_outcomes(
            started_at, now, financials, reconciliation, experiment_history
        ),
        "books": portfolio["books"],
        "attention": portfolio["attention"],
        "book_count": portfolio["book_count"],
        "thb_rate": portfolio["thb_rate"],
    }


def build_dashboard_overview() -> dict:
    """Summarize the current autonomous publishing loop for the main dashboard."""
    books = get_books()
    status_counts: dict[str, int] = {}
    for book in books:
        status = book.get("status") or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1

    queue_file = Path(__file__).parent / "queue.txt"
    queue = []
    if queue_file.exists():
        queue = [line.strip() for line in queue_file.read_text().splitlines() if line.strip()]

    from profit_tracker import build_portfolio
    from winner_signals import get_winners

    portfolio = build_portfolio()
    winners = get_winners()
    sales_state_file = KDP_DIR / "sales-sync-state.json"
    sales_state = {}
    mtd_orders_all_types = 0
    mtd_kenp = 0
    if sales_state_file.exists():
        try:
            sales_state = json.loads(sales_state_file.read_text())
            for row in sales_state.get("titles", {}).values():
                mtd_orders_all_types += int(row.get("orders") or 0)
                mtd_kenp += int(row.get("kenp") or row.get("pagesRead") or 0)
        except (OSError, json.JSONDecodeError):
            pass

    title_limit = {}
    title_limit_file = Path(__file__).parent / "data" / "kdp-title-limit.json"
    if title_limit_file.exists():
        try:
            title_limit = json.loads(title_limit_file.read_text())
            datetime.fromisoformat(title_limit.get("retry_after", ""))
            title_limit["active"] = bool(title_limit.get("active"))
        except (OSError, json.JSONDecodeError, ValueError):
            title_limit = {}

    queue_blocker = None
    if queue:
        first = next((book for book in books if book.get("slug") == queue[0]), None)
        if first and first.get("kdp_error"):
            queue_blocker = {
                "slug": first["slug"],
                "title": first.get("title", first["slug"]),
                "error": str(first["kdp_error"]).splitlines()[0][:220],
            }

    adhd_watch_file = KDP_DIR / ".adhd-series-watch-state.json"
    adhd_series = {}
    if adhd_watch_file.exists():
        try:
            adhd_series = json.loads(adhd_watch_file.read_text())
        except (OSError, json.JSONDecodeError):
            pass

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "adhd_series": adhd_series,
        "counts": {
            "total": len(books),
            "uploaded": status_counts.get("uploaded", 0),
            "tracked_asins": sum(
                1 for book in books
                if book.get("status") == "uploaded" and book.get("asin")
            ),
            "ready": status_counts.get("ready", 0),
            "quality_failed": status_counts.get("quality_failed", 0),
            "queued_for_kdp": len(queue),
        },
        "queue": queue,
        "queue_blocker": queue_blocker,
        "sales": {
            "units_30d": portfolio["summary"]["units_30d"],
            "verified_royalties_mtd_usd": portfolio["verified_royalties_mtd_usd"],
            "verified_royalties_mtd_thb": round(
                portfolio["verified_royalties_mtd_usd"] * portfolio["thb_rate"], 0
            ),
            "mtd_orders_all_types": mtd_orders_all_types,
            "mtd_kenp": mtd_kenp,
            "money_warning": "verified KDP overview royalties are the money source of truth",
            "books_with_data": portfolio["summary"]["books_with_data"],
            "last_sync": sales_state.get("updated_at", ""),
        },
        "winners": winners[:3],
        "automation": {
            "generation": ["01:00"],
            "kdp_upload": ["02:30", "06:30"],
            "sales_sync": "09:15",
            "timezone": "Asia/Bangkok",
            "learning": "ยอดขายจริง → หา niche ใกล้เคียง + เช็กฤดูกาลก่อนสร้าง",
            "paused": bool(title_limit.get("active")),
            "pause_reason": "KDP จำกัดการสร้าง title ใหม่ชั่วคราว" if title_limit.get("active") else "",
            "retry_after": title_limit.get("retry_after", ""),
        },
    }


def check_read(request: Request):
    """Public read access — login removed for viewing per user order 2026-07-05.
    Write/publish actions and full-book file downloads still require the token."""
    return


def check_auth(request: Request):
    token = request.cookies.get("libra_token")
    if not TOKEN or not token or not secrets.compare_digest(token, TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")


def reject_frozen_kdp_mutation(action: str, slug: str | None = None) -> None:
    """TOTAL KDP FREEZE: refuse before reading or writing any listing state."""
    try:
        assert_kdp_mutation_allowed(action, slug)
    except KDPFrozenError as exc:
        raise HTTPException(status_code=423, detail={
            "code": exc.code,
            "action": exc.action,
            "reason": str(exc),
        }) from exc


def get_book_dir(slug: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,100}", slug):
        raise HTTPException(status_code=400, detail="Invalid book slug")
    return KDP_DIR / slug


@app.post("/api/auth/login")
async def login(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    attempts = [stamp for stamp in LOGIN_ATTEMPTS.get(client_ip, []) if now - stamp < LOGIN_WINDOW_SECONDS]
    if len(attempts) >= LOGIN_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
    body = await request.json()
    username_ok = secrets.compare_digest(str(body.get("username", "")), USERNAME)
    password_ok = secrets.compare_digest(str(body.get("password", "")), PASSWORD)
    if username_ok and password_ok:
        LOGIN_ATTEMPTS.pop(client_ip, None)
        response = JSONResponse({"ok": True})
        response.set_cookie(
            "libra_token",
            TOKEN,
            httponly=True,
            secure=True,
            samesite="strict",
            max_age=86400,
            path="/libra",
        )
        return response
    attempts.append(now)
    LOGIN_ATTEMPTS[client_ip] = attempts
    raise HTTPException(status_code=401, detail="Wrong password")


@app.get("/api/books")
async def list_books(request: Request, status: str = None):
    check_read(request)
    books = get_books()
    if status:
        books = [b for b in books if b.get("status") == status]
    return books


@app.get("/api/dashboard/overview")
async def dashboard_overview(request: Request):
    check_read(request)
    return build_dashboard_overview()


@app.get("/api/books/{slug}/epub")
async def download_epub(slug: str, request: Request):
    check_auth(request)
    book_dir = get_book_dir(slug)
    if not book_dir.exists():
        raise HTTPException(status_code=404)
    epubs = list(book_dir.glob("*.epub"))
    if not epubs:
        raise HTTPException(status_code=404, detail="No EPUB found")
    return FileResponse(epubs[0], filename=epubs[0].name, media_type="application/epub+zip")


@app.get("/api/books/{slug}/pdf")
async def download_pdf(slug: str, request: Request):
    check_auth(request)
    book_dir = get_book_dir(slug)
    if not book_dir.exists():
        raise HTTPException(status_code=404)
    pdfs = [p for p in book_dir.glob("*paperback*.pdf")]
    if not pdfs:
        raise HTTPException(status_code=404, detail="No PDF found")
    return FileResponse(pdfs[0], filename=pdfs[0].name, media_type="application/pdf")


@app.get("/api/books/{slug}/cover")
async def get_cover(slug: str, request: Request):
    check_read(request)
    cover = get_book_dir(slug) / "cover.jpg"
    if not cover.exists():
        raise HTTPException(status_code=404)
    return FileResponse(cover, media_type="image/jpeg")


@app.post("/api/books/{slug}/generate-pdf")
async def generate_pdf(slug: str, request: Request, force: bool = False):
    check_auth(request)
    book_dir = get_book_dir(slug)
    listing_file = book_dir / "listing.json"
    if not listing_file.exists():
        raise HTTPException(status_code=404, detail="Book not found")
    # Return early (and skip the Telegram notify) if the PDF already exists.
    if not force and list(book_dir.glob("*paperback*.pdf")):
        return {"ok": True, "message": "PDF already exists"}

    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from pdf_builder import build_paperback_pdf

    # Single source of truth for the pandoc invocation (fonts, CJK/Thai line
    # breaking, header, trim size) lives in pdf_builder.build_paperback_pdf.
    try:
        pdf_path = build_paperback_pdf(slug, force=force)
    except ValueError as exc:
        msg = str(exc)
        code = 404 if ("not found" in msg.lower() or "No ebook.md" in msg) else 422
        raise HTTPException(status_code=code, detail=msg)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    title = json.loads(listing_file.read_text()).get("title", slug)
    title_th = await translate_th(title)
    await notify(f"\U0001F4C4 <b>PDF Generated</b>\n{title}\n({title_th})")
    return {"ok": True, "message": "PDF generated", "filename": pdf_path.name}


@app.patch("/api/books/{slug}/status")
async def update_status(slug: str, request: Request):
    check_auth(request)
    listing_file = get_book_dir(slug) / "listing.json"
    if not listing_file.exists():
        raise HTTPException(status_code=404)
    data = json.loads(listing_file.read_text())
    body = await request.json()
    new_status = body.get("status")
    if new_status not in {"ready", "archived"}:
        raise HTTPException(status_code=400, detail="Status can only be changed to ready or archived manually")
    if new_status == "ready":
        # "ready" is the publish queue's entry state — archived stays local.
        reject_frozen_kdp_mutation("mark_ready", slug)
        from quality_gate import validate_book, write_report
        quality = validate_book(slug, require_pdf=True)
        write_report(quality)
        if not quality.passed:
            raise HTTPException(status_code=422, detail={"quality_errors": quality.errors})
    data["status"] = new_status
    if new_status == "ready":
        data["uploaded_at"] = None
    listing_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    title = data.get("title", slug)
    title_th = await translate_th(title)
    await notify(f"↩️ <b>Status changed to {new_status}</b>\n{title}\n({title_th})")
    return {"ok": True, "status": new_status}


@app.post("/api/books")
async def create_book(request: Request):
    """Create a new book entry. Called by Tim/skills after generating an ebook."""
    check_auth(request)
    body = await request.json()
    slug = body.get("slug")
    if not slug:
        raise HTTPException(status_code=400, detail="slug is required")
    book_dir = get_book_dir(slug)
    listing_file = book_dir / "listing.json"
    if not listing_file.exists():
        raise HTTPException(status_code=404, detail=f"No listing.json found at {book_dir}")
    data = json.loads(listing_file.read_text())
    title = data.get("title", slug)
    subtitle = data.get("subtitle", "")
    lang = data.get("language", "")
    keywords_count = len(data.get("keywords", []))
    has_epub = any(book_dir.glob("*.epub"))
    has_cover = (book_dir / "cover.jpg").exists()
    title_th = await translate_th(title)
    parts = [f"📚 <b>New Book on Libra</b>"]
    parts.append(f"<b>{title}</b>")
    if title_th:
        parts.append(f"({title_th})")
    if subtitle:
        parts.append(f"<i>{subtitle}</i>")
    if lang:
        parts.append(f"Language: {lang}")
    parts.append(f"Keywords: {keywords_count}")
    parts.append(f"EPUB: {'✅' if has_epub else '❌'}  Cover: {'✅' if has_cover else '❌'}")
    parts.append(f"\nhttps://libra.incomeinclick.com")
    await notify("\n".join(parts))
    return {"ok": True, "slug": slug, "title": title}


@app.post("/api/books/{slug}/request-approval")
async def request_approval(slug: str, request: Request):
    """Send approval request to Telegram before uploading to KDP"""
    check_auth(request)
    reject_frozen_kdp_mutation("request_approval", slug)
    book_dir = get_book_dir(slug)
    listing_file = book_dir / "listing.json"
    if not listing_file.exists():
        raise HTTPException(status_code=404, detail="Book not found")

    data = json.loads(listing_file.read_text())
    data["approval_pending"] = True
    listing_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    title = data.get("title", slug)
    subtitle = data.get("subtitle", "")
    description = data.get("description", "")[:200]
    keywords = ", ".join(data.get("keywords", [])[:5])
    language = data.get("language", "English")

    title_th = await translate_th(title)

    # Build approval message with review link
    msg = f"📤 <b>New Book Ready for Review</b>\n\n"
    msg += f"<b>{title}</b>\n"
    if title_th:
        msg += f"<i>({title_th})</i>\n"
    if subtitle:
        msg += f"Subtitle: {subtitle}\n"
    msg += f"Language: {language}\n"
    msg += f"Keywords: {keywords}\n\n"
    msg += f"<a href='https://newton-winai-klinprasom.incomeinclick.in.th/libra/review/{slug}?slug={slug}'>📖 Review & Approve</a>"

    await notify(msg)
    return {"ok": True, "message": "Approval request sent to Telegram"}


@app.post("/api/books/{slug}/approve-kdp")
async def approve_kdp(slug: str, request: Request):
    """Approve KDP upload and trigger the upload process"""
    check_auth(request)
    reject_frozen_kdp_mutation("new_title", slug)
    book_dir = get_book_dir(slug)
    listing_file = book_dir / "listing.json"
    if not listing_file.exists():
        raise HTTPException(status_code=404, detail="Book not found")

    from quality_gate import validate_book, write_report
    quality = validate_book(slug, require_pdf=True, check_urls=True, require_editorial=True)
    write_report(quality)
    if not quality.passed:
        raise HTTPException(status_code=422, detail={"quality_errors": quality.errors})

    data = json.loads(listing_file.read_text())
    if data.get("kdp_uploading"):
        raise HTTPException(status_code=409, detail="This book is already being uploaded")
    data["approval_pending"] = False
    data["kdp_uploading"] = True
    listing_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    title = data.get("title", slug)
    subtitle = data.get("subtitle", "")
    title_th = await translate_th(title)

    # Auto-generate cover if missing
    cover_file = book_dir / "cover.jpg"
    if not cover_file.exists() or cover_file.stat().st_size < 10000:
        try:
            from cover_generator import generate_cover as _gen_cover
            _gen_cover(
                book_dir   = book_dir,
                title      = title,
                subtitle   = subtitle,
                author     = data.get("author", "WK Bui"),
                categories = data.get("categories", []),
                keywords   = data.get("keywords", []),
            )
        except Exception:
            pass

    # Trigger KDP upload in background
    upload_log = open(KDP_DIR / "logs" / f"upload-{slug}.log", "a")
    subprocess.Popen(
        ["python3", str(Path(__file__).parent / "kdp_upload.py"), slug],
        stdout=upload_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    upload_log.close()

    msg = f"⏳ <b>Uploading to KDP...</b>\n{title}\n({title_th})"
    await notify(msg)

    return {"ok": True, "message": "KDP upload started"}


@app.get("/api/books/{slug}/content")
async def get_book_content(slug: str, request: Request):
    """Return the book's markdown content for preview."""
    check_auth(request)
    book_dir = get_book_dir(slug)
    md_file = book_dir / "ebook.md"
    if md_file.exists():
        return {"format": "markdown", "content": md_file.read_text()}
    # Fallback: extract text from EPUB
    epubs = list(book_dir.glob("*.epub"))
    if epubs and epubs[0].stat().st_size > 100:
        try:
            import zipfile
            from html.parser import HTMLParser
            class TextExtractor(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.text = []
                def handle_data(self, data):
                    self.text.append(data)
            content_parts = []
            with zipfile.ZipFile(str(epubs[0])) as zf:
                for name in sorted(zf.namelist()):
                    if name.endswith(('.xhtml', '.html', '.htm')):
                        html = zf.read(name).decode('utf-8', errors='ignore')
                        parser = TextExtractor()
                        parser.feed(html)
                        content_parts.append('\n'.join(parser.text))
            return {"format": "text", "content": '\n\n'.join(content_parts)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"EPUB read error: {e}")
    raise HTTPException(status_code=404, detail="No content found (ebook.md or EPUB)")


@app.get("/preview/{slug}", response_class=HTMLResponse)
async def preview_page(slug: str, request: Request):
    """Full book preview — cover, details, and full content."""
    check_read(request)
    book_dir = get_book_dir(slug)
    listing_file = book_dir / "listing.json"
    if not listing_file.exists():
        return HTMLResponse("<h1>Book not found</h1>", status_code=404)
    html_path = Path(__file__).parent / "templates" / "preview.html"
    return HTMLResponse(html_path.read_text())


@app.get("/review/{slug}", response_class=HTMLResponse)
async def review_page(slug: str, request: Request):
    """Review page to check book details before KDP upload"""
    check_read(request)
    book_dir = get_book_dir(slug)
    listing_file = book_dir / "listing.json"
    if not listing_file.exists():
        return HTMLResponse("<h1>Book not found</h1>", status_code=404)
    html_path = Path(__file__).parent / "templates" / "review.html"
    return HTMLResponse(html_path.read_text())


@app.get("/approval/{slug}", response_class=HTMLResponse)
async def approval_page(slug: str, request: Request):
    """Approval page for KDP upload"""
    check_read(request)
    book_dir = get_book_dir(slug)
    listing_file = book_dir / "listing.json"
    if not listing_file.exists():
        return HTMLResponse("<h1>Book not found</h1>", status_code=404)
    html_path = Path(__file__).parent / "templates" / "approval.html"
    return HTMLResponse(html_path.read_text())


@app.get("/api/pipeline-status")
async def pipeline_status(request: Request):
    """Return all books with per-step pipeline status."""
    check_read(request)
    books_out = []
    for listing_file in sorted(KDP_DIR.glob("*/listing.json"), reverse=True):
        if listing_file.parent.name == "logs":
            continue
        slug = listing_file.parent.name
        try:
            data = json.loads(listing_file.read_text())
        except Exception:
            continue
        book_dir = listing_file.parent
        has_md    = (book_dir / "ebook.md").exists()
        has_epub  = len(list(book_dir.glob("*.epub"))) > 0
        cover     = book_dir / "cover.jpg"
        has_cover = cover.exists() and cover.stat().st_size > 10_000
        has_pdf   = len([p for p in book_dir.glob("*paperback*.pdf")]) > 0
        kdp_book_id = data.get("kdp_book_id") or ""
        has_upload  = bool(kdp_book_id) or data.get("status") == "uploaded"
        steps = {
            "generate": has_md,
            "epub":     has_epub,
            "cover":    has_cover,
            "upload":   has_upload,
            "pdf":      has_pdf,
        }
        # current step = first False step, or "done" if all True
        step_order = ["generate", "epub", "cover", "upload", "pdf"]
        current = "done"
        for s in step_order:
            if not steps[s]:
                current = s
                break
        books_out.append({
            "slug":         slug,
            "title":        data.get("title", slug),
            "language":     data.get("language", ""),
            "created_at":   data.get("created_at", ""),
            "uploaded_at":  data.get("uploaded_at", ""),
            "status":       data.get("status", "ready"),
            "kdp_uploading":data.get("kdp_uploading", False),
            "kdp_error":    data.get("kdp_error", ""),
            "kdp_book_id":  kdp_book_id,
            "steps":        steps,
            "current_step": current,
        })
    books_out.sort(key=lambda b: b.get("created_at", ""), reverse=True)
    return {"books": books_out, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M")}


@app.get("/status", response_class=HTMLResponse)
async def status_page(request: Request):
    check_read(request)
    html_path = Path(__file__).parent / "templates" / "status.html"
    return HTMLResponse(html_path.read_text())


@app.get("/api/strategy")
async def strategy_board(request: Request):
    """Depth-loop command center: hero books + plan timeline + checkpoint."""
    check_read(request)
    cfg_path = Path(__file__).parent / "data" / "strategy_timeline.json"
    cfg = json.loads(cfg_path.read_text())
    today = datetime.now().date()

    # month-to-date downloads per ASIN (free-promo units show as orders/$0)
    mtd = {}
    state_path = KDP_DIR / "sales-sync-state.json"
    mtd_royalties = 0.0
    if state_path.exists():
        state = json.loads(state_path.read_text())
        for asin, t in state.get("titles", {}).items():
            mtd[asin] = t.get("orders", 0)
            mtd_royalties += t.get("royalties", 0.0) or 0.0

    # lifetime revenue from feedback histories
    lifetime = 0.0
    for fh in KDP_DIR.glob("*/feedback-history.json"):
        try:
            for snap in json.loads(fh.read_text()):
                lifetime += float(snap.get("revenue_usd") or 0)
        except Exception:
            continue

    heroes = []
    for slug in cfg["hero_slugs"]:
        lp = KDP_DIR / slug / "listing.json"
        if not lp.exists():
            continue
        l = json.loads(lp.read_text())
        pb = l.get("paperback", {})
        heroes.append({
            "slug": slug,
            "title": l.get("title", slug),
            "language": l.get("language", ""),
            "series": (l.get("series") or {}).get("title"),
            "ebook": {
                "asin": l.get("asin"),
                "live_status": l.get("live_status"),
                "select": bool(l.get("kdp_select")),
                "aplus": (l.get("aplus") or {}).get("status"),
            },
            "paperback": {
                "submitted_at": pb.get("submitted_at"),
                "price_usd": pb.get("price_usd"),
                "asin": pb.get("asin"),
                "status": (pb.get("live_status") or ("IN_REVIEW" if pb.get("submitted_at") else None)),
            },
            "free_promo": l.get("free_promo"),
            "promo_days_left": cfg["promo_days_left"].get(slug),
            "mtd_downloads": mtd.get(l.get("asin"), 0),
            "amazon_url": f"https://www.amazon.com/dp/{l.get('asin')}" if l.get("asin") else None,
        })

    # live counts across the whole shelf
    live_ebooks = 0
    for lp in KDP_DIR.glob("*/listing.json"):
        try:
            if json.loads(lp.read_text()).get("live_status") == "LIVE":
                live_ebooks += 1
        except Exception:
            continue

    events = []
    for ev in cfg["events"]:
        d0 = datetime.strptime(ev["date"], "%Y-%m-%d").date()
        d1 = datetime.strptime(ev.get("end", ev["date"]), "%Y-%m-%d").date()
        state = "done" if (ev.get("done") or d1 < today) else ("today" if d0 <= today <= d1 else "upcoming")
        events.append({**ev, "state": state})

    return {
        "strategy_name": cfg["strategy_name"],
        "checkpoint": cfg["checkpoint"],
        "days_to_checkpoint": (datetime.strptime(cfg["checkpoint"], "%Y-%m-%d").date() - today).days,
        "summary": {
            "live_ebooks": live_ebooks,
            "paperbacks_submitted": sum(1 for h in heroes if h["paperback"]["submitted_at"]),
            "mtd_downloads": sum(mtd.values()),
            "mtd_royalties_usd": round(mtd_royalties, 2),
            "lifetime_revenue_usd": round(lifetime, 2),
        },
        "heroes": heroes,
        "timeline": events,
        "actions_bui": cfg["actions_bui"],
    }


@app.get("/api/distribution")
async def distribution_dashboard_api(request: Request):
    """Return the July distribution experiment report with money/free split."""
    check_read(request)
    from distribution_report import build_report
    return build_report()


@app.get("/api/distribution/monitor")
async def distribution_monitor_api(request: Request):
    """Return the single-glance monitor for the active Libra distribution plan."""
    check_read(request)
    from distribution_report import CATEGORY_HEALTH_STATE, build_monitor, build_report, _load_json
    return build_monitor(
        build_report(),
        overview=build_dashboard_overview(),
        category_health=_load_json(CATEGORY_HEALTH_STATE, {}),
    )


@app.get("/api/kdp-agent")
async def kdp_agent_api(request: Request):
    """Return the KDP auto manager agent state and role verdicts."""
    check_read(request)
    from distribution_report import CATEGORY_HEALTH_STATE, build_monitor, build_report, _load_json
    monitor = build_monitor(
        build_report(),
        overview=build_dashboard_overview(),
        category_health=_load_json(CATEGORY_HEALTH_STATE, {}),
    )
    return {
        "generated_at": monitor["generated_at"],
        "agent": monitor["kdp_agent"],
        "roles": monitor["actual_vs_plan"]["roles"],
        "actual_vs_plan": monitor["actual_vs_plan"]["metrics"],
    }


@app.get("/distribution", response_class=HTMLResponse)
async def distribution_dashboard_page(request: Request):
    check_read(request)
    from distribution_report import DOWNLOADS_HTML, build_report, render_html, write_outputs, write_chrome_guide
    if DOWNLOADS_HTML.exists():
        return HTMLResponse(DOWNLOADS_HTML.read_text(encoding="utf-8"))
    report = build_report()
    write_outputs(report)
    write_chrome_guide(report)
    return HTMLResponse(render_html(report))


@app.get("/distribution/monitor", response_class=HTMLResponse)
async def distribution_monitor_page(request: Request):
    check_read(request)
    from distribution_report import CATEGORY_HEALTH_STATE, build_monitor, build_report, render_monitor_html, _load_json
    monitor = build_monitor(
        build_report(),
        overview=build_dashboard_overview(),
        category_health=_load_json(CATEGORY_HEALTH_STATE, {}),
    )
    return HTMLResponse(render_monitor_html(monitor))


@app.get("/api/profit/portfolio")
async def profit_portfolio(request: Request):
    """Return verified financial, experiment, and checkpoint business truth."""
    check_read(request)
    return build_profit_dashboard()


@app.get("/api/profit/agent")
async def profit_agent_state(request: Request):
    """Return a sanitized view of the latest profit-agent state."""
    check_read(request)
    return _load_profit_agent_state()


@app.post("/api/profit/{slug}/snapshot")
async def record_profit_snapshot(slug: str, request: Request):
    """Record one KDP feedback snapshot for profit tracking."""
    check_auth(request)
    get_book_dir(slug)
    body = await request.json()
    allowed = {
        "date", "bsr", "units_7d", "kenp_7d", "impressions_7d",
        "reviews_count", "avg_rating", "revenue_usd",
    }
    snapshot = {k: body[k] for k in allowed if k in body}
    if not snapshot:
        raise HTTPException(status_code=400, detail="No snapshot fields provided")
    from feedback_loop import record_snapshot
    result = record_snapshot(slug, snapshot)
    return {"ok": True, "snapshot": result}


@app.get("/profit", response_class=HTMLResponse)
async def profit_page(request: Request):
    check_read(request)
    html_path = Path(__file__).parent / "templates" / "profit.html"
    return HTMLResponse(html_path.read_text())


# ── Live Audit endpoints ───────────────────────────────────────────────────────

@app.get("/api/audit/report")
async def get_audit_report(request: Request):
    """Return latest audit report JSON."""
    check_read(request)
    report_file = KDP_DIR / "logs" / "audit_report.json"
    if not report_file.exists():
        raise HTTPException(status_code=404, detail="No audit report found. Run kdp_live_audit.py first.")
    return json.loads(report_file.read_text())


@app.post("/api/audit/run")
async def run_audit(request: Request):
    """Trigger audit in background. Returns immediately."""
    check_auth(request)
    import subprocess as _sp
    _sp.Popen(
        ["python3", str(Path(__file__).parent / "kdp_live_audit.py")],
        stdout=open(str(KDP_DIR / "logs" / "audit.log"), "a"),
        stderr=subprocess.STDOUT,
    )
    await notify("🔍 <b>KDP Live Audit started</b>\nCheck /audit when complete.")
    return {"ok": True, "message": "Audit started in background. Check /audit for results."}


@app.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request):
    """Show the live audit HTML dashboard."""
    check_read(request)
    report_file = KDP_DIR / "logs" / "audit_report.html"
    if not report_file.exists():
        return HTMLResponse("""
<html><body style="font-family:sans-serif;max-width:800px;margin:80px auto;text-align:center">
<h2>No audit report yet</h2>
<p>Run the audit first:</p>
<pre style="background:#eee;padding:16px;border-radius:8px;display:inline-block">
python3 /root/libra/kdp_live_audit.py
</pre>
<p>Or trigger it via the API:</p>
<pre style="background:#eee;padding:16px;border-radius:8px;display:inline-block">
POST /api/audit/run
</pre>
</body></html>""")
    return HTMLResponse(report_file.read_text())


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "templates" / "index.html"
    return HTMLResponse(html_path.read_text())


# ── Content Hub & first-party tracking (Task 5) ────────────────────────────
# Public organic-traffic pages plus signed, privacy-safe outbound Amazon
# click tracking. See content_hub.py for token signing and the destination
# allowlist.

GROWTH_HUB_CAMPAIGN = "content-hub"
_SLUG_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,100}")


def _live_book_asin(slug):
    """Return (listing, asin) for a book slug that is both uploaded AND
    currently live on Amazon, or None if the slug is malformed, unknown,
    missing an ASIN, or not live_status == "LIVE" (e.g. BLOCKED/pulled —
    same convention as aplus_assets.py and kdp_bookshelf_roster.py). A
    blocked/dead ASIN must never get a tracked CTA sending traffic to it."""
    if not isinstance(slug, str) or not _SLUG_ID_RE.fullmatch(slug):
        return None
    listing_file = KDP_DIR / slug / "listing.json"
    if not listing_file.exists():
        return None
    try:
        listing = json.loads(listing_file.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    asin = listing.get("asin")
    if not asin or listing.get("live_status") != "LIVE":
        return None
    return listing, asin


def _hub_cta_path(slug: str, campaign: str, destination: str) -> str:
    return f"/growth/out/{make_tracking_token(slug, campaign, destination)}"


@app.get("/growth/books/{slug}", response_class=HTMLResponse)
async def growth_book_hub_page(slug: str):
    """Public book hub page with exactly one tracked Amazon CTA."""
    result = _live_book_asin(slug)
    if result is None:
        return HTMLResponse("<h1>Book not found</h1>", status_code=404)
    listing, asin = result
    destination = f"https://www.amazon.com/dp/{asin}"
    try:
        cta_path = _hub_cta_path(slug, GROWTH_HUB_CAMPAIGN, destination)
    except TrackingConfigError:
        raise HTTPException(status_code=503, detail="Growth tracking is temporarily unavailable")
    html_path = Path(__file__).parent / "templates" / "hub_book.html"
    page = render_hub_page(html_path.read_text(), {
        "TITLE": escape_text(listing.get("title", slug)),
        "DESCRIPTION": escape_text(listing.get("description", "")),
        "CTA_URL": escape_text(cta_path),
        "CTA_LABEL": "View on Amazon",
    })
    return HTMLResponse(page)


@app.get("/growth/articles/{article_id}", response_class=HTMLResponse)
async def growth_article_hub_page(article_id: str):
    """Public article hub page with exactly one tracked Amazon CTA linking
    to the article's target book."""
    if not _SLUG_ID_RE.fullmatch(article_id):
        return HTMLResponse("<h1>Article not found</h1>", status_code=404)
    article_file = GROWTH_ARTICLES_DIR / f"{article_id}.json"
    if not article_file.exists():
        return HTMLResponse("<h1>Article not found</h1>", status_code=404)
    try:
        article = json.loads(article_file.read_text())
    except (OSError, json.JSONDecodeError):
        return HTMLResponse("<h1>Article not found</h1>", status_code=404)

    target_slug = article.get("target_slug")
    result = _live_book_asin(target_slug)
    if result is None:
        return HTMLResponse("<h1>Article not found</h1>", status_code=404)
    _, asin = result
    destination = f"https://www.amazon.com/dp/{asin}"
    campaign = article.get("campaign") or GROWTH_HUB_CAMPAIGN
    try:
        cta_path = _hub_cta_path(target_slug, campaign, destination)
    except TrackingConfigError:
        raise HTTPException(status_code=503, detail="Growth tracking is temporarily unavailable")
    html_path = Path(__file__).parent / "templates" / "hub_article.html"
    page = render_hub_page(html_path.read_text(), {
        "TITLE": escape_text(article.get("title", article_id)),
        "BODY": paragraphs_html(article.get("body", "")),
        "CTA_URL": escape_text(cta_path),
        "CTA_LABEL": escape_text(article.get("cta_label", "View on Amazon")),
    })
    return HTMLResponse(page)


@app.get("/growth/out/{token}")
async def growth_outbound_click(token: str):
    """Verify a signed tracking token, record one privacy-safe
    amazon_outbound hub event, and redirect to the approved destination."""
    try:
        payload = resolve_tracking_token(token, allowed_hosts=_outbound_allowlists())
    except TrackingConfigError:
        raise HTTPException(status_code=503, detail="Growth tracking is temporarily unavailable")
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid tracking link")
    if payload.get("destination_kind") == "payhip":
        # A Payhip click gets its own event kind and an opaque click id; a sale
        # cannot be attributed to it until a round trip is proven.
        event = build_outbound_event(
            payload["slug"], payload["campaign"],
            event_kind="payhip_outbound", click_id=payload.get("click_id"),
        )
    else:
        event = build_outbound_event(payload["slug"], payload["campaign"])
    record_hub_event(PROFIT_LEDGER_FILE, event)
    return RedirectResponse(url=payload["destination"], status_code=307)


def _payhip_hosts() -> frozenset:
    raw = ENV.get("PAYHIP_ALLOWED_HOSTS", "payhip.com,www.payhip.com")
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def _outbound_allowlists() -> dict:
    from content_hub import APPROVED_AMAZON_HOSTS
    return {"amazon": APPROVED_AMAZON_HOSTS, "payhip": _payhip_hosts()}


@app.get("/growth/products/{slug}", response_class=HTMLResponse)
async def growth_product_page(slug: str):
    """Public product page: one tracked Payhip CTA, attribution shown as unknown."""
    from payhip_catalog import list_products

    product = next((p for p in list_products(PROFIT_LEDGER_FILE)
                    if p["slug"] == slug and p["status"] == "live"), None)
    if product is None:
        return HTMLResponse("<h1>Product not found</h1>", status_code=404)
    listing_file = get_book_dir(slug) / "listing.json"
    listing = json.loads(listing_file.read_text(encoding="utf-8")) if listing_file.exists() else {}
    try:
        token = make_tracking_token(
            slug, GROWTH_HUB_CAMPAIGN, product["provider_product_id"],
            destination_kind="payhip", allowed_hosts=_payhip_hosts(),
        )
    except TrackingConfigError:
        raise HTTPException(status_code=503, detail="Growth tracking is temporarily unavailable")
    price = f"{product['price_minor'] // 100}.{product['price_minor'] % 100:02d} {product['currency']}"
    html_path = Path(__file__).parent / "templates" / "hub_book.html"
    page = render_hub_page(html_path.read_text(), {
        "TITLE": escape_text(listing.get("title", slug)),
        "DESCRIPTION": escape_text(
            (listing.get("description", "") + f"\n\nPrix : {price}").strip()
        ),
        "CTA_URL": escape_text(f"/growth/out/{token}"),
        "CTA_LABEL": f"Acheter — {price}",
    })
    return HTMLResponse(page)


@app.get("/api/growth/summary")
async def growth_summary_api(request: Request):
    """Aggregate totals for tracked hub events (e.g. Amazon outbound
    clicks) by event kind and slug."""
    check_read(request)
    return growth_summary(PROFIT_LEDGER_FILE)


# ── Growth Autopilot dashboard (Task 10) ───────────────────────────────────
# Read-only operating view over data/growth-autopilot-state.json (written
# atomically by growth_autopilot.run_growth_controller, Task 9) plus ledger
# read models (verified revenue, tracked traffic, growth evidence). This
# page/API exposes no mutation path -- no "run now"/"execute" control ever
# bypasses the controller. See GET /growth and GET /api/growth/state.

def _load_growth_autopilot_state() -> dict:
    """Safe read of the Growth Autopilot's latest state. A missing or
    corrupt file is an honest "no run yet" ({}), never a 500 -- the same
    fail-closed-to-empty convention as _load_profit_agent_state."""
    try:
        payload = json.loads(GROWTH_AUTOPILOT_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    allowed = (
        "generated_at", "mode", "locked", "phase", "started_at", "readiness",
        "observations_collected", "scored_titles", "plan", "executed", "blocked", "reason",
        "verification",
    )
    return {key: payload[key] for key in allowed if key in payload}


def build_growth_dashboard() -> dict:
    """Combine the Growth Autopilot's latest persisted state with ledger
    read models (verified revenue, tracked traffic, growth evidence) into
    one read-only operating view for Task 10. `plan.actions` (proposed)
    and `executed` (adapter-verified) are always kept as distinct keys --
    a planned action is never merged into or mistaken for an executed
    one."""
    from business_ledger import growth_evidence as ledger_growth_evidence, portfolio_financials
    from growth_policy import DAILY_CAP_THB, INITIAL_TITLE_CAP_THB, MONTHLY_CAP_THB

    state = _load_growth_autopilot_state()
    data_available = bool(state)

    day = None
    if state.get("generated_at") and state.get("started_at"):
        try:
            generated = datetime.fromisoformat(state["generated_at"])
            started = datetime.fromisoformat(state["started_at"])
            day = (generated - started).days + 1
        except ValueError:
            day = None

    now = _profit_now()
    month = now.strftime("%Y-%m")
    financials = portfolio_financials(PROFIT_LEDGER_FILE, month)

    evidence_rows = ledger_growth_evidence(PROFIT_LEDGER_FILE)
    evidence_by_kind: dict = {}
    for row in evidence_rows:
        evidence_by_kind[row["kind"]] = evidence_by_kind.get(row["kind"], 0) + 1

    plan = state.get("plan") or {}

    return {
        "data_available": data_available,
        "generated_at": state.get("generated_at"),
        "mode": state.get("mode"),
        "phase": state.get("phase"),
        "day": day,
        "started_at": state.get("started_at"),
        "readiness": state.get("readiness") or {},
        "observations_collected": state.get("observations_collected", 0),
        "portfolio": state.get("scored_titles") or [],
        "plan": {"actions": plan.get("actions", []), "phase": plan.get("phase")},
        "executed": state.get("executed") or [],
        "blocked": state.get("blocked") or [],
        "verification": state.get("verification"),
        "evidence_funnel": {"total": len(evidence_rows), "by_kind": evidence_by_kind},
        "verified_revenue": {
            "verified_royalties_usd": financials.get("verified_royalties_usd", 0.0),
            "month": month,
            "snapshot_count": financials.get("snapshot_count", 0),
        },
        "traffic": growth_summary(PROFIT_LEDGER_FILE),
        "caps_thb": {"daily": DAILY_CAP_THB, "monthly": MONTHLY_CAP_THB, "initial_title": INITIAL_TITLE_CAP_THB},
        "contribution_profit_usd": financials.get("contribution_profit_usd"),
        # Deliberately named for exactly what this checks -- the
        # calendar/Growth-Gate WINDOW (phase == "growth"), never
        # "ads may spend now". Real spend additionally requires per-title
        # ads_eligibility (royalty growth / KENP >=100 / >=20 tracked
        # clicks, see growth_policy.ads_eligibility), which this dashboard
        # deliberately does not recompute -- see _growth_spend_html's
        # caption, which must always accompany this value.
        "growth_gate_window_open": state.get("phase") == "growth",
    }


def _growth_score_text(value) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def _growth_portfolio_table_html(portfolio: list) -> str:
    if not portfolio:
        return '<p class="text-slate-400">No scored titles yet.</p>'
    rows = "".join(
        '<tr class="border-t border-slate-700">'
        f'<td class="py-2 pr-4 font-medium text-white">{escape_text(row.get("slug"))}</td>'
        f'<td class="py-2 pr-4">{escape_text(row.get("classification"))}</td>'
        f'<td class="py-2 pr-4">{escape_text(_growth_score_text(row.get("score")))}</td>'
        f'<td class="py-2">{"fresh" if row.get("evidence_fresh") else "stale"}</td>'
        "</tr>"
        for row in portfolio
    )
    return (
        '<table class="w-full text-left"><thead><tr class="text-slate-500">'
        '<th class="py-2 pr-4">Title</th><th class="py-2 pr-4">Classification</th>'
        '<th class="py-2 pr-4">Score</th><th class="py-2">Evidence</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )


def _growth_evidence_funnel_html(funnel: dict) -> str:
    total = funnel.get("total", 0)
    if not total:
        return '<p class="text-slate-400">No growth evidence recorded yet.</p>'
    items = "".join(
        f"<li>{escape_text(kind)}: {escape_text(count)}</li>"
        for kind, count in sorted((funnel.get("by_kind") or {}).items())
    )
    return f'<p>{escape_text(total)} evidence row(s) on record.</p><ul class="mt-1 list-disc list-inside">{items}</ul>'


def _growth_traffic_html(traffic: dict) -> str:
    total = (traffic or {}).get("total_events", 0)
    if not total:
        return '<p class="text-slate-400">No tracked traffic yet.</p>'
    items = "".join(
        f"<li>{escape_text(kind)}: {escape_text(count)}</li>"
        for kind, count in sorted((traffic.get("by_event_kind") or {}).items())
    )
    return f'<p>{escape_text(total)} tracked event(s).</p><ul class="mt-1 list-disc list-inside">{items}</ul>'


def _growth_planned_html(actions: list) -> str:
    if not actions:
        return '<p class="text-slate-400">No organic tests proposed this cycle.</p>'
    items = "".join(
        f'<li><span class="font-medium text-white">{escape_text(a.get("slug"))}</span>'
        f" &middot; {escape_text(a.get('variable'))}</li>"
        for a in actions
    )
    return f'<ul class="list-disc list-inside space-y-1">{items}</ul>'


def _growth_executed_html(executed: list) -> str:
    if not executed:
        return '<p class="text-slate-400">No verified actions yet.</p>'
    items = []
    for entry in executed:
        evidence = entry.get("evidence") or {}
        change = evidence.get("verified_state_change") or {}
        detail = ""
        if change:
            detail = f' (before: {escape_text(change.get("before"))} &rarr; after: {escape_text(change.get("after"))})'
        confirmation = evidence.get("confirmation_id")
        conf_text = f" &middot; confirmation {escape_text(confirmation)}" if confirmation else ""
        items.append(
            f'<li><span class="font-medium text-white">{escape_text(entry.get("slug"))}</span>'
            f' &middot; {escape_text(entry.get("kind"))}{conf_text}{detail}</li>'
        )
    return f'<ul class="list-disc list-inside space-y-1">{"".join(items)}</ul>'


def _growth_blocked_html(blocked: list) -> str:
    if not blocked:
        return '<p class="text-slate-400">Nothing blocked this cycle.</p>'
    items = "".join(
        f'<li><span class="font-medium text-white">{escape_text(entry.get("slug"))}</span>'
        f' &middot; {escape_text(entry.get("kind"))}: {escape_text(entry.get("reason"))}</li>'
        for entry in blocked
    )
    return f'<ul class="list-disc list-inside space-y-1">{items}</ul>'


def _growth_verification_html(verification) -> str:
    """Read-only section for verify_growth_state's findings (Task 11a
    --verify). Silent (empty string, no section at all) when no
    verification has run yet -- this must never fabricate a section for a
    run that never happened. Flagged entries (an action recorded "executed"
    without verifiable before/after proof) are listed with their reason,
    same as the existing Blocked actions section's style, no buttons."""
    if not verification:
        return ""
    status = verification.get("status", "unknown")
    checked = verification.get("checked", 0)
    flagged = verification.get("flagged") or []
    if flagged:
        flagged_html = '<ul class="list-disc list-inside space-y-1 text-red-300">' + "".join(
            f'<li><span class="font-medium">{escape_text(entry.get("slug"))}</span>'
            f' &middot; {escape_text(entry.get("kind"))}: {escape_text(entry.get("reason"))}</li>'
            for entry in flagged
        ) + "</ul>"
    else:
        flagged_html = '<p class="text-slate-400">No flagged actions.</p>'
    return (
        '<section class="py-6 border-b border-slate-700">'
        '<h2 class="font-bold text-white">Verification</h2>'
        '<p class="text-xs text-slate-400 mt-1">Reconciliation of executed actions against verifiable before/after proof.</p>'
        f'<p class="mt-3 text-sm text-slate-200">Status: <span class="font-medium">{escape_text(status)}</span>'
        f" &middot; {escape_text(checked)} action(s) checked.</p>"
        f'<h3 class="mt-4 text-sm font-semibold text-red-300">Flagged</h3>'
        f'<p class="text-xs text-slate-400 mt-1">Recorded "executed" but without verifiable before/after proof.</p>'
        f'<div class="mt-2 text-sm text-slate-200">{flagged_html}</div>'
        "</section>"
    )


def _growth_verified_revenue_html(revenue: dict) -> str:
    amount = revenue.get("verified_royalties_usd")
    amount_text = f"${float(amount):.2f}" if amount is not None else "—"
    return (
        f'<p><span class="text-2xl font-bold text-white">{escape_text(amount_text)}</span> '
        f'verified KDP royalties for {escape_text(revenue.get("month"))} '
        f'({escape_text(revenue.get("snapshot_count", 0))} snapshot(s)).</p>'
    )


def _growth_spend_html(view: dict) -> str:
    caps = view.get("caps_thb") or {}
    contribution = view.get("contribution_profit_usd")
    contribution_text = f"${float(contribution):.2f}" if contribution is not None else "—"
    window_open = view.get("growth_gate_window_open")
    return (
        f'<p>Growth Gate window: <span class="font-medium">{"open" if window_open else "closed"}</span> '
        f'&middot; caps THB {float(caps.get("daily", 0)):.2f}/day, '
        f'THB {float(caps.get("monthly", 0)):.2f}/month, '
        f'THB {float(caps.get("initial_title", 0)):.2f}/title (initial).</p>'
        f'<p class="mt-1 text-xs text-slate-400">Spend also requires per-title eligibility '
        f'(royalty growth / KENP &ge;100 / &ge;20 tracked clicks).</p>'
        f'<p class="mt-2">Verified contribution profit (lifetime): '
        f'<span class="font-medium">{escape_text(contribution_text)}</span></p>'
    )


@app.get("/growth", response_class=HTMLResponse)
async def growth_dashboard_page(request: Request):
    """Read-only Growth Autopilot operating dashboard -- Task 10. Renders
    data/growth-autopilot-state.json plus ledger read models, with
    "Planned" always visually distinct from "Executed with evidence". No
    control surface: this page cannot start, stop, or execute anything --
    see the Growth Autopilot CLI (scripts/libra_growth_autopilot.py) for
    the only way to actually run the controller."""
    check_read(request)
    view = build_growth_dashboard()
    readiness = view["readiness"]

    if not view["data_available"]:
        readiness_label, readiness_tone, readiness_detail = "No run yet", "text-slate-400", ""
    elif readiness.get("mutation_allowed"):
        readiness_label, readiness_tone = "Ready", "text-emerald-300"
        readiness_detail = f"{readiness.get('open_incidents', 0)} open incident(s)."
    else:
        readiness_label, readiness_tone = "Blocked", "text-red-300"
        readiness_detail = f"Reason: {readiness.get('reason', 'unknown')}."
        if readiness.get("blocked_slugs"):
            readiness_detail += f" Blocked titles: {', '.join(readiness['blocked_slugs'])}."

    no_run_banner = ""
    if not view["data_available"]:
        no_run_banner = (
            '<div class="mt-6 p-4 rounded-lg bg-amber-500/10 border border-amber-500/30 '
            'text-amber-200 text-sm">No Growth Autopilot run recorded yet. Run '
            "<code>scripts/libra_growth_autopilot.py --shadow</code> to generate the first state.</div>"
        )

    phase_day = view["phase"] or "—"
    if view["day"]:
        phase_day = f"{phase_day} / day {view['day']}"

    context = {
        "NO_RUN_BANNER": no_run_banner,
        "PHASE_DAY": escape_text(phase_day),
        "MODE": escape_text(view["mode"] or "—"),
        "READINESS_LABEL": escape_text(readiness_label),
        "READINESS_TONE": readiness_tone,
        "READINESS_DETAIL": escape_text(readiness_detail),
        "FRESHNESS": escape_text(view["generated_at"] or "no data yet"),
        "VERIFIED_REVENUE_BODY": _growth_verified_revenue_html(view["verified_revenue"]),
        "PORTFOLIO_TABLE": _growth_portfolio_table_html(view["portfolio"]),
        "EVIDENCE_FUNNEL": _growth_evidence_funnel_html(view["evidence_funnel"]),
        "TRAFFIC_BODY": _growth_traffic_html(view["traffic"]),
        "PLANNED_BODY": _growth_planned_html(view["plan"]["actions"]),
        "EXECUTED_BODY": _growth_executed_html(view["executed"]),
        "BLOCKED_BODY": _growth_blocked_html(view["blocked"]),
        "VERIFICATION_SECTION": _growth_verification_html(view["verification"]),
        "SPEND_BODY": _growth_spend_html(view),
    }
    html_path = Path(__file__).parent / "templates" / "growth.html"
    return HTMLResponse(render_hub_page(html_path.read_text(), context))


@app.get("/api/growth/state")
async def growth_state_api(request: Request):
    """Read-only JSON view of the Growth Autopilot's latest state plus
    ledger read models -- Task 10. Mirrors GET /growth's data. A missing
    state file returns data_available: false rather than a 404/500."""
    check_read(request)
    return build_growth_dashboard()


# ── Commerce webhooks (Payhip + Stripe, TEST MODE ONLY) ──────────────────────
# Public endpoints. Everything they touch is fail-closed: without complete
# commerce configuration they return 503 rather than guessing a default, and no
# response, log line, or stored row ever contains a secret, signature, raw body
# or customer identity.

def commerce_settings() -> CommerceSettings:
    try:
        return CommerceSettings.from_sources(ENV)
    except CommerceConfigError as exc:
        raise HTTPException(status_code=503, detail={"code": "commerce_not_configured",
                                                     "reason": str(exc)}) from exc


def _commerce_receipt(receipt: dict) -> Response:
    status = receipt["status"]
    if status == "conflict":
        # Same provider event id, different content: a critical incident is
        # already open and no projection was touched.
        return JSONResponse(status_code=409, content={"status": "conflict"})
    return JSONResponse(status_code=200, content={"status": "accepted" if status == "inserted" else status})


@app.post("/api/webhooks/stripe")
async def stripe_webhook_route(request: Request):
    from stripe_webhook import StripeWebhookError, normalize_stripe_event, verify_stripe_event

    settings = commerce_settings()
    raw_body = await request.body()
    if len(raw_body) > settings.max_webhook_bytes:
        raise HTTPException(status_code=413, detail={"code": "body_too_large"})

    try:
        event = verify_stripe_event(
            raw_body,
            request.headers.get("Stripe-Signature", ""),
            settings,
            now=int(time.time()),
        )
        normalized = normalize_stripe_event(
            event, raw_body, received_at=datetime.now(timezone.utc).isoformat()
        )
    except StripeWebhookError as exc:
        raise HTTPException(status_code=_stripe_status(exc.code),
                            detail={"code": exc.code}) from exc

    from commerce_ledger import record_provider_event
    from commerce_reconciliation import reconcile_event

    receipt = record_provider_event(PROFIT_LEDGER_FILE, normalized)
    if receipt["status"] == "inserted":
        # The inbox row is committed first: a projection failure returns 500 but
        # leaves the event retryable instead of losing it.
        reconcile_event(PROFIT_LEDGER_FILE, normalized["provider"], normalized["event_id"])
    return _commerce_receipt(receipt)


def _stripe_status(code: str) -> int:
    if code == "body_too_large":
        return 413
    if code in ("wrong_mode", "wrong_account"):
        return 403
    if code == "unsupported_event":
        return 202
    return 400


@app.post("/api/webhooks/payhip/{callback_token}")
async def payhip_webhook_route(callback_token: str, request: Request):
    from payhip_webhook import (
        PayhipWebhookError,
        normalize_payhip_event,
        verify_payhip_callback_token,
    )

    settings = commerce_settings()
    try:
        verify_payhip_callback_token(callback_token, settings.payhip_webhook_token)
    except PayhipWebhookError:
        # Generic 404: the response must not reveal that this path exists.
        raise HTTPException(status_code=404, detail="Not Found")

    raw_body = await request.body()
    if len(raw_body) > settings.max_webhook_bytes:
        raise HTTPException(status_code=413, detail={"code": "body_too_large"})

    try:
        normalized = normalize_payhip_event(
            raw_body, settings, received_at=datetime.now(timezone.utc).isoformat()
        )
    except PayhipWebhookError as exc:
        status = 202 if exc.code == "unsupported_event" else 400
        raise HTTPException(status_code=status, detail={"code": exc.code}) from exc

    from commerce_ledger import record_provider_event
    from commerce_reconciliation import reconcile_event

    receipt = record_provider_event(PROFIT_LEDGER_FILE, normalized)
    if receipt["status"] == "inserted":
        reconcile_event(PROFIT_LEDGER_FILE, normalized["provider"], normalized["event_id"])
    return _commerce_receipt(receipt)


@app.get("/api/commerce/summary")
async def commerce_summary_api(request: Request):
    check_auth(request)
    from commerce_ledger import open_incidents
    from commerce_reconciliation import currency_totals

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": ENV.get("LIBRA_COMMERCE_MODE", "unconfigured"),
        "by_currency": currency_totals(PROFIT_LEDGER_FILE),
        "open_incidents": open_incidents(PROFIT_LEDGER_FILE),
        # Until a controlled transaction proves the click id survives checkout,
        # we cannot attribute a sale to a campaign. Say so rather than show 0.
        "attribution": {"status": "unknown", "verified_sales": 0},
    }


@app.post("/api/webhooks/lemonsqueezy")
async def lemonsqueezy_webhook_route(request: Request):
    """Lemon Squeezy is the merchant of record: its signed order is the money
    fact, so the signature is the whole defence and it covers the raw bytes."""
    from lemonsqueezy_webhook import (
        LemonSqueezyWebhookError,
        normalize_lemonsqueezy_event,
        verify_lemonsqueezy_signature,
    )

    settings = commerce_settings()
    raw_body = await request.body()
    if len(raw_body) > settings.max_webhook_bytes:
        raise HTTPException(status_code=413, detail={"code": "body_too_large"})

    try:
        verify_lemonsqueezy_signature(
            raw_body, request.headers.get("X-Signature", ""), settings
        )
        normalized = normalize_lemonsqueezy_event(
            raw_body, settings, received_at=datetime.now(timezone.utc).isoformat()
        )
    except LemonSqueezyWebhookError as exc:
        status = {
            "body_too_large": 413,
            "not_configured": 503,
            "wrong_store": 403,
            "unsupported_event": 202,
        }.get(exc.code, 400)
        raise HTTPException(status_code=status, detail={"code": exc.code}) from exc

    from commerce_ledger import record_provider_event
    from commerce_reconciliation import reconcile_event

    receipt = record_provider_event(PROFIT_LEDGER_FILE, normalized)
    if receipt["status"] == "inserted":
        reconcile_event(PROFIT_LEDGER_FILE, normalized["provider"], normalized["event_id"])
    return _commerce_receipt(receipt)
