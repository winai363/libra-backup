"""Find Payhip's real login page from the homepage and dump its form — read-only."""
import asyncio, json
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        pg = await b.new_page()
        await pg.goto("https://payhip.com/", wait_until="domcontentloaded", timeout=30000)
        await pg.wait_for_timeout(2500)
        links = await pg.evaluate("""() => [...document.querySelectorAll('a')]
            .map(a => ({text:(a.innerText||'').trim().slice(0,30), href:a.href}))
            .filter(a => /log ?in|sign ?in|sign ?up/i.test(a.text))""")
        print("AUTH LINKS:", json.dumps(links, ensure_ascii=False))
        target = next((l["href"] for l in links if "log" in l["text"].lower() or "sign in" in l["text"].lower()), None)
        if not target:
            print("no login link found"); await b.close(); return
        r = await pg.goto(target, wait_until="domcontentloaded", timeout=30000)
        await pg.wait_for_timeout(3000)
        print("LOGIN URL:", pg.url, "status:", r.status if r else None, "title:", await pg.title())
        fields = await pg.evaluate("""() => [...document.querySelectorAll('input,button,form,iframe')]
            .slice(0,50).map(e => ({tag:e.tagName, type:e.type||'', name:e.name||'', id:e.id||'',
            placeholder:e.placeholder||'', src:(e.src||'').slice(0,60), text:(e.innerText||'').trim().slice(0,40)}))""")
        print(json.dumps(fields, ensure_ascii=False, indent=1))
        await pg.screenshot(path="logs/payhip-shots/probe-login.png", full_page=True)
        await b.close()
asyncio.run(main())
