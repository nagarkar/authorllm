"""A book as a workflow of roles: the state machine and the record of who
did which step of one book, and what each step handed back.

A book is written by a team of roles (interviewer, drafter, critic,
structural editor, line editor, copy editor, proofreader, author, ...).
Which roles there are, in what order, and whether a person or an agent
does each one is the book's own choice, written in its definition file,
`<manuscript>/_book/workflow.toml` (seeded once from
`library/book-workflow.toml` by `authorlm book init`, then the book's to
edit). This module reads that file, says which step is next, hands the
role its pack (written to `<manuscript>/_book/packs/<run-id>.md`), and
records each attempt as one row of `step_runs` and each change of its
status as one row of `step_events`. It is the only writer of those tables
and of the `_book/` folder. It calls no model: an agent step is
carried out by the conversation that drives these verbs, or by its
subagents, exactly as a developmental edit is.

A step runs once per scope of its grain: once for the book, once per part,
or once per chapter. A chapter is a main-matter file of `toc.toml` that is
not a part page and not declared `outside`; a part is a top-level file
with files beneath it, and its scope is the `dc-` id of that file. A
chapter outside every part is read with the part before it (the first
part, when none comes before), and a book without parts is read as one
part whose scope is the book. Scopes are document ids, never file names,
so a renamed chapter keeps its runs; that is why every verb here needs the
manuscript's `document_ids` setting on.

Decisions:
- Each step names its actor, `agent` or `human:<who>`, and the actor can
  be changed per book and per step (`assign`) without touching anything
  else: a person and an agent are interchangeable holders of a role. A run
  copies the actor when it starts, so a later change never rewrites what
  happened. Enforced by:
  tests/test_book_workflow.py::AssignTests::test_assign_swaps_the_actor_and_old_runs_keep_theirs.
- Between agent steps nothing stops: a step is ready as soon as every step
  it follows is done for its scope. When a step's actor is a person the
  run waits, the board says who is waited on, and that person is notified.
  Enforced by:
  tests/test_book_workflow.py::OrderingTests::test_the_book_runs_in_order_across_all_three_grains
  and
  tests/test_book_workflow.py::HumanStepTests::test_a_human_step_waits_names_the_person_and_notifies.
- No walls: a pack lists what a role is handed up front and never limits
  what it may read; any role may read the whole manuscript. The one
  exception is declared per step: an `independent` reader is not handed
  what the steps before it returned, nor its own earlier attempt.
  Enforced by:
  tests/test_book_workflow.py::PackTests::test_the_pack_states_the_contract
  and
  tests/test_book_workflow.py::PackTests::test_an_independent_step_is_not_handed_earlier_outputs.
- Any step can be restarted, for any reason (wrong information from the
  author, a flawed prompt, missing context). What that step and the steps
  that followed it produced, for the same scope and the scopes inside it,
  is kept and marked superseded; the rerun is handed the old result as
  reference only, with no authority over the new one. What was made
  before never becomes the standard. Enforced by:
  tests/test_book_workflow.py::RestartTests::test_restart_supersedes_the_step_and_its_dependents_only
  and
  tests/test_book_workflow.py::RestartTests::test_the_rerun_is_handed_the_old_result_as_reference_only.
- A restart never supersedes a wider scope: a part or book step already
  done on the old work stays done and is flagged on the board as resting
  on a restarted step, for the author to decide. Enforced by:
  tests/test_book_workflow.py::RestartTests::test_a_wider_step_that_rested_on_a_restarted_one_is_flagged.
- Every change of a run's status is one row of `step_events`, written in
  the same transaction by the one function that changes a status
  (`_transition`); the table is only ever appended to, so it is the
  record of what happened and when (`history`). Enforced by:
  tests/test_book_workflow.py::HistoryTests::test_no_run_has_a_status_its_last_event_does_not_give
  and
  tests/test_book_workflow.py::HistoryTests::test_only_one_function_writes_a_runs_status.
- The definition may change while a book is in flight. Steps are matched
  by their id, never by position, and each run records a fingerprint of
  the definition file and of its step's contract as they were when it
  started; the board says when a run in force was made under an earlier
  version of its step's contract. Enforced by:
  tests/test_book_workflow.py::DefinitionVersionTests::test_steps_are_matched_by_id_not_by_position
  and
  tests/test_book_workflow.py::DefinitionVersionTests::test_status_says_when_a_run_was_made_under_an_earlier_contract.
- The workflow is sequential steps, `after` dependencies, one run per
  scope of a step's grain, and supersede. It has no gateways, timers or
  conditions.
- The diagram (`diagram`) is drawn from the definition file and the runs
  each time it is asked for, as Mermaid flowchart text and as its nodes
  and edges; nothing here renders it. A step's state has one source
  (`_cell`) for the status board and the diagram, so the two cannot
  disagree; superseded is a run's status and never a step's state.
  Enforced by:
  tests/test_book_workflow.py::DiagramTests::test_adding_a_step_changes_the_diagram
  and
  tests/test_book_workflow.py::DiagramTests::test_every_node_carries_the_state_the_status_board_gives.
- Recording what rested on the register is not left to the role's
  memory: an agent's run of a step that is given the register is marked
  done only when its result declares its uses (`register_uses`), each
  labelled applies or infers, or says it rested on none and why. `done`
  writes those uses under the run, through `register.use`. A person's
  step is exempt. Why: the author must be able to tell a choice they made
  from one made for them. Enforced by:
  tests/test_book_workflow.py::RegisterUsesOnDoneTests::test_done_is_declined_without_the_key_and_names_it
  and
  tests/test_book_workflow.py::RegisterUsesOnDoneTests::test_the_uses_are_written_under_the_run.
- The trade's order is large to small, and each role's contract
  (`leaves_alone`) says what it does not change: a later role that finds
  an earlier-level problem files it back and does not fix it.
- Every pack carries the register entries in scope, with the author's
  words and the machine's reading (register.py), and tells the role to
  record a use, `applies` or `infers`, for each entry its work rests on.
  Enforced by:
  tests/test_book_workflow.py::PackTests::test_the_pack_carries_the_register_in_scope.
- Each agent role may have a role file in the checkout,
  `.claude/agents/book-<role slug>.md`: a Claude Code subagent that says
  how that trade does its step with AuthorLM's existing verbs. When one
  exists, an agent step's pack names it near the top, so whoever starts
  the step knows which subagent to hand it to. The role file is advice on
  method; the contract in the pack is the one in force. A missing role
  file, or a package installed outside a checkout, changes nothing else.
  Enforced by:
  tests/test_book_role_agents.py::PackNamesTheRoleFileTests::test_an_agent_steps_pack_names_its_role_file
  and
  tests/test_book_role_agents.py::PackNamesTheRoleFileTests::test_a_role_without_a_file_gets_a_pack_that_names_none.

Terms:
- **workflow definition**: the book's own list of steps,
  `_book/workflow.toml`.
- **step**: one role's turn in the workflow: its id, role, actor, grain,
  the steps it follows, what it is given, what it returns and what it
  leaves alone.
- **actor**: who carries out a step: `agent`, or `human:<who>`.
- **grain**: how often a step runs: once for the `book`, once per `part`,
  or once per `chapter`.
- **step run**: one attempt at one step for one scope, a row of
  `step_runs`: running (an agent has it), waiting (a person has it), done,
  failed (kept; the step can be started again), or superseded (kept,
  replaced by a restart).
- **step event**: one change of a step run's status, a row of
  `step_events`: the run, the status before and after, who, and why.
- **step contract**: what a step asks of its role: its role, grain, the
  steps it follows, what it is given, what it returns and what it leaves
  alone. Not its actor.
- **pack**: what a role is handed when its run starts: its contract, the
  register entries in scope, what the steps before it returned, and, on a
  rerun, the superseded result as reference.
- **independent step**: a step whose reader must judge the text cold: its
  pack withholds what earlier steps returned.
- **role file**: a Claude Code subagent file for one agent role,
  `.claude/agents/book-<role slug>.md`, named in that role's packs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import docs, register, structure
from .db import Database, ko_fields, loads, now_iso

DEFINITION_DIR = "_book"
DEFINITION_FILENAME = "workflow.toml"
PACKS_DIR = "packs"
RUN_PREFIX = "sr"
EVENT_PREFIX = "se"
ROLE_FILE_PREFIX = "book-"
GRAINS = ("book", "part", "chapter")
BOOK = "book"
STEP_KEYS = ("id", "role", "actor", "grain", "after", "gives", "returns",
             "artifacts", "leaves_alone", "independent")
_REQUIRED_KEYS = ("id", "role", "actor", "grain", "returns", "leaves_alone")

# The named inputs a step's `gives` may list, and where each one is. A step
# id is also a valid name: it means what that step returned.
GIVES = {
    "brief": "the book's brief: the register entries of kind "
             + ", ".join(register.BRIEF_KINDS) + ", below",
    "register": "every register entry in scope, below",
    "outline": "the book's outline: {toc}, and the register's shape and "
               "thesis entries, below",
    "chapter": "the chapter: {chapter}",
    "part": "the part's chapters, in reading order: {part}",
    "manuscript": "every chapter, in the reading order of {toc}, under "
                  "{root}",
    "summaries": "the summaries of what is written so far: authorlm "
                 "summary outline -m {name}",
}
_GIVES_GRAINS = {"chapter": ("chapter",), "part": ("part", "chapter")}

_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_ACTOR = re.compile(r"agent|human:[a-z0-9]+(?:-[a-z0-9]+)*")


# ---------------------------------------------------------------- definition

@dataclass(frozen=True, slots=True)
class Step:
    id: str
    role: str
    actor: str
    grain: str
    after: tuple[str, ...]
    gives: tuple[str, ...]
    returns: str
    artifacts: tuple[str, ...]
    leaves_alone: str
    independent: bool


def contract_hash(step: Step) -> str:
    """A fingerprint of what a step asks of its role: everything in its
    definition but its id and its actor (who holds a role is not part of
    the role's contract, and a run records its actor itself)."""
    contract = {"role": step.role, "grain": step.grain,
                "after": list(step.after), "gives": list(step.gives),
                "returns": step.returns, "artifacts": list(step.artifacts),
                "leaves_alone": step.leaves_alone,
                "independent": step.independent}
    encoded = json.dumps(contract, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class Definition:
    path: Path
    steps: tuple[Step, ...]
    text_hash: str           # a fingerprint of the whole definition file

    def step(self, step_id: str) -> Step:
        for step in self.steps:
            if step.id == step_id:
                return step
        raise LookupError(
            f"'{step_id}' is not a step of this book's workflow "
            f"({self.path}). Its steps: "
            + ", ".join(s.id for s in self.steps))


def definition_path(manuscript: dict) -> Path:
    return Path(manuscript["path"]) / DEFINITION_DIR / DEFINITION_FILENAME


def default_definition_path() -> Path:
    """`authorlm/library/book-workflow.toml` in the checkout: the workflow
    a book starts from."""
    return (Path(__file__).resolve().parent.parent / "library"
            / "book-workflow.toml")


def role_slug(role: str) -> str:
    """A role's name as a file-name slug: "copy editor" gives
    "copy-editor". The one place a role's name becomes a slug."""
    return "-".join(re.findall(r"[a-z0-9]+", role.lower()))


def role_agent_name(role: str) -> str:
    """The name of a role's subagent, and the stem of its role file:
    "copy editor" gives "book-copy-editor"."""
    return ROLE_FILE_PREFIX + role_slug(role)


def role_files_dir() -> Path:
    """`.claude/agents/` in the checkout this package runs from: where the
    role files are. The folder is absent when the package is installed
    outside a checkout; `role_file` then finds nothing."""
    return (Path(__file__).resolve().parent.parent.parent / ".claude"
            / "agents")


def role_file(role: str) -> Path | None:
    """The role file for `role` (`.claude/agents/book-<slug>.md`), or None
    when the checkout has none. A role file says how the role does its
    work with AuthorLM's verbs; a role without one is still carried out
    from its pack alone."""
    if not role_slug(role):
        return None
    try:
        path = role_files_dir() / f"{role_agent_name(role)}.md"
        return path if path.is_file() else None
    except OSError:
        return None


def _strings(table: dict, key: str, where: str) -> tuple[str, ...]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str)
                                              for v in value):
        raise ValueError(f"{where}: {key} must be a list of strings")
    return tuple(value)


