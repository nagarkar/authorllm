"""`audio._validate_chapter_config` and `_validate_voice_key` are the
gates on every `[[chapter]]` override in audiobook.toml.

A silent drift here accepts an absolute path, a path with `..`, a
duplicate file, a non-.md name, or a voice key that is not a cast key —
and the next build narrates the wrong chapter or crashes mid-export.
Pure over dicts; no workspace.
Run: python3 tests/test_validate_chapter_config.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.audio import (  # noqa: E402
    AudioError, _validate_chapter_config, _validate_config, _validate_voice_key,
)


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _raises(label: str, call, needle: str) -> None:
    try:
        call()
    except AudioError as err:
        check(label, needle in str(err), err)
        return
    check(label, False, "no AudioError")


def test_voice_key_shape() -> None:
    _validate_voice_key("narrator", "where")
    _validate_voice_key("chitta_darshana", "where")
    _validate_voice_key("a1-b", "where")
    for bad in ("", "Narrator", "1voice", "has space", None, 3):
        _raises(f"voice key {bad!r} refused",
                lambda b=bad: _validate_voice_key(b, "cast.voice"),
                "nonempty cast key")


def test_chapter_must_be_a_list_of_tables() -> None:
    _raises("not a list",
            lambda: _validate_chapter_config({"file": "a.md"}, "cfg"),
            "repeatable [[chapter]]")
    _raises("member not a table",
            lambda: _validate_chapter_config(["one.md"], "cfg"),
            "must be a table")


def test_chapter_file_and_voice_rules() -> None:
    good = [{"file": "one.md", "voice": "narrator"},
            {"file": "two.md", "voice": "guest"}]
    _validate_chapter_config(good, "cfg")
    check("two distinct chapters pass", True)

    _raises("unknown key",
            lambda: _validate_chapter_config(
                [{"file": "one.md", "voice": "narrator", "rate": 1}], "cfg"),
            "unknown key rate")
    _raises("absolute path",
            lambda: _validate_chapter_config(
                [{"file": "/tmp/one.md", "voice": "narrator"}], "cfg"),
            "manuscript .md file")
    _raises("parent traversal",
            lambda: _validate_chapter_config(
                [{"file": "../one.md", "voice": "narrator"}], "cfg"),
            "manuscript .md file")
    _raises("not markdown",
            lambda: _validate_chapter_config(
                [{"file": "one.txt", "voice": "narrator"}], "cfg"),
            "manuscript .md file")
    _raises("whitespace padding",
            lambda: _validate_chapter_config(
                [{"file": " one.md", "voice": "narrator"}], "cfg"),
            "manuscript .md file")
    _raises("duplicate file",
            lambda: _validate_chapter_config(
                [{"file": "one.md", "voice": "narrator"},
                 {"file": "one.md", "voice": "guest"}], "cfg"),
            "duplicate file")
    _raises("bad voice on chapter",
            lambda: _validate_chapter_config(
                [{"file": "one.md", "voice": "Narrator"}], "cfg"),
            "nonempty cast key")


def test_validate_config_requires_voices_and_enums() -> None:
    base = {
        "schema": 1,
        "text": {"voice": "narrator", "footnotes": "drop"},
        "headings": {"voice": "headings"},
        "credits": {"voice": "credits"},
    }
    _validate_config(base, "audiobook.toml")
    check("minimal valid config passes", True)

    _raises("missing text.voice",
            lambda: _validate_config(
                {**base, "text": {"footnotes": "drop"}}, "cfg"),
            "text.voice is required")
    _raises("bad enum",
            lambda: _validate_config(
                {**base, "text": {"voice": "narrator",
                                  "footnotes": "skip"}}, "cfg"),
            "footnotes")
    _raises("unknown table",
            lambda: _validate_config({**base, "extra": {}}, "cfg"),
            "unknown table")
    _raises("wrong schema",
            lambda: _validate_config({**base, "schema": 99}, "cfg"),
            "schema = 99")


def main() -> None:
    for test in (
        test_voice_key_shape,
        test_chapter_must_be_a_list_of_tables,
        test_chapter_file_and_voice_rules,
        test_validate_config_requires_voices_and_enums,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
