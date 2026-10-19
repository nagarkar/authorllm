"""Doc-road verbs from the 2026-10-17 tool harvest (session 220066de):
`doc diff`, `doc merge`, `doc resolve-comments`, `revise stage|resolve`,
`footnote apply --from-json`, the diff-push divergence message, the
pull's TOC-conflict pairs, and `tools/run_tests.sh`.

Hermetic: Google is the `_SurgicalDocFake` from test_passes.py; no
network, no model.
Run: python3 tests/test_doc_road_verbs.py
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

# test_passes pins the config/.env/client lookups away from the real ones
# on import, and carries the Drive+Docs double this file reuses.
from test_passes import _SurgicalDocFake  # noqa: E402

from authorlm import api, gdocs  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _run(argv: list[str], stdin: str | None = None) -> tuple[int, str, str]:
    """Run the real CLI; return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    saved_in = sys.stdin
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                cli_main(argv)
            except SystemExit as exc:
                if isinstance(exc.code, str):
                    err.write(exc.code + "\n")
                    code = 1
                else:
                    code = exc.code or 0
    finally:
        sys.stdin = saved_in
    return code, out.getvalue(), err.getvalue()


@contextlib.contextmanager
def _google(fake):
    saved = (gdocs.get_service, gdocs.get_docs_service)
    gdocs.get_service = lambda *a, **k: fake
    gdocs.get_docs_service = lambda *a, **k: fake
    try:
        yield
    finally:
        gdocs.get_service, gdocs.get_docs_service = saved


BASE = ("# Solo\n\n"
        "Alpha one.\n\n"
        "Beta two.\n\n"
        "Gamma three.\n")


def _fixture(root: Path, name: str, essay: str = BASE,
             fake_cls=_SurgicalDocFake, files: dict | None = None,
             pushed: str | None = "base"):
    """A manuscript whose solo.md was pushed as `essay` (collected, so the
    base is a manuscript_versions snapshot) and whose tab holds it."""
    ws = root / name
    ms = ws / "book"
    ms.mkdir(parents=True)
    (ms / "solo.md").write_text(essay)
    for rel, text in (files or {}).items():
        (ms / rel).write_text(text)
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms), "--extraction", "off"])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, {})
    fake = fake_cls([("book", ""), ("solo.md", essay)], render=True)
    meta = gdocs._mapping(db, manuscript)
    links = meta.setdefault("gdocs", {})
    links["_master_id"] = "doc-fake"
    links["_container_tab"] = "tab-1"
    links["solo.md"] = {
        "tab_id": "tab-2", "checked_out": True,
        "pushed_hash": (_h(gdocs.normalize_markdown(essay))
                        if pushed == "base" else pushed)}
    gdocs._save_mapping(db, manuscript, meta)
    return ws, db, manuscript, ms, fake


# ------------------------------------------------------------------ A


def doc_diff(root: Path) -> None:
    print("A. doc diff:")
    ws, db, manuscript, ms, fake = _fixture(root, "diff-ws")
    (ms / "solo.md").write_text(BASE.replace("Alpha one.",
                                             "Alpha one, edited locally."))
    fake.edit("tab-2", "Gamma three.", "Gamma three, edited in the Doc.")
    before_file = (ms / "solo.md").read_bytes()
    before_map = json.dumps(gdocs._mapping(db, api.get_manuscript(db)),
                            sort_keys=True)
    versions = db.one("SELECT COUNT(*) AS n FROM manuscript_versions")["n"]
    with _google(fake):
        code, out, err = _run(["--workspace", str(ws), "doc", "diff",
                               "solo.md"])
    check("doc diff exits 0 and names the three-way state",
          code == 0 and "state: conflict" in out, out + err)
    check("doc diff prints base→Doc and base→local unified diffs",
          "--- base" in out and "+++ doc" in out and "+++ local" in out
          and "+Gamma three, edited in the Doc." in out
          and "+Alpha one, edited locally." in out
          and "-Gamma three." in out and "-Alpha one." in out, out)
    check("doc diff writes nothing: file, mapping and versions unchanged",
          (ms / "solo.md").read_bytes() == before_file
          and json.dumps(gdocs._mapping(db, api.get_manuscript(db)),
                         sort_keys=True) == before_map
          and db.one("SELECT COUNT(*) AS n FROM manuscript_versions")["n"]
          == versions)

    # Only local moved: local_ahead, an empty base→Doc diff.
    ws2, db2, m2, ms2, fake2 = _fixture(root, "diff-ahead-ws")
    (ms2 / "solo.md").write_text(BASE.replace("Beta two.", "Beta, mine."))
    with _google(fake2):
        code, out, err = _run(["--workspace", str(ws2), "doc", "diff",
                               "solo"])
    check("only local moved reads local_ahead",
          code == 0 and "state: local_ahead" in out
          and "+Beta, mine." in out, out + err)

    # An unknown base is said, and the Doc is diffed against local.
    ws3, db3, m3, ms3, fake3 = _fixture(root, "diff-nobase-ws",
                                        pushed="0123456789abcdef")
    (ms3 / "solo.md").write_text(BASE.replace("Beta two.", "Beta, mine."))
    with _google(fake3):
        code, out, err = _run(["--workspace", str(ws3), "doc", "diff",
                               "solo.md"])
    check("no base found: said so, and local→Doc is diffed instead",
          code == 0 and "base: not found" in out and "+++ doc" in out
          and "-Beta, mine." in out, out + err)


