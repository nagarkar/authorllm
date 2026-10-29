# Extraction redesign — concepts and edges, same schema

Status: RATIFIED 2026-10-25 (the eight rulings in §0, recorded verbatim in intent). BUILT phases 1-5 of §9 (PR nagarkar/authorllm#271 and the phase-5 PR); phase 6 partly, phase 7 done — see the §9 notes. No code changed. Nothing in the
live workspace was written; every number below came from read-only queries
against `~/.authorlm/authorlm.db` (manuscript SMSTTD, `ms-b33871dc315c`,
637 collected versions, latest 2026-10-25) and from reading the chapter
files. The owner's verdict that started this, verbatim: "our concept and
edge extraction doesn't work very well. It is deterministic, and the number
of false signals are very high and it's causing major problems. We need to
retire and redo all of concept and edge extraction processing although the
schema itself is fine."

Companion designs: track-a-two-step-extraction.md (the adjudication pass
this supersedes), lens-architecture-design.md (the "never judge against a
summary" ruling of 2026-09-06, which §4.3 honours), critique-pass-design.md
§4 (essay summaries as the editor's working memory), domain-vocabulary.md
(beliefs versus laws; nothing here writes `style_laws`).

## 0. The owner's rulings (2026-10-25)

Asked one at a time and answered; the reasoning follows each.

1. **Reject reasons: optional, prompted.** The triage surface asks for a
   one-line reason on every reject of an extracted concept or edge and
   lets the author skip it. Not required, unlike a critique reject.
2. **Adjudicator model: Claude Opus-class, chosen at run time.** The
   author's words: "let's do claude opus-class but have the system prompt
   me for what model I want to use. I don't always have api credits so I
   want the 2.5-pro or flash options, or even open ai." So
   `[extraction] adjudicator_model` holds a default, and every `--native`
   run that would adjudicate prints the choices (Opus-class, gemini-2.5-pro,
   gemini-2.5-flash, an OpenAI model) with the run's estimated cost for
   each, and asks before the first call. `--adjudicator <model>` answers
   it non-interactively.
3. **Belief seeding: stopped.** Extraction verdicts and dismissals of
   machine proposals never seed `editorial_beliefs`. Only an author's
   review explanation does. The 10 triage-seeded candidates are left as
   they are.
4. **The 102 open `note_update` proposals: `superseded`.** A new state
   value, not an author verdict; the firewall ignores it, so the new
   pipeline may re-raise any that still clear the floor, now with a span.
5. **The 161 inferred edges: re-adjudicated** through the new gates and
   adjudicator (§5.2). Survivors reach the author with spans; failures
   retire with the revive path intact.
6. **First live run: one essay**, hierarchy.md, where the §2.4 sample
   came from. The author triages it before the book runs.
7. **Summaries: wait.** The summary hierarchy (PR nagarkar/authorllm#268)
   is wired into block M first; the first run carries summaries.
8. **Retired names: §4.10 agreed, revivals migrated.** Below the bar
   superseded; above it re-asked once with spans.
9. **No lexical near-miss of a retired name** (ruled 2026-10-25 after the
   replay). The difflib rule at 0.8 blocked nine concepts the author had
   confirmed ("Change" against "challenge", "Metaphysic" against
   "Metaphysics", "Spiritual" against "Ritual") and no rejected one. The
   exact ban stays; the adjudicator's block P is the semantic version.
10. **Confirmed extracted concepts absent from the text are retired by the
    migration** (ruled 2026-10-25). The replay found 53 confirmed nodes
    whose name and aliases occur nowhere in the book, nearly all
    paraphrases the old extractor coined; gate 1 would never admit them
    now. `extract migrate` retires them on the same terms as a failed
    pending row (tagged, one evidence row, `--undo` revives). Author-
    declared concepts are never touched.
11. **Extraction is lazy, and unattended** (ruled later the same day; the
    author's words: "I want to extract lazily, and unattended … even
    concept extraction should be lazy. If a fresh concept tree is
    required, someone should run the concept extraction"). Nothing mines
    the graph as a side effect: `init`, `collect` and `write complete`
    never extract. `authorlm extract --native` and the MCP tool
    `extract_concepts` are the only roads, taken when a fresh graph is
    wanted, and a run so asked for has no question left to answer:
    `adjudicator_prompt = "default"` with the opus-class adjudicator is the
    shipped setting (the asked-for choice of ruling 2 stays available as
    `adjudicator_prompt = "ask"` and `--adjudicator`). This retires the
    2026-09-25 ruling that switched extraction off pending the redo:
    `[extraction] enabled` is a plain kill switch again, on.

## 1. The pipeline as found, end to end

What runs today, with the line that does it. The owner's word
"deterministic" is half right: the candidates come from a model
(`extraction.py:854`), and everything that admits, grounds, scores and
retires them is deterministic string work. The noise comes from both halves.

### 1.1 Triggers

- `authorlm extract` (`cli.py:180-193`): collects a revision, then
  `extract_concepts`.
- Closing a writeup (`api.py:3191`, `api.py:3278-3284`): a collect, then
  `run_extraction` (`api.py:4370-4377`) when `extraction_allowed` is true.
- MCP `extract_concepts` (`mcp_server.py:1807`).
- Two switches: `[extraction] enabled` (`extraction.py:558-564`,
  `config.toml:124`, **off since the owner's ruling of 2026-09-25**) and the
  per-manuscript switch in `manuscripts.metadata` (`extraction.py:573-589`).
  `[extraction] adjudicate = true` (`config.toml:139`).

### 1.2 What is read

- Scope: files changed since the watermark `manuscripts.metadata.
  last_extracted_version` (`extraction.py:343-367`), or everything with
  `--full`. The watermark is a version id, advanced only after every pass
  succeeds (`extraction.py:653-700`, `1208-1210`).
- Unit: the heading-delimited **section** (`_sections`,
  `extraction.py:386-403`, ratified 2026-08-10); changed sections only on an
  incremental run (`_changed_sections`, `406-411`); batched into payloads of
  at most `MAX_TEXT_CHARS = 24000` (`extraction.py:30`, `414-446`). A full
  run that exceeds one payload becomes one pass per file in reading order
  (`extraction.py:760-820`).
- Context: a `KNOWN CONCEPTS:` line of up to 200 live names, alphabetical,
  trimmed to fit (`extraction.py:449-469`, `843-851`); plus `triage_feedback`
  (`extraction.py:208-302`): the 40 most recent retired names, 20 retypes,
  30 confirmed names, 20 rejected edges, 15 relation corrections, each a
  fixed recency window (`LIMIT` at lines 220, 228, 235, 247, 256), plus the
  distilled beliefs from `loop.generator_feedback`. The comment at
  `extraction.py:297-301` concedes 538 decisions on record and 75 reaching
  the model. No ratified concept **notes** are sent, no style law, no
  summary.

### 1.3 The model call and the adjudication call

- One `complete_json` with the prompt in `prompts/extraction.md`
  (`extraction.py:854`, thinking off). The prompt asks for 10-20 nodes per
  call (`extraction.md:12`) under a `MAX_CONCEPTS = 40` cap
  (`extraction.py:31`); on a 34-unit book that is a standing order for
  340-680 concepts. It also asks for 5-15 links per call (`extraction.md:68`).
- Adjudication (`adjudication.py:329-426`, prompt `prompts/adjudication.md`):
  per candidate, the 3 nearest live concepts and 3 nearest rejected names by
  Jaccard overlap above 0.30 (`adjudication.py:55-61`, `199-222`), a located
  quote found by `concept_pattern` (`adjudication.py:121-166`), one call per
  payload (`adjudication.py:349`), verdicts `new | improves | subsumed |
  drop`. Only `new` continues. One evidence row per pass
  (`adjudication.py:309-326`).

### 1.4 How nodes, edges, aliases and proposals are written

- Concept loop (`extraction.py:925-1048`): name clipped to 80 chars, notes to
  300; a retired name is banned (`897-910`) unless it recurs in changed text,
  which raises a `revival` proposal (`938-955`); a near-miss of a retired
  name within difflib 0.8 raises `variant_of_retired` (`961-979`); kinds
  `concept | metaphor | example` must pass the recurrence bar, at least two
  paragraph contexts in two sections or files (`extraction.py:980-993`,
  `hygiene.py:174`, `204-213`); then `add_concept` writes the row
  (`extraction.py:999-1001`, `concepts.py:23-47`) and stamps
  `metadata = {"origin": "extracted", "confirmed": false}` plus
  `introduced_in` from `primary_location` (`extraction.py:1013-1017`,
  `concepts.py:266-285`). An existing concept with a materially different
  definition raises a `note_update` proposal (`1020-1046`), gated by the 0.90
  paraphrase floor (`extraction.py:39`, `476-502`). The loop stops at 40 new
  nodes (`1047-1048`).
- Edge loop (`extraction.py:1068-1112`): both endpoints must already be
  known nodes (`1079-1084`), the relation must be in `VALID_RELATIONS`
  (`extraction.py:25-29`; unknown relations are dropped, `1085-1091`), both
  endpoint names must occur in the payload text by `concept_pattern`
  (`endpoint_in_text`, `1059-1067`), then `link_concepts(..., status=
  "inferred")` (`1101-1102`, `concepts.py:214-234`) with `support = 0` and
  `evidence = "[]"`. **No edge carries the sentence that asserted it.** A
  rejected edge argued again raises `edge_reproposal` (`1105-1112`).
- Aliases (`extraction.py:1128-1191`): only the naming-ceremony case
  (canonical live, alias not yet a node, sentence quoted verbatim, in
  changed text) raises an `alias` proposal; folds and retired names are
  logged as `extraction_adjudication` evidence (`115-177`).
- Realization (`extraction.py:1192-1200`, `concepts.py:288-317`): every
  declared node whose name matches anywhere becomes `realized`.
- Skip counts (`extraction.py:911-922`, returned at `1212-1232`):
  `skipped_malformed`, `skipped_unknown_endpoint`,
  `skipped_unknown_relation`, `suppressed` (banned or alias cases),
  `ungrounded_links`, `below_bar`, `materiality_refused`, `screened`.

### 1.5 Where verdicts land

- Concept keep/retype: `metadata.confirmed = true` and a `concept_triage`
  evidence row (`triage.py:684-701`, `extraction.py:180-205`). Retire:
  `retire_concept` (`triage.py:702-707`, `concepts.py:153-175`), status
  `retired`, every live edge retired with it.
- Edge keep/flip/retype: status `declared` (`triage.py:721-741`). Reject:
  status `rejected` (`742-747`).
- Zero-token bulk rules (`triage_rules.py:15-56`, `214-236`): known alias,
  duplicate name, unmentioned, below bar; retired endpoint, self-reference,
  duplicate settled, unmentioned endpoint. Evidence `deterministic_triage`
  (`triage.py:773-790`). On the live workspace that evidence type has **zero
  rows**: the rules have never been applied there.

### 1.6 The deterministic pieces, named

`concept_pattern` (`concepts.py:237-243`: a word-boundary match, plural
tolerant, case-blind), used for realization, grounding, `introduced_in`,
recurrence, located quotes and `endpoint_in_text`; Jaccard `similarity`
(`loop.py:102-110`) with `NEAR_DUPLICATE = 0.72` (`loop.py:122`) for the
proposal firewall (`proposals.py:124-153`) and adjudication retrieval; the
recurrence bar; the retired-name ban; difflib near-miss at 0.8 (dropped, §0.9); the 0.90
paraphrase floor; `triage_rules`. Of these, `concept_pattern` is the one
that manufactures false signal: on the sample below, "Experience" and
"Discernment" co-occur in 74 paragraphs because both are ordinary English
words, so the edge between them was "grounded" wherever either word fell.

## 2. The baseline: false signals, measured

### 2.1 Nodes

| | count |
|---|---|
| all nodes | 1,121 |
| machine-extracted | 1,047 |
| extracted, retired | 685 (65% of extracted) |
| extracted, live, confirmed | 243 |
| extracted, live, unconfirmed | 119 |
| author-declared (no origin) | 74 (7 retired) |

Retirement rate by kind, extracted nodes only:

| kind | confirmed | pending | retired | retired % |
|---|---|---|---|---|
| objection | 2 | 1 | 12 | 80 |
| metaphor | 8 | 1 | 34 | 79 |
| concept | 162 | 45 | 557 | 73 |
| question | 10 | 0 | 22 | 69 |
| syllogism | 1 | 0 | 2 | 67 |
| example | 2 | 2 | 6 | 60 |
| mathematical_construct | 1 | 6 | 5 | 42 |
| historical_reference | 57 | 64 | 47 | 28 |

Author verdicts on record (`concept_triage`): 288 confirmed, 578 rejected,
10 merged, 4 retyped. Two of three rejected. One rejection in 578 states a
reason.

### 2.2 Edges

| status | count |
|---|---|
| declared (author-settled) | 154 |
| inferred (awaiting) | 161 |
| rejected | 372 |
| retired (an endpoint retired) | 882 |

Of 1,569 edges, 1,254 (80%) are rejected or retired. `edge_triage`
verdicts: 47 confirmed, 84 rejected, 37 retyped; 0 rejections carry a
reason. Relations ranked by how often they ended rejected or retired:

| relation | settled | pending | rejected+retired | bad % |
|---|---|---|---|---|
| co_occurs | 0 | 0 | 138 | 100 |
| defines | 4 | 6 | 94 | 90 |
| motivates | 3 | 3 | 44 | 88 |
| elaborates | 9 | 12 | 138 | 87 |
| creates | 2 | 15 | 109 | 87 |
| refutes | 3 | 5 | 49 | 86 |
| answers | 2 | 3 | 24 | 83 |
| specializes | 5 | 12 | 65 | 79 |
| leads_to | 16 | 27 | 163 | 79 |
| distinguishes | 0 | 4 | 15 | 79 |
| contrasts_with | 19 | 19 | 122 | 76 |
| illustrates | 19 | 9 | 81 | 74 |
| foreshadows | 5 | 1 | 16 | 73 |
| permits | 10 | 21 | 81 | 72 |
| depends_on | 32 | 21 | 85 | 62 |
| generalizes | 25 | 3 | 30 | 52 |

The three relations the author keeps are structural (`depends_on`,
`generalizes`, `contrasts_with`, `illustrates`). The five worst (`defines`,
`motivates`, `elaborates`, `creates`, `refutes`) are the ones whose prompt
definitions (`extraction.md:52-64`) ask the model to infer a mechanism the
text rarely states. `co_occurs` is no longer in `VALID_RELATIONS` and
survives only as 138 dead rows.

### 2.3 Proposals and adjudication

| kind | adopted | dismissed | open |
|---|---|---|---|
| note_update | 14 | 862 | 102 (76 concepts) |
| revival | 1 | 26 | 68 |
| vanished | 5 | 48 | 33 |
| alias | 5 | 90 | 3 |
| variant_of_retired | 1 | 11 | 4 |
| edge_reproposal | 0 | 14 | 0 |

`note_update` is adopted 1.6% of the time it is decided. The 210 open rows
date from 2026-07-26 to 2026-10-18; all but the 33 `vanished` (which collect
raises, not extraction) are older than 2026-09-06, the last extraction.

Adjudication (304 passes, 1,825 candidates): 813 new, 77 improves, 174
subsumed, 297 dropped, the rest unjudged. The screen passed 45% as "new"
against an author record of 33%; it was tuned on its own prompt's
calibration text (`adjudication.md:21-27`) with no measurement after.

### 2.4 Twenty recent inferred edges, judged against the text

The twenty most recent `inferred` edges (all 2026-09-05/06, hierarchy.md and
neighbours), each checked against the chapter paragraphs where both
endpoints occur. "co-paragraphs" is the number of paragraphs in the whole
book containing both endpoint names by `concept_pattern`.

| # | edge | co-para | verdict | why |
|---|---|---|---|---|
| 1 | Franz Boas elaborates Cultural relativism | 0 | reject | Boas is a cited source (mixing.md footnote); a person does not elaborate a doctrine. No span. |
| 2 | ideological population specializes ranking | 1 | reject | the text makes it a kind of population, not a kind of ranking |
| 3 | Jati specializes endogamy | 1 | reject | Jati is "the endogamous population"; endogamy is a practice. Category error. |
| 4 | population depends_on accord | 32 | confirm | "A population is a bundle of individual trajectories in sufficient accord" |
| 5 | rank depends_on ranking | 9 | confirm | "A rank is a place in the ordering" |
| 6 | ranking depends_on population | 10 | confirm | "A hierarchy is the ordering, by that metric, of a population" |
| 7 | ranking depends_on metric | 7 | confirm | same sentence |
| 8 | Deception born to perish contrasts_with Sustaining deception | 0 | reject | neither name occurs as a term; both nodes unconfirmed paraphrases |
| 9 | Good Choice leads_to capacity for Becoming | 3 | confirm | "action that enlarges the capacity for Becoming" |
| 10 | Good Choice depends_on Awareness | 3 | confirm | "The good choice requires ... awareness of what may yet arise" |
| 11 | Dharma creates Samsara | 5 | retype | Samsara is named as the Dharma's own play, not a creation |
| 12 | Discernment defines Experience | 74 | retype | "Experience requires distinction": Experience depends_on Discernment, reversed |
| 13 | Jati leads_to Caste | 2 | reject | the text says caste maps jati to varna; no causal claim from jati |
| 14 | Jati generalizes Race | 0 | reject | no span; both are demes, neither generalizes the other |
| 15 | endogamy permits Jati | 1 | reject | same sentence as #3; Jati is the endogamous population, not permitted by it |
| 16 | ranking leads_to rank | 9 | reject | duplicate of #5 reversed |
| 17 | metric leads_to ranking | 7 | reject | duplicate of #7 reversed |
| 18 | supremacism leads_to Contraction of discernment | 1 | retype | "treated it as a contraction of discernment": specializes, not leads_to |
| 19 | Caste leads_to supremacism | 0 | reject | plausible, but no paragraph asserts it |
| 20 | memetic population specializes population | 3 | confirm | correct, but the text says "mimetic"; the node name never occurs |

7 confirm, 3 retype, 10 reject: the same one-in-three the author's record
shows. Five of twenty have no paragraph containing both endpoints at all,
which the current `endpoint_in_text` gate cannot see because it tests each
endpoint against the whole payload separately. Three are mirror duplicates
of a kept edge. One (#20) names a concept with a word the book never uses.

### 2.5 The bar the new pipeline must clear

Measured on a replay of the same candidates against the author's recorded
verdicts (§6.1), before any live run:

| measure | today | required |
|---|---|---|
| concept candidates the author confirms | 33% | at least 70% |
| edge candidates the author confirms or retypes | 50% | at least 75% |
| edges with no co-paragraph reaching triage | 25% of sample | 0 |
| mirror-duplicate edges reaching triage | 15% of sample | 0 |
| note_update proposals adopted | 1.6% | at least 25% |
| decisions asked of the author per full run | 605 (track-a §4) | at most 150 |

## 3. Principles

1. **The author is the last gate, never the first.** Nothing reaches triage
   that the pipeline cannot defend with a quoted span and a score.
2. **The text is the authority; summaries propose, the text grounds.**
   Every concept and edge carries the sentence(s) it was read from, verified
   verbatim against the unit. A summary may suggest a candidate; it may
   never be the evidence for one (ruling of 2026-09-06, lens design §1).
3. **Ratified knowledge is context, not target.** The model sees the
   author's notes for the concepts the unit realizes and is asked only what
   this unit adds; settled notes stay machine-unwritable
   (`proposals.py:1-15`).
4. **Author verdicts are a labelled dataset, not a prompt appendix.** 866
   concept verdicts and 168 edge verdicts exist. They calibrate the gate
   offline and are retrieved whole as precedents, not windowed to 40.
5. **The schema stays.** Every table and column keeps its name and type;
   §4.8 lists what is reinterpreted.
6. **One payload, two minds** (lens design §2). The assembled payload is
   printable, hashed, and identical whether the `[llm]` model or a subagent
   answers it.

## 4. The design

### 4.1 Unit of extraction: the section, inside its essay

The section stays the unit, as ratified 2026-08-10, for one reason the
sample confirms: an edge is assertable only where both endpoints sit in one
argument. A paragraph is too small (the four-link chain of hierarchy.md
spans two paragraphs); an essay is too large (god.md is 7,460 words, and a
40-concept cap is where over-production came from).

One **extraction call per section**, carrying the essay frame (§4.3). One
**adjudication call per essay**, over every candidate its sections
produced. SMSTTD today: 34 toc units, 265 sections, 1,949 paragraphs,
92,425 words.

### 4.2 Context each extraction call carries

Blocks in order, each with a sha256 header, assembled by
`extraction.assemble(db, manuscript, file, section) -> ExtractionPayload`
(same shape as `lenses.assemble`, lens design §4):

- **S, law.** The extraction prompt. Under it, the file's effective style
  law rendered from `styles.effective_style(db, mid, file)`
  (`styles.py:149`): lexicon and protected terms are
  what tell the model which capitalised words are terms of art ("Quality",
  "Space") and which are English.
- **G, glossary.** The graph's slice for this file (`api.scoped_concepts(
  file=)`, the call `summaries._concept_slice` already makes at
  `summaries.py:587-600`): name, aliases, kind, ratified notes,
  `introduced_in`, and the settled edges among them. Not the alphabetical
  200-name inventory of `extraction.py:843-851`; the slice is what this
  essay actually uses.
- **P, precedents.** For this essay, every retired extracted name whose
  `introduced_in` or last located file was this file, with the author's
  reason where one exists; plus the 20 nearest retired names by Jaccard to
  the section's own capitalised terms. Whole, not windowed.
- **M, summaries** (reserved, §4.3). Labelled absence when none exist.
- **E, the section.** With its heading, its essay's title, and the
  paragraph numbers `[n]` in the same form `summaries._numbered_paragraphs`
  uses (`summaries.py:58-66`), so every candidate can cite a paragraph.

Replaces: the `KNOWN CONCEPTS:` line, `triage_feedback`, and the unlabelled
concatenation of `=== file ===` chunks.

### 4.3 The summary interface (reserved input from the sibling session)

The sibling session is designing paragraph, section and essay summaries.
This pipeline reserves block M for them and asks for this contract:

```python
summaries.for_unit(db, manuscript, file) -> {
  "essay":    {"summary": str, "source_hash": str,
               "introduces": [str], "assumes": [str], "promises": [str]},
  "sections": [{"heading": str, "paragraphs": [int, int],
                "summary": str, "terms": [str], "source_hash": str}],
  "paragraphs": [{"n": int, "gist": str, "terms": [str],
                  "source_hash": str}],
}
```

Any level may be absent (`None`); the payload then prints `SUMMARIES: none
for <level>` and the run proceeds. A level whose `source_hash` does not
match the current text is printed as STALE and used only as a candidate
source, never as grounding. What each level is used for:

- **Essay.** `introduces` is the **primary concept-candidate list** for
  the essay: the summariser already produces INTRODUCES, ASSUMES and
  PROMISES (`prompts/summarizer.md`, the labels after MOVES), and a term the
  essay summary says it introduces is a stronger prior than a term the
  section model names on its own. `assumes` seeds cross-essay `depends_on`
  candidates (this essay depends on a concept introduced earlier), the one
  edge class a section cannot see. `promises` seeds `foreshadows`.
- **Section.** `terms` bounds the edge search: an edge candidate whose
  endpoints are not both among the section's terms or glossary entries is
  rejected before the model call. The section summary replaces the whole
  essay as the frame for a section call, which is what keeps the call
  small.
- **Paragraph.** `gist` and `terms` are the cheap grounding pre-check: a
  candidate citing `[n]` whose name is not in paragraph n's terms and not in
  its text is dropped deterministically. Paragraph gists also give the
  adjudicator a one-line context per candidate instead of the three-sentence
  located quote (`adjudication.py:121-147`), which is what let candidate
  payloads balloon.

What summaries are **not** used for: deciding a candidate's verdict, or
grounding an edge. Grounding is the quoted span against the section text,
always.

### 4.4 Candidates, scores, gates

The extraction call returns, per candidate:

```json
{"concepts": [{"name", "kind", "notes", "paragraphs": [n], "span": "<verbatim>",
               "confidence": 0.0-1.0, "why": "<one clause>"}],
 "links":    [{"from", "relation", "to", "paragraph": n, "span": "<verbatim>",
               "confidence": 0.0-1.0, "why": "<one clause>"}]}
```

Deterministic gates, in order, each a named skip count:

1. **Span.** `span` must occur verbatim (whitespace-folded) in the cited
   paragraph of the section. Else `ungrounded`. This replaces
   `endpoint_in_text`, which tested each endpoint against the whole payload
   and let 5 of 20 sampled edges through with no shared paragraph.
2. **Both endpoints in the span's paragraph.** For a link, both names (or
   aliases) by `concept_pattern` **inside the cited paragraph**, not the
   payload. Else `ungrounded`.
3. **Term-of-art check.** A single-word name must match by
   `mention_pattern` (`concepts.py:246-260`, case-kept) at least once; a
   name that only ever matches case-blind is an English word, not a term
   (the "Experience" failure, #12). Else `common_word`.
4. **Recurrence bar**, unchanged (`hygiene.py:204-213`) for
   `concept | metaphor | example`.
5. **Retired-name ban**, unchanged, for names the author retired. A name a
   migration run retired (`retired_from.by_run`) is not banned: the ban is
   the author's no, and §4.10's door opens only on text written after the
   retirement, so a machine retirement that banned would silence the name
   for good, and it is not a precedent in block P either (found on the
   first live run, 2026-10-25: the migration had
   retired `hierarchy` and `rank`, and 36 of hierarchy.md's 67 candidates
   then died as unknown endpoints). No lexical near-miss (ruling §0.9):
   the ban tolerates case and plurals, and block P shows the adjudicator
   the nearest retired names with reasons, which is the same check done
   semantically.
6. **Mirror and duplicate edges.** A link whose reverse exists among the
   batch or the settled edges, or that duplicates a settled edge, is folded
   into the stronger one (`duplicate`): #16 and #17 never reach the author.
7. **Relation whitelist for extraction.** `VALID_RELATIONS` stays the
   schema's vocabulary, but extraction may **propose** only the relations
   the author keeps more than half the time: `depends_on`, `generalizes`,
   `specializes`, `contrasts_with`, `illustrates`, `permits`, `answers`,
   `foreshadows`. `defines`, `motivates`, `elaborates`, `creates`, `refutes`,
   `distinguishes`, `leads_to` remain available to the author by hand and
   to retype, and are re-enabled per relation when the replay (§6.1) shows
   the new gate keeps them above 60%.
8. **Confidence floor.** Below the knob, logged, not queued.

Score = model confidence × grounding strength (1.0 when the span is a
definition-shaped sentence or heading, 0.7 otherwise) × precedent factor
(0.5 if a retired name is within 0.72 Jaccard, 1.0 otherwise) × summary
agreement (1.2 if the essay summary's `introduces` names it, capped at 1).

### 4.5 Adjudication, once per essay, on a stronger model

The screen that exists is kept in shape and moved to a stronger model,
chosen at run time with its cost shown (ruling §0.2). Input: every surviving candidate of the essay with
its span, score and paragraph gist, block G, the whole precedent set for the
essay, and the essay summary. Verdicts stay `new | improves | subsumed |
drop` for concepts and `new | subsumed | drop | retype` for links, with
**a reason per verdict** stored in the evidence row's metadata. Only `new`
and `retype` reach the author; `improves` becomes a `note_update` proposal
through `proposals.create` as today. The adjudicator may not add candidates.

A per-essay ceiling (knob) on what reaches triage, ranked by score; the
remainder is logged `screened` and listable with `extract screened <file>`.

### 4.6 Author verdicts feed back

- **Precedents, whole.** Block P and the adjudicator read every rejection
  for the essay and the nearest 20 overall. The `LIMIT 40/20/30/20/15`
  windows go.
- **Reasons.** Prompted, optional (ruling §0.1): when given, `concept_triage` and `edge_triage` rows
  carry `metadata.explanation` verbatim (as `proposals._record_evidence`
  already does at `proposals.py:523-545`), and the precedent line reads
  "rejected: <name> — <reason>".
- **Calibration, re-measured per run.** After each triage batch,
  `extract calibrate` replays the batch's candidates and prints precision
  per kind and per relation against the author's verdicts; a relation that
  falls under 60% leaves the proposable set until it recovers. Thresholds
  move on measurement, never by hand-edit of the prompt.
- **Prompts improve by hand, measured by replay.** The pipeline never
  edits a prompt. The owner edits `prompts/extraction.md` or
  `prompts/adjudication.md` (shown by `authorlm prompts show`), then
  `extract calibrate --replay` prints precision before and after the edit.
  The skip-count tallies per essay are the prompt's bug reports: a rise in
  `ungrounded` means the model paraphrases spans, a rise in `common_word`
  means it names English words as terms. The span and endpoint gates
  themselves are rules, not thresholds, and calibration never loosens
  them; what improves is the model's compliance, which those tallies
  measure.
- **No belief seeding from extraction** (§0.3). Extraction evidence stays
  in `SYSTEM_EVIDENCE_TYPES` (`db.py:479-480`) with `supports_belief` NULL,
  and proposal-triage dismissals of machine proposals stop seeding
  `editorial_beliefs`. Only an author's review explanation does.

### 4.7 Aliases

Unchanged in rule (naming ceremony only, `extraction.py:1114-1126`), but
the alias candidate must cite its paragraph and the sentence is checked
against that paragraph, not the flattened payload (`extraction.py:1127`).
A candidate concept whose name is within 0.72 of a live concept's name or
alias is sent to the adjudicator as a **subsumption question**, never
written as a node: #20 ("memetic" for "mimetic") becomes an alias proposal
on the live concept, not a new node.

### 4.8 Columns reinterpreted (no DDL)

| column | today | after |
|---|---|---|
| `concept_nodes.metadata` | `{origin, confirmed, realized_at, realized_version, retired_from}` | adds `extraction: {run, file, section, paragraphs, span, score, adjudicator, reason}`; `origin` stays `"extracted"`; `confirmed` keeps its meaning |
| `concept_edges.evidence` | always `"[]"` | JSON list of grounding spans `[{run, file, section, paragraph, span}]`; one per run that re-found the edge |
| `concept_edges.support` | always 0 | `len(evidence)`: distinct runs that grounded it |
| `concept_edges.metadata` | `{retired_from}` | adds `extraction: {score, adjudicator, reason}` |
| `concept_edges.relation` | 16 values incl. dead `co_occurs` | same vocabulary; extraction proposes the §4.4 subset |
| `knowledge_proposals.source` | `"extraction"` | `"extraction/<run>"` so a run's proposals can be listed and superseded together |
| `knowledge_proposals.state` | `open \| adopted \| dismissed \| demoted` | adds `superseded` (machine clean-up, excluded from the firewall's states at `proposals.py:69-72`, `144-146`) |
| `evidence.evidence_type = extraction_adjudication` | one row per pass, totals in `target` | one row per **candidate** verdict, reason in `metadata`; `signal` ∈ `new, improves, subsumed, drop, retype, ungrounded, common_word, duplicate, below_floor, migrated_out` |
| `manuscripts.metadata.last_extracted_version` | watermark | kept; adds `extraction.runs: [{id, started, model, units, cost}]` |

`triage_rules.py` keeps its rules; `concept_unmentioned` and
`edge_unmentioned_endpoint` become redundant with gate 1 and are retired
with the old module.

### 4.9 Verbs

- `extract <file> [--section H]` prints the assembled payload (polarity as
  `filter run`: the call needs the flag); `extract --native` makes the
  calls; `extract --full --native` runs the book.
- `extract screened <file>`, `extract calibrate`, `extract runs`.
- `extract --dry-run` runs everything and writes the would-be rows to a
  report, touching nothing.

### 4.10 Retired names: a warning that yields to insistence

Today a retired name recurring in changed text raises a `revival` proposal
(`extraction.py:938-955`) from a single mention, on every collect: 68 sit
open, 26 dismissed, 1 adopted. The ban on silent re-entry stays. The door
changes:

- The first time a retired name clears the recurrence bar in text written
  after its retirement, one revival proposal is raised, with the spans.
  Adopting it revives the node (`concepts.py:178-211`) and clears the ban.
- Dismissing with a reason sets `metadata.retired_for_good = true`; the
  name is never asked about again, only logged as `screened`.
- Dismissing without that mark re-asks only when the name's recurrence
  count has doubled since the last ask.

Migration of the 68 open revivals under this rule: those below the bar in
the current text are superseded (§5.3); those above it are re-asked once
with spans. Ruling §0.8.

## 5. Migration

Order matters; each step is a verb with a report, and nothing is deleted.

### 5.1 Backup

`sqlite3 ~/.authorlm/authorlm.db ".backup ~/.authorlm/backups/
pre-extraction-v2-<date>.db"` (WAL-safe). The rollback in §5.6 is this
file.

### 5.2 The 119 unconfirmed nodes and 161 inferred edges

Not retired wholesale. Each is re-run through gates 1-7 against the current
text (its span is re-located by `concept_pattern`, then the strict gates
apply) and the per-essay adjudicator, as a candidate batch tagged
`run = migration-1`. Survivors stay pending with a fresh score and reach
the author in one triage batch per essay. Failures are retired with
`retired_from` set (so `revive_concept` works, `concepts.py:178-211`) and
an `extraction_adjudication` row with `signal = migrated_out` and the gate
that failed. On the sample, that is 5 edges out by gate 2 and 3 by gate 6
before any model call.

### 5.2a Confirmed concepts the text no longer carries

Ruling §0.10. `extraction_migration.absent_confirmed`: a confirmed
extracted concept whose name and every alias match nowhere in the eligible
files is retired in the same run, `retired_from.by_run` set, one
`extraction_adjudication` row `signal = migrated_out`, `gate = absent`.
Measured on a copy of the live workspace 2026-10-25: 47 rows (the replay's
53 less six still carried by an alias or a file outside the chapters).
`--undo` revives them with the rest.

### 5.3 The 210 open proposals

| kind | open | action |
|---|---|---|
| note_update | 102 | state `superseded` (§0.4); the new run re-raises any that still clear the floor, now with a span |
| revival | 68 | §4.10: below the bar `superseded`, above it re-asked once with spans |
| variant_of_retired | 4 | `superseded` |
| alias | 3 | kept open: they carry a quoted sentence already |
| vanished | 33 | kept open: raised by collect, not extraction |

### 5.4 Retired rows and verdict history

Untouched. The 685 retired extracted nodes are the precedent set; the 866
concept and 168 edge verdicts are the calibration set.

### 5.5 The first full run

1. `extract calibrate --replay` on the historical candidates (§6.1), no
   author cost, until §2.5 is met.
2. `extract --full --dry-run` on a copy of the workspace, report reviewed
   by the owner: counts per essay, the proposable set, the cost.
3. Block M wired to the summary hierarchy of PR nagarkar/authorllm#268
   (ruling §0.7).
4. `extract hierarchy.md --native` on the live workspace (ruling §0.6);
   the author triages that one batch.
5. `extract --full --native`, one essay at a time in reading order, each
   essay's triage batch offered before the next essay runs.

### 5.6 Rollback

No feature flag and no parallel code path (no back-compat during
prototyping): the old modules are replaced. Rollback is `git revert` of the
build commits plus restoring the §5.1 backup. Because every migration write
is tagged `migration-1` or a run id in metadata, a partial rollback
(reopen the superseded proposals, revive the `migrated_out` rows) is a
scripted inverse and is written with the migration.

## 6. Measurement and cost

### 6.1 The replay is the acceptance test

`tools/measure_adjudication.py` (track-a §4) already replays the 914
historical extracted concepts and 573 relationships against the author's
verdicts with zero author cost. The new gates and adjudicator are measured
the same way, and §2.5 is the pass mark. The one honest limit: historical
candidates have no spans, so gate 1 is replayed by re-locating each name in
the text of the version it was extracted from (`manuscript_versions.files`
holds every version, `extraction.py:359-366`). Of every paragraph that
carries the name, the row is judged on the one whose sentence grounds it
most strongly (`grounding_strength`: a definition-shaped sentence or a
heading before a passing mention), first in reading order among equals;
the first mention alone sent `hierarchy` to be judged on "hierarchy of
processes enveloped within each process" in another essay (2026-10-25).
The migration (§5.2) relocates the same way, against the current text.

### 6.2 Prompt-cache economics

Blocks S, G, P and M are identical across the sections of one essay, so an
essay's section calls share a cached prefix; only E changes. The payload is
hashed per block for exactly this reason.

### 6.3 Cost of a full run (prices from `usage.price`, 2026-10-25)

| | calls | input tokens | output tokens | model | cost |
|---|---|---|---|---|---|
| extraction, per section | 265 | ~3K each (≈800K) | ~0.5K each | gemini-2.5-flash ($0.30 / $2.50 per M) | ~$0.60 |
| adjudication, per essay | 34 | ~12K each (≈410K) | ~2K each | gemini-2.5-pro ($1.25 / $10 per M) | ~$1.20 |
| adjudication, per essay (alternative) | 34 | same | same | Claude Opus-class | ~$12 |
| replay calibration | one extraction-equivalent | | | | ~$2 |

A whole-book run on flash plus pro is under $3; under $15 with an
Opus-class adjudicator, which is the ruled default (§0.2); the run-time
prompt shows these figures for the chosen scope before the first call. Incremental runs after a collect are one or two
sections. For comparison, the usage ledger shows $2.49 spent on all API
calls to date.

## 7. Knobs

| knob | where | proposed | note |
|---|---|---|---|
| `[extraction] enabled` | config.toml | `false` until §5.5 step 1 passes | existing |
| `[extraction] unit` | config.toml | `section` | `paragraph` and `essay` accepted for measurement runs |
| `[extraction] model` | config.toml | `gemini/gemini-2.5-flash` | section calls |
| `[extraction] adjudicator_model` | config.toml | an Opus-class Claude model | default only; ruling §0.2 |
| `[extraction] adjudicator_prompt` | config.toml | `default` (shipped; `ask` in code) | ruling 9: unattended, the opus-class default taken without a question; `ask` lists Opus-class, gemini-2.5-pro, gemini-2.5-flash and an OpenAI model with each one's estimated cost before the first call; `default` skips the prompt; `--adjudicator <model>` overrides |
| `[extraction] confidence_floor` | config.toml | `0.70` | gate 8; re-measured by `calibrate` |
| `[extraction] max_concepts_per_essay` | config.toml | `12` | reaches triage; the rest screened |
| `[extraction] max_links_per_essay` | config.toml | `15` | same |
| `[extraction] proposable_relations` | config.toml | the eight of §4.4 gate 7 | per-relation, moved by `calibrate` |
| `[extraction] precedent_jaccard` | config.toml | `0.72` | `loop.NEAR_DUPLICATE`, unchanged |
| `[extraction] precedents_nearest` | config.toml | `20` | block P, overall nearest |
| `[extraction] summaries` | config.toml | `prefer` | `require` waits for the sibling; `ignore` for A/B |
| `[extraction] max_section_chars` | config.toml | `12000` | a longer section splits at paragraph boundaries; never truncates |
| `[extraction] thinking_budget` | config.toml | `0` for extraction, model default for adjudication | |
| `[extraction] run_cost_ceiling_usd` | config.toml | `20` | a run stops and reports when exceeded |
| recurrence bar | `hygiene.py:204-213` | unchanged | ratified 2026-08-08 |
| `NOTE_UPDATE_PARAPHRASE_FLOOR` | `extraction.py:39` | `0.90`, unchanged | |

## 8. Rejected alternatives

- **Keep the two-step pipeline and tune the prompt.** Track-a's ceiling
  (37% fewer rows with a perfect adjudicator) is the most the current shape
  can give; the candidates themselves are ungrounded.
- **Paragraph as the unit.** Edges span paragraphs; 1,949 calls per run.
- **Essay as the unit.** The 40-cap over-production of §1.3, and god.md
  does not fit a cheap context with its glossary.
- **Pure deterministic extraction** (term lists, capitalised phrases).
  Track-a §1 measured the lexical route: a similarity cut removes 21 of
  914 candidates. The author's rejections are semantic.
- **Summaries as the authority for grounding.** Ruled out 2026-09-06.
- **A feature flag with both pipelines live.** No back-compat during
  prototyping; rollback is the backup and the revert.
- **Deleting the retired rows to "start clean".** They are the only
  labelled data the system has.

## 9. Build plan (after ratification; small commits)

1. `extraction.assemble` with hashed blocks S, G, P, M, E; `extract <file>`
   prints it. Block M reads `summaries.for_unit` and prints absence.
2. The candidate contract and gates 1-8 with named skip counts; hermetic
   tests on hierarchy.md fixtures for #1-#20 of §2.4.
3. The per-essay adjudicator with reasons; per-candidate evidence rows.
4. `extract calibrate --replay`; §2.5 as the pass mark.
5. Migration verbs: `extract backup`, `extract migrate [--dry-run]`
   (§5.2-5.3), and `extract migrate --undo <run>`. BUILT 2026-10-25
   (`extraction_migration.py`); rehearsed gates-only on a copy of the live
   workspace: 118 pending concepts (110 kept, 8 out), 161 inferred edges
   (60 kept, 101 out: 47 ungrounded, 43 not proposable, 8 duplicates, 3
   unknown endpoint), 210 proposals (102 note_update and 4
   variant_of_retired superseded, 45 revivals superseded and 23 re-asked
   with spans, 3 alias and 33 vanished kept). The adjudicated run is what
   cuts the 110 further.
6. Retire `adjudication.py`, `triage_feedback`, the inventory line, and the
   two redundant `triage_rules`; `docs/track-a-two-step-extraction.md`
   marked superseded by this document. BUILT 2026-10-25, in two steps.
   First `adjudication.py`, its prompt, test and `[extraction] adjudicate`
   knob, the two rules, and track-a marked superseded. Then the old
   single-call path itself: `extract_concepts`, `triage_feedback`, the
   KNOWN CONCEPTS inventory line, the `--edges-only` / `--aliases` modes
   and `prompts/extraction-legacy.md` are deleted, with their three
   registry entries and `tools/measure_adjudication.py` (its numbers stand
   in track-a §4). `api.run_extraction` is the non-interactive front on
   `extraction.run`, the MCP tool `extract_concepts` its one caller (with
   an `adjudicator` argument); under ruling 9 `init`, `collect` and
   `write complete` no longer extract at all, and `init --no-extract` is
   gone with the behaviour it switched off. A
   section reply with a null where the contract wants a list counts as
   unanswered (the watermark stays), and `render_run` reports each alias
   candidate's fate. The eighteen end-to-end sites drive `extraction.run`
   through the test stub, which now answers the §4.4 contract; of the six
   `test_api` checks, the alias rulings (§4.7) and the materiality floor
   were ported onto `extraction.run`, the prompt-provenance and
   skip-bucket checks were dropped (the registry check stays; the gates'
   named skip counts are `test_extraction_gates`), and the inline
   watermark / alias-hit / null-reply blocks became `api.run_extraction`
   checks. Dropped with the old contract, not replaced: `edge_reproposal`
   proposals from extraction (a rejected relation is now held at gate 5),
   the whole-text aliases sweep, and the hierarchical-pass fallback
   (sections split into parts instead).
7. Skill update: `.claude/skills/authorlm/SKILL.md` on the new verbs and
   on what a triage batch now shows (span, score, reason). DONE 2026-10-25.
