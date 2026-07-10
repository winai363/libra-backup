"""Distribution reporting for the July Libra KDP experiment.

This module treats KDP royalties as the money source of truth and keeps
free downloads separate from paid revenue. It powers the HTML dashboard,
daily Telegram report, and manual-action packs.
"""

from __future__ import annotations

import html
import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


LIBRA_DIR = Path(__file__).parent
KDP_DIR = LIBRA_DIR.parent / "kdp"
STRATEGY_FILE = LIBRA_DIR / "data" / "strategy_timeline.json"
REDDIT_SCHEDULE = LIBRA_DIR / "data" / "reddit_promo_schedule.json"
MANUAL_STATE_FILE = LIBRA_DIR / "data" / "manual-task-state.json"
LOVELY_DIR = LIBRA_DIR.parent / "lovelybooks"
REPORT_JSON = LIBRA_DIR / "data" / "distribution-report.json"
DOWNLOADS_HTML = LIBRA_DIR.parent / "downloads" / "libra-distribution-dashboard.html"
CHROME_GUIDE = LIBRA_DIR.parent / "downloads" / "kdp-pins" / "CLAUDE-CHROME-POSTING-GUIDE.md"
CATEGORY_HEALTH_STATE = LIBRA_DIR / "data" / "category_health_state.json"

DEFAULT_PLAN_TARGETS = {
    "revenue_usd": 25.0,
    "orders_downloads": 120,
    "kenp": 500,
    "free_downloads": 100,
}


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return default


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _history(slug: str) -> list[dict]:
    rows = _load_json(KDP_DIR / slug / "feedback-history.json", [])
    return rows if isinstance(rows, list) else []


def _window_units(slug: str, start: date | None, end: date | None) -> tuple[int, int, float]:
    if not (start and end):
        return 0, 0, 0.0
    units = kenp = 0
    royalties = 0.0
    for row in _history(slug):
        row_date = _parse_date(row.get("date"))
        if not row_date or not (start <= row_date <= end + timedelta(days=1)):
            continue
        unit_value = row.get("delta_units") if "delta_units" in row else row.get("units_7d")
        kenp_value = row.get("delta_kenp") if "delta_kenp" in row else row.get("kenp_7d")
        units += max(0, int(unit_value or 0))
        kenp += max(0, int(kenp_value or 0))
        royalties += float(row.get("revenue_usd") or 0.0)
    return units, kenp, round(royalties, 2)


def _month_units(slug: str, today: date) -> tuple[int, int, float]:
    start = today.replace(day=1)
    return _window_units(slug, start, today)


def _sales_state() -> dict:
    state = _load_json(KDP_DIR / "sales-sync-state.json", {})
    titles = state.get("titles", {}) if isinstance(state, dict) else {}
    orders = 0
    kenp = 0
    royalties = 0.0
    for row in titles.values():
        orders += int(row.get("orders") or 0)
        kenp += int(row.get("kenp") or row.get("pagesRead") or 0)
        royalties += float(row.get("royalties") or 0.0)
    return {
        "last_sync": state.get("updated_at", ""),
        "mtd_orders_all_types": orders,
        "mtd_kenp": kenp,
        "mtd_royalties_usd": round(royalties, 2),
        "warning": "orders include free downloads; royalties are the money source of truth",
    }


def _lovely_status() -> dict:
    book = LOVELY_DIR / "book_indexed.flag"
    tool = LOVELY_DIR / "leserunde_tool.flag"
    published = LOVELY_DIR / "shots" / "published.png"
    if book.exists() and tool.exists() and published.exists():
        status = "ready"
    elif book.exists() and tool.exists():
        status = "tool_ready"
    elif book.exists():
        status = "book_indexed"
    else:
        status = "waiting"
    return {
        "status": status,
        "book_indexed": book.exists(),
        "aktion_starten": tool.exists(),
        "leserunde_published": published.exists(),
        "next_action": (
            "25-26 ก.ค. ตอบคอมเมนต์ใน LovelyBooks และชวนโหลดฟรีจาก Amazon; ห้ามส่งไฟล์เอง"
            if status == "ready"
            else "รอ flag จาก watcher หรือเช็ก LovelyBooks อีกครั้ง"
        ),
    }


def _promo_for(listing: dict) -> dict | None:
    promo = listing.get("free_promo") or listing.get("promotion")
    return promo if isinstance(promo, dict) else None


def _reddit_posts() -> list[dict]:
    data = _load_json(REDDIT_SCHEDULE, {})
    posts = data.get("posts", []) if isinstance(data, dict) else []
    return posts if isinstance(posts, list) else []


