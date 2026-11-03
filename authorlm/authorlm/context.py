"""Declared inputs: the vocabulary a pass asks in, and the summary levels each
member needs.

A processing module (the critique edit pass, the beat loop, a filter, a
lens, extraction, the reader load) names the artifacts it needs as members
of `Input`, in its own code or, for a lens or filter, in the artifact's
front matter by the member's string value. This module only names them and
brings their summary levels up to date; `input_blocks.supply` renders them.
It imports nothing above the manuscript text, so the lowest declaring module
can read the vocabulary.
"""

from __future__ import annotations

from enum import Enum

from .db import Database

class Input(str, Enum):
    """Every block a pass can declare. The value is the front-matter
    spelling; an unknown name is rejected by name at parse time, as the
    filter and lens vocabularies already are."""

    STYLE_LAW = "style-law"                  # styles.render
    VALIDATED_BELIEFS = "beliefs"            # editorial_beliefs, validated
    INTENTS = "intents"                      # passes.intents_in_scope
    CONCEPT_SLICE = "concepts"               # concepts.scoped_concepts(file=)
    PROTECTED_TERMS = "protected"            # filtering.protected_terms
    DICTIONARY = "dictionary"                # the pronunciation dictionary
    CAST = "cast"                            # _profiles/cast
    PROFILES = "profiles"                    # _profiles/*.md, named by the pass
    PRIOR_RUNS = "prior-runs"                # the pass's own history on this file
    ESSAY_SUMMARIES = "essay-summaries"      # before/after, ensure(level=essay)
    SECTION_OUTLINE = "section-outline"      # this essay's section summaries
    PARAGRAPH_RECORDS = "paragraph-records"  # this essay's records
    REFERENCED_SECTIONS = "referenced-sections"  # text the records refer back to
    TARGET_CHAPTERS = "target-chapters"      # lenses.assemble's T block
    READING_ORDER = "reading-order"
    ESSAY_TEXT = "essay-text"                # the unit under review, whole

    @classmethod
    def parse(cls, names: list[str]) -> list["Input"]:
        """Front-matter names to members; an unknown name is refused BY
        NAME, with the vocabulary in the message."""
        out = []
        for name in names:
            try:
                out.append(cls(name))
            except ValueError:
                raise ValueError(
                    f"unknown input '{name}'; the vocabulary is: "
                    + ", ".join(m.value for m in cls)) from None
        return out


# The summary level each input needs fresh before it is read.
_LEVEL_OF = {
    Input.ESSAY_SUMMARIES: "essay",
    Input.SECTION_OUTLINE: "section",
    Input.PARAGRAPH_RECORDS: "paragraph",
    Input.REFERENCED_SECTIONS: "paragraph",
}


def ensure_for(db: Database, manuscript: dict, file: str,
               needs: list[Input], llm, echo=print) -> list[dict]:
    """Bring up to date every summary level the declaration reads
    (ruling 3): the essay level over the whole book (before/after read
    every unit), the lower levels for this file only — except
    REFERENCED_SECTIONS, whose index is every file's records. Returns the
    build reports; each level that built prints one cost line (ruling 9)."""
    from . import summaries as sums

    reports = []
    levels = {_LEVEL_OF[n] for n in needs if n in _LEVEL_OF}
    if not levels:
        return reports
    book_wide = (Input.ESSAY_SUMMARIES in needs
                 or Input.REFERENCED_SECTIONS in needs)
    highest = max(levels, key=sums.LEVELS.index)
    reports.append(sums.ensure(db, manuscript, llm, level=highest,
                               files=None if book_wide else [file], echo=echo))
    return reports
