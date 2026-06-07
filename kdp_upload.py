#!/usr/bin/env python3
"""
KDP Upload Automation — Uploads ebook to Amazon KDP
Usage: python3 kdp_upload.py <slug>
"""

import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kdp_upload")
logging.getLogger("httpx").setLevel(logging.WARNING)

# Load config
ENV = {}
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            ENV[k.strip()] = v.strip()

KDP_DIR = Path(ENV.get("KDP_DIR", "/root/kdp"))
KDP_EMAIL = ENV.get("KDP_EMAIL", "")
KDP_PASSWORD = ENV.get("KDP_PASSWORD", "")
AUTHOR_NAME = ENV.get("AUTHOR_NAME", "")
SESSION_FILE = Path(__file__).parent / "kdp_session.json"
TELEGRAM_BOT_TOKEN = ENV.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = ENV.get("TELEGRAM_CHAT_ID", "")


def require_quality_gate(slug: str) -> bool:
    """Block every KDP write unless the deterministic 40-page gate passes."""
    try:
        from quality_gate import validate_book, write_report
        report = validate_book(
            slug,
            require_pdf=True,
            check_urls=True,
            require_editorial=True,
        )
        write_report(report)
        if report.passed:
            return True
        logger.error("Quality gate blocked %s: %s", slug, "; ".join(report.errors))
        return False
    except Exception as exc:
        logger.error("Quality gate crashed for %s: %s", slug, exc)
        return False


