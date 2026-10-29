"""`generation.pronunciation_diff` / `param_diff` flag a stale take for re-render.

Wrong empty detail hides a voice or pronunciation change and leaves paid
takes out of date; wrong noise forces a re-render the author did not ask
for. `flags_from_state` surfaces these strings on the audiobook page.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.generation import param_diff, pronunciation_diff


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_pronunciation_add_change_remove() -> None:
    old = [{"term": "Kant", "say": "kahnt"},
           {"term": "Hegel", "say": "hay-gul"}]
    new = [{"term": "Kant", "say": "kant"},
           {"term": "Nietzsche", "say": "nee-chuh"}]
    got = pronunciation_diff(old, new)
    check("changed say is reported as was → now",
          "Kant: kahnt → kant" in got, got)
    check("added term is reported",
          "added Nietzsche → nee-chuh" in got, got)
    check("removed term is reported",
          "removed Hegel" in got, got)
    check("identical dictionaries yield nothing",
          pronunciation_diff(old, old) == [],
          pronunciation_diff(old, old))


def test_param_diff_voice_model_numeric() -> None:
    base = {"voiceId": "v1", "cast": "narrator", "model": "eleven_v2",
            "stability": 0.5, "similarity": 0.7, "speed": 1.0,
            "pronunciations": []}
    check("voiceId change flags voice",
          param_diff(base, {**base, "voiceId": "v2"}) == ["voice"])
    check("cast change flags voice",
          param_diff(base, {**base, "cast": "other"}) == ["voice"])
    check("model change flags model",
          param_diff(base, {**base, "model": "eleven_v3"}) == ["model"])
    check("stability drift beyond epsilon flags stability",
          param_diff(base, {**base, "stability": 0.51}) == ["stability"])
    check("missing numeric defaults to 1.0 for comparison",
          "speed" in param_diff({"voiceId": "v1"}, {"voiceId": "v1",
                                                    "speed": 0.9}))


def test_param_diff_pronunciations_and_fallback() -> None:
    old = {"voiceId": "v1", "model": "m",
           "pronunciations": [{"term": "a", "say": "ay"}]}
    new = {"voiceId": "v1", "model": "m",
           "pronunciations": [{"term": "a", "say": "ah"}]}
    check("pronunciation change rides on param_diff",
          param_diff(old, new) == ["a: ay → ah"], param_diff(old, new))
    identical = {"voiceId": "v1", "model": "m", "stability": 0.5,
                 "similarity": 0.5, "speed": 1.0, "pronunciations": []}
    check("no field moved still reports a generic parameters change",
          param_diff(identical, dict(identical)) == ["parameters"])


if __name__ == "__main__":
    test_pronunciation_add_change_remove()
    test_param_diff_voice_model_numeric()
    test_param_diff_pronunciations_and_fallback()
    print("ok")
