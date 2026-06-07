#!/usr/bin/env python3
"""
GPT-4.1 Fallback Writer for KDP Auto-Generate
Used when Claude Code is unavailable (token limit, downtime, etc.)
"""

import os
import sys
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv("/root/libra/.env")

KDP_DIR = Path(os.getenv("KDP_DIR", "/root/kdp"))
AUTHOR = os.getenv("AUTHOR_NAME", "WK Bui")
GUIDELINES = Path("/root/libra/kdp-writing-guidelines.md").read_text()

# Languages that don't use spaces as word delimiters — use character count instead
CJK_LANGS = {'ja', 'zh', 'zh-tw', 'zh-cn', 'ko'}


def content_units(content, lang_code):
    """Return word count for Latin langs, character count for CJK langs."""
    if lang_code in CJK_LANGS:
        # Count non-whitespace, non-markdown-syntax chars
        return len([c for c in content if c.strip() and c not in '`*#[]()!_~|'])
    return len(content.split())


def pages_estimate(units, lang_code):
    """Estimate printed pages from units."""
    if lang_code in CJK_LANGS:
        return units // 600  # ~600 chars per page for CJK
    return units // 300      # ~300 words per page for Latin


def continuation_threshold(lang_code):
    """Trigger continuation if content is below this."""
    return 30000 if lang_code in CJK_LANGS else 10000


def abort_threshold(lang_code):
    """Abort if content is still below this after continuation."""
    return 30000 if lang_code in CJK_LANGS else 10000


def get_existing_books():
    """List existing books to avoid duplicates."""
    books = []
    for listing in KDP_DIR.glob("*/listing.json"):
        try:
            data = json.loads(listing.read_text())
            books.append(f"- {data.get('title', '')} ({data.get('language', '')})")
        except Exception:
            continue
    return "\n".join(books)


def call_gpt(messages, max_tokens=16000):
    """Call GPT-4.1 API."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-4.1",
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.7,
    )
    choice = response.choices[0]
    if choice.finish_reason and choice.finish_reason != "stop":
        print(f"  WARNING: GPT finish_reason={choice.finish_reason} (may be truncated)")
    return choice.message.content


def call_gpt_with_search(prompt, max_tokens=8000):
    """Call GPT-4.1 with web search enabled (Responses API)."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.responses.create(
        model="gpt-4.1",
        tools=[{"type": "web_search"}],
        input=prompt,
        max_output_tokens=max_tokens,
    )
    # Extract text from response output items
    texts = []
    for item in response.output:
        if hasattr(item, 'content'):
            for block in item.content:
                if hasattr(block, 'text'):
                    texts.append(block.text)
        elif hasattr(item, 'text'):
            texts.append(item.text)
    return "\n".join(texts) if texts else str(response.output)


def step0_market_research(existing_books):
    """Research real market demand before choosing a topic."""
    prompt = f"""You are a KDP market research analyst. Research REAL market demand for ebook topics.

Existing books (avoid these topics):
{existing_books}

YOUR TASK — use web search to find:
1. Search for "best selling kindle ebooks 2026" and "trending ebook niches 2026"
2. Search for trending topics in different language marketplaces (German, Spanish, French, Italian, etc.)
3. Search for "amazon kindle bestseller categories" in non-English markets
4. Look for underserved niches — topics with high demand but few quality books
5. Check which languages/marketplaces are underrepresented in existing books above

After researching, provide:
- TOP 5 promising topics with evidence of demand (search results, bestseller rankings)
- For each: language, marketplace, estimated demand level, competition level
- Your #1 recommendation with reasoning

Be specific — cite actual search findings, not guesses."""

    return call_gpt_with_search(prompt)


