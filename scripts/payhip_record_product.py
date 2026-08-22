#!/usr/bin/env python3
"""Record a Payhip product that a human just created — after proving it exists.

    python3 scripts/payhip_record_product.py --slug SLUG --url https://payhip.com/b/xxxxx

Opens the PUBLIC product page (no login, no CAPTCHA), checks that the book's
title is on it, then records the product as live so /growth/products/<slug>
goes up. A URL whose page does not show the title is refused — a pasted link is
not evidence, a page that says the right thing is.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

LIBRA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIBRA_DIR))

from payhip_catalog import CatalogError, build_product_spec, record_product  # noqa: E402

KDP_DIR = Path(os.getenv("KDP_DIR", "/root/kdp"))
STAGING_DIR = Path(os.getenv("KDP_STAGING_ROOT", "/root/kdp-staging"))
LEDGER = LIBRA_DIR / "data" / "libra-business.db"
ALLOWED_HOSTS = {"payhip.com", "www.payhip.com"}


def find_book(slug: str) -> Path:
    for root in (KDP_DIR, STAGING_DIR):
        if (root / slug / "listing.json").exists():
            return root / slug
    raise SystemExit(f"no book named {slug}")


def verify_public_page(url: str, title: str, *, fetch=None) -> dict:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS or not parts.path.startswith("/b/"):
        raise SystemExit(f"not a Payhip product URL: {url}")
    fetch = fetch or (lambda u: httpx.get(u, follow_redirects=True, timeout=30).text)
    html = fetch(url)
    needle = title[:30].casefold()
    present = needle in html.casefold()
    return {"url": url, "title_found": present, "title_probe": title[:30], "html_bytes": len(html)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--price-minor", type=int, default=1290)
    parser.add_argument("--currency", default="EUR")
    args = parser.parse_args(argv)

    book = find_book(args.slug)
    try:
        spec = build_product_spec(book, price_minor=args.price_minor, currency=args.currency)
    except CatalogError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    evidence = verify_public_page(args.url, spec["title"])
    if not evidence["title_found"]:
        print(json.dumps({"recorded": False, "reason": "title_not_on_public_page", **evidence},
                         ensure_ascii=False))
        return 3
    record_product(LEDGER, spec, provider_product_id=args.url, status="live")
    print(json.dumps({"recorded": True, "product_page": f"/libra/growth/products/{spec['slug']}",
                      **evidence}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
