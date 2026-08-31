# Filter Pass — Design Reference

Ratified 2026-08-30. This document is the build reference for the filter
pass: the autoregressive *editing* mode of the writing loop, where one
ratified editorial concern is applied to one essay a paragraph at a time.

Related designs: `autoregressive-writing-design.md` §15.20 (the ruling and
what it means for the write path), `critique-pass-design.md` (the staged-edit
machinery this borrows and generalizes), `sweep-framework.md` (lenses, and
the criterion for which tier a pass runs on),
`margin-threads-design.md` (the `<<old>>{{new}}` grammar).

---

## 0. What a filter is, and what it is not

A **lens** is a ratified prompt that reads one essay whole and reports
*findings*. Its output is a `guidance_history` row; the author rules on it
with `lens review`.

A **filter** is a ratified prompt that reads one essay *unit by unit* and
proposes an *edit* to each unit. Its output is a staged `doc_threads` row —
the same object the critique pass stages, triaged by the same verbs, settled
by the same flow, recorded as the same evidence.

> **A filter is a lens-shaped artifact whose findings are pre-cast as edits,
> and which therefore routes through the edit door instead of the guidance
> queue.**

It rhymes with `lenses.py` in its *artifact* (a markdown prompt in an
underscore directory, an add/list/show family, no code to add a new one).
It rhymes with `passes.py` in its *output*. It rhymes with `writing.py` in
its *execution* (a deterministic payload assembler with a stable cached
prefix, a registered prompt file, and a billed native path that ships
dormant).

**Not a beat** — a beat appends new prose under a plan; a filter rewrites in
place and adds nothing. **Not revision mode** — §11 reserved
`writeups.mode='revision'` for targeted edits, and this settles that question
in the negative: targeted edits belong to the edit door, not the append-only
beat cursor. **Not a critique pass** — no external critic, no campaign, no
toc cursor, no preflight. **Not a sweep** — a sweep narrows and asks one
tight question; a filter asks the same question of every unit.

**Jurisdiction: ONE ESSAY.** A motif used one way in `becker.md` and the
opposite way in `kindness.md` is a real editorial finding and this pass
cannot see it. That question is a lens. The metaphor filter's own prompt says
so, so the author is never misled about what was checked.

---

## 1. The artifact — `_filters/<name>.md`

A sibling directory to `_lenses/`, not a shared one with a kind marker.
Three reasons, in order of weight:

1. `lens list` and `filter list` must not be able to disagree about a file.
   A shared directory with an in-file kind marker means a malformed or absent
   marker silently reclassifies an artifact — and the two kinds have
   different *outputs*, a guidance row versus a manuscript edit. Two
   directories cannot be ambiguous.
2. The underscore prefix is already load-bearing: underscore directories are
   observation-invisible, so a filter prompt can never be read as manuscript
   prose, extracted from, or put in a reading order.
3. `lenses.add_lens` is ten lines. The duplication a sibling costs is smaller
   than the abstraction a shared directory would need, and the two artifacts
   diverge anyway: a filter has front matter.

```
---
class = "sequential"
state = "a running ledger of words already flagged as repeated"
---

# <the prompt, from here to the end of the file, verbatim>
```

- Delimiters are `---`; the body between them is **TOML** — the project's
  configuration language — parsed with stdlib `tomllib`. No YAML dependency.
- `class` is **required**, one of `{"sequential", "global"}`. Absent or
  unknown is refused at `filter add` and again at `filter run`, naming both
  legal values. A filter is never silently defaulted into a class: the class
  decides whether unit 12 can see unit 11's edit, and guessing that is
  guessing about the author's editorial method.
- `state` is optional, one line, **documentation only** — for `filter show`
  and for the payload. The harness never parses the state itself.
- Any other key is refused **by name**, so a typo (`klass`, `type`) fails at
  the moment the author writes it rather than at the first run.
- The prompt body is everything after the closing `---`; its first prose line
  is the summary `filter list` prints.

Names reuse `lenses._NAME`: `^[a-z0-9][a-z0-9-]{1,40}$`.

**Adding a filter in an existing class is a file and no code.** Inventing a
*third* class is code, and this design says so rather than pretending the set
is open — a design that implies an open set gets handed a `whole-file` filter
within a month.

### 1.1 The two classes

|  | `sequential` | `global` |
|---|---|---|
| The question | "given everything that came before, is this unit right?" | "given the whole essay, is this unit right?" |
| Unit N sees | the run's own text for units 1..N−1, plus the carried STATE | the whole pinned essay, plus the frozen REGISTRY |
| Order matters | yes | no |
| Prelude | none, or one **optional**, declared in front matter (§15.22) | one, **required**, before any unit |
| Carried state | yes | no; the registry is frozen instead |
| Examples | duplicate words, audio-friendly voice continuity | metaphor consistency, motif discipline |

**Why `global` units do not also see the filtered prefix.** (a) It doubles
the payload. (b) It destroys the class's one real property — order
independence — which is what lets a global run be assembled in one shot and
re-run for a single unit. (c) The coordination a global filter needs is not
"what did I just write" but "what does this essay do with fire, across all
thirty-four paragraphs", and that is the registry's job.

The cost, stated: **two units of a global run may propose mutually
inconsistent fixes to the same motif.** The registry is the coordination
mechanism; the settle is where the author catches a collision. Every
`replace` names the registry entry it is about, in `ref`.

**Why `sequential` units see the run's own proposals.** A word ledger is
worthless otherwise: shown the *original* unit 3, unit 5 cannot know the run
already removed the second "moreover" there. Conditioning on the run's
proposed prefix is what makes the pass autoregressive rather than N
independent calls with a shared prompt.

The cost, stated, and it is real: **the settle can falsify the
conditioning.** If the author rejects unit 3, every later unit was drafted
against a prefix that did not survive. This **warns and never blocks**
(§15.13); the settle report prints the downstream list and the `--from n`
remedy. The list is derived, never stored. It is deliberately *not*
automatic: re-running the tail discards the author's verdicts on those units,
and discarding verdicts is never something a verb does on its own.

---

## 2. The unit, and the matching law

Units are `passes.paragraphs_of(text)`, 1-indexed — the same split the
critique pass uses. **One definition of "unit" across the whole door**, so a
filter proposal and a critique proposal on the same paragraph are literally
the same kind of object. A unit whose first line is a markdown heading is
marked `heading: true` in the payload — deterministic, free, and it lets a
prompt say "leave headings alone" without the harness enforcing a rule that
belongs to editorial law.

**Filters replace WHOLE units. They never insert and never delete.**

- `action` is `keep` or `replace`. `insert` is refused: a filter passes each
  bit through, it does not add bits.
