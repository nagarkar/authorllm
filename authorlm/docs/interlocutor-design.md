# The interlocutor — design reference

Ratified 2026-09-06 in a grilling session with the author. This document is
the build reference for the interlocutor: a third-person critical reading of
a manuscript from inside a named tradition (Plato, Epictetus, Basilides,
Shankara, Becker, Nietzsche …), run as a Claude subagent and landing on the
critique road.

Related: `sweep-framework.md` (why a lens is not this), `critique-pass-design.md`
(the road the report rides), `filter-pass-design.md` (the artifact grammar this
borrows).

---

## 1. What it is, and what it is not

A **lens** reads one essay and reports findings. A **filter** reads one essay
unit by unit and proposes an edit to each. An **interlocutor** reads the whole
book from inside a tradition it is expert in and produces a *report*: the
objections the tradition's literature already holds against claims the book
makes, the misattributions the book commits about the tradition, and, for each
objection, whether the book as written already meets it.

It is not a lens (Q1, ratified): it is whole-manuscript, report-shaped, and the
thing wanted at the end is a triageable pile of improvement tasks with critic
provenance. That is the critique road, built for exactly that.

It is not the drafter's inverse of the fabrication rule but its relocation. The
drafter may use only five permitted inputs and no world knowledge. The
interlocutor's whole value IS world knowledge, so the guard moves from the
inputs to the citations: **an objection with no locus is not an objection**
(§5).

The system is for AuthorLM, not for one manuscript. Nothing here names SMSTTD
except the worked example.

## 2. The artifact — `_interlocutors/<name>.md`

Author-ratified before it runs, like every lens and filter. Sidecar directory
(underscore-prefixed), so it is invisible to observation and never exported.
TOML front matter between `---` lines, then the prose prompt.

```toml
---
engaged = ["epictetus.md"]        # essays that have already engaged this critic
position = ["metaphysic.md"]      # essays carried whole as the author's position

[[terms]]
name = "prohairesis"
aliases = ["prohairetic", "moral purpose", "faculty of choice"]
kind = "tradition"

[[terms]]
name = "Choice"
kind = "book"
reason = "the book's nearest analogue to prohairesis"
---
```

Keys, all validated at `interlocutor add` so a declined add leaves no file:

- `terms` — required, non-empty. Each entry has `name`, optional `aliases`,
  `kind` ∈ {`tradition`, `book`}, and for `book` a required one-line `reason`.
  Two kinds by ruling (Q13): the tradition's own vocabulary, and the book's own
  terms the author declares adjacent to it. The mapping is the author's claim,
  ratified with the artifact; the critic is told it is the author's, not
  evidence.
- `engaged` — optional list of manuscript files that already engage this
  critic (a Connections essay, or any essay). Carried whole in every payload.
  Ruling (amendment 1): a run flag `--engaged` adds to the list; this is not a
  system for one manuscript, and "the connections essay" is just the common
  case.
- `position` — optional list of manuscript files carried whole as the
  *authoritative author position* (amendment 2). Distinct from `engaged`: a
  position essay states what the book holds; an engaged essay states what the
  book has already said about the critic. Run flag `--position` adds to it.

Files named in `engaged` and `position` must be manuscript content files; a
name that is not is out of bounds at add.

The prose carries: the **corpus declaration** (which works, which
commentators supply the objections' literature names, what is out of scope,
and for a fragmentary tradition which reconstruction is spoken from — Q12),
the persona, where to start, and the boundaries (§5).

