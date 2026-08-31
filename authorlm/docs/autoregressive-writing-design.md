# Autoregressive Generation at the Paragraph Level — Design

Design for a beat-by-beat co-writing loop: validated manuscript knowledge
seeds a new chapter, the author ratifies a plan, and the system proposes
one unit (paragraph / heading / epigraph) at a time — each conditioned on
the plan, the ratified style, the Concept Graph, the learned policies, and
everything accepted so far — with the author's reaction to every beat
captured as evidence. Companion to `auditors-requirements.md` (the
auditors are this loop's inner critics) and referenced from
`design-backlog.md`.

Status: design ratified 2026-08-01; first manual trial 2026-08-02 (§10);
grilling session 2026-08-02 resolved the build questions and the core loop
is now **built** — see §11 for exactly what is implemented, partial, and
deliberately left out (anything unlisted there is not implemented). The
author remains the execution engine throughout — the loop is an
autoregressive *proposer*, never an autonomous writer (Common Core P3:
machine output is quarantined to hypotheses).

Execution model: the loop runs in **skill mode** — the conversational
agent (Claude Code, under the authorlm skill) is the drafting model and
orchestrator; the `authorlm write` CLI verbs are the deterministic state
machine and evidence channel. The programmatic single-process loop this
document originally specified (§4's explicit cache mechanics, §8) remains
the spec for a *future* built version only.

## 1. Prior art: what to take from Re³ and DOC

Both systems generate long documents by conditioning each passage on a
plan plus the text so far. Neither has a human in the loop or a persistent
learned model of one author — that is AuthorLM's contribution.

### From Re³ (Recursive Reprompting and Revision)

| Re³ module | Adopt / adapt | AuthorLM integration |
|---|---|---|
| **Plan** (premise → setting/outline) | Adapt | The author supplies the outline (§7); the system expands it into beats and the author ratifies. Never machine-invented from a premise. |
| **Draft** — recursive prompt construction: per step, re-assemble only the relevant outline point + story-so-far summary + recent text | **Adopt as the core payload discipline** | Our assembly is *deterministic from the database*, not LLM-selected: the beat's plan item, the graph neighborhood of that beat's concepts, the effective style guide, in-scope policies, the accepted-text tail + running digest. This is Common Core P2's "curated payload" (§4). |
| **Rewrite** — generate k candidates, rerank for coherence/relevance | Adapt, default k=1 | The author is the reranker. Optional `--candidates k` per beat when they want alternatives; every non-chosen candidate rejection is evidence. No automated reranker model. |
| **Edit** — entity-attribute tracking for factual consistency | **Replace** | The Concept Graph is a strictly stronger consistency substrate than Re³'s inferred attribute dictionaries: ratified notes are the author's definitions, `refutes` targets must never be endorsed, aliases resolve, metaphor ≠ identity. The concept auditor (auditors-requirements R3) runs as the per-beat consistency check. |

### From DOC (Detailed Outline Control)

| DOC component | Adopt / adapt | AuthorLM integration |
|---|---|---|
| **Detailed outliner** — hierarchical breakdown of a high-level outline into fine-grained leaf beats, each with a length allocation | **Adopt** | Plan ratification (§5 step 1): the author's outline is expanded into leaf beats — each beat carries its target concepts (graph node refs), its role (narrative opener, definition, objection, transition, close), and a length budget. The author edits and ratifies the beat list before any prose exists. Style structure elements ("open sections with narrative or metaphor", "define key terms in dedicated sections") inform the expansion. |
| **Detailed controller** — a trained classifier steering decoding toward the current outline item | **Replace** | No logit access through hosted APIs, and none needed: the equivalent is the generate → audit → author-gate inner loop. Each draft is checked against its beat spec + style guide + graph *before* the author sees it (cheap self-audit in the same call), and nothing enters the conditioning context without the author's acceptance. |
| Length budgeting per leaf | Adopt | Per-beat length budget in the beat spec; the author's habitual paragraph lengths (observable from collected versions) seed the defaults. |

**What neither system has, which AuthorLM supplies:** a ratified style
guide as law; a curated concept graph with author-confirmed semantics; a
learned policy corpus with confidence; per-beat human verdicts that feed a
learning loop; and provenance (every accepted beat becomes a collected
revision with transitions attributed to an episode).

**Rejected outright:** end-to-end automation (no beat is auto-accepted),
automated revision loops that converge without the author, and any
reranking model standing between draft and author.

## 2. State model

A **writeup** (persisted, resumable) is a DB row (`writeups` table):

- `intent_id` — bound to a declared intent. N writeups per intent, each
  targeting exactly **one file** (the continuity contract, effective style
  guide, and accepted text are all per-file). A writeup is *not* a chapter:
  it is one beat-loop run over one file; a cross-chapter writing intent
  spawns one writeup per file.
- `file`, `mode` (`fresh` is the only built mode; revision mode is out of
  scope, §11), `status` (`active | completed | abandoned`)
- `source_version_id` — the manuscript version pinned at initiation: the
  old text as drafting raw material, and the restore target for abandon
- `plan` — the ratified beat plan as JSON: ordered specs
  `{n, role, concepts, budget, notes}`. Each beat's `n` is **stable and
  monotonic, never reused** (a replan keeps written beats and assigns
  fresh `n`s to the remainder), so recorded verdicts can never point at
  the wrong spec. The counter lives in the row metadata (`next_n`).
- `cursor` — index into the plan of the current beat
- `learnings` — JSON list of distilled session lessons (§5 step 5), each a
  one-line normative note. (Formerly "learnings ledger"; renamed — it is a
  flat list, not a ledger.) Learnings are **not** verdicts: a 15-beat
  writeup yields ~15 verdicts in the review tables and perhaps 0–3
  learnings, written only when a pattern recurs.
- the **accepted text** lives in the file itself (source of truth); no
  digest is stored (the running digest and tail window belong to the
  future programmatic loop, §11)

**Beat proposals are `guidance_history` rows** (`kind='beat'`,
`batch_id` = writeup id, `batch_index` = the beat's `n`), so verdicts flow
through the existing `record_review` pathway unchanged — review row,
evidence (weighted high when explained), policy reinforcement, and
candidate-policy seeding from rejection explanations all come free, and a
writeup's full history (draft → verdict → final) reconstructs from one
query on `batch_id`. Queries that manage guidance batches filter to the
`GUIDANCE_KINDS` allowlist (positive membership, so future kinds never
touch that code): a mid-writeup guidance run cannot supersede a pending
beat, and a beat row cannot hijack "the latest batch" in `review()`.

Completing the writeup does **not** complete the intent — intent
completion stays a separate, conversational `complete_intent` (episode
analysis runs there as usual).

## 3. Conditioning context — the layered payload

Ordered strictly by volatility (this ordering *is* the cache design, §4):

| Layer | Contents | Changes when |
|---|---|---|
| L0 — Law | Effective style guide for the file (rendered, inheritance applied); validated policies in scope; the drafting rules (terms of art capitalized, refuted positions never endorsed, aliases are one concept) | Author ratifies a style/policy change (rare; deliberate) |
| L1 — Chapter frame | Ratified beat plan; settled-knowledge digest: ratified notes for every concept the chapter touches, the continuity contract (§7), relevant precedents ("when you introduced Discernment you did X") | Replanning; concept curation mid-writeup |
| L2 — Accepted text | Running digest + verbatim tail (last N accepted beats) | Every accepted beat (append-only at the tail) |
| L3 — Beat-local | Current beat spec; graph-neighborhood delta not already in L1; learnings; the author's last reaction | Every request |

Serialization of L0/L1 must be deterministic (stable ordering, no
timestamps, no run IDs) — a silent invalidator here forfeits the entire
economics of §4.

## 4. Common Core P2: curated payload with a cache key

> P2 — "Every LLM call: a defined decision, a curated payload, a cache
> key. Token efficiency is the goal; avoidance is not. Identical payloads
> replay from cache; only changed payloads spend." (Common Core §1.12A)

**Skill-mode assessment (2026-08-02): none of the mechanics below need
reimplementing for the built loop.** In skill mode the Claude Code
conversation *is* the transcript: it is naturally append-only (L0/L1
assembled once at initiation via the existing tools, each beat appending
propose → verdict turns at the tail), and the harness applies prompt
caching to that prefix automatically. The economics this section derives —
stable layers billed roughly once, each beat paying only for what
changed — arrive for free, with no `cache_control` code in AuthorLM
(`llm.py` has none today). Everything below therefore specifies the
*future programmatic loop only* and is retained as its reference design;
the same holds for §8.

Per-paragraph generation looks expensive — 30 beats ≈ 30 calls carrying
the same law, plan, and prior text. Prompt caching makes it the *cheap*
architecture, because provider-side caching is a **prefix match**: the
stable layers are billed roughly once, and each subsequent beat pays full
price only for what actually changed.

### Mechanics (Anthropic Messages API, the reference implementation)

- Render order is `tools → system → messages`; a `cache_control`
  breakpoint caches everything before it. Reads bill ~0.1× the input
  rate; writes bill 1.25× (5-minute TTL) or 2× (1-hour TTL). Max 4
  breakpoints per request; minimum cacheable prefix is model-dependent
  (512 tokens on the newest tier, 1024–4096 on others) — our L0+L1 far
  exceeds any minimum.
- **Breakpoint layout** (3 of 4 used, one reserved):
  1. end of L0 (system block) — `ttl: "1h"`
  2. end of L1 (first user block: plan + settled digest) — `ttl: "1h"`
  3. last content block of the most recently accepted turn (the rolling
     tail) — default 5-minute TTL
- **The beat loop is exactly the documented multi-turn pattern**: each
  beat appends an assistant turn (the draft) and a user turn (the
  verdict/accepted text), and the tail breakpoint moves with it. Earlier
  breakpoints remain valid read points, so cache hits accrue
  incrementally as the chapter grows — the marginal cost of beat N is
  ~(beat N's new tokens) + 0.1×(everything before it), not a re-read of
  the chapter.
- **1h TTL on L0/L1 is deliberate**: the author reads, rewords, and
  thinks between beats — gaps routinely exceed 5 minutes. The 2× write
  premium amortizes after 3 reads; a 30-beat chapter reads them ~30
  times.
- **Author edits are tail edits by construction.** When the author
  rewrites a beat, the rewrite replaces the just-proposed draft at the
  tail — invalidating only the tail segment, which is the cheap part.
  Nothing upstream moves. (Mid-chapter surgery — revising beat 3 while
  writing beat 20 — invalidates from beat 3; the session should surface
  that cost and offer to defer the edit to a revision pass.)
- **Learnings without invalidation**: on models supporting
  mid-conversation system messages, learnings updates append as
  `{"role": "system", ...}` messages after the cached history instead of
  editing L0 — the operator-authority channel that preserves the prefix.
  Fallback on other models: carry the learnings in the L3 user turn.
- **Known constraints to design around**: the cache is *model-scoped*
  (never switch models mid-writeup — a per-beat audit on a cheaper model
  cannot read the drafting cache); a breakpoint looks back at most 20
  content blocks (keep beats to a few blocks each, or drop an
  intermediate breakpoint); a cache entry is readable only once the
  writing request begins streaming (no parallel fan-out on a cold
  prefix); pre-warm with a `max_tokens: 0` request when resuming a
  session so the first beat after a break doesn't pay cold-write latency.
- **Verification is part of the implementation**: every call logs
  `cache_read_input_tokens` / `cache_creation_input_tokens` alongside
  the existing live-call/token report (MVP convention). A beat loop
  showing zero cache reads has a silent invalidator and should say so
  loudly.

### Illustrative economics

Assume L0+L1 ≈ 10K tokens, tail window ≈ 2K, 30 beats, current Opus-tier
input pricing ($5/MTok). Without caching the stable layers alone cost
30 × 10K × $5/M ≈ **$1.50** of pure re-reading. With the layout above:
one 2× write (~$0.10) + 29 reads at 0.1× (~$0.15) ≈ **$0.25** — an ~85%
reduction on the repeated portion, growing with beat count. Output tokens
(the drafts) dominate what remains, which is exactly P2's intent: spend
on the *decision*, not the re-reading.

## 5. Beat-by-beat algorithm

Each step names its CLI verb (`authorlm write …`); prose and plan JSON
travel over stdin. Gates marked ⛔ are enforced by the verb, not the skill.

```
0. INITIATE   write start <file> --intent <id>
              author supplies outline + placement; declare_intent stays the
              conversational front door. ⛔ style guide attached; ⛔ file
              not checked out to Google Docs; ⛔ no other active writeup on
              the file. Pins source_version_id (the old text as raw
              material), truncates the file, collects the honest 'removed'
              transition. The opening seed is just beat 1 — author-written
              (accepted without a proposal) or model-proposed (former Q5).
1. PLAN       write plan   (JSON array on stdin; --replace to amend)
              expand outline → leaf beat specs (DOC-style) conversationally;
              the author RATIFIES; the verb persists. Replace keeps written
              beats and assigns fresh stable n's to the remainder.
2. PROPOSE    write propose --why "<explanation>"   (draft on stdin)
              the skill assembles the payload (style law, concept notes,
              policies, precedents, accepted text + pinned raw material),
              drafts, and SELF-CHECKS against beat spec, style law, graph
              before registering. ⛔ --why (which concepts it realizes,
              which precedent it follows) is mandatory — verdict evidence
              hangs off it. ⛔ checkout gate. A redraft supersedes the
              pending proposal. Presented to the author WITH explanation.
3. FEEDBACK   author reacts in any wording:
                accept        → write accept            (review evidence)
                edit/reword   → write accept, their text on stdin —
                                recorded as 'modified'; draft→final diff
                                is the evidence (draft in the guidance
                                row, final in the version)
                reject+reason → write reject --reason "<verbatim>" —
                                ⛔ reason required at the tool level;
                                cursor unchanged; redraft with reason in
                                context
                revise plan   → write plan --replace (accepted text
                                untouched)
4. APPEND     inside write accept, atomically: accepted text appended to
              the file → collect (compact per-beat summary; transitions
              attach to the episode; deterministic scans only — the
              extraction pass is deferred to completion) → record_review
              (evidence + policy reinforcement + seeding) → cursor
              advances. The skill never edits the manuscript file itself.
5. INTEGRATE  write learn "<lesson>" when a pattern recurs across verdicts
              (e.g. "author tightened both openers → propose tighter");
              learnings ride forward in the drafting context; k ← k+1
6. COMPLETE   write complete → final collect + the deferred extraction
              pass; then, separately and conversationally, complete_intent
              → episode analysis → jurisdiction-routed seeding. The whole
              writeup is one richly-annotated record: per-beat
              draft → verdict → final text, reconstructable from
              guidance_history by batch_id.
              (write abandon at any point restores the file from
              source_version_id — a truncated file with a dead writeup is
              the worst end state.)
```

Interrupt/resume at any step: `write status` is the resume entry point
(writeup, cursor, current beat, pending proposal, learnings, tallies).

**Google Docs interplay.** Round trips are supported *between* beats:
push the partial chapter, hand-edit, pull — the pull collects the edits as
ordinary transition evidence, verdict rows are untouched, the cursor does
not move. While checked out, `propose` and `accept` are gated (a local
append during the checkout window would be silently discarded by the next
pull). After a pull that changed the text a pending draft was conditioned
on, the skill re-proposes rather than letting a stale draft be accepted.

## 6. Learning-loop integration

**Inputs (existing capabilities conditioning every beat):**
- Style system: `get_style(file)` rendered effective guide = L0 law
- Concept Graph: ratified notes/edges/aliases for target concepts;
  refuted positions as hard negative constraints (L1/L3)
- Policies: validated policies in scope (L0); candidates never constrain
  drafting — they surface only as questions
- Episodes: `find_precedents` for the chapter's graph neighbors (L1)
- Plan/TOC: `get_plan` placement logic seeds beat expansion

**Outputs (evidence flowing back out):**
- Each beat verdict is a review: accept/reject/modify with reasoning
  verbatim → the `review_suggestion` pathway (policy reinforcement).
  Beat-level granularity is the payoff — a 15-beat chapter yields ~15
  recorded judgments where the one-shot redo of rebirth.md yielded one.
- Author rewrites are diffs: draft → accepted text, attached to the
  episode via the normal collect; episode analysis at completion mines
  them; the jurisdiction router (auditors-requirements R5) classifies
  the patterns at seeding time.
- The learnings list is *session-local and disposable* — it never
  writes to the style/policy stores directly. Recurring learnings are
  exactly what episode analysis should promote through the normal
  ratification path; learnings are scaffolding, not memory.
- Auditors (once built) run on each collect in step 4, per their normal
  triggers — the writeup adds no special audit machinery.

## 7. Author workflow

**Initiation contract.** The author starts a writeup by supplying:
1. an **outline** of the piece (any granularity; prose or bullets),
2. an **opening paragraph** — the author's own when they have one (it
   anchors voice concretely, over and above the style guide, and becomes
   the first accepted beat); when not provided — notably in revision
   mode, where the existing accepted text already anchors the voice —
   the model *proposes* the chapter's first words as an ordinary beat,
   gated like any other (ratified by the author 2026-08-02, resolving
   former open question 5),
3. the **placement** in the manuscript: position in toc.md.

**The TOC prefix defines the continuity contract.** Everything before the
placement point is settled context; the system derives from it:
- *Style continuity*: the file's effective guide (attachment or
  inherited); an unattached file blocks initiation until the author picks
  a guide (cf. auditors-requirements R2.4).
- *Concept continuity*: concepts realized in TOC-prior files are
  **available** — citable and buildable-upon using the author's ratified
  definitions, without re-introduction. Concepts introduced only in
  TOC-later files are **unavailable** — a beat needing one must forward-
  reference ("as a later essay will show"), never assume it; the
  prerequisite-gap rules (with their policy-based suppressions, e.g. the
  preface-window policy) apply per beat, not post hoc.
- *Voice continuity*: precedents and the running digest of the
  immediately preceding chapter's close, so openings can echo or pivot
  deliberately.

**Session rhythm.** Initiation and plan ratification are conversational;
the outcome is persisted by `authorlm write start` / `write plan` (§5).
The beat loop is propose → react, at the author's pace — reactions in
plain words (accept / reword inline / reject-with-reason), recorded by
the corresponding verb.
Doc-bridge round trips stay possible between beats: the author can push
the partial chapter to Google Docs, hand-edit, pull — the pull is just a
bigger step-3 edit, and the loop resumes with the pulled text as L2.

## 8. LLM APIs for token efficiency in this architecture

- **Reference target: Anthropic Messages API** with explicit
  `cache_control` (§4). Requirements on any provider: explicit or
  reliable prefix caching, cache-hit observability in usage metadata,
  and long-enough TTL to span author think-time. Common Core requires
  independence from any *particular* model — the layered-payload design
  is provider-neutral; only the breakpoint syntax is adapter-specific
  (AuthorLM's `llm.py` adapter owns it).
- **Model choice:** one model per writeup session, held constant — the
  cache is model-scoped, so mid-session switching forfeits the prefix.
  Default `claude-opus-5` (current Opus tier, $5/$25 per MTok, 512-token
  cache minimum). Drafting in the author's register is
  intelligence-sensitive; economy comes from caching and from
  `output_config.effort` (drafting at `high`; the same-call self-check
  adds no extra request; any *separate* mechanical calls may run at
  `low`), not from downgrading the drafting model.
- **Thinking/effort:** adaptive thinking on; effort is the per-beat cost
  lever. Note `max_tokens` caps thinking + text together — size it for
  the beat budget plus thinking headroom.
- **Batch API (50% discount) is for the offline paths only** — full-
  manuscript audits, bulk re-extraction — never the interactive loop.
- **Session resume:** pre-warm the prefix with a `max_tokens: 0` request
  (billed as a cache write, no output) so the first beat after a break is
  fast; skip pre-warming when resuming within the TTL.
- **Reporting:** every beat logs live calls, input/output tokens, and
  cache read/write tokens (MVP convention + §4 verification).

## 9. Open questions

1. Tail-window size (verbatim beats vs digest boundary): fixed N, token
   budget, or section-aligned? *(Deferred with the programmatic loop —
   irrelevant while the skill holds full context, §11.)*
2. Should the same-call self-check be trusted, or should the style/
   concept auditors run as separate cheap calls per beat (costing a
   second request but giving independent judgment)? Interacts with
   auditors-requirements Q4. **Partially resolved 2026-08-02 (grilling):
   the built `write propose` deliberately enforces nothing about content —
   the self-check is skill discipline, because mechanical per-beat lint is
   exactly what the auditors are designed to be, and building a second
   competing home for that logic right before the auditors would be
   waste. Independent adversarial judgment remains the auditors' question.**
3. k-candidates UX: when the author asks for alternatives, present
   side-by-side or sequentially? Are rejections of non-chosen candidates
   full-weight evidence or weaker? *(Still open; expected to bite in
   fresh-drafting mode — treat the next writeup as data-gathering.)*
4. ~~Mid-chapter surgery~~ **Resolved 2026-08-02 (grilling): a full
   rewrite of an existing essay is fresh-drafting mode** — the old text
   is pinned as `source_version_id` raw material, the file is truncated,
   and beats append. Truncate-and-rebuild is what a rewrite *is*; the
   mid-session "concepts temporarily unrealized" noise is honest, whereas
   an observed sibling file would make the graph believe claims exist
   twice. In-place revision mode (targeted edits at arbitrary positions)
   remains unbuilt — it waits for a use case (§11).
5. ~~Does the opening-paragraph requirement generalize?~~ **Resolved
   2026-08-02** during the first manual trial (rebirth.md bridge): the
   author-written seed is preferred but optional; absent one, the model
   proposes the first words as a gated beat (see §7.2).
6. ~~Where does the ratified beat plan live?~~ **Resolved 2026-08-02
   (grilling): in the DB, as the `plan` JSON column on the writeup row
   (§2)** — not a session artifact file (no extra docs floating around),
   and not `get_plan` integration. What integration would add — `build_plan`
   suppressing placements a live writeup already covers, completion
   cross-checking planned vs realized concepts — is real but modest, and
   deferred (§11); storing beats in the DB now does not foreclose it.

## 10. First manual trial — findings (2026-08-02)

Trial: the rebirth.md seam bridge (intent di-d82b3c3163c0, episode
ep-303a8c13ea82). Four beats proposed, four accepted unmodified; one new
concept ('Life is a trajectory') realized mid-loop by the collect.
Findings against the open questions:

- **Q3 (k-candidates):** no data pressure — k=1 sufficed 4/4 in revision
  mode, where the existing text tightly constrains the target. Expect k
  to matter for fresh drafting, not seams.
- **Q4 (mid-chapter surgery):** the entire trial WAS mid-chapter surgery
  (edits at the opening, middle, and close of an existing essay). For
  the built version this means **revision mode cannot ride the
  append-only rolling transcript** — its beats target arbitrary
  positions, so either each revision beat re-assembles the payload
  (fresh L2 from the file, no conversation-cache accrual) or the loop
  accepts tail invalidation per beat. Revision mode and fresh-drafting
  mode have different cache profiles; design them as two modes of one
  loop.
- **Q6 (plan artifact):** conversational ratification was sufficient at
  4-beat scale; a persisted plan artifact is a >~8-beat concern.
- **Q2 (self-check):** implicit same-call checking only; never
  adversarially tested. Still open.
- **Q1 (tail window):** no data — manual mode holds full context.
- **Operational, not in the original design** (all three addressed in the
  build, 2026-08-02): (a) initiation must gate on Google-Docs checkout
  state — the trial hit an expired OAuth token and a checked-out file
  before the first append. *Built: the checkout gate is enforced on
  `write start`, `propose`, and `accept` (the propose/accept gates close
  the push→pull window, where a local append would be silently discarded
  by the next pull).* (b) Per-beat collection should use the compact
  path — the MCP collect_revision dump (~68KB with gaps_before/after) is
  exactly the R6.3 anti-pattern. *Built: `write accept` prints a compact
  summary (version, transitions, realizations, gap delta only).* (c) Beat
  verdicts had no recording channel of their own. *Built: beats are
  guidance rows (§2); verdicts flow through `record_review`.*

## 11. Implementation status (2026-08-02)

Convention: anything not listed as implemented here is **not
implemented**. See the grilling session of 2026-08-02 for the reasoning
behind each line.

**Implemented** (e2e-tested in `tests/test_e2e.py`, Scenario W):

- `writeups` table (§2) and the CLI verbs `authorlm write
  start | plan | status | propose | accept | reject | learn | complete |
  abandon` (§5), fresh-drafting mode only.
- Beat-verdict channel: proposals as `guidance_history` rows
  (`kind='beat'`), verdicts through `record_review` — evidence, policy
  reinforcement, candidate seeding from explained rejections.
- `GUIDANCE_KINDS` allowlist protecting guidance-batch queries from
  non-guidance kinds (and vice versa).
- Gates: style attachment + checkout + single-active-writeup on start;
  checkout on propose/accept; mandatory `--why` on propose; mandatory
  `--reason` on reject; abandon restores from the pinned source version.
- Config-file `api_key` fallback in `llm.py` (both providers, env wins) —
  without it, CLI-driven loops in key-less shells silently lose
  per-completion extraction and rejection-explanation distillation.
- Skill alignment: the beat-loop section of
  `.claude/skills/authorlm/SKILL.md` (drafting discipline, self-check
  before propose, verbatim reasons, re-propose after a doc pull).

**Partial / behavioral notes:**

- Per-beat collects run the deterministic pipeline only (transitions,
  realizations, gap delta); the LLM extraction pass is deferred to
  `write complete`. Rationale: one LLM call per accept adds latency for
  little — realization scanning (which conditions gap analysis) is
  deterministic anyway, and extraction is incremental at completion.
- `write complete` does not require the plan to be exhausted (the author
  decides when done); unwritten beats are reported.

**Deliberately not built (YAGNI — waiting on a use case or a dependency):**

- **Revision mode** (`mode` column reserves the name): targeted edits at
  arbitrary positions in existing text. Fresh-drafting covers rewrites
  (§9.4); build revision mode when a real revision session demands it.
- **k-candidates** (`--candidates k`, §1): no data pressure yet (§10 Q3).
- **Auditors as per-beat checks** (§5 step 2's independent judgment):
  waits for auditors-requirements; `write propose` intentionally carries
  no content lint so the auditors have one home.
- **Tail window / running digest** (§2, §3 L2): skill mode holds full
  context; these belong to the programmatic loop.
- **Programmatic cache layer** (§4 mechanics, §8): `llm.py` has no
  `cache_control`; skill mode gets the economics free (§4 assessment).
- **`get_plan` integration** (§9.6): placement suppression and
  planned-vs-realized cross-checks.
- **MCP write tools**: the loop is CLI-only by design — one call surface
  for the skill; improving the verbs improves every session without
  touching the skill.

## 12. Essay-summary integration — recommendation and current state (2026-08-27)

Added by the V/summary-scaling & V/summary-tests remediation pass
(`authorlm/summaries.py`, `authorlm/prompts/summarizer.md`), answering a
Sponsor request: whether per-paragraph summaries would beat per-essay ones,
and what machinery is missing before an autoregressive-drafting trial. This
section is **additive** — nothing above is edited. Where a claim above
would mislead a reader given what's built now, that is called out
explicitly below rather than folded silently into the older text.

### 12.1 What changed under this document since 2026-08-02

Four facts about the machinery this design depends on, or sits next to,
changed since this document was last touched. None contradicts a specific
claim above, but a reader relying on this doc as current should know:

- **Essay summaries now scale to the unit's length**
  (`summaries.target_length`): roughly n/10 to n/7.5 words, floored at 40,
  capped at 500 — e.g. ~150-200 words for a 1500-word essay, ~300-400 for
  a 3000-word one. Previously a fixed 150-250 words regardless of essay
  length.
- **Paragraph coverage in essay summaries is now VERIFIED, not merely
  requested.** `summaries.paragraph_coverage` checks that every paragraph
  a summary was asked to cite (numbered `[n]` markers in the prompt input)
  was actually cited in the output, and reports `missing` /
  `out_of_range` / `complete`. Informational only — it never blocks a
  rebuild.
- **`critique resolve` no longer reads the Doc destructively** (X7-1v2):
  it exports the Doc tab as markdown and three-ways it against local,
  refusing to guess and surfacing a genuine two-sided edit as a conflict
  instead of silently resolving it. Relevant here because §5 step 6 and
  the Doc-bridge description (§5, §7) both lean on Doc reads as trustworthy
  raw material — that assumption is now a tested guarantee, not an
  aspiration.
- **Belief promotion is now gated on distinct-session, non-system
  evidence** (INV-2): a machine-authored conjecture can seed a candidate
  policy but can never validate one by itself. This bears directly on §3's
  L0 layer ("validated policies in scope") and §6's "candidate-policy
  seeding from rejection explanations" — the seeding path §6 describes was
  always meant to feed *human* ratification; INV-2 is the enforcement of
  exactly that boundary, not a new rule this design didn't anticipate.

### 12.2 Is essay-summary compression already part of this loop? Corrected finding

**No — precisely: the compressed-context machinery (`essay_summaries`,
`summaries.py`) is wired into EDITING an essay and not into WRITING one.**
(An earlier draft of this finding said summaries were "wired into
nothing"; that was imprecise and the coordinator caught it — the corrected
version below is the load-bearing claim of this section.)

Verified against every consumer, not asserted:
- `passes.py:182` (`summaries_ready`) calls `sums.before_after` and
  **gates** the critique edit pass on it — a stale summary genuinely
  blocks `build_context` (proven by regression test,
  `tests/test_passes.py`). Essay summaries are load-bearing **for
  editing**.
- Every other production consumer of `summaries.py` — `mark_changed`,
  `rebuild`, `rebuild_one`, `status`, `units` — writes or reports
  summaries; none of them reads a summary *into* a decision about what to
  draft.
- The beat loop's drafting payload (§3's L1/L3 layers, assembled per §5
  step 2 and `.claude/skills/authorlm/SKILL.md`'s "Propose" step — "style
  law, concept notes, policies, precedents") consults none of
  `essay_summaries`. `write_start` / `write_plan` / `write_propose` /
  `write_accept` / `write_complete` (`api.py`) contain no reference to
  `summaries.py`.

This is smaller and more specific than "wired into nothing": the plumbing
*pattern* a drafting loop would need — gate on freshness, read
`before_after` for compressed prior-essay context — already exists and is
tested in `passes.py`. It needs to be **followed into the write path, not
invented.**

One adjacent distinction worth being explicit about, since it is easy to
conflate: §3's L2 "running digest" (accepted-text-so-far digest for the
essay *currently being drafted*) is a different, still-unbuilt concept
(§11: deliberately not built, skill mode holds full context instead) from
`essay_summaries` (compressed context for *other* essays, built and used
by editing). Neither is currently consumed by the write path; they solve
different problems and neither should be assumed to stand in for the
other.

### 12.3 Would per-paragraph summaries be better? No.

There are already two autoregressive chains in this codebase, at two
different grains, and they should not be merged into one:

- The **beat loop** is already autoregressive at the paragraph level,
  conditioned on the *verbatim* accepted text so far (L2) — correct,
  because while drafting an essay you want the real sentences, not a lossy
  compression of them.
- **Essay summaries** compress *other* essays, a book-level concern, not a
  paragraph-level one.

Per-paragraph summaries would serve both grains at once and serve each
worse:
- **They would cost more than they save.** At this manuscript's scale (24
  essays, ~300 concepts, essays running ~1,400-4,500 words / ~10-30
  paragraphs each — 400-600 paragraphs total), a "before/after" context
  built from individual paragraph summaries would, by essay 20, be
  summarizing 350+ paragraphs — plausibly more total tokens than the
  ~150-500-word per-essay summaries this pass just made length-proportional.
  The compression that makes "a few thousand tokens" (the module docstring's
  own target) tractable comes specifically from summarizing at the essay
  grain.
- **Staleness tracking gets much harder for no real payoff.** The current
  model works because `source_hash` is one hash of the whole essay and
  `mark_changed` only reasons about file order. Paragraph summaries would
  need the same freshness machinery *per paragraph*, and paragraph
  boundaries shift on every edit — inserting one paragraph near the top
  renumbers everything after it.
- **The actual paragraph-level problem this task raised — "don't silently
  drop a paragraph" — is already solved without a second summary tier.**
  Numbered-paragraph citation plus `paragraph_coverage`'s verification
  (§12.1) gives paragraph-level accountability *inside* one essay summary.
  That is what "capture every paragraph" asked for; it does not require a
  paragraph to have its own summary row.

**Addendum, 2026-08-28 (the Z build pass): read the coverage figure
narrowly.** `paragraph_coverage` now parses the cross-bracket range the
real summarizer actually writes (`[1]-[11]`, not just the prompt's
in-bracket `[2-3]`) — before that fix the metric slandered its own
summaries, reporting 350+ paragraphs uncited across 18 of 24 essays
where the true figure is 11 of 1,138. But the corrected parser buys a
weaker guarantee than it looks: a single blanket range like `[1]-[30]`
satisfies `complete` on its own. So a complete reading means "no
paragraph fell outside a cited span", **not** "every paragraph got its
own move in MOVES". The drafting trial below should not over-trust the
number — a high coverage rate is necessary, not sufficient, and the
qualitative half of §12.5 (does the redraft miss anything load-bearing?)
is what actually tests whether the compression holds.

**This is a measurement gap, not an opinion gap.** `paragraph_coverage` is
tested against stub responses (this remediation pass is barred from live
billed LLM calls), so there is no real coverage rate yet against actual
essays and a real model. If coverage against real essays turns out
reliably complete, per-essay summaries with verified coverage are
sufficient. If it turns out routinely incomplete even after prompting for
it, that would be real evidence for finer grain — but the fix to try first
is a bigger length budget or a stricter prompt, not a new schema and a
second staleness state machine.

### 12.4 What's missing before a drafting trial (dependency order)

The beat loop itself (§11: writeups table, all six `write` verbs, the
guidance-history evidence channel, the gates, skill alignment) is real,
built, and should **not** be rebuilt. What's missing is specifically the
connection identified in §12.2:

1. **A function that turns `before_after(file)` into L1 text.** Nothing
   calls it from the write path today (§12.2). Small — glue, not a new
   subsystem — living near `summaries.py` or `passes.py`.
2. **A freshness gate on `write_start`**, mirroring
   `passes.summaries_ready`. If essay summaries become drafting input, a
   stale one is a lie about the text there exactly as much as it is in the
   critique pass.
3. **A placement-aware `before_after`.** `sums.units()` reads the
   *committed* TOC; a brand-new essay not yet placed raises `LookupError`
   from `before_after`. Fresh-drafting mode today covers rewrites of
   existing files (§9.4), not essays with no TOC entry yet.
   **Correction, 2026-08-28 (the Z build pass): the premise above is
   wrong, and the truth was worse.** An unplaced essay did *not* raise —
   `structure.reading_order` **appends** an on-disk file that toc.toml
   never mentions, so `before_after` returned the entire book as settled
   context *before* it and nothing after: §7's continuity contract
   exactly inverted, silently, for the one file whose placement actually
   matters. `LookupError` was in practice unreachable from the write
   path at all, because `write_start` resolves its argument through
   `_resolve_relpath`, which only matches files that exist on disk.
   What is built now: `before_after(..., placement=)` takes either the
   name of the unit the essay follows or the `PLACEMENT_START` sentinel,
   surfaced as `write start --after <file> | --after start` and
   persisted in the writeup's metadata so `write status` recomputes the
   same split on resume; and, with **no** placement, an unlisted essay
   is **refused** — naming both remedies (a toc.toml entry, or
   `--after`) — because an appended position is a fallback, not a
   declaration, and guessing it silently is the bug this item exists to
   close.
4. **A decision on whether incomplete coverage should block drafting.**
   `paragraph_coverage` is informational-only by design for editing (an
   editor can still use a summary short one transitional paragraph). That
   tolerance may be wrong when a summary is the *only* thing the next
   essay is conditioned on — a Sponsor judgment call, not a mechanical one.
5. **Respect the concurrent `status` column** (deprecated essays, landing
   from a parallel stream) in whatever eventually reads `essay_summaries`
   for drafting context — check this dependency before building item 1.

### 12.5 The smallest experiment

Before building any of §12.4: run `summarize rebuild --all` once for real
coverage numbers (≈24 cheap-tier calls — the first real measurement of
§12.3's open question), then hand-assemble one existing essay's
`before_after` summaries plus its concept slice and style guide, and ask
for a from-scratch redraft with no peeking at the real text. Have the
author judge it against the original: does it respect what came before,
does it miss anything a human would consider load-bearing. Clean coverage
numbers plus a usably-close redraft is the strongest signal to greenlight
§12.4 items 1-2 for real; a traceable miss points at a specific paragraph
(via `paragraph_coverage`) rather than a vague feeling, and the fix to try
first is still inside the per-essay design (§12.3). No new schema, gate,
or CLI verb needed to run this.

## 13. Two author use cases on the loop — new essays and modeled rewrites (2026-08-29)

Added by the use-case expansion pass, answering two requests from the author:
write a new essay from a one-paragraph brief, and rewrite an existing essay as
a *modeled* rewrite that accounts for every point in the original. This section
is **additive** — nothing above is edited. Where a claim above would mislead a
reader given what is built now, that is called out explicitly here rather than
folded silently into the older text.

Neither use case is a new mode. Both are `mode='fresh'` (§2), distinguished by
what the writeup's metadata holds: a `brief` with `created_file` is a new
essay; a `digest` is a modeled rewrite. Revision mode remains unbuilt (§11).
Nothing here adds an LLM call to the write path: the conversational agent does
all drafting, extraction, and accounting prose; the verbs stay deterministic
state, gates, and evidence (§0's skill-mode statement, unchanged).

### 13.1 UC-A — a new essay from a one-paragraph brief

`write start` learns to target a file that does not exist yet:

```
write start <name>.md --new --intent <id> --after <file> --style <guide>
                                                        (brief on stdin)
```

- **`--new` is explicit** because `docs._match` resolves by unique
  case-insensitive substring: without the flag, a typo would silently become a
  new essay. `--new` on a name that already resolves is refused.
- **The brief travels on stdin and is required with `--new`**, for the same
  reason `write plan` and `write learn` take stdin (prose in an argument is a
  quoting disaster) and because a brand-new file has *no pinned raw material*.
  The brief is the only essay-specific grounding the beats have; without it the
  loop is asking a model to invent commitments, which is what §12.5's
  experiment did. It is persisted in the writeup metadata and reprinted by
  `write status`. The missing-brief refusal is the **last** of the flag
  gates — after `--new` consistency, the intent, the style guide and the
  placement, and before the summary-freshness gate. Every other refusal
  names a flag the author can append to the command they just typed; this
  one asks them to go and compose a paragraph, and a gate parade typed at
  a terminal has no stdin at all, so checking it first would mask every
  other refusal behind "give me a brief".
- **"Proposed by the drafter loop" is conversational.** The skill may draft a
  candidate brief; the author ratifies the wording; the ratified wording is
  what reaches `write start`. No verb, and no LLM call in the write path.
- **Placement is required**, and mostly already enforced: a file that does not
  exist is unlisted once created, and §12.4 item 3's refusal covers unlisted
  files. The refusal fires *before* the file is created, so a blocked start
  still leaves the disk untouched.
- **Style is attached at start with `--style`.** `style attach` cannot be run
  first: `api._validate_file` requires the file on disk, deliberately (there is
  no files table; integrity is enforced against disk at the point of entry).
  The guide name is validated before anything is created; the attachment
  written afterwards is an ordinary `style_attachments` row.
- **The pin is the pre-writeup state, which does not contain the file.** That
  is what makes the restore target honest: `write abandon` on a writeup that
  created its file **deletes the file**. The pre-collect that already guards
  BUG-2/A1 runs first, so whatever prose was in it survives in version history;
  the subsequent collect records the removal, and any concept whose primary
  location was that file raises a `vanished` proposal — the same honest noise a
  truncate-and-rebuild produces (§9.4).
- **`write complete` registers the toc entry.** A finished new essay with no
  `toc.toml` entry can never be summarized in position, so the next
  `critique run` or `write start` near it is refused for a file the author
  believes is done. Completion therefore inserts the stanza textually after the
  placement anchor (carrying the anchor's `parent`, never guessing `matter`),
  preserving comments and unknown attributes; if the anchor cannot be found it
  prints the stanza to paste and does not fail.

### 13.2 UC-B — a modeled rewrite, with removal accounting

A modeled rewrite is §9.4's fresh-mode-over-an-existing-file — already built —
plus a digest, dispositions, and a report.

**`write digest`** is the deterministic channel to the pinned original:

- with no stdin it **prints** the pinned source text (the file on disk is
  truncated; the old essay lives in `source_version_id`);
- with JSON on stdin it **persists** a structured digest to the writeup
  metadata: `points`, `examples`, `references`, `inconsistencies` (each
  contradiction naming the TOC-earlier essay it conflicts with);
- with `--dispositions` it **merges** per-point dispositions;
- with `--show` it prints the stored digest and the accounting tally;
- it is refused outright on a writeup that created its file — there is no
  source essay to digest.

Every item carries a **stable id**, unique across the whole digest, because the
removal accounting references them. Cross-references (`serves`, `points`) must
resolve to real point ids, and `inconsistencies[].with` must resolve to a real
manuscript file; a dangling reference is refused rather than stored, since an
accounting built on dangling ids is decorative. `--replace` is allowed but may
not drop an id that already carries a disposition.

The digest is produced by the skill and **reviewed by the author before it is
persisted**. That review is what makes it evidence rather than a machine
reading — the same principle as plan ratification (§13.3).

**Dispositions** are `kept` or `removed`; `removed` requires a reason, for the
same reason `write reject` requires one. Two values only — the nuance belongs
in the free-text reason, which is what the removal section quotes. An optional
`beat` links a kept point to the beat that carried it.

**The "What Was Removed and Why" section is authored content**, drafted by the
skill as the final beat(s) from the *recorded* reasons, accepted by the author
like any beat, and therefore evidence like any beat. Its placement needs no
machinery: the loop appends, so the last beat lands last, which is what "after
the footnotes" means for an append-only loop (order any footnotes beat before
it in the plan). Note that this section is ordinary manuscript prose — it will
be collected, scanned, summarized, pushed, and exported. The durable record of
the accounting is the writeup metadata, so deleting the section later loses
nothing.

**`write complete` reports, and does not block.** When a digest exists it
cross-checks every point id against the recorded dispositions and prints
kept / removed / **UNACCOUNTED** — the last loudly, with the ids and their
claims — and persists the tally to the writeup metadata. It does not refuse.
This follows §11's existing principle that "`write complete` does not require
the plan to be exhausted — the author decides when done": an unaccounted point
is the same kind of open editorial item as an unwritten beat. Blocking would
also reward the cheapest escape — a bare `kept` on a point that was in fact
dropped — converting a visible gap into an invisible lie. `write status` shows
the same tally on every resume, so the report at completion is never the first
time the author sees it.

`write complete` deliberately does not check that the section exists in the
file. That is content lint, and §11 is explicit that `write propose` carries
none so the auditors have one home; the same holds here.

### 13.3 "We cannot make things up" — where each beat's facts come from

The author's constraint, verbatim, from the sitting that requested these use
cases. It is a claim about permitted inputs, not a tone.

A beat may be grounded **only** in: the author's brief; the drafting context's
BEFORE block (§12.4 item 1); the concept graph's ratified notes; the effective
style guide — and, for a modeled rewrite, the digest. The digest is the **sole
authority on what the original said**: a point not in the digest is not in the
original, even when a neighbour's AFTER summary appears to describe it (during
a rewrite those summaries can still describe the *old* version — observed in
the §12.5 runs). Reordering, compressing and sharpening are the point of a
modeled rewrite and are fully permitted; adding a claim, an example, or a
citation that is in neither the digest nor the brief is a new authorial
commitment and belongs to the author.

**Plan ratification is the guard against invented commitments.** §12.5's
blind-redraft experiment had the style guide, the concept slice and the full
before/after context — everything §12.4 shipped — and still invented an
attribution, recording in its own notes: "I do not know the real essay's actual
argument, so I built one and attributed the underlying thesis to Rieff." It
happened because 2,244 words were requested in one shot: the gap between "a
claim is needed here" and "I have no claim here" was closed at drafting time,
silently. The beat plan moves that decision upstream of the prose. Therefore:

- a beat spec's `notes` state **the claim the beat will make**, not merely its
  rhetorical function;
- every proper name, citation or quotation a beat will use is named in the spec
  (or, in a rewrite, is a digest reference id) before it appears in prose;
- `--why` on propose repeats the grounding by id;
- a beat that discovers mid-draft that it needs an ungrounded fact triggers
  `write plan --replace` or a question to the author — never an invention.

### 13.4 What is still not built

Unchanged from §11, and re-confirmed against these two use cases:

- **Revision mode** — UC-B is specifically the rewrite case that does *not*
  force it (§9.4). Seam and bridge writing (§10's first trial) is revision mode
  under another name and waits with it.
- **k-candidates (§9.3)** — still deliberately absent, but note that §10's
  prediction ("expect k to matter for fresh drafting, not seams") now has its
  test: UC-A is the purest fresh drafting the loop will see. Superseded
  proposals carry no verdict today, so k is not free; it needs
  `propose --alt` + `accept --pick` and a sibling-rejection path through
  `record_review`.
- **Essay merge / split** — a merge needs multiple pinned sources; the digest
  above is per-writeup on purpose, and a `sources` list would be an additive
  superset when the use case arrives.
- **Modeling on a *different* essay** (`write digest --source <file>`) — the
  same-file reading is what the removal-accounting requirement implies; the
  cross-file reading waits for the author to ask for it.
- **Summary rebuild at `write complete`** — completion reminds; it does not
  rebuild. `rebuild_one` marks everything downstream `upstream_stale`, a side
  effect completion was not asked for.
- **`plan.draft_stubs`** is now the codebase's only ungated LLM drafting path
  (no plan, no gates, no verdict, straight into `_drafts/`). It predates this
  loop and should be retired rather than expanded; the supported path for
  turning a plan item into prose is `write start`.

## 14. Parallel writeups — in-flight context (2026-08-29)

Added at the author's request after the first real sessions. Additive: nothing above is edited.

Two writeups on two essays, at once, is already reachable — `write start` refuses only a second
writeup on the *same* file, and `_writeup` asks for `--writeup` once more than one is active. The
author's case: essay A comes after essay B in reading order, both rewrites are open, and A's
drafting context has to say something about a B that is not there.

### 14.1 What went wrong

`write start` truncates its file, so a writeup on B leaves B empty on disk. Every context read is
driven off disk, so B's stored summary stopped matching B's text and read as `stale` — and a stale
summary is a lie about the text, which both gates refuse without a `--force`. Starting A was
therefore blocked by B, and the refusal's own remedy — `summarize rebuild` — would have summarized
the *empty file* and stored the result as fresh: a confident summary of nothing, which A would then
have been conditioned on. The annoyance was the refusal; the defect was the remedy.

The same fault had a second face. `structure.reading_order` appends an unlisted on-disk file, so a
brand-new essay created by `write start --new` appeared in every other unit's AFTER block with no
summary at all — `missing`, refused — and blocked every other writeup and every critique run until
it was finished. Both are the same mistake: **an in-flight file was being judged by its disk state,
which during a writeup is not a statement about the essay at all.**

### 14.2 In-flight context: the pinned version is the essay

A file with an active writeup contributes its **pinned** text to everyone else's context —
`writeups.source_version_id` → `manuscript_versions.files` → that file's text — never the bytes on
disk. The comparison is like with like at no cost: both the pinned version and `units()` read
through `read_manuscript_files`, so both have already been normalized the same way.

Two states join `fresh | stale | upstream_stale | deprecated | missing`:

- **`rewriting`** — a stored summary matches the pinned text. That summary is served, loudly labelled
  as the *pre-rewrite* essay.
- **`unwritten`** — the writeup created the file, so there is no pre-rewrite text and nothing to
  serve. It outranks even `deprecated`: a recreated filename's old summary describes a different
  essay, and serving it would be exactly the lie `deprecated` exists to prevent.

Both are **accepted by both gates**, which remain one predicate (`passes.summaries_ready`) with no
second code path, because two gates disagreeing about one row is how this system tells lies. When no
stored summary matches the pinned text the entry still reads `missing` / `stale` / `deprecated` and
is still refused — there is nothing honest to serve — but now the printed remedy works:
`summarize rebuild` summarizes the **pinned version's text pulled from the database**, never the
placeholder. Afterwards the entry settles at `rewriting`, not `fresh`; it may not pretend to describe
the file on disk. A `--new` file has no text to summarize at all, so the rebuild skips it and says so
rather than quietly summarizing an empty file.

The split this rests on is worth naming: `units()` stays **disk truth** — the collector, the
staleness marker and the editor's paragraph list all want the bytes as they are — while
`context_units()` is **context truth**, and only the conditioning paths use it.

### 14.3 The placeholder

`write start` on an existing file now leaves a deterministic placeholder instead of an empty file:
the author's words, `being presently rewritten`, plus a short note pointing at `write status`,
`write digest` and `write abandon`. It is visible text, not an HTML comment, because the one path
where a hidden marker fails is the one that matters — an HTML comment is stripped on the way to a
PDF, and a mid-rewrite essay would go on silently vanishing from the exported book. It is bracketed
`[AuthorLM: …]` rather than `<<…>>` because `<<` and `>>` are reserved by the pending-change
grammar, and a literal `<<…>>` in a file would make the next `diff push` of it fail with a message
about a margin thread that does not exist.

`write start --new` writes no placeholder. The placeholder marks a *truncation*, and creating a file
truncates nothing; a created file's empty window is deliberately invisible (no toc entry at start),
and other sessions see it through its `writeups` row as `unwritten`, which is the more reliable
signal anyway.

**A finished essay never contains it**, guaranteed twice over: the first accepted beat replaces it
rather than appending after it, and `write complete` refuses outright on a file that is still only
the placeholder — pointing at `write abandon`, which is what the author means when zero beats were
accepted. If the author hand-edited around the marker, completion warns loudly and does not block;
that is authored content, and §13.2's report-don't-block applies.

Everything that observes prose is taught that a placeholder is not prose: the concept realization and
primary-location scans are handed the file as empty (`concept_pattern` is case-insensitive, so a
concept named "Being" would otherwise have been marked realized in a truncated essay, silently), the
extractor's empty-chapter skip covers it, the export omits it **with a named warning** where it used
to drop a truncated essay in silence, and `doc push` / `diff push` refuse it. The one thing that does
change shape is the collected transition: a truncation now records a `rewrite` rather than a
`delete`, which is the more truthful of the two.

### 14.4 Being told

There is no flag to turn this off. At `write start`, at `write status`, and inside the payload the
skill reads, a context containing in-flight essays opens with a `!!` block naming each one and
saying plainly what the author already suspected: conditioning on the pre-rewrite text is **slightly
suboptimal** — whatever those rewrites change, this draft will not know about, and this draft will
not be in their context either. `critique run` prints the same thing in its own words. Each entry
carries its own `!!` label as well, so a reader who scrolls past the header still cannot mistake a
pre-rewrite summary for a current one.

This widens what `!!` means, from "the gate refuses this" to **"do not take this entry at face
value"** — which is what it had already come to mean in practice, since the coverage note has always
marked entries the gate accepts.

### 14.5 When the other writeup ends

Nothing new is built for this, because nothing is broken there.

**Completed:** the writeup leaves `active`, so the file is no longer in flight and is judged by disk
again. Its summary now describes neither disk nor anything else — `stale`, loudly, refused by both
gates, remedied by `summarize rebuild`, which is exactly what `write complete` already tells the
author to run.

**Abandoned:** the restore writes the pinned text back byte for byte, so the **hash round trip
closes** — the stored `source_hash` matches the restored text again, the entry stops reading `stale`,
and both gates accept it with nothing to rebuild. It does not necessarily read `fresh`: the truncation
upstream was a real change, so the neighbour is normally left `upstream_stale`, which is tolerated
(its own text did not move, only its conditioning). The drafting context is recomputed on every read,
so the neighbour's next `write status` simply stops warning. An abandoned `--new` writeup deletes its
file, which leaves the reading order and takes its `unwritten` entry with it.

*(Corrected 2026-08-29 during review: the section as first written said "reads `fresh` again", which
the test E6 could not reproduce because the fixture is realistic — the upstream truncation leaves the
restored neighbour `upstream_stale`. The test was right and this paragraph was wrong; the property
that actually matters, and is now asserted, is the hash round trip plus both gates accepting.)*

### 14.6 What is deliberately not done

- **No schema change.** `writeups` and `essay_summaries` carry everything needed; in-flight is
  derived from `status = 'active'`, and the pre-rewrite text is already pinned.
- **No locking, no ordering, no queue.** Parallel writeups are not serialized and are not meant to
  be; the author asked to be *told*, not stopped.
- **No cross-writeup awareness inside a beat.** A draft is not shown what the other writeup is
  producing. It could not be: those beats have no verdicts yet, and conditioning on unratified prose
  is the failure mode §13.3 exists to prevent.
- **No summary rebuild at `write complete`** — unchanged from §13.4, and this section does not
  become a reason to add one.
- **Four consumers are deliberately NOT blinded**, recorded here so they are not rediscovered later
  as regressions of this change (review 2026-08-29). Each one is *about* what happened on disk, which
  is exactly what the placeholder truthfully records:
  - **the collected transition itself** — a truncation records a `rewrite` ("Rewrote N → 1
    paragraph(s)"), and episode analysis quotes that transition in its OBSERVED CHANGES payload. That
    is a record of an event, not the extractor being asked to mine a marker for concepts.
  - **`sweeps.ontology`** and **`lenses.run_native`** — both read manuscript text directly. A
    mid-rewrite file will read as a near-empty essay in their reports, which is what it is; neither
    writes to the graph.
  - **`collect`'s `extract_hint` threshold** — the truncation's paragraph delta counts toward the
    "enough changed to be worth extracting" hint. Harmless: the hint only suggests running the
    extractor, which now skips the file per file (§14.7's F1 correction).
  What IS blinded is everything that would write a claim about the essay from those bytes: the
  realization and primary-location scans, the prerequisite-gap walk on **both sides of its
  before/after delta** (every collect during a writeup has a before-version that already contains the
  placeholder, so blinding one side would leave the marker deciding the subtraction), and the
  extractor — on all three of its routes into a payload (§14.8).

### 14.7 One capture per invocation (2026-08-29, after a live incident)

The author hit the remaining half of this in a real session: an essay truncated by a
parallel `write start` *just before* a rebuild read it. §14.2 answers that for a file that
is already in flight — the rebuild summarizes the pinned version. What was still open is
the window: a verb that reads the disk twice can have the manuscript change between the
two reads, and the dangerous shape is a **gate and the thing it gates reading
separately** — the gate passes on text A, a parallel session truncates an essay, and the
context is assembled from text B, which is exactly the lie the gate exists to prevent,
arriving with the gate's approval on it.

So: **one capture per verb invocation, shared by the gate and by the assembly.**
`summaries.capture()` names the snapshot; `before_after`, `drafting_context` and
`passes.summaries_ready` accept one and pass it down, and take their own when it is
omitted, so no existing caller changes. `api.write_start` took three captures (the gate,
then the drafting context's two reads) and now takes one — the target file never appears
in its own context, so the truncation the verb performs does not make the capture stale.
`passes.build_context` took the gate's capture, a separate `units()` pass for the
paragraph list the editor model edits, and a separate in-flight query; all three now read
the one snapshot.

`rebuild` already held a single snapshot taken before its first model call, and
`rebuild_one`'s double read was collapsed by §14.2's rewrite; both now say so, because the
property is invisible in the code and one refactor from being lost. The mid-run case
closes cleanly: a file that becomes in-flight **after** the snapshot is summarized from
its pre-truncation text, which is precisely the text that writeup went on to pin — so the
hash the rebuild stores is the hash the in-flight logic looks for, and the entry lands on
`rewriting` rather than `stale`.

Not done: any locking, and any re-read to *detect* that the manuscript moved mid-verb. The
capture is a consistent view, not a transaction; a verb finishes against the manuscript it
started with, and says so.

### 14.8 The extraction guard was dead code (2026-08-29, review F1)

Worth recording, because the shape of the mistake is reusable. §14.3 says the extractor is taught
that a placeholder is not prose, and the code said so too — `if not text.strip() or
is_placeholder(text)` in `extract_concepts`. It never fired once. Every payload `extraction.py`
builds is a concatenation of `=== <name> ===` labelled chunks (`_manuscript_text`,
`_section_payloads`), so a whole-string match against an assembled payload cannot succeed by
construction. The guard read exactly like a guard, passed review, and sent the marker to the model.

The fix is granularity, not logic: the check belongs where a single file's text is still a single
file's text. `extraction.is_in_flight` now carries that reasoning in its docstring, and is applied in
`_manuscript_text`'s loop, on the changed-sections units (a truncation IS a changed section, and its
"sections" are the placeholder's own paragraphs), and on the explicit-files units before
`_section_payloads` assembles them. The whole-payload check is gone; a pass whose only file was in
flight now simply has no text and ends on the unchanged `if not text.strip()`.

The test lesson is the sharper one: the original assertion — "no extraction payload in this scenario
carries the marker" — passed against the broken code, because the scenario happened to run no
extraction while a file was in flight. A guard's test has to make the guarded thing actually happen.

## 15. `write draft` — beat generation as a verb (2026-08-29)

Added by the programmatic-drafting pass, answering the author's question: "Why are the
beats drafted in the chat — shouldn't that be a verb too, and a prompt to go with
that? Otherwise we will not have reliable outputs, yes?" — and their ruling that
followed it: implement a `[writing]` config section with Fable 5 as the preferred
model for beat generation.

This section is **additive** — nothing above is edited. But unlike §12, §13 and §14,
this one contradicts standing statements in the older text, so §15.1 says exactly
which, rather than leaving a reader to discover it.

### 15.1 What this supersedes — the skill-mode paragraph, honestly

Four statements above were true when written and are now false for the drafting call
specifically. Each is superseded only in that scope; everything else about skill mode
stands.

1. **The execution-model paragraph at the top of this document** says the loop "runs
   in skill mode — the conversational agent (Claude Code, under the authorlm skill) is
   the drafting model and orchestrator", and that "the programmatic single-process
   loop this document originally specified (§4's explicit cache mechanics, §8) remains
   the spec for a *future* built version only." **The drafting model is no longer the
   conversational agent by default.** `authorlm write draft` is the programmatic
   drafting call, and §4 and §8 are the spec for something that now exists.
   The agent remains the orchestrator, the interlocutor, and the author's counterpart
   for everything conversational — plan ratification, digest review, brief drafting,
   relaying verdicts. It also remains a legitimate drafter on request (§15.6). It is
   no longer the *default* drafter.
2. **§4's skill-mode assessment** — "none of the mechanics below need reimplementing
   for the built loop", because "in skill mode the Claude Code conversation *is* the
   transcript" and "the harness applies prompt caching to that prefix automatically."
   That reasoning was correct for skill mode and does not transfer: `write draft`
   sends its own request, so it must place its own breakpoints. §4's mechanics are
   implemented, with the deviations §15.4 records.
3. **§11's "deliberately not built" list** — "Programmatic cache layer (§4 mechanics,
   §8): `llm.py` has no `cache_control`." It has it now, on the writing path only.
   Every other call in the system still has none, and none of them needs it.
4. **§13's framing** — "Nothing here adds an LLM call to the write path: the
   conversational agent does all drafting … the verbs stay deterministic state, gates,
   and evidence." The write path now has exactly one LLM call, in exactly one verb.
   `start`, `plan`, `digest`, `propose`, `accept`, `reject`, `learn`, `abandon` remain
   deterministic; `complete` keeps the extraction call it always had. The invariant
   that actually mattered — **no verb decides anything about the manuscript without
   the author** — is untouched: `write draft` produces a pending proposal and nothing
   else. Common Core P3 quarantine holds exactly as before.

What did NOT change, and is worth saying because a reader skimming the above will
assume it did: the author's loop. Accept, reword-inline, reject-with-reason. Same
verbs, same gates, same evidence rows, same review pathway, same policy reinforcement.
The draft arrives from a different place; nothing downstream of it can tell.

### 15.2 The verb

`authorlm write draft [--writeup <prefix>] [--dry-run]`, no stdin. It gates (active
writeup, not checked out, a ratified plan with a current beat, a configured
`[writing]` model, an API key for that model's vendor), assembles the layered payload
of §3 deterministically from stored state, renders the registered prompt
`authorlm/prompts/beat-draft.md`, makes one call, self-checks inside that same call
(§5 step 2), and registers the result **through the existing `write propose`** — so
the guidance row, the mandatory `--why`, supersede-on-redraft, the verdict evidence
and the policy reinforcement are byte-for-byte what they were.

`write propose` is unchanged and remains fully supported. A beat drafted in
conversation and registered by hand produces the same evidence it always did.

The payload is assembled from: the effective style guide; validated beliefs; the
drafting context (§12.4 item 1, with §14's in-flight warnings); the brief; the digest;
the ratified plan; ratified concept notes for every concept the plan names; the
accepted text so far, read from the file; and the current beat's spec, the learnings,
and the author's last verdict verbatim. Note the graph slice: `scoped_concepts(file=)`
is useless during a writeup — the file is truncated or a placeholder, so nothing
matches — and the concept notes therefore come from the beat specs' declared
`concepts`, which is another reason §13.3's plan-ratification requirement is load
bearing rather than merely disciplined.

`--dry-run` prints the payload and its per-block hashes and makes no call. It is how a
silent cache invalidator is found, and how the author audits what is actually sent.

### 15.3 `[writing]`, and why it does not fall back

```toml
[writing]
model = "anthropic/claude-fable-5"   # required; NO fallback to [llm] model
max_tokens = 8000                    # thinking + prose share this ceiling
effort = "high"                      # §8's per-beat cost lever
timeout_seconds = 600                # [llm]'s 120s fails every adaptive-thinking beat
cache = true                         # §15.4's breakpoints; the kill switch
```

An absent `[writing]` section **refuses the draft** and prints the four lines to
paste. It does not fall back to `[llm] model`, which is `gemini/gemini-2.5-flash`:
falling back would put the manuscript's prose in a cheap-tier register, and the author
would attribute the result to the loop rather than to a line of configuration they
never wrote. §8 already ruled that drafting is intelligence-sensitive and that economy
comes from caching and effort, not from a smaller model. The refusal names
`write propose` as the escape hatch, so a missing section can never make the loop
unusable.

One drafting model per writeup (§8, because the cache is model-scoped and voice should
not have a seam). The first draft records the model in the writeup's metadata; a
different configured model on a later draft **warns loudly and proceeds** — the same
report-don't-block rule §13.2 applies to the removal accounting.

### 15.4 Caching, as built

§4's reference layout, with two deviations recorded honestly.

Breakpoints, `ttl: "1h"`, on the two stable layers: end of the system block (prompt
file + style law) and end of the first user block (beliefs + drafting context + brief
+ digest + plan + concept notes). The accepted text and the beat-local layer follow
uncached. Two of four breakpoints used.

**Deviation 1: no rolling-tail breakpoint.** §4 specifies a third breakpoint on the
most recently accepted turn. On this manuscript's measured payload it is worth about
$1.10 on a 30-beat chapter — 11% of the available saving, for the only part of the
layout where the 20-content-block lookback and per-beat invalidation become live
concerns. Reserved, not refused; revisit if a measured chapter shows the tail
dominating.

**Deviation 2: no growing transcript.** §4 describes the multi-turn pattern, each beat
appending an assistant and a user turn. `write draft` instead re-assembles the whole
payload from stored state on every call. The cache economics are identical — the cache
is a prefix match over rendered tokens, and the stable blocks are byte-identical either
way — while re-assembly avoids storing assistant turns (persistence §11 declined) and
avoids a second source of truth about accepted text competing with the file. It also
makes a writeup resumed a week later produce the same payload it would have produced
at beat 1, which a transcript cannot promise.

Measured on this manuscript (24 essays; the drafting context alone is ~21.5K tokens,
71% of the stable payload), a 30-beat chapter at 1.4 calls per beat over three
sittings, on Fable 5 at $10/$50 per MTok with cache writes at $20 (1h) and reads at
$1: **$20.62 uncached, $10.93 cached** — 68% off input spend, 47% off the total. The
1h TTL pays for itself after 2.2 reads; a sitting makes about 14, which is §4's
author-think-time argument confirmed rather than assumed.

Every draft logs live calls, input and output tokens, and cache read/write tokens.
A draft that is not the writeup's first, on a caching-capable model, that reads zero
cached tokens prints a loud warning naming the stable layers and pointing at
`--dry-run`: per §4, a beat loop showing zero cache reads has a silent invalidator and
must say so. One such invalidator was found and closed during design: validated
beliefs must be sorted by statement with no confidence printed, because confidence
moves on every verdict and would have re-ordered the cached layer between every pair
of beats.

### 15.5 Failure is loud and leaves nothing

`write propose` is the last statement of the success path. A disabled LLM, a missing
key, a provider error after retries, a model refusal (`stop_reason: refusal`), a
truncated reply, an unparseable reply, or an empty draft all raise before it. **No
failed draft can leave a pending proposal**, and each failure says what happened and
what to do about it.

The model has a legal way to decline. §13.3 says a beat needing an ungrounded fact
"becomes a question to the author, asked before `write propose`" — the prompt makes
that a reply shape: `BLOCKED` plus `QUESTION`. Nothing is registered, the cursor does
not move, and the question is printed. That is §12.5's observed failure — a model that
invented an attribution rather than admitting it had none — given somewhere to go.

### 15.6 What is deliberately not done

- **k-candidates (§9.3)** — still absent, though `write draft` makes it nearly free
  (the payload is assembled; a second call is one line). It still needs
  `propose --alt` + `accept --pick` and a sibling-rejection path through
  `record_review`, and there is still no data pressure. Unchanged from §13.4.
- **Pre-warming (§4, §8)** — no `max_tokens: 0` request on resume. The first beat
  after a break pays one cold write, a few seconds and about $0.60.
- **Auditors as separate per-beat calls (§9.2)** — the self-check remains same-call,
  and `write draft` enforces nothing about its content, exactly as `write propose`
  enforces nothing. The auditors keep their one home.
- **Caching anywhere else** — extraction, summaries, the critique editor and the
  triage analyzer all keep `complete()` unchanged. Their payloads are not layered and
  are not re-sent, so breakpoints would buy nothing.
- **Automated coverage of the cache path** — the hermetic suite runs against the
  OpenAI-compatible stub and the live-optional test runs on the cheap tier, so no test
  exercises `cache_control`. The usage line is its only verification, which is why the
  logging and the caching shipped together.
- **`plan.draft_stubs`** — still the codebase's other ungated LLM drafting path, and
  now with less excuse than ever: `write draft` is what it should have been. Retire it.

### 15.7 Reconciliations recorded at implementation (2026-08-29)

Dated notes, not silent edits to the text above. Each is a place where the design as
written met the code as it actually landed.

1. **D6 was already fixed on the base.** The design's change 24 (re-resolve `api_key`
   after overriding `model`) landed at rc/25: `LLMClient.api_key` is now a *property*
   resolving `vendor_key(self.model, …)` on every access. `writing_llm` therefore only
   sets `.model`; there is nothing left to re-resolve, and the writing vendor's own
   variable is what gets sent. The design's §5 change 1 is satisfied by construction.
2. **The key gate fires only for a KNOWN vendor prefix, and resolves the key the way
   the REQUEST does.** §1.1 gate 7 says "an API key resolves for the writing model's
   own vendor". A model string with no vendor prefix (a proxy or a local endpoint) has
   no environment variable to name, and `llm.py` already treats an unlisted prefix as
   "let litellm resolve it" — so the refusal fires only when `VENDOR_KEY_ENV` knows the
   prefix. What it then checks is `vendor_key(model, [llm])`, not the vendor variable
   directly: `[llm] api_key_env` outranks the vendor convention there, because an
   OpenAI-compatible proxy uses an arbitrary bearer token no convention can derive.
   Reading the vendor variable directly refused a correctly configured proxy the moment
   it was pointed at an `anthropic/*` model string, and told the author to set a
   variable their setup does not use. The gate also runs *inside* `draft()`, after the
   replay-cache check rather than before it — a replayed fixture needs no key, and a
   gate in front of the cache would have made the drafting path the one thing in the
   record/replay suite nobody could run. It is still strictly before any live call.
3. **`cache_control` is gated on the anthropic vendor as well as on
   `supports_prompt_caching`.** `supports_prompt_caching` is also true for
   `gemini/gemini-2.5-flash`, which is the live-optional suite's model; attaching
   Anthropic block grammar there would have exercised a path the design explicitly
   does not claim. `caching_available()` requires litellm transport **and** an
   `anthropic/` prefix **and** the cost-map flag.
4. **The usage line's cache clause prints on every draft, zeros included.** §5 change 3
   said the clause appears "when the counters are non-zero"; §6.1's T6 requires the
   stub run to read `cache 0 read / 0 written`. The clause is therefore keyed on a
   `draft_calls` counter, so every other caller's line is byte-for-byte unchanged and
   the drafting line always reports the number §4 asks the author to watch.
5. **`LAST VERDICT` reads the review, not the guidance state.** §1.3 named states
   `reviewed`/`rejected`/`superseded`; the schema's actual verdict states are
   `accepted`/`modified`/`rejected`, and the author's reason lives in
   `editorial_reviews.explanation`. The block joins the two tables and prints
   `<decision> on beat n=<k>: <reason verbatim>`, with no ids and no timestamps.
6. **A `<<` or `>>` in the drafted prose is a parse failure.** §1.5 forbids the
   markers in the prompt but did not say what to do when one arrives anyway. Since
   `gdocs.diff_push` raises on any paragraph containing `<<`, a draft carrying one
   would poison the next surgical Doc push — so the parser refuses it, nothing is
   registered, and the message names the reason.
7. **`--dry-run` runs every gate, including the `[writing]` ones.** §1.1 says every
   gate runs before any model call and §1.3 says `--dry-run` makes none; the simplest
   reading of both is that the gates are not skipped. A dry run on an unconfigured
   `[writing]` therefore prints the same refusal a real draft would.
8. **The seam is shown from `drafting_models`, and the warning counts
   `drafting_beats`.** Both are writeup metadata (§1.7's "no schema change, no index").
   `write status` and `write complete` print the list whenever it is non-empty, so a
   two-model writeup announces its seam on every resume.

### 15.8 Hardenings from review (2026-08-29)

Four more, from the implementation review. Each closed a hole that the tests as
first written did not discriminate.

1. **`BLOCKED` refuses only when it LEADS.** The parser matched the label anywhere,
   so a beat whose prose contained a bare `BLOCKED` line — a plausible thing for
   prose to do — was read as a refusal and silently discarded. It is now a refusal
   only when no `DRAFT` label precedes it. Both readings stay fail-safe: a leading
   `BLOCKED` registers nothing, a trailing one is manuscript text the author still
   rules on.
2. **`drafting_model` is written AFTER `write_propose` returns.** Registration is the
   last thing that can fail, so it goes first among the writes. Recording the model
   before it would leave a writeup claiming to have been drafted by a model on a beat
   that was never registered — residue about work that did not happen. The
   model-change warning is unaffected: it is computed from the metadata as it stood
   before the call.
3. **The zero-cache-reads warning is asserted positively.** It was free to delete with
   the suite still green, which left the caching feature's only production
   verification itself unverified. The predicate is now `api.cache_cold`, named and
   unit-tested in all four of its conditions, and the printed `!!` text is pinned by
   driving `cli._print_draft_usage` directly.
4. **`finish_reason` is tested, not merely implemented.** A provider refusal and a
   truncation both arrive as HTTP 200 with a body; nothing but `finish_reason`
   separates either from a beat. Both are now exercised through the stub and asserted
   to leave no row, no cursor move and no file change — and to say *different* things,
   because the remedies are nothing alike. With the classification removed, a refusal
   registers silently as a beat, which is risk R-c happening.

### 15.9 `plan.draft_stubs` retired (2026-08-29)

§13.4 and §15.6 each flagged `plan.draft_stubs` as the codebase's other ungated
LLM drafting path and said it should go. It is now removed, sponsor-ruled: the
cheap `[llm]`-model stub drafter that wrote opening passages straight into
`_drafts/` with no plan, no gates, no verdict, and no style law. `write start`
+ `write draft` (§15) is the supported path from a plan item to prose, and has
been since this section's own §15.1. Removed with it: the `OPENING_DRAFT_SYSTEM`
prompt constant, the `plan --draft` CLI flag, `api.get_plan`'s `draft=`
parameter, and the MCP `get_plan` tool's `draft=` parameter. `plan.build_plan`
(deterministic placement) is untouched, and so is the generic underscore-dir
observation-ignore behavior that `_drafts/` happened to rely on — that
mechanism is not specific to this feature and stays.

### 15.10 The billed drafting path is off by configuration (2026-08-30)

Sponsor ruling, on seeing the first month's API charges: *"I didn't realize it
would be that expensive… I don't want to be hit with big dollars."*

`[writing]` is removed from the shipped `authorlm/config.toml`, and with it the
two `[critique]` Sonnet overrides of 2026-08-29 (§15.7's siblings; the
summarizer and the editor both billed ~10¢+ per essay per rebuild). Nothing in
the tracked configuration now names an `anthropic/` model, so no Anthropic-billed
path remains. Summaries and the critique editor fall back to `[llm] model`
(`gemini/gemini-2.5-flash`).

**No code is reverted.** The verb, `prompts/beat-draft.md`, the payload
assembler and §15.4's cache layer all stay, dormant and restorable by config.
§15.3's no-fallback rule is what makes this work: it was built so an absent
`[writing]` could not quietly degrade the prose to a cheap-tier register, and it
turns out to be exactly the shape of a kill switch. `write draft` (without
`--dry-run`) refuses, by design, and its refusal names the TOML to paste.
Both restore recipes live in the config file's own comments, where the author
looks, rather than only here.

**The default drafting flow is now the dry run, fed into the conversation.**
`write draft --dry-run` assembles and prints the payload — the same blocks, the
same checksums, the same registered prompt — and the assistant drafts the beat
against it, then registers through `write propose --why …`. This is not
§15.1's superseded "skill mode": the machinery assembly is still the verb's, and
still deterministic and reproducible; only the generation moved. What is lost is
the per-beat reproducibility of a pinned model, which is what restoring
`[writing]` buys back.

**One reorder came with the ruling.** `api.write_draft` ran the `[writing]`-model
gate before the `--dry-run` return, so with the section gone the dry run refused
too — the kill switch withheld the audit instrument along with the call.
Reviewer adjudication #7 had accepted that ordering and noted the reverse was a
small change "if the author complains"; this ruling is that complaint. The gate
now sits **below** the dry-run return. Assembling and printing a payload needs
neither a model nor a key. The dry run reports the configured model when
`[writing]` names one and says so when it does not, and it names the registered
prompt file — which the conversational flow drafts under. Every gate *above* the
return still runs on a dry run (writeup status, checkout, a ratified plan and a
beat to draft, `[llm] enabled`), so a dry run on an unplanned writeup still
refuses rather than printing an empty payload; the vendor-key gate was already
below it, inside `draft()` after the replay-cache check (§15.3's note); and
nothing mutating moved — the dry-run return still precedes `on_start`, the call,
and `write_propose`.

**A guard, so restoring it is deliberate.** `tests/test_api.py`'s
`check_shipped_config_bills_no_anthropic_path` fails if any model-bearing key in
the shipped config names an `anthropic/` model. Updating that check is part of
turning spending back on, which is what keeps it from happening by accident.

**Follow-up, same day: the summarizer gets a cheaper model of its own.**
`[critique] summarizer_model` returns as `openai/gpt-5.6-luna` — about $0.26 per
full rebuild of the book against roughly $0.49 on the `[llm]` default, so the
override that exists to buy editorial judgment also lowers the bill on the most
repeated call in the system. `editor_model` stays unset and the critique editor
runs on the default. The line is written into `config.toml` and **commented
out** pending a temperature fix in `llm.py`; until it lands the summaries run on
the default. This does not touch the ruling above: luna is an `openai/` model,
no Anthropic-billed path returns, and the AH-3 guard is unchanged by it.

`.claude/skills/authorlm/SKILL.md` §"The beat loop" and
`docs/writing-essays-tutorial.md` §0, §2.4, §10 and §11 are aligned to this.

### 15.11 Model profiles — the adapter surface above LiteLLM (2026-08-31)

Sponsor ruling, on the retry-without-temperature safety net that made GPT-5.6
Luna work: *"This is not ok. We need adapters for the different models."*

The net was resilience standing in for knowledge. A model whose contract we
already knew was still being told at 400-time, once per process, what a table
could have said before the first call — and the two live failures that produced
it (Claude 5's `temperature != 1`, then Luna's server-side version of the same
rule) were both facts, not accidents.

**LiteLLM stays.** It is the transport and the translation layer, and nothing
here replaces it or adds per-provider classes. What is added is a thin
AuthorLM-owned table above it — `MODEL_PROFILES` in `llm.py`, beside
`VENDOR_KEY_ENV`, which is the same kind of lookup (model string → the contract
that model imposes on our request). Each row says three things: whether
`temperature` may be sent at all, whether `reasoning_effort` may, and whether
block-level `cache_control` is worth attaching. Matching is by prefix with the
longest match winning, so a dated release inherits its family's row.

**Rows are verified, never guessed.** The Claude 5 family (sonnet-5, fable-5,
opus-5) is `vendor-default-only`, which litellm's own map confirms
(`supports_sampling_params = false`); `claude-haiku-4-5` carries no such flag and
therefore keeps its temperature — the family is enumerated rather than globbed
precisely because `claude-haiku-4-5` and `claude-haiku-5` would be swept the
wrong way by any `claude-*-5` pattern. Luna is `vendor-default-only` from the
live failure of 2026-08-30. The gemini 2.5 family takes temperature, as it always
has. `gpt-5.6-sol` and `-terra` get **no row**: nobody has run them here, and a
guess in this table is worse than an admission.

**An unknown model is never refused.** It gets `UNKNOWN_PROFILE`, which is
field-for-field what this module did before the table existed, plus one quiet
line saying there is no profile and that a rejection will self-correct once. The
retry net is still there and still saves that call — **but firing it is now an
alarm**: the warning names `MODEL_PROFILES`, the file, and the value to write,
because the net firing means the registry has a gap and the message is the
maintenance instruction. `drop_params`-suppressed drops stay invisible by
construction (litellm offers no callback when it silently sheds a param); the
answer to those is a row, which is what the table is for.

**Nothing about the shipped configuration changed.** Gemini keeps its 0.2, Luna
keeps sending nothing, `[writing]` is still absent, every cache key and replay
fixture is byte-identical, and `caching_available` returns the same four answers
the cache-layer suite pins (§15.4) — an anthropic model with no row still falls
back to litellm's cost map, so the table added knowledge without moving behavior.
What did change is the direction of authority: config can no longer force a
parameter a known model rejects, and when config tries, the client says so in one
line and omits it.

`draft()`'s "no sampling parameter at all" (§15.4, risk R-e) is untouched and
still unconditional. The registry now says the same thing about the drafting
model declaratively; a test asserts the two never disagree rather than making
either derive from the other.

### 15.12 The live test bench (2026-08-31)

Sponsor ruling: *"We need an automated test to make sure that rewrite placeholder
is written in obsidian. You could do this on a test manuscript. […] You should
probably have one test manuscript in the database that you use exclusively for
testing these kinds of things."* Some properties of §14.3's placeholder are only
true of a LIVE workspace — the real database, the real config, the real CLI, a
real file that a real Obsidian vault would open — and the author's manuscripts
are not the thing a smoke check may truncate. So there is now a third place
between the hermetic suites and the author's work: `tools/testbench.py`, which
owns one permanent, disposable manuscript named `testbench` (three tiny essays
of fixed text, in the gitignored `manuscripts/` tree — the script's constants
are the committed source of truth, and `--setup` writes any file that is
missing). `python3 tools/testbench.py --setup` is idempotent, is the ONLY step
that may call a model (three essay summaries on the cheap summarizer) and prints
the cost line; on a healthy bench it is a no-op that says so.
`--check placeholder` then drives the real CLI: `write start` on the middle
essay, and it asserts on the file ON DISK that it holds exactly `PLACEHOLDER`,
that the marker is not inside an HTML comment, front matter, or a code fence,
and that it survives a markdown strip as a plain paragraph — which is the
property that makes it visible in Obsidian and the property §14.3 was written
for. A `try/finally` abandons the writeup either way and the restore is asserted
byte for byte; the whole check runs with the provider keys scrubbed out of the
subprocess environment and passes with no keys at all. The name `testbench` is
the safety rail: it is a constant, re-asserted before every mutating step, and
the script refuses a directory or a registered row that is anything else. The
standing rule is that live smoke checks land here as named `--check`s rather
than as one-off scripts; `tests/test_e2e.py`'s Scenario TB drives the whole
driver hermetically against a temp workspace and the stub model server, and
proves the visibility assertions DISCRIMINATE by re-running the check against a
file whose marker has been hidden inside an HTML comment.

### 15.13 Standing defaults ratified (2026-08-30)

Two open Sponsor questions are closed as RATIFIED DEFAULTS, no longer
pending rulings (author: "Adopt", 2026-08-30, on the orchestrator's
recommendations):

- **Unaccounted digest points at `write complete` WARN and never block**
  (§13's Q1). Rationale ratified with it: a block can be satisfied by a
  lazy `kept` on a dropped point — it converts a visible gap into an
  invisible lie; the warning names every unaccounted id; completion
  remains the author's call. MT-4 remains the standing way to revisit
  with evidence.
- **Incomplete paragraph coverage WARNS and never blocks drafting**
  (§12.4 item 4). Measured basis: blocking would have stopped 5 of 24
  essays, four over a single transitional paragraph; coverage is span
  membership, not a quality guarantee. Staleness — an actual lie about
  the text — already blocks.

### 15.14 The committed triage dist can no longer go stale unnoticed (2026-08-29)

AK: `authorlm/triage_dist/index.html` inlines `docs/writing-essays-tutorial.md`
(vite `?raw` import) as a committed build product, so HelpTabTest's
line-pinned tripwires could still pass against a stale dist if a rewrite
happened to keep those exact lines. `vite.config.ts`'s `tutorial-checksum`
plugin now hashes the doc's raw bytes at build time and stamps
`<!-- tutorial-sha256:<hex> -->` into the emitted HTML; `HelpTabTest`
recomputes the same hash from the doc and asserts an exact match, making any
doc edit without a rebuild a structural test failure.

### 15.15 Client provenance — which chat did this (2026-08-30)

Sponsor ruling: *"track which chat session … was involved with which AuthorLM
session/writeup/intent, so I can go back and look at the session history and
reconstruct"*, and *"create a hook interface … the interface should be as
generic as possible."*

The gap was structural, not accidental. AuthorLM records **whose judgment**
produced a row — `source_id` into the `sources` registry, an author or the
system — and that is the provenance the belief machinery needs. It has never
recorded **which conversation** the author was having at the time. With one
chat that distinction is idle. With three chats open against one workspace, a
resumable writeup, and a session that is a shared ambient work period by
design, it is the difference between a history that can be reconstructed and
one that can only be guessed at.

**The stamp is per invocation, never per "current client".** A resolver in
`clients.py` returns one small descriptor — engine, session id, label,
started_at, transcript hint — resolved once per CLI invocation and once per MCP
tool call, through four layers: an explicit `AUTHORLM_CLIENT` override; an
ordered list of engine adapters; an ambient workspace marker; then nothing.
The layers are ranked by how much the answer is worth, and every stamp carries
the rank as `precision`. `exact` means the identity travelled with this
invocation. `ambient` means it was inferred from workspace state and may
misattribute under parallel chats. When two clients are live at once the
ambient layer **declines to name a session at all** — a stamp that says "one of
three" can be recovered from; one that confidently names the wrong chat
corrupts the reconstruction it was built for.

**The Claude Code adapter needs no hook to be correct.** Measured on 2.1.246
and 2.1.247: `CLAUDE_CODE_SESSION_ID` is in the environment of both Bash-tool
subprocesses and stdio MCP servers, and it differs per chat — the author's two
live `authorlm-mcp` processes carry the ids of the two chats that spawned them,
and each id is the name of that chat's transcript file. The variable is
undocumented, so it is not leaned on alone: `CLAUDECODE=1` (which *is*
documented for both surfaces) gates the adapter, and a `SessionStart` hook
writes a per-session marker under `~/.authorlm/clients/` carrying the label,
the start time and the transcript path. Today the hook only enriches. If the
undocumented variable ever goes away, the marker — keyed by the Claude host pid,
which reaches both surfaces through `CLAUDE_CODE_MESSAGING_SOCKET` — becomes
the mapping, and the adapter stays exact. Adapters are a tuple in one module,
the same declarative shape as `MODEL_PROFILES` (§15.11): a new engine is one
entry plus a documented hook snippet, and an engine whose hooks we cannot reach
degrades to `ambient`, not to a wrong answer.

**One stamp site, not a list of them.** `ko_fields` builds the KnowledgeObject
base for every table in the schema and already writes an empty `metadata`; the
join key goes there, so every row born after this change carries it with no
call-site edits. The reconstruction payload — label, start time, transcript
path — is written once per client per AuthorLM session, into that session's
`metadata.clients` list, and reached by joining on `(engine, session)`. A
writeup keeps its own touched-by list, deduped, exactly as it already keeps
`drafting_models`, because a writeup resumed by a second chat is the ordinary
case and the seam should be visible. No schema change, no index, no migration.

**The payoff verb is `authorlm provenance <id>`**, with `--client <session>`
inverting it. It reads row metadata for the join and `trace.jsonl` for the
timeline, and it prints, for a completed essay, which chat drafted each beat
and which chat accepted it. Rows created before this shipped carry no stamp,
and the verb says so in a footnote rather than rendering them as `unknown` and
inviting the inference. Nothing is backfilled: correlating old rows to old
transcripts by timestamp is a confident guess across parallel chats, which is
the one failure this design exists to prevent. For those, the pre-provenance
method still works and stays documented — grep the session transcripts for the
object's id; the filename is the session.

### 15.16 The database performance log — measurement stops being an event (2026-08-30)

Sponsor ruling: *"Can we configure the databases to by default log stuff and
make sure that we can later on see performance logs from the database itself
instead of us having to do one-off measurements? That way we can monitor the
performance over all time."*

`trace.jsonl` (§ "Logging & traces") is the **verb** timeline: one line per CLI
command or MCP tool call, with the wall time of the whole operation. It can say
that `collect` took 900 ms and it cannot say where those 900 ms went. Every
answer to *that* question so far has been a one-off: instrument by hand, run
the verb, read the number, throw the instrument away. A number obtained that
way is a snapshot of one afternoon on one dataset — it cannot show a trend, and
a trend is the only thing that distinguishes "this query is slow" from "this
query is *becoming* slow", which is the distinction that matters when the
dataset grows monotonically for years.

**So the measurement is now standing.** Every query through
`Database.one/all/insert/update`, plus the `BEGIN IMMEDIATE` and the `COMMIT`
that bracket a transaction, is timed with `perf_counter` and folded into an
aggregation dict **on the Database instance**, keyed by normalized SQL shape
(whitespace collapsed, truncated at 120 chars) to `{n, total_s, max_s}`. The
transaction *body* is deliberately not timed as a unit: that would
double-count every query inside it and make the totals a lie.

**One line per invocation, not per query.** The aggregate is written once —
per CLI process (at the end of dispatch, which every `sys.exit` path in `cli.py`
passes through, with `atexit` as the backstop) and per MCP tool dispatch, since
that server is long-lived. A verb issuing 900 queries therefore costs 900 dict
updates and **one** line of I/O. That ratio is the whole argument for shipping
it on by default rather than behind a flag nobody remembers to set. On top of
it, any single query over `[db] slow_ms` (default 100) writes its own immediate
line tagged `"slow": true` — the aggregate already counts it, so that line is
the detail: when it happened, and under which client.

Each line carries the client provenance join key (§15.15), resolved once per
invocation and reused, so the perf log joins straight to `trace.jsonl`, to row
metadata, and to the chat transcript. `authorlm dbperf [--days N] [--top K]`
reads it back: top shapes by total time and by worst single query, the per-day
grand totals, the slow-log tail, per-client attribution. It does not open the
database, so it is safe against a live workspace mid-flight.

**It is never in the way.** Every write swallows its own failure — a full disk,
an unwritable directory, an unserializable value — because a performance log
that can break a `collect` is worse than no performance log. The log write sits
outside every timing window, so it never measures itself, and a workspace
deleted out from under a late flush is never resurrected by one. No new
dependency, no schema change, no index. `perf_log = false` leaves `Database`
holding no recorder at all, so the cost of opting out is one `is None` test per
query, not a disabled timer.

**The standing watch item this exists to catch.** The latest-manuscript-version
`SELECT *` reads the whole `files` JSON blob — the complete manuscript text —
to answer a question about the newest row. Measured at 17.7 ms max across 245
versions, growing linearly with the version count, and it runs on the common
path. It is not worth fixing today and the fix is already known: project the
columns actually needed, or add one ordinary index (never a `CREATE UNIQUE
INDEX`). What was missing was not the fix but the trigger. Now `authorlm
dbperf` shows that shape's `max_ms` and its per-day totals over the whole
history, so the decision to spend the fix is made off a trend instead of off
another afternoon's one-off measurement.


### 15.17 Scope-derived intent attachment — a rewrite serves every goal that covers it (2026-08-29)

Sponsor ruling: an intent should not have to be named to be served. The scope
already on `declared_intents` says where a goal applies; a rewrite of an essay
should automatically serve every active intent whose scope covers that essay,
with no new grouping object — *the scope tier is the group*.

**The gap was one-sided.** `declared_intents.scope` and
`passes.intents_in_scope` have existed since the critique pass, and two
consumers use them: `preflight` refuses an edit pass while proposed in-scope
intents are untriaged, and `build_context` puts the active ones in the editor's
payload. The write path never called either. A writeup bound itself to exactly
one intent through `writeups.intent_id` and the other goals covering that essay
were invisible to it — including to the beat drafter, whose payload carried no
intent at all, in any block. A beat has never been told what the rewrite is for.

**Derive, then ratify.** `write start` with no `--intent` derives the set —
`intents_in_scope(file, 'active')` — and persists it in
`writeups.metadata.intents` as PROPOSED, printed grouped by tier: `file`,
`chapter` (the toc parent chain), `manuscript`. Explicit `--intent` flags
override derivation entirely and the author's first flag is the primary, so the
single-flag command that existed before this section produces bit-for-bit the
same writeup. The set FREEZES at `write plan`, which is already the author's
ratification gate (§13.3): the plan's own output prints the frozen set above the
beats, so ratifying the plan knowingly ratifies the intents, and there is no
second confirmation verb. `write intents --add/--remove/--primary` adjusts
between start and plan; `--add` stays legal afterwards as an explicit join, at
the cost of one re-billed cached prefix.

**Frozen means frozen.** An intent declared or re-scoped mid-writeup never joins
silently. `write status` reports it as *newly in scope since ratification*, and
the author either joins it (`--add`) or dismisses it (`--ignore`). Membership
after the freeze changes only by an authorial verb.

**No schema change, and no group entity.** `writeups.intent_id` keeps holding
the PRIMARY. Everything else lives in writeup metadata, on the dedup-append
idiom `drafting_models` and `clients` already use. The member record freezes the
intent's `statement`, `scope` and `tier` at ratification rather than joining
them live — `scope` is mutable now (§15.17's `intent scope`), and a live join
would let a re-scope in another chat silently rewrite block A between two beats,
which is design F4's silent invalidator arriving by a new road. Freezing also
makes the record history-safe: re-scoping an intent cannot retro-narrate what a
finished writeup was serving.

**Episode attribution, and a bug it uncovered.** The most specific in-scope
intent is primary — file over chapter over manuscript — and per-beat transitions
attach to the primary's episode ONLY. No fan-out: one authorial act is never
mined twice. Two intents tied at the most specific tier are not broken by rule;
the machine has no information that separates them and the choice is
irreversible, so `write start` prints the tie and `write plan` refuses until
`write intents --primary` settles it, in the same shape the missing-`--style`
refusal uses — a refusal that names the candidates rather than sending the
author away to look them up.

Building this exposed a live defect. `collect` attached transitions to
`sessions.current_episode`, which returns *the session's most recently created
open episode*, whatever intent it belongs to — and `write_accept` and
`write_reject` passed the same row to `record_review`. With two writeups open at
once, an ordinary way to work since §14, every beat of both landed on whichever
intent had been declared last. The rule "attach to the primary's episode" fixes
mis-attribution first and prevents fan-out second. `collect` grows one optional
`episode` parameter; every other caller is unchanged; a new
`sessions.episode_for_intent` finds the intent's open episode in ANY session,
because an intent declared yesterday has no open episode in today's.

**Completion reports, and never blocks.** At `write complete` every member gets
a disposition — served, explicitly deferred, or UNSERVED — and an unserved
member warns and names both exits, per §15.13's ratified default. Beat specs MAY
tag the intents they serve, like the digest's point ids; an untagged beat counts
toward every member. Each member intent also records `served_by` on its own row,
so a chapter-wide intent's completion analysis can see the three essays rewritten
under it without a group object ever existing.

**Scope curation.** `intent declare` gains `--scope <file> | --chapter <opener>
| --manuscript`, and `intent scope <id> …` (re)places an existing one. A
re-scope is metadata-forward: the column moves, `metadata.scope_history` records
the move, and no episode, transition or frozen member record is rewritten. The
absent scope still means manuscript-wide, but it now has a consequence the
author must see, so both surfaces say what it means at declaration time and the
skill asks one line when the author has not said.

The one backfill is the author's own sitting, not a migration: for each
unscoped active intent, `intent scope --triage` shows the statement and the
files its episodes ACTUALLY touched, with a suggested tier and the reason for
it, and the author rules from evidence rather than memory. Until that sitting
happens, `write start` warns when many manuscript-wide intents would attach to
every writeup — a warning, because the author may want to write today.

**Determinism.** The frozen set joins block A as an INTENTS section rendered from
the member records alone — tier label and statement, no ids, no timestamps, no
status — so identical stored state still yields byte-identical blocks S and A.
`beat-draft.md` gains one paragraph saying what an intent is *not*: a goal
governs selection and emphasis, never grounding. A claim that appears only in an
intent is a question for the author. Adding the section re-bills every in-flight
writeup's cached prefix exactly once at deploy; `cache_cold`'s warning firing
once, on the next beat of each open writeup, is expected.

**Two corrections to the text above, made while building it.** The flag is
`--book-wide`, not `--manuscript`: `-m/--manuscript` is already registered on
every subparser as the manuscript SELECTOR, and a second registration of the
same option string is an argparse conflict at parser-build time.

And "`collect` grows one optional `episode` parameter; every other caller is
unchanged" named too few callers. This section named `write_accept` and
`write_reject`; the write path in fact runs six collects, and each one needed a
ruling rather than an assumption. The complete list, with what each does and
why:

| Site | Disposition |
|---|---|
| `write_start`, collect 1 (uncollected pre-existing edits) | **ambient, by declaration.** That work PREDATES the writeup — it was done under whatever the session was already doing — and filing it against a goal declared a moment later would be back-dating. |
| `write_start`, collect 2 (the truncation) | **routed to the primary**, when the primary is settled. It is the writeup's own first act and the largest single transition it will ever produce. While a tie is UNSETTLED it stays ambient: `writeups.intent_id` then holds the lowest-id candidate as a placeholder, and real work must never be attributed to a candidate the author has not chosen — the refuse-to-guess doctrine of the tiebreak itself, applied to the episode instead of the column. `write plan` settles it, and everything after that point is routed. |
| `write_accept` | **routed to the primary**, and the same row goes to `record_review`. |
| `write_reject` | no collect; its `record_review` is **routed to the primary**. |
| `write_complete` | **routed to the primary.** Usually a no-op, but a doc pull or a hand edit between the last accepted beat and the completion makes it a real collect with real transitions. |
| `write_abandon`, both collects | **excluded, deliberately.** They record the pre-abandon snapshot (RISK K2) and the restore — the unwinding of the writeup, not work done under its goal. |
| `_critique_resolve_essay`, collect 1 (`pre-critique-resolve`) | **ambient, by declaration.** It snapshots the author's own uncollected local edits, which PREDATE this verb — the same reasoning as `write_start`'s first collect. |
| `_critique_resolve_essay`, collect 2 (`critique-resolve`) | **NO episode** (`api.NO_EPISODE`). Added 2026-08-30 (AQ). |
| `_critique_rollback`, collect 1 (`pre-critique-rollback`) | **ambient, by declaration.** Same reasoning: the author's own uncollected edit is their work and predates the verb. |
| `_critique_rollback`, collect 2 (`critique-rollback`) | **NO episode.** ADDED 2026-08-30 (AQ) — there was no second collect at all, so the version history held the pre-rollback snapshot and then a GAP where the restore should have been. Ambient, it would have credited whatever goal happened to be open with the UNWINDING of an edit pass. |
| `filter_run`, `filter_record` | no collect. |
| `filter_settle`, collect 1 (`pre-filter-settle`) | **ambient, by declaration.** The author's own uncollected edit, snapshotted before the settle overwrites the file. |
| `filter_settle`, collect 2 (`filter-settle`) | **NO episode.** A filter pass is hygiene, not work toward a declared goal (§15.20). |
| `filter_rollback`, collect 1 (`pre-filter-rollback`) | **ambient, by declaration.** As above. |
| `filter_rollback`, collect 2 (`filter-rollback`) | **NO episode.** The unwinding of a pass, as with `critique-rollback`. |

**`write_abandon`'s two collects are unchanged and stay as ratified.** They
record the pre-abandon snapshot (RISK K2) and the restore, and their row above
calls them "excluded, deliberately — the unwinding of the writeup, not work
done under its goal." A reader coming from the rollback rows will notice that
`critique-rollback` and `filter-rollback` are *also* unwindings and are filed
under NO episode rather than left to the ambient default, and may read the two
as inconsistent. The standing ruling holds and the cases differ:
`write_abandon` unwinds a writeup whose primary intent is KNOWN, so the
question there is which episode to route to and the answer is none of them —
excluded from routing, ambient by default. The rollback verbs sit on the edit
door, which has never had an episode to route to at all, so for them "none" has
to be said explicitly or the default says something false. If a future sitting
wants the two unified, that is a ruling to make on the record and not a
tidy-up to perform in passing.

**The table was incomplete outside the write path too, and that omission
also had teeth (2026-08-30).** `_critique_resolve_essay` runs two collects and
neither was ruled on here. The second — the one that records the settle's own
change to the essay — was ambient, so *every critique-pass resolve attached its
transitions to whichever episode happened to be open*: this section's own bug,
living on in the critique pass, one file away from where it was fixed. Settling
staged edits is **hygiene, not goal-work**. Neither home was right: filing it
against the open goal makes a completion report say that goal was served by an
edit sweep, and opening an episode for it would be the grouping object this
section refused. So it attaches to none, through a new `api.NO_EPISODE`
sentinel — `episode=None` already means *ambient*, so saying "attach to
nothing" needed a third value that is neither a row nor `None`. The version
history, the `critique_edit` evidence rows (which have always carried
`episode_id = NULL`) and the pass row are the complete record, and explained
rejections still reach belief learning because that path runs off the evidence
stream and the author's words, never off an episode. The filter pass (§15.20)
files its own settle the same way, for the same reason and through the same
sentinel.

Naming only the two verbs that run every beat was an omission in this text
rather than a decision, and the omission had teeth: the truncation is the
writeup's biggest transition, and with two writeups open it was landing on
whichever intent had been declared last.

Everything else in this section is as ratified.


### 15.18 The usage ledger and the budget seam — spend stops being a document (2026-08-30)

Sponsor ruling: *"building a termination and budget feature first class would
make sense. For now we don't need to cut off the usage when the budget has
reached, but it would be good to track usage so if you ever want to take this to
commercial stage, we can have that ready-made."*

The spend audit found seventeen billable call sites reached by thirty-one
triggers, four of which fire with no author action, and it closed with a standing
observation: the reason two of those escaped five previous catches is that
neither path prints a usage line, so **a doc claim was the only thing standing
between the author and the spend**. That is the defect. A document is a claim
about the code; it is checked by a person, it ages the moment the code moves, and
it cannot answer *what did last week actually cost*. `trace.jsonl` knows which
verbs ran and `db-perf.jsonl` knows where their time went; nothing knew where
their money went.

**So the accounting is now standing, and it sits below the layer that prints.**
Every call through `LLMClient` already counts its tokens; those counts now fold
into an in-memory aggregate keyed by `(purpose, model)` — where `purpose` is the
config section that chose the model, `[llm]` or `[critique] summarizer_model` or
`[writing]` — and one JSON line per invocation lands in
`<workspace>/.authorlm/logs/usage.jsonl` beside the other two logs. The seam is
`_record_usage`, which is *beneath* `stats_line()` and `_report_llm` and the CLI
and the MCP server, so the auto-collect extraction and the whole MCP surface —
the two paths the audit found invisible — are covered with no call-site edit.
Sixteen of the seventeen call sites reach it that way; `generate_image` is not a
client and records itself in one line.

**The architecture is `dbperf`'s, because the argument that won there wins
here.** One line per invocation rather than per call, so a verb making 24
summariser calls costs 24 dict updates and one line of I/O; 5 MB rotation, two
generations; every write swallows its own failure, because a usage log that can
break a `collect` is worse than no usage log; `[usage] ledger = false` holds no
recorder at all, so opting out costs one identity test per model call — a test
against something that takes hundreds of milliseconds. Each line carries the
client provenance join key (§15.15), so the ledger joins straight to the verb
timeline, to row metadata, and to the chat transcript. And, rhyming with the slow
line, any single call estimated over `[usage] expensive_usd` writes its own
immediate line: the aggregate already counts it, so that line is the detail —
when, under which chat, and for what.

**Cost is an estimate and says so, everywhere.** Rates come from litellm's model
map through `get_model_info`, which resolves the vendor prefix — `model_cost`
keyed directly does not, and would have priced `openai/gpt-5.6-luna` and
`anthropic/claude-fable-5` at `null` while pricing `gemini/gemini-2.5-flash`
correctly, i.e. silently lost the two most expensive paths in the shipped
configuration. A model the map does not know records **tokens with a null cost,
never a guess**, and the report says how many calls it could not price rather
than printing a total that quietly omits them. It is a public price list, not an
invoice, and the header says that too.

**The chat side is consumption, not billing, and is measured rather than
assumed.** Beat drafting moved into the conversation precisely so it would be
subscription-covered (§15.10) — which made it free to the invoice and invisible
to everything. It is now measured, through an **optional** adapter capability: an
adapter that can point at its engine's transcript may implement
`usage(client, checkpoint)`, and the Claude Code adapter does, by parsing the
session's JSONL incrementally from a byte offset so each sweep reads only the new
bytes. Three facts from reading real transcripts shaped it. The volume on a
cached turn lives in `cache_creation_input_tokens` and `cache_read_input_tokens`,
not in `input_tokens`, which is routinely `2` — so all four counters are read.
`iterations` restates the same numbers and is ignored. And **the same
`message.id` is written to the file up to six times with an identical usage
object**: across this machine's 49 transcripts, 21,709 usage-bearing lines carry
10,571 distinct ids, so a parser that sums lines overstates by 2.05×.
Deduplication on `message.id` is the difference between a number and a fiction.
Chat totals carry **no dollar figure** — a subscription does not bill per token,
and printing a notional one would invent a bill that does not exist.

**The sweep runs opportunistically and never as a process.** At the flush point
that already exists, for the chat that is *right now* driving AuthorLM —
resolved `exact`, with an adapter that implements the capability — and at most
once per session per five minutes. Between sweeps the cost is one `stat`.
`authorlm usage --sweep` forces it across every live session, and the
`SessionEnd` hook, which already unlinks the marker, gains one forced sweep
before it: the only place a chat's final turns are ever counted, and worth the
milliseconds, because the tail of a long session is often its largest part. That
one sweep declines the discontinuity recompute — a full file read against the
hook's documented 1.5 s shared budget — and takes the `restart` loss; the
checkpoint is left untouched, so the next opportunistic sweep of that session
does the recompute off the hook's clock. No daemon, no thread, no cron. A session
with no hook is *reported* as uncounted rather than silently under-counted.

**The ledger carries no prose.** Counts, model strings, config-section names, the
`command + action` verb label `trace.jsonl` already permits, and the provenance
join key. No prompt, no completion, no filename, no transcript path. The
transcript parser reads six keys and coerces four integers; it does not evaluate,
dispatch on, or act upon anything a transcript says, and a line whose usage field
contains an instruction produces a zero.

**`authorlm usage [--days N] [--by client|purpose|model|day|verb]`** reads it
back: the estimated API total with its unpriced remainder named, chat consumption
labelled as subscription and dollar-free, the per-day trend, the top consuming
chats, and the expensive-call tail. Like `dbperf` it does not open the database,
so it is safe against a live workspace mid-flight, and like `dbperf` it is honest
about an empty log rather than printing an empty table.

**And the budget is built, tested, and dormant — which is the ruling, exactly.**
`[budget]` is a section the shipped config does not have, the same way `[writing]`
is a section the shipped config does not have, and for a related reason: what is
absent is what is off. Present, it drives a warning line on the report and one
stderr line per process when the day's estimate crosses `warn_at`.
`enforce = true` additionally makes `budget.gate()` refuse a call before it is
built, at three seams — `complete`, `draft`, `generate_image` — with a refusal
shaped like `WRITING_ABSENT`: the section, the number, and the recipe both ways.
**No shipped configuration sets it, and no call is refused today.**
`budget.settings()` inverts `dbperf.settings()`'s default deliberately: a broken
config must not be a silent off-switch for a *measurement*, and it must not be a
silent refusal for a *policy*. Commercial-readiness is not the enforcement; it is
the seam being exercised on every billable path, returning `None`, with a test
that proves it would refuse if it were ever asked to.

**Reconciliation with `it-462f742f2bae`** (open: *"usage lines on silent auto
paths"*). That item is about the author seeing a **printed line in the moment**;
this section is about the spend being **recoverable after the fact**. They are
different remedies for the same finding and neither subsumes the other. This work
deliberately **does not** add printed lines to the auto-collect path or the MCP
surface — that stays `it-462f742f2bae`'s remit, and doing it here would duplicate
its work and pre-empt its own ruling on what the MCP surface should return. What
it contributes is a seam: once the ledger exists, that printed line can be
sourced from `usage.pending()` — the un-flushed recorder totals — instead of from
a per-verb `stats_line()` the auto path has no client handle for.

### 15.19 The blackboard doctrine (ratified 2026-08-30)

The database is a blackboard: parallel chat sessions, the MCP server, the
CLI, and the watcher all read and write shared authorial state. Four
incidents taught the same lesson by different roads (the mid-rebuild race
→ snapshot isolation; the parallel-rewrite truncation → pinned in-flight
context; the episode mis-attribution → primary-intent routing; the
two-ambient-markers case → provenance's refusal). The rule they share is
now doctrine, to be built to rather than retrofitted toward:

1. **Every read of shared authorial state is capture-consistent or
   pinned.** A verb resolves its view of the manuscript once per
   invocation; a gate judges exactly the text its consumer will use.
2. **Every ambiguous attribution refuses rather than guesses.** Work is
   never filed to a placeholder, a tie, or "whichever was most recent" —
   the author's explicit choice, or nothing.
3. **A concurrent writer invalidates loudly, never silently.** Staleness,
   in-flight neighbors, and newly-in-scope arrivals are flagged states
   with named remedies, not conditions discovered later.

Review checklist line: any new surface touching shared state names which
of the three rules covers each read, write, and attribution it performs.


### 15.20 The filter pass — the loop's editing mode (2026-08-30)

Sponsor ruling, on the difference between a lens and a filter: a lens "takes
the whole essay and judges it at once"; a filter "takes each bit of an essay
and passes it through that same process" — unit by unit, one ratified prompt,
each unit conditioned on what came before, *"like the autoregressive model
we've created for the write verb."* With it, three rulings: approximate
idempotency is enough ("we don't need perfect idempotency but yes, we need
approximate"); there are two use cases, not one, and some "require a global
view of the document à la prompt pinning"; and the implementation is
chat-first — "a chat-only implementation, which still generates learning and
feeds it back into the system, and uses a prompt that is in the system… use
some of the free tokens we get as part of subscription to do the bulk of the
work instead of making external LLM calls."

Design: `docs/filter-pass-design.md`. This section records what it means for
the text above.

**§11's revision mode is answered, in the negative.** §11 reserved
`writeups.mode='revision'` for "targeted edits at arbitrary positions in
existing text" and said to build it "when a real revision session demands
it". The filter pass is that demand, and it is **not** built inside the write
loop. §10's Q4 finding was that revision beats cannot ride the append-only
rolling transcript because they target arbitrary positions; the conclusion it
did not draw is that a targeted edit is not a beat at all. It is a staged
`<<old>>{{new}}` proposal, and this system already has a door for those. So a
filter's output goes to `doc_threads` through the critique pass's settle flow,
not to `guidance_history` through the beat cursor. The `mode` column keeps its
reserved name for seam and bridge writing (§10's first trial), which is
genuinely write-loop work; the editing use case has left.

**The two modes of one loop turn out to be two doors of one system.** §10 said
"design them as two modes of one loop". What is built instead shares the parts
that were worth sharing — the layered payload with a stable cached prefix, the
registered prompt file, the deterministic assembly the conversation drafts
against, the verbatim-anchor law, the author's accept / reword / reject with
the reason recorded verbatim — and forks the part that was never going to
share: appending under a plan versus replacing in place under a verdict.

**The finding→edit door is now a contract, not a coincidence.** `passes.stage`
was written for critique-pass output; three of the four settle functions
(`compose_marked_text`, `final_text_from_marked`, `record_resolution`) turned
out to be origin-agnostic already, and the fourth (`staged_threads`) hardcoded
one literal. The door is now explicit: a producer supplies a file, a unit
index, the unit's echo, a replacement and a why; the harness supplies `old`
from the file itself and never from the producer; and a producer that cannot
anchor verbatim gets nothing, with no fuzzy match and no closest-paragraph
guess. `origin_type` names the producer — `author_comment`, `critique`,
`filter`, and `lens` when a lens finding starts carrying a proposal. Lenses
are unchanged today; the contract is written so they need no refactor when
they arrive.

**A local settle transport, and one canonicalization to make it safe.** The
critique pause lives in a Google Doc. A filter's pause lives in the local
file: the marked text is composed by the same pure function, written to disk,
post-edited in Obsidian, and read back by the same resolver. This rests on
applying `threads.py`'s already-ratified law — "while a thread is pending, the
CANONICAL text is the old text" — to local files as well as to Doc pulls, in
one line inside `read_manuscript_files`, beside the `strip_embed_lines` call
that has always canonicalized at that same seam for the same kind of reason
(local derived machinery is not the essay). The local grammar is the REPLACE
form ALONE — `<<old>>{{new}}` — and not the Doc canonicalizer: in a Doc tab a
bare `{{…}}` is a critique-pass insertion, but in a manuscript file it is the
author's `{{title}}` or their set notation, and the broad strip deleted every
one of them from everything the system observes, silently. A filter never
stages an insertion, so the narrow rule loses nothing. Every observer therefore reads the
essay; only the settle verbs, which own the grammar, read the markers. Two
guards are NOT free and are built with it: `critique_forms_pending`
generalizes over `origin_type`, and `_refuse_mid_rewrite` grows a byte-level
marked-file check — `push_doc` reads the file directly and would otherwise
push the markers into the Doc, where the next pull would silently discard the
settle. It is the one place this design moves a rule below §14.2's disk-truth
/ context-truth line, and it is recorded here because it is the change most
worth challenging.

**Attribution: no episode, and that is the point.** A filter's verdicts are
`evidence` rows with `evidence_type='filter_edit'` and `episode_id = NULL` —
byte for byte the shape `critique_edit` has always had — and the settle's
SECOND collect attaches its transitions to **no** episode, through a new
`api.NO_EPISODE` sentinel. Its FIRST collect stays ambient, deliberately:
that one snapshots the author's own uncollected local edits before the settle
overwrites the file, and those predate the verb and are their work. The two
collects of one verb have different dispositions for different reasons, and
§15.17's table now names every one of them. Neither of the obvious homes was right. Filing a
duplicate-word sweep against the file's in-scope intents would make §15.17's
completion report say a goal was served by hygiene; a filter-owned episode
would be the grouping object §15.17 refused. Explained rejections still reach
belief learning, because that path runs off the evidence stream and the
author's words, not off an episode. The in-scope intents do appear in the
payload — labelled, as in `beat-draft.md`, as governing selection and emphasis
and never grounding, and here never attribution either.

**Third dormant section, and the pattern is now standing.** `[filtering]` is
absent from the shipped config, as `[writing]` (§15.10) and `[budget]`
(§15.18) are absent, and for the same reason: what is absent is what is off.
`filter run` makes no model call at all — the flag polarity is inverted
against `write draft --dry-run` deliberately, because the chat path is the
default here from the first commit rather than after a ruling. `--native`
refuses without the section and names the chat flow. The recipe's recommended
model is the CHEAP tier, which is the opposite of §8's drafting ruling and is
the sweep framework's criterion applied honestly: drafting's quality comes
from the model, a filter's comes from the assembled context and a prompt that
fully specifies the check. A filter that needs a mind rather than a rule is a
lens.

**Deliberately not done:**

- **Cross-essay filters.** A filter's jurisdiction is one essay. The motif
  used one way in `becker.md` and the opposite way in `kindness.md` is a real
  finding and this pass cannot see it; that question is a lens. Stated so the
  metaphor filter is never mistaken for a book-wide check.
- **Convergence.** Approximate idempotency is four mechanisms — an
  identical-text refusal, the prior runs' rejection reasons verbatim in the
  cached layer, a standing paragraph in the harness prompt, and fresh state
  per run — and no fixpoint loop. A filter whose proposals never stop has a
  prompt fault, and the remedy is `filter show` and an edit.
- **A suppression key.** A `dedupe_key` on (filter, file, unit, hash(old))
  would harden re-proposal suppression and would also silently hide a real
  finding the moment a unit's text moved for an innocent reason. The
  rejection reasons are the softer, honest instrument.
- **A third class.** `sequential` and `global` are what the ruling names and
  what the three ratified filters need. Adding a filter in an existing class
  is a file and no code; inventing a class is code, and says so.
- **Filter ordering and composition.** Two filters do not commute and no
  order is enforced or recommended by the machinery. That is editorial
  practice, not a feature.

**Four departures from the text above, made while building it.**

1. **The critique-pass finding is FIXED here, not deferred.** The design
listed `_critique_resolve_essay`'s ambient collect as a one-line follow-up
for whoever owns that stream. It ships in this one: the sentinel §15.20 adds
IS the whole fix, the bug is live on a path the author uses today, and
leaving a known mis-attribution in place while shipping the machinery that
fixes it would be the kind of thing rediscovered later as a regression.
§15.17's disposition table now names both of that verb's collects.

2. **A global filter's run is created by its PRELUDE, not by `filter run`.**
The text has `filter run` refuse when a global run has no registry. Built
literally, that created the run row and then refused, leaving a half-made run
`filter status` would report. `filter run` now refuses BEFORE creating
anything when no run exists at all, and the prelude — which is where a global
run really begins — creates the row and carries the two-filters-on-one-file
warning.

3. **Triage is scoped to the ACTIVE run, not to the file.** Listing every
filter thread on an essay let `filter triage --accept 1` re-accept a proposal
the author had rejected in an earlier run, silently resurrecting it into this
run's settle. A settled run's rejections belong in block A's PRIOR RUNS
section, where their reasons are law for the next run — not in a triage list.

4. **The pull path gets the symmetric guard, built rather than noted.** The
text called the checkout gate "mostly unreachable" cover for it and said
mostly unreachable is not a guard. A marked file is now skipped and reported
by name on pull, in the shape `conflicts` and `local_ahead` already use, and
`--force` cannot reach past it.

5. **The canonicalization is the REPLACE form only, and the text's stated
reason for the broad one was false.** The design specified `strip_pending` on
the grounds that it is "pure, idempotent on unmarked text". It is not:
`{{title}}`, `{{a, b}}` and any Handlebars sample in an essay ABOUT templating
were deleted from every observed copy of the manuscript, with no warning,
because once they were gone there was nothing left to warn about — and
`is_marked` tripped on them, refusing such a file's Doc push forever with a
message about a settle that did not exist. Four sites narrowed
(`read_manuscript_files`, `is_marked`, `unmark`, and the settle's
`final_text_from_marked`, which would otherwise have deleted a `{{title}}`
from the finished essay). A filter never stages an insertion, so nothing this
seam exists for is lost.

6. **A re-record supersedes.** An ordinal the new reply did not reach is a
proposal that no longer exists; left standing it appeared in the next `filter
edits` list under a number the author reads as current, and could be accepted
into a settle it was never part of. `passes.stage` has always done this for
the critique pass, and the door's two producers now agree about it.

7. **`_critique_rollback` gets the same treatment as `_critique_resolve`.** It
had no post-rollback collect at all, so the restore was never recorded; and
collected ambiently it would have credited whatever goal was open with the
undoing of an edit pass. `write_abandon`'s two collects stay as ratified —
§15.17 now says why the cases differ rather than leaving a reader to infer an
inconsistency.

8. **`filter rollback` refuses on a marked file**, naming both exits: a
rollback mid-pause destroys post-edits with nothing left to recover them from,
which is a strictly worse orphan than the one `filter status` finds, where the
bytes describe the state completely.

### 15.21 Filter CREATION goes on MCP; the loop stays CLI-only (2026-08-30)

Sponsor ruling: "Is there a verb to create new filters? We need to integrate
this support into the full toolset — cli, mcp, skill.md." §15.20 shipped the
CLI-only artifact (`filter add`/`list`/`show`) but never exposed it past the
shell. The ruling this section records: filter CREATION is a curation act,
the same standing as `add_style_law`/`define_style_guide` — one author
decision, one ratified record — so `add_filter`/`list_filters`/`show_filter`
now exist as MCP tools, thin wrappers with the same read-then-ratify doctrine
the shell already enforces. The filter LOOP (`run`/`prelude`/`record`/
`settle`/`triage-flags`/`status`/`unmark`/`rollback`/`abandon`) is unchanged
and stays CLI-only, by the identical one-call-surface reasoning §15.20 gives
the write loop: `list_filter_edits`/`triage_filter_edits` remain the loop's
only conversational door. Creating a filter is a single, idempotent act with
no autoregressive state to protect; running one is the loop, and the loop
gets one surface.

### 15.22 Protected terms and the pronunciation dictionary (2026-08-30)

*(Numbered 15.22 rather than 15.21: §15.21 was taken by the MCP filter-creation
ruling, which merged the same day this stream began.)*

Two Sponsor rulings on the filter pass, arriving the day after it shipped, and
they turn out to be one mechanism seen from two sides.

**The first: a filter must know the author's vocabulary, deterministically.**
*"Does the filter get the concept graph…so it knows not to substitute terms of
art?"* It did not. Block A carried CONCEPT NOTES — the author's definitions, in
prose — and a definition is not a list. The duplicate-words artifact papered
over the gap by naming eight terms in its own text, which is a lexicon that
cannot grow and cannot be checked. Block A now carries PROTECTED TERMS, derived
from `concept_nodes` by a rule with no model in it: four of the eight node kinds,
two of the three statuses, names and aliases, plus every term the pronunciation
dictionary carries. The kinds are chosen on one line — **vocabulary, not
label**. `concept`, `metaphor`, `mathematical_construct` and
`historical_reference` name words the author uses in the prose; `objection`,
`example`, `question` and `syllogism` name moves in an argument, and their names
are sentences ("Question about Supreme God"), so listing them would protect the
ordinary English inside them and teach the model that the list means nothing.
Retired concepts are excluded because a retired name is vocabulary the author
deliberately abandoned, and protecting it would freeze exactly the wording a
recast should be free to move; the synonyms that matter survive anyway, because
`merge_concepts` turns a retired duplicate's names into aliases of the
canonical. The capitalization rule is not re-implemented: it is
`concepts.mention_pattern`'s ratified rule — *a single-word name declared with a
capital is the term only where the text capitalizes it* — stated once in the
block's header, with the list left complete underneath it. Pre-filtering the
list by capitalization would have dropped every borrowed lower-case term
(`anattā`, `ressentiment`, `upaya`) from the very protection the second ruling
exists to give them.

The harness supplies the list and does not enforce it — with one exception that
costs nothing. `filter record` now runs `protected_loss(old, new, protected)`,
a pure function over `mention_pattern`, and **warns by name** when a replacement
drops a protected term the original carried. It warns rather than refusing
because a legitimate recast can drop one of two mentions, and a refusal discards
the whole reply for a judgment the harness is not entitled to make. It is the
line between supplying law and being the editor, and the pass stays on the
supplying side of it.

**The second: the terms that are hard to say need somewhere to live.**
*"a running pronunciation dictionary table associated with the manuscript…
make sure the pass does not overwrite existing pronunciations that have been
validated earlier… similar to how toc.md lives… editable from the doc."*
`pronunciations.md` is a new manuscript-root sidecar: markdown, a table, mirrored
into the master Doc as an ordinary tab so the author edits it there like an
essay, and excluded from reading order, concept scanning, summaries, critique,
filter targeting, the manifest and every export by a filename flag that widens
`structure.is_structural`. The flag is a **filename**, not a marker inside the
file, for §1's reason: a marker a Doc round trip can eat is a marker that
silently reclassifies the dictionary as an essay, at which point it ships inside
the book. It stays inside `read_manuscript_files`, so it is versioned,
checksummed, diffed and rollback-able like everything else — structure is not
content, but it is still the author's.

The one inversion is the tab list, and it is what makes three otherwise-invisible
sites safety-critical. `gdocs._reading_order_files` appends the sidecar so it
gets a tab; from that moment `links` contains a file `parse_toc_tree` can never
contain. `sync_tab_structure`, the pull's toc sync and `rewrite_toc_from_doc`
all compare those two lists, and unguarded they would report a permanent
`unsynced` — tab-order sync simply stops working — and then write the dictionary
into `toc.toml`, at which point it becomes a chapter and every exclusion above
unravels at once. All three are filtered, and each one has a test that fails
when its own guard is removed.

Immutability was asked for and is not implemented as a rule. It is a consequence
of there being **exactly one writer**: `proposals.adopt`. Not the model, not
`filter prelude`, not `filter record`, not `filter settle`, not `filter
rollback`, not `filter unmark`, and not the Doc pull except as the author's own
edit arriving through the ordinary tab write. The write itself is
`structure.insert_toc_entry`'s discipline applied to a second table — pure text
insertion, rewriting no line it did not add, on the file's own dominant line
ending. "Exactly one writer" turned out to need defending rather than merely
asserting: `push_doc` normalizes a file's text and writes the normalized bytes
back, so it is a second writer of anything the normalizer is not a no-op on,
and a `\|` escape put the dictionary in exactly that class. The rule that
closes it is that nothing this system writes can carry a pipe at all (below),
and the test that holds it is E3 counting `doc push` as its tenth verb. In front of the pull sits the highest-value line in the whole feature:
an incoming tab that parses to **zero rows** over a local file that has rows is
skipped and reported by name, and `--force` does not reach past it. One bad
Docs export would otherwise destroy the dictionary silently, with the only
recovery in a version history nobody thinks to look at. A pull that parses to
*fewer* rows is not guarded — that is an author deleting a row, which is
legitimate and must work.

**The prelude generalizes, and the class table is amended rather than
smuggled.** §1.1 said a sequential filter has no prelude. A prelude is now
*a whole-essay pass before any unit is judged, producing a run-scoped artifact*:
for `global` that artifact is the frozen registry, rendered into block A; for a
sequential filter that declares `prelude = "pronunciations"` in front matter it
is a set of proposals, and nothing enters block A. The sequential prelude is
optional where the registry is required, because a run without a dictionary diff
is still a correct audio pass while a global unit judged against no registry is
judged against nothing. This is the first front-matter key added since
ratification, and the honest statement is that adding a filter which wants this
prelude is code, once — §1's "a file and no code" holds for the class set, not
for the harness's repertoire.

What counts as difficult is a **floor computed by the harness and a ceiling left
to the prompt**. The floor is one deterministic test: a term containing a
non-ASCII character whose Unicode category is a *letter*. On the live graph that
admits all sixteen of `Bṛhadāraṇyaka`, `Nāgārjuna`, `anattā`, `Śūnyatā`,
`οὐκ ὄν θεός` and their kin, and correctly refuses `Noether's Theorem`, whose
only non-ASCII character is a curly apostrophe. That is the Sponsor's "all
standard non-english terms" with no model in it at all. Above the floor the
prompt judges, because the prompt is reading the prose and can see an italicized
borrowing the graph has never heard of; below it, an ordinary English word doing
duty as a term of art (`Field`, `Test`, `Measure`) is never a candidate, because
nobody needs to be told how to say "field".

Additions ride the ordinary proposal queue, which the door required anyway: a
filter never inserts, so a new dictionary row could not come through the edit
door if it wanted to. The queue's verbatim-repeat hash was not enough — a second
prelude offering a different respelling of a dismissed term would sail past it,
the same way eighteen `note_update` rows once accumulated on one concept — so
the guard is on the term, not the spelling: **one proposal per term, ever, in any
state.** The cost is stated rather than hidden: an author who dismisses because
the respelling was wrong is never asked again, and their remedy is to write the
row themselves, which was the better path in that case anyway.

**And the loop closes on itself, which is the part worth ratifying.** The
Sponsor ruled that a term with a pronunciation is settled: the audio filter
stops flagging it. So the dictionary is simultaneously this pass's **output
channel** and its **suppression list** — a term enters because the filter did
not know how to say it, and once it is in, the filter never asks again. That is
why the dictionary sits in block A, the cached layer, read live from the file
rather than pinned to the run's version: an accepted pronunciation must take
effect at the next window, not the next run. A dictionary edit therefore
invalidates the stable layer and re-bills the cached prefix. That is an honest
invalidation — the suppression set genuinely changed — and `filter run` already
prints the per-block hashes, so it is visible rather than mysterious. §4's four
mechanisms of approximate idempotency gain a fifth that is not approximate at
all, and it costs no convergence machinery: the pass terminates on this concern
because its own output is what stops it.

One doctrine bend is recorded rather than hidden. The blackboard checklist says
every read of shared authorial state is capture-consistent or pinned, and the
dictionary is neither: `summaries.capture`'s `texts` map is built from
`reading_order`, which the sidecar flag removes it from, so it cannot be in the
capture without putting the dictionary back into the reading order. Widening
`capture`'s tuple arity for a non-prose sidecar would break every unpack site;
pinning to `source_version_id` would contradict the suppression ruling above.
What ships is `api._filter_capture` reading it ONCE per invocation and handing
it to run, record, prelude and settle alike, so the doctrine's actual
requirement — one read per invocation, the gate and the thing it gates reading
the same bytes — holds. Found and fixed in the same breath: `scoped_concepts`
was already doing a second disk read inside that same one-capture verb, so
CONCEPT NOTES could differ between window N and window N+1 if a parallel session
touched the file. It gains an optional `text=` override and now reads the
capture too.

**And the three artifacts stop naming this book's words, which ratifies a
boundary that had been left implicit.** A filter artifact is a per-manuscript
object: it lives in the gitignored `_filters/`, the author ratifies it, and it
is supposed to carry doctrine specific enough to be wrong somewhere else — a
duplicate-words filter that did not know this author repeats deliberately would
strip the refrains out of the book. **The prompt carries the author's DOCTRINE;
the payload carries the book's DATA; and where a prompt enumerates data the
payload can supply, the enumeration goes.** So "Nothing, Nothingness, Qualities,
the Chid" leaves both prompts for a reference to PROTECTED TERMS — the Sponsor's
own phrasing, *a list of terms of art will be provided and should be left
alone* — and the motif families leave for a reference to the `[figure]` elements
of STYLE LAW. Refrain-by-default, cut-don't-substitute, precision-over-brevity
and "a listener cannot look back" stay exactly where they are, in the author's
words, because that is what a per-manuscript artifact is *for*. What the three
become is templates: a second manuscript copies them, argues with the doctrine,
and inherits the vocabulary, the families and the pronunciations from its own
graph, law and dictionary with no editing at all.

The motif families were checked for a deterministic derivation and **do not have
one**, which is recorded rather than fudged. The names are real but they live
inside two different `[figure]` statements in two different grammars —
`walls/chains, fire/light, …` after a colon in one, `Fire, fields, walls, stone,
ledger, ladder, and tremor` in prose in another — and a parser would have to
choose which element is authoritative. Choosing is guessing, the same objection
that refused italic-borrowed terms. But the enumeration still leaves the
prompts, because `styles.render` already prints every element whole into block S
with its aspect in brackets: the data is in the payload, in the author's own
ratified words, and the prompt can name it instead of copying it. The aspect tag
is the structured handle; the names inside the statement are not; the design uses
the one that is. The distinction pairs (test/measure, virtue/quality) stay
listed, because `concept-identity` law was read and governs the proposal
screen's verdicts on the graph rather than prose word choice — routing prose law
through it would be the aspect mismatch `loop.REGISTRY` already records once —
and because a template needs a worked example. One sentence names STYLE LAW's
`[lexicon]` elements as the authority the example only illustrates.

**Deliberately not done:**

- **Parsing motif families out of the figure law.** Two elements, two grammars,
  no schema, and both are sentences the author may reword at any ratification.
  The prompts point at the law instead, which is strictly more correct than a
  copy that can disagree with it.
- **A hand-maintained lexicon file.** The graph is the vocabulary. A second
  source that can disagree with it is the ambiguity §1 refuses.
- **Italic-borrowed terms as a derived category.** The only source is `*…*` in
  prose; parsing it is prose parsing, it collides with ordinary emphasis, and it
  is not stable across a Doc round trip — which would break the byte-stability
  the cached layer rests on. The prompt still sees the italics, because the
  units are verbatim.
- **IPA.** The author reads this table. A notation they cannot check is a
  notation they cannot rule on.
- **Escaping a pipe.** The first cut wrote `\|`, and that was worse than
  nothing: `normalize_markdown`'s `_ESCAPE` strips exactly that backslash, and
  `push_doc` normalizes and WRITES THE RESULT BACK to disk — so the escape was
  defeated on the author's own machine and the row reparsed with its cells
  shifted one to the left (`a|b` said `ay-bee` became `a` said `b`). The row
  COUNT does not change under that, so §2.6's zero-rows guard never sees it: a
  settled row silently rewritten, by a SECOND WRITER, which is the one thing
  this file exists to make impossible. A pipe is now refused on all three
  sides — the prelude reply, the row renderer, and the parser, which skips a
  pipe-bearing row and names it rather than guessing at its cells. Refusing
  rather than escaping is also what makes
  `normalize_markdown(render(rows)) == render(rows)` true, which is what keeps
  `push_doc`'s write-back a no-op and `proposals.adopt` the only writer. A
  pipe carries no phonetic information; no pronunciation needs one.
- **A "re-open" verb for a dismissed pronunciation.** The file is the escape
  hatch, and it is a better one.
- **Refusing a reply that drops a protected term.** It warns. The harness
  supplies law; it is not the editor.
- **Pointing the bench's live check at the author's real graph.** Found by the
  live acceptance: `--check pronunciations` asserted "the protected set is
  non-empty" against the testbench manuscript, which is registered
  `--no-extract` and therefore has no graph at all — so that assertion could
  never pass on its own, and its two siblings ("no retired name", "no label
  kind") passed VACUOUSLY over empty sets and would have passed over a
  completely broken derivation. Reading the author's real manuscript instead
  was refused: `testbench.py`'s load-bearing rail is that nothing in it
  resolves a manuscript by argument, and a live read would have to resolve one
  nobody named — "the manuscript that is not the testbench" — making the
  check's verdict depend on which manuscripts happen to be registered in the
  workspace. `--setup` now seeds four concepts on the bench instead (one plain,
  one non-ASCII, one label kind, one retired), the check asserts them BY NAME,
  and the two vacuous assertions gained a precondition that the bench really
  has a retired node and a label-kind node for them to be about. The
  live-graph question is answered by the product rather than by the script,
  and the measurement is recorded: `protected_terms` over the live SMSTTD graph
  returns 313 nodes / 348 names, which is §1.6's own figure.
- **Seeding the file from a filter path.** `pronunciations.md` appears on the
  first ACCEPTED proposal and never before. An empty table that materializes
  because a filter *ran* is a file in the author's vault they did not ask for,
  and it is instantly a Doc tab, a version-history entry and a diff.


### 15.23 The filter pass gains a Doc transport (2026-08-30)

Sponsor ruling: *"We need to build in a way to review edits more easily. We
already have the ability to send `<<new>>` edits to the Google doc as part of
comment processing… Can we use that methodology to make edits? That path also
has the learning loop built in. We can keep the authorlm verb for the review
process if running cli or in chat (using mcp)."*

§15.20 chose a LOCAL settle transport for the filter pass and called it the
riskiest decision in that design, on three grounds: dozens of forms and an OAuth
round trip per essay, the author reads essays in Obsidian, and the Doc bridge is
credential-bound where the pass must work key-less. All three grounds stand. None
of them is a reason the Doc road should be *unavailable*, and the Sponsor asked
for it because reading dozens of struck-and-green spans in Google Docs is
genuinely easier than reading `<<old>>{{new}}` in a text file.

So the transport becomes a **property of the run, not of the pass**. The run's
`mode` — in its `metadata`, the house idiom for new state on an existing row — is
absent until a transport verb chooses one: `filter push <essay>` writes the run's
accepted forms into the essay's tab through the same surgical writer `critique
write` uses and sets `doc`; `filter settle` with no prior push takes the local
road and sets `local`. Once set the mode is frozen — switching is
settle-then-rerun, never a toggle, because a run whose forms are half in the Doc
and half on disk is a run nobody can reason about. Local remains the DEFAULT and
the recommendation: its state is fully described by the bytes on disk, and doc
mode's is not.

Three things this ruling settles, recorded because each was got wrong once:

1. **The settle reads the DOC, not a pulled local file.** `pull_doc` runs
   `strip_pending` on incoming text before writing it, so pulling a tab that
   carries forms writes the pre-edit text and discards every `{{new}}` half —
   including the author's rewordings. X7-1v2 made `critique resolve` read the tab
   as markdown and three-way it against local; the local file is the comparison,
   never the authority. The filter's doc settle does exactly the same, through
   the same function (now `gdocs.tab_marked_markdown`).
2. **In doc mode the local file holds the OLD text, unmarked.** The critique
   model held exactly: ratified content changes only at approval. This costs the
   filter's staging nothing — `staging.mark_local`, `unmark`, `is_marked` and
   `resolve_local` are the local transport's four functions and the doc road
   calls none of them. It also means push guard (a) splits cleanly: its DB half
   (`forms_pending`, already generalized over `origin_type`) fires in doc mode
   and its byte half (`staging.is_marked`) fires in local mode, and the ordering
   they were given — most-informative first, both separately reachable — is what
   makes that work with no new guard. `filter rollback` needed the same
   treatment for the same reason, and got a DB refusal above its byte check:
   in doc mode the bytes are clean and only the rows can see that forms are out.
3. **The learning loop the ruling cited was never wired to the filter pass.**
   `record_resolution` was origin-parameterized in the filter stream, so the
   proposal→final diffs have always been recorded; but the second half — ≥2
   modified acceptances distilled into a pattern candidate for the author to
   ratify — lived in `_critique_learnings` in the CLI and only the critique
   resolve reached it. It is hoisted to `passes.settle_learnings` and called from
   both settles and both transports. The symmetry with §15.20's own §1.8 is
   exact: there, the filter's attribution ruling exposed and fixed the same hole
   in `critique resolve`; here, its transport ruling exposes and fixes the same
   hole in the filter's own settle. A settle now costs two model calls where
   §15.20 claimed one — the cheap-tier summary rebuild, and the general-tier
   distiller when and only when the author reworded at least two proposals.

**Identical old halves ("twins"), Sponsor-ruled: push all.** Two staged edits
can share a byte-identical `proposed_old` — that is two byte-identical
paragraphs in one essay, which refrains and liturgical repetitions really
produce. They are pushed, not held back and not refused: holding twins on the
local road while the rest went to the Doc would split one run across two
transports, the exact state the mode freeze exists to forbid. `filter push`
prints one informational line instead (§15.13, warn-never-block). Their join at
settle is POSITIONAL — the Doc carries no thread id, and Google's anchor store is
private, so there is nowhere to put one — which is correct for the text in every
case and best-effort for the attribution in exactly one: deleting a whole marked
span can attach the decline to the twin the author kept. The manuscript is never
affected; only the record of which of two threads proposing identical changes was
refused. Accepted by Sponsor ruling.

Verifying that invariant found a live defect in the critique pass and fixed it
first. `gdocs._locate_in_tab` returned the FIRST verbatim match of its needle, so
twins both resolved to the same span; the descending write order then nested the
second write inside the first (`<<<<old>>{{new}}>>{{new}}`), and
`threads.PENDING`, being non-greedy, left the remainder in the finished
manuscript as literal text. It now takes an `occurrence` index — defaulting to 0,
so `propose_change`'s hand-quoted span is byte-identical — which
`write_pending_forms` computes from the local paragraph list. That is correct
only in company with the descending write order, and the two are documented as
jointly correct or not at all.

MCP is unchanged, and that is the ruling rather than an omission. `gdocs` is not
imported by the MCP server at all, deliberately: agent-driven calls must never be
able to pop an OAuth consent window. The review half the Sponsor named is already
on MCP and is transport-independent; what doc mode owes chat is legibility, so
`list_filter_edits` reports the run's mode and whether forms are out, and names
the CLI verb that ends the pause.
