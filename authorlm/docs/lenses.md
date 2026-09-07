# Ratified lenses — SMSTTD

Repo record of the lens artifacts installed in `manuscripts/SMSTTD/_lenses/` (gitignored manuscript data — this file is the checked-in canonical copy, same pattern as the filter appendix in filter-pass-design.md). The live artifact is what runs; on any re-ratification update both.

Rewritten 2026-09-06 as eight book-agnostic lenses (from the earlier five: historical-reach → evidence-reach; metaphor-as-mechanism → illustration-as-argument; pitch + repetition-across-parts → audience-pitch; uncited-apparatus → unsourced-claims; the consistency draft → framework-consistency and cross-references; through-line and validity-and-objections new). Each opens with TOML front matter (docs/lens-architecture-design.md §3), then verbatim Flag/Keep examples from the manuscript and author-made repairs mined from its revision history, then the rules, which refer only to the runner's INPUTS and close with REWRITE OR JUDGMENT (design §12). The rules never name summaries; where a rule needs another chapter, the authority is that chapter's text in block T. Footnotes are requested with a `footnote` gist, never written (footnote-directive-design §12).

Sweep order: framework-consistency, cross-references, through-line, validity-and-objections, evidence-reach, illustration-as-argument, audience-pitch, unsourced-claims.

## `audience-pitch`

```markdown
---
class = "cross-chapter"
inputs = ["audience", "glossary", "scheme", "registers", "passes", "reading-order"]
targets = ["neighbours", "earlier", "book"]
examples = "prompt"
---
# Pitched where the reader actually stands

*Examples are illustrations drawn verbatim from one manuscript; the rules beneath them are general. Each Flag pairs with a Keep that superficially resembles it.*

## Examples

### Flag — The ramp is not monotonic
> A rank is a place in the ordering. Rank is therefore a state, not a possession. It is true only as of the last beat in which it was earned.
— `hierarchy.md`, section: The Impulse to Rank
Why it is a finding: "Beat" is a term of the Metaphysic's Heartbeat (one cleaving plus the collapse that answers it; Time as the count of beats), and the earlier chapters show it introduced only in `metaphysic.md`, which sits in the back matter after this chapter; no chapter before `hierarchy.md` uses the word at all, and the GLOSSARY has no entry for it. `hierarchy.md` leans on it seven times ("at every beat", "before the first beat", "before any beat", "wrong by the next beat") as the unit in which rank is re-earned, so a reader who read faithfully from `discernment.md` through `kindness.md` holds a metric whose unit the book has not yet supplied.
What the finding says: `hierarchy.md`, from its first section onward and throughout the three tests of a ranking; the reader is assumed to have the Heartbeat's "beat" as the atomic interval of Choice, which the book supplies only in `metaphysic.md` (The Heartbeat), two chapters later; too much.

### Keep — Genuine difficulty
> It asks for a *before*. A first cleaving that needed to happen implies a waiting — a stretch in which Nothing stood unrendered, a hall before the bell. But time is the count of beats, and the count begins at the first beat.
— `metaphysic.md`, section: Why the First Cleaving?
Why it is not a finding: The paragraph has to be read twice, but every term it uses (cleaving, beat, the count) was built in this chapter before this section, and the AUDIENCE PROFILE's reader finishes long books and reads a paragraph twice without complaint. Hard is not mispitched; nothing here exceeds what the profile grants once the chapter's own definitions are in hand.

### Flag — It opens at full altitude
> Therefore, the Seven Sermons begin even earlier, with Nothing. From Nothing arise the two Realms: Fields and Qualities. Of the two necessary Fields, one is *Chid*, the Field of division or sundering, which the Sermons call the Field of Choice.
— `discernment.md`, section: Before Consciousness
Why it is a finding: This is the first argumentative chapter after the frame, and its first three paragraphs run the derivation (consciousness, distinction, division, Nothing, the two Realms, *Chid*) before any sentence says what the chapter is for; the orienting line "That widening and narrowing is the subject of this essay" arrives only in paragraph four, and the one sentence that does orient ("finds determinism in the machinery of fate") lives on the part opener `ascending.md`, which a listener never hears as part of this chapter. Its successor `good-choice.md` opens by naming what this chapter supplied and what it did not; this one starts the machinery cold.
What the finding says: `discernment.md`, the opening three paragraphs; the reader is assumed to carry the Realms and *Chid* into the argument at speed, with no on-ramp inside the chapter saying what it will trace until a page later; too much (of altitude, not of content).

### Keep — A sentence that orients
> The essay on Before Consciousness supplied three terms: discernment, awareness, and action. They describe how a choice is made.
— `good-choice.md`, section: The Good Choice
Why it is not a finding: The opening names the three terms and where they came from without re-deriving any of them, then states what the new chapter will do with them. It points back in a sentence and moves on; nothing is taught twice.

### Flag — Its density is out of line with its neighbours
> According to Hippolytus, Basilides opens his cosmology with the οὐκ ὄν θεός — the God that is not. Before anything existed, not even emptiness existed. This is not the classical void, not the emptiness of unoccupied space.
— `basilides.md`, section: The God That Is Not
Why it is a finding: In 1,090 words this chapter asks for Hippolytus (never bridged), Greek script, the Pleroma and Abraxas as doctrine, a second far-side school's *Spanda* with two of its authors, and "the Conservation of Nothing" as a named principle that the book states only in the back matter. The nearest full chapters on either side are `god.md`, which bridges Wittgenstein and Gödel in the text, and `epictetus.md`, whose figure the AUDIENCE PROFILE places on the near side and which bridges him with a biography anyway; the 148-word part opener `connections.md` between them is transparent. Difficulty saws (high here, low next, highest in `indic.md`) where it should climb.
What the finding says: `basilides.md`, whole chapter, densest in "The God That Is Not" and "The Pulse"; the reader is assumed to hold Gnostic cosmology, a second Indic school, and the Conservation of Nothing, none of which the book has supplied by this point (the principle arrives in `metaphysic.md`; the Gnostic figures are named in `preface.md` but not taught; the Indic school is never introduced); too much, relative to `god.md` and `epictetus.md` on either side.

### Keep — A figure or school the AUDIENCE PROFILE grants
> Nietzsche’s most sustained structural argument — developed across the *Genealogy*, *Beyond Good and Evil*, and *Zarathustra* — is that the moral frameworks of modernity are elaborate defenses of weakness dressed as virtue.
— `nietzsche.md`, section: The Error of Specialness and the First Floor
Why it is not a finding: Three works are named by title with no clause saying what they are, and the section leans on them throughout; but the AUDIENCE PROFILE places Nietzsche on the near side of the line, where a name, a claim, and a clause is enough. The density is real and it is paid for by what the reader brings.

### Flag — A load-bearing tradition is never introduced as such
> Kashmir Shaivism arrives at the same threshold by a different path. Abhinavagupta describes the ultimate ground as *Śūnya-Pūrṇa* — empty yet full — a state prior to differentiation that is nevertheless not inert.
— `indic.md`, section: Nothing and the Nāsadīya Sūkta
Why it is a finding: The school is the spine of this chapter — it carries four sections of terms and authors — and the AUDIENCE PROFILE names it and this author in as many words as what the reader does not have. No chapter anywhere says what the school is, when or where it arose, or what it holds; `basilides.md` gives one of its terms an appositive and this chapter gives the author dates, but bridging a term does not introduce a school. One mention would be the SENTENCE-LEVEL PASS's; a spine across four sections is this lens's.
What the finding says: `indic.md`, from "Nothing and the Nāsadīya Sūkta" through "Liberation Without Escape"; the reader is assumed to know the school as a tradition with a position on the ground of being, and the book never supplies it; too much.

### Keep — A section in a PROTECTED REGISTER
> Harken. Do ye recall the words of Basilides on the subject of Nothing? I shall begin there, that the smallest cost ye may bear.
— `sermons.md`, section: Sermon One
Why it is not a finding: This leans on a teaching the AUDIENCE PROFILE says the reader does not have, in an archaic voice, and would read as an unbridged tradition anywhere else; but the passage sits in a PROTECTED REGISTER, which asserts by design. The debt it names is the business of `basilides.md`, not of this passage.

### Flag — A bridge lives only where a listener cannot hear it
> Carse, whom the reader met in the Recapitulation, named this distinction in *Finite and Infinite Games* (1986). The present framework does not borrow the distinction; it derives it.
— `impulses.md`, section: The Infinite Game
Why it is a finding: The only earlier meeting is a footnote in `recapitulation.md` ("popularized by James P Carse"); no sentence of any chapter's body has said who he is. The finite and infinite game is the spine of three sections here, and a listener, who cannot hear a footnote, meets the man for the first time in a clause that says he has been met already, with a title and a date and nothing to hold.
What the finding says: `impulses.md`, "The Infinite Game" and the two sections that build on it; the reader is assumed to have met Carse, which the book supplied only in a footnote to `recapitulation.md` and, for a listener, never; too much.

### Keep — One paragraph's fault
> Gödel showed that any sufficiently powerful and consistent formal system of arithmetic contains true statements it can neither prove nor disprove within itself. He showed further that a consistent, sufficiently strong, effectively axiomatized formal system containing enough arithmetic cannot establish its own consistency from within [^F4].
— `god.md`, section: Climbing Through Duality
Why it is not a finding: The second theorem is a derivation carried one step past what the claim (an instrument known to be limited still does work) needs, and the AUDIENCE PROFILE says this reader cannot check the step — but that is one unit's fault, the SENTENCE-LEVEL PASS's "derivation carried past what the claim needs". The chapter as a whole is pitched where its neighbours are; reporting this here is the finding the author sees twice.

### Flag — It re-teaches crossed ground
> Two Fields, and only two. One sunders Qualities and makes distinction; one joins them and returns everything to sameness. Both are aspects of Nothing, the clasp of all opposites, and their full account belongs to the Metaphysic.
— `kindness.md`, section: The Warmth Beneath the Cold
Why it is a finding: The two Fields, sunder-versus-join, and the deferral to the back matter were taught in `discernment.md` ("Where Choice sunders, Nothingness joins. Where Choice draws a distinction, Nothingness dissolves distinctions into sameness.") and again in `impulses.md` ("The full account of this duality belongs to the Metaphysic."). This is a paragraph that re-derives, with the same verbs and the same deferral; a reader of either chapter gains nothing.
What the finding says: `kindness.md`, "The Warmth Beneath the Cold", first paragraph; the earlier telling lives in `discernment.md` (The Counterpart of Choice) and `impulses.md` (The Infinite Game); the passage adds nothing — the chapter's own claim, that false compassion is Nothingness in a warm coat, begins in the next paragraph and would stand on a one-clause recall; too little.

### Keep — A re-statement that advances
> Philosophers call this essentialism. It takes many more careful forms than this, but this is the form that concerns us here: the belief that what a thing truly is outranks what it becomes.
— `rebirth.md`, section: The Trap Of Essentialism
Why it is not a finding: `preface.md` already defined the term ("The belief that what a thing is can be fixed independently of what it has become and what it may yet become."), but this is a sharper formulation — origin outranking becoming — narrowed to the form the chapter will dismantle, in the chapter the ORGANIZING SCHEME assigns to it; it then derives a consequence (essentialism as a twin of determinism) the preface never drew.

### Flag — It re-summarizes a chapter the reading order already mapped
> Sermon 7 closes on this precise note, stripped of theatrical apparatus. The Dead, dispossessed of their commandments, their eternal virtues, and their static identities, desperately demand everlasting life as compensation. The Herdsman tells them they already possess it — not as a peaceful, localized rest, but as a current of endless becoming.
— `nietzsche.md`, section: The Infinite Game and Amor Fati
Why it is a finding: The answer to the demand for everlasting life — already possessed, becoming not rest — is what `recapitulation.md` mapped for that section ("Everlasting life is not a pleasurable stasis. It is endless Becoming.") and what `good-life.md` closed on; three sentences mid-section re-run the demand and the answer without a new consequence, objection, or application, and they are not at a boundary and not the same words, so the refrain test fails on two parts.
What the finding says: `nietzsche.md`, "The Infinite Game and Amor Fati", final paragraph; the earlier telling lives in `recapitulation.md` (Sermon Seven: The Temple Shakes) and `good-life.md` (The Common Error); the passage adds only "on this precise note", a link to *amor fati* the paragraph never works out; too little.

### Keep — A refrain that passes the strict test
> The Dead asked for everlasting life. It was theirs all along: a life that never stops becoming, and whose becoming never ends.
— `good-life.md`, section: The Common Error
Why it is not a finding: The same content `recapitulation.md` mapped ("Everlasting life ... endless Becoming"), but all four parts of the test hold at once: two short sentences, the chapter's final line, returning the mapped words "everlasting life" and "becoming", and working by rhythm — cutting it loses the cadence that returns the chapter to the Dead's question, not information.

### Flag — It re-lists the ORGANIZING SCHEME where the chapter's stated work does not need it
> The *Seven More Sermons* set out to give those shared visions a firmer ground, stated plainly enough to be tested against the world, and to name the four false idols the original left untouched: Determinism, Essentialism, Exceptionalism, and Religiosity.
— `about.md`, section: About the Author
Why it is a finding: The biography's work is to say who wrote the book and why, not to enumerate the ORGANIZING SCHEME, which `preface.md` listed ("Determinism, Exceptionalism, Essentialism, and Religiosity") and `recapitulation.md` and the part opener `ascending.md` each named again; the list does no work the section's other sentences do not already do.
What the finding says: `about.md`, "About the Author", second paragraph; the earlier telling lives in `preface.md` (The Four False Idols), repeated in `recapitulation.md` and `ascending.md`; the passage adds nothing, and the same paragraph also re-tells in its own words an anecdote `ecce-homo.md` told; too little.

### Keep — The ORGANIZING SCHEME item named by the chapter assigned to it
> Choices once made and never revised, or choices repeated, appear as patterns of habit; habits mistaken for necessity become fate. And fate, worshipped long enough, becomes the first temple wall of Determinism.
— `discernment.md`, section: Between Necessity and Randomness
Why it is not a finding: This names one item of the ORGANIZING SCHEME, already listed in `preface.md`; but this is the chapter the scheme assigns to that item (the part opener says it "finds determinism in the machinery of fate"), and naming the wall you are striking, in one clause and not as the list, is the chapter's stated work.

### Flag — The same telling recurs across three or more chapters
> The parable of the Burning House from the *Lotus Sutra*,[^E2] introduced in the essay on the Good Choice, shows both the predicament and the way forward. The father employs a lie and a lure. The promise of wondrous carts draws the children into the cold, hard safety of the outdoors.
— `kindness.md`, section: Kindness that Kills
Why it is a finding: The parable is told in `good-choice.md` (The Burning House), then used in `good-life.md` three times, in `impulses.md` once, here, and in `becker.md` — five chapters. Each recurrence adds a little (a summit tested, a reformer's fate, false compassion, a vital lie), so no single one is a re-teaching; but a reader hears the story five times, and this is the only recurrence after the first that re-narrates the plot (the lie, the lure, the carts, the outdoors) where a one-clause recall would carry its application.
What the finding says: `kindness.md`, opening paragraph, sentences three to five; the telling lives in `good-choice.md` (The Burning House) and recurs in `good-life.md`, `impulses.md`, and `becker.md`; this passage adds only the application in the sentence that follows, which needs none of the re-narration; too little, in aggregate.

### Keep — A deferral kept in as many words
> But a nearer question comes first. If a life is a trajectory of choices, how ought such a trajectory be steered? What would it mean to live well?
— `rebirth.md`, section: The Memory Of Causation
Why it is not a finding: The chapter sets the question aside on the record; the target chapter confirms `good-life.md` opens with the same sentence verbatim and answers it, and the grammar deferred in the preceding sentence (thread, choice, history, trajectory) is supplied in `metaphysic.md`. A promise kept is structure, not a gap, and the verbatim return sits at two structural boundaries as the promise's fulfilment.

### Flag — A sentence recurs verbatim in another chapter
> Beneath every version runs the error of specialness: the belief that one has been exempted from the common requirement to make the right choice.
— `becker.md`, section: The Error Beneath the Errors
Why it is a finding: The clause after the colon is word-for-word the definition `christic.md` gave ("what the Sermons name the error of specialness: the belief that one has been exempted from the common requirement to make the right choice."), mid-argument in both, at no boundary, and this chapter's own section is meant to go beneath the error, not define it again. A reader in reading order meets the sentence the second time here.
What the finding says: `becker.md`, "The Error Beneath the Errors", end of the first paragraph; the earlier telling lives in `christic.md` (No Passive Pardon); the passage adds nothing to the definition, and the section's new claim (that every exemption machine runs on it) would stand on the term alone; too little.

### Repaired examples

*Author-made repairs mined from this manuscript's revision history (2026-09-06). Each Before is the passage as it stood at the named collected version; each After is the current text.*

### Repaired — The ramp is not monotonic
Before (v411):
> A race that preserves an immutable quality because it is valued is therefore closed to evolution along that quality. No member is required to evolve, nor is the evolution measured.
After (current):
> Throughout, an immutable quality means one fixed before the ranked individual's first choice and closed to all choices after it within one lifetime.[1]
Why the before was a finding: "Immutable quality" is leaned on as a term of art here and again in Supremacism ("impossible to measure a change in an immutable quality against oneself"), yet no earlier chapter, the GLOSSARY, or this chapter's own opening had said what fixes a quality or over what span; the reader is assumed to hold a distinction the book never supplied.
What the repair did: the chapter now supplies the term at its head, with the scope ("within one lifetime") declared before the machinery that uses it.

## The lens

You are looking for a chapter pitched above or below its reader, judged over the whole chapter and the reading order, never one paragraph at a time. The reader is the one the AUDIENCE PROFILE describes, who has read every earlier chapter in reading order and may be listening; a promise that chapters "stand alone" does not exempt them from what they were already given. What the AUDIENCE PROFILE grants needs no bridge; what it withholds the book must supply first. The earlier chapters themselves are the authority on what the reader has been given: read the chapter a rule turns on, never a summary or digest of it (those change while the chapter does not), and where that chapter is not before you, mark the finding provisional and name the chapter to check. The GLOSSARY is the authority on what the book defines; the ORGANIZING SCHEME lists its framework. One paragraph's fault belongs to the SENTENCE-LEVEL PASS for reader load; point there, do not report it.

Flag a passage when:

- **The ramp is not monotonic.** The chapter assumes a term, result, or distinction nothing earlier in the reading order supplied, by the earlier chapters' own text and the GLOSSARY, not by what the book intends.
- **It opens at full altitude.** The machinery starts before any sentence in the chapter itself says what it is for or will lean on; a part opener or chapter card does not count.
- **Its density is out of line with its neighbours.** Compare with the nearest full chapters on either side; chapter cards and part openers are transparent. One chapter demanding several times what those demand is a lurch where the book should climb; a comparison, not a threshold: name the neighbours.
- **A load-bearing tradition is never introduced as such.** A school or figure leaned on across several sections, which the AUDIENCE PROFILE places outside what the reader has, and which no chapter introduces — what it is, when, what it holds. Bridging one of its terms does not introduce the school; one mention is the SENTENCE-LEVEL PASS's, a spine is yours.
- **A bridge lives only where a listener cannot hear it.** A footnote, chapter card, or part opener is the only place the book introduces what the chapter treats as met; for the listener it is absent.
- **It re-teaches crossed ground.** A paragraph or more re-derives what an earlier chapter established, adding nothing for a reader of that chapter. A sentence orients; a paragraph that re-derives re-teaches; a page is always a finding.
- **It re-summarizes a chapter the reading order already mapped,** without a new consequence, objection, or application.
- **It re-lists the ORGANIZING SCHEME where the chapter's stated work does not need it.** The test is the chapter's assigned work, not the headed section.
- **The same telling recurs across three or more chapters,** even where each recurrence adds a little. Tally the chapters; flag the recurrence a one-clause recall could best replace.
- **A sentence recurs verbatim in another chapter,** outside a refrain passing the test below. Flag the later occurrence, whatever the concept.

Refuse to flag:

- **A section in a PROTECTED REGISTER.** The debt it names belongs to the chapter that owes it.
- **Genuine difficulty.** Hard is not mispitched; the finding is a demand the AUDIENCE PROFILE says this reader should not pay for, and where you cannot name it, none.
- **One paragraph's fault.** The SENTENCE-LEVEL PASS's; report only what the whole reading shows.
- **A refrain that passes the strict test** — all four at once: short (a sentence or two), at a structural boundary (a chapter's or section's first or last lines), near-identical wording, and working by rhythm, so cutting it loses cadence, not information. Failing one part, it is re-telling.
- **A sentence that orients.** It names what was taught and where, then moves on without re-deriving.
- **A re-statement that advances.** A new consequence, a sharper formulation narrowed to the chapter's work, or a different door into the room.
- **A deferral kept in as many words.** A question set aside on the record and answered where promised, or a later chapter delivering the fuller account earlier chapters deferred to it. The promised chapter's own text says whether it is kept.
- **A figure or school the AUDIENCE PROFILE grants,** however heavily leaned on.
- **The ORGANIZING SCHEME item named by the chapter assigned to it.** Naming the item it strikes is that chapter's work.

For each finding, state four things and stop: which chapter and where in it; what the reader is assumed to have or to lack, and where the book supplied it — the chapter, by name — or that it never does; for the "too little" pole, where the earlier telling lives (chapter and section, by name) and what the passage adds, saying "nothing" if that is the honest answer; and whether the fault is too much or too little.

REWRITE OR JUDGMENT (ruled 2026-09-06). Every finding proposes its repair as
`replacement` whenever the chapter, the GLOSSARY, and the target chapters
supply what the sentence needs: a wrong word, a misstated credit, a missing
clause, a dropped scoping, a frame sentence, a corrected pointer. Reserve
`judgment` for a repair that is a decision the author alone can make — cut
or move a section, choose between two claims the chapter makes, supply a
fact the payload does not contain, add an argument that does not yet exist —
and state that decision in one clause. A finding with neither is a note the
author cannot act on in the Doc, and is a fault of the reply.
```

