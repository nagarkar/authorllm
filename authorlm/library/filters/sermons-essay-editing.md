---
class = "global"
state = "not used: the registry (the essay's position, terms, definitions, key lines, stories, metaphors, standard, sections and references) is this filter's coordination object"
summaries = true
---

# Seven More Sermons: essay editing

Hold each unit of a modern-English essay or commentary of Seven More Sermons to
the Dead (Chitta Darshana) to the book's house style: its voice, its sentences,
how its stories carry its argument, and how it handles terms, references and
footnotes. This filter makes judgments, so it proposes and the author decides.
Most units of an edited essay need nothing: `keep` them.

## Scope

This filter applies to the modern-English essays and commentary (Part II). It
does not apply to the Early Modern English sermons: those belong to the
kjv-bunyan-style skill. If THE ESSAY is a sermon, `keep` every unit. A sermon
passage quoted inside an essay is copied byte for byte.

## Rank

1. **STYLE LAW** outranks every rule here, and PROTECTED TERMS and CONCEPT
   NOTES are the book's own vocabulary and settled definitions. Where a fix
   would break a law or cost a protected term, `keep` the unit and name the law
   or the term in `ref`.
2. **The registry** comes next: the reading of what this essay actually does.
3. **The rules below** come last. Only the numbered rules here are enforced.

Three subjects are generic and belong to STYLE LAW, not to this filter: em
dashes, wordiness, and aphorisms (where one may stand in its explanation, and
what makes one bare). The rules below that touch them (V1, S1, S5, S6) defer to
the law and state only what to do in a unit. Where STYLE LAW is silent on one
of these subjects, leave dashes and aphorism placement alone, and on wordiness
or a bare aphorism ask rather than guess.
*By the owner's ruling of 2026-11-25: "Lets have things in style laws that are
generic - like em dash, wordiness (line 9) and 'explanation from simple terms'
(line 10). Not in individual filters."*

## The registry: the prelude

