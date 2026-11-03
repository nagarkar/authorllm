"""The recordings' freshness rule (tests/_doc_freshness.py), with a
fake clock, log and Doc: no Google call, no file touched.
Run: python3 tests/test_doc_freshness.py
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _doc_freshness import LogDeleted, decide, deleted_message, parse  # noqa: E402

PASSED = 0
NOW = dt.datetime(2026, 10, 30, 18, 0, tzinfo=dt.timezone(
    dt.timedelta(hours=-7)))


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


class Doc:
    """The fixture Doc's date line, counting reads."""

    def __init__(self, when):
        self.when, self.reads = when, 0

    def __call__(self):
        self.reads += 1
        return self.when


def days(n: float) -> dt.datetime:
    return NOW - dt.timedelta(days=n)


def main_test() -> None:
    print("the recordings' freshness rule:")
    doc = Doc(days(1))
    check("a log under 7 days old: fresh, and the Doc is not read",
          decide(NOW, days(6.9), True, True, doc) == "fresh"
          and doc.reads == 0)

    doc = Doc(days(8))
    check("a log 7 days old or more, the Doc as old: re-record",
          decide(NOW, days(7), True, True, doc) == "record"
          and doc.reads == 1)

    doc = Doc(None)
    check("an old log and no date line in the Doc: re-record",
          decide(NOW, days(30), True, True, doc) == "record")

    doc = Doc(days(9))
    check("a missing log, the Doc 7 days old or more: re-record",
          decide(NOW, None, False, True, doc) == "record")

    doc = Doc(days(2))
    try:
        decide(NOW, None, False, True, doc)
        msg = ""
    except LogDeleted as err:
        msg = str(err)
    check("a missing log, the Doc recorded 2 days ago: FAIL, to the agent",
          msg == deleted_message(days(2))
          and "fake.log was deleted" in msg
          and "find what deleted tests/doc_cache/fake.log" in msg
          and "only after a human approves" in msg
          and "Never rewrite that date automatically" in msg, msg)

    doc = Doc(days(2))
    check("no Google token: skip, with no Doc read and no failure",
          decide(NOW, None, False, False, doc) == "skip"
          and doc.reads == 0)
    check("...and with a stale log too",
          decide(NOW, days(20), True, False, doc) == "skip")

    check("the log and the Doc line parse, with their timezone",
          parse("2026-10-30T17:40:00-07:00\n") == dt.datetime(
              2026, 10, 30, 17, 40, tzinfo=dt.timezone(
                  dt.timedelta(hours=-7)))
          and parse("Last recorded: 2026-10-30T17:40:00-07:00")
          is not None)
    check("a time without a timezone, or no time, is no date at all",
          parse("2026-10-30T17:40:00") is None and parse("") is None
          and parse("garbage") is None)
    # The wiring: no token, so ensure_fresh skips with a one-line notice
    # and reads neither the Doc nor the network.
    import contextlib
    import io
    import tempfile

    import _doc_freshness as fr
    saved = (fr.LOG, fr.TOKEN, fr.read_doc_time)
    tmp = Path(tempfile.mkdtemp())
    fr.LOG, fr.TOKEN = tmp / "fake.log", tmp / "no-token.json"
    fr.read_doc_time = lambda: (_ for _ in ()).throw(
        AssertionError("the Doc was read"))
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            action = fr.ensure_fresh()
    finally:
        fr.LOG, fr.TOKEN, fr.read_doc_time = saved
    check("ensure_fresh with no token: 'skip' and one notice line",
          action == "skip" and out.getvalue().count("\n") == 1
          and "no Google token" in out.getvalue(), out.getvalue())
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    main_test()
