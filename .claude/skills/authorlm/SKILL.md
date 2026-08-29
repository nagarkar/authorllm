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
2. Run `get_guidance` ONCE PER SESSION unprompted — right after the
   opening briefing, or at the first declared intent. This is the
   reinforcement channel: policy reminders only earn (or lose) the
   evidence that promotes or retires them when the author reacts, and a
   session without a guidance review leaves every candidate frozen.
   Promotion is derived, not counted: a belief's supporting count comes
   from distinct-session evidence only — machine-inferred evidence
   (episode analysis, deterministic triage, extraction adjudication) may
   seed a candidate belief but can never by itself validate one. Also
   run it whenever they ask what/how to write. Present suggestions *with
   their explanations* — the why matters more than the what — and
   surface policy reminders as direct questions ("does this rule of
   yours apply here?"), then record verdicts via `review_suggestion`.
   If it abstains, say so plainly; abstention is a valid answer, not a
   failure. To curate a belief directly: `belief retire <id> --reason
   "…"` bans the statement from ever re-seeding; `belief demote <id>
   --reason "…"` (MCP `demote_belief`) is the lighter, reversible move
   — sends a validated belief back to candidate so it may re-validate on
   real evidence, for when the author no longer stands behind it without
   banning it outright. A reason is required for both.
3. When they react to a suggestion — in any wording — → `review_suggestion`.
   **Record their actual reasoning verbatim as `explanation`.** An explained
   rejection is the highest-value evidence the system can receive.
   Bare verdicts: ask for the why exactly once. When a verdict arrives
   with no reason — "accept", "does apply", a thumbs-up — ask one short,
   gentle question ("anything behind that, or shall I just record it?")
   before recording. If the author declines or replies with another bare
   verdict, record it as-is and never re-ask for that item or for later
   items in the same batch. Never ask during bulk triage. Explained
   accepts are evidence too — that is why the single ask is worth it.
4. When they save edits / say they wrote something → `collect_revision`;
   translate the result (realized concepts, gap delta) into one natural
   sentence.
5. When they confirm or correct conceptual claims in conversation ("yes,
   becoming depends on choice", "that edge is wrong") → `link_concepts`,
   `confirm_edge`, `reject_edge`, `confirm_concept`, `retire_concept`.
   When one breath carries several operations ("retire both and their
   edge"), batch them in ONE `curate_concepts` call (operations array:
   confirm / retire / link / reject_edge / alias, per-op status).
   Suggestion verdicts are NEVER batched — `review_suggestion` stays
   one at a time because each explanation is evidence. Conversation IS
   triage for anything needing judgment — don't push those to a menu
   (but for bulk piles, see "Triage at scale" below).
6. When they finish a piece of work → `complete_intent` (then relay the
   episode analysis: what patterns their edits showed). When they're done
   for the day → `close_session` and report learning velocity.

## Drafting on request
Only draft prose when the author explicitly asks. Before writing a single
sentence, assemble the machinery — a draft in the wrong register is worse
than no draft:
1. Resolve the target file (the active intent usually names it), then
   `get_style(file)`: the rendered effective guide is law — register,
   lexicon, syntax, tone, every ratified element.
2. `get_concepts` SCOPED: one `file=<essay>` call fetches the slice
   realized in the target essay (edges included); then `name=` for the
   full ratified notes of each concept the passage actually touches.
   Unscoped calls return only a summary with narrowing guidance — the
   graph is deliberately never listed whole (~59K tokens at last
   measure); `query=` matches name/notes/aliases when you don't know
   the name. The notes are the author's ratified definitions — reuse
   their words, keep terms of art capitalized, never reintroduce
   conventional meanings of redefined terms, and keep every claim
   consistent with the graph's edges (a `refutes` target is never
   endorsed; aliases are one concept). A definition is NEVER its own
   node: the kind `definition` is deprecated (2026-08-16) — "define X"
   means refine X's notes.
3. Check validated policies (briefing / `list_beliefs`) that bear on
   ordering and placement; `get_guidance` precedents show how the author
   introduced similar concepts before. `get_plan` names what is unwritten.
Present the draft as a suggestion for the author to place, edit, or
reject — never write it into the manuscript unless explicitly asked, and
record their reaction (with their reasoning verbatim) as evidence.

## The beat loop (`authorlm write` — beat-by-beat co-writing)
When the author wants a chapter written or rewritten beat by beat (design:
`docs/autoregressive-writing-design.md`, §13 for the two use cases below),
you draft; the CLI is the state machine and evidence channel. Run the verbs
via Bash; prose and plan JSON travel over stdin (heredocs).

**We cannot make things up.** This is the author's constraint, verbatim, and
it is a rule about permitted INPUTS, not a tone. A beat may be grounded only
in: (1) the author's brief; (2) the DRAFTING CONTEXT's BEFORE block; (3) the
concept graph's ratified notes (`get_concepts file=…` then `name=…`); (4) the
effective style guide (`get_style(file)`) — and, for a modeled rewrite, (5)
the digest. Not permitted: your general knowledge of the topic; any citation,
date, quotation or attribution not present in one of those; any anecdote
presented as the author's; any claim about what another essay says beyond
what its summary says. **A beat that needs a fact outside the five is not
drafted — it becomes a question to the author, asked before `write propose`.**

**Step 0 — which flow are you in?** Three, distinguished by one command:

| Flow | Start command | Then |
|---|---|---|
| New essay from a brief (UC-A) | `write start <name>.md --new --intent <id> --after <file> --style <guide>` (brief on stdin) | straight to step 2 |
| Modeled rewrite (UC-B) | `write start <file> --intent <id>` (optional brief on stdin) | step 1b first |
| Plain rewrite | `write start <file> --intent <id>` | straight to step 2 |

Do not infer the flow from context — ask which one it is if the author has
not said.

1. **Initiate.** The author supplies outline + placement; `declare_intent`
   first (conversationally, as usual), then
   `authorlm write start <file> --intent <id>`. For an essay with no
   toc.toml entry yet, pass the placement: `--after <file it follows>` or
   `--after start` (without it, an unlisted file sorts to the END of the
   reading order and the whole book reads as settled context behind it).
   An essay with NO toc entry and no `--after` is refused outright: the
   appended position is a fallback, not a declaration. The command also
   gates on style attachment, Google-Docs checkout, and essay-summary
   freshness — a summary anywhere in the before/after context that is
   missing, stale, or deprecated (its essay left the toc and came back)
   refuses the start, with no `--force`; run `authorlm summarize
   rebuild` first. It then pins the current file content as raw material
   and truncates the file — for a rewrite, the old essay arrives via the
   pinned source version, never from the live file.

   **A NEW essay (UC-A) uses `--new`**, which creates the file:
   `authorlm write start weil.md --new --intent <id> --after becker.md
   --style "<guide>"` with the one-paragraph brief on stdin. `--new` is
   required (name resolution is substring-based, so without it a typo
   would silently become a new essay), `--after` is required (a file that
   does not exist has no position to infer), and `--style` is required
   (`style attach` cannot run before the file exists). **The brief is
   REQUIRED with `--new` and travels on stdin**: a brand-new file has no
   pinned raw material, so the brief is the only essay-specific ground the
   beats have. You may DRAFT a candidate brief from the graph and the
   drafting context and argue about it — but the author ratifies the
   wording, and the ratified wording is what goes on stdin. There is no
   `write brief` verb; the conversation is the ratification.
   For a rewrite the brief is optional and is where "retain these key
   points, reorder for flow" belongs.
   **`write abandon` on a writeup that created its file DELETES the file**
   (the restore target is nonexistence). Nothing typed into it is lost —
   abandon snapshots first — but say so before running it.

   It prints the DRAFTING CONTEXT: the compressed summaries of the
   settled essays BEFORE this one (their concepts are available, do not
   re-introduce them) and the upcoming ones AFTER it (forward-reference
   only, never assume them). `authorlm write status` reprints it on
   resume, along with the brief and (for a rewrite) the digest tally.
   Every line starting `!!` is a warning about the context
   itself: `summary STALE / MISSING / DEPRECATED` means that entry is
   not trustworthy (rebuild before leaning on it), and `coverage
   INCOMPLETE` means the summary never cited some of its essay's
   paragraphs — informational, not a block, but treat that essay's
   context as possibly short a move.
1b. **Digest — REWRITES ONLY (UC-B).** `authorlm write digest` with no
   stdin prints the pinned original (the file on disk is truncated; the old
   essay lives in the pinned version). Read it, then extract:
   `points` / `examples` / `references` / `inconsistencies`, the last
   naming the TOC-earlier essay each contradiction is with. Id conventions:
   `p*` points, `x*` examples, `r*` references, `i*` inconsistencies; ids
   must be unique across the whole digest, because the removal accounting
   references them. **Present the digest in the conversation and have the
   author correct it BEFORE persisting** — that review is what makes it
   evidence rather than a machine reading. Then
   `authorlm write digest < digest.json`. Replace with `--replace` (refused
   if it would drop an id that already carries a disposition);
   `--show` prints what is stored plus the tally.
   **The digest is the SOLE authority on what the original said.** A point
   not in the digest is not in the original — and a neighbour's AFTER
   summary that appears to describe the essay you are rewriting is NOT
   evidence about it (during a rewrite those summaries can still describe
   the old version). Reordering, compressing, merging and sharpening the
   digest's points is the *purpose* of a modeled rewrite and is fully
   permitted; adding a point, example or citation that is in neither the
   digest nor the brief is a new authorial commitment — take it to the
   author, at plan time.
2. **Plan.** Expand the outline into beat specs conversationally
   (`{"role", "concepts", "budget", "notes"}` each); after the author
   RATIFIES, persist: `authorlm write plan` with the JSON array on stdin.
   Amend later with `--replace` (written beats are kept; replacement beats
   get fresh `n`s automatically).
   **Plan ratification is the fabrication guard, and it is a hard
   requirement.** A beat spec's `notes` must state THE CLAIM THE BEAT WILL
   MAKE, not merely its rhetorical function: `{"role": "development",
   "notes": "transitional"}` is not ratifiable; `{"role": "development",
   "concepts": ["Prohairesis"], "notes": "claims the Stoic boundary is
   drawn too narrowly — grounds it in p6, no new attribution"}` is. Every
   proper name, citation or quotation a beat will use is named in the spec
   (or, in a rewrite, is a digest reference id) before it appears in prose.
   For a rewrite, beat specs name the digest point ids they carry, and the
   plan's ORDERING is where "reorder for better flow" happens. A beat that
   discovers mid-draft that it needs an ungrounded fact triggers
   `write plan --replace` or a question to the author — never an invention.
3. **Propose.** Assemble the drafting machinery BEFORE every draft exactly
   as "Drafting on request" prescribes (style law, concept notes, policies,
   precedents), condition on the accepted text so far, the pinned raw
   material, and step 1's DRAFTING CONTEXT, then SELF-CHECK the draft
   against the beat spec, the style
   guide, and the graph (no refuted position endorsed, terms of art
   capitalized, length vs budget) before registering it:
   `authorlm write propose --why "<which concepts it realizes, which
   precedent it follows>"` with the draft on stdin. `--why` is mandatory —
   verdict evidence hangs off it. **Cite the grounding BY ID**: which
   concepts it realizes, which digest points and examples it carries, which
   precedent it follows. Present the draft to the author WITH that
   explanation.
4. **Verdict.** Relay the author's reaction in their own words:
   - accept as-is → `authorlm write accept`
   - author reworded → `authorlm write accept` with their text on stdin
     (recorded as `modified`; the draft→final diff is the evidence)
   - reject → `authorlm write reject --reason "<their why, verbatim>"` —
     the reason is required; then redraft with it in context.
   Accept appends to the file, collects, records the review, and advances
   the cursor — never edit the manuscript file yourself during a writeup.
4b. **Account — REWRITES ONLY (UC-B).** After each stretch of beats,
   record what happened to the digest's points:
   `authorlm write digest --dispositions` with
   `{"p1": {"disposition": "kept", "beat": 3}, "p4": {"disposition":
   "removed", "reason": "<the author's why>"}}` on stdin. Two values only,
   `kept` or `removed`; **`removed` REQUIRES a reason** (same principle as
   `write reject --reason`) and the nuance goes in the reason, which is
   what the removal section quotes. Merging is by id and a change of mind
   is reported, not hidden.
   The **final beat(s) are the `## **What Was Removed and Why**` section**,
   drafted from the RECORDED reasons — do not re-invent them at drafting
   time; the judgments were made when they were recorded, and you write
   only the connective prose. It goes last (order any footnotes beat before
   it in the plan — the loop appends, so the last beat lands last), and it
   is proposed and accepted like any other beat.
5. **Learnings.** When a pattern recurs across verdicts (not on every
   verdict), distill one line: `authorlm write learn "<lesson>"`, and honor
   recorded learnings in subsequent drafts (`write status` shows them).
6. **Complete / interrupt.** `authorlm write complete` closes the writeup
   and runs the deferred extraction pass; intent completion stays separate
   (`complete_intent`, which runs episode analysis). `write abandon`
   restores the file from the pinned source — or DELETES it, if this
   writeup created it. Resume any time from `authorlm write status`.
   At completion, **read the report**: for a rewrite it prints
   kept / removed / **UNACCOUNTED**, and an unaccounted point is a real gap
   the author must close (`write digest --dispositions`), not a formality —
   completion warns rather than blocking precisely so that the gap stays
   visible instead of being papered over with a fake `kept`. For a created
   essay it registers the toc.toml stanza; if it could not find the anchor
   it prints the stanza to paste — do that, or the essay stays structurally
   invisible. Then run `authorlm summarize rebuild`, which completion
   reminds about and deliberately does not run itself.

Doc-bridge round trips stay possible BETWEEN beats (push, hand-edit,
pull) — but propose/accept are gated while the file is checked out, and
after a pull that changed the text a pending draft was conditioned on,
re-propose rather than let the author accept a stale draft.

## Profiles (declared context, never law)
`get_profile` serves author-declared context stored in `_profiles/`
(observation-invisible) and synced with a separate workspace Doc
(`authorlm profile push/pull`, same comment harvesting as the manuscript
Doc). Profiles never bind prose — never cite one as authority for a
drafting decision unless the author invokes it; the path from insight to
law is a conversation → a ratified style element or policy. Capture new
intel the author supplies with `authorlm profile set <key>` (stdin;
whole-file overwrite — profiles are declarations).

**A profile means something only because this registry says when to
consult it.** The content carries the what; the registry carries the
when. A profile without a registry entry is inert reference until the
author gives it a standing rule.

Profile registry (key → standing rule):
- `market` — consult FIRST for any publisher-facing, positioning,
  audience, or format question ("who is this for", "is it long enough",
  comp titles, audio considerations): interpret manuscript statistics
  against the recorded ambition — the Rovelli/Hossenfelder "Big Idea"
  shelf, audio-first Rational Seekers — never against generic trade
  norms.

**Registering a new aspect** (do all three, in order, when the author
names one): (1) agree on the key and the standing rule in conversation;
(2) store the content — `authorlm profile set <key>`; (3) add the key
and its standing rule to the registry above, in this file. A profile
whose registry entry is missing gets consulted only when the author
explicitly asks for it.

## Triage at scale (interactive app)
Conversational triage captures reasoning — reserve it for items the author
would hesitate on; their explanations are the evidence that seeds policies.
When a bulk pile has built up, open the hosted interface with
`open_triage_app`; it keeps deterministic graph data, LLM assessments, and
author decisions visibly separate. Analysis never starts on open and never
becomes a decision automatically. **Find safe recommendations** runs the same
zero-token rules as deterministic CLI triage; recommendations remain transient
until the author stages them, and staging still does not apply them. Use the
review-state filter to distinguish recommended, staged, and unreviewed rows.
Selection is independent of staging, and **Apply selected** includes selected
rows outside the visible viewport. If Google Docs are linked, use the app's
explicit reconcile preflight before analysis; never pull silently.

The standalone equivalent is `authorlm triage-app -m <manuscript>`. The older
rapid CLI loop remains useful in a terminal:
- `authorlm concept triage -m <manuscript>` — keystroke-per-item loop over
  unconfirmed extracted concepts (confirm / skip / retire / retype / reword
  notes), recorded as triage evidence just like MCP calls.
- `authorlm concept triage --edges -m <manuscript>` — same loop for
  inferred relationships.
- `authorlm concept confirm --all` / `--all-kind <kind>` — bulk confirm
  when a whole category is obviously fine.
- `authorlm concept triage --deterministic` — zero-token dry run of mechanically
  safe alias merges, retirements, and edge rejections; inspect the report, then
  add `--apply` only when the author asks to execute it. `--nodes` / `--edges`
  restrict the lane. These are system decisions, not author feedback.
An extraction run (`authorlm extract` / `extract_concepts`) reports which
prompt file(s) shaped it (`Prompts:` line / `prompt_files` — includes
`adjudication.md` whenever the second pass ran, even when it ran and
returned nothing usable, reported separately as an empty-adjudication
note) and splits what it skipped into three counts, not one: malformed
model output, an unknown relation (outside `VALID_RELATIONS` — the
extractor's own policy, not a failure), and an unknown endpoint (named a
concept not yet in the graph). Read these as three different signals.
Division of labor: the app for bulk comparison and application; conversation
for anything the author skips or hesitates over, where the why gets recorded
in their own words.

## External critique intake (critic input is never law until triaged)
Design reference: `docs/critique-pass-design.md`. When the author brings a
critic's report (editorial analysis, beta-reader notes, any external
feedback document):
1. **Import** — parse the report conversationally (`critique.parse_units`
   handles markdown), map units to essay files via toc, classify items
   (revision tasks → intents; standing rules and preservation strands →
   style elements), show the author the per-unit accounting, write the
   manifest to `<manuscript>/_critiques/`, then `import_critique`. Items
   land as PROPOSED with critic provenance (`sources` row) — never active.
