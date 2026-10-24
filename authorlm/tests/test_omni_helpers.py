"""Pure Omni helpers from #255/#257: model gate, duration clamp, billed
USD from token usage, and the edit request body.

`test_media.test_omni_*` covers one happy start/poll/edit path. These pin
the edges that gate spend and the edit wire shape: wrong clamp bills the
wrong length; a broken `omni_usd` formula mis-reports cost; an `edit_body`
drift breaks podlm/Google's revise call.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import media_gen
from authorlm.llm import DEFAULT_IMAGE_MODEL


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_is_omni_and_default_image_model() -> None:
    check("gemini-omni prefix is Omni",
          media_gen.is_omni("gemini-omni-1.1-flash") is True)
    check("a longer Omni id stays Omni",
          media_gen.is_omni("gemini-omni-9.9-pro") is True)
    check("Veo is not Omni",
          media_gen.is_omni("veo-3.1-fast-generate-preview") is False)
    check("empty model is not Omni", media_gen.is_omni("") is False)
    # #256: 2.5 Flash Image is deprecated; pin the replacement string so a
    # revert cannot slip past tests that only compare against the constant.
    check("DEFAULT_IMAGE_MODEL is gemini-3.1-flash-lite-image",
          DEFAULT_IMAGE_MODEL == "gemini/gemini-3.1-flash-lite-image",
          DEFAULT_IMAGE_MODEL)


def test_omni_duration_clamp() -> None:
    d = media_gen._duration
    check("Omni floors under 3s to 3",
          d({"estimated_seconds": 1}, False, omni=True) == 3)
    check("Omni keeps 4s",
          d({"estimated_seconds": 4}, False, omni=True) == 4)
    check("Omni caps over 10s at 10",
          d({"estimated_seconds": 40}, False, omni=True) == 10)
    check("Omni missing estimate defaults toward 8 (in range)",
          d({}, False, omni=True) == 8)
    # forced_eight is a Veo path; Omni ignores it and uses the estimate.
    check("Omni ignores forced_eight",
          d({"estimated_seconds": 4}, True, omni=True) == 4)
    check("Veo path still buckets when omni=False",
          d({"estimated_seconds": 5}, False, omni=False) == 6)


def test_omni_usd_from_usage() -> None:
    # Live probe shape from test_media (28 in, 23168 video out, 259 thought):
    # 28*1.5e-6 + (0+259)*9e-6 + 23168*17.5e-6 ≈ 0.41
    usage = {"total_input_tokens": 28, "total_output_tokens": 23168,
             "total_thought_tokens": 259,
             "output_tokens_by_modality": [
                 {"modality": "video", "tokens": 23168}]}
    got = media_gen.omni_usd(usage)
    check("live-shaped usage rounds to 0.41", got == 0.41, got)
    check("empty usage is 0.0", media_gen.omni_usd({}) == 0.0)
    # Text-only output (no video modality row): all output tokens are text.
    text_only = {"total_input_tokens": 1000, "total_output_tokens": 100,
                 "total_thought_tokens": 0,
                 "output_tokens_by_modality": []}
    # 1000*1.5e-6 + 100*9e-6 = 0.0024 → 0.0 at two decimals
    check("small text-only usage rounds to 0.0",
          media_gen.omni_usd(text_only) == 0.0)
    bigger = {"total_input_tokens": 0, "total_output_tokens": 10000,
              "total_thought_tokens": 0,
              "output_tokens_by_modality": [
                  {"modality": "video", "tokens": 8000}]}
    # text = 10000-8000 = 2000 → 2000*9e-6 + 8000*17.5e-6 = 0.158 → 0.16
    check("mixed text+video splits modalities",
          media_gen.omni_usd(bigger) == 0.16, media_gen.omni_usd(bigger))


def test_edit_body_wire_shape() -> None:
    p = {"model": "gemini-omni-1.1-flash",
         "previous_interaction_id": "v1_abc",
         "prompt": "warmer light Keep everything else the same.",
         "aspect_ratio": "16:9"}
    body = media_gen.edit_body(p)
    check("edit continues the prior interaction",
          body["previous_interaction_id"] == "v1_abc")
    check("edit runs in the background", body["background"] is True)
    check("edit input is the instruction prompt string",
          body["input"] == p["prompt"])
    check("edit response is video at 720p",
          body["response_format"] == {"type": "video",
                                      "aspect_ratio": "16:9",
                                      "resolution": "720p"},
          body["response_format"])
    check("edit names the Omni model",
          body["model"] == "gemini-omni-1.1-flash")


def main() -> None:
    tests = [test_is_omni_and_default_image_model, test_omni_duration_clamp,
             test_omni_usd_from_usage, test_edit_body_wire_shape]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
