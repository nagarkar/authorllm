"""The audiobook page's push after an edit (2026-11-12), hermetic.

The author edited a parable on the page while the same file's tab was
edited in the Doc; the page's Pull then said "the Doc matches the files
· changed on both sides, left alone: …", and Push said "did not push;
doc has changed. pull first". Now a saved edit queues a push of its file
behind the refresh: it goes up when the Doc side has not moved since the
last push or pull (unchanged, local_ahead) and is held back, named, with
a link to the Merge forms page, when it has (doc_ahead, conflict) or
when the tab has forms waiting. The save never waits on Google.

Every Google call is a fake: the Docs service answers only revisionId,
and doc_three_way / push_doc are replaced where the decision needs them.

Run: python3 tests/test_audiobook_autopush.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# test_audio pins the config, .env and client away from the real ones on
# import, and owns the audiobook fixture.
import test_audio as ta  # noqa: E402
from test_audio import _book_copy, _workspace, check  # noqa: E402

from authorlm import audio, audiobook, gdocs  # noqa: E402
from authorlm.normalize import normalize_markdown  # noqa: E402


class FakeDocs:
    """A Docs service that answers only the revision read."""

    def __init__(self, revision: str = "r1") -> None:
        self.revision = revision
        self.reads = 0

    def documents(self):
        return self

    def get(self, documentId: str, fields: str = "", **_kw):
        assert fields == "revisionId", f"only the revision is read, not {fields}"
        self.reads += 1
        return self

    def execute(self):
        return {"revisionId": self.revision}


def _hash(text: str) -> str:
    return hashlib.sha256(gdocs.strip_embed_lines(normalize_markdown(text))
                          .encode()).hexdigest()[:16]


def _link(s, name: str, **extra) -> None:
    """Map `name` to a tab, pushed at its current text."""
    text = (Path(s.manuscript["path"]) / name).read_text(encoding="utf-8")
    meta = gdocs._mapping(s.db, s.manuscript)
    links = meta.setdefault("gdocs", {})
    links["_master_id"] = "m1"
    links[name] = {"tab_id": "t1", "pushed_hash": _hash(text), **extra}
    gdocs._save_mapping(s.db, s.manuscript, meta)


def _plain(message: str) -> bool:
    return ("—" not in message and "–" not in message
            and "refus" not in message.lower())


def check_pull_summary() -> None:
    ps = audiobook.pull_summary
    check("a file left alone is never covered by 'the Doc matches'",
          ps([], ["a.md"], [], ["b.md"])
          == "the Doc matches a.md · changed on both sides, left alone: b.md "
             "(settle it on the Merge forms page)", ps([], ["a.md"], [], ["b.md"]))
    check("the incident's pull names only the file left alone",
          ps([], [], [], ["king-who-owned-the-rain.md"])
          == "changed on both sides, left alone: king-who-owned-the-rain.md "
             "(settle it on the Merge forms page)")
    check("pulled, matched and kept files are named apart",
          ps(["a.md"], ["b.md", "c.md"], ["d.md"], [])
          == "pulled a.md · the Doc matches b.md, c.md · the file is ahead of "
             "the Doc, kept: d.md (Push sends it)")
    check("a pull that touched nothing says so", ps([], [], [], []) == "nothing new in the Doc")
    for args in (([], ["a.md"], [], ["b.md"]), (["a.md"], [], ["d.md"], ["e.md"])):
        check("the pull line is plain words", _plain(ps(*args)), ps(*args))


def check_doc_side_state(s) -> None:
    name = "kindness.md"
    path = Path(s.manuscript["path"]) / name
    original = path.read_text(encoding="utf-8")
    real_three = gdocs.doc_three_way
    calls: list[str] = []
    try:
        docs = FakeDocs("r1")
        meta = gdocs._mapping(s.db, s.manuscript)
        meta.setdefault("gdocs", {}).pop(name, None)
        gdocs._save_mapping(s.db, s.manuscript, meta)
        side = gdocs.doc_side_state(s.db, s.manuscript, name, None, docs)
        check("a file with no tab is 'unlinked', with no call to Google",
              side["state"] == "unlinked" and docs.reads == 0, str(side))

        # The base text as the bridge keeps it: what the tab holds.
        base = gdocs.strip_embed_lines(normalize_markdown(original))
        _link(s, name)

        def three(state: str, doc: str):
            def fake(*_a, **_k):
                calls.append(state)
                return {"state": state, "base_hash": gdocs.text_hash(base),
                        "base": base, "doc": doc, "tab": doc, "local": "",
                        "revision": docs.revision}
            return fake

        gdocs.doc_three_way = three("unchanged", base)
        side = gdocs.doc_side_state(s.db, s.manuscript, name, None, docs)
        rec = gdocs.doc_status(s.db, s.manuscript)[name]
        check("no doc_rev yet: the tab is read, and the revision it holds the base at is kept",
              side == {"relpath": name, "state": "unchanged", "fast": False}
              and calls == ["unchanged"] and rec.get("doc_rev") == "r1", str((side, rec)))
        side = gdocs.doc_side_state(s.db, s.manuscript, name, None, docs)
        check("unchanged, the cheap way: the Doc still at doc_rev, no tab read",
              side["state"] == "unchanged" and side["fast"] and calls == ["unchanged"], str(side))
        path.write_text(original.replace("Kindness", "Kindliness", 1), encoding="utf-8")
        side = gdocs.doc_side_state(s.db, s.manuscript, name, None, docs)
        check("local_ahead, the cheap way: only the file moved",
              side["state"] == "local_ahead" and side["fast"] and calls == ["unchanged"], str(side))
        docs.revision = "r2"   # someone edited some tab of the Doc
        gdocs.doc_three_way = three("local_ahead", base)
        side = gdocs.doc_side_state(s.db, s.manuscript, name, None, docs)
        check("the Doc moved elsewhere: the tab is read and still holds the base (local_ahead)",
              side["state"] == "local_ahead" and not side["fast"]
              and gdocs.doc_status(s.db, s.manuscript)[name].get("doc_rev") == "r2", str(side))
        for state in ("doc_ahead", "conflict"):
            docs.revision = f"r-{state}"
            gdocs.doc_three_way = three(state, base + "\nA Doc edit.\n")
            side = gdocs.doc_side_state(s.db, s.manuscript, name, None, docs)
            check(f"{state}: read from the tab, and no doc_rev is kept for it",
                  side["state"] == state and not side["fast"]
                  and gdocs.doc_status(s.db, s.manuscript)[name].get("doc_rev") == "r2",
                  str(side))
    finally:
        gdocs.doc_three_way = real_three
        path.write_text(original, encoding="utf-8")


def check_autopush(s) -> None:
    """The decision in each state, through a real edit on the page."""
    name = "kindness.md"
    path = Path(s.manuscript["path"]) / name
    original = path.read_text(encoding="utf-8")
    real = (gdocs.push_doc, gdocs.doc_side_state, gdocs.get_service,
            gdocs.get_docs_service)
    pushed: list = []
    state = {"now": "local_ahead"}

    def fake_push(db, manuscript, query, **kw):
        pushed.append((query, kw.get("guard")))
        return {"relpath": query, "url": "https://docs.google.com/x?tab=t1", "held": []}

    def fake_side(db, manuscript, query, service, docs_service, **_k):
        assert (service, docs_service) == ("drive", "docs")
        return {"relpath": query, "state": state["now"], "fast": True}

    gdocs.push_doc, gdocs.doc_side_state = fake_push, fake_side
    gdocs.get_service = lambda *a, **k: "drive"
    gdocs.get_docs_service = lambda *a, **k: "docs"
    n = 0

    def edit_once() -> dict:
        nonlocal n
        n += 1
        sec = next(x for x in s.status("kindness")["chapter"]["sections"]
                   if x["kind"] == "paragraph")
        src = s.source("kindness", sec["id"])
        assert src["file"] == name, src
        return s.edit("kindness", sec["id"], src["markdown"] + f" Edit {n}.")

    try:
        # No tab yet: the save queues nothing for the Doc.
        meta = gdocs._mapping(s.db, s.manuscript)
        meta.setdefault("gdocs", {}).pop(name, None)
        gdocs._save_mapping(s.db, s.manuscript, meta)
        out = edit_once()
        check("an edit to a file with no tab queues no push",
              out["changed"] and out["job"]["kind"] == "refresh" and out["push"] is None, str(out))

        _link(s, name)
        state["now"] = "local_ahead"
        out = edit_once()
        job = out["push"]
        check("local_ahead: the edit is pushed, guarded, behind the refresh",
              out["job"]["kind"] == "refresh" and job["kind"] == "autopush"
              and job["state"] == "done" and pushed == [(name, True)]
              and job["message"] == f"pushed {name}" and not job.get("held"), str(job))
        check("the job carries its timings", isinstance(job.get("secs"), float)
              and isinstance(job.get("checkSecs"), float), str(job))
        check("the Doc line says the page pushed",
              s.status("kindness")["doc"]["state"] == "pushed from the page")

        pushed.clear()
        state["now"] = "unchanged"
        out = edit_once()
        check("unchanged: nothing is pushed, and it says so",
              pushed == [] and out["push"]["state"] == "done"
              and out["push"]["message"].startswith("nothing to push"), str(out["push"]))

        for st in ("doc_ahead", "conflict"):
            pushed.clear()
            state["now"] = st
            out = edit_once()
            job = out["push"]
            check(f"{st}: not pushed; the job says so and links Merge forms",
                  pushed == [] and job["state"] == "done" and job.get("held") is True
                  and job.get("link") == "#Scratch/forms"
                  and job["message"].startswith(f"did not push {name}")
                  and "Merge forms" in job["message"] and _plain(job["message"]), str(job))
            st_ = s.status("kindness")
            check(f"{st}: the chapter's status names the held file",
                  [h["file"] for h in st_.get("held", [])] == [name]
                  and st_.get("formsLink") == "#Scratch/forms", str(st_.get("held")))
            sec = next(x for x in st_["chapter"]["sections"] if x["kind"] == "paragraph")
            src = s.source("kindness", sec["id"])
            check(f"{st}: the edit box warns before the next edit, with no call to Google",
                  src.get("warning") == job["message"] and src.get("link") == "#Scratch/forms",
                  str(src))

        pushed.clear()
        state["now"] = "local_ahead"
        out = edit_once()
        check("once the Doc side is back, the push goes and the warning clears",
              pushed == [(name, True)] and not s.status("kindness").get("held")
              and "warning" not in s.source("kindness", next(
                  x for x in s.status("kindness")["chapter"]["sections"]
                  if x["kind"] == "paragraph")["id"]), str(out["push"]))

        # The Doc moved between the check and the push: the guard holds.
        def changed_push(db, manuscript, query, **kw):
            raise gdocs.DocChanged(f"{query}: {gdocs.DOC_CHANGED}")
        gdocs.push_doc = changed_push
        out = edit_once()
        check("a tab edited between the check and the push is held back, not failed",
              out["push"]["state"] == "done" and out["push"].get("held") is True, str(out["push"]))
        gdocs.push_doc = fake_push
        s.held.clear()

        # Pending forms: held back before any call to Google.
        pushed.clear()
        gdocs.get_service = gdocs.get_docs_service = _no_google
        row = {"id": "dt-autopush", "version": 1, "created_at": "2026-11-12T00:00:00Z",
               "created_by": "test", "schema_version": "1", "metadata": json.dumps({}),
               "manuscript_id": s.manuscript["id"], "origin_type": "critique",
               "origin_id": "cp-autopush", "file": name, "proposed_old": "a",
               "proposed_new": "b", "state": "written", "our_reply_ids": "[]"}
        s.db.insert("doc_threads", row)
        out = edit_once()
        job = out["push"]
        check("pending forms: not pushed; merge the forms first, with the link",
              pushed == [] and job["state"] == "done" and job.get("held") is True
              and job.get("link") == "#Scratch/forms"
              and "forms" in job["message"] and "Merge" in job["message"]
              and _plain(job["message"]), str(job))
        s.db.update("doc_threads", "dt-autopush", {"state": "applied"})
    finally:
        (gdocs.push_doc, gdocs.doc_side_state, gdocs.get_service,
         gdocs.get_docs_service) = real
        path.write_text(original, encoding="utf-8")
        s.held.clear()


def _no_google(*_a, **_k):
    raise AssertionError("pending forms are checked before any call to Google")


def check_pull_holds(s) -> None:
    """A Pull that leaves a file alone marks it held; one that brings it
    down clears it."""
    real = (gdocs.pull_doc, gdocs.get_service, gdocs.get_docs_service)
    gdocs.get_service = lambda *a, **k: "drive"
    gdocs.get_docs_service = lambda *a, **k: "docs"
    try:
        gdocs.pull_doc = lambda db, m, query=None, **k: {
            "changed": [], "unchanged": [], "conflicts": [query]}
        job = s.enqueue("pull", stem="kindness")
        check("a conflicted pull says the file was left alone, never that it matches",
              job["message"] == "changed on both sides, left alone: kindness.md "
                                "(settle it on the Merge forms page)"
              and job.get("link") == "#Scratch/forms"
              and [h["file"] for h in s.status("kindness")["held"]] == ["kindness.md"], str(job))
        gdocs.pull_doc = lambda db, m, query=None, **k: {
            "changed": [], "unchanged": [query], "conflicts": []}
        job = s.enqueue("pull", stem="kindness")
        check("a pull that finds the tab matching clears the warning",
              job["message"] == "the Doc matches kindness.md"
              and s.status("kindness")["held"] == [], str(job))
    finally:
        gdocs.pull_doc, gdocs.get_service, gdocs.get_docs_service = real


def check_save_never_waits(book: Path, ws: Path) -> None:
    """The real page's session (a worker thread): the save returns while
    the push job is still asking Google."""
    real = (gdocs.push_doc, gdocs.doc_side_state, gdocs.get_service,
            gdocs.get_docs_service)
    slow = 0.6

    def slow_side(db, manuscript, query, *a, **k):
        time.sleep(slow)
        return {"relpath": query, "state": "local_ahead", "fast": False}

    gdocs.doc_side_state = slow_side
    gdocs.push_doc = lambda db, m, q, **k: {"relpath": q, "url": "u", "held": []}
    gdocs.get_service = lambda *a, **k: "drive"
    gdocs.get_docs_service = lambda *a, **k: "docs"
    audiobook._SESSIONS.clear()
    try:
        s = audiobook.session(str(ws), "Scratch", inline=False)
        _link(s, "kindness.md")
        sec = next(x for x in s.status("kindness")["chapter"]["sections"]
                   if x["kind"] == "paragraph")
        src = s.source("kindness", sec["id"])
        t0 = time.monotonic()
        out = s.edit("kindness", sec["id"], src["markdown"] + " Quick.")
        took = time.monotonic() - t0
        check(f"the save returns before the Doc is asked ({took * 1000:.0f} ms, "
              f"Google faked at {slow * 1000:.0f} ms)",
              took < slow and out["push"]["state"] == "queued", str(out))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job = next(j for j in s.jobs()["recent"] + s.jobs()["queued"]
                       + [s.jobs()["running"] or {}] if j.get("id") == out["push"]["id"])
            if job.get("state") == "done":
                break
            time.sleep(0.05)
        check("the push job lands in the job strip after the refresh",
              job.get("state") == "done" and job["message"] == "pushed kindness.md"
              and job["secs"] >= slow, str(job))
        print(f"  timing: save {took * 1000:.1f} ms; push job {job['secs'] * 1000:.0f} ms "
              f"(with Google faked at {slow * 1000:.0f} ms)")
    finally:
        (gdocs.push_doc, gdocs.doc_side_state, gdocs.get_service,
         gdocs.get_docs_service) = real
        audiobook._SESSIONS.clear()


def check_doc_side_on_the_doc_road_fake() -> None:
    """doc_side_state against the Doc road's counting double (a revision
    that moves on every Doc edit, a log of every call): a pull records
    the revision, a local edit after it is decided with one revision
    read and no export, and the push that follows exports only for its
    read-back. A Doc edit sends the check to the tab."""
    import tempfile

    from test_doc_road_verbs import BASE, _CountingFake, _fixture

    root = Path(tempfile.mkdtemp(prefix="authorlm-autopush-road-"))
    try:
        _ws, db, manuscript, ms, fake = _fixture(root, "road",
                                                 fake_cls=_CountingFake)
        fake.edit("tab-2", "Gamma three.", "Gamma, from the Doc.")
        gdocs.pull_doc(db, manuscript, query="solo.md", service=fake,
                       docs_service=fake, with_comments=False)
        rec = gdocs.doc_status(db, manuscript)["solo.md"]
        check("a pull records the revision its tab was read at",
              rec.get("doc_rev") == fake.rev()
              and "Gamma, from the Doc." in (ms / "solo.md").read_text(), str(rec))
        (ms / "solo.md").write_text((ms / "solo.md").read_text().replace(
            "Beta two.", "Beta, mine."))
        fake.reset()
        side = gdocs.doc_side_state(db, manuscript, "solo.md", fake, fake)
        check("after the pull, a local edit is local_ahead with one revision read",
              side["state"] == "local_ahead" and side["fast"]
              and fake.count("rev") == 1 and fake.count("export") == 0
              and fake.count("get") == 0, str(fake.calls))
        fake.reset()
        gdocs.push_doc(db, manuscript, "solo.md", service=fake,
                       docs_service=fake, guard=True)
        check("the guarded push after it reads no export for its guard",
              "Beta, mine." in fake.tab_text("solo.md") and fake.count("export") == 0,
              str(fake.calls))
        # A rebuild push (no comments on the tab) leaves no doc_rev, so
        # the first check after it reads the tab once and records one.
        side = gdocs.doc_side_state(db, manuscript, "solo.md", fake, fake)
        check("right after the push: unchanged", side["state"] == "unchanged", str(side))
        fake.reset()
        side = gdocs.doc_side_state(db, manuscript, "solo.md", fake, fake)
        check("...and the next check is the cheap way: one revision read",
              side["state"] == "unchanged" and side["fast"]
              and fake.calls == [("rev", fake.rev())], str((side, fake.calls)))
        fake.edit("tab-2", "Alpha one.", "Alpha, typed in the Doc.")
        side = gdocs.doc_side_state(db, manuscript, "solo.md", fake, fake)
        check("a Doc edit with no local edit: doc_ahead, read from the tab",
              side["state"] == "doc_ahead" and not side["fast"], str(side))
        (ms / "solo.md").write_text((ms / "solo.md").read_text().replace(
            "Gamma, from the Doc.", "Gamma, mine again."))
        side = gdocs.doc_side_state(db, manuscript, "solo.md", fake, fake)
        check("a Doc edit and a local edit: conflict", side["state"] == "conflict", str(side))
        assert BASE  # the fixture's essay
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    ta._assert_offline()
    check_pull_summary()
    check_doc_side_on_the_doc_road_fake()
    book = _book_copy()
    ws, _out = _workspace(book)
    audiobook._SESSIONS.clear()
    try:
        s = audiobook.session(str(ws), "Scratch", inline=True)
        audio.export(s.db, s.manuscript, resolve_dictionary=False)
        check_doc_side_state(s)
        check_autopush(s)
        check_pull_holds(s)
        check_save_never_waits(book, ws)
    finally:
        audiobook._SESSIONS.clear()
        shutil.rmtree(ws, ignore_errors=True)
        shutil.rmtree(book, ignore_errors=True)
    print(f"\nAll {ta.PASSED} checks passed.")


if __name__ == "__main__":
    main()
