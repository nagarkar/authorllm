"""The candidate contract and the eight gates (docs/extraction-redesign.md
§4.4, §4.10, §9 phase 2), on the twenty edges of §2.4.

Standalone, hermetic, zero LLM calls:  python3 tests/test_extraction_gates.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _offline_prices  # noqa: E402,F401  (llmlm prices: no network)

from authorlm import extraction, proposals  # noqa: E402
from authorlm.concepts import retire_concept  # noqa: E402
from authorlm.db import loads  # noqa: E402
from authorlm.revisions import collect_revision  # noqa: E402

import _extraction_fixture as fx  # noqa: E402

PASSED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if not ok:
        raise AssertionError(f"FAILED: {label}\n{detail}")
    PASSED += 1
    print(f"  ok: {label}")


def replies_for(payloads, items: list[tuple[str, dict]]) -> list[dict]:
    """One reply per section payload, each candidate placed in the reply
    of the section that holds the paragraph it cites."""
    replies = [{"concepts": [], "links": [], "aliases": []} for _ in payloads]
    for key, item in items:
        cited = item.get("paragraph") or (item.get("paragraphs") or [None])[0]
        index = next((i for i, p in enumerate(payloads)
                      if cited in p.paragraphs), 0)
        replies[index][key].append(item)
    return replies


def screen_edges(db, ms, rows, knob=None, confidence=0.9):
    """The §2.4 rows of one essay through parse and the gates."""
    knob = knob or extraction.knobs({})
    file = rows[0]["file"]
    payloads = extraction.assemble_essay(db, ms, file, knob=knob)
    items = [("links", fx.link_candidate(r, confidence)) for r in rows]
    return extraction.screen_replies(db, ms, payloads,
                                     replies_for(payloads, items), knob)


def outcomes(db, ms, knob=None) -> dict[int, dict]:
    by_file: dict[str, list[dict]] = {}
    for row in fx.EDGES:
        by_file.setdefault(row["file"], []).append(row)
    out: dict[int, dict] = {}
    for rows in by_file.values():
        screening = screen_edges(db, ms, rows, knob)
        for cand in screening.candidates:
            out[int(cand["why"].rsplit("#", 1)[1])] = cand
    return out


def check_section_2_4(root: Path) -> None:
    print("the twenty edges of §2.4 through the eight gates:")
    db, ms = fx.build(root / "s24")
    got = outcomes(db, ms)

    def verdict(n: int):
        c = got[n]
        return None if c.get("signal") is None else (c["signal"], c["gate"])

    table = "\n".join(f"#{n}: {verdict(n)} — {got[n].get('reason', '')}"
                      for n in sorted(got))
    check("the seven §2.4 confirms that extraction may propose (#4-7, 10, "
          "20) pass every gate",
          all(verdict(n) is None for n in (4, 5, 6, 7, 10, 20)), table)
    check("#1, #8, #14, #19 — no paragraph names both endpoints — fail "
          "gate 2 (ungrounded)",
          all(verdict(n) == ("ungrounded", "1-2") for n in (1, 8, 14, 19))
          and all("both endpoints" in got[n]["reason"]
                  for n in (1, 8, 14, 19)), table)
    check("#16 and #17, mirrors of #5 and #7, fail gate 6 (duplicate), "
          "folded into the edge the author kept",
          verdict(16) == ("duplicate", "6") and verdict(17) == ("duplicate", "6")
          and "rank —depends_on→ ranking" in got[16]["reason"]
          and "ranking —depends_on→ metric" in got[17]["reason"], table)
    check("#9 (a confirm) is grounded and unique, and gate 7 holds it: "
          "leads_to is not proposable until calibrate measures it back in",
          verdict(9) == ("not_proposable", "7"), table)
    check("so are the creates / defines / leads_to rows #11, #12, #13, #18",
          all(verdict(n) == ("not_proposable", "7") for n in (11, 12, 13, 18)),
          table)
    check("#2 and #3 are grounded, proposable category errors: they pass "
          "the gates and are the adjudicator's to catch",
          all(verdict(n) is None for n in (2, 3)), table)
    check("#15, the mirror of #3 on the same sentence (§2.4), folds into it "
          "at gate 6", verdict(15) == ("duplicate", "6")
          and "Jati —specializes→ endogamy" in got[15]["reason"], table)
    check("#20 grounds through the alias: 'memetic population' is named in "
          "the text as 'mimetic population'",
          verdict(20) is None and got[20]["from"] == "memetic population")
    widened = extraction.knobs({"extraction": {"proposable_relations": [
        *extraction.DEFAULT_PROPOSABLE, "leads_to"]}})
    again = outcomes(db, ms, widened)
    check("with leads_to proposable, #9 passes all eight; #16/#17 still fold "
          "into the depends_on edges (a proposable relation is the stronger)",
          again[9].get("signal") is None
          and again[16].get("signal") == "duplicate"
          and again[17].get("signal") == "duplicate", str(again[9]))
    check("score: a definition-shaped span grounds at 1.0, another at 0.7",
          got[4]["score"] == 0.9 and got[7]["score"] == round(0.9 * 0.7, 4),
          f"{got[4]['score']} {got[7]['score']}")


def check_link_gates(root: Path) -> None:
    print("link gates beyond §2.4:")
    db, ms = fx.build(root / "links")
    knob = extraction.knobs({})
    payloads = extraction.assemble_essay(db, ms, "hierarchy.md", knob=knob)
    text = fx.FILES["hierarchy.md"]
    pop = fx.paragraph_number(text, "bundle of individual trajectories")
    caste = fx.paragraph_number(text, "Caste maps jati")
    from authorlm import api

    api.link_concepts(db, ms, "population", "depends_on", "accord")
    api.link_concepts(db, ms, "Jati", "leads_to", "Caste")
    db.update("concept_edges", db.one(
        "SELECT id FROM concept_edges WHERE relation = 'leads_to'")["id"],
        {"status": "rejected"})
    items = [
        ("links", {"from": "population", "relation": "depends_on",
                   "to": "accord", "paragraph": pop,
                   "span": "A population is a bundle", "confidence": 0.9}),
        ("links", {"from": "Caste", "relation": "depends_on", "to": "Jati",
                   "paragraph": caste, "span": "Caste maps jati to varna",
                   "confidence": 0.4}),
        ("links", {"from": "Jati", "relation": "leads_to", "to": "Caste",
                   "paragraph": caste, "span": "Caste maps jati to varna",
                   "confidence": 0.9}),
        ("links", {"from": "Nobody", "relation": "depends_on",
                   "to": "population", "paragraph": pop,
                   "span": "A population", "confidence": 0.9}),
        ("links", {"from": "population", "relation": "depends_on",
                   "to": "accord", "paragraph": pop,
                   "span": "A population is a bundle of trajectories",
                   "confidence": 0.9}),
        ("links", {"from": "accord", "relation": "depends_on",
                   "to": "population", "paragraph": 999,
                   "span": "A population", "confidence": 0.9}),
        ("links", "not a dict"),
        ("links", {"from": "rank", "relation": "depends_on", "to": "rank",
                   "paragraph": pop, "span": "x"}),
    ]
    replies = replies_for(payloads, [(k, v) for k, v in items
                                     if isinstance(v, dict)])
    replies[0]["links"].append("not a dict")
    s = extraction.screen_replies(db, ms, payloads, replies, knob)
    links = {(c["from"], c["relation"], c["to"], c["span"]): c
             for c in s.candidates if c["type"] == "link"}
    refound = links[("population", "depends_on", "accord",
                     "A population is a bundle")]
    check("a link the graph already holds is re-found, not re-asked: "
          "duplicate, with the edge whose evidence gains the span",
          refound["signal"] == "duplicate" and refound.get("refound"),
          str(refound))
    check("a span that is not verbatim in its paragraph is ungrounded",
          links[("population", "depends_on", "accord",
                 "A population is a bundle of trajectories")]["signal"]
          == "ungrounded")
    check("a paragraph outside the section the call saw grounds nothing",
          links[("accord", "depends_on", "population", "A population")]
          ["reason"] == "cites no paragraph of the section")
    check("a relation the author rejected is banned at gate 5",
          links[("Jati", "leads_to", "Caste", "Caste maps jati to varna")]
          ["signal"] == "retired")
    check("gate 8: confidence under the floor is logged, not queued",
          links[("Caste", "depends_on", "Jati", "Caste maps jati to varna")]
          ["signal"] == "below_floor")
    check("an endpoint that is no concept is unknown_endpoint",
          links[("Nobody", "depends_on", "population", "A population")]
          ["signal"] == "unknown_endpoint")
    check("the self-link and the non-object are malformed, counted",
          s.skips["malformed"] == 2, str(s.skips))
    check("every candidate carries its gate and reason, and the tally adds "
          "up", sum(s.skips.values()) - s.skips["malformed"]
          + len(s.survivors) == len(links), json.dumps(s.skips))


def check_concept_gates(root: Path) -> None:
    print("concept gates:")
    db, ms = fx.build(root / "concepts")
    fx.add_node(db, ms, "ordering", status="retired", confirmed=False)
    fx.add_node(db, ms, "hierarchies", status="retired", confirmed=False)
    knob = extraction.knobs({})
    text = fx.FILES["hierarchy.md"]
    first = fx.paragraph_number(text, "Every society sorts")
    rankings = fx.paragraph_number(text, "A hierarchy is the ordering")
    rank = fx.paragraph_number(text, "A rank is a place")
    kinds = fx.paragraph_number(text, "Populations come in kinds")
    race = fx.paragraph_number(text, "Race is a deme")

    def concept(name, n, span, kind="concept", confidence=0.9):
        return ("concepts", {"name": name, "kind": kind, "paragraphs": [n],
                             "span": span, "confidence": confidence,
                             "notes": f"{name}, as the text uses it"})

    items = [
        concept("Sorting", first, "Every society sorts its members"),
        concept("deme", race, "Race is a deme too"),
        concept("ordering", rankings, "A hierarchy is the ordering"),
        concept("hierarchy", rankings, "A hierarchy is the ordering",
                kind="historical_reference"),
        concept("Populations", kinds, "Populations come in kinds"),
        concept("ranking", rank, "A ranking assigns each member a rank"),
        concept("the ranking", rank, "outside the ranking that gave it",
                kind="historical_reference"),
        concept("trajectories", first, "Every society sorts its members"),
        concept("Ghost", rank, "A rank is a place in the ordering"),
    ]
    payloads = extraction.assemble_essay(db, ms, "hierarchy.md", knob=knob)
    s = extraction.screen_replies(db, ms, payloads,
                                  replies_for(payloads, items), knob)
    got = {c["name"]: c for c in s.candidates if c["type"] == "concept"}
    check("gate 3: a single word capitalised only where a sentence starts "
          "is an English word — common_word",
          got["Sorting"]["signal"] == "common_word"
          and got["Populations"]["signal"] == "common_word",
          str({k: v.get("signal") for k, v in got.items()}))
    check("gate 4: a concept in one context fails the recurrence bar",
          got["deme"]["signal"] == "below_bar", str(got["deme"]))
    check("gate 5: a retired name that recurs is still banned",
          got["ordering"]["signal"] == "retired"
          and got["ordering"]["reason"] == "the author retired this name",
          str(got["ordering"]))
    check("gate 5: so is a near-miss of a retired name (difflib 0.8)",
          got["hierarchy"]["signal"] == "retired"
          and "hierarchies" in got["hierarchy"]["reason"],
          str(got["hierarchy"]))
    check("a name already in the graph is an improves/subsumed question, "
          "never a new node: it skips gates 3-5 and carries the node",
          got["ranking"].get("signal") is None
          and got["ranking"]["existing"] == "ranking")
    check("§4.7: a name within the firewall's distance of a live concept "
          "goes to the adjudicator as a subsumption question",
          got["the ranking"].get("signal") is None
          and got["the ranking"]["near"] == "ranking",
          str(got["the ranking"]))
    check("gate 1-2: a name its cited paragraph does not hold is ungrounded",
          got["trajectories"]["signal"] == "ungrounded"
          and got["Ghost"]["signal"] == "ungrounded")

    print("concept duplicates and the link to a fresh concept:")
    multi = {**fx.FILES, "hierarchy.md": fx.FILES["hierarchy.md"]
             + "\n## **Again**\n\nThe Accord Line holds where the accord "
               "holds; the Accord Line is drawn by the population.\n\n"
               "## **Still**\n\nThe Accord Line again, and the population "
               "it keeps.\n"}
    db2, ms2 = fx.build(root / "dups", files=multi)
    text2 = multi["hierarchy.md"]
    a = fx.paragraph_number(text2, "The Accord Line holds")
    b = fx.paragraph_number(text2, "The Accord Line again")
    payloads2 = extraction.assemble_essay(db2, ms2, "hierarchy.md", knob=knob)
    items2 = [
        concept("Accord Line", a, "The Accord Line holds where the accord "
                "holds", confidence=0.8),
        concept("Accord Line", b, "The Accord Line again", confidence=0.95),
        ("links", {"from": "Accord Line", "relation": "depends_on",
                   "to": "population", "paragraph": a,
                   "span": "the Accord Line is drawn by the population",
                   "confidence": 0.9}),
    ]
    s2 = extraction.screen_replies(db2, ms2, payloads2,
                                   replies_for(payloads2, items2), knob)
    lines = [c for c in s2.candidates if c["type"] == "concept"]
    link = next(c for c in s2.candidates if c["type"] == "link")
    check("the same new name from two sections folds into the stronger",
          [c.get("signal") for c in lines] == ["duplicate", None]
          and lines[1]["confidence"] == 0.95, str(lines))
    check("a link may end on a concept candidate of the same essay that "
          "survived", link.get("signal") is None
          and link["_ends"][0][0] == "new:accord line", str(link))
    rendered = extraction.render_screening(s2)
    check("the printed tally names every gate's count and each fate",
          "gates: 2 of 3 candidate(s) passed; duplicate 1" in rendered
          and "✗ Accord Line (concept)" in rendered, rendered)


def check_revival(root: Path) -> None:
    print("§4.10 — a retired name yields only to insistence:")
    db, ms = fx.build(root / "revival")
    node = fx.add_node(db, ms, "Vigilance", status="realized",
                       confirmed=False)
    retire_concept(db, ms["id"], dict(node))
    node = dict(db.one("SELECT * FROM concept_nodes WHERE id = ?",
                       (node["id"],)))
    check("retire_concept stamps when (retired_from.at)",
          (loads(node["metadata"], {})["retired_from"] or {}).get("at"))
    files = dict(fx.FILES)
    check("no recurrence in text written since retirement — no ask",
          extraction.revival_due(db, ms, node, files) is None)

    def write(extra: dict[str, str]) -> dict[str, str]:
        source = Path(ms["path"])
        for name, add in extra.items():
            files[name] = files[name] + add
            (source / name).write_text(files[name], encoding="utf-8")
        collect_revision(db, dict(ms), session_id=None, source="test")
        return files

    write({"hierarchy.md": "\n## **Vigilance**\n\nVigilance is the labor "
                           "of keeping a ranking honest.\n",
           "mixing.md": "\nA mixed population needs Vigilance most.\n"})
    due = extraction.revival_due(db, ms, node, files)
    check("recurring past the bar in new text (two files; the heading is a "
          "context too) — one ask is due, with its spans",
          due is not None and due["recurrence"]["contexts"] == 3
          and any("labor of keeping" in c["span"] for c in due["contexts"]),
          str(due))
    knob = extraction.knobs({})
    v = fx.paragraph_number(files["hierarchy.md"], "Vigilance is the labor")
    payloads = extraction.assemble_essay(db, ms, "hierarchy.md", knob=knob)
    replies = replies_for(payloads, [("concepts", {
        "name": "Vigilance", "kind": "concept", "paragraphs": [v],
        "span": "Vigilance is the labor of keeping a ranking honest",
        "confidence": 0.9})])
    s = extraction.screen_replies(
        db, ms, payloads, replies, knob,
        revive=extraction.reviver(db, ms, files, "extract-test"))
    cand = next(c for c in s.candidates if c["type"] == "concept")
    row = db.one("SELECT * FROM knowledge_proposals WHERE kind = 'revival'")
    check("gate 5 still holds the name, and raises the one revival proposal",
          cand["signal"] == "retired" and cand.get("proposal_id")
          and row is not None and row["source"] == "extraction/extract-test",
          str(cand))
    payload = loads(row["payload"], {})
    check("the proposal carries the spans and the count the next ask is "
          "measured against",
          payload["recurrence"]["contexts"] == 3 and payload["spans"][0]
          ["span"].startswith("Vigilance is the labor"), str(payload))
    check("while it is open, no second ask",
          extraction.revival_due(db, ms, node, files) is None)
    proposals.dismiss(db, ms["id"], dict(row))
    check("dismissed without a reason: not again until the count doubles",
          extraction.revival_due(db, ms, node, files) is None)
    write({"good-choice.md": "\nVigilance again.\n",
           "discernment.md": "\nVigilance, and vigilance.\n"})
    check("...not at 5 contexts, under double the 3 it was asked at",
          extraction.revival_due(db, ms, node, files) is None)
    write({"preface.md": "\nVigilance at last.\n"})
    check("...and once it has doubled (3 → 6 contexts), asked again",
          extraction.revival_due(db, ms, node, files) is not None)
    second = extraction.raise_revival(
        db, ms, cand, node, extraction.revival_due(db, ms, node, files), "r2")
    proposals.dismiss(db, ms["id"], dict(second),
                      reason="Vigilance stays a word here, not a term")
    node = dict(db.one("SELECT * FROM concept_nodes WHERE id = ?",
                       (node["id"],)))
    check("dismissed WITH a reason: retired for good, never asked again",
          loads(node["metadata"], {}).get("retired_for_good") is True
          and extraction.revival_due(db, ms, node, files) is None)


def main_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-extract-gates-"))
    try:
        check_section_2_4(root)
        check_link_gates(root)
        check_concept_gates(root)
        check_revival(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nAll {PASSED} checks passed.")


if __name__ == "__main__":
    main_test()
