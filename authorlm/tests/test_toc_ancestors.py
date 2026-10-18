"""`passes.toc_ancestors` walks toc.toml parents nearest-first for the
critique/intent scope chain.

A cycle in parent links must terminate (the `cur not in chain` guard);
a missing file or a root chapter returns []. `scope_chain` in
`test_passes.py` covers the happy one-parent case inside a full init —
this file pins the pure edges without a workspace.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.passes import toc_ancestors


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _ms(tmp: Path, toc: str, files: dict[str, str]) -> dict:
    root = tmp / "book"
    root.mkdir()
    (root / "toc.toml").write_text(toc, encoding="utf-8")
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    return {"id": "m", "path": str(root)}


def test_nearest_first_chain() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _ms(Path(tmp),
                 '[[chapter]]\nfile = "book.md"\n\n'
                 '[[chapter]]\nfile = "part.md"\nparent = "book.md"\n\n'
                 '[[chapter]]\nfile = "essay.md"\nparent = "part.md"\n',
                 {"book.md": "# Book\n", "part.md": "# Part\n",
                  "essay.md": "# Essay\n"})
        check("nearest parent first, then grandparent",
              toc_ancestors(ms, "essay.md") == ["part.md", "book.md"])
        check("root chapter has no ancestors",
              toc_ancestors(ms, "book.md") == [])
        check("unknown file has no ancestors",
              toc_ancestors(ms, "ghost.md") == [])


def test_parent_cycle_terminates() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _ms(Path(tmp),
                 '[[chapter]]\nfile = "a.md"\nparent = "b.md"\n\n'
                 '[[chapter]]\nfile = "b.md"\nparent = "a.md"\n',
                 {"a.md": "# A\n", "b.md": "# B\n"})
        got = toc_ancestors(ms, "a.md")
        check("cycle yields each parent once then stops",
              got == ["b.md", "a.md"], got)


def main() -> None:
    tests = [
        test_nearest_first_chain,
        test_parent_cycle_terminates,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
