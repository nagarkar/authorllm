"""The critique pass (design §5–§7): preflight gate, context package,
output contract + echo validation, staging as critique threads, verdicts
with free undo, marked-text composition (pending-form grammar incl.
insertions), resolution matching (author post-edits win), rollback pin.
Drive is not touched: the pure functions the Doc verbs compose are what's
tested here.

Run: python3 tests/test_passes.py
"""

from __future__ import annotations

import os

# Offline suite: pin the project-config and .env lookups away from the
# real ones. Without this a checkout's config.toml (llm enabled, keys in
# .env) is picked up by every test process and the suite makes live,
# billed model calls — and asserts against whatever they return.
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
# And pin client provenance OFF. The suites run INSIDE a Claude Code
# Bash call, so CLAUDE_CODE_SESSION_ID is in their own environment and
# the claude-code adapter would stamp the developer's live chat into
# every fixture row — tests passing for the wrong reason. Same failure
# mode as a leaked config, so it gets the same treatment: pin it.
os.environ["AUTHORLM_CLIENT"] = "none"

def _assert_offline() -> None:
    """Fail loudly if the real project config or .env leaks into a test.

    Before config moved into the repo, a test workspace simply had no
    config.toml, so the LLM was off and no suite could make a billed
    call. Now a checkout always HAS an enabled config, and that safety
    came from nothing but the pins above — so it is asserted, not
    assumed."""
    import os as _os

    from authorlm import paths as _paths

    assert not _paths.config_path().exists(), (
        f"test isolation broken: reading the real config at "
        f"{_paths.config_path()}")
    assert _paths.load_env() == [], "test isolation broken: .env was loaded"
    leaked = [v for v in _paths_vendor_vars() if _os.environ.get(v)]
    assert not leaked, f"test isolation broken: vendor keys in env: {leaked}"


def _paths_vendor_vars() -> list:
    from authorlm.llm import VENDOR_KEY_ENV

    return sorted(VENDOR_KEY_ENV.values())



import contextlib
import http.server
import io
import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import api, critique, gdocs, passes, summaries as sums  # noqa: E402
from authorlm import threads as th  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.db import loads  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


class _RecordingDrive:
    """A Drive+Docs double that RECORDS every request body it is handed.

    The push guards are tested by their OUTCOME, not by which exception
    happens to surface: the question is not "did push_doc raise?" but
    "did any text containing `<<` ever reach the wire?". A `None` service
    answers that only by accident — the AttributeError it raises would
    still be raised by a push that had already formed and sent the body.

    Implements exactly enough of the two services for `push_doc`'s
    REBUILD path to run end to end: no open comments (so the surgical
    path is not taken), a temp-doc create that captures the uploaded
    markdown, a documents.get that returns an empty imported doc, and a
    batchUpdate that captures its requests."""

    def __init__(self, tab_id: str = "tab-1", title: str = "solo.md",
                 book: str = "book"):
        self.bodies: list[str] = []
        self.tab_id = tab_id
        self.title = title
        self.book = book

    # -- the recorded surface ------------------------------------------
    def _record(self, value) -> None:
        if isinstance(value, (str, bytes)):
            self.bodies.append(value.decode("utf-8", "replace")
                               if isinstance(value, bytes) else value)
        else:
            self.bodies.append(json.dumps(value, default=str))

    @property
    def sent(self) -> str:
        return "\n".join(self.bodies)

    def files(self):
        outer = self

        class _Req:
            def __init__(self, result):
                self._result = result

            def execute(self):
                return self._result

        class _Files:
            def create(self, body=None, media_body=None, fields=None):
                if media_body is not None:
                    outer._record(media_body.getbytes(0, media_body.size()))
                outer._record(body)
                return _Req({"id": "temp-doc"})

            def delete(self, fileId=None):
                return _Req({})

            def get(self, **kw):
                return _Req({})

            def list(self, **kw):
                return _Req({"files": []})
        return _Files()

    def comments(self):
        class _Req:
            def execute(self):
                return {"comments": []}

        class _Comments:
            def list(self, **kw):
                return _Req()
        return _Comments()

    def documents(self):
        outer = self

        class _Req:
            def __init__(self, result):
                self._result = result

            def execute(self):
                return self._result

        class _Documents:
            def get(self, documentId=None, includeTabsContent=None):
                def tab(tid, title):
                    return {"tabProperties": {"tabId": tid,
                                              "title": title},
                            "documentTab": {"body": {"content": []}},
                            "childTabs": []}
                return _Req({"tabs": [tab("tab-root", outer.book),
                                      tab(outer.tab_id, outer.title)],
                             "body": {"content": []}})

            def batchUpdate(self, documentId=None, body=None):
                outer._record(body)
                return _Req({})
        return _Documents()


class _SurgicalDocFake:
    """A Drive+Docs double whose tab bodies hold REAL text and whose
    `batchUpdate` actually APPLIES `insertText` / `deleteContentRange`.

    The other two doubles in this file answer different questions:
    `_RecordingDrive` proves what reached the wire and `_ResolveDocFake`
    proves what came back from an export. Neither can answer the
    question the surgical writer poses — *where in the tab did the form
    land, and what did the tab become as a result* — because neither
    mutates. A writer that locates the wrong span is invisible to a
    double that throws its requests away.

    The model is Docs': a tab body is a flat string whose first
    character is at doc index 1, paragraphs are its `\\n`-terminated
    segments, and the temp-doc import → transplant → tab pipeline runs
    end to end (so `push_doc`'s REBUILD path works against it).
    Style requests are accepted and ignored: colour and strikethrough
    are not what any assertion here turns on."""

    def __init__(self, tabs, book: str = "book"):
        import re as _re

        self._re = _re
        self.book = book
        self.master_id = "doc-fake"
        self.bodies: list[str] = []          # every batchUpdate, as JSON
        self.temps: dict[str, str] = {}
        self._next = [100]
        self.tabs = [{"id": f"tab-{i}", "title": t,
                      "body": self._as_body(x)}
                     for i, (t, x) in enumerate(tabs, 1)]

    # -- the text model ------------------------------------------------
    def _paras(self, markdown: str) -> list[str]:
        return [p.strip() for p in self._re.split(r"\n\s*\n", markdown)
                if p.strip()]

    def _as_body(self, markdown: str) -> str:
        return "".join(p + "\n" for p in self._paras(markdown))

    def _tab(self, tab_id: str) -> dict | None:
        return next((t for t in self.tabs if t["id"] == tab_id), None)

    def tab_text(self, title: str) -> str:
        """The tab's body exactly as it now stands — what the author
        would see, markers and all."""
        return next(t["body"] for t in self.tabs if t["title"] == title)

    def _content(self, body: str) -> list[dict]:
        out, index = [], 1
        for seg in body.splitlines(keepends=True):
            end = index + len(seg)
            out.append({
                "startIndex": index, "endIndex": end,
                "paragraph": {
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "elements": [{"startIndex": index, "endIndex": end,
                                  "textRun": {"content": seg,
                                              "textStyle": {}}}]}})
            index = end
        return out

    # -- Drive ---------------------------------------------------------
    def files(self):
        outer = self

        class _Req:
            def __init__(self, result):
                self._result = result

            def execute(self):
                return self._result

        class _Files:
            def create(self, body=None, media_body=None, fields=None):
                outer._next[0] += 1
                temp_id = f"temp-{outer._next[0]}"
                if media_body is not None:
                    outer.temps[temp_id] = media_body.getbytes(
                        0, media_body.size()).decode("utf-8")
                return _Req({"id": temp_id})

            def delete(self, fileId=None):
                return _Req({})

            def get(self, **kw):
                return _Req({})

            def list(self, **kw):
                return _Req({"files": []})

            def export(self, fileId=None, mimeType=None):
                whole = "\n".join(
                    f"# **{t['title']}**\n\n"
                    + "\n\n".join(outer._paras(t["body"]))
                    + ("\n" if t["body"].strip() else "")
                    for t in outer.tabs)
                return _Req(whole.encode("utf-8"))
        return _Files()

    def comments(self):
        class _Req:
            def execute(self):
                return {"comments": []}

        class _Comments:
            def list(self, **kw):
                return _Req()
        return _Comments()

    # -- Docs ----------------------------------------------------------
    def documents(self):
        outer = self

        class _Req:
            def __init__(self, result):
                self._result = result

            def execute(self):
                return self._result

        class _Documents:
            def get(self, documentId=None, includeTabsContent=None):
                if documentId in outer.temps:
                    return _Req({"body": {"content": outer._content(
                        outer._as_body(outer.temps[documentId]))}})
                tabs = [{"tabProperties": {"tabId": t["id"],
                                           "title": t["title"]},
                         "documentTab": {"body": {
                             "content": outer._content(t["body"])}},
                         "childTabs": []} for t in outer.tabs]
                return _Req({"tabs": tabs, "body": {"content": []}})

            def batchUpdate(self, documentId=None, body=None):
                outer.bodies.append(json.dumps(body, default=str))
                replies = []
                for req in (body or {}).get("requests", []):
                    replies.append(outer._apply(req))
                return _Req({"replies": replies})
        return _Documents()

    def _apply(self, req: dict) -> dict:
        if "insertText" in req:
            spec = req["insertText"]
            tab = self._tab(spec["location"].get("tabId"))
            if tab is not None:
                at = spec["location"]["index"] - 1
                tab["body"] = tab["body"][:at] + spec["text"] + \
                    tab["body"][at:]
            return {}
        if "deleteContentRange" in req:
            rng = req["deleteContentRange"]["range"]
            tab = self._tab(rng.get("tabId"))
            if tab is not None:
                tab["body"] = (tab["body"][:rng["startIndex"] - 1]
                               + tab["body"][rng["endIndex"] - 1:])
            return {}
        if "addDocumentTab" in req:
            self._next[0] += 1
            tab_id = f"tab-{self._next[0]}"
            title = req["addDocumentTab"].get(
                "tabProperties", {}).get("title", "")
            self.tabs.append({"id": tab_id, "title": title, "body": ""})
            return {"addDocumentTab": {"tabProperties": {"tabId": tab_id}}}
        if "deleteTab" in req:
            self.tabs = [t for t in self.tabs
                         if t["id"] != req["deleteTab"].get("tabId")]
            return {}
        return {}          # styles, bullets, tab moves: accepted, ignored


MARKED_ESSAY = (
    "# Solo\n\n"
    "A **bold** claim opens the essay.\n\n"
    "Original paragraph text.\n\n"
    "![](_illustrations/solo-1.png)\n\n"
    "- a list item\n")


def _local_transport_guards(root: Path) -> None:
    """AQ step 4 / 4b — the local settle transport's canonicalization and
    its two push guards, plus the pull-path guard.

    These are the safety-critical part of the filter pass and they are
    tested at the seam rather than through the filter verbs, because the
    change lands BEFORE those verbs exist and must be reviewable on its
    own. Everything here is a `doc_threads` row with
    `origin_type='filter'` and a marked file on disk — no filter run, no
    network, no model call."""
    import hashlib as _hashlib
    import json as _json

    from authorlm import revisions as _rev
    from authorlm import staging as _staging
    from authorlm.db import ko_fields as _ko

    print("AQ/4: pending forms on disk — canonicalization and the guards:")

    ws = root / "marked-ws"
    ms = ws / "book"
    ms.mkdir(parents=True)
    (ms / "solo.md").write_text(MARKED_ESSAY)
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms)])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    mid = manuscript["id"]
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, {})            # v1: the pristine essay
    pristine_observed = _rev.read_manuscript_files(ms)["solo.md"]
    versions_before = db.one(
        "SELECT COUNT(*) AS n FROM manuscript_versions WHERE "
        "manuscript_id = ?", (mid,))["n"]

    # A mapping, so push_doc gets past the no-tab refusal and reaches
    # the guards rather than failing for an unrelated reason.
    normalized = gdocs.normalize_markdown(MARKED_ESSAY)
    meta = gdocs._mapping(db, manuscript)
    links = meta.setdefault("gdocs", {})
    links["_master_id"] = "doc-fake"
    links["solo.md"] = {
        "tab_id": "tab-1", "checked_out": False,
        "pushed_hash": _hashlib.sha256(
            normalized.encode()).hexdigest()[:16]}
    gdocs._save_mapping(db, manuscript, meta)

    # Mark the file, exactly as `filter resolve --pause` will: the pure
    # composer's output written to disk.
    marked = MARKED_ESSAY.replace(
        "Original paragraph text.",
        th.render_pending("Original paragraph text.",
                          "Rewritten paragraph text."))
    (ms / "solo.md").write_text(marked)
    thread = _ko("dt")
    thread.update(
        manuscript_id=mid, origin_type="filter",
        origin_id="fr-test:solo.md:1", file="solo.md", anchor_quote=None,
        proposed_old="Original paragraph text.",
        proposed_new="Rewritten paragraph text.",
        note="test", state="written", our_reply_ids="[]",
        last_author_reply_id=None, scope_kind="file", scope_ref="solo.md",
        metadata=_json.dumps({"kind": "replace", "anchor_paragraph": 2}))
    db.insert("doc_threads", thread)

    check("the bytes on disk really do carry the pending form — the "
          "guarded thing has to actually happen (§14.8)",
          "<<Original paragraph text.>>{{Rewritten paragraph text.}}"
          in (ms / "solo.md").read_text(), (ms / "solo.md").read_text())

    # --- F29: the canonicalization property ---------------------------
    observed = _rev.read_manuscript_files(ms)["solo.md"]
    check("while marked, read_manuscript_files returns the ORIGINAL text "
          "byte for byte — the canonical text of a pending form is its "
          "OLD half, and every observer goes through this seam",
          observed == pristine_observed,
          repr(observed[:200]))
    check("...and it still strips illustration embed lines while doing "
          "it — the pre-existing canonicalization at this seam is not "
          "displaced by the new one (F31b)",
          "_illustrations/solo-1.png" not in observed
          and "![](" not in observed, repr(observed))

    # --- F30: the version history never sees a marker -----------------
    with contextlib.redirect_stdout(io.StringIO()):
        report = api.collect(db, manuscript, {})
    versions_after = db.one(
        "SELECT COUNT(*) AS n FROM manuscript_versions WHERE "
        "manuscript_id = ?", (mid,))["n"]
    check("while marked, collect records NOTHING — the marked file is "
          "byte-identical to the essay through the seam, so there is no "
          "change to snapshot and no marker can enter version history",
          report.get("unchanged") is True
          and versions_after == versions_before,
          str({"report": report, "before": versions_before,
               "after": versions_after}))
    check("and no transition anywhere in the database mentions a marker",
          not db.all("SELECT id FROM editorial_transitions WHERE "
                     "manuscript_id = ? AND detail LIKE '%<<%'", (mid,)))

    # --- F31 (a): the DB-level guard, naming the right remedy ---------
    raised_a = None
    try:
        gdocs.push_doc(db, manuscript, "solo.md", service=None,
                       docs_service=None)
    except LookupError as err:
        raised_a = str(err)
    check("doc push refuses a file with FILTER pending forms and names "
          "that producer's own settle verb — `critique_forms_pending` "
          "filtered on origin_type='critique', so a filter's forms "
          "pushed their markers straight into the Doc, where the next "
          "pull would silently discard the resolve (guard a)",
          raised_a is not None and "filter pending forms" in raised_a
          and "filter resolve solo.md" in raised_a
          and "critique resolve" not in raised_a, raised_a)
    check("and `forms_pending` reports the ORIGIN, not a bool — that is "
          "what lets one message name the right remedy for either "
          "producer",
          gdocs.forms_pending(db, mid, "solo.md") == "filter",
          repr(gdocs.forms_pending(db, mid, "solo.md")))

    # --- F31 (b): the BYTE-level guard, with the rows gone from under it
    # Asserting only (a) would leave this guard free to delete with the
    # suite green (§15.8 note 3), and it is the one that survives a
    # database that has lost the run row.
    db.conn.execute("DELETE FROM doc_threads WHERE id = ?", (thread["id"],))
    db.conn.commit()
    check("the database now knows nothing about the marked file — so "
          "only a byte check can save it",
          gdocs.forms_pending(db, mid, "solo.md") is None)
    check("staging.is_marked reads the BYTES, so it still says yes",
          _staging.is_marked((ms / "solo.md").read_text()))
    raised_b = None
    try:
        gdocs.push_doc(db, manuscript, "solo.md", service=None,
                       docs_service=None)
    except LookupError as err:
        raised_b = str(err)
    check("doc push STILL refuses on the byte check alone, and names "
          "'filter unmark' — the recovery for a state that is fully "
          "described by the bytes (guard b)",
          raised_b is not None and "mid-settle" in raised_b
          and "filter unmark solo.md" in raised_b, raised_b)
    raised_c = None
    try:
        gdocs.diff_push(db, manuscript, "solo.md", None, None)
    except LookupError as err:
        raised_c = str(err)
    check("...and so does the SURGICAL push path, which reads the file "
          "directly too — one edit to _refuse_mid_rewrite covers both "
          "because both already call it",
          raised_c is not None and "mid-settle" in raised_c, raised_c)

    # The OUTCOME, not the exception. The question these guards answer is
    # not "did push_doc raise?" — a `None` service raises either way —
    # but "did any text carrying `<<` ever reach the wire?". A recording
    # double answers it directly, and the same double with both guards
    # disabled proves the assertion is not vacuous.
    _orig_guards = (gdocs.forms_pending, gdocs._refuse_mid_rewrite)
    drive_open = _RecordingDrive(tab_id="tab-1")
    try:
        gdocs.forms_pending = lambda *a, **k: None
        gdocs._refuse_mid_rewrite = lambda *a, **k: None
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                gdocs.push_doc(db, manuscript, "solo.md", service=drive_open,
                               docs_service=drive_open)
            except Exception:                             # noqa: BLE001
                pass
    finally:
        gdocs.forms_pending, gdocs._refuse_mid_rewrite = _orig_guards
    check("with BOTH guards disabled the marked text really does reach "
          "the wire — the recording double sees `<<`, so the assertion "
          "below is about the guards and not about a push that never "
          "happens",
          "<<" in drive_open.sent, drive_open.sent[:300])
    drive_guarded = _RecordingDrive(tab_id="tab-1")
    try:
        gdocs.push_doc(db, manuscript, "solo.md", service=drive_guarded,
                       docs_service=drive_guarded)
    except LookupError:
        pass
    check("with the guards in place NO request body contains a marker — "
          "in fact no request body is formed at all",
          not any("<<" in b for b in drive_guarded.bodies)
          and drive_guarded.bodies == [], drive_guarded.sent[:300])

    # --- the pull path must not discard a resolve ----------------------
    pulled = []

    class _PullFake:
        """Enough of Drive+Docs for pull_doc to reach the write. Its
        export carries the essay WITHOUT the marks, which is exactly the
        shape that would overwrite the resolve."""

        def files(self):
            class _Files:
                def export(self, fileId=None, mimeType=None):
                    class _Req:
                        def execute(self):
                            return (
                                "# **solo.md**\n\n"
                                "# Solo\n\nA **bold** claim opens the "
                                "essay.\n\nA sentence the Doc has and "
                                "local does not.\n\n- a list item\n"
                            ).encode("utf-8")
                    return _Req()
            return _Files()

        def documents(self):
            class _Documents:
                def get(self, documentId=None, includeTabsContent=None):
                    class _Req:
                        def execute(self):
                            return {"tabs": [
                                {"tabProperties": {"tabId": "tab-1",
                                                   "title": "solo.md"},
                                 "childTabs": []}]}
                    return _Req()
            return _Documents()

    before_bytes = (ms / "solo.md").read_bytes()
    fake = _PullFake()
    report = gdocs.pull_doc(db, manuscript, "solo.md", service=fake,
                            force=True, with_comments=False,
                            docs_service=fake)
    pulled.append(report)
    check("doc pull leaves a MID-SETTLE file untouched, byte for byte, "
          "even under --force — pulling over staged forms discards the "
          "author's post-edits with nothing said",
          (ms / "solo.md").read_bytes() == before_bytes,
          (ms / "solo.md").read_text()[:200])
    check("...and it says so by name rather than skipping in silence",
          report.get("marked") == ["solo.md"], str(report))
    check("the guard is not just 'pull never writes': the same fake "
          "DOES overwrite the file once the marks are gone",
          _pull_writes_when_unmarked(db, manuscript, ms, fake))

    _braces_are_the_authors(root)


TWIN_ESSAY = (
    "Alpha opens the essay and says a thing.\n\n"
    "And so the wall stands, and the Dead do not pass.\n\n"
    "Gamma follows, saying something else entirely.\n\n"
    "And so the wall stands, and the Dead do not pass.\n\n"
    "Omega closes the essay.\n")

TWIN = "And so the wall stands, and the Dead do not pass."


def _twin_fixture(root: Path, subdir: str, essay: str = TWIN_ESSAY):
    """A workspace whose one essay is mapped to a tab of a live-text
    Doc fake, positioned exactly where `critique write` finds it."""
    ws = root / subdir
    ms = ws / "book"
    ms.mkdir(parents=True)
    (ms / "solo.md").write_text(essay)
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms)])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, {})
    fake = _SurgicalDocFake([("book", ""), ("solo.md", essay)])
    meta = gdocs._mapping(db, manuscript)
    links = meta.setdefault("gdocs", {})
    links["_master_id"] = "doc-fake"
    links["_container_tab"] = "tab-1"
    links["solo.md"] = {"tab_id": "tab-2", "checked_out": False,
                        "pushed_hash": None}
    gdocs._save_mapping(db, manuscript, meta)
    return db, manuscript, ms, fake


def _twin_threads(db, mid: str, anchors, origin_type: str = "critique"):
    from authorlm.db import ko_fields as _ko

    made = []
    for anchor, new in anchors:
        row = _ko("dt")
        row.update(
            manuscript_id=mid, origin_type=origin_type,
            origin_id=f"twin:{anchor}", file="solo.md", anchor_quote=None,
            proposed_old=TWIN, proposed_new=new, note="test",
            state="accepted", our_reply_ids="[]",
            last_author_reply_id=None, scope_kind="file",
            scope_ref="solo.md",
            metadata=json.dumps({"kind": "replace",
                                 "anchor_paragraph": anchor,
                                 "intent_id": None, "original_new": new,
                                 "unit": anchor}))
        db.insert("doc_threads", row)
        made.append(row)
    return made