def step1_choose_topic(existing_books, market_research):
    """Choose marketplace, language, and topic."""
    prompt = f"""You are a KDP ebook strategist. Choose a NEW profitable ebook topic based on REAL market research.

Existing books (DO NOT duplicate):
{existing_books}

=== MARKET RESEARCH FINDINGS ===
{market_research}
=== END RESEARCH ===

Instructions:
1. Use the market research above to pick a topic with REAL proven demand
2. Pick a language/marketplace that is NOT overrepresented in existing books
3. Prioritize: high demand + low competition niches found in the research
4. The topic must be specific enough to fill 20-30 pages with real value
5. Choose NON-FICTION only. Do not choose novels, romance, fantasy, poetry, or other fiction.

Return JSON only (no markdown):
{{"language": "Full Language Name", "lang_code": "xx", "marketplace": "amazon.xx", "title": "Book Title in Target Language", "subtitle": "Subtitle in Target Language", "slug": "short-slug-english", "niche": "niche category", "description_en": "Brief English description of the book"}}"""

    result = call_gpt([{"role": "user", "content": prompt}])
    # Extract JSON from response
    # Find JSON by matching balanced braces
    depth = 0
    start = result.find('{')
    if start == -1:
        raise ValueError(f"No JSON found in response: {result[:200]}")
    for i in range(start, len(result)):
        if result[i] == '{': depth += 1
        elif result[i] == '}': depth -= 1
        if depth == 0:
            try:
                return json.loads(result[start:i+1])
            except json.JSONDecodeError:
                break
    raise ValueError(f"Could not parse JSON: {result[:200]}")


def step1b_content_research(topic):
    """Research real sources and facts before writing."""
    prompt = f"""You are a research assistant preparing sources for an ebook.

Book topic: {topic['title']} ({topic['language']})
Niche: {topic['niche']}
Description: {topic['description_en']}

YOUR TASK — use web search to find REAL sources:
1. Search for expert articles, guides, and official resources about this topic
2. Search in BOTH English AND {topic['language']} for broader coverage
3. Find real statistics, research findings, and expert recommendations
4. Look for official guidelines from governments, health organizations, or industry bodies
5. Find at least 8-12 credible sources

For each source provide:
- Title of the article/page
- URL
- Key facts/statistics/quotes that can be used in the book
- Why this source is credible

Also compile a "Key Facts Summary" — the most important data points the book should include.

Be thorough — these sources will become the book's Vancouver-style references."""

    return call_gpt_with_search(prompt, max_tokens=8000)


def step2_write_book(topic, content_research):
    """Write the full ebook content using researched sources."""
    prompt = f"""You are an expert ebook writer. Write a complete ebook in **{topic['language']}**.

Title: {topic['title']}
Subtitle: {topic['subtitle']}
Author: {AUTHOR}

=== RESEARCHED SOURCES (use these for citations) ===
{content_research}
=== END SOURCES ===

WRITING GUIDELINES (MUST follow):
{GUIDELINES}

Write the COMPLETE book content in Markdown format. Requirements:
- 10,000+ words minimum and at least 40 actual printed pages at 6×9 format
- Follow the structure: Parts → Chapters → Sections
- Include practical examples, step-by-step guides, tables, and bullet points
- Use the REAL sources above for citations [1], [2], etc. — do NOT invent references
- End with these back matter sections in order:
  1. Resources section (useful websites/tools related to the topic)
  2. Vancouver-style references appendix (REAL URLs from research only)
  3. About the Author (2–3 sentences about WK Bui as a writer focused on practical self-improvement and education)
  4. A warm, heartfelt 2–3 sentence review request — ask the reader to leave an honest review on Amazon, explaining it helps other readers discover the book
  5. Disclaimer (legal disclaimer appropriate to the topic)
- Tables must have max 4-5 columns with short cell text

LANGUAGE RULE: Write ENTIRELY in {topic['language']}. Do NOT switch to English mid-sentence. Technical brand names, abbreviations, and proper nouns may stay in their original form, but all explanatory prose must be in {topic['language']}.

INTERIOR ART: This edition uses a professional text-first interior. Do not insert image tags.

CRITICAL PDF FORMATTING RULES — violations cause broken TOC page numbers, blank pages, and headings separated from content:
- Do NOT write "# {topic['title']}" as the first heading — the title page is auto-generated from metadata
- Do NOT write "## Inhaltsverzeichnis" / "## Table of Contents" / "## Contenido" or any manually numbered chapter list — the TOC is auto-generated with correct page numbers; writing one manually causes duplicate TOC and wrong page numbers
- Start the content DIRECTLY with the preface section using a ## heading (e.g., "## Über dieses Buch", "## About This Book", "## Sobre este Libro")
- Use # heading ONLY for major parts (e.g., "# Teil 1: Grundlagen") — each # heading starts a new page in PDF, so only use for real structural divisions
- Do NOT use --- as page separators between minor sections — it creates an ugly horizontal line with no real layout benefit; just use paragraph spacing instead
- Each section (## or ###) must have at least 150 words of content so it fills at least half a page; never write a heading with only 1-2 sentences below it
- Do NOT end chapters with a "Key Takeaway", "Summary", "Fazit" or similar short box containing less than 100 words — integrate takeaways into the body text or expand them to a full section with examples

Write the entire book now — all parts, all chapters, all sections. Do not abbreviate, skip sections, or add a note like "continue in next response"."""

    return call_gpt(
        [{"role": "user", "content": prompt}],
        max_tokens=32000,
    )


