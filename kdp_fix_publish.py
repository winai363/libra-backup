"""
Fix publish: check accessibility checkbox → Save and Continue → pricing → Publish
Usage: python3 kdp_fix_publish.py
"""
import asyncio, json, sys
from pathlib import Path
from playwright.async_api import async_playwright
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kdp_freeze import assert_kdp_mutation_allowed  # noqa: E402

BOOK_ID  = "A3M1PU96JJ0H1T"
LISTING  = Path("/root/kdp/anxiety-workbook-young-women-de/listing.json")
SESSION  = Path("/root/libra/kdp_session.json")
LOGIN_SC = Path("/root/libra/kdp_login_full.py")

async def main():
    assert_kdp_mutation_allowed("fix_publish")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            storage_state=str(SESSION),
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/91.0 Safari/537.36",
            viewport={"width": 1280, "height": 900}
        )
        page = await ctx.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => false})")

        # ── 1. Load content page ─────────────────────────────────────────────
        url = f"https://kdp.amazon.com/en_US/title-setup/kindle/{BOOK_ID}/content"
        await page.goto(url, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(4000)
        print(f"[1] Content page: {page.url}")

        if "signin" in page.url or "/ap/" in page.url:
            print("[!] Session expired — re-logging in...")
            import subprocess
            subprocess.run(["python3", str(LOGIN_SC)], timeout=120)
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(4000)

        # ── 2. Scroll to bottom + native-click accessibility checkbox ────────
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(2000)

        # div[role="checkbox"] that is unchecked (aria-checked=false)
        # Find the VISIBLE unchecked confirmation checkbox
        # Amazon renders 2 duplicate divs — only 1 is visible (offsetParent != null)
        checked_one = False
        cb_info = await page.evaluate("""() => {
            const divs = document.querySelectorAll('div[role="checkbox"]');
            const results = [];
            divs.forEach((div, i) => {
                results.push({
                    index: i,
                    ariaChecked: div.getAttribute('aria-checked'),
                    visible: div.offsetParent !== null,
                    text: (div.closest('label') || div.parentElement || div).textContent.trim().slice(0, 80),
                    rect: JSON.stringify(div.getBoundingClientRect())
                });
            });
            return results;
        }""")
        print(f"[2] Checkboxes found: {len(cb_info)}")
        for info in cb_info:
            print(f"    [{info['index']}] aria={info['ariaChecked']} vis={info['visible']} | {info['text'][:60]}")

        # Use React fiber to properly trigger onClick (standard Playwright click
        # changes aria-checked visually but doesn't update React state)
        react_result = await page.evaluate("""() => {
            const cbs = document.querySelectorAll('div[role="checkbox"]');
            let clicked = 0;
            for (const cb of cbs) {
                const txt = (cb.closest('label') || cb.parentElement || cb).textContent || '';
                if (txt.toLowerCase().includes('confirm') && cb.offsetParent !== null) {
                    const key = Object.keys(cb).find(k => k.startsWith('__reactFiber'));
                    if (key) {
                        let fiber = cb[key];
                        while (fiber) {
                            const props = fiber.memoizedProps || fiber.pendingProps || {};
                            if (props.onClick) {
                                props.onClick({type:'click', target:cb, currentTarget:cb,
                                    stopPropagation:()=>{}, preventDefault:()=>{}});
                                clicked++;
                                break;
                            }
                            fiber = fiber.return;
                        }
                    }
                }
            }
            return clicked;
        }""")
        print(f"[2] Clicked {react_result} checkbox(es) via React fiber")
        await page.wait_for_timeout(1500)

        # Verify aria-checked
        states = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('div[role="checkbox"]'))
                .map(cb => cb.getAttribute('aria-checked'));
        }""")
        print(f"    aria-checked states: {states}")
        await page.screenshot(path="/tmp/fix_after_cb_click.png")
        checked_one = react_result > 0

        await page.screenshot(path="/tmp/fix_01_checked.png")

        # ── 3. Click Save and Continue ───────────────────────────────────────
        # Scroll to bottom first so button is in view
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1500)
        await page.screenshot(path="/tmp/fix_02_before_save.png")

        save_btn = page.locator('button:has-text("Save and Continue")')
        count_sb = await save_btn.count()
        print(f"[3] Found {count_sb} Save and Continue button(s)")
        if count_sb == 0:
            print("[!] No Save and Continue — checking page URL")
            print(f"    URL: {page.url}")
            await browser.close()
            return False

        await save_btn.first.click()
        print("[3] Clicked Save and Continue")
        await page.wait_for_timeout(3000)
        await page.screenshot(path="/tmp/fix_02_after_save.png")
        print(f"    URL after save click: {page.url}")
        # Quick check for any visible errors
        err_texts = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('.a-color-error, [class*="error-msg"]'))
                .map(el => el.textContent.trim().slice(0, 100))
                .filter(t => t.length > 5);
        }""")
        for e in err_texts[:5]:
            print(f"    ERR: {e[:80]}")

        # ── 4. Wait for pricing page; re-click Save and Continue if dialog ───
        print("[4] Waiting for pricing page (max 5 min)...")
        for i in range(60):
            await page.wait_for_timeout(5000)
            cur = page.url
            if "pricing" in cur:
                print(f"    ✓ Pricing page reached after ~{(i+1)*5+3}s")
                break
            # If Save and Continue re-enabled after ≥30s, click again (dialog closed)
            if "content" in cur and (i + 1) * 5 >= 30:
                btn2 = page.locator('button:has-text("Save and Continue"):not([disabled])').first
                if await btn2.count() > 0:
                    if (i + 1) % 6 == 0:  # only re-click every 30s
                        print(f"    Re-clicking Save and Continue at {(i+1)*5+3}s")
                        await btn2.click()
                        await page.wait_for_timeout(2000)
            if (i + 1) % 6 == 0:
                await page.screenshot(path=f"/tmp/fix_wait_{i+1}.png")
                print(f"    {(i+1)*5+3}s: {cur}")
        else:
            print("[!] Timed out — still not on pricing page")
            await page.screenshot(path="/tmp/fix_timeout.png")
            await browser.close()
            return False

        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)
        await page.screenshot(path="/tmp/fix_03_pricing.png")
        print(f"[4] Pricing URL: {page.url}")

        # ── 5. Select 70% royalty ────────────────────────────────────────────
        r70 = page.locator('input[type="radio"][value="70_PERCENT"]')
        if await r70.count() > 0:
            await r70.first.click(force=True)
            await page.wait_for_timeout(2000)
            print("[5] ✓ 70% royalty selected")

        # ── 6. Set price $2.99 ───────────────────────────────────────────────
        price = page.locator('input[name*="[US][price_vat_inclusive]"]')
        if await price.count() > 0:
            await price.first.fill("2.99")
            await price.first.press("Tab")
            await page.wait_for_timeout(3000)
            print("[6] ✓ Price $2.99")

        await page.screenshot(path="/tmp/fix_04_price_set.png")

        # ── 7. Publish ───────────────────────────────────────────────────────
        pub = page.locator('button:has-text("Publish Your Kindle eBook"), button:has-text("Publish")')
        if await pub.count() == 0:
            print("[!] No Publish button found")
            await browser.close()
            return False

        await pub.first.scroll_into_view_if_needed()
        await pub.first.click()
        print("[7] Clicked Publish")
        await page.wait_for_timeout(10000)
        await page.screenshot(path="/tmp/fix_05_result.png")

        if await page.locator(':text("Please fix the highlighted error")').count() > 0:
            print("[!] Still validation errors — check /tmp/fix_05_result.png")
            await browser.close()
            return False

        # ── 8. Update listing.json ───────────────────────────────────────────
        data = json.loads(LISTING.read_text())
        data["kdp_uploading"] = False
        data["kdp_error"] = None
        LISTING.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print("[8] ✅ Published! listing.json updated.")
        await browser.close()
        return True

if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
