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
    ENV, KDP_DIR, KDP_EMAIL, KDP_PASSWORD, SESSION_FILE, logger, notify,
    set_ai_disclosure,
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


async def _reauth(page, slug):
    """Amazon forces a fresh sign-in (max_auth_age=0) when creating a title.
    Handle password re-entry + TOTP on the spot, then land on return_to URL."""
    def _is_auth_page(url):
        path = url.split("?", 1)[0]  # query params may embed 'signin' (openid)
        return "/ap/" in path or "signin" in path
    if not _is_auth_page(page.url):
        return
    logger.info("re-auth wall — signing in inline…")
    try:
        email_in = page.locator("input#ap_email, input[name='email']").first
        if await email_in.is_visible(timeout=3000):
            await email_in.fill(KDP_EMAIL)
            try:
                await page.locator("input#continue").first.click(timeout=3000)
                await page.wait_for_timeout(2000)
            except Exception:
                pass
    except Exception:
        pass
    pw = page.locator("input[type='password'], input#ap_password").first
    await pw.wait_for(state="visible", timeout=15000)
    await pw.fill(KDP_PASSWORD)
    await page.locator("input#signInSubmit, input[type='submit']").first.click()
    await page.wait_for_timeout(5000)
    body = (await page.inner_text("body")).lower()
    if "two-step" in body or "verification" in body or "otp" in body:
        import pyotp
        secret = ENV.get("KDP_TOTP_SECRET", "")
        if not secret:
            raise RuntimeError("re-auth needs TOTP but KDP_TOTP_SECRET missing")
        # device-choice page (radio) appears sometimes; code page other times
        code_in = page.locator(
            "input[name='otpCode'], input#auth-mfa-otpcode, input[autocomplete='one-time-code']").first
        if not await code_in.is_visible():
            try:
                radio = page.locator("input[value*='TOTP'], input[value*='totp']").first
                await radio.click(timeout=4000)
            except Exception:
                pass
            await page.locator("input[type='submit'], button[type='submit']").first.click()
            await code_in.wait_for(state="visible", timeout=15000)
        await code_in.fill(pyotp.TOTP(secret).now())
        await page.locator("input[type='submit'], button[type='submit']").first.click()
        await page.wait_for_timeout(6000)
    await _shot(page, slug, "reauth-done")
    if _is_auth_page(page.url):
        raise RuntimeError(f"re-auth did not clear: {page.url}")
    logger.info(f"re-auth ok → {page.url}")


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


