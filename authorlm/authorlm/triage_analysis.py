"""On-demand, resumable-in-parts LLM analysis for the Triage App.

There is deliberately no worker queue. A UI starts a run and calls one bounded
batch at a time; each completed batch is durable, while unfinished work stops
when the caller stops.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .db import Database, ko_fields, loads, now_iso
from .llm import LLMClient
from . import triage


ONTOLOGY_SYSTEM = """You are building a compact consistency map for a philosophy
manuscript. Read the complete supplied manuscript. Return a JSON object with:
thesis (string), argument_arc (array of short strings), central_concepts (array
of objects with name and role), distinctions (array of short strings), and
tensions (array of short strings). Describe the manuscript; do not evaluate its
literary quality. Be concise enough for reuse in later scoring calls."""


ANALYSIS_SYSTEM = """You are a conservative graph curator. Follow the analyzer
profile exactly. Return JSON shaped as {"results": [...]}. Every result must
contain object_id, score (integer 1-100), confidence (integer 1-100), why (one
compact paragraph), and evidence (an array of {passage_id, reason}). Evidence
may cite only passage IDs supplied for that object. Empty evidence is valid when
the manuscript does not support the item. Do not turn analysis into a decision."""


def _latest_version(db: Database, manuscript_id: str) -> dict:
    row = db.one(
        "SELECT * FROM manuscript_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT 1", (manuscript_id,))
    if not row:
        raise RuntimeError("collect the manuscript before analyzing it")
    return dict(row)


def _paragraphs(version: dict) -> list[dict[str, str]]:
    passages: list[dict[str, str]] = []
    for file, text in sorted(loads(version["files"], {}).items()):
        heading = ""
        occurrences: dict[str, int] = {}
        for block in re.split(r"\n\s*\n", text):
            block = block.strip()
            if not block:
                continue
            lines = block.splitlines()
            if lines[0].startswith("#"):
                heading = lines[0].lstrip("#").strip()
                if len(lines) == 1:
                    continue
                block = "\n".join(lines[1:]).strip()
            if not block:
                continue
            occurrences[block] = occurrences.get(block, 0) + 1
            digest = hashlib.sha256(
                f"{file}\0{block}\0{occurrences[block]}".encode()).hexdigest()[:12]
            passages.append({
                "id": f"p-{digest}",
                "file": file,
                "heading": heading,
                "text": block,
            })
    return passages


def _llm(config: dict[str, Any], profile: dict[str, Any]) -> LLMClient:
    effective = json.loads(json.dumps(config))
    if profile.get("model"):
        effective.setdefault("llm", {})["model"] = profile["model"]
    client = LLMClient(effective)
    client.purpose = "triage"               # the usage ledger's key (AP)
    return client


def _reuse_context(db: Database, manuscript_id: str,
                   manuscript_version_id: str,
                   model: str) -> dict[str, Any] | None:
    rows = db.all(
        "SELECT context FROM triage_runs WHERE manuscript_id = ? "
        "AND manuscript_version_id = ? ORDER BY created_at DESC",
        (manuscript_id, manuscript_version_id),
    )
    for row in rows:
        context = loads(row["context"], {})
        if context.get("ontology") and context.get("model") == model:
            return context
    return None


def _build_context(client: LLMClient, version: dict,
                   passages: list[dict[str, str]]) -> dict[str, Any]:
    manuscript = "\n\n".join(
        f"[{p['id']}] {p['file']}#{p['heading']}\n{p['text']}" for p in passages)
    ontology = client.complete_json(ONTOLOGY_SYSTEM, manuscript, thinking_budget=0)
    if not isinstance(ontology, dict):
        raise RuntimeError("the LLM did not produce a usable manuscript consistency map")
    return {
        "ontology": ontology,
        "passage_count": len(passages),
        "manuscript_checksum": version["checksum"],
        "model": client.model,
    }


def start_run(db: Database, manuscript: dict, config: dict[str, Any],
              triage_type: str, object_ids: list[str],
              profile_id: str | None = None,
              profile_version: str | None = None) -> dict[str, Any]:
    """Collect local files, pin that revision, and create analysis provenance."""
    if not object_ids:
        raise ValueError("there are no rows to analyze")
    from . import api

    api.collect(db, manuscript, config, analyze=False)
    version = _latest_version(db, manuscript["id"])
    rows = {r["id"]: r for r in triage.list_rows(db, manuscript, triage_type)}
    missing = [object_id for object_id in object_ids if object_id not in rows]
    if missing:
        raise LookupError(f"{len(missing)} requested row(s) no longer exist")
    profile = triage.resolve_profile(
        manuscript, triage_type, profile_id, profile_version)
    snapshot, digest = triage.profile_snapshot(profile)
    client = _llm(config, profile)
    if not client.enabled:
        raise RuntimeError("the LLM is disabled in AuthorLM configuration")
    passages = _paragraphs(version)
    context = _reuse_context(db, manuscript["id"], version["id"], client.model)
    reused = context is not None
    if context is None:
        context = _build_context(client, version, passages)
    context["objects"] = {object_id: rows[object_id] for object_id in object_ids}
    context["profile_hash"] = digest
    run = ko_fields("tr")
    run.update(
        manuscript_id=manuscript["id"], triage_type=triage_type,
        profile_id=profile["id"], profile_version=str(profile["version"]),
        profile_snapshot=snapshot, manuscript_version_id=version["id"],
        requested_ids=json.dumps(list(dict.fromkeys(object_ids))),
        context=json.dumps(context), status="incomplete", completed_at=None,
    )
    db.insert("triage_runs", run)
    return {
        "run_id": run["id"],
        "requested_ids": loads(run["requested_ids"], []),
        "batch_size": int(profile.get("batch_size", 12)),
        "manuscript_version": version["version_no"],
        "profile": {"id": profile["id"], "version": str(profile["version"]),
                    "label": profile["label"]},
        "context_reused": reused,
        "usage": client.stats_line(),
    }


def _evidence_packet(triage_type: str, row: dict[str, Any],
                     passages: list[dict[str, str]]) -> list[dict[str, str]]:
    if triage_type == "concepts":
        terms = [row["name"], *row.get("aliases", [])]
    else:
        terms = [row["from_name"], row["to_name"]]
    patterns = [re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
                for term in terms if term]
    ranked = []
    for passage in passages:
        hits = sum(1 for pattern in patterns if pattern.search(passage["text"]))
        if hits:
            ranked.append((hits, passage))
    ranked.sort(key=lambda item: (-item[0], item[1]["file"], item[1]["id"]))
    limit = 6 if triage_type == "concepts" else 8
    return [passage for _, passage in ranked[:limit]]


def _object_payload(triage_type: str, row: dict[str, Any],
                    passages: list[dict[str, str]]) -> dict[str, Any]:
    evidence = _evidence_packet(triage_type, row, passages)
    if triage_type == "concepts":
        item = {k: row.get(k) for k in
                ("id", "name", "kind", "status", "introduced_in", "notes", "aliases")}
    else:
        item = {k: row.get(k) for k in
                ("id", "from_name", "relation", "to_name", "status", "support")}
    item["object_id"] = item.pop("id")
    item["passages"] = evidence
    return item


def _validate_results(profile: dict[str, Any], requested: list[str], raw: Any,
                      passage_lookup: dict[str, dict[str, dict[str, str]]]
                      ) -> list[dict[str, Any]]:
    if not isinstance(raw, dict) or not isinstance(raw.get("results"), list):
        raise RuntimeError("the analyzer returned an invalid result envelope")
    by_id = {item.get("object_id"): item for item in raw["results"]
             if isinstance(item, dict)}
    if set(by_id) != set(requested):
        raise RuntimeError("the analyzer did not return exactly one result per row")
    allowed = {field["id"] for field in profile["output_fields"]} | {"object_id"}
    results = []
    for object_id in requested:
        item = by_id[object_id]
        for key in ("score", "confidence"):
            if not isinstance(item.get(key), int) or not 1 <= item[key] <= 100:
                raise RuntimeError(f"the analyzer returned an invalid {key} for {object_id}")
        if not isinstance(item.get("why"), str):
            raise RuntimeError(f"the analyzer returned no reasoning for {object_id}")
        evidence = item.get("evidence", [])
        if not isinstance(evidence, list):
            raise RuntimeError(f"the analyzer returned invalid evidence for {object_id}")
        resolved = []
        object_passages = passage_lookup[object_id]
        for citation in evidence:
            if not isinstance(citation, dict) or citation.get("passage_id") not in object_passages:
                raise RuntimeError(f"the analyzer cited evidence outside the packet for {object_id}")
            passage = object_passages[citation["passage_id"]]
            resolved.append({
                "passage_id": passage["id"],
                "file": passage["file"],
                "heading": passage["heading"],
                "excerpt": passage["text"],
                "reason": str(citation.get("reason", "")),
            })
        clean = {key: value for key, value in item.items() if key in allowed}
        clean["evidence"] = resolved
        results.append(clean)
    return results


def analyze_batch(db: Database, manuscript: dict, config: dict[str, Any],
                  run_id: str, object_ids: list[str]) -> dict[str, Any]:
    run_row = db.one(
        "SELECT * FROM triage_runs WHERE id = ? AND manuscript_id = ?",
        (run_id, manuscript["id"]),
    )
    if not run_row:
        raise LookupError(f"no triage analysis run '{run_id}'")
    run = dict(run_row)
    if run["status"] == "complete":
        return run_status(db, manuscript, run_id)
    requested = loads(run["requested_ids"], [])
    batch = list(dict.fromkeys(object_ids))
    if not batch or any(object_id not in requested for object_id in batch):
        raise ValueError("the batch must contain requested rows from this run")
    already = {r["object_id"] for r in db.all(
        "SELECT object_id FROM triage_assessments WHERE run_id = ?", (run_id,))}
    batch = [object_id for object_id in batch if object_id not in already]
    if not batch:
        return run_status(db, manuscript, run_id)
    profile = loads(run["profile_snapshot"], {})
    max_batch = int(profile.get("batch_size", 12))
    if len(batch) > max_batch:
        raise ValueError(f"analyzer batches are limited to {max_batch} rows")
    version_row = db.one("SELECT * FROM manuscript_versions WHERE id = ?",
                         (run["manuscript_version_id"],))
    if not version_row:
        raise RuntimeError("the pinned manuscript version is missing")
    version = dict(version_row)
    passages = _paragraphs(version)
    context = loads(run["context"], {})
    rows = context.get("objects", {})
    if any(object_id not in rows for object_id in batch):
        raise RuntimeError("the run is missing its pinned graph-row snapshot")
    payloads = [_object_payload(run["triage_type"], rows[object_id], passages)
                for object_id in batch]
    allowed_passages = {
        payload["object_id"]: {passage["id"]: passage for passage in payload["passages"]}
        for payload in payloads
    }
    prompt = {
        "profile_instruction": profile["prompt"],
        "consistency_map": context.get("ontology", {}),
        "objects": payloads,
        "requested_output_fields": profile["output_fields"],
    }
    client = _llm(config, profile)
    if not client.enabled:
        raise RuntimeError("the LLM is disabled in AuthorLM configuration")
    raw = client.complete_json(ANALYSIS_SYSTEM, json.dumps(prompt), thinking_budget=0)
    results = _validate_results(profile, batch, raw, allowed_passages)
    with db.transaction():
        for result in results:
            object_id = result["object_id"]
            assessment = ko_fields("ta")
            assessment.update(
                manuscript_id=manuscript["id"], run_id=run_id,
                triage_type=run["triage_type"], object_id=object_id,
                object_version=rows[object_id]["version"], output=json.dumps(result),
            )
            db.insert("triage_assessments", assessment)
        done = db.one("SELECT COUNT(*) AS n FROM triage_assessments WHERE run_id = ?",
                      (run_id,))["n"]
        if done >= len(requested):
            db.update("triage_runs", run_id,
                      {"status": "complete", "completed_at": now_iso()})
    status = run_status(db, manuscript, run_id)
    status.update(results=results, usage=client.stats_line())
    return status


def run_status(db: Database, manuscript: dict, run_id: str) -> dict[str, Any]:
    row = db.one("SELECT * FROM triage_runs WHERE id = ? AND manuscript_id = ?",
                 (run_id, manuscript["id"]))
    if not row:
        raise LookupError(f"no triage analysis run '{run_id}'")
    run = dict(row)
    requested = loads(run["requested_ids"], [])
    complete = {r["object_id"] for r in db.all(
        "SELECT object_id FROM triage_assessments WHERE run_id = ?", (run_id,))}
    return {
        "run_id": run_id,
        "status": run["status"],
        "completed": len(complete),
        "total": len(requested),
        "remaining_ids": [object_id for object_id in requested if object_id not in complete],
    }
