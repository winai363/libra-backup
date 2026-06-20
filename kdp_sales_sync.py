#!/usr/bin/env python3
"""
kdp_sales_sync.py — Pull real KDP sales into the Libra dashboard.

Logs into KDP Reports with the saved session, reads the dashboard JSON APIs
(per-ASIN orders, KENP pages, royalties for the current month), and records a
daily snapshot into each book's kdp/<slug>/feedback-history.json — the same file
profit_tracker.py / the Libra dashboard already read.

Why month-to-date + diff (not "today"):
  KDP processes orders up to a few days after they are placed, and restates
  royalties later. We store the last-seen cumulative per ASIN and record the
  daily *increment* (delta). That keeps profit_tracker's window sums correct and
  still captures late-arriving data.

Run daily via cron. Idempotent within a day (re-running replaces today's entry).

Usage:
  python3 kdp_sales_sync.py            # sync today
  python3 kdp_sales_sync.py --dry-run  # fetch + print, write nothing
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

LIBRA_DIR = Path(__file__).parent
KDP_DIR = LIBRA_DIR.parent / "kdp"
SESSION_FILE = LIBRA_DIR / "kdp_session.json"
STATE_FILE = KDP_DIR / "sales-sync-state.json"
LOG_FILE = LIBRA_DIR / "logs" / "sales-sync.log"

BASE = "https://kdpreports.amazon.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


# ---------- helpers ----------

def _log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return default


# Stopwords (multi-language) dropped before token-overlap matching so generic
# connectors don't inflate the score. Topic words carry the signal.
_STOP = {
    "the", "a", "an", "of", "for", "and", "to", "in", "on", "with", "your", "you",
    "de", "la", "el", "los", "las", "para", "en", "y", "con", "del", "un", "una",
    "le", "les", "des", "et", "der", "die", "das", "und", "fur", "mit", "im",
    "guia", "guide", "guida", "guide", "paso", "step", "book", "edition",
}


def _norm_title(t: str) -> str:
    """Lowercase, strip accents (ó->o, ñ->n), collapse to single spaces."""
    t = unicodedata.normalize("NFKD", t or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def _tokens(t: str) -> set[str]:
    return {w for w in _norm_title(t).split() if w not in _STOP and len(w) > 1}


def build_indexes() -> tuple[dict, dict, dict]:
    """Return (asin->slug, normtitle->slug, slug->{path,tokens})."""
    by_asin, by_title, listings = {}, {}, {}
    for lf in sorted(KDP_DIR.glob("*/listing.json")):
        slug = lf.parent.name
        d = _load_json(lf, {})
        listings[slug] = {"path": lf, "tokens": _tokens(d.get("title", ""))}
        if d.get("asin"):
            by_asin[d["asin"]] = slug
        t = _norm_title(d.get("title", ""))
        if t:
            by_title[t] = slug
    return by_asin, by_title, listings


def _backfill_asin(slug: str, asin: str, idx) -> None:
    by_asin, _, listings = idx
    lf = listings[slug]["path"]
    d = _load_json(lf, {})
    if d.get("asin") != asin:
        d["asin"] = asin
        lf.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
        by_asin[asin] = slug
        _log(f"  backfilled asin {asin} -> {slug}")


def resolve_slug(asin: str, title: str, idx) -> str | None:
    by_asin, by_title, listings = idx
    if asin in by_asin:
        return by_asin[asin]

    # Exact normalized title (fast path).
    slug = by_title.get(_norm_title(title))
    if slug:
        _backfill_asin(slug, asin, idx)
        return slug

    # Token-overlap: KDP often merges title+subtitle, so match by how much of a
    # listing's title tokens appear in the (longer) KDP title. Pick the best.
    api_tokens = _tokens(title)
    if not api_tokens:
        return None
    best_slug, best_score = None, 0.0
    for s, info in listings.items():
        lt = info["tokens"]
        if not lt:
            continue
        score = len(lt & api_tokens) / len(lt)
        if score > best_score:
            best_slug, best_score = s, score
    if best_slug and best_score >= 0.6 and len(listings[best_slug]["tokens"] & api_tokens) >= 3:
        _log(f"  matched by tokens ({best_score:.2f}) -> {best_slug}")
        _backfill_asin(best_slug, asin, idx)
        return best_slug
    return None


def upsert_today_snapshot(slug: str, snap: dict) -> str:
    """Append today's snapshot, or replace it if one already exists for today."""
    path = KDP_DIR / slug / "feedback-history.json"
    history = _load_json(path, [])
    today = snap["date"]

    prev = next((s for s in reversed(history) if s.get("date") != today), None)
    snap["delta_units"] = snap.get("units_7d", 0) - (prev or {}).get("units_7d", 0)
    snap["delta_kenp"] = snap.get("kenp_7d", 0) - (prev or {}).get("kenp_7d", 0)

    history = [s for s in history if s.get("date") != today]
    history.append(snap)
    path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    return "updated" if prev is not None and any(s.get("date") == today for s in _load_json(path, [])) else "added"


