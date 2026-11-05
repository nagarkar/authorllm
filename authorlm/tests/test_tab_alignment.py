"""The cut of the whole-Doc markdown export into tabs.

Google's export of a master Doc is every tab's markdown, one after the
other, with nothing in it that says where a tab ends. `gdocs.align_export`
cuts it by walking the export against every word of every tab, as the
structured read (`documents.get(includeTabsContent=True)`, which carries
tab ids) holds them: a tab ends where its own words end, the next tab
must start there word for word, and a tab the export does not match stops
the caller with a message that names it. Text never moves to another
file.

One test per failure the earlier cuts had (by tab-title heading, by first
paragraph, by opening heading), named for the pull request that mended
it: #27, #31, #36, #102, #252, #262, #309, #317, #322, #337, #357, #361,
and issue #155. Part one reads what Google really returned for those
shapes, recorded from the fixture Doc into tests/doc_cache/6.json.gz
(tests/doc_road_live.py, scenario 6; the shapes are in _doc_shapes.py).
Part two builds each shape by hand, in the form part one shows Google
writes, to reach the cases one Doc cannot hold at once.

Run: python3 tests/test_tab_alignment.py
"""
from __future__ import annotations

import gzip
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import _doc_shapes as SH  # noqa: E402

from authorlm import gdocs  # noqa: E402
from authorlm.gdocs import ILLUS_TAB_TITLE, ExportMismatch  # noqa: E402

CACHE = HERE / "doc_cache"
PASSED = 0


