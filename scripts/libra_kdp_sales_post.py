#!/usr/bin/env python3
"""Post organic Libra KDP sales/promotional updates to Facebook.

This intentionally does not create ads. It uses Loom's existing Facebook
credential store and caption guards, then records the post in Loom's
sales_posts table so comment-reply can route people to the Amazon link.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

LOOM_ROOT = Path("/root/loom")
LIBRA_ROOT = Path("/root/libra")
KDP_ROOT = Path("/root/kdp")
LOOM_DB = LOOM_ROOT / "data" / "loom.db"
STATE_FILE = KDP_ROOT / ".libra-kdp-sales-post-state.json"
GRAPH = "https://graph.facebook.com/v23.0"
TZ = ZoneInfo("Asia/Bangkok")

WORKFLOW_ID = "libra-kdp-sales-post"
PAGE_ID = "1167163833140098"  # AI ใช้จริง
CREDENTIAL_ID = "fb-aijaijing"

BOOKS = [
    {
        "slug": "adhd-self-help-adults-es",
        "label": "เล่มหลัก",
        "angle": "คู่มือจัดระบบชีวิตและเพิ่มสมาธิสำหรับผู้ใหญ่ที่มี TDAH",
    },
    {
        "slug": "adhd-adults-workbook-es",
        "label": "เวิร์กบุ๊ก",
        "angle": "แบบฝึกหัดและเทมเพลตช่วยจัดวัน ลดความวุ่นวาย และโฟกัสงาน",
    },
    {
        "slug": "adhd-adults-focus-work-relationships-es",
        "label": "เล่มต่อยอด",
        "angle": "กลยุทธ์เรื่องสมาธิ งาน และความสัมพันธ์สำหรับผู้ใหญ่ที่มี TDAH",
    },
]

PROMO_START_ICT = datetime(2026, 7, 2, 14, 0, tzinfo=TZ)
PROMO_END_ICT = datetime(2026, 7, 7, 13, 59, tzinfo=TZ)


def _load_loom_helpers():
    env_path = LOOM_ROOT / ".env"
    if env_path.exists() and not os.environ.get("FERNET_KEY"):
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("FERNET_KEY="):
                os.environ["FERNET_KEY"] = line.split("=", 1)[1].strip()
                break
    sys.path.insert(0, str(LOOM_ROOT))
    from backend.services.credential_service import credential_service
    from backend.services.step_executors.fb_post_executor import (
        _publish_limits_for_page,
        _publish_rate_violation,
        _record_sales_post,
        _sanitize_facebook_caption,
    )

    return (
        credential_service,
        _publish_limits_for_page,
        _publish_rate_violation,
        _record_sales_post,
        _sanitize_facebook_caption,
    )


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _credential_token(credential_id: str) -> str:
    credential_service, *_ = _load_loom_helpers()
    with sqlite3.connect(LOOM_DB) as conn:
        row = conn.execute(
            "SELECT encrypted_value FROM credentials WHERE id=?",
            (credential_id,),
        ).fetchone()
    if not row:
        raise RuntimeError(f"missing Facebook credential: {credential_id}")
    return credential_service.decrypt(row[0])


def _fb_error(response: httpx.Response) -> str:
    try:
        body = response.json()
        err = body.get("error") or {}
        message = err.get("message") or response.text
        code = err.get("code")
        subcode = err.get("error_subcode")
        return f"Facebook API error {code or ''}{('/' + str(subcode)) if subcode else ''}: {message}"
    except Exception:
        return f"Facebook HTTP {response.status_code}: {response.text[:300]}"


def _check_response(response: httpx.Response) -> dict:
    if response.is_success:
        return response.json()
    raise RuntimeError(_fb_error(response))


def _recent_post_times(client: httpx.Client, token: str, page_id: str) -> list[str]:
    since = int((datetime.now(timezone.utc).timestamp()) - 24 * 3600)
    page = client.get(
        f"{GRAPH}/{page_id}",
        params={"fields": "is_published,can_post", "access_token": token},
    )
    status = _check_response(page)
    if status.get("is_published") is False:
        raise RuntimeError("Facebook page is not published")
    if status.get("can_post") is False:
        raise RuntimeError("Facebook has disabled publishing for this page")

    posts = client.get(
        f"{GRAPH}/{page_id}/published_posts",
        params={
            "fields": "created_time",
            "since": since,
            "limit": 10,
            "access_token": token,
        },
    )
    data = _check_response(posts)
    return [str(item.get("created_time") or "") for item in data.get("data", [])]


def _live_books() -> list[dict]:
    books = []
    for spec in BOOKS:
        listing_path = KDP_ROOT / spec["slug"] / "listing.json"
        listing = _load_json(listing_path, {})
        asin = str(listing.get("asin") or "").strip()
        if not asin or str(listing.get("live_status") or "").upper() != "LIVE":
            continue
        books.append({**spec, "listing": listing})
    return books


def _select_book(books: list[dict], state: dict) -> dict:
    used = state.get("posted_slugs", [])
    for book in books:
        if book["slug"] not in used:
            return book
    state["posted_slugs"] = []
    return books[0]


def _amazon_url(asin: str) -> str:
    return f"https://www.amazon.com/dp/{asin}"


def _caption(book: dict, now: datetime) -> str:
    listing = book["listing"]
    title = listing.get("title", "")
    subtitle = listing.get("subtitle", "")
    asin = listing.get("asin", "")
    url = _amazon_url(asin)
    price = listing.get("price")
    promo = listing.get("free_promo") or {}
    in_promo = PROMO_START_ICT <= now <= PROMO_END_ICT

    if in_promo and promo:
        offer = "ตอนนี้เล่มหลักเปิดให้โหลดฟรีช่วง Free Promo บน Kindle ครับ"
    elif now < PROMO_START_ICT and promo:
        offer = "Free Promo ของเล่มหลักจะเริ่ม 2 ก.ค. 2026 เวลา 14:00 น. ตามเวลาไทยครับ"
    else:
        offer = f"ราคา Kindle ปัจจุบันประมาณ ${price} ครับ" if price else "เปิดขายบน Kindle แล้วครับ"

    return f"""ทดลองพอร์ต KDP ของ Libra รอบนี้เป็นซีรีส์ภาษาสเปนเรื่อง TDAH en Adultos ครับ

