#!/usr/bin/env python3
"""
metadata_polish.py — Deterministic, post-LLM cleanup of KDP listing metadata.

The LLM (seo_optimizer) is *asked* to follow Amazon's 2026 keyword/description
rules, but compliance is unverified. This module enforces them in code so a bad
generation can't ship a suppression-risk listing. Two public functions:

  sanitize_keywords(keywords, title, subtitle, categories) -> (clean, dropped)
  to_html_description(text) -> str   # whitelisted-HTML, conversion-structured

Amazon 2026 rules enforced (sources: Kindlepreneur keyword rules rev. Dec 2025,
KDP metadata guidelines): 7 keyword slots × 50 chars, no punctuation inside a
phrase, no Amazon program names, no subjective/claim words, no phrases fully
redundant with the title/subtitle, no generic single words. Description: only
<b><i><br><p><h4>-<h6><ul><ol><li> render in the KDP blurb box (4,000-char cap).
"""

import html as _html
import re

# Amazon program names — using these as keywords risks listing suppression.
_PROGRAM = (
    "kindle unlimited", "kdp select", "prime reading", "kindle direct",
    "audible", "kindle scribe",
)

# Subjective / hype claim markers Amazon bans from keywords. Matched at WORD
# boundaries so "debt free", "new mothers", "freelancers" are never tripped —
# only unambiguous product-hype is blocked. Multilingual hype words included
# only where they are unambiguous (e.g. "bestseller", "gratis", "kostenlos").
_CLAIM_PATTERNS = [
    r"#\s*1\b", r"\bnumber one\b", r"\bbest[\s-]?sell(?:er|ing)\b",
    r"\btop[\s-]?rated\b", r"\baward[\s-]?winning\b", r"\bnew release\b",
    r"\bon sale\b", r"\b(?:5|five)[\s-]?star\b", r"\bmoney[\s-]?back\b",
    r"\bbest seller\b",
    # Unambiguous non-English hype
    r"\bbestseller\b", r"\bkostenlos\b", r"\bgratuit(?:e)?\b",
    r"\bgratis\b", r"\bgratuit[oa]\b", r"\bmás vendido\b", r"\bmas vendido\b",
]
_CLAIM_RE = re.compile("|".join(_CLAIM_PATTERNS), re.IGNORECASE)

# Generic single words that waste a slot (Amazon indexes these for everyone).
_GENERIC = {
    "book", "books", "ebook", "ebooks", "kindle", "amazon", "guide", "read",
    "reading", "libro", "libros", "buch", "livre", "boek",
}

# Stopwords stripped before the "fully redundant with title" check (multilingual,
# small on purpose — only the highest-frequency function words).
_STOP = {
    "the", "a", "an", "of", "for", "to", "and", "in", "on", "your", "you", "with",
    "el", "la", "los", "las", "de", "del", "para", "y", "con", "un", "una",
    "der", "die", "das", "und", "für", "mit", "den", "ein", "eine",
    "le", "les", "des", "et", "pour", "avec", "un", "une",
    "het", "een", "en", "voor", "met",
}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zà-ÿ0-9]+", text.lower())


def _clean_phrase(phrase: str) -> str:
    """Strip punctuation Amazon penalises; keep letters/digits/space/hyphen."""
    p = phrase.strip().strip('"“”‘’')
    p = re.sub(r"[^\w\s\-à-ÿ]", " ", p, flags=re.UNICODE)  # drop , . # ! etc.
    p = re.sub(r"\s+", " ", p).strip()
    return p


def _is_banned(phrase: str) -> str | None:
    """Return a reason string if the phrase must be dropped, else None."""
    low = phrase.lower()
    if any(prog in low for prog in _PROGRAM):
        return "amazon-program-name"
    if _CLAIM_RE.search(phrase):
        return "claim-hype"
    # A phrase that is nothing but generic/stopwords carries no search value.
    content = set(_words(phrase)) - _STOP
    if content and content <= _GENERIC:
        return "generic-only"
    return None


