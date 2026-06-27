#!/usr/bin/env python3
"""
kdp_categories.py — Robust KDP category selection for the 2026 modal.

The KDP "Categories" modal is a React cascade: a level-0 <select> (Category),
which on change reveals a level-1 <select> (Subcategory), which reveals leaf
"placement" checkboxes. Playwright's select_option DOES drive the React cascade
(verified), so we set the native <select> by label and fuzzy-match each step of
the book's intended category path against the REAL options Amazon offers — never
trusting the LLM's free-text path to match Amazon's tree verbatim.

Public:  async def set_categories(page, target_paths, logger=None) -> list[str]
Returns the list of category paths actually applied. Caller still clicks the
page's own Save/Publish; this function clicks "Save categories" to close the modal.
"""
import json
import re

_STOP = {"and", "the", "of", "for", "a", "an", "to", "in", "ebooks", "ebook",
         "kindle", "books", "book", "store", "general"}


def _tokens(s: str) -> set:
    return set(re.findall(r"[a-z0-9à-ÿ]+", (s or "").lower())) - _STOP


def _score(target_tokens: set, option_text: str) -> float:
    o = _tokens(option_text)
    if not o or not target_tokens:
        return 0.0
    inter = len(target_tokens & o)
    return inter / len(target_tokens | o)  # Jaccard


def _path_segments(path: str) -> list[str]:
    segs = [p.strip() for p in re.split(r"[>›/|]", path) if p.strip()]
    # Keep only segments with real content tokens — this drops store-root prefixes
    # like "Kindle eBooks" / "Books" so segment 0 is the real level-0 category.
    return [s for s in segs if _tokens(s)]


async def _enabled_selects(page):
    """All visible+enabled react category selects, as [{name,value,level,chosen,options}]."""
    return await page.evaluate("""() => {
        return [...document.querySelectorAll('select.a-native-dropdown')]
          .filter(s => !s.disabled && s.offsetParent !== null && /react-aui/.test(s.name))
          .map(s => {
            let level = null, chosen = false;
            try { const v = JSON.parse(s.value || '{}'); level = v.level; chosen = 'key' in v; } catch(e){}
            return { name: s.name, value: s.value, level, chosen,
                     options: [...s.options].map(o => o.text) };
          });
    }""")


async def _pick_select(page, target_tokens):
    """Find the lowest-level enabled UNSET select; return (name, options) or None."""
    sels = await _enabled_selects(page)
    unset = [s for s in sels if not s["chosen"] and s["level"] is not None]
    if not unset:
        return None
    unset.sort(key=lambda s: s["level"])
    return unset[0]


async def _expand_a_row(page):
    """Expand a collapsed category accordion row so its cascade selects render."""
    collapsed = page.locator('a.a-expander-header[aria-expanded="false"]').filter(visible=True)
    if await collapsed.count():
        try:
            await collapsed.first.click(timeout=5000)
            await page.wait_for_timeout(1200)
            return True
        except Exception:
            pass
    return False


async def _select_at_level(page, level):
    """Return the enabled cascade select at a given level, set OR unset. Needed to
    re-drive level-0 after a Remove/Reset leaves the row populated at the old path."""
    sels = await _enabled_selects(page)
    matches = [s for s in sels if s["level"] == level]
    return matches[0] if matches else None


async def _choose(page, name, options, target_tokens, logger):
    """select_option the best-matching option (skip 'Select one'). Returns label or None."""
    best, best_score = None, 0.0
    for opt in options:
        if opt.strip().lower() in ("select one", "select"):
            continue
        sc = _score(target_tokens, opt)
        if sc > best_score:
            best, best_score = opt, sc
    if best is None:
        if logger:
            logger.warning(f"    · {name}: no option matched {target_tokens} "
                           f"(opts sample: {options[:6]})")
        return None
    try:
        await page.select_option(f'select[name="{name}"]', label=best)
        await page.wait_for_timeout(1800)
        if logger:
            logger.info(f"    · {name}: '{best}' (match {best_score:.2f})")
        return best
    except Exception as e:
        if logger:
            logger.warning(f"    · {name}: select '{best}' failed: {e}")
        return None


async def _placement_checkboxes(page):
    """Visible, unchecked leaf placement checkboxes with their label text."""
    return await page.evaluate("""() => {
        const out = [];
        for (const cb of document.querySelectorAll('input[type=checkbox]')) {
            if (cb.offsetParent === null || cb.checked) continue;
            if (!/^checkbox-\\d+/.test(cb.className)) continue;  // leaf placement boxes
            let label = '';
            const wrap = cb.closest('div,span,li');
            if (wrap) label = (wrap.innerText || '').trim().slice(0, 60);
            out.push({ cls: cb.className, label });
        }
        return out;
    }""")


