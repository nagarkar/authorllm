"""`gdocs.split_tabbed_export` cuts a whole-master markdown export into
per-tab bodies.

Boundaries are tab-title H1s (`# **title**`). A prose H1 that repeats
another tab's title must not start a section out of turn when `order`
is the Docs tab tree (it-x7-2): otherwise the essay truncates and the
later real tab absorbs or loses content. Names absent from `order`
still match anywhere (no positional info).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.gdocs import split_tabbed_export


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


KNOWN = {"preface.md", "sermons.md", "discernment.md"}


def test_known_tabs_and_content_h1() -> None:
    export = "\n".join([
        "# **Tab 1**", "",
        "# **preface.md**", "", "Intro prose.", "",
        "# **sermons.md**", "",
        "# **The First Sermon**", "", "Harken.", "",
        "# **discernment.md**", "", "Plain words.",
    ])
    parts = split_tabbed_export(export, KNOWN)
    check("every known tab becomes a key",
          set(parts) == KNOWN, parts.keys())
    check("content H1 in export style is never a boundary",
          "# **The First Sermon**" in parts["sermons.md"]
          and "Harken." in parts["sermons.md"], parts["sermons.md"])
    check("unknown leading tab is ignored; bodies stay clean",
          parts["preface.md"].strip() == "Intro prose."
          and parts["discernment.md"].strip() == "Plain words.",
          parts)


def test_order_ignores_out_of_turn_title_collision() -> None:
    """it-x7-2: a prose heading that repeats a later tab's title must
    stay inside the current essay when `order` is given."""
    export = "\n".join([
        "# **a.md**", "", "Intro", "",
        "# **manifest**", "", "Prose after collision", "",
        "# **b.md**", "", "B body", "",
        "# **manifest**", "", "Real inventory",
    ])
    known = {"a.md", "b.md", "manifest"}
    order = ["a.md", "b.md", "manifest"]
    parts = split_tabbed_export(export, known, order=order)
    check("keys follow the known set",
          set(parts) == known, parts.keys())
    check("out-of-turn # manifest stays inside a.md",
          "# **manifest**" in parts["a.md"]
          and "Prose after collision" in parts["a.md"],
          parts["a.md"])
    check("the real inventory tab is the later boundary only",
          parts["manifest"].strip() == "Real inventory",
          parts["manifest"])
    check("b.md is not truncated by the collision",
          parts["b.md"].strip() == "B body", parts["b.md"])


def test_without_order_known_name_still_matches_anywhere() -> None:
    """No `order`: a known title is still a boundary wherever it appears
    (legacy behaviour when the Docs tab tree was unavailable)."""
    export = "\n".join([
        "# **a.md**", "", "Intro", "",
        "# **manifest**", "", "Prose after collision", "",
        "# **b.md**", "", "B body", "",
        "# **manifest**", "", "Real inventory",
    ])
    known = {"a.md", "b.md", "manifest"}
    parts = split_tabbed_export(export, known)
    check("first out-of-turn known title still opens a section",
          "Prose after collision" not in parts.get("a.md", ""),
          parts.get("a.md"))
    check("later same title overwrites that section's body",
          parts["manifest"].strip() == "Real inventory",
          parts["manifest"])


def test_name_absent_from_order_matches_anywhere() -> None:
    export = "\n".join([
        "# **a.md**", "", "A", "",
        "# **orphan.md**", "", "Orphan body", "",
        "# **b.md**", "", "B",
    ])
    known = {"a.md", "b.md", "orphan.md"}
    # orphan.md is known but not in the Docs order list.
    parts = split_tabbed_export(export, known, order=["a.md", "b.md"])
    check("orphan absent from order still becomes a section",
          parts["orphan.md"].strip() == "Orphan body", parts)
    check("ordered tabs still split correctly around it",
          parts["a.md"].strip() == "A"
          and parts["b.md"].strip() == "B", parts)


def main() -> None:
    tests = [
        test_known_tabs_and_content_h1,
        test_order_ignores_out_of_turn_title_collision,
        test_without_order_known_name_still_matches_anywhere,
        test_name_absent_from_order_matches_anywhere,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
