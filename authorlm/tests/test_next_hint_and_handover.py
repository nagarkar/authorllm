"""`changes._next_hint` is the land result's "what next" line; 
`book._handed_over` is the pack section that either withholds earlier
results (independent reads) or pastes them in.

A wrong hint sends the author to push when the Doc is unlinked, or skips
the push when the tab still needs it. A wrong handover either leaks prior
outputs into a cold read or drops a done earlier result the role needs.
Pure over constructed runs — no workspace.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.book import (Definition, Layout, Scope, Step,  # noqa: E402
                           _handed_over)
from authorlm.changes import _next_hint  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _step(step_id: str, grain: str, after: tuple[str, ...] = (),
          *, independent: bool = False) -> Step:
    return Step(id=step_id, role="role", actor="agent", grain=grain,
                after=after, gives=(), returns="x", artifacts=(),
                leaves_alone="y", independent=independent)


def test_next_hint() -> None:
    ms = {"name": "MS"}
    unlinked = _next_hint(ms, "one.md", "unlinked", "sr-9")
    check("unlinked skips the push and goes straight to book done",
          unlinked.startswith("when the step is finished:")
          and "doc push" not in unlinked
          and "book done sr-9" in unlinked
          and "-m MS" in unlinked, unlinked)

    linked = _next_hint(ms, "one.md", "synced", "sr-9")
    check("any linked state asks for doc push first",
          linked.startswith("authorlm doc push one.md -m MS; then ")
          and "book done sr-9" in linked, linked)

    for state in ("conflict", "ahead", "behind"):
        hint = _next_hint(ms, "ch.md", state, "sr-2")
        check(f"{state} also asks for a push before done",
              "doc push ch.md" in hint and "book done sr-2" in hint, hint)


def test_handed_over() -> None:
    draft = _step("draft", "chapter")
    critic = _step("critic", "chapter", ("draft",))
    cold = _step("cold-read", "chapter", ("draft",), independent=True)
    definition = Definition(path=Path("w.toml"),
                            steps=(draft, critic, cold), text_hash="x")
    layout = Layout(
        chapters=(Scope("chapter", "dc-1", "one.md"),),
        parts=(),
        part_of={},
        not_yet_documents=(),
    )
    scope = Scope("chapter", "dc-1", "one.md")

    withheld = _handed_over(definition, layout, {}, cold, scope)
    check("an independent step withholds earlier outputs on purpose",
          any("withheld from this pack on purpose" in ln for ln in withheld)
          and not any(ln.startswith("### draft") for ln in withheld),
          withheld)

    empty = _handed_over(definition, layout, {}, critic, scope)
    check("a dependent step with no earlier done run says Nothing",
          any("Nothing: no step comes before this one." in ln
              for ln in empty), empty)

    runs = {
        ("draft", "dc-1"): [{
            "status": "done", "id": "sr-d1", "scope_label": "one.md",
            "result": {"note": "drafted"},
        }],
    }
    handed = _handed_over(definition, layout, runs, critic, scope)
    check("a done earlier run is pasted under its step heading",
          any(ln.startswith("### draft (role), one.md, run sr-d1")
              for ln in handed)
          and any('"note": "drafted"' in ln for ln in handed), handed)
    check("a running earlier attempt is not handed over",
          "Nothing" in "\n".join(_handed_over(
              definition, layout,
              {("draft", "dc-1"): [{"status": "running", "id": "sr-x",
                                    "scope_label": "one.md",
                                    "result": {}}]},
              critic, scope)))


def main() -> None:
    for test in (test_next_hint, test_handed_over):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
