"""`media.parse_fields` is the grammar door for every [Clip]/[Collage]
field list: description, optional ⇢ note, then `| key: value` pairs.

It promises that unknown or repeated fields become errors and are never
silently dropped, that cast splits on commas, that a quoted `text:`
value peels its quotes, and that field names are case-insensitive.
Happy-path voice/use parsing lives in `test_export_upload`; this file
pins the firewall edges the scan suite only hits indirectly.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.media import parse_fields


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_description_and_ref() -> None:
    got = parse_fields("hello world ⇢ note.md | audio: mute")
    check("description peels the ⇢ note ref",
          got["description"] == "hello world", repr(got))
    check("ref is the note filename", got["ref"] == "note.md", repr(got))
    check("known field is kept", got["fields"] == {"audio": "mute"},
          repr(got))
    check("no errors on a clean body", got["errors"] == [], repr(got))


def test_unknown_field_is_an_error() -> None:
    got = parse_fields("desc | colour: red")
    check("unknown field is not stored", got["fields"] == {}, repr(got))
    check("unknown field records an error",
          any("unknown field 'colour:'" in e for e in got["errors"]),
          got["errors"])


def test_repeated_field_is_an_error() -> None:
    got = parse_fields("desc ⇢ note.md | use: a.mp4 | USE: b.mp4")
    check("first value wins", got["fields"].get("use") == "a.mp4",
          repr(got))
    check("second value is refused as a duplicate",
          any("field 'use:' given twice" in e for e in got["errors"]),
          got["errors"])


def test_case_insensitive_keys() -> None:
    got = parse_fields("x | Voice: author | AUDIO: duck")
    check("Voice: normalizes to voice",
          got["fields"].get("voice") == "author", repr(got))
    check("AUDIO: normalizes to audio",
          got["fields"].get("audio") == "duck", repr(got))
    check("no errors for case variants of known keys",
          got["errors"] == [], repr(got))


def test_cast_splits_on_commas() -> None:
    got = parse_fields("x | cast: Alice, Bob, , Carol")
    check("cast becomes a stripped name list",
          got["fields"].get("cast") == ["Alice", "Bob", "Carol"],
          repr(got))


def test_quoted_text_peels_matching_quotes() -> None:
    got = parse_fields('x | text: "Mount Kailash"')
    check("matching double quotes are peeled",
          got["fields"].get("text") == "Mount Kailash", repr(got))
    bare = parse_fields("x | text: bare words")
    check("unquoted text is kept as written",
          bare["fields"].get("text") == "bare words", repr(bare))


def main() -> None:
    tests = [test_description_and_ref, test_unknown_field_is_an_error,
             test_repeated_field_is_an_error, test_case_insensitive_keys,
             test_cast_splits_on_commas, test_quoted_text_peels_matching_quotes]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
