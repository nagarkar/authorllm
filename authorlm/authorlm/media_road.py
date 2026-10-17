"""The Doc road for clip specs (docs/video-clips-design.md §8e).

A drafted spec is reviewed where the author reads: when the file is
checked out to the Doc, `stage_specs` writes each spec into the tab as a
pending form, `<<[Clip: excerpt… ⇢ key.md | …]>>{{[Clip: whole spec ⇢
key.md | …]}}`, and the local file stays pristine. The author edits or
accepts in place (deleting a form declines it). `resolve_specs` reads the
tab back, moves every accepted text into its kind's file (a YouTube brief,
a generation spec or a note, `media.SPEC_DIRS`), puts the tag back to
excerpt + `⇢`, records the evidence, collects under no
episode and pushes the tab. The same machinery as the footnote and
explain directives (directives.py), with one difference: a clip's tag is
never consumed, only its spec file changes.

A file that is NOT checked out takes the direct road, `media.set_spec`:
chat was the review.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

from . import media

ORIGIN = "clip"


def _services(services, file: str, step: str):
    if services is None:
        raise ValueError(f"{file} is checked out to the Doc: the spec {step} "
                         "needs the Drive/Docs services")
    return services() if callable(services) else services


def stage_specs(db, manuscript: dict, specs: dict[str, str],
                services=None, supersede: bool = False) -> dict:
    """Write each {key: spec} as a pending form into its file's tab. All
    keys must sit in one file (one tab, one resolve). `supersede`
    withdraws this file's clip forms already out (the author has not
    ruled on them) before staging: writing the new forms first brings the
    tab level with the pristine local file, which clears the old marks.
    Returns {file, staged: [key], failed: [(key, reason)], withdrawn, url}."""
    from . import gdocs
    from .db import ko_fields
    from .revisions import _paragraphs

    if not specs:
        raise ValueError("no spec to stage")
    root = Path(manuscript["path"])
    forms = [media.spec_form(root, key, text) for key, text in specs.items()]
    files = {f["file"] for f in forms}
    if len(files) != 1:
        raise ValueError("stage the specs of one file at a time; these span "
                         + ", ".join(sorted(files)))
    file = files.pop()
    mid = manuscript["id"]
    withdrawn = 0
    if supersede:
        out = pending(db, mid, file)
        for t in out:
            meta = json.loads(t.get("metadata") or "{}")
            meta["superseded"] = True
            db.update("doc_threads", t["id"],
                      {"state": "withdrawn", "metadata": json.dumps(meta)})
            withdrawn += 1
        if out:
            # As lens and morph supersede (api.lens_push): the withdrawn
            # forms leave the tab only when it is rebuilt from the file,
            # and the push leaves the file checked out, so pull it down.
            service, docs_service = _services(services, file, "staging")
            # Paragraph by paragraph, overwriting only the withdrawn forms
            # (a tab with open comments is never rebuilt wholesale).
            keys = tuple(f"⇢ {json.loads(t.get('metadata') or '{}')['key']}.md"
                         for t in out)
            gdocs.diff_push(db, manuscript, file, service, docs_service,
                            clearing=keys)
            if (gdocs.doc_status(db, manuscript).get(file) or {}).get(
                    "checked_out"):
                gdocs.pull_doc(db, manuscript, query=file, service=service,
                               docs_service=docs_service, with_comments=False)
    pending_origin = gdocs.forms_pending(db, mid, file)
    if pending_origin:
        remedy = gdocs._SETTLE_REMEDY.get(pending_origin,
                                          "settle the staged edits")
        hint = (" (or restage with --supersede to withdraw them)"
                if pending_origin == ORIGIN else "")
        raise ValueError(f"{file} already carries {pending_origin} forms in its "
                         f"tab — run '{remedy.format(file=file)}' before "
                         f"staging more{hint}.")
    text = (root / file).read_text(encoding="utf-8")
    lines = text.split("\n")
    threads = []
    for form in forms:
        before = "\n".join(lines[:form["i"]])
        anchor = len(_paragraphs(before + "\nx")) if before.strip() else 1
        row = ko_fields("dt")
        row.update(
            manuscript_id=mid, origin_type=ORIGIN,
            origin_id=f"{ORIGIN}:{file}:{row['id'][-12:]}", file=file,
            anchor_quote=form["old"][:200], proposed_old=form["old"],
            proposed_new=form["new"], note=f"spec for {form['key']}",
            state="proposed", our_reply_ids="[]", last_author_reply_id=None,
            scope_kind="file", scope_ref=file,
            metadata=json.dumps({"kind": "replace", "anchor_paragraph": anchor,
                                 "intent_id": None,
                                 "original_new": form["new"],
                                 "gist": form["key"], "line": form["i"] + 1,
                                 "key": form["key"], "directive": ORIGIN}))
        db.insert("doc_threads", row)
        threads.append(row)
    service, docs_service = _services(services, file, "staging")
    result = gdocs.write_pending_forms(db, manuscript, file, threads,
                                       service, docs_service)
    written = {t["id"] for t in result["written"]}
    for t in threads:
        db.update("doc_threads", t["id"],
                  {"state": "written" if t["id"] in written else "withdrawn"})
    return {"file": file, "mode": "marked", "withdrawn": withdrawn,
            "staged": [json.loads(t["metadata"])["key"] for t in threads
                       if t["id"] in written],
            "failed": [(json.loads(t["metadata"])["key"], why)
                       for t, why in result["failed"]],
            "url": result["url"]}


def pending(db, manuscript_id: str, file: str | None = None) -> list[dict]:
    """Spec forms out in a tab (state `written`)."""
    from .directives import pending as directive_pending

    return directive_pending(db, manuscript_id, ORIGIN, file)


def resolve_specs(db, manuscript: dict, config: dict, file: str,
                  services=None) -> dict:
    """Finish the Doc road: read the tab, honour the author's edits to
    every spec form, land the accepted specs in their files, record the
    evidence, collect under no episode, push the tab. Returns {file,
    forms, landed: [{key, words}], declined, version_no, url, warnings}."""
    from . import api, gdocs, passes

    mid = manuscript["id"]
    written = passes.staged_threads(db, mid, file, states=("written",),
                                    origin_type=ORIGIN)
    if not written:
        raise LookupError(f"{file} has no clip spec forms out in its tab — "
                          "nothing to resolve.")
    keys = {json.loads(t["metadata"] or "{}").get("key") for t in written}
    service, docs_service = _services(services, file, "resolve")
    fetched = gdocs.tab_marked_markdown(db, manuscript, file, service,
                                        docs_service)
    if fetched["state"] == "missing":
        raise LookupError(f"'{file}' has no matching section in the master Doc "
                          f"export — 'doc push {file}' rebuilds the tab from "
                          "the local file, which still holds the excerpts.")
    if fetched["state"] == "conflict":
        raise ValueError(f"'{file}' changed both locally and in the Doc since "
                         "the last sync — compare them by hand, then re-run "
                         f"'clip resolve {file}'.")
    warnings = list(fetched["marker_warnings"])
    final, forms = passes.final_text_from_marked(fetched["marked"],
                                                 written=written)
    root = Path(manuscript["path"])
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, config, source="pre-clip-resolve")
    passes.record_resolution(db, mid, file, forms, origin_type=ORIGIN,
                             evidence_type="clip_spec_edit", final_text=final)
    final, landed = media.absorb_specs(root, final, keys)
    api._write_resolved_text(root / file, root, final)
    with contextlib.redirect_stdout(io.StringIO()):
        after = api.collect(db, manuscript, config, source="clip-resolve",
                            episode=api.NO_EPISODE)
    states = [db.one("SELECT state FROM doc_threads WHERE id = ?",
                     (t["id"],))["state"] for t in written]
    url = None
    try:
        url = gdocs.push_doc(db, manuscript, file, service=service,
                             docs_service=docs_service).get("url")
    except Exception as err:                            # noqa: BLE001
        warnings.append(f"the specs landed locally but the push failed ({err}) "
                        f"— 'doc push {file}' clears the marks.")
    return {"file": file, "forms": len(forms), "landed": landed,
            "declined": states.count("declined"),
            "version_no": after.get("version_no"), "url": url,
            "warnings": warnings}
