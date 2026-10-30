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
import copy
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
    check("a hard-break join the Doc's paragraphs explain is read as "
          "those paragraphs: the push lands, no hand edit asked",
          msg == "" and "Body para, changed." in fake.tab_text("solo.md"),
          msg)

    d = gdocs.first_divergence(["# One", "Two **bold** here.", "Three."],
                               ["# One", "Two bold here.", "Four."])
    check("alignment ignores markup; a plain divergence carries no fix",
          d["index"] == 2 and d["doc"] == "Three." and d["export"] == "Four."
          and d["hard_break_after"] is None, str(d))


LISTED = ("# Solo\n\n"
          "Boundaries:\n\n"
          "- first item\n"
          "- second item\n\n"
          "[Illustration: a lone tracker kneeling]\n\n"
          "[^RD1]: Jung, Seven Sermons.\n\n"
          "Body para.\n")


class _ListAndHardBreakFake(_HardBreakFake):
    """The export joins list items with single newlines (the Doc holds
    one paragraph per bullet) and, as in _HardBreakFake, glues the
    illustration tag to the footnote with a hard line break."""

    def files(self):
        inner = super().files()

        class _Files:
            def __getattr__(self, name):
                return getattr(inner, name)

            def export(self, fileId=None, mimeType=None):
                data = inner.export(fileId=fileId,
                                    mimeType=mimeType).execute()
                text = data.decode("utf-8").replace(
                    "- first item\n\n- second item",
                    "- first item\n- second item")

                class _Req:
                    def execute(self):
                        return text.encode("utf-8")
                return _Req()
        return _Files()


def list_and_join(root: Path) -> None:
    print("F2. a list block and a hard-break join in one tab:")
    units = gdocs._md_units(LISTED)
    check("_md_units splits a list block into its items",
          "- first item" in units and "- second item" in units, str(units))
    export = ("Boundaries:\n\n- first item\n- second item\n\n"
              "[Illustration: a lone tracker kneeling]  \n"
              "[^RD1]: Jung, Seven Sermons.\n\nBody para.\n")
    doc = ["Boundaries:", "first item", "second item",
           "[Illustration: a lone tracker kneeling]",
           "[^RD1]: Jung, Seven Sermons.", "Body para."]
    got = gdocs._split_hard_breaks(gdocs._md_units(export), doc)
    check("list items and the hard-break join both align with the "
          "Doc's paragraphs",
          len(got) == len(doc) and got[1] == "- first item"
          and got[3] == "[Illustration: a lone tracker kneeling]",
          str(got))
    ws, db, manuscript, ms, fake = _fixture(root, "listjoin-ws", LISTED,
                                            fake_cls=_ListAndHardBreakFake)
    (ms / "solo.md").write_text(LISTED.replace("Body para.",
                                               "Body para, changed."))
    try:
        gdocs.diff_push(db, manuscript, "solo.md", fake, fake)
        msg = ""
    except LookupError as err:
        msg = str(err)
    check("the push lands with the list and the join intact",
          msg == "" and "Body para, changed." in fake.tab_text("solo.md")
          and "second item" in fake.tab_text("solo.md"), msg)


class _SoftBreakJoinFake(_SurgicalDocFake):
    """Google's markdown import reads a soft line break inside a
    paragraph as a space: consecutive tag lines with no blank line
    between land as ONE Doc paragraph, space-joined, and the export
    hands them back on one line (picture-story.md, 2026-10-25)."""

    def _paras(self, markdown: str) -> list[str]:
        out = []
        for p in super()._paras(markdown):
            lines = p.split("\n")
            if not all(gdocs._LIST_ITEM.match(l) for l in lines):
                p = " ".join(l.strip() for l in lines)
            out.append(p)
        return out


STACKED_TAB = ("Opening line.\n\n[Illustration: one ⇢ one.md] "
               "[Illustration: two ⇢ two.md]\n\nClosing line.\n")
STACKED_LOCAL = ("Opening line.\n\n[Illustration: one ⇢ one.md]\n"
                 "[Illustration: two ⇢ two.md]\n"
                 "[Illustration: three ⇢ three.md]\n\nClosing line.\n")


