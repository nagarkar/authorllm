"""The one place that supplies a pass's declared inputs.

A processing module names what it needs as `context.Input` members; `supply`
renders each requested block deterministically from stored state and never
decides what a pass should see: changing what a pass receives is a change to
its declaration, never to this module.

It sits above the text it renders because one block, INTENTS, reads the scope
rules in `passes`, so nothing below the editing passes may import it. What the
lower modules need from the vocabulary is in `context`.

Two conventions carried over from the assemblers this replaces
(`filtering._section`, `writing._section`): an absent block renders as
`(none)` rather than disappearing, and a block's text is stable across calls
on unchanged state (no ids, no timestamps), so prompt caches hold.

Summary levels are the one kind of input that may be BUILT on the way in: with
an `llm` the requested level is brought up to date by `context.ensure_for`,
which prints its cost line and proceeds; without one the stored rows are
served as they are, each marked with its state, and nothing is gated here.
"""

from __future__ import annotations

from .concepts import concept_notes
from .context import Input, ensure_for
from .db import Database

NONE = "(none)"

# How many other-chapter sections a backward reference may pull in
# (REFERENCED_SECTIONS): the text of a dozen sections is already a
# chapter's worth; past that the pass is reading the book, not a pointer.
REFERENCED_CAP = 12

# Inputs whose rendering needs an argument only the declaring pass has
# (a filter's name for PRIOR_RUNS, the profile keys, a lens's targets).
# Declared in `Input` so the vocabulary is complete; rendered by the module
# that owns the argument, named in the message, until that module moves onto
# `supply` with a parameter for it.
_OWNED_ELSEWHERE = {
    Input.PROTECTED_TERMS: "filtering.protected_terms",
    Input.DICTIONARY: "filtering._dictionary_block",
    Input.CAST: "filtering.cast_block",
    Input.PROFILES: "filtering._profiles_block",
    Input.PRIOR_RUNS: "filtering.prior_runs",
    Input.TARGET_CHAPTERS: "lenses._targets_block",
}


def section(label: str, body: str) -> str:
    body = (body or "").strip()
    return f"{label}\n{body or NONE}\n"


# ------------------------------------------------------------- renderers

def _style_law(db, manuscript, file, texts, capture) -> str:
    from .styles import render

    return render(db, manuscript["id"], file)


def _beliefs(db, manuscript, file, texts, capture) -> str:
    rows = db.all(
        "SELECT statement FROM editorial_beliefs "
        "WHERE manuscript_id = ? AND status = 'validated'",
        (manuscript["id"],))
    return "\n".join(f"- {s}" for s in
                     sorted(row["statement"] for row in rows))


def _intents(db, manuscript, file, texts, capture) -> str:
    from .passes import intents_block

    return intents_block(db, manuscript, file)


def _concepts(db, manuscript, file, texts, capture) -> str:
    return concept_notes(db, manuscript, file, texts.get(file))


def _reading_order(db, manuscript, file, texts, capture) -> str:
    return "\n".join(f"{n}. {f}" + ("  <- this essay" if f == file else "")
                     for n, f in enumerate(texts, 1))


def _essay_text(db, manuscript, file, texts, capture) -> str:
    return texts.get(file) or ""


def _essay_summaries(db, manuscript, file, texts, capture) -> str:
    """The essays before and after this one, each with its summary and its
    state. Ungated and MARKED, as the filter pass renders them: a stale
    entry is shown with `!!` and the reader decides."""
    from . import summaries as sums

    before, after = sums.before_after(db, manuscript, file,
                                      capture=capture)

    def render(entries):
        out = []
        for e in entries:
            state = e.get("state") or ""
            mark = "" if state == "fresh" else f"  !! summary {state}"
            body = (e.get("summary") or "").strip()
            out.append(f"[{e['file']}]{mark}" + (f"\n{body}" if body
                                                  else "  !! no summary"))
        return "\n\n".join(out) or NONE
    return ("BEFORE this essay in the reading order (settled; their concepts "
            "are available here)\n" + render(before)
            + "\n\nAFTER this essay (forward reference only; never assume "
            "what they say)\n" + render(after))


def _section_outline(db, manuscript, file, texts, capture) -> str:
    """This essay's section summaries in order: the map of the argument a
    pass editing the essay was missing. A section with no current summary
    is marked, not hidden."""
    from . import summaries as sums

    unit = sums.for_unit(db, manuscript, file, texts.get(file), texts=texts)
    if not unit["sections"]:
        return ""
    out = []
    for s in unit["sections"]:
        head = f"[§{s['n']}] {s['heading'] or '(preamble)'} " \
               f"(paragraphs {s['paragraphs'][0]}-{s['paragraphs'][1]})"
        body = (s["summary"] or "").strip() or "!! no section summary"
        out.append(f"{head}\n{body}")
    return "\n\n".join(out)


