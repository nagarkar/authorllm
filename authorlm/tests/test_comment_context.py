"""Comment context on the pull (it adds where a Doc comment's anchor sits
and the summaries already fresh there, never building one) and
`summarize put` (a section summary written by the session, stamped with
the section's hash, refused without the four labels). No LLM.

Run: python3 tests/test_comment_context.py
"""

from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import contextlib
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _offline_prices  # noqa: E402,F401

from authorlm import api, summaries as sums  # noqa: E402
from authorlm.gdocs import _flat_prose  # noqa: E402
from test_summary_hierarchy import ESSAY  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


GOOD = ("POINT: The Field is the remainder.\n\nMOVES:\n[3] [4] [5] Defines.\n\n"
        "DEPENDS ON: nothing.\n\nLEAVES OPEN: nothing.")


def main_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-cctx-"))
    try:
        ws = root / "ws"
        ms = ws / "book"
        ms.mkdir(parents=True)
        (ms / "toc.toml").write_text('[[chapter]]\nfile = "doors.md"\n')
        (ms / "doors.md").write_text(ESSAY)
        db = api.open_db(str(ws), create=True)
        manuscript = api.register_manuscript(db, "book", str(ms))
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, manuscript, {}, source="test")
        quote = "a Choice specializes the Field"

        print("comment context:")
        ctx = sums.comment_context(db, manuscript, "doors.md", ESSAY, quote,
                                   _flat_prose)
        check("the anchor's paragraph and section are found",
              ctx["section"] == 2 and ctx["heading"] == "## First Section",
              str(ctx))
        check("no summary yet: both None, nothing built",
              ctx["section_summary"] is None and ctx["essay_summary"] is None)
        check("a quote not in the text gives None",
              sums.comment_context(db, manuscript, "doors.md", ESSAY,
                                   "not in the essay at all", _flat_prose)
              is None)

        print("summarize put:")
        try:
            sums.put_section_summary(db, manuscript, "doors.md", ESSAY, 2,
                                     "POINT: only a point.")
            ok = False
        except ValueError as err:
            ok = "MOVES" in str(err)
        check("a summary missing labels is not stored", ok)
        try:
            sums.put_section_summary(db, manuscript, "doors.md", ESSAY, 9, GOOD)
            ok = False
        except ValueError:
            ok = True
        check("a section number out of range is not stored", ok)
        row = sums.put_section_summary(db, manuscript, "doors.md", ESSAY, 2,
                                       GOOD)
        check("stored with model 'session'", row["model"] == "session")
        ctx = sums.comment_context(db, manuscript, "doors.md", ESSAY, quote,
                                   _flat_prose)
        check("the pull's context now shows it",
              ctx["section_summary"].startswith("POINT: The Field"))
        edited = ESSAY.replace("specializes the Field into a world",
                               "specializes the Field into worlds")
        ctx = sums.comment_context(db, manuscript, "doors.md", edited,
                                   "a Choice specializes the Field",
                                   _flat_prose)
        check("an edit to the section makes it stale again",
              ctx["section_summary"] is None)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    main_test()
