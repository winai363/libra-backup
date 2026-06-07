#!/usr/bin/env python3
"""
kdp_live_audit.py — Audit all uploaded/live KDP books for structural issues.

Usage:
  python3 kdp_live_audit.py              # audit every uploaded book
  python3 kdp_live_audit.py --slug SLUG  # audit a single book

Outputs:
  /root/kdp/logs/audit_report.json       — machine-readable report (for replace script)
  /root/kdp/logs/audit_report.html       — HTML dashboard
  {book_dir}/audit/corrected/            — corrected EPUB + PDF
  {book_dir}/audit/original_backup/      — backup of originals (never overwritten)
  {book_dir}/audit/qa_report.txt         — per-book QA detail
"""

import json
import os
import re
import sys
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
load_dotenv("/root/libra/.env")

import md_cleaner
import book_validator

KDP_DIR   = Path(os.getenv("KDP_DIR", "/root/kdp"))
LIBRA_DIR = Path(__file__).parent
LOGS_DIR  = KDP_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)


# ── Book discovery ─────────────────────────────────────────────────────────────

def get_uploaded_books(slug_filter=None):
    """Return all books that have been uploaded to KDP (have kdp_book_id)."""
    books = []
    for listing_file in sorted(KDP_DIR.glob("*/listing.json")):
        if listing_file.parent.name == "logs":
            continue
        try:
            data = json.loads(listing_file.read_text())
        except Exception:
            continue

        slug   = listing_file.parent.name
        if slug_filter and slug != slug_filter:
            continue

        kdp_id = data.get("kdp_book_id", "")
        status = data.get("status", "ready")
        if not kdp_id and status not in ("uploaded", "live"):
            continue

        books.append({
            "slug":        slug,
            "title":       data.get("title", slug),
            "subtitle":    data.get("subtitle", ""),
            "author":      data.get("author", "WK Bui"),
            "language":    data.get("language", ""),
            "kdp_book_id": kdp_id,
            "status":      status,
            "created_at":  data.get("created_at", ""),
            "uploaded_at": data.get("uploaded_at", ""),
            "book_dir":    str(listing_file.parent),
        })
    return books


# ── Per-book audit ─────────────────────────────────────────────────────────────

