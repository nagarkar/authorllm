"""One JSON request contract for the standalone and MCP App transports."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import api, gdocs, triage, triage_analysis, triage_rules
from .db import Database
from .revisions import read_manuscript_files


def _sync_status(db: Database, manuscript: dict) -> dict[str, Any]:
    links = gdocs.doc_status(db, manuscript)
    files = sorted(key for key, value in links.items()
                   if not key.startswith("_") and isinstance(value, dict))
    return {
        "linked": bool(links.get("_master_id")),
        "master_id": links.get("_master_id"),
        "files": files,
        "message": ("Google Docs are linked. Reconcile before analysis if the "
                    "Doc may contain newer edits." if links.get("_master_id")
                    else "No Google Docs master is linked."),
    }


def _reconcile(db: Database, manuscript: dict, workspace: str | None) -> dict[str, Any]:
    config = api.load_config(workspace)
    if not _sync_status(db, manuscript)["linked"]:
        return {"in_sync": [], "pulled": [], "pushed": [],
                "pending_push": [], "conflicts": [], "errors": []}
    try:
        service = gdocs.get_service(config, workspace, interactive=False)
        docs_service = gdocs.get_docs_service(config, workspace, interactive=False)
    except ModuleNotFoundError as err:
        raise RuntimeError(
            f"Google Docs support is unavailable ({err.name}); install authorlm[gdocs]") from err
    except ValueError as err:
        raise RuntimeError(
            "Google Docs are not authorized here; run 'authorlm doc auth' explicitly") from err
    # Snapshot before the reconcile can overwrite anything (BUG-1 / A2):
    # a base-less mapped file's pull is destructive, and this is the
    # recovery point. The post-reconcile collect below is conditional on
    # something having been pulled; this one is not.
    api.collect(db, manuscript, config, analyze=False)
    report = gdocs.reconcile(db, manuscript, service, docs_service=docs_service)
    if report["pulled"]:
        report["collection"] = api.collect(db, manuscript, config, analyze=False)
    return report


def _recommendations(db: Database, manuscript: dict,
                     triage_type: str) -> dict[str, Any]:
    if triage_type not in {"concepts", "edges"}:
        # Proposals and critique items have no deterministic rules: each is
        # already an opinion awaiting the author's verdict. An empty plan,
        # not an error — the button is honest instead of broken.
        return {"decisions": [], "protected": [],
                "counts": {"concepts": 0, "edges": 0, "protected": 0}}
    files = read_manuscript_files(Path(manuscript["path"]))
    return triage_rules.plan(
        db, manuscript, files,
        include_concepts=triage_type == "concepts",
        include_edges=triage_type == "edges",
    )


def dispatch(method: str, params: dict[str, Any], workspace: str | None = None,
             db: Database | None = None) -> dict[str, Any]:
    db = db or api.open_db(workspace)
    manuscript = api.get_manuscript(db, params.get("manuscript"))
    triage_type = params.get("triage_type", "concepts")
    if method == "snapshot":
        result = triage.snapshot(
            db, manuscript, triage_type, params.get("profile_id"),
            params.get("profile_version"))
        result["sync"] = _sync_status(db, manuscript)
        return result
    if method == "stage":
        return triage.stage_decisions(
            db, manuscript, triage_type, params.get("decisions", []))
    if method == "recommendations":
        return _recommendations(db, manuscript, triage_type)
    if method == "stage_recommendations":
        object_ids = list(dict.fromkeys(params.get("object_ids", [])))
        if not object_ids:
            raise ValueError("select at least one recommendation to stage")
        plan = _recommendations(db, manuscript, triage_type)
        by_id = {item["object_id"]: item for item in plan["decisions"]}
        stale = [object_id for object_id in object_ids if object_id not in by_id]
        if stale:
            raise ValueError(
                f"{len(stale)} selected recommendation(s) are no longer current; "
                "find safe recommendations again")
        result = triage.stage_decisions(db, manuscript, triage_type, [{
            "object_id": object_id,
            "action": by_id[object_id]["action"],
            "parameters": by_id[object_id].get("parameters") or {},
        } for object_id in object_ids])
        return {**result, "recommendations": [by_id[object_id]
                                               for object_id in object_ids]}
    if method == "unstage":
        return triage.unstage_decisions(
            db, manuscript, triage_type, params.get("object_ids", []))
    if method == "apply":
        return triage.apply_selected(
            db, manuscript, triage_type, params.get("object_ids", []))
    if method == "start_analysis":
        return triage_analysis.start_run(
            db, manuscript, api.load_config(workspace), triage_type,
            params.get("object_ids", []), params.get("profile_id"),
            params.get("profile_version"))
    if method == "analyze_batch":
        return triage_analysis.analyze_batch(
            db, manuscript, api.load_config(workspace), params["run_id"],
            params.get("object_ids", []))
    if method == "run_status":
        return triage_analysis.run_status(db, manuscript, params["run_id"])
    if method == "sync_status":
        return _sync_status(db, manuscript)
    if method == "reconcile_docs":
        return _reconcile(db, manuscript, workspace)
    raise ValueError(f"unknown triage request '{method}'")
