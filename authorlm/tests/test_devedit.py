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
        check("main matter is edited, in reading order",
              cov["edit"] == ["one.md", "two.md"], cov["edit"])
        check("front and back matter are reference only",
              cov["reference"] == ["beta.md", "papers.md"], cov["reference"])
        check("outside and unlisted files are not covered",
              "metaphysic.md" not in cov["edit"] + cov["reference"]
              and "stray.md" not in cov["edit"] + cov["reference"])


def test_steps_wait_for_confirmed_coverage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        folder = devedit.new_run(ms, today="2026-11-06")
        try:
            devedit.mark_step(folder, ms, "terms", "done")
            check("a step cannot finish before coverage is confirmed", False)
        except RuntimeError:
            check("a step cannot finish before coverage is confirmed", True)
        devedit.confirm_coverage(folder)
        check("the next step after coverage is summaries",
              devedit.next_step(folder, ms) == "summaries")


def test_done_step_goes_stale_when_a_covered_file_changes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        folder = devedit.new_run(ms)
        devedit.confirm_coverage(folder)
        devedit.mark_step(folder, ms, "terms", "done", executor="authorlm")
        rows = {r["step"]: r for r in devedit.status(folder, ms)}
        check("terms is done", rows["terms"]["status"] == "done")
        (Path(ms["path"]) / "two.md").write_text("# Two\n\nChanged.\n")
        rows = {r["step"]: r for r in devedit.status(folder, ms)}
        check("terms is stale after two.md changed",
              rows["terms"]["status"] == "stale"
              and rows["terms"]["changed"] == ["two.md"], rows["terms"])


def test_second_run_same_day_gets_its_own_folder() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        a = devedit.new_run(ms, today="2026-11-06")
        b = devedit.new_run(ms, today="2026-11-06")
        check("two runs, two folders", a.name == "2026-11-06"
              and b.name == "2026-11-06-2", (a.name, b.name))
        check("the latest run is the default", devedit.run_dir(ms) == b)


def test_term_index_covers_only_covered_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        folder = devedit.new_run(ms)
        devedit.confirm_coverage(folder)
        rows = devedit.term_index(folder, ms)
        dharma = next((r for r in rows if r["term"] == "Dharma"), None)
        check("Dharma is indexed across two files",
              dharma is not None and dharma["files"] == ["one.md", "two.md"], dharma)
        check("its definition-shaped sentence is found",
              dharma and any("is the ground" in d[2] for d in dharma["definitions"]))
        check("the snapshot is written", (folder / "terms.json").exists())


def test_decisions_are_binding_or_provisional() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms = _manuscript(Path(tmp))
        folder = devedit.new_run(ms)
        (folder / "decisions.json").write_text(json.dumps(
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
        devedit.term_index(folder, ms)
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
        cov = devedit.move_coverage(folder, "beta.md", "edit")
        check("beta.md is edited, in reading order",
              cov["edit"] == ["beta.md", "one.md", "two.md"]
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


def main() -> None:
    tests = [test_coverage_follows_toc,
             test_steps_wait_for_confirmed_coverage,
             test_done_step_goes_stale_when_a_covered_file_changes,
             test_second_run_same_day_gets_its_own_folder,
             test_term_index_covers_only_covered_files,
             test_decisions_are_binding_or_provisional,
             test_accept_applies_and_reject_needs_reason,
             test_apply_refuses_changed_text,
             test_markdown_diff_bolds_additions_and_strikes_removals,
             test_gather_group_and_build_queue,
             test_front_matter_can_move_into_the_edit]
    for fn in tests:
        print(fn.__name__)
        fn()
    print(f"OK — {len(tests)} developmental-edit cases")


if __name__ == "__main__":
    main()
