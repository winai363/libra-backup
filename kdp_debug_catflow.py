#!/usr/bin/env python3
"""Probe the KDP category modal INTERACTION (does select_option drive the React
cascade?). Removes existing placements, clicks Add another category, tries to
set the Category select, and reports whether Subcategory populates. Clicks
CANCEL at the end — nothing is saved."""
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

LIBRA = Path(__file__).parent
SESSION_FILE = LIBRA / "kdp_session.json"
OUT = Path("/tmp/claude-0/-root/cf572050-ccdf-47d8-bd83-9038c1012e0f/scratchpad")


async def dump_selects(page, tag):
    info = await page.evaluate("""() => {
        const sels = [...document.querySelectorAll('select.a-native-dropdown')];
        return sels.map(s => ({
            name: s.name, disabled: s.disabled,
            value: s.value,
            optCount: s.options.length,
            sample: [...s.options].slice(0,6).map(o => o.text)
        }));
    }""")
    print(f"\n[{tag}] selects:")
    for s in info:
        print(f"  {s['name']} disabled={s['disabled']} val={s['value'][:40]!r} opts={s['optCount']} {s['sample'][:5]}")
    return info


async def main(slug):
    listing = json.loads((Path("/root/kdp") / slug / "listing.json").read_text())
    book_id = listing["kdp_book_id"]
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=str(SESSION_FILE))
        page = await context.new_page()
        await page.goto(f"https://kdp.amazon.com/en_US/title-setup/kindle/{book_id}/details",
                        wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)

        for label in ("Edit categories", "Add categories"):
            btn = await page.query_selector(f'button:has-text("{label}")')
            if btn:
                await btn.scroll_into_view_if_needed(); await btn.click()
                print("opened via", label); break
        await page.wait_for_timeout(3500)
        await dump_selects(page, "modal-open")

        # Click "Add another category" → new editable row (no Remove for this probe)
        add = page.locator('button:has-text("Add another category")')
        if await add.count():
            await add.first.click(); await page.wait_for_timeout(2500)
            print("clicked Add another category")
        else:
            print("no 'Add another category' button")
        sel_info = await dump_selects(page, "after-add")

        # Find the first ENABLED category select (level 0)
        target_name = None
        for s in sel_info:
            if not s["disabled"] and any("Business" in t or "Arts" in t for t in s["sample"]):
                target_name = s["name"]; break
        print("enabled level-0 select:", target_name)

        if target_name:
            try:
                await page.select_option(f'select[name="{target_name}"]', label="Business & Money")
                print("select_option Business & Money -> OK")
            except Exception as e:
                print("select_option by label failed:", e)
                # try by value containing the key
                opts = await page.eval_on_selector(f'select[name="{target_name}"]',
                    "s => [...s.options].map(o=>({t:o.text,v:o.value}))")
                bm = next((o for o in opts if "Business" in o["t"]), None)
                if bm:
                    await page.select_option(f'select[name="{target_name}"]', value=bm["v"])
                    print("select_option by value -> OK")
            await page.wait_for_timeout(2500)
            await dump_selects(page, "after-set-category")
            # Did a subcategory / placement appear?
            cbs = await page.evaluate("() => [...document.querySelectorAll('input[type=checkbox]')].filter(c=>c.offsetParent).length")
            print("visible checkboxes now:", cbs)

        await page.screenshot(path=str(OUT / "catflow.png"))
        # Cancel — discard everything
        cancel = await page.query_selector('button:has-text("Cancel")')
        if cancel:
            await cancel.click(); print("clicked Cancel (nothing saved)")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "easy-taxes-self-employed-spain"))
