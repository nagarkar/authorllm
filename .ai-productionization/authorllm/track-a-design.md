# Track A — Two-step extraction: cut the author's triage burden

Branch `design/track-a`. Opt-in behind `[extraction] adjudicate`, **default off**.
The Sponsor's governing principle throughout: *the point is not to create more work
for the author.*

---

## 1. Why the current pipeline is noisy — root causes, with locations

**Root cause 1 — every candidate the model names becomes a question only the author
can close.** `extraction.py:655` calls `add_concept` on each extracted concept and
stamps it `{"origin": "extracted", "confirmed": false}` (`extraction.py:661-665`);
`extraction.py:733` calls `link_concepts(..., status="inferred")`. Both write
*directly into the graph*. There is no screening step between the model's output and
the queue — the only gates are lexical (retired-name ban, `hygiene.passes_recurrence_bar`,
`endpoint_in_text`). The author is the first thing in the pipeline capable of saying
"that is not a concept."

**Root cause 2 — the near-duplicate firewall exists but this path never reaches it.**
`proposals.create` runs `_suppressed_as_near_duplicate` (`proposals.py:46,58-87`) over
`loop.similarity` before anything is queued. Note updates, aliases, revivals and edge
re-proposals all pass through it. New extracted concepts and new inferred edges — the
1,040 rows — do not. They bypass `proposals.py` entirely.

**Root cause 3 — the feedback that does exist forgets.** `triage_feedback`
(`extraction.py:102-196`) injects the author's precedents into the extraction prompt,
but under fixed recency caps (`LIMIT 40/20/30/20/15`). The comment at
`extraction.py:191-195` already concedes the problem: 538 author decisions on record,
75 reaching the model. Measured on the snapshot: 575 concept rejections exist; at most
40 are ever visible.

**Root cause 4 — over-production is structural, not a prompt accident.** The prompt says
"Aim for 10-20 strong nodes" (`prompts/extraction.md:12`) and `MAX_CONCEPTS = 40`
(`extraction.py:30`) — both **per call**. A hierarchical run issues one call per file
(`extraction.py:481`), so a 24-file manuscript is asked for 240-480 concepts.

**What the root cause is NOT.** The obvious hypothesis — the extractor keeps proposing
duplicates of concepts already in the graph — is measurably false at the repo's own
ratified threshold. Of the 575 distinct rejected names, only 15 have a live concept
within `loop.NEAR_DUPLICATE` (0.72); 118 within a much looser 0.5. Replaying all 914
extracted concepts in creation order, a deterministic "drop anything close to a prior
rejection" rule at 0.72 removes 21 of 914 — 19 correctly, 2 wrongly. **The author's
rejections are semantic ("not load-bearing"), not lexical.** No amount of string
similarity closes this queue. That is the measured case for spending model calls here,
and it is the Sponsor's instinct confirmed rather than assumed.

Snapshot, for the record: 914 machine-extracted concept nodes, 671 of which the author
retired. 874 `concept_triage` evidence rows — 575 `rejected`, 286 `confirmed`. 166
`edge_triage` rows — 83 `rejected`, 46 `confirmed`, 37 `retyped`. Exactly one rejection
in 575 carries a stated reason, so the learning loop gets nothing from them either.

---

## 2. The design

Two steps, as the Sponsor described them.

### Step one — unchanged

`extract_concepts` still sends the changed sections and gets back
`{"concepts", "links", "aliases"}`. **Nothing about step one changes** — not the
prompt, not the payload batching, not the section unit, not the watermark.

### Step two — adjudication (new, `authorlm/adjudication.py`)

Inserted at `extraction.py:561-565`, immediately after `llm.complete_json` returns and
before any of the merge loops. Six lines in `extraction.py`; everything else is the new
module.

**2a. Deterministic retrieval — free, no tokens.** For each candidate concept,
`adjudication.neighbours` ranks every live concept by `loop.similarity` — the same
Jaccard-over-content-words function the proposal firewall is measured on
(`loop.py:102-122`) — scoring the candidate's *name* against the concept's name and
aliases, and the candidate's *name + definition* against the concept's name + notes,
taking whichever is closer. Top 3 above a floor. For each candidate link,
the existing edges between similar endpoints. And — the part the current system
cannot do — the nearest names the author has **already rejected**
(`adjudication._pool` reads them as retired nodes whose origin is `extracted`).

This deliberately reuses the existing near-duplicate machinery rather than adding a
second one. It changes only the threshold, and for a stated reason: `NEAR_DUPLICATE =
0.72` is calibrated for *suppression*, where a false positive silently eats a real
proposal. Retrieval has no such cost — a wrong neighbour is one line the adjudicator
ignores — so `RETRIEVAL_FLOOR = 0.30` buys recall. ("seeds of agency" vs "agency"
scores 0.5: invisible to the firewall, exactly what the adjudicator needs to see.)

