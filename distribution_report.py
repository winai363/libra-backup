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
LOVELY_DIR = LIBRA_DIR.parent / "lovelybooks"
REPORT_JSON = LIBRA_DIR / "data" / "distribution-report.json"
DOWNLOADS_HTML = LIBRA_DIR.parent / "downloads" / "libra-distribution-dashboard.html"
CHROME_GUIDE = LIBRA_DIR.parent / "downloads" / "kdp-pins" / "CLAUDE-CHROME-POSTING-GUIDE.md"


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


def telegram_message(report: dict) -> str:
    money = report["money"]
    lines = [
        "📚 Libra Distribution Daily",
        f"เงินจริง MTD: ${money['mtd_royalties_usd']:.2f} | orders/downloads: {money['mtd_orders_all_types']} orders/downloads",
        f"Checkpoint: {report['checkpoint']['date']} ({report['checkpoint']['days_left']} วัน)",
        f"LovelyBooks: {report['lovelybooks']['status']}",
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
  </section>
  <section class="card">
    <h2>งานวันนี้</h2>
    <ul>{action_items}</ul>
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
