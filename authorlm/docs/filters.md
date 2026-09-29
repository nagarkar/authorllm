# Ratified filters — SMSTTD (post-design additions)

Repo record of filter artifacts ratified by the author after the original three of docs/filter-pass-design.md's appendix (duplicate-words, metaphor-consistency, audio-friendly). Live artifacts run from `manuscripts/SMSTTD/_filters/` (gitignored manuscript data); this file is the checked-in canonical copy. On any re-ratification update both.

## `claim-status` (ratified 2026-08-31)

```markdown
---
class = "sequential"
state = "a ledger of the constructions already ruled on — each assertoric phrase flagged, the verdict it received, and the replacement idiom the author preferred"
---

# Claim status

Flag a unit where the language of demonstration outruns the claim's actual
status: "proves", "demonstrates", "requires", "must", "it follows",
"confirms", "is derived" — attached to a claim that is an axiom, a
definition, an analogy, or a conjecture rather than a demonstrated result.
The same concern covers borrowed scientific authority: a physics term
(symmetry, conservation, entropy, momentum) presented as if the argument
had done physics.

Propose the smallest edit that makes the verb match the status —
"proposes", "frames", "makes possible", "suggests", "models" — preserving
the sentence's rhythm and everything else in it.

Refuse to flag:
- a genuine deduction from the book's own ratified definitions — where the
  premises are stated and the step is valid, "it follows" is earned;
- the Sermons' register — prophetic assertion is its ratified voice;
- emphasis with no inference claimed ("this much must be said plainly").

Consistency is the point: rule the way the ledger shows the author has
already ruled on the same construction.
```

## `image-then-paraphrase` (ratified 2026-08-31)

```markdown
---
class = "sequential"
state = "the live figure — the image the reading just passed, from this unit or the last, and whether its work has since been re-done in abstract prose"
---

# Image, then paraphrase

The book often says the same thing once in metaphor and twice in abstract
prose. Flag a sentence or span that restates what an immediately preceding
image has already established — the figure lands, and then a plainer
sentence repeats its content with the imagery stripped out.

Propose cutting the paraphrase, keeping the image; join the seams so the
prose reads clean without it.

Refuse to flag:
- a follow-on that ADDS — a new distinction, a qualification, a consequence
  the image cannot carry; that is development, not paraphrase;
- the first introduction of a term of art — when a figure ushers a concept
  into the book's vocabulary, the precise formulation beside it is the
  definition doing its job;
- recall at a distance — an image restated essays later is orientation or
  repetition, another pass's business; this filter reads only the seam
  between an image and its immediate shadow.
```


## `modal-register` (ratified 2026-09-01)

The companion to the `historical-reach` lens (docs/lenses.md): the lens finds the
structural gap over a whole reading, this filter repairs it a unit at a time.
Distinct from `claim-status`, which finds a verb that claims too much; this one
finds a claim asserted with no verb of claiming at all. They never flag the same
sentence, and they need different state — a ledger of constructions there, the
frame-in-force here.

