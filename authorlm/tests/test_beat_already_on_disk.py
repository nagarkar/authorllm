"""`api._beat_already_on_disk` is the write_accept crash-window guard.

`write_accept` appends the beat before it records the review. A crash
between those leaves the proposal `proposed` while the file already
holds the text; a bare retry must finish the DB half without appending
again. False negative duplicates a beat; false positive skips a real
append. `test_write_accept_idempotent` covers the integration path;
these pins cover the pure suffix rules.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.api import _beat_already_on_disk


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_empty_accepted_is_never_present() -> None:
    check("empty accepted is not on disk",
          _beat_already_on_disk("hello", "") is False)
    check("whitespace-only accepted strips to empty → False",
          _beat_already_on_disk("hello", "\n\n") is False)


def test_exact_body_match() -> None:
    check("exact body match is present",
          _beat_already_on_disk("the beat", "the beat") is True)
    check("trailing newlines on either side still match",
          _beat_already_on_disk("the beat\n\n", "the beat\n") is True)


def test_blank_line_suffix() -> None:
    check("beat after \\n\\n is present",
          _beat_already_on_disk("intro\n\nthe beat", "the beat") is True)
    check("beat after more prose and \\n\\n is present",
          _beat_already_on_disk("a\n\nb\n\nthe beat\n", "the beat") is True)


def test_bare_suffix_without_blank_line_refuses() -> None:
    check("suffix without a blank line is not the beat",
          _beat_already_on_disk("introthe beat", "the beat") is False)
    check("single newline before the beat is not enough",
          _beat_already_on_disk("intro\nthe beat", "the beat") is False)
    check("unrelated body is not present",
          _beat_already_on_disk("other text", "the beat") is False)


def main() -> None:
    for t in (test_empty_accepted_is_never_present, test_exact_body_match,
              test_blank_line_suffix,
              test_bare_suffix_without_blank_line_refuses):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
