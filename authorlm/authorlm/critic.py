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

`parse_report` reads the critic's reply back. Its contract is the
prompt's: VERDICT then PASS or FAIL, then FINDINGS lines.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import lint
from .db import Database, loads

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


def _book(manuscript: dict, exclude: str) -> tuple[dict[str, list[str]],
                                                   list[str]]:
    from .revisions import read_manuscript_files
    from .structure import content_files, reading_order

    files = content_files(read_manuscript_files(Path(manuscript["path"])))
    order, _unlisted = reading_order(files)
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
               include_self: bool = True) -> lint.Report:
    """The lint report for one draft against the book. The file being
    drafted stays IN the corpus by default: during a writeup the file on
    disk is the accepted text, and a beat that repeats an earlier beat
    is the most common fault of all."""
    from .revisions import read_manuscript_files
    from .structure import content_files

    files = content_files(read_manuscript_files(Path(manuscript["path"])))
    corpus = lint.corpus_from_files(files, exclude=None if include_self
                                    else file)
    return lint.lint_text(draft, corpus=corpus,
                          concepts=concept_provenance(db, manuscript["id"]),
                          this_file=file)


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
    from . import api as _api

    original = _api._version_text(db, writeup.get("source_version_id"), file)
    if original and _api.is_placeholder(original):
        original = None
    # The settled context the drafter was allowed to lean on: the BEFORE
    # half of the drafting context. The critic must see the same ground,
    # or a back-reference to an earlier essay is unjudgeable. AFTER is
    # withheld: it is not ground for the drafter either.
    try:
        context = _api._status_drafting_context(db, manuscript, writeup)
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
