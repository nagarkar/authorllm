"""Clip key minting rests on three pure helpers in `media.py`.

`_youtube_id` pulls the id out of watch / youtu.be / shorts / embed URLs
so a confirmed clip gets a stable `yt-<id>` base. `_slug` turns a
description into the provisional key. `_is_path` separates a frame or
local file from a cast key or audio mode. Wrong parses collide keys or
treat a voice role as a path.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.media import _is_path, _slug, _youtube_id


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_youtube_id_shapes() -> None:
    vid = "dQw4w9WgXcQ"
    check("youtu.be short link",
          _youtube_id(f"https://youtu.be/{vid}") == vid)
    check("watch?v=",
          _youtube_id(f"https://www.youtube.com/watch?v={vid}") == vid)
    check("watch?v= with extra query params",
          _youtube_id(f"https://www.youtube.com/watch?v={vid}&t=30") == vid)
    check("/shorts/",
          _youtube_id("https://youtube.com/shorts/abc123XYZ_") == "abc123XYZ_")
    check("/embed/",
          _youtube_id(f"https://www.youtube.com/embed/{vid}") == vid)
    check("any host with ?v= still yields the id (key minting)",
          _youtube_id(f"https://example.com/watch?v={vid}") == vid)
    check("non-YouTube URL is None",
          _youtube_id("https://vimeo.com/123") is None)
    check("plain text is None",
          _youtube_id("not-a-url") is None)


def test_slug() -> None:
    check("lowercase, punctuation stripped, first four words",
          _slug("He Slumps at his Desk!") == "he-slumps-at-his")
    check("words=N truncates",
          _slug("one two three four five", 2) == "one-two")
    check("punctuation-only description is empty (caller uses untitled)",
          _slug("!!!") == "")


def test_is_path() -> None:
    check("slash marks a path", _is_path("assets/frame.jpg") is True)
    check("a known video extension marks a path", _is_path("clip.mp4") is True)
    check("a cast key is not a path", _is_path("ecce-man") is False)
    check("an audio mode is not a path", _is_path("mute") is False)
    check("a one-letter suffix is not an extension",
          _is_path("a.b") is False)


def main() -> None:
    for t in (test_youtube_id_shapes, test_slug, test_is_path):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