```markdown
---
class = "sequential"
state = "the frame in force — whether a declared model or a governing antecedent still licenses the generic present at this point in the reading — together with a ledger of the constructions already ruled on and the repair the author preferred for each"
---

# Modal register

A model and a law are different claims, and the sentence has to say which one
is being made. Flag a unit where a claim about how things actually go — over
generations, across societies, through history — is asserted in the bare
indicative, when what the essay supports is a tendency it proposes.

This is not a hedging pass. The fault is a mismatch between a claim's status
and the mood it is written in, and it runs in both directions: a conjecture
reported as fact, and a fact hedged into a conjecture.

## What counts

- **A temporal inevitable.** "Eventually", "sooner or later", "in time",
  "always ends this way" — a claim whose only stated condition is the passage
  of time. Waiting is not a mechanism, and a reader who wants to argue will
  start here.
- **A universal quantifier over a historical subject.** "Every civilization",
  "each generation", "all traditions", "every ideology is". One counterexample
  sinks these, and the argument almost never needs the universal to work.
- **Exhausted possibility.** "Is inescapable", "cannot be avoided", "is not
  inexhaustible", a bare "must". These say no other course is open — the
  strongest claim available, and it is usually made in a subordinate clause
  where nothing defends it.
- **A process narrated as a report.** A run of bare present-tense sentences
  carrying a sequence forward as though it had been observed, where the
  sequence is the essay's own conjecture. This is the commonest form and the
  hardest to see: no single sentence is false, and the run as a whole asserts
  a chronicle.
- **A diagnosis of the present age offered as settled.** A claim about what
  people now do, believe, or cannot help doing, stated as established fact
  with nothing behind it.
- **The reverse fault: a fact hedged into a conjecture.** Something that
  actually happened, written with "could" or "may", reads as though the essay
  is unsure whether it happened. An event takes the past indicative. Restoring
  it is as much this filter's work as softening an overclaim.

## What does not count, ever

- **A generic present under a frame that is still in force.** This is the
  whole reason the filter carries state. Where the essay has said what kind of
  thing it is describing, or where a governing antecedent — "when the forms
  are neglected…", "left without…", "in a society of that kind…" — still holds
  over the stretch you are reading, the generic present is the correct tense
  and there is no finding. Never hedge a sentence whose licence is two
  sentences above it.
- **A claim about a single life.** The scope decides. What one person does,
  becomes, or fails to become is not a claim about history, and it takes the
  indicative freely.
- **An analytic consequence of the book's own definitions.** Where a claim
  follows from terms this book has defined, it is definitional and the
  indicative is right. PROTECTED TERMS names the vocabulary; a claim built out
  of those terms is the book reasoning, not the book predicting.
- **Free indirect speech, and questions in another voice.** A run of questions
  put in the mouth of the person being described asserts nothing. It is
  frequently the best modal instrument on the page. Leave it, and where the
  surrounding indicatives are the finding, say in `why` that the voiced
  passage is doing the same work correctly.
- **A typifying generic carrying a figure.** The kind that names a type rather
  than a population — a child, a citizen, a climber. Low risk, and cutting the
  form costs the prose more than it saves.
- **The Sermons' register.** Prophetic assertion is ratified voice. This
  filter serves the essays and the Appendix.
- **Cited report at proper strength.** A historical claim that says what it
  found — "supplied ample examples", "was named before" — and cites it, is the
  target register, not a fault. Note it in the ledger as an exemplar; the
  repairs should be pitched to match it.
- **The language of demonstration.** "Proves", "demonstrates", "it follows",
  "is derived", "confirms" — a verb claiming the argument did more work than
  it did belongs to the claim-status filter, not to this one. The two never
  flag the same sentence: that filter finds a verb that claims too much, this
  one finds a claim asserted with no verb of claiming at all.

## How to fix

Three moves, in this order of preference. Take the first one that works.

1. **Scope it.** Put the governing antecedent at the head of the run. One
   clause can license four sentences that would each need a hedge on their
   own, and it costs the rhythm nothing.
2. **Hedge the verb.** "Can", "may", "tends to" — on the sentence that carries
   the claim, not on its neighbours.
3. **Convert to argument-form.** "There is no reason to think X" in place of
   "X is not so". The style law already requires an argument wherever the
   premises do not force the conclusion, and this is what that looks like at
   sentence scale.

**Do not hedge every verb in a run.** A page of "may" flattens prose the tone
law requires to be challenging and unflinching, and it reads as a loss of
nerve rather than a gain in precision. One scoping clause plus the two or
three sentences no scope can reach is the whole repair. Where a unit is
already covered by an earlier scope, `keep` it and say so in `why`.

Where a claim is the essay's own and central, prefer owning it to softening
it: a stated conjecture is a harder target than an unattributed law, and it
loses nothing in force.

Change nothing else in the unit. Not the rhythm, not the emphasis, not the
sentence order.

## The state

Two things, carried forward together.

FRAME — what licenses the generic present right now, and how far it reaches:

    frame: declared n=12 ("what follows is the shape of a tendency") — in force
    antecedent: n=31 "when the forms are neglected" — governs n=31 through n=34
    frame: none in force since n=35

LEDGER — one line per construction ruled on, with what was done:

    "eventually" — n=37, scoped to the process rather than the clock
    "each generation" — n=35, hedged to "a generation can"
    "could abolish" — n=41, over-hedged; restored to the past indicative
    "supplied ample examples" — n=39, exemplar; left alone

Both examples are worked illustrations of the FORM, not lists.

Before you flag a unit, read the FRAME: a unit standing inside a live frame is
not a finding, and hedging it duplicates a licence the essay already granted.
Then read the LEDGER: rule the way this run has already ruled on the same
construction, and use the repair idiom that was preferred there. Consistency
across the essay is the point — a reader who meets two different fixes for one
construction learns that neither was a decision.
```