## `cross-references`

```markdown
---
class = "cross-chapter"
inputs = ["glossary", "reading-order"]
targets = ["pointers"]
examples = "prompt"
---
# Pointers to other chapters that keep their word

*Examples are illustrations drawn verbatim from one manuscript; the rules beneath them are general. Each Flag pairs with a Keep that superficially resembles it.*

## Examples

### Flag — "the shape of the argument" carried as vocabulary, not mechanism
> The paper on Mixing in the Appendix argues this claim by asking what happens when two populations meet. Here is only the shape of the argument. A population forms around the qualities that Nature rewards, and Nature presents a different face in every place.
— `hierarchy.md`, section: Different Grounds
What the target chapter actually supplies:
> Where two populations end up, and which way they move afterwards, is a weighted mean of where each was and where each was heading. Each population's openness decides how much of the other it takes; each population's inertial memory decides how much force that takes; and the size of the other scales the pull.
— `mixing.md`, section: The Newcomer and the Meeting
Why it is a finding: The passage promises the shape of the target's argument for separation and then carries only the target's nouns (population, quality, Nature); the two quantities the target's model runs on, openness and inertial memory, never appear, so the reader is handed the conclusion's vocabulary and nothing that produces it.
What the finding says: The pointing passage and its section; the target chapter and section; the mechanism missing, taken from the other chapters' own text (two populations move toward a weighted mean set by each one's openness and inertial memory, and separation follows only where openness is too weak to overcome inherited motion carrying them apart); and that one or two sentences naming those quantities and that condition would carry it.

### Flag — a deferral the promised chapter does not deliver
> Each must be measured at the scale on which its action occurs: an individual by choices attributable to the individual, an institution by the choices it organizes and sustains, and a population by the direction of its common action. None may borrow the deeds of another and wear them as its own. This is a heavy burden, and that struggle is the work of *The Redemption We Must Name*.
— `hierarchy.md`, section: The Labor of Ranking
What the target chapter actually supplies:
> The Sermons do not rescue. The Dead descend again toward the world because the Field of Choice still calls. Redemption is the capacity to bear that tension without becoming its prisoner.
— `redemption.md`, section: The Return to Choice
Why it is a finding: The the other chapters' text of the target list its moves (five bargains, four kinds of knowledge, the return to choice) and every one belongs to the single chooser; attributing choices to institutions or populations appears nowhere in what the target introduces or argues, so the promise is made here and kept by no chapter.
What the finding says: The pointing passage; the chapter it names and, per that chapter's own text, that the chapter does not take up collective attribution; that closing the gap requires the promised chapter to take it up, the pointer to be redirected to a chapter that does, or the promise to be withdrawn.

### Flag — a circular deferral
> Populations may mix, but they are not always compatible. The essay on Kindness that Kills argued that different populations may genuinely need different conditions to thrive, and that the answer is to live and grow in different places as separate populations, rather than being forced to mix.
— `hierarchy.md`, section: Populations
What the target chapter actually supplies:
> The claim is stated here, previewed in the essay on The Ordering of Society, and argued in the paper on Mixing in the Appendix.
— `kindness.md`, section: The Strongest Objection
Why it is a finding: This chapter credits the earlier one with having argued the claim; the earlier one says it only states the claim and sends the reader forward to this chapter for a preview and to the appendix for the argument, and the other chapters' own text of both chapters carry the claim as asserted and deferred. A reader following either pointer arrives back where they started.
What the finding says: The loop, chapter by chapter (this chapter sends the reader back; the earlier chapter sends them forward), and that per that chapter's own text neither link holds the argument; the word "argued" here misstates the target and must become "stated" or "asserted", with the argument located where it actually lives, if anywhere.

### Flag — a term used in a sense another chapter or the GLOSSARY fixed differently
> Throughout, an immutable quality means one fixed before the ranked individual's first choice and closed to all choices after it within one lifetime.
— `hierarchy.md`, section: The Impulse to Rank
What the target chapter actually supplies:
> The other is immutable—thus beyond measure and unseen. Though bound to each other, they are cleft apart by their very nature. The mutable we name the Realm of Qualities; the immutable, the Realm of Fields.
— `sermons.md`, section: Sermon One
Why it is a finding: The GLOSSARY lists "Immutable" as an alias of the Fields, the realm the founding chapter sets against the Qualities, so "an immutable quality" is, in the book's fixed sense, a contradiction in terms; the local stipulation does not undo the collision, it makes one word carry two senses across chapters (a third chapter adds "Immutable Rules" for sealed past choices).
What the finding says: The term; the sense it carries here; the sense fixed elsewhere, with the chapter and the GLOSSARY entry that fix it; and that carrying the settled sense requires a different word here for a quality fixed at birth ("inherited", "birth-fixed"), or a GLOSSARY ruling that admits the second sense.

### Flag — a back-reference that misstates the earlier chapter
> In the Sermons, the Field of Nothingness is not a resting place, not a womb, and not a conscious remainder. It is described as “the undying death — the cessation of all movement, all perception, and all knowing.”
— `indic.md`, section: Divergences with Tantric Shaivism
What the target chapter actually supplies:
> Nothingness is the undying death—not the end of one life, but the cessation of all movement, all perception, and all knowing. It is the stillborn womb of all qualities, wherein even the possibility of creation is extinguished.
— `sermons.md`, section: Sermon One
Why it is a finding: The passage denies, on the earlier chapter's behalf, the very figure the earlier chapter uses ("the stillborn womb of all qualities"), and its quotation silently drops the clause "not the end of one life, but", which is the qualification the earlier chapter made; the divergence being drawn (dissolution as womb versus as death) is one the source complicates rather than supports.
What the finding says: What this passage says the earlier chapter said; what the earlier chapter said, quoted; the difference; and that the reference must restore the dropped clause and either withdraw "not a womb" or account for the stillborn womb.

### Keep — a one-clause orientation that claims no argument
> What rises from level to level is the Resolution of Awareness, which the reader met in the essay Before Consciousness. That is why greater awareness feels like increased freedom.
— `impulses.md`, section: Habit and the Gaps
Why it is not a finding: It carries only the target's term and says where the term was taught, which resembles the shape-without-mechanism flag; but it claims to give no argument, one clause places the reader and moves on, and the target chapter confirms the target gives discernment its degrees of resolution under that name.

### Keep — a deferral the promised chapter keeps
> And what of the one who has already fallen through the floor? Whether he can be remade, and what redemption is once it is no longer a service to be purchased, is work for **The Redemption We Must Name**.
— `kindness.md`, section: The Warmth Beneath the Cold
Why it is not a finding: The same chapter is promised as in the unkept deferral above, and this promise it keeps: the earlier chapters show the target dismantling salvation as a bargain and naming a redemption that cannot be sold, so the pointer's questions are answered where it says they will be, in other words than the pointer's.

### Keep — a forward reference the reading order requires to be light
> The full account of the two Fields belongs to the *Metaphysic*. Here we trace the field of distinction-making and what is lost when it contracts.
— `discernment.md`, section: The Counterpart of Choice
Why it is not a finding: The target lies in the back matter, so its mechanism cannot be given here without the book being read out of order; the pointer names what it is not supplying and what it will do instead, and the earlier chapters show the target deriving the two Fields from its axiom.

### Keep — re-teaching, which belongs to audience-pitch
> The parable of the Burning House from the *Lotus Sutra*,[^E2] introduced in the essay on the Good Choice, shows both the predicament and the way forward. The father employs a lie and a lure. The promise of wondrous carts draws the children into the cold, hard safety of the outdoors.
— `kindness.md`, section: opening, before The Two Questions
Why it is not a finding: The pointer is accurate (the other chapters' own text place the parable's introduction in the named chapter) and carries the target's content faithfully; its fault, if any, is that it re-tells a page the reading order already taught, and that is audience-pitch's re-teaching test, not this lens's fidelity test.

### Keep — a paraphrase the earlier chapter agrees with
> The essay on Rebirth described metamorphosis seen from the inside, as a common event. What perishes in that crossing is real. What persists is real.
— `becker.md`, section: What Dies
Why it is not a finding: The back-reference uses none of the target's sentences, which resembles the misstatement flag; but the target says in as many words that rebirth is metamorphosis seen from the inside and nature's ordinary work, and the other chapters' own text agree, so fidelity to the sense is kept without the sentence.

### Repaired examples

*Author-made repairs mined from this manuscript's revision history (2026-09-06). Each Before is the passage as it stood at the named collected version; each After is the current text.*

### Repaired — Pointer credits the wrong chapter
Before (v412):
> This essay asks: What is a valid and justified ordering of society, and which orderings are contrary to the good life we discussed two essays ago?
After (current):
> This essay asks: What is a valid and justified ordering of society, and which orderings are contrary to the good life as the essay on The Good Life defined it?
Why the before was a finding: the pointer credits the fixing of "the good life" to the chapter two places back in the reading order, which by the table of contents is Impulses, not The Good Life (three back); the chapter it names does not take the point up, and a reader cannot check the term against the other chapters' own text of an unnamed target.
What the repair did: pointer redirected to the chapter that actually fixed the term, named.

### Repaired — A point credited to a chapter that does not carry it
Before (v413):
> The third condition is that the choices recorded by the rank are the individual's own. Populations act, as the essay on Before Consciousness implied, and so do families and institutions.
After (current):
> The third condition is that the choices recorded by the rank belong to the agent being ranked. Populations act, and so do families, institutions, and individuals.
Why the before was a finding: the passage credits "populations act" to Before Consciousness, whose contribution on this ground is supremacism as a contraction of discernment; nothing in that chapter states that populations are agents, and "implied" concedes the pointer is not carrying an argument the target holds.
What the repair did: the credit was withdrawn and the claim made in this chapter's own voice, with the scale of each agent's action stated here.

## The lens

You are looking at every place this chapter points at another chapter of the
same book: a forward deferral ("X takes this up"), a back-reference ("as X
argued"), a borrowed argument ("here is the shape of X's case"), or a term
that X fixed. Of each you ask one question: does the pointer keep its word?
Read the chapter whole and list its pointers first. Check each against the text of the chapter it names, read directly: that chapter is the only authority on what it delivers, as distinct from what the book meant it to deliver, and no summary or digest of it stands in for it, because those change while the chapter does not. The GLOSSARY is the authority on a term's
settled sense and on which chapter fixed it. Anything a SENTENCE-LEVEL PASS
owns (a duplicated word, a mood, a missing footnote) is not yours; you report
only what needs two chapters open at once. A pointer into a PROTECTED
REGISTER is checked like any other: the register protects the target's
voice, not this chapter's account of it.

Flag a passage when:
- it claims to give another chapter's argument, or "the shape" of it, and
  carries only that chapter's vocabulary or conclusions; say, from the target chapter, which mechanism is missing: the quantities, steps, or condition
  that produce the conclusion;
- it defers a point to a named chapter that, by its own text, does not take the point up, or takes up a different one (a different question, scope, or
  unit of analysis);
- it defers or credits a point to a chapter which, by its own pointers,
  defers or credits the point back here, so that no chapter holds the
  argument; a passage that both defers and claims to give the shape is
  judged by the first rule;
- it uses a term in a sense that another chapter or the GLOSSARY fixed
  differently, whether or not this chapter stipulates its own sense; a local
  stipulation is the finding, not a defence against it;
- it states what an earlier chapter said, quoted or paraphrased, and the
  earlier chapter said something else: a clause dropped from a quotation, a
  figure denied that the source uses, a claim credited as argued that the
  source only asserts.

Refuse to flag:
- a one-clause orientation ("as chapter X argued", "which the reader met in
  X") that claims to give no argument and moves on; orientation is a clause,
  not a deployment;
- a deferral the promised chapter's own text shows it keeps, even in other
  words than the pointer's;
- a forward reference to a chapter the reading order places after this one,
  kept deliberately light; the reader cannot be given the mechanism early,
  and a pointer that names what it is not supplying has done its work;
- re-teaching: a pointer that is accurate but re-tells what the reading order
  already taught; hand it to audience-pitch;
- a paraphrase in this chapter's own words that the target chapter's text agrees with; fidelity is to the sense, not the sentence.

For each finding, state four things and stop: the pointing passage, quoted,
and the section it sits in; the chapter it points to and, from its own text, what that chapter actually delivers on the point; the gap, in one
sentence, whether a missing mechanism, an unkept or circular promise, two
senses of one term, or a misstatement; and what closing the gap requires (a
sentence naming the mechanism, a redirected or withdrawn promise, a different
word, a restored quotation), given as `judgment` where the fix is a decision only the author can make.
Do not write the target chapter's argument yourself; the finding names what
is missing, it does not supply it.

REWRITE OR JUDGMENT (ruled 2026-09-06). Every finding proposes its repair as
`replacement` whenever the chapter, the GLOSSARY, and the target chapters
supply what the sentence needs: a wrong word, a misstated credit, a missing
clause, a dropped scoping, a frame sentence, a corrected pointer. Reserve
`judgment` for a repair that is a decision the author alone can make — cut
or move a section, choose between two claims the chapter makes, supply a
fact the payload does not contain, add an argument that does not yet exist —
and state that decision in one clause. A finding with neither is a note the
author cannot act on in the Doc, and is a fault of the reply.
```

