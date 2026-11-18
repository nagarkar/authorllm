"""`export docx --watermark TEXT --format "font=…; size=…; line-spacing=…"`
(nagarkar/authorllm#469): the watermark header and footer parts, the
restyled styles.xml, and that a plain export is pandoc's output untouched.

Hermetic: a scratch workspace, pandoc locally, no network, no model.
Run: python3 tests/test_export_docx_format.py
"""
from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import test_passes  # noqa: E402,F401  (pins config/.env away from the real ones)

from authorlm import docx_style  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            cli_main(argv)
        except SystemExit as exc:
            code = 1 if isinstance(exc.code, str) else (exc.code or 0)
            if isinstance(exc.code, str):
                out.write(exc.code)
    return code, out.getvalue()


CHAPTER = """# The First Chapter

Body text that runs on.[^1]

A second paragraph.

[^1]: A footnote.
"""


def main() -> int:
    if shutil.which("pandoc") is None:
        print("SKIP: pandoc not installed")
        return 0
    tmp = Path(tempfile.mkdtemp())
    try:
        ws, ms = tmp / "ws", tmp / "ws" / "Book"
        ms.mkdir(parents=True)
        (ms / "one.md").write_text(CHAPTER, encoding="utf-8")
        (ms / "toc.toml").write_text('[[chapter]]\nfile = "one.md"\n',
                                     encoding="utf-8")
        run(["--workspace", str(ws), "init", "--name", "Book", "--path",
             str(ms), "--extraction", "off"])
        base = ["--workspace", str(ws), "export", "docx"]
        out = ms / "_exports" / "Book.docx"

        print("plain export")
        code, text = run(base)
        check("plain export succeeds", code == 0, text)
        with zipfile.ZipFile(out) as z:
            names = set(z.namelist())
            plain_styles = z.read("word/styles.xml").decode()
        check("no watermark header or footer part",
              docx_style.HEADER_PART not in names
              and docx_style.FOOTER_PART not in names, names)
        check("styles untouched (no pageBreakBefore)",
              "pageBreakBefore" not in plain_styles)

        print("--watermark and --format")
        code, text = run(base + [
            "--watermark", "Review copy for A & B",
            "--format", "font=Times New Roman; size=12; line-spacing=2"])
        check("export with both flags succeeds", code == 0, text)
        with zipfile.ZipFile(out) as z:
            names = set(z.namelist())
            styles = z.read("word/styles.xml").decode()
            hdr = z.read(docx_style.HEADER_PART).decode()
            ftr = z.read(docx_style.FOOTER_PART).decode()
            doc = z.read("word/document.xml").decode()
            rels = z.read("word/_rels/document.xml.rels").decode()
            types = z.read("[Content_Types].xml").decode()
        check("body font is Times New Roman",
              'w:ascii="Times New Roman"' in styles)
        check("size 12pt is w:sz 24", '<w:sz w:val="24"/>' in styles)
        check("line-spacing 2 is w:line 480", 'w:line="480"' in styles)
        h1 = styles[styles.index('w:styleId="Heading1"'):]
        h1 = h1[:h1.index("</w:style>")]
        check("Heading 1 has pageBreakBefore and is centered",
              "<w:pageBreakBefore/>" in h1 and 'w:val="center"' in h1, h1)
        body = styles[styles.index('w:styleId="BodyText"'):]
        body = body[:body.index("</w:style>")]
        check("BodyText has a 0.5in first-line indent",
              'w:firstLine="720"' in body, body)
        fn = styles[styles.index('w:styleId="FootnoteText"'):]
        fn = fn[:fn.index("</w:style>")]
        check("footnotes stay single-spaced", 'w:line="240"' in fn, fn)
        check("header holds the VML text-path watermark",
              "<v:textpath" in hdr
              and 'string="Review copy for A &amp; B"' in hdr, hdr[-400:])
        check("footer holds the text beside a PAGE field",
              "Review copy for A &amp; B" in ftr and " PAGE " in ftr)
        check("section references header and footer",
              "rIdAuthorLMHdr" in doc and "rIdAuthorLMFtr" in doc)
        check("relationships and content types registered",
              "headerAuthorLM.xml" in rels and "footerAuthorLM.xml" in rels
              and "/word/headerAuthorLM.xml" in types)

        print("errors")
        code, text = run(base + ["--format", "colour=red"])
        check("unknown --format key fails naming it",
              code == 1 and "unknown key 'colour'" in text, text)
        code, text = run(base + ["--format", "size=big"])
        check("non-numeric size fails", code == 1 and "number" in text, text)
        code, text = run(["--workspace", str(ws), "export", "epub",
                          "--format", "size=12"])
        check("--format is docx only", code == 1 and "docx" in text, text)
        code, text = run(["--workspace", str(ws), "export", "pdf",
                          "--print-ready", "--watermark", "X"])
        check("--watermark not on a print-ready PDF",
              code == 1 and "--watermark" in text, text)
        check("parse_format reads all three keys",
              docx_style.parse_format("font=Garamond;size=11;line-spacing=1.5")
              == {"font": "Garamond", "size": 11.0, "line-spacing": 1.5})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
