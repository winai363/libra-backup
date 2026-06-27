#!/usr/bin/env python3
"""
restore_open_items.py — One-shot-until-done restorer for the two juvenile books
whose categories were left incomplete while resolving KDP "open items" (reading
age + Adult/YA category conflict) on 2026-06-27.

Both books got their reading age set and (book 1) the category conflict resolved,
but the category re-apply ran before the multi-category modal bug was fixed, so
each ended with only 1 (or a wrong) category. They then went In Review for 24-72h.

This script re-runs update_metadata() — which now applies all 3 verified
categories from listing.json — for each slug, but ONLY once the book is LIVE
again (update_metadata returns False while a book is still In Review). On success
it stamps listing.json so the work isn't repeated. Safe to run daily via cron;
it no-ops once both books are done.

Run: python3 restore_open_items.py
"""
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import kdp_upload

KDP_DIR = Path("/root/kdp")
SLUGS = ["teen-anxiety-workbook-french", "bilingual-english-spanish-kids-vocab"]
STAMP = "open_items_categories_restored_at"


def main():
    pending = []
    for slug in SLUGS:
        f = KDP_DIR / slug / "listing.json"
        d = json.loads(f.read_text())
        if d.get(STAMP):
            print(f"[{slug}] already restored ({d[STAMP]}) — skip")
            continue
        pending.append(slug)

    if not pending:
        print("All open-item books restored. Nothing to do (cron can be removed).")
        return

    for slug in pending:
        print(f"[{slug}] attempting category restore via --meta …")
        ok = asyncio.run(kdp_upload.update_metadata(slug))
        if ok:
            f = KDP_DIR / slug / "listing.json"
            d = json.loads(f.read_text())
            d[STAMP] = datetime.now().isoformat(timespec="seconds")
            f.write_text(json.dumps(d, ensure_ascii=False, indent=2))
            print(f"[{slug}] ✅ restored 3 categories + stamped")
        else:
            print(f"[{slug}] ⏳ not restored yet (likely still In Review) — will retry next run")


if __name__ == "__main__":
    main()
