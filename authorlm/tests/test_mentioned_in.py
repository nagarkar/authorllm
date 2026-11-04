"""`hygiene.mentioned_in` is the groundedness gate for concepts and edges.

It returns the first file whose text mentions any of the supplied names
(word-boundary, plural-tolerant via `concept_pattern`), or None. A silent
regression either retires grounded hypotheses as ungrounded or lets
ungrounded edges into the evidence stream. The api suite exercises the
recurrence bar via `passes_recurrence_bar`; this file pins the mention
lookup itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.hygiene import mentioned_in


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_first_file_wins() -> None:
    files = {
        "a.md": "No relevant terms here.",
        "b.md": "The Undertow pulls every choice.",
        "c.md": "The Undertow returns later.",
    }
    got = mentioned_in(files, ["Undertow"])
    check("returns the first file that mentions the name",
          got == "b.md", repr(got))


def test_none_when_absent() -> None:
    got = mentioned_in({"a.md": "Plain prose."}, ["Undertow"])
    check("absent name yields None", got is None, repr(got))


def test_empty_name_skipped() -> None:
    got = mentioned_in({"a.md": "text"}, ["", None])  # type: ignore[list-item]
    check("empty / None names never match", got is None, repr(got))


def test_plural_tolerant() -> None:
    got = mentioned_in({"a.md": "Several trajectories diverge."},
                       ["trajectory"])
    check("simple English plural still counts as a mention",
          got == "a.md", repr(got))


def test_word_boundary() -> None:
    got = mentioned_in({"a.md": "The fieldstone wall stands."}, ["field"])
    check("substring inside a larger word is not a mention",
          got is None, repr(got))


def test_any_name_suffices() -> None:
    got = mentioned_in({"a.md": "Aliases matter too."},
                       ["Undertow", "Aliases"])
    check("any listed name hits", got == "a.md", repr(got))


def main() -> None:
    tests = [test_first_file_wins, test_none_when_absent,
             test_empty_name_skipped, test_plural_tolerant,
             test_word_boundary, test_any_name_suffices]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
