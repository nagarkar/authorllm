"""`extraction._changed_sections` and `_fit_inventory` bound extraction
attention and prompt size.

`_changed_sections` is the section-grain attention scope: one edited
paragraph must send its heading section, not the whole file, and a
moved-but-unchanged section must stay out. `_fit_inventory` drops
KNOWN CONCEPTS names from the end so the inventory never pushes a
payload past `max_chars` — the manuscript text is never the thing that
gives way. Neither helper had a direct hermetic assertion.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.extraction import _changed_sections, _fit_inventory


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_changed_sections_section_grain() -> None:
    old = ("# Book\n\nPreamble stays.\n\n"
           "## One\n\nOriginal one.\n\n"
           "## Two\n\nOriginal two.\n")
    new = ("# Book\n\nPreamble stays.\n\n"
           "## One\n\nRewritten one.\n\n"
           "## Two\n\nOriginal two.\n")
    got = _changed_sections(old, new)
    check("only the edited section is returned",
          got == ["## One\n\nRewritten one."], got)
    # Moved but byte-identical: order-blind membership must exclude it.
    moved = ("# Book\n\nPreamble stays.\n\n"
             "## Two\n\nOriginal two.\n\n"
             "## One\n\nOriginal one.\n")
    check("moved-but-unchanged sections are excluded",
          _changed_sections(old, moved) == [])
    added = old + "\n## Three\n\nBrand new.\n"
    check("a brand-new section is included",
          _changed_sections(old, added) == ["## Three\n\nBrand new."],
          _changed_sections(old, added))
    check("identical texts → empty",
          _changed_sections(old, old) == [])


def test_fit_inventory_drops_from_end() -> None:
    names = ["Alpha", "Beta", "Gamma", "Delta"]
    # Body already near the cap: inventory must shrink from the end.
    body_len = 20
    max_chars = body_len + len("KNOWN CONCEPTS: \n\n") + len("Alpha; Beta")
    got = _fit_inventory(list(names), max_chars, body_len)
    check("names drop from the end until the prefix fits",
          got == "Alpha; Beta", got)
    check("empty names → empty string",
          _fit_inventory([], 100, 10) == "")
    # When even one name cannot fit, return empty rather than overflowing.
    tiny = body_len + len("KNOWN CONCEPTS: \n\n") + 1
    check("overflowing single name yields empty inventory",
          _fit_inventory(["VeryLongConceptName"], tiny, body_len) == "")
    roomy = _fit_inventory(list(names), 10_000, 10)
    check("ample budget keeps every name",
          roomy == "Alpha; Beta; Gamma; Delta", roomy)


def main() -> None:
    for t in (test_changed_sections_section_grain,
              test_fit_inventory_drops_from_end):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
