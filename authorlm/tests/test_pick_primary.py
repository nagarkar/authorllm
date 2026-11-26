"""`api._pick_primary` chooses the most specific intent member for a
writeup — file over nearest chapter over farther chapter over
manuscript — and refuses to break a tie at the winning tier.

Wrong specificity silently demotes the essay-local goal; a silent
tie-break invents a "standing" primary the author never chose
(design-intent-scope §1.7). `_members_view` and `_resolve_member_prefix`
are the CLI/block-A surface over that same block: order and prefix
ambiguity must stay pure functions of the ratified members.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.api import (
    _members_view,
    _pick_primary,
    _resolve_member_prefix,
)


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _ms(tmp: Path) -> dict:
    root = tmp / "book"
    root.mkdir()
    (root / "toc.toml").write_text(
        '[[chapter]]\nfile = "book.md"\n\n'
        '[[chapter]]\nfile = "part.md"\nparent = "book.md"\n\n'
        '[[chapter]]\nfile = "essay.md"\nparent = "part.md"\n',
        encoding="utf-8")
    for name in ("book.md", "part.md", "essay.md"):
        (root / name).write_text(f"# {name}\n", encoding="utf-8")
    return {"id": "m", "path": str(root)}


def _member(mid: str, scope: str | None) -> dict:
    return {"id": mid, "scope": scope}


def test_empty_and_singleton() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _ms(Path(tmp))
        check("empty members → no primary, no candidates",
              _pick_primary(ms, "essay.md", []) == (None, []))
        primary, candidates = _pick_primary(
            ms, "essay.md", [_member("solo", "essay.md")])
        check("singleton is primary",
              primary == "solo" and candidates == ["solo"],
              (primary, candidates))


def test_specificity_and_tie() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _ms(Path(tmp))
        members = [
            _member("ms", None),
            _member("far", "book.md"),
            _member("near", "part.md"),
            _member("file", "essay.md"),
        ]
        primary, candidates = _pick_primary(ms, "essay.md", members)
        check("file-scoped member beats every ancestor and manuscript",
              primary == "file" and candidates == ["file"],
              (primary, candidates))

        # Two at the winning (nearest chapter) tier — machine must not break.
        tied = [
            _member("zeta", "part.md"),
            _member("alpha", "part.md"),
            _member("ms", None),
        ]
        primary, candidates = _pick_primary(ms, "essay.md", tied)
        check("tie at winning tier leaves primary unset",
              primary is None, primary)
        check("tied candidates are id-sorted, not input order",
              candidates == ["alpha", "zeta"], candidates)


def test_members_view_order() -> None:
    block = {"members": [
        {"id": "b-file", "role": "secondary", "tier": "file"},
        {"id": "a-ms", "role": "secondary", "tier": "manuscript"},
        {"id": "primary", "role": "primary", "tier": "chapter"},
        {"id": "a-file", "role": "secondary", "tier": "file"},
        {"id": "weird", "role": "secondary", "tier": "unknown-tier"},
    ]}
    ids = [m["id"] for m in _members_view(block)]
    check("primary first, then tier rank, then id; unknown last",
          ids == ["primary", "a-file", "b-file", "a-ms", "weird"], ids)
    check("empty / missing members → []",
          _members_view({}) == [] and _members_view({"members": None}) == [])


def test_resolve_member_prefix() -> None:
    block = {"members": [{"id": "abc-111"}, {"id": "abc-222"},
                         {"id": "xyz-999"}]}
    check("unique substring resolves",
          _resolve_member_prefix(block, "xyz") == "xyz-999")
    check("no hit → None",
          _resolve_member_prefix(block, "nope") is None)
    try:
        _resolve_member_prefix(block, "abc")
        raised = None
    except LookupError as err:
        raised = str(err)
    check("ambiguous prefix raises LookupError naming the count",
          raised is not None and "ambiguous" in raised and "2" in raised,
          raised)


def main() -> None:
    tests = [
        test_empty_and_singleton,
        test_specificity_and_tie,
        test_members_view_order,
        test_resolve_member_prefix,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
