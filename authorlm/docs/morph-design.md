# Morph — paragraph-level defect detection and correction

Status: CORE BUILT 2026-09-26/27 (`findings.py` extraction, `morph.py`
detection engine, `authorlm morph run`/`morph review` CLI, cost
reporting via `LLMClient.stats_line()`), REVISED 2026-09-27 after an
independent design review — four load-bearing corrections below (§3,
§5, §6, §7) plus several confirmed-as-is rejections, recorded so the
reasoning isn't lost. Name settled as `morph`. Drafted after a design
conversation comparing AuthorLM's editing architecture against a
"morphogenetic" neural-cellular-automaton essay-editing proposal
(paragraphs as cells, diffusing quality signals, a shared local update
rule, conserved invariants) — that analogy lives in chat history only;
this doc is the concrete, scoped feature it converged on.

**Still open, blocking a full first ship**: pushing a staged edit into
the actual Google Doc tab (§10) — detection and local staging work
today; nothing yet calls `write_pending_forms` for a morph-origin
batch, so a proposal isn't visible to the author until that exists.

## 1. Scope

Detect, and where safe correct, five paragraph-level defects. Four
share one combined LLM call (§3); the fifth is fully deterministic and
was renamed after review to say so honestly:

- **lexical-repetition** (renamed from "redundancy" 2026-09-27 —
  review §5.1: the deterministic machinery it actually runs on
  (`lint.check_overlap`, `critic.nearest_passages`) only ever catches
  verbatim/near-verbatim repetition, not semantic redundancy; a
  semantic version was proposed and rejected on cost grounds, see §5).
- **claim-before-evidence** — a claim asserted with no supporting
  evidence nearby.
- **discontinuity** — a paragraph does not follow coherently from the
  one immediately before it.
- **specificity** — vague, generic prose that fails to engage the
  material.
- **concept-invalidation** — a paragraph contradicts a ratified
  concept-graph note.

More defect types are expected later; adding one is a new `Flag:`
section in the shared rubric file (§8), not new plumbing.

## 2. Lifecycle: observation → finding → proposal → settlement

Four named states, adopted from the 2026-09-27 review (§1) — but
**not** its two-model-call mechanism. The review's `morph run` /
`morph repair` split would mean re-sending the paragraph, window, and
rubric a second time for every accepted finding: strictly more tokens
than what's built. Rejected on cost grounds (explicit constraint:
adopting review feedback must not change token cost). What's kept is
the vocabulary and the state boundaries, mapped onto the single-call
design that's actually built:

- **Observation** — the model's typed `{present, confidence}` decision
  per dimension per paragraph, produced by the one combined call.
  Machine output, not yet guidance; §9 covers persisting the negative/
  abstain observations this state implies, which nothing currently
  stores.
- **Finding** — a `present: true` observation becomes a
  `guidance_history` row (`kind="morph"`).
- **Proposal** — when the same call can also draft a safe fix (§6), the
  drafted `replacement`/`judgment` is staged as a `doc_threads` edit
  **immediately**, in the same run, not gated behind a separate accept
  step. Confirmed 2026-09-27: an accept-gated design was drafted and
  rejected — it would mean running `morph review --accept` just to see
  a proposal reach the Doc, which is friction nothing else in this
  codebase requires. The review step already exists: it's the Doc
  itself (leave a form in place = accept, empty or delete it = decline
  — the same "the Doc settle IS the review" convention lens/filter/
  critique already use).
