"""Nested-path chapter stems must not collide in the audiobook export.

`intro.md` + `part2/intro.md` are both legal via toc/rglob. Stem-only
keys made `chapters/intro.json` and the book list last-wins, so the
first chapter's speech vanished. Run: python3 tests/test_audio_nested_stems.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"
os.environ.pop("ELEVENLABS_API_KEY", None)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import audio  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "audiobook"
PASSED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if not ok:
        raise AssertionError(f"FAIL: {label}" + (f" — {detail}" if detail else ""))
    PASSED += 1
    print(f"  ok  {label}")


def _manuscript(root: Path) -> dict:
    return {
        "id": 1, "path": str(root), "name": "Nested", "title": "Nested",
        "author": "Ada", "narrator": "Ned", "publisher": "Pub",
        "copyright_year": 2026, "copyright_owner": "Ada",
        "language": "en", "settings_json": "{}",
    }


def _book(toc: str, files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="authorlm-audio-nested-"))
    (root / "_audio").mkdir()
    shutil.copy(FIXTURE / "_audio" / "audiobook.toml", root / "_audio" / "audiobook.toml")
    shutil.copy(FIXTURE / "_audio" / "cast.md", root / "_audio" / "cast.md")
    (root / "toc.toml").write_text(toc, encoding="utf-8")
    (root / "title.md").write_text("# Nested\n", encoding="utf-8")
    (root / "pronunciations.md").write_text("# Pronunciations\n", encoding="utf-8")
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_nested_basename_collision() -> None:
    root = _book(
        '''
[[chapter]]
file = "intro.md"

[[chapter]]
file = "part2/intro.md"
''',
        {
            "intro.md": "# Intro A\n\nUniquely A content here about alpha words.\n",
            "part2/intro.md": "# Intro B\n\nUniquely B content here about beta words.\n",
        },
    )
    try:
        ms = _manuscript(root)
        built = audio.build(ms)
        stems = [c["stem"] for c in built["book"]["chapters"]]
        check("book lists two distinct stems", stems == ["intro", "intro-2"], str(stems))
        check("chapters dict keeps both",
              set(built["chapters"]) >= {"intro", "intro-2"})
        check("first file keeps stem intro",
              built["chapters"]["intro"]["file"] == "intro.md")
        check("nested file gets intro-2",
              built["chapters"]["intro-2"]["file"] == "part2/intro.md")

        audio.export(None, ms, resolve_dictionary=False)
        on_disk = sorted(
            p.name for p in (root / "_audio" / "chapters").glob("*.json")
            if not p.name.startswith("_"))
        check("both chapter JSON files written",
              on_disk == ["intro-2.json", "intro.json"], str(on_disk))
        texts = " ".join(
            (root / "_audio" / "chapters" / name).read_text(encoding="utf-8")
            for name in on_disk)
        check("first chapter speech preserved", "Uniquely A" in texts)
        check("second chapter speech preserved", "Uniquely B" in texts)

        only = audio.export(None, ms, only=["part2/intro.md"],
                            resolve_dictionary=False)
        # Re-export only the nested file — must target intro-2, not wipe intro.
        check("only=path resolves to nested stem",
              any(p.endswith("intro-2.json") for p in only["written"] + only["unchanged"]))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_flat_stems_unchanged() -> None:
    root = _book(
        '''
[[chapter]]
file = "alpha.md"

[[chapter]]
file = "beta.md"
''',
        {
            "alpha.md": "# Alpha\n\nAlpha body prose for speech.\n",
            "beta.md": "# Beta\n\nBeta body prose for speech.\n",
        },
    )
    try:
        built = audio.build(_manuscript(root))
        stems = [c["stem"] for c in built["book"]["chapters"]]
        check("flat manuscripts keep bare stems", stems == ["alpha", "beta"],
              str(stems))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_allocate_stem_unit() -> None:
    used: set[str] = set()
    check("first intro is bare", audio._allocate_stem("intro.md", used) == "intro")
    check("second intro is suffixed",
          audio._allocate_stem("part2/intro.md", used) == "intro-2")
    check("third intro continues",
          audio._allocate_stem("part3/intro.md", used) == "intro-3")
    check("unrelated stem stays bare",
          audio._allocate_stem("outro.md", used) == "outro")


if __name__ == "__main__":
    test_allocate_stem_unit()
    test_nested_basename_collision()
    test_flat_stems_unchanged()
    print(f"\nAll {PASSED} checks passed.")