{book['label']}: {title}
{subtitle}

เหมาะกับคนที่ต้องการ {book['angle']} เป็นภาษาสเปน

{offer}

ลิงก์ Amazon:
{url}

หมายเหตุ: ยังไม่ยิงแอด ใช้โพสต์ organic และ Free Promo เพื่อดูสัญญาณดาวน์โหลด/อ่านจริงก่อนครับ

#AIใช้จริง #KDP #Kindle #TDAH #หนังสือดิจิทัล"""


def _post_photo(client: httpx.Client, token: str, page_id: str, cover: Path, message: str) -> str:
    with cover.open("rb") as fh:
        response = client.post(
            f"{GRAPH}/{page_id}/photos",
            data={"access_token": token, "message": message},
            files={"source": (cover.name, fh, "image/jpeg")},
            timeout=120,
        )
    data = _check_response(response)
    post_id = str(data.get("post_id") or data.get("id") or "").strip()
    if not post_id:
        raise RuntimeError(f"Facebook photo post returned no post id: {data}")
    return post_id


def run(*, dry_run: bool = False, force: bool = False, promo_window_only: bool = False) -> dict:
    (
        _credential_service,
        publish_limits_for_page,
        publish_rate_violation,
        record_sales_post,
        sanitize_caption,
    ) = _load_loom_helpers()

    now = datetime.now(TZ)
    if promo_window_only and not (PROMO_START_ICT <= now <= PROMO_END_ICT):
        return {"status": "skipped", "reason": "outside_promo_window"}

    state = _load_json(STATE_FILE, {})
    today_key = now.date().isoformat()
    if state.get("last_post_date") == today_key and not force:
        return {"status": "skipped", "reason": "already_posted_today"}

    books = _live_books()
    if not books:
        raise RuntimeError("no live Libra KDP promo books found")
    book = _select_book(books, state)
    listing = book["listing"]
    cover = KDP_ROOT / book["slug"] / "cover.jpg"
    if not cover.exists():
        raise RuntimeError(f"missing cover: {cover}")

    token = _credential_token(CREDENTIAL_ID)
    max_posts, min_gap = publish_limits_for_page(PAGE_ID)
    with httpx.Client(timeout=45) as client:
        recent = _recent_post_times(client, token, PAGE_ID)
        violation = publish_rate_violation(
            recent,
            max_posts_per_24h=max_posts,
            min_interval_hours=min_gap,
        )
        if violation and not force:
            return {"status": "skipped", "reason": f"publish_safety: {violation}"}

        message = sanitize_caption(_caption(book, now))
        if dry_run:
            return {
                "status": "dry_run",
                "page_id": PAGE_ID,
                "slug": book["slug"],
                "message": message,
                "cover": str(cover),
            }

        post_id = _post_photo(client, token, PAGE_ID, cover, message)

    asin = str(listing.get("asin") or "")
    record_sales_post(
        PAGE_ID,
        WORKFLOW_ID,
        None,
        post_id,
        _amazon_url(asin),
        None,
        ["ลิงก์", "โหลด", "ฟรี", "หนังสือ", "TDAH"],
    )

    state["last_post_date"] = today_key
    state.setdefault("posted_slugs", []).append(book["slug"])
    state["last_post"] = {
        "at": now.isoformat(),
        "page_id": PAGE_ID,
        "workflow_id": WORKFLOW_ID,
        "slug": book["slug"],
        "asin": asin,
        "post_id": post_id,
    }
    _save_json(STATE_FILE, state)
    return {"status": "posted", "slug": book["slug"], "post_id": post_id}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--promo-window-only", action="store_true")
    args = parser.parse_args()
    result = run(
        dry_run=args.dry_run,
        force=args.force,
        promo_window_only=args.promo_window_only,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