**2b. One model call per extraction pass.** Prompt: `authorlm/prompts/adjudication.md`,
registered in `prompt_registry.py` as `adjudication` so `authorlm prompts show
adjudication` prints it and the author can edit it without a code change. The user
message is the rendered candidate block (candidate, its definition, its nearest
existing concepts *with their ratified definitions*, and the author's similar past
rejections) followed by the same text the candidates came from.

The prompt carries the measured calibration rather than a vibe: *"of 874 extraction
candidates the author has triaged, 575 were rejected… a verdict of 'new' is a positive
claim that this candidate is in the surviving third."* This is the Sponsor's "not
rewarding that initial step for producing a shit load of concepts", made explicit and
grounded in their own record.

**2c. Verdicts.** Concepts: `new | improves | subsumed | drop`. Links:
`new | subsumed | drop`. Only `new` costs the author anything.

### How "subsumed by an existing definition" is represented

Two distinct outcomes, because the Sponsor asked for two distinct things:

| verdict | what happens | author cost |
|---|---|---|
| `new` | candidate continues into the ordinary extraction path; **every existing gate still applies on top** (recurrence bar, retired-name ban, `endpoint_in_text`, `MAX_CONCEPTS`) | one triage row, as today |
| `improves` | a `note_update` **proposal** against the existing concept, via `proposals.create` — the existing kind, the existing near-duplicate firewall, the existing review surface | one proposal, and only when the definition genuinely gains |
| `subsumed` | nothing enters the graph. Recorded in an `extraction_adjudication` evidence row as `"<candidate> ⊂ <existing>"` | **zero** |
| `drop` | nothing enters the graph. Recorded in the same evidence row | **zero** |

`improves` is the Sponsor's stated purpose for doing the extra extraction at all: *"if
the definitions that have been introduced can be improved by the text, that they are
improved."* It is a proposal, never a write — settled knowledge stays
machine-unwritable, and the test asserts the node's notes are not rewritten.

The screening log is **one evidence row per adjudication pass**, not one per candidate:
`evidence_type = 'extraction_adjudication'`, `signal = 'screened'`, `supports_belief =
NULL`, `weight = 'low'`, with the full per-candidate detail in `metadata`. It is an
audit trail, not a queue — the author can see everything suppressed on their behalf
without being asked about any of it. `extraction_adjudication` is added to
`db.SYSTEM_EVIDENCE_TYPES` (`db.py:415`) so provenance attributes it to the machine,
never to the author. That matters: mislabelling a machine screening as author evidence
would be the INV-2 failure in a new place.

### Failure behavior

If the adjudication call returns anything other than a dict, `adjudicate` returns the
result **untouched** and reports no stats — a failed screening degrades to today's
behavior rather than silently swallowing an extraction. A candidate the model simply
omits from its reply is treated as dropped and counted in `unjudged`; the prompt says
so explicitly.

---

## 3. What is NOT changing

- **The default.** `adjudicate = false`. With the flag off, `adjudication.enabled`
  returns False, the module is never entered, and the code path is the one that shipped.
- Step one: the extraction prompt, the section unit, payload batching, the watermark,
  the incremental diff, `changed_files_since_extraction`, `_changed_sections`.
- Every existing gate: recurrence bar, retired-name ban, near-miss/variant proposals,
  `endpoint_in_text`, alias validation, `MAX_CONCEPTS`.
- `triage.py`, `triage_transport.py`, the triage GUI, `triage_rules.py`,
  `triage_analysis.py` — untouched.
- Belief promotion thresholds, `_lifecycle_status`, `beliefs.py` — untouched.
- `add_concept`'s existing behavior of updating an existing concept's notes
  (`concepts.py:35-38`) — left exactly as found. Adjudication routes around it by
  removing subsumed candidates *before* the merge loop, so it never triggers it; but
  the behavior itself is not this branch's to change. **Flagged for the Sponsor** — see
  §7.
- No schema change, no index, no migration.

---

## 4. Measurement

`python3 tools/measure_adjudication.py` — zero LLM calls, ~3 minutes. It reads the
read-only pre-campaign snapshot, writes the real manuscript text (24 files, 280,579
chars) into a throwaway workspace, and replays the **real** extraction output — the
914 concepts and 573 relationships the extractor actually produced against this
manuscript — through `extract_concepts` itself, once per file, four times.

```
corpus: SMSTTD — 24 files, 280,579 chars; 914 extracted concepts
        (671 the author later rejected), 573 relationships (193 rejected)

run                                                   concepts  edges  props   QUEUE  calls
BEFORE  flag off (today's pipeline)                        412    192      1     605     26
AFTER   flag on, adjudicator passes everything             412    192      1     605     49
AFTER   flag on, deterministic precedent only (FLOOR)      405    191      1     597     49   1% fewer
AFTER   flag on, author's own verdicts (CEILING)           181    201      1     383     49  37% fewer

deterministic retrieval, over the 671 candidates the author rejected:
  207 were shown an existing concept close enough to be a subsumption candidate
  289 were shown a name the author had already rejected
```

**What each row is.**

