#!/usr/bin/env python3
"""
Fix a KDP book that's missing cover or has issues.
Waits for book to be editable, then re-uploads cover and saves.

Usage: python3 kdp_fix_book.py <ASIN>
"""
import asyncio
import sys
import json
from pathlib import Path
from playwright.async_api import async_playwright

ENV = {}
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            ENV[k.strip()] = v.strip()

SESSION_FILE = Path(__file__).parent / "kdp_session.json"
KDP_DIR = Path(ENV.get("KDP_DIR", "/root/kdp"))


async def fix_book(asin: str):
    # Find the book directory by ASIN or slug
    book_dir = None
    for d in KDP_DIR.iterdir():
        if d.is_dir() and d.name == asin:
            book_dir = d
            break
        listing = d / "listing.json"
        if listing.exists():
            data = json.loads(listing.read_text())
            if data.get("asin") == asin:
                book_dir = d
                break

    # If no match by ASIN, use first arg as slug
    if not book_dir:
        book_dir = KDP_DIR / asin
    if not book_dir.exists():
        print(f"❌ Book directory not found: {asin}")
        return

    cover_path = book_dir / "cover.jpg"
    if not cover_path.exists():
        print(f"❌ No cover.jpg in {book_dir}")
        return

    cover_size = cover_path.stat().st_size
    print(f"Cover: {cover_path} ({cover_size} bytes)")
    if cover_size < 10000:
        print("⚠️ Cover too small — generate a proper one first")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            storage_state=str(SESSION_FILE),
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()

        # Go to bookshelf
        await page.goto("https://kdp.amazon.com/en_US/bookshelf", wait_until="networkidle")
        if "signin" in page.url:
            print("❌ Session expired — run kdp_login_full.py first")
            await browser.close()
            return

        # Find the book and check its status
        text = await page.evaluate("() => document.body.innerText")
        if "In review" in text:
            print("⚠️ Book is still 'In review' — cannot edit yet")
            print("  Amazon review takes up to 72 hours.")
            print("  Run this script again after review completes.")
            await browser.close()
            return

        # Try to navigate to content page
        # First find the book's edit link
        content_url = f"https://kdp.amazon.com/en_US/title-setup/kindle/{asin}/content"
        await page.goto(content_url, wait_until="networkidle")
        await page.wait_for_timeout(3000)

        if "/content" not in page.url:
            print(f"⚠️ Cannot access content page. URL: {page.url}")
            await page.screenshot(path="/tmp/kdp_fix_debug.png")
            await browser.close()
            return

        print("✅ Content page accessible")

        # Upload cover
        upload_tab = await page.query_selector('a:has-text("Upload a cover you already have")')
        if upload_tab:
            await upload_tab.click()
            await page.wait_for_timeout(1500)

        cover_input = await page.query_selector('#data-assets-cover-file-upload-AjaxInput')
        if not cover_input:
            cover_input = await page.query_selector('#data-assets-cover-jp-file-upload-AjaxInput')

        if cover_input:
            await cover_input.set_input_files(str(cover_path))
            print("→ Uploading cover...")
            # Wait for processing
            try:
                await page.wait_for_selector(
                    '#data-assets-cover-asset-status[value*="SUCCESS"]',
                    timeout=60000
                )
                print("✅ Cover uploaded successfully!")
            except Exception:
                await page.wait_for_timeout(15000)
                print("⚠️ Cover upload status unclear, continuing...")
        else:
            print("❌ Cover input not found")

        # Save
        await page.get_by_text("Save and Continue", exact=False).first.click()
        await page.wait_for_timeout(5000)
        print(f"After save: {page.url}")
        await page.screenshot(path="/tmp/kdp_fix_result.png")

        if "/pricing" in page.url:
            print("✅ Saved successfully — now on pricing page")
            # Re-publish
            publish_btn = page.locator('button:has-text("Publish")')
            if await publish_btn.count() > 0:
                await publish_btn.first.click()
                await page.wait_for_timeout(10000)
                print("✅ Re-published!")
                await page.screenshot(path="/tmp/kdp_fix_published.png")

        await browser.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 kdp_fix_book.py <ASIN_or_slug>")
        sys.exit(1)
    asyncio.run(fix_book(sys.argv[1]))
