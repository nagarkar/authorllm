"""The Merge forms page: every open outside revision of a manuscript in one
place, ruled on there and merged one file at a time.

An outside caller (`revise stage`) puts its revisions in a file's Doc tab as
`<<old>>{{new}}` forms; the author rules on them in the tab and `revise
resolve` lands them. A filter pass on the Doc road (`filter push`) and a
lens (`lens push`) put their forms there the same way, and `filter
resolve` / `lens resolve` land them. With forms spread over many tabs
the author hunted for them; this page lists them all, grouped by file, with the text each form's
green half holds NOW (so an edit the author made in the Doc shows), the
paragraph around it, and Accept / Edit / Decline.

A ruling is held on its thread (`metadata.page_ruling`) until the file is
merged; nothing touches the Doc or the file before that. Merge does, for one
file, what the author would do by hand, and stops where a person must look:

1. Read the file three ways (`gdocs.doc_three_way`). Changed on both sides
   since the last push (`conflict`): stop, show both sides of `doc diff`.
   No forced pull (it drops local edits) and no push over it. Such a
   file is settled under Conflicts instead (`land_conflict`, below),
   once its forms are merged.
2. Every form still in the tab has a ruling, and its green half still reads
   as it did when it was ruled; otherwise stop and say which.
3. Only the tab moved (`doc_ahead`): pull it first, an ordinary pull. A tab
   whose text is an OLDER version of the local file is stale, not an edit,
   and is never pulled (2026-11-10: a staging pulled a stale tab over the
   file and reverted another session's accepted edits).
4. `api.resolve_revisions` per outside caller, `api.filter_resolve` per
   filter run, `api.lens_resolve` once for all the file's lens forms,
   with the page's rulings applied to the tab's text as if typed there (`rulings`). The outside caller's resolve keeps a local
   edit the tab has not seen, puts the illustration embed lines back, and
   clears the tab; a lens's resolve clears its own forms in place; a
   filter's resolve leaves the tab to step 6, as the filter protocol's
   `doc push` after `filter resolve`.
5. Every `![](_illustrations/...)` line the file had is still there, or is
   put back under its tag; one that cannot be is said, and nothing is
   pushed.
6. A guarded push (`push_doc(guard=True)`) when the tab is not yet the file.

The page is `/api/forms` on the audiobook server (`audiobook_server.py`);
`dispatch` is its transport, as `devedit.dispatch` is the Dev edit's.
"""

from __future__ import annotations

import contextlib
import difflib
import io
import json
import re
import threading
from pathlib import Path
from typing import Any

from . import api, form_grammar, gdocs
from .db import Database, loads, now_iso
from .normalize import clamp, normalize_markdown
from .revisions import EMBED_LINE, strip_embed_lines

RULING_KEY = "page_ruling"
# The producers whose forms are ruled and merged here: outside callers
# (`revise stage`), filter passes on the Doc road (`filter push`) and
# lenses (`lens push`). Every file with an open form from any of them is
# listed.
PRODUCERS = (api.EXTERNAL_ORIGIN, api.FILTER_ORIGIN, api.LENS_ORIGIN)
_IN = ", ".join("?" for _ in PRODUCERS)
VERDICTS = ("accept", "edit", "decline")


# ----------------------------------------------------------------- reading

def open_threads(db: Database, manuscript: dict,
                 file: str | None = None) -> dict[str, list[dict]]:
    """file → its open threads (`written`) from the page's producers, in
    staging order, each with `origin`: the outside caller's name, the
    filter's, or the lens's. Files with none are absent."""
    sql = ("SELECT * FROM doc_threads WHERE manuscript_id = ? "
           f"AND origin_type IN ({_IN}) AND state = 'written'")
    args: list[Any] = [manuscript["id"], *PRODUCERS]
    if file is not None:
        sql += " AND file = ?"
        args.append(file)
    out: dict[str, list[dict]] = {}
    filters: dict[str, str] = {}
    for row in db.all(sql + " ORDER BY created_at, id", tuple(args)):
        t = dict(row)
        if t["origin_type"] == api.FILTER_ORIGIN:
            run_id = (t.get("origin_id") or "").split(":", 1)[0]
            if run_id not in filters:
                run = db.one("SELECT filter FROM filter_runs WHERE id = ?",
                             (run_id,))
                filters[run_id] = run["filter"] if run else ""
            t["origin"] = filters[run_id]
        elif t["origin_type"] == api.LENS_ORIGIN:
            # origin_id is '<lens>@<hash>:<file>:<ordinal>'.
            t["origin"] = (t.get("origin_id") or "").split(":", 1)[0] \
                .split("@", 1)[0]
        else:
            t["origin"] = _meta(t).get("origin") or ""
        out.setdefault(t["file"], []).append(t)
    return out


def _meta(thread: dict) -> dict:
    return loads(thread.get("metadata"), {}) or {}


def ruling_of(thread: dict) -> dict | None:
    """The page's held ruling on a thread: {verdict, text, seen, at}."""
    return _meta(thread).get(RULING_KEY)


def _other_forms(db: Database, manuscript: dict, file: str) -> dict[str, int]:
    """origin_type → how many of its forms are out in `file`'s tab, for
    every producer not ruled here (a critique, a margin proposal). They
    also block new forms."""
    rows = db.all(
        "SELECT origin_type, COUNT(*) AS n FROM doc_threads WHERE "
        "manuscript_id = ? AND file = ? AND state = 'written' "
        f"AND origin_type NOT IN ({_IN}) GROUP BY origin_type",
        (manuscript["id"], file, *PRODUCERS))
    return {r["origin_type"]: r["n"] for r in rows}