def step2_continue_book(topic, main_body_content, outline_summary=""):
    """Continue writing a book that is too short.

    Args:
        topic: book metadata dict
        main_body_content: book content WITHOUT back matter (split by caller)
        outline_summary: formatted list of existing Parts so the model
                         uses correct sequential Part numbers
    """
    word_count = len(main_body_content.split())
    next_part_hint = ""
    if outline_summary and outline_summary != "(no Parts found)":
        next_part_hint = (
            f"\nCURRENT BOOK OUTLINE (Parts already written — do NOT repeat these):\n"
            f"{outline_summary}\n\n"
            f"Continue AFTER the last Part listed above. "
            f"Number new Parts sequentially from where the outline ends."
        )

    prompt = f"""You are continuing to write a non-fiction ebook in {topic['language']}.

Book title: {topic['title']}
The main content so far is {word_count} words, which is too short. \
A KDP paperback needs 10,000+ words to fill 30+ pages.
{next_part_hint}

Add at least 3 more full chapters in {topic['language']}. Each chapter must have:
- A Part heading with # (e.g., # Part 7: Advanced Strategies)
- 4–5 sections with ## (each section must have 200+ words)
- Practical content: examples, step-by-step guides, or tables
- Target: 3,000+ words of additional content

CRITICAL RULES:
- Do NOT include back matter (Resources, References, About the Author, Disclaimer) — those are added separately after all chapters are written.
- Do NOT repeat Part numbers that are already in the outline above.
- Do NOT write a table of contents or title heading.
- Write ENTIRELY in {topic['language']}. Do NOT switch languages.

Write the additional chapters now:"""

    return call_gpt([{"role": "user", "content": prompt}], max_tokens=16000)


def step2c_generate_images(book_dir, content, topic):
    """Generate AI images for placeholders in ebook.md using gpt-image-1.

    GPT inserts markers like ![caption](images/img_1.jpg) during writing.
    This function generates each referenced image and saves it to book_dir/images/.
    Returns updated content (image tags removed for any that fail to generate).
    """
    import re, base64, io
    try:
        from PIL import Image as PILImage
    except ImportError:
        print("  WARNING: Pillow not available, skipping image generation")
        return content

    pattern = re.compile(r'!\[([^\]]*)\]\(images/(img_\d+\.jpg)\)')
    matches = pattern.findall(content)
    if not matches:
        print("  No image placeholders found in content")
        return content

    images_dir = book_dir / "images"
    images_dir.mkdir(exist_ok=True)

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    genre_hint = topic.get('niche', topic.get('description_en', ''))

    updated_content = content
    for alt_text, filename in matches:
        img_path = images_dir / filename
        if img_path.exists():
            continue

        img_prompt = (
            f"{alt_text}. "
            f"Professional editorial photography style, topic: {genre_hint}. "
            "Clean, realistic composition, suitable for a non-fiction book interior. "
            "No text, no words, no labels, no letters anywhere in the image."
        )

        try:
            print(f"  Generating {filename}: {alt_text[:60]}")
            response = client.images.generate(
                model="gpt-image-1",
                prompt=img_prompt,
                size="1024x1024",
                n=1,
            )
            img_data = base64.b64decode(response.data[0].b64_json)
            img = PILImage.open(io.BytesIO(img_data))
            img.save(str(img_path), "JPEG", quality=88)
            print(f"    Saved: {img_path}")
        except Exception as e:
            print(f"    WARNING: Image generation failed for {filename}: {e}")
            # Remove the broken image tag so PDF/EPUB renders cleanly
            updated_content = updated_content.replace(
                f'![{alt_text}](images/{filename})', ''
            )

    return updated_content


