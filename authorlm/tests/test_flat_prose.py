"""`gdocs._flat_prose` matches Doc comment anchors across emphasis.

A Doc margin comment quotes the rendered text (*religare* comes back as
religare). Without stripping `*`/`_`, `locate_quote` left the comment
unattributed (read-again.md, 2026-10-26). Happy/ambiguous/empty paths
live in open coverage for `locate_quote`; this file pins the emphasis
door itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.gdocs import _flat_prose, locate_quote


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_flat_prose_strips_emphasis_and_whitespace() -> None:
    check("asterisks drop; whitespace collapses",
          _flat_prose("*religare*  comes\nback") == "religare comes back")
    check("underscores drop too",
          _flat_prose("see _religare_ now") == "see religare now")
    check("bold markers drop",
          _flat_prose("**Title** line") == "Title line")
    check("empty / whitespace-only folds to empty",
          _flat_prose("") == "" and _flat_prose("  \n\t ") == "")


def test_locate_quote_matches_doc_without_asterisks() -> None:
    files = {"read-again.md": (
        "# Opening\n\n"
        "the word *religare* binds the passages.\n"
    )}
    # What Google's comment API returns: rendered text, no markers.
    rel, heading = locate_quote(files, "the word religare binds")
    check("Doc quote without asterisks → the file",
          rel == "read-again.md", repr(rel))
    check("Doc quote without asterisks → nearest heading",
          heading == "Opening", repr(heading))

    rel2, _ = locate_quote({"u.md": "see _religare_ now\n"},
                           "see religare now")
    check("underscore emphasis also matches a bare Doc quote",
          rel2 == "u.md", repr(rel2))


def main() -> None:
    tests = [
        test_flat_prose_strips_emphasis_and_whitespace,
        test_locate_quote_matches_doc_without_asterisks,
    ]
    for fn in tests:
        print(fn.__name__)
        fn()
    print(f"OK — {len(tests)} flat_prose cases")


if __name__ == "__main__":
    main()
