# Critique Pass — Design Reference

Ratified 2026-08-13 in a grilling session with the author. This document is
the build reference for (a) importing external critique into AuthorLM and
(b) the essay-by-essay edit machinery that proposes revisions against it.
First feedstock: `SMSTTD_Editorial_Analysis.docx` (developmental editorial
report, 26 revision units, ~260 prioritized changes, dated 2026-08-13).

Related designs: `autoregressive-writing-design.md` (the beat loop — the
critique pass borrows its pin/state-machine genome at essay granularity),
`margin-threads-design.md` (pending-form grammar, surgical push, verdict
semantics — generalized here).

---

## 1. Principles (ratified)

1. **Provenance before ingestion.** AuthorLM's learning model rests on the
   author's verdicts being the evidence stream. External critique enters as
   a *candidate pile with critic provenance*; nothing becomes standing law
   or a sanctioned work order until the author triages it. Rejections (with
   reasons, verbatim) are kept forever — they are evidence, often the most
   valuable kind.
2. **General machinery, not a one-off.** The intake works for any future
   critic — human or machine — and any manuscript.
3. **The contract is confirmed-only.** The edit pass consumes only
   author-confirmed rules, guidance, policies, and intents. Proposals never
   reach an LLM pass, under any flag.
4. **The author reviews in the Doc; the shell triages; chat stays
   sovereign.** Machine edits are staged, triaged in the CLI, written to
   the Doc as pending diffs, post-edited by the author, and resolved by an
   explicit verb.

---

## 2. Provenance

### 2.1 `sources` registry (new table)

```
sources
  id          TEXT PK        -- src-...
  kind        TEXT NOT NULL  -- author | critic | system
  name        TEXT NOT NULL  -- "nagarkar", "SMSTTD Editorial Analysis"
  detail      TEXT           -- e.g. "literary critic, docx, 2026-08-13"
  created_at  TEXT NOT NULL
```

One row per distinct voice. A later critique from the same critic is a new
row (new document, new date) — traceability either way.

### 2.2 `source_id` column

Added to: `declared_intents`, `concept_nodes`, `concept_edges`,
`style_elements`, `editorial_policies`, `evidence`.

- **Semantics:** whose *judgment originated the content*. Ratification is
  a separate fact and already has a home — triage verdicts land in
  reviews/evidence as today. A critic-sourced intent the author accepts
  remains critic-sourced, author-ratified.
- `created_by` keeps meaning *operator* (who ran the tool). It is not
  provenance.
- **No back-compat** (house rule): real column, no metadata fallback.
  Backfill existing rows by hand: style elements and policies → author;
  concepts/edges → author or system (existing `metadata.origin: extracted`
  says which); evidence → author.
- **Badging:** briefings and guidance visibly mark non-author-sourced
  material (e.g. "12 critic-sourced intents pending triage") so critic
  input never silently blends into the author's record.

---

## 3. Critique intake

### 3.1 Item taxonomy (five kinds, ratified)

| # | Kind | Example from the report | Lands as |
|---|------|------------------------|----------|
| 1 | Standing law | "Separate commentary from proof" | *Proposed* style element at the right guide level (house / part / essay) |
| 2 | One-time revision task | "Cut the section by at least half" | *Proposed* intent, scoped |
| 3 | Structural decision | Reorder essays; new "Grammar of Choice" essay | *Proposed* intent, manuscript-scoped |
| 4 | Epistemic / claim-level | "Do not claim Nothing is irrefutable" | Unconfirmed `objection` concept node (+ contradiction machinery), source_id set — rides the existing concept-triage loop |
| 5 | Preservation strand | Strengths: symbolic vocabulary, voice | *Proposed* style element, house level, phrased as preservation ("the recurring images are load-bearing; edits preserve them") |

No new intake table: intents, style elements, and concept nodes with
`source_id` + a pre-acceptance status *are* the intake surface.

### 3.2 Intent lifecycle & scope (schema changes)

