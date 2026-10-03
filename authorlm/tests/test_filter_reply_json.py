"""`filtering.reply_json` is the first parser every pasted filter reply
goes through (registry prelude, pronunciation prelude, filter record).

It promises two things: a reply wrapped in a ``` fence is tolerated, and
anything that is not JSON is refused with a `ReplyError` that echoes the
first 400 characters of what came in. The e2e and passes suites only feed
it `json.dumps(...)` output, so neither the fence-stripping branch nor the
invalid-JSON branch runs there. This file pins both.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import filtering
from authorlm.filtering import ReplyError, reply_json


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _raises(text):
    """Return the ReplyError reply_json raises for `text`, or None."""
    try:
        reply_json(text)
    except ReplyError as err:
        return err
    return None


def test_plain_json() -> None:
    got = reply_json('{"units": [], "state": "s"}')
    check("plain JSON parses to the matching dict",
          got == {"units": [], "state": "s"}, repr(got))


def test_fenced_with_language_tag() -> None:
    got = reply_json('```json\n{"units": []}\n```')
    check("```json fence is stripped", got == {"units": []}, repr(got))


def test_bare_fence() -> None:
    got = reply_json('```\n{"a": 1}\n```')
    check("bare ``` fence is stripped", got == {"a": 1}, repr(got))


def test_unclosed_fence() -> None:
    got = reply_json('```json\n{"a": 1}')
    check("opening fence with no closing fence still parses",
          got == {"a": 1}, repr(got))


def test_whitespace_around_fence() -> None:
    got = reply_json('  \n\n```json\n{"a": 1}\n```  \n\t\n')
    check("whitespace around a fenced block is tolerated",
          got == {"a": 1}, repr(got))


def test_not_json() -> None:
    err = _raises("not json at all")
    check("non-JSON raises ReplyError", err is not None)
    check("ReplyError is a ValueError", isinstance(err, ValueError))
    check("ReplyError is filtering.ReplyError",
          isinstance(err, filtering.ReplyError))
    msg = str(err)
    check("message says 'not valid JSON'", "not valid JSON" in msg, msg)
    check("message echoes the input", "not json at all" in msg, msg)


def test_head_cut_at_400() -> None:
    err = _raises("x" * 400 + "Y" * 600)
    check("long non-JSON raises ReplyError", err is not None)
    msg = str(err)
    check("message carries the first 400 characters", "x" * 400 in msg)
    check("message carries nothing past character 400", "Y" not in msg,
          msg[-120:])


def test_degenerate_inputs() -> None:
    for label, text in (("lone ``` with no newline", "```"),
                        ("empty string", ""),
                        ("None", None)):
        err = _raises(text)
        check(f"{label} raises ReplyError", err is not None)
        check(f"{label} message says 'not valid JSON'",
              "not valid JSON" in str(err), str(err))


def main() -> None:
    tests = [test_plain_json, test_fenced_with_language_tag,
             test_bare_fence, test_unclosed_fence,
             test_whitespace_around_fence, test_not_json,
             test_head_cut_at_400, test_degenerate_inputs]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
