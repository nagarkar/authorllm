"""`filtering._is_protected` / `_names_of` are the kind+status gate and
the name/alias expansion behind `protected_terms` (and so behind
`book land`'s protected-loss warnings). Wrong kind → ordinary English
from an objection label is guarded as vocabulary; a missing alias → a
landed change that drops the author's own synonym is never warned.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.filtering import (
    PROTECTED_KINDS,
    PROTECTED_STATUSES,
    _is_protected,
    _names_of,
)


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_only_vocabulary_kinds_in_live_statuses() -> None:
    for kind in sorted(PROTECTED_KINDS):
        for status in sorted(PROTECTED_STATUSES):
            check(f"{kind}/{status} is protected",
                  _is_protected({"kind": kind, "status": status}))
    check("objection/declared is not protected (sentence labels)",
          not _is_protected({"kind": "objection", "status": "declared"}))
    check("concept/retired is not protected",
          not _is_protected({"kind": "concept", "status": "retired"}))
    check("missing fields are not protected",
          not _is_protected({}))


def test_names_of_includes_aliases() -> None:
    check("name plus aliases",
          _names_of({"name": "Field", "aliases": ["arena", "Ground"]})
          == ["Field", "arena", "Ground"])
    check("missing aliases → name alone",
          _names_of({"name": "Field"}) == ["Field"])
    check("empty aliases → name alone",
          _names_of({"name": "Field", "aliases": []}) == ["Field"])
    check("None aliases → name alone",
          _names_of({"name": "Field", "aliases": None}) == ["Field"])


def main() -> None:
    for test in (
        test_only_vocabulary_kinds_in_live_statuses,
        test_names_of_includes_aliases,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
