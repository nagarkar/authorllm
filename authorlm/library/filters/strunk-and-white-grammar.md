---
class = "global"
state = "not used: the shared strunk-and-white reading is this filter's coordination object"
reading = "strunk-and-white"
profiles = ["audience"]
---

# Strunk and White: grammar

Fix the sentence faults that make a reader misread or stop: a clause cut off
from the sentence it belongs to, a verb that disagrees with its subject, a
pronoun in the wrong case, a modifier attached to the wrong actor, related words
kept apart, a series in unlike forms, a tense that drifts, a word used for its
near-twin. These have answers careful editors agree on. Most units of edited
prose have none: `keep` them. AUDIENCE PROFILE and the registry's register line
say what counts as formal for this book.

## Run order

The three strunk-and-white filters run in this order, each over the text the
author accepted from the one before: concision (it rewords), then grammar (it
checks what the rewording left), then mechanics (it settles the marks once the
words are fixed). This filter is the second.

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

## What counts, per unit

- **G1 Fragment.** In exposition and argument: a subordinate clause or phrase cut
  off by a full stop from the main clause it needs, so the reader must reread
  to attach it (`The argument fails. Because the second premise is false.`).
  Join it. In narration and a told tale, only a clause opened by because,
  although, which, while or since, cut off from the clause it modifies (not a
  sentence opened by `Which is why` or `Which meant`, a style choice); a
  fragment of perception (`Thunder. Then rain.`) or a beat in a character's
  idiom is style.
- **G2 Agreement.** A verb that disagrees in number with its subject: across an
  intervening phrase after an ordinary head noun (`The list of names were
  long`); with `each` or `every`. Number-transparent heads take their number
  from the noun after `of`, and are correct: `a number of critics have`, `the
  majority of the votes were`, `a lot of`, `a couple of`, `the rest of`, `a
  third of`, `a variety of`. `The number of critics is` is singular. One amount
  is singular (`Ten miles is a long way`). With or and nor the verb agrees with
  the nearer subject. A compound naming one thing (`bread and butter is`) or
  headed by each or every is singular. `There is` before a compound whose first
  member is singular is correct. `none` takes either number. `either` and
  `neither` alone take a singular verb only where the register is formal.
  Collective nouns: flag only a verb that switches number for the same noun
  inside one sentence. `the only one of the students who has` is singular and
  correct; `one of those who` with a singular verb is changed only where the
  register is formal.
- **G3 Pronoun case.** A pronoun in the wrong case after a preposition or as a
  subject in narration or exposition (`between you and I`, `Me and him went`,
  `for he and his brother`, coordinated `myself` for me or I); hypercorrect
  `whom` or `whomever` as the subject of its own clause (`the critic whom I think
  misread Hume`, `to whomever asks`). `than` plus a pronoun only where it leaves
  two readings after a transitive verb (`She trusts him more than me`): repair
  with a verb (`than she trusts me`), never a bare `than I`.
- **G4 Dangling modifier.** An opening participle, gerund or infinitive phrase
  with no subject of its own, whose implied actor is not the subject of the main
  clause, AND where a reader would attach it to the wrong actor and stumble
  (`Walking home, the rain began.`; `Having reread the essay, the conclusion
  seemed weak.`). Never flag participles that work as prepositions (`given`,
  `considering`, `assuming`, `granted`, `provided`, `regarding`, `concerning`,
  `including`, `following`, `according to`, `owing to`, `based on`); disjuncts
  whose actor is the writer or reader (`strictly speaking`, `judging by`, `put
  simply`, `to be fair`, `to see why`, `taken together`); absolutes with their
  own subject (`All things considered`, `The premises granted`). Make the actor
  the subject, or give the phrase its own subject, changing as little as
  possible.
- **G5 Related words apart.** A subject so far from its verb that a reader loses
  it, or a modifier next to the wrong word where the sense changes: move them
  together. A pronoun or relative that can attach to two nouns, both plausible
  and the difference mattering (ask): judge it read aloud first, and if the stress
  and sense make the reference plain on one hearing, leave it. Preverbal `only`
  is normal English: flag it only where two readings are both live and matter.
