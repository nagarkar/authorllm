# AuthorLM Agent Instructions

## Response Style

- Keep outputs terse by default.
- Prefer a "Here's what you need to do" style when giving guidance.
- Lead with action items, not background explanation.
- Keep summaries to 3-5 bullets max unless the user asks for detail.
- Avoid long walkthroughs unless explicitly requested.
- After any AuthorLM tool output, immediately translate it into the next
  action for the author.
- When output includes comments, proposals, conflicts, warnings, or
  outstanding questions, surface them conversationally without waiting to
  be asked.
- If author judgment is required, ask exactly one concise question.
- Do not stop at status reporting when the output implies a next step.

## Domain vocabulary (read before touching the code)

AuthorLM splits all knowledge into what the machine **inferred by watching**
and what the author **declared**. Two tables, one door between them:

- `editorial_beliefs` — machine guesses, carry `confidence`, retractable.
  Never authored directly.
- `style_laws` — author-declared, binding, no confidence. Composed per file
  into "the effective guide is law". No model in the loop.

A belief graduates into law only when the author accepts it. An unratified
rule must never be written into `style_laws`.

Do NOT reason from a table or column name here — read the module docstring
first (`policies.py`, `styles.py` state their invariant in paragraph one).
Renamed 2026-08-20; pre-rename text says "editorial policy" for a belief and
"style element" for a law. Full ontology: `authorlm/docs/domain-vocabulary.md`.

When working on AuthorLM manuscripts or using the AuthorLM MCP server, read
this file first and follow it fully:

- `.claude/skills/authorlm/SKILL.md`

That file defines:

- how to use the AuthorLM MCP tools
- how to interpret guidance, collect, proposals, policies, and session state
- how to handle Google Docs reconciliation and margin threads

Do not use AuthorLM MCP tools until you have read that file.