async def notify(message: str):
    """Send Telegram notification"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
    except Exception as e:
        logger.error(f"Telegram notify failed: {e}")


async def set_ai_disclosure(page) -> None:
    """Set mandatory KDP AI disclosure for GPT text and AI-generated cover art.

    On update flows, Amazon may show a reduced option set (e.g. no 'Entire work'
    for text if the book was already disclosed differently). We try the preferred
    option first, fall back to 'Some content', and skip gracefully if the
    accordion is absent (already set on an existing title).
    """
    ai_accordion = page.locator('[data-a-accordion-name="generative-ai-questionnaire-accordion"]')
    # If the accordion doesn't exist on this page (e.g. KDP skips it on updates), skip.
    if not await ai_accordion.is_visible():
        logger.info("AI disclosure accordion not present — skipping (likely pre-set on existing title)")
        return
    yes_row = ai_accordion.locator('[data-a-accordion-row-name="yes"] .a-accordion-row')
    await yes_row.click()
    await page.wait_for_timeout(800)
    # text: prefer "Entire work", fall back to "Some content" if unavailable on update
    selections = {
        "generative-ai-questionnaire-text": (["Entire work", "Some content"], "GPT-4.1"),
        "generative-ai-questionnaire-images": (["Some images", "Some content"], "gpt-image-1"),
        "generative-ai-questionnaire-translations": (["None"], None),
    }
    for selector_id, (candidates, tool_name) in selections.items():
        container = page.locator(".a-dropdown-container").filter(has=page.locator(f"#{selector_id}"))
        if not await container.is_visible():
            logger.info(f"  AI {selector_id}: dropdown not visible — skipping")
            continue
        await container.locator(".a-button-dropdown").click()
        await page.wait_for_timeout(500)
        clicked = False
        for target_text in candidates:
            for element in await page.query_selector_all("li a, li"):
                try:
                    if not await element.is_visible():
                        continue
                    text = (await element.inner_text()).strip()
                    # Match prefix: "Entire work" matches "Entire work, with minimal or no editing"
                    if text == target_text or text.startswith(target_text):
                        # Use JS dispatch to bypass pointer-events intercept from dropdown overlay
                        await page.evaluate("el => el.click()", element)
                        clicked = True
                        logger.info(f"  AI {selector_id}: set to '{text}'")
                        break
                except Exception:
                    continue
            if clicked:
                break
        if not clicked:
            logger.warning(f"⚠️ AI disclosure: no matching option for {selector_id} — leaving as-is")
        if clicked and tool_name:
            content_type = selector_id.rsplit("-", 1)[-1]
            prompt_id = f"generative-ai-questionnaire-{content_type}-tools-prompt"
            input_box = page.locator(f'input[aria-labelledby="{prompt_id}"]').first
            if await input_box.is_visible():
                await input_box.fill(tool_name)
            else:
                logger.warning(f"⚠️ AI tool field not visible for {content_type} — skipping")
    logger.info("✓ AI tools done")


def _generate_fallback_cover(book_dir, title, subtitle, author, categories=None, keywords=None):
    """Generate a smart book cover using cover_generator (genre-aware design)."""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from cover_generator import generate_cover
        out = generate_cover(
            book_dir   = book_dir,
            title      = title,
            subtitle   = subtitle,
            author     = author,
            categories = categories or [],
            keywords   = keywords   or [],
        )
        logger.info(f"✓ Cover generated: {out} ({out.stat().st_size} bytes)")
        return out
    except Exception as e:
        logger.error(f"Cover generation failed: {e}")
        return None


async def upload_to_kdp(slug: str):
    """Upload ebook to KDP"""
    if not require_quality_gate(slug):
        return False
    book_dir = KDP_DIR / slug
    listing_file = book_dir / "listing.json"

    if not listing_file.exists():
        logger.error(f"No listing.json found for {slug}")
        return False

    listing = json.loads(listing_file.read_text())
    title = listing.get("title", slug)
    subtitle = listing.get("subtitle", "")
    description = listing.get("description", "")
    keywords = listing.get("keywords", [])
    categories = listing.get("categories", [])
    language = listing.get("language", "English")

    # Find files
    epubs = list(book_dir.glob("*.epub"))
    covers = list(book_dir.glob("cover.jpg"))

    if not epubs:
        logger.error(f"No EPUB found for {slug}")
        return False
    if not covers:
        logger.warning(f"⚠️ No cover found for {slug}, generating cover...")
        cover_path = _generate_fallback_cover(book_dir, title, subtitle,
                                              AUTHOR_NAME or "Unknown",
                                              categories=categories, keywords=keywords)
        if not cover_path:
            logger.error("❌ Failed to generate cover")
            return False
    else:
        cover_path = covers[0]

    epub_path = epubs[0]

    # Validate cover file — must be valid JPEG > 10KB
    cover_size = cover_path.stat().st_size
    if cover_size < 10000:
        logger.warning(f"⚠️ Cover too small ({cover_size} bytes), regenerating...")
        cover_path = _generate_fallback_cover(book_dir, title, subtitle,
                                              AUTHOR_NAME or "Unknown",
                                              categories=categories, keywords=keywords)
        if not cover_path:
            logger.error("❌ Failed to generate cover")
            return False

    # Validate description
    if not description or len(description.strip()) < 20:
        logger.error(f"❌ Description too short or empty ({len(description)} chars)")
        return False

    logger.info(f"Uploading {title} to KDP...")

    SESSION_FILE = Path(__file__).parent / "kdp_session.json"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )

        # Load saved session if available (skips OTP)
        if SESSION_FILE.exists():
            logger.info("Loading saved session...")
            context = await browser.new_context(
                storage_state=str(SESSION_FILE),
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            )
        else:
            logger.warning("⚠️  No saved session found. Run kdp_login_setup.py first!")
            await browser.close()
            return False

        page = await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => false})")

        try:
            # Step 1: Go directly to bookshelf (session handles auth)
            logger.info("Opening KDP Bookshelf...")
            await page.goto("https://kdp.amazon.com/en_US/bookshelf", wait_until="domcontentloaded", timeout=60000)

            # Check if session is still valid
            current_url = page.url
            if "signin" in current_url or "/ap/" in current_url:
                logger.warning("⚠️ Session expired — auto re-login...")
                await browser.close()

                # Auto re-login via kdp_login_full.py
                import subprocess as _sp
                login_result = _sp.run(
                    ["python3", str(Path(__file__).parent / "kdp_login_full.py")],
                    capture_output=True, text=True, timeout=120
                )
                if "Session saved" not in login_result.stdout:
                    logger.error(f"❌ Auto re-login failed: {login_result.stdout[-300:]}")
                    await notify("❌ KDP Session Expired — auto re-login failed")
                    return False

                logger.info("✅ Auto re-login successful, resuming upload...")
                # Re-open browser with fresh session
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    storage_state=str(SESSION_FILE),
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                )
                page = await context.new_page()
                await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => false})")
                await page.goto("https://kdp.amazon.com/en_US/bookshelf", wait_until="domcontentloaded", timeout=60000)

                if "signin" in page.url or "/ap/" in page.url:
                    logger.error("❌ Still not logged in after re-login")
                    await browser.close()
                    return False

            logger.info("✅ Session valid")

            # Step 2: Resume existing book OR find draft OR create new title
            existing_book_id = listing.get("kdp_book_id", "")

            if existing_book_id:
                # Previous attempt already created the book on KDP — go to details page first to update SEO
                logger.info(f"Resuming existing book: {existing_book_id}")
                details_url = f"https://kdp.amazon.com/en_US/title-setup/kindle/{existing_book_id}/details"
                await page.goto(details_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3000)

                if "signin" in page.url or "/ap/" in page.url:
                    logger.warning("⚠️ Session expired on Resume eBook — re-logging in...")
                    await browser.close()
                    import subprocess
                    subprocess.run(["python3", "/root/libra/kdp_login_full.py"])
                    browser = await p.chromium.launch(headless=True)
                    context = await browser.new_context(storage_state=str(SESSION_FILE))
                    page = await context.new_page()
                    await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => false})")
                    await page.goto(details_url, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(3000)

                if "bookshelf" in page.url and "title-setup" not in page.url:
                    # Book is in review — previous upload actually succeeded; mark it done
                    logger.info("✅ Book is in KDP review — previous upload succeeded, marking done")
                    listing["status"] = "uploaded"
                    listing["uploaded_at"] = datetime.now().strftime("%Y-%m-%d")
                    listing["kdp_uploading"] = False
                    listing["kdp_error"] = ""
                    listing_file.write_text(json.dumps(listing, ensure_ascii=False, indent=2))
                    await notify(f"✅ <b>Already in KDP Review</b>\n{title}\n\nPrevious upload succeeded.")
                    return True
                logger.info(f"Resumed at details page: {page.url}")
            else:
                # Check for existing draft on bookshelf (title match)
                logger.info("Checking for existing draft matching this book...")
                await page.goto("https://kdp.amazon.com/en_US/bookshelf", wait_until="domcontentloaded", timeout=60000)

                title_key = title[:20].lower()
                matching_draft_url = await page.evaluate(f'''() => {{
                    const key = {json.dumps(title_key)};
                    const links = Array.from(document.querySelectorAll("a"));
                    for (const a of links) {{
                        if (!(a.textContent || "").includes("Continue setup")) continue;
                        let node = a.parentElement;
                        for (let i = 0; i < 12; i++) {{
                            if (!node) break;
                            if (node.textContent.toLowerCase().includes(key)) return a.href;
                            node = node.parentElement;
                        }}
                    }}
                    return null;
                }}''')

                if matching_draft_url:
                    logger.info(f"Found matching draft — resuming: {matching_draft_url}")
                    await page.goto(matching_draft_url, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(3000)
                    logger.info(f"Resumed at: {page.url}")
                else:
                    # Create new title
                    logger.info("Creating new title...")
                    await page.get_by_text("Create new title or series").click()
                    await page.wait_for_timeout(2000)
                    # Select Kindle eBook
                    await page.get_by_text("Create eBook").click()
                    await page.wait_for_timeout(3000)

                    # Amazon sometimes forces re-auth on write actions even with valid session
                    if "signin" in page.url or "/ap/" in page.url:
                        logger.warning("⚠️ Session expired on Create eBook — re-logging in...")
                        await browser.close()
                        import subprocess as _sp2
                        login_result2 = _sp2.run(
                            ["python3", str(Path(__file__).parent / "kdp_login_full.py")],
                            capture_output=True, text=True, timeout=120
                        )
                        if "Session saved" not in login_result2.stdout:
                            logger.error(f"❌ Re-login failed: {login_result2.stdout[-300:]}")
                            await notify("❌ KDP Re-login failed during Create eBook")
                            return False
                        logger.info("✅ Re-login OK — retrying Create eBook...")
                        browser = await p.chromium.launch(headless=True)
                        context = await browser.new_context(
                            storage_state=str(SESSION_FILE),
                            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                        )
                        page = await context.new_page()
                        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => false})")
                        await page.goto("https://kdp.amazon.com/en_US/bookshelf", wait_until="domcontentloaded", timeout=60000)
                        await page.get_by_text("Create new title or series").click()
                        await page.wait_for_timeout(2000)
                        await page.get_by_text("Create eBook").click()
                        await page.wait_for_timeout(2000)

                    await page.wait_for_url("**/title-setup/**", timeout=20000)
                    logger.info("✅ Selected Kindle eBook format")

            # Step 3: Fill Details page (skip if already on content page)
            if "/content" not in page.url:
                logger.info("Filling book details...")
                await page.wait_for_selector('#data-title', timeout=15000)

                # Title
                await page.fill('#data-title', title)

                # Subtitle
                if subtitle:
                    await page.fill('#data-subtitle', subtitle)

                # Author name (split into first/last)
                author_parts = AUTHOR_NAME.strip().split(" ", 1)
                first_name = author_parts[0]
                last_name = author_parts[1] if len(author_parts) > 1 else ""
                await page.fill('#data-primary-author-first-name', first_name)
                await page.fill('#data-primary-author-last-name', last_name)

                # Keywords (up to 7)
                for i, kw in enumerate(keywords[:7]):
                    await page.fill(f'input[name="data[keywords][{i}]"]', kw)

                # Description — Amazon uses CKEditor
                import json as _json
                desc_js = _json.dumps(description)
                await page.evaluate(f'''() => {{
                    const desc = {desc_js};
                    if (typeof CKEDITOR !== "undefined") {{
                        for (const name in CKEDITOR.instances) {{
                            CKEDITOR.instances[name].setData(desc);
                        }}
                    }}
                    const hidden = document.querySelector('input[name="data[description]"]');
                    if (hidden) {{
                        hidden.value = desc;
                        hidden.dispatchEvent(new Event("change", {{bubbles: true}}));
                    }}
                }}''')
                await page.wait_for_timeout(1000)

                # Adult content = No, Publishing rights = non-public domain
                await page.evaluate('''() => {
                    const noPD = document.querySelector("#non-public-domain");
                    if (noPD) { noPD.checked = true; noPD.dispatchEvent(new Event("change",{bubbles:true})); }
                    document.querySelectorAll('input[name="data[is_adult_content]-radio"]').forEach(r => {
                        if (r.value === "false") { r.checked = true; r.dispatchEvent(new Event("change",{bubbles:true})); }
                    });
                }''')
                await page.wait_for_timeout(500)

                # Keywords: Fill all 7 backend keywords from listing.json for SEO
                keywords_list = listing.get("keywords", [])
                if keywords_list:
                    import json as _json
                    for i in range(min(7, len(keywords_list))):
                        kw = keywords_list[i]
                        await page.evaluate(f'''() => {{
                            const el = document.querySelector('input[name="data[keywords][{i}]"]');
                            if (el) {{
                                el.value = {_json.dumps(kw)};
                                el.dispatchEvent(new Event("change", {{bubbles:true}}));
                            }}
                        }}''')
                    logger.info(f"✓ Filled {min(7, len(keywords_list))} SEO keywords")
                else:
                    logger.warning("⚠️ No keywords found in listing.json")

                # Category: open modal, search for AI category, and select relevant checkboxes
                try:
                    cat_btn = await page.query_selector('button:has-text("Choose categories"), button:has-text("Change categories")')
                    if cat_btn:
                        await cat_btn.click()
                        await page.wait_for_timeout(2000)
                        
                        categories_list = listing.get("categories", [])
                        if categories_list:
                            # Try to search for the category if KDP search box is present
                            search_input = page.locator('input[type="search"], input[placeholder*="Search"]')
                            if await search_input.count() > 0:
                                await search_input.first.fill(categories_list[0])
                                await page.keyboard.press("Enter")
                                await page.wait_for_timeout(2000)
                            else:
                                # Fallback: try to select 'Nonfiction' or first option
                                try:
                                    await page.select_option('select', label="Non-Fiction", force=True)
                                    await page.wait_for_timeout(1000)
                                except:
                                    try:
                                        await page.select_option('select', index=1, force=True)
                                        await page.wait_for_timeout(1000)
                                    except:
                                        pass

                        # Click up to 2 visible checkboxes
                        checkboxes = page.locator('input[type="checkbox"]:visible')
                        count = await checkboxes.count()
                        clicked = 0
                        for i in range(count):
                            if clicked >= 2: break
                            # Don't uncheck if already checked
                            is_checked = await checkboxes.nth(i).is_checked()
                            if not is_checked:
                                await checkboxes.nth(i).click(force=True)
                                clicked += 1
                        
                        logger.info(f"✓ Selected {clicked} categories")
                        await page.wait_for_timeout(500)
                        
                        save_cat = await page.query_selector('button:has-text("Save categories")')
                        if save_cat:
                            await save_cat.click()
                            await page.wait_for_timeout(1000)
                    else:
                        logger.info("✓ Category already set")
                except Exception as e:
                    logger.warning(f"⚠️ Category step failed: {e}")

                # Language dropdown (Amazon uses custom JS dropdown)
                lang_map_kdp = {"English": "english", "German": "german", "Spanish": "spanish", "French": "french", "Italian": "italian", "Portuguese": "portuguese"}
                lang_val = lang_map_kdp.get(language, "english")
                await page.evaluate(f'''() => {{
                    const sel = document.querySelector('select[name="data[language]"]');
                    if (sel) {{
                        sel.value = "{lang_val}";
                        sel.dispatchEvent(new Event("change", {{ bubbles: true }}));
                    }}
                }}''')

                logger.info("✅ Book details filled")

                # Save & continue to content page
                await page.get_by_text("Save and Continue", exact=False).first.click()
                await page.wait_for_timeout(3000)

                await page.screenshot(path="/tmp/kdp_after_details_save.png")
                logger.info(f"After details save URL: {page.url}")
                logger.info("✅ Details page saved")

                # Wait for content page navigation
                await page.wait_for_url("**/content**", timeout=30000)

                # Save kdp_book_id immediately so retry can resume instead of creating a duplicate
                import re as _re
                _m = _re.search(r'/kindle/([A-Z0-9]+)/', page.url)
                if _m:
                    _book_id = _m.group(1)
                    _lst = json.loads(listing_file.read_text())
                    _lst["kdp_book_id"] = _book_id
                    listing_file.write_text(json.dumps(_lst, ensure_ascii=False, indent=2))
                    logger.info(f"✓ Saved kdp_book_id: {_book_id}")

            logger.info(f"Content page: {page.url}")

            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(3000)

            # Check if EPUB is already uploaded (from previous run)
            epub_status = await page.evaluate('''() => {
                const el = document.getElementById("data-assets-interior-asset-status");
                return el ? el.value : "";
            }''')
            if "SUCCESS" in epub_status and not existing_book_id:
                logger.info("✅ EPUB already uploaded, skipping re-upload")
            else:
                logger.info("Uploading EPUB...")
                epub_input = await page.query_selector('#data-assets-interior-file-upload-AjaxInput')
                if not epub_input:
                    raise Exception("EPUB file input not found")
                await epub_input.set_input_files(str(epub_path))
                logger.info("✅ EPUB uploading... waiting for processing...")
                try:
                    await page.wait_for_selector('#data-assets-interior-asset-status[value*="SUCCESS"]', timeout=120000)
                    logger.info("✅ EPUB processing complete")
                except:
                    logger.warning("⚠️  Could not confirm EPUB processing, waiting 30s...")
                    await page.wait_for_timeout(30000)

            # Check if cover is already uploaded
            cover_status = await page.evaluate('''() => {
                const el = document.getElementById("data-assets-cover-asset-status");
                return el ? el.value : "";
            }''')
            if "SUCCESS" in cover_status and not existing_book_id:
                logger.info("✅ Cover already uploaded, skipping re-upload")
            else:
                logger.info("Uploading cover...")
                # Switch to "Upload a cover you already have" tab
                try:
                    upload_tab = await page.query_selector('a:has-text("Upload a cover you already have")')
                    if upload_tab:
                        await upload_tab.click()
                        await page.wait_for_timeout(1500)
                        logger.info("✓ Switched to cover file upload tab")
                except Exception as e:
                    logger.warning(f"Could not switch cover tab: {e}")

                cover_input = await page.query_selector('#data-assets-cover-file-upload-AjaxInput')
                if not cover_input:
                    # Try jp variant
                    cover_input = await page.query_selector('#data-assets-cover-jp-file-upload-AjaxInput')
                if cover_input:
                    await cover_input.set_input_files(str(cover_path))
                    await page.wait_for_timeout(10000)
                    logger.info("✅ Cover uploaded")
                else:
                    logger.warning("⚠️ Cover file input not found, skipping")

            # DRM selection (enable DRM)
            drm_radio = await page.query_selector('input[name="data[is_drm]-radio"][value="true"]')
            if drm_radio:
                await drm_radio.scroll_into_view_if_needed()
                await page.wait_for_timeout(300)
                await drm_radio.click()
                await page.wait_for_timeout(500)
                # Verify it's actually checked
                is_checked = await drm_radio.is_checked()
                if not is_checked:
                    # Fallback: JS click
                    await page.evaluate('(el) => el.click()', drm_radio)
                    await page.wait_for_timeout(500)
                    is_checked = await drm_radio.is_checked()
                logger.info(f"✓ DRM selected (checked={is_checked})")
                if not is_checked:
                    logger.warning("⚠️ DRM radio click did not register — KDP may block Save and Continue")

            try:
                await set_ai_disclosure(page)
            except Exception as e:
                raise RuntimeError(f"AI disclosure failed; upload blocked: {e}") from e
            logger.info("✓ AI tools done")
            await page.wait_for_timeout(1000)

            # Confirm accuracy checkbox — Amazon renders as div[role="checkbox"] (React).
            # Must trigger via React fiber onClick; plain Playwright click changes aria-checked
            # visually but does NOT update React state, causing "check the box" error on save.
            try:
                n = await page.evaluate("""() => {
                    const cbs = document.querySelectorAll('div[role="checkbox"]');
                    let clicked = 0;
                    for (const cb of cbs) {
                        if (cb.getAttribute('aria-checked') === 'true') continue;
                        const txt = (cb.closest('label') || cb.parentElement || cb).textContent || '';
                        if (!txt.toLowerCase().includes('confirm')) continue;
                        const key = Object.keys(cb).find(k => k.startsWith('__reactFiber'));
                        if (key) {
                            let fiber = cb[key];
                            while (fiber) {
                                const props = fiber.memoizedProps || fiber.pendingProps || {};
                                if (props.onClick) {
                                    props.onClick({type:'click', target:cb, currentTarget:cb,
                                        stopPropagation:()=>{}, preventDefault:()=>{}});
                                    clicked++; break;
                                }
                                fiber = fiber.return;
                            }
                        }
                    }
                    return clicked;
                }""")
                await page.wait_for_timeout(500)
                logger.info(f"✓ Accessibility confirm checkbox: clicked {n} via React fiber")
            except Exception as e:
                logger.warning(f"⚠️ Confirm checkbox failed: {e}")

            logger.info("✅ Content page fields filled")

            # Save & continue to pricing page
            await page.get_by_text("Save and Continue", exact=False).first.click()
            await page.wait_for_timeout(5000)
            await page.screenshot(path="/tmp/kdp_after_content_save.png")
            logger.info(f"After content save URL: {page.url}")
            logger.info("✅ Content page saved")

            # Step 6: Pricing page — smart wait with progress checking
            # KDP "Preparing your files" can take 1-10 minutes
            # After dialog closes, KDP stays on content page — must click "Save and Continue" again
            max_wait = 900  # 15 minutes max
            waited = 0
            was_preparing = False
            while waited < max_wait:
                if "pricing" in page.url:
                    break

                try:
                    # Check if "Preparing your files" dialog is showing
                    preparing = await page.query_selector('text="Preparing your files"')
                    if preparing:
                        if waited % 30 == 0:
                            logger.info(f"⏳ KDP processing files... ({waited}s elapsed)")
                        was_preparing = True
                        await page.wait_for_timeout(10000)
                        waited += 10
                        continue

                    # Check for error messages (including DRM validation error)
                    error_el = await page.query_selector('.a-alert-error, [data-alert-type="error"], .error-message, .a-box-inner .a-alert-content')
                    if error_el:
                        error_text = await error_el.inner_text()
                        logger.error(f"❌ KDP error during processing: {error_text[:200]}")
                        await page.screenshot(path="/tmp/kdp_processing_error.png")
                        # If DRM error, try to fix and retry Save and Continue once
                        if "Digital Rights Management" in error_text or "DRM" in error_text:
                            logger.info("🔧 Detected DRM error — re-selecting DRM and retrying...")
                            drm_fix = await page.query_selector('input[name="data[is_drm]-radio"][value="true"]')
                            if drm_fix:
                                await drm_fix.scroll_into_view_if_needed()
                                await page.evaluate('(el) => el.click()', drm_fix)
                                await page.wait_for_timeout(1000)
                                save_btn_fix = await page.query_selector('button:has-text("Save and Continue")')
                                if save_btn_fix:
                                    await save_btn_fix.click()
                                    await page.wait_for_timeout(5000)
                                    continue
                        raise Exception(f"KDP processing error: {error_text[:200]}")

                    # Dialog gone — KDP doesn't auto-navigate, must click "Save and Continue" again
                    if was_preparing or "content" in page.url:
                        save_btn = await page.query_selector('button:has-text("Save and Continue"), input[value*="Save and Continue"]')
                        if save_btn:
                            is_disabled = await save_btn.get_attribute("disabled")
                            if not is_disabled:
                                logger.info("⏩ Dialog gone — clicking Save and Continue again...")
                                await save_btn.click()
                                was_preparing = False
                                await page.wait_for_timeout(5000)
                                continue

                    # Still on content page, no dialog, no button — just wait
                    await page.wait_for_timeout(10000)
                    waited += 10

                except Exception as _nav_err:
                    # KDP navigated to pricing mid-query — check URL before raising
                    if "pricing" in page.url:
                        logger.info("✅ KDP navigated to pricing (caught during query)")
                        break
                    err_str = str(_nav_err)
                    if "Execution context was destroyed" in err_str or "Most likely because of a navigation" in err_str or "most likely because of a navigation" in err_str:
                        logger.info(f"⚡ Navigation detected mid-loop, retrying... URL={page.url}")
                        await page.wait_for_timeout(2000)
                        continue
                    raise

            # Check if Amazon intercepted with a re-authentication prompt
            if "ap/signin" in page.url:
                logger.info("⚠️ Intercepted re-authentication prompt! Attempting to login...")
                try:
                    password_input = page.locator('input[type="password"]')
                    if await password_input.count() > 0 and await password_input.is_visible():
                        await password_input.fill(KDP_PASSWORD)
                        logger.info("Filled password")
                        await page.locator('input[type="submit"], button[type="submit"], #signInSubmit').first.click()
                        await page.wait_for_load_state("domcontentloaded")
                        await page.wait_for_timeout(5000)
                except Exception as e:
                    logger.warning(f"Failed to handle re-auth: {e}")

            if "pricing" not in page.url:
                await page.screenshot(path="/tmp/kdp_pricing_timeout.png")
                raise Exception(f"Timeout waiting for pricing page ({max_wait}s). URL: {page.url}")

            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(2000)
            logger.info(f"Pricing page: {page.url}")

            await page.screenshot(path="/tmp/kdp_pricing_before.png")

            # Select KDP pricing / royalty — 70% royalty option if available
            try:
                royalty_70 = page.locator('input[type="radio"][id*="70"], input[type="radio"][value*="70"]')
                if await royalty_70.count() > 0:
                    await royalty_70.first.click(force=True)
                    logger.info("✓ Selected 70% royalty")
                    await page.wait_for_timeout(1000)
            except Exception as e:
                logger.warning(f"Royalty selection: {e}")

            # Select "All territories" (worldwide rights)
            try:
                all_terr = page.locator('input[type="radio"]').filter(has_text="All territories")
                if await all_terr.count() == 0:
                    # Try by label
                    all_terr = page.locator('label:has-text("All territories") input[type="radio"]')
                if await all_terr.count() > 0:
                    await all_terr.first.click(force=True)
                    logger.info("✓ Selected all territories")
                else:
                    # Fallback: click first radio in territories section
                    await page.evaluate('''() => {
                        const radios = document.querySelectorAll('input[type="radio"]');
                        for (const r of radios) {
                            const label = r.closest('label') || r.parentElement;
                            if (label && label.textContent.toLowerCase().includes('all territories')) {
                                r.click();
                                return;
                            }
                        }
                    }''')
                    logger.info("✓ Territories (fallback)")
            except Exception as e:
                logger.warning(f"Territories: {e}")
            await page.wait_for_timeout(1000)

            # Set primary marketplace price using Playwright fill (not JS)
            logger.info("Setting price...")
            price_set = False
            # Try common price input selectors
            for sel in ['input[name*="[US][list_price]"]', 'input[name*="[US][price"]',
                        'input[name*="list_price"][name*="US"]',
                        '#data-pricing-print-us-702-702-702 input']:
                try:
                    price_input = page.locator(sel).first
                    if await price_input.count() > 0 and await price_input.is_visible():
                        await price_input.fill("2.99")
                        await price_input.press("Tab")  # trigger blur/change
                        price_set = True
                        logger.info(f"✓ Price set via {sel}")
                        break
                except Exception:
                    continue

            if not price_set:
                # Find any visible price input
                price_inputs = await page.query_selector_all('input[type="text"]')
                for inp in price_inputs:
                    try:
                        name = await inp.get_attribute('name') or ''
                        if 'price' in name.lower() or 'list_price' in name.lower():
                            if await inp.is_visible():
                                await inp.fill("2.99")
                                await inp.evaluate('el => el.dispatchEvent(new Event("blur",{bubbles:true}))')
                                price_set = True
                                logger.info(f"✓ Price set via name={name}")
                                break
                    except Exception:
                        continue

            if not price_set:
                logger.warning("⚠️ Could not find price input")
                # Dump all inputs for debug
                inputs_info = await page.evaluate('''() => {
                    return Array.from(document.querySelectorAll('input')).slice(0, 30).map(i =>
                        `${i.type} name=${i.name} id=${i.id} vis=${i.offsetParent!==null}`
                    ).join('\\n');
                }''')
                logger.info(f"Inputs:\n{inputs_info}")

            await page.wait_for_timeout(3000)
            await page.screenshot(path="/tmp/kdp_pricing_after_price.png")

            # Step 7: Publish
            logger.info("Publishing...")
            publish_btn = page.locator('button:has-text("Publish"), input[type="submit"]:has-text("Publish")')
            if await publish_btn.count() > 0:
                await publish_btn.first.scroll_into_view_if_needed()
                await page.wait_for_timeout(500)
                await page.screenshot(path="/tmp/kdp_before_publish.png")
                await publish_btn.first.click()
                logger.info("✓ Clicked publish button")
            else:
                logger.warning("⚠️ Publish button not found")
                btns = await page.evaluate('''() =>
                    Array.from(document.querySelectorAll('button')).map(b =>
                        b.textContent.trim().slice(0,60) + ' disabled=' + b.disabled
                    ).join('\\n')
                ''')
                logger.info(f"Buttons:\n{btns}")

            await page.wait_for_timeout(10000)
            await page.screenshot(path="/tmp/kdp_after_publish.png")

            # Check success
            final_url = page.url
            success = "bookshelf" in final_url or "in_review" in final_url
            if success:
                logger.info("✅ Published successfully!")

                # Update status in listing.json
                listing_file = book_dir / "listing.json"
                listing = json.loads(listing_file.read_text())
                listing["status"] = "uploaded"
                listing["uploaded_at"] = datetime.now().strftime("%Y-%m-%d")
                listing["kdp_uploading"] = False
                listing["kdp_error"] = ""
                listing_file.write_text(json.dumps(listing, ensure_ascii=False, indent=2))

                # Send success notification
                title = listing.get("title", slug)
                msg = f"🎉 <b>Published on KDP!</b>\n{title}\n\nYour book is now available on Kindle Store."
                await notify(msg)

                return True
            else:
                logger.warning("⚠️ Publish may have succeeded, please verify on KDP")

                # Never claim success unless KDP confirms it.
                listing_file = book_dir / "listing.json"
                listing = json.loads(listing_file.read_text())
                listing["status"] = "needs_verification"
                listing["kdp_uploading"] = False
                listing["kdp_error"] = f"Publish result not confirmed; final URL: {final_url}"[:300]
                listing_file.write_text(json.dumps(listing, ensure_ascii=False, indent=2))

                title = listing.get("title", slug)
                msg = f"⚠️ <b>KDP Upload Complete (verify needed)</b>\n{title}\n\nPlease check your KDP account to verify the book was published."
                await notify(msg)

                return False

        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error(f"❌ Upload failed: {e}")

            # Mark as failed
            listing_file = book_dir / "listing.json"
            if listing_file.exists():
                listing = json.loads(listing_file.read_text())
                listing["kdp_uploading"] = False
                listing["kdp_error"] = str(e)[:100]
                listing_file.write_text(json.dumps(listing, ensure_ascii=False, indent=2))

                title = listing.get("title", slug)
                msg = f"❌ <b>KDP Upload Failed</b>\n{title}\n\nError: {str(e)[:100]}"
                await notify(msg)

            return False

        finally:
            await browser.close()


async def update_ebook_content(slug: str) -> bool:
    """
    Update the interior EPUB file of an already-published KDP ebook.
    Finds the book on the bookshelf by title, navigates to its content
    editing page, re-uploads the EPUB, and saves.
    """
    if not require_quality_gate(slug):
        return False
    book_dir = KDP_DIR / slug
    listing_file = book_dir / "listing.json"
    if not listing_file.exists():
        logger.error(f"No listing.json for {slug}")
        return False

    data = json.loads(listing_file.read_text())
    title = data.get("title", slug)
    epub_path = book_dir / "ebook.epub"

    if not epub_path.exists():
        logger.error(f"No ebook.epub for {slug}")
        return False

    logger.info(f"=== UPDATE EBOOK CONTENT: {title} ===")

    async with async_playwright() as p:
        if not SESSION_FILE.exists():
            logger.error("No session file — run kdp_login_full.py first")
            return False

        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            storage_state=str(SESSION_FILE),
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        )
        page = await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => false})")

        try:
            # ── Step 1: Go to bookshelf ──────────────────────────────────────
            await page.goto("https://kdp.amazon.com/en_US/bookshelf", wait_until="domcontentloaded", timeout=60000)

            if "signin" in page.url or "/ap/" in page.url:
                logger.warning("Session expired — re-logging in...")
                await browser.close()
                import subprocess as _sp
                _sp.run(["python3", str(Path(__file__).parent / "kdp_login_full.py")],
                        capture_output=True, text=True, timeout=120)
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    storage_state=str(SESSION_FILE),
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                )
                page = await context.new_page()
                await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => false})")
                await page.goto("https://kdp.amazon.com/en_US/bookshelf", wait_until="domcontentloaded", timeout=60000)

            # ── Step 2: Navigate directly to this book's content edit page ────
            # Use the stored kdp_book_id if available (most reliable).
            # DO NOT scrape the bookshelf — multiple books on the page make it
            # easy to click the wrong "Manage title" button.
            book_id = data.get("kdp_book_id")
            if book_id:
                logger.info(f"Using stored book ID: {book_id}")
            else:
                logger.error("No kdp_book_id in listing.json — cannot safely determine which book to update.")
                logger.error("Run the bookshelf scraper manually or set kdp_book_id in listing.json.")
                return False

            full_url = f"https://kdp.amazon.com/en_US/title-setup/kindle/{book_id}/content"
            logger.info(f"Navigating to content page: {full_url}")

            # ── Step 3: Navigate to content editing page ──────────────────────
            # Use domcontentloaded (faster than networkidle) with extended timeout
            await page.goto(full_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)

            if "signin" in page.url or "/ap/" in page.url:
                logger.warning("Re-auth required on content edit page — re-logging in...")
                await browser.close()
                import subprocess as _sp3
                login_r = _sp3.run(
                    ["python3", str(Path(__file__).parent / "kdp_login_full.py")],
                    capture_output=True, text=True, timeout=120
                )
                if "Session saved" not in login_r.stdout:
                    logger.error(f"Re-login failed: {login_r.stdout[-200:]}")
                    return False
                logger.info("Re-login OK — retrying content page...")
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    storage_state=str(SESSION_FILE),
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                )
                page = await context.new_page()
                await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => false})")
                await page.goto(full_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3000)
                if "signin" in page.url or "/ap/" in page.url:
                    logger.error("Still on signin page after re-login")
                    return False

            logger.info(f"On content page: {page.url}")

            # KDP redirects to bookshelf when book is still "In review" — not editable yet
            if "bookshelf" in page.url and "title-setup" not in page.url:
                logger.warning("⏳ Redirected to bookshelf — book still In Review, cannot edit yet")
                return False

            # ── Step 4: Upload new EPUB ───────────────────────────────────────
            logger.info("Uploading new EPUB...")
            epub_input = await page.query_selector('#data-assets-interior-file-upload-AjaxInput')
            if not epub_input:
                await page.screenshot(path="/tmp/kdp_update_content.png")
                raise Exception("EPUB file input not found on content page")

            await epub_input.set_input_files(str(epub_path))
            logger.info("EPUB upload started — waiting for processing...")

            try:
                await page.wait_for_selector(
                    '#data-assets-interior-asset-status[value*="SUCCESS"]',
                    timeout=180000
                )
                logger.info("✅ EPUB processed successfully")
            except Exception:
                logger.warning("Could not confirm EPUB processing — waiting 60s...")
                await page.wait_for_timeout(60000)

            # ── Step 5: Accessibility confirm checkbox (React fiber) ──────────
            try:
                n = await page.evaluate("""() => {
                    const cbs = document.querySelectorAll('div[role="checkbox"]');
                    let clicked = 0;
                    for (const cb of cbs) {
                        if (cb.getAttribute('aria-checked') === 'true') continue;
                        const txt = (cb.closest('label') || cb.parentElement || cb).textContent || '';
                        if (!txt.toLowerCase().includes('confirm')) continue;
                        const key = Object.keys(cb).find(k => k.startsWith('__reactFiber'));
                        if (key) {
                            let fiber = cb[key];
                            while (fiber) {
                                const props = fiber.memoizedProps || fiber.pendingProps || {};
                                if (props.onClick) {
                                    props.onClick({type:'click', target:cb, currentTarget:cb,
                                        stopPropagation:()=>{}, preventDefault:()=>{}});
                                    clicked++; break;
                                }
                                fiber = fiber.return;
                            }
                        }
                    }
                    return clicked;
                }""")
                await page.wait_for_timeout(500)
                if n:
                    logger.info(f"✓ Accessibility confirm: clicked {n} checkbox(es) via React fiber")
            except Exception as e:
                logger.warning(f"⚠️ Confirm checkbox: {e}")

            await set_ai_disclosure(page)

            # ── Step 6: Save ──────────────────────────────────────────────────
            logger.info("Saving...")
            waited = 0
            content_saved = False
            while waited < 900:
                save_btn = await page.query_selector(
                    'button:has-text("Save and Continue"), input[value*="Save and Continue"]'
                )
                if save_btn:
                    disabled = await save_btn.get_attribute("disabled")
                    if not disabled:
                        await save_btn.click()
                        logger.info("✓ Clicked Save and Continue")
                        await page.wait_for_timeout(8000)
                        content_saved = True
                        break
                await page.wait_for_timeout(10000)
                waited += 10

            # If we land on pricing page, just save without changing price
            republished = False
            if "pricing" in page.url:
                logger.info("On pricing page — saving current pricing...")
                save_price = await page.query_selector(
                    'button:has-text("Save and Publish"), button:has-text("Publish")'
                )
                if save_price:
                    await save_price.click()
                    await page.wait_for_timeout(10000)
                    logger.info("✓ Re-published with updated content")
                    republished = True

            if not content_saved or not republished:
                raise RuntimeError(
                    f"KDP update was not confirmed (content_saved={content_saved}, republished={republished}, "
                    f"url={page.url})"
                )

            # Update listing status
            data["kdp_uploading"] = False
            data["content_updated_at"] = datetime.now().strftime("%Y-%m-%d")
            data["status"] = "uploaded"
            data["kdp_error"] = ""
            listing_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

            await notify(f"✅ <b>KDP Content Updated</b>\n{title}\n\nEPUB re-uploaded with layout fixes.")
            logger.info("✅ Update complete!")
            return True

        except Exception as e:
            logger.error(f"❌ Update failed: {e}")
            await notify(f"❌ <b>KDP Update Failed</b>\n{title}\n\nError: {str(e)[:100]}")
            return False
        finally:
            await browser.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 kdp_upload.py <slug> [--update]")
        sys.exit(1)

    slug = sys.argv[1]
    update_mode = "--update" in sys.argv

    if update_mode:
        result = asyncio.run(update_ebook_content(slug))
    else:
        result = asyncio.run(upload_to_kdp(slug))
    sys.exit(0 if result else 1)
