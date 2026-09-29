#!/usr/bin/env python3
"""pdf2md — convert a PDF to Markdown, OCRing pages that need it (macOS).

Generic ingestion tool for any PDF headed into the writing workflow:
critique reports, references, research articles, photographed books.
Hybrid per page: a page with a substantial body-zone text layer is
extracted directly; an image-only page (including scanned pages whose
only embedded text is a margin stamp or folio) is rendered and read with
Apple's Vision framework (accurate mode). When OCR is unavailable the
text layer is kept rather than aborting the whole convert. Both paths
land in one cleanup pipeline: running header/footer and page-number
stripping, de-hyphenation, paragraph re-flow, conservative single-level
heading detection, and <!-- p.N --> page markers so quotes stay
traceable to physical pages.

Footnotes flow inline in reading order. Multi-column layouts are not
untangled. macOS-only (Vision); text-layer-only PDFs work anywhere.

Usage:
  python3 pdf2md.py book.pdf                # writes book.md alongside
  python3 pdf2md.py book.pdf -o notes.md --pages 12-40 --force-ocr
"""

import argparse
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

try:
    import pymupdf as fitz
except ImportError:
    sys.exit("missing dependency — run: pip3 install pymupdf")

# Layout thresholds, all in fractions of page height/width so text-layer
# pages (points) and OCR'd pages (pixels) measure alike.
MARGIN_ZONE = 0.08        # top/bottom band searched for headers/footers
REPEAT_FRACTION = 0.3     # line repeating on this share of pages = furniture
HEADING_RATIO = 1.35      # line height vs body height to count as a heading
HEADING_MAX_WORDS = 12
PARA_GAP_RATIO = 1.9      # vertical gap vs typical line gap = new paragraph
INDENT_RATIO = 1.2        # indent vs body height = new paragraph
# Below this many BODY-zone (non-margin) characters, a page is treated as
# image-only and OCR'd. Margin stamps/folios do not count — see body_layer_chars.
MIN_TEXT_LAYER_CHARS = 50


def ocr_page(page: "fitz.Page", dpi: int) -> list[dict]:
    """Render one page and return Vision text observations as line dicts.

    Raises RuntimeError when Vision is unavailable or recognition fails —
    callers decide whether to fall back to the text layer. Never
    ``sys.exit``: a stamp-only page that needs OCR must not abort a
    multi-page convert that still has readable text-layer pages left.
    """
    try:
        import Vision
        from Foundation import NSData
    except ImportError as err:
        raise RuntimeError(
            "OCR requires macOS Vision — pip3 install pyobjc-framework-Vision"
        ) from err

    pix = page.get_pixmap(dpi=dpi)
    png = pix.tobytes("png")
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
        NSData.dataWithBytes_length_(png, len(png)), None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(f"Vision OCR failed on page {page.number + 1}: {error}")

    lines = []
    for obs in request.results() or []:
        candidates = obs.topCandidates_(1)
        if not candidates:
            continue
        text = candidates[0].string().strip()
        if not text:
            continue
        box = obs.boundingBox()  # normalized, origin bottom-left
        lines.append({
            "text": text,
            "x0": box.origin.x,
            "y_top": 1.0 - box.origin.y - box.size.height,
            "height": box.size.height,
        })
    lines.sort(key=lambda l: (l["y_top"], l["x0"]))
    return lines


def text_layer_page(page: "fitz.Page") -> list[dict]:
    """Return the embedded text layer as line dicts in normalized units."""
    width, height = page.rect.width, page.rect.height
    lines = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            text = "".join(span["text"] for span in line["spans"]).strip()
            if not text:
                continue
            x0, y0, _, _ = line["bbox"]
            size = max(span["size"] for span in line["spans"])
            lines.append({
                "text": text,
                "x0": x0 / width,
                "y_top": y0 / height,
                "height": size / height,
            })
    lines.sort(key=lambda l: (l["y_top"], l["x0"]))
    return lines