- *Before*: the pipeline as it ships. 605 rows of author triage from one full pass.
  (605, not 914, because the recurrence bar and the other gates already drop a third.)
- *Passthrough*: flag on, adjudicator rules everything `new`. **Identical to before,
  605 = 605.** This is the plumbing check: adjudication itself adds and removes nothing.
- *Floor*: no model judgment at all — drop only what is within 0.72 of a name the
  author already rejected. **8 rows, 1%.** The deterministic half cannot do this job.
- *Ceiling*: the adjudicator returns the verdict the author actually gave. Concepts
  412 → 181 (**56% fewer**), queue 605 → 383 (**37% fewer**).

**Honest caveats, stated plainly:**

1. The ceiling is a **ceiling, not a prediction.** It is what a perfect adjudicator
   would achieve. How close a real model gets cannot be measured without billed calls,
   and this branch made none. The realized number lies somewhere in 1%–37%, and where
   it lands is the one thing the Sponsor should insist on measuring before turning the
   flag on for real.
2. Edges go *up* in the ceiling run (192 → 201). This is a second-order effect of
   existing code, not of the design: `extraction.py:684` breaks the concept loop after
   `MAX_CONCEPTS = 40` new nodes per pass. Dropping candidates means the cap is reached
   less often, so more concepts exist by the time the link loop runs, so more links
   find both endpoints known. Worth knowing; not worth fixing on this branch.
3. The last two lines are pure deterministic measurement with no model involved: of the
   671 candidates the author rejected, the retrieval hands the adjudicator a
   subsumption candidate for 207 and a matching past rejection for 289. That is the
   evidence the model gets — it is real, and it is far more than the `LIMIT 40` window
   the current prompt sees.

---

## 5. Cost

Measured character counts from the same run (a full 24-file re-extraction):

| | calls | chars sent |
|---|---|---|
| extraction (step one) | 26 | 420,668 |
| adjudication (step two) | 23 | 587,701 |
| **total** | **49** | **1,008,369** |

- **+1 call per extraction pass that produced candidates.** A pass with no candidates
  makes no adjudication call. Call count roughly doubles.
- **~2.4× the input characters** (~250K tokens vs ~105K at 4 chars/token) for a full
  manuscript re-extraction. Adjudication is the larger half because it re-sends the
  same text alongside the candidate block — the model cannot rule "the text does not
  assert this" without the text.
- **Per ordinary collect** — the incremental case, which is what the author actually
  runs — an extraction is 1–2 payloads. So the real day-to-day cost is **1–2 extra
  calls per collect**, output-token-cheap (verdicts, not prose), thinking disabled.
- No price is quoted here because the model is configurable (`[llm] model`); multiply
  by the vendor's current input rate for `gemini/gemini-2.5-flash`.

The Sponsor chose deterministic to be cheap. The honest trade: this roughly doubles a
cost that was already small, to remove work from the only resource in the system that
does not scale.

---

## 6. Files

New:
- `authorlm/authorlm/adjudication.py` — retrieval, prompt assembly, verdict application
- `authorlm/authorlm/prompts/adjudication.md` — the step-two prompt
- `authorlm/tests/test_adjudication.py` — 14 hermetic checks, zero LLM calls
- `authorlm/tools/measure_adjudication.py` — the measurement above

Changed (small):
- `authorlm/authorlm/extraction.py` — 6-line hook at 561-565; `screened` threaded
  through the two aggregators and the summary
- `authorlm/authorlm/db.py` — `extraction_adjudication` added to `SYSTEM_EVIDENCE_TYPES`
- `authorlm/authorlm/prompt_registry.py` — one registry entry
- `authorlm/authorlm/cli.py` — one clause in the extraction summary line
- `authorlm/config.toml` — `[extraction] adjudicate = false`

---

## 7. What the Sponsor must decide

1. **Turn the flag on for one real extraction and measure.** The 37% ceiling is real;
   the realized number is not known and cannot be without spending calls. One
   `extract --full` with the flag on, against a scratch workspace, settles it.
2. **Is a suppressed candidate acceptable if it is never shown?** The design assumes
   yes — a wrongly dropped concept returns free the next time the author writes about
   it. If the Sponsor wants dropped candidates *reviewable*, the evidence log already
   holds them and a `--show-screened` listing is a small addition. It would also start
   re-growing the queue, which is why it is not in this branch.
3. **`add_concept` silently rewrites an existing concept's notes** when extraction
   re-proposes it with a different definition (`concepts.py:35-38`), *and* files a
   `note_update` proposal against the pre-image (`extraction.py:674`). The write
   happens whether or not the proposal is adopted. This is pre-existing, is not
   touched here, and looks wrong — machine-unwritable settled knowledge is the stated
   rule. Separate decision.
4. **Should adjudication verdicts feed belief learning?** They are logged as machine
   evidence with `supports_belief = NULL` and deliberately feed nothing, pending the
   INV-2 decision. The author's own triage verdicts remain the larger untapped signal
   (belief-audit §"The author's triage labor is not reaching the loop").
