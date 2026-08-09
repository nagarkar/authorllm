# EditorLLM → AuthorLM feature research (2026-08-05)

Research-only survey of /Users/nagarkar/workspace/repos/EditorLLM, oriented
around verbs (explicit and implicit), with mappings onto AuthorLM's
architecture. Produced by a read-only research agent; nothing was modified
in either repo. Ranked candidates await the author's prioritization —
nothing here is ratified.

## What EditorLLM is

A Google Apps Script add-on bound to a Google Doc (TypeScript → flat-scope
GAS bundle) plus a browser sidebar and a partly-stubbed Tauri desktop
companion. The manuscript lives in Google Docs tabs; the "database" is
Document/User/Script Properties + CacheService; all agent output lands as
Drive comments, bookmarks, named ranges, and generated tabs.

Two framing facts:

1. **EditorLLM never learns.** No evidence store. Author reactions are
   discarded or fed back only as prompt context on the next run. AuthorLM's
   premise — verdicts as durable evidence → learned policies — is absent.
2. Its W2 sweeps are annotation-only: comments and highlights, never prose
   replacement (the user manual claims otherwise; the code does not).

## 1. Verb inventory

### Agent-facing core (the "three workflows", src/BaseAgent.ts)

- **generateInstructions (W1)** — regenerate an agent's own system-prompt
  tab from the manuscript. Reads StyleProfile + own tab + the *last
  generated version* (Scratch tab) so the author's manual edits are
  detected and preserved. Old tab backed up before overwrite
  (src/CollaborationService.ts:748).
