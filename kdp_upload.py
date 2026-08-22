#!/usr/bin/env python3
"""
KDP Upload Automation — Uploads ebook to Amazon KDP
Usage: python3 kdp_upload.py <slug>
"""

import asyncio
import json
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

from kdp_categories import set_categories
from kdp_freeze import KDPFrozenError, assert_kdp_mutation_allowed
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
TITLE_LIMIT_STATE_FILE = Path(__file__).parent / "data" / "kdp-title-limit.json"


class TitleCreationLimitError(RuntimeError):
    """KDP temporarily refuses new titles for this account."""


def is_title_creation_limit(page_text: str) -> bool:
    normalized = " ".join((page_text or "").lower().split())
    return (
        "title creation limit exceeded" in normalized
        or "number of books that can be submitted for publishing has been exceeded" in normalized
    )


def record_title_creation_limit(slug: str, message: str) -> str:
    """Pause new-title work for 24 hours and return the retry timestamp."""
    retry_after = datetime.now() + timedelta(hours=24)
    TITLE_LIMIT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    TITLE_LIMIT_STATE_FILE.write_text(
        json.dumps(
            {
                "active": True,
                "slug": slug,
                "detected_at": datetime.now().isoformat(timespec="seconds"),
                "retry_after": retry_after.isoformat(timespec="seconds"),
                "reason": message,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return retry_after.isoformat(timespec="minutes")

PUBLISH_CONFIRMATION_PHRASES = (
    "successfully submitted",
    "submitted for review",
    "your book has been submitted",
    "your changes have been submitted",
)


def is_kdp_publish_confirmed(url: str, page_text: str) -> bool:
    """Return True only for explicit KDP publish confirmation."""
    normalized_url = (url or "").lower()
    normalized_text = " ".join((page_text or "").lower().split())
    if "/bookshelf" in normalized_url:
        return True
    return any(phrase in normalized_text for phrase in PUBLISH_CONFIRMATION_PHRASES)


async def wait_for_publish_confirmation(page, timeout_ms: int = 120000) -> bool:
    """Poll KDP until the publish action has explicit confirmation."""
    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
    while asyncio.get_running_loop().time() < deadline:
        try:
            page_text = await page.locator("body").inner_text(timeout=5000)
        except Exception:
            page_text = ""
        if is_kdp_publish_confirmed(page.url, page_text):
            return True
        await page.wait_for_timeout(2000)
    return False


async def wait_for_pricing_page(page, timeout_seconds: int = 900) -> bool:
    """Wait for KDP file preparation and confirm navigation to pricing."""
    waited = 0
    while waited < timeout_seconds:
        if "/pricing" in page.url:
            return True
        try:
            error_el = await page.query_selector(
                '.a-alert-error, [data-alert-type="error"], .error-message'
            )
            if error_el:
                error_text = (await error_el.inner_text()).strip()
                if error_text:
                    raise RuntimeError(f"KDP content processing error: {error_text[:300]}")

            body_txt = (await page.evaluate("() => document.body.innerText || ''")) or ""
            preparing = "reparing" in body_txt and "file" in body_txt.lower()
            if preparing:
                if waited % 30 == 0:
                    logger.info("KDP is preparing files (%ss elapsed)", waited)
            elif "/content" in page.url:
                save_btn = await page.query_selector(
                    'button:has-text("Save and Continue"), input[value*="Save and Continue"]'
                )
                if save_btn and await save_btn.get_attribute("disabled") is None:
                    logger.info("File preparation dialog cleared; retrying Save and Continue")
                    # Short timeout + swallow: if an overlay still covers the button
                    # we keep looping rather than aborting the whole wait on a 30s click timeout.
                    try:
                        await save_btn.click(timeout=5000)
                    except Exception:
                        pass
            await page.wait_for_timeout(10000)
            waited += 10
        except RuntimeError:
            raise
        except Exception as exc:
            if "/pricing" in page.url:
                return True
            if "navigation" not in str(exc).lower():
                raise
            await page.wait_for_timeout(2000)
    return "/pricing" in page.url


async def inspect_bookshelf_title(slug: str) -> list[dict]:
    """Find matching KDP bookshelf entries without modifying account data."""
    book_dir = KDP_DIR / slug
    listing_file = book_dir / "listing.json"
    if not listing_file.exists() or not SESSION_FILE.exists():
        logger.error("Bookshelf inspection requires listing.json and a KDP session")
        return []
    listing = json.loads(listing_file.read_text(encoding="utf-8"))
    title = str(listing.get("title", "")).strip()
    if not title:
        logger.error("Bookshelf inspection requires a title")
        return []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(storage_state=str(SESSION_FILE))
            page = await context.new_page()
            await page.goto(
                "https://kdp.amazon.com/en_US/bookshelf",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            if "signin" in page.url or "/ap/" in page.url:
                logger.error("KDP session expired; bookshelf inspection stopped")
                return []
            matches = await page.evaluate(
                """(targetTitle) => {
                    const normalize = value => (value || "")
                        .toLowerCase().replace(/\\s+/g, " ").trim();
                    const target = normalize(targetTitle);
                    const shortTarget = target.slice(0, 24);
                    const results = [];
                    const seen = new Set();
                    for (const link of document.querySelectorAll("a[href]")) {
                        const href = link.href || "";
                        const idMatch = href.match(/\\/kindle\\/([A-Z0-9]+)\\//);
                        let node = link;
                        for (let i = 0; i < 10 && node; i++, node = node.parentElement) {
                            const text = normalize(node.innerText);
                            if (!text.includes(target) && !text.includes(shortTarget)) continue;
                            const key = `${idMatch ? idMatch[1] : ""}|${href}`;
                            if (seen.has(key)) break;
                            seen.add(key);
                            results.push({
                                book_id: idMatch ? idMatch[1] : null,
                                href,
                                text: (node.innerText || "").trim().slice(0, 500),
                            });
                            break;
                        }
                    }
                    return results;
                }""",
                title,
            )
            logger.info("Bookshelf inspection found %s matching entries for %s", len(matches), title)
            return matches
        finally:
            await browser.close()


def preflight_update(slug: str) -> bool:
    """Check update readiness without opening a browser or writing to KDP."""
    if not require_quality_gate(slug):
        return False

    book_dir = KDP_DIR / slug
    listing_file = book_dir / "listing.json"
    epub_file = book_dir / "ebook.epub"
    if not listing_file.exists():
        logger.error("Preflight failed: listing.json is missing")
        return False
    try:
        listing = json.loads(listing_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Preflight failed: invalid listing.json: %s", exc)
        return False
    if not listing.get("kdp_book_id"):
        logger.error("Preflight failed: kdp_book_id is missing")
        return False
    if not epub_file.exists() or epub_file.stat().st_size < 5_000:
        logger.error("Preflight failed: ebook.epub is missing or invalid")
        return False
    if not SESSION_FILE.exists() or SESSION_FILE.stat().st_size == 0:
        logger.error("Preflight failed: KDP session file is missing")
        return False

    logger.info(
        "Preflight passed for %s (book_id=%s). No browser opened and no KDP write made.",
        slug,
        listing["kdp_book_id"],
    )
    return True


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


async def set_reading_age(page, min_age, max_age, logger=None) -> bool:
    """Set the KDP "Reading interest age" range (juvenile / young-adult titles).

    Amazon locks these dropdowns whenever the title is flagged as adult content,
    and an old upload may carry a stale 18+/adult flag that keeps the locked,
    2-option placeholder selects in place. So we first force the "Sexually
    Explicit … = No" radio, which clears the lock and reveals the real
    Baby–17 selects, then drive them via select_option (same React-native trick
    the category cascade uses). Returns True if both values stuck.
    """
    def _log(m):
        (logger.info if logger else print)(m)
    # Clear any stale adult-content lock so the age selects unlock.
    try:
        await page.evaluate("""() => {
            const r = document.querySelector('input[name="data[is_adult_content]-radio"][value="false"]');
            if (r) r.click();
        }""")
        await page.wait_for_timeout(1200)
    except Exception as e:
        _log(f"  reading-age: could not toggle adult-content radio: {e}")
    ok = True
    for which, val in (("start", str(min_age)), ("end", str(max_age))):
        sel = f'select#data-reading-interest-age-{which}-input-native:not([disabled])'
        try:
            await page.select_option(sel, value=val, timeout=5000)
            got = await page.eval_on_selector(sel, "el => el.value")
            if got != val:
                ok = False
                _log(f"  reading-age {which}: wanted {val}, got {got!r}")
        except Exception as e:
            ok = False
            _log(f"  reading-age {which}: {str(e)[:100]}")
    if ok:
        _log(f"✓ Reading interest age set: {min_age}-{max_age}")
    return ok


def juvenile_reading_age(categories):
    """Derive a KDP reading-interest-age {min,max} from a book's category paths,
    or None for adult titles (which must leave the age range blank).

    KDP flags juvenile titles ("Reading Interest Age is missing") whenever a book
    sits in Children's eBooks or Teen & Young Adult but has no age range. The
    pipeline picks categories but never set an age, so every kids/teen book got
    flagged. We infer a sensible range from the categories themselves: an explicit
    "Ages 6-8" bucket if present, else a broad default per juvenile band."""
    joined = " | ".join(categories or []).lower()
    m = re.search(r"ages?\s*(\d+)\s*[-–]\s*(\d+)", joined)
    if m:
        return {"min": int(m.group(1)), "max": int(m.group(2))}
    if "baby-2" in joined:
        return {"min": 0, "max": 2}
    if "young adult" in joined or "teen & young adult" in joined:
        return {"min": 13, "max": 17}
    if "children's ebooks" in joined or "children's books" in joined or "childrens" in joined:
        return {"min": 3, "max": 8}
    return None


async def set_ai_disclosure(page, require_selections: bool = False) -> None:
    """Set mandatory KDP AI disclosure for GPT text and AI-generated cover art.

    On update flows, Amazon may show a reduced option set (e.g. no 'Entire work'
    for text if the book was already disclosed differently). We try the preferred
    option first, fall back to 'Some content', and skip gracefully if the
    accordion is absent (already set on an existing title).
    """
    ai_accordion = page.locator('[data-a-accordion-name="generative-ai-questionnaire-accordion"]')
    # If the accordion doesn't exist on this page (e.g. KDP skips it on updates), skip.
    if not await ai_accordion.is_visible():
        if require_selections:
            raise RuntimeError("AI disclosure questionnaire is not visible")
        logger.info("AI disclosure accordion not present — skipping (likely pre-set on existing title)")
        return
    yes_row = ai_accordion.locator('[data-a-accordion-row-name="yes"] .a-accordion-row')
    await yes_row.click()
    await page.wait_for_timeout(800)
    # text: prefer "Entire work", fall back to "Some content" if unavailable on update
    selections = {
        "generative-ai-questionnaire-text": ("ENTIRE_AND_MINIMAL", "GPT-4.1"),
        "generative-ai-questionnaire-images": ("FEW_AND_MINIMAL", "gpt-image-1"),
        "generative-ai-questionnaire-translations": ("NONE", None),
    }
    for selector_id, (target_value, tool_name) in selections.items():
        container = page.locator(".a-dropdown-container").filter(has=page.locator(f"#{selector_id}"))
        if not await container.is_visible():
            if require_selections:
                raise RuntimeError(f"Required AI disclosure dropdown is unavailable: {selector_id}")
            logger.info(f"  AI {selector_id}: dropdown not visible — skipping")
            continue
        select = page.locator(f"#{selector_id}")
        try:
            selected = await select.select_option(value=target_value)
            await page.wait_for_timeout(300)
            current_value = await select.input_value()
            clicked = target_value in selected and current_value == target_value
        except Exception:
            clicked = False
            current_value = ""
        if clicked:
            selected_text = await select.locator(f'option[value="{target_value}"]').inner_text()
            logger.info(f"  AI {selector_id}: set to '{selected_text}'")
        else:
            if require_selections:
                raise RuntimeError(f"Required AI disclosure option is unavailable: {selector_id}")
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


def _get_book_price(slug: str) -> str:
    """Return recommended price string from pricing-recommendation.json, else '2.99'."""
    try:
        rec = json.loads((KDP_DIR / slug / "pricing-recommendation.json").read_text())
        price = float(rec.get("recommended_price_usd", 2.99))
        price = max(2.99, min(9.99, price))
        return f"{price:.2f}"
    except Exception:
        return "2.99"


async def upload_to_kdp(slug: str):
    """Upload ebook to KDP"""
    assert_kdp_mutation_allowed("new_title", slug)
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
    # Prefer the whitelisted-HTML blurb (hook/bullets/CTA) when present; the
    # KDP CKEditor renders HTML. Fall back to the plain description.
    description = listing.get("description_html") or listing.get("description", "")
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

                # Category: drive the 2026 React cascade modal, fuzzy-matching the
                # book's intended category paths against Amazon's real category tree
                # (see kdp_categories.set_categories). Replaces the old "click the
                # first 2 visible checkboxes" logic that mis-filed every book.
                try:
                    cats = listing.get("categories", [])
                    # Snap onto KDP's real tree (idempotent for already-real paths)
                    # so books whose listing predates the resolver still upload with
                    # valid, tickable categories instead of GPT's invented nesting.
                    try:
                        from category_resolver import resolve_paths
                        cats = resolve_paths(cats) or cats
                    except Exception as _re:
                        logger.warning(f"⚠️ category resolver skipped: {_re}")
                    if cats:
                        applied = await set_categories(page, cats, logger)
                        logger.info(f"✓ Categories applied: {len(applied)} — {applied}")
                    else:
                        logger.info("✓ No categories in listing; skipping")
                except Exception as e:
                    logger.warning(f"⚠️ Category step failed: {e}")

                # Dismiss any lingering category-modal popover. When 0 categories
                # match (e.g. a category path the KDP tree doesn't contain), the
                # modal's Cancel can leave an 'a-popover-floating-close' backdrop
                # that intercepts the very next click and times it out for 30s,
                # failing the whole upload. Escape clears it. (The update flow
                # already does this; the new-title flow was missing it.)
                try:
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(800)
                except Exception:
                    pass

                # Reading interest age — juvenile/YA titles need a range or KDP flags
                # "Reading Interest Age is missing". Explicit listing value wins;
                # otherwise auto-derive from the categories (adult titles → None).
                try:
                    ria = listing.get("reading_interest_age") or juvenile_reading_age(cats)
                    if ria and ria.get("min") is not None and ria.get("max") is not None:
                        await set_reading_age(page, ria["min"], ria["max"], logger)
                except Exception as e:
                    logger.warning(f"⚠️ Reading-age step failed: {e}")

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

                page_text = await page.locator("body").inner_text(timeout=5000)
                if is_title_creation_limit(page_text):
                    message = "KDP title creation limit exceeded"
                    retry_after = record_title_creation_limit(slug, message)
                    current = json.loads(listing_file.read_text(encoding="utf-8"))
                    current["kdp_uploading"] = False
                    current["kdp_error"] = f"{message}; auto-retry after {retry_after}"
                    listing_file.write_text(
                        json.dumps(current, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    await notify(
                        "⏸️ <b>KDP จำกัดการสร้างหนังสือใหม่ชั่วคราว</b>\n"
                        f"{title}\n\nระบบพักการสร้างและจะลองใหม่หลัง {retry_after}"
                    )
                    raise TitleCreationLimitError(message)

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
                await set_ai_disclosure(page, require_selections=True)
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
            book_price = _get_book_price(slug)
            logger.info(f"Setting price: ${book_price}")
            price_set = False
            # Try common price input selectors
            for sel in ['input[name*="[US][list_price]"]', 'input[name*="[US][price"]',
                        'input[name*="list_price"][name*="US"]',
                        '#data-pricing-print-us-702-702-702 input']:
                try:
                    price_input = page.locator(sel).first
                    if await price_input.count() > 0 and await price_input.is_visible():
                        await price_input.fill(book_price)
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
                                await inp.fill(book_price)
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
                listing["publish_submission_confirmed_at"] = datetime.now().isoformat(
                    timespec="seconds"
                )
                listing["kdp_uploading"] = False
                listing["kdp_error"] = ""
                listing["quality_errors"] = []
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

        except TitleCreationLimitError:
            raise
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
    assert_kdp_mutation_allowed("update_ebook_content")
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
                        content_saved = await wait_for_pricing_page(page)
                        if content_saved:
                            logger.info(f"✓ Navigated to: {page.url}")
                        else:
                            logger.error(
                                "KDP did not reach pricing after content processing (url=%s)",
                                page.url,
                            )
                        break
                await page.wait_for_timeout(10000)
                waited += 10

            # Updates are not complete until KDP accepts Save and Publish.
            republished = False
            logger.info(f"Post-save URL: {page.url}")
            if "pricing" in page.url:
                logger.info("On pricing page — saving current pricing...")
                save_price = await page.query_selector(
                    'button:has-text("Save and Publish"), button:has-text("Publish")'
                )
                if not save_price:
                    raise RuntimeError("KDP Save and Publish button was not found")
                disabled = await save_price.get_attribute("disabled")
                if disabled is not None:
                    raise RuntimeError("KDP Save and Publish button remained disabled")
                await save_price.click()
                logger.info("✓ Clicked Save and Publish; waiting for KDP confirmation")
                if await wait_for_publish_confirmation(page):
                    logger.info("✓ KDP confirmed the update submission")
                    republished = True
                else:
                    raise RuntimeError(
                        f"KDP did not confirm update submission after Save and Publish (url={page.url})"
                    )

            if not content_saved or not republished:
                raise RuntimeError(
                    f"KDP update was not confirmed (content_saved={content_saved}, republished={republished}, "
                    f"url={page.url})"
                )

            # Update listing status
            data["kdp_uploading"] = False
            data["content_updated_at"] = datetime.now().strftime("%Y-%m-%d")
            data["update_submission_confirmed_at"] = datetime.now().isoformat(timespec="seconds")
            data["status"] = "uploaded"
            data["kdp_error"] = ""
            data["quality_errors"] = []
            listing_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

            await notify(f"✅ <b>KDP Content Updated</b>\n{title}\n\nEPUB re-uploaded with layout fixes.")
            logger.info("✅ Update complete!")
            return True

        except Exception as e:
            logger.error(f"❌ Update failed: {e}")
            try:
                failed_data = json.loads(listing_file.read_text(encoding="utf-8"))
                failed_data["kdp_uploading"] = False
                failed_data["kdp_error"] = str(e)[:300]
                listing_file.write_text(
                    json.dumps(failed_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as state_exc:
                logger.error("Could not persist KDP update failure state: %s", state_exc)
            await notify(f"❌ <b>KDP Update Failed</b>\n{title}\n\nError: {str(e)[:100]}")
            return False
        finally:
            await browser.close()


async def update_cover(slug: str) -> bool:
    """
    Replace the cover image of an already-published KDP ebook.
    Navigates to the book's content edit page, re-uploads cover.jpg,
    handles the accessibility checkbox + AI disclosure, and republishes.
    Mirrors update_ebook_content() but swaps the EPUB step for the cover step.
    NOTE: republishing is blocked by the TOTAL KDP FREEZE guard below.
    """
    assert_kdp_mutation_allowed("cover")
    book_dir = KDP_DIR / slug
    listing_file = book_dir / "listing.json"
    if not listing_file.exists():
        logger.error(f"No listing.json for {slug}")
        return False

    data = json.loads(listing_file.read_text())
    title = data.get("title", slug)
    cover_path = book_dir / "cover.jpg"

    if not cover_path.exists() or cover_path.stat().st_size < 10000:
        logger.error(f"No valid cover.jpg for {slug}")
        return False

    logger.info(f"=== UPDATE COVER: {title} ===")

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
            book_id = data.get("kdp_book_id")
            if not book_id:
                logger.error("No kdp_book_id in listing.json — cannot determine which book to update.")
                return False

            full_url = f"https://kdp.amazon.com/en_US/title-setup/kindle/{book_id}/content"
            await page.goto(full_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)

            if "signin" in page.url or "/ap/" in page.url:
                logger.warning("Session expired — re-logging in...")
                await browser.close()
                import subprocess as _sp
                login_r = _sp.run(
                    ["python3", str(Path(__file__).parent / "kdp_login_full.py")],
                    capture_output=True, text=True, timeout=120
                )
                if "Session saved" not in login_r.stdout:
                    logger.error(f"Re-login failed: {login_r.stdout[-200:]}")
                    return False
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

            # ── Upload new cover ──────────────────────────────────────────────
            logger.info("Uploading new cover...")
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
                cover_input = await page.query_selector('#data-assets-cover-jp-file-upload-AjaxInput')
            if not cover_input:
                await page.screenshot(path="/tmp/kdp_update_cover.png")
                raise Exception("Cover file input not found on content page")

            await cover_input.set_input_files(str(cover_path))
            logger.info("Cover upload started — waiting for processing...")
            try:
                await page.wait_for_selector(
                    '#data-assets-cover-asset-status[value*="SUCCESS"]',
                    timeout=180000
                )
                logger.info("✅ Cover processed successfully")
            except Exception:
                logger.warning("Could not confirm cover processing — waiting 30s...")
                await page.wait_for_timeout(30000)

            # ── Accessibility confirm checkbox (React fiber) ──────────────────
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

            # ── Save and Continue → pricing ───────────────────────────────────
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
                        content_saved = await wait_for_pricing_page(page)
                        break
                await page.wait_for_timeout(10000)
                waited += 10

            republished = False
            logger.info(f"Post-save URL: {page.url}")
            if "pricing" in page.url:
                logger.info("On pricing page — republishing...")
                save_price = await page.query_selector(
                    'button:has-text("Save and Publish"), button:has-text("Publish")'
                )
                if not save_price:
                    raise RuntimeError("KDP Save and Publish button was not found")
                disabled = await save_price.get_attribute("disabled")
                if disabled is not None:
                    raise RuntimeError("KDP Save and Publish button remained disabled")
                await save_price.click()
                logger.info("✓ Clicked Save and Publish; waiting for KDP confirmation")
                if await wait_for_publish_confirmation(page):
                    logger.info("✓ KDP confirmed the cover update submission")
                    republished = True
                else:
                    raise RuntimeError(
                        f"KDP did not confirm update after Save and Publish (url={page.url})"
                    )

            if not content_saved or not republished:
                raise RuntimeError(
                    f"KDP cover update not confirmed (content_saved={content_saved}, "
                    f"republished={republished}, url={page.url})"
                )

            data["kdp_uploading"] = False
            data["cover_updated_at"] = datetime.now().strftime("%Y-%m-%d")
            data["update_submission_confirmed_at"] = datetime.now().isoformat(timespec="seconds")
            data["status"] = "uploaded"
            data["kdp_error"] = ""
            listing_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

            await notify(f"✅ <b>KDP Cover Updated</b>\n{title}\n\nNew CJK-safe cover re-uploaded.")
            logger.info("✅ Cover update complete!")
            return True

        except Exception as e:
            logger.error(f"❌ Cover update failed: {e}")
            try:
                failed_data = json.loads(listing_file.read_text(encoding="utf-8"))
                failed_data["kdp_uploading"] = False
                failed_data["kdp_error"] = str(e)[:300]
                listing_file.write_text(
                    json.dumps(failed_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as state_exc:
                logger.error("Could not persist failure state: %s", state_exc)
            await notify(f"❌ <b>KDP Cover Update Failed</b>\n{title}\n\nError: {str(e)[:100]}")
            return False
        finally:
            await browser.close()


async def update_metadata(slug: str, skip_categories: bool = False) -> bool:
    """Update an already-published book's Amazon metadata ONLY — keywords, HTML
    description, and categories (via the React-cascade setter) — then republish.

    Unlike upload_to_kdp(), this does NOT re-upload the EPUB or re-run the content
    quality gate, so it works on live books whose content wouldn't pass current
    gates. Navigates details → content → pricing → publish, changing nothing on
    the content page beyond the required confirmations.
    """
    assert_kdp_mutation_allowed("metadata")
    book_dir = KDP_DIR / slug
    listing_file = book_dir / "listing.json"
    if not listing_file.exists():
        logger.error(f"No listing.json for {slug}")
        return False

    data = json.loads(listing_file.read_text())
    title = data.get("title", slug)
    description = data.get("description_html") or data.get("description", "")
    keywords_list = data.get("keywords", [])
    cats = data.get("categories", [])
    # Resolve GPT paths onto KDP's real tree (idempotent for already-real paths).
    try:
        from category_resolver import resolve_paths
        cats = resolve_paths(cats) or cats
    except Exception:
        pass
    book_id = data.get("kdp_book_id")
    if not book_id:
        logger.error("No kdp_book_id in listing.json — cannot update metadata.")
        return False

    logger.info(f"=== UPDATE METADATA: {title} ===")

    async with async_playwright() as p:
        if not SESSION_FILE.exists():
            logger.error("No session file — run kdp_login_full.py first")
            return False
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=str(SESSION_FILE), user_agent=ua)
        page = await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => false})")

        try:
            details_url = f"https://kdp.amazon.com/en_US/title-setup/kindle/{book_id}/details"
            await page.goto(details_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)

            if "signin" in page.url or "/ap/" in page.url:
                logger.warning("Session expired — re-logging in...")
                await browser.close()
                import subprocess as _sp
                login_r = _sp.run(["python3", str(Path(__file__).parent / "kdp_login_full.py")],
                                  capture_output=True, text=True, timeout=120)
                if "Session saved" not in login_r.stdout:
                    logger.error(f"Re-login failed: {login_r.stdout[-200:]}")
                    return False
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(storage_state=str(SESSION_FILE), user_agent=ua)
                page = await context.new_page()
                await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => false})")
                await page.goto(details_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3000)
                if "signin" in page.url or "/ap/" in page.url:
                    logger.error("Still on signin page after re-login")
                    return False

            if "bookshelf" in page.url and "title-setup" not in page.url:
                logger.warning("⏳ Redirected to bookshelf — book still In Review, cannot edit yet")
                return False

            logger.info(f"On details page: {page.url}")
            import json as _json

            # ── Description (HTML blurb) ──────────────────────────────────────
            if description:
                desc_js = _json.dumps(description)
                await page.evaluate(f'''() => {{
                    const desc = {desc_js};
                    if (typeof CKEDITOR !== "undefined") {{
                        for (const n in CKEDITOR.instances) CKEDITOR.instances[n].setData(desc);
                    }}
                    const h = document.querySelector('input[name="data[description]"]');
                    if (h) {{ h.value = desc; h.dispatchEvent(new Event("change", {{bubbles:true}})); }}
                }}''')
                await page.wait_for_timeout(800)

            # ── Keywords (7 backend slots) ────────────────────────────────────
            for i in range(min(7, len(keywords_list))):
                kw = keywords_list[i]
                await page.evaluate(f'''() => {{
                    const el = document.querySelector('input[name="data[keywords][{i}]"]');
                    if (el) {{ el.value = {_json.dumps(kw)}; el.dispatchEvent(new Event("change", {{bubbles:true}})); }}
                }}''')
            logger.info(f"✓ Filled {min(7, len(keywords_list))} keywords")

            # ── Reading interest age (juvenile / YA titles only) ──────────────
            # Explicit listing value wins; otherwise auto-derive from categories so
            # kids/teen books never ship without an age range.
            ria = data.get("reading_interest_age") or juvenile_reading_age(cats)
            if ria and ria.get("min") is not None and ria.get("max") is not None:
                await set_reading_age(page, ria["min"], ria["max"], logger)

            # ── Categories (React cascade) ────────────────────────────────────
            if cats and not skip_categories:
                applied = await set_categories(page, cats, logger)
                logger.info(f"✓ Categories applied: {len(applied)} — {applied}")
                # Let the modal fully close so its backdrop can't intercept the
                # Save and Continue click that follows.
                await page.wait_for_timeout(2500)
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                await page.wait_for_timeout(800)

            # ── details → content ─────────────────────────────────────────────
            waited = 0
            while waited < 60:
                btn = await page.query_selector('button:has-text("Save and Continue"), input[value*="Save and Continue"]')
                if btn and not await btn.get_attribute("disabled"):
                    try:
                        await btn.click(timeout=8000)
                    except Exception:
                        # A lingering category-modal backdrop can intercept the click;
                        # dispatch it directly via JS to bypass the overlay.
                        await btn.evaluate("el => el.click()")
                    logger.info("✓ details: Save and Continue")
                    break
                await page.wait_for_timeout(5000); waited += 5
            # Fail fast if the details page didn't advance (a validation error keeps
            # us on /details; proceeding would hang in wait_for_pricing_page for 15m).
            advanced = False
            for _ in range(8):
                await page.wait_for_timeout(2000)
                if "/details" not in page.url:
                    advanced = True
                    break
            if not advanced:
                await page.screenshot(path=f"/tmp/kdp_meta_stuck_{slug}.png")
                raise RuntimeError(f"details page did not advance (validation error?) url={page.url}")

            # ── content page: required confirmations, then → pricing ──────────
            if "content" in page.url:
                try:
                    await page.evaluate("""() => {
                        for (const cb of document.querySelectorAll('div[role="checkbox"]')) {
                            if (cb.getAttribute('aria-checked') === 'true') continue;
                            const txt = (cb.closest('label') || cb.parentElement || cb).textContent || '';
                            if (!txt.toLowerCase().includes('confirm')) continue;
                            const key = Object.keys(cb).find(k => k.startsWith('__reactFiber'));
                            if (key) { let f = cb[key]; while (f) { const pr = f.memoizedProps||f.pendingProps||{};
                                if (pr.onClick) { pr.onClick({type:'click',target:cb,currentTarget:cb,stopPropagation:()=>{},preventDefault:()=>{}}); break; } f = f.return; } }
                        }
                    }""")
                except Exception:
                    pass
                await set_ai_disclosure(page)
                await page.wait_for_timeout(500)
                waited = 0
                while waited < 120:
                    btn = await page.query_selector('button:has-text("Save and Continue"), input[value*="Save and Continue"]')
                    if btn and not await btn.get_attribute("disabled"):
                        try:
                            await btn.click(timeout=8000)
                        except Exception:
                            await btn.evaluate("el => el.click()")
                        logger.info("✓ content: Save and Continue")
                        break
                    await page.wait_for_timeout(5000); waited += 5

            await wait_for_pricing_page(page)

            # ── pricing → publish ─────────────────────────────────────────────
            republished = False
            logger.info(f"Pre-publish URL: {page.url}")
            if "pricing" in page.url:
                save_price = await page.query_selector('button:has-text("Save and Publish"), button:has-text("Publish")')
                if not save_price:
                    raise RuntimeError("Save and Publish button not found")
                if await save_price.get_attribute("disabled") is not None:
                    raise RuntimeError("Save and Publish remained disabled")
                try:
                    await save_price.click(timeout=8000)
                except Exception:
                    await save_price.evaluate("el => el.click()")
                logger.info("✓ Clicked Save and Publish; waiting for confirmation")
                if await wait_for_publish_confirmation(page):
                    republished = True
                else:
                    raise RuntimeError(f"KDP did not confirm after Save and Publish (url={page.url})")

            if not republished:
                raise RuntimeError(f"Metadata update not confirmed (url={page.url})")

            data["kdp_uploading"] = False
            data["metadata_updated_at"] = datetime.now().isoformat(timespec="seconds")
            data["update_submission_confirmed_at"] = datetime.now().isoformat(timespec="seconds")
            data["status"] = "uploaded"
            data["kdp_error"] = ""
            listing_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            logger.info("✅ Metadata update complete!")
            return True

        except Exception as e:
            logger.error(f"❌ Metadata update failed: {e}")
            try:
                fd = json.loads(listing_file.read_text(encoding="utf-8"))
                fd["kdp_uploading"] = False
                fd["kdp_error"] = str(e)[:300]
                listing_file.write_text(json.dumps(fd, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            return False
        finally:
            await browser.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: python3 kdp_upload.py <slug> "
            "[--update|--cover|--preflight-update|--inspect-title]"
        )
        sys.exit(1)

    slug = sys.argv[1]
    update_mode = "--update" in sys.argv
    cover_mode = "--cover" in sys.argv
    meta_mode = "--meta" in sys.argv
    preflight_mode = "--preflight-update" in sys.argv
    inspect_mode = "--inspect-title" in sys.argv

    # TOTAL KDP FREEZE: every CLI mode below either mutates KDP or opens the
    # authenticated session/browser, so the guard runs before any dispatch.
    if inspect_mode:
        action = "inspect_title"
    elif preflight_mode:
        action = "preflight_update"
    elif cover_mode:
        action = "cover"
    elif meta_mode:
        action = "metadata"
    elif update_mode:
        action = "update_ebook_content"
    else:
        action = "new_title"
    try:
        assert_kdp_mutation_allowed(action, slug)
    except KDPFrozenError as exc:
        print(f"{exc.code}: {exc.action}: {exc}", file=sys.stderr)
        sys.exit(73)

    if inspect_mode:
        matches = asyncio.run(inspect_bookshelf_title(slug))
        print(json.dumps(matches, ensure_ascii=False, indent=2))
        result = True
    elif preflight_mode:
        result = preflight_update(slug)
    elif cover_mode:
        result = asyncio.run(update_cover(slug))
    elif meta_mode:
        result = asyncio.run(update_metadata(slug, skip_categories="--no-categories" in sys.argv))
    elif update_mode:
        result = asyncio.run(update_ebook_content(slug))
    else:
        try:
            result = asyncio.run(upload_to_kdp(slug))
        except TitleCreationLimitError as exc:
            logger.warning("⏸️ %s", exc)
            sys.exit(42)
    sys.exit(0 if result else 1)
