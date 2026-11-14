"""Agent edits that land directly in a chapter file, with a change list
the author can read and put back.

Owner, 2026-11-13, asked "In a ghostwritten book, should agent editors'
changes land directly with a change list?": "Yes, land directly". The
same day: what to automate is "the grunt work of editing, copy editing,
line editing"; "nothing should be published without reading and approvals
by the author"; the aim is that authors "know how to criticize it very
precisely". The lead developer's reading: this is per manuscript, the
`agent_edits` setting (book.py). A manuscript without the setting, or set
to `forms`, is untouched by this module: every verb here that writes
declines, and the house rules about forms, the first-draft word and Doc
tabs apply as before.

Under `direct`, a running agent step whose definition says `changes_text
= true` lands its changes with `land`: a list of `{old, new, note}`, each
`old` standing exactly once in the file. Every change is one row of
`step_changes`, keyed by the chapter's document id, never deleted. The
author reads them (`list_changes`) and puts any of them back (`revert`).
This module is the only writer of `step_changes`. It calls no model and
never calls Google to write: after a landing the caller pushes the file
(`authorlm doc push <file>`), as the house rules say.

Decisions:
- A batch is all or none. Every item is checked against the file as it
  stands before anything is written, and each failing item is named.
  Enforced by:
  tests/test_book_changes.py::LandDeclinesTests::test_a_missing_or_doubled_old_declines_the_whole_batch.
- Every landed change can be found again. `new` may not be empty (to cut
  words, give a few neighbouring words in both `old` and `new`), and after
  the batch each `new` must stand exactly once in the file; otherwise the
  batch is declined and asks for more surrounding words. Why: a change the
  author cannot put back is not a change list item. Enforced by:
  tests/test_book_changes.py::LandDeclinesTests::test_a_change_that_could_not_be_put_back_is_declined.
- Who may land is read from the workflow definition, never from a list of
  role names: the step's `changes_text`. Enforced by:
  tests/test_book_changes.py::LandDeclinesTests::test_a_step_without_changes_text_may_not_land.
- A run lands only inside its scope: a chapter step in its chapter, a part
  step in its part's files, a book step in any file of the manuscript.
  Enforced by:
  tests/test_book_changes.py::LandDeclinesTests::test_a_chapter_run_lands_only_in_its_chapter.
- Before a file is written, what is on disk is snapshotted (`collect`,
  source `pre-book-land`, the step every other verb that overwrites a
  manuscript file takes), and after it the new text is collected with no
  episode: an agent's edit is not work toward whatever goal the author has
  open. Enforced by:
  tests/test_book_changes.py::LandTests::test_the_text_before_and_after_a_landing_are_both_snapshots.
- A file whose Doc tab holds what the file does not is left alone: forms
  written into the file, a file checked out to the Doc, or a tab edited
  since the last push or pull (`gdocs.doc_side_state`, the check the
  audiobook page makes before it pushes an edit). A manuscript with no
  Doc never reaches Google. Enforced by:
  tests/test_book_changes.py::LandDeclinesTests::test_a_tab_the_author_edited_is_pulled_first.
- A change is put back only while its `new` text still stands exactly
  once in the file; otherwise that one is declined and the answer quotes
  what stands there now. A change put back keeps its row, with who and
  why. Enforced by:
  tests/test_book_changes.py::RevertTests::test_revert_puts_the_text_back_and_keeps_the_row
  and
  tests/test_book_changes.py::RevertTests::test_revert_declines_when_the_text_has_moved_on.
- A first draft is not a change list item. A chapter file that holds only
  its title is filled by `draft`: the whole text is written through the
  same checks, the snapshot taken after it is the copy of the draft, the
  run's event log says so, and `book done` lists the file among the run's
  artifacts. No `step_changes` row is written. A file that already holds
  prose is written over only by the run that drafted it or by a rerun of
  the step. Enforced by:
  tests/test_book_changes.py::DraftTests::test_a_first_draft_is_an_artifact_and_an_event_not_a_change
  and
  tests/test_book_changes.py::DraftTests::test_a_file_that_holds_prose_is_not_drafted_over.
- Every landing, draft and revert is one row of `step_events` (through
  `book.note`), so `book history` shows it. Enforced by:
  tests/test_book_changes.py::LandTests::test_a_landing_shows_in_the_history.

Terms:
- **change**: one replacement an agent role landed in a chapter file: the
  words that stood (`old`), the words that replaced them (`new`) and the
  reason (`note`); a row of `step_changes`, `applied` or `reverted`.
- **change list**: the changes of a run, a step, a file or a scope, in
  reading order, as the author reads them.
- **landing**: one call of `land`: a batch of changes to one file, all
  written or none.
"""