def _identical_old_halves(root: Path) -> None:
    """F-D20 / F-D21 — the surgical writer locates by OCCURRENCE INDEX.

    `_locate_in_tab` returned the FIRST verbatim match of its needle. Two
    staged edits whose `proposed_old` halves are byte-identical (two
    identical paragraphs in one essay: a refrain, a liturgical
    repetition) therefore both resolved to the SAME span, and because
    the write order is descending the second write landed INSIDE the
    wrapper the first had just planted — `<<<<old>>{{new2}}>>{{new4}}`.
    At settle `threads.PENDING` is non-greedy, so it matched
    `<<<<old>>{{new2}}` with `old = "<<old"`, matched no thread,
    collapsed to that old half, and left the literal string
    `<<old>>{{new4}}` in the finished manuscript.

    That is text corruption from a rare-but-real input, it is live in
    the critique pass today, and it is what the filter's push-all ruling
    (design-filter-doc-settle §9.3) makes reachable a second time. Both
    producers are fixed by the one edit, so it is proved here on the
    producer that already ships."""
    print("§9.3: identical old halves — occurrence-indexed location:")

    db, manuscript, ms, fake = _twin_fixture(root, "twin-ws")
    mid = manuscript["id"]
    threads = _twin_threads(db, mid, [(2, "NEW AT TWO."), (4, "NEW AT FOUR.")])

    # --- F-D21: the default is byte-for-byte the old behaviour --------
    span_first = gdocs._locate_in_tab(fake, "doc-fake", "tab-2", TWIN)
    span_zero = gdocs._locate_in_tab(fake, "doc-fake", "tab-2", TWIN,
                                     occurrence=0)
    span_second = gdocs._locate_in_tab(fake, "doc-fake", "tab-2", TWIN,
                                       occurrence=1)
    body = fake.tab_text("solo.md")
    check("F-D21 the default locates the FIRST occurrence — the "
          "hand-quoted span `propose_change` means, unchanged",
          span_first == span_zero and span_first[0] - 1 == body.find(TWIN),
          str({"default": span_first, "explicit": span_zero,
               "find": body.find(TWIN)}))
    check("F-D21 occurrence=1 locates the SECOND, and they are different "
          "spans — the parameter does something",
          span_second is not None and span_second[0] > span_first[0]
          and span_second[0] - 1 == body.find(TWIN, span_first[0]),
          str({"first": span_first, "second": span_second}))
    check("F-D21 an occurrence the tab does not have is None, never the "
          "last one it does have",
          gdocs._locate_in_tab(fake, "doc-fake", "tab-2", TWIN,
                               occurrence=2) is None)

    # --- F-D20: the writer places each form at its OWN paragraph ------
    result = gdocs.write_pending_forms(db, manuscript, "solo.md", threads,
                                       fake, fake)
    tab = fake.tab_text("solo.md")
    check("F-D20 both threads are written — neither is lost to a "
          "read-back failure caused by the other",
          len(result["written"]) == 2 and not result["failed"],
          str({"written": len(result["written"]),
               "failed": [(t["id"][:8], why)
                          for t, why in result["failed"]]}))
    check("F-D20 the tab carries NO nested wrapper — `<<<<` is the "
          "signature of two forms landing on one span",
          "<<<<" not in tab, tab)
    check("F-D20 each form sits at its own paragraph, in the essay's own "
          "order: the first twin took NEW AT TWO, the second NEW AT FOUR",
          tab.index("{{NEW AT TWO.}}") < tab.index("Gamma follows")
          < tab.index("{{NEW AT FOUR.}}"), tab)

    for t in result["written"]:
        db.update("doc_threads", t["id"], {"state": "written"})
    fetched = gdocs.tab_marked_markdown(db, manuscript, "solo.md",
                                          fake, fake)
    written = passes.staged_threads(db, mid, "solo.md", states=("written",))
    final, forms = passes.final_text_from_marked(fetched["marked"],
                                                 written=written)
    check("F-D20 the resolved essay carries NO residual marker — the "
          "corruption this fixes is a literal `<<old>>{{new}}` left in "
          "the author's manuscript",
          "<<" not in final and "{{" not in final and ">>" not in final,
          final)
    check("F-D20 ...and both new halves landed, each in its own place",
          final.index("NEW AT TWO.") < final.index("Gamma follows")
          < final.index("NEW AT FOUR."), final)


DOC_ESSAY = "\n\n".join([
    "# The Wall",
    "Alpha opens the essay and says a thing worth saying twice.",
    "And so the wall stands, and the Dead do not pass.",
    "Gamma follows, saying something else entirely.",
    "And so the wall stands, and the Dead do not pass.",
    "Omega closes the essay on a falling cadence.",
]) + "\n"

DOC_FILTER = (
    '---\nclass = "sequential"\n'
    'state = "a ledger of every word already flagged as repeated"\n---\n\n'
    "# Duplicate words and phrases\n\n"
    "Flag a word or phrase used again too soon, and propose the wording "
    "that removes the repetition.\n")


def _filter_reply(units, replaces: dict) -> str:
    entries = []
    for n in range(1, len(units) + 1):
        echo = passes.echo_of(units[n - 1])
        if n in replaces:
            entries.append({"n": n, "echo": echo, "action": "replace",
                            "new": replaces[n], "why": "the test's reason"})
        else:
            entries.append({"n": n, "echo": echo, "action": "keep"})
    return json.dumps({"units": entries, "state": "ledger: (empty)"})


def _doc_run(root: Path, subdir: str, replaces: dict, essay: str = DOC_ESSAY,
             accept: bool = True):
    """A real filter run over a real essay, triaged, with the essay's tab
    mapped to a live-text Doc fake. Every verb here is the shipped one —
    no LLM, no network."""
    ws = root / subdir
    ms = ws / "book"
    ms.mkdir(parents=True)
    (ms / "solo.md").write_text(essay)
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms)])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, {})
    api.filter_add(manuscript, "duplicate-words", DOC_FILTER)
    api.filter_run(db, manuscript, {}, "duplicate-words", "solo.md")
    units = passes.paragraphs_of(essay)
    api.filter_record(db, manuscript, {}, "solo.md",
                      _filter_reply(units, replaces))
    if accept:
        staged = api.filter_edits(db, manuscript, "solo.md")
        api.filter_triage(db, manuscript, "solo.md",
                          [{"item": str(i["n"]), "verdict": "accept"}
                           for i in staged["items"]])
    fake = _SurgicalDocFake([("book", ""), ("solo.md", essay)])
    meta = gdocs._mapping(db, manuscript)
    links = meta.setdefault("gdocs", {})
    links["_master_id"] = "doc-fake"
    links["_container_tab"] = "tab-1"
    links["solo.md"] = {"tab_id": "tab-2", "checked_out": False,
                        "pushed_hash": None}
    gdocs._save_mapping(db, manuscript, meta)
    return db, manuscript, ms, fake


def _run_row(db, mid: str):
    return dict(db.one("SELECT * FROM filter_runs WHERE manuscript_id = ? "
                       "ORDER BY created_at DESC LIMIT 1", (mid,)))


def _the_transport_is_frozen(root: Path) -> None:
    """§2.1 / D-3 — the run's TRANSPORT, absent until a transport verb
    takes one, frozen for the life of the run once taken."""
    print("§2.1: the run's transport, frozen at the verb that takes it:")

    db, manuscript, ms, _fake = _doc_run(root, "mode-ws", {2: "TWO REDONE."})
    mid = manuscript["id"]
    check("a triaged run has NO transport yet — absent is meaningful, and "
          "a default would lie about a run that has staged and triaged "
          "edits and not yet decided where to read them",
          api._run_mode(_run_row(db, mid)) is None,
          str(_run_row(db, mid).get("metadata")))
    check("...and `filter status` says so in the author's words rather "
          "than printing a null",
          api.filter_status(db, manuscript)["runs"][0]["mode"] is None)

    with contextlib.redirect_stdout(io.StringIO()):
        api.filter_resolve(db, manuscript, {}, "solo.md", pause=True)
    check("`filter resolve --pause` TAKES the local road, and the run "
          "records it — the transport is frozen by the verb that chooses "
          "it, not at run start (D-3)",
          api._run_mode(_run_row(db, mid)) == "local",
          str(_run_row(db, mid).get("metadata")))
    status = api.filter_status(db, manuscript)["runs"][0]
    check("...and status reports the mode AND how many forms are out",
          status["mode"] == "local" and status["forms_out"] == 1,
          str(status))

    # The other direction: a run already on the DOC road refuses --pause.
    db2, ms2, msdir2, _f2 = _doc_run(root, "mode-ws-2", {2: "TWO REDONE."})
    mid2 = ms2["id"]
    run2 = _run_row(db2, mid2)
    api._freeze_run_mode(db2, run2, "doc")
    before = (msdir2 / "solo.md").read_bytes()
    raised = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            api.filter_resolve(db2, ms2, {}, "solo.md", pause=True)
    except ValueError as err:
        raised = str(err)
    check("F-D1 a run on the DOC road refuses `filter resolve --pause`, "
          "naming the road it is already on and both ways off it — one "
          "run's forms in two places is the state the freeze exists to "
          "forbid",
          raised is not None and "put its forms in the Doc" in raised
          and "filter resolve solo.md" in raised
          and "filter unmark solo.md --force" in raised, raised)
    check("F-D1 ...and NOTHING moved: the file is byte-identical and no "
          "thread left 'accepted'",
          (msdir2 / "solo.md").read_bytes() == before
          and db2.one("SELECT COUNT(*) AS n FROM doc_threads WHERE "
                      "manuscript_id = ? AND state = 'written'",
                      (mid2,))["n"] == 0,
          str({"bytes": (msdir2 / "solo.md").read_bytes() == before}))


CONTAINING_ESSAY = "\n\n".join([
    "# The Wall",
    "And so the wall stands, and the Dead do not pass. Or so I once "
    "believed.",
    "And so the wall stands, and the Dead do not pass.",
    "Gamma follows, saying something else entirely.",
    "And so the wall stands, and the Dead do not pass.",
    "Omega closes the essay.",
]) + "\n"


def _a_paragraph_that_contains_another(root: Path) -> None:
    """P1 — `_occurrence` must count in the SAME universe `_locate_in_tab`
    searches: SUBSTRING occurrences in the joined tab text, not paragraphs
    equal to the needle.

    The two universes agree only while no paragraph strictly CONTAINS
    another paragraph's whole text. A refrain that also opens a longer
    paragraph breaks that, and this manuscript is full of refrains. When
    they diverge the writer plants each form in the wrong paragraph,
    the read-back proof passes (the form IS present, just not where it
    belongs), the resolve resolves both threads `cleaned`, and the
    author's manuscript is silently corrupted.

    `main` fails LOUDLY on this same input — first-match-always nests the
    second write and the read-back catches it. Trading a loud failure for
    a silent wrong write is a strictly worse bug than the one AV-1 set
    out to fix, so it gets its own probe."""
    print("P1: a paragraph that CONTAINS another paragraph's text:")

    db, manuscript, ms, fake = _doc_run(
        root, "contains-ws", {3: "THREE.", 5: "FIVE."},
        essay=CONTAINING_ESSAY)
    mid = manuscript["id"]
    paragraphs = passes.paragraphs_of(CONTAINING_ESSAY)
    twin = paragraphs[2]
    check("the fixture really is the hazard: paragraph 2 CONTAINS the "
          "twin text without being equal to it (§14.8 — the guarded "
          "thing has to actually happen)",
          twin in paragraphs[1] and paragraphs[1] != twin
          and paragraphs[2] == paragraphs[4] == twin, str(paragraphs[1]))
    check("P1 the two universes disagree on this input, which is the "
          "whole bug: paragraph-EQUALITY counts 1 before unit 5, "
          "SUBSTRING counts 2",
          sum(1 for p in paragraphs[:4] if p == twin) == 1
          and "\n".join(paragraphs[:4]).count(twin) == 2)

    result = api.filter_push(db, manuscript, {}, "solo.md",
                             services=lambda: (fake, fake))
    tab = fake.tab_text("solo.md")
    check("P1 the containing paragraph is NOT touched — its form belongs "
          "to no thread, and planting one there rewrites a paragraph the "
          "author never had a proposal on",
          "<<" not in tab.split("\n")[1], repr(tab.split("\n")[1]))
    check("P1 each form sits at its OWN unit: THREE at the first bare "
          "twin, FIVE at the second",
          tab.index("{{THREE.}}") < tab.index("Gamma follows")
          < tab.index("{{FIVE.}}"), tab)
    check("P1 ...and both threads landed, so the fix is not a loud "
          "failure standing in for a correct write",
          result["written"] == 2 and not result["failed"],
          str([(t["id"][:8], w) for t, w in result["failed"]]))

    settle = api.filter_resolve(db, manuscript, {}, "solo.md",
                               services=lambda: (fake, fake))
    final = (ms / "solo.md").read_text()
    check("P1 the resolved manuscript is correct: the containing "
          "paragraph survives whole, and neither twin's replacement "
          "landed inside it",
          "And so the wall stands, and the Dead do not pass. Or so I "
          "once believed." in final
          and "THREE. Or so I once believed." not in final, final)
    check("P1 ...both replacements landed, each at its own unit, and no "
          "bare twin is left over",
          final.index("THREE.") < final.index("Gamma follows")
          < final.index("FIVE.")
          and settle["forms"] == 2, final)

    # --- the discrimination half of F-D20, never written until now ----
    # Forcing the occurrence to 0 must reproduce the ORIGINAL defect. An
    # assertion that the parameter is correct proves nothing unless
    # removing it breaks something.
    db2, ms2, msdir2, fake2 = _doc_run(
        root, "occ-zero-ws", {3: "THREE.", 5: "FIVE."})
    original = gdocs._occurrence
    gdocs._occurrence = lambda paragraphs, n, needle: 0
    try:
        forced = api.filter_push(db2, ms2, {}, "solo.md",
                                 services=lambda: (fake2, fake2))
    finally:
        gdocs._occurrence = original
    tab2 = fake2.tab_text("solo.md")
    check("F-D20 (discrimination) with the occurrence forced to 0 the "
          "original defect comes straight back — both writes land on one "
          "span, nesting as `<<<<`, and a thread fails the read-back. "
          "The parameter is load-bearing, not decoration",
          "<<<<" in tab2 and forced["written"] == 1
          and len(forced["failed"]) == 1, tab2)


EMBED_SKEW_ESSAY = "\n\n".join([
    "# The Wall",
    "Alpha",
    "[Illustration: pic 0]",
    "![](_illustrations/p0.png)",
    "Alpha",
    "[Illustration: pic 1]",
    "![](_illustrations/p1.png)",
    "Alpha",
    "[Illustration: pic 2]",
    "![](_illustrations/p2.png)",
    "Alpha",
    "Beta",
    "Alpha",
]) + "\n"


def _embed_blank_lines_skew_occurrence(root: Path) -> None:
    """Blank-line embed paragraphs shift raw-disk unit indices vs the
    embed-stripped universe staging and the leveled Doc share.

    `write_pending_forms` used to count `_occurrence` on raw disk. A
    later identical refrain then undercounted — the form landed on an
    earlier twin, read-back still passed, resolve silently edited the
    wrong span. The hazard is real whenever an embed sits on its own
    blank-line-bounded paragraph (author edit, orphan line)."""
    from authorlm.revisions import strip_embed_lines

    print("P-embed: blank-line embeds must not skew occurrence:")

    stripped = strip_embed_lines(EMBED_SKEW_ESSAY)
    sp = passes.paragraphs_of(stripped)
    rp = passes.paragraphs_of(EMBED_SKEW_ESSAY)
    last_n = next(i for i in range(len(sp), 0, -1) if sp[i - 1] == "Alpha")
    so = gdocs._occurrence(sp, last_n, "Alpha")
    ro = gdocs._occurrence(rp, last_n, "Alpha")
    check("the fixture really is the hazard: stripped and raw disagree "
          "on the last Alpha's occurrence (§14.8)",
          so != ro and so > ro, f"stripped_occ={so} raw_occ={ro} n={last_n}")
    check("...and raw unit n is NOT the target Alpha (it is an embed "
          "or earlier content) — that is the index skew",
          rp[last_n - 1] != "Alpha", repr(rp[last_n - 1]))

    ws = root / "embed-skew-ws"
    ms = ws / "book"
    ms.mkdir(parents=True)
    (ms / "solo.md").write_text(EMBED_SKEW_ESSAY)
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms)])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, {})
    api.filter_add(manuscript, "duplicate-words", DOC_FILTER)
    api.filter_run(db, manuscript, {}, "duplicate-words", "solo.md")
    # Stage against STRIPPED units — the universe filter_record uses.
    api.filter_record(db, manuscript, {}, "solo.md",
                      _filter_reply(sp, {last_n: "ALPHA-LAST."}))
    staged = api.filter_edits(db, manuscript, "solo.md")
    api.filter_triage(db, manuscript, "solo.md",
                      [{"item": str(i["n"]), "verdict": "accept"}
                       for i in staged["items"]])
    fake = _SurgicalDocFake([("book", ""), ("solo.md", EMBED_SKEW_ESSAY)])
    meta = gdocs._mapping(db, manuscript)
    links = meta.setdefault("gdocs", {})
    links["_master_id"] = "doc-fake"
    links["_container_tab"] = "tab-1"
    links["solo.md"] = {"tab_id": "tab-2", "checked_out": False,
                        "pushed_hash": None}
    gdocs._save_mapping(db, manuscript, meta)

    result = api.filter_push(db, manuscript, {}, "solo.md",
                             services=lambda: (fake, fake))
    tab = fake.tab_text("solo.md")
    check("P-embed the push landed the one form",
          result["written"] == 1 and not result["failed"],
          str({"written": result["written"], "failed": result["failed"]}))
    # The last Alpha is after Beta in the leveled tab.
    check("P-embed the form sits on the LAST Alpha (after Beta), not an "
          "earlier twin — that is the silent wrong plant this fixes",
          tab.index("Beta") < tab.index("{{ALPHA-LAST.}}")
          and tab.count("<<Alpha>>") == 1, tab)

    settle = api.filter_resolve(db, manuscript, {}, "solo.md",
                               services=lambda: (fake, fake))
    final = (ms / "solo.md").read_text()
    check("P-embed resolve edits only the last Alpha; earlier Alphas and "
          "Beta survive; illustration tags stay",
          final.index("Beta") < final.index("ALPHA-LAST.")
          and final.count("\nAlpha\n") == 4  # four earlier Alphas unchanged
          and "[Illustration: pic 0]" in final
          and settle["forms"] == 1, final)
    check("P-embed ...and blank-line orphan embeds are still on disk "
          "(restore_orphan_embeds), not dropped by the strip/compose",
          "![](_illustrations/p0.png)" in final
          and "![](_illustrations/p2.png)" in final, final)


def _the_doc_is_the_review(root: Path) -> None:
    """The Sponsor-intent correction (2026-08-30): `filter push` takes
    UNTRIAGED proposals to the Doc, because the tab is the review.

    The Sponsor ran the live flow and met *"nothing is accepted — 6
    proposal(s) are still awaiting your verdict"* on a freshly recorded
    run. Gating the Doc road on a prior CLI verdict makes the author
    rule on every edit in the shell before they can look at any of them
    in the Doc — reviewing twice, which is the opposite of the ruling
    this road was built to serve."""
    print("Sponsor intent: the Doc settle IS the review:")

    # --- the exact live scenario: recorded, NOT triaged, pushed -------
    db, manuscript, ms, fake = _doc_run(
        root, "all-proposed-ws",
        {2: "TWO REDONE.", 4: "FOUR REDONE.", 6: "SIX REDONE."},
        accept=False)
    mid = manuscript["id"]
    states = [t["state"] for t in api._run_threads(db, mid,
                                                   _run_row(db, mid))]
    check("the fixture is the Sponsor's: every proposal is untriaged, "
          "exactly as `filter record` leaves them (§14.8)",
          states == ["proposed"] * 3, str(states))

    result = api.filter_push(db, manuscript, {}, "solo.md",
                             services=lambda: (fake, fake))
    check("an ALL-PROPOSED run pushes — this is the refusal the Sponsor "
          "hit, and the whole of the correction",
          result["written"] == 3 and not result["failed"],
          str(result["written"]))
    tab = fake.tab_text("solo.md")
    check("...and all three forms really are in the tab for them to rule "
          "on there",
          tab.count("<<") == 3 and "{{TWO REDONE.}}" in tab
          and "{{SIX REDONE.}}" in tab, tab)

    # The tab IS the review: one taken by silence, one reworded, one
    # emptied. No CLI verdict was ever given on any of them.
    _reword_in_tab(fake, "{{FOUR REDONE.}}", "{{Four, in my own words.}}")
    _reword_in_tab(
        fake,
        "<<Omega closes the essay on a falling cadence.>>"
        "{{SIX REDONE.}}",
        "Omega closes the essay on a falling cadence.")
    settle = api.filter_resolve(db, manuscript, {}, "solo.md",
                               services=lambda: (fake, fake))
    final = (ms / "solo.md").read_text()
    rows = {loads(t["metadata"], {}).get("anchor_paragraph"): t["state"]
            for t in api._run_threads(db, mid, _run_row(db, mid))}
    check("the three Doc-side outcomes are recorded as the three "
          "verdicts, for threads that were NEVER triaged in the shell: "
          "untouched → cleaned, reworded → cleaned, emptied → declined",
          rows[2] == "cleaned" and rows[4] == "cleaned"
          and rows[6] == "declined", str(rows))
    check("...and the manuscript carries exactly what the author did in "
          "the tab",
          "TWO REDONE." in final and "Four, in my own words." in final
          and "SIX REDONE." not in final
          and "Omega closes the essay on a falling cadence." in final,
          final)
    ev = db.all("SELECT signal FROM evidence WHERE manuscript_id = ? AND "
                "evidence_type = 'filter_edit' ORDER BY created_at",
                (mid,))
    signals = sorted(r["signal"] for r in ev)
    check("the evidence is the resolve's and ONLY the resolve's — one row "
          "per thread, no triage row in front of it, because no triage "
          "happened. `record_resolution` keys off `written` alone and "
          "never asks what the thread was before",
          signals == ["declined", "resolved", "revised"], str(signals))
    check("...and the modified acceptance is the learnings feedstock, "
          "recorded from a thread that was `proposed` an hour ago",
          len(settle["diffs"]) == 1
          and settle["diffs"][0]["final"] == "Four, in my own words.",
          str(settle["diffs"]))


