"""Hermetic tests: pdf2md must not treat margin stamps as a text layer.

A scanned page whose only embedded text is a ≥50-char journal header /
download watermark used to clear MIN_TEXT_LAYER_CHARS, skip OCR, then
lose its body after furniture stripping (multi-page) or ship the stamp
alone (single-page). The gate now counts BODY-zone characters only.

Run: PYTHONPATH=. python3 tests/test_pdf2md_text_layer_gate.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

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
            "pymupdf required for test_pdf2md_text_layer_gate.py — "
            "pip install pymupdf"
        ) from err
    spec = importlib.util.spec_from_file_location("pdf2md", PDF2MD_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _line(text: str, y_top: float, height: float = 0.015, x0: float = 0.1) -> dict:
    return {"text": text, "x0": x0, "y_top": y_top, "height": height}


def _stamp_pdf(path: Path, pages: int) -> None:
    import pymupdf as fitz

    stamp = "Journal of Philosophy | Vol. 12 | Downloaded copy for personal use"
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 30), stamp, fontsize=9)
        page.insert_text((300, 760), str(i + 1), fontsize=9)
    doc.save(path)
    doc.close()


def main() -> None:
    m = load_pdf2md()

    stamp = "Journal of Philosophy | Vol. 12 | Downloaded copy for personal use"
    check("margin stamp alone contributes zero body-layer chars",
          m.body_layer_chars([
              _line(stamp, y_top=0.03),
              _line("42", y_top=0.95),
          ]) == 0, stamp)

    body = "Agency is prior to choice in the account that follows."
    check("body-zone prose counts toward the text-layer gate",
          m.body_layer_chars([
              _line(stamp, y_top=0.03),
              _line(body, y_top=0.25),
              _line("42", y_top=0.95),
          ]) == len(body), body)
    check("body of MIN_TEXT_LAYER_CHARS clears the gate",
          m.body_layer_chars([_line("x" * m.MIN_TEXT_LAYER_CHARS, y_top=0.4)])
          >= m.MIN_TEXT_LAYER_CHARS)

    # End-to-end: stamp-only PDFs must invoke OCR, not the text path.
    import tempfile

    ocr_pages: list[int] = []

    def fake_ocr(page, dpi):
        ocr_pages.append(page.number + 1)
        return [
            _line(f"Body paragraph on page {page.number + 1} about agency.",
                  y_top=0.22),
        ]

    m.ocr_page = fake_ocr

    with tempfile.TemporaryDirectory() as tmp:
        single = Path(tmp) / "stamp_one.pdf"
        _stamp_pdf(single, 1)
        ocr_pages.clear()
        out = m.convert(single, None, False, 300)
        check("single-page stamp-only PDF takes the OCR path",
              ocr_pages == [1], repr(ocr_pages))
        check("single-page stamp-only convert recovers OCR body",
              "agency" in out.lower() and "Journal of Philosophy" not in out,
              out)

        multi = Path(tmp) / "stamp_six.pdf"
        _stamp_pdf(multi, 6)
        ocr_pages.clear()
        out = m.convert(multi, None, False, 300)
        check("six-page stamp-only PDF OCRs every page",
              ocr_pages == [1, 2, 3, 4, 5, 6], repr(ocr_pages))
        check("six-page stamp-only convert is non-empty with body text",
              "agency" in out.lower() and out.strip() != "",
              repr(out[:200]))

        # Real body text still uses the text layer (OCR must not run).
        import pymupdf as fitz

        real = Path(tmp) / "real_text.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        # Well below the top margin band (MARGIN_ZONE * 792 ≈ 63pt).
        page.insert_text(
            (72, 200),
            "x" * 60 + " The substantive body clears the gate on its own.",
            fontsize=11)
        doc.save(real)
        doc.close()
        ocr_pages.clear()
        out = m.convert(real, None, False, 300)
        check("substantial body-zone text stays on the text path",
              ocr_pages == [], repr(ocr_pages))
        check("substantial body-zone text survives convert",
              "substantive body" in out, out)

        # OCR unavailable: convert must not abort; keep the text layer.
        def boom(page, dpi):
            raise RuntimeError("Vision missing (test)")

        m.ocr_page = boom
        tiny = Path(tmp) / "short.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 200), "Short.", fontsize=11)
        doc.save(tiny)
        doc.close()
        out = m.convert(tiny, None, False, 300)
        check("OCR RuntimeError falls back instead of aborting convert",
              "Short." in out, out)

    print(f"\nALL CHECKS PASSED ({PASSED})")


if __name__ == "__main__":
    main()
