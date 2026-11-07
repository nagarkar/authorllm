"""Split the Drive connector's text of the SMSTTD master Doc into per-tab markdown files.

Usage: python3 -I split_master.py <master.md> <outdir> [tab ...]
Tabs are H1 lines of the form `# **name.md**` (with stray trailing asterisks).
The connector escapes markdown punctuation with backslashes; those are undone.
Headings lose their bold wrapper. Trailing whitespace and the `**` bold
artifacts between tabs are dropped. Prints a per-tab word count table.
"""
import re, sys, pathlib

src, outdir, *want = sys.argv[1:]
text = pathlib.Path(src).read_text(encoding="utf-8")
lines = text.split("\n")
tab_re = re.compile(r"^# \*\*([A-Za-z0-9_\\./-]+\.md)\*+\s*$")
tabs = []  # (name, start, end)
for i, ln in enumerate(lines):
    m = tab_re.match(ln)
    if m:
        name = m.group(1).replace("\\_", "_")
        if tabs:
            tabs[-1][2] = i
        tabs.append([name, i + 1, len(lines)])

def unescape(s: str) -> str:
    # Google's export escapes these with a backslash.
    return re.sub(r"\\([\\`*_{}\[\]()#+\-.!~<>|\"'$])", r"\1", s)

def clean(body):
    out = []
    for ln in body:
        ln = ln.rstrip()
        if ln in ("**", "*", "***", "****"):
            continue
        # headings: strip bold wrapper  "## **Title**" -> "## Title"
        m = re.match(r"^(#{1,6})\s+\*\*(.+?)\*\*\s*$", ln)
        if m:
            ln = f"{m.group(1)} {m.group(2)}"
        ln = unescape(ln)
        out.append(ln)
    # collapse 3+ blank lines to 1 blank line
    s = "\n".join(out)
    s = re.sub(r"\n{3,}", "\n\n", s).strip("\n") + "\n"
    return s

od = pathlib.Path(outdir)
od.mkdir(parents=True, exist_ok=True)
rows = []
for name, a, b in tabs:
    if want and name not in want:
        continue
    body = clean(lines[a:b])
    target = od / name.replace("/", "__")
    target.write_text(body, encoding="utf-8")
    rows.append((name, len(body.split()), body.count("\n")))
for name, w, l in rows:
    print(f"{name:34s} {w:7d} words {l:6d} lines")
