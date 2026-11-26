"""`docx_style._ppr_insert` / `_fonts` / `format_styles` mutate styles.xml
for `export docx --format` (#469).

`test_export_docx_format.py` needs pandoc and hits the happy path through
a full zip. These pins are the pure OOXML edges a wrong edit silently
corrupts: existing `<w:pPr>`, self-closing `<w:pPr/>`, a style with only
`<w:name/>`, and font attribute escaping. Hermetic — no pandoc, no
workspace, no model.
Run: python3 tests/test_docx_style_helpers.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import docx_style  # noqa: E402
from authorlm.docx_style import _fonts, _ppr_insert  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_ppr_insert_into_existing() -> None:
    block = '<w:style w:styleId="BodyText"><w:pPr><w:spacing w:after="0"/>' \
            "</w:pPr></w:style>"
    got = _ppr_insert(block, '<w:ind w:firstLine="720"/>')
    check("xml is prepended inside an open w:pPr",
          '<w:pPr><w:ind w:firstLine="720"/><w:spacing w:after="0"/>'
          in got, got)
    check("the rest of the style is kept",
          got.endswith("</w:style>") and "BodyText" in got, got)


def test_ppr_insert_expands_self_closing() -> None:
    block = '<w:style w:styleId="X"><w:name w:val="X"/><w:pPr/></w:style>'
    got = _ppr_insert(block, "<w:pageBreakBefore/>")
    check("self-closing w:pPr becomes an element holding the xml",
          "<w:pPr><w:pageBreakBefore/></w:pPr>" in got
          and "<w:pPr/>" not in got, got)


def test_ppr_insert_after_name_when_no_ppr() -> None:
    block = '<w:style w:styleId="Title"><w:name w:val="Title"/></w:style>'
    got = _ppr_insert(block, '<w:jc w:val="center"/>')
    check("when there is no pPr, one is created after w:name",
          '<w:name w:val="Title"/><w:pPr><w:jc w:val="center"/></w:pPr>'
          in got, got)


def test_fonts_quotes_special_characters() -> None:
    # quoteattr switches to single quotes when the value holds ", so the
    # inner quotes stay literal — every rFonts slot must still carry them.
    got = _fonts('Times "New" Roman')
    check("quoteattr puts the name on every rFonts slot",
          got.count("Times \"New\" Roman") == 4
          and "w:ascii=" in got and "w:hAnsi=" in got
          and "w:eastAsia=" in got and "w:cs=" in got, got)
    amp = _fonts("A & B")
    check("ampersand is escaped as an entity",
          "A &amp; B" in amp and amp.count("A &amp; B") == 4, amp)
    plain = _fonts("Garamond")
    check("a plain name is quoted once per slot",
          plain.count('w:ascii="Garamond"') == 1
          and plain.startswith("<w:rFonts "), plain)


def test_format_styles_body_indent_and_footnote_spacing() -> None:
    styles = (
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:styleId="BodyText">'
        '<w:name w:val="Body Text"/>'
        '<w:pPr><w:ind w:left="0"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="FootnoteText">'
        '<w:name w:val="Footnote Text"/>'
        '<w:pPr><w:spacing w:line="480" w:lineRule="auto"/></w:pPr>'
        "</w:style>"
        '<w:style w:type="paragraph" w:styleId="Heading1">'
        '<w:name w:val="heading 1"/>'
        '<w:pPr><w:jc w:val="left"/></w:pPr></w:style>'
        "</w:styles>")
    got = docx_style.format_styles(
        styles, {"font": "Garamond", "size": 12.0, "line-spacing": 2.0})
    body = got[got.index('w:styleId="BodyText"'):]
    body = body[:body.index("</w:style>")]
    check("BodyText drops the old indent and gets a first-line indent",
          'w:firstLine="720"' in body and 'w:ind w:left="0"' not in body,
          body)
    fn = got[got.index('w:styleId="FootnoteText"'):]
    fn = fn[:fn.index("</w:style>")]
    check("footnotes are forced to single spacing after 120",
          'w:line="240"' in fn and 'w:after="120"' in fn
          and 'w:line="480"' not in fn, fn)
    h1 = got[got.index('w:styleId="Heading1"'):]
    h1 = h1[:h1.index("</w:style>")]
    check("Heading1 gets a page break and centered jc via _ppr_insert",
          "<w:pageBreakBefore/>" in h1 and 'w:val="center"' in h1, h1)
    check("docDefaults carry the body font and half-point size",
          'w:ascii="Garamond"' in got and '<w:sz w:val="24"/>' in got, got)


def main() -> None:
    for test in (test_ppr_insert_into_existing,
                 test_ppr_insert_expands_self_closing,
                 test_ppr_insert_after_name_when_no_ppr,
                 test_fonts_quotes_special_characters,
                 test_format_styles_body_indent_and_footnote_spacing):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
