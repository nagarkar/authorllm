# Triage App Backend Integration: Change Record and Risk Guide

Status: implemented in a dirty, uncommitted worktree; ownership review required  
Date: 2026-08-15  
Scope: backend changes that intersect the existing CLI, MCP server, database, and packaging

## Why this document exists

The Triage App was added while the repository already contained substantial uncommitted work. A clean baseline was not established and the owner was not asked to confirm which dirty files were safe to modify. That was a process failure.

As a result, `git diff HEAD` is not a reliable record of the Triage App changes alone. It combines earlier work by other contributors with this implementation. This document records the changes attributable to the Triage App work from the conversation and identifies the places where those changes can affect existing CLI or MCP behavior.

Do not use this document to infer ownership of unrelated hunks in a modified file. In particular, changes in `briefing.py`, `concepts.py`, `gdocs.py`, `guidance.py`, `plan.py`, `policies.py`, `sessions.py`, `shell.py`, `styles.py`, `threads.py`, and `ui.py` were already part of the mixed worktree and are not claimed as Triage App work here.

## Intended architecture

The intended layering is:

```text
React UI / local HTTP / MCP App / interactive CLI
                    |
              surface adapters
                    |
          application API or service
                    |
      triage domain and analysis services
                    |
 concepts.py / extraction.py / revisions.py / llm.py
                    |
                  db.py
```

The implementation currently shares the triage mutation kernel, but does not yet consistently use the existing `api.py` facade as the single application boundary.

Current call paths are:

```text
Web or MCP App
  -> triage_transport.dispatch()
  -> triage_rules.plan() / triage.stage_decisions() / apply_selected()
  -> triage.apply_action()
  -> concepts.py / extraction.py / db.py

Interactive CLI triage
  -> cli._run_triage() / cli._run_edge_triage()
  -> triage.apply_one()
  -> triage.apply_action()
  -> concepts.py / extraction.py / db.py

Existing MCP graph tools
  -> api.py graph wrappers
  -> triage.apply_one()
  -> triage.apply_action()

Some one-shot CLI graph verbs
  -> cli.py direct database/domain calls
```

The last path is the remaining inconsistency. The Triage App and interactive CLI triage share mutation semantics. Not every pre-existing `concept` CLI verb has been routed through the shared application facade.

## Files attributable to this feature

### New backend files

| File | Responsibility |
| --- | --- |
| `authorlm/triage.py` | Schema-driven triage read model, analyzer profile loading, persistent drafts, validation, and concept/edge mutations |
| `authorlm/triage_rules.py` | Pure zero-token planner for conservative bulk concept/edge decisions; protects staged and author-connected rows |
| `authorlm/triage_analysis.py` | On-demand LLM runs, pinned manuscript and graph snapshots, batching, validation, and assessment persistence |
| `authorlm/triage_transport.py` | JSON request dispatcher shared by the local HTTP server and MCP App |
| `authorlm/triage_server.py` | Dependency-free localhost HTTP server for the compiled standalone app |
| `authorlm/triage_profiles/concept-keepability.v1.json` | Built-in concept analyzer profile |
| `authorlm/triage_profiles/edge-keepability.v1.json` | Built-in edge analyzer profile |
| `authorlm/triage_dist/index.html` | Compiled single-file frontend served by local HTTP and MCP resource handlers |
| `tests/test_triage_app.py` | Backend, CLI-parity, mutation, staleness, and analysis-contract tests |

### Existing or already-dirty files touched for integration

