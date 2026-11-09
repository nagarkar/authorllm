"""Beat-by-beat repetition: the drafter is shown what earlier essays already
say about the beat's concepts, the critic and the lint read only the book
(never the files toc.toml declares outside), and a borrowed term set in
italics once stays italic.

    python3 tests/test_beat_repetition.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Offline, like every suite here: no real config, no .env, no client stamp.
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import api, concepts, critic, lint, writing  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402

TOC = '''outside = ["metaphysic.md", "papers/"]

[[chapter]]
file = "rebirth.md"

[[chapter]]
file = "whitehead.md"

[[chapter]]
file = "later.md"
'''

REBIRTH_DEFINES = ("A locus is the point where a history turns into a "
                   "choice.")
FILES = {
    "toc.toml": TOC,
    "rebirth.md": (
        "# Rebirth\n\n"
        "The essay opens on the wheel of lives.\n\n"
        f"{REBIRTH_DEFINES}\n\n"
        "Every locus carries its history. The locus chooses, and loci that "
        "align choose more coherently together.\n"),
    # The accepted text of the essay being written: it sets a borrowed
    # term in italics once.
    "whitehead.md": (
        "# Whitehead\n\n"
        "Whitehead's final real things are *actual occasions*.\n"),
    "later.md": (
        "# Later\n\n"
        "A locus is defined once more here, after the essay being "
        "written.\n"),
    "metaphysic.md": (
        "# Metaphysic\n\n"
        "The locus is the point of the spear at the active end of a "
        "trajectory, and each choice builds on the distinctions already "
        "in hand.\n"),
    "papers/axioms.md": (
        "# Axioms\n\n"
        "Each choice builds on the distinctions already in hand, the "
        "axioms say.\n"),
}


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


@contextlib.contextmanager
def _book():
    """A workspace over the fixture manuscript, with `Locus` in the graph
    (alias `loci`). Yields (db, manuscript, writeup, beat)."""
    root = Path(tempfile.mkdtemp(prefix="authorlm-repetition-"))
    try:
        ms = root / "manuscript"
        for name, text in FILES.items():
            (ms / name).parent.mkdir(parents=True, exist_ok=True)
            (ms / name).write_text(text)
        ws = root / "ws"
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms)])
        db = api.open_db(str(ws))
        manuscript = api.get_manuscript(db)
        node = concepts.add_concept(db, manuscript["id"], "Locus",
                                    notes="The active end of a trajectory.")
        concepts.add_alias(db, manuscript["id"], node, "loci")
        writeup = {"id": "wu-fixture", "file": "whitehead.md",
                   "learnings": "[]", "plan": "[]",
                   "source_version_id": None,
                   "metadata": json.dumps(
                       {"brief": "Whitehead's *prehension* is a feeling."})}
        beat = {"n": 3, "role": "occasions are choices",
                "concepts": ["locus"], "budget": 200}
        yield db, manuscript, writeup, beat
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_drafter_sees_the_earlier_essays_wording() -> None:
    with _book() as (db, manuscript, writeup, beat):
        block = writing._already_in_book(db, manuscript, writeup, beat)
        print("    --- ALREADY IN THE BOOK (fixture) ---")
        for line in block.splitlines():
            print(f"    {line}")
        check("the beat concept resolves through the graph, aliases shown",
              block.startswith("locus (also: loci)\n"), block[:80])
        check("the earlier essay's defining paragraph is shown, with file, "
              "paragraph number and the essay's name",
              '[rebirth.md ¶3 — the essay "Rebirth" — first defined here]'
              in block and REBIRTH_DEFINES in block, block)
        check("the fullest statement comes too",
              "[rebirth.md ¶4" in block and "most fully stated" in block,
              block)
        check("a later essay is not shown", "later.md" not in block, block)
        check("files declared outside are not shown",
              "metaphysic.md" not in block and "papers/" not in block
              and "point of the spear" not in block, block)
        check("the essay being written is not its own earlier essay",
              "whitehead.md" not in block, block)
        bare = writing._already_in_book(
            db, manuscript, writeup, {**beat, "concepts": ["prehension"]})
        check("a concept no earlier essay carries says so",
              bare == "prehension: no earlier essay states it.\n", bare)
        beat_block = writing._beat_block(db, manuscript, writeup, beat)
        check("block C carries the heading and the block",
              writing.ALREADY_HEADING in beat_block
              and REBIRTH_DEFINES in beat_block)
        check("the heading says plainly: cite by name, never restate",
              "Cite them by the essay's name" in writing.ALREADY_HEADING
              and "Never restate or re-define" in writing.ALREADY_HEADING)
        again = writing._already_in_book(db, manuscript, writeup, beat)
        check("the block is deterministic", again == block)


def test_critic_reads_only_the_book() -> None:
    with _book() as (db, manuscript, writeup, beat):
        draft = ("Each choice builds on the distinctions already in hand. "
                 "The locus is the point of the spear at the active end of "
                 "a trajectory. Loci that align choose more coherently "
                 "together.")
        files, order = critic.book(manuscript)
        check("the book leaves out outside files and the TOC",
              sorted(files) == ["later.md", "rebirth.md", "whitehead.md"],
              sorted(files))
        check("the book keeps the TOC's reading order",
              order == ["rebirth.md", "whitehead.md", "later.md"], order)
        paras, order = critic._book(manuscript, exclude="whitehead.md")
        near = critic.nearest_passages(draft, paras, order)
        check("nearest passages exclude the outside files",
              near and all(name in ("rebirth.md", "later.md")
                           for name, _k, _t, _s in near), near)
        report = critic.lint_draft(db, manuscript, "whitehead.md", draft,
                                   writeup=writeup)
        refs = [f.ref for f in report.findings if f.code == "overlap"]
        check("the lint corpus excludes the outside files: the Metaphysic's "
              "own sentence is no overlap", refs == [], refs)
        payload = critic.assemble(db, manuscript, writeup, beat, draft,
                                  report)
        check("the critic sees the drafter's ALREADY IN THE BOOK block",
              writing.ALREADY_HEADING in payload
              and writing._already_in_book(db, manuscript, writeup, beat)
              in payload)
        nearest = payload.split("NEAREST PASSAGES", 1)[1].split("LINT", 1)[0]
        check("the critic's NEAREST PASSAGES name no outside file",
              "metaphysic.md" not in nearest and "papers/" not in nearest,
              nearest)


def test_italic_terms_stay_italic() -> None:
    accepted = "Whitehead's final real things are *actual occasions*."
    brief = "Whitehead's *prehension* is a feeling."
    draft = (
        "Each actual occasion takes in the others.\n\n"
        "Here the *concrescence* begins; the concrescence ends in a "
        "decision.\n\n"
        "He calls it “a prehension of the actual occasion”, and so it is."
        "[^1]\n\n"
        "[^1]: Process and Reality, on the actual occasion and "
        "concrescence.\n\n"
        "On this occasion, the point *is* plain.")
    report = lint.lint_text(draft, accepted=accepted, brief=brief)
    found = [(f.unit, f.ref, f.severity) for f in report.findings
             if f.code == "italic-term"]
    check("a term italic in the accepted text, roman in the draft, is a "
          "warning (plural-tolerant)",
          (1, "actual occasions", "warning") in found, found)
    check("a term italic earlier in the draft, roman later, is a warning",
          (2, "concrescence", "warning") in found, found)
    check("quotations and footnotes are not flagged, and neither is the "
          "plain English word or emphasis",
          [u for u, _r, _s in found if u not in (1, 2)] == [], found)
    check("a term italic in the brief is not flagged where it is only "
          "quoted", not any(r == "prehension" for _u, r, _s in found), found)
    rendered = lint.render(report)
    check("render prints the warning with its message",
          "[warn] ¶1 italic-term: 'actual occasions' is italic in the "
          "accepted text but roman here" in rendered, rendered)
    roman = lint.lint_text("A prehension binds.", brief=brief)
    check("the brief seeds the check",
          [f.ref for f in roman.findings if f.code == "italic-term"]
          == ["prehension"], roman.findings)
    clean = lint.lint_text("The *actual occasion* is a drop. Each *actual "
                           "occasion* perishes.", accepted=accepted)
    check("italic on every use is clean",
          not [f for f in clean.findings if f.code == "italic-term"],
          clean.findings)


def test_write_critique_seeds_the_lint_from_the_writeup() -> None:
    with _book() as (db, manuscript, writeup, beat):
        report = critic.lint_draft(db, manuscript, "whitehead.md",
                                   "Each actual occasion is a choice. A "
                                   "prehension follows.", writeup=writeup)
        refs = sorted(f.ref for f in report.findings
                      if f.code == "italic-term")
        check("lint_draft reads the accepted text on disk and the brief",
              refs == ["actual occasions", "prehension"], refs)


def main() -> None:
    tests = [test_drafter_sees_the_earlier_essays_wording,
             test_critic_reads_only_the_book,
             test_italic_terms_stay_italic,
             test_write_critique_seeds_the_lint_from_the_writeup]
    for fn in tests:
        print(fn.__name__)
        fn()
    print(f"OK — {len(tests)} beat-repetition cases")


if __name__ == "__main__":
    main()