def stacked_tags(root: Path) -> None:
    print("F3. stacked tag lines (one markdown paragraph):")
    units = gdocs._md_units(STACKED_LOCAL)
    check("_md_units splits consecutive tag lines into one unit each",
          units[1:4] == ["[Illustration: one ⇢ one.md]",
                         "[Illustration: two ⇢ two.md]",
                         "[Illustration: three ⇢ three.md]"], str(units))
    check("a prose paragraph with a soft break stays one unit",
          len(gdocs._md_units("a line\nand more\n")) == 1)
    ws, db, manuscript, ms, fake = _fixture(root, "stacked-ws", STACKED_TAB,
                                            fake_cls=_SoftBreakJoinFake)
    check("the fake reproduces the space-join in the tab",
          "one.md] [Illustration: two" in fake.tab_text("solo.md"),
          fake.tab_text("solo.md"))
    (ms / "solo.md").write_text(STACKED_LOCAL)
    try:
        out = gdocs.diff_push(db, manuscript, "solo.md", fake, fake)
        msg = ""
    except LookupError as err:
        out, msg = {}, str(err)
    tab = fake.tab_text("solo.md")
    check("diff push lands stacked tags and its read-back agrees",
          msg == "" and out.get("mode") == "diff"
          and "[Illustration: one ⇢ one.md]\n[Illustration: two" in tab
          and "three.md]" in tab, msg + "\n" + tab)
    out = gdocs.diff_push(db, manuscript, "solo.md", fake, fake)
    check("a second push finds nothing to do", out.get("ops") == 0, str(out))


# ------------------------------------------------------------------ I