_BREAK = re.compile(r"\n[ \t]*\n")


def _paragraph_around(text: str, start: int, end: int) -> tuple[str, str]:
    """The text of the blank-line paragraph around text[start:end], before
    and after that span, every other form in it read as its old half."""
    breaks = list(_BREAK.finditer(text))
    p_start = max((m.end() for m in breaks if m.end() <= start), default=0)
    p_end = min((m.start() for m in breaks if m.start() >= end),
                default=len(text.rstrip("\n")))
    before = form_grammar.strip_pending(text[p_start:start])[0]
    after = form_grammar.strip_pending(text[end:p_end])[0]
    return before, after


def _paragraph_before(text: str, start: int) -> str:
    """The paragraph that ends before position `start` (an addition's
    anchor), its forms read as their old halves."""
    head = text[:start].rstrip()
    breaks = list(_BREAK.finditer(head))
    at = breaks[-1].end() if breaks else 0
    return form_grammar.strip_pending(head[at:])[0]


# A word diff for a card: kept words plain, removed words struck, inserted
# words green, in reading order, so a form that keeps most of its old text
# (a lens that prepends one sentence) does not read as all changed.
_TOKEN = re.compile(r"\w+(?:['\u2019]\w+)*|\s+|[^\w\s]")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text or "")


def _tiny(text: str) -> bool:
    """An equal run too small to stand between two changes: whitespace or
    punctuation alone, or one short word with its spaces."""
    words = re.findall(r"\w+", text)
    return not words or (len(words) == 1 and len(words[0]) <= 3
                         and len(text) <= 6)


def word_diff(old: str, new: str) -> list[dict]:
    """Segments [{op: equal|delete|insert, text}] taking `old` to `new`,
    over word, space and punctuation tokens. A tiny equal run between two
    changes is folded into them, and each change reads delete then insert."""
    a, b = _tokens(old), _tokens(new)
    chunks: list[list[str]] = []  # [kind, old text, new text]
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, a, b, autojunk=False).get_opcodes():
        chunks.append(["equal" if tag == "equal" else "change",
                       "".join(a[i1:i2]), "".join(b[j1:j2])])
    for i in range(1, len(chunks) - 1):
        if (chunks[i][0] == "equal" and chunks[i - 1][0] == "change"
                and chunks[i + 1][0] == "change" and _tiny(chunks[i][1])):
            chunks[i][0] = "change"
    merged: list[list[str]] = []
    for c in chunks:
        if merged and merged[-1][0] == c[0] == "change":
            merged[-1][1] += c[1]
            merged[-1][2] += c[2]
        else:
            merged.append(list(c))
    out: list[dict] = []
    for kind, o, n in merged:
        if kind == "equal":
            out.append({"op": "equal", "text": o})
            continue
        if o:
            out.append({"op": "delete", "text": o})
        if n:
            out.append({"op": "insert", "text": n})
    return out


def _form_row(thread: dict, form: dict | None, marked: str | None,
              local: str) -> dict:
    meta = _meta(thread)
    old = thread.get("proposed_old") or ""
    row: dict[str, Any] = {
        "id": thread["id"], "origin": thread.get("origin") or "",
        "producer": thread["origin_type"],
        "note": thread.get("note") or "", "kind": "replace" if old else "insert",
        "old": old, "proposed": thread.get("proposed_new") or "",
        "current": None, "found": form is not None,
        "prefix": "", "suffix": "", "anchor": "",
        "ruling": ruling_of(thread)}
    if form is not None and marked is not None:
        row["current"] = form["new"]
        if old:
            row["prefix"], row["suffix"] = _paragraph_around(
                marked, form["start"], form["end"])
        else:
            row["anchor"] = _paragraph_before(marked, form["start"])
    elif old and old in local:
        at = local.find(old)
        row["prefix"], row["suffix"] = _paragraph_around(
            local, at, at + len(old))
    row["edited_in_doc"] = (row["current"] is not None
                            and row["current"] != row["proposed"])
    # A ruling made on a green half the tab no longer reads (the Doc was
    # edited after it) does not count: the form goes back to To rule,
    # flagged, and merging waits for a new ruling. The held ruling stays
    # in the database until then, so the page can say what it was.
    row["ruling_stale"] = None
    if (row["ruling"] and row["current"] is not None
            and row["current"] != row["ruling"]["seen"]):
        row["ruling_stale"] = row["ruling"]
        row["ruling"] = None
    # The card's diff: the old text against what lands if merged now.
    ruling = row["ruling"] or {}
    shown = (ruling.get("text") if ruling.get("verdict") == "edit"
             else None) or row["current"] or row["proposed"]
    row["segments"] = word_diff(old, shown) if old else []
    return row


def _diff_sides(view: dict) -> dict:
    """The two sides `doc diff` prints, from a `doc_three_way` view."""
    rel = view["relpath"]
    if view.get("base") is None:
        return {"base_found": False, "doc": "", "local": "",
                "note": f"no pushed base is on record for {rel}, so the "
                        f"sides cannot be told apart here; run "
                        f"'authorlm doc diff {rel}' and settle by hand"}
    return {"base_found": True,
            "doc": gdocs.unified(view["base"], view["doc"],
                                 f"base/{rel}", f"doc/{rel}"),
            "local": gdocs.unified(view["base"], view["local"],
                                   f"base/{rel}", f"local/{rel}")}


