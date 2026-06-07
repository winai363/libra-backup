import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state='/root/libra/kdp_session.json')
        page = await context.new_page()
        await page.goto("https://kdp.amazon.com/en_US/title-setup/kindle/AXVYEMDPG0Z6C/content")
        await page.wait_for_timeout(5000)
        
        ai_accordion = page.locator('[data-a-accordion-name="generative-ai-questionnaire-accordion"]')
        yes_row = ai_accordion.locator('[data-a-accordion-row-name="yes"] .a-accordion-row')
        if await yes_row.count() > 0:
            await yes_row.click()
            await page.wait_for_timeout(2000)
            
            # Click the dropdown triggers so the menus are in the DOM
            for trigger in await ai_accordion.locator('.a-button-dropdown').all():
                try:
                    await trigger.click()
                    await page.wait_for_timeout(500)
                except:
                    pass

            html = await ai_accordion.inner_html()
            # also get body for popovers
            body = await page.evaluate("() => document.body.innerHTML")
            with open("/tmp/ai_form.html", "w") as f:
                f.write(html + "\n\n==== BODY ====\n\n" + body)
            print("Dumped AI form to /tmp/ai_form.html")
        else:
            print("Yes row not found")
        await browser.close()

asyncio.run(main())
