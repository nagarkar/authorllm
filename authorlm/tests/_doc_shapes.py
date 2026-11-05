"""The tab shapes that broke the cut of the whole-Doc export into tabs,
one per pull request that mended the old cut (#27, #31, #36, #102, #252,
#262, #309, #317, #322, #337, #357, #361) and issue #155.

`tests/doc_road_live.py` (scenario 6) puts them into the fixture Doc,
records what Google returns for them (the structured read and the
markdown export of one revision) into `tests/doc_cache/6.json.gz`, and
takes them out again. `tests/test_tab_alignment.py` reads that recording.

FILES are manuscript files, pushed through the bridge, so each tab holds
what Google's own markdown import makes of the text. HAND_TABS are made
through the Docs API, as an author makes a tab by hand.
"""
from __future__ import annotations

# More than eighty characters, the length the old prefix match cut at.
SHARED = ("The question is always the same, and it is asked of every "
          "tradition that hardens into habit over the years")
EPIGRAPH = "The idea of death haunts the human animal."

FILES = {
    # A dialogue line that stands alone ("Yes."), and SHARED as a
    # paragraph of its own, both inside the chapter.
    "sh-a.md": ("# **Alpha**\n\nOpening of A.\n\nYes.\n\n"
                f"{SHARED}.\n\nMore A.\n"),
    # Opens with the same short line, no heading (#309, #322, #357).
    "sh-b.md": ("Yes.\n\nReal body of B.\n\n# **Beta**\n\n"
                "B after its heading.\n"),
    # Opens with SHARED and goes on differently (#317, #337).
    "sh-c.md": (f"{SHARED}, and C goes on from there.\n\nBody of C.\n"),
    # An epigraph before the heading (#357, #361).
    "sh-d.md": (f"*{EPIGRAPH}* — Becker\n\n# **Delta**\n\nBody of D.\n\n"
                f"As Becker wrote, {EPIGRAPH[0].lower()}{EPIGRAPH[1:]}\n"),
    # A tag and an epigraph before a heading another tab opens with too.
    "sh-e.md": ("[Omit: all]\n\n*Some things are in our power.*\n\n"
                "# **Alpha**\n\nE opens with the heading A opens with.\n"),
    # Content headings equal to the manuscript's name (the container
    # tab's title, #36), to the manifest tab's title and to the next
    # tab's title (#102).
    "sh-f.md": ("# **doc-road-fixture**\n\nA title page.\n\n"
                "# **manifest**\n\nProse under a heading named like the "
                "manifest tab.\n\n# **sh-g.md**\n\nProse under a heading "
                "named like the next tab.\n"),
    # Lists, a hard break, escaped math, a rule, and a table that ends
    # the tab with an empty row.
    "sh-g.md": ("# **Gamma**\n\n- first bullet\n- second bullet\n\n"
                "1. step one\n2. step two\n\n"
                "A line that breaks\\\nhard in the middle.\n\n"
                "Inline math $x_1 + y_2 = z$ and a price of $5.\n\n"
                "$$ E = mc^2 $$\n\n---\n\nAfter the rule.\n\n"
                "| Term | Says |\n| --- | --- |\n| karma | action |\n"
                "| dharma | order |\n|  |  |\n"),
}

# A small picture Google serves, pasted into two tabs.
PICTURE = ("https://www.gstatic.com/images/branding/googlelogo/1x/"
           "googlelogo_color_92x30dp.png")
FOOTNOTE = "The footnote's own text, typed by hand."

# (title, text typed into the tab, under the container tab at index 0?,
#  style of the tab's last, empty paragraph, where a picture goes)
HAND_TABS = [
    # A tab an author made and never typed in: one empty paragraph.
    ("sh-empty.md", "", True, None, None),
    # A tab that holds empty paragraphs only (issue #155).
    ("sh-blank.md", "\n\n", False, None, None),
    # A scratch tab (#27, #31).
    ("Tab 31", "Scratch notes the author keeps.\n", False, None, None),
    # The Reader-load tab an old push left behind (#252, #262).
    ("manifest.md", "Reader load, as an old push left it.\n", False, None,
     None),
    # Markdown's own marks typed as plain text: how the export escapes
    # an image embed, a link, a list number, a star.
    ("sh-literal.md", "![](_illustrations/lone-0a1b2c3d-01.png)\n"
     "A [bracketed](parenthesized) phrase, x*y*z and 2*3.\n"
     "1. A line that opens like a list item.\n", False, None, None),
    # Opens with a line of marks and ends with an empty heading.
    ("sh-marks.md", "——\nMiddle words of the marks tab.\n", False,
     "HEADING_2", None),
    # Ends with a page break and a pasted picture alone in its paragraph;
    # the next tab opens with one.
    ("sh-picture-a.md", "Words of the tab that ends with a picture.\n",
     False, None, "end"),
    ("sh-picture-b.md", "\nWords of the tab that opens with a picture.\n",
     False, None, "start"),
]

TITLES = list(FILES) + [t[0] for t in HAND_TABS]
