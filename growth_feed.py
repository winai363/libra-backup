"""Libra growth feed — an RSS 2.0 feed of QA-approved hub articles, shaped for
Pinterest's native RSS auto-publishing.

What Pinterest requires (help.pinterest.com, "Auto-publish Pins from your RSS
feed", read 2026-09-12): RSS 2.* or RSS 1.* (Atom is not supported); each item
needs a title, a description, a link on the *claimed* domain, and an image in an
`<image>`, `<enclosure>` or `<media:content>` tag; Pins appear within 24 hours of
a feed change, oldest content first, up to 200 Pins per day.

What this module refuses to do:
  - emit an entry that is not marked QA-approved;
  - emit a link or image outside our own site (Pinterest would reject it, and an
    off-site link is how a feed turns into someone else's advert);
  - emit manuscript text or a sample beyond a short description — the feed
    carries a teaser and a link, never the book;
  - emit a backlog. The feed is capped, so connecting it cannot fire dozens of
    Pins at once.

Building a feed file is not publishing. Serving it is gated on
`posting_authorization.channel_authorized("pinterest-rss")`.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urlsplit

# A feed that grows without bound becomes a Pin flood on first connection.
DEFAULT_MAX_ITEMS = 5
MAX_ITEMS_HARD = 20
TITLE_LIMITS = (10, 140)
DESCRIPTION_LIMITS = (30, 500)
# Only our own hub paths may be linked: the claimed location is the /libra/growth
# subpath, and a Pin that leaves it is not traffic we can measure or stand behind.
ALLOWED_LINK_PREFIXES = ("/libra/growth/articles/", "/libra/growth/books/")
ALLOWED_IMAGE_PREFIXES = ("/libra/api/books/", "/libra/static/", "/libra/growth/pins/")
# Keys that would mean a manuscript or a restricted sample is riding along.
FORBIDDEN_KEYS = ("manuscript", "ebook_md", "sample_text", "epub", "pdf", "chapter_text")


class FeedEntryRejected(ValueError):
    """An entry failed a feed rule. Raised, never skipped silently: a feed that
    quietly drops items hides why a Pin never appeared."""


def _same_site_path(url: str, site_base: str, allowed_prefixes: tuple) -> str:
    base = urlsplit(site_base)
    parts = urlsplit(url)
    if parts.scheme and parts.scheme != "https":
        raise FeedEntryRejected(f"must be https: {url!r}")
    if parts.hostname and parts.hostname.lower() != (base.hostname or "").lower():
        raise FeedEntryRejected(f"off-site url: {url!r}")
    path = parts.path or ""
    if not path.startswith(allowed_prefixes):
        raise FeedEntryRejected(f"path not allowed in feed: {path!r}")
    return path


def validate_entry(entry: dict, *, site_base: str, now: datetime) -> dict:
    """Return the normalised entry, or raise FeedEntryRejected."""
    if not isinstance(entry, dict):
        raise FeedEntryRejected("entry must be an object")
    for key in FORBIDDEN_KEYS:
        if key in entry:
            raise FeedEntryRejected(f"entry carries book content in {key!r}")
    if entry.get("qa_approved") is not True:
        raise FeedEntryRejected(f"entry {entry.get('id')!r} is not QA-approved")

    entry_id = str(entry.get("id") or "").strip()
    if not entry_id:
        raise FeedEntryRejected("entry needs a stable id for the feed guid")
    title = str(entry.get("title") or "").strip()
    if not TITLE_LIMITS[0] <= len(title) <= TITLE_LIMITS[1]:
        raise FeedEntryRejected(f"title length {len(title)} outside {TITLE_LIMITS}")
    description = " ".join(str(entry.get("description") or "").split())
    if not DESCRIPTION_LIMITS[0] <= len(description) <= DESCRIPTION_LIMITS[1]:
        raise FeedEntryRejected(f"description length {len(description)} outside {DESCRIPTION_LIMITS}")

    link_path = _same_site_path(str(entry.get("link") or ""), site_base, ALLOWED_LINK_PREFIXES)
    image_path = _same_site_path(str(entry.get("image_url") or ""), site_base, ALLOWED_IMAGE_PREFIXES)

    raw_published = str(entry.get("published_at") or "").strip()
    try:
        published = datetime.fromisoformat(raw_published)
    except ValueError as error:
        raise FeedEntryRejected(f"published_at must be ISO-8601: {raw_published!r}") from error
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    if published > now:
        raise FeedEntryRejected(f"published_at is in the future: {raw_published!r}")

    return {
        "id": entry_id,
        "title": title,
        "description": description,
        "link": site_base.rstrip("/") + link_path if not str(entry["link"]).startswith("http")
                else str(entry["link"]),
        "image_url": site_base.rstrip("/") + image_path if not str(entry["image_url"]).startswith("http")
                else str(entry["image_url"]),
        "published_at": published,
    }


def load_entries(directory: Path) -> list:
    """Every *.json in `directory`, unvalidated. Missing directory reads as empty
    so an unconfigured feed is an empty feed, never an error page."""
    path = Path(directory)
    if not path.is_dir():
        return []
    entries = []
    for file in sorted(path.glob("*.json")):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            data.setdefault("id", file.stem)
            entries.append(data)
    return entries


def select_entries(entries: list, *, site_base: str, now: datetime,
                   max_items: int = DEFAULT_MAX_ITEMS,
                   allowed_campaigns=None) -> tuple:
    """(kept, rejected) — the newest `max_items` valid entries, ordered oldest
    first because Pinterest publishes a feed's oldest content first.

    `allowed_campaigns` keeps one channel's feed to that channel's articles: a
    LinkedIn article must not become a Pin just because it was approved."""
    if max_items > MAX_ITEMS_HARD:
        raise ValueError(f"max_items {max_items} above the hard cap {MAX_ITEMS_HARD}")
    kept, rejected = [], []
    for entry in entries:
        try:
            if allowed_campaigns is not None:
                campaign = (entry or {}).get("campaign") if isinstance(entry, dict) else None
                if campaign not in set(allowed_campaigns):
                    raise FeedEntryRejected(
                        f"campaign {campaign!r} does not belong to this feed's channel")
            kept.append(validate_entry(entry, site_base=site_base, now=now))
        except FeedEntryRejected as error:
            rejected.append({"id": str(entry.get("id") if isinstance(entry, dict) else entry),
                             "reason": str(error)})
    kept.sort(key=lambda e: e["published_at"])
    return kept[-max_items:], rejected


def build_rss(entries: list, *, site_base: str, feed_path: str = "/libra/growth/feed.xml",
              channel_title: str = "Libra reading guides",
              channel_description: str = "Short practical guides from our books, "
                                         "with a link to the book they came from.",
              language: str = "en", now: datetime | None = None,
              max_items: int = DEFAULT_MAX_ITEMS, allowed_campaigns=None) -> tuple:
    """(xml, report). Entries are validated here, so an invalid entry can never
    reach the served feed."""
    now = now or datetime.now(timezone.utc)
    kept, rejected = select_entries(entries, site_base=site_base, now=now, max_items=max_items,
                                    allowed_campaigns=allowed_campaigns)

    items = []
    for entry in kept:
        items.append("\n".join([
            "    <item>",
            f"      <title>{html.escape(entry['title'])}</title>",
            f"      <link>{html.escape(entry['link'])}</link>",
            f"      <guid isPermaLink=\"false\">libra-{html.escape(entry['id'])}</guid>",
            f"      <pubDate>{format_datetime(entry['published_at'])}</pubDate>",
            f"      <description>{html.escape(entry['description'])}</description>",
            f"      <media:content url=\"{html.escape(entry['image_url'])}\" medium=\"image\"/>",
            f"      <enclosure url=\"{html.escape(entry['image_url'])}\" type=\"image/jpeg\"/>",
            "    </item>",
        ]))

    xml = "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">',
        "  <channel>",
        f"    <title>{html.escape(channel_title)}</title>",
        f"    <link>{html.escape(site_base.rstrip('/') + '/libra/growth')}</link>",
        f"    <description>{html.escape(channel_description)}</description>",
        f"    <language>{html.escape(language)}</language>",
        f"    <lastBuildDate>{format_datetime(now)}</lastBuildDate>",
        f"    <atom:self xmlns:atom=\"http://www.w3.org/2005/Atom\" href=\"{html.escape(site_base.rstrip('/') + feed_path)}\"/>",
        *items,
        "  </channel>",
        "</rss>",
        "",
    ])
    report = {"items": len(kept), "rejected": rejected, "max_items": max_items,
              "built_at": now.isoformat()}
    return xml, report