def step3_write_listing(topic):
    """Generate KDP listing (title, description, keywords)."""
    prompt = f"""Create a conversion-focused, policy-compliant Amazon KDP listing in {topic['language']}.

Title: {topic['title']}
Subtitle: {topic['subtitle']}
Niche: {topic['niche']}

SEO REQUIREMENTS:
- Keep the title natural and accurate; do not keyword-stuff or make unsupported bestseller claims
- Subtitle must state the reader, problem, and practical outcome
- Description: 700-1,500 characters, persuasive but accurate, with short readable paragraphs
- Exactly 7 unique localized long-tail keyword phrases, 2-50 characters each
- Keywords must represent distinct search intents and must not repeat the title verbatim
- Exactly 2 highly relevant KDP category paths in the target language/marketplace
- No competitor author names, trademarks used for traffic, pricing, or temporary claims

Return JSON only (no markdown):
{{
  "title": "{topic['title']}",
  "subtitle": "{topic['subtitle']}",
  "author": "{AUTHOR}",
  "language": "{topic['language']}",
  "description": "Book description in {topic['language']} (700-1500 chars, with line breaks)",
  "keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5", "keyword6", "keyword7"],
  "categories": ["category1", "category2"],
  "status": "draft",
  "created_at": "{time.strftime('%Y-%m-%d %H:%M')}"
}}"""

    result = call_gpt([{"role": "user", "content": prompt}], max_tokens=2000)
    json_match = re.search(r'\{.*\}', result, re.DOTALL)
    if json_match:
        return json.loads(json_match.group())
    raise ValueError(f"Could not parse listing JSON: {result}")


def generate_cover(book_dir, title, subtitle, author, categories=None, keywords=None):
    """Generate a smart genre-aware book cover using cover_generator."""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from cover_generator import generate_cover as _gen
        _gen(
            book_dir   = book_dir,
            title      = title,
            subtitle   = subtitle,
            author     = author,
            categories = categories or [],
            keywords   = keywords   or [],
        )
        print(f"  Cover generated: {book_dir / 'cover.jpg'}")
        return True
    except Exception as e:
        print(f"  WARNING: Cover generation failed: {e}")
        return False


