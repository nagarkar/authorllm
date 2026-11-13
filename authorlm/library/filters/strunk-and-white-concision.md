---
class = "global"
state = "not used: the shared strunk-and-white reading is this filter's coordination object"
reading = "strunk-and-white"
profiles = ["audience"]
---

# Strunk and White: diction and concision

Replace the words the writer did not choose, and cut the words that do no work:
the ready-made phrase, the padded verb, the inflated word, the needless phrase,
the passive that hides a responsible actor, the empty booster. This filter makes
judgments, so it proposes and the author decides. Most units of edited prose
need nothing: `keep` them. A voice is not a fault, and a sentence you would have
written differently is not a fault.

## Run order

The three strunk-and-white filters run in this order, each over the text the
author accepted from the one before: concision (it rewords), then grammar (it
checks what the rewording left), then mechanics (it settles the marks once the
words are fixed). This filter is the first.

## Rank

1. **STYLE LAW** outranks every rule here. Where a law sets a convention (no
   semicolons, no dashes, curly quotes, the serial comma, a register), the law
   is the convention. Where a fix would break a law, `keep` the unit and name
   the law in `ref`.
2. **The essay's own settled practice** comes next, as the registry records it.
3. **The rules below** come last. They describe standard modern English, not
   the whole of Strunk and White: only the numbered rules here are enforced.