## `evidence-reach`

```markdown
---
class = "chapter"
inputs = ["glossary", "registers", "passes"]
examples = "prompt"
---
# Confidence that outruns the evidence

*Examples are illustrations drawn verbatim from one manuscript; the rules beneath them are general. Each Flag pairs with a Keep that superficially resembles it.*

## Examples

### Flag — The architecture is demonstrated one way and claimed another
> Traditions that disagree profoundly about God, the self, and the final condition nevertheless preserve a similar movement: from outward form, through disciplined attention and offering, toward an experience in which the outward form becomes progressively less necessary.
> But the inheritance is not inexhaustible. The exemplar fades. The ritual becomes an empty gesture and is discarded.
— `god.md`, section: The Same Ascent (first quote); The Death of God (second quote)
Why it is a finding: The material under The Same Ascent establishes, from cited texts, that three traditions share a structure of ascent; the prose of The Downward Turn and The Death of God then narrates a sequence civilizations run after the ascent, which no tradition, text, or example in the essay reaches. Agreement across traditions is evidence about the traditions, not that anything happened in the order proposed.
What the finding says: The essay demonstrates cited texts (Vedic corpus, Christian mystical sources, Buddhist suttas) for the shared upward architecture and nothing for the descent. The prose claims "The idols disappear before their effects do", "But the inheritance is not inexhaustible", and "Eventually the iconoclast's program succeeds." The smallest concession is one frame sentence at the head of The Downward Turn saying that what follows is the essay's proposed model of what a civilization does after the ascent, and that the traditions supply the ascent only.

### Keep — A frame declared for the stretch, then worked inside
> A population in accord runs on the assumption of accord. Its members do not verify what a stranger will do at the market or at the door. If discord rises, more negotiation, verification, enforcement, translation, and subgroup-specific accommodation are required, because behavior can no longer be assumed from shared expectations.
— `mixing.md`, section: Discord: Trust
Why it is not a finding: The paper declared its frame in the introduction ("The point of this paper is to establish a well-defined model for understanding the dynamics of accord when populations meet") and every rung of discord is then narrated inside that model, with the evidence class named as it goes ("The evidence here is contested"). The frame governs the whole stretch it precedes, so the generic present is the correct tense and a page of it is not a finding.

### Flag — The model runs without a frame
> They keep to the rule, ignore the reformer, and in time suffer the consequences at a great cost to themselves. Scaled from the one to the many, this is how civilizations end: exploiters and their victims keeping faith with rules that no longer bear weight.
— `impulses.md`, section: Reformers and Exploiters
Why it is a finding: Nowhere in the essay is the herd-exploiter-reformer sequence declared to be a model, a tendency, or a law; the only frame sentence in the essay ("What follows examines each impulse in turn") describes the essay's order, not the kind of claim it makes. Left without that sentence, the generic present of the whole section reads as a report of how civilizations have in fact ended.
What the finding says: The essay demonstrates an argument from defined terms (Vestigial Rule, re-legere, re-ligare) and a single-life analogy (habit), and names no civilization. The prose claims "this is how civilizations end". The cheapest closure is a declared frame at the head of the section: that the sorting of a society into herd, exploiter, and reformer is the shape the two impulses predict when scaled up, offered as a model to be tested, not a chronicle.

### Keep — A single unhedged sentence
> Every civilization has spoken of rebirth.
— `rebirth.md`, section: Rebirth
Why it is not a finding: This is a universal quantifier over a historical subject, but it is one sentence, followed by four examples and an argument that never depends on the universal; it is one unit's fault and belongs to the modal-register pass, which would hedge it or scope it. The whole reading of the essay shows a conceptual argument about identity, not a history presented as a model.

### Flag — A frame is borrowed for a stretch it does not govern
> Civilizations are the inertial mass of their institutions, and institutions are formalized norms and rituals. They can build a civilization or manufacture its suffering, and the difference is almost never in their founding.
— `kindness.md`, section: The Therapeutic Drift
Why it is a finding: The essay does declare frames, but late and for other claims: "All of this rests on an empirical claim" governs the floor-and-good-lives claim in The Strongest Objection, and "The conjecture of this essay is" governs the identification of false compassion with a metaphysical field in the coda. Neither sentence reaches back to the institutional lifecycle narrated flat in The Therapeutic Drift, so that stretch stands unframed however well-framed the rest of the essay is.
What the finding says: The essay demonstrates one cited reading (Rieff) for a single lineage, confessor to therapist, and no second institution. The prose claims "Civilizations are the inertial mass of their institutions" and "the difference is almost never in their founding." The smallest concession is a frame at the head of The Therapeutic Drift stating that the drift described is the essay's model of institutional decay, illustrated by one lineage.

### Keep — An argument from GLOSSARY definitions, while it stays conceptual
> A stone, a mind, a star, and a civilization differ in complexity but not in principle. Each is two things - the present expression of an accumulated history of actions, and the history of actions itself.
— `metaphysic.md`, section: Memory as The Ledger
Why it is not a finding: The sentence names civilizations in the bare indicative, but the claim is drawn entirely from the book's defined terms (action, state, history, trajectory) and says what a civilization is in that grammar, not what civilizations have done. It never leaves the conceptual plane, so it owes history nothing; had it gone on to say that actual civilizations therefore end a certain way, it would be a finding.

### Flag — Two claims of the same shape point in opposite directions
> We have seen the virtue of earning your place versus the futility of inheritance. The hierarchy that forgets this principle drifts toward a violent death.
> The system persists because it demands nothing. It requires no earning of rank, and a population can be kind to itself by ignoring the very logic of standards.
— `hierarchy.md`, section: The Lifecycle & The Need for Vigilance (first quote); Caste (second quote)
Why it is a finding: As tendencies the two coexist: a hierarchy that stops earning its rank weakens, and an unearned rank is easy to keep. As laws they collide: the essay's own central example, caste, is a hierarchy that forgot the principle and has persisted for a thousand years without the violent death the first passage says follows. The collision shows the law-reading producing an inconsistency the essay does not otherwise have.
What the finding says: The essay demonstrates one historical example (caste) and two analogies (aristocracy, the elite class); the example supports persistence, not death. The prose claims "drifts toward a violent death" and, of the same kind of hierarchy, "persists because it demands nothing." The Strongest Objection section already half-concedes ("a corpse can stand a long time propped in a doorway"); the smallest closure is to carry that concession back into the Lifecycle section by hedging the one verb, or by stating that death and long survival are both outcomes the model allows.

### Keep — A section in a PROTECTED REGISTER
> The Dharmic knoweth that the self, the clan, the nation, and the world are ever becoming, and so too is identity. Pride in identity is but the comfort of a shadow.
— `sermons.md`, section: Sermon Four
Why it is not a finding: This asserts a law over nations and the world with no frame and no evidence, which in an essay would be a finding; here the prophetic voice asserts by design, the section is listed among the PROTECTED REGISTERS, and the lens exempts it by rule.

### Flag — The hedging and the evidence run in opposite directions
> Extinction is the terminus of the habitual life. Institutions arrive there by the same road men do, and usually while congratulating themselves on their compassion.
> Rather than part with the illusion of innocence, most of us blame the messenger instead, or settle for a compromise: a bargain that never dares to demand our own disadvantage.
— `kindness.md`, section: The Fierce Yes (first quote); The Collector (second quote)
Why it is a finding: Every piece of evidence in the essay is at the scale of one life: the father of the parable, the splinted arm, the mother and the lazy son. The claims about individuals are the ones qualified ("most of us", "almost always", "if ever"), while the claims about institutions and civilizations are stated flat ("Falling civilizations forget this work", "Institutions arrive there"), exactly where the essay has the least support.
What the finding says: The essay demonstrates a cited parable and three analogies at the scale of one life, and one cited reading (Rieff) for the confessor-to-therapist lineage. The prose claims "Extinction is the terminus of the habitual life. Institutions arrive there by the same road men do". The smallest closure is a narrowed scope: state that the institutional claim is the individual case scaled up by argument, not observed, or hedge the one verb ("Institutions can arrive there by the same road").

### Keep — A claim the chapter openly marks as speculation
> Whether two populations can find a narrower ground to share cannot be settled in advance. No argument shows that such a ground cannot exist. This essay can say only who decides and by what count.
— `hierarchy.md`, section: Different Grounds
Why it is not a finding: The surrounding section runs a model of populations meeting in the generic present, which would draw the lens, but the essay here concedes its own reach in its own voice: it does not know the outcome, no argument settles it, and it limits itself to who decides. Where the essay has already conceded the reach, the concession is the answer to this lens and the passage is closed.

### Flag — A leg of the argument is asserted and never argued
> When neither party sees the error, the error becomes durable and the system dies a slow death. When the sufferers see through it, they stop accepting the fraud; the ground is established and the stage is set for a renewed upward movement.
— `kindness.md`, section: Who Pays
Why it is a finding: The three-branch outcome (slow death, renewed upward movement, quick collapse) is the pivot on which the essay's civilizational hope rests, and the second branch, the sufferers seeing through the fraud and a society turning upward, is reached by no example, no tradition, and no citation anywhere in the essay. The Fierce Yes and The Great Leveling assume the stage rather than earn it.
What the finding says: The essay demonstrates nothing for this leg; the individual cases (the mother, the splint) illustrate only the first branch. The prose claims "the stage is set for a renewed upward movement." The smallest closure is a stated objection or concession: that the essay knows no case in which a society has seen through institutionalized false compassion and recovered, and offers the branch as what the model requires rather than what history has shown.

### Keep — A specific historical claim whose fault, if any, is its truth or its source
> The third error was set in stone by the British. The rigidity of the ranking was largely fixed when the colonial administration introduced birth certificates.
— `hierarchy.md`, section: Caste
Why it is not a finding: This is a dated, checkable claim about one administration's act, stated flat and without a source, and it may well be contested; but its evidence class is a source, not a model, and what would close it is a citation or a correction, not a frame or a hedge. That is the business of the unsourced-claims lens and of a fact-checker; this lens judges register, not accuracy.

### Flag — A generalization stands on a sample chosen for it
> Why did the best of every age, on different paths, arrive at the same realization — that the choosing does not end? So faithful a convergence is not chance. It must come from something the summits share.
— `good-life.md`, section: The Common Error
Why it is a finding: The essay reaches "the best of every age" through one voice per summit (Mill, the Buddha, Aristides and Solomon, Krishna, Ashoka and Epictetus, the parable of the talents, the ox-herding pictures), and each voice was chosen because it arrived at the essay's conclusion; the convergence the section then treats as data is the selection rule that produced the sample. A reader who held the essay entire would notice that no summit's dissenting champion was heard, and that the essay itself half-admits the gap ("If I have not spoken of the summit you hold dear").
What the finding says: The essay demonstrates a set of cited exemplars, one per summit, selected for their agreement with the four requirements. The prose claims "the best of every age, on different paths, arrive at the same realization" and "So faithful a convergence is not chance." The smallest concession is a frame sentence naming the selection: that these are the voices the essay finds most persuasive at each summit, chosen because they converge, and that the argument rests on the four requirements, not on the census.

### Keep — An example offered as illustration of a claim argued on other grounds
> The Hindu civilization, is perhaps the oldest surviving civilization, and surviving is the appropriate word. For most of the last thousand years it has limped where it once took great leaps. It has earned the distinction, jointly with its tormentors, of sliding down a slippery slope and ending up with the Caste System, which manages to make all three errors.
— `hierarchy.md`, section: The Compounding of Errors
Why it is not a finding: One civilization is made the showcase, and the essay says in the next sentence that it chose the example because it is near to the author; but the three errors were derived beforehand from the essay's own definitions of quality, metric, hierarchy, and rank, and nothing in the claim about how rankings err depends on caste being typical. The example instantiates a conclusion argued elsewhere; it is not the evidence for it.

### Repaired examples

*Author-made repairs mined from this manuscript's revision history (2026-09-06). Each Before is the passage as it stood at the named collected version; each After is the current text.*

### Repaired — A generalization stands on a sample chosen for it
Before (v412):
> The Hindu civilization, perhaps the oldest surviving civilization, has earned the distinction of sliding down a slippery slope and ending up with the Caste System, which manages to make all three errors. It is, like most things, an accident of history, and demonstrates the importance of vigilance and foresight.
After (current):
> It has earned the distinction, jointly with its tormentors, of sliding down a slippery slope and ending up with the Caste System, which manages to make all three errors. I choose this example because it is near to me and demonstrates the importance of vigilance and foresight.
Why the before was a finding: one case, caste, is offered as what "history" demonstrates about how hierarchies compound their errors; the case was selected because it fits all three errors, and the fit is then read as evidence ("demonstrates"), with the selection unnamed.
What the repair did: the selection was named in the author's voice ("I choose this example because it is near to me"), so the case is now an illustration of a claim argued on other grounds rather than a sample standing for the class.

## The lens

You are looking for the gap between what a chapter demonstrates and what its prose claims: confidence outrunning the evidence class, most often a conceptual model presented as a pattern history has been shown to follow, or a general claim resting on a sample too small or too selected to carry it. Read the whole chapter and name the evidence class behind each large claim; use the GLOSSARY to tell a conceptual conclusion from an empirical one, and the PROTECTED REGISTERS to know which sections are exempt. Report only what the whole reading reveals; a single sentence's mood belongs to the SENTENCE-LEVEL PASS for modal register.

Flag a passage when:

- **The architecture is demonstrated one way and claimed another.** Several cases are shown to share a structure; the prose concludes that societies have run a sequence. Agreement across cases is evidence about the cases, not about order.
- **The model runs without a frame.** Nowhere in the stretch does the chapter say what kind of thing it is offering: a model, a tendency, a possibility, a law. Absent it, the generic present reads as report.
- **A frame is borrowed for a stretch it does not govern.** A frame licenses only what it precedes and covers; one declared for a single claim, or in a coda, does not reach a lifecycle narrated elsewhere.
- **Two claims of the same shape point in opposite directions.** As tendencies they coexist; as laws they collide, and the collision shows the law-reading producing an inconsistency the chapter would not otherwise have. Name both.
- **The hedging and the evidence run in opposite directions.** Claims about individuals are qualified, claims about institutions or civilizations are flat, and the chapter's evidence, whatever its class, is at the scale of the individual. The prose is most confident where it is least supported.
- **A leg of the argument is asserted and never argued.** A stage of the proposed pattern that no example, tradition, or citation in the chapter reaches. Earn it or say so.
- **A generalization stands on a sample chosen for it.** A claim about a class rests on one case, or on exemplars each selected because they fit, and the fit is then treated as evidence. Selection is not convergence.

Refuse to flag:

- **A frame declared for the stretch, then worked inside.** Once the kind of claim is on the record, the generic present is the correct tense.
- **A single unhedged sentence.** One unit's fault; point the SENTENCE-LEVEL PASS at it.
- **An argument from GLOSSARY definitions, while it stays conceptual.** A conclusion from defined terms owes history nothing while it says what a thing is. Once applied to what actual societies have done, it is this lens's business.
- **Sections in a PROTECTED REGISTER.** Their voice asserts by design.
- **A claim the chapter openly marks as speculation.** The concession is the answer and the passage is closed.
- **An example offered as illustration of a claim argued on other grounds.** If the claim was derived before the example arrived and does not depend on its being typical, the selection is not a fault.
- **A specific historical claim whose fault, if any, is its truth or its source.** A dated, checkable statement is closed by a citation, not a frame; hand it to the unsourced-claims lens. This lens judges register, not accuracy.

Whether the conclusion would follow if the evidence were granted belongs to the validity-and-objections lens.

For each finding, state three things and stop:

1. **What the chapter actually demonstrates**: the evidence class, named (cited texts, an argument from GLOSSARY definitions, a historical example, an analogy, a chosen set of exemplars, or nothing) and, for a sample, how it was selected.
2. **What the prose claims**: quoted, in the chapter's own words.
3. **The smallest concession that closes the gap**: a declared frame, a narrowed scope, a named selection, or a stated objection. Prefer the frame: one sentence at the head of a section licenses everything under it; narrowing each claim costs paragraphs.

Where the gap found is also the chapter's strongest objection, say so in one line and leave the rest to the validity-and-objections lens.

REWRITE OR JUDGMENT (ruled 2026-09-06). Every finding proposes its repair as
`replacement` whenever the chapter, the GLOSSARY, and the target chapters
supply what the sentence needs: a wrong word, a misstated credit, a missing
clause, a dropped scoping, a frame sentence, a corrected pointer. Reserve
`judgment` for a repair that is a decision the author alone can make — cut
or move a section, choose between two claims the chapter makes, supply a
fact the payload does not contain, add an argument that does not yet exist —
and state that decision in one clause. A finding with neither is a note the
author cannot act on in the Doc, and is a fault of the reply.
```

