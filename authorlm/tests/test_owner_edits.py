"""A file only the owner edits: `edits = "owner"` on its toc.toml entry.

Source: the owner's ruling of 2026-11-25 on the Sermons: "I don't want
to change the sermons at all at this stage. All further edits there will
be by me manually." The existing `register = "protected"` keeps lenses off
a file's voice and nothing else (filters, lint fixes, revise stage, agent
landings and the critique pass all still wrote to it), so the flag is a
second toc key, checked by every automated write road. Each road declines
for a flagged file and says why; the Filters and Lint views show the
reason. The owner's own edits in the Doc and `doc pull` are not roads an
automated pass takes and are not checked.

Hermetic: a temp workspace; any Google or model call fails the test.
Run: python3 tests/test_owner_edits.py
"""
from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import contextlib  # noqa: E402
import io  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import api, changes, devedit, gdocs, lenses, lint_page, llm  # noqa: E402
from authorlm import passes, runs_page, staging, structure  # noqa: E402
from authorlm import filters as flt  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: object = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


TOC = ('[[chapter]]\nfile = "a.md"\n\n'
       '[[chapter]]\nfile = "s.md"\nregister = "protected"\nedits = "owner"\n')
ESSAY = "# Alpha\n\nFirst unit of alpha.\n\nSecond unit of alpha.\n"
SERMON = "# Sermon\n\nFirst unit of the sermon.\n\nSecond unit -- of the sermon.\n"
FILTER = '---\nclass = "sequential"\n---\n\n# Own pass\n\nThe prompt.\n'
LENS = "# Plain reader\n\nFlag what a plain reader would stumble on.\n"


@contextlib.contextmanager
def _world():
    root = Path(tempfile.mkdtemp(prefix="authorlm-owner-edits-")).resolve()
    saved = (gdocs.get_service, gdocs.get_docs_service, llm.LLMClient)
    saved_env = {k: os.environ.get(k) for k in (flt.LIBRARY_ENV, lenses.LIBRARY_ENV)}

    def boom(*_a, **_k):
        raise AssertionError("an outside service was asked for")
    try:
        (root / "flib").mkdir()
        (root / "llib").mkdir()
        os.environ[flt.LIBRARY_ENV] = str(root / "flib")
        os.environ[lenses.LIBRARY_ENV] = str(root / "llib")
        ws, ms = root / "ws", root / "ws" / "manuscript"
        ms.mkdir(parents=True)
        (ms / "toc.toml").write_text(TOC)
        (ms / "a.md").write_text(ESSAY)
        (ms / "s.md").write_text(SERMON)
        (ms / "_filters").mkdir()
        (ms / "_filters" / "own-pass.md").write_text(FILTER)
        (ms / "_lenses").mkdir()
        (ms / "_lenses" / "own-lens.md").write_text(LENS)
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms)])
        gdocs.get_service = gdocs.get_docs_service = boom
        llm.LLMClient = boom
        db = api.open_db(str(ws))
        man = api.get_manuscript(db)
        yield ws, Path(man["path"]), db, man
    finally:
        gdocs.get_service, gdocs.get_docs_service, llm.LLMClient = saved
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(root, ignore_errors=True)


def _declines(label: str, fn, *, says: str = "s.md") -> None:
    try:
        fn()
    except (ValueError, RuntimeError) as err:
        text = str(err)
        check(f"{label} declines and says why", says in text
              and 'edits = "owner"' in text, text)
        return
    check(f"{label} declines", False, "it wrote")


def check_the_flag() -> None:
    check("the flagged file is listed",
          structure.owner_edits_files(TOC) == ["s.md"])
    why = structure.owner_edits_reason(TOC, "s.md")
    check("its reason names the file, the key and the owner's hand",
          why is not None and "s.md" in why and 'edits = "owner"' in why
          and "by hand" in why, why)
    check("a nested path matches by file name",
          structure.owner_edits_reason(TOC, "part/s.md") is not None)
    check("an unflagged file has no reason",
          structure.owner_edits_reason(TOC, "a.md") is None)
    check("no toc, no flag", structure.owner_edits_reason("", "s.md") is None)


def check_every_road_declines() -> None:
    with _world() as (ws, ms, db, man):
        before = (ms / "s.md").read_text()
        _declines("filter run", lambda: api.filter_run(
            db, man, {}, "own-pass", "s.md"))
        _declines("filter record (staging)", lambda: staging.stage_edits(
            db, man["id"], "run-x", "s.md", SERMON,
            [{"n": 2, "new": "Changed.", "why": "w"}]))
        _declines("lens run", lambda: lenses.assemble(db, man, "own-lens", "s.md"))
        _declines("the critique pass", lambda: passes.build_context(
            db, man, "s.md", {"id": "p-x"}))
        out = api.stage_revisions(db, man, "s.md",
                                  [{"old": "First unit", "new": "Changed",
                                    "note": "n"}], "harvest", None, None)
        reasons = {why for _r, why in out["failed"]}
        check("revise stage holds every revision and says why",
              not out["written"] and len(out["failed"]) == 1
              and all('edits = "owner"' in w for w in reasons), out)
        _declines("an agent step's landing", lambda: changes._require_writable(
            db, man, "s.md", {}, str(ws), lambda _rel: "same"))
        _declines("a developmental edit's apply", lambda: devedit.apply_item(
            man, {"id": "d1", "file": "s.md", "original": "First unit",
                  "proposed": "Changed"}))
        res = lint_page.apply_tab(db, man, "s.md",
                                  [{"id": "f1", "kind": "untrack", "file": "s.md",
                                    "rule": "quote-style"}], None, None)
        check("a lint fix (one by one or bulk) is not applied and says why",
              not res["applied"] and res["blocked"]
              and 'edits = "owner"' in res["blocked"][0]["reason"], res)
        linted = lint_page.run(db, man, ["a.md", "s.md"])
        check("lint lists no findings for the flagged tab and says why",
              all(f.get("file") != "s.md" for g in linted["groups"]
                  for f in g["findings"])
              and 'edits = "owner"' in linted["held"].get("s.md", ""), linted)
        files = runs_page.read_manuscript_files(ms)
        tabs = {t["file"]: t for t in runs_page.tabs(db, man, files)}
        for t in tabs.values():
            t["blocked"] = runs_page.tab_blocker(t)
        check("the Filters view shows the flagged tab as not available, with the reason",
              'edits = "owner"' in (tabs["s.md"]["blocked"] or "")
              and tabs["a.md"]["blocked"] is None, tabs)
        check("the tab row carries the reason for the Lint view",
              'edits = "owner"' in (tabs["s.md"].get("owner_edits") or "")
              and tabs["a.md"].get("owner_edits") is None)
        artifact = {"name": "own-pass", "error": None}
        why = runs_page.blocker(db, man, "filter", artifact, tabs["s.md"], SERMON)
        check("a filter run request on the flagged tab is not queued",
              'edits = "owner"' in (why or ""), why)
        check("the file is byte for byte what it was",
              (ms / "s.md").read_text() == before)
        # The unflagged essay still takes a run (the flag is per file).
        payload = api.filter_run(db, man, {}, "own-pass", "a.md")
        check("an unflagged file still runs", payload is not None)


def main() -> None:
    for fn in (check_the_flag, check_every_road_declines):
        print(f"{fn.__name__}:")
        fn()
    print(f"OK — {PASSED} owner-edits checks")


if __name__ == "__main__":
    main()
