"""The illustration renderer runs on llmlm's shared cast module
(`llmlm.cast`, nagarkar/llmlm#20) and nothing a manuscript holds changed
with the move.

`tests/fixtures/illus_shared_cast.json` was recorded from the code as it
stood BEFORE the move (authorlm's own cast functions in `illus.py`). This
file replays the same slot through today's code and compares byte for
byte: the composed prompt the image model receives, the candidate file
names with their hashes, the fields stored inside the image, and the pin
file. It also reads an image whose `authorlm:` fields were laid down by
hand the way the old code laid them down.

Hermetic: scratch manuscript, the image model replaced by a fake. No
network, no spend.
Run: python3 tests/test_illus_shared_cast.py
"""

from __future__ import annotations

import json
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

FIXTURE = HERE / "fixtures" / "illus_shared_cast.json"
LAW = ("ILLUSTRATION STYLE (ratified by the author — follow strictly):\n"
       "- Woodcut, high-contrast linework only. (no colour wash)\n"
       "- Figures small against wide ground.")
CAST_FILE = (
    "[Omit: pdf, docx, epub, md, audio]\n\n# Cast\n\n"
    "[Illustration: a lone pilgrim with a wide hat | cast-id: pilgrim]\n\n"
    "[Illustration: a tower of pale stone lit from within "
    "| cast-id: tower-of-light]\n\n"
    "[Illustration: a ruined chapel on a bare hill at dawn "
    "| cast-id: chapel-stage]\n\n"
    "[Illustration: a ferryman nobody has drawn yet | cast-id: ferryman]\n\n"
    "[/Omit]\n")
CHAPTER = (
    "# **The Road**\n\n"
    "The first paragraph sets out.\n\n"
    "[Illustration: the pilgrim   climbs toward the tower at dusk, "
    "the hat pulled low | caption: The climb "
    "| cast: pilgrim, tower-of-light | prior: chapel-stage]\n\n"
    "A paragraph between.\n\n"
    "[Illustration: the ferryman waits at the ford | cast: ferryman, ghost]\n\n"
    "[Illustration: an empty road under rain]\n\n"
    "The closing paragraph.\n")


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" ({detail})" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def tiny_png() -> bytes:
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
            + _chunk(b"IEND", b""))


def observe() -> dict:
    """Everything the move must leave alone, from one scratch manuscript."""
    tmp = Path(tempfile.mkdtemp())
    seen: dict = {}
    sent: list = []

    def gen(prompt, input_png, references=None):
        sent.append({"prompt": prompt, "input": input_png is not None,
                     "references": len(references or [])})
        return tiny_png()

    try:
        ms = tmp / "book"
        ms.mkdir()
        (ms / "illustration_cast.md").write_text(CAST_FILE, encoding="utf-8")
        (ms / "01-road.md").write_text(CHAPTER, encoding="utf-8")
        manuscript = {"id": "x", "path": str(ms)}
        config = {"illustrations": {"model": "gemini/test-image",
                                    "image_size": "1024x1536"}}
        with mock.patch("authorlm.illus.illustration_law",
                        lambda *a, **k: LAW):
            plates = {}
            for fragment in ("lone pilgrim", "tower of pale", "ruined chapel"):
                slot = illus.find_slot(ms, fragment)[0]
                out = illus.render_slot(None, manuscript, slot, config,
                                        generator=gen)
                plates[fragment] = out["written"]
            seen["plates"] = plates
            seen["pin_text"] = illus.pin_path(ms).read_text(encoding="utf-8")
            scene = illus.find_slot(ms, "climbs toward the tower")[0]
            assembled = illus.effective_prompt(None, manuscript, scene)
            seen["composed"] = assembled["composed"]
            seen["desc_hash"] = assembled["desc_hash"]
            seen["style_hash"] = assembled["style_hash"]
            seen["references"] = [
                {k: str(v) if k != "path" else Path(v).name
                 for k, v in r.items()} for r in assembled["references"]]
            del sent[:]
            out = illus.render_slot(None, manuscript, scene, config,
                                    generator=gen, count=2)
            # Externalizing rewrote the tag: re-find the slot, as the CLI does.
            scene = illus.find_slot(ms, "climbs toward the tower")[0]
            again = illus.render_slot(None, manuscript, scene, config,
                                      generator=gen, from_n=1,
                                      model="gemini/other")
            seen["sent"] = list(sent)
            seen["written"] = out["written"] + again["written"]
            seen["result"] = {k: v for k, v in out.items()}
            seen["off_model_result"] = {k: v for k, v in again.items()}
            metas = [illus.read_metadata(ms / illus.ILLUS_DIR / name)
                     for name in seen["written"]]
            for meta in metas:
                meta.pop("authorlm:date", None)
            seen["metadata"] = metas
            raw = (ms / illus.ILLUS_DIR / out["written"][0]).read_bytes()
            seen["field_order"] = [
                key for key in ("prompt", "caption", "style", "model", "size",
                                "cast", "prior", "date")
                if f"authorlm:{key}".encode() in raw]
            seen["field_offsets_ascend"] = (
                [raw.index(f"authorlm:{key}".encode())
                 for key in seen["field_order"]]
                == sorted(raw.index(f"authorlm:{key}".encode())
                          for key in seen["field_order"]))
            ferry = illus.find_slot(ms, "ferryman waits")[0]
            seen["errors"] = illus.resolve_references(ms, ferry)["errors"]
            try:
                illus.render_slot(None, manuscript, ferry, config,
                                  generator=gen)
                seen["declined"] = ""
            except LookupError as err:
                seen["declined"] = str(err)
            seen["ferry_file_untouched"] = (
                "[Illustration: the ferryman waits at the ford | cast: "
                "ferryman, ghost]" in (ms / "01-road.md").read_text())
            bare = illus.find_slot(ms, "empty road")[0]
            with mock.patch("authorlm.illus.illustration_law",
                            lambda *a, **k: ""):
                seen["bare_composed"] = illus.effective_prompt(
                    None, manuscript, bare)["composed"]
                seen["bare_written"] = illus.render_slot(
                    None, manuscript, bare, config, generator=gen)["written"]
            seen["status"] = [
                {k: s[k] for k in ("prompt", "state", "candidates",
                                   "embedded", "off_model", "cast", "prior")}
                for s in illus.slot_status(None, manuscript)]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return seen


