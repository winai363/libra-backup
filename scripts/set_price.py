"""
set_price.py — Change ONLY the list price of an already-LIVE Kindle ebook.

Goes straight to the pricing tab (no details/content pages touched), fills the
US list-price input, and publishes. Content and metadata are untouched — the
minimal possible interaction with a live book, so it does not re-trigger a
content review the way EPUB/cover re-uploads do.

Usage:
  python3 scripts/set_price.py <slug> <price>            # e.g. 2.99
  python3 scripts/set_price.py <slug> <price> --dry-run  # fill input, screenshot, DON'T publish
"""
import asyncio
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

LIBRA = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIBRA))
from kdp_freeze import assert_kdp_mutation_allowed  # noqa: E402

from kdp_upload import (  # noqa: E402
    KDP_DIR, SESSION_FILE, logger, wait_for_publish_confirmation,
)


def tg(msg):
    # Same pattern as free_promo_auto.tg (NOT imported — that module runs on import).
    import os
    import urllib.parse
    import urllib.request

    def _env(k):
        v = os.getenv(k)
        if v:
            return v
        envf = LIBRA / ".env"
        if envf.exists():
            for line in envf.read_text().splitlines():
                if line.startswith(k + "="):
                    return line.split("=", 1)[1].strip()
        return None

    tok, chat = _env("TELEGRAM_BOT_TOKEN"), _env("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return
    payload = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=payload, timeout=20)
    except Exception as e:
        print("telegram failed:", e)

PRICE_SELECTORS = [
    'input[name*="[US][list_price]"]', 'input[name*="[US][price"]',
    'input[name*="list_price"][name*="US"]',
]


def _is_signin(url: str) -> bool:
    # Path-only check: post-login URLs carry "signin" in the query string.
    from urllib.parse import urlparse
    path = urlparse(url).path
    return "signin" in path or "/ap/" in path


