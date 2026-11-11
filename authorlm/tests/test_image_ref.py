"""`media_gen._image_ref` resolves a Veo start/end/cast frame value to an
image path: a relative path under the manuscript root, a known cast id's
plate under `_illustrations/`, or None.

A wrong resolution ships a billed Veo job with a missing or swapped
reference frame. Pure over root + cast map — no network, no files read.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.illus import ILLUS_DIR
from authorlm.media_gen import _image_ref


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


ROOT = Path("/manuscript")


def test_relative_path() -> None:
    got = _image_ref(ROOT, "frames/open.png", {})
    check("a path-shaped value joins the manuscript root",
          got == ROOT / "frames/open.png", got)
    nested = _image_ref(ROOT, "_clips/notes/x.jpg", {"alice": "a.png"})
    check("path wins even when a cast map is present",
          nested == ROOT / "_clips/notes/x.jpg", nested)


def test_known_cast_plate() -> None:
    got = _image_ref(ROOT, "alice", {"alice": "alice-plate.png",
                                     "bob": "bob-plate.png"})
    check("a known cast id resolves to its plate under _illustrations",
          got == ROOT / ILLUS_DIR / "alice-plate.png", got)


def test_unknown_returns_none() -> None:
    check("an unknown cast id yields None",
          _image_ref(ROOT, "carol", {"alice": "alice-plate.png"}) is None)
    check("empty cast map yields None for a bare id",
          _image_ref(ROOT, "alice", {}) is None)


def test_does_not_invent_illustration_paths() -> None:
    # An illustration key is not a path (no slash / extension) and is not
    # a cast id unless the cast map says so — never invent `_illustrations/`.
    got = _image_ref(ROOT, "hero-shot", {"alice": "alice-plate.png"})
    check("an illustration-like key without a cast entry is None",
          got is None, got)
    check("a falsey plate entry is treated as unknown",
          _image_ref(ROOT, "alice", {"alice": ""}) is None)


def main() -> None:
    tests = [
        test_relative_path,
        test_known_cast_plate,
        test_unknown_returns_none,
        test_does_not_invent_illustration_paths,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
