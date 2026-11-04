"""`filtering.validate_reply` is §2.5's filter-reply door: every pasted
or model reply for `filter record` / settle must pass here before any
`<<old>>{{new}}` is staged.

It refuses (never repairs) on shape, window order, echo mismatch,
illegal actions, empty replace, reserved markers, sequential empty
state, and oversize state. The only downgrade is a no-op replace →
keep. The e2e and passes suites feed it already-valid replies, so the
refusal branches never run there. This file pins each branch hermetically.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.filtering import STATE_CAP, ReplyError, validate_reply
from authorlm.passes import echo_of


UNITS = [
    "Alpha says a thing about the world today.",
    "Beta repeats a phrase for emphasis alone.",
    "Gamma closes the short essay cleanly.",
]


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _item(n: int, action: str = "keep", *, new: str | None = None,
          echo: str | None = None, why: str = "", ref=None) -> dict:
    unit = UNITS[n - 1]
    item = {"n": n, "echo": echo if echo is not None else echo_of(unit),
            "action": action, "why": why}
    if new is not None:
        item["new"] = new
    if ref is not None:
        item["ref"] = ref
    return item


def _ok(items, *, klass: str = "independent", state=None,
        window=(1, 3)) -> dict:
    raw = {"units": items}
    if state is not None:
        raw["state"] = state
    return validate_reply(raw, UNITS, window, klass)


def _raises(raw, *, klass: str = "independent", window=(1, 3)):
    try:
        validate_reply(raw, UNITS, window, klass)
    except ReplyError as err:
        return err
    return None


def test_happy_keep_and_replace() -> None:
    got = _ok([
        _item(1, "keep"),
        _item(2, "replace", new="Beta says it once, clearly.",
              why="duplicate"),
        _item(3, "keep"),
    ], state="ledger")
    check("one replace and two keeps",
          got == {"edits": [{"n": 2, "new": "Beta says it once, clearly.",
                             "why": "duplicate", "ref": None}],
                  "keeps": [1, 3], "state": "ledger"},
          repr(got))


def test_not_object_and_no_units() -> None:
    err = _raises(["not", "a", "dict"])
    check("non-object raises", err is not None)
    check("non-object names JSON object", "JSON object" in str(err), err)
    err = _raises({"units": "nope"})
    check("missing units list raises", err is not None)
    check("missing units names the list", "`units` list" in str(err), err)


def test_window_order() -> None:
    err = _raises({"units": [_item(1), _item(3), _item(2)]})
    check("out-of-order ns raise", err is not None)
    check("order refusal names out of order or expected window",
          "out of order" in str(err) or "expected n=" in str(err), err)
    err = _raises({"units": [_item(1), _item(2)]}, window=(1, 3))
    check("missing n raises", err is not None)
    check("missing n is named", "missing n=" in str(err), err)


def test_echo_mismatch() -> None:
    err = _raises({"units": [
        _item(1, echo="wrong echo words here"),
        _item(2), _item(3),
    ]})
    check("bad echo raises", err is not None)
    check("echo mismatch names the unit", "unit 1 echo mismatch" in str(err),
          err)


def test_insert_and_unknown_action() -> None:
    err = _raises({"units": [
        _item(1, "insert", new="new paragraph"),
        _item(2), _item(3),
    ]})
    check("insert raises", err is not None)
    check("insert refusal names ADDS", "never ADDS" in str(err), err)
    err = _raises({"units": [
        _item(1, "delete"), _item(2), _item(3),
    ]})
    check("unknown action raises", err is not None)
    check("unknown action names keep/replace",
          "keep" in str(err) and "replace" in str(err), err)


def test_empty_replace_and_markers() -> None:
    err = _raises({"units": [
        _item(1, "replace", new="   "),
        _item(2), _item(3),
    ]})
    check("empty replace raises", err is not None)
    check("empty replace names no text", "no text" in str(err), err)
    err = _raises({"units": [
        _item(1, "replace", new="Alpha <<old>> kept"),
        _item(2), _item(3),
    ]})
    check("reserved marker raises", err is not None)
    check("reserved marker names <<", "'<<'" in str(err), err)


def test_noop_replace_becomes_keep() -> None:
    got = _ok([
        _item(1, "replace", new=UNITS[0]),
        _item(2), _item(3),
    ])
    check("no-op replace downgrades to keep",
          got["edits"] == [] and got["keeps"] == [1, 2, 3], repr(got))


def test_sequential_state_and_cap() -> None:
    err = _raises({"units": [_item(1), _item(2), _item(3)]},
                  klass="sequential")
    check("sequential without state raises", err is not None)
    check("sequential refusal names state", "state" in str(err), err)
    err = _raises({"units": [_item(1), _item(2), _item(3)],
                   "state": "x" * (STATE_CAP + 1)})
    check("oversize state raises", err is not None)
    check("oversize refusal names the cap",
          f"{STATE_CAP:,}" in str(err), err)
    err = _raises({"units": [_item(1), _item(2), _item(3)],
                   "state": {"not": "text"}})
    check("structured state raises", err is not None)
    check("structured state names opaque TEXT", "opaque TEXT" in str(err),
          err)


def main() -> None:
    tests = [
        test_happy_keep_and_replace,
        test_not_object_and_no_units,
        test_window_order,
        test_echo_mismatch,
        test_insert_and_unknown_action,
        test_empty_replace_and_markers,
        test_noop_replace_becomes_keep,
        test_sequential_state_and_cap,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