A register (scriptural, archaic, oral storytelling, a child's voice) never
excuses a grammar fault: G1 to G8 apply in every register. A register governs
only diction and formulas (C1, C2), spelling (M9) and punctuation by its own
models (Punctuation for the ear). Formal register raises the bar on a few
grammar points; no register lowers it.

## Fault or choice

A departure from a rule is a CHOICE, and you `keep` it, when any of these holds:

- it recurs in the essay (the registry shows the same form elsewhere);
- it sits at a joint: the end of a paragraph, the answer to a question, the
  turn of a scene, a line of speech;
- it makes a pattern with its neighbours (a run of like shapes, a refrain, a
  series of three);
- read aloud, it sounds intended: the voice lands on it.

It is a FAULT only when it is isolated, sits mid-paragraph, and read aloud
sounds unfinished or makes the listener go back. When the signs disagree, it is
a choice. When you are not sure the sentence is wrong, it is not.

This test is for rules about shape and sound, where a writer can choose: every
concision rule (C2 to C8, and C1 except that recurrence never makes a stock
phrase a choice), G1, G5, G6, G7, and M3 to M7. It never applies to
faults with one right answer: agreement (G2), case (G3), a modifier that
misattaches (G4), a word confused with its twin (G8), apostrophes and homophones
(M1), the series under a settled convention (M2), drift from a settled
convention (M8), slips (M9) and dialogue scaffolding (M10). A confused word, a
wrong case or a slip is a fault wherever it stands and however often it recurs:
recurrence there is a habit to fix in every unit, not a choice to keep. The same
holds for a stock or inflated phrase under C1: a habit is not a decision.

All of this governs the author's own narration. In quoted speech, and in the
idiom of a first-person narrator, a narrator in a character's idiom or a told
tale, a nonstandard form that recurs in that voice is the voice: no rule here
touches it. Only a form that appears nowhere else in that voice is judged, and
only a plain typing slip is fixed in quoted speech.

## The ear

Before you return a `replace`, read the new unit aloud in your head against the
old one. If the new one is stiffer, stranger, harder to say on a first reading,
or flatter to hear than the fault it fixes, the rule is not worth it: `keep` the
unit. Break any of these rules sooner than write a sentence a reader would
stumble on. On the rules with one right answer (listed above), the ear chooses
the most natural correct fix; it does not decide whether to fix.

## Ask, do not guess

Some faults have an answer only the author knows: which of two nouns a pronoun
means, whether a clause between commas restricts or adds, which particular thing
a vague word stands for, whether a run of sentences plods on purpose. Rules
marked (ask) are never rewritten. Return `replace` with the unit unchanged
except for one tag planted straight after the sentence:
`[Suggestion: Query: <the question in one line, naming both readings>]`. A
query costs the author a decision just as a rewrite does: plant one only where a
careful reader would actually misread or stall.

Doubt about whether there is a fault means `keep`; doubt about the fix means
ask. A question that would recur in several units (a rhythm the essay keeps, a
habit of the voice) is asked once, at its first unit, never planted in every
unit.

## The registry: the prelude

Read THE ESSAY whole and return these lines. The three strunk-and-white filters
share ONE reading of each essay (`reading = "strunk-and-white"`): the first
pass to run makes it and the later passes receive the same bytes, even after
edits, so two passes never disagree about what the essay is. Describe the
essay's form, which line edits do not change, never a sentence's wording.

    mode: exposition | argument | narration | told tale | dialogue | verse, by units
      (a "told tale" is a story a character tells, quoted or not; a first-person
      narrator's voice counts as a character's)
    register: formal | general | conversational | oral storytelling | scriptural or
      archaic | child's voice; declared by STYLE LAW, or held by the text without
      a law (say which). Formal means STYLE LAW or AUDIENCE PROFILE declares it,
      or the essay writes for specialists, avoids contractions and uses whom.
    terms of the field: technical and foreign words the essay uses in their
      technical sense (the words and the units)
    audience: from AUDIENCE PROFILE, in a phrase, if present
    sound: written for the ear (read aloud, told) or for the eye; usual sentence
      length; the characteristic shape (paired clauses, and-chains, short
      declaratives, long periodic sentences)
    tense: narration tense; reporting tense for what writers say (present | past);
      past for dated events; units where the tense shifts on purpose
    deliberate repetition: only the standard techniques (refrain, anaphora or
      epistrophe, parallel series and threes, a told tale's pattern, a key term
      kept constant, a build to a climax, a summary for a listener, a STYLE LAW);
      for each, the technique, the phrase and the units. Accidental echoes are
      not listed.
    hedges on purpose: units whose qualifiers mark how strong a claim is
    conventions:
      serial-comma: yes | no
      clause-comma: usual | when long | rare   (comma before and/but joining clauses)
      possessive-s: s's | s'   (the possessive of a name ending in s)
      quotes: curly | straight
      quote-punctuation: american | logical
      dash: em spaced | em closed | en spaced
      ellipsis: three dots | spaced dots | ellipsis character
      colon-capital: capital | lower   (before a full sentence)
      numbers: words to ten | words to one hundred | figures
      compound-modifiers: hyphenated | open   (before a noun)
      spelling: american | british
      semicolons: <free text: used, or not seen>
      italics: <free text: titles, foreign words, emphasis>
      spelling-variants: <free text: variants seen, such as toward/towards>
    out of scope: verse, headings, footnote definitions, quoted text by another
      writer, code, math, tags in square brackets, and text inside an [Omit] block

Write the conventions block in that shape: one line per key, then `law` where
STYLE LAW sets it (the defaults set most), else ONE of its listed forms, or
`mixed: ask the author` (the essay uses both forms, with the counts), or `not
seen`.

A convention marked "mixed: ask the author" is out of bounds for this run: `keep`
every unit on that point, except the first unit where the convention occurs.
There, once per convention, plant
`[Suggestion: Query: style sheet: <convention>, the essay has <n> of one form and <m> of the other; which is the book's?]`
and change nothing else in that unit. The answer becomes STYLE LAW, the book's
style sheet. One open convention is one question, never a dozen unit proposals.

## Never a fault, whatever any usage book says

Only the numbered rules below are enforced. These are standard English, and the
Strunk and White name must not bring them in:

restrictive `which` and restrictive `that`, in either choice, even mixed (never
change one to the other); a split infinitive; a preposition at the end of a
sentence; a sentence that begins with And, But, Or, So, Because or However;
singular `they` for an indefinite or unknown person, or for anyone who uses it;
`hopefully` as a sentence adverb; `while` for although and `since` for because
where the sense is clear; `this` or `which` referring to a whole clause; `none`
with a plural verb; `whose` for a thing; `data` and `media` with either number;
`like` meaning such as; `due to` as a preposition; `different than` and
`different to`; `try and` outside formal register; `than me` and `it's me`;
object `who`, except directly after a preposition (`to whom`); a pronoun before
a gerund in either case (`his leaving`, `him leaving`); `over` for more than; an
omitted `that`; `-wise` suffixes; `media` with either number when it means the
press (not `a media` for one medium); `that` referring to people; `between` with
more than two; `each other` and `one another` for two or for many; `each` after a
plural subject with a plural verb (`They each have`); `could care less` and `hone
in` outside formal register.

## Out of scope, always

Copy these byte for byte: verse, headings, footnote definitions, quoted text by
another writer, code, math (`$...$`), tags in square brackets, text inside an
[Omit] block. Quoted speech keeps its grammar, filler and rhythm; only a plain
typing slip in it is fixed. A first-person narrator, narration in a character's
idiom, and a told tale keep their idiom (dialect grammar, filler, formulas,
repetition, rhythm); a fault that is not part of that idiom (it appears nowhere
else in the voice, and the Fault or choice test calls it a fault) is judged as
narration.

## Three standing protections

After STYLE LAW, these outrank every rule below:

- **Precision.** A qualifier, a scope, a hedge that marks how strong a claim is
  (the registry's hedges on purpose), or a distinction the claim needs stays,
  however many words it costs.
- **Repetition, when it is a technique.** Repetition stays only when it is one
  of the standard techniques: a refrain or chorus; anaphora or epistrophe (the
  same words opening or closing successive clauses); a parallel series, the
  rule of three included; the repeated pattern of a told tale or a story for
  children (three brothers, three tries); a key term kept the same so its sense
  does not shift; a repeat that builds to a climax or lands an emphasis at a
  joint; a summary for a listener; a repetition a STYLE LAW declares. Any other
  repetition (a word echoed by accident, a sentence that restates the one
  before, a phrase used twice with nothing gained) is not protected: C2 judges
  it. Never fix a repeated noun by swapping in a pronoun or a synonym: elegant
  variation is a fault this filter must not create. Cut the repeat or keep it.
- **The audience and the ear.** A word the AUDIENCE PROFILE's reader needs, or a
  word the sentence needs for its sound (a beat it leans on, the stress on its
  last word, a repeated sound), is working.

## What counts, per unit

Listed in order of how often they matter. When two apply to one sentence, fix
the first.

- **C1 Stale and inflated diction.** A phrase taken ready-made instead of chosen:
  a dying metaphor whose image no reader still sees (`toe the line`, `the
  elephant in the room`, `at the end of the day`); a padded verb, a weak verb
  plus a noun where one plain verb exists (`give rise to` for cause, `make
  contact with` for meet, `serve the purpose of`, `have the effect of`, `made a
  decision` for decided, but not `made a hasty decision`), only where that verb
  is at least as common and as easy to say as the phrase (`pay attention`
  stays: `heed` is not plainer); a formal or rare word where a common one fits
  the same sentence with the same sense and the same grammar (`utilize` for use
  where nothing special is being put to use, `commence` for begin, `in the event
  that` for if, `endeavour` for try), judged by register, never by a word's
  origin, and never where the formal word has a sense the plain one lacks
  (`prior to` meaning logically prior; `facilitate the transfer` is not `help
  the transfer`); jargon from another trade where an everyday word exists
  (`leverage`, `going forward`); a foreign phrase used for an air of learning
  where an everyday English one says the same (`vis-à-vis` for about, `per se`
  for emphasis, `ipso facto` for therefore). Test: would a reader see an image
  or hear a decision in this phrase? If not, replace it with the plain word.
  Leave a figure the essay owns (the registry), a PROTECTED TERM, a STYLE LAW
  lexicon entry, a term of the field (the registry) used in its technical sense
  (`a priori`, `mens rea`); in narration, a told tale or prose for a young
  audience, a formula of the telling that marks where the listener is in the
  story (`once upon a time`, `the biggest storm anyone could remember`, `made
  their way home`, `happily ever after`); a long or grand word the prose savours
  on purpose, or uses for irony or a character's pomposity; and all of these in
  a character's voice or a declared register.
- **C2 Needless words.** A phrase that says in many words what fewer say with
  nothing lost: `the fact that`, `there is no doubt but that`, `in a hasty
  manner`, `who is` and `which was` before a phrase that can stand alone, `the
  reason is because`, `as to whether`, `in order to` where `to` alone keeps the
  sense (never after ask, want, try, arrange or another verb that takes an
  infinitive object), a sentence that repeats the one before and adds nothing (no example, no
  narrowing, no turn, and not one of the techniques the Repetition protection
  names), and a word or phrase echoed by accident within a sentence or the next
  where one occurrence can go with nothing lost (if neither can go, keep). Cut; where the fix is one exact word for the phrase, use that word.
  Leave the formulas of a declared register (`it came to pass that`, `and it was
  so`) and of oral storytelling (`once upon a time there was`).
- **C3 Passive that hides a responsible actor.** A passive clause is `be` or
  `get` plus a past participle whose subject receives the action (`The proposal
  was rejected`, `He got fired`), or a participle alone in a reduced clause
  (`the essay rejected last year`); an active version with the same verb exists
  and a `by` phrase could be added. These are NOT passives, never touch them
  here: `be` with an adjective or a participle that names a state or takes
  `very` (`was interested`, `is flawed`, `was closed all winter`), `there is`,
  `it is` plus an adjective (`it is clear that`), the cleft `it is X who`,
  `became`, `seemed`, `was walking`. But `it was decided that`, `it is believed
  that`, `it has been argued that` ARE passives, with the clause moved to the
  end. Flag a passive only when
  the actor is known from the essay, is responsible for what happened, and the
  sentence reads as evasion without them (`Mistakes were made` where the essay
  knows who made them). Leave a passive whose actor is unknown, generic or beside
  the point; whose subject is the paragraph's topic or the character whose eyes
  we are behind; whose `by` phrase stands last on purpose; that states a
  definition or method; that already names its actor; or whose missing actor is
  the meaning (an inherited rule, a custom with no author). Never supply an actor
  the essay does not name. Where an agentless `It has been argued` hides a source
  rather than an actor, plant `[Footnote: <what source is needed>]` and leave the
  voice.
- **C4 Empty boosters.** `very`, `really`, `truly`, `so`, `extremely`,
  `basically`, `totally`, `absolutely`, `actually`, `literally`, used to push a
  word the sentence already makes strong (`very unique`, `really essential`).
  Test: strike the word; if the sentence says the same with no loss of degree, it
  was empty. Never `so` before a result clause (`so wide it let the daylight
  through`) or in a teller's or character's voice; `literally` where the literal
  reading is live belongs to grammar G8. Never a downtoner that sets a degree or hedges (`rather`, `quite`,
  `somewhat`, `pretty`, `fairly`, `a little`), never a word that measures size,
  amount or pace (`a little more each year`), never `actually` marking a contrast
  with what was expected.
- **C5 Negative form.** `not` plus a word where one positive word says exactly
  the same (`did not remember` for forgot, `not many` for few). Never replace
  `not X` with an un-, in-, dis- or im- word: `not honest` and `dishonest` are
  different claims. Leave litotes (`not unlike`, `not without merit`), a denial,
  a contrast, a correction, and a thing that did not happen when the reader
  expected it to (`did not say a word`).
- **C6 Vague for definite (ask).** A general word standing where the essay has
  already named, or plainly implies, the particular thing (`a period of
  unfavourable weather` where the essay has said it was a week of rain;
  `things`, `aspects`, `factors`, `in terms of` used as filler). Never where the
  general word is the subject (`nature`, `being`, `the good`), a PROTECTED TERM,
  or a deliberate abstraction. Where the essay names the particular, replace;
  where it only implies it, ask. Never invent a detail.
- **C7 Loose sentences in a run (exposition and argument only; ask).** Four or
  more consecutive sentences, each two clauses joined by and, but, so or which,
  where the sameness is plainly accidental and the paragraph reads as a list of
  afterthoughts. Ask; never rewrite. In narration, a told tale, a declared
  register or prose for a young audience, a run of and-clauses or short
  sentences of one shape is the gait: leave it.
- **C8 Weak ending (exposition and argument only; ask).** A sentence whose last
  words are a minor qualifier, a date or a stock phrase that buries the word
  carrying the point, where moving only that phrase forward fixes it. Ask; never
  invert a sentence to put a word last. In narration keep the order in which
  things happen or are seen.

## Worth a decision

Propose only where the unit's waste is worth the author's decision: a phrase of
three or more words cut or replaced, two or more faults in the unit, or one fault
that makes a sentence hard to read. A lone booster, a single `in order to`, one
`the fact that` is not worth it: `keep` the unit.

## How to fix

Prefer cutting to rewriting, and rewriting to adding. Keep every claim, image,
term and the sentence's order. Never shorten a sentence at the cost of its
meaning. If you find yourself rewriting every sentence of a unit for different
reasons, the unit has a voice you do not share: `keep` it. The exception is a
unit where every sentence carries the same named fault (C1 most often, a
paragraph built of stock phrases): propose, and say in `why` which fault recurs.
Name the rule id (C1 to C8) in `ref` and say in `why`, in a phrase, what the
change gains.

## Examples

Short source key: Strunk 1918 (The Elements of Style), Woolley 1907 (Handbook of Composition), Abbott 1883 (How to Write Clearly), Kipling 1902 (Just So Stories): public domain; (original) and (correction ours) mark text written for this filter.

- **C1** Fault: `Her Majesty here partook of lunch.` -> `Her Majesty had lunch here.` (Abbott 1883, rule 3; correction ours). Leave alone: `"We'll cross that bridge when we come to it," said the ferryman.` (stock phrase in a character's voice) (original). Leave alone: `Then one night came the biggest storm anyone on the hill could remember.` (a teller's formula marking the turn) (original).
- **C2** Fault: `Owing to the fact that the bridge was down, we went by the ford.` -> `Since the bridge was down, we went by the ford.` (Strunk 1918, rule 13; sentence ours). Leave alone: `the great grey-green, greasy Limpopo River, all set about with fever-trees` (a refrain a child waits for; its length is its pleasure) (Kipling 1902, The Elephant's Child).
- **C3** Fault: `The committee reviewed the clinic's funding in March. It was decided that the clinic would close.` -> `The committee reviewed the clinic's funding in March and decided that the clinic would close.` (original). Leave alone: `The landlord raised the rent twice that year, and in December the family was evicted.` (the family is the topic; no actor is named for the eviction, so none is supplied) (original).
- **C4** Fault: `The results were absolutely essential to the case, and the method was totally unique.` -> `The results were essential to the case, and the method was unique.` (original). Leave alone: `The stream is now quite dried up.` (quite here means completely: it states completion, it does not boost) (Woolley 1907, Glossary, Quite).
- **C5** Fault: `Not many readers finished the book.` -> `Few readers finished the book.` (original). Leave alone: `Not that I loved Caesar less, but Rome the more.` (a contrast, not a roundabout positive) (Strunk 1918, rule 11).
- **C6** Fault: `A period of unfavourable weather set in.` -> `A period of unfavourable weather set in. [Suggestion: Query: what weather was it, rain or cold? name it if the essay knows, or keep the general phrase]` (Strunk 1918, rule 12; query ours). Leave alone: `Justice asks more of us than the law does.` (the abstraction is the subject) (original).
- **C7** Fault: `The report was finished in May, and the board read it in June. The chairman approved it, but the treasurer objected. The vote was delayed, so the members went home. The matter came up again in the autumn, and it was settled then.` -> the same four sentences, with `[Suggestion: Query: four sentences in a row join two clauses the same loose way; vary their shapes, or is the sameness meant?]` after the fourth (original). Leave alone: `He fed the horse and he banked the fire and he sat down to wait.` (narration: the and-chain is the gait) (original).
- **C8** Fault: `Humanity has hardly advanced in fortitude since that time, though it has advanced in many other ways.` -> the same sentence with `[Suggestion: Query: end on fortitude (Since that time humanity has advanced in many other ways, but it has hardly advanced in fortitude), or keep the order?]` (Strunk 1918, rule 18; query ours). Leave alone: `Kant answered Hume's challenge with the synthetic a priori in 1781.` (a date closing a plain statement of fact buries nothing) (original).