from __future__ import annotations

import contextlib
import difflib
import io
import json
import re
from pathlib import Path

from . import book, docs, structure
from .db import Database, ko_fields, loads, now_iso

CHANGE_PREFIX = "sc"
STATUSES = ("applied", "reverted")
DRAFTED_KEY = "drafted"
LANDED_KEY = "changes_landed"


# -------------------------------------------------------------------- checks

def _quiet_collect(db: Database, manuscript: dict, config: dict,
                   source: str, *, after: bool = False) -> dict:
    """`collect`, without its printing. `after` files the snapshot under
    no episode, as a filter's settle does."""
    from .collect import collect
    from .sessions import NO_EPISODE

    with contextlib.redirect_stdout(io.StringIO()):
        if after:
            return collect(db, manuscript, config or {}, source=source,
                           episode=NO_EPISODE)
        return collect(db, manuscript, config or {}, source=source)


def google_tab_state(db: Database, manuscript: dict, relpath: str,
                     config: dict, workspace: str | None = None) -> str:
    """`gdocs.doc_side_state` for one file: unlinked, unchanged,
    local_ahead, doc_ahead or conflict. A file with no pushed tab is
    `unlinked` without any call to Google."""
    from . import gdocs

    if not gdocs.has_pushed_tab(db, manuscript, relpath):
        return "unlinked"
    service = gdocs.get_service(config or {}, workspace, interactive=False)
    docs_service = gdocs.get_docs_service(config or {}, workspace,
                                          interactive=False)
    return gdocs.doc_side_state(db, manuscript, relpath, service,
                                docs_service)["state"]


def _require_writable(db: Database, manuscript: dict, relpath: str,
                      config: dict, workspace: str | None, tab_state) -> str:
    """Decline to write a file whose Doc side holds what the file does
    not; returns the tab's state otherwise. `tab_state(relpath)` stands in
    for the read of Google (tests pass one)."""
    from . import gdocs

    name = manuscript["name"]
    entry = gdocs.doc_status(db, manuscript).get(relpath)
    if isinstance(entry, dict) and entry.get("checked_out"):
        raise ValueError(
            f"{relpath} is checked out to Google Docs, so the Doc is the "
            f"working copy and nothing was changed. Pull it first: authorlm "
            f"doc pull {relpath} -m {name}")
    origin = gdocs.forms_pending(db, manuscript["id"], relpath)
    if origin:
        raise ValueError(
            f"{relpath} carries {origin} forms the author has not settled, "
            "so nothing was changed. Settle them first with the resolve "
            "verb of the pass that wrote them (`filter resolve`, `critique "
            "resolve` or `revise resolve`), then try again.")
    try:
        state = (tab_state(relpath) if tab_state else google_tab_state(
            db, manuscript, relpath, config, workspace))
    except Exception as err:  # noqa: BLE001 - any failure to read Google
        raise ValueError(
            f"{relpath} has a tab in the Google Doc and it could not be "
            f"read ({err}), so nothing was changed: the tab may hold edits "
            "the file does not.") from err
    if state in ("doc_ahead", "conflict"):
        raise ValueError(
            f"{relpath}'s tab in the Google Doc has edits that are not in "
            "the file yet, so nothing was changed. Pull them first: "
            f"authorlm doc pull {relpath} -m {name}; then try again.")
    return state


