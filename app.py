import json
import os
import glob
import httpx
import logging
import secrets
import time
import re
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("libra")
logging.getLogger("httpx").setLevel(logging.WARNING)

# ── Config from .env ──
ENV = {}
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            ENV[k.strip()] = v.strip()

app = FastAPI(title="Libra")

KDP_DIR = Path(ENV.get("KDP_DIR", "/root/kdp"))
USERNAME = ENV.get("USERNAME", "")
PASSWORD = ENV.get("PASSWORD", "")
TOKEN = ENV.get("SESSION_TOKEN", "")
LOGIN_ATTEMPTS = {}
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_ATTEMPTS = 5

TELEGRAM_BOT_TOKEN = ENV.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = ENV.get("TELEGRAM_CHAT_ID", "")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline' "
        "https://fonts.googleapis.com https://cdn.tailwindcss.com; "
        "font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline' "
        "https://cdn.tailwindcss.com https://cdn.jsdelivr.net; connect-src 'self'"
    )
    return response


async def translate_th(text: str) -> str:
    """Translate text to Thai using Google Translate."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://translate.googleapis.com/translate_a/single",
                params={"client": "gtx", "sl": "auto", "tl": "th", "dt": "t", "q": text},
                timeout=5,
            )
            return resp.json()[0][0][0]
    except Exception:
        return ""


async def notify(message: str):
    """Send Telegram notification to Pond's personal chat."""
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
    except Exception as e:
        logger.error(f"Telegram notify failed: {e}")


def get_books():
    books = []
    for listing_file in sorted(KDP_DIR.glob("*/listing.json"), reverse=True):
        slug = listing_file.parent.name
        try:
            data = json.loads(listing_file.read_text())
            data["slug"] = slug
            # Find epub file
            epubs = list(listing_file.parent.glob("*.epub"))
            data["has_epub"] = len(epubs) > 0
            data["epub_name"] = epubs[0].name if epubs else None
            # Find paperback PDF
            pdfs = [p for p in listing_file.parent.glob("*paperback*.pdf")]
            data["has_pdf"] = len(pdfs) > 0
            data["pdf_name"] = pdfs[0].name if pdfs else None
            # Find cover
            cover = listing_file.parent / "cover.jpg"
            data["has_cover"] = cover.exists()
            books.append(data)
        except Exception:
            continue
    # Sort by created_at descending
    books.sort(key=lambda b: b.get("created_at", ""), reverse=True)
    return books


