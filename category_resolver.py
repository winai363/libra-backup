#!/usr/bin/env python3
"""
category_resolver.py — Map the category paths GPT proposes onto KDP's REAL tree.

GPT reliably names the right top-level ("Arts & Photography", "Business & Money")
and the right concepts, but invents the middle nesting and leaf names — e.g.
"Arts & Photography > Painting > Watercolor" when KDP actually nests it as
"Arts & Photography > Art > Painting > General". set_categories then matches 0–2
of 3 and the book ships under-categorised.

resolve_paths() snaps each proposed path to the closest real leaf in the snapshot
produced by kdp_category_tree.py, keeping the result inside the proposed
top-level (audience band) and de-duplicating. It's deterministic — same role as
metadata_polish for keywords — and runs in seo_optimizer before listing.json is
written, so every book reaches upload with valid, tickable category paths.
"""
import json
import re
from pathlib import Path

TREE_FILE = Path(__file__).parent / "data" / "kdp_category_tree.json"

_STOP = {"and", "the", "of", "for", "a", "an", "to", "in", "with", "amp"}
# Tokens too generic to anchor a leaf choice — "Anxiety Disorders" vs "Eating
# Disorders" share only "disorders", which must NOT make them a match.
_GENERIC = {"disorders", "disorder", "general", "reference", "studies", "study",
            "topics", "issues", "other", "misc", "guides", "guide", "skills"}
_PHRASE_ALIASES = (
    ("stressmanagement", "stress management"),
    ("stress management", "health stress"),
    ("anxiety disorders", "anxieties phobias"),
    ("men's health", "men health general"),
    ("mens health", "men health general"),
    ("gezondheid, geest & lichaam", "health fitness dieting"),
    ("gezondheid geest lichaam", "health fitness dieting"),
    ("gezonde levensstijl", "healthy living"),
    ("zelfhulp", "self help"),
)


def _tok(s: str) -> set:
    s = s.lower().replace("&", " and ")
    for src, dst in _PHRASE_ALIASES:
        s = s.replace(src, dst)
    return {w for w in re.split(r"[^a-z0-9]+", s) if w and w not in _STOP}


def load_tree(path: Path = TREE_FILE) -> list:
    try:
        return json.loads(Path(path).read_text()).get("leaves", [])
    except Exception:
        return []


_STORE_ROOTS = {"kindle ebooks", "kindle store", "books", "ebooks", "kindle books", "kindle"}


def _segments(path: str) -> list:
    segs = [s.strip() for s in re.split(r"[>›/|]", path) if s.strip()]
    # Drop a leading store-root prefix ("Kindle eBooks > …") so the real top-level
    # is segment 0. Otherwise "Kindle eBooks > Education & Teaching" matches the
    # wrong top-level "Children's eBooks" via the shared word "eBooks".
    while segs and segs[0].strip().lower() in _STORE_ROOTS:
        segs = segs[1:]
    return segs


