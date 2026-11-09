"""The critic payload seam — stored state plus one draft in, one block out.

`authorlm write critique` assembles what an INDEPENDENT reader needs to
find the faults the author keeps sending beats back for: the laws and
learnings as a numbered checklist, the accepted text, the protected
vocabulary, the settled concept notes, the paragraphs elsewhere in the
book the draft most resembles (ranked mechanically, so cross-essay
repetition is visible without shipping the whole book), the lint
report, and the draft itself. No database writes happen here and no
model is called: the critic is whoever reads the payload — a subagent
with an empty context in chat mode, a model on the native path later.

The book here is the BOOK: the files toc.toml declares `outside`
(papers, side pieces, the Metaphysic) are left out of the nearest
passages and the lint corpus alike (`book`). They are mirrored to
the Doc but are not part of what the reader holds, so a critic that
ranked them sent the drafter to cite text that has left the book
(whitehead.md, 2026-11-07: "re-defines locus in the Metaphysic's own
words", against metaphysic.md, which is outside).

The critic also sees the drafter's ALREADY IN THE BOOK block, built by
the same function from the same beat (`writing._already_in_book`), so a
REPEAT finding is judged on the ground the drafter was given.

`parse_report` reads the critic's reply back. Its contract is the
prompt's: VERDICT then PASS or FAIL, then FINDINGS lines.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import lint
from .db import Database, loads
from .revisions import _version_text, is_placeholder
from .summaries import drafting_context_for_write_status

NEAREST = 8          # passages from other essays shown to the critic
NEAREST_N = 5        # shingle width for the resemblance ranking


class ReportError(ValueError):
    """The critic replied in neither shape."""


def critic_prompt() -> str:
    from . import prompt_registry

    return prompt_registry.by_name("beat-critic").text()


def prompt_location() -> str:
    from . import prompt_registry

    return prompt_registry.by_name("beat-critic").location


# ------------------------------------------------------------- the blocks

def _section(label: str, body: str) -> str:
    body = (body or "").strip()
    return f"{label}\n{body or '(none)'}\n"


def checklist(db: Database, manuscript_id: str, file: str,
              learnings: list[str]) -> str:
    """Laws numbered L1…, learnings numbered G1… — the critic cites the
    number, so a finding is traceable to the exact ratified element."""
    from . import styles as st

    lines = []
    for i, element in enumerate(st.effective_style(db, manuscript_id, file), 1):
        lines.append(f"L{i} [{element['aspect']}] {element['statement']}")
    for i, lesson in enumerate(learnings, 1):
        lines.append(f"G{i} {lesson}")
    return "\n".join(lines)


def nearest_passages(draft: str, files: dict[str, list[str]],
                     order: list[str], limit: int = NEAREST,
                     n: int = NEAREST_N) -> list[tuple[str, int, str, float]]:
    """The `limit` paragraphs across `files` that share the most n-word
    shingles with the draft, as (file, ¶index, text, score). Ties break
    by reading order then position, so the block is deterministic."""
    target = lint.shingles(draft, n)
    if not target:
        return []
    rank = {name: i for i, name in enumerate(order)}
    scored = []
    for name, paras in files.items():
        for k, para in enumerate(paras, 1):
            sh = lint.shingles(para, n)
            if not sh:
                continue
            common = len(sh & target)
            if common == 0:
                continue
            score = common / (len(sh | target) or 1)
            scored.append((-score, rank.get(name, len(rank)), k, name, para))
    scored.sort()
    return [(name, k, para, round(-neg, 4))
            for neg, _r, k, name, para in scored[:limit]]


def book(manuscript: dict) -> tuple[dict[str, str], list[str]]:
    """({file: text}, reading order) for the book's prose on disk:
    structural files and every file toc.toml declares `outside` removed.
    The one definition of "the book" for the write loop's critic, lint
    and ALREADY IN THE BOOK block, so the three cannot disagree on what
    the book already says. The order is read from toc.toml BEFORE the
    structural files are dropped (dropping them first loses the TOC and
    falls back to alphabetical order)."""
    from .revisions import read_manuscript_files
    from .structure import (TOC_FILENAME, content_files, is_outside,
                            reading_order, toc_outside)

    files = read_manuscript_files(Path(manuscript["path"]))
    outside = toc_outside(files.get(TOC_FILENAME) or "")
    order, _unlisted = reading_order(files)
    return ({name: text for name, text in content_files(files).items()
             if not is_outside(name, outside)}, order)


def _book(manuscript: dict, exclude: str) -> tuple[dict[str, list[str]],
                                                   list[str]]:
    files, order = book(manuscript)
    paras = {name: lint.paragraphs(text) for name, text in files.items()
             if name != exclude}
    return paras, order


def concept_provenance(db: Database, manuscript_id: str
                       ) -> list[tuple[str, str]]:
    rows = db.all(
        "SELECT name, introduced_in FROM concept_nodes WHERE manuscript_id = ? "
        "AND kind = 'concept' AND status IN ('realized', 'declared') "
        "AND introduced_in IS NOT NULL", (manuscript_id,))
    return [(r["name"], r["introduced_in"]) for r in rows]


def lint_draft(db: Database, manuscript: dict, file: str, draft: str,
               include_self: bool = True,
               writeup: dict | None = None) -> lint.Report:
    """The lint report for one draft against the book. The file being
    drafted stays IN the corpus by default: during a writeup the file on
    disk is the accepted text, and a beat that repeats an earlier beat
    is the most common fault of all.

    With `writeup`, the italic-term check is seeded from the accepted
    text and the brief, where a borrowed term is first set in italics."""
    files, _order = book(manuscript)
    corpus = lint.corpus_from_files(files, exclude=None if include_self
                                    else file)
    accepted = brief = ""
    if writeup is not None:
        path = Path(manuscript["path"]) / writeup["file"]
        accepted = path.read_text(encoding="utf-8") if path.exists() else ""
        if is_placeholder(accepted):
            accepted = ""
        brief = loads(writeup["metadata"], {}).get("brief") or ""
    return lint.lint_text(draft, corpus=corpus,
                          concepts=concept_provenance(db, manuscript["id"]),
                          this_file=file, accepted=accepted, brief=brief)


def assemble(db: Database, manuscript: dict, writeup: dict, beat: dict,
             draft: str, report: lint.Report) -> str:
    """The whole critic payload as one text."""
    from . import writing
    from . import filtering as fg

    file = writeup["file"]
    learnings = loads(writeup["learnings"], [])
    paras, order = _book(manuscript, exclude=file)
    nearest = nearest_passages(draft, paras, order)
    nearest_text = "\n\n".join(
        f"[{name} ¶{k}]\n{text}" for name, k, text, _score in nearest)
    try:
        protected = fg.protected_terms(db, manuscript, file)["all"]
    except Exception:       # a file with no scoped slice yet
        protected = []
    # A rewrite pins the original: it is the author's own prose for THIS
    # essay, and the reference for voice and casing that the accepted
    # text cannot supply while it is still empty. (The lint corpus does
    # NOT include it, deliberately: a modeled rewrite is meant to reuse
    # the original's language.)
    original = _version_text(db, writeup.get("source_version_id"), file)
    if original and is_placeholder(original):
        original = None
    # The settled context the drafter was allowed to lean on: the BEFORE
    # half of the drafting context. The critic must see the same ground,
    # or a back-reference to an earlier essay is unjudgeable. AFTER is
    # withheld: it is not ground for the drafter either.
    try:
        context = drafting_context_for_write_status(db, manuscript, writeup)
    except Exception:
        context = ""
    marker = context.find("AFTER")
    before = context[:marker] if marker > 0 else context
    blocks = [
        critic_prompt().rstrip("\n") + "\n",
        _section("THE CHECKLIST (binding; cite the number)",
                 checklist(db, manuscript["id"], file, learnings)),
        _section("LAST VERDICT", writing._last_verdict(db, writeup)),
        _section("THIS BEAT", json.dumps(beat, indent=2)),
        # The brief is ground for the drafter (block A), so it is ground for
        # the critic too: a new essay's sources live only there, and without
        # it every quotation the brief supplied reads as invented.
        _section("THE BRIEF (the author's ratified ground for this essay)",
                 loads(writeup["metadata"], {}).get("brief") or "(no brief)"),
        _section("CONCEPT NOTES",
                 writing._concept_notes(db, manuscript, [beat])),
        _section("PROTECTED TERMS", "\n".join(f"- {t}" for t in protected)),
        _section("SETTLED CONTEXT (what the earlier essays say, as their "
                 "summaries say it — the only ground for a back-reference)",
                 before),
        writing._accepted_block(manuscript, writeup),
        _section("THE ORIGINAL ESSAY (pinned at write start; the author's own "
                 "prose for this essay — the reference for voice and casing; "
                 "a rewrite may reuse its language freely)",
                 original or "(not a rewrite)"),
        _section(writing.ALREADY_HEADING,
                 writing._already_in_book(db, manuscript, writeup, beat)),
        _section("NEAREST PASSAGES ELSEWHERE IN THE BOOK (ranked by "
                 "resemblance; the book already says these)", nearest_text),
        _section("LINT", lint.render(report).split("\n", 1)[1]),
        _section("THE DRAFT", draft.strip()),
    ]
    return "\n".join(blocks)


# -------------------------------------------------------------- the reply

def parse_report(text: str) -> dict:
    """{"verdict": "PASS"|"FAIL", "findings": [str, …]}."""
    lines = [line.rstrip() for line in (text or "").splitlines()]
    verdict = None
    findings: list[str] = []
    mode = None
    for line in lines:
        bare = line.strip()
        if bare == "VERDICT":
            mode = "verdict"
            continue
        if bare == "FINDINGS":
            mode = "findings"
            continue
        if mode == "verdict" and bare:
            if bare.upper() not in ("PASS", "FAIL"):
                raise ReportError(
                    f"VERDICT must be PASS or FAIL, got {bare!r}")
            verdict = bare.upper()
            mode = None
        elif mode == "findings" and bare.startswith("-"):
            findings.append(bare[1:].strip())
    if verdict is None:
        head = " ".join((text or "").split())[:160]
        raise ReportError(
            "no VERDICT line in the critic's report — it must reply "
            f"VERDICT then PASS or FAIL (got: {head!r})")
    if verdict == "FAIL" and not findings:
        raise ReportError("VERDICT FAIL with no FINDINGS lines")
    return {"verdict": verdict, "findings": findings}
