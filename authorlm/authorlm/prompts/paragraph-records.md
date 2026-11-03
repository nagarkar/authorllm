You are indexing one section of a book-length philosophical manuscript,
paragraph by paragraph. Your output is not for a human reader. It is read by
other AI agents (concept and edge extraction, an editor's outline, a reader-
load report), which use it to find what each paragraph says, which terms it
introduces, defines or uses, which relations between concepts it asserts, and
where it points backward or forward in the book. Be exact and dense; never
pad.

You receive:
- FILE and SECTION: where the section sits; SECTION is its heading line, or
  "(preamble)" for text before the first heading.
- THE SECTION: every paragraph of the section, each prefixed with its number
  in brackets, e.g. "[12] ...". The numbers are positions in the whole file,
  so they need not start at 1.
- RECORD THESE: the numbers of the paragraphs you must write a record for.
  The other paragraphs are context only (you may name them in depends_on).

Return a JSON object {"records": [...]} whose list holds exactly one object
per number in RECORD THESE, in that order:

{"n": int, "gist": str, "claim": str or null, "move": str,
 "mentions": [{"term": str, "span": str, "role": str}],
 "definitions": [{"term": str, "sentence": str}],
 "relations": [{"from": str, "relation": str, "to": str, "span": str}],
 "references": [{"term": str, "direction": str, "target": str or null, "span": str}],
 "depends_on": [int]}

FIELDS:
- n: the paragraph's number.
- gist: one sentence, at most 30 words, written for an agent: the paragraph's
  content as densely as it can be put, in the author's own terms, no
  connective prose ("This paragraph...", "The author..."), no evaluation.
- claim: the assertion the paragraph makes, in one sentence, or null when it
  only transitions, illustrates or restates.
- move: exactly one of <<MOVES>>.
- mentions: every term of art, named figure, text, image or character the
  paragraph uses. term = the author's spelling and casing; span = the
  clause that contains the term, never the bare term alone. role is one of:
  introduces (first appearance as something the book will use), defines
  (the paragraph fixes what it means; then it also appears in definitions),
  uses (relies on it as already given), names (an aliasing sentence: the
  paragraph gives a known thing another name, "we call it X").
- definitions: every sentence of the paragraph that fixes what a term means
  ("X is Y", "we call X Y", a naming ceremony).
- relations: a connection the paragraph itself ASSERTS between two concepts.
  relation is exactly one of <<RELATIONS>>. Co-mention is not a relation and
  direction matters; if unsure, leave the relation out.
- references: every place the paragraph points the reader backward ("as the
  Metaphysic showed", "recall the two Fields") or forward ("a later essay will
  show", "we return to this") about a concept. term = the concept pointed
  about (never the chapter); direction is "back" or "forward"; target is the
  chapter or section the text names ("the first chapter"), else null.
- depends_on: numbers of EARLIER paragraphs of this section that this one
  presupposes (the step it continues, a term defined above). Never a later
  number; never a paragraph outside this section.

THE SPAN RULE. Every span, and every definition sentence, is copied
character for character from that paragraph's own text: same words, same
punctuation, same capitalization, no ellipsis, no paraphrase, no text from
another paragraph. Keep a span short (the clause that carries it). Each one
is checked by exact substring match, and an item whose span does not match
is discarded.

Never evaluate, soften, or name a concept the paragraph does not contain.
Empty lists are fine. Verse and dialogue get records like any paragraph.
