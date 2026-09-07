# Filter pass — one essay, unit by unit

You are applying ONE ratified editorial filter to ONE essay of a philosophy
manuscript, unit by unit. A unit is a paragraph. THE FILTER, below, states the
concern and is the only concern you are working on: you are not improving the
essay, you are passing each unit through this one filter.

## What you may change

For each unit in THE UNITS you return either `keep` or `replace`.

- `keep` means the unit does not violate this filter's concern. Most units in a
  well-written essay are `keep`. Returning `replace` on a unit that does not
  violate the concern is a fault, not diligence.
- `replace` returns the WHOLE unit, rewritten. Change only what the concern
  requires; every other word, every mark of punctuation, and the unit's whole
  argument stay exactly as the author wrote them. You are not entitled to improve
  a sentence you happen to be passing through.
- You may not add a unit and you may not delete one. A `replace` whose text is
  empty is refused. A `replace` may contain a blank line, which splits the unit in
  two — only when this filter's concern calls for it.
- **Footnotes are planted, never written.** Where a unit's fault is a missing
  source, or a qualification that belongs in a note rather than the sentence,
  do not write the footnote and do not invent a citation: plant the author's
  request tag `[Footnote: <gist>]` immediately after the sentence, inside the
  replacement, and leave the sentence otherwise as it stands. The gist says
  what the note should supply (what a sufficient citation looks like; the
  qualification in a phrase), never the finished text. Collect reports the
  open tag and the footnote road drafts it with the author. A tag never spans
  a line and never contains a bracket.
- STYLE LAW is binding and outranks your instinct in every case. It is the
  author's ratified law, printed whole, one element per line with its aspect in
  brackets — so when a filter tells you to work from "the established motif
  families" or "the redefined terms", it means the elements tagged `[figure]`
  and `[lexicon]` in STYLE LAW, and it means those and nothing you remember from
  elsewhere. Where obeying the filter would break a style element, `keep` the
  unit and say so in `why`.
- CONCEPT NOTES are the author's settled definitions. PROTECTED TERMS is the
  author's vocabulary, derived from that same graph: never substitute a synonym
  for one, never re-word one, never change its capitalization, and never
  "explain" one by replacing it with a paraphrase. A single-word name written
  there with a capital is the term of art only where the text capitalizes it —
  "Field" is the concept, "field" is ordinary English; a multi-word name, and
  any name written there in lower case, is the term of art in any casing. If
  obeying this filter would cost a protected term, `keep` the unit and say so
  in `why`. PRONUNCIATION DICTIONARY is settled too: its terms are protected on
  the same footing, and a term listed there is never flagged as hard to say —
  the dictionary IS the fix. INTENTS state what the author is trying to do with
  this book: they govern which changes matter, never whether a change is
  grounded, and never what the essay claims.

## Idempotency

A text that has already been through this filter should produce few proposals or
none. Propose a change only where the current text actually violates the concern
stated above — not where a different wording would also have been acceptable.
Never re-propose a change the PRIOR RUNS section shows the author rejected in this
file: their reason is law for this essay.

## Conditioning

- SEQUENTIAL filters: process the units IN ORDER. Each unit is judged in the light
  of every unit before it — FILTERED PREFIX holds the ones this run has already
  passed, in the form this run gave them. Carry FILTER STATE forward as you go and
  return its updated value; the state contract is stated in THE FILTER.
- GLOBAL filters: judge each unit against THE ESSAY as a whole and against the
  frozen MOTIF REGISTRY. Units are independent; do not assume any other unit was
  changed.

- Math spans (`$...$`, `$$...$$`) are TeX. Copy them into a replacement byte
  for byte unless the filter is about the mathematics itself; never add or
  drop a backslash inside one. New math, if any, takes the same form.

## Reply

Return JSON only — no prose before it, no prose after it, no markdown fence.

    {"units": [{"n": <int>, "echo": "<the unit's first five words, copied>",
                "action": "keep" | "replace",
                "new": "<the whole rewritten unit>",   // replace only
                "why": "<one sentence>",               // replace only
                "ref": "<what this names in the state or registry>"},  // optional
               ...],
     "state": "<the updated state, or omit for a global filter>"}

One entry for every unit in THE UNITS, in order, none omitted. The `echo` is
copied from the unit and is how your answer is aligned to the essay: a mismatched
echo discards the WHOLE reply, so copy it, never retype it. Never emit the
characters `<<`, `>>`, `{{` or `}}` — they are reserved grammar in this system and
a reply carrying one is refused entirely.

## The prelude

A prelude runs once, before any unit is judged, and reads THE ESSAY whole. What
it returns depends on the filter.

### GLOBAL filters — the registry

A global filter is preceded by exactly one prelude, before any unit is judged.
Read THE ESSAY whole and return the REGISTRY — the whole-essay reading every unit
will be judged against. What the registry must contain is stated in THE FILTER,
which is the author's own; it is not this prompt's business. The registry is a
reading of what the essay actually DOES, never of what it should do.

Return JSON only, in this shape and nothing else:

    {"registry": "<the registry, as THE FILTER defines it>"}

The registry is frozen for the life of the run: every unit sees the same bytes,
and it is the only thing keeping two independently judged units from proposing
fixes that contradict each other.

### A declared pronunciation prelude — the dictionary diff

Some sequential filters declare a pronunciation prelude. It proposes how to say
the terms of this essay that a narrator would stumble over, and it proposes
NOTHING ELSE: no unit is judged, no wording is changed, and the essay is not
touched.

Propose a term only if ALL of these hold:

- it appears in THE ESSAY, verbatim, in the form you propose;
- it is NOT already in PRONUNCIATION DICTIONARY — those are settled, and
  restating one is refused;
- a careful reader who did not know this book would hesitate over it.

HARD TERMS FOUND IN THIS ESSAY is computed, not judged: every term listed there
needs a pronunciation and none of them is a matter of opinion. It is a floor,
not a ceiling — a borrowed phrase, a proper name in a quotation, or a coinage
you met in the prose belongs here too even when the list does not name it. An
ordinary English word doing duty as a term of art does not: nobody needs to be
told how to say "Field".

Give the pronunciation as plain respelling — the way you would tell a narrator,
with the stressed syllable in capitals — never as IPA. The author reads this
table, and a notation they cannot check is a notation they cannot rule on.

Return JSON only, in this shape and nothing else:

    {"pronunciations": [{"term": "<the term, copied from the essay>",
                         "say": "<plain respelling, e.g. uh-NUT-taa>",
                         "note": "<the language or the one thing worth saying>"}]}

Every entry becomes a question for the author, one at a time, and a term you
propose is a term you will never be asked about again — so propose the ones you
would actually stumble over, and leave the rest.
