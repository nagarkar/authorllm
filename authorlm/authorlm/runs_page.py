"""The Filters and lenses page (`runs_page.dispatch`, `/api/runs`): browse
every filter and lens a manuscript sees, edit an artifact's text in place,
and ask for runs of one or more of them on one or more tabs.

**What "apply" does here, and what it does not.** A filter or lens run gets
its model output from a Claude Code session: `filter run` and `lens run`
print a payload and make no model call, an empty-context subagent drafts
the reply, and `filter apply` / `lens register` stage the forms and push
them to the tab (the skill's filter beat and lens road). The billed road
(`--native`) is off for filters by ruling (`[filtering]` is absent from
config.toml) and is not the standing road for lenses. So this page makes
NO model call and stages NO forms itself. It writes one `run_requests` row
per (artifact, tab) and shows that row's state truthfully: `queued` until a
session picks it up (`authorlm requests`), then whatever the run's own rows
say (units recorded, forms staged, forms in the tab, settled). The state is
DERIVED from `filter_runs`, `doc_threads` and the lens findings on every
read, so a request can never say more than the run has done.

**Guards are the verbs' own.** A tab with forms out, a protected register
(lenses), an artifact that does not parse, and a filter that already ran on
this exact text are shown with the reason and are not queued. A tab checked
out to the Doc is queued with what it needs said beside it: the session
pulls the tab first (`doc pull`, the remedy the checkout gate itself names),
and the request's verbs start with that pull. Nothing here forces past a
guard; the session's verbs stop on the same things. Protected terms need nothing from this page: they
ride in the payload the session assembles.

**Editing.** Both kinds are files (`_filters/<name>.md`, `_lenses/<name>.md`
in the manuscript; `library/filters/`, `library/lenses/` in the repo), so
both are editable. `save` parses the new text with the artifact's own
parser before anything is written, declines a name or a path outside those
four folders, declines when the file changed on disk since the page read
it, and copies the text it replaces to `<workspace>/prompt-backups/` first.
That copy is the recovery for a manuscript's own artifact (manuscript data
is not in git); a library artifact is also tracked by the repo's git.
"""

from __future__ import annotations

import hashlib
import os
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import api, filters as flt, gdocs, lenses, passes, paths, structure
from .db import Database, ko_fields, loads, now_iso
from .revisions import read_manuscript_files
from .structure import reading_order, title_of

KINDS = ("filter", "lens")
BACKUP_DIR = "prompt-backups"
# A request is work for a session while it is in one of these states.
WAITING = ("queued", "drafting")
# What a pair the workflow holds back says the page can do about it.
SKIP_HINT = ("To ask anyway, outside the workflow, give one sentence of why "
             "in the box below; it is recorded on the essay's workflow.")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"kind is 'filter' or 'lens', not {kind!r}")
    return kind


# ------------------------------------------------------------- the folders

def folders(manuscript: dict, kind: str) -> dict[str, Path]:
    """The two folders an artifact of this kind may live in. Nothing on
    this page reads or writes an artifact anywhere else."""
    if _kind(kind) == "filter":
        return {"manuscript": Path(manuscript["path"]) / flt.FILTER_DIR,
                "library": flt.library_dir()}
    return {"manuscript": Path(manuscript["path"]) / lenses.LENS_DIR,
            "library": lenses.library_dir()}


def artifact_path(manuscript: dict, kind: str, name: str,
                  source: str | None = None) -> tuple[Path, str]:
    """`(path, source)` for a named artifact: the manuscript's own when it
    has one, else the library's (`source` names one of the two outright).
    The name is a kebab-case slug, so it cannot carry a path; the resolved
    file must still sit directly in its folder, which keeps a symlink from
    leading a write elsewhere."""
    dirs = folders(manuscript, kind)
    if not lenses._NAME.match(name or ""):
        raise ValueError(f"{kind} names are short kebab-case slugs, e.g. "
                         "'duplicate-words'")
    if source is None:
        source = ("manuscript"
                  if (dirs["manuscript"] / f"{name}.md").is_file()
                  else "library")
    if source not in dirs:
        raise ValueError("source is 'manuscript' or 'library'")
    path = dirs[source] / f"{name}.md"
    if path.resolve().parent != dirs[source].resolve():
        raise ValueError(f"{path} is outside the {kind} folders; this page "
                         "leaves it alone")
    return path, source


