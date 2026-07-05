"""
author_url_retry.py — Daily retry: create Author Page URL (amazon.com/author/wkbui).

First attempt 2026-07-03 got Amazon's "Something went wrong. Please try again
later." (profile too fresh). This retries ONCE per day (no spamming a
choose-once endpoint), Telegrams the outcome, and removes its own cron line
after success. Also reports how many books have synced into Author Central.

Playwright gotchas (learned 2026-07-03): the modal's input/buttons are often
invisible to locators even when rendered — use coordinate clicks + keyboard.
"""
import asyncio
import subprocess
import sys
from pathlib import Path

LIBRA = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIBRA))

from kdp_upload import SESSION_FILE, logger  # noqa: E402

URL_NAME = "wkbui"
CRON_MARK = "author_url_retry.py"


def tg(msg):
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


def remove_own_cron():
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    lines = [l for l in (r.stdout or "").splitlines() if CRON_MARK not in l]
    subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)


async def count_books(pg) -> str:
    try:
        await pg.goto("https://author.amazon.com/books",
                      wait_until="domcontentloaded", timeout=60000)
        await pg.wait_for_timeout(15000)
        n = await pg.evaluate(
            '() => document.querySelectorAll("img[src*=\'media-amazon\']").length')
        return str(n)
    except Exception:
        return "?"


async def main() -> None:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context(storage_state=str(SESSION_FILE),
                                  viewport={"width": 1280, "height": 720})
        pg = await ctx.new_page()
        await pg.goto("https://author.amazon.com/profile",
                      wait_until="domcontentloaded", timeout=60000)
        await pg.wait_for_timeout(6000)
        body = await pg.evaluate("() => document.body.innerText")

        if f"author/{URL_NAME}" in body:
            books = await count_books(pg)
            tg(f"✅ Author Page URL พร้อมแล้ว: amazon.com/author/{URL_NAME}\n"
               f"📚 หนังสือ sync เข้า Author Central: {books} เล่ม\n(หยุด retry อัตโนมัติ)")
            remove_own_cron()
            await b.close()
            return

        if "Create link" not in body:
            tg("⚠️ Author URL retry: หน้า profile ไม่มีปุ่ม Create link และยังไม่มี URL — "
               "ต้องเช็คมือ (screenshot: /tmp/author_url_retry.png)")
            await pg.screenshot(path="/tmp/author_url_retry.png")
            await b.close()
            return

        await pg.get_by_text("Create link", exact=True).first.click()
        await pg.wait_for_timeout(4000)
        await pg.mouse.click(695, 451)          # modal URL input
        await pg.keyboard.type(URL_NAME, delay=80)
        await pg.wait_for_timeout(1500)
        await pg.get_by_text("Submit", exact=True).first.click()
        await pg.wait_for_timeout(3000)
        await pg.mouse.click(856, 397)          # OK on confirm dialog
        await pg.wait_for_timeout(10000)
        await pg.screenshot(path="/tmp/author_url_retry.png")

        body = await pg.evaluate("() => document.body.innerText")
        if "Something went wrong" in body:
            tg("⏳ Author URL (wkbui): Amazon ยังตอบ 'Something went wrong' — "
               "จะลองใหม่พรุ่งนี้ 14:50 อัตโนมัติ")
        else:
            # reload profile to verify
            await pg.goto("https://author.amazon.com/profile",
                          wait_until="domcontentloaded", timeout=60000)
            await pg.wait_for_timeout(6000)
            body = await pg.evaluate("() => document.body.innerText")
            if f"author/{URL_NAME}" in body:
                books = await count_books(pg)
                tg(f"🎉 สร้าง Author Page URL สำเร็จ: amazon.com/author/{URL_NAME}\n"
                   f"📚 หนังสือ sync แล้ว: {books} เล่ม\n(หยุด retry อัตโนมัติ)")
                remove_own_cron()
            else:
                tg("⚠️ Author URL (wkbui): submit แล้วแต่ยังไม่เห็น URL บน profile — "
                   "จะลองใหม่พรุ่งนี้ (screenshot: /tmp/author_url_retry.png)")
        await b.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"author_url_retry failed: {e}")
        tg(f"❌ Author URL retry error: {str(e)[:150]} — จะลองใหม่พรุ่งนี้")
        sys.exit(1)
