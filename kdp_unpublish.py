#!/usr/bin/env python3
"""
kdp_unpublish.py — Remove duplicate KDP listings (audit 2026-06-25, approved by Bui).

Libra published a few books more than once. The bookshelf roster
(kdp_bookshelf_roster.py) identified the duplicates. For each book we KEEP the
copy whose kdp_book_id matches our local listing.json (the one our pipeline can
update) and REMOVE the strays:
  - LIVE strays  -> Unpublish (removed from the Amazon storefront; republishable)
  - DRAFT strays -> Archive   (removed from the active bookshelf)

SAFETY: each target is pinned by kdp_book_id AND verified against an expected
ASIN + title fragment before ANY click. If the live row doesn't match, that
target is skipped. Nothing destructive runs without --confirm; a dry run stops
at the confirmation modal and screenshots it.

Usage:
  python3 kdp_unpublish.py            # dry run: open menu + screenshot, no commit
  python3 kdp_unpublish.py --confirm  # actually unpublish / archive
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kdp_freeze import assert_kdp_mutation_allowed  # noqa: E402

LIBRA_DIR = Path(__file__).parent
KDP_DIR = LIBRA_DIR.parent / "kdp"
SESSION_FILE = LIBRA_DIR / "kdp_session.json"
SHOTS = LIBRA_DIR / "logs" / "unpublish-shots"

# Targets approved by Bui — the duplicate strays to remove. The KEEPERS
# (B0H4CPDDBY, B0H3FQ7BDQ) are intentionally NOT in this list.
# Refreshed 2026-07-08 from bookshelf-roster.json: the 3 current DRAFT strays
# (all never-published). Drafts can only be Archived (no Unpublish link on a draft).
TARGETS = [
    {
        "book_id": "A1XSA2OH5H2JIY", "asin": "B0H365SW7S", "action": "archive",
        "title_must_include": "Advanced Python", "note": "orphan generic draft (slug=None)",
    },
    {
        "book_id": "A2X8DSDRZJFNWR", "asin": "B0H3FJNK8Z", "action": "archive",
        "title_must_include": "Quaderno Creativo AI", "note": "ai-creative-workbook-italian duplicate (LIVE kept)",
    },
    {
        "book_id": "A326HKKCYZ0HYN", "asin": "B0H38QFT5Z", "action": "archive",
        "title_must_include": "Mocktail-Rezepte", "note": "sober-mocktails-de duplicate (LIVE kept)",
    },
]

BOOKSHELF = "https://kdp.amazon.com/en_US/bookshelf"


def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}")


# JS: locate the row for a book_id, return its visible text + the ASINs it shows.
_ROW_INFO_JS = r"""(bid) => {
    const re = new RegExp('/(kindle|paperback|hardcover)/' + bid + '(/|\\?|$)');
    for (const a of document.querySelectorAll('a[href]')) {
        if (re.test(a.href || '')) {
            let n = a;
            for (let i = 0; i < 14 && n.parentElement; i++) {
                n = n.parentElement;
                if ((n.innerText || '').length > 140) break;
            }
            return (n.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 400);
        }
    }
    return null;
}"""

# JS: within the target row, open the kebab and click the action link.
_OPEN_AND_CLICK_JS = r"""({bid, idPrefix}) => {
    const re = new RegExp('/(kindle|paperback|hardcover)/' + bid + '(/|\\?|$)');
    for (const a of document.querySelectorAll('a[href]')) {
        if (re.test(a.href || '')) {
            let n = a;
            for (let i = 0; i < 14 && n.parentElement; i++) {
                n = n.parentElement;
                if ((n.innerText || '').length > 140) break;
            }
            const kebab = [...n.querySelectorAll('button')]
                .find(x => (x.getAttribute('aria-label') || '') === 'more actions');
            if (!kebab) return 'no-kebab';
            kebab.click();
            return 'kebab-clicked';
        }
    }
    return 'row-not-found';
}"""


async def _click_action_link(page, bid: str, id_prefix: str) -> bool:
    """Click the action link (#unpublish-<bid> / archive) scoped to the row."""
    return await page.evaluate(
        r"""({bid, idPrefix}) => {
            const re = new RegExp('/(kindle|paperback|hardcover)/' + bid + '(/|\\?|$)');
            for (const a of document.querySelectorAll('a[href]')) {
                if (re.test(a.href || '')) {
                    let n = a;
                    for (let i = 0; i < 14 && n.parentElement; i++) {
                        n = n.parentElement;
                        if ((n.innerText || '').length > 140) break;
                    }
                    const link = n.querySelector('a[id^="' + idPrefix + '"]');
                    if (link) { link.click(); return true; }
                }
            }
            return false;
        }""",
        {"bid": bid, "idPrefix": id_prefix},
    )


async def _click_modal_confirm(page, label: str) -> bool:
    """Click the confirm button (text == label) inside the visible modal."""
    return await page.evaluate(
        r"""(label) => {
            const vis = el => { const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
            const els = [...document.querySelectorAll('.a-popover button, .a-popover a, [role=dialog] button, [role=dialog] a, .a-button-input')];
            const hit = els.find(e => vis(e) && (e.innerText || e.value || '').trim().toLowerCase() === label.toLowerCase());
            if (hit) { hit.click(); return true; }
            return false;
        }""",
        label,
    )


async def run(confirm: bool) -> None:
    assert_kdp_mutation_allowed("unpublish")
    from playwright.async_api import async_playwright

    SHOTS.mkdir(parents=True, exist_ok=True)
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            storage_state=str(SESSION_FILE), viewport={"width": 1400, "height": 1000}
        )
        try:
            for t in TARGETS:
                bid, asin = t["book_id"], t["asin"]
                _log(f"--- {t['note']} (book_id={bid}, asin={asin}, action={t['action']}) ---")
                page = await context.new_page()
                await page.goto(BOOKSHELF, wait_until="domcontentloaded", timeout=60000)
                if "signin" in page.url or "/ap/" in page.url:
                    _log("  !! session expired — aborting"); break
                await page.wait_for_timeout(3500)
                try:
                    await page.select_option(
                        "#refreshedbookshelftable-records-per-page-dropdown-option", "50")
                    await page.wait_for_timeout(4500)
                except Exception:
                    await page.wait_for_timeout(2000)

                # SAFETY: verify the row matches book_id + asin + title before acting.
                row_text = await page.evaluate(_ROW_INFO_JS, bid)
                if not row_text:
                    _log("  !! row not found — skip"); results.append((t, "row-not-found")); await page.close(); continue
                if asin not in row_text or t["title_must_include"].lower() not in row_text.lower():
                    _log(f"  !! safety mismatch — row='{row_text[:90]}' — SKIP"); results.append((t, "safety-mismatch")); await page.close(); continue
                _log(f"  ✓ verified row: {row_text[:80]}")

                # Open kebab.
                state = await page.evaluate(_OPEN_AND_CLICK_JS, {"bid": bid, "idPrefix": ""})
                if state != "kebab-clicked":
                    _log(f"  !! could not open menu ({state}) — skip"); results.append((t, state)); await page.close(); continue
                await page.wait_for_timeout(1500)

                id_prefix = "unpublish-" if t["action"] == "unpublish" else "itemset_archive_title"
                confirm_label = "Unpublish" if t["action"] == "unpublish" else "Archive"
                clicked = await _click_action_link(page, bid, id_prefix)
                if not clicked:
                    _log(f"  !! action link ({id_prefix}) not found — skip"); results.append((t, "no-action-link")); await page.close(); continue
                await page.wait_for_timeout(2500)
                shot = SHOTS / f"{asin}_modal.png"
                await page.screenshot(path=str(shot))
                _log(f"  modal screenshot -> {shot.name}")

                if not confirm:
                    _log("  [dry-run] stopping at confirmation modal (no commit)")
                    results.append((t, "dry-run-modal")); await page.close(); continue

                ok = await _click_modal_confirm(page, confirm_label)
                if not ok:
                    _log(f"  !! confirm button '{confirm_label}' not found — NOT committed"); results.append((t, "confirm-not-found")); await page.close(); continue
                await page.wait_for_timeout(4000)
                await page.screenshot(path=str(SHOTS / f"{asin}_after.png"))
                # Re-read row to confirm the new status.
                after = await page.evaluate(_ROW_INFO_JS, bid)
                _log(f"  ✓ committed {t['action']}; row now: {(after or 'gone')[:80]}")
                results.append((t, f"done:{t['action']}"))
                await page.close()
        finally:
            await browser.close()

    _log("==== SUMMARY ====")
    for t, outcome in results:
        _log(f"  {outcome:<18} {t['asin']} — {t['note']}")
    # Record local status for committed actions.
    if confirm:
        _mark_local(results)


def _mark_local(results) -> None:
    """Tag the local listing.json (if the stray ASIN was the recorded one)."""
    roster = KDP_DIR / "bookshelf-roster.json"
    for t, outcome in results:
        if not outcome.startswith("done"):
            continue
        # Strays usually aren't the keeper recorded in listing.json, so just log
        # them into a removed-duplicates ledger for the audit trail.
        ledger = KDP_DIR / "removed-duplicates.json"
        data = json.loads(ledger.read_text()) if ledger.exists() else []
        data.append({
            "asin": t["asin"], "book_id": t["book_id"], "action": t["action"],
            "note": t["note"], "at": datetime.now().isoformat(timespec="seconds"),
        })
        ledger.write_text(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(run(confirm="--confirm" in sys.argv))
