#!/usr/bin/env python3
"""Everything a human needs to list one product on Payhip — in one folder.

Payhip has no product API and its login sits behind reCAPTCHA, which we do not
work around. So the machine prepares everything and the human does the one
five-minute upload:

    python3 scripts/payhip_upload_pack.py --slug SLUG --price-minor 1290 --currency EUR

Output: /root/downloads/payhip-<slug>/ with the buyer bundle, the cover, a
copy-paste text file, and a Thai checklist. After the upload, record the
product with scripts/payhip_record_product.py — it verifies the public page.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

LIBRA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIBRA_DIR))

from payhip_catalog import CatalogError, build_bundle, build_product_spec  # noqa: E402
from settings import load_env_file  # noqa: E402

KDP_DIR = Path(os.getenv("KDP_DIR", "/root/kdp"))
STAGING_DIR = Path(os.getenv("KDP_STAGING_ROOT", "/root/kdp-staging"))
DOWNLOADS = Path(os.getenv("LIBRA_DOWNLOADS", "/root/downloads"))
PUBLIC_BASE = "https://newton-winai-klinprasom.incomeinclick.in.th/libra"


def find_book(slug: str) -> Path:
    for root in (KDP_DIR, STAGING_DIR):
        if (root / slug / "listing.json").exists():
            return root / slug
    raise SystemExit(f"no book named {slug}")


def checklist_th(spec: dict, webhook_url: str | None) -> str:
    webhook_line = (
        f"   URL: {webhook_url}" if webhook_url
        else "   (ยังไม่มี token — รัน scripts/commerce_setup_check.py ก่อน)"
    )
    return f"""เช็คลิสต์ลงสินค้าใน Payhip — {spec['title']}
==========================================================

ใช้เวลา ~5 นาที ทำครั้งเดียวต่อเล่ม ทุกอย่างหลังจากนี้ระบบทำเองหมด

1) เข้า https://payhip.com/auth/login → ติ๊ก "I'm not a robot" → Log in

2) กด Add new product → Digital product
   - Product name : ดูใน product-text.txt (บรรทัด TITLE)
   - Price        : {spec['price_display']} {spec['currency']}
   - Description  : copy ทั้งก้อนจาก product-text.txt (ส่วน DESCRIPTION)
   - Product file : อัปโหลด  {Path(spec['bundle']).name}   (ไฟล์ที่ลูกค้าจะได้)
   - Cover image  : อัปโหลด  cover.jpg
   - กด Save / Publish

3) เปิดหน้าสินค้าที่ได้ (URL จะเป็น https://payhip.com/b/xxxxx) แล้ว copy URL นั้นมาบอกผม
   ผมจะรัน:  python3 scripts/payhip_record_product.py --slug {spec['slug']} --url <URL>
   (ระบบจะเปิดหน้าสาธารณะตรวจว่ามีชื่อเล่มจริงก่อนบันทึก — มีหลักฐาน)

4) ทำครั้งเดียวตลอดไป (ไม่ต้องทำซ้ำต่อเล่ม):
   Account → Settings → Payment Details → Connect Stripe
   Account → Settings → (ช่อง Webhook / Notifications URL) ใส่:
{webhook_line}

ห้ามทำ: เปลี่ยนราคาใน Amazon / แตะเล่มเก่า — เล่มนี้ไม่ได้อยู่ใน KDP Select จึงขายที่นี่ได้
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--price-minor", type=int, required=True)
    parser.add_argument("--currency", default="EUR")
    args = parser.parse_args(argv)

    book = find_book(args.slug)
    try:
        spec = build_product_spec(book, price_minor=args.price_minor, currency=args.currency)
        bundle = build_bundle(book, LIBRA_DIR / "data" / "payhip-bundles")
    except CatalogError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    spec["bundle"] = str(bundle)

    out = DOWNLOADS / f"payhip-{args.slug}"
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bundle, out / bundle.name)
    shutil.copy2(spec["cover"], out / "cover.jpg")
    (out / "product-text.txt").write_text(
        f"TITLE\n{spec['title']}\n\nSUBTITLE\n{spec['subtitle']}\n\n"
        f"PRICE\n{spec['price_display']} {spec['currency']}\n\n"
        f"DESCRIPTION\n{spec['description']}\n\nKEYWORDS\n{', '.join(spec['keywords'])}\n",
        encoding="utf-8",
    )
    env = load_env_file(LIBRA_DIR / ".env")
    token = env.get("PAYHIP_WEBHOOK_TOKEN_TEST")
    webhook_url = f"{PUBLIC_BASE}/api/webhooks/payhip/{token}" if token else None
    (out / "CHECKLIST-TH.txt").write_text(checklist_th(spec, webhook_url), encoding="utf-8")
    (out / "spec.json").write_text(json.dumps({k: v for k, v in spec.items() if k != "description"},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"pack": str(out), "files": sorted(p.name for p in out.iterdir()),
                      "bundle_bytes": bundle.stat().st_size}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