async def create_paperback(slug, price_usd, publish=True, paths_override=None, reupload=False):
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
            # search narrowed the shelf — require EXACTLY our one title before
            # touching a page-level button (kindle edit links count must be 1)
            kindle_links = await page.locator('a[href*="/kindle/"][href*="/details"], a[href*="/kindle/"]').all()
            ids = set()
            for kl in kindle_links:
                href = await kl.get_attribute("href") or ""
                m = __import__("re").search(r"/kindle/([A-Z0-9]{8,})/", href)
                if m:
                    ids.add(m.group(1))
            if ids != {book_id}:
                raise RuntimeError(f"shelf not narrowed to our book (saw ids={ids}) — abort")
            await _shot(page, slug, "1-row")
            # resume an existing paperback draft if the row already has one
            pb_id = listing.get("paperback", {}).get("kdp_book_id")
            if not pb_id:
                hrefs = await page.evaluate(
                    """() => Array.from(document.querySelectorAll('a'))
                        .map(a => a.getAttribute('href') || '')
                        .filter(h => /\\/paperback\\/[A-Z0-9]{8,}\\//.test(h))""")
                m = [__import__("re").search(r"/paperback/([A-Z0-9]{8,})/", h).group(1) for h in hrefs]
                pb_id = m[0] if m else None
            if pb_id:
                logger.info(f"resuming paperback draft {pb_id}")
                await page.goto(f"https://kdp.amazon.com/en_US/title-setup/paperback/{pb_id}/content",
                                wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(5000)
                await _reauth(page, slug)
            else:
                await page.locator("button:has-text('Create paperback'), a:has-text('Create paperback')").first.click()
                await page.wait_for_load_state("domcontentloaded")
                await page.wait_for_timeout(5000)
                await _reauth(page, slug)
            await page.wait_for_timeout(3000)
            if "/title-setup/paperback/" not in page.url:
                raise RuntimeError(f"not on paperback setup page: {page.url}")
            m = __import__("re").search(r"/paperback/([A-Z0-9]{8,})/", page.url)
            if m:
                listing.setdefault("paperback", {})["kdp_book_id"] = m.group(1)
                (book_dir / "listing.json").write_text(
                    json.dumps(listing, ensure_ascii=False, indent=2))
            logger.info(f"paperback setup url: {page.url}")
            await _shot(page, slug, "2-details")

            on_details = "/details" in page.url.split("?")[0]
            if not on_details:
                logger.info("landed on content tab — details already complete, skipping")
            else:
                # ── DETAILS TAB (metadata pre-copied from the ebook) ───────
                # categories do NOT carry over ("Add at least one new category
                # to continue") — apply via the proven 3-category modal driver
                # (fuzzy matcher survives Kindle-tree vs print-tree naming)
                if listing.get("paperback", {}).get("categories_done"):
                    logger.info("categories already applied on a previous run — skip")
                else:
                    from kdp_categories import set_categories
                    cats = paths_override or listing.get("paperback", {}).get("categories") \
                        or listing.get("categories", [])
                    applied = await set_categories(page, cats, logger=logger)
                    logger.info(f"paperback categories applied: {applied}")
                    if not applied:
                        raise RuntimeError("no category could be applied on paperback details")
                    listing.setdefault("paperback", {})["categories_done"] = applied
                    (book_dir / "listing.json").write_text(
                        json.dumps(listing, ensure_ascii=False, indent=2))
                await set_ai_disclosure(page)
                await _shot(page, slug, "3-details-filled")
                await _click_first(page, [
                    "#save-and-continue-announce",
                    "button:has-text('Save and Continue')",
                    "span.a-button-inner input[type='submit']",
                ], "Save and Continue (details)")
                await page.wait_for_timeout(8000)
                await _reauth(page, slug)  # Amazon may demand sign-in mid-flow
                await page.wait_for_timeout(2000)
                body = await page.inner_text("body")
                if "Add at least one new category" in body:
                    await _shot(page, slug, "ERROR-details-validation")
                    # modal picks were lost (e.g. auth wall before save) — make
                    # the next run redo them instead of trusting the stamp
                    listing.setdefault("paperback", {}).pop("categories_done", None)
                    (book_dir / "listing.json").write_text(
                        json.dumps(listing, ensure_ascii=False, indent=2))
                    raise RuntimeError("details validation still failing (categories) — stamp cleared for retry")
            await _shot(page, slug, "4-content")

            # ── CONTENT TAB ────────────────────────────────────────────────
            # free KDP ISBN — radio "Get a free KDP ISBN" then button "Assign ISBN"
            body = await page.inner_text("body")
            if "assigned a free KDP ISBN" in body:
                logger.info("ISBN already assigned — skip")
            elif "Assign ISBN" in body:
                try:
                    await page.locator("text=Get a free KDP ISBN").first.click(timeout=5000)
                    await page.wait_for_timeout(1000)
                except Exception:
                    pass
                await _click_first(page, [
                    "button:has-text('Assign ISBN')",
                    "input[value='Assign ISBN']",
                    ".a-button:has-text('Assign ISBN')",
                ], "Assign ISBN", timeout=10000)
                # confirmation modal "Free KDP ISBN" — the confirm button is the
                # LAST 'Assign ISBN' control on the page (section button is first)
                try:
                    await page.locator("text=Free KDP ISBN").first.wait_for(state="visible", timeout=8000)
                    await page.wait_for_timeout(800)
                    # button lives in shadow DOM — role locator pierces it and
                    # only matches accessible (visible) nodes
                    try:
                        await page.get_by_role("button", name="Assign ISBN").last.click(timeout=6000)
                        logger.info("ISBN modal confirmed via role locator")
                    except Exception:
                        # last resort: coordinate click on the modal button
                        # (stable KDP modal layout at 1280x720 viewport)
                        await page.mouse.click(1053, 417)
                        logger.info("ISBN modal confirmed via coordinate click")
                except Exception as e:
                    logger.info(f"ISBN confirm modal handling: {e}")
                assigned = False
                for _ in range(18):
                    await page.wait_for_timeout(5000)
                    body = await page.inner_text("body")
                    if "assigned a free KDP ISBN" in body or "ISBN: 979" in body:
                        assigned = True
                        break
                if not assigned:
                    await _shot(page, slug, "ERROR-isbn")
                    raise RuntimeError("ISBN still unassigned after clicking Assign")
                logger.info("ISBN assigned")
            else:
                logger.info("ISBN section shows no Assign button (already assigned)")
            await _shot(page, slug, "5-isbn")

            # print options: defaults are B&W on white, 6x9, no bleed, matte —
            # verify by reading the summary text; only click if different.
            body = await page.inner_text("body")
            for want in ["Black & white interior with white paper", "6 x 9 in", "No Bleed", "Matte"]:
                if want.lower() not in body.lower():
                    logger.warning(f"print option not showing default: {want!r}")

            # dismiss a stuck "Uploading…" modal from any previous run
            try:
                await page.locator("button:has-text('Cancel Upload')").first.click(timeout=3000)
                await page.wait_for_timeout(2000)
                logger.info("cancelled a stale upload modal")
            except Exception:
                pass

            # upload manuscript via the framework's dedicated input + hidden
            # status field (same ids as the proven ebook flow)
            async def _asset_status(kind):
                # print pages use data-print-book-publisher-* ids; cover has a
                # separate "pdf-only" track for print-ready PDF covers
                ids = {
                    "interior": ["data-print-book-publisher-interior-asset-status"],
                    "cover": ["data-print-book-publisher-cover-pdf-only-asset-status",
                              "data-print-book-publisher-cover-asset-status"],
                }[kind]
                return await page.evaluate(
                    "(ids) => ids.map(i => { const el = document.getElementById(i);"
                    " return el ? el.value : ''; }).join('|')", ids)

            async def _dump_upload_dom():
                info = await page.evaluate(
                    """() => ({
                        fileInputs: [...document.querySelectorAll("input[type=file]")].map(i => i.id || i.name || '?'),
                        statusEls: [...document.querySelectorAll("[id*=status i]")].map(i => `${i.id}=${(i.value||i.textContent||'').slice(0,40)}`).slice(0,20),
                    })""")
                logger.info(f"DOM dump: {info}")

            async def _upload_asset(kind, path, shotname, success_text, max_rounds):
                """kind: 'interior'|'cover'. Retries around KDP's transient
                'We're Sorry' upload-service failures."""
                for attempt in range(1, 4):
                    body = await page.inner_text("body")
                    if not reupload and attempt == 1 and (
                            success_text in body or "SUCCESS" in await _asset_status(kind)):
                        logger.info(f"{kind} already uploaded — skip")
                        return
                    if "We're Sorry" in body:
                        logger.warning(f"{kind}: 'We're Sorry' modal — reloading page")
                        await page.reload(wait_until="domcontentloaded")
                        await page.wait_for_timeout(6000)
                    sel = ("#data-print-book-publisher-interior-file-upload-AjaxInput"
                           if kind == "interior" else
                           "#data-print-book-publisher-cover-pdf-only-file-upload-AjaxInput")
                    inp = page.locator(sel)
                    if not await inp.count():
                        inp = page.locator("input[type='file']").first if kind == "interior" \
                            else page.locator("input[type='file']").last
                    await inp.set_input_files(str(path))
                    logger.info(f"{kind} uploading… (attempt {attempt})")
                    if reupload:
                        # replacing an existing file: give KDP time to flip the
                        # stale SUCCESS status before we start polling it
                        await page.wait_for_timeout(60000)
                    for _ in range(max_rounds):
                        await page.wait_for_timeout(10000)
                        st = await _asset_status(kind)
                        body = await page.inner_text("body")
                        if "SUCCESS" in st or success_text in body:
                            await _shot(page, slug, shotname)
                            logger.info(f"{kind} uploaded ✓")
                            return
                        if "ERROR" in st or "We're Sorry" in body:
                            break
                    await _shot(page, slug, f"{shotname}-retry{attempt}")
                await _dump_upload_dom()
                raise RuntimeError(f"{kind} upload failed after 3 attempts")

            await _upload_asset("interior", interior, "6-manuscript",
                                "Manuscript uploaded successfully", 90)

            # cover: choose "upload a cover you already have" radio, then file
            try:
                await page.locator("text=Upload a cover you already have").first.click(timeout=6000)
                await page.wait_for_timeout(1500)
            except Exception:
                logger.info("cover-choice radio not found (may default to upload)")
            await _upload_asset("cover", cover, "7-cover",
                                "Cover uploaded successfully", 60)

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

            # print flow asks the AI-tools disclosure on the CONTENT tab
            await set_ai_disclosure(page, require_selections=True)
            await _click_first(page, [
                "#save-and-continue-announce",
                "button:has-text('Save and Continue')",
            ], "Save and Continue (content)")
            await page.wait_for_timeout(10000)
            body = await page.inner_text("body")
            if "Specify if you used AI tools" in body:
                await _shot(page, slug, "ERROR-ai-disclosure")
                raise RuntimeError("AI disclosure still unanswered on content tab")
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
        paths = None
        if "--paths" in sys.argv:
            paths = [p.strip() for p in sys.argv[sys.argv.index("--paths") + 1].split(";") if p.strip()]
        publish = "--no-publish" not in sys.argv
        ok = asyncio.run(create_paperback(slug, price, publish=publish, paths_override=paths,
                                          reupload="--reupload" in sys.argv))
        sys.exit(0 if ok else 1)
