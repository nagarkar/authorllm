"""`structure.title_of` / `find_file` are the shared file-lookup helpers
moved down from `lenses` in #346.

Lens repair, extraction, and summaries resolve a basename or nested path
through `find_file`; a wrong miss raises LookupError and aborts the pass.
`title_of` feeds labels when a file has no better name. Integration
suites hit the happy path; this file pins basename / nested match and
heading-title edges hermetically.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.structure import find_file, title_of


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


FILES = {
    "preface.md": "# Preface\n\nBody.",
    "part/alpha.md": "# *Alpha*\n\nBody.",
    "part/beta.md": "No heading yet.\n",
}


def test_find_file_exact_and_basename() -> None:
    check("exact relative path matches",
          find_file(FILES, "part/alpha.md") == "part/alpha.md")
    check("basename matches a nested file",
          find_file(FILES, "alpha.md") == "part/alpha.md")
    check("top-level basename matches",
          find_file(FILES, "preface.md") == "preface.md")


def test_find_file_missing_raises() -> None:
    try:
        find_file(FILES, "missing.md")
    except LookupError as err:
        check("missing file raises LookupError naming the query",
              "missing.md" in str(err), err)
    else:
        raise AssertionError("expected LookupError")


def test_title_of_heading_and_emphasis() -> None:
    check("first AT1 becomes the title",
          title_of(FILES["preface.md"]) == "Preface")
    check("emphasis markers around the title strip",
          title_of(FILES["part/alpha.md"]) == "Alpha")
    check("no heading yields empty title",
          title_of(FILES["part/beta.md"]) == "")
    check("empty / None text yields empty title",
          title_of("") == "" and title_of(None) == "")  # type: ignore[arg-type]


def main() -> None:
    for t in (test_find_file_exact_and_basename,
              test_find_file_missing_raises,
              test_title_of_heading_and_emphasis):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