def _scope_documents(book_layout: book.Layout, grain: str,
                     key: str) -> list[str] | None:
    """The document ids inside a scope; None for the whole manuscript."""
    if grain == "book" or key == book.BOOK:
        return None
    if grain == "chapter":
        return [key]
    return [key] + [chapter.key for chapter in book_layout.chapters
                    if book_layout.part_of.get(chapter.key) == key]


def _open_run(db: Database, manuscript: dict, run_id: str, verb: str
              ) -> tuple[dict, book.Step]:
    """The run and its step, for a verb that writes a chapter file: the
    setting is direct, the run is an agent's and running, and its step's
    definition says it changes text."""
    name = manuscript["name"]
    book._require_document_ids(db, manuscript)
    if book.agent_edits(db, manuscript) != "direct":
        raise ValueError(
            f"edits in this book go to the author as forms ({name}'s "
            "agent_edits setting is forms), so nothing was changed. Follow "
            "the house rules for forms. The setting is the author's to "
            "change, not a role's.")
    run = book._run(db, book._find_run(db, manuscript, run_id))
    if run["actor"] != "agent":
        raise ValueError(
            f"{run['id']} ({run['step']}) is {run['actor']}'s step, not an "
            f"agent's, so `book {verb}` is not open to it: a person edits "
            "the file themselves.")
    if run["status"] != "running":
        raise ValueError(
            f"{run['id']} ({run['step']}) is {run['status']}, not running, "
            "so nothing was changed. Only a running agent step lands "
            f"changes. See what is next: authorlm book next -m {name}")
    step = book.load_definition(manuscript).step(run["step"])
    if not step.changes_text:
        raise ValueError(
            f"{step.id} ({step.role}) is not a step that changes text: its "
            "definition has no `changes_text = true`, so nothing was "
            "changed. Report the problem in your result (`filed_back`, or "
            "your findings) for the role whose step it is.")
    return run, step


def _file_in_scope(db: Database, manuscript: dict, run: dict,
                   file: str) -> tuple[str, str, Path]:
    """(relpath, document id, path) of `file`, which must lie inside the
    run's scope."""
    relpath = docs._resolve_relpath(manuscript, file)
    document_id = docs.document_id(db, manuscript, relpath)
    allowed = _scope_documents(book.layout(db, manuscript), run["grain"],
                               run["scope"])
    if allowed is not None and document_id not in allowed:
        raise ValueError(
            f"{relpath} is outside this run's scope: {run['id']} "
            f"({run['step']}) runs for {run['scope_label']} and changes "
            "only that. Nothing was changed. A problem in another chapter "
            "is filed back in your result.")
    return relpath, document_id, Path(manuscript["path"]) / relpath


def _short(text: str, limit: int = 60) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"