- An empty `new` is refused — "delete this paragraph" is not a filter's
  judgment to make.
- A `new` containing a blank line (a *split*) is **allowed**:
  `threads.PENDING` is `re.DOTALL`, so the form parses and collapses to two
  paragraphs at resolve.

**`old` is never taken from the model.** The recorder reads the unit's text
from the capture and uses that as `proposed_old`; the reply supplies only
`n`, `echo`, `action`, `new` and `why`. Whole-unit replacement rather than
sub-spans, deliberately: the door's matching law is `proposed_old` equality,
and a second matching law for sub-spans is a second way for the door to be
wrong. The cost is coarser diffs, which the author reads in the file anyway.

### 2.1 Anchoring hygiene, in one place

| Check | Fires at | On failure |
|---|---|---|
| `n` in range `1..len(units)` | `filter record` | refuse the whole reply |
| one entry per unit in the window, in order | `filter record` | refuse, naming the missing `n`s |
| `echo` equals `passes.echo_of(unit)` | `filter record` | refuse, printing expected vs. got |
| `action` in `{keep, replace}` | `filter record` | refuse the whole reply |
| `new` non-empty | `filter record` | refuse the whole reply |
| `new != old` | `filter record` | silently downgraded to `keep` |
| `<< >> {{ }}` in `new` | `filter record` | refuse, naming the marker |
| state within the 4,000-char cap | `filter record` | refuse, naming the size |
| `proposed_old == the unit` at compose time | `filter settle` | raise, name the unit, say the text drifted |
| the form is present verbatim after marking | `filter settle --pause` | unmark and refuse |

**The whole reply is refused, never part of it** (`passes.ContractError`'s
standing rule). A partially admitted reply is a run whose conditioning nobody
can reconstruct.

Note the distinction warn-never-block turns on: a reply that **omits units it
was asked about** is a contract violation — refuse, and the assistant
re-answers; this is not the author's business. A **run that never covers some
units of the essay** is an ordinary open editorial state — it warns at settle
and never blocks, exactly as an unwritten beat does.

---

## 3. The state model

**State is opaque carried text. AuthorLM stores it and re-renders it; it
never parses it, never schematizes it, never validates its content.** The
Sponsor's own examples are heterogeneous — a word ledger, a metaphor
registry, a voice-continuity note — and any schema imposed here is a schema
the next filter's prompt has to fight. The prompt is the filter; a state
schema in code would be a second, hidden half of the prompt the author cannot
edit.

- The reply carries a top-level `"state"` string; `filter record` writes it
  verbatim to `filter_runs.state`, replacing the previous value.
- `filtering.assemble` renders it into block C. Absent renders `(none)` — a
  section that comes and goes changes the block's shape, and shape changes
  are cache invalidations.
- **Capped at 4,000 characters**, refused over rather than truncated: a
  silently truncated ledger lies about what has been flagged. Without a cap a
  long run's state grows without bound and the volatile block eventually
  dominates the payload.

The **registry** is the same idea with a different lifetime: written once by
the prelude, stored in `filter_runs.registry`, rendered into **block A** (the
cached layer), immutable for the life of the run. `filter prelude --replace`
rewrites it and invalidates the cached prefix, which the verb says out loud.

**A prelude artifact that is not a registry (§15.22).** A prelude generalizes
to *a whole-essay pass that runs before any unit is judged and produces a
run-scoped artifact*. For `global` that artifact is the frozen registry, above.
For a sequential filter that declares `prelude = "pronunciations"` in its front
matter, the artifact is a set of **proposals on the ordinary queue**: nothing
enters block A, nothing is frozen into the run beyond
`metadata.prelude = {kind, at, proposed, suppressed}`, and the run's mechanics
are untouched. It is **optional** where the registry is required, because a run
whose dictionary diff never ran is still a correct audio pass, while a global
unit judged against no registry is judged against nothing — `filter run` prints
one line and proceeds. A second prelude on the same run refuses without
`--replace`, in the registry's own shape.

---

## 4. Approximate idempotency — the mechanism, and its boundary

Ruled: *"We don't need perfect idempotency but yes, we need approximate."*
Four mechanisms, decreasing strength. No convergence machinery, no fixpoint
loop, no "run until stable".

**M1 — the identical-text refusal (the only hard guarantee).** Every settled
run records the `manuscript_versions.id` it produced. At `filter run`, if the
file's current text is byte-identical to the text a prior *settled* run of
this same filter on this same file produced, the run refuses and names the
prior run, its date and its tallies. `--again` proceeds.

**M2 — the prior-runs block (the mechanism that does the real work).** Block
A carries `PRIOR RUNS OF THIS FILTER ON THIS FILE`, rendered from every
settled run except the current one, with the author's rejection reasons
verbatim. Their reasons are the highest-value evidence this system collects;
putting them in the *cached* layer of the next run is the cheapest possible
way to stop a filter re-proposing what the author already refused.

**M3 — the prompt's standing paragraph** in `prompts/filter-run.md`.

**M4 — fresh state per run.** `filter_runs.state` is never carried between
runs: a prior run's ledger described a prior text, and re-loading it would
make run two condition on a fiction.

**What this does NOT guarantee**, because the author is owed the boundary:

- Text that changed *slightly* between runs may produce a proposal set that
  differs in more than the changed units. Nothing pins the model's attention.
- Reject, change nothing, `--again`: M2 makes re-proposal unlikely, not
  impossible. There is no hard suppression key. There could be — a
  `dedupe_key` on `(filter, file, unit, hash(old))`. It is deliberately
  **not** built: a unit's text changes for a hundred innocent reasons and a
  stale suppression key would silently hide a real finding. The rejection
  *reasons* are the softer, honest instrument.
- A global run's registry is re-derived every run and will differ in wording
  even on identical text. Only proposals are compared, never registries.
- **Two filters do not commute.** No ordering is enforced or recommended; if
  the author wants an order, that is editorial practice, not a feature.
- **Nothing converges.** A filter is not a fixpoint operator. If a filter's
  proposals never stop, the filter's PROMPT is wrong, and the remedy is
  `filter show` and an edit — not another run. `filter status` prints the run
  history per (filter, file) so that is visible without having to notice it.

---

## 5. The run record — `filter_runs`

A new table. A filter run has no host row: it is not bound to a writeup, an
intent, a critique pass, or a session. `critique_passes` is the wrong shape
in every column and overloading it would corrupt that pass's state machine.
`guidance_history` rows are proposals, not run headers.