def _follows(steps: dict[str, Step], step_id: str) -> set[str]:
    """Every step `step_id` follows, directly or through others."""
    seen: set[str] = set()
    pending = list(steps[step_id].after)
    while pending:
        name = pending.pop()
        if name not in seen:
            seen.add(name)
            pending.extend(steps[name].after)
    return seen


def parse_definition(text: str, path: Path) -> Definition:
    """The definition in `text`, checked: an unknown key, actor, grain,
    step name or given input is an error naming it, so a typing mistake
    never silently switches a step off."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as err:
        raise ValueError(f"{path} is not valid TOML: {err}") from err
    unknown = sorted(set(data) - {"step"})
    if unknown:
        raise ValueError(f"{path}: unknown key '{unknown[0]}'; the file "
                         "holds [[step]] tables only")
    tables = data.get("step", [])
    if not isinstance(tables, list) or not tables:
        raise ValueError(f"{path}: no [[step]] tables")
    steps: dict[str, Step] = {}
    for table in tables:
        where = f"{path}: step '{table.get('id', '?')}'"
        unknown = sorted(set(table) - set(STEP_KEYS))
        if unknown:
            raise ValueError(f"{where}: unknown key '{unknown[0]}'; the "
                             f"keys are {', '.join(STEP_KEYS)}")
        for key in _REQUIRED_KEYS:
            if not isinstance(table.get(key), str) or not table[key].strip():
                raise ValueError(f"{where}: {key} is required, as text")
        if not _SLUG.fullmatch(table["id"]):
            raise ValueError(f"{where}: an id is lower-case words joined "
                             "by hyphens")
        if table["id"] in steps:
            raise ValueError(f"{where}: two steps have this id")
        if table["id"] in GIVES:
            raise ValueError(f"{where}: this id is the name of a given "
                             "input; choose another")
        if not _ACTOR.fullmatch(table["actor"]):
            raise ValueError(f"{where}: actor '{table['actor']}' must be "
                             "agent or human:<who>")
        if table["grain"] not in GRAINS:
            raise ValueError(f"{where}: grain '{table['grain']}' must be "
                             f"one of {', '.join(GRAINS)}")
        if not isinstance(table.get("independent", False), bool):
            raise ValueError(f"{where}: independent must be true or false")
        steps[table["id"]] = Step(
            id=table["id"], role=table["role"], actor=table["actor"],
            grain=table["grain"], after=_strings(table, "after", where),
            gives=_strings(table, "gives", where), returns=table["returns"],
            artifacts=_strings(table, "artifacts", where),
            leaves_alone=table["leaves_alone"],
            independent=table.get("independent", False))
    for step in steps.values():
        where = f"{path}: step '{step.id}'"
        for name in step.after:
            if name not in steps:
                raise ValueError(f"{where}: after names '{name}', which is "
                                 "not a step")
    for step in steps.values():
        where = f"{path}: step '{step.id}'"
        followed = _follows(steps, step.id)
        if step.id in followed:
            raise ValueError(f"{where}: its after list leads back to "
                             "itself in a circle")
        for name in step.gives:
            if name in GIVES:
                grains = _GIVES_GRAINS.get(name)
                if grains and step.grain not in grains:
                    raise ValueError(
                        f"{where}: gives '{name}', which only a step of "
                        f"grain {' or '.join(grains)} can be given")
            elif name not in steps:
                raise ValueError(
                    f"{where}: gives '{name}', which is neither a step nor "
                    f"one of {', '.join(GIVES)}")
            elif step.independent:
                raise ValueError(
                    f"{where}: an independent step is not given what "
                    f"another step returned ('{name}')")
            elif name not in followed:
                raise ValueError(
                    f"{where}: gives '{name}', a step it does not follow; "
                    "add it to after")
    return Definition(
        path=path, steps=tuple(steps.values()),
        text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:12])


def _require_document_ids(db: Database, manuscript: dict) -> None:
    if not docs.document_ids_enabled(db, manuscript):
        raise ValueError(
            "the book workflow keeps its record by document id, and "
            "document ids are off for this manuscript. Turn them on: "
            f"authorlm manuscript set --document-ids on -m "
            f"{manuscript['name']}")


def load_definition(manuscript: dict) -> Definition:
    path = definition_path(manuscript)
    if not path.is_file():
        raise LookupError(
            f"this book has no workflow definition ({path}). Write the "
            f"default one: authorlm book init -m {manuscript['name']}")
    return parse_definition(path.read_text(encoding="utf-8"), path)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def init_definition(db: Database, manuscript: dict, *,
                    overwrite: bool = False) -> dict:
    """Write the shipped default definition into the book; returns
    `{path, overwrote}`. A definition already there is the book's own and
    is left as it is unless `overwrite`."""
    _require_document_ids(db, manuscript)
    path = definition_path(manuscript)
    exists = path.is_file()
    if exists and not overwrite:
        raise ValueError(
            f"this book already has a workflow definition ({path}), and it "
            "may hold the book's own changes. To replace it with the "
            f"default: authorlm book init --overwrite -m "
            f"{manuscript['name']}")
    text = default_definition_path().read_text(encoding="utf-8")
    parse_definition(text, default_definition_path())
    _write_atomic(path, text)
    return {"path": str(path), "overwrote": exists}


def assign(db: Database, manuscript: dict, step_id: str, actor: str) -> dict:
    """Give a step to an agent or to a named person, by changing its
    `actor` line in the definition file and nothing else; returns `{step,
    was, actor, path}`. Runs already started keep the actor they had."""
    _require_document_ids(db, manuscript)
    if not _ACTOR.fullmatch(actor or ""):
        raise ValueError(f"'{actor}' is not an actor: give agent or "
                         "human:<who>, e.g. human:author")
    definition = load_definition(manuscript)
    step = definition.step(step_id)
    text = definition.path.read_text(encoding="utf-8")
    blocks = re.split(r"(?m)^(?=\[\[step\]\])", text)
    id_line = re.compile(rf'(?m)^id\s*=\s*"{re.escape(step_id)}"\s*$')
    actor_line = re.compile(r'(?m)^actor\s*=\s*"[^"]*"')
    for index, block in enumerate(blocks):
        if id_line.search(block):
            blocks[index], count = actor_line.subn(f'actor = "{actor}"',
                                                   block, count=1)
            if count != 1:
                raise ValueError(
                    f"{definition.path}: step '{step_id}' has no plain "
                    'actor = "…" line to change; edit the file by hand')
            break
    changed = "".join(blocks)
    parse_definition(changed, definition.path)
    if changed != text:
        _write_atomic(definition.path, changed)
    return {"step": step_id, "was": step.actor, "actor": actor,
            "path": str(definition.path)}


# -------------------------------------------------------------------- scopes

@dataclass(frozen=True, slots=True)
class Scope:
    grain: str
    key: str                 # "book", or the `dc-` id of the document
    label: str               # "the book", or the document's file name


@dataclass(frozen=True, slots=True)
class Layout:
    """The book's parts and chapters, in reading order, from `toc.toml`."""
    chapters: tuple[Scope, ...]
    parts: tuple[Scope, ...]
    part_of: dict[str, str]              # chapter key -> its part's key
    not_yet_documents: tuple[str, ...]   # TOC files with no document yet

    def scopes(self, grain: str) -> tuple[Scope, ...]:
        if grain == "book":
            return (Scope("book", BOOK, "the book"),)
        return self.parts if grain == "part" else self.chapters


