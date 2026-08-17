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

When working on AuthorLM manuscripts or using the AuthorLM MCP server, read
this file first and follow it fully:

- `.claude/skills/authorlm/SKILL.md`

That file defines:

- how to use the AuthorLM MCP tools
- how to interpret guidance, collect, proposals, policies, and session state
- how to handle Google Docs reconciliation and margin threads

Do not use AuthorLM MCP tools until you have read that file.