def _mixed_push_set_membership(root: Path) -> None:
    """Which states go to the Doc, and what the author is told."""
    print("push set membership: proposed and accepted go, rejected stays:")

    db, manuscript, ms, fake = _doc_run(
        root, "mixed-ws",
        {2: "TWO REDONE.", 4: "FOUR REDONE.", 6: "SIX REDONE."},
        accept=False)
    mid = manuscript["id"]
    staged = api.filter_edits(db, manuscript, "solo.md")
    by_unit = {i["unit"]: i["n"] for i in staged["items"]}
    api.filter_triage(db, manuscript, "solo.md", [
        {"item": str(by_unit[2]), "verdict": "accept"},
        {"item": str(by_unit[6]), "verdict": "reject",
         "reason": "the cadence there is deliberate"}])

    result = api.filter_push(db, manuscript, {}, "solo.md",
                             services=lambda: (fake, fake))
    tab = fake.tab_text("solo.md")
    rows = {loads(t["metadata"], {}).get("anchor_paragraph"): t["state"]
            for t in api._run_threads(db, mid, _run_row(db, mid))}
    check("the ACCEPTED one and the UNTRIAGED one both go to the Doc",
          result["written"] == 2 and rows[2] == "written"
          and rows[4] == "written", str(rows))
    check("the REJECTED one stays home — that verdict is already given "
          "and its reason is already evidence",
          rows[6] == "rejected" and "{{SIX REDONE.}}" not in tab, tab)
    note = "\n".join(result["warnings"])
    check("the mixed-set line says ALL of them go and that the tab is "
          "where the verdict happens — the old line said the untriaged "
          "ones were NOT going, which was the defect",
          "All 2 changes go to the Doc" in note
          and "1 you have already accepted" in note
          and "1 you have not ruled on yet" in note
          and "The tab is the review" in note
          and "NOT going to the Doc" not in note, note)
    check("...and the rejected one is accounted for by count, so the "
          "author is never left wondering where it went",
          "1 change(s) you already turned down stay home" in note, note)

    # --- nothing left to push, when everything was turned down --------
    db2, ms2, msdir2, fake2 = _doc_run(root, "all-rejected-ws",
                                       {2: "TWO REDONE."}, accept=False)
    staged2 = api.filter_edits(db2, ms2, "solo.md")
    api.filter_triage(db2, ms2, "solo.md",
                      [{"item": str(staged2["items"][0]["n"]),
                        "verdict": "reject", "reason": "no"}])
    raised = None
    try:
        api.filter_push(db2, ms2, {}, "solo.md",
                        services=lambda: (fake2, fake2))
    except LookupError as err:
        raised = str(err)
    check("a run whose every proposal was turned down refuses, saying "
          "so — the refusal survives, it just no longer fires on an "
          "untriaged run",
          raised is not None and "nothing on solo.md can go to the Doc"
          in raised and "you turned down" in raised, raised)
    check("...and nothing reached the wire", not fake2.bodies)


def _failed_writes_keep_their_own_state(root: Path) -> None:
    """A thread that fails to land keeps the state it had — a proposal
    stays a proposal. Promoting it to `accepted` would fabricate a
    verdict the author never gave."""
    print("a partial push: each failed thread keeps its OWN prior state:")

    db, manuscript, ms, fake = _doc_run(
        root, "revert-ws", {2: "TWO REDONE.", 4: "FOUR REDONE.",
                            6: "SIX REDONE."}, accept=False)
    mid = manuscript["id"]
    staged = api.filter_edits(db, manuscript, "solo.md")
    by_unit = {i["unit"]: i["n"] for i in staged["items"]}
    api.filter_triage(db, manuscript, "solo.md",
                      [{"item": str(by_unit[4]), "verdict": "accept"}])

    # Units 4 and 6 vanish from the tab between the levelling push and
    # the marks: one was ACCEPTED, one was never triaged.
    original = gdocs.push_doc

    def _push_then_edit(db_, ms_, query, **kw):
        out = original(db_, ms_, query, **kw)
        tab = next(t for t in fake.tabs if t["title"] == "solo.md")
        tab["body"] = tab["body"].replace(
            "Gamma follows, saying something else entirely.",
            "Gamma, rewritten by the author in the Doc.").replace(
            "Omega closes the essay on a falling cadence.",
            "Omega, also rewritten in the Doc.")
        return out

    gdocs.push_doc = _push_then_edit
    try:
        result = api.filter_push(db, manuscript, {}, "solo.md",
                                 services=lambda: (fake, fake))
    finally:
        gdocs.push_doc = original

    rows = {loads(t["metadata"], {}).get("anchor_paragraph"): t["state"]
            for t in api._run_threads(db, mid, _run_row(db, mid))}
    check("the one that landed is `written`",
          result["written"] == 1 and len(result["failed"]) == 2
          and rows[2] == "written", str(rows))
    check("the ACCEPTED thread that failed is still `accepted`",
          rows[4] == "accepted", str(rows))
    check("the UNTRIAGED thread that failed is still `proposed` — NOT "
          "accepted. A failed write must never hand the machine's "
          "proposal the author's verdict",
          rows[6] == "proposed", str(rows))

    # --- and the same rule on the way back out ------------------------
    api.filter_unmark(db, manuscript, "solo.md", force=True,
                      services=lambda: (fake, fake))
    back = {loads(t["metadata"], {}).get("anchor_paragraph"): t["state"]
            for t in api._run_threads(db, mid, _run_row(db, mid))}
    check("`filter unmark` returns a written thread to the state it was "
          "pushed FROM — the untriaged one comes back `proposed`, not "
          "`accepted`. Unmark undoes the marking, never the triage, and "
          "it must not invent a verdict either",
          back[2] == "proposed", str(back))


def _the_doc_transport_push(root: Path) -> None:
    """F-D2, F-D3, F-D4, F-D5, F-D11, F-D19 — `filter push` puts the
    run's accepted forms in the Doc and leaves the disk alone."""
    from authorlm import revisions as _rev
    from authorlm import staging as _staging

    print("§2.3: filter push — the forms go to the Doc, the disk keeps "
          "the old text:")

    db, manuscript, ms, fake = _doc_run(
        root, "push-ws", {2: "TWO REDONE.", 4: "FOUR REDONE.",
                          5: "FIVE REDONE."})
    mid = manuscript["id"]
    before = (ms / "solo.md").read_bytes()
    observed_before = _rev.read_manuscript_files(ms)["solo.md"]

    result = api.filter_push(db, manuscript, {}, "solo.md",
                             services=lambda: (fake, fake))

    # --- F-D2: local holds OLD, unmarked, byte-identical --------------
    check("F-D2 the local file is BYTE-IDENTICAL to its pre-push text — "
          "in doc mode the essay on disk is the old text, and it is the "
          "thing the author can always fall back to",
          (ms / "solo.md").read_bytes() == before,
          (ms / "solo.md").read_text()[:200])
    check("F-D2 ...and it is not marked: `staging.mark_local` is the "
          "local transport's function and the doc road never calls it",
          not _staging.is_marked((ms / "solo.md").read_text()))
    check("F-D2 ...and the observation seam returns the same text it "
          "returned before the push — the canonicalizer is a proven "
          "no-op here, which is the point",
          _rev.read_manuscript_files(ms)["solo.md"] == observed_before)
    check("F-D2 ...and the verb SAYS the file is untouched, because on "
          "this fixture it is",
          result["local_unchanged"] is True)

    # --- F-D3: the forms reached the wire, and only those threads -----
    wire = "\n".join(fake.bodies)
    tab = fake.tab_text("solo.md")
    threads = {t["id"]: t for t in api._run_threads(db, mid,
                                                    _run_row(db, mid))}
    written = [t for t in threads.values() if t["state"] == "written"]
    check("F-D3 every accepted thread is now `written`",
          result["written"] == 3 and len(written) == 3,
          str({"reported": result["written"], "rows": len(written)}))
    check("F-D3 the recorded batchUpdate bodies carry the new half of "
          "every one of them, in the pending grammar",
          all(f'>>{{{{{t["proposed_new"]}}}}}' in wire for t in written),
          wire[-400:])
    check("F-D3 ...and the tab now really carries all three, well-formed "
          "and unnested",
          tab.count("<<") == 3 and "<<<<" not in tab
          and tab.count("{{") == 3, tab)
    check("F-D3 the run took the doc road, and says so",
          result["mode"] == "doc"
          and api._run_mode(_run_row(db, mid)) == "doc")

    # --- F-D19: the ordering invariant twin alignment rests on --------
    order_in_doc = [tab.index("{{TWO REDONE.}}"),
                    tab.index("{{FOUR REDONE.}}"),
                    tab.index("{{FIVE REDONE.}}")]
    by_created = [t["proposed_new"] for t in api.staging.door_threads(
        db, mid, "solo.md", states=("written",),
        origin_type=api.FILTER_ORIGIN)]
    check("F-D19 the DOCUMENT order of the placed forms equals the "
          "`created_at, id` order of their threads — twin self-alignment "
          "at resolve rests on nothing else, so it is asserted directly "
          "rather than inferred",
          order_in_doc == sorted(order_in_doc)
          and by_created == ["TWO REDONE.", "FOUR REDONE.", "FIVE REDONE."],
          str({"doc": order_in_doc, "threads": by_created}))
    check("F-D19 ...and the WRITE order was descending, which is what "
          "makes occurrence-from-original correct: the LAST body sent "
          "carries the LOWEST anchor's form",
          "{{TWO REDONE.}}" in fake.bodies[-1], fake.bodies[-1][:300])

    # --- F-D5: the DB push guard now fires for a doc-mode run ---------
    raised = None
    try:
        gdocs.push_doc(db, manuscript, "solo.md", service=fake,
                       docs_service=fake)
    except LookupError as err:
        raised = str(err)
    check("F-D5 `doc push` on a doc-mode essay is refused by the DB "
          "guard, naming `filter resolve` — the byte guard is blind here "
          "because the file is clean, and doc mode is the case that "
          "proves the two-guard ordering was right",
          raised is not None and "filter pending forms" in raised
          and "filter resolve solo.md" in raised, raised)
    check("F-D5 ...and the byte guard really is silent — only the DB "
          "guard can know anything in doc mode",
          not _staging.is_marked((ms / "solo.md").read_text()))

    # --- a second push is refused, naming both exits ------------------
    raised2 = None
    try:
        api.filter_push(db, manuscript, {}, "solo.md",
                        services=lambda: (fake, fake))
    except ValueError as err:
        raised2 = str(err)
    check("a second push while forms are out refuses early and BY NAME "
          "rather than three functions deep in push_doc's own guard",
          raised2 is not None and "already out" in raised2
          and "filter resolve solo.md" in raised2
          and "filter unmark solo.md --force" in raised2, raised2)

    # --- F-D11: the checkout gate discriminates -----------------------
    db3, ms3, msdir3, fake3 = _doc_run(root, "push-ws-3", {2: "TWO REDONE."})
    meta = gdocs._mapping(db3, ms3)
    meta["gdocs"]["solo.md"]["checked_out"] = True
    gdocs._save_mapping(db3, ms3, meta)
    raised3 = None
    try:
        api.filter_push(db3, ms3, {}, "solo.md",
                        services=lambda: (fake3, fake3))
    except ValueError as err:
        raised3 = str(err)
    check("F-D11 `filter push` refuses a CHECKED-OUT file, naming `doc "
          "pull` — staging read `old` from local, so pushing forms onto "
          "a tab the author has since edited is the drift case",
          raised3 is not None and "checked out to Google Docs" in raised3
          and "doc pull solo.md" in raised3, raised3)
    check("F-D11 ...and nothing reached the wire: no request body was "
          "formed at all", not fake3.bodies, str(fake3.bodies[:1]))


NON_CANONICAL_ESSAY = (
    "# The Wall\n\n"
    "* one thing the wall does   \n"
    "* another thing it does\n\n"
    "Alpha opens the essay and says a thing worth saying twice.  \n\n"
    "Gamma follows, saying something else entirely.\n\n\n\n"
    "Omega closes the essay on a falling cadence.\n")


def _the_push_says_what_it_did_to_the_file(root: Path) -> None:
    """P2b — `filter push` must not claim the local file is untouched
    when the levelling push canonicalized it.

    The truth was already computed (`local_unchanged`) and thrown away,
    while the CLI printed "still holds <file> exactly as it was"
    unconditionally. On a file that was not already canonical that is
    false, and an author who later finds a diff they were told did not
    exist has been given a reason to distrust the whole road.

    The fixture is deliberately non-canonical — `*` bullets, trailing
    spaces, a run of blank lines — so the assertion can actually FAIL.
    F-D2's original fixture was already canonical, which made its
    byte-identity claim true for a reason that had nothing to do with
    the code under test."""
    import authorlm.gdocs as _gd

    print("P2b: what the push did to the file on disk:")

    ws = root / "noncanon-ws"
    db, manuscript, ms, fake = _doc_run(
        root, "noncanon-ws", {3: "ALPHA REDONE."},
        essay=NON_CANONICAL_ESSAY)
    before = (ms / "solo.md").read_text()
    check("the fixture really is non-canonical — otherwise this whole "
          "test is asserting nothing (§14.8)",
          gdocs.normalize_markdown(before) != before, repr(before))

    saved = (_gd.get_service, _gd.get_docs_service)
    _gd.get_service = lambda *a, **k: fake
    _gd.get_docs_service = lambda *a, **k: fake
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli_main(["--workspace", str(ws), "filter", "push", "solo.md"])
        printed = out.getvalue()
    finally:
        _gd.get_service, _gd.get_docs_service = saved

    after = (ms / "solo.md").read_text()
    check("P2b the push DID rewrite the file — this is the case the old "
          "message lied about",
          after != before and after == gdocs.normalize_markdown(before),
          repr(after))
    check("P2b ...and the verb says so instead of claiming the file is "
          "as it was: it names what moved (markers, spacing) and what "
          "did not (the prose)",
          "exactly as it was" not in printed
          and "did rewrite it once" in printed
          and "not a word of the prose" in printed, printed)
    check("P2b ...and it still says the plain thing that matters: the "
          "file holds the OLD essay, none of these changes is in it",
          "still holds the OLD solo.md" in printed
          and "none of these changes is in it" in printed, printed)
    check("P2b the file really does still hold the old text, unmarked — "
          "canonicalization moved bytes, not meaning",
          "ALPHA REDONE." not in after
          and "Alpha opens the essay and says a thing worth saying twice."
          in after and "<<" not in after, after)


def _the_push_is_partial_or_nothing(root: Path) -> None:
    """F-D4 — a thread whose old text is absent from the tab fails alone;
    a push that lands NOTHING leaves the run's transport unchosen."""
    print("§2.3 step 7-8: a partial push, and a push that lands nothing:")

    db, manuscript, ms, fake = _doc_run(
        root, "partial-ws", {2: "TWO REDONE.", 4: "FOUR REDONE."})
    mid = manuscript["id"]
    # The author edited unit 4 in the Doc since the run was staged. The
    # local drift check passes (local is pristine); the TAB no longer
    # carries that paragraph verbatim, so that one thread fails there.
    original = gdocs.push_doc

    def _push_then_edit(db_, ms_, query, **kw):
        out = original(db_, ms_, query, **kw)
        tab = next(t for t in fake.tabs if t["title"] == "solo.md")
        tab["body"] = tab["body"].replace(
            "Gamma follows, saying something else entirely.",
            "Gamma, rewritten by the author in the Doc this morning.")
        return out

    gdocs.push_doc = _push_then_edit
    try:
        result = api.filter_push(db, manuscript, {}, "solo.md",
                             services=lambda: (fake, fake))
    finally:
        gdocs.push_doc = original

    states = {t["proposed_new"]: t["state"] for t in api._run_threads(
        db, mid, _run_row(db, mid))}
    check("F-D4 the thread whose old text is gone from the tab FAILS, "
          "by reason, and the other one lands",
          result["written"] == 1 and len(result["failed"]) == 1
          and "not found verbatim" in result["failed"][0][1],
          str({"written": result["written"],
               "failed": [w for _t, w in result["failed"]]}))
    check("F-D4 ...and the failed thread stays `accepted`, so it can "
          "still be settled locally or reworded",
          states.get("FOUR REDONE.") == "accepted"
          and states.get("TWO REDONE.") == "written", str(states))
    check("F-D4 ...and the mode IS set, because something landed",
          api._run_mode(_run_row(db, mid)) == "doc")

    # And now the other half: a push where NOTHING lands.
    db2, ms2, msdir2, fake2 = _doc_run(root, "nothing-ws",
                                       {2: "TWO REDONE."})
    mid2 = ms2["id"]

    def _push_then_wipe(db_, ms_, query, **kw):
        out = original(db_, ms_, query, **kw)
        tab = next(t for t in fake2.tabs if t["title"] == "solo.md")
        tab["body"] = "Nothing this run staged is in this tab any more.\n"
        return out

    gdocs.push_doc = _push_then_wipe
    try:
        result2 = api.filter_push(db2, ms2, {}, "solo.md",
                                  services=lambda: (fake2, fake2))
    finally:
        gdocs.push_doc = original
    check("F-D4 a push where NOTHING landed leaves the transport "
          "UNCHOSEN — recording it would strand the run in a mode with "
          "no forms in it",
          result2["written"] == 0
          and api._run_mode(_run_row(db2, mid2)) is None,
          str(result2["mode"]))
    check("F-D4 ...and every thread is still `accepted`, so the local "
          "road is still open to this run",
          all(t["state"] == "accepted" for t in api._run_threads(
              db2, mid2, _run_row(db2, mid2))
              if t["state"] != "rejected"))


def _twins_go_to_the_doc(root: Path) -> None:
    """F-D16 / §9.2 — push ALL, twins included, with one warning line."""
    print("§9: identical old halves are pushed, not held back:")

    db, manuscript, ms, fake = _doc_run(
        root, "twins-push-ws", {3: "TWIN ONE REDONE.", 5: "TWIN TWO REDONE."})
    mid = manuscript["id"]
    result = api.filter_push(db, manuscript, {}, "solo.md",
                             services=lambda: (fake, fake))
    note = "\n".join(result["warnings"])
    check("§9.2 push says the twins exist — naming the shared text, both "
          "units, and the one input that misfiles the record",
          "same paragraph text, word for word" in note
          and "units 3 and 5" in note
          and "settle by position" in note
          and "empty its green half" in note, note)
    check("§9.2 ...and it WARNED rather than blocking: both twins went "
          "to the Doc, which is the Sponsor's ruling",
          result["written"] == 2 and not result["failed"],
          str(result["written"]))
    tab = fake.tab_text("solo.md")
    check("F-D16 each twin's form sits at its OWN paragraph, in the "
          "essay's own order, with no nesting",
          "<<<<" not in tab
          and tab.index("{{TWIN ONE REDONE.}}") < tab.index("Gamma follows")
          < tab.index("{{TWIN TWO REDONE.}}"), tab)


def _reword_in_tab(fake, old_form: str, new_form: str) -> None:
    """The author, editing the {{new}} half in Google Docs."""
    tab = next(t for t in fake.tabs if t["title"] == "solo.md")
    assert old_form in tab["body"], tab["body"]
    tab["body"] = tab["body"].replace(old_form, new_form)


def _the_doc_settle(root: Path) -> None:
    """F-D7, F-D12, F-D14 — the resolve reads the DOC, three-ways it
    against local, and ends in exactly the local road's evidence."""
    from authorlm import staging as _staging

    print("§2.4: the doc settle reads the tab, not a pulled local file:")

    db, manuscript, ms, fake = _doc_run(
        root, "settle-ws", {2: "TWO AS PROPOSED.", 4: "FOUR AS PROPOSED.",
                            6: "SIX AS PROPOSED."})
    mid = manuscript["id"]
    api.filter_push(db, manuscript, {}, "solo.md",
                    services=lambda: (fake, fake))
    # The author, in the Doc: rewords one, leaves one alone, DELETES the
    # whole marked span of the third.
    _reword_in_tab(fake, "{{TWO AS PROPOSED.}}", "{{TWO, IN MY OWN WORDS.}}")
    _reword_in_tab(
        fake,
        "<<Omega closes the essay on a falling cadence.>>"
        "{{SIX AS PROPOSED.}}",
        "Omega closes the essay on a falling cadence.")

    check("F-D11 the push left the file CHECKED OUT — in doc mode the Doc "
          "is the working copy, which is the whole point of the mode",
          gdocs.doc_status(db, manuscript)["solo.md"]["checked_out"] is True)

    result = api.filter_resolve(db, manuscript, {}, "solo.md",
                               services=lambda: (fake, fake))
    final = (ms / "solo.md").read_text()

    check("F-D11 ...and the doc settle does NOT refuse it: the gate would "
          "refuse the one verb whose job is to read the Doc back, which "
          "is why `critique resolve` has no gate either",
          result["run"]["status"] == "settled", str(result["run"]["status"]))
    check("F-D14 the resolve never marks local: the finished essay carries "
          "the {{new}} halves' TEXT and no marker",
          not _staging.is_marked(final) and "<<" not in final
          and "{{" not in final, final)
    check("the author's Doc-side rewording WINS, the untouched form is "
          "taken as an acceptance, and the deleted one leaves the old "
          "text standing",
          "TWO, IN MY OWN WORDS." in final
          and "FOUR AS PROPOSED." in final
          and "Omega closes the essay on a falling cadence." in final
          and "SIX AS PROPOSED." not in final, final)

    rows = {loads(t["metadata"], {}).get("anchor_paragraph"): t["state"]
            for t in api._run_threads(db, mid, _run_row(db, mid))}
    check("F-D7 the reworded form is `cleaned` and the untouched one is "
          "too — an untouched form is an ACCEPTANCE, the standing "
          "critique contract",
          rows[2] == "cleaned" and rows[4] == "cleaned", str(rows))
    check("F-D7 ...and the form the author DELETED is `declined`",
          rows[6] == "declined", str(rows))

    ev = db.all("SELECT * FROM evidence WHERE manuscript_id = ? AND "
                "evidence_type = 'filter_edit' ORDER BY created_at", (mid,))
    check("F-D7 every evidence row of a DOC settle is `filter_edit` with "
          "`episode_id IS NULL` and weight 'high' — byte for byte the "
          "local road's shape, because it is the same call",
          len(ev) >= 3 and all(r["episode_id"] is None for r in ev)
          and all(r["weight"] == "high" for r in ev),
          str([(r["evidence_type"], r["episode_id"], r["weight"])
               for r in ev]))
    check("F-D7 the modified acceptance is recorded as a proposal→final "
          "diff, and ONLY it",
          len(result["diffs"]) == 1
          and result["diffs"][0]["proposal"] == "TWO AS PROPOSED."
          and result["diffs"][0]["final"] == "TWO, IN MY OWN WORDS.",
          str(result["diffs"]))
    check("Q-2 the resolve does NOT re-push, and says the tab keeps its "
          "marks until the next ordinary `doc push`",
          result["tab_still_marked"] is True
          and "<<" in fake.tab_text("solo.md"), fake.tab_text("solo.md"))
    check("the run settled, on the doc road",
          result["run"]["status"] == "settled" and result["mode"] == "doc")


