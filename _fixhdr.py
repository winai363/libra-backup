import re

src = open('/root/libra/app.py').read()

# Find the header_file.write_text(r"""  ...  """) block and rebuild it cleanly.
start_marker = 'header_file.write_text(r"""'
end_marker = '""")'
i = src.find(start_marker)
assert i != -1, "start not found"
j = src.find(end_marker, i)
assert j != -1, "end not found"

clean_header = r'''header_file.write_text(r"""
\usepackage{tabularx}
\usepackage{booktabs}
\usepackage{adjustbox}
\usepackage{float}
\usepackage{longtable}
\usepackage{array}
\usepackage{graphicx}

% --- URL line breaking (prevent overflow) ---
\usepackage{url}
\usepackage{xurl}
\urlstyle{same}
\Urlmuskip=0mu plus 3mu

% --- Full justification: both left AND right edges flush (book standard) ---
% NO \raggedright in the body. microtype (character protrusion + font expansion)
% is the key to justified text WITHOUT ugly word-gaps/rivers: it micro-adjusts
% spacing so both edges stay flush while gaps between words stay tight and even.
\usepackage[protrusion=true,expansion=true]{microtype}

% Moderate hyphenation: REQUIRED so justified text has no big gaps. Without it,
% justified text produces wide rivers. Allowed but strongly discouraged here
% (very few hyphens, no rivers, no broken-looking words at the margin).
\hyphenpenalty=900
\tolerance=2000
\sloppy
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
\renewcommand{\longtable}{\small\oldlongtable}
\setlength{\tabcolsep}{3pt}
\renewcommand{\arraystretch}{1.2}
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

% --- Prevent orphans/widows ---
\widowpenalty=10000
\clubpenalty=10000

% --- Prevent orphaned headings (heading alone at page bottom) ---
\usepackage{needspace}
\usepackage{etoolbox}
\preto\section{\needspace{6\baselineskip}}
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

""")'''

new_src = src[:i] + clean_header + src[j+len(end_marker):]
open('/root/libra/app.py','w').write(new_src)

# Report counts
import collections
hdr = new_src[new_src.find(start_marker):new_src.find(end_marker, new_src.find(start_marker))]
out = []
for key in ['microtype', 'labelformat=empty', 'floatplacement', r'setkeys{Gin}', 'hyphenpenalty=900', 'hyphenpenalty=10000', 'raggedright}']:
    out.append(f"{key}: {hdr.count(key)}")
open('/root/libra/_fixreport.txt','w').write('\n'.join(out))
print("done")