def _parse(kind: str, text: str) -> dict:
    """The artifact's own parser. Raises its error (a ValueError) with the
    legal values named."""
    if kind == "filter":
        meta, _body = flt.parse_front_matter(text)
        return meta
    meta, body = lenses.parse_lens(text)
    if not body.strip():
        raise lenses.LensError("a lens is its prompt; there is nothing "
                               "after the front matter.")
    return meta


# --------------------------------------------------------------- last runs

def _lens_batches(db: Database, manuscript: dict) -> list[dict]:
    """Every lens batch of the manuscript: lens, file, when, findings."""
    batches: dict[str, dict] = {}
    for row in db.all(
            "SELECT batch_id, created_at, metadata FROM guidance_history "
            "WHERE manuscript_id = ? AND kind = ? ORDER BY created_at",
            (manuscript["id"], lenses.LENS_KIND)):
        meta = loads(row["metadata"], {}) or {}
        b = batches.setdefault(row["batch_id"], {
            "lens": meta.get("lens"), "file": meta.get("file"),
            "created_at": row["created_at"] or "", "findings": 0})
        b["findings"] += 1
    return sorted(batches.values(), key=lambda b: b["created_at"])


def _filter_runs(db: Database, manuscript: dict) -> list[dict]:
    """`filter status`'s rows, each with its full creation time."""
    runs = api.filter_status(db, manuscript)["runs"]
    born = {r["id"]: r["created_at"] or "" for r in db.all(
        "SELECT id, created_at FROM filter_runs WHERE manuscript_id = ?",
        (manuscript["id"],))}
    return [dict(r, created_at=born.get(r["id"], "")) for r in runs]


def _lens_forms_out(db: Database, manuscript: dict) -> dict[tuple, int]:
    """(lens, file) -> its forms out in that file's tab."""
    out: dict[tuple, int] = {}
    for row in db.all(
            "SELECT file, origin_id FROM doc_threads WHERE manuscript_id = ? "
            "AND origin_type = ? AND state = 'written'",
            (manuscript["id"], api.LENS_ORIGIN)):
        # origin_id is '<lens>@<hash>:<file>:<ordinal>'.
        name = (row["origin_id"] or "").split(":", 1)[0].split("@", 1)[0]
        out[(name, row["file"])] = out.get((name, row["file"]), 0) + 1
    return out


# -------------------------------------------------------------------- tabs

def tabs(db: Database, manuscript: dict,
         files: dict[str, str] | None = None) -> list[dict]:
    """The manuscript's chapter files in reading order, each with what a
    run would meet there: forms out (by producer), the Doc checkout, and
    whether it is a protected register."""
    if files is None:
        files = read_manuscript_files(Path(manuscript["path"]))
    order, _unlisted = reading_order(files)
    links = gdocs.doc_status(db, manuscript)
    master = links.get("_master_id")
    out_forms: dict[str, dict[str, int]] = {}
    for row in db.all(
            "SELECT file, origin_type, COUNT(*) AS n FROM doc_threads WHERE "
            "manuscript_id = ? AND state = 'written' GROUP BY file, "
            "origin_type", (manuscript["id"],)):
        out_forms.setdefault(row["file"], {})[row["origin_type"]] = row["n"]
    protected = set(lenses.protected_files(files))
    toc_text = files.get(structure.TOC_FILENAME) or ""
    rows = []
    for rel in order:
        text = files[rel]
        entry = links.get(rel) if isinstance(links.get(rel), dict) else {}
        tab_id = entry.get("tab_id")
        rows.append({
            "file": rel, "title": title_of(text) or rel,
            "words": len(text.split()),
            "units": len(passes.paragraphs_of(text)),
            "checked_out": bool(entry.get("checked_out")),
            "forms_out": out_forms.get(rel, {}),
            "protected": rel in protected,
            "owner_edits": structure.owner_edits_reason(toc_text, rel),
            "url": gdocs.tab_url(master, tab_id) if master and tab_id
            else None})
    return rows


def tab_needs(tab: dict) -> str | None:
    """What a session must do on this tab before a run, or None: a tab
    checked out to the Doc is pulled first, because the Doc is the working
    copy and the file on disk is behind it."""
    if tab["checked_out"]:
        return ("checked out to Google Docs, so the Doc is the working "
                "copy. The session pulls it first: authorlm doc pull "
                f"{tab['file']}")
    return None


