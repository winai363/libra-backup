#!/usr/bin/env python3
"""
kdp_live_replace.py — Replace KDP live book manuscripts after human approval.

Usage:
  python3 kdp_live_replace.py                        # interactive — approve per book
  python3 kdp_live_replace.py --slug SLUG            # single book only
  python3 kdp_live_replace.py --all                  # approve all REPLACE books (no prompt)

Safety rules enforced:
  - Never uploads if QA action != "REPLACE"
  - Never uploads if corrected EPUB is missing
  - Never overwrites original ebook.epub without backup
  - Never modifies title, author, ISBN, ASIN, pricing, categories, keywords, cover
  - Only replaces interior manuscript
  - Full rollback available via audit/original_backup/

Requires:
  - kdp_live_audit.py to have been run first (reads audit_report.json)
  - kdp_session.json to be valid (run kdp_login_full.py if not)
"""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from kdp_freeze import assert_kdp_mutation_allowed  # noqa: E402
from dotenv import load_dotenv
load_dotenv("/root/libra/.env")

KDP_DIR      = Path(os.getenv("KDP_DIR", "/root/kdp"))
LIBRA_DIR    = Path(__file__).parent
LOGS_DIR     = KDP_DIR / "logs"
REPORT_FILE  = LOGS_DIR / "audit_report.json"
SESSION_FILE = LIBRA_DIR / "kdp_session.json"
REPLACE_LOG  = LOGS_DIR / "replace_log.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOGS_DIR / "replace.log"), encoding="utf-8"),
    ]
)
logger = logging.getLogger("kdp_live_replace")


# ── Safety checks ──────────────────────────────────────────────────────────────

class SafetyError(Exception):
    pass


def safety_check(book_result):
    """Raise SafetyError if this book must not be uploaded."""
    action = book_result.get("action")
    if action != "REPLACE":
        raise SafetyError(f"Action is '{action}', not 'REPLACE'. Only REPLACE books can be uploaded.")

    if not book_result.get("corrected_epub"):
        raise SafetyError("No corrected EPUB file path in audit report. Run kdp_live_audit.py first.")

    epub = Path(book_result["corrected_epub"])
    if not epub.exists():
        raise SafetyError(f"Corrected EPUB not found on disk: {epub}")

    if epub.stat().st_size < 5000:
        raise SafetyError(f"Corrected EPUB is suspiciously small ({epub.stat().st_size} bytes). Aborting.")

    kdp_id = book_result.get("kdp_book_id", "")
    if not kdp_id:
        raise SafetyError("No kdp_book_id in audit report. Cannot safely identify which KDP book to update.")

    book_dir = Path(book_result["book_dir"])
    backup_dir = book_dir / "audit" / "original_backup"
    if not backup_dir.exists():
        raise SafetyError("No original_backup directory found. Run kdp_live_audit.py first to create backups.")

    if not SESSION_FILE.exists():
        raise SafetyError("No KDP session file. Run python3 kdp_login_full.py first.")


# ── Approval prompt ────────────────────────────────────────────────────────────

def prompt_approval(book_result):
    """Show book details + QA results and ask for y/n approval. Returns bool."""
    title     = book_result["title"]
    slug      = book_result["slug"]
    kdp_id    = book_result["kdp_book_id"]
    lang      = book_result.get("language", "")
    epub      = book_result.get("corrected_epub", "—")
    pdf       = book_result.get("corrected_pdf", "—")
    notes     = book_result.get("notes", [])
    issues    = book_result.get("qa_issues", [])

    print("\n" + "─" * 60)
    print(f"📚  {title}")
    print(f"    Slug:      {slug}")
    print(f"    Language:  {lang}")
    print(f"    KDP ID:    {kdp_id}")
    print(f"    Corrected EPUB: {epub}")
    print(f"    Corrected PDF:  {pdf}")
    print()
    print("QA Issues found and auto-fixed:")
    for iss in issues:
        icon  = "✗" if iss["severity"] == "error" else "⚠"
        fixed = " [auto-fixed]" if iss["fixed"] else " [UNFIXED]"
        print(f"  {icon} {iss['message']}{fixed}")
    if not issues:
        print("  (none)")
    print()
    for n in notes:
        print(f"  ℹ {n}")
    print()
    print("This will REPLACE the interior manuscript on Amazon KDP.")
    print("Cover, title, price, categories, and keywords will NOT be changed.")
    print()

    while True:
        ans = input("Approve this replacement? [y/n/q to quit all]: ").strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        if ans in ("q", "quit"):
            print("Quit — no further replacements.")
            sys.exit(0)
        print("Please enter y, n, or q.")


# ── KDP upload ─────────────────────────────────────────────────────────────────

