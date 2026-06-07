#!/usr/bin/env python3
"""Debug: screenshot the OTP page to see available options"""
import asyncio
from pathlib import Path

ENV = {}
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            ENV[k.strip()] = v.strip()

KDP_EMAIL = ENV.get("KDP_EMAIL", "")
KDP_PASSWORD = ENV.get("KDP_PASSWORD", "")

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()
        await page.goto("https://kdp.amazon.com", wait_until="networkidle")

        # Sign in
        signin_btn = await page.query_selector('button:has-text("Sign in"), a:has-text("Sign in")')
        if signin_btn:
            await signin_btn.click()
            await page.wait_for_timeout(2000)

        # Email
        for sel in ['input[type="email"]', 'input#ap_email', 'input[name="email"]']:
            try:
                await page.wait_for_selector(sel, timeout=5000)
                await page.fill(sel, KDP_EMAIL)
                break
            except:
                continue
        submit = await page.query_selector('button[type="submit"], input[type="submit"]')
        if submit:
            await submit.click()
        await page.wait_for_timeout(2000)

        # Password
        for sel in ['input[type="password"]', 'input#ap_password']:
            try:
                await page.wait_for_selector(sel, timeout=5000)
                await page.fill(sel, KDP_PASSWORD)
                break
            except:
                continue
        submit = await page.query_selector('button[type="submit"], input[type="submit"]')
        if submit:
            await submit.click()
        await page.wait_for_timeout(5000)

        # Screenshot the OTP/verification page
        await page.screenshot(path="/tmp/kdp_otp_page.png", full_page=True)
        print(f"URL: {page.url}")

        # Dump all text
        text = await page.evaluate("() => document.body.innerText")
        print("--- PAGE TEXT ---")
        print(text[:3000])

        # Dump all links/buttons
        links = await page.evaluate('''() => {
            const items = [];
            document.querySelectorAll('a, button, input[type="submit"]').forEach(el => {
                const text = el.textContent?.trim() || el.value || '';
                if (text) items.push(el.tagName + ': ' + text.slice(0, 100));
            });
            return items.join('\\n');
        }''')
        print("--- LINKS/BUTTONS ---")
        print(links)

        await browser.close()

asyncio.run(main())
