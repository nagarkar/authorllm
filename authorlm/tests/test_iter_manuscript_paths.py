"""`revisions.iter_manuscript_paths` is the observation visibility gate.

Directories whose names start with `.` or `_`, and dotfiles, are invisible
to collect. A miss here feeds add/delete noise (or worse, auto-retire).
E2E covers `_drafts`/`_concepts` indirectly; this file pins the walker
hermetically on a temp tree.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.revisions import iter_manuscript_paths


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _touch(root: Path, rel: str, body: str = "x\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_keeps_visible_and_drops_hidden() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _touch(root, "preface.md")
        _touch(root, "part/a.md")
        _touch(root, "toc.toml", "[[chapter]]\nfile = \"preface.md\"\n")
        _touch(root, "_illustrations/fig.md")
        _touch(root, "_concepts/Trajectory.md")
        _touch(root, ".obsidian/workspace.md")
        _touch(root, ".hidden.md")
        _touch(root, "part/.secret.md")
        _touch(root, "notes.txt")
        # Non-manuscript extension must be ignored.
        _touch(root, "image.png", "")

        got = iter_manuscript_paths(root)
        keys = set(got)
        check("keeps top-level essay", "preface.md" in keys, keys)
        check("keeps nested essay", "part/a.md" in keys, keys)
        check("keeps toc.toml", "toc.toml" in keys, keys)
        check("keeps .txt", "notes.txt" in keys, keys)
        check("drops _illustrations", "_illustrations/fig.md" not in keys, keys)
        check("drops _concepts", "_concepts/Trajectory.md" not in keys, keys)
        check("drops .obsidian", ".obsidian/workspace.md" not in keys, keys)
        check("drops top-level dotfile", ".hidden.md" not in keys, keys)
        check("drops nested dotfile", "part/.secret.md" not in keys, keys)
        check("ignores non-manuscript suffix", "image.png" not in keys, keys)
        check("exactly the four visible paths",
              keys == {"preface.md", "part/a.md", "toc.toml", "notes.txt"},
              keys)


def main() -> None:
    print("test_keeps_visible_and_drops_hidden:")
    test_keeps_visible_and_drops_hidden()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