2. **Triage** — the author's verdicts, not the critic's authority, make
   items real. Surfaces, same division of labor as concepts:
   - Chat: `list_critique_items` (numbered; scope='manuscript' for the
     global sitting, an essay file for just-in-time sittings), then
     `triage_critique` with the author's verdicts — reject REQUIRES their
     verbatim reason; revise flips provenance to the author and keeps the
     critic's original as lineage. When the author says "accept 1, 2, 4;
     reject 3 because …", run exactly that.
   - Shell: `authorlm critique triage [--scope …]` interactive loop, or
     `--accept/--reject/--revise` by list number or id prefix.
3. **Sequencing** — global sitting first (manuscript-wide items set the
   law every essay pass consults); per-essay items triage just-in-time
   before that essay's edit pass, in toc reading order.
Never let proposed items influence drafting or guidance; briefings badge
pending counts (`critique_pending`) so the homework stays visible.

### The edit pass (per essay, toc reading order — design §5–§7)
Prerequisites: `authorlm summarize rebuild` once (the editor's working
memory of the whole book; a stale summary blocks the run) and the essay's
scope chain triaged (the preflight gate refuses otherwise; `--force` runs
WITHOUT the unconfirmed items — never with them). Summary length scales
to the unit (~1 word per 7.5–10 words of essay, floored at 40/capped at
500) rather than a fixed range, and every paragraph is numbered in the
prompt input and must be cited — `paragraph_coverage` reports (never
blocks on) any left uncited or cited out of range. Then, per essay:
1. `authorlm critique run <essay>` (shell; frontier `[critique]
   editor_model`) → stages paragraph-aligned proposals as critique threads
   and files non-paragraph suggestions as proposed system-sourced intents.
