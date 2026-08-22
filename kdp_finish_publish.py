"""
Quick-fix: navigate directly to the pricing page for a book
that has "Live with unpublished changes" and click Publish.
Usage: python3 kdp_finish_publish.py <slug> [price]
       price defaults to 2.99; pass the book's real list price (e.g. 4.99) so a
       non-$2.99 title is not silently repriced when we only want to submit the
       pending draft.
"""
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kdp_freeze import KDPFrozenError, assert_kdp_mutation_allowed

KDP_DIR = Path("/root/kdp")
SESSION_FILE = Path("/root/libra/kdp_session.json")
LOGIN_SCRIPT = Path("/root/libra/kdp_login_full.py")


async def finish_publish(slug: str, price: str = "2.99") -> bool:
    assert_kdp_mutation_allowed("publish", slug)
    book_dir = KDP_DIR / slug
    listing_file = book_dir / "listing.json"
    if not listing_file.exists():
        print(f"[ERROR] No listing.json for {slug}")
        return False

    data = json.loads(listing_file.read_text())
    book_id = data.get("kdp_book_id")
    title = data.get("title", slug)

    if not book_id:
        print("[ERROR] No kdp_book_id in listing.json")
        return False

    pricing_url = f"https://kdp.amazon.com/en_US/title-setup/kindle/{book_id}/pricing"
    print(f"[INFO] Navigating to pricing page: {pricing_url}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        context = await browser.new_context(
            storage_state=str(SESSION_FILE),
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        )
        page = await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => false})")

        await page.goto(pricing_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)

        # Re-login if needed
        if "signin" in page.url or "/ap/" in page.url:
            print("[WARN] Session expired — re-logging in...")
            await browser.close()
            import subprocess as _sp
            _sp.run(["python3", str(LOGIN_SCRIPT)], capture_output=True, text=True, timeout=120)
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                storage_state=str(SESSION_FILE),
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            )
            page = await context.new_page()
            await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => false})")
            await page.goto(pricing_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(4000)

        print(f"[INFO] Current URL: {page.url}")

        # If redirected away from pricing, something is wrong
        if "pricing" not in page.url and "title-setup" not in page.url:
            await page.screenshot(path="/tmp/kdp_finish_publish_error.png")
            print(f"[ERROR] Not on pricing page. URL: {page.url}")
            await browser.close()
            return False

        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)

        # Take screenshot to see current state
        await page.screenshot(path="/tmp/kdp_finish_publish_before.png")
        print("[INFO] Screenshot saved to /tmp/kdp_finish_publish_before.png")

        # ── Step 1: Select 70% royalty ──────────────────────────────────────
        try:
            royalty_70 = page.locator('input[type="radio"][id*="70"], input[type="radio"][value*="70"]')
            if await royalty_70.count() > 0:
                await royalty_70.first.click(force=True)
                print("[INFO] ✓ Selected 70% royalty")
                await page.wait_for_timeout(1000)
        except Exception as e:
            print(f"[WARN] Royalty selection: {e}")

        # ── Step 2: All territories ─────────────────────────────────────────
        try:
            await page.evaluate('''() => {
                const radios = document.querySelectorAll("input[type=radio]");
                for (const r of radios) {
                    const label = r.closest("label") || r.parentElement;
                    if (label && label.textContent.toLowerCase().includes("all territories")) {
                        r.click(); return;
                    }
                }
            }''')
            print("[INFO] ✓ All territories selected")
        except Exception as e:
            print(f"[WARN] Territories: {e}")
        await page.wait_for_timeout(1000)

        # ── Step 3: Set price $2.99 ─────────────────────────────────────────
        price_set = False
        for sel in ['input[name*="[US][list_price]"]', 'input[name*="[US][price"]',
                    'input[name*="list_price"][name*="US"]',
                    '#data-pricing-print-us-702-702-702 input']:
            try:
                price_input = page.locator(sel).first
                if await price_input.count() > 0 and await price_input.is_visible():
                    await price_input.fill(price)
                    await price_input.press("Tab")
                    price_set = True
                    print(f"[INFO] ✓ Price set via {sel} -> {price}")
                    break
            except Exception:
                continue

        if not price_set:
            price_inputs = await page.query_selector_all('input[type="text"]')
            for inp in price_inputs:
                try:
                    name = await inp.get_attribute('name') or ''
                    if ('price' in name.lower() or 'list_price' in name.lower()) and await inp.is_visible():
                        await inp.fill(price)
                        await inp.evaluate('el => el.dispatchEvent(new Event("blur",{bubbles:true}))')
                        price_set = True
                        print(f"[INFO] ✓ Price set via name={name} -> {price}")
                        break
                except Exception:
                    continue

        if not price_set:
            print("[WARN] Could not find price input — will try publishing anyway")

        await page.wait_for_timeout(3000)
        await page.screenshot(path="/tmp/kdp_finish_publish_btn.png")

        # ── Step 4: Click Publish ───────────────────────────────────────────
        for selector in [
            'button:has-text("Publish Your Kindle eBook")',
            'button:has-text("Save and Publish")',
            'button:has-text("Publish")',
            'input[type="submit"]:has-text("Publish")',
        ]:
            btn = page.locator(selector)
            count = await btn.count()
            if count > 0:
                print(f"[INFO] Found publish button: {selector}")
                await btn.first.scroll_into_view_if_needed()
                await btn.first.click()
                print("[INFO] Clicked Publish button")
                await page.wait_for_timeout(8000)
                await page.screenshot(path="/tmp/kdp_finish_publish_after.png")
                print("[INFO] After-click screenshot saved to /tmp/kdp_finish_publish_after.png")

                # Check for validation errors. KDP's banner is "Please fix the
                # highlighted error(s) to continue" — match as a SUBSTRING (an
                # exact-text locator silently misses the "(s) to continue" suffix
                # and reports a false success while the book never publishes).
                error_box = page.get_by_text("Please fix the highlighted error", exact=False)
                if await error_box.count() > 0:
                    print("[ERROR] Validation errors on pricing page — not published; "
                          "see /tmp/kdp_finish_publish_after.png")
                    await browser.close()
                    return False

                # Update listing
                data["kdp_uploading"] = False
                data["kdp_error"] = None
                listing_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
                print(f"[SUCCESS] ✅ Published: {title}")
                await browser.close()
                return True

        # No publish button found
        await page.screenshot(path="/tmp/kdp_finish_publish_nobtn.png")
        print("[WARN] No Publish button found — screenshot at /tmp/kdp_finish_publish_nobtn.png")
        print(f"[INFO] Page title: {await page.title()}")
        await browser.close()
        return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 kdp_finish_publish.py <slug> [price]")
        sys.exit(1)
    slug = sys.argv[1]
    price = sys.argv[2] if len(sys.argv) > 2 else "2.99"
    try:
        ok = asyncio.run(finish_publish(slug, price))
    except KDPFrozenError as exc:
        print(f"{exc.code}: {exc.action}: {exc}", file=sys.stderr)
        sys.exit(73)
    sys.exit(0 if ok else 1)
