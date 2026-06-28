#!/usr/bin/env python3
"""
ADHD Spanish series (experiment B) — daily Telegram progress digest.

Runs after kdp_sales_sync (09:15) and reads each book's feedback-history.json
(mtd_orders / mtd_kenp / mtd_royalties / delta_units / delta_kenp) + roster live
status. Sends a Telegram update when there is news (new KU page-reads, new sales,
status change, or during the Free Promo window) and a weekly heartbeat otherwise
— so Bui is kept informed without 0-activity spam.
"""
import os, json
from pathlib import Path
from datetime import date, datetime
import urllib.request, urllib.parse

KDP = Path("/root/kdp")
ROSTER = KDP / "bookshelf-roster.json"
STATE = KDP / ".adhd-series-watch-state.json"
BOOKS = {
    "adhd-self-help-adults-es": "Guía (lead)",
    "adhd-adults-workbook-es": "Cuaderno",
    "adhd-adults-focus-work-relationships-es": "Enfoque",
}
FREE_PROMO = (date(2026, 7, 2), date(2026, 7, 6))
PROMO_SOURCE = "KDP promotion-manager verified 2026-06-28"


def _env(k):
    v = os.getenv(k)
    if v:
        return v
    envf = Path("/root/libra/.env")
    if envf.exists():
        for line in envf.read_text().splitlines():
            if line.startswith(k + "="):
                return line.split("=", 1)[1].strip()
    return None


def tg(msg):
    tok, chat = _env("TELEGRAM_BOT_TOKEN"), _env("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        print("no telegram creds"); return
    data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=20)
        print("telegram sent")
    except Exception as e:
        print("telegram failed:", e)


def latest_snap(slug):
    f = KDP / slug / "feedback-history.json"
    if not f.exists():
        return {}
    try:
        hist = json.loads(f.read_text())
        return hist[-1] if isinstance(hist, list) and hist else {}
    except Exception:
        return {}


def roster_status():
    if not ROSTER.exists():
        return {}
    try:
        r = json.loads(ROSTER.read_text())
        return {e.get("slug"): (e.get("status") or "").upper()
                for e in r.get("entries", []) if e.get("slug") in BOOKS}
    except Exception:
        return {}


def main():
    today = date.today()
    rs = roster_status()
    tot_kenp = tot_units = 0.0
    tot_roy = 0.0
    d_kenp = d_units = 0
    lines = []
    for slug, name in BOOKS.items():
        s = latest_snap(slug)
        kenp = s.get("mtd_kenp", 0) or 0
        units = s.get("mtd_orders", 0) or 0
        roy = s.get("mtd_royalties", 0) or 0
        tot_kenp += kenp; tot_units += units; tot_roy += roy
        d_kenp += s.get("delta_kenp", 0) or 0
        d_units += s.get("delta_units", 0) or 0
        st = rs.get(slug, "?")
        lines.append(f"• {name}: {int(units)} ขาย · {int(kenp)} KU หน้า · ${roy:.2f}" +
                     ("" if st == "LIVE" else f" [{st}]"))

    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    seen_kenp = state.get("seen_kenp", 0)
    seen_units = state.get("seen_units", 0)
    last_sent = state.get("last_sent_date", "")
    last_status = state.get("last_status", {})

    in_promo = FREE_PROMO[0] <= today <= FREE_PROMO[1]
    milestones = []
    if tot_kenp > 0 and seen_kenp == 0:
        milestones.append("🎉 KU page-read แรก! (มีคนเริ่มอ่านใน Kindle Unlimited)")
    if tot_units > 0 and seen_units == 0:
        milestones.append("🎉 ขายเล่มแรกของซีรีส์!")

    status_changes = []
    for slug, name in BOOKS.items():
        old = last_status.get(slug)
        new = rs.get(slug, "?")
        if old and old != new:
            status_changes.append(f"• {name}: {old} → {new}")

    new_activity = d_kenp > 0 or d_units > 0 or bool(milestones)
    is_monday = today.weekday() == 0
    should_send = new_activity or bool(status_changes) or in_promo or (is_monday and last_sent != today.isoformat())

    if should_send:
        hdr = "📚 TDAH en Adultos (KDP experiment B) — อัปเดต " + today.isoformat()
        promo = ""
        if in_promo:
            promo = f"\n🎁 Free Promo กำลังรัน ({FREE_PROMO[0]}→{FREE_PROMO[1]}) — ช่วงปั๊มดาวน์โหลด/rank"
        elif today < FREE_PROMO[0]:
            promo = f"\n🎁 Free Promo: ตั้งไว้ {FREE_PROMO[0]}→{FREE_PROMO[1]}"
        summary = (f"\nรวม: {int(tot_units)} ขาย · {int(tot_kenp)} KU หน้า · ${tot_roy:.2f}"
                   f"  (วันนี้ +{int(d_units)} ขาย / +{int(d_kenp)} KU)")
        body = "\n".join(lines)
        ms = ("\n\n" + "\n".join(milestones)) if milestones else ""
        st = ("\n\nสถานะเปลี่ยน:\n" + "\n".join(status_changes)) if status_changes else ""
        tail = ("\n\n💡 ตอนนี้ยังไม่ยิงแอด: ใช้ Free Promo ก่อนเพื่อลดการเบิร์นงบ "
                "แล้วค่อยประเมินหลังเห็น KU/ดาวน์โหลด/รีวิวจริง")
        tg(f"{hdr}{promo}{summary}\n{body}{ms}{st}{tail}")
        state["last_sent_date"] = today.isoformat()

    state["seen_kenp"] = max(seen_kenp, tot_kenp)
    state["seen_units"] = max(seen_units, tot_units)
    state["last_status"] = {slug: rs.get(slug, "?") for slug in BOOKS}
    state["free_promo"] = {
        "lead_slug": "adhd-self-help-adults-es",
        "start": FREE_PROMO[0].isoformat(),
        "end": FREE_PROMO[1].isoformat(),
        "source": PROMO_SOURCE,
    }
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print(f"sent={should_send} kenp={tot_kenp} units={tot_units} promo={in_promo} new={new_activity}")


if __name__ == "__main__":
    main()
