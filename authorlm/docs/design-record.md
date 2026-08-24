# Design record — ratified and BUILT

Designs that graduated from the backlog. The rationale is kept
here because the decisions (jurisdictions, rejected alternatives)
outlive the build. Implementation status of the co-writing loop
lives in autoregressive-writing-design.md §11.

## Tab hierarchy and reordering from toc.md (gdocs)
**Status (2026-08):** Built against `toc.toml` (`structure.py` —
`[[chapter]]` + `parent` tree; `gdocs.sync_tab_structure`). The prose
below is the original design note and still says `toc.md`; read every
`toc.md` here as `toc.toml`.

`reading_order` (structure.py) parses toc.md as a flat filename list —
indentation never enters the parse — and `ensure_master` (gdocs.py) sends
`addDocumentTab` with only a title, never `parentTabId`. Consequences:
nested toc entries push as flat sibling tabs, and toc order is honored
only at tab-creation time — reordering toc.md later never repositions
existing tabs. Design: toc.md indentation defines tab hierarchy (nested
entries become child tabs via `parentTabId`); `ensure_master` diffs the
Doc's tab tree against the toc tree on every push and repositions /
re-parents existing tabs to match, rather than only appending missing
ones. Ratified 2026-07-27; build when a manuscript actually nests its
chapters.


## Autoregressive paragraph-level co-writing loop
Full design in `autoregressive-writing-design.md` (ratified 2026-08-01).
Beat-by-beat chapter writing: author supplies outline + ≥1 opening
paragraph + TOC placement; system expands to a ratified beat plan
(DOC-style leaf beats), then proposes one unit at a time conditioned on
style law + Concept Graph + validated policies + accepted text, with the
author gating every beat and each verdict/rewrite captured as evidence
(beat-level granularity: ~15 recorded judgments per chapter vs 1 for a
one-shot draft). Economics rest on Common Core P2 via prompt caching:
layered payload ordered by volatility, breakpoints at law/frame/tail,
1h TTL on stable layers — ~85% saving on re-read tokens across a
30-beat chapter. Adopts Re³'s recursive payload assembly and DOC's
detailed outliner; replaces their controllers/rerankers with the
auditors and the author. Build after the auditors (they are the loop's
inner critics); the manual conversational version of the loop is
available today on request.


