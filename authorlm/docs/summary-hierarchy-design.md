# Summary hierarchy — paragraph, section and essay summaries, and what each consumer reads

Status: DRAFT for ratification, 2026-10-25. Written from the owner's request of
the same day ("a first class paragraph summary generation process … we still
need section summaries … an essay summary which takes all of the section
summaries … figure out what the correct inputs to filters and lenses ought to
be … a deep dive is required"). No code changed, no summaries rebuilt, nothing
written to the live workspace; every count below was read from the live DB
read-only. A sibling session is redesigning concept and edge extraction
(`extraction.py`); §7 is the summary side of the interface it will consume, and
is a proposal until that design names what it wants.

Companion designs: critique-pass-design.md §4 (the current `essay_summaries`
table), autoregressive-writing-design.md §12 (the 2026-08-27 finding that
per-paragraph summaries were not worth it, revisited in §3.1 here),
filter-pass-design.md §9.1a (the `summaries` front-matter key),
lens-architecture-design.md §1–2 (lenses never read summaries),
track-a-two-step-extraction.md (why extraction is noisy).

## 0. Decisions for the owner

Each is a question; the recommendation follows it and the rest of the document
assumes the recommendation. These are the grill questions this design needs
answered before a build plan is cut.

1. **What is a "section"?** The code's unit today is the toc entry (a file;
   36 in SMSTTD's reading order). Headings split those into 265 subunits,
   and the extraction unit ratified 2026-08-10 is exactly that heading
   subunit. Recommendation: **paragraph = blank-line block; section =
   heading-delimited subunit (`extraction._sections`); essay = toc unit.** A
   file with no heading is one section. The existing `essay_summaries` row is
   then already the essay grain; what changes is how it is built (§3.4).
2. **Is a paragraph summary prose or a record?** Recommendation: **a record
   with a prose field** — one `gist` sentence for humans plus `claim`, `move`,
   `mentions` (each with its verbatim span) and `relations` (each with its
   verbatim span), because extraction and the reader-load report need the
   fields and nobody needs a second prose rendering of a 47-word paragraph.
3. **Does a collect rebuild the cheap levels on its own?** Today a collect
   only marks (`api.py:982`). Recommendation: **yes for paragraph and section
   summaries of the changed files** (about a cent per edited section, §5),
   **no for the essay level**, which keeps mark-don't-cascade because it is
   autoregressive over the book.
4. **May a lens use summaries to find text?** The 2026-09-06 ruling says a
   lens never judges against a summary. Recommendation: **a summary may act
   as an index that chooses which paragraphs of another chapter enter the T
   block as text, and never appears in the payload itself** (§6.2). The
   finding still quotes and hashes the text.
5. **Which model per level?** Recommendation: the configured summarizer
   (`[critique] summarizer_model`, today `openai/gpt-5.6-luna`) for all three,
   with `[summaries] paragraph_model` as an override so the paragraph pass
   can move to a flash-lite tier if its grounding check holds there.
6. **Replace the essay summary's format?** The MOVES / INTRODUCES / ASSUMES /
   PROMISES / DEBTS / MOTIFS / DOES NOT block was ratified for the critique
   editor. Recommendation: **keep those labels and add FLOW in front** (the
   section-by-section line of the argument, which is what the owner asked
   for), and derive INTRODUCES and ASSUMES from paragraph mentions instead of
   asking the model to list them (§6.6). No back-compat: the first full
   rebuild replaces every row.
7. **Does the recurrence bar move out of the model?** The concept admission
   law ("used multiple times") is verified today by a regex scan after the
   model has already proposed the concept. With per-paragraph mentions the
   count is a `GROUP BY` before any proposal is made (§7.3). Recommendation:
   yes, but this is the extraction session's call; here it is only made
   possible.
8. **What happens to tiny paragraphs?** 537 of SMSTTD's 1,949 paragraphs are
   under 15 words (headings, verse lines, dialogue beats, part cards).
   Recommendation: a heading paragraph gets a row with `kind = heading` and no
   model call; every other paragraph gets its own row, however short, so the
   coverage invariant is "every paragraph has a row", not "every paragraph is
   cited somewhere" (the weaker guarantee §12.3 of the writing design warned
   about).

## 1. What exists today, with locations

### 1.1 Storage

One table, `essay_summaries` (`db.py:335-353`): `file`, `summary` (one prose
block with the seven labels), `source_hash` (first 16 hex of sha256 of the
unit's text, `summaries.py:43`), `upstream_hash` (hash of the concatenated
prior summaries), `upstream_stale`, `status` (`current | deprecated`), unique
on (manuscript, file). No coverage column: coverage is recomputed from the
stored summary and the current paragraph count whenever it is shown
(`summaries.py:497-521`).