def _manual_progress(heroes: list[dict]) -> dict:
    state = _load_json(MANUAL_STATE_FILE, {})
    pinterest = state.get("pinterest", {}) if isinstance(state, dict) else {}
    completed = pinterest.get("completed_slugs", []) if isinstance(pinterest, dict) else []
    targets = pinterest.get("target_slugs", []) if isinstance(pinterest, dict) else []
    completed_slugs = [slug for slug in completed if isinstance(slug, str)]
    target_slugs = [slug for slug in targets if isinstance(slug, str)]
    if not target_slugs:
        target_slugs = [hero["slug"] for hero in heroes]
    remaining_slugs = [slug for slug in target_slugs if slug not in completed_slugs]
    return {
        "pinterest": {
            "target_slugs": target_slugs,
            "completed_slugs": completed_slugs,
            "completed_count": len(completed_slugs),
            "remaining_slugs": remaining_slugs,
            "remaining_count": len(remaining_slugs),
            "last_updated": pinterest.get("last_updated", ""),
            "note": pinterest.get("note", ""),
        }
    }


def _today_actions(today: date, heroes: list[dict], lovely: dict) -> list[dict]:
    actions: list[dict] = []
    today_s = today.isoformat()
    for hero in heroes:
        promo = hero.get("free_promo") or {}
        start = _parse_date(promo.get("start"))
        end = _parse_date(promo.get("end"))
        if start and end and start - timedelta(days=3) <= today <= end:
            if today < start:
                actions.append({
                    "channel": "Pinterest",
                    "slug": hero["slug"],
                    "action": f"ปูพินก่อนแจกฟรี {hero['title']} ({start}→{end})",
                })
            else:
                actions.append({
                    "channel": "Amazon/KDP",
                    "slug": hero["slug"],
                    "action": f"เช็กว่าโปรฟรี active และดูโหลดสะสม {hero['free_downloads']} ครั้ง",
                })
    for post in _reddit_posts():
        if post.get("date") == today_s:
            actions.append({
                "channel": "Reddit",
                "slug": post.get("slug", ""),
                "action": "ส่ง Telegram พร้อมข้อความ r/FreeEBOOKS ให้บุ๋ยก๊อปไปโพสต์",
            })
    if today_s in {"2026-07-25", "2026-07-26"} and lovely.get("status") == "ready":
        actions.append({
            "channel": "LovelyBooks",
            "slug": "adhd-workbook-german-adults",
            "action": "โพสต์/ตอบใน Leserunde ว่าโหลดฟรีจาก Amazon ได้วันนี้; ห้ามส่งไฟล์เอง",
        })
    if not actions:
        actions.append({"channel": "Monitor", "slug": "", "action": "ดูรายงานวันนี้และเตรียมรอบแจกฟรีถัดไป"})
    return actions


def build_report(today: date | None = None) -> dict:
    today = today or date.today()
    cfg = _load_json(STRATEGY_FILE, {})
    hero_slugs = cfg.get("hero_slugs", [])
    heroes = []
    active = []
    upcoming = []
    total_free = 0

    for slug in hero_slugs:
        listing = _load_json(KDP_DIR / slug / "listing.json", {})
        promo = _promo_for(listing)
        start = _parse_date((promo or {}).get("start"))
        end = _parse_date((promo or {}).get("end"))
        free_units, promo_kenp, promo_roy = _window_units(slug, start, end)
        mtd_units, mtd_kenp, mtd_roy = _month_units(slug, today)
        total_free += free_units
        hero = {
            "slug": slug,
            "title": listing.get("title", slug),
            "asin": listing.get("asin", ""),
            "amazon_url": f"https://www.amazon.com/dp/{listing.get('asin')}" if listing.get("asin") else "",
            "live_status": listing.get("live_status", ""),
            "select": bool(listing.get("kdp_select")),
            "aplus": (listing.get("aplus") or {}).get("status", ""),
            "paperback_status": (listing.get("paperback") or {}).get("live_status", ""),
            "free_promo": promo,
            "free_downloads": free_units,
            "promo_kenp": promo_kenp,
            "promo_royalties_usd": promo_roy,
            "mtd_units": mtd_units,
            "mtd_kenp": mtd_kenp,
            "mtd_royalties_usd": mtd_roy,
        }
        heroes.append(hero)
        if start and end and start <= today <= end:
            active.append({"slug": slug, "start": start.isoformat(), "end": end.isoformat(), "downloads": free_units})
        elif start and start > today:
            upcoming.append({"slug": slug, "start": start.isoformat(), "end": end.isoformat() if end else ""})

    lovely = _lovely_status()
    checkpoint = _parse_date(cfg.get("checkpoint"))
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "strategy": cfg.get("strategy_name", ""),
        "checkpoint": {
            "date": cfg.get("checkpoint", ""),
            "days_left": (checkpoint - today).days if checkpoint else None,
        },
        "money": _sales_state(),
        "free_promos": {
            "total_downloads_in_promo_windows": total_free,
            "active": active,
            "upcoming": upcoming,
        },
        "lovelybooks": lovely,
        "manual_progress": _manual_progress(heroes),
        "hero_books": heroes,
        "today_actions": [],
        "rules": [
            "ห้ามนับ digitalOrders เป็นเงินจริง เพราะรวมโหลดฟรี",
            "เงินฟรีดูจาก royalties เท่านั้น",
            "LovelyBooks ohne Verlosung ห้ามส่งไฟล์เอง ให้ชี้ไป Amazon free promo",
            "ยังไม่ซื้อ paid promo จนกว่าจะเห็นโหลด/รีวิว/KU/paid sale ตามเกณฑ์",
        ],
    }
    report["today_actions"] = _today_actions(today, heroes, lovely)
    return report