def _check_items(changes, text: str) -> tuple[list[dict], str]:
    """The batch checked against the file's text: the cleaned items and
    the text they produce. Every failing item is named in one error."""
    if not isinstance(changes, list) or not changes:
        raise ValueError(
            'the changes are a JSON list of {"old": "…", "new": "…", '
            '"note": "…"}, at least one')
    problems: list[str] = []
    spans: list[tuple[int, int, int]] = []
    items: list[dict] = []
    for number, item in enumerate(changes, 1):
        label = f"item {number}"
        if not isinstance(item, dict) or not all(
                isinstance(item.get(k), str) for k in ("old", "new", "note")):
            problems.append(f"{label}: needs old, new and note, each as "
                            "text")
            continue
        old, new, note = item["old"], item["new"], item["note"].strip()
        label = f"{label} («{_short(old)}»)"
        if not note:
            problems.append(f"{label}: the note is empty; say why, and by "
                            "what law, register entry or filter")
        if not old:
            problems.append(f"{label}: old is empty; give the words that "
                            "change")
            continue
        if not new.strip():
            problems.append(
                f"{label}: new is empty. To cut words, give a few "
                "neighbouring words in both old and new, so the change can "
                "be found again and put back")
        if old == new:
            problems.append(f"{label}: old and new are the same")
        count = text.count(old)
        if count == 0:
            problems.append(f"{label}: these words are not in the file as "
                            "it stands")
        elif count > 1:
            problems.append(f"{label}: these words stand {count} times in "
                            "the file; give more of the words around them "
                            "so they name one place")
        else:
            start = text.index(old)
            spans.append((start, start + len(old), number))
        items.append({"old": old, "new": new, "note": note})
    spans.sort()
    for (_s, end, first), (start, _e, second) in zip(spans, spans[1:]):
        if start < end:
            problems.append(f"items {first} and {second} change overlapping "
                            "words; make them one change")
    if not problems:
        result = text
        for start, end, number in sorted(spans, reverse=True):
            result = result[:start] + changes[number - 1]["new"] + result[end:]
        for number, item in enumerate(items, 1):
            if result.count(item["new"]) != 1:
                problems.append(
                    f"item {number} («{_short(item['old'])}»): after the "
                    f"change, «{_short(item['new'])}» would stand "
                    f"{result.count(item['new'])} times in the file, so it "
                    "could not be found again to be put back; give more of "
                    "the words around it in both old and new")
        if not problems:
            return items, result
    raise ValueError(
        "nothing was changed: the batch is all or none, and "
        + (f"{len(problems)} things are wrong with it:\n  - "
           if len(problems) > 1 else "one thing is wrong with it:\n  - ")
        + "\n  - ".join(problems))


def _next_hint(manuscript: dict, relpath: str, state: str, run_id: str
               ) -> str:
    name = manuscript["name"]
    push = ("" if state == "unlinked"
            else f"authorlm doc push {relpath} -m {name}; then ")
    return (f"{push}when the step is finished: authorlm book done {run_id} "
            f"--result-json <file> -m {name}")


# --------------------------------------------------------------------- write

def land(db: Database, manuscript: dict, config: dict, run_id: str,
         file: str, changes, *, workspace: str | None = None,
         tab_state=None) -> dict:
    """Land a batch of changes in one chapter file under a running agent
    step; returns `{run, step, role, file, document_id, landed, version_no,
    next}`. `changes` is a list of `{old, new, note}`. Declines, with
    nothing changed, as the module docstring says."""
    run, step = _open_run(db, manuscript, run_id, "land")
    relpath, document_id, path = _file_in_scope(db, manuscript, run, file)
    state = _require_writable(db, manuscript, relpath, config, workspace,
                              tab_state)
    _check_items(changes, path.read_text(encoding="utf-8"))
    # Snapshot what is on disk before it is written over. An edit made
    # outside this verb predates the landing and is the author's.
    _quiet_collect(db, manuscript, config, "pre-book-land")
    items, text = _check_items(changes, path.read_text(encoding="utf-8"))
    first = (db.one("SELECT MAX(seq) AS n FROM step_changes WHERE run_id = ?",
                    (run["id"],))["n"] or 0) + 1
    rows = [{**ko_fields(CHANGE_PREFIX), "manuscript_id": manuscript["id"],
             "run_id": run["id"], "step": step.id, "role": step.role,
             "document_id": document_id, "seq": first + offset,
             "old": item["old"], "new": item["new"], "note": item["note"],
             "status": "applied"} for offset, item in enumerate(items)]
    with db.transaction():
        for row in rows:
            db.insert("step_changes", row)
        book.note(db, manuscript, run["id"], actor=run["actor"],
                  reason=f"landed {len(rows)} change(s) in {relpath}")
        book._write_atomic(path, text)
    after = _quiet_collect(db, manuscript, config, "book-land", after=True)
    return {"run": run["id"], "step": step.id, "role": step.role,
            "file": relpath, "document_id": document_id,
            "landed": [_change(db, row) for row in rows],
            "version_no": after.get("version_no"),
            "next": _next_hint(manuscript, relpath, state, run["id"])}


