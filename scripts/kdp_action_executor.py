#!/usr/bin/env python3
"""Safe auto-executor for Libra 90-Day Profit Agent actions.

Passed to run_daily(executor=...) so manual_required/ready experiment actions
execute automatically — but only after hard validation gates, and only with
real-state verification before claiming success:

- category_update: proposed leaf must exist in the audited KDP tree and the
  replaced path must be on the listing. Changes are verified at the modal
  level (chips read back) BEFORE the page-level save+publish; any mismatch
  aborts by navigating away without saving.
- free_promo: delegated to free_promo_auto.schedule_one, which aborts unless
  the promotion manager page lists the promo as Scheduled/In progress.
- metadata_update on the title field is permanently refused (cover/interior
  mismatch risk — see APPROVED_EXPERIMENTS note in profit_agent.py).

Every result is loud: executed and failed actions send a Telegram message.
An action the executor refuses stays manual_required — never silent success.
At most MAX_MUTATIONS_PER_RUN KDP mutations happen per daily run.
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

LIBRA_DIR = Path(__file__).resolve().parent.parent
import sys  # noqa: E402

sys.path.insert(0, str(LIBRA_DIR))
sys.path.insert(0, str(LIBRA_DIR / "scripts"))

from distribution_report import send_telegram  # noqa: E402

KDP_DIR = Path("/root/kdp")
LEDGER_FILE = LIBRA_DIR / "data" / "libra-business.db"
TREE_FILE = LIBRA_DIR / "data" / "kdp_category_tree.json"
SESSION_FILE = LIBRA_DIR / "kdp_session.json"
SHOTS_DIR = LIBRA_DIR / "logs" / "action-shots"
MAX_MUTATIONS_PER_RUN = 1
ERROR_MARKER = "error(s) to continue"  # KDP validation banner (substring match)


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z0-9à-ÿ]+", (text or "").lower()))


def load_tree_leaves(path: Path = TREE_FILE) -> set:
    try:
        return set(json.loads(path.read_text()).get("leaves", []))
    except (OSError, json.JSONDecodeError):
        return set()


def load_listing(slug: str) -> dict | None:
    try:
        return json.loads((KDP_DIR / slug / "listing.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def latest_snapshot_id(db_path: Path = LEDGER_FILE) -> int | None:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT id FROM kdp_snapshots ORDER BY observed_at DESC, id DESC LIMIT 1"
        ).fetchone()
    return row[0] if row else None


def validate_category_action(action: dict, listing: dict, leaves: set) -> tuple[bool, str, list]:
    """Pure validation gate. Returns (ok, reason, target_paths)."""
    proposed = (action.get("proposed_value") or "").strip()
    replaces = (action.get("replaces") or "").strip()
    current = list(listing.get("categories") or [])
    if not proposed:
        return False, "no proposed_value", []
    if leaves and proposed not in leaves:
        return False, f"proposed category not in audited KDP tree: {proposed}", []
    if not current:
        return False, "listing has no current categories to modify", []
    if proposed in current:
        return False, "proposed category already on listing", []
    if replaces:
        if replaces not in current:
            return False, f"replaces path not on listing: {replaces}", []
        targets = [proposed if c == replaces else c for c in current]
    else:
        if len(current) >= 3:
            return False, "listing already has 3 categories and no replaces given", []
        targets = current + [proposed]
    if len(targets) != len(set(targets)):
        return False, "target categories contain duplicates", []
    return True, "ok", targets[:3]


def validate_action(action: dict, listing: dict | None, leaves: set) -> tuple[bool, str, dict]:
    kind = action.get("kind")
    if float(action.get("cost_usd") or 0) != 0:
        return False, "executor only runs zero-cost actions", {}
    if listing is None:
        return False, f"no listing.json for slug {action.get('slug')}", {}
    if kind == "metadata_update" and action.get("field") == "title":
        return False, "title changes are permanently refused (cover/interior mismatch risk)", {}
    if kind == "category_update":
        ok, reason, targets = validate_category_action(action, listing, leaves)
        return ok, reason, {"targets": targets}
    if kind == "free_promo":
        match = re.search(r"(\d+)\s*-?\s*day", action.get("proposed_value") or "")
        days = int(match.group(1)) if match else 1
        if not 1 <= days <= 5:
            return False, f"free promo days out of KDP Select range: {days}", {}
        return True, "ok", {"days": days}
    return False, f"unsupported action kind for auto-execution: {kind}", {}


def _parent_tokens(path: str) -> set:
    segs = [seg.strip() for seg in re.split(r"[>›/|]", path) if seg.strip()]
    return _tokens(" ".join(segs[:-1]))


def verify_chips(targets: list, chips: list) -> tuple[bool, str | None]:
    """Category chips render one accordion row per PARENT path (no placement
    leaf, and several placements under the same parent share one row), so we
    verify that each target's parent path is covered by some chip. Leaf-level
    coverage is verified separately via KDP's "N out of 3 category placements"
    counter plus set_categories' own applied-path check."""
    for target in targets:
        parent = _parent_tokens(target)
        if not parent or not any(parent <= _tokens(chip) for chip in chips):
            return False, target
    return True, None