def body_layer_chars(lines: list[dict]) -> int:
    """Characters outside the top/bottom margin zone.

    The text-vs-OCR gate must ignore stamps, download watermarks, and
    folios living in the margin: a scanned page whose only embedded text
    is a 55-char journal header used to clear ``MIN_TEXT_LAYER_CHARS``,
    skip OCR, then lose its entire body after furniture stripping (or
    ship the stamp alone as the page). Body-zone characters are what
    "a substantial embedded text layer" means.
    """
    total = 0
    for line in lines:
        top = line["y_top"]
        bottom = top + line["height"]
        if top < MARGIN_ZONE or bottom > 1 - MARGIN_ZONE:
            continue
        total += len(line["text"])
    return total


def _try_ocr(page: "fitz.Page", dpi: int, number: int,
             fallback: list[dict]) -> tuple[list[dict], str]:
    """OCR one page; on failure keep ``fallback`` rather than aborting."""
    try:
        return ocr_page(page, dpi), "ocr"
    except RuntimeError as err:
        print(f"  page {number}: OCR unavailable ({err}); "
              f"{'using text layer' if fallback else 'no text layer'}",
              file=sys.stderr)
        return fallback, "text-fallback" if fallback else "ocr-failed"


def furniture_keys(pages: list[tuple[int, list[dict]]]) -> set[str]:
    """Keys of running headers/footers: margin-zone lines that repeat."""
    counts = Counter()
    for _, lines in pages:
        seen = set()
        for line in lines:
            if line["y_top"] < MARGIN_ZONE or line["y_top"] + line["height"] > 1 - MARGIN_ZONE:
                seen.add(re.sub(r"\d+", "#", line["text"].lower()).strip())
        counts.update(seen)
    threshold = max(3, REPEAT_FRACTION * len(pages))
    return {key for key, n in counts.items() if n >= threshold}


def is_furniture(line: dict, repeated: set[str]) -> bool:
    in_margin = (line["y_top"] < MARGIN_ZONE
                 or line["y_top"] + line["height"] > 1 - MARGIN_ZONE)
    if not in_margin:
        return False
    text = line["text"].strip()
    if re.fullmatch(r"[0-9]+|[ivxlcdm]+", text.lower()):
        return True  # bare page number, arabic or roman
    return re.sub(r"\d+", "#", text.lower()).strip() in repeated


def is_heading(line: dict, body_height: float) -> bool:
    text = line["text"]
    return (line["height"] >= HEADING_RATIO * body_height
            and len(text.split()) <= HEADING_MAX_WORDS
            and not text.endswith((".", ",", ";"))
            and any(c.isalpha() for c in text))


def join(prefix: str, addition: str) -> str:
    """Append a line to a paragraph, resolving end-of-line hyphenation."""
    if prefix.endswith("-") and addition[:1].islower():
        return prefix[:-1] + addition
    return f"{prefix} {addition}" if prefix else addition