```sql
CREATE TABLE IF NOT EXISTS filter_runs (
    {KNOWLEDGE_OBJECT_COLUMNS},
    manuscript_id       TEXT NOT NULL,
    filter              TEXT NOT NULL,   -- _filters/<name>.md
    class               TEXT NOT NULL,   -- sequential | global, FROZEN at run start
    file                TEXT NOT NULL,   -- relpath; exactly one file per run
    source_version_id   TEXT,            -- the pin: the text units were read from,
                                         -- and the rollback target
    unit_count          INTEGER NOT NULL DEFAULT 0,
    cursor              INTEGER NOT NULL DEFAULT 0,
    state               TEXT,            -- opaque carried text (sequential)
    registry            TEXT,            -- opaque frozen text (global prelude)
    result_version_id   TEXT,            -- produced at settle
    status              TEXT NOT NULL DEFAULT 'active'  -- active|settled|abandoned
);
```

`class` is **frozen at run start**. Editing `_filters/<name>.md` mid-run
cannot change a live run's mechanics; the run notices the drift and warns.
No index (§15.17's discipline; `dbperf` is the trigger).
`KNOWLEDGE_OBJECT_COLUMNS` brings `metadata`, which brings the client
provenance stamp for free.

**Concurrency: one active run per (filter, file).** Two *different* filters
may be active on one file — they are different concerns — but their staged
edits can collide on the same unit, so run creation **warns loudly** and
names the other filter. It does not refuse: two filters on one essay is
legitimate, and a refusal would be the machine deciding the author's work
order. Whichever settles second hits `compose_marked_text`'s drift check and
refuses loudly, which is a safe failure.

---

## 6. Attribution — no episode, and that is the point

`passes._edit_evidence` has always written `episode_id=None`. That is the
edit door's precedent and it is right here for the same reason: an episode
records authorial work *in service of a declared goal*, and episode analysis
mines it into precedents and beliefs. "Removed the second 'moreover'" is not
a precedent worth mining, and filing it under "rewrite Becker to foreground
the refrain" would make §15.17's completion analysis report that goal as
served by a duplicate-word sweep. That is a lie of attribution.

1. **Verdict evidence:** `evidence_type='filter_edit'`, `episode_id=NULL`,
   `weight='high'`, byte for byte the shape `critique_edit` already builds.
   **Explained rejections still reach belief learning**, because that path
   runs off the evidence stream and the explanation, not off an episode.
2. **Transitions from the settle's collect:** attached to **no episode**,
   through `api.NO_EPISODE`. `episode=None` already means *ambient* — the
   session's most recently created open episode, whatever goal it belongs to
   — which is exactly §15.17's mis-attribution. A filter has no goal to be
   filed under, so it is filed under none. The version history, the evidence
   rows and the run row are the complete record.
3. **Intents appear in the payload and own nothing.** Block A carries the
   in-scope active intents, tier label and statement only, labelled the way
   `beat-draft.md` labels them: *a goal governs selection and emphasis, never
   grounding — and here, never attribution.*

**This exposed the same hole in `critique resolve`, and it is fixed in the
same stream.** `_critique_resolve_essay` collected ambiently, so every
critique-pass resolve attached its transitions to whichever episode happened
to be open. It now passes `NO_EPISODE`. Its FIRST collect
(`pre-critique-resolve`) stays ambient deliberately: it snapshots the
author's own uncollected local edits, which predate the verb.

---

## 7. The finding→edit door

> A producer may stage an edit if it supplies: `file` (a relpath in the
> reading order), `n` (a 1-based unit index), `echo` (the unit's first five
> words), `new` (non-empty, free of the reserved markers, different from the
> unit), and `why`. **The harness supplies `old` from the file itself. The
> producer never supplies it.**
>
> In return the edit receives: a `doc_threads` row whose shape it does not
> own; the triage verbs with their evidence; the compose-and-pause; the
> resolve with the author's post-edits winning; the proposal→final diff as
> evidence; and the rollback to a pinned version.
>
> A producer that cannot supply a verbatim-anchored unit gets **nothing**.
> No partial admission, no fuzzy match, no "closest paragraph".

`origin_type` names the producer: `author_comment`, `critique`, `filter`, and
`lens` when a lens finding starts carrying a proposal. `origin_id` is
`{owner}:{file}:{ordinal}`, so re-runs replace in place under the existing
`UNIQUE (manuscript_id, origin_type, origin_id)`.

### 7.1 What was already origin-agnostic

Three of the four settle functions read only thread fields:
`compose_marked_text`, `final_text_from_marked`, `verdict`. The fourth,
`staged_threads`, hardcoded one literal, and `_edit_evidence` hardcoded one
more. So the door cost **four parameters, all defaulting to today's values**:

```python
staged_threads(..., origin_type: str = "critique")
_edit_evidence(..., evidence_type: str = "critique_edit")
verdict(..., evidence_type: str = "critique_edit")
record_resolution(..., origin_type: str = "critique",
                       evidence_type: str = "critique_edit")
```

The check that this step is right is that the existing suite stays green
**with no edits to it**. `staging.py` owns only what is genuinely new — the
staging entry point that takes source-derived `old`, the local transport, the
direct apply, the rollback — and it *calls* `passes`; it moves nothing out.

### 7.2 A lens finding walking through the door — specified, not built

Give the lens finding schema one optional field, `replacement`. When present,
`_store_findings` locates the unit containing the quote, **refuses the
proposal if the quote occurs in more than one unit or more than once within
its unit** (an unanchored `str.replace` picks an occurrence, and picking is
guessing), builds `new`, and stages it with `origin_type='lens'`. Open
question: whether ruling on the finding settles the edit. Default: they are
independent, and `lens review --accept` prints a line saying the edit is
still open. Two verdicts on one object is worse than one verdict that leaves
work visible. Nothing built this stream.

---

## 8. The local settle transport, and the canonicalization it rests on

**This is the riskiest decision in the design.**

The critique settle's *pause* lives in Google Docs. For a filter that is
wrong three ways: a run on a 34-paragraph essay produces dozens of forms and
an OAuth round trip; the author reads essays in Obsidian; and the Doc bridge
is CLI-and-credential-bound, which would make the filter pass unusable in
exactly the key-less offline shell the testbench and the write loop already
work in.

So the door gains a **local transport**:

1. `filter settle <file> --pause` runs `passes.compose_marked_text` — the
   pure function that is already the Doc composer's source of truth — and
   writes the result **to the local file**. Visible text in the file is the
   affordance, because the author opens these files in Obsidian and a hidden
   marker fails in exactly the place that matters.
2. The author reads and post-edits the `{{new}}` halves. Their words win.
3. `filter settle <file>` reads the local file and runs
   `threads.pending_forms` → `passes.final_text_from_marked` →
   `passes.record_resolution` — **all three unchanged**. Byte for byte the
   critique resolve's second half with `critique_tab_markdown` replaced by
   `path.read_text`.

**Direct apply is the DEFAULT** (no flag), because a filter's edits are
mechanical and numerous, the triage verdict already *is* the author's ruling,
and `revise` already carries their wording. The pause is for the filter whose
edits the author wants to read in place. As built they are literally the same
code: the direct apply composes the marked text and resolves it in one
breath, so there is one composition routine and not two. `--pause` on a run
whose threads are already `written` is refused with the state named.

### 8.1 The canonicalization

While the file is marked, the bytes on disk are not the essay. Six consumers
would be lied to: `collect` would record markers into version history, the
extractor would mine them, the summarizer would summarize them, the
realization scan would match inside them, `export` would ship them, a lens
would report on them.

The answer is the law already ratified in `threads.py`'s docstring — *"while
a thread is pending, the CANONICAL text is the old text; ratified content
changes only at approval"* — applied to local files too, in one place:

```python
# authorlm/revisions.py — read_manuscript_files, beside strip_embed_lines
files[rel] = threads.strip_replacements(strip_embed_lines(text))[0]
```

**`strip_replacements`, and NOT `strip_pending`, and this is the correction
that matters most in this section.** The design first specified
`strip_pending`, on the reasoning that it is "pure, idempotent on unmarked
text". That claim is false, and the falsity is a data-loss bug.
`strip_pending` is the DOC canonicalizer: in a Doc tab nothing put `{{…}}`
there but AuthorLM, so a bare one is a critique-pass insertion and collapsing
it to nothing is correct. Pointed at LOCAL files the rule inverts — nothing
put `{{…}}` there but the AUTHOR. `{{title}}` in a template, `{{a, b}}` in
set notation, a Handlebars sample in an essay about templating are ordinary
prose, and the broad strip deleted every one of them from everything the
system observes: the version history, the summaries, the concept scans, the
export. Silently, because once they were gone `_ANY_MARKER` had nothing left
to warn about. `staging.is_marked` tripped on them too, refusing such a
file's Doc push forever with a message about a settle that did not exist.

So the local grammar is the **replace form alone**: `<<old>>{{new}}`
collapses to its old half, a surviving `<<` or `>>` is warned about, and a
bare `{{…}}` is left exactly where the author put it. A filter never stages
an insertion — `insert` is refused by the recorder, because a filter passes
each bit through rather than adding bits — so narrowing loses nothing this
seam exists for. `strip_replacements` IS a true no-op on text carrying no
`<<old>>{{new}}` form, which is what makes it safe here unconditionally.

The same narrowing applies at three more sites, for the same reason:
`staging.is_marked` (a file with braces is not mid-settle),
`staging.unmark` (a recovery that damaged the file it recovered would be no
recovery), and `staging.resolve_local`, which passes `kinds=("replace",)` to
`final_text_from_marked` — an unmatched insertion form collapses to its old
half, and for an insertion that is the empty string, so a settle that looked
at bare `{{…}}` would have deleted the author's `{{title}}` from the finished
essay. The critique pass keeps both kinds, where both are its own.

**This seam is already the canonicalizer, which is the argument for putting
it here.** `read_manuscript_files` has never returned the bytes on disk: it
drops illustration embed lines because they are local derived machinery the
observed manuscript must not contain. A pending form is the same kind of
thing by a different road. Every observer goes through this seam, so one line
covers all six; the only code that sees markers is the settle code, which
reads `Path.read_text` directly and owns the grammar.

**The smaller alternative, recorded so the choice is knowing:** apply
`strip_pending` only in `summaries.capture` and `collect_revision`, covering
the two consumers that can *write a claim*. It is two lines instead of one
and it leaves `export.publish_markdown` able to ship a marked essay — the
failure mode §14.3 treats as the one that matters most. The single
canonicalization is what shipped.

### 8.2 The guards, which are NOT free

`push_doc` reads the file with `path.read_text`, **not** through
`read_manuscript_files`, so the canonicalization does not hide the markers
from it. Its local-text guards were `_refuse_mid_rewrite` (a whole-file
placeholder check) and `critique_forms_pending`, which queried
`origin_type = 'critique'` only. A marked filter file would have pushed its
markers straight into the Doc, and the next pull would have overwritten the
local file and **silently discarded the settle**.

1. `critique_forms_pending` generalizes to `forms_pending(db, mid, relpath)`
   over any `origin_type`, returning the origin so the refusal names the
   right remedy (`critique resolve` or `filter settle`).
2. `_refuse_mid_rewrite` grows a second case: `staging.is_marked(text)`, a
   byte check on the raw file exactly as `api.is_placeholder` is. It is the
   guard that survives a database that has lost the run row. Both `push_doc`
   and `diff_push` already call it, so one edit covers both.

They are ordered **most-informative first** (the DB guard above the byte
guard) so both are separately reachable, which is what makes the two
assertions discriminate rather than one shadowing the other.

3. The pull path gets the symmetric guard: a marked local file is skipped and
   reported by name, in the shape `conflicts` and `local_ahead` already use,
   and `--force` cannot reach past it. The checkout gate makes this mostly
   unreachable; "mostly unreachable" is not a guard.

Both push guards are tested by their **outcome**, against a recording Drive
double: the question is not "did `push_doc` raise?" — a `None` service
answers that by accident, and would raise just as loudly *after* forming and
sending the body — but "did any text containing `<<` reach the wire?". With
the guards, no request body is formed at all. With both disabled, the same
double records the markers, which is what makes the first assertion mean
something.

`filter rollback` **refuses on a marked file**, naming both exits. A rollback
mid-pause would destroy post-edits the author may already have made, with
nothing left to recover them from — a strictly worse orphan than the one
`filter status` finds, where the bytes describe the state completely.

A crash between "compose" and "write threads" leaves a marked file with no
written threads. `filter status` detects it (bytes carry forms, no matching
row) and `filter unmark <file>` is `strip_pending` written back — a two-line
recovery for a state that is fully described by the bytes.

---

## 9. Execution — chat mode first

```
authorlm filter run <name> <file> [--window N] [--from N] [--again] [--native]
```

- **Without `--native` it makes no model call at all.** It assembles and
  prints the payload — the same blocks, the same checksums, the named
  registered prompt — and the conversation drafts the reply against it.
- `--native` is the billed path, and it refuses when `[filtering]` is absent.

**The flag polarity is deliberately inverted against `write draft`.**
`--dry-run` exists there because that verb was built billed and the ruling
came later, so the flag now names the *default* flow, which is backwards.
`filter run` is chat-first from its first commit, so the zero-call behaviour
needs no flag. The cost is two verbs with opposite defaults; the mitigations
are `filter run --dry-run` accepted as a silent no-op alias, and the
inversion stated out loud in the verb's output, in `--help`, in the skill,
and in the tutorial.

**The class decides the round-trip shape:**

| | chat mode (default) | native mode (dormant) |
|---|---|---|
| `sequential` | **one reply** for the whole window: the assistant processes units in order *inside its own reply*, carrying the state forward | one call **per unit**; state carried through `filter_runs.state` |
| `global` | one reply for the prelude → the registry; then **one reply** for all units | one call for the prelude, then one per unit, sharing the S+A prefix |

The autoregressive discipline is *preserved* in chat mode, not approximated:
the assistant genuinely conditions unit N on its own output for N−1, within
one generation. That is the ruling read literally, and it costs one round
trip instead of thirty-four.

### 9.1 The payload — `authorlm/filtering.py`

```
Block S  (system; cache breakpoint 1)
  prompts/filter-run.md              the harness prompt, verbatim
  THE FILTER (ratified by the author) the artifact body, front matter stripped
  STYLE LAW                          styles.render(db, mid, file)

Block A  (user; cache breakpoint 2)
  INTENTS                            in-scope active; emphasis only, never attribution
  VALIDATED BELIEFS                  sorted by statement, no confidence
  CONCEPT NOTES                      api.scoped_concepts(file, text=capture), sorted
  PROTECTED TERMS                    every class: the derivation, §15.22 §1.1
  PRONUNCIATION DICTIONARY           every class: pronunciations.md, read live
  PRIOR RUNS OF THIS FILTER ON THIS FILE   dates, tallies, reasons verbatim
  THE ESSAY                          global class only: every unit, numbered
  MOTIF REGISTRY                     global class only: the frozen prelude output

Block B  (user; volatile, grows)
  FILTERED PREFIX                    sequential only; global: (not applicable)

Block C  (user; volatile)
  FILTER STATE                       sequential only: the carried state, or (none)
  THE UNITS                          the window: n, heading flag, echo, verbatim text
  OUTPUT CONTRACT                    the reply grammar
```

Every serialization rule is inherited verbatim from `writing.py` and every
one applies: beliefs sorted by **statement** with no confidence printed
(confidence moves on every explained verdict, so printing or sorting on it
silently re-bills the cached layer); intents as tier label and statement
only; no row ids, no timestamps, no counters; an absent section prints
`(none)`.

PROTECTED TERMS and PRONUNCIATION DICTIONARY render for **every filter of
every class, always** — a section that comes and goes is a shape change, and
shape changes are cache invalidations; the dictionary's terms join the
protected set, so `duplicate-words` needs it too; and a `global` filter needs
the protection as much as a sequential one, because a motif family's own name
is a protected term. The dictionary is read LIVE from the file rather than
pinned to `source_version_id`, so an accepted pronunciation takes effect at the
next WINDOW; that invalidates block A honestly, and `filter run` already prints
the per-block hashes. `api._filter_capture` reads it once per invocation and
hands it to run, record, prelude and settle alike.

Two notes worth recording because they **invert** the write path's:

- **`scoped_concepts(file=file)` works here and does not there.** A writeup
  truncates the essay to a placeholder, so the file-scoped graph slice is
  empty for the very file being drafted. A filter runs on an intact essay.
- **There is no drafting context and no before/after summaries.** Requiring
  `passes.summaries_ready` would make every filter run block on the book's
  summary state — §14.1's defect wearing a new hat. The price is the
  cross-essay view, and §0's jurisdiction boundary is how it is paid.

`filter run` prints `payload.hashes` and `payload.sizes` per block, so a
silent cache invalidator is findable and the author can audit what is sent.

### 9.2 The reply grammar

JSON, not labelled prose: a beat is one continuous piece of prose where
escaping is a hazard, but a filter reply is many small keyed records that
need alignment against unit indices and echoes. The same grammar serves the
chat path (the assistant writes the JSON into a heredoc) and the native path.

```json
{"units": [{"n": 8, "echo": "And so the wall stands", "action": "replace",
            "new": "…the rewritten paragraph…",
            "why": "second 'moreover' in three units",
            "ref": "moreover"}],
 "state": "…opaque text this filter's prompt defines…"}
```

`ref` is optional free text: what the entry names in the state or registry.
Stored on the thread's metadata and used by the settle report to group a
global run's proposals, which is how a motif collision becomes visible.
`state` is required for `sequential`, optional for `global`.

### 9.3 The native path — fully specified, shipping dormant

`[filtering]` is a config section the shipped `config.toml` **does not
have**, the same way `[writing]` and `[budget]` are absent, and for the same
reason: *what is absent is what is off.* Third instance, and now the standing
shape.

- **No fallback to `[llm] model`.** A silent degradation to a different tier
  is a result the author would attribute to the loop rather than to a line of
  configuration they never wrote. The refusal names `filter run` (no flag) as
  the escape hatch, so a missing section can never make the pass unusable.
- **The recommended model is the CHEAP tier, the opposite of the drafting
  ruling** — deliberately. Drafting's quality comes from the model's
  judgment, so economy there comes from caching and effort, not a smaller
  model. A filter's quality comes from the assembled context and a prompt
  that fully specifies the check, which is the sweep framework's own
  criterion for the flash tier. *A filter that needs a mind rather than a
  rule is a lens.*
- `llm.filtering_llm(config)` mirrors `llm.writing_llm` exactly;
  `FILTERING_ABSENT` mirrors `WRITING_ABSENT`.
- **Caching**: `complete_json_blocks` puts two `ttl: 1h` breakpoints on
  blocks S and A through the same `_block_messages` the drafting path uses.
  `caching_available` requires litellm transport *and* an anthropic-family
  profile; under the recommended recipe that is False, so the native path
  ships with **no cache behaviour at all** and the breakpoints are dead code
  until someone configures an Anthropic model. Said plainly, because a saving
  that does not exist is worse than no saving. The `cache_cold` warning is
  **not** reused: its conditions are about a beat loop.
- **MODEL_PROFILES**: nothing new. **Budget**: `budget.gate("filtering", …)`
  is reached automatically; no new seam. **The AH-3 guard** passes because
  the section is absent, and would keep passing under the recommended recipe
  because Luna is an `openai/` model.

---

## 10. Composition with standing doctrine

**One capture per invocation.** `filter run`, `filter record` and
`filter settle` each take exactly one `summaries.capture` and hand it to the
gate *and* to the assembly: the in-flight check, the unit split, the echo
validation and the `old` extraction all read the one capture. §14.7's
dangerous shape is precisely what `filter record` would otherwise be, since
it validates echoes against the file and then stages `old` from it.

**A run pins its source version**, and `filter record` **refuses** if the
file's current text no longer matches, naming `--again`. That is the
one-capture rule held across the conversational gap between assembly and
registration — a gap the write path does not have.

**Placeholders and in-flight files are REFUSED.** `api.is_placeholder`; the
file in the capture's in-flight set (refusal wording borrowed verbatim from
`passes.build_context`); and `api.marker_present`, the weaker check, which
warns rather than blocking.

