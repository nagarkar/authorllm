"""`book._strings` is the TOML gate on every list-of-strings field in a
workflow step (`after`, `gives`, `artifacts`). A non-list or a non-string
member must refuse with the step's `where` — otherwise a typing mistake
silently empties a dependency list and a step starts too soon.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.book import _strings


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _raises(label: str, table, key: str, where: str, needle: str) -> None:
    try:
        _strings(table, key, where)
    except ValueError as err:
        check(label, needle in str(err) and where in str(err), err)
        return
    check(label, False, "no ValueError")


def test_accepts_a_list_of_strings() -> None:
    check("empty default when the key is absent",
          _strings({}, "after", "step x") == ())
    check("tuple of the strings in order",
          _strings({"after": ["a", "b"]}, "after", "step x")
          == ("a", "b"))


def test_refuses_non_lists_and_non_strings() -> None:
    _raises("a string is not a list",
            {"after": "a"}, "after", "step draft", "must be a list of strings")
    _raises("a bare string member refuses",
            {"gives": ["brief", 1]}, "gives", "step draft",
            "must be a list of strings")
    _raises("None member refuses",
            {"artifacts": [None]}, "artifacts", "step draft",
            "must be a list of strings")


def main() -> None:
    for test in (test_accepts_a_list_of_strings,
                 test_refuses_non_lists_and_non_strings):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
