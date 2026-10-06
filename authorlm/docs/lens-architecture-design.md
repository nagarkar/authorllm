# Lens architecture — cross-chapter lenses on deterministic inputs

Status: BUILT 2026-09-06 (branch lens-architecture; phases 1–6 of §11 in `lenses.py`, `cli.py`, tests in test_api.py; the seven decisions of §9 taken as recommended, with one deviation noted in §9.5 below). Drafted 2026-09-06 after the eight book-agnostic
lenses were ratified and run on hierarchy.md (docs/lenses.md carries the
artifacts; the checked-in copy there still shows the retired five and is
updated when this design is ratified). Companion designs: sweep-framework.md
(the lens class), filter-pass-design.md §7.2 (the lens door), filters.md
(front matter, payload blocks, the polarity rule).

## 1. The problem, as found on 2026-09-06

A lens today is a bare prompt in `_lenses/<name>.md`. `lens run` sends one
call to the `[llm]` model with three things: the harness preamble, the lens
text, and the concept notes for the concepts the essay names. `lens register`
accepts findings from outside through the same hygiene gate. That is the whole
machine, and it was enough for lenses that read one chapter against itself.

Three of the eight ratified lenses cannot be run that way, and two more have a
rule that cannot:

- `cross-references` needs the text of every chapter the essay points to.
- `audience-pitch` needs the reader (AUDIENCE PROFILE), the reading order, the
  neighbouring chapters (density), and what earlier chapters supplied (ramp).
- `through-line` needs the neighbouring chapters for proportion.
- `illustration-as-argument` needs the chapter a figure is deferred to, and the
  earlier chapter that gave a figure its first mechanism.
- `validity-and-objections` needs the chapter that supplies a premise.

The first drafts of those lenses named SUMMARIES as the authority for the
other chapter. That was a fault, ruled out the same day: a summary is a derived
artifact rebuilt on every settle, so a finding judged against it is judged
against something that changes while the chapter does not; and the native run
never had the summaries anyway, so the model invented "per the SUMMARIES" from
the examples inside the lens file. The ruling: **lenses run independently and
never on summaries. Where a rule needs another chapter, the authority is that
chapter's own text.**

Two smaller faults surfaced with it. The lens has no payload view: nothing
prints what the model was shown, so the chat agent cannot draft against the
same material the native path uses, and a subagent registering findings
assembles its own context by hand (the filter pass forbids exactly that for
filters). And the lens prompt now carries a long Examples section (10–25 KB per
lens, ratified as the place the line falls), which is sent whole on every run
without a way to say so or to omit it.

## 2. Principles (ratified 2026-09-06, restated)

1. **Independence.** A lens's inputs are the chapter under review plus stable,
   author-ratified artifacts: the concept graph (GLOSSARY), the profiles
   (AUDIENCE PROFILE, ORGANIZING SCHEME), the toc (reading order, PROTECTED
   REGISTERS), the filter roster (SENTENCE-LEVEL PASSES), and the TEXT of
   other chapters. Never a summary, digest, or any other derived view.
2. **One payload, two minds.** Whatever the native model is shown is exactly
   what a subagent or the chat agent is shown. The payload is assembled
   deterministically, printed on demand, and hashed block by block, as the
   filter payload is.
3. **Targets are resolved, not guessed.** Which other chapters a lens sees is
   decided by rules in code (pointer resolution, reading-order neighbours),
   declared per lens in front matter, and listed in the payload with each
   target's hash. A finding about another chapter names that chapter and
   quotes it, and the quote is checked against that file.
4. **Findings carry provenance.** Every finding records the hash of the essay
   and of each target it was judged against, so staleness is a byte
   comparison, not a summary rebuild.
5. **The evidence loop is untouched.** Findings remain guidance rows of kind
   `lens`, reviewed through `lens review`, edits through the §7.2 door.

## 3. The artifact: front matter on a lens

Lenses adopt the filters' TOML front matter (`filters.parse_front_matter`,
already written; the same delimiter, the same tomllib parse, the same
unknown-key refusal). A lens with no front matter is read as the defaults
below, so the five old lenses and any author-written prompt keep running
unchanged.

```toml
---
class    = "cross-chapter"                      # "chapter" (default) | "cross-chapter"
inputs   = ["glossary", "audience", "scheme", "registers", "passes", "reading-order"]
targets  = ["pointers", "neighbours", "earlier"] # only when class = "cross-chapter"
examples = "prompt"                             # "prompt" (default) | "omit"
---
# Title
…examples…
## The lens
…rules…
```

`inputs` vocabulary (anything else is refused by name):

