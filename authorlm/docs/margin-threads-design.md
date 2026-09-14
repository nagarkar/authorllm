# Margin threads — in-context Doc comment conversations

Grilled and ratified 2026-08-08. The author's motivating pain, verbatim:
"I often cannot locate where you made the changes related to the
comments I had added to the doc because by the time you update the doc,
you have already removed all the comments."

Converges with the EditorLLM research's top finding (anchored
suggestions + resolution-as-verdict, docs/editorllm-research.md §2.1),
arrived at independently by the author with a stronger twist: the
conversation itself lives in the margin.

## The lifecycle

1. The author comments in the Doc (and edits freely, as today).
2. Pull: direct edits apply to local files immediately (unchanged).
   New comments surface to chat; the collaborator (Claude) drafts a
   concrete fix with the full drafting machinery (style law, concept
   notes), then registers it: `authorlm doc propose` — which EDITS THE
   TEXT with the pending-change grammar (below) and posts a short
   prefixed reply ("AuthorLM: proposed — <note>"). The change is
   visible in-context, anchored, before any approval.
3. The author sees, inline: struck-through old text and the proposed
   new text, with their comment still pinned to the spot. They reply
   with a verdict keyword, or with anything else (= conversation), or
   resolve, or ignore.
4. Pull advances the state machine (ADJUSTED 2026-08-08 after the
   first live run — author: with the in-line marker machinery, "the
   final go ahead or LGTM is enough for you to resolve the comment. We
   don't have to do another round for resolution," which also
   eliminates orphaned comments):
   - approval keyword → cleanup AND close: the pending span becomes the
     final text (the author may have edited the {{new}} half — modified
     acceptance, their version wins); the receipt is the RESOLVING
     reply ("AuthorLM: applied and closed — reopen or comment anew if
     it reads wrong"). The approval itself is the accept evidence; the
     in-context review already happened in the pending phase, where the
     anchor lives.
   - decline keyword → revert AND close ("AuthorLM: reverted and
     closed"); a decline reason, when present, is rejection evidence
     verbatim.
   - any other reply → conversation: routed to chat; nothing touches
     the text; the collaborator answers in-thread or revises the
     proposal.
   - author resolves a *proposed* thread without approving → withdraw
     sweep: the pending span reverts, thread closes as withdrawn, bare
     rejection recorded, no re-asking in the margin.
5. Every pull ends with the thread ledger in its output
   ("2 proposed, 1 applied awaiting review, 1 cleaned").

## The pending-change grammar (reserved in Doc tabs)

    <<old text>>{{new text}}

- `<<` inserted before and `>>` after the anchored span: boundary
  insertions, so the comment anchor SURVIVES, now wrapped.
- `{{new text}}` inserted immediately after: the proposal, visible in
  place. (Build refinement over the grilling, where only `<<`/`>>` were
  named: the insert half needs its own delimiters so stripping and
  reverts are unambiguous. Flagged to the author; veto welcome.)
- Visual polish via one updateTextStyle each: strikethrough on the old
  span, suggestion-green on the new. The ASCII markers remain the
  parseable truth; formatting is cosmetic.
- Pull-strip keeps local files canonical: while a proposal is PENDING,
  canonical text is the OLD text (`<<old>>` unwraps to old, `{{new}}`
  drops). Ratified content changes only at approval. Observation never
  sees markers (same doctrine as illustration embed lines).
- Unbalanced or hand-edited markers: never guess — warn in the pull
  output, leave the span alone, settle in conversation.

## Verdict grammar (deterministic; no LLM between words and manuscript)

- Approve (whole-reply match, case/punct-insensitive): "go ahead",
  "make the change", "apply", "yes", "ok", "lgtm".
- Decline: "no", "don't", "revert", "reject".
- Everything else is conversation.
- Chat stays sovereign: any thread can be decided in chat; the thread
  gets the same receipt as an in-margin verdict.

## Authorship and identity

Replies posted via the Drive API carry the author's own OAuth identity,
so the "AuthorLM: " prefix is the ONLY authorship signal — required on
every machine reply, and the parser treats un-prefixed replies as the
author's.

## State: the doc_threads table

Real state, never derived: comment_id (Drive), file, anchor_quote,
proposed_old, proposed_new, note, state, our reply ids, last processed
author-reply id (idempotency). States: proposed | conversation |
cleaned (approved and applied) | declined | withdrawn | stale.
Scope metadata uses stable ids (manuscript ms-*, guide sg-*); files are
path-keyed like the rest of the system (known rename limitation, shared
with style attachments; a file-identity layer is a separate design if
ever needed).

## Modified acceptance (discovered in the first live test, 2026-08-08)

The author may EDIT the {{new}} half in place before approving — that
is a modified acceptance, honored exactly like the beat loop's: the
rule is structure-exact, content-flexible. The OLD half must match the
record verbatim (it is what gets deleted); the new half is the
author's to rewrite, approval applies THEIR version, the original
proposal is kept in thread metadata, and the proposal→final diff is
evidence.

## Export encodings (learned the hard way, same test)

The Doc's markdown export escapes the markers (`\<\<`) and renders
strikethrough as `~~`; the normalizer unescapes angle brackets and the
pending grammar tolerates `~~` wrappers. Any new formatting applied to
pending spans must be checked against its markdown-export rendering.

## Exactness rule (drift)

An approval authorizes ONE exact textual transformation, not an
intention. Apply/revert fire only if the recorded structure
(`<<old>>{{new}}`, verbatim) is intact in the tab. Anything else marks
the thread stale: "AuthorLM: the passage changed since this proposal —
re-proposing", and a fresh proposal is drafted against current text.

## Push discipline

- Thread operations (marker inserts, cleanups, reverts) are small
  targeted batchUpdates — they always run.
- The surgical DIFF PUSH (step 3) replaces the wholesale tab rebuild:
  partial in means (edits only changed ranges), total in result (tab
  ends byte-equivalent to local, PROVEN by a re-export read-back;
  mismatch → one retry → loud conflict, never a silent rebuild).
  Anchors on unchanged text survive by construction.
- SHIPPED (step 3): thread- or comment-bearing tabs **without tables**
  push surgically by paragraph diff (export-space diff, positional
  paragraph mapping, temp-doc imports for changed paragraphs,
  read-back proof with one retry); edits overlapping a pending span
  refuse loudly. Tabs without open threads keep the battle-tested
  rebuild push. The interim defer-general-pushes rule is lifted.
- SHIPPED (2026-09-03, it-08b8b0a0c737 / e21a4ac): a tab whose local
  markdown contains a pipe-table delimiter row **always rebuilds**,
  even when threads or comments are open. Docs reports every cell as a
  paragraph while the markdown export carries the table as one block,
  so the two sides never align and `diff_push` refuses. Comment anchors
  orphaned by that rebuild are the known price — resolve open threads
  before pushing a table-bearing file.
- Checkout semantics unchanged.

## Evidence (the third channel)

Margin verdicts flow into the same evidence loop as chat reviews and
triage: author resolution of an applied thread = accept; declines with
reasons = rejection with the reason verbatim (highest-value). The
distillation guardrail, author verbatim: the learning "might be
generalized to that essay or can be generalized to a parent essay or
the whole document" — the distiller receives the file and its guide
chain, proposes the NARROWEST honest scope (file path | guide id |
manuscript id) as candidate metadata; and "if a general principle is
not possible and the discussion is overly specific to a particular
thread, we should not try to create a learning proposal by force of
habit or necessity" — decline-to-distill is the default posture;
evidence is always recorded, candidates only when a principle is real.
Margin-seeded candidates display their source and scope in reminders.

## Scope of the feature

Manuscript master Doc only (profiles keep harvest-and-resolve). Runs
inside every pull; no new ritual. Chat remains a full control surface.

## Doctrine rewrites (step 5, same session the feature ships)

- "The margin is never memory" → "the margin is a working
  conversation; the DB is its memory."
- "Comments never survive a push" → "threads survive pushes by
  construction."
- Reserved grammar (`<<`/`>>`/`{{`/`}}`) documented; approval keyword
  list documented; the propose verb joins the drafting rules (chat
  drafts, CLI is the state machine — the beat-loop division of labor).

## Status: ALL FIVE STEPS SHIPPED (2026-08-08)

Steps 4-5 landed with: margin_thread evidence rows on every terminal
verdict (accepted / modified with proposal→final diff / declined /
withdrawn); `doc decide --approve|--decline --reason` as the sovereign
chat door and the explained-verdict path; seed_margin_candidate with
the author's guardrail verbatim in the distiller prompt (decline by
default, narrowest honest scope, ids for guides/manuscript, path for
files); doctrine rewritten in the authorlm skill and README.

## Build order

1. Design doc (this file); doc_threads table; grammar + pull-strip;
   `doc propose` (marker edit + prefixed reply); `doc threads` ledger.
2. Verdict machine: reply parsing, approve→cleanup, decline→revert,
   withdraw, stale rule, read-back verification of every thread op.
3. Surgical diff push; interim rule lifted.
4. Evidence wiring + scoped decline-capable distillation.
5. Doctrine rewrite in the authorlm skill + README.

Edge cases inventoried during grilling: ambiguous replies route to
chat; double pulls are idempotent by comment id; watcher never sees
markers; threads on [Illustration:] tags flow into the normal
desc-hash/render-consent machinery; equivalence-failure protocol above;
author resolution of un-applied proposals = withdraw.