- **G6 Parallel form.** The members of a series or a correlative pair (both/and,
  not only/but also, either/or, neither/nor) in forms that do not fit the same
  slot, so the reader stumbles (`She likes reading, to swim and a good film.`).
  Coordinates of different kinds that each fit the slot are correct (`a fool and
  proud of it`, `slowly and with care`). A correlative one word early or late is
  not a fault unless it changes what is coordinated.
- **G7 Tense drift.** A narration or summary that shifts tense without reason,
  against the registry's tense line. The literary present for what a text says
  beside the past for what its author did (`Kant argues ... Kant wrote it in
  1781`) is correct; so is a lineage narrated in the past until its present
  steps.
- **G8 Words confused.** In narration and exposition only, a word used for its
  near-twin, where one answer is right: affect/effect, imply/infer (infer for
  imply), principle/principal, complement/compliment, discreet/discrete,
  flout/flaunt, reign/rein, pore/pour (over), peak/peek/pique, faze/phase,
  loath/loathe, tortuous/torturous, wreak/reek (`reek havoc`; never change
  `wrought havoc`), militate/mitigate, elicit/illicit, eminent/imminent/immanent,
  prescribe/proscribe, precede/proceed, censor/censure, adverse/averse,
  allude/elude, accept/except, breath/breathe, `irregardless`. Sound-alike
  spellings (its/it's, lead/led, loose/lose, then/than) belong to mechanics M1.
  Where the register is formal only: `less` directly before a
  plural count noun (`less objections`; never `less than ten`, `or less`, `one
  less`, time, money, distance) and `like` introducing a full clause. Flag
  `literally` only where the literal reading is live and the sentence misleads.

## How to fix

Change the fewest words that make the sentence correct. Keep every claim, every
term of art, and the sentence's order where the fault allows. Name the rule id
(G1 to G8) in `ref` and say in `why`, in a phrase, what was wrong.

## Examples

Short source key: Strunk 1918 (The Elements of Style), Woolley 1907 (Handbook of Composition), Fowler 1908 (The King's English, 2nd ed.): public domain; Wikibooks Rhetoric and Composition: CC BY-SA 4.0; (original) and (correction ours) mark text written for this filter.

- **G1** Fault: `The care of oil lamps requires every day some untidy and disagreeable labor. While electric lights give the housekeeper no trouble.` -> `The care of oil lamps requires every day some untidy and disagreeable labor, while electric lights give the housekeeper no trouble.` (Woolley 1907, rule 24 C). Leave alone: `Again and again he called out. No reply.` (narration: an emphatic fragment is style) (Strunk 1918, rule 6).
- **G2** Fault: `The quality of the arguments were poor.` -> `The quality of the arguments was poor.` (original). Leave alone: `A number of the passengers were already asleep.` (number-transparent head takes the plural) (original).
- **G3** Fault: `His brother, whom he said would send him the money` -> `His brother, who he said would send him the money` (Strunk 1918, ch. V, Whom). Leave alone: `"Who do you mean?" "It's me."` (dialogue, and it's me is standard) (Fowler 1908, ch. II, Case).
- **G4** Fault: `On arriving in Chicago, his friends met him at the station.` -> `When he arrived in Chicago, his friends met him at the station.` (Strunk 1918, rule 7). Leave alone: `Considering the circumstances, you may go.` (participle working as a preposition) (Fowler 1908, ch. II, Participles).
- **G5** Fault: `The storm broke just as we reached the shore with great violence.` -> `The storm broke with great violence just as we reached the shore.` (Woolley 1907, rule 77; correction ours). Leave alone: `He only found two mistakes.` (preverbal only, one live reading) (Strunk 1918, rule 16, who calls it a fault).
- **G6** Fault: `The crowd began to wave handkerchiefs and shouting good-byes.` -> `The crowd began to wave handkerchiefs and to shout good-byes.` (Woolley 1907, rule 111 A). Leave alone: `She worked slowly and with great care.` (unlike forms that each fit the slot) (original).
- **G7** Fault: `She opened the letter, reads it twice, and put it in the drawer.` -> `She opened the letter, read it twice, and put it in the drawer.` (original). Leave alone: `The Friar confesses that it was he who married them.` (past inside a present summary marks earlier action) (Strunk 1918, rule 17).
- **G8** Fault: `The drought effected the harvest.` -> `The drought affected the harvest.` (original). Leave alone: `The signers of the petition were less than a hundred.` (less than a number is correct) (Strunk 1918, ch. V, Less).
