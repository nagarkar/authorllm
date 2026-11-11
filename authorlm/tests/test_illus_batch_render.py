"""`illus render` with no fragment renders every unrendered slot, one
after another, exactly as single-slot renders would.

Two defects this pins (2026-11-10):
- it-d088feca40aa (#397): the batch copied each slot without its cast
  and prior, so scenes went to the image model text-only.
- it-6bd797b7bd4a (#396): every line number was taken before the loop;
  each render externalizes a tag and inserts an embed line, so from the
  second slot on the writes landed on prose.

Hermetic: scratch workspace, the image model replaced by a fake that
records the references it receives. No network, no spend.
Run: python3 tests/test_illus_batch_render.py
"""

from __future__ import annotations

import contextlib
import io
import shutil
import struct
import sys
import tempfile
import zlib
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import test_passes  # noqa: E402,F401  (pins config and .env offline)

from authorlm import illus  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" ({detail})" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def tiny_png() -> bytes:
    def chunk(t, d):
        return (struct.pack(">I", len(d)) + t + d
                + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
            + chunk(b"IEND", b""))


FILL = ("under a low grey sky with wind moving through the dry grass and "
        "a narrow path running down toward the river where the old stones "
        "lie half buried in mud and moss, the light thin and cold, the "
        "far hills fading into mist, every line drawn with patient care "
        "and nothing hurried in the whole of the scene")


def scene(subject: str) -> str:
    desc = f"{subject} {FILL}"
    assert len(desc.split()) > 50, len(desc.split())
    return f"[Illustration: {desc} | cast: pilgrim]"


PROSE = [
    "# **The Road**",
    "The first paragraph sets out, and it must survive every render "
    "exactly as written.",
    "A second paragraph sits between the first and second scenes.",
    "A third paragraph follows the second scene and precedes the third.",
    "The closing paragraph ends the chapter after the last scene.",
]
SCENES = [scene("the pilgrim sets out at dawn from a ruined chapel"),
          scene("the pilgrim crosses a flooded ford at noon"),
          scene("the pilgrim rests beneath a lone oak at dusk")]


def chapter_text() -> str:
    parts = [PROSE[0], PROSE[1], SCENES[0], PROSE[2], SCENES[1], PROSE[3],
             SCENES[2], PROSE[4]]
    return "\n\n".join(parts) + "\n"


class Calls:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, config, prompt, input_png=None, references=None,
                 model=None, size=None):
        self.calls.append({"prompt": prompt,
                           "references": list(references or [])})
        return tiny_png()


def run(ws: Path, *argv: str) -> tuple[int, str]:
    out = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            cli_main(["--workspace", str(ws), *argv])
        except SystemExit as exc:
            if isinstance(exc.code, str):
                out.write(exc.code + "\n")
                code = 1
            else:
                code = exc.code or 0
    return code, out.getvalue()


def setup(tmp: Path) -> tuple[Path, Path]:
    ws = tmp / "ws"
    ms = ws / "book"
    ms.mkdir(parents=True)
    (ms / "01-road.md").write_text(chapter_text(), encoding="utf-8")
    (ms / "illustration_cast.md").write_text(
        "[Omit: pdf, docx, epub, md, audio]\n\n# Cast\n\n"
        "[Illustration: a lone pilgrim with a wide hat | cast-id: pilgrim]"
        "\n\n[/Omit]\n", encoding="utf-8")
    code, out = run(ws, "init", "--name", "book", "--path", str(ms),
                    "--extraction", "off")
    assert code == 0, out
    return ws, ms


