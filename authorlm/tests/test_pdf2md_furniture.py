"""Hermetic tests: pdf2md furniture must not eat English body words.

The old page-number test was `fullmatch([0-9]+|[ivxlcdm]+)`, which
treated any margin-zone line made only of roman-numeral letters as a
folio — including English words (did, civil, mild, mid…). A wrapped
page-final "did" in the bottom 8% was deleted, and convert's
cross-page join then produced a corrupted sentence.

Run: PYTHONPATH=. python3 tests/test_pdf2md_furniture.py
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
            "pymupdf required for test_pdf2md_furniture.py — "
            "pip install pymupdf"
        ) from err
    spec = importlib.util.spec_from_file_location("pdf2md", PDF2MD_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _line(text: str, y_top: float, height: float = 0.015, x0: float = 0.1) -> dict:
    return {"text": text, "x0": x0, "y_top": y_top, "height": height}


def main() -> None:
    m = load_pdf2md()
    empty: set[str] = set()

    # --- false positives the character-class test used to strip ---
    for word in ("did", "civil", "mild", "mid", "dim", "lid", "ill",
                 "mill", "mimic", "civic", "id", "midi"):
        check(f"margin body word {word!r} is not a page number",
              m.is_furniture(_line(word, y_top=0.93), empty) is False)

    check("top-margin pronoun 'I' alone is not stripped without repetition",
          m.is_furniture(_line("I", y_top=0.05), empty) is False)
    check("top-margin 'I' still strips when it is running furniture",
          m.is_furniture(_line("I", y_top=0.05), {"i"}) is True)

    # --- real folio lines still go ---
    check("arabic page number in the margin still strips",
          m.is_furniture(_line("12", y_top=0.01), empty))
    check("well-formed roman folio in the bottom margin still strips",
          m.is_furniture(_line("xiv", y_top=0.95), empty))
    check("multi-letter roman 'iii' still strips",
          m.is_furniture(_line("iii", y_top=0.95), empty))
    check("repeating header in the top margin still strips",
          m.is_furniture(_line("Chapter 1", y_top=0.02), {"chapter #"}))
    check("body-zone text is never furniture even when the key matches",
          m.is_furniture(_line("did", y_top=0.4), empty) is False)

    # --- end-to-end: page-final 'did' survives and joins across the break ---
    pages = [
        (1, [
            _line("She refused the premise, as any careful reader", 0.85),
            _line("did", 0.93),
        ]),
        (2, [
            _line("when pressed on the necessity claim itself.", 0.12),
        ]),
    ]
    repeated = m.furniture_keys(pages)
    cleaned = [(n, [l for l in lines if not m.is_furniture(l, repeated)])
               for n, lines in pages]
    check("furniture_keys does not classify English 'did' as furniture",
          "did" not in repeated, repr(repeated))
    check("page-final 'did' survives furniture stripping",
          any(l["text"] == "did" for l in cleaned[0][1]),
          repr(cleaned[0][1]))

    # Mimic convert's cross-page join on the cleaned lines.
    heights = [l["height"] for _, lines in cleaned for l in lines]
    body = sorted(heights)[len(heights) // 2]
    out: list[str] = []
    for _, lines in cleaned:
        paragraphs = m.reflow(lines, body) if lines else []
        if out and paragraphs and m.continues(out[-1]) \
                and not paragraphs[0].startswith("## "):
            out[-1] = m.join(out[-1], paragraphs.pop(0))
        out.extend(paragraphs)
    joined = out[0] if out else ""
    check("cross-page join keeps 'did' in the recovered sentence",
          "reader did when pressed" in joined, joined)

    print(f"\nALL CHECKS PASSED ({PASSED})")


if __name__ == "__main__":
    main()
