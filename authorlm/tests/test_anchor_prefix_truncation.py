"""`_split_by_anchors` must not truncate the Docs-API anchor to 80 chars.

After Google dropped tab-title headings, every pull splits the export at
each tab's first paragraph. Matching `startswith(want[:80])` treated two
openings that share an 80-character plain prefix as the same boundary, so
the later tab stole the rest of the prior chapter and pull overwrote the
local files.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.gdocs import _split_by_anchors


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_shared_prefix_does_not_steal_prior_tab() -> None:
    # Two openers share >80 plain characters, then diverge. Truncating the
    # needle to 80 would match A's mid-chapter line when searching for B.
    shared = (
        "In the previous chapter we established that choice under "
        "constraint still counts as"
    )
    assert len(shared) >= 80, shared
    text = (
        f"{shared} freedom.\n\n"
        "Body of A continues with unique material about apples.\n\n"
        f"{shared} freedom when the agent owns the act.\n\n"
        "Rest of A about oranges.\n\n"
        f"{shared} something completely different for B.\n\n"
        "Body of B.\n"
    )
    out = _split_by_anchors(
        text, ["a.md", "b.md"],
        {"a.md": f"{shared} freedom.",
         "b.md": f"{shared} something completely different for B."},
    )
    check("b starts at its own opener",
          out.get("b.md", "").startswith(
              f"{shared} something completely different for B."),
          out.get("b.md", "")[:120])
    check("A's mid-chapter shared-prefix line stays in a",
          f"{shared} freedom when the agent owns the act." in out.get("a.md", ""),
          out.get("a.md"))
    check("A's unique body is not stolen into b",
          "apples" not in out.get("b.md", "")
          and "oranges" not in out.get("b.md", ""),
          out.get("b.md"))


def test_true_prefix_still_matches_longer_export_line() -> None:
    # Docs-API paragraph is a proper prefix of the export line (soft join).
    text = (
        "Opening of one.\n\nBody of one.\n\n"
        "Opening of two with a long first paragraph that continues.\n\n"
        "Body of two.\n"
    )
    out = _split_by_anchors(
        text, ["one.md", "two.md"],
        {"one.md": "Opening of one.",
         "two.md": "Opening of two with a long first paragraph"},
    )
    check("full-anchor prefix still finds the later tab",
          "two.md" in out
          and out["two.md"].startswith("Opening of two"),
          out)


if __name__ == "__main__":
    test_shared_prefix_does_not_steal_prior_tab()
    test_true_prefix_still_matches_longer_export_line()
    print("ok")