- **evaluateInstructions (implicit)** — LLM-as-judge scores the machinery
  it just wrote, 0–5 + rationale, against per-agent markdown rubrics
  (src/agentHelpers.ts:704, src/prompts/agents/*-rubric.md), persisted and
  shown as sidebar badges. Fires automatically after every W1.
- **annotateTab (W2)** — sweep one tab, place anchored comments.
  {match_text, reason} ops, grounding-validated, prior annotations cleared
  per agent per tab first.
- **annotateSelectedTabs** — multi-tab sweep, per-tab error isolation
  (src/Code.ts:1200).
- **handleCommentThreads (W3)** — reply to @tag comment threads
  (src/CommentProcessor.ts:253): tag read from the *last* message (threads
  re-routable mid-conversation), threads grouped by agent, chunked 5–10
  per LLM call, batch replies validated (hallucinated/duplicate thread IDs
  dropped, src/BaseAgent.ts:239).

### The annotation primitive (docs/annotations.md — best machinery in repo)

Four artifacts per annotation, created in rollback-safe order, joined by
one bookmark ID: bookmark + named range (exact span) + Drive comment whose
**body embeds the bookmark URL** (recovery key lives in the comment itself
— cleanup needs no side store) + highlight. Cleared by agent prefix, tab,
doc-wide, or comment ID (src/CollaborationService.ts:95-677).

**Auto-cleanup (implicit)**: a time trigger polls resolved comments and
clears their artifacts — *author resolving a comment is the accept signal*
— but nothing is ever recorded from it (src/AnnotationAutoCleanup.ts).

**Typed directives**: anchored structured metadata (named range + JSON
payload in DocumentProperties) with CRUD, absolute-offset computation, and
lazy orphan GC (src/DirectivePersistence.ts, src/agentHelpers.ts:423-648).

### The seven built-in agents (each a lens)

- **Architect** — generates the StyleProfile, an eight-section "author
  operating system" (philosophy, worldview, argument logic, voice, rhythm,
  vocabulary register, structure, motifs) + downstream-agent guidance +
  mood/personality/chapter-mood sections
  (src/prompts/agents/architect-styleprofile-schema.md).
- **EarTune** — read-aloud cadence sweep; every annotation must carry
  `Suggested rewrite: "..."`; phonetic lexicon suggestions.
- **Auditor** — internal consistency vs. the manuscript's own axioms,
  notation, terminology.
- **Tether** — "External Anchor": bridges to the scholarly record;
  forbidden to correct the framework from outside; triages findings as
  Error | Controversial (flag, never recommend removal) | **Alignment**
  (scholarship that supports the author)
  (src/prompts/agents/tether-system-role.md).
- **TTS** — cast/voice + pause directives as anchored bookmark directives.
- **Publisher** — front/back matter tabs.
- **GeneralPurpose** — @AI catch-all responder, prompt is an author-edited
  doc tab.

### User-defined agents + teams

- Custom agents: {name, @tag, system prompt, instruction tab, context tab,
  workflows}, scoped user/document/script with promote/demote and
  ownership (src/CustomAgentService.ts). **Export/import bundles carry the
  tab contents** — a lens is a self-contained transferable artifact.
- W6 "run with context": source + prompt + context + a user-typed action →
  writes into an existing output tab; never touches the source.
- Teams: ordered lens lists run round-robin per paragraph, each seeing
  prior turns; **resumable** via lastProcessedIndex checkpoint + 4-minute
  wall clock + status:'continuing' (src/AgentTeamAnalysis.ts).

### Manuscript management / publishing / audio / infra (abridged)

- Tab merge into Manuscript; standard-tab scaffolding; 810-line
  markdown↔Docs round-tripper; **managed-tab allowlist** with
  OVERRIDE-logged force paths (src/DocOps.ts:472, src/Code.ts:933).
- Publishing: one enum-constrained LLM call generates Copyright, About The
  Author, 3 sales blurbs, 3 spoiler-free hooks (with duration/rationale),
  cover concepts, audio credits; "missing" mode fills only blanks
  (src/PublisherAgent.ts:52). Cover images generated **seeded by the
  existing baseline** with timestamped archiving
  (src/CoverImageGenerator.ts). EPUB/ACX packages in versioned Drive
  folders + a **structural readiness audit** (src/Code.ts:676) checking
  every deliverable piece exists.
- Audio: TTS directives, break heuristics at headings/ellipses, manifest
  builder, ElevenLabs stitching, PLS pronunciation rules.
- Infra: live job console with paged logs (src/Tracer.ts); retry with
  backoff that **scales with input size** + Retry-After parsing
  (src/HttpRetry.ts); prompts as markdown compiled to constants and
  dumped to full_prompts.md for review (scripts/generate_prompt_constants.js).

## 2. What AuthorLM lacks — ranked

### Tier 1 — high value

1. **Anchored suggestions + resolution-as-verdict.** Project guidance
   suggestions into the Doc as anchored comments carrying an opaque
   suggestion_id in the body (EditorLLM's join-key-in-comment trick);
   store (suggestion_id, comment_id, file, quote) in SQLite; on pull,
   **resolved = accepted, reply text = the explanation**, feeding
   review_suggestion automatically with the author's verbatim words. Turns
   the Doc into an evidence-collection surface without editing prose.
   Caveat: AuthorLM's push rebuilds tabs, killing anchors (gdocs.py:213) —
   needs an anchor-survival strategy.
2. **Tether — the External Anchor lens.** Most philosophy-native feature.
   `authorlm tether <file>`: file text + Concept Graph notes + profile →
   {quote, kind: error|controversial|alignment, note, source}, flowing
   through the existing review loop as evidence. The Concept Graph lets it
   check external claims against *ratified* notes — something EditorLLM
   structurally cannot do. "Alignment" findings (scholarship that supports
   the author) are the killer half.
3. **Machinery self-regeneration + LLM-as-judge scoring.**
   `style propose` / `policy propose`: generate a revised style guide from
   manuscript + verdict history, land it as a *proposal* (never
   auto-ratified — strictly better than EditorLLM's overwrite-then-review).
   Borrow: keeping the last generated version so author hand-edits are
   detectable evidence; rubric-scored 0–5 judge with persisted rationale
   as a quality gate on any generated artifact.
4. **Custom lenses + portable bundles.** `authorlm lens add/list/run`:
   author-defined lenses ("Kantian objections", "Sanskrit transliteration
   consistency") producing ordinary suggestions → ordinary verdicts → the
   same policy learning. Export bundles that carry their prompt AND
   dependent context = reusing editorial judgment across manuscripts.
5. **Suggestion hygiene filters.** Grounding validation (drop ops whose
   match_text isn't verbatim in the passage), no-op detection (drop
   suggestions whose proposed rewrite already appears), anchor-collision
   checks (src/agentHelpers.ts:81-150). Ungrounded suggestions poison the
   evidence: the author rejects noise and the policy learner concludes
   something false about their taste. Cheap; do first.
6. **Consistency audit vs. the Concept Graph.** `authorlm audit <file>`:
   run the whole file against ratified concept notes + typed edges
   (refutes/depends-on), flag contradictions and terminology drift, output
   as suggestions with concept IDs so verdicts become evidence about the
   concept.

### Tier 2 — medium

7. Resumable checkpointing for long runs (run_progress table).
8. W6-style `derive <file> "<action>" --to <path>` — transform-to-sink,
   never touching manuscript content (précis, objection list, lecture
   outline).
9. Multi-file sweep mode with per-file error isolation + run report.
10. Live job console (jobs table, --follow, MCP get_job_status).
11. Publication readiness audit (`export --check`): ratified styles?
    unresolved proposals? unrendered slots? pandoc present? open intents?
12. Front/back-matter generation with fill-only-blanks preservation.
13. Illustration iteration seeded by the existing image with baseline
    archiving (partially covered by illus --from; archiving idea is new).

### Tier 3 — niche

14. Audiobook pipeline (directives abstraction is the generalizable part).
15. Managed-target allowlist + OVERRIDE-logged force paths.
16. Provider/model configuration surface with live model listing.

## 3. Overlapping verbs where EditorLLM is better (steal the pattern)

- Suggestion placement: four-artifact join keyed on an ID embedded in the
  comment body; ordered creation with rollback → no orphans.
- Comment threads: batch N per LLM call with thread-ID schema, validate
  replies, drop hallucinated IDs; tag from last message enables
  re-routing; only registered tags match (no @Aristotle false positives).
- Generated-artifact validation: enum-constrained schemas + post-validator
  reporting {written, missing, unexpected}.
- Rubric-scored self-evaluation of machinery with persisted rationale.
- **Prompts as first-class reviewable markdown assets** (src/prompts/**)
  compiled at build; diffable in git, readable by the author. The
  StyleProfile schema content is directly relevant to AuthorLM's style and
  profile schemas.
- Backoff scaling with input size; Retry-After parsing.
- Resumable checkpoints + deliberate quota-gap sleeps between chained calls.
- Export: versioned artifact folders + readiness audit layer.
- Portable bundles that carry content, not references.

**Where AuthorLM is already better (don't regress):** real computed diffs
vs. asking the LLM to spot edits; durable evidence + learned policies vs.
re-deriving from the manuscript every run; typed Concept Graph vs. one
prose StyleProfile blob; explicit ratification vs. overwrite-then-review;
SQLite vs. 9KB property values.

## 4. Maturity

Working, deployed, tested product (~13.7k lines TS + ~15k sidebar; 24 unit
suites/~600 its, integration tier with real Gemini calls + cost
accounting, E2E tier posting real Doc comments; deploy scripts gate on the
pyramid). Weak spots: 3,315-line Code.ts grab-bag, stale docs, loose git
hygiene (99 uncommitted files). **Don't borrow code** (GAS storage/idioms
don't transfer); **do borrow**: the prompt corpus (StyleProfile schema,
Tether role, rubrics), the annotation join-key design, validation helpers,
batch-reply normalisation, retry policy, checkpointing pattern.
