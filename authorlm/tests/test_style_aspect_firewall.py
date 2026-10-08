"""Figure / illustration / illustration-placement must never cross prompts.

`styles` composition is shared; each consumer filters to one aspect:
`illus.illustration_law` for image renders, `placement.placement_law`
for the spot-finder. Mixing them paints prose metaphors into image
prompts (it-3e79bce24f73) or render craft into placement criteria.
This file seeds one file with all three aspects and asserts each door
keeps only its own statements.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import api, illus, placement, styles
from authorlm.cli import main as cli_main


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _fixture():
    root = Path(tempfile.mkdtemp(prefix="authorlm-aspect-firewall-"))
    ws = root / "ws"
    ms_path = ws / "book"
    ms_path.mkdir(parents=True)
    (ms_path / "01-chapter.md").write_text(
        "# Chapter\n\nGravity bends light.\n", encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms_path), "--no-extract"])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db, "book")
    return db, manuscript


def test_aspect_firewall() -> None:
    db, manuscript = _fixture()
    mid = manuscript["id"]
    file = "01-chapter.md"
    styles.add_element(
        db, mid, "figure",
        "Metaphors may lean on chains of causation.",
        file=file)
    styles.add_element(
        db, mid, "illustration",
        "Woodcut, high-contrast linework only.",
        file=file, notes="no colour wash")
    styles.add_element(
        db, mid, "illustration-placement",
        "At most one image per major turn.",
        file=file)

    effective = styles.effective_style(db, mid, file)
    check("effective_style carries all three aspects",
          {e["aspect"] for e in effective}
          == {"figure", "illustration", "illustration-placement"},
          [e["aspect"] for e in effective])

    law = illus.illustration_law(db, mid, file)
    check("illustration_law keeps illustration statement + notes",
          "Woodcut, high-contrast linework only." in law
          and "no colour wash" in law
          and "ILLUSTRATION STYLE" in law, law)
    check("illustration_law excludes figure and placement prose",
          "chains of causation" not in law
          and "major turn" not in law
          and "PLACEMENT LAW" not in law, law)
    check("style_hash is non-zero when illustration law is present",
          illus.style_hash(law) != "0000"
          and illus.style_hash("") == "0000")

    place = placement.placement_law(db, mid, file)
    check("placement_law keeps only illustration-placement",
          "At most one image per major turn." in place
          and "PLACEMENT LAW" in place, place)
    check("placement_law excludes figure and illustration craft",
          "chains of causation" not in place
          and "Woodcut" not in place
          and "ILLUSTRATION STYLE" not in place, place)

    empty = illus.illustration_law(db, mid, "missing.md")
    check("no illustration elements → empty law and 0000 hash",
          empty == "" and illus.style_hash(empty) == "0000", repr(empty))
    check("no placement elements → empty placement law",
          placement.placement_law(db, mid, "missing.md") == "")


def main() -> None:
    test_aspect_firewall()
    print("test_style_aspect_firewall: all checks passed")


if __name__ == "__main__":
    main()
