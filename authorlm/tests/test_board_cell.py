"""`book._cell`, `waited_on` and `_start_command` are the one source of
step state for the status board and the diagram.

A wrong cell state starts a blocked step, hides a ready one, or lets the
board and the diagram disagree. Pure over a constructed Definition +
Layout — no workspace.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.book import (BOOK, Definition, Layout, Scope, Step,  # noqa: E402
                           _cell, _start_command, contract_hash, waited_on)


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _step(step_id: str, grain: str, after: tuple[str, ...] = (),
          actor: str = "agent") -> Step:
    return Step(id=step_id, role="role", actor=actor, grain=grain,
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
        ),
        parts=(Scope("part", "dc-p1", "p1.md"),),
        part_of={"dc-1": "dc-p1", "dc-2": "dc-p1"},
        not_yet_documents=(),
    )


def _run(status: str, *, run_id: str = "sr-1", attempt: int = 1,
         actor: str = "agent", contract: str | None = None,
         step: Step | None = None, started_at: str = "2026-11-01T00:00:00Z",
         scope: str = "dc-1") -> dict:
    return {
        "status": status, "id": run_id, "attempt": attempt, "actor": actor,
        "contract_hash": (contract if contract is not None
                          else contract_hash(step or _step("x", "chapter"))),
        "started_at": started_at, "scope": scope,
    }


def test_cell_states() -> None:
    draft = _step("draft", "chapter")
    critic = _step("critic", "chapter", ("draft",), actor="human:author")
    definition = _definition(draft, critic)
    layout = _layout()

    ready = _cell(definition, layout, {}, draft, "dc-1")
    check("no run and nothing unmet → ready",
          ready["state"] == "ready" and ready["run"] is None
          and ready["blocked_by"] == [] and ready["earlier_attempts"] == 0,
          ready)

    blocked = _cell(definition, layout, {}, critic, "dc-1")
    check("no run and unmet earlier → blocked with that wait",
          blocked["state"] == "blocked"
          and blocked["blocked_by"] == [{"step": "draft", "scope": "dc-1"}]
          and blocked["actor"] == "human:author", blocked)

    done_runs = {("draft", "dc-1"): [_run("done", step=draft)]}
    check("a done run in force reports its status and id",
          _cell(definition, layout, done_runs, draft, "dc-1")["state"]
          == "done"
          and _cell(definition, layout, done_runs, draft, "dc-1")["run"]
          == "sr-1")
    check("earlier done clears the wait → critic is ready",
          _cell(definition, layout, done_runs, critic, "dc-1")["state"]
          == "ready")

    waiting_run = _run("waiting", step=critic, actor="human:author")
    waiting = _cell(definition, layout,
                    {**done_runs, ("critic", "dc-1"): [waiting_run]},
                    critic, "dc-1")
    check("a waiting run keeps the person who holds it",
          waiting["state"] == "waiting" and waiting["actor"] == "human:author"
          and waiting["blocked_by"] == [], waiting)

    superseded_only = {
        ("draft", "dc-1"): [_run("superseded", step=draft, run_id="sr-old")],
    }
    alone = _cell(definition, layout, superseded_only, draft, "dc-1")
    check("a superseded latest is not in force → ready again",
          alone["state"] == "ready" and alone["run"] is None
          and alone["earlier_attempts"] == 1, alone)

    rerun = {
        ("critic", "dc-1"): [
            _run("superseded", step=critic, run_id="sr-old", attempt=1),
            _run("running", step=critic, run_id="sr-new", attempt=2,
                 contract="stale-hash"),
        ],
    }
    cell = _cell(definition, layout, {**done_runs, **rerun}, critic, "dc-1")
    check("rerun counts earlier attempts and flags a stale contract",
          cell["state"] == "running" and cell["run"] == "sr-new"
          and cell["attempt"] == 2 and cell["earlier_attempts"] == 1
          and cell["contract_changed"] is True, cell)


def test_waited_on_and_start_command() -> None:
    check("an agent is waited on by no one", waited_on("agent") is None)
    check("human:author is named 'the author'",
          waited_on("human:author") == "the author")
    check("any other human keeps their name",
          waited_on("human:editor") == "editor")

    ms = {"name": "MS"}
    chapter = _step("draft", "chapter")
    book_step = _step("interview", "book")
    check("a chapter start names --scope with the file label",
          _start_command(ms, chapter, Scope("chapter", "dc-1", "one.md"))
          == "authorlm book start draft --scope one.md -m MS")
    check("a book-grain start omits --scope",
          _start_command(ms, book_step, Scope("book", BOOK, "book"))
          == "authorlm book start interview -m MS")
    check("the book scope key also omits --scope for a chapter-grain step",
          _start_command(ms, chapter, Scope("book", BOOK, "book"))
          == "authorlm book start draft -m MS")


def main() -> None:
    for test in (test_cell_states, test_waited_on_and_start_command):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