Read THE ESSAY whole, with the summaries of the essays before and after it, and
return these lines. Record what the essay does, never what it should do.

    position: where this essay stands in the reading order; which parts come
      before it and which after. The essays come before the Metaphysic.
    terms available: the book's terms defined in essays before this one
      (from the summaries and CONCEPT NOTES)
    terms introduced here: each term this essay introduces, the unit of its
      first use, and the unit where it is defined in plain English (or "not
      defined")
    terms used early: each term this essay uses that is defined only later
      in the book (the Metaphysic included), and the units
    definitions: each term the essay defines, the unit, and every other unit
      that defines it again or differently
    technical words in ordinary use: each word with a book meaning (time as
      the counting of choices) that the essay also uses in its ordinary sense,
      and the units of the ordinary uses
    key lines: for each section, its central sentence if it has one, the unit
      it stands in, where in its explanation that is (start, middle, end, or
      standing with no explanation), and the units that restate it in weaker
      words
    stories and images: each story, example or image and its units; how it is
      introduced (by a question it answers | by a claim it illustrates |
      neither); the unit that states its lesson, or "none"; whether its outcome
      is told, and the unit
    metaphors: each sustained figure, its vehicle (roads, a pathway), its
      units, and any unit where the vehicle changes
    standard: the standard the essay states for judging its cases, the unit
      that states it, each example the essay judges, and whether the essay
      judges it by that standard
    sections: each section, its heading if any (and whether the heading names
      a story or a concept), its units, and whether it runs long with no
      subheading
    references: external scriptures and figures as the essay names them, and
      the units; attributions left vague ("an ancient text", "a sage"); forward
      and backward references to other parts of the book and whether each is
      inline or in a footnote; footnote markers and their forms
    out of scope: sermon text, verse, quoted text by another writer, footnote
      definitions, code, math, tags in square brackets, text inside an [Omit]
      block

## Ask, do not guess

Some faults have an answer only the author holds: a gap in the argument, a story
that must move or go, a key line standing where STYLE LAW does not allow, a term
with two definitions. Rules marked (ask) are never rewritten. Return `replace`
with the unit unchanged except for one tag planted straight after the sentence:
`[Suggestion: Query: <the question in one line, naming the fault and a concrete fix>]`.
A query costs the author a decision just as a rewrite does: plant one only where
a careful reader would stall or object.

One query per unit. When a unit holds both a fault this filter fixes and a
question, the question comes first: plant the query, change nothing else, and
the fix waits for the next run.

A filter replaces a unit in place. It never adds a unit, deletes one or moves
text between units. Every rule below whose fix would do one of those is (ask).

## What counts, per unit

### Voice

- **V1 Authoritative, declarative, non-defensive.** The voice is authoritative,
  declarative and non-defensive. Sections close on declarative lines. On em dashes
  this filter states no rule of its own: STYLE LAW on em dashes governs. Fix a
  defensive or apologetic sentence, and a section whose last line trails off in
  a qualification or a question it does not mean to leave open.
- **V2 No hedges and no meta-commentary in the body.** Cut a hedge (`perhaps`,
  `it may be that`, `in some sense`, `one might say`) and cut commentary on the
  essay itself (`as we shall see`, `it is worth noting`, `this essay argues`,
  `let us now turn`). A transition that says why the next move happens is not
  meta-commentary: A3 requires it.
- **V3 Speak plainly.** Prefer the concrete image to the abstract formulation.
  If an idea needs "possibilities creating possibilities" language to explain,
  find a concrete case instead. Where the essay already supplies the concrete
  case (the registry's stories and images), use it. Where it does not, ask:
  never invent a case.

### Sentences

- **S1 Avoid wordy sentences.** What counts as wordy, and what a cut must
  keep, is STYLE LAW on wordiness; this filter states no rule of its own. Make
  the cut the law calls for inside the unit and name the law in `ref`
  ("another puts herself first," not "another puts her own needs first").
- **S2 Every verb that needs an object gets one** ("to seek new experiences,"
  not "to seek more").
- **S3 Vary repeated nouns.** When the same subject recurs ("the child" five
  times), mix in natural alternatives (girl, daughter, toddler, one who...)
  without blurring who is meant. Never vary a PROTECTED TERM, a term the essay
  defines, or a word held constant by a metaphor (A5): those stay the same so
  their sense does not shift.
- **S4 Never state the same idea twice in a row in different words.** That
  includes definitions: state a definition once, and introduce related terms
  where they are needed, not back to back. Within a unit, cut the weaker
  statement. Where the registry's definitions line shows a second definition in
  another unit, and this unit holds the second one, cut the restatement if the
  sentence around it survives; otherwise ask.
- **S5 Elevate key lines.** A central sentence (e.g. "Every question is an
  experiment, and every answer an experience.") is the section's key line.
  Where a key line or aphorism may stand is STYLE LAW on aphorisms; this filter
  states no rule of its own. Never query or move a key line for standing at the
  start or at the end of its explanation. In the unit that holds the key line,
  a blank line may set the line apart. Delete the weaker restatements
  elsewhere: in a unit the registry lists as a weaker restatement, cut the
  restating sentence. Where the registry's key-lines record shows a key line
  standing where STYLE LAW on aphorisms does not allow (ask), plant the query
  on the key line.
- **S6 Earn bare aphorisms.** What makes an aphorism bare (a claim like "The
  horizon is eternal") is STYLE LAW on aphorisms; this filter states no rule of
  its own. Where the law finds one bare and the essay's own argument supplies
  the missing sentence's content, write it; where it does not, ask.
- **S7 Use the book's own vocabulary over generic synonyms** ("whether a better
  choice exists," not "a better alternative"). The book's vocabulary is
  PROTECTED TERMS, CONCEPT NOTES and the `[lexicon]` elements of STYLE LAW.

### Structure and argument

- **A1 Be consistent about how stories relate to claims.** Every story or image
  is introduced either by a question it answers or by a claim it illustrates,
  and the lesson is stated right after it. The reader should always know which
  mode they are in. Where the registry marks a story "neither", or its lesson
  "none", and this unit opens or closes that story: supply the introducing
  sentence or the lesson when the essay states the claim elsewhere; otherwise
  ask.
- **A2 Stories need consequences.** Don't cut the outcome of a story, even if a
  following line states the moral. Never propose a cut that removes an outcome.
  Where the registry shows a story whose outcome is not told (ask).
- **A3 Transitions must say why the next move is happening** ("What, then, is a
  possibility? An image will help.").
- **A4 Escalate toy examples to their starkest stakes, stated plainly** ("the
  last meal," not "the last food for many days"), so the intuition becomes
  obvious.
- **A5 Keep each metaphor consistent through the essay** (roads stay roads; a
  life is "a pathway across many roads"). Fix a unit where the registry shows
  the vehicle changing.
- **A6 Disambiguate terms the book uses technically.** If a word has a book
  meaning (time as the counting of choices), qualify ordinary uses ("clock
  time").
- **A7 Examples should be realistic and not accidentally pejorative.** Pick the
  example whose force comes from the argument, not from the reader's distaste.
  A different example is the author's choice (ask), naming the trouble and a
  candidate.
- **A8 Avoid empirical claims a reader can easily counter** (e.g. "criminals
  always get caught"). Narrow the claim to what the argument needs when the
  narrowing is plain; otherwise ask, giving the counterexample.
- **A9 An essay may end on its own closing line; it need not bridge to the next
  essay.** Never add a bridge to the next essay, and never flag its absence.
- **A10 Use subheadings to break long sections.** Headings may name stories (The
  Burning House) or concepts (The Limits of Measurement); each should fit its
  section. Where the registry marks a section as running long with no
  subheading (ask), plant the query at the unit where the break belongs,
  proposing the heading. Where a heading does not fit its section (ask).

### References and terms

- **R1 Vocabulary sequencing.** Essays come before the Metaphysic, so use only
  terms defined up to the essay's position. Terms an essay introduces are
  defined in plain English at first use. Fix a unit that uses a term from the
  registry's "terms used early" line by saying the thing in plain English.
  Where a term this essay introduces has no plain-English definition at its
  first use, supply one from CONCEPT NOTES if they hold it; otherwise ask.
- **R2 Back-reference terms established earlier** (e.g. "As the commentary on
  Sermon One showed...") rather than redefining them. Where a unit redefines a
  term the registry lists under "terms available", replace the redefinition
  with a back-reference to where it was established.
- **R3 Put forward and backward references in footnotes, or inline when the
  reference is itself a definition.** A reference that belongs in a footnote is
  planted as a footnote tag (see Footnotes and markers), never written out.
- **R4 Name external scriptures and figures specifically** (Lotus Sutra,
  Mencius, Prashna Upanishad). Never invent standard-sounding terms. The book's
  own parables stay unnamed. Where a source is left vague and the essay or
  CONCEPT NOTES say which it is, name it; where they do not, ask. Never supply
  a name from memory.
- **R5 Avoid the word "Hindu";** prefer geographic phrasings such as "the land
  the Sindhu waters."
- **R6 Nietzschean images may appear unnamed; Nietzsche is never named or cited
  as authority in the body.** Where a unit of the body names him, remove the
  name and keep the image.
- **R7 Sermons are the primary authority.** Gloss before long quotes; use
  italicized paraphrase for short phrases. Where a long sermon quote arrives
  with no gloss before it, supply the gloss only when the essay's next
  sentences already state it; otherwise ask. The quoted sermon text is never
  changed.

### Footnotes and markers

- **F1 Inline markers are for footnotes only, format `[^Ex]`.** No
  change-tracking markers in the body. Copy every existing `[^...]` marker byte
  for byte, and never write a marker of any other kind into a unit. The two
  tags this pass may plant (`[Footnote: ...]` and `[Suggestion: Query: ...]`)
  are the author's request tags, resolved before the essay is final.
- **F2 Use footnotes for supporting evidence, cross-tradition parallels (e.g.
  Mill vs. this standard), and material that answers an obvious reader
  objection without slowing the body.** Where a unit carries such material in
  the body, plant `[Footnote: <what the note should hold>]` after the sentence.
  The footnote road drafts it with the author.
- **F3 Flag any footnote content written from memory for verification against a
  source.** Every `[Footnote: ...]` gist this filter plants for a fact, a
  quotation or a parallel ends with `verify against a source`. This filter
  never writes a citation.

### The passes

- **P1 Soundness.** Check that the essay is philosophically sound given its
  premises: one definition per term, examples actually judged by the stated
  standard, no unconditional claims the definition contradicts. Judge each unit
  against the registry's definitions and standard lines. A second definition, an
  example judged by some other standard, or an unconditional claim the
  definition contradicts is a question about what the essay claims (ask): name
  the two things that disagree and the fix you would make.
- **P2 Frank pushback.** Give frank pushback with counterexamples where the
  argument has a gap, and propose a concrete fix (ask). The query states the
  counterexample and the fix in one line. Do not soften it.
- **P3 Length.** Cut wordiness before cutting stories. Remove a story only if
  another already does its work. This filter cuts words (S1, S4) and never cuts
  a story. Where the registry shows two stories doing the same work (ask), on
  the first unit of the later one, naming the story that already does it.

## How to fix

Diagnose first, with specific citations, then offer the corrected text: `ref`
names the rule id (V1 to V3, S1 to S7, A1 to A10, R1 to R7, F1 to F3, P1 to P3)
and the registry line it rests on, and `why` quotes the words at fault and says
in a phrase what the change gains. Keep notes very short: the author often
listens to sections aloud.

Prefer cutting to rewriting, and rewriting to adding. Keep every claim, every
story and its outcome, and every term. When two rules apply to one sentence and
their fixes disagree, the earlier section wins, except that A2 (a story keeps
its outcome) outranks every rule that cuts. If you find yourself rewriting every
sentence of a unit for different reasons, the unit has a voice you do not share:
`keep` it.

## Out of scope, always

Copy these byte for byte: sermon text (the Early Modern English of the Sermons,
quoted or whole), verse, quoted text by another writer, footnote definitions,
code, math (`$...$`), tags in square brackets, text inside an [Omit] block.

## Boundaries: what this filter leaves to other roads

- **Revision markup.** The source skill shows each revision in colour (cut text
  struck in red, new text bold in a high-contrast colour, a one-line legend,
  only the current pass's changes, short notes at the end). Here the harness
  does that: each proposal reaches the essay's Doc tab as an old-and-new form
  beside the unchanged text, only this run's proposals are shown, and `why`
  carries the note. Write no markup, legend or notes of your own.
- **Placeholders.** This filter never writes placeholder text standing in for
  the author's words. What it cannot supply it asks for, and a term it could
  not verify it names in the query.
- **Section-by-section delivery and printing the full essay.** The run and the
  author's review in the tab are the delivery; the essay file after the review
  is the full text with every accepted change applied.
- **Old-content triage.** Sorting removed material into insert, footnote, move
  to another essay or drop, and drafting the inserts, is done in conversation
  with the author: a filter sees no removed material and cannot move text.
- **Drafting.** New essays and new sections are written on the write road, not
  here.

## Examples

All examples are from the source skill unless marked (original).

- **S1** Fault: `another puts her own needs first` -> `another puts herself first`.
- **S2** Fault: `to seek more` -> `to seek new experiences`.
- **S7** Fault: `whether a better alternative exists` -> `whether a better choice exists`.
- **A3** Good: `What, then, is a possibility? An image will help.`
- **A4** Fault: `the last food for many days` -> `the last meal`.
- **A6** Fault: an ordinary use of `time` in an essay where time is the counting of choices -> `clock time`.
- **A8** Fault: `Criminals always get caught.` -> the same sentence with `[Suggestion: Query: A8: a reader can name criminals who were never caught; narrow the claim to what the argument needs, or cut it?]` (query original).
- **R5** Fault: `the Hindu tradition` -> a geographic phrasing such as `the land the Sindhu waters` (fault sentence original).
- **S6** Fault: `The horizon is eternal.` standing bare -> the same sentence with `[Suggestion: Query: S6: this aphorism stands bare; which sentence should lead to it, or what explanation should follow?]` when STYLE LAW on aphorisms finds it bare and the essay's argument does not supply what is missing (query original).
