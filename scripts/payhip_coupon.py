#!/usr/bin/env python3
"""Create or list Payhip discount codes through the API.

    python3 scripts/payhip_coupon.py --list
    python3 scripts/payhip_coupon.py --create TESTRUN95 --percent-off 95 \
        --product-key GDRi5 --usage-limit 1

Used to make a controlled test purchase cheap: Payhip has no sandbox, so the
only honest way to prove the money path is a real transaction — a 95% code
turns a €12.90 rehearsal into roughly €0.65.

Payhip sits behind Cloudflare and returns 403 (error 1010) to a bare Python
user agent, so a browser UA is required. That is a compatibility header, not an
attempt to look like a person: the request is authenticated with our own key.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

LIBRA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIBRA_DIR))

from settings import load_env_file  # noqa: E402

API = "https://payhip.com/api/v2/coupons"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _headers(api_key: str) -> dict:
    return {"payhip-api-key": api_key, "User-Agent": UA, "Accept": "application/json"}


def _call(method: str, api_key: str, payload: dict | None = None) -> dict:
    data = None
    headers = _headers(api_key)
    if payload is not None:
        # Payhip's API takes form-encoded fields, not JSON — a JSON body comes
        # back as "required_parameters" with every field apparently missing.
        data = urllib.parse.urlencode(payload).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(API, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="ignore")[:300]
        raise SystemExit(f"payhip_api_error: HTTP {exc.code} {body}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Payhip discount codes")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true")
    mode.add_argument("--create", metavar="CODE")
    parser.add_argument("--percent-off", type=float)
    parser.add_argument("--product-key", help="the product id, e.g. GDRi5")
    parser.add_argument("--usage-limit", type=int, default=1)
    parser.add_argument("--notes", default="")
    args = parser.parse_args(argv)

    api_key = load_env_file(LIBRA_DIR / ".env").get("PAYHIP_API_KEY", "")
    if not api_key:
        raise SystemExit("PAYHIP_API_KEY missing from .env")

    if args.list:
        print(json.dumps(_call("GET", api_key), ensure_ascii=False, indent=2))
        return 0

    if not args.percent_off or not args.product_key:
        raise SystemExit("--percent-off and --product-key are required with --create")
    if not 0 < args.percent_off <= 100:
        raise SystemExit("--percent-off must be between 0 and 100")

    payload = {
        "code": args.create,
        "coupon_type": "single",
        "product_key": args.product_key,
        "percent_off": args.percent_off,
        "usage_limit": args.usage_limit,
        "notes": args.notes or "controlled test purchase",
    }
    result = _call("POST", api_key, payload)
    print(json.dumps({"created": args.create, "percent_off": args.percent_off,
                      "usage_limit": args.usage_limit, "response": result},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
