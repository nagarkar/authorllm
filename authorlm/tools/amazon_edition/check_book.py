"""Mechanical checks over book/*.md for the Amazon edition. Prints one line per finding.

python3 -I check_book.py <bookdir> [file ...]
Checks: leftover pending-form markers and directive tags; footnote references without
definitions and definitions without references (per file); archaic verb forms outside the
Sermons (stale quotations); references to the Appendix or the Mixing paper; the words
refuse/refuses/refusal; em and en dashes (counts); bold in headings; `[Title: …]` notes;
duplicate footnote labels across files; HTML comments; `* *` split italics; lines of
list bullets in essays (allowed only in the Preface's four idols and the Sermons).
"""
import re, sys, pathlib
bookdir = pathlib.Path(sys.argv[1])
files = [bookdir / f for f in sys.argv[2:]] or sorted(bookdir.glob("*.md"))
ARCH = re.compile(r"\b(thou|thy|thee|thine|ye|hath|doth|shalt|wilt|shouldest|makest|seekest|knowest|findest)\b|\b[a-z]{3,}eth\b", re.I)
ARCH_OK = {"beneath", "teeth", "sheth", "nearest", "earnest", "honest", "modest", "forest", "rest", "test", "best", "west", "east", "chest", "quest", "harvest", "interest", "priest", "manifest", "contest", "protest", "request", "suggest", "latest", "greatest", "highest", "lowest", "finest", "largest", "smallest", "farthest", "furthest", "deepest", "oldest", "strongest", "weakest", "richest", "poorest", "simplest", "subtlest", "widest", "fullest", "surest", "purest", "wisest", "truest", "nearest", "dearest", "closest", "fittest", "cruelest", "noblest", "gentlest", "everest", "tempest", "digest", "invest", "arrest", "detest", "attest", "divest", "guest", "nest", "vest", "jest", "zest", "pest", "behest", "bequest", "conquest", "inquest", "sheth", "teeth", "sabbath", "fortieth", "twentieth", "thirtieth", "fiftieth", "hundredth", "beneath", "wreath", "heath", "sheath", "breath", "death", "faith", "earth", "truth", "youth", "worth", "north", "south", "both", "cloth", "moth", "broth", "froth", "sloth", "wrath", "path", "math", "bath", "oath", "booth", "tooth", "smooth", "ruth", "growth", "warmth", "length", "strength", "depth", "width", "fourth", "mouth", "birth", "mirth", "hearth", "girth", "forth", "worth", "elizabeth", "macbeth", "nazareth", "kenneth", "twentieth", "eightieth"}
for p in files:
    t = p.read_text(encoding="utf-8")
    name = p.name
    for m in re.finditer(r"<<|>>|\{\{|\}\}|\[Explain:|\[Footnote:|\[Omit|\[/Omit\]|<!--|\[Title:", t):
        ln = t.count("\n", 0, m.start()) + 1
        print(f"{name}:{ln}: marker {m.group(0)}")
    refs = set(re.findall(r"\[\^([A-Za-z0-9-]+)\](?!:)", t)); defs = re.findall(r"^\[\^([A-Za-z0-9-]+)\]:", t, re.M)
    for d in sorted(set(defs) - refs): print(f"{name}: footnote defined but unused [^{d}]")
    for r in sorted(refs - set(defs)): print(f"{name}: footnote referenced but undefined [^{r}]")
    for d in set(x for x in defs if defs.count(x) > 1): print(f"{name}: footnote defined twice [^{d}]")
    if name != "sermons_new.md":
        for i, line in enumerate(t.split("\n"), 1):
            hits = [m.group(0) for m in ARCH.finditer(line) if m.group(0).lower() not in ARCH_OK]
            if hits: print(f"{name}:{i}: archaic form(s) {hits} :: {line.strip()[:110]}")
    for i, line in enumerate(t.split("\n"), 1):
        if re.search(r"\bAppendix\b|paper on Mixing|Mixing Problem|\(appendix\)", line): print(f"{name}:{i}: appendix reference :: {line.strip()[:120]}")
        if re.search(r"\brefus(e|es|al|ed|ing)\b", line, re.I): print(f"{name}:{i}: refuse-word :: {line.strip()[:120]}")
        if re.match(r"^#{1,6} .*\*\*", line): print(f"{name}:{i}: bold in heading :: {line.strip()}")
        if re.search(r"\*\s+\*|\*\*\s+\*\*", line): print(f"{name}:{i}: split emphasis :: {line.strip()[:120]}")
        if re.match(r"^\s*([-*+]|\d+\.)\s", line) and name not in ("preface.md", "sermons_new.md", "title.md"): print(f"{name}:{i}: list item in body :: {line.strip()[:100]}")
        if re.search(r"Redemption We Must Name", line): print(f"{name}:{i}: old Redemption title :: {line.strip()[:120]}")
    em = t.count("—"); en = t.count("–")
    if em or en: print(f"{name}: dashes em={em} en={en}")
# duplicate labels across files
alld = {}
for p in files:
    for d in re.findall(r"^\[\^([A-Za-z0-9-]+)\]:", p.read_text(encoding="utf-8"), re.M): alld.setdefault(d, []).append(p.name)
dups = {k: v for k, v in alld.items() if len(v) > 1}
print(f"-- labels shared across files: {len(dups)} (prefixed at build time)")
