"""`media_gen._scrub` replaces base64 blobs with their length in job logs.

`start` / `poll` dump the Gemini operation JSON beside each take. Leaving
`data` fields intact would write megabytes of base64 into
`.authorlm/clips/jobs/` and into error snippets. Wrong scrubbing either
leaks the payload or mangles non-`data` fields the poll path still reads.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.media_gen import _scrub


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_replaces_data_strings_with_length() -> None:
    blob = "abcd" * 10  # 40 chars
    got = _scrub({"data": blob, "mimeType": "image/png"})
    check("data string becomes a length marker",
          got["data"] == "<40 base64 chars>", got)
    check("sibling keys are preserved",
          got["mimeType"] == "image/png", got)


def test_walks_nested_dicts_and_lists() -> None:
    payload = {
        "response": {
            "candidates": [
                {"content": {"parts": [{"data": "xy"}, {"text": "hi"}]}},
                {"data": "zzzz"},
            ]
        },
        "name": "operations/1",
    }
    got = _scrub(payload)
    check("nested list item data is scrubbed",
          got["response"]["candidates"][0]["content"]["parts"][0]["data"]
          == "<2 base64 chars>", got)
    check("sibling text is untouched",
          got["response"]["candidates"][0]["content"]["parts"][1]["text"]
          == "hi", got)
    check("second candidate data is scrubbed",
          got["response"]["candidates"][1]["data"] == "<4 base64 chars>",
          got)
    check("top-level non-data keys survive",
          got["name"] == "operations/1", got)


def test_non_string_data_and_primitives() -> None:
    check("data dict is walked, not length-tagged",
          _scrub({"data": {"nested": 1}}) == {"data": {"nested": 1}})
    check("data list is walked",
          _scrub({"data": ["a", "b"]}) == {"data": ["a", "b"]})
    check("plain string is unchanged", _scrub("raw") == "raw")
    check("int is unchanged", _scrub(3) == 3)
    check("None is unchanged", _scrub(None) is None)
    check("empty structures stay empty",
          _scrub({}) == {} and _scrub([]) == [])


def main() -> None:
    for t in (test_replaces_data_strings_with_length,
              test_walks_nested_dicts_and_lists,
              test_non_string_data_and_primitives):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