def _ratio_label(done: int, total: int) -> str:
    return f"{done}/{total}" if total else "0/0"


def _sales_sync_fresh(last_sync: str, generated_at: str) -> bool:
    sync_day = _parse_date(last_sync)
    generated_day = _parse_date(generated_at)
    return bool(sync_day and generated_day and sync_day >= generated_day)


def _percent(actual: float, plan: float) -> int:
    if plan <= 0:
        return 100 if actual >= plan else 0
    return max(0, min(100, round((actual / plan) * 100)))


def _progress_status(percent: int, *, early_label: str = "early") -> str:
    if percent >= 100:
        return "on_plan"
    if percent >= 60:
        return "watch"
    if percent >= 25:
        return early_label
    return "behind"


def _metric(name: str, actual: float, plan: float, actual_label: str, plan_label: str, status: str | None = None) -> dict:
    pct = _percent(actual, plan)
    return {
        "name": name,
        "actual": actual,
        "plan": plan,
        "actual_label": actual_label,
        "plan_label": plan_label,
        "percent": pct,
        "status": status or _progress_status(pct),
    }


def _build_actual_vs_plan(
    report: dict,
    *,
    hero_total: int,
    hero_live: int,
    hero_select: int,
    hero_aplus: int,
    pin_done: int,
    pin_total: int,
    blocker_count: int,
) -> dict:
    money = report.get("money", {})
    promos = report.get("free_promos", {})
    metrics = [
        _metric(
            "Revenue",
            float(money.get("mtd_royalties_usd") or 0.0),
            DEFAULT_PLAN_TARGETS["revenue_usd"],
            f"${float(money.get('mtd_royalties_usd') or 0.0):.2f}",
            f"${DEFAULT_PLAN_TARGETS['revenue_usd']:.2f}",
            "early" if float(money.get("mtd_royalties_usd") or 0.0) > 0 else "behind",
        ),
        _metric(
            "Orders/downloads",
            int(money.get("mtd_orders_all_types") or 0),
            DEFAULT_PLAN_TARGETS["orders_downloads"],
            str(int(money.get("mtd_orders_all_types") or 0)),
            str(DEFAULT_PLAN_TARGETS["orders_downloads"]),
        ),
        _metric(
            "KENP",
            int(money.get("mtd_kenp") or 0),
            DEFAULT_PLAN_TARGETS["kenp"],
            str(int(money.get("mtd_kenp") or 0)),
            str(DEFAULT_PLAN_TARGETS["kenp"]),
        ),
        _metric(
            "Free downloads",
            int(promos.get("total_downloads_in_promo_windows") or 0),
            DEFAULT_PLAN_TARGETS["free_downloads"],
            str(int(promos.get("total_downloads_in_promo_windows") or 0)),
            str(DEFAULT_PLAN_TARGETS["free_downloads"]),
            "behind",
        ),
        _metric("Hero LIVE", hero_live, hero_total, _ratio_label(hero_live, hero_total), _ratio_label(hero_total, hero_total), "on_plan" if hero_live == hero_total else "behind"),
        _metric("KDP Select", hero_select, hero_total, _ratio_label(hero_select, hero_total), _ratio_label(hero_total, hero_total), "on_plan" if hero_select == hero_total else "behind"),
        _metric("A+ submitted", hero_aplus, hero_total, _ratio_label(hero_aplus, hero_total), _ratio_label(hero_total, hero_total), "on_plan" if hero_aplus == hero_total else "behind"),
        _metric("Pinterest", pin_done, pin_total, str(pin_done), str(pin_total), "on_plan" if pin_done == pin_total else "behind"),
        _metric("Blockers", 0 if blocker_count else 1, 1, str(blocker_count), "0", "on_plan" if blocker_count == 0 else "blocked"),
    ]
    roles = {
        "CFO": {
            "status": "early" if float(money.get("mtd_royalties_usd") or 0.0) > 0 else "behind",
            "verdict": "มีเงินจริงแล้ว แต่ proof ยังเล็กมาก; ห้ามเพิ่มงบจนกว่าจะเห็นผลหลัง promo windows",
            "target": f"${DEFAULT_PLAN_TARGETS['revenue_usd']:.2f} royalties by checkpoint",
        },
        "COO": {
            "status": "on_plan" if blocker_count == 0 and hero_live == hero_total and hero_select == hero_total else "blocked",
            "verdict": "ระบบหลักพร้อมเดินรอบแจกฟรี; โฟกัสคิวงาน manual และ freshness",
            "target": "0 blockers, 0 KDP queue, fresh daily sync",
        },
        "CMO": {
            "status": "on_plan" if pin_total and pin_done == pin_total else "behind",
            "verdict": "distribution ยังช้ากว่าแผน เพราะ Pinterest ยังไม่ครบ 4/4",
            "target": "Pinterest 4/4 plus Reddit/LovelyBooks on promo dates",
        },
        "KDP Strategist": {
            "status": "watch",
            "verdict": "รอข้อมูลจากรอบแจกฟรี 15-26 ก.ค. ก่อนตัดสิน paid promo หรือ Amazon Ads",
            "target": "Use downloads, KENP, reviews, and royalties after each promo window",
        },
    }
    return {
        "targets": DEFAULT_PLAN_TARGETS,
        "metrics": metrics,
        "roles": roles,
    }


