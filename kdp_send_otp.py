#!/usr/bin/env python3
"""
Send OTP via WhatsApp or Call, then wait for user to provide the code.
Usage:
    python3 kdp_send_otp.py              # send via WhatsApp (default)
    python3 kdp_send_otp.py call         # send via phone call
    python3 kdp_send_otp.py verify 123456  # verify OTP code
"""
import asyncio
import sys
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
SESSION_FILE = Path("/root/libra/kdp_session.json")
PRE_OTP_FILE = Path("/tmp/kdp_pre_otp.json")

async def login_and_send_otp(method="whatsapp"):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()

        print(f"Logging in as {KDP_EMAIL}...")
        await page.goto("https://kdp.amazon.com", wait_until="networkidle")

        signin_btn = await page.query_selector('button:has-text("Sign in"), a:has-text("Sign in")')
        if signin_btn:
            await signin_btn.click()
            await page.wait_for_timeout(2000)

        # Email
        for sel in ['input[type="email"]', 'input#ap_email', 'input[name="email"]']:
            try:
                await page.wait_for_selector(sel, timeout=5000)
                await page.fill(sel, KDP_EMAIL)
                print("✓ Email filled")
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
                print("✓ Password filled")
                break
            except:
                continue
        submit = await page.query_selector('button[type="submit"], input[type="submit"]')
        if submit:
            await submit.click()
        await page.wait_for_timeout(5000)

        # Now on OTP page — select method
        if method == "call":
            call_radio = await page.query_selector('input[type="radio"][value*="CALL"], input[type="radio"][value*="call"]')
            if not call_radio:
                # Try by label text
                radios = await page.query_selector_all('input[type="radio"]')
                for r in radios:
                    parent = await r.evaluate_handle("el => el.parentElement")
                    text = await parent.evaluate("el => el.textContent")
                    if "call" in text.lower():
                        call_radio = r
                        break
            if call_radio:
                await call_radio.click(force=True)
                print("✓ Selected: Call")
            else:
                print("⚠️ Call option not found, using WhatsApp")
        else:
            print("✓ Using WhatsApp (default)")

        # Click "Send OTP" button
        send_btn = await page.query_selector('button:has-text("Send OTP"), input[type="submit"]')
        if not send_btn:
            send_btn = await page.query_selector('#auth-send-code')
        if send_btn:
            await send_btn.click()
            print("✅ OTP SENT! Check your phone.")
        else:
            print("⚠️ Send OTP button not found")
            await page.screenshot(path="/tmp/kdp_otp_debug.png")

        await page.wait_for_timeout(3000)
        await page.screenshot(path="/tmp/kdp_after_send_otp.png")

        # Save state for verify step
        await context.storage_state(path=str(PRE_OTP_FILE))
        print(f"\nURL: {page.url}")
        print(f"\nรอรับ OTP แล้วรัน:")
        print(f"  python3 {__file__} verify <OTP_CODE>")

        await browser.close()


async def verify_otp(code: str):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            storage_state=str(PRE_OTP_FILE),
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()

        print(f"Submitting OTP: {code}")
        await page.goto("https://www.amazon.com/ap/mfa", wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # Fill OTP
        filled = False
        for sel in ['input[name="otpCode"]', 'input#auth-mfa-otpcode', 'input[type="text"]', 'input[autocomplete="one-time-code"]']:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await page.fill(sel, code)
                    filled = True
                    print("✓ OTP entered")
                    break
            except:
                continue

        if not filled:
            await page.screenshot(path="/tmp/kdp_otp_verify_debug.png")
            print("⚠️ OTP input not found")
            text = await page.evaluate("() => document.body.innerText.slice(0, 1000)")
            print(text)
            await browser.close()
            return

        # Submit
        submit = await page.query_selector('button[type="submit"], input[type="submit"], #auth-signin-button')
        if submit:
            await submit.click()
        await page.wait_for_timeout(5000)

        # Check success
        current_url = page.url
        print(f"URL after OTP: {current_url}")

        if "kdp.amazon.com" in current_url and "/ap/" not in current_url and "signin" not in current_url:
            await context.storage_state(path=str(SESSION_FILE))
            print(f"\n✅ Login success! Session saved to {SESSION_FILE}")
        else:
            await page.screenshot(path="/tmp/kdp_otp_result.png")
            text = await page.evaluate("() => document.body.innerText.slice(0, 1000)")
            print(f"⚠️ May need further action. Screenshot: /tmp/kdp_otp_result.png")
            print(text[:500])

        await browser.close()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "verify":
        asyncio.run(verify_otp(sys.argv[2]))
    elif len(sys.argv) >= 2 and sys.argv[1] == "call":
        asyncio.run(login_and_send_otp("call"))
    else:
        asyncio.run(login_and_send_otp("whatsapp"))