## `framework-consistency`

```markdown
---
class = "chapter"
inputs = ["glossary", "passes"]
examples = "prompt"
---
# Distinctions the chapter draws and then drops

*Examples are illustrations drawn verbatim from one manuscript; the rules beneath them are general. Each Flag pairs with a Keep that superficially resembles it.*

## Examples

### Flag — a scoped word used in its plain sense
> The first measures a quality against oneself in the past. The past is immutable, and there can be no cheating.
— `hierarchy.md`, section: Supremacism
Where the distinction was introduced:
> Throughout, an immutable quality means one fixed before the ranked individual's first choice and closed to all choices after it within one lifetime.
— `hierarchy.md`, section: The Impulse to Rank
Why it is a finding: The chapter reserved "immutable" for a quality fixed before the first choice and closed within one lifetime, and here applies it to the past itself, in the plain sense; the next paragraph then uses the scoped sense ("a change in an immutable quality"), so one word carries two meanings across two adjacent paragraphs.
What the finding says: The scoping of "immutable" (The Impulse to Rank) is not carried to Supremacism, where the word describes a history rather than a quality; carrying it requires a different word for the past. Mechanical replacement: "The past cannot be revised, and there can be no cheating."

### Keep — which sense a bare set-noun carries
> The most successful populations tend to agree on proxies to rank mathematicians, priests, computer scientists, and plumbers, measure them regularly, and replace them when they lose their grip.
— `hierarchy.md`, section: The Impulse to Rank
Why it is not a finding: The chapter goes on to define a population relative to the qualities on which its members are in accord, and "the most successful populations" reads here as societies, which looks like a defined word used in its plain sense; but no stipulation of the chapter is contradicted, only one of the noun's allowed senses is left unnamed, and which sense a bare set-noun carries in one sentence, with the one qualifying word that repairs it, is the ledger of the SENTENCE-LEVEL PASS that owns set-nouns.

### Flag — a restated test that asks something different
> Is the quality measured as often as it can change?
— `hierarchy.md`, section: The Lifecycle & The Need for Vigilance
Where the distinction was introduced:
> The first is whether the metric grips the quality it names, and how often the measurement is taken: at every beat, once, or before any beat at all.
— `hierarchy.md`, section: Rankings
Why it is a finding: The chapter fixes three questions and announces that the Lifecycle tests are "the three questions of the previous section, now put to the hierarchy itself", yet the restated first test keeps the frequency half and drops the grip half, so it asks something different; two paragraphs later the chapter names the very case the dropped clause covered ("A hierarchy may be measured often even as its metric has stopped capturing the quality"), and The Labor of Ranking restores the clause.
What the finding says: The first question (Rankings) is not mirrored in the Lifecycle tests, where the grip clause is dropped and the case it covered can no longer be registered. Mechanical replacement: "Does the metric grip the quality it names, and is the quality measured as often as it can change?"

### Keep — a deliberate deferral
> But he speaks to them at his own fire, and a civilization is not gathered at one fire. How a civilization recovers must wait until we have said how a man does.
— `kindness.md`, section: The Great Leveling
Why it is not a finding: The chapter's two questions are applied to a civilization here without being carried to that scale, which looks like an enumeration that stops short of the scheme; but the passage says in as many words that the question is taken up later, and The Fierce Yes then does so ("what one man does, a civilization must find a way to do together").

### Flag — a stipulation contradicted in the sense it excluded
> The same process occurs when two populations meet, but now the size of each bundle of trajectories, the inertia of each, and their permeability to one another can no longer be ignored.
— `mixing.md`, section: The Newcomer and the Meeting
Where the distinction was introduced:
> What can be said of two trajectories can be said of two populations. In matters of principle, we will use the terms "population" and "trajectory" interchangeably.
— `mixing.md`, section: The Mixing Problem
Why it is a finding: The stipulation excludes, in matters of principle, any sense of "population" in which it differs from a trajectory; the later passage rests a matter of principle, which way a meeting converges, on the size of a bundle, a property a single trajectory does not have, so it uses "population" in exactly the sense the stipulation excluded. The sentence's own set-noun is a SENTENCE-LEVEL PASS's business; the contradiction between the two passages is this lens's.
What the finding says: The stipulation (The Mixing Problem) is contradicted by The Newcomer and the Meeting and by every later result carrying a size term; carrying it requires the stipulation to be narrowed to what it licenses (interchangeable in matters of direction and accord, not of size) or withdrawn where size enters. Judgment finding.

### Keep — a pointer to another chapter
> The paper on Mixing in the Appendix argues this claim by asking what happens when two populations meet. Here is only the shape of the argument. A population forms around the qualities that Nature rewards, and Nature presents a different face in every place.
— `hierarchy.md`, section: Different Grounds
Why it is not a finding: The passage promises another chapter's argument and carries only that chapter's definitions, which looks like a stipulation left uncarried; but nothing this chapter introduced is contradicted here, and whether a pointer carries its target's mechanism, and whether the promise to Mixing is kept, is the cross-references lens's question, which has the other chapters' own text to answer it.

### Flag — a GLOSSARY definition the chapter later contradicts
> Rank is therefore a state, not a possession. It is true only as of the last beat in which it was earned.
— `hierarchy.md`, section: The Impulse to Rank
Where the distinction was introduced:
> The question every ranking system must answer is how often an individual has to earn a rank again. If the metric is a diploma, the measurement is taken once. If the metric is membership in a lineage, it was taken before the individual's first choice, once or not at all.
— `hierarchy.md`, section: Rankings
Why it is a finding: The sentence reproduces the GLOSSARY definition of rank word for word, and by making a rank true only as of a beat in which it was earned it leaves no beat at which a rank measured once, or before any choice, is ever true; the later section then treats exactly such ranks as ranks ("The worst impose a rank without you ever making a choice"), distinguishing a measurement taken from a rank earned, which the definition collapses.
What the finding says: The definition of rank (The Impulse to Rank) is contradicted by the measured-versus-earned distinction of Rankings; carrying it requires either that the definition speak of measurement ("true only as of the last beat in which it was measured"), so that a rank measured once or never remains a rank, badly founded, or that the chapter stop calling unearned standing a rank. The finding is the sentence, not the GLOSSARY. Judgment finding.

### Keep — a term in its GLOSSARY sense
> It asks whether the act enlarges or diminishes the capacity for life and Nature that sustains it.
— `good-choice.md`, section: The Choice of Deception
Why it is not a finding: The chapter's opening tests "the capacity for Becoming" and this sentence tests "the capacity for life and Nature", which reads as the key term drifting between sections; but the GLOSSARY gives Becoming the alias Existence and defines Nature as manifest existence, so the sentence uses the same term in its settled sense, and the chapter has not scoped it otherwise.

### Keep — owned by a sentence-level pass
> Accord is a present relation. A hierarchy orders that present cross-section of the cross-section
— `hierarchy.md`, section: Populations
Why it is not a finding: The doubled "cross-section" reads at first like a confusion about what a hierarchy orders; it is a duplication inside one sentence, which a SENTENCE-LEVEL PASS owns, and the intended sense (a hierarchy orders the present cross-section) is carried everywhere else in the chapter.

### Repaired examples

*Author-made repairs mined from this manuscript's revision history (2026-09-06). Each Before is the passage as it stood at the named collected version; each After is the current text.*

### Repaired — Enumeration does not mirror the scheme in number
Before (v411):
> Two things are therefore decided whenever a population ranks itself. The first is when the measurement is taken: at every beat, once, or before any beat at all. The second is who makes the choices that the measurement records: the individual, or something standing in his place.
After (current):
> Three questions are therefore decided whenever a population ranks itself, and each follows from something this book has already established.
Why the before was a finding: the Rankings section fixed a scheme of two decisions, while the same version's Lifecycle section put three test questions to the hierarchy (adding "Does the individual primarily make the choice that changes the quality") and The Compounding of Errors said caste "manages to make all three errors"; the enumerations disagreed with the scheme in number.
What the repair did: the scheme was recast as three questions, each stated in its own paragraph, so the Lifecycle tests ("the three questions of the previous section, now put to the hierarchy itself") and the three closing conditions now mirror it.

### Repaired — An example given without the parts the scheme assigns it
Before (v413):
> The metric of Jati is purity as genealogical provenance. Membership in a jati is a credential: it records who descends from whom, and it is granted once, at birth.
After (current):
> The quality ranked by a jati is purity as genealogical provenance; its metric is the rule of descent by which that purity is established. Membership is therefore a credential with discrete values: admitted or excluded.
Why the before was a finding: the chapter's four-links scheme separates the quality from the metric that assigns it a value, but the jati example named purity as the metric and gave no metric at all, describing the case in terms the scheme had replaced.
What the repair did: the distinction was carried to the example, quality and metric each named, with the metric's few-valued form tied back to the chapter's own remark that a metric may take only two values.

### Repaired — A clarification given once is contradicted by a later passage
Before (v413):
> If the choices were made by a parent, a precept, or an exemplar, the rank measures them, and the individual merely wears the rank for show.
After (current):
> If it follows from choices made by the individual’s parents, the rank belongs to their history and is merely inherited by the individual. If it rewards obedience to a precept or exemplar without asking whether the member has discerned, tested, or improved upon it, the rank measures conformity, and the individual merely wears the rank for show.
Why the before was a finding: the section on Racial, Ideological and Mimetic Populations stipulates that alignment and imitation are "not corrupt in itself" and that "the corruption comes when conformity is made the last metric rather than the first lesson", yet the third question treated any rank following a precept or exemplar as worn for show, using the clarified phrase in the sense the clarification excluded.
What the repair did: the third question was split to carry the distinction, inheritance from parents on one side and unexamined conformity to a precept or exemplar on the other.

## The lens

You are looking for a distinction the chapter introduces and then fails to carry to the places in the same chapter where it does work: its examples, enumerations, conditions, and closings. The scope is the single chapter; a scheme or term that drifts between chapters belongs to the cross-references lens, and so does every pointer from this chapter to another. Of the INPUTS, the GLOSSARY is the authority on a term's settled sense and the only thing you consult outside the chapter; the SENTENCE-LEVEL PASSES own faults that live inside one sentence, and where one of them owns a fault you point there rather than report it. No other chapter is needed.

Read the chapter whole. First list, for yourself, every distinction it introduces: a definition; a scheme (a fixed set of tests, links, errors, or questions); a scoping (a word given a narrower sense than its plain one, marked "throughout" or "here"); a stipulation (two terms to be read as one; a phrase not to be read literally); a contrast. For each, note where it is stated and every example, enumeration, condition, and closing that should carry it. Only what is on that list can ground a finding.

Flag a passage when:
- a word the chapter has scoped is used in its plain sense, or an example is described in terms the chapter's own scheme has replaced: a case that the scheme says has certain parts (a quality and its metric, a condition and its test) is given without them;
- an enumeration of tests, errors, conditions, or hard cases does not mirror the scheme the chapter fixed, in number or in any wording that changes what a test asks; wording that asks the same thing in other words is not a finding;
- a stipulation or clarification given once is contradicted by a later passage that uses the clarified phrase in the sense the clarification excluded;
- a sentence reproduces a GLOSSARY definition and a later passage of the same chapter contradicts it; the finding is the sentence, never the GLOSSARY.

Refuse to flag:
- a deliberate deferral, where the chapter says in as many words that a question is taken up later;
- a pointer to another chapter, whether one orienting clause or a promise of its argument; whether it carries the target's mechanism, and whether the deferral is kept, is the cross-references lens's question;
- a term used in the sense the GLOSSARY gives it, however far from the plain sense; but where the chapter has itself scoped the term, the chapter's scoping governs within the chapter and the first Flag rule applies;
- which of its allowed senses a bare set-noun (a people, a class, a population) carries in one sentence; the SENTENCE-LEVEL PASS that keeps the ledger of set-noun senses owns that. This lens sees a set-noun only where the chapter stipulated one sense and a later passage contradicts the stipulation;
- anything else a SENTENCE-LEVEL PASS owns: word choice, mood, duplication, register, punctuation. This lens sees only what spans paragraphs.

For each finding, state the passage verbatim, the distinction it fails to carry and the section where that distinction was introduced, and in one sentence what carrying it would require; where the fix is mechanical (a missing scoping word, a dropped clause of a test) supply the replacement sentence, and where it is a judgment give the decision as `judgment`, and stop.

REWRITE OR JUDGMENT (ruled 2026-09-06). Every finding proposes its repair as
`replacement` whenever the chapter, the GLOSSARY, and the target chapters
supply what the sentence needs: a wrong word, a misstated credit, a missing
clause, a dropped scoping, a frame sentence, a corrected pointer. Reserve
`judgment` for a repair that is a decision the author alone can make — cut
or move a section, choose between two claims the chapter makes, supply a
fact the payload does not contain, add an argument that does not yet exist —
and state that decision in one clause. A finding with neither is a note the
author cannot act on in the Doc, and is a fault of the reply.
```