**Checkout gate** on `run`, on `record`, and on `settle` — the gate on
`record` closes the same push→pull window `write propose`/`accept` closed.

**Summaries are NOT required to run**, but `filter settle` rebuilds the
essay's summary exactly as `critique resolve` does, because the text changed
and a stale summary is a lie the next gate will refuse. That is one billed
summarizer call per settle, on the cheap tier, and it is **the one model call
the whole chat-mode filter path makes**. It is announced with its usage line
and fails soft. It marks downstream essays `upstream_stale`, and the settle
output says so.

**Usage ledger purpose tags.** Native path: `purpose = "filtering"`, the
config section that chose the model. Chat path: nothing to tag and nothing
invented — the conversation's consumption is counted by the transcript
adapter and reported with no dollar figure.

**Provenance.** `ko_fields` gives every `filter_runs` and `doc_threads` row
the join key. A run additionally keeps a touched-by list via `clients.touch`,
so a run assembled in one chat and settled in another announces the seam —
which is the *ordinary* case here.

**Blackboard doctrine, the checklist line.** Every read of shared authorial
state is capture-consistent or pinned. Every attribution refuses rather than
guessing: evidence carries no episode, transitions attach to none, and a run
whose artifact class changed under it says so rather than switching. Every
concurrent writer invalidates loudly: an in-flight neighbour refuses, a
drifted pin refuses, a second filter's proposals warn by name, and a marked
file is a state the bytes themselves declare.

