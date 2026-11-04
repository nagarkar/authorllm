"""`media_gen.desc_hash` names what shapes a Veo take on disk.

Only the spec text and the visual fields (`start`, `end`, `cast`,
`prior`, `look`, `text`) enter the hash. A confirmed YouTube URL, a
local `use:` path, or an `audio:` mode must not move the hash — otherwise
every field confirmation looks like a new picture and every take goes
stale. Whitespace in the spec folds so a re-wrap does not either.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import media_gen


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_whitespace_folds() -> None:
    a = media_gen.desc_hash("  hello   world  ", {"start": "1:00"})
    b = media_gen.desc_hash("hello world", {"start": "1:00"})
    check("collapsed whitespace keeps the same hash", a == b, (a, b))


def test_field_order_and_non_shaping() -> None:
    shaping = {"start": "1:00", "end": "2:00", "cast": "ecce-man"}
    a = media_gen.desc_hash("desk", {**shaping, "youtube": "https://youtu.be/x",
                                    "audio": "mute", "use": "clip.mp4"})
    b = media_gen.desc_hash("desk", {"end": "2:00", "cast": "ecce-man",
                                    "start": "1:00"})
    check("non-shaping fields do not move the hash", a == b, (a, b))
    check("json key order does not move the hash", a == b)


def test_shaping_field_moves_hash() -> None:
    base = media_gen.desc_hash("desk", {"start": "1:00", "end": "2:00"})
    moved = media_gen.desc_hash("desk", {"start": "1:00", "end": "2:00",
                                         "prior": "establishing"})
    check("adding a shaping field moves the hash", base != moved, (base, moved))
    check("empty shaping still hashes the spec (8 hex chars)",
          len(media_gen.desc_hash("desk", {})) == 8)
    # Empty spec + no fields still feeds json.dumps({}) into _hash, so
    # the digest is of "{}", never the zero-pad reserved for a blank
    # string alone.
    empty = media_gen.desc_hash("", {})
    check("empty input is not the blank-string zero-pad",
          empty != media_gen._hash("", 8) and empty == media_gen._hash("{}", 8),
          empty)


def main() -> None:
    for t in (test_whitespace_folds, test_field_order_and_non_shaping,
              test_shaping_field_moves_hash):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
