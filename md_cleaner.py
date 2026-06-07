"""
md_cleaner.py — sanitize GPT-generated ebook markdown before PDF/EPUB generation.

Three common defects GPT produces that break PDF layout:

1. Leads with "# Book Title" heading — creates duplicate title (maketitle already
   renders it) and pollutes the pandoc TOC with wrong page numbers.

2. Contains "## Inhaltsverzeichnis" (or equivalent) as a manual TOC list — creates
   a second TOC that shadows the pandoc-generated one and shifts all page numbers.

3. Uses "---" horizontal rules immediately before "# Chapter" headings — since
   \\chapter{} forces a page break anyway, the --- is stranded on the previous
   page, leaving it near-empty with just a horizontal rule.
"""

import re

# ── Chapter heading patterns (these are REAL content, not title blocks) ──
_CHAPTER_PREFIXES = re.compile(
    r'^# (?:Teil|Part|Parte|Kapitel|Chapter|Chapitre|Capítulo|'
    r'Sezione|Bagian|Phần|Раздел|Section|篇|章)\s*\d',
    re.IGNORECASE,
)

# ── TOC heading keywords across supported languages ──
_TOC_KEYWORDS = [
    "Inhaltsverzeichnis",       # German
    "Table of Contents",        # English
    "Contents",
    "Contenido",                # Spanish
    r"[ÍI]ndice",               # Spanish / Italian / Portuguese
    "Indice",
    "Sommario",                 # Italian
    r"Sum[áa]rio",              # Portuguese
    r"M[ụu]c\s*l[ụu]c",        # Vietnamese
    "目录", "目次",               # Chinese / Japanese
    "Inhoudsopgave",            # Dutch
    "Table des matières",       # French
    "Sommaire",
    "Kazalo",                   # Croatian / Slovenian
    "Содержание",               # Russian
    r"Spis tre[śs]ci",          # Polish
    "Tartalomjegyzék",          # Hungarian
]
_TOC_HEADING_RE = re.compile(
    r'^##\s+(?:' + '|'.join(_TOC_KEYWORDS) + r')\s*$',
    re.IGNORECASE | re.MULTILINE,
)


def _is_title_block_heading(line: str) -> bool:
    """Return True if this # heading looks like a book title (not a chapter)."""
    stripped = line.strip()
    if not stripped.startswith('# '):
        return False
    # If it matches a chapter prefix pattern, it's a real chapter — keep it
    if _CHAPTER_PREFIXES.match(stripped):
        return False
    # Otherwise treat it as a title heading — remove it
    return True


def _remove_title_block(content: str) -> str:
    """
    Strip the leading title block that GPT writes at the top of the markdown.

    GPT-generated title blocks look like:
        # Book Title
        ## Subtitle Heading          ← subtitle heading (skip)
        ### Tagline                  ← tagline heading (skip)
        **Autor: WK Bui**            ← bold metadata (skip)
        ---                          ← separator
        ## Über dieses Buch          ← FIRST REAL CONTENT (keep)

    Key rule: a `## ` heading that appears BEFORE the first `---` separator is
    still part of the title block (subtitle/tagline).  A `## ` that appears
    AFTER a `---` separator is the first real content section.  This lets us
    correctly skip "## Creative Subtitle" while keeping "## Über dieses Buch".

    Edge case: if no `---` separator exists (rare), we still skip all
    consecutive `## `/ `### ` headings immediately after `# Title` and keep
    the first `## ` that follows a blank line after any bold/italic metadata.
    """
    lines = content.split('\n')
    i = 0
    # Skip leading blank lines
    while i < len(lines) and not lines[i].strip():
        i += 1

    if i >= len(lines) or not _is_title_block_heading(lines[i]):
        return content

    i += 1  # consume the # Title line
    seen_separator = False   # True after we hit a --- separator
    seen_h2_subtitle = False # True after we skip a ## subtitle heading
    seen_metadata = False    # True after we see a **bold** / *italic* line

    while i < len(lines):
        s = lines[i].strip()

        if s == '---':
            seen_separator = True
            i += 1

        elif s == '':
            i += 1

        elif s.startswith('## ') or s.startswith('### '):
            if seen_separator:
                # ## after --- → first real content, stop
                break
            elif seen_metadata and not seen_h2_subtitle:
                # We saw **bold** metadata before any ## → this ## is content, stop.
                # (Pattern: # Title / **Autor:** / ## Real Section)
                break
            elif seen_h2_subtitle:
                # Already skipped one ## subtitle → this second one is content, stop
                break
            else:
                # First ## with no preceding metadata or --- → subtitle, skip
                seen_h2_subtitle = True
                i += 1

        elif re.match(r'^\*+[^*]', s):
            # **Bold** or *italic* metadata line → skip, mark metadata seen
            seen_metadata = True
            i += 1

        elif _CHAPTER_PREFIXES.match(s):
            # Real chapter heading (e.g. "# Teil 1") → content
            break

        else:
            # Real prose content → stop
            break

    return '\n'.join(lines[i:]).lstrip('\n')


