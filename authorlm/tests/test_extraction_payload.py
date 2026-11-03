"""The redesigned extraction's payload (docs/extraction-redesign.md §4.2,
§4.3, §9 phase 1): `extraction.assemble` and `authorlm extract <file>`.

Standalone, hermetic, zero LLM calls:  python3 tests/test_extraction_payload.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _offline_prices  # noqa: E402,F401  (llmlm prices: no network)

from authorlm import api, extraction, summaries  # noqa: E402
from authorlm.db import ko_fields  # noqa: E402

import _extraction_fixture as fx  # noqa: E402

PASSED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if not ok:
        raise AssertionError(f"FAILED: {label}\n{detail}")
    PASSED += 1
    print(f"  ok: {label}")


def check_blocks(root: Path) -> None:
    print("blocks S G P M E, hashed:")
    db, ms = fx.build(root / "blocks")
    api.add_style_law(db, ms, "lexicon",
                      "Capitalise Good Choice; 'ranking' stays lowercase.",
                      file="hierarchy.md")
    one = extraction.assemble(db, ms, "hierarchy.md", "Rankings")
    again = extraction.assemble(db, ms, "hierarchy.md", "Rankings")
    check("the payload has the five blocks in order",
          [b for b, _t in one.blocks] == ["S", "G", "P", "M", "E"])
    check("deterministic: the same state assembles the same hashes",
          one.hashes == again.hashes, json.dumps(one.hashes, indent=1))
    check("S is the extraction prompt with the file's style law under it",
          one.law.startswith(extraction.section_system(
              extraction.DEFAULT_PROPOSABLE))
          and "STYLE LAW for hierarchy.md" in one.law
          and "Capitalise Good Choice" in one.law, one.law[-400:])
    check("S names only the proposable relations as the contract's set",
          "'depends_on'" in one.law and "<<PROPOSABLE_RELATIONS>>" not in one.law
          and "relation is one of\n['answers', 'contrasts_with'" in one.law,
          one.law[one.law.index("relation is one of"):][:200])
    essay = extraction.assemble_essay(db, ms, "hierarchy.md")
    check("one payload per section with prose (headings alone are not units)",
          [extraction.unit_key(p.unit) for p in essay] == ["1", "2", "3", "4",
                                                          "5", "6"],
          str([extraction.unit_key(p.unit) for p in essay]))
    shared = {name for p in essay for name, h in p.hashes.items()
              if name != "E" and h != essay[0].hashes[name]}
    check("S, G, P and M are identical across the essay's sections — one "
          "cached prefix (§6.2); only E changes",
          not shared and len({p.hashes["E"] for p in essay}) == len(essay),
          str(shared))
    rendered = extraction.render_essay(essay)
    check("the printed form says no call was made and hashes every block",
          "no model call was made" in rendered
          and rendered.count("───── block E") == len(essay)
          and rendered.count("───── block S") == 1
          and all(h in rendered for h in essay[0].hashes.values()),
          rendered[:600])


def check_numbering(root: Path) -> None:
    print("E numbers paragraphs as summaries._numbered_paragraphs does:")
    db, ms = fx.build(root / "numbers")
    text = fx.FILES["hierarchy.md"]
    numbered, _count = summaries._numbered_paragraphs(text)
    lines = set(numbered.split("\n\n"))
    payloads = extraction.assemble_essay(db, ms, "hierarchy.md")
    cited = [f"[{n}] {p}" for pl in payloads for n, p in pl.unit["paragraphs"]]
    check("every [n] paragraph in every E block is exactly the summarizer's",
          all(c in lines for c in cited) and all(c in pl.section for pl in payloads
                                                 for c in cited
                                                 if c in pl.section),
          "\n".join(c for c in cited if c not in lines))
    rankings = extraction.assemble(db, ms, "hierarchy.md", "rankings")
    n = fx.paragraph_number(text, "A rank is a place in the ordering")
    check("a section keeps file-wide numbers, not 1-based per section",
          f"[{n}] A rank is a place" in rankings.section and n > 5,
          rankings.section[:300])
    check("the E header names the essay, its title, the section and range",
          re.search(r"E — THE SECTION — hierarchy\.md — “The Ordering of "
                    r"Society” — section 3 of 6: Rankings — paragraphs "
                    r"\[\d+\]-\[\d+\]", rankings.section) is not None,
          rankings.section.splitlines()[0])


def check_selection(root: Path) -> None:
    print("--section by key or heading; long sections split, never truncate:")
    db, ms = fx.build(root / "select")
    by_key = extraction.assemble(db, ms, "hierarchy.md", "3")
    by_heading = extraction.assemble(db, ms, "hierarchy.md", "**Rankings**")
    check("a section is named by its number or its heading's text",
          by_key.hashes == by_heading.hashes)
    try:
        extraction.assemble(db, ms, "hierarchy.md", "nowhere")
        check("an unknown section is refused with the list", False)
    except LookupError as err:
        check("an unknown section is refused with the list",
              "Rankings" in str(err) and "Caste" in str(err), str(err))
    try:
        extraction.assemble(db, ms, "hierarchy.md")
        check("a many-section file needs --section for ONE payload", False)
    except LookupError as err:
        check("a many-section file needs --section for ONE payload",
              "--section" in str(err), str(err))
    config = {"extraction": {"max_section_chars": 120}}
    parts = extraction.assemble_essay(db, ms, "hierarchy.md", "Rankings",
                                      knob=extraction.knobs(config))
    text = fx.FILES["hierarchy.md"]
    whole = [n for n, _p in extraction.extraction_units(text, 10**6)[2]
             ["paragraphs"]]
    check("a section over the cap splits at paragraph boundaries into parts",
          len(parts) > 1 and [extraction.unit_key(p.unit) for p in parts][0]
          == "3.1", str([extraction.unit_key(p.unit) for p in parts]))
    check("...and never truncates: every paragraph is in exactly one part",
          [n for p in parts for n, _t in p.unit["paragraphs"]] == whole)
    check("a part's header says which part it is",
          "part 2 of" in parts[1].section.splitlines()[0])


def check_summaries(root: Path) -> None:
    print("block M — the summary hierarchy, absence and STALE labelled:")
    db, ms = fx.build(root / "summaries")
    absent = extraction.assemble(db, ms, "hierarchy.md", "Rankings")
    check("with no summaries, each level prints a labelled absence",
          all(f"SUMMARIES: none for {level}" in absent.summaries
              for level in ("essay", "sections", "paragraphs")),
          absent.summaries)
    text = fx.FILES["hierarchy.md"]
    secs = summaries.sections_of(text)
    rank_para = next(p for s in secs for p in s["paragraphs"]
                     if p["text"].startswith("A rank is a place"))
    summaries.store_paragraph_record(
        db, ms, "hierarchy.md", rank_para,
        {"gist": "a rank is a position in an ordering",
         "mentions": [{"term": "rank", "span": "A rank", "role": "defines"}]},
        "stub")
    summaries.store_section_summary(db, ms, "hierarchy.md", secs[2],
                                    "POINT: what a ranking is.", "stub")
    row = ko_fields("es")
    row.update(manuscript_id=ms["id"], file="hierarchy.md",
               summary="MOVES: [1] the ordering of society.",
               source_hash="0000000000000000", upstream_hash="",
               upstream_stale=0, sections_hash="", model="stub")
    db.insert("essay_summaries", row)
    present = extraction.assemble(db, ms, "hierarchy.md", "Rankings")
    check("the essay summary appears, STALE on a source_hash mismatch",
          "ESSAY (source_hash 0000000000000000) — STALE" in present.summaries
          and "the ordering of society" in present.summaries,
          present.summaries[:400])
    check("a section with a current row prints its summary; one without is "
          "STALE",
          "SECTION 3: Rankings" in present.summaries
          and "POINT: what a ranking is." in present.summaries
          and re.search(r"SECTION 2: Populations \[\d+\]-\[\d+\] — STALE",
                        present.summaries) is not None, present.summaries)
    n = fx.paragraph_number(text, "A rank is a place")
    check("paragraph gists come with their [n] and terms; the rest STALE",
          f"[{n}] a rank is a position in an ordering (terms: rank)"
          in present.summaries
          and "(STALE: no record of the current text)" in present.summaries,
          present.summaries)
    check("the derived lists of the essay level are printed",
          "INTRODUCES: rank" in present.summaries, present.summaries)
    ignored = extraction.assemble_essay(
        db, ms, "hierarchy.md", "Rankings",
        knob=extraction.knobs({"extraction": {"summaries": "ignore"}}))[0]
    check("summaries = ignore prints that, and nothing else",
          "SUMMARIES: ignored" in ignored.summaries
          and "ESSAY" not in ignored.summaries)
    try:
        extraction.assemble_essay(
            db, ms, "hierarchy.md", "Rankings",
            knob=extraction.knobs({"extraction": {"summaries": "require"}}))
        check("summaries = require stops on a missing or STALE level", False)
    except ValueError as err:
        check("summaries = require stops on a missing or STALE level",
              "STALE" in str(err), str(err))


def check_glossary_and_precedents(root: Path) -> None:
    print("blocks G and P:")
    db, ms = fx.build(root / "gp")
    db.update("concept_nodes", db.one(
        "SELECT id FROM concept_nodes WHERE name = 'ranking'")["id"],
        {"notes": "The ordering of a population by a metric."})
    extraction_meta = json.dumps({"origin": "extracted", "confirmed": False})
    db.update("concept_nodes", db.one(
        "SELECT id FROM concept_nodes WHERE name = 'metric'")["id"],
        {"metadata": extraction_meta})
    api.link_concepts(db, ms, "rank", "depends_on", "ranking")
    api.link_concepts(db, ms, "ranking", "depends_on", "metric")
    db.update("concept_edges", db.one(
        "SELECT id FROM concept_edges WHERE relation = 'depends_on' AND "
        "to_node = (SELECT id FROM concept_nodes WHERE name = 'metric')")["id"],
        {"status": "inferred"})
    payload = extraction.assemble(db, ms, "hierarchy.md", "Rankings")
    check("G carries kind, aliases and the FULL notes of the slice",
          "- ranking (concept) — The ordering of a population by a metric."
          in payload.glossary
          and "- memetic population (concept) (also: mimetic population)"
          in payload.glossary, payload.glossary)
    check("G marks an unconfirmed extracted node as a hypothesis",
          "- metric (concept) [unconfirmed]" in payload.glossary)
    check("G lists the settled edges only, never an inferred one",
          "- rank —depends_on→ ranking" in payload.glossary
          and "ranking —depends_on→ metric" not in payload.glossary,
          payload.glossary)
    check("G is the file= slice: a concept this essay never names is absent",
          "Franz Boas" not in payload.glossary)

    retired = fx.add_node(db, ms, "Ranking Order", status="retired",
                          confirmed=False, introduced_in="hierarchy.md")
    from authorlm.extraction import record_triage

    record_triage(db, ms["id"], retired, "rejected",
                  reason="a paraphrase of ranking, not a term")
    fx.add_node(db, ms, "Caste System", status="retired", confirmed=False,
                introduced_in="mixing.md")
    fx.add_node(db, ms, "Pottery", status="retired", confirmed=False,
                introduced_in="mixing.md")
    fx.add_node(db, ms, "Declared Retiree", status="retired", extracted=False,
                introduced_in="hierarchy.md")
    payload = extraction.assemble(db, ms, "hierarchy.md", "Rankings")
    check("P lists every rejected name of the essay with the author's reason",
          "- rejected: Ranking Order (concept) — a paraphrase of ranking, "
          "not a term" in payload.precedents, payload.precedents)
    check("P adds the nearest rejected names elsewhere, by similarity to the "
          "essay's capitalised terms",
          "Nearest rejected names elsewhere (1):" in payload.precedents
          and "- rejected: Caste System (concept)" in payload.precedents
          and "Pottery" not in payload.precedents, payload.precedents)
    check("P is the machine's rejections only: an author-retired declared "
          "concept is not a precedent",
          "Declared Retiree" not in payload.precedents)


def check_cli(root: Path) -> None:
    print("authorlm extract — prints; --native needs the switches:")
    from authorlm.cli import main

    ws = root / "cli"
    db, ms = fx.build(ws, workspace=True)
    os.environ["AUTHORLM_CONFIG"] = str(ws / ".authorlm" / "config.toml")
    (ws / ".authorlm" / "config.toml").write_text(
        "[llm]\nenabled = false\n[extraction]\nenabled = false\n")

    def run(*argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        code = 0
        try:
            with contextlib.redirect_stdout(buffer):
                main(["--workspace", str(ws), *argv])
        except SystemExit as err:
            code = 0 if err.code in (0, None) else 1
            if isinstance(err.code, str):
                buffer.write(err.code)
        return code, buffer.getvalue()

    code, out = run("extract", "hierarchy.md", "--section", "Rankings")
    check("`extract <file> --section H` prints one section's payload, with "
          "the switch off — printing is not a call",
          code == 0 and "no model call was made" in out
          and out.count("───── block E") == 1 and "Rankings" in out, out[:400])
    code, out = run("extract", "hierarchy.md")
    check("`extract <file>` prints every section, shared blocks once",
          code == 0 and out.count("───── block E") == 6
          and out.count("───── block G") == 1, out[:400])
    code, out = run("extract", "--full")
    check("`extract --full` prints every essay in reading order",
          code == 0 and out.count("extract ") >= 5
          and out.index("extract discernment.md") < out.index(
              "extract preface.md"), out[:200])
    code, out = run("extract", "hierarchy.md", "--native")
    check("`--native` is refused while [extraction] enabled = false",
          code == 1 and "switched off" in out, out)
    code, out = run("extract", "a.md", "b.md", "--section", "1")
    check("--section with two files is refused", code == 1
          and "ONE file" in out, out)


def main_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-extract-payload-"))
    try:
        check_blocks(root)
        check_numbering(root)
        check_selection(root)
        check_summaries(root)
        check_glossary_and_precedents(root)
        check_cli(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nAll {PASSED} checks passed.")


if __name__ == "__main__":
    main_test()
