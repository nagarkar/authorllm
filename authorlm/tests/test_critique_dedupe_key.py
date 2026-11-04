"""`critique._dedupe_key` is the cross-source idempotency normalizer for
critique import.

Two critic reports that restate the same item — differing only by case,
markdown markup, or punctuation — must collapse to one key so a later
import never re-litigates a ruled item. Items that differ in a content
word must stay distinct. `import_manifest` exercises this only through
full DB fixtures; this file pins the normalizer directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.critique import _dedupe_key


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_case_and_whitespace() -> None:
    a = _dedupe_key("  Drop The Soft Opener.  ")
    b = _dedupe_key("drop the soft opener")
    check("case and outer whitespace collapse", a == b, (a, b))
    check("internal whitespace collapses",
          _dedupe_key("drop   the\tsoft opener") == a)


def test_markdown_and_punctuation() -> None:
    plain = _dedupe_key("drop the soft opener")
    md = _dedupe_key("**Drop** the _soft_ `opener`!")
    punct = _dedupe_key("Drop the soft opener?")
    check("markdown markup is stripped", md == plain, (md, plain))
    check("terminal punctuation is stripped", punct == plain, (punct, plain))


def test_brackets_and_parens() -> None:
    a = _dedupe_key("see the claim (again)")
    b = _dedupe_key("see the claim again")
    check("parens and their content stay as words after strip",
          a == b, (a, b))


def test_distinct_content_stays_distinct() -> None:
    a = _dedupe_key("drop the soft opener")
    b = _dedupe_key("drop the hard closer")
    check("a changed content word yields a different key",
          a != b, (a, b))


def main() -> None:
    tests = [
        test_case_and_whitespace,
        test_markdown_and_punctuation,
        test_brackets_and_parens,
        test_distinct_content_stays_distinct,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
