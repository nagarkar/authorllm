"""The twenty edges of docs/extraction-redesign.md §2.4, as a fixture.

The author's manuscripts are gitignored and this repository is public, so
the text below is NOT hierarchy.md. It is a small manuscript written
around the sentences §2.4 itself quotes (already public in the design
doc), with the one property of the real chapters the gates can see kept
exactly: which endpoint pairs share a paragraph and which do not. #1, #8,
#14 and #19 share none (co-paragraph 0 in §2.4); every other pair shares
at least one.

Each EDGES row is the edge as the old extractor proposed it, the
paragraph a model answering the new contract would cite (found by a
marker substring, so the numbers follow the text), the span it would
quote, and §2.4's verdict.
"""

from __future__ import annotations

import json
from pathlib import Path

from authorlm import api
from authorlm.db import Database
from authorlm.revisions import collect_revision, _paragraphs

FILES = {
    "preface.md": (
        "# **Preface**\n\n"
        "The Dharma is the field of choice, and Samsara is named as the "
        "Dharma's own play: not a thing the Dharma made, but the Dharma at "
        "play.\n"),
    "hierarchy.md": (
        "# **The Ordering of Society**\n\n"
        "Every society sorts its members. This essay asks what the sorting "
        "is made of, and why it keeps failing the people it sorts.\n\n"
        "## **Populations**\n\n"
        "A population is a bundle of individual trajectories in sufficient "
        "accord. The accord is what lets a population act, and what it "
        "loses when the accord frays.\n\n"
        "Populations come in kinds. A mimetic population is a population "
        "held together by imitation; an ideological population is one held "
        "together by a shared account of the world.\n\n"
        "## **Rankings**\n\n"
        "A hierarchy is the ordering, by that metric, of a population. "
        "Every ranking needs a metric before it can begin, and every "
        "ranking needs a population to order.\n\n"
        "A rank is a place in the ordering. A ranking assigns each member a "
        "rank, and the rank means nothing outside the ranking that gave "
        "it.\n\n"
        "An ideological population turns its ranking into a creed: the "
        "metric becomes a belief, and doubting the ranking becomes "
        "heresy.\n\n"
        "## **Caste**\n\n"
        "Jati is the endogamous population; endogamy is the practice that "
        "keeps a jati whole across generations.\n\n"
        "Caste maps jati to varna. The mapping is the ranking; the jati is "
        "what gets ranked.\n\n"
        "Caste hardened the ranking into birth, and the ranks stopped "
        "moving.\n\n"
        "## **Race**\n\n"
        "Race is a deme too: a population drawn by descent and then ordered "
        "by the drawing.\n\n"
        "## **Supremacism**\n\n"
        "A supremacism is a ranking that has forgotten its metric.\n"),
    "mixing.md": (
        "# **Mixing**\n\n"
        "Cultural relativism holds that no culture can be judged from "
        "outside itself.[^1]\n\n"
        "The mixing of populations tests that claim, because a mixed "
        "population judges both of its sources from inside.\n\n"
        "[^1]: Franz Boas, the anthropologist from whom the doctrine is "
        "usually traced.\n"),
    "good-choice.md": (
        "# **The Good Choice**\n\n"
        "The Good Choice is action that enlarges the capacity for Becoming. "
        "It is measured by what it opens, not by what it secures.\n\n"
        "The good choice requires awareness of what may yet arise. "
        "Awareness comes first; without it the Good Choice is only luck.\n\n"
        "A deception may be born to perish, or it may sustain itself; the "
        "essay treats both as failures of awareness.\n"),
    "discernment.md": (
        "# **Discernment**\n\n"
        "Experience requires distinction: what is not told apart is not "
        "experienced at all.\n\n"
        "The tradition treated supremacism as a contraction of discernment, "
        "a narrowing of what one is willing to tell apart.\n"),
}

