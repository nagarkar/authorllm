"""A Listen button's voice: /api/try-text takes an optional `voice`, one of
the offered voices, for that listen only; audiobook.toml is untouched."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import audiobook, generation as gen  # noqa: E402

FAILS = 0


def check(name, ok, detail=""):
    global FAILS
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f" — {detail}"))
    FAILS += 0 if ok else 1


def main() -> int:
    rendered = []

    def renderer(root, speech, voice, rate):
        rendered.append(voice)
        p = Path(root) / "x.mp3"
        p.write_bytes(b"MP3")
        return p

    gen.preview_settings = lambda m: ("Jamie (Premium)", 180)
    audiobook.INSTALLED_VOICES = lambda: {"Jamie (Premium)", "Alex"}
    audiobook.PREVIEW_RENDERER = renderer
    ms = {"path": "/nonexistent"}

    v = audiobook.listen_voices(ms)
    check("listen voices start with the preview voice",
          v["choices"][0] == "Jamie (Premium)" and "Alex" in v["choices"] and not v["missing"], str(v))
    audiobook.render_pasted_preview(ms, "Hello there.")
    audiobook.render_pasted_preview(ms, "Hello there.", "Alex")
    check("no voice reads in the preview voice; a chosen one in that voice",
          rendered == ["Jamie (Premium)", "Alex"], str(rendered))
    for bad in ("Zarvox",):
        try:
            audiobook.render_pasted_preview(ms, "Hello.", bad)
            check(f"{bad} is turned down", False, "no error")
        except ValueError:
            check(f"{bad} is turned down", True)
    audiobook.INSTALLED_VOICES = lambda: {"Jamie (Premium)"}
    try:
        audiobook.render_pasted_preview(ms, "Hello.", "Alex")
        check("a voice not installed is turned down", False, "no error")
    except ValueError as err:
        check("a voice not installed is turned down", "not installed" in str(err), str(err))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
