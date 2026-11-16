"""`book._unmet` and `book._rests_on_restarted` are the wait / flag gates
on the status board: which earlier scopes a step still needs, and when a
done wider step rests on work that was restarted underneath it.

A wrong wait list starts a chapter before its part is done; a missed
restart flag leaves the author thinking a book-level step still stands.
Pure over a constructed Definition + Layout — no workspace.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.book import (BOOK, Definition, Layout, Scope, Step,  # noqa: E402
                           _rests_on_restarted, _unmet)


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _step(step_id: str, grain: str, after: tuple[str, ...] = ()) -> Step:
    return Step(id=step_id, role="role", actor="agent", grain=grain,
                after=after, gives=(), returns="x", artifacts=(),
                leaves_alone="y", independent=False)


def _definition(*steps: Step) -> Definition:
    return Definition(path=Path("workflow.toml"), steps=steps,
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


def test_unmet_lists_incomplete_related_scopes() -> None:
    draft = _step("draft", "chapter")
    critic = _step("critic", "chapter", ("draft",))
    assessment = _step("assessment", "book", ("critic",))
    definition = _definition(draft, critic, assessment)
    layout = _layout()

    check("no after → nothing unmet",
          _unmet(definition, layout, {}, draft, "dc-1") == [])

    waiting = _unmet(definition, layout, {}, critic, "dc-1")
    check("missing earlier run waits on that chapter",
          waiting == [{"step": "draft", "scope": "dc-1"}], waiting)

    runs = {("draft", "dc-1"): [{"status": "done", "id": "sr-1"}]}
    check("done earlier clears the wait",
          _unmet(definition, layout, runs, critic, "dc-1") == [])

    book_waiting = _unmet(definition, layout, {
        ("critic", "dc-1"): [{"status": "done", "id": "sr-c1"}],
        ("critic", "dc-2"): [{"status": "running", "id": "sr-c2"}],
    }, assessment, BOOK)
    check("book step waits on every unfinished chapter of what it follows",
          book_waiting == [
              {"step": "critic", "scope": "dc-2"},
              {"step": "critic", "scope": "dc-3"},
          ], book_waiting)

    # A part step that follows a chapter grain has no related chapter
    # scopes when the layout holds none — that is the scope=None wait.
    empty_part = Layout(chapters=(), parts=(Scope("part", "dc-empty", "e.md"),),
                        part_of={}, not_yet_documents=())
    chapter_step = _step("line", "chapter")
    part_after = _step("part-edit", "part", ("line",))
    waiting_none = _unmet(_definition(chapter_step, part_after), empty_part,
                          {}, part_after, "dc-empty")
    check("a part with no chapters waits with scope None",
          waiting_none == [{"step": "line", "scope": None}], waiting_none)


def test_rests_on_restarted_flags_wider_done_runs() -> None:
    draft = _step("draft", "chapter")
    assessment = _step("assessment", "book", ("draft",))
    definition = _definition(draft, assessment)
    layout = _layout()

    done_book = {"status": "done", "id": "sr-a", "scope": BOOK,
                 "started_at": "2026-11-01T00:00:00Z"}
    check("a non-done run never rests on a restart",
          _rests_on_restarted(
              definition, layout, {}, assessment,
              {**done_book, "status": "running"}) is False)

    unfinished = {
        ("draft", "dc-1"): [{"status": "done", "id": "sr-1",
                             "started_at": "2026-10-01T00:00:00Z"}],
        # dc-2/dc-3 missing → unmet
    }
    check("unmet earlier scopes flag the done book run",
          _rests_on_restarted(definition, layout, unfinished, assessment,
                              done_book) is True)

    all_done = {
        ("draft", key): [{"status": "done", "id": f"sr-{key}",
                          "started_at": "2026-10-01T00:00:00Z"}]
        for key in ("dc-1", "dc-2", "dc-3")
    }
    check("all earlier done and older → not resting on a restart",
          _rests_on_restarted(definition, layout, all_done, assessment,
                              done_book) is False)

    redone = {
        ("draft", "dc-1"): [{"status": "done", "id": "sr-1",
                             "started_at": "2026-10-01T00:00:00Z"}],
        ("draft", "dc-2"): [{"status": "done", "id": "sr-2",
                             "started_at": "2026-11-02T00:00:00Z"}],
        ("draft", "dc-3"): [{"status": "done", "id": "sr-3",
                             "started_at": "2026-10-01T00:00:00Z"}],
    }
    check("an earlier scope redone later flags the wider run",
          _rests_on_restarted(definition, layout, redone, assessment,
                              done_book) is True)

    superseded = {
        ("draft", "dc-1"): [{"status": "superseded", "id": "sr-old",
                             "started_at": "2026-10-01T00:00:00Z"}],
        ("draft", "dc-2"): [{"status": "done", "id": "sr-2",
                             "started_at": "2026-10-01T00:00:00Z"}],
        ("draft", "dc-3"): [{"status": "done", "id": "sr-3",
                             "started_at": "2026-10-01T00:00:00Z"}],
    }
    check("a superseded latest earlier attempt is unmet → flagged",
          _rests_on_restarted(definition, layout, superseded, assessment,
                              done_book) is True)


def main() -> None:
    for test in (
        test_unmet_lists_incomplete_related_scopes,
        test_rests_on_restarted_flags_wider_done_runs,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