def step4_create_files(topic, content, listing, market_research="", content_research=""):
    """Save all files to disk."""
    book_dir = KDP_DIR / topic["slug"]
    book_dir.mkdir(parents=True, exist_ok=True)

    # ebook.md — (1) strip OpenAI tracking params, (2) fix layout defects
    content = re.sub(r'\?utm_source=openai', '', content)

    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent))
        import md_cleaner
        content = md_cleaner.clean(content)
        print("  md_cleaner: structural fixes applied to ebook.md")
    except Exception as e:
        print(f"  WARNING: md_cleaner failed: {e}")

    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent))
        from book_validator import validate_and_fix
        content, qa_report = validate_and_fix(content)
        print(f"  book_validator: {qa_report.summary()}")
        # Write QA report to disk for audit
        (book_dir / "qa-report.txt").write_text(qa_report.summary(), encoding="utf-8")
    except Exception as e:
        print(f"  WARNING: book_validator failed: {e}")

    (book_dir / "ebook.md").write_text(content, encoding="utf-8")

    # listing.json
    (book_dir / "listing.json").write_text(
        json.dumps(listing, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # metadata.yaml
    (book_dir / "metadata.yaml").write_text(
        f'---\ntitle: "{topic["title"]}"\n'
        f'subtitle: "{topic["subtitle"]}"\n'
        f'author: "{AUTHOR}"\n'
        f'lang: {topic["lang_code"]}\n'
        f'date: "{time.strftime("%Y")}"\n---\n',
        encoding="utf-8",
    )

    # Generate cover
    generate_cover(
        book_dir   = book_dir,
        title      = topic["title"],
        subtitle   = topic["subtitle"],
        author     = AUTHOR,
        categories = listing.get("categories", []),
        keywords   = listing.get("keywords", []),
    )

    # Save research files for audit/debugging (clean tracking params)
    if market_research:
        (book_dir / "market-research.md").write_text(
            re.sub(r'\?utm_source=openai', '', market_research), encoding="utf-8")
    if content_research:
        (book_dir / "content-research.md").write_text(
            re.sub(r'\?utm_source=openai', '', content_research), encoding="utf-8")

    print(f"Files created in {book_dir}")
    return book_dir


def step5_generate_epub(book_dir):
    """Generate EPUB from ebook.md."""
    import subprocess

    md_file = book_dir / "ebook.md"
    meta_file = book_dir / "metadata.yaml"
    epub_file = book_dir / "ebook.epub"

    cmd = [
        "pandoc", str(md_file), "-f", "markdown-yaml_metadata_block",
        "-o", str(epub_file),
        "--resource-path", str(book_dir),
        "--metadata-file", str(meta_file),
        "--epub-cover-image", str(book_dir / "cover.jpg"),
        "--css", "/root/libra/epub.css",
        "--toc", "--toc-depth=2",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        print(f"EPUB generation failed: {result.stderr[:300]}")
        return False
    print(f"EPUB generated: {epub_file}")
    return True


def notify_telegram(message):
    """Send notification to Telegram."""
    import urllib.request
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": message, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Telegram notification failed: {e}")


def _validate_topic(topic):
    """Validate that topic has all required fields."""
    required = ["language", "lang_code", "title", "subtitle", "slug", "niche", "description_en"]
    missing = [k for k in required if k not in topic or not topic[k]]
    if missing:
        raise ValueError(f"Topic missing required fields: {missing}")
    # Sanitize slug
    topic["slug"] = re.sub(r'[^a-z0-9\-]', '-', topic["slug"].lower().strip())
    return topic


def run_dry_run() -> int:
    """Validate the generation pipeline boundary without API calls or writes."""
    print("=== GPT-4.1 Fallback Writer: deterministic dry run ===")
    errors = []

    required_files = [
        Path("/root/libra/quality_gate.py"),
        Path("/root/libra/editorial_review.py"),
        Path("/root/libra/market_intelligence.py"),
        Path("/root/libra/epub.css"),
        Path("/root/libra/kdp-writing-guidelines.md"),
    ]
    for path in required_files:
        if not path.is_file():
            errors.append(f"missing required file: {path}")

    for command in ("pandoc", "python3"):
        if not shutil.which(command):
            errors.append(f"missing command: {command}")

    cron = subprocess.run(
        ["crontab", "-l"],
        capture_output=True,
        text=True,
        check=False,
    )
    active_libra_cron = [
        line for line in cron.stdout.splitlines()
        if "/root/libra/" in line and line.strip() and not line.lstrip().startswith("#")
    ]
    if active_libra_cron:
        errors.append("Libra cron is active; dry-run boundary is not isolated")

    source = Path(__file__).read_text(encoding="utf-8")
    uploader_name = "kdp_" + "upload.py"
    if uploader_name in source:
        errors.append(f"writer directly references {uploader_name}")

    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1

    print("PASS: required pipeline files and commands are available")
    print("PASS: Libra cron is paused")
    print("PASS: writer has no direct KDP upload invocation")
    print("PASS: no API calls, book files, queue entries, or KDP writes were made")
    return 0


def main():
    if "--dry-run" in sys.argv[1:]:
        return run_dry_run()

    # Support --topic-file <path>: skip discovery/scoring, use pre-scouted topic
    topic_file = None
    if "--topic-file" in sys.argv:
        idx = sys.argv.index("--topic-file")
        if idx + 1 < len(sys.argv):
            topic_file = Path(sys.argv[idx + 1])

    print("=== GPT-4.1 Fallback Writer (with Research) ===")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    existing = get_existing_books()

    try:
        if topic_file and topic_file.exists():
            # topic_scout.py already did market research + scoring → skip steps 0,1,1.5
            print(f"\n[1/7] Using pre-scouted topic from {topic_file}")
            topic = json.loads(topic_file.read_text(encoding="utf-8"))
            topic = _validate_topic(topic)
            market_research = topic.pop("_market_score_raw", "")
            market_score_data = topic.pop("_market_score", None)
            print(f"  Topic: {topic['title']} ({topic['language']})")
            print(f"  Slug: {topic['slug']}")
            if market_score_data:
                print(f"  Market score: {market_score_data.get('total_score','?')}/100 → {market_score_data.get('go_no_go','?')}")
            if (KDP_DIR / topic["slug"]).exists():
                print(f"  ERROR: {topic['slug']} already exists, aborting")
                sys.exit(1)
        else:
            # Step 0: Market Research
            print("\n[1/7] Market research (web search)...")
            market_research = step0_market_research(existing)
            print(f"  Research length: {len(market_research)} chars")
            if len(market_research) < 100:
                print("  WARNING: Market research too short, proceeding with limited data")

            # Step 1: Choose topic based on research
            print("\n[2/7] Choosing topic from research...")
            topic = step1_choose_topic(existing, market_research)
            topic = _validate_topic(topic)
            print(f"  Topic: {topic['title']} ({topic['language']})")
            print(f"  Slug: {topic['slug']}")

            # Check if already exists
            if (KDP_DIR / topic["slug"]).exists():
                print(f"  ERROR: {topic['slug']} already exists, aborting")
                sys.exit(1)

            # Step 1.5: Market Intelligence Scoring (go/no-go gate)
            print("\n[2b/7] Market intelligence scoring...")
            try:
                from market_intelligence import score_and_save, print_summary, THRESHOLD
                market_score = score_and_save(topic)
                print_summary(market_score)
                if market_score["go_no_go"] == "NO-GO":
                    print(f"\n  BLOCKED: Market score {market_score['total_score']}/100 < threshold {THRESHOLD}")
                    print(f"  Reasoning: {market_score['reasoning']}")
                    print("  Aborting — choose a different topic.")
                    sys.exit(2)
                print(f"  Market score: {market_score['total_score']}/100 → GO")
            except SystemExit:
                raise
            except Exception as e:
                print(f"  WARNING: Market intelligence failed: {e}. Proceeding without score.")

        # Step 1b: Content Research
        print("\n[3/7] Content research (web search)...")
        content_research = step1b_content_research(topic)
        print(f"  Research length: {len(content_research)} chars")

        # Step 2: Write book using research
        print("\n[4/7] Writing book content...")
        content = step2_write_book(topic, content_research)
        lang_code = topic.get("lang_code", "en")
        units = content_units(content, lang_code)
        est = pages_estimate(units, lang_code)
        unit_label = "chars" if lang_code in CJK_LANGS else "words"
        print(f"  {unit_label.capitalize()}: {units} (~{est} pages)")

        # Continue in bounded passes until the 40-page minimum is reached.
        continuation_pass = 0
        while units < continuation_threshold(lang_code) and continuation_pass < 3:
            continuation_pass += 1
            print(f"  Content short ({units} {unit_label}, ~{est} pages). Adding more chapters...")
            # Split back matter out BEFORE calling continuation so the new
            # chapters are inserted into the correct position (before back matter),
            # preventing duplicate Part numbers after Resources/References/etc.
            from book_validator import split_at_back_matter, extract_part_outline
            main_body, back_matter = split_at_back_matter(content)
            outline_summary = extract_part_outline(main_body)
            print(f"  Existing outline:\n{outline_summary}")
            continuation = step2_continue_book(topic, main_body, outline_summary)
            content = main_body.rstrip() + "\n\n" + continuation.lstrip()
            if back_matter:
                content = content.rstrip() + "\n\n" + back_matter
                print(f"  Back matter re-attached after continuation chapters.")
            units = content_units(content, lang_code)
            est = pages_estimate(units, lang_code)
            print(f"  {unit_label.capitalize()} after continuation: {units} (~{est} pages)")

        if units < abort_threshold(lang_code):
            raise ValueError(f"Book too short ({units} {unit_label}), aborting")

        # Step 3: Create listing
        print("\n[5/7] Creating listing...")
        listing = step3_write_listing(topic)
        print(f"  Keywords: {', '.join(listing.get('keywords', []))}")

        # Step 4: Save files
        print("\n[6/7] Saving files...")
        book_dir = step4_create_files(topic, content, listing, market_research, content_research)

        # Step 5: Generate EPUB
        print("\n[7/7] Generating EPUB...")
        if not step5_generate_epub(book_dir):
            raise ValueError("EPUB generation failed")

        from quality_gate import validate_book, write_report
        gate = validate_book(topic["slug"])
        write_report(gate)
        if not gate.passed:
            listing_path = book_dir / "listing.json"
            failed_listing = json.loads(listing_path.read_text(encoding="utf-8"))
            failed_listing["status"] = "quality_failed"
            failed_listing["quality_errors"] = gate.errors
            listing_path.write_text(
                json.dumps(failed_listing, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            raise ValueError("Quality gate failed: " + "; ".join(gate.errors[:3]))

        listing_path = book_dir / "listing.json"
        ready_listing = json.loads(listing_path.read_text(encoding="utf-8"))
        ready_listing["status"] = "ready"
        ready_listing["quality_errors"] = []
        listing_path.write_text(
            json.dumps(ready_listing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        import subprocess as _subprocess
        editorial = _subprocess.run(
            ["python3", "/root/libra/editorial_review.py", topic["slug"]],
            text=True,
            timeout=900,
        )
        if editorial.returncode != 0:
            failed_listing = json.loads(listing_path.read_text(encoding="utf-8"))
            failed_listing["status"] = "quality_failed"
            listing_path.write_text(
                json.dumps(failed_listing, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            raise ValueError("AI editorial review did not approve the manuscript")

        # Step SEO: optimize keywords, categories, description using competitor research
        print("\n[SEO] Running SEO optimizer...")
        try:
            import seo_optimizer
            seo_result = seo_optimizer.optimize(topic["slug"])
            seo_optimizer.print_report(seo_result)
            print(f"  SEO score: {seo_result.get('seo_score_before','?')} → {seo_result.get('seo_score_after','?')}")
        except Exception as e:
            print(f"  WARNING: SEO optimizer failed: {e} — keeping original listing")

        # Step PRICE: smart pricing based on competitor analysis
        print("\n[PRICE] Running pricing engine...")
        try:
            import pricing_engine
            price_result = pricing_engine.analyze(topic["slug"])
            pricing_engine.print_report(price_result)
        except Exception as e:
            print(f"  WARNING: Pricing engine failed: {e} — will use default $2.99")

        # Notify success
        seo_score = seo_result.get("seo_score_after", "?") if "seo_result" in dir() else "?"
        price_rec = price_result.get("recommended_price_usd", 2.99) if "price_result" in dir() else 2.99
        notify_telegram(
            f"📚 <b>New Book (GPT-4.1 + Research)</b>\n"
            f"{topic['title']}\n"
            f"({topic['language']}) — {units} {unit_label} (~{est} pages)\n"
            f"Market score ✅ | SEO score: {seo_score}/10\n"
            f"Recommended price: ${price_rec}\n"
            f"Status: ready"
        )

    except Exception as e:
        print(f"\n❌ FAILED: {e}")
        notify_telegram(f"❌ <b>GPT-4.1 Fallback Failed</b>\n{str(e)[:200]}")
        sys.exit(1)

    print(f"\n=== Done! Book ready at {book_dir} ===")


if __name__ == "__main__":
    sys.exit(main() or 0)
