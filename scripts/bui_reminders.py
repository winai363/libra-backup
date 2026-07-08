#!/usr/bin/env python3
"""Bui's manual-task queue for the Libra free-only July experiment.

These are the things only Bui can do (Pinterest upload, LovelyBooks community,
checkpoint decisions) — things the server can't automate. A daily cron runs this;
on a date that matches the schedule it Telegram-pings Bui with that day's task.

Add/adjust items in SCHEDULE. `--test` sends the whole list now (dry check).
"""
import os
import sys
import json
import urllib.request
from datetime import date
from pathlib import Path

ENV = Path("/root/libra/.env")


def _tg(msg: str) -> bool:
    env = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    tok = env.get("TELEGRAM_BOT_TOKEN", "")
    chat = env.get("TELEGRAM_CHAT_ID", "")
    if not tok or not chat:
        print("no telegram creds")
        return False
    data = json.dumps({"chat_id": chat, "text": msg}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{tok}/sendMessage",
        data=data, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:
        print(f"tg error: {e}")
        return False


# date (ISO) -> reminder text. Free-only July plan (no spend).
SCHEDULE = {
    "2026-07-09": (
        "📌 Libra — งานบุ๋ย (ทำเร็วสุด)\n"
        "อัพ Pinterest batch2 (10 พิน) ที่ FileBrowser → downloads → pinterest-batch2\n"
        "จัดพินให้: workbook-es (Cuaderno TDAH) โผล่ช่วง 15-19 ก.ค. · focus-es (Enfoque) ช่วง 22-26 ก.ค.\n"
        "= ตัวขยายวันแจกฟรี ถ้าไม่มีพิน ฟรีเดี่ยวได้แค่ 2-7 โหลด"
    ),
    "2026-07-14": (
        "🎁 พรุ่งนี้ workbook-es (Cuaderno TDAH) เริ่มแจกฟรี 15-19 ก.ค.\n"
        "เช็คว่าพิน Pinterest ชี้เล่มนี้ตั้งโชว์ช่วงนี้แล้ว"
    ),
    "2026-07-21": (
        "🎁 พรุ่งนี้ focus-es (Enfoque TDAH) เริ่มแจกฟรี 22-26 ก.ค.\n"
        "เช็คพิน Pinterest ชี้เล่มนี้"
    ),
    "2026-07-25": (
        "🇩🇪 วันนี้ (25-26 ก.ค.) LovelyBooks Leserunde + german ADHS-Workbook แจกฟรี\n"
        "โพสต์เตือนในรอบ Leserunde + ตอบคอมเมนต์ผู้ร่วม (ห้ามแจกไฟล์เอง = ชี้ไปโหลดฟรี Amazon เท่านั้น)"
    ),
    "2026-07-31": (
        "🔖 CHECKPOINT วันนี้ — ทบทวนผล free-only 1 เดือน\n"
        "เกณฑ์: เล่มไหน >100 โหลด + ≥1 รีวิว → พิจารณา paid stack รอบหน้า | <30 โหลด → แก้ตัวสินค้า ไม่ใช่ช่อง\n"
        "+ ตัดสินใจคำถามพอร์ต: KDP ควรได้เวลา/เงินเท่าไหร่ vs lane ที่ทำเงินจริง"
    ),
    "2026-08-03": (
        "📊 รายงานผลโปรฟรี ก.ค. — สรุปยอดโหลด/รีวิว/ขาย ต่อเล่มฮีโร่ เพื่อปิด checkpoint"
    ),
}


def main():
    if "--test" in sys.argv:
        print("SCHEDULE:")
        for d, m in sorted(SCHEDULE.items()):
            print(f"\n[{d}]\n{m}")
        if "--send" in sys.argv:
            _tg("🧪 ทดสอบระบบเตือน Libra — คิวงานบุ๋ยตั้งแล้ว (Pinterest / LovelyBooks / checkpoint)")
            print("\n(test telegram sent)")
        return
    today = date.today().isoformat()
    msg = SCHEDULE.get(today)
    if msg:
        ok = _tg(msg)
        print(f"{today}: reminder {'sent' if ok else 'FAILED'}")
    else:
        print(f"{today}: no reminder scheduled")


if __name__ == "__main__":
    main()