## `spiral-support` (ratified 2026-09-01)

God-essay specific, and deliberately so: the additive work — installing the
frame, conceding the reach, anchoring a contestable historical claim — needs the
essay's own claim ledger, which no generic prompt can carry. Adds paragraphs via
the legal split (a `new` containing a blank line, §2), never `insert`.

```markdown
---
class = "global"
state = "not used — the registry is this filter's coordination object"
---

# Support for the spiral

Written for the essay on God, and for the spiral it proposes: the claim that
worship ascends, that the ascent makes its own forms unnecessary, that the
forms are then neglected, and that need eventually returns.

This filter does not rewrite claims. Matching a verb to a claim's status is
the modal-register filter's work, and a unit whose only fault is its mood
belongs there. This filter supplies what the claims need in order to stand:
the frame that says what kind of thing the spiral is, the concession the
argument owes where its support does not reach, and the anchor where a
historical claim needs a source.

## The registry — the prelude

Before any unit is judged, read THE ESSAY whole and return one entry for every
claim it makes about how people, traditions, or societies actually behave over
time. For each entry: the claim in the essay's own words, its SCOPE, what the
essay SUPPLIES in support, and whether that support REACHES the scope.

Scope is one of: a life, a tradition, a civilization, an age.
Support is one of: cited texts, named traditions, a historical example, an
argument from the book's defined terms, an analogy, or nothing.

    the ascent through worship — scope: a tradition — support: four traditions,
      cited — reaches. ¶18, ¶24, ¶25, ¶26.
    the neglect of the forms at altitude — scope: a civilization — support:
      argument from the book's terms only — does not reach. ¶28, ¶29.
    the return through need — scope: a civilization — support: none; the only
      support anywhere in the essay is at the scope of a single life — does not
      reach. ¶38.
    the replacement idols — scope: an age — support: cited, with twentieth-century
      examples — reaches. ¶36.

That is a worked illustration of the FORM, not the list. The entries are
whatever this reading of the essay actually finds, and the count changes as the
essay changes.

Two rules for the registry itself. **Scope is read from the sentence, not from
the section**: a paragraph under a heading about civilizations may be making a
claim about one life, and the entry records what the sentence says. And a claim
whose support reaches it is recorded all the same — the essay's well-supported
claims are what the concessions get measured against, and the units that carry
them are the ones this filter must leave alone.

## What counts, per unit

- **The frame is missing where the model is introduced.** The unit that first
  states the pattern as a pattern owes the reader one sentence saying what kind
  of thing is on offer — a model, a tendency, a shape the essay proposes — so
  that everything under it can use the generic present without claiming to
  report. Propose the same unit with that sentence added. Use the essay's own
  words for the spiral wherever it has already supplied them, and add nothing
  else. One frame in the essay, at the introduction; a frame restated later is
  the modal-register filter's problem, not a second finding here.

- **A claim whose registry entry says the support does not reach it.** Two
  legal moves, and never both in one unit: NARROW the claim to the scope its
  support actually reaches, or CONCEDE the reach in the essay's own voice.
  Prefer narrowing where the argument survives it; prefer conceding where
  narrowing would cost the essay a claim it plainly means to make.

- **A contestable historical or empirical claim with no anchor.** The citation
  law requires a source for every contestable historical claim, and a claim
  about what a century did, or what people now believe, is exactly that.
  Propose the unit with a footnote anchor in the ratified `[^F*]` form at the
  sentence that needs it, and say in `why` precisely what the note must supply:
  a work and a location.
  **You may not write the note.** Naming a source, a date, an edition, or a
  figure that the essay does not already contain is making something up, and
  that is refused here whatever it would cost the argument. The anchor plus the
  stated requirement is the whole proposal; the author supplies the source. If
  you cannot place an anchor without inventing what hangs off it, `keep` the
  unit and put the question to the author in `why`.

- **The objection the essay owes.** The structure law asks every major essay to
  state its strongest counterargument at full force, with concession where
  real. Where the registry shows a claim whose support does not reach its
  scope, and the essay nowhere concedes it, propose the concession as a NEW
  PARAGRAPH: replace the unit that closes the argument with that unit
  unchanged, a blank line, and the concession. Build the concession out of the
  registry and nothing else — what the essay shows, what it does not show, and
  what follows anyway. The objection protocol exists to cover more ground, not
  to offer terms of surrender: the concession states the limit and then says
  what the essay still claims inside it.
  **At most one added paragraph in the whole run.** A concession stated twice
  is a retreat, and two units proposing one each is the commonest way this
  filter can go wrong.

## What does not count

- **A sentence whose only fault is its verb or its mood.** An unhedged claim
  that the registry says is well supported does not need this filter, and one
  that is badly supported needs support, not a hedge. Modal-register handles
  the mood.
- **A claim the registry records as reaching its scope.** Leave it entirely.
  These units are the essay working.
- **A claim about a single life.** The essay's individual-scale claims are its
  best-supported ones. Do not attach concessions to them.
- **Anything that would need a fact the essay does not contain.** Not a
  citation, not an example, not a date, not a tradition the essay has not
  already named. Where the fix requires one, the unit is `keep` and the
  requirement becomes a question to the author in `why`.
- **Headings.** Leave them exactly as they are.
- **Any other essay.** This filter sees one essay. A claim supported in a
  different essay is invisible from here; do not assume it and do not mention
  it.

## Naming your finding

Every `replace` names its registry entry in `ref`, so the author reads all the
proposals about one claim together. Where two units touch the same entry, only
one of them may carry the concession — the other narrows, anchors, or keeps.
State in `why` which of the four moves you made (frame, narrow, concede,
anchor) and what the registry entry says the support is. A proposal whose `why`
cannot name the registry entry it rests on is a proposal this filter should not
be making.
```

