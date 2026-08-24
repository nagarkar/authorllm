# AuthorLM — Minimum MVP Definition

Derived from the **Common Core RFC (v2.1)** and the **AuthorLM Specialization
RFC (v2.1)** in `../specs/`. Target use case: **writing new works of
philosophy** with an editorial collaborator that learns from evidence.

## Scope selection rationale

The AuthorLM RFC prescribes its own MVP boundary:

- **§23.7 growth order:** revision collection → transitions → episodes →
  declared intent → Concept Graph → guidance → *(replay → long-term memory)*.
  The MVP implements stages 1–6; 7–8 are deferred.
- **§23.9:** Version 1 is a **single orchestrator**, not multi-agent —
  linear stages, pure functions, every stage leaving an inspectable artifact.
- **§23.1 four questions** (does it improve evidence / explainability /
  replay / understanding of the author?) gated every feature below.
- **§19.14:** AuthorLM is *intent-driven*: the pipeline starts from the
  declared writing objective, not from document diffs.

## MVP capabilities

| # | Capability | RFC basis |
| :- | :--- | :--- |
| 1 | Manuscript registration + immutable checksummed revision snapshots | §17.3, §18.4 |
| 2 | Transition detection (insert / rewrite / delete, located by file + heading) | §17.4, §19.5 |
| 3 | Declared intent capture (authoritative observations) | §9.2, §20.4 |
| 4 | Editorial episodes grouping transitions under the session's intent | Ch. 10, §17.5 |
| 5 | Concept Graph: declared/realized nodes, typed edges, auto-realization, inferred co-occurrence edges as hypotheses | Ch. 21 |
| 6 | Guidance with mandatory explanations + **abstention** as a first-class output | §17.10, §11.5–§11.6 |
| 7 | Review loop (accept/reject/modify/defer + explanations) → evidence → policy confidence updates; explained rejections seed candidate policies | §20.6, §12.4, A.10 |
| 8 | Policy lifecycle: candidate → validated → retired, driven by cross-session evidence; conservative promotion | §8.2–§8.3, Common Core §8.5 |
| 9 | Session-opening learning briefing + learning-velocity report at session end | §20.3, §24.4 |
| 10 | KnowledgeObject base (id, version, created_at, created_by, schema_version, metadata) on every persistent object | §18.3 |

## Guidance heuristics (all evidence-traceable)

1. **Bridge**: a concept named by the active intent is declared in the graph
   but unrealized in text → introduce it, anchored to realized neighbors.
2. **Prerequisite gap**: text order contradicts graph order
   (`permits`/`motivates`/`foreshadows`/`depends_on`).
3. **Unanswered objection**: objection nodes with no incoming `answers` edge.
4. **Policy reminder**: validated policies apply; candidate policies ask for
   confirmation (once per session — support must span independent sessions).
5. **Abstention** when none of the above is justified.

## Tech stack (per RFC §23.2)

Python 3.11+ (stdlib core), SQLite, Markdown manuscripts, TOML project
config (`config.toml`) with secrets in `.env`; optional LLM via LiteLLM
or an OpenAI-compatible HTTP API (off / unreachable → graceful
deterministic fallback).

## Deliberately deferred

- **Replay engine** (§20.10, §24.5) — the immutable observation store makes
  this possible later without schema change.
- **Version access & restoration** (§18.9) — CLI to view any collected
  revision and restore one to disk (`history show` / `history restore`).
  Every revision is already stored in full in `manuscript_versions`, so
  this is implementable at any time with no schema change. Restoration
  must write the old content as a *new* revision — history is never
  rewound, only advanced (Common Core §6.5).
- **Long-term cross-project memory** (Ch. 22).
- **Inferred intents** (table exists; generation needs an LLM to be useful).
- **Execution levels ≥2** (prepared revisions, mechanical auto-edits).
- **Prompt/reasoning-artifact versioning** (§24.6) — no prompts persist yet.
- Multi-agent anything (§23.9 says don't).

## Storage principle

"Persist only what you cannot reliably reconstruct" (§18.8): versions,
transitions, intents, episodes, reviews, evidence, policies, guidance
history, and the Concept Graph persist; retrieval results, prompt text, and
intermediate reasoning do not.