def _a_hand_resolved_tab_settles_true(root: Path) -> None:
    """it-4c5a8038c304 — the author resolves the forms BY HAND in the
    Doc: deletes the markers and leaves the prose they want, instead of
    editing inside the braces. The settle infers the verdicts from the
    text itself (the author's ruling, 2026-08-31): old standing as-is →
    decline; old gone → acceptance, the tab's prose being the final."""
    print("it-4c5a8038c304: a hand-resolved tab settles as the author "
          "ruled:")

    db, manuscript, ms, fake = _doc_run(
        root, "handres-ws", {2: "TWO AS PROPOSED.", 4: "FOUR AS PROPOSED.",
                             6: "SIX AS PROPOSED."})
    mid = manuscript["id"]
    api.filter_push(db, manuscript, {}, "solo.md",
                    services=lambda: (fake, fake))
    # Unit 2: kept the new verbatim — markers deleted, plain prose left.
    _reword_in_tab(
        fake,
        "<<Alpha opens the essay and says a thing worth saying twice.>>"
        "{{TWO AS PROPOSED.}}",
        "TWO AS PROPOSED.")
    # Unit 4: kept the change but reworded it — markers deleted.
    _reword_in_tab(
        fake,
        "<<Gamma follows, saying something else entirely.>>"
        "{{FOUR AS PROPOSED.}}",
        "FOUR AS PROPOSED, BUT IN MY OWN WORDS.")
    # Unit 6: turned the change down — markers deleted, OLD text stands.
    _reword_in_tab(
        fake,
        "<<Omega closes the essay on a falling cadence.>>"
        "{{SIX AS PROPOSED.}}",
        "Omega closes the essay on a falling cadence.")

    result = api.filter_resolve(db, manuscript, {}, "solo.md",
                               services=lambda: (fake, fake))
    final = (ms / "solo.md").read_text()
    rows = {loads(t["metadata"], {}).get("anchor_paragraph"): t["state"]
            for t in api._run_threads(db, mid, _run_row(db, mid))}
    check("old gone + new kept verbatim → cleaned (an acceptance), "
          "NOT declined", rows[2] == "cleaned", str(rows))
    check("old gone + new reworded → cleaned (a modified acceptance)",
          rows[4] == "cleaned", str(rows))
    check("old standing as-is → declined, exactly as before",
          rows[6] == "declined", str(rows))
    check("the manuscript carries what the author left in the tab",
          "TWO AS PROPOSED." in final
          and "FOUR AS PROPOSED, BUT IN MY OWN WORDS." in final
          and "SIX AS PROPOSED." not in final
          and "Omega closes the essay on a falling cadence." in final,
          final)
    check("the hand-rewording is recorded as a proposal→final diff, and "
          "the thread's final wording is the author's",
          len(result["diffs"]) == 1
          and result["diffs"][0]["proposal"] == "FOUR AS PROPOSED."
          and result["diffs"][0]["final"]
          == "FOUR AS PROPOSED, BUT IN MY OWN WORDS.",
          str(result["diffs"]))
    ev = db.all("SELECT signal FROM evidence WHERE manuscript_id = ? AND "
                "evidence_type = 'filter_edit' ORDER BY created_at", (mid,))
    check("the resolve's evidence reads accept / revise / decline — the "
          "verdicts the author actually gave",
          sorted(r["signal"] for r in ev[-3:])
          == ["declined", "resolved", "revised"],
          str([r["signal"] for r in ev]))


def _the_lens_door(root: Path) -> None:
    """§7.2, built (2026-08-31) — a lens finding carrying a `replacement`
    walks through the door: staged as origin_type='lens', pushed into the
    tab as forms, settled back under the filter road's whole contract,
    the hand-resolution inference included. Ambiguity is refused, never
    guessed; findings and edits stay independent verdicts."""
    from authorlm import lenses as _lenses

    print("§7.2: the lens door — findings with replacements:")

    ws = root / "lensdoor-ws"
    ms = ws / "book"
    ms.mkdir(parents=True)
    (ms / "solo.md").write_text(DOC_ESSAY)
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms)])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, {})
    _lenses.add_lens(manuscript, "door-lens", "# Door lens\nA test lens.")
    session, _ = api.ensure_session(db, manuscript)
    mid = manuscript["id"]

    result = _lenses.register_findings(
        db, manuscript, session, "door-lens", "solo.md", [
            {"quote": "says a thing worth saying twice",
             "note": "the phrase undercuts itself",
             "replacement": "says a thing once, and well"},
            {"quote": "Gamma follows, saying something else entirely.",
             "note": "flat connective",
             "replacement": "Gamma follows, and changes the subject."},
            {"quote": "And so the wall stands, and the Dead do not pass.",
             "note": "twin paragraphs",
             "replacement": "AND SO REWORDED."},
            {"quote": "Omega closes the essay on a falling cadence.",
             "note": "a finding with no replacement stays a finding"},
        ])
    check("anchored replacements stage doc_threads rows; the ambiguous "
          "twin quote is refused (never guessed); the plain finding "
          "stages nothing",
          len(result["edits_staged"]) == 2
          and len(result["edits_refused"]) == 1
          and "2 units" in result["edits_refused"][0]["reason"]
          and len(result["findings"]) == 4, str(result["edits_refused"]))
    check("the harness built `new` from the unit ITSELF — the quote "
          "replaced inside the unit's own text, `old` never supplied by "
          "the producer",
          any(t["proposed_new"] ==
              "Alpha opens the essay and says a thing once, and well."
              for t in result["edits_staged"]),
          str([t["proposed_new"] for t in result["edits_staged"]]))
    linked = [json.loads(r["metadata"]).get("edit_thread")
              for r in result["findings"]]
    check("findings and their edits are linked, and only where an edit "
          "was staged",
          sum(1 for x in linked if x) == 2, str(linked))

    fake = _SurgicalDocFake([("book", ""), ("solo.md", DOC_ESSAY)])
    meta = gdocs._mapping(db, manuscript)
    links = meta.setdefault("gdocs", {})
    links["_master_id"] = "doc-fake"
    links["_container_tab"] = "tab-1"
    links["solo.md"] = {"tab_id": "tab-2", "checked_out": False,
                        "pushed_hash": None}
    gdocs._save_mapping(db, manuscript, meta)

    push = api.lens_push(db, manuscript, {}, "solo.md",
                         services=lambda: (fake, fake))
    check("lens push writes the staged edits as forms; local keeps the "
          "OLD text",
          push["written"] == 2 and push["local_unchanged"]
          and "<<" in fake.tab_text("solo.md"), str(push))
    try:
        api.lens_push(db, manuscript, {}, "solo.md",
                      services=lambda: (fake, fake))
        raised = False
    except ValueError as err:
        raised = "already out" in str(err)
    check("a second lens push refuses while forms are out", raised)

    # The author, in the Doc: hand-resolves one form (markers deleted,
    # new text kept) and rewords the other inside its braces.
    _reword_in_tab(
        fake,
        "<<Alpha opens the essay and says a thing worth saying twice.>>"
        "{{Alpha opens the essay and says a thing once, and well.}}",
        "Alpha opens the essay and says a thing once, and well.")
    _reword_in_tab(
        fake,
        "{{Gamma follows, and changes the subject.}}",
        "{{Gamma follows, and turns the page.}}")

    settle = api.lens_resolve(db, manuscript, {}, "solo.md",
                             services=lambda: (fake, fake))
    final = (ms / "solo.md").read_text()
    check("both verdicts land true — the hand-resolved form is an "
          "acceptance, the braces rewording a modified acceptance",
          settle["accepted"] == 2 and settle["declined"] == 0
          and "says a thing once, and well." in final
          and "Gamma follows, and turns the page." in final, final)
    check("the modified acceptance is the learnings feedstock",
          len(settle["diffs"]) == 1
          and settle["diffs"][0]["final"]
          == "Gamma follows, and turns the page.", str(settle["diffs"]))
    ev = db.all("SELECT * FROM evidence WHERE manuscript_id = ? AND "
                "evidence_type = 'lens_edit' ORDER BY created_at", (mid,))
    check("evidence lands as lens_edit — the door's own stream, NO "
          "episode",
          sorted(r["signal"] for r in ev) == ["resolved", "revised"]
          and all(r["episode_id"] is None for r in ev),
          str([r["signal"] for r in ev]))


def _the_changed_words_pop(root: Path) -> None:
    """Author request 2026-08-31: a one-word edit inside a
    paragraph-sized form was invisible in the Doc. The push now
    word-diffs old vs new deterministically and colors the differing
    words (red in the struck half, blue in the green half) — COLOR
    only, because the resolve reads the markdown export and bold would
    leak literal asterisks into the resolved prose."""
    print("the changed words pop: word-diff highlights on the form:")

    old = "From Nothing, the sermon derives two opposing Fields."
    new = "From Nothing, the sermon proposes two opposing Fields."
    reqs = gdocs._mark_replace_requests("tab-9", 100, 100 + len(old),
                                        old, new)
    base = reqs[:4]
    hi = reqs[4:]
    check("the base form requests are unchanged: insert-new, insert-<<, "
          "strike, green",
          len(base) == 4 and "insertText" in base[0]
          and base[2]["updateTextStyle"]["textStyle"]["strikethrough"]
          is True, str(base))
    old_base = 100 + 2
    new_base = 100 + 4 + len(old) + 2
    d_start = old.index("derives")
    p_start = new.index("proposes")
    check("exactly one red span over 'derives' and one blue span over "
          "'proposes', at the right utf-16 offsets inside the form",
          len(hi) == 2
          and hi[0]["updateTextStyle"]["range"]["startIndex"]
          == old_base + d_start
          and hi[0]["updateTextStyle"]["range"]["endIndex"]
          == old_base + d_start + len("derives")
          and hi[0]["updateTextStyle"]["textStyle"]["foregroundColor"]
          == gdocs.RED_GONE
          and hi[1]["updateTextStyle"]["range"]["startIndex"]
          == new_base + p_start
          and hi[1]["updateTextStyle"]["range"]["endIndex"]
          == new_base + p_start + len("proposes")
          and hi[1]["updateTextStyle"]["textStyle"]["foregroundColor"]
          == gdocs.BLUE_NEW, str(hi))
    check("color only — no highlight request touches bold or italics",
          all(r["updateTextStyle"]["fields"] == "foregroundColor"
              for r in hi), str(hi))

    # A cut (words removed, nothing added on that side) highlights only
    # the struck side.
    old2 = "The claim stands. It repeats the image in plainer words."
    new2 = "The claim stands."
    _, spans = gdocs._word_diff_spans(old2, new2)
    old_spans, _ = gdocs._word_diff_spans(old2, new2)
    check("a pure cut yields red spans and no blue",
          old_spans and not spans, str((old_spans, spans)))

    # A rewrite where most words changed is all change and no signal:
    # no highlights at all.
    o3, n3 = gdocs._word_diff_spans(
        "Alpha beta gamma delta epsilon.",
        "Entirely different words in every position here.")
    check("a mostly-rewritten form gets NO highlight — all-highlight is "
          "no signal", o3 == [] and n3 == [], str((o3, n3)))
    reqs3 = gdocs._mark_replace_requests(
        "tab-9", 50, 80, "Alpha beta gamma delta epsilon.",
        "Entirely different words in every position here.")
    check("...and the form request list is exactly the base four",
          len(reqs3) == 4, str(len(reqs3)))

    # --- F-D12: a genuine two-sided edit refuses, and moves nothing ---
    db2, ms2, msdir2, fake2 = _doc_run(root, "conflict-ws",
                                       {2: "TWO AS PROPOSED."})
    mid2 = ms2["id"]
    api.filter_push(db2, ms2, {}, "solo.md",
                    services=lambda: (fake2, fake2))
    local_drift = (msdir2 / "solo.md").read_text().replace(
        "Omega closes the essay on a falling cadence.",
        "Omega closes, rewritten on disk and nowhere else.")
    (msdir2 / "solo.md").write_text(local_drift)
    tab2 = next(t for t in fake2.tabs if t["title"] == "solo.md")
    tab2["body"] = tab2["body"].replace(
        "Gamma follows, saying something else entirely.",
        "Gamma, rewritten in the Doc and nowhere else.")
    raised = None
    try:
        api.filter_resolve(db2, ms2, {}, "solo.md",
                          services=lambda: (fake2, fake2))
    except ValueError as err:
        raised = str(err)
    check("F-D12 local moved AND the tab's settled content moved → the "
          "doc settle refuses rather than picking a side",
          raised is not None
          and "changed both locally and in the Doc" in raised, raised)
    check("F-D12 ...and it resolved NOTHING: the file is untouched and "
          "every thread is still `written`",
          (msdir2 / "solo.md").read_text() == local_drift
          and all(t["state"] == "written" for t in api._run_threads(
              db2, mid2, _run_row(db2, mid2))),
          (msdir2 / "solo.md").read_text()[:120])
    check("F-D12 ...and the run is still active — a refusal settles "
          "nothing",
          _run_row(db2, mid2)["status"] == "active")


def _a_pull_between_push_and_settle(root: Path) -> None:
    """F-D6 — `doc pull` while forms are out is legal and harmless, and
    the resolve afterwards still finds them."""
    print("§2.6: a pull between the push and the resolve:")

    db, manuscript, ms, fake = _doc_run(
        root, "pull-ws", {2: "TWO AS PROPOSED.", 4: "FOUR AS PROPOSED."})
    mid = manuscript["id"]
    api.filter_push(db, manuscript, {}, "solo.md",
                    services=lambda: (fake, fake))
    _reword_in_tab(fake, "{{TWO AS PROPOSED.}}", "{{TWO, REWORDED.}}")
    tab = next(t for t in fake.tabs if t["title"] == "solo.md")
    tab["body"] = tab["body"].replace(
        "Omega closes the essay on a falling cadence.",
        "Omega closes, with a sentence the author added in the Doc.")
    before_tab = tab["body"]

    with contextlib.redirect_stdout(io.StringIO()):
        report = gdocs.pull_doc(db, manuscript, "solo.md", service=fake,
                                docs_service=fake, with_comments=False)
    local = (ms / "solo.md").read_text()
    check("F-D6 the pull is NOT skipped — the local file is unmarked, so "
          "the byte guard is silent, and that is correct",
          "solo.md" not in report.get("marked", []), str(report))
    check("F-D6 it wrote the OLD halves locally and never a marker — "
          "`strip_pending` collapses each form to its old half, which is "
          "exactly why the resolve must read the DOC and not this file",
          "<<" not in local and "{{" not in local
          and "TWO REDONE." not in local
          and "Alpha opens the essay" in local, local)
    check("F-D6 ...and it landed the author's edit made ELSEWHERE in the "
          "tab", "a sentence the author added in the Doc" in local, local)
    check("F-D6 the pull never wrote to the Doc: the tab still carries "
          "both forms", fake.tab_text("solo.md") == before_tab)
    check("F-D6 ...and the run's `written` rows are untouched",
          sum(1 for t in api._run_threads(db, mid, _run_row(db, mid))
              if t["state"] == "written") == 2)
    check("F-D6 ...and the checkout is cleared",
          not gdocs.doc_status(db, manuscript)["solo.md"]["checked_out"])

    result = api.filter_resolve(db, manuscript, {}, "solo.md",
                               services=lambda: (fake, fake))
    final = (ms / "solo.md").read_text()
    check("F-D6 a resolve AFTER that pull still finds both forms and "
          "takes the reworded half",
          result["forms"] == 2 and "TWO, REWORDED." in final
          and "FOUR AS PROPOSED." in final and "<<" not in final, final)


def _twins_settle_by_position(root: Path) -> None:
    """F-D17 / F-D18 — the twin join is positional; the TEXT is always
    right and the attribution is best-effort when a span is deleted."""
    print("§9.1: twins settle by position:")

    db, manuscript, ms, fake = _doc_run(
        root, "twins-settle-ws",
        {3: "TWIN ONE REDONE.", 5: "TWIN TWO REDONE."})
    mid = manuscript["id"]
    api.filter_push(db, manuscript, {}, "solo.md",
                    services=lambda: (fake, fake))
    _reword_in_tab(fake, "{{TWIN ONE REDONE.}}", "{{THE FIRST, MY WORDING.}}")
    result = api.filter_resolve(db, manuscript, {}, "solo.md",
                               services=lambda: (fake, fake))
    final = (ms / "solo.md").read_text()
    check("F-D17 the reworded text lands at the FIRST twin and the "
          "original proposal at the second — each form's own text in its "
          "own place",
          final.index("THE FIRST, MY WORDING.")
          < final.index("Gamma follows")
          < final.index("TWIN TWO REDONE."), final)
    check("F-D17 the `revised` diff attaches to the thread whose form was "
          "first in document order, and ONLY to it",
          len(result["diffs"]) == 1
          and result["diffs"][0]["proposal"] == "TWIN ONE REDONE.",
          str(result["diffs"]))

    # --- F-D18: one twin's whole marked span DELETED in the Doc -------
    db2, ms2, msdir2, fake2 = _doc_run(
        root, "twins-deleted-ws",
        {3: "TWIN ONE REDONE.", 5: "TWIN TWO REDONE."})
    mid2 = ms2["id"]
    api.filter_push(db2, ms2, {}, "solo.md",
                    services=lambda: (fake2, fake2))
    _reword_in_tab(
        fake2,
        "<<And so the wall stands, and the Dead do not pass.>>"
        "{{TWIN ONE REDONE.}}",
        "And so the wall stands, and the Dead do not pass.")
    result2 = api.filter_resolve(db2, ms2, {}, "solo.md",
                                services=lambda: (fake2, fake2))
    final2 = (msdir2 / "solo.md").read_text()
    check("F-D18 the TEXT outcome is strict: the surviving form's text "
          "lands in its own place and the deleted one leaves the old "
          "paragraph standing",
          final2.count("And so the wall stands, and the Dead do not pass.")
          == 1
          and final2.index("And so the wall stands") < final2.index("Gamma")
          < final2.index("TWIN TWO REDONE."), final2)
    states = sorted(t["state"] for t in api._run_threads(
        db2, mid2, _run_row(db2, mid2)) if t["state"] in ("cleaned",
                                                          "declined"))
    check("F-D18 exactly one thread is `declined` and one `cleaned` — "
          "the attribution is POSITIONAL BEST-EFFORT (RISK-6, accepted "
          "by Sponsor ruling): which of two threads proposing changes to "
          "identical text got the decline is not recoverable from a Doc "
          "that carries no thread id",
          states == ["cleaned", "declined"], str(states))
    check("F-D18 ...and the manuscript text is correct regardless, which "
          "is the half that was never allowed to be best-effort",
          "<<" not in final2 and "{{" not in final2, final2)


def _the_learnings_loop_reaches_the_filter(root: Path) -> None:
    """F-D8 / §1.2 — the pattern-candidate distiller fires for a filter
    settle, on BOTH transports, exactly once, and only at ≥2 modified
    acceptances.

    `record_resolution` was origin-parameterized in the filter stream, so
    the proposal→final diffs have always been recorded. The half that
    turns them into a rule the author can ratify lived in a CLI-private
    `_critique_learnings` and only `critique resolve` reached it, so the
    highest-value output of a resolve — *your post-edits keep doing the
    same thing; here is the rule you may be enacting* — had never fired
    for a filter, in either mode."""
    from authorlm import placement as _placement

    print("§1.2: the learnings loop, now reaching both filter roads:")

    calls: list[list] = []

    def _recording_distiller(db_, ms_, items, llm):
        calls.append(list(items))
        return {"statement": "prefer the Anglo-Saxon word"}

    original = _placement.distill_batch
    _placement.distill_batch = _recording_distiller
    try:
        # --- LOCAL road: pause, reword TWO halves in the file, settle --
        db, manuscript, ms, _fake = _doc_run(
            root, "learn-local", {2: "TWO AS PROPOSED.",
                                  4: "FOUR AS PROPOSED."})
        with contextlib.redirect_stdout(io.StringIO()):
            api.filter_resolve(db, manuscript, {}, "solo.md", pause=True)
        marked = (ms / "solo.md").read_text()
        (ms / "solo.md").write_text(
            marked.replace("{{TWO AS PROPOSED.}}", "{{TWO, MY WORDING.}}")
                  .replace("{{FOUR AS PROPOSED.}}", "{{FOUR, MY WORDING.}}"))
        result = api.filter_resolve(db, manuscript, {}, "solo.md")
        check("F-D8 two modified acceptances on the LOCAL road call the "
              "shared distiller EXACTLY once — the filter pass never "
              "reached this before, in either mode, and doc mode must "
              "not be the only road that closes the gap",
              len(calls) == 1 and len(calls[0]) == 2, str(calls))
        check("F-D8 ...and the candidate is returned to the caller rather "
              "than printed inside the distiller, so each surface owns "
              "its own output",
              result["pattern_candidate"] == {
                  "statement": "prefer the Anglo-Saxon word"},
              str(result["pattern_candidate"]))

        # --- DOC road: reword TWO halves in the tab, settle -----------
        calls.clear()
        db2, ms2, msdir2, fake2 = _doc_run(
            root, "learn-doc", {2: "TWO AS PROPOSED.",
                                4: "FOUR AS PROPOSED."})
        api.filter_push(db2, ms2, {}, "solo.md",
                    services=lambda: (fake2, fake2))
        _reword_in_tab(fake2, "{{TWO AS PROPOSED.}}", "{{TWO, MY WORDING.}}")
        _reword_in_tab(fake2, "{{FOUR AS PROPOSED.}}",
                       "{{FOUR, MY WORDING.}}")
        result2 = api.filter_resolve(db2, ms2, {}, "solo.md",
                                    services=lambda: (fake2, fake2))
        check("F-D8 the same two on the DOC road call it exactly once too "
              "— the parity claim, made testable rather than asserted",
              len(calls) == 1 and len(calls[0]) == 2
              and len(result2["diffs"]) == 2, str(calls))

        # --- ONE modified acceptance: it must NOT fire ---------------
        calls.clear()
        db3, ms3, msdir3, _f3 = _doc_run(root, "learn-one",
                                         {2: "TWO AS PROPOSED."})
        with contextlib.redirect_stdout(io.StringIO()):
            api.filter_resolve(db3, ms3, {}, "solo.md", pause=True)
        marked3 = (msdir3 / "solo.md").read_text()
        (msdir3 / "solo.md").write_text(
            marked3.replace("{{TWO AS PROPOSED.}}", "{{TWO, MY WORDING.}}"))
        result3 = api.filter_resolve(db3, ms3, {}, "solo.md")
        check("F-D8 ONE modified acceptance does not fire it — a pattern "
              "needs at least two instances, and a candidate raised from "
              "one is a guess the author has to refuse",
              not calls and len(result3["diffs"]) == 1
              and result3["pattern_candidate"] is None, str(calls))
    finally:
        _placement.distill_batch = original


