"""Pure helpers of the Lint view (`lint_page`): where a pending form sits,
how its origin is named, which rule a quote-style finding becomes, and
how a spelling-variant rewrite keeps the match's capitals.

These decide whether a mechanical fix rewrites the right span, whether
a blocked fix names the form that stopped it, whether an unbalanced
quote is ruled one-by-one instead of bulk-curled, and whether Colour→Color
keeps COLOUR / Colour. No prior suite pinned them by name.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import api, lint_page


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_page_rule_splits_unbalanced() -> None:
    check("a mixed quote-style finding stays quote-style (bulk)",
          lint_page._page_rule({"code": "quote-style", "ref": "mixed"})
          == "quote-style")
    check("an unbalanced quote-style finding becomes unbalanced-quotes",
          lint_page._page_rule({"code": "quote-style", "ref": "unbalanced"})
          == "unbalanced-quotes")
    check("every other code passes through",
          lint_page._page_rule({"code": "spelling", "ref": "colour"})
          == "spelling")


def test_cased_keeps_match_capitals() -> None:
    check("all-caps match (length > 1) uppercases the replacement",
          lint_page._cased("COLOUR", "color") == "COLOR")
    check("a single capital keeps title case on the replacement",
          lint_page._cased("Colour", "color") == "Color")
    check("a lower match leaves the replacement as given",
          lint_page._cased("colour", "color") == "color")
    check("a one-letter upper match is title case, not ALL CAPS",
          lint_page._cased("A", "an") == "An")


def test_origin_name() -> None:
    check("an outside caller's origin metadata is named",
          lint_page._origin_name({
              "origin_type": api.EXTERNAL_ORIGIN,
              "metadata": json.dumps({"origin": "harvest"}),
          }) == "harvest")
    check("a bare outside caller falls back to a plain label",
          lint_page._origin_name({
              "origin_type": api.EXTERNAL_ORIGIN,
              "metadata": "{}",
          }) == "an outside caller")
    check("a lens names its short id, dropping hash and file",
          lint_page._origin_name({
              "origin_type": api.LENS_ORIGIN,
              "origin_id": "plain-reader@abc:ch.md:1",
              "metadata": "{}",
          }) == "lens plain-reader")
    check("a filter run is named as such",
          lint_page._origin_name({
              "origin_type": api.FILTER_ORIGIN,
              "origin_id": "run-1",
              "metadata": "{}",
          }) == "a filter run")
    check("any other origin_type is echoed",
          lint_page._origin_name({
              "origin_type": "critique",
              "metadata": "{}",
          }) == "critique")


def test_form_span() -> None:
    text = ("# Title\n\n"
            "First paragraph holds the phrase once.\n\n"
            "Second paragraph holds the phrase again.\n")
    phrase = "the phrase"
    unique = ("Only one hit of UNIQUE_OLD in this file.\n")
    check("an insert (no old words) has no span",
          lint_page._form_span(text, {"proposed_old": ""}) is None)
    check("a unique old half is its only start..end",
          lint_page._form_span(unique, {"proposed_old": "UNIQUE_OLD",
                                        "metadata": "{}"})
          == (unique.index("UNIQUE_OLD"),
              unique.index("UNIQUE_OLD") + len("UNIQUE_OLD")))
    # Ambiguous: two hits; the anchor picks the second paragraph.
    spans = lint_page.paragraph_spans(text)
    second = spans[2]  # title, first prose, second prose (0-based)
    hit = lint_page._form_span(text, {
        "proposed_old": phrase,
        "metadata": json.dumps({"anchor_paragraph": 3}),
    })
    check("an ambiguous old half uses anchor_paragraph when it fits",
          hit == (text.index(phrase, second[0]),
                  text.index(phrase, second[0]) + len(phrase)),
          hit)
    first_hit = text.index(phrase)
    check("without a usable anchor, the first hit wins",
          lint_page._form_span(text, {
              "proposed_old": phrase,
              "metadata": "{}",
          }) == (first_hit, first_hit + len(phrase)))
    check("a missing old half returns the sentinel (-1, -1)",
          lint_page._form_span(text, {
              "proposed_old": "NO SUCH WORDS",
              "metadata": "{}",
          }) == (-1, -1))


def main() -> None:
    for t in (test_page_rule_splits_unbalanced, test_cased_keeps_match_capitals,
              test_origin_name, test_form_span):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