- **New status `proposed`** — birth state for any intent whose source is
  not the author. Author-declared intents stay born-`active`.
  Verdicts: `proposed → active` (accepted) or `proposed → rejected`
  (kept forever, reason verbatim, evidence recorded).
- **New nullable `scope` column** — a file path. Resolution uses the toc
  tree (`toc.toml` `parent` chain), which already encodes the hierarchy:
  - `scope = <essay>.md` — narrow;
  - `scope = <part-opener>.md` (e.g. `ascending.md`) — chapter-wide;
  - `scope = NULL` — manuscript-wide.
  "Intents relevant to essay E" = intents whose scope ∈ {E, E's toc
  ancestors, NULL}. Same compositional logic as the style guide chain; no
  new hierarchy invented.
- Critic units finer than a file (Sermon One … Seven are sections of
  `sermons.md`) import scoped to the file with the unit name preserved in
  the intent text.

### 3.3 Triage

- `authorlm critique triage [-f <essay>]` — rapid keystroke-per-item loop
  over proposed intents (accept / reject-with-reason / edit-then-accept),
  mirroring `concept triage`. File-scoped so a sitting covers one essay's
  ~10 items.
- Proposed style elements triage in the same sitting (accept → active
  element at its level; reject → recorded with reason).
- Conversational triage in chat remains sovereign for anything the author
  hesitates on; explanations recorded verbatim.
- Every verdict is evidence and feeds the policy distiller as usual.

---

## 4. Summaries (`essay_summaries`, new table)

```
essay_summaries
  manuscript_id  TEXT NOT NULL
  file           TEXT NOT NULL   -- toc [[chapter]] entry
  summary        TEXT NOT NULL   -- ~150–250 words
  source_hash    TEXT NOT NULL   -- hash of the essay text summarized
  upstream_hash  TEXT NOT NULL   -- hash of concatenated prior summaries
  upstream_stale INTEGER DEFAULT 0
  created_at     TEXT NOT NULL
  UNIQUE (manuscript_id, file)
```

- **Autoregressive, reading order** (toc order, front matter included).
  Each summary is conditioned on all prior summaries.
- **Content bar (ratified):** the summary is the *editor's working memory*
  of the rest of the book — argument moves and central claims in the
  book's own terms of art, concepts introduced (grounded by the graph's
  `file=` slice, passed to the prompt), promises made forward, debts owed
  backward, motifs deployed, and what the essay deliberately does NOT
  re-explain (this powers the "don't explain everything four times" rule).
  Terse and dense; every sentence earns its place.
- **Staleness: mark, don't cascade.** A collect that changes essay N marks
  N's summary stale (`source_hash` mismatch) and flags downstream rows
  `upstream_stale`. The edit pass *refuses* to run on a `source_hash`
  mismatch in any summary it consumes; it *tolerates* `upstream_stale`.
- **Rebuild at confirm:** `critique resolve` rebuilds the essay's summary
  as part of the gate, so processing in reading order keeps every "before"
  summary fresh by construction.
- `authorlm summarize --rebuild` — full sequential pass (campaign start,
  and after structural changes: reorders, new essays).
- **The summarizer prompt is a checked-in repo artifact** (reviewable and
  ratifiable like style law), not buried in code.

---

## 5. The edit pass

### 5.1 Preflight gate (ratified)

`critique run <essay>` assembles the essay's effective contract and counts
anything unconfirmed touching it: proposed critic/style items in the guide
chain, proposed intents in the scope chain, candidate policies in scope.
If any exist → print them and **refuse to run**. `--force` proceeds — but
**unconfirmed items are never included in the pass context, forced or
not**. Force means "run without the pending items," never "run with
unratified ones."

### 5.2 Context (the full package)

1. The essay's full text, paragraphs explicitly numbered.
2. Before-summary block and after-summary block (from `essay_summaries`).
3. The rendered effective style guide for the file (law).
4. File-scoped concept slice with ratified notes.
5. Active intents via the scope chain (the accepted critic work orders for
   this essay + inherited chapter/manuscript intents).
6. Validated policies.
7. Learnings recorded earlier in the same pass.

The editor prompt is a checked-in repo artifact.