def audit_book(book_info):
    """Run full QA on one book. Returns audit result dict."""
    slug     = book_info["slug"]
    book_dir = Path(book_info["book_dir"])
    md_file  = book_dir / "ebook.md"

    result = {
        "slug":           slug,
        "title":          book_info["title"],
        "language":       book_info["language"],
        "kdp_book_id":    book_info["kdp_book_id"],
        "status":         book_info["status"],
        "book_dir":       str(book_dir),
        "audited_at":     datetime.now().strftime("%Y-%m-%d %H:%M"),
        # File locations
        "original_md":    None,
        "corrected_md":   None,
        "original_pdf":   None,
        "corrected_pdf":  None,
        "original_epub":  None,
        "corrected_epub": None,
        # QA
        "qa_issues":      [],
        "issues_found":   False,
        "all_auto_fixed": False,
        "corrected":      False,
        # Action
        "action":         "PASS",   # PASS | REPLACE | MANUAL_REVIEW | NO_SOURCE
        "notes":          [],
        "error":          None,
    }

    # ── Check source markdown ──────────────────────────────────────────────────
    if not md_file.exists():
        result["action"] = "NO_SOURCE"
        result["notes"].append("ebook.md missing — cannot audit or correct.")
        return result

    result["original_md"] = str(md_file)

    pdfs  = sorted(book_dir.glob("*paperback*.pdf"))
    epubs = sorted(book_dir.glob("*.epub"))
    result["original_pdf"]  = str(pdfs[0])  if pdfs  else None
    result["original_epub"] = str(epubs[0]) if epubs else None

    # ── Run QA ────────────────────────────────────────────────────────────────
    try:
        content     = md_file.read_text(encoding="utf-8")
        cleaned     = md_cleaner.clean(content)
        fixed, rpt  = book_validator.validate_and_fix(cleaned)

        result["qa_issues"] = [
            {"severity": i.severity, "message": i.message, "fixed": i.fix_applied}
            for i in rpt.issues
        ]
        result["issues_found"]   = bool(rpt.issues)
        result["all_auto_fixed"] = rpt.passed and any(i.fix_applied for i in rpt.issues)

        if not rpt.issues:
            result["action"] = "PASS"
            result["notes"].append("No structural issues found. Book is clean.")
            return result

        # ── Issues found — save corrected files ───────────────────────────────
        audit_dir     = book_dir / "audit"
        corrected_dir = audit_dir / "corrected"
        backup_dir    = audit_dir / "original_backup"
        audit_dir.mkdir(exist_ok=True)
        corrected_dir.mkdir(exist_ok=True)
        backup_dir.mkdir(exist_ok=True)

        # Per-book QA text report
        (audit_dir / "qa_report.txt").write_text(rpt.summary(), encoding="utf-8")

        # Backup originals (never overwrite existing backups)
        _backup(md_file,         backup_dir / "ebook.md.bak")
        if pdfs:  _backup(pdfs[0],  backup_dir / (pdfs[0].name  + ".bak"))
        if epubs: _backup(epubs[0], backup_dir / (epubs[0].name + ".bak"))

        # Save corrected markdown
        corrected_md = corrected_dir / "ebook.md"
        corrected_md.write_text(fixed, encoding="utf-8")
        result["corrected_md"] = str(corrected_md)

        if rpt.passed:
            result["action"] = "REPLACE"
            result["notes"].append("All issues were auto-fixed. Corrected files generated.")
        else:
            result["action"] = "MANUAL_REVIEW"
            result["notes"].append("Some issues could not be auto-fixed. Manual edit needed before replacement.")

        # Generate corrected EPUB (always attempt — needed for KDP upload)
        corrected_epub = _build_epub(book_info, corrected_md, corrected_dir)
        if corrected_epub:
            result["corrected_epub"] = str(corrected_epub)
        else:
            result["notes"].append("WARNING: corrected EPUB generation failed. Check pandoc/metadata.")
            if result["action"] == "REPLACE":
                result["action"] = "MANUAL_REVIEW"

        # Generate corrected PDF (for human review before approval)
        corrected_pdf = _build_pdf(book_info, fixed, corrected_dir, book_dir)
        if corrected_pdf:
            result["corrected_pdf"]  = str(corrected_pdf)
            result["corrected"]      = True
        else:
            result["notes"].append("WARNING: corrected PDF generation failed (non-blocking).")

    except Exception as e:
        result["action"] = "MANUAL_REVIEW"
        result["error"]  = str(e)
        result["notes"].append(f"Audit error: {e}")

    return result


def _backup(src: Path, dst: Path):
    """Copy src → dst only if dst does not already exist."""
    if src.exists() and not dst.exists():
        shutil.copy2(str(src), str(dst))


def _build_epub(book_info, corrected_md: Path, output_dir: Path):
    """Generate EPUB from corrected markdown using pandoc."""
    book_dir  = Path(book_info["book_dir"])
    meta_file = book_dir / "metadata.yaml"
    if not meta_file.exists():
        return None
    epub_out = output_dir / "corrected.epub"
    try:
        r = subprocess.run(
            ["pandoc", str(corrected_md), "-f", "markdown-yaml_metadata_block",
             "-o", str(epub_out),
             "--resource-path", str(book_dir),
             "--metadata-file", str(meta_file),
             "--toc", "--toc-depth=2"],
            capture_output=True, text=True, timeout=90,
        )
        return epub_out if r.returncode == 0 and epub_out.exists() else None
    except Exception:
        return None