## Doc-comments ingestion (`doc pull` comments by default)
Google Docs comments are author reactions — evidence, not Doc state to
preserve. Push rebuilds a tab wholesale (deleteContentRange, gdocs.py:491),
so anchors die on every round trip; anchor preservation (diff-aware
transplant) was considered and rejected as deep bridge work solving the
wrong problem. Design, ratified in conversation 2026-08-03:
(1) **Harvest on pull, by default.** `doc pull` lists open comments
(Drive v3 comments.list; the existing drive.file token suffices — verified
live on the master Doc) and its output carries a compact "Comments to
address (N)" section: location, clamped quote, the author's words
VERBATIM (clamp the anchor quote, never the comment — R6.3 discipline).
Effect: a chat agent running the pull sees the comments in-band and
processes them unprompted, the same mechanism as the briefing's caught_up.
Skill rule to add at build: address every comment in pull output; when
acting on one, record the author's comment verbatim as the evidence/reason.
`--no-comments` as escape hatch.
(2) **Store per comment:** content verbatim; quotedFileContent (the
anchor); location in the transition convention `file#nearest-heading`,
derived by matching the quoted text against the pulled tabs (the master
Doc is ONE Drive file — comments.list is file-level, so tab/essay
attribution comes from the text match; ambiguous matches stored
unattributed and surfaced); the file association; comment id, author
identity, createdTime, replies (the id powers the resolve round-trip).
Storage home (guidance-like reviewable kind vs. evidence rows) decided at
build.
(3) **Resolve-with-receipt, never delete.** After ingestion, reply via
replies.create with action=resolved and a receipt ("Ingested into AuthorLM
as review evidence — <date>"). Resolved comments collapse out of the
margin (no orphan bloat after the next push) yet stay reopenable and
auditable in the Doc. Deletion rejected: irreversible destruction of the
author's words if ingestion ever has a bug.
(4) **No addressing prefix.** Process ALL open comments. The author
invented an "Authorlm:" prefix and omitted it on 2 of 5 comments in the
first live session — a prefix gate silently drops exactly the comments
written in deepest engagement. Future multi-collaborator filtering keys on
the comment's author identity (already in the API payload), not a textual
convention; classifying question/directive/reaction is the ingestion
step's job, not the author's.
First live harvest: 5 comments, 2026-08-03, ingested manually mid-session
(all five processed into the good-life.md revision batch, v70–v71).
**Built 2026-08-03** (same day — the friction was immediate): doc_comments
table (db.py); fetch_open_comments / locate_quote / ingest_comments /
resolve_comments_with_receipt (gdocs.py); pull harvests by default with a
"Comments to address" block and --no-comments escape (cli.py); skill rule
added; e2e Scenario DC covers location, verbatim storage, clamping,
dedupe, and the resolve round-trip against a fake Drive service.


## Tab reconciliation on pull (adopt / re-adopt hand-made Doc tabs)
Ratified and **built 2026-08-04**, prompted by the author drafting a new
essay directly as a Doc tab (redemption.md) — which the bridge could not
see: pulls only processed mapped tabs, unknown tabs risked being swallowed
into the preceding tab's section, and a later push would have created a
duplicate. Design: the Docs API (documents.get with tab content) is the
authority on what tabs exist; the stable key is the tab ID (survives
renames), titles are display names. On every pull, classify_tabs
(gdocs.py) reconciles: unknown '*.md' tab with no local file → ADOPTED
(new essay materializes on that same pull; output tells the author to
place it in toc.md); unknown '*.md' tab matching an existing file or link
→ RE-ADOPTED (relinked; the push-base is dropped, so content drift
surfaces as a CONFLICT — never the no-base Doc-wins default, because when
both sides hold content, choosing is the author's call); known id with a
changed title → renamed warning (titles must stay exact filenames);
duplicate of a live mapped tab, or two unknown tabs sharing a name →
ambiguous, left untouched; manifest/non-md tabs ignored. Every tab title
becomes a split boundary, killing the swallow hazard entirely.
Reconciliation is best-effort: a pull must survive a failed tab listing
(tabs_error warning). e2e: Scenario DC classify_tabs checks (6).


## Manuscript profiles + workspace Doc (declared context, second bridge)
Ratified via grilling and **built 2026-08-04**. Jurisdiction: profiles are
STATED CONTEXT about the project (market intelligence, positioning,
ambition) — neither prose (never observed: they live in `_profiles/`,
invisible to collect/extraction by the underscore rule), nor style law,
nor learned policy. Storage is files, not DB (the doc-editing requirement
made the bridge the dominant consumer, and the bridge speaks files); git
history is NOT assumed (manuscript dirs generally aren't repos).
Build: DocBridge descriptor generalizes the gdocs bridge — (meta_key,
root, doc_name, toc_sync, rich_manifest) — manuscript bridge = ("gdocs",
ms root, TOC sync on, rich manifest) vs workspace bridge =
("gdocs_workspace", _profiles/, toc sync OFF (tab order is free-form),
minimal manifest, "_profiles/" comment-location prefix). One code path
for push/pull/three-way/comments/adoption on both Docs; the workspace
Doc is created lazily on first `profile push`, in the same Drive folder.
Surfaces: CLI `profile list|show|set|push|pull` (set = whole-file
overwrite from stdin — profiles are declarations); MCP `get_profile`
(READ-ONLY, content verbatim — never compacted); one briefing line
(profiles + word counts). Skill rule: consult profiles FIRST for
positioning/audience/publisher questions; never binding on prose — the
path from insight to law is conversation → ratified style element or
policy (profiles inform the conversation that creates law, never bypass
ratification).
Deferred (author, 2026-08-04): capturing profile edit history in
AuthorLM's own version store — files are unversioned when the manuscript
dir isn't a git repo; build a small profile_versions capture (snapshot on
set/pull-change) if losing profile history ever hurts.

## Container tab (protected root identity, nested-everything)
Designed and built 2026-08-05, prompted by the live API finding that
root-level tabs reject ALL tabProperties updates with a server 500
(nested tabs accept everything — verified by matrix test; likely a
Google-side defect, undocumented). Design: one protected root tab per
bridge, titled exactly the manuscript's DB name (the identity of an
AuthorLM-managed Doc; renames are detected on pull and reported — the
API cannot fix a root tab). Body is a fixed sentinel ("Automatically
generated by AuthorLM. Do not delete."), rewritten on every push. All
content tabs nest under it — born movable — and ensure_master creates
new tabs there. The one-time migration of pre-existing root tabs is
manual (same root-tab bug); pull output lists the needed drags. The
walk/classify/toc-sync machinery needed zero changes: non-md tabs are
transparent to hierarchy by design. Depth note: Docs tabs nest 3 levels;
container + parts + essays consumes all three (author accepts,
2026-08-05). Manifest stays at true root (unmovable, harmless).

## Proposal learning loop (generic across triage queues)
Ratified and BUILT 2026-08-20, grilled end-to-end. Author's framing: dismissals are
recorded but nothing learns from them, so "paraphrased repeat: can come
back as a new proposal" — 18 open `note_update` proposals for `Nothing`
alone, four of them the same sentence with different punctuation.

### The finding: four loop mechanisms, six queues, no shared contract
Each stage of a learning loop exists exactly ONCE, in a different queue,
and none is wired to the others:
- **screen** (law cuts proposals before the author): only
  `placement.arbitrate` — and it already does the right thing, pulling law
  from style rows filtered by `aspect` and cutting with recorded reasons.
- **distil** (verdict → candidate rule): only `placement.distill_batch` →
  `seed_margin_candidate`.
- **generator feedback**: `extraction.triage_feedback` (concepts/edges) and
  `passes.learnings` (within one critique pass only).
- **firewall** (dedup): only `proposals._content_hash`, exact match.
`knowledge_proposals` has none of them: 41 dismissals produce
`proposal_review` evidence, and `grep proposal_review` returns exactly one
hit — the write site. Write-only since inception.

### Empirical grounding (SMSTTD, 2026-08-20)
- 763 open proposals (623 `note_update`, 54 `vanished`, 49 `alias`, 21
  `revival`, 11 `edge_reproposal`, 5 `variant_of_retired`); ~580KB
  serialized ≈ **145K tokens**, so `list_proposals` is uncallable from chat.
- `triage_feedback` is NOT unbounded — it is a fixed recency window
  (`LIMIT 40/20/30/20/15`) at 4,621 chars. **538 author decisions on
  record, 75 reach the extractor: 86% silently dropped by recency**, with
  no record that a lesson existed (377 rejected concepts → 40; 83 rejected
  edges → 20; 37 relation corrections → 15; 41 proposal dismissals → 0).
- The illustration loop has fired 13 times and closed zero times: 13
  `margin-thread` candidates, all 2026-08-10, none promoted, and no style
  row anywhere carries policy provenance.

### The two levels (they were repeatedly conflated; keep them apart)
- **Level 0** — the proposals themselves. What the author triages.
- **Level 1** — rules ABOUT level 0, distilled from dismissal reasons.
Three mechanisms act on level 0 and only one involves learning:
① **firewall**, deterministic token similarity, no model, collapses
paraphrase churn (18 `Nothing` rows → ~5); ② **belief**, the level-1 rule;
③ **screen**, applies ② to ①'s survivors. The author did ① by hand
already — three dismissals read "Duplicate of pr-…, already adopted".

### Design
**Storage — no new tables.** A heuristic is an ordinary `editorial_beliefs`
row for its whole life; the belief ledger never moves, so retraction works.
The author's three states map onto machinery that already exists:
`candidate` (distilled, inert) → `validated` = **implemented** (acting,
unblessed; auto-promoted by `_lifecycle_status`) → **accepted** =
graduated into `style_laws` via the existing convert verb. Rejected: a new
`triage_heuristics` table, a generic `beliefs` side-table, and a
two-linked-row scheme — all three either duplicated the belief machinery or
wrote unratified rules into declared law, violating the `style_laws`
invariant (see `domain-vocabulary.md`).

**Aspects** name the governed artifact: `concept-note`,
`concept-identity`; illustrations reuse the existing
`illustration-placement`. Never `triage-*`.

**Seam — thin `LoopSpec` Strategy in a registry, not a class hierarchy.**
The stages need to know only how to RENDER an item and where its law
lives; they never need `adopt()`. So the per-kind `describe`/`adopt`
if/elif chains are NOT touched — they encode queue semantics, not loop
semantics. `LoopSpec(key, table, aspect, evidence_type, render,
open_state, cut_state)` — one callable, everything else data, keyed
`"proposals/note_update"`. Matches the codebase grain (651 module
functions, 8 classes, all infrastructure; precedent: `Prompt` in
prompt_registry.py, `RULES` in triage_rules.py). An ABC hierarchy would be
14 classes and the only inheritance in the project. The four stages compose
as a pipeline, which is what structurally kills the two-call-paths bug
below. Commonalities across queues get extracted LATER, from two working
clients — not designed up front.

**Cuts are actions, not decorations.** The screen RESOLVES a proposal
(state flips, attributed `by: screen` + belief id), as `arbitrate` already
does. Machine cuts are NEVER recorded as author evidence — otherwise a
belief reinforces itself, raises its own confidence, and cuts harder.

**Folds, not deletions** (`list_proposals` returns a separate `folded`
key). This is load-bearing, not cosmetic: because machine cuts are not
evidence, `supporting` cannot move, so the ONLY channel that can ever
increment `contradicting` is the author expanding a fold and adopting
something the screen cut. Hard suppression would make auto-promotion
irreversible in practice while looking reversible in the schema.
`resolve_proposal` must therefore search folded rows too, and adopting one
must fire `reinforce(<belief>, "rejected")`. Response shape:
`{open: [...], folded: [{belief, statement, status, accepted: false,
count, sample}]}` — ~145K tokens → ~5K, with `accepted: false` carrying the
promoted-but-not-blessed provenance the author asked to see on every
triage operation. `verbose=True` per the existing MCP convention.

**Thresholds are per-source**, not global: `triage-*` = 2, keep
`episode-analysis` = 3. The two evidence kinds are not comparable — a
triage belief is distilled from explanations the author TYPED; an
episode-analysis belief is inferred from a diff with no explanation at all.

**Matching is folded into the distiller** (level 1), deterministic token
similarity only at level 0 (the firewall must be free). The distiller is
handed the existing beliefs for the source/aspect and answers
reinforce-or-create; no embeddings, no second similarity threshold, and the
belief list stays short precisely because matching works. Non-determinism
at level 1 accepted. Both distiller prompts migrate from inline constants
to `authorlm/prompts/policy-distill.md` and `margin-distill.md` — the
registry's own stated policy for inline prompts "when its verb is next
touched".

### Prerequisite bugs (all verified, all independent of the design)
1. `proposals._record_evidence` truncates `summary + reason` to 200 chars
   and the reason comes SECOND — the live `Nothing` row ends mid-word at
   "It flattens an ontologica". The verbatim reason exists nowhere else.
   `placement.py` does it correctly, in `metadata.explanation`. A distiller
   built on this evidence would train on truncated reasons.
2. MCP `triage_illustrations` calls `placement.decide(..., llm=_llm())`,
   firing `seed_margin_candidate` per verdict and bypassing the ">= 2"
   guardrail; `distill_batch` has ZERO MCP call sites. The CLI does it
   right (`llm=None` per verdict, one batch call). This produced the 13
   junk candidates — six paraphrases of "be specific" — so the 0-for-13 is
   a bug's output, not the author's judgment. Discard them, do not migrate.
3. `seed_candidate_policy` matches beliefs by `lower(statement) =
   lower(?)` — **exact string equality**. Paraphrases spawn duplicate
   beliefs at `supporting=1` that never reach any threshold. This, not the
   threshold, is why nothing ever validated; it is `_content_hash` one
   layer up.
4. `list_proposals` returns every open row with full payload plus
   `describe()` output restating it.

### Scope of first build
`note_update` + `alias` + illustrations — three `LoopSpec`s across two
tables, validating BOTH axes of variation (per-kind within a queue,
per-queue) and covering 672 of 763 open items (88%). Illustrations is
migration, not new build: `arbitrate` is already the screen and
`distill_batch` already the distiller; re-expressing them as the shared
pipeline is what fixes bug 2 structurally. One client would prove nothing;
all six is the speculative generality deliberately rejected.

**Simulation before tuning:** the author asked to fix semantic matching
FIRST, then replay the real corpus (41 proposal dismissals, 18 illustration
verdicts, 130 episode-analysis beliefs) to measure the actual N-beliefs vs
M-items curve, and only then decide whether a volume-adaptive threshold is
needed. Author's intuition was N ~ O(log10(M)) with thresholds LOWERED at
high volume; those pull opposite ways (a lower bar makes N grow FASTER).
Resolution: belief COUNT is governed by distiller matching, latency-to-act
by the threshold. With matching fixed, high volume already accumulates
faster on the same belief, so the log curve comes free. An adaptive
threshold is CUT for now with a standing reason: it would mask a matching
regression — rising volume would lower the bar and promote junk faster,
which is how the bug becomes permanent.

### What shipped
`authorlm/loop.py` — the four stages once, for every queue. `LoopSpec` is a
frozen dataclass in a registry (`render` for the screen, `dedupe_render` for
the firewall, an aspect, a source); `proposals.py`'s per-kind `describe`/
`adopt` conditionals were NOT touched, as designed. Three specs registered:
`proposals/note_update`, `proposals/alias`, `illustrations`.

New surfaces: `screen_proposals` (MCP); `list_proposals` gained
`kind` / `proposal_id` / `belief` / `limit` / `verbose` and now returns
`{open, counts, folded, truncated}`. New prompt files, per the registry's
own migrate-when-touched rule: `belief-distill.md`, `margin-distill.md`,
`triage-screen.md`.

Renames landed with the build: `editorial_policies` → `editorial_beliefs`,
`style_elements` → `style_laws`, `policies.py` → `beliefs.py`, plus the
author-facing verbs (`list_beliefs`, `retire_belief`, `merge_beliefs`,
`convert_belief_to_law`, `add_style_law`, …) and the persisted enum values
`policy_curation|policy_revival|policy_reminder` → `belief_*`. Migration is
`db._migrate_beliefs_and_laws` (idempotent; SCHEMA creates the new tables
empty, so each shell is dropped and the populated original renamed over it).
Ontology in `docs/domain-vocabulary.md`, summarised in AGENTS.md.

Tests: `tests/test_loop.py` (45 checks) covers the firewall, per-source
thresholds, MATCH-vs-NEW distillation, retired-belief revival, batch
distillation, screen attribution, folds, and the retraction path. Suite:
212 + 381 + 45 + 68 + 39 + 24 + triage_app, all passing.

### Reconciliation — the second axis (added 2026-08-21)
The firewall asks "have I been told this before?" (proposal vs proposal).
It ran, folded 56, and the queue stayed at ~769. Measurement showed why:
the remaining proposals were neither duplicates (52% of concepts had
exactly ONE open proposal) nor cosmetic (median similarity of proposed to
current note: 0.20). They were answers to a question that had stopped
being a question.

RECONCILIATION is the other axis: proposal vs THE WORLD IT WAS RAISED
AGAINST. `placement.sweep_stale` had always done this for illustration
anchors; nothing did it for knowledge proposals. On the live queue:

```
  alias               considered   49  satisfied   1  orphan 27  re-based   0  live 21
  edge_reproposal     considered   11  satisfied   0  orphan  4  re-based   0  live  7
  note_update         considered  617  satisfied 170  orphan142  re-based 298  live  7
  revival             considered   30  satisfied   0  orphan  0  re-based   0  live 30
  vanished            considered   54  satisfied  35  orphan  0  re-based   0  live 19
  variant_of_retired  considered    8  satisfied   0  orphan  0  re-based   0  live  8
  → 379 settled, 298 re-based; 769 open becomes 390 live
```

Three verdicts settle a proposal without judgment — SATISFIED (what it asks
for already happened: the note already says exactly this; the concept is
already retired), ORPHAN (what it refers to is gone or retired) — and one
deliberately does NOT: STALE proposals are RE-BASED, never dismissed. Only
19 of 347 collapse once compared against the current note, and among the
rest are proposals that would REGRESS the note; they read as improvements
only against the stale base they carry. The live `Carl Jung` case proposes
dropping *Memories, Dreams, Reflections*, which the author had since added.

Like screen cuts, reconciliation is pipeline hygiene and writes NO author
evidence — nobody judged anything. It must also bypass `proposals.dismiss`,
whose `vanished` branch re-declares the concept as a placeholder; a
reconciled `vanished` proposal would otherwise un-retire what it settled.

`LoopSpec.reconcile` is the hook, data-shaped like `dedupe_render`. Four
further kinds (`vanished`, `revival`, `edge_reproposal`,
`variant_of_retired`) registered specs for this stage ONLY: their screen
half stays inert because `active_law` finds no belief for those sources and
no distiller is wired to them.

Order matters, cheapest first: ORPHAN → SATISFIED → RE-BASE → redundancy
(firewall) → identity (alias clusters) → the learned screen. Each stage
shrinks the input to the next, and only the last two need the author.

Rejected: extending the firewall ACROSS targets to catch fragmented
concepts. 24 near-name pairs carry 173 open proposals (28%), but pooling
them unlocks zero additional folds, and name similarity is anti-correlated
with sameness in this manuscript — `Seven Sermons` vs `Seven More Sermons`
scores 1.00 and is its most load-bearing distinction, as do `Mutable` vs
`Immutable` and `sṛṣṭi`/`sthiti`/`saṃhāra`. Identity must propose, never
decide.

### First live run, end to end (2026-08-23)
```
  770 open
  -> reconcile           391   (379 settled, 298 re-based; deterministic)
  -> beliefs re-derived   49 beliefs from 130 explanations, 12 acting
  -> author judged         3 retired, 3 ratified into law, 2 kept as beliefs
  -> screen              182   (211 cut, 15 LLM calls, 27K in / 89K out)
```
Two findings the run produced that the design had not anticipated.

**The screen must batch.** The first attempt sent all 305 note_update rows in
one call and timed out with zero cuts recorded. `SCREEN_BATCH = 25` plus a
400-char cap per rendered row; a failed batch is caught and skipped so one
bad reply cannot lose the rest.

**A sound law can be a near-universal screen.** `se-b2ad5` ("a revised note
must not replace a specific, context-bound settled note with a generic
gloss") was ratified on the strength of a correct statement and a correct
example, and then cut 210 of 305 — 69% — on its own. Spot-checking five:
'steadfast tree' and 'Sameness' were right; 'Archivist' (the proposal ADDED a
linkage) and 'Metamorphosis' were over-cuts. The principle is sound; as a
screen criterion it is nearly always satisfiable, because almost every
rewrite loses SOME specific content. The author accepted the cuts — 182 open
is triageable — and the law stays active, so it will cut future extractions
at the same rate. Filed as it-44f3f98413be: ratification should preview the
blast radius over the open queue before promoting, and a law cutting >50%
should require explicit confirmation.

Also fixed here: `folded()` queried every screen cut in the manuscript for
EVERY proposal spec, so one group was reported six times, five of them
resolved against the wrong aspect and rendered "(law retired)".
`LoopSpec.kind` scopes queries to the kind a spec owns.

### Measured, not guessed (tools/simulate_loop.py)
Both thresholds the design deliberately left open were settled by replaying
the live corpus against a COPY of the database.

**Firewall = 0.72 Jaccard over content words.** Sweeping 1054 real proposals:
0.50 suppresses 20%, 0.72 suppresses 8.0% (76/849 note_update, 8/91 alias),
0.90 suppresses 3.2%. Every pair sampled in the 0.72–0.80 risk zone was a
genuine restatement; the first FALSE positive sits at 0.71 ("the spiritual
realm, equally existent with the corporeal realm" vs "…with the Realm of
Qualities"), so 0.72 sits just above it. 0.85 would lose 42 true
restatements.

The sweep also caught a real bug in the first cut: comparing `render` output
scored two unrelated re-definitions of 'The Dead' at 0.92, because the
summary line and the CURRENT note are identical for every proposal against
the same concept and the shared boilerplate floated every pair toward 1.0.
Hence `dedupe_render` — the firewall compares ONLY what distinguishes two
rows. Suppression fell from a flattering 22.5% to an honest 8.0%.

**No adaptive threshold.** 42 real dismissal explanations through the live
distiller yield 7 beliefs, 4 validated:
```
M:   5   10   15   20   25   30   35   40   42
N:   3    3    4    4    5    5    5    6    7
```
M grew 8.4x while N grew 2.3x, against log10(42)/log10(5) = 2.32 — the
author's O(log10(M)) intuition, confirmed. Belief count is governed by
distiller matching, not by the promotion bar, so volume needs no special
handling: more dismissals accumulate faster on the SAME belief. Caveat: most
replayed reasons were the pre-fix truncated ones, so live performance should
be better, not worse.
