"""Workspace style defaults: `library/style-defaults.toml` is the lowest
layer of every file's effective guide, and a manuscript law displaces a
default by naming `default:<key>` in `overrides` (a comma list displaces
several), so a book's own law always wins over a default.

Hermetic: AUTHORLM_STYLE_DEFAULTS points at a temp file, except the last
check, which parses the real defaults so a malformed row fails here.
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

from authorlm import api, styles
from authorlm.db import Database

DEFAULTS = '''
[[default]]
key = "dash"
aspect = "formatting"
statement = "Closed em dash."

[[default]]
key = "semicolons"
aspect = "formatting"
statement = "Semicolons allowed."

[[default]]
key = "possessive-s"
aspect = "formatting"
statement = "Charles'."
'''


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _setup(root: Path) -> tuple[Database, dict]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "defaults.toml").write_text(DEFAULTS, encoding="utf-8")
    os.environ[styles.DEFAULTS_ENV] = str(root / "defaults.toml")
    db = Database(root / "authorlm.db")
    ms_dir = root / "ms"
    ms_dir.mkdir()
    (ms_dir / "a.md").write_text("# A\n\nText.\n", encoding="utf-8")
    manuscript = dict(api.register_manuscript(db, "M", str(ms_dir)))
    styles.create_guide(db, manuscript["id"], "House")
    return db, manuscript


def _ids(db: Database, manuscript: dict) -> list[str]:
    return [e["id"] for e in styles.effective_style(db, manuscript["id"], "a.md")]


def test_defaults_are_lowest_layer(root: Path) -> None:
    db, ms = _setup(root)
    law = api.add_style_law(db, ms, "lexicon", "House word.", guide_name="House")
    ids = _ids(db, ms)
    check("house law first, defaults after in file order",
          ids == [law["id"], "default:dash", "default:semicolons",
                  "default:possessive-s"], ids)
    check("render shows a default",
          "Charles'." in styles.render(db, ms["id"], "a.md"))
    os.environ[styles.DEFAULTS_ENV] = str(root / "missing.toml")
    check("a missing defaults file is no defaults", _ids(db, ms) == [law["id"]])


def test_one_law_displaces_two_defaults(root: Path) -> None:
    db, ms = _setup(root)
    law = api.add_style_law(db, ms, "formatting", "No dashes, no semicolons.",
                            guide_name="House",
                            overrides="default:dash, default:semicolons")
    check("overrides stored as a comma list",
          law["overrides"] == "default:dash,default:semicolons", law["overrides"])
    ids = _ids(db, ms)
    check("the book's law wins; the untouched default stays",
          ids == [law["id"], "default:possessive-s"], ids)
    overview = api.style_overview(db, ms)
    by = {d["id"]: d["displaced_by"] for d in overview["defaults"]}
    check("overview names the displacing law",
          by["default:dash"] == [law["id"]] and by["default:possessive-s"] == [], by)


def test_unknown_default_named(root: Path) -> None:
    db, ms = _setup(root)
    try:
        api.add_style_law(db, ms, "formatting", "x", guide_name="House",
                          overrides="default:oxford")
    except LookupError as exc:
        check("unknown default fails loudly", "default:dash" in str(exc), exc)
    else:
        check("unknown default fails loudly", False)


def test_real_defaults_parse() -> None:
    os.environ.pop(styles.DEFAULTS_ENV, None)
    rows = styles.default_laws()
    keys = {r["default"] for r in rows}
    check("real defaults parse and carry the ratified keys",
          {"serial-comma", "possessive-s", "dash", "semicolons", "spelling"} <= keys,
          sorted(keys))
    check("every real default is a prose aspect",
          all(r["aspect"] in styles.PROSE_ASPECTS for r in rows))


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        test_defaults_are_lowest_layer(base / "1")
        test_one_law_displaces_two_defaults(base / "2")
        test_unknown_default_named(base / "3")
    test_real_defaults_parse()
    print("test_style_defaults: all checks passed")


if __name__ == "__main__":
    main()
