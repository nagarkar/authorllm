"""`filtering.filtered_prefix` is the autoregressive conditioning block
for sequential filter runs: later windows must see THIS RUN's rewrites
of earlier units, not the original manuscript text.

Dead thread states (withdrawn / rejected / declined) must fall back to
the source unit — otherwise a discarded edit re-conditions every later
window and the pass double-removes or falsely declines. Pending (and
other live) states must keep rewriting. Before this file, F9 only
asserted that a live proposed_new appears in block B.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.filtering import filtered_prefix


UNITS = ["Unit one original.", "Unit two original.", "Unit three."]


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _thread(anchor: int, proposed_new: str, state: str) -> dict:
    return {
        "state": state,
        "proposed_new": proposed_new,
        "metadata": json.dumps({"anchor_paragraph": anchor}),
    }


def test_live_pending_rewrites() -> None:
    got = filtered_prefix(
        UNITS, window_start=3,
        threads=[_thread(1, "Unit one rewritten.", "pending"),
                 _thread(2, "Unit two rewritten.", "accepted")])
    check("pending rewrite appears in the prefix",
          "[1] Unit one rewritten." in got, got)
    check("accepted rewrite appears in the prefix",
          "[2] Unit two rewritten." in got, got)
    check("units are blank-line separated",
          got == ("[1] Unit one rewritten.\n\n"
                  "[2] Unit two rewritten."), got)


def test_dead_states_fall_back_to_source() -> None:
    for state in ("withdrawn", "rejected", "declined"):
        got = filtered_prefix(
            UNITS, window_start=2,
            threads=[_thread(1, f"DEAD via {state}", state)])
        check(f"{state}: source unit is restored",
              got == "[1] Unit one original.", got)
        check(f"{state}: discarded rewrite is absent",
              f"DEAD via {state}" not in got, got)


def test_empty_prefix_before_first_unit() -> None:
    got = filtered_prefix(UNITS, window_start=1, threads=[
        _thread(1, "should never appear", "pending")])
    check("window starting at unit 1 has an empty prefix", got == "",
          repr(got))


def test_missing_anchor_ignored() -> None:
    got = filtered_prefix(
        UNITS, window_start=2,
        threads=[{"state": "pending", "proposed_new": "orphan",
                  "metadata": json.dumps({"kind": "replace"})}])
    check("a thread without anchor_paragraph leaves the source",
          got == "[1] Unit one original.", got)


def main() -> None:
    tests = [test_live_pending_rewrites, test_dead_states_fall_back_to_source,
             test_empty_prefix_before_first_unit, test_missing_anchor_ignored]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
