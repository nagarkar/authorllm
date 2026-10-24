"""`media.is_tag_line` decides which lines `strip_for_book` drops from
PDF/EPUB exports.

A false positive deletes prose that looks like a tag; a false negative
leaves Clip/Collage/Montage markup in the printed book. The matcher is
whole-line only (leading whitespace or trailing junk → False).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.media import is_tag_line


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_clip_and_container_lines() -> None:
    for line in (
        "[Clip: a beach | duration: 4]",
        "[clip: lower case]",
        "[/Clip]",
        "[/clip]",
        "[Collage: cutaway]",
        "[Montage: sequence | duration: 8]",
        "[/Collage]",
        "[/montage]",
    ):
        check(f"media tag is recognized: {line!r}", is_tag_line(line) is True)


def test_false_friends_are_not_media_tags() -> None:
    for line in (
        "[Illustration: a diagram]",
        "[Omit: aside]",
        "[Voice: guest]",
        "[Suggestion: simplify]",
        "[Footnote: note]",
        "[Explain: why]",
        "![alt](_clips/x.mp4)",
        "plain prose about [Clip: mention] inline",
        "  [Clip: indented]",
        "[Clip: trailing] junk",
        "",
        "[]",
    ):
        check(f"not a media tag line: {line!r}", is_tag_line(line) is False)


def main() -> None:
    tests = [test_clip_and_container_lines, test_false_friends_are_not_media_tags]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