def tab_blocker(tab: dict) -> str | None:
    """Why no new run may be asked for on this tab now, or None: one
    producer's forms per tab. A file only the owner edits (toc `edits =
    "owner"`) takes no run at all."""
    if tab.get("owner_edits"):
        return tab["owner_edits"]
    if tab["forms_out"]:
        whose = ", ".join(f"{n} from the {origin} pass"
                          for origin, n in sorted(tab["forms_out"].items()))
        return (f"{sum(tab['forms_out'].values())} form(s) are already in "
                f"this tab ({whose}). Settle them on Merge forms first; two "
                "producers' forms in one tab cannot be told apart.")
    return None


def blocker(db: Database, manuscript: dict, kind: str, artifact: dict,
            tab: dict, text: str) -> str | None:
    """Why this one run cannot be queued, or None. Each reason is one the
    run's own verbs would stop on."""
    if artifact.get("error"):
        return (f"the {kind} '{artifact['name']}' does not parse: "
                f"{artifact['error']}")
    why = tab_blocker(tab)
    if why:
        return why
    if kind == "lens":
        if tab["protected"]:
            return ("a protected register: no lens runs on it; the register "
                    "protects its voice by design.")
        return None
    mid = manuscript["id"]
    if api._active_filter_run(db, mid, artifact["name"], tab["file"]):
        return None                       # the request joins the live run
    for prior in api._settled_runs(db, mid, artifact["name"], tab["file"]):
        produced = api._version_text(db, prior["result_version_id"],
                                     tab["file"])
        if produced is not None and produced == text:
            return (f"{artifact['name']} already ran on this exact text "
                    f"(settled {(prior['created_at'] or '')[:10]}) and "
                    "nothing has changed since. A session can look again "
                    f"with: authorlm filter run {artifact['name']} "
                    f"{tab['file']} --again")
    return None


# ----------------------------------------------------------------- catalog

def _config() -> dict:
    paths.load_env()
    path = paths.config_path()
    return tomllib.loads(path.read_text()) if path.exists() else {}


def drafting(config: dict) -> dict:
    """How each kind's run gets its model output, read from the same
    configuration the verbs read. The page prints this beside Apply."""
    filtering = ((config.get("filtering") or {}).get("model") or "").strip()
    llm = config.get("llm") or {}
    return {
        "filter": {
            "road": "session",
            "native_model": filtering or None,
            "note": ("A Claude Code session drafts each filter reply from "
                     "the payload 'filter run' prints. The billed road "
                     "('filter run --native') is "
                     + (f"configured ({filtering}) but is not started from "
                        "this page." if filtering else
                        "off: [filtering] is absent from config.toml."))},
        "lens": {
            "road": "session",
            "native_model": (llm.get("model") if llm.get("enabled")
                             else None),
            "note": ("A Claude Code session drafts each lens reply from the "
                     "payload 'lens run' prints. 'lens run --native' would "
                     "send it to the [llm] model instead; this page does "
                     "not start that.")}}


def catalog(db: Database, manuscript: dict,
            config: dict | None = None) -> dict:
    """Everything the page lists: filters, lenses, tabs, the requests and
    how runs are drafted. Reads only."""
    runs = _filter_runs(db, manuscript)
    batches = _lens_batches(db, manuscript)
    filters = []
    for f in flt.list_filters(manuscript):
        path, source = artifact_path(manuscript, "filter", f["name"])
        mine = [r for r in runs if r["filter"] == f["name"]]
        last = mine[-1] if mine else None
        filters.append({
            "kind": "filter", "name": f["name"], "class": f["class"],
            "summary": f["summary"], "error": f["error"],
            "source": source, "path": str(path),
            "prelude": f.get("prelude"),
            "runs": len(mine),
            "last": last and {
                "date": last["date"], "file": last["file"],
                "status": last["status"], "proposed": last["proposed"],
                "accepted": last["accepted"], "rejected": last["rejected"],
                "forms_out": last["forms_out"]}})
    lens_rows = []
    for item in lenses.list_lenses(manuscript):
        path, source = artifact_path(manuscript, "lens", item["name"])
        mine = [b for b in batches if b["lens"] == item["name"]]
        last = mine[-1] if mine else None
        lens_rows.append({
            "kind": "lens", "name": item["name"], "class": item["class"],
            "summary": item["summary"], "error": item["error"],
            "source": source, "path": str(path),
            "targets": item["targets"], "runs": len(mine),
            "last": last and {"date": last["created_at"][:10],
                              "file": last["file"],
                              "findings": last["findings"]}})
    files = read_manuscript_files(Path(manuscript["path"]))
    tab_rows = tabs(db, manuscript, files)
    for tab in tab_rows:
        tab["blocked"] = tab_blocker(tab)
        tab["needs"] = tab_needs(tab)
    return {"manuscript": manuscript["name"], "filters": filters,
            "lenses": lens_rows, "tabs": tab_rows,
            "requests": requests(db, manuscript, runs=runs, batches=batches),
            "drafting": drafting(_config() if config is None else config)}


