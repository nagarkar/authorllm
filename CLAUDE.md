# authorllm

@~/.claude/metalm/guidelines/index.md

AuthorLM, an editorial collaborator for the owner's philosophy writing: a CLI (`authorlm`) and an MCP server (`authorlm-mcp`) over one SQLite workspace (`~/.authorlm/`), plus three local web pages (Triage App, pronunciation workbench, audiobook review page). It calls LLM vendors through LiteLLM (keys in `authorlm/.env`), the Google Docs and Drive APIs for the Doc bridge, and pandoc for exports. `audiostation/` is a Tauri desktop app that turns the audiobook folders AuthorLM writes into audio with ElevenLabs and ffmpeg.

## Commands

- Install: `cd authorlm && pip install -e ".[all]"` (builds the web pages; needs Node at the version in `authorlm/.nvmrc`)
- Test: `cd authorlm && for f in tests/test_*.py; do [ "$f" = tests/test_live_llm.py ] || python3 "$f" || break; done` (hermetic). `python3 tests/test_live_llm.py` sends any request without a recording in `tests/llm_cache/` to the live, billed model.
- Lint: none yet (`docs/design/exceptions.md`).
- Docs: `metalm-gendocs` regenerates `docs/generated/`; the pre-commit hook runs it.
- CLI: `authorlm <verb>`; see `.claude/skills/authorlm/SKILL.md`.

## Exceptions to metalm

- Adopted with grandfathered exceptions, one row each in `docs/design/exceptions.md`; new work follows metalm.

## Agent skills

- Issue tracker: GitHub issues on `nagarkar/authorllm`. See `docs/agents/issue-tracker.md`.
- Triage labels: defaults. See `docs/agents/triage-labels.md`.

## Repo notes (moved verbatim from AGENTS.md; to be sorted by the metalm migration)

### Response Style

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

### Domain vocabulary (read before touching the code)

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