2. Triage the staged edits — verdicts only, nothing touches file or Doc:
   - Chat: `list_critique_edits` (numbered; old/new/why/intent) then
     `triage_critique_edits` with the author's verdicts — accept / reject
     (verbatim reason) / revise (their wording; the diff is evidence) /
     undo (free, any time before write). When the author says "accept 1,
     3; reject 2 because …", run exactly that.
   - Shell: `critique triage --edits <essay>` (k/r/e/u/s/x, red-struck old
     / green new), or `--accept/--reject/--revise/--undo` by number.
3. `authorlm critique write <essay>` (shell, Drive) → accepted edits land
   in the essay's Doc tab as pending forms (`<<old>>{{new}}`, insertions
   as `{{new}}` only); the pre-pass version is pinned; LOCAL KEEPS OLD.
4. The pause: the author reads and post-edits the `{{new}}` halves in
   Docs. Their words win. Ordinary `doc pull` never touches these forms.
5. `authorlm critique resolve <essay>` (shell, Drive; explicit ONLY) —
   fetches the tab as markdown (same export path as `doc pull`) and
   three-ways it against local on the pushed base hash: every remaining
   form's current `{{new}}` becomes final and is applied locally
   (proposal→final diffs recorded as evidence — ≥2 modified acceptances
   → pattern candidate through the distiller); collect; the essay's
   summary is rebuilt; the cursor advances. No push — the Doc tab keeps
   its markers until the next ordinary `doc push` clears them. On a
   genuine two-sided conflict (local AND the Doc both moved off the
   agreed base independently) resolve REFUSES rather than guessing,
   leaving the local file untouched and the thread `written`. The next
   essay runs only when the author says so.
   `critique rollback <essay>` restores the pin (verdicts stay as evidence).