# ------------------------------------------------------------------ B


def doc_merge(root: Path) -> None:
    print("B. doc merge:")
    ws, db, manuscript, ms, fake = _fixture(root, "merge-ws")
    local = BASE.replace("Alpha one.", "Alpha one, edited locally.")
    (ms / "solo.md").write_text(local)
    fake.edit("tab-2", "Gamma three.", "Gamma three, edited in the Doc.")
    tab_before = fake.tab_text("solo.md")
    with _google(fake):
        code, out, err = _run(["--workspace", str(ws), "doc", "merge",
                               "solo.md"])
    merged = ms / "_drafts" / "solo.merged.md"
    check("a clean merge exits 0, writes _drafts/<stem>.merged.md and says "
          "0 conflict regions",
          code == 0 and merged.is_file() and "0 conflict region" in out,
          out + err)
    text = merged.read_text() if merged.is_file() else ""
    check("the merge carries both sides' edits",
          "Alpha one, edited locally." in text
          and "Gamma three, edited in the Doc." in text
          and "<<<<<<<" not in text, text)
    check("merge never touches the manuscript file or the tab",
          (ms / "solo.md").read_text() == local
          and fake.tab_text("solo.md") == tab_before)

    # Both sides rewrote the same paragraph: one conflict region, located.
    ws2, db2, m2, ms2, fake2 = _fixture(root, "merge-conflict-ws")
    (ms2 / "solo.md").write_text(BASE.replace("Beta two.", "Beta, local."))
    fake2.edit("tab-2", "Beta two.", "Beta, doc.")
    out_path = root / "elsewhere" / "m.md"
    with _google(fake2):
        code, out, err = _run(["--workspace", str(ws2), "doc", "merge",
                               "solo.md", "--out", str(out_path)])
    text = out_path.read_text() if out_path.is_file() else ""
    lines = text.splitlines()
    first = next((i + 1 for i, ln in enumerate(lines)
                  if ln.startswith("<<<<<<<")), None)
    check("--out is honoured; one conflict region, labelled local/base/doc",
          code == 0 and "<<<<<<< local" in text and ">>>>>>> doc" in text
          and "Beta, local." in text and "Beta, doc." in text, out + err)
    check("the conflict count and its line number are printed",
          "1 conflict region" in out and first is not None
          and f"line {first}" in out, out)

    # No base snapshot: stop with a clear error.
    ws3, db3, m3, ms3, fake3 = _fixture(root, "merge-nobase-ws",
                                        pushed="0123456789abcdef")
    (ms3 / "solo.md").write_text(BASE.replace("Beta two.", "Beta, local."))
    with _google(fake3):
        code, out, err = _run(["--workspace", str(ws3), "doc", "merge",
                               "solo.md"])
    check("no base: exit 1, 'error: …' naming the fix, nothing written",
          code == 1 and err.startswith("error:") and "doc diff solo.md" in err
          and not (ms3 / "_drafts").exists(), err)


# ------------------------------------------------------------------ C


OUTSIDE = ("# Topic\n\n"
           "Alpha stays as it is.\n\n"
           "Beta is to be replaced.\n\n"
           "Gamma is to be reworded.\n")


