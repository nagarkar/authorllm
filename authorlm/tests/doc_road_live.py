"""Drive the Doc-road failure scenarios (test_doc_road_failures.py)
against the LIVE Google API on the one fixture Doc, "AuthorLM test
fixture: Doc road", and record every call into tests/doc_cache/.

    python3 tests/doc_road_live.py            # run live, report, no record
    python3 tests/doc_road_live.py --record   # run live and re-record

Not part of the hermetic suite (the file name does not start with
test_). It needs the owner's Google token (~/.authorlm/gdocs_token.json);
the database is a throwaway workspace, never ~/.authorlm. Every request
is checked before it is sent: only the fixture Doc and the temp import
Docs this run creates may be named, so no manuscript Doc can be touched.
Spends nothing beyond ordinary Docs and Drive API calls.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import gzip
import io
import json
import re
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import googleapiclient.http as _http  # noqa: E402

from authorlm import api, gdocs  # noqa: E402
from authorlm import threads as th  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402

import test_doc_road_failures as F  # noqa: E402
from _doc_freshness import DOC_PREFIX, DOC_TAB, LOG  # noqa: E402

CACHE = HERE / "doc_cache"
FIXTURE = CACHE / "fixture_doc.json"
TITLE = "AuthorLM test fixture: Doc road"
NAME = "doc-road-fixture"          # the manuscript; its container tab
FORBIDDEN = {"1VDLq5605h6f9yykyMCtWnsCOP9lnMIsTdVl6IrgF2ls"}  # SMSTTD

# ------------------------------------------------------------ recorder

ALLOWED: set[str] = set()
CALLS: list[dict] = []
SCENARIO = ["setup"]
_orig_execute = _http.HttpRequest.execute
_ID = re.compile(r"/(?:documents|files)/([A-Za-z0-9_-]{20,})")


def _scrub(value):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in ("emailAddress", "photoLink", "me", "permissionId"):
                continue
            if k == "displayName":
                v = "author"
            out[k] = _scrub(v)
        return out
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _execute(self, *a, **k):
    ids = _ID.findall(self.uri)
    for doc_id in ids:
        if doc_id in FORBIDDEN or doc_id not in ALLOWED:
            raise RuntimeError(f"refused: request names {doc_id}, which is "
                               "not the fixture Doc or a temp Doc of this run")
    started = time.time()
    try:
        got = _orig_execute(self, *a, **k)
    except Exception as err:
        CALLS.append({"scenario": SCENARIO[0], "method": self.method,
                      "uri": self.uri.split("&key=")[0],
                      "body": _body(self), "error": str(err)})
        raise
    if (self.method == "POST" and self.uri.split("?")[0].endswith("/files")
            and isinstance(got, dict) and got.get("id")):
        ALLOWED.add(got["id"])     # a temp import Doc this run created
    CALLS.append({
        "scenario": SCENARIO[0], "method": self.method,
        "uri": self.uri.split("&key=")[0], "body": _body(self),
        "seconds": round(time.time() - started, 2),
        "response": _scrub(got if not isinstance(got, bytes)
                           else got.decode("utf-8", "replace"))})
    return got


def _body(req):
    if not req.body or req.headers.get("content-type", "").startswith(
            "multipart"):
        return None if not req.body else "<media upload>"
    try:
        return _scrub(json.loads(req.body))
    except (ValueError, TypeError):
        return None


_http.HttpRequest.execute = _execute

# ------------------------------------------------------------ the Doc


def services():
    from authorlm import paths
    paths.load_env()
    svc = gdocs.get_service({}, None, interactive=False)
    docs = gdocs.get_docs_service({}, None, interactive=False)
    return svc, docs


def fixture_doc(svc) -> str:
    if FIXTURE.exists():
        doc_id = json.loads(FIXTURE.read_text())["doc_id"]
        ALLOWED.add(doc_id)
        return doc_id
    got = svc.files().create(body={"name": TITLE, "mimeType": gdocs.GDOC_MIME},
                             fields="id").execute()
    CACHE.mkdir(exist_ok=True)
    FIXTURE.write_text(json.dumps({"doc_id": got["id"], "title": TITLE,
                                   "url": gdocs.tab_url(got["id"])},
                                  indent=1) + "\n")
    return got["id"]


def stamp(docs, doc_id: str, when: str) -> None:
    """The recording time on one line, `Last recorded: <when>`, in the
    fixture Doc's own `fake-log` tab (created at the root once). No
    scenario reads that tab: it is not a manuscript file."""
    got = docs.documents().get(documentId=doc_id,
                               includeTabsContent=True).execute()
    tab = next((t for t in got.get("tabs", [])
                if t["tabProperties"].get("title") == DOC_TAB), None)
    if tab is None:
        reply = docs.documents().batchUpdate(documentId=doc_id, body={
            "requests": [{"addDocumentTab": {
                "tabProperties": {"title": DOC_TAB}}}]}).execute()
        tab_id = reply["replies"][0]["addDocumentTab"]["tabProperties"][
            "tabId"]
        end = 2
    else:
        tab_id = tab["tabProperties"]["tabId"]
        content = tab.get("documentTab", {}).get("body", {}).get(
            "content", [])
        end = content[-1]["endIndex"] if content else 2
    reqs = []
    if end > 2:
        reqs.append({"deleteContentRange": {"range": {
            "tabId": tab_id, "startIndex": 1, "endIndex": end - 1}}})
    reqs.append({"insertText": {"location": {"tabId": tab_id, "index": 1},
                                "text": DOC_PREFIX + when}})
    docs.documents().batchUpdate(documentId=doc_id,
                                 body={"requests": reqs}).execute()


def resolve_all_comments(svc, doc_id: str) -> None:
    """A fresh run starts with an empty margin: earlier runs' comments
    are resolved (never deleted)."""
    for c in gdocs.fetch_open_comments(svc, doc_id):
        svc.replies().create(fileId=doc_id, commentId=c["id"],
                             body={"content": "fixture reset",
                                   "action": "resolve"},
                             fields="id").execute()


def comment(svc, doc_id: str, quote: str) -> str:
    got = svc.comments().create(
        fileId=doc_id, fields="id",
        body={"content": "a note on this",
              "quotedFileContent": {"mimeType": "text/html",
                                    "value": quote}}).execute()
    return got["id"]


def edit(docs, doc_id: str, tab_id: str, old: str, new: str) -> None:
    """The author typing in the tab: `old` (as the tab shows it) becomes
    `new`, in one write."""
    span = gdocs._locate_in_tab(docs, doc_id, tab_id, old)
    assert span is not None, f"edit: {old!r} is not in the tab"
    reqs = [{"deleteContentRange": {"range": {
        "tabId": tab_id, "startIndex": span[0], "endIndex": span[1]}}}]
    if new:
        reqs.append({"insertText": {"location": {"tabId": tab_id,
                                                 "index": span[0]},
                                    "text": new}})
    docs.documents().batchUpdate(documentId=doc_id,
                                 body={"requests": reqs}).execute()


def tab_text(docs, doc_id: str, tab_id: str) -> str:
    return "".join(c for _, c in gdocs._tab_runs(docs, doc_id, tab_id))


def snapshot(svc, docs, doc_id: str, label: str) -> None:
    """The three reads the fakes model, side by side, at one state."""
    old = SCENARIO[0]
    SCENARIO[0] = f"{old}:snapshot:{label}"
    try:
        docs.documents().get(documentId=doc_id,
                             includeTabsContent=True).execute()
        if hasattr(gdocs, "MASTER_FIELDS"):
            docs.documents().get(documentId=doc_id, includeTabsContent=True,
                                 fields=gdocs.MASTER_FIELDS).execute()
        svc.files().export(fileId=doc_id,
                           mimeType=gdocs.MARKDOWN_MIME).execute()
        gdocs.fetch_open_comments(svc, doc_id)
    finally:
        SCENARIO[0] = old


# ------------------------------------------------------------ fixture


def tagged(name: str) -> str:
    """ESSAY with this file's own heading and sentences, so a comment's
    quote is attributed to one tab only."""
    return (F.ESSAY.replace("# **Discernment**", f"# **{name}**")
            .replace("We must be watchful.", f"We must be watchful ({name}).")
            .replace("Habit carries discernment forward.",
                     f"Habit carries discernment forward ({name})."))


FILES = {
    # Headings as the manuscripts write them (`# **Title**`): Google's
    # export gives an H1 back bold, so a plain `# Title` never round-trips.
    "s1a.md": F.ESSAY.replace("# **Discernment**", "# **s1a**"),
    "s1b.md": F.ESSAY.replace("# **Discernment**", "# **s1b**"),
    "s1c.md": F.ESSAY.replace("# **Discernment**", "# **s1c**"),
    "s2.md": tagged("s2"),
    "s3a.md": tagged("s3a").replace("forward (s3a).", "forward (s3a).[^D1]"),
    "s3b.md": tagged("s3b").replace("forward (s3b).", "forward (s3b).[^D1]"),
    "s3c.md": tagged("s3c").replace("forward (s3c).", "forward (s3c).[^D1]"),
    "s4.md": tagged("s4") + "\n[Illustration: a lone tracker ⇢ lone.md]\n",
    "s5a.md": tagged("s5a"),
    "s5b.md": tagged("s5b"),
}
EMBED = "![](_illustrations/lone-0a1b2c3d-01.png)"


def build(root: Path, svc, docs, doc_id: str):
    ws = root / "ws"
    ms = ws / NAME
    (ms / "_illustrations").mkdir(parents=True)
    (ms / "_illustrations" / "lone-0a1b2c3d-01.png").write_bytes(b"\x89PNG")
    for rel, text in FILES.items():
        (ms / rel).write_text(text)
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", NAME,
                  "--path", str(ms), "--extraction", "off"])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, {})
    meta = gdocs._mapping(db, manuscript)
    meta.setdefault("gdocs", {})["_master_id"] = doc_id
    gdocs._save_mapping(db, manuscript, meta)
    resolve_all_comments(svc, doc_id)
    for rel in FILES:
        gdocs.push_doc(db, manuscript, rel, service=svc, docs_service=docs,
                       rebuild=True)
    # An open comment whose quote is in every tab: unattributed, so every
    # tab pushes down the surgical path, as discernment.md did.
    comment(svc, doc_id, "The question is always the same.")
    # s4's image line goes in after the push: the Doc never holds it.
    (ms / "s4.md").write_text((ms / "s4.md").read_text().replace(
        "lone.md]\n", "lone.md]\n" + EMBED + "\n"))
    return ws, db, manuscript, ms


def tab_of(db, manuscript, rel: str) -> str:
    return gdocs._mapping(db, manuscript)["gdocs"][rel]["tab_id"]


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


# ------------------------------------------------------------ scenarios

RESULTS: list[tuple[str, bool, str]] = []


def run(name: str, fn, *a) -> None:
    SCENARIO[0] = name
    try:
        fn(*a)
    except F.Failed as err:
        RESULTS.append((name, False, str(err)))
    except Exception:  # noqa: BLE001
        RESULTS.append((name, False, traceback.format_exc()))
    else:
        RESULTS.append((name, True, ""))


def clean(text: str) -> bool:
    return not any(m in text for m in ("<<", ">>", "{{", "}}"))


def s1(env, rel: str, before=None, between=None, origin="ending-r1"):
    svc, docs, doc_id, db, manuscript, ms = env
    tab = tab_of(db, manuscript, rel)
    if before:
        before(db, manuscript)
    revs = [dict(r) for r in F.MIXED]
    staged = api.stage_revisions(db, manuscript, rel, revs, origin,
                                 svc, docs)
    F.expect("all eight staged", len(staged["written"]) == 8,
             str(staged["failed"]))
    snapshot(svc, docs, doc_id, f"{rel} staged")
    if between:
        between(tab)
    done = _quiet(api.resolve_revisions, db, manuscript, rel, origin,
                  svc, docs)
    snapshot(svc, docs, doc_id, f"{rel} resolved")
    return done, tab_text(docs, doc_id, tab), (ms / rel).read_text()


def s1a(env):
    svc, docs, doc_id, db, manuscript, ms = env
    done, tab, final = s1(env, "s1a.md")
    F.expect("eight accepted, tab clean",
             done["accepted"] == 8 and clean(tab), tab)
    exported = svc.files().export(fileId=doc_id,
                                  mimeType=gdocs.MARKDOWN_MIME).execute()
    exported = exported.decode() if isinstance(exported, bytes) else exported
    F.expect("the heading addition landed as a heading",
             "## **Momentum and Alignment**" in exported
             and "\\#\\# **Momentum and Alignment**" not in exported,
             exported[:3000])


def s1b(env):
    def before(db, m):
        row = F.ko_fields("dt")
        row.update(manuscript_id=m["id"], origin_type="external",
                   origin_id="old:dot", file="s1b.md", anchor_quote=None,
                   proposed_old="x, y", proposed_new=".", note="old",
                   state="cleaned", our_reply_ids="[]",
                   last_author_reply_id=None, scope_kind="file",
                   scope_ref="s1b.md",
                   metadata=json.dumps({"kind": "replace"}))
        db.insert("doc_threads", row)
    done, tab, final = s1(env, "s1b.md", before=before)
    F.expect("the tab is clean and resolve says so truthfully",
             clean(tab) and not done["tab_still_marked"], tab)


def s1c(env):
    svc, docs, doc_id, db, manuscript, ms = env

    def between(tab):
        edit(docs, doc_id, tab, "This danger can be met.}}",
             "This danger can be met, and planned for.}}")
        edit(docs, doc_id, tab, "{{Alignment answers this danger.}}", "")
    done, tab, final = s1(env, "s1c.md", between=between)
    F.expect("6/1/1", (done["accepted"], done["modified"],
                       done["declined"]) == (6, 1, 1),
             json.dumps({k: done[k] for k in ("accepted", "modified",
                                              "declined")}))
    F.expect("wording landed, tab clean",
             "This danger can be met, and planned for." in final
             and "Alignment answers this danger." not in final
             and clean(tab), final + "\n---\n" + tab)


def s2(env):
    svc, docs, doc_id, db, manuscript, ms = env
    rel = "s2.md"
    comment(svc, doc_id, "Habit carries discernment forward (s2).")
    (ms / rel).write_text((ms / rel).read_text().replace(
        "forward (s2).", "forward (s2). A new line the author wrote."))
    out = gdocs.push_doc(db, manuscript, rel, service=svc, docs_service=docs,
                         guard=True)
    F.expect("push holds the paragraph", bool(out.get("held")),
             json.dumps(out, default=str))
    revs = [{"old": "Does it deepen the capacity to distinguish and "
                    "become?", "new": "Does it deepen us?", "note": "r"}]
    api.stage_revisions(db, manuscript, rel, revs, "o2", svc, docs)
    _quiet(api.resolve_revisions, db, manuscript, rel, "o2", svc, docs)
    final = (ms / rel).read_text()
    F.expect("held edit kept", "A new line the author wrote." in final,
             final)


def _propose(env, rel: str, tag: str):
    """Two real margin proposals on `rel`, through pull and propose."""
    svc, docs, doc_id, db, manuscript, ms = env
    c1 = comment(svc, doc_id, f"We must be watchful ({tag}).")
    c2 = comment(svc, doc_id, "Now consider the other side. Where "
                              "discernment deepens, distinctions appear.")
    _quiet(gdocs.pull_doc, db, manuscript, query=rel, service=svc,
           docs_service=docs)
    for row in db.all("SELECT id FROM doc_comments WHERE comment_id IN "
                      "(?, ?)", (c1, c2)):
        db.update("doc_comments", row["id"], {"file": rel})
    gdocs.propose_change(db, manuscript, c1, f"We must be watchful ({tag}).",
                         "We must keep watch.", "n", service=svc,
                         docs_service=docs)
    gdocs.propose_change(db, manuscript, c2, "Now consider the other side.",
                         "Consider the other side.", "n", service=svc,
                         docs_service=docs)
    return c1, c2


def _unterminate(env, rel: str, tag: str):
    svc, docs, doc_id, db, manuscript, ms = env
    tab = tab_of(db, manuscript, rel)
    c1, c2 = _propose(env, rel, tag)
    edit(docs, doc_id, tab, "We must keep watch.}}", "We must keep watch.")
    snapshot(svc, docs, doc_id, f"{rel} unterminated")
    return c1, c2


def s3a(env):
    svc, docs, doc_id, db, manuscript, ms = env
    _unterminate(env, "s3a.md", "s3a")
    rep = _quiet(gdocs.pull_doc, db, manuscript, query="s3a.md",
                 service=svc, docs_service=docs, with_comments=False)
    text = (ms / "s3a.md").read_text()
    F.expect("no conflict, text kept, no marker, the file named",
             not rep["conflicts"]
             and "forward (s3a).[^D1]" in text
             and "Now consider the other side." in text
             and "<<" not in text and "{{" not in text
             and "s3a.md" in rep.get("unterminated", []),
             json.dumps(rep.get("conflicts")) + "\n" + text)


def s3b(env):
    svc, docs, doc_id, db, manuscript, ms = env
    c1, _ = _unterminate(env, "s3b.md", "s3b")
    before = (ms / "s3b.md").read_text()
    try:
        gdocs.decide_thread(db, manuscript, c1, "approve", service=svc,
                            docs_service=docs)
    except Exception as err:  # noqa: BLE001
        F.expect("a refusal leaves the file", (ms / "s3b.md").read_text()
                 == before, str(err))
        return
    text = (ms / "s3b.md").read_text()
    F.expect("no garble", "{{" not in text and "}}" not in text
             and "Now consider the other side." in text
             and "forward (s3b).[^D1]" in text, text)


def s3c(env):
    svc, docs, doc_id, db, manuscript, ms = env
    _unterminate(env, "s3c.md", "s3c")
    out = gdocs.doc_merge(db, manuscript, "s3c.md", svc, docs)
    merged = Path(out["out"]).read_text()
    F.expect("draft keeps the text", "forward (s3c).[^D1]" in merged
             and "Now consider the other side" in merged, merged)


def s4(env):
    svc, docs, doc_id, db, manuscript, ms = env
    tab = tab_of(db, manuscript, "s4.md")
    (ms / "s4.md").write_text((ms / "s4.md").read_text().replace(
        "Now consider", "Now, consider"))
    edit(docs, doc_id, tab, "The question is always", "The question is ever")
    out = gdocs.doc_merge(db, manuscript, "s4.md", svc, docs)
    merged = Path(out["out"]).read_text()
    F.expect("both edits, image kept", "Now, consider" in merged
             and "The question is ever" in merged and EMBED in merged,
             merged)


def s5a(env):
    svc, docs, doc_id, db, manuscript, ms = env
    rel = "s5a.md"
    c1, _ = _propose(env, rel, "s5a")
    th.set_state(db, dict(th.get_thread(db, manuscript["id"], c1)),
                 "cleaned")
    (ms / rel).write_text((ms / rel).read_text().replace(
        "We must be watchful (s5a).", "We must keep watch.").replace(
        "Habit carries", "Habit, edited locally, carries"))
    gdocs.push_doc(db, manuscript, rel, service=svc, docs_service=docs)
    tab = tab_text(docs, doc_id, tab_of(db, manuscript, rel))
    F.expect("lands, form gone", "Habit, edited locally" in tab
             and "<<We must be watchful" not in tab, tab)


def s5b(env):
    svc, docs, doc_id, db, manuscript, ms = env
    rel = "s5b.md"
    tab = tab_of(db, manuscript, rel)
    c1, _ = _propose(env, rel, "s5b")
    # The author edits inside the struck old half: the record no longer
    # matches, and the approval goes stale (the real path).
    edit(docs, doc_id, tab, "We must be watchful (s5b).",
         "We must be so watchful (s5b).")
    try:
        out = gdocs.decide_thread(db, manuscript, c1, "approve",
                                  service=svc, docs_service=docs)
    except LookupError as err:   # decide says stale by raising
        out = {"error": str(err)}
    state = th.get_thread(db, manuscript["id"], c1)["state"]
    F.expect("the approval went stale", state == "stale",
             json.dumps(out, default=str))
    (ms / rel).write_text((ms / rel).read_text().replace(
        "Habit carries", "Habit, edited locally, carries"))
    try:
        res = gdocs.push_doc(db, manuscript, rel, service=svc,
                             docs_service=docs)
    except LookupError as err:
        raise F.Failed(f"push blocked: {err}")
    text = tab_text(docs, doc_id, tab)
    F.expect("pushed or held, no leftover block",
             "Habit, edited locally" in text or res.get("held"), text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true",
                    help="write the calls to tests/doc_cache/*.json")
    ap.add_argument("--only", nargs="*", default=None,
                    help="run only these scenarios (e.g. 1a 3b)")
    args = ap.parse_args()
    svc, docs = services()
    doc_id = fixture_doc(svc)
    ALLOWED.add(doc_id)
    print(f"fixture Doc: {gdocs.tab_url(doc_id)}")
    root = Path(tempfile.mkdtemp(prefix="authorlm-live-"))
    finished = False
    try:
        ws, db, manuscript, ms = build(root, svc, docs, doc_id)
        snapshot(svc, docs, doc_id, "after setup")
        env = (svc, docs, doc_id, db, manuscript, ms)
        for name, fn in (("1a", s1a), ("1b", s1b), ("1c", s1c), ("2", s2),
                         ("3a", s3a), ("3b", s3b), ("3c", s3c), ("4", s4),
                         ("5a", s5a), ("5b", s5b)):
            if args.only and name not in args.only:
                continue
            run(name, fn, env)
            print(("pass  " if RESULTS[-1][1] else "FAIL  ") + name,
                  flush=True)
        finished = True
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if args.record and not finished:
        print("not recorded: the run stopped before every scenario ran")
    elif args.record:
        # A scenario that fails is still a recording: the failures are
        # what the recordings exist to show (the fixes wait on the owner).
        by: dict[str, list] = {}
        for c in CALLS:
            by.setdefault(c["scenario"].split(":")[0], []).append(c)
        for old in CACHE.glob("*.json.gz"):
            if not args.only or old.name.split(".")[0] in by:
                old.unlink()
        for name, calls in by.items():
            with gzip.open(CACHE / f"{name}.json.gz", "wt",
                           encoding="utf-8") as out:
                json.dump(calls, out, indent=1, ensure_ascii=False)
        when = dt.datetime.now(dt.timezone.utc).astimezone().isoformat(
            timespec="seconds")
        SCENARIO[0] = "stamp"
        stamp(docs, doc_id, when)
        LOG.write_text(when + "\n")
        print(f"recorded {len(CALLS)} calls into {CACHE}; fake.log and the "
              f"fixture Doc's '{DOC_TAB}' tab say {when}")
    for name, ok, why in RESULTS:
        if not ok:
            print(f"--- {name}\n" + why.strip()[:1500])
    return 0 if all(ok for _, ok, _ in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
