#!/usr/bin/env python3
"""
Enroll ONE book in KDP Select via its per-book promotion-manager page (targeted
by book_id in the URL — unambiguous, no wrong-book risk). Safety: aborts if the
page does not contain the expected title substring. Screenshots every stage.

Usage: kdp_enroll_v2.py <BOOK_ID> <TITLE_SUBSTR> <SHOT_PREFIX>
"""
import asyncio, os, sys
from pathlib import Path
from playwright.async_api import async_playwright

SESSION = "/root/libra/kdp_session.json"
SHOT_DIR = Path(os.getenv("KDP_ENROLL_SHOT_DIR", "/root/libra/logs/kdp-enroll-shots"))


def usage():
    print("Usage: kdp_enroll_v2.py <BOOK_ID> <TITLE_SUBSTR> <SHOT_PREFIX>")
    print("Example: kdp_enroll_v2.py A4PFUY0VD5I5Z 'TDAH en Adultos' lead")


async def first_enabled_button(page, labels):
    for label in labels:
        loc = page.get_by_role("button", name=label, exact=False)
        for i in range(await loc.count()):
            btn = loc.nth(i)
            if await btn.is_visible() and await btn.is_enabled():
                return label, i, btn
    return None, None, None

async def main():
    if len(sys.argv) != 4:
        usage()
        sys.exit(2)
    bid, title_sub, pfx = sys.argv[1], sys.argv[2], sys.argv[3]
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        c = await b.new_context(storage_state=SESSION,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
        pg = await c.new_page()
        await pg.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>false})")
        await pg.goto(f"https://kdp.amazon.com/action/bookshelf.kindlepromotions/title-setup/{bid}/promotion-manager",
                      wait_until="domcontentloaded", timeout=60000)
        if "signin" in pg.url or "/ap/" in pg.url:
            print("SESSION_EXPIRED"); await b.close(); return
        await pg.wait_for_timeout(6000)
        body = await pg.evaluate("()=>document.body.innerText")

        # SAFETY: confirm we're on the right book's page
        if title_sub.lower() not in body.lower():
            print(f"ABORT: title '{title_sub}' not on page — wrong book? not clicking.")
            await pg.screenshot(path=str(SHOT_DIR / f"{pfx}_abort.png"), full_page=True)
            await b.close(); return

        # Already enrolled?
        if "Enroll in KDP Select" not in body and ("enrolled" in body.lower() or "Manage KDP Select" in body):
            print("ALREADY ENROLLED (no Enroll button present)")
            await pg.screenshot(path=str(SHOT_DIR / f"{pfx}_already.png"), full_page=True)
            await b.close(); return

        _label, _idx, enroll = await first_enabled_button(pg, ["Enroll in KDP Select"])
        if not enroll:
            print("ABORT: Enroll in KDP Select button not found/enabled.")
            await pg.screenshot(path=str(SHOT_DIR / f"{pfx}_no_button.png"), full_page=True)
            await b.close(); return
        await enroll.scroll_into_view_if_needed()
        await enroll.click()
        await pg.wait_for_timeout(4000)
        await pg.screenshot(path=str(SHOT_DIR / f"{pfx}_modal.png"), full_page=True)
        mbody = await pg.evaluate("()=>document.body.innerText")
        print("after-click has '90':", "90" in mbody, "| exclusiv:", "exclusiv" in mbody.lower())

        # Confirm in the modal: tick any required checkbox, then the confirm button.
        try:
            cbs = pg.locator('input[type="checkbox"]:visible')
            for i in range(await cbs.count()):
                cb = cbs.nth(i)
                if not await cb.is_checked():
                    await cb.check()
                    print(f"checked a checkbox #{i}")
        except Exception as e:
            print("checkbox step:", e)

        confirmed = False
        label, idx, btn = await first_enabled_button(
            pg, ["Enroll in KDP Select", "Confirm", "Enroll", "Continue", "Agree"]
        )
        if btn:
            await btn.click()
            confirmed = True
            print(f"clicked confirm button: '{label}' (#{idx})")
        await pg.wait_for_timeout(6000)
        await pg.screenshot(path=str(SHOT_DIR / f"{pfx}_after.png"), full_page=True)
        final = await pg.evaluate("()=>document.body.innerText")
        enrolled_now = ("Enroll in KDP Select" not in final) or ("enrolled" in final.lower())
        print("confirm clicked:", confirmed, "| looks enrolled:", enrolled_now)
        await b.close()

asyncio.run(main())
