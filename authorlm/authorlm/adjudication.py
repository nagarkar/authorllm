"""Step two of a two-step extraction: adjudicate the candidates (opt-in).

The deterministic extractor admits every candidate the model names, so
every candidate becomes a question only the author can close. On this
author's live record that produced 874 concept-triage and 166 edge-triage
decisions, of which 575 concept verdicts were 'rejected' — two of every
three. The queue, not the graph, is where the cost lands.

This module inserts one adjudication pass between the extractor and the
graph:

  1. DETERMINISTIC RETRIEVAL (free, no model call). For each candidate,
     the nearest existing concepts — and the nearest names the author has
     already rejected — using `loop.similarity`, the same Jaccard overlap
     the proposal firewall is measured on. Retrieval is recall-oriented
     and deliberately *not* a filter: on the live corpus a similarity cut
     alone removes only 21 of 914 extracted concepts, because the author's
     rejections are semantic ("not load-bearing"), not lexical.
  2. ONE MODEL CALL per extraction payload, given the candidates, their
     neighbours and the rejection precedents, returning a verdict each:
     new | improves | subsumed | drop.

Verdicts land as follows, and only 'new' costs the author anything:

  new       → the candidate proceeds into the ordinary extraction path and
              all the existing gates (recurrence bar, retired-name ban,
              groundedness) still apply on top.
  improves  → a `note_update` proposal against the existing concept — the
              existing proposal kind, through the existing near-duplicate
              firewall. Settled knowledge stays machine-unwritable.
  subsumed  → nothing. Logged as evidence, never queued.
  drop      → nothing. Logged as evidence, never queued.

Opt-in and off by default: `[extraction] adjudicate = true` in config.toml.
With the flag off this module is never called and extraction behaves
exactly as before.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import proposals
from .concepts import concept_pattern
from .db import Database, ko_fields, loads
from .loop import similarity

ADJUDICATION_PROMPT_PATH = Path(__file__).parent / "prompts" / "adjudication.md"

# How many neighbours and precedents accompany each candidate. Small on
# purpose: the payload is candidates + context, not manuscript text, so
# the adjudication call stays a fraction of the extraction call it follows.
MAX_NEIGHBOURS = 3
MAX_PRECEDENTS = 3
# Retrieval floor, not a decision threshold. `loop.NEAR_DUPLICATE` (0.72)
# is calibrated for *suppression* — for retrieval it is far too strict:
# 'seeds of agency' vs 'agency' scores 0.5 and is exactly the pair the
# adjudicator needs to see. Judgment is the model's; recall is ours.
RETRIEVAL_FLOOR = 0.30
CONCEPT_VERDICTS = {"new", "improves", "subsumed", "drop"}
EDGE_VERDICTS = {"new", "subsumed", "drop"}
UNLOCATABLE = "UNLOCATABLE — does not appear verbatim in the source text"


def adjudication_system() -> str:
    return ADJUDICATION_PROMPT_PATH.read_text(encoding="utf-8")


# --------------------------------------------------- located quotes (TrackA/1)
#
# Pass 2 used to re-send the whole pass-1 payload verbatim so the adjudicator
# had context to judge each candidate — on measurement, 81% of that payload
# was the source text repeated byte-for-byte, and on multi-file passes it was
# the majority of what pushed calls past extraction_max_chars. What the
# adjudicator actually needs is not the whole text: it is, per candidate, the
# sentence it occurs in plus at least one neighbour — enough to tell "the
# text asserts this" from "this is a passing mention" or "these two concepts
# merely co-occur" without re-reading the chapter.

_HEADING_LINE = re.compile(r"^#{1,6}[ \t].+$")
_FILE_LABEL = re.compile(r"^=== .+ ===$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=\S)")


def _strip_known_concepts(text: str) -> str:
    """The KNOWN CONCEPTS inventory line (extraction.py) is an index, not
    source prose — it must never be mistaken for a place a candidate was
    said, so it is dropped before locating quotes."""
    if text.startswith("KNOWN CONCEPTS:"):
        _, _, rest = text.partition("\n\n")
        return rest
    return text


def _sentences(text: str) -> list[tuple[str | None, str]]:
    """(heading, sentence) pairs in reading order. `heading` is the nearest
    preceding markdown heading — kept with each sentence because measurement
    showed the heading is what signals a phrase is a term of art rather than
    a passing mention."""
    heading = None
    out: list[tuple[str | None, str]] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln.strip() for ln in block.strip("\n").split("\n") if ln.strip()]
        if not lines:
            continue
        if _FILE_LABEL.match(lines[0]):
            lines = lines[1:]
        if lines and _HEADING_LINE.match(lines[0]):
            heading = lines[0]
            lines = lines[1:]
        collapsed = " ".join(lines)
        for sentence in _SENTENCE_END.split(collapsed):
            sentence = sentence.strip()
            if sentence:
                out.append((heading, sentence))
    return out


def _quote_for(name: str, sentences: list[tuple[str | None, str]]) -> str | None:
    """The candidate's sentence plus at least one neighbour, labelled with
    its section heading. Neighbours prefer to stay inside the candidate's
    own section (not to bleed context from an unrelated passage) but a
    section of one sentence still gets a neighbour from next door — a
    candidate is never shown alone. None when the name does not occur
    verbatim anywhere in the text."""
    if not name.strip() or not sentences:
        return None
    pattern = concept_pattern(name)
    for i, (heading, sentence) in enumerate(sentences):
        if not pattern.search(sentence):
            continue
        idxs = [i]
        if i > 0 and sentences[i - 1][0] == heading:
            idxs.insert(0, i - 1)
        if i + 1 < len(sentences) and sentences[i + 1][0] == heading:
            idxs.append(i + 1)
        if len(idxs) == 1:
            if i > 0:
                idxs.insert(0, i - 1)
            elif i + 1 < len(sentences):
                idxs.append(i + 1)
        lo, hi = idxs[0], idxs[-1] + 1
        quote = " ".join(s for _h, s in sentences[lo:hi])
        label = f"[{heading}] " if heading else ""
        return f'{label}"{quote}"'
    return None


def _located(name: str, sentences: list[tuple[str | None, str]]) -> str:
    return _quote_for(name, sentences) or UNLOCATABLE


def enabled(llm) -> bool:
    """True when the author has opted in. Reads the client's own config so
    no call signature changes; a stub client without `.config` is off."""
    config = getattr(llm, "config", None) or {}
    return bool(config.get("extraction", {}).get("adjudicate", False))


# ------------------------------------------------------ ① retrieval (free)


def _pool(db: Database, mid: str) -> tuple[list[dict], list[str]]:
    """(live concepts, names the author has rejected). A rejected name is a
    retired node that the machine proposed — the author's 'no' — as opposed
    to a concept the author declared and later retired for other reasons."""
    live, rejected = [], []
    for row in db.all(
        "SELECT name, kind, notes, status, aliases, metadata FROM concept_nodes "
        "WHERE manuscript_id = ?", (mid,),
    ):
        meta = loads(row["metadata"], {})
        if row["status"] == "retired":
            if meta.get("origin") == "extracted":
                rejected.append(row["name"])
            continue
        live.append({"name": row["name"], "kind": row["kind"],
                     "notes": row["notes"] or "",
                     "aliases": loads(row["aliases"], [])})
    return live, rejected


def _rank(candidate: str, options: list[tuple[str, object]],
          limit: int) -> list[object]:
    """The `limit` most similar options above the retrieval floor."""
    scored = [(similarity(candidate, text), value) for text, value in options]
    scored = [(s, v) for s, v in scored if s >= RETRIEVAL_FLOOR]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [v for _s, v in scored[:limit]]


def neighbours(candidate: dict, live: list[dict]) -> list[dict]:
    """Existing concepts nearest to a candidate. Scored on the name and on
    name+definition together, whichever is closer — a candidate can be a
    duplicate by label ('The Good Choice' / 'Good Choice') or by meaning."""
    name = str(candidate.get("name", ""))
    full = f"{name} {candidate.get('notes') or ''}"
    options = []
    for node in live:
        label = " ".join([node["name"], *node["aliases"]])
        score = max(similarity(name, label),
                    similarity(full, f"{label} {node['notes']}"))
        options.append((score, node))
    ranked = [(s, n) for s, n in options if s >= RETRIEVAL_FLOOR]
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [n for _s, n in ranked[:MAX_NEIGHBOURS]]


def _existing_edges(db: Database, mid: str) -> list[str]:
    return [
        f"{r['a']} —{r['relation']}→ {r['b']}"
        for r in db.all(
            "SELECT f.name AS a, e.relation AS relation, t.name AS b "
            "FROM concept_edges e "
            "JOIN concept_nodes f ON f.id = e.from_node "
            "JOIN concept_nodes t ON t.id = e.to_node "
            "WHERE e.manuscript_id = ? AND e.status != 'retired'",
            (mid,),
        )
    ]


# -------------------------------------------------------- ② the model call


def _render_candidates(concepts: list[dict], links: list[dict],
                       live: list[dict], rejected: list[str],
                       edges: list[str],
                       sentences: list[tuple[str | None, str]]) -> str:
    lines: list[str] = []
    if concepts:
        lines.append("CANDIDATE CONCEPTS")
        for item in concepts:
            name = str(item.get("name", ""))
            lines.append(f"\n- candidate: {name} ({item.get('kind', 'concept')})")
            lines.append(f"  definition: {item.get('notes') or '(none given)'}")
            lines.append(f"  located: {_located(name, sentences)}")
            near = neighbours(item, live)
            if near:
                lines.append("  nearest existing concepts:")
                for node in near:
                    lines.append(f"    * {node['name']} ({node['kind']}): "
                                 f"{node['notes'] or '(no definition yet)'}")
            else:
                lines.append("  nearest existing concepts: none close")
            precedents = _rank(name, [(r, r) for r in rejected],
                               MAX_PRECEDENTS)
            if precedents:
                lines.append("  author previously REJECTED, similar: "
                             + "; ".join(str(p) for p in precedents))
    if links:
        lines.append("\nCANDIDATE RELATIONSHIPS")
        for item in links:
            src, dst = str(item.get("from", "")), str(item.get("to", ""))
            rendered = f"{src} —{item.get('relation', '')}→ {dst}"
            lines.append(f"\n- candidate: {rendered}")
            src_q, dst_q = _located(src, sentences), _located(dst, sentences)
            lines.append(f"  located, {src}: {src_q}")
            if dst_q == src_q and src_q != UNLOCATABLE:
                lines.append(f"  located, {dst}: (same passage as {src} above)")
            else:
                lines.append(f"  located, {dst}: {dst_q}")
            near = _rank(rendered, [(e, e) for e in edges], MAX_NEIGHBOURS)
            if near:
                lines.append("  existing edges between these concepts: "
                             + "; ".join(str(n) for n in near))
    return "\n".join(lines)


def _verdict_maps(reply: dict) -> tuple[dict, dict]:
    concepts, links = {}, {}
    for item in reply.get("concepts") or []:
        if not isinstance(item, dict):
            continue
        verdict = str(item.get("verdict", "")).strip().lower()
        if verdict in CONCEPT_VERDICTS:
            concepts[str(item.get("name", "")).strip().lower()] = item
    for item in reply.get("links") or []:
        if not isinstance(item, dict):
            continue
        verdict = str(item.get("verdict", "")).strip().lower()
        if verdict in EDGE_VERDICTS:
            key = (str(item.get("from", "")).strip().lower(),
                   str(item.get("relation", "")).strip().lower(),
                   str(item.get("to", "")).strip().lower())
            links[key] = item
    return concepts, links


# ------------------------------------------------------- ③ apply the verdicts


def _record(db: Database, mid: str, stats: dict, detail: dict) -> None:
    """One evidence row per adjudication pass: what the machine screened out
    on the author's behalf, so the screening is auditable without being
    triage. supports_belief stays NULL — this is a log, not a judgment."""
    row = ko_fields("ev")
    row.update(
        manuscript_id=mid,
        episode_id=None,
        evidence_type="extraction_adjudication",
        signal="screened",
        target=(f"{stats['candidates']} candidate(s): {stats['new']} new, "
                f"{stats['improves']} improve, {stats['subsumed']} subsumed, "
                f"{stats['dropped']} dropped"),
        supports_belief=None,
        weight="low",  # a machine screening decision, never author evidence
        metadata=json.dumps(detail),
    )
    db.insert("evidence", row)


def adjudicate(db: Database, mid: str, llm, result: dict,
               text: str) -> tuple[dict, dict | None]:
    """Screen one extraction result. Returns (result, stats).

    `result` comes back with only the candidates that survived; `stats` is
    None when the adjudicator could not be consulted, in which case the
    result is returned untouched — a failed screening must never silently
    swallow an extraction.
    """
    concepts = [c for c in (result.get("concepts") or [])
                if isinstance(c, dict) and str(c.get("name", "")).strip()]
    links = [l for l in (result.get("links") or []) if isinstance(l, dict)]
    if not concepts and not links:
        return result, None

    live, rejected = _pool(db, mid)
    edges = _existing_edges(db, mid) if links else []
    sentences = _sentences(_strip_known_concepts(text))
    rendered = _render_candidates(concepts, links, live, rejected, edges,
                                  sentences)
    reply = llm.complete_json(adjudication_system(), rendered, thinking_budget=0)
    if not isinstance(reply, dict):
        return result, None

    concept_verdicts, link_verdicts = _verdict_maps(reply)
    by_name = {n["name"].lower(): n for n in live}
    kept_concepts, kept_links = [], []
    stats = {"candidates": len(concepts) + len(links), "new": 0,
             "improves": 0, "subsumed": 0, "dropped": 0, "unjudged": 0}
    detail: dict[str, list] = {"subsumed": [], "dropped": [], "unjudged": [],
                               "improves": []}

    for item in concepts:
        name = str(item["name"]).strip()
        judged = concept_verdicts.get(name.lower())
        if judged is None:
            stats["unjudged"] += 1
            detail["unjudged"].append(name)
            continue
        verdict = str(judged.get("verdict", "")).strip().lower()
        if verdict == "new":
            stats["new"] += 1
            kept_concepts.append(item)
            continue
        existing = str(judged.get("existing", "")).strip()
        node = by_name.get(existing.lower())
        if verdict == "improves" and node is not None:
            improved = str(judged.get("notes") or item.get("notes") or "").strip()[:300]
            row = db.one(
                "SELECT * FROM concept_nodes WHERE manuscript_id = ? "
                "AND lower(name) = lower(?) AND status != 'retired'",
                (mid, node["name"]),
            )
            if improved and row is not None and proposals.create(
                db, mid, "note_update", row["id"],
                {"name": row["name"], "current_note": row["notes"],
                 "proposed_note": improved, "current_kind": row["kind"],
                 "proposed_kind": row["kind"]},
            ):
                stats["improves"] += 1
                detail["improves"].append(f"{name} → {row['name']}")
                continue
            # Nothing to raise (empty note, or the firewall already holds an
            # equivalent question): the candidate is simply subsumed.
            verdict = "subsumed"
        if verdict == "subsumed":
            stats["subsumed"] += 1
            detail["subsumed"].append(
                f"{name} ⊂ {existing}" if existing else name)
        else:
            stats["dropped"] += 1
            detail["dropped"].append(name)

    for item in links:
        key = (str(item.get("from", "")).strip().lower(),
               str(item.get("relation", "")).strip().lower(),
               str(item.get("to", "")).strip().lower())
        rendered_link = f"{item.get('from')} —{item.get('relation')}→ {item.get('to')}"
        judged = link_verdicts.get(key)
        if judged is None:
            stats["unjudged"] += 1
            detail["unjudged"].append(rendered_link)
            continue
        verdict = str(judged.get("verdict", "")).strip().lower()
        if verdict == "new":
            stats["new"] += 1
            kept_links.append(item)
        elif verdict == "subsumed":
            stats["subsumed"] += 1
            detail["subsumed"].append(rendered_link)
        else:
            stats["dropped"] += 1
            detail["dropped"].append(rendered_link)

    stats["screened"] = (stats["subsumed"] + stats["dropped"]
                         + stats["unjudged"])
    _record(db, mid, stats, detail)
    return ({**result, "concepts": kept_concepts, "links": kept_links}, stats)