def sanitize_keywords(keywords, title="", subtitle="", categories=None):
    """Enforce Amazon 2026 keyword rules deterministically.

    Returns (clean_keywords, dropped) where dropped is a list of
    (original_phrase, reason). Order is preserved; near-duplicates and phrases
    fully redundant with the title/subtitle are removed. Never raises.
    """
    categories = categories or []
    # Verbatim title/subtitle strings — a keyword that merely echoes the whole
    # title is wasted (Amazon already indexes title words). Partial word overlap
    # is fine and common in good long-tails, so we only block near-exact repeats.
    title_norm = " ".join(_words(title))
    subtitle_norm = " ".join(_words(subtitle))

    clean, dropped, seen = [], [], set()
    for raw in keywords or []:
        original = str(raw)
        # Check hype/program markers on the RAW phrase first — cleaning strips the
        # very punctuation ("#1", quotes) that signals a banned claim.
        reason = _is_banned(original)
        if reason:
            dropped.append((original, reason))
            continue
        phrase = _clean_phrase(original)
        if not phrase:
            dropped.append((original, "empty-after-clean"))
            continue
        # Amazon's 50-per-slot limit is byte-counted (UTF-8): accented chars cost
        # 2 bytes, so a 49-char Spanish phrase can overflow and the WHOLE slot is
        # ignored. Trim by bytes at a word boundary.
        while len(phrase.encode("utf-8")) > 50:
            trimmed = phrase.rsplit(" ", 1)[0].strip()
            if not trimmed or trimmed == phrase:
                phrase = phrase[:len(phrase) - 1].strip()
            else:
                phrase = trimmed
        if len(phrase) < 2:
            dropped.append((original, "too-short"))
            continue
        # Re-check on the cleaned form too (e.g. generic-only after punctuation drop).
        reason = _is_banned(phrase)
        if reason:
            dropped.append((original, reason))
            continue
        # Drop only if the phrase is essentially the title/subtitle verbatim
        # (exact repeat or a contiguous slice of it) — not mere word overlap.
        pnorm = " ".join(_words(phrase))
        if pnorm and (pnorm == title_norm or pnorm == subtitle_norm
                      or (len(pnorm) > 12 and (pnorm in title_norm or pnorm in subtitle_norm))):
            dropped.append((original, "redundant-with-title"))
            continue
        key = phrase.casefold()
        # Collapse trivial singular/plural duplicates (English trailing 's').
        norm = re.sub(r"s\b", "", key)
        if key in seen or norm in seen:
            dropped.append((original, "duplicate"))
            continue
        seen.add(key)
        seen.add(norm)
        clean.append(phrase)
        if len(clean) == 7:
            break
    return clean, dropped


# --- Description → whitelisted HTML -----------------------------------------

_ALLOWED_TAGS = {"b", "i", "br", "p", "h4", "h5", "h6", "ul", "ol", "li", "strong", "em"}
_BULLET_RE = re.compile(r"^\s*(?:[-*•·–]|\d+[.)])\s+(.*)$")


def to_html_description(text: str, max_chars: int = 4000) -> str:
    """Convert a plain-text description into conversion-structured, whitelisted
    HTML for the KDP description box: bold hook line, short <p> paragraphs,
    a <ul> of benefit bullets, and the closing call-to-action paragraph.

    Input is treated as plain text — any stray markup is escaped, so the output
    can only contain the whitelisted tags this function emits. Caps at max_chars
    (KDP limit 4,000) by trimming trailing blocks, never breaking a tag.
    """
    if not text:
        return ""
    # If it already looks like our HTML (re-run), strip to plain text first.
    if re.search(r"</?(p|ul|li|h[4-6]|b|strong)>", text, re.I):
        text = re.sub(r"<[^>]+>", "\n", text)
        text = _html.unescape(text)

    raw_lines = [ln.strip() for ln in text.replace("\r", "").split("\n")]
    # Group into blocks: consecutive bullet lines → one list; other non-empty
    # lines → paragraphs (blank line separates paragraphs).
    blocks = []  # ("hook"|"p"|"ul", payload)
    buf_para = []
    buf_bullets = []

    def flush_para():
        if buf_para:
            blocks.append(("p", " ".join(buf_para)))
            buf_para.clear()

    def flush_bullets():
        if buf_bullets:
            blocks.append(("ul", list(buf_bullets)))
            buf_bullets.clear()

    for ln in raw_lines:
        if not ln:
            flush_para()
            flush_bullets()
            continue
        m = _BULLET_RE.match(ln)
        if m:
            flush_para()
            buf_bullets.append(m.group(1).strip())
        else:
            flush_bullets()
            buf_para.append(ln)
    flush_para()
    flush_bullets()

    if not blocks:
        return ""

    # First paragraph block becomes the bold hook.
    out = []
    hook_done = False
    for kind, payload in blocks:
        if kind == "p" and not hook_done:
            out.append(f"<b>{_html.escape(payload)}</b>")
            hook_done = True
        elif kind == "p":
            out.append(f"<p>{_html.escape(payload)}</p>")
        elif kind == "ul":
            items = "".join(f"<li>{_html.escape(it)}</li>" for it in payload if it)
            if items:
                out.append(f"<ul>{items}</ul>")

    # Assemble, trimming trailing blocks until within the char cap.
    while out:
        html_doc = "".join(out)
        if len(html_doc) <= max_chars:
            return html_doc
        out.pop()
    return ""


if __name__ == "__main__":
    # Tiny self-test.
    kws = ["kindle unlimited tax guide", "tax guide for freelancers spain",
           "bestseller finance book", "autonomo iva 2026", "debt free living tips",
           "self employed taxes spain", "new mothers tax", "book", "impuestos autonomos",
           "easy taxes for the self employed in spain"]
    clean, dropped = sanitize_keywords(kws, title="Easy Taxes for the Self-Employed in Spain")
    print("CLEAN:", clean)
    print("DROPPED:", dropped)
    desc = ("Struggling with Spanish taxes as a freelancer?\n\n"
            "This plain-language guide walks you through every step.\n\n"
            "- File your IVA with confidence\n- Claim every deduction\n- Avoid costly fines\n\n"
            "Start saving money today — grab your copy now.")
    print("\nHTML:\n", to_html_description(desc))