def reflow(lines: list[dict], body_height: float) -> list[str]:
    """Turn one page's lines into paragraphs ('## ' prefix marks headings)."""
    gaps = [b["y_top"] - (a["y_top"] + a["height"])
            for a, b in zip(lines, lines[1:])
            if b["y_top"] - (a["y_top"] + a["height"]) > 0]
    # Lower quartile, not median: on a short page the paragraph gaps
    # themselves would drag a median up past the split threshold.
    typical_gap = sorted(gaps)[len(gaps) // 4] if gaps else body_height * 0.4
    body_x0 = min((l["x0"] for l in lines), default=0.0)
    page_break_gap = max(PARA_GAP_RATIO * typical_gap, 0.8 * body_height)

    paragraphs: list[str] = []
    previous = None
    for line in lines:
        if is_heading(line, body_height):
            paragraphs.append("## " + line["text"])
            previous = None
            continue
        starts_new = (
            previous is None
            or paragraphs[-1].startswith("## ")
            or line["y_top"] - (previous["y_top"] + previous["height"]) > page_break_gap
            or line["x0"] - body_x0 > INDENT_RATIO * body_height)
        if starts_new or not paragraphs:
            paragraphs.append(line["text"])
        else:
            paragraphs[-1] = join(paragraphs[-1], line["text"])
        previous = line
    return paragraphs


def continues(paragraph: str) -> bool:
    """True if a page-final paragraph looks unfinished mid-sentence."""
    return (not paragraph.startswith("## ")
            and not paragraph.rstrip().endswith((".", "!", "?", ":", '"', "'", ")")))


def convert(pdf_path: Path, pages_range: tuple[int, int] | None,
            force_ocr: bool, dpi: int) -> str:
    doc = fitz.open(pdf_path)
    first, last = pages_range or (1, doc.page_count)
    first, last = max(1, first), min(doc.page_count, last)

    pages = []
    for number in range(first, last + 1):
        page = doc[number - 1]
        if force_ocr:
            lines, how = _try_ocr(page, dpi, number, fallback=[])
        else:
            # Gate on BODY-zone characters, not raw get_text length:
            # margin stamps alone used to clear MIN_TEXT_LAYER_CHARS and
            # skip OCR, so scanned journal pages converted to "" (or to
            # the watermark alone) after furniture stripping.
            layer = text_layer_page(page)
            if body_layer_chars(layer) >= MIN_TEXT_LAYER_CHARS:
                lines, how = layer, "text"
            else:
                lines, how = _try_ocr(page, dpi, number, fallback=layer)
        pages.append((number, lines))
        if number % 10 == 0 or number == last:
            print(f"  page {number}/{last} ({how})", file=sys.stderr)

    repeated = furniture_keys(pages)
    pages = [(n, [l for l in lines if not is_furniture(l, repeated)])
             for n, lines in pages]

    heights = [l["height"] for _, lines in pages for l in lines]
    if not heights:
        return ""
    body_height = statistics.median(heights)

    out: list[str] = []
    for number, lines in pages:
        paragraphs = reflow(lines, body_height) if lines else []
        marker = f"<!-- p.{number} -->"
        # A paragraph running across the page break stays one paragraph;
        # the marker then reads "page N starts within the text above it".
        if out and paragraphs and continues(out[-1]) and not paragraphs[0].startswith("## "):
            out[-1] = join(out[-1], paragraphs.pop(0))
        out.append(marker)
        out.extend(paragraphs)
    return "\n\n".join(out) + "\n"


def parse_pages(spec: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)(?:-(\d+))?", spec)
    if not match:
        raise argparse.ArgumentTypeError("expected N or N-M, e.g. --pages 12-40")
    first = int(match.group(1))
    return first, int(match.group(2) or first)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a PDF (text-layer or scanned) to Markdown.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("-o", "--output", type=Path,
                        help="output path (default: alongside the PDF)")
    parser.add_argument("--pages", type=parse_pages, metavar="N[-M]",
                        help="1-based inclusive page range")
    parser.add_argument("--force-ocr", action="store_true",
                        help="OCR every page, ignoring any embedded text layer")
    parser.add_argument("--dpi", type=int, default=300,
                        help="render resolution for OCR (default 300)")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace an existing output file")
    args = parser.parse_args()

    if not args.pdf.is_file():
        sys.exit(f"no such file: {args.pdf}")
    output = args.output or args.pdf.with_suffix(".md")
    if output.exists() and not args.overwrite:
        sys.exit(f"{output} exists — pass --overwrite to replace it "
                 "(it may contain hand edits)")

    markdown = convert(args.pdf, args.pages, args.force_ocr, args.dpi)
    output.write_text(markdown)
    print(f"wrote {output}", file=sys.stderr)


if __name__ == "__main__":
    main()