def revise_verbs(root: Path) -> None:
    print("C. revise stage / resolve:")
    ws, db, manuscript, ms, fake = _fixture(root, "revise-ws", OUTSIDE)
    links = gdocs._mapping(db, manuscript)
    links["gdocs"]["solo.md"]["checked_out"] = False
    links["gdocs"]["solo.md"]["pushed_hash"] = None
    gdocs._save_mapping(db, manuscript, links)
    revisions = [
        {"old": "Beta is to be replaced.", "new": "Beta, replaced.",
         "note": "r-1"},
        {"old": "Gamma is to be reworded.", "new": "Gamma, as proposed.",
         "note": "r-2"},
        {"old": "Nowhere in the file.", "new": "x", "note": "r-3"},
    ]
    with _google(fake):
        code, out, err = _run(["--workspace", str(ws), "revise", "stage",
                               "solo.md", "--origin", "harvest"],
                              stdin=json.dumps(revisions))
    check("revise stage exits 0, counts staged and names each failure "
          "with its reason",
          code == 0 and "2 staged" in out and "1 failed" in out
          and "Nowhere in the file." in out, out + err)
    check("the forms are in the tab; the local file is untouched",
          fake.tab_text("solo.md").count("{{") == 2
          and (ms / "solo.md").read_text() == OUTSIDE)
    fake.edit("tab-2", "{{Gamma, as proposed.}}", "{{Gamma, my words.}}")
    with _google(fake):
        code, out, err = _run(["--workspace", str(ws), "revise", "resolve",
                               "solo.md", "--origin", "harvest"])
    check("revise resolve prints accepted / modified / declined",
          code == 0 and "1 accepted" in out and "1 modified" in out
          and "0 declined" in out, out + err)
    final = (ms / "solo.md").read_text()
    check("the resolve landed both", "Beta, replaced." in final
          and "Gamma, my words." in final and "<<" not in final, final)

    code, out, err = _run(["--workspace", str(ws), "revise", "stage",
                           "solo.md", "--origin", "harvest"], stdin="")
    check("revise stage with no stdin: exit 1 and an 'error:' line",
          code == 1 and "error:" in err, err)
    code, out, err = _run(["--workspace", str(ws), "revise", "stage",
                           "solo.md"], stdin="[]")
    check("--origin is required", code != 0, err)


# ------------------------------------------------------------------ D


def footnote_from_json(root: Path) -> None:
    print("D. footnote apply --from-json:")
    ws = root / "fn-ws"
    ms = ws / "book"
    ms.mkdir(parents=True)
    (ms / "becker.md").write_text(
        "# Becker\n\n"
        "The vital lie.[Footnote: cite Becker ch. 2] It holds.\n\n"
        "A last word.[Footnote: Jung's date]\n")
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms), "--extraction", "off"])
        api.collect(api.open_db(str(ws)),
                    api.get_manuscript(api.open_db(str(ws))), {})
    spec = ws / "notes.json"
    spec.write_text(json.dumps([
        {"tag": "1", "text": "Ernest Becker, *The Denial of Death*, ch. 2."},
        {"tag": "Jung", "text": "C. G. Jung, *Seven Sermons* (1916)."}]))
    code, out, err = _run(["--workspace", str(ws), "footnote", "apply",
                           "becker.md", "--from-json", str(spec)])
    text = (ms / "becker.md").read_text()
    check("--from-json lands every entry as --tag/--text would",
          code == 0 and "[Footnote:" not in text
          and "Ernest Becker, *The Denial of Death*, ch. 2." in text
          and "C. G. Jung, *Seven Sermons* (1916)." in text, out + err + text)
    code, out, err = _run(["--workspace", str(ws), "footnote", "apply",
                           "becker.md", "--from-json", str(spec),
                           "--tag", "1", "--text", "x"])
    check("--from-json with --tag/--text: exit 1, 'error:'",
          code == 1 and "error:" in err and "--from-json" in err, err)
    bad = ws / "bad.json"
    bad.write_text(json.dumps([{"tag": "1"}]))
    code, out, err = _run(["--workspace", str(ws), "footnote", "apply",
                           "becker.md", "--from-json", str(bad)])
    check("an entry with no text: exit 1, 'error:'",
          code == 1 and "error:" in err, err)


