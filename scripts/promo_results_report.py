#!/usr/bin/env python3
"""
promo_results_report.py — Report free-promo download results to Telegram.

For every book with a free_promo stamp:
  • while the promo runs (start..end): daily line "โหลดแล้ว N ครั้ง"
  • the day after it ends (+1 grace day for report lag): final summary,
    stamped into listing.json free_promo.result so it reports once.

Free downloads = sum of feedback-history delta_units on promo dates
(price is $0 during the promo, so every unit in the window is a free
download — revenue stays 0).

Runs after kdp_sales_sync (cron 09:15) — cron 09:40 daily.
"""
import json
import os
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

KDP = Path("/root/kdp")


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
        print("no telegram creds")
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=data,
            timeout=20)
    except Exception as e:
        print("telegram failed:", e)


def promo_units(slug: str, start: date, end: date) -> int:
    f = KDP / slug / "feedback-history.json"
    if not f.exists():
        return 0
    try:
        rows = json.loads(f.read_text())
    except Exception:
        return 0
    total = 0
    for r in rows:
        try:
            d = date.fromisoformat(r.get("date", ""))
        except Exception:
            continue
        # +1 day: KDP dashboard numbers can land a day late
        if start <= d <= end + timedelta(days=1):
            total += max(0, r.get("delta_units") or 0)
    return total


def main():
    today = date.today()
    running, finals = [], []
    for lf in sorted(KDP.glob("*/listing.json")):
        d = json.loads(lf.read_text())
        # free_promo = our scheduler's stamp; promotion = manually-set promos
        key = "free_promo" if d.get("free_promo") else "promotion"
        fp = d.get(key)
        if not fp or fp.get("status") not in ("Scheduled", "Running", "Done"):
            continue
        try:
            # dates may be plain (2026-07-04) or full ISO with tz offset
            start = date.fromisoformat(str(fp["start"])[:10])
            end = date.fromisoformat(str(fp["end"])[:10])
        except Exception:
            continue
        slug = lf.parent.name
        title = (d.get("actual_live_title") or d.get("title", slug))[:45]
        n = promo_units(slug, start, end)
        if start <= today <= end:
            day_no = (today - start).days + 1
            days = (end - start).days + 1
            running.append(f"• {title} — วันที่ {day_no}/{days}: "
                           f"โหลดแล้ว {n} ครั้ง")
            if fp.get("status") == "Scheduled":
                fp["status"] = "Running"
                d[key] = fp
                lf.write_text(json.dumps(d, ensure_ascii=False, indent=2))
        elif today > end and not fp.get("result"):
            # grace day so the last day's numbers arrive before the summary
            if today < end + timedelta(days=2):
                continue
            fp["status"] = "Done"
            fp["result"] = {"free_downloads": n,
                            "reported": today.isoformat()}
            d[key] = fp
            lf.write_text(json.dumps(d, ensure_ascii=False, indent=2))
            finals.append(f"• {title} ({fp['start']}→{fp['end']}): "
                          f"โหลดฟรีรวม {n} ครั้ง")
    if running:
        tg("🎁 Libra Free Promo วันนี้:\n" + "\n".join(running))
    if finals:
        tg("🏁 Libra Free Promo จบแล้ว — ผลรวม:\n" + "\n".join(finals) +
           "\nดูต่อ: อันดับ/ยอดขายหลังโปร 3-7 วันข้างหน้า")
    print(f"running={len(running)} finals={len(finals)}")
    for line in running + finals:
        print(line)


if __name__ == "__main__":
    main()
