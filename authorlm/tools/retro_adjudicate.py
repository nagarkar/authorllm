"""Retro-adjudication: point the existing adjudicator at the EXISTING triage
backlog (not at a fresh extraction) and measure what it would remove.

DRY RUN. Never writes to the database — not even the sandbox copy. Only
`db.all`/`db.one` (read queries) and adjudication.py's own read-only helpers
are used; `adjudication.adjudicate()` itself is never called, because it
inserts an evidence row per pass (`_record`), and `proposals`/`triage`
mutators are never imported.

`adjudicate()` is written for a freshly-extracted `result` dict (concepts +
links straight from one extraction pass over one file's changed text). The
backlog is a different shape entirely — existing concept_nodes rows,
existing concept_edges rows, and knowledge_proposals rows of 7 different
kinds — so this script re-derives candidate dicts of the SAME shape
`_render_candidates` expects, and re-implements just the "which file is this
against, and what verdict came back" bookkeeping around it. Everything that
makes the verdicts good — the deterministic neighbour/precedent retrieval,
the located-quote extraction, the prompt itself — is imported unchanged from
`authorlm.adjudication`.

  python3 tools/retro_adjudicate.py --workspace <scratch>/retro [--manuscript SMSTTD]
                                     [--dry-count] [--out results.json]

--dry-count builds the per-file candidate grouping and prints it WITHOUT
spending a single LLM call — use it first to sanity-check the batching
before paying for the real run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import adjudication  # noqa: E402
from authorlm import paths  # noqa: E402
from authorlm.concepts import concept_pattern  # noqa: E402
from authorlm.db import Database, loads  # noqa: E402
from authorlm.llm import LLMClient  # noqa: E402

PROPOSAL_KINDS = ("note_update", "revival", "vanished", "alias",
                  "variant_of_retired", "edge_reproposal", "belief_revival")

# Proposal kinds fed through the SAME candidate-concept judgment
# (new/improves/subsumed/drop) the live adjudicator applies to fresh
# extraction: each one is, at bottom, "is this name+definition worth a
# question to the author, or does it collapse into something settled /
# already-answered". note_update literally re-poses the question that
# created it in the first place (an "improves" verdict IS a note_update
# proposal); revival and variant_of_retired reuse the precedent-rejection
# machinery the adjudicator already has (the retired name shows up as an
# "author previously REJECTED" precedent with similarity 1.0); alias is a
# subsumption question by construction.
CONCEPT_SHAPED_KINDS = {"note_update", "revival", "alias", "variant_of_retired"}
# edge_reproposal is a rejected edge argued again — exactly the
# new/subsumed/drop question the adjudicator already asks of a candidate
# relationship.
LINK_SHAPED_KINDS = {"edge_reproposal"}
# vanished asks the OPPOSITE question ("should settled knowledge be
# retired because its text disappeared") and belief_revival is not a
# concept/edge at all — neither fits this adjudicator's verdict vocabulary,
# so both are carried through unadjudicated (see report: "design mismatch").
SKIPPED_KINDS = {"vanished", "belief_revival"}

_KIND_SUFFIX = re.compile(
    r"\s*\((?:concept|objection|example|metaphor|question|"
    r"historical_reference|mathematical_construct|syllogism)\)\s*$",
    re.IGNORECASE)

REASON_ADDENDUM = (
    "\n\nAdditionally, for a retrospective measurement report only — this "
    "does not change any verdict rule above — include a \"reason\" key in "
    "EVERY concept and link verdict object: one short clause naming the "
    "specific textual or precedent basis for the verdict (e.g. quote a "
    "phrase from the located text, or name the precedent/existing concept "
    "that decided it)."
)


def load_sandbox_llm(sandbox_config_path: Path) -> LLMClient:
    """The sandbox config.toml (adjudicate=true, the model) plus the repo's
    own .env (real GEMINI_API_KEY) — load_config() would read the wrong
    config.toml (it always resolves beside the package, not the workspace),
    so this reproduces just the two steps that matter instead."""
    import tomllib

    paths.load_env()  # repo .env -> os.environ (GEMINI_API_KEY)
    config = tomllib.loads(sandbox_config_path.read_text())
    return LLMClient(config)


def latest_files(db: Database, mid: str) -> dict[str, str]:
    row = db.one(
        "SELECT files FROM manuscript_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT 1", (mid,))
    return json.loads(row["files"]) if row else {}


def essay_files(files: dict[str, str]) -> list[str]:
    return sorted(n for n in files if n.endswith(".md") and n not in
                 ("title.md",))


def locate(name: str, files: dict[str, str], order: list[str],
          preferred: str | None) -> str | None:
    """Home file for a candidate: its recorded location if that file still
    exists, else the first file (reading order) whose text mentions it
    verbatim, else None (genuinely unlocatable — excluded, not guessed)."""
    if preferred and preferred in files:
        return preferred
    pattern = concept_pattern(name) if name.strip() else None
    if pattern:
        for fname in order:
            if pattern.search(files[fname]):
                return fname
    return None


# ------------------------------------------------------- backlog collection


def fetch_backlog(db: Database, mid: str) -> dict:
    concepts = [dict(r) for r in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND status != 'retired' "
        "ORDER BY created_at", (mid,))
        if (lambda m: m.get("origin") == "extracted" and not m.get("confirmed"))
        (loads(r["metadata"], {}))]
    edges = [dict(r) for r in db.all(
        "SELECT ce.*, fn.name AS from_name, tn.name AS to_name, "
        "fn.introduced_in AS from_introduced_in, tn.introduced_in AS to_introduced_in "
        "FROM concept_edges ce "
        "JOIN concept_nodes fn ON fn.id = ce.from_node "
        "JOIN concept_nodes tn ON tn.id = ce.to_node "
        "WHERE ce.manuscript_id = ? AND ce.status = 'inferred' ORDER BY ce.created_at",
        (mid,))]
    proposals = [dict(r) for r in db.all(
        "SELECT * FROM knowledge_proposals WHERE manuscript_id = ? AND state = 'open' "
        "ORDER BY created_at", (mid,))]
    by_kind: dict[str, list] = {k: [] for k in PROPOSAL_KINDS}
    for p in proposals:
        by_kind.setdefault(p["kind"], []).append(p)
    return {"concepts": concepts, "edges": edges, "proposals_by_kind": by_kind}


def target_node(db: Database, node_id: str) -> dict | None:
    row = db.one("SELECT * FROM concept_nodes WHERE id = ?", (node_id,))
    return dict(row) if row else None


# --------------------------------------------------------- candidate build


class Item:
    """One backlog row, wrapped uniformly for grouping/reporting regardless
    of which of the 9 source tables/kinds it came from."""

    def __init__(self, source: str, row: dict, cand_kind: str,
                key: tuple, render: dict):
        self.source = source          # 'concept' | 'edge' | proposal kind
        self.row = row                # the raw backlog row
        self.cand_kind = cand_kind    # 'concept' | 'link'
        self.key = key                 # match key back to the LLM verdict
        self.render = render           # dict fed to _render_candidates
        self.home: str | None = None
        self.verdict: dict | None = None   # the judged verdict, or None
        self.judged = False


def build_items(db: Database, mid: str, backlog: dict, files: dict,
                order: list[str]) -> tuple[list[Item], list[Item]]:
    """(items, skipped) — skipped are vanished/belief_revival (design
    mismatch, never sent to the LLM) plus anything genuinely unlocatable."""
    items: list[Item] = []
    skipped: list[Item] = []

    for c in backlog["concepts"]:
        name = c["name"]
        it = Item("concept", c, "concept", ("concept", name.lower()),
                  {"name": name, "kind": c["kind"], "notes": c["notes"] or ""})
        it.home = locate(name, files, order, c.get("introduced_in"))
        (items if it.home else skipped).append(it)

    for e in backlog["edges"]:
        rendered = f"{e['from_name']} —{e['relation']}→ {e['to_name']}"
        it = Item("edge", e, "link",
                  ("link", e["from_name"].lower(), e["relation"].lower(),
                   e["to_name"].lower()),
                  {"from": e["from_name"], "relation": e["relation"],
                   "to": e["to_name"]})
        home = None
        for fname in order:
            if (concept_pattern(e["from_name"]).search(files[fname])
                    and concept_pattern(e["to_name"]).search(files[fname])):
                home = fname
                break
        if home is None:
            home = (locate(e["from_name"], files, order, e.get("from_introduced_in"))
                    or locate(e["to_name"], files, order, e.get("to_introduced_in")))
        it.home = home
        it.render["_display"] = rendered
        (items if it.home else skipped).append(it)

    for p in backlog["proposals_by_kind"].get("note_update", []):
        pl = loads(p["payload"], {})
        target = target_node(db, p["target"])
        name = pl.get("name", "")
        it = Item("note_update", p, "concept", ("concept", name.lower()),
                  {"name": name, "kind": pl.get("proposed_kind") or pl.get("current_kind") or "concept",
                   "notes": pl.get("proposed_note") or ""})
        it.home = locate(name, files, order,
                         target.get("introduced_in") if target else None)
        (items if it.home else skipped).append(it)

    for p in backlog["proposals_by_kind"].get("revival", []):
        pl = loads(p["payload"], {})
        target = target_node(db, p["target"])
        name = pl.get("name", "")
        it = Item("revival", p, "concept", ("concept", name.lower()),
                  {"name": name, "kind": pl.get("kind") or "concept",
                   "notes": pl.get("notes") or ""})
        it.home = locate(name, files, order,
                         target.get("introduced_in") if target else None)
        (items if it.home else skipped).append(it)

    for p in backlog["proposals_by_kind"].get("variant_of_retired", []):
        pl = loads(p["payload"], {})
        target = target_node(db, p["target"])
        name = pl.get("proposed_name", "")
        it = Item("variant_of_retired", p, "concept", ("concept", name.lower()),
                  {"name": name, "kind": pl.get("kind") or "concept",
                   "notes": pl.get("notes") or ""})
        it.home = locate(name, files, order,
                         target.get("introduced_in") if target else None)
        (items if it.home else skipped).append(it)

    for p in backlog["proposals_by_kind"].get("alias", []):
        pl = loads(p["payload"], {})
        a_node = target_node(db, p["target"])
        name = pl.get("alias", "")
        notes = f"{pl.get('sentence', '')} — the text identifies this with '{pl.get('canonical', '')}'"
        it = Item("alias", p, "concept", ("concept", name.lower()),
                  {"name": name, "kind": (a_node or {}).get("kind") or "concept",
                   "notes": notes})
        it.home = locate(name, files, order,
                         pl.get("location") or (a_node.get("introduced_in") if a_node else None))
        (items if it.home else skipped).append(it)

    for p in backlog["proposals_by_kind"].get("edge_reproposal", []):
        pl = loads(p["payload"], {})
        src, rel, dst = pl.get("from_name", ""), pl.get("relation", ""), pl.get("to_name", "")
        it = Item("edge_reproposal", p, "link",
                  ("link", src.lower(), rel.lower(), dst.lower()),
                  {"from": src, "relation": rel, "to": dst})
        home = None
        for fname in order:
            if (concept_pattern(src).search(files[fname])
                    and concept_pattern(dst).search(files[fname])):
                home = fname
                break
        it.home = home or locate(src, files, order, None) or locate(dst, files, order, None)
        (items if it.home else skipped).append(it)

    for kind in SKIPPED_KINDS:
        for p in backlog["proposals_by_kind"].get(kind, []):
            it = Item(kind, p, "n/a", (), {})
            skipped.append(it)

    return items, skipped


# ------------------------------------------------------------- LLM adjudication


def run_file(db: Database, mid: str, llm: LLMClient, fname: str,
            file_items: list[Item], files: dict) -> dict:
    concepts_by_key: dict[tuple, dict] = {}
    links_by_key: dict[tuple, dict] = {}
    for it in file_items:
        if it.cand_kind == "concept":
            concepts_by_key.setdefault(it.key, it.render)
        else:
            links_by_key.setdefault(it.key, it.render)
    concepts = list(concepts_by_key.values())
    links = list(links_by_key.values())

    live, rejected = adjudication._pool(db, mid)
    # HARNESS FIX: adjudication._existing_edges selects status != 'retired',
    # which INCLUDES the inferred edges we are feeding as candidates — so the
    # model saw each candidate listed as an existing edge and correctly ruled
    # it "already exists" (142 self-referential subsumptions in the first run).
    # Correct in live extraction, where candidates are not yet persisted; wrong
    # when retro-scoring rows that are already in the table. Show only edges
    # the author has actually settled.
    edges = ([
        f"{r['a']} —{r['relation']}→ {r['b']}"
        for r in db.all(
            "SELECT f.name AS a, e.relation AS relation, t.name AS b "
            "FROM concept_edges e "
            "JOIN concept_nodes f ON f.id = e.from_node "
            "JOIN concept_nodes t ON t.id = e.to_node "
            "WHERE e.manuscript_id = ? AND e.status NOT IN ('retired', 'inferred')",
            (mid,),
        )
    ] if links else [])
    sentences = adjudication._sentences(
        adjudication._strip_known_concepts(files[fname]))
    rendered = adjudication._render_candidates(
        concepts, links, live, rejected, edges, sentences)
    system = adjudication.adjudication_system() + REASON_ADDENDUM
    reply = llm.complete_json(system, rendered, thinking_budget=0)
    if not isinstance(reply, dict):
        return {"concept_verdicts": {}, "link_verdicts": {}, "ok": False}
    concept_verdicts, link_verdicts = adjudication._verdict_maps(reply)
    # The prompt asks the model to echo the candidate's "name" back exactly
    # — but on some replies it echoes the FULL "- candidate: NAME (kind)"
    # line instead of just NAME, which silently orphans that verdict from
    # `it.key` (a bare lowered name) and makes the candidate look unjudged.
    # Alias the verdict under the suffix-stripped key too so either form
    # matches; this only widens matching, never changes a verdict.
    for raw_key in list(concept_verdicts):
        stripped = _KIND_SUFFIX.sub("", raw_key).strip()
        if stripped and stripped != raw_key and stripped not in concept_verdicts:
            concept_verdicts[stripped] = concept_verdicts[raw_key]
    # _verdict_maps drops the "reason" key (it only knows the production
    # schema) — recover it straight from the raw reply.
    for entry in reply.get("concepts") or []:
        if isinstance(entry, dict) and "reason" in entry:
            key = str(entry.get("name", "")).strip().lower()
            if key in concept_verdicts:
                concept_verdicts[key]["reason"] = entry["reason"]
    for entry in reply.get("links") or []:
        if isinstance(entry, dict) and "reason" in entry:
            key = (str(entry.get("from", "")).strip().lower(),
                   str(entry.get("relation", "")).strip().lower(),
                   str(entry.get("to", "")).strip().lower())
            if key in link_verdicts:
                link_verdicts[key]["reason"] = entry["reason"]
    return {"concept_verdicts": concept_verdicts, "link_verdicts": link_verdicts,
            "ok": True}


def apply_verdicts(file_items: list[Item], result: dict) -> None:
    for it in file_items:
        if it.cand_kind == "concept":
            key = it.key[1]
            judged = result["concept_verdicts"].get(key)
        else:
            key = it.key[1:]
            judged = result["link_verdicts"].get(key)
        it.judged = result["ok"]
        it.verdict = judged


# ----------------------------------------------------- confirmed-evidence check


CONCEPT_NAME_RE = re.compile(r"^(.*?)(?:\s*\(|\s*:|$)")


def confirmed_concept_names(db: Database, mid: str) -> set[str]:
    names = set()
    for r in db.all(
        "SELECT target FROM evidence WHERE manuscript_id = ? "
        "AND evidence_type = 'concept_triage' AND signal IN "
        "('confirmed', 'retyped', 'merged')", (mid,)):
        target = r["target"] or ""
        m = CONCEPT_NAME_RE.match(target)
        if m:
            names.add(m.group(1).strip().lower())
    return names


EDGE_TARGET_RE = re.compile(r"^(?:.*⇒\s*)?(.+?—.+?→.+?)(?:\s+—\s*reason:.*)?$")


def confirmed_edge_triples(db: Database, mid: str) -> set[str]:
    triples = set()
    for r in db.all(
        "SELECT target FROM evidence WHERE manuscript_id = ? "
        "AND evidence_type = 'edge_triage' AND signal IN "
        "('confirmed', 'retyped')", (mid,)):
        target = r["target"] or ""
        m = EDGE_TARGET_RE.match(target)
        rendered = (m.group(1) if m else target).strip()
        triples.add(" ".join(rendered.split()).lower())
    return triples


# ------------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--manuscript", default="SMSTTD")
    ap.add_argument("--dry-count", action="store_true",
                    help="build the grouping and print it; zero LLM calls")
    ap.add_argument("--out", default=None,
                    help="write full results as JSON to this path")
    ap.add_argument("--only-file", default=None,
                    help="adjudicate a single file only (smoke-testing)")
    ap.add_argument("--resume-unjudged", default=None,
                    help="path to a prior --out JSON; re-adjudicate only "
                         "the items it left unjudged (smaller per-file "
                         "batches so the model judges every candidate), "
                         "merge, and write back to --out")
    args = ap.parse_args()

    ws = Path(args.workspace)
    db_path = ws / ".authorlm" / "authorlm.db"
    config_path = ws / ".authorlm" / "config.toml"
    if not db_path.exists():
        sys.exit(f"no such database: {db_path}")

    db = Database(db_path)
    ms = db.one("SELECT * FROM manuscripts WHERE name = ?", (args.manuscript,))
    if not ms:
        sys.exit(f"no manuscript named '{args.manuscript}'")
    mid = ms["id"]

    files = latest_files(db, mid)
    order = essay_files(files)
    print(f"manuscript: {args.manuscript} ({mid}) — {len(order)} essay files")

    backlog = fetch_backlog(db, mid)
    n_concepts = len(backlog["concepts"])
    n_edges = len(backlog["edges"])
    n_props = sum(len(v) for v in backlog["proposals_by_kind"].values())
    print(f"backlog: {n_concepts} unconfirmed concepts, {n_edges} inferred "
          f"edges, {n_props} open proposals "
          f"({', '.join(f'{k} {len(v)}' for k, v in backlog['proposals_by_kind'].items() if v)})")

    items, skipped_items = build_items(db, mid, backlog, files, order)
    by_file: dict[str, list[Item]] = {}
    for it in items:
        by_file.setdefault(it.home, []).append(it)

    print(f"\n{len(items)} items resolved to a home file across "
          f"{len(by_file)} files; {len(skipped_items)} skipped "
          f"(design-mismatch kinds or unlocatable)")
    for fname in sorted(by_file):
        c = sum(1 for it in by_file[fname] if it.cand_kind == "concept")
        l = sum(1 for it in by_file[fname] if it.cand_kind == "link")
        print(f"  {fname:<20} {c:>4} concept-shaped  {l:>4} link-shaped")

    if args.dry_count:
        return

    if args.resume_unjudged:
        prior = json.loads(Path(args.resume_unjudged).read_text())
        needs_resume = {
            (e["source"], e["row_id"]) for e in prior["items"]
            if not e.get("verdict") or e["verdict"] == "unjudged"
        }
        resume_by_file: dict[str, list[Item]] = {}
        for it in items:
            if (it.source, it.row["id"]) in needs_resume:
                resume_by_file.setdefault(it.home, []).append(it)
        print(f"\nresuming {len(needs_resume)} previously-unjudged item(s) "
              f"across {len(resume_by_file)} file(s)")

        llm = load_sandbox_llm(config_path)
        for fname in sorted(resume_by_file):
            file_items = resume_by_file[fname]
            print(f"  re-adjudicating {fname} ({len(file_items)} item(s), "
                  f"smaller batch)...", file=sys.stderr)
            result = run_file(db, mid, llm, fname, file_items, files)
            apply_verdicts(file_items, result)

        confirmed_names = confirmed_concept_names(db, mid)
        confirmed_edges = confirmed_edge_triples(db, mid)
        by_key = {(it.source, it.row["id"]): it
                 for file_items in resume_by_file.values() for it in file_items}
        still_unjudged = 0
        for entry in prior["items"]:
            key = (entry["source"], entry["row_id"])
            it = by_key.get(key)
            if it is None:
                continue
            v = it.verdict or {}
            raw = str(v.get("verdict", "")).strip().lower()
            new_verdict = raw if (it.judged and raw) else "unjudged"
            if new_verdict == "unjudged":
                still_unjudged += 1
                continue
            entry["verdict"] = new_verdict
            entry["existing"] = v.get("existing")
            entry["reason"] = v.get("reason")
            entry["notes"] = v.get("notes")
        prior["cost"] = {
            "calls": prior["cost"]["calls"] + llm.live_calls,
            "input_tokens": prior["cost"]["input_tokens"] + llm.input_tokens,
            "output_tokens": prior["cost"]["output_tokens"] + llm.output_tokens,
        }
        out_path = args.out or args.resume_unjudged
        Path(out_path).write_text(json.dumps(prior, indent=2))
        print(f"\nresume pass: {llm.live_calls} call(s), {llm.input_tokens:,} "
              f"in / {llm.output_tokens:,} out tokens; "
              f"{len(needs_resume) - still_unjudged} newly judged, "
              f"{still_unjudged} still unjudged")
        print(f"wrote {out_path}")
        return

    if args.only_file:
        by_file = {args.only_file: by_file[args.only_file]}

    llm = load_sandbox_llm(config_path)
    print(f"\nLLM: enabled={llm.enabled} model={llm.model}")

    for fname in sorted(by_file):
        file_items = by_file[fname]
        print(f"  adjudicating {fname} ({len(file_items)} items)...",
              file=sys.stderr)
        result = run_file(db, mid, llm, fname, file_items, files)
        apply_verdicts(file_items, result)

    confirmed_names = confirmed_concept_names(db, mid)
    confirmed_edges = confirmed_edge_triples(db, mid)

    out = {
        "manuscript": args.manuscript,
        "cost": {"calls": llm.live_calls, "input_tokens": llm.input_tokens,
                 "output_tokens": llm.output_tokens},
        "items": [],
        "skipped": [{"source": it.source, "row_id": it.row["id"]}
                    for it in skipped_items],
    }
    for it in items:
        v = it.verdict or {}
        raw_verdict = str(v.get("verdict", "")).strip().lower()
        # The prompt's own rule: "a candidate you do not judge is treated
        # as dropped" — but it is worth tracking apart from an explicit
        # "drop" verdict, exactly as production `adjudicate()` does with
        # its `unjudged` stat.
        verdict = raw_verdict if (it.judged and raw_verdict) else "unjudged"
        rendered_label = (it.render.get("_display") or
                          f"{it.render.get('name', '')} — "
                          f"{it.render.get('from', '')} {it.render.get('relation', '')} "
                          f"{it.render.get('to', '')}").strip(" —")
        entry = {
            "source": it.source, "row_id": it.row["id"], "home": it.home,
            "label": it.render.get("name") or
                    f"{it.render.get('from')} —{it.render.get('relation')}→ {it.render.get('to')}",
            "verdict": verdict, "existing": v.get("existing"),
            "reason": v.get("reason"), "notes": v.get("notes"),
        }
        name_key = it.key[1] if it.key else ""
        if it.cand_kind == "concept":
            entry["confirmed_before"] = name_key in confirmed_names
        else:
            triple = " ".join(
                f"{it.render.get('from')} —{it.render.get('relation')}→ {it.render.get('to')}"
                .split()).lower()
            entry["confirmed_before"] = triple in confirmed_edges
        out["items"].append(entry)

    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2))
        print(f"\nwrote {args.out}")
    else:
        print(json.dumps(out, indent=2))

    print(f"\nLLM cost: {llm.live_calls} call(s), {llm.input_tokens:,} in / "
          f"{llm.output_tokens:,} out tokens")


if __name__ == "__main__":
    main()
