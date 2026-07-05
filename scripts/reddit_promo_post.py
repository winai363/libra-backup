#!/usr/bin/env python3
"""reddit_promo_post.py — auto-post free-promo announcements to r/FreeEBOOKS
on scheduled days, via Reddit's official API (praw, script app).

Safety gates before posting:
  1. today must match a schedule entry (data/reddit_promo_schedule.json)
  2. the book's listing.json free_promo window must cover today (i.e. the
     KDP giveaway is actually running — r/FreeEBOOKS requires the book to be
     free at post time)
  3. Reddit credentials must exist in .env — otherwise Telegram-notify and exit

.env keys required:
  REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD

Cron: daily 21:00 Asia/Bangkok (= ~14:00 UTC, good global visibility);
idempotent via posted_at stamp in the schedule file.
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kdp_upload import ENV, KDP_DIR, logger  # noqa: E402

SCHEDULE = Path(__file__).resolve().parent.parent / "data" / "reddit_promo_schedule.json"


def notify(msg: str):
    import httpx
    tok, chat = ENV.get("TELEGRAM_BOT_TOKEN"), ENV.get("TELEGRAM_CHAT_ID")
    if not tok:
        return
    try:
        httpx.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                   json={"chat_id": chat, "text": msg}, timeout=10)
    except Exception:
        pass


def promo_active(slug: str, today: date) -> bool:
    try:
        l = json.loads((KDP_DIR / slug / "listing.json").read_text())
        fp = l.get("free_promo") or {}
        start = str(fp.get("start", ""))[:10]
        end = str(fp.get("end", ""))[:10]
        return bool(start and end and start <= today.isoformat() <= end)
    except Exception as e:
        logger.warning(f"promo check {slug}: {e}")
        return False


def main():
    cfg = json.loads(SCHEDULE.read_text())
    today = date.today()
    entry = next((p for p in cfg["posts"] if p["date"] == today.isoformat()), None)
    if not entry:
        logger.info("no reddit post scheduled today")
        return
    if entry.get("posted_at"):
        logger.info("already posted today — skip")
        return
    if not promo_active(entry["slug"], today):
        msg = (f"⚠️ Reddit post SKIPPED: {entry['slug']} — KDP free promo ไม่ active วันนี้ "
               f"(กติกา r/FreeEBOOKS ต้องฟรีจริงตอนโพสต์)")
        logger.warning(msg)
        notify(msg)
        return

    creds = {k: ENV.get(k) for k in
             ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USERNAME", "REDDIT_PASSWORD")}
    if not all(creds.values()):
        notify(f"🔔 วันนี้มีคิวโพสต์ Reddit ({entry['slug']}) แต่ยังไม่มีบัญชี Reddit ในระบบ — "
               f"โพสต์มือได้จาก downloads/kdp-launch-kit/reddit-posts.md")
        logger.warning("no reddit credentials — notified for manual post")
        return

    import praw
    reddit = praw.Reddit(
        client_id=creds["REDDIT_CLIENT_ID"],
        client_secret=creds["REDDIT_CLIENT_SECRET"],
        username=creds["REDDIT_USERNAME"],
        password=creds["REDDIT_PASSWORD"],
        user_agent=f"libra-free-promo-announcer/1.0 by u/{creds['REDDIT_USERNAME']}",
    )
    sub = reddit.subreddit(cfg["subreddit"])
    flair_id = None
    try:
        for f in sub.flair.link_templates.user_selectable():
            if cfg.get("flair_hint", "").lower() in f["flair_text"].lower():
                flair_id = f["flair_template_id"]
                break
    except Exception as e:
        logger.warning(f"flair lookup: {e}")
    submission = sub.submit(title=entry["title"], selftext=entry["body"],
                            flair_id=flair_id)
    entry["posted_at"] = datetime.now().isoformat(timespec="seconds")
    entry["reddit_url"] = f"https://reddit.com{submission.permalink}"
    SCHEDULE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    logger.info(f"posted: {entry['reddit_url']}")
    notify(f"✅ โพสต์ Reddit แล้ว: {entry['slug']}\n{entry['reddit_url']}")


if __name__ == "__main__":
    main()
