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
