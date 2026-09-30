"""Interlocutor tests (docs/interlocutor-design.md).

The artifact grammar and its refusals, the deterministic scan, the payload
blocks, the report gate (baseline refuses whole, findings drop one at a
time), the manifest join, and the landing on the critique road. Hermetic:
no model is ever called on this road.

Run: python3 tests/test_interlocutor.py
"""

from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import api, concepts, interlocutor as il  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.interlocutor import InterlocutorError  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


def refuses(label: str, fn, needle: str) -> None:
    try:
        fn()
    except (InterlocutorError, LookupError) as err:
        check(label, needle in str(err), str(err))
        return
    raise AssertionError(f"FAIL: {label} — did not refuse")


TOC = '''[[chapter]]
file = "part.md"

[[chapter]]
file = "alpha.md"
parent = "part.md"

[[chapter]]
file = "beta.md"
parent = "part.md"

[[chapter]]
file = "links.md"

[[chapter]]
file = "stoic.md"
parent = "links.md"

[[chapter]]
file = "back.md"
matter = "back"
'''

ALPHA = """# **Alpha**

The opening paragraph says nothing of note.

## **The Inner Citadel**

Choice is the faculty by which a life is steered, and choices accumulate.

A prohairetic act is one the agent owns. The Stoic would call it up to us.

## **Divergences**

Here the book parts from the Stoic: apatheia is not the goal.
"""

BETA = """# **Beta**

Beta says that Distinction precedes Choice, and that is the whole premise.

The word choice appears here in lower case, and the scan is case-blind.
"""

STOIC = """# **Prohairesis**

## **What Is in Our Power**

The book reads prohairesis as Choice and nothing else.
"""

ARTIFACT = """---
engaged = ["stoic.md"]

[[terms]]
name = "prohairesis"
aliases = ["prohairetic", "up to us"]
kind = "tradition"

[[terms]]
name = "apatheia"
kind = "tradition"

[[terms]]
name = "Choice"
kind = "book"
reason = "the book's nearest analogue to prohairesis"
---

# Who is reading

You read this book as a scholar of Epictetus.

## Refusals

- No objection without a locus.
"""


def _workspace() -> tuple[Path, Path]:
    root = Path(tempfile.mkdtemp(prefix="authorlm-interlocutor-"))
    ws = root / "ws"
    ms = ws / "book"
    ms.mkdir(parents=True)
    (ms / "toc.toml").write_text(TOC)
    (ms / "part.md").write_text("# Part\n\nThe part opener.\n")
    (ms / "alpha.md").write_text(ALPHA)
    (ms / "beta.md").write_text(BETA)
    (ms / "links.md").write_text("# Links\n\nOpener.\n")
    (ms / "stoic.md").write_text(STOIC)
    (ms / "back.md").write_text("# Back\n\nA choice in the back matter.\n")
    (ws / ".authorlm").mkdir()
    (ws / ".authorlm" / "config.toml").write_text("[llm]\nenabled = false\n")
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms)])
    return ws, ms


def _cli(ws: Path, *argv: str, stdin: str | None = None) -> str:
    out = io.StringIO()
    old = sys.stdin
    try:
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        with contextlib.redirect_stdout(out):
            cli_main(["--workspace", str(ws), *argv])
    finally:
        sys.stdin = old
    return out.getvalue()


