"""`revisions.peel_embed_suffix` keeps illustration settle from raising
false "text drifted" when an embed line is glued under its
`[Illustration: …]` tag.

Observation strips embeds; disk keeps them on the next line with no
blank between, so `_paragraphs` makes one unit. Compose must peel the
suffix for the drift check and re-attach it after the pending form.
`strip_embed_lines` is the observation half of the same contract. Neither
was called directly by a hermetic test before this file.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.revisions import peel_embed_suffix, strip_embed_lines


EMBED = "![](_illustrations/plate-01.png)"
EMBED_DOT = "![](./_illustrations/plate-02.png)"
OTHER = "![](assets/cover.png)"


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_no_embed() -> None:
    para = "[Illustration: river]\nProse under the tag."
    core, suffix = peel_embed_suffix(para)
    check("no embed → core is the whole paragraph", core == para, repr(core))
    check("no embed → empty suffix", suffix == "", repr(suffix))


def test_single_trailing_embed() -> None:
    para = f"[Illustration: river]\n{EMBED}"
    core, suffix = peel_embed_suffix(para)
    check("core keeps the tag line",
          core == "[Illustration: river]", repr(core))
    check("suffix is a leading newline plus the embed",
          suffix == f"\n{EMBED}", repr(suffix))
    check("rejoining recovers the paragraph",
          core + suffix == para)


def test_relative_and_multiple_embeds() -> None:
    para = f"Tag line\n{EMBED}\n{EMBED_DOT}"
    core, suffix = peel_embed_suffix(para)
    check("core stops before the first trailing embed",
          core == "Tag line", repr(core))
    check("every trailing _illustrations embed is peeled",
          suffix == f"\n{EMBED}\n{EMBED_DOT}", repr(suffix))


def test_non_illustration_image_stays() -> None:
    para = f"Cover note\n{OTHER}"
    core, suffix = peel_embed_suffix(para)
    check("a non-_illustrations image is not an embed suffix",
          core == para and suffix == "", (core, suffix))


def test_trailing_whitespace_on_embed() -> None:
    para = f"Tag\n{EMBED}  \t"
    core, suffix = peel_embed_suffix(para)
    check("trailing spaces on an embed line still match",
          core == "Tag" and suffix == f"\n{EMBED}  \t",
          (core, suffix))


def test_empty_and_none() -> None:
    check("empty string peels to itself",
          peel_embed_suffix("") == ("", ""))
    check("None peels like empty",
          peel_embed_suffix(None) == (None, ""))  # type: ignore[arg-type]


def test_strip_embed_lines() -> None:
    text = (f"Lead.\n\n[Illustration: a]\n{EMBED}\n\n"
            f"Middle.\n{OTHER}\n\nTail.\n{EMBED_DOT}\n")
    got = strip_embed_lines(text)
    check("observation drops only _illustrations embeds",
          EMBED not in got and EMBED_DOT not in got, got)
    check("non-illustration images stay", OTHER in got, got)
    check("prose and tags stay",
          "Lead." in got and "[Illustration: a]" in got
          and "Middle." in got and "Tail." in got, got)


def main() -> None:
    for t in (test_no_embed, test_single_trailing_embed,
              test_relative_and_multiple_embeds,
              test_non_illustration_image_stays,
              test_trailing_whitespace_on_embed,
              test_empty_and_none, test_strip_embed_lines):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
