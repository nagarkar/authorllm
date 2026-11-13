"""Shared readings: one reading per essay per filter family (readings.py,
issue #412).

Hermetic: a temp workspace, a temp filter library, and preludes driven by
piped replies (the chat path), so no model is called.
"""

from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import contextlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import api  # noqa: E402
from authorlm import filters as flt  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.db import loads  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: object = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


def _artifact(reading: str | None = "fam", klass: str = "global") -> str:
    line = f'reading = "{reading}"\n' if reading else ""
    return f'---\nclass = "{klass}"\n{line}---\n\n# A filter\n\nThe prompt.\n'


def _reading(possessive: str, mode: str = "narration") -> str:
    return (f"mode: {mode}\nregister: general\nconventions:\n"
            f"  serial-comma: yes\n  possessive-s: {possessive}\n"
            f"  dash: law\n  italics: titles\n"
            f"out of scope: verse")


def _reply(registry: str) -> str:
    return json.dumps({"registry": registry})


def check_library_filters_declare_reading() -> None:
    old = os.environ.pop(flt.LIBRARY_ENV, None)
    try:
        for name in ("concision", "grammar", "mechanics"):
            meta, body = flt.load_filter(
                {"path": "/nonexistent"}, f"strunk-and-white-{name}")
            check(f"strunk-and-white-{name} declares the shared reading",
                  meta["reading"] == "strunk-and-white")
            check(f"{name}: no leftover refresh or book-check wording",
                  "PRIOR SHARED READING" not in body
                  and "mixed in the book" not in body)
    finally:
        if old is not None:
            os.environ[flt.LIBRARY_ENV] = old


def check_front_matter() -> None:
    try:
        flt.parse_front_matter(_artifact(klass="sequential"))
        check("reading on a sequential filter is rejected", False)
    except flt.FilterError:
        check("reading on a sequential filter is rejected", True)
    try:
        flt.parse_front_matter(_artifact(reading="Not A Slug"))
        check("a reading name must be a slug", False)
    except flt.FilterError:
        check("a reading name must be a slug", True)
    meta, _ = flt.parse_front_matter(_artifact(reading=None))
    check("a global filter without reading has reading None",
          meta["reading"] is None)


def check_prelude_flow() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-readings-"))
    old = os.environ.get(flt.LIBRARY_ENV)
    try:
        lib = root / "lib"
        lib.mkdir()
        for name in ("fam-one", "fam-two", "fam-three"):
            (lib / f"{name}.md").write_text(_artifact())
        (lib / "solo.md").write_text(_artifact(reading=None))
        os.environ[flt.LIBRARY_ENV] = str(lib)

        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "a.md").write_text("# A\n\nMoses' staff.\n\nThe end.\n")
        (ms / "b.md").write_text("# B\n\nCharles's hat.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms)])
        db = api.open_db(str(ws))
        man = api.get_manuscript(db)
        mid = man["id"]

        def prelude(name, file, reply=None, replace=False):
            return api.filter_prelude(db, man, {}, name, file,
                                      replace=replace, reply=reply)

        def currents(file):
            return db.all("SELECT * FROM filter_readings WHERE "
                          "manuscript_id = ? AND file = ? AND "
                          "status = 'current'", (mid, file))

        first = prelude("fam-one", "a.md", _reply(_reading("s'")))
        check("the first prelude records the shared reading",
              first["reading"]["reused"] is False and len(currents("a.md"))
              == 1 and first["registry"] == _reading("s'"), first["reading"])

        second = prelude("fam-two", "a.md")
        run2 = api._active_filter_run(db, mid, "fam-two", "a.md")
        check("a second pass on the same text reuses it: no payload, the "
              "same bytes frozen into its own run",
              second["payload"] is None and second["reading"]["reused"]
              and run2["registry"] == _reading("s'")
              and loads(run2["metadata"], {})["reading"]["reused"],
              second)

        (ms / "a.md").write_text("# A\n\nMoses' staff.\n\nThe very end.\n")
        third = prelude("fam-three", "a.md")
        check("edited text does not refresh the reading: the third pass "
              "reuses it too", third["payload"] is None
              and third["reading"]["reused"]
              and third["registry"] == _reading("s'"), third)

        api.filter_abandon(db, man, "a.md", "fam-three")
        fresh = prelude("fam-three", "a.md", replace=True)
        check("--replace reads the essay again (payload, nothing frozen yet)",
              fresh["registry"] is None and fresh["payload"] is not None)
        prelude("fam-three", "a.md", _reply(_reading("s'", mode="told tale")),
                replace=True)
        rows = db.all("SELECT status FROM filter_readings WHERE "
                      "manuscript_id = ? AND file = 'a.md'", (mid,))
        check("recording supersedes, never edits: one current, one "
              "superseded", sorted(r["status"] for r in rows)
              == ["current", "superseded"], [dict(r) for r in rows])
        run1 = api._active_filter_run(db, mid, "fam-one", "a.md")
        check("a live run keeps the copy it took",
              run1["registry"] == _reading("s'"), run1["registry"])
        check("the next pass gets the replaced reading",
              "told tale" in currents("a.md")[0]["registry"])

        solo = prelude("solo", "a.md", _reply("mode: argument"))
        check("a global filter with no reading family behaves as before",
              "reading" not in solo and len(currents("a.md")) == 1)

        b = prelude("fam-one", "b.md", _reply(_reading("s's")))
        check("each essay has its own reading",
              b["reading"]["reused"] is False
              and "possessive-s: s's" in currents("b.md")[0]["registry"]
              and "told tale" in currents("a.md")[0]["registry"])
    finally:
        if old is None:
            os.environ.pop(flt.LIBRARY_ENV, None)
        else:
            os.environ[flt.LIBRARY_ENV] = old
        shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    for t in (check_library_filters_declare_reading, check_front_matter,
              check_prelude_flow):
        print(f"{t.__name__}:")
        t()
    print(f"ALL CHECKS PASSED ({PASSED})")


if __name__ == "__main__":
    main()
