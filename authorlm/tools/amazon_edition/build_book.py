"""Assemble the Amazon edition of SMSTTD and build print PDF (via HTML), docx and epub.

python3 -I build_book.py <bookdir> <outdir> <order.txt> [--front <frontdir>] [--keep-illustrations]

order.txt lines: `file.md [flags]`; flags: cont (run on from the previous file: no page break,
same notes block), demote (headings one level down), toc (list in the contents even if the
file has no # heading), notoc (keep out of the contents). `#` comments allowed.
Front matter: <frontdir>/halftitle.html, title.html, copyright.html, dedication.html,
epigraph.html are inserted before the contents if present (print and epub only; the docx
takes title.md as its first page).

Print road: every file is converted on its own to an HTML fragment, so each essay carries its
own notes under a "Notes" heading at its end and note numbers restart per essay. Fragments
are joined with page breaks, wrapped with print.css, and rendered by make_pdf.js.
Docx and epub roads: one pandoc run over the joined markdown, with footnote labels prefixed
per file so they stay unique; docx gets real footnotes, epub gets end-of-chapter notes.
"""
import re, sys, subprocess, pathlib, html, json

args = sys.argv[1:]
keep_ill = "--keep-illustrations" in args
if keep_ill: args.remove("--keep-illustrations")
frontdir = None
if "--front" in args:
    i = args.index("--front"); frontdir = pathlib.Path(args[i + 1]); del args[i:i + 2]
bookdir, outdir, orderfile = map(pathlib.Path, args[:3])
outdir.mkdir(parents=True, exist_ok=True)
TOOLS = pathlib.Path(__file__).parent
TITLE = "Seven More Sermons To The Dead"; AUTHOR = "Chinmay Nagarkar"

VOICE = re.compile(r"^\[Voice:[^\]]*\]\s*$", re.M)
ILL = re.compile(r"^\[Illustration:\s*(?P<body>[^\]]*)\]\s*$", re.M)
OMIT = re.compile(r"\[Omit:[^\]]*\].*?\[/Omit\]\s*", re.S)
FN = re.compile(r"\[\^([A-Za-z0-9-]+)\]")

def render_ill(m):
    if not keep_ill: return ""
    body = m.group("body"); cap = re.search(r"caption:\s*(?P<c>[^|]*)", body)
    c = cap.group("c").strip() if cap else body.split("⇢")[0].strip()
    return f"\n*[Plate: {html.escape(c)}]*\n"

