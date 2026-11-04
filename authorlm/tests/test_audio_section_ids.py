"""`audio.section_id` / `silence_id` / `applicable_rules` are the paid
audio cache keys.

A wrong hash re-bills ElevenLabs for text already rendered, or skips a
good take after a pronunciation or voice change. Voice floats are pinned
to three decimals so `0.6` and `0.600` share a cache entry; rules are
whole-word hits sorted by pronunciation key.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import audio


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


VOICE = {
    "voice_id": "voice-a",
    "model": "eleven_multilingual_v2",
    "stability": 0.6,
    "similarity": 0.8,
    "speed": 1.0,
}


def test_applicable_rules_whole_word_and_sorted() -> None:
    rules = [
        {"term": "Kant", "say": "kahnt"},
        {"term": "Hegel", "say": "hay-gul"},
        {"term": "Spinoza", "say": "spi-no-za"},
    ]
    hits = audio.applicable_rules("Kant met Hegel at dawn.", rules)
    check("whole-word hits only, sorted by pronunciation key",
          hits == [("Hegel", "hay-gul"), ("Kant", "kahnt")], repr(hits))
    miss = audio.applicable_rules("Kantian reading of Hegelian thought.", rules)
    check("prefix/suffix forms do not count as whole-word hits",
          miss == [], repr(miss))
    none = audio.applicable_rules("no terms here", rules)
    check("no hits yields an empty list", none == [], repr(none))
    check("empty rules list yields an empty list",
          audio.applicable_rules("Kant", []) == [])


def test_section_id_stable_under_voice_float_formatting() -> None:
    text = "Kant spoke."
    rules = audio.applicable_rules(text, [{"term": "Kant", "say": "kahnt"}])
    a = audio.section_id(text, VOICE, rules)
    b = audio.section_id(text, {**VOICE, "stability": 0.600,
                                "similarity": 0.800, "speed": 1.000}, rules)
    check("0.6 and 0.600 produce the same section id", a == b, f"{a} vs {b}")
    check("section id is a 32-char hex digest",
          len(a) == 32 and all(c in "0123456789abcdef" for c in a), a)


def test_section_id_changes_with_inputs() -> None:
    rules = [("Kant", "kahnt")]
    base = audio.section_id("Kant spoke.", VOICE, rules)
    check("different text → different id",
          audio.section_id("Kant wrote.", VOICE, rules) != base)
    check("different voice_id → different id",
          audio.section_id("Kant spoke.", {**VOICE, "voice_id": "other"},
                           rules) != base)
    check("different stability → different id",
          audio.section_id("Kant spoke.", {**VOICE, "stability": 0.61},
                           rules) != base)
    check("different pronunciation rules → different id",
          audio.section_id("Kant spoke.", VOICE, [("Kant", "kant")]) != base)
    check("same inputs → same id",
          audio.section_id("Kant spoke.", VOICE, rules) == base)


def test_silence_id_duration_and_ordinal() -> None:
    a = audio.silence_id(500, 0)
    check("silence id is a 32-char hex digest",
          len(a) == 32 and all(c in "0123456789abcdef" for c in a), a)
    check("duration change → different id",
          audio.silence_id(501, 0) != a)
    check("ordinal change → different id",
          audio.silence_id(500, 1) != a)
    check("same duration+ordinal → same id",
          audio.silence_id(500, 0) == a)


def main() -> None:
    tests = [test_applicable_rules_whole_word_and_sorted,
             test_section_id_stable_under_voice_float_formatting,
             test_section_id_changes_with_inputs,
             test_silence_id_duration_and_ordinal]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