- **Settlement** — the existing Doc propose/settle machinery already
  records accepted/modified/declined and the final text; nothing morph-
  specific needed here (review §1.4/§7.4 raised concerns about this
  that turned out to describe already-working shared behavior, or to
  require changing that shared behavior for every producer at once —
  see §7.4's rejection below).

Detection and correction stay conceptually separate (this section), but
mechanically they're one call, one run, one immediate staging step —
by design, to keep cost flat and friction at zero.

An unattended multi-paragraph or whole-essay autonomous drafting loop
(composing detect→correct repeatedly with no human between iterations)
is still not what this builds — every step here checkpoints on the
author before the NEXT run, even though nothing checkpoints mid-run
between drafting and staging. Nothing here forecloses that composition
for later; §9's evidence trail would support it without new logging if
it ever comes up.

## 3. Detection: a combined call, not one call per defect

**Lexical-repetition stays on existing deterministic machinery** — no
new call. `lint.check_overlap` (verbatim 8-word shingle overlap, no
model) and `critic.nearest_passages` (5-word-shingle Jaccard ranking,
pure Python, no model). A semantic-redundancy alternative was proposed
in review (§5.1 Option B: deterministic retrieval + one bounded
semantic judgment call) and **rejected on cost grounds** — it adds a
call that doesn't exist today. The design doc's earlier claim that
semantic judgment gives "no accuracy gain" was never actually measured
and has been removed; the rejection here is about cost, not accuracy.

**The other four dimensions share ONE combined LLM call per paragraph**,
scoring all four simultaneously, on the project's cheap `[llm]` tier
(`gemini/gemini-2.5-flash` today, per `config.toml`). Reasons, in order
of how they were established:

1. Paragraph roles (claim/evidence/transition) are NOT reliably
   available as stored metadata to key a cheap lookup off — free text,
   no fixed taxonomy, drifts out of sync with position after any later
   edit (`api.py:2166`). All four dimensions need a fresh judgment
   call, not a lookup.
2. **Empirically validated.** Two live pilot runs (2026-09-26, synthetic
   6-paragraph essay, hand-injected known defects, `gemini-2.5-flash`):
   combined call got every injected defect right both times; separate
   single-dimension calls badly over-fired when unscaffolded, and even
   with identical scaffolding, **specificity judged in isolation was
   inverted in both runs** — flagged every concrete paragraph as vague,
   missed the one actually vague paragraph. It needs the *other*
   paragraphs' content in view as an implicit contrast; isolating it
   doesn't just cost more, it breaks the judgment.
3. Output is a typed decision plus confidence per dimension —
   `{"present": bool, "confidence": 0.0-1.0}` — so a caller can act
   differently at different confidence. **A literal floor is now
   defined** (2026-09-27, was previously "informational only," which
   review §9 correctly flagged as never actually gating anything):
   **confidence ≥ 0.8 is required for a `replacement` to auto-stage**;
   below that floor, a finding still gets recorded but falls back to
   `judgment`-only regardless of what the model drafted (§6). This is
   an author-set operating threshold, not a claim that 0.8 is
   calibrated truth — review §9's point stands: confidence is
   uncalibrated telemetry, used here only to gate which findings are
   trusted enough to auto-propose text, never treated as ground truth
   about whether the defect is real. Raising or lowering 0.8 is a
   one-line change once real manuscript data (§10) gives evidence
   either way.

## 4. Context window for the combined call

One fixed window, sent uniformly: the positional neighborhood
$N(i) = \{i-2, i-1, i, i+1, i+2\}$ plus the concept-graph note slice
for whatever concepts the paragraph mentions. Not tailored per
dimension — one payload function, cheap to extend, and the token cost
of a slightly wider window than strictly needed is trivial next to the
complexity of branching context per dimension.

**Paragraph identity is the fingerprint, not the ordinal** (review
§2.2, accepted as a real gap, zero cost — bookkeeping only). An edit
anywhere in the manuscript can shift every later paragraph's ordinal;
keying a cached observation to "paragraph 7" the way `_findings_from_
reply` currently does is exactly the staleness bug this codebase has
already hit once before (§12.3 of `autoregressive-writing-design.md`,
per-paragraph summaries). Not yet implemented (§10) — the fingerprint
should cover: file, normalized target-paragraph hash, nearest heading,
neighbor-paragraph hashes, concept-authority hashes/versions, rubric
hash, detector version. Ordinals stay presentation-only once this
lands.

Lexical-repetition is explicitly NOT part of this window — its natural
"neighbor" metric is content similarity across the whole book, not
positional adjacency, and it already has its own machinery (§3).

## 5. Targeting: every paragraph, not concept-narrowed — and a wider dirty-set

**Empirically checked against `sweeps.py`'s `ontology` auditor**
(2026-09-26 live test): ontology's candidate-selection gate (changed
file, paragraph ≥ 80 chars, names a tracked concept) excludes 3 of 4
target dimensions unconditionally, and the 4th only survives with
proactive alias curation. Where a paragraph DID survive narrowing, the
narrower context didn't degrade the judgment — the problem is the
selection criterion, not context size. **Conclusion unchanged: check
every paragraph, full stop.** Narrowing controls cost and what
surfaces to the author, never which paragraph gets a call.