def layout(db: Database, manuscript: dict) -> Layout:
    toc_path = Path(manuscript["path"]) / structure.TOC_FILENAME
    toc = toc_path.read_text(encoding="utf-8") if toc_path.is_file() else ""
    attributes = structure.toc_attrs(toc)
    outside = structure.toc_outside(toc)
    ids = {d["relpath"]: d["id"] for d in docs.list_documents(db, manuscript)
           if d["status"] == "active"}
    entries: list[tuple[str, int]] = []
    missing: list[str] = []
    for name, depth in structure.parse_toc_tree(toc):
        if (attributes.get(name, {}).get("matter") in ("front", "back")
                or structure.is_outside(name, outside)):
            continue
        if name in ids:
            entries.append((name, depth))
        else:
            missing.append(name)
    chapters: list[Scope] = []
    parts: list[Scope] = []
    part_of: dict[str, str] = {}
    before_any_part: list[str] = []
    for index, (name, depth) in enumerate(entries):
        opens_part = (depth == 0 and index + 1 < len(entries)
                      and entries[index + 1][1] > 0)
        if opens_part:
            parts.append(Scope("part", ids[name], name))
            for key in before_any_part:
                part_of[key] = ids[name]
            before_any_part = []
            continue
        chapters.append(Scope("chapter", ids[name], name))
        if parts:
            part_of[ids[name]] = parts[-1].key
        else:
            before_any_part.append(ids[name])
    if chapters and not parts:
        parts.append(Scope("part", BOOK, "the book"))
        part_of = {key: BOOK for key in before_any_part}
    return Layout(chapters=tuple(chapters), parts=tuple(parts),
                  part_of=part_of, not_yet_documents=tuple(missing))


def _related_scopes(book_layout: Layout, grain: str, key: str,
                    other_grain: str) -> list[str]:
    """The scopes of `other_grain` that hold, are, or lie inside the scope
    `key` of `grain`."""
    if other_grain == grain:
        return [key]
    if other_grain == "book":
        return [BOOK]
    if grain == "book":
        return [scope.key for scope in book_layout.scopes(other_grain)]
    if grain == "part":
        return [chapter.key for chapter in book_layout.chapters
                if book_layout.part_of[chapter.key] == key]
    return [book_layout.part_of[key]]


def _resolve_scope(book_layout: Layout, step: Step,
                   scope: str | None) -> Scope:
    """The scope a verb names for `step`: None or `book` for a book step,
    a `dc-` id or a file name for a part or chapter step."""
    choices = book_layout.scopes(step.grain)
    if step.grain == "book":
        if scope in (None, BOOK):
            return choices[0]
        raise ValueError(f"{step.id} runs once for the whole book; leave "
                         "--scope out")
    if scope is None and step.grain == "part" and len(choices) == 1:
        return choices[0]
    names = (", ".join(c.label for c in choices)
             or f"none yet (no {step.grain} in toc.toml has a document)")
    if scope is None:
        raise ValueError(f"{step.id} runs once per {step.grain}: name one "
                         f"with --scope <file or dc- id>. They are: {names}")
    for choice in choices:
        if scope in (choice.key, choice.label):
            return choice
    extra = (" Front matter, back matter, part pages and files declared "
             "outside the book are not chapters."
             if step.grain == "chapter" else "")
    raise ValueError(f"{scope} is not a {step.grain} of this book.{extra} "
                     f"They are: {names}")


