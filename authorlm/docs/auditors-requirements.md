# Auditors — Requirements

Requirements for the retrospective auditors (style drift, concept/ontology
pollution, policy conformance) and the seeding-time jurisdiction router.
Distilled from the author's rulings in conversation (2026-07-31), the
policy-migration exercise that preceded them, prior sessions on token
efficiency and the style system, and the RFCs. Requirements, not design:
the shape of each analyzer is deliberately unspecified.

## Background and evidence base

- **Jurisdiction rule** (ratified during the 2026-07-31 policy migration):
  *style* governs how prose reads (timeless, per-scope); *policy* governs
  what/where/when-revising decisions; the *Concept Graph* governs what is
  true. The migration found 34 of 46 learned policies were misfiled by
  this rule (17 style-shaped incl. duplicates, 3 graph facts, garbage,
  dupes) — the misfiling is systematic, not incidental, because episode
  analysis observes *edits*, and edits express all three jurisdictions.
- **The learning loop is half-idle without reviews.** Policies are seeded
  constantly (episode analysis at every complete_intent) but reinforced
  only when guidance is reviewed. The skill now mandates one get_guidance
  per session; auditors are the second reinforcement channel.
- **Token-efficiency findings** (this session, 2026-07-31): a full
  `get_briefing` returned 622 KB; `collect_revision` for three typo fixes
  returned 61 KB, nearly all of it `gaps_before`/`gaps_after` duplication;
  `get_concepts` on a hub node returned ~70 full edge rows. Roughly half
  of every record is boilerplate (`version`, `created_by`,
  `schema_version`, `metadata`, `manuscript_id`) never used
  conversationally.
- **Common Core P2** (specs/Common Core RFC §1.12A): "Every LLM call: a
  defined decision, a curated payload, a cache key. Token efficiency is
  the goal; avoidance is not. Identical payloads replay from cache; only
  changed payloads spend."
- **Style system session** (2026-07-29): the style workflow was designed
  to be "token-cost-sensitive," mapping each file to an effective guide
  via inheritance; guides exist (house → sermons / essays) with per-file
  attachments and an enforced aspect vocabulary (citation, figure,
  formatting, lexicon, register, rhetoric, structure, syntax, tone).
- **MVP session** (2026-07-18): every command that spends LLM calls
  reports # of live calls and input/output tokens. Auditors inherit this.

## R1 — Common analyzer contract

- **R1.1** Auditors propose, never execute. Findings arrive as proposals
  for the author's verdict; no auditor edits manuscript text, style
  elements, policies, or the graph.
- **R1.2** Auditors run on the *delta* — the changed/new paragraphs of a
  collect — never the whole manuscript, except in an explicit on-demand
  full-audit invocation (R7.3; entry points listed in R8 'On-demand triggers').
- **R1.3** Every finding carries an explanation tracing it to the rule it
  checked (style element id, policy id, edge/note id) — same standard as
  guidance (§11.6): the why matters more than the what.
- **R1.4** Abstention is a valid output (§11.5). A clean delta produces no
  findings and says so cheaply.
- **R1.5** No re-litigation: a finding the author has ruled on is never
  re-proposed. Dedupe by stable semantic key (rule id + target), not
  verbatim text — the lesson of the proposal near-dupe audit (verbatim
  hashes let reworded duplicates through; node-pair-level bans are needed
  for edges).
- **R1.6** Author verdicts are recorded with their reasoning verbatim,
  as high-weight evidence (§19.11).

## R2 — Style drift auditor

- **R2.1** Compares delta prose per-aspect against the file's *effective*
  guide (inheritance chain, nearest scope first, overrides applied). The
  free-text `notes` on elements (inspect/avoid hints) steer the check.
- **R2.2** Two finding types: (a) *drift* — prose contradicts a ratified
  element; (b) *candidate element* — a consistent habit the guide doesn't
  yet record (style learning, see R5).
