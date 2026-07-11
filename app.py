import json
import os
import glob
import httpx
import logging
import secrets
import time
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("libra")
logging.getLogger("httpx").setLevel(logging.WARNING)

# ── Config from .env ──
ENV = {}
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            ENV[k.strip()] = v.strip()

app = FastAPI(title="Libra")

KDP_DIR = Path(ENV.get("KDP_DIR", "/root/kdp"))
PROFIT_LEDGER_FILE = Path(__file__).parent / "data" / "libra-business.db"
PROFIT_AGENT_STATE_FILE = Path(__file__).parent / "data" / "profit-agent-state.json"
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
    return {
        "generated_at": now.isoformat(),
        "financials": financials,
        "kpi_plan": _profit_kpi_plan(ledger),
        "reconciliation": reconciliation,
        "policy": {
            **(read_policy_mode(PROFIT_LEDGER_FILE) or {"paid_spend_allowed": False, "enabled": False}),
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
    import subprocess
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