def check_auth(request: Request):
    token = request.cookies.get("libra_token")
    if not TOKEN or not token or not secrets.compare_digest(token, TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")


def get_book_dir(slug: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,100}", slug):
        raise HTTPException(status_code=400, detail="Invalid book slug")
    return KDP_DIR / slug


@app.post("/api/auth/login")
async def login(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    attempts = [stamp for stamp in LOGIN_ATTEMPTS.get(client_ip, []) if now - stamp < LOGIN_WINDOW_SECONDS]
    if len(attempts) >= LOGIN_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
    body = await request.json()
    username_ok = secrets.compare_digest(str(body.get("username", "")), USERNAME)
    password_ok = secrets.compare_digest(str(body.get("password", "")), PASSWORD)
    if username_ok and password_ok:
        LOGIN_ATTEMPTS.pop(client_ip, None)
        response = JSONResponse({"ok": True})
        response.set_cookie(
            "libra_token",
            TOKEN,
            httponly=True,
            secure=True,
            samesite="strict",
            max_age=86400,
            path="/libra",
        )
        return response
    attempts.append(now)
    LOGIN_ATTEMPTS[client_ip] = attempts
    raise HTTPException(status_code=401, detail="Wrong password")


@app.get("/api/books")
async def list_books(request: Request, status: str = None):
    check_auth(request)
    books = get_books()
    if status:
        books = [b for b in books if b.get("status") == status]
    return books


@app.get("/api/books/{slug}/epub")
async def download_epub(slug: str, request: Request):
    check_auth(request)
    book_dir = get_book_dir(slug)
    if not book_dir.exists():
        raise HTTPException(status_code=404)
    epubs = list(book_dir.glob("*.epub"))
    if not epubs:
        raise HTTPException(status_code=404, detail="No EPUB found")
    return FileResponse(epubs[0], filename=epubs[0].name, media_type="application/epub+zip")


@app.get("/api/books/{slug}/pdf")
async def download_pdf(slug: str, request: Request):
    check_auth(request)
    book_dir = get_book_dir(slug)
    if not book_dir.exists():
        raise HTTPException(status_code=404)
    pdfs = [p for p in book_dir.glob("*paperback*.pdf")]
    if not pdfs:
        raise HTTPException(status_code=404, detail="No PDF found")
    return FileResponse(pdfs[0], filename=pdfs[0].name, media_type="application/pdf")


@app.get("/api/books/{slug}/cover")
async def get_cover(slug: str, request: Request):
    check_auth(request)
    cover = get_book_dir(slug) / "cover.jpg"
    if not cover.exists():
        raise HTTPException(status_code=404)
    return FileResponse(cover, media_type="image/jpeg")


@app.post("/api/books/{slug}/generate-pdf")
async def generate_pdf(slug: str, request: Request, force: bool = False):
    check_auth(request)
    book_dir = get_book_dir(slug)
    listing_file = book_dir / "listing.json"
    md_file = book_dir / "ebook.md"
    if not listing_file.exists():
        raise HTTPException(status_code=404, detail="Book not found")
    if not md_file.exists():
        raise HTTPException(status_code=404, detail="No ebook.md found")

    data = json.loads(listing_file.read_text())
    title = data.get("title", slug)
    safe_name = title.replace(" ", "-").replace("/", "-").replace(":", "-")
    pdf_path = book_dir / f"{safe_name}-paperback.pdf"

    if pdf_path.exists() and not force:
        return {"ok": True, "message": "PDF already exists"}
    if force:
        for old_pdf in book_dir.glob("*paperback*.pdf"):
            old_pdf.unlink(missing_ok=True)

    # Also create metadata.yaml if missing
    meta_file = book_dir / "metadata.yaml"
    if not meta_file.exists():
        lang = data.get("language", "pt-BR")
        lang_code = "pt-BR" if "Portuguese" in lang else "en" if "English" in lang else "id" if "Indonesian" in lang else lang[:5]
        meta_file.write_text(
            f'---\ntitle: "{title}"\n'
            f'subtitle: "{data.get("subtitle", "")}"\n'
            f'author: "WK Bui"\nlang: {lang_code}\ndate: "2026"\n---\n'
        )

    import subprocess
    import re
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    import md_cleaner

    # Pre-process markdown
    md_text = md_file.read_text(encoding="utf-8")

    # Step 1 — structural fixes: remove title heading, inline TOC, and ---
    #          before chapter headings (all three corrupt pandoc TOC page numbers
    #          or leave near-empty pages in the PDF).
    md_processed = md_cleaner.clean(md_text)

    # Step 1b — structural validation: detect and fix duplicate Part numbers,
    #            misplaced continuation chapters, and back matter position.
    try:
        from book_validator import validate_and_fix
        md_processed, qa_report = validate_and_fix(md_processed)
        qa_text = qa_report.summary()
        logger.info(f"book_validator [{slug}]: {qa_text}")
        # Persist QA report alongside the PDF
        (book_dir / "qa-report.txt").write_text(qa_text, encoding="utf-8")
        if not qa_report.passed:
            raise HTTPException(
                status_code=422,
                detail=f"Book structure issues that could not be auto-fixed:\n{qa_text}"
            )
    except HTTPException:
        raise
    except Exception as _bv_err:
        logger.warning(f"book_validator error for {slug}: {_bv_err}")

    # Step 2 — wrap bare URLs in <> so pandoc converts them to \url{}
    md_processed = re.sub(
        r'(?<![(<\[])(?<!\]\()https?://[^\s\)>\]]+',
        r'<\g<0>>',
        md_processed,
    )

    # Prepend copyright page (raw LaTeX passthrough — xelatex handles UTF-8 natively)
    subtitle = data.get("subtitle", "")
    import time as _time
    pub_year = _time.strftime('%Y')
    copyright_block = (
        "\\newpage\n"
        "\\thispagestyle{empty}\n\n"
        f"*{title}*\n\n"
        + (f"*{subtitle}*\n\n" if subtitle else "")
        + "---\n\n"
        f"Copyright © {pub_year} WK Bui\n\n"
        "All rights reserved. No part of this publication may be reproduced, distributed, "
        "or transmitted in any form or by any means, including photocopying or electronic methods, "
        "without the prior written permission of the author.\n\n"
        "*Disclaimer: This publication is for informational and educational purposes only. "
        "The author and publisher make no representations or warranties regarding the accuracy "
        "or completeness of any information contained herein. "
        "Readers should consult a qualified professional for advice specific to their situation.*\n\n"
        "\\clearpage\n\n"
    )
    md_processed = copyright_block + md_processed

    processed_md = book_dir / "_ebook_processed.md"
    processed_md.write_text(md_processed, encoding="utf-8")

    # Create LaTeX header for better table/layout handling
    header_file = book_dir / "_header.tex"
    header_file.write_text(r"""
\usepackage{tabularx}
\usepackage{booktabs}
\usepackage{adjustbox}
\usepackage{float}
\usepackage{longtable}
\usepackage{array}
\usepackage{graphicx}
\usepackage{ragged2e}
\usepackage{titlesec}

% --- URL line breaking (prevent overflow) ---
\usepackage{url}
\usepackage{xurl}
\urlstyle{same}
\Urlmuskip=0mu plus 3mu

% --- Readable body text ---
% We use full justification for the body text, as requested.
\microtypesetup{protrusion=true,expansion=false}

% Moderate hyphenation to avoid rivers and broken words at the margin.
\hyphenpenalty=700
\tolerance=1000
\emergencystretch=2em
\hbadness=10000
\vbadness=10000
\setlength{\hfuzz}{5pt}
\overfullrule=0pt

% Allow line breaks at any character in URLs
\makeatletter
\g@addto@macro\UrlBreaks{\UrlOrds}
\makeatother
\PassOptionsToPackage{breaklinks=true}{hyperref}

% --- Page layout ---
\raggedbottom

% --- Table overflow prevention ---
\let\oldlongtable\longtable
\renewcommand{\longtable}{\scriptsize\oldlongtable}
\setlength{\tabcolsep}{2pt}
\renewcommand{\arraystretch}{1.28}
\newcolumntype{L}[1]{>{\raggedright\arraybackslash}p{#1}}
\newcolumntype{C}[1]{>{\centering\arraybackslash}p{#1}}

% Catch images that are too wide
\makeatletter
\def\maxwidth{\ifdim\Gin@nat@width>\linewidth\linewidth\else\Gin@nat@width\fi}
\makeatother

% --- Interior images: scale to 72% of line width, centered ---
\setkeys{Gin}{width=0.72\linewidth,keepaspectratio}

% Center figures, small italic caption, no redundant "Figure N:" prefix
\usepackage[font=small,labelfont=it,labelformat=empty,justification=centering]{caption}

% Pin images exactly where written in the text (no floating to page top/bottom),
% so an image never drifts away from its paragraph and leaves a weird gap.
\floatplacement{figure}{H}

% --- Paragraph spacing ---
\setlength{\parskip}{0.5em}
\setlength{\parindent}{0pt}

% --- Headings ---
% Keep headings ragged-right. Full justification on short headings creates
% giant word gaps such as "Planificación     de     inversiones".
\titleformat{\section}
  {\normalfont\Large\bfseries\RaggedRight}{\thesection}{1em}{}
\titleformat{\subsection}
  {\normalfont\large\bfseries\RaggedRight}{\thesubsection}{1em}{}
\titleformat{\subsubsection}
  {\normalfont\normalsize\bfseries\RaggedRight}{\thesubsubsection}{1em}{}

% --- Prevent orphans/widows ---
\widowpenalty=10000
\clubpenalty=10000

% --- Chapter/section page break rules ---
% Only # headings (\chapter) force new pages.
% ## headings (\section) just need enough space.
\usepackage{needspace}
\usepackage{etoolbox}
\preto\section{\needspace{8\baselineskip}}
\preto\subsection{\needspace{5\baselineskip}}
\preto\subsubsection{\needspace{4\baselineskip}}

% --- Clean chapter/Part page breaks ---
% Each # heading (a "Part") ALWAYS starts on a fresh page (default \chapter
% \clearpage), but with a COMPACT head: no 50pt top gap, no "Chapter N" label.
\makeatletter
\def\@makechapterhead#1{%
  \vspace*{20\p@}%
  {\parindent \z@ \raggedright \normalfont
    \huge \bfseries #1\par\nobreak
    \vskip 24\p@
  }}
\def\@makeschapterhead#1{%
  \vspace*{20\p@}%
  {\parindent \z@ \raggedright
    \normalfont \huge \bfseries #1\par\nobreak
    \vskip 24\p@
  }}
\makeatother

% --- Better page breaks ---
\predisplaypenalty=0
\postdisplaypenalty=0
\makeatletter
\@beginparpenalty=0
\@endparpenalty=0
\@itempenalty=-100
\makeatother

""")

    # KDP paperback: 6x9 inch trim size
    result = subprocess.run(
        ["pandoc", str(processed_md), "-o", str(pdf_path),
         "--pdf-engine=xelatex",
         "--resource-path", str(book_dir),
         "--template", "/root/libra/kdp-template.latex",
         "--metadata-file", str(meta_file),
         "--toc", "--toc-depth=2",
         "-H", str(header_file),
         "--lua-filter", "/root/libra/break-urls.lua",
         "--lua-filter", "/root/libra/fix-tables.lua",
         "-V", "geometry:paperwidth=6in,paperheight=9in,top=0.75in,bottom=0.75in,inner=0.75in,outer=0.625in",
         "-V", "fontsize=10pt",
         "-V", "mainfont=DejaVu Serif",
         "-V", "sansfont=DejaVu Sans",
         "-V", "monofont=DejaVu Sans Mono",
         "-V", "documentclass=report",
         "-V", "classoption=openany,oneside"],
        capture_output=True, text=True, timeout=120
    )
    # Cleanup temp files
    processed_md.unlink(missing_ok=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {result.stderr[:500]}")

    title_th = await translate_th(title)
    await notify(f"📄 <b>PDF Generated</b>\n{title}\n({title_th})")
    return {"ok": True, "message": "PDF generated", "filename": pdf_path.name}


@app.patch("/api/books/{slug}/status")
async def update_status(slug: str, request: Request):
    check_auth(request)
    listing_file = get_book_dir(slug) / "listing.json"
    if not listing_file.exists():
        raise HTTPException(status_code=404)
    data = json.loads(listing_file.read_text())
    body = await request.json()
    new_status = body.get("status")
    if new_status not in {"ready", "archived"}:
        raise HTTPException(status_code=400, detail="Status can only be changed to ready or archived manually")
    if new_status == "ready":
        from quality_gate import validate_book, write_report
        quality = validate_book(slug, require_pdf=True)
        write_report(quality)
        if not quality.passed:
            raise HTTPException(status_code=422, detail={"quality_errors": quality.errors})
    data["status"] = new_status
    if new_status == "ready":
        data["uploaded_at"] = None
    listing_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    title = data.get("title", slug)
    title_th = await translate_th(title)
    await notify(f"↩️ <b>Status changed to {new_status}</b>\n{title}\n({title_th})")
    return {"ok": True, "status": new_status}


@app.post("/api/books")
async def create_book(request: Request):
    """Create a new book entry. Called by Tim/skills after generating an ebook."""
    check_auth(request)
    body = await request.json()
    slug = body.get("slug")
    if not slug:
        raise HTTPException(status_code=400, detail="slug is required")
    book_dir = get_book_dir(slug)
    listing_file = book_dir / "listing.json"
    if not listing_file.exists():
        raise HTTPException(status_code=404, detail=f"No listing.json found at {book_dir}")
    data = json.loads(listing_file.read_text())
    title = data.get("title", slug)
    subtitle = data.get("subtitle", "")
    lang = data.get("language", "")
    keywords_count = len(data.get("keywords", []))
    has_epub = any(book_dir.glob("*.epub"))
    has_cover = (book_dir / "cover.jpg").exists()
    title_th = await translate_th(title)
    parts = [f"📚 <b>New Book on Libra</b>"]
    parts.append(f"<b>{title}</b>")
    if title_th:
        parts.append(f"({title_th})")
    if subtitle:
        parts.append(f"<i>{subtitle}</i>")
    if lang:
        parts.append(f"Language: {lang}")
    parts.append(f"Keywords: {keywords_count}")
    parts.append(f"EPUB: {'✅' if has_epub else '❌'}  Cover: {'✅' if has_cover else '❌'}")
    parts.append(f"\nhttps://libra.incomeinclick.com")
    await notify("\n".join(parts))
    return {"ok": True, "slug": slug, "title": title}


@app.post("/api/books/{slug}/request-approval")
async def request_approval(slug: str, request: Request):
    """Send approval request to Telegram before uploading to KDP"""
    check_auth(request)
    book_dir = get_book_dir(slug)
    listing_file = book_dir / "listing.json"
    if not listing_file.exists():
        raise HTTPException(status_code=404, detail="Book not found")

    data = json.loads(listing_file.read_text())
    data["approval_pending"] = True
    listing_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    title = data.get("title", slug)
    subtitle = data.get("subtitle", "")
    description = data.get("description", "")[:200]
    keywords = ", ".join(data.get("keywords", [])[:5])
    language = data.get("language", "English")

    title_th = await translate_th(title)

    # Build approval message with review link
    msg = f"📤 <b>New Book Ready for Review</b>\n\n"
    msg += f"<b>{title}</b>\n"
    if title_th:
        msg += f"<i>({title_th})</i>\n"
    if subtitle:
        msg += f"Subtitle: {subtitle}\n"
    msg += f"Language: {language}\n"
    msg += f"Keywords: {keywords}\n\n"
    msg += f"<a href='https://newton-winai-klinprasom.incomeinclick.in.th/libra/review/{slug}?slug={slug}'>📖 Review & Approve</a>"

    await notify(msg)
    return {"ok": True, "message": "Approval request sent to Telegram"}


@app.post("/api/books/{slug}/approve-kdp")
async def approve_kdp(slug: str, request: Request):
    """Approve KDP upload and trigger the upload process"""
    check_auth(request)
    book_dir = get_book_dir(slug)
    listing_file = book_dir / "listing.json"
    if not listing_file.exists():
        raise HTTPException(status_code=404, detail="Book not found")

    from quality_gate import validate_book, write_report
    quality = validate_book(slug, require_pdf=True, check_urls=True, require_editorial=True)
    write_report(quality)
    if not quality.passed:
        raise HTTPException(status_code=422, detail={"quality_errors": quality.errors})

    data = json.loads(listing_file.read_text())
    if data.get("kdp_uploading"):
        raise HTTPException(status_code=409, detail="This book is already being uploaded")
    data["approval_pending"] = False
    data["kdp_uploading"] = True
    listing_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    title = data.get("title", slug)
    subtitle = data.get("subtitle", "")
    title_th = await translate_th(title)

    # Auto-generate cover if missing
    cover_file = book_dir / "cover.jpg"
    if not cover_file.exists() or cover_file.stat().st_size < 10000:
        try:
            from cover_generator import generate_cover as _gen_cover
            _gen_cover(
                book_dir   = book_dir,
                title      = title,
                subtitle   = subtitle,
                author     = data.get("author", "WK Bui"),
                categories = data.get("categories", []),
                keywords   = data.get("keywords", []),
            )
        except Exception:
            pass

    # Trigger KDP upload in background
    import subprocess
    upload_log = open(KDP_DIR / "logs" / f"upload-{slug}.log", "a")
    subprocess.Popen(
        ["python3", str(Path(__file__).parent / "kdp_upload.py"), slug],
        stdout=upload_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    upload_log.close()

    msg = f"⏳ <b>Uploading to KDP...</b>\n{title}\n({title_th})"
    await notify(msg)

    return {"ok": True, "message": "KDP upload started"}


@app.get("/api/books/{slug}/content")
async def get_book_content(slug: str, request: Request):
    """Return the book's markdown content for preview."""
    check_auth(request)
    book_dir = get_book_dir(slug)
    md_file = book_dir / "ebook.md"
    if md_file.exists():
        return {"format": "markdown", "content": md_file.read_text()}
    # Fallback: extract text from EPUB
    epubs = list(book_dir.glob("*.epub"))
    if epubs and epubs[0].stat().st_size > 100:
        try:
            import zipfile
            from html.parser import HTMLParser
            class TextExtractor(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.text = []
                def handle_data(self, data):
                    self.text.append(data)
            content_parts = []
            with zipfile.ZipFile(str(epubs[0])) as zf:
                for name in sorted(zf.namelist()):
                    if name.endswith(('.xhtml', '.html', '.htm')):
                        html = zf.read(name).decode('utf-8', errors='ignore')
                        parser = TextExtractor()
                        parser.feed(html)
                        content_parts.append('\n'.join(parser.text))
            return {"format": "text", "content": '\n\n'.join(content_parts)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"EPUB read error: {e}")
    raise HTTPException(status_code=404, detail="No content found (ebook.md or EPUB)")


@app.get("/preview/{slug}", response_class=HTMLResponse)
async def preview_page(slug: str, request: Request):
    """Full book preview — cover, details, and full content."""
    check_auth(request)
    book_dir = get_book_dir(slug)
    listing_file = book_dir / "listing.json"
    if not listing_file.exists():
        return HTMLResponse("<h1>Book not found</h1>", status_code=404)
    html_path = Path(__file__).parent / "templates" / "preview.html"
    return HTMLResponse(html_path.read_text())


@app.get("/review/{slug}", response_class=HTMLResponse)
async def review_page(slug: str, request: Request):
    """Review page to check book details before KDP upload"""
    check_auth(request)
    book_dir = get_book_dir(slug)
    listing_file = book_dir / "listing.json"
    if not listing_file.exists():
        return HTMLResponse("<h1>Book not found</h1>", status_code=404)
    html_path = Path(__file__).parent / "templates" / "review.html"
    return HTMLResponse(html_path.read_text())


@app.get("/approval/{slug}", response_class=HTMLResponse)
async def approval_page(slug: str, request: Request):
    """Approval page for KDP upload"""
    check_auth(request)
    book_dir = get_book_dir(slug)
    listing_file = book_dir / "listing.json"
    if not listing_file.exists():
        return HTMLResponse("<h1>Book not found</h1>", status_code=404)
    html_path = Path(__file__).parent / "templates" / "approval.html"
    return HTMLResponse(html_path.read_text())


@app.get("/api/pipeline-status")
async def pipeline_status(request: Request):
    """Return all books with per-step pipeline status."""
    check_auth(request)
    books_out = []
    for listing_file in sorted(KDP_DIR.glob("*/listing.json"), reverse=True):
        if listing_file.parent.name == "logs":
            continue
        slug = listing_file.parent.name
        try:
            data = json.loads(listing_file.read_text())
        except Exception:
            continue
        book_dir = listing_file.parent
        has_md    = (book_dir / "ebook.md").exists()
        has_epub  = len(list(book_dir.glob("*.epub"))) > 0
        cover     = book_dir / "cover.jpg"
        has_cover = cover.exists() and cover.stat().st_size > 10_000
        has_pdf   = len([p for p in book_dir.glob("*paperback*.pdf")]) > 0
        kdp_book_id = data.get("kdp_book_id") or ""
        has_upload  = bool(kdp_book_id) or data.get("status") == "uploaded"
        steps = {
            "generate": has_md,
            "epub":     has_epub,
            "cover":    has_cover,
            "upload":   has_upload,
            "pdf":      has_pdf,
        }
        # current step = first False step, or "done" if all True
        step_order = ["generate", "epub", "cover", "upload", "pdf"]
        current = "done"
        for s in step_order:
            if not steps[s]:
                current = s
                break
        books_out.append({
            "slug":         slug,
            "title":        data.get("title", slug),
            "language":     data.get("language", ""),
            "created_at":   data.get("created_at", ""),
            "uploaded_at":  data.get("uploaded_at", ""),
            "status":       data.get("status", "ready"),
            "kdp_uploading":data.get("kdp_uploading", False),
            "kdp_error":    data.get("kdp_error", ""),
            "kdp_book_id":  kdp_book_id,
            "steps":        steps,
            "current_step": current,
        })
    books_out.sort(key=lambda b: b.get("created_at", ""), reverse=True)
    return {"books": books_out, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M")}


@app.get("/status", response_class=HTMLResponse)
async def status_page(request: Request):
    check_auth(request)
    html_path = Path(__file__).parent / "templates" / "status.html"
    return HTMLResponse(html_path.read_text())


# ── Live Audit endpoints ───────────────────────────────────────────────────────

@app.get("/api/audit/report")
async def get_audit_report(request: Request):
    """Return latest audit report JSON."""
    check_auth(request)
    report_file = KDP_DIR / "logs" / "audit_report.json"
    if not report_file.exists():
        raise HTTPException(status_code=404, detail="No audit report found. Run kdp_live_audit.py first.")
    return json.loads(report_file.read_text())


@app.post("/api/audit/run")
async def run_audit(request: Request):
    """Trigger audit in background. Returns immediately."""
    check_auth(request)
    import subprocess as _sp
    _sp.Popen(
        ["python3", str(Path(__file__).parent / "kdp_live_audit.py")],
        stdout=open(str(KDP_DIR / "logs" / "audit.log"), "a"),
        stderr=subprocess.STDOUT,
    )
    await notify("🔍 <b>KDP Live Audit started</b>\nCheck /audit when complete.")
    return {"ok": True, "message": "Audit started in background. Check /audit for results."}


@app.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request):
    """Show the live audit HTML dashboard."""
    check_auth(request)
    report_file = KDP_DIR / "logs" / "audit_report.html"
    if not report_file.exists():
        return HTMLResponse("""
<html><body style="font-family:sans-serif;max-width:800px;margin:80px auto;text-align:center">
<h2>No audit report yet</h2>
<p>Run the audit first:</p>
<pre style="background:#eee;padding:16px;border-radius:8px;display:inline-block">
python3 /root/libra/kdp_live_audit.py
</pre>
<p>Or trigger it via the API:</p>
<pre style="background:#eee;padding:16px;border-radius:8px;display:inline-block">
POST /api/audit/run
</pre>
</body></html>""")
    return HTMLResponse(report_file.read_text())


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "templates" / "index.html"
    return HTMLResponse(html_path.read_text())