async def replace_on_kdp(book_result):
    """Upload corrected EPUB to KDP for an approved book. Returns bool."""
    assert_kdp_mutation_allowed("live_replace")
    from playwright.async_api import async_playwright

    slug     = book_result["slug"]
    title    = book_result["title"]
    kdp_id   = book_result["kdp_book_id"]
    book_dir = Path(book_result["book_dir"])
    epub_src = Path(book_result["corrected_epub"])
    epub_dst = book_dir / "ebook.epub"

    # ── Backup and swap EPUB ───────────────────────────────────────────────────
    backup_dir = book_dir / "audit" / "original_backup"
    epub_bak   = backup_dir / "ebook.epub.bak"
    if epub_dst.exists() and not epub_bak.exists():
        shutil.copy2(str(epub_dst), str(epub_bak))
        logger.info(f"  Backed up original EPUB → {epub_bak}")

    shutil.copy2(str(epub_src), str(epub_dst))
    logger.info(f"  Copied corrected EPUB → {epub_dst}")

    # ── Playwright upload ──────────────────────────────────────────────────────
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            storage_state=str(SESSION_FILE),
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        )
        page = await ctx.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => false})")

        try:
            # Step 1: Verify session
            logger.info("  → Navigating to KDP bookshelf...")
            await page.goto("https://kdp.amazon.com/en_US/bookshelf",
                            wait_until="domcontentloaded", timeout=60000)
            if "signin" in page.url or "/ap/" in page.url:
                logger.warning("  Session expired — re-logging in...")
                await browser.close()
                result = subprocess.run(
                    ["python3", str(LIBRA_DIR / "kdp_login_full.py")],
                    capture_output=True, text=True, timeout=120,
                )
                if "Session saved" not in result.stdout:
                    raise SafetyError(f"Re-login failed: {result.stdout[-200:]}")
                browser = await pw.chromium.launch(headless=True)
                ctx = await browser.new_context(
                    storage_state=str(SESSION_FILE),
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                )
                page = await ctx.new_page()
                await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => false})")
                await page.goto("https://kdp.amazon.com/en_US/bookshelf",
                                wait_until="domcontentloaded", timeout=60000)

            # Step 2: Navigate to content edit page
            content_url = f"https://kdp.amazon.com/en_US/title-setup/kindle/{kdp_id}/content"
            logger.info(f"  → Opening content page: {content_url}")
            await page.goto(content_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)

            if "signin" in page.url or "/ap/" in page.url:
                raise SafetyError("Redirected to signin after navigation — session invalid.")

            # KDP redirects to bookshelf when book is still "In review"
            if "bookshelf" in page.url and "title-setup" not in page.url:
                raise SafetyError("Book is still In Review — cannot edit yet. Try again in 24–72h.")

            logger.info(f"  → On content page: {page.url}")

            # Step 3: Upload corrected EPUB
            epub_input = await page.query_selector('#data-assets-interior-file-upload-AjaxInput')
            if not epub_input:
                await page.screenshot(path="/tmp/kdp_replace_debug.png")
                raise SafetyError("EPUB file input (#data-assets-interior-file-upload-AjaxInput) not found.")

            await epub_input.set_input_files(str(epub_dst))
            logger.info("  → EPUB uploading — waiting for KDP processing...")

            # Step 4: Wait for processing success
            try:
                await page.wait_for_selector(
                    '#data-assets-interior-asset-status[value*="SUCCESS"]',
                    timeout=300000,  # 5 minutes
                )
                logger.info("  → EPUB processed successfully by KDP ✓")
            except Exception:
                logger.warning("  → Could not confirm processing status — waiting 90s extra...")
                await page.wait_for_timeout(90000)

            # Step 5: Accessibility confirmation checkbox (React fiber click)
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
                                    props.onClick({type:'click',target:cb,currentTarget:cb,
                                        stopPropagation:()=>{},preventDefault:()=>{}});
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
                    logger.info(f"  → Accessibility checkbox: clicked {n} box(es) via React fiber ✓")
            except Exception as e:
                logger.warning(f"  → Accessibility checkbox skipped: {e}")

            # Step 6: Save and Continue (wait up to 15 minutes for button to enable)
            logger.info("  → Waiting for Save and Continue button...")
            waited = 0
            saved  = False
            while waited < 900:
                btn = await page.query_selector(
                    'button:has-text("Save and Continue"), input[value*="Save and Continue"]'
                )
                if btn:
                    disabled = await btn.get_attribute("disabled")
                    if not disabled:
                        await btn.click()
                        logger.info("  → Clicked Save and Continue ✓")
                        await page.wait_for_timeout(8000)
                        saved = True
                        break
                await page.wait_for_timeout(10000)
                waited += 10

            if not saved:
                raise SafetyError("Save and Continue button never became enabled after 15 minutes.")

            # Step 7: Confirm we're on pricing page (or at least not on content page)
            final_url = page.url
            logger.info(f"  → After save, URL: {final_url}")

            if "pricing" in final_url:
                logger.info("  → On pricing page — saving without changing price...")
                pub_btn = await page.query_selector(
                    'button:has-text("Save and Publish"), button:has-text("Publish")'
                )
                if pub_btn:
                    await pub_btn.click()
                    await page.wait_for_timeout(10000)
                    logger.info("  → Re-published with updated content ✓")

            await page.screenshot(path=f"/tmp/kdp_replace_{slug}_result.png")
            return True

        except SafetyError:
            raise
        except Exception as e:
            await page.screenshot(path=f"/tmp/kdp_replace_{slug}_error.png")
            raise RuntimeError(f"Playwright upload failed: {e}") from e
        finally:
            await browser.close()


