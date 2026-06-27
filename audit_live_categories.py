#!/usr/bin/env python3
"""Audit every LIVE book's REAL KDP categories, flag the ones that need fixing
(any top-level-only placement, or fewer than 3), and compute the resolver's
proposed replacement. Read-only. Writes a report to scratchpad for the batch fixer.
"""
import asyncio, json, sys
from pathlib import Path
from playwright.async_api import async_playwright
from kdp_categories import _open_modal
from category_resolver import resolve_paths
import logging
logging.basicConfig(level=logging.ERROR); log = logging.getLogger("a")

SESSION = "/root/libra/kdp_session.json"
ROSTER = "/root/kdp/bookshelf-roster.json"
OUT = "/tmp/claude-0/-root/37927626-0e42-4118-bced-c8613437c904/scratchpad/cat_audit.json"


async def read_cats(page):
    return await page.evaluate(r"""()=>{
      const cnt=([...document.querySelectorAll('*')].map(e=>e.childElementCount===0?(e.innerText||''):'').find(t=>/out of 3 categor/i.test(t))||'');
      const chips=[...document.querySelectorAll('a.a-expander-header')].map(e=>(e.innerText||'').trim().replace(/\s+/g,' '));
      return {cnt, chips};
    }""")


def is_shallow(chip):
    # "Kindle Books › Self-Help" → only the top level, no subcategory placement.
    parts = [p.strip() for p in chip.split("›") if p.strip()]
    return len(parts) <= 2  # "Kindle Books" + one top-level only


async def main():
    roster = json.load(open(ROSTER))
    live = [e for e in roster["entries"] if e.get("status") == "LIVE" and e.get("slug")]
    report = []
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        c = await b.new_context(storage_state=SESSION)
        for e in live:
            slug = e["slug"]
            try:
                d = json.loads(Path(f"/root/kdp/{slug}/listing.json").read_text())
                bid = d.get("kdp_book_id")
                if not bid:
                    continue
                pg = await c.new_page()
                await pg.goto(f"https://kdp.amazon.com/en_US/title-setup/kindle/{bid}/details",
                              wait_until="domcontentloaded", timeout=60000)
                await pg.wait_for_timeout(3000)
                if "bookshelf" in pg.url and "title-setup" not in pg.url:
                    print(f"{slug}: IN_REVIEW skip"); await pg.close(); continue
                await _open_modal(pg, log); await pg.wait_for_timeout(1200)
                r = await read_cats(pg)
                await pg.close()
                chips = r["chips"]
                shallow = [ch for ch in chips if is_shallow(ch)]
                count = len(chips)
                proposed = resolve_paths(d.get("categories", []))
                # Needs fix if: any shallow placement, <3 categories, or a resolver
                # proposal that's all real (different from a clearly-bad current set).
                needs = bool(shallow) or count < 3
                report.append({"slug": slug, "kdp_count": count, "kdp_chips": chips,
                               "shallow": shallow, "listing_cats": d.get("categories", []),
                               "proposed": proposed, "needs_fix": needs})
                flag = "⚠️ FIX" if needs else "ok"
                print(f"{flag:7} {slug:44} [{count}cat] shallow={len(shallow)}")
            except Exception as ex:
                print(f"{slug}: ERR {str(ex)[:60]}")
        await b.close()
    Path(OUT).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    need = [r for r in report if r["needs_fix"]]
    print(f"\n=== {len(need)}/{len(report)} books need category fix → {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