def _build_pdf(book_info, fixed_content: str, output_dir: Path, book_dir: Path):
    """Generate corrected paperback PDF using pandoc + xelatex."""
    meta_file   = book_dir / "metadata.yaml"
    header_file = output_dir / "_header.tex"
    if not meta_file.exists():
        return None

    _ensure_header(header_file)

    title    = book_info["title"]
    subtitle = book_info.get("subtitle", "")
    safe     = re.sub(r'[^\w\-]', '-', title)[:60]
    pdf_out  = output_dir / f"{safe}-corrected-paperback.pdf"

    # URL wrapping
    content = re.sub(r'(?<![(<\[])(?<!\]\()https?://[^\s\)>\]]+', r'<\g<0>>', fixed_content)

    # Copyright block
    year = datetime.now().strftime('%Y')
    cblock = (
        "\\newpage\n\\thispagestyle{empty}\n\n"
        f"*{title}*\n\n"
        + (f"*{subtitle}*\n\n" if subtitle else "")
        + "---\n\n"
        f"Copyright © {year} WK Bui\n\n"
        "All rights reserved. No part of this publication may be reproduced, "
        "distributed, or transmitted in any form or by any means, including "
        "photocopying or electronic methods, without the prior written permission of the author.\n\n"
        "*Disclaimer: This publication is for informational and educational purposes only. "
        "The author and publisher make no representations or warranties regarding the accuracy "
        "or completeness of any information contained herein. "
        "Readers should consult a qualified professional for advice specific to their situation.*\n\n"
        "\\clearpage\n\n"
    )
    content = cblock + content

    tmp_md = output_dir / "_tmp.md"
    tmp_md.write_text(content, encoding="utf-8")
    try:
        r = subprocess.run(
            ["pandoc", str(tmp_md), "-o", str(pdf_out),
             "--pdf-engine=xelatex",
             "--resource-path", str(book_dir),
             "--template", str(LIBRA_DIR / "kdp-template.latex"),
             "--metadata-file", str(meta_file),
             "--toc", "--toc-depth=2",
             "-H", str(header_file),
             "--lua-filter", str(LIBRA_DIR / "break-urls.lua"),
             "--lua-filter", str(LIBRA_DIR / "fix-tables.lua"),
             "-V", "geometry:paperwidth=6in,paperheight=9in,top=0.75in,bottom=0.75in,inner=0.75in,outer=0.625in",
             "-V", "fontsize=10pt",
             "-V", "mainfont=DejaVu Serif",
             "-V", "sansfont=DejaVu Sans",
             "-V", "monofont=DejaVu Sans Mono",
             "-V", "documentclass=report",
             "-V", "classoption=openany,oneside"],
            capture_output=True, text=True, timeout=180,
        )
        tmp_md.unlink(missing_ok=True)
        return pdf_out if r.returncode == 0 and pdf_out.exists() else None
    except Exception:
        tmp_md.unlink(missing_ok=True)
        return None


