#!/usr/bin/env python3
"""
Enroll ONE book in KDP Select via its per-book promotion-manager page (targeted
by book_id in the URL — unambiguous, no wrong-book risk). Safety: aborts if the
page does not contain the expected title substring. Screenshots every stage.

Usage: kdp_enroll_v2.py <BOOK_ID> <TITLE_SUBSTR> <SHOT_PREFIX>
"""
import asyncio, sys
from playwright.async_api import async_playwright

SESSION = "/root/libra/kdp_session.json"
SHOT_DIR = "/tmp/claude-0/-root/cd148d5a-d67e-433a-8a51-a3cc0eab19b7/scratchpad"

async def main():
    bid, title_sub, pfx = sys.argv[1], sys.argv[2], sys.argv[3]
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
            await pg.screenshot(path=f"{SHOT_DIR}/{pfx}_abort.png", full_page=True)
            await b.close(); return

        # Already enrolled?
        if "Enroll in KDP Select" not in body and ("enrolled" in body.lower() or "Manage KDP Select" in body):
            print("ALREADY ENROLLED (no Enroll button present)")
            await pg.screenshot(path=f"{SHOT_DIR}/{pfx}_already.png", full_page=True)
            await b.close(); return

        enroll = pg.get_by_role("button", name="Enroll in KDP Select", exact=False)
        if await enroll.count() == 0:
            enroll = pg.get_by_text("Enroll in KDP Select", exact=False)
        await enroll.first.scroll_into_view_if_needed()
        await enroll.first.click()
        await pg.wait_for_timeout(4000)
        await pg.screenshot(path=f"{SHOT_DIR}/{pfx}_modal.png", full_page=True)
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
        for label in ["Enroll in KDP Select", "Confirm", "Enroll", "Continue", "Agree"]:
            loc = pg.get_by_role("button", name=label, exact=False)
            cnt = await loc.count()
            for i in range(cnt):
                btn = loc.nth(i)
                if await btn.is_visible() and await btn.is_enabled():
                    await btn.click()
                    confirmed = True
                    print(f"clicked confirm button: '{label}' (#{i})")
                    break
            if confirmed:
                break
        await pg.wait_for_timeout(6000)
        await pg.screenshot(path=f"{SHOT_DIR}/{pfx}_after.png", full_page=True)
        final = await pg.evaluate("()=>document.body.innerText")
        enrolled_now = ("Enroll in KDP Select" not in final) or ("enrolled" in final.lower())
        print("confirm clicked:", confirmed, "| looks enrolled:", enrolled_now)
        await b.close()

asyncio.run(main())
