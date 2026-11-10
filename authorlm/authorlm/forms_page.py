"""The Merge forms page: every open outside revision of a manuscript in one
place, ruled on there and merged one file at a time.

An outside caller (`revise stage`) puts its revisions in a file's Doc tab as
`<<old>>{{new}}` forms; the author rules on them in the tab and `revise
resolve` lands them. With forms spread over many tabs the author hunted for
them; this page lists them all, grouped by file, with the text each form's
green half holds NOW (so an edit the author made in the Doc shows), the
paragraph around it, and Accept / Edit / Decline.

A ruling is held on its thread (`metadata.page_ruling`) until the file is
merged; nothing touches the Doc or the file before that. Merge does, for one
file, what the author would do by hand, and stops where a person must look:

1. Read the file three ways (`gdocs.doc_three_way`). Changed on both sides
   since the last push (`conflict`): stop, show both sides of `doc diff`.
   No forced pull (it drops local edits) and no push over it.
2. Every form still in the tab has a ruling, and its green half still reads
   as it did when it was ruled; otherwise stop and say which.
3. Only the tab moved (`doc_ahead`): pull it first, an ordinary pull. A tab
   whose text is an OLDER version of the local file is stale, not an edit,
   and is never pulled (2026-11-10: a staging pulled a stale tab over the
   file and reverted another session's accepted edits).
4. `api.resolve_revisions` per origin, with the page's rulings applied to
   the tab's text as if typed there (`rulings`). It keeps a local edit the
   tab has not seen, puts the illustration embed lines back, and clears the
   tab (a push, or `settle_forms_in_tab`).
5. Every `![](_illustrations/...)` line the file had is still there, or is
   put back under its tag; one that cannot be is said, and nothing is
   pushed.
6. A guarded push (`push_doc(guard=True)`) when the tab is not yet the file.

The page is `/api/forms` on the audiobook server (`audiobook_server.py`);
`dispatch` is its transport, as `devedit.dispatch` is the Dev edit's.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
from pathlib import Path
from typing import Any

from . import api, form_grammar, gdocs
from .db import Database, loads, now_iso
from .normalize import clamp, normalize_markdown
from .revisions import EMBED_LINE, strip_embed_lines

RULING_KEY = "page_ruling"
VERDICTS = ("accept", "edit", "decline")


# ----------------------------------------------------------------- reading

def open_threads(db: Database, manuscript: dict,
                 file: str | None = None) -> dict[str, list[dict]]:
    """file → its open outside-revision threads (`written`), in staging
    order. Files with none are absent."""
    sql = ("SELECT * FROM doc_threads WHERE manuscript_id = ? "
           "AND origin_type = ? AND state = 'written'")
    args: list[Any] = [manuscript["id"], api.EXTERNAL_ORIGIN]
    if file is not None:
        sql += " AND file = ?"
        args.append(file)
    out: dict[str, list[dict]] = {}
    for row in db.all(sql + " ORDER BY created_at, id", tuple(args)):
        out.setdefault(row["file"], []).append(dict(row))
    return out


def _meta(thread: dict) -> dict:
    return loads(thread.get("metadata"), {}) or {}


def ruling_of(thread: dict) -> dict | None:
    """The page's held ruling on a thread: {verdict, text, seen, at}."""
    return _meta(thread).get(RULING_KEY)


def _other_forms(db: Database, manuscript: dict, file: str) -> dict[str, int]:
    """origin_type → how many of its forms are out in `file`'s tab, for
    every producer that is not an outside caller (a pass, a margin
    proposal). These are not ruled here; they also block new forms."""
    rows = db.all(
        "SELECT origin_type, COUNT(*) AS n FROM doc_threads WHERE "
        "manuscript_id = ? AND file = ? AND state = 'written' "
        "AND origin_type != ? GROUP BY origin_type",
        (manuscript["id"], file, api.EXTERNAL_ORIGIN))
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


def _form_row(thread: dict, form: dict | None, marked: str | None,
              local: str) -> dict:
    meta = _meta(thread)
    old = thread.get("proposed_old") or ""
    row: dict[str, Any] = {
        "id": thread["id"], "origin": meta.get("origin") or "",
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
    """Every open form, by file, as the Doc holds it now. Read-only: one
    export of the master Doc (`gdocs.read_manuscript`), and for a file in
    conflict its three-way view. No Google call when nothing is open."""
    groups = open_threads(db, manuscript)
    links = gdocs.doc_status(db, manuscript)
    master = links.get("_master_id")
    out: dict[str, Any] = {"doc_url": gdocs.tab_url(master) if master else None,
                           "files": [], "total": 0}
    if not groups:
        return out
    read = gdocs.read_manuscript(db, manuscript, service, docs_service)
    root = Path(manuscript["path"])
    for rel in sorted(groups):
        threads = groups[rel]
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
            "blocking": (f"This tab takes no new forms until its {len(rows)} "
                         f"open form(s) are merged"
                         + (" and the other producers' forms ("
                            + ", ".join(f"{n} {o}" for o, n in others.items())
                            + ") are settled" if others else "") + ".")}
        if state == "conflict":
            try:
                info["diff"] = _diff_sides(gdocs.doc_three_way(
                    db, manuscript, rel, service, docs_service))
            except LookupError as err:
                info["diff"] = {"base_found": False, "doc": "", "local": "",
                                "note": str(err)}
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
    if row is None or row["origin_type"] != api.EXTERNAL_ORIGIN:
        raise LookupError(f"no outside revision {thread_id} on this manuscript")
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
                    f"the two sides below, then settle them with 'authorlm doc "
                    f"merge {rel}' before merging here.",
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

    by_origin: dict[str, list[dict]] = {}
    for t in threads:
        by_origin.setdefault(_meta(t).get("origin") or "", []).append(t)
    for origin, group in by_origin.items():
        rulings = {t["id"]: _landing_text(ruling_of(t)) for t in group
                   if t["id"] in pairs}
        done = api.resolve_revisions(db, manuscript, rel, origin, service,
                                     docs_service, config, rulings=rulings)
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


# ----------------------------------------------------------------- transport

def dispatch(method: str, params: dict[str, Any],
             workspace: str | None = None) -> dict:
    """The page's API (`/api/forms`): list, decide, merge. Every method
    names the manuscript. The database and the Google services are the
    audio page's session's, as for the Dev edit."""
    from . import audiobook

    session = audiobook.session(workspace, params.get("manuscript"))
    manuscript = session.manuscript
    db = session._thread_db()
    try:
        if method == "list":
            groups = open_threads(db, manuscript)
            if not groups:
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
    except (LookupError, ValueError, KeyError) as err:
        return {"error": str(err)}
    return {"error": f"unknown method '{method}'"}