def artifact_grammar(ms: Path, manuscript: dict) -> None:
    print("artifact grammar:")
    files = {"alpha.md": ALPHA, "stoic.md": STOIC, "toc.toml": TOC}
    meta, prose = il.parse_artifact(ARTIFACT, files)
    check("terms parse with kind, aliases, reason",
          [t["name"] for t in meta["terms"]] == ["prohairesis", "apatheia",
                                                  "Choice"]
          and meta["terms"][0]["aliases"] == ["prohairetic", "up to us"]
          and meta["terms"][2]["reason"].startswith("the book's"))
    check("engaged and position lists parse (position defaults empty)",
          meta["engaged"] == ["stoic.md"] and meta["position"] == [])
    check("prose is what follows the front matter",
          prose.startswith("# Who is reading"))

    refuses("no front matter is refused with the shortest legal artifact",
            lambda: il.parse_artifact("# just prose"), "shortest legal")
    refuses("unclosed front matter is refused",
            lambda: il.parse_artifact("---\n[[terms]]\nname='x'\n"),
            "never closed")
    refuses("unknown key is refused loudly",
            lambda: il.parse_artifact(
                "---\nklass = 1\n[[terms]]\nname = \"x\"\nkind = "
                "\"tradition\"\n---\nprose"), "unknown front-matter key")
    refuses("terms are required",
            lambda: il.parse_artifact("---\nengaged = []\n---\nprose"),
            "`terms` is required")
    refuses("a term needs a legal kind",
            lambda: il.parse_artifact(
                "---\n[[terms]]\nname = \"x\"\nkind = \"mine\"\n---\nprose"),
            "kind must be one of")
    refuses("a book term needs a reason",
            lambda: il.parse_artifact(
                "---\n[[terms]]\nname = \"Choice\"\nkind = \"book\"\n---\np"),
            "needs a `reason`")
    refuses("a duplicate term is refused",
            lambda: il.parse_artifact(
                "---\n[[terms]]\nname = \"x\"\nkind = \"tradition\"\n"
                "[[terms]]\nname = \"X\"\nkind = \"tradition\"\n---\nprose"),
            "listed twice")
    refuses("engaged must name a manuscript file when files are known",
            lambda: il.parse_artifact(
                "---\nengaged = [\"ghost.md\"]\n[[terms]]\nname = \"x\"\n"
                "kind = \"tradition\"\n---\nprose", files),
            "not a manuscript file")
    refuses("empty prose is refused",
            lambda: il.parse_artifact(
                "---\n[[terms]]\nname = \"x\"\nkind = \"tradition\"\n---\n"),
            "nothing after the front matter")

    refuses("add refuses a bad name",
            lambda: il.add_interlocutor(manuscript, "Bad Name", ARTIFACT),
            "kebab-case")
    before = sorted((ms / "_interlocutors").glob("*")) \
        if (ms / "_interlocutors").is_dir() else []
    refuses("a refused add writes no file",
            lambda: il.add_interlocutor(manuscript, "broken", "# prose only"),
            "front matter")
    after = sorted((ms / "_interlocutors").glob("*")) \
        if (ms / "_interlocutors").is_dir() else []
    check("… and the directory is unchanged", before == after)
    path, meta = il.add_interlocutor(manuscript, "epictetus", ARTIFACT)
    check("add writes _interlocutors/<name>.md",
          path.resolve() == (ms / "_interlocutors" / "epictetus.md").resolve()
          and path.is_file())
    from authorlm.revisions import read_manuscript_files
    check("the sidecar directory is invisible to observation",
          "_interlocutors/epictetus.md" not in read_manuscript_files(ms))
    rows = il.list_interlocutors(manuscript)
    check("list shows the name, first heading, term count, engaged",
          rows and rows[0]["name"] == "epictetus"
          and rows[0]["summary"] == "Who is reading"
          and rows[0]["terms"] == 3 and rows[0]["engaged"] == ["stoic.md"])