---

## 11. The verbs

```
filter add <name>                 the artifact on stdin; refuses a bad class by name
filter list | show <name>
filter prelude <name> <file>      global (registry JSON on stdin) or a filter
                                  declaring prelude = "pronunciations"
                                  ({"pronunciations": [...]} on stdin); --native
filter run <name> <file>          assembles a window; NO model call
filter record <file>              the reply JSON on stdin
filter edits <file>               the active run's proposals, numbered
filter triage <file> --accept … --reject … --reason "…" --revise … --text "…"
filter settle <file> [--pause]
filter status [<file>]
filter unmark <file>              strip_pending written back
filter rollback <file>            restore the pin; verdicts stay as evidence
filter abandon <file>             drop the run; withdraw its open proposals
```

MCP carries `list_filter_edits` and `triage_filter_edits` only. **`filter
run` is deliberately NOT on MCP** — the same ruling the write loop gets: the
loop is CLI-only so there is one call surface, and improving a verb improves
every session without touching the skill.

`filter edits` and `filter triage` are scoped to the file's **active** runs.
A settled run's rejections are history and belong in the next run's PRIOR
RUNS block, not in a triage list where `--accept 1` could resurrect one.

A pronunciation prelude's output never touches the edit door: it files
`pronunciation` proposals on the ordinary knowledge queue, so `proposal
review | list | accept | dismiss` and `list_proposals` / `resolve_proposal` on
MCP carry it with no new surface. That is where the *review* half is reachable
from a chat session, which is where the author actually rules.

