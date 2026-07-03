#!/usr/bin/env python3
"""
free_promo_auto.py — Auto-schedule KDP Select Free Book Promotions.

Policy (approved 2026-07-03):
  A book gets ONE 3-day free promo per Select term when ALL hold:
    • live_status == LIVE and kdp_select.status == Enrolled
    • live ≥ MIN_AGE_DAYS (7) — give organic a week first
    • no recorded sales/KENP (feedback-history.json absent or all zero)
    • no free_promo already stamped for this term
    • promo would end ≥ TERM_MARGIN days before the Select term ends
  Staggered: at most PER_RUN books per run (daily cron) so the catalog
  is not free all at once. Promo = start tomorrow (PST), 3 days.

Flow per book mirrors kdp_enroll_v2.py: targeted promotion-manager URL,
title-substring safety, screenshots at every stage, stamp listing.json
only after the page confirms the promotion exists.

Usage:
  python3 scripts/free_promo_auto.py --list              # show candidates
  python3 scripts/free_promo_auto.py --dry-run           # fill form, don't save
  python3 scripts/free_promo_auto.py                     # schedule (≤3 books)
  python3 scripts/free_promo_auto.py --only slug1,slug2
"""
import asyncio
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from playwright.async_api import async_playwright

KDP = Path("/root/kdp")
SESSION = "/root/libra/kdp_session.json"
SHOTS = Path("/root/libra/logs/promo-shots")

MIN_AGE_DAYS = 7
PROMO_DAYS = 3          # start .. start+2
PER_RUN = 3
TERM_MARGIN = 5


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
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=data,
            timeout=20)
    except Exception as e:
        print("telegram failed:", e)


def has_sales(slug: str) -> bool:
    f = KDP / slug / "feedback-history.json"
    if not f.exists():
        return False
    try:
        rows = json.loads(f.read_text())
    except Exception:
        return False
    return any((r.get("mtd_orders") or 0) > 0 or (r.get("mtd_kenp") or 0) > 0
               or (r.get("units_7d") or 0) > 0 or (r.get("kenp_7d") or 0) > 0
               for r in rows)


def live_age_days(d: dict) -> int:
    ts = (d.get("publish_submission_confirmed_at")
          or d.get("uploaded_at") or d.get("created_at") or "")
    try:
        return (date.today() - date.fromisoformat(ts[:10])).days
    except Exception:
        return 999   # no timestamp = old catalog book


def candidates(only=None):
    out = []
    for lf in sorted(KDP.glob("*/listing.json")):
        slug = lf.parent.name
        if only is not None and slug not in only:
            continue
        try:
            d = json.loads(lf.read_text())
        except Exception:
            continue
        sel = d.get("kdp_select") or {}
        if d.get("live_status") != "LIVE" or sel.get("status") != "Enrolled":
            continue
        if d.get("free_promo"):
            continue
        if not d.get("kdp_book_id"):
            continue
        if live_age_days(d) < MIN_AGE_DAYS:
            continue
        if has_sales(slug):
            continue
        try:
            term_end = date.fromisoformat(sel.get("term_end", "2099-01-01"))
        except Exception:
            term_end = date(2099, 1, 1)
        promo_end = date.today() + timedelta(days=PROMO_DAYS)
        if promo_end + timedelta(days=TERM_MARGIN) > term_end:
            continue
        title = d.get("actual_live_title") or d.get("title", "")
        out.append((slug, d["kdp_book_id"], title))
    return out