def listing(db: Database, manuscript: dict, service, docs_service) -> dict:
    """Every open form, by file, as the Doc holds it now, and every file
    changed on both sides (Conflicts), forms or not. Read-only: one
    export of the master Doc (`gdocs.read_manuscript`), and for a file in
    conflict its three-way view and its cards (`conflict_view`). No
    Google call when the manuscript has no master Doc."""
    groups = open_threads(db, manuscript)
    links = gdocs.doc_status(db, manuscript)
    master = links.get("_master_id")
    out: dict[str, Any] = {"doc_url": gdocs.tab_url(master) if master else None,
                           "files": [], "total": 0, "conflicts": 0}
    if not master:
        return out
    read = gdocs.read_manuscript(db, manuscript, service, docs_service)
    root = Path(manuscript["path"])
    in_conflict = {rel for rel, e in read["files"].items()
                   if e.get("state") == "conflict"}
    for rel in sorted(set(groups) | in_conflict):
        threads = groups.get(rel, [])
        entry = read["files"].get(rel) or {}
        marked = entry.get("marked")
        pairs = api.pair_external_forms(marked, threads) if marked else {}
        path = root / rel
        local = path.read_text(encoding="utf-8") if path.exists() else ""
        rows = [_form_row(t, pairs.get(t["id"]), marked, local)
                for t in threads]
        tab = (links.get(rel) or {}).get("tab_id") if isinstance(
            links.get(rel), dict) else None
        others = _other_forms(db, manuscript, rel)
        state = entry.get("state") or "missing"
        info: dict[str, Any] = {
            "file": rel, "url": gdocs.tab_url(master, tab) if master else None,
            "state": state, "count": len(rows), "forms": rows,
            "origins": sorted({r["origin"] for r in rows}),
            "others": others,
            "undecided": sum(1 for r in rows if r["found"] and not r["ruling"]),
            # Forms the tab no longer holds (all of them when the tab was
            # not found): the page lists these under Invalid, not To rule.
            "invalid": sum(1 for r in rows if not r["found"]),
            "blocking": (f"This tab takes no new forms until its {len(rows)} "
                         f"open form(s) are merged"
                         + (" and the other producers' forms ("
                            + ", ".join(f"{n} {o}" for o, n in others.items())
                            + ") are settled" if others else "") + ".")
            if rows else ""}
        if state == "conflict":
            try:
                view = gdocs.doc_three_way(db, manuscript, rel, service,
                                           docs_service)
                info["diff"] = _diff_sides(view)
                info["conflict"] = conflict_view(db, manuscript, rel, view)
            except LookupError as err:
                info["diff"] = {"base_found": False, "doc": "", "local": "",
                                "note": str(err)}
                info["conflict"] = {"key": None, "base_found": False,
                                    "note": str(err), "regions": [],
                                    "decided": 0, "held": None,
                                    "ready": False}
            out["conflicts"] += 1
        out["files"].append(info)
        out["total"] += len(rows)
    return out


# ----------------------------------------------------------------- ruling

def decide(db: Database, manuscript: dict, thread_id: str, verdict: str,
           seen: str | None, text: str | None = None) -> dict:
    """Hold a ruling on one open form until its file is merged. `verdict`
    is accept, edit or decline, or clear to take a ruling back. `seen` is
    the green half as the page showed it; merging stops if the tab no
    longer reads so. An edit's `text` is one paragraph on one line, free
    of the form markers. Nothing is written to the Doc or the file."""
    row = db.one("SELECT * FROM doc_threads WHERE id = ? AND manuscript_id = ?",
                 (thread_id, manuscript["id"]))
    if row is None or row["origin_type"] not in PRODUCERS:
        raise LookupError(f"no open form {thread_id} on this manuscript")
    thread = dict(row)
    if thread["state"] != "written":
        raise ValueError(f"form {thread_id} is no longer open "
                         f"(it is {thread['state']}); refresh the page")
    meta = _meta(thread)
    if verdict == "clear":
        meta.pop(RULING_KEY, None)
        db.update("doc_threads", thread_id, {"metadata": json.dumps(meta)})
        return {"id": thread_id, "ruling": None}
    if verdict not in VERDICTS:
        raise ValueError(f"a ruling is one of {', '.join(VERDICTS)} or clear")
    if seen is None:
        raise ValueError("this form is not in the tab, so the tab decides it "
                         "at merge; it takes no ruling here")
    if verdict == "edit":
        text = (text or "").strip()
        if not text:
            raise ValueError("an edit needs text; to turn the form down, "
                             "Decline it")
        if "\n" in text:
            raise ValueError("the new text is one paragraph on one line")
        form_grammar.assert_no_pending_markers("", text)
        if text == seen:
            verdict = "accept"
        elif (thread["origin_type"] == api.LENS_ORIGIN
              and not thread.get("proposed_old")):
            raise ValueError("a lens judgment is kept or declined as it "
                             "stands; to say it otherwise, Decline it and "
                             "write your own")
    ruling = {"verdict": verdict, "seen": seen, "at": now_iso(),
              "text": text if verdict == "edit" else None}
    meta[RULING_KEY] = ruling
    db.update("doc_threads", thread_id, {"metadata": json.dumps(meta)})
    return {"id": thread_id, "ruling": ruling}


def _landing_text(ruling: dict) -> str:
    """What a ruling puts in the form's green half: the text as seen for an
    acceptance, the author's for an edit, nothing for a decline."""
    if ruling["verdict"] == "decline":
        return ""
    if ruling["verdict"] == "edit":
        return ruling["text"]
    return ruling["seen"]


# ----------------------------------------------------------------- merging

def _embed_lines(text: str) -> list[str]:
    return [ln for ln in text.split("\n") if EMBED_LINE.match(ln)]