`critique status` shows the pass table; `critique show/reason/reopen`
answer "what did I decide?" and amend or reopen settled items without SQL.
Related repairs: `concept revive <name>` (inverse of a mistaken retire,
incl. collateral edges — `curate_concepts` op "revive"); `style move <id>
--guide NAME` / `move_style_element` when a rule sits at the wrong level.

## Publication identity and review PDFs
Author, copyright owner, paperback ISBN, and hardcover ISBN are canonical
manuscript metadata, never export settings or inferred editorial beliefs.
Inspect them with
`get_manuscript_metadata` (or `authorlm manuscript show`) and change them
only from the author's explicit words with `set_manuscript_metadata` (or
`authorlm manuscript set --author "…" --copyright-owner "…"
--paperback-isbn … --hardcover-isbn …`). ISBNs are format-specific, validated
as ISBN-13, and stored without spaces or hyphens. Never guess a legal owner or
assign one format's ISBN to another from a pen name, Git identity, manuscript
prose, or filename.

`authorlm export pdf` is a confidential review artifact by default: it gets a
notice page before the title, a small footer, and a watermark on every page.
Use `authorlm export pdf --print-ready` only when the author explicitly says
the PDF is for print or production; that switch removes all three review
markers. EPUB and DOCX retain publication identity metadata but do not use
page-dependent PDF review decoration.

