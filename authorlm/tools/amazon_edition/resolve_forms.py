"""Collapse AuthorLM pending-change forms in a markdown file for the Amazon copy.

  ~~<<old>>~~{{new}}  -> new      (a staged replacement; the author has not ruled)
  {{new}}             -> new      (a staged insertion)
  [Omit: ...] ... [/Omit]  -> removed (working notes the export drops)
  [Explain: ...]      -> removed from the body, written to the log as a reviser instruction
  [Footnote: ...]     -> left in place (a placeholder the reviser must resolve), listed in the log
  leading whitespace before a footnote definition is stripped; bold wrappers in headings are removed

Usage: python3 -I resolve_forms.py (--dry-run|--apply) <logdir> <file.md> [...]
With --dry-run nothing is written but the log. The log, <logdir>/<name>.md, lists every
form with its old and new halves, so a reviser can revert an adoption that reads worse.
"""
import re, sys, pathlib

_IN_FORM = r"(?:(?!<<|\{\{).)*?"
_IN_OLD = r"(?:(?!<<).)*?"
PENDING = re.compile(r"(?:~~)*<<(?P<old>" + _IN_OLD + r")>>(?:~~)*\{\{(?P<new>" + _IN_FORM + r")\}\}", re.DOTALL)
INSERTION = re.compile(r"(?<![>}])\{\{(?P<new>" + _IN_FORM + r")\}\}", re.DOTALL)
OMIT = re.compile(r"\[Omit:[^\]]*\].*?\[/Omit\]\s*", re.DOTALL)
EXPLAIN = re.compile(r"\s?\[Explain:\s*(?P<note>[^\]]*(?:\][^\]]*)*?)\](?=[\s.,;:)]|$)", re.DOTALL)
FOOTNOTE_PH = re.compile(r"\[Footnote:[^\]]*\]")
STRIKE_AT_BREAK = re.compile(r"~~(\\\n|  \n)~~")

def unstrike(s):
    return STRIKE_AT_BREAK.sub(r"\1", s).replace("~~", "")

mode, logdir, *files = sys.argv[1:]
apply = mode == "--apply"
logdir = pathlib.Path(logdir); logdir.mkdir(parents=True, exist_ok=True)
for f in files:
    p = pathlib.Path(f); text = p.read_text(encoding="utf-8")
    log = [f"# Pending forms resolved in {p.name}", "", "Policy: the staged `{{new}}` half was adopted; the `<<old>>` half is the text the author last ratified. A reviser may restore the old half where it reads better. `[Explain: …]` notes from the referee rounds are reviser instructions, removed from the body. `[Footnote: …]` placeholders stay in the body until the reviser resolves them.", ""]
    n = 0
    def rep(m):
        global n; n += 1
        old = unstrike(m.group("old")).strip(); new = m.group("new").strip()
        log.append(f"## Form {n} (replacement)\n\nOLD: {old}\n\nNEW: {new}\n")
        return m.group("new")
    text2 = PENDING.sub(rep, text)
    def ins(m):
        global n; n += 1
        log.append(f"## Form {n} (insertion)\n\nNEW: {m.group('new').strip()}\n")
        return m.group("new")
    text2 = INSERTION.sub(ins, text2)
    omits = OMIT.findall(text2)
    for o in omits: log.append(f"## Omit block removed\n\n{o.strip()[:400]}\n")
    text2 = OMIT.sub("", text2)
    notes = [m.group("note").strip() for m in EXPLAIN.finditer(text2)]
    for k, note in enumerate(notes, 1): log.append(f"## Explain note {k} (reviser instruction)\n\n{note}\n")
    text2 = EXPLAIN.sub("", text2)
    phs = FOOTNOTE_PH.findall(text2)
    for ph in phs: log.append(f"## Footnote placeholder left in body\n\n{ph}\n")
    text2 = re.sub(r"(?m)^[ \t]+(\[\^[A-Za-z0-9-]+\]:)", r"\1", text2)
    text2 = re.sub(r"(?m)^(#{1,6})\s+\*\*(.+?)\*\*\s*$", r"\1 \2", text2)
    text2 = re.sub(r"(?m)[ \t]+$", "", text2)
    text2 = re.sub(r"\n{3,}", "\n\n", text2)
    left = re.findall(r"<<|>>|\{\{|\}\}", text2)
    log.append(f"\nSummary: {n} forms, {len(omits)} omit blocks, {len(notes)} explain notes, {len(phs)} footnote placeholders, leftover markers: {len(left)}")
    (logdir / p.name).write_text("\n".join(log) + "\n", encoding="utf-8")
    print(f"{p.name:26s} forms={n:3d} omit={len(omits)} explain={len(notes)} fnph={len(phs)} leftover={len(left)} {'APPLIED' if apply else 'dry-run'}")
    if apply: p.write_text(text2, encoding="utf-8")