def test_batch_render_sends_references_and_keeps_prose() -> None:
    tmp = Path(tempfile.mkdtemp())
    try:
        ws, ms = setup(tmp)
        fake = Calls()
        with mock.patch("authorlm.llm.generate_image", fake):
            # The plate first, by fragment: it becomes the picked plate.
            code, out = run(ws, "illus", "render", "lone pilgrim")
            check("the cast plate renders by fragment", code == 0, out)
            index, problems = illus.cast_index(ms)
            plate = index.get("pilgrim", {}).get("plate")
            check("the pilgrim plate is picked", bool(plate) and not problems,
                  (index, problems))
            plate_bytes = (ms / illus.ILLUS_DIR / plate).read_bytes()
            before = len(fake.calls)

            code, out = run(ws, "illus", "render")
        check("the batch render exits cleanly", code == 0, out)
        check("the batch reports no failures", "failed" not in out, out)
        scene_calls = fake.calls[before:]
        check("each of the three scenes went to the model once",
              len(scene_calls) == 3, len(scene_calls))
        check("every scene received the pilgrim plate as its reference",
              all(c["references"] == [plate_bytes] for c in scene_calls),
              [len(c["references"]) for c in scene_calls])
        check("every composed prompt names the reference",
              all("Image 1: the Pilgrim of this book" in c["prompt"]
                  for c in scene_calls))

        text = (ms / "01-road.md").read_text(encoding="utf-8")
        lines = text.split("\n")
        tags = [(i, illus.parse_tag(line)) for i, line in enumerate(lines)
                if illus.parse_tag(line)]
        prose = [line for i, line in enumerate(lines)
                 if line.strip() and not illus.parse_tag(line)
                 and not line.startswith("![](")]
        check("every prose paragraph is byte-identical, in order",
              prose == PROSE, prose)
        check("each tag appears once (three tags, no leftover inline copy)",
              len(tags) == 3 and len({t["ref"] for _, t in tags}) == 3,
              [t for _, t in tags])
        check("every tag is externalized and keeps its cast",
              all(t["ref"] and t["cast"] == ["pilgrim"] for _, t in tags),
              [t for _, t in tags])
        check("every tag has its embed line directly under it",
              all(lines[i + 1].startswith(f"![]({illus.ILLUS_DIR}/")
                  for i, _ in tags))
        for (_, t), original in zip(tags, SCENES):
            full = illus.load_prompts(ms)[t["ref"]]
            want = illus.parse_tag(original)["prompt"]
            check(f"{t['ref']} holds its own full description",
                  " ".join(full.split()) == want, full)
        metas = [illus.read_metadata(ms / illus.ILLUS_DIR / lines[i + 1]
                 .rsplit("/", 1)[-1].rstrip(")")) for i, _ in tags]
        check("every scene image records the cast in its metadata",
              all(m.get("authorlm:cast", "").startswith("pilgrim=")
                  for m in metas), metas)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_guards() -> None:
    tmp = Path(tempfile.mkdtemp())
    try:
        ws, ms = setup(tmp)
        from authorlm.db import Database  # noqa: F401

        fake = Calls()
        with mock.patch("authorlm.llm.generate_image", fake):
            run(ws, "illus", "render", "lone pilgrim")
        slot = illus.find_slot(ms, "flooded ford")[0]
        stale = {**slot, "line": slot["line"] - 2}
        before = (ms / "01-road.md").read_bytes()
        try:
            illus.externalize(ms, stale)
            stopped = False
        except LookupError as err:
            stopped = "flooded" in str(err) or "pilgrim" in str(err)
        check("externalize at a stale line stops, naming the slot", stopped)
        check("and leaves the file untouched",
              (ms / "01-road.md").read_bytes() == before)

        # A dict trimmed of its cast (the #397 shape) and with a stale
        # line still renders with its reference: render_slot re-finds it.
        trimmed = {k: v for k, v in slot.items()
                   if k not in ("cast", "prior")}
        trimmed["line"] = 1
        sent = []

        def gen(prompt, input_png, references=None):
            sent.append(references)
            return tiny_png()

        manuscript = {"id": "x", "path": str(ms)}
        with mock.patch("authorlm.illus.illustration_law",
                        lambda *a, **k: ""):
            illus.render_slot(None, manuscript, trimmed, {}, generator=gen)
        check("a trimmed, stale slot dict still renders with its reference",
              len(sent) == 1 and sent[0] and len(sent[0]) == 1, sent)
        prose_now = [line for line in
                     (ms / "01-road.md").read_text().split("\n")
                     if line.strip() and not illus.parse_tag(line)
                     and not line.startswith("![](")]
        check("and the prose is unchanged", prose_now == PROSE, prose_now)

        # A tag whose cast id resolves to nothing stops, naming the slot.
        (ms / "02-ghost.md").write_text(
            "Prose before.\n\n[Illustration: a ghost on the stair "
            "| cast: ghost]\n\nProse after.\n", encoding="utf-8")
        ghost = illus.find_slot(ms, "ghost on the stair")[0]
        ghost_before = (ms / "02-ghost.md").read_bytes()
        try:
            with mock.patch("authorlm.illus.illustration_law",
                            lambda *a, **k: ""):
                illus.render_slot(None, manuscript, ghost, {}, generator=gen)
            stopped, msg = False, ""
        except LookupError as err:
            stopped, msg = True, str(err)
        check("an unresolved cast stops the render with LookupError",
              stopped and "ghost" in msg, msg)
        check("and nothing was sent or written",
              len(sent) == 1
              and (ms / "02-ghost.md").read_bytes() == ghost_before)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    tests = [test_batch_render_sends_references_and_keeps_prose, test_guards]
    failed = 0
    for test in tests:
        print(test.__name__)
        try:
            test()
        except AssertionError:
            failed += 1
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