def _only_a_title(text: str) -> bool:
    """True for a chapter file that holds no prose yet: nothing, or
    heading lines alone."""
    return all(line.lstrip().startswith("#")
               for line in text.splitlines() if line.strip())


def draft(db: Database, manuscript: dict, config: dict, run_id: str,
          file: str, text: str, *, workspace: str | None = None,
          tab_state=None) -> dict:
    """Write a whole first draft into a chapter file that holds only its
    title, under a running agent step; returns `{run, step, file, words,
    version_no, replaced, next}`. Not a change list item: see the module
    docstring."""
    from .revisions import is_placeholder

    run, step = _open_run(db, manuscript, run_id, "draft")
    relpath, _document_id, path = _file_in_scope(db, manuscript, run, file)
    if not (text or "").strip() or _only_a_title(text):
        raise ValueError("the draft is empty: give the chapter's whole "
                         "text, its title line included")
    state = _require_writable(db, manuscript, relpath, config, workspace,
                              tab_state)
    current = path.read_text(encoding="utf-8")
    if is_placeholder(current):
        raise ValueError(
            f"{relpath} is being rewritten by an active writeup (`authorlm "
            "write`), so nothing was changed. Finish or abandon the "
            "writeup first.")
    row = book._find_run(db, manuscript, run["id"])
    drafted = loads(row["result"], {}).get(DRAFTED_KEY, [])
    replaced = not _only_a_title(current)
    if replaced and relpath not in drafted and run["attempt"] == 1:
        raise ValueError(
            f"{relpath} already holds prose, and this run did not draft "
            "it, so nothing was changed. A first draft goes into a file "
            "that holds only its title. Change what is there with: "
            f"authorlm book land {run['id']} {relpath} --changes-json "
            f"<file> -m {manuscript['name']}")
    _quiet_collect(db, manuscript, config, "pre-book-draft")
    body = text if text.endswith("\n") else text + "\n"
    words = len(body.split())
    if relpath not in drafted:
        drafted = [*drafted, relpath]
    with db.transaction():
        book.note(
            db, manuscript, run["id"], actor=run["actor"],
            reason=f"drafted {relpath}: {words} words"
                   + (", written over the earlier draft" if replaced else ""),
            changes={"result": json.dumps(
                {**loads(row["result"], {}), DRAFTED_KEY: drafted},
                ensure_ascii=False)})
        book._write_atomic(path, body)
    after = _quiet_collect(db, manuscript, config, "book-draft", after=True)
    return {"run": run["id"], "step": step.id, "file": relpath,
            "words": words, "version_no": after.get("version_no"),
            "replaced": replaced,
            "next": _next_hint(manuscript, relpath, state, run["id"])}


def result_with_record(db: Database, run_row, result: dict) -> dict:
    """What `book done` records for a run that wrote chapter files: the
    result with the count of changes it landed (`changes_landed`) and the
    files it drafted (`drafted`, each also among `artifacts`). A run that
    wrote nothing gets its result back as it is."""
    landed = db.one("SELECT COUNT(*) AS n FROM step_changes WHERE run_id = ?",
                    (run_row["id"],))["n"]
    drafted = loads(run_row["result"], {}).get(DRAFTED_KEY, [])
    if not landed and not drafted:
        return result
    result = dict(result)
    if landed:
        result[LANDED_KEY] = landed
    if drafted:
        result[DRAFTED_KEY] = drafted
        artifacts = list(result.get("artifacts", []))
        result["artifacts"] = artifacts + [name for name in drafted
                                           if name not in artifacts]
    return result


# ---------------------------------------------------------------------- read

def _change(db: Database, row) -> dict:
    """A row of `step_changes` (or the dict just inserted as one) as every
    verb returns it; `file` is the name the document carries now."""
    row = dict(row)
    return {"id": row["id"], "run": row["run_id"], "step": row["step"],
            "role": row["role"], "document_id": row["document_id"],
            "file": book._label(db, row["document_id"]), "seq": row["seq"],
            "old": row["old"], "new": row["new"], "note": row["note"],
            "status": row["status"], "landed_at": row["created_at"],
            "reverted_at": row.get("reverted_at"),
            "reverted_by": row.get("reverted_by"),
            "reverted_reason": row.get("reverted_reason")}