def scan_and_payload(db, ms: Path, manuscript: dict) -> None:
    print("scan:")
    from authorlm.revisions import read_manuscript_files
    files = read_manuscript_files(ms)
    meta, _ = il.load(manuscript, "epictetus", files)
    hits = il.scan(files, meta["terms"])
    where = [(h["file"], h["n"]) for h in hits]
    check("hits come in reading order, one per unit",
          where == [("alpha.md", 4), ("alpha.md", 5), ("alpha.md", 7),
                    ("beta.md", 2), ("beta.md", 3), ("stoic.md", 3),
                    ("back.md", 2)], str(where))
    a4 = next(h for h in hits if h["file"] == "alpha.md" and h["n"] == 4)
    check("plural and case are tolerated (choices → Choice, book)",
          a4["terms"] == [("Choice", "book")], str(a4["terms"]))
    check("the enclosing heading is tracked, markup stripped",
          a4["heading"] == "The Inner Citadel")
    a5 = next(h for h in hits if h["file"] == "alpha.md" and h["n"] == 5)
    check("aliases match and name the term, not the alias",
          [t for t, _ in a5["terms"]] == ["prohairesis"], str(a5["terms"]))
    check("part and matter are tagged from the toc",
          a4["part"] == "part.md" and a4["matter"] == "main"
          and next(h for h in hits if h["file"] == "back.md")["matter"]
          == "back")
    check("a heading unit sets the section and is not itself a hit",
          not any(h["n"] == 1 for h in hits if h["file"] == "stoic.md"))
    narrowed = il.scan(files, meta["terms"], ["links.md", "stoic.md"])
    check("a narrowed scan sees only the selected files",
          {h["file"] for h in narrowed} == {"stoic.md"})

    print("sampling:")
    big = [{"file": f"f{i % 5}.md", "part": None, "matter": "main",
            "heading": None, "n": i, "terms": [(f"t{i % 9}", "book")],
            "text": f"unit {i}"} for i in range(1, 91)]
    s = il.sample_hits(big, max_terms=4, max_per_term=3, seed=7)
    check("sampling caps terms and locations per term",
          len(s["chosen_terms"]) == 4 and all(v <= 3 for v in s["shown"].values())
          and len(s["hits"]) <= 12 and s["total_units"] == 90)
    mixed = big + [{"file": "f9.md", "part": None, "matter": "main",
                    "heading": None, "n": 200 + i,
                    "terms": [("prohairesis", "tradition")],
                    "text": f"stoic unit {i}"} for i in range(1, 3)]
    ms_ = il.sample_hits(mixed, max_terms=4, max_per_term=3, seed=7)
    check("a tradition term is always shown; the cap falls on the book's "
          "terms alone",
          "prohairesis" in ms_["chosen_terms"]
          and len([t for t in ms_["chosen_terms"] if t != "prohairesis"]) == 4)
    check("chosen terms keep the artifact's order",
          s["chosen_terms"] == [t for t in s["all_terms"]
                                if t in s["chosen_terms"]])
    check("sampled hits keep reading order",
          [h["n"] for h in s["hits"]] == sorted(h["n"] for h in s["hits"]))
    again = il.sample_hits(big, max_terms=4, max_per_term=3, seed=7)
    check("the same seed reproduces the same sample",
          [h["n"] for h in again["hits"]] == [h["n"] for h in s["hits"]])
    other = il.sample_hits(big, max_terms=4, max_per_term=3, seed=8)
    check("a different seed samples differently",
          [h["n"] for h in other["hits"]] != [h["n"] for h in s["hits"]])
    check("a fresh run draws its own seed and records it",
          isinstance(il.sample_hits(big)["seed"], int))
    everything = il.sample_hits(big, max_terms=0, max_per_term=0, seed=1)
    check("caps of 0 mean no sampling", len(everything["hits"]) == 90)
    s["_all_hits"] = big
    cov = il.coverage_block(s)
    check("coverage names every term, shown or not, with where it lives",
          "of 9 term(s)" in cov and "none shown this run" in cov
          and "Seed 7 reproduces it" in cov and "f0.md ×" in cov)

    block = il.mentions_block(hits, whole={"stoic.md"})
    check("mentions block groups by file and heading with the unit verbatim",
          "### alpha.md  (part: part.md, matter: main)" in block
          and "#### §The Inner Citadel" in block
          and "Choice is the faculty" in block)
    check("a file carried whole gives locations only",
          "carried whole above" in block
          and "reads prohairesis as Choice" not in block)
    check("an empty scan says so",
          "no unit" in il.mentions_block([]))

    print("payload:")
    concepts.add_concept(db, manuscript["id"], "Choice",
                         notes="The steering faculty.")
    out = ms.parent / "scratch" / "run1"
    result = il.build_payload(db, manuscript, "epictetus",
                              position=["beta.md"], out=out)
    payload = result["payload"]
    check("payload opens with the registered prompt",
          payload.startswith("# Interlocutor"))
    for label in ("THE INTERLOCUTOR", "TERMS", "THE BOOK", "AUTHOR POSITION",
                  "ENGAGED", "CONCEPT NOTES", "MENTIONS", "MANUSCRIPT ROOT",
                  "WRITE TO"):
        check(f"payload carries {label}", f"## {label}" in payload)
    check("position from the flag is carried whole, numbered",
          "### beta.md\n\n[¶1] # **Beta**" in payload)
    check("engaged from the artifact is carried whole",
          "### stoic.md\n\n[¶1]" in payload)
    check("concept notes carry the ratified note for the book term that hit",
          "- Choice — The steering faculty." in payload)
    check("missing summaries are marked, never refused",
          "[alpha.md]" in payload and "!! no summary" in payload)
    check("the manuscript root is absolute and lists the scanned files",
          str(ms.resolve()) in payload and "- back.md" in payload)
    check("write-to names report and manifest beside the payload",
          f"{out}.report.md" in payload and f"{out}.manifest.json" in payload)
    check("mentions is announced as a sample with its seed and coverage",
          "## MENTIONS (a SAMPLE of the scan" in payload
          and f"Seed: {result['seed']}" in payload
          and "Scan: 7 unit(s) mention a term" in payload)
    seeded = il.build_payload(db, manuscript, "epictetus", seed=42,
                              max_terms=1, max_per_term=1)
    check("a seeded, capped run shows every tradition term and one book term, "
          "one location each",
          seeded["seed"] == 42
          and [t for t in seeded["sampled_terms"]
               if t in ("prohairesis", "apatheia")]
          and len([t for t in seeded["sampled_terms"]
                   if t not in ("prohairesis", "apatheia")]) == 1
          and "(every tradition term; the book's terms sampled)"
          in seeded["payload"])
    check("payload, and the mentions sidecar, are written",
          out.is_file() and Path(f"{out}.mentions.md").is_file())
    refuses("--engaged must name a manuscript file",
            lambda: il.build_payload(db, manuscript, "epictetus",
                                     engaged=["ghost.md"]),
            "not a manuscript file")
    narrowed = il.build_payload(db, manuscript, "epictetus",
                                file_names=["links.md"])
    check("--file narrows the scan by toc descent and says so",
          narrowed["selected"] == ["links.md", "stoic.md"]
          and "scope: links.md" in narrowed["payload"]
          and "(outside this run's scope)" in narrowed["payload"])
    refuses("run refuses an unknown interlocutor by name",
            lambda: il.build_payload(db, manuscript, "plato"),
            "no interlocutor 'plato'")

    print("draft payload:")
    refuses("draft refuses without an engaged essay",
            lambda: il.build_draft_payload(db, manuscript, "becker", []),
            "author's input")
    draft = il.build_draft_payload(db, manuscript, "becker", ["alpha.md"])
    check("draft carries the exemplar, the engaged essay, the concepts",
          "### _interlocutors/epictetus.md" in draft["payload"]
          and "### alpha.md" in draft["payload"]
          and "- Choice" in draft["payload"])
    check("draft shows an installed critic with no position, and suggests "
          "an empty list",
          "- epictetus: (none)" in draft["payload"]
          and "Suggested: position = []" in draft["payload"]
          and il.position_on_record(manuscript) == [])
    il.add_interlocutor(manuscript, "stoa", ARTIFACT.replace(
        'engaged = ["stoic.md"]',
        'engaged = ["stoic.md"]\nposition = ["beta.md", "alpha.md"]'))
    il.add_interlocutor(manuscript, "porch", ARTIFACT.replace(
        'engaged = ["stoic.md"]',
        'engaged = ["stoic.md"]\nposition = ["beta.md"]'))
    check("position on record: every file any critic carries, most-carried "
          "first, then reading order",
          il.position_on_record(manuscript) == ["beta.md", "alpha.md"])
    draft = il.build_draft_payload(db, manuscript, "becker", ["alpha.md"])
    check("draft seeds the new artifact's position from the record",
          "- stoa: beta.md, alpha.md" in draft["payload"]
          and 'Suggested: position = ["beta.md", "alpha.md"]'
          in draft["payload"])
    for extra in ("stoa", "porch"):
        il._path(manuscript, extra).unlink()