## `history-not-quality` (ratified 2026-09-01)

Written for `hierarchy.md`, and the first filter to declare
`summaries = true` (design §9.1a) — its findings turn on what the Metaphysic
already defines, so it pays for the cross-essay view.

Bounded against its siblings in its own prose: `claim-status` owns a verb
that claims too much, `modal-register` owns a claim asserted in the wrong
mood, and this one owns a rank claimed where no present measurement stands
behind it. None of the three flags the same sentence.

The refusals are where the work is. It cannot move, cut or reorder, so a
paragraph in the wrong place is a `keep` with the reason in `why`. It never
swaps an example for a cleaner one — this essay defers questions in as many
words, and from inside a sequential reading a setup and a fault are
indistinguishable.

```markdown
---
class = "sequential"
summaries = true
state = "the sense ledger — for each set-noun the essay has used, which set it was ruled to mean at each unit and the repair the author preferred; plus any term introduced into the prose, so it is never introduced twice"
---

# History is not present quality

One fault: a rank claimed where no present measurement stands behind it.
Nearly always a fact about history has been put in the measurement's place —
a line of descent, a credential, an initiation, a victory won once. Flag the
unit that does this, and flag the vocabulary that lets it happen without
anyone noticing.

Two principles carry the whole test.

> A past measurement stays permanently true as history. It does not thereby
> stay true as present rank.

> A deme bundles trajectories by shared history. A hierarchy orders loci by
> manifested quality. The error is to confuse the two.

## What counts

- **A set of trajectories called a trajectory.** A set of them is not one of
  them, however tightly they run together. CONCEPT NOTES carries what the
  book has defined a set of loci to be; where a sentence makes the set into a
  single continuing thing, propose the sentence that keeps them plural.

- **A set-noun used bare.** The essay's word for a set of people carries
  several senses — the set a given ranking actually reaches, a line of
  descent, a society, a species, a community bound by shared ideas. Where a
  sentence would read differently under two of them, it has to say which one
  it means. One qualifying word, usually, and that is the whole repair.

- **A history ranked in place of a present quality.** A lineage, a
  trajectory, a bloodline, an accumulated descent — none of these is what a
  metric reaches. What a metric reaches is a quality manifested at a present
  locus. Where the prose ranks the history itself, propose the sentence that
  ranks what is actually measured.

- **Shared development offered as the ground of a comparison.** Where the
  essay grounds a comparison in trajectories having grown together, run in
  parallel, or reached a common stage, it has named the wrong ground. The
  ground is that one quality and one metric reach both loci. A novice and a
  master belong to one ranking precisely because the measure reaches both,
  and their difference in stage is what the ranking is FOR.

- **A substitution the essay commits without marking it.** This is the
  essay's central charge, and it lands hardest where the substitution is
  named as a substitution rather than merely performed on the page. Where a
  unit commits the error the essay exists to expose, propose the sentence
  that exposes it.

- **A quality denied, where what should be denied is the measurement.** To
  say that some named thing is not a quality at all is a larger claim than
  the argument needs and a harder one to hold. The defensible claim is always
  the narrower one: that it is assigned from history rather than measured as
  presently manifested. Propose the narrower sentence.

- **The ranking act attributed to Nature.** The one form of this fault that
  is not about history. Nature supplies qualities that vary; a category and a
  metric are chosen, and the ordering follows from the choice. Where the
  prose has Nature doing the ordering, propose the sentence that returns the
  choosing to whoever chose.

## What does not count, ever

- **The error quoted, staged, or attributed.** The essay's whole subject is
  people who answer a present question with an answer about ancestry. A unit
  reporting that answer, or putting it in the mouth of the one who gives it,
  is the argument working. Only an unmarked substitution in the essay's own
  voice is a finding.

- **A history claimed as history.** A degree, a victory, an initiation, an
  ancestor's demonstrated learning — these are permanently true as records of
  what happened, and saying so is correct. The fault begins only where the
  record is offered as a present measurement.

- **A term of art.** PROTECTED TERMS is the authority, and it is the whole
  vocabulary rather than a sample. Never substitute a synonym for one, never
  re-word one, never change its capitalization. Where a term of art is used
  in a sense CONCEPT NOTES does not support, the repair is the surrounding
  sentence, never the term.

- **A question the essay has deliberately set aside.** This essay defers
  questions in as many words and answers them later. A unit that raises
  something it does not settle is structure, not fault. A sequential reading
  has only the prefix and cannot see what is coming, so where a unit reads as
  unfinished, assume the essay finishes it, and `keep`.

- **An example.** An example is never swapped for a cleaner one. An example
  that seems to invite an objection is very often the setup for an answer the
  essay gives later, and from inside a sequential reading the two are
  indistinguishable. Where an example genuinely carries the fault, repair the
  sentence around it and leave the example standing.

- **Headings, and the footnote definitions at the end.** Leave them exactly
  as they are. A footnote anchor in the `[^…]` form is never dropped from a
  replacement.

- **Anything about the essay's ORDER.** Which section a paragraph belongs in,
  which argument should come first, whether a stretch belongs in another essay
  at all — these are real questions and this filter cannot act on them. It
  replaces a unit in place or leaves it. Where the fault is that the paragraph
  is in the wrong place, `keep` it and say so in `why`.

- **A claim that is merely too broad for its support.** An account of why an
  institution persists, a diagnosis of an age, a universal over a historical
  subject: these overreach, but they do not confuse history with present
  measurement. `modal-register` owns the mood and `claim-status` owns the
  verb. This filter never hedges a verb and never softens a claim. It changes
  WHICH THING is claimed about, never how strongly.

## How to fix

Smallest repair first. Take the first one that works.

1. **Name the sense.** One qualifying word in front of the set-noun and the
   sentence is unambiguous. This is the commonest fix and usually the whole
   fix.
2. **Move the sentence's subject from the history to the locus.** What is
   ranked is the quality manifested now; recast the clause around that and
   keep everything else.
3. **Narrow the claim.** Where the unit denies too much, propose the sentence
   that denies exactly the measurement and no more.
4. **Name the substitution.** Where the unit commits the error the essay
   exists to expose, the repair is the sentence saying that a fact about
   ancestry has been offered in answer to a question about present quality.

A term the book has now defined may be introduced into the prose at the point
where the essay is plainly reaching for it, and there only — ONCE, never
re-glossed, never in a unit already carrying a new term. Introducing more
vocabulary than the argument needs is the fault this filter's own subject
matter makes easiest to commit.

Change nothing else in the unit. Not the rhythm, not the emphasis, not the
sentence order.

Name the ledger line in `ref` — the set-noun or the term the finding is
about — so the author reads every proposal about one word together.

## The state

One line per set-noun and per introduced term, carried forward:

    population — n=3 read as a line of descent, qualified; n=14 the set a
      ranking reaches, qualified; n=41 left bare (author preferred it)
    accord — n=3 kept; n=44 grounded in shared development, recast
    commensurability — introduced n=14; never re-glossed

That is a worked illustration of the FORM, not a list of what to look for.

Before you flag a set-noun, read the ledger. If it is already there, this is
the second half of a pattern you have begun to fix, and what you do here must
match what you did there: a reader who meets two different repairs for one
word learns that neither was a decision. If a term is in the ledger as
introduced, it is introduced, and this reading never introduces it again.
```
