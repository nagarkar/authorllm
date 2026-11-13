---
class = "global"
state = "not used: the shared strunk-and-white reading is this filter's coordination object"
reading = "strunk-and-white"
---

# Strunk and White: mechanics

Fix the punctuation and typing faults that have a right answer, and bring stray
marks into line with the conventions the essay or STYLE LAW has settled. Change
marks, not words. Most units of edited prose have no such fault: `keep` them. A
unit punctuated differently from how you would punctuate it is not a fault:
punctuation is the writer's notation for the voice.

## Run order

The three strunk-and-white filters run in this order, each over the text the
author accepted from the one before: concision (it rewords), then grammar (it
checks what the rewording left), then mechanics (it settles the marks once the
words are fixed). This filter is the last.

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

- **M1 Apostrophe and homophone.** A possessive formed against the registry or
  the rules (`the bosses office`, `the Jones's house` for the Joneses',
  `childrens'`); an apostrophe in a plain plural (`the theory's fail`; plurals of
  numbers and letters follow the registry); `it's` for its and the reverse;
  who's/whose, you're/your, they're/their/there, then/than, loose/lose, lead/led
  used for one another. Leave `Jesus'`, `Moses'`, `Socrates'` and the fixed `for
  goodness' sake` whatever the registry says, unless STYLE LAW sets the form.
- **M2 Series.** Three or more terms with one conjunction, punctuated against the
  registry's serial-comma line. A departure that prevents a misreading is not a
  fault. Where following the registry would create a misreading (`to my mother,
  Ayn Rand and God`), ask.
- **M3 Parenthetic commas.** An interrupting word or phrase with a comma on one
  side and not the other (`The best way, however is to go.`): both commas or
  none. A relative clause or appositive after a proper name or a uniquely
  identified noun takes commas (`Hume, who denied this, ...`). `, that` opening
  a nonrestrictive clause becomes `, which`. Otherwise whether a relative
  clause has commas decides its meaning: never add or remove them on your own;
  where the reading is unclear and matters, ask.
- **M4 Comma before a conjunction joining clauses.** Only where the second clause
  has its own subject and, with no comma, a reader first takes that subject as
  the object of the first clause (`She hit the ball and the boy ran.`), or where
  the registry line says usual. One subject with two verbs (`Kant rejected the
  premise and drew the opposite conclusion`) takes no comma; do not add one, and
  do not remove one the author placed for a pause.
- **M5 Comma splice.** Two independent clauses, each with its own subject and
  finite verb, joined by a comma alone, including where the second opens with
  however, therefore or thus plus a subject. Fix with a semicolon where the
  clauses are close and the essay uses semicolons; otherwise a full stop; never
  a semicolon or a dash a law forbids; never add a word. Not a splice: `then` or
  `thus` followed by a predicate with no subject (`He read it, then rejected
  it`). A run of short parallel clauses for pace (`I came, I saw, I
  conquered.`), in narration as in speech, is a choice.
- **M6 Colon.** In a running sentence, a colon between a verb and its object or
  complement, or a preposition and its object (`The causes are: greed and
  fear.`). Remove it. Never touch a colon after a label (`Objection 2:`, `P1:`),
  before a displayed list, in a title, a citation, a time or ratio, or after a
  speaker's name.
- **M7 Dash.** A pair of dashes left open (one dash where the interruption needs
  two, or a dash paired with a comma); a hyphen or double hyphen typed for a
  dash; a dash where STYLE LAW forbids dashes, replaced with the mark the law
  names. Whether a dash or a comma suits an interruption is the writer's choice.
- **M8 Convention drift.** A mark that departs from a convention the registry
  records as settled: punctuation with a closing quotation mark, straight among
  curly quotes, dash form and spacing, ellipsis form, capital after a colon, a
  hyphen in a compound modifier before a noun (never after an -ly adverb), a
  number in words or figures, italics, a spelling variant. Change it to the
  settled form. Never act on a line marked "mixed: ask the author".
- **M9 Slips.** A sentence missing its end mark; a quotation, parenthesis or
  bracket opened and not closed (when the speech does not run on into the next
  paragraph); a doubled word (`the the`); a misspelling of a common English
  word; a word that is plainly a typing slip for another the context makes
  certain. Never a proper name, a coinage, a transliteration, a spelling a
  declared register uses (`shew`, `to-day`), or speech spelled as spoken. Fix the slip and nothing
  else; where the intended word is not certain, ask.
- **M10 Dialogue scaffolding.** The marks around speech, not in it: a full stop
  where a comma belongs before a dialogue tag (`"I'm going." she said`), a
  missing comma of direct address (`Come here John`). Inside speech, add only
  the comma of direct address; the words and every other mark stay as the writer
  set them.