async def _check_best_placement(page, target_tokens, logger):
    # Placement checkboxes can take a moment to render after the subcategory is
    # chosen — retry a few times before giving up.
    boxes = []
    for _ in range(4):
        boxes = await _placement_checkboxes(page)
        if boxes:
            break
        await page.wait_for_timeout(1200)
    if not boxes:
        if logger:
            logger.warning("    no placement checkboxes appeared")
        return None
    best, best_score = boxes[0], -1.0
    for b in boxes:
        sc = _score(target_tokens, b["label"])
        if sc > best_score:
            best, best_score = b, sc
    # Refuse a zero-overlap placement. When the drill-down lands in the wrong
    # subtree, every leaf scores 0 and the old code still checked boxes[0] —
    # that's how a "Children's > Activity Books" target became "Atlases". A
    # rejected target is dropped; if all are dropped set_categories CANCELs and
    # keeps the book's existing categories rather than saving garbage.
    if best_score <= 0.0:
        if logger:
            logger.warning(f"    no placement matched {target_tokens} "
                           f"(best '{best['label']}' scored 0) — skipping target")
        return None
    cls = best["cls"].split()[0]
    try:
        # KDP placement checkboxes are custom-styled — the real <input> is visually
        # hidden, so Playwright's .check() reports "not visible". A JS click on the
        # input still fires the React onChange and registers the placement.
        ok = await page.evaluate("""(cls) => {
            const el = document.querySelector('input.' + cls);
            if (!el) return false;
            el.scrollIntoView({block: 'center'});
            el.click();
            return true;
        }""", cls)
        await page.wait_for_timeout(1000)
        if ok:
            if logger:
                logger.info(f"    ✓ placement '{best['label']}' (match {best_score:.2f})")
            return best["label"]
    except Exception as e:
        if logger:
            logger.warning(f"    placement check failed: {e}")
    return None


async def _selected_count(page):
    txt = await page.evaluate(
        """() => { const m=[...document.querySelectorAll('*')]
            .map(e=>e.childElementCount===0?(e.innerText||''):'')
            .find(t=>/out of 3 categor/i.test(t)); return m||''; }""")
    m = re.search(r"(\d+)\s+out of 3", txt or "")
    return int(m.group(1)) if m else None


async def _open_modal(page, logger):
    for label in ("Edit categories", "Add categories", "Change categories", "Choose categories"):
        btn = await page.query_selector(f'button:has-text("{label}")')
        if btn:
            await btn.scroll_into_view_if_needed()
            await page.wait_for_timeout(400)
            try:
                await btn.click(timeout=8000)
            except Exception:
                return False
            await page.wait_for_timeout(3500)
            if logger:
                logger.info(f"  category modal opened via '{label}'")
            return True
    if logger:
        logger.warning("  no category button found")
    return False


async def _reset(page, logger):
    """Delete ALL existing category accordion rows so we rebuild from scratch.

    The modal is an accordion: each category is a row. The per-row 'Delete' link
    only shows on the *expanded* row and doesn't auto-advance, while the 'Remove'
    placement links and the 'Reset' link leave empty rows behind that then block
    'Add another category' (max 3 rows). So: delete the expanded row, expand the
    next collapsed row, repeat. KDP keeps one final empty row, which we reuse."""
    deleted = 0
    for _ in range(8):
        dele = page.locator('a').filter(has_text=re.compile(r"^Delete$")).filter(visible=True)
        if await dele.count():
            try:
                await dele.first.click(timeout=5000)
                await page.wait_for_timeout(1200); deleted += 1
                continue
            except Exception:
                pass
        # No Delete visible — expand a collapsed row so its Delete appears.
        collapsed = page.locator('a.a-expander-header[aria-expanded="false"]').filter(visible=True)
        if await collapsed.count():
            try:
                await collapsed.first.click(timeout=5000)
                await page.wait_for_timeout(1000)
                continue
            except Exception:
                pass
        break
    # KDP keeps one final row that can't be deleted; its cascade stays locked to the
    # old category. Its per-row "Reset" link clears the placement and re-enables the
    # level-0 select so we can re-drive it ("0 out of 3 category placements").
    reset = page.locator('a').filter(has_text=re.compile(r"^Reset$")).filter(visible=True)
    if await reset.count():
        try:
            await reset.first.click(timeout=5000)
            await page.wait_for_timeout(1500)
        except Exception:
            pass
    if logger:
        logger.info(f"  deleted {deleted} row(s) + reset last")
    return True


