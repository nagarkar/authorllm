"""`book._reference` builds the restart pack's "earlier attempt" block:
independent steps withhold the prior result; others get it as reference
only, with failed vs restarted reason.

Wrong branch → a cold reader is handed the earlier draft, or a rerun is
told a failed attempt was only restarted. Hermetic — `_still_landed`
stubbed; no workspace.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.book import Step, _fenced, _reference  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _step(*, independent: bool = False) -> Step:
    return Step(id="critic", role="critic", actor="agent", grain="chapter",
                after=(), gives=(), returns="x", artifacts=(),
                leaves_alone="y", independent=independent)


def _earlier(**extra) -> dict:
    base = {"id": "sr-old", "attempt": 1, "result": {"note": "first try"},
            "superseded_reason": None, "failed_reason": None}
    base.update(extra)
    return base


def test_empty_earlier_returns_nothing() -> None:
    got = _reference(None, {"name": "book"}, _step(), [])
    check("no earlier attempts → empty", got == [], got)


def test_independent_withholds_result_on_failure() -> None:
    earlier = [_earlier(failed_reason="timed out")]
    with mock.patch("authorlm.book._still_landed",
                    return_value=["still-landed-line", ""]) as still:
        got = _reference("db", {"name": "book"}, _step(independent=True),
                         earlier)
    check("_still_landed asked with the earlier list",
          still.call_args.args[2] is earlier, still.call_args)
    text = "\n".join(got)
    check("heading says withheld",
          "## Earlier attempt: withheld" in text, text)
    check("says the step failed, not restarted",
          "and failed: timed out" in text, text)
    check("does not fence the earlier result",
          _fenced(earlier[0]["result"]) not in text, text)
    check("still-landed lines are appended",
          "still-landed-line" in text, text)


def test_reason_prefers_superseded_over_failed() -> None:
    # how comes from failed_reason; the quoted reason prefers
    # superseded_reason when both are set.
    earlier = [_earlier(failed_reason="timed out",
                        superseded_reason="author rewrote brief")]
    with mock.patch("authorlm.book._still_landed", return_value=[]):
        got = _reference(None, {"name": "book"}, _step(independent=True),
                         earlier)
    text = "\n".join(got)
    check("how is failed when failed_reason is set",
          "and failed:" in text, text)
    check("quoted reason is the superseded one",
          "author rewrote brief" in text
          and "timed out" not in text, text)


def test_non_independent_is_reference_only_with_fenced_result() -> None:
    earlier = [_earlier(attempt=2, superseded_reason="author rewrote brief",
                        result={"ok": True})]
    with mock.patch("authorlm.book._still_landed", return_value=[]):
        got = _reference("db", {"name": "book"}, _step(independent=False),
                         earlier)
    text = "\n".join(got)
    check("heading says reference only",
          "## Earlier attempt: reference only" in text, text)
    check("names attempt number and restarted wording",
          "attempt 2" in text and "was restarted" in text, text)
    check("embeds the restart reason",
          "author rewrote brief" in text, text)
    check("fences the earlier result after the still-landed gap",
          _fenced({"ok": True}) in text, text)
    check("says the earlier result has no authority",
          "has no authority" in text, text)


def test_missing_reason_falls_back() -> None:
    earlier = [_earlier()]
    with mock.patch("authorlm.book._still_landed", return_value=[]):
        got = _reference(None, {"name": "book"}, _step(), earlier)
    text = "\n".join(got)
    check("fallback reason when none recorded",
          "no reason was recorded" in text, text)
    check("without failed_reason it was restarted",
          "was restarted" in text, text)


def test_uses_latest_earlier_attempt() -> None:
    earlier = [
        _earlier(id="sr-1", attempt=1, superseded_reason="old"),
        _earlier(id="sr-2", attempt=2, failed_reason="blew up",
                 result={"n": 2}),
    ]
    with mock.patch("authorlm.book._still_landed", return_value=[]):
        got = _reference(None, {"name": "book"}, _step(), earlier)
    text = "\n".join(got)
    check("latest run id is named", "run sr-2" in text, text)
    check("latest failure wins over earlier supersede",
          "and failed" in text and "blew up" in text, text)
    check("fences the latest result",
          _fenced({"n": 2}) in text, text)


def main() -> None:
    for test in (
        test_empty_earlier_returns_nothing,
        test_independent_withholds_result_on_failure,
        test_reason_prefers_superseded_over_failed,
        test_non_independent_is_reference_only_with_fenced_result,
        test_missing_reason_falls_back,
        test_uses_latest_earlier_attempt,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
