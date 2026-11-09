"""Terms: recognising a manuscript's terms of art and finding where they occur.

One home for the term rules that concept extraction and the developmental
edit both read: how a name matches text (`concept_pattern`, `mention_pattern`),
which capitalised words are candidate terms (`capitalised_terms`), whether a
capitalised word is a term or only a sentence opening (`Terms.is_term_of_art`),
and whether a sentence defines a name (`grounding_strength`). `Terms.index()`
is the book-wide term index: every recurring term with each place it occurs
and the sentences shaped like its definition.

The index is computed from the text on every call and never stored as truth.
It reads no database and no concept graph: the graph is a model's extraction
and can lag the text, while a pattern pass over a whole book takes seconds.

Decisions:
- Extraction and the developmental edit share this one implementation of the
  term rules, so the two cannot drift apart. (owner)
- The term index is built from the manuscript text alone, fresh on every call;
  nothing in the concept graph or `_concepts` notes is treated as
  authoritative. (owner)

Terms:
- **term index**: every recurring term of the manuscript, with its locations
  (file, section heading, paragraph number) and its definition-shaped
  sentences, from `Terms.index()`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ASCII, curly, and modifier letter apostrophes: Docs round-trips and LLM
# extraction disagree on the mark. A name stored with one form must still
# match text that uses another (2026-10-28: extract migrate's absent gate
# retired confirmed concepts whose only "absence" was ' vs ’).
_APOSTROPHE = "['‘’ʼ]"


def _escape_name(name: str) -> str:
    """`re.escape`, but any apostrophe in the name matches any apostrophe."""
    folded = (name.replace("‘", "'").replace("’", "'")
              .replace("ʼ", "'"))
    return _APOSTROPHE.join(re.escape(part) for part in folded.split("'"))


def concept_pattern(name: str) -> re.Pattern:
    """Word-boundary match tolerant of simple English plurals
    (trajectory/trajectories, field/fields, class/classes) and of ASCII
    vs curly apostrophes."""
    if name and name[-1].lower() == "y":
        stem = _escape_name(name[:-1])
        return re.compile(rf"\b{stem}(?:y|ies)\b", re.IGNORECASE)
    return re.compile(rf"\b{_escape_name(name)}(?:e?s)?\b", re.IGNORECASE)


def mention_pattern(name: str) -> re.Pattern:
    """Where a concept appears *as a term of art* — for ordering-sensitive
    checks (first mentions, prerequisite gaps). Single-word names declared
    with a capital keep it: the manuscripts capitalize their terms of art
    ('ye name it Space'), so casual English reuse of the same word ('time
    and space') must not count as the concept. Multi-word names collide
    with casual prose far less, and keep the tolerant match — which
    concept_pattern remains for existence checks (realization), where any
    casing of 'the herdsman' is genuinely the concept."""
    if " " not in name.strip() and name[:1].isupper():
        if name[-1].lower() == "y":
            stem = _escape_name(name[:-1])
            return re.compile(rf"\b{stem}(?:y|ies)\b")
        return re.compile(rf"\b{_escape_name(name)}(?:e?s)?\b")
    return concept_pattern(name)


def fold(text: str | None) -> str:
    """Whitespace folded and emphasis markers dropped: the book's headings
    are bold, and a model quoting `**Rankings**` as `Rankings` has still
    quoted the text. Nothing else is forgiven — not a word, not a comma."""
    return " ".join((text or "").replace("*", "").split())


_CAPITALISED = re.compile(r"[A-Z][\w’'-]+(?:[ \t]+[A-Z][\w’'-]+)*")


def capitalised_terms(text: str) -> set[str]:
    return set(_CAPITALISED.findall(text.replace("*", "")))


def sentence_initial(text: str, at: int) -> bool:
    i = at - 1
    while i >= 0 and text[i] in " \t*_#>\"'“‘([":
        i -= 1
    return i < 0 or text[i] in ".!?\n"


_DEFINES_AFTER = re.compile(
    r"^[^.;:]{0,30}?\b(?:is|are|was|were|means|mean|names|denotes|refers "
    r"to|is called|we call|ye call)\b")
_DEFINES_BEFORE = re.compile(r"\b(?:call|calls|called|name|names|named)\b"
                             r"[^.;:]{0,30}$")


def grounding_strength(span: str, name: str, paragraph: str = "") -> float:
    """§4.4's grounding term: 1.0 when the span is a heading or
    definition-shaped about `name` ("X is …", "we call it X"), 0.7
    otherwise."""
    folded = fold(span)
    if paragraph.lstrip().startswith("#") or folded.startswith("#"):
        return 1.0
    m = concept_pattern(name).search(folded) if name else None
    if m and (_DEFINES_AFTER.search(folded[m.end():])
              or _DEFINES_BEFORE.search(folded[:m.start()])):
        return 1.0
    return 0.7


# What the index leaves out of a paragraph before looking for terms: footnote
# definitions are citations (authors, titles, publishers), and inline
# directives are notes to the author, not the book's prose.
_FOOTNOTE_DEF = re.compile(r"^\s*\[\^[^\]]+\]:")
_DIRECTIVE = re.compile(r"\[(?:Footnote|Explain|Suggestion):[^\[\]\n]*\]",
                        re.IGNORECASE)
_FOOTNOTE_REF = re.compile(r"\[\^[^\]]+\]")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z*“\"‘'(])")
# Words a sentence opens with are part of the sentence, not of the term:
# "The Dharma", "When Nature". Stripped from the front of a candidate.
_OPENERS = frozenset("""
A All Also An And Any As At Because Before Both But By Each Even Every For
From He Her Here His How If In Into It Its Like Many More Most No Nor Not Now
Of On Once One Only Or Our She Since So Some Such That The Their Then There
These They This Those Thus To Under Until We What When Where Whether Which
While Who Why With Without Yet You Your
""".split())
# Capitalised words that are grammar, never terms, even mid-sentence.
_NOT_TERMS = frozenset({"I", "O", "Mr", "Mrs", "Dr", "St", "One", "Two",
                        "Three", "Four", "Five", "Six", "Seven", "Eight",
                        "Nine", "Ten"})
_POSSESSIVE = re.compile(r"[’']s$")


@dataclass(frozen=True)
class Location:
    file: str
    section: str        # the nearest heading above, emphasis folded; "" before any
    paragraph: int      # 1-based count of prose paragraphs in the file


@dataclass
class Term:
    name: str
    count: int = 0
    locations: list[Location] = field(default_factory=list)
    definitions: list[tuple[Location, str]] = field(default_factory=list)

    @property
    def files(self) -> list[str]:
        return list(dict.fromkeys(loc.file for loc in self.locations))


def _paragraphs(text: str) -> list[tuple[str, str]]:
    """(section heading, paragraph text) for each prose paragraph, with
    footnote definitions and inline directives removed."""
    out: list[tuple[str, str]] = []
    section = ""
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        m = _HEADING.match(block)
        if m and "\n" not in block:
            section = fold(m.group(2))
            continue
        if _FOOTNOTE_DEF.match(block):
            continue
        prose = _FOOTNOTE_REF.sub("", _DIRECTIVE.sub("", block)).strip()
        if prose:
            out.append((section, prose))
    return out


class Terms:
    """The term rules applied to one manuscript's files, in reading order."""

    def __init__(self, files: dict[str, str]):
        self.files = files

    def is_term_of_art(self, name: str) -> bool:
        """Gate 3: a single capitalised word must occur capitalised somewhere
        a sentence does NOT start (`mention_pattern`, case kept). A name that
        is only ever capitalised by its position is an English word, not a
        term — the "Experience" failure of §2.4 #12. Multi-word and lowercase
        names pass: `mention_pattern` keeps their tolerant match."""
        if " " in name.strip() or not name[:1].isupper():
            return True
        pattern = mention_pattern(name)
        return any(not sentence_initial(text, m.start())
                   for text in self.files.values()
                   for m in pattern.finditer(text))

    def index(self, min_count: int = 3, min_files: int = 2) -> list[Term]:
        """The term index: each capitalised term of art that occurs at least
        `min_count` times or in at least `min_files` files, in order of first
        appearance, with every location and its definition-shaped sentences."""
        paras = [(file, section, n, prose)
                 for file, text in self.files.items()
                 for n, (section, prose) in enumerate(_paragraphs(text), 1)]
        candidates: dict[str, None] = {}
        for _file, _section, _n, prose in paras:
            for raw in capitalised_terms(prose):
                words = raw.split()
                while words and words[0] in _OPENERS:
                    words.pop(0)
                name = _POSSESSIVE.sub("", " ".join(words)).strip(" -'’")
                if (len(name) > 1 and name not in _NOT_TERMS
                        and name not in _OPENERS and "_" not in name):
                    candidates.setdefault(name, None)
        # `mention_pattern` already matches the plural, so "Sermons" is
        # "Sermon" and is not listed twice.
        for name in list(candidates):
            for singular in (name[:-1], name[:-2], name[:-3] + "y"):
                if singular != name and singular in candidates:
                    candidates.pop(name, None)
                    break
        prose_files = {file: "\n\n".join(p for f, _s, _n, p in paras if f == file)
                       for file in self.files}
        gate = Terms(prose_files)
        terms: list[Term] = []
        for name in candidates:
            if not gate.is_term_of_art(name):
                continue
            pattern = mention_pattern(name)
            term = Term(name)
            for file, section, n, prose in paras:
                hits = list(pattern.finditer(prose))
                if not hits:
                    continue
                loc = Location(file, section, n)
                term.count += len(hits)
                term.locations.append(loc)
                for sentence in _SENTENCE.split(prose):
                    if (pattern.search(sentence)
                            and grounding_strength(sentence, name) == 1.0):
                        term.definitions.append((loc, sentence.strip()))
            if term.count >= min_count or len(term.files) >= min_files:
                terms.append(term)
        return terms