class _UnspacedJoinFake(_SurgicalDocFake):
    """Google's markdown export joins a paragraph that carries no space
    below to the next one with a hard line break ("  \\n"); measured
    live 2026-10-25 on a scratch Doc. Paragraphs a diff push inserts
    arrive with no spacing until apply_tab_spacing runs, so an export
    read before that sees fewer paragraphs than the tab holds."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.unspaced: set[str] = set()

    def _lines(self, tab_id):
        tab = self._tab(tab_id)
        return set(tab["body"].split("\n")) if tab else set()

    def _apply(self, req):
        if "insertText" in req:
            tab_id = req["insertText"]["location"].get("tabId")
            before = self._lines(tab_id)
            out = super()._apply(req)
            self.unspaced |= {l for l in self._lines(tab_id) - before
                              if l.strip()}
            return out
        if "updateParagraphStyle" in req:
            spec = req["updateParagraphStyle"]
            below = (spec.get("paragraphStyle", {}).get("spaceBelow")
                     or {}).get("magnitude")
            tab = self._tab(spec["range"].get("tabId"))
            if tab is not None and below:
                a, b = spec["range"]["startIndex"], spec["range"]["endIndex"]
                index = 1
                for seg in tab["body"].splitlines(keepends=True):
                    if index < b and index + len(seg) > a:
                        self.unspaced.discard(seg.rstrip("\n"))
                    index += len(seg)
            return {}
        return super()._apply(req)

    def files(self):
        inner = super().files()
        outer = self

        class _Req:
            def __init__(self, data):
                self._data = data

            def execute(self):
                return self._data

        class _Files:
            def __getattr__(self, name):
                return getattr(inner, name)

            def export(self, fileId=None, mimeType=None):
                chunks = []
                for t in outer.tabs:
                    lines = [l for l in outer._markdown_of_tab(t).split("\n")
                             if l.strip()]
                    blocks: list[str] = []
                    glue = False
                    for line in lines:
                        if glue and blocks:
                            blocks[-1] += "  \n" + line
                        else:
                            blocks.append(line)
                        glue = line in outer.unspaced
                    chunks.append(f"# **{t['title']}**\n\n"
                                  + "\n\n".join(blocks)
                                  + ("\n" if t["body"].strip() else ""))
                return _Req("\n".join(chunks).encode("utf-8"))
        return _Files()


class _DroppingFake(_SurgicalDocFake):
    """An export that loses a paragraph outright: no alignment can
    explain it, so the push must stop before it writes anything."""

    def files(self):
        inner = super().files()

        class _Files:
            def __getattr__(self, name):
                return getattr(inner, name)

            def export(self, fileId=None, mimeType=None):
                text = inner.export(fileId=fileId,
                                    mimeType=mimeType).execute().decode()
                text = text.replace("Beta two.\n\n", "")

                class _Req:
                    def execute(self):
                        return text.encode("utf-8")
                return _Req()
        return _Files()


class _DropsAfterWriteFake(_SurgicalDocFake):
    """An export that loses a paragraph only once the push has written:
    the read-back fails after text has landed in the tab."""

    def files(self):
        inner = super().files()
        outer = self

        class _Files:
            def __getattr__(self, name):
                return getattr(inner, name)

            def export(self, fileId=None, mimeType=None):
                text = inner.export(fileId=fileId,
                                    mimeType=mimeType).execute().decode()
                if _text_writes(outer):
                    text = text.replace("Alpha one.\n\n", "")

                class _Req:
                    def execute(self):
                        return text.encode("utf-8")
                return _Req()
        return _Files()


def _text_writes(fake) -> list[str]:
    return [b for b in fake.bodies
            if "insertText" in b or "deleteContentRange" in b]


def spacing_join(root: Path) -> None:
    print("I. diff push survives Google's join of unspaced paragraphs:")
    new = BASE.replace("Beta two.", "Beta changed.\n\nBeta added.")
    ws, db, manuscript, ms, fake = _fixture(root, "join-ws", BASE,
                                            fake_cls=_UnspacedJoinFake)
    (ms / "solo.md").write_text(new)
    try:
        out = gdocs.diff_push(db, manuscript, "solo.md", fake, fake)
        msg = ""
    except LookupError as err:
        out, msg = {}, str(err)
    check("the push that inserts paragraphs lands (it read the export "
          "only after the spacing was applied)",
          msg == "" and out.get("ops", 0) >= 1, msg)
    check("the tab holds the local paragraphs",
          "Alpha one.\nBeta changed.\nBeta added.\nGamma three.\n"
          in fake.tab_text("solo.md")
          and "Beta two." not in fake.tab_text("solo.md"),
          fake.tab_text("solo.md"))
    check("every written paragraph carries spacing, so the tab "
          "round-trips through the export", not fake.unspaced,
          str(fake.unspaced))
    try:
        again = gdocs.diff_push(db, manuscript, "solo.md", fake, fake)
        msg = ""
    except LookupError as err:
        again, msg = {}, str(err)
    check("the next diff push is a clean no-op",
          msg == "" and again.get("ops") == 0, msg or str(again))

    # A tab left unspaced by an earlier push: the export joins two of its
    # paragraphs. The push reads the join as those paragraphs and lands.
    ws, db, manuscript, ms, fake = _fixture(root, "join2-ws", BASE,
                                            fake_cls=_UnspacedJoinFake)
    fake.unspaced.add("Alpha one.")
    (ms / "solo.md").write_text(BASE.replace("Gamma three.",
                                             "Gamma, changed."))
    try:
        gdocs.diff_push(db, manuscript, "solo.md", fake, fake)
        msg = ""
    except LookupError as err:
        msg = str(err)
    check("a tab whose export already joins two paragraphs still pushes",
          msg == "" and "Gamma, changed." in fake.tab_text("solo.md")
          and "Alpha one.\nBeta two.\n" in fake.tab_text("solo.md"), msg)

    # Validation precedes every text write: a disagreement no alignment
    # explains stops the push with the tab untouched.
    ws, db, manuscript, ms, fake = _fixture(root, "drop-ws", BASE,
                                            fake_cls=_DroppingFake)
    before = fake.tab_text("solo.md")
    (ms / "solo.md").write_text(BASE.replace("Gamma three.",
                                             "Gamma, changed."))
    try:
        gdocs.diff_push(db, manuscript, "solo.md", fake, fake)
        msg = ""
    except LookupError as err:
        msg = str(err)
    check("an unexplained disagreement stops the push",
          "tab structure and export disagree" in msg, msg)
    check("...having written no text to the tab",
          not _text_writes(fake) and fake.tab_text("solo.md") == before,
          str(_text_writes(fake)))
    check("...and the message does not send the author to hand-edit "
          "the Doc", "in the Doc" not in msg and "Nothing was written"
          in msg, msg)

    # A disagreement found AFTER this push wrote is not "nothing was
    # written": the message says the push wrote and where to look.
    ws, db, manuscript, ms, fake = _fixture(root, "drop-after-ws", BASE,
                                            fake_cls=_DropsAfterWriteFake)
    (ms / "solo.md").write_text(BASE.replace("Gamma three.",
                                             "Gamma, changed."))
    try:
        gdocs.diff_push(db, manuscript, "solo.md", fake, fake)
        msg = ""
    except LookupError as err:
        msg = str(err)
    check("a disagreement after a write stops the push",
          bool(_text_writes(fake)) and "disagree" in msg, msg)
    check("...and says this push wrote to the tab, never 'Nothing was "
          "written'", "Nothing was written" not in msg
          and "this push wrote" in msg.lower()
          and "authorlm doc diff solo.md" in msg, msg)

    # The settle step (forms whose verdict is in the record but still
    # stand in the tab) is a text write too: it waits for validation.
    from authorlm.db import ko_fields
    ws, db, manuscript, ms, fake = _fixture(root, "drop-settle-ws", BASE,
                                            fake_cls=_DroppingFake)
    fake.edit("tab-2", "Alpha one.", "<<Alpha one.>>{{Alpha new.}}")
    thread = ko_fields("dt")
    thread.update(
        manuscript_id=manuscript["id"], origin_type="filter",
        origin_id="fr-test:solo.md:1", file="solo.md", anchor_quote=None,
        proposed_old="Alpha one.", proposed_new="Alpha new.", note="test",
        state="cleaned", our_reply_ids="[]", last_author_reply_id=None,
        scope_kind="file", scope_ref="solo.md",
        metadata=json.dumps({"kind": "replace"}))
    db.insert("doc_threads", thread)
    before = fake.tab_text("solo.md")
    (ms / "solo.md").write_text(BASE.replace("Alpha one.", "Alpha new.")
                                .replace("Gamma three.", "Gamma, changed."))
    try:
        gdocs.diff_push(db, manuscript, "solo.md", fake, fake)
        msg = ""
    except LookupError as err:
        msg = str(err)
    check("a disagreement stops the push before the settle step",
          "tab structure and export disagree" in msg, msg)
    check("...the settled form still stands; no text was written",
          not _text_writes(fake) and fake.tab_text("solo.md") == before,
          str(_text_writes(fake)))


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


class _TabTreeStub:
    """Just enough of the Docs API for sync_tab_structure: one container
    tab whose children are the titles given, in order; index moves among
    those children are applied and recorded."""

    def __init__(self, titles: list[str]):
        self.children = list(titles)
        self.moves: list[dict] = []
        stub = self

        class _Req:
            def __init__(self, fn):
                self.fn = fn

            def execute(self):
                return self.fn()

        class _Documents:
            def get(self, documentId=None, includeTabsContent=None, fields=None):
                return _Req(lambda: {"tabs": [{
                    "tabProperties": {"tabId": "tab-c", "title": "book"},
                    "childTabs": [{"tabProperties": {
                        "tabId": f"tab-{t}", "title": t,
                        "parentTabId": "tab-c"}}
                        for t in stub.children]}]})

            def batchUpdate(self, documentId=None, body=None):
                def run():
                    for r in body["requests"]:
                        props = r["updateDocumentTabProperties"][
                            "tabProperties"]
                        stub.moves.append(props)
                        title = props["tabId"][len("tab-"):]
                        stub.children.remove(title)
                        stub.children.insert(props["index"], title)
                    return {}
                return _Req(run)
        self._docs = _Documents()

    def documents(self):
        return self._docs


def toc_doc_only_tabs(root: Path) -> None:
    print("G2. tabs toc.toml does not list leave tab-order sync alone:")
    ws = root / "toc2-ws"
    ms = ws / "book"
    ms.mkdir(parents=True)
    (ms / "toc.toml").write_text(
        '[[chapter]]\nfile = "a.md"\n\n[[chapter]]\nfile = "new.md"\n'
        '\n[[chapter]]\nfile = "z.md"\n')
    for name in ("a.md", "new.md", "z.md", "extra.md"):
        (ms / name).write_text(f"# {name}\n\nBody.\n")
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms), "--extraction", "off"])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    meta = gdocs._mapping(db, manuscript)
    links = meta.setdefault("gdocs", {})
    links["_master_id"] = "doc-fake"
    links["_container_tab"] = "tab-c"
    for name in ("a.md", "new.md", "z.md", "extra.md", "gone.md"):
        links[name] = {"tab_id": f"tab-{name}", "checked_out": True}
    # The base predates new.md; a push appended new.md's tab after z.md;
    # extra.md and gone.md have tabs but no toc.toml entry.
    links["_tab_structure"] = [["a.md", None], ["z.md", None]]
    gdocs._save_mapping(db, manuscript, meta)
    stub = _TabTreeStub(["a.md", "z.md", "new.md", "extra.md", "gone.md"])
    got = gdocs.sync_tab_structure(db, manuscript, stub)
    check("no conflict: the push moves new.md to its toc.toml place",
          got.get("moved", 0) >= 1 and "skipped" not in got
          and stub.children[:3] == ["a.md", "new.md", "z.md"],
          f"{got} {stub.children}")
    again = gdocs.sync_tab_structure(db, manuscript, stub)
    check("the next push finds the order in sync",
          again.get("insync") is True, str(again))
    stub.children = ["z.md", "a.md", "new.md", "extra.md", "gone.md"]
    (ms / "toc.toml").write_text(
        '[[chapter]]\nfile = "new.md"\n\n[[chapter]]\nfile = "a.md"\n'
        '\n[[chapter]]\nfile = "z.md"\n')
    both = gdocs.sync_tab_structure(db, manuscript, stub)
    check("a real two-sided reorder is still a conflict",
          "also changed (conflict)" in both.get("skipped", ""), str(both))


# ------------------------------------------------------------------ J


def _mask_tree(fields: str) -> dict:
    """A partial-response `fields` string as a tree: {name: subtree or
    None (the whole value)}. `a/b` is `a(b)`, as Google reads it."""
    def parse(s: str, i: int) -> tuple[dict, int]:
        tree: dict = {}
        name = ""
        while i < len(s):
            ch = s[i]
            if ch == "(":
                sub, i = parse(s, i + 1)
                _put(tree, name, sub)
                name = ""
            elif ch == ")":
                if name:
                    _put(tree, name, None)
                return tree, i + 1
            elif ch == ",":
                if name:
                    _put(tree, name, None)
                name = ""
                i += 1
            else:
                name += ch
                i += 1
        if name:
            _put(tree, name, None)
        return tree, i

    def _put(tree: dict, path: str, sub) -> None:
        head, _, rest = path.strip().partition("/")
        if rest:
            node = tree.setdefault(head, {})
            if node is not None:
                _put(node, rest, sub)
        else:
            tree[head] = sub

    return parse(fields, 0)[0]


def _prune(value, tree):
    """`value` as Google returns it under the mask `tree`."""
    if tree is None:
        return value
    if isinstance(value, list):
        return [_prune(v, tree) for v in value]
    if isinstance(value, dict):
        return {k: _prune(value[k], sub) for k, sub in tree.items()
                if k in value}
    return value


class _CountingFake(_UnspacedJoinFake):
    """The surgical double with what the read budget turns on: a
    revisionId that moves on every change to the Doc (any tab, any
    text, any spacing — computed from the state, so a test that edits
    the tab by hand moves it too), `writeControl` checked and answered,
    partial-response masks applied (a field the code reads but did not
    ask for comes back missing, as from Google), paragraph spacing
    visible in `paragraphStyle`, paged comments, and a log of every call
    as (kind, revision): rev, get, temp, export, write, create, delete,
    comments."""

    def __init__(self, *a, comments=None, **k):
        super().__init__(*a, **k)
        self.calls: list[tuple[str, str]] = []
        self.margin = list(comments or [])
        self.hooks: dict = {}          # kind → callable run after the call
        self.masks: list = []          # each master read's fields string

    def rev(self) -> str:
        state = json.dumps([[t["id"], t["title"], t["body"]]
                            for t in self.tabs]
                           + [sorted(self.unspaced)]
                           + [sorted((r["tab"], r["start"], r["end"],
                                      r["mark"]) for r in self.styled)])
        return "rev-" + hashlib.sha256(state.encode()).hexdigest()[:10]

    def log(self, kind: str) -> None:
        self.calls.append((kind, self.rev()))
        hook = self.hooks.pop(kind, None)
        if hook is not None:
            hook()

    def count(self, kind: str) -> int:
        return sum(1 for k, _ in self.calls if k == kind)

    def reset(self) -> None:
        self.calls = []

    def _spaced(self, doc: dict) -> dict:
        def walk(tabs):
            for tab in tabs:
                for item in tab.get("documentTab", {}).get("body", {}).get(
                        "content", []):
                    para = item.get("paragraph")
                    if not para:
                        continue
                    text = "".join(e.get("textRun", {}).get("content", "")
                                   for e in para.get("elements", []))
                    style = para.setdefault("paragraphStyle", {})
                    if text.rstrip("\n") not in self.unspaced:
                        # Google leaves a zero magnitude out.
                        style.update(lineSpacing=115,
                                     spaceAbove={"unit": "PT"},
                                     spaceBelow={"magnitude": 6,
                                                 "unit": "PT"})
                walk(tab.get("childTabs", []))
        walk(doc.get("tabs", []))
        return doc

    def documents(self):
        inner = super().documents()
        outer = self

        class _Req:
            def __init__(self, result):
                self._result = result

            def execute(self):
                return self._result

        class _Documents:
            def get(self, documentId=None, includeTabsContent=None,
                    fields=None):
                if documentId in outer.temps:
                    outer.log("temp")
                    return inner.get(documentId=documentId)
                if fields == "revisionId":
                    outer.log("rev")
                    return _Req({"revisionId": outer.rev()})
                outer.log("get")
                outer.masks.append(fields)
                doc = inner.get(documentId=documentId,
                                includeTabsContent=includeTabsContent
                                ).execute()
                doc = outer._spaced(copy.deepcopy(doc))
                doc["revisionId"] = outer.rev()
                if fields:
                    doc = _prune(doc, _mask_tree(fields))
                return _Req(doc)

            def batchUpdate(self, documentId=None, body=None):
                required = ((body or {}).get("writeControl") or {}).get(
                    "requiredRevisionId")
                if required and required != outer.rev():
                    import httplib2
                    from googleapiclient.errors import HttpError
                    raise HttpError(
                        httplib2.Response({"status": 400}),
                        b'{"error": {"message": "The required revision '
                        b'ID does not match the latest revision."}}')
                reply = inner.batchUpdate(documentId=documentId,
                                          body=body).execute()
                outer.log("write")
                reply["writeControl"] = {"requiredRevisionId": outer.rev()}
                return _Req(reply)
        return _Documents()

    def files(self):
        inner = super().files()
        outer = self

        class _Files:
            def __getattr__(self, name):
                return getattr(inner, name)

            def export(self, fileId=None, mimeType=None):
                got = inner.export(fileId=fileId, mimeType=mimeType)
                outer.log("export")
                return got

            def create(self, **kw):
                outer.log("create")
                return inner.create(**kw)

            def delete(self, **kw):
                outer.log("delete")
                return inner.delete(**kw)
        return _Files()

    def comments(self):
        outer = self

        class _Req:
            def __init__(self, result):
                self._result = result

            def execute(self):
                return self._result

        class _Comments:
            def list(self, fileId=None, pageToken=None, pageSize=None,
                     includeDeleted=None, fields=None):
                outer.log("comments")
                size = min(pageSize or 20, 100)
                at = int(pageToken or 0)
                page = outer.margin[at: at + size]
                got = {"comments": copy.deepcopy(page)}
                if at + size < len(outer.margin):
                    got["nextPageToken"] = str(at + size)
                return _Req(got)
        return _Comments()


def _comment(cid: str, quoted: str) -> dict:
    return {"id": cid, "content": "a note", "resolved": False,
            "quotedFileContent": {"value": quoted},
            "author": {"displayName": "author"},
            "createdTime": f"2026-10-30T00:00:{cid[-2:]}Z", "replies": []}


def _settled(db, manuscript, old: str, new: str) -> None:
    """A filter form whose verdict is in the record ('cleaned'), its
    markers already gone from the tab: what makes every push of a file
    with history run the settle step (`form_news` is never empty)."""
    from authorlm.db import ko_fields
    thread = ko_fields("dt")
    thread.update(
        manuscript_id=manuscript["id"], origin_type="filter",
        origin_id=f"fr-test:solo.md:{old}", file="solo.md",
        anchor_quote=None, proposed_old=old, proposed_new=new, note="t",
        state="cleaned", our_reply_ids="[]", last_author_reply_id=None,
        scope_kind="file", scope_ref="solo.md",
        metadata=json.dumps({"kind": "replace"}))
    db.insert("doc_threads", thread)


def _budget(fake, label: str) -> str:
    kinds = ("rev", "get", "export", "comments", "write", "temp")
    return f"{label}: " + ", ".join(f"{k}={fake.count(k)}" for k in kinds)


def read_budget(root: Path) -> None:
    """The Doc road's Google read budget (2026-10-30 profiles: a push
    exported the whole Doc four times at one revision, a stage did 20
    full reads). Each scenario checks the outcome first, then the
    number of calls, so a cache that answers wrongly fails before a
    count can pass."""
    print("J. the Doc road reads each revision once:")
    many = [_comment(f"c-{i:02d}", "Alpha one.") for i in range(25)]

    # 1. A guarded push with history (the settle step runs and finds
    #    nothing to take out) and a margin of 25 comments.
    ws, db, manuscript, ms, fake = _fixture(root, "budget-push-ws",
                                            fake_cls=_CountingFake)
    fake.margin = many
    _settled(db, manuscript, "Gamma old.", "Gamma three.")
    (ms / "solo.md").write_text(BASE.replace("Beta two.", "Beta, mine."))
    fake.reset()
    out = gdocs.push_doc(db, manuscript, "solo.md", service=fake,
                         docs_service=fake, guard=True)
    check("the push lands the local edit", out.get("mode") == "diff"
          and "Beta, mine." in fake.tab_text("solo.md")
          and "Beta two." not in fake.tab_text("solo.md"),
          fake.tab_text("solo.md"))
    print("   " + _budget(fake, "push"))
    check("two exports: the guard's and the read-back after the write",
          fake.count("export") == 2, _budget(fake, "push"))
    check("one full read per revision the push meets (three: before "
          "the edit, after it, after the spacing)",
          fake.count("get") <= 3, _budget(fake, "push"))
    check("one page of comments, read once", fake.count("comments") == 1,
          _budget(fake, "push"))
    check("every master read went out with a fields mask",
          fake.masks and all(fake.masks), str(fake.masks))

    # 2. The read-back is a fresh export after the write, and the spacing
    #    write precedes it (the 2026-10-25 order, 746d8c84).
    kinds = [k for k, _ in fake.calls]
    last_write = max(i for i, k in enumerate(kinds) if k == "write")
    last_export = max(i for i, k in enumerate(kinds) if k == "export")
    revs = [r for k, r in fake.calls if k == "export"]
    check("the read-back export comes after the last write, at a new "
          "revision", last_export > last_write and revs[0] != revs[-1],
          str(fake.calls))

    # 3. Spacing writes only what lacks it.
    fake.reset()
    sent = gdocs.apply_tab_spacing(fake, "doc-fake", "tab-2",
                                   gdocs.DOC_SPACING)
    check("a tab already spaced takes no spacing write",
          sent == 0 and fake.count("write") == 0, str(fake.calls))
    fake.unspaced.add("Gamma three.")
    fake.reset()
    sent = gdocs.apply_tab_spacing(fake, "doc-fake", "tab-2",
                                   gdocs.DOC_SPACING)
    check("one unspaced paragraph: one range, one write",
          sent == 1 and fake.count("write") == 1 and not fake.unspaced,
          str(fake.calls))

    # 4. A revision that moves between two reads is read again.
    fake.reset()
    first = gdocs.master_export(fake, fake, "doc-fake")
    again = gdocs.master_export(fake, fake, "doc-fake")
    check("the same revision is exported once",
          first == again and fake.count("export") == 1, str(fake.calls))
    fake.edit("tab-2", "Gamma three.", "Gamma, typed in the Doc.")
    moved = gdocs.master_export(fake, fake, "doc-fake")
    check("an edit in the Doc forces a fresh export",
          fake.count("export") == 2 and "Gamma, typed in the Doc." in moved
          and "Gamma, typed in the Doc." not in first, str(fake.calls))
    doc = gdocs.master_doc(fake, "doc-fake")
    fake.edit("tab-2", "Alpha one.", "Alpha, typed.")
    doc2 = gdocs.master_doc(fake, "doc-fake")
    check("...and a fresh full read",
          doc2.get("revisionId") != doc.get("revisionId")
          and "Alpha, typed." in json.dumps(doc2), "")

    # 5. Pull, then push in a new process: the pull's export, kept on
    #    disk by revision, stands in for the push guard's.
    ws, db, manuscript, ms, fake = _fixture(root, "budget-disk-ws",
                                            fake_cls=_CountingFake)
    fake.edit("tab-2", "Gamma three.", "Gamma, from the Doc.")
    gdocs.pull_doc(db, manuscript, query="solo.md", service=fake,
                   docs_service=fake, with_comments=False)
    check("the pull brought the Doc's edit home",
          "Gamma, from the Doc." in (ms / "solo.md").read_text(), "")
    links = gdocs._mapping(db, manuscript)
    links["gdocs"]["solo.md"]["checked_out"] = True
    gdocs._save_mapping(db, manuscript, links)
    (ms / "solo.md").write_text((ms / "solo.md").read_text().replace(
        "Beta two.", "Beta, mine."))
    gdocs._READS.clear()                         # a new process
    fake.margin = [_comment("c-01", "Alpha one.")]
    fake.reset()
    gdocs.push_doc(db, manuscript, "solo.md", service=fake,
                   docs_service=fake, guard=True)
    print("   " + _budget(fake, "push after pull"))
    check("the push after a pull exports once (the read-back)",
          fake.count("export") == 1
          and "Beta, mine." in fake.tab_text("solo.md"),
          _budget(fake, "push after pull"))

    # 6. A push whose edit meets a margin proposal stops before Google.
    ws, db, manuscript, ms, fake = _fixture(root, "budget-overlap-ws",
                                            fake_cls=_CountingFake)
    from authorlm import threads as th
    fake.edit("tab-2", "Beta two.", "<<Beta two.>>{{Beta, proposed.}}")
    fake.margin = [_comment("c-01", "Beta two.")]
    th.create_thread(db, manuscript["id"], "c-01", "solo.md", "Beta two.",
                     "Beta two.", "Beta, proposed.", "n", None,
                     scope_kind="file", scope_ref="solo.md")
    before = fake.tab_text("solo.md")
    (ms / "solo.md").write_text(BASE.replace("Beta two.", "Beta, mine."))
    fake.reset()
    try:
        gdocs.push_doc(db, manuscript, "solo.md", service=fake,
                       docs_service=fake, guard=True)
        msg = ""
    except LookupError as err:
        msg = str(err)
    check("the overlap is named, as the tab check names it",
          "overlaps a pending margin thread" in msg, msg)
    check("...and no Google call was made, nothing written",
          fake.calls == [] and fake.tab_text("solo.md") == before,
          str(fake.calls))
    (ms / "solo.md").write_text(BASE.replace("Gamma three.",
                                             "Gamma, mine."))
    fake.reset()
    gdocs.push_doc(db, manuscript, "solo.md", service=fake,
                   docs_service=fake, guard=True)
    check("an edit beside the proposal still pushes; the form stands",
          "Gamma, mine." in fake.tab_text("solo.md")
          and "<<Beta two.>>{{Beta, proposed.}}" in fake.tab_text("solo.md"),
          fake.tab_text("solo.md"))

    # 7. revise stage, then resolve: outcomes, then the budget.
    ws, db, manuscript, ms, fake = _fixture(root, "budget-revise-ws",
                                            OUTSIDE, fake_cls=_CountingFake)
    fake.margin = many
    revisions = [
        {"old": "Beta is to be replaced.", "new": "Beta, replaced.",
         "note": "r-1"},
        {"old": "Gamma is to be reworded.", "new": "Gamma, as proposed.",
         "note": "r-2"},
        {"old": "", "new": "Delta, added.", "note": "r-3",
         "anchor_paragraph": 2},
    ]
    fake.reset()
    staged = api.stage_revisions(db, manuscript, "solo.md", revisions,
                                 "budget", fake, fake)
    print("   " + _budget(fake, "stage, 3 forms"))
    check("stage writes all three forms",
          len(staged["written"]) == 3 and not staged["failed"]
          and fake.tab_text("solo.md").count("{{") == 3,
          str(staged["failed"]) + fake.tab_text("solo.md"))
    check("stage: one export for the pull, one for the export proof at "
          "most", fake.count("export") <= 2, _budget(fake, "stage"))
    check("stage: one full read per form written, plus at most four",
          fake.count("get") <= 3 + 4, _budget(fake, "stage"))
    fake.edit("tab-2", "{{Gamma, as proposed.}}", "{{Gamma, my words.}}")
    fake.reset()
    with contextlib.redirect_stdout(io.StringIO()):
        done = api.resolve_revisions(db, manuscript, "solo.md", "budget",
                                     fake, fake)
    print("   " + _budget(fake, "resolve, 3 forms"))
    final = (ms / "solo.md").read_text()
    check("resolve lands all three, the author's wording kept",
          done["landed"] and "Beta, replaced." in final
          and "Gamma, my words." in final and "Delta, added." in final
          and not done["tab_still_marked"]
          and "{{" not in fake.tab_text("solo.md"),
          json.dumps(done, default=str) + fake.tab_text("solo.md"))
    check("resolve: at most three exports (its read, the push's read "
          "after the forms come out, the read-back)",
          fake.count("export") <= 3, _budget(fake, "resolve"))
    check("resolve: one page of comments per fetch, one fetch",
          fake.count("comments") <= 1, _budget(fake, "resolve"))


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
        list_and_join(root)
        stacked_tags(root)
        spacing_join(root)
        toc_conflict(root)
        toc_doc_only_tabs(root)
        read_budget(root)
        run_tests_script(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    main_test()