# (name, kind, aliases) — every endpoint of the twenty, live in the graph
# as it was when §2.4 was sampled.
NODES = [
    ("Franz Boas", "historical_reference", []),
    ("Cultural relativism", "concept", []),
    ("ideological population", "concept", []),
    ("ranking", "concept", []),
    ("Jati", "concept", []),
    ("endogamy", "concept", []),
    ("population", "concept", []),
    ("accord", "concept", []),
    ("rank", "concept", []),
    ("metric", "concept", []),
    ("Deception born to perish", "concept", []),
    ("Sustaining deception", "concept", []),
    ("Good Choice", "concept", []),
    ("capacity for Becoming", "concept", []),
    ("Awareness", "concept", []),
    ("Dharma", "concept", []),
    ("Samsara", "concept", []),
    ("Discernment", "concept", ["Experience"]),
    ("Experience", "concept", []),
    ("Caste", "concept", []),
    ("Race", "concept", []),
    ("supremacism", "concept", []),
    ("Contraction of discernment", "concept", []),
    ("memetic population", "concept", ["mimetic population"]),
]

# §2.4, one row per edge. `at` is a substring of the paragraph the
# candidate cites; `span` is what it quotes. `gate` is what the eight
# gates must do with it under the default proposable set: None passes,
# else (skip-count name, gate number).
EDGES = [
    dict(n=1, src="Franz Boas", rel="elaborates", dst="Cultural relativism",
         file="mixing.md", at="no culture can be judged",
         span="Cultural relativism holds that no culture can be judged from "
              "outside itself.", verdict="reject", gate=("ungrounded", 2)),
    dict(n=2, src="ideological population", rel="specializes", dst="ranking",
         file="hierarchy.md", at="turns its ranking into a creed",
         span="An ideological population turns its ranking into a creed",
         verdict="reject", gate=None),
    dict(n=3, src="Jati", rel="specializes", dst="endogamy",
         file="hierarchy.md", at="Jati is the endogamous population",
         span="Jati is the endogamous population", verdict="reject",
         gate=None),
    dict(n=4, src="population", rel="depends_on", dst="accord",
         file="hierarchy.md", at="bundle of individual trajectories",
         span="A population is a bundle of individual trajectories in "
              "sufficient accord", verdict="confirm", gate=None),
    dict(n=5, src="rank", rel="depends_on", dst="ranking",
         file="hierarchy.md", at="A rank is a place in the ordering",
         span="A rank is a place in the ordering", verdict="confirm",
         gate=None),
    dict(n=6, src="ranking", rel="depends_on", dst="population",
         file="hierarchy.md", at="A hierarchy is the ordering",
         span="A hierarchy is the ordering, by that metric, of a population",
         verdict="confirm", gate=None),
    dict(n=7, src="ranking", rel="depends_on", dst="metric",
         file="hierarchy.md", at="A hierarchy is the ordering",
         span="A hierarchy is the ordering, by that metric, of a population",
         verdict="confirm", gate=None),
    dict(n=8, src="Deception born to perish", rel="contrasts_with",
         dst="Sustaining deception", file="good-choice.md",
         at="A deception may be born to perish",
         span="A deception may be born to perish, or it may sustain itself",
         verdict="reject", gate=("ungrounded", 2)),
    dict(n=9, src="Good Choice", rel="leads_to", dst="capacity for Becoming",
         file="good-choice.md", at="enlarges the capacity for Becoming",
         span="action that enlarges the capacity for Becoming",
         verdict="confirm", gate=("not_proposable", 7)),
    dict(n=10, src="Good Choice", rel="depends_on", dst="Awareness",
         file="good-choice.md", at="requires awareness",
         span="The good choice requires awareness of what may yet arise",
         verdict="confirm", gate=None),
    dict(n=11, src="Dharma", rel="creates", dst="Samsara",
         file="preface.md", at="Samsara is named",
         span="Samsara is named as the Dharma's own play",
         verdict="retype", gate=("not_proposable", 7)),
    dict(n=12, src="Discernment", rel="defines", dst="Experience",
         file="discernment.md", at="Experience requires distinction",
         span="Experience requires distinction", verdict="retype",
         gate=("not_proposable", 7)),
    dict(n=13, src="Jati", rel="leads_to", dst="Caste",
         file="hierarchy.md", at="Caste maps jati to varna",
         span="Caste maps jati to varna", verdict="reject",
         gate=("not_proposable", 7)),
    dict(n=14, src="Jati", rel="generalizes", dst="Race",
         file="hierarchy.md", at="Race is a deme too",
         span="Race is a deme too", verdict="reject", gate=("ungrounded", 2)),
    dict(n=15, src="endogamy", rel="permits", dst="Jati",
         file="hierarchy.md", at="Jati is the endogamous population",
         span="endogamy is the practice that keeps a jati whole",
         verdict="reject", gate=None),
    dict(n=16, src="ranking", rel="leads_to", dst="rank",
         file="hierarchy.md", at="A rank is a place in the ordering",
         span="A ranking assigns each member a rank", verdict="reject",
         gate=("duplicate", 6)),
    dict(n=17, src="metric", rel="leads_to", dst="ranking",
         file="hierarchy.md", at="A hierarchy is the ordering",
         span="Every ranking needs a metric before it can begin",
         verdict="reject", gate=("duplicate", 6)),
    dict(n=18, src="supremacism", rel="leads_to",
         dst="Contraction of discernment", file="discernment.md",
         at="contraction of discernment",
         span="treated supremacism as a contraction of discernment",
         verdict="retype", gate=("not_proposable", 7)),
    dict(n=19, src="Caste", rel="leads_to", dst="supremacism",
         file="hierarchy.md", at="Caste hardened the ranking",
         span="Caste hardened the ranking into birth", verdict="reject",
         gate=("ungrounded", 2)),
    dict(n=20, src="memetic population", rel="specializes", dst="population",
         file="hierarchy.md", at="A mimetic population is",
         span="A mimetic population is a population held together by "
              "imitation", verdict="confirm", gate=None),
]


