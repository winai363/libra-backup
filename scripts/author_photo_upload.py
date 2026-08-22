#!/usr/bin/env python3
"""author_photo_upload.py — set the Author Central (author.amazon.com) profile photo.

Usage:
  python3 scripts/author_photo_upload.py --inspect          # read-only: screenshot + dump clickables
  python3 scripts/author_photo_upload.py <path-to-jpg>      # upload photo

Author Central gotchas (from libra/memory.md 3ก.ค.): buttons are often
<a class="ui ... button"> not <button> — querySelectorAll('button') comes back
empty; use get_by_text / JS clicks. Screenshots land in
/root/kdp/logs/author-central-shots/.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kdp_freeze import assert_kdp_mutation_allowed  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402
from kdp_upload import SESSION_FILE, logger  # noqa: E402

SHOTS = Path("/root/kdp/logs/author-central-shots")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")


async def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(SHOTS / f"{name}.png"))
    logger.info(f"📸 {SHOTS / name}.png")


async def run(photo: str | None, inspect: bool):
    assert_kdp_mutation_allowed("author_photo")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(storage_state=str(SESSION_FILE), user_agent=UA)
        page = await ctx.new_page()
        try:
            await page.goto("https://author.amazon.com/profile",
                            wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(6000)
            await _shot(page, "1-profile")
            if "signin" in page.url.split("?")[0] or "/ap/" in page.url.split("?")[0]:
                raise RuntimeError("Author Central session expired — needs KDP re-login first")

            if inspect:
                dump = await page.evaluate(
                    """() => [...document.querySelectorAll('a, button, [role=button], input, label')]
                        .filter(el => el.getBoundingClientRect().width > 0)
                        .map(el => ({tag: el.tagName, t: (el.innerText||el.value||'').trim().slice(0,60),
                                     cls: (el.className||'').toString().slice(0,60)}))
                        .filter(x => x.t || x.tag === 'INPUT').slice(0, 60)""")
                for x in dump:
                    print(x)
                fi = await page.evaluate(
                    "() => [...document.querySelectorAll('input[type=file]')].map(i => i.id || i.name || i.accept || '?')")
                print("file inputs:", fi)
                return True

            # click a photo-ish control to reveal the file input (varies by UI)
            for txt in ["Add photo", "Edit photo", "Change photo", "photo", "Photo"]:
                try:
                    await page.get_by_text(txt, exact=False).first.click(timeout=4000)
                    logger.info(f"clicked control: {txt!r}")
                    await page.wait_for_timeout(2000)
                    break
                except Exception:
                    continue
            fin = page.locator("input[type='file']").first
            await fin.wait_for(state="attached", timeout=15000)
            await fin.set_input_files(photo)
            logger.info("photo file set")
            await page.wait_for_timeout(5000)
            await _shot(page, "2-after-file")
            # confirm/crop modal → Save/Upload/Apply
            for txt in ["Publish", "Save", "Upload", "Apply", "Done", "OK"]:
                try:
                    await page.get_by_text(txt, exact=True).last.click(timeout=4000)
                    logger.info(f"clicked confirm: {txt!r}")
                    await page.wait_for_timeout(4000)
                except Exception:
                    continue
            await page.wait_for_timeout(5000)
            await _shot(page, "3-final")
            logger.info("done — verify screenshot 3-final")
            return True
        except Exception as e:
            await _shot(page, "ERROR")
            logger.error(f"❌ {e}")
            return False
        finally:
            await browser.close()


if __name__ == "__main__":
    inspect = "--inspect" in sys.argv
    photo = next((a for a in sys.argv[1:] if not a.startswith("--")), None)
    if not inspect and not photo:
        print(__doc__)
        sys.exit(1)
    ok = asyncio.run(run(photo, inspect))
    sys.exit(0 if ok else 1)