## `illustration-as-argument`

```markdown
---
class = "cross-chapter"
inputs = ["glossary", "registers", "passes"]
targets = ["pointers"]
examples = "prompt"
---
# When the illustration carries the argument

*Examples are illustrations drawn verbatim from one manuscript; the rules beneath them are general. Each Flag pairs with a Keep that superficially resembles it.*

## Examples

### Flag — the claim's only support is the image, here or where it defers
> We can state a lemma: trajectories do not die. The extended mode of Choice reads trajectories. The ledger is cosmic, not private; a history does not become unavailable because its frontier has dissolved.
— `metaphysic.md`, section: The Undying Trajectory
Why it is a finding: The Persistence Theorem the passage builds on earns only that a trajectory is immutable; the lemma needs the stronger claim that a dissolved locus's history stays *available* as a reference for new acts elsewhere, and in this passage the sole support for that step is the ledger's property of being one book anyone may read. No other passage is named as carrying it.
What the finding says: The ledger is trusted to prove availability, not immutability; the support is in this passage only and is the image. A stated premise could carry it: History Space is defined as the totality of trajectories, and the extended mode of Choice takes the whole trajectory as its reference, so the passage need only say that the extended mode reads History Space rather than the dissolved locus's present state. The GLOSSARY term behind the figure is Choice (its extended mode); the ledger sentence can then illustrate a step already made.

### Keep — illustration after the argument
> The good life may cast happiness or reduce suffering, as warmth is cast by a flame, but new agency empowered by that life — within and in others — is the real gain. Therefore, to pursue warmth and not the fire is to mistake the half for the whole.
— `good-life.md`, section: The Avoidance of Suffering
Why it is not a finding: The "Therefore" looks as though it reasons from flame to conclusion, but the conclusion was earned two sections earlier, in "The Good Choice, Repeated", from stated terms (a good life is a trajectory of choices that enlarges the capacity to discern and become); the flame decorates a verdict already reached.

### Keep — the premise sits in the chapter the passage names
> Nothingness annihilates: it settles every question, finishes every story, and ends the game. Choice tends to continue: every move it makes creates new moves to be made. The full account of this duality belongs to the Metaphysic.
— `impulses.md`, section: The Infinite Game
Why it is not a finding: The sentence before this says the two Fields "pull in opposite directions", a force figure; but each Field's tendency is stated here as a premise (settles every question; every move creates new moves), and the passage names the chapter that carries the full account. The figure is not the only support either in this passage or in the passage it defers to; whether the deferral is kept is the cross-references lens's question.

### Flag — a property of the figure silently promoted to a law
> The Law is the floor. It is not liberty's constraint but its precondition. The principled individual must discriminate; the floor must not.
— `kindness.md`, section: The Hard Floor
Why it is a finding: A floor's physical property (a surface that bears weight is what lets the dancer move) has been carried over, with no analogy declared and no premise stated, into a general law that Law is the precondition of liberty; the section's earlier argument about hours freed from vigilance supports "helps" but not "precondition".
What the finding says: The floor is trusted to prove necessity; the support in this passage is the image, and no other passage is named. A stated premise could carry it: the book elsewhere holds that a difference registers only against a reference that does not itself differ, so consequences that follow consistently are the reference against which the difference between choices can register at all; stated here, "precondition" is earned and the floor becomes illustration. The GLOSSARY term behind the figure is Choice (discernment as its first half).

### Keep — an analogy declared as a model, with its disanalogies named
> Its magnitude is the population's inertial memory, written $m$, which plays the part in the space of qualities that mass plays in mechanics: it measures how much force a change of direction takes. Unlike mass, it is built and spent.
— `mixing.md`, section: Inertia
Why it is not a finding: The mass analogy is declared as an analogy ("plays the part"), the disanalogy is named in the same breath ("Unlike mass, it is built and spent"), and nothing downstream rests on a property of mass that inertial memory was not given; the promotion is loud and bounded, not silent.

### Keep — a PROTECTED REGISTERS line quoted inside the chapter
> As Sermon One puts it, *The Dharma is the volcanic fire that thrusteth the mountain skyward, the gentle rain that returns it to dust, the ceaseless wheel with wheels within wheels*. The Universe is a rhythm that continues.
— `basilides.md`, section: The Pulse
Why it is not a finding: The identity claim in figure (the Dharma *is* fire, rain, wheel) is a quoted line from a section PROTECTED REGISTERS lists as speaking in figures by design; the chapter reports it and does not reason from the fire's properties in its own voice. Had the chapter's own prose restated the identity and argued from it, the paraphrase would be in scope.

### Flag — a defined term replaced by its image where precision is tested
> Does the form point toward something no individual can possess, and does it still point beyond itself? Does the form still point beyond itself? An idol is a rung, and a rung is judged by whether anyone climbs.
— `god.md`, section: The Criteria
Why it is a finding: The chapter defined an idol, in "What an Idol Is", as a form whose significance points beyond the measurable object; at the moment the first test must say how to tell that a form still points, the definition is swapped for the ladder image and the criterion becomes a property of rungs.
What the finding says: The rung is trusted to supply the test itself, so "whether anyone climbs" is never cashed into anything checkable; the support is in this passage only and is the image. A stated premise could carry it, and the chapter's own third test shows the vocabulary is available: a form still points when attention on it moves toward what is not subject to measurement and enlarges the capacity to discern and choose again. The term behind the figure is the chapter's own definition of idol; the first test could be stated in those terms and keep the rung as the picture.

### Keep — a figure the passage cashes out at once
> A habit is momentum. It is a memory of the past, an echo of choices already made. A habit forms when a once-creative move becomes so familiar that one no longer sees it as a choice.
— `impulses.md`, section: Habit and the Gaps
Why it is not a finding: The figure is an identity claim ("is momentum"), but the next two sentences convert it into a definition stated in the passage (a remembered move no longer seen as a choice), and the rest of the section reasons from that definition, not from any property of physical momentum.

### Flag — an example that does not instantiate the claim it decorates
> Athens learned the danger of virtue carried as a crown from virtue's own exemplar. Their leader Aristides was called the Just until the city voted him into exile. One voter — illiterate, and not knowing the man before him — handed his shard to Aristides himself.
— `good-life.md`, section: Virtue
Why it is a finding: The claim the anecdote decorates is that a virtue held as an end in itself, "for the name it earns here", does not always enhance life, because a record of past choices cannot tell its holder when to bend. The anecdote shows a city's resentment of a reputation its bearer did not seek, and the chapter itself calls his conduct "virtue perfect even in the act of its own exiling"; the mechanism the claim names (the vow answering where discernment was needed) is absent, and the fit is asserted afterwards ("The tree that would not bend has no better epitaph") rather than shown.
What the finding says: The example is trusted to instantiate "virtue held as an end closes its holder's future"; what it instantiates is "a name for virtue draws envy", a different and weaker claim about the crowd rather than the virtuous man. The claim's support is the neighbouring examples, not this one; no GLOSSARY term stands behind it. The chapter's steadfast tree and the honest hermit two sections later do instantiate the stated mechanism.

### Keep — an example scoped to the clause it illustrates
> Neither is corrupt in itself. A person may adopt a principle deliberately, and an apprentice learns the trade by copying the master's hands; alignment and imitation are how anyone first learns to choose. The corruption comes when conformity is made the last metric rather than the first lesson, and the precept or the exemplar is placed beyond measurement.
— `hierarchy.md`, section: Racial, Ideological and Mimetic Populations
Why it is not a finding: The apprentice instantiates only the narrow clause it is attached to (imitation is how one first learns), and the passage says so, then states separately the condition under which imitation becomes corruption. An example offered for one clause, with its limit declared, is doing exactly the work it is credited with.

### Flag — the same figure carrying a different mechanism from an earlier chapter's
> The model of the cosmic ledger is a useful tool. Every action contributes a permanent entry. The global balance remains Nothing, yet local balances may not.
— `metaphysic.md`, section: Memory as The Ledger
Why it is a finding: the other chapters' text record that the essay on the good life used the ledger as the account a virtue "settles in an afterlife", glossed there as a temple wall keeping its accounts: a moral tally that can stand in credit and is closed at death. Here the ledger is a conservation record whose total is always Nothing and is never settled. The same word names two operations, and no sentence in either chapter marks the change; the GLOSSARY defines neither.
What the finding says: Two mechanisms, two locations: a moral account closed at death (the essay on the good life, section Virtue, and its footnote) and a neutral record that always balances (here). A reader carrying the earlier sense reads "local balances may not" as moral credit. The GLOSSARY fixes no ledger term, so the author must choose which mechanism the figure owns and mark the other use as a different figure.

### Keep — the same figure at a new scale, its mechanism carried
> In physical systems, it appears as inertia. In living systems, as habit. In the mind, as preference and conditioning.
— `metaphysic.md`, section: The Many Scales
Why it is not a finding: The head of the list fixes one mechanism (momentum is history continued) and declares these as its scales; the other chapters' text show the earlier essay on the two impulses gave habit the same mechanism ("a memory of the past, an echo of choices already made"). Recurrence with the mechanism carried and the scale named is the figure working, not a finding.

### Repaired examples

*Author-made repairs mined from this manuscript's revision history (2026-09-06). Each Before is the passage as it stood at the named collected version; each After is the current text.*

### Repaired — A property of the figure silently promoted to a rule
Before (v413):
> But a man can leave a family or a population, just as a cell cannot leave a body, and the agent with greater freedom is held to the measurement first.
After (current):
> Each must be measured at the scale on which its action occurs: an individual by choices attributable to the individual, an institution by the choices it organizes and sustains, and a population by the direction of its common action. None may borrow the deeds of another and wear them as its own.
Why the before was a finding: the only support for "the agent with greater freedom is held to the measurement first" is the cell-and-body image; a property of the figure (cells cannot leave) is read off as a rule about who is measured first, with the analogy neither declared nor bounded.
What the repair did: the figure was dropped and a stated premise put in its place, measurement at the scale of the action, with the rule against borrowing another's deeds carrying the step the image had carried.

### Repaired — An example that does not instantiate the claim it decorates
Before (v412):
> The second error is ranking the jatis against each other. The Brahmins above the Vaishyas, and a hundred Brahmin jatis that must each be slotted into their appropriate position in the rank order.
After (current):
> The second error is ranking the jatis against each other: the Brahmins above the Vaishyas, and a hundred Brahmin jatis that must each be slotted into their appropriate position in the rank order. No quality anyone could improve is served by knowing which of the hundred outranks which. The ordering enlarges nothing but itself.
Why the before was a finding: the chapter's second error is that improving the quality enlarges only the rank, not the capacity to choose; the hundred-jatis anecdote shows an elaborate ordering but not that mechanism, and the fit is asserted only by the label "second error".
What the repair did: the mechanism was named inside the example ("No quality anyone could improve is served"; "enlarges nothing but itself"), so the anecdote now instantiates the claim it decorates.

## The lens

You are reading one chapter for places where an illustration — a figure of speech, an analogy, a model borrowed from another field, an example or an anecdote — is asked to do the argument's work: where a conclusion rests on what the picture is like, or on what the story happens to show, rather than on a stated premise. A figure may illustrate a step already made; it may not make the step. An example may show a claim; it may not show a weaker or different claim and be credited with the one it decorates. Read the chapter whole, list its load-bearing figures and examples, then check each against the GLOSSARY (which defined term, if any, stands behind the figure, and what it is defined to do), the earlier chapters themselves (how they used the figure, and whether a chapter this one defers to states the premise; read them, never a summary of them), and PROTECTED REGISTERS (which voices are exempt).

Flag a passage when:
- the only support for a claim IS the image, in this passage or in the passage it explicitly defers to — a ledger records, therefore nothing is lost. Follow the deferral first; if the deferred passage states the premise, it is not a finding.
- a property of the figure is silently promoted to a law — a field exerts force, so the term "pulls"; a floor bears weight, so law is a precondition. "Silently" means the passage neither declares the analogy nor bounds it; a declared and bounded model is refused below.
- a term defined in the GLOSSARY or earlier in this chapter is replaced by its image at exactly the point where precision is tested — where a test, criterion, or distinction must be stated and the figure's property is offered instead.
- an example or anecdote does not instantiate the claim it decorates, or instantiates a weaker or different claim — the mechanism the claim names is absent from the story, and the fit is asserted in a closing sentence rather than shown.
- the same figure carries a different mechanism here from the one an earlier chapter gave it, as that chapter's own text or the GLOSSARY records — the same word naming two operations, with no sentence marking the change.

Refuse to flag:
- illustration after the argument — a figure decorating a conclusion already earned from stated premises or defined terms, in this chapter or in the chapter it names.
- a figure whose premise sits in the chapter the passage names as carrying it; the deferral is the support, and whether the deferral is kept belongs to cross-references.
- a voice listed in PROTECTED REGISTERS, including its lines quoted inside this chapter. Paraphrase of such a line in the chapter's own voice, or reasoning from the quoted figure's properties in that voice, is in scope.
- a figure the passage itself cashes out — the image is followed at once by the GLOSSARY term or a definition stated here, and nothing downstream uses a property the definition did not grant.
- an analogy declared as a model with its disanalogies named ("plays the part mass plays; unlike mass, it is spent"). Judge only whether what follows uses a property the disanalogies removed.
- an example scoped to the clause it illustrates, where the passage says what the example does and does not show.
- the same figure at a new scale, its mechanism carried and the shift declared.

A figure used against itself within this chapter, or a mixed figure, is a SENTENCE-LEVEL PASS (metaphor consistency): point there. A claim whose support is non-figurative but thin, or whose conclusion does not follow, is another lens's.

For each finding, state the passage; what the illustration is trusted to prove; where the claim's support is (this passage, the deferred passage, nowhere); whether a stated premise — from the GLOSSARY, from this chapter, or one the author would have to add — could carry the claim without the figure, naming the GLOSSARY term if one stands behind it; for an example, the claim it actually instantiates; for a two-mechanism figure, both mechanisms and both locations. Do not draft the rewrite or judge the claim's truth. Then stop.

REWRITE OR JUDGMENT (ruled 2026-09-06). Every finding proposes its repair as
`replacement` whenever the chapter, the GLOSSARY, and the target chapters
supply what the sentence needs: a wrong word, a misstated credit, a missing
clause, a dropped scoping, a frame sentence, a corrected pointer. Reserve
`judgment` for a repair that is a decision the author alone can make — cut
or move a section, choose between two claims the chapter makes, supply a
fact the payload does not contain, add an argument that does not yet exist —
and state that decision in one clause. A finding with neither is a note the
author cannot act on in the Doc, and is a fault of the reply.
```

## `through-line`