# ---------------------------------------------------------------------- runs

def _label(db: Database, document_id: str | None) -> str:
    if document_id is None:
        return "the book"
    try:
        return docs.document_relpath(db, document_id)
    except LookupError:
        return document_id


def _run(db: Database, row) -> dict:
    return {"id": row["id"], "step": row["step"],
            "scope": row["scope_document_id"] or BOOK,
            "scope_label": _label(db, row["scope_document_id"]),
            "grain": row["scope_kind"], "attempt": row["attempt"],
            "status": row["status"], "actor": row["actor"],
            "note": row["note"], "result": loads(row["result"], {}),
            "started_at": row["created_at"],
            "finished_at": row["finished_at"],
            "superseded_by": row["superseded_by"],
            "superseded_reason": row["superseded_reason"],
            "failed_reason": row["failed_reason"],
            "definition_hash": row["definition_hash"],
            "contract_hash": row["contract_hash"]}


def _transition(db: Database, manuscript: dict, to_status: str, *,
                actor: str, reason: str = "", run: dict | None = None,
                new_row: dict | None = None,
                changes: dict | None = None) -> None:
    """Change a run's status and log the change, as one transaction. This
    is the only place a run's status is written (a new run included), so
    no status changes without a row in `step_events`; that table is only
    ever appended to. `new_row` starts a run; `run` moves an existing one,
    with any other columns that change with it in `changes`."""
    with db.transaction():
        if new_row is not None:
            db.insert("step_runs", {**new_row, "status": to_status})
            run_id, step, kind = (new_row["id"], new_row["step"],
                                  new_row["scope_kind"])
            document_id, before = new_row["scope_document_id"], None
        else:
            db.update("step_runs", run["id"],
                      {**(changes or {}), "status": to_status})
            run_id, step, kind = run["id"], run["step"], run["grain"]
            document_id = None if run["scope"] == BOOK else run["scope"]
            before = run["status"]
        db.insert("step_events", {
            **ko_fields(EVENT_PREFIX), "manuscript_id": manuscript["id"],
            "run_id": run_id, "step": step, "scope_kind": kind,
            "scope_document_id": document_id, "from_status": before,
            "to_status": to_status, "actor": actor, "reason": reason})


def _runs(db: Database, manuscript: dict) -> dict[tuple[str, str], list[dict]]:
    """Every run of the manuscript, by (step id, scope key), oldest
    attempt first."""
    out: dict[tuple[str, str], list[dict]] = {}
    for row in db.all(
            "SELECT * FROM step_runs WHERE manuscript_id = ? "
            "ORDER BY attempt, created_at", (manuscript["id"],)):
        run = _run(db, row)
        out.setdefault((run["step"], run["scope"]), []).append(run)
    return out


def _current(runs: dict, step_id: str, key: str) -> dict | None:
    """The run in force for a step and scope: its latest attempt, unless
    that was superseded."""
    attempts = runs.get((step_id, key)) or []
    if attempts and attempts[-1]["status"] != "superseded":
        return attempts[-1]
    return None


def _unmet(definition: Definition, book_layout: Layout, runs: dict,
           step: Step, key: str) -> list[dict]:
    """What `step` still waits for at scope `key`: each step it follows
    that is not done for the scopes this one rests on. A step that follows
    a finer-grained one waits until there is at least one such scope."""
    waiting: list[dict] = []
    for name in step.after:
        earlier = definition.step(name)
        keys = _related_scopes(book_layout, step.grain, key, earlier.grain)
        if not keys:
            waiting.append({"step": name, "scope": None})
        for other in keys:
            run = _current(runs, name, other)
            if run is None or run["status"] != "done":
                waiting.append({"step": name, "scope": other})
    return waiting


def _rests_on_restarted(definition: Definition, book_layout: Layout,
                        runs: dict, step: Step, run: dict) -> bool:
    """True for a done run when a step it followed has since been
    restarted: that step is no longer done, or was done again later."""
    if run["status"] != "done":
        return False
    if _unmet(definition, book_layout, runs, step, run["scope"]):
        return True
    for name in step.after:
        earlier = definition.step(name)
        for other in _related_scopes(book_layout, step.grain, run["scope"],
                                     earlier.grain):
            if _current(runs, name, other)["started_at"] > run["started_at"]:
                return True
    return False


def _cell(definition: Definition, book_layout: Layout, runs: dict,
          step: Step, key: str) -> dict:
    """A step's state for one scope, as every board and the diagram show
    it: `state` is the status of the run in force (running, waiting, done,
    failed), or, with no run in force, blocked (a step it follows is not
    done) or ready. A superseded run is never in force, so superseded is
    never a state; `earlier_attempts` counts the attempts before this
    one."""
    run = _current(runs, step.id, key)
    attempts = runs.get((step.id, key)) or []
    unmet = [] if run else _unmet(definition, book_layout, runs, step, key)
    return {
        "state": run["status"] if run else ("blocked" if unmet else "ready"),
        "run": run["id"] if run else None,
        "attempt": run["attempt"] if run else None,
        "earlier_attempts": len(attempts) - (1 if run else 0),
        "actor": run["actor"] if run else step.actor, "blocked_by": unmet,
        "rests_on_restarted": bool(run) and _rests_on_restarted(
            definition, book_layout, runs, step, run),
        "contract_changed": bool(run) and (
            run["contract_hash"] != contract_hash(step))}


def waited_on(actor: str) -> str | None:
    """The person an actor names, as the board says it: `the author` for
    `human:author`, the name for anyone else, None for an agent."""
    if actor == "agent":
        return None
    who = actor.split(":", 1)[1]
    return "the author" if who == "author" else who


def _start_command(manuscript: dict, step: Step, scope: Scope) -> str:
    where = "" if step.grain == "book" or scope.key == BOOK \
        else f" --scope {scope.label}"
    return f"authorlm book start {step.id}{where} -m {manuscript['name']}"


def status(db: Database, manuscript: dict) -> dict:
    """Every step with its state for every scope, and who is waited on.
    A state is blocked (a step it follows is not done), ready, running (an
    agent has it), waiting (a person has it) or done."""
    _require_document_ids(db, manuscript)
    definition = load_definition(manuscript)
    book_layout = layout(db, manuscript)
    runs = _runs(db, manuscript)
    steps: list[dict] = []
    waiting_on: list[dict] = []
    counts: dict[str, int] = {}
    first_ready: str | None = None
    for step in definition.steps:
        cells: list[dict] = []
        for scope in book_layout.scopes(step.grain):
            cell = _cell(definition, book_layout, runs, step, scope.key)
            state, actor = cell["state"], cell["actor"]
            counts[state] = counts.get(state, 0) + 1
            cells.append({"scope": scope.key, "label": scope.label, **cell})
            who = waited_on(actor)
            if who and state in ("waiting", "ready"):
                waiting_on.append({"who": who, "step": step.id,
                                   "scope": scope.key, "label": scope.label,
                                   "asked": state == "waiting",
                                   "run": cell["run"]})
            if state in ("ready", "failed") and first_ready is None:
                first_ready = _start_command(manuscript, step, scope)
        steps.append({"id": step.id, "role": step.role, "actor": step.actor,
                      "grain": step.grain, "independent": step.independent,
                      "scopes": cells})
    warnings = [f"{name} is in toc.toml but is not a document yet, so no "
                "step runs for it: authorlm collect"
                for name in book_layout.not_yet_documents]
    if not book_layout.chapters:
        warnings.append("no chapter yet: a chapter is a main-matter file "
                        "of toc.toml that has been collected")
    defined = {step.id for step in definition.steps}
    for (step_id, _key), attempts in runs.items():
        last = attempts[-1]
        if step_id not in defined and last["status"] != "superseded":
            warnings.append(
                f"run {last['id']} ({last['status']}, {last['scope_label']}) "
                f"is of step '{step_id}', which is no longer in the "
                "definition; it is kept and counts for nothing")
    asked = [w for w in waiting_on if w["asked"]]
    if first_ready:
        hint = first_ready
    elif asked:
        hint = (f"waiting on {asked[0]['who']}; when they have answered: "
                f"authorlm book done {asked[0]['run']} -m "
                f"{manuscript['name']}")
    elif counts.get("running"):
        hint = ("a step is running; record its result with: authorlm book "
                "done <run-id>")
    else:
        hint = "every step is done"
    return {"manuscript": manuscript["name"],
            "definition": str(definition.path), "steps": steps,
            "waiting_on": waiting_on, "counts": counts,
            "warnings": warnings, "next": hint}