Live state (manuscript `ms-b33871dc315c`, read 2026-10-25): 35 rows, all
`current`, 12,643 summary words in total; the longest is kindness.md at 876
words, the shortest recap-sermon-five.md at 72. The book's reading order has
36 toc units.

### 1.2 Building

- Unit = toc entry, front matter included (`summaries.units`, `:143-150`).
- Autoregressive in reading order: each call carries every prior summary
  (`summarize_unit`, `:608-654`, PRIOR SUMMARIES block), the graph's `file=`
  slice (`_concept_slice`, `:587-599`), a target length of n/10 to n/7.5
  words floored at 40 and capped at 500 (`target_length`, `:47-55`), and the
  unit's text with every paragraph numbered `[n]` (`_numbered_paragraphs`,
  `:58-65`; paragraphs are blank-line blocks, `revisions._paragraphs`, `:231`).
- The prompt (`prompts/summarizer.md`) asks for MOVES with a paragraph
  citation per move, then INTRODUCES, ASSUMES, PROMISES, DEBTS, MOTIFS,
  DOES NOT, prose only, author's vocabulary verbatim.
- `paragraph_coverage` (`:77-118`) checks that every number 1..N appears in
  some citation; a cross-bracket range `[1]-[30]` counts every paragraph
  between as cited, so `complete` means "no paragraph fell outside a cited
  span", not "every paragraph got a move".
- `rebuild` (`:657-717`) takes ONE snapshot of context text before the first
  call (in-flight files contribute their pinned text, `_context_units`,
  `:205-219`); with `only_missing_or_stale` a fresh unit is reused until the
  first stale one, after which everything downstream cascades.
  `rebuild_one` (`:720-753`) rebuilds one unit against the current priors
  and marks everything after it `upstream_stale`.

### 1.3 Staleness

Mark, never cascade (module docstring, `:1-15`). `_state` (`:248-281`)
decides, in order: `unwritten` (a writeup created the file) → `missing` →
`deprecated` (file left the toc, `api._deprecate_departed_summaries`, `:860`)
→ `stale` (source_hash mismatch) → `rewriting` (in flight, pinned text
matches) → `upstream_stale` → `fresh`. `mark_changed` (`:756-775`) runs after
every collect (`api.py:982`) and flags the rows after the first changed unit.

### 1.4 Cost, measured

The usage ledger (`~/.authorlm/logs/usage.jsonl`, purpose
`critique.summarizer`) records 217 calls on `openai/gpt-5.6-luna` since
2026-08-30: 3.39 M input tokens, 249 K output tokens, $0.98 in total. That is
about 15.6 K input and 1.1 K output tokens per call. The input is dominated by
the PRIOR SUMMARIES block, which grows to ~12 K words by the last unit.

| | per full rebuild (36 units) | per `rebuild_one` late in the book |
|---|---|---|
| input tokens | ~560 K | ~20 K |
| output tokens | ~41 K | ~1.2 K |
| cost at $0.20 / $1.20 per M | **~$0.16** | ~$0.006 |

### 1.5 Consumers, and what each does with a summary

| consumer | reads | how | gated? | location |
|---|---|---|---|---|
| critique edit pass | before/after essay summaries | `summaries_ready` then `build_context` puts `before` and `after` into the editor's context | blocks on missing / stale / deprecated; tolerates upstream_stale | `passes.py:220-256`, `:356-377` |
| beat loop (write path) | before/after essay summaries | `drafting_context` renders them as the L1 book frame, with `!!` state warnings and a coverage note per entry; `write start` gates on the same predicate | same gate as the edit pass | `summaries.py:524-582`, `api.py:1515-1533`, `api.py:1838` |
| filter pass | before/after essay summaries | only when the filter declares `summaries = true`; rendered ungated and marked, never blocked | no (`§9.1a`) | `filters.py:193-199`, `filtering.py:341-380`, `:787-797` |
| manifest reader load | every essay summary's INTRODUCES and ASSUMES lists | regex over the prose labels, normalized against graph names and aliases; debt = ASSUMES minus everything introduced earlier | no; stale rows are shown and marked | `manifest.py:40-118` |
| lenses | nothing | by ruling: the assembler does not import `_neighbour_summaries`, the prompt says "no summary of any chapter exists here" | n/a | `lenses.py:19-23`, `:270-273` |
| briefing | nothing | its "summary" is `proposals.describe`, unrelated | n/a | `briefing.py:77` |
| critique.py | nothing directly | the pass reads through `passes.build_context` | | |
| `write collect`, `critique resolve` | write only | `mark_changed`; rebuild-at-gate | | `api.py:982`, `cli.py:1593-1617` |