```markdown
---
class = "cross-chapter"
inputs = ["glossary", "registers", "passes", "reading-order"]
targets = ["neighbours"]
examples = "prompt"
---
# One claim, and every section in its service

*Examples are illustrations drawn verbatim from one manuscript; the rules beneath them are general. Each Flag pairs with a Keep that superficially resembles it.*

## Examples

### Flag — The chapter names its claim twice, differently
> The conjecture of this essay is that false compassion is the Field of Nothingness in a warm coat. It does what Nothingness does: it dissolves distinctions, relieves tension, and moves everything it touches toward stillness.
— `kindness.md`, section: The Warmth Beneath the Cold
Why it is a finding: The opening declares in as many words what the essay is ("This essay takes kindness as a test case for a claim made since the steadfast tree: any virtue, held as an end and spared all discernment, becomes a vice."); the last section declares a different claim as "the conjecture of this essay", and no section between them argues it — the body's spine (the two questions, the hard floor, who pays) argues a third thing, that a kindness is judged by what it leaves behind and whether it was born to end. Two readers asked to write the chapter's claim in one sentence would write different sentences.
What the finding says: Claim as read from the body: a kindness is good only if it enlarges the recipient's capacity to choose and is built to end. `kindness.md`, The Warmth Beneath the Cold, first two paragraphs: no single claim — the closing's "conjecture of this essay" is declared, never argued, and differs from the opening's. Move: retire "of this essay" and announce the paragraph as a coda handed to the metaphysical chapter, or grow it into a section that earns the word.

### Keep — A voice in a PROTECTED REGISTER
> And what of the fortress of Thought, and the symbols upon its walls? The Dharma exceedeth them all. For it lieth beyond the reach of any word, though every word doth reach for it.
— `sermons.md`, section: Sermon One
Why it is not a finding: Within one page the sermon moves from Nothing to the two Fields to Time to Thought to the wheel, and no sentence says what the sermon is for; in an essay that would be the first flag above. But the runner marks this voice PROTECTED, and a proclamation has no through-line to keep; the debt is settled by the essays that unpack it.

### Flag — A passage serves no claim the chapter makes
> And yet, they cannot explain the initial condition — the Big Bang. If they manage to conceive of a cyclic universe, they cannot get past the fine-tuning problem. They require at least one miracle to justify their claim.
— `redemption.md`, section: The Deterministic Bargain
Why it is a finding: The chapter's claim is that the salvations on offer are bargains struck by fatigue, and that redemption is the return to choice; this section's job is to show determinism as the fifth such bargain. Whether determinists can account for the initial condition or fine-tuning is an argument that determinism is false, which no step of the chapter needs — cut the paragraph and the next one ("Determinism, given its full wish — it would be no better place than hell") stands untouched.
What the finding says: Claim: the offered salvations are fatigue's bargains; redemption is the return to choice. `redemption.md`, The Deterministic Bargain, second paragraph: dead passage — it prosecutes a different case against the same target and feeds no later step. Move: cut, or hand it to the chapter that argues determinism on evidence.

### Keep — A digression the chapter announces and returns from
> But he speaks to them at his own fire, and a civilization is not gathered at one fire. How a civilization recovers must wait until we have said how a man does.
— `kindness.md`, section: The Great Leveling
Why it is not a finding: The section has drifted from the single man to the whole civilization, which a reader could take for the essay losing its subject; but the text names the drift, says it will wait, and the section after next returns in matching words ("And what one man does, a civilization must find a way to do together."). A stated detour with a stated return is structure in the author's hands.

### Flag — Proportion inverted
> The turn is inescapable; the height gained is not. We can still extend each rise as far as possible by asking what makes idol worship effective, which the opening asked for and that we have been hinting at this whole time.
— `god.md`, section: The Criteria
Why it is a finding: The opening names the criteria as the deliverable ("Only then do the criteria arrive for judging when the practice aligns the worshipper"); they arrive in the last section as three paragraphs, one test each, after some 2,400 words on the spiral, of which the two descending subsections (The Downward Turn, The Death of God) run 900 words and are promised nowhere in the opening. The quoted sentence concedes the shape: what was asked for has been "hinted at this whole time".
What the finding says: Claim: an idol is a rung, and worship is judged by whether the climber climbs. `god.md`, The Criteria against The Downward Turn and The Death of God: inverted weight — the promised deliverable gets three paragraphs, the unpromised descent gets two sections. Move: grow each test to the length of one spiral subsection, or announce the descent in the opening as part of the promise.

### Keep — A load-bearing step kept brief because another chapter carries it
> The paper on Mixing in the Appendix argues this claim by asking what happens when two populations meet. Here is only the shape of the argument.
— `hierarchy.md`, section: Different Grounds
Why it is not a finding: The section carries the essay's heaviest consequence in a page and says so, names the chapter that argues it, and the other chapters confirm that the appendix paper builds the model and runs the cases; the brevity is a hand-off, not a hole. Were the named chapter absent from the other chapters' text, or found not to do the work, that would be the cross-reference lens's finding, not this one's.

### Flag — The title promises what the chapter disowns
> This essay has not defended God. If God is the underlying principle of reality, then God needs no defense, and the Field of Choice is not diminished by a single broken statue.
— `god.md`, section: The Criteria
Why it is a finding: The title is "In Favor of God"; the opening says "let no reader take what follows for a defense of God", and the closing says the essay has not defended God and names what it did defend — the climber and the rungs. The title names the one thesis the chapter twice disowns, so a reader carrying the title through the essay waits for a case the essay says it will not make; whether the title is irony or a leftover, the pages do not tell him.
What the finding says: Claim: an idol is a rung, and worship is judged by whether the climber climbs. `god.md`, title against the opening's second paragraph and the closing paragraph: broken promise — the title names a defense the chapter refuses twice. Move: retitle to what the closing says it defended, or make the title's irony land in the opening's first paragraph.

### Keep — An opening that raises a question and delivers its sharpening
> Yet this question, asked without enough preparation, is oddly premature. No philosopher asks how a river ought to flow before he knows what a river is, nor how a tree ought to grow before he has understood trees.
— `good-life.md`, section: (opening, before the first heading)
Why it is not a finding: The opening raises "how ought one climb?" and a reader checking promises against delivery will notice the essay never says how; but the quoted sentences declare that question premature and turn to the prior one — what the good life is — which the essay then answers. A question raised and set aside on the record is not a promise, and the chapter that leaves it unanswered has kept its word.

### Flag — The order hands the reader a conclusion before its premise
> Populations may mix, but they are not always compatible. The essay on Kindness that Kills argued that different populations may genuinely need different conditions to thrive, and that the answer is to live and grow in different places as separate populations, rather than being forced to mix.
— `hierarchy.md`, section: Populations
Why it is a finding: The section's job is to define a population and accord; this paragraph states the essay's heaviest consequence (separate populations) with only a back-reference for support, and the premise that makes it follow inside this chapter — that a floor built on a quality one population cannot express becomes two standards in practice — arrives seven sections later in Different Grounds. No sentence here says the conclusion is being previewed, so the reader holds the remedy for six sections without the disease.
What the finding says: Claim: a ranking is valid only while its metric grips a quality, serves the good life, and records the ranked one's own choices. `hierarchy.md`, Populations, fourth paragraph: wrong order — a conclusion stated in passing, argued in Different Grounds, with nothing between marking it as a preview. Move: move it into Different Grounds, or announce it here ("the essay argues this below").

### Keep — A seam between paragraphs
> Sometimes a single population can have all three characteristics.
— `hierarchy.md`, section: Racial, Ideological and Mimetic Populations
Why it is not a finding: A one-sentence paragraph dangling at the end of a section, joined to nothing on either side, looks like structure gone wrong; but the section serves the claim (three kinds of population whose answers to the three questions go wrong), and a dangling sentence is a seam between paragraphs. A SENTENCE-LEVEL PASS owns it; report the paragraph here only if the section around it served no claim.

### Repaired examples

*Author-made repairs mined from this manuscript's revision history (2026-09-06). Each Before is the passage as it stood at the named collected version; each After is the current text.*

### Repaired — The order hands the reader a conclusion before its premise
Before (v390):
> A race is therefore a deme closed to evolution. It measures itself only at birth, so no member is required to evolve as much as a member of a population that keeps ranking its members by the qualities they presently manifest.
After (current):
> A population based on race settles its rankings before the first beat, before the first breath is taken. Others settle them later, and can settle them just as badly.
Why the before was a finding: in v390 the Race section drew its conclusion from when the measurement is taken, a premise the chapter fixed only two sections later in Ranking ("Two things are therefore decided whenever a population ranks itself. The first is when the measurement is taken"), and no sentence said why the reader should hold the verdict first.
What the repair did: the Rankings section was moved before the section on racial, ideological and mimetic populations, and the race paragraph now reads off the three questions already fixed.

## The lens

You are looking for a chapter that does not know its claim, or knows it and does not serve it: sections that pull toward no claim the chapter makes, weight laid where the argument is light, a title or opening that promises what the pages do not deliver, a closing that banks what was never shown, and an order that hands the reader a conclusion before its premise. Judge over the whole chapter with the neighbouring chapters themselves beside it, because proportion and deferral can only be judged against what the chapters on either side carry. Sections in a PROTECTED REGISTER are exempt. A paragraph that joins badly to its neighbour is not yours; a SENTENCE-LEVEL PASS owns transitions.

Begin by writing, in one sentence, what the chapter's claim is as the chapter itself declares or implies it — in its title, its opening, and its close — and quote the words where it does so. Then list its sections in order and give each one's job in the argument in a few words: premise, step, test, objection, illustration, aside, return. Every finding below is read off that list.

Flag a passage when:

- **No statable claim, or two.** The title, the opening, and the closing each name the claim and do not name the same one; or no sentence anywhere says what the chapter is for and the sections do not add up to one. Two competent readers writing the one-sentence claim would write different sentences.
- **A section serves no claim the chapter makes.** It argues something adjacent, decorates, or settles a quarrel the chapter never joined; cut it and no later step loses a premise.
- **Proportion is inverted.** The load-bearing step — the one every later section leans on, or the deliverable the opening promised — gets a paragraph while an aside or an illustration gets pages. Judge weight against the job, not against the word count alone.
- **The title or opening promises what the chapter does not deliver, or the closing claims what was not shown.** Check the closing's verbs against the section list: "has shown", "we have seen", "the conjecture of this essay" must each point at a section that did the work.
- **The order forces the reader to hold a conclusion before its premise arrives,** and no sentence says why. A conclusion stated first as a thesis and marked as one is fine; one stated in passing and argued much later, with nothing between, is not.

Refuse to flag:

- **A digression the chapter announces and returns from.** If the text says it is stepping aside and later says it is back, the structure is in the author's hands.
- **A load-bearing step kept brief because another chapter carries it.** If the chapter names that chapter and that chapter's own text confirms it does the work, the brevity is a hand-off. If it does not confirm, that is the cross-references lens's finding, not yours.
- **An opening that raises a question rather than promising an answer,** and a chapter that delivers the question's sharpening — or says in as many words which question it will answer first.
- **Anything in a PROTECTED REGISTER.** A voice that proclaims by design has no through-line to keep.
- **The seam between two paragraphs.** A hard turn, a missing bridge, a one-line paragraph that dangles: a SENTENCE-LEVEL PASS owns that. Report the paragraph only if the section around it serves no claim.

Most of what this lens finds is a judgment — a claim to choose, a section to cut or grow, a paragraph to move — and is stated as the structural move the author must decide. Where the cure is a sentence — an announcing sentence, a thesis marked as one, a closing verb corrected against the section list — propose it as the rewrite.

For each finding, state three things and stop: the chapter's claim as you wrote it in your first sentence; where the fault is — section and passage — and which of the five it is (no claim, dead section, inverted weight, broken promise, wrong order), with the claim it fails against; and the smallest structural move that would cure it: cut, move before or after a named section, shrink or grow, retitle, or announce. Name the move; do not draft it.

REWRITE OR JUDGMENT (ruled 2026-09-06). Every finding proposes its repair as
`replacement` whenever the chapter, the GLOSSARY, and the target chapters
supply what the sentence needs: a wrong word, a misstated credit, a missing
clause, a dropped scoping, a frame sentence, a corrected pointer. Reserve
`judgment` for a repair that is a decision the author alone can make — cut
or move a section, choose between two claims the chapter makes, supply a
fact the payload does not contain, add an argument that does not yet exist —
and state that decision in one clause. A finding with neither is a note the
author cannot act on in the Doc, and is a fault of the reply.
```

## `unsourced-claims`

