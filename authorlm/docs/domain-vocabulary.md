# Domain vocabulary — beliefs, law, hypotheses, confirmation

Read this before touching `editorial_beliefs`, `style_laws`, or anything
that writes to them. It exists because both tables were previously
misnamed, and the names actively caused wrong reasoning (see "Why this
document exists" below).

## The ontology

AuthorLM holds two kinds of knowledge, and the distinction is the spine of
the whole system. Everything is either something the machine **inferred by
watching** (bottom-up, carries confidence, can be retracted) or something
the author **declared** (top-down, binding, no confidence because it is not
a guess).

|                   | Bottom-up — learned, has confidence, retractable | Top-down — declared, binding |
| ----------------- | ------------------------------------------------ | ---------------------------- |
| **graph objects** | hypothesis — an unconfirmed concept or edge       | confirmed                    |
| **rules**         | `editorial_beliefs`                               | `style_laws` → composed into a guide |

There is exactly ONE door between the columns in each row:

- objects cross by **confirmation** (the author confirms a hypothesis)
- rules cross by **graduation** (`convert_belief_to_law` — a belief that
  kept being right becomes law)

Nothing crosses sideways. A belief never becomes an object; a law never
becomes a hypothesis.

## What each table is for

**`editorial_beliefs`** — what the machine has guessed about the author's
editorial judgment, by watching what they did (`episode-analysis`) or by
distilling what they said (`review-explanation`, `margin-thread`,
`triage-*`). Every row carries `supporting` / `contradicting` /
`confidence` and moves through `candidate → validated → retired` under
`_lifecycle_status`. **Beliefs are never authored directly.** A belief that
accumulates contradiction demotes and then retires — that is the retraction
mechanism, and it is the reason the counters must keep accruing for a
belief's whole life, including after it starts acting.

**`style_laws`** — what the author declared. Composed per file through a
single-parent guide tree with `overrides` displacement (`effective_style`);
composition is deterministic set arithmetic, **no model in the loop**. The
author ratifies every row. The composed result is what the code calls
"law" (`placement_law`, `illustration_law`, "the effective guide is law").

The practical consequence: an unratified rule must NEVER be written into
`style_laws`. If the machine wants a rule to act before the author has
blessed it, that rule stays a `validated` belief — acting on a belief is
allowed, promoting an unblessed rule into declared law is not.

## Aspect naming

`style_laws.aspect` names the **governed artifact**, never the machinery
that consumes it — `register`, `lexicon`, `illustration-placement`,
`concept-note`, `concept-identity`. Never `triage-*`. A law is a claim
about an artifact and stays true whether or not the pipeline that learned
it exists; naming it after the pipeline reintroduces exactly the confusion
this document was written to end.

The aspects must never mix: `figure` is figurative language for PROSE,
`illustration` is image-render law, `illustration-placement` is
where-images-belong law. Each consumer filters to its own aspect.

## Renames (2026-08-20)

| Was | Now | Why |
| --- | --- | --- |
| `editorial_policies` | `editorial_beliefs` | "Policy" reads top-down, but the table is 100% machine-inferred and stores `supporting`/`contradicting`/`confidence`; `reinforce_policy`'s own docstring already called it "a policy's belief record". |
| `style_elements` | `style_laws` | "Element" named the row's composition role but carried no normative sense; the codebase already called the ratified result "law" in 12 `_law` symbols, `PLACEMENT LAW`, `IMAGE LAW`, and "ratified prose law". |

Commits and RFC text predating 2026-08-20 use the old names. When reading
them: "editorial policy" means a machine belief, and "style element" means
a declared law. The word "policy" in pre-rename text never means
author-declared.

`style_policies` was considered and rejected: it recycles the word being
vacated, so every pre-rename document would read as its own opposite.

## Why this document exists

On 2026-08-20 a design session spent five questions reasoning from the
table NAMES rather than their contents — arguing that "editorial policies
are prose rules, so learned triage heuristics don't belong there," when in
fact the table already held 13 illustration rules and had never held a
single author-authored row. The error was only caught when the author
asked what the tables were actually for.

The lesson generalizes: **never reason from a table name in this codebase
without reading its module docstring.** `policies.py` and `styles.py` both
state their invariant in their first paragraph.
