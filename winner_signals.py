#!/usr/bin/env python3
"""
winner_signals.py — the learning loop's memory of what already sold.

Reads the per-book sales snapshots (kdp/<slug>/feedback-history.json, populated
by kdp_sales_sync.py) and surfaces the books with real traction. topic_scout.py
uses this to (a) prompt GPT for ADJACENT topics serving the same proven buyers
and (b) boost the ranking of candidates in a proven niche/language.

Window default = 90 days: sales are sparse, so a proven niche should keep
influencing discovery for a while, not expire after 30 days.

Usage:
  python3 winner_signals.py            # print current winners
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

KDP_DIR = Path(__file__).parent.parent / "kdp"
WINDOW_DAYS = 90


def _load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return default


def _sum_recent(history: list[dict], field: str, days: int, today: date) -> float:
    total = 0.0
    for snap in history:
        d = snap.get("date", "")
        try:
            sd = date.fromisoformat(str(d)[:10])
        except ValueError:
            continue
        if (today - sd).days <= days:
            try:
                total += float(snap.get(field, 0) or 0)
            except (TypeError, ValueError):
                pass
    return total


def get_winners(window_days: int = WINDOW_DAYS, today: date | None = None) -> list[dict]:
    """Return books with real traction in the window, richest first.

    A winner = any book whose sales snapshots show units, KENP pages, or royalty
    > 0 in the window. Each entry carries the niche/language/marketplace that
    topic_scout needs to find adjacent opportunities.
    """
    today = today or date.today()
    winners = []
    for hf in sorted(KDP_DIR.glob("*/feedback-history.json")):
        slug = hf.parent.name
        history = _load_json(hf, [])
        if not history:
            continue
        units = _sum_recent(history, "units_7d", window_days, today)
        kenp = _sum_recent(history, "kenp_7d", window_days, today)
        revenue = round(_sum_recent(history, "revenue_usd", window_days, today), 2)
        if units <= 0 and kenp <= 0 and revenue <= 0:
            continue
        listing = _load_json(hf.parent / "listing.json", {})
        winners.append({
            "slug": slug,
            "title": listing.get("title", slug),
            "niche": listing.get("niche", "") or "",
            "language": listing.get("language", "") or "",
            "marketplace": listing.get("marketplace", "") or "",
            "units": int(units),
            "kenp": int(kenp),
            "revenue_usd": revenue,
        })
    winners.sort(key=lambda w: (w["revenue_usd"], w["units"], w["kenp"]), reverse=True)
    return winners


def proven_sets(winners: list[dict]) -> tuple[set[str], set[str]]:
    """Return (proven_languages_lower, proven_niche_words) for scoring boosts."""
    langs = {w["language"].lower() for w in winners if w["language"]}
    niche_words: set[str] = set()
    for w in winners:
        for word in (w["niche"] + " " + w["title"]).lower().replace(",", " ").split():
            if len(word) > 3:
                niche_words.add(word)
    return langs, niche_words


def format_for_prompt(winners: list[dict]) -> str:
    """Build the PROVEN WINNERS block injected into the discovery prompt."""
    if not winners:
        return ("PROVEN WINNERS: none yet — no book has recorded a sale. "
                "Explore freely for high-demand, low-competition niches.")
    lines = [
        "PROVEN WINNERS — these books ALREADY SOLD on KDP. Real buyers paid money "
        "for them, so the demand is verified (not guessed):",
    ]
    for w in winners[:6]:
        sig = []
        if w["units"]:
            sig.append(f"{w['units']} sale(s)")
        if w["kenp"]:
            sig.append(f"{w['kenp']} KENP pages")
        if w["revenue_usd"]:
            sig.append(f"${w['revenue_usd']} royalty")
        lang = f"{w['language']}, {w['marketplace']}".strip(", ")
        niche = f" niche={w['niche']}" if w["niche"] else ""
        lines.append(f'- "{w["title"]}" [{lang}]{niche} — {", ".join(sig)}')
    lines.append(
        "\nEXPLOIT THESE WINS: at least 2 of your 5 candidates MUST be ADJACENT "
        "topics that serve the SAME proven buyers — same language/marketplace and "
        "the same buyer persona, covering a RELATED need they will also pay for "
        "(e.g. a winning Spanish self-employed tax guide → VAT/IVA guide, "
        "bookkeeping, quarterly-tax, or deductions guide for the same Spanish "
        "autónomos). These must still pass the low-competition filter and must NOT "
        "duplicate an existing book. The remaining candidates may explore new niches."
    )
    return "\n".join(lines)


if __name__ == "__main__":
    ws = get_winners()
    if not ws:
        print("No winners yet — no book has recorded a sale.")
        sys.exit(0)
    print(f"=== Proven winners (last {WINDOW_DAYS} days) ===")
    for w in ws:
        print(f"  {w['slug']:40} {w['language']:8} units={w['units']} "
              f"kenp={w['kenp']} ${w['revenue_usd']}  niche={w['niche']!r}")
    print("\n--- prompt block preview ---")
    print(format_for_prompt(ws))
