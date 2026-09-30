"""Deterministic prose lint — the checks a harness CAN make without a model.

Every check here encodes a ratified law or a recurring author verdict
(review 2026-09-06 over 69 recorded beat rejections): sentences opening
with "What", "this not that" distinctions, a term inside its own
definition, stacked em-dashes, over-long sentences, reading grade,
verbatim overlap with prose the book already has, and a paragraph that
re-defines a concept another essay introduced. None of them needs a model
and none of them is a judgment: a finding names the sentence and the law,
and the author (or the critic) decides.

Severity is the only policy this module carries:

- `error`   — a law the text breaks mechanically; `write propose` refuses
              unless overridden with a reason (the override is evidence).
- `warning` — a pattern worth a look that legitimate prose also produces
              ("X, not Y" is ordinary English; the author writes it).
- `info`    — a measurement (reading grade) the critic can weigh.

No database access: callers pass the corpus and the concept list in.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# ----------------------------------------------------------- data shapes


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str           # error | warning | info
    unit: int               # 1-based paragraph index in the linted text
    quote: str              # the sentence or phrase the finding names
    message: str
    ref: str = ""           # what it points at: a file ¶n, a concept, a law

    def key(self) -> tuple[str, str]:
        """Identity for delta comparison: the same fault on the same
        words is the same finding whichever unit it moved to."""
        return (self.code, _flat(self.quote))


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    def codes(self) -> list[str]:
        return sorted({f.code for f in self.findings})


# --------------------------------------------------------------- helpers

_ABBREV = ("e.g.", "i.e.", "cf.", "etc.", "vs.", "Dr.", "St.", "Mr.",
           "Mrs.", "Ms.", "No.", "ch.", "pp.", "p.", "vol.")
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"'”’)\]]*\s+(?=[\"'“‘(\[]?[A-Z0-9])")
_WORD = re.compile(r"[A-Za-zÀ-ɏḀ-ỿ'’]+")
_DASH = "—"


def _flat(text: str) -> str:
    return " ".join(text.split())


def paragraphs(text: str) -> list[str]:
    """Blank-line separated units, headings included (a heading is a unit
    the checks mostly skip)."""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def is_heading(unit: str) -> bool:
    first = unit.splitlines()[0].strip()
    return first.startswith("#") or (
        first.startswith("**") and first.endswith("**") and "\n" not in unit)


def sentences(paragraph: str) -> list[str]:
    """A tolerant sentence split. Abbreviations are protected by a
    placeholder; a footnote anchor or closing quote after the period
    stays with its sentence."""
    text = _flat(paragraph)
    for abbrev in _ABBREV:
        text = text.replace(abbrev, abbrev.replace(".", "\x00"))
    parts = _SENTENCE_END.split(text)
    out = []
    for part in parts:
        part = part.replace("\x00", ".").strip()
        if part:
            out.append(part)
    return out


def words(text: str) -> list[str]:
    return _WORD.findall(text)


def _syllables(word: str) -> int:
    w = word.lower().strip("'’")
    if not w:
        return 0
    if len(w) <= 3:
        return 1
    groups = re.findall(r"[aeiouy]+", w)
    count = len(groups)
    if w.endswith("e") and not w.endswith(("le", "ee", "ye")) and count > 1:
        count -= 1
    if w.endswith("ed") and not w.endswith(("ted", "ded")) and count > 1:
        count -= 1
    return max(1, count)


def reading_grade(paragraph: str) -> float | None:
    """Flesch–Kincaid grade level for one paragraph, or None when there is
    too little text to say anything."""
    sents = sentences(paragraph)
    ws = words(paragraph)
    if len(ws) < 12 or not sents:
        return None
    syl = sum(_syllables(w) for w in ws)
    grade = 0.39 * (len(ws) / len(sents)) + 11.8 * (syl / len(ws)) - 15.59
    return round(grade, 1)


def _normalize_words(text: str) -> list[str]:
    return [w.lower().strip("'’") for w in words(text)]


def shingles(text: str, n: int) -> set[tuple[str, ...]]:
    ws = _normalize_words(text)
    return {tuple(ws[i:i + n]) for i in range(0, max(0, len(ws) - n + 1))}


def sha(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- checks

_WHAT_OPENER = re.compile(r"^[\"'“‘(\[]*What\b")
_NOT_BUT = re.compile(
    r"\bnot\b(?!\s+(?:only|just|merely|simply)\b)"
    r"(?P<x>[^.;:?!]{1,70}?)"
    r",?\s+\bbut\b(?!\s+(?:also|rather)\b)")
_X_NOT_Y = re.compile(r",\s*(?:and\s+)?not\s+(?:a|an|the|its|their|his|her)?"
                      r"\s*[\w' -]{1,40}[.;]")
_DEFINITION = re.compile(
    r"^(?:A|An|The)\s+(?P<term>[A-Za-z][\w'’-]*(?:\s+[A-Za-z][\w'’-]*){0,2})"
    r"\s+(?:is|are|means|names|denotes)\b(?P<rest>.*)$")


def check_what_opener(units: list[str]) -> list[Finding]:
    out = []
    for i, unit in enumerate(units, 1):
        if is_heading(unit):
            continue
        for s in sentences(unit):
            if _WHAT_OPENER.match(s):
                out.append(Finding(
                    "what-opener", "error", i, s,
                    "sentence opens with 'What' (law: don't start "
                    "sentences with the word 'what')"))
    return out


def check_this_not_that(units: list[str]) -> list[Finding]:
    out = []
    for i, unit in enumerate(units, 1):
        if is_heading(unit):
            continue
        for s in sentences(unit):
            if _NOT_BUT.search(s):
                out.append(Finding(
                    "this-not-that", "warning", i, s,
                    "'not X but Y' shape (law: state each side on its own "
                    "terms, no 'this not that' language) — keep only if "
                    "both sides are stated in full"))
            elif _X_NOT_Y.search(s):
                out.append(Finding(
                    "this-not-that", "info", i, s,
                    "'X, not Y' tail — ordinary English; check it is not "
                    "carrying a distinction the law says to spell out"))
    return out


def check_term_in_definition(units: list[str]) -> list[Finding]:
    out = []
    for i, unit in enumerate(units, 1):
        if is_heading(unit):
            continue
        for s in sentences(unit):
            m = _DEFINITION.match(s)
            if not m:
                continue
            term = m.group("term").strip()
            rest = m.group("rest")
            pattern = re.compile(rf"\b{re.escape(term)}s?\b", re.IGNORECASE)
            if pattern.search(rest):
                out.append(Finding(
                    "term-in-definition", "error", i, s,
                    f"'{term}' appears inside its own definition (law: "
                    f"don't use the term in its definition)", ref=term))
    return out


def check_em_dashes(units: list[str]) -> list[Finding]:
    out = []
    for i, unit in enumerate(units, 1):
        if is_heading(unit):
            continue
        for s in sentences(unit):
            n = s.count(_DASH) + s.count(" -- ")
            if n >= 3:
                out.append(Finding(
                    "em-dash-stack", "error", i, s,
                    f"{n} em-dashes in one sentence (law: never stack "
                    f"multiple em-dash pairs; a parenthetical usually "
                    f"becomes its own sentence)"))
            elif n >= 1:
                out.append(Finding(
                    "em-dash", "info", i, s,
                    "em-dash present (law: use sparingly)"))
    return out


def check_sentence_length(units: list[str], limit: int = 40) -> list[Finding]:
    out = []
    for i, unit in enumerate(units, 1):
        if is_heading(unit):
            continue
        for s in sentences(unit):
            n = len(words(s))
            if n > limit:
                out.append(Finding(
                    "long-sentence", "warning", i, s,
                    f"{n} words (law: prefer short sentences; break long "
                    f"periodic constructions)"))
    return out


def check_reading_grade(units: list[str], max_grade: float = 13.0
                        ) -> list[Finding]:
    out = []
    for i, unit in enumerate(units, 1):
        if is_heading(unit):
            continue
        grade = reading_grade(unit)
        if grade is None:
            continue
        severity = "warning" if grade > max_grade else "info"
        out.append(Finding(
            "reading-grade", severity, i, _flat(unit)[:80],
            f"reading grade {grade}" + (
                f" — above the {max_grade:g} ceiling; shorter sentences "
                f"and plainer words bring it down" if severity == "warning"
                else "")))
    return out


def check_overlap(units: list[str], corpus: dict[str, list[str]],
                  n: int = 8) -> list[Finding]:
    """Verbatim n-word overlap between each unit and the corpus, which maps
    a label (file) to its paragraphs. One finding per (unit, file ¶k)
    pair, quoting the longest matched run.

    Eight words is long enough that a match is a sentence the book
    already has, not a shared idiom; a refrain the author repeats on
    purpose is exactly what the override reason records."""
    index: dict[tuple[str, ...], list[tuple[str, int]]] = {}
    for label, paras in corpus.items():
        for k, para in enumerate(paras, 1):
            for sh in shingles(para, n):
                index.setdefault(sh, []).append((label, k))
    out = []
    for i, unit in enumerate(units, 1):
        ws = _normalize_words(unit)
        hits: dict[tuple[str, int], list[int]] = {}
        for pos in range(0, max(0, len(ws) - n + 1)):
            sh = tuple(ws[pos:pos + n])
            for where in index.get(sh, ()):
                hits.setdefault(where, []).append(pos)
        for (label, k), positions in sorted(hits.items()):
            # the longest contiguous run of matching shingles → phrase
            positions.sort()
            best_start, best_len, run_start, run_len = positions[0], 1, positions[0], 1
            for prev, cur in zip(positions, positions[1:]):
                if cur == prev + 1:
                    run_len += 1
                else:
                    run_start, run_len = cur, 1
                if run_len > best_len:
                    best_start, best_len = run_start, run_len
            phrase = " ".join(ws[best_start:best_start + n + best_len - 1])
            out.append(Finding(
                "overlap", "error", i, phrase,
                f"{n + best_len - 1} words verbatim from {label} ¶{k} — "
                f"the book already says this; cite it or cut it",
                ref=f"{label} ¶{k}"))
    return out


def check_redefinition(units: list[str], concepts: list[tuple[str, str]],
                       this_file: str | None = None) -> list[Finding]:
    """A definitional sentence whose subject is a concept another essay
    introduced. `concepts` is [(name, introduced_in), …]."""
    out = []
    subjects = [(name, where) for name, where in concepts
                if name and where and where != this_file]
    for i, unit in enumerate(units, 1):
        if is_heading(unit):
            continue
        for s in sentences(unit):
            m = _DEFINITION.match(s)
            if not m:
                continue
            term = m.group("term").strip().lower()
            for name, where in subjects:
                if term == name.lower() or term == name.lower() + "s":
                    out.append(Finding(
                        "redefinition", "warning", i, s,
                        f"reads as a definition of '{name}', which "
                        f"{where} introduced — build on it, don't "
                        f"re-introduce it", ref=f"{name} ← {where}"))
    return out


# ------------------------------------------------------------ the entry

def lint_text(text: str, *, corpus: dict[str, list[str]] | None = None,
              concepts: list[tuple[str, str]] | None = None,
              this_file: str | None = None, max_grade: float = 13.0,
              long_sentence: int = 40, overlap_n: int = 8) -> Report:
    units = paragraphs(text)
    findings: list[Finding] = []
    findings += check_what_opener(units)
    findings += check_this_not_that(units)
    findings += check_term_in_definition(units)
    findings += check_em_dashes(units)
    findings += check_sentence_length(units, long_sentence)
    findings += check_reading_grade(units, max_grade)
    if corpus:
        findings += check_overlap(units, corpus, overlap_n)
    if concepts:
        findings += check_redefinition(units, concepts, this_file)
    findings.sort(key=lambda f: (f.unit, {"error": 0, "warning": 1,
                                          "info": 2}[f.severity], f.code))
    return Report(findings)


def delta(old: Report, new: Report) -> Report:
    """Findings the new text has that the old text did not — what an edit
    INTRODUCED, which is the only thing a replacement is answerable for."""
    seen = {f.key() for f in old.findings}
    return Report([f for f in new.findings if f.key() not in seen])


def corpus_from_files(files: dict[str, str], exclude: str | None = None
                      ) -> dict[str, list[str]]:
    """{file: paragraphs} for overlap checks. `exclude` drops one file —
    the one being edited, whose own text is not "the book already says
    this"."""
    return {name: paragraphs(text) for name, text in sorted(files.items())
            if name != exclude}