| key | rendered block | deterministic source |
|---|---|---|
| `glossary` | GLOSSARY | `api.scoped_concepts(file, text=essay)`: name, aliases, notes, **introduced in**, edges among the selected nodes |
| `audience` | AUDIENCE PROFILE | `_profiles/audience.md` via `filtering._profiles_block` (a labelled absence if missing) |
| `scheme` | ORGANIZING SCHEME | `_profiles/scheme.md` — a new, short, author-written profile listing the book's framework items (walls, idols, parts, stages); labelled absence if missing |
| `registers` | PROTECTED REGISTERS | toc.toml entries carrying `register = "protected"` (new per-chapter key, riding on the TOC like `matter` and `illustrations`); a lens asked to run ON a protected chapter refuses |
| `passes` | SENTENCE-LEVEL PASSES | `_filters/*.md`: name and first line each, so a lens hands off by name |
| `reading-order` | READING ORDER | the toc as a numbered list: file, title (first heading), matter, and a `card` mark for entries under a word threshold (part openers), with the essay under review marked |

`targets` vocabulary (each renders a TARGETS block entry: file, title, sha, full
text):

| key | what is included | how it is resolved |
|---|---|---|
| `pointers` | every chapter this essay refers to | a resolver scans the essay for the house's framed references ("the essay on X", "the paper on X", "Sermon N", "the Metaphysic", "the Recapitulation", "the Appendix", "the essay Before Consciousness") and matches X against chapter titles, file stems, and an optional alias table `_profiles/chapter-aliases.toml` (title → [aliases]); unresolved pointers are listed in the payload as POINTERS UNRESOLVED (a finding candidate in its own right) |
| `neighbours` | the nearest full chapter before and after, by reading order | cards (below the word threshold) and part openers are transparent, as `audience-pitch`'s density rule requires |
| `earlier` | NOT the text of every earlier chapter (too large, and dilutes) | two derived-but-deterministic lists: TERMS INTRODUCED LATER — concepts this essay names whose `introduced_in` sorts after it in reading order (the ramp check, from the graph, zero tokens); and TERMS INTRODUCED EARLIER — concept, chapter, so a lens can name where the book supplied a term |
| `book` | zero-token cross-file derivations | VERBATIM RECURRENCES — sentences of ≥ N words appearing in more than one chapter (a pure auditor); CONCEPT REALIZATIONS — for each concept this essay realizes, the list of chapters that also realize it (candidates for "the same telling recurs across three or more chapters") |

`earlier` and `book` deliberately never include other chapters' prose. What a
lens needs from "everything before this" is either a fact the graph already
holds (where a term was introduced) or a pattern a pure auditor can compute.
The judgment stays with the lens; the search does not.

## 4. Payload assembly

`lenses.assemble(db, manuscript, name, file) -> LensPayload` mirrors
`filtering.assemble` and reuses its block helpers. Blocks, in order, each with
a sha256 in its header:

- **S — law.** The harness preamble (rewritten to describe the finding
  contract in §5) and the lens artifact (examples included unless
  `examples = "omit"`).
- **A — inputs.** The blocks `inputs` asked for, rendered as above. Stable
  across the lenses run on one essay in one sitting, so a `lens sweep` (§6)
  assembles it once.
- **T — targets.** Only for `class = "cross-chapter"`: the resolved chapters,
  each as `--- <file> — <title> — sha <…> ---` followed by its text, then the
  derived lists (TERMS INTRODUCED LATER, VERBATIM RECURRENCES, …).
- **E — the essay.** The chapter under review, whole, with its sha.

The preamble tells the model what each block is and that T is the ONLY
authority on the other chapters. Nothing in the assembler can add a summary:
`_neighbour_summaries` is not imported, and the front-matter vocabulary has
no key that would render one.

Native and external paths consume the same object. `run_lens` becomes
"assemble, then `llm.complete_json(payload.system, payload.user)`";
`lens run` with no flag PRINTS the payload; a subagent reads the printed
payload and answers it; `lens register` stores the answer.

Size, on hierarchy.md today (measured against the current files):

| lens | class | approx. payload |
|---|---|---|
| framework-consistency, evidence-reach, validity-and-objections, unsourced-claims | chapter | essay 7K + glossary 4K + lens 4–6K ≈ 15–17K tokens |
| through-line | cross-chapter, neighbours | + two neighbours ≈ 10K → 27K |
| illustration-as-argument | cross-chapter, pointers | + 6 pointed chapters ≈ 35K → 50K |
| cross-references | cross-chapter, pointers | ≈ 50K |
| audience-pitch | cross-chapter, neighbours + earlier + book | + neighbours 10K + derived lists 2K + profile 1K → 30K |

