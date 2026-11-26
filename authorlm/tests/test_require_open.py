"""`book._require_open` is the gate on finish and fail: a superseded or
failed run is closed and must not take a new result.

A broken gate lets an agent record results on a closed attempt, so the
board and the pack for the next start disagree about which run is in
force. Pure over a row dict; no workspace.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.book import _require_open  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _row(status: str, **extra) -> dict:
    base = {"id": "sr-1", "step": "draft", "status": status,
            "superseded_reason": None, "failed_reason": None}
    base.update(extra)
    return base


def test_running_and_waiting_pass() -> None:
    for status in ("running", "waiting"):
        try:
            _require_open(_row(status))
        except ValueError as err:
            raise AssertionError(f"{status} should pass: {err}") from err
    check("running and waiting are open", True)


def test_superseded_names_reason_and_restart() -> None:
    try:
        _require_open(_row("superseded",
                           superseded_reason="author rewrote the brief"))
    except ValueError as err:
        msg = str(err)
        check("names the run id", "sr-1" in msg, msg)
        check("says it was superseded", "was superseded" in msg, msg)
        check("embeds the restart reason",
              "author rewrote the brief" in msg, msg)
        check("points at book start for the step",
              "authorlm book start draft" in msg, msg)
        return
    raise AssertionError("expected ValueError for superseded")


def test_failed_names_reason_and_restart() -> None:
    try:
        _require_open(_row("failed", failed_reason="model timed out"))
    except ValueError as err:
        msg = str(err)
        check("names the run id", "sr-1" in msg, msg)
        check("says it failed", "failed" in msg and "closed" in msg, msg)
        check("embeds the failure reason", "model timed out" in msg, msg)
        check("points at book start for the step",
              "authorlm book start draft" in msg, msg)
        return
    raise AssertionError("expected ValueError for failed")


def main() -> None:
    for test in (
        test_running_and_waiting_pass,
        test_superseded_names_reason_and_restart,
        test_failed_names_reason_and_restart,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
