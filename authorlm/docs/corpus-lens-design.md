# The corpus lens — design reference

Ratified 2026-09-21 in a grilling session with the author. The authorlm half
of ytlm's D25 (`ytlm/docs/design-decisions.md`): a lens that reads one
essay and reports whether a transcript corpus bears out what the essay says
a tradition holds. First target: `indic.md` against the Indic corpora in
ytlm. Companion designs: `lens-architecture-design.md` (input classes,
payload, provenance), `critique-pass-design.md` (the road the report
rides), `interlocutor-design.md` (what this is NOT).

Status: DESIGNED, not built. Blocked on ytlm slices 2, 3 and 4 (clean-up,
glossary, extraction with the `claims` style and the register gate), by the
author's ruling that extraction runs only on cleaned transcripts.

## 1. What it is, and what it is not

A **corpus lens** reads one essay against evidence: anchored claims that
named speakers made in recorded talks, each with a timestamp that opens the
video at that second. It answers one question per essay claim about a
tradition: does the corpus bear this out?

It is not an interlocutor. The interlocutor reads the whole book from inside
a tradition and its loci are literature references the author must check.
The corpus lens's loci are transcript anchors a machine verifies; the author
rules on evidence, not on citations. The intent evidence brief and a
corpus-grounded interlocutor were considered and dropped from this design.

## 2. The artifact

A lens file, `_lenses/<name>.md`, with the ordinary lens front matter plus a
new input class:

```toml
---
class    = "chapter"
inputs   = ["glossary", "corpus"]
corpus   = ["cor-…advaita", "cor-…buddhism", "cor-…tantra", "cor-…jainism"]
---
```

`corpus` lists ytlm corpus ids. Each has `perspective` set in ytlm (D16), so
every claim in the payload names its tradition by construction. A video may
belong to several corpora; the author splits and names them in ytlm.

## 3. The payload

Assembled by `lens run`, deterministically, hashed block by block, printed on
demand, per lens-architecture principle 2 (one payload, two minds):

- the essay, the concept notes, the glossary: as for any chapter lens;
- **CLAIMS**: one block per ytlm shard, produced by the consumer adapter
  (`authorlm/adapters/ytlm.py`) running `ytlm render <ids…> --style claims
  --out <file>`. Every theory and fact from the listed corpora (questions and
  inference chains excluded), each with speaker, corpus perspective, locus
  `yt:<video_id>@<seconds>`, and the verbatim anchor. ytlm shards by whole
  videos in published order under its configured token budget; `lens run`
  loops over the shards, one subagent each.

ytlm never sees the essay. authorlm never opens a transcript file.

## 4. Findings and verdicts

Per essay claim about the tradition, one of:

| verdict | meaning | lands as |
|---|---|---|
| `supported` | loci agree | report only, as evidence |
| `qualified` | loci agree with a condition the essay omits | proposed intent |
| `contradicted` | loci disagree | proposed intent |
| `silent` | the corpus does not speak to it | report only, listed |

A finding quotes the essay passage verbatim and one or more transcript
segments verbatim, each with its locus and the tradition it speaks for.

## 5. Reconciliation across shards

A fresh subagent reads every shard report and the essay and writes one
report. It may merge findings on the same essay passage, drop duplicates,
mark conflicts (a support beside a contradiction), and re-judge a verdict in
the light of all shards. Constraint: every surviving finding, and every
re-judged verdict, cites only loci that appeared in some shard report.
Import drops anything else and counts the drops.

## 6. Two gates

1. `ytlm render register <answer>` verifies every transcript quote against
   the transcript at its locus; a miss drops the finding, counted.
2. `lens import` (this lens) verifies every essay quote against `indic.md`,
   as lens import already does for chapter quotes.

Each owner checks its own text. Neither system opens the other's files.

## 7. The road

Critique. Report and manifest land in
`_critiques/<date>-corpus-<lens>.{md,json}`; the manifest imports as
PROPOSED intents with critic provenance naming corpus id, video id and
timestamp, triaged with `critique triage` like any critic's. The fixes ride
the filter road, never a chat draft (author ruling 2026-09-06).

## 8. Running it, once built

With `$S` the scratchpad and `<ms>` the manuscript:

1. `authorlm lens run indic-corpus --file indic.md --out $S/indic.payload -m <ms>`
   prints the shard count and the hashes; no model call.
2. One subagent per shard, the default model, fresh context: read the shard
   payload in full, write the findings file its WRITE TO block names, say
   nothing else.
3. One reconciling subagent over the shard findings and the essay.
4. `authorlm lens import indic-corpus $S/indic.answer -m <ms>` runs the ytlm
   register gate through the adapter, then the essay gate, lands report and
   manifest, imports the intents.
5. Read the report back as prose, by essay section: the contradicted and
   qualified claims with speaker, tradition and timestamp; the supported
   ones in a line each; the silent list. Never the payload or a hash.

## 9. Prerequisites in ytlm, in order

Slice 2 whole (three clean-up passes and the Obsidian review round-trip),
slice 3 (glossary), slice 4 (`claims` style, sharded render, register gate),
`corpus remove` and the per-perspective split of the Dharma corpus, then the
consumer adapter (slice 6).