On the `[llm]` model a 50K-token call is a few cents. Through a subagent it is
roughly 60K fresh tokens plus the reading turns, on the order of a fifth of a
filter beat, as measured on 2026-09-06.

## 5. The finding contract

```json
{"findings": [{
  "quote":        "<verbatim, from the essay under review>",
  "note":         "<the finding>",
  "rule":         "<the Flag rule's heading, verbatim from the lens>",
  "replacement":  "<optional — §7.2 door, essay only>",
  "footnote":     "<optional — the gist of a footnote the sentence should carry; the door plants [Footnote: gist] after the quote as a staged edit (footnote design §12)>",
  "judgment":     "<optional — ONLY when no replacement can be written from the payload: the decision the author alone can make, one clause; the door plants [Judgment: lens — clause | id: gd-…] after the quote as a staged edit>",
  "target_file":  "<optional — a chapter in block T>",
  "target_quote": "<optional — verbatim, from that chapter>"
}]}
```

Hygiene, extended: `quote` must occur in the essay (today's gate); when
`target_file` is given it must be a file in block T and `target_quote`, if
given, must occur in it verbatim, else the finding is stored with
`target_unverified` set and the review shows it. `rule` is matched against the
lens's Flag headings; unmatched rules are kept but flagged, and the tally per
rule (`lens status`) is what shows a rule that never fires or fires on
everything, the same signal `filter status` gives for a prompt fault.

Metadata on every stored finding: lens, file, essay sha, `{target: sha}` for
each target in T, `rule`. `lens status <file>` lists batches per lens with
per-rule tallies and marks a batch STALE when the essay's or any target's
current sha differs from the recorded one. That is the whole of what
summaries were being asked to do, done by byte comparison.

## 6. Verbs

- `lens add <name>` — validates front matter (vocabulary, class/targets
  agreement), as `filter add` does.
- `lens run <name> <essay>` — assembles and PRINTS the payload; no model call.
  `lens run --native` makes the `[llm]` call on the same payload. **This
  reverses today's polarity** to match `filter run` (design §… the third
  instance of the rule "the call is what needs the flag"); `--dry-run` is
  accepted as a no-op alias, and the first `lens run` in a session says which
  way round it is. Open decision, §9.
- `lens register <name> <essay> [--reply <file>]` — unchanged door, reply JSON
  on stdin or from a file; refuses a reply whose `target_file`s are not in the
  payload's T block for that lens (a subagent that read a chapter the lens did
  not declare has left the payload).
- `lens sweep <essay> [--native] [--only a,b] [--skip a,b]` — the roster in
  the ratified order (framework-consistency, cross-references, through-line,
  validity-and-objections, evidence-reach, illustration-as-argument,
  audience-pitch, unsourced-claims), block A assembled once, one payload
  printed per lens (or one call each). After the last, findings whose quotes
  overlap across lenses are cross-linked in metadata (`also_flagged_by`) so the
  author sees one passage once with two notes, never two rows to rule on
  separately.
- `lens status <essay>` — batches, per-rule tallies, STALE marks, unverified
  target quotes.
- `lens review`, `lens push`, `lens resolve` — unchanged.

## 7. Examples in the artifact

The Examples section is part of the ratified prompt and is sent with it by
default: it is where the line falls, and a rule without its example is what
the independent review found wanting. `examples = "omit"` exists for a
deliberately cheap run. The skill's standing rule stands: when the author rules
an example wrong or moves the line, the lens file is edited at once and the
reasoning recorded afterward; nothing in the verbs writes to a lens file.

## 8. Rejected alternatives

- **Summaries as the cross-chapter authority.** Derived, rebuilt on every
  settle, and not what the native run had. Ruled out 2026-09-06.
- **Whole-book text in the payload.** 75K words is affordable on the cheap
  model and ruinous on a subagent, and it dilutes: a lens asked to hold thirty
  chapters finds less in the one it was asked about. Targets are declared and
  resolved instead; "earlier" and "book" arrive as derived lists.
- **LLM-based pointer resolution.** A model deciding which chapter "the essay
  on Kindness" is would make the target set non-deterministic between runs.
  The resolver is code plus an author-owned alias table.
- **A separate lens class for cross-chapter lenses with its own store.** One
  store, one review loop, one door; the difference is declared inputs.

## 9. Decisions for the author

