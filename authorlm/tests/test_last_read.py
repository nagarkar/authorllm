"""`changes._last_read` picks the author's latest finished person-step
that was given `changes` and that covers the pack's scope.

Wrong pick → the pack under- or over-counts "new since the author last
read", so the author is told every landed edit is fresh or that none
are. Pure over a constructed Definition/Layout and run dicts; no
workspace.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.book import BOOK, Definition, Layout, Scope, Step  # noqa: E402
from authorlm.changes import _last_read  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _step(step_id: str, *gives: str, actor: str = "human:author",
          grain: str = "chapter") -> Step:
    return Step(id=step_id, role="author", actor=actor, grain=grain,
                after=(), gives=gives, returns="x", artifacts=(),
                leaves_alone="y", independent=False)


def _definition(*steps: Step) -> Definition:
    return Definition(path=Path("/ms/_book/workflow.toml"), steps=steps,
                      text_hash="deadbeef")


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


def _run(step: str, *, actor: str = "human:author", grain: str = "chapter",
         scope: str = "dc-1", finished_at: str = "2026-11-01T10:00:00Z",
         failed_reason: str | None = None) -> dict:
    return {"id": f"sr-{step}-{scope}-{finished_at}", "step": step,
            "actor": actor, "grain": grain, "scope": scope,
            "finished_at": finished_at, "failed_reason": failed_reason}


def test_empty_runs_is_none() -> None:
    definition = _definition(_step("read", "changes", grain="part"))
    got = _last_read(definition, _layout(), {},
                     Scope("chapter", "dc-1", "one.md"))
    check("no runs → None", got is None, got)


def test_ignores_agent_unfinished_failed_and_non_changes_steps() -> None:
    definition = _definition(
        _step("draft", "chapter", actor="agent"),
        _step("read", "changes", grain="part"),
        _step("critic", "findings", actor="agent"),
    )
    runs = {
        "draft": [_run("draft", actor="agent", finished_at="2026-11-05T10:00:00Z")],
        "read": [
            _run("read", grain="part", scope="dc-p1", finished_at=None),
            _run("read", grain="part", scope="dc-p1",
                 finished_at="2026-11-04T10:00:00Z",
                 failed_reason="timed out"),
        ],
        "critic": [_run("critic", actor="agent",
                        finished_at="2026-11-06T10:00:00Z")],
    }
    got = _last_read(definition, _layout(), runs,
                     Scope("chapter", "dc-1", "one.md"))
    check("agent / unfinished / failed / non-changes → None",
          got is None, got)


def test_exact_scope_and_latest_finished_win() -> None:
    definition = _definition(_step("read", "changes", grain="chapter"))
    older = _run("read", scope="dc-1", finished_at="2026-11-01T10:00:00Z")
    newer = _run("read", scope="dc-1", finished_at="2026-11-03T10:00:00Z")
    other = _run("read", scope="dc-2", finished_at="2026-11-04T10:00:00Z")
    runs = {"read": [older, newer, other]}
    got = _last_read(definition, _layout(), runs,
                     Scope("chapter", "dc-1", "one.md"))
    check("exact chapter match picks the later finished_at",
          got is newer, got)
    check("a later read of another chapter does not cover this one",
          got["scope"] == "dc-1", got)


def test_book_grain_covers_every_scope() -> None:
    definition = _definition(_step("approve", "changes", grain="book"))
    book_read = _run("approve", grain="book", scope=BOOK,
                     finished_at="2026-11-02T10:00:00Z")
    runs = {"approve": [book_read]}
    for scope in (
        Scope("book", BOOK, "the book"),
        Scope("part", "dc-p2", "p2.md"),
        Scope("chapter", "dc-3", "three.md"),
    ):
        got = _last_read(definition, _layout(), runs, scope)
        check(f"book read covers {scope.grain}/{scope.key}",
              got is book_read, got)


def test_part_read_covers_its_chapters_only() -> None:
    definition = _definition(_step("part-read", "changes", grain="part"))
    part1 = _run("part-read", grain="part", scope="dc-p1",
                 finished_at="2026-11-02T10:00:00Z")
    part2 = _run("part-read", grain="part", scope="dc-p2",
                 finished_at="2026-11-05T10:00:00Z")
    runs = {"part-read": [part1, part2]}
    in_part = _last_read(definition, _layout(), runs,
                         Scope("chapter", "dc-2", "two.md"))
    check("part read covers a chapter it holds",
          in_part is part1, in_part)
    other = _last_read(definition, _layout(), runs,
                       Scope("chapter", "dc-3", "three.md"))
    check("part read does not cover another part's chapter",
          other is part2, other)
    part_self = _last_read(definition, _layout(), runs,
                           Scope("part", "dc-p1", "p1.md"))
    check("part read covers the part scope itself",
          part_self is part1, part_self)


def main() -> None:
    for test in (
        test_empty_runs_is_none,
        test_ignores_agent_unfinished_failed_and_non_changes_steps,
        test_exact_scope_and_latest_finished_win,
        test_book_grain_covers_every_scope,
        test_part_read_covers_its_chapters_only,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