REPORT = """# epictetus reads book — 2026-09-06

## Baseline
- `alpha.md` — whole
- `beta.md` — §Beta

## Objections
### O1. the dichotomy of control — Discourses 1.1 [sure]
Bears on: `alpha.md` §The Inner Citadel — "Choice is the faculty by which a life is steered"
The objection: Epictetus draws the line at what is up to us.
Verdict: partial
Where addressed: `beta.md` — "Distinction precedes Choice"
Missing premise: what makes a Distinction up to us.
Improvement: State in beta.md what makes a Distinction up to the agent. → scope: beta.md

### O2. apatheia as telos — Discourses 3.2 [check]
Bears on: `alpha.md` §Divergences — "apatheia is not the goal"
The objection: For the Stoic, freedom from passion is the goal.
Verdict: addressed
Where addressed: `alpha.md` §Divergences — "Here the book parts from the Stoic"; `beta.md` §Beta — "Distinction precedes Choice"
Inference: the book declares the divergence and owns it.

### O3. an objection with no locus
Bears on: `alpha.md` — "Choice is the faculty"
The objection: nobody said this.
Verdict: unaddressed
Improvement: do something → scope: alpha.md

### O4. misquoted objection — Enchiridion 1 [sure]
Bears on: `alpha.md` — "Choice is the faculty by which a life is derailed"
The objection: never mind.
Verdict: unaddressed
Improvement: fix it → scope: alpha.md

## Misattributions
### M1. prohairesis is not Choice — Discourses 1.17 [sure]
Passage: `stoic.md` §What Is in Our Power — "reads prohairesis as Choice and nothing else"
What the tradition holds: prohairesis is narrower.
Contested: no
Repair: Say that prohairesis is narrower than Choice. → scope: stoic.md

### M2. a passage that is not in the book — Discourses 2.1 [sure]
Passage: `stoic.md` — "this sentence was never written"
What the tradition holds: whatever.
Repair: none → scope: stoic.md

## Terms not on the list
- assent — the scan should carry sunkatathesis
"""

