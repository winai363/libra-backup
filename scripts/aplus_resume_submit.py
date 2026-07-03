#!/usr/bin/env python3
"""
aplus_resume_submit.py — Finish/inspect a pending A+ draft from the hub.

Opens the marketplace A+ hub, lists all content rows (name + status),
optionally opens the named draft and submits it (with modal confirm).

Usage:
  python3 scripts/aplus_resume_submit.py <slug> --list
  python3 scripts/aplus_resume_submit.py <slug> --submit
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/root/libra")
from aplus_upload import (SESSION, SESSION_APLUS, KDP, marketplace_login,
                          shot, _is_signin)
from playwright.async_api import async_playwright


async def run(slug: str, do_submit: bool):
    content = json.loads((KDP / slug / "aplus" / "content.json").read_text())
    name = content["content_name"]
    domain = content.get("marketplace", "amazon.com")
    hub_url = f"https://kdp.{domain}/aplus/content-manager"

    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True,
                                    args=["--disable-blink-features=AutomationControlled"])
        state = str(SESSION_APLUS if SESSION_APLUS.exists() else SESSION)
        c = await b.new_context(storage_state=state,
                                viewport={"width": 1500, "height": 1000})
        pg = await c.new_page()
        try:
            await pg.goto(hub_url, timeout=60000)
            await pg.wait_for_timeout(4000)
            if _is_signin(pg.url):
                if not await marketplace_login(pg, slug):
                    return 1
                await pg.goto(hub_url, timeout=60000)
                await pg.wait_for_timeout(4000)
                await c.storage_state(path=str(SESSION_APLUS))
            await shot(pg, slug, "resume_hub")
            rows = pg.locator("table tr")
            n = await rows.count()
            print(f"hub rows: {n}")
            for i in range(n):
                t = (await rows.nth(i).inner_text()).replace("\n", " | ")
                if t.strip():
                    print(f"  row {i}: {t[:160]}")
            if not do_submit:
                return 0
            link = pg.get_by_text(name, exact=False).first
            await link.click()
            await pg.wait_for_timeout(4000)
            await shot(pg, slug, "resume_opened")
            # walk forward to submit step
            for label in ("Next: Apply ASINs", "Next: Review & Submit"):
                btn = pg.get_by_role("button", name=label, exact=False)
                if await btn.count():
                    await btn.first.click()
                    await pg.wait_for_timeout(4000)
            await shot(pg, slug, "resume_review")
            sub = pg.get_by_role("button", name="Submit for approval",
                                 exact=False)
            await sub.first.click()
            await pg.wait_for_timeout(2500)
            await shot(pg, slug, "resume_confirm_modal")
            if await sub.count() > 1:
                await sub.last.click()
            await pg.wait_for_timeout(5000)
            await shot(pg, slug, "resume_submitted")
            body = await pg.locator("body").inner_text()
            print("SUBMITTED" if "ubmitted" in body or
                  "Submit for approval" not in body else "UNKNOWN — check shot")
            return 0
        finally:
            await b.close()


if __name__ == "__main__":
    slug = sys.argv[1]
    sys.exit(asyncio.run(run(slug, "--submit" in sys.argv)))