def next_runs(db: Database, manuscript: dict) -> dict:
    """The step runs that can start now (`ready`: every step they follow
    is done for their scope) and those under way (`in_progress`). Reads
    only; nothing is started or marked."""
    board = status(db, manuscript)
    definition = load_definition(manuscript)
    book_layout = layout(db, manuscript)
    ready: list[dict] = []
    in_progress: list[dict] = []
    for row in board["steps"]:
        step = definition.step(row["id"])
        for cell in row["scopes"]:
            entry = {"step": step.id, "role": step.role,
                     "scope": cell["scope"], "label": cell["label"],
                     "actor": cell["actor"],
                     "waiting_on": waited_on(cell["actor"])}
            if cell["state"] in ("ready", "failed"):
                scope = next(s for s in book_layout.scopes(step.grain)
                             if s.key == cell["scope"])
                ready.append({**entry, "command": _start_command(
                    manuscript, step, scope)})
            elif cell["state"] in ("running", "waiting"):
                in_progress.append({**entry, "run": cell["run"],
                                    "state": cell["state"]})
    return {"manuscript": manuscript["name"], "ready": ready,
            "in_progress": in_progress, "next": board["next"]}


# --------------------------------------------------------------------- packs

def _fenced(value: dict) -> str:
    return ("```json\n" + json.dumps(value, indent=2, ensure_ascii=False)
            + "\n```")


def _entry_lines(entry: dict, db: Database) -> list[str]:
    where = ("the whole book" if entry["scope"] is None
             else _label(db, entry["scope"]))
    lines = [f"### {entry['kind']}/{entry['key']} ({entry['id']}, {where})",
             "", f"- Author's words: {entry['author_words'] or '(none yet)'}",
             f"- Reading: {entry['reading'] or '(none yet)'}"]
    if entry["test"]:
        lines.append(f"- Test: {entry['test']}")
    return lines + [""]


def _register_section(db: Database, manuscript: dict, book_layout: Layout,
                      scope: Scope, run_id: str,
                      must_declare_uses: bool) -> list[str]:
    if scope.grain == "book":
        document_ids = None
    elif scope.grain == "chapter":
        document_ids = [scope.key]
    else:
        document_ids = [scope.key] + _related_scopes(
            book_layout, "part", scope.key, "chapter")
    entries = register.in_scope(db, manuscript, document_ids)
    lines = [
        "## The register: what the author has settled", "",
        "Each entry is the author's words, verbatim, and the machine's "
        "reading of them. Keeping to a settled entry is your work; a new "
        "choice is the author's. For every entry your work rests on, "
        "a use is recorded that says which it is: `applies` when you do "
        "what the entry says, `infers` when you go beyond what it says.", ""]
    if must_declare_uses:
        lines += [
            "**Required.** This run cannot be marked done unless its result "
            f"says what rested on the register. Put `{USES_KEY}` in the "
            "result you hand back: a list with one item for each entry your "
            "work rested on,", "",
            '    {"entry": "<kind/key or id>", "mode": "applies" or '
            '"infers", "note": "<what rests on it>", "document": "<the '
            'chapter file, optional>"}', "",
            f'or, if your work rested on no entry, `"{USES_KEY}": "none"` '
            f'with a `{USES_REASON_KEY}` saying why. `book done` records '
            "each use under this run, so do not also record them yourself.",
            "",
            "An inference is fine, and often needed: the author cannot "
            "settle everything in advance. It must be labelled `infers`, so "
            "that the author can tell what they chose from what you chose "
            "for them. Never label an inference `applies`.", ""]
    else:
        lines += [
            "Record each use as you go, or list them in your result under "
            f"`{USES_KEY}` (items of entry, mode, note) and `book done` "
            "records them. An inference is fine; it must be labelled "
            "`infers`.", "",
            f"    authorlm register use <kind/key> --mode applies|infers "
            f"--actor <who> --step-run {run_id} --note '<what rests on it>' "
            f"-m {manuscript['name']}", ""]
    groups = (("settled", "Settled"),
              ("proposed", "Proposed, not settled: the author has not "
                           "agreed to these; no work rests on them"),
              ("reopened", "Reopened: the author is reconsidering these; "
                           "no work rests on them until they are settled "
                           "again"))
    for status_name, heading in groups:
        chosen = [e for e in entries if e["status"] == status_name]
        if status_name == "settled" and not chosen:
            lines += ["Nothing is settled in this scope yet.", ""]
        if chosen and status_name != "settled":
            lines += [f"**{heading}**", ""]
        for entry in chosen:
            lines += _entry_lines(entry, db)
    return lines


def _handed_over(definition: Definition, book_layout: Layout, runs: dict,
                 step: Step, scope: Scope) -> list[str]:
    lines = ["## From the steps before", ""]
    if step.independent:
        return lines + [
            "This is an independent read. What the steps before it "
            "returned for this scope (drafting notes, other reviews) is "
            "withheld from this pack on purpose, so that you judge the "
            "text cold. Do not go looking for it.", ""]
    names = list(dict.fromkeys(step.after + tuple(
        name for name in step.gives if name not in GIVES)))
    handed = 0
    for name in names:
        earlier = definition.step(name)
        for key in _related_scopes(book_layout, step.grain, scope.key,
                                   earlier.grain):
            run = _current(runs, name, key)
            if run is None or run["status"] != "done":
                continue
            handed += 1
            lines += [f"### {name} ({earlier.role}), {run['scope_label']}, "
                      f"run {run['id']}", "", _fenced(run["result"]), ""]
    if not handed:
        lines += ["Nothing: no step comes before this one.", ""]
    return lines


def _reference(step: Step, earlier: list[dict]) -> list[str]:
    """The superseded attempts at this step and scope, for a rerun."""
    if not earlier:
        return []
    latest = earlier[-1]
    reason = (latest["superseded_reason"] or latest["failed_reason"]
              or "no reason was recorded")
    how = "failed" if latest["failed_reason"] else "was restarted"
    if step.independent:
        return [
            "## Earlier attempt: withheld", "",
            f"This step was run before (run {latest['id']}) and {how}: "
            f"{reason}. Because this is an independent read, the earlier "
            "result is withheld from this pack.", ""]
    return [
        "## Earlier attempt: reference only", "",
        f"This step was run before (run {latest['id']}, attempt "
        f"{latest['attempt']}) and {how}. The reason given: {reason}.",
        "",
        "What it returned is below as reference only. It has no authority "
        "over your work: it is not the standard, and you are not asked to "
        "keep, match or repair it. Use it only where it helps.", "",
        _fenced(latest["result"]), ""]