There is no MCP verb for summaries at all (`mcp_server.py` has no summarize
tool); the CLI has `summarize status | show <file> | rebuild [file] [--all]`
(`cli.py:1873-1929`).

## 2. Does a section summary help edge extraction today? No, and it cannot.

`extraction.py` never reads `essay_summaries` or imports `summaries`: the only
"summary" in the module is the result dict `extract_concepts` returns
(`extraction.py:624`). Its inputs are:

- the raw text of the changed heading sections (`_changed_sections`,
  `:406-411`; the extraction unit ratified 2026-08-10), batched into payloads
  of at most `extraction_max_chars` = 24,000 characters (`_section_payloads`,
  `:414-446`, `config.toml:14`), or the whole file set when `--full`;
- a KNOWN CONCEPTS inventory of names (`_inventory_names`, `:449-456`),
  trimmed to fit (`_fit_inventory`, `:459-469`);
- the author's triage precedents (`triage_feedback`, `:208`);
- the prompt (`prompts/extraction.md`): concepts, links with a strict relation
  guide, aliasing sentences quoted verbatim.

A proposed link survives only if both endpoints occur in the text the model
saw (`endpoint_in_text`, `:1059-1067`), and a concept only if it recurs across
the manuscript (`hygiene.passes_recurrence_bar`). So **the owner's belief is
correct, and for a stronger reason than suspected: no summary of any level
reaches extraction, so none can be helping.**