def older_version_of(db: Database, manuscript: dict, rel: str, doc: str,
                     local: str) -> int | None:
    """The version number of an earlier snapshot of `rel` that the Doc's
    text is, when it is not the local file's text: the tab is then behind
    the file, not ahead of it. None when the Doc's text is no earlier
    version."""
    target = gdocs.text_hash(doc)
    if target == gdocs.text_hash(local):
        return None
    rows = db.all(
        "SELECT version_no, json_extract(files, ?) AS text FROM "
        "manuscript_versions WHERE manuscript_id = ? ORDER BY version_no DESC",
        ('$."' + rel.replace('"', '\\"') + '"', manuscript["id"]))
    for row in rows:
        raw = row["text"]
        if not raw:
            continue
        normalized = normalize_markdown(raw)
        for text in (strip_embed_lines(normalized), normalized,
                     strip_embed_lines(raw), raw):
            if gdocs.text_hash(text) == target:
                return row["version_no"]
    return None


def keep_embeds(before: str, after: str, root: Path
                ) -> tuple[str, list[str], list[str]]:
    """`after` with every illustration embed line of `before` it lost put
    back: under its tag (`illus.reembed`, the pick `before` made), else
    right after the line that preceded it in `before`. Returns (text, the
    lines put back, the lines that found no place)."""
    from .illus import capture_embeds, reembed

    def lost_from(text: str) -> list[str]:
        have = _embed_lines(text)
        return [ln for ln in _embed_lines(before) if ln not in have]

    lost = lost_from(after)
    if not lost:
        return after, [], []
    text = reembed(after, root, capture_embeds(before))
    lines = text.split("\n")
    old = before.split("\n")
    for line in lost_from(text):
        at = old.index(line)
        prev = next((old[i] for i in range(at - 1, -1, -1)
                     if old[i].strip()), None)
        if prev is not None and prev in lines:
            lines.insert(lines.index(prev) + 1, line)
    text = "\n".join(lines)
    missing = lost_from(text)
    return text, [ln for ln in lost if ln not in missing], missing


def merge(db: Database, manuscript: dict, file: str, service, docs_service,
          config: dict | None = None) -> dict:
    """Land one file's ruled forms (see the module docstring for the
    steps). Returns {file, stopped, message, accepted, modified, declined,
    warnings, pushed, url}; `stopped` names why nothing landed (conflict,
    undecided, changed, stale, pull, resolve) and is None when it did. A
    conflict also carries `diff`, the two sides of `doc diff`."""
    rel = file
    threads = open_threads(db, manuscript, rel).get(rel, [])
    links = gdocs.doc_status(db, manuscript)
    tab = (links.get(rel) or {}).get("tab_id") if isinstance(
        links.get(rel), dict) else None
    result: dict[str, Any] = {
        "file": rel, "stopped": None, "message": "", "accepted": 0,
        "modified": 0, "declined": 0, "warnings": [], "pushed": False,
        "url": gdocs.tab_url(links.get("_master_id"), tab)
        if links.get("_master_id") else None}

    def stop(why: str, message: str, **extra: Any) -> dict:
        result.update(stopped=why, message=message, **extra)
        return result

    if not threads:
        return stop("none", f"{rel} has no open forms")
    view = gdocs.doc_three_way(db, manuscript, rel, service, docs_service)
    if view["state"] == "conflict":
        return stop("conflict",
                    f"{rel} changed both in the file and in its tab since the "
                    f"last push. Nothing was pulled, landed or pushed. Compare "
                    f"the two sides under Conflicts.",
                    diff=_diff_sides(view))

    pairs = api.pair_external_forms(view["tab"], threads)
    undecided = [t for t in threads if t["id"] in pairs and not ruling_of(t)]
    if undecided:
        return stop("undecided",
                    f"{len(undecided)} form(s) in {rel} have no ruling yet: "
                    + "; ".join("«" + clamp(t.get("proposed_old")
                                                  or t["proposed_new"]) + "»"
                                for t in undecided)
                    + ". Merging lands every form in the tab, so rule on each "
                      "first.")
    moved = [t for t in threads if t["id"] in pairs
             and pairs[t["id"]]["new"] != ruling_of(t)["seen"]]
    if moved:
        return stop("changed",
                    f"{len(moved)} form(s) in {rel} read differently in the "
                    f"Doc than when you ruled on them: "
                    + "; ".join("«" + clamp(pairs[t["id"]]["new"]) + "»"
                                for t in moved)
                    + ". Refresh and rule on them again.")

    path = Path(manuscript["path"]) / rel
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    if view["state"] == "doc_ahead":
        version = older_version_of(db, manuscript, rel, view["doc"],
                                   view["local"])
        if version is not None:
            return stop("stale",
                        f"{rel}'s tab holds an older text of the file "
                        f"(version {version}), not a new edit; pulling it "
                        f"would undo what the file gained since. Nothing was "
                        f"pulled or landed. Run 'authorlm doc diff {rel}' and "
                        f"bring the tab up to the file before merging.")
        report = gdocs.pull_doc(db, manuscript, rel, service=service,
                                docs_service=docs_service)
        if rel in report.get("conflicts", []) or rel in report.get(
                "missing", []):
            return stop("pull", f"the pull of {rel} did not come down "
                                f"cleanly ({', '.join(k for k in ('conflicts', 'missing') if rel in report.get(k, []))}); "
                                f"nothing was landed")
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, manuscript, config or {}, source="forms-page-pull")
        if rel in report.get("changed", []):
            result["warnings"].append(
                f"{rel} was edited in the Doc since the last push; that edit "
                f"was pulled into the file first.")

    by_origin: dict[tuple[str, str], list[dict]] = {}
    for t in threads:
        # One `lens resolve` lands every lens's forms in the file at once.
        key = ((api.LENS_ORIGIN, "lens") if t["origin_type"] == api.LENS_ORIGIN
               else (t["origin_type"], t["origin"]))
        by_origin.setdefault(key, []).append(t)
    for (producer, origin), group in by_origin.items():
        rulings = {t["id"]: _landing_text(ruling_of(t)) for t in group
                   if t["id"] in pairs}
        if producer == api.FILTER_ORIGIN:
            done = _land_filter(db, manuscript, rel, origin, rulings,
                                service, docs_service, config)
        elif producer == api.LENS_ORIGIN:
            done = _land_lens(db, manuscript, rel, rulings,
                              service, docs_service, config)
        else:
            done = api.resolve_revisions(db, manuscript, rel, origin,
                                         service, docs_service, config,
                                         rulings=rulings)
        result["warnings"] += done.get("warnings") or []
        if not done.get("landed"):
            return stop("resolve",
                        f"the {origin} forms in {rel} did not land "
                        f"(state {done.get('state')}); see the warnings")
        for k in ("accepted", "modified", "declined"):
            result[k] += done.get(k) or 0

    after = path.read_text(encoding="utf-8") if path.exists() else ""
    kept, put_back, missing = keep_embeds(before, after,
                                          Path(manuscript["path"]))
    if put_back:
        path.write_text(kept, encoding="utf-8")
        result["warnings"].append(
            f"{len(put_back)} illustration line(s) the landing dropped were "
            f"put back in {rel}: " + "; ".join(put_back))
    if missing:
        result["warnings"].append(
            f"{len(missing)} illustration line(s) are gone from {rel} and "
            f"could not be put back, so nothing was pushed; restore them "
            f"from the file's version history: " + "; ".join(missing))
        result["message"] = f"{rel}: landed, but illustration lines are missing"
        return result

    pending = gdocs.forms_pending(db, manuscript["id"], rel)
    if pending:
        result["warnings"].append(
            f"not pushed: {rel}'s tab still carries {pending} forms, which "
            f"are settled by their own resolve.")
    elif gdocs.unchanged_since_push(db, manuscript, rel):
        result["pushed"] = True
    else:
        try:
            gdocs.push_doc(db, manuscript, rel, service=service,
                           docs_service=docs_service, guard=True)
            result["pushed"] = True
        except Exception as err:  # noqa: BLE001 — the guard and Google's errors
            result["warnings"].append(f"not pushed: {err}")
    result["message"] = (f"{rel}: {result['accepted']} accepted, "
                         f"{result['modified']} modified, "
                         f"{result['declined']} declined"
                         + ("" if result["pushed"] else "; the tab was not "
                            "brought up to the file"))
    return result


