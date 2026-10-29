"""`extract calibrate --replay` (docs/extraction-redesign.md §4.6, §6.1):
the historical candidates replayed through the gates and the adjudicator,
measured against the author's own verdicts, and the three thresholds of
§4.6 moved on the measurement. Zero author cost; modelled on the
retired tools/measure_adjudication.py (track-a §4, now superseded).

WHAT IS REPLAYED
  * Candidates: every machine-extracted concept (`concept_nodes` with
    `metadata.origin = extracted`) and every relation the machine
    proposed or the author settled — an edge from the system source, or
    one inferred, rejected, declared or triaged — as the old extractor
    proposed it: for a retyped edge, the relation before the retype. A
    declared edge counts as a kept candidate, as §2.2 counts it: edges
    settled before the
    sources registry were backfilled to the author, so provenance cannot
    tell a hand-declared edge from a confirmed extracted one.
  * Labels: the latest `concept_triage` / `edge_triage` row (confirmed
    and retyped are kept, rejected and merged are not), else the row's
    own state (a retired node or rejected edge was rejected; a confirmed
    node or declared edge kept). An edge retired with its endpoint, and
    anything still pending, carries no label and is measured by nobody.
  * Text: `manuscript_versions.files` of the version current when the
    candidate was created. Historical candidates carry no span (§6.1's
    honest limit), so each is re-located: a concept in the first
    paragraph, in reading order, naming it; a relation in the first
    paragraph naming both ends, else the first naming one (and gate 2
    then fails it). The span is that paragraph's first sentence naming it.
  * Gates: all eight, essay by essay — except that gate 7 is MEASURED,
    not applied: every relation goes on to the adjudicator, so a relation
    outside the proposable set can earn its way back in (gate 6 still
    ranks a proposable relation above one that is not, as a live run
    does). Gate 8 applies
    only where a confidence was recorded (no historical candidate has
    one). No summaries are read: they describe today's text, not the
    version a candidate came from.
  * The leave-out rule, so no verdict grades itself: each essay is
    replayed against the author's record of every OTHER essay. Its own
    candidates are absent from its glossary, its precedents, the
    retired-name ban and the rejected-relation ban.
  * The adjudicator: one call per essay over the survivors, the model
    chosen at run time as for `--native`. `--gates-only` replays the
    deterministic half alone, free; it reports and moves nothing.

WHAT MOVES (§4.6), recorded in `manuscripts.metadata.extraction.runs`
  * proposable_relations: a relation whose candidates the pipeline brings
    to the author at ≥ 60% precision (confirmed or retyped) on at least
    MIN_SUPPORT labelled candidates is proposable; under 60% on as many,
    it is not; with fewer, it keeps its place.
  * confidence_floor: the lowest floor, from the current one up, at which
    the labelled candidates that carry a confidence meet §2.5's bar
    (70% concepts, 75% relations); unchanged while too few carry one.
  * max_concepts_per_essay / max_links_per_essay: the largest ceiling, up
    to the configured one, that keeps the projected decisions of a full
    run within §2.5's 150, split between concepts and relations by their
    share of what reaches the author.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .concepts import concept_pattern, node_names
from .db import Database, loads, now_iso
from .revisions import _paragraphs

TARGET_CONCEPTS = 0.70      # §2.5: concept candidates the author confirms
TARGET_LINKS = 0.75         # §2.5: edge candidates confirmed or retyped
RELATION_BAR = 0.60         # §4.4 gate 7 / §4.6
DECISION_BUDGET = 150       # §2.5: decisions asked per full run
MIN_SUPPORT = 5             # labelled candidates before a threshold moves
FLOORS = tuple(round(0.50 + 0.05 * i, 2) for i in range(10))  # 0.50-0.95

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_EDGE_REASON = re.compile(r" [-—] reason: ")


@lru_cache(maxsize=None)
def _pattern(name: str):
    return concept_pattern(name)


def _names_in(names, text: str) -> bool:
    return any(_pattern(n).search(text) for n in names if n)


# ------------------------------------------------------------- the labels

def _concept_verdicts(db: Database, mid: str) -> dict[str, str]:
    """name (lower) → the latest concept_triage signal for it."""
    out: dict[str, str] = {}
    for row in db.all(
            "SELECT signal, target FROM evidence WHERE manuscript_id = ? AND "
            "evidence_type = 'concept_triage' ORDER BY created_at", (mid,)):
        head = row["target"].split(" — reason: ", 1)[0]
        if row["signal"] == "retyped":
            name = head.split(": ", 1)[0]
        elif row["signal"] == "merged":
            name = head.split(" → alias of ", 1)[0]
        else:
            m = re.match(r"^(.*) \([a-z_]+\)$", head)
            name = m.group(1) if m else head
        out[name.strip().lower()] = row["signal"]
    return out


def _edge_verdicts(db: Database, mid: str) -> dict[str, tuple[str, str | None]]:
    """'A —r→ B' as the edge reads NOW → (signal, the label before a
    retype, or None)."""
    out: dict[str, tuple[str, str | None]] = {}
    for row in db.all(
            "SELECT signal, target FROM evidence WHERE manuscript_id = ? AND "
            "evidence_type = 'edge_triage' ORDER BY created_at", (mid,)):
        head = _EDGE_REASON.split(row["target"], 1)[0]
        before, _, after = head.partition(" ⇒ ")
        out[(after or before).strip()] = (row["signal"],
                                          before.strip() if after else None)
    return out


_LABEL = {"confirmed": "kept", "retyped": "retyped", "rejected": "rejected",
          "merged": "merged"}
_GOOD = ("kept", "retyped")


def _confidences(db: Database, mid: str) -> dict[str, float]:
    """node or edge id → the confidence its new-pipeline candidate carried
    (from its `extraction_adjudication` row); historical rows have none."""
    out: dict[str, float] = {}
    for row in db.all(
            "SELECT metadata FROM evidence WHERE manuscript_id = ? AND "
            "evidence_type = 'extraction_adjudication' AND signal IN "
            "('new', 'retype')", (mid,)):
        meta = loads(row["metadata"], {}) or {}
        if meta.get("confidence") is None:
            continue
        for key in ("node_id", "edge_id"):
            if meta.get(key):
                out[meta[key]] = float(meta["confidence"])
    return out


_ARROW = re.compile(r"^(?P<a>.+?) —(?P<r>[a-z_]+)→ (?P<b>.+)$")


def historical(db: Database, manuscript: dict) -> dict:
    """{concepts, links, unresolved}: the machine's historical candidates
    with their labels (see the module docstring)."""
    mid = manuscript["id"]
    nodes = {r["id"]: dict(r) for r in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ?", (mid,))}
    by_name: dict[str, dict] = {}
    for node in nodes.values():
        by_name.setdefault(node["name"].lower(), node)
    verdicts = _concept_verdicts(db, mid)
    confidence = _confidences(db, mid)
    concepts = []
    for node in nodes.values():
        meta = loads(node["metadata"], {}) or {}
        if meta.get("origin") != "extracted":
            continue
        signal = verdicts.get(node["name"].lower())
        if signal in _LABEL:
            label, source = _LABEL[signal], "triage"
            label = "kept" if label == "retyped" else label
        elif node["status"] == "retired":
            label, source = "rejected", "state"
        elif meta.get("confirmed"):
            label, source = "kept", "state"
        else:
            label, source = None, None
        concepts.append({"id": node["id"], "node": node, "name": node["name"],
                         "kind": node["kind"], "notes": node["notes"],
                         "created_at": node["created_at"], "label": label,
                         "label_source": source,
                         "confidence": confidence.get(node["id"])})
    system = db.one("SELECT id FROM sources WHERE kind = 'system' LIMIT 1")
    edge_verdicts = _edge_verdicts(db, mid)
    links, unresolved = [], 0
    for e in db.all("SELECT * FROM concept_edges WHERE manuscript_id = ? "
                    "ORDER BY created_at", (mid,)):
        a, b = nodes.get(e["from_node"]), nodes.get(e["to_node"])
        if a is None or b is None:
            continue
        now = f"{a['name']} —{e['relation']}→ {b['name']}"
        verdict = edge_verdicts.get(now)
        machine = ((system and e["source_id"] == system["id"])
                   or e["status"] in ("inferred", "rejected", "declared")
                   or verdict)
        if not machine:
            continue
        src, relation, dst = a, e["relation"], b
        if verdict and verdict[1]:
            m = _ARROW.match(verdict[1])
            if not m or m.group("a").lower() not in by_name \
                    or m.group("b").lower() not in by_name:
                unresolved += 1
                continue
            src, relation, dst = (by_name[m.group("a").lower()], m.group("r"),
                                  by_name[m.group("b").lower()])
        if verdict and verdict[0] in _LABEL:
            label, source = _LABEL[verdict[0]], "triage"
        elif e["status"] == "rejected":
            label, source = "rejected", "state"
        elif e["status"] == "declared":
            label, source = "kept", "state"
        else:
            label = None
            source = "collateral" if e["status"] == "retired" else None
        links.append({"id": e["id"], "edge": dict(e), "from_node": src,
                      "to_node": dst, "from": src["name"], "relation": relation,
                      "to": dst["name"], "created_at": e["created_at"],
                      "label": label, "label_source": source,
                      "confidence": confidence.get(e["id"])})
    return {"concepts": concepts, "links": links, "unresolved": unresolved}


# --------------------------------------------------------- the relocation

class _Versions:
    """The manuscript's versions, their files loaded once, on demand."""

    def __init__(self, db: Database, mid: str):
        self.db = db
        self.rows = [dict(r) for r in db.all(
            "SELECT id, version_no, created_at FROM manuscript_versions "
            "WHERE manuscript_id = ? ORDER BY version_no", (mid,))]
        self._files: dict[str, dict] = {}
        self._layout: dict[str, list] = {}

    def at(self, created_at: str) -> str | None:
        """The version current when a candidate was created (the first,
        for one older than every version)."""
        chosen = None
        for row in self.rows:
            if row["created_at"] <= created_at:
                chosen = row["id"]
            else:
                break
        return chosen or (self.rows[0]["id"] if self.rows else None)

    def files(self, vid: str) -> dict[str, str]:
        if vid not in self._files:
            row = self.db.one("SELECT files FROM manuscript_versions WHERE "
                              "id = ?", (vid,))
            self._files[vid] = loads(row["files"], {}) if row else {}
        return self._files[vid]

    def layout(self, vid: str) -> list[tuple[str, list[tuple]]]:
        """[(file, [(n, paragraph, section heading, section ordinal)])], in
        reading order."""
        if vid not in self._layout:
            from .extraction import _plain_heading, eligible_files
            from .summaries import sections_of

            files = self.files(vid)
            out = []
            for name in eligible_files(files):
                paras = []
                for sec in sections_of(files[name]):
                    heading = _plain_heading(sec["heading"])
                    for p in sec["paragraphs"]:
                        paras.append((p["ordinal"], p["text"], heading,
                                      sec["section_ordinal"]))
                out.append((name, paras))
            self._layout[vid] = out
        return self._layout[vid]