| File | Triage-related change |
| --- | --- |
| `authorlm/db.py` | Added triage tables and indexes; added transaction depth and `Database.transaction()`; identifies deterministic triage evidence as system-authored |
| `authorlm/api.py` | Delegated graph confirm, retire, merge, and edge decisions to the shared triage mutation service |
| `authorlm/cli.py` | Reused triage help and mutation actions in interactive triage; added `triage-app` and deterministic bulk triage; included triage tables in unregister cleanup |
| `authorlm/mcp_server.py` | Added MCP App resource, `open_triage_app`, and app-only `triage_app_request` (**all three removed 2026-09-04** — the MCP App road never rendered in the author's client; the local HTTP server is the one road); `_guard` now treats `RuntimeError` as an author-readable failure |
| `authorlm/extraction.py` | Extended `record_triage()` with an optional rejection reason; excludes deterministic retirements from author feedback |
| `authorlm/prompt_registry.py` | Registered the two analysis system prompts and two profile prompts; the file itself had mixed provenance |
| `pyproject.toml` | Packaged profile JSON, prompt Markdown, and compiled app HTML |
| `tests/test_api.py` | Added the two Triage App MCP tools to the expected MCP surface |

The optional dependency changes in `pyproject.toml` (`mcp<2`, `gdocs`, and `all`) predate the Triage App implementation in this conversation and should not be attributed to this feature without separate review.

## Shared triage service

`authorlm/triage.py` is intended to be independent of React, HTTP, MCP, and terminal presentation.

It owns:

| Area | Behavior |
| --- | --- |
| Schemas | Defines deterministic columns, available actions, parameter and reason-prompt requirements, and shared help for `concepts` and `edges` |
| Profiles | Loads built-ins plus `<manuscript>/_triage/profiles/*.json`; a manuscript profile overrides the same profile id and version |
| Snapshot | Joins graph rows with the selected assessment, draft decisions, analyzer profiles, manuscript revision state, and incomplete runs |
| Drafts | Persists one pending decision per manuscript, triage type, and graph object |
| Recommendations | Computes conservative zero-token actions from the current manuscript and graph without persisting them |
| Validation | Validates action names, concept kinds, edge relations, aliases, and required parameters |
| Mutation | Applies keep, retire/reject, retype, flip, alias merge, and concept note changes |
| Evidence | Records concept or edge triage evidence where the calling workflow historically does so |

There are three mutation entry points:

- `apply_one()` wraps a single action in a database transaction. Interactive CLI triage and API graph wrappers use this path.
- `apply_selected()` validates every selected draft and then applies the whole set in one transaction. The UI uses this path.
- `apply_deterministic()` validates every generated rule and object version,
  applies the complete zero-token plan in one transaction, and records system
  provenance. The deterministic CLI and hygiene apply path use this entry point.

All three call `apply_action()`. This is the shared mutation kernel.

### Batch and conflict semantics

Drafts store the graph object's `version` at the time the decision is staged. `apply_selected()` rejects the entire batch if any selected row has changed or disappeared. No selected mutation is committed in that case.

Alias merges are restricted to one row per backend apply operation because a merge can repoint or retire several graph rows and invalidate other staged decisions. The UI can collect canonical choices for several selected rows through a one-at-a-time wizard; selected rows that already have an alias draft are skipped. When applying a selection containing aliases, the UI sends separate one-row `apply` requests. This preserves the backend guard but means a later conflict can stop the sequence after earlier merges have committed; the UI reports partial completion explicitly.

Drafts are database-persistent and are not scoped to a browser, MCP connection, CLI session, or user. A second client staging a decision for the same object overwrites the first client's draft. This is a known concurrency limitation.

Deterministic recommendations are client-side, transient review data. They are
not drafts and do not survive a refresh. Before converting selected
recommendations into drafts, the transport recomputes the current plan and
rejects any object whose recommendation is no longer valid. UI selection is
also transient and independent of both recommendations and drafts: deselecting
a row never deletes a staged decision.

### Evidence compatibility

Interactive concept and edge triage continue to record the same evidence categories and legacy edge-description format used by the previous CLI implementation.

Deterministic bulk decisions use `evidence_type=deterministic_triage` with a
system source and rule/object metadata. They intentionally bypass
`concept_triage` and `edge_triage`: those streams represent author judgment and
feed future extraction prompts. The extraction feedback query excludes concepts
retired by deterministic evidence.

One-shot edge API operations use `record_evidence=False` to preserve their previous behavior. They mutate edge status or relation but do not add interactive-triage evidence.

Concept retirement can now accept a reason from the UI. `extraction.record_triage()` appends that reason to the evidence target. Existing callers pass no reason and retain their previous output.

The `retire`, `retype`, and `alias` action schemas include an optional `reason`
descriptor. UI clients use this metadata to prompt before staging instead of
hardcoding action names. Keep and other actions neither prompt for nor persist a
reason. Retype and alias reasons are included in the resulting triage evidence;
CLI and MCP clients that ignore the additive descriptor retain their existing
behavior.

## CLI intersections

The following existing interactive CLI operations now call the shared triage service:

| CLI workflow | Shared operation |
| --- | --- |
| Concept keep | `apply_one(..., "concepts", ..., "keep")` |
| Concept retype | `apply_one(..., "concepts", ..., "retype")` |
| Concept retire | `apply_one(..., "concepts", ..., "retire")` |
| Concept alias merge | `apply_one(..., "concepts", ..., "alias")` |
| Concept note reword | `apply_one(..., "concepts", ..., "reword_notes")` |
| Edge confirm | `apply_one(..., "edges", ..., "keep")` |
| Edge retype | `apply_one(..., "edges", ..., "retype")` |
| Edge flip and confirm | `apply_one(..., "edges", ..., "flip")` |
| Edge alias merge | `apply_one(..., "edges", ..., "alias")` |
| Edge reject | `apply_one(..., "edges", ..., "reject")` |

The original CLI row query, ordering, fixed-width note wrapping, key handling, output wording, and evidence formatting were restored after compatibility review. This matters because UI display rows deserialize fields such as `aliases`; passing those display rows into legacy concept code caused an alias-merge failure. CLI selection now continues to use raw database rows while sharing only the mutation service.

The new CLI command is:

```text
authorlm triage-app [-m MANUSCRIPT] [--host 127.0.0.1] [--port 0] [--no-open]
authorlm concept triage --deterministic [--nodes|--edges] [--apply]
```

It starts a blocking local `HTTPServer`, serves the compiled app, and exposes `POST /api/triage`. It does not start a background daemon.

The deterministic command reads the current manuscript, builds a mutation-free
plan, and prints it. `--apply` revalidates every object version before using the
shared `triage.apply_action()` semantics inside one transaction. Edge decisions
run before concept retirements so collateral edge retirement cannot hide a
planned rejection. No partial plan is committed on conflict.

### CLI paths not yet normalized

`cmd_concept` still contains some direct database/domain mutations, including one-shot edge confirmation, edge rejection, unconfirmation, and graph editing. Some equivalent functions in `api.py` already delegate to `triage.apply_one()`, but the CLI does not consistently call those API functions.

This means the architecture is currently shared for interactive triage, not universally shared for every graph mutation exposed by the CLI.

## MCP intersections

The MCP server now exposes:

| MCP item | Visibility | Behavior |
| --- | --- | --- |
| `ui://authorlm/triage-app.html` | Resource | Returns the compiled single-file app as `text/html;profile=mcp-app` |
| `open_triage_app` | Model | Resolves the manuscript and opens the MCP App resource |
| `triage_app_request` | App only | Dispatches app requests into Python; models should not invoke it directly |

`triage_app_request` accepts a method and parameter object. Supported methods are:

| Method | Backend effect |
| --- | --- |
| `snapshot` | Read schemas, rows, assessments, drafts, profiles, and incomplete runs |
| `recommendations` | Compute the current lane's zero-token plan without persisting or applying it |
| `stage_recommendations` | Recompute selected recommendations, reject stale selections, and create normal persistent drafts |
| `stage` | Create or replace persistent decisions |
| `unstage` | Delete selected decisions |
| `apply` | Atomically apply the decisions in one request; alias requests remain limited to one row |
| `start_analysis` | Collect local files, pin a revision and graph rows, build or reuse analysis context, and create a run |
| `analyze_batch` | Run and persist one bounded LLM batch |
| `run_status` | Return completed and remaining object ids |
| `sync_status` | Report whether a Google Docs master is linked |
| `reconcile_docs` | Reconcile Google Docs and collect any pulled changes |

The MCP `_guard()` now catches `RuntimeError` in addition to `LookupError` and `ValueError`. This converts expected analysis, configuration, and Google Docs failures into `{ok: false, error: ...}` instead of raising through FastMCP. That change affects all MCP tools that raise `RuntimeError`, not only the Triage App.

The Triage App MCP tools use the same `AUTHORLM_WORKSPACE` resolution and database location as existing MCP tools. Opening the app does not itself open an AuthorLM editing session.

## Google Docs and collection behavior

Analysis has two separate pre-analysis steps:

1. The UI can explicitly call `reconcile_docs`. If linked and authorized, reconciliation may pull Doc-side changes or push local-only changes. Pulled changes are followed by `api.collect()`.
2. `start_analysis()` always calls `api.collect(..., analyze=False)` before pinning the manuscript revision.

No Google Docs pull occurs silently inside `start_analysis()`. If the user elects to analyze the local snapshot, collection still records the current local files before the LLM call.

Contributors must account for the fact that starting analysis can create a manuscript revision even though it does not mutate manuscript files or graph decisions.

## LLM analysis and persistence

`authorlm/triage_analysis.py` implements synchronous, caller-driven analysis. There is no queue, worker, daemon, or automatic restart.

The lifecycle is:

1. Collect local manuscript files.
2. Resolve and snapshot the selected analyzer profile.
3. Pin the collected manuscript version and requested graph-row versions.
4. Build one reusable whole-manuscript ontology map, or reuse one for the same manuscript version and model.
5. Accept bounded object batches from the caller.
6. Build deterministic evidence packets from pinned manuscript passages.
7. Validate that the LLM returns exactly one result per requested object and cites only passages supplied for that object.
8. Persist each completed batch.

Completed batches survive a closed browser or disconnected MCP client. An incomplete run remains visible and can be resumed explicitly. The LLM itself does not resume; only missing object ids are sent in later calls.

An assessment is current only when all of the following remain true:

- The selected profile id and version match.
- The assessment references the latest collected manuscript version.
- Local manuscript files still match that collected version.
- The graph object's current version matches the pinned object version.

Otherwise the assessment remains visible as `outdated`.

## Database changes and global implications

Three additive tables were introduced:

| Table | Purpose |
| --- | --- |
| `triage_runs` | Analyzer profile snapshot, pinned manuscript revision, requested ids, reusable context, and run status |
| `triage_assessments` | One persisted LLM output per run and object |
| `triage_drafts` | One pending author decision per manuscript, triage type, and object |

The tables are created by the global `SCHEMA` script using `CREATE TABLE IF NOT EXISTS`. `SCHEMA_VERSION` remains `1`; there is no explicit migration record for these additions.

`Database.insert()` and `Database.update()` no longer commit while `_transaction_depth` is nonzero. `Database.transaction()` uses `BEGIN IMMEDIATE`, commits the outermost successful block, and rolls back the outermost failed block.

This is the highest-impact cross-cutting change because every CLI and MCP operation uses the same `Database` class. Outside an explicit transaction, insert and update retain their previous immediate-commit behavior.

Nested transactions are depth-counted, not SQLite savepoints. If an inner transaction fails and its exception is caught inside an outer transaction, the inner writes are not independently rolled back. Current triage callers let failures propagate, but future callers must not assume savepoint semantics.

`cmd_unregister` now deletes triage assessments, runs, and drafts before deleting the manuscript.

## Packaging changes

Setuptools package data now includes:

```text
prompts/*.md
triage_profiles/*.json
triage_dist/*.html
```

Fresh editable and wheel installs need these files or the MCP/local app and built-in profiles will fail at runtime. The frontend source under `web/triage-app` is not needed at runtime, but is needed to rebuild `triage_dist/index.html`. `triage_dist/index.html` is build output and not tracked in git: `setup.py` builds it on every install (2026-10-05).

## Compatibility work performed

The following regressions were found and corrected during the post-implementation CLI audit:

| Regression | Correction |
| --- | --- |
| Shared help changed punctuation | Restored the exact existing CLI help text and made it the shared schema help |
| Edge flip plus retype was recorded as confirmed | Preserved the old `retyped` signal and before/after evidence format |
| One-shot edge API commands began producing triage evidence | Added explicit no-evidence behavior for legacy one-shot API operations |
| CLI concept triage consumed UI-normalized alias lists | Restored raw database rows for CLI selection while retaining shared mutations |
| CLI note display changed wrapping | Restored the original fixed 76-column wrapping |

## Known risks and loose ends

### 1. Establish an attributable baseline before further edits

The worktree must be reviewed with the people who owned the pre-existing changes. Create a file-and-hunk inventory, identify which changes belong together, and split them into reviewed commits or branches. Do not revert mixed files wholesale.

The pre-Triage state cannot be reconstructed reliably from `HEAD` because relevant files were already dirty.

### 2. Normalize the application boundary

The repository describes `api.py` as the single logic layer beneath CLI and MCP, but the Triage App transport and interactive CLI call `triage.py` directly.

Recommended resolution:

```text
CLI adapter ---------+
HTTP adapter --------+--> api.py triage facade --> triage.py / triage_analysis.py
MCP App adapter -----+
```

Add thin triage facade functions to `api.py`, route `triage_transport.py` and interactive CLI through them, and keep presentation and transport logic out of the facade.

### 3. Decide the scope of shared graph mutations

Choose whether all `concept` CLI graph mutations should use the same service or whether only interactive triage belongs there. Today the split is implicit. It should be an explicit architectural decision with compatibility tests.

### 4. Review global transaction semantics

The new transaction mechanism affects a foundational class. Add focused tests for:

- Existing immediate commits outside transactions.
- Full rollback of concept retirement and alias merge failures.
- Evidence and graph mutation atomicity.
- Nested transaction behavior, or replace depth counting with savepoints.
- Concurrent CLI and MCP writers under SQLite WAL.

### 5. Decide draft ownership and concurrency behavior

Persistent drafts are shared globally per object. Decide whether that is intentional. If multiple app clients are supported, drafts need an owner or workspace session key and explicit conflict behavior.

### 6. Add an applied-batch audit and undo decision

A guarded undo for the most recent compatible applied batch was discussed but not implemented. There is no apply-batch ledger. Graph evidence remains, but the exact set of rows changed by a multi-object apply is not recorded as one reversible unit.

Either implement a version-checked apply ledger and inverse operation or explicitly remove undo from the intended scope.

### 7. Formalize schema migration and retention policy

Decide whether additive tables require a `SCHEMA_VERSION` bump. Also define retention or cleanup for old runs, large stored ontology contexts, assessments, and abandoned drafts.

### 8. Harden the standalone HTTP boundary

The default host is loopback, but `--host` can expose the unauthenticated mutation API. There is no authentication, CSRF defense, origin check, request-size limit, or content security policy.

Keep it local-only or add explicit safeguards before supporting non-loopback hosting.

### 9. Complete MCP host compatibility testing

The app resource and tool metadata were validated against the installed FastMCP version and exercised locally. Claude Remote is the primary target. Codex/ChatGPT MCP App rendering remains best effort and needs host-level testing.

### 10. Clarify error-envelope ownership

`mcp_server._guard()` now catches `RuntimeError` globally. Review other MCP tools for places where a `RuntimeError` should still be treated as an unexpected server defect rather than an author-readable operational error.

## Verification completed

The current worktree passed:

| Suite | Result |
| --- | --- |
| Existing end-to-end CLI suite | 380 checks passed |
| Existing API/MCP suite | 205 checks passed |
| Triage backend and compatibility suite | 12 tests passed |
| Critique suite | 68 checks passed |
| Passes suite | 39 checks passed |
| Summaries suite | 24 checks passed |
| Frontend production build | Passed; single HTML artifact generated |
| Python compilation | Passed for changed backend modules |
| `git diff --check` | Passed |

These tests reduce compatibility risk but do not resolve the attribution problem or prove host-level MCP App compatibility.

## Safe contributor checklist

Before changing the CLI, MCP tools, database, or triage code:

1. Read this document and inspect the current dirty worktree.
2. Confirm which uncommitted hunks belong to which contributor.
3. Preserve the shared `apply_action()` semantics or change CLI and UI tests together.
4. Do not pass UI-normalized display rows into low-level concept functions that expect database JSON strings.
5. Preserve optimistic object-version checks for staged batch application.
6. Treat `Database.transaction()` as outer-transaction atomic, not savepoint-nested.
7. Rebuild `authorlm/triage_dist/index.html` after frontend changes (`npm run build` in `web/triage-app/`; it is not tracked in git).
8. Run `tests/test_e2e.py`, `tests/test_api.py`, and `tests/test_triage_app.py` before handing off.
9. Test `open_triage_app` and `triage_app_request` through the actual target MCP host before release.
10. Commit only reviewed, attributable hunks; do not bundle unrelated dirty-worktree changes.
