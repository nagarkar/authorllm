"""The old roads retired as entry points (workflow/roads.py), and lint
versus copyedit (§11.9 Q2, U8, U9; §12 row 6).

- `filter run`, `filter prelude`, `lens run`, `write start` and the
  Filters view's run requests send the author to the essay's workflow,
  saying how to start or continue it.
- Run anyway only with a skip of one sentence of reason, recorded as a
  `skip` decision on the essay's open instance (a refine instance is
  started when there is none, a new one for an essay that does not exist
  yet, the decision saying why).
- At the road's own step (filters for filters and lenses, construction
  beat by beat for the beat loop) the road opens with no word.
- `book lint` owns quote marks, dash form, spelling by dictionary and
  footnote anchors; `--fix` applies the mechanical fixes directly and
  lists them; the copyedit brief never names what lint owns.

Hermetic: a temp workspace; any Google or model call fails the test.

    python3 tests/test_retired_roads.py
"""

from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import contextlib  # noqa: E402
import io  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import api, changes, gdocs, llm, proof, runs_page  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.workflow import core, roads  # noqa: E402

REPO = Path(__file__).resolve().parents[1]

TOC = '''[[chapter]]
file = "one.md"

[[chapter]]
file = "two.md"
'''

ONE = ("# One\n\nThe answer -- if there is one -- waits. A well-known case, "
       "pp. 3-5, from 1914–1918.\n\nHe said \"nothing\" and left - briefly."
       "\n\nSee [the page](https://example.com/a?utm_source=x&id=2).\n")
TWO = "# Two\n\nThe second essay.\n"
FILTER = '---\nclass = "sequential"\n---\n\n# Own pass\n\nThe prompt.\n'
LENS = "# Plain reader\n\nFlag what a plain reader would stumble on.\n"


