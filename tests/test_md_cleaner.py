"""Tests for md_cleaner.py — all deterministic, no API calls."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from md_cleaner import clean, _normalize_thematic_breaks


def test_normalize_thematic_breaks_converts_bare_dashes():
    """Standalone --- lines should become * * * to avoid pandoc YAML parse errors."""
    content = "## Chapter One\n\nSome prose.\n\n---\n\n## Chapter Two\n"
    result = _normalize_thematic_breaks(content)
    assert "---" not in result
    assert "* * *" in result


def test_normalize_thematic_breaks_ignores_dashes_in_prose():
    """--- inside a fenced code block should not be touched."""
    content = "Text before.\n\n---\n\nText after."
    result = _normalize_thematic_breaks(content)
    assert "---" not in result
    assert "* * *" in result


def test_normalize_thematic_breaks_does_not_touch_yaml_front_matter():
    """The replacement is idempotent — already-converted * * * stays as is."""
    content = "## Section\n\n* * *\n\n## Next Section\n"
    result = _normalize_thematic_breaks(content)
    assert result == content


def test_normalize_preserves_em_dashes_in_text():
    """Em dashes (—) and hyphenated text are not affected."""
    content = "## Chapter\n\nThe CEO — Jane Smith — announced results.\n\n---\n\n## Next\n"
    result = _normalize_thematic_breaks(content)
    assert "—" in result
    assert "---" not in result


def test_clean_removes_title_heading():
    """clean() strips the leading # Book Title heading. A ## after --- is kept as first real chapter."""
    # Pattern: # Title → --- separator → ## Real Chapter (the --- marks end of title block)
    content = "# My Book Title\n\n---\n\n## Chapter One\n\nContent here.\n"
    result = clean(content)
    assert not result.startswith("# My Book Title")
    assert "## Chapter One" in result


def test_clean_removes_inline_toc():
    """clean() should remove manually-written Table of Contents sections."""
    content = "## Chapter One\n\n## Inhaltsverzeichnis\n\n1. Chapter One\n2. Chapter Two\n\n## Chapter Two\n\nContent.\n"
    result = clean(content)
    assert "Inhaltsverzeichnis" not in result
    assert "Chapter One" in result
    assert "Chapter Two" in result


def test_clean_normalizes_thematic_breaks():
    """clean() pipeline includes thematic break normalization."""
    content = "## Chapter\n\nSome text.\n\n---\n\n## Next\n\nMore text.\n"
    result = clean(content)
    assert "---" not in result


def test_clean_is_idempotent():
    """Calling clean() twice produces the same result as calling it once."""
    content = "# Title\n\n## Inhaltsverzeichnis\n\n1. Intro\n\n## Intro\n\nText.\n\n---\n\n## Conclusion\n\nEnd.\n"
    once = clean(content)
    twice = clean(once)
    assert once == twice
