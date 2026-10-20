---
name: papers-physics
description: The physics field skill for the papers core, used with it. Adds the equations-in-outline stage, the programs and probes that produce every quoted number, the physicist and math-verifier referees, verbatim listings as appendices, and the local publish road. Use for a paper that derives, computes or models (the Choice Theory series from SMSTTD is the first case); load `papers` first.
---

# papers-physics: what physics adds to the core

Everything in `papers/SKILL.md` applies. This file lists only the differences.

## Stages the core leaves to the field

| core stage | in physics |
|---|---|
| **C3. Field outline** | the equations in outline: each with a one-line justification and the premise it rests on, no derivations yet; the physicist and the math verifier read it before the author's gate |
| **D. Field preparation** | the programs: one that runs the paper's object (a lattice model of the theory's operations) and the probes that produce every number the paper will quote. Their listings and printed outputs are appendices, verbatim, so a verifier can recompute them. The math verifier confirms every number by an independent method sharing no code. |
| **E. Body** | one derivation at a time; a derivation that a probe can test is tested before it is shown |

## Referees in use

| referee | checks | must do |
|---|---|---|
| physicist | derivations, hidden posits, overclaims, sign conventions, boundary conditions, whether "derived" is derived | rerun the probes it doubts; separate what is proven from what is read |
| math verifier | every identity symbolically; every printed number by an independent method sharing no code; listings byte for byte against the files | a table: claim, its value, the paper's value, verdict |
| line editor | house vocabulary, sentence length, duplication inside a section, citation form, terms marked as the series' own, the same claim stated consistently wherever it appears | ready-to-apply replacements |
| philosopher of physics | grounding in the chapters, what the book does and does not say, the amendments the paper implies | quote the chapter |

What these caught in the first project, which a drafter alone did not: a sign claimed as derived when it was a posit restated; a conjecture whose sign came out opposite to what its prose said; a factor of two in a self-term; a stability bound sitting exactly on its edge; three statements of one claim that disagreed; an abstract over its limit.

## What the paper carries

Beside what the core lists: an appendix per program and per probe with the listing and the printed output in fenced blocks, refreshed from a fresh run after any change to the program; every number in the text is one of those printed numbers; a glossary row for every term of the series with the book's nearest word; a boundaries paragraph naming every constant, dimension and posit the grammar does not fix.

## Publish road, local only

```bash
authorlm doc push papers/<paper>.md -m <Manuscript>
authorlm export pdf --file papers/<paper>.md -m <Manuscript>
authorlm export docx --file papers/<paper>.md -m <Manuscript> --upload --as-doc --drive-id <renderedDocId>
```

`--drive-id` is needed only on the first upload; later uploads of the same paper remember the rendered Doc and replace it in place.

Then the PDF goes to the author. The rendered Doc ids live in the project's `papersequence` tab.