async def shot(pg, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    p = SHOTS / f"{name}.png"
    try:
        await pg.screenshot(path=str(p), full_page=True)
    except Exception:
        await pg.screenshot(path=str(p))
    print(f"  shot: {p.name}", flush=True)


async def schedule_one(slug: str, bid: str, title: str, dry_run: bool) -> bool:
    start = date.today() + timedelta(days=1)
    end = start + timedelta(days=PROMO_DAYS - 1)
    async with async_playwright() as p:
        b = await p.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"])
        c = await b.new_context(storage_state=SESSION,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
        pg = await c.new_page()
        await pg.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>false})")
        try:
            await pg.goto("https://kdp.amazon.com/action/bookshelf.kindlepromotions/"
                          f"title-setup/{bid}/promotion-manager",
                          wait_until="domcontentloaded", timeout=60000)
            await pg.wait_for_timeout(6000)
            from urllib.parse import urlsplit
            if urlsplit(pg.url).path.startswith("/ap/"):
                print("SESSION_EXPIRED")
                return False
            body = await pg.evaluate("()=>document.body.innerText")
            # safety: right book?
            sub = title[:25]
            if sub.lower() not in body.lower():
                await shot(pg, f"{slug}_abort_title")
                print(f"  ABORT: title substring not on page")
                return False
            if "Free Book Promotion" not in body:
                await shot(pg, f"{slug}_abort_no_fbp")
                print("  ABORT: no Free Book Promotion section (not enrolled?)")
                return False
            await shot(pg, f"{slug}_1_manager")
            # open the Free Book Promotion creation form
            create = pg.get_by_role("button",
                                    name="Create a new Free Book Promotion",
                                    exact=False)
            if not await create.count():
                create = pg.get_by_text("Create a new Free Book Promotion",
                                        exact=False)
            if not await create.count():
                # some layouts: section card with its own "Create" button near
                # the "Free Book Promotion" heading
                create = pg.locator(
                    'button:near(:text("Free Book Promotion"))').filter(
                    has_text="Create")
            await create.first.click()
            await pg.wait_for_timeout(4000)
            await shot(pg, f"{slug}_2_form")
            # date fields (MM/DD/YYYY, US locale)
            fmt = "{:%m/%d/%Y}"
            dates = pg.locator('input[placeholder*="MM" i], '
                               'input[name*="start" i], input[name*="date" i]')
            n = await dates.count()
            if n < 2:
                dates = pg.locator('input[type="text"]:visible')
                n = await dates.count()
            if n < 2:
                await shot(pg, f"{slug}_abort_no_dates")
                print("  ABORT: date inputs not found")
                return False
            await dates.nth(0).fill(fmt.format(start))
            await dates.nth(1).fill(fmt.format(end))
            await pg.keyboard.press("Escape")   # close any datepicker overlay
            await pg.wait_for_timeout(1000)
            await shot(pg, f"{slug}_3_dates")
            if dry_run:
                print(f"  DRY-RUN: would schedule {start} → {end}")
                return True
            save = None
            for label in ("Save changes", "Save", "Schedule", "Submit",
                          "Start promotion", "Create promotion"):
                cand = pg.get_by_role("button", name=label, exact=False)
                for k in range(await cand.count()):
                    el = cand.nth(k)
                    if await el.is_visible() and await el.is_enabled():
                        save = el
                        break
                if save:
                    break
            if not save:
                await shot(pg, f"{slug}_abort_no_save")
                print("  ABORT: save button not found")
                return False
            await save.click()
            await pg.wait_for_timeout(6000)
            await shot(pg, f"{slug}_4_saved")
            final = await pg.evaluate("()=>document.body.innerText")
            ok = ("scheduled" in final.lower() or "promotion" in final.lower()
                  and fmt.format(start).split("/")[0] in final)
            # strongest signal: the manager page now lists a promotion row
            listed = ("Free Book Promotion" in final and
                      ("Scheduled" in final or "In progress" in final))
            if not listed:
                print("  WARN: could not confirm 'Scheduled' on page")
                return False
            lf = KDP / slug / "listing.json"
            d = json.loads(lf.read_text())
            d["free_promo"] = {
                "status": "Scheduled",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "source": f"free_promo_auto {date.today().isoformat()}",
            }
            lf.write_text(json.dumps(d, ensure_ascii=False, indent=2))
            print(f"  SCHEDULED {start} → {end}")
            return True
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
            try:
                await shot(pg, f"{slug}_error")
            except Exception:
                pass
            return False
        finally:
            await b.close()


def main():
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    todo = candidates(only)
    if "--list" in sys.argv:
        print(f"{len(todo)} candidate(s):")
        for slug, bid, title in todo:
            print(f"  {slug}  ({title[:50]})")
        return
    dry = "--dry-run" in sys.argv
    todo = todo[:PER_RUN]
    if not todo:
        print("no candidates today")
        return
    print(f"scheduling {len(todo)} promo(s), dry_run={dry}\n", flush=True)
    ok = []
    for slug, bid, title in todo:
        print(f"• {slug}", flush=True)
        if asyncio.run(schedule_one(slug, bid, title, dry)):
            ok.append(slug)
    print(f"\nDONE: {len(ok)}/{len(todo)}")
    if ok and not dry:
        start = (date.today() + timedelta(days=1)).isoformat()
        tg("🎁 Libra Free Promo ตั้งอัตโนมัติ " + str(len(ok)) + " เล่ม "
           f"(เริ่ม {start}, 3 วัน):\n" + "\n".join("• " + s for s in ok))


if __name__ == "__main__":
    main()