def _given(manuscript: dict, book_layout: Layout, step: Step,
           scope: Scope) -> list[str]:
    root = Path(manuscript["path"])
    part_key = (scope.key if scope.grain == "part"
                else book_layout.part_of.get(scope.key))
    part_files = [str(root / chapter.label)
                  for chapter in book_layout.chapters
                  if book_layout.part_of[chapter.key] == part_key]
    places = {"toc": str(root / structure.TOC_FILENAME), "root": str(root),
              "name": manuscript["name"],
              "chapter": str(root / scope.label),
              "part": ", ".join(part_files)}
    lines = []
    for name in step.gives:
        if name in GIVES:
            lines.append(f"  - {name}: {GIVES[name].format(**places)}")
        else:
            lines.append(f"  - {name}: what that step returned, below")
    return lines or ["  - nothing by name"]


def _pack(db: Database, manuscript: dict, definition: Definition,
          book_layout: Layout, runs: dict, step: Step, scope: Scope,
          run: dict, earlier: list[dict]) -> str:
    who = waited_on(run["actor"])
    lines = [
        f"# Pack: {step.id} ({step.role}), {scope.label}, attempt "
        f"{run['attempt']}", "",
        f"Run {run['id']} of the book workflow for {manuscript['name']}. "
        + (f"This step is {who}'s: ask them, and record what they say in "
           "their own words." if who else "This step is an agent's."), ""]
    role_path = None if who else role_file(step.role)
    if role_path:
        lines += [
            f"Role file: {role_path}", "",
            f"Hand this step to the subagent `{role_agent_name(step.role)}`"
            ", giving it the path of this pack "
            f"({pack_path(manuscript, run['id'])}). The role file says how "
            "the role does its work; the contract below is the one in "
            "force for this book.", ""]
    lines += [
        "## Your contract", "",
        f"- Role: {step.role}", "- You are given:",
        *_given(manuscript, book_layout, step, scope),
        f"- You hand back: {step.returns}"]
    if step.artifacts:
        lines.append("- Files you write: " + ", ".join(step.artifacts))
    lines += [
        f"- You leave alone: {step.leaves_alone}",
        "- Independent: " + (
            "yes. You are not handed what earlier steps returned."
            if step.independent else "no"), "",
        "You may read the whole manuscript and anything else in the "
        "record: this pack lists what you are handed, not what you may "
        "read. A problem that belongs to an earlier step is filed back in "
        "your result, not fixed here.", ""]
    lines += _register_section(
        db, manuscript, book_layout, scope, run["id"],
        run["actor"] == "agent" and "register" in step.gives)
    lines += _handed_over(definition, book_layout, runs, step, scope)
    lines += _reference(step, earlier)
    lines += [
        "## When you are done", "",
        "Write what you hand back as one JSON object (suggested keys: "
        "`summary`, `artifacts` for the files you wrote, `register_entries` "
        "for the entry ids you added, `filed_back` for problems that "
        f"belong to an earlier step; and `{USES_KEY}` as the register "
        "section above says), then:", "",
        f"    authorlm book done {run['id']} --result-json <file> -m "
        f"{manuscript['name']}", ""]
    return "\n".join(lines)


def pack_path(manuscript: dict, run_id: str) -> Path:
    return (Path(manuscript["path"]) / DEFINITION_DIR / PACKS_DIR
            / f"{run_id}.md")


# --------------------------------------------------------------------- verbs

def send_notification(title: str, body: str) -> bool:
    """Tell the owner through the SuperLM notifier, when it is installed.
    Returns whether it was called; a missing or failing notifier is never
    an error, because the board says who is waited on either way."""
    program = shutil.which("superlm-notify")
    if not program:
        return False
    try:
        subprocess.run([program, "--repo", "authorllm", "--title", title,
                        "--body", body], check=False, capture_output=True,
                       timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def start(db: Database, manuscript: dict, step_id: str,
          scope: str | None = None, *, note: str = "",
          notify=None) -> dict:
    """Start the next attempt at a step for a scope and write its pack;
    returns `{run, pack_path, waiting_on, notified, role_file,
    next}`. An agent's run
    is `running`; a person's is `waiting`, and that person is notified
    (`notify(title, body)`, by default the SuperLM notifier)."""
    _require_document_ids(db, manuscript)
    definition = load_definition(manuscript)
    book_layout = layout(db, manuscript)
    step = definition.step(step_id)
    where = _resolve_scope(book_layout, step, scope)
    runs = _runs(db, manuscript)
    name = manuscript["name"]
    current = _current(runs, step.id, where.key)
    if current and current["status"] == "done":
        raise ValueError(
            f"{step.id} is already done for {where.label} (run "
            f"{current['id']}). To run it again, keeping the old result as "
            f"reference: authorlm book restart {step.id}"
            + ("" if where.key == BOOK else f" --scope {where.label}")
            + f" --reason '<why>' -m {name}")
    if current and current["status"] != "failed":
        raise ValueError(
            f"{step.id} is already {current['status']} for {where.label} "
            f"(run {current['id']}). Record its result: authorlm book done "
            f"{current['id']} -m {name}")
    unmet = _unmet(definition, book_layout, runs, step, where.key)
    if unmet:
        first = unmet[0]
        document_id = None if first["scope"] == BOOK else first["scope"]
        what = (f"{first['step']} for {_label(db, document_id)}"
                if first["scope"] else f"{first['step']}, which has nothing "
                                       "to run on yet")
        raise ValueError(
            f"{step.id} follows {what}, which is not done"
            + (f" (and {len(unmet) - 1} more)" if len(unmet) > 1 else "")
            + f". See what is next: authorlm book next -m {name}")
    earlier = runs.get((step.id, where.key)) or []
    who = waited_on(step.actor)
    row = {**ko_fields(RUN_PREFIX), "manuscript_id": manuscript["id"],
           "step": step.id, "scope_kind": step.grain,
           "scope_document_id": None if where.key == BOOK else where.key,
           "attempt": len(earlier) + 1, "actor": step.actor, "note": note,
           "definition_hash": definition.text_hash,
           "contract_hash": contract_hash(step)}
    run = {"id": row["id"], "attempt": row["attempt"], "actor": step.actor}
    path = pack_path(manuscript, row["id"])
    _write_atomic(path, _pack(db, manuscript, definition, book_layout, runs,
                              step, where, run, earlier))
    with db.transaction():
        _transition(db, manuscript, "waiting" if who else "running",
                    actor=step.actor, reason=note, new_row=row)
        if earlier:
            db.update("step_runs", earlier[-1]["id"],
                      {"superseded_by": row["id"]})
    notified = False
    if who:
        notified = bool((notify or send_notification)(
            f"{name}: waiting on {who}",
            f"The book workflow step '{step.id}' ({where.label}) is "
            f"waiting on {who}. See: authorlm book status -m {name}"))
    role_path = None if who else role_file(step.role)
    how = (f"Hand {path} to the subagent {role_agent_name(step.role)} "
           f"(role file {role_path}), or read it and follow it"
           if role_path else f"Read {path} and follow it")
    return {"run": show_run(db, manuscript, row["id"])["run"],
            "pack_path": str(path), "waiting_on": who, "notified": notified,
            "role_file": str(role_path) if role_path else None,
            "next": f"{how}; then: authorlm book done "
                    f"{row['id']} --result-json <file> -m {name}"}


def _find_run(db: Database, manuscript: dict, run_id: str):
    rows = db.all("SELECT * FROM step_runs WHERE manuscript_id = ? AND "
                  "id LIKE ?", (manuscript["id"],
                                (run_id or "").replace("%", "") + "%"))
    if len(rows) != 1:
        found = "no" if not rows else "more than one"
        raise LookupError(f"{found} step run matches '{run_id}'. See the "
                          f"runs: authorlm book status -m "
                          f"{manuscript['name']}")
    return rows[0]


def show_run(db: Database, manuscript: dict, run_id: str) -> dict:
    """One run with its result, and the register uses recorded under it:
    `{run, uses, pack_path}`."""
    _require_document_ids(db, manuscript)
    row = _find_run(db, manuscript, run_id)
    uses = db.all("SELECT * FROM register_uses WHERE step_run_id = ? "
                  "ORDER BY created_at, id", (row["id"],))
    return {"run": _run(db, row),
            "uses": [{"entry_id": u["entry_id"], "mode": u["mode"],
                      "actor": u["actor"], "note": u["note"],
                      "document_id": u["document_id"]} for u in uses],
            "pack_path": str(pack_path(manuscript, row["id"]))}


def finish(db: Database, manuscript: dict, run_id: str,
           result: dict | None = None) -> dict:
    """Record what a run handed back and mark it done; returns the run.
    `result` is a JSON object; `artifacts` in it is a list of paths and
    `register_entries` a list of entry ids that exist."""
    _require_document_ids(db, manuscript)
    row = _find_run(db, manuscript, run_id)
    result = {} if result is None else result
    if not isinstance(result, dict):
        raise ValueError("a result is one JSON object, e.g. "
                         '{"summary": "…", "artifacts": ["one.md"]}')
    if row["status"] == "done":
        raise ValueError(
            f"{row['id']} is already done. To run the step again: authorlm "
            f"book restart {row['step']} --reason '<why>'")
    _require_open(row)
    for key in ("artifacts", "register_entries"):
        value = result.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str)
                                                  for v in value):
            raise ValueError(f"result.{key} must be a list of strings")
    for entry_id in result.get("register_entries", []):
        if not db.one("SELECT id FROM register_entries WHERE id = ? AND "
                      "manuscript_id = ?", (entry_id, manuscript["id"])):
            raise LookupError(f"result.register_entries names '{entry_id}', "
                              "which is not a register entry of this "
                              "manuscript. List them: authorlm register list")
    step = next((s for s in load_definition(manuscript).steps
                 if s.id == row["step"]), None)
    uses = _register_uses(result, row, step)
    who = (f"agent:{step.role}" if step and row["actor"] == "agent"
           else row["actor"])
    with db.transaction():
        for item in uses:
            register.use(db, manuscript, item["entry"], mode=item["mode"],
                         actor=who, note=item["note"],
                         step_run_id=row["id"],
                         document=item.get("document"))
        _transition(db, manuscript, "done", actor=row["actor"],
                    run=_run(db, row),
                    changes={"finished_at": now_iso(),
                             "result": json.dumps(result,
                                                  ensure_ascii=False)})
    return show_run(db, manuscript, row["id"])["run"]