### 5.3 Output contract

```json
{
  "paragraphs": [
    {"n": 1, "echo": "first ~5 words of paragraph 1",
     "action": "keep"},
    {"n": 2, "echo": "…", "action": "replace",
     "new": "…", "why": "one sentence", "intent_id": "di-…"}
  ],
  "insertions": [
    {"after": 4, "new": "…", "why": "…", "intent_id": "di-…"}
  ],
  "suggestions": [
    {"kind": "glossary|structural|other", "text": "…", "intent_id": "di-…"}
  ]
}
```

- One element per source paragraph, in order; `keep` = unchanged (the
  spec's "empty element").
- **Echo validation:** every element must echo its paragraph number and
  the paragraph's opening words; the harness validates both against the
  source and rejects/retries the whole response on any mismatch.
  Misalignment is catastrophic and silent otherwise.
- `old` text for each replace is taken verbatim from the source paragraph
  (never from the model), so pending-form writes cannot drift.
- Insertions anchor after a numbered paragraph.
- Non-paragraph `suggestions` become **proposed intents** (source =
  system, lineage to the critic intent that prompted them) and land in the
  triage pile — the machinery feeds itself; nothing is dropped.
- `why` should name the intent/policy/precedent the edit serves — verdict
  evidence hangs off it, as with `write propose --why`.

### 5.4 Models (config, LiteLLM ids)

```toml
[critique]
editor_model     = "openai/gpt-5.6-terra"      # author's preference; swappable
summarizer_model = "gemini/gemini-2.5-flash"    # or openai/gpt-5.6-luna
```

- Editor: frontier tier, never the cheap tier. Cost is trivial at any
  frontier tier (~20K in / ~8K out per essay ≈ $0.14 Terra / $0.22
  Sonnet 5 / $0.30 Opus 5; full campaign $4–8) — pick on quality. Worth
  A/B-ing one essay across models early and choosing by taste.
- Pricing snapshot 2026-08: GPT-5.6 Sol $5/$30, Terra $2/$12, Luna
  $0.20/$1.20; Claude Opus 5 $5/$25, Sonnet 5 $3/$15, Haiku 4.5 $1/$5.
  GPT long-context surcharge (Sol → $10/$45) is not reached at essay-pass
  sizes.

---

## 6. Thread generalization & the Doc round trip

### 6.1 `doc_threads` origin polymorphism (schema change)

Replace the `comment_id` join key with:

```
origin_type  TEXT NOT NULL   -- author_comment | critique
origin_id    TEXT NOT NULL   -- Drive comment id | critique proposal ref
```

Existing rows migrate by hand (`author_comment` + old comment_id); the
dedicated `comment_id` column dies (no back-compat). Room for future
origins without schema change.

### 6.2 Findings: Doc comment anchoring (experiments, 2026-08-13)

Recorded so nobody retries this:

- Drive API `comments.create` without `anchor` → **unanchored** comment
  (quote box only, no span attachment).
- Drive persists any supplied `kix.*` anchor string, **but the Docs UI
  never resolves third-party anchors**. A named range created via the
  Docs API (`createNamedRange`, id `kix.…`) is accepted as a comment
  anchor and persists via the API, yet renders unanchored; the doc's 71
  real (UI-created) comment anchors are **not** named ranges — Google's
  anchor store is private. **Programmatic anchored comments on Google
  Docs are not possible.** Machine edits therefore do not use comments at
  all (ratified).

### 6.3 The round trip (ratified flow)

1. **Propose:** pass output staged as `doc_threads` rows
   (`origin_type: critique`), verbatim-validated `proposed_old`/`new`.
2. **Triage (shell):** verdicts only — nothing touches file or Doc.
   Undo is free (flip any verdict). Rejections die with reasons recorded;
   revises are modified acceptances.
3. **Diff-write:** accepted edits are written to the essay's tab as
   pending forms — `<<old>>{{new}}` for replaces, a **new `{{new}}`-only
   insertion form** (grammar extension) for insertions — via the existing
   surgical, read-back-proven push. Local file keeps the OLD text
   (standing rule: local keeps old until approval).
4. **The pause:** the author reads the marked-up essay in Docs and may
   post-edit any `{{new}}` half. Their words win. `critique undo <n>`
   withdraws an individual form pre-resolve.
5. **Resolve (explicit, deterministic):** `authorlm critique resolve
   <essay>` — never triggered by an ordinary pull. Pulls the tab, takes
   every remaining pending form's current `{{new}}` half as final,
   applies locally, strips the forms from the tab, records
   proposal→final diffs as evidence (modified acceptances feed the
   margin-learnings duty: surface recurring patterns unprompted at ≥2
   instances). Then: collect, close the essay's episode, **rebuild the
   essay's summary**, advance the cursor.
6. **Rollback:** `critique rollback <essay>` restores the pinned pre-pass
   state (Doc + local); verdicts survive as evidence.

An essay's file is only ever in two states: pristine or fully resolved.

---

## 7. The pass entity & verbs

```
critique_passes
  id, manuscript_id, source_id, created_at
  cursor            -- index into toc reading order
  essay_state       -- per-essay: pending | proposed | triaged |
                    --            written | resolved | skipped
  pinned_version    -- per-essay pre-pass pin (set at diff-write)
  status            -- active | completed | abandoned
```

One active pass per manuscript. Verbs (CLI; conversational equivalents via
chat as usual):

| Verb | Does |
|---|---|
| `critique import <file>` | Parse critique doc → sources row + proposed intents / style elements / objection nodes with scope + source |
| `critique status` | Pass state, cursor, per-essay progress, pending triage counts |
| `critique run [essay]` | Preflight gate → summaries check → edit pass → stage proposals |
| `critique triage` | Verdict loop over staged proposals (accept / reject / revise / undo) |
| `critique undo <n>` | Flip a verdict (pre-write) or withdraw a pending form from the tab (pre-resolve) |
| `critique resolve <essay>` | The confirmation gate (§6.3 step 5) |
| `critique rollback <essay>` | Restore pinned state; keep evidence |

Sequencing (ratified): **toc reading order**, not the critic's priority
order — it guarantees fresh upstream summaries at every step. Skipping an
essay = fast-resolve with zero edits. **Advance is manual**: resolve
closes essay N; the next `critique run` starts N+1 on the author's word.
Per-essay critic items triage **just-in-time** at that essay's pass start.

### Campaign bootstrap (order of operations)

1. `critique import` the docx (mapping below).
2. One sitting: triage the **global** material (~25 items — ten global
   changes, strands, architecture proposal) — this sets the law every
   essay pass consults.
3. `summarize --rebuild` — initial full sequential pass.
4. Essays in toc order, gated per §5.1, advanced manually.

---

## 8. Import mapping — SMSTTD Editorial Analysis (2026-08-13)

Global sections (manuscript scope, triaged in the bootstrap sitting):
Executive verdict · Recommended architecture (type 3) · SWOT — Strengths
= preservation strands (type 5), Weaknesses/Threats mined for types 1/4 ·
Ten global changes (types 1/3) · Source-check notes · Final priority
order (advisory only; sequencing is toc order).

Dossier units → scope:

| Unit | Scope |
|---|---|
| Front matter and part openings | NULL (manuscript) |
| Preface: The Hammer Upon the Temple | preface.md |
| Sermons: Prologue; Sermon One–Seven; Sermon Seven and the Anagramma | sermons.md (unit name kept in intent text) |
| Recapitulation | recapitulation.md |
| Ecce Homo | ecce-homo.md |
| Before Consciousness | ascending.md |
| The Good Choice | good-choice.md |
| Rebirth | rebirth.md |
| The Good Life | good-life.md |
| The Two Impulses, the Two Religions | impulses.md |
| The Redemption We Must Name | redemption.md |
| Kindness That Kills | kindness.md |
| Connections to Basilides | basilides.md |
| Prohairesis | epictetus.md |
| Eastern Connections | indic.md |
| Christic Connections | christic.md |
| Nietzschean Connections | nietzsche.md |
| The Denial of Death | becker.md |
| The Metaphysic | metaphysic.md |

Not covered by any unit: `discernment.md`, `connections.md`, `title.md` —
their passes run with global law + inherited intents only (or are
skipped).

---

## 9. Schema change summary

| Change | Table | Note |
|---|---|---|
| New | `sources` | provenance registry |
| Add `source_id` | intents, concept_nodes, concept_edges, style_elements, policies, evidence | backfill by hand; no fallback |
| Add `status='proposed'`, `rejected` | `declared_intents` | non-author births |
| Add `scope` (nullable file path) | `declared_intents` | toc-chain resolution |
| New | `essay_summaries` | §4 |
| Replace `comment_id` with `origin_type`+`origin_id` | `doc_threads` | migrate rows by hand |
| New | `critique_passes` | §7 |
| Grammar | pending forms | `{{new}}`-only insertion form |
| Config | `[critique]` in config.toml | `editor_model`, `summarizer_model` |

## 10. Standing duties acquired

- Briefings badge critic-/system-sourced pending items and counts.
- Modified acceptances at `critique resolve` join the margin-learnings
  duty: surface recurring patterns unprompted at ≥2 instances; candidates
  go through the decline-by-default scoped distiller and author
  ratification.
- The two LLM prompts (summarizer, editor) are repo artifacts; changes to
  them are reviewed like style law.

## 11. Implementation status (2026-08-15)

All four stages built and tested; the design above is the reference,
this section records where the build landed differently or added detail.

| Stage | Landed as | Tests |
|---|---|---|
| 1 Schema/provenance | `sources`, `source_id`, intent `proposed`/`rejected` + `scope`, `essay_summaries`, `critique_passes`; `doc_threads` origin polymorphism (`origin_type`/`origin_id`, live rows migrated by table rebuild) | test_critique |
| 2 Intake | `critique import/status/list/triage` (+ show/reason/reopen), MCP `import_critique`, `critique_status`, `list_critique_items`, `triage_critique`; briefing badge `critique_pending`; SMSTTD import audited: 338 doc items → 318 rows + 10 ratified exclusions, zero loss | test_critique |
| 3 Summaries | `summarize status/show/rebuild [file] [--all]`, `authorlm/prompts/summarizer.md`, mark-don't-cascade on collect, `[critique] summarizer_model`; `authorlm prompts` registry + `--help` notes on every LLM verb | test_summaries |
| 4 Edit pass | `passes.py`; `authorlm/prompts/editor.md`; `critique run/edits/triage --edits/write/resolve/rollback`; grammar: `{{new}}`-only insertion form; MCP `list_critique_edits`, `triage_critique_edits`, `move_style_element`, `curate_concepts` op `revive`; `concept revive`, `style move` | test_passes |

Deviations / additions worth knowing:

- **Verbs**: the design's `critique triage` (edits) is `critique triage
  --edits <essay>`; the intake triage keeps the bare form. Diff-write is
  its own verb `critique write` (the design folded it into triage end) so
  the author decides when the Doc changes.
- **Verdict keys** unified across every triage loop: `k` accept, `r`
  reject, `e` edit+accept, `u` undo (edits), `s` skip, `x`/`q` quit; `a`
  prints a hint (concept triage reserves it for alias).
- **Reject without reason** in the intake loop still skips (strict);
  bulk `--reject` requires `--reason`. Author verdict on softening is
  pending.
- **Insertion stripping**: `strip_pending` drops an insertion AND its
  paragraph separator so the local canonical text is byte-clean.
- **Resolve** re-pushes the final local file to rebuild the tab clean
  (read-back proof inside push) rather than surgically stripping forms.
- **Preflight** counts proposed intents in the scope chain, proposed
  style elements on the file/guide chain, and candidate policies seeded
  *since the pass began* (the pass's own distiller output). The live
  record carried 116 older candidates; a literal "all candidates" gate
  would have refused every essay forever.
- **Anchored Doc comments are impossible** (§6.2) — machine edits use
  pending forms + shell/chat triage only.