def render(report: Report, heading: str = "LINT") -> str:
    """The report as text for a payload or a terminal. Info lines are
    folded to one summary per code so the block stays short."""
    lines = [heading]
    if not report.findings:
        lines.append("(clean)")
        return "\n".join(lines)
    for f in report.findings:
        if f.severity == "info" and f.code != "reading-grade":
            continue
        tag = {"error": "ERROR", "warning": "warn", "info": "info"}[f.severity]
        quote = _flat(f.quote)
        if len(quote) > 160:
            quote = quote[:157] + "…"
        lines.append(f"- [{tag}] ¶{f.unit} {f.code}: {f.message}")
        lines.append(f"    «{quote}»")
    folded = [f for f in report.findings
              if f.severity == "info" and f.code != "reading-grade"]
    if folded:
        counts: dict[str, int] = {}
        for f in folded:
            counts[f.code] = counts.get(f.code, 0) + 1
        lines.append("- [info] " + ", ".join(
            f"{code} ×{n}" for code, n in sorted(counts.items())))
    return "\n".join(lines)


# ------------------------------------------------- the marked view

_TOKEN = re.compile(r"\s+|[^\s]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def _strip_bold(text: str) -> str:
    return text.replace("**", "")


def _match_paragraph(unit: str, originals: list[str], floor: float = 0.35
                     ) -> int | None:
    import difflib

    best, best_ratio = None, floor
    a = _normalize_words(unit)
    for k, para in enumerate(originals):
        b = _normalize_words(para)
        if not b:
            continue
        ratio = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
        if ratio > best_ratio:
            best, best_ratio = k, ratio
    return best


def mark_changes(draft: str, original: str) -> str:
    """The draft with every span that differs from the original in bold.

    Each draft paragraph is paired with the original paragraph it most
    resembles (word-sequence similarity above a floor); the words the
    draft adds or changes are wrapped in `**`, unchanged words are left
    plain, and a paragraph with no counterpart is bold whole — new
    material. The draft's own bold markup is removed first, so the marks
    mean one thing. Headings are compared like any other unit.

    Presentation only: nothing here touches the manuscript."""
    import difflib

    originals = [_strip_bold(p) for p in paragraphs(original)]
    out = []
    for unit in paragraphs(draft):
        unit = _strip_bold(unit)
        k = _match_paragraph(unit, originals)
        if k is None:
            out.append(f"**{unit}**")
            continue
        a = _tokens(unit)
        b = _tokens(originals[k])
        a_words = [t.lower().strip(".,;:!?\"'“”‘’()") if not t.isspace() else t
                   for t in a]
        b_words = [t.lower().strip(".,;:!?\"'“”‘’()") if not t.isspace() else t
                   for t in b]
        matcher = difflib.SequenceMatcher(None, a_words, b_words,
                                          autojunk=False)
        pieces: list[tuple[bool, str]] = []
        for op, i1, i2, _j1, _j2 in matcher.get_opcodes():
            chunk = "".join(a[i1:i2])
            if not chunk:
                continue
            pieces.append((op != "equal", chunk))
        # merge runs; whitespace-only changed chunks join their neighbour
        merged: list[tuple[bool, str]] = []
        for changed, chunk in pieces:
            if chunk.isspace() and merged:
                merged[-1] = (merged[-1][0], merged[-1][1] + chunk)
                continue
            if merged and merged[-1][0] == changed:
                merged[-1] = (changed, merged[-1][1] + chunk)
            else:
                merged.append((changed, chunk))
        rendered = []
        for changed, chunk in merged:
            if changed and chunk.strip():
                lead = chunk[:len(chunk) - len(chunk.lstrip())]
                trail = chunk[len(chunk.rstrip()):]
                rendered.append(f"{lead}**{chunk.strip()}**{trail}")
            else:
                rendered.append(chunk)
        out.append("".join(rendered))
    return "\n\n".join(out)


def marked_html(marked: str) -> str:
    """The marked view as an HTML fragment: `**…**` spans become
    `<span class="chg">…</span>`, paragraphs `<p>`, a `#` heading `<h1>`.
    No styling here; the presenting surface supplies the palette."""
    import html as _html

    out = []
    for unit in paragraphs(marked):
        text = unit
        level = 0
        while text.startswith("#"):
            level += 1
            text = text[1:]
        text = text.strip()
        escaped = _html.escape(text)
        parts = escaped.split("**")
        rendered = "".join(
            f'<span class="chg">{part}</span>' if i % 2 else part
            for i, part in enumerate(parts))
        tag = f"h{min(level, 3)}" if level else "p"
        out.append(f"<{tag}>{rendered}</{tag}>")
    return "\n".join(out)


def best_match(unit: str, candidates: list[str]) -> tuple[int | None, float]:
    """(index, ratio) of the candidate paragraph most like `unit` by word
    sequence, or (None, 0.0) when nothing resembles it at all."""
    import difflib

    a = _normalize_words(_strip_bold(unit))
    best, best_ratio = None, 0.0
    for k, para in enumerate(candidates):
        b = _normalize_words(_strip_bold(para))
        if not a or not b:
            continue
        ratio = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
        if ratio > best_ratio:
            best, best_ratio = k, ratio
    return best, round(best_ratio, 3)


def changed_only(marked: str) -> tuple[list[str], int]:
    """From a marked view, the paragraphs that carry a change (bold span),
    and the count of unchanged ones left out. Headings are kept only when
    they changed."""
    units = paragraphs(marked)
    kept = [u for u in units if "**" in u]
    return kept, len(units) - len(kept)
