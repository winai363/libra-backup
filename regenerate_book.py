#!/usr/bin/env python3
"""Regenerate an existing KDP book in place to fix broken/duplicated content.

Safe flow:
  1. Back up the old book dir (copy) to /root/kdp/.regen-backup/<slug>-<ts>.
  2. Reconstruct a writer topic from the old listing + market-score.
  3. Remove the old dir (writer aborts if the slug exists) and run the writer.
  4. On failure, restore from backup. On success, carry over kdp_book_id so the
     fixed manuscript can be pushed to the SAME KDP book via --update.
  5. Run the quality gate and print the result. Does NOT upload — caller decides.
"""
import sys, json, shutil, subprocess
from pathlib import Path
from datetime import datetime

KDP = Path("/root/kdp")
LANG_CODE = {"japanese": "ja", "italian": "it", "english": "en", "spanish": "es",
             "german": "de", "french": "fr", "portuguese": "pt-BR",
             "indonesian": "id"}


def build_topic(slug: str):
    bd = KDP / slug
    listing = json.loads((bd / "listing.json").read_text(encoding="utf-8"))
    niche = "General Nonfiction"
    ms = bd / "market-score.json"
    if ms.exists():
        niche = json.loads(ms.read_text(encoding="utf-8")).get("niche", niche)
    lang = listing.get("language", "English")
    desc_en = (listing.get("description_en")
               or f"A practical {niche} book titled '{listing.get('title','')}'. "
                  f"{listing.get('subtitle','')}".strip())
    topic = {
        "language": lang,
        "lang_code": LANG_CODE.get(lang.lower(), lang[:2].lower()),
        "title": listing["title"],
        "subtitle": listing.get("subtitle", ""),
        "slug": slug,
        "niche": niche,
        "description_en": desc_en,
    }
    # The writer only writes market-research.md (a QA-required file) when the
    # topic carries _market_score_raw. Reuse the existing research so the writer's
    # own gate passes (same book/topic).
    mr = bd / "market-research.md"
    if mr.exists():
        topic["_market_score_raw"] = mr.read_text(encoding="utf-8")
    return topic, listing


def main():
    slug = sys.argv[1]
    bd = KDP / slug
    topic, old_listing = build_topic(slug)
    kdp_id = old_listing.get("kdp_book_id")

    bak = KDP / ".regen-backup" / f"{slug}-{datetime.now():%Y%m%d-%H%M%S}"
    bak.parent.mkdir(exist_ok=True)
    shutil.copytree(bd, bak)
    print(f"[regen] backed up old book to {bak}")

    shutil.rmtree(bd)
    tf = Path(f"/tmp/regen-topic-{slug}.json")
    tf.write_text(json.dumps(topic, ensure_ascii=False), encoding="utf-8")
    print(f"[regen] topic: {topic['title']} ({topic['language']}) / niche={topic['niche']}")

    # Retry the writer once — GPT length variance / transient API errors can make
    # a single run fall short of the length gate.
    produced = False
    for attempt in (1, 2):
        print(f"[regen] writer attempt {attempt}...")
        r = subprocess.run(
            ["python3", "/root/libra/gpt_fallback_writer.py", "--topic-file", str(tf)],
            cwd="/root/libra",
        )
        produced = all((bd / f).exists() for f in ("listing.json", "ebook.md",
                                                    "ebook.epub", "cover.jpg"))
        if produced:
            break
        print(f"[regen] attempt {attempt} did not produce a complete book (rc={r.returncode})")
        if bd.exists():
            shutil.rmtree(bd)
    if not produced:
        print(f"[regen] WRITER FAILED (rc={r.returncode}, files missing) — restoring backup")
        if bd.exists():
            shutil.rmtree(bd)
        shutil.copytree(bak, bd)
        sys.exit(1)
    # Carry over audit files the topic-file path may skip, so QA's required-file
    # check passes (copy from the backup of the same book).
    for f in ("market-research.md", "content-research.md"):
        if not (bd / f).exists() and (bak / f).exists():
            shutil.copy(bak / f, bd / f)

    if kdp_id:
        nl = json.loads((bd / "listing.json").read_text(encoding="utf-8"))
        nl["kdp_book_id"] = kdp_id
        (bd / "listing.json").write_text(
            json.dumps(nl, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[regen] carried over kdp_book_id {kdp_id}")

    # The upload gate (require_quality_gate) demands a paperback PDF AND a passing
    # editorial-review.json for EVERY KDP write. The --topic-file writer path may
    # skip these, so produce them here — otherwise the fixed book can't be uploaded.
    print("[regen] building paperback PDF...")
    try:
        from pdf_builder import build_paperback_pdf
        pdf = build_paperback_pdf(slug, force=True)
        print(f"[regen] PDF: {pdf.name}")
    except Exception as exc:
        print(f"[regen] PDF generation FAILED: {exc}")

    if not (bd / "editorial-review.json").exists():
        print("[regen] running editorial review...")
        subprocess.run(["python3", "/root/libra/editorial_review.py", slug],
                       cwd="/root/libra")

    # Full upload gate (structural + PDF render + editorial) — the real ship test.
    print("[regen] running full upload gate...")
    import importlib
    import quality_gate as qg
    importlib.reload(qg)
    rep = qg.validate_book(slug, require_pdf=True, require_editorial=True)
    print(f"[regen] GATE passed={rep.passed}")
    for e in rep.errors:
        print("   ERROR:", e)
    dup = rep.metrics.get("duplicate_chapter_numbers")
    graft = rep.metrics.get("grafted_content_heading")
    print(f"[regen] duplicate_chapters={dup} grafted={graft}")
    print("[regen] DONE — ready to upload" if rep.passed
          else "[regen] DONE (GATE FAILED — review before upload)")


if __name__ == "__main__":
    main()