# ---------- KDP fetch ----------

async def fetch_kdp() -> dict:
    """Return {'overview': {...}, 'titles': [{asin,title,orders,pagesRead,royalties,currency}]}."""
    from playwright.async_api import async_playwright

    date_param = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(storage_state=str(SESSION_FILE), user_agent=UA)
        page = await context.new_page()
        await page.goto(f"{BASE}/dashboard", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2500)

        async def get_json(path: str):
            resp = await context.request.get(BASE + path)
            if resp.status != 200 or "json" not in resp.headers.get("content-type", ""):
                raise RuntimeError(f"{path} -> status {resp.status} ({resp.headers.get('content-type','')[:30]})")
            return await resp.json()

        overview = (await get_json(
            f"/reports/dashboard/overview?date={date_param}&viewOption=THIS_MONTH"
        )).get("overviewWidget", {})
        titles = (await get_json(
            f"/reports/dashboard/topEarningTitles?date={date_param}&viewOption=THIS_MONTH"
        )).get("topTitlesWidget", {}).get("topTitles", [])

        await browser.close()
        return {"overview": overview, "titles": titles}


# ---------- main ----------

def sync(dry_run: bool = False) -> None:
    today = date.today().isoformat()
    month = today[:7]

    _log(f"=== KDP sales sync {today} (dry_run={dry_run}) ===")
    data = asyncio.run(fetch_kdp())
    ov = data["overview"]
    _log(f"Overview THIS_MONTH: digitalOrders={ov.get('digitalOrders')} "
         f"kenpRead={ov.get('kenpRead')} royalties={ov.get('totalRoyalties')} {ov.get('currency')}")

    state = _load_json(STATE_FILE, {})
    if state.get("month") != month:
        state = {"month": month, "titles": {}}  # cumulative resets each calendar month
    baseline = state["titles"]

    idx = build_indexes()
    new_baseline = {}
    recorded = 0

    for t in data["titles"]:
        asin = t.get("asin", "")
        cur_orders = int(t.get("orders", 0) or 0)
        cur_pages = int(t.get("pagesRead", 0) or 0)
        cur_roy = float(t.get("royalties", 0.0) or 0.0)
        new_baseline[asin] = {"orders": cur_orders, "pagesRead": cur_pages, "royalties": cur_roy}

        prev = baseline.get(asin, {"orders": 0, "pagesRead": 0, "royalties": 0.0})
        d_orders = max(0, cur_orders - int(prev.get("orders", 0)))
        d_pages = max(0, cur_pages - int(prev.get("pagesRead", 0)))
        d_roy = round(max(0.0, cur_roy - float(prev.get("royalties", 0.0))), 2)

        slug = resolve_slug(asin, t.get("title", ""), idx)
        tag = slug or f"UNMATCHED({asin})"
        _log(f"  {tag}: MTD orders={cur_orders} pages={cur_pages} roy={cur_roy} "
             f"| today +{d_orders}o +{d_pages}p +{d_roy}{t.get('currency','')}")

        if slug is None:
            _log(f"    !! no listing.json matches '{t.get('title','')[:60]}' — skipped")
            continue

        # Record a snapshot when there is a daily increment, or to seed the first
        # observation of a book that already has month-to-date sales.
        first_seen = asin not in baseline
        if d_orders == 0 and d_pages == 0 and not (first_seen and (cur_orders or cur_pages)):
            continue

        snap = {
            "date": today,
            "source": "kdp_sales_sync",
            "units_7d": d_orders,
            "kenp_7d": d_pages,
            "revenue_usd": d_roy if t.get("currency") == "USD" else 0.0,
            "mtd_orders": cur_orders,
            "mtd_kenp": cur_pages,
            "mtd_royalties": cur_roy,
            "currency": t.get("currency", ""),
        }
        if dry_run:
            _log(f"    [dry-run] would record -> {slug}: {snap}")
        else:
            how = upsert_today_snapshot(slug, snap)
            _log(f"    {how} snapshot -> {slug}")
            recorded += 1

    if not dry_run:
        state["titles"] = new_baseline
        state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        _log(f"Done. {recorded} book(s) updated. State saved -> {STATE_FILE.name}")
    else:
        _log("Done (dry-run, nothing written).")


if __name__ == "__main__":
    sync(dry_run="--dry-run" in sys.argv)
