"""`gdocs._merge_changes` writes one caller's mapping edits onto current metadata.

A naive whole-dict save drops another tab's `pushed_hash` when two Doc
pushes overlap (bind-again.md, 2026-10-26). Nested `gdocs` keys, deletes,
and untouched siblings must all survive.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.gdocs import _merge_changes


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_untouched_sibling_preserved() -> None:
    seen = {"gdocs": {"a.md": {"tab_id": "t1"}, "b.md": {"tab_id": "t2"}}}
    mine = {"gdocs": {"a.md": {"tab_id": "t1", "pushed_at": "now"},
                      "b.md": {"tab_id": "t2"}}}
    now = {"gdocs": {"a.md": {"tab_id": "t1"},
                     "b.md": {"tab_id": "t2", "pushed_hash": "new"}}}
    out = _merge_changes(seen, mine, now)
    check("sibling pushed_hash from now survives",
          out["gdocs"]["b.md"].get("pushed_hash") == "new", out)
    check("caller's nested scalar lands",
          out["gdocs"]["a.md"].get("pushed_at") == "now", out)


def test_scalar_overwrite_and_nested_merge() -> None:
    seen = {"flag": False, "gdocs": {"x.md": {"tab_id": "t"}}}
    mine = {"flag": True, "gdocs": {"x.md": {"tab_id": "t", "pushed_hash": "h"}}}
    now = {"flag": False, "gdocs": {"x.md": {"tab_id": "t"}, "y.md": {"tab_id": "u"}}}
    out = _merge_changes(seen, mine, now)
    check("scalar change overwrites", out["flag"] is True, out)
    check("nested dict merges recursively",
          out["gdocs"]["x.md"] == {"tab_id": "t", "pushed_hash": "h"}, out)
    check("key only in now is kept",
          out["gdocs"].get("y.md") == {"tab_id": "u"}, out)


def test_delete_and_untouched_top_level() -> None:
    seen = {"keep": 1, "drop": 2, "gdocs": {}}
    mine = {"keep": 1, "gdocs": {}}  # drop removed by caller
    now = {"keep": 1, "drop": 2, "gdocs": {}, "other": 9}
    out = _merge_changes(seen, mine, now)
    check("key removed from mine is deleted", "drop" not in out, out)
    check("key only in now (never seen/mine) kept", out.get("other") == 9, out)
    check("unchanged keep stays", out.get("keep") == 1, out)


if __name__ == "__main__":
    test_untouched_sibling_preserved()
    test_scalar_overwrite_and_nested_merge()
    test_delete_and_untouched_top_level()
    print("ok")
