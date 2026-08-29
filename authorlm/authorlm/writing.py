"""The drafting payload seam — stored state in, four blocks out.

`authorlm write draft` (design-write-draft.md) is the only verb in the
beat loop that makes an LLM call. This module owns exactly one thing:
turning the writeup's stored state into the four content blocks that call
sends, and turning the reply back into a `(why, self-check, draft)` triple
or a refusal. No database writes happen here.

**The one requirement: identical stored state ⇒ byte-identical blocks S
and A.** The blocks are ordered by volatility, and that order IS the
cache design (§3/§4) — the two stable layers carry the cache breakpoints,
so anything that reorders or re-words them between beat N and beat N+1
forfeits the whole prefix. Every serialization rule below exists because
the obvious implementation would have broken that:

- validated beliefs are sorted by STATEMENT and print no confidence
  number. `list_beliefs` orders by `confidence DESC`, and confidence moves
  on every explained verdict — i.e. on every beat. Printing it, or
  sorting on it, would have silently re-billed the cached layer between
  each pair of beats (design F4: a silent invalidator, found before it
  shipped).
- concept notes come from the beat specs' declared `concepts`, not from
  `scoped_concepts(file=…)`: `write start` truncates the essay to a
  placeholder, so the file-scoped graph slice is empty for the very file
  being drafted (design F3).
- no row ids, no timestamps, no counters anywhere.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .db import Database, loads

NONE = "(none)"
EMPTY_ACCEPTED = "(nothing accepted yet)"
OUTPUT_CONTRACT = """OUTPUT CONTRACT
Reply in exactly one of the two shapes the prompt defines, and nothing else:
WHY / SELF-CHECK / DRAFT (labels on their own lines, the prose running to the
end of the reply), or BLOCKED / QUESTION when the beat cannot be grounded.
Never emit the characters << or >> anywhere in the reply."""


@dataclass(frozen=True)
class Payload:
    """The four blocks, in volatility order. S and A are the cached
    prefix; B and C are re-read every beat."""

    law: str        # block S — prompt file + style law      (system)
    frame: str      # block A — beliefs, context, plan, graph (user)
    accepted: str   # block B — the essay so far              (user)
    beat: str       # block C — this beat, verdicts, contract (user)

    @property
    def system_blocks(self) -> list[str]:
        return [self.law]

    @property
    def user_blocks(self) -> list[str]:
        return [self.frame, self.accepted, self.beat]

    @property
    def blocks(self) -> list[tuple[str, str]]:
        return [("S", self.law), ("A", self.frame),
                ("B", self.accepted), ("C", self.beat)]

    @property
    def hashes(self) -> dict[str, str]:
        return {name: hashlib.sha256(text.encode()).hexdigest()
                for name, text in self.blocks}

    @property
    def sizes(self) -> dict[str, int]:
        return {name: len(text) for name, text in self.blocks}


@dataclass(frozen=True)
class Draft:
    why: str
    self_check: str
    text: str


@dataclass(frozen=True)
class Blocked:
    reason: str
    question: str


class ReplyError(ValueError):
    """The model replied in neither shape. Carries what was missing and
    the head of the reply, so the author can see what came back."""


# ------------------------------------------------------------- the prompt

def beat_draft_prompt() -> str:
    """The registered prompt file, verbatim (mirrors
    `summaries.summarizer_prompt`)."""
    from . import prompt_registry

    return prompt_registry.by_name("beat-draft").text()


def prompt_location() -> str:
    from . import prompt_registry

    return prompt_registry.by_name("beat-draft").location


# ------------------------------------------------------------- the blocks

def _section(label: str, body: str) -> str:
    """One labelled section. An absent section prints `(none)` rather than
    disappearing — a section that comes and goes changes the block's
    shape, and shape changes are cache invalidations."""
    body = (body or "").strip()
    return f"{label}\n{body or NONE}\n"


def _law_block(db: Database, manuscript_id: str, file: str) -> str:
    """Block S — the least volatile layer, and the system message.

    `styles.render` is already stable: file scope first, then up the guide
    chain, `ORDER BY created_at` within each layer."""
    from . import styles as st

    return (beat_draft_prompt().rstrip("\n") + "\n\n"
            + _section("STYLE LAW", st.render(db, manuscript_id, file)))


def _validated_beliefs(db: Database, manuscript_id: str) -> str:
    rows = db.all(
        "SELECT statement FROM editorial_beliefs "
        "WHERE manuscript_id = ? AND status = 'validated'",
        (manuscript_id,))
    return "\n".join(f"- {s}" for s in
                     sorted(row["statement"] for row in rows))


def _plan_concepts(plan: list) -> list[str]:
    """Every concept named ACROSS THE WHOLE PLAN, de-duplicated
    case-insensitively and sorted by name.

    The union rather than this beat's slice, deliberately: it makes block
    A identical for every beat of one plan, which is the stable prefix the
    cache is built on. The current beat's concept names are repeated in
    block C, where they cost four words."""
    seen: dict[str, str] = {}
    for beat in plan:
        if not isinstance(beat, dict):
            continue
        for name in beat.get("concepts") or []:
            if isinstance(name, str) and name.strip():
                seen.setdefault(name.strip().lower(), name.strip())
    return [seen[key] for key in sorted(seen)]


def _concept_notes(db: Database, manuscript: dict, plan: list) -> str:
    from . import api

    lines: list[str] = []
    for name in _plan_concepts(plan):
        try:
            shown = api.show_concept(db, manuscript, name)
        except LookupError:
            # NOT skipped. A beat that names a concept the graph does not
            # have is information the drafter must see — silently dropping
            # it would let the model invent the definition.
            lines.append(f"- {name} — (not in the graph)")
            continue
        node = shown["node"]
        notes = (node["notes"] or "").strip() or "(no notes)"
        lines.append(f"- {node['name']} — {notes}")
        edges = sorted(
            ((e.get("relation") or "", e.get("from_name") or "",
              e.get("to_name") or "") for e in shown["edges"]))
        for relation, source, target in edges:
            lines.append(f"    {source} {relation} {target}")
    return "\n".join(lines)


def _frame_block(db: Database, manuscript: dict, writeup: dict,
                 drafting_context: str) -> str:
    """Block A — the chapter frame, and the second cache breakpoint."""
    meta = loads(writeup["metadata"], {})
    plan = loads(writeup["plan"], [])
    digest = meta.get("digest")
    return "\n".join([
        _section("VALIDATED BELIEFS",
                 _validated_beliefs(db, manuscript["id"])),
        _section("DRAFTING CONTEXT", drafting_context),
        _section("BRIEF", meta.get("brief") or ""),
        _section("DIGEST",
                 json.dumps(digest, indent=2) if digest else ""),
        _section("RATIFIED PLAN", json.dumps(plan, indent=2)),
        _section("CONCEPT NOTES", _concept_notes(db, manuscript, plan)),
    ])


def _accepted_block(manuscript: dict, writeup: dict) -> str:
    """Block B — the essay as the author has approved it.

    Under the in-flight stream a truncated file holds the mid-rewrite
    PLACEHOLDER. That marker is scaffolding, never prose, and must never
    reach the drafting payload as if the essay opened with it."""
    from . import api

    path = Path(manuscript["path"]) / writeup["file"]
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if api.is_placeholder(text) or not text.strip():
        text = ""
    return _section("ACCEPTED TEXT SO FAR", text or EMPTY_ACCEPTED)


def _last_verdict(db: Database, writeup: dict) -> str:
    row = db.one(
        "SELECT gh.batch_index AS n, er.decision AS decision, "
        "       er.explanation AS explanation "
        "FROM editorial_reviews er "
        "JOIN guidance_history gh ON gh.id = er.guidance_id "
        "WHERE gh.batch_id = ? "
        "ORDER BY er.created_at DESC, er.id DESC LIMIT 1",
        (writeup["id"],))
    if not row:
        return ""
    reason = (row["explanation"] or "").strip() or "(no reason given)"
    return f"{row['decision']} on beat n={row['n']}: {reason}"


def _previous_draft(db: Database, writeup: dict, n: int) -> str:
    """The draft for THIS beat that the author turned down (or that a
    redraft superseded), so the model can see the fault it must not
    repeat."""
    row = db.one(
        "SELECT suggestion FROM guidance_history "
        "WHERE batch_id = ? AND batch_index = ? "
        "AND state IN ('rejected', 'superseded') "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (writeup["id"], n))
    return (row["suggestion"] or "").strip() if row else ""


def _beat_block(db: Database, writeup: dict, beat: dict) -> str:
    """Block C — everything that changes every beat."""
    learnings = loads(writeup["learnings"], [])
    sections = [
        _section("THIS BEAT", json.dumps(beat, indent=2)),
        _section("LEARNINGS", "\n".join(f"- {x}" for x in learnings)),
        _section("LAST VERDICT", _last_verdict(db, writeup)),
    ]
    previous = _previous_draft(db, writeup, beat["n"])
    if previous:
        sections.append(_section("PREVIOUS DRAFT (rejected)", previous))
    sections.append(OUTPUT_CONTRACT + "\n")
    return "\n".join(sections)


def assemble(db: Database, manuscript: dict, writeup: dict, beat: dict,
             capture=None) -> Payload:
    """The whole payload, from stored state only.

    `capture` is the invocation's single summaries snapshot (Stream AC's
    one-capture-per-invocation convention): the drafting context that
    reaches the model must be the same text the caller's own gates saw,
    not a second read a parallel session could have moved underneath it."""
    from . import api

    return Payload(
        law=_law_block(db, manuscript["id"], writeup["file"]),
        frame=_frame_block(db, manuscript, writeup,
                           api._drafting_context(db, manuscript, writeup,
                                                 capture=capture)),
        accepted=_accepted_block(manuscript, writeup),
        beat=_beat_block(db, writeup, beat),
    )


# -------------------------------------------------------------- the reply

_LABELS = ("WHY", "SELF-CHECK", "DRAFT", "BLOCKED", "QUESTION")


def _label_lines(text: str) -> dict[str, int]:
    """Line index of each bare-uppercase label, first occurrence only.

    Bare words on their own line, the convention `prompts/summarizer.md`
    already uses — and deliberately NOT `<<…>>`, which is reserved
    grammar here (`threads._ANY_MARKER`; `gdocs.diff_push` raises on any
    paragraph containing `<<`), so a draft carrying those delimiters
    would break the next surgical Doc push."""
    found: dict[str, int] = {}
    for index, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if stripped in _LABELS and stripped not in found:
            found[stripped] = index
    return found


def parse_reply(text: str) -> Draft | Blocked:
    """§1.5's parser. `DRAFT` is last and unterminated on purpose: split
    on the first line equal to `DRAFT` and everything after it is the
    manuscript text — no escaping, no JSON, no quoting hazard, and no way
    for a stray delimiter inside the prose to truncate the beat."""
    text = (text or "").replace("\r\n", "\n")
    lines = text.splitlines()
    at = _label_lines(text)

    # A refusal only when BLOCKED comes FIRST. Everything after the DRAFT
    # line is manuscript text, and a beat is perfectly entitled to contain
    # a line reading BLOCKED — a bare word on its own line is a plausible
    # thing for prose to do. Reading that as a refusal would silently
    # discard a good draft. Both readings stay fail-safe: a leading
    # BLOCKED registers nothing, and a trailing one is prose the author
    # still rules on.
    if "BLOCKED" in at and at["BLOCKED"] < at.get("DRAFT", len(lines) + 1):
        body = _between(lines, at["BLOCKED"] + 1,
                        at.get("QUESTION", len(lines)))
        question = (_between(lines, at["QUESTION"] + 1, len(lines))
                    if "QUESTION" in at else "")
        return Blocked(reason=body, question=question)

    missing = [label for label in ("WHY", "SELF-CHECK", "DRAFT")
               if label not in at]
    if missing:
        raise ReplyError(_reply_error(
            f"the reply carries neither shape — missing "
            f"{', '.join(missing)} (and no BLOCKED)", text))
    if not (at["WHY"] < at["SELF-CHECK"] < at["DRAFT"]):
        raise ReplyError(_reply_error(
            "WHY, SELF-CHECK and DRAFT are present but out of order", text))
    why = _between(lines, at["WHY"] + 1, at["SELF-CHECK"])
    self_check = _between(lines, at["SELF-CHECK"] + 1, at["DRAFT"])
    draft = _between(lines, at["DRAFT"] + 1, len(lines))
    if not why:
        raise ReplyError(_reply_error("WHY is empty — the verdict evidence "
                                      "hangs off it", text))
    if not draft:
        raise ReplyError(_reply_error("nothing follows the DRAFT line", text))
    if "<<" in draft or ">>" in draft:
        raise ReplyError(_reply_error(
            "the draft contains '<<' or '>>', which are reserved grammar in "
            "this system (the Doc bridge refuses any paragraph carrying "
            "them). Nothing was registered", text))
    return Draft(why=why, self_check=self_check, text=draft)


def _between(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[start:end]).strip()


def _reply_error(what: str, text: str) -> str:
    head = (text or "").strip()[:400]
    return f"{what}. The first 400 characters of what came back:\n\n{head}"
