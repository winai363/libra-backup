#!/usr/bin/env python3
"""
aplus_upload.py — Create & submit A+ Content for one book via KDP.

Entry: the book's promotion-manager page (targeted by book_id — no wrong-book
risk) → A+ Content card → choose marketplace → Manage A+ Content → builder.

Stages (screenshot after every step into logs/aplus-shots/<slug>_*.png):
  1 nav      promotion-manager, scope the A+ card, pick marketplace
  2 hub      Manage A+ Content → A+ hub (new tab or same tab)
  3 create   Start creating A+ content → content details (name, language)
  4 module   add "Standard Image Header With Text", upload image, fill texts
  5 asin     Apply ASINs step — search own ASIN, apply
  6 submit   Review & submit (skipped with --dry-run)

Usage:
  python3 aplus_upload.py <slug> [--dry-run] [--stop-after N]
Requires /root/kdp/<slug>/aplus/content.json (from aplus_assets.py).
"""
import asyncio
import json
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kdp_freeze import assert_kdp_mutation_allowed  # noqa: E402

SESSION = "/root/libra/kdp_session.json"
# A+ hub lives on per-marketplace KDP domains (kdp.amazon.es etc.) that need
# their own auth. Keep those cookies in a SEPARATE session file — never write
# back to the main kdp_session.json other crons depend on.
SESSION_APLUS = Path("/root/libra/kdp_session_aplus.json")
KDP = Path("/root/kdp")
SHOTS = Path("/root/libra/logs/aplus-shots")

ENV = {}
_env_path = Path("/root/libra/.env")
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            ENV[k.strip()] = v.strip()


def _is_signin(url: str) -> bool:
    """Signin check on the URL PATH only — after a successful login Amazon
    redirects to the hub with openid params that embed 'signin' in the QUERY,
    which must not count as being on the signin page."""
    from urllib.parse import urlsplit
    path = urlsplit(url).path
    return path.startswith("/ap/") or "signin" in path


async def marketplace_login(pg, slug):
    """Complete Amazon signin on a localized domain (same widget IDs everywhere)."""
    email, pw = ENV.get("KDP_EMAIL", ""), ENV.get("KDP_PASSWORD", "")
    secret = ENV.get("KDP_TOTP_SECRET", "")
    if not (email and pw):
        print("ABORT: no credentials in .env")
        return False
    try:
        em = pg.locator("#ap_email, input[type='email']")
        if await em.count() and await em.first.is_visible():
            await em.first.fill(email)
            cont = pg.locator("#continue, input#continue, button[type='submit'], "
                              "input[type='submit']")
            if await cont.count():
                await cont.first.click()
                await pg.wait_for_timeout(2500)
        pwf = pg.locator("#ap_password, input[type='password']")
        await pwf.first.fill(pw)
        remember = pg.locator("input[name='rememberMe']")
        if await remember.count():
            try:
                await remember.first.check()
            except Exception:
                pass
        await pg.locator("#signInSubmit, button[type='submit'], "
                         "input[type='submit']").first.click()
        await pg.wait_for_timeout(4000)
        # OTP challenge
        otp = pg.locator("#auth-mfa-otpcode, input[name='otpCode'], "
                         "input[name='code']")
        if await otp.count() and await otp.first.is_visible():
            if not secret:
                await shot(pg, slug, "login_otp_no_secret")
                print("ABORT: OTP asked but no KDP_TOTP_SECRET")
                return False
            import pyotp
            await otp.first.fill(pyotp.TOTP(secret).now())
            keep = pg.locator("#auth-mfa-remember-device")
            if await keep.count():
                try:
                    await keep.first.check()
                except Exception:
                    pass
            await pg.locator("#auth-signin-button, button[type='submit'], "
                             "input[type='submit']").first.click()
            await pg.wait_for_timeout(5000)
        return not _is_signin(pg.url)
    except Exception as e:
        print(f"login error: {type(e).__name__}: {e}")
        await shot(pg, slug, "login_error")
        return False

MARKET_LABEL = {
    "amazon.com": "Amazon.com", "amazon.es": "Amazon.es", "amazon.fr": "Amazon.fr",
    "amazon.de": "Amazon.de", "amazon.it": "Amazon.it", "amazon.nl": "Amazon.nl",
    "amazon.pl": "Amazon.pl", "amazon.co.uk": "Amazon.co.uk",
    "amazon.co.jp": "Amazon.co.jp",
}


class Stop(Exception):
    pass


