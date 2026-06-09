#!/usr/bin/env python3
"""Top up a too-short (but structurally clean) book to the length gate by
appending new parts that CONTINUE the existing outline. Inserts before back
matter, runs the structural fixer, regenerates the EPUB, then reports QA.

Use after a surgical de-duplication leaves a book under the word/char minimum.
"""
import sys, json, importlib
from pathlib import Path

sys.path.insert(0, "/root/libra")
import gpt_fallback_writer as w
import book_validator as bv
import quality_gate as qg
importlib.reload(bv); importlib.reload(qg)

KDP = Path("/root/kdp")


def units(content, lang_code):
    return w.content_units(content, lang_code)


def main():
    slug = sys.argv[1]
    max_passes = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    bd = KDP / slug
    listing = json.loads((bd / "listing.json").read_text(encoding="utf-8"))
    lang = listing.get("language", "English")
    lang_code = {"japanese": "ja", "italian": "it", "english": "en",
                 "spanish": "es", "german": "de", "french": "fr",
                 "portuguese": "pt-BR"}.get(lang.lower(), lang[:2].lower())
    topic = {"title": listing["title"], "language": lang,
             "subtitle": listing.get("subtitle", "")}

    content = (bd / "ebook.md").read_text(encoding="utf-8")
    # The writer counts whitespace tokens; the quality gate counts fewer (strips
    # markdown/headings). Aim ~12% above the writer threshold so the gate's
    # stricter count still clears the minimum.
    target = int(w.continuation_threshold(lang_code) * 1.12)

    for p in range(max_passes):
        u = units(content, lang_code)
        print(f"[topup] pass {p}: {u} units (target {target})")
        if u >= target:
            break
        main_body, back_matter = bv.split_at_back_matter(content)
        outline = bv.extract_part_outline(main_body)
        cont = w.step2_continue_book(topic, main_body, outline)
        content = main_body.rstrip() + "\n\n" + cont.lstrip()
        if back_matter:
            content = content.rstrip() + "\n\n" + back_matter

    content, _ = bv.validate_and_fix(content)
    import md_cleaner
    importlib.reload(md_cleaner)
    content = md_cleaner.clean(content)
    (bd / "ebook.md").write_text(content, encoding="utf-8")
    w.step5_generate_epub(bd)
    print(f"[topup] final units: {units(content, lang_code)}")

    rep = qg.validate_book(slug)
    print(f"[topup] QA passed={rep.passed}")
    for e in rep.errors:
        print("   ERROR:", e)
    print(f"[topup] dup={rep.metrics.get('duplicate_chapter_numbers')} "
          f"grafted={rep.metrics.get('grafted_content_heading')}")


if __name__ == "__main__":
    main()
