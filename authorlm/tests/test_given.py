"""`book._given` formats every named `gives` path into the pack contract
(`chapter` / `part` / `toc` / `scope_flag` / unknown step ids).

Wrong part joining or an empty book `scope_flag` ships a bad contract:
the role is pointed at the wrong files or told to pass a scope the book
step must not take. Pure over a constructed Layout; no workspace.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.book import Layout, Scope, Step, _given  # noqa: E402
from authorlm import structure  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _layout() -> Layout:
    return Layout(
        chapters=(
            Scope("chapter", "dc-1", "one.md"),
            Scope("chapter", "dc-2", "two.md"),
            Scope("chapter", "dc-3", "three.md"),
        ),
        parts=(
            Scope("part", "dc-p1", "p1.md"),
            Scope("part", "dc-p2", "p2.md"),
        ),
        part_of={"dc-1": "dc-p1", "dc-2": "dc-p1", "dc-3": "dc-p2"},
        not_yet_documents=(),
    )


def _step(*gives: str) -> Step:
    return Step(id="draft", role="drafter", actor="agent", grain="chapter",
                after=(), gives=gives, returns="x", artifacts=(),
                leaves_alone="y", independent=False)


def test_empty_gives_says_nothing_by_name() -> None:
    got = _given({"name": "book", "path": "/ms"}, _layout(), _step(),
                 Scope("chapter", "dc-1", "one.md"))
    check("empty gives → nothing by name",
          got == ["  - nothing by name"], got)


def test_chapter_path_uses_scope_label() -> None:
    got = _given({"name": "book", "path": "/ms"}, _layout(),
                 _step("chapter"), Scope("chapter", "dc-1", "one.md"))
    check("chapter give names the chapter path",
          got == ["  - chapter: the chapter: /ms/one.md"], got)


def test_part_joins_sibling_chapters_in_order() -> None:
    got = _given({"name": "book", "path": "/ms"}, _layout(),
                 _step("part"), Scope("part", "dc-p1", "p1.md"))
    check("part give joins the part's chapter paths",
          got == ["  - part: the part's chapters, in reading order: "
                  "/ms/one.md, /ms/two.md"], got)


def test_chapter_scope_part_give_uses_owning_part() -> None:
    got = _given({"name": "book", "path": "/ms"}, _layout(),
                 _step("part"), Scope("chapter", "dc-3", "three.md"))
    check("chapter scope still resolves its part's chapters",
          got == ["  - part: the part's chapters, in reading order: "
                  "/ms/three.md"], got)


def test_book_scope_flag_is_empty_for_changes() -> None:
    got = _given({"name": "book", "path": "/ms"}, _layout(),
                 _step("changes"), Scope("book", "book", "the book"))
    check("book scope → changes command has no --scope",
          got == ["  - changes: the changes agent roles landed in this "
                  "scope, counted below: authorlm book changes -m book"],
          got)


def test_chapter_scope_flag_names_the_file() -> None:
    got = _given({"name": "book", "path": "/ms"}, _layout(),
                 _step("changes"), Scope("chapter", "dc-2", "two.md"))
    check("chapter scope → changes command carries --scope",
          got == ["  - changes: the changes agent roles landed in this "
                  "scope, counted below: authorlm book changes "
                  "--scope two.md -m book"], got)


def test_unknown_give_points_at_step_result_below() -> None:
    got = _given({"name": "book", "path": "/ms"}, _layout(),
                 _step("voice-sample"), Scope("chapter", "dc-1", "one.md"))
    check("unknown step id → what that step returned, below",
          got == ["  - voice-sample: what that step returned, below"], got)


def test_outline_names_toc_path() -> None:
    ms = {"name": "book", "path": "/ms"}
    got = _given(ms, _layout(), _step("outline"),
                 Scope("book", "book", "the book"))
    toc = f"/ms/{structure.TOC_FILENAME}"
    check("outline give embeds the toc path",
          got == [f"  - outline: the book's outline: {toc}, and the "
                  "register's shape and thesis entries, below"], got)


def main() -> None:
    for test in (
        test_empty_gives_says_nothing_by_name,
        test_chapter_path_uses_scope_label,
        test_part_joins_sibling_chapters_in_order,
        test_chapter_scope_part_give_uses_owning_part,
        test_book_scope_flag_is_empty_for_changes,
        test_chapter_scope_flag_names_the_file,
        test_unknown_give_points_at_step_result_below,
        test_outline_names_toc_path,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