**The dirty-set is wider than "this paragraph's own text changed"**
(review §2.1, accepted as a real correctness bug, not yet implemented
— see §10). The detector judges paragraph $i$ using its ±2 window, so
a cached verdict on $i$ can go stale from an edit to $i-1$ or $i+1$
that never touched $i$ itself. The corrected rule for the automatic
(delta-only) trigger:

```
directly changed paragraphs
∪ paragraphs whose ±2 window overlaps a changed/inserted/deleted/moved paragraph
∪ paragraphs mentioning a concept whose note or a relevant edge changed
∪ paragraphs with no successful observation under the current rubric hash
```

An on-demand full-manuscript pass (`--full`, already built) remains the
override for a first run or a file with no collected history —
mirroring the escape hatch `ontology` itself needed for the same case
(`sweeps.py:194-198`). Worth being honest that this correctly widens,
not just cleans up, what gets checked on an average run — that's the
fix, not a side effect to minimize.

**A morph-owned watermark** (review §2.3, accepted, not yet
implemented): a manuscript version counts as complete for morph only
once every intended target in that run has either succeeded or has an
explicit terminal failure recorded — a failed/interrupted run must not
silently advance past unobserved targets, and a rerun should resume
idempotently. `collect` itself must stay successful even when morph
fails (§10 already commits to failure isolation; this is the same
principle applied to resumability).

## 6. Correction: replacement vs. judgment-only

Every finding is one of two shapes: a `judgment` (a bare `[Judgment:
…]` tag, no proposed text) or a `replacement` (a proposed edit, staged
immediately per §2, `origin_type="morph"`). Both require the author's
real Doc-settle action before anything is final — the distinction
affects presentation, never whether something ships unreviewed.

**The safe-replacement boundary — reopened by review §6, reaffirmed as
written, not tightened.** The review proposed disallowing any new
fact, citation, or named entity in a replacement, even ones that only
"sharpen" something already gestured at. Considered and **rejected**
(2026-09-27): this reverts a boundary the author explicitly set earlier
in this design's own history — "the LLM is a powerful thing... this
should be leveraged... judgment is a backstop, not the default." The
rule stands as originally written: propose a `replacement` when it can
be written using material already available — the paragraph, its
window, the concept note, protected terms, or general knowledge that
only *sharpens or corrects* something already asserted; emit a
`judgment` when the fix requires *inventing* new argumentative content
the author hasn't authorized. Worth recording for future readers: in
the one live test run so far, the accepted specificity replacement
never actually needed to reach outside the paragraph's own window —
so the looser rule hasn't yet been exercised at its actual boundary.
That's evidence to watch as real manuscript cases accumulate (§10),
not a reason to tighten preemptively.

Claim-before-evidence is judgment-only more often than the others in
practice (its defining defect — "supply the missing evidence" —
usually *is* the invention risk), while specificity and
concept-invalidation are usually safe for a `replacement`. This is a
per-instance model judgment call, not a hardcoded table, now further
gated by the **0.8 confidence floor** (§3): below it, a finding falls
back to `judgment` regardless of what the model drafted, independent
of which dimension it is.

