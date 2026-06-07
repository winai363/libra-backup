#!/usr/bin/env python3
"""
Login to Amazon KDP and save session to skip OTP in future.

Usage:
    # Step 1: run without OTP first (triggers OTP email/SMS)
    python3 kdp_login_setup.py

    # Step 2: run with OTP from email/SMS
    python3 kdp_login_setup.py 123456
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


async def main():
    from playwright.async_api import async_playwright

    otp_code = sys.argv[1].strip() if len(sys.argv) > 1 else None

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )

        if otp_code and PRE_OTP_FILE.exists():
            # Step 2: load pre-OTP state and submit OTP
            print(f"Submitting OTP: {otp_code}")
            context = await browser.new_context(
                storage_state=str(PRE_OTP_FILE),
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            )
            page = await context.new_page()

            # Go back to KDP which should still be on OTP page
            await page.goto("https://kdp.amazon.com", wait_until="networkidle")
            await page.wait_for_timeout(2000)

            content = await page.content()
            otp_keywords = ["otp", "one-time", "verification code", "two-step", "authenticator", "enter the code"]
            needs_otp = any(k in content.lower() for k in otp_keywords)

            if needs_otp:
                for selector in ['input[type="text"]', 'input[name="otpCode"]', 'input[placeholder*="code" i]', 'input[autocomplete="one-time-code"]']:
                    try:
                        await page.wait_for_selector(selector, timeout=3000)
                        await page.fill(selector, otp_code)
                        print("✓ OTP entered")
                        break
                    except:
                        continue

                submit = await page.query_selector('button[type="submit"], input[type="submit"]')
                if submit:
                    await submit.click()
                    await page.wait_for_timeout(3000)
            else:
                print("⚠️ OTP page not found, may have expired. Re-running login...")
                await browser.close()
                sys.argv = [sys.argv[0]]  # reset args
                await main()
                return

        else:
            # Step 1: fresh login (will trigger OTP)
            print(f"Logging in as {KDP_EMAIL}...")
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            )
            page = await context.new_page()
            await page.goto("https://kdp.amazon.com", wait_until="networkidle")

            # Click sign in if needed
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
            await page.wait_for_timeout(3000)

            # Check if OTP needed
            content = await page.content()
            if any(k in content.lower() for k in ["otp", "one-time", "verification code", "two-step"]):
                # Save state so Step 2 can continue from here
                await context.storage_state(path=str(PRE_OTP_FILE))
                print()
                print(">>> Amazon ส่ง OTP มาแล้ว <<<")
                print("กรุณาดูที่ email หรือ SMS แล้วรัน:")
                print()
                print(f"    python3 {__file__} <OTP_CODE>")
                print()
                print("เช่น: python3 kdp_login_setup.py 123456")
                await browser.close()
                return
            else:
                print("✓ No OTP needed, direct login")

        # Check login success
        current_url = page.url
        if "kdp.amazon.com" in current_url and "/ap/" not in current_url and "signin" not in current_url:
            await context.storage_state(path=str(SESSION_FILE))
            print()
            print(f"✅ Session saved! ({SESSION_FILE})")
            print("kdp_upload.py จะใช้ session นี้โดยอัตโนมัติ ไม่ต้อง OTP อีก")
        else:
            await page.screenshot(path="/tmp/kdp_login_debug.png")
            print(f"⚠️ Login may have failed. Current URL: {current_url}")
            print("Screenshot saved: /tmp/kdp_login_debug.png")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
