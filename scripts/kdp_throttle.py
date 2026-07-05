#!/usr/bin/env python3
"""กิ่งล้ม KDP: throttle/pause การผลิตหนังสือ (reversible ทันที).

ใช้เมื่อ ADHD free promo (2-6 ก.ค.) ไม่เกิดผล (0 KENP/0 units) = การผลิตหว่าน
ไม่คุ้ม (60+ เล่มขายได้ $2). เขียน kdp-title-limit.json ให้ทั้ง auto-generate.sh
และ process_kdp_queue.sh หยุด (มีเช็คไฟล์นี้อยู่แล้ว).

  python3 kdp_throttle.py --pause     # หยุดผลิต+อัป (ประหยัด API)
  python3 kdp_throttle.py --resume    # เปิดกลับ
  python3 kdp_throttle.py --status
"""
import argparse
import json
from pathlib import Path

LIMIT = Path("/root/libra/data/kdp-title-limit.json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pause", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    cur = {}
    if LIMIT.exists():
        try:
            cur = json.loads(LIMIT.read_text() or "{}")
        except Exception:
            cur = {}
    if a.pause:
        LIMIT.write_text(json.dumps({"active": True, "reason": "adhd-promo-fail throttle", "by": "decision_gates"}))
        print("PAUSED: kdp generation+upload หยุดแล้ว (ลบไฟล์/resume เพื่อเปิดกลับ)")
    elif a.resume:
        LIMIT.write_text(json.dumps({"active": False}))
        print("RESUMED: kdp generation เปิดกลับ")
    else:
        print("status:", "PAUSED" if cur.get("active") else "active", cur)


if __name__ == "__main__":
    main()