def _best(hits: list[tuple], name: str, span_of) -> tuple | None:
    """The passage to judge a relocated row on: of every paragraph that
    carries the name, the one whose sentence grounds it most strongly
    (`grounding_strength`: a definition-shaped sentence or heading before
    a passing mention), the first in reading order among equals. The
    first mention alone sent "hierarchy" to be judged on "hierarchy of
    processes enveloped within each process" in another essay, and the
    adjudicator rightly dropped THAT span (2026-10-25)."""
    from .extraction import grounding_strength

    if not hits:
        return None
    return max(hits, key=lambda hit: grounding_strength(span_of(hit[2]), name,
                                                        hit[2]))


def _sentence(paragraph: str, *name_sets) -> str:
    sentences = _SENTENCE.split(paragraph)
    for names in name_sets:
        hit = next((s for s in sentences
                    if all(_names_in(ns, s) for ns in names)), None)
        if hit:
            return hit.strip()
    return sentences[0].strip()


def relocate(hist: dict, versions: _Versions,
             known: dict[str, dict] | None = None) -> list[dict]:
    """Every labelled-or-not historical candidate as a gate-ready
    candidate in the shape `extraction.parse_reply` gives, carrying its
    history in `_hist`, its version's text in `_files`, and `co_paragraph`
    for a relation (whether any paragraph names both ends). A relation's
    ends are named as the gates will resolve them — through `known`, the
    live graph's names and aliases — so the paragraph re-located and the
    paragraph gate 2 reads are judged by the same names."""
    known = known or {}

    def names_of(node: dict) -> list[str]:
        if node["status"] != "retired":
            return node_names(node)
        live = known.get(node["name"].lower())
        return node_names(live) if live else node_names(node)

    out = []
    for kind_key, kind in (("concepts", "concept"), ("links", "link")):
        for h in hist[kind_key]:
            vid = versions.at(h["created_at"])
            if vid is None:
                continue
            layout = versions.layout(vid)
            base = {"type": kind, "confidence": h.get("confidence"),
                    "why": "replayed", "_hist": h,
                    "_files": versions.files(vid)}
            if kind == "concept":
                names = node_names(h["node"])
                hits = [(f, n, p, head, so) for f, paras in layout
                        for n, p, head, so in paras if _names_in(names, p)]
                hit = _best(hits, h["name"], lambda p: _sentence(p, [names]))
                cand = {**base, "name": h["name"], "kind": h["kind"],
                        "notes": h["notes"], "_aliases": names[1:]}
                if hit is None:
                    cand.update(file=h["node"].get("introduced_in") or "?",
                                section=None, unit="?", paragraphs=[],
                                span="", _cited={})
                else:
                    f, n, p, head, so = hit
                    cand.update(file=f, section=head, unit=str(so),
                                paragraphs=[n], span=_sentence(p, [names]),
                                _cited={n: p})
            else:
                a, b = names_of(h["from_node"]), names_of(h["to_node"])
                boths = [(f, n, p, head, so) for f, paras in layout
                         for n, p, head, so in paras
                         if _names_in(a, p) and _names_in(b, p)]
                both = _best(boths, h["from"],
                             lambda p: _sentence(p, [a, b], [a], [b]))
                one = both or next(((f, n, p, head, so) for f, paras in layout
                                    for n, p, head, so in paras
                                    if _names_in(a, p) or _names_in(b, p)),
                                   None)
                cand = {**base, "from": h["from"], "relation": h["relation"],
                        "to": h["to"], "co_paragraph": both is not None}
                if one is None:
                    cand.update(file=h["from_node"].get("introduced_in") or "?",
                                section=None, unit="?", paragraph=None,
                                span="", _cited={})
                else:
                    f, n, p, head, so = one
                    cand.update(file=f, section=head, unit=str(so),
                                paragraph=n, span=_sentence(p, [a, b], [a], [b]),
                                _cited={n: p})
            out.append(cand)
    return out