**Precision about which essay** (author ruling 2026-09-06, on the first
draft: "the scope is the book … it should not be misunderstood with any
other essay"). The artifact governs a whole-book reading, so the bare words
"the essay" are never written. Every reference names the file in backticks
and, at first use, its title and part; a label such as "the Connections
essay" is defined once as that file and no other. The bootstrap prompt
(`prompts/interlocutor-draft.md`) requires this of every artifact it drafts.

### 2.1 Bootstrap — the comparison is the author's input

Ruling (Q13, amended): the concept comparison is an author input, never a
subagent guess. A critic with an essay that already engages it uses that essay
as the comparison; a critic without one waits for the author's rough
comparison and the bootstrap will not run without one.

The first artifact (Epictetus) is drafted in conversation and ratified there
(Q3: "B for Plato, then A"). Later artifacts come from `interlocutor draft
<name> --engaged <essay> --out <payload>`: a payload carrying the artifact
grammar, every installed artifact as exemplar, the engaged essay whole, and
the book's concept names with aliases; a subagent drafts the artifact; the
author reads it; `interlocutor add` installs it. `draft` makes no model call.

## 3. The run — one instruction, one turn

Ruling (Q10, amended): "run Epictetus on the book" is one instruction. The
session assembles, spawns, waits, verifies, imports, and reads the report back
as prose in the same turn, with the paths of the two files produced. The
author never sees the payload, the manifest, or a hash.

### 3.1 `interlocutor run <name> [--file …] [--engaged …] [--position …] --out <payload>`

No model call. Deterministic assembly:

1. **Scan** (step 1 of the author's spec, deterministic). Every content file
   in toc reading order — Sermons and Appendix included (Q4) — narrowed by
   `--file` (a chapter or a part opener, `select_chapters` semantics) for
   cheap follow-up runs. Each file is split into units (`passes.paragraphs_of`);
   the enclosing heading is tracked; every term and alias is matched at word
   boundaries, case-insensitive, plural-tolerant (`concepts.concept_pattern`).
   A hit is (file, part, matter, heading, unit n, terms matched, unit text).
   **The MENTIONS shown are a sample of the scan** (author ruling
   2026-09-06: "Do sampling, both across concepts and for locations where
   each concept exists … every time I run the interlocutor, I might get new
   insights"). Sampling is two-stage — at most `--max-terms` (default 12)
   of the BOOK's terms with hits, the tradition's own terms always shown
   (they are few and the point: the first run sampled "Nature", 71 units,
   and left "prohairesis", 12 units, unshown), then at most
   `--max-per-term` locations (default 6) of each — with a seed drawn
   fresh per run, printed, written into the
   payload and the report, and reproducible with `--seed`. A cap of 0
   disables sampling on that axis. The full scan is kept for a coverage
   table (every term, total units, how many shown, which files carry the
   rest) so the critic knows what the sample is a sample of and where to
   open a file whole. This replaced the earlier proposal to trim the book's
   core vocabulary from the term list.
2. **Payload**, in blocks:
   - the harness prompt `prompts/interlocutor.md` (reading protocol, verdict
     scale, output contract);
   - THE INTERLOCUTOR — the artifact prose;
   - TERMS — the term table, kinds and reasons;
   - THE BOOK — toc in reading order with each file's summary (marked `!!`
     when stale or missing, never stopping the run), part and matter;
   - AUTHOR POSITION — position essays whole, paragraphs numbered;
   - ENGAGED — engaged essays whole, paragraphs numbered;
   - CONCEPT NOTES — the ratified notes of every graph concept whose name or
     alias is a `book` term that hit;
   - MENTIONS — the coverage table, then the sampled hits grouped by file
     and heading, each unit verbatim. For a file already carried whole,
     only the locations;
   - MANUSCRIPT ROOT — the absolute path and the list of files the subagent
     may open whole, for step 2;
   - OUTPUT CONTRACT — the report and manifest shapes and the two paths to
     write them to (`<payload>.report.md`, `<payload>.manifest.json`).
3. The deterministic MENTIONS block is also written beside the payload
   (`<payload>.mentions.md`) so `import` can prepend it to the final report
   unchanged. The subagent never reproduces it.

### 3.2 The subagent

Default model, empty context (the beat-critic precedent: judgment about what
counts as an objection is the whole job). It reads the payload, opens whole
essays from disk when a claim needs more than the hit's section (step 2), and
records every file and section it read as its **baseline** (Q5). No web
access; loci come from knowledge with an honesty mark (Q6).

### 3.3 The report (subagent output, markdown)

```
# <Critic> reads <manuscript> — <date>

## Baseline
- <file.md> — whole
- <file.md> — §<Heading>
  (one line per extraction beyond the MENTIONS block)

## Objections
### O1. <literature name> — <locus> [sure|check]
Bears on: `<file.md>` §<Heading> — "<verbatim quote>"
The objection: <what the tradition holds against the claim, and why it is
  load-bearing — one paragraph>
Verdict: addressed | partial | unaddressed
Where addressed: `<file.md>` §<Heading> — "<verbatim quote>"
Inference: <how the quoted premises entail the answer — required when the
  answer is by inference rather than stated>
Missing premise: <required for partial and unaddressed>
Improvement: <one sentence — supply the premise, or state the claim in the
  form that meets the strongest version> → scope: <file.md>

## Misattributions
### M1. <short name> — <locus> [sure|check]
Passage: `<file.md>` §<Heading> — "<verbatim quote>"
What the tradition holds: <one paragraph>
Contested: yes|no  (a contested reading is never an error)
Repair: <one sentence> → scope: <file.md>

## Terms not on the list
- <term> — <why it should be>
```

Rulings encoded in the contract:

- **Addressed by inference counts as addressed** (Q7, amended). The book need
  not name the objection. The verdict is `addressed` when the book's theory as
  stated entails an answer; the report quotes the premises and states the
  inference so the author can check the step. No improvement item.
- `partial`: the inference needs a premise the book does not supply, or the
  book meets a weak form and not the strongest in the literature. `unaddressed`:
  nothing in the baseline supplies premises. Both name the missing premise and
  produce one improvement.
- **An improvement is never "name the objection and answer it."** The author's
  words: "I don't want to keep saying 'Plato's objection A: my response to A'
  all through these non-fiction essays." The improvement supplies a premise or
  restates a claim, placed in the essay where the premise belongs.
- Every objection carries its **literature name** where one exists ("the Third
  Man", "the Euthyphro dilemma") — that name is what the author would search —
  and a **locus**: a passage (Stephanus page, Discourses book.chapter) or a
  named commentator and work, marked `sure` or `check`. A `check` is a to-do
  for the author, not a defect.
- **Misattributions** (Q11): everything a scholar of the tradition would mark —
  a doctrine attributed in a form the tradition would dispute, a
  mischaracterization, a misquotation. Kept apart from objections because the
  repairs differ: a premise versus a corrected sentence and a citation.
  Contested readings among commentators are marked contested and never counted
  as errors.

### 3.4 The manifest (subagent output, JSON)

The existing critique manifest shape, unchanged, so `critique.import_manifest`
runs as is:

```json
{"source": {"name": "Epictetus, read by Claude",
            "detail": "interlocutor run, 2026-09-06, scope: whole book"},
 "items": [{"kind": "intent", "unit": "O3 the dichotomy of control",
            "ordinal": 1, "scope": "god.md", "text": "…"}]}
```

One intent per `partial` or `unaddressed` objection and per misattribution
(Q8: intents only, no objection nodes). `unit` opens with the report id
(`O3`, `M1`) so import can join item to finding. `scope` is the essay the
improvement belongs in, or null when the critic cannot place it.

### 3.5 `interlocutor import <name> <payload>`

Runs automatically in the same turn (amendment 4). Deterministic:

1. **Baseline check.** Every file listed exists in the manuscript; every
   heading listed exists in that file (heading text compared with markup and
   hashes stripped). A failure **declines the whole import** (Q5): the baseline
   is the report's honesty claim.
2. **Per-finding hygiene**, the lens gate's law applied here: a finding whose
   `Bears on` / `Where addressed` / `Passage` quote is not verbatim in the
   named file (whitespace-normalized, case-insensitive), or whose locus line
   is missing, is **dropped and counted**. Its manifest items go with it.
3. **Manifest join.** An item whose `unit` id is not a surviving finding, or
   points at an `addressed` objection, or names a scope that is not a
   manuscript file, is dropped and counted.
4. **Land.** The final report = a harness header (critic, date, scope, drop
   counts) + the deterministic MENTIONS block + the subagent's body, copied to
   `<manuscript>/_critiques/<date>-interlocutor-<name>.md`; the surviving
   manifest to `.json` beside it; then `critique.import_manifest`. Items land
   as **proposed** intents with critic provenance. Nothing is law until
   triaged.

**No memory between runs** (Q9, ratified B). Each run is a fresh report; the
author reconciles by eye; the import's text-dedupe catches what it catches.

## 3.6 From finding to paragraph — the fixes ride the filter road

Ratified 2026-09-06 after the first run. The report lands intents, and an
intent is a work order, not a sentence. The road from a finding to the
paragraph it bears on is the filter pass, never a draft in the conversation:
a chat draft skips the style law, the protected terms and the lint, and the
first attempt at surgical fixes for the Epictetus report broke five ratified
style elements before the author caught it.

The follow-through is one **global filter per interlocutor run**,
`_filters/<critic>-objections.md`, whose prompt carries the surviving
findings verbatim in substance — the passage, what the tradition holds, the
missing premise, the repair asked — grouped by the file each bears on. Its
prelude returns, as the registry, the findings that name THE ESSAY matched
to unit numbers, marking QUESTION any repair that needs a fact the report
does not contain (that unit is `keep` with the question in `why`). It runs
on each essay the report touched, one beat each, through the ordinary road:
`filter prelude`, `filter run` to a payload, a clean-context subagent
drafts the reply under STYLE LAW and PROTECTED TERMS, `filter apply` stages
the forms in the Doc tab, the author rules there. The filter's list is
closed: a unit the registry does not name is never touched. The
objection-and-answer format is out of bounds in the artifact's own prose.

**Footnotes (ruled 2026-09-06).** A finding may carry a `Footnote:` line: the
gist of a note the passage should carry when the repair is a source to supply
or a qualification. The follow-through filter carries that gist into the unit
it repairs and plants the author's `[Footnote: <gist>]` tag there, per the
standing rule in `prompts/filter-run.md`; the note itself is drafted on the
footnote road (footnote-directive-design §12). No critic and no filter
invents a citation.

## 4. Reading it back

The session reads the report back as prose **by essay**: which objections are
open against it, what each asks for, which misattributions need a corrected
sentence — plus the two paths. Then the intents are triaged as with any
critic (`list_critique_items` / `triage_critique`, scope = the essay).

## 5. The boundaries (written into every artifact's prose)

- No objection without a locus. An objection the critic cannot place in the
  tradition's own texts or its literature is an opinion, and it is not
  reported.
- No dialogue format. Never ask the essays to name the critic and answer them
  point by point.
- No objection from outside the declared corpus.
- A contested reading is never a misattribution.
- The book's own vocabulary is not evidence of agreement or disagreement. The
  `book`-kind terms are the author's mapping and are read as such.
- The Sermons are read as claims the book makes; the improvement for a claim
  that lives only in a sermon points at the essay that defends it, never at
  the sermon.

## 6. Verbs

| Verb | Does | Model call |
|---|---|---|
| `interlocutor add <name>` | ratify an artifact (stdin), validated whole first | no |
| `interlocutor list` / `show <name>` | as for lenses | no |
| `interlocutor draft <name> --engaged <essay> --out <p>` | bootstrap payload for a new critic's artifact | no |
| `interlocutor run <name> [--file …] [--engaged …] [--position …] --out <p>` | scan + payload (+ mentions sidecar) | no |
| `interlocutor import <name> <p>` | verify, gate, land in `_critiques/`, import manifest | no |

Nothing native. The sweep framework's matrix puts a whole-book reading that
reasons at length on the Claude side of the door; a billed path can be added
the day four critics need to run unattended.

## 7. Order of first use (ratified)

Epictetus first (small stable corpus, a direct term map already in
`epictetus.md`, a Divergences section the critic can start from), then Becker
as the cheap second to confirm the road generalizes, then Nietzsche, then
Basilides (fragmentary; the corpus declaration is itself the scholarly
argument). Plato waits for the author's rough comparison.