def _reading_index(manuscript: dict) -> dict[str, int]:
    toc_path = Path(manuscript["path"]) / structure.TOC_FILENAME
    toc = toc_path.read_text(encoding="utf-8") if toc_path.is_file() else ""
    return {name: index
            for index, (name, _depth) in enumerate(
                structure.parse_toc_tree(toc))}


def _scope_filter(db: Database, manuscript: dict,
                  scope: str | None) -> list[str] | None:
    """The document ids a `--scope` names: a chapter, a part with its
    chapters, or None for the whole book."""
    if scope is None:
        return None
    key = book._scope_key(db, manuscript, scope)
    if key == book.BOOK:
        return None
    book_layout = book.layout(db, manuscript)
    if any(part.key == key for part in book_layout.parts):
        return _scope_documents(book_layout, "part", key)
    return [key]


def list_changes(db: Database, manuscript: dict, *, run: str | None = None,
                 step: str | None = None, scope: str | None = None,
                 file: str | None = None, status: str | None = None,
                 since: str | None = None) -> dict:
    """The change list: `{changes, counts, next}`, each change with its
    id, the role and step that made it, the file, old, new and note, in
    the book's reading order and then in the order landed. Narrowed by
    run, step, scope (a chapter or a part), file, status, or to those
    landed after `since` (a timestamp)."""
    book._require_document_ids(db, manuscript)
    if status is not None and status not in STATUSES:
        raise ValueError("status is applied or reverted")
    run_id = book._find_run(db, manuscript, run)["id"] if run else None
    wanted = _scope_filter(db, manuscript, scope)
    if file:
        document_id = docs.document_id(
            db, manuscript, docs._resolve_relpath(manuscript, file))
        wanted = ([document_id] if wanted is None or document_id in wanted
                  else [])
    order = _reading_index(manuscript)
    changes = []
    for row in db.all("SELECT * FROM step_changes WHERE manuscript_id = ? "
                      "ORDER BY created_at, seq", (manuscript["id"],)):
        if ((run_id and row["run_id"] != run_id)
                or (step and row["step"] != step)
                or (status and row["status"] != status)
                or (since and row["created_at"] <= since)
                or (wanted is not None and row["document_id"] not in wanted)):
            continue
        changes.append(_change(db, row))
    changes.sort(key=lambda c: (order.get(c["file"], len(order)), c["file"],
                                c["landed_at"], c["seq"]))
    counts = {name: sum(1 for c in changes if c["status"] == name)
              for name in STATUSES}
    name = manuscript["name"]
    return {"manuscript": name, "changes": changes, "counts": counts,
            "next": (f"put one back: authorlm book revert <change-id> "
                     f"--reason '<why>' -m {name}" if counts["applied"]
                     else f"authorlm book status -m {name}")}


def _last_read(definition: book.Definition, book_layout: book.Layout,
               runs: dict, scope: book.Scope) -> dict | None:
    """The author's last finished read of this scope: the latest run, by
    when it finished, of a person's step that is given `changes`, for
    this scope or one that holds it."""
    given = {step.id for step in definition.steps if "changes" in step.gives}
    latest = None
    for attempts in runs.values():
        for run in attempts:
            if (run["step"] not in given or run["actor"] == "agent"
                    or not run["finished_at"] or run["failed_reason"]):
                continue
            covers = (run["grain"] == "book"
                      or (run["grain"], run["scope"]) == (scope.grain,
                                                          scope.key)
                      or (run["grain"] == "part" and scope.grain == "chapter"
                          and book_layout.part_of.get(scope.key)
                          == run["scope"]))
            if covers and (latest is None
                           or run["finished_at"] > latest["finished_at"]):
                latest = run
    return latest