def _ensure_header(header_file: Path):
    """Write the shared LaTeX header for PDF generation (idempotent)."""
    header_file.write_text(r"""
\usepackage{tabularx,booktabs,adjustbox,float,longtable,array,graphicx,ragged2e,titlesec}
\usepackage{url,xurl}
\urlstyle{same}
\Urlmuskip=0mu plus 3mu
\AtBeginDocument{\RaggedRight}
\microtypesetup{protrusion=true,expansion=false}
\hyphenpenalty=900
\tolerance=2000
\sloppy
\emergencystretch=3em
\hbadness=10000
\vbadness=10000
\setlength{\hfuzz}{5pt}
\overfullrule=0pt
\makeatletter
\g@addto@macro\UrlBreaks{\UrlOrds}
\makeatother
\PassOptionsToPackage{breaklinks=true}{hyperref}
\raggedbottom
\let\oldlongtable\longtable
\renewcommand{\longtable}{\scriptsize\oldlongtable}
\setlength{\tabcolsep}{2pt}
\renewcommand{\arraystretch}{1.28}
\newcolumntype{L}[1]{>{\raggedright\arraybackslash}p{#1}}
\newcolumntype{C}[1]{>{\centering\arraybackslash}p{#1}}
\makeatletter
\def\maxwidth{\ifdim\Gin@nat@width>\linewidth\linewidth\else\Gin@nat@width\fi}
\makeatother
\setkeys{Gin}{width=0.72\linewidth,keepaspectratio}
\usepackage[font=small,labelfont=it,labelformat=empty,justification=centering]{caption}
\floatplacement{figure}{H}
\setlength{\parskip}{0.5em}
\setlength{\parindent}{0pt}
\titleformat{\section}
  {\normalfont\Large\bfseries\RaggedRight}{\thesection}{1em}{}
\titleformat{\subsection}
  {\normalfont\large\bfseries\RaggedRight}{\thesubsection}{1em}{}
\titleformat{\subsubsection}
  {\normalfont\normalsize\bfseries\RaggedRight}{\thesubsubsection}{1em}{}
\widowpenalty=10000
\clubpenalty=10000
\usepackage{needspace,etoolbox}
\preto\section{\needspace{6\baselineskip}}
\preto\subsection{\needspace{5\baselineskip}}
\preto\subsubsection{\needspace{4\baselineskip}}
\makeatletter
\def\@makechapterhead#1{%
  \vspace*{20\p@}%
  {\parindent \z@ \raggedright \normalfont \huge \bfseries #1\par\nobreak \vskip 24\p@}}
\def\@makeschapterhead#1{%
  \vspace*{20\p@}%
  {\parindent \z@ \raggedright \normalfont \huge \bfseries #1\par\nobreak \vskip 24\p@}}
\makeatother
""")


# ── HTML report ────────────────────────────────────────────────────────────────

