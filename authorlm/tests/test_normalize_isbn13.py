"""`api._normalize_isbn13` / `_validate_format_isbns` are the catalog
identity door for every paperback/hardcover ISBN the CLI and MCP accept.

Integration coverage in `test_api` hits checksum/prefix/distinctness through
`register_manuscript` / `manuscript set`. This file pins the pure parser
edges (empty clear, hyphen/space canonicalization, illegal characters,
length) so a silent drift cannot ship a bad ISBN into export metadata.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.api import _normalize_isbn13, _validate_format_isbns


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _raises(value: str) -> str:
    try:
        _normalize_isbn13(value)
    except ValueError as err:
        return str(err)
    return ""


def test_empty_clears() -> None:
    check("blank ISBN normalizes to empty (clear)",
          _normalize_isbn13("") == "")
    check("whitespace-only also clears",
          _normalize_isbn13("   ") == "")


def test_hyphen_and_space_canonicalization() -> None:
    check("hyphens strip to digit form",
          _normalize_isbn13("979-8-90452-354-1") == "9798904523541")
    check("spaces strip the same way",
          _normalize_isbn13("979 8 90452 354 1") == "9798904523541")
    check("978 prefix is accepted",
          _normalize_isbn13("978-0-306-40615-7") == "9780306406157")


def test_illegal_characters() -> None:
    msg = _raises("979-8-90452-354-X")
    check("letters are refused", "digits, spaces, or hyphens" in msg, msg)


def test_wrong_length() -> None:
    msg = _raises("978030640615")
    check("12 digits are refused", "exactly 13 digits" in msg, msg)
    msg = _raises("97803064061570")
    check("14 digits are refused", "exactly 13 digits" in msg, msg)


def test_prefix_and_checksum() -> None:
    msg = _raises("4006381333931")
    check("non-book EAN prefix is refused", "978 or 979" in msg, msg)
    msg = _raises("979-8-90452-354-2")
    check("bad checksum is refused", "checksum is invalid" in msg, msg)


def test_format_distinctness() -> None:
    _validate_format_isbns("", "")
    _validate_format_isbns("9798904523541", "")
    _validate_format_isbns("", "9798904523510")
    check("one-sided and empty pairs are allowed", True)
    try:
        _validate_format_isbns("9798904523541", "9798904523541")
        raised = ""
    except ValueError as err:
        raised = str(err)
    check("identical paperback/hardcover ISBNs are refused",
          "must be different" in raised, raised)


def main() -> None:
    tests = [test_empty_clears, test_hyphen_and_space_canonicalization,
             test_illegal_characters, test_wrong_length,
             test_prefix_and_checksum, test_format_distinctness]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
