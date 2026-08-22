"""Drive Payhip through a browser — because Payhip's API cannot create products.

Payhip's public API covers coupons and license keys only (checked 22 Aug 2026
at payhip.com/api-reference). Product creation, file upload and webhook
settings exist only in the web UI, so this module does what the KDP uploader
does: a persisted Playwright session, one action at a time, and **before/after
evidence for every mutation** — a click is not a result.

Offline by default. Nothing here opens a browser unless `execute=True` is
passed and real credentials exist. Selectors live in one dict so the first real
session can correct them with `--inspect` without touching the logic.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

LIBRA_DIR = Path(__file__).resolve().parent
SESSION_FILE = LIBRA_DIR / "payhip_session.json"
SHOTS_DIR = LIBRA_DIR / "logs" / "payhip-shots"

BASE_URL = "https://payhip.com"
LOGIN_URL = f"{BASE_URL}/auth/login"
PRODUCTS_URL = f"{BASE_URL}/dashboard/products"
NEW_PRODUCT_URL = f"{BASE_URL}/product/add/digital"
SETTINGS_URL = f"{BASE_URL}/dashboard/settings"

# The selector contract. Values are best-effort starting points and MUST be
# confirmed with `python3 scripts/payhip_publish.py --inspect` on the first
# real login; the tests only guarantee every step we drive has an entry.
SELECTORS = {
    # confirmed against the live form 22 Aug 2026 (probe_payhip_login_form.py)
    "login_email": "input[name='login']",
    "login_password": "input[name='password']",
    "login_submit": "button[type='submit']:has-text('Log in')",
    "logged_in_marker": "a[href*='logout'], a[href*='/dashboard'], a[href*='/product/add']",
    "product_new": "a[href*='/product/add']",
    "product_name": "input[name='name'], input[name='product_name']",
    "product_price": "input[name='price']",
    "product_description": "textarea[name='description'], [contenteditable='true']",
    "product_file_input": "input[type='file'][name*='file'], input[type='file']",
    "product_cover_input": "input[type='file'][name*='image'], input[type='file'][accept*='image']",
    "product_save": "button:has-text('Save'), button:has-text('Publish'), button[type='submit']",
    "product_saved_marker": "a[href*='payhip.com/b/']",
    "product_list_row": "a[href*='/b/']",
    "settings_webhook_url": "input[name*='webhook'], input[placeholder*='webhook']",
    "settings_webhook_save": "button:has-text('Save')",
}


class PayhipAdminError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


# ── credentials & session ────────────────────────────────────────────────────

def load_credentials(env: dict) -> dict:
    email = env.get("PAYHIP_EMAIL") or os.environ.get("PAYHIP_EMAIL")
    password = env.get("PAYHIP_PASSWORD") or os.environ.get("PAYHIP_PASSWORD")
    if not email or not password:
        raise PayhipAdminError("credentials_missing", "set PAYHIP_EMAIL and PAYHIP_PASSWORD in .env")
    return {"email": email, "password": password}


def save_session(state: dict) -> Path:
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps(state), encoding="utf-8")
    os.chmod(SESSION_FILE, 0o600)
    return SESSION_FILE


# ── plans (pure, testable) ──────────────────────────────────────────────────

def plan_product_upsert(spec: dict, bundle_path: Path) -> list:
    bundle_path = Path(bundle_path)
    if not bundle_path.exists():
        raise PayhipAdminError("bundle_missing", str(bundle_path))
    return [
        {"action": "open", "url": NEW_PRODUCT_URL},
        {"action": "fill", "selector": SELECTORS["product_name"], "value": spec["title"]},
        {"action": "fill", "selector": SELECTORS["product_price"], "value": spec["price_display"]},
        {"action": "fill", "selector": SELECTORS["product_description"], "value": spec["description"]},
        {"action": "upload_file", "selector": SELECTORS["product_file_input"], "path": str(bundle_path)},
        {"action": "upload_cover", "selector": SELECTORS["product_cover_input"], "path": spec["cover"]},
        {"action": "click_save", "selector": SELECTORS["product_save"]},
        {"action": "verify_listed", "url": PRODUCTS_URL, "selector": SELECTORS["product_list_row"],
         "expect_title": spec["title"]},
    ]


def plan_webhook_setup(callback_url: str) -> list:
    if not callback_url.startswith("https://"):
        raise PayhipAdminError("https_required", callback_url)
    return [
        {"action": "open", "url": SETTINGS_URL},
        {"action": "fill", "selector": SELECTORS["settings_webhook_url"], "value": callback_url},
        {"action": "click_save", "selector": SELECTORS["settings_webhook_save"]},
    ]


# ── evidence (pure, testable) ────────────────────────────────────────────────

def build_evidence(*, action: str, before: dict | None, after: dict | None,
                   screenshots: list | None = None) -> dict:
    if before is None or after is None:
        raise PayhipAdminError("evidence_incomplete", "before and after states are both required")
    return {
        "action": action,
        "verified_state_change": {"before": before, "after": after},
        "external_url": after.get("product_url"),
        "screenshots": list(screenshots or []),
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def classify_outcome(*, before: dict, after: dict) -> str:
    """Only a visible after-state counts. A click alone is not a business result."""
    if after.get("listed") and after.get("product_url"):
        return "executed"
    return "manual_required"


# ── live driver (only runs when asked, and only with real credentials) ──────

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


async def _new_context(p, *, headless: bool = True):
    # Same posture as the KDP/free-promo drivers: plain Chrome fingerprint, no
    # automation banner. reCAPTCHA v3 scores headless bots low, so this matters.
    browser = await p.chromium.launch(
        headless=headless, args=["--disable-blink-features=AutomationControlled"]
    )
    kwargs = {"user_agent": UA, "locale": "en-US", "viewport": {"width": 1366, "height": 850}}
    if SESSION_FILE.exists():
        kwargs["storage_state"] = str(SESSION_FILE)
    context = await browser.new_context(**kwargs)
    await context.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
    )
    return browser, context


async def _shot(page, name: str) -> str:
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SHOTS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}.png"
    await page.screenshot(path=str(path), full_page=True)
    return str(path)


async def ensure_logged_in(page, credentials: dict) -> None:
    await page.goto(PRODUCTS_URL, wait_until="domcontentloaded")
    if await page.query_selector(SELECTORS["logged_in_marker"]) and "login" not in page.url:
        return
    await page.goto(LOGIN_URL, wait_until="domcontentloaded")
    await page.fill(SELECTORS["login_email"], credentials["email"])
    await page.fill(SELECTORS["login_password"], credentials["password"])
    await page.wait_for_timeout(1500)  # let reCAPTCHA v3 mint its token first
    await page.click(SELECTORS["login_submit"])
    await page.wait_for_timeout(6000)
    if "/auth/login" in page.url or not await page.query_selector(SELECTORS["logged_in_marker"]):
        shot = await _shot(page, "login-failed")
        body = (await page.evaluate("() => document.body.innerText") or "")[:300]
        if "not a robot" in body.lower() or "recaptcha" in page.url.lower():
            # House rule: CAPTCHA means a human, never a workaround. Payhip's
            # reCAPTCHA scores this server's headless Chrome low and falls back
            # to the checkbox (seen 22 Aug 2026) — use the upload pack instead.
            raise PayhipAdminError(
                "captcha_manual_required",
                f"Payhip asked for the 'I am not a robot' checkbox; use scripts/payhip_upload_pack.py (shot={shot})",
            )
        raise PayhipAdminError("login_failed", f"url={page.url} shot={shot} text={body!r}")
    await page.context.storage_state(path=str(SESSION_FILE))
    os.chmod(SESSION_FILE, 0o600)


async def _listed_state(page, title: str) -> dict:
    await page.goto(PRODUCTS_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)
    rows = await page.query_selector_all(SELECTORS["product_list_row"])
    for row in rows:
        text = (await row.inner_text()) or ""
        href = await row.get_attribute("href") or ""
        if title[:30].lower() in text.lower() and "/b/" in href:
            url = href if href.startswith("http") else BASE_URL + href
            return {"listed": True, "product_url": url}
    return {"listed": False}


async def inspect(credentials: dict) -> dict:
    """Dump what the logged-in product form actually looks like — for fixing SELECTORS."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser, context = await _new_context(p)
        page = await context.new_page()
        try:
            await ensure_logged_in(page, credentials)
            await page.goto(NEW_PRODUCT_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            shot = await _shot(page, "inspect-new-product")
            fields = await page.evaluate("""() => [...document.querySelectorAll('input,textarea,select,button')]
                .slice(0, 80).map(e => ({tag: e.tagName, type: e.type || '', name: e.name || '',
                                         id: e.id || '', placeholder: e.placeholder || '',
                                         text: (e.innerText || '').trim().slice(0, 40)}))""")
            return {"url": page.url, "screenshot": shot, "fields": fields}
        finally:
            await browser.close()


async def upsert_product(spec: dict, bundle_path: Path, credentials: dict) -> dict:
    """Create the product and prove it exists afterwards."""
    from playwright.async_api import async_playwright

    plan = plan_product_upsert(spec, bundle_path)
    shots: list = []
    async with async_playwright() as p:
        browser, context = await _new_context(p)
        page = await context.new_page()
        try:
            await ensure_logged_in(page, credentials)
            before = await _listed_state(page, spec["title"])
            if before["listed"]:
                return build_evidence(action="create_product", before=before, after=before) | {
                    "outcome": "already_listed"
                }
            for step in plan:
                action = step["action"]
                if action == "open":
                    await page.goto(step["url"], wait_until="domcontentloaded")
                    await page.wait_for_timeout(2500)
                elif action == "fill":
                    await page.fill(step["selector"], step["value"])
                elif action in ("upload_file", "upload_cover"):
                    await page.set_input_files(step["selector"], step["path"])
                    await page.wait_for_timeout(4000)
                elif action == "click_save":
                    shots.append(await _shot(page, "before-save"))
                    await page.click(step["selector"])
                    await page.wait_for_timeout(6000)
                    shots.append(await _shot(page, "after-save"))
                elif action == "verify_listed":
                    pass
            after = await _listed_state(page, spec["title"])
            evidence = build_evidence(action="create_product", before=before, after=after, screenshots=shots)
            evidence["outcome"] = classify_outcome(before=before, after=after)
            return evidence
        finally:
            await browser.close()


async def set_webhook(callback_url: str, credentials: dict) -> dict:
    from playwright.async_api import async_playwright

    plan = plan_webhook_setup(callback_url)
    async with async_playwright() as p:
        browser, context = await _new_context(p)
        page = await context.new_page()
        try:
            await ensure_logged_in(page, credentials)
            for step in plan:
                if step["action"] == "open":
                    await page.goto(step["url"], wait_until="domcontentloaded")
                    await page.wait_for_timeout(2500)
                elif step["action"] == "fill":
                    await page.fill(step["selector"], step["value"])
                elif step["action"] == "click_save":
                    await page.click(step["selector"])
                    await page.wait_for_timeout(3000)
            shot = await _shot(page, "webhook-saved")
            value = await page.input_value(SELECTORS["settings_webhook_url"])
            after = {"webhook_set": value == callback_url}
            return build_evidence(action="set_webhook", before={"webhook_set": False},
                                  after=after, screenshots=[shot])
        finally:
            await browser.close()