def clean(text, demote):
    text = OMIT.sub("", text); text = VOICE.sub("", text); text = ILL.sub(render_ill, text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    if demote: text = re.sub(r"(?m)^(#{1,5}) ", r"#\1 ", text)
    text = re.sub(r"(?m)[ \t]+$", "", text); text = re.sub(r"\n{3,}", "\n\n", text).strip("\n") + "\n"
    return text

def pandoc(src, fmt, extra=()):
    r = subprocess.run(["pandoc", "-f", "markdown+smart+footnotes+raw_attribute+fenced_divs", "-t", fmt, *extra], input=src, capture_output=True, text=True)
    if r.returncode: print("pandoc error:", r.stderr[:500])
    return r.stdout

entries = []
for line in orderfile.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#"): continue
    name, *flags = line.split(); entries.append((name, set(flags)))

# ---- group files into units (a unit = a file plus its `cont` followers), one HTML fragment per unit
units = []
for name, flags in entries:
    p = bookdir / name; t = clean(p.read_text(encoding="utf-8"), "demote" in flags)
    if "cont" in flags and units: units[-1]["md"] += "\n\n" + t; units[-1]["names"].append(name)
    else: units.append({"names": [name], "md": t, "flags": flags})

PAGE_BREAK_MD = "\n\n```{=openxml}\n<w:p><w:r><w:br w:type=\"page\"/></w:r></w:p>\n```\n\n"
joined = []; words_rows = []
for i, u in enumerate(units):
    prefix = re.sub(r"[^A-Za-z0-9]", "", pathlib.Path(u["names"][0]).stem)
    md_prefixed = FN.sub(lambda m: f"[^{prefix}-{m.group(1)}]", u["md"])
    joined.append((PAGE_BREAK_MD if i else "") + md_prefixed)
    body = re.sub(r"(?m)^\[\^[^\]]+\]:.*$", "", u["md"])
    words_rows.append((", ".join(u["names"]), len(body.split())))
whole = "".join(joined)
(outdir / "book.md").write_text(whole, encoding="utf-8")
total = sum(w for _, w in words_rows)
for n, w in words_rows: print(f"{n:60s} {w:6d}")
print(f"TOTAL body words ~{total}")
left = re.findall(r"<<|>>|\{\{|\}\}|\[Explain:|\[Footnote:|\[Voice:|\[Illustration:|\[Omit|<!--", whole)
print("leftover markers:", len(left))

# ---- print road: per-unit fragments with their own notes
frags = []; toc = []
for u in units:
    frag = pandoc(u["md"], "html5", ["--mathml"])
    # rename the notes section heading and strip back-links
    frag = frag.replace('<section id="footnotes" class="footnotes footnotes-end-of-document" role="doc-endnotes">\n<hr />', '<section class="footnotes" role="doc-endnotes">\n<h4 class="notes-head">Notes</h4>')
    frag = re.sub(r'<a href="#fnref[^"]*" class="footnote-back" role="doc-backlink">[^<]*</a>', "", frag)
    # make ids unique per unit
    uid = re.sub(r"[^A-Za-z0-9]", "", pathlib.Path(u["names"][0]).stem)
    frag = re.sub(r'(id|href)="(#?)(fn|fnref)(\d+)"', lambda m: f'{m.group(1)}="{m.group(2)}{m.group(3)}-{uid}-{m.group(4)}"', frag)
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", frag, re.S)
    if "notoc" not in u["flags"]:
        if h1:
            frag = frag.replace(h1.group(0), f'<h1 id="u-{uid}">{h1.group(1)}</h1>', 1)
            toc.append((re.sub(r"<[^>]+>", "", h1.group(1)), f"u-{uid}", u["flags"]))
    frags.append(f'<div class="unit" id="unit-{uid}">\n{frag}\n</div>')

front = ""
if frontdir:
    for f in ("halftitle", "title", "copyright", "dedication", "epigraph"):
        fp = frontdir / f"{f}.html"
        if fp.exists(): front += f'<div class="front {f}">\n{fp.read_text(encoding="utf-8")}\n</div>\n'
toc_html = "<nav class=\"toc\"><h1 class=\"toc-title\">Contents</h1><ul>" + "".join(
    f'<li class="{"chapter" if "chapter" in fl else "essay"}"><a href="#{i}">{html.escape(t)}</a></li>' for t, i, fl in toc) + "</ul></nav>"
css = (TOOLS / "print.css").read_text(encoding="utf-8")
doc = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>{html.escape(TITLE)}</title><style>{css}</style></head><body>
{front}{toc_html}
{"".join(frags)}
</body></html>"""
(outdir / "print.html").write_text(doc, encoding="utf-8")
r = subprocess.run(["node", str(TOOLS / "make_pdf.js"), str(outdir / "print.html"), str(outdir / "book.pdf"), TITLE], capture_output=True, text=True)
print("pdf:", r.returncode, (r.stdout + r.stderr).strip()[:300])
try:
    from pypdf import PdfReader; print("pages:", len(PdfReader(str(outdir / "book.pdf")).pages))
except Exception as e: print("page count unavailable:", e)

# ---- docx and epub roads
common = ["-f", "markdown+smart+footnotes+raw_attribute+fenced_divs", "--metadata", f"title={TITLE}", "--metadata", "lang=en-US", "--metadata", f"author={AUTHOR}"]
ref = TOOLS / "reference.docx"
epub_md = whole
if frontdir:
    epub_front = ""
    for f in ("title", "copyright", "dedication", "epigraph"):
        fp = frontdir / f"{f}.md"
        if fp.exists(): epub_front += fp.read_text(encoding="utf-8") + PAGE_BREAK_MD
    epub_md = epub_front + whole
(outdir / "epub.md").write_text(epub_md, encoding="utf-8")
r = subprocess.run(["pandoc", str(outdir / "epub.md"), "-o", str(outdir / "book.docx"), *common, "--toc", "--toc-depth=1"] + (["--reference-doc", str(ref)] if ref.exists() else []), capture_output=True, text=True)
print("docx:", r.returncode, r.stderr.strip()[:300])
css_epub = pathlib.Path("/home/user/authorllm/authorlm/authorlm/publication/epub.css")
r = subprocess.run(["pandoc", str(outdir / "epub.md"), "-o", str(outdir / "book.epub"), *common, "-t", "epub3", "--toc", "--toc-depth=1", "--split-level=1", "--mathml"] + (["--css", str(css_epub)] if css_epub.exists() else []), capture_output=True, text=True)
print("epub:", r.returncode, r.stderr.strip()[:300])