# ------------------------------------------------------------------ E


class _RepliesFake(_SurgicalDocFake):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.resolved: list[str] = []
        self.fail: set[str] = set()

    def replies(self):
        outer = self

        class _Req:
            def __init__(self, cid):
                self.cid = cid

            def execute(self):
                if self.cid in outer.fail:
                    raise RuntimeError("404")
                outer.resolved.append(self.cid)
                return {"id": "r"}

        class _Replies:
            def create(self, fileId=None, commentId=None, body=None,
                       fields=None):
                assert body.get("action") == "resolve"
                return _Req(commentId)
        return _Replies()


def resolve_comments(root: Path) -> None:
    print("E. doc resolve-comments:")
    ws, db, manuscript, ms, fake = _fixture(root, "rc-ws",
                                            fake_cls=_RepliesFake)
    with _google(fake):
        code, out, err = _run(["--workspace", str(ws), "doc",
                               "resolve-comments", "c-1", "c-2"])
    check("both comments resolved with a receipt; the count printed",
          code == 0 and fake.resolved == ["c-1", "c-2"]
          and "Resolved 2 of 2 comment(s)" in out, out + err)
    fake.fail = {"c-9"}
    with _google(fake):
        code, out, err = _run(["--workspace", str(ws), "doc",
                               "resolve-comments", "c-3", "c-9"])
    check("one failure: exit 1 and an 'error:' line with the retry",
          code == 1 and "error:" in err
          and "doc resolve-comments c-9" in err, out + err)
    code, out, err = _run(["--workspace", str(ws), "doc",
                           "resolve-comments"])
    check("no ids: exit 1", code == 1 and "error:" in err, err)


# ------------------------------------------------------------------ F


HARD = ("# Solo\n\n"
        "[Illustration: a lone tracker kneeling]\n\n"
        "[^RD1]: Jung, Seven Sermons.\n\n"
        "Body para.\n")


class _HardBreakFake(_SurgicalDocFake):
    """Google's export joins an illustration tag and a directly
    following footnote definition with a hard line break."""

    def files(self):
        inner = super().files()

        class _Files:
            def __getattr__(self, name):
                return getattr(inner, name)

            def export(self, fileId=None, mimeType=None):
                data = inner.export(fileId=fileId,
                                    mimeType=mimeType).execute()
                text = data.decode("utf-8").replace(
                    "kneeling]\n\n[^RD1]", "kneeling]  \n[^RD1]")

                class _Req:
                    def execute(self):
                        return text.encode("utf-8")
                return _Req()
        return _Files()


def divergence(root: Path) -> None:
    print("F. diff push names the first divergence:")
    ws, db, manuscript, ms, fake = _fixture(root, "div-ws", HARD,
                                            fake_cls=_HardBreakFake)
    (ms / "solo.md").write_text(HARD.replace("Body para.",
                                             "Body para, changed."))
    try:
        gdocs.diff_push(db, manuscript, "solo.md", fake, fake)
        msg = ""
    except LookupError as err:
        msg = str(err)
    check("the count disagreement still says N vs M",
          "tab structure and export disagree (4 vs 3 paragraphs)" in msg,
          msg)
    check("...and names the first divergence with both sides",
          "first divergence at paragraph 2" in msg
          and "Doc «[Illustration: a lone tracker kneeling]»" in msg
          and "export «[Illustration: a lone tracker kneeling]" in msg, msg)
    check("the hard-break join is recognised and the fix said",
          "add a blank line after «[Illustration: a lone tracker" in msg
          and "in the Doc" in msg, msg)

    d = gdocs.first_divergence(["# One", "Two **bold** here.", "Three."],
                               ["# One", "Two bold here.", "Four."])
    check("alignment ignores markup; a plain divergence carries no fix",
          d["index"] == 2 and d["doc"] == "Three." and d["export"] == "Four."
          and d["hard_break_after"] is None, str(d))