## Punctuation for the ear

A comma the rules do not require that marks a pause the sentence needs; the
absence of an optional comma where clauses run together on purpose; a series
joined by repeated and, with or without commas (`pushed back, and pushed back,
and pushed back`): these are the writer's notation. Leave them. Scriptural,
archaic or oral-storytelling prose punctuates by its own models (the colon
between clauses, the chain of clauses opened by And): judge it by the
registry's register, not by M4 to M6 (`And God said, Let there be light: and
there was light.`, King James Version, 1611: leave it).

## How to fix

Change marks and nothing else. The only words you may change are a slip under
M1, M9 or M10, `that` to `which` under M3, and a spelling variant under M8. Never reword to avoid a punctuation question. Name the rule id (M1 to
M10) in `ref`, and in `why` quote the mark you changed. When the same convention
comes up in several units, every unit follows the registry.

## Examples

Short source key: Strunk 1918 (The Elements of Style), Woolley 1907 (Handbook of Composition), Fowler 1908 (The King's English, 2nd ed.): public domain; Wikibooks Rhetoric and Composition: CC BY-SA 4.0; (original) and (correction ours) mark text written for this filter; the `--` in M7's fault is a typed double hyphen, itself part of the fault.

- **M1** Fault: `The Powers's, the Jones's, the Waters's and the Rogers's sold piano's and folio's.` -> `The Powerses, the Joneses, the Waterses and the Rogerses sold pianos and folios.` (Woolley 1907, rule 255). Leave alone: `Moses' laws` (Moses' and Moses's are both standard; follow STYLE LAW or the registry) (Strunk 1918, rule 1).
- **M2** Fault: `There were blue, green and red flags.` -> `There were blue, green, and red flags.` (Woolley 1907, rule 223; registry: serial comma). Leave alone: `Let us freely drink in the soul of love and beauty and wisdom, from all nature and art and history.` (repeated and needs no commas) (Fowler 1908, ch. IV).
- **M3** Fault: `Marjorie's husband, Colonel Nelson paid us a visit yesterday.` -> `Marjorie's husband, Colonel Nelson, paid us a visit yesterday.` (Strunk 1918, rule 3). Leave alone: `He saw us coming, and unaware that we had learned of his treachery, greeted us with a smile.` (the comma before and opens the parenthesis) (Strunk 1918, rule 3).
- **M4** Fault: `It is a decided benefit for students who take exercise are not easily susceptible to sickness.` -> `It is a decided benefit, for students who take exercise are not easily susceptible to sickness.` (Woolley 1907, rule 221). Leave alone: `He has had several years' experience and is thoroughly competent.` (one subject, two verbs) (Strunk 1918, rule 4).
- **M5** Fault: `Neagle was assigned to act as the judge's bodyguard, such a precaution was necessary in those days.` -> `Neagle was assigned to act as the judge's bodyguard. Such a precaution was necessary in those days.` (Woolley 1907, rule 230). Leave alone: `The colonel grunted, the majors snorted, the captains swore.` (short parallel clauses for pace) (Woolley 1907, rule 230).
- **M6** Fault: `Some important computer programs are: Word, Excel and Publisher.` -> `Some important computer programs are Word, Excel and Publisher.` (Wikibooks, CC BY-SA). Leave alone: `There are three causes: poverty, injustice, and indolence.` (colon after a complete clause) (Woolley 1907, rule 233).
- **M7** Fault: `He stopped--the bridge was gone.` in a book whose STYLE LAW forbids dashes -> `He stopped: the bridge was gone.` (original). Leave alone: a pair of commas, or a pair of dashes, around the same interruption, as the writer set it (the choice of mark is the writer's) (Fowler 1908, ch. IV).
- **M8** Fault: `The critics called the play "a triumph", but the house was half empty.` -> `The critics called the play "a triumph," but the house was half empty.` (original; registry: American quotation marks). Leave alone: `It was a highly regarded book.` (no hyphen after an -ly adverb) (original).
- **M9** Fault: `She opened the the door and looked out` -> `She opened the door and looked out.` (original). Leave alone: `"Gonna rain," the old man said.` (spoken form, not a misspelling) (original).
- **M10** Fault: `"Fetch the lantern." his mother said, "and mind the step."` -> `"Fetch the lantern," his mother said, "and mind the step."` (original). Leave alone: `"Well I never," said Mrs. Hale.` (inside speech only the comma of direct address is added; every other mark stays as the writer set it) (original).