def pack_section(db: Database, manuscript: dict, definition: book.Definition,
                 book_layout: book.Layout, runs: dict, step: book.Step,
                 scope: book.Scope) -> list[str]:
    """The pack section for a step given `changes`: how many changes agent
    roles landed in the scope, in all and since the author last read it,
    and the command that lists them."""
    wanted = _scope_documents(book_layout, scope.grain, scope.key)
    rows = [row for row in db.all(
        "SELECT document_id, status, created_at FROM step_changes WHERE "
        "manuscript_id = ?", (manuscript["id"],))
        if wanted is None or row["document_id"] in wanted]
    name = manuscript["name"]
    where = "" if scope.key == book.BOOK else f" --scope {scope.label}"
    lines = ["## What agent roles changed", ""]
    if not rows:
        return lines + [
            f"Agent roles have landed no change in {scope.label}."
            + (" Edits in this book go to the author as forms."
               if book.agent_edits(db, manuscript) != "direct" else ""), ""]
    back = sum(1 for row in rows if row["status"] == "reverted")
    last = _last_read(definition, book_layout, runs, scope)
    total = (f"Agent roles have landed {len(rows)} change(s) in "
             f"{scope.label}" + (f", {back} of them since put back"
                                 if back else "") + ".")
    command = f"    authorlm book changes{where} -m {name}"
    if last is None:
        lines += [total + " The author has not read this scope before, so "
                  "all of them are new to the author.", "",
                  "Each one, with the words before and after and the "
                  "reason:", "", command, ""]
    else:
        since = sum(1 for row in rows
                    if row["created_at"] > last["finished_at"])
        lines += [
            total + f" {since} of them landed since the author last read "
            f"this scope ({last['step']}, run {last['id']}).", "",
            "Those landed since that read, each with the words before and "
            "after and the reason:", "",
            f"    authorlm book changes{where} --since "
            f"{last['finished_at']} -m {name}", "", "All of them:", "",
            command, ""]
    return lines + [
        "The author can put any change back: authorlm book revert "
        f"<change-id> --reason '<why>' -m {name}", ""]


# -------------------------------------------------------------------- revert

def _stands_now(text: str, words: str) -> str:
    """What the file holds where `words` used to stand, in a sentence for
    the author: the paragraph that shares most with them, quoted."""
    best, shared = "", 0
    for paragraph in re.split(r"\n\s*\n", text):
        match = difflib.SequenceMatcher(
            None, paragraph, words, autojunk=False).find_longest_match(
                0, len(paragraph), 0, len(words))
        if match.size > shared:
            best, shared = paragraph.strip(), match.size
    if shared < max(4, len(words) * 0.4):
        return "Nothing close to those words is in the file now."
    return f"What stands there now: «{best}»"


def _find_change(db: Database, manuscript: dict, change_id: str):
    rows = db.all("SELECT * FROM step_changes WHERE manuscript_id = ? AND "
                  "id LIKE ?", (manuscript["id"],
                                (change_id or "").replace("%", "") + "%"))
    if len(rows) != 1:
        found = "no" if not rows else "more than one"
        raise LookupError(
            f"{found} change matches '{change_id}'. List them: authorlm "
            f"book changes -m {manuscript['name']}")
    return rows[0]


