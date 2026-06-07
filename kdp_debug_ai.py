#!/usr/bin/env python3
"""Debug: inspect AI tools section visibility and structure on content page"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

SESSION_FILE = Path(__file__).parent / "kdp_session.json"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            storage_state=str(SESSION_FILE),
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()

        # Go to content page directly
        await page.goto("https://kdp.amazon.com/en_US/title-setup/kindle/A1XSA2OH5H2JIY/content", wait_until="networkidle")
        await page.wait_for_timeout(3000)
        print(f"URL: {page.url}")

        # Find the AI tools section — dump parent structure
        ai_section = await page.evaluate('''() => {
            // Look for the AI questionnaire container
            const sel = document.getElementById("generative-ai-questionnaire-text");
            if (!sel) return "SELECT NOT FOUND";

            // Walk up to find the whole AI section
            let el = sel;
            for (let i = 0; i < 10; i++) {
                el = el.parentElement;
                if (!el) break;
            }

            // Get the section HTML (limited)
            return el ? el.outerHTML.slice(0, 5000) : "PARENT NOT FOUND";
        }''')
        print("--- AI SECTION HTML (top-level) ---")
        print(ai_section[:5000])

        # Check visibility of each element in the chain
        vis_chain = await page.evaluate('''() => {
            const sel = document.getElementById("generative-ai-questionnaire-text");
            if (!sel) return "NOT FOUND";
            let out = "";
            let el = sel;
            let depth = 0;
            while (el && depth < 15) {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                out += `\\n[${depth}] ${el.tagName}.${(el.className||'').toString().slice(0,50)} ` +
                       `display=${style.display} visibility=${style.visibility} ` +
                       `opacity=${style.opacity} ` +
                       `rect=${Math.round(rect.width)}x${Math.round(rect.height)} ` +
                       `overflow=${style.overflow}`;
                el = el.parentElement;
                depth++;
            }
            return out;
        }''')
        print("\n--- VISIBILITY CHAIN ---")
        print(vis_chain)

        # Check if there's a Yes/No question or toggle for AI tools
        ai_toggle = await page.evaluate('''() => {
            const body = document.body.innerHTML.toLowerCase();
            const patterns = [
                "did you use ai",
                "generative ai",
                "ai-generated",
                "ai tools"
            ];
            let results = [];
            for (const pat of patterns) {
                const idx = body.indexOf(pat);
                if (idx !== -1) {
                    // Get surrounding HTML
                    const slice = document.body.innerHTML.slice(Math.max(0,idx-300), idx+500);
                    results.push("--- " + pat + " ---\\n" + slice.slice(0, 800));
                }
            }
            return results.join("\\n\\n") || "NONE FOUND";
        }''')
        print("\n--- AI TOGGLE/QUESTION ---")
        print(ai_toggle[:5000])

        # Check all radio buttons on page
        radios = await page.evaluate('''() => {
            let out = [];
            document.querySelectorAll('input[type="radio"]').forEach(r => {
                const label = r.parentElement?.textContent?.trim()?.slice(0, 80) || '';
                out.push(`name=${r.name} value=${r.value} checked=${r.checked} visible=${r.offsetParent!==null} label=${label}`);
            });
            return out.join("\\n");
        }''')
        print("\n--- ALL RADIO BUTTONS ---")
        print(radios)

        # Full page screenshot
        await page.screenshot(path="/tmp/kdp_content_ai_debug.png", full_page=True)
        print("\n✅ Screenshot: /tmp/kdp_content_ai_debug.png")

        await browser.close()

asyncio.run(main())
