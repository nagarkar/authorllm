"""Doc sync doors that silently corrupt when wrong.

`_split_by_anchors` — every pull cuts the whole-Doc export (no tab-title
headings in it) at each tab's opening heading, read from the Docs API as
the first paragraph styled HEADING_n, backed up over the paragraphs that
precede it (an epigraph). A non-heading line never matches, so a short
dialogue line that repeats a tab's opener cannot steal the prior chapter's
tail; the gate stops a pull whose tabs share an opening heading, whose
later tab opens with a heading an earlier tab holds inside, or whose
chapter tab opens without one.

`_holds_doc_edits` — the push guard for a hand-settled conflict. Set
membership treated a Doc add/delete of a repeated line as held whenever
another copy remained, so push restored (or wiped) the Doc's edit. A
bag-of-lines check also treated a Doc-only paragraph reorder as held,
so a guarded push wrote the local order back over the tab.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.gdocs import (ILLUS_TAB_TITLE, _holds_doc_edits,
                            _split_by_anchors, tab_anchors, walk_tabs)


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _tab(title: str, paras: list[tuple[str, str]], children=()) -> dict:
    """A tab as `documents.get(includeTabsContent=True)` returns it:
    each paragraph carries `paragraphStyle.namedStyleType` and its text
    runs (the shape of the recordings in tests/doc_cache/)."""
    return {"tabProperties": {"title": title},
            "documentTab": {"body": {"content": [
                {"paragraph": {"paragraphStyle": {"namedStyleType": style},
                               "elements": [{"textRun": {
                                   "content": text + "\n"}}]}}
                for style, text in paras]}},
            "childTabs": list(children)}


def _split(export: str, *tabs: dict) -> dict[str, str]:
    doc = {"tabs": list(tabs)}
    props: list = []
    walk_tabs(doc["tabs"], props, [])
    return _split_by_anchors(export, [t for _, t in props], tab_anchors(doc))


H1, H2, P = "HEADING_1", "HEADING_2", "NORMAL_TEXT"


def test_dialogue_line_is_not_an_anchor() -> None:
    # Tab b's first prose line, "Yes.", also stands alone earlier in tab a.
    # Only b's opening heading is a candidate; the prose line never is.
    export = ("# **Alpha**\n\nOpening of A.\n\nYes.\n\nMore A.\n\n"
              "# **Beta**\n\nYes.\n\nReal body of B.\n")
    out = _split(export,
                 _tab("a.md", [(H1, "Alpha"), (P, "Opening of A."),
                               (P, "Yes."), (P, "More A.")]),
                 _tab("b.md", [(H1, "Beta"), (P, "Yes."),
                               (P, "Real body of B.")]))
    check("b starts at its opening heading",
          out.get("b.md", "").startswith("# **Beta**\n\nYes.\n"), out)
    check("a keeps its own 'Yes.' line and everything after it",
          out.get("a.md") == "# **Alpha**\n\nOpening of A.\n\nYes.\n\nMore A.\n",
          out.get("a.md"))


def test_epigraph_lands_in_its_own_tab() -> None:
    # Seven SMSTTD chapters open with an italic epigraph (or an
    # `[Omit: all]` tag) and put the heading second.
    export = ("# **Alpha**\n\nBody of A.\n\nLast line of A.\n\n"
              "*The idea of death haunts the human animal.* — Becker\n\n"
              "# **Beta**\n\nBody of B.\n\n"
              "\\[Omit: all\\]\n\n*Some things are in our power.*\n\n"
              "# **Gamma**\n\nBody of C.\n")
    out = _split(export,
                 _tab("a.md", [(H1, "Alpha"), (P, "Body of A."),
                               (P, "Last line of A.")]),
                 _tab("b.md", [(P, "The idea of death haunts the human "
                                   "animal. — Becker"),
                               (H1, "Beta"), (P, "Body of B.")]),
                 _tab("c.md", [(P, "[Omit: all]"),
                               (P, "Some things are in our power."),
                               (H1, "Gamma"), (P, "Body of C.")]))
    check("the epigraph opens its own tab's file",
          out.get("b.md", "").startswith("*The idea of death"), out)
    check("the prior tab ends before the epigraph",
          out.get("a.md") == "# **Alpha**\n\nBody of A.\n\nLast line of A.\n",
          out.get("a.md"))
    check("two paragraphs before the heading both back up",
          out.get("c.md") == ("\\[Omit: all\\]\n\n*Some things are in our "
                              "power.*\n\n# **Gamma**\n\nBody of C.\n"),
          out.get("c.md"))
    check("b ends before c's tag",
          out.get("b.md") == ("*The idea of death haunts the human animal.*"
                              " — Becker\n\n# **Beta**\n\nBody of B.\n"),
          out.get("b.md"))

    # The API says one paragraph precedes the heading but the export unit
    # above it is different text: the back-up stops short rather than
    # taking the prior tab's last paragraph.
    export2 = "# **Alpha**\n\nLast line of A.\n\n# **Beta**\n\nBody of B.\n"
    out = _split(export2,
                 _tab("a.md", [(H1, "Alpha"), (P, "Last line of A.")]),
                 _tab("b.md", [(P, "An epigraph the export lost."),
                               (H1, "Beta"), (P, "Body of B.")]))
    check("a mismatched pre-heading paragraph never takes the prior tab's line",
          out.get("a.md") == "# **Alpha**\n\nLast line of A.\n"
          and out.get("b.md") == "# **Beta**\n\nBody of B.\n", out)

    # Soft-break join of the prior tab's last line with this tab's
    # epigraph: substring containment would steal "Last line of A." into
    # b; equality keeps it in a and leaves the epigraph stuck there
    # rather than inventing a boundary inside the joined unit.
    export3 = ("# **Alpha**\n\nBody of A.\n\nLast line of A.\n"
               "*The idea of death haunts the human animal.* — Becker\n\n"
               "# **Beta**\n\nBody of B.\n")
    out = _split(export3,
                 _tab("a.md", [(H1, "Alpha"), (P, "Body of A."),
                               (P, "Last line of A.")]),
                 _tab("b.md", [(P, "The idea of death haunts the human "
                                   "animal. — Becker"),
                               (H1, "Beta"), (P, "Body of B.")]))
    check("a soft-break-joined prior line is not stolen into the next tab",
          "Last line of A." in out.get("a.md", "")
          and "Last line of A." not in out.get("b.md", "")
          and out.get("b.md") == "# **Beta**\n\nBody of B.\n", out)

    # Prior chapter paraphrases the epigraph; the epigraph line itself is
    # missing from the export. Substring match would pull the paraphrase
    # into b; equality stops short.
    export4 = ("# **Alpha**\n\nBody of A.\n\n"
               "As Becker wrote, the idea of death haunts the human "
               "animal.\n\n# **Beta**\n\nBody of B.\n")
    out = _split(export4,
                 _tab("a.md", [(H1, "Alpha"), (P, "Body of A."),
                               (P, "As Becker wrote, the idea of death "
                                   "haunts the human animal.")]),
                 _tab("b.md", [(P, "the idea of death haunts the human "
                                   "animal."),
                               (H1, "Beta"), (P, "Body of B.")]))
    check("a prior line that merely contains the epigraph is not stolen",
          out.get("a.md") == ("# **Alpha**\n\nBody of A.\n\n"
                              "As Becker wrote, the idea of death haunts "
                              "the human animal.\n")
          and out.get("b.md") == "# **Beta**\n\nBody of B.\n", out)

    # Two before-paragraphs soft-break-joined into one export unit still
    # back up as one unit when the unit equals their join exactly.
    export5 = ("# **Alpha**\n\nBody of A.\n\n"
               "\\[Omit: all\\]\n*Some things are in our power.*\n\n"
               "# **Gamma**\n\nBody of C.\n")
    out = _split(export5,
                 _tab("a.md", [(H1, "Alpha"), (P, "Body of A.")]),
                 _tab("c.md", [(P, "[Omit: all]"),
                               (P, "Some things are in our power."),
                               (H1, "Gamma"), (P, "Body of C.")]))
    check("joined before-paragraphs still back up when the unit equals them",
          out.get("c.md") == ("\\[Omit: all\\]\n*Some things are in our "
                              "power.*\n\n# **Gamma**\n\nBody of C.\n")
          and out.get("a.md") == "# **Alpha**\n\nBody of A.\n", out)


def test_later_echo_of_an_opening_heading_does_not_split() -> None:
    export = ("# **Alpha**\n\nSpeaking of Beta.\n\n## **Beta**\n\nA sub-section "
              "of A named like tab b.\n\n# **Beta**\n\nB body.\n\n"
              "# **Alpha**\n\nB echoes A's opener as a heading of its own.\n")
    out = _split(export,
                 _tab("a.md", [(H1, "Alpha"), (P, "Speaking of Beta."),
                               (H2, "Beta"), (P, "A sub-section")]),
                 _tab("b.md", [(H1, "Beta"), (P, "B body.")]))
    check("prose and a heading of another level never start the next tab",
          out.get("a.md", "").startswith("# **Alpha**\n\nSpeaking of Beta.\n\n"
                                         "## **Beta**"), out)
    check("a later echo of an earlier opener stays inside the later tab",
          out.get("b.md") == ("# **Beta**\n\nB body.\n\n# **Alpha**\n\n"
                              "B echoes A's opener as a heading of its own.\n"),
          out.get("b.md"))


def test_duplicate_opening_heading_stops_the_pull() -> None:
    export = "# **Alpha**\n\nOne.\n\n# **Alpha**\n\nTwo.\n"
    try:
        _split(export, _tab("a.md", [(H1, "Alpha"), (P, "One.")]),
               _tab("b.md", [(H1, "Alpha"), (P, "Two.")]))
        check("two tabs with one opening heading stop the pull", False)
    except LookupError as err:
        check("the stop names both tabs and the heading",
              "'a.md'" in str(err) and "'b.md'" in str(err)
              and "# Alpha" in str(err) and "retitle" in str(err), str(err))
    # Same words at another level is a different heading.
    out = _split("# **Alpha**\n\nOne.\n\n## **Alpha**\n\nTwo.\n",
                 _tab("a.md", [(H1, "Alpha"), (P, "One.")]),
                 _tab("b.md", [(H2, "Alpha"), (P, "Two.")]))
    check("the gate matches on level and text",
          out.get("b.md") == "## **Alpha**\n\nTwo.\n", out)


def test_opening_heading_inside_an_earlier_tab_stops_the_pull() -> None:
    export = ("# **Alpha**\n\nOne.\n\n## **Sermon One**\n\nInner.\n\n"
              "## **Sermon One**\n\nTwo.\n")
    a = _tab("a.md", [(H1, "Alpha"), (P, "One."), (H2, "Sermon One"),
                      (P, "Inner.")])
    try:
        _split(export, a, _tab("b.md", [(H2, "Sermon One"), (P, "Two.")]))
        check("an opener that is an inner heading of an earlier tab stops",
              False)
    except LookupError as err:
        check("the stop names the earlier tab, the later tab and the heading",
              "'a.md'" in str(err) and "'b.md'" in str(err)
              and "## Sermon One" in str(err) and "retitle" in str(err),
              str(err))
    out = _split(export.replace("## **Sermon One**\n\nTwo.",
                                "## **Sermon One: The Error of Specialness**"
                                "\n\nTwo."),
                 a, _tab("b.md", [(H2, "Sermon One: The Error of Specialness"),
                                  (P, "Two.")]))
    check("a longer heading sharing the words is its own heading",
          out.get("b.md") == ("## **Sermon One: The Error of Specialness**"
                              "\n\nTwo.\n")
          and "Inner." in out.get("a.md", ""), out)
    # The echo the other way round (a later tab holds an earlier opener
    # inside it) is allowed: test_later_echo_of_an_opening_heading_does_not_split.


def test_tab_without_heading_stops_the_pull() -> None:
    export = "# **Alpha**\n\nOne.\n\nJust prose, no heading.\n\nMore.\n"
    try:
        _split(export, _tab("a.md", [(H1, "Alpha"), (P, "One.")]),
               _tab("c.md", [(P, "Just prose, no heading."), (P, "More.")]))
        check("a chapter tab that opens with prose stops the pull", False)
    except LookupError as err:
        check("the stop names the tab and its first line",
              "'c.md'" in str(err)
              and "every tab must open with a heading" in str(err)
              and "Just prose, no heading." in str(err), str(err))
    # AuthorLM's own tabs (the container, the manifest, the illustrations
    # tab and its prompt subtabs) and a scratch tab open with plain text
    # and start at that exact line; an empty tab gets an empty section.
    export = ("Automatically generated by AuthorLM. Do not delete.\n\n"
              "# **Alpha**\n\nOne.\n\nManuscript: book\n\nDoc version: 3\n\n"
              "Illustration prompt descriptions.\n\n"
              "A boy in a plain linen tunic stands on a hilltop.\n\n"
              "Scratch notes.\n")
    out = _split(export,
                 _tab("book", [(P, "Automatically generated by AuthorLM. "
                                   "Do not delete.")]),
                 _tab("a.md", [(H1, "Alpha"), (P, "One.")]),
                 _tab("new.md", []),
                 _tab("manifest", [(P, "Manuscript: book"),
                                   (P, "Doc version: 3")]),
                 _tab(ILLUS_TAB_TITLE,
                      [(P, "Illustration prompt descriptions.")],
                      children=[_tab("a-boy-in-a.md",
                                     [(P, "A boy in a plain linen tunic "
                                          "stands on a hilltop.")])]),
                 _tab("notes", [(P, "Scratch notes.")]))
    check("the container tab starts the export",
          out.get("book") == "Automatically generated by AuthorLM. "
                             "Do not delete.\n", out)
    check("the manifest is cut off the chapter before it",
          out.get("a.md") == "# **Alpha**\n\nOne.\n"
          and out.get("manifest") == "Manuscript: book\n\nDoc version: 3\n",
          out)
    check("a prompt subtab is not a chapter and splits on its first line",
          out.get("a-boy-in-a.md") == "A boy in a plain linen tunic stands "
                                      "on a hilltop.\n", out)
    check("an empty chapter tab gets an empty section and no stop",
          out.get("new.md") == "\n", out)
    check("a scratch tab splits on its first line",
          out.get("notes") == "Scratch notes.\n", out)


def test_holds_rejects_duplicate_line_false_positive() -> None:
    base = "Alpha.\n\nBeta.\n\nGamma.\n"
    doc_add = "Alpha.\n\nBeta.\n\nBeta.\n\nGamma.\n"
    local = "Alpha.\n\nBeta.\n\nGamma.\n\n# my local edit\n"
    check("Doc's second copy of a repeated line is not held by set membership",
          _holds_doc_edits(local, base, doc_add) is False)

    base_dup = "Intro.\n\nShared line.\n\nMiddle.\n\nShared line.\n\nEnd.\n"
    doc_rm = "Intro.\n\nShared line.\n\nMiddle.\n\nEnd.\n"
    check("Doc's deletion of one of two identical lines is not held while both remain",
          _holds_doc_edits(base_dup, base_dup, doc_rm) is False)


def test_holds_settled_adjacent_edit() -> None:
    # Same fixture as test_audio's bind-again settled conflict.
    base = "x\n\na\n\nb\n\nc\n\nd\n"
    doc = "x\n\nA\n\nn1\n\nb\n\nn2\n\nc\n\nd\n"
    settled = "x\n\nA\n\nn1\n\nB\n\nn2\n\nc\n\nd\n"
    lost = "x\n\nA\n\nB\n\nn2\n\nc\n\nd\n"
    check("settled conflict with adjacent local edit holds",
          _holds_doc_edits(settled, base, doc) is True)
    check("dropping a Doc-added line refuses",
          _holds_doc_edits(lost, base, doc) is False)


def test_holds_rejects_doc_only_reorder() -> None:
    base = ("Alpha paragraph.\n\nBeta paragraph.\n\n"
            "Gamma paragraph.\n")
    doc = ("Beta paragraph.\n\nAlpha paragraph.\n\n"
           "Gamma paragraph.\n")
    check("Doc-only paragraph reorder is not held by bag equality",
          _holds_doc_edits(base, base, doc) is False)
    local = base + "\nLocal only.\n"
    check("reorder plus an unrelated local line still refuses",
          _holds_doc_edits(local, base, doc) is False)


if __name__ == "__main__":
    test_dialogue_line_is_not_an_anchor()
    test_epigraph_lands_in_its_own_tab()
    test_later_echo_of_an_opening_heading_does_not_split()
    test_duplicate_opening_heading_stops_the_pull()
    test_opening_heading_inside_an_earlier_tab_stops_the_pull()
    test_tab_without_heading_stops_the_pull()
    test_holds_rejects_duplicate_line_false_positive()
    test_holds_settled_adjacent_edit()
    test_holds_rejects_doc_only_reorder()
    print("ok")