def show(manuscript: dict, kind: str, name: str) -> dict:
    """One artifact's whole file, as it is on disk (front matter and all),
    with the hash `save` checks. A file that does not parse is still shown,
    with its error, so it can be mended here."""
    path, source = artifact_path(manuscript, _kind(kind), name)
    if not path.is_file():
        raise LookupError(f"no {kind} '{name}'")
    text = path.read_text(encoding="utf-8")
    try:
        meta, error = _parse(kind, text), None
    except ValueError as err:
        meta, error = None, str(err)
    return {"kind": kind, "name": name, "source": source, "path": str(path),
            "text": text, "sha": _sha(text), "meta": meta, "error": error,
            "recovery": recovery_note(source)}


def recovery_note(source: str) -> str:
    """Where the replaced text of a save can be found again."""
    copy = (f"the text a save replaces is copied to {BACKUP_DIR}/ in the "
            "workspace folder first")
    if source == "library":
        return (f"Library file, shared by every manuscript: {copy}, and the "
                "repo's git tracks the file (commit the change there).")
    return (f"This manuscript's own file: {copy}. Manuscript data is not in "
            "git, so that copy is the recovery.")


# -------------------------------------------------------------------- save

def _workspace_dir(db: Database) -> Path:
    return Path(db.path).parent


def save(db: Database, manuscript: dict, kind: str, name: str, text: str,
         seen: str | None, to: str | None = None) -> dict:
    """Write an edited artifact back to its file. In order: the name and
    the folder are checked; the text is parsed by the artifact's own
    parser (a text that does not parse is never written); the file must
    still be the one the page read (`seen` is its hash); the text it
    replaces is copied to the workspace's backups; then one atomic write.

    `to="manuscript"` on a library artifact writes this manuscript's own
    copy instead (what `filter add` / `lens add` of a library name does)
    and leaves the library file as it is."""
    kind = _kind(kind)
    path, source = artifact_path(manuscript, kind, name)
    if to is not None and to != source:
        if to != "manuscript":
            raise ValueError("a manuscript's own artifact moves to the "
                             "library with 'lens promote', not from here")
        path, source = artifact_path(manuscript, kind, name, "manuscript")
    if not (text or "").strip():
        raise ValueError(f"a {kind} is its prompt; the text is empty and "
                         "nothing was written")
    meta = _parse(kind, text)
    text = text.replace("\r\n", "\n").strip() + "\n"
    old = path.read_text(encoding="utf-8") if path.is_file() else None
    if old is not None and seen != _sha(old):
        raise ValueError(
            f"{path.name} changed on disk since this page read it, so "
            "nothing was written. Reload the text and make the edit again.")
    backup = None
    if old is not None and old != text:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        owner = manuscript["name"] if source == "manuscript" else "library"
        backup = (_workspace_dir(db) / BACKUP_DIR / owner / f"{kind}s"
                  / f"{name}.{stamp}.md")
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(old, encoding="utf-8")
    if old != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.saving")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    warnings = []
    if kind == "filter":
        live = [dict(r) for r in db.all(
            "SELECT file FROM filter_runs WHERE manuscript_id = ? AND "
            "filter = ? AND status = 'active'", (manuscript["id"], name))]
        if live:
            warnings.append(
                f"{len(live)} run(s) of '{name}' are active "
                f"({', '.join(r['file'] for r in live)}): their next window "
                "reads the new prompt; their class stays as it started.")
    return {"kind": kind, "name": name, "source": source, "path": str(path),
            "text": text, "sha": _sha(text), "meta": meta, "error": None,
            "changed": old != text,
            "backup": str(backup) if backup else None,
            "warnings": warnings, "recovery": recovery_note(source)}


# ---------------------------------------------------------------- requests

