"""Insertion anchors counted in the same universe `_locate_in_tab` searches.

`_occurrence` defaults to `rendered_text`. An insertion that falls back
through `tab_anchor_text` (heading marker gone, link text only) must
recount with that view — otherwise occurrence 0 for `## Title` / `[url](url)`
makes the locator plant on the first earlier plain-text match of the
stripped needle, and resolve marks the wrong form cleaned.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import gdocs


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_heading_topic_named_earlier() -> None:
    paragraphs = [
        "Why I wrote this book is simple.",
        "## Why I wrote this book",
    ]
    n, anchor = 2, paragraphs[1]
    check("rendered universe still sees the ## marker once",
          gdocs._occurrence(paragraphs, n, anchor) == 0)
    check("tab-anchor universe counts the earlier plain topic name",
          gdocs._occurrence(paragraphs, n, anchor,
                            view=gdocs.tab_anchor_text) == 1,
          gdocs.tab_anchor_text(anchor))


def test_exported_link_after_bare_url() -> None:
    paragraphs = [
        "Preface names https://youtu.be/a?t=1 early.",
        "Body between.",
        "[https://youtu.be/a?t=1](https://youtu.be/a?t=1)",
    ]
    n, anchor = 3, paragraphs[2]
    check("rendered universe counts the markdown link form, not the bare URL",
          gdocs._occurrence(paragraphs, n, anchor) == 0)
    check("tab-anchor universe steps over the bare URL that the tab also holds",
          gdocs._occurrence(paragraphs, n, anchor,
                            view=gdocs.tab_anchor_text) == 1,
          gdocs.tab_anchor_text(anchor))


def _with_tab(tab_body: str, locate):
    """Stub `_tab_runs` + `_locate_in_tab` for a flat single-run tab body."""
    runs = [(1, tab_body)]
    original_runs = gdocs._tab_runs
    original_locate = gdocs._locate_in_tab
    gdocs._tab_runs = lambda *a, **k: runs
    gdocs._locate_in_tab = locate
    return original_runs, original_locate


def test_locate_form_insertion_uses_tab_anchor_occurrence() -> None:
    """A judgment / footnote insert after a heading whose title already
    appeared in prose must locate the heading, not the earlier mention."""
    paragraphs = [
        "Why I wrote this book is simple.",
        "## Why I wrote this book",
        "First body paragraph.",
    ]
    # Tab text runs: no ## marker (importer → heading style).
    tab_body = (
        "Why I wrote this book is simple.\n"
        "Why I wrote this book\n"
        "First body paragraph.\n"
    )
    calls: list[tuple[str, int]] = []

    def capture(docs, master, tab_id, needle, occurrence=0, runs=None):
        calls.append((needle, occurrence))
        # Mimic the tab: raw markdown never present; stripped text is.
        if needle.startswith("#") or "[" in needle:
            return None
        full = tab_body
        offset = -1
        for _ in range(occurrence + 1):
            offset = full.find(needle, offset + 1)
            if offset < 0:
                return None
        return (1 + offset, 1 + offset + len(needle))

    originals = _with_tab(tab_body, capture)
    try:
        reqs, old = gdocs._locate_form(
            {"proposed_old": "", "proposed_new": "[^J1]: note",
             "metadata": '{"anchor_paragraph": 2}'},
            paragraphs, None, "doc", "tab")
    finally:
        gdocs._tab_runs, gdocs._locate_in_tab = originals

    check("raw heading locate misses, then stripped locate uses occurrence 1",
          calls == [("## Why I wrote this book", 0),
                    ("Why I wrote this book", 1)],
          calls)
    check("the insert is an insertion (empty old half)", old == "")
    check("a locate request was produced", bool(reqs))
    # End index of the SECOND "Why I wrote this book" (the heading line).
    first = tab_body.find("Why I wrote this book")
    second = tab_body.find("Why I wrote this book", first + 1)
    expect_end = 1 + second + len("Why I wrote this book")
    insert_at = next(
        (r["insertText"]["location"]["index"] for r in reqs
         if "insertText" in r), None)
    check("form is planted after the heading, not the earlier topic name",
          insert_at == expect_end, (insert_at, expect_end, reqs))


def test_locate_form_link_anchor_skips_earlier_bare_url() -> None:
    paragraphs = [
        "Preface names https://youtu.be/a?t=1 early.",
        "[https://youtu.be/a?t=1](https://youtu.be/a?t=1)",
    ]
    tab_body = (
        "Preface names https://youtu.be/a?t=1 early.\n"
        "https://youtu.be/a?t=1\n"
    )
    calls: list[tuple[str, int]] = []

    def capture(docs, master, tab_id, needle, occurrence=0, runs=None):
        calls.append((needle, occurrence))
        if "[" in needle:
            return None
        full = tab_body
        offset = -1
        for _ in range(occurrence + 1):
            offset = full.find(needle, offset + 1)
            if offset < 0:
                return None
        return (1 + offset, 1 + offset + len(needle))

    originals = _with_tab(tab_body, capture)
    try:
        reqs, _ = gdocs._locate_form(
            {"proposed_old": "", "proposed_new": "after link",
             "metadata": '{"anchor_paragraph": 2}'},
            paragraphs, None, "doc", "tab")
    finally:
        gdocs._tab_runs, gdocs._locate_in_tab = originals

    check("link fallback recounts past the bare URL in the preface",
          calls[-1] == ("https://youtu.be/a?t=1", 1), calls)
    end = 1 + tab_body.rfind("https://youtu.be/a?t=1") + len(
        "https://youtu.be/a?t=1")
    insert_at = next(
        (r["insertText"]["location"]["index"] for r in reqs
         if "insertText" in r), None)
    check("form lands after the link paragraph, not the preface URL",
          insert_at == end, (insert_at, end))


if __name__ == "__main__":
    test_heading_topic_named_earlier()
    test_exported_link_after_bare_url()
    test_locate_form_insertion_uses_tab_anchor_occurrence()
    test_locate_form_link_anchor_skips_earlier_bare_url()
    print("ok")
