"""Hermetic tests for tools/pdf2md.py — PDF→Markdown cleanup + CLI gates.

Covers the pure layout/cleanup pipeline and CLI refusals without Vision OCR.
OCR itself is macOS-only; sparse/force-OCR pages are exercised via a stub.

Run: python3 tests/test_pdf2md.py
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

# Offline suite pins — same contract as the other tool suites.
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"


def _assert_offline() -> None:
    import os as _os

    from authorlm import paths as _paths
    from authorlm.llm import VENDOR_KEY_ENV

    assert not _paths.config_path().exists(), (
        f"test isolation broken: reading the real config at "
        f"{_paths.config_path()}")
    assert _paths.load_env() == [], "test isolation broken: .env was loaded"
    leaked = [v for v in sorted(VENDOR_KEY_ENV.values()) if _os.environ.get(v)]
    assert not leaked, f"test isolation broken: vendor keys in env: {leaked}"


import importlib.util  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASSED = 0
PDF2MD_PATH = Path(__file__).resolve().parents[1] / "tools" / "pdf2md.py"


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


def load_pdf2md():
    try:
        import pymupdf  # noqa: F401
    except ImportError as err:
        raise SystemExit(
            "pymupdf required for test_pdf2md.py — pip install pymupdf"
        ) from err
    spec = importlib.util.spec_from_file_location("pdf2md", PDF2MD_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _line(text: str, y_top: float, height: float = 0.02, x0: float = 0.1) -> dict:
    return {"text": text, "x0": x0, "y_top": y_top, "height": height}


def check_parse_pages(m) -> None:
    check("parse_pages accepts a single page", m.parse_pages("7") == (7, 7))
    check("parse_pages accepts an inclusive range",
          m.parse_pages("12-40") == (12, 40))
    try:
        m.parse_pages("12-")
        bad = False
    except argparse.ArgumentTypeError as err:
        bad = "expected N or N-M" in str(err)
    check("parse_pages refuses a trailing dash", bad)
    try:
        m.parse_pages("abc")
        bad = False
    except argparse.ArgumentTypeError:
        bad = True
    check("parse_pages refuses non-numeric input", bad)


def check_join_and_continues(m) -> None:
    check("join de-hyphenates when the next line continues lowercase",
          m.join("philo-", "sophy") == "philosophy")
    check("join keeps a space when the next line is not a hyphen continuation",
          m.join("ends.", "Next") == "ends. Next")
    check("join seeds an empty prefix with the addition alone",
          m.join("", "Alone") == "Alone")
    check("continues is true for mid-sentence page-final prose",
          m.continues("the field permits further"))
    check("continues is false after terminal punctuation",
          m.continues("Choice makes distinction possible.") is False)
    check("continues is false for a heading paragraph",
          m.continues("## Chapter One") is False)


def check_heading_and_furniture(m) -> None:
    body = 0.02
    heading = _line("Chapter One", y_top=0.2, height=body * m.HEADING_RATIO)
    check("is_heading accepts a short, taller alpha line",
          m.is_heading(heading, body))
    check("is_heading refuses a sentence that ends with a period",
          m.is_heading(_line("Too long to be a heading really.",
                             y_top=0.2, height=body * m.HEADING_RATIO),
                       body) is False)
    check("is_heading refuses body-sized lines",
          m.is_heading(_line("Ordinary prose", y_top=0.2, height=body),
                       body) is False)

    repeated = {"chapter #"}
    check("is_furniture strips a bare arabic page number in the margin",
          m.is_furniture(_line("12", y_top=0.01), repeated))
    check("is_furniture strips a roman page number in the bottom margin",
          m.is_furniture(_line("xiv", y_top=0.95, height=0.02), repeated))
    check("is_furniture strips a repeating header in the top margin",
          m.is_furniture(_line("Chapter 1", y_top=0.02), repeated))
    check("is_furniture leaves body text alone even if the key matches",
          m.is_furniture(_line("Chapter 1", y_top=0.4), repeated) is False)

    pages = [
        (1, [_line("Running Header 1", 0.02), _line("Body A", 0.3)]),
        (2, [_line("Running Header 2", 0.02), _line("Body B", 0.3)]),
        (3, [_line("Running Header 3", 0.02), _line("Body C", 0.3)]),
        (4, [_line("Odd Footer", 0.02), _line("Body D", 0.3)]),
    ]
    keys = m.furniture_keys(pages)
    check("furniture_keys collapses digit runs and keeps repeating margin lines",
          "running header #" in keys and "odd footer" not in keys,
          repr(keys))


def check_reflow(m) -> None:
    body = 0.02
    # Two body lines with a small gap → one paragraph; then a large gap +
    # indent → a second paragraph; a tall short line → a heading.
    lines = [
        _line("First", y_top=0.20, height=body, x0=0.10),
        _line("line", y_top=0.23, height=body, x0=0.10),
        _line("Second", y_top=0.40, height=body, x0=0.10 + m.INDENT_RATIO * body + 0.01),
        _line("CHAPTER", y_top=0.55, height=body * m.HEADING_RATIO, x0=0.10),
        _line("After", y_top=0.62, height=body, x0=0.10),
        _line("hy-", y_top=0.65, height=body, x0=0.10),
        _line("phen", y_top=0.68, height=body, x0=0.10),
    ]
    paragraphs = m.reflow(lines, body)
    check("reflow joins near lines into one paragraph",
          paragraphs[0] == "First line", repr(paragraphs))
    check("reflow starts a new paragraph on a large indent",
          paragraphs[1] == "Second", repr(paragraphs))
    check("reflow prefixes headings with ##",
          paragraphs[2] == "## CHAPTER", repr(paragraphs))
    check("reflow de-hyphenates across joined lines after a heading",
          paragraphs[3] == "After hyphen", repr(paragraphs))


def _write_text_pdf(path: Path, pages: list[str]) -> None:
    import pymupdf

    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page(width=612, height=792)
        # Insert enough characters to clear MIN_TEXT_LAYER_CHARS.
        page.insert_text((72, 72), text, fontsize=12)
    doc.save(path)
    doc.close()


def check_convert_and_cli(m) -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-pdf2md-"))
    try:
        pdf = root / "sample.pdf"
        # Two pages; page 1 ends mid-sentence so convert joins across the break.
        _write_text_pdf(pdf, [
            "Choice makes distinction possible when the field is open enough to",
            "permit further choices without collapsing the prior act.",
        ])

        md = m.convert(pdf, pages_range=None, force_ocr=False, dpi=72)
        check("convert emits a page marker for each text-layer page",
              "<!-- p.1 -->" in md and "<!-- p.2 -->" in md, md)
        check("convert joins a mid-sentence paragraph across the page break",
              "open enough to permit further choices" in md
              and "open enough to\n\npermit" not in md,
              md)
        check("convert keeps trailing newline on non-empty output",
              md.endswith("\n"))

        # Page range clamp: asking past the end still yields only real pages.
        md_range = m.convert(pdf, pages_range=(2, 99), force_ocr=False, dpi=72)
        check("convert clamps an oversize page range to the document",
              "<!-- p.2 -->" in md_range and "<!-- p.1 -->" not in md_range,
              md_range)

        empty_pdf = root / "blank.pdf"
        _write_text_pdf(empty_pdf, [" "])  # whitespace-only → OCR path
        stub_lines = [_line("Recovered OCR prose that is long enough.", 0.3)]
        with mock.patch.object(m, "ocr_page", return_value=stub_lines) as ocr:
            md_ocr = m.convert(empty_pdf, None, force_ocr=False, dpi=72)
        check("convert falls back to OCR when the text layer is thin",
              ocr.called and "Recovered OCR prose" in md_ocr, md_ocr)

        with mock.patch.object(m, "ocr_page", return_value=stub_lines) as ocr:
            m.convert(pdf, None, force_ocr=True, dpi=72)
        check("convert --force-ocr skips the embedded text layer",
              ocr.called)

        # CLI gates.
        out = root / "sample.md"
        out.write_text("hand edits\n", encoding="utf-8")
        argv = ["pdf2md.py", str(pdf), "-o", str(out)]
        old_argv = sys.argv
        sys.argv = argv
        try:
            try:
                m.main()
                refused = False
            except SystemExit as err:
                refused = "exists" in str(err) and "--overwrite" in str(err)
        finally:
            sys.argv = old_argv
        check("main refuses to clobber an existing markdown file",
              refused and out.read_text(encoding="utf-8") == "hand edits\n")

        missing = root / "gone.pdf"
        sys.argv = ["pdf2md.py", str(missing)]
        try:
            try:
                m.main()
                missing_ok = False
            except SystemExit as err:
                missing_ok = "no such file" in str(err)
        finally:
            sys.argv = old_argv
        check("main refuses a missing PDF path", missing_ok)

        # Successful write with --overwrite.
        sys.argv = ["pdf2md.py", str(pdf), "-o", str(out), "--overwrite",
                    "--pages", "1"]
        stderr = io.StringIO()
        with mock.patch.object(sys, "stderr", stderr):
            try:
                m.main()
            finally:
                sys.argv = old_argv
        written = out.read_text(encoding="utf-8")
        check("main --overwrite writes markdown and reports the path",
              "<!-- p.1 -->" in written
              and "wrote " in stderr.getvalue()
              and "<!-- p.2 -->" not in written,
              written)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)


def main_test() -> None:
    m = load_pdf2md()
    check_parse_pages(m)
    check_join_and_continues(m)
    check_heading_and_furniture(m)
    check_reflow(m)
    check_convert_and_cli(m)
    print(f"\nAll {PASSED} checks passed.")


if __name__ == "__main__":
    _assert_offline()
    main_test()