USES_KEY = "register_uses"
USES_REASON_KEY = "register_uses_reason"


def _register_uses(result: dict, row, step: Step | None) -> list[dict]:
    """The register uses a result declares, checked for shape; the caller
    writes them. An agent's run of a step that is given the register must
    declare them: a list of `{entry, mode, note, document?}`, or the word
    `none` with a reason. A person's step, and a step not given the
    register, may leave the key out."""
    name = row["step"]
    required = (row["actor"] == "agent" and step is not None
                and "register" in step.gives)
    how = (f'add "{USES_KEY}" to the result: a list of {{"entry": '
           '"<kind/key or id>", "mode": "applies" or "infers", "note": '
           '"<what rests on it>"}, with an optional "document"; or '
           f'"{USES_KEY}": "none" with a "{USES_REASON_KEY}". An inference '
           'is fine; label it infers')
    if USES_KEY not in result:
        if required:
            raise ValueError(
                f"{name} was given the register, so its result must say "
                f"which entries the work rested on, and it has no "
                f"{USES_KEY}. To fix: {how}")
        return []
    declared = result[USES_KEY]
    if declared == "none":
        reason = result.get(USES_REASON_KEY)
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(
                f'{USES_KEY} is "none" but there is no {USES_REASON_KEY}: '
                "say in a sentence why the work rested on no entry")
        return []
    if not isinstance(declared, list) or not declared:
        raise ValueError(
            f"{USES_KEY} must be a list with at least one use, or the "
            f"word none. To fix: {how}")
    for item in declared:
        if not isinstance(item, dict):
            raise ValueError(f"each item of {USES_KEY} is an object. "
                             f"To fix: {how}")
        for key in ("entry", "note"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError(f"an item of {USES_KEY} has no {key}. "
                                 f"To fix: {how}")
        if item.get("mode") not in register.MODES:
            raise ValueError(
                f"the use of {item['entry']} has mode "
                f"'{item.get('mode')}'; it must be applies or infers")
        if not isinstance(item.get("document", ""), str):
            raise ValueError(f"the use of {item['entry']}: document is a "
                             "file name or a dc- id")
    return declared


def _require_open(row) -> None:
    """A run takes a result or a failure only while it is running or
    waiting."""
    if row["status"] == "superseded":
        raise ValueError(
            f"{row['id']} was superseded by a restart "
            f"({row['superseded_reason']}), so it is closed. Start the step "
            f"again: authorlm book start {row['step']}")
    if row["status"] == "failed":
        raise ValueError(
            f"{row['id']} failed ({row['failed_reason']}), so it is closed. "
            f"Start the step again: authorlm book start {row['step']}")


def fail(db: Database, manuscript: dict, run_id: str, *,
         reason: str) -> dict:
    """Mark a running or waiting run failed, with the reason; returns the
    run. A failed run is kept. The steps that follow it stay blocked, and
    the step can be started again: the next attempt is told why this one
    failed."""
    _require_document_ids(db, manuscript)
    if not (reason or "").strip():
        raise ValueError("a failure records its reason: --reason '<what "
                         "went wrong>'")
    row = _find_run(db, manuscript, run_id)
    if row["status"] == "done":
        raise ValueError(
            f"{row['id']} is already done. To run the step again: authorlm "
            f"book restart {row['step']} --reason '<why>'")
    _require_open(row)
    _transition(db, manuscript, "failed", actor=row["actor"],
                reason=reason.strip(), run=_run(db, row),
                changes={"finished_at": now_iso(),
                         "failed_reason": reason.strip()})
    return show_run(db, manuscript, row["id"])["run"]


def restart(db: Database, manuscript: dict, step_id: str,
            scope: str | None = None, *, reason: str,
            dry_run: bool = False) -> dict:
    """Supersede a step's run in force and every run that followed from
    it, for the same scope and the scopes inside it; returns `{step,
    reason, dry_run, superseded}`. Nothing is deleted: each run keeps its
    result, and the next `start` hands it to the rerun as reference. A
    part or chapter step with no `scope` is restarted for every scope it
    has run for. `dry_run` lists the runs and writes nothing."""
    _require_document_ids(db, manuscript)
    if not (reason or "").strip():
        raise ValueError("a restart records its reason: --reason '<what "
                         "was wrong>'")
    definition = load_definition(manuscript)
    book_layout = layout(db, manuscript)
    step = definition.step(step_id)
    if scope is None and step.grain != "book":
        keys = [s.key for s in book_layout.scopes(step.grain)]
    else:
        keys = [_resolve_scope(book_layout, step, scope).key]
    runs = _runs(db, manuscript)
    reached: set[tuple[str, str]] = set()
    pending = [(step.id, key) for key in keys]
    while pending:
        name, key = pending.pop()
        if (name, key) in reached:
            continue
        reached.add((name, key))
        grain = definition.step(name).grain
        for later in definition.steps:
            if name in later.after and (GRAINS.index(later.grain)
                                        >= GRAINS.index(grain)):
                pending.extend(
                    (later.id, inner) for inner in _related_scopes(
                        book_layout, grain, key, later.grain))
    order = {s.id: n for n, s in enumerate(definition.steps)}
    superseded = sorted(
        (run for name, key in reached
         if (run := _current(runs, name, key)) is not None),
        key=lambda run: (order[run["step"]], run["scope_label"]))
    if not superseded:
        raise ValueError(
            f"nothing to restart: {step.id} has no run in force"
            + (f" for {scope}" if scope else "")
            + f". See the runs: authorlm book status -m {manuscript['name']}")
    if not dry_run:
        with db.transaction():
            for run in superseded:
                _transition(db, manuscript, "superseded", actor="restart",
                            reason=reason.strip(), run=run,
                            changes={"superseded_reason": reason.strip()})
        superseded = [show_run(db, manuscript, run["id"])["run"]
                      for run in superseded]
    return {"step": step.id, "reason": reason.strip(), "dry_run": dry_run,
            "superseded": superseded,
            "next": f"authorlm book next -m {manuscript['name']}"}


def _scope_key(db: Database, manuscript: dict, scope: str) -> str:
    """`book`, a `dc-` id or a file name -> the scope key."""
    if scope == BOOK or scope.startswith("dc-"):
        return scope
    return docs.document_id(db, manuscript, scope)


def history(db: Database, manuscript: dict, *, scope: str | None = None,
            step: str | None = None) -> dict:
    """The log of every status change of every run, oldest first:
    `{events, counts}`, narrowed to one step or one scope when given.
    Read from `step_events`, which is only ever appended to."""
    _require_document_ids(db, manuscript)
    key = _scope_key(db, manuscript, scope) if scope else None
    events = []
    counts: dict[str, int] = {}
    for row in db.all(
            "SELECT rowid, * FROM step_events WHERE manuscript_id = ? "
            "ORDER BY created_at, rowid", (manuscript["id"],)):
        event_scope = row["scope_document_id"] or BOOK
        if (step and row["step"] != step) or (key and event_scope != key):
            continue
        counts[row["to_status"]] = counts.get(row["to_status"], 0) + 1
        events.append({
            "at": row["created_at"], "run": row["run_id"],
            "step": row["step"], "grain": row["scope_kind"],
            "scope": event_scope,
            "scope_label": _label(db, row["scope_document_id"]),
            "from_status": row["from_status"],
            "to_status": row["to_status"], "actor": row["actor"],
            "reason": row["reason"]})
    return {"manuscript": manuscript["name"], "events": events,
            "counts": counts,
            "next": f"authorlm book status -m {manuscript['name']}"}


# ------------------------------------------------------------------- diagram

# The look of each mark and status in the diagram. A node's shape also
# says who does the step: a rounded box for a person, a hexagon for an
# independent reader, a plain box for an agent.
DIAGRAM_CLASSES = {
    "human": "stroke-width:3px",
    "independent": "stroke-dasharray:5 5",
    "blocked": "fill:#eeeeee,stroke:#9e9e9e",
    "ready": "fill:#fff8e1,stroke:#f9a825",
    "running": "fill:#e3f2fd,stroke:#1565c0",
    "waiting": "fill:#fce4ec,stroke:#ad1457",
    "done": "fill:#e8f5e9,stroke:#2e7d32",
    "failed": "fill:#ffebee,stroke:#c62828",
    # A second mark, beside the state: the step was run before and
    # restarted, or failed and was started again.
    "reran": "stroke:#6a1b9a,stroke-width:3px",
}
_GRAIN_TITLES = {"book": "book: once for the whole book",
                 "part": "part: once for each part",
                 "chapter": "chapter: once for each chapter"}


def _diagram_scope(db: Database, manuscript: dict, book_layout: Layout,
                   scope: str | None) -> dict[str, str | None]:
    """The scope key each grain's steps are shown at, for the scope asked
    for: a chapter also fixes its part; a part leaves the chapter steps
    without a state; the book fixes only the book steps."""
    keys: dict[str, str | None] = {"book": None, "part": None,
                                   "chapter": None}
    if scope is None:
        return keys
    key = _scope_key(db, manuscript, scope)
    keys["book"] = BOOK
    if any(chapter.key == key for chapter in book_layout.chapters):
        keys["chapter"] = key
        keys["part"] = book_layout.part_of[key]
    elif any(part.key == key for part in book_layout.parts):
        keys["part"] = key
    elif key != BOOK:
        raise ValueError(
            f"{scope} is not a chapter or part of this book. See them: "
            f"authorlm book status -m {manuscript['name']}")
    return keys


def diagram(db: Database, manuscript: dict,
            scope: str | None = None) -> dict:
    """The workflow as a Mermaid flowchart, drawn from the definition
    file: `{mermaid, nodes, edges, scope}`. One node per step, grouped by
    grain, an edge for each `after`, a mark for a person's step and for an
    independent step. With a `scope` (a chapter, a part, or `book`) each
    node also carries its state there, the same one `status` gives
    (`_cell`): blocked, ready, running, waiting, done or failed. A step
    that was run before and restarted says so with a second mark and its
    attempt number, never as its state."""
    _require_document_ids(db, manuscript)
    definition = load_definition(manuscript)
    book_layout = layout(db, manuscript)
    runs = _runs(db, manuscript)
    keys = _diagram_scope(db, manuscript, book_layout, scope)
    nodes: list[dict] = []
    for step in definition.steps:
        key = keys[step.grain]
        cell = (_cell(definition, book_layout, runs, step, key)
                if key is not None else None)
        nodes.append({
            "node": "step_" + step.id.replace("-", "_"), "step": step.id,
            "role": step.role, "actor": step.actor,
            "human": step.actor != "agent", "independent": step.independent,
            "grain": step.grain,
            "status": cell["state"] if cell else None,
            "run": cell["run"] if cell else None,
            "earlier_attempts": cell["earlier_attempts"] if cell else 0})
    edges = [{"from": earlier, "to": step.id}
             for step in definition.steps for earlier in step.after]
    by_step = {node["step"]: node for node in nodes}
    lines = ["flowchart TD"]
    for grain in GRAINS:
        members = [node for node in nodes if node["grain"] == grain]
        if not members:
            continue
        lines.append(f'  subgraph grain_{grain}["{_GRAIN_TITLES[grain]}"]')
        for node in members:
            text = "<br/>".join(
                [node["step"], node["role"], node["actor"]]
                + (["independent"] if node["independent"] else [])
                + ([f"attempt {node['earlier_attempts'] + 1}"]
                   if node["earlier_attempts"] else []))
            text = text.replace('"', "#quot;")
            opening, closing = (('{{"', '"}}') if node["independent"] else
                                ('(["', '"])') if node["human"] else
                                ('["', '"]'))
            lines.append(f"    {node['node']}{opening}{text}{closing}")
        lines.append("  end")
    lines += [f"  {by_step[edge['from']]['node']} --> "
              f"{by_step[edge['to']]['node']}" for edge in edges]
    lines += [f"  classDef {name} {look}"
              for name, look in DIAGRAM_CLASSES.items()]
    for node in nodes:
        for name in ((["human"] if node["human"] else [])
                     + (["independent"] if node["independent"] else [])
                     + ([node["status"]] if node["status"] else [])
                     + (["reran"] if node["earlier_attempts"] else [])):
            lines.append(f"  class {node['node']} {name}")
    return {"manuscript": manuscript["name"],
            "scope": _scope_key(db, manuscript, scope) if scope else None,
            "mermaid": "\n".join(lines) + "\n", "nodes": nodes,
            "edges": edges,
            "next": f"authorlm book status -m {manuscript['name']}"}
