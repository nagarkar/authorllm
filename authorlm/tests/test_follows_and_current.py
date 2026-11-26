"""`book._follows` and `book._current` decide which earlier steps a run
waits on, and which attempt of a step/scope is still in force. A broken
transitive after-list lets a step start too soon; treating a superseded
latest attempt as current hides that the step is open again.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.book import Step, _current, _follows


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _step(step_id: str, after: tuple[str, ...] = ()) -> Step:
    return Step(id=step_id, role="role", actor="agent", grain="book",
                after=after, gives=(), returns="x", artifacts=(),
                leaves_alone="y", independent=False)


def test_follows_is_transitive_and_deduped() -> None:
    steps = {
        "a": _step("a"),
        "b": _step("b", ("a",)),
        "c": _step("c", ("b",)),
        "d": _step("d", ("a", "b")),
    }
    check("root follows nothing", _follows(steps, "a") == set())
    check("one hop", _follows(steps, "b") == {"a"})
    check("two hops", _follows(steps, "c") == {"a", "b"})
    check("shared ancestors are not doubled",
          _follows(steps, "d") == {"a", "b"})


def test_current_ignores_a_superseded_latest_attempt() -> None:
    check("no runs → none", _current({}, "draft", "dc-1") is None)
    done = {"status": "done", "id": "sr-1"}
    check("latest done is current",
          _current({("draft", "dc-1"): [done]}, "draft", "dc-1") is done)
    superseded = {"status": "superseded", "id": "sr-1"}
    check("latest superseded leaves the cell empty",
          _current({("draft", "dc-1"): [superseded]},
                   "draft", "dc-1") is None)
    rerun = {"status": "running", "id": "sr-2"}
    check("a later attempt after supersede is current",
          _current({("draft", "dc-1"): [superseded, rerun]},
                   "draft", "dc-1") is rerun)


def main() -> None:
    for test in (
        test_follows_is_transitive_and_deduped,
        test_current_ignores_a_superseded_latest_attempt,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
