#!/usr/bin/env python3
"""
Full KDP login — login, send OTP, wait for user input, verify, save session.
All in one browser session (no state reload).

Usage: python3 kdp_login_full.py
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

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()

        print(f"Logging in as {KDP_EMAIL}...")
        await page.goto("https://kdp.amazon.com", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        signin_btn = await page.query_selector('button:has-text("Sign in"), a:has-text("Sign in")')
        if signin_btn:
            await signin_btn.click()
            await page.wait_for_timeout(2000)

        # Email
        for sel in ['input[type="email"]', 'input#ap_email', 'input[name="email"]']:
            try:
                await page.wait_for_selector(sel, timeout=5000)
                await page.fill(sel, KDP_EMAIL)
                print("✓ Email")
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
                print("✓ Password")
                break
            except:
                continue
        submit = await page.query_selector('button[type="submit"], input[type="submit"]')
        if submit:
            await submit.click()
        await page.wait_for_timeout(5000)

        # Check if on OTP page
        await page.screenshot(path="/tmp/kdp_2fa_start.png")
        content = await page.content()
        if "send otp" in content.lower() or "two-step" in content.lower() or "verification" in content.lower():
            totp_secret = ENV.get("KDP_TOTP_SECRET", "")

            code = None
            if totp_secret:
                # --- AUTO OTP via TOTP Authenticator App ---
                # Step 1: Select the TOTP radio — try multiple strategies
                totp_selected = False

                # Strategy A: by value attribute containing "TOTP"
                totp_radio = await page.query_selector('input[name="otpDeviceContext"][value*="TOTP"]')
                if not totp_radio:
                    totp_radio = await page.query_selector('input[value*="TOTP"], input[value*="totp"]')
                if totp_radio:
                    await totp_radio.click()
                    print("✓ Selected TOTP radio (by value)")
                    await page.wait_for_timeout(500)
                    totp_selected = True

                if not totp_selected:
                    # Strategy B: click via Playwright locator (text matching)
                    for text_pattern in ["Authenticator App", "Authenticator"]:
                        try:
                            loc = page.get_by_text(text_pattern, exact=False)
                            if await loc.count() > 0:
                                await loc.first.click()
                                print(f"✓ Selected TOTP via get_by_text({text_pattern!r})")
                                await page.wait_for_timeout(500)
                                totp_selected = True
                                break
                        except Exception:
                            continue

                if not totp_selected:
                    # Strategy C: click the LAST radio on the page (TOTP is always last)
                    try:
                        radios = await page.query_selector_all('input[type="radio"]')
                        if radios:
                            await radios[-1].click()
                            print(f"✓ Selected last radio button (TOTP fallback)")
                            await page.wait_for_timeout(500)
                            totp_selected = True
                    except Exception:
                        pass

                if not totp_selected:
                    print("⚠️ Could not find TOTP option — proceeding anyway")

                # Step 2: Click "Send OTP" submit button
                # Note: Amazon's submit button has value="" (empty), so use input[type="submit"]
                send_btn = await page.query_selector('input[type="submit"], button[type="submit"]')
                if send_btn and await send_btn.is_visible():
                    await send_btn.click()
                    print("✓ Clicked Send OTP")
                    # Wait for OTP code entry page to appear (not just a fixed 3s timeout)
                    try:
                        await page.wait_for_selector(
                            'input[name="otpCode"], input#auth-mfa-otpcode, input[autocomplete="one-time-code"], input[type="number"]',
                            timeout=10000
                        )
                        print("✓ OTP entry page loaded")
                    except Exception:
                        # Fallback: broader wait for any visible text input
                        try:
                            await page.wait_for_selector('input[type="text"]', timeout=5000)
                        except Exception:
                            await page.wait_for_timeout(5000)
                    await page.screenshot(path="/tmp/kdp_after_send_otp_totp.png")
                else:
                    print("⚠️ Send OTP button not found — cannot proceed")

                # Step 3: Generate TOTP code fresh (right before fill to minimize clock skew)
                import pyotp
                totp_obj = pyotp.TOTP(totp_secret)
                code = totp_obj.now()
                remaining = 30 - (__import__("time").time() % 30)
                print(f"✅ Auto-generated TOTP: {code} (valid {remaining:.0f}s)")

            else:
                # No TOTP secret — use WhatsApp/SMS fallback
                send_btn = await page.query_selector('button:has-text("Send OTP"), input[value*="Send OTP"]')
                if send_btn and await send_btn.is_visible():
                    await send_btn.click()
                    print("✅ OTP sent to phone/WhatsApp!")
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                await page.wait_for_timeout(2000)

                Path("/tmp/kdp_otp_ready").write_text("ready")
                print("WAITING_FOR_OTP")
                sys.stdout.flush()

                otp_file = Path("/tmp/kdp_otp_code")
                otp_file.unlink(missing_ok=True)

                for _ in range(120):
                    if otp_file.exists():
                        code = otp_file.read_text().strip()
                        if code:
                            print(f"Got OTP: {code}")
                            break
                    await asyncio.sleep(1)
                else:
                    print("❌ Timeout waiting for OTP")
                    await browser.close()
                    return

            # Fill OTP input
            await page.screenshot(path="/tmp/kdp_otp_entry_page.png")
            filled = False
            otp_selectors = [
                'input[name="otpCode"]',
                'input#auth-mfa-otpcode',
                'input[autocomplete="one-time-code"]',
                'input[type="number"]',
                'input[type="text"]',
            ]

            # Try named/id selectors first, then fallback to first visible input
            for sel in otp_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        await page.fill(sel, code, timeout=5000)
                        filled = True
                        print(f"✓ OTP entered ({sel})")
                        break
                except Exception:
                    continue

            if not filled:
                # Last resort: use locator to grab first visible input on page
                try:
                    await page.locator("input:visible").first.fill(code, timeout=5000)
                    filled = True
                    print("✓ OTP entered (first visible input)")
                except Exception:
                    pass

            if not filled:
                await page.screenshot(path="/tmp/kdp_otp_fill_debug.png")
                text = await page.evaluate("() => document.body.innerText.slice(0, 500)")
                print(text)
                print("❌ Login failed — cannot fill OTP. Check /tmp/kdp_otp_fill_debug.png")
                await browser.close()
                return

            # Submit OTP
            submit = await page.query_selector('button[type="submit"], input[type="submit"], #auth-signin-button')
            if submit:
                await submit.click()
            await page.wait_for_timeout(5000)

        # Check success
        current_url = page.url
        print(f"Final URL: {current_url}")

        if "kdp.amazon.com" in current_url and "/ap/" not in current_url and "signin" not in current_url:
            await context.storage_state(path=str(SESSION_FILE))
            print(f"✅ Session saved! ({SESSION_FILE})")
        else:
            await page.screenshot(path="/tmp/kdp_login_result.png")
            text = await page.evaluate("() => document.body.innerText.slice(0, 500)")
            print(f"⚠️ Login status unclear")
            print(text[:300])

        # Cleanup
        Path("/tmp/kdp_otp_ready").unlink(missing_ok=True)
        Path("/tmp/kdp_otp_code").unlink(missing_ok=True)

        await browser.close()

asyncio.run(main())
