"""`staging.apply_accepted` is the local-road compose that turns accepted
filter/critique threads into finished essay bytes.

It reuses `passes.compose_marked_text` (verbatim drift check) then collapses
every `<<old>>{{new}}` form to its new half. A silent change here either
writes the wrong paragraph after a mid-pause edit or applies an edit the
author never accepted. Settle e2e covers the full pause; this file pins the
pure apply door — success, multi-unit order, and drift refusal — without a
database.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import staging
from authorlm import threads as th


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _thread(old: str, new: str, n: int) -> dict:
    return {
        "proposed_old": old,
        "proposed_new": new,
        "metadata": json.dumps({"kind": "replace", "anchor_paragraph": n}),
    }


ESSAY = "Alpha paragraph.\n\nBeta paragraph.\n\nGamma paragraph."


def test_single_replace() -> None:
    threads = [_thread("Alpha paragraph.", "Alpha rewritten.", 1)]
    got = staging.apply_accepted(ESSAY, threads)
    check("accepted replace collapses to the new half",
          got == "Alpha rewritten.\n\nBeta paragraph.\n\nGamma paragraph.\n",
          repr(got))
    check("no pending markers remain", "<<" not in got and "{{" not in got)


def test_multi_unit_order() -> None:
    threads = [
        _thread("Gamma paragraph.", "Gamma last.", 3),
        _thread("Alpha paragraph.", "Alpha first.", 1),
    ]
    got = staging.apply_accepted(ESSAY, threads)
    check("both accepted units land in document order",
          got == "Alpha first.\n\nBeta paragraph.\n\nGamma last.\n",
          repr(got))


def test_drift_refuses() -> None:
    threads = [_thread("Alpha paragraph.", "Alpha rewritten.", 1)]
    try:
        staging.apply_accepted(
            "Alpha moved.\n\nBeta paragraph.\n\nGamma paragraph.", threads)
        raised = ""
    except ValueError as err:
        raised = str(err)
    check("drifted proposed_old raises rather than guessing",
          "no longer matches" in raised, raised)


def test_is_marked_narrow() -> None:
    # The apply door's pause guard must ignore author {{templating}}.
    check("bare {{title}} is not mid-settle",
          not staging.is_marked("See {{title}} in the preface."))
    check("has_replacement agrees with is_marked on templating",
          not th.has_replacement("See {{title}} in the preface."))
    marked = "<<Alpha paragraph.>>{{Alpha rewritten.}}"
    check("a real replace form is mid-settle", staging.is_marked(marked))
    check("has_replacement sees the replace form",
          th.has_replacement(marked))


def main() -> None:
    for t in (test_single_replace, test_multi_unit_order, test_drift_refuses,
              test_is_marked_narrow):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