# ------------------------------------------------------------- the replay

@dataclass
class Replay:
    groups: dict[str, list[dict]]           # essay → its candidates
    essay_payloads: dict[str, object]       # essay → adjudicator context
    knob: dict
    unresolved: int = 0
    adjudicated: bool = False
    model: str | None = None
    failed: list[str] = field(default_factory=list)

    @property
    def survivors(self) -> int:
        return sum(1 for cs in self.groups.values() for c in cs
                   if c.get("_gates_passed"))


def _context(db: Database, manuscript: dict, files: dict[str, str],
             mine: list[dict], candidate_edges: set[str]):
    """The gates' context for one essay, with the essay's own candidates
    left out of every list (the leave-out rule). Gate 6 folds against no
    candidate edge of any essay: which of two essays' mirrors a live run
    would have asked first is the reading order's to say (`measure`)."""
    from .extraction import gate_context

    ctx = gate_context(db, manuscript, files)
    own_nodes = {c["_hist"]["id"] for c in mine if c["type"] == "concept"}
    own_edges = candidate_edges
    ctx.known = {k: n for k, n in ctx.known.items() if n["id"] not in own_nodes}
    ctx.retired = {k: n for k, n in ctx.retired.items()
                   if n["id"] not in own_nodes}
    own_names = {c["name"] for c in mine if c["type"] == "concept"}
    ctx.rejected_names = [n for n in ctx.rejected_names if n not in own_names]
    own_triples = {(c["_hist"]["edge"]["from_node"],
                    c["_hist"]["edge"]["relation"],
                    c["_hist"]["edge"]["to_node"])
                   for c in mine if c["type"] == "link"}
    ctx.rejected = {t for t in ctx.rejected if t not in own_triples}
    ctx.edges = {pair: [e for e in edges if e["id"] not in own_edges]
                 for pair, edges in ctx.edges.items()}
    ctx.edges = {pair: edges for pair, edges in ctx.edges.items() if edges}
    return ctx


