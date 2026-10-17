"""`lenses.resolve_pointers` decides which other chapters a lens INPUTS
payload may pull in as pointed-to essays.

A hit is a case-sensitive whole-phrase match against a chapter title
(or its article-stripped form / alias). Lowercase prose must not fire;
a framed \"the essay on X\" whose X matches no chapter is reported as
unresolved so the alias table can grow; the file never points at itself.
Wrong matching either floods the payload with false positives or drops
real cross-chapter context.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.lenses import chapter_names, resolve_pointers


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


FILES = {
    "toc.toml": '[[chapter]]\nfile = "fear.md"\n[[chapter]]\n'
                'file = "good-life.md"\n[[chapter]]\nfile = "essay.md"\n',
    "fear.md": "# Fear\n\nFear is the subject.\n",
    "good-life.md": "# The Good Life\n\nLiving well.\n",
    "essay.md": (
        "# This Essay\n\n"
        "We return to Fear and to The Good Life.\n"
        "Elsewhere one might speak of the good life in lowercase.\n"
        "See also the essay on Unmapped Topic for contrast.\n"
    ),
}


def test_chapter_names_article_strip() -> None:
    names = chapter_names(FILES)
    check("Fear keeps its title",
          "Fear" in names["fear.md"], names["fear.md"])
    check("The Good Life keeps the full title",
          "The Good Life" in names["good-life.md"], names["good-life.md"])
    check("article-stripped Good Life is also a name",
          "Good Life" in names["good-life.md"], names["good-life.md"])
    check("structural toc.toml is absent",
          "toc.toml" not in names)


def test_case_sensitive_hit() -> None:
    resolved, unresolved = resolve_pointers(FILES, "essay.md")
    targets = {f for f, _ in resolved}
    check("Fear is resolved (capitalized)",
          "fear.md" in targets, resolved)
    check("The Good Life is resolved",
          "good-life.md" in targets, resolved)
    check("self is never a target",
          "essay.md" not in targets, resolved)


def test_lowercase_prose_is_not_a_pointer() -> None:
    files = {
        **FILES,
        "essay.md": "# This Essay\n\nSpeaking of the good life casually.\n",
    }
    resolved, _ = resolve_pointers(files, "essay.md")
    check("lowercase 'the good life' does not resolve Good Life",
          resolved == [], resolved)


def test_unresolved_framed_reference() -> None:
    _, unresolved = resolve_pointers(FILES, "essay.md")
    check("framed 'the essay on Unmapped Topic' is unresolved",
          any("Unmapped Topic" in u for u in unresolved), unresolved)


def main() -> None:
    tests = [
        test_chapter_names_article_strip,
        test_case_sensitive_hit,
        test_lowercase_prose_is_not_a_pointer,
        test_unresolved_framed_reference,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
