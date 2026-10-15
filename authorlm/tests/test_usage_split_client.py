"""`usage._split` and `usage._client_name` are the report's join keys.

`_group_key` (#194) composes them; a drift in the `purpose|model`
partition or the `engine/session[:12]` render still corrupts every
`by=` column even when `_group_key`'s branch table stays intact. Pin the
primitives hermetically.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import usage


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_split() -> None:
    check("purpose|model partitions on the first pipe",
          usage._split("filter|gpt-4.1") == ("filter", "gpt-4.1"))
    check("extra pipes stay in the model half",
          usage._split("a|b|c") == ("a", "b|c"))
    check("missing pipe → empty model",
          usage._split("alone") == ("alone", ""))
    check("non-str keys stringify",
          usage._split(12) == ("12", ""))  # type: ignore[arg-type]


def test_client_name() -> None:
    check("missing / non-dict client → None",
          usage._client_name({}) is None
          and usage._client_name({"client": "x"}) is None)
    long = "abcdefghijklmnop"
    got = usage._client_name({
        "client": {"engine": "claude-code", "session": long},
    })
    check("renders engine/session truncated to 12",
          got == f"claude-code/{long[:12]}", repr(got))
    check("missing session becomes '-'",
          usage._client_name({"client": {"engine": "athena"}})
          == "athena/-")
    check("missing engine becomes '?'",
          usage._client_name({"client": {"session": "abc"}}) == "?/abc")


def main() -> None:
    for t in (test_split, test_client_name):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