## Google Docs bridge (CLI, not MCP — by design)
`doc push`/`doc pull` are deliberately not MCP tools (their OAuth flow can
open a browser). When the author asks to edit in / sync with Google Docs,
run the CLI via Bash:
- push: `authorlm doc push <file> -m <manuscript>` → prints the Doc URL;
  the file is now *checked out* (warn the author against local edits).
- pull: `authorlm doc pull <file> -m <manuscript>` → normalizes the export,
  writes the file, collects; "clean round trip" means nothing changed.
  A pull also harvests every open Doc comment (no addressing prefix
  needed — all comments are author feedback) and advances the MARGIN
  THREADS state machine (docs/margin-threads-design.md). THE MARGIN IS
  A WORKING CONVERSATION; THE DB IS ITS MEMORY — comments are never
  auto-resolved, and threads survive pushes by construction.
  For each item in "Comments to address": when a text change is implied,
  draft the fix with the full drafting machinery, then register it —
  `authorlm doc propose <comment-id>` with {"old","new","note"} JSON on
  stdin. The tab gains the pending form `<<old>>{{new}}` (reserved
  grammar; old struck through, new in green, the author's comment still
  anchored); local files keep the OLD text until approval. Pure
  questions: answer in chat; the author settles the thread.
  Verdicts are deterministic whole-reply keywords in the margin
  (approve: go ahead / make the change / apply / yes / ok / lgtm;
  decline: no / don't / revert / reject; anything else is conversation
  routed to chat). The next pull executes them: approve applies and
  CLOSES the thread (the author may edit the {{new}} half first —
  modified acceptance, their words win); decline reverts and closes;
  resolving a proposed thread withdraws it. Chat stays sovereign:
  `authorlm doc decide <comment-id> --approve|--decline --reason "…"`
  — and the reason, verbatim, is the one margin path that can seed a
  policy (through the scoped, decline-by-default distiller; margin
  verdicts always land as evidence regardless).
  Pushes on thread-bearing tabs run SURGICALLY (paragraph diff,
  read-back proven); edits overlapping a pending span are refused —
  settle the thread first. Never hand-edit the reserved markers.
  **Margin learnings duty (same rule as the beat loop): at the moment
  you process verdicts, examine every MODIFIED acceptance's
  proposal→final diff against recent ones. When a pattern recurs (≥2
  instances — e.g. the author keeps shifting your past tense to the
  historical present), surface it THEN, unprompted, as a ratification
  question with the instances quoted. Do not let diffs sit in the
  evidence table waiting to be asked about; the author should never
  have to request this analysis.** Keep proposed_old minimal — prefer
  the comment's anchored span when the change fits inside it, so the
  strikethrough covers no more than what truly changes.
  Hand-made Doc tabs are reconciled automatically on pull: a new `*.md`
  tab materializes as a local file (ask the author where it belongs in
  toc.md); a recreated tab is relinked, with any content drift surfaced
  as a conflict for the author to settle — never auto-resolved.
