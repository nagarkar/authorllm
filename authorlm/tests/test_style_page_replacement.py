"""Unit pins for style_page's replacement resolver and law↔file bindings.

The Style laws page shows which active law replaced a retired one, and
which files still hold each law. `test_style_page` covers the verbs end
to end; these pin the three replacement roads (named at retire, named in
overrides, likely by similarity) and the binding map — a wrong answer
points the author at the wrong successor or hides a live binding.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import api, style_page, styles  # noqa: E402
from authorlm.db import Database  # noqa: E402

PASSED = 0

DEFAULTS = '''
[[default]]
key = "dash"
aspect = "formatting"
statement = "Closed em dash, no spaces."
'''


def check(label: str, cond: bool, detail: object = "") -> None:
    global PASSED
    print(f"  [{'ok' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)
    PASSED += 1


def _book(root: Path, name: str):
    ws = root / name
    (ws / ".authorlm").mkdir(parents=True)
    (ws / "defaults.toml").write_text(DEFAULTS, encoding="utf-8")
    os.environ[styles.DEFAULTS_ENV] = str(ws / "defaults.toml")
    ms = ws / "book"
    ms.mkdir()
    for f in ("a.md", "b.md", "loose.md"):
        (ms / f).write_text(f"# {f}\n\nText.\n", encoding="utf-8")
    db = Database(ws / ".authorlm" / "authorlm.db")
    manuscript = dict(api.register_manuscript(db, "book", str(ms)))
    for guide, parent in (("House", None), ("Part", "House")):
        api.define_style_guide(db, manuscript, guide, parent=parent)
    api.attach_style(db, manuscript, "a.md", "Part")
    api.attach_style(db, manuscript, "b.md", "House")
    return ws, db, manuscript


def test_replacement_roads(root: Path) -> None:
    ws, db, manuscript = _book(root, "repl")
    old = api.add_style_law(db, manuscript, "citation",
                            "Cite every claim.", guide_name="Part")
    new = api.add_style_law(db, manuscript, "citation",
                            "Cite every contestable claim.",
                            guide_name="Part")
    api.retire_style_law(db, manuscript, old["id"], reason="reworded",
                         replaced_by=new["id"])
    hist = api.style_history(db, manuscript, old["id"])
    active = [dict(r) for r in db.all(
        "SELECT * FROM style_laws WHERE manuscript_id = ? AND status = 'active'",
        (manuscript["id"],))]
    retired = dict(db.one("SELECT * FROM style_laws WHERE id = ?", (old["id"],)))
    got = style_page._replacement(retired, active, hist)
    check("replaced_by in history is a known replacement",
          got and got["id"] == new["id"] and got["known"] is True, got)

    # Overrides road: the successor names the soon-to-be-retired id while
    # it is still active, then the predecessor is retired.
    left = api.add_style_law(db, manuscript, "structure",
                             "One question per essay.", guide_name="House")
    right = api.add_style_law(db, manuscript, "structure",
                              "One question per section.",
                              guide_name="House", overrides=left["id"])
    api.retire_style_law(db, manuscript, left["id"], reason="narrowed")
    hist = api.style_history(db, manuscript, left["id"])
    active = [dict(r) for r in db.all(
        "SELECT * FROM style_laws WHERE manuscript_id = ? AND status = 'active'",
        (manuscript["id"],))]
    retired = dict(db.one("SELECT * FROM style_laws WHERE id = ?", (left["id"],)))
    got = style_page._replacement(retired, active, hist)
    check("an active law that overrides the retired id is known",
          got and got["id"] == right["id"] and got["known"] is True, got)

    # Likely road: retire by hand (no replaced_by) then re-add similar words.
    older = api.add_style_law(db, manuscript, "lexicon",
                              "Never write towards; write toward.",
                              guide_name="House")
    db.update("style_laws", older["id"], {"status": "retired"})
    readd = api.add_style_law(db, manuscript, "lexicon",
                              "Never write towards; always write toward.",
                              guide_name="House")
    hist = api.style_history(db, manuscript, older["id"])  # only the add
    active = [dict(r) for r in db.all(
        "SELECT * FROM style_laws WHERE manuscript_id = ? AND status = 'active'",
        (manuscript["id"],))]
    retired = dict(db.one("SELECT * FROM style_laws WHERE id = ?",
                          (older["id"],)))
    got = style_page._replacement(retired, active, hist)
    check("a similar later law at the same level is a likely replacement",
          got and got["id"] == readd["id"] and got["known"] is False, got)

    # Wrong level / earlier / dissimilar → None
    orphan = api.add_style_law(db, manuscript, "tone",
                               "Keep the voice dry.", guide_name="Part")
    db.update("style_laws", orphan["id"], {"status": "retired"})
    hist = api.style_history(db, manuscript, orphan["id"])
    active = [dict(r) for r in db.all(
        "SELECT * FROM style_laws WHERE manuscript_id = ? AND status = 'active'",
        (manuscript["id"],))]
    retired = dict(db.one("SELECT * FROM style_laws WHERE id = ?",
                          (orphan["id"],)))
    got = style_page._replacement(retired, active, hist)
    check("no similar successor yields None", got is None, got)


def test_bindings(root: Path) -> None:
    ws, db, manuscript = _book(root, "bind")
    house = api.add_style_law(db, manuscript, "lexicon",
                              "Use everyday words.", guide_name="House")
    part = api.add_style_law(db, manuscript, "structure",
                             "One question per essay.", guide_name="Part")
    only_b = api.add_style_law(db, manuscript, "citation",
                               "Cite every claim.", guide_name="House")
    # Displace house citation for a.md via a Part law? Keep it simple:
    # Part inherits House, so house laws reach a.md and b.md and loose.md.
    bound = style_page._bindings(db, manuscript)
    check("a House law binds every file that inherits House",
          set(bound.get(house["id"], [])) >= {"a.md", "b.md", "loose.md"},
          bound.get(house["id"]))
    check("a Part law binds only files under Part",
          bound.get(part["id"]) == ["a.md"], bound.get(part["id"]))
    check("an unused citation still binds the files that hold House",
          set(bound.get(only_b["id"], [])) >= {"a.md", "b.md", "loose.md"},
          bound.get(only_b["id"]))
    # A file-local law binds only that file.
    local = api.add_style_law(db, manuscript, "formatting",
                              "Prefer short paragraphs.", file="b.md")
    bound = style_page._bindings(db, manuscript)
    check("a file-local law binds only its file",
          bound.get(local["id"]) == ["b.md"], bound.get(local["id"]))


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        test_replacement_roads(root)
        test_bindings(root)
    print(f"\n{PASSED} checks passed")


if __name__ == "__main__":
    main()
