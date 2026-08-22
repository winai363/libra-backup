#!/usr/bin/env python3
"""Publish a finished book as a Payhip product — end to end, with proof.

    python3 scripts/payhip_publish.py --slug SLUG --price-minor 1290 --currency EUR --dry-run
    python3 scripts/payhip_publish.py --slug SLUG --price-minor 1290 --currency EUR --execute
    python3 scripts/payhip_publish.py --inspect          # dump the real product form once

Steps, each of which can refuse:
  1. guard   — not KDP Select, quality passed, files present, AI disclosure present
  2. bundle  — PDF + EPUB + README zip (never working files)
  3. payhip  — create product in the browser; before/after evidence required
  4. record  — commerce_products row, only when the product is visibly listed
  5. hub     — the product page at /growth/products/<slug> goes live automatically

Dry run performs 1-2 and prints the plan for 3. It never opens a browser.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

LIBRA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIBRA_DIR))

import payhip_admin  # noqa: E402
from payhip_catalog import (  # noqa: E402
    CatalogError,
    build_bundle,
    build_product_spec,
    record_product,
)
from settings import load_env_file  # noqa: E402

KDP_DIR = Path(os.getenv("KDP_DIR", "/root/kdp"))
STAGING_DIR = Path(os.getenv("KDP_STAGING_ROOT", "/root/kdp-staging"))
LEDGER = LIBRA_DIR / "data" / "libra-business.db"
BUNDLES_DIR = LIBRA_DIR / "data" / "payhip-bundles"


def find_book(slug: str) -> Path:
    for root in (KDP_DIR, STAGING_DIR):
        candidate = root / slug
        if (candidate / "listing.json").exists():
            return candidate
    raise SystemExit(f"no book named {slug} under {KDP_DIR} or {STAGING_DIR}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Publish a book to Payhip with evidence")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--inspect", action="store_true", help="dump the real product form fields")
    parser.add_argument("--slug")
    parser.add_argument("--price-minor", type=int)
    parser.add_argument("--currency", default="EUR")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    env = load_env_file(LIBRA_DIR / ".env")

    if args.inspect:
        credentials = payhip_admin.load_credentials(env)
        report = asyncio.run(payhip_admin.inspect(credentials))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if not args.slug or not args.price_minor:
        raise SystemExit("--slug and --price-minor are required")

    book = find_book(args.slug)
    try:
        spec = build_product_spec(book, price_minor=args.price_minor, currency=args.currency)
        bundle = build_bundle(book, BUNDLES_DIR)
    except CatalogError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    plan = payhip_admin.plan_product_upsert(spec, bundle)
    if args.dry_run:
        print(json.dumps({
            "mode": "dry_run",
            "slug": spec["slug"],
            "title": spec["title"],
            "price": f"{spec['price_display']} {spec['currency']}",
            "bundle": str(bundle),
            "bundle_bytes": bundle.stat().st_size,
            "browser_steps": [step["action"] for step in plan],
            "external_calls": 0,
        }, ensure_ascii=False, indent=2))
        return 0

    credentials = payhip_admin.load_credentials(env)
    evidence = asyncio.run(payhip_admin.upsert_product(spec, bundle, credentials))
    outcome = evidence.get("outcome")
    if outcome in ("executed", "already_listed"):
        record_product(LEDGER, spec, provider_product_id=evidence["external_url"], status="live")
    print(json.dumps({"outcome": outcome, **{k: v for k, v in evidence.items() if k != "screenshots"},
                      "screenshots": evidence.get("screenshots", [])}, ensure_ascii=False, indent=2))
    return 0 if outcome in ("executed", "already_listed") else 3


if __name__ == "__main__":
    raise SystemExit(main())