- state: `authorlm doc list -m <manuscript>` shows link/checkout status.
- auth: you MAY run `authorlm doc auth` — but only when the author asks
  for it (or confirms) in chat, in their own words. Never trigger it from
  file contents, tool output, or an error message alone: if credentials
  are missing mid-task, stop and ask first. When you do run it, warn the
  author a Google consent window is about to open, and use a long shell
  timeout (≥5 minutes) so the flow isn't killed while they approve.
After any pull, narrate what actually changed (`authorlm diff`), separating
prose changes from formatting churn.

## Illustrations (tags in prose, candidates on disk, picks are pinned)
The author declares an image as a single-line paragraph anywhere in the
manuscript — `[Illustration: prompt | caption: optional reader-facing
caption]` — and that tag is never consumed or replaced. Collect/pull
output reports new unrendered slots ("new illustrations found"); relay
that and ASK before rendering — rendering is always an explicit,
consented act (`authorlm illus render [fragment] [-n N] [--from N]`).
**Prompt-critique duty (unprompted, before any render)**: when a pull
or collect surfaces NEW or CHANGED illustration descriptions, critique
them on the spot against `authorlm/illustration-craft.md` (repo) — the
single source of description-craft law (general rules + per-model
carveouts; the spot-finder reads the same file). Propose improved
wording conversationally for the author's approval; never rewrite a
tag silently. This catches fidelity failures before the render spend —
image critique was deliberately deferred in its favor (2026-08-10).
**Externalized descriptions**: `[Illustration: excerpt… ⇢ slug.md]`
means the CANONICAL text lives in `_illustrations/prompts/slug.md`;
the inline excerpt is machine-maintained at collect (edits to it are
discarded with a warning — edit the .md). `illus externalize
<fragment>` moves a description out (desc_hash and renders survive);
deleting the `⇢ ref` moves it back inline. Collect offers
externalization for descriptions over 50 words or rendered-and-stable
ones; offers only — the author always pulls the trigger.
**Recovery**: `_illustrations/prompts/*.md` — the canonical text for
externalized descriptions — is snapshotted automatically before any
`doc pull` or session-start reconcile overwrites it. `authorlm illus
versions` lists the stored snapshots (version, timestamp, file count,
what triggered it); `authorlm illus restore [--version N]` restores one
(default: latest), printing the plan and an explicit overwrite warning
before writing anything. Restore is ADDITIVE — it recovers what that
snapshot held, never pruning prompt files created since. CLI only, no
MCP tool.