async def set_price(
    slug: str, price: str, dry_run: bool, evidence_out: dict | None = None
) -> bool:
    assert_kdp_mutation_allowed("price")
    from playwright.async_api import async_playwright

    listing_file = KDP_DIR / slug / "listing.json"
    data = json.loads(listing_file.read_text())
    book_id = data.get("kdp_book_id")
    if not book_id:
        logger.error("No kdp_book_id in listing.json")
        return False
    pricing_url = f"https://kdp.amazon.com/en_US/title-setup/kindle/{book_id}/pricing"

    async with async_playwright() as p:
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=str(SESSION_FILE), user_agent=ua)
        page = await context.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => false})")
        try:
            await page.goto(pricing_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(4000)

            if _is_signin(page.url):
                logger.warning("Session expired — re-logging in...")
                await browser.close()
                r = subprocess.run(
                    ["python3", str(LIBRA / "kdp_login_full.py")],
                    capture_output=True, text=True, timeout=180)
                if "Session saved" not in r.stdout:
                    logger.error(f"Re-login failed: {r.stdout[-200:]}")
                    return False
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    storage_state=str(SESSION_FILE), user_agent=ua)
                page = await context.new_page()
                await page.goto(pricing_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(4000)
                if _is_signin(page.url):
                    logger.error("Still on signin after re-login")
                    return False

            if "/pricing" not in page.url:
                logger.error(f"Did not land on pricing page (url={page.url}) — "
                             "book may be In Review; try again later")
                return False

            # Fill the US list-price input (same selector cascade as kdp_upload).
            price_set = False
            old_val = ""
            for sel in PRICE_SELECTORS:
                try:
                    inp = page.locator(sel).first
                    if await inp.count() > 0 and await inp.is_visible():
                        old_val = await inp.input_value()
                        await inp.fill(price)
                        await inp.press("Tab")
                        price_set = True
                        logger.info(f"✓ Price {old_val} → {price} via {sel}")
                        break
                except Exception:
                    continue
            if not price_set:
                for inp in await page.query_selector_all('input[type="text"]'):
                    name = (await inp.get_attribute("name")) or ""
                    if ("price" in name.lower() or "list_price" in name.lower()) \
                            and await inp.is_visible():
                        old_val = await inp.input_value()
                        await inp.fill(price)
                        await inp.evaluate(
                            'el => el.dispatchEvent(new Event("blur",{bubbles:true}))')
                        price_set = True
                        logger.info(f"✓ Price {old_val} → {price} via name={name}")
                        break
            if not price_set:
                await page.screenshot(path=f"/tmp/set_price_noinput_{slug}.png")
                logger.error("Price input not found — screenshot saved")
                return False

            # Force 70% royalty plan via a user-like click on the radio label.
            # NOTE (2026-07-03 investigation): while a Free Promotion is active,
            # KDP disables the royalty binding (`jele-binding-disabled`) — the
            # radio LOOKS clickable but the model stays at 35% and the row keeps
            # showing "35% / $1.05". Publishing in that state would save 35%.
            # So: click, then HARD-VERIFY the row cell flipped to 70% below.
            try:
                lab70 = page.locator(
                    'div[data-a-input-name="data[digital][royalty_rate]-radio"]'
                ).filter(has_text="70%")
                if await lab70.count() > 0:
                    await lab70.first.click()
                    await page.wait_for_timeout(3000)
            except Exception as e:
                logger.warning(f"70% radio click: {e}")

            await page.wait_for_timeout(2000)
            await page.screenshot(path=f"/tmp/set_price_filled_{slug}.png")

            # SAFETY GATE — the Rate cell in the marketplace row is the model's
            # true state. Exactly one bare "35%" text node exists page-wide when the
            # rate is stuck at 35% (verified 2026-07-03). If it's still there,
            # abort rather than publish a royalty downgrade.
            still_35 = await page.locator("text=/^35%$/").count()
            if still_35 > 0:
                logger.error(
                    f"ABORT: rate cell still shows 35% ({still_35} node) — royalty "
                    "binding likely locked (active promo?) — NOT publishing. "
                    f"Screenshot: /tmp/set_price_filled_{slug}.png")
                return False
            logger.info("✓ Rate cell no longer 35% — 70% plan active")

            if dry_run:
                logger.info(f"DRY-RUN: input filled ({old_val} → {price}), NOT publishing. "
                            f"Screenshot: /tmp/set_price_filled_{slug}.png")
                return True

            btn = page.locator(
                'button:has-text("Save and Publish"), button:has-text("Publish")')
            if await btn.count() == 0:
                logger.error("Publish button not found")
                return False
            await btn.first.scroll_into_view_if_needed()
            await btn.first.click()
            logger.info("✓ Clicked publish; waiting for confirmation")
            if not await wait_for_publish_confirmation(page):
                logger.error(f"No publish confirmation (url={page.url})")
                return False

            confirmation_text = "publish confirmation observed"
            try:
                body = await page.locator("body").inner_text(timeout=5000)
                confirmation_text = next(
                    (line.strip() for line in body.splitlines()
                     if any(word in line.lower() for word in ("publish", "review", "submitted"))
                     and line.strip()),
                    confirmation_text,
                )[:300]
            except Exception:
                pass
            after_shot = f"/tmp/set_price_confirmed_{slug}.png"
            await page.screenshot(path=after_shot, full_page=True)
            confirmed_at = datetime.now().isoformat(timespec="seconds")
            if evidence_out is not None:
                evidence_out.update({
                    "pricing_url": pricing_url,
                    "before": {"price": old_val, "source": "kdp_price_input"},
                    "after": {"price": str(price), "confirmation": confirmation_text},
                    "confirmation_url": page.url,
                    "confirmed_at": confirmed_at,
                    "screenshot": after_shot,
                })

            data["price"] = float(price)
            data["price_updated_at"] = confirmed_at
            listing_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            logger.info(f"✅ Price updated: {slug} {old_val} → ${price}")
            return True
        finally:
            await browser.close()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    slug, price = sys.argv[1], sys.argv[2]
    dry = "--dry-run" in sys.argv
    ok = asyncio.run(set_price(slug, price, dry))
    if not dry:
        tg(f"Libra set_price {slug} → ${price}: {'✅ done' if ok else '❌ FAILED (see logs)'}")
    sys.exit(0 if ok else 1)
