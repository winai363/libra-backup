#!/usr/bin/env python3
"""
kdp_bookshelf_roster.py — Read the FULL KDP bookshelf (read-only) and reconcile
it with our local listing.json files.

Why this exists:
  Two blind spots in the pipeline (audit 2026-06-25):
    1. kdp_upload.py saves kdp_book_id (internal title id) but not always the ASIN
       (the public amazon.com/dp/ id) — so we cannot verify a book is actually live.
    2. kdp_sales_sync.py only queries topEarningTitles (earners only) — it never
       lists the whole catalog, so we are blind to drafts / in-review / blocked books.

  This script logs into the bookshelf with the saved session, walks EVERY entry
  (handling pagination), and for each row captures: book_id, ASIN, status
  (LIVE / IN REVIEW / DRAFT / BLOCKED / UNPUBLISHED), format, and the raw text.
  It maps each row to a local slug by title token-overlap (reusing the proven
  matcher from kdp_sales_sync) and backfills `asin` + `live_status` into listing.json.

  NO writes are made to KDP. Local listing.json files are updated only to record
  the ASIN and the observed live status.

Usage:
  python3 kdp_bookshelf_roster.py            # fetch, reconcile, write
  python3 kdp_bookshelf_roster.py --dry-run  # fetch + print, write nothing
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path

LIBRA_DIR = Path(__file__).parent
KDP_DIR = LIBRA_DIR.parent / "kdp"
SESSION_FILE = LIBRA_DIR / "kdp_session.json"
LOG_FILE = LIBRA_DIR / "logs" / "bookshelf-roster.log"
ROSTER_FILE = KDP_DIR / "bookshelf-roster.json"
ACK_FILE = KDP_DIR / "roster-acknowledged.json"  # ASINs already known as dup/orphan

sys.path.insert(0, str(LIBRA_DIR))
# Reuse the index builder + tokenizer from sales sync. We deliberately do NOT use
# resolve_slug here: it writes to listing.json as a side effect (bad for --dry-run)
# and has no guard against two KDP rows claiming the same slug.
from kdp_sales_sync import build_indexes, _load_json, _tokens  # noqa: E402

# Status priority when several bookshelf rows resolve to the same local book
# (duplicate uploads): keep the most "real" one's ASIN.
_STATUS_RANK = {"LIVE": 4, "IN_REVIEW": 3, "DRAFT": 2, "UNPUBLISHED": 1, "UNKNOWN": 0}

ASIN_RE = re.compile(r"\bB0[A-Z0-9]{8}\b")


def _log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def classify_status(text: str) -> str:
    """Map the row's visible badge to a coarse lifecycle status.

    NOTE: 'Submitted on <date>' is a *publish date*, not a review status — KDP
    shows it on Live books too. So we never treat 'submitted' as in-review.
    The real status badge is one of Live / In review / Draft / Blocked.
    """
    t = " ".join((text or "").lower().split())
    if "in review" in t:
        return "IN_REVIEW"
    if "blocked" in t or "quality issue" in t or "needs your attention" in t:
        return "BLOCKED"
    if "draft" in t:
        return "DRAFT"
    if "unpublish" in t:  # "unpublished" / "unpublishing"
        return "UNPUBLISHED"
    if "live" in t:
        return "LIVE"
    return "UNKNOWN"


PRICE_RE = re.compile(r"\$\s?(\d+\.\d{2})\s*([A-Z]{3})")


def _telegram(msg: str) -> None:
    """Fire-and-forget Telegram alert (same env keys as notify-new-books.py)."""
    import urllib.request
    env = {}
    p = LIBRA_DIR / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    token, chat = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        _log("  (no telegram creds — skipping alert)")
        return
    try:
        data = json.dumps({"chat_id": chat, "text": msg, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as exc:
        _log(f"  telegram send failed: {exc}")


def compute_alerts(report: dict) -> dict:
    """Find NEW problems worth pinging Bui about:
       - live_duplicates: a book with ≥2 LIVE rows (active cannibalization)
       - live_orphans:    a LIVE book on KDP with no local listing
    """
    # Group every entry by the (book, format) it belongs to (winner.slug or
    # duplicate_of). Keep format in the key so an ebook + its paperback edition
    # aren't mistaken for a cannibalizing duplicate.
    groups: dict[tuple, list] = {}
    for e in report["entries"]:
        key = e.get("slug") or e.get("duplicate_of")
        if key:
            groups.setdefault((key, e.get("format", "ebook")), []).append(e)
    live_dups = {}
    for (slug, _fmt), group in groups.items():
        live = [e for e in group if e["status"] == "LIVE"]
        if len(live) >= 2:
            live_dups[slug] = [e["asin"] for e in live]
    live_orphans = [e for e in report["orphans"] if e["status"] == "LIVE"]

    # Takedown watch: a tracked book now shown BLOCKED on the bookshelf.
    blocked = [e for e in report["entries"]
               if e["status"] == "BLOCKED" and (e.get("slug") or e.get("duplicate_of"))]

    # Vanished watch: a book our listing.json records as LIVE that no longer
    # appears on the bookshelf at all (possible silent takedown). Guard against a
    # partial fetch — only trust this when the roster looks complete.
    gone = []
    if report["total_rows"] >= 20:
        present = {e.get("slug") for e in report["entries"] if e.get("slug")}
        for lf in KDP_DIR.glob("*/listing.json"):
            d = _load_json(lf, {})
            slug = lf.parent.name
            if d.get("live_status") == "LIVE" and slug not in present:
                gone.append({"slug": slug, "asin": d.get("asin"), "title_guess": d.get("title", slug)})

    return {"live_duplicates": live_dups, "live_orphans": live_orphans,
            "blocked": blocked, "gone": gone}


# ---------- bookshelf scrape ----------

# Pull every book "row" off the page. We anchor on the edit links KDP renders for
# each title (/kindle/<ID>/ , /paperback/<ID>/ , /hardcover/<ID>/), climb to the
# enclosing row container, and capture its full text. Dedup by book_id.
_EXTRACT_JS = r"""
() => {
    const rows = {};
    const idRe = /\/(kindle|paperback|hardcover)\/([A-Z0-9]{8,})\//;
    for (const link of document.querySelectorAll("a[href]")) {
        const m = (link.href || "").match(idRe);
        if (!m) continue;
        const bookId = m[2];
        // Climb to the full row container — needs to be large enough to also
        // include the TITLE cell, not just the format/status/actions cell.
        let node = link;
        for (let i = 0; i < 12 && node.parentElement; i++) {
            node = node.parentElement;
            if ((node.innerText || "").length > 140) break;
        }
        const text = (node.innerText || "").replace(/\s+/g, " ").trim().slice(0, 600);
        if (!rows[bookId] || text.length > rows[bookId].length) {
            rows[bookId] = text;
        }
    }
    return rows;  // { bookId: rowText }
}
"""


async def _find_next_button(page):
    """Return a clickable, enabled 'next page' control or None."""
    selectors = [
        'button[aria-label="Next page"]:not([disabled])',
        'a[aria-label="Next page"]',
        'li.a-last:not(.a-disabled) a',
        'span.a-pagination li.a-last:not(.a-disabled) a',
    ]
    for sel in selectors:
        el = await page.query_selector(sel)
        if el:
            return el
    return None


async def fetch_bookshelf() -> dict:
    """Return { book_id: row_text } across all bookshelf pages."""
    from playwright.async_api import async_playwright

    all_rows: dict[str, str] = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(storage_state=str(SESSION_FILE))
            page = await context.new_page()
            await page.goto(
                "https://kdp.amazon.com/en_US/bookshelf",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            if "signin" in page.url or "/ap/" in page.url:
                raise RuntimeError("KDP session expired — re-run kdp_login_setup.py")

            # Show as many rows as possible on one page (bookshelf defaults to 10).
            await page.wait_for_timeout(3000)
            try:
                sel = "#refreshedbookshelftable-records-per-page-dropdown-option"
                opts = await page.evaluate(
                    f"""() => {{
                        const s = document.querySelector('{sel}');
                        return s ? [...s.options].map(o => o.value) : [];
                    }}"""
                )
                if opts:
                    best = max(opts, key=lambda v: int(re.sub(r"\D", "", v) or 0))
                    await page.select_option(sel, best)
                    _log(f"  set records-per-page -> {best}")
                    await page.wait_for_timeout(3000)
            except Exception as exc:  # fall back to pagination loop
                _log(f"  records-per-page set failed ({exc}); using pagination")

            for page_no in range(1, 30):  # hard stop at 30 pages
                await page.wait_for_timeout(2500)
                # Lazy content: scroll to bottom to force-load the row list.
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1200)
                rows = await page.evaluate(_EXTRACT_JS)
                before = len(all_rows)
                for bid, txt in rows.items():
                    if bid not in all_rows or len(txt) > len(all_rows[bid]):
                        all_rows[bid] = txt
                _log(f"  page {page_no}: saw {len(rows)} rows (total unique {len(all_rows)})")

                nxt = await _find_next_button(page)
                if not nxt:
                    break
                # If clicking next adds nothing new twice, bail to avoid loops.
                await nxt.click()
                await page.wait_for_timeout(2500)
                if len(all_rows) == before:
                    rows2 = await page.evaluate(_EXTRACT_JS)
                    if all(b in all_rows for b in rows2):
                        break
            return all_rows
        finally:
            await browser.close()


# ---------- reconcile ----------

def _title_match(api_tokens: set[str], listings: dict) -> tuple[str | None, float]:
    """Best slug by token overlap (same rule kdp_sales_sync uses)."""
    if not api_tokens:
        return None, 0.0
    best_slug, best_score = None, 0.0
    for slug, info in listings.items():
        lt = info["tokens"]
        if not lt:
            continue
        score = len(lt & api_tokens) / len(lt)
        if score > best_score:
            best_slug, best_score = slug, score
    if best_slug and best_score >= 0.6 and len(listings[best_slug]["tokens"] & api_tokens) >= 3:
        return best_slug, best_score
    return None, best_score


def reconcile(rows: dict[str, str], dry_run: bool) -> dict:
    by_asin, _by_title, listings = build_indexes()

    # asin/book_id -> (slug, format). build_indexes only knows the ebook (top-level
    # asin), so add the paperback/hardcover editions recorded under their own sub-key
    # — otherwise their LIVE bookshelf rows look like orphans (false "no listing" alert).
    asin_index = {a: (s, "ebook") for a, s in by_asin.items()}
    bookid_index = {}
    for slug, info in listings.items():
        d = _load_json(info["path"], {})
        if d.get("kdp_book_id"):
            bookid_index[d["kdp_book_id"]] = (slug, "ebook")
        for fmt in ("paperback", "hardcover"):
            f = d.get(fmt) or {}
            if f.get("asin"):
                asin_index[f["asin"]] = (slug, fmt)
            if f.get("kdp_book_id"):
                bookid_index[f["kdp_book_id"]] = (slug, fmt)

    # Pass 1 — build a candidate per row with a match authority:
    #   3 = book_id exact, 2 = asin exact, 1 = title token-overlap, 0 = none.
    entries = []
    for book_id, text in sorted(rows.items()):
        asin_m = ASIN_RE.search(text)
        asin = asin_m.group(0) if asin_m else None
        status = classify_status(text)
        price_m = PRICE_RE.search(text)
        title_guess = text.split("  ")[0][:120]

        slug, fmt, authority, score = None, "ebook", 0, 0.0
        if book_id in bookid_index:
            (slug, fmt), authority = bookid_index[book_id], 3
        elif asin and asin in asin_index:
            (slug, fmt), authority = asin_index[asin], 2
        else:
            slug, score = _title_match(_tokens(title_guess), listings)
            if slug:
                authority = 1

        entries.append({
            "book_id": book_id,
            "asin": asin,
            "status": status,
            "slug": slug,
            "format": fmt,
            "match_authority": authority,
            "match_score": round(score, 2),
            "price": float(price_m.group(1)) if price_m else None,
            "currency": price_m.group(2) if price_m else None,
            "title_guess": title_guess,
            "amazon_url": f"https://www.amazon.com/dp/{asin}" if asin else None,
        })

    # Pass 2 — resolve slug collisions. When several rows claim one slug (duplicate
    # uploads on KDP), the winner is highest authority, then most-real status, then
    # ASIN present. Losers are flagged as duplicates and NOT written.
    # Collisions are per (slug, format): two ebook rows for one book is a real
    # duplicate, but an ebook + its paperback are distinct legit editions.
    by_slug: dict[tuple, list] = {}
    for e in entries:
        if e["slug"]:
            by_slug.setdefault((e["slug"], e["format"]), []).append(e)

    duplicates = []
    for (slug, _fmt), group in by_slug.items():
        group.sort(
            key=lambda e: (e["match_authority"], _STATUS_RANK.get(e["status"], 0), bool(e["asin"])),
            reverse=True,
        )
        for loser in group[1:]:
            loser["slug"] = None
            loser["duplicate_of"] = slug
            duplicates.append(loser)

    fetched_at = datetime.now().isoformat(timespec="seconds")
    report = {
        "fetched_at": fetched_at,
        "total_rows": len(rows),
        "by_status": {},
        "matched": 0,
        "orphans": [],       # live/draft on KDP, no local listing
        "duplicates": duplicates,  # extra KDP rows for a book we already track
        "entries": entries,
    }
    for e in entries:
        report["by_status"][e["status"]] = report["by_status"].get(e["status"], 0) + 1

    for e in entries:
        if e["slug"]:
            report["matched"] += 1
            if not dry_run:
                lf = listings[e["slug"]]["path"]
                d = _load_json(lf, {})
                # Write ebook fields at top level; paperback/hardcover into their sub-key.
                target = d if e["format"] == "ebook" else d.setdefault(e["format"], {})
                changed = False
                if e["asin"] and target.get("asin") != e["asin"]:
                    target["asin"] = e["asin"]; changed = True
                if target.get("kdp_book_id") != e["book_id"]:
                    target["kdp_book_id"] = e["book_id"]; changed = True
                if target.get("live_status") != e["status"]:
                    target["live_status"] = e["status"]
                    target["live_status_checked_at"] = fetched_at
                    changed = True
                if changed:
                    lf.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
        elif "duplicate_of" not in e:
            report["orphans"].append(e)

    return report


def maybe_alert(report: dict) -> None:
    """Telegram Bui about NEW live-duplicates / live-orphans only (no daily spam)."""
    alerts = compute_alerts(report)
    ack = set(_load_json(ACK_FILE, {}).get("asins", []))

    new_lines, new_asins = [], set()
    for slug, asins in alerts["live_duplicates"].items():
        fresh = [a for a in asins if a not in ack]
        if fresh:
            new_lines.append(f"⚠️ <b>เล่มซ้ำขายพร้อมกัน</b> ({slug}): {', '.join(asins)}")
            new_asins.update(asins)
    for e in alerts["live_orphans"]:
        if e["asin"] not in ack:
            new_lines.append(f"👻 <b>เล่ม LIVE ไม่มี listing</b>: {e['asin']} — {e['title_guess'][:60]}")
            new_asins.add(e["asin"])
    for e in alerts["blocked"]:
        key = e.get("asin") or e.get("book_id")
        if key not in ack:
            new_lines.append(f"🚫 <b>โดน BLOCKED (Amazon takedown?)</b>: {e['asin']} — {e['title_guess'][:55]}")
            new_asins.add(key)
    for e in alerts["gone"]:
        key = e.get("asin") or e.get("slug")
        if key not in ack:
            new_lines.append(f"❓ <b>เคย LIVE แต่หายจากชั้น</b>: {e['slug']} ({e.get('asin')})")
            new_asins.add(key)

    if not new_lines:
        _log("alert: no new live-duplicates / orphans / blocked / vanished")
        return
    msg = "📕 <b>Libra KDP roster เจอปัญหาใหม่</b>\n\n" + "\n".join(new_lines) + \
          "\n\nตรวจ bookshelf / รัน kdp_unpublish.py / สร้าง listing เพื่อแก้"
    _telegram(msg)
    _log(f"alert: pinged Bui about {len(new_asins)} new item(s)")
    ack |= new_asins
    ACK_FILE.write_text(json.dumps({"asins": sorted(ack)}, indent=2), encoding="utf-8")


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    do_alert = "--alert" in sys.argv
    _log(f"=== KDP bookshelf roster (dry_run={dry_run}, alert={do_alert}) ===")
    rows = asyncio.run(fetch_bookshelf())
    _log(f"Fetched {len(rows)} bookshelf rows.")
    report = reconcile(rows, dry_run)

    if not dry_run:
        ROSTER_FILE.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    _log("---- STATUS BREAKDOWN ----")
    for st, n in sorted(report["by_status"].items(), key=lambda x: -x[1]):
        _log(f"  {st:<12} {n}")
    _log(f"matched to slug: {report['matched']}/{report['total_rows']}  "
         f"orphans: {len(report['orphans'])}  duplicates: {len(report['duplicates'])}")
    for e in report["orphans"]:
        _log(f"  ORPHAN [{e['status']}] asin={e['asin']} :: {e['title_guess'][:70]}")
    for e in report["duplicates"]:
        _log(f"  DUPLICATE of {e['duplicate_of']} [{e['status']}] asin={e['asin']} :: {e['title_guess'][:50]}")
    if not dry_run:
        _log(f"Roster written -> {ROSTER_FILE}")
    if do_alert:
        maybe_alert(report)


if __name__ == "__main__":
    main()