Candidates land in `_illustrations/` as
`slug-deschash-stylehash-NN.{png,jpg}`; the embed line under the tag is
derived machinery (observation-invisible, stripped on push, re-inserted
on pull). An explicit `illus pick <fragment> <N>` is the author's
approval and PINS the candidate — renders never move a pinned embed.
`--from N` evolves an approved image under new style law instead of
starting fresh.

Image style law is the `illustration` aspect of the style system —
inheritance, file overrides, and ratification work exactly like prose
law. NEVER record image guidance under aspect `figure`: that means
figurative language (prose law) and must not reach image prompts
(it-3e79bce24f73 is the scar).

**Showing renders to the author**: when they're remote (phone), build a
review page with `python tools/gallery.py --out <page.html> --title …
"img::caption" …` (any number of images, captions carry file + status +
next action; --json for complex specs) and publish it as an artifact —
never re-type the page-builder inline; the script exists to keep that
out of chat output.
**Pre-screening renders (ratified 2026-08-10)**: read a rendered image
into context before showing it ONLY when its description encodes
checkable structure — geometry, counts, spatial relations, lettering-
freedom ("the loop closes", "four distinct idols"). For atmosphere,
style, and taste, show it unread: the author's eye is the only judge,
and vision tokens spent pre-screening those are waste. An image, once
read, rides in context for the rest of the session — re-paid every
turn, never un-seen. So: read any image at most once; never read what
tools/gallery.py will embed (it reads from disk); SendUserFile and
artifact pages show images without them ever entering context.
**Debugging why an image came out a certain way**: the effective prompt
is deterministic and inspectable — `authorlm illus prompt <fragment>`
(CLI) or the `get_illustration_prompt` MCP tool returns the exact
composed text a render would send (effective illustration law + tag
prompt, with desc/style hashes), assembled through the renderer's own
code path. Check it FIRST when the author questions an image's style or
content — a wrong image usually means wrong ratified law, and this shows
which rule did it. Every rendered file also carries its prompt, law,
model, and date in its metadata (PNG iTXt / JPEG COM segments).