def check(label: str, cond: bool, detail: object = "") -> None:
    print(f"  [{'ok' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


@contextlib.contextmanager
def world():
    saved = (gdocs.get_service, gdocs.get_docs_service, llm.LLMClient)

    def boom(*_a, **_k):
        raise AssertionError("an outside service was asked for")
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        ms = ws / "MS"
        ms.mkdir(parents=True)
        (ms / "toc.toml").write_text(TOC)
        (ms / "one.md").write_text(ONE)
        (ms / "two.md").write_text(TWO)
        (ms / "_filters").mkdir()
        (ms / "_filters" / "own-pass.md").write_text(FILTER)
        (ms / "_lenses").mkdir()
        (ms / "_lenses" / "own-lens.md").write_text(LENS)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                cli_main(["--workspace", str(ws), "init", "--name", "MS",
                          "--path", str(ms), "--extraction", "off"])
            gdocs.get_service = gdocs.get_docs_service = boom
            llm.LLMClient = boom
            db = api.open_db(str(ws))
            man = dict(db.one("SELECT * FROM manuscripts WHERE name = 'MS'"))
            yield db, man, ws, Path(man["path"])
        finally:
            gdocs.get_service, gdocs.get_docs_service, llm.LLMClient = saved


def cli(ws: Path, *argv: str) -> tuple[str, str | None]:
    """(stdout, the SystemExit message or None)."""
    buf = io.StringIO()
    stop = None
    with contextlib.redirect_stdout(buf):
        try:
            cli_main(["--workspace", str(ws), *argv])
        except SystemExit as err:
            if err.code not in (None, 0):
                stop = str(err.code)
    return buf.getvalue(), stop


def _skips(db, inst) -> list[dict]:
    return [d for d in core.decisions(db, inst) if d["kind"] == "skip"]


# --------------------------------------------------------------- the roads

def test_old_verbs_send_to_the_workflow() -> None:
    with world() as (db, man, ws, _ms):
        for argv, what in ((("filter", "run", "own-pass", "one.md"),
                            "a filter run (own-pass)"),
                           (("filter", "prelude", "own-pass", "one.md"),
                            "a filter run (own-pass)"),
                           (("lens", "run", "own-lens", "one.md"),
                            "a lens run (own-lens)"),
                           (("write", "start", "one.md"),
                            "the beat loop (write start)")):
            _out, stop = cli(ws, *argv)
            check(f"`{' '.join(argv[:2])}` stops and sends to the workflow",
                  stop is not None and what in stop
                  and "authorlm workflow start one --kind refine -m MS"
                  in stop and "--skip-reason" in stop, stop)
        check("nothing was started or recorded by being sent away",
              core.instances(db, man) == []
              and db.one("SELECT COUNT(*) AS n FROM filter_runs")["n"] == 0)
        _out, stop = cli(ws, "write", "start", "three.md", "--new")
        check("a new essay is sent to a new workflow",
              stop is not None and "--kind new" in stop, stop)


def test_skip_with_reason_is_recorded() -> None:
    with world() as (db, man, ws, _ms):
        reason = "The essay was edited by hand last month and needs only this pass."
        out = roads.enter(db, man, "one.md", "filter", name="own-pass",
                          reason=reason)
        inst = out["instance"]
        check("a skip with no open instance starts a refine instance",
              out["how"] == "skip" and inst["kind"] == "refine"
              and inst["file"] == "one.md", out)
        d = out["decision"]
        check("the skip is a workflow decision with the author's reason",
              d["kind"] == "skip" and d["reason"] == reason
              and d["words"] == reason, d)
        check("it names the road, the filter, the step, and why the "
              "instance was started",
              d["detail"]["road"] == "filter"
              and d["detail"]["name"] == "own-pass"
              and d["detail"]["step"] == "start"
              and d["detail"]["instance_started"] is True
              and "started so the skip" in d["detail"]["why_started"],
              d["detail"])
        check("the skip does not move the instance's step",
              core.get_instance(db, man, inst["id"])["step"] == "start")
        again = roads.enter(db, man, "one.md", "filter", name="own-pass")
        check("the same filter on the same instance continues with no "
              "second reason (a run goes window by window)",
              again["how"] == "skipped-before"
              and len(_skips(db, inst)) == 1, again)
        other = None
        try:
            roads.enter(db, man, "one.md", "filter", name="other-pass")
        except roads.SentToWorkflow as err:
            other = str(err)
        check("another filter is sent to the workflow again, naming the "
              "open instance and its step",
              other and inst["id"] in other and "at the start step" in other,
              other)
        lens = roads.enter(db, man, "one.md", "lens", name="own-lens",
                           reason="A quick look before the letter.")
        check("a second skip lands on the same open instance",
              lens["instance"]["id"] == inst["id"]
              and lens["decision"]["detail"]["instance_started"] is False
              and len(_skips(db, inst)) == 2)
        for bad in ("", "   ", "Two sentences. Not one."):
            try:
                roads.enter(db, man, "two.md", "filter", name="own-pass",
                            reason=bad)
                raised = ""
            except ValueError as err:
                raised = str(err)
            check(f"a reason of {bad!r} is not enough", bool(raised), bad)
        check("and nothing was started for them",
              core.instances(db, man, "two.md") == [])
        dry = roads.enter(db, man, "two.md", "filter", name="own-pass",
                          reason="Checking only.", dry_run=True)
        check("a dry run checks the reason and writes nothing",
              dry["how"] == "skip" and core.instances(db, man, "two.md") == [])

        new = roads.enter(db, man, "three", "beats",
                          reason="Drafting a short new essay from a brief.")
        check("a skip for an essay that does not exist yet starts a new "
              "instance", new["instance"]["kind"] == "new"
              and new["instance"]["file"] == "three.md", new["instance"])

        out, stop = cli(ws, "lens", "run", "own-lens", "two.md",
                        "--skip-reason", "Only the lens is wanted here.")
        two = core.instances(db, man, "two.md")
        check("the CLI's --skip-reason records the skip and says so",
              len(two) == 1 and "skip recorded on " + two[0]["id"] in out
              and _skips(db, two[0])[0]["detail"]["road"] == "lens",
              (out, stop))


def test_the_roads_own_step_needs_no_word() -> None:
    with world() as (db, man, _ws, _ms):
        inst = core.start(db, man, "one", "refine")
        inst = core.skip(db, inst, "Straight to the filters for this test.",
                         to="filters")
        for road, name in (("filter", "own-pass"), ("lens", "own-lens")):
            out = roads.enter(db, man, "one.md", road, name=name)
            check(f"a {road} at the filter step opens through the workflow",
                  out["how"] == "workflow" and out["decision"] is None)
        try:
            roads.enter(db, man, "one.md", "beats")
            closed = ""
        except roads.SentToWorkflow as err:
            closed = str(err)
        check("the beat loop at the filter step is sent back",
              "belongs to the construction step" in closed, closed)
        check("no skip was recorded for the roads at their own step",
              len(_skips(db, inst)) == 1)

        refine = core.start(db, man, "two", "refine")
        refine = core.skip(db, refine, "Letter done in chat.",
                           to="construction")
        try:
            roads.enter(db, man, "two.md", "beats")
            closed = ""
        except roads.SentToWorkflow as err:
            closed = str(err)
        check("a refine construction applying findings is not the beat loop",
              "not beat by beat" in closed, closed)
        core.set_unit(db, refine, mode="beats", reason="The letter says rebuild.")
        out = roads.enter(db, man, "two.md", "beats")
        check("construction built beat by beat opens the beat loop",
              out["how"] == "workflow")

        core.skip(db, core.get_instance(db, man, inst["id"]),
                  "Finished for this test.", to="done")
        try:
            roads.enter(db, man, "one.md", "filter", name="own-pass")
            closed = ""
        except roads.SentToWorkflow as err:
            closed = str(err)
        check("a finished instance is not an open one: start the next",
              "no open workflow" in closed, closed)


def test_filters_view_requests() -> None:
    with world() as (db, man, _ws, _ms):
        items = [{"kind": "filter", "name": "own-pass"},
                 {"kind": "lens", "name": "own-lens"}]
        out = runs_page.enqueue(db, man, items, ["one.md"])
        check("without a reason the requests are held back for the workflow",
              out["queued"] == [] and len(out["blocked"]) == 2
              and all(b.get("workflow") for b in out["blocked"])
              and all("in the box below" in b["reason"]
                      for b in out["blocked"]), out["blocked"])
        preview = runs_page.enqueue(db, man, items, ["one.md"], dry_run=True,
                                    skip_reason="Only these two passes now.")
        check("a preview with a reason would queue them and records nothing",
              len(preview["queued"]) == 2 and core.instances(db, man) == [])
        out = runs_page.enqueue(db, man, items, ["one.md"],
                                skip_reason="Only these two passes now.")
        inst = core.instances(db, man, "one.md")
        check("with a reason they are queued and each skip is recorded",
              len(out["queued"]) == 2 and len(inst) == 1
              and sorted(d["detail"]["road"] for d in _skips(db, inst[0]))
              == ["filter", "lens"]
              and all(d["detail"]["via"] == "filters view"
                      for d in _skips(db, inst[0])), out)
        check("the session's `filter run` for that request then needs no "
              "second reason",
              roads.enter(db, man, "one.md", "filter",
                          name="own-pass")["how"] == "skipped-before")


# ------------------------------------------------------------------- lint

def test_dash_form() -> None:
    house = proof.dash_law([
        "Dashes: a closed em dash (—), no spaces, for an interruption or "
        "aside: \"the answer—if there is one—waits\". The en dash (–) only "
        "for ranges: 1914–1918. Never a hyphen or a double hyphen for a "
        "dash."])
    check("the house default reads as a closed em dash", house == "closed")
    check("a law for spaced dashes reads as spaced",
          proof.dash_law(["Dashes: a spaced em dash ( — )."]) == "spaced")
    check("a law that says nothing of dashes is not read",
          proof.dash_law(["Quotation marks: curly."]) is None)
    text = ("The answer -- if there is one -- waits. A well-known case, "
            "pp. 3-5, 1914–1918, 1914 – 1918, Smith–Jones. He paused — then "
            "spoke - briefly. `a -- b` and 3 - 4.\n\n| a | b |\n|---|---|\n"
            "\n---\n")
    faults = proof.dash_faults(text, "closed")
    found = [text[lo:hi] for lo, hi, _n, _w in faults]
    check("double hyphens, spaced hyphens and a spaced em dash are faults",
          found == [" -- ", " -- ", " — ", " - "], found)
    check("hyphens in words, ranges, joined pairs, code, arithmetic, "
          "tables and rules are not", len(faults) == 4)
    rows = proof.check_dash_form(text, "closed")
    check("one finding per paragraph, with the count",
          len(rows) == 1 and rows[0].code == "dash-form"
          and "3 more dashes" in rows[0].message, rows)
    spaced = proof.dash_faults("He paused—then spoke.", "spaced")
    check("under a spaced law a closed em dash is the fault",
          [s[2] for s in spaced] == [" — "], spaced)


def test_lint_fix_applies_and_lists() -> None:
    with world() as (db, man, ws, ms):
        board = changes.lint_chapter(db, man, "one.md")
        codes = {f["code"] for f in board["findings"]}
        check("book lint reports quote marks, dash form and tracking tags",
              {"quote-style", "dash-form", "link-tracking"} <= codes, codes)
        dry = changes.lint_fix(db, man, ["one.md"], dry_run=True)
        check("a dry run plans the three mechanical fixes and writes nothing",
              sorted(p["rule"] for p in dry["planned"]["one.md"])
              == ["dash-form", "link-tracking", "quote-style"]
              and (ms / "one.md").read_text() == ONE, dry)
        out, stop = cli(ws, "book", "lint", "one.md", "--fix", "-m", "MS")
        text = (ms / "one.md").read_text()
        check("--fix applies them straight into the file",
              stop is None and "answer—if there is one—waits" in text
              and "said “nothing” and left—briefly" in text
              and "utm_source" not in text and "id=2" in text
              and "well-known" in text and "3-5" in text
              and "1914–1918" in text, (stop, text))
        check("and lists each changed paragraph with before and after",
              "fixed one.md:3" in out and "  + The answer—if" in out
              and "mechanical fix(es) applied" in out, out)
        check("no form is staged for a lint fix",
              db.one("SELECT COUNT(*) AS n FROM doc_threads")["n"] == 0)
        check("each changed paragraph is logged for undo",
              db.one("SELECT COUNT(*) AS n FROM lint_changes")["n"] == 3)
        after = changes.lint_chapter(db, man, "one.md")
        left = {f["code"] for f in after["findings"]}
        check("after --fix lint has no mechanical finding left",
              not left & {"quote-style", "dash-form", "link-tracking"}, left)
        out, _stop = cli(ws, "book", "lint", "one.md", "--fix", "-m", "MS")
        check("a second --fix has nothing to do",
              "no quote mark, dash or link to fix" in out, out)


def test_copyedit_leaves_lint_alone() -> None:
    check("every convention lint owns is a lint rule",
          set(changes.LINT_OWNS) <= set(proof.RULES), changes.LINT_OWNS)
    check("the ones lint fixes itself are lint rules too",
          set(changes.MECHANICAL_FIXES) <= set(proof.RULES))
    brief = (REPO / "library" / "workflow" / "filters" / "copyedit.md"
             ).read_text(encoding="utf-8")
    owns = re.search(r"Never touch what lint owns: ([^.]+)\.", brief)
    check("the copyedit brief says it never touches what lint owns",
          owns is not None)
    named = [p.strip() for p in owns.group(1).split(",")]
    check("and names each of lint's conventions",
          sorted(named) == sorted(changes.LINT_OWNS.values()), named)
    scope = "\n".join(line for line in brief.splitlines()
                      if line.startswith("- "))
    overlap = {
        "quote-style": r"quot(?:e|ation) marks?",
        "dash-form": r"\b(?:em|en) dash|double hyphen|dash form",
        "spelling": r"\bspell",
        "footnote": r"footnote",
        "doubled-word": r"doubled word",
    }
    for rule, pattern in overlap.items():
        hit = re.search(pattern, scope, re.IGNORECASE)
        check(f"copyedit's scope does not repeat lint's {rule}", hit is None,
              hit and hit.group(0))
    check("every rule lint owns has its overlap pattern here",
          set(overlap) == set(changes.LINT_OWNS))


def test_skill_points_to_the_workflow() -> None:
    skill = (REPO.parent / ".claude" / "skills" / "authorlm" / "SKILL.md"
             ).read_text(encoding="utf-8")
    for heading, words in (("## The beat loop", "--skip-reason"),
                           ("## The filter pass", "--skip-reason"),
                           ("### The Filters page and run requests",
                            "one sentence of why")):
        at = skill.find(heading)
        check(f"SKILL.md has '{heading}'", at >= 0)
        section = skill[at:at + 2000]
        check(f"SKILL.md '{heading}' sends to the workflow",
              "authorlm workflow" in section and words in section)


def main() -> None:
    tests = [test_old_verbs_send_to_the_workflow,
             test_skip_with_reason_is_recorded,
             test_the_roads_own_step_needs_no_word,
             test_filters_view_requests,
             test_dash_form,
             test_lint_fix_applies_and_lists,
             test_copyedit_leaves_lint_alone,
             test_skill_points_to_the_workflow]
    for fn in tests:
        print(fn.__name__)
        fn()
    print(f"OK — {len(tests)} retired-road and lint cases")


if __name__ == "__main__":
    main()