def _land_filter(db: Database, manuscript: dict, rel: str, name: str,
                 rulings: dict[str, str], service, docs_service,
                 config: dict | None) -> dict:
    """One filter run's forms in `rel`, landed by `filter resolve` (the
    tab read back: untouched accepts, an emptied green half declines, a
    rewording is the author's), with the page's rulings typed in first.
    The same shape as `resolve_revisions`' result; the push is merge's."""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            done = api.filter_resolve(
                db, manuscript, config or {}, rel, name=name,
                services=lambda: (service, docs_service), rulings=rulings)
    except (LookupError, ValueError) as err:
        return {"landed": False, "state": "filter", "warnings": [str(err)]}
    modified = len(done.get("diffs") or [])
    return {"landed": True, "warnings": done.get("warnings") or [],
            "accepted": done["accepted"] - modified, "modified": modified,
            "declined": done["declined"]}


def _land_lens(db: Database, manuscript: dict, rel: str,
               rulings: dict[str, str], service, docs_service,
               config: dict | None) -> dict:
    """Every lens form in `rel`, landed by `lens resolve` (the tab read
    back: untouched accepts, an emptied green half declines, a rewording
    is the author's), with the page's rulings typed in first. Its own
    forms leave the tab in place; the push is merge's."""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            done = api.lens_resolve(
                db, manuscript, config or {}, rel,
                services=lambda: (service, docs_service), rulings=rulings)
    except (LookupError, ValueError) as err:
        return {"landed": False, "state": "lens", "warnings": [str(err)]}
    modified = len(done.get("diffs") or [])
    return {"landed": True, "warnings": done.get("warnings") or [],
            "accepted": done["accepted"] - modified, "modified": modified,
            "declined": done["declined"]}


# --------------------------------------------------------------- conflicts
# A file changed both in the file and in its tab since the last push is
# settled here instead of by hand (`doc merge`, edit the markers, `doc
# pull --force`, copy, collect, `doc push`; done so on 2026-11-12). The
# two sides merge against the last pushed base (`gdocs.merge3`); a region
# only one side changed merges by itself, and each region both changed is
# a card: Keep mine, Keep the Doc's, Keep both, or Edit. Choices are held
# in the manuscript's metadata (`conflict_rulings`, by file, by region id)
# until the file lands, as form rulings are held on their threads.
#
# A region's id is the hash of its three texts, so a region that reads
# the same after a recompute keeps its choice, and one that changed is a
# new card. The whole conflict has a `key` (the hash of the three sides);
# landing takes the key the page showed and recomputes first: a side
# that moved since means a new state to look at, not a landing.

CONFLICT_KEY = "conflict_rulings"
CONFLICT_VERDICTS = ("mine", "doc", "both", "edit")


def _sides_key(local: str, base: str, doc: str) -> str:
    return gdocs.text_hash(local + "\x00" + base + "\x00" + doc)


