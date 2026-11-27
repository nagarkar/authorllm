"""Unit pins for lint_page's quote/dash target, apply planner, and undo
row selection.

`test_lint_page` covers Apply/Undo through the Doc road. These pin the
pure doors that decide which style a quote/dash fix aims at, which fixes
in a batch can land without overlapping, and which lint_changes rows
Undo may reverse — wrong answers here rewrite the wrong prose or refuse
a valid undo.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import api, lint_page, styles  # noqa: E402
from authorlm.db import Database, ko_fields  # noqa: E402

PASSED = 0


def check(label: str, cond: bool, detail: object = "") -> None:
    global PASSED
    print(f"  [{'ok' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)
    PASSED += 1


def _ms(root: Path, name: str):
    ws = root / name
    (ws / ".authorlm").mkdir(parents=True)
    # No workspace defaults: a missing file leaves effective style empty
    # of dash/quote house laws so the target helpers' "no law" road is visible.
    os.environ[styles.DEFAULTS_ENV] = str(ws / "missing-defaults.toml")
    ms = ws / "book"
    ms.mkdir()
    (ms / "a.md").write_text(
        '# A\n\nHe said "hello" and “goodbye”.\n\n'
        "A spaced — dash — here.\n", encoding="utf-8")
    db = Database(ws / ".authorlm" / "authorlm.db")
    manuscript = dict(api.register_manuscript(db, "book", str(ms)))
    api.define_style_guide(db, manuscript, "House", parent=None)
    api.attach_style(db, manuscript, "a.md", "House")
    return ws, db, manuscript, ms


def test_quote_and_dash_target(root: Path) -> None:
    ws, db, manuscript, ms = _ms(root, "quote")
    text = (ms / "a.md").read_text(encoding="utf-8")
    style, law = lint_page._quote_target(db, manuscript, "a.md", text)
    check("mixed quotes with no law pick the majority style",
          style == "curly" and law is None, (style, law))
    check("no dash law means no dash target",
          lint_page._dash_target(db, manuscript, "a.md") is None)

    q = api.add_style_law(db, manuscript, "formatting",
                          "Use straight quote marks throughout.",
                          guide_name="House")
    style, law = lint_page._quote_target(db, manuscript, "a.md", text)
    check("a quote law names the style and the law row",
          style == "straight" and law and law["id"] == q["id"]
          and "straight" in law["statement"], (style, law))

    api.add_style_law(db, manuscript, "formatting",
                      "Use a spaced em dash with spaces around it.",
                      guide_name="House")
    check("a dash law sets the spaced form",
          lint_page._dash_target(db, manuscript, "a.md") == "spaced")

    pure = '# B\n\nNo quotes here.\n'
    style, law = lint_page._quote_target(db, manuscript, "a.md", pure)
    # Law still speaks even when the file has no quotes to count.
    check("a quote law still wins on a quote-free file",
          style == "straight" and law and law["id"] == q["id"], (style, law))


def test_plan_again(root: Path) -> None:
    ws, db, manuscript, ms = _ms(root, "plan")
    text = "# T\n\nThe colour was grey and colour again.\n"
    (ms / "a.md").write_text(text, encoding="utf-8")

    ok = {"kind": "word", "word": "colour", "to": "color", "id": "1"}
    taken, why, err = lint_page._plan_again([ok], text, db, manuscript, "a.md")
    check("a matching word fix is taken for every hit",
          err is None and len(taken) == 2 and not why
          and {t[3] for t in taken} == {0}
          and all(t[2] == "color" for t in taken), (taken, why))

    gone = {"kind": "word", "word": "missing", "to": "gone", "id": "2"}
    taken, why, err = lint_page._plan_again([gone], text, db, manuscript, "a.md")
    check("a word no longer in the file is blocked with a re-lint hint",
          err is None and not taken and 0 in why
          and "run lint again" in why[0], why)

    a = {"kind": "word", "word": "colour", "to": "color", "id": "a"}
    b = {"kind": "word", "word": "colour", "to": "hue", "id": "b"}
    taken, why, err = lint_page._plan_again([a, b], text, db, manuscript, "a.md")
    check("a second fix that overlaps the first is blocked",
          err is None and len(taken) >= 1 and any(
              "overlaps" in w for w in why.values()), (taken, why))

    bad = {"kind": "nope", "id": "x"}
    taken, why, err = lint_page._plan_again([bad], text, db, manuscript, "a.md")
    check("an unknown kind is recorded as why, not raised",
          err is None and not taken and 0 in why
          and "not" in why[0], why)


def _row(db, manuscript_id: str, *, batch: str, file: str = "a.md",
         before: str = "old", after: str = "new", undone: str | None = None,
         meta: dict | None = None, row_id: str | None = None) -> str:
    fields = ko_fields("lc")
    if row_id:
        fields["id"] = row_id
    if meta is not None:
        fields["metadata"] = json.dumps(meta)
    fields.update(manuscript_id=manuscript_id, batch_id=batch, file=file,
                  rule="curl", line=3, before_text=before, after_text=after,
                  pushed=0, undone_at=undone, restaged=None)
    db.insert("lint_changes", fields)
    return fields["id"]


def test_undo_fixes(root: Path) -> None:
    ws, db, manuscript, ms = _ms(root, "undo")
    mid = manuscript["id"]

    try:
        lint_page._undo_fixes(db, manuscript, [])
        check("empty ids raise", False)
    except ValueError as err:
        check("empty ids raise", "name the changes" in str(err), err)

    try:
        lint_page._undo_fixes(db, manuscript, ["missing"])
        check("unknown id raises LookupError", False)
    except LookupError as err:
        check("unknown id raises LookupError", "nothing to undo" in str(err),
              err)

    keep = _row(db, mid, batch="b1", before="was grey", after="was gray",
                row_id="lc-keep")
    _row(db, mid, batch="b1", before="gone", after="gone2",
         meta={"doc": "withdrawn"}, row_id="lc-out")
    _row(db, mid, batch="b2", before="old", after="new",
         undone="2026-01-01T00:00:00.000000Z", row_id="lc-done")
    by_batch = _row(db, mid, batch="batch-x", before="A", after="B",
                    row_id="lc-batch")

    fixes = lint_page._undo_fixes(db, manuscript, [keep])
    check("undo by change id swaps after→old and before→new",
          len(fixes) == 1 and fixes[0]["id"] == keep
          and fixes[0]["old"] == "was gray" and fixes[0]["new"] == "was grey"
          and fixes[0]["kind"] == "paragraph" and fixes[0]["rule"] == "undo",
          fixes)

    try:
        lint_page._undo_fixes(db, manuscript, ["lc-out"])
        check("withdrawn rows are not undone", False)
    except LookupError:
        check("withdrawn rows are not undone", True)

    try:
        lint_page._undo_fixes(db, manuscript, ["lc-done"])
        check("already-undone rows are not undone", False)
    except LookupError:
        check("already-undone rows are not undone", True)

    fixes = lint_page._undo_fixes(db, manuscript, ["batch-x"])
    check("undo by batch_id finds the batch's live rows",
          len(fixes) == 1 and fixes[0]["id"] == by_batch
          and fixes[0]["old"] == "B" and fixes[0]["new"] == "A", fixes)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        test_quote_and_dash_target(root)
        test_plan_again(root)
        test_undo_fixes(root)
    print(f"\n{PASSED} checks passed")


if __name__ == "__main__":
    main()