def _recovery_while_forms_are_out(root: Path) -> None:
    """F-D9 / F-D10 — rollback refuses while forms are out even though
    the local file is clean, and `filter unmark` takes them back out."""
    from authorlm import staging as _staging

    print("§2.5: rollback and unmark while the forms are in the Doc:")

    db, manuscript, ms, fake = _doc_run(
        root, "recover-ws", {2: "TWO AS PROPOSED.", 4: "FOUR AS PROPOSED."})
    mid = manuscript["id"]
    api.filter_push(db, manuscript, {}, "solo.md",
                    services=lambda: (fake, fake))
    before = (ms / "solo.md").read_bytes()

    # --- F-D9: the DB refusal, where the byte guard is blind ----------
    check("F-D9 the local file is UNMARKED, so the byte guard the local "
          "rollback relies on cannot see anything at all",
          not _staging.is_marked((ms / "solo.md").read_text()))
    raised = None
    try:
        api.filter_rollback(db, manuscript, {}, "solo.md")
    except ValueError as err:
        raised = str(err)
    check("F-D9 `filter rollback` refuses anyway, on the DB guard, and "
          "names BOTH exits — the byte check alone would have restored "
          "the pin over a file whose forms are sitting in the Doc "
          "pointed at text that no longer exists there",
          raised is not None and "still out in the Google Doc" in raised
          and "filter resolve solo.md" in raised
          and "filter unmark solo.md --force" in raised, raised)
    check("F-D9 ...and NOTHING moved: the file is byte-identical and "
          "every form is still `written`",
          (ms / "solo.md").read_bytes() == before
          and sum(1 for t in api._run_threads(db, mid, _run_row(db, mid))
                  if t["state"] == "written") == 2)

    # --- Q-4 / P2a: the refusal must describe the REAL blast radius ---
    # The author has also edited the tab somewhere OUTSIDE any marked
    # passage. The rebuild is whole-tab, so that edit dies too, and a
    # refusal saying "nothing else is at risk" is a lie about scope.
    elsewhere = "Omega closes, with a sentence I typed in the Doc."
    tab_row = next(t for t in fake.tabs if t["title"] == "solo.md")
    tab_row["body"] = tab_row["body"].replace(
        "Omega closes the essay on a falling cadence.", elsewhere)
    refused = None
    try:
        api.filter_unmark(db, manuscript, "solo.md",
                          services=lambda: (fake, fake))
    except ValueError as err:
        refused = str(err)
    check("Q-4 the DOC unmark requires --force and says exactly what is "
          "destroyed — the one place doc mode is deliberately LESS "
          "convenient than local, because it is the one place the loss "
          "is unrecoverable",
          refused is not None and "--force" in refused, refused)
    check("P2a ...and it names the REAL blast radius: the rebuild is "
          "WHOLE-TAB, so an edit made anywhere else in that tab dies "
          "with the green halves. Saying 'nothing else is at risk' was "
          "a lie about scope",
          refused is not None and "REBUILDS THE WHOLE TAB" in refused
          and "any other edit you made anywhere else" in refused
          and "nothing else is at risk" not in refused, refused)
    check("P2a ...and it names the exit that costs nothing: 'doc pull' "
          "brings an outside-the-forms edit down to the file first and "
          "leaves the tab and the forms alone",
          refused is not None and "doc pull solo.md" in refused
          and "leaves the tab and the forms alone" in refused, refused)
    check("Q-4 ...and the refusal moved nothing: the tab still carries "
          "both forms and both threads are still `written`",
          fake.tab_text("solo.md").count("<<") == 2
          and sum(1 for t in api._run_threads(db, mid, _run_row(db, mid))
                  if t["state"] == "written") == 2,
          fake.tab_text("solo.md"))

    # --- F-D10: the doc unmark, withdraw-then-rebuild -----------------
    bodies_before = len(fake.bodies)
    result = api.filter_unmark(db, manuscript, "solo.md", force=True,
                               services=lambda: (fake, fake))
    tab = fake.tab_text("solo.md")
    check("F-D10 the tab is rebuilt CLEAN — the threads return to "
          "`accepted` first, which is what lifts `push_doc`'s own "
          "forms_pending refusal, and only then does the push run",
          "<<" not in tab and "{{" not in tab
          and "Alpha opens the essay" in tab, tab)
    check("F-D10 ...and a rebuild really reached the wire",
          len(fake.bodies) > bodies_before)
    check("F-D10 the local file is BYTE-UNCHANGED — it has held the old "
          "text throughout, so there is nothing for this recovery to "
          "restore", (ms / "solo.md").read_bytes() == before)
    states = [t["state"] for t in api._run_threads(db, mid,
                                                   _run_row(db, mid))]
    check("F-D10 the author's VERDICTS survive: the forms are back at "
          "`accepted`, not withdrawn — unmark undoes the marking, never "
          "the triage",
          result["reopened"] == 2 and states.count("accepted") == 2
          and "withdrawn" not in states, str(states))
    check("P2a ...and the unrelated Doc-side edit really IS gone, which "
          "is why the refusal has to say so: the assertion is the honest "
          "outcome, not the comfortable one",
          elsewhere not in tab
          and "Omega closes the essay on a falling cadence." in tab, tab)

    # --- P2a: the remedy the refusal names is REAL, not a slogan ------
    db3, ms3, msdir3, fake3 = _doc_run(root, "recover-pull-ws",
                                       {2: "TWO AS PROPOSED."})
    api.filter_push(db3, ms3, {}, "solo.md",
                    services=lambda: (fake3, fake3))
    tab3 = next(t for t in fake3.tabs if t["title"] == "solo.md")
    tab3["body"] = tab3["body"].replace(
        "Omega closes the essay on a falling cadence.", elsewhere)
    with contextlib.redirect_stdout(io.StringIO()):
        gdocs.pull_doc(db3, ms3, "solo.md", service=fake3,
                       docs_service=fake3, with_comments=False)
        api.filter_unmark(db3, ms3, "solo.md", force=True,
                          services=lambda: (fake3, fake3))
    check("P2a following the named remedy — 'doc pull' first, then "
          "unmark --force — the outside-the-forms edit SURVIVES, in the "
          "file and in the rebuilt tab. The refusal names a real exit",
          elsewhere in (msdir3 / "solo.md").read_text()
          and elsewhere in fake3.tab_text("solo.md"),
          (msdir3 / "solo.md").read_text())
    check("P2a ...and the forms are still gone from the tab and the "
          "verdicts still survive",
          "<<" not in fake3.tab_text("solo.md")
          and sum(1 for t in api._run_threads(db3, ms3["id"],
                                              _run_row(db3, ms3["id"]))
                  if t["state"] == "accepted") == 1,
          fake3.tab_text("solo.md"))

    # --- and NOW the rollback the refusal named is reachable ---------
    # The rebuild left the file checked out, exactly as any `doc push`
    # does, so the ordinary gate stands in front of it and names its own
    # remedy. That is the pre-existing rule, not a leftover of the forms.
    still = None
    try:
        api.filter_rollback(db, manuscript, {}, "solo.md")
    except ValueError as err:
        still = str(err)
    check("after the rebuild the only thing left in front of the "
          "rollback is the ORDINARY checkout gate — the forms refusal is "
          "gone",
          still is not None and "checked out to Google Docs" in still
          and "still out" not in still, still)
    with contextlib.redirect_stdout(io.StringIO()):
        gdocs.pull_doc(db, manuscript, "solo.md", service=fake,
                       docs_service=fake, with_comments=False)
        rolled = api.filter_rollback(db, manuscript, {}, "solo.md")
    check("F-D9 with the forms taken out and the checkout cleared, the "
          "rollback the refusal named goes through",
          rolled["file"] == "solo.md")

    # --- the LOCAL road's byte guard is still separately reachable ----
    db2, ms2, msdir2, _f2 = _doc_run(root, "recover-local-ws",
                                     {2: "TWO AS PROPOSED."})
    mid2 = ms2["id"]
    with contextlib.redirect_stdout(io.StringIO()):
        api.filter_resolve(db2, ms2, {}, "solo.md", pause=True)
    db2.conn.execute("DELETE FROM doc_threads WHERE manuscript_id = ? AND "
                     "state = 'written'", (mid2,))
    db2.conn.commit()
    raised2 = None
    try:
        api.filter_rollback(db2, ms2, {}, "solo.md")
    except ValueError as err:
        raised2 = str(err)
    check("the BYTE guard is still separately reachable: with the rows "
          "deleted from under it, a marked local file still refuses — it "
          "is the guard that survives a database that has lost the run "
          "row, and asserting only the DB guard would leave it free to "
          "delete with the suite green",
          raised2 is not None and "mid-settle" in raised2, raised2)


def _two_runs_one_essay(root: Path, subdir: str):
    """A second filter with its own active run on the same essay — which
    the pass permits by design (it warns, it does not refuse)."""
    db, manuscript, ms, fake = _doc_run(
        root, subdir, {2: "TWO AS PROPOSED."})
    api.filter_add(manuscript, "second-filter",
                   DOC_FILTER.replace("Duplicate words and phrases",
                                      "A second concern entirely"))
    api.filter_run(db, manuscript, {}, "second-filter", "solo.md")
    units = passes.paragraphs_of(DOC_ESSAY)
    api.filter_record(db, manuscript, {}, "solo.md",
                      _filter_reply(units, {4: "GAMMA, SECOND FILTER."}),
                      name="second-filter")
    return db, manuscript, ms, fake


def _chat_sees_each_run_s_transport(root: Path) -> None:
    """P3b — `filter_edits` (and so `list_filter_edits` on MCP) must
    report the transport PER RUN.

    Two filters on one essay is legitimate work. When their transports
    differ, collapsing them to a single value produced `mode=None` — read
    by chat as "no transport chosen" — and suppressed the transport note
    in exactly the case it matters most: one run's forms sitting in the
    author's Doc while another's proposals are still on the table."""
    print("P3b: two runs on one essay, on different roads:")

    db, manuscript, ms, fake = _two_runs_one_essay(root, "two-runs-ws")
    mid = manuscript["id"]
    api.filter_push(db, manuscript, {}, "solo.md",
                    services=lambda: (fake, fake), name="duplicate-words")
    report = api.filter_edits(db, manuscript, "solo.md")
    by_name = {r["filter"]: r for r in report["runs"]}
    check("P3b every active run reports its OWN transport and its own "
          "forms-out count",
          by_name["duplicate-words"]["mode"] == "doc"
          and by_name["duplicate-words"]["forms_out"] == 1
          and by_name["second-filter"]["mode"] is None
          and by_name["second-filter"]["forms_out"] == 0, str(report["runs"]))
    check("P3b the collapsed `mode` is still None when the runs disagree "
          "— that value was never wrong, it was just not enough",
          report["mode"] is None)
    check("P3b ...and the transport note FIRES anyway, naming which "
          "filter's forms are in the Doc. Suppressing it here was the "
          "bug: chat would have offered to apply what is already sitting "
          "in the author's Doc",
          report["transport_note"] is not None
          and "duplicate-words (1)" in report["transport_note"]
          and "filter resolve solo.md" in report["transport_note"],
          str(report["transport_note"]))
    check("P3b ...and it says the OTHER run's proposals are still "
          "ordinary triage, so the note narrows chat's behaviour instead "
          "of freezing the whole essay",
          "still yours to triage as usual"
          in (report["transport_note"] or ""), report["transport_note"])


def _status_can_reach_the_tab(root: Path) -> None:
    """P3d / design step 9 — `filter status` carries the tab URL for a
    doc-mode run with forms out, and says that its orphan scan covers
    the local road only.

    Both are about the same honesty: on the Doc road the forms are
    somewhere this shell cannot show, and a clean byte scan must not read
    as a clean bill of health (RISK-1)."""
    import authorlm.gdocs as _gd

    print("P3d: status reaches the tab, and admits what it cannot see:")

    ws = root / "status-ws"
    db, manuscript, ms, fake = _doc_run(root, "status-ws",
                                        {2: "TWO AS PROPOSED."})
    before = api.filter_status(db, manuscript, "solo.md")["runs"][0]
    check("before the push there is no tab URL — nothing is out there to "
          "look at, and a link to an unmarked tab is noise",
          before["tab_url"] is None and before["mode"] is None)
    check("...and no orphan-scan caveat either: this manuscript has "
          "never been on the Doc road, so the byte scan really is the "
          "whole truth",
          api.filter_status(db, manuscript,
                            "solo.md")["orphan_scan_note"] is None)

    api.filter_push(db, manuscript, {}, "solo.md",
                    services=lambda: (fake, fake))
    report = api.filter_status(db, manuscript, "solo.md")
    row = report["runs"][0]
    check("P3d a doc-mode run with forms out carries its TAB URL, read "
          "from the stored mapping — no network and no credentials, "
          "which is what makes it safe to put in a status verb",
          row["tab_url"] == "https://docs.google.com/document/d/doc-fake"
                            "/edit?tab=tab-2", str(row["tab_url"]))
    check("P3d ...and the orphan scan says out loud that it covers the "
          "LOCAL road only, so a clean byte scan is not read as a clean "
          "bill of health (RISK-1)",
          report["orphan_scan_note"] is not None
          and "covers the LOCAL road only" in report["orphan_scan_note"]
          and "solo.md" in report["orphan_scan_note"],
          str(report["orphan_scan_note"]))
    check("P3d ...and it names the documented recovery, because this is "
          "a cost that is mitigated by instructions rather than detected",
          "doc push <essay>" in (report["orphan_scan_note"] or ""),
          report["orphan_scan_note"])

    saved = (_gd.get_service, _gd.get_docs_service)
    _gd.get_service = lambda *a, **k: fake
    _gd.get_docs_service = lambda *a, **k: fake
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli_main(["--workspace", str(ws), "filter", "status",
                      "solo.md"])
        printed = out.getvalue()
    finally:
        _gd.get_service, _gd.get_docs_service = saved
    check("P3d the CLI prints both — the author can click through to the "
          "tab from the shell, and is told what the scan cannot see",
          "?tab=tab-2" in printed
          and "covers the LOCAL road only" in printed, printed)


def _a_second_run_may_not_push_into_the_same_tab(root: Path) -> None:
    """P3c / Q-1, ruled: refuse EARLY and BY NAME when another producer's
    forms are already in this essay's tab.

    The push's composed drift check runs against LOCAL, which knows
    nothing about the tab, so a second push would mark a tab already
    carrying the first run's forms. It fails regardless —
    `push_doc`'s `forms_pending` refuses the levelling push from inside
    `write_pending_forms` — so the ruling is about WHERE the author meets
    the refusal and whether it names the filter they know."""
    print("P3c / Q-1: a second run pushing into an occupied tab:")

    db, manuscript, ms, fake = _two_runs_one_essay(root, "occupied-tab-ws")
    mid = manuscript["id"]
    api.filter_push(db, manuscript, {}, "solo.md",
                    services=lambda: (fake, fake), name="duplicate-words")
    staged = api.filter_edits(db, manuscript, "solo.md")
    api.filter_triage(db, manuscript, "solo.md",
                      [{"item": str(i["n"]), "verdict": "accept"}
                       for i in staged["items"] if i["state"] == "proposed"])
    bodies_before = len(fake.bodies)
    tab_before = fake.tab_text("solo.md")
    raised = None
    try:
        api.filter_push(db, manuscript, {}, "solo.md",
                        services=lambda: (fake, fake), name="second-filter")
    except ValueError as err:
        raised = str(err)
    check("P3c the second run's push is refused EARLY, naming the filter "
          "whose forms are already in the tab and the verb that ends its "
          "pause — not a row id, and not three functions deep in "
          "push_doc's own guard",
          raised is not None and "'duplicate-words'" in raised
          and "already in solo.md's tab" in raised
          and "filter resolve solo.md" in raised, raised)
    check("P3c ...and it says WHY, which is the part a warning could not "
          "carry: two producers' forms in one tab cannot be told apart "
          "at resolve, because the join is the old text",
          raised is not None
          and "cannot be told apart at resolve" in raised, raised)
    check("P3c ...and NOTHING moved: no request body was formed and the "
          "tab is byte-identical, so the refusal really is early rather "
          "than a rollback after a half-done write",
          len(fake.bodies) == bodies_before
          and fake.tab_text("solo.md") == tab_before,
          str(len(fake.bodies) - bodies_before))
    check("P3c ...and the second run's own verdicts are untouched — a "
          "refusal is not a withdrawal",
          all(t["state"] == "accepted" for t in api._run_threads(
              db, mid, dict(db.one(
                  "SELECT * FROM filter_runs WHERE manuscript_id = ? AND "
                  "filter = 'second-filter'", (mid,))))))


def _the_hint_after_an_unmark(root: Path) -> None:
    """P3a — the resolve's "the tab still shows the marks" line must be
    keyed on whether THIS settle read the tab, not on the run's mode.

    `filter unmark` deliberately does not clear the mode (§2.1), so a run
    whose forms were taken back is still `mode='doc'` while its tab has
    just been rebuilt CLEAN. Keying the hint on the mode sent the author
    to look at struck-and-green text that is not there, and at a `doc
    push` with nothing to clear."""
    print("P3a: the resolve hint after the forms were taken back:")

    db, manuscript, ms, fake = _doc_run(root, "hint-ws",
                                        {2: "TWO AS PROPOSED."})
    api.filter_push(db, manuscript, {}, "solo.md",
                    services=lambda: (fake, fake))
    api.filter_unmark(db, manuscript, "solo.md", force=True,
                      services=lambda: (fake, fake))
    check("the tab really was rebuilt clean — otherwise the hint would "
          "be TRUE and this test asserts nothing (§14.8)",
          "<<" not in fake.tab_text("solo.md"), fake.tab_text("solo.md"))
    settled = api.filter_resolve(db, manuscript, {}, "solo.md",
                                services=lambda: (fake, fake))
    check("P3a the resolve does NOT claim the tab still shows the marks — "
          "it read the FILE, and the tab it would be talking about was "
          "rebuilt clean one verb ago",
          settled["tab_still_marked"] is False,
          str({"hint": settled["tab_still_marked"],
               "mode": settled["mode"]}))
    check("P3a ...and the run's mode is STILL 'doc' at that moment, "
          "which is exactly why keying the hint on the mode was wrong",
          settled["mode"] == "doc")
    check("P3a the hint still fires on a resolve that really did read the "
          "tab — the fix narrows it, it does not delete it",
          _a_real_doc_settle_still_hints(root))


def _a_real_doc_settle_still_hints(root: Path) -> bool:
    db, manuscript, ms, fake = _doc_run(root, "hint-ws-2",
                                        {2: "TWO AS PROPOSED."})
    api.filter_push(db, manuscript, {}, "solo.md",
                    services=lambda: (fake, fake))
    out = api.filter_resolve(db, manuscript, {}, "solo.md",
                            services=lambda: (fake, fake))
    return out["tab_still_marked"] is True


def _awkward_new_halves_through_the_doc(root: Path) -> None:
    """F-D13 / F-D15 — the two shapes §4 and §8 flag as UNPROVEN through
    a real Doc: a `{{new}}` half containing a blank line, and one
    containing pandoc footnote syntax.

    What can be asserted hermetically is asserted here. What CANNOT is
    said out loud rather than faked: whether GOOGLE's markdown exporter
    re-emits surgically INSERTED `[^3]` unchanged is a property of
    Google's exporter, and a double that round-trips it by construction
    would be the vacuous assertion §15.22 already recorded once. The
    live probe is named below and deliberately not run — the suite makes
    no network call and pops no OAuth window."""
    print("§4 / RISK-2 / RISK-3: awkward {{new}} halves through the Doc:")

    # --- F-D13: a `new` half split by a blank line -------------------
    db, manuscript, ms, fake = _doc_run(
        root, "split-ws",
        {2: "The first half of a split replacement.\n\n"
            "And the second half, a whole paragraph later."})
    mid = manuscript["id"]
    result = api.filter_push(db, manuscript, {}, "solo.md",
                             services=lambda: (fake, fake))
    thread = api._run_threads(db, mid, _run_row(db, mid))[0]
    landed = thread["state"] == "written"
    settled_ok = None
    if landed:
        settle = api.filter_resolve(db, manuscript, {}, "solo.md",
                                   services=lambda: (fake, fake))
        settled_ok = settle["forms"] == 1
    check("F-D13 a `new` half containing a blank line either round-trips "
          "verbatim (written, and the resolve finds its form) OR fails "
          "the read-back and stays `accepted` — the disjunction is the "
          "claim, because there is no third outcome in which a thread is "
          "`written` but its form is unfindable at resolve",
          (landed and settled_ok) or
          (not landed and thread["state"] == "accepted"
           and result["written"] == 0),
          str({"state": thread["state"], "written": result["written"],
               "settled_ok": settled_ok}))
    if landed:
        final = (ms / "solo.md").read_text()
        check("F-D13 ...and on the branch that DID land, the finished "
              "essay carries both halves and no marker",
              "The first half of a split replacement." in final
              and "And the second half, a whole paragraph later." in final
              and "<<" not in final, final)

    # --- F-D15: pandoc footnote syntax inside a `new` half -----------
    db2, ms2, msdir2, fake2 = _doc_run(
        root, "footnote-ws",
        {2: "Alpha opens the essay, as the ledger records.[^3]"})
    api.filter_push(db2, ms2, {}, "solo.md",
                    services=lambda: (fake2, fake2))
    wire = "\n".join(fake2.bodies)
    check("F-D15 the footnote marker reaches the wire LITERALLY — the "
          "surgical writer builds `insertText` requests carrying literal "
          "characters and never passes them through `escape_footnotes`, "
          "which is right, because nothing on that path parses markdown",
          "[^3]" in wire and "\\\\[^3]" not in wire,
          [b for b in fake2.bodies if "[^3]" in b][:1])
    settle2 = api.filter_resolve(db2, ms2, {}, "solo.md",
                                services=lambda: (fake2, fake2))
    final2 = (msdir2 / "solo.md").read_text()
    check("F-D15 ...and it survives export→settle byte-identically "
          "through this bridge: no backslash was added and none was "
          "stripped",
          settle2["forms"] == 1 and "as the ledger records.[^3]" in final2
          and "\\[^3]" not in final2, final2)
    print("  SKIPPED (live probe, no OAuth in this suite): whether "
          "GOOGLE's markdown exporter re-emits a surgically INSERTED "
          "`[^3]` unchanged. `normalize_markdown`'s _ESCAPE is what "
          "makes the PUSHED round trip byte-clean; the inserted case is "
          "outside that proof (design §4, RISK-2). Run it by hand: "
          "`filter push` an essay whose new half carries [^3], then "
          "`filter resolve`, and compare. If it fails, the disposition is "
          "a per-thread refusal at push naming the local road — NOT an "
          "escape, which would re-create §15.22's pipe-escape failure in "
          "a new place.")
    print("  SKIPPED (live probe, same reason): whether a blank line "
          "inside a {{new}} half survives Docs' own paragraph handling "
          "(RISK-3). The read-back proof is the arbiter either way, and "
          "the disjunction above is what holds regardless of which side "
          "the real Doc falls on.")


