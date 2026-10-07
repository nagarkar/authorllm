"""Doc tab spacing: [gdocs.spacing] in _exports/settings.toml.

`gdocs.doc_spacing` feeds every insert-only Doc heal. A nested override
that silently fell back to DOC_SPACING left author-declared spacing
unapplied on every content write.

Run: python3 tests/test_doc_spacing.py
"""

from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import gdocs  # noqa: E402
from authorlm.export import EXPORT_DIR  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


def main_test() -> None:
    with tempfile.TemporaryDirectory(prefix="authorlm-doc-spacing-") as root:
        ms = {"path": root}
        check("missing settings.toml yields DOC_SPACING",
              gdocs.doc_spacing(ms) == gdocs.DOC_SPACING
              == {"line_spacing": 115, "space_above": 0, "space_below": 6},
              str(gdocs.doc_spacing(ms)))

        exports = Path(root) / EXPORT_DIR
        exports.mkdir()
        (exports / "settings.toml").write_text(
            'title = "A Book"\nvariant = "images"\n', encoding="utf-8")
        check("flat export keys alone leave DOC_SPACING intact",
              gdocs.doc_spacing(ms) == gdocs.DOC_SPACING,
              str(gdocs.doc_spacing(ms)))

        (exports / "settings.toml").write_text(
            'title = "A Book"\n\n'
            "[gdocs.spacing]\n"
            "line_spacing = 150\n"
            "space_below = 12\n",
            encoding="utf-8")
        got = gdocs.doc_spacing(ms)
        check("nested [gdocs.spacing] overrides named keys and keeps "
              "unmentioned defaults",
              got == {"line_spacing": 150, "space_above": 0,
                      "space_below": 12},
              str(got))

        (exports / "settings.toml").write_text(
            'title = "A Book"\n\n'
            "[gdocs]\n"
            "[gdocs.spacing]\n"
            "space_above = 3\n",
            encoding="utf-8")
        got = gdocs.doc_spacing(ms)
        check("partial [gdocs.spacing] merges over DOC_SPACING",
              got == {"line_spacing": 115, "space_above": 3,
                      "space_below": 6},
              str(got))

        (exports / "settings.toml").write_text(
            'title = "A Book"\n\n[gdocs]\nclient_secret = "x"\n',
            encoding="utf-8")
        check("a [gdocs] block without spacing still defaults",
              gdocs.doc_spacing(ms) == gdocs.DOC_SPACING,
              str(gdocs.doc_spacing(ms)))

    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    main_test()
