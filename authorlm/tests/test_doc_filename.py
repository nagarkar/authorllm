"""`docs.doc_filename` is the only sanitizer `add_doc` uses for new chapter
paths. Unsafe characters become hyphens, whitespace collapses, empty names
refuse, and a missing extension becomes `.md`. Existing suites only call
`add_doc` with already-safe names, so a regression here would land files
with `/` or `#` in the path (or accept a blank title).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.docs import doc_filename


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_spaces_become_hyphens() -> None:
    got = doc_filename("  On  Gravity  ")
    check("leading/trailing strip and internal spaces → hyphens",
          got == "On-Gravity.md", got)


def test_unsafe_characters() -> None:
    got = doc_filename(r'a/b:c*d?"e<f>g|h#i')
    # Each reserved character becomes its own hyphen — adjacent `?"`
    # therefore yields `--`, not a collapsed single dash.
    check("path and Doc-title reserved chars become hyphens",
          got == "a-b-c-d--e-f-g-h-i.md", got)


def test_empty_refuses() -> None:
    for label, name in (("empty string", ""),
                        ("whitespace only", "   ")):
        try:
            doc_filename(name)
        except ValueError as err:
            check(f"{label} raises ValueError", True)
            check(f"{label} message names empty",
                  "empty" in str(err).lower(), str(err))
        else:
            check(f"{label} raises ValueError", False)
    # Unsafe-only input is not empty after sanitization — each char
    # becomes a hyphen — so it is a legal (if ugly) filename.
    check("slash-only name becomes hyphens.md",
          doc_filename("///") == "---.md", doc_filename("///"))


def test_extension_rules() -> None:
    check("already .md is kept",
          doc_filename("chapter.md") == "chapter.md")
    check(".markdown is kept",
          doc_filename("notes.markdown") == "notes.markdown")
    check(".txt is kept",
          doc_filename("aside.txt") == "aside.txt")
    check("missing extension gains .md",
          doc_filename("chapter") == "chapter.md")
    check("uppercase .MD still counts as present",
          doc_filename("Chapter.MD") == "Chapter.MD")


def main() -> None:
    tests = [test_spaces_become_hyphens, test_unsafe_characters,
             test_empty_refuses, test_extension_rules]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
