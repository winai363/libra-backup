#!/usr/bin/env python3
"""
kdp_debug_categories.py — Open the KDP category modal for one book and dump its
DOM + a screenshot, so we can write a correct path-navigation selector instead
of blindly clicking the first checkboxes. Read-only: never saves/publishes.

Usage: python3 kdp_debug_categories.py <slug>
"""
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

LIBRA = Path(__file__).parent
SESSION_FILE = LIBRA / "kdp_session.json"
OUT = Path("/tmp/claude-0/-root/cf572050-ccdf-47d8-bd83-9038c1012e0f/scratchpad")
OUT.mkdir(parents=True, exist_ok=True)


async def main(slug: str):
    listing = json.loads((Path("/root/kdp") / slug / "listing.json").read_text())
    book_id = listing.get("kdp_book_id")
    if not book_id:
        print("no kdp_book_id"); return
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=str(SESSION_FILE))
        page = await context.new_page()
        url = f"https://kdp.amazon.com/en_US/title-setup/kindle/{book_id}/details"
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        if "signin" in page.url or "ap/signin" in page.url:
            print("SESSION EXPIRED"); await browser.close(); return

        # Find the categories button (real label is "Add categories" / "Edit categories")
        btn = None
        for label in ("Add categories", "Edit categories", "Change categories", "Choose categories"):
            btn = await page.query_selector(f'button:has-text("{label}")')
            if btn:
                print("clicking button:", label)
                break
        print("category button found:", bool(btn))
        if btn:
            await btn.scroll_into_view_if_needed()
            await page.wait_for_timeout(500)
            await btn.click()
            await page.wait_for_timeout(5000)

        # viewport screenshot catches overlays/modals better than full_page
        await page.screenshot(path=str(OUT / "cat_modal.png"))

        # Dump the largest visible overlay, else whole body
        modal_html = await page.evaluate("""() => {
            const sels = ['[role=dialog]', '.a-popover-wrapper', '.a-popover', '.a-modal',
                          'div[class*=modal]', 'div[class*=Modal]', 'div[class*=ialog]'];
            let best=null, area=0;
            for (const s of sels) {
                for (const el of document.querySelectorAll(s)) {
                    if (el.offsetParent===null) continue;
                    const r=el.getBoundingClientRect(); const a=r.width*r.height;
                    if (a>area){area=a;best=el;}
                }
            }
            return best ? best.outerHTML : document.body.outerHTML;
        }""")
        (OUT / "cat_modal.html").write_text(modal_html, encoding="utf-8")

        # Structured summary: visible interactive elements across the WHOLE doc
        summary = await page.evaluate("""() => {
            const root = document;
            const vis = el => el.offsetParent !== null;
            const grab = sel => [...root.querySelectorAll(sel)].filter(vis).slice(0, 40).map(e => ({
                tag: e.tagName, type: e.type||'', role: e.getAttribute('role')||'',
                text: (e.innerText||e.value||e.getAttribute('aria-label')||'').trim().slice(0,60),
                cls: (e.className||'').toString().slice(0,50)
            }));
            return {
                buttons: grab('button'),
                checkboxes: grab('input[type=checkbox]'),
                radios: grab('input[type=radio]'),
                searchInputs: grab('input[type=search], input[type=text]'),
                treeItems: grab('[role=treeitem], li, [class*=tree], [class*=Tree]').slice(0,30),
                links: grab('a').slice(0,20),
            };
        }""")
        (OUT / "cat_modal_summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print("buttons:", len(summary["buttons"]), "checkboxes:", len(summary["checkboxes"]),
              "radios:", len(summary["radios"]), "searchInputs:", len(summary["searchInputs"]),
              "treeItems:", len(summary["treeItems"]))
        await browser.close()
        print("dumped to", OUT)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "easy-taxes-self-employed-spain"))