def prepare(db: Database, manuscript: dict, knob: dict,
            only: list[str] | None = None) -> Replay:
    """Load, relocate and gate the historical candidates (free). `only`
    restricts the replay to those essays."""
    from types import SimpleNamespace

    from .extraction import (_glossary_block, _precedent_block,
                             glossary_nodes, precedent_rows, screen)
    from .lenses import _title_of
    from .revisions import read_manuscript_files

    from .extraction import eligible_files, gate_context

    hist = historical(db, manuscript)
    versions = _Versions(db, manuscript["id"])
    latest = (versions.files(versions.rows[-1]["id"]) if versions.rows
              else read_manuscript_files(Path(manuscript["path"])))
    candidates = relocate(hist, versions,
                          gate_context(db, manuscript, latest).known)
    order = {name: i for i, name in enumerate(eligible_files(latest))}
    groups: dict[str, list[dict]] = {}
    for c in sorted(candidates, key=lambda c: order.get(c["file"], 10**6)):
        groups.setdefault(c["file"], []).append(c)
    if only:
        groups = {f: cs for f, cs in groups.items() if f in only}
    candidate_edges = {h["id"] for h in hist["links"]}
    payloads = {}
    for essay, mine in groups.items():
        ctx = _context(db, manuscript, latest, mine, candidate_edges)
        screen(mine, ctx, knob, apply_gate7=False)
        for c in mine:
            c["_gates_passed"] = c.get("signal") is None
        text = latest.get(essay, "")
        own = {c["_hist"]["id"] for c in mine if c["type"] == "concept"}
        nodes, edges = glossary_nodes(db, manuscript, essay, text, exclude=own) \
            if essay in latest else ([], [])
        prec = precedent_rows(db, manuscript, essay, text,
                              int(knob["precedents_nearest"]), exclude=own)
        payloads[essay] = SimpleNamespace(
            file=essay, title=_title_of(text) or essay,
            glossary=_glossary_block(nodes, edges),
            precedents=_precedent_block(prec, essay), summary_unit=None)
    return Replay(groups=groups, essay_payloads=payloads, knob=knob,
                  unresolved=hist["unresolved"])