def paragraph_number(text: str, marker: str) -> int:
    """The file-wide [n] of the paragraph containing `marker` — the
    numbering `summaries._numbered_paragraphs` prints."""
    for n, para in enumerate(_paragraphs(text), 1):
        if marker in para:
            return n
    raise LookupError(marker)


def link_candidate(row: dict, confidence: float = 0.9) -> dict:
    """The row as a model answering §4.4's contract would return it."""
    return {"from": row["src"], "relation": row["rel"], "to": row["dst"],
            "paragraph": paragraph_number(FILES[row["file"]], row["at"]),
            "span": row["span"], "confidence": confidence,
            "why": f"§2.4 #{row['n']}"}


def build(root: Path, files: dict[str, str] | None = None,
          nodes: list | None = None, *, workspace: bool = False
          ) -> tuple[Database, dict]:
    """A workspace holding the fixture manuscript, collected once, with
    every endpoint as a live, machine-extracted, confirmed node.
    `workspace` puts the database where `--workspace root` looks for it."""
    source = root / "manuscript"
    source.mkdir(parents=True)
    for name, text in (files or FILES).items():
        (source / name).write_text(text, encoding="utf-8")
    if workspace:
        (root / ".authorlm").mkdir(parents=True, exist_ok=True)
    db = Database(root / (".authorlm/authorlm.db" if workspace
                          else "authorlm.db"))
    manuscript = api.register_manuscript(db, "fixture", str(source))
    collect_revision(db, dict(manuscript), session_id=None, source="test")
    manuscript = dict(db.one("SELECT * FROM manuscripts WHERE id = ?",
                             (manuscript["id"],)))
    for name, kind, aliases in (NODES if nodes is None else nodes):
        add_node(db, manuscript, name, kind, aliases)
    return db, manuscript


def add_node(db: Database, manuscript: dict, name: str, kind: str = "concept",
             aliases: list[str] | None = None, *, status: str = "realized",
             extracted: bool = True, confirmed: bool = True,
             notes: str | None = None, introduced_in: str | None = None
             ) -> dict:
    from authorlm.db import ko_fields

    row = ko_fields("cn")
    meta = {"origin": "extracted", "confirmed": confirmed} if extracted else {}
    row.update(manuscript_id=manuscript["id"], name=name, kind=kind,
               status=status, introduced_in=introduced_in, notes=notes,
               aliases=json.dumps(aliases or []),
               metadata=json.dumps(meta), source_id=db.source("system"))
    db.insert("concept_nodes", row)
    return row
