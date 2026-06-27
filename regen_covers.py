#!/usr/bin/env python3
"""
regen_covers.py — Regenerate KDP covers with the redesigned (type/photo/
illustration-led) cover_generator. Backs up the existing cover first.

Usage:
  python3 regen_covers.py --live            # all LIVE books
  python3 regen_covers.py --slug SLUG       # one book
  python3 regen_covers.py --all             # every book with listing.json
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import cover_generator as cg

KDP = Path(os.getenv("KDP_DIR", "/root/kdp"))


def regen(slug: str) -> dict:
    bdir = KDP / slug
    lf = bdir / "listing.json"
    if not lf.exists():
        return {"slug": slug, "ok": False, "err": "no listing.json"}
    d = json.loads(lf.read_text())
    cover = bdir / "cover.jpg"
    if cover.exists():
        bak = bdir / "cover.pre-redesign.jpg"
        if not bak.exists():               # keep the FIRST backup only
            bak.write_bytes(cover.read_bytes())
    genre = cg.detect_genre(d.get("title", ""), d.get("categories", []),
                            d.get("keywords", []))
    fam = cg.COVER.get(genre, cg.COVER["default"])["family"]
    try:
        cg.generate_cover(
            book_dir=bdir,
            title=d.get("title", slug),
            subtitle=d.get("subtitle", ""),
            author=d.get("author", "WK Bui"),
            categories=d.get("categories", []),
            keywords=d.get("keywords", []),
        )
        thumb = cg._thumbnail_ok(cover)
        size = cover.stat().st_size
        return {"slug": slug, "ok": True, "genre": genre, "family": fam,
                "thumb": thumb, "kb": size // 1024}
    except Exception as e:
        return {"slug": slug, "ok": False, "genre": genre, "err": str(e)[:120]}


def main():
    args = sys.argv[1:]
    slugs = []
    if "--slug" in args:
        slugs = [args[args.index("--slug") + 1]]
    else:
        for lf in sorted(KDP.glob("*/listing.json")):
            d = json.loads(lf.read_text())
            if "--all" in args or ("--live" in args and d.get("live_status") == "LIVE"):
                slugs.append(lf.parent.name)
    print(f"Regenerating {len(slugs)} cover(s)\n")
    results = []
    for s in slugs:
        r = regen(s)
        results.append(r)
        if r["ok"]:
            warn = "" if r["thumb"] else "  ⚠️THUMB"
            print(f"  ✓ {r['family']:13} {r['slug'][:40]:40} {r['kb']}KB{warn}")
        else:
            print(f"  ✗ {r['slug'][:40]:40} {r.get('err')}")
    ok = sum(1 for r in results if r["ok"])
    warns = [r["slug"] for r in results if r.get("ok") and not r.get("thumb")]
    print(f"\nDone: {ok}/{len(results)} ok; thumbnail warnings: {len(warns)}")
    for w in warns:
        print("   ⚠️", w)


if __name__ == "__main__":
    main()