def test_nothing_a_manuscript_holds_changed_with_the_move() -> None:
    want = json.loads(FIXTURE.read_text(encoding="utf-8"))
    got = json.loads(json.dumps(observe()))
    check("the composed prompt of the fixture slot is byte for byte the "
          "prompt the old code composed",
          got["composed"].encode("utf-8") == want["composed"].encode("utf-8"),
          got["composed"])
    check("the image model is sent that same prompt, with the plates",
          got["sent"] == want["sent"]
          and got["sent"][0]["prompt"] == want["composed"], got["sent"])
    check("candidate names and their hashes are identical",
          got["written"] == want["written"]
          and got["plates"] == want["plates"]
          and got["bare_written"] == want["bare_written"]
          and (got["desc_hash"], got["style_hash"])
          == (want["desc_hash"], want["style_hash"]), got["written"])
    check("the fields stored in each image are identical, in the same order",
          got["metadata"] == want["metadata"]
          and got["field_order"] == want["field_order"]
          and got["field_offsets_ascend"], got["metadata"])
    check("the pin file is byte for byte the same",
          got["pin_text"] == want["pin_text"], got["pin_text"])
    check("the reasons a cast could not be attached read the same",
          got["errors"] == want["errors"]
          and got["declined"] == want["declined"]
          and got["ferry_file_untouched"], got["errors"])
    check("a slot with no cast and no style law still sends its bare "
          "description", got["bare_composed"] == want["bare_composed"])
    for key in sorted(want):
        check(f"unchanged: {key}", got[key] == want[key], got[key])


def test_an_image_written_before_the_move_still_reads() -> None:
    """The bytes are laid down here by hand, the way the old `_itxt_chunk`
    and JPEG COM writer laid them down, not by today's writer."""
    tmp = Path(tempfile.mkdtemp())
    try:
        fields = {"authorlm:prompt": "a lone pilgrim — with a wide hat",
                  "authorlm:model": "gemini/test-image",
                  "authorlm:off-model": "true",
                  "authorlm:cast": "pilgrim=pilgrim-01.png"}
        png = tiny_png()
        chunks = b"".join(
            _chunk(b"iTXt", k.encode("latin-1") + b"\x00\x00\x00\x00\x00"
                   + v.encode("utf-8")) for k, v in fields.items())
        (tmp / "old.png").write_bytes(png[:33] + chunks + png[33:])
        check("an old PNG's authorlm: fields read back",
              illus.read_metadata(tmp / "old.png") == fields,
              illus.read_metadata(tmp / "old.png"))
        segments = b""
        for k, v in fields.items():
            body = f"{k}: {v}".encode("utf-8")
            segments += b"\xff\xfe" + struct.pack(">H", len(body) + 2) + body
        (tmp / "old.jpg").write_bytes(
            b"\xff\xd8" + segments + b"\xff\xda\x00\x02rest\xff\xd9")
        check("an old JPEG's authorlm: fields read back",
              illus.read_metadata(tmp / "old.jpg") == fields,
              illus.read_metadata(tmp / "old.jpg"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    if "--record" in sys.argv:
        FIXTURE.write_text(json.dumps(observe(), indent=1, sort_keys=True,
                                      ensure_ascii=False) + "\n",
                           encoding="utf-8")
        print(f"recorded {FIXTURE}")
        return 0
    tests = [test_nothing_a_manuscript_holds_changed_with_the_move,
             test_an_image_written_before_the_move_still_reads]
    failed = 0
    for test in tests:
        print(test.__name__)
        try:
            test()
        except AssertionError:
            failed += 1
    print(f"{len(tests) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