def _the_doc_road_through_the_cli(root: Path) -> None:
    """Scenario FD — the whole doc road through the real CLI against the
    fake Drive: push → (the author rewords one half in the tab) →
    settle."""
    import authorlm.gdocs as _gd

    print("Scenario FD — the doc road, end to end through the CLI:")

    ws = root / "fd-ws"
    db, manuscript, ms, fake = _doc_run(
        root, "fd-ws", {2: "TWO AS PROPOSED.", 4: "FOUR AS PROPOSED."})
    mid = manuscript["id"]
    original = (ms / "solo.md").read_bytes()
    saved = (_gd.get_service, _gd.get_docs_service)
    _gd.get_service = lambda *a, **k: fake
    _gd.get_docs_service = lambda *a, **k: fake
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli_main(["--workspace", str(ws), "filter", "push", "solo.md"])
        pushed = out.getvalue()
        check("FD push reports the count and the tab URL, and tells the "
              "author how to rule in the tab — all three verdicts, "
              "because untriaged proposals go out too now — and that "
              "the disk keeps the old text",
              "2 change(s) written into solo.md's tab" in pushed
              and "docs.google.com/document/d/doc-fake" in pushed
              and "leave a change alone to take it" in pushed
              and "empty its green half to turn it down" in pushed
              and "reword the green half to make it yours" in pushed
              and "still holds solo.md" in pushed, pushed)
        status = io.StringIO()
        with contextlib.redirect_stdout(status):
            cli_main(["--workspace", str(ws), "filter", "status",
                      "solo.md"])
        check("FD `filter status` shows the transport and the forms-out "
              "count", "transport: doc; 2 form(s) out" in status.getvalue(),
              status.getvalue())
        check("FD the file on disk is still byte-identical",
              (ms / "solo.md").read_bytes() == original)

        _reword_in_tab(fake, "{{TWO AS PROPOSED.}}",
                       "{{Two, as I would rather have it.}}")

        out2 = io.StringIO()
        with contextlib.redirect_stdout(out2):
            cli_main(["--workspace", str(ws), "filter", "resolve",
                      "solo.md"])
        settled = out2.getvalue()
        final = (ms / "solo.md").read_text()
        check("FD the resolve finalizes both, says how many were in the "
              "author's own wording, and says the tab keeps its marks "
              "until the next doc push",
              "Finalized: 2 change(s) made final in solo.md, 1 of them in "
              "your wording rather than mine." in settled
              and "Your next 'doc push solo.md' clears them." in settled,
              settled)
        check("FD ...and it still says nothing was filed against a goal",
              "Nothing here was filed against any of your goals" in settled)
        check("FD the finished essay carries the author's wording and no "
              "marker",
              "Two, as I would rather have it." in final
              and "FOUR AS PROPOSED." in final and "<<" not in final, final)
        versions = db.all(
            "SELECT * FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no DESC LIMIT 1", (mid,))
        check("FD the resolve was collected — the version history has the "
              "finished essay", loads(versions[0]["files"], {}).get(
                  "solo.md", "").find("Two, as I would rather have it.") >= 0)
        settle_ids = [r["id"] for r in db.all(
            "SELECT id FROM editorial_transitions WHERE manuscript_id = ? "
            "AND version_after = ?", (mid, versions[0]["id"]))]
        attached: set[str] = set()
        for r in db.all("SELECT transition_ids FROM editorial_episodes "
                        "WHERE manuscript_id = ?", (mid,)):
            attached.update(loads(r["transition_ids"], []))
        check("FD ...and under NO episode: a filter pass is hygiene, and "
              "filing its transitions against whatever goal happened to "
              "be open is the mis-attribution §15.17 records",
              settle_ids and not any(t in attached for t in settle_ids),
              str({"settle": settle_ids, "attached": sorted(attached)}))
    finally:
        _gd.get_service, _gd.get_docs_service = saved


TEMPLATE_ESSAY = (
    "# On Templating\n\n"
    "A template engine substitutes: {{title}} becomes the page's title, "
    "and {{author.name}} becomes mine.\n\n"
    "In set-builder notation we write {{a, b}} for the pair, which is "
    "not a template at all.\n\n"
    "The point is that a brace is only a brace.\n")


def _braces_are_the_authors(root: Path) -> None:
    """AQ/FU-A — the local canonicalizer is the REPLACE form ONLY.

    `strip_pending` is the DOC canonicalizer: in a Doc tab nothing put
    `{{…}}` there but AuthorLM, so a bare one is a critique-pass
    insertion and deleting it is ratified. Pointed at LOCAL files that
    rule inverts — `{{title}}`, `{{a, b}}`, a Handlebars sample in an
    essay ABOUT templating are the author's own prose — and the broad
    strip deleted every one of them from everything the system observes,
    silently, because once they were gone there was nothing left to warn
    about. `is_marked` tripped on them too, refusing the file's Doc push
    forever with a message about a resolve that does not exist.

    A filter never stages an insertion (`insert` is refused by the
    recorder), so narrowing loses nothing this seam exists for."""
    from authorlm import revisions as _rev
    from authorlm import staging as _staging
    from authorlm import threads as _th

    print("AQ/FU-A: a brace the AUTHOR wrote is not this grammar's:")

    ws = root / "braces-ws"
    ms = ws / "book"
    ms.mkdir(parents=True)
    (ms / "templating.md").write_text(TEMPLATE_ESSAY)
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms)])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)

    observed = _rev.read_manuscript_files(ms)["templating.md"]
    check("an essay containing {{title}} and {{a, b}} reads back BYTE "
          "IDENTICAL through the observation seam — the author's braces "
          "are prose, not machinery, and nothing in the system may eat "
          "them",
          observed == TEMPLATE_ESSAY, repr(observed))
    check("...and the narrow strip warns about nothing, because there is "
          "nothing wrong: a warning on every templating example would be "
          "noise that trains the author to ignore the warnings that "
          "matter",
          _th.strip_replacements(TEMPLATE_ESSAY)[1] == [],
          _th.strip_replacements(TEMPLATE_ESSAY)[1])
    check("staging.is_marked says NO — the file is not mid-settle",
          not _staging.is_marked(TEMPLATE_ESSAY))

    meta = gdocs._mapping(db, manuscript)
    links = meta.setdefault("gdocs", {})
    links["_master_id"] = "doc-fake"
    links["templating.md"] = {"tab_id": "tab-1", "checked_out": False}
    gdocs._save_mapping(db, manuscript, meta)
    drive = _RecordingDrive(title="templating.md")
    raised = None
    try:
        gdocs.push_doc(db, manuscript, "templating.md", service=drive,
                       docs_service=drive)
    except LookupError as err:
        raised = str(err)
    check("doc push does NOT refuse it for being mid-settle — a file the "
          "author fills with braces must stay pushable forever",
          raised is None or "mid-settle" not in raised, raised)
    check("...and it really pushed: the author's braces went to the wire "
          "intact, which is what the narrowing is FOR",
          "{{title}}" in drive.sent and "{{a, b}}" in drive.sent,
          drive.sent[:300])

    # And the narrowing did not cost the guard its teeth: add one real
    # replace form and every one of those answers flips.
    marked = TEMPLATE_ESSAY.replace(
        "The point is that a brace is only a brace.",
        _th.render_pending("The point is that a brace is only a brace.",
                           "A brace is only a brace."))
    (ms / "templating.md").write_text(marked)
    check("a REAL <<old>>{{new}} form still marks the file",
          _staging.is_marked(marked))
    check("...and still canonicalizes to the OLD half, leaving the "
          "author's own braces exactly where they were",
          _rev.read_manuscript_files(ms)["templating.md"]
          == TEMPLATE_ESSAY,
          repr(_rev.read_manuscript_files(ms)["templating.md"]))
    drive2 = _RecordingDrive(title="templating.md")
    raised2 = None
    try:
        gdocs.push_doc(db, manuscript, "templating.md", service=drive2,
                       docs_service=drive2)
    except LookupError as err:
        raised2 = str(err)
    check("...and still refuses the push, naming filter unmark",
          raised2 is not None and "mid-settle" in raised2
          and "filter unmark" in raised2, raised2)
    check("...and NOTHING reached the wire — the outcome, not the "
          "exception: no request body was ever formed, let alone one "
          "carrying a marker",
          drive2.bodies == [], drive2.sent[:300])
    text, _warn = _staging.unmark(ms / "templating.md")
    check("filter unmark restores the essay byte for byte, braces and "
          "all — a recovery that damaged the file it recovered would be "
          "no recovery",
          text == TEMPLATE_ESSAY
          and (ms / "templating.md").read_text() == TEMPLATE_ESSAY,
          repr(text))


def _pull_writes_when_unmarked(db, manuscript, ms: Path, fake) -> bool:
    """Discrimination for the pull guard: unmark the file and prove the
    very same stubbed pull overwrites it. Without this the assertion
    above would pass just as well against a pull that never writes."""
    from authorlm import threads as _th

    (ms / "solo.md").write_text(
        _th.strip_pending((ms / "solo.md").read_text())[0])
    gdocs.pull_doc(db, manuscript, "solo.md", service=fake, force=True,
                   with_comments=False, docs_service=fake)
    return ("A sentence the Doc has and local does not."
            in (ms / "solo.md").read_text())


