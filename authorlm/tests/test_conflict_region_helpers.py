"""`forms_page.conflict_segments` / `region_text` / `_wrap_like` /
`_landing_text` / `_touches_tag` decide what a Merge-forms conflict card
puts on disk, and what a ruled form's green half carries into resolve.

`test_forms_conflicts.py` exercises the page end-to-end. These pins are
the pure edges that a wrong edit silently corrupts: one-side changes
must not become cards; identical dual regions need distinct ids; Keep
both with an empty side must not leave a blank paragraph; Edit must keep
the region's blank-line wrap; accept/edit/decline must land the right
half. Hermetic — shells out only to `git merge-file` (as production
does). No DB, no Google, no model.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("AUTHORLM_CONFIG", "/nonexistent/authorlm-test/config.toml")
os.environ.setdefault("AUTHORLM_ENV", "/nonexistent/authorlm-test/.env")
os.environ.setdefault("AUTHORLM_CLIENT", "none")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import forms_page, gdocs


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_conflict_segments_one_card() -> None:
    base = "A\n\nB\n\nC\n"
    local = "A\n\nB-mine\n\nC\n"
    doc = "A\n\nB-doc\n\nC\n"
    segs = forms_page.conflict_segments(local, base, doc)
    regions = [s for s in segs if isinstance(s, dict)]
    cleans = [s for s in segs if isinstance(s, str)]
    check("one both-sides region becomes one card", len(regions) == 1, segs)
    check("clean sides stay as joined text around it",
          len(cleans) == 2 and cleans[0] == "A\n\n" and cleans[1] == "\nC\n",
          cleans)
    card = regions[0]
    check("card carries each side's lines with their ends",
          card["local"] == "B-mine\n" and card["base"] == "B\n"
          and card["doc"] == "B-doc\n", card)
    check("card id is the hash of the three sides",
          card["id"] == forms_page._sides_key(
              card["local"], card["base"], card["doc"]), card)


def test_one_side_only_is_clean_text() -> None:
    base = "A\n\nB\n\nC\n"
    # Local rewrote A; Doc rewrote C — neither region is both-sides.
    local = "A-mine\n\nB\n\nC\n"
    doc = "A\n\nB\n\nC-doc\n"
    segs = forms_page.conflict_segments(local, base, doc)
    check("one-side edits merge by themselves: no cards",
          segs == ["A-mine\n\nB\n\nC-doc\n"] and all(
              isinstance(s, str) for s in segs), segs)


def test_duplicate_region_ids_are_suffixed() -> None:
    # Two regions with the same three texts must keep distinct ids so a
    # ruling on one cannot stick to the other after a recompute.
    local = "X\n\nSame\n\nY\n\nSame\n\nZ\n"
    base = "X\n\nOld\n\nY\n\nOld\n\nZ\n"
    doc = "X\n\nDoc\n\nY\n\nDoc\n\nZ\n"
    ids = [s["id"] for s in forms_page.conflict_segments(local, base, doc)
           if isinstance(s, dict)]
    check("two identical regions get a -2 suffix on the second",
          len(ids) == 2 and ids[0] != ids[1] and ids[1] == ids[0] + "-2",
          ids)


def test_unclosed_region_fails_loudly() -> None:
    # conflict_segments never invents a close for a truncated merge-file
    # dump; hand settlement is the only recovery. Feed the parser a
    # broken marker dump through merge3.
    real = gdocs.merge3

    def broken(local, base, doc, relpath="", diff3=False):
        return ("<<<<<<< local\nstuck\n", [1])

    gdocs.merge3 = broken  # type: ignore[assignment]
    try:
        forms_page.conflict_segments("a", "b", "c")
    except LookupError as exc:
        check("unclosed region raises LookupError naming hand settle",
              "unclosed" in str(exc).lower()
              and "doc merge" in str(exc).lower(), exc)
    else:
        check("unclosed region raises LookupError naming hand settle", False)
    finally:
        gdocs.merge3 = real  # type: ignore[assignment]


def test_region_text_choices() -> None:
    region = {"local": "Mine.\n", "base": "Old.\n", "doc": "Doc.\n"}
    check("mine keeps the file's side",
          forms_page.region_text(region, {"verdict": "mine"}) == "Mine.\n")
    check("doc keeps the Doc's side",
          forms_page.region_text(region, {"verdict": "doc"}) == "Doc.\n")
    check("both joins as separate paragraphs",
          forms_page.region_text(region, {"verdict": "both"})
          == "Mine.\n\nDoc.\n")
    check("both with empty mine is just the Doc",
          forms_page.region_text({"local": "", "doc": "Doc.\n"},
                                 {"verdict": "both"}) == "Doc.\n")
    check("both with empty doc is just mine",
          forms_page.region_text({"local": "Mine.\n", "doc": ""},
                                 {"verdict": "both"}) == "Mine.\n")
    check("edit puts the author's text where the region sat",
          forms_page.region_text(region, {"verdict": "edit",
                                          "text": "Settled."})
          == "Settled.\n")


def test_wrap_like_keeps_blank_lines() -> None:
    check("leading and trailing blank lines of the region are kept",
          forms_page._wrap_like("EDIT", "\n\nBODY\n\n") == "\n\nEDIT\n\n")
    check("an all-blank region still ends with a newline",
          forms_page._wrap_like("EDIT", "\n\n") == "EDIT\n")
    check("no blank lines → body plus a trailing newline",
          forms_page._wrap_like("EDIT", "BODY\n") == "EDIT\n")


def test_landing_text_by_verdict() -> None:
    row = {"verdict": "accept", "seen": "as seen", "text": "as edited"}
    check("accept lands the text the author saw",
          forms_page._landing_text(row) == "as seen")
    check("edit lands the author's rewrite",
          forms_page._landing_text({**row, "verdict": "edit"}) == "as edited")
    check("decline lands an empty half (form removed)",
          forms_page._landing_text({**row, "verdict": "decline"}) == "")


def test_touches_tag() -> None:
    tag = "[Illustration: A gem ⇢ a-gem.md]\n"
    check("a region whose any side is a picture tag is flagged",
          forms_page._touches_tag("plain\n", tag) is True)
    check("ordinary prose is not",
          forms_page._touches_tag("plain prose\n", "also plain\n") is False)


def main() -> None:
    tests = [
        test_conflict_segments_one_card,
        test_one_side_only_is_clean_text,
        test_duplicate_region_ids_are_suffixed,
        test_unclosed_region_fails_loudly,
        test_region_text_choices,
        test_wrap_like_keeps_blank_lines,
        test_landing_text_by_verdict,
        test_touches_tag,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