```markdown
---
class = "chapter"
inputs = ["glossary", "registers"]
examples = "prompt"
---
# Unsourced claims: borrowings that arrive without a checkable source

*Examples are illustrations drawn verbatim from one manuscript; the rules beneath them are general. Each Flag pairs with a Keep that superficially resembles it.*

## Examples

### Flag — quotation, epigraph included, with no work named
> The eye through which I see God is the same eye through which God sees me; my eye and God’s eye are one eye, one seeing, one knowing, one love. - Meister Eckhart
— `christic.md`, section: Christic Connections (epigraph above the title)
Why it is a finding: A named thinker is quoted verbatim and no work, sermon number, edition or translator appears anywhere in the essay; christic.md carries no footnotes at all. The borrowing is load-bearing — it states the essay's thesis in miniature — and Eckhart's German sermons survive in contested redactions, so the exact wording is something a specialist could check and would.
What the finding says: A historian of medieval mysticism would check it. A sufficient citation is Meister Eckhart, German Sermon 12 ("Qui audit me, non confundetur") in the Quint numbering (Deutsche Werke I), together with the English translation being quoted — M. O'C. Walshe, The Complete Mystical Works of Meister Eckhart, with his sermon number and page.

### Flag — quotation with a work named but no location, translator or edition
> As the Gospel of Thomas puts it: "If you bring forth what is within you, what you bring forth will save you."
— `christic.md`, section: Beyond the Demiurge: Breaking the Ego’s Crown
Why it is a finding: The work is named and nothing else is: no saying number, no translation, no edition, and the essay has no notes. A bare title does not pass. The Gospel of Thomas is a numbered collection whose English wordings differ markedly between translators, so without a saying number and a translator the quoted words cannot be checked.
What the finding says: A scholar of early Christian literature would check it. A sufficient citation is Gospel of Thomas, saying 70, in The Nag Hammadi Library in English, ed. James M. Robinson, translated by Thomas O. Lambdin, with the page; or whichever translation the wording is taken from, named.

### Flag — unmarked close paraphrase of a famous line
> Man is a rope stretched over an abyss — behind Man, the animal; ahead of Man, a horizon that does not yet exist.
— `nietzsche.md`, section: The Übermensch and the Ladder of Sermon 7
Why it is a finding: The sentence is not marked as a quotation and attributes nothing, so the reader takes the image as the author's own, yet it reproduces one of Nietzsche's most recognizable lines almost word for word; the second clause blends the source with the author's gloss. Unmarked close paraphrase of a famous line counts as a quotation, and nietzsche.md has no notes.
What the finding says: A Nietzsche scholar would check it. A sufficient citation is Thus Spoke Zarathustra, Prologue, section 4, in a named translation — Walter Kaufmann, The Portable Nietzsche (Viking, 1954), with the page — and quotation marks or "as Zarathustra says" around the borrowed clause.

### Flag — meaning or derivation from a classical or sacred language asserted as fact
> The Dharma is reclaimed here primarily for its etymological root: *dhṛ* — "that which grounds,” and ma — “creation”.
— `indic.md`, section: Eastern Connections
Why it is a finding: A derivation from a classical language is stated as established fact with no footnote (indic.md has none) and the book's central term rests on it. A specialist would dispute the second half: in dharma the -ma is a nominal suffix, as in karma from kṛ, not an independent morpheme meaning "creation"; only dhṛ, "to hold, support," is uncontroversial. The same derivation is footnoted in a later chapter at [^R3], but that note names no work, and a source in another chapter would not cover this one in any case.
What the finding says: A Sanskritist would check it. A sufficient citation is a standard dictionary entry — Monier-Williams, A Sanskrit-English Dictionary (1899), s.v. dharma and dhṛ — or an etymological dictionary of Old Indo-Aryan under the root dhar; and if "ma = creation" is the author's own gloss rather than a received etymology, the sentence should say so.

### Flag — doctrine attributed to a tradition or figure in a disputable form
> The historical Basilides on the other hand begins with Nothing. It is Jung, in his seven Sermons, who begins one step later with the *Pleroma* and attributes that beginning to Basilides.
— `basilides.md`, section: The God That Is Not
Why it is a finding: "The historical Basilides" is given a definite starting point, but Basilides survives only in two incompatible hostile reports — Irenaeus describes an emanationist system from an unbegotten Father, and Hippolytus alone gives the "non-existent God" — and which report reflects Basilides is a live scholarly dispute. The epigraph names "Refutation of All Heresies, VII" with no chapter, translation or edition, the Irenaean account is never mentioned, and basilides.md has no footnotes.
What the finding says: A historian of Gnosticism would check it. A sufficient citation is Hippolytus, Refutatio omnium haeresium VII.20–21 (M. David Litwa's translation, SBL Press, 2016, or the Ante-Nicene Fathers vol. 5 text), alongside Irenaeus, Adversus haereses I.24.3–7, with a clause conceding that the two reports diverge; for the dispute itself, Winrich Löhr, Basilides und seine Schule (1996).

### Flag — scientific result invoked as settled support when it is a research position
> A cleaner parallel comes from quantum information: a system in a pure state has zero von Neumann entropy, and all observed entropy is entanglement entropy arising from the partial perspective of subsystems.
— `metaphysic.md`, section: The Conservation of Nothing
Why it is a finding: The first clause is textbook, but "all observed entropy is entanglement entropy" is a specific and still-argued position in the foundations of statistical mechanics, offered as the "cleaner" support for the axiom immediately after the zero-energy hypothesis was honestly marked as contested. It arrives with no footnote, while the weaker neighbouring parallel at least received names and dates. Common knowledge does not rescue it: the textbook fact is the first clause, and the passage leans on the second.
What the finding says: A physicist would check it. A sufficient citation is Popescu, Short and Winter, "Entanglement and the foundations of statistical mechanics," Nature Physics 2 (2006), 754–758, or Goldstein, Lebowitz, Tumulka and Zanghì, "Canonical typicality," Physical Review Letters 96, 050403 (2006) — with wording that presents it as one research program rather than the settled account.

### Flag — count or statistic stated as fact
> The final outcome, more than a thousand jatis ordered in a hierarchy, would easily be ridiculous if it were not both true and cruel. Only a gravely wounded civilization would accept such a blow.
— `hierarchy.md`, section: Caste
Why it is a finding: "More than a thousand" is offered as a fact about the world ("if it were not both true"), not as an illustration, and no note attaches to it; the chapter's one demographic note, [^E4], sources a different figure (a 1931 census percentage) and does not reach this sentence. Enumerations of endogamous groups vary by an order of magnitude depending on who counted and how, so the number is exactly what a specialist would ask to see sourced.
What the finding says: A demographer or anthropologist of South Asia would check it. A sufficient citation is a named enumeration with its table — the caste tables of a decennial Census of India, or the Anthropological Survey of India's People of India project (K. S. Singh, general editor), with the volume and the count it gives.

### Flag — date or dating asserted with a precision on which specialists differ
> *Prakāśa* (luminous manifestation) and *Vimarśa* (self-reflective agency) — as developed by Abhinavagupta (c. 975–1025 CE) and Kṣemarāja in the *Pratyabhijñāhṛdayam* — map directly onto the Realm of Qualities and the Field of Choice.
— `indic.md`, section: Convergence with The Non-Dual
Why it is a finding: The parenthesis gives a lifespan to the quarter-century, but the standard dating in the field, drawn from the colophons of his own works, runs roughly 950–1016, and the essay has no notes. A "c." does not make a disputed dating common knowledge; that a figure lived around the turn of the eleventh century would pass, this range does not.
What the finding says: An Indologist would check it. A sufficient citation is the discussion of his date in K. C. Pandey, Abhinavagupta: An Historical and Philosophical Study (Varanasi: Chowkhamba, 2nd ed. 1963), or a recent scholarly introduction to Kashmir Shaivism that states and defends the dates used.

### Flag — contestable historical claim stated bare
> The third error was set in stone by the British. The rigidity of the ranking was largely fixed when the colonial administration introduced birth certificates.
— `hierarchy.md`, section: Caste
Why it is a finding: A specific administrative act is named as the cause of a large historical change, with no note; the chapter's eight notes cover scripture and terminology, none of them this. The scholarly account of colonial hardening of caste centres on the decennial census and its caste tables, not on birth registration, so a historian would ask which act is meant and where it is documented.
What the finding says: A historian of colonial South Asia would check it. A sufficient citation is Nicholas B. Dirks, Castes of Mind: Colonialism and the Making of Modern India (Princeton University Press, 2001), the chapters on the census, or Susan Bayly, Caste, Society and Politics in India (Cambridge University Press, 1999), with page; and if birth registration specifically is meant, the ordinance or act by name and year.

### Flag — position attributed to a named author, especially a living one, without a work and location
> Evolution, the step beyond gnosis, is what John Vervaeke calls Participatory Knowledge — the highest form, above the propositional (logic), the procedural (method), and the perspectival (the detection of boundaries).
— `christic.md`, section: The Calculus of Reality: Fields and Qualities
Why it is a finding: A fourfold scheme and a ranking within it are attributed to a living scholar by name, with no work, lecture or page anywhere in the essay. The ranking ("the highest form") and the glosses in parentheses are the author's paraphrase, and a living author can be misrepresented in print; that is a straw-man risk and a legal one, and the reader cannot go and look.
What the finding says: The named author's own readers would check it. A sufficient citation is the specific lecture in his published series Awakening from the Meaning Crisis (2019), by episode number, or a paper of his in which the four kinds of knowing are set out, with page; and wording that separates what he says from what the essay makes of it.

### Flag — footnoted, but the note cites nothing checkable
> The world then appears to follow a mathematical law [^D4].
— `discernment.md`, section: Between Necessity and Randomness
Why it is a finding: The anchor promises a source, but the note reads: "For an exploration of the universe’s laws as evolving habits rather than fixed constraints, see C.S. Peirce’s theory of "habit-taking" in the universe." A name and a doctrine with no work and no location is not checkable; the exemption for footnoted passages does not apply.
What the finding says: A historian of American philosophy would check it. A sufficient citation is C. S. Peirce, "The Architecture of Theories," The Monist 1 (1891), 161–176, or "A Guess at the Riddle" in The Essential Peirce, vol. 1 (Indiana University Press, 1992), with page.

### Keep — the author's own coinage, as the GLOSSARY records
> The Dharma is the *Principium Separationis* — the immutable ground that kindles all distinction, providing the essential landscape of separation that allows greater becoming.
— `nietzsche.md`, section: The Will and the Field of Choice
Why it is not a finding: The italicised Latin looks like a borrowed technical term wanting an etymology and a source at first use — and it echoes Schopenhauer's principium individuationis, which a reader might reach for — but the GLOSSARY registers it as the book's own alternate name for the Field of Choice, introduced in the book's own opening voice. Internal vocabulary; there is nothing external to cite.

### Keep — a thinker's standing biography
> He wrote nothing himself. What survives is his student Arrian’s record of his lectures — the Discourses and the compressed handbook known as the Enchiridion.
— `epictetus.md`, section: Prohairesis
Why it is not a finding: An uncited historical claim about a named thinker, in an essay with no footnotes, so it superficially matches the historical-claim rule; but Epictetus's slavery, Arrian's authorship of the Discourses, and the Enchiridion as an abridgement are the standing preface to every edition, and no classicist would demand a source for them. Only the neighbouring "His name in Greek means the acquired one" edges toward the etymology rule, and even that is the dictionary sense of epíktētos.

### Keep — a settled result used in its textbook form
> This idea is not new. Noether’s theorem in Physics argues that symmetry gives rise to conservation.
— `metaphysic.md`, section: The Conservation of Nothing
Why it is not a finding: A scientific result is invoked as support with no note, two paragraphs before the entanglement-entropy sentence flagged above; but this is the theorem in the form every mechanics textbook states without citation, and the passage uses it in that form rather than extending it. Common knowledge outranks the science rule when both tests are met; the flagged neighbour fails the second test.

### Keep — already footnoted with a work and a location
> Ecclesiastes speaks it in the voice of Solomon, the wisest of Israel's kings: be not righteous overmuch — why shouldest thou destroy thyself?[^L11]
— `good-life.md`, section: Virtue
Why it is not a finding: A scripture quotation carrying a contestable attribution (Solomonic authorship) would be a double finding if bare; but the anchor sits on the passage and the note supplies work, verse and translation, and addresses the attribution itself: "[^L11]: Ecclesiastes 7:16 (KJV). The book speaks as "the son of David, king in Jerusalem" (Eccl. 1:1), traditionally identified as Solomon."

### Keep — a number offered as a hypothetical or illustration
> The man on whom the consequence falls has a name, a mother, a future that never was. The thousand who were never robbed because the law held its ground have nothing new to show.
— `kindness.md`, section: The Hard Floor
Why it is not a finding: A round count with no note, in a paragraph about a real social mechanism; but "the thousand who were never robbed" is a hypothetical, readable as such from its own sentence — no place, period or population is claimed, and the number stands for "the many". The caste count flagged above claims to be true of the world; this one does not.

### Keep — a date every reference work gives
> Basilides lived in the second century CE, in Alexandria, and survives almost entirely through the polemics of those who refuted him.
— `basilides.md`, section: Connections to Basilides
Why it is not a finding: A dating and a place, uncited, in the same essay whose account of Basilides's doctrine is flagged above; but the century and the city are what every encyclopedia entry on Basilides opens with, and no historian of Gnosticism would ask for a source. The dispute begins where the essay says what he taught, not when and where he lived.

### Keep — the declared thesis of a named work, attributed to the work as a whole
> *The Denial of Death* argued that the terror of dying is the mainspring of human character and culture.
— `becker.md`, section: The Denial of Death
Why it is not a finding: A position is attributed to a named author with no page and no note (becker.md has none), so it resembles the attributed-position rule; but the claim is the book's own declared thesis, attributed to the work as a whole and named as such, and the whole work is its location. The same essay's narrower attributions ("The therapist, he argues, has become the confessional's successor") would need a chapter or page.

### Keep — work and location given in the running text
> To individuate is to stand firmly on the ground and still the monkey mind that is accustomed to wearing a crown (Chitta Vritti Nirodha, Yoga Sutras, 1.2).
— `christic.md`, section: Beyond the Demiurge: Breaking the Ego’s Crown
Why it is not a finding: A phrase from a sacred language, freely glossed, in an essay with no notes — the sentence before the Gospel of Thomas finding above; but the parenthesis supplies the original words, the work and the sūtra number, which is a location a reader can open. Whether "the monkey mind that is accustomed to wearing a crown" is a fair rendering of the sūtra is a question of accuracy, and goes to the human fact-check, not to this lens.

### Repaired examples

*Author-made repairs mined from this manuscript's revision history (2026-09-06). Each Before is the passage as it stood at the named collected version; each After is the current text.*

### Repaired — A doctrine attributed to a tradition in a form its scholars would dispute
Before (v400):
> As new skills were found useful in the world, their qualities and metrics were identified. New categories were added [^E2]: medicine, archery, music, and architecture, each with its own standards. That is the second question answered through evolution.
After (current):
> As new skills were found useful in the world, the tradition did recognize them. Medicine, archery, music, and architecture each received a body of knowledge of its own, the upavedas,[^E2] with standards for the practitioner. But the recognition remained within the scheme of knowledge and was not adopted as a metric.
Why the before was a finding: the passage attributes to the Hindu tradition the addition of four new ranked "categories" with their own "metrics", a claim about the upavedas an Indologist would dispute, and its note ([^E2]) fixed the list of four as settled fact where the tradition's own lists vary.
What the repair did: the attribution was narrowed to what the reference works state (the upavedas as bodies of knowledge that confer no varna), and the note now names the variant list ("some lists give *Arthaśāstra* in place of architecture").

## The lens

You are looking for borrowings that arrive without a source a reader could check: quotations (epigraphs and unmarked close paraphrase included), translations and etymologies, doctrines attributed to traditions or figures, scientific results, statistics and counts, dates, contestable historical claims, and positions attributed to named authors. The GLOSSARY decides which terms are the author's own coinages; those need no external source. Inside PROTECTED REGISTERS, flag only a quotation attributed by name to a real work; the rest of that voice asserts by design. A source anywhere in the chapter, text or note, covers the passage; one in another chapter does not, though the finding may say where it lives.

This lens tests the presence of a checkable source, not the truth of the claim. A sourced claim you believe false, or a note whose location may not contain the quoted words, belongs to the human fact-check; do not report it. The sentence's mood and the placement of its anchor belong to the SENTENCE-LEVEL PASSES.

A sufficient source is a work and a location — section, verse, page, saying, edict, table — and, for a translated quotation, the translator or edition. A bare title, a year, a name, or "see the literature" does not pass.

Common knowledge outranks every bullet below, on a two-part test: would the standard reference work or textbook of that field state it without citation, and does the passage use it in that standard form? A textbook theorem, a dictionary gloss, a thinker's standing biography, a date every encyclopedia gives pass; an extension of the result, a contested reading, a figure or dating on which specialists differ do not.

Flag a passage when:
- it quotes a text, in an epigraph or in the body, and names no work;
- it quotes a text and names a work but no location — or, for a translation, no translator or edition;
- it closely paraphrases a famous line without marking it as borrowed; treat this as a quotation;
- it gives the meaning or derivation of a word from a classical or sacred language as established fact;
- it attributes a doctrine or a starting point to a tradition or historical figure in a form a scholar of that tradition might dispute;
- it invokes a scientific result as settled support when the result is a research position, not the textbook form;
- it states a count, statistic or magnitude as a fact about the world;
- it asserts a date or dating with a precision on which specialists differ;
- it makes a historical claim that a historian of that period might dispute;
- it attributes a position to a named author ("X argues", "what X calls") with no work and location — sharpest for a living author, where a misstatement is a straw man and a legal exposure;
- it carries a note, but the note names no work and no location — a name or a doctrine alone is not checkable.

Refuse to flag:
- the author's own coinages and any term the GLOSSARY registers as the book's;
- a thinker's standing biography, or any fact the field's reference works state without citation;
- a settled result used in its textbook form;
- a passage whose note gives work and location (and translator, for a translation);
- a number offered as a hypothetical or illustration and readable as such from its own sentence;
- a date every reference work gives;
- the declared thesis of a named work, attributed to the work as a whole — the work is the location;
- a passage whose running text gives work and location, with the original words where the gloss is free.

For each finding, state the passage (file and sentence), the category from the list above, which reader would check it (a classicist, a physicist, the named author's own readers), and what a sufficient citation looks like — a work and a location, not "see the literature". Name a real work only when you are sure it exists; otherwise describe the kind of source (a critical edition, a census table, a dictionary entry). Never invent a citation. Where a source is wanted and you cannot supply a verified one, request it: set the finding's `footnote` to what a sufficient citation looks like, and the harness plants a `[Footnote: …]` tag after the sentence for the author's footnote road to draft. Then stop.

REWRITE OR JUDGMENT (ruled 2026-09-06). Every finding proposes its repair as
`replacement` whenever the chapter, the GLOSSARY, and the target chapters
supply what the sentence needs: a wrong word, a misstated credit, a missing
clause, a dropped scoping, a frame sentence, a corrected pointer. Reserve
`judgment` for a repair that is a decision the author alone can make — cut
or move a section, choose between two claims the chapter makes, supply a
fact the payload does not contain, add an argument that does not yet exist —
and state that decision in one clause. A finding with neither is a note the
author cannot act on in the Doc, and is a fault of the reply.
```

## `validity-and-objections`