def generate_html_report(results, summary):
    """Return a standalone HTML string for the audit dashboard."""
    rows = ""
    for r in results:
        action = r["action"]
        color = {
            "PASS":          "#2ecc71",
            "REPLACE":       "#e67e22",
            "MANUAL_REVIEW": "#e74c3c",
            "NO_SOURCE":     "#95a5a6",
        }.get(action, "#bdc3c7")

        issues_html = ""
        for i in r.get("qa_issues", []):
            icon = "✗" if i["severity"] == "error" else "⚠"
            fixed_tag = " <small style='color:#27ae60'>[auto-fixed]</small>" if i["fixed"] else ""
            issues_html += f"<li>{icon} {i['message']}{fixed_tag}</li>"
        if not issues_html:
            issues_html = "<li style='color:#27ae60'>No issues found ✓</li>"

        corrected_links = ""
        if r.get("corrected_pdf"):
            corrected_links += f"<br><small>📄 PDF: {r['corrected_pdf']}</small>"
        if r.get("corrected_epub"):
            corrected_links += f"<br><small>📚 EPUB: {r['corrected_epub']}</small>"

        rows += f"""
        <tr>
          <td><strong>{r['title']}</strong><br><small>{r['slug']}</small></td>
          <td>{r.get('language','')}</td>
          <td><code>{r.get('kdp_book_id','—')}</code></td>
          <td><span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">{action}</span></td>
          <td><ul style="margin:0;padding-left:18px">{issues_html}</ul>{corrected_links}</td>
          <td><small>{' | '.join(r.get('notes', []))}</small></td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>KDP Live Audit — {summary['generated_at']}</title>
  <style>
    body{{font-family:sans-serif;max-width:1400px;margin:40px auto;padding:0 20px;background:#f5f5f5}}
    h1{{color:#2c3e50}} .stats{{display:flex;gap:20px;margin:20px 0}}
    .stat{{background:#fff;border-radius:8px;padding:16px 24px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
    .stat .num{{font-size:32px;font-weight:bold}} .stat .lbl{{color:#666;font-size:13px}}
    table{{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
    th{{background:#2c3e50;color:#fff;padding:10px 14px;text-align:left;font-size:13px}}
    td{{padding:10px 14px;border-bottom:1px solid #eee;vertical-align:top;font-size:13px}}
    tr:last-child td{{border-bottom:none}} tr:hover td{{background:#fafafa}}
    code{{background:#eee;padding:1px 4px;border-radius:3px;font-size:12px}}
  </style>
</head>
<body>
<h1>📚 KDP Live Book Audit</h1>
<p style="color:#666">Generated: {summary['generated_at']} &nbsp;|&nbsp;
Run <code>python3 kdp_live_replace.py</code> to start the replacement workflow.</p>

<div class="stats">
  <div class="stat"><div class="num">{summary['total']}</div><div class="lbl">Total Audited</div></div>
  <div class="stat"><div class="num" style="color:#2ecc71">{summary['pass']}</div><div class="lbl">Clean (PASS)</div></div>
  <div class="stat"><div class="num" style="color:#e67e22">{summary['replace']}</div><div class="lbl">Auto-Fixed (REPLACE)</div></div>
  <div class="stat"><div class="num" style="color:#e74c3c">{summary['manual_review']}</div><div class="lbl">Manual Review</div></div>
  <div class="stat"><div class="num" style="color:#95a5a6">{summary['no_source']}</div><div class="lbl">No Source</div></div>
</div>

<table>
  <thead>
    <tr><th>Book</th><th>Language</th><th>KDP Book ID</th><th>Action</th><th>QA Issues</th><th>Notes</th></tr>
  </thead>
  <tbody>{rows}</tbody>
</table>
</body>
</html>"""


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    slug_filter = None
    if "--slug" in sys.argv:
        idx = sys.argv.index("--slug")
        if idx + 1 < len(sys.argv):
            slug_filter = sys.argv[idx + 1]

    print(f"=== KDP Live Audit === {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    books = get_uploaded_books(slug_filter)
    if not books:
        print("No uploaded/live books found.")
        return

    print(f"Found {len(books)} book(s) to audit.\n")
    results = []

    for i, book in enumerate(books, 1):
        print(f"[{i}/{len(books)}] {book['title']} ({book['slug']})")
        r = audit_book(book)
        results.append(r)
        action_icon = {"PASS": "✓", "REPLACE": "→", "MANUAL_REVIEW": "⚠", "NO_SOURCE": "✗"}.get(r["action"], "?")
        print(f"  {action_icon} {r['action']}  {' | '.join(r['notes'])}")
        if r["error"]:
            print(f"  ERROR: {r['error']}")
        print()

    # Summary
    summary = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total":         len(results),
        "pass":          sum(1 for r in results if r["action"] == "PASS"),
        "replace":       sum(1 for r in results if r["action"] == "REPLACE"),
        "manual_review": sum(1 for r in results if r["action"] == "MANUAL_REVIEW"),
        "no_source":     sum(1 for r in results if r["action"] == "NO_SOURCE"),
    }

    # Save JSON report
    report = {"summary": summary, "books": results}
    report_json = LOGS_DIR / "audit_report.json"
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report saved: {report_json}")

    # Save HTML report
    report_html = LOGS_DIR / "audit_report.html"
    report_html.write_text(generate_html_report(results, summary), encoding="utf-8")
    print(f"Dashboard:    {report_html}")

    # Print summary
    print(f"\n{'='*50}")
    print(f"  Total:         {summary['total']}")
    print(f"  PASS:          {summary['pass']}")
    print(f"  REPLACE:       {summary['replace']}  ← run kdp_live_replace.py")
    print(f"  MANUAL REVIEW: {summary['manual_review']}")
    print(f"  NO SOURCE:     {summary['no_source']}")
    print(f"{'='*50}")
    print(f"\nNext step: python3 /root/libra/kdp_live_replace.py")


if __name__ == "__main__":
    main()