MANIFEST = {
    "source": {"name": "Epictetus, read by Claude",
               "detail": "interlocutor run, test"},
    "items": [
        {"kind": "intent", "unit": "O1 the dichotomy of control",
         "ordinal": 1, "scope": "beta.md",
         "text": "State in beta.md what makes a Distinction up to the agent."},
        {"kind": "intent", "unit": "O2 apatheia as telos", "ordinal": 2,
         "scope": "alpha.md", "text": "should be dropped: addressed"},
        {"kind": "intent", "unit": "O3 no locus", "ordinal": 3,
         "scope": "alpha.md", "text": "should be dropped: finding dropped"},
        {"kind": "intent", "unit": "M1 prohairesis is not Choice",
         "ordinal": 4, "scope": "stoic.md",
         "text": "Say that prohairesis is narrower than Choice."},
        {"kind": "intent", "unit": "M1 again with a bad scope",
         "ordinal": 5, "scope": "ghost.md", "text": "dropped: scope"},
        {"kind": "style_element", "unit": "M1 wrong kind", "ordinal": 6,
         "text": "dropped: kind"},
        {"kind": "intent", "unit": "no id at all", "ordinal": 7,
         "scope": None, "text": "dropped: no id"},
    ],
}


def report_gate(db, ms: Path, manuscript: dict) -> None:
    print("report parsing and the gate:")
    parsed = il.parse_report(REPORT)
    check("baseline lines parse as whole or heading",
          parsed["baseline"] == [{"file": "alpha.md", "heading": None,
                                  "whole": True},
                                 {"file": "beta.md", "heading": "Beta",
                                  "whole": False}], str(parsed["baseline"]))
    ids = [f["id"] for f in parsed["findings"]]
    check("every finding heading is found", ids == ["O1", "O2", "O3", "O4",
                                                     "M1", "M2"], str(ids))
    o1 = parsed["findings"][0]
    check("locus and mark come off the heading",
          o1["title"] == "the dichotomy of control"
          and o1["locus"] == "Discourses 1.1" and o1["mark"] == "sure")
    check("verdict parses", o1["verdict"] == "partial"
          and parsed["findings"][1]["verdict"] == "addressed")
    check("a heading with no locus parses with locus None",
          parsed["findings"][2]["locus"] is None)
    check("multi-line key values are joined",
          parsed["findings"][0]["fields"]["Improvement"].endswith(
              "→ scope: beta.md"))

    from authorlm.revisions import read_manuscript_files
    files = read_manuscript_files(ms)
    gate = il.verify_report(files, parsed)
    check("a true baseline passes", gate["baseline_errors"] == [],
          str(gate["baseline_errors"]))
    kept = [f["id"] for f in gate["kept"]]
    dropped = {d["id"]: d["reason"] for d in gate["dropped"]}
    check("verbatim, located findings survive", kept == ["O1", "O2", "M1"],
          str(kept))
    o2 = next(f for f in gate["kept"] if f["id"] == "O2")
    check("a Where-addressed line with several file/quote pairs checks each "
          "quote against ITS file (the first live run's bug)",
          o2["files"] == {"alpha.md", "beta.md"})
    check("… and _quotes_of pairs each quote with the nearest file before it",
          il._quotes_of('`a.md` §X — "one"; `b.md` — "two" and "three"')
          == [("a.md", "one"), ("b.md", "two"), ("b.md", "three")])
    swapped = il.parse_report(REPORT.replace(
        '`beta.md` §Beta — "Distinction precedes Choice"',
        '`alpha.md` §Beta — "Distinction precedes Choice"'))
    check("a quote paired with the wrong file is still dropped",
          "O2" in {d["id"] for d in il.verify_report(files, swapped)["dropped"]})
    check("a locus-less objection is dropped and says why",
          "no locus" in dropped.get("O3", ""), str(dropped))
    check("a misquoted objection is dropped and names the file",
          "not verbatim in alpha.md" in dropped.get("O4", ""), str(dropped))
    check("a misattribution whose passage is not in the book is dropped",
          "not verbatim in stoic.md" in dropped.get("M2", ""), str(dropped))

    bad = il.parse_report(REPORT.replace("- `beta.md` — §Beta",
                                         "- `beta.md` — §Nowhere\n"
                                         "- `ghost.md` — whole"))
    errs = il.verify_report(files, bad)["baseline_errors"]
    check("a baseline naming a missing heading or file is an error",
          len(errs) == 2 and "no such heading" in errs[0]
          and "not a manuscript file" in errs[1], str(errs))

    manifest, items_dropped = il.gate_manifest(MANIFEST, gate["kept"], files)
    units = [i["unit"] for i in manifest["items"]]
    check("manifest items join to surviving, non-addressed findings only",
          units == ["O1 the dichotomy of control",
                    "M1 prohairesis is not Choice"], str(units))
    reasons = " | ".join(d["reason"] for d in items_dropped)
    check("dropped items say why: addressed, dropped finding, scope, kind, id",
          "is addressed" in reasons and "surviving finding" in reasons
          and "not a manuscript file" in reasons
          and "other kinds are refused" in reasons, reasons)
    refuses("a manifest in the wrong shape is refused",
            lambda: il.gate_manifest({"items": 1}, gate["kept"], files),
            "critique manifest shape")


