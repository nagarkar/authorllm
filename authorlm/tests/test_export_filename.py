"""`export.export_filename` sanitizes the combined-export basename.

Unlike `docs.doc_filename` (chapter paths, keeps/adds an extension), this
helper always appends `.md` after stripping path-unsafe characters. A
regression would write combined exports under names that break the
filesystem or collide with path separators. Nothing in the suite called
it directly before this file.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.export import export_filename


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_strip_and_append_md() -> None:
    check("leading/trailing whitespace stripped, .md appended",
          export_filename("  My Book  ") == "My Book.md")
    check("already-named book still gains .md (basename, not extension rule)",
          export_filename("book.md") == "book.md.md")


def test_unsafe_characters() -> None:
    got = export_filename(r'a/b:c*d?"e<f>g|h')
    check("path-reserved characters become hyphens",
          got == "a-b-c-d--e-f-g-h.md", got)


def test_empty_refuses() -> None:
    for label, name in (("empty string", ""),
                        ("whitespace only", "   ")):
        try:
            export_filename(name)
        except ValueError as err:
            check(f"{label} raises ValueError", True)
            check(f"{label} message names empty",
                  "empty" in str(err).lower(), str(err))
        else:
            check(f"{label} raises ValueError", False)
    check("slash-only becomes hyphens.md",
          export_filename("///") == "---.md")


def main() -> None:
    for t in (test_strip_and_append_md, test_unsafe_characters,
              test_empty_refuses):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
