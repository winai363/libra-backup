import re
import subprocess
from pathlib import Path

def clean_images(content):
    # Remove markdown images ![alt](url)
    content = re.sub(r'!\[.*?\]\(.*?\)', '', content)
    # Remove HTML images <img src="..." />
    content = re.sub(r'<img[^>]+>', '', content)
    # Remove trailing blank lines
    content = re.sub(r'\n\s*\n\s*\n', '\n\n', content)
    return content

def rebuild():
    kdp_dir = Path("/root/kdp")
    books = [d for d in kdp_dir.iterdir() if d.is_dir() and (d / "ebook.md").exists()]
    
    # We must also import md_cleaner to fix formatting
    import sys
    sys.path.append("/root/libra")
    import md_cleaner
    
    for book in books:
        md_file = book / "ebook.md"
        epub_file = book / "ebook.epub"
        meta_file = book / "metadata.yaml"
        
        content = md_file.read_text(encoding="utf-8")
        
        # 1. Apply formatting cleaner
        content = md_cleaner.clean(content)
        
        # 2. Strip images
        content = clean_images(content)
        
        # Save back to ebook.md
        md_file.write_text(content, encoding="utf-8")
        
        # Build EPUB
        print(f"Rebuilding EPUB for {book.name}...")
        cmd = [
            "pandoc", str(md_file), "-f", "markdown-yaml_metadata_block",
            "-o", str(epub_file),
            "--resource-path", str(book),
            "--metadata-file", str(meta_file),
            "--epub-cover-image", str(book / "cover.jpg"),
            "--css", "/root/libra/epub.css",
            "--toc", "--toc-depth=2",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"Failed to build EPUB for {book.name}: {res.stderr}")
        else:
            print(f"Successfully rebuilt {book.name}")

if __name__ == "__main__":
    rebuild()
