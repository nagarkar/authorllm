"""`audiobook._with_preview_voice` rewrites audiobook.toml so [preview]
voice is the named Mac voice, every other line unchanged.

Listen / try-text pick a manuscript's preview voice through this stamp
(#483 / #486). A silent miss leaves Jamie (or no [preview] table) on a
manuscript that should speak as Alex. Hermetic — string in, string out.
Run: python3 tests/test_with_preview_voice.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("AUTHORLM_CONFIG",
                      "/nonexistent/authorlm-preview-voice-toml/config.toml")
os.environ.setdefault("AUTHORLM_ENV",
                      "/nonexistent/authorlm-preview-voice-toml/.env")
os.environ.setdefault("AUTHORLM_CLIENT", "none")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.audiobook import _with_preview_voice  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_appends_preview_table_when_missing() -> None:
    src = 'title = "book"\n'
    got = _with_preview_voice(src, "Alex")
    check("keeps the prior lines",
          got.startswith('title = "book"\n'), got)
    check("adds a [preview] table with the voice",
          '[preview]\nvoice = "Alex"\n' in got, got)


def test_replaces_existing_preview_voice() -> None:
    src = '[preview]\nvoice = "Jamie"\nrate = 1\n'
    got = _with_preview_voice(src, "Alex")
    check("voice line is rewritten in place",
          got == '[preview]\nvoice = "Alex"\nrate = 1\n', got)


def test_inserts_voice_into_preview_table_without_one() -> None:
    src = "[preview]\nrate = 1\n[other]\nx = 1\n"
    got = _with_preview_voice(src, "Alex")
    check("voice is inserted before the next table",
          got == '[preview]\nrate = 1\nvoice = "Alex"\n[other]\nx = 1\n',
          got)


def test_appends_voice_when_preview_is_last_table() -> None:
    src = "[preview]\nrate = 1\n"
    got = _with_preview_voice(src, "Alex")
    check("voice is appended at EOF inside [preview]",
          got == '[preview]\nrate = 1\nvoice = "Alex"\n', got)


def main() -> None:
    for fn in (test_appends_preview_table_when_missing,
               test_replaces_existing_preview_voice,
               test_inserts_voice_into_preview_table_without_one,
               test_appends_voice_when_preview_is_last_table):
        print(f"{fn.__name__}:")
        fn()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
