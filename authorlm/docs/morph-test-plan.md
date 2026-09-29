# Morph test plan

Status: PLAN, updated 2026-09-27 after an independent design review
(see `morph-design.md`'s status line for what was adopted/rejected).
Nothing here is implemented yet — `findings.py` and `morph.py` still
have zero dedicated test coverage; they're only exercised indirectly
(1127 `test_api.py` + 308 `test_passes.py` checks pass unchanged, and
this session's live smoke tests, most recently a 24/24 run against the
richer post-review rubric). This plan is what closes that gap,
organized by layer, cheapest and most load-bearing first. §7 and §8
below fold in the review's own §9 (confidence-calibration battery) and
§11 (acceptance-test matrix) rather than keeping them as a separate
document.

## 1. `findings.py` — the extracted door (offline, no LLM)

This is the highest-leverage layer to cover first: both lens and morph
depend on it, and a regression here breaks two producers at once, not
one. All of these run against a stub in-memory db, no model calls —
same style as `test_passes.py`'s door tests.

- `check_hygiene_gate_drops_ungrounded_quote` — a finding whose `quote`
  doesn't appear verbatim in `file_text` is dropped, counted in
  `dropped_ungrounded`, never stored.
- `check_hygiene_gate_requires_note` — a finding with a real quote but
  empty `note` is dropped (matches the pre-extraction behavior exactly).
- `check_judgment_plants_tag_with_finding_id` — a finding carrying
  `judgment` (no `replacement`) plants `[Judgment: <producer> — <gist> |
  id: <row-id-prefix>]` after the quoted text, unit-anchored via
  `edit_entry`.
- `check_replacement_stages_a_doc_thread` — a finding carrying
  `replacement` produces a staged `doc_threads` row via `stage_edits`,
  and the stored finding's metadata gets `edit_thread` set to that row's
  id (the independence the design commits to — verdict and edit are
  separate).
  # SAFETY-CRITICAL, do not skip:
- `check_replacement_refused_on_ambiguous_quote` — a `replacement` whose
  `quote` appears in more than one unit, or more than once within its
  unit, is refused (`edit_refused`), never silently anchored to the
  wrong occurrence.
- `check_replacement_refused_on_reserved_markers` — a `replacement`
  containing `<<`, `>>`, `{{`, or `}}` is refused, not staged with
  corrupted markup.
- `check_producer_key_defaults_to_producer` — calling `store_findings`
  with no `producer_key` writes `metadata["producer"]`, not
  `metadata["lens"]` — confirms morph's default path doesn't
  accidentally collide with lens's reserved key.
- `check_producer_key_lens_backward_compat` — calling with
  `producer_key="lens"` reproduces byte-identical metadata shape to
  pre-extraction `_store_findings` (same keys, same dedupe_key format) —
  this is the test that would have caught a regression in this
  session's refactor had one existed; write it even though the full
  suite already passed, since that was incidental coverage, not
  intentional.
- `check_target_file_undeclared_is_refused` — a finding naming a
  `target_file` not in `target_files` lands in `refused_targets`, never
  stored (lens's cross-chapter guard, exercised through the generic
  path since morph never supplies `target_files` and should never
  trigger this branch at all — worth a `check_morph_never_triggers_
  target_validation` asserting exactly that, given the whole point of
  factoring this out was that a same-file producer opts out for free).
- `check_rule_matched_against_rubric_flag_headings` — `rule="Specificity"`
  against a `rubric_body` containing `### Flag — Specificity` sets
  `rule_known=True`; an unrecognized rule string sets it `False` but is
  still kept (never dropped for an unknown rule name).

## 2. `morph.py` — targeting and payload assembly (offline, no LLM)

- `check_targets_full_returns_every_prose_paragraph` — `full=True`
  returns every 1-based index except headings/footnote-defs/directive
  lines; a file that's ALL non-prose returns `None`, not `[]` (the
  distinction the design commits to: "nothing to check" vs. "checked
  everything, found nothing" must stay distinguishable by callers).
- `check_targets_excludes_headings_and_directives` — the exact bug this
  session found live: a unit that's a bare `## Heading`, a `[^x]:`
  footnote definition, or an `[Illustration:]`/`[Voice:]` tag is never a
  target, `full` or delta.
- `check_targets_delta_only_changed_paragraphs` — given two collected
  versions differing at paragraph 3 only, `targets(full=False)` returns
  `[3]`, not every paragraph of the file (this is the paragraph-level
  precision the design chose over ontology's file-level-only diffing —
  worth its own test specifically because it's a deliberate departure
  from the precedent, not an accident).
- `check_targets_no_history_returns_none` — fewer than two collected
  versions (a brand-new manuscript) returns `None` without `full=True`,
  matching the "name a file explicitly" convention `ontology` already
  established for the same case.
- `check_window_clamps_at_file_boundaries` — paragraph 1's window is
  `[1,2,3]` not `[-1,0,1,2,3]`; the last paragraph's window clamps
  symmetrically.
- `check_assemble_includes_concept_claims_only_for_mentioned_concepts` —
  a window mentioning a tracked concept gets its `notes`/`refutes`/
  `depends_on` claims in the payload; a window mentioning none gets no
  "Ratified concept claims" block at all (not an empty one — matches
  the "never send a section that isn't earning its place" pattern
  already established elsewhere in this codebase, e.g. filter payloads).

## 3. Reply parsing and the safety-critical split (offline, stubbed replies)

- `check_parse_reply_strips_markdown_fences` — a reply wrapped in
  ```` ```json ... ``` ```` parses the same as bare JSON.
- `check_parse_reply_bad_json_raises_not_silently_drops` — malformed
  JSON raises `ValueError` naming the parse error, rather than being
  swallowed into "0 findings" (a silent miss here is worse than a loud
  failure — the author should know the run didn't actually check
  anything, not see a clean "0 findings" that looks like a real
  all-clear).
- `check_findings_from_reply_present_with_neither_judgment_nor_
  replacement` — a malformed dimension (`present: true`, no `judgment`
  or `replacement` key) still becomes a finding (bare judgment naming
  the dimension), never silently dropped — this is a real defensive
  case, not a hypothetical: a reply that's 95% well-formed shouldn't
  lose the 5% that's malformed.
- `check_confidence_floor_downgrades_replacement_to_judgment` — a
  dimension with `present: true`, a real `replacement`, and
  `confidence` below `CONFIDENCE_FLOOR` (0.8) produces a finding with
  `judgment` set (the drafted text preserved inside it) and NO
  `replacement` key — confirms the floor actually gates staging, not
  just informs it (this is the thing review §9 flagged as never
  wired in; it's now code, so it needs its own test rather than
  relying on the design doc's word for it).
- `check_confidence_floor_at_exactly_the_threshold_stages` — confidence
  `== 0.8` counts as meeting the floor (`>=`, not `>`) — a boundary
  case worth pinning explicitly so a future refactor doesn't flip it.
  # SAFETY-CRITICAL, the one that matters most:
- `check_claim_before_evidence_replacement_never_contains_new_proper_
  nouns` — given a stubbed reply where `claim_before_evidence` carries a
  `replacement` naming a specific fact/citation/example not present
  anywhere in the input window or concept note, flag it. This can't be
  fully automated (recognizing "invented" vs. "grounded" is exactly the
  judgment call the rubric itself makes) — so this test is a **content
  audit, not a pass/fail assertion**: log every `claim_before_evidence`
  replacement ever produced against a labeled corpus (§4) to a file a
  human reviews periodically, and fail loudly only on the mechanical
  half — a `claim_before_evidence` finding with a `replacement` at all
  should be rare enough that the count itself is the signal (rubric
  §"When you find something" defaults this dimension to `judgment`; a
  replacement should be the exception, not the rule — assert the RATE
  stays low across the labeled corpus, not zero, since the rubric's own
  stated exception — the note or a neighbor already supplies the exact
  support — is legitimate).

## 4. A small labeled corpus, not just the one synthetic essay

Today's validation used exactly one synthetic 6-paragraph essay (the
highway/virtue-ethics one from this session) and one live pass over
`preface.md` that surfaced no labeled ground truth, just a plausibility
read. Neither is enough to trust before this runs unattended against
essays the author hasn't pre-read. Before that:

- Extend the synthetic essay to 2-3 more short, hand-labeled cases
  covering: a paragraph correctly NOT flagged despite superficially
  resembling a defect (the "Keep" examples already in the rubric,
  turned into test fixtures — they're currently only prose in
  `morph-rubric.md`, never actually run against the model to confirm
  they stay unflagged); a paragraph with TWO simultaneous real defects
  (does the combined call catch both, or does one crowd out the other);
  a concept-invalidation case where the note does NOT supply the exact
  correction (confirms `judgment`, not a fabricated `replacement`, per
  the rubric's own stated exception boundary).
- Pull 3-5 REAL paragraphs from the actual manuscript — deliberately
  including at least one the author would call a false-positive risk
  (dense philosophical prose that's supposed to sound different from
  its neighbors, so a naive "specificity" or "discontinuity" read might
  misfire) — and have the author label them once, by hand, as the first
  real (not synthetic) ground truth this design has ever been checked
  against. This is the test that actually answers "does this work on
  MY book," which nothing run so far has answered.

## 5. Integration (CLI, still offline via the replay cache)

- `check_morph_run_cli_reports_nothing_changed_without_full` — a file
  with no version delta and no `--full` prints the "nothing changed"
  note and makes zero LLM calls (verify via the replay cache's call
  count, same pattern `test_drafting_replay_needs_no_key` already uses
  elsewhere).
- `check_morph_run_cli_prints_cost_line` — a live (or replayed) run's
  stdout contains the `stats_line()` clause, including a `~$` figure
  when the configured model has known pricing — and confirms it's
  ABSENT (not `~$0.0000`) when pointed at a model `usage.price()`
  returns `None` for, per usage.py's own "null cost is a different
  claim than zero" rule. This is the direct test for this session's
  `stats_line` change, which currently has zero dedicated coverage of
  its own — only indirect coverage via the full suite still passing.
- `check_morph_review_records_verdict_independent_of_edit_state` — the
  `edits_staged`/independence behavior already tested for lens
  (`§7.2`) reproduced for a morph finding specifically, since the CLI
  path is new code, not just the shared door.
- `check_morph_repair_resolves_a_morph_planted_judgment_tag` — the
  claim in this session's own report ("`lens repair` already handles
  this, by id, unmodified") is currently ASSERTED, not tested. One
  concrete test: plant a `[Judgment: …]` tag via `morph.run`, then run
  `lens repair`'s assemble/record path against it end to end, confirm
  the producer label in the repair payload reads correctly (the
  `meta.get('lens') or meta.get('producer')` fallback this session
  added) and the repair lands.

## 6. What's explicitly NOT covered yet, and why that's a real gap

- **No test for pushing a morph-staged edit into an actual Doc tab** —
  because that capability doesn't exist yet (`docs/morph-design.md`
  §10). Nothing to test until it's built; noting it here so it isn't
  mistaken for an oversight later.
- **No cost-cap / budget-exceeded test** — `authorlm/llm.py` has no
  per-run spending cap the way supplylm's `Client._guard` does
  (`usd_per_tick`/`usd_per_day`). Worth deciding whether morph needs one
  before it ever runs unattended on `collect` (§2 of the design commits
  to human-checkpointed correction, but detection itself running
  automatically on every collect, uncapped, against a big manuscript
  is a real cost-runaway shape supplylm's caps exist specifically to
  prevent). Flagging as a design question this test plan surfaced, not
  answering it here.
- **No regression test asserting `lens`/`filter`/`critique` stay
  unaffected by future morph changes** — today that's covered
  incidentally by running the full `test_api.py`/`test_passes.py` suite
  after any `findings.py` edit. Worth formalizing as an explicit CI
  gate description (run both suites on any diff touching `findings.py`
  or `lenses.py`) rather than relying on remembering to do it by hand,
  the way this session did.

## 7. Acceptance-test matrix (folded in from the 2026-09-27 review's §11)

The design is ready to call solid when expected behavior is pinned for
all of these — most map onto specific §1-§6 checks above; a few name
things not yet covered anywhere else (marked NEW):

**Targeting and invalidation** (ties to design §4/§5's fingerprinting
and dirty-set work, not yet implemented — these tests block on that):
edit target paragraph only; edit each of the four neighbor positions
without touching the target; insert/delete a paragraph at the judged
boundary; move a paragraph within a file; change a heading without
changing body prose (covered — §2's `_is_prose` exclusion); change a
ratified concept note or relevant edge; add an alias that makes a
paragraph newly retrievable; run with no prior morph watermark.

**Execution**: one invalid item in an otherwise valid batch reply
(NEW — not yet in §3, worth adding: a reply where one paragraph's
entry is malformed but the rest parse fine should keep the valid
findings, not fail the whole batch); missing/duplicated/unknown
paragraph key (partially covered by §3's key-pinning discussion, no
test written yet); a model timeout after partial results (NEW,
depends on the watermark/resumability work, design §5); rerun after
interruption (same); manual `collect` with morph disabled and
automatic `collect` with morph failing (NEW — nothing today verifies
morph failure can't roll back or block collection, since morph isn't
wired into `collect` at all yet).

**Findings and identity**: identical rerun with an open finding still
pending; identical rerun after rejection; same defect after the
paragraph materially changes; rubric revision with unchanged prose;
same quote occurring twice in one paragraph or file (§1's
`check_replacement_refused_on_ambiguous_quote` covers the last one;
the rest need the fingerprint-based dedup design §5/§7 calls for and
flags as not yet implemented).

**Repair safety**: repair needs a new citation (§3's safety-critical
audit); repair possible from neighbor text (§4's labeled corpus);
repair would remove a protected term (NEW — nothing currently checks
this explicitly; worth a dedicated test once protected-term validation
is wired into staging, not just drafting); two accepted defects on one
paragraph (NEW — blocks on the compose-call design in `morph-design.md`
§6, not yet implemented); another producer already has an open thread
on the paragraph (NEW — blocks on the collision check, design §7, not
yet implemented); author modifies the proposed replacement before
settling (already exercised by the shared Doc-settle machinery lens/
filter use — worth one morph-specific instance of that same test
rather than assuming it transfers for free).

**Reporting and evidence**: negative observations remain queryable
(blocks on §9's structured observation log, design §9, not yet
implemented); abstentions distinguishable from failures (same); final
accepted/modified text linked to contributing findings (already true
via `edit_thread` in finding metadata — worth a test confirming it,
not just trusting the mechanism); staled findings retain their review
history (blocks on the staleness work, design §7).

## 8. Confidence calibration (folded in from the review's §9)

The 24-cell synthetic pilot (most recent run, post-review rubric) is
directional evidence that the detector works at all — it is not
threshold calibration, and the 0.8 floor now wired into `morph.py`
(§3 above) is a starting guess, not a measured number. Before trusting
that number in production:

- Evaluate against a labeled set that includes real manuscript
  paragraphs, deliberate hard negatives, boundary-adjacent edits, and
  at least one concept-alias failure case — not just the synthetic
  essay (this is the same corpus §4 already calls for; calibration is
  one more reason to build it, not a separate effort).
- Report precision and recall per dimension, not one aggregate accuracy
  number — the pilot so far has never distinguished "good at
  specificity, bad at concept-invalidation" from "good on average."
- Compare joint-dimensions-one-paragraph-per-request against the
  bounded-batch version actually shipped (design §3) on the *same*
  cases, since nothing has verified batching itself preserves quality
  — only that per-paragraph windowing beats no windowing.
- Run the same cases across at least two rubric revisions to see
  whether a wording change moves accuracy or just moves *which* cases
  it gets right — useful signal for how much to trust future rubric
  edits. (Nothing is committed to git yet — `morph-rubric.md` is still
  an untracked file this session edited in place; keeping snapshots at
  each real revision, not just relying on git history that doesn't
  exist yet, is a prerequisite for this comparison.)
- Repeat a run on identical input at least twice to measure decision
  stability before treating any single confidence number as meaningful
  — an unstable decision at 0.8 confidence is a different problem than
  a stable one, and nothing distinguishes them today.
- Once real findings exist, have the author rate severity and repair
  usefulness directly — the actual ground truth for "was this worth
  showing you" is the author's reaction, not a synthetic label.

This is a testing/evaluation investment, not a permanent runtime cost
— it doesn't change what `morph run` costs per call once the threshold
it informs is set.

## Suggested order

1 (offline, cheap, highest leverage) → 3's mechanical half (offline) →
2 (offline) → 5 (offline, some replayed) → 7 (pins expected behavior
for the not-yet-built pieces before building them, so §4/§8's real-data
labeling effort has stable ground to test against) → 4 and 8 together
(the parts that cost real tokens and the author's labeling time) → 3's
audit half (ongoing, not a one-time pass). Don't build the live-corpus
pass (§4) before §1-3 are solid — there's no point spending the author's labeling
effort on a detector whose deterministic plumbing (targeting, hygiene
gate, reply parsing) isn't independently verified first.
