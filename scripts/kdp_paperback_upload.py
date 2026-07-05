#!/usr/bin/env python3
"""kdp_paperback_upload.py — add a PAPERBACK edition to an existing LIVE ebook.

Safe-by-steps design (per careful-verify rule):
  --inspect        read-only: open bookshelf, locate the title row, dump the
                   candidate "Create paperback" links/buttons + screenshot.
  <slug> --price N full run: create linked paperback, fill details (AI
                   disclosure), content (free KDP ISBN, 6x9 B&W white paper,
                   no bleed, matte, upload interior+cover PDF, previewer
                   approve), pricing (list price USD), then PUBLISH.
  --no-publish     stop after pricing is filled; leaves a resumable draft.

Screenshots of every step land in /root/kdp/logs/paperback-shots/<slug>/.
State written to listing.json under key "paperback".
"""
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.async_api import async_playwright  # noqa: E402

from kdp_upload import (  # noqa: E402 — module import is side-effect free
    KDP_DIR, SESSION_FILE, logger, notify, set_ai_disclosure,
)

SHOTS = Path("/root/kdp/logs/paperback-shots")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")


async def _shot(page, slug, name):
    d = SHOTS / slug
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.png"
    try:
        await page.screenshot(path=str(p), full_page=False)
        logger.info(f"📸 {p}")
    except Exception as e:
        logger.warning(f"screenshot {name} failed: {e}")


async def _new_page(p):
    browser = await p.chromium.launch(headless=True,
                                      args=["--disable-blink-features=AutomationControlled"])
    context = await browser.new_context(storage_state=str(SESSION_FILE), user_agent=UA)
    page = await context.new_page()
    await page.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => false})")
    return browser, page


async def _goto_bookshelf(page):
    await page.goto("https://kdp.amazon.com/en_US/bookshelf",
                    wait_until="domcontentloaded", timeout=60000)
    if "signin" in page.url or "/ap/" in page.url:
        raise RuntimeError("session expired — run kdp_session_ensure.py first")
    await page.wait_for_timeout(4000)


async def _find_row(page, book_id):
    """Return the row container element for our ebook (anchor on its edit link)."""
    link = page.locator(f'a[href*="/kindle/{book_id}/"]').first
    await link.wait_for(state="attached", timeout=30000)
    # climb to a container that also holds the format-action links
    row = link
    for _ in range(8):
        row = row.locator("xpath=..")
        try:
            txt = await row.inner_text(timeout=2000)
        except Exception:
            continue
        if "Create paperback" in txt or "paperback" in txt.lower():
            return row
    return None


async def inspect(slug):
    listing = json.loads((KDP_DIR / slug / "listing.json").read_text())
    book_id = listing["kdp_book_id"]
    async with async_playwright() as p:
        browser, page = await _new_page(p)
        try:
            await _goto_bookshelf(page)
            # search box narrows the shelf to our title (39+ books, pagination)
            try:
                sb = page.locator('input[type="search"], input[placeholder*="earch"]').first
                await sb.fill(listing["title"][:40])
                await sb.press("Enter")
                await page.wait_for_timeout(3500)
            except Exception as e:
                logger.warning(f"search box: {e}")
            try:
                await page.locator(f'a[href*="/kindle/{book_id}/"]').first.scroll_into_view_if_needed(timeout=10000)
                await page.wait_for_timeout(1000)
            except Exception as e:
                logger.warning(f"scroll: {e}")
            await _shot(page, slug, "inspect-1-shelf")
            pb_links = await page.evaluate(
                """() => Array.from(document.querySelectorAll('a,button'))
                    .filter(el => /paperback|create/i.test((el.innerText||'') + ' ' + (el.getAttribute('href')||'')))
                    .map(el => ({t:(el.innerText||'').trim().slice(0,60), href:el.getAttribute('href')}))
                    .slice(0,40)"""
            )
            print("PAPERBACK/CREATE clickables on page:")
            for c in pb_links:
                print(" ", c)
            row = await _find_row(page, book_id)
            if row is None:
                logger.error("row with 'Create paperback' NOT found")
                dump = await page.evaluate(
                    """(bid) => {
                        const a = document.querySelector(`a[href*="/kindle/${bid}/"]`);
                        if (!a) return 'edit link not found';
                        let el = a; for (let i=0;i<8;i++) el = el.parentElement || el;
                        return el.innerText.slice(0, 2500);
                    }""", book_id)
                print("ROW-TEXT-DUMP:\n", dump)
                return
            txt = await row.inner_text()
            print("ROW TEXT:\n", txt[:2000])
            cands = await row.locator("a, button").all()
            print(f"\n{len(cands)} clickables in row:")
            for c in cands[:60]:
                try:
                    t = (await c.inner_text(timeout=800)).strip().replace("\n", " ")[:70]
                    href = await c.get_attribute("href")
                    if t or href:
                        print(f"  [{t}] href={href}")
                except Exception:
                    pass
            await _shot(page, slug, "inspect-2-row")
        finally:
            await browser.close()


