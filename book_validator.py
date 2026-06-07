"""
book_validator.py — KDP book structure validator and auto-fixer.

The primary bug this fixes: when step2_continue_book() is called because
the initial book was too short, GPT appends new Part N headings AFTER the
already-written back matter (Resources, References, About the Author, etc.),
producing duplicate Part numbers and misplaced back matter.

Fix applied: detects Parts that appear after back matter and moves them to
before back matter, restoring correct book order.
"""

import re
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


# ── Back matter section patterns (must only appear at END of book) ────────────
# Matches # or ## level headings so we catch both "# Resources" and "## Resources"
_BACK_MATTER_RE = re.compile(
    r'^#{1,2}\s+(?:'
    # English
    r'Resources?|References?|Bibliography|'
    r'About\s+(?:the\s+)?Authors?|'
    r'(?:Please\s+)?Leave\s+a\s+Review|Review\s+Request|'
    r'Disclaimer|'
    # German
    r'(?:Über|Ueber)\s+(?:d(?:en?|ie)\s+)?Autou?r(?:in)?|'
    r'Haftungsausschluss|Quellen|Literatur(?:verzeichnis)?|'
    r'Ressourcen|Bewertung(?:s(?:bitte|anfrage))?|'
    # Spanish
    r'Sobre\s+(?:el?|la)\s+[Aa]utora?|'
    r'Descargo\s+de\s+[Rr]esponsabilidad|Recursos|Referencias|'
    r'Deja\s+una\s+Rese|'
    # French
    r'[ÀA]\s+propos\s+de|Ressources|R[ée]f[ée]rences|'
    r'Clause\s+de\s+non.responsabilit[ée]|'
    # Italian
    r"Informazioni\s+sull'autore?|Risorse|Riferimenti|"
    r'Esclusione\s+di\s+responsabilit[àa]|Lascia\s+una\s+Recensione|'
    # Portuguese
    r'Sobre\s+o\s+[Aa]utor(?:a)?|Recursos|Refer[êe]ncias|'
    r'Aviso\s+Legal|Deixe\s+uma\s+Avalia|'
    # Dutch
    r'Over\s+de\s+[Aa]uteur|Bronnen|Referenties|'
    # Polish
    r'O\s+[Aa]utorze|[ŹZ]r[oó]d[łl]a|Zastrze[żz]enie'
    r')\b.*$',
    re.IGNORECASE | re.MULTILINE,
)

# Part headings:  # Part 4: Title  /  # Teil 4  /  # Parte 4  etc.
_PART_NUMBER_RE = re.compile(
    r'^#\s+(?:Part|Teil|Parte|Ph[aầ]n|Раздел|Bagian|Partie|Deel|'
    r'Sección|Bölüm|部分?|篇)\s+(\d+)',
    re.IGNORECASE | re.MULTILINE,
)

_H1_RE = re.compile(r'^#\s+\S', re.MULTILINE)
_H2_RE = re.compile(r'^##\s+\S', re.MULTILINE)


@dataclass
class QAIssue:
    severity: str   # "error" | "warning" | "info"
    message: str
    fix_applied: bool = False


@dataclass
class QAReport:
    issues: List[QAIssue] = field(default_factory=list)
    total_h1: int = 0
    total_h2: int = 0
    back_matter_position: str = "end"   # "end" | "middle" | "missing"
    duplicate_part_numbers: List[int] = field(default_factory=list)
    passed: bool = True

    def add(self, severity: str, message: str, fix_applied: bool = False):
        self.issues.append(QAIssue(severity, message, fix_applied))
        # Only unfixed errors cause a hard failure
        if severity == "error" and not fix_applied:
            self.passed = False

    def recompute_passed(self):
        """Recalculate passed based on remaining unfixed errors."""
        self.passed = not any(
            iss.severity == "error" and not iss.fix_applied for iss in self.issues
        )

    def summary(self) -> str:
        lines = [
            "=== KDP Book QA Report ===",
            f"H1 sections  : {self.total_h1}",
            f"H2 sections  : {self.total_h2}",
            f"Back matter  : {self.back_matter_position}",
        ]
        if self.duplicate_part_numbers:
            lines.append(f"Dup. Parts   : {self.duplicate_part_numbers}")
        lines.append(f"Status       : {'PASS ✓' if self.passed else 'FAIL ✗'}")
        if self.issues:
            lines.append("\nIssues:")
            for iss in self.issues:
                icon = {"error": "✗", "warning": "⚠", "info": "•"}.get(iss.severity, "•")
                tag = " [auto-fixed]" if iss.fix_applied else ""
                lines.append(f"  {icon} [{iss.severity.upper()}] {iss.message}{tag}")
        return "\n".join(lines)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _back_matter_start(content: str) -> Optional[int]:
    """Character offset where the first back matter heading begins, or None."""
    m = _BACK_MATTER_RE.search(content)
    if not m:
        return None
    return content.rfind('\n', 0, m.start()) + 1


