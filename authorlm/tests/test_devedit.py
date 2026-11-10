"""The developmental edit's run folder: coverage from the TOC, step staleness,
letter decisions, and verdicts that apply only to unchanged text.

    python3 tests/test_devedit.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import devedit  # noqa: E402

# A new run archives the earlier ones; in tests they go to a scratch folder,
# never the author's real archive.
devedit.ARCHIVE_ROOT = Path(tempfile.mkdtemp(prefix="devedit-archive-"))

TOC = '''outside = ["metaphysic.md"]

[[chapter]]
file = "beta.md"
matter = "front"

[[chapter]]
file = "one.md"

[[chapter]]
file = "two.md"

[[chapter]]
file = "papers.md"
matter = "back"
'''

FILES = {
    "beta.md": "# Beta\n\nThe book promises an argument.\n",
    "one.md": "# One\n\nThe Dharma is the ground. It moves all.\n",
    "two.md": "# Two\n\nThe Dharma returns here.\n",
    "papers.md": "# Papers\n\nThe Fields are two.\n",
    "metaphysic.md": "# Metaphysic\n\nOld text.\n",
    "stray.md": "# Stray\n\nNot in the TOC.\n",
}


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _manuscript(tmp: Path) -> dict:
    root = tmp / "MS"
    root.mkdir()
    (root / "toc.toml").write_text(TOC)
    for name, text in FILES.items():
        (root / name).write_text(text)
    return {"name": "MS", "id": "ms", "path": str(root)}


def _with_queue(folder: Path, items: list[dict]) -> None:
    (folder / "queue.json").write_text(json.dumps(items))


def test_coverage_follows_toc() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        cov = devedit.propose_coverage(devedit._disk_files(ms))
        check("every listed file is edited, front and back matter included, "
              "in reading order",
              cov["edit"] == ["beta.md", "one.md", "two.md", "papers.md"], cov["edit"])
        check("nothing starts as reference only", cov["reference"] == [], cov)
        check("outside and unlisted files are not covered",
              "metaphysic.md" not in cov["edit"] + cov["reference"]
              and "stray.md" not in cov["edit"] + cov["reference"])


def test_steps_wait_for_confirmed_coverage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        folder = devedit.new_run(ms, today="2026-11-06")
        try:
            devedit.mark_step(folder, ms, "stages", "done")
            check("a step cannot finish before coverage is confirmed", False)
        except RuntimeError:
            check("a step cannot finish before coverage is confirmed", True)
        devedit.confirm_coverage(folder)
        check("the next step after coverage is the stages",
              devedit.next_step(folder, ms) == "stages")


def test_done_step_goes_stale_when_a_covered_file_changes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        folder = devedit.new_run(ms)
        devedit.confirm_coverage(folder)
        devedit.mark_step(folder, ms, "stages", "done", executor="authorlm")
        rows = {r["step"]: r for r in devedit.status(folder, ms)}
        check("stages is done", rows["stages"]["status"] == "done")
        (Path(ms["path"]) / "two.md").write_text("# Two\n\nChanged.\n")
        rows = {r["step"]: r for r in devedit.status(folder, ms)}
        check("stages is stale after two.md changed",
              rows["stages"]["status"] == "stale"
              and rows["stages"]["changed"] == ["two.md"], rows["stages"])


def test_a_new_run_archives_the_old_one() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        a = devedit.new_run(ms, today="2026-11-06")
        devedit.confirm_coverage(a)
        devedit.note(a, "beta.md", "An old run's notebook.")
        b = devedit.new_run(ms, today="2026-11-06")
        archived = Path(devedit.state(b)["archived"][0])
        check("one run at a time: the old run left the manuscript folder",
              devedit.runs(ms) == ["2026-11-06"] and b.name == "2026-11-06"
              and archived.parent == devedit.ARCHIVE_ROOT / "MS", devedit.runs(ms))
        check("the old run was moved whole, never deleted",
              (archived / "notebook.md").exists())
        check("the new run starts from a clean slate",
              devedit.notebook(b) == "" and not (b / "stages").exists())
        devedit.new_run(ms, today="2026-11-06")
        check("a third run archives under a fresh name",
              len(list((devedit.ARCHIVE_ROOT / "MS").glob("devedit-2026-11-06*"))) >= 2)


def test_term_index_grows_from_finished_essays_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        root = Path(ms["path"])
        (root / "one.md").write_text("# One\n\nThe Dharma is the ground. "
                                     "The Dharma moves all.\n")
        (root / "two.md").write_text("# Two\n\nThe Chid returns. The Chid "
                                     "returns again.\n")
        folder = devedit.new_run(ms)
        devedit.confirm_coverage(folder)
        check("before any essay is finished the index is empty",
              devedit.term_index(folder, ms) == [])
        devedit.note(folder, "beta.md", "Nothing to change.")
        devedit.note(folder, "one.md", "Kept the definition.")
        rows = devedit.term_index(folder, ms)
        names = [r["term"] for r in rows]
        check("a finished essay's term is indexed", "Dharma" in names, names)
        dharma = next(r for r in rows if r["term"] == "Dharma")
        check("its definition-shaped sentence is found",
              any("is the ground" in d[2] for d in dharma["definitions"]))
        check("an essay not yet finished contributes nothing", "Chid" not in names, names)
        check("nothing is snapshotted to disk", not (folder / "terms.json").exists())


def test_decisions_are_binding_or_provisional() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        folder = devedit.new_run(ms)
        devedit.confirm_coverage(folder)
        devedit.stage_dir(folder).mkdir(parents=True)
        (devedit.stage_dir(folder) / "decisions.json").write_text(json.dumps(
            [{"n": 1, "question": "Split Chapter 1?", "options": ["yes", "no"]}]))
        row = devedit.decide(folder, 1, "no", "keep it whole", binding=False)
        check("the answer, reason and provisional flag are kept",
              row["answer"] == "no" and row["reason"] == "keep it whole"
              and row["binding"] is False and row["answered"])
        try:
            devedit.decide(folder, 1, "maybe")
            check("an answer outside the options is declined", False)
        except ValueError:
            check("an answer outside the options is declined", True)


def test_accept_applies_and_reject_needs_reason() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        folder = devedit.new_run(ms)
        _with_queue(folder, [
            {"id": "a1", "file": "one.md", "tier": "must",
             "original": "It moves all.", "proposed": "It moves everything."},
            {"id": "a2", "file": "two.md", "tier": "could",
             "original": "returns here", "proposed": "comes back here"}])
        check("must comes before could",
              [i["id"] for i in devedit.pending(folder)] == ["a1", "a2"])
        devedit.verdict(folder, ms, "a1", "accept", text="It moves the whole.")
        body = (Path(ms["path"]) / "one.md").read_text()
        check("the author's rewrite is what lands", "It moves the whole." in body, body)
        check("the file is waiting to be pushed",
              devedit.unpushed(folder) == ["one.md"])
        try:
            devedit.verdict(folder, ms, "a2", "reject")
            check("a rejection without a reason is declined", False)
        except ValueError:
            check("a rejection without a reason is declined", True)
        devedit.verdict(folder, ms, "a2", "reject", reason="the old word is right")
        check("two.md is untouched by a rejection",
              (Path(ms["path"]) / "two.md").read_text() == FILES["two.md"])
        check("the verdicts are logged",
              len((folder / "verdicts.jsonl").read_text().splitlines()) == 2)


def test_apply_refuses_changed_text() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        folder = devedit.new_run(ms)
        _with_queue(folder, [{"id": "b1", "file": "one.md", "tier": "must",
                              "original": "It moves all.",
                              "proposed": "It moves everything."}])
        (Path(ms["path"]) / "one.md").write_text("# One\n\nRewritten by hand.\n")
        try:
            devedit.verdict(folder, ms, "b1", "accept")
            check("an item whose original text is gone is not applied", False)
        except RuntimeError:
            check("an item whose original text is gone is not applied", True)
        check("the file keeps the author's text",
              "Rewritten by hand." in (Path(ms["path"]) / "one.md").read_text())
        check("the item stays undecided",
              not devedit.queue(folder)[0].get("verdict"))


def test_markdown_diff_bolds_additions_and_strikes_removals() -> None:
    out = devedit.markdown_diff("by the measure of your knowing",
                                "by how they choose")
    check("removed words are struck", "~~the measure of your knowing~~" in out, out)
    check("added words are bold", "**how they choose**" in out, out)
    check("shared words are plain", out.startswith("by "), out)


def test_gather_group_and_build_queue() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        folder = devedit.new_run(ms)
        devedit.confirm_coverage(folder)
        for f in ("beta.md", "one.md", "two.md"):
            devedit.note(folder, f, "Finished.")
        (folder / "findings").mkdir()
        (folder / "findings" / "one.json").write_text(json.dumps([
            {"finding": "Dharma defined twice", "letter_ref": "P1",
             "items": [{"tier": "must", "original": "The Dharma is the ground.",
                        "proposed": "The Dharma is the ground of all.",
                        "reason": "r"}]}]))
        (folder / "findings" / "two.json").write_text(json.dumps([
            {"finding": "Return unexplained", "items": [
                {"tier": "should", "original": "The Dharma returns here.",
                 "proposed": "The Dharma returns here, as promised.", "reason": "r"}]}]))
        items = devedit.gather(folder)
        check("items are numbered per file and finding",
              [i["id"] for i in items] == ["one-1.1", "two-1.1"], [i["id"] for i in items])
        groups = devedit.group(folder)
        check("both items share the term Dharma",
              any(g["kind"] == "term" and g["key"] == "Dharma"
                  and g["items"] == ["one-1.1", "two-1.1"] for g in groups), groups)
        (folder / "integrated.json").write_text(json.dumps(items))
        (folder / "checks").mkdir()
        (folder / "checks" / "one-1.1.json").write_text(json.dumps(
            {"result": "fix", "proposed": "The Dharma is the ground of all things.",
             "reason": "tighter"}))
        try:
            devedit.build_queue(folder)
            check("an unchecked item is held back and named", False)
        except RuntimeError as err:
            check("an unchecked item is held back and named", "two-1.1" in str(err))
        check("the checked item is queued with the checker's fix",
              devedit.queue(folder)[0]["proposed"]
              == "The Dharma is the ground of all things.")
        (folder / "checks" / "two-1.1.json").write_text(json.dumps(
            {"result": "drop", "reason": "answered elsewhere"}))
        devedit.build_queue(folder)
        check("a dropped item leaves the queue",
              [i["id"] for i in devedit.queue(folder)] == ["one-1.1"])


def test_front_matter_can_move_into_the_edit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        folder = devedit.new_run(ms)
        cov = devedit.move_coverage(folder, "beta.md", "reference")
        check("beta.md can be moved to reference only",
              cov["reference"] == ["beta.md"] and "beta.md" not in cov["edit"], cov)
        cov = devedit.move_coverage(folder, "beta.md", "edit")
        check("and back into the edit, in reading order",
              cov["edit"] == ["beta.md", "one.md", "two.md", "papers.md"]
              and "beta.md" not in cov["reference"], cov)
        try:
            devedit.move_coverage(folder, "metaphysic.md", "edit")
            check("an outside file cannot be brought in", False)
        except LookupError:
            check("an outside file cannot be brought in", True)
        devedit.confirm_coverage(folder)
        try:
            devedit.move_coverage(folder, "beta.md", "reference")
            check("confirmed coverage does not change", False)
        except RuntimeError:
            check("confirmed coverage does not change", True)


def test_packs_carry_style_audience_and_arcs() -> None:
    from authorlm import styles
    from authorlm.db import Database

    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        db = Database(Path(tmp) / "authorlm.db")
        guide = styles.create_guide(db, ms["id"], "Essays")
        styles.attach_file(db, ms["id"], "two.md", guide)
        styles.add_element(db, ms["id"], "structure",
                           "Each essay pursues one question.", guide=guide)
        styles.add_element(db, ms["id"], "illustration",
                           "Parchment ground for every image.", guide=guide)
        prof = Path(ms["path"]) / "_profiles"
        prof.mkdir()
        (prof / "audience.md").write_text("# Audience\n\nA common reader.\n")
        folder = devedit.new_run(ms)
        devedit.confirm_coverage(folder)
        (folder / "findings").mkdir()
        (folder / "findings" / "one.json").write_text(json.dumps(
            {"findings": [], "arcs": [{"case": "the cookie", "stage": "starts",
                                       "summary": "the easy case of sharing"}]}))
        ctx = "\n".join(devedit._standing_context(db, ms, "two.md"))
        check("the pack carries the file's prose style law",
              "Each essay pursues one question." in ctx, ctx)
        check("image law never reaches the pack",
              "Parchment" not in ctx, ctx)
        check("the audience profile is passed whole",
              "A common reader." in ctx, ctx)
        check("a missing profile is said, not silently left out",
              "(no _profiles/examples.md)" in ctx, ctx)
        names = devedit.covered(folder)
        arcs = "\n".join(devedit._earlier_arcs(folder, names, "two.md"))
        check("a later essay sees the arcs an earlier agent proposed",
              "the cookie" in arcs and "one.md" in arcs, arcs)
        check("the first essay sees no earlier arcs",
              devedit._earlier_arcs(folder, names, "one.md") == [])
        check("a findings object and a bare list read the same way",
              devedit._findings_of({"findings": [1], "arcs": []}) == [1]
              and devedit._findings_of([2]) == [2])
        (prof / "market.md").write_text("# Market\n\n## Comps\n\nWiman.\n")
        (prof / "synopsis.md").write_text("# Synopsis\n\nA stale reader.\n")
        ctx = "\n".join(devedit._standing_context(db, ms, "two.md"))
        check("the market profile is passed, its headings nested under the pack's",
              "#### Comps" in ctx and "Wiman." in ctx, ctx)
        check("the synopsis never reaches an agent", "stale reader" not in ctx, ctx)
        check("an agent reads a covered file",
              "returns here" in devedit.read(folder, ms, "two.md"))
        for outside in ("stray.md", "metaphysic.md", "_profiles/synopsis.md"):
            try:
                devedit.read(folder, ms, outside)
                check(f"{outside} is declined", False)
            except LookupError:
                check(f"{outside} is declined", True)
        book = "\n".join(devedit._standing_context(db, ms, None))
        check("the book pack carries the audience but no file's style law",
              "A common reader." in book and "Style law" not in book, book)


def _declined(label: str, fn, *needles: str) -> None:
    try:
        fn()
    except LookupError as err:
        check(label, all(n in str(err) for n in needles), err)
        return
    check(label, False, "it was read")


def test_read_follows_only_references_that_are_there() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        root = Path(ms["path"])
        for d in ("_drafts", "_critiques", "_exports", "_illustrations"):
            (root / d).mkdir()
        (root / "_drafts" / "b.md").write_text("# B\n\nSee c.md for more.\n")
        (root / "_drafts" / "c.md").write_text("# C\n\nDeeper.\n")
        (root / "_drafts" / "hidden.md").write_text("# Hidden\n\nOnly a note names me.\n")
        (root / "_drafts" / "audio-only.md").write_text("# Audio\n\nNamed for audio.\n")
        (root / "_drafts" / "stale-review.md").write_text("# Review\n\nNobody names me.\n")
        (root / "_critiques" / "2026-11-01-review.md").write_text("# Critique\n")
        (root / "_exports" / "book.md").write_text("# Export\n")
        (root / "two.md").write_text(
            "# Two\n\nThe Dharma returns here; see b.md.\n\n"
            "[Omit: all]\n\nWorking notes: hidden.md, old-run letter.md.\n\n[/Omit]\n\n"
            "[Omit: audio]\n\nOn the page, see audio-only.md.\n\n[/Omit]\n")
        old = devedit.new_run(ms, today="2026-10-01")
        devedit.confirm_coverage(old)
        (old / "letter.md").write_text("# An old run's letter\n")
        (root / "one.md").write_text("# One\n\nThe Dharma is the ground. "
                                     "It moves all. Compare _devedit/2026-10-01/letter.md.\n")
        folder = devedit.new_run(ms)
        devedit.confirm_coverage(folder)
        rd = lambda f, via=None, **kw: devedit.read(folder, ms, f, via=via, **kw)

        print("  core sources read directly:")
        check("a covered file reads", "returns here" in rd("two.md"))
        check("a covered file's section reads", "ground" in rd("one.md", section=1))
        check("the run's own files read", "{" in rd(f"_devedit/{folder.name}/state.json"))
        listing = devedit.sources_listing(folder, ms)
        check("the listing names only core sources, never a draft",
              "two.md" in listing and "_drafts" not in listing, listing)

        print("  files nobody names stay closed:")
        _declined("a review draft nobody names is declined",
                  lambda: rd("_drafts/stale-review.md"), "not a core source")
        _declined("a critique is declined", lambda: rd("_critiques/2026-11-01-review.md"))
        _declined("an export is declined", lambda: rd("_exports/book.md"))
        _declined("a file outside the TOC is declined", lambda: rd("stray.md"))
        _declined("a path out of the manuscript folder is declined",
                  lambda: rd("../outside.md", via=["two.md"]), "does not name")
        _declined("the same draft claimed via a covered file that does not name it",
                  lambda: rd("_drafts/stale-review.md", via=["two.md"]),
                  "'two.md' does not name")

        print("  A names B, so B opens from A, and only from A:")
        check("B reads via A, by bare name", "See c.md" in rd("b.md", via=["two.md"]))
        check("B reads via A, by path", "See c.md" in rd("_drafts/b.md", via=["two.md"]))
        _declined("B without the reference is declined",
                  lambda: rd("_drafts/b.md"), "--via")
        _declined("B claimed from a covered file that does not name it is an error",
                  lambda: rd("b.md", via=["one.md"]), "'one.md' does not name 'b.md'")
        _declined("a via that is not a core source is an error",
                  lambda: rd("b.md", via=["_drafts/c.md"]), "not a covered file")
        (folder / "book-pack.md").write_text("# Pack\n\nQuotes stale-review.md.\n")
        _declined("a run's generated file opens nothing, even when it names a draft",
                  lambda: rd("stale-review.md", via=[f"_devedit/{folder.name}/book-pack.md"]),
                  "generated")

        print("  chains hold link by link:")
        check("C reads via A then B", "Deeper." in rd("c.md", via=["two.md", "b.md"]))
        _declined("C claimed straight from A is an error",
                  lambda: rd("c.md", via=["two.md"]), "'two.md' does not name 'c.md'")
        _declined("a chain with a false middle link is an error",
                  lambda: rd("c.md", via=["two.md", "stale-review.md"]),
                  "'two.md' does not name 'stale-review.md'")

        print("  what an [Omit: all] note names opens nothing:")
        _declined("a draft named only in an [Omit: all] note is declined",
                  lambda: rd("hidden.md", via=["two.md"]), "does not name")
        check("a file named in an [Omit: audio] region still opens (it is printed)",
              "Named for audio." in rd("audio-only.md", via=["two.md"]))

        print("  other runs stay closed even when named:")
        _declined("another run's letter is declined even when a source names it",
                  lambda: rd("_devedit/2026-10-01/letter.md", via=["one.md"]),
                  "does not name")
        (Path(tmp) / "outside.md").write_text("# Outside the manuscript\n")
        (root / "two.md").write_text((root / "two.md").read_text()
                                     + "\nAlso ../outside.md.\n")
        _declined("a file outside the manuscript folder stays closed even when named",
                  lambda: rd("../outside.md", via=["two.md"]), "does not name")
        _declined("--section is for covered files only",
                  lambda: rd("b.md", via=["two.md"], section=1), "covered file only")


def test_essays_go_one_at_a_time_through_the_notebook() -> None:
    from authorlm.db import Database

    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        db = Database(Path(tmp) / "authorlm.db")
        folder = devedit.new_run(ms)
        devedit.confirm_coverage(folder)
        try:
            devedit.essay_pack(db, ms, folder, "two.md")
            check("an essay waits for the one before it", False)
        except RuntimeError as err:
            check("an essay waits for the first earlier essay not finished",
                  "waits for beta.md" in str(err), err)
        check("a trial pack runs out of order",
              "Trial pack" in devedit.essay_pack(db, ms, folder, "two.md", trial=True))
        devedit.note(folder, "beta.md", "Nothing to change.")
        devedit.note(folder, "one.md", "Moved the definition of the Dharma up. "
                                       "The author kept 'ground'.")
        check("the essay is finished once it has its entry",
              devedit.finished(folder) == ["beta.md", "one.md"])
        pack = devedit.essay_pack(db, ms, folder, "two.md")
        check("the next essay's pack carries the notebook",
              "kept 'ground'" in pack and "### one.md —" in pack
              and pack.count("Editor's notebook") == 1, pack[:400])
        check("a finished essay's next pass is a rerun, with its own findings file",
              devedit.pass_of(folder, "one.md") == 2
              and devedit.findings_name(folder, "one.md") == "one--p2"
              and devedit.findings_name(folder, "two.md") == "two")
        devedit.note(folder, "one.md", "Second pass: the open finding is addressed.")
        check("each pass gets its own entry, numbered",
              "— pass 2" in devedit.notebook(folder)
              and devedit.finished(folder) == ["beta.md", "one.md"])
        (folder / "items.json").write_text(json.dumps([{
            "id": "one-1.1", "file": "one.md", "tier": "should",
            "original": "It moves all.", "proposed": "It moves everything.",
            "order": 1}]))
        (folder / "checks").mkdir()
        (folder / "checks" / "one-1.1.json").write_text(json.dumps(
            {"result": "pass", "reason": "ok"}))
        check("the queue builds from gathered items before any integration",
              [i["id"] for i in devedit.build_queue(folder)] == ["one-1.1"])


STAGED_TOC = '''outside = ["metaphysic.md"]

[[chapter]]
file = "title.md"
matter = "front"

[[chapter]]
file = "preface.md"
matter = "front"

[[chapter]]
file = "part1.md"

[[chapter]]
file = "a.md"
parent = "part1.md"

[[chapter]]
file = "b.md"
parent = "part1.md"

[[chapter]]
file = "part2.md"

[[chapter]]
file = "c.md"
parent = "part2.md"

[[chapter]]
file = "about.md"
matter = "back"
'''


def _staged(tmp: Path) -> dict:
    root = tmp / "MS"
    root.mkdir()
    (root / "toc.toml").write_text(STAGED_TOC)
    for name, text in {"title.md": "# Title\n", "preface.md": "# Preface\n\nWhy.\n",
                       "part1.md": "# Part I: First\n", "a.md": "# A\n\nAlpha.\n",
                       "b.md": "# B\n\nBeta.\n", "part2.md": "# Part II: Second\n",
                       "c.md": "# C\n\nGamma.\n", "about.md": "# About\n",
                       "metaphysic.md": "# The Metaphysic\n"}.items():
        (root / name).write_text(text)
    return {"name": "MS", "id": "ms", "path": str(root)}


def test_stages_come_from_the_toc_and_close_in_order() -> None:
    from authorlm.db import Database

    with tempfile.TemporaryDirectory() as tmp:
        ms = _staged(Path(tmp))
        db = Database(Path(tmp) / "authorlm.db")
        folder = devedit.new_run(ms)
        devedit.confirm_coverage(folder)
        rows = devedit.stages(folder)
        check("front matter, then one stage per part, a lone back-matter file "
              "joining the last",
              [(s["name"], s["files"]) for s in rows] == [
                  ("Front matter", ["title.md", "preface.md"]),
                  ("Part I: First", ["part1.md", "a.md", "b.md"]),
                  ("Part II: Second", ["part2.md", "c.md", "about.md"])], rows)
        for f in ("title.md", "preface.md"):
            devedit.note(folder, f, "Done.")
        try:
            devedit.essay_pack(db, ms, folder, "part1.md")
            check("a stage's first essay waits for the stage before to close", False)
        except RuntimeError as err:
            check("a stage's first essay waits for the stage before to close",
                  "stage 0 (Front matter) is not closed" in str(err), err)
        try:
            devedit.close_stage(folder, 0)
            check("a stage closes only with its letter", False)
        except RuntimeError as err:
            check("a stage closes only with its letter", "no letter" in str(err), err)
        d = devedit.stage_dir(folder, 0)
        d.mkdir(parents=True)
        (d / "letter.md").write_text("# Letter for the front matter\n\nThe Preface promises.\n")
        (d / "decisions.json").write_text(json.dumps(
            [{"n": 1, "question": "Keep the epigraph?", "options": ["yes", "no"]}]))
        try:
            devedit.close_stage(folder, 0)
            check("and with every decision answered", False)
        except RuntimeError as err:
            check("and with every decision answered", "unanswered" in str(err), err)
        devedit.decide(folder, 1, "yes", stage=0)
        try:
            devedit.close_stage(folder, 1)
            check("only the current stage can close", False)
        except ValueError:
            check("only the current stage can close", True)
        devedit.close_stage(folder, 0)
        check("closed, the next stage opens",
              devedit.current_stage(folder)["n"] == 1
              and "Part I" in devedit.essay_pack(db, ms, folder, "part1.md"))
        devedit.note(folder, "part1.md", "Done.")
        pack = devedit.essay_pack(db, ms, folder, "a.md")
        check("an essay's pack holds the finished essays' summaries, later ones by "
              "title only",
              "## The essays already edited" in pack and "### preface.md" in pack
              and "- c.md: C" in pack and "### c.md" not in pack, pack)
        book = devedit.book_pack(db, ms, folder, 0)
        check("a stage's book pack never reaches the stages still to come",
              "## preface.md" in book and "## a.md" not in book, book[:600])
        try:
            devedit.close_stage(folder, 1)
            check("a stage with unfinished essays does not close", False)
        except RuntimeError as err:
            check("a stage with unfinished essays does not close",
                  "a.md" in str(err), err)


def test_the_ledger_holds_the_standard_across_passes() -> None:
    from authorlm.db import Database

    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        db = Database(Path(tmp) / "authorlm.db")
        folder = devedit.new_run(ms)
        devedit.confirm_coverage(folder)
        (folder / "findings").mkdir()
        (folder / "findings" / "one.json").write_text(json.dumps({"findings": [
            {"finding": "Dharma defined late", "items": [
                {"tier": "must", "original": "The Dharma is the ground.",
                 "proposed": "The Dharma, the ground, comes first.", "reason": "r"}]},
            {"finding": "Opening too abstract", "items": [
                {"tier": "should", "original": "It moves all.",
                 "proposed": "It moves everything we know.", "reason": "r"}]},
            {"finding": "Needs a case", "items": [
                {"tier": "could", "move": "add a scene before the definition",
                 "reason": "r"}]},
            {"finding": "Footnote too long", "items": [
                {"tier": "could", "original": "The Dharma is the ground.",
                 "proposed": "x", "reason": "r"}]}], "arcs": []}))
        devedit.gather(folder)
        (folder / "checks").mkdir()
        for i, result in (("one-1.1", "pass"), ("one-2.1", "pass"),
                          ("one-3.1", "pass"), ("one-4.1", "drop")):
            (folder / "checks" / f"{i}.json").write_text(json.dumps(
                {"result": result, "reason": "ok"}))
        devedit.build_queue(folder)
        q = {i["id"]: i for i in devedit.queue(folder)}
        q["one-1.1"].update(verdict="accept")
        q["one-2.1"].update(verdict="reject", reason_given="the abstraction is the point")
        _with_queue(folder, list(q.values()))
        rows = {r["finding"]: r for r in devedit.ledger(folder, "one.md")}
        check("accepted is addressed", rows["Dharma defined late"]["status"] == "addressed")
        check("rejected with a reason is declined, the reason kept",
              rows["Opening too abstract"]["status"] == "declined"
              and rows["Opening too abstract"]["reasons"] == ["the abstraction is the point"])
        check("undecided is open", rows["Needs a case"]["status"] == "open")
        check("dropped by the checker is dropped",
              rows["Footnote too long"]["status"] == "dropped", rows)
        devedit.note(folder, "beta.md", "Done.")
        devedit.note(folder, "one.md", "Pass 1 done.")
        pack = devedit.essay_pack(db, ms, folder, "one.md")
        check("a rerun's pack says its pass and its findings file",
              "Pass 2 on this essay" in pack and "findings/one--p2.json" in pack, pack[:300])
        check("and carries the ledger, the author's reason with it",
              "## The ledger for this essay (pass 2)" in pack
              and "[declined]" in pack and "the abstraction is the point" in pack
              and "never reverse" in pack, pack)
        (folder / "findings" / "one--p2.json").write_text(json.dumps({"findings": [
            {"finding": "Needs a case", "items": [
                {"tier": "could", "original": "It moves all.",
                 "proposed": "It moves all, as a river moves a stone.", "reason": "r"}]}]}))
        items = devedit.gather(folder)
        check("a rerun's items keep their essay and their own ids",
              any(i["id"] == "one--p2-1.1" and i["file"] == "one.md" for i in items),
              [i["id"] for i in items])


def test_audit_finds_what_must_be_fixed_first() -> None:
    from authorlm import styles
    from authorlm.db import Database

    with tempfile.TemporaryDirectory() as tmp:
        ms = _staged(Path(tmp))
        db = Database(Path(tmp) / "authorlm.db")
        folder = devedit.new_run(ms)
        guide = styles.create_guide(db, ms["id"], "Essays")
        for f in ("title.md", "preface.md", "part1.md", "a.md", "b.md", "part2.md",
                  "c.md"):
            styles.attach_file(db, ms["id"], f, guide)
        gone = styles.add_element(db, ms["id"], "structure", "Old rule.", guide=guide)
        styles.retire_element(db, gone)
        styles.add_element(db, ms["id"], "structure",
                           f"Follow {gone['id'][:8]} where it applies.", guide=guide)
        styles.add_element(db, ms["id"], "structure",
                           "The essays apply; the Metaphysic formalizes.", guide=guide)
        prof = Path(ms["path"]) / "_profiles"
        prof.mkdir()
        (prof / "audience.md").write_text("# Audience\n\nA common reader.\n")
        (prof / "market.md").write_text("# Market\n\nSee metaphysic.md.\n")
        problems = devedit.audit(db, ms, folder)
        text = "\n".join(problems)
        check("a missing profile is found", "_profiles/examples.md" in text, text)
        check("an edited file with no style guide is found",
              "no style guide attached: about.md" in text, text)
        check("a rule citing a retired rule is found", "which is retired" in text, text)
        check("a rule naming an outside file by its title is found",
              "'Metaphysic'" in text and "outside the book" in text, text)
        check("a profile naming an outside file by name is found",
              "_profiles/market.md names 'metaphysic.md'" in text, text)


def test_a_decision_with_edits_applies_when_answered() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        folder = devedit.new_run(ms)
        devedit.confirm_coverage(folder)
        devedit.stage_dir(folder).mkdir(parents=True)
        (devedit.stage_dir(folder) / "decisions.json").write_text(json.dumps([
            {"n": 1, "question": "Name the ground?", "options": ["yes", "no"],
             "edits": {"yes": [{"file": "one.md", "original": "The Dharma is the ground.",
                                "proposed": "The Dharma is the Ground of Creation.",
                                "why": "Use the Sermons' one name"}]}},
            {"n": 2, "question": "Draft a clause?", "options": ["draft", "leave"]},
            {"n": 3, "question": "Gone text?", "options": ["fix"],
             "edits": {"fix": [{"file": "two.md", "original": "Not there.",
                                "proposed": "x"}]}}]))
        row = devedit.decide_and_apply(folder, ms, 1, "yes")
        body = (Path(ms["path"]) / "one.md").read_text()
        check("answering applies the option's edit to the local file",
              "Ground of Creation" in body and row["applied"][0]["id"] == "stage0-d1.1", row)
        q = {i["id"]: i for i in devedit.queue(folder)}
        check("and records it as an accepted, applied item, so it is pushed and "
              "in the ledger",
              q["stage0-d1.1"]["verdict"] == "accept" and q["stage0-d1.1"]["applied"]
              and "one.md" in devedit.unpushed(folder), q)
        row = devedit.decide_and_apply(folder, ms, 2, "draft")
        check("an option without edits applies nothing (its essay is rerun)",
              row["applied"] == [] and row["answer"] == "draft", row)
        row = devedit.decide_and_apply(folder, ms, 3, "fix")
        check("an edit whose original is gone is reported, not forced",
              "error" in row["applied"][0] and "x" not in (Path(ms["path"]) / "two.md").read_text(),
              row)


def test_a_verdict_can_be_revised() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        folder = devedit.new_run(ms)
        items = [{"id": "r1.1", "file": "one.md", "tier": "must",
                  "original": "It moves all.", "proposed": "It moves everything.",
                  "finding": "f1"},
                 {"id": "r2.1", "file": "two.md", "tier": "should",
                  "original": "returns here", "proposed": "comes back here",
                  "finding": "f2"}]
        (folder / "items.json").write_text(json.dumps(items))
        _with_queue(folder, items)
        one, two = Path(ms["path"]) / "one.md", Path(ms["path"]) / "two.md"
        status = lambda key: next(r["status"] for r in devedit.ledger(folder)
                                  if r["key"] == key)
        try:
            devedit.revise(folder, ms, "r1.1", "reject", reason="x")
            check("an undecided item is not revised", False)
        except ValueError:
            check("an undecided item is not revised", True)

        devedit.verdict(folder, ms, "r2.1", "reject", reason="old word")
        check("ledger: rejected is declined", status("r2") == "declined")
        devedit.revise(folder, ms, "r2.1", "accept")
        check("reject→accept applies", "comes back here" in two.read_text())
        check("ledger follows: addressed", status("r2") == "addressed")
        check("the revised file waits for a push", "two.md" in devedit.unpushed(folder))

        devedit.verdict(folder, ms, "r1.1", "accept")
        devedit.mark_pushed(folder, "one.md")
        try:
            devedit.revise(folder, ms, "r1.1", "reject")
            check("undoing to reject needs a reason", False)
        except ValueError:
            check("undoing to reject needs a reason", True)
        devedit.revise(folder, ms, "r1.1", "accept", text="It moves the whole.")
        check("accept with new text swaps", "It moves the whole." in one.read_text()
              and "everything" not in one.read_text(), one.read_text())
        check("the swap waits for a push", "one.md" in devedit.unpushed(folder))
        devedit.mark_pushed(folder, "one.md")
        devedit.revise(folder, ms, "r1.1", "reject", reason="keep it plain")
        check("accept→reject undoes the change", one.read_text() == FILES["one.md"],
              one.read_text())
        check("the undo waits for a push", "one.md" in devedit.unpushed(folder))
        check("ledger follows: declined", status("r1") == "declined")
        devedit.revise(folder, ms, "r1.1", "defer")
        check("ledger: deferred is open", status("r1") == "open")

        devedit.revise(folder, ms, "r1.1", "accept")
        one.write_text("# One\n\nRewritten by hand.\n")
        try:
            devedit.revise(folder, ms, "r1.1", "defer")
            check("an undo whose applied text is gone is refused", False)
        except RuntimeError:
            check("an undo whose applied text is gone is refused", True)
        check("the hand edit is kept", "Rewritten by hand." in one.read_text())
        check("the verdict stays accept",
              next(i for i in devedit.queue(folder) if i["id"] == "r1.1")["verdict"] == "accept")
        log = [json.loads(l) for l in (folder / "verdicts.jsonl").read_text().splitlines()]
        check("revisions are logged as revisions",
              sum(1 for l in log if l.get("revision")) == 5, log)
        check("decided lists newest first",
              devedit.decided(folder)[0]["id"] == "r1.1")


def test_decided_shows_only_the_current_stage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _staged(Path(tmp))
        folder = devedit.new_run(ms)
        devedit.confirm_coverage(folder)
        mk = lambda id_, f: {"id": id_, "file": f, "tier": "must", "original": "x",
                             "proposed": "y", "verdict": "accept",
                             "decided": f"2026-01-01T00:00:0{len(id_) % 10}"}
        _with_queue(folder, [mk("r1.1", "preface.md"), mk("stage0-d1.1", "a.md"),
                             mk("r2.1", "a.md"), mk("stage1-d2.1", "c.md"),
                             {"id": "r3.1", "file": "b.md", "tier": "must",
                              "original": "x", "proposed": "y"}])
        ids = lambda **kw: sorted(i["id"] for i in devedit.decided(folder, **kw))
        check("before any stage closes, the front matter's items show",
              ids() == ["r1.1", "stage0-d1.1"], ids())
        st = devedit.state(folder)
        st["stages"] = {"0": {"closed": "2026-01-02"}}
        devedit._save_state(folder, st)
        check("a closed stage's items drop out; the current stage's items and "
              "its letter-decision items stay",
              ids() == ["r2.1", "stage1-d2.1"], ids())
        check("every=True lists the whole run",
              ids(every=True) == ["r1.1", "r2.1", "stage0-d1.1", "stage1-d2.1"])


def test_the_card_shows_the_whole_paragraph() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        (Path(ms["path"]) / "one.md").write_text(
            "# One\n\nFirst para stays.\n\nThe Dharma is the ground. It moves all.\n"
            "Still the same para.\n\nSecond para here.\n\nLast one.\n")
        frag = devedit._for_page({"id": "r1", "file": "one.md", "tier": "must",
                                  "original": "is the ground", "proposed": "is the soil"}, ms)
        check("a fragment comes with its whole paragraph, as it reads now",
              frag["paragraph"] == "The Dharma is the ground. It moves all.\nStill the same para.",
              frag)
        check("and with the proposed text in place",
              frag["paragraph_proposed"] == "The Dharma is the soil. It moves all.\nStill the same para."
              and frag["prefix"] == "The Dharma " and frag["suffix"].startswith(". It moves"), frag)
        multi = devedit._for_page({"id": "r2", "file": "one.md", "tier": "must",
                                   "original": "para.\n\nSecond", "proposed": "para. Second"}, ms)
        check("an original across paragraphs brings all of them",
              multi["paragraph"] == "The Dharma is the ground. It moves all.\n"
              "Still the same para.\n\nSecond para here."
              and multi["paragraph_proposed"].endswith("same para. Second para here."), multi)
        gone = devedit._for_page({"id": "r3", "file": "one.md", "tier": "must",
                                  "original": "not in the file", "proposed": "x"}, ms)
        check("a passage no longer in the file says so",
              gone.get("context_missing") and "paragraph" not in gone, gone)
        move = devedit._for_page({"id": "r4", "file": "one.md", "tier": "must",
                                  "move": "move the second para up"}, ms)
        check("a move item is unchanged", "paragraph" not in move and "context_missing" not in move)


def main() -> None:
    tests = [test_coverage_follows_toc,
             test_steps_wait_for_confirmed_coverage,
             test_done_step_goes_stale_when_a_covered_file_changes,
             test_a_new_run_archives_the_old_one,
             test_term_index_grows_from_finished_essays_only,
             test_decisions_are_binding_or_provisional,
             test_accept_applies_and_reject_needs_reason,
             test_apply_refuses_changed_text,
             test_markdown_diff_bolds_additions_and_strikes_removals,
             test_gather_group_and_build_queue,
             test_front_matter_can_move_into_the_edit,
             test_packs_carry_style_audience_and_arcs,
             test_read_follows_only_references_that_are_there,
             test_essays_go_one_at_a_time_through_the_notebook,
             test_stages_come_from_the_toc_and_close_in_order,
             test_the_ledger_holds_the_standard_across_passes,
             test_audit_finds_what_must_be_fixed_first,
             test_a_decision_with_edits_applies_when_answered,
             test_a_verdict_can_be_revised,
             test_decided_shows_only_the_current_stage,
             test_the_card_shows_the_whole_paragraph]
    for fn in tests:
        print(fn.__name__)
        fn()
    print(f"OK — {len(tests)} developmental-edit cases")


if __name__ == "__main__":
    main()
