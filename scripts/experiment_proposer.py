#!/usr/bin/env python3
"""Deterministic experiment proposer for the Libra 90-Day Profit Agent.

"AI คิด experiment ใหม่ภายใต้กรอบที่ setup ไว้": proposals are derived from
observable state (listing vs audited KDP tree, KDP Select enrollment) and
snapped to real tree leaves by category_resolver — never free-text values.
Every proposal must pass the SAME validate_action gate the executor enforces
before an experiment is created, so nothing unexecutable ever enters the queue.

Sources (zero-cost only, one variable per experiment):
- category_update: an uploaded title whose listing has a category that is not
  a real tree leaf, or whose leaf cannot be driven by the category matcher
  (stop-word leaf like "General"). Proposed value = resolver snap of the bad
  path, checked drivable.
- free_promo: KDP-Select-enrolled title that has never run a free promo.
- price_update: LIVE title with real KU reading (KENP) but ~no royalties —
  test the bottom of the 70% band ($2.99). Pricing page only, no re-review.

Caps: at most MAX_NEW_PER_RUN new experiment per run and MAX_ACTIVE active
experiments overall. A value already tried for the same slug+kind (any prior
cycle) is never proposed again. Exhausted failed experiments (3/3 attempts)
are closed as inconclusive with an audit record so the loop never jams.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

LIBRA_DIR = Path(__file__).resolve().parent.parent
import sys  # noqa: E402

sys.path.insert(0, str(LIBRA_DIR))
sys.path.insert(0, str(LIBRA_DIR / "scripts"))

from category_resolver import _tok, resolve_paths  # noqa: E402
from distribution_report import send_telegram  # noqa: E402
from kdp_action_executor import load_tree_leaves, validate_action  # noqa: E402
import kdp_action_executor as executor_config  # noqa: E402
from kdp_categories import _tokens as _matcher_tokens  # noqa: E402
from profit_agent import ACTIVE_STATUSES, create_experiment, record_action_result  # noqa: E402

KDP_DIR = Path("/root/kdp")
LEDGER_FILE = LIBRA_DIR / "data" / "libra-business.db"
MAX_ACTIVE = 3
MAX_NEW_PER_RUN = 1


def leaf_drivable(path: str) -> bool:
    """set_categories matches the placement leaf by tokens minus stop words —
    a leaf like "General" reduces to nothing and can never be targeted."""
    segments = [seg.strip() for seg in re.split(r"[>›/|]", path) if seg.strip()]
    return bool(segments) and bool(_matcher_tokens(segments[-1]))


def _segments(path: str) -> list:
    return [seg.strip() for seg in re.split(r"[>›/|]", path) if seg.strip()]


# Tokens too generic to prove a leaf fits THIS book — a 1-word overlap on
# "health" is how an anxiety workbook ends up proposed into Prostate Health.
GENERIC_TOKENS = {
    "general", "health", "business", "computer", "computers", "technology",
    "management", "personal", "life", "skills", "tools", "culture", "home",
    "based", "guide", "guides", "workbook", "adults", "money", "self", "help",
}
_STORE_ROOT_TOKENS = {"kindle", "ebooks", "ebook", "books", "book", "store"}


def strip_store_prefix(path: str) -> str:
    segments = _segments(path)
    while segments and _tok(segments[0]) <= _STORE_ROOT_TOKENS:
        segments = segments[1:]
    return " > ".join(segments)


def _context_tokens(listing: dict) -> set:
    # Title + keywords only. The book's own category paths are full of
    # structural words (computer, health, counseling) that fake an overlap.
    parts = [listing.get("title") or ""] + list(listing.get("keywords") or [])
    return _tok(" ".join(parts)) - GENERIC_TOKENS


def pick_category_fix(listing: dict, leaves: set) -> tuple[str, str] | None:
    """Return (replaces, proposed) or None — and None whenever unsure.

    Paths are judged after stripping the store-root prefix ("Kindle eBooks >")
    — a store-prefixed but otherwise-valid path is a recording artifact, not a
    broken placement, and never worth an experiment slot. Only a path whose
    normalized form is NOT a real tree leaf is a candidate. The proposed value
    must be a structural relative (child of the broken parent path, or a
    resolver snap) with >= 2 non-generic title/keyword token overlaps. All the
    book's OTHER categories must be drivable leaves, because the executor
    re-drives all 3 and aborts if any target cannot be set."""
    categories = list(listing.get("categories") or [])
    bad = next((c for c in categories if strip_store_prefix(c) not in leaves), None)
    if bad is None:
        return None
    others = [strip_store_prefix(c) for c in categories if c != bad]
    if any(o not in leaves or not leaf_drivable(o) for o in others):
        return None

    normalized = strip_store_prefix(bad)
    if any(leaf.startswith(normalized + " > ") for leaf in leaves):
        # Non-leaf parent path — consider the leaves underneath it.
        pool = [(leaf, leaf[len(normalized):]) for leaf in leaves
                if leaf.startswith(normalized + " > ") and leaf_drivable(leaf)]
    else:
        # Fully invalid path — resolver snap, judged on its last two segments.
        pool = [(leaf, " ".join(_segments(leaf)[-2:]))
                for leaf in resolve_paths([bad], sorted(leaves))
                if leaf in leaves and leaf_drivable(leaf)]
    context = _context_tokens(listing)
    best, best_score = None, 0
    for leaf, new_part in pool:
        if leaf in categories:
            continue
        score = len(context & (_tok(new_part) - GENERIC_TOKENS))
        if score > best_score:
            best, best_score = leaf, score
    if best is None or best_score < 2:
        return None
    return bad, best


def category_candidate(slug: str, listing: dict, leaves: set) -> dict | None:
    fix = pick_category_fix(listing, leaves)
    if fix is None:
        return None
    replaces, proposed = fix
    return {
        "kind": "category_update", "field": "category",
        "proposed_value": proposed, "replaces": replaces, "cost_usd": 0,
        "hypothesis": "Replacing an invalid/undrivable category with a context-matched real tree leaf can improve positioning and read-through.",
        "variable": "category",
    }


def free_promo_candidate(slug: str, listing: dict) -> dict | None:
    select = listing.get("kdp_select") or {}
    if select.get("status") != "Enrolled" or listing.get("free_promo"):
        return None
    return {
        "kind": "free_promo", "field": "promotion_window",
        "proposed_value": "2-day KDP Select free promotion", "cost_usd": 0,
        "hypothesis": "A first free promotion can surface verified commercial demand for an invisible title.",
        "variable": "promotion",
    }


def distribution_evidence(slug: str, pairings: dict, schedule: dict) -> dict:
    """Classify distribution truth without equating reminders to publication."""
    posts = [post for post in schedule.get("posts", []) if post.get("slug") == slug]
    for post in posts:
        proof = executor_config.valid_distribution_proof(post)
        if proof:
            return {
                "status": "verified",
                "usable_for_promo": True,
                "channel": post.get("channel") or "reddit",
                "proof": str(proof),
            }
    declared = pairings.get("pairings", {}).get(slug, [])
    if any(post.get("reminded_at") for post in posts):
        return {
            "status": "reminded", "usable_for_promo": False,
            "channel": "reddit", "proof": None,
        }
    if declared or posts:
        channel = (declared[0].get("channel") if declared else
                   posts[0].get("channel") or "reddit")
        return {
            "status": "planned", "usable_for_promo": False,
            "channel": channel, "proof": None,
        }
    return {
        "status": "missing", "usable_for_promo": False,
        "channel": None, "proof": None,
    }


def _distribution_evidence_for(slug: str) -> dict:
    try:
        pairings = json.loads(executor_config.PAIRINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pairings = {}
    try:
        schedule = json.loads(executor_config.REDDIT_SCHEDULE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        schedule = {}
    return distribution_evidence(slug, pairings, schedule)


PRICE_TEST_VALUE = "2.99"        # bottom of the 70%-royalty band
MIN_KENP_FOR_PRICE_TEST = 50     # someone actually READ it in KU, not a stray page
MAX_ROYALTIES_FOR_PRICE_TEST = 1.0  # ...but nobody meaningfully buys it

# --- Signal-sufficiency (distribution-starved) gate --------------------------
# A price test optimizes PAID conversion, and its outcome is measured over a
# 14-day window. That outcome is only above statistical noise if the title
# already gets enough traffic — orders (incl. free downloads/borrows) plus KU
# read-throughs — that "reads but nobody buys" is a genuine conversion signal
# rather than a visibility artifact. Below the floor a title is
# *distribution-starved*: the bottleneck is being seen, not the price, so tuning
# price/metadata just measures luck. We don't spend a scarce experiment slot
# (MAX_ACTIVE=3) on that; the title is surfaced for distribution work instead.
# Free promo — the traffic-generating lever — stays available to these titles.
PAGES_PER_BORROW = 300           # ≈ one full KU read-through of an avg nonfiction/workbook
MIN_TRAFFIC_FOR_PRICE_TEST = 10  # orders + borrow-equivalents in the peak recorded month


def _title_traffic(history: list) -> float:
    """Peak-month demand proxy from the same MTD fields kdp_sales_sync records:
    orders (incl. free downloads/borrows) + KU borrow-equivalents. This is how
    many eyeballs reach the title — a price conversion change is only measurable
    once that traffic clears MIN_TRAFFIC_FOR_PRICE_TEST."""
    orders = max((int(e.get("mtd_orders") or 0) for e in history), default=0)
    pages = max((int(e.get("mtd_kenp") or 0) for e in history), default=0)
    return orders + pages / PAGES_PER_BORROW


def distribution_starved_titles(kdp_dir: Path = KDP_DIR) -> list:
    """LIVE, published titles whose peak-month traffic is below the price-test
    measurability floor. Their bottleneck is distribution (being seen), not
    price/metadata — surfaced daily so human effort goes to traffic, not tuning."""
    starved = []
    for listing_path in sorted(kdp_dir.glob("*/listing.json")):
        try:
            listing = json.loads(listing_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if listing.get("status") != "uploaded":
            continue
        if str(listing.get("live_status") or "").upper() != "LIVE":
            continue
        try:
            history = json.loads((listing_path.parent / "feedback-history.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            history = []
        if _title_traffic(history) < MIN_TRAFFIC_FOR_PRICE_TEST:
            starved.append(listing_path.parent.name)
    return starved


def _current_price(slug: str, listing: dict, kdp_dir: Path) -> float | None:
    """listing.price is written by set_price on every change; before the first
    change the live price is whatever kdp_upload set = recommended_price_usd
    from pricing-recommendation.json. Neither record → None (skip, never guess)."""
    for value in (listing.get("price"),):
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            return None
    try:
        rec = json.loads((kdp_dir / slug / "pricing-recommendation.json").read_text(encoding="utf-8"))
        return float(rec["recommended_price_usd"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def price_candidate(slug: str, listing: dict, royalties: float, kdp_dir: Path = KDP_DIR) -> dict | None:
    """KU readers engage with the book but nobody buys at the current price →
    test the lowest 70%-band price. Evidence is deterministic (KENP recorded by
    kdp_sales_sync in feedback-history.json); missing/unreadable evidence = skip,
    never guess. LIVE books only — the executor gate re-checks this too."""
    if str(listing.get("live_status") or "").upper() != "LIVE":
        return None
    price = _current_price(slug, listing, kdp_dir)
    if price is None or price <= float(PRICE_TEST_VALUE):
        return None
    try:
        history = json.loads((kdp_dir / slug / "feedback-history.json").read_text(encoding="utf-8"))
        kenp = max(int(entry.get("mtd_kenp") or 0) for entry in history)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None  # ไม่มั่นใจ = ข้าม
    if kenp < MIN_KENP_FOR_PRICE_TEST or royalties > MAX_ROYALTIES_FOR_PRICE_TEST:
        return None
    # Signal-sufficiency: a distribution-starved title (too little traffic for a
    # 14-day conversion change to clear noise) is a visibility problem, not a
    # price problem — don't spend a scarce experiment slot tuning luck.
    if _title_traffic(history) < MIN_TRAFFIC_FOR_PRICE_TEST:
        return None
    # One variable per window: a free promo inside the 14-day evaluation window
    # would pollute the price signal (and KDP locks royalty binding during the
    # promo). Re-proposed automatically once the promo has passed.
    promo = listing.get("free_promo") or {}
    start, end = promo.get("start"), promo.get("end")
    if start and end:
        today = datetime.now(timezone.utc).date()
        window_end = (today + timedelta(days=14)).isoformat()
        if start <= window_end and end >= today.isoformat():
            return None
    return {
        "kind": "price_update", "field": "list_price",
        "proposed_value": PRICE_TEST_VALUE, "cost_usd": 0,
        "hypothesis": "KU readers engage with this title but nobody buys at the current price; the lowest 70%-band price can convert that interest into orders.",
        "variable": "price",
    }


def attributed_royalties(db_path: Path) -> dict:
    """asin -> lifetime-attributed royalties from the latest snapshot."""
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT a.asin, a.royalties_usd FROM kdp_title_attribution a "
            "WHERE a.snapshot_id = (SELECT id FROM kdp_snapshots ORDER BY observed_at DESC, id DESC LIMIT 1)"
        ).fetchall()
    return {asin: float(value) for asin, value in rows}


def already_tried(db_path: Path, slug: str, kind: str, proposed_value: str) -> bool:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT action_json FROM experiments WHERE slug = ?", (slug,)
        ).fetchall()
    for (raw,) in rows:
        try:
            action = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if action.get("kind") == kind and action.get("proposed_value") == proposed_value:
            return True
    return False


def active_state(db_path: Path) -> tuple[set, int]:
    placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            f"SELECT slug FROM experiments WHERE status IN ({placeholders})",
            sorted(ACTIVE_STATUSES),
        ).fetchall()
    slugs = {slug for (slug,) in rows}
    return slugs, len(rows)


def close_exhausted_failures(db_path: Path, now: datetime, notify=None) -> list:
    """A failed experiment whose action burned all 3 attempts can never advance
    — close it as inconclusive (audited) so the registry doesn't jam."""
    closed = []
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT e.id, e.slug FROM experiments e WHERE e.status='failed' AND ("
            "SELECT MAX(attempt) FROM agent_actions a WHERE a.experiment_id=e.id "
            "AND a.kind != 'internal_transition') >= 3"
        ).fetchall()
        for exp_id, slug in rows:
            reason = "Closed automatically: action failed 3/3 attempts; a redesigned cycle may be proposed from current state."
            connection.execute(
                "UPDATE experiments SET status='inconclusive', result_json=? WHERE id=?",
                (json.dumps({"outcome": "inconclusive", "reason": reason, "executed": False,
                             "closed_at": now.isoformat(), "closed_by": "experiment_proposer"}, sort_keys=True), exp_id),
            )
            closed.append((exp_id, slug, reason))
    for exp_id, slug, reason in closed:
        record_action_result(
            db_path,
            {"kind": "internal_transition", "slug": slug, "experiment_id": exp_id,
             "action_key": f"experiment:{exp_id}:failed:inconclusive"},
            {"observed_at": now.isoformat(),
             "internal_transition": {"before": "failed", "after": "inconclusive"}, "reason": reason},
        )
        if notify:
            notify(f"Libra proposer 🔚 ปิด experiment {exp_id} ({slug}) — 3/3 attempts ล้มเหลว")
    return closed


