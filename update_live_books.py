#!/usr/bin/env python3
import asyncio
import os
import sys
from pathlib import Path

# Add libra directory to path
LIBRA_DIR = Path("/root/libra")
sys.path.insert(0, str(LIBRA_DIR))

from kdp_upload import upload_to_kdp

KDP_DIR = Path("/root/kdp")

async def update_all_books():
    print("=== Starting Batch Update of Live Books ===")
    books = [d for d in KDP_DIR.iterdir() if d.is_dir() and d.name != "logs"]
    
    for book_dir in books:
        slug = book_dir.name
        print(f"\n[{slug}] Processing...")
        
        # 1. Regenerate the EPUB and PDF
        try:
            import app
            print(f"[{slug}] Rebuilding PDF and EPUB with new Justified text and layout rules...")
            content = (book_dir / "ebook.md").read_text(encoding="utf-8")
            processed_content = app._process_markdown_for_pdf(content)
            (book_dir / "_ebook_processed.md").write_text(processed_content, encoding="utf-8")
            
            app._generate_pdf(book_dir)
            app._generate_epub(book_dir)
            print(f"[{slug}] PDF and EPUB generated.")
            
        except Exception as e:
            print(f"[{slug}] Failed to regenerate files: {e}")
            continue
            
        # 2. Upload to KDP (this will now use our updated SEO logic)
        try:
            print(f"[{slug}] Uploading to KDP (SEO + Content)...")
            await upload_to_kdp(slug)
            print(f"[{slug}] Upload complete.")
        except Exception as e:
            print(f"[{slug}] Upload failed: {e}")

if __name__ == "__main__":
    asyncio.run(update_all_books())