def _progress(req: dict, runs: list[dict], batches: list[dict],
              lens_out: dict[tuple, int]) -> dict:
    """A request's state, read from what its run has put on record."""
    if req["status"] == "cancelled":
        return {"state": "cancelled", "detail": "taken off the list"}
    if req["kind"] == "filter":
        mine = [r for r in runs if r["filter"] == req["name"]
                and r["file"] == req["file"]
                and (r["created_at"] >= req["created_at"]
                     or r["status"] == "active")]
        if not mine:
            return {"state": "queued",
                    "detail": "waiting for a session to draft it"}
        run = mine[-1]
        if run["status"] == "abandoned":
            return {"state": "abandoned", "detail": "the run was abandoned"}
        if run["status"] == "settled":
            return {"state": "settled",
                    "detail": f"{run['accepted']} accepted, "
                              f"{run['rejected']} declined"}
        if run["cursor"] < run["unit_count"]:
            return {"state": "drafting",
                    "detail": f"units {run['cursor']} of "
                              f"{run['unit_count']} recorded"}
        if run["forms_out"]:
            return {"state": "in_doc", "url": run["tab_url"],
                    "detail": f"{run['forms_out']} form(s) in the tab, "
                              "waiting for your ruling"}
        if run["proposed"]:
            return {"state": "staged",
                    "detail": f"{run['proposed']} proposal(s) staged, not "
                              "yet in the tab"}
        return {"state": "clean", "detail": "read every unit; no proposals"}
    mine = [b for b in batches if b["lens"] == req["name"]
            and b["file"] == req["file"]
            and b["created_at"] >= req["created_at"]]
    if not mine:
        return {"state": "queued",
                "detail": "waiting for a session to draft it"}
    found = sum(b["findings"] for b in mine)
    out = lens_out.get((req["name"], req["file"]), 0)
    if out:
        return {"state": "in_doc",
                "detail": f"{found} finding(s); {out} form(s) in the tab, "
                          "waiting for your ruling"}
    return {"state": "findings",
            "detail": f"{found} finding(s) registered; none is a form in "
                      "the tab now"}


def commands(manuscript: dict, req: dict, klass: str | None,
             checked_out: bool = False) -> list[str]:
    """The verbs a session runs for one request, in order. A tab checked
    out to the Doc is pulled first."""
    m = f" -m {manuscript['name']}"
    name, file = req["name"], req["file"]
    steps = [f"authorlm doc pull {file}{m}"] if checked_out else []
    if req["kind"] == "lens":
        return steps + [
            f"authorlm lens run {name} {file} --out <payload>{m}",
            "(an empty-context subagent answers the payload)",
            f"authorlm lens register {name} {file} --reply <json>{m}"]
    if klass == "global":
        steps.append(f"authorlm filter prelude {name} {file}{m}")
    steps += [f"authorlm filter run {name} {file}{m}",
              "(an empty-context subagent answers the payload)",
              f"authorlm filter apply {name} {file}{m}   (reply on stdin)"]
    return steps


def requests(db: Database, manuscript: dict, runs: list[dict] | None = None,
             batches: list[dict] | None = None) -> list[dict]:
    """Every request not taken off the list, oldest first, each with its
    derived state and the verbs that carry it out."""
    rows = [dict(r) for r in db.all(
        "SELECT * FROM run_requests WHERE manuscript_id = ? AND "
        "status != 'cancelled' ORDER BY created_at, id",
        (manuscript["id"],))]
    if not rows:
        return []
    runs = _filter_runs(db, manuscript) if runs is None else runs
    batches = _lens_batches(db, manuscript) if batches is None else batches
    lens_out = _lens_forms_out(db, manuscript)
    classes = {f["name"]: f["class"] for f in flt.list_filters(manuscript)}
    links = gdocs.doc_status(db, manuscript)
    out = []
    for r in rows:
        progress = _progress(r, runs, batches, lens_out)
        entry = links.get(r["file"])
        pull = (progress["state"] == "queued" and isinstance(entry, dict)
                and bool(entry.get("checked_out")))
        if pull:
            progress["detail"] += ("; the tab is checked out to the Doc, so "
                                   "it is pulled first")
        out.append({"id": r["id"], "kind": r["kind"], "name": r["name"],
                    "file": r["file"], "asked_at": r["created_at"],
                    **progress,
                    "commands": commands(manuscript, r,
                                         classes.get(r["name"]), pull)})
    return out


