# Verse and Cast — Design Reference

Drafted 2026-09-24 from a grilling session on The Dharma of Nothing (DON);
**ratified 2026-09-25** section by section in conversation ("Rest looks
fine", "Sounds good"). Status: **ratified, nothing built.** Every ruling
below is the author's unless marked *proposed*. Five tool changes follow
from it (§10, §15); this document is their build reference, entered in
`design-backlog.md`, in the house pattern for designed-not-yet-built work.

Related designs: `illustration-placement-design.md` (the slot, pick, and
placement machinery this extends), `sweep-framework.md` (where the checks
run), `margin-threads-design.md` (the `<<old>>{{new}}` grammar the poem
rewrites use), `audiobook-pipeline-design.md` (the paragraph as the unit of
audio, which a stanza becomes).

---

## 0. What this covers

A verse manuscript differs from the essays in four ways the tool does not
yet know: its lines break inside a paragraph; every poem carries an image;
the images share a recurring cast that must look the same across eighty
renders; and the printed page is a fixed spread rather than a flowing
column. This document settles each, and the seam between what the tool can
enforce and what only a reader can judge.

The first manuscript is DON: 87 poems in ten topical files, one file per
section, poems as `##` headings, each with an italic one-line thesis. The
format is commented sutras: poem, then a short prose commentary (bhashya).
Audio-first, then print and Kindle. Author metadata is Chinmay Nagarkar
alone; the byline is the pen name Chitta Darshana.

## 1. The verse flag

One toc attribute marks a file as verse, beside `register` and
`illustrations`:

```toml
[[chapter]]
file = "error.md"
form = "verse"
```

The lint, the sweep, the export profile, and the placement scan key off it.
Prose files are untouched by anything in this document.

**What the flag decides, and what it does not (ruling 2026-09-25).** Three
things are kept apart so the flag never grows into a pile of code paths:

- **Form** says what a *unit* is. Prose: the paragraph. Verse: the stanza,
  with hard breaks inside it. A future play or dialogue form: the speech.
  This is all the flag decides. It is a small enum that grows only when a
  genuinely new unit type appears, because it changes what collect, lint,
  filters, doc threads, and audio treat as one thing.
- **Layout** says how units land on pages: `spread`, `inline`, `facing`,
  `one-per-page`. A per-manuscript setting beside trim, bleed, and type
  size (§8), never a file attribute. A second verse book keeps the stanza
  as its unit and chooses its own page; a new verse style is new values
  here, not new code.
- **Style law** says how the words behave: rhyme, archaism, thesis lines.
  That is the existing style system and needs nothing new.

A haiku collection tomorrow is `form = "verse"`, `layout = "one-per-page"`,
and its own guide.

**Shipped 2026-09-25.** `form = "verse"` is read by `authorlm/verse.py`
(`form_map`, `is_verse`), the one seam that knows what a poem is: a
`##` heading and everything to the next heading (`poems_of`: title,
thesis line, span, hash; `frame_of` for the section title and epigraph;
`stanzas_of`; `find_poem` by number, title, or unambiguous fragment).
DON's ten poem files carry the flag in `toc.toml`.

**The poem heading level is declared, not assumed (author's question,
2026-09-25).** A per-file toc attribute `poem_level` (1–6, default 2)
beside `form` says which heading opens a poem; `verse.poem_level` reads
it and every poem-aware verb passes it to the splitter. Every collect
runs `verse.verse_report`: each verse file's poem count at its level,
and a WARNING when a file marked verse has no poems there, naming the
levels headings were actually found at ("headings found at level 3 (12)
— set poem_level in toc.toml"). So a wrong level shows on import, when
the fix is one line, not when a lens refuses.

## 1a. The poem grain of a lens (ruled and shipped 2026-09-25)

The author's objection: a poem-level finding anchored only by where its
quote fell "is not a reliable mechanism." So the lens runner gained a
second axis beside `class`:

| Front matter | Values | Meaning |
|---|---|---|
| `class` | `chapter`, `cross-chapter` | Against itself, or against other text (unchanged) |
| `grain` | `chapter` (default), `poem` | The unit block E holds: the whole file, or one poem |
| `targets` | the four, plus `siblings` | At poem grain, the other poems of the same file, full text |

Enforced in `authorlm/lenses.py`: `siblings` needs `grain = "poem"`;
a poem-grain lens refuses a file not marked verse; `lens run <lens>
<file>` at poem grain assembles one payload per poem (`assemble_poems`;
`--out <dir>` writes them, `--poem <n|title>` takes one); block E holds
the section frame as context and the one poem as the subject; a
finding's quote must be verbatim inside THAT poem or it is dropped as
ungrounded; each stored finding carries the poem's title, number, and
hash; `lens status` calls a batch stale only when its own poem changed,
never when a sibling was reworded. Eighteen checks in
`tests/test_api.py`. The two lens artifacts (§10, change 4) are written
against the ratified verse law and are not yet installed.

## 2. Hard line breaks (the stanza is the paragraph)

**Finding (2026-09-24).** The poems were stored one line per line with a
blank line between stanzas. Markdown treats a single newline as a soft
break, so the Doc push ran each stanza into one sentence. A round-trip probe
through Google's own converter showed: bare newlines join; a trailing
backslash or two trailing spaces import as true line breaks inside one
paragraph; the export comes back with two trailing spaces; `<br>` is
mangled. The obstacle is ours: the canonical-markdown pass strips trailing
whitespace, and the files never had hard breaks.

**Ruling.** The canonical form for verse is the backslash hard break: every
line in a stanza ends with `\` except the last; stanzas stay separated by a
blank line. Pandoc renders it as a line break in EPUB, PDF, and DOCX;
Obsidian renders it; Google imports it correctly. A stanza is therefore one
paragraph, which is the right unit for filters, lenses, lint, doc threads,
and audio.

Consequences: the canonical-markdown pass converts an exported two-space
break to a backslash break *before* it strips trailing whitespace; the audio
export drops the backslashes before speech; the lint does not count them.

**The Doc road for verse forms (found and fixed 2026-09-27, first lens push
on a verse file).** The tab holds a stanza's breaks as U+000B line-break
characters inside one paragraph, and the markdown export closes and reopens
strikethrough at every one of them inside a struck span. Two seams carry the
difference: `gdocs.render_emphasis` (and so `rendered_text`) maps a
backslash break to the line-break character, so the locator finds a stanza
and the writer inserts one; `threads.pending_forms` drops the exporter's
`~~` fragments at hard breaks, so the export proof and the resolve read the
old half back verbatim. Eight of nine Prologue forms landed on the first
push after the fix; two more wait behind paragraphs another form already
claims.

**Judgments plant as insertions (author ruling 2026-09-27, "lets plant
judgement that way in the lighter form").** A judgment finding no longer
strikes its unit to append the tag; the tag alone goes into the tab as a
green insertion paragraph after the unit (`findings.store_findings` stages
an insert thread; `lenses.one_per_unit` keys forms by kind and paragraph
so a tag may sit beside one rewrite of the same stanza; the insertion's
anchor is located as the tab renders it). At `lens resolve` the tag is
ruled by presence, braced or bare: present accepts the finding and the
tag FOLDS back onto the end of the paragraph it follows, where the file
has always carried an open judgment and where `lens repair` looks; gone
rejects it. The lighter form is the tab's; the record's form is
unchanged.

**A judgment states options, not one clause (author ruling 2026-09-27).**
The first Prologue run planted tags like "rhyme line four with line two, or
recast the stanza as a parallel litany" and "keep the inversions, or unbend
them and re-rhyme the two couplets"; the author: they "could include more
information or examples or suggestions", and two needed explaining in chat.
The lens contract (`lenses.LENS_SYSTEM`) now asks for two or three concrete
options tied to the words they touch, with a candidate word, rhyme, or cut
where one is obvious, in at most two sentences and without brackets or line
breaks (the tag grammar). The reasoning stays in the finding's `note`.

## 3. The cast

**Ruling.** The images share a cast of symbols. They tell a story; they are
not people. The human element is present without portraiture: figures are
recognized by posture and attribute, never by facial likeness or
expression.

| Symbol | Signifies | Notes |
|---|---|---|
| The Seeker | Consciousness: the reader, or the author; the human element | A particular configuration of choices. First appears on the Journey plate, never before the circle and the point |
| The Dragon | The mind | Nietzsche's dragon of fire and gold; "the first Dragon feeds on children" |
| The Chain | The ego | Appears only linking the Seeker to the Dragon (*proposed rule*, unopposed) |
| The Ship | The body | The Seeker's vessel; "I captained the waves" |
| The Tower of Light | Enlightenment | Sighted, not reached; the lighthouse of the guru |
| The Empty Circle | Nothing | The ground everything steps out of |
| The Point | Choice | The first mark on empty paper; "No army of points can create a line" |
| The Tree | All of existence | The inverted Ashwattha, roots above and branches below: every choice made and every trajectory traversed |

One-off images (the mirror, the tower of turtles, Shiva's dancer, the
Matrix) stay one-offs. A poem's image uses at most two cast members plus
its section's stage. The Realm of Qualities has no symbol: ink tone carries
it, as the tradition's "five colors of black."

## 4. The storyboard (one stage per section)

Fixed before any poem is illustrated; every poem's image is constrained by
its section's stage. **Ruling 2026-09-25: no cast plate and no opener plate
appears in the book.** The stage plates below are rendered into the cast
file as unprinted references (they serve as `prior:` images, §5); the
reader meets the arc only through the poems' own images, in sequence.
Section openers are plain title pages with the epigraph.

| Section | Stage plate (reference only) | Stage |
|---|---|---|
| Prologue | Empty paper; one point | Nothing and the first choice; no Seeker yet |
| The Journey | The Seeker steps onto the Ship | The reader sets out; the body carries them |
| Error | The Dragon rises from below deck | The mind, met |
| Words and Expression | The Dragon breathing a stream of marks, not fire | Words as the mind's exhalation |
| Relegere | The Seeker chained to the Dragon; the ship adrift | Ego binds consciousness to the mind |
| Wisdom | The chain slack on the deck; Seeker and Dragon face each other | Discernment begins |
| The Path to Mastery | The Seeker on the Dragon's back; the Tower on the horizon | Mastery; enlightenment sighted |
| Metaphysics | The circle; inside it a point, a line of points, the inverted Tree growing down from them, the Seeker among the branches | Nothing, Choice, existence as the record of choices |
| The Good Life | The Ship moored beneath the Tree's hanging branches; the Seeker walking ashore | A life lived inside existence |
| Remainder, Cast file | No plate | Set-aside material carries no story |

In Relegere the Seeker is always chained; on the Path, never.

## 5. The cast file and the tag grammar

**Ruling.** The cast is a first-class entity, kept as an essay-like file
`illustration_cast.md`, back matter, with the Omit region on its first line
(`[Omit: pdf, docx, epub, md, audio]` … `[/Omit]`), exactly as
`remainder.md` does. It pushes and pulls like any chapter; its tags are
ordinary slots; `illus render` and `illus pick` work unchanged; the Omit
keeps its plates out of every output. Its plates never appear in the book
(ruling 2026-09-25): the file holds the eight cast members and the ten
stage plates as references for rendering only. Rendered plates are
reviewed in the gallery artifact.

Tag grammar (an extension of the existing `[Illustration: … | caption: …]`
form):

- `[Illustration: <description> | cast-id: dragon]` **defines** a cast
  member or an opener plate; the id is a slug, unique in the manuscript.
- `[Illustration: <description> | cast: seeker, dragon]` **references** one
  to three ids. The pinned pick of each named slot is the file sent with the
  render. An unpicked cast slot is a render-time error, which forces the
  plates to come first.
- `| prior: relegere-opener` names one plate to follow for stage and
  composition, distinct from `--from` (which means "edit this image").
- A tag with no `cast:` is permitted and renders on words alone.

Explicit ids were chosen over matching capitalized names in the prose:
deterministic, checkable, and a typo is a reported error rather than a
silent omission.

**Stale cast.** Render metadata records the plate files used. Re-picking a
cast plate marks every dependent slot `stale-cast` in `illus status`
(beside the existing `stale-desc`) and offers a re-render of exactly those
slots. Without this the cast drifts silently, which is the failure the
design exists to prevent.

## 6. The reference mechanism and the prompt

**Vendor facts (2026-09-24).** The illustration model pinned in config is
`openai/gpt-image-2`; SMSTTD's plates carry it in their metadata. The
renderer today passes one input image, and only to Gemini; OpenAI renders
go through a text-only generation call. OpenAI's image *edits* endpoint
takes up to 16 input images for the GPT Image models, gpt-image-2
included, processed at high fidelity with no setting to turn. Gemini 3 Pro
Image takes up to 14 (6 objects, 5 characters); Gemini 3.1 Flash Image adds
3 style references.

**Ruling.** Build on the OpenAI edits endpoint with N references: same
vendor as SMSTTD, the higher ceiling, and one fewer per-manuscript setting.
Gemini stays available by explicit request.

**The composed prompt**, deterministic and inspectable with `illus prompt`:

1. The ratified illustration law, as today.
2. A reference block, identification only, one short line per attached
   image, in attachment order: "Image 1: the Dragon of this book. Image 2:
   the Seeker. Image 3: the previous plate in this series; keep its stage
   and composition." Meaning ("the mind", "consciousness") is *not* sent:
   likeness is the model's business, meaning is ours. Whether the block
   helps, and how much, is decided by the trial (§9), not assumed.
3. The tag's own description, as today. The poem's verse is never sent.
4. The files, attached in the order the block names them.

PNG metadata records the plate files used (§5).

## 7. The per-manuscript illustration pin

**Ruling (ratified in session, 2026-09-24).** A style is model-bound.
Each manuscript pins its illustration `model` and `image_size` in a
per-manuscript settings file, written automatically at the first render
from the global default, read on every render after, changed only by an
explicit command. A render on any other model or size stops and says so;
an off-model render happens only by a named flag and the result is marked
off-model in status. **No automatic fallback**: a fallback render is the
silent style break the pin exists to prevent, arriving on the day the
primary vendor is down. The global setting affects what a *new* manuscript
starts with and nothing else.

DON's pin: `openai/gpt-image-2`, `1024x1536` (2:3 portrait). File:
*proposed* `<manuscript>/_illustrations/settings.toml` (does not exist yet);
the global default it is copied from is `authorlm/config.toml:109`.

## 8. The printed page

**Ruling.** Trim 6 x 9, no bleed, matching SMSTTD on the shelf; the plates
are the print edition's argument. Black ink only: sumi-e is monochrome, and
a single color accent would force a color interior at several times the
per-copy cost. (The earlier proposal's vermilion seal accent is withdrawn.)

**The spread.** One poem per spread: the full-page image on the verso, the
poem opening on the recto with heading, thesis, verse, and commentary. The
reader never asks which poem an image belongs to. Section openers carry no
plate: a right-hand title page with the epigraph, the verso before it
blank when the count falls odd, as in any book. Kindle reads image,
heading, thesis, poem, commentary in order; the spread does not exist
there and needs only a CSS rule so each plate starts a screen.

**Pagination rules, from trade practice.**

- One type size for the whole book; no poem or commentary is ever shrunk
  to fit.
- A poem that outruns its page turns over onto the next page, breaking
  only at a stanza break and never stranding a single line.
- Blank versos are conventional (before a section opener when the count
  falls odd); blank rectos never.
- Plates carry no folio or running head; the facing text page carries
  both.
- The unit is two pages. When it cannot be, it is four, which keeps the
  left-image right-poem pattern for every poem that follows:

| Case | Spread 1 | Spread 2 |
|---|---|---|
| Poem and commentary fit the recto (most poems) | image; poem with commentary below | none |
| Poem alone exceeds the recto | image; the poem's first stanzas | rest of the poem on the verso; commentary on the recto (up to about 300 words) |
| Poem fits, commentary does not | not allowed: it would make a blank recto. The commentary is cut to fit, or the poem takes the four-page unit with the commentary given the whole facing recto | |

**Type size and layout are per-manuscript settings (ruling 2026-09-25),**
beside trim and bleed in the export settings, never file attributes:
`type_size` (*proposed default* 11 pt on 15 pt leading, one size for verse
and commentary, headings by the template's scale) and `layout = "spread"`.
The commentary budget is computed from them.

**Commentary budget.** From the trim, the type size, and the poem's line
count the tool computes a word budget per poem and reports it; short poems
earn longer commentaries. The 120-word cap in the style proposal becomes
this computed number.

## 8a. Resolution (ruled 2026-09-25, "Do both")

The pinned render size, 1024 x 1536, is the largest gpt-image-2 produces
and is short of print resolution for a full 6 x 9 page (171 pixels per
inch against KDP's recommended 300 and flagged-below 200). Two measures,
both in the export road, neither touching the renders or the picks:

1. **Plates print inside the type area**, never edge to edge (bleed is
   off in any case): roughly 4.5 x 7 inches at this trim, which alone
   lifts the plate to about 220 pixels per inch.
2. **Plates are upscaled 2x at export**, to 2048 x 3072, by a local
   upscaler in the PDF book profile (Real-ESRGAN or equivalent; plain
   resampling as the fallback when the model is absent, reported not
   hidden). Ink wash upscales gracefully; a neural upscaler keeps the
   dry-brush edges. The upscaled file is a build product under
   `_exports/`, never a candidate; the manuscript's plates stay as
   rendered. EPUB and Kindle use the rendered size unchanged.

Enforcement joins §9 as *checked*: the export reports each plate's
effective pixels per inch on the page and stops below 200. Setting:
`upscale = 2` beside `type_size` and `layout` in `_exports/settings.toml`
(*proposed*, does not exist yet; tool change 3).

## 9. How the constraints are enforced

Three kinds, honestly separated. *Structural*: the layout code cannot
produce the violation. *Checked*: a zero-token sweep reports it at collect
or before export. *Judgment*: a lens reads for it because no rule can.

| Constraint | Captured in | Enforced by | Kind |
|---|---|---|---|
| Spread: image verso, poem recto | Export book profile, verse branch | Template code | Structural |
| Plates carry no folio or running head | Export template | Same | Structural |
| One type size | Export template | Markdown has no size markup | Structural |
| Turnover at stanza breaks only; no stranded line | Export template | A stanza is one paragraph kept together on a page | Structural |
| Kindle order | Tag position between thesis and verse | Sweep checks position | Checked |
| No blank recto; even page count per poem unit | PDF-level check after render | Each poem's heading page and plate page are located in the finished PDF: plate on a verso, heading on the facing recto, unit even; violations named by poem with the budget that fixes them | Checked |
| Commentary word budget | Sweep, computed | Over budget is an error at collect and at export | Checked |
| Every poem has one tag, one to three cast ids, all resolving to picked plates | Sweep over verse files | Each fault named | Checked |
| Stale cast | Render metadata | `illus status` | Checked |
| Model and size pin | Per-manuscript settings | Render stops unless a named flag is passed | Checked |
| 2:3 portrait, no bleed | The pin; the bleed property | Pick warns on another aspect; export reads bleed | Checked |
| Print resolution: plate inside the type area, upscaled 2x, at least 200 pixels per inch | Export book profile; `upscale` setting | Export reports each plate's effective density and stops below 200 | Checked |
| Black ink only | Style law in the prompt; pixel check | Pick and readiness flag a plate whose chroma exceeds a threshold | Checked |
| Hard line breaks in stanzas | Canonical-markdown rule; lint | A verse line without its trailing backslash, except the last of a stanza, is a lint error | Checked |
| Thesis line under every heading; title grammar with the article | Lint on verse files | Error per heading | Checked |
| Omit region on remainder and cast files | Existing region grammar | Export strips; sweep checks the opening tag | Checked |
| One image per poem, the turn in the last couplet, one voice, no archaism for rhyme, rhyme never bends syntax | Style law on the DON guide | A verse lens; the critic on any rewrite | Judgment |
| The commentary says what the poem does not; never paraphrases | Style law | Same lens | Judgment |

**Why LaTeX and why the check is on the PDF.** The PDF road is already
manuscript, pandoc, LaTeX (XeLaTeX), PDF, with geometry computed from the
trim and a second pass when the gutter band moves. Trade software (InDesign,
Affinity) encodes the same constraints as paragraph-style options applied
by a layout artist who is the only enforcer, and builds the EPUB separately.
Here the constraints live on the manuscript (trim, bleed, verse flag, type
size, layout name), a filter generates the LaTeX for verse from them (one
command per poem: plate, heading, thesis, stanzas, commentary), and the
check runs on the finished PDF, which is what a printer receives and which
does not depend on engine internals. Change the trim and nothing is placed
by hand.

## 10. Tool changes that follow (each an improvement task citing this doc)

1. **Hard-break canonical form.** SHIPPED 2026-09-25: normalize converts
   two-space breaks to backslash breaks before stripping whitespace
   (`authorlm/gdocs.py`, `_HARD_BREAK`, only before a continuation line);
   the audio speech path drops the backslashes (`authorlm/audio.py`,
   `speech_text`); two checks in `tests/test_api.py`; DON's 299 stanzas
   converted and re-pushed; a full pull came back with every stanza
   intact. The lint row (a stanza line without its backslash) waits for
   `form = "verse"` in change 3, since the lint cannot yet tell verse
   from prose.
2. **Cast references and the pin.** SHIPPED 2026-09-25 (19 checks in
   `tests/test_api.py`). Tag grammar `cast-id:` / `cast:` / `prior:` in
   `authorlm/illus.py` (`parse_tag`, `_OPTION`); `cast_index`,
   `resolve_references`, `reference_block` (identification only, between
   DEPICT and STYLE in `effective_prompt`); `read_metadata` and the
   `authorlm:cast` / `authorlm:prior` / `authorlm:off-model` fields every
   render writes; `stale-cast` and `off_model` in `slot_status`;
   `cast_problems` in `slot_report`. The pin: `_illustrations/settings.toml`
   (`ensure_pin`, `resolve_pin`, `write_pin`), stamped at `init` and by the
   first render of an older manuscript, changed only by `authorlm illus
   pin`; a render names another model with `--model` and is marked
   off-model; no fallback. Renders: `authorlm/llm.py` `generate_image`
   takes `references`, `model`, `size`; gemini/* attaches them to
   generateContent, openai/* goes through the images EDITS endpoint
   (`_openai_image_edit`, up to 16 inputs). CLI: `illus pin`, `illus
   render --model/--size`, `illus prompt` lists the attached plates,
   `illus list` reports stale-cast, off-model plates, and cast problems.
   DON pinned `openai/gpt-image-2` at `1024x1536`; SMSTTD stamped at its
   current `1536x1024`. `illustration_cast.md` drafted (8 cast, 9 stages)
   under Omit, in the toc as back matter, awaiting the cast sitting.
3. **Verse export profile and the poetry sweep.** `form = "verse"`; the
   spread layout in the book profile with plates inside the type area;
   the 2x export upscale and the pixels-per-inch check (§8a); the EPUB
   CSS rule; commentary budgets; the PDF-level pagination check; the
   verse lint rows.
4. **Verse lenses.** The poem grain and `siblings` target SHIPPED
   2026-09-25 (§1a). Two artifacts follow, written against the ratified
   verse and commentary law and read to the author as prose before
   install: `verse-law` (chapter class, poem grain: thesis shares no
   words with the poem, one governing image to the end, the last couplet
   turns, one voice, rhyme scheme and slant, rhyme bending syntax,
   archaism and proper-name counts, a struck stanza better than its
   replacement) and `section-arc` (cross-chapter class, poem grain,
   targets siblings, neighbours, book: adjacent poems on one image, two
   poems restating one thesis, the epigraph unhonoured, order, a
   governing figure belonging to another section's stage, titles outside
   the three forms). Boundaries: never a new image or claim, never a term
   of art, never meaning bent to rhyme; merges, cuts, and reorders are
   judgment tags.

## 11. Order of work and the trial

1. Ratify this document; ratify the DON house style rows it touches
   (`manuscripts/DON/_drafts/house-style-proposal.md`, illustration rows
   amended: portrait 2:3, no accent, spread).
2. Tool change 1, convert the files, re-push, confirm stanzas in the Doc.
3. Tool change 2. Then the **cast sitting**: eight canonical descriptions
   drafted by the session, several candidates per symbol, the author picks.
   Then the ten stage plates, rendered against the cast, picked; all of
   them references only, none printed.
4. **The trial**: three poems from one section (proposed: Relegere, whose
   stage is the most constrained), each rendered three ways: words only,
   words plus cast references, words plus cast plus prior. Judged on
   likeness and on whether the prior's composition bleeds in. The reference
   block's wording is settled here.
5. Poem descriptions drafted per section, in reading order, after that
   section's opener is picked, and triaged in the app or in chat.
6. Tool change 3; a layout trial of three poems of different lengths in
   the book profile before the eighty are laid out.

## 12. Ruled on 2026-09-25 (were open)

- **Type size**: a per-manuscript export setting, not a flag (§8).
- **Commentary illustrations**: diagrams for the metaphysics section only,
  drawn in the same ink manner, placed after the poem inside the
  commentary; decided at commentary-drafting time, poem by poem; none by
  default. Agreed.
- **Cast plates in the book**: none. No opener plates. The cast file holds
  references only (§4, §5).
- **The Seeker's dress**: ruled 2026-09-25, "Pilgrim is fine for now." The
  pilgrim of the ink tradition: a plain robe, a wide conical hat, a staff,
  seen from behind or in profile. The hat is the attribute that makes the
  figure recognizable at any size and hides the face by convention rather
  than by avoidance, and the wandering monk is the lineage the poems
  themselves cite (Dhyana, Ch'an, Zen). Hoods are ruled out: the poems'
  "hooded priests" are the antagonists of Relegere, and SMSTTD's hooded
  figures would read as the same book. Medieval garb pulls toward period;
  modern dress dates the book. Confirmed or replaced at the cast sitting
  when the first candidates exist.

## 13. Still open

- Nothing. The document awaits the author's ratification as a whole.

## 14. Where every setting in this document lives

| Setting | Lives in | Set by |
|---|---|---|
| Trim, bleed, ISBNs, author, publisher, narrator, language, year | `manuscripts` table, `~/.authorlm/authorlm.db` | `authorlm manuscript set` (`authorlm/cli.py:290`) |
| Export title, variant, cover, PDF engine; *proposed* `type_size`, `layout` | `<manuscript>/_exports/settings.toml` (DON: not yet written; created on first `export set`) | `authorlm export set`; defaults and loader `authorlm/export.py:317-325` |
| Illustration model and size, global default today | `authorlm/config.toml:109` `[illustrations]` | Hand edit; read by `authorlm/llm.py:1306` `resolve_image_setting` |
| Per-manuscript illustration pin (§7) | *proposed, does not exist yet*: `<manuscript>/_illustrations/settings.toml` | Written at first render; changed by an explicit verb (tool change 2) |
| `form`, `matter`, `register`, `illustrations` per file | `<manuscript>/toc.toml` | Hand edit; parsed in `authorlm/structure.py:127` (`form` is *proposed*, tool change 3) |
| Audio text rules, TTS model, credits | `<manuscript>/_audio/audiobook.toml` | Hand edit after `audio init` |
| Market, audience, pitch | `<manuscript>/_profiles/*.md` | `authorlm profile set` |
| Style law (verse, commentary, glossary, illustration) | `style_laws` table, `~/.authorlm/authorlm.db`, guide "DON house style" | `authorlm style add … --guide "DON house style"` |

## 15. Where settings live, standardized (ruled 2026-09-25)

The locations in §14 grew one subsystem at a time. Three kinds of thing
were mixed under the word "settings," and the ruling separates them:

| Kind | Belongs to | Lives in |
|---|---|---|
| Installation and account: vendor keys, budgets, DB tuning, the Google OAuth client, the defaults a new manuscript is stamped from | This machine and billing account; never travels with a book | `.env` (secrets and secret paths), `authorlm/config.toml` (non-secret installation values) |
| The book's own settings: publication identity, export, illustration pin, audio rules | The manuscript, wherever it goes | TOML files in the folder each governs (below) |
| Structure and declarations | The manuscript's text | `toc.toml`, `_profiles/*.md`, unchanged |

**The pattern.** A value a person sets lives in the folder it governs, in a
file named for what it is, with one loader convention and validation on
load. Ownership stays per file: `_audio/audiobook.toml` is the author's and
the tool never rewrites it; `_exports/settings.toml` and
`_illustrations/settings.toml` are written by their verbs.

- `manuscript.toml` at the folder root takes publication identity: author,
  copyright owner, ISBNs, trim, bleed, narrator, publisher, year, language.
  `manuscript set` writes it and validates as it does today (ISBN-13 and
  the rest). The database keeps only the registration, name and path,
  because a folder cannot say where it is.
- `_illustrations/settings.toml` takes the pin (§7), **written complete at
  `init`**, not at first render.
- `config.toml` is reduced to installation concerns and the stamping
  defaults. The `[gdocs]` block leaves it: the client-secret path is
  account material and moves to `.env` as `GDOCS_CLIENT_SECRET` (or the
  file moves to `~/.authorlm/` beside the token, with that as the code's
  default), read at `authorlm/gdocs.py:277`. The token already lives
  outside the checkout at `~/.authorlm/gdocs_token.json`.

**Copy, then own: never inherit.** Seeding from `config.toml` at init is a
snapshot, stamped complete with every key present. After init the
manuscript file is the only file read for those keys. There is no override
chain where an unset key falls through to the global file at read time:
that chain would re-couple a book to the global setting by the back door,
which is the drift the pin exists to prevent. Changing `config.toml`
changes what the next manuscript starts with and nothing else.

**What remains in the database.** Configuration proper: the registration
only. Everything else there is not configuration and stays: evidence and
observation (versions, transitions, episodes, intents, reviews, sessions,
usage); knowledge (concepts, edges, beliefs, proposals, summaries); law
(style guides, laws, attachments, each row carrying provenance and the
author's words); bridge and run state (Doc links, checkouts, threads,
filter and critique runs, illustration proposals, improvement tasks).
Publication identity is the one thing that crosses that line today and the
one thing that moves.

**Tool change 5 (separate from the verse work, §10):** `manuscript.toml`
and the identity move; the pin at init folded into tool change 2; the
`[gdocs]` block to `.env`; SMSTTD's row exported to its `manuscript.toml`
by hand once, per the no-back-compat rule.
