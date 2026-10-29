"""Footnote `| label:` override parsing (`directives._LABEL_OPT`).

The override must sit at the end of the tag body, letters only; a match
uppercases the stem and strips it from the gist. A typo (digit, trailing
junk, empty value) must NOT partially apply — the whole `| label:…`
stays in the gist and `label` stays None, so `next_label` falls back to
the file's series instead of minting a bad stem.

`test_api` covers one happy `| label: RD` through `scan_text`. This file
pins the refusal and case/spacing edges that decide whether an author
typo silently poisons the footnote series.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import directives as dv


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _fn(body: str) -> dict:
    tags = dv.scan_text(f"X.[Footnote: {body}]\n", "footnote")
    check(f"exactly one footnote for body {body!r}", len(tags) == 1, tags)
    return tags[0]


def test_happy_override_strips_and_uppercases() -> None:
    for body, want_gist, want_label in (
        ("Jung's date | label: RD", "Jung's date", "RD"),
        ("Jung's date | label: rd", "Jung's date", "RD"),
        ("Jung's date | LABEL: Ab", "Jung's date", "AB"),
        ("cite|label:XY", "cite", "XY"),
        ("cite  |  label:  zz  ", "cite", "ZZ"),
    ):
        tag = _fn(body)
        check(f"{body!r} → gist {want_gist!r}",
              tag["gist"] == want_gist, repr(tag["gist"]))
        check(f"{body!r} → label {want_label!r}",
              tag["label"] == want_label, repr(tag["label"]))


def test_typos_do_not_partially_apply() -> None:
    """Digits, empty value, or trailing junk after the override: the
    regex fails at `$`, so the author keeps seeing the raw `| label:…`
    in the gist and no stem is taken."""
    for body in (
        "cite | label: RD2",
        "cite | label:",
        "cite | label: RD | extra",
        "cite | label: R-D",
        "cite | label: RD more",
    ):
        tag = _fn(body)
        check(f"{body!r} keeps full body as gist",
              tag["gist"] == body, repr(tag["gist"]))
        check(f"{body!r} leaves label None",
              tag["label"] is None, repr(tag["label"]))


def test_override_only_on_footnotes() -> None:
    """Explain / suggestion bodies may contain `| label:` text; only
    footnotes parse the override."""
    text = ("A.[Explain: note | label: RD]\n\n"
            "B.[Suggestion: tighten | label: RD]\n")
    ex = dv.scan_text(text, "explain")
    sg = dv.scan_text(text, "suggestion")
    check("explain ignores | label:",
          ex[0]["gist"] == "note | label: RD" and ex[0].get("label") is None,
          repr(ex[0]))
    check("suggestion ignores | label:",
          sg[0]["gist"] == "tighten | label: RD"
          and sg[0].get("label") is None, repr(sg[0]))


def main() -> None:
    tests = [
        test_happy_override_strips_and_uppercases,
        test_typos_do_not_partially_apply,
        test_override_only_on_footnotes,
    ]
    for fn in tests:
        print(fn.__name__)
        fn()
    print(f"OK — {len(tests)} label_opt cases")


if __name__ == "__main__":
    main()