def check(label: str, cond: bool, detail: object = "") -> None:
    global PASSED
    print(f"  [{'ok' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)
    PASSED += 1


# ---------------------------------------------------------------- builders

H1, H2, P = "HEADING_1", "HEADING_2", "NORMAL_TEXT"
TABLE, RULE = "TABLE", "RULE"


def _para(style: str, text: str) -> dict:
    return {"paragraph": {"paragraphStyle": {"namedStyleType": style},
                          "elements": [{"textRun": {"content": text + "\n"}}]}}


def _tab(title: str, paras: list, children=(), tab_id: str = "") -> dict:
    """A tab as `documents.get(includeTabsContent=True)` returns it. Each
    item is (style, text); (TABLE, rows) is a table of cell texts and
    (RULE, "") a horizontal rule."""
    content = []
    for style, text in paras:
        if style == TABLE:
            content.append({"table": {"tableRows": [
                {"tableCells": [{"content": [_para(P, cell)]}
                                for cell in row]} for row in text]}})
        elif style == RULE:
            content.append({"paragraph": {"elements": [
                {"horizontalRule": {}}, {"textRun": {"content": "\n"}}]}})
        else:
            content.append(_para(style, text))
    return {"tabProperties": {"title": title,
                              "tabId": tab_id or f"t.{title}"},
            "documentTab": {"body": {"content": content}},
            "childTabs": list(children)}


def _markdown(tab: dict) -> str:
    """One tab's body in the form Google's export writes (part one checks
    the form against the recording): a heading as `# **Text**`, a
    paragraph as its text, blank lines between."""
    out = []
    for item in tab["documentTab"]["body"]["content"]:
        para = item.get("paragraph")
        if not para:
            continue
        text = "".join(e.get("textRun", {}).get("content", "")
                       for e in para["elements"]).rstrip("\n")
        named = para.get("paragraphStyle", {}).get("namedStyleType", P)
        if not text:
            continue
        out.append("#" * int(named[-1]) + f" **{text}**"
                   if named.startswith("HEADING_") else text)
    return "\n\n".join(out)


def _flat(tabs) -> list[dict]:
    out = []
    for t in tabs:
        out.append(t)
        out.extend(_flat(t.get("childTabs", [])))
    return out


def _export(*tabs: dict) -> str:
    return "\n\n".join(m for m in (_markdown(t) for t in _flat(tabs))
                       if m) + "\n"


def _cut(export: str, *tabs: dict) -> dict[str, str]:
    doc = {"tabs": list(tabs)}
    order = [t["tabProperties"]["title"] for t in _flat(tabs)]
    return gdocs.split_tabbed_export(export, set(order), order=order,
                                     tab_doc=doc)


def _stops(export: str, *tabs: dict) -> str:
    try:
        _cut(export, *tabs)
    except ExportMismatch as err:
        return str(err)
    return ""


def _whole(*tabs: dict) -> dict[str, str]:
    """What each tab's section must be: its own markdown, all of it."""
    return {t["tabProperties"]["title"]: (_markdown(t) or "") + "\n"
            for t in _flat(tabs)}


# ------------------------------------------------- part one: the recording

def _recorded() -> list[tuple[str, dict, str]]:
    """(label, structured read, export) of every recorded snapshot."""
    out = []
    for path in sorted(CACHE.glob("*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            calls = json.load(f)
        by: dict[str, dict] = {}
        for c in calls:
            label = c["scenario"]
            if ":snapshot:" not in label or "response" not in c:
                continue
            slot = by.setdefault(label, {})
            if "/export" in c["uri"]:
                slot["export"] = c["response"]
            elif (isinstance(c["response"], dict) and "tabs" in c["response"]
                  and (c["method"] == "POST" or "fields=" in c["uri"])):
                # The read the bridge makes (MASTER_FIELDS); its long
                # field list goes out as a POST.
                slot["doc"] = c["response"]
        out.extend((label, s["doc"], s["export"]) for label, s in by.items()
                   if "doc" in s and "export" in s)
    return out


FOOTNOTE_SNAPSHOT = "6:snapshot:footnote"


def _words_of(text: str) -> list[str]:
    return [w for line in text.splitlines()
            for w in gdocs._export_line_words(line)]


def test_every_recorded_snapshot_is_cut_tab_by_tab() -> None:
    """Whatever the fixture Doc held at each recorded moment (staged
    forms, struck text, the manifest's hard breaks), the cut gives every
    tab a section, and each section holds exactly its tab's words."""
    snaps = _recorded()
    check("there are recorded snapshots to cut", len(snaps) >= 10,
          "run: python3 tests/doc_road_live.py --record")
    cut_tabs = 0
    for label, doc, export in snaps:
        if label == FOOTNOTE_SNAPSHOT:    # the one the cut stops on
            continue
        whole = gdocs.unescape_export_math(export)
        sections = gdocs.align_export(whole, doc)
        tabs = gdocs.tab_words(doc)
        wrong = [s.title for s, t in zip(sections, tabs)
                 if s.tab_id != t.tab_id
                 or _words_of(s.text) != list(t.words)]
        cut_tabs += len(sections)
        if wrong or len(sections) != len(tabs):
            check(f"[{label}] every section holds its own tab's words",
                  False, wrong)
        back = [line.rstrip() for s in sections
                for line in s.text.splitlines() if line.strip()]
        if back != [line.rstrip() for line in whole.splitlines()
                    if line.strip()]:
            check(f"[{label}] the sections together are the export, with "
                  "no line lost, repeated or moved", False)
    check(f"all {len(snaps) - 1} snapshots cut, {cut_tabs} tab sections, "
          "each holding its own tab's words and no line lost or moved", True)


def _shapes() -> tuple[dict, str, dict[str, str]]:
    for label, doc, export in _recorded():
        if label == "6:snapshot:shapes":
            whole = gdocs.unescape_export_math(export)
            return doc, whole, {s.title: s.text
                                for s in gdocs.align_export(whole, doc)}
    raise AssertionError("no recording of the shapes (scenario 6); run: "
                         "python3 tests/doc_road_live.py --record")


def test_recorded_shapes() -> None:
    doc, whole, cut = _shapes()
    check("every shape tab has a section",
          all(t in cut for t in SH.TITLES), sorted(cut))

    # The hand-built form of part two is the form Google writes.
    a = SH.FILES["sh-a.md"]
    check("a heading exports as `# **Text**` and paragraphs stand apart "
          "by one blank line (the form part two builds by hand)",
          cut["sh-a.md"] == a, cut["sh-a.md"])

    check("#309 #322: the dialogue line 'Yes.' stays in the chapter that "
          "holds it, and the tab that opens with it starts there",
          "Yes.\n\n" + SH.SHARED in cut["sh-a.md"]
          and cut["sh-a.md"].rstrip().endswith("More A.")
          and cut["sh-b.md"].startswith("Yes.\n\nReal body of B."),
          cut["sh-a.md"] + "---" + cut["sh-b.md"])
    check("#357: a chapter tab that opens with plain text is cut like any "
          "other", cut["sh-b.md"].rstrip().endswith("B after its heading."),
          cut["sh-b.md"])
    check("#317 #337: a tab that opens with a long line an earlier tab "
          "also holds starts at its own copy",
          cut["sh-c.md"].startswith(SH.SHARED + ", and C goes on")
          and cut["sh-c.md"].rstrip().endswith("Body of C."), cut["sh-c.md"])
    check("#357 #361: an epigraph before the heading opens its own tab, "
          "and the tab before ends before it",
          cut["sh-d.md"].startswith("*" + SH.EPIGRAPH + "*")
          and "Becker" not in cut["sh-c.md"]
          and cut["sh-d.md"].rstrip().endswith(
              SH.EPIGRAPH[0].lower() + SH.EPIGRAPH[1:]), cut["sh-d.md"])
    check("#357: a tag and an epigraph before a heading two tabs share",
          cut["sh-e.md"].lstrip("\\").startswith("[Omit: all")
          and "# **Alpha**" in cut["sh-e.md"]
          and cut["sh-e.md"].rstrip().endswith("A opens with."),
          cut["sh-e.md"])
    check("#36 #102: content headings named like the manuscript, the "
          "manifest tab and the next tab stay inside their chapter",
          cut["sh-f.md"].startswith("# **doc-road-fixture**")
          and "# **manifest**" in cut["sh-f.md"]
          and "# **sh-g.md**" in cut["sh-f.md"].replace("\\", "")
          and cut["sh-f.md"].rstrip().endswith("like the next tab."),
          cut["sh-f.md"])
    g = cut["sh-g.md"]
    check("lists, a hard break, escaped math, a rule and a table are one "
          "tab's section, first line to last",
          g.startswith("# **Gamma**") and "first bullet" in g
          and "step two" in g and "hard in the middle." in g
          and "x_1 + y_2 = z" in g and "E = mc^2" in g
          and "After the rule." in g and "| dharma | order |" in g, g)
    check("...and the table's empty last row stays with its table",
          g.rstrip().splitlines()[-1].startswith("|")
          and not _words_of(g.rstrip().splitlines()[-1]), g[-200:])
    check("#155: a tab never typed in and a tab of empty paragraphs get "
          "empty sections",
          cut["sh-empty.md"] == "\n" and cut["sh-blank.md"] == "\n",
          (cut["sh-empty.md"], cut["sh-blank.md"]))
    check("#27 #31: a hand-made scratch tab is its own section",
          cut["Tab 31"] == "Scratch notes the author keeps.\n",
          cut["Tab 31"])
    check("#252 #262: a leftover manifest.md tab is its own section, and "
          "the manifest tab keeps its own text",
          cut["manifest.md"] == "Reader load, as an old push left it.\n"
          and "Reader load, as an old push" not in cut["manifest"]
          and cut["manifest"].startswith("Manuscript:"),
          cut["manifest.md"] + cut["manifest"][:80])
    lit = cut["sh-literal.md"]
    check("markdown's own marks typed as text come back escaped, and the "
          "tab that holds them is cut whole",
          "\\[" in lit and "illustrations" in lit
          and lit.rstrip().endswith("opens like a list item."), lit)
    marks = cut["sh-marks.md"].rstrip().splitlines()
    check("a tab that opens with a line of marks and ends with an empty "
          "heading keeps both",
          marks[0].strip() == "——" and marks[-1].strip() == "##"
          and "Middle words of the marks tab." in marks, marks)
    pa, pb = cut["sh-picture-a.md"], cut["sh-picture-b.md"]
    check("a tab that ends with a page break and a pasted picture keeps "
          "the picture's line, and the tab that opens with one starts at "
          "its own (Google leaves no blank line between the two)",
          pa.rstrip().splitlines()[-1].strip() == "![][image1]"
          and pa.startswith("Words of the tab that ends with a picture.")
          and pb.startswith("![][image2]")
          and "Words of the tab that opens with a picture." in pb
          and "![][image1]  \n![][image2]" in whole, pa + "---" + pb[:120])
    check("no shape tab's text is in any other section",
          not any("Scratch notes" in s for t, s in cut.items()
                  if t != "Tab 31")
          and sum("Yes." in s for s in cut.values()) == 2,
          [t for t, s in cut.items() if "Yes." in s])


def test_recorded_footnote_stops_the_cut() -> None:
    """A footnote made with Insert > Footnote (the bridge writes its own
    as text): Google puts its text after the last tab, where the earlier
    cuts gave it to whatever tab came last."""
    for label, doc, export in _recorded():
        if label == FOOTNOTE_SNAPSHOT:
            break
    else:
        raise AssertionError("no recording of the footnote (scenario 6); "
                             "run: python3 tests/doc_road_live.py --record")
    whole = gdocs.unescape_export_math(export)
    check("the export writes the footnote's text after every tab's",
          whole.rstrip().splitlines()[-1].startswith("[image")
          and f"[^1]: {SH.FOOTNOTE}" in " ".join(whole.split()), whole[-400:])
    try:
        gdocs.align_export(whole, doc)
        msg = ""
    except ExportMismatch as err:
        msg = str(err)
    check("text in no tab stops the cut, and the message says it is a "
          "footnote and what to do",
          "in no tab" in msg and SH.FOOTNOTE in msg
          and "Insert > Footnote" in msg and "[^X]" in msg, msg)


# ------------------------------------------- part two: one test per failure

def test_27_a_hand_made_tab_after_a_prompt_tab() -> None:
    # reconcile split at mapped files only: "Tab 31" was appended to the
    # prompt file before it, which changed the description's hash.
    tabs = [_tab("book", [(P, "Automatically generated by AuthorLM. "
                              "Do not delete.")]),
            _tab("a.md", [(H1, "Alpha"), (P, "One.")]),
            _tab(ILLUS_TAB_TITLE, [(P, "Illustration prompt descriptions.")],
                 children=[_tab("a-boy-in-a.md", [
                     (P, "A boy in a plain linen tunic stands on a "
                         "hilltop.")])]),
            _tab("Tab 31", [(P, "Scratch notes.")])]
    out = _cut(_export(*tabs), *tabs)
    check("every tab is a boundary: the prompt file before a hand-made "
          "tab is untouched", out == _whole(*tabs), out)


def test_31_trailing_tabs_do_not_enter_the_essay() -> None:
    # diff_push's section for an essay swallowed the tabs after it, and
    # its paragraph count no longer matched the tab's.
    tabs = [_tab("a.md", [(H1, "Alpha"), (P, "Body of the essay.")]),
            _tab("manifest", [(P, "Manuscript: book"),
                              (P, "Doc version: 3")]),
            _tab("Tab 31", [(H1, "Scratch"), (P, "Scratch notes.")])]
    out = _cut(_export(*tabs), *tabs)
    check("the essay's section is the essay's paragraphs and no more",
          out["a.md"] == "# **Alpha**\n\nBody of the essay.\n"
          and gdocs._md_paragraphs(out["a.md"]) == ["# **Alpha**",
                                                    "Body of the essay.\n"],
          out["a.md"])
    check("...and the tabs after it keep their own", out == _whole(*tabs),
          out)


def test_36_a_content_h1_equal_to_the_manuscript_name() -> None:
    # The container tab is titled the manuscript's name; a title page
    # `# **Book Title**` reset the section and emptied the essay.
    tabs = [_tab("Book Title", [(P, "Automatically generated by AuthorLM. "
                                    "Do not delete.")]),
            _tab("title.md", [(H1, "Book Title"), (P, "By the author.")]),
            _tab("one.md", [(H1, "Book Title"), (P, "A chapter that opens "
                                                    "with the name too.")])]
    out = _cut(_export(*tabs), *tabs)
    check("a title page named like the manuscript keeps its text",
          out == _whole(*tabs), out)
    # The same Doc in the form Google wrote until 2026-10-26, a title
    # heading before every tab: the content H1 is byte-identical to the
    # container's title heading.
    titled = "".join(f"# **{t['tabProperties']['title']}**\n\n"
                     f"{_markdown(t)}\n\n" for t in tabs)
    out = _cut(titled, *tabs)
    check("...also in an export that carries tab-title headings",
          out == _whole(*tabs), out)
    # No container text at all (an empty first tab named like the H1).
    tabs[0] = _tab("Book Title", [])
    out = _cut(_export(*tabs), *tabs)
    check("...and when the tab named like it holds nothing",
          out == _whole(*tabs), out)


def test_102_a_content_heading_named_like_the_next_tab() -> None:
    # `# **manifest**` inside the essay just before the manifest tab
    # ended the essay early; the next tab took its tail.
    tabs = [_tab("a.md", [(H1, "Alpha"), (P, "Intro."), (H1, "manifest"),
                          (P, "Prose after the collision.")]),
            _tab("manifest", [(P, "Manuscript: book"),
                              (P, "Real inventory.")]),
            _tab("b.md", [(H1, "manifest"), (P, "B opens with it too.")])]
    out = _cut(_export(*tabs), *tabs)
    check("the essay keeps its tail and the next tab its own text",
          out == _whole(*tabs), out)
    titled = "".join(f"# **{t['tabProperties']['title']}**\n\n"
                     f"{_markdown(t)}\n\n" for t in tabs)
    check("...also in an export that carries tab-title headings",
          _cut(titled, *tabs) == _whole(*tabs), _cut(titled, *tabs))


def test_252_262_a_leftover_manifest_md_tab() -> None:
    # The Reader-load report an old push left as a tab titled
    # `manifest.md`: it is a tab like any other to the cut, so its text
    # reaches neither the chapter before it nor the manifest after.
    tabs = [_tab("a.md", [(H1, "Alpha"), (P, "One.")]),
            _tab("manifest.md", [(P, "Reader load — for each essay, what "
                                     "it assumes."), (P, "a.md — debt 0")]),
            _tab("manifest", [(P, "Manuscript: book"),
                              (P, "a.md — 51 words")])]
    out = _cut(_export(*tabs), *tabs)
    check("the leftover tab is its own section", out == _whole(*tabs), out)


def test_309_a_short_opener_is_no_prefix_of_a_longer_line() -> None:
    # `startswith(want[:80])` with no floor: "Yes." matched the earlier
    # line "Yes. This is still chapter A." and took A's tail.
    tabs = [_tab("a.md", [(H1, "Alpha"), (P, "Opening of A."),
                          (P, "Yes. This is still chapter A."),
                          (P, "More A.")]),
            _tab("b.md", [(P, "Yes."), (P, "Real body of B.")])]
    out = _cut(_export(*tabs), *tabs)
    check("a keeps its line and all after it; b starts at its own",
          out == _whole(*tabs), out)


def test_317_two_chapters_share_an_80_character_opening() -> None:
    shared = ("In the beginning there was only the question, and the "
              "question was asked of everyone who came")
    assert len(shared) > 80
    tabs = [_tab("a.md", [(H1, "Alpha"), (P, shared + ", said the first."),
                          (P, "The rest of A.")]),
            _tab("b.md", [(P, shared + ", said the second."),
                          (P, "The rest of B.")])]
    out = _cut(_export(*tabs), *tabs)
    check("b starts at its own opening, not at A's line that shares its "
          "first eighty characters", out == _whole(*tabs), out)


def test_322_a_duplicate_dialogue_line() -> None:
    # Exact equality on a short line: b's opener "Yes." stands alone
    # earlier in a, and also later in b and in c.
    tabs = [_tab("a.md", [(H1, "Alpha"), (P, "Opening of A."), (P, "Yes."),
                          (P, "More A.")]),
            _tab("b.md", [(P, "Yes."), (P, "Real body of B."), (P, "Yes.")]),
            _tab("c.md", [(P, "Yes."), (P, "Yes."), (P, "Body of C.")])]
    out = _cut(_export(*tabs), *tabs)
    check("every 'Yes.' stays in the tab that holds it",
          out == _whole(*tabs), out)


def test_337_a_long_opener_restated_in_the_chapter_before() -> None:
    thesis = ("Momentum is what a tradition becomes when nobody asks "
              "what it was for")
    tabs = [_tab("a.md", [(H1, "Alpha"), (P, thesis + "."),
                          (P, thesis + ", and A says it twice."),
                          (P, "End of A.")]),
            _tab("b.md", [(P, thesis + "."), (P, "Body of B.")])]
    out = _cut(_export(*tabs), *tabs)
    check("a long opener that the chapter before states twice is no "
          "boundary there", out == _whole(*tabs), out)
    # The uniqueness gate of #322 and #337 dropped a tab whose opener is
    # echoed later in the book.
    tabs.append(_tab("c.md", [(H1, "Gamma"), (P, thesis + "."),
                              (P, "End of C.")]))
    out = _cut(_export(*tabs), *tabs)
    check("...and an echo later in the book drops no tab",
          out == _whole(*tabs), out)


def test_357_shapes_the_heading_gate_stopped() -> None:
    # The heading anchor needed every chapter to open with a heading that
    # no other tab opens with and no earlier tab holds inside; a pull
    # stopped otherwise. A walk of the whole content needs none of that.
    tabs = [_tab("a.md", [(H1, "Alpha"), (P, "One."), (H2, "Sermon One"),
                          (P, "Inner.")]),
            _tab("b.md", [(H2, "Sermon One"), (P, "Two.")]),
            _tab("c.md", [(H1, "Alpha"), (P, "Three.")]),
            _tab("d.md", [(P, "Just prose, no heading."), (P, "More.")]),
            _tab("e.md", [(H1, "Epsilon"), (P, "Speaking of Alpha."),
                          (H1, "Alpha"), (P, "A later echo.")])]
    out = _cut(_export(*tabs), *tabs)
    check("two tabs with one opening heading, an opener that is an inner "
          "heading of an earlier tab, a chapter that opens with prose and "
          "a later echo are all cut where the tabs end",
          out == _whole(*tabs), out)


def test_361_an_epigraph_before_the_heading() -> None:
    epi = "The idea of death haunts the human animal. — Becker"
    a = _tab("a.md", [(H1, "Alpha"), (P, "Body of A."),
                      (P, "Last line of A.")])
    b = _tab("b.md", [(P, epi), (H1, "Beta"), (P, "Body of B.")])
    c = _tab("c.md", [(P, "[Omit: all]"),
                      (P, "Some things are in our power."), (H1, "Gamma"),
                      (P, "Body of C.")])
    export = ("# **Alpha**\n\nBody of A.\n\nLast line of A.\n\n"
              "*The idea of death haunts the human animal.* — Becker\n\n"
              "# **Beta**\n\nBody of B.\n\n"
              "\\[Omit: all\\]\n\n*Some things are in our power.*\n\n"
              "# **Gamma**\n\nBody of C.\n")
    out = _cut(export, a, b, c)
    check("an epigraph, and a tag with one, open their own tab's file",
          out["a.md"] == "# **Alpha**\n\nBody of A.\n\nLast line of A.\n"
          and out["b.md"].startswith("*The idea of death")
          and out["b.md"].endswith("Body of B.\n")
          and out["c.md"].startswith("\\[Omit: all\\]\n\n*Some things"),
          out)
    # The export soft-break-joins A's last line and b's epigraph (no
    # blank line between): substring containment stole A's line into b.
    joined = export.replace("Last line of A.\n\n*The", "Last line of A.\n*The")
    out = _cut(joined, a, b, c)
    check("a soft-break join of the prior line and the epigraph is cut "
          "between the two lines",
          out["a.md"].endswith("Last line of A.\n")
          and out["b.md"].startswith("*The idea of death"), out)
    # The chapter before paraphrases the epigraph.
    a2 = _tab("a.md", [(H1, "Alpha"), (P, "As Becker wrote, the idea of "
                                          "death haunts the human animal.")])
    b2 = _tab("b.md", [(P, "the idea of death haunts the human animal."),
                       (H1, "Beta"), (P, "Body of B.")])
    out = _cut(_export(a2, b2), a2, b2)
    check("a prior line that contains the epigraph is not taken with it",
          out == _whole(a2, b2), out)
    # The tab holds an epigraph the export does not: the old cut went on
    # without it; nothing says the export is the same Doc, so this stops.
    lost = "# **Alpha**\n\nLast line of A.\n\n# **Beta**\n\nBody of B.\n"
    msg = _stops(lost, _tab("a.md", [(H1, "Alpha"), (P, "Last line of A.")]),
                 _tab("b.md", [(P, "An epigraph the export lost."),
                               (H1, "Beta"), (P, "Body of B.")]))
    check("a tab whose opening the export does not hold stops the cut, "
          "naming the tab and showing both sides",
          "tab 'b.md'" in msg and "An epigraph the export lost" in msg
          and "Beta Body of B" in msg and "after tab 'a.md'" in msg, msg)


def test_155_tabs_that_hold_nothing() -> None:
    tabs = [_tab("book", []),
            _tab("a.md", [(H1, "Alpha"), (P, "One.")]),
            _tab("new.md", []),
            _tab("about.md", [(P, ""), (P, ""), (P, "")]),
            _tab("b.md", [(H1, "Beta"), (P, "Two.")]),
            _tab("last.md", [])]
    out = _cut(_export(*tabs), *tabs)
    check("a tab with no paragraphs and a tab of empty ones get empty "
          "sections; their neighbours keep their text",
          out == {"book": "\n", "a.md": "# **Alpha**\n\nOne.\n",
                  "new.md": "\n", "about.md": "\n",
                  "b.md": "# **Beta**\n\nTwo.\n", "last.md": "\n"}, out)
    empty = [_tab("book", []), _tab("a.md", [])]
    check("a Doc that holds nothing is cut into empty sections",
          _cut("\n", *empty) == {"book": "\n", "a.md": "\n"},
          _cut("\n", *empty))


def test_tables_lists_hard_breaks_and_math() -> None:
    a = _tab("a.md", [(H1, "Alpha"), (P, "first bullet"),
                      (P, "second bullet"), (P, "step one"), (P, "step two"),
                      (P, "A line that breaks\x0bhard in the middle."),
                      (P, "Inline math $x_1 + y_2 = z$ and a price of $5."),
                      (RULE, ""),
                      (TABLE, [["Term", "Says"], ["karma", "action"],
                               ["", ""]])])
    b = _tab("b.md", [(TABLE, [["", ""], ["x", "y"]]), (P, "1. Not a list: "
                                                            "typed so.")])
    export = ("# **Alpha**\n\n* first bullet\n* second bullet\n\n"
              "1. step one\n2. step two\n\n"
              "A line that breaks  \nhard in the middle.\n\n"
              "Inline math $x\\_1 \\+ y\\_2 \\= z$ and a price of $5.\n\n"
              "---\n\n"
              "| Term | Says |\n| :---- | :---- |\n| karma | action |\n"
              "|  |  |\n\n"
              "|  |  |\n| :---- | :---- |\n| x | y |\n\n"
              "1\\. Not a list: typed so.\n")
    out = _cut(export, a, b)
    check("a tab ends after its table's empty last row, and the next "
          "starts at its own table's empty first row",
          out["a.md"].endswith("| karma | action |\n|  |  |\n")
          and out["b.md"].startswith("|  |  |\n| :---- | :---- |\n| x | y |")
          and out["b.md"].endswith("typed so.\n"), out)
    check("list numbers, bullets, escapes and a rule are not words the "
          "tab lacks", out["a.md"].startswith("# **Alpha**\n\n* first")
          and "---" in out["a.md"], out["a.md"])
    # Emphasis marks may sit inside a word: the tab holds "xp1", the
    # export writes it as x*p1*.
    c = _tab("c.md", [(P, "def L(): return (p1.xp1.vy-p1.yp1.vx)")])
    out = _cut("def L(): return (p1.x*p1.vy-p1.y*p1.vx)\n", c)
    check("emphasis marks inside a word do not split it",
          out["c.md"] == "def L(): return (p1.x*p1.vy-p1.y*p1.vx)\n", out)
    # A pasted picture alone in its paragraph is a line of the export; a
    # page break is none (recorded: sh-picture-a.md, sh-picture-b.md).
    def pic() -> dict:
        return {"paragraph": {"elements": [
            {"inlineObjectElement": {"inlineObjectId": "kix.1"}},
            {"textRun": {"content": "\n"}}]}}
    f = _tab("f.md", [(P, "Words before the picture.")])
    f["documentTab"]["body"]["content"] += [
        {"paragraph": {"elements": [{"pageBreak": {}},
                                    {"textRun": {"content": "\n"}}]}}, pic()]
    g = _tab("g.md", [(P, "Words after the picture.")])
    g["documentTab"]["body"]["content"].insert(0, pic())
    out = _cut("Words before the picture.\n\n![][image1]  \n![][image2]  \n"
               "Words after the picture.\n\n"
               "[image1]: <data:image/png;base64,iVBORw0KGgo=>\n", f, g)
    check("each picture's line goes with the tab that holds the picture",
          out["f.md"] == "Words before the picture.\n\n![][image1]\n"
          and out["g.md"].startswith("![][image2]  \nWords after the "
                                     "picture."), out)
    # A link's target and a pasted image's reference and data.
    d = _tab("d.md", [(P, "See the source for it."), (P, "After.")])
    e = _tab("e.md", [(P, "Next tab.")])
    out = _cut("See [the source](https://example.com/a_(b)?t=4) for it."
               "![][image1]\n\nAfter.\n\nNext tab.\n\n"
               "[image1]: <data:image/png;base64,iVBORw0KGgo=>\n", d, e)
    check("a link's target and an image's reference and data are not "
          "words the tab lacks",
          out["d.md"].endswith("After.\n")
          and out["e.md"].startswith("Next tab."), out)


def test_a_difference_inside_a_tab_does_not_move_the_cut() -> None:
    body = [(P, f"Sentence number {n} of the chapter goes on a while.")
            for n in range(12)]
    a = _tab("a.md", [(H1, "Alpha")] + body)
    b = _tab("b.md", [(H1, "Beta"), (P, "Body of B stands here, whole and "
                                        "long enough to be walked.")])
    export = _export(a, b)
    more = export.replace("Sentence number 5 of",
                          "Sentence number 5 (see a chip the read does "
                          "not show) of")
    out = _cut(more, a, b)
    check("words the export adds inside a tab stay in that tab",
          "a chip the read does not show" in out["a.md"]
          and out["b.md"] == _whole(a, b)["b.md"], out)
    fewer = export.replace("Sentence number 6 of the chapter goes on a "
                           "while.\n\n", "")
    out = _cut(fewer, a, b)
    check("a paragraph the export drops inside a tab moves no boundary "
          "(the push's own paragraph check then names it)",
          "number 6" not in out["a.md"] and "number 7" in out["a.md"]
          and out["b.md"] == _whole(a, b)["b.md"], out)


def test_a_mismatch_stops_and_names_the_tab() -> None:
    a = _tab("a.md", [(H1, "Alpha"), (P, "Body of A.")])
    b = _tab("b.md", [(H1, "Beta"), (P, "Body of B.")])
    export = _export(a, b)
    msg = _stops(export.replace("Body of B.", "Another body, edited since."),
                 a, b)
    check("a tab whose words changed since the read stops the cut",
          "tab 'b.md'" in msg and "Beta Body of B" in msg
          and "edited since" in msg and "run the command again" in msg, msg)
    msg = _stops(export + "\nA paragraph in no tab.\n", a, b)
    check("text after the last tab's stops the cut: it is in no tab",
          "in no tab" in msg and "after tab 'b.md'" in msg
          and "A paragraph in no tab" in msg, msg)
    msg = _stops(export.replace("# **Beta**", "Stray line.\n\n# **Beta**"),
                 a, b)
    check("text between two tabs stops the cut, naming the tab it is "
          "not the start of",
          "tab 'b.md'" in msg and "Stray line" in msg, msg)
    msg = _stops("# **Alpha**\n\nBody of A.\n", a, b)
    check("an export that ends before a tab's text stops the cut",
          "tab 'b.md'" in msg and "its end" in msg, msg)
    long_a = _tab("a.md", [(H1, "Alpha")] + [
        (P, f"Sentence number {n} of the chapter goes on a while.")
        for n in range(6)])
    msg = _stops(_export(long_a, b).replace(
        "Sentence number 5 of the chapter goes on a while.",
        "Sentence number 5 ends otherwise."), long_a, b)
    check("a tab whose last words differ stops the cut: nothing says "
          "where it ends",
          "tab 'a.md'" in msg and "ends otherwise" in msg, msg)
    marks = export.replace("# **Beta**", "|  |  |\n\n# **Beta**")
    msg = _stops(marks, a, b)
    check("a line of table marks alone between two tabs stops the cut",
          "tab 'b.md'" in msg and "holds no word" in msg
          and "between tab 'a.md' and this tab" in msg, msg)
    check("a stop is a LookupError, as the callers expect",
          issubclass(ExportMismatch, LookupError))


def test_sections_carry_tab_ids() -> None:
    twin1 = _tab("a.md", [(H1, "Alpha"), (P, "First copy.")], tab_id="t.1")
    twin2 = _tab("a.md", [(H1, "Alpha"), (P, "Second copy.")], tab_id="t.2")
    got = gdocs.align_export(_export(twin1, twin2), {"tabs": [twin1, twin2]})
    check("two tabs with one title are two sections, each under its own "
          "tab id",
          [(s.tab_id, s.text) for s in got]
          == [("t.1", "# **Alpha**\n\nFirst copy.\n"),
              ("t.2", "# **Alpha**\n\nSecond copy.\n")], got)


def test_no_text_moves_between_files() -> None:
    """Tabs built from a small stock of lines, so every line recurs
    across tabs, cut two thousand times: each section is its own tab's
    text, whole."""
    rng = random.Random(366)
    stock = ["Yes.", "No.", "Alpha", "Beta", "The question is always the "
             "same.", "Yes. And then more.", "manifest", "a.md", "One.",
             "The question is always the same, said the other.", "——"]
    for _ in range(2000):
        tabs = []
        for n in range(rng.randint(1, 6)):
            paras = [(rng.choice([P, P, P, H1, H2]), rng.choice(stock))
                     for _ in range(rng.randint(0, 7))]
            tabs.append(_tab(rng.choice(["a.md", "b.md", "manifest",
                                         f"t{n}.md"]) if n else "book",
                             paras, tab_id=f"t.{n}"))
        got = gdocs.align_export(_export(*tabs), {"tabs": tabs})
        want = [(_markdown(t) or "") + "\n" for t in tabs]
        if [s.text for s in got] != want:
            check("every section is its own tab's text", False,
                  (_export(*tabs), [s.text for s in got]))
    check("2000 Docs of recurring lines: every section is its own tab's "
          "text", True)


def main() -> None:
    tests = [
        test_every_recorded_snapshot_is_cut_tab_by_tab,
        test_recorded_shapes,
        test_recorded_footnote_stops_the_cut,
        test_27_a_hand_made_tab_after_a_prompt_tab,
        test_31_trailing_tabs_do_not_enter_the_essay,
        test_36_a_content_h1_equal_to_the_manuscript_name,
        test_102_a_content_heading_named_like_the_next_tab,
        test_252_262_a_leftover_manifest_md_tab,
        test_309_a_short_opener_is_no_prefix_of_a_longer_line,
        test_317_two_chapters_share_an_80_character_opening,
        test_322_a_duplicate_dialogue_line,
        test_337_a_long_opener_restated_in_the_chapter_before,
        test_357_shapes_the_heading_gate_stopped,
        test_361_an_epigraph_before_the_heading,
        test_155_tabs_that_hold_nothing,
        test_tables_lists_hard_breaks_and_math,
        test_a_difference_inside_a_tab_does_not_move_the_cut,
        test_a_mismatch_stops_and_names_the_tab,
        test_sections_carry_tab_ids,
        test_no_text_moves_between_files,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print(f"all checks passed ({PASSED})")


if __name__ == "__main__":
    main()