def enqueue(db: Database, manuscript: dict, items: list[dict],
            files: list[str], dry_run: bool = False,
            skip_reason: str | None = None) -> dict:
    """Ask for a run of each artifact on each tab. Returns, per pair, one
    of: queued (a new row), already (a request for it is still waiting), or
    blocked (with the reason; nothing is written for it). `dry_run` says
    what would happen and writes nothing. No model is called either way.

    A run is asked for through the essay's workflow (workflow/roads.py,
    §11.9 Q2): a tab whose workflow is not at its filter step is blocked
    with `workflow: true` and how to start or continue the workflow, unless
    `skip_reason` (one sentence) is given; then the skip is recorded on the
    essay's workflow (not on a dry run) and the request is queued."""
    from .workflow import roads
    if not items or not files:
        raise ValueError("choose at least one filter or lens and one tab")
    texts = read_manuscript_files(Path(manuscript["path"]))
    by_file = {t["file"]: t for t in tabs(db, manuscript, texts)}
    known = {("filter", f["name"]): f for f in flt.list_filters(manuscript)}
    known.update({("lens", item["name"]): item
                  for item in lenses.list_lenses(manuscript)})
    waiting = {(r["kind"], r["name"], r["file"])
               for r in requests(db, manuscript) if r["state"] in WAITING}
    queued, already, blocked = [], [], []
    for file in files:
        tab = by_file.get(file)
        if tab is None:
            raise LookupError(f"'{file}' is not a tab of "
                              f"{manuscript['name']}")
        for item in items:
            kind, name = _kind(str(item.get("kind"))), str(item.get("name"))
            artifact = known.get((kind, name))
            if artifact is None:
                raise LookupError(f"no {kind} '{name}'")
            row = {"kind": kind, "name": name, "file": file}
            why = blocker(db, manuscript, kind, artifact, tab, texts[file])
            if why:
                blocked.append({**row, "reason": why})
            elif (kind, name, file) in waiting:
                already.append(row)
            else:
                try:
                    roads.enter(db, manuscript, file, kind, name=name,
                                reason=skip_reason, via="filters view",
                                dry_run=dry_run, skip_hint=SKIP_HINT)
                except roads.SentToWorkflow as err:
                    blocked.append({**row, "reason": str(err),
                                    "workflow": True})
                    continue
                if not dry_run:
                    fields = ko_fields("rq")
                    fields.update(manuscript_id=manuscript["id"], kind=kind,
                                  name=name, file=file, status="queued",
                                  cancelled_at=None)
                    db.insert("run_requests", fields)
                    row["id"] = fields["id"]
                waiting.add((kind, name, file))
                queued.append(row)
    return {"queued": queued, "already": already, "blocked": blocked,
            "dry_run": dry_run,
            "requests": requests(db, manuscript)}


def cancel(db: Database, manuscript: dict, request_id: str) -> dict:
    """Take a request off the list. It stops nothing a session has begun:
    a run that exists is ended with its own verb (`filter abandon`)."""
    rows = [dict(r) for r in db.all(
        "SELECT * FROM run_requests WHERE manuscript_id = ? AND id LIKE ?",
        (manuscript["id"], f"{request_id}%"))]
    if len(rows) != 1:
        raise LookupError(f"no one request matches '{request_id}'")
    db.update("run_requests", rows[0]["id"],
              {"status": "cancelled", "cancelled_at": now_iso()})
    return {"cancelled": rows[0]["id"],
            "requests": requests(db, manuscript)}


# ---------------------------------------------------------------- dispatch

def dispatch(method: str, params: dict[str, Any],
             workspace: str | None = None) -> dict:
    """The page's API (`/api/runs`): catalog, show, save, preview (what a
    set of requests would do, nothing written), queue, cancel, and the
    Lint view's `lint.*` methods (lint_page.dispatch). Every method names
    the manuscript. The database is the audio page's session's, as for
    Merge forms. No method calls a model; only `lint.apply` and
    `lint.undo` call Google (the tab's pull and push)."""
    from . import audiobook

    session = audiobook.session(workspace, params.get("manuscript"))
    manuscript = session.manuscript
    db = session._thread_db()
    try:
        if method.startswith("lint."):
            # The Lint view of this page (lint_page.py).
            from . import lint_page
            return lint_page.dispatch(method[5:], params, session)
        if method == "catalog":
            return catalog(db, manuscript)
        if method == "show":
            return show(manuscript, str(params["kind"]), str(params["name"]))
        if method == "save":
            return save(db, manuscript, str(params["kind"]),
                        str(params["name"]), str(params.get("text") or ""),
                        params.get("seen"), params.get("to"))
        if method in ("preview", "queue"):
            return enqueue(db, manuscript, params.get("items") or [],
                           [str(f) for f in params.get("files") or []],
                           dry_run=method == "preview",
                           skip_reason=params.get("skip_reason") or None)
        if method == "cancel":
            return cancel(db, manuscript, str(params["id"]))
    except (LookupError, ValueError, KeyError) as err:
        return {"error": str(err)}
    return {"error": f"unknown method '{method}'"}