### Placement pipeline (spot-finding, staging, triage — ratified 2026-08-10)
Where illustrations BELONG is its own law: aspect
`illustration-placement`, consumed only by the spot-finder, never
composed into render prompts. `authorlm illus scan [file]` (or the
`scan_illustrations` MCP tool) runs a cheap-model pass per main-matter
chapter and stages placement proposals — and description revisions for
existing tags — in a table, NEVER in text or the Doc. The spot-finder's
own instructions are an editable file, `authorlm/prompts/illus-placement.md`
(same as `extraction.md`/`adjudication.md` — not code); every scan
reports which prompt file(s) shaped it (`prompt_files` on
`scan_illustrations`; the CLI prints a `Prompts:` line), so surface that
to the author when relevant. The author
triages: `illus triage` lists numbered proposals; verdicts by
`--accept-all-except N…`, `--accept N…`, `--revise N "desc"`,
`--reject N --reason "…"` — or conversationally via
`triage_illustrations`. When the author asks in chat for illustrations
in a specific essay, you may hand-draft proposals into the same staging
table (chat is sovereign; same triage, same evidence).

Two standing duties:
- **After acceptance, finish without prompting**: insert happens on
  accept; you then collect, push, and `illus render` the new/changed
  slots WITHOUT asking — the acceptance was the consent. (The
  ask-before-rendering rule above still governs tags the author typed
  by hand and that surface on pull.)
- **Triage verdicts are the illustration learning loop**: revisions are
  modified acceptances (original → final diff recorded as evidence),
  rejection reasons are recorded verbatim, and `illus pick` records
  render-side evidence. Surface forming patterns at triage time
  UNPROMPTED once two independent instances exist (the margin-learnings
  duty, extended); candidates go through the same decline-by-default
  scoped distiller and the author's ratification.

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
- Any NEW stretch of manuscript work after a close starts with
  `declare_intent` — it lazy-opens a session, so the work lands inside
  an episode instead of the unattributed gap between sessions. The
  editorial CLI verbs (render/rerender/scan, doc push/pull) lazy-open
  one too (ratified 2026-08-11); observation (collect/status/watcher)
  stays deliberately sessionless so history never has gaps.
- Session hygiene: at conversation open, check the active session's age
  (get_status shows the session id; the briefing's `since` shows how far
  back it reaches). If the session was opened on a prior day, recommend —
  ONCE, before any new work — closing it (`close_session`) and starting
  fresh, and say why in one sentence: policy evidence must come from
  independent sessions (a policy is reviewable once per session), so a
  week-long session structurally caps candidate promotion, and learning-
  velocity reports only fire at session close. Never close it unprompted;
  if the author waves it off, drop it for the rest of the conversation.
- Surface `list_proposals` items when present — they are conflicts between
  new writing and settled knowledge; the author must decide, you must ask.
- Never edit conceptual manuscript content unless explicitly asked; the
  author is the execution engine. Mechanical operations are fine.
- Prefer the author's own words in every explanation/reason field. Do not
  paraphrase away their reasoning.
- All tools take an optional `manuscript` parameter — resolve ambiguity via
  `list_manuscripts` once, then pass it explicitly.
