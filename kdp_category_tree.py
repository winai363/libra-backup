#!/usr/bin/env python3
"""
kdp_category_tree.py — Snapshot KDP's REAL category tree (the live category-picker
modal) to data/kdp_category_tree.json, so we can resolve the category paths GPT
proposes (which often don't match KDP's actual nesting, e.g. GPT says
"Arts & Photography > Painting > Watercolor" but KDP nests it as
"Arts & Photography > Art > Painting > General") to valid leaf paths before upload.

Output JSON: {"generated_at": "...", "leaves": ["L0 > L1 > ... > Placement", ...]}

We enumerate by re-driving the cascade selects WITHOUT ticking any placement
(ticking would lock the row), so a single open modal yields a whole L0 subtree.

Usage:
  python3 kdp_category_tree.py                 # core top-levels Libra uses
  python3 kdp_category_tree.py "Arts & Photography"   # one top-level (test)
"""
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

from kdp_categories import (_open_modal, _reset, _select_at_level,
                            _expand_a_row, _placement_checkboxes)


async def _robust_l0(page):
    """Get the level-0 select, retrying with a row-expand — the modal is flaky and
    a bare _select_at_level(0) intermittently returns None (that's how an entire
    top-level like Self-Help got crawled as only 2 leaves)."""
    for _ in range(6):
        l0 = await _select_at_level(page, 0)
        if l0:
            return l0
        await _expand_a_row(page)
        await page.wait_for_timeout(1200)
    return None

LIBRA = Path(__file__).parent
SESSION = LIBRA / "kdp_session.json"
OUT = LIBRA / "data" / "kdp_category_tree.json"
# A LIVE book whose modal we borrow to read the (global) category tree.
PROBE_BOOK = "easy-taxes-self-employed-spain"

CORE_TOP_LEVELS = [
    "Business & Money", "Health, Fitness & Dieting", "Self-Help",
    "Computers & Technology", "Arts & Photography", "Children's eBooks",
    "Teen & Young Adult", "Parenting & Relationships", "Cookbooks, Food & Wine",
    "Crafts, Hobbies & Home", "Education & Teaching", "Reference",
]


def _opts(select):
    return [o for o in (select or {}).get("options", [])
            if o.strip().lower() not in ("select one", "select")]


async def _set(page, name, label):
    await page.select_option(f'select[name="{name}"]', label=label)
    await page.wait_for_timeout(900)


async def enumerate_top_level(page, l0, log):
    """Return a sorted list of full leaf paths under one top-level category."""
    leaves = set()
    await _reset(page, log)
    l0sel = await _robust_l0(page)
    if not l0sel:
        return []
    if l0 not in _opts(l0sel):
        print(f"  [{l0}] not a top-level option — skipping")
        return []
    await _set(page, l0sel["name"], l0)
    l1sel = await _select_at_level(page, 1)
    l1_options = _opts(l1sel)
    print(f"  [{l0}] {len(l1_options)} subcategories")

    for l1 in l1_options:
        try:
            # Re-drive L0→L1 fresh each time (cheap reset keeps the row editable).
            await _reset(page, log)
            l0sel = await _robust_l0(page)
            await _set(page, l0sel["name"], l0)
            l1sel = await _select_at_level(page, 1)
            await _set(page, l1sel["name"], l1)
            # Direct placements right under L1.
            for pb in await _placement_checkboxes(page):
                leaves.add(f"{l0} > {l1} > {pb['label']}")
            # Optional L2 level.
            l2sel = await _select_at_level(page, 2)
            for l2 in _opts(l2sel):
                try:
                    await _set(page, l2sel["name"], l2)
                    for pb in await _placement_checkboxes(page):
                        leaves.add(f"{l0} > {l1} > {l2} > {pb['label']}")
                    # Re-fetch the L2 select handle for the next iteration (the
                    # cascade re-rendered after selecting a value).
                    l2sel = await _select_at_level(page, 2)
                    if not l2sel:
                        break
                except Exception as e:
                    print(f"    L2 '{l2}' failed: {str(e)[:60]}")
                    l2sel = await _select_at_level(page, 2)
                    if not l2sel:
                        break
        except Exception as e:
            print(f"  L1 '{l1}' failed: {str(e)[:60]}")
            continue
    return sorted(leaves)


async def main(top_levels):
    import logging
    logging.basicConfig(level=logging.WARNING)
    log = logging.getLogger("tree")
    book_id = json.loads((Path("/root/kdp") / PROBE_BOOK / "listing.json").read_text())["kdp_book_id"]
    all_leaves = []
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        c = await b.new_context(storage_state=str(SESSION))
        page = await c.new_page()
        await page.goto(f"https://kdp.amazon.com/en_US/title-setup/kindle/{book_id}/details",
                        wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        if not await _open_modal(page, log):
            print("could not open category modal"); await b.close(); return
        for l0 in top_levels:
            try:
                leaves = await enumerate_top_level(page, l0, log)
                print(f"  [{l0}] → {len(leaves)} leaves", flush=True)
                all_leaves.extend(leaves)
                # Persist after EACH top-level so a long crawl that's interrupted
                # (or a single flaky modal) never loses the work already done, and
                # the snapshot is usable as soon as the first top-level lands.
                _persist(all_leaves)
            except Exception as e:
                print(f"[{l0}] FAILED: {str(e)[:80]}", flush=True)
        await b.close()

    if not all_leaves:
        print("No leaves enumerated — NOT overwriting snapshot."); return
    print(f"\n✅ done → {OUT}")


def _persist(new_leaves):
    """Write the snapshot, merging with whatever is already on disk so each
    top-level's results accumulate (and a refresh never shrinks coverage)."""
    existing = set()
    if OUT.exists():
        try:
            existing = set(json.loads(OUT.read_text()).get("leaves", []))
        except Exception:
            existing = set()
    merged = sorted(existing | set(new_leaves))
    OUT.write_text(json.dumps(
        {"generated_at": datetime.now().isoformat(timespec="seconds"),
         "top_levels": sorted({l.split(" > ")[0] for l in merged}),
         "leaves": merged}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    tops = [" ".join(sys.argv[1:])] if len(sys.argv) > 1 else CORE_TOP_LEVELS
    asyncio.run(main(tops))