def revert(db: Database, manuscript: dict, config: dict,
           change_ids: list[str] | None = None, *, run: str | None = None,
           reason: str, by: str = "author", workspace: str | None = None,
           tab_state=None) -> dict:
    """Put changes back: the named ones, or every applied change of
    `run` (last landed first); returns `{reverted, declined, next}`. Each
    is judged on its own: a change is put back only while its `new` text
    stands exactly once in its file. The reason is kept verbatim, with
    who; the row stays."""
    book._require_document_ids(db, manuscript)
    if not (reason or "").strip():
        raise ValueError("putting a change back records why: --reason "
                         "'<why>'")
    if not (by or "").strip():
        raise ValueError("--by names who put the change back")
    if bool(change_ids) == bool(run):
        raise ValueError("name the changes to put back (sc- ids), or one "
                         "run with --run; not both, not neither")
    if run:
        run_id = book._find_run(db, manuscript, run)["id"]
        rows = db.all("SELECT * FROM step_changes WHERE run_id = ? AND "
                      "status = 'applied' ORDER BY seq DESC", (run_id,))
        if not rows:
            raise ValueError(f"{run_id} has no applied change to put back")
    else:
        rows = [_find_change(db, manuscript, cid) for cid in change_ids]
    name = manuscript["name"]
    reverted: list[dict] = []
    declined: list[dict] = []
    pushes: list[str] = []
    for row in rows:
        change = _change(db, row)
        relpath = change["file"]
        path = Path(manuscript["path"]) / relpath

        def decline(why: str) -> None:
            declined.append({"id": row["id"], "file": relpath, "why": why})

        if row["status"] == "reverted":
            decline(f"already put back by {row['reverted_by']}: "
                    f"{row['reverted_reason']}")
            continue
        if not path.is_file():
            decline(f"{relpath} is not on disk")
            continue
        try:
            state = _require_writable(db, manuscript, relpath, config,
                                      workspace, tab_state)
        except ValueError as err:
            decline(str(err))
            continue
        count = path.read_text(encoding="utf-8").count(row["new"])
        if count == 1:
            _quiet_collect(db, manuscript, config, "pre-book-revert")
        text = path.read_text(encoding="utf-8")
        count = text.count(row["new"])
        if count != 1:
            decline(
                ("the text has moved on: the words this change put in are "
                 "no longer in the file. " + _stands_now(text, row["new"]))
                if count == 0 else
                f"the words this change put in now stand {count} times in "
                "the file, so the place cannot be told.")
            continue
        at = now_iso()
        with db.transaction():
            db.update("step_changes", row["id"], {
                "status": "reverted", "reverted_at": at,
                "reverted_by": by.strip(),
                "reverted_reason": reason.strip()})
            book.note(db, manuscript, row["run_id"], actor=by.strip(),
                      reason=f"put back {row['id']} in {relpath}: "
                             f"{reason.strip()}")
            book._write_atomic(path, text.replace(row["new"], row["old"]))
        _quiet_collect(db, manuscript, config, "book-revert", after=True)
        reverted.append(_change(db, _find_change(db, manuscript, row["id"])))
        if state != "unlinked" and relpath not in pushes:
            pushes.append(relpath)
    hint = "; ".join(f"authorlm doc push {relpath} -m {name}"
                     for relpath in pushes)
    return {"manuscript": name, "reverted": reverted, "declined": declined,
            "next": hint or f"authorlm book changes -m {name}"}


# ---------------------------------------------------------------------- lint

def lint_chapter(db: Database, manuscript: dict, file: str) -> dict:
    """The deterministic lint (lint.py) over one whole chapter as it is on
    disk, against the rest of the book: `{file, findings, counts, next}`,
    each finding with its law, its severity and the words quoted. No
    model call, nothing written, open under either `agent_edits` value.
    The chapter itself is left out of the overlap check, so a chapter is
    not reported as repeating itself."""
    from . import critic, lint

    relpath = docs._resolve_relpath(manuscript, file)
    text = (Path(manuscript["path"]) / relpath).read_text(encoding="utf-8")
    report = critic.lint_draft(db, manuscript, relpath, text,
                               include_self=False)
    findings = [{"code": f.code, "severity": f.severity,
                 # The reading grade is a paragraph's; lint.py quotes the
                 # paragraph's opening words only.
                 "quote": " ".join(f.quote.split())
                 + ("…" if f.code == "reading-grade" else ""),
                 # A paragraph number is no use to a reader: the words
                 # quoted are the place.
                 "message": re.sub(r"\s*¶\d+", "", f.message),
                 "ref": re.sub(r"\s*¶\d+", "", f.ref)}
                for f in report.findings]
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    return {"manuscript": manuscript["name"], "file": relpath,
            "findings": findings, "counts": counts,
            "next": f"authorlm book status -m {manuscript['name']}"}