This is a **deliberate, conscious divergence** from a real principle
elsewhere in this codebase: auditor findings (`sweeps.py`) are
read-only by explicit design (`proposals.py:475-482`). Morph diverges
on purpose — but the risk that principle guards against is real and
already happened once here (the §13.3 blind-redraft experiment,
`autoregressive-writing-design.md`, inventing an unauthorized
attribution). The judgment-only carve-out for claim-before-evidence,
plus the confidence floor, are what keep that failure mode from
recurring in morph specifically.

**Multiple defects on one paragraph — resolved 2026-09-27 (review
§7.1, modified).** Composing several accepted findings into one
coherent rewrite genuinely needs a model call — two independent
full-paragraph rewrites aren't textually composable without one — so
this is a real, bounded exception to "no new cost," accepted because
the author judged the frequency low enough to be worth it (one
concrete data point: paragraph P6 in the original pilot was flagged
for both claim-before-evidence and concept-invalidation in the same
run — "occasional," not rare, concentrated in synthesis/conclusion-
shaped paragraphs). The design: **within the same `morph run`
invocation**, not gated behind a later accept step (that would
reintroduce the rejected two-call lifecycle) — if a single paragraph
comes back from the detection call with two or more findings *both*
at or above the 0.8 confidence floor, make one additional bounded call,
scoped to that paragraph only, to produce a single composed
replacement addressing all of them before staging. A paragraph with
only one finding above the floor, or additional findings below it,
skips this entirely — those extra findings stay as separate
`judgment`-only records rather than triggering composition. Not yet
implemented (§10).

Real insertion (not just whole-paragraph replacement) is available
when needed — the `{{new}}`-only pending-form grammar the critique pass
already uses (`threads.py:40`) supports a new paragraph with no `old`
half; filters' "never insert" rule (`filtering.py:1029`) is a
self-imposed discipline of that one producer, not a limitation of the
underlying transport.

## 7. Category and identity: a new type, not lens, not auditor — built

Neither existing category fit without inheriting unwanted baggage:
lens requires a real `_lenses/*.md` file even for external findings
and silently inherits the protected-register exemption; auditor is
read-only by explicit principle and its narrowing can't see 3 of 4
target dimensions. So: a dedicated `kind="morph"` in `guidance_history`,
its own CLI verb family.

**Built, 2026-09-26/27:**
- [`authorlm/findings.py`](../authorlm/findings.py) — the extracted,
  producer-agnostic storage door: hygiene-gates a quote, stores the
  row, optionally stages a Doc edit. Lens is now a thin wrapper over it
  (`producer_key="lens"` preserves every existing metadata shape
  byte-for-byte — confirmed against all 1125 `test_api.py` + 308
  `test_passes.py` checks, unchanged).
- [`authorlm/morph.py`](../authorlm/morph.py) — targeting, windowed
  payload assembly, the combined call, reply parsing, registration.
- `authorlm morph run <file> [--full]` and `authorlm morph review <n>
  --accept|--reject|--modify|--defer` ([cli.py:4083](../authorlm/cli.py)).
  `morph repair` doesn't need to exist as its own verb — `lens repair`
  already resolves a morph-planted `[Judgment: …]` tag unmodified,
  confirmed live: its lookup is by finding id, not by producer kind.
- `LLMClient.stats_line()` now reports an estimated dollar cost (via
  `usage.estimate`, litellm's live pricing map, not a hand-maintained
  table) on every call site that already prints it — morph gets this
  for free, and so does every existing verb.