- **R2.3** Verdict semantics for drift: **ratify-as-change** (the guide
  was wrong / taste moved — update element), **accept-as-override**
  (deliberate local exception), **reject** (prose drifted — author fixes
  the text; the auditor still doesn't).
- **R2.4** Files without an attached guide are skipped and *named* in the
  result (currently preface.md, christic.md, indic.md, metaphysic.md, toc.md) —
  silent skipping would read as "audited clean."
- **R2.5** Register boundaries are law: EME never bleeds into essays,
  essay register never into sermons (existing ratified elements). Cross-
  register movement of text is a drift finding, not a new element.

## R3 — Concept auditor (ontology pollution)

- **R3.1** Compares claims in delta prose against settled graph
  knowledge: ratified notes (author's definitions), declared edges, and
  rejected/refuted positions. Jurisdiction: constrains what the text may
  CLAIM — never how it reads (that is R2's job).
- **R3.2** A `refutes` target must never be endorsed; a rejected edge's
  claim reappearing in prose is a finding.
- **R3.3** Aliases resolve before comparison (aliases are the same
  concept), but **metaphor is not identity**: the Flame/Shadow ruling —
  "Flame vs Shadow is a metaphor for the Fields vs Qualities. Flame is
  not an alias for Field" — must be representable, so the auditor cannot
  treat figurative identification sentences as literal same-concept
  claims (the alias-proposal failure mode observed in extraction).
- **R3.4** Verb-noun discipline from the author's rulings: "To choose is
  to distinguish. Choice is a noun, to choose is a verb" — argumentative
  identifications in prose are not identity claims about concepts.
- **R3.5** Reference material: the "Metaphysical Guardrails" of the
  author's style profile (Fields possess zero qualities; Realm of
  Qualities is passive; Nothingness and Dharma co-equal; redemption is
  seized, not received) — see design-backlog "Ontology-pollution
  analyzer."

## R4 — Policy conformance auditor

- **R4.1** Retrospective checking applies only to *checkable* policies —
  those whose satisfaction is visible in finished text ("challenge
  common assumptions before presenting a new framework"). Decision-time
  policies ("place explanations where they best serve narrative flow")
  are guidance material, not audit material; the auditor must not
  re-litigate placement decisions after the fact.
- **R4.2** Findings are evidence, both directions: a confirmed violation
  the author *fixes* supports the policy; a violation the author
  *endorses* ("the text is right") is contradicting evidence that weakens
  it. This closes the loop the same way review_suggestion does.
- **R4.3** **Policies need scope.** The 2026-07-31 session produced the
  seeded candidate "Apply policies only within their defined scope" from
  the author's rejection of a preface-scoped policy surfaced against an
  essay intent. The auditor (and the reminder mechanism) must respect a
  scoping mechanism — plausibly mirroring style attachments (per-file /
  per-guide / manuscript-wide).
- **R4.4** **Policies can suppress structural findings.** The validated
  policy "the preface is not the repository of concepts; it's merely a
  window" directly contradicts prerequisite-gap findings caused by
  preface alias mentions (e.g. The Dharma in preface.md making Field of
  Choice "appear" before Nothing). Gap detection — and any auditor —
  must consult validated policies and suppress finding classes they rule
  out, reporting the suppression count rather than the findings.
- **R4.5** Candidate policies may surface as at most gentle questions
  (existing budget: ≤3 reminders, one review per policy per session, so
  evidence stays independent across sessions §11.2); validated policies
  audit assertively.

## R5 — Jurisdiction router at seeding time

- **R5.1** When episode analysis or a review explanation yields a
  pattern, classify it *at birth*: how-prose-reads → candidate **style
  element** (proposed for ratification, carrying evidence, never
  auto-ratified); what/where decision → candidate **policy**; claim
  about concepts → **graph proposal**. Today everything becomes a
  policy; the migration exists because of that.
- **R5.2** Candidate style elements get a lifecycle mirroring policies
  (evidence, confidence, author ratification) — ratified elements remain
  binary law.
- **R5.3** Curation tools (policy retire/merge/convert, 2026-07-31)
  remain the escape hatch for misrouted items; routing reduces their
  need, it does not remove them.

## R6 — Token efficiency

- **R6.1** Curated payloads (Common Core P2): an auditor's LLM call
  contains the delta paragraphs plus only the rules in scope for the
  changed files (effective guide elements; policies in scope; the graph
  neighborhood of concepts mentioned in the delta) — never the full
  graph, full policy table, or full guide set.
- **R6.2** Cache-keyed: unchanged (rules, delta) pairs replay from cache;
  a clean round-trip pull (no delta) costs zero LLM calls.
- **R6.3** Compact results: findings only, as compact projections — no
  raw DB rows, no before/after state dumps (the gaps_before/gaps_after
  lesson). Deltas, not snapshots.
- **R6.4** Every auditor run reports # of live LLM calls and token usage
  (MVP convention).
- **R6.5** Bounded batch: an auditor surfaces at most a handful of
  findings per collect (rest summarized by count, retrievable on
  demand) — the author's attention is the scarcest resource in the loop.

## R7 — Workflow placement: automatic vs on-demand

- **R7.1** **Automatic at collect** (every save/pull, delta-scoped):
  extraction (existing), style drift (R2), concept pollution (R3).
  Rationale: these check *the text just written* while context is warm,
  and collect is already the observation choke-point.
- **R7.2** **Automatic at complete_intent / session end:** episode
  analysis (existing) with the jurisdiction router (R5), plus policy
  conformance (R4) over the episode's accumulated transitions — the
  natural retrospective unit is the finished piece, not each keystroke.
- **R7.3** **On-demand only:** whole-manuscript or whole-file audits
  (`authorlm audit [file]`, and an MCP equivalent) — e.g. after
  attaching a guide to a previously uncovered file, or before
  publishing. Full audits are expensive and interruptive; they must
  never run implicitly.
- **R7.4** **Never automatic:** any text mutation; style-element
  ratification; policy promotion beyond evidence arithmetic.
- **R7.5** Findings queue as proposals and NEVER block the author's
  save/pull; auditing is asynchronous to writing. Surfacing follows the
  session rhythm: briefing (session open) → guidance incl. reminders
  (once per session, per skill) → audit findings reviewed
  conversationally or via a keystroke triage loop (concept-triage
  pattern) when the pile is bulk.

## R8 — Trigger inventory (exhaustive)

"At collect" is not one command — collect is the observation choke-point
reached from six entry points, and auditors hooked there fire from ALL of
them identically:

| Entry point | What the author did | Auditors triggered |
|---|---|---|
| `authorlm collect` | explicit snapshot | extraction + style (R2) + concept (R3) on the delta |
| `authorlm doc pull <file>` | edited in Google Docs, pulled back | same — pull normalizes, writes, collects |
| `authorlm watch` | saved a file while the watcher runs | same, auto-collect on change |
| `authorlm shell` (open) | started an interactive session | catch-up collect, same auditors on whatever changed since last time |
| MCP `collect_revision` | told the assistant "I saved / I wrote" | same |
| MCP `get_briefing` / `get_guidance` | opened a conversation / asked for guidance | both catch-up-collect internally (`caught_up`), same auditors |
| — clean round trip / no delta | nothing changed | **zero LLM calls, zero findings** (R6.2) |

Episode-boundary triggers:

| Entry point | Auditors triggered |
|---|---|
| `complete_intent` (CLI `intent complete`, or MCP) | episode analysis + jurisdiction router (R5) + policy conformance (R4) over the episode's transitions |
| `close_session` / `session end` | closes open episodes, then same as above for each |
| `authorlm analyze` | manual re-run for episodes the automatic path missed (e.g. no LLM configured at the time) |

On-demand triggers (never implicit):

| Entry point | Scope |
|---|---|
| `authorlm audit <file>` / MCP equivalent | one file, full text against its effective guide + graph + in-scope policies |
| `authorlm audit --all` | whole manuscript; expected use: after attaching a guide to an uncovered file, pre-publication |

## R9 — Author workflow walkthrough (normative)

The auditors must fit this session shape without adding ceremony; the
author's actions are the ones already habitual (nothing new to remember):

1. **Session open** (chat or `shell`): briefing runs → catch-up collect →
   any delta is audited (R7.1) → briefing reports: prose changes first,
   then learning news, then **audit findings as proposals** — bounded per
   R6.5, suppressed classes reported as counts (R4.4).
2. **Guidance, once per session** (skill duty): policy reminders and
   structural findings surface as direct questions; verdicts recorded
   with reasoning verbatim. This is reinforcement channel #1.
3. **Declare intent → write.** Writing happens in Google Docs or locally;
   nothing audits keystrokes; nothing ever blocks a save (R7.5).
4. **Pull / collect:** each pull triggers the delta auditors. Findings
   queue; the assistant narrates the diff first (house rule), findings
   second. Verdicts can be given conversationally in any wording —
   recording them is the surface's job, not the author's.
5. **Complete intent:** episode analysis + policy audit of the finished
   piece; the assistant relays decisions/patterns and any conformance
   findings; verdicts are reinforcement channel #2.
6. **Bulk piles** go to the keystroke triage loop (`concept triage`
   pattern) — a future `audit triage` must exist for finding piles;
   anything the author hesitates on returns to conversation where the
   why gets recorded.
7. **Session close:** open episodes analyzed; learning velocity reported.

Constraint restated as a requirement: the author never invokes an auditor
in the normal loop — auditors ride existing actions (open, pull, intent
complete, close). The only new verbs the author ever types are the
optional on-demand audits (R8) and the triage loop (R9.6).

## Open questions

1. Scoping mechanism for policies (R4.3): file/guide/manuscript, or
   free-form predicates? Who declares scope — author at curation time,
   or the router at seeding time?
2. Should `foreshadows` edges generate prerequisite gaps at all? A
   foreshadow is arguably a *deliberate* early mention. (Author leaned
   this way when rejecting Nothing —foreshadows→ Field of Choice.)
3. Relation vocabulary gaps: no `constrains`; no way to mark two edges
   as one dual act (Nothing permits both Fields as a single cleaving) —
   currently approximated by reification (First Cleaving). Does the
   concept auditor need dual-awareness, or does reification suffice?
4. Does the policy auditor share one LLM pass with episode analysis
   (same transitions, same moment) or run separately?
5. Unattached-file coverage (R2.4): attach guides to preface/christic/
   indic/metaphysic first, or let the auditor's skip-report drive that?