def conflict_segments(local: str, base: str, doc: str
                      ) -> list[str | dict]:
    """The three-way merge of `local` and `doc` against `base`, as a list
    of clean text (str, already merged) and regions both sides changed
    ({id, local, base, doc}, each the region's lines with their line
    ends). Joining the clean parts with one choice per region gives the
    merged text."""
    merged, _ = gdocs.merge3(local, base, doc, diff3=True)
    out: list[str | dict] = []
    clean: list[str] = []
    region: dict | None = None
    side = ""
    seen: dict[str, int] = {}
    for line in merged.splitlines(keepends=True):
        bare = line.rstrip("\n")
        if region is None and bare == "<<<<<<< local":
            if clean:
                out.append("".join(clean))
                clean = []
            region = {"local": "", "base": "", "doc": ""}
            side = "local"
        elif region is not None and side == "local" and bare == "||||||| base":
            side = "base"
        elif region is not None and side in ("local", "base") \
                and bare == "=======":
            side = "doc"
        elif region is not None and side == "doc" and bare == ">>>>>>> doc":
            rid = _sides_key(region["local"], region["base"], region["doc"])
            seen[rid] = seen.get(rid, 0) + 1
            region["id"] = rid if seen[rid] == 1 else f"{rid}-{seen[rid]}"
            out.append(region)
            region = None
        elif region is not None:
            region[side] += line
        else:
            clean.append(line)
    if region is not None:  # an unterminated region: never guessed at
        raise LookupError("the three-way merge came back with an unclosed "
                          "region; settle this file with 'authorlm doc "
                          "merge' by hand")
    if clean:
        out.append("".join(clean))
    return out


def _touches_tag(*texts: str) -> bool:
    from .illus import parse_tag

    return any(parse_tag(ln) for t in texts for ln in t.split("\n"))


def _wrap_like(text: str, like: str) -> str:
    """`text` with the blank lines `like` had before and after it, so a
    choice sits in the merged text as the region did."""
    body = like.strip("\n")
    if not body:
        return text.strip("\n") + "\n"
    lead = like[:len(like) - len(like.lstrip("\n"))]
    trail = like[len(like.rstrip("\n")):] or "\n"
    return lead + text.strip("\n") + trail


def region_text(region: dict, ruling: dict) -> str:
    """What one choice puts in place of a region."""
    verdict = ruling["verdict"]
    mine, doc = region["local"], region["doc"]
    if verdict == "mine":
        return mine
    if verdict == "doc":
        return doc
    if verdict == "both":
        if not mine.strip():
            return doc
        if not doc.strip():
            return mine
        return mine.rstrip("\n") + "\n\n" + doc.lstrip("\n")
    like = mine if mine.strip() else doc
    return _wrap_like(ruling.get("text") or "", like)


def _held_rulings(db: Database, manuscript: dict, rel: str) -> dict:
    return dict((gdocs._read_metadata(db, manuscript).get(CONFLICT_KEY)
                 or {}).get(rel) or {})


def _forms_out(db: Database, manuscript: dict, rel: str) -> str | None:
    """Why a conflict cannot land yet: forms are out in its tab (one
    producer at a time). None when it can."""
    n = len(open_threads(db, manuscript, rel).get(rel, []))
    others = _other_forms(db, manuscript, rel)
    if not n and not others:
        return None
    parts = ([f"{n} open form{'s' if n != 1 else ''}"] if n else []) + [
        f"{k} {o} form{'s' if k != 1 else ''}" for o, k in others.items()]
    return (f"This tab still has {' and '.join(parts)} out. One change lands "
            f"on a tab at a time, so this conflict can be settled here but "
            f"lands only once those forms are merged.")


def conflict_view(db: Database, manuscript: dict, rel: str, view: dict
                  ) -> dict:
    """The page's Conflicts entry for one file, from its `doc_three_way`
    view: {key, base_found, note, regions: [{id, local, doc, base,
    illustration, ruling}], decided, held, ready}. `held` says why it
    cannot land (forms out); `ready` is every card decided and nothing
    held."""
    out: dict[str, Any] = {"key": None, "base_found": False, "note": "",
                           "regions": [], "decided": 0,
                           "held": _forms_out(db, manuscript, rel),
                           "ready": False}
    if view.get("base") is None:
        out["note"] = (f"No pushed base is on record for {rel}, so the two "
                       f"sides cannot be told apart here. Run 'authorlm doc "
                       f"diff {rel}' and settle it by hand.")
        return out
    local, base, doc = view["local"], view["base"], view["doc"]
    rulings = _held_rulings(db, manuscript, rel)
    out.update(key=_sides_key(local, base, doc), base_found=True)
    for seg in conflict_segments(local, base, doc):
        if isinstance(seg, str):
            continue
        ruling = rulings.get(seg["id"])
        out["regions"].append({
            "id": seg["id"], "local": seg["local"].strip("\n"),
            "doc": seg["doc"].strip("\n"), "base": seg["base"].strip("\n"),
            "illustration": _touches_tag(seg["local"], seg["doc"],
                                         seg["base"]),
            "ruling": ruling})
    out["decided"] = sum(1 for r in out["regions"] if r["ruling"])
    out["ready"] = (out["decided"] == len(out["regions"])
                    and not out["held"])
    return out


