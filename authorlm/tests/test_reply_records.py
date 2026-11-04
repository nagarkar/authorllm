"""Paragraph-record reply unwrap (`_reply_records`) and JSON schema shape.

After #306 the flash call asks for `paragraph_schema()` with a strict
object root `{"records": [...]}`. `_reply_records` unwraps that (and a
bare list) into `{n: item}`. A silent unwrap/schema drift drops every
record or accepts a poisoned shape; the paragraph-records suite covers
storage, not these doors.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import summaries
from authorlm.extraction import VALID_RELATIONS


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_reply_records_unwrap() -> None:
    wrapped = {"records": [
        {"n": 1, "gist": "first"},
        {"n": 1, "gist": "dup"},
        {"n": "x", "gist": "bad"},
        {"gist": "no-n"},
        {"n": 3, "gist": "third"},
    ]}
    got = summaries._reply_records(wrapped)
    check("wrapped records keyed by n",
          set(got) == {1, 3}, sorted(got))
    check("duplicate n keeps the first item",
          got[1]["gist"] == "first", got[1])
    check("non-int / missing n are dropped",
          "x" not in got and None not in got)

    check("bare list unwraps the same way",
          summaries._reply_records([{"n": 2, "gist": "b"}])
          == {2: {"n": 2, "gist": "b"}})
    check("dict with two list values yields nothing (ambiguous)",
          summaries._reply_records({"a": [{"n": 1}], "b": [{"n": 2}]})
          == {})
    check("None / empty / non-list → empty",
          summaries._reply_records(None) == {}
          and summaries._reply_records([]) == {}
          and summaries._reply_records("nope") == {})


def test_paragraph_schema_shape() -> None:
    name, schema = summaries.paragraph_schema()
    check("schema name is paragraph_records",
          name == "paragraph_records", name)
    check("root requires records",
          schema.get("type") == "object"
          and schema.get("required") == ["records"],
          schema.get("required"))
    record = schema["properties"]["records"]["items"]
    props = record["properties"]
    check("move enum is the MOVES tuple",
          props["move"]["enum"] == list(summaries.MOVES),
          props["move"]["enum"])
    check("mention role enum is MENTION_ROLES",
          props["mentions"]["items"]["properties"]["role"]["enum"]
          == list(summaries.MENTION_ROLES))
    check("reference direction enum is REFERENCE_DIRECTIONS",
          props["references"]["items"]["properties"]["direction"]["enum"]
          == list(summaries.REFERENCE_DIRECTIONS))
    check("relation enum is extraction.VALID_RELATIONS (sorted)",
          props["relations"]["items"]["properties"]["relation"]["enum"]
          == sorted(VALID_RELATIONS))
    check("depends_on is a list of integers",
          props["depends_on"] == {
              "type": "array", "items": {"type": "integer"}})


def main() -> None:
    tests = [
        test_reply_records_unwrap,
        test_paragraph_schema_shape,
    ]
    for fn in tests:
        print(fn.__name__)
        fn()
    print(f"OK — {len(tests)} reply_records / schema cases")


if __name__ == "__main__":
    main()