async def set_categories(page, target_paths, logger=None):
    """Apply up to 3 category paths in the already-loaded title-setup page."""
    if not await _open_modal(page, logger):
        return []
    await _reset(page, logger)

    applied = []
    for path in target_paths[:3]:
      # Best-effort per category — a flaky modal interaction on one target must
      # never abort the others (or the whole publish). Any error → skip this one.
      try:
        segs = _path_segments(path)
        if not segs:
            continue
        top_tokens = _tokens(segs[0])
        rest_tokens = _tokens(" ".join(segs[1:])) or top_tokens
        if logger:
            logger.info(f"  → target: {path}")

        # After the first category, get a fresh cascade row via "Add another
        # category" (it enables once the current row is complete and we're < 3).
        if applied:
            add = page.locator('button:has-text("Add another category"), button:has-text("Add category")')
            for _ in range(5):
                if await add.count() and await add.first.is_enabled():
                    break
                await page.wait_for_timeout(1000)
            if await add.count() and await add.first.is_enabled():
                try:
                    await add.first.click(timeout=8000)
                except Exception:
                    pass
                await page.wait_for_timeout(1800)

        # Drive the level-0 (Category) select EXPLICITLY, even if it still holds a
        # value from a just-removed category — re-selecting it re-renders the whole
        # cascade with fresh, unset child levels. (Reset/Remove leaves the row fully
        # populated at the old path, so _pick_select would otherwise grab a stale
        # deep select and never navigate to the new branch.)
        l0 = await _select_at_level(page, 0)
        if not l0:
            # The reused last row (KDP keeps ≥1) may be collapsed — expand it so its
            # cascade selects become visible/editable.
            await _expand_a_row(page)
            l0 = await _select_at_level(page, 0)
        if not l0:
            if logger: logger.warning("    no level-0 category select; skipping")
            continue
        if not await _choose(page, l0["name"], l0["options"], top_tokens, logger):
            continue
        await page.wait_for_timeout(1200)
        # Deeper levels (Subcategory, optional 3rd) — match each against its OWN
        # path segment, not a lumped token bag. Lumping let a wrong-but-overlapping
        # option win (e.g. "Literature & Fiction > Beginner Readers" drilled into
        # "Historical Fiction" because both share "fiction"). The final segment is
        # the placement leaf; once segments run out we match deeper selects against
        # the leaf tokens, which correctly scores 0 on a wrong subtree and stops.
        mids = segs[1:-1]               # subcategory levels between L0 and the leaf
        leaf_tokens = _tokens(segs[-1]) or rest_tokens
        mi = 0
        for _ in range(2):
            s2 = await _pick_select(page, rest_tokens)
            if not s2:
                break
            seg_tokens = _tokens(mids[mi]) if mi < len(mids) else leaf_tokens
            if not await _choose(page, s2["name"], s2["options"], seg_tokens, logger):
                break
            if mi < len(mids):
                mi += 1
        # Leaf placement checkbox
        chosen = await _check_best_placement(page, leaf_tokens, logger)
        if chosen:
            applied.append(path)
      except Exception as e:
        if logger:
            logger.warning(f"    target failed, skipping: {str(e)[:100]}")
        continue
      if len(applied) >= 3:
        break

    # If nothing matched, CANCEL so we don't save an empty category set — a book
    # with zero categories fails KDP's "Save and Continue" validation and hangs.
    # Cancel discards the Reset, preserving the book's existing categories.
    if not applied:
        try:
            cancel = page.locator('button:has-text("Cancel")')
            if await cancel.count():
                await cancel.first.click(timeout=8000)
                await page.wait_for_timeout(1500)
        except Exception:
            pass
        if logger:
            logger.warning("  0 categories matched — cancelled (kept existing)")
        return applied

    try:
        save = page.locator('button:has-text("Save categories")')
        if await save.count():
            await save.first.click(timeout=8000)
            await page.wait_for_timeout(2000)
            if logger:
                logger.info(f"  saved {len(applied)} categor(ies): {applied}")
    except Exception as e:
        # Modal glitched on save — fall back to Cancel so the details page isn't
        # left with an open/half-saved modal blocking Save and Continue.
        if logger:
            logger.warning(f"  Save categories failed ({str(e)[:60]}); cancelling")
        try:
            await page.locator('button:has-text("Cancel")').first.click(timeout=8000)
            await page.wait_for_timeout(1500)
        except Exception:
            pass
        return []
    return applied


if __name__ == "__main__":
    # Standalone test: fix one book's categories and verify. SAVES to KDP.
    import asyncio, logging, sys
    from pathlib import Path
    from playwright.async_api import async_playwright

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("cat")
    slug = sys.argv[1] if len(sys.argv) > 1 else "easy-taxes-self-employed-spain"
    listing = json.loads((Path("/root/kdp") / slug / "listing.json").read_text())
    targets = listing.get("categories", [])
    log.info(f"targets for {slug}: {targets}")

    async def run():
        sf = str(Path(__file__).parent / "kdp_session.json")
        async with async_playwright() as p:
            b = await p.chromium.launch(headless=True)
            ctx = await b.new_context(storage_state=sf)
            pg = await ctx.new_page()
            await pg.goto(f"https://kdp.amazon.com/en_US/title-setup/kindle/{listing['kdp_book_id']}/details",
                          wait_until="domcontentloaded", timeout=60000)
            await pg.wait_for_timeout(4000)
            applied = await set_categories(pg, targets, log)
            log.info(f"APPLIED: {applied}")
            await pg.screenshot(path="/tmp/claude-0/-root/cf572050-ccdf-47d8-bd83-9038c1012e0f/scratchpad/catset.png")
            await b.close()

    asyncio.run(run())