def _build_kdp_agent(actual_vs_plan: dict, blockers: list[str]) -> dict:
    next_actions = []
    if blockers:
        next_actions.append("แก้ blocker ก่อนให้ agent เสนอ growth action")
    else:
        next_actions.append("อย่าเพิ่งซื้อ paid promo จนกว่าจะถึง checkpoint หรือมี proof หลัง promo windows")
    cmo = actual_vs_plan.get("roles", {}).get("CMO", {})
    if cmo.get("status") == "behind":
        next_actions.append("เร่ง Pinterest ที่เหลือให้ครบ 4/4 ก่อนรอบแจกฟรี 15-26 ก.ค.")
    next_actions.append("หลังแต่ละ free promo ให้เทียบ downloads, KENP, reviews, royalties กับ target แล้วค่อยเลือก next move")
    return {
        "name": "Libra KDP Auto Manager",
        "mode": "auto_advisor",
        "authority": "read, diagnose, set targets, recommend next actions; no paid spend or KDP publishing mutation without guard/approval",
        "cadence": "daily after KDP sales sync",
        "roles": ["CFO", "COO", "CMO", "KDP Strategist"],
        "next_actions": next_actions,
        "guardrails": [
            "KDP royalties are the money source of truth",
            "orders/downloads include free activity and cannot justify spend alone",
            "no paid promo before checkpoint unless real proof appears",
            "do not send files on LovelyBooks; point readers to Amazon free promo",
        ],
    }


def build_monitor(
    report: dict,
    *,
    overview: dict | None = None,
    category_health: dict | None = None,
) -> dict:
    """Build a single-glance monitor from the distribution report.

    The monitor intentionally keeps old quality-failed drafts out of the
    blocker count. This July plan is distribution-first; blockers are only
    issues that can stop the active hero-book experiment.
    """
    overview = overview or {}
    category_health = category_health or _load_json(CATEGORY_HEALTH_STATE, {})
    heroes = report.get("hero_books", [])
    hero_total = len(heroes)
    hero_live = sum(1 for h in heroes if h.get("live_status") == "LIVE")
    hero_select = sum(1 for h in heroes if h.get("select"))
    hero_aplus = sum(1 for h in heroes if h.get("aplus") in {"submitted", "approved", "live"})
    scheduled_or_done = sum(1 for h in heroes if h.get("free_promo"))

    pinterest = report.get("manual_progress", {}).get("pinterest", {})
    pin_done = int(pinterest.get("completed_count") or 0)
    pin_remaining = int(pinterest.get("remaining_count") or 0)
    pin_total = pin_done + pin_remaining

    blockers: list[str] = []
    counts = overview.get("counts", {}) if isinstance(overview, dict) else {}
    if int(counts.get("queued_for_kdp") or 0) > 0:
        blockers.append(f"KDP queue has {counts.get('queued_for_kdp')} pending item(s)")
    queue_blocker = overview.get("queue_blocker") if isinstance(overview, dict) else None
    if queue_blocker:
        blockers.append(f"Queue blocker: {queue_blocker.get('slug', 'unknown')}")
    signature = category_health.get("signature", {}) if isinstance(category_health, dict) else {}
    if int(signature.get("blocker_count") or 0) > 0:
        blockers.append(f"Category blockers: {signature.get('blocker_count')}")
    if hero_live < hero_total:
        blockers.append(f"Hero ebooks live: {_ratio_label(hero_live, hero_total)}")
    if hero_select < hero_total:
        blockers.append(f"Hero KDP Select: {_ratio_label(hero_select, hero_total)}")
    if hero_aplus < hero_total:
        blockers.append(f"Hero A+ submitted: {_ratio_label(hero_aplus, hero_total)}")

    money = report.get("money", {})
    sync_last = money.get("last_sync") or overview.get("sales", {}).get("last_sync", "")
    sync_fresh = _sales_sync_fresh(sync_last, report.get("generated_at", ""))
    if not sync_fresh:
        blockers.append("KDP sales sync is stale")

    days_left = report.get("checkpoint", {}).get("days_left")
    timeline_label = "On track"
    if isinstance(days_left, int) and days_left < 0:
        timeline_label = "Past checkpoint"
        blockers.append("Checkpoint date has passed")
    elif report.get("free_promos", {}).get("active"):
        timeline_label = "Promo active"

    score = 100
    score -= min(40, len(blockers) * 12)
    if pin_total and pin_remaining:
        score -= 8
    if scheduled_or_done < max(1, hero_total - 1):
        score -= 10
    if float(money.get("mtd_royalties_usd") or 0) <= 0:
        score -= 12
    score = max(0, min(100, score))
    if blockers:
        status = "blocked"
        label = "Blocked"
    elif score >= 75:
        status = "on_track"
        label = "On track"
    else:
        status = "watch"
        label = "Watch"

    actual_vs_plan = _build_actual_vs_plan(
        report,
        hero_total=hero_total,
        hero_live=hero_live,
        hero_select=hero_select,
        hero_aplus=hero_aplus,
        pin_done=pin_done,
        pin_total=pin_total,
        blocker_count=len(blockers),
    )
    kdp_agent = _build_kdp_agent(actual_vs_plan, blockers)

    return {
        "generated_at": report.get("generated_at", ""),
        "overall": {"status": status, "label": label, "score": score},
        "money": {
            "royalties": float(money.get("mtd_royalties_usd") or 0.0),
            "orders": int(money.get("mtd_orders_all_types") or 0),
            "kenp": int(money.get("mtd_kenp") or 0),
        },
        "timeline": {
            "label": timeline_label,
            "days_left": days_left,
            "checkpoint": report.get("checkpoint", {}).get("date", ""),
        },
        "setup": {
            "hero_live": _ratio_label(hero_live, hero_total),
            "hero_select": _ratio_label(hero_select, hero_total),
            "hero_aplus": _ratio_label(hero_aplus, hero_total),
            "scheduled_or_done_promos": _ratio_label(scheduled_or_done, hero_total),
        },
        "manual": {
            "pinterest": {
                "label": f"{pin_done}/{pin_total} done" if pin_total else "0/0 done",
                "remaining": pinterest.get("remaining_slugs", []),
            }
        },
        "promo": {
            "active": report.get("free_promos", {}).get("active", []),
            "upcoming": report.get("free_promos", {}).get("upcoming", []),
            "free_downloads": int(report.get("free_promos", {}).get("total_downloads_in_promo_windows") or 0),
        },
        "health": {
            "kdp_queue": str(counts.get("queued_for_kdp", 0)),
            "category": signature.get("status", "unknown"),
            "category_warnings": int(signature.get("warning_count") or 0),
            "sales_sync": "fresh" if sync_fresh else "stale",
        },
        "blockers": {"count": len(blockers), "items": blockers},
        "today_actions": report.get("today_actions", []),
        "decision": {
            "recommendation": (
                "แก้ blocker ก่อนตัดสินงบ" if blockers
                else "รอ checkpoint ก่อนซื้อ paid promo"
            )
        },
        "actual_vs_plan": actual_vs_plan,
        "kdp_agent": kdp_agent,
    }