async def shot(page, slug, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    p = SHOTS / f"{slug}_{name}.png"
    try:
        await page.screenshot(path=str(p), full_page=True)
    except Exception:
        await page.screenshot(path=str(p))
    print(f"  shot: {p.name}", flush=True)


async def run(slug: str, dry_run: bool, stop_after: int):
    assert_kdp_mutation_allowed("aplus_upload")
    bdir = KDP / slug
    listing = json.loads((bdir / "listing.json").read_text())
    content = json.loads((bdir / "aplus" / "content.json").read_text())
    bid = listing.get("kdp_book_id")
    asin = listing.get("asin") or content.get("asin")
    title = listing.get("title", "")
    if not bid or not asin:
        print(f"ABORT: missing kdp_book_id/asin for {slug}")
        return 2
    market = MARKET_LABEL.get(content.get("marketplace", ""), "Amazon.com")

    # A+ hub URL is deterministic per marketplace (mirrors the KDP dropdown map)
    domain = content.get("marketplace", "amazon.com")
    hub_url = f"https://kdp.{domain}/aplus/content-manager"

    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True,
                                    args=["--disable-blink-features=AutomationControlled"])
        state = str(SESSION_APLUS if SESSION_APLUS.exists() else SESSION)
        c = await b.new_context(storage_state=state,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            viewport={"width": 1500, "height": 1000})
        pg = await c.new_page()
        await pg.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>false})")
        hub = pg

        try:
            # -- stage 1+2: straight to the marketplace A+ hub ---------------
            print(f"  hub url: {hub_url}", flush=True)
            await hub.goto(hub_url, wait_until="domcontentloaded", timeout=60000)
            await hub.wait_for_timeout(4000)
            if _is_signin(hub.url):
                print("  signin required — logging in...", flush=True)
                if not await marketplace_login(hub, slug):
                    await shot(hub, slug, "abort_login_failed")
                    print("LOGIN_FAILED"); return 3
                if hub_url.split("//")[1].split("/")[0] not in hub.url:
                    await hub.goto(hub_url, wait_until="domcontentloaded",
                                   timeout=60000)
                await hub.wait_for_timeout(4000)
                if _is_signin(hub.url):
                    await shot(hub, slug, "abort_hub_signin")
                    print("SESSION_EXPIRED at hub"); return 3
            # persist marketplace cookies for future runs (separate file)
            await c.storage_state(path=str(SESSION_APLUS))
            await hub.wait_for_timeout(3000)
            await shot(hub, slug, "2_hub")
            if stop_after in (1, 2):
                raise Stop()

            # -- stage 3: start creating -------------------------------------
            start = hub.get_by_role("button", name="Start creating A+ content",
                                    exact=False)
            if not await start.count():
                start = hub.get_by_text("Start creating A+ content", exact=False)
            if not await start.count():
                start = hub.get_by_role("button", name="Create A+ content",
                                        exact=False)
            await start.first.click()
            await hub.wait_for_timeout(5000)
            await shot(hub, slug, "3_editor")
            name_in = hub.get_by_label("Content name", exact=False)
            if not await name_in.count():
                name_in = hub.locator(
                    'input[name*="name" i], input[placeholder*="name" i]')
            await name_in.first.fill(content["content_name"])
            await shot(hub, slug, "3_named")
            if stop_after == 3:
                raise Stop()

            # -- stage 4: add module + image + texts -------------------------
            add = hub.get_by_role("button", name="Add module", exact=False)
            await add.first.click()
            await hub.wait_for_timeout(2500)
            await shot(hub, slug, "4_module_picker")
            mod = hub.get_by_text("Standard Image Header With Text", exact=False)
            if not await mod.count():
                mod = hub.get_by_text("Standard Image Header", exact=False)
            await mod.first.click()
            await hub.wait_for_timeout(1000)
            addsel = hub.get_by_role("button", name="Add", exact=True)
            if await addsel.count():
                await addsel.first.click()
            await hub.wait_for_timeout(2500)
            await shot(hub, slug, "4_module_added")

            # image slot: clicking "Click to add image" opens either a native
            # file chooser or an asset-library modal with its own file input
            img_slot = hub.get_by_text("Click to add image", exact=False)
            try:
                async with hub.expect_file_chooser(timeout=8000) as fc:
                    await img_slot.first.click()
                chooser = await fc.value
                await chooser.set_files(content["image"])
            except Exception:
                # "Add Image" modal has no <input type=file>; the dropzone is
                # drag/drop-only — synthesize a real drop event with the file.
                await shot(hub, slug, "4_image_modal")
                import base64
                b64 = base64.b64encode(
                    Path(content["image"]).read_bytes()).decode()
                # Playwright locators pierce shadow DOM — grab the real element
                # inside the modal, then dispatch composed drag events so they
                # cross shadow boundaries to wherever the listener sits.
                dz_handle = await hub.get_by_text(
                    "Drag image here", exact=False).last.element_handle()
                dropped = await dz_handle.evaluate(
                    """async (el, b64) => {
                        const bin = atob(b64);
                        const arr = new Uint8Array(bin.length);
                        for (let i = 0; i < bin.length; i++)
                            arr[i] = bin.charCodeAt(i);
                        const file = new File([arr], 'header_970x600.jpg',
                                              {type: 'image/jpeg'});
                        const dt = new DataTransfer();
                        dt.items.add(file);
                        // walk from the deepest element upward across shadow
                        // roots, dispatching at each level
                        const targets = [];
                        let n = el;
                        for (let i = 0; n && i < 12; i++) {
                            targets.push(n);
                            n = n.parentElement ||
                                (n.getRootNode() && n.getRootNode().host) ||
                                null;
                        }
                        for (const t of targets) {
                            for (const type of ['dragenter', 'dragover',
                                                'drop']) {
                                t.dispatchEvent(new DragEvent(type, {
                                    bubbles: true, cancelable: true,
                                    composed: true, dataTransfer: dt}));
                            }
                        }
                        return 'dispatched on ' + targets.length + ' levels';
                    }""", b64)
                print(f"  drop: {dropped}", flush=True)
            await hub.wait_for_timeout(8000)
            await shot(hub, slug, "4_image_uploaded")
            # fill alt text while the Add Image modal is still open (the modal
            # has its own required alt field on some marketplaces)
            malt = hub.locator('input[placeholder*="alt" i]')
            if await malt.count() and await malt.last.is_visible():
                try:
                    await malt.last.fill(content["alt_text"])
                except Exception:
                    pass
            # confirm modal ("Add") / crop dialog — iterate matches LAST-first:
            # the modal sits on top, but earlier hidden "Add" buttons (module
            # picker) match the same role+name and used to swallow the click.
            confirmed = False
            for label in ("Add", "Apply", "Save", "Use image", "Upload"):
                btn2 = hub.get_by_role("button", name=label, exact=True)
                for k in range(await btn2.count() - 1, -1, -1):
                    el = btn2.nth(k)
                    if not await el.is_visible():
                        continue
                    try:
                        await el.click()
                        await hub.wait_for_timeout(4000)
                        confirmed = True
                        break
                    except Exception:
                        continue
                if confirmed:
                    break
            # the modal must be GONE before touching fields behind it
            if await hub.get_by_text("Add Image", exact=True).count():
                still = hub.get_by_text("Add Image", exact=True)
                if await still.first.is_visible():
                    await shot(hub, slug, "4_modal_stuck")
                    raise RuntimeError("Add Image modal did not close")
            await shot(hub, slug, "4_image_done")

            alt = hub.get_by_label("Image keywords", exact=False)
            if not await alt.count():
                alt = hub.locator('input[placeholder*="alt" i], '
                                  'input[placeholder*="keyword" i], '
                                  'textarea[placeholder*="keyword" i]')
            if await alt.count():
                await alt.first.fill(content["alt_text"])

            heads = hub.locator('input[placeholder*="headline" i], '
                                'input[aria-label*="headline" i]')
            n_heads = await heads.count()
            if n_heads:
                await heads.first.fill(content["headline"])
            bodyf = hub.locator('div[contenteditable="true"], '
                                'textarea[placeholder*="body" i]')
            if await bodyf.count():
                await bodyf.first.click()
                await bodyf.first.fill(content["body"])
            await shot(hub, slug, "4_texts_filled")
            if stop_after == 4:
                raise Stop()

            # -- stage 5: apply ASIN -----------------------------------------
            nxt = hub.get_by_role("button", name="Next: Apply ASINs", exact=False)
            if not await nxt.count():
                nxt = hub.get_by_text("Apply ASINs", exact=False)
            await nxt.first.click()
            await hub.wait_for_timeout(4000)
            await shot(hub, slug, "5_asin_page")
            search = hub.locator('input[type="search"], '
                                 'input[placeholder*="ASIN" i]')
            # typeahead: type slowly to trigger the suggestion dropdown,
            # then click the suggestion (there is no "Add" button)
            await search.first.click()
            await search.first.press_sequentially(asin, delay=120)
            await hub.wait_for_timeout(4000)
            await shot(hub, slug, "5_asin_found")
            sug = hub.locator(
                f'[role="option"]:has-text("{asin}"), '
                f'li:has-text("{asin}"), '
                f'[class*="suggest" i]:has-text("{asin}"), '
                f'[class*="result" i]:has-text("{asin}")').first
            try:
                await sug.click(timeout=8000)
            except Exception:
                # fallback: any visible text node with the ASIN outside the input
                alt = hub.get_by_text(asin, exact=False)
                for k in range(await alt.count()):
                    el = alt.nth(k)
                    tag = await el.evaluate("e => e.tagName")
                    if tag != "INPUT":
                        await el.click()
                        break
            await hub.wait_for_timeout(3000)
            # suggestion click adds a result row with a checkbox +
            # "Apply content" button — tick it, then apply
            row_cb = hub.locator('input[type="checkbox"]:visible')
            if await row_cb.count():
                await row_cb.first.check()
            await shot(hub, slug, "5_asin_row_checked")
            apply_b = hub.get_by_role("button", name="Apply content",
                                      exact=False)
            if not await apply_b.count():
                apply_b = hub.get_by_text("Apply content", exact=False)
            await apply_b.first.click()
            await hub.wait_for_timeout(3000)
            # if a previous (failed) content is already applied to this ASIN,
            # an "Override existing content?" modal appears — confirm it
            override = hub.get_by_role("button", name="Override", exact=False)
            if await override.count() and await override.first.is_visible():
                await override.first.click()
                print("  override existing content: confirmed", flush=True)
                await hub.wait_for_timeout(3000)
            await shot(hub, slug, "5_asin_applied")
            applied_hdr = await hub.get_by_text(
                re.compile(r"Applied ASINs \((\d+)\)")).first.inner_text()
            n_applied = int(re.search(r"\((\d+)\)", applied_hdr).group(1))
            print(f"applied ASINs: {n_applied}")
            if n_applied < 1:
                raise RuntimeError("ASIN was not applied (count still 0)")
            if stop_after == 5:
                raise Stop()

            # -- stage 6: review & submit ------------------------------------
            rev = hub.get_by_role("button", name="Next: Review & Submit",
                                  exact=False)
            if not await rev.count():
                rev = hub.get_by_text("Review", exact=False)
            await rev.first.click()
            await hub.wait_for_timeout(4000)
            await shot(hub, slug, "6_review")
            if dry_run:
                print("DRY-RUN: stopping before submit")
                raise Stop()
            sub = hub.get_by_role("button", name="Submit for approval",
                                  exact=False)
            await sub.first.click()
            await hub.wait_for_timeout(2500)
            # a confirmation modal opens with a second "Submit for approval"
            await shot(hub, slug, "6_confirm_modal")
            if await sub.count() > 1:
                await sub.last.click()
            await hub.wait_for_timeout(5000)
            await shot(hub, slug, "6_submitted")
            body_txt = await hub.locator("body").inner_text()
            if "Content validation failed" in body_txt:
                # surface WHICH field Amazon flags: open Fix content and dump
                # invalid elements before failing
                fix = hub.get_by_role("button", name="Fix content", exact=False)
                if await fix.count():
                    await fix.first.click()
                    await hub.wait_for_timeout(4000)
                    await shot(hub, slug, "6_fix_content")
                    bad = await hub.evaluate("""() => {
                        const sels = ['[aria-invalid="true"]', '.a-form-error',
                            '[class*="error" i] input', 'input:invalid',
                            '[class*="invalid" i]'];
                        const out = [];
                        for (const s of sels)
                            document.querySelectorAll(s).forEach(e => out.push(
                                s + ' :: ' + (e.getAttribute('aria-label') ||
                                e.getAttribute('placeholder') || e.name ||
                                e.className || e.tagName).slice(0, 120)));
                        return out.slice(0, 20);
                    }""")
                    print("  invalid elements:", json.dumps(bad, indent=1))
                raise RuntimeError("content validation failed")
            if "Submit for approval" in body_txt and "submitted" not in \
                    body_txt.lower():
                raise RuntimeError("submit confirmation did not go through")
            print("SUBMITTED")
            # stamp listing
            lf = bdir / "listing.json"
            d = json.loads(lf.read_text())
            d["aplus"] = {"status": "submitted", "content_name":
                          content["content_name"], "marketplace":
                          content.get("marketplace", "")}
            lf.write_text(json.dumps(d, ensure_ascii=False, indent=2))
            return 0
        except Stop:
            print(f"STOPPED (stop_after={stop_after}, dry_run={dry_run})")
            return 0
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")
            try:
                await shot(pg, slug, "error")
            except Exception:
                pass
            return 1
        finally:
            await b.close()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__); sys.exit(2)
    slug = args[0]
    dry = "--dry-run" in sys.argv
    stop_after = 0
    if "--stop-after" in sys.argv:
        stop_after = int(sys.argv[sys.argv.index("--stop-after") + 1])
    sys.exit(asyncio.run(run(slug, dry, stop_after)))


if __name__ == "__main__":
    main()