def _remove_inline_toc(content: str) -> str:
    """
    Remove any manually-written TOC section that GPT inserts.

    Matches a ## TOC heading followed by a numbered list, and removes both
    the heading and the list (up to the next ## / # heading or ---).
    """
    lines = content.split('\n')
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _TOC_HEADING_RE.match(line):
            # Skip heading
            i += 1
            # Skip the list body and any blank lines until next structural element
            while i < len(lines):
                s = lines[i].strip()
                # Stop at: another heading, --- separator, or a non-list non-blank line
                if s.startswith('#') or s == '---':
                    break
                # List items: "1. ...", "- ...", "* ..."
                if re.match(r'^(\d+[\.\)]\s|[-*]\s)', s) or s == '':
                    i += 1
                    continue
                break
            # Also consume the trailing --- if present
            if i < len(lines) and lines[i].strip() == '---':
                i += 1
        else:
            result.append(line)
            i += 1
    return '\n'.join(result)


def _strip_dash_before_chapter(content: str) -> str:
    """
    Remove --- horizontal rules that appear immediately before a # chapter
    heading.  \\chapter{} already forces a new page, so --- stranded on the
    previous page creates a near-empty page with just a horizontal rule.
    """
    # Pattern: \n---\n (optional blank lines) # heading
    content = re.sub(r'\n---\n(\s*\n)(#[^#])', r'\n\n\1\2', content)
    # Also remove a leading --- at the very start of the document
    content = content.lstrip('\n')
    if content.startswith('---\n'):
        content = content[4:]
    return content


def _remove_orphaned_metadata(content: str) -> str:
    """
    Remove bold/italic/separator lines that precede the first ## heading.

    These are leftover title-block remnants (**Subtitle**, *Autorin: WK Bui*,
    ---) that survive when the # Title heading was already stripped in a
    previous pass.  We only remove them when every line before the first ##
    is blank, ---, **bold**, or *italic* — never if real prose is present.
    """
    lines = content.split('\n')
    j = 0
    while j < len(lines):
        s = lines[j].strip()
        if s.startswith('## ') or _CHAPTER_PREFIXES.match(s):
            # All lines before j were metadata/blank — drop them
            if j > 0:
                return '\n'.join(lines[j:]).lstrip('\n')
            return content
        elif s.startswith('# ') and not _CHAPTER_PREFIXES.match(s):
            # Unhandled title heading — leave for _remove_title_block
            return content
        elif s == '' or s == '---' or re.match(r'^\*+[^*\n]', s):
            j += 1  # blank, horizontal rule, or **bold** / *italic* line
        else:
            return content  # real prose found — don't strip
    return content


def _normalize_thematic_breaks(content: str) -> str:
    """Replace bare --- thematic breaks with * * * to prevent pandoc YAML parse errors.

    Pandoc's yaml_metadata_block extension treats standalone --- as YAML block
    starts anywhere in the document. This converts them to an equivalent
    thematic break that pandoc won't misread as YAML.
    Only replaces --- on its own line (with optional trailing whitespace).
    """
    return re.sub(r'(?m)^---[ \t]*$', '* * *', content)


def clean(content: str) -> str:
    """
    Apply all fixes in order and return the cleaned markdown.
    Safe to call multiple times (idempotent).
    """
    content = _remove_title_block(content)
    content = _remove_orphaned_metadata(content)
    content = _remove_inline_toc(content)
    content = _strip_dash_before_chapter(content)
    content = _normalize_thematic_breaks(content)
    return content.lstrip('\n')