def estimate(replay: Replay) -> dict:
    """The adjudication cost estimate for the replay, in
    `extraction_adjudicator.estimate_scope`'s shape: the survivor count is
    known, nothing is assumed."""
    from . import extraction_adjudicator as adj

    system = len(adj.adjudicator_system(replay.knob["proposable_relations"]))
    shared = sum(system + len(p.glossary) + len(p.precedents)
                 for f, p in replay.essay_payloads.items()
                 if any(c.get("_gates_passed") for c in replay.groups[f]))
    n = replay.survivors
    return {"essays": len(replay.groups), "sections": 0, "candidates": n,
            "assumed": False, "extraction_in": 0, "extraction_out": 0,
            "adjudication_in": shared // adj.CHARS_PER_TOKEN
            + adj.CANDIDATE_TOKENS_IN * n,
            "adjudication_out": adj.VERDICT_TOKENS_OUT * n}


def adjudicate(replay: Replay, judge_llm) -> None:
    """One adjudicator call per essay over its survivors; verdicts folded
    in by `extraction.decide`. An essay whose call fails is listed in
    `failed` and measured on its gates alone."""
    from . import extraction_adjudicator as adj
    from .extraction import decide

    replay.adjudicated = True
    replay.model = judge_llm.model
    for essay, mine in replay.groups.items():
        passed = [c for c in mine if c.get("_gates_passed")]
        if not passed:
            continue
        verdicts = adj.adjudicate(judge_llm, replay.essay_payloads[essay],
                                  passed, replay.knob["proposable_relations"])
        if verdicts is None:
            replay.failed.append(essay)
            continue
        decide(passed, verdicts, replay.knob)


# ------------------------------------------------------------ the measure

def _reaches(c: dict, adjudicated: bool) -> bool:
    if not c.get("_gates_passed"):
        return False
    if not adjudicated:
        return True
    return c.get("signal") in ("new", "retype") and (
        c["type"] == "link" or c.get("signal") == "new")


