"""From a finished book to a Payhip product — guards first, files second.

The guard that matters most: an EPUB enrolled in KDP Select is exclusive to
Amazon. Selling it anywhere else breaks that term, and with four content
blocks on the account already, a fifth strike is the one risk this lane exists
to avoid. So enrolment is checked before anything is packaged.
"""

from __future__ import annotations

import json
import re
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from business_ledger import init_ledger

CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class CatalogError(RuntimeError):
    pass


def _listing(book_dir: Path) -> dict:
    path = Path(book_dir) / "listing.json"
    if not path.exists():
        raise CatalogError(f"listing.json missing in {book_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


def _pdf(book_dir: Path) -> Path:
    pdfs = sorted(p for p in Path(book_dir).glob("*.pdf") if "paperback" in p.name.lower())
    if not pdfs:
        raise CatalogError("paperback PDF missing")
    return pdfs[0]


def guard_book_for_payhip(book_dir: Path) -> dict:
    """Refuse anything that must not become a Payhip product. Returns the listing."""
    book_dir = Path(book_dir)
    listing = _listing(book_dir)

    select = listing.get("kdp_select") or {}
    if str(select.get("status") or "").lower() == "enrolled":
        raise CatalogError(
            "this ebook is enrolled in KDP Select and is exclusive to Amazon — "
            "selling it elsewhere breaks that term"
        )

    manifest_path = book_dir / "staging-manifest.json"
    if manifest_path.exists():
        status = json.loads(manifest_path.read_text(encoding="utf-8")).get("status")
        if status != "staged_quality_passed":
            raise CatalogError(f"book has not passed quality staging (status={status})")
    else:
        report = book_dir / "quality-report.json"
        if not report.exists() or not json.loads(report.read_text()).get("passed"):
            raise CatalogError("book has no passing quality report")

    for required in ("ebook.epub", "cover.jpg"):
        path = book_dir / required
        if not path.exists() or path.stat().st_size < 1000:
            raise CatalogError(f"{required} missing or empty")
    _pdf(book_dir)

    if listing.get("ai_generated_images") and not listing.get("ai_content_disclosure"):
        raise CatalogError("AI-generated images without an AI content disclosure")
    return listing


def _readme(listing: dict) -> str:
    ai_note = ""
    if listing.get("ai_generated_images") or listing.get("ai_content_disclosure"):
        ai_note = (
            "\nTransparence : les illustrations intérieures ont été produites avec "
            "l'aide d'un modèle d'intelligence artificielle et vérifiées par l'auteur.\n"
        )
    return (
        f"{listing.get('title', '')}\n"
        f"{listing.get('subtitle', '')}\n\n"
        "Contenu de ce téléchargement :\n"
        "  - version PDF (mise en page imprimable)\n"
        "  - version EPUB (liseuse / téléphone)\n"
        f"{ai_note}\n"
        "Usage personnel. Merci de ne pas redistribuer ce fichier.\n"
        "© WK Bui\n"
    )


def build_bundle(book_dir: Path, out_dir: Path) -> Path:
    """Package exactly what the buyer receives: PDF + EPUB + a short README."""
    book_dir = Path(book_dir)
    listing = guard_book_for_payhip(book_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle = out_dir / f"{book_dir.name}.zip"

    pdf = _pdf(book_dir)
    safe_title = re.sub(r"[^A-Za-z0-9À-ÿ]+", "-", str(listing.get("title", book_dir.name))).strip("-")
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(pdf, arcname=f"{safe_title}.pdf")
        archive.write(book_dir / "ebook.epub", arcname=f"{safe_title}.epub")
        archive.writestr("LISEZ-MOI.txt", _readme(listing))
    return bundle


def build_product_spec(book_dir: Path, *, price_minor: int, currency: str) -> dict:
    book_dir = Path(book_dir)
    listing = guard_book_for_payhip(book_dir)
    if isinstance(price_minor, bool) or not isinstance(price_minor, int) or price_minor <= 0:
        raise CatalogError("price_minor must be a positive integer in minor units")
    if not CURRENCY_RE.match(str(currency)):
        raise CatalogError("currency must be an uppercase ISO code")

    description = str(listing.get("description") or "").strip()
    if listing.get("ai_generated_images") or listing.get("ai_content_disclosure"):
        description += (
            "\n\nTransparence : les illustrations intérieures ont été produites avec l'aide "
            "d'un modèle d'intelligence artificielle et vérifiées par l'auteur."
        )
    return {
        "slug": book_dir.name,
        "title": str(listing.get("title") or book_dir.name),
        "subtitle": str(listing.get("subtitle") or ""),
        "language": str(listing.get("language") or ""),
        "description": description,
        "price_minor": price_minor,
        "currency": currency,
        "price_display": f"{price_minor // 100}.{price_minor % 100:02d}",
        "cover": str(book_dir / "cover.jpg"),
        "keywords": list(listing.get("keywords") or []),
    }


def record_product(path: Path, spec: dict, *, provider_product_id: str, status: str,
                   provider: str = "payhip") -> None:
    path = Path(path)
    init_ledger(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO commerce_products"
            "(slug, provider, provider_product_id, status, currency, price_minor, updated_at)"
            " VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(slug) DO UPDATE SET"
            # provider is updated too: a product moved to another storefront
            # must stop being attributed to the old one.
            " provider=excluded.provider,"
            " provider_product_id=excluded.provider_product_id, status=excluded.status,"
            " currency=excluded.currency, price_minor=excluded.price_minor,"
            " updated_at=excluded.updated_at",
            (spec["slug"], provider, provider_product_id, status, spec["currency"],
             int(spec["price_minor"]), datetime.now(timezone.utc).isoformat()),
        )


def list_products(path: Path) -> list:
    path = Path(path)
    if not path.exists():
        return []
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT * FROM commerce_products ORDER BY updated_at DESC"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(row) for row in rows]
