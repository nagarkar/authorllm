"""`gdocs._execute` rides out Docs/Drive quota and server errors (#440).

A `doc push --rebuild` sends more than the Docs write quota (60 writes
per minute per user) allows; without retry it died with HttpError 429
partway through. Every bridge request now goes through `_execute`, which
retries 429 and 5xx with backoff and gives up after `RETRY_TRIES`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httplib2
from googleapiclient.errors import HttpError

from authorlm import gdocs


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _error(status: int, retry_after: str | None = None) -> HttpError:
    info = {"status": str(status)}
    if retry_after is not None:
        info["retry-after"] = retry_after
    return HttpError(httplib2.Response(info), b'{"error": "quota"}')


class FakeRequest:
    def __init__(self, failures: list[HttpError], result=None):
        self.failures = list(failures)
        self.result = result
        self.calls = 0

    def execute(self):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return self.result


def _capture_sleeps() -> list[float]:
    waits: list[float] = []
    gdocs._sleep = waits.append
    return waits


def test_retries_429_then_succeeds() -> None:
    waits = _capture_sleeps()
    req = FakeRequest([_error(429), _error(429, retry_after="30")],
                      result={"ok": True})
    out = gdocs._execute(req)
    check("returns the eventual result", out == {"ok": True}, out)
    check("three attempts", req.calls == 3, req.calls)
    check("slept twice", len(waits) == 2, waits)
    check("Retry-After honoured", waits[1] >= 30, waits)
    check("waits stay under the cap",
          all(w <= gdocs.RETRY_MAX_WAIT for w in waits), waits)


def test_gives_up_after_limit() -> None:
    waits = _capture_sleeps()
    req = FakeRequest([_error(503)] * 20)
    try:
        gdocs._execute(req)
        raised = None
    except HttpError as err:
        raised = err
    check("raises after the limit", raised is not None)
    check("tried RETRY_TRIES times", req.calls == gdocs.RETRY_TRIES, req.calls)
    check("slept between tries", len(waits) == gdocs.RETRY_TRIES - 1, waits)
    check("last wait reaches about a minute", waits[-1] >= 45, waits)


def test_other_errors_not_retried() -> None:
    _capture_sleeps()
    req = FakeRequest([_error(404)])
    try:
        gdocs._execute(req)
    except HttpError:
        pass
    check("404 raised at once", req.calls == 1, req.calls)


if __name__ == "__main__":
    test_retries_429_then_succeeds()
    test_gives_up_after_limit()
    test_other_errors_not_retried()
    print("all ok")
