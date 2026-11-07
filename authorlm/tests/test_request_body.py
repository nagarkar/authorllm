"""`media_gen.request_body` is the Veo predictLongRunning wire shape.

`personGeneration` flips between `allow_all` (text-only) and
`allow_adult` (any first-frame or reference image). Getting that wrong
either rejects a legitimate image prompt at the API or loosens the
person-generation policy on a text-only call. `test_media` covers one
text-only happy path; these pins cover the image branch and the
last-frame-alone edge the gate deliberately ignores.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import media_gen

# Minimal 1×1 PNG so `_inline` can sniff the mime type without fixtures.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f80f00000101000518d84e0000000049454e44ae426082"
)


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _plan(**overrides) -> dict:
    base = {
        "prompt": "a quiet room",
        "first_frame": None,
        "last_frame": None,
        "references": [],
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "duration": 6,
    }
    base.update(overrides)
    return base


def test_text_only_allows_all() -> None:
    body = media_gen.request_body(_plan())
    check("text-only personGeneration is allow_all",
          body["parameters"]["personGeneration"] == "allow_all",
          body["parameters"])
    check("duration is an int seconds field",
          body["parameters"]["durationSeconds"] == 6)
    check("no image keys on a text-only instance",
          set(body["instances"][0]) == {"prompt"}, body["instances"][0])


def test_first_frame_allows_adult() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        frame = Path(tmp) / "first.png"
        frame.write_bytes(_PNG)
        body = media_gen.request_body(_plan(first_frame=str(frame)))
    check("first frame flips to allow_adult",
          body["parameters"]["personGeneration"] == "allow_adult",
          body["parameters"])
    check("first frame lands under image",
          "image" in body["instances"][0], body["instances"][0].keys())
    check("inline mime is image/png",
          body["instances"][0]["image"]["inlineData"]["mimeType"]
          == "image/png")


def test_references_allow_adult() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ref = Path(tmp) / "ref.png"
        ref.write_bytes(_PNG)
        body = media_gen.request_body(_plan(references=[str(ref)]))
    check("a reference alone flips to allow_adult",
          body["parameters"]["personGeneration"] == "allow_adult",
          body["parameters"])
    refs = body["instances"][0]["referenceImages"]
    check("referenceImages carries asset type",
          refs == [{"image": refs[0]["image"],
                    "referenceType": "asset"}], refs)


def test_last_frame_alone_stays_allow_all() -> None:
    # The gate keys off first_frame/references only — lastFrame is a
    # continuation cue, not a person-generation input for this policy.
    with tempfile.TemporaryDirectory() as tmp:
        last = Path(tmp) / "last.png"
        last.write_bytes(_PNG)
        body = media_gen.request_body(_plan(last_frame=str(last)))
    check("last frame alone keeps allow_all",
          body["parameters"]["personGeneration"] == "allow_all",
          body["parameters"])
    check("lastFrame is still attached",
          "lastFrame" in body["instances"][0], body["instances"][0].keys())


def test_full_image_bundle() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        first = Path(tmp) / "first.png"
        last = Path(tmp) / "last.png"
        ref = Path(tmp) / "ref.png"
        for p in (first, last, ref):
            p.write_bytes(_PNG)
        body = media_gen.request_body(_plan(
            first_frame=str(first), last_frame=str(last),
            references=[str(ref)], duration="8",
            aspect_ratio="9:16", resolution="1080p"))
    params = body["parameters"]
    inst = body["instances"][0]
    check("bundle is allow_adult",
          params["personGeneration"] == "allow_adult", params)
    check("duration string coerces to int",
          params["durationSeconds"] == 8, params)
    check("aspect and resolution pass through",
          params["aspectRatio"] == "9:16"
          and params["resolution"] == "1080p", params)
    check("all three image slots are present",
          {"prompt", "image", "lastFrame", "referenceImages"}
          <= set(inst), inst.keys())


def main() -> None:
    for t in (test_text_only_allows_all, test_first_frame_allows_adult,
              test_references_allow_adult, test_last_frame_alone_stays_allow_all,
              test_full_image_bundle):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
