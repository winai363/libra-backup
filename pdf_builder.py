#!/usr/bin/env python3
"""pdf_builder.py — build the KDP paperback PDF for a book.

Extracted from app.py's /api/books/{slug}/generate-pdf route so the same logic
can run without an authenticated HTTP request — e.g. from regenerate_book.py,
topup_book.py, or the command line after a regeneration. The FastAPI route in
app.py delegates here so there is a single source of truth for the pandoc
invocation (fonts, CJK/Thai line breaking, header, trim size).

Usage:  python3 pdf_builder.py <slug> [--force]
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import md_cleaner
from book_validator import validate_and_fix

KDP_DIR = Path("/root/kdp")


def build_paperback_pdf(slug: str, force: bool = False, root: Path | None = None) -> Path:
    """Build (or reuse) the paperback PDF. Returns the PDF path.

    `root` lets frozen staging build inside its own tree instead of live KDP.

    Raises ValueError if the book is missing required files or the structural
    validator finds issues it cannot auto-fix; RuntimeError if pandoc fails.
    """
    book_dir = (root if root is not None else KDP_DIR) / slug
    listing_file = book_dir / "listing.json"
    md_file = book_dir / "ebook.md"
    if not listing_file.exists():
        raise ValueError(f"Book not found: {slug}")
    if not md_file.exists():
        raise ValueError(f"No ebook.md found for {slug}")

    data = json.loads(listing_file.read_text(encoding="utf-8"))
    title = data.get("title", slug)
    safe_name = title.replace(" ", "-").replace("/", "-").replace(":", "-")
    pdf_path = book_dir / f"{safe_name}-paperback.pdf"

    if pdf_path.exists() and not force:
        return pdf_path
    if force:
        for old_pdf in book_dir.glob("*paperback*.pdf"):
            old_pdf.unlink(missing_ok=True)

    # Also create metadata.yaml if missing
    meta_file = book_dir / "metadata.yaml"
    if not meta_file.exists():
        lang = data.get("language", "pt-BR")
        lang_code = ("pt-BR" if "Portuguese" in lang else "en" if "English" in lang
                     else "id" if "Indonesian" in lang else lang[:5])
        meta_file.write_text(
            f'---\ntitle: "{title}"\n'
            f'subtitle: "{data.get("subtitle", "")}"\n'
            f'author: "WK Bui"\nlang: {lang_code}\ndate: "2026"\n---\n'
        )

    md_text = md_file.read_text(encoding="utf-8")

    # Step 1 — structural cleanup (title heading, inline TOC, --- before chapters)
    md_processed = md_cleaner.clean(md_text)

    # Step 1b — structural validation: duplicate Parts, back matter position, etc.
    md_processed, qa_report = validate_and_fix(md_processed)
    qa_text = qa_report.summary()
    (book_dir / "qa-report.txt").write_text(qa_text, encoding="utf-8")
    if not qa_report.passed:
        raise ValueError(
            f"Book structure issues that could not be auto-fixed:\n{qa_text}")

    # Step 2 — wrap bare URLs in <> so pandoc converts them to \url{}
    md_processed = re.sub(
        r'(?<![(<\[])(?<!\]\()https?://[^\s\)>\]]+',
        r'<\g<0>>',
        md_processed,
    )

    # Prepend copyright page (raw LaTeX passthrough — xelatex handles UTF-8)
    subtitle = data.get("subtitle", "")
    pub_year = time.strftime('%Y')
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

    # --- Script detection: DejaVu has no CJK/Thai glyphs, so xelatex would
    # silently DROP those characters (blank paperback). Pick a script-capable
    # font and enable ICU line breaking (CJK/Thai have no inter-word spaces).
    _lang = str(data.get("language", "")).lower()
    if any(k in _lang for k in ("japanese", "chinese", "korean", "mandarin",
                                "cantonese")) or _lang in ("ja", "zh", "ko",
                                                           "zh-cn", "zh-tw"):
        _font = "Noto Sans CJK JP"
        font_args = ["-V", f"mainfont={_font}", "-V", f"sansfont={_font}",
                     "-V", "monofont=Noto Sans Mono CJK JP"]
        _script_lb = ('\n% --- CJK line breaking ---\n'
                      '\\XeTeXlinebreaklocale "ja"\n'
                      '\\XeTeXlinebreakskip=0pt plus 0.1pt\n')
    elif "thai" in _lang or _lang == "th":
        _font = "Noto Sans Thai"
        font_args = ["-V", f"mainfont={_font}", "-V", f"sansfont={_font}",
                     "-V", f"monofont={_font}"]
        _script_lb = ('\n% --- Thai line breaking (ICU dictionary) ---\n'
                      '\\XeTeXlinebreaklocale "th"\n'
                      '\\XeTeXlinebreakskip=0pt plus 0.1pt\n')
    else:
        font_args = ["-V", "mainfont=DejaVu Serif", "-V", "sansfont=DejaVu Sans",
                     "-V", "monofont=DejaVu Sans Mono"]
        _script_lb = ""

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

% --- No overfull lines into KDP margins (previewer rejects them) ---
\sloppy
\setlength{\emergencystretch}{3em}
% ragged-right chapter/section titles so long bold headings wrap instead of
% protruding into the margin (KDP "insufficient gutter" error)
\titleformat{\chapter}[display]{\normalfont\huge\bfseries\RaggedRight}{\chaptertitlename\ \thechapter}{20pt}{\Huge}
\titleformat{\section}{\normalfont\Large\bfseries\RaggedRight}{\thesection}{1em}{}
\titleformat{\subsection}{\normalfont\large\bfseries\RaggedRight}{\thesubsection}{1em}{}

% --- Readable body text ---
\microtypesetup{protrusion=true,expansion=false}

% Allow hyphenation especially for long compound words (German, etc.)
\hyphenpenalty=200
\tolerance=1400
\emergencystretch=3em
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

% Pin images exactly where written in the text (no floating).
\floatplacement{figure}{H}

% --- Paragraph spacing ---
\setlength{\parskip}{0.5em}
\setlength{\parindent}{0pt}

% --- Headings ---
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
\usepackage{needspace}
\usepackage{etoolbox}
\preto\section{\needspace{8\baselineskip}}
\preto\subsection{\needspace{5\baselineskip}}
\preto\subsubsection{\needspace{4\baselineskip}}

% --- Clean chapter/Part page breaks ---
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

""" + _script_lb)

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
         *font_args,
         "-V", "documentclass=report",
         "-V", "classoption=openany,oneside"],
        capture_output=True, text=True, timeout=120
    )
    processed_md.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(f"PDF generation failed: {result.stderr[:500]}")
    return pdf_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 pdf_builder.py <slug> [--force]")
        sys.exit(1)
    _slug = sys.argv[1]
    _force = "--force" in sys.argv
    p = build_paperback_pdf(_slug, force=_force)
    print(f"PDF ready: {p}")