---

## 12. Tests

- `tests/test_passes.py` — the local transport at the seam, with no filter
  verbs: the canonicalization, both push guards separately (the second with
  the run's threads deleted from under it), the pull guard, and the
  critique-resolve attribution regression.
- `tests/test_e2e.py` Scenario FP — the whole pass through the real CLI:
  artifact and class, payload determinism (S and A byte-identical across
  windows), every anchoring refusal asserting *twice* (the message, and that
  nothing was staged and the cursor did not move), the door, the local
  transport, approximate idempotency, warn-never-block, and the refusals from
  doctrine.
- `tests/test_e2e.py` Scenario PR and Scenario PB — protected terms and the
  pronunciation dictionary (§15.22): the derivation over a fixture graph
  carrying every kind and every status, block-A byte stability across
  processes and hash seeds, the honest invalidation, `protected_loss`, every
  refusal of `parse_pronunciations` asserting twice, the full cycle (flag →
  accept → re-run → neither flagged nor re-proposed), immutability across all
  nine verbs, every exclusion one assertion each, the three Doc-sync guards
  and the pull's zero-rows refusal that `--force` cannot pass.
- `tools/testbench.py --check pronunciations` — the live check against the
  real manuscript, in `--check filter`'s shape and discriminating the same
  way.
- `tools/testbench.py --check filter` — the live check, in `--check
  placeholder`'s shape. It asserts the echo refusal ON THE DATABASE, that
  `proposed_old` came from disk, that the marked form is visible markdown in
  all four hiding places, and — live — that every read path still reports the
  original text while the file is marked. It **discriminates**: the hermetic
  suite runs it against a bench whose forms are hidden inside an HTML comment
  and asserts it fails by name.

---

## 13. Deliberately not done

- **Cross-essay filters.** One essay is the jurisdiction; the book-wide
  question is a lens.
- **Convergence.** Four mechanisms and no fixpoint loop.
- **A suppression key.** It would harden re-proposal suppression and would
  also silently hide a real finding the moment a unit's text moved for an
  innocent reason.
- **A third class.** Adding a filter in an existing class is a file and no
  code; inventing a class is code, and says so.
- **Filter ordering and composition.** Two filters do not commute and no
  order is enforced or recommended. That is editorial practice.
- **The lens door.** Specified in §7.2 so that when it is built it is three
  lines and not a refactor. Nothing built this stream.
- **Parsing motif families out of the figure law (§15.22).** Two `[figure]`
  elements, two grammars, no schema, and both are sentences the author may
  reword at any ratification. The prompts point at the law instead, which is
  strictly more correct than a copy that can disagree with it.
- **A hand-maintained lexicon file.** The graph is the vocabulary. A second
  source that can disagree with it is the ambiguity §1.2 refuses.
- **Italic-borrowed terms as a derived category.** The only source is `*…*`
  in prose; parsing it is prose parsing, it collides with ordinary emphasis,
  and it is not stable across a Doc round trip — which would break the
  byte-stability the cached layer rests on. The prompt still sees the
  italics, because the units are verbatim.
- **Refusing a reply that drops a protected term.** It warns. The harness
  supplies law; it is not the editor.

---

## Appendix — the three filters, in full (the ratification surface)

These are ratifiable artifacts, written to be read by the author and piped into
`filter add`. They contain editorial law only: the reply grammar, the idempotency
paragraph and the conditioning rules live in the harness prompt
(`authorlm/prompts/filter-run.md`, §9.2 above) and are not repeated here, which
is what keeps each one short enough to actually rule on.

They live HERE and not in a shipped library: they are the author's, they belong
in the gitignored `<manuscript>/_filters/`, and this appendix is the ratification
surface. The assistant pipes each into `filter add` once the author approves it.
A `--from-library` flag would be machinery in place of a conversation, and the
conversation IS the ratification.

**The genericity boundary (ratified with §15.22).** A filter artifact is a
per-manuscript object BY DESIGN, and that specificity is the point rather than a
defect: a duplicate-words filter that did not know this author repeats
deliberately would strip the refrains out of the book. The line, in one sentence:

> **A filter's prompt carries the author's DOCTRINE; the payload carries the
> book's DATA; and where a prompt enumerates data the payload can supply, the
> enumeration goes.**

An enumeration in a prompt is a copy of the graph that cannot grow, cannot be
curated, and drifts silently the moment the author adds a concept — which is the
whole reason PROTECTED TERMS exists. So the three artifacts below no longer name
this book's terms of art (the PROTECTED TERMS block is the authority) or its
motif families (STYLE LAW's `[figure]` elements are). The distinction pairs stay
listed, with STYLE LAW's `[lexicon]` elements named as the authority the list
only illustrates — a template needs a worked example, and it must not pretend
the example is the law. Refrain-by-default, cut-don't-substitute,
precision-over-brevity and "a listener cannot look back" stay exactly where they
are, in the author's words, because that is what a per-manuscript artifact is
FOR.

What the three become is **templates**: a second manuscript copies them, argues
with the doctrine, and inherits the vocabulary, the families and the
pronunciations from its OWN graph, style law and dictionary with no editing at
all. Scenario PR's template check asserts it — an artifact that names a term of
this book is a template that will lie to the next one.

### `duplicate-words` — sequential, word-ledger state

```markdown
---
class = "sequential"
state = "a ledger of every word or phrase already flagged as repeated, with the units it was flagged in and what was done about it"
---

# Duplicate words and phrases

Flag a word or phrase that has been used again too soon, and propose the wording
that removes the repetition without removing anything else.

## What counts

- The same uncommon word twice in one unit, or in units close enough together that
  a reader hears it as an echo — roughly, within three units.
- The same connective opening consecutive units: "And so", "But", "Moreover",
  "Thus", "Yet". Two in a row is a pattern; three is a tic.
- The same construction repeated: two units both built as "It is not X. It is Y."
- A technical term glossed a second time in the same essay. The style law is
  explicit — a term is introduced once per essay, at first use, and never
  reintroduced, least of all in adjacent paragraphs. The second gloss goes; the
  reference stays.

## What does not count, ever

- **A refrain.** This author repeats deliberately, and the repetition is often the
  argument. A phrase that returns at a structural moment — an opening echoed at a
  close, a line that has been building through the essay — is a refrain and you
  leave it exactly as it is. When you cannot tell whether a repetition is a tic or
  a refrain, it is a refrain: `keep` it and say in `why` that you thought about it.
- **A term of art.** A list of the author's terms of art is provided in
  PROTECTED TERMS, and everything on that list is left alone: never substitute a
  synonym for one, never decapitalize one, never swap one for a paraphrase. The
  list is the whole vocabulary — every name and every alternate name of every
  concept, figure, construct and proper name this book uses, drawn from the
  author's own graph and from the pronunciation dictionary — so it is the
  authority, not a sample of one. These terms repeat as often as the argument
  needs. When one genuinely does repeat too closely, the fix is to CUT the
  second mention or recast the sentence around it, never to replace it; if
  neither is possible, `keep` the unit and say so in `why`.
- **A motif word.** The established motif families are the ones the `[figure]`
  elements of STYLE LAW name; their words recur across the whole book on
  purpose. Within one paragraph, four uses of one of them may be three too many;
  across an essay, they are the book working. Judge the paragraph, never the
  essay.
- **Two words that only look alike.** Some pairs are distinct terms in this book
  with a law that says never to interchange them — "test" and "measure" are one
  such pair, "virtue" and "quality" another. STYLE LAW's `[lexicon]` elements are
  the authority on which pairs those are and what each one means; the two named
  here are examples of the kind, not the whole set. Repetition is a matter of
  the same word twice, not of near neighbours.
- **Function words.** The, of, and, to, that, is. Never flag them.

## How to fix

Cut, don't substitute. The best fix for a repeated connective is usually to delete
it and let the sentence start where it wants to; the author's syntax law already
prefers sentences opening with And, But, For, Wherefore, and a paratactic rhythm
tolerates a bare opening better than a fresh adverb. Where a word must go and
nothing can replace it, recast the clause rather than reaching for a thesaurus:
the lexicon law prefers earthy Anglo-Saxon nouns, and a Latinate substitute
introduced to avoid a repetition is a worse fault than the repetition.

Change nothing else in the unit. Not the punctuation, not the emphasis, not the
sentence order.

## The state

Return a ledger, one line per flagged word or phrase:

    moreover — flagged n=6, cut; n=11 left (refrain)
    the Chid — glossed n=3; second gloss cut n=18
    and so — opened n=29, n=30, n=31; cut at n=31

Carry it forward through every unit. Before you flag a word, read the ledger: if it
is already there, you are looking at the second half of a pattern you have already
begun to fix, and what you do here must be consistent with what you did there. If a
word is in the ledger as a refrain, it is a refrain everywhere in this essay.
```

### `metaphor-consistency` — global, with a prelude registry

```markdown
---
class = "global"
state = "not used — the registry is this filter's coordination object"
---

# Metaphor consistency

The book's figures are a system, not decoration. This filter finds the places where
a figure is used against itself: where the same motif carries two incompatible
meanings, where a metaphor is mixed, and where a figure is turned into a claim it
cannot support.

## The registry — the prelude

Before any unit is judged, read THE ESSAY whole and return the registry: an entry
for every motif family the essay uses, and for each one, in the author's own terms,
what it is doing HERE.

    fire — what the Qualities do to a life; always active, always the agent, never
      something a person holds or controls. ¶4, ¶9, ¶27, ¶31.
    wall — what the Dead cannot pass; the limit of a life lived without the Test.
      ¶2, ¶11, ¶11, ¶19.
    ledger — the count itself, neutral, neither friend nor accuser. ¶14, ¶22.
    journey/path — a life's shape over time. ¶6, ¶33.

The registry is a reading of what the essay actually does, not of what it should
do. If a motif is used two ways, the registry says so — that is the finding, and
the units that create the inconsistency will be judged against it.

Draw the family names from the `[figure]` elements of STYLE LAW, which name the
established families this author works in. The registry example above is a
worked illustration of the FORM, not the list: the families are whatever the law
names, and they change when the author changes the law. A figure that belongs to
no family in the law is recorded in the registry under its own name and flagged
in the prelude as a stranger — introducing a new motif family is an authorial
decision, not a filter's.

## What counts, per unit

- **A motif used against its own sense in this essay.** The registry says fire is
  the agent and never a possession; a unit in which someone "carries his fire" is
  the finding. Propose the wording that restores the registry's sense.
- **A mixed figure.** Two families collided inside one sentence: a wall that
  catches light, a ledger that burns, a path that strikes. The families are
  distinct on purpose; crossing them dissolves both.
- **A figure asked to do an argument's work.** A metaphor is not an identity claim.
  Where a unit reasons FROM the figure — "since the wall is stone, it cannot be
  moved, and therefore…" — the argument is resting on the picture rather than on
  the claim, and the fix is to make the claim and let the figure illustrate it.
- **A parable retold in a way that changes it.** The style law permits a recurring
  parable to be retold from a new perspective and forbids reinventing it. A
  retelling that changes what happens is the finding.

## What does not count

- **A motif recurring often.** Recurrence is the system working. Only conflict is
  a finding.
- **A new figure that is consistent with itself and with the families.** Novelty
  is the author's business, not this filter's.
- **The abstractness of a concept.** "Ground every metaphysical concept in a
  concrete physical analogy" is a real style element and it is not this filter: a
  unit that is too abstract is not a unit whose metaphor is inconsistent. Leave it.
- **Anything in another essay.** This filter sees one essay. A motif used one way
  here and the opposite way in a different essay is a real finding and it is
  invisible from where you are standing; do not guess at it and do not mention it.
  That question belongs to a lens over the whole book.
- **A term of art.** The names in PROTECTED TERMS are the author's vocabulary,
  and a `replace` that swaps one of them for a synonym is out of bounds even
  when the swap would fix a mixed figure. A motif family's own name is one of
  these: restore the registry's SENSE by rewriting what is said about the
  figure, never by renaming the figure.

## Naming your finding

Every `replace` names the registry entry it is about, in `ref`. Two units that
touch the same entry will be read together by the author, and they must not
propose fixes that contradict each other: each unit is judged independently, so
the registry is the only thing keeping them coherent. Where the registry itself
records that a motif is used two ways, choose the sense that the majority of the
essay's uses carry, say so in `why`, and be consistent about it in every unit.
```

### `audio-friendly` — sequential, voice-continuity state, pronunciation prelude

```markdown
---
class = "sequential"
prelude = "pronunciations"
state = "the voice note: how the reading has been going — the current sentence rhythm, the last few sentence openings, any construction that has just been used and should not recur in the ear"
---

# Audio-friendly

This essay will be read aloud. Find the places where the prose works on the page
and fails in the ear, and propose the wording that survives the reading.

A listener cannot look back. They cannot see a parenthesis, an em dash, a
capitalization, or a paragraph break. They hold about one sentence in mind at a
time. Everything below follows from that.

## What counts

- **A sentence whose subject and verb are too far apart to hold.** By the time the
  verb arrives the listener has lost what it is about. Break it, or move the verb
  forward. The style law already asks for short sentences and for long periodic
  constructions to be broken rather than chained; this filter is that law with a
  listener in front of it.
- **A nested subordinate clause.** One level is fine in the ear. Two is not. The
  syntax law names this as a fault on the page too: no nested Latinate subclauses.
- **A parenthesis or an em-dash aside carrying real content.** Punctuation the ear
  cannot hear must not be load-bearing. Either promote the aside to its own
  sentence or fold it into the main clause.
- **A homophone that will be misheard in this context.** The one that matters
  most in this book: a term from PROTECTED TERMS whose ordinary homonym is also
  live in the sentence — the capitalization does all the work on the page and
  none of it aloud. Recast so the sense is carried by the words, never by
  replacing the term.
- **A pronoun whose antecedent is more than a sentence back**, or which could
  attach to either of two nouns. Name the thing.
- **A list of more than three items with no structure.** In the ear a flat list
  becomes noise after three. Group it, or give it a rhythm.
- **A number or a citation the ear cannot parse.** Section numbers, dates in
  running text, a parenthetical reference mid-sentence.
- **Two consecutive sentences with the same opening shape.** In the eye this is
  parallelism; in the ear, at close range, it is a stutter — unless it IS the
  parallelism the style law asks for, in which case leave it.

## What does not count

- **A term of art, however hard to say.** Never simplify the lexicon for the ear.
  A list of the author's terms of art is provided in PROTECTED TERMS, and
  everything on it stays exactly as it is, capitalization and all. A listener
  who has to work at a word is being served; a listener who is given a different
  word has been lied to.
- **The register.** The prose is meant to sound like scripture read aloud; that is
  the point, and paratactic weight, inversion, antithesis and cumulative rhythm are
  ratified law. Do not flatten a period into three flat sentences because the ear
  "prefers" short ones. Break a sentence only when a listener would actually lose
  the thread of it — never merely because it is long.
- **Repetition.** Repetition is *better* in the ear than on the page, and the
  refrains are doing work. That is a different filter's business, and this one
  never removes a repetition.
- **A heading.** Headings are read as headings. Leave them.

## The pronunciation dictionary

PRONUNCIATION DICTIONARY is the author's settled reading of the terms a narrator
would stumble over. It is law here in the same way the style law is law.

A term in the dictionary is SETTLED: never flag it as hard to say, never
recast a sentence to avoid it, and never propose a gloss for it. The dictionary
IS the fix. If a term is hard to say and it is in the dictionary, the work is
already done.

A term that is hard to say and is NOT in the dictionary is not this pass's
business either — it belongs to the prelude, which proposes a row for the author
to rule on. Judging the unit and stocking the dictionary are two different jobs,
and doing the second one here would put a pronunciation into the prose.

## The rule that outranks the rest

Never shorten a sentence at the cost of its meaning. The style law says it in as
many words: when brevity and precision conflict, precision wins. A listener who
hears a simpler sentence that says less has been served worse than one who has to
attend to a harder sentence that says the thing.

## The state

Carry a short voice note forward, three or four lines, rewritten each unit:

    rhythm — long cumulative periods through ¶1-4; ¶5 broke short, deliberate
    openings — "And so" (¶3), "But" (¶4), "For" (¶6); do not open ¶7 with a
      conjunction
    just used — the "not X but Y" antithesis at ¶6; hold it back for a few units
    watch — "Field" and "field" both live from ¶8

It is a note about how the reading has been going, so that the next unit continues
a voice rather than starting one. Read it before you judge each unit, and rewrite
it after.
```
