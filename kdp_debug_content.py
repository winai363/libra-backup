#!/usr/bin/env python3
"""
Debug script — inspect KDP content page DOM for AI tools and confirm checkbox.
Goes to first draft found, dumps HTML of critical sections, saves screenshots.
"""
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

from playwright.async_api import async_playwright

SESSION_FILE = Path(__file__).parent / "kdp_session.json"

async def debug():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            storage_state=str(SESSION_FILE),
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()

        # Go to bookshelf
        print("→ Going to bookshelf...")
        await page.goto("https://kdp.amazon.com/en_US/bookshelf", wait_until="networkidle")
        print(f"  URL: {page.url}")

        if "signin" in page.url or "/ap/" in page.url:
            print("❌ Session expired!")
            await browser.close()
            return

        # Find a draft to inspect
        continue_btn = await page.query_selector('a:has-text("Continue setup"), button:has-text("Continue setup")')
        if not continue_btn:
            print("No draft found. Looking for any book to edit...")
            # Try clicking Actions > Edit on first book
            await page.screenshot(path="/tmp/kdp_bookshelf.png")
            print("Screenshot saved: /tmp/kdp_bookshelf.png")

            # Dump page text to see what's there
            text = await page.evaluate("() => document.body.innerText.slice(0, 3000)")
            print("--- BOOKSHELF TEXT ---")
            print(text)
            await browser.close()
            return

        print("→ Found draft, opening content page...")
        await continue_btn.click()
        await page.wait_for_timeout(3000)
        print(f"  URL after continue: {page.url}")

        # If still on details page, save and continue
        if "/content" not in page.url:
            print("→ On details page, saving to get to content...")
            await page.get_by_text("Save and Continue", exact=False).first.click()
            await page.wait_for_timeout(5000)
            print(f"  URL after save: {page.url}")

        if "/content" not in page.url:
            print("❌ Could not reach content page")
            await page.screenshot(path="/tmp/kdp_debug_stuck.png")
            await browser.close()
            return

        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(3000)
        await page.screenshot(path="/tmp/kdp_content_full.png")
        print("✅ On content page")

        # Dump AI tools section HTML
        ai_html = await page.evaluate('''() => {
            const selectors = [
                "#generative-ai-questionnaire-text",
                "#generative-ai-questionnaire-images",
                "#generative-ai-questionnaire-translations"
            ];
            let out = "";
            for (const s of selectors) {
                const el = document.querySelector(s);
                if (el) {
                    // Go up 4 levels to get surrounding container
                    let parent = el;
                    for (let i = 0; i < 4; i++) parent = parent.parentElement || parent;
                    out += "\\n=== " + s + " ===\\n" + parent.outerHTML.slice(0, 2000) + "\\n";
                } else {
                    out += "\\n=== " + s + " === NOT FOUND\\n";
                }
            }
            return out;
        }''')
        print("\n--- AI TOOLS HTML ---")
        print(ai_html[:5000])

        # Dump all role="checkbox" elements
        role_checkboxes = await page.evaluate('''() => {
            const els = document.querySelectorAll('[role="checkbox"]');
            let out = "";
            els.forEach((el, i) => {
                let parent = el;
                for (let j = 0; j < 3; j++) parent = parent.parentElement || parent;
                out += "\\n=== role=checkbox #" + i + " ===\\n";
                out += "aria-checked: " + el.getAttribute("aria-checked") + "\\n";
                out += "text: " + el.textContent.slice(0, 200) + "\\n";
                out += parent.outerHTML.slice(0, 1000) + "\\n";
            });
            return out || "NONE FOUND";
        }''')
        print("\n--- ROLE=CHECKBOX ELEMENTS ---")
        print(role_checkboxes[:5000])

        # Check all input checkboxes
        input_checkboxes = await page.evaluate('''() => {
            const els = document.querySelectorAll('input[type="checkbox"]');
            let out = "";
            els.forEach((el, i) => {
                let parent = el;
                for (let j = 0; j < 2; j++) parent = parent.parentElement || parent;
                out += "\\n=== input[checkbox] #" + i + " ===\\n";
                out += "id: " + el.id + ", name: " + el.name + ", checked: " + el.checked + "\\n";
                out += parent.outerHTML.slice(0, 500) + "\\n";
            });
            return out || "NONE FOUND";
        }''')
        print("\n--- INPUT CHECKBOXES ---")
        print(input_checkboxes[:5000])

        # Look for "confirm" related text
        confirm_html = await page.evaluate('''() => {
            const body = document.body.innerHTML;
            const idx = body.toLowerCase().indexOf("confirm");
            if (idx === -1) return "NO 'confirm' text found in body";
            return body.slice(Math.max(0, idx-200), idx+1000);
        }''')
        print("\n--- CONFIRM AREA HTML ---")
        print(confirm_html[:3000])

        # Check Amazon dropdown triggers
        dropdown_triggers = await page.evaluate('''() => {
            const triggers = document.querySelectorAll('.a-dropdown-trigger, [id$="-button"]');
            let out = "";
            triggers.forEach((el, i) => {
                if (i < 10) {
                    out += "\\n#" + i + ": " + el.tagName + " id=" + el.id + " class=" + el.className.slice(0,50) + "\\n";
                    out += "  text: " + el.textContent.trim().slice(0,100) + "\\n";
                }
            });
            return out || "NONE";
        }''')
        print("\n--- AMAZON DROPDOWN TRIGGERS ---")
        print(dropdown_triggers[:3000])

        await browser.close()
        print("\n✅ Debug complete. Screenshots in /tmp/kdp_*.png")

asyncio.run(debug())
