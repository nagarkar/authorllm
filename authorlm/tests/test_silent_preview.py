"""`generation._silent` — the say-preview silence door (#304).

macOS `say` can write a header and no samples (~4.6 KB) when a voice
gives up on a dash. `render_preview` retries with dashes-as-commas when
`_silent` is true. The audio suite stubs `say` end-to-end; this file pins
the size threshold so a drift cannot skip the retry or false-positive on
real audio.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.generation import _silent


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_silent_threshold() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "preview.aiff"
        check("missing file is silent", _silent(path))
        path.write_bytes(b"\0" * 100)
        check("tiny header-only file (< 8 KiB) is silent", _silent(path))
        path.write_bytes(b"\0" * 8191)
        check("8191 bytes is still silent", _silent(path))
        path.write_bytes(b"\0" * 8192)
        check("8192 bytes is not silent", not _silent(path))
        path.write_bytes(b"\0" * 30_000)
        check("a one-word-sized file is not silent", not _silent(path))


def main() -> None:
    print("test_silent_threshold")
    test_silent_threshold()
    print("OK — 1 silent-preview case")


if __name__ == "__main__":
    main()