def _precision(rows: list[dict]) -> dict:
    labelled = [c for c in rows if c["_hist"]["label"]]
    good = sum(1 for c in labelled if c["_hist"]["label"] in _GOOD)
    return {"n": len(labelled), "good": good,
            "precision": round(good / len(labelled), 3) if labelled else None}


def measure(replay: Replay, knob: dict, configured: dict) -> dict:
    """Precision per kind and per relation (today: every labelled
    candidate; after the gates; after the whole pipeline), §2.5's table,
    and the thresholds moved by §4.6's rules. `configured` holds the
    ceilings from config.toml, the most a ceiling may move back up to."""
    adjudicated = replay.adjudicated and bool(replay.model)
    everything = [c for cs in replay.groups.values() for c in cs]
    for c in everything:
        c["_reached"] = _reaches(c, adjudicated)
    # Essay by essay, in reading order, as a live run goes: a relation an
    # earlier essay brought to the author is in the graph by the time a
    # later essay proposes it, or its mirror, and folds there.
    asked: set[frozenset] = set()
    folded_across = 0
    for cs in replay.groups.values():
        # A concept the essay itself proposes is `new:<name>` to the gates;
        # here it is the node it historically was, as in every other essay.
        own = {f"new:{c['name'].lower()}": c["_hist"]["id"] for c in cs
               if c["type"] == "concept"}

        def pair(c: dict) -> frozenset:
            return frozenset(own.get(e[0], e[0]) for e in c["_ends"][:2])

        mine = sorted((c for c in cs if c["type"] == "link"
                       and c["_reached"]), key=lambda c: -c["score"])
        for c in mine:
            if pair(c) in asked:
                c["_reached"] = False
                folded_across += 1
        asked |= {pair(c) for c in mine if c["_reached"]}

    def table(key: str, kind: str) -> dict:
        # Keyed by what was PROPOSED (`_hist`): a retype moves the
        # candidate's relation, but gate 7 judges the one extraction named.
        out = {}
        rows = [c for c in everything if c["type"] == kind]
        for value in sorted({c["_hist"][key] for c in rows}):
            sub = [c for c in rows if c["_hist"][key] == value]
            out[value] = {"today": _precision(sub),
                          "gates": _precision([c for c in sub
                                               if c["_gates_passed"]]),
                          "pipeline": _precision([c for c in sub
                                                  if c["_reached"]])}
        return out

    by_kind = table("kind", "concept")
    by_relation = table("relation", "link")
    skips: dict[str, int] = {}
    for c in everything:
        if not c["_gates_passed"]:
            skips[c["signal"]] = skips.get(c["signal"], 0) + 1

    # §4.6 — the proposable set
    proposable = set(knob["proposable_relations"])
    moved: list[str] = []
    from .extraction import VALID_RELATIONS

    if adjudicated:
        for relation, cells in by_relation.items():
            if relation not in VALID_RELATIONS:
                continue                # dead vocabulary (co_occurs) never returns
            p = cells["pipeline"]
            if p["n"] < MIN_SUPPORT or p["precision"] is None:
                continue
            if p["precision"] >= RELATION_BAR and relation not in proposable:
                proposable.add(relation)
                moved.append(f"{relation} in ({p['precision']:.0%} of {p['n']})")
            elif p["precision"] < RELATION_BAR and relation in proposable:
                proposable.discard(relation)
                moved.append(f"{relation} out ({p['precision']:.0%} of "
                             f"{p['n']})")

    reached_concepts = [c for c in everything if c["type"] == "concept"
                        and c["_reached"]]
    reached_links = [c for c in everything if c["type"] == "link"
                     and c["_reached"]
                     and c["_hist"]["relation"] in proposable]

    # §4.6 — the confidence floor
    floor = float(knob["confidence_floor"])
    new_floor = floor
    floors = []
    for rows, target in ((reached_concepts, TARGET_CONCEPTS),
                         (reached_links, TARGET_LINKS)):
        carrying = [c for c in rows if c.get("confidence") is not None
                    and c["_hist"]["label"]]
        if len(carrying) < MIN_SUPPORT:
            continue
        for f in (x for x in FLOORS if x >= floor):
            sub = [c for c in carrying if c["confidence"] >= f]
            p = _precision(sub)
            if p["n"] >= MIN_SUPPORT and p["precision"] >= target:
                floors.append(f)
                break
    if adjudicated and floors:
        new_floor = max(floors)
        if new_floor != floor:
            moved.append(f"confidence floor {floor:.2f} → {new_floor:.2f}")

    # §4.6 — the per-essay ceilings
    in_c = {id(c) for c in reached_concepts}
    in_l = {id(c) for c in reached_links}
    per_essay_c = [sum(1 for c in cs if id(c) in in_c)
                   for cs in replay.groups.values()]
    per_essay_l = [sum(1 for c in cs if id(c) in in_l)
                   for cs in replay.groups.values()]
    total = sum(per_essay_c) + sum(per_essay_l)
    budget_c = round(DECISION_BUDGET * sum(per_essay_c) / total) if total else 0
    budget_l = DECISION_BUDGET - budget_c

    def ceiling(per_essay: list[int], budget: int, most: int) -> int:
        for cap in range(int(most), -1, -1):
            if sum(min(n, cap) for n in per_essay) <= budget:
                return cap
        return 0

    cap_c = ceiling(per_essay_c, budget_c,
                    configured.get("max_concepts_per_essay",
                                   knob["max_concepts_per_essay"]))
    cap_l = ceiling(per_essay_l, budget_l,
                    configured.get("max_links_per_essay",
                                   knob["max_links_per_essay"]))
    if adjudicated:
        for name, before, after in (
                ("max_concepts_per_essay", knob["max_concepts_per_essay"],
                 cap_c),
                ("max_links_per_essay", knob["max_links_per_essay"], cap_l)):
            if int(before) != after:
                moved.append(f"{name} {before} → {after}")
    projected = (sum(min(n, cap_c) for n in per_essay_c)
                 + sum(min(n, cap_l) for n in per_essay_l))

    reached_pairs = {(c["from"].lower(), c["to"].lower()) for c in reached_links}
    section_2_5 = {
        "concepts confirmed": (_precision(reached_concepts)["precision"],
                               TARGET_CONCEPTS),
        "edges confirmed or retyped": (_precision(reached_links)["precision"],
                                       TARGET_LINKS),
        "edges with no co-paragraph reaching triage": (
            sum(1 for c in reached_links if not c.get("co_paragraph")), 0),
        "mirror-duplicate edges reaching triage": (
            sum(1 for c in reached_links
                if (c["to"].lower(), c["from"].lower()) in reached_pairs), 0),
        "decisions per full run (projected)": (projected, DECISION_BUDGET),
    }
    labels: dict[str, int] = {}
    for c in everything:
        key = c["_hist"]["label_source"] or "unlabelled"
        labels[key] = labels.get(key, 0) + 1
    thresholds = {"proposable_relations": sorted(proposable),
                  "confidence_floor": new_floor,
                  "max_concepts_per_essay": cap_c,
                  "max_links_per_essay": cap_l}
    before = {k: knob[k] for k in thresholds}
    if not adjudicated:
        thresholds = dict(before)
        moved = []
    return {"essays": len(replay.groups),
            "concepts": sum(1 for c in everything if c["type"] == "concept"),
            "links": sum(1 for c in everything if c["type"] == "link"),
            "unresolved": replay.unresolved, "labels": labels,
            "skips": skips, "folded_across": folded_across, "by_kind": by_kind, "by_relation": by_relation,
            "section_2_5": section_2_5, "before": before,
            "thresholds": thresholds, "moved": moved,
            "adjudicated": adjudicated, "model": replay.model,
            "failed": list(replay.failed),
            "proposable_now": sorted(knob["proposable_relations"])}