def settle(db: Database, manuscript: dict, file: str, region: str,
           verdict: str, text: str | None = None) -> dict:
    """Hold a choice on one conflict card until the file lands: mine,
    doc, both, or edit (with `text`, the region as the author wants it;
    paragraphs separated by blank lines), or clear to take it back.
    Nothing is written to the Doc or the file."""
    if not file or not region:
        raise ValueError("a choice names its file and its card")
    meta = gdocs._mapping(db, manuscript)
    held = meta.setdefault(CONFLICT_KEY, {})
    mine = held.setdefault(file, {})
    if verdict == "clear":
        mine.pop(region, None)
        if not mine:
            held.pop(file, None)
        gdocs._save_mapping(db, manuscript, meta)
        return {"file": file, "region": region, "ruling": None}
    if verdict not in CONFLICT_VERDICTS:
        raise ValueError(f"a choice is one of {', '.join(CONFLICT_VERDICTS)} "
                         f"or clear")
    if verdict == "edit":
        text = "\n".join(ln.rstrip() for ln in (text or "").split("\n")
                         ).strip("\n")
        if not text.strip():
            raise ValueError("an edit needs text; to keep one side as it "
                             "is, choose that side")
        form_grammar.assert_no_pending_markers("", text)
        if any(ln.startswith(("<<<<<<<", "=======", ">>>>>>>", "|||||||"))
               for ln in text.split("\n")):
            raise ValueError("the text still holds merge markers; take "
                             "them out")
    ruling = {"verdict": verdict, "at": now_iso(),
              "text": text if verdict == "edit" else None}
    mine[region] = ruling
    gdocs._save_mapping(db, manuscript, meta)
    return {"file": file, "region": region, "ruling": ruling}


def _clear_rulings(db: Database, manuscript: dict, rel: str) -> None:
    meta = gdocs._mapping(db, manuscript)
    if rel in (meta.get(CONFLICT_KEY) or {}):
        meta[CONFLICT_KEY].pop(rel, None)
        gdocs._save_mapping(db, manuscript, meta)


def land_conflict(db: Database, manuscript: dict, file: str, key: str | None,
                  service, docs_service, config: dict | None = None) -> dict:
    """Land one file's settled conflict: recompute the regions and stop
    (`moved`, with the new `conflict`) when either side changed since the
    page's `key`; then a forced pull (the Doc's text becomes the base;
    the file's text was collected first, so it stays in history), the
    merged text written with every picture line kept, collect, and the
    normal push (surgical when the tab carries comments or threads).
    Returns the merge result's shape with `kept` (counts per choice);
    `stopped` is forms, gone, nobase, moved, undecided, pictures, pull,
    or None when it landed."""
    rel = file
    links = gdocs.doc_status(db, manuscript)
    tab = (links.get(rel) or {}).get("tab_id") if isinstance(
        links.get(rel), dict) else None
    result: dict[str, Any] = {
        "file": rel, "stopped": None, "message": "", "conflict_landing": True,
        "accepted": 0, "modified": 0, "declined": 0,
        "kept": {v: 0 for v in CONFLICT_VERDICTS},
        "warnings": [], "pushed": False,
        "url": gdocs.tab_url(links.get("_master_id"), tab)
        if links.get("_master_id") else None}

    def stop(why: str, message: str, **extra: Any) -> dict:
        result.update(stopped=why, message=message, **extra)
        return result

    held = _forms_out(db, manuscript, rel)
    if held:
        return stop("forms", held + " Nothing was pulled or pushed.")
    view = gdocs.doc_three_way(db, manuscript, rel, service, docs_service)
    if view["state"] != "conflict":
        return stop("gone", f"{rel} is no longer changed on both sides "
                            f"(now: {view['state']}). Refresh the page.")
    info = conflict_view(db, manuscript, rel, view)
    if not info["base_found"]:
        return stop("nobase", info["note"], conflict=info)
    if key != info["key"]:
        return stop("moved", f"{rel} changed in the file or in its tab since "
                             f"these cards were shown. Here is the new "
                             f"state; check the cards and Merge again. "
                             f"Nothing was pulled or pushed.",
                    conflict=info)
    open_cards = [r for r in info["regions"] if not r["ruling"]]
    if open_cards:
        return stop("undecided", f"{len(open_cards)} card(s) in {rel} have "
                                 f"no choice yet.", conflict=info)

    rulings = _held_rulings(db, manuscript, rel)
    parts: list[str] = []
    for seg in conflict_segments(view["local"], view["base"], view["doc"]):
        if isinstance(seg, str):
            parts.append(seg)
        else:
            ruling = rulings[seg["id"]]
            result["kept"][ruling["verdict"]] += 1
            parts.append(region_text(seg, ruling))
    merged = normalize_markdown("".join(parts))

    root = Path(manuscript["path"])
    path = root / rel
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    from .illus import capture_embeds, reembed
    final, _back, missing = keep_embeds(
        before, reembed(merged, root, capture_embeds(before)), root)
    if missing:
        return stop("pictures",
                    f"{len(missing)} picture line(s) of {rel} would find no "
                    f"place in the merged text (its tag is gone?): "
                    + "; ".join(missing) + ". Nothing was pulled or pushed; "
                    "keep the side that has the tag, or put the tag back "
                    "in an edit.", conflict=info)

    quiet = contextlib.redirect_stdout(io.StringIO())
    with quiet:
        # The file's own text goes into history before the pull covers it.
        api.collect(db, manuscript, config or {}, source="forms-page-conflict")
    report = gdocs.pull_doc(db, manuscript, rel, service=service,
                            docs_service=docs_service, force=True)
    landed_pull = rel in report.get("changed", []) or rel in report.get(
        "unchanged", [])
    if not landed_pull:
        why = [k for k in ("conflicts", "missing", "marked", "unterminated",
                           "sidecar_unparsable") if rel in report.get(k, [])]
        return stop("pull", f"the forced pull of {rel} did not come down "
                            f"({', '.join(why) or 'no reason given'}); "
                            f"nothing was written or pushed")
    path.write_text(final, encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, config or {}, source="forms-page-conflict")
    _clear_rulings(db, manuscript, rel)
    if gdocs.unchanged_since_push(db, manuscript, rel):
        result["pushed"] = True
    else:
        try:
            gdocs.push_doc(db, manuscript, rel, service=service,
                           docs_service=docs_service, guard=True)
            result["pushed"] = True
        except Exception as err:  # noqa: BLE001 — the guard and Google's errors
            result["warnings"].append(f"not pushed: {err}")
    k = result["kept"]
    result["message"] = (f"{rel}: {len(info['regions'])} card(s) settled "
                         f"({k['mine']} kept mine, {k['doc']} kept the "
                         f"Doc's, {k['both']} kept both, {k['edit']} edited)"
                         + ("" if result["pushed"] else "; the tab was not "
                            "brought up to the file"))
    return result