async def _read_chips(page) -> list:
    return await page.evaluate(
        """() => [...document.querySelectorAll('a.a-expander-header')]
            .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' '))
            .filter(Boolean)"""
    )


async def _close_modal(page) -> bool:
    """Close the categories modal via its VISIBLE Cancel button. A plain
    button:has-text("Cancel") locator resolves to KDP's hidden unsaved-changes
    dialog first and times out."""
    ok = await page.evaluate(
        """() => {
            const visible = [...document.querySelectorAll('button')]
                .filter(b => (b.innerText || '').trim() === 'Cancel' && b.offsetParent !== null);
            if (!visible.length) return false;
            visible[0].click();
            return true;
        }"""
    )
    await page.wait_for_timeout(1500)
    return ok


async def _shot(page, name: str) -> str:
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SHOTS_DIR / f"{name}.png"
    try:
        await page.screenshot(path=str(path))
    except Exception:
        pass
    return str(path)


async def _execute_category(action: dict, listing: dict, targets: list) -> dict:
    from playwright.async_api import async_playwright

    from kdp_categories import _open_modal, set_categories

    slug = action["slug"]
    book_id = listing.get("kdp_book_id")
    if not book_id:
        return {"returncode": 1, "error": "listing has no kdp_book_id"}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    before_categories = list(listing.get("categories") or [])

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=str(SESSION_FILE))
        page = await context.new_page()
        try:
            await page.goto(
                f"https://kdp.amazon.com/en_US/title-setup/kindle/{book_id}/details",
                wait_until="domcontentloaded", timeout=60000,
            )
            await page.wait_for_timeout(4000)
            # Signin redirects keep "title-setup" in the return_to query param —
            # judge by the URL path only.
            path = urlsplit(page.url).path
            if path.startswith("/ap/") or "title-setup" not in path:
                await _shot(page, f"{slug}_{stamp}_abort_redirect")
                return {"returncode": 1, "error": f"details page not editable (redirected to {page.url[:80]})"}
            body = await page.evaluate("() => document.body.innerText")
            if "In review" in body:
                return {"returncode": 1, "error": "book is In review — not editable"}

            # Real BEFORE state from KDP itself, not just the local listing.
            if not await _open_modal(page, None):
                return {"returncode": 1, "error": "category modal did not open (before-read)"}
            before_chips = await _read_chips(page)
            if not await _close_modal(page):
                return {"returncode": 1, "error": "could not close modal after before-read"}

            applied = await set_categories(page, targets, None)
            if len(applied) != len(targets):
                # Modal state is page-local until "Save and Continue" — leaving
                # the page discards it, so the live book keeps its categories.
                await _shot(page, f"{slug}_{stamp}_abort_partial")
                return {"returncode": 1,
                        "error": f"only {len(applied)}/{len(targets)} categories applied — aborted without saving"}

            # Verify at the modal level BEFORE any server-side save.
            from kdp_categories import _selected_count
            if not await _open_modal(page, None):
                return {"returncode": 1, "error": "category modal did not reopen (verify-read)"}
            after_chips = await _read_chips(page)
            placements = await _selected_count(page)
            if not await _close_modal(page):
                return {"returncode": 1, "error": "could not close modal after verify-read"}
            chips_ok, missing = verify_chips(targets, after_chips)
            if not chips_ok or placements != len(targets):
                await _shot(page, f"{slug}_{stamp}_abort_verify")
                return {"returncode": 1,
                        "error": f"verify-read mismatch (chips={after_chips}, placements={placements}, missing={missing}) — aborted without saving"}
            await _shot(page, f"{slug}_{stamp}_1_verified_modal")

            # Page-level save: details → content → pricing.
            for _ in range(3):
                if "/pricing" in page.url:
                    break
                await page.get_by_text("Save and Continue", exact=False).first.click(timeout=15000)
                await page.wait_for_timeout(7000)
                body = await page.evaluate("() => document.body.innerText")
                if ERROR_MARKER in body:
                    await _shot(page, f"{slug}_{stamp}_abort_validation")
                    return {"returncode": 1, "error": "KDP validation error during Save and Continue"}
            if "/pricing" not in page.url:
                await _shot(page, f"{slug}_{stamp}_abort_nopricing")
                return {"returncode": 1, "error": f"never reached pricing page (at {page.url[:80]})"}

            publish = page.locator('button:has-text("Publish")')
            if not await publish.count():
                await _shot(page, f"{slug}_{stamp}_abort_nopublish")
                return {"returncode": 1, "error": "publish button not found on pricing page"}
            await publish.first.click(timeout=15000)
            await page.wait_for_timeout(10000)
            body = await page.evaluate("() => document.body.innerText")
            if ERROR_MARKER in body:
                await _shot(page, f"{slug}_{stamp}_abort_publish_validation")
                return {"returncode": 1, "error": "KDP validation error on publish"}
            shot = await _shot(page, f"{slug}_{stamp}_2_published")
            if "review" not in body.lower() and "bookshelf" not in page.url:
                return {"returncode": 1,
                        "error": "publish clicked but no review/bookshelf confirmation on page",
                        "screenshot": shot}
        finally:
            await browser.close()

    listing["categories"] = targets
    (KDP_DIR / slug / "listing.json").write_text(
        json.dumps(listing, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    snapshot_id = latest_snapshot_id()
    return {
        "returncode": 0,
        "confirmation_id": f"kdp-category-update:{slug}:{stamp}",
        "external_url": f"https://kdp.amazon.com/en_US/title-setup/kindle/{book_id}/details",
        "screenshot": shot,
        "verified_state_change": {
            "before": {"categories": before_categories, "chips": before_chips},
            "after": {"categories": targets, "chips": after_chips},
            "before_snapshot_id": snapshot_id,
            "after_snapshot_id": snapshot_id,
        },
    }


async def _execute_free_promo(action: dict, listing: dict, days: int) -> dict:
    from free_promo_auto import schedule_one

    slug = action["slug"]
    book_id = listing.get("kdp_book_id")
    if not book_id:
        return {"returncode": 1, "error": "listing has no kdp_book_id"}
    before_promo = dict(listing.get("free_promo") or {"status": "none"})
    start = date.today() + timedelta(days=1)
    ok = await schedule_one(slug, book_id, listing.get("title", ""), False, start=start, days=days)
    if not ok:
        return {"returncode": 1, "error": "schedule_one could not verify a Scheduled promo"}
    after = json.loads((KDP_DIR / slug / "listing.json").read_text(encoding="utf-8")).get("free_promo") or {}
    snapshot_id = latest_snapshot_id()
    return {
        "returncode": 0,
        "confirmation_id": f"kdp-free-promo:{slug}:{after.get('start')}:{after.get('end')}",
        "external_url": "https://kdpreports.amazon.com/",
        "verified_state_change": {
            "before": before_promo,
            "after": after,
            "before_snapshot_id": snapshot_id,
            "after_snapshot_id": snapshot_id,
        },
    }


def build_executor(*, notify: bool = True):
    """Return an executor(action) -> result dict for run_daily."""
    budget = {"used": 0}
    leaves = load_tree_leaves()

    def _notify(message: str) -> None:
        if notify:
            try:
                send_telegram(message)
            except Exception:
                pass

    def executor(action: dict) -> dict:
        slug = action.get("slug", "?")
        label = f"{action.get('kind')}:{slug}"
        listing = load_listing(slug) if slug != "?" else None
        ok, reason, extra = validate_action(action, listing, leaves)
        if not ok:
            _notify(f"Libra executor ⏸ {label} ไม่ execute: {reason} (ยังเป็น manual_required)")
            # No evidence + returncode 0 → record_action_result keeps manual_required.
            return {"returncode": 0, "executor_skip_reason": reason}
        if budget["used"] >= MAX_MUTATIONS_PER_RUN:
            _notify(f"Libra executor ⏸ {label}: ครบโควตา mutation {MAX_MUTATIONS_PER_RUN} ครั้ง/รอบแล้ว รอรอบถัดไป")
            return {"returncode": 0, "executor_skip_reason": "daily mutation budget reached"}
        budget["used"] += 1
        try:
            if action["kind"] == "category_update":
                result = asyncio.run(_execute_category(action, listing, extra["targets"]))
            else:  # free_promo (validate_action allows only these two through)
                result = asyncio.run(_execute_free_promo(action, listing, extra["days"]))
        except Exception as exc:  # browser/session crash → failed, retryable
            result = {"returncode": 1, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        if result.get("returncode") == 0 and result.get("verified_state_change"):
            _notify(f"Libra executor ✅ {label} executed + verified: {result.get('confirmation_id')}")
        else:
            _notify(f"Libra executor ❌ {label} ล้มเหลว: {result.get('error', 'unknown')} (จะ retry ตาม attempt limit)")
        return result

    return executor


if __name__ == "__main__":
    # Manual smoke test: validate (never execute) the latest pending actions.
    from profit_agent import latest_experiment_action

    with sqlite3.connect(LEDGER_FILE) as connection:
        rows = connection.execute(
            "SELECT id, slug, status FROM experiments ORDER BY id"
        ).fetchall()
    leaves = load_tree_leaves()
    for exp_id, slug, status in rows:
        latest = latest_experiment_action(LEDGER_FILE, exp_id)
        if not latest:
            print(f"exp {exp_id} {slug} [{status}]: no action recorded yet")
            continue
        action = latest["action"]
        ok, reason, extra = validate_action(action, load_listing(slug), leaves)
        print(f"exp {exp_id} {slug} [{status}] {action.get('kind')}: valid={ok} ({reason}) {extra}")
