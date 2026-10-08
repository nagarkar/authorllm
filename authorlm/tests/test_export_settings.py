"""`export.load_settings` / `set_setting` own `_exports/settings.toml`.

Known flat keys drive pandoc/epub metadata. Authors may also hand-edit
nested tables such as `[gdocs.spacing]` — Doc body spacing law that
`gdocs.doc_spacing` merges over `DOC_SPACING`. A `set_setting` rewrite
that kept only the flat defaults used to delete that nested block, so
the next insert-only heal silently fell back to the ratified defaults.
This file pins: nested tables load, survive a flat-key write, and reach
`doc_spacing`; unknown keys still refuse; quotes/backslashes round-trip.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.export import (EXPORT_DIR, load_settings, set_setting,
                             _settings_path)
from authorlm.gdocs import DOC_SPACING, doc_spacing


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _manuscript_with_spacing() -> dict:
    root = Path(tempfile.mkdtemp(prefix="authorlm-export-settings-"))
    ms = {"path": str(root)}
    exports = root / EXPORT_DIR
    exports.mkdir()
    (exports / "settings.toml").write_text(
        '# AuthorLM export settings — hand-edited spacing.\n'
        'title = "Book"\n'
        'variant = "images"\n'
        'language = "en"\n'
        'reference_docx = ""\n'
        'cover_image = ""\n'
        'pdf_engine = "xelatex"\n'
        'pdf_font = ""\n'
        'pdf_mathfont = "STIX Two Math"\n'
        '\n'
        '[gdocs.spacing]\n'
        'line_spacing = 200\n'
        'space_above = 1\n'
        'space_below = 12\n',
        encoding="utf-8",
    )
    return ms


def test_load_keeps_nested_gdocs_spacing() -> None:
    ms = _manuscript_with_spacing()
    settings = load_settings(ms)
    check("load_settings returns nested [gdocs.spacing]",
          settings.get("gdocs", {}).get("spacing")
          == {"line_spacing": 200, "space_above": 1, "space_below": 12},
          settings.get("gdocs"))
    check("doc_spacing merges the nested override over DOC_SPACING",
          doc_spacing(ms) == {"line_spacing": 200, "space_above": 1,
                              "space_below": 12},
          doc_spacing(ms))


def test_set_setting_preserves_nested() -> None:
    ms = _manuscript_with_spacing()
    set_setting(ms, "title", 'A "Revised" Title')
    settings = load_settings(ms)
    check("set_setting keeps the nested spacing table on disk",
          settings.get("gdocs", {}).get("spacing")
          == {"line_spacing": 200, "space_above": 1, "space_below": 12},
          settings.get("gdocs"))
    check("set_setting still updates the flat key",
          settings["title"] == 'A "Revised" Title', settings["title"])
    check("doc_spacing still sees the override after set_setting",
          doc_spacing(ms)["line_spacing"] == 200, doc_spacing(ms))
    text = _settings_path(ms).read_text(encoding="utf-8")
    check("rewritten TOML still names the [gdocs.spacing] table",
          "[gdocs.spacing]" in text, text)


def test_defaults_without_nested() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-export-defaults-"))
    ms = {"path": str(root)}
    check("absent settings.toml → DOC_SPACING defaults",
          doc_spacing(ms) == DOC_SPACING, doc_spacing(ms))
    set_setting(ms, "variant", "slots")
    check("first set_setting creates flat defaults only",
          load_settings(ms)["variant"] == "slots"
          and "gdocs" not in load_settings(ms),
          load_settings(ms))
    check("still DOC_SPACING when no nested table exists",
          doc_spacing(ms) == DOC_SPACING, doc_spacing(ms))


def test_unknown_key_and_escapes() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-export-escapes-"))
    ms = {"path": str(root)}
    try:
        set_setting(ms, "nope", "x")
        refused = False
    except LookupError:
        refused = True
    check("unknown export setting raises LookupError", refused)
    set_setting(ms, "title", 'A "Great" Book')
    set_setting(ms, "reference_docx", "C:\\styles\\ref.docx")
    settings = load_settings(ms)
    check("quotes and backslashes round-trip through TOML",
          settings["title"] == 'A "Great" Book'
          and settings["reference_docx"] == "C:\\styles\\ref.docx",
          settings)


def main() -> None:
    test_load_keeps_nested_gdocs_spacing()
    test_set_setting_preserves_nested()
    test_defaults_without_nested()
    test_unknown_key_and_escapes()
    print("test_export_settings: all checks passed")


if __name__ == "__main__":
    main()
