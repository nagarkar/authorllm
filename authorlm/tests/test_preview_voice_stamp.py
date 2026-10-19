"""Lazy-preview stamp and orphan sweep (#227).

`check_preview_voice` records what `[preview]` voice/rate the mp3s were
made with. A missing stamp adopts the current settings without wiping
existing files (migration); a changed stamp clears finished mp3s but
leaves in-flight `.part` renders alone. `sweep_previews` drops mp3s
whose section id no chapter still names — without touching live ones or
partials.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import generation as gen


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _preview_dir(root: Path) -> Path:
    folder = root / gen.PREVIEW_DIR
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def test_check_preview_voice_adopt_and_clear() -> None:
    with tempfile.TemporaryDirectory(prefix="preview-voice-") as tmp:
        root = Path(tmp)
        folder = _preview_dir(root)
        live = folder / "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.mp3"
        part = folder / "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.part.mp3"
        live.write_bytes(b"LIVE")
        part.write_bytes(b"PART")

        cleared = gen.check_preview_voice(root, "Narrator", 175)
        stamp = folder / gen.PREVIEW_STAMP
        check("first call adopts settings and clears nothing",
              cleared == 0 and live.exists() and part.exists()
              and stamp.read_text(encoding="utf-8") == "Narrator\n175\n",
              cleared)
        check("matching stamp is a no-op",
              gen.check_preview_voice(root, "Narrator", 175) == 0
              and live.exists())

        cleared = gen.check_preview_voice(root, "Other", 175)
        check("a new voice clears finished previews",
              cleared == 1 and not live.exists(), cleared)
        check("in-flight .part files survive a voice change",
              part.exists())
        check("stamp records the new voice and rate",
              stamp.read_text(encoding="utf-8") == "Other\n175\n")

        live.write_bytes(b"LIVE2")
        cleared = gen.check_preview_voice(root, "Other", 200)
        check("a rate-only change also clears finished previews",
              cleared == 1 and not live.exists()
              and stamp.read_text(encoding="utf-8") == "Other\n200\n",
              cleared)


def test_sweep_previews_keeps_live_drops_orphans() -> None:
    with tempfile.TemporaryDirectory(prefix="preview-sweep-") as tmp:
        root = Path(tmp)
        chapters = root / "chapters"
        chapters.mkdir()
        live_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        orphan_id = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        (chapters / "one.json").write_text(json.dumps({
            "sections": [
                {"type": "heading", "id": "heading-id", "text": "One"},
                {"type": "speech", "id": live_id, "text": "Hello.",
                 "voiceId": "v"},
            ],
        }), encoding="utf-8")
        book = {
            "schema": gen.SCHEMA,
            "chapters": [{"stem": "one", "file": "chapters/one.json"}],
        }
        folder = _preview_dir(root)
        (folder / f"{live_id}.mp3").write_bytes(b"LIVE")
        (folder / f"{orphan_id}.mp3").write_bytes(b"ORPHAN")
        (folder / f"{orphan_id}.part.mp3").write_bytes(b"PART")

        gone = gen.sweep_previews(root, book)
        check("orphan finished preview is swept",
              gone == 1 and not (folder / f"{orphan_id}.mp3").exists(), gone)
        check("live preview stays",
              (folder / f"{live_id}.mp3").exists())
        check("orphan .part is left alone (in flight)",
              (folder / f"{orphan_id}.part.mp3").exists())
        check("missing preview folder sweeps zero",
              gen.sweep_previews(root / "missing", book) == 0)


def main() -> None:
    for t in (test_check_preview_voice_adopt_and_clear,
              test_sweep_previews_keeps_live_drops_orphans):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