# ----------------------------------------------------------------- transport

# ------------------------------------------------------------------ queue
# The page may press Merge on several tabs; the merges run one after
# another on a thread of this same web server, never side by side: each
# merge snapshots the whole manuscript and checks the whole Doc's
# revision, so two at once could interleave. The queue lives in the
# server's memory: a restart forgets what was waiting (nothing half-done
# is lost; a merge either lands and pushes or stops and says why).

_QUEUES: dict[tuple, dict] = {}
_QLOCK = threading.Lock()


def _queue(key: tuple) -> dict:
    return _QUEUES.setdefault(key, {"pending": [], "running": None,
                                    "results": {}, "thread": None})


def queue_status(key: tuple) -> dict:
    with _QLOCK:
        q = _queue(key)
        return {"pending": list(q["pending"]), "running": q["running"],
                "results": dict(q["results"])}


def _run_queue(key: tuple, run_one) -> None:
    while True:
        with _QLOCK:
            q = _queue(key)
            if not q["pending"]:
                q["running"] = None
                q["thread"] = None
                return
            file = q["pending"].pop(0)
            q["running"] = file
            fn = q.setdefault("calls", {}).pop(file, run_one)
        try:
            result = fn(file)
        except Exception as err:  # noqa: BLE001 — reported on the page
            result = {"file": file, "stopped": "error",
                      "message": f"{type(err).__name__}: {err}",
                      "accepted": 0, "modified": 0, "declined": 0,
                      "warnings": [], "pushed": False, "url": None}
        if result.get("error"):
            result = {"file": file, "stopped": "error",
                      "message": result["error"], "accepted": 0,
                      "modified": 0, "declined": 0, "warnings": [],
                      "pushed": False, "url": None}
        with _QLOCK:
            q = _queue(key)
            q["results"][file] = result
            q["running"] = None


def enqueue(key: tuple, file: str, run_one, start: bool = True) -> dict:
    """Add `file` to the queue (once) and start the worker if it is idle.
    `run_one(file)` does one merge on the worker's thread."""
    with _QLOCK:
        q = _queue(key)
        if file != q["running"] and file not in q["pending"]:
            q["pending"].append(file)
            q["results"].pop(file, None)
            # Each entry runs the call it was queued with (a forms merge
            # or a conflict landing), on the one worker.
            q.setdefault("calls", {})[file] = run_one
        if start and q["thread"] is None:
            q["thread"] = threading.Thread(
                target=_run_queue, args=(key, run_one), daemon=True,
                name=f"merge-forms-{key[1]}")
            q["thread"].start()
    return queue_status(key)


def dispatch(method: str, params: dict[str, Any],
             workspace: str | None = None) -> dict:
    """The page's API (`/api/forms`): list, decide, merge (at once),
    enqueue (merge on the queue's thread; with `conflict: true` and the
    `key` the page showed, land the file's settled conflict instead),
    queue (its state), settle (hold a choice on one conflict card) and
    land (a conflict landing at once). Every method
    names the manuscript. The database and the Google services are the
    audio page's session's, as for the Dev edit."""
    from . import audiobook

    session = audiobook.session(workspace, params.get("manuscript"))
    manuscript = session.manuscript
    db = session._thread_db()
    try:
        if method == "list":
            if not gdocs.doc_status(db, manuscript).get("_master_id"):
                return listing(db, manuscript, None, None)
            _config, service, docs_service = session._doc_services()
            return listing(db, manuscript, service, docs_service)
        if method == "decide":
            return decide(db, manuscript, str(params["id"]),
                          str(params["verdict"]), params.get("seen"),
                          params.get("text"))
        if method == "merge":
            config, service, docs_service = session._doc_services()
            return merge(db, manuscript, str(params["file"]), service,
                         docs_service, config)
        if method == "settle":
            return settle(db, manuscript, str(params["file"]),
                          str(params["region"]), str(params["verdict"]),
                          params.get("text"))
        if method == "land":
            config, service, docs_service = session._doc_services()
            return land_conflict(db, manuscript, str(params["file"]),
                                 params.get("key"), service, docs_service,
                                 config)
        key = (workspace, manuscript["name"])
        if method == "enqueue":
            seen_key = params.get("key")
            conflict = bool(params.get("conflict"))

            def run_one(file: str) -> dict:
                # The worker's own thread: its own database connection.
                tdb = session._thread_db()
                config, service, docs_service = session._doc_services()
                if conflict:
                    return land_conflict(tdb, manuscript, file, seen_key,
                                         service, docs_service, config)
                return merge(tdb, manuscript, file, service, docs_service,
                             config)
            return enqueue(key, str(params["file"]), run_one)
        if method == "queue":
            return queue_status(key)
    except (LookupError, ValueError, KeyError) as err:
        return {"error": str(err)}
    return {"error": f"unknown method '{method}'"}
