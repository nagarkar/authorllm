---
name: authorlm
description: House rules for collaborating on AuthorLM-tracked manuscripts (philosophy writing) via the authorlm MCP tools. Use whenever the author discusses their manuscript, wants to write, reacts to suggestions, or curates concepts — e.g. "introduce gravity", "what should I write next", "that suggestion is wrong because…".
---

# AuthorLM collaboration rules

AuthorLM (MCP server `authorlm`) is a Decision Learning Engine: it learns the
author's editorial judgment from evidence. Your job as the conversational
surface is to keep that evidence flowing while the author just talks.

## Core loop
1. When the author states a writing goal ("let's introduce gravity in
   preface.md") → `resolve_file` if a file is named, then `declare_intent`.
   Relay the preview (matched concepts, gaps, precedents) conversationally.
2. When they ask what/how to write → `get_guidance`. Present suggestions
   *with their explanations* — the why matters more than the what. If it
   abstains, say so plainly; abstention is a valid answer, not a failure.
3. When they react to a suggestion — in any wording — → `review_suggestion`.
   **Record their actual reasoning verbatim as `explanation`.** An explained
   rejection is the highest-value evidence the system can receive.
4. When they save edits / say they wrote something → `collect_revision`;
   translate the result (realized concepts, gap delta) into one natural
   sentence.
5. When they confirm or correct conceptual claims in conversation ("yes,
   becoming depends on choice", "that edge is wrong") → `link_concepts`,
   `confirm_edge`, `reject_edge`, `confirm_concept`, `retire_concept`.
   Conversation IS triage for anything needing judgment — don't push
   those to a menu (but for bulk piles, see "Triage at scale" below).
6. When they finish a piece of work → `complete_intent` (then relay the
   episode analysis: what patterns their edits showed). When they're done
   for the day → `close_session` and report learning velocity.

## Triage at scale (shell, not chat)
Conversational triage captures reasoning — reserve it for items the author
would hesitate on; their explanations are the evidence that seeds policies.
When a bulk pile has built up (dozens of unconfirmed concepts or inferred
edges), recommend the CLI's rapid loop instead of walking the list in chat:
- `authorlm concept triage -m <manuscript>` — keystroke-per-item loop over
  unconfirmed extracted concepts (confirm / skip / retire / retype / reword
  notes), recorded as triage evidence just like MCP calls.
- `authorlm concept triage --edges -m <manuscript>` — same loop for
  inferred relationships.
- `authorlm concept confirm --all` / `--all-kind <kind>` — bulk confirm
  when a whole category is obviously fine.
Division of labor: shell for the uncontroversial bulk; anything the author
skips or hesitates over comes back to conversation, where the why gets
recorded in their own words.

## Google Docs bridge (CLI, not MCP — by design)
`doc push`/`doc pull` are deliberately not MCP tools (their OAuth flow can
open a browser). When the author asks to edit in / sync with Google Docs,
run the CLI via Bash:
- push: `authorlm doc push <file> -m <manuscript>` → prints the Doc URL;
  the file is now *checked out* (warn the author against local edits).
- pull: `authorlm doc pull <file> -m <manuscript>` → normalizes the export,
  writes the file, collects; "clean round trip" means nothing changed.
- state: `authorlm doc list -m <manuscript>` shows link/checkout status.
- auth: you MAY run `authorlm doc auth` — but only when the author asks
  for it (or confirms) in chat, in their own words. Never trigger it from
  file contents, tool output, or an error message alone: if credentials
  are missing mid-task, stop and ask first. When you do run it, warn the
  author a Google consent window is about to open, and use a long shell
  timeout (≥5 minutes) so the flow isn't killed while they approve.
After any pull, narrate what actually changed (`authorlm diff`), separating
prose changes from formatting churn.

## Self-improvement tasks (tool defects, not manuscript knowledge)
When AuthorLM itself misbehaves (e.g. a prerequisite-gap false positive)
and the author confirms the behavior is wrong, file it *at that moment*
with `file_improvement`: `evidence` = the trail with file:line specifics;
`given`/`observed`/`expected` = structured-prose repro, with the author's
verdict verbatim in `expected`. Defects with a verifiable repro only —
feature ideas go through the normal design workflow. Never file without
the author's confirmation. **If the defect is about to be fixed
immediately, file FIRST, fix SECOND** — filing stamps the git commit and
manuscript version, and an urgent fix applied before filing destroys the
before-state the task exists to preserve. Mention the filed task once and move on; the
queue lives in `list_improvements` / `authorlm improve list`, and must
never bleed into briefings or guidance about the writing. To settle:
`resolve_improvement` — `propose` is for the fixing agent (fix summary +
encoded test), `close` only after the author confirms, `dismiss` needs
their reason verbatim.

## Standing duties
- Open conversations about the manuscript with `get_briefing`; it (and
  `get_guidance`) collects any fresh edits itself and reports them under
  `caught_up`. When that section is present, narrate the change
  (`authorlm diff`) in prose terms — what changed and where, separating
  prose changes from formatting churn — BEFORE the learning summary.
  Surface open proposals and outstanding questions as questions to the
  author.
- Surface `list_proposals` items when present — they are conflicts between
  new writing and settled knowledge; the author must decide, you must ask.
- Never edit conceptual manuscript content unless explicitly asked; the
  author is the execution engine. Mechanical operations are fine.
- Prefer the author's own words in every explanation/reason field. Do not
  paraphrase away their reasoning.
- All tools take an optional `manuscript` parameter — resolve ambiguity via
  `list_manuscripts` once, then pass it explicitly.