def analyze_proposals(kdp_dir: Path, db_path: Path, leaves: set) -> dict:
    """Expose why observable candidates cannot become safe experiments."""
    active_slugs, _ = active_state(db_path)
    royalties = attributed_royalties(db_path)
    proposals = []
    eligible_raw = 0
    blocked_missing_distribution = 0
    distribution_required = []
    for listing_path in sorted(kdp_dir.glob("*/listing.json")):
        slug = listing_path.parent.name
        is_active = slug in active_slugs
        try:
            listing = json.loads(listing_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if listing.get("status") != "uploaded":
            continue
        if str(listing.get("live_status") or "").upper() == "BLOCKED":
            continue  # dead listing (content-review block) — never spend a slot on it
        royalty = royalties.get(listing.get("asin"), 0.0)
        promo = listing.get("free_promo") or {}
        promo_status = promo.get("status")
        if promo_status in {"Running", "Scheduled"}:
            evidence = _distribution_evidence_for(slug)
            if not evidence["usable_for_promo"]:
                active = promo_status == "Running"
                distribution_required.append({
                    "kind": "distribution_required",
                    "slug": slug,
                    "asin": listing.get("asin"),
                    "channel": evidence.get("channel") or "reddit",
                    "evidence_status": evidence["status"],
                    "promo_status": promo_status,
                    "promo_start": promo.get("start"),
                    "promo_end": promo.get("end"),
                    "next_action": (
                        "publish during active promo and capture post_url or post_id"
                        if active else
                        "publish when promo becomes active and capture post_url or post_id"
                    ),
                    "kdp_mutation": False,
                })
        if is_active:
            continue
        for candidate in (category_candidate(slug, listing, leaves),
                          free_promo_candidate(slug, listing),
                          price_candidate(slug, listing, royalty, kdp_dir)):
            if candidate is None:
                continue
            if already_tried(db_path, slug, candidate["kind"], candidate["proposed_value"]):
                continue
            eligible_raw += 1
            if candidate["kind"] == "free_promo":
                evidence = _distribution_evidence_for(slug)
                if not evidence["usable_for_promo"]:
                    blocked_missing_distribution += 1
                    distribution_required.append({
                        "kind": "distribution_required",
                        "slug": slug,
                        "asin": listing.get("asin"),
                        "channel": evidence.get("channel") or "reddit",
                        "evidence_status": evidence["status"],
                        "next_action": "publish externally and capture post_url or post_id",
                        "kdp_mutation": False,
                    })
                    continue
            action = {k: candidate[k] for k in ("kind", "field", "proposed_value", "cost_usd")}
            if "replaces" in candidate:
                action["replaces"] = candidate["replaces"]
            ok, reason, _ = validate_action({**action, "slug": slug}, listing, leaves)
            if not ok:
                continue
            proposals.append({
                "slug": slug, "asin": listing.get("asin"),
                "royalties": royalty,
                "action": action, "hypothesis": candidate["hypothesis"],
                "variable": candidate["variable"],
            })
    # Highest-earning titles first (fix where the money is); at equal royalties
    # category fixes, then first promos (review engine), then price tests; slug
    # for determinism.
    kind_rank = {"category_update": 0, "free_promo": 1, "price_update": 2}
    proposals.sort(key=lambda p: (-p["royalties"],
                                  kind_rank.get(p["action"]["kind"], 3),
                                  p["slug"]))
    promo_rank = {"Running": 0, "Scheduled": 1}
    distribution_required.sort(key=lambda item: (
        promo_rank.get(item.get("promo_status"), 2),
        item.get("promo_start") or "9999-12-31",
        item["slug"],
    ))
    return {
        "proposals": proposals,
        "blocker_funnel": {
            "eligible_raw": eligible_raw,
            "blocked_missing_distribution": blocked_missing_distribution,
            "safe_executable": len(proposals),
        },
        "distribution_required": distribution_required,
    }


def gather_proposals(kdp_dir: Path, db_path: Path, leaves: set) -> list:
    return analyze_proposals(kdp_dir, db_path, leaves)["proposals"]


def run_proposer(*, kdp_dir: Path = KDP_DIR, db_path: Path = LEDGER_FILE,
                 leaves: set | None = None, now: datetime | None = None,
                 dry_run: bool = False, notify: bool = True) -> dict:
    now = now or datetime.now(timezone.utc)
    leaves = leaves if leaves is not None else load_tree_leaves()

    def _notify(message: str) -> None:
        if notify and not dry_run:
            try:
                send_telegram(message)
            except Exception:
                pass

    closed = [] if dry_run else close_exhausted_failures(db_path, now, _notify)
    _, active_count = active_state(db_path)
    created = []
    if not leaves:
        return {"error": "no category tree loaded — refusing to propose", "created": []}
    analysis = analyze_proposals(kdp_dir, db_path, leaves)
    proposals = analysis["proposals"]
    for proposal in proposals:
        if len(created) >= MAX_NEW_PER_RUN or active_count + len(created) >= MAX_ACTIVE:
            break
        if dry_run:
            created.append(proposal)
            continue
        experiment = create_experiment(
            db_path, slug=proposal["slug"], asin=proposal["asin"] or "",
            variable=proposal["variable"], action=proposal["action"],
            hypothesis=proposal["hypothesis"], now=now,
        )
        created.append({**proposal, "experiment_id": experiment["id"], "cycle_key": experiment["cycle_key"]})
        _notify(
            f"Libra proposer 🧪 experiment ใหม่ #{experiment['id']} {proposal['slug']}: "
            f"{proposal['action']['kind']} → {proposal['action']['proposed_value']}"
        )
    starved = distribution_starved_titles(kdp_dir)
    if starved:
        _notify(
            f"Libra proposer 📉 {len(starved)} เล่ม distribution-starved "
            f"(traffic ต่ำเกินวัดผล price/metadata ได้) — คอขวด = คนไม่เห็น ไม่ใช่ราคา"
        )
    return {"closed_exhausted": len(closed), "candidates": len(proposals),
            "created": created, "active_before": active_count,
            "blocker_funnel": analysis["blocker_funnel"],
            "distribution_required": analysis["distribution_required"],
            "distribution_starved": len(starved),
            "distribution_starved_slugs": starved}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="list proposals without creating experiments")
    args = parser.parse_args()
    result = run_proposer(dry_run=args.dry_run, notify=not args.dry_run)
    print(json.dumps(result, sort_keys=True, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