def record(db: Database, manuscript: dict, report: dict, run_id: str,
           cost: float | None) -> dict:
    """The calibration run, with the thresholds it moved, appended to
    `manuscripts.metadata.extraction.runs` — where `extraction.knobs`
    reads them back. Only an adjudicated replay is recorded."""
    from .extraction import record_run

    entry = {"id": run_id, "started": now_iso(), "kind": "calibrate",
             "model": report["model"], "units": report["essays"],
             "cost": round(cost, 4) if cost is not None else None,
             "candidates": {"concepts": report["concepts"],
                            "links": report["links"]},
             "precision": {k: v[0] for k, v in report["section_2_5"].items()},
             "before": report["before"], "thresholds": report["thresholds"],
             "moved": report["moved"]}
    record_run(db, manuscript, entry)
    return entry


# ------------------------------------------------------------- the print

def _cell(p: dict) -> str:
    if not p["n"]:
        return "     —     "
    return f"{p['precision']:>5.0%} of {p['n']:<3}"


def render(report: dict) -> str:
    lines = [f"replay: {report['essays']} essay(s), {report['concepts']} "
             f"concept and {report['links']} relation candidate(s); labels "
             + ", ".join(f"{k} {v}" for k, v in sorted(report["labels"].items()))
             + (f"; {report['unresolved']} retyped edge(s) unresolvable"
                if report["unresolved"] else "")]
    if report["failed"]:
        lines.append("adjudicator failed for: " + ", ".join(report["failed"])
                     + " (measured on the gates alone)")
    lines.append("gates held back: " + (", ".join(
        f"{k} {v}" for k, v in sorted(report["skips"].items())) or "nothing")
        + (f"; {report['folded_across']} relation(s) folded into an earlier "
           "essay's" if report.get("folded_across") else ""))
    for title, key in (("concepts by kind", "by_kind"),
                       ("relations proposed", "by_relation")):
        if not report[key]:
            continue
        lines.append("")
        lines.append(f"{title:<24} {'today':<11} {'after gates':<11}"
                     + (" after the adjudicator" if report["adjudicated"]
                        else ""))
        for value, cells in report[key].items():
            mark = ""
            if key == "by_relation":
                mark = " *" if value in report["proposable_now"] else "  "
            lines.append(f"{value + mark:<24} {_cell(cells['today'])} "
                         f"{_cell(cells['gates'])}"
                         + (f" {_cell(cells['pipeline'])}"
                            if report["adjudicated"] else ""))
    lines.append("(* proposable before this replay; precision = the share "
                 "the author kept or retyped)")
    lines.append("")
    lines.append("§2.5, the bar:")
    for name, (value, bar) in report["section_2_5"].items():
        if isinstance(value, float):
            ok = value >= bar
            shown = f"{value:.0%}"
            need = f"≥ {bar:.0%}"
        elif value is None:
            lines.append(f"  · {name}: not measured — no labelled candidate "
                         f"reached it (required ≥ {bar:.0%})")
            continue
        else:
            ok = value <= bar
            shown, need = str(value), (f"≤ {bar}" if bar else "0")
        lines.append(f"  {'✓' if ok else '✗'} {name}: {shown} (required "
                     f"{need})")
    lines.append("  · note_update proposals adopted: not measurable by "
                 "replay (≥ 25% required)")
    lines.append("")
    if report["adjudicated"]:
        lines.append("thresholds: " + ("; ".join(report["moved"])
                                       or "nothing moved"))
        t = report["thresholds"]
        lines.append(f"  proposable: {', '.join(t['proposable_relations'])}")
        lines.append(f"  confidence floor {t['confidence_floor']:.2f}; "
                     f"ceilings {t['max_concepts_per_essay']} concept(s), "
                     f"{t['max_links_per_essay']} relation(s) per essay")
    else:
        lines.append("gates only: nothing recorded, no threshold moved")
    return "\n".join(lines)


def describe_current(db: Database, manuscript: dict, knob: dict) -> str:
    """`extract calibrate` without --replay: the thresholds in force and
    where they came from."""
    from .extraction import runs

    latest = next((r for r in reversed(runs(db, manuscript))
                   if r.get("kind") == "calibrate"), None)
    head = (f"thresholds from calibration {latest['id']} "
            f"({latest['started'][:10]}, {latest.get('model')})"
            if latest and knob.get("calibrated_by") else
            "thresholds from config.toml (no calibration recorded)")
    lines = [head,
             f"  proposable: {', '.join(sorted(knob['proposable_relations']))}",
             f"  confidence floor {float(knob['confidence_floor']):.2f}; "
             f"ceilings {knob['max_concepts_per_essay']} concept(s), "
             f"{knob['max_links_per_essay']} relation(s) per essay"]
    if latest:
        lines.append("  last measured: " + json.dumps(latest.get("precision")))
    lines.append("Run 'extract calibrate --replay' to measure again.")
    return "\n".join(lines)