def landing(db, ws: Path, ms: Path, manuscript: dict) -> None:
    print("import — landing on the critique road:")
    out = ms.parent / "scratch" / "run2"
    il.build_payload(db, manuscript, "epictetus", out=out)
    refuses("import refuses when the subagent wrote nothing",
            lambda: il.import_run(db, manuscript, "epictetus", out),
            "nothing to import")
    Path(f"{out}.report.md").write_text(REPORT)
    Path(f"{out}.manifest.json").write_text(json.dumps(MANIFEST))
    result = il.import_run(db, manuscript, "epictetus", out)
    landed = Path(result["report"])
    check("the report lands in _critiques/ named by date and critic",
          landed.parent.resolve() == (ms / "_critiques").resolve()
          and landed.name.endswith("-interlocutor-epictetus.md"))
    text = landed.read_text()
    check("the landed report opens with the harness header and counts",
          text.startswith("<!-- interlocutor: epictetus")
          and "Objections kept: 2 (addressed 1, partial 1, unaddressed 0)" in text
          and "findings dropped at the gate: 3" in text)
    check("the deterministic mentions block is prepended, then the body",
          text.index("## Mentions (deterministic scan)")
          < text.index("### alpha.md  (part: part.md")
          < text.index("## Objections"))
    check("dropped findings are listed in the header",
          "- O3 an objection with no locus: no locus" in text)
    manifest = json.loads(Path(result["manifest"]).read_text())
    check("the landed manifest is the gated one",
          [i["unit"] for i in manifest["items"]]
          == ["O1 the dichotomy of control", "M1 prohairesis is not Choice"])
    imp = result["imported"]
    check("two proposed intents were imported", imp["intents"] == 2, str(imp))
    rows = db.all("SELECT i.statement, i.status, i.scope, s.name AS src "
                  "FROM declared_intents i JOIN sources s ON s.id = i.source_id "
                  "WHERE i.manuscript_id = ? ORDER BY i.created_at",
                  (manuscript["id"],))
    check("they are proposed, scoped, and carry the critic's provenance",
          len(rows) == 2 and all(r["status"] == "proposed" for r in rows)
          and rows[0]["scope"] == "beta.md"
          and rows[0]["src"] == "Epictetus, read by Claude", str([dict(r) for r in rows]))

    Path(f"{out}.report.md").write_text(REPORT)
    again = il.import_run(db, manuscript, "epictetus", out)
    check("a second import lands a second file rather than overwriting",
          again["report"] != result["report"]
          and Path(again["report"]).name.endswith("-2.md"))
    check("… and its items are skipped as already present (same source, "
          "unit, ordinal), verdicts standing",
          again["imported"]["intents"] == 0
          and again["imported"]["skipped"] == 2, str(again["imported"]))

    bad = ms.parent / "scratch" / "run3"
    il.build_payload(db, manuscript, "epictetus", out=bad)
    Path(f"{bad}.report.md").write_text(
        REPORT.replace("- `alpha.md` — whole", "- `ghost.md` — whole"))
    Path(f"{bad}.manifest.json").write_text(json.dumps(MANIFEST))
    before = sorted((ms / "_critiques").glob("*"))
    refuses("a false baseline refuses the whole import",
            lambda: il.import_run(db, manuscript, "epictetus", bad),
            "baseline does not match")
    check("… and lands nothing",
          sorted((ms / "_critiques").glob("*")) == before)

    print("CLI:")
    listing = _cli(ws, "interlocutor", "list")
    check("list prints the interlocutor with its engaged essay",
          "epictetus:" in listing and "engaged: stoic.md" in listing)
    shown = _cli(ws, "interlocutor", "show", "epictetus")
    check("show prints the artifact", "[[terms]]" in shown)
    run = _cli(ws, "interlocutor", "run", "epictetus", "--file", "links.md",
               "--position", "beta.md", "--out",
               str(ms.parent / "scratch" / "cli-run"))
    check("run reports scope, hits and the no-model-call promise",
          "scope: links.md" in run and "No model call" in run
          and "position: beta.md" in run)
    added = _cli(ws, "interlocutor", "add", "becker",
                 stdin=ARTIFACT.replace('engaged = ["stoic.md"]',
                                        'engaged = ["alpha.md"]'))
    check("add via CLI ratifies and reports the lists",
          "ratified" in added and "engaged: alpha.md" in added)
    try:
        _cli(ws, "interlocutor", "add", "broken", stdin="# no front matter")
        raise AssertionError("FAIL: CLI add did not refuse")
    except SystemExit as err:
        check("CLI add refuses with the grammar's own words",
              "front matter" in str(err))
    from authorlm.cli import LLM_VERBS
    check("interlocutor announces its prompt like every LLM-shaped verb",
          "interlocutor" in LLM_VERBS)


def main_test() -> None:
    ws, ms = _workspace()
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    artifact_grammar(ms, manuscript)
    scan_and_payload(db, ms, manuscript)
    report_gate(db, ms, manuscript)
    landing(db, ws, ms, manuscript)
    print(f"\nall {PASSED} checks passed")


if __name__ == "__main__":
    main_test()
