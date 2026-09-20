"""Refuse lens replacements that would rewrite inside a larger word.

`_edit_entry` anchors a finding's optional `replacement` by replacing the
verbatim quote inside its unit. A short quote that appears only as a
prefix/infix of a longer token (quote `form` inside `formalism`) used to
pass the uniqueness gate and corrupt the manuscript via `str.replace`
(`structurealism`). The door must refuse that fragment, while still
accepting a free-standing whole-word or multi-word quote.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from authorlm.lenses import _edit_entry, _quote_is_word_fragment

PASSED = 0


def check(label: str, cond: bool, detail="") -> None:
    global PASSED
    if cond:
        PASSED += 1
        print(f"  OK  {label}")
    else:
        print(f"  FAIL  {label}: {detail}")
        raise SystemExit(1)


def main() -> None:
    print("lens edit fragment refuse:")

    check("form inside formalism is a word fragment",
          _quote_is_word_fragment(
              "Kantian formalism underwrites the claim.", "form"))
    check("standalone form is not a fragment",
          not _quote_is_word_fragment(
              "The form of judgment matters.", "form"))
    check("phrase spanning spaces is not a fragment",
          not _quote_is_word_fragment(
              "Kantian formalism underwrites the claim.",
              "Kantian formalism"))
    check("quote at unit start before space is free-standing",
          not _quote_is_word_fragment("form matters here.", "form"))
    check("quote before punctuation is free-standing",
          not _quote_is_word_fragment("the form, then the matter.", "form"))

    entry, reason = _edit_entry(
        ["Kantian formalism underwrites the claim."],
        "form", "structure", "prefer structure", set())
    check("fragment quote refuses rather than rewriting formalism",
          entry is None and "fragment of a larger word" in reason, reason)

    entry, reason = _edit_entry(
        ["The form of judgment matters."],
        "form", "structure", "prefer structure", set())
    check("whole-word quote still stages the surgical unit rewrite",
          entry is not None
          and entry["new"] == "The structure of judgment matters.",
          (entry, reason))

    entry, reason = _edit_entry(
        ["Kantian formalism underwrites the claim."],
        "Kantian formalism", "Kantian structure", "reword", set())
    check("multi-word quote still rewrites its free-standing span",
          entry is not None
          and entry["new"] == "Kantian structure underwrites the claim.",
          (entry, reason))

    entry, reason = _edit_entry(
        ["The form of formalism."],
        "form", "structure", "prefer structure", set())
    check("word + prefix still refuses as ambiguous (unchanged gate)",
          entry is None and "more than once" in reason, reason)

    print(f"\nALL CHECKS PASSED ({PASSED})")


if __name__ == "__main__":
    main()