def telegram_message(report: dict) -> str:
    money = report["money"]
    pinterest = report.get("manual_progress", {}).get("pinterest", {})
    lines = [
        "📚 Libra Distribution Daily",
        f"เงินจริง MTD: ${money['mtd_royalties_usd']:.2f} | orders/downloads: {money['mtd_orders_all_types']} orders/downloads",
        f"Checkpoint: {report['checkpoint']['date']} ({report['checkpoint']['days_left']} วัน)",
        f"LovelyBooks: {report['lovelybooks']['status']}",
        f"Pinterest: done {pinterest.get('completed_count', 0)} / remaining {pinterest.get('remaining_count', 0)}",
        "",
        "งานวันนี้:",
    ]
    for item in report["today_actions"][:8]:
        lines.append(f"- {item['channel']}: {item['action']}")
    upcoming = report["free_promos"].get("upcoming", [])[:3]
    if upcoming:
        lines.append("")
        lines.append("โปรถัดไป:")
        for p in upcoming:
            lines.append(f"- {p['slug']}: {p['start']}→{p['end']}")
    lines.append("")
    lines.append("หมายเหตุ: orders/downloads ไม่ใช่เงิน ให้ดู royalties เป็นหลัก")
    lines.append("HTML: /files/ → downloads → libra-distribution-dashboard.html")
    return "\n".join(lines)


