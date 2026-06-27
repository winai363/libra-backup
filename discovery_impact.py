#!/usr/bin/env python3
"""
discovery_impact.py — Measure whether the 2026-06 discovery overhaul (new
bestseller-style covers + corrected categories + sanitized keywords + HTML
descriptions) actually moves sales, per book and in aggregate.

It reads the data the system already collects:
  • each book's kdp/<slug>/feedback-history.json  (daily sales snapshots from
    kdp_sales_sync: delta_units, delta_kenp, revenue_usd, dated)
  • the intervention date per book (cover_updated_at / metadata_updated_at in
    listing.json) — the day its discovery metadata changed

Two modes:
  --baseline   freeze a "before" reference now (intervention date + cumulative
               sales to date) → kdp/discovery-baseline.json. Run once, today.
  --report     compare sales velocity BEFORE vs AFTER each book's intervention
               date (units/day, KENP/day, royalty/day) → table + aggregate lift.
               Run anytime; meaningful after ~1-2 weeks of post-change data.

Honest limitation: KDP's API exposes orders/KENP/royalty, NOT impressions, so we
measure the OUTCOME (sales) — we can't isolate "more impressions" from "better
conversion". That's fine: sales are what matter. With a near-zero pre-change
baseline, "after" sales starting at all is the signal to watch.
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

KDP = Path("/root/kdp")
BASELINE_FILE = KDP / "discovery-baseline.json"


def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _intervention_date(listing: dict) -> str | None:
    """Earliest discovery-change date for the book (cover or metadata)."""
    cands = []
    for k in ("cover_updated_at", "metadata_updated_at"):
        v = listing.get(k)
        if v:
            cands.append(str(v)[:10])
    return min(cands) if cands else None


def _live_books():
    out = []
    for lf in sorted(KDP.glob("*/listing.json")):
        d = _load(lf, {})
        if d.get("live_status") == "LIVE":
            out.append((lf.parent.name, d))
    return out


def _history(slug):
    return _load(KDP / slug / "feedback-history.json", [])


def _cumulative(history):
    """Total observed units / KENP / royalty across the whole history."""
    u = sum(s.get("delta_units", 0) for s in history)
    k = sum(s.get("delta_kenp", 0) for s in history)
    r = sum(s.get("revenue_usd", 0) or 0 for s in history)
    return u, k, r


def cmd_baseline():
    books = _live_books()
    today = date.today().isoformat()
    snap = {"captured_at": today, "books": {}}
    for slug, d in books:
        iv = _intervention_date(d) or today
        u, k, r = _cumulative(_history(slug))
        snap["books"][slug] = {
            "asin": d.get("asin"),
            "intervention_date": iv,
            "cumulative_units_before": u,
            "cumulative_kenp_before": k,
            "cumulative_royalty_before": round(r, 2),
        }
    BASELINE_FILE.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✓ Baseline frozen for {len(snap['books'])} LIVE books → {BASELINE_FILE}")
    print(f"  intervention dates: "
          + ", ".join(sorted({b['intervention_date'] for b in snap['books'].values()})))
    tot = sum(b["cumulative_royalty_before"] for b in snap["books"].values())
    print(f"  total royalty observed before change: ${tot:.2f}")


def _window_sums(history, start, end):
    """Sum sales for snapshots with start <= date < end (ISO strings)."""
    u = k = 0
    r = 0.0
    for s in history:
        dt = s.get("date", "")
        if start <= dt < end:
            u += s.get("delta_units", 0)
            k += s.get("delta_kenp", 0)
            r += s.get("revenue_usd", 0) or 0
    return u, k, r


def cmd_report(window_days=14):
    books = _live_books()
    today = date.today()
    rows = []
    agg = {"bu": 0, "bk": 0, "br": 0.0, "au": 0, "ak": 0, "ar": 0.0,
           "bd": 0, "ad": 0}
    for slug, d in books:
        iv = _intervention_date(d)
        if not iv:
            continue
        iv_d = datetime.strptime(iv, "%Y-%m-%d").date()
        before_start = (iv_d - timedelta(days=window_days)).isoformat()
        hist = _history(slug)
        bu, bk, br = _window_sums(hist, before_start, iv)
        au, ak, ar = _window_sums(hist, iv, (today + timedelta(days=1)).isoformat())
        before_days = window_days
        after_days = max(1, (today - iv_d).days)
        rows.append({
            "slug": slug, "iv": iv, "after_days": after_days,
            "bu": bu, "br": br, "au": au, "ar": ar,
            "bvel": br / before_days, "avel": ar / after_days,
        })
        agg["bu"] += bu; agg["bk"] += bk; agg["br"] += br
        agg["au"] += au; agg["ak"] += ak; agg["ar"] += ar
        agg["ad"] = max(agg["ad"], after_days)

    print(f"\n=== Discovery Impact Report — {today.isoformat()} ===")
    print(f"(BEFORE = {window_days}d pre-change · AFTER = since each book's change date)\n")
    print(f"{'book':42} {'changed':10} {'aft_d':>5} {'before$':>8} {'after$':>8} {'units→':>7}")
    for r in sorted(rows, key=lambda x: -x["ar"]):
        print(f"{r['slug'][:42]:42} {r['iv']:10} {r['after_days']:>5} "
              f"{r['br']:>8.2f} {r['ar']:>8.2f} {r['bu']:>3}→{r['au']:<3}")
    days_after = max(1, agg["ad"])
    print("\n--- AGGREGATE ---")
    print(f"  BEFORE: {agg['bu']} units, {agg['bk']} KENP, ${agg['br']:.2f}  "
          f"(velocity ${agg['br']/window_days:.3f}/day)")
    print(f"  AFTER:  {agg['au']} units, {agg['ak']} KENP, ${agg['ar']:.2f}  "
          f"(velocity ${agg['ar']/days_after:.3f}/day over {days_after}d)")
    if agg["br"] > 0:
        lift = (agg["ar"] / days_after) / (agg["br"] / window_days)
        print(f"  → velocity lift: {lift:.2f}x")
    elif agg["ar"] > 0:
        print(f"  → sales STARTED after the change (before-baseline was $0)")
    else:
        print(f"  → no post-change sales yet — give it more days, then re-run")
    print()


if __name__ == "__main__":
    if "--baseline" in sys.argv:
        cmd_baseline()
    elif "--report" in sys.argv:
        wd = 14
        if "--days" in sys.argv:
            wd = int(sys.argv[sys.argv.index("--days") + 1])
        cmd_report(wd)
    else:
        print(__doc__)
        print("Usage: python3 discovery_impact.py [--baseline | --report [--days N]]")