def _score(proposed: str, leaf: str, book_context: set, subject: set) -> float:
    """Higher = better real-leaf match for a proposed path.

    book_context = union of every proposed path's below-top tokens. It anchors the
    match to the book's overall subject so an exact leaf-word hit in an unrelated
    subtree (e.g. proposed "…Painting > Instruction" matching Music's
    "Instruction & Study") loses to the on-subject Art subtree."""
    p_segs, l_segs = _segments(proposed), _segments(leaf)
    if not p_segs or not l_segs:
        return -1.0
    p_top, l_top = _tok(p_segs[0]), _tok(l_segs[0])
    top = len(p_top & l_top) / len(p_top | l_top) if (p_top | l_top) else 0.0
    l_rest = _tok(" ".join(l_segs[1:]))
    l_rest_specific = l_rest - _GENERIC
    p_leaf_specific = _tok(p_segs[-1]) - _GENERIC
    proposed_all = _tok(" ".join(p_segs))
    leaf_all = _tok(" ".join(l_segs))
    # Do not let broad wellness/stress/men's-health categories drift into highly
    # specific medical leaves unless the source category actually names them.
    if "ptsd" in leaf_all and not ({"ptsd", "trauma", "traumatic"} & proposed_all):
        return -1.0
    if "prostate" in leaf_all and "prostate" not in proposed_all:
        return -1.0
    _JUVENILE = {"children", "teen"}
    if top == 0.0:
        # Allow a CROSS-top-level match only when the proposed leaf concept is
        # fully present in the real path. GPT uses Amazon browse names whose
        # top-level differs from KDP's picker (e.g. browse "Self-Help > Time
        # Management" lives at picker "Business & Money > Skills > Time Management").
        # Containment keeps this safe — a vague single-word overlap won't cross.
        l_all_specific = l_rest_specific | (_tok(l_segs[-1]) - _GENERIC)
        if not p_leaf_specific or not (p_leaf_specific <= l_all_specific):
            return -1.0
        # NEVER cross an adult book into a juvenile top-level (an adult stoicism
        # book must not land in "Children's eBooks > … > Philosophy").
        if (l_top & _JUVENILE) and not (p_top & _JUVENILE):
            return -1.0
    # The proposed LEAF names what the book is actually about — the single most
    # reliable signal. GPT's MID segments ("Mental Health") are its guessed nesting
    # and are often wrong, so weight them far less than the leaf concept (otherwise
    # an ADHD book lands in "Mental Health > General" instead of the real
    # "Pathologies > Attention-Deficit Disorder" leaf).
    p_leaf, l_leaf = _tok(p_segs[-1]), _tok(l_segs[-1])
    leaf_exact = len((p_leaf & l_leaf) - _GENERIC)
    leaf_anywhere = len((p_leaf & l_rest) - _GENERIC)
    p_mid = _tok(" ".join(p_segs[1:-1]))
    mid_overlap = len((p_mid & l_rest) - _GENERIC)
    # Subtree relevance: fraction of the REAL path's (specific) tokens the book is
    # actually about — penalises foreign subtrees (a Music leaf for a painting book).
    relevance = len(l_rest_specific & book_context) / len(l_rest_specific) if l_rest_specific else 0.0
    # Gate the leaf match by how on-subject its subtree is: an exact leaf word that
    # lands in a FOREIGN subtree (e.g. "Instruction" matching Music for a painting
    # book) is mostly coincidence and must be damped, while the same leaf in an
    # on-subject subtree (ADHD under Pathologies) counts fully.
    leaf_gate = 0.3 + min(relevance, 1.0)
    # The subject is the theme recurring across the book's proposed paths (e.g.
    # "painting" in all of acuarela's). A real path carrying it is almost certainly
    # the right subtree, so reward it strongly — this is what keeps a watercolour
    # book's "…> Instruction" out of the Music subtree.
    subject_match = min(len(l_rest & subject), 1)   # tie-breaker, not a dominator
    general_bonus = 0.2 if l_leaf == {"general"} else 0.0
    return (3.0 * top + 1.2 * subject_match
            + (2.5 * leaf_exact + 1.0 * leaf_anywhere) * leaf_gate
            + 1.0 * mid_overlap + 1.5 * relevance + general_bonus)


def resolve_paths(proposed_paths, leaves=None, max_n: int = 3, min_score: float = 3.0):
    """Return up to max_n real KDP leaf paths matching the proposed paths.

    Falls back to returning the original path when no real leaf clears min_score
    (so a missing/partial snapshot never drops a book's categories to nothing —
    set_categories will still fuzzy-match it live)."""
    leaves = leaves if leaves is not None else load_tree()
    if not leaves:
        return list(dict.fromkeys(proposed_paths))[:max_n]
    # The book's overall subject = every proposed path's below-top tokens.
    book_context, _counts = set(), {}
    for p in proposed_paths:
        segs = _segments(p)
        toks = _tok(" ".join(segs[1:]))
        book_context |= toks
        for t in toks:
            _counts[t] = _counts.get(t, 0) + 1
    book_context -= _GENERIC          # generic words must not anchor relevance
    # Subject = the theme recurring across ≥2 of the book's proposed paths.
    subject = {t for t, n in _counts.items() if n >= 2} - _GENERIC
    out, used = [], set()
    for path in proposed_paths:
        best, best_s = None, min_score
        for leaf in leaves:
            if leaf in used:
                continue
            s = _score(path, leaf, book_context, subject)
            if s > best_s:
                best, best_s = leaf, s
        if best:
            out.append(best); used.add(best)
        else:
            # No confident real match — keep the original (deduped later).
            if path not in out:
                out.append(path)
    # De-dupe preserving order, cap at max_n.
    seen, final = set(), []
    for p in out:
        if p not in seen:
            seen.add(p); final.append(p)
    return final[:max_n]


if __name__ == "__main__":
    import sys
    leaves = load_tree()
    print(f"tree: {len(leaves)} leaves\n")
    samples = {
        "acuarela (GPT)": [
            "Arts & Photography > Painting > Watercolor",
            "Arts & Photography > Painting > Instruction",
            "Arts & Photography > Painting > Techniques",
        ],
        "guia (GPT)": [
            "Business & Money > Accounting > Financial",
            "Business & Money > Small Business & Entrepreneurship > Home-Based",
            "Business & Money > Taxation > Small Business",
        ],
        "teen-anxiety (GPT-ish)": [
            "Health, Fitness & Dieting > Mental Health > Anxiety Disorders",
            "Self-Help > Emotions",
            "Teen & Young Adult > Personal Health > Depression & Mental Health",
        ],
    }
    if len(sys.argv) > 1:
        samples = {"cli": sys.argv[1:]}
    for name, paths in samples.items():
        print(f"## {name}")
        for r in resolve_paths(paths, leaves):
            print(f"   {r}")
        print()