**Review §10's interface critique — mostly already satisfied, one real
fix identified.** `store_findings` never calls `assemble()`, never
decides which observation becomes a finding, never drafts a correction
— those live in `morph.py`, not `findings.py`, already matching the
review's stated boundary. The one place it doesn't fully match:
review §10 says the door "must not infer author acceptance" — and
staging a replacement immediately (§2, §6) *could* read that way.
Resolved: staging a **proposal** is not inferring **acceptance** —
nothing marks a finding "accepted" until the author actually settles
it in the Doc; §2's immediate-staging design is the "Doc proposal"
state the review itself describes, not a skipped state. No interface
change needed here beyond what §5/§9 already require (fingerprint-
based dedup so a rerun doesn't restage an already-open equivalent
finding — not yet implemented, §10).

**Typed interface (`GroundedFinding` dataclass vs. plain dicts,
review §10) — decided: keep dicts, revisit later.** The interface has
exactly two callers so far (lens, morph), both working correctly on
dicts, with no bug so far traceable to the lack of typing. Converting
now would be formalizing a shape before a third caller or real
production use has shown what fields actually matter — exactly the
kind of premature structure YAGNI warns against for a feature that
hasn't shipped a single real finding to an author yet. Revisit if a
third producer wants this door, or if a malformed-finding bug actually
occurs.

**Not yet implemented — collision with another producer's open thread**
(review §7.2, accepted): before staging a morph proposal, check
whether the target paragraph already has an open thread from lens,
filter, or critique, and if so, record the finding but mark its repair
blocked with the blocking thread's id rather than staging a competing
edit. Pure bookkeeping, zero model cost.

**Rejected — changing the Doc-settle grammar itself** (review §7.4).
"Don't overload leaving a tag in place as acceptance" would mean
changing shared, already-ratified behavior every producer relies on
("the Doc settle IS the review" — `filter-pass-design.md`'s own
words), not a morph-specific fix. If that convention is genuinely
wrong, it's a separate proposal touching `threads.py`/`gdocs.py`
broadly, not something to fork for morph alone.

## 8. Rubric: an author-editable prompt file, like lenses

The literal text: [morph-rubric.md](morph-rubric.md). Iterated three
times against the synthetic ground-truth essay before settling on a
version scoring 23/24 with every replacement/judgment choice matching
the intended policy exactly.

**Structural consistency confirmed** (review §8): the rubric declares
exactly four `Flag:` sections, matching exactly the four dimensions the
combined call scores — lexical-repetition is correctly absent, since it
never goes through this call (§3). No mismatch to fix.

**Per-Flag richness — accepted, to be added to the rubric text**
(review §8, not yet drafted): each `Flag:` section should define a
positive criterion, explicit non-examples, the required quote shape,
when to abstain, and the minimum explanation needed for grounding —
the current rubric has the first two (via Flag/Keep example pairs) but
not an explicit abstain condition per dimension. Specific refinements
also accepted from review:

- **Claim-before-evidence** (§5.2): distinguish a claim already
  supported in the window, a claim functioning as a stated thesis/
  preview, a claim whose evidence is explicitly deferred elsewhere, and
  a claim genuinely missing evidence — only the last is a finding.
- **Specificity** (§5.4): the `reason`/`judgment` text must name both
  what's generic and what concrete manuscript material would fix it;
  "could be more specific" with no grounded contrast is an `abstain`
  (i.e., `present: false`), not a finding.
- **Concept-invalidation** (§5.5): if the concept-claims slice supplied
  for a paragraph's window is empty, the model should return
  `present: false` for this dimension rather than guess against nothing
  — never judge concept-invalidation with no authority to judge against.
  The deterministic retrieval itself (exact name/alias matching, via
  `concept_pattern`) stays as built; review §5.5's "optional semantic
  retrieval" escalation is **rejected on cost grounds** (a new
  embedding/retrieval call that doesn't exist today).

