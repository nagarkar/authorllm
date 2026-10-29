"""Direct pins for extraction gate-1/2/3 pure helpers (§4.4).

`test_extraction_gates.py` exercises them through `screen_replies` on the
§2.4 fixture. This file pins the doors themselves: emphasis-folded span
match, name presence, and the "Experience" common-word rule so a silent
regression cannot hide behind the screening harness.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import extraction


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_fold_drops_emphasis_and_whitespace() -> None:
    check("_fold collapses whitespace",
          extraction._fold("  a\n\tb  ") == "a b")
    check("_fold strips * and keeps words",
          extraction._fold("**Rankings**") == "Rankings")
    check("_fold on None / empty is empty",
          extraction._fold(None) == "" and extraction._fold("") == "")


def test_span_in_gate1() -> None:
    para = "A hierarchy of **Rankings** assigns each member a place."
    check("plain span matches emphasized paragraph",
          extraction.span_in("Rankings", para))
    check("emphasized span matches plain paragraph",
          extraction.span_in("**Rankings**",
                             "A hierarchy of Rankings assigns."))
    check("empty / whitespace-only span is not in",
          not extraction.span_in("", para)
          and not extraction.span_in("   ", para))
    check("a different word is not in",
          not extraction.span_in("Ghost", para))
    check("case is kept after fold (not lowered)",
          not extraction.span_in("rankings", para))


def test_names_in_gate2() -> None:
    para = "A rank is a place in the ordering."
    check("present name hits",
          extraction.names_in(["rank"], para))
    check("absent name misses",
          not extraction.names_in(["Ghost"], para))
    check("empty / blank names never hit",
          not extraction.names_in([], para)
          and not extraction.names_in([""], para))
    check("any one of several names is enough",
          extraction.names_in(["Ghost", "rank"], para))


def test_sentence_initial() -> None:
    text = "Sorting is common. Mid Experience shows it.\nExperience again."
    check("file start is sentence-initial",
          extraction._sentence_initial(text, 0))
    mid = text.index("Mid Experience") + len("Mid ")
    check("after a letter is not sentence-initial",
          not extraction._sentence_initial(text, mid))
    after = text.index("Experience again")
    check("after newline is sentence-initial",
          extraction._sentence_initial(text, after))
    after_stop = text.index("Mid Experience")
    # "Mid" starts after ". "
    check("after period+space is sentence-initial",
          extraction._sentence_initial(text, after_stop))


def test_is_term_of_art_gate3() -> None:
    only_initial = {"a.md": "Sorting is common at the start."}
    check("capitalised only at sentence start → common word",
          not extraction.is_term_of_art("Sorting", only_initial))
    mid = {"a.md": "The mid-sentence Experience shows the term."}
    check("capitalised mid-sentence → term of art",
          extraction.is_term_of_art("Experience", mid))
    check("multi-word names always pass",
          extraction.is_term_of_art("Accord Line", {"a.md": "nothing"}))
    check("lowercase names always pass",
          extraction.is_term_of_art("deme", {"a.md": "nothing"}))


def main() -> None:
    tests = [
        test_fold_drops_emphasis_and_whitespace,
        test_span_in_gate1,
        test_names_in_gate2,
        test_sentence_initial,
        test_is_term_of_art_gate3,
    ]
    for fn in tests:
        print(fn.__name__)
        fn()
    print(f"OK — {len(tests)} extraction gate-helper cases")


if __name__ == "__main__":
    main()
