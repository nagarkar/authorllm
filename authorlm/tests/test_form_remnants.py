"""`form_grammar.form_remnants` / `strip_form_remnants` clean hand-resolved
Doc forms after pull.

When the author deletes `<<old>>{{` in the Doc and keeps the new half with
its closing `}}`, pull must strip that remnant — and only that remnant.
Treating an open form's own closer, author prose `}}`, or a half still
opened by `{{` as a remnant corrupts the tab. #346 moved this grammar out
of `threads`; nothing hermetically pins the remnant edge cases.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.form_grammar import form_remnants, strip_form_remnants


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_hand_resolved_remnant() -> None:
    # Author kept the new half and its }} after deleting <<old>>{{.
    text = "Lead. new span}} Tail."
    spots = form_remnants(text, ["new span"])
    check("remnant offset points at the closing braces",
          spots == [(len("Lead. new span"), "new span")], spots)
    cleaned, taken = strip_form_remnants(text, ["new span"])
    check("strip removes only the remnant braces",
          cleaned == "Lead. new span Tail." and taken == ["new span"],
          (cleaned, taken))


def test_open_form_closer_is_not_a_remnant() -> None:
    # An old thread whose new half is "." must not steal the closer off a
    # live form that ends in a full stop.
    text = "Before. <<old text>>{{new text.}} After."
    spots = form_remnants(text, [".", "new text."])
    check("open {{…}} closer is not reported as a remnant",
          spots == [], spots)


def test_half_opened_by_braces_is_skipped() -> None:
    text = "Keep {{new span}} intact."
    spots = form_remnants(text, ["new span"])
    check("a half still opened by {{ is not a remnant",
          spots == [], spots)


def test_prose_braces_untouched() -> None:
    text = "Set notation: {a, b}} is author prose."
    spots = form_remnants(text, ["new span", "missing"])
    check("}} that does not follow an owned new half is ignored",
          spots == [], spots)
    cleaned, taken = strip_form_remnants(text, ["new span"])
    check("strip is a no-op when nothing matches",
          cleaned == text and taken == [], (cleaned, taken))


def test_longest_half_wins_and_order_is_stable() -> None:
    text = "Alpha beta}} and beta}} done."
    spots = form_remnants(text, ["beta", "Alpha beta"])
    check("longer half claims the earlier remnant; shorter takes the later",
          spots == [(len("Alpha beta"), "Alpha beta"),
                    (len("Alpha beta}} and beta"), "beta")],
          spots)
    cleaned, taken = strip_form_remnants(text, ["beta", "Alpha beta"])
    check("both remnants peel without eating prose",
          cleaned == "Alpha beta and beta done."
          and taken == ["Alpha beta", "beta"],
          (cleaned, taken))


def test_empty_news_and_empty_halves() -> None:
    text = "plain}} text"
    check("empty news list finds nothing",
          form_remnants(text, []) == [])
    check("blank new halves are ignored",
          form_remnants(text, ["", None]) == [])  # type: ignore[list-item]


def main() -> None:
    for t in (test_hand_resolved_remnant,
              test_open_form_closer_is_not_a_remnant,
              test_half_opened_by_braces_is_skipped,
              test_prose_braces_untouched,
              test_longest_half_wins_and_order_is_stable,
              test_empty_news_and_empty_halves):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