def split_back_matter(content: str) -> Tuple[str, str]:
    """Return (main_body, back_matter).  back_matter is '' if none found."""
    pos = _back_matter_start(content)
    if pos is None:
        return content, ""
    return content[:pos].rstrip(), "\n\n" + content[pos:]


def split_at_back_matter(content: str) -> Tuple[str, str]:
    """Alias of split_back_matter — used by gpt_fallback_writer."""
    return split_back_matter(content)


def get_last_part_number(content: str) -> int:
    """Return the highest Part number found in content (0 if none)."""
    nums = [int(m.group(1)) for m in _PART_NUMBER_RE.finditer(content)]
    return max(nums) if nums else 0


def extract_part_outline(content: str) -> str:
    """Return a formatted list of existing Part headings for use in prompts.

    Example output:
        Part 1: The New Era of Freelance Design
        Part 2: Getting Started with AI Tools
        Part 3: Crafting Powerful Prompts

    Returns '(no Parts found)' if none detected.
    """
    lines = content.split('\n')
    parts = []
    for line in lines:
        stripped = line.strip()
        if _PART_NUMBER_RE.match(stripped):
            # Remove leading # and clean up
            parts.append(stripped.lstrip('#').strip())
    if not parts:
        return "(no Parts found)"
    return "\n".join(f"  - {p}" for p in parts)


def _duplicate_part_numbers(content: str) -> List[int]:
    from collections import Counter
    c = Counter(int(m.group(1)) for m in _PART_NUMBER_RE.finditer(content))
    return sorted(n for n, cnt in c.items() if cnt > 1)


def _parts_are_sequential(content: str) -> bool:
    """Return True if Part numbers are 1,2,3,…N with no gaps or repeats."""
    nums = [int(m.group(1)) for m in _PART_NUMBER_RE.finditer(content)]
    return nums == list(range(1, len(nums) + 1))


# ── Core fixers ────────────────────────────────────────────────────────────────

def _renumber_duplicate_parts(content: str, report: QAReport) -> str:
    """
    Renumber duplicate # Part N headings so all are sequential.

    After moving continuation before back matter, Part numbers can still
    duplicate (e.g., original Part 4,5,6 followed by continuation Part 4,5,6).
    The second and later occurrences of any number get new sequential numbers
    starting from max_existing + 1.

    Example: Parts 1,2,3,4,5,6 then 4,5,6  →  1,2,3,4,5,6 then 7,8,9
    """
    seen_nums: set = set()
    max_num = get_last_part_number(content)
    next_new: List[int] = [max_num + 1]
    changed: List[bool] = [False]

    def _replace(m: re.Match) -> str:
        num = int(m.group(1))
        full = m.group(0)
        if num not in seen_nums:
            seen_nums.add(num)
            return full
        new_num = next_new[0]
        next_new[0] += 1
        changed[0] = True
        rel_start = m.start(1) - m.start()
        rel_end   = m.end(1)   - m.start()
        return full[:rel_start] + str(new_num) + full[rel_end:]

    result = _PART_NUMBER_RE.sub(_replace, content)
    if changed[0]:
        report.add(
            "error",
            "Duplicate Part numbers renumbered to maintain sequential order.",
            fix_applied=True,
        )
    return result


