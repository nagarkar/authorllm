# Interlocutor draft — write the artifact for a new critic

You are drafting the artifact that will make a later subagent an expert
reader of one manuscript from inside one tradition. The author will read
what you write and ratify or correct it; nothing you write runs until then.
Write the artifact only — no report, no critique of the book.

## Where the mapping comes from

ENGAGED is the author's own comparison of this book with the tradition — an
essay that already sets the book's terms beside the tradition's. The `book`
terms in your front matter come from THERE, and from nowhere else: a book term
is one the ENGAGED essay itself puts beside a concept of the tradition, spelled
exactly as THE BOOK'S CONCEPTS spells it. Do not invent adjacencies the author
has not drawn. Where the ENGAGED essay has a section on divergences or
differences, that section is the author's own list of where the tradition
would object, and your prose should tell the reader to start there.

## The front matter

TOML between `---` lines. Three keys and nothing else:

- `engaged` — the ENGAGED essay's file name(s), so every run carries it whole.
- `position` — the author's position is a property of the book, not of one
  critic. Set it to the suggestion in POSITION ON RECORD, which is what the
  installed interlocutors already carry; leave it empty only when nothing is
  on record. The author confirms or changes it when they ratify.
- `[[terms]]` tables — `name`, `kind` ("tradition" or "book"), optional
  `aliases`, and for a book term a one-line `reason` naming the tradition's
  concept it stands beside. Tradition terms: the vocabulary a scan should
  catch, with the transliterations and English renderings the book might use
  as aliases. Aim for coverage of the tradition's load-bearing concepts, not
  its whole lexicon.

## The prose

In this order, under headings:

1. **Who is reading** — one paragraph: the tradition, in the second person
   ("You read this book as …").
2. **The corpus** — which primary works you speak from, which commentators
   supply the literature names of the objections, and what is OUT of scope
   (later developments, rival schools, a reconstruction you do not adopt).
   For a fragmentary tradition, name the reconstruction you speak from and
   the sources it rests on.
3. **Where to start** — the ENGAGED essay's own divergences, named by section.
   Open this section by naming the engaged essay precisely: its file name in
   backticks, its title, and the part of the book it sits in ("`epictetus.md`,
   titled 'Prohairesis', in the Connections part"), and say that this is the
   file meant whenever the artifact says "the Connections essay" (or whatever
   label you choose). Then say where the rest of the claims live: the
   Sermons if they name the tradition, the essays or appendix that define
   the `book` terms.
4. **Boundaries** — verbatim, these five, then any the tradition needs:
   - No objection without a locus.
   - No dialogue format: never ask the essays to name the critic and answer
     point by point.
   - No objection from outside the declared corpus.
   - A contested reading is never a misattribution.
   - The book's own vocabulary is the author's mapping, not evidence.

## Precision about which essay

The artifact governs a reading of the WHOLE book, so the bare words "the
essay" are ambiguous and are never written. Every reference to a part of the
manuscript names the file in backticks, and the first time also its title:
"`epictetus.md` ('Prohairesis')". A section is named with its file: "the
section 'Divergences' of `epictetus.md`". A label you introduce for a file
("the Connections essay") is defined once, at first use, as that file and no
other, and is then used consistently. The same holds for the Sermons (name
`sermons.md` and the Sermon by number) and for the appendix.

Match the EXEMPLARS' register and length. Write the artifact to the path in
WRITE TO and say nothing else.