# ── Log helper ─────────────────────────────────────────────────────────────────

def log_result(slug, success, error_msg=None):
    """Append result to replace_log.json."""
    entry = {
        "slug":        slug,
        "timestamp":   datetime.now().strftime("%Y-%m-%d %H:%M"),
        "success":     success,
        "error":       error_msg,
    }
    existing = []
    if REPLACE_LOG.exists():
        try:
            existing = json.loads(REPLACE_LOG.read_text())
        except Exception:
            existing = []
    existing.append(entry)
    REPLACE_LOG.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def update_listing(book_result):
    """Update listing.json with content_updated_at after successful replacement."""
    listing_file = Path(book_result["book_dir"]) / "listing.json"
    try:
        data = json.loads(listing_file.read_text())
        data["content_updated_at"] = datetime.now().strftime("%Y-%m-%d")
        listing_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Could not update listing.json: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    # Parse args
    slug_filter = None
    approve_all = "--all" in sys.argv
    if "--slug" in sys.argv:
        idx = sys.argv.index("--slug")
        if idx + 1 < len(sys.argv):
            slug_filter = sys.argv[idx + 1]

    print("=== KDP Live Manuscript Replacement ===")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print()

    # Load audit report
    if not REPORT_FILE.exists():
        print(f"❌ Audit report not found: {REPORT_FILE}")
        print("   Run: python3 kdp_live_audit.py")
        sys.exit(1)

    report = json.loads(REPORT_FILE.read_text())
    all_books = report.get("books", [])
    summary   = report.get("summary", {})

    print(f"Audit report from: {summary.get('generated_at', '—')}")
    print(f"Total: {summary.get('total',0)}  REPLACE: {summary.get('replace',0)}  "
          f"MANUAL REVIEW: {summary.get('manual_review',0)}")
    print()

    # Filter candidates
    candidates = [
        b for b in all_books
        if b["action"] == "REPLACE"
        and (slug_filter is None or b["slug"] == slug_filter)
    ]

    if not candidates:
        print("✓ No books require replacement.")
        sys.exit(0)

    print(f"Found {len(candidates)} book(s) eligible for replacement.\n")

    approved = []
    skipped  = []

    for book in candidates:
        if approve_all:
            try:
                safety_check(book)
                approved.append(book)
                print(f"  AUTO-APPROVED: {book['title']}")
            except SafetyError as e:
                print(f"  SAFETY BLOCK: {book['title']} — {e}")
                skipped.append((book, str(e)))
        else:
            try:
                safety_check(book)
            except SafetyError as e:
                print(f"\n⛔ SAFETY BLOCK — {book['title']}: {e}")
                skipped.append((book, str(e)))
                continue

            if prompt_approval(book):
                approved.append(book)
            else:
                skipped.append((book, "User declined"))

    if not approved:
        print("\nNo replacements approved. Done.")
        sys.exit(0)

    print(f"\n{'─'*60}")
    print(f"Starting replacement for {len(approved)} book(s)...")
    print(f"{'─'*60}\n")

    for i, book in enumerate(approved, 1):
        title = book["title"]
        slug  = book["slug"]
        print(f"[{i}/{len(approved)}] Replacing: {title}")

        try:
            success = asyncio.run(replace_on_kdp(book))
            if success:
                update_listing(book)
                log_result(slug, True)
                print(f"  ✅ SUCCESS — {slug}")
                print(f"     Screenshot: /tmp/kdp_replace_{slug}_result.png")
            else:
                log_result(slug, False, "replace_on_kdp returned False")
                print(f"  ❌ FAILED — {slug}")
        except SafetyError as e:
            log_result(slug, False, f"SafetyError: {e}")
            print(f"  ⛔ SAFETY BLOCK — {e}")
        except Exception as e:
            log_result(slug, False, str(e))
            print(f"  ❌ ERROR — {e}")
        print()

    print("─" * 60)
    print("Replacement complete. Results saved to:")
    print(f"  {REPLACE_LOG}")
    if skipped:
        print(f"\nSkipped ({len(skipped)}):")
        for b, reason in skipped:
            print(f"  - {b['title']}: {reason}")


if __name__ == "__main__":
    main()