def _fix_continuation_after_back_matter(content: str, report: QAReport) -> str:
    """
    Move any Part headings that appear inside/after the back matter block
    to before the back matter.

    Broken layout:
        # Part 1 … # Part 6  →  # Resources  →  # References  →  # Part 4 (continuation)

    Fixed layout:
        # Part 1 … # Part 6  →  # Part 4 (continuation)  →  # Resources  →  # References
    """
    bm_pos = _back_matter_start(content)
    if bm_pos is None:
        return content

    back_section = content[bm_pos:]

    part_match = _PART_NUMBER_RE.search(back_section)
    if not part_match:
        return content

    cont_line_start = back_section.rfind('\n', 0, part_match.start()) + 1
    pure_back_matter = back_section[:cont_line_start].rstrip()
    continuation     = back_section[cont_line_start:].strip()
    main_body        = content[:bm_pos].rstrip()

    # Renumber ALL Part headings in the continuation sequentially after main_body.
    # This ensures correct ordering even when continuation has non-duplicate Part
    # numbers (e.g., continuation had Parts 5,6,7 while main had 1-6 → becomes 7,8,9)
    next_part = get_last_part_number(main_body) + 1
    counter = [next_part]

    def _seq_replace(m: re.Match) -> str:
        full = m.group(0)
        rel_start = m.start(1) - m.start()
        rel_end   = m.end(1)   - m.start()
        result = full[:rel_start] + str(counter[0]) + full[rel_end:]
        counter[0] += 1
        return result

    continuation = _PART_NUMBER_RE.sub(_seq_replace, continuation)

    fixed = main_body + "\n\n" + continuation + "\n\n" + pure_back_matter
    report.add(
        "error",
        "Continuation chapters were appended after back matter — restructured to correct order.",
        fix_applied=True,
    )
    return fixed


# ── Public API ─────────────────────────────────────────────────────────────────

def validate_and_fix(content: str) -> Tuple[str, QAReport]:
    """
    Validate book structure, auto-fix known issues.
    Returns (fixed_content, report).
    passed=True means the book is ready to export (all errors were resolved).
    """
    report = QAReport()

    report.total_h1 = len(_H1_RE.findall(content))
    report.total_h2 = len(_H2_RE.findall(content))

    # Pre-fix: assess initial state (informational only — fixers will run next)
    bm_pos = _back_matter_start(content)
    if bm_pos is None:
        report.back_matter_position = "missing"
        report.add("warning", "No back matter section found (Resources/References/About the Author).")
    elif _PART_NUMBER_RE.search(content[bm_pos:]):
        report.back_matter_position = "middle"
    else:
        report.back_matter_position = "end"

    # Apply all auto-fixers (each adds its own issue with fix_applied=True)
    content = _fix_continuation_after_back_matter(content, report)
    content = _renumber_duplicate_parts(content, report)

    # Final sequential cleanup: if Part numbers are still non-sequential (e.g.,
    # 1,2,3,4,5,6,8,9,7 from a partial prior fix), renumber them 1,2,3,…N.
    if not _parts_are_sequential(content):
        counter = [1]

        def _seq_fix(m: re.Match) -> str:
            full = m.group(0)
            rel_start = m.start(1) - m.start()
            rel_end   = m.end(1)   - m.start()
            result = full[:rel_start] + str(counter[0]) + full[rel_end:]
            counter[0] += 1
            return result

        content = _PART_NUMBER_RE.sub(_seq_fix, content)
        report.add(
            "error",
            "Part numbers were non-sequential after renaming — resequenced to 1,2,3,…N.",
            fix_applied=True,
        )

    # Post-fix: check what remains unfixed
    remaining_dupes = _duplicate_part_numbers(content)
    if remaining_dupes:
        # This is a hard error — duplicates survived renumbering
        report.add(
            "error",
            f"Duplicate Part numbers could not be resolved: {remaining_dupes}. Manual edit required.",
            fix_applied=False,
        )
        report.duplicate_part_numbers = remaining_dupes
    else:
        report.duplicate_part_numbers = []

    final_bm = _back_matter_start(content)
    if final_bm is not None:
        report.back_matter_position = (
            "middle" if _PART_NUMBER_RE.search(content[final_bm:]) else "end"
        )
    if report.back_matter_position == "middle":
        report.add("error", "Back matter is still not at the end of the book after auto-fix.", fix_applied=False)

    # passed = no unfixed errors remain
    report.recompute_passed()

    return content, report