def _paragraph_records(db, manuscript, file, texts, capture) -> str:
    """This essay's records as compact JSON lines, one per paragraph: the
    agent-readable grain."""
    import json

    from . import summaries as sums

    recs = sums.paragraph_records(db, manuscript, files=[file],
                                  texts={file: texts.get(file, "")})
    return "\n".join(json.dumps({k: v for k, v in r.items()
                                 if k not in ("file", "para_hash")},
                                ensure_ascii=False)
                     for r in recs)


def referenced_sections(db: Database, manuscript: dict, file: str,
                        texts: dict[str, str]) -> list[dict]:
    """Which sections of OTHER essays this essay's records refer back to:
    for each backward reference, the section holding the first
    `introduces | defines` mention of its term in reading order, when that
    section is in another file. Deduplicated, in reading order, capped at
    REFERENCED_CAP. Each: {file, section_ordinal, heading, section_hash,
    text, terms}."""
    from . import summaries as sums

    recs = sums.paragraph_records(db, manuscript, files=[file],
                                  texts={file: texts.get(file, "")})
    wanted = []
    for rec in recs:
        for ref in rec["references"]:
            if ref["direction"] == "back" and ref["term"] not in wanted:
                wanted.append(ref["term"])
    if not wanted:
        return []
    order = list(texts)
    found: dict[tuple[str, int], dict] = {}
    for term in wanted:
        hit = next((m for m in sums.mentions(db, manuscript, term=term,
                                             texts=texts)
                    if m["role"] in ("introduces", "defines")
                    and m["file"] != file), None)
        if hit is None:
            continue
        key = (hit["file"], hit["section_ordinal"])
        if key in found:
            found[key]["terms"].append(term)
            continue
        verse = hit["file"] in sums.verse_files_of(manuscript)
        secs = sums.sections_of(texts.get(hit["file"], ""), verse_file=verse)
        sec = next((s for s in secs
                    if s["section_ordinal"] == hit["section_ordinal"]), None)
        if sec is None:
            continue
        found[key] = {"file": hit["file"],
                      "section_ordinal": sec["section_ordinal"],
                      "heading": sec["heading"],
                      "section_hash": sec["section_hash"],
                      "text": "\n\n".join(p["text"] for p in sec["paragraphs"]),
                      "terms": [term]}
    rows = sorted(found.values(),
                  key=lambda r: (order.index(r["file"]) if r["file"] in order
                                 else len(order), r["section_ordinal"]))
    return rows[:REFERENCED_CAP]


def _referenced_sections(db, manuscript, file, texts, capture) -> str:
    rows = referenced_sections(db, manuscript, file, texts)
    return "\n\n".join(
        f"[{r['file']} §{r['section_ordinal']} {r['heading'] or '(preamble)'}] "
        f"(sha {r['section_hash']}; referred to for: {', '.join(r['terms'])})\n"
        f"{r['text']}" for r in rows)


_RENDER = {
    Input.STYLE_LAW: _style_law,
    Input.VALIDATED_BELIEFS: _beliefs,
    Input.INTENTS: _intents,
    Input.CONCEPT_SLICE: _concepts,
    Input.ESSAY_SUMMARIES: _essay_summaries,
    Input.SECTION_OUTLINE: _section_outline,
    Input.PARAGRAPH_RECORDS: _paragraph_records,
    Input.REFERENCED_SECTIONS: _referenced_sections,
    Input.READING_ORDER: _reading_order,
    Input.ESSAY_TEXT: _essay_text,
}


def supply(db: Database, manuscript: dict, file: str, needs: list[Input], *,
           capture=None, llm=None, echo=print) -> dict[Input, str]:
    """Render every declared input for `file`, in the order declared.

    `capture` is the caller's one snapshot (`summaries.capture`), so the
    gate, the summaries and the text a pass sees are the same bytes;
    omitted, one is taken here. With `llm` (a client or `summaries.clients`
    dict) the summary levels the declaration reads are rebuilt first where
    dirty; without one they are served as stored and marked."""
    from . import summaries as sums

    for need in needs:
        if need in _OWNED_ELSEWHERE:
            raise NotImplementedError(
                f"{need.value} is declared but still rendered by "
                f"{_OWNED_ELSEWHERE[need]}, which needs an argument only "
                f"its pass has; move that renderer here to supply it")
    if llm is not None:
        ensure_for(db, manuscript, file, needs, llm, echo=echo)
    capture = capture or sums.capture(db, manuscript)
    texts = capture[0]
    return {need: _RENDER[need](db, manuscript, file, texts, capture)
            for need in needs}