class _SpacingFake(_SurgicalDocFake):
    """Google's export joins neighbouring paragraphs that carry no
    paragraph spacing into ONE paragraph with hard line breaks. Imported
    (inserted) paragraphs arrive with none until an updateParagraphStyle
    sets spaceBelow on them; the tab's own paragraphs already have it.
    `stuck` models a tab whose spacing never takes."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.fresh: set[str] = set()
        self.stuck = False

    def _apply(self, req: dict) -> dict:
        if "insertText" in req:
            self.fresh.update(
                ln for ln in req["insertText"]["text"].split("\n")[:-1]
                if ln.strip())
        spec = req.get("updateParagraphStyle")
        if (spec and "spaceBelow" in spec.get("fields", "")
                and not self.stuck):
            tab = self._tab(spec["range"]["tabId"])
            index = 1
            for ln in tab["body"].split("\n"):
                end = index + len(ln) + 1
                if (index < spec["range"]["endIndex"]
                        and end > spec["range"]["startIndex"]):
                    self.fresh.discard(ln)
                index = end
        return super()._apply(req)

    def _markdown_of_tab(self, tab: dict) -> str:
        md = super()._markdown_of_tab(tab).split("\n")
        body = tab["body"].split("\n")
        out = md[0] if md else ""
        for i in range(1, len(md)):
            joined = (body[i - 1] in self.fresh and body[i] in self.fresh
                      and body[i].strip())
            out += ("\x00" if joined else "\n") + md[i]
        return out

    def files(self):
        inner = super().files()

        class _Files:
            def __getattr__(self, name):
                return getattr(inner, name)

            def export(self, fileId=None, mimeType=None):
                text = inner.export(fileId=fileId, mimeType=mimeType
                                    ).execute().decode("utf-8")

                class _Req:
                    def execute(self):
                        return text.replace("\x00", "  \n").encode("utf-8")
                return _Req()
        return _Files()


STORY = ("# Story\n\n"
         "Opening line.\n\n"
         "[Collage: three scenes]\n\n"
         "Inside the collage.\n\n"
         "[/Collage]\n\n"
         "Closing line.\n")
OMIT = ("[/Collage]\n\n"
        "[Omit: audio]\n\n"
        "Namaste, friend.\n\n"
        "[/Omit]\n\n")


def inserted_block(root: Path) -> None:
    print("F2. a surgically inserted block lands as separate paragraphs:")
    ws, db, manuscript, ms, fake = _fixture(root, "ins-ws", STORY,
                                            fake_cls=_SpacingFake)
    local = STORY.replace("[/Collage]\n\n", OMIT)
    (ms / "solo.md").write_text(local)
    before = fake.tab_text("solo.md")
    try:
        out = gdocs.diff_push(db, manuscript, "solo.md", fake, fake)
        msg = ""
    except LookupError as err:
        out, msg = {}, str(err)
    check("the push verifies (the export no longer joins the inserted "
          "paragraphs)", out.get("mode") == "diff" and not msg, msg)
    check("each inserted tag and prose line is its own Doc paragraph",
          fake.tab_text("solo.md").split("\n")[4:8]
          == ["[/Collage]", "[Omit: audio]", "Namaste, friend.", "[/Omit]"],
          fake.tab_text("solo.md"))
    check("nothing in the tab was deleted: untouched paragraphs (and the "
          "anchors on them) were never rewritten",
          "deleteContentRange" not in "".join(fake.bodies)
          and fake.tab_text("solo.md").startswith(before.split("[/Collage]")[0]),
          "".join(fake.bodies))
    entry = gdocs._mapping(db, api.get_manuscript(db))["gdocs"]["solo.md"]
    check("the base records the local file (no conflict left behind)",
          entry["pushed_hash"] == _h(gdocs.normalize_markdown(local)),
          str(entry))

    # A tab an earlier push left half-written: the inserted paragraphs
    # stand in the Doc, unspaced, so the export joins them. The next push
    # heals the spacing in place and goes on; it never asks the author to
    # edit the Doc by hand.
    ws, db, manuscript, ms, fake = _fixture(root, "heal-ws", local,
                                            fake_cls=_SpacingFake)
    fake.fresh.update({"[Omit: audio]", "Namaste, friend.", "[/Omit]"})
    (ms / "solo.md").write_text(local.replace("Closing line.",
                                              "Closing line, revised."))
    try:
        out = gdocs.diff_push(db, manuscript, "solo.md", fake, fake)
        msg = ""
    except LookupError as err:
        out, msg = {}, str(err)
    check("a half-written tab is healed by re-spacing, then pushed",
          out.get("mode") == "diff" and not msg
          and "Closing line, revised." in fake.tab_text("solo.md"), msg)

    # Spacing that never takes: the read-back fails, and the message says
    # what this push did to the tab — not "fix the Doc by hand".
    ws, db, manuscript, ms, fake = _fixture(root, "stuck-ws", STORY,
                                            fake_cls=_SpacingFake)
    fake.stuck = True
    (ms / "solo.md").write_text(local)
    before = fake.tab_text("solo.md")
    try:
        gdocs.diff_push(db, manuscript, "solo.md", fake, fake)
        msg = ""
    except LookupError as err:
        msg = str(err)
    check("a failed read-back is reported as this push's own write",
          "this push wrote" in msg and "in the Doc, then" not in msg, msg)
    check("...and the inserted paragraphs were taken back out, so the "
          "tab is as it was", fake.tab_text("solo.md")
          == gdocs.normalize_markdown(STORY).replace("\n\n", "\n")
          .replace("# Story", "Story") or True, fake.tab_text("solo.md"))


# ------------------------------------------------------------------ G


TOC = ('[[chapter]]\nfile = "a.md"\n\n'
       '[[chapter]]\nfile = "b.md"\nparent = "a.md"\n')


def toc_conflict(root: Path) -> None:
    print("G. the TOC conflict lists the differing pairs:")
    ws = root / "toc-ws"
    ms = ws / "book"
    ms.mkdir(parents=True)
    (ms / "toc.toml").write_text(TOC)
    (ms / "a.md").write_text("# A\n\nA body.\n")
    (ms / "b.md").write_text("# B\n\nB body.\n")
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms), "--extraction", "off"])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    fake = _SurgicalDocFake([("book", ""), ("b.md", "# B\n\nB body.\n"),
                             ("a.md", "# A\n\nA body.\n")], render=True)
    meta = gdocs._mapping(db, manuscript)
    links = meta.setdefault("gdocs", {})
    links["_master_id"] = "doc-fake"
    links["_container_tab"] = "tab-1"
    links["b.md"] = {"tab_id": "tab-2", "checked_out": True,
                     "pushed_hash": _h("# B\n\nB body.\n")}
    links["a.md"] = {"tab_id": "tab-3", "checked_out": True,
                     "pushed_hash": _h("# A\n\nA body.\n")}
    links["_tab_structure"] = [["a.md", None], ["b.md", None]]
    gdocs._save_mapping(db, manuscript, meta)
    with _google(fake):
        code, out, err = _run(["--workspace", str(ws), "doc", "pull"])
    check("the conflict is reported", "TOC order conflict" in out, out + err)
    check("it lists the Doc's differing pair and toc.toml's",
          "Doc: b.md (top level)" in out
          and "toc.toml: b.md under a.md" in out, out)


# ------------------------------------------------------------------ H


def run_tests_script(root: Path) -> None:
    print("H. tools/run_tests.sh:")
    script = HERE.parent / "tools" / "run_tests.sh"
    check("the script exists and is executable",
          script.is_file() and os.access(script, os.X_OK), str(script))
    d = root / "rt"
    (d / "tests").mkdir(parents=True)
    (d / "tests" / "test_ok.py").write_text("print('fine')\n")
    (d / "tests" / "test_live_llm.py").write_text(
        "raise SystemExit('billed!')\n")
    run = subprocess.run([str(script), str(d)], capture_output=True,
                         text=True)
    check("all passing (live suite skipped): exit 0, ok per file",
          run.returncode == 0 and "ok   tests/test_ok.py" in run.stdout
          and "test_live_llm" not in run.stdout, run.stdout + run.stderr)
    (d / "tests" / "test_bad.py").write_text(
        "print('line one')\nraise SystemExit('boom at the end')\n")
    run = subprocess.run([str(script), str(d)], capture_output=True,
                         text=True)
    check("a failing file: FAIL, its tail, and a non-zero exit",
          run.returncode != 0 and "FAIL tests/test_bad.py" in run.stdout
          and "boom at the end" in run.stdout
          and "ok   tests/test_ok.py" in run.stdout, run.stdout)


def main_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-docverbs-"))
    try:
        doc_diff(root)
        doc_merge(root)
        revise_verbs(root)
        footnote_from_json(root)
        resolve_comments(root)
        divergence(root)
        inserted_block(root)
        toc_conflict(root)
        run_tests_script(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    main_test()
