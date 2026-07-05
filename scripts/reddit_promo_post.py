#!/usr/bin/env python3
"""reddit_promo_post.py — on each scheduled free-promo day, send บุ๋ย a rich
Telegram reminder with a copy-paste-ready r/FreeEBOOKS post + step-by-step
instructions. (Reddit blocks this server's IP for API/login, so posting is
manual by design — this script does the remembering, the checking, and the
hand-holding.)

Safety gates before reminding:
  1. today matches a schedule entry (data/reddit_promo_schedule.json)
  2. the book's KDP free_promo window covers today (r/FreeEBOOKS requires the
     book to actually be free at post time) — if not, warn instead of remind
  3. idempotent via reminded_at stamp

Cron: daily 20:00 Asia/Bangkok.
"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kdp_upload import ENV, KDP_DIR, logger  # noqa: E402

SCHEDULE = Path(__file__).resolve().parent.parent / "data" / "reddit_promo_schedule.json"


def tg(msg: str) -> bool:
    import httpx
    tok, chat = ENV.get("TELEGRAM_BOT_TOKEN"), ENV.get("TELEGRAM_CHAT_ID")
    if not tok:
        logger.warning("no telegram token")
        return False
    try:
        r = httpx.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                       json={"chat_id": chat, "text": msg,
                             "disable_web_page_preview": True}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"telegram: {e}")
        return False


def promo_active(slug: str, today: date):
    try:
        l = json.loads((KDP_DIR / slug / "listing.json").read_text())
        fp = l.get("free_promo") or {}
        start, end = str(fp.get("start", ""))[:10], str(fp.get("end", ""))[:10]
        return (start and end and start <= today.isoformat() <= end), (start, end)
    except Exception as e:
        logger.warning(f"promo check {slug}: {e}")
        return False, ("", "")


def build_reminder(entry: dict, window) -> str:
    return (
        "📣 ถึงเวลาโพสต์แจกหนังสือฟรีลง Reddit แล้ว!\n"
        f"(หนังสือฟรีจริงช่วง {window[0]} → {window[1]} — เช็คแล้ว ✅)\n\n"
        "━━━ วิธีทำ (1 นาที) ━━━\n"
        "1) เปิดแอป Reddit บนมือถือ (ล็อกอิน WK_Bui_Books)\n"
        "2) ไปที่กลุ่ม r/FreeEBOOKS → กดปุ่ม + (Create Post)\n"
        "3) ก๊อป 'หัวข้อ' ด้านล่างวางในช่อง Title\n"
        "4) ก๊อป 'เนื้อหา' วางในช่อง Body\n"
        "5) เลือก Flair = \"Kindle\" (ถ้ามีให้เลือก)\n"
        "6) กด Post — เสร็จ!\n\n"
        "━━━ 📋 หัวข้อ (Title) ━━━\n"
        f"{entry['title']}\n\n"
        "━━━ 📋 เนื้อหา (Body) ━━━\n"
        f"{entry['body']}\n\n"
        "⚠️ ถ้ากดลิงก์แล้วหนังสือ 'ไม่ฟรี' อย่าโพสต์ (กลุ่มจะลบ) — แจ้งผมได้เลย"
    )


def main():
    cfg = json.loads(SCHEDULE.read_text())
    today = date.today()
    entry = next((p for p in cfg["posts"] if p["date"] == today.isoformat()), None)
    if not entry:
        logger.info("no reddit reminder scheduled today")
        return
    if entry.get("reminded_at"):
        logger.info("already reminded today — skip")
        return

    active, window = promo_active(entry["slug"], today)
    if not active:
        tg(f"⚠️ วันนี้มีคิวโพสต์ Reddit ({entry['slug']}) แต่โปรแจกฟรีของ KDP "
           f"ยังไม่ active (ช่วง {window[0]}→{window[1]}) — ยังไม่ต้องโพสต์ "
           f"เดี๋ยวผมเตือนใหม่เมื่อฟรีจริง")
        logger.warning(f"promo not active for {entry['slug']} — soft warn sent")
        return

    ok = tg(build_reminder(entry, window))
    if ok:
        from datetime import datetime
        entry["reminded_at"] = datetime.now().isoformat(timespec="seconds")
        SCHEDULE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
        logger.info(f"reminder sent for {entry['slug']}")
    else:
        logger.error("telegram reminder failed — will retry next cron run")


if __name__ == "__main__":
    main()