**Rubric hash attachment** (review §8, ties to §4's fingerprinting):
once fingerprinting lands, every run/observation/finding/repair should
carry the rubric's content hash, so a rubric edit alone is enough to
invalidate what it produced — not yet implemented.

## 9. Data capture — observations, not just findings

Review §3 caught a real gap: `_findings_from_reply` only ever
materializes a finding for `present: true` — negative and abstain
results vanish entirely, even though §9 (below, unchanged) already
commits to wanting this data for a future training/calibration phase.
**Accepted, not yet implemented**: log one structured observation per
(paragraph, dimension) per run — decision, confidence, quote, reason,
target/window/concept-authority hashes, rubric hash, and the resulting
finding id when one was surfaced. Storage choice, decided 2026-09-27:
**an append-only JSON log**, not a new relational table — the door
`findings.store_findings` and `guidance_history` already give durable
storage for the *surfaced* half; a dedicated multi-column SQL schema
for the full observation stream is more structure than a feature with
zero production runs has earned. Upgrade to a real table only if/when
actual calibration work starts and the JSON log proves awkward to
query — YAGNI, not a permanent decision.

Everything already true here stays true: every registered finding
carries `quote`, `note`, `rule`, and either `judgment` or `replacement`,
plus the author's eventual accept/decline, durably, independent of
belief-confidence math. `rule` already doubles as a clean defect-type
tag (§8). What's new is capturing the observations that *don't* become
findings too, which the old design silently dropped.

## 10. Open items (updated 2026-09-27)

Blocking a real first use:
- **Push to Doc** — nothing yet stages a morph proposal into the actual
  Google Doc tab (`write_pending_forms` is never called for
  `origin_type="morph"`). Detection and local staging work; the author
  can't see a proposal yet. This is the single most important
  remaining gap.

Accepted from review, not yet implemented (all zero additional model
cost, all bookkeeping/prompt-text):
- Paragraph fingerprinting as durable identity (§4), and the fingerprint-
  based dedup this enables for reruns (§7).
- The wider dirty-set rule and morph-owned watermark (§5).
- The 0.8 confidence floor actually gating replacement-vs-judgment in
  code (§3, §6) — currently only decided, not wired into `morph.py`.
- The conditional same-run compose call for multi-defect paragraphs
  above the confidence floor (§6).
- Structured per-observation logging, append-only JSON (§9).
- Open-thread collision check against other producers before staging
  (§7).
- Rubric per-Flag richness: abstain conditions, claim-before-evidence's
  four-way distinction, specificity's grounded-contrast requirement,
  concept-invalidation's empty-slice abstain rule, rubric-hash
  attachment (§8).

Confirmed rejected, not open questions:
- Semantic redundancy / semantic concept retrieval (cost).
- The two-call observation/repair lifecycle (cost + friction).
- Tightening the safe-replacement boundary past what's already written
  (reopens a settled decision without new evidence).
- Changing the Doc-settle grammar for morph alone (wrong scope).
- Parallel batch execution (no evidence of need yet — YAGNI).
- Typed dataclass interface for `findings.store_findings` (premature
  for a two-caller interface with zero known bugs — YAGNI, revisit
  later).

Test coverage for all of the above: [morph-test-plan.md](morph-test-plan.md).

## Verse (added 2026-09-27, first run on a verse manuscript)

Morph judges prose paragraphs only. In a verse file a stanza (lines ending
in the backslash hard break) and a bare italic line (a thesis line or an
epigraph) are not prose: `morph._is_prose` defers to `verse.is_verse_unit`
and skips them, because claim-before-evidence and specificity mean nothing
for a line of verse, and a `replacement` staged against a stanza would be
a poem rewritten unasked (verse-and-cast-design.md §2; the verse lenses
own poems). The rubric is shared: DON's `_morph/paragraph-defects.md` is a
copy of SMSTTD's, which carries only generic examples. First DON run
(title.md, prologue.md, `--full`): title clean; the Prologue's prose gave
eleven findings, most of them the tool reading the chapter-plan bullet
lists as paragraphs and the Prologue's summary sentences as unsupported
claims — a list unit is not a prose paragraph either, and a book's prologue
carries claims whose evidence is the chapters. Both are candidates for the
next guard; neither is patched yet.