1. **Polarity of `lens run`.** Recommended: print by default, `--native` to
   call, matching `filter run` and the ruled pattern. Alternative: keep
   `lens run` native (today's habit, and lenses are cheap) and add
   `lens payload` for the printed form.
2. **ORGANIZING SCHEME as a profile.** Recommended: `_profiles/scheme.md`,
   author-written, a dozen lines. Alternative: derive from the concept graph
   (the Four Temple Walls node and its children), which is deterministic but
   only as good as the graph.
3. **PROTECTED REGISTERS on the toc.** Recommended: `register = "protected"`
   on sermons.md's entry. Alternative: infer from the existing `voice` key,
   which conflates casting with register.
4. **Alias table for pointer resolution.** Recommended: `_profiles/
   chapter-aliases.toml`, seeded once from the titles ("The Good Life" ↔
   "the essay on The Good Life"; "Mixing" ↔ "the paper on Mixing"). The
   resolver's misses are reported, so the table grows on evidence.
5. **Neighbour threshold.** What counts as a card: recommended, an entry under
   400 words or with `matter != "main"`. *Built as word count alone:* the
   Metaphysic is back matter and eleven thousand words, and treating matter as
   a card marker would have made every appendix chapter transparent. The
   reading order still shows each entry's matter.
6. **Native path for cross-chapter lenses.** Recommended: allowed (the cheap
   model's context is ample), with the subagent road preferred where the lens
   is judgment-shaped, per sweep-framework's matrix.
7. **Front matter for the eight installed lenses**, as the table in §10; to be
   written into the files on ratification and mirrored in docs/lenses.md.

## 10. Proposed front matter for the eight

| lens | class | inputs | targets |
|---|---|---|---|
| through-line | cross-chapter | glossary, registers, passes, reading-order | neighbours |
| validity-and-objections | cross-chapter | glossary, registers, passes | pointers |
| evidence-reach | chapter | glossary, registers, passes | — |
| illustration-as-argument | cross-chapter | glossary, registers, passes | pointers |
| framework-consistency | chapter | glossary, passes | — |
| cross-references | cross-chapter | glossary, reading-order | pointers |
| audience-pitch | cross-chapter | audience, glossary, scheme, registers, passes, reading-order | neighbours, earlier, book |
| unsourced-claims | chapter | glossary, registers | — |

## 11. Build plan (after ratification; small commits)

1. Front matter on lenses: `lenses.load_lens` via `filters.parse_front_matter`
   with the lens vocabulary; defaults for bare prompts; `lens add` validation.
2. Inputs: GLOSSARY with `introduced in`; profiles (`audience`, `scheme`);
   PROTECTED REGISTERS from the toc key; SENTENCE-LEVEL PASSES; READING ORDER.
3. Targets: the pointer resolver and alias table; neighbours; the two derived
   lists for `earlier`; the recurrence auditor for `book`.
4. `lenses.assemble` with hashed blocks; `run_lens` on the payload;
   `lens run` prints, `--native` calls.
5. Finding contract: `rule`, `target_file`, `target_quote`; extended hygiene;
   provenance metadata; `lens status` with STALE.
6. `lens sweep` with shared block A and cross-lens overlap linking.
7. Skill update: the payload-to-subagent protocol for lenses, same shape as
   filters; docs/lenses.md refreshed with the eight artifacts and their front
   matter.

## 12. Judgments: ruled in the tab, repaired after (built 2026-09-06)

The author's ruling, after the first sweep left fifteen findings with no new
text: a finding is a rewrite whenever the payload suffices, and "judgment" is
reserved for a repair only the author can decide — cut or move a section,
choose between two claims, supply a fact the payload lacks, add an argument
that does not exist. Every lens's rules now say so (REWRITE OR JUDGMENT), and
the harness preamble says it to every model.

A judgment travels the same road as every other proposal: the door plants
the author's own request tag after the quoted sentence,
`…sentence.[Judgment: <lens> — <decision> | id: gd-…]`, as a staged edit;
the surgical push shows it as a green insertion with nothing struck. **In
the tab, deleting the tag rejects the finding and leaving it accepts it**;
`lens resolve` propagates the form's verdict to the finding through
`record_review` (`propagate_verdicts`), for rewrites as well — the form is
the finding's verdict. An accepted judgment then stands in the file as an
open `[Judgment: …]` directive (kind added to `directives.py`: collect
reports it, exports strip it, `apply` refuses it) until `lens repair
<essay>` acts on it: a payload of STYLE LAW, PROTECTED TERMS, GLOSSARY, each
judgment with its finding, its unit, and the target chapter it named, and
the essay; the reply, per judgment, is a `rewrite` (the whole unit, tag
gone, staged and pushed as a form), a `question` (the one fact needed,
returned to the author), or an `intent` (a structural change filed as a
scoped intent, the tag's removal staged). `lens repair --dismiss <ids>
--reason` rejects from chat and stages the tag's removal. Lens threads are
keyed by batch rather than lens name so a re-run never overwrites forms
already out in a tab, and `lens push` lets new lens forms join ones already
out.