def _env(k: str) -> str:
    if os.getenv(k):
        return os.getenv(k, "")
    envf = LIBRA_DIR / ".env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8").splitlines():
            if line.startswith(k + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def send_telegram(message: str) -> bool:
    tok, chat = _env("TELEGRAM_BOT_TOKEN"), _env("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        print("no telegram creds")
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": message, "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=20)
        return True
    except Exception as exc:
        print(f"telegram failed: {exc}")
        return False


def render_html(report: dict) -> str:
    def e(value: Any) -> str:
        return html.escape(str(value if value is not None else ""))

    hero_rows = "\n".join(
        f"<tr><td>{e(h['title'])}<br><small>{e(h['slug'])}</small></td>"
        f"<td>{e(h['live_status'])}</td><td>{e(h['free_downloads'])}</td>"
        f"<td>{e(h['mtd_kenp'])}</td><td>${h['mtd_royalties_usd']:.2f}</td>"
        f"<td>{e((h.get('free_promo') or {}).get('start',''))} → {e((h.get('free_promo') or {}).get('end',''))}</td></tr>"
        for h in report["hero_books"]
    )
    action_items = "\n".join(
        f"<li><b>{e(a['channel'])}</b>: {e(a['action'])}</li>"
        for a in report["today_actions"]
    )
    rules = "\n".join(f"<li>{e(r)}</li>" for r in report["rules"])
    pinterest = report.get("manual_progress", {}).get("pinterest", {})
    completed = ", ".join(pinterest.get("completed_slugs", [])) or "-"
    remaining = ", ".join(pinterest.get("remaining_slugs", [])) or "-"
    return f"""<!doctype html>
<html lang="th">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Libra Distribution Dashboard</title>
  <style>
    body {{ margin:0; font-family: Arial, sans-serif; background:#0f172a; color:#e5e7eb; }}
    main {{ max-width:1120px; margin:0 auto; padding:28px 18px 48px; }}
    h1 {{ margin:0 0 6px; color:#fbbf24; }}
    .muted {{ color:#94a3b8; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; margin:18px 0; }}
    .card {{ background:#111827; border:1px solid #243044; border-radius:8px; padding:14px; }}
    .num {{ font-size:28px; font-weight:700; margin-top:8px; }}
    table {{ width:100%; border-collapse:collapse; background:#111827; border-radius:8px; overflow:hidden; }}
    th, td {{ padding:10px; border-bottom:1px solid #243044; text-align:left; vertical-align:top; }}
    th {{ color:#fbbf24; font-size:13px; }}
    small {{ color:#94a3b8; }}
    a {{ color:#38bdf8; }}
  </style>
</head>
<body>
<main>
  <h1>Libra Distribution Dashboard</h1>
  <div class="muted">Generated {e(report['generated_at'])} · Checkpoint {e(report['checkpoint']['date'])} ({e(report['checkpoint']['days_left'])} วัน)</div>
  <section class="grid">
    <div class="card"><div class="muted">เงินจริง MTD</div><div class="num">${report['money']['mtd_royalties_usd']:.2f}</div><small>Source: KDP royalties</small></div>
    <div class="card"><div class="muted">Orders/Downloads MTD</div><div class="num">{e(report['money']['mtd_orders_all_types'])}</div><small>รวมโหลดฟรี ไม่ใช่เงิน</small></div>
    <div class="card"><div class="muted">Free Downloads Hero</div><div class="num">{e(report['free_promos']['total_downloads_in_promo_windows'])}</div><small>นับเฉพาะช่วงโปรโมชัน</small></div>
    <div class="card"><div class="muted">LovelyBooks</div><div class="num">{e(report['lovelybooks']['status'])}</div><small>{e(report['lovelybooks']['next_action'])}</small></div>
    <div class="card"><div class="muted">Pinterest Progress</div><div class="num">{e(pinterest.get('completed_count', 0))} done</div><small>remaining {e(pinterest.get('remaining_count', 0))}</small></div>
  </section>
  <section class="card">
    <h2>งานวันนี้</h2>
    <ul>{action_items}</ul>
  </section>
  <section class="card" style="margin:18px 0">
    <h2>Manual Progress</h2>
    <p><b>Pinterest completed</b>: {e(completed)}</p>
    <p><b>Pinterest remaining</b>: {e(remaining)}</p>
    <p><small>{e(pinterest.get('note', ''))}</small></p>
  </section>
  <h2>Hero Books</h2>
  <table>
    <thead><tr><th>Book</th><th>Status</th><th>Free</th><th>KENP MTD</th><th>เงินจริง MTD</th><th>Promo</th></tr></thead>
    <tbody>{hero_rows}</tbody>
  </table>
  <section class="card" style="margin-top:18px">
    <h2>กฎตัดสิน</h2>
    <ul>{rules}</ul>
  </section>
</main>
</body>
</html>
"""


def render_monitor_html(monitor: dict) -> str:
    def e(value: Any) -> str:
        return html.escape(str(value if value is not None else ""))

    status = monitor["overall"]["status"]
    blockers = monitor.get("blockers", {}).get("items", [])
    blocker_items = "\n".join(f"<li>{e(item)}</li>" for item in blockers) or "<li>ไม่มี blocker ที่หยุดแผนฮีโร่ตอนนี้</li>"
    actions = "\n".join(
        f"<li><b>{e(a.get('channel', ''))}</b>: {e(a.get('action', ''))}</li>"
        for a in monitor.get("today_actions", [])
    ) or "<li>ดูตัวเลขและรอรอบแจกฟรีถัดไป</li>"
    upcoming = "\n".join(
        f"<tr><td>{e(p.get('slug', ''))}</td><td>{e(p.get('start', ''))}</td><td>{e(p.get('end', ''))}</td></tr>"
        for p in monitor.get("promo", {}).get("upcoming", [])
    ) or "<tr><td colspan='3'>ยังไม่มีโปรถัดไป</td></tr>"
    remaining = ", ".join(monitor.get("manual", {}).get("pinterest", {}).get("remaining", [])) or "-"
    avp = monitor.get("actual_vs_plan", {})
    bars = "\n".join(
        f"""<div class="bar-row">
          <div class="bar-head"><b>{e(m.get('name'))}</b><span>{e(m.get('actual_label'))} / {e(m.get('plan_label'))}</span></div>
          <div class="bar-track"><div class="bar-fill {e(m.get('status'))}" style="width:{e(m.get('percent', 0))}%"></div></div>
          <div class="bar-foot"><span>{e(m.get('percent', 0))}%</span><span>{e(m.get('status'))}</span></div>
        </div>"""
        for m in avp.get("metrics", [])
    )
    role_cards = "\n".join(
        f"""<div class="role-card">
          <div class="role-top"><b>{e(role)}</b><span class="status {e(data.get('status'))}">{e(data.get('status'))}</span></div>
          <p>{e(data.get('verdict'))}</p>
          <small>{e(data.get('target'))}</small>
        </div>"""
        for role, data in avp.get("roles", {}).items()
    )
    agent_actions = "\n".join(
        f"<li>{e(item)}</li>" for item in monitor.get("kdp_agent", {}).get("next_actions", [])
    ) or "<li>รอข้อมูลรอบถัดไป</li>"
    return f"""<!doctype html>
<html lang="th">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Libra Monitor</title>
  <style>
    :root {{
      --bg:#0b1020; --panel:#121826; --panel2:#172033; --line:#2a3548;
      --text:#e8edf7; --muted:#98a6ba; --good:#23c483; --warn:#f4b740; --bad:#ee5b5b;
      --ink:#071018;
    }}
    body {{ margin:0; background:var(--bg); color:var(--text); font-family:Inter, Arial, sans-serif; }}
    main {{ max-width:1180px; margin:0 auto; padding:24px 16px 44px; }}
    header {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:18px; }}
    h1 {{ margin:0; font-size:30px; line-height:1.1; }}
    h2 {{ margin:0 0 12px; font-size:17px; }}
    .muted {{ color:var(--muted); }}
    .pill {{ display:inline-flex; align-items:center; min-height:30px; padding:0 12px; border-radius:999px; font-weight:700; color:var(--ink); background:var(--warn); }}
    .pill.on_track {{ background:var(--good); }}
    .pill.blocked {{ background:var(--bad); color:white; }}
    .score {{ font-size:46px; font-weight:800; letter-spacing:0; }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
    .grid.two {{ grid-template-columns:1.1fr .9fr; margin-top:12px; }}
    .card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; }}
    .metric {{ font-size:26px; font-weight:800; margin-top:8px; }}
    .label {{ color:var(--muted); font-size:13px; }}
    .stack {{ display:grid; gap:10px; }}
    .bar-list {{ display:grid; gap:12px; }}
    .bar-row {{ background:#0d1424; border:1px solid var(--line); border-radius:8px; padding:10px; }}
    .bar-head, .bar-foot, .role-top {{ display:flex; justify-content:space-between; gap:12px; align-items:center; }}
    .bar-head span, .bar-foot {{ color:var(--muted); font-size:13px; }}
    .bar-track {{ height:12px; background:#253044; border-radius:999px; overflow:hidden; margin:8px 0; }}
    .bar-fill {{ height:100%; background:var(--good); border-radius:999px; }}
    .bar-fill.behind, .bar-fill.blocked {{ background:var(--bad); }}
    .bar-fill.early, .bar-fill.watch {{ background:var(--warn); }}
    .role-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; }}
    .role-card {{ background:#0d1424; border:1px solid var(--line); border-radius:8px; padding:12px; }}
    .role-card p {{ margin:10px 0 8px; }}
    .status {{ color:var(--ink); background:var(--warn); border-radius:999px; padding:3px 8px; font-size:12px; font-weight:700; }}
    .status.on_plan {{ background:var(--good); }}
    .status.behind, .status.blocked {{ background:var(--bad); color:white; }}
    table {{ width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
    th, td {{ padding:10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
    th {{ color:#c9d8ef; font-size:12px; text-transform:uppercase; }}
    ul {{ margin:0; padding-left:18px; }}
    li {{ margin:6px 0; }}
    .decision {{ background:var(--panel2); border-left:4px solid var(--good); }}
    @media (max-width: 860px) {{
      header, .grid.two {{ display:block; }}
      .grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
      .role-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
      .card {{ margin-bottom:12px; }}
    }}
    @media (max-width: 540px) {{ .grid, .role-grid {{ grid-template-columns:1fr; }} h1 {{ font-size:24px; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>Libra Monitor</h1>
      <div class="muted">Generated {e(monitor.get('generated_at'))} · checkpoint {e(monitor['timeline']['checkpoint'])}</div>
    </div>
    <div>
      <span class="pill {e(status)}">{e(monitor['overall']['label'])}</span>
      <div class="score">{e(monitor['overall']['score'])}</div>
      <div class="muted">plan score</div>
    </div>
  </header>

  <section class="grid">
    <div class="card"><div class="label">เงินจริง MTD</div><div class="metric">${monitor['money']['royalties']:.2f}</div><div class="muted">KDP royalties เท่านั้น</div></div>
    <div class="card"><div class="label">Orders/downloads</div><div class="metric">{e(monitor['money']['orders'])}</div><div class="muted">รวมโหลดฟรี</div></div>
    <div class="card"><div class="label">KENP</div><div class="metric">{e(monitor['money']['kenp'])}</div><div class="muted">หน้าอ่าน Kindle Unlimited</div></div>
    <div class="card"><div class="label">เหลือถึง checkpoint</div><div class="metric">{e(monitor['timeline']['days_left'])} วัน</div><div class="muted">{e(monitor['timeline']['label'])}</div></div>
  </section>

  <section class="grid">
    <div class="card"><div class="label">Hero ebooks LIVE</div><div class="metric">{e(monitor['setup']['hero_live'])}</div></div>
    <div class="card"><div class="label">KDP Select</div><div class="metric">{e(monitor['setup']['hero_select'])}</div></div>
    <div class="card"><div class="label">A+ submitted</div><div class="metric">{e(monitor['setup']['hero_aplus'])}</div></div>
    <div class="card"><div class="label">Pinterest</div><div class="metric">{e(monitor['manual']['pinterest']['label'])}</div><div class="muted">เหลือ: {e(remaining)}</div></div>
  </section>

  <section class="grid two">
    <div class="card">
      <h2>งานวันนี้</h2>
      <ul>{actions}</ul>
    </div>
    <div class="card decision">
      <h2>คำแนะนำตอนนี้</h2>
      <p>{e(monitor['decision']['recommendation'])}</p>
      <p class="muted">ถ้ายังไม่มี proof จากโหลด/รีวิว/KU/paid sale ให้รอข้อมูลถึง checkpoint ก่อนเพิ่มงบ</p>
    </div>
  </section>

  <section class="grid two">
    <div class="card">
      <h2>Actual vs Plan</h2>
      <div class="bar-list">{bars}</div>
    </div>
    <div class="card">
      <h2>KDP Auto Manager Agent</h2>
      <p><b>{e(monitor.get('kdp_agent', {}).get('name', 'Libra KDP Auto Manager'))}</b> · {e(monitor.get('kdp_agent', {}).get('mode', 'auto_advisor'))}</p>
      <p class="muted">{e(monitor.get('kdp_agent', {}).get('authority', ''))}</p>
      <ul>{agent_actions}</ul>
    </div>
  </section>

  <section class="card" style="margin-top:12px">
    <h2>CFO / COO / CMO / KDP Strategist</h2>
    <div class="role-grid">{role_cards}</div>
  </section>

  <section class="grid two">
    <div class="card">
      <h2>Blockers</h2>
      <ul>{blocker_items}</ul>
    </div>
    <div class="card">
      <h2>System Health</h2>
      <div class="stack">
        <div>KDP queue: <b>{e(monitor['health']['kdp_queue'])}</b></div>
        <div>Category: <b>{e(monitor['health']['category'])}</b> · warnings {e(monitor['health'].get('category_warnings', 0))}</div>
        <div>Sales sync: <b>{e(monitor['health']['sales_sync'])}</b></div>
      </div>
    </div>
  </section>

  <h2 style="margin-top:18px">Promo Calendar</h2>
  <table>
    <thead><tr><th>Book</th><th>Start</th><th>End</th></tr></thead>
    <tbody>{upcoming}</tbody>
  </table>
</main>
</body>
</html>
"""


def write_outputs(report: dict) -> None:
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    DOWNLOADS_HTML.parent.mkdir(parents=True, exist_ok=True)
    DOWNLOADS_HTML.write_text(render_html(report), encoding="utf-8")


def write_chrome_guide(report: dict) -> None:
    CHROME_GUIDE.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Claude for Chrome — KDP Distribution Posting Guide",
        "",
        "ใช้ไฟล์พินในโฟลเดอร์นี้กับ Pinterest ที่ล็อกอินจริงในเครื่องบุ๋ย",
        "",
        "## คำสั่งให้วางใน Claude for Chrome",
        "",
        "ช่วยโพสต์พินจากโฟลเดอร์ kdp-pins โดยทำทีละ 2-3 พินต่อวัน ใช้ caption จาก CAPTIONS.txt และใส่ลิงก์ Amazon ให้ตรงกับหนังสือ ห้ามใช้ไฟล์จาก pinterest-batch2 เพราะเป็น Etsy",
        "",
        "## ลำดับความสำคัญ",
    ]
    for hero in report["hero_books"]:
        promo = hero.get("free_promo") or {}
        if promo:
            lines.append(f"- {hero['slug']}: {promo.get('start')}→{promo.get('end')} | {hero.get('amazon_url','')}")
    lines.extend([
        "",
        "## LovelyBooks",
        "วันที่ 25-26 ก.ค. ให้ตอบใน Leserunde ว่า ebook โหลดฟรีจาก Amazon ได้วันนี้ ห้ามส่งไฟล์ PDF/EPUB เอง",
    ])
    CHROME_GUIDE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(send: bool = False) -> dict:
    report = build_report()
    write_outputs(report)
    write_chrome_guide(report)
    if send:
        send_telegram(telegram_message(report))
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true", help="send Telegram daily report")
    args = parser.parse_args()
    result = main(send=args.send)
    print(f"wrote {REPORT_JSON}")
    print(f"wrote {DOWNLOADS_HTML}")
    print(f"lovelybooks={result['lovelybooks']['status']} royalties=${result['money']['mtd_royalties_usd']:.2f}")