Would today's summary help if it were fed in? Not for edges. The MOVES block
is written for an editor deciding what another essay may assume; it names the
central claim and the moves, not the sentences that assert a relation. A
316-word MOVES block over a 1,385-word essay (basilides.md) has dropped the
very spans the grounding gate (`endpoint_in_text`) and the alias rule ("the
exact sentence copied verbatim") require. What extraction lacks is not a
higher-level view; it is a paragraph-level one with the asserting span kept.
That is §7.

## 3. The hierarchy

### 3.1 Why the 2026-08-27 "no" is reversed

autoregressive-writing-design.md §12.3 rejected paragraph summaries on three
grounds. Each has changed:

- *Cost*: it assumed a before/after context assembled from paragraph
  summaries. Nothing here puts paragraph summaries into a drafting or edit
  context; those keep the essay grain (§6). The paragraph level serves
  extraction, the reader load and the section summary's citation skeleton.
- *Staleness*: it assumed paragraph rows keyed by ordinal, so an insertion
  renumbers everything. Rows here are keyed by the paragraph's content hash
  (§4.1); an insertion costs one new row and a section rebuild.
- *Coverage*: it argued numbered citation already gives paragraph-level
  accountability. The addendum of 2026-08-28 conceded that a blanket
  `[1]-[30]` satisfies the check. A row per paragraph is the only coverage
  guarantee that cannot be satisfied by a range.

### 3.2 Paragraph summary

- **Unit**: one blank-line block (`revisions._paragraphs`), as the critique
  pass and the writing loop already define it. Average 47 words in SMSTTD.
- **Kind** (deterministic, no model): `heading` (first line starts with `#`),
  `verse` (the file's toc entry is verse, or every line is short),
  `dialogue` (the house's speaker-prefixed lines), `prose`. Headings get no
  model call and an empty summary; they exist so a section's paragraph list
  is complete and so `ordinal` is a position in the file.
- **Record** (one JSON object per paragraph, produced by one call per
  section, see §3.5):
  - `gist`: one sentence, at most ~30 words, in the author's vocabulary.
  - `claim`: the assertion this paragraph makes, or `null` for a paragraph
    that only transitions, illustrates or restates.
  - `move`: one of `states | defines | argues | illustrates | objects |
    answers | transitions | restates | promises | recalls`.
  - `mentions`: `[{term, span, role}]` — every term of art, named figure,
    text, image or character the paragraph uses; `span` is the verbatim
    substring of the paragraph containing it (checked by byte comparison
    before storage, a mention with no matching span is dropped and counted);
    `role` is `introduces | defines | uses | names` (`names` is an aliasing
    sentence, the extraction prompt's ALIAS GUIDE case).
  - `relations`: `[{from, relation, to, span}]` with `relation` from
    `extraction.VALID_RELATIONS`; `span` verbatim, checked the same way.
  - `depends_on`: the hashes of earlier paragraphs in the same section the
    model says this one presupposes (the step it continues, the term it uses
    that was defined above). Within-section only; across sections the
    section summary carries it.
- **Length**: the record's prose is the `gist`; the whole record for a
  47-word paragraph runs ~60–80 tokens. Across the book that is ~90 K output
  tokens, which is the number that makes it "about the same cost as section
  generation" (§5).
- **What it must not do**: evaluate, soften, or name a concept the paragraph
  does not contain. The span rule enforces the last one mechanically.

### 3.3 Section summary — rebuilt from text, with the paragraph records as skeleton

The question was: from paragraph summaries, or from text? **From both, with
the text as authority.** A section is short (265 sections over 92 K words,
~350 words each on average, the largest a few thousand), so re-reading its
text costs almost nothing, and a summary written from text cannot inherit a
paragraph record's error. The paragraph records are supplied as a numbered
skeleton the model must place: every non-heading paragraph hash appears in
exactly one move, which replaces today's `[n]` citation check with an
equality check on sets (no range can satisfy it).

- **Unit**: `extraction._sections` on the file, each with its heading line;
  a file without headings is one section. Key: `(file, section_ordinal)`
  for display, `section_hash` = hash of the ordered paragraph hashes for
  identity.
- **Content** (prose, labelled like today's block): `POINT` (what the section
  establishes, one or two sentences), `MOVES` (in order, each citing its
  paragraphs by ordinal), `DEPENDS ON` (earlier sections of the same essay
  it presupposes, by heading), `LEAVES OPEN` (what it defers to a later
  section or essay).
- **Length**: n/10 to n/7.5 words of the section's text, floored at 30,
  capped at 250 (today's formula, re-floored for the smaller unit).
- **Conditioning**: the section's text, its paragraph records, the graph
  slice for the file, and the POINT lines of the earlier sections of the same
  essay (not their full summaries: within one essay the sections are read
  together by every consumer that wants them).

### 3.4 Essay summary — composed from section summaries

- **Unit**: the toc entry, as today. The row stays in `essay_summaries`.
- **Inputs**: the essay's section summaries (never the text: at this level the
  compression is the point, and metaphysic.md is 13,778 words), the graph
  slice, and the prior essay summaries in reading order exactly as today
  (`PRIOR SUMMARIES`), so the autoregressive book memory is unchanged.
- **Content**: `FLOW` first (new: the line of the argument section by
  section, one clause per section, naming each by heading, which is "the
  flow of the whole essay" the owner asked for), then MOVES (now citing
  sections rather than paragraphs), PROMISES, DEBTS, MOTIFS, DOES NOT as in
  the ratified prompt. INTRODUCES and ASSUMES stop being model prose: they
  are derived from the paragraph records (§6.6) and rendered into the block
  by code, so the manifest's "sentence fragments" limitation
  (`manifest.py:25-30`) closes.
- **Length**: today's formula over the essay's word count, unchanged.
- **Staleness**: unchanged semantics (`source_hash`, `upstream_hash`,
  `upstream_stale`, `status`), plus `sections_hash` so a rebuild can tell
  "my sections changed" from "the text changed under me while my sections
  were rebuilt".

### 3.5 Calls

- Paragraph records are produced **one call per section**, the section's
  text numbered by paragraph ordinal, JSON array out. One call per paragraph
  would be 1,949 calls with a prompt each; one call per essay would put
  185-paragraph god.md into a single 10 K-token answer, which Luna-class
  models truncate. A section over `max_section_chars` (24,000, extraction's
  cap) splits at paragraph boundaries into two calls.
- Section summaries: one call per section (265).
- Essay summaries: one call per essay (36), after its sections.
- Order within a rebuild: paragraphs of a section → that section → next
  section … → the essay → the next essay. A stale paragraph record forces
  only its own section and essay to rebuild; the essay's downstream
  neighbours are marked, as today.

## 4. Storage

Three tables (3NF: the levels have different columns and different keys; one
`summaries` table with a `level` column was considered and rejected, §9).
All carry `KNOWLEDGE_OBJECT_COLUMNS` through `ko_fields`.

### 4.1 `paragraph_summaries`

```
manuscript_id   TEXT NOT NULL
file            TEXT NOT NULL
para_hash       TEXT NOT NULL     -- hash of the paragraph's normalized text
ordinal         INTEGER NOT NULL  -- 1-based position at last build (display only)
section_ordinal INTEGER NOT NULL  -- the section it sat in at last build
kind            TEXT NOT NULL     -- heading | verse | dialogue | prose
record          TEXT NOT NULL     -- the JSON object of §3.2
model           TEXT NOT NULL     -- which model wrote it (the ledger's key)
status          TEXT NOT NULL DEFAULT 'current'   -- current | orphaned
UNIQUE (manuscript_id, file, para_hash)
```

Identity is the content hash. An unchanged paragraph that moves keeps its row
(its `ordinal` is refreshed by the next section build, no model call). A
paragraph whose text changed gets a new row; the old row becomes `orphaned`
(kept, like `deprecated` essays: the record of what a paragraph said, useful
to the proposal learning loop; pruned by an explicit verb, never on collect).
A paragraph that appears verbatim twice in one file (a refrain) has one row
and two ordinals; the second is stored in the section row's paragraph list.

### 4.2 `section_summaries`

```
manuscript_id   TEXT NOT NULL
file            TEXT NOT NULL
section_hash    TEXT NOT NULL     -- hash of the ordered para_hashes
section_ordinal INTEGER NOT NULL  -- position at last build
heading         TEXT              -- the heading line, NULL for a preamble
para_hashes     TEXT NOT NULL     -- JSON list, in order
summary         TEXT NOT NULL     -- POINT / MOVES / DEPENDS ON / LEAVES OPEN
model           TEXT NOT NULL
status          TEXT NOT NULL DEFAULT 'current'   -- current | orphaned
UNIQUE (manuscript_id, file, section_hash)
```

### 4.3 `essay_summaries`

Unchanged columns plus `sections_hash TEXT NOT NULL` (hash of the ordered
section hashes) and `model TEXT NOT NULL`. Existing rows are replaced by the
first full rebuild (no migration of their text; the format changes).

### 4.4 Staleness per level

| edit | paragraph rows | section rows | essay row | downstream essays |
|---|---|---|---|---|
| wording inside one paragraph | that paragraph: new row needed | its section: stale (hash moved) | stale (`source_hash`) | `upstream_stale` |
| paragraph inserted or deleted | one new row, or one orphan | its section: stale | stale | `upstream_stale` |
| paragraph moved within a section | none | stale (order is in the hash) | stale | `upstream_stale` |
| paragraph moved across sections | none | both sections stale | stale | `upstream_stale` |
| heading renamed | the heading paragraph: new row (no model call) | stale | stale | `upstream_stale` |
| file leaves the toc | untouched | untouched | `deprecated` (as today) | `upstream_stale` |

`status` per level is derived at read time exactly as `_state` does today,
from the hashes, never stored as a flag; the only stored flag remains the
essay row's `upstream_stale`, because conditioning on prior essays is the one
thing a hash of the file cannot see.

### 4.5 Incremental rebuild

`summaries.rebuild(level=…)` reads one capture (as today) and for each essay
in reading order: computes the paragraph hashes, finds the paragraphs with no
`current` row, groups them by section, makes one call per such section,
stores the records; rebuilds every section whose `section_hash` has no
current row; rebuilds the essay if its `sections_hash` or `source_hash`
moved, or if `--all`; marks downstream. A paragraph edit in essay 30 of 36
therefore costs: one paragraph-record call for its section, one section call,
one essay call. The prior-summaries block of that essay call is the same size
as today's `rebuild_one`.

## 5. Cost on SMSTTD

Counts from the live DB (version 637, 2026-10-25): 36 toc units, 92,425
words, 1,949 paragraphs (265 of them headings; 537 under 15 words), 265
sections, 429 live concepts, 1,569 edges. Token estimates use 1.35 tokens per
word for the prose and the measured per-call overhead of today's summarizer.
Prices are the configured summarizer's (`openai/gpt-5.6-luna`, $0.20 input /
$1.20 output per M tokens, from `~/.llmlm/prices.json`); a flash-lite tier is
$0.10 / $0.40.

| level | calls per full build | input tokens | output tokens | cost (Luna) | cost (flash-lite) |
|---|---|---|---|---|---|
| paragraph records | 265 (one per section) | ~125 K text + ~130 K prompt and slice overhead ≈ 255 K | ~90 K | **~$0.16** | ~$0.06 |
| section summaries | 265 | ~125 K text + ~90 K records + ~130 K overhead ≈ 345 K | ~45 K | **~$0.12** | ~$0.05 |
| essay summaries | 36 | ~45 K section summaries + ~560 K cumulative priors (as today) ≈ 600 K | ~41 K | **~$0.17** | n/a (keep the ratified model) |
| whole hierarchy | 566 | ~1.2 M | ~176 K | **~$0.45** | ~$0.28 with the cheap tier on the lower two |

For comparison today's full rebuild is ~$0.16 (§1.4). An incremental rebuild
after editing one section late in the book is three calls, ~25 K input and
~2 K output tokens, **about one cent**. The paragraph level is, as the owner
expected, about the price of the section level; the essay level is unchanged
because its cost was always the prior-summaries block, which this design does
not touch.

Wall clock: today's rebuild is sequential (prior summaries). Paragraph and
section calls within one essay are also sequential (section N's POINT lines
feed section N+1), but different essays' lower levels are independent and may
run in parallel before the essay pass; a full build at four workers is on the
order of today's.

## 6. Inputs: which consumer gets which level

| consumer | today | proposed | why |
|---|---|---|---|
| extraction | raw section text + name inventory | **paragraph records** (mentions, relations, spans) grouped by section, plus the **section summary** of each section in the payload as linking context; never the essay summary | grounding is per paragraph; the span is what `endpoint_in_text` and the alias rule want; the essay level is too compressed to carry a relation (§2) |
| critique edit pass | before/after essay summaries + the target's paragraphs | unchanged **before/after essay summaries**, plus the **target essay's section summaries** as the editor's map of what it is editing | the editor already has every paragraph of the target; what it lacks is the target's own argument outline. Gate unchanged |
| beat loop (drafting) | before/after essay summaries | unchanged **before/after essay summaries** (FLOW-first format), plus, for a rewrite of an existing essay, the pinned essay's **section summaries** as the plan's scaffold | L1 stays essay-grain (§3.1 cost argument); the loop conditions on verbatim accepted text at the paragraph grain already (L2), so paragraph records add nothing there |
| filters | neighbours' essay summaries when `summaries = true` | the same, and a new front-matter key **`outline = true`** that adds the essay's own section summaries to block A | a sequential filter sees one unit at a time and today has no idea where in the argument it is; the outline is intra-essay, so it does not reopen the §9.1a gating objection (rendered ungated and marked, like `summaries`) |
| lenses | nothing | **nothing in the payload** (ruling kept). A `targets` key **`located`** may use paragraph records as an index to pick which paragraphs of another chapter enter T as text (§6.2) | the finding quotes and hashes text; the summary only chose which text |
| manifest reader load | INTRODUCES / ASSUMES prose lists | **paragraph mentions** with `role = introduces | defines` vs `uses`, normalized against the graph, per essay | closes the known fragment limitation; the number of introduces and the debt list become exact |
| `summarize show`, the chat agent | one prose block | all three levels, by flag | an editor working on one paragraph wants its record, not the essay's memory |

### 6.1 Where today's input is wrong or missing

- Extraction has no summary input at all (§2); its only linking context is a
  list of names, which is why the alias rule and the relation guide have to
  rely on the model remembering what it just read across a 24,000-character
  payload.
- The manifest reads prose lists as if they were a vocabulary; its own
  docstring says the real repair is upstream in the summarizer (`manifest.py:
  29-30`). §3.4 is that repair.
- The filter pass's `summaries = true` gives a filter the other essays and
  nothing about the essay it is filtering: the wrong level for a per-unit
  pass.
- The edit pass hands the editor every paragraph of the target and a memory
  of every other essay, and no outline of the target: the one essay it is
  allowed to change is the one it has no map of.

### 6.2 Lenses: a summary as a pointer, never as a witness

The ruling stands: no summary in the payload, every finding about another
chapter names it and quotes its text, provenance is the text hash
(`lens-architecture-design.md §2`). The `earlier` target key already shows
the shape of what is allowed: zero-token derived lists computed from the
graph. A `located` key extends that: for each term the essay uses that the
graph says was introduced elsewhere, the paragraph records of the introducing
chapter say which paragraphs carry `role = introduces | defines` for it, and
those paragraphs' TEXT (with the chapter's hash and the paragraph ordinals)
enter T under the chapter's file name. The model never sees a record; it sees
chapter text chosen by a record. A stale record can at worst choose the wrong
paragraph, which the lens sees as text that does not say what the essay
assumes, and reports as such. Whether `located` replaces `pointers` for the
premise-supplying case (`validity-and-objections`) is a lens-by-lens choice.

### 6.3 The extraction session's interface

§7 below. It is written here because the summary side must exist before the
extraction side can read it; the extraction redesign owns the consumer and
may narrow or rename fields.

### 6.4 The beat loop

The drafting context keeps the essay grain. Two changes only: the FLOW line
leads each neighbour's entry, and `drafting_context`'s coverage note is
replaced by a section-count line (coverage is now structural, §3.3). The
`summaries_ready` gate is untouched; it still gates on essay rows.

### 6.5 The critique pass

`build_context` adds `sections` (the target's section summaries, fresh by the
same capture) beside `paragraphs`; `render_user_message` prints them as an
OUTLINE block before the paragraphs. The editor prompt's instruction set does
not change.

### 6.6 INTRODUCES and ASSUMES by derivation

For essay E: INTRODUCES(E) = terms with a mention of `role = introduces |
defines` in E's paragraph records whose graph node is `introduced_in` E (or
has no earlier `introduces` mention in reading order); ASSUMES(E) = terms
mentioned in E with `role = uses` whose first `introduces | defines` mention
lies in an earlier essay, or in no essay (a debt). Both lists are normalized
against `concept_nodes` names and aliases as `manifest._canonical_names`
does today, and rendered into the essay summary block by code, under the same
labels, so `manifest._field` keeps working and the summarizer prompt loses
two sections it was never good at.

## 7. The summary side of the extraction interface

What a paragraph record hands extraction, per paragraph, in the words of the
proposal learning loop and the sweep framework (a link is proposed only when
the text asserts it):

```json
{
  "file": "basilides.md",
  "para_hash": "3f9c…",
  "ordinal": 12,
  "section_ordinal": 2,
  "kind": "prose",
  "gist": "Basilides' pleroma is read as the Field before any Choice.",
  "claim": "The pleroma names the undivided Field prior to Choice.",
  "move": "defines",
  "mentions": [
    {"term": "pleroma", "span": "the pleroma, which Basilides sets before all things", "role": "defines"},
    {"term": "the Field", "span": "is the Field before any Choice has been made", "role": "uses"},
    {"term": "Basilides", "span": "which Basilides sets before all things", "role": "uses"}
  ],
  "relations": [
    {"from": "pleroma", "relation": "specializes", "to": "the Field",
     "span": "the pleroma … is the Field before any Choice has been made"}
  ],
  "depends_on": ["a81e…"]
}
```

Guarantees the summary side makes:

1. **Every span is a verbatim substring of the paragraph** (byte check before
   storage; failures are counted in the build report and the mention is
   dropped). Extraction's `endpoint_in_text` becomes a lookup, and the alias
   rule's "exact sentence copied verbatim" is already satisfied.
2. **Every paragraph has a row** (headings included, with an empty record).
   Coverage is set equality, not citation parsing.
3. **Terms are the author's spelling and casing**, as they appear in the span;
   normalization against the graph is the consumer's step, with
   `manifest._canonical_names` as the shared helper.
4. **Relations use `extraction.VALID_RELATIONS`** and nothing else; an unknown
   relation is dropped at the summary side and counted.
5. **`depends_on` is within-section**; cross-section dependence is in the
   section summary's DEPENDS ON prose, and cross-essay dependence in the essay
   summary's ASSUMES list (derived, §6.6).

What extraction can then do, paragraph by paragraph (the extraction session
decides whether it does):

- Candidate concepts = terms with ≥ 2 `introduces | defines | uses` mentions
  across distinct paragraphs in distinct contexts (the recurrence law, as a
  query rather than a regex sweep after the fact, §0.7).
- Candidate edges = (from, relation, to) triples asserted in ≥ 1 paragraph,
  each carrying its asserting spans; an edge asserted in two paragraphs is
  stronger evidence than the model's confidence ever was.
- Aliases = mentions with `role = names`, span included.
- Notes for a concept = the `claim` of each paragraph where it is `defines`,
  in reading order: the author's own definitional sentences, not a paraphrase.

Query shape the consumer gets: `summaries.paragraph_records(db, manuscript,
files=None, since_hash=None) -> list[dict]` in reading order, and
`summaries.mentions(db, manuscript, term=None) -> list[dict]` across the book.
Both read-only, both over `status = 'current'` rows.

## 8. Verbs

CLI (`cmd_summarize`), replacing today's three actions:

| verb | does |
|---|---|
| `summarize status [--level paragraph\|section\|essay]` | per essay: fresh / stale / … as today; with a level, the count of current vs needed rows at that level |
| `summarize rebuild [file] [--level …] [--all]` | the incremental pass of §4.5; `--level essay` alone is today's behaviour; `--all` forces every row |
| `summarize show <file> [--section N] [--paragraph N] [--json]` | the essay block; with `--section` that section's block and its paragraph gists; with `--paragraph` the record |
| `summarize outline <file>` | the essay's FLOW line and each section's POINT, the one-screen map |
| `summarize prune` | drops `orphaned` paragraph and section rows older than `[summaries] keep_orphans_days` |

MCP (`mcp_server.py`), new, read-only: `get_summary(file, level="essay",
section=None, paragraph=None)` and `summary_status()`. No MCP rebuild verb:
rebuilds are billed and the CLI already owns every billed verb that is not a
drafting step.

Automatic triggers (knob `[summaries] auto`): after `write collect` and
`critique resolve`, the paragraph and section levels of the changed files
rebuild if `auto` includes them; the essay level keeps today's two triggers
(`critique resolve` at the gate, explicit `rebuild`). The report line a
collect prints gains "summaries: N paragraph record(s), M section(s)
rebuilt; essay marked stale".

## 9. Rejected alternatives

- **One `summaries` table with a `level` column.** The three levels differ in
  key (content hash vs section hash vs file), in what `status` means, and in
  which carries `upstream_stale`; a single table would carry nullable columns
  for every level and a `CHECK` per level. Three tables, one fact each.
- **One call per paragraph.** 1,949 prompts; the overhead would exceed the
  content. One call per section keeps the paragraphs' neighbours in view,
  which `depends_on` needs anyway.
- **Essay summary from text.** It is what today's summarizer does, and it is
  the reason a 13,778-word essay yields a 719-word block the manifest has to
  regex. Composition from sections is cheaper and gives FLOW for free.
- **Section summary from paragraph records only.** Cheaper by ~125 K input
  tokens per build (~$0.03) and one error-propagation step worse. Not worth it.
- **Feeding essay summaries to extraction.** §2: the level is wrong.
- **Putting a `located` text into lenses as a summary excerpt.** Ruled out
  2026-09-06; the pointer form of §6.2 is the only use that survives the
  ruling.
- **Keying paragraph rows by ordinal.** The 2026-08-27 objection; content
  hashes answer it.

## 10. Knobs

| setting | where | proposed value | why |
|---|---|---|---|
| paragraph unit | code | blank-line block (`revisions._paragraphs`) | the unit every other pass already uses |
| section unit | code | heading-delimited (`extraction._sections`), whole file if no heading | the ratified extraction unit |
| essay unit | code | toc entry | unchanged |
| paragraph `gist` length | prompt | ≤ 30 words | one sentence; the record carries the rest |
| paragraph record per call | code | one call per section; split at `max_section_chars` = 24,000 | §3.5 |
| section summary length | `target_length` variant | n/10 to n/7.5 words, floor 30, cap 250 | today's ratio, re-floored |
| essay summary length | `target_length` | n/10 to n/7.5, floor 40, cap 500 | unchanged |
| essay summary labels | prompt | FLOW, MOVES, PROMISES, DEBTS, MOTIFS, DOES NOT; INTRODUCES and ASSUMES derived | §3.4, §6.6 |
| `[critique] summarizer_model` | config | `openai/gpt-5.6-luna` (as configured) | all three levels by default |
| `[summaries] paragraph_model` | config, new | unset (falls through to the summarizer) | lets the paragraph pass move to a cheap tier after a span-check trial |
| `[summaries] auto` | config, new | `["paragraph", "section"]` | §0.3 |
| `[summaries] keep_orphans_days` | config, new | 90 | the learning loop reads old records; nothing else does |
| span check | code | byte equality, drop and count on failure | §7 guarantee 1 |
| coverage | code | set equality of paragraph hashes per section | replaces `paragraph_coverage` |
| extraction input | consumer | paragraph records + section summaries | §6 |
| edit pass input | consumer | before/after essays + target's sections | §6 |
| beat loop input | consumer | before/after essays, FLOW-first | §6 |
| filter input | front matter | `summaries = true` as today; `outline = true` new | §6 |
| lens input | front matter | none; `targets = ["located"]` picks text via records | §6.2 |
| manifest input | consumer | paragraph mentions | §6.6 |
| MCP | new | `get_summary`, `summary_status`, read-only | §8 |
| ledger purposes | `llm.purpose` | `summaries.paragraph`, `summaries.section`, `critique.summarizer` (essay, unchanged key) | per-level spend stays visible |

## 11. Build plan (after ratification; small commits, each with tests)

1. Tables and hashing (`db.py`, `summaries.py`): paragraph kinds, hashes,
   `paragraph_records`, `mentions`, status derivation; hermetic tests on a
   fixture essay with headings, verse and a refrain.
2. Paragraph pass: `prompts/paragraph-records.md`, one call per section, span
   check, build report; recorded live test on one short essay
   (basilides.md, 40 paragraphs) with the recording committed.
3. Section pass: `prompts/section-summary.md`, skeleton equality check.
4. Essay pass: the new FLOW-first prompt, INTRODUCES / ASSUMES derivation,
   `sections_hash`; `rebuild` with levels; a full live rebuild of SMSTTD
   recorded with its measured cost beside the estimates of §5.
5. Consumers: manifest first (pure gain), then the edit pass OUTLINE block,
   then `outline = true` for filters, then the drafting context's FLOW line.
6. Verbs: CLI actions, MCP read verbs, collect's auto trigger.
7. Lenses `located` target: only after the extraction session's design lands,
   since it shares the index.
8. Extraction consumes `paragraph_records`: in the extraction session's own
   plan, against this interface.