class Stub(http.server.BaseHTTPRequestHandler):
    """Summarizer → canned; editor → scripted responses (a queue so a
    contract violation can be followed by a corrected reply)."""
    editor_replies: list[dict] = []
    editor_calls: list[str] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        system = body["messages"][0]["content"]
        user = body["messages"][-1]["content"]
        if "THE UNIT: " in user:
            unit = user.split("THE UNIT: ", 1)[1].split("\n", 1)[0]
            content = f"MOVES: summary of {unit}."
        elif "Essay editor" in system:
            Stub.editor_calls.append(user)
            content = json.dumps(Stub.editor_replies.pop(0))
        else:
            content = json.dumps({"concepts": [], "links": [], "aliases": []})
        payload = json.dumps({
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


ESSAY = ("# Alpha\n\nFirst paragraph of alpha, plainly stated.\n\n"
         "Second paragraph carries the argument forward.\n\n"
         "Third paragraph closes the essay.\n")


def good_reply(paragraphs):
    return {
        "paragraphs": [
            {"n": 1, "echo": passes.echo_of(paragraphs[0]), "action": "keep"},
            {"n": 2, "echo": passes.echo_of(paragraphs[1]), "action": "replace",
             "new": "First paragraph of alpha, stated with care.",
             "why": "serves the clarity intent", "intent_id": "di-x"},
            {"n": 3, "echo": passes.echo_of(paragraphs[2]), "action": "keep"},
            {"n": 4, "echo": passes.echo_of(paragraphs[3]), "action": "replace",
             "new": "Third paragraph closes the essay decisively.",
             "why": "the ending should land", "intent_id": None},
        ],
        "insertions": [{"after": 2, "new": "A bridging paragraph, new.",
                        "why": "the intent asks for a bridge",
                        "intent_id": "di-x"}],
        "suggestions": [{"kind": "glossary", "text": "Add 'alpha' to a glossary.",
                         "intent_id": None}],
    }


CANONICAL_DICT = (
    "# Pronunciations\n\n"
    "How they are said.\n\n"
    "| Term | Say it | Note |\n"
    "| --- | --- | --- |\n"
    "| anattā | uh-NUT-taa | Pali; the second a is long |\n"
    "| Nāgārjuna | naa-GAAR-ju-na |  |\n"
    "| Śūnyatā | shoon-yuh-TAA | Sanskrit |\n")


def pronunciation_purity() -> None:
    """P1–P8 — `pronunciations.py`, the pure layer (§15.22).

    No database, no manuscript, no model: text in, text out. The file is
    the AUTHOR'S, so every assertion here is about what the module
    refuses to touch as much as about what it reads."""
    import unicodedata

    from authorlm import pronunciations as pron
    from authorlm.gdocs import normalize_markdown

    print("\npronunciation dictionary — the pure layer:")

    rows, warns = pron.parse(CANONICAL_DICT)
    check("P1 a canonical table parses to its rows, in file order",
          [r["term"] for r in rows] == ["anattā", "Nāgārjuna", "Śūnyatā"]
          and rows[0]["say"] == "uh-NUT-taa"
          and rows[0]["note"] == "Pali; the second a is long"
          and rows[1]["note"] == "" and not warns, f"{rows} {warns}")

    # P2 — everything Docs actually emits: alignment colons, padded
    # cells, a bolded header, an NBSP, a backslash-escaped pipe, a
    # trailing empty cell, a trailing empty row, and a fourth column.
    docsy = (
        "# Pronunciations\n\n"
        "How they are said.\n\n"
        "| **Term** | **Say it** | **Note** | |\n"
        "| :--- | :---: | ---------- | --- |\n"
        "|  anattā  |  uh-NUT-taa  | Pali; the second a is long | |\n"
        "| Nāgārjuna | naa-GAAR-ju-na |  | |\n"
        "| Śūnyatā | shoon-yuh-TAA | Sanskrit | |\n"
        "|  |  |  | |\n")
    docsy_rows, docsy_warns = pron.parse(docsy)
    check("P2 a Docs-shaped export of the same table parses to "
          "BYTE-IDENTICAL rows — colons, padding, bold, NBSP, a fourth "
          "column, a trailing empty row",
          docsy_rows == rows and not docsy_warns,
          f"{docsy_rows}\n{rows}\n{docsy_warns}")
    # A pipe is the table's own column separator and does not survive
    # the Doc bridge in ANY form: normalize_markdown strips exactly the
    # backslash an escape would write, and push_doc normalizes and writes
    # back, so an escaped pipe is unescaped on the author's own disk and
    # the row reparses with its cells shifted one to the left. The row
    # COUNT is unchanged by that, so §2.6's zero-rows guard never sees
    # it — a resolved row silently rewritten, which is the one thing this
    # file exists to make impossible. So the row is SKIPPED and named.
    pipe_table = ("| Term | Say it | Note |\n| --- | --- | --- |\n"
                  "| a\\|b | ay-bee | the \\| is literal |\n"
                  "| anattā | uh-NUT-taa | Pali |\n")
    escaped, esc_warns = pron.parse(pipe_table)
    check("P7 an author-typed ESCAPED pipe row is skipped and named, "
          "never read as shifted cells — and the resolved row beside it "
          "survives untouched",
          escaped == [{"term": "anattā", "say": "uh-NUT-taa",
                       "note": "Pali"}]
          and len(esc_warns) == 1 and "a|b" in esc_warns[0]
          and "cannot be read as one three-column row" in esc_warns[0],
          f"{escaped}\n{esc_warns}")
    pushed = normalize_markdown(pipe_table)
    shifted, shift_warns = pron.parse(pushed)
    check("P7 ...and after push_doc's normalize-and-write-back has "
          "stripped the backslash, the SAME row is skipped for the "
          "shift instead — the two signatures of one fault",
          shifted == escaped and len(shift_warns) == 1
          and "its cells are shifted" in shift_warns[0],
          f"{shifted}\n{shift_warns}")
    check("P7 a table that is ALL pipe rows parses to NOTHING, so the "
          "pull's zero-rows guard refuses it — the two guards compose "
          "rather than fight",
          pron.parse("| Term | Say it | Note |\n| --- | --- | --- |\n"
                     "| a|b | c | d |\n")[0] == [], "")
    check("P7 a trailing EMPTY cell is still tolerated — that is what "
          "Docs actually emits, and it means nothing",
          pron.parse("| Term | Say it | Note |\n| --- | --- | --- |\n"
                     "| anattā | uh-NUT-taa | Pali | |\n")[0]
          == [{"term": "anattā", "say": "uh-NUT-taa", "note": "Pali"}], "")
    try:
        raised_pipe = ""
        pron.render_row({"term": "a|b", "say": "x"})
    except ValueError as err:
        raised_pipe = str(err)
    check("P7 and render_row REFUSES a pipe rather than escaping one — "
          "nothing this system writes can carry one, which is what makes "
          "the fixed-point check below true and keeps proposals.adopt the "
          "only writer of the file",
          "does not survive the Doc bridge" in raised_pipe, raised_pipe)

    prosy = (CANONICAL_DICT
             + "\nAuthor's own note below, with | a pipe in it.\n")
    prosy_rows, _ = pron.parse(prosy)
    check("P3 prose before and after the table is ignored, never read as "
          "rows and never rewritten",
          prosy_rows == rows, prosy_rows)

    nfd = unicodedata.normalize("NFD", "Śūnyatā")
    check("P4 the fixture really does differ in bytes (NFC vs NFD)",
          nfd != "Śūnyatā" and pron.key(nfd) == pron.key("Śūnyatā"))
    dup = (CANONICAL_DICT
           + f"| {nfd} | SHOON-ya-taa | a second, decomposed |\n"
           + "| śūnyatā | shoo-nya-TAA | a third, lower case |\n")
    dup_rows, dup_warns = pron.parse(dup)
    check("P4 case and Unicode composition collapse to ONE term; the "
          "FIRST row wins and the duplicates are warned about, never "
          "deleted",
          len(dup_rows) == 3 and dup_rows[2]["say"] == "shoon-yuh-TAA"
          and len(dup_warns) == 2
          and all("appears more than once" in w for w in dup_warns)
          and all("nothing was deleted" in w for w in dup_warns),
          f"{dup_rows}\n{dup_warns}")

    crlf = CANONICAL_DICT.replace("\n", "\r\n")
    added = pron.add_row(crlf, {"term": "Böhme", "say": "BUR-muh",
                                "note": "German"})
    check("P5 add_row on a CRLF file KEEPS CRLF — not one LF is "
          "introduced (insert_toc_entry's hard-won rule, and a Doc round "
          "trip is a recurring source of CRLF)",
          added.count("\n") == added.count("\r\n"),
          repr(added[-60:]))
    check("P5 and it rewrites no line it did not add — the old bytes are "
          "a literal PREFIX of the new ones",
          added.startswith(crlf) and added == crlf + "| Böhme | BUR-muh "
          "| German |\r\n", repr(added[len(crlf):]))
    check("P5 add_row is idempotent on a term already present, in any "
          "casing or composition",
          pron.add_row(added, {"term": "böhme", "say": "different"})
          == added)

    seeded = pron.add_row("", {"term": "anattā", "say": "uh-NUT-taa",
                               "note": "Pali"})
    check("P6 add_row on a missing file produces the seed: title, the "
          "prose paragraph, the header row, the delimiter, one row",
          seeded.startswith(pron.TITLE) and pron.PREAMBLE in seeded
          and pron.HEADER in seeded and pron.DELIMITER in seeded
          and seeded.endswith("| anattā | uh-NUT-taa | Pali |\n")
          and pron.parse(seeded)[0] == [
              {"term": "anattā", "say": "uh-NUT-taa", "note": "Pali"}],
          repr(seeded))

    written = pron.render(rows)
    check("P7 normalize_markdown is a NO-OP on what we write, so an "
          "accepted row never dirties the file on the next doc push",
          normalize_markdown(written) == written,
          repr(normalize_markdown(written)))

    unfinished = ("| Term | Say it | Note |\n| --- | --- | --- |\n"
                  "| Chāndogya |  | started, not finished |\n")
    un_rows, un_warns = pron.parse(unfinished)
    check("P8 a term with an empty pronunciation is KEPT as a row and "
          "warned about — the author is working on it",
          len(un_rows) == 1 and un_rows[0]["say"] == ""
          and len(un_warns) == 1 and "Chāndogya" in un_warns[0],
          f"{un_rows} {un_warns}")
    check("P8 and it is in `terms`, so it suppresses proposals for that "
          "term exactly as a finished row does",
          pron.terms(unfinished) == ["Chāndogya"], pron.terms(unfinished))

    try:
        raised = ""
        pron.render_row({"term": "a\nb", "say": "x"})
    except ValueError as err:
        raised = str(err)
    check("a newline in any field is refused — a row is one line",
          "one line" in raised, raised)


def main_test() -> None:
    pronunciation_purity()
    root = Path(tempfile.mkdtemp(prefix="authorlm-passes-"))
    server = http.server.HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ws = root / "ws"
        ms = ws / "book"
        ms.mkdir(parents=True)
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "part.md"\n\n'
            '[[chapter]]\nfile = "alpha.md"\nparent = "part.md"\n\n'
            '[[chapter]]\nfile = "beta.md"\nparent = "part.md"\n')
        (ms / "part.md").write_text("# Part\n\nThe part opener.\n")
        (ms / "alpha.md").write_text(ESSAY)
        (ms / "beta.md").write_text("# Beta\n\nBeta text.\n")
        (ws / ".authorlm").mkdir()
        (ws / ".authorlm" / "config.toml").write_text(
            "[llm]\nenabled = true\nprovider = \"openai\"\n"
            f"base_url = \"http://127.0.0.1:{server.server_port}/v1\"\n"
            "model = \"general\"\n\n"
            "[critique]\neditor_model = \"frontier\"\n"
            "summarizer_model = \"cheap\"\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms)])
        db = api.open_db(str(ws))
        manuscript = api.get_manuscript(db)
        mid = manuscript["id"]
        import tomllib
        config = tomllib.loads((ws / ".authorlm" / "config.toml").read_text())

        print("scope chain & gate:")
        check("scope chain = file, toc ancestors, manuscript-wide",
              passes.scope_chain(manuscript, "alpha.md")
              == ["alpha.md", "part.md", None])
        # The tier is what makes `chapter` a group without a group entity
        # (design-intent-scope §1.1). It is pure — an index into the
        # chain — and it is what write start derives a member record from.
        check("scope_tier: the file itself is the `file` tier",
              passes.scope_tier(manuscript, "alpha.md", "alpha.md") == "file")
        check("scope_tier: a toc ancestor is the `chapter` tier",
              passes.scope_tier(manuscript, "alpha.md", "part.md") == "chapter")
        check("scope_tier: no scope at all is manuscript-wide",
              passes.scope_tier(manuscript, "alpha.md", None) == "manuscript")
        check("scope_tier: a scope naming a file this one is NOT under is "
              "no tier at all — not in scope, and derivation must not "
              "silently promote it to manuscript-wide",
              passes.scope_tier(manuscript, "alpha.md", "beta.md") is None)
        check("scope_specificity orders file over chapter over manuscript, "
              "and puts an out-of-chain scope last",
              [passes.scope_specificity(manuscript, "alpha.md", s)
               for s in ("alpha.md", "part.md", None, "beta.md")]
              == [0, 1, 2, 3])
        crit_src = db.source("critic", "Test Critic", "test")
        critique.import_manifest(db, mid, {
            "source": {"name": "Test Critic", "detail": "test"},
            "items": [
                {"kind": "intent", "unit": "Alpha", "ordinal": 1,
                 "text": "Clarify the first paragraph.", "scope": "alpha.md"},
                {"kind": "intent", "unit": "Part", "ordinal": 1,
                 "text": "Tighten every essay in the part.", "scope": "part.md"},
                {"kind": "intent", "unit": "Beta", "ordinal": 1,
                 "text": "Beta-only work.", "scope": "beta.md"},
                {"kind": "intent", "unit": "Global", "ordinal": 1,
                 "text": "Book-wide rule.", "scope": None},
            ]})
        gate = passes.preflight(db, manuscript, "alpha.md")
        check("gate sees proposed intents in the scope chain, not siblings",
              not gate["clear"]
              and {i["statement"] for i in gate["proposed_intents"]}
              == {"Clarify the first paragraph.",
                  "Tighten every essay in the part.", "Book-wide rule."})
        p = passes.ensure_pass(db, mid, crit_src)
        try:
            passes.build_context(db, manuscript, "alpha.md", p)
            blocked = False
        except RuntimeError as err:
            blocked = "preflight" in str(err)
        check("build_context refuses while the gate is dirty", blocked)
        # Accept the alpha + part items, reject the global one → gate clear.
        pend = critique.pending(db, mid)["intents"]
        for it in pend:
            if it["scope"] in ("alpha.md", "part.md"):
                critique.accept_intent(db, mid, it)
            elif it["scope"] is None:
                critique.reject_intent(db, mid, it, "not now")
        check("gate clears once this essay's chain is triaged (beta's item "
              "is irrelevant)",
              passes.preflight(db, manuscript, "alpha.md")["clear"])

        print("summaries gate:")
        try:
            passes.build_context(db, manuscript, "alpha.md", p)
            blocked = False
        except RuntimeError as err:
            blocked = "missing summaries" in str(err)
        check("build_context refuses when summaries are missing", blocked)
        sums.rebuild(db, manuscript, sums.summarizer_llm(config))
        ctx = passes.build_context(db, manuscript, "alpha.md", p)
        check("context: 4 paragraphs, 2 active intents in scope, before/after",
              len(ctx["paragraphs"]) == 4 and len(ctx["intents"]) == 2
              and [e["file"] for e in ctx["before"]] == ["part.md"]
              and [e["file"] for e in ctx["after"]] == ["beta.md"])
        msg = passes.render_user_message(ctx)
        check("user message numbers paragraphs and carries the contract blocks",
              "[1] # Alpha" in msg and "=== INTENTS ===" in msg
              and "Tighten every essay" in msg and "Beta-only work" not in msg)

        print("stale summary genuinely blocks the edit pass (source_hash "
              "mismatch — 'a lie about the text' — not just a missing row):")
        beta_row = db.one(
            "SELECT * FROM essay_summaries WHERE manuscript_id = ? AND "
            "file = ?", (mid, "beta.md"))
        db.update("essay_summaries", beta_row["id"], {"source_hash": "deadbeef"})
        check("beta.md now reads stale by source_hash",
              next(r for r in sums.status(db, manuscript) if r["file"] == "beta.md")
              ["state"] == "stale")
        try:
            passes.build_context(db, manuscript, "alpha.md", p)
            blocked, err_msg = False, ""
        except RuntimeError as err:
            blocked, err_msg = True, str(err)
        check("build_context refuses to run: the guard is real, not "
              "decorative — a stale summary in the before/after context "
              "blocks the pass exactly as a missing one does",
              blocked and "stale summaries" in err_msg and "beta.md" in err_msg,
              err_msg)
        sums.rebuild_one(db, manuscript, "beta.md", sums.summarizer_llm(config))
        ctx_after_fix = passes.build_context(db, manuscript, "alpha.md", p)
        check("once the stale summary is rebuilt, the guard clears and the "
              "pass runs again",
              ctx_after_fix["after"][0]["state"] == "fresh")

        print("an essay with no toc.toml entry (§12.4 item 3) — the edit "
              "pass cannot place it either, so it refuses rather than "
              "reading the whole book as settled context behind it:")
        (ms / "zeta.md").write_text("# Zeta\n\nNobody put this in the toc.\n")
        sums.rebuild(db, manuscript, sums.summarizer_llm(config))
        try:
            passes.build_context(db, manuscript, "zeta.md", p)
            blocked, err_msg = False, ""
        except LookupError as err:
            blocked, err_msg = True, str(err)
        check("build_context on the unlisted essay REFUSES — the appended "
              "position is a fallback, not a declaration, and the edit "
              "pass reads the same before/after split the drafting gate "
              "does",
              blocked and "no toc.toml entry" in err_msg, err_msg)
        check("...naming the universally applicable remedy (a toc.toml "
              "entry) FIRST, and marking the write-start remedy as "
              "drafting-only — a critique-pass user must not be handed "
              "advice for a different verb",
              err_msg.index("Add it to toc.toml")
              < err_msg.index("write start")
              and "for that writeup only" in err_msg, err_msg)
        ctx_listed = passes.build_context(db, manuscript, "alpha.md", p)
        check("a LISTED essay still builds its context with the unlisted "
              "file sitting on disk — the refusal is about the target, "
              "not a manuscript-wide freeze",
              [e["file"] for e in ctx_listed["before"]] == ["part.md"]
              and [e["file"] for e in ctx_listed["after"]]
              == ["beta.md", "zeta.md"],
              str(([e["file"] for e in ctx_listed["before"]],
                   [e["file"] for e in ctx_listed["after"]])))
        (ms / "zeta.md").unlink()
        sums.rebuild(db, manuscript, sums.summarizer_llm(config))

        # Force semantics: dirty gate + force → runs, but without the items.
        critique.import_manifest(db, mid, {
            "source": {"name": "Test Critic", "detail": "test"},
            "items": [{"kind": "intent", "unit": "Alpha", "ordinal": 2,
                       "text": "A late unconfirmed item.", "scope": "alpha.md"}]})
        forced = passes.build_context(db, manuscript, "alpha.md", p, force=True)
        check("--force runs WITHOUT unconfirmed items (never with them)",
              forced["forced"] and all(
                  i["statement"] != "A late unconfirmed item."
                  for i in forced["intents"]))
        late = [i for i in critique.pending(db, mid)["intents"]][0]
        critique.reject_intent(db, mid, late, "no")

        print("output contract:")
        paras = ctx["paragraphs"]
        ok = passes.validate_output(good_reply(paras), paras)
        check("valid reply → 2 edits (verbatim old from source), 1 insertion, "
              "1 suggestion",
              len(ok["edits"]) == 2 and ok["edits"][0]["old"] == paras[1]
              and len(ok["insertions"]) == 1 and len(ok["suggestions"]) == 1)
        bad = good_reply(paras)
        bad["paragraphs"][1]["echo"] = "wrong words here now"
        try:
            passes.validate_output(bad, paras)
            raised = False
        except passes.ContractError as err:
            raised = "echo mismatch" in str(err)
        check("echo mismatch discards the whole response", raised)
        short = good_reply(paras)
        short["paragraphs"].pop()
        try:
            passes.validate_output(short, paras)
            raised = False
        except passes.ContractError:
            raised = True
        check("wrong paragraph count is rejected", raised)

        print("run_editor retries on contract violation:")
        Stub.editor_replies = [bad, good_reply(paras)]
        llm = passes.editor_llm(config)
        check("editor model comes from [critique] editor_model",
              llm.model == "frontier")
        result = passes.run_editor(llm, ctx)
        check("first (bad) reply rejected, corrected reply accepted",
              len(Stub.editor_calls) == 2
              and "PREVIOUS RESPONSE WAS REJECTED" in Stub.editor_calls[1]
              and len(result["edits"]) == 2)

        print("staging:")
        out = passes.stage(db, manuscript, p, "alpha.md", result)
        staged = passes.staged_threads(db, mid, "alpha.md")
        check("3 critique threads staged in document order (¶2, insert after "
              "¶2, ¶4)",
              len(staged) == 3
              and [loads(t["metadata"], {})["anchor_paragraph"] for t in staged]
              == [2, 2, 4]
              and staged[1]["proposed_old"] == ""
              and all(t["origin_type"] == "critique" for t in staged))
        check("suggestion filed as a proposed, system-sourced intent with lineage",
              len(out["suggestions"]) == 1
              and out["suggestions"][0]["status"] == "proposed"
              and out["suggestions"][0]["source_id"] == db.source("system")
              and loads(out["suggestions"][0]["metadata"], {})["lineage"]["file"]
              == "alpha.md")
        check("essay state → proposed", passes.essay_state(p, "alpha.md")
              == "proposed")
        check("author-comment open_threads never sees critique threads",
              th.open_threads(db, mid) == [])

        print("verdicts + undo:")
        t_edit, t_ins, t_end = staged
        passes.verdict(db, mid, t_edit, "accept")
        passes.verdict(db, mid, t_ins, "revise", "A bridging paragraph, mine.")
        passes.verdict(db, mid, t_end, "reject", "the ending is fine")
        st = {t["id"]: t for t in passes.staged_threads(db, mid, "alpha.md")}
        check("accept / revise / reject land",
              st[t_edit["id"]]["state"] == "accepted"
              and st[t_ins["id"]]["state"] == "accepted"
              and st[t_ins["id"]]["proposed_new"] == "A bridging paragraph, mine."
              and st[t_end["id"]]["state"] == "rejected")
        passes.verdict(db, mid, st[t_end["id"]], "undo")
        passes.verdict(db, mid, st[t_ins["id"]], "undo")
        st = {t["id"]: t for t in passes.staged_threads(db, mid, "alpha.md")}
        check("undo is free: rejected → proposed; revised → proposed with the "
              "original wording restored",
              st[t_end["id"]]["state"] == "proposed"
              and st[t_ins["id"]]["state"] == "proposed"
              and st[t_ins["id"]]["proposed_new"] == "A bridging paragraph, new.")
        passes.verdict(db, mid, st[t_ins["id"]], "accept")
        evs = db.all("SELECT signal FROM evidence WHERE manuscript_id = ? AND "
                     "evidence_type = 'critique_edit'", (mid,))
        check("every verdict (incl. undo) is evidence",
              {e["signal"] for e in evs}
              >= {"accepted", "revised", "rejected", "undone"})

        print("marked text (diff-write composition):")
        threads = passes.staged_threads(db, mid, "alpha.md")
        marked = passes.compose_marked_text(ESSAY, threads)
        check("accepted replace renders <<old>>{{new}}; insertion renders "
              "{{new}} after its anchor; rejected/proposed untouched",
              "<<First paragraph of alpha, plainly stated.>>{{First paragraph "
              "of alpha, stated with care.}}" in marked
              and "{{A bridging paragraph, new.}}" in marked
              and "Third paragraph closes the essay.\n" in marked
              and "decisively" not in marked)
        drifted = ESSAY.replace("plainly stated", "PLAINLY stated")
        try:
            passes.compose_marked_text(drifted, threads)
            raised = False
        except ValueError as err:
            raised = "drifted" in str(err)
        check("drifted paragraph fails loudly before any Doc write", raised)
        check("strip_pending on the marked text gives back the pristine essay",
              th.strip_pending(marked)[0].strip() == ESSAY.strip())

        print("critique write order (same-anchor insert before replace):")
        # A replace of paragraph n wraps it as <<old>>{{new}}. Writing the
        # replace first makes the subsequent insert locate `old` inside that
        # wrapped form and plant {{insert}} between old and >>, corrupting
        # the Doc. Inserts at the same anchor must precede replaces.
        order = gdocs.pending_write_order([
            {"id": "rep6", "proposed_old": "p6", "metadata":
             json.dumps({"anchor_paragraph": 6})},
            {"id": "ins5", "proposed_old": "", "metadata":
             json.dumps({"anchor_paragraph": 5})},
            {"id": "rep5", "proposed_old": "p5", "metadata":
             json.dumps({"anchor_paragraph": 5})},
            {"id": "ins4", "proposed_old": "", "metadata":
             json.dumps({"anchor_paragraph": 4})},
        ])
        check("higher anchors first; inserts before replaces at same anchor",
              [t["id"] for t in order] == ["rep6", "ins5", "rep5", "ins4"],
              str([t["id"] for t in order]))

        print("resolution (author post-edits win):")
        for t in threads:
            if t["state"] == "accepted":
                db.update("doc_threads", t["id"], {"state": "written"})
        written = passes.staged_threads(db, mid, "alpha.md",
                                        states=("written",))
        # The author edits the insertion's {{new}} half in the Doc.
        edited = marked.replace("{{A bridging paragraph, new.}}",
                                "{{A bridging paragraph, in my voice.}}")
        # A foreign margin-thread form on the same tab must NOT be approved.
        polluted = edited.replace(
            "Third paragraph closes the essay.",
            "<<Third paragraph closes the essay.>>{{Foreign margin rewrite.}}")
        final, forms = passes.final_text_from_marked(polluted, written=written)
        check("final text = critique forms' current {{new}}; foreign forms "
              "stay OLD",
              "stated with care." in final
              and "A bridging paragraph, in my voice." in final
              and "Third paragraph closes the essay." in final
              and "Foreign margin rewrite" not in final
              and "<<" not in final and "{{" not in final)
        diffs = passes.record_resolution(db, mid, "alpha.md", forms)
        check("modified acceptance recorded as a proposal→final diff",
              len(diffs) == 1
              and diffs[0]["proposal"] == "A bridging paragraph, new."
              and diffs[0]["final"] == "A bridging paragraph, in my voice.")
        states = {t["state"] for t in passes.staged_threads(
            db, mid, "alpha.md", states=("cleaned", "declined", "written"))}
        check("written threads closed (cleaned)", states == {"cleaned"})

        print("pin & rollback bookkeeping:")
        passes.pin_version(db, p, "alpha.md", "mv-test")
        p2 = passes.active_pass(db, mid)
        check("pinned version recorded on the pass row",
              passes.pinned_version(p2, "alpha.md") == "mv-test")

        print("CLI surface (no Drive):")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_main(["--workspace", str(ws), "critique", "status"])
        check("critique status shows the pass table",
              "Edit pass" in buf.getvalue() and "alpha.md" in buf.getvalue())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_main(["--workspace", str(ws), "critique", "edits", "alpha.md"])
        listing = buf.getvalue()
        check("critique edits lists only the still-open edit (the undone one), "
              "with old/new/why rendered",
              "decisively" in listing and "why: the ending should land" in listing
              and "stated with care" not in listing)

        print("staging re-run replaces cleanly (no UNIQUE crash):")
        again = passes.stage(db, manuscript, p, "alpha.md", result)
        check("re-run stages without IntegrityError and resets to proposed",
              len(again["staged"]) == 3
              and all(t["state"] == "proposed" for t in again["staged"]))

        # #24 x #26: #26 made stage() replace in place; #24 gates push_doc
        # on threads still 'written'. A re-run that reset 'written' to
        # 'proposed' would quietly lift that gate while the Doc still
        # carries the form — the two fixes are individually right and
        # leave this hole between them.
        pending = passes.staged_threads(db, mid, "alpha.md")[0]
        db.update("doc_threads", pending["id"], {"state": "written"})
        try:
            passes.stage(db, manuscript, p, "alpha.md", result)
            refused = False
        except ValueError as err:
            refused = "already written" in str(err)
        after = db.one("SELECT state FROM doc_threads WHERE id = ?",
                       (pending["id"],))["state"]
        check("re-staging over a form already written to the Doc is "
              "refused, and the form stays written so the push gate holds",
              refused and after == "written", f"refused={refused} state={after}")
        db.update("doc_threads", pending["id"], {"state": "proposed"})

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_main(["--workspace", str(ws), "critique", "show", "--query",
                      "first paragraph"])
        check("critique show --query finds settled items with their status",
              "Clarify the first paragraph" in buf.getvalue()
              and "active" in buf.getvalue())
        rejected = db.one("SELECT id FROM declared_intents WHERE statement = "
                          "'Book-wide rule.'")["id"]
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "critique", "reason",
                      rejected[:11], "--text", "the real why"])
        check("critique reason amends a rejected item's reason",
              db.one("SELECT outcome FROM declared_intents WHERE id = ?",
                     (rejected,))["outcome"] == "the real why")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "critique", "reopen",
                      rejected[:11]])
        check("critique reopen sends it back to proposed",
              db.one("SELECT status FROM declared_intents WHERE id = ?",
                     (rejected,))["status"] == "proposed")

        print("concept revive (inverse of retire):")
        from authorlm import concepts as cg
        cg.add_concept(db, mid, "Gravity", notes="pulls")
        cg.add_concept(db, mid, "Mass", notes="has weight")
        cg.add_concept(db, mid, "Void", notes="empty")
        cg.link_concepts(db, mid, "Gravity", "depends_on", "Mass")
        cg.link_concepts(db, mid, "Gravity", "contrasts_with", "Void")
        g = dict(cg.get_concept(db, mid, "Gravity"))
        db.update("concept_nodes", g["id"],
                  {"status": "realized", "introduced_in": "alpha.md"})
        g = dict(cg.get_concept(db, mid, "Gravity"))
        # An unrelated retirement first: Void goes down, taking its edge —
        # that edge must NOT come back when Gravity is revived.
        cg.retire_concept(db, mid, dict(cg.get_concept(db, mid, "Void")))
        n_edges = cg.retire_concept(db, mid, g)
        check("retire records prior status and takes down live edges",
              n_edges == 1 and db.one("SELECT status FROM concept_nodes WHERE "
                                      "id = ?", (g["id"],))["status"]
              == "retired")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "concept", "revive", "Gravity"])
        g2 = dict(cg.get_concept(db, mid, "Gravity"))
        edges = {(e["relation"], e["status"]) for e in db.all(
            "SELECT * FROM concept_edges WHERE from_node = ?", (g2["id"],))}
        check("revive restores realized + introduced_in and only ITS "
              "collateral edge (Void's stays retired)",
              g2["status"] == "realized" and g2["introduced_in"] == "alpha.md"
              and ("depends_on", "declared") in edges
              and ("contrasts_with", "retired") in edges)
        out = api.curate_concepts(db, manuscript,
                                  [{"op": "revive", "name": "Nope"}])
        check("curate_concepts revive op reports a missing concept per-op",
              out["results"][0]["ok"] is False)

        print("style move:")
        from authorlm import styles
        house = styles.create_guide(db, mid, "house")
        part_guide = styles.create_guide(db, mid, "part", parent_name="house")
        el = styles.add_element(db, mid, "tone", "Be grave.", guide=house)
        moved = api.move_style_law(db, manuscript, el["id"][:10],
                                       guide_name="part")
        check("move re-scopes an element to another guide by name",
              moved["guide"] == "part"
              and db.one("SELECT guide_id FROM style_laws WHERE id = ?",
                         (el["id"],))["guide_id"] == part_guide["id"])
        moved = api.move_style_law(db, manuscript, el["id"][:10],
                                       file="alpha.md")
        check("move to a file clears the guide",
              moved["file"] == "alpha.md"
              and db.one("SELECT guide_id, file FROM style_laws WHERE id "
                         "= ?", (el["id"],))["guide_id"] is None)
        try:
            api.move_style_law(db, manuscript, el["id"][:10],
                                   guide_name="nope")
            raised = False
        except LookupError:
            raised = True
        check("move to an unknown guide is a loud error", raised)

        print("prompt registry:")
        from authorlm import prompt_registry as pr
        check("editor prompt registered as a file prompt",
              pr.by_name("editor").file == "editor.md"
              and "echo" in pr.by_name("editor").text())

        print("recovery: critique rollback snapshots uncollected edits "
              "(BUG-2 / A1):")
        # Self-contained fixture — a fresh manuscript, independent of the
        # alpha/beta/part narrative above, so the destructive write under
        # test can't be confused with anything already staged on 'book'.
        import argparse
        from authorlm.cli import _critique_rollback

        rb_ws = root / "rollback-ws"
        rb_ms = rb_ws / "book"
        rb_ms.mkdir(parents=True)
        (rb_ms / "solo.md").write_text("# Solo\n\nOriginal pinned content.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(rb_ws), "init", "--name", "book",
                      "--path", str(rb_ms)])
        rb_db = api.open_db(str(rb_ws))
        rb_manuscript = api.get_manuscript(rb_db)
        rb_mid = rb_manuscript["id"]
        api.collect(rb_db, rb_manuscript, {})  # v1: the version to pin
        rb_p = passes.ensure_pass(rb_db, rb_mid, rb_db.source("system"))
        v1_row = rb_db.one(
            "SELECT id FROM manuscript_versions WHERE manuscript_id = ? "
            "AND version_no = 1", (rb_mid,))
        passes.pin_version(rb_db, rb_p, "solo.md", v1_row["id"])

        # An open episode FIRST, so the two collects' different
        # dispositions are both observable (AQ/FU-F). Session start runs
        # its own catch-up collect, so it has to happen BEFORE the
        # uncollected edit or that edit is swept up by the catch-up
        # instead of by the verb under test.
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(rb_ws), "session", "start"])
            cli_main(["--workspace", str(rb_ws), "intent", "declare",
                      "Tighten Solo throughout"])
        rb_open = [r["id"] for r in rb_db.all(
            "SELECT id FROM editorial_episodes WHERE manuscript_id = ? "
            "AND status = 'open'", (rb_mid,))]
        check("there IS an open episode for a rollback to be "
              "mis-attributed to (§14.8)", len(rb_open) >= 1, rb_open)

        # The author edits the file after the pin but never collects — the
        # exact state 'critique rollback' must not silently destroy.
        UNCOLLECTED_ROLLBACK = ("# Solo\n\nUNCOLLECTED AUTHOR EDIT, "
                                "NEVER COLLECTED.\n")
        (rb_ms / "solo.md").write_text(UNCOLLECTED_ROLLBACK)
        versions_before_rb = rb_db.one(
            "SELECT MAX(version_no) AS n FROM manuscript_versions WHERE "
            "manuscript_id = ?", (rb_mid,))["n"]

        rb_args = argparse.Namespace(target="solo.md", workspace=str(rb_ws))
        with contextlib.redirect_stdout(io.StringIO()):
            _critique_rollback(rb_db, rb_manuscript, rb_args)

        def _rb_transitions(version_no: int) -> list[str]:
            row = rb_db.one(
                "SELECT id FROM manuscript_versions WHERE manuscript_id = ? "
                "AND version_no = ?", (rb_mid, version_no))
            if row is None:
                return []
            return [r["id"] for r in rb_db.all(
                "SELECT id FROM editorial_transitions WHERE "
                "manuscript_id = ? AND version_after = ?", (rb_mid, row["id"]))]

        attached = set()
        for r in rb_db.all(
                "SELECT transition_ids FROM editorial_episodes WHERE "
                "manuscript_id = ?", (rb_mid,)):
            attached.update(loads(r["transition_ids"], []))
        pre_ids = _rb_transitions(versions_before_rb + 1)
        post_ids = _rb_transitions(versions_before_rb + 2)
        check("BOTH directions, first: the PRE-rollback collect is "
              "AMBIENT, so the author's own uncollected edit attached to "
              "their open episode — it is their work and it predates the "
              "verb, exactly as write_start's first collect does",
              pre_ids and all(tid in attached for tid in pre_ids),
              str({"pre": pre_ids, "attached": sorted(attached)}))
        check("BOTH directions, second: the POST-rollback collect files "
              "the UNWINDING of an edit pass under NO episode. Ambient, "
              "it would have credited whatever goal happened to be open "
              "with the undoing of a pass — the same lie critique "
              "resolve was telling one function away (AQ/FU-F)",
              post_ids and not any(tid in attached for tid in post_ids),
              str({"post": post_ids, "attached": sorted(attached)}))
        check("...and the restore IS recorded at all: before this the "
              "version history held the pre-rollback snapshot and then a "
              "GAP where the restore should have been",
              bool(post_ids), post_ids)
        check("rollback restores the pinned content",
              (rb_ms / "solo.md").read_text()
              == "# Solo\n\nOriginal pinned content.\n")
        rb_versions = rb_db.all(
            "SELECT files FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no", (rb_mid,))
        recovered = any(
            loads(v["files"], {}).get("solo.md") == UNCOLLECTED_ROLLBACK
            for v in rb_versions)
        check("critique rollback snapshots the uncollected edit into "
              "history before overwriting it (BUG-2 / A1)", recovered)

        print("recovery/it-x7-1v2: critique resolve reads the Doc tab as "
              "MARKDOWN (export + split_tabbed_export, never the textRun "
              "walk) and three-ways it against local:")
        import json as _json

        import authorlm.gdocs as _gdocs_mod
        from authorlm.cli import _critique_resolve_essay
        from authorlm.db import ko_fields as _ko

        class _ResolveDocFake:
            """Minimal Drive+Docs stub for the real critique-resolve
            flow. Only files().export() (whole-Doc markdown, forms
            intact — the same shape the real exporter produces) and
            documents().get(includeTabsContent=True) (tab titles/order,
            what walk_tabs needs) are implemented. There is no
            batchUpdate at all: if resolve ever tried to push, it would
            raise AttributeError instead of silently succeeding — that
            absence IS this suite's 'no push' proof."""

            def __init__(self, tabs):
                self.tabs = [{"id": f"tab-{i}", "title": t, "text": x}
                             for i, (t, x) in enumerate(tabs, 1)]

            def set_tab(self, title, text):
                for t in self.tabs:
                    if t["title"] == title:
                        t["text"] = text

            def files(self):
                outer = self

                class _Files:
                    def export(self, fileId=None, mimeType=None):
                        def as_markdown(text):
                            paras = [ln for ln in text.split("\n")
                                    if ln.strip()]
                            return ("\n\n".join(paras)
                                    + ("\n" if paras else ""))
                        whole = "\n".join(
                            f"# **{t['title']}**\n\n{as_markdown(t['text'])}"
                            for t in outer.tabs)

                        class _Req:
                            def execute(self):
                                return whole.encode("utf-8")
                        return _Req()
                return _Files()

            def documents(self):
                outer = self

                class _Documents:
                    def get(self, documentId=None,
                           includeTabsContent=None):
                        tabs = [{"tabProperties": {"tabId": t["id"],
                                                   "title": t["title"]},
                                "childTabs": []} for t in outer.tabs]

                        class _Req:
                            def execute(self):
                                return {"tabs": tabs}
                        return _Req()
                return _Documents()

        def _resolve_fixture(subdir):
            """A fresh workspace with 'solo.md' pushed (pristine, richly
            formatted) and its base hash recorded, exactly as a real
            push would leave it before 'critique write' marks any
            spans."""
            import hashlib as _hashlib

            pristine = ("# Solo\n\n"
                       "A **bold** claim opens the essay.\n\n"
                       "Original paragraph text.\n\n"
                       "- a list item\n\n"
                       "[see the source](https://example.com/source)\n")
            ws = root / subdir
            ms = ws / "book"
            ms.mkdir(parents=True)
            (ms / "solo.md").write_text(pristine)
            with contextlib.redirect_stdout(io.StringIO()):
                cli_main(["--workspace", str(ws), "init", "--name", "book",
                          "--path", str(ms)])
            db_ = api.open_db(str(ws))
            manuscript_ = api.get_manuscript(db_)
            mid_ = manuscript_["id"]
            api.collect(db_, manuscript_, {})  # v1
            passes.ensure_pass(db_, mid_, db_.source("system"))
            normalized = _gdocs_mod.normalize_markdown(pristine)
            base_hash = _hashlib.sha256(
                normalized.encode()).hexdigest()[:16]
            meta = _gdocs_mod._mapping(db_, manuscript_)
            links = meta.setdefault("gdocs", {})
            links["_master_id"] = "doc-fake"
            links["solo.md"] = {"tab_id": "tab-1", "checked_out": False,
                                "pushed_hash": base_hash}
            _gdocs_mod._save_mapping(db_, manuscript_, meta)
            return db_, manuscript_, ms, mid_, pristine

        def _stage_written(db_, mid_, proposed_new):
            written_row = _ko("dt")
            written_row.update(
                manuscript_id=mid_, origin_type="critique", origin_id="cp1",
                file="solo.md", anchor_quote=None,
                proposed_old="Original paragraph text.",
                proposed_new=proposed_new,
                note="test", state="written", our_reply_ids="[]",
                last_author_reply_id=None, scope_kind="file",
                scope_ref="solo.md",
                metadata=_json.dumps({"kind": "replace",
                                      "anchor_paragraph": 1,
                                      "intent_id": None,
                                      "original_new": proposed_new}))
            db_.insert("doc_threads", written_row)
            return written_row

        # --- Scenario 1: the real flow — pristine local, forms in the Doc
        # tab, an author post-edit to the {{new}} half (modified
        # acceptance), and an unrelated edit made ELSEWHERE in the tab.
        # Proves: heading/bold/list/link survive, the elsewhere edit
        # lands, and the modified acceptance is recorded as evidence.
        db1, ms1, msdir1, mid1, pristine1 = _resolve_fixture("resolve-ws-1")
        _stage_written(db1, mid1, "RESOLVED PARAGRAPH TEXT.")
        doc_text_1 = (
            "# Solo\n\n"
            "A **bold** claim opens the essay.\n\n"
            "<<Original paragraph text.>>{{RESOLVED PARAGRAPH TEXT, "
            "POST-EDITED IN THE DOC.}}\n\n"
            "- a list item\n\n"
            "[see the source](https://example.com/source)\n\n"
            "A sentence added only in the Doc, elsewhere in the tab, "
            "never sent locally.\n")
        fake1 = _ResolveDocFake([("solo.md", doc_text_1)])
        _orig1 = (_gdocs_mod.get_service, _gdocs_mod.get_docs_service)
        _gdocs_mod.get_service = lambda *a, **k: fake1
        _gdocs_mod.get_docs_service = lambda *a, **k: fake1
        out1 = io.StringIO()
        try:
            args1 = argparse.Namespace(target="solo.md", workspace=str(
                root / "resolve-ws-1"))
            with contextlib.redirect_stdout(out1):
                _critique_resolve_essay(db1, ms1, args1)
        finally:
            _gdocs_mod.get_service, _gdocs_mod.get_docs_service = _orig1
        resolved1 = (msdir1 / "solo.md").read_text()
        check("resolve keeps the heading, bold, list marker, and link "
              "URL intact — the Doc-as-markdown path never flattens "
              "structure the way the textRun walk did (it-x7-1v2)",
              resolved1.startswith("# Solo\n\n")
              and "**bold**" in resolved1
              and "- a list item" in resolved1
              and "[see the source](https://example.com/source)"
              in resolved1
              and "<<" not in resolved1 and "{{" not in resolved1,
              resolved1)
        check("the author's post-edit to the {{new}} half in the Doc wins",
              "RESOLVED PARAGRAPH TEXT, POST-EDITED IN THE DOC." in
              resolved1, resolved1)
        check("an edit made elsewhere in the tab (outside any pending "
              "form) lands in the local file",
              "A sentence added only in the Doc, elsewhere in the tab, "
              "never sent locally." in resolved1, resolved1)
        check("the modified acceptance is recorded as evidence (proposal "
              "→ final diff, the learnings feedstock)",
              "1 modified acceptance(s) recorded" in out1.getvalue(),
              out1.getvalue())
        row1 = db1.one("SELECT * FROM doc_threads WHERE manuscript_id = ? "
                       "AND file = 'solo.md'", (mid1,))
        check("the written thread closed (cleaned) once resolved",
              row1["state"] == "cleaned", dict(row1))

        # --- Scenario 2: a GENUINE two-sided conflict — local drifted
        # AND the Doc's settled (form-collapsed) content drifted too, in
        # unrelated ways. Resolve must stop and surface it, never pick a
        # side silently.
        db2, ms2, msdir2, mid2, pristine2 = _resolve_fixture("resolve-ws-2")
        _stage_written(db2, mid2, "RESOLVED PARAGRAPH TEXT.")
        LOCAL_DRIFT = pristine2.replace(
            "- a list item", "- a DIFFERENTLY edited list item, local only")
        (msdir2 / "solo.md").write_text(LOCAL_DRIFT)
        doc_text_2 = (
            "# Solo\n\n"
            "A **very** different claim, edited only in the Doc.\n\n"
            "<<Original paragraph text.>>{{RESOLVED PARAGRAPH TEXT.}}\n\n"
            "- a list item\n\n"
            "[see the source](https://example.com/source)\n")
        fake2 = _ResolveDocFake([("solo.md", doc_text_2)])
        _orig2 = (_gdocs_mod.get_service, _gdocs_mod.get_docs_service)
        _gdocs_mod.get_service = lambda *a, **k: fake2
        _gdocs_mod.get_docs_service = lambda *a, **k: fake2
        conflict_err = None
        try:
            args2 = argparse.Namespace(target="solo.md", workspace=str(
                root / "resolve-ws-2"))
            with contextlib.redirect_stdout(io.StringIO()):
                _critique_resolve_essay(db2, ms2, args2)
        except SystemExit as exc:
            conflict_err = str(exc)
        finally:
            _gdocs_mod.get_service, _gdocs_mod.get_docs_service = _orig2
        check("a genuine two-sided edit is surfaced as a conflict, not "
              "silently resolved",
              conflict_err is not None
              and "changed both locally and in the Doc" in conflict_err,
              conflict_err)
        check("the local file is untouched by a surfaced conflict",
              (msdir2 / "solo.md").read_text() == LOCAL_DRIFT,
              (msdir2 / "solo.md").read_text())
        row2 = db2.one("SELECT * FROM doc_threads WHERE manuscript_id = ? "
                       "AND file = 'solo.md'", (mid2,))
        check("a surfaced conflict leaves the written thread untouched",
              row2["state"] == "written", dict(row2))

        # --- Scenario 3 (BUG-2 / A1, re-founded on the real flow): local
        # drifted (an uncollected edit outside the critique/Doc flow) but
        # the Doc's settled content did NOT — 'local_ahead', not a
        # conflict. Resolve still applies the Doc's resolution (the
        # explicit act the author invoked), snapshotting the local drift
        # into version history first so it is never silently lost.
        db3, ms3, msdir3, mid3, pristine3 = _resolve_fixture("resolve-ws-3")
        _stage_written(db3, mid3, "RESOLVED PARAGRAPH TEXT.")
        UNCOLLECTED_RESOLVE = pristine3 + (
            "\nUNCOLLECTED PARAGRAPH ADDED LOCALLY, NEVER SENT TO THE "
            "DOC.\n")
        (msdir3 / "solo.md").write_text(UNCOLLECTED_RESOLVE)
        doc_text_3 = pristine3.replace(
            "Original paragraph text.",
            "<<Original paragraph text.>>{{RESOLVED PARAGRAPH TEXT.}}")
        fake3 = _ResolveDocFake([("solo.md", doc_text_3)])
        _orig3 = (_gdocs_mod.get_service, _gdocs_mod.get_docs_service)
        _gdocs_mod.get_service = lambda *a, **k: fake3
        _gdocs_mod.get_docs_service = lambda *a, **k: fake3
        try:
            args3 = argparse.Namespace(target="solo.md", workspace=str(
                root / "resolve-ws-3"))
            with contextlib.redirect_stdout(io.StringIO()):
                _critique_resolve_essay(db3, ms3, args3)
        finally:
            _gdocs_mod.get_service, _gdocs_mod.get_docs_service = _orig3
        resolved3 = (msdir3 / "solo.md").read_text()
        check("resolve still applies the Doc's resolution over a "
              "local-only drift ('local_ahead', not a conflict)",
              "RESOLVED PARAGRAPH TEXT." in resolved3
              and "UNCOLLECTED PARAGRAPH" not in resolved3, resolved3)
        res_versions = db3.all(
            "SELECT files FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no", (mid3,))
        recovered = any(
            loads(v["files"], {}).get("solo.md") == UNCOLLECTED_RESOLVE
            for v in res_versions)
        check("critique resolve snapshots the uncollected local edit into "
              "history before overwriting the file (BUG-2 / A1)", recovered)

        # --- Scenario 4 (AQ, the §15.17 mis-attribution family living on
        # in the critique pass): a resolve run while a parallel writeup's
        # goal has an open episode must attach its transitions to NO
        # episode. `collect(episode=None)` means AMBIENT — the session's
        # most recently created open episode, whatever goal it belongs to
        # — so before the fix every critique-pass resolve filed its
        # transitions against whichever intent happened to be open, and
        # §15.17's completion analysis would report that goal as served
        # by an edit sweep it had nothing to do with. Settling an edit is
        # hygiene, not goal-work (filter-pass design §1.8).
        db4, ms4, msdir4, mid4, pristine4 = _resolve_fixture("resolve-ws-4")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(root / "resolve-ws-4"),
                      "session", "start"])
            cli_main(["--workspace", str(root / "resolve-ws-4"),
                      "intent", "declare",
                      "Rewrite Solo to foreground the refrain"])
        open_before = {
            r["id"]: r["transition_ids"] for r in db4.all(
                "SELECT id, transition_ids FROM editorial_episodes "
                "WHERE manuscript_id = ? AND status = 'open'", (mid4,))}
        check("the fixture really does have an open episode for a goal "
              "to be mis-attributed TO — a guard whose test cannot make "
              "the guarded thing happen proves nothing (§14.8)",
              len(open_before) >= 1, open_before)
        _stage_written(db4, mid4, "RESOLVED PARAGRAPH TEXT.")
        doc_text_4 = pristine4.replace(
            "Original paragraph text.",
            "<<Original paragraph text.>>{{RESOLVED PARAGRAPH TEXT.}}")
        fake4 = _ResolveDocFake([("solo.md", doc_text_4)])
        _orig4 = (_gdocs_mod.get_service, _gdocs_mod.get_docs_service)
        _gdocs_mod.get_service = lambda *a, **k: fake4
        _gdocs_mod.get_docs_service = lambda *a, **k: fake4
        try:
            args4 = argparse.Namespace(target="solo.md", workspace=str(
                root / "resolve-ws-4"))
            with contextlib.redirect_stdout(io.StringIO()):
                _critique_resolve_essay(db4, ms4, args4)
        finally:
            _gdocs_mod.get_service, _gdocs_mod.get_docs_service = _orig4
        check("the resolve really did change the essay — otherwise there "
              "would be no transitions to mis-attribute",
              "RESOLVED PARAGRAPH TEXT." in
              (msdir4 / "solo.md").read_text(),
              (msdir4 / "solo.md").read_text())
        open_after = {
            r["id"]: r["transition_ids"] for r in db4.all(
                "SELECT id, transition_ids FROM editorial_episodes "
                "WHERE manuscript_id = ?", (mid4,))}
        grew = {eid: (open_before[eid], open_after[eid])
                for eid in open_before
                if open_after.get(eid) != open_before[eid]}
        check("critique resolve attaches its transitions to NO episode: "
              "every episode open when it ran carries exactly the "
              "transitions it carried before (AQ, §15.17's family)",
              not grew, grew)
        born = set(open_after) - set(open_before)
        check("...and it opened no new episode to file them under either "
              "— filing under a fresh episode would be the grouping "
              "object §15.17 refused, wearing a new hat",
              not any(loads(db4.one(
                  "SELECT transition_ids FROM editorial_episodes "
                  "WHERE id = ?", (eid,))["transition_ids"], [])
                  for eid in born),
              sorted(born))
        settle_ts = [dict(r) for r in db4.all(
            "SELECT * FROM editorial_transitions WHERE manuscript_id = ? "
            "AND detail LIKE '%RESOLVED PARAGRAPH TEXT%'", (mid4,))]
        check("the transitions themselves DO exist — the version history "
              "is the complete record, and nothing was suppressed to "
              "make the attribution assertion pass",
              len(settle_ts) >= 1, settle_ts)

        print("recovery: doc pull snapshots uncollected edits before "
              "overwriting (BUG-1 / A2):")
        # three_way() unconditionally prefers the Doc's tab over local
        # content whenever the file has no recorded base_hash (a
        # base-less mapped pull) — that mechanism is stubbed directly
        # here rather than through a fake tabbed export, since whether a
        # real Google export triggers it is a separate, unresolved
        # question (register C7) that A2's snapshot-first fix does not
        # depend on either way.
        dp_ws = root / "docpull-ws"
        dp_ms = dp_ws / "book"
        dp_ms.mkdir(parents=True)
        (dp_ms / "solo.md").write_text("Original collected content.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(dp_ws), "init", "--name", "book",
                      "--path", str(dp_ms)])
        dp_db = api.open_db(str(dp_ws))
        dp_manuscript = api.get_manuscript(dp_db)
        api.collect(dp_db, dp_manuscript, {})  # v1

        UNCOLLECTED_PULL = ("UNCOLLECTED PARAGRAPH, NEVER COLLECTED, ABOUT "
                            "TO BE PULLED OVER.\n")
        (dp_ms / "solo.md").write_text(UNCOLLECTED_PULL)

        def _fake_service2(config, workspace=None, interactive=False):
            return object()

        def _fake_pull_doc(db, manuscript, query=None, service=None,
                           force=False, with_comments=True,
                           docs_service=None, bridge=None):
            (Path(manuscript["path"]) / "solo.md").write_text(
                "PULLED FROM DOC.\n")
            return {"changed": ["solo.md"], "unchanged": [], "conflicts": [],
                    "local_ahead": [], "missing": [], "doc_id": "doc-1"}

        _orig2 = (_gdocs_mod.get_service, _gdocs_mod.get_docs_service,
                 _gdocs_mod.pull_doc)
        _gdocs_mod.get_service = _fake_service2
        _gdocs_mod.get_docs_service = _fake_service2
        _gdocs_mod.pull_doc = _fake_pull_doc
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                cli_main(["--workspace", str(dp_ws), "doc", "pull",
                          "solo.md"])
        finally:
            (_gdocs_mod.get_service, _gdocs_mod.get_docs_service,
             _gdocs_mod.pull_doc) = _orig2

        check("pull overwrote the file with the Doc's content",
              (dp_ms / "solo.md").read_text() == "PULLED FROM DOC.\n")
        dp_versions = dp_db.all(
            "SELECT files FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no", (dp_manuscript["id"],))
        recovered = any(
            loads(v["files"], {}).get("solo.md") == UNCOLLECTED_PULL
            for v in dp_versions)
        check("doc pull snapshots the uncollected local edit into history "
              "before the Doc overwrites it (BUG-1 / A2)", recovered)

        _local_transport_guards(root)
        _identical_old_halves(root)
        _the_transport_is_frozen(root)
        _a_paragraph_that_contains_another(root)
        _embed_blank_lines_skew_occurrence(root)
        _the_doc_is_the_review(root)
        _mixed_push_set_membership(root)
        _failed_writes_keep_their_own_state(root)
        _the_doc_transport_push(root)
        _the_push_says_what_it_did_to_the_file(root)
        _the_push_is_partial_or_nothing(root)
        _twins_go_to_the_doc(root)
        _the_doc_settle(root)
        _a_hand_resolved_tab_settles_true(root)
        _the_lens_door(root)
        _the_changed_words_pop(root)
        _a_pull_between_push_and_settle(root)
        _twins_settle_by_position(root)
        _the_learnings_loop_reaches_the_filter(root)
        _recovery_while_forms_are_out(root)
        _chat_sees_each_run_s_transport(root)
        _status_can_reach_the_tab(root)
        _a_second_run_may_not_push_into_the_same_tab(root)
        _the_hint_after_an_unmark(root)
        _awkward_new_halves_through_the_doc(root)
        _the_doc_road_through_the_cli(root)
    finally:
        server.shutdown()
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    _assert_offline()
    main_test()