```markdown
---
class = "cross-chapter"
inputs = ["glossary", "registers", "passes"]
targets = ["pointers"]
examples = "prompt"
---
# Does the conclusion follow, and is the strongest objection met

*Examples are illustrations drawn verbatim from one manuscript; the rules beneath them are general. Each Flag pairs with a Keep that superficially resembles it.*

## Examples

### Flag — The conclusion needs a premise the chapter never states
> Choice is not something life happens to possess — it is the principle by which anything exists at all. If a standard must be derived from reality, and if all intentional existence is constituted by the Field of Choice, reality's own candidate must be the good choice, repeated.
— `good-life.md`, section: The Good Choice, Repeated
Why it is a finding: The two stated premises say what a standard must be derived from and what existence is made of; the conclusion says what the standard *is*. The step needs a third premise the chapter never states: that whatever constitutes existence is also what makes an existence good. Nothing stated rules out a standard derived from a choice-constituted reality that nevertheless measures some quality (the "thriving" the previous section called the evidence) rather than choosing itself.
What the finding says: Conclusion quoted; the two premises the chapter gives; the missing premise named in one sentence ("what constitutes existence is the measure of its goodness"); and that the chapter must either state and defend it or narrow the conclusion to "a candidate".

### Keep — A premise supplied by an earlier chapter
> The opposite of the good life is the habitual life. Whatever its content: reflexive obedience, reflexive concealment, reflexive opposition — the habitual life has ceased to choose in every moment. That is the slavery the Sermons name, and the end of the good choice. The terminus of slavery is not defeat. It is extinction.
— `good-life.md`, section: The Good Choice, Repeated
Why it is not a finding: The step from "has ceased to choose" to "extinction" needs a premise this chapter does not state, which looks like the flag above. But the other chapters' text show the essay on discernment established it three chapters earlier (repetition admits no variation, possibilities go unexplored, evolution ends, extinction follows), and Sermon Four asserted it ("That which aligneth not, perisheth"). A premise the reader has already been given is not hidden; the finding, if any, is a cross-reference one.

### Flag — A step holds only if a term keeps one sense, and the term has changed sense
> Partition does not end conflict; the record above says so. It moves conflict from every home to a border, and conflict at a border is still the lesser evil, because a population behind a border can still rank its own. The argument against partition is an argument against expecting peace from it, never against separating populations that cannot converge.
— `mixing.md`, section: Discord: Partition
Why it is a finding: Three paragraphs earlier the same section defines the partition the record refutes as "the separation of the populations into territories each can defend." The closing sentence holds only if "partition" and "separating populations that cannot converge" name two different things, and the chapter's own definition makes them one. The evidence conceded against the first is therefore evidence against the second, and the chapter's remedy (stated here and in two earlier essays) is exactly what the record was said to refute.
What the finding says: The term ("partition"); its two senses (territorial separation after war; separation of populations as a remedy); the sentence where the sense shifts; and that the conclusion survives only if the chapter can say what separates the two cases, which it has not.

### Keep — A term the chapter itself splits into two named senses and keeps apart
> This puts truth and falsehood in their true contrast at last. A falsehood, in the ordinary sense, misstates what is. A falsehood in the deeper sense forecloses what might yet be
— `good-choice.md`, section: The Two Truths
Why it is not a finding: "Truth" and "falsehood" each carry two senses across this argument, which is the shape of an equivocation. But the chapter names both senses, marks which is in play at every step ("small *t*", "capital *T*", "ordinary sense", "deeper sense"), and draws its conclusion from the contrast between them, not from a silent slide between them. Equivocation requires the shift to be unmarked.

### Flag — An objection answered in a weaker form than a critic would give it
> And yet, they cannot explain the initial condition — the Big Bang. If they manage to conceive of a cyclic universe, they cannot get past the fine-tuning problem. They require at least one miracle to justify their claim.
— `redemption.md`, section: The Deterministic Bargain
Why it is a finding: The determinist's claim, as a determinist would state it, is that each state of the world fixes the next; it says nothing about the first state and owes no account of it. The chapter refutes a determinism that must also be a complete cosmogony, which no serious determinist defends, and then draws the consequence ("Worse, this is a universe without hope") as though the position had fallen.
What the finding says: The objection as the chapter states it; the objection as a critic would state it (a law of succession that is silent on origins, and compatible with the compatibilist's account of responsibility); the sentence where the weaker form was substituted; and that the chapter's material answers only the weaker form.

### Keep — An argument from stated definitions whose conclusion is conceptual
> The grand terminus is false just as determinism is, for Choice, being immutable, is never silenced. But the bargainer does not consult metaphysics; he consults his fatigue.
— `redemption.md`, section: Perfection: The Third Bargain
Why it is not a finding: This too dismisses determinism in a sentence, but the step is from a definition the GLOSSARY fixes (the Fields are immutable; determinism is the belief that leaves no room for Choice) to a conclusion that is conceptual (no final state in which Choice is silenced). It is valid by construction. Whether the definition fits the world is a question of reach and belongs to evidence-reach, not here.

### Flag — An objection raised and left unanswered without saying so
> And there are those who genuinely cannot bear a consequence. For them the floor is not a floor but a blade, and a mercy without discernment, given to them, is better than a discernment without mercy.
— `kindness.md`, section: The Strongest Objection
Why it is a finding: The objection has three legs and this is the third, stated at full strength. The two replies that follow (a mercy must name the date it ends; the test is verified afterward in the count of good lives) answer the first two legs and do not reach a person for whom no end-date and no later count can help. The section then moves to a different claim (that populations needing different floors should be separated) without saying that the third leg stands.
What the finding says: The leg left standing, quoted; the replies given and which legs they meet; and that the chapter owes either an answer or a sentence conceding that its standard is limited to those who can bear a consequence.

### Keep — Sections in a PROTECTED REGISTER
> “Thou speakest against names and identity, yet surely thou hast one, for thou art not as we are! Reveal thy name, that thou mayest be humbled!”
— `sermons.md`, section: Sermon Four
Why it is not a finding: The objection closes the sermon unanswered, which in an essay would be the flag above. But the section is in a PROTECTED REGISTER: its voice asserts and its interlocutors object by design, and the answer, when it comes, comes in the next sermon as part of the form. The lens exempts it by rule and does not weigh the objection's strength.

### Flag — A "therefore" that is a restatement
> The one spared pays later, in installments, in capacity, at compounding rates. Most false kindness is therefore a transaction in which the giver takes the benefit and the recipient is billed. The fate of the transaction turns on who sees through it.
— `kindness.md`, section: Who Pays
Why it is a finding: The "therefore" sentence repeats the two sentences before it (the giver is relieved now; the one spared pays later) and adds nothing; no inference has occurred. It matters because the section opened by making "who pays" the test for whether a kindness kills: if the kindness that kills is *defined* by the giver's benefit and the recipient's bill, watching who pays cannot detect it, and the test is circular.
What the finding says: The conclusion; the sentences it repeats; and that either a premise must be added (some independent mark by which the giver's relief can be told from the recipient's gain) or the sentence should drop its "therefore" and stand as a definition.

### Keep — A figure carrying a step
> The good life may cast happiness or reduce suffering, as warmth is cast by a flame, but new agency empowered by that life — within and in others — is the real gain. Therefore, to pursue warmth and not the fire is to mistake the half for the whole.
— `good-life.md`, section: The Avoidance of Suffering
Why it is not a finding: The "Therefore" looks like the restatement above, but a step is taken: from "agency is the gain and happiness its by-product" to "pursuing the by-product is an error". The stated premise does yield the conclusion. What carries the weight is the flame-and-warmth image, and whether an image may carry a step is illustration-as-argument's question, not this lens's.

### Flag — A concession that gives up the conclusion or answers a different objection
> Yes, the subtlest theologians, Gregory of Nyssa among them, deny that heaven stands still and preach an endless deepening.[^RD4] The quarrel here is not with the word *heaven*. It is with the promise underneath it: that somewhere, the cost of change is finally waived.
— `redemption.md`, section: Perfection: The Third Bargain
Why it is a finding: The objection (the serious theology of heaven is not a final state) is conceded, and the reply moves the target to "the promise that the cost of change is waived." But an endless deepening waives no cost, so the counterexample is untouched by the new target; the concession answers an objection no one raised (that the word is at fault) and quietly gives up the section's claim that "Paradise, Utopia, *Swarga*, and Heaven" name the perfection bargain, which the section goes on using unrevised.
What the finding says: The claim as stated before the concession; the claim that survives it (heaven conceived as stillness); the objection the reply actually meets; and where the conclusion must be narrowed to match.

### Keep — An objection the chapter concedes in its own voice and marks as the limit of its claim
> The iconoclast is right when a form has become the terminus, when it feeds the ego, or when it freezes Choice. He is wrong when he destroys a functioning rung merely because it is a rung.
— `god.md`, section: The Criteria
Why it is not a finding: This concedes the objection's true part as fully as the flag above, but it does so in the chapter's own voice, says exactly where the concession stops, and the chapter's conclusion (a defense of the climber and the rungs, not of God) was scoped from the opening to survive it. A concession that costs the conclusion nothing it still claims is the answer to this lens, not its target.

### Repaired examples

*Author-made repairs mined from this manuscript's revision history (2026-09-06). Each Before is the passage as it stood at the named collected version; each After is the current text.*

### Repaired — An objection raised and left unanswered
Before (v412):
> But there is yet another related objection - that continued expression and survival is not enough to meet the requirements of ***The Good Choice***. The question is whether an arrangement enlarges Becoming and whom it diminishes. Oppressive systems can survive extremely well. [insert: suggest an argument]
After (current):
> This answers the objection that survival is the proof, and it is an objection the essay's own example invites. The Hindu civilization has survived with caste for longer than most civilizations have existed, and if survival were Nature's verdict, the evidence would favor caste. But oppressive systems can survive extremely well, and a corpse can stand a long time propped in a doorway.
Why the before was a finding: the objection that survival proves nothing was raised against the chapter's own first measure ("whether the population survives") and then left standing, its reply an editorial placeholder; a leg of the objection stood in silence.
What the repair did: the objection was stated at the critic's strength, with the chapter's own example (caste's longevity) turned against it, and answered: survival is the precondition for being measured, and the verdict is in the direction of the ranked's capacity to choose.

### Repaired — An objection answered in a weaker form than a critic would give it
Before (v412):
> A claim about intelligence, or religious superiority, or racial superiority is answered by whether the ranked members go on expressing, generation after generation, a quality the members have stopped earning. A claim of Nature's law is answered by the flaws Nature exposes in it, and by whether the population survives.
After (current):
> Continued expression proves nothing by itself. A hereditary elite may go on expressing competence for generations because it has kept the schools to itself. The measure is not whether the quality persists in the ranked, but whether the rank still answers when it does not.
Why the before was a finding: the objection, as its best proponent states it, is that every hierarchy can assert Nature's verdict and none can be shown to lack it; the reply's measures, continued expression and survival, are exactly what a hereditary elite that keeps the schools or a caste order that has stood for centuries can show, so the reply met a weaker objection than the one raised.
What the repair did: the measure was recast so that the critic's own cases fail it, from whether the quality persists to whether the rank still registers when it does not.

### Repaired — A step that holds only if a term keeps one sense
Before (v400):
> Populations in different locations care about different qualities, or the same qualities in different measure, and their rankings are not commensurable: neither can express or experience the other's quality well enough to be measured by it. A hard floor laid across both is therefore not one floor.
After (current):
> Populations in different places come to care about different qualities, or the same qualities in different measure. Where both can express the quality and merely weigh it differently, the difference is discord, and discord can be negotiated.
Why the before was a finding: commensurability was defined in Populations as "the mutual ability to express and experience a quality of interest in each other", yet here valuing a quality "in different measure" is equated with not being commensurable, and the "therefore" about the floor rests on that shifted sense.
What the repair did: the term was split into two named senses and kept apart, discord (same quality, weighed differently, negotiable) and incommensurability (the quality cannot be expressed at all), with the floor conclusion narrowed to the second.

### Repaired — The conclusion needs a premise the chapter never states
Before (v412):
> The second condition is that quality reflects what is valuable for the individual and the population as a whole to live a good life. This reflection happens as often as we exercise the system of ranking. The more the population tries to improve, the more it is worth the cost; if not, a more appropriate quality must be used instead.
After (current):
> The second condition is that the quality reflects what is valuable for the individual and the population together to live a good life. The verification is the standard the book has used since the essay on The Good Choice: whether those who climb the ranking, those beneath it, and those beside it can choose more than they could before.
Why the before was a finding: the condition concludes that a wrongly chosen quality must be replaced, but states no test by which "valuable" is decided; "the more the population tries to improve, the more it is worth the cost" restates the condition rather than supplying the premise.
What the repair did: the missing premise was stated, the enlargement-of-choice standard, and credited to the earlier chapter that supplies it.

## The lens

You are looking for the places where a chapter's conclusion does not follow from what the chapter states: a premise it needs and never gives; a term that changes sense between the steps of one argument; a "therefore" that repeats rather than infers; and, in the chapter's handling of objections, an objection stated weaker than a critic would state it, an objection raised and left standing without acknowledgement, or a concession that gives up the conclusion or answers something no one asked. Use the GLOSSARY for a term's settled sense, so that a shift of sense can be told from a stipulated one. Sections in a PROTECTED REGISTER are exempt. A single sentence's fault belongs to the SENTENCE-LEVEL PASSES; this lens reports only what a whole argument shows, and points there.

Begin by writing out, for each major conclusion the chapter draws, the premises the chapter actually states for it, in the chapter's words, and every objection it raises against itself. Judge from that list.

Flag a passage when:
- **The conclusion needs a premise the chapter never states.** Name the premise in one sentence. If it is contestable, the chapter must state and defend it; if it is not, the chapter can add it.
- **A step holds only if a term keeps one sense, and the term has changed sense.** Quote the sentence where the sense shifts and give both senses. A stipulated second sense the chapter announces is not a shift.
- **An objection is answered in a weaker form than a critic would give it.** State the objection as its best proponent would, then show which form the chapter's reply meets.
- **An objection is raised and left unanswered without saying so.** Count the legs of the objection and the legs the replies reach. A leg left standing in silence is the finding; a leg conceded in as many words is not.
- **A "therefore" is a restatement.** The sentence after the connective repeats the sentences before it. Flag it only where the restatement is then used as a result, or where it makes a test circular.
- **A concession is a surrender or a dodge.** It gives up what the conclusion still claims, or it concedes something the objection did not raise while the raised point stands.

Refuse to flag:
- **An argument from stated definitions whose conclusion is conceptual.** Valid by construction, whatever its reach; whether the definitions fit the world belongs to evidence-reach.
- **An objection the chapter concedes in its own voice and marks as the limit of its claim.** The concession is the answer to this lens.
- **A premise supplied by an earlier chapter.** That chapter's own text says what the reader has been given; name the chapter. Whether the pointer to it is adequate belongs to cross-references.
- **A term the chapter itself splits into two named senses and keeps apart.**
- **A figure carrying a step.** Whether an image may bear an argument's weight belongs to illustration-as-argument. Ask here only whether the stated premise yields the conclusion.
- **Sections in a PROTECTED REGISTER.**

For each finding, state four things and stop:

1. **The conclusion**, quoted.
2. **What the chapter actually gives for it**: the premises stated, the objection as stated, the replies as stated.
3. **What is missing or has moved**: the unstated premise named; the term and its two senses; the objection at a critic's strength; the leg left standing; the sentences the "therefore" repeats; the claim the concession gave up.
4. **The smallest repair**: a premise stated, a sense held, a leg answered or conceded, a conclusion narrowed. Prefer the concession where the chapter's material cannot answer: a gap conceded in the chapter's own voice is no longer a gap a reader can open against it.

Where the strongest objection a critic would raise against the chapter's main conclusion appears nowhere in the chapter, say so in one closing sentence, at the strength a critic would give it. Where that objection simply *is* a gap another lens has found (a claim its evidence does not reach, an image doing a premise's work), say so plainly: the chapter owes it a statement and either an answer or a concession.

REWRITE OR JUDGMENT (ruled 2026-09-06). Every finding proposes its repair as
`replacement` whenever the chapter, the GLOSSARY, and the target chapters
supply what the sentence needs: a wrong word, a misstated credit, a missing
clause, a dropped scoping, a frame sentence, a corrected pointer. Reserve
`judgment` for a repair that is a decision the author alone can make — cut
or move a section, choose between two claims the chapter makes, supply a
fact the payload does not contain, add an argument that does not yet exist —
and state that decision in one clause. A finding with neither is a note the
author cannot act on in the Doc, and is a fault of the reply.
```