async def _click_first(page, selectors, desc, timeout=15000):
    last = None
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=timeout)
            await loc.click()
            logger.info(f"clicked {desc} via {sel}")
            return True
        except Exception as e:
            last = e
    raise RuntimeError(f"could not click {desc}: {last}")


async def create_paperback(slug, price_usd, publish=True):
    book_dir = KDP_DIR / slug
    listing = json.loads((book_dir / "listing.json").read_text())
    if listing.get("paperback", {}).get("submitted_at"):
        logger.info("paperback already submitted — skip (use listing.json to reset)")
        return True
    book_id = listing["kdp_book_id"]
    interior = sorted(book_dir.glob("*paperback.pdf"))[0]
    cover = sorted(book_dir.glob("*paperback-cover.pdf"))[0]
    logger.info(f"interior={interior.name} cover={cover.name} price=${price_usd}")

    async with async_playwright() as p:
        browser, page = await _new_page(p)
        try:
            await _goto_bookshelf(page)
            try:
                sb = page.locator('input[type="search"], input[placeholder*="earch"]').first
                await sb.fill(listing["title"][:40])
                await sb.press("Enter")
                await page.wait_for_timeout(3500)
            except Exception:
                pass
            row = await _find_row(page, book_id)
            if row is None:
                raise RuntimeError("bookshelf row with 'Create paperback' not found")
            await _shot(page, slug, "1-row")
            await row.locator("a:has-text('Create paperback'), button:has-text('Create paperback')").first.click()
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(5000)
            logger.info(f"paperback setup url: {page.url}")
            await _shot(page, slug, "2-details")

            # ── DETAILS TAB (metadata pre-copied from the ebook by KDP) ────
            await set_ai_disclosure(page)
            # some flows require explicitly confirming ownership/rights radio —
            # already answered on the ebook, normally pre-filled.
            await _shot(page, slug, "3-details-filled")
            await _click_first(page, [
                "#save-and-continue-announce",
                "button:has-text('Save and Continue')",
                "span.a-button-inner input[type='submit']",
            ], "Save and Continue (details)")
            await page.wait_for_timeout(8000)
            await _shot(page, slug, "4-content")

            # ── CONTENT TAB ────────────────────────────────────────────────
            # free KDP ISBN
            try:
                await _click_first(page, [
                    "button:has-text('Assign me a free KDP ISBN')",
                    "input[value*='free KDP ISBN']",
                    "a:has-text('Assign me a free KDP ISBN')",
                ], "free ISBN", timeout=8000)
                await page.wait_for_timeout(2500)
                # confirm modal if present
                try:
                    await page.locator("button:has-text('Assign ISBN'), input[type='button'][value*='Assign']").first.click(timeout=4000)
                except Exception:
                    pass
            except RuntimeError:
                logger.info("free-ISBN control not found (maybe already assigned)")
            await _shot(page, slug, "5-isbn")

            # print options: defaults are B&W on white, 6x9, no bleed, matte —
            # verify by reading the summary text; only click if different.
            body = await page.inner_text("body")
            for want in ["Black & white interior with white paper", "6 x 9 in", "No Bleed", "Matte"]:
                if want.lower() not in body.lower():
                    logger.warning(f"print option not showing default: {want!r}")

            # upload manuscript
            files = page.locator("input[type='file']")
            await files.nth(0).set_input_files(str(interior))
            logger.info("manuscript uploading…")
            await page.wait_for_timeout(5000)
            ok = False
            for _ in range(60):  # up to 10 min
                body = await page.inner_text("body")
                if "Manuscript uploaded successfully" in body:
                    ok = True
                    break
                if "error" in body.lower() and "manuscript" in body.lower():
                    break
                await page.wait_for_timeout(10000)
            await _shot(page, slug, "6-manuscript")
            if not ok:
                raise RuntimeError("manuscript upload did not confirm")

            # cover: choose "upload a cover you already have" radio, then file
            try:
                await page.locator("text=Upload a cover you already have").first.click(timeout=6000)
                await page.wait_for_timeout(1500)
            except Exception:
                logger.info("cover-choice radio not found (may default to upload)")
            n = await files.count()
            await files.nth(n - 1).set_input_files(str(cover))
            logger.info("cover uploading…")
            ok = False
            for _ in range(36):
                body = await page.inner_text("body")
                if "Cover uploaded successfully" in body:
                    ok = True
                    break
                await page.wait_for_timeout(10000)
            await _shot(page, slug, "7-cover")
            if not ok:
                raise RuntimeError("cover upload did not confirm")

            # previewer (mandatory before publish)
            await _click_first(page, [
                "#print-preview-announce",
                "button:has-text('Launch Previewer')",
                "a:has-text('Launch Previewer')",
            ], "Launch Previewer", timeout=30000)
            logger.info("previewer processing — this takes minutes…")
            approved = False
            for _ in range(90):  # up to 15 min
                await page.wait_for_timeout(10000)
                try:
                    btn = page.locator("button:has-text('Approve'), a:has-text('Approve')").first
                    if await btn.is_visible():
                        await btn.click()
                        approved = True
                        break
                except Exception:
                    pass
                body = await page.inner_text("body")
                if "we found" in body.lower() and "issue" in body.lower():
                    await _shot(page, slug, "8-previewer-issues")
                    raise RuntimeError("previewer reported issues — see screenshot")
            await _shot(page, slug, "8-previewer")
            if not approved:
                raise RuntimeError("previewer Approve never appeared")
            await page.wait_for_timeout(6000)

            await _click_first(page, [
                "#save-and-continue-announce",
                "button:has-text('Save and Continue')",
            ], "Save and Continue (content)")
            await page.wait_for_timeout(10000)
            await _shot(page, slug, "9-pricing")

            # ── PRICING TAB ────────────────────────────────────────────────
            price_inputs = page.locator(
                "input[id*='price'][type='text'], input[name*='price']")
            filled = False
            cnt = await price_inputs.count()
            for i in range(cnt):
                inp = price_inputs.nth(i)
                if await inp.is_visible() and await inp.is_editable():
                    await inp.fill(str(price_usd))
                    await inp.press("Tab")
                    filled = True
                    break
            if not filled:
                raise RuntimeError("no editable price input found")
            await page.wait_for_timeout(6000)
            body = await page.inner_text("body")
            if "60%" not in body:
                logger.warning("royalty 60% not visible — check screenshot")
            await _shot(page, slug, "10-priced")

            listing.setdefault("paperback", {})
            listing["paperback"].update({
                "price_usd": price_usd,
                "interior": interior.name,
                "cover": cover.name,
                "setup_url": page.url,
            })

            if not publish:
                logger.info("--no-publish: draft left on KDP")
            else:
                await _click_first(page, [
                    "#save-publish-announce",
                    "button:has-text('Publish Your Paperback Book')",
                    "button:has-text('Publish your paperback book')",
                ], "Publish")
                await page.wait_for_timeout(12000)
                await _shot(page, slug, "11-published")
                listing["paperback"]["submitted_at"] = datetime.now().isoformat(timespec="seconds")
                await notify(f"📘 Paperback submitted: {listing['title']} (${price_usd})")

            (book_dir / "listing.json").write_text(
                json.dumps(listing, ensure_ascii=False, indent=2))
            logger.info("✅ done")
            return True
        except Exception as e:
            await _shot(page, slug, "ERROR")
            logger.error(f"❌ {e}")
            await notify(f"❌ Paperback upload failed: {slug} — {e}")
            return False
        finally:
            await browser.close()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("Usage:")
        print("  python3 scripts/kdp_paperback_upload.py <slug> --inspect")
        print("  python3 scripts/kdp_paperback_upload.py <slug> --price 9.99 [--no-publish]")
        sys.exit(2)
    slug = args[0]
    if "--inspect" in sys.argv:
        asyncio.run(inspect(slug))
    else:
        price = 9.99
        if "--price" in sys.argv:
            price = float(sys.argv[sys.argv.index("--price") + 1])
        publish = "--no-publish" not in sys.argv
        ok = asyncio.run(create_paperback(slug, price, publish=publish))
        sys.exit(0 if ok else 1)
