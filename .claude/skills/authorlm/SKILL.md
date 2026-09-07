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
   **Give the intent a PLACE.** When the author names a file, pass it as
   `declare_intent(scope=<file>)`. When they do not, ask ONE line — "just
   this essay, the whole part, or the whole book?" — and never default
   silently: an unscoped intent attaches to EVERY future writeup, which is
   a real consequence and is theirs to choose. A part is named by its toc
   opener (`intent scope <id> --chapter <opener>` on the CLI). Say what the
   answer means in one clause when you record it.
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
`write draft --dry-run --out` assembles the payload to a file, a DRAFTER
SUBAGENT with an empty context answers it, `write critique --out` assembles
the critic's payload and a CRITIC SUBAGENT rules on the draft, and
`write propose --critique …` registers it (step 3); the CLI is also the
state machine and evidence channel; you orchestrate and relay, and you
never draft a beat in this conversation. Run the verbs via Bash; prose
and plan JSON travel over stdin (heredocs).

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
| New essay from a brief (UC-A) | `write start <name>.md --new --after <file> --style <guide>` (brief on stdin) | straight to step 2 |
| Modeled rewrite (UC-B) | `write start <file>` (optional brief on stdin) | step 1b first |
| Plain rewrite | `write start <file>` | straight to step 2 |

**`--intent` is no longer routine.** With no flag the intent set is DERIVED
from the scopes covering the essay; pass `--intent <id>` (repeatable) only
when the author reaches for a goal by name, and then the first flag is the
primary. See "Intents on a writeup", below.

Do not infer the flow from context — ask which one it is if the author has
not said.

**When the author asks to rewrite an existing essay and has not said which
kind, ASK ONCE, in author terms.** This is a real fork with different
consequences, and it has been missed because it was put as jargon
("full rewrite or modeled rewrite?") that read like a phrasing preference.
Put it as the two things that will actually happen, one line each, with the
recommendation attached — and NO verb or flag names in the question itself:

> Two ways to do this:
> - **I first extract every point, example, and reference from the current
>   essay; you rule on what's kept or dropped, and the final essay accounts
>   for anything removed.** (Recommended when the old content matters.)
> - **The old essay is raw material; nothing is tracked point-by-point.**

Recommend the first whenever the existing essay carries content the author
would mind losing — which is the usual case for anything already drafted.
Ask once, take the answer in whatever words it comes in, and proceed; do
not re-ask at each beat.

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
   (`style attach` cannot run before the file exists).
   **PROPOSE the style guide; do not ask an open question.** Before the
   start command, run `authorlm style guides`, read the placement
   anchor's attachment, and put it to the author in the same breath as
   the placement — "after becker.md, so 'Connections essays' — yes?" —
   naming the other guides on record if the answer is not obvious. Never
   ask "which style guide?" with no candidates, and never guess silently:
   the attachment is drafting law and naming it is the author's explicit
   act. (The refusal names the candidates too, but the author should not
   have to see a refusal to learn them.) **The brief is
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
   evidence rather than a machine reading.
   **Present it as a numbered prose READING, never as JSON and never as a
   flat wall of items.** The author never sees the payload. Read the
   points back in the original's own order, numbered, each in one
   sentence with its example or reference attached to it rather than
   listed separately. Then, after the reading, put the two or three you
   are least confident about and every claimed inconsistency to the
   author explicitly by number — those are where their ruling changes the
   outcome ("does discernment.md really own p6?"). Take corrections by
   number, in words; you re-compose the JSON. A digest of ~18 items shown
   as data gets rubber-stamped, and a rubber-stamped digest is exactly
   the machine reading the review exists to prevent. Then
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
1c. **Intents on a writeup — relay, settle, never build.** `write start`
   prints the PROPOSED set grouped by tier (file / chapter / manuscript)
   with the primary marked. Relay it in the author's terms, one sentence,
   naming the goals rather than ids: *"Three of your goals cover this
   essay — the Becker rewrite, the Part II tightening, and the standing
   rule about thinkers' names. The Becker one is the primary, so the work
   gets recorded against it. Anything there that shouldn't be?"* Adjust
   with `write intents --add/--remove/--primary <id>` while the set is
   PROPOSED. **Settle a tie BEFORE the plan**: two intents at the same
   most-specific tier leave the primary unset, `write plan` refuses, and
   the choice is the author's — the primary owns every verdict and every
   transition and cannot be re-pointed after the first accepted beat.
   After the plan the set is FROZEN: `write status` reports anything newly
   in scope and the author either joins it (`--add`, which re-bills the
   cached prefix once) or dismisses it (`--ignore`); `--remove` is gone
   and the honest verb is `--defer <id> --reason "<their words>"`.
   Relay the per-goal dispositions at `write complete` — served, deferred
   with the reason, or UNSERVED — as a sentence, and note that closing the
   goals themselves is separate.

   **The scope triage (one-time sitting).** `list_intents(evidence=True)`,
   or `authorlm intent scope --triage`, returns every active unscoped
   intent with the files its episodes ACTUALLY touched, the writeups bound
   to it, and a suggested tier with the reason. Present them ONE AT A TIME,
   oldest first, in the author's terms — no ids in the sentence, no flag
   names, never a menu. Run one `scope_intent` per ruling and report a
   running count. An intent the author cannot place is left alone and comes
   back next sitting; never guess. Close with how many are now file-scoped,
   part-scoped, and deliberately book-wide — the last number is the one
   that matters, because those ride along with every future writeup.

2. **Plan.** Expand the outline into beat specs conversationally
   (`{"role", "concepts", "budget", "notes"}` each); after the author
   RATIFIES, persist: `authorlm write plan` with the JSON array on stdin.
   **PRESENT the plan as a numbered READING, and say plainly what you are
   asking for.** The author's words on the old presentation: *"This is
   interesting but not clear what needs to be done."* Ratification is the
   fabrication guard — the most important gate in this loop — and an
   unclear ask gets a shrug instead of a ruling, which is the same as no
   gate at all. Never show the JSON, and never present the plan as a
   description you are merely narrating. One numbered entry per beat, four
   things each, in plain words:
   1. what the beat is FOR (its role, said as a job: "opens the essay",
      "answers the objection", "closes on the ground it started from");
   2. what it will CLAIM or COVER, in one sentence of ordinary English —
      the claim itself, not "develops the argument";
   3. which of the author's own points, examples and concepts it carries
      (name them the way the author names them; in a rewrite, say which
      point from the current essay each one is);
   4. roughly how long it runs.

   Then ask, in these terms:

   > Approve as-is, or tell me in any words: reorder, strike a beat,
   > change a budget, add a beat, or redirect one — I'll revise and
   > re-present until you say it's right. Nothing is drafted until you
   > approve.

   Mean it: re-present the whole revised reading after every change rather
   than patching one line, and do not run `write plan` until the author has
   actually approved.
   Amend later with `--replace` (written beats are kept; replacement beats
   get fresh `n`s automatically).
   **Before `write plan --replace`, SETTLE any pending proposal** —
   normally `write reject --reason "<why the plan is wrong>"`, because
   that reason is precisely the evidence the replan exists to record.
   Replacement beats get fresh `n`s, so a draft left pending on a dropped
   beat would be stranded as `proposed` forever, invisible to
   `write status` and permanently skewing the tallies. The verb now
   refuses the replace and names both exits (reject with a reason, or
   accept the draft first) — treat the refusal as a reminder, not a
   surprise, and never reach for `write accept` merely to clear it.
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
3. **Draft — NEVER in this conversation (ratified 2026-09-06).** The
   drafter and the critic are SUBAGENTS with empty contexts; you
   orchestrate and never draft. The review of 69 recorded rejections
   found the two faults this fixes: drafting inside the orchestrating
   window let the drafter skip the payload and draft from memory (Sep 5:
   "are you even looking at the prior beats?"), and nothing checked a
   beat before the author did. Every step below is CLI or a subagent;
   `write propose` REFUSES a beat that did not travel this road.
   Use `$S` for the session scratchpad and `N` for the beat number.

   a. **Payload to a file.** `authorlm write draft --dry-run --out
      $S/beat-N.payload -m <ms>`. No model call; it prints only sizes and
      hashes and RECORDS that the payload for this beat was assembled
      under the current state. Do not read the payload into this
      conversation and do not gather its parts by hand.
   b. **Drafter subagent.** Spawn one (Agent tool, default model, fresh
      context) with exactly this prompt, paths filled in:

      > You are the beat drafter for a philosophy manuscript. Read
      > `<$S/beat-N.payload>` in full. It holds the prompt you work under
      > and every block you may draw on, and it is your entire world: use
      > nothing you know from elsewhere, read no other file, run no other
      > tool, ask no questions. Reply in exactly the shape the payload's
      > OUTPUT CONTRACT demands (WHY / SELF-CHECK / DRAFT, or BLOCKED /
      > QUESTION). Write your reply to `<$S/beat-N.reply>` and say nothing
      > else.

      A BLOCKED reply is a question for the author; nothing is registered.
   c. **Extract the draft.** `awk 'f{print} /^DRAFT$/{f=1}'
      $S/beat-N.reply > $S/beat-N.draft`.
   d. **Critic payload.** `authorlm write critique --out $S/beat-N.critic
      -m <ms> < $S/beat-N.draft`. Deterministic, no model call: the laws
      and learnings as a numbered checklist, the accepted text, protected
      terms, the concept notes, the eight passages elsewhere in the book
      the draft most resembles, the LINT report, and the draft. It prints
      the lint summary: a lint ERROR (a sentence opening with "What", a
      term inside its own definition, three em-dashes in a sentence, eight
      words verbatim from the book) means REDRAFT NOW — go to (f) with the
      lint lines as the findings; do not spend a critic on it.
   e. **Critic subagent.** Spawn one (Agent tool, the DEFAULT model,
      fresh context) with exactly this prompt. Not Sonnet: on the first
      trial (good-choice.md, 2026-09-06) Sonnet returned three casing
      false positives in four runs, each costing a two-minute loop, and
      the default model's judgment on what counts as a law is the whole
      point of the check.

      > You are the beat critic. Read `<$S/beat-N.critic>` in full; it holds
      > the prompt you work under and everything you may use. Use nothing
      > else, read no other file, run no other tool. Reply in exactly the
      > shape its OUTPUT CONTRACT demands (VERDICT, then FINDINGS when
      > FAIL). Write your reply to `<$S/beat-N.report>` and say nothing
      > else.

   f. **Redraft on FAIL, at most twice.** Spawn the drafter again with the
      prompt from (b) plus: "Then read `<$S/beat-N.report>`, the critic's
      findings on the previous draft at `<$S/beat-N.draft>`. Fix every
      finding. The rest of the payload still governs." Then (c), (d), (e)
      again. After two FAILs, stop: bring the author the draft AND the
      critic's open findings in one line each, and register it with
      `--no-critic "critic still fails: <the findings, briefly>"` so the
      row records it.
   g. **Register.** `authorlm write propose --why "<concepts by id,
      precedent>" --critique $S/beat-N.report -m <ms> < $S/beat-N.draft`.
      The verb checks three things and refuses otherwise: the payload was
      assembled since the last verdict/replan/learning, the lint has no
      ERROR, and the critic saw THIS draft and said PASS. A refusal names
      the missing step; do it, never work around it.
   h. **Show the author ONLY the changed paragraphs.** Run `authorlm
      write show --changed -m <ms>` and paste its output as the beat:
      every span that differs from the pinned original is in bold, a
      paragraph with no counterpart is bold whole (new material), the
      unchanged paragraphs are left out with a one-line count, and the
      essay's own bold is hidden in this view (author rulings 2026-09-06:
      "bold the changed sections in each beat"; "you don't have to print
      all the unchanged paragraphs, I only need to see the changed
      paragraphs"). **A beat with no change is said so, plainly, in
      orange (or bold): "No change against the original in this beat."**
      Never skip the showing: every beat is shown, even a verbatim one.
      Say what the marks mean in one line the first time. Add the
      WHY in one sentence and, in one more line, what the critic caught
      and the redraft fixed (or that it passed clean). Never the payload,
      the report, a hash or a path. For a new essay there is no original
      and `write show` says so; present the plain draft.
      **Color when the surface allows it (author ruling 2026-09-06: "more
      than bold, like an orange color for dark theme").** When an inline
      widget tool is available (`show_widget`), render `authorlm write
      show --changed --html` inside it: a `<div>` with the essay's serif face, the
      `.chg` spans in orange (`#e8963c` on dark, `#b85c00` on light, via
      `prefers-color-scheme`), a one-line legend, no other styling. Paste
      the bold markdown as well, so the beat is also in the transcript.

   **Dictation.** When the author supplies the text themselves ("write
   exactly this", a reworded beat), it was conditioned on nothing:
   register it with `write propose --no-critic "author dictated"` (the
   reason is recorded on the row). The lint still runs; a lint ERROR on
   the author's own words is shown to them, and `--lint-override "<their
   why>"` registers it — never edit their words to satisfy the lint.

   **The lint is law made mechanical, not a judgment.** Its ERRORs encode
   ratified laws (no "What" openers, no term in its own definition, no
   stacked em-dashes, nothing verbatim from the book). Its warnings
   ("not X but Y", a definitional sentence about a concept another essay
   introduced, a sentence over 40 words, a reading grade above 13) are
   for the critic to weigh. An override is evidence and needs a reason.

   **The billed verb is the exception, not the default.** `authorlm write draft`
   without `--dry-run` sends the payload to the pinned `[writing]` model, self-checks
   in the same call, and registers the result through the ordinary propose path
   (bypassing the chat-road gates: it assembled its own payload). Reach
   for it ONLY when the author explicitly asks for the pinned model, and **say that it
   bills the Anthropic API per beat before you run it**. As shipped it refuses — there
   is no `[writing]` section — and the refusal names the exact TOML to paste and the
   environment variable it needs. Relay that refusal as the configuration decision it
   is; do not paste the TOML in yourself, and never edit `config.toml` to re-enable a
   billed path without the author saying so in as many words. If the author does
   restore it: one model drafts one writeup (design §8 — the prompt cache is
   model-scoped and the voice should not have a seam), and `write draft` warns if
   `[writing] model` changed mid-writeup; relay that warning rather than letting it
   scroll past.
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
   **You DERIVE the `kept` half yourself and record it without asking.**
   The ratified plan names the point ids per beat, `--why` repeats them by
   id, and accept records which beat landed — so every `kept` (with its
   `beat`) is already determined, and pulling the author into a JSON
   accounting step for it is the highest-frequency non-judgment
   interruption in the whole workflow. **Bring the author ONLY the
   removals**, one at a time, in words: a point that is in the digest and
   in no accepted beat is a removal awaiting their ruling, and the reason
   is theirs and required. State the derived `kept` set in one line
   BEFORE you run `write digest --dispositions` ("p1, p3, p7 kept across
   beats 2-4 — recording that"), in the same turn and without asking a
   question: shown before recorded, so a wrong derivation is contradicted
   while it is still a sentence rather than after it is evidence. Do not
   ask them to compose it, and do not make them answer to proceed.
   The **final beat(s) are the `## **What Was Removed and Why**` section**,
   drafted from the RECORDED reasons — do not re-invent them at drafting
   time; the judgments were made when they were recorded, and you write
   only the connective prose. It goes last (order any footnotes beat before
   it in the plan — the loop appends, so the last beat lands last), and it
   is proposed and accepted like any other beat. **It does not stay in the
   manuscript** (author ruling 2026-08-30, verbatim: "The 'What was removed
   and why' should not be present in local or google doc"): after
   `write complete`, strip the section from the file before any doc push —
   the recorded dispositions in the writeup are the durable record, and the
   accepted beat is its author-review; the prose itself is workflow
   accounting, not book content.
5. **Learnings.** When a pattern recurs across verdicts (not on every
   verdict), distill one line and send it on STDIN — `write learn` takes no
   positional lesson (a positional after `-m` trips argparse's greedy
   `nargs='*'`), so `authorlm write learn "<lesson>"` silently ignores the
   argument and then refuses for want of stdin:

   ```
   authorlm write learn <<'EOF'
   <the one-line lesson>
   EOF
   ```

   Honor recorded learnings in subsequent drafts (`write status` shows them).
6. **Complete / interrupt.** When the author accepts the last beat, or
   says the essay is done, **that IS the consent — proceed and report**.
   Do not ask a confirming question whose answer they have already given
   ("shall I complete the writeup?"): run `authorlm write complete`, then
   `authorlm summarize rebuild` (mention the spend — roughly one
   cheap-tier call per stale unit), then `complete_intent`, and report the
   accounting tally and the episode analysis in a sentence or two.
   `close_session` is OFFERED, not required.
   `authorlm write complete` closes the writeup
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

**Parallel-edit mode (author ruling 2026-09-06, on good-choice.md).** The
author's words: *"I'm making edits in the doc in parallel. In this mode I
don't want the filter to write locally and push to the doc at the end. I
want the filter to keep sharing content here, and pull from the doc at
the end to make sure all the sections were updated. They may not be
updated exactly."* The author says so once; from then on, for that
writeup:
- The DOC is the author's working copy. They carry each accepted beat
  into the Doc by hand, reworded as they like, while the loop runs. The
  local file still receives the accepted beats (that is the record the
  verdicts hang off) but it is NOT the essay any more.
- **Never `doc push` that essay**, during the writeup or after it: a push
  would overwrite what they typed. `write accept` never pushes, so the
  only way to break this is to reach for `doc push` yourself. Don't.
- Beats keep arriving HERE (step 3h), one at a time, verdicts as usual.
- **At the end, the Doc wins:** `authorlm doc pull <essay> --force -m
  <ms>` (the local accepted text stays in version history), then
  `authorlm write landed -m <ms>` — a deterministic check that looks for
  every accepted beat in the pulled text paragraph by paragraph and
  reports each as landed exactly, reworded, or MISSING, plus the
  paragraphs the author added that match no beat. Read the MISSING ones
  to the author by their opening words; reworded is not a fault, it is
  their words winning. Only then `write complete`.
- The same rule governs a FILTER run when the author says they are
  editing in parallel: no `filter push`; read the proposals here, take
  chat verdicts (`list_filter_edits` / `triage_filter_edits`), and end
  with `doc pull --force` rather than `filter resolve` writing locally.

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
- `audience` — consult for any question about PITCH: whether a passage
  assumes too much or explains too much, whether a term needs a bridge,
  whether a gloss is condescension. It records what the reader already
  has and what they do not, and its standing test is the one to apply.
  **Precedence, ratified 2026-09-02: where `audience` and `market`
  disagree, `audience` governs DRAFTING and `market` governs
  POSITIONING.** They answer different questions — who the prose may
  assume it is talking to, versus who might buy the book — and the
  reader-load filter and the pitch lens name `audience` in their front
  matter so the payload carries it.

**Registering a new aspect** (do all three, in order, when the author
names one): (1) agree on the key and the standing rule in conversation;
(2) store the content — `authorlm profile set <key>`; (3) add the key
and its standing rule to the registry above, in this file. A profile
whose registry entry is missing gets consulted only when the author
explicitly asks for it.

## Triage at scale (interactive app)
Conversational triage captures reasoning — reserve it for items the author
would hesitate on; their explanations are the evidence that seeds policies.
When a bulk pile has built up, open the local app with `authorlm
triage-app -m <manuscript>` (run it in the background from Bash; it serves
the page on a loopback port and opens the browser — there is no MCP tool
for it since 2026-09-04); it keeps deterministic graph data, LLM assessments, and
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
0. **Ingest to Markdown first** — the intake pipeline reads Markdown.
   A critique arriving as a PDF is converted with
   `python3 authorlm/tools/pdf2md.py <report.pdf>` (repo tool: direct
   text-layer extraction with per-page OCR fallback, header/page-number
   stripping, de-hyphenation, `<!-- p.N -->` page markers) — not with
   ad-hoc extraction scripts. A `.docx` converts with
   `pandoc <report.docx> -t markdown` (or `textutil` on macOS). Keep the
   converted `.md` beside the manifest in `<manuscript>/_critiques/` as
   the report's durable copy.
1. **Import** — parse the report conversationally (`critique.parse_units`
   handles markdown), map units to essay files via toc, classify items
   (revision tasks → intents; standing rules and preservation strands →
   style elements), show the author the per-unit accounting, write the
   manifest to `<manuscript>/_critiques/`, then `import_critique`. Items
   land as PROPOSED with critic provenance (`sources` row) — never active.
   **Dedupe is two-layered, and both layers are mandatory (ratified
   2026-08-31: additional critiques must not duplicate critiques).**
   - The VERB is deterministic: `import_manifest` skips (a) same-source
     (unit, ordinal) re-imports and (b) any item whose normalized text
     matches an existing critique item from ANY critic source in ANY
     status — a later report can never re-litigate a ruled item or
     double a pending one. Skips come back under `duplicates` with the
     existing id/status/source: read them to the author in prose.
   - The SESSION does the semantic layer the verb cannot: before writing
     the manifest, check each candidate item against the existing
     critique items (`list_critique_items` for what is still pending,
     `list_critique_decisions` for the resolved verdicts and the author's
     recorded reasons — never the DB) and the ratified law. A PARAPHRASE of an item the author
     already accepted, already rejected (their reason stands), or
     already ratified as a style element is not imported as new — it is
     listed in the per-unit accounting as a skip, with which existing
     item covers it and, for rejections, the author's recorded reason.
     Sharpened versions of a still-pending item may be imported; say
     they supersede the older wording so triage rules once, not twice.
   A report that supersedes an earlier one only re-rules the essays it
   actually re-reviewed: offer the author a bulk rejection ("superseded
   by the <date> report") for the older PENDING items on those essays
   only — never touch decided items, and never assume supersession for
   essays the new report did not receive.
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
`critique status` shows the pass table. "What did I already decide, and
why?" is answered without SQL by `critique list --decided [--scope FILE]
[--verdict accept|reject|revise] [--query TEXT]` (chat:
`list_critique_decisions`, same filters — narrow rather than raising
`limit`, a manuscript can carry hundreds of settled rejections), and for
one item by `critique show <id|--query>`; `critique reason` amends a
recorded reason, `critique reopen` sends an item back to proposed.
Related repairs: `concept revive <name>` (inverse of a mistaken retire,
incl. collateral edges — `curate_concepts` op "revive"); `style move <id>
--guide NAME` / `move_style_element` when a rule sits at the wrong level.

## The interlocutor (a tradition reads the whole book)
Design reference: `docs/interlocutor-design.md`. An **interlocutor** is a
third-person critical reading of the WHOLE book from inside one tradition
— Epictetus, Basilides, Plato, Shankara. It is not a lens (a lens reads one
essay and reports findings) and it rides the critique road: its report
lands in `_critiques/` and every improvement it asks for becomes a PROPOSED
intent with critic provenance, triaged like any critic's (`critique
triage --scope <essay>`, `list_critique_items`). Nothing is law until the
author rules.

The artifact is `_interlocutors/<name>.md`: TOML front matter (`terms`,
two kinds — the tradition's own vocabulary and the BOOK'S words the author
declares adjacent, each with a reason; `engaged`, essays that already
engage this critic; `position`, essays carried whole as the author's
authoritative position) and then the prose: the corpus declaration, where
to start, the refusals. The author reads and ratifies it before it runs,
like every lens. **The concept comparison is the author's input, never a
subagent's guess**: a critic with an essay that engages it uses that essay;
a critic without one waits for the author's rough comparison, and
`interlocutor draft` refuses without `--engaged`.

**"Run Epictetus on the book" is ONE instruction.** Carry it to the end in
one turn, the way a filter is applied. With `$S` the scratchpad:

1. `authorlm interlocutor run epictetus --out $S/epictetus.payload -m <ms>`
   (`--file <chapter|part>` narrows a follow-up run after one essay was
   revised; `--engaged` / `--position` ADD essays to the artifact's own
   lists). Deterministic: the term scan, the toc with summaries, the
   position and engaged essays whole, the concept notes for the book terms
   that hit, the mentions, the manuscript root, the output contract. No
   model call. Do not read the payload into this conversation.
   **The mentions are a SAMPLE** (author ruling 2026-09-06): at most 12
   terms and 6 locations per term, drawn fresh each run so a re-run
   surfaces new paragraphs and new insights; the coverage table tells the
   critic where the rest live. The seed is printed and recorded in the
   report — mention it in one clause when you read the report back, and
   pass `--seed N` only when the author asks to reproduce a run.
   `--max-terms 0 --max-per-term 0` shows everything.
2. Spawn ONE subagent (Agent tool, the DEFAULT model, fresh context):

   > You are the interlocutor for a philosophy manuscript. Read
   > `<$S/epictetus.payload>` in full. It holds the prompt you work under,
   > the tradition you read from, and everything you may use; you may
   > also open whole files under its MANUSCRIPT ROOT, and every one you
   > open goes in your Baseline. Use no web and no other tool. Write the
   > two files its WRITE TO block names, in exactly the shape its OUTPUT
   > CONTRACT demands, and say nothing else.

3. `authorlm interlocutor import epictetus $S/epictetus.payload -m <ms>` —
   automatically, in the same turn. It verifies the Baseline against disk
   (a false baseline REFUSES the whole import), drops any finding whose
   quote is not verbatim in the named file or whose heading has no locus
   (counted, listed in the report header), joins the manifest to the
   surviving findings (an `addressed` objection yields no item), prepends
   the deterministic mentions block, lands report and manifest in
   `_critiques/<date>-interlocutor-<name>.{md,json}`, and imports the
   manifest as proposed intents.
4. **Read the report back as prose, BY ESSAY**, then give the two paths.
   For each essay: which objections are open against it (`partial` or
   `unaddressed`), the literature name and locus of each, and the one
   sentence it asks for; then the misattributions and their repairs; then
   the `addressed` objections in one line each so the author knows what is
   covered — including the by-inference ones, whose inference is in the
   report for checking. Any `[check]` locus is a to-do for the author; say
   so. Never the payload, the manifest, or a hash.

Rulings the contract encodes, so you can answer for them: "addressed"
includes ADDRESSED BY INFERENCE — the book need not name the objection if
its theory as stated entails the answer (the author: "I don't want to keep
saying 'Plato's objection A: my response to A' all through these
non-fiction essays"); an improvement supplies a premise or restates a
claim, never asks for that dialogue; the Sermons are read as claims the
book makes and the repair always points at the essay that defends the
claim; misattributions (mischaracterizations included) are reported apart
from objections; a contested reading is never an error; there is NO memory
between runs — a re-run is a fresh report and the author reconciles by eye.

**From finding to paragraph: the fixes ride the FILTER road, never a chat
draft (author ruling 2026-09-06, after a chat draft broke five style
laws).** When the author asks for the fixes, write one `global` filter per
run, `_filters/<critic>-objections.md`, whose prompt carries the surviving
findings — passage, what the tradition holds, missing premise, repair —
grouped by file, with a prelude that maps the findings naming THE ESSAY to
unit numbers and marks QUESTION any repair needing a fact the report lacks
(that unit is `keep`, question in `why`). Read it to the author as prose,
`filter add` on their yes, then run it as beats, one per essay the report
touched, exactly as the filter pass below says: prelude → run to a payload →
clean-context drafter subagent → `filter apply` → the brief prose summary →
the author settles in the tab. The filter's list is closed, and the
objection-and-answer format is refused in its prose. Never paste proposed
replacement prose into the conversation as "surgical fixes": the payload
carries STYLE LAW and PROTECTED TERMS and the record step lints; the
conversation carries none of that.

**A new critic** (after Epictetus): `authorlm interlocutor draft <name>
--engaged <essay> --out $S/<name>.draft -m <ms>`; a subagent with the
prompt "Read `<payload>` in full and write the artifact it asks for to the
path in WRITE TO; use nothing else and say nothing else"; then read the
artifact TO THE AUTHOR AS PROSE — the corpus it claims, the book terms it
maps and why, the refusals — and only on their yes `authorlm interlocutor
add <name> < $S/<name>.draft.artifact.md`. An artifact the author has not
read is one they cannot rule on.
**Propose the position; never ask an open question about it** (author
ruling 2026-09-06). The author's position is a property of the book, not
of one critic: `interlocutor list` shows what every installed artifact
carries as `position`, the draft payload's POSITION ON RECORD block seeds
the new artifact with it, and you put it to the author in the same breath
as the rest of the reading — "the other critics carry the Recapitulation
and the Metaphysic as your position; same here?" — naming any file one
critic carries and the others do not. When the author names a different
position for one critic, ask once whether the change is for that critic
or for all of them, and edit the other artifacts if it is for all. Ratified order of first use: Epictetus,
then Becker, then Nietzsche, then Basilides; Plato waits for the author's
rough comparison. The checked-in canonical copies live in
`docs/interlocutors.md`, the lenses' pattern.

## The filter pass (one concern, one essay, unit by unit)
Design reference: `docs/filter-pass-design.md`. A **lens** reads one essay
whole and reports findings. A **filter** reads one essay UNIT BY UNIT and
proposes an edit to each unit — one ratified prompt applied to every
paragraph, each conditioned on what came before, the way the write verb's
beat loop conditions each beat. A filter is one concern on ONE essay: a
motif used one way in `becker.md` and the opposite way in `kindness.md`
is a real finding and a filter cannot see it. That question is a lens.

**The lens door (built 2026-08-31, design §7.2).** A lens finding may
carry an optional `replacement` — the quoted text's substitute within its
unit. When it anchors cleanly (quote verbatim in exactly one unit, once),
the harness builds `new` from the unit itself and stages a doc thread with
`origin_type='lens'`; ambiguity is refused per finding, never guessed, and
the finding itself is always kept. `lens push <essay>` then writes the
staged lens edits into the essay's tab as `<<old>>{{new}}` forms and
`lens resolve <essay>` reads the tab back — the filter doc road's contract
verbatim: the tab is the review, untouched = accept, old-restored =
decline, reworded (or hand-resolved) = the author's words win, one
producer's forms per tab, evidence as `lens_edit` under no episode.
Ruling on a FINDING (`lens review` / conversation) and settling its EDIT
are independent verdicts — accepting the finding does not apply the edit.
When registering findings on the author's behalf, include `replacement`
only where the fix is confidently mechanical; judgment-shaped findings
stay findings.

The artifact is `<manuscript>/_filters/<name>.md` — TOML front matter
declaring `class` (`sequential` or `global`), then the prompt. Adding a
filter in an existing class is a file and no code; a third class would be
code. `filter add <name>` with the artifact on stdin; `filter list`,
`filter show <name>`. In a shell-less context, `add_filter` /
`list_filters` / `show_filter` do the same three things over MCP, under
the same read-then-ratify rule; the loop itself (`run`/`prelude`/
`record`/`settle`/…) stays a CLI-only surface.

**Three filters are written and waiting to be ratified**, in full, in
`docs/filter-pass-design.md`'s appendix:

- **`duplicate-words`** (sequential) — a word or phrase used again too
  soon, with a carried ledger. Knows that a refrain is not a tic, that a
  term of art repeats as often as the argument needs, and that a motif
  word recurring across the book is the book working.
- **`metaphor-consistency`** (global) — where a figure is used against
  itself: a motif carrying two incompatible senses, a mixed figure, a
  metaphor asked to do an argument's work. Its prelude reads the essay
  whole and returns a motif registry.
- **`audio-friendly`** (sequential) — where the prose works on the page
  and fails in the ear, with a carried voice note. It also declares a
  **pronunciation prelude** (below).

**They are not installed.** `filter list` on the author's manuscript is
empty until they say yes to one. When the author reaches for a filter,
or when one of these three would obviously serve what they are doing,
read them the relevant one AS PROSE — what it flags, and what it
deliberately refuses to flag — and offer to install it. On a yes, pipe
that appendix block verbatim into `filter add <name>`. Do not paraphrase
the artifact when installing it: what goes in the file is what they
ratified. A filter they have not read is a filter they cannot rule on.

**THE FLAG POLARITY IS INVERTED against `write draft`, and the author
must never be confused about which way round it is.** `authorlm filter
run <name> <essay>` makes **no model call at all** — it prints the
payload and YOU draft the reply in this conversation. `write draft` is
the other way round: it calls unless you pass `--dry-run`. Say this in
plain words the first time in a session that the author reaches for
either verb, and never describe `filter run` as "a dry run" — there is
nothing dry about it, it is the flow. `--native` is the billed path and
refuses unless `[filtering]` is in config.toml, which it deliberately is
not.

**The cache breakpoints are a NATIVE-path mechanism; do not cite them to
explain chat-mode cost.** The payload's four blocks (S law / A frame /
B filtered prefix / C this window) have cache breakpoints after S and
after A, and on `--native` that stable prefix is written once and
re-read at cache-read rate. In chat mode there is NO model call, so no
breakpoint applies: the only cost is how many times the payload is
printed into the conversation, which is once per `filter run`
invocation — once per WINDOW.

**Do NOT pass `--window`, and say why if the author asks.** The default
is the whole essay in one reply (`span = window if window else
len(units)`), which is both the cheapest and the most autoregressive:
unit N conditions on the run's own output for N−1 inside a single
generation. `--window N` splits the run into ⌈units/N⌉ invocations and
**re-prints block A every time** — with `summaries = true` that is tens
of thousands of tokens again per window. The ONLY reason to reach for it
is OUTPUT length: forty-odd rewritten paragraphs plus `why` and `ref` is
a long generation and a truncated reply is refused whole. If that
happens, `--window 15` and turn `summaries` off first. (`--from N` is a
different flag and IS routine — it re-opens the window at unit N after a
settle falsified the conditioning.)

**`summaries = true` in a filter's front matter** adds the compressed
summaries of the essays before and after this one to block A — un-gated,
so a stale summary is shown and marked `!!` rather than refusing the
run. It costs real tokens (on a mid-sized essay, block A grows roughly
5×), so declare it only on a filter whose findings genuinely turn on
what another essay already covers. Design reference §9.1a carries the
measured numbers.

**The payload already carries cross-essay DEFINITIONS without it.**
`scoped_concepts(file=…)` selects a concept if it was introduced in this
essay OR its name or alias appears in the essay's text — so a term
defined in `metaphysic.md` and used in `hierarchy.md` arrives with its
notes, its aliases, and the graph edges among the selected nodes. The
limit worth knowing: an essay only receives the definition of a concept
whose name it ALREADY WRITES. A concept the essay needs but has not yet
named is absent, and the cheapest fix is to write the word in once by
hand — every later payload then picks it up. Never reach for a lens
merely to reference another essay; reach for one when the JUDGMENT is
cross-essay (a motif used two ways, material that belongs in a different
essay), because a filter can only replace a unit in place and can never
move or cut one.

**Unless the author has declared parallel-edit mode** (see the beat loop:
the Doc is their working copy, nothing pushes, the Doc is pulled with
`--force` at the end and `write landed` checks what landed), the
following holds.

**"APPLY FILTER X TO ESSAY Y" IS ONE INSTRUCTION, NOT THREE.** The author's
words on the old behaviour: *"I don't know why we have three different verbs
required in three different steps to do that one thing."* They are right.
`run`, `record` and `push` are the state machine's steps, not theirs — they
never type them, YOU do. When the author asks for a filter to be applied,
carry the whole thing to the end in ONE turn: assemble the payload, draft the
reply, `filter apply` (which records AND pushes), then read the proposals
back to them as prose. Do not stop in the middle to ask which road, whether
to push, or whether they would like to triage first. The Doc tab IS the
review, and the point of the pass is that the proposals arrive where they
will be ruled on.

**"FILTER THE ESSAY" IS A SEQUENCE OF BEATS, ONE FILTER PER KIND OF FIX
(author ruling 2026-09-06, verbatim: "A filter will typically do beats, one
for the various fixes that are required").** A filter pass on an essay is
never one filter. When the author says "filter the essay", or hands over a
list of what is wrong with it, first sort the problems by which installed
filter OWNS each concern (`filter list`; each artifact's "does not count"
section names the owner of what it refuses), and say the sort back in one
breath: these items go to modal-register, these to claim-status, these to
nothing installed. Then run the owning filters as BEATS, one after another,
each beat carried to the end in one turn exactly as the paragraph above
says: run → subagent draft → `filter apply` → the brief prose summary →
the author settles in the tab → `filter resolve` + `doc push` → the next
beat starts. Only one producer's forms may sit in a tab at a time, so a
beat's push waits for the previous beat's resolve; name the next beat and
wait for the author's word that the tab is settled. Never record several
filters against the same text to save a round trip — an earlier beat's
accepted edits leave the later replies' whole-unit replacements stale, and
a stale `new` would overwrite what the author just accepted. Order the
beats by the fixes the author actually asked for, then the standing sweeps
(duplicate-words, audio-friendly, image-then-paraphrase, reader-load,
modal-register, claim-status) as the essay's state warrants. A problem no
installed filter owns is reported as such in the sort, WITH a filter
proposed for it (read to the author as prose and ratified before `filter
add`) — never handed back as "outside the filter's concern" with nothing
offered.

**A chat verdict on a pushed proposal is NOT reachable by number.** Once
`filter apply` has put the forms in the tab, `filter edits` lists nothing
and `filter triage --reject N` answers "no edit N (there are 0)"; a
`filter resolve` run right after that reads the untouched form as an
ACCEPTANCE and finalizes it. (This happened on 2026-09-06: the author had
refused a proposal in chat, the triage silently missed, the resolve
accepted it, and the push carried it to the Doc; recovery was `doc pull`,
`filter rollback`, `doc push`, and the accepted verdict stayed on the
record.) So when the author refuses a proposal in chat AFTER the push,
do not resolve: either empty that form's green half in the tab yourself
before the resolve, or record the refusal BEFORE the apply by running
`filter record` then `filter triage --reject` and only then `filter push`.
A triage result that reads "there are 0" is a stop, never a step.

**THE STANDING POST-MERGE STEP: after the author merges a revision, read
the essay WHOLE before saying "verified" (author ruling 2026-09-06).**
"Pull and verify" means two verifications, and the reply names which
were done. The first is mechanical: footnote anchors and definitions
paired, no bare numeric anchors, internal references plain roman, no
reserved characters, no "What" openers, typos. The second is
argumentative, and it is the one that gets skipped: re-read the merged
essay from the top and, for every distinction the essay introduces (a
definition, a scheme of tests, a scoping such as "within one lifetime"),
list where it is stated, where it is grounded, and every example,
enumeration, and condition that should carry it — then report the places
where it was introduced and not deployed. Check every "the shape of the
argument" pointer to another essay against that essay's summary
(`summarize` keeps them on file): the pointer must compress the target's
mechanism, not only its definitions. Treat ratified concept notes and
style laws as constraints on MEANING in this pass: a sentence that quotes
a note verbatim and undermines the essay's own argument is a finding, not
compliance. The lenses for this pass are the eight installed on 2026-09-06
(`lens list`), rewritten book-agnostic from the earlier five after an
independent review; each opens with verbatim Flag/Keep examples from the
manuscript and a rules section that refers only to the runner's INPUTS
(AUDIENCE PROFILE, GLOSSARY = the concept notes, ORGANIZING SCHEME,
PROTECTED REGISTERS = the Sermons, SENTENCE-LEVEL PASSES = the filters).
**Lenses run independently and never on summaries** (author ruling
2026-09-06, built the same day: docs/lens-architecture-design.md). A lens
declares in TOML front matter its `class` (`chapter` — the essay against
itself; `cross-chapter` — the essay against the TEXT of other chapters),
its `inputs` (glossary = the concept notes with where each term was
introduced; audience = `_profiles/audience.md`; scheme =
`_profiles/scheme.md`; registers = toc entries with `register =
"protected"`; passes = the filter roster; reading-order) and, for a
cross-chapter lens, its `targets`: `pointers` (chapters the essay names,
resolved by title and `_profiles/chapter-aliases.toml`, their full text
carried), `neighbours` (nearest full chapters either side), `earlier`
(TERMS INTRODUCED LATER/EARLIER, from the graph), `book` (verbatim
recurrences and concept realizations, zero tokens). No key renders a
summary and none ever will; a finding about another chapter names it
from block T and quotes it, and the quote is checked against that file.
**THE FLAG POLARITY MATCHES `filter run`:** `lens run <name> <essay>`
makes NO model call — it prints four hashed blocks (S lens, A inputs, T
targets, E essay) for a subagent to answer; `lens run --native` sends the
same blocks to the `[llm]` model. Say which way round it is the first time
a session reaches for it. The lens road, one turn, same shape as the
filter beat: `lens run <name> <essay> --out $S/<name>.payload`; spawn an
empty-context drafter ("read the payload in full; it is your entire
world; answer block S's contract; write the JSON to <reply>"); `lens
register <name> <essay> --reply <json>` (it re-assembles the payload and
REFUSES a finding naming a chapter the lens did not declare); then relay
the findings as prose and hand verdicts to `lens review <n>`. `lens sweep
<essay> [--native] [--only a,b] [--skip a,b] [--out DIR]` runs the roster
in the ratified order with block A assembled once and cross-links
findings whose quotes overlap across lenses. **What reaches the Doc, and what does not:** a lens finding that
carries a rewrite (`replacement`) or a footnote gist is staged as a
pending edit and PUSHED to the tab in the same turn by `run --native`,
`register`, and `sweep --native` (`--no-push` for the local road); a
finding with no new text — most judgment findings — never reaches the
Doc, because the pending-change grammar has nothing to show, and the
bridge cannot create an anchored comment. Read those with `lens findings
<essay>` and rule with `lens review <gd-id>`; never tell the author to
look for them in the tab. `lens status <essay>` shows
each batch's per-rule tallies, unverified target quotes, and STALE where
the essay or a target has changed since (byte comparison, not a
summary). A lens refuses to run ON a protected register. `lens run` on
hierarchy.md measures 5K–60K tokens of payload depending on class (a
pointer lens carries six chapters); native is cents, the subagent road a
fifth of a filter beat.
**A lens file's Examples section is living evidence:
when the author rules an example wrong or moves the line, edit the lens
file at once and record the reasoning in authorlm afterward.** A filter cannot see any
of this, because none of it is unit-local. Only after both passes
does the reply say "verified", and it says which pass found what. The
occasion for this rule: on 2026-09-06 a merged hierarchy.md was
"verified" mechanically, and a second reader then found a run of
introduced-but-undeployed distinctions the checklist repairs had left
behind.

**FOOTNOTES ARE PLANTED, NEVER WRITTEN — by every producer (author
ruling 2026-09-06: "a standard thing this filter does, for all
interlocutors").** A filter's replacement, a lens finding's `footnote`
gist, and an interlocutor report's `Footnote:` line all end in the same
place: the author's own request tag `[Footnote: <gist>]` planted right
after the sentence, inside the proposed text. The gist says what the note
should supply (what a sufficient citation looks like; the qualification in
a phrase); the finished note is drafted on the footnote road
(footnote-directive-design §3): collect reports the open tag, the chat
drafts against the file's own footnotes and the anchor sentence, the
author rules, `footnote apply` lands it. Nothing in any producer invents a
citation — the REJECTED law of 2026-09-03 stands. The lens harness composes
the tag from a `footnote` gist when no replacement is given; the filter
drafter writes it into `new` itself; an interlocutor's follow-through
objections filter carries the report's gist into the unit it repairs.

**A RESOLVE IS NOT FINISHED UNTIL THE DOC HAS THE RESULT.** `filter
resolve` deliberately does NOT re-push — a resolve that also pushed could
fail halfway on the network after the evidence was already recorded, so
the verb stops at the evidence. That is a reason to push SECOND, never a
reason not to push. The author's words: *"Why do I need to remember to
tell you to do a doc push every time I tell you to 'resolve'?"* So every
`filter resolve` and `lens resolve` is followed by `doc push <essay>` in
the same turn, without being asked. It also clears the struck-and-green
marks the resolve leaves behind in the tab, which are otherwise still
sitting there the next time the author opens the Doc. If the push fails,
say so and retry the PUSH — the verdicts are already safe on disk.

Four things legitimately interrupt that, and nothing else does:
- a `global` filter, whose prelude must be drafted and frozen first;
- a run needing more than one window — the apply that COMPLETES the run is
  the one that pushes; say how many units are left and carry on;
- the author having asked for the LOCAL road, which is `filter record` then
  `filter resolve --pause`, never `apply`;
- a genuine refusal from a verb, which is reported, never worked around.

`authorlm filter apply [<name>] <essay>` (reply JSON on stdin) is `record`
and `push` in one verb. The DRAFTING step is not in it and cannot be: in chat
mode `filter run` makes no model call, YOU answer the payload in the
conversation, and no CLI process can stand in for that. The numbered loop
below is the machinery `apply` wraps — read it to know what happens, never as
a script to walk the author through.

The loop, per essay:
1. `authorlm filter run <name> <essay>` (shell, no call). Read the
   payload. For a `global` filter, `filter prelude <name> <essay>` comes
   first — draft the registry, pipe `{"registry": "…"}` back into the
   same command; it is then frozen for the run. For a filter whose front
   matter declares `prelude = "pronunciations"` the same verb runs the
   dictionary diff instead (below); it is OPTIONAL, and `filter run`
   prints one line and proceeds if it has not been run.
2. **The reply is drafted by a subagent with an empty context, never in
   this conversation (same rule as the beat loop, ratified 2026-09-06).**
   Send the payload to a file — `authorlm filter run <name> <essay> -m
   <ms> > $S/<name>-<essay>.payload` — and spawn a drafter (Agent tool,
   default model) with: "Read `<payload file>` in full; it is your entire
   world — use nothing you know from elsewhere, read no other file, run
   no other tool. Answer it exactly as its Reply section demands: one
   entry per unit, in order, the state carried forward, each `echo`
   copied. Write the JSON, and nothing else, to `<reply file>`." Then
   pipe that file into `authorlm filter apply` (or `record`). A mismatched
   `echo` discards the whole reply and nothing is staged. `record` now
   also LINTS every replacement for what it introduces (a "What" opener,
   a term inside its own definition, stacked em-dashes, a verbatim run
   from another essay, a re-definition) and warns by unit — read those
   warnings to the author with the protected-term ones; they are usually
   the proposals worth looking at first.
3. **`authorlm filter push <essay>` — ALWAYS, immediately after a
   successful record (author ruling 2026-09-01, verbatim: "always add
   these to the doc using the old/new syntax just like we do for
   lenses ... the actual doc update should flow through the same
   deterministic code path as lensing").** The Doc road is the standing
   protocol for every filter run, not an option to offer: the staged
   proposals go into the essay's tab as struck-through old text with the
   new text in green, through the same forms pipeline `lens push` uses,
   and the author rules on them THERE. Never apply a filter's edits
   locally without being told to in as many words, and never hand-write
   forms into a file or a tab — the verb is the only producer. Then
   **summarize the proposals in chat VERY BRIEFLY**: one line per
   changed paragraph saying which paragraph and what kind of repair, the
   tab URL once, and the one or two you are least sure about. Not the
   old and new text — the tab shows that. Never show JSON, a payload, a
   unit index they did not ask for, an id, or a flag name.
4. Verdicts happen in the tab (untouched = accept, green half emptied
   = decline, reworded = the author's words win) and `filter resolve
   <essay>` reads them back when the author says they are done. Chat
   verdicts (`list_filter_edits` / `triage_filter_edits`, or `filter
   triage <essay> --accept … --reject … --reason "…"` in the shell)
   remain available for a change the author wants to knock out before
   looking, and are never required. **Take a rejection's reason in the author's own words,
   verbatim.** It is the highest-value evidence the run produces: the
   next run of this filter on this essay is shown their reasons before
   it starts, which is most of what stops it proposing the same thing
   twice.
5. `authorlm filter resolve <essay>` applies the accepted edits directly
   (the default: the triage verdict already IS the ruling). `--pause`
   instead writes `<<old>>{{new}}` forms into the local file for the
   author to read and reword in Obsidian; `filter resolve` with no flag
   then finalizes, their words winning. While the file is marked it will
   not push to Docs and every observer still reads the original text;
   `filter unmark <essay>` is the way out. `filter rollback <essay>`
   restores the run's pin (verdicts stay as evidence).

   **5b. The Doc road.** `authorlm filter push <essay>` writes the
   run's staged edits into the essay's tab of the master Doc as
   struck-through old text with the new text in green, and leaves the
   file on disk holding the OLD essay. **The standing expectation is
   that the author then goes to the Doc and settles there** — `filter
   settle <essay>` reads the tab back when they say they are done.

   **Step 4 is OPTIONAL on this road, and usually skipped.** The tab IS
   the review: an untouched form is an acceptance, an emptied green half
   is a decline, a reworded one is a modified acceptance, and the resolve
   records all three exactly as a CLI verdict would. So `filter push`
   takes the UNTRIAGED proposals out too — never make the author rule on
   everything in chat first and then again in the Doc. Only a change
   they have already turned down stays home. If they want to knock a
   few out before looking, that still works and the push says which is
   which; it is a convenience, not a gate.

   Tell them three things, once:
   - **An untouched form is an acceptance.** Leaving a change alone
     means taking it. Nothing needs to be marked "yes".
   - **Edit inside the `{{ }}` braces only. Leave `<< >>` alone.**
     Editing the old half breaks the join, and the resolve records that
     change as a decline — their text still survives, but the verdict on
     the record is wrong.
   - **When two changes replace identical paragraphs**, `filter push`
     says so. They settle by position. Rewording either is safe;
     deleting one outright may attach the decline to the other twin. The
     manuscript text is never affected.

   **Since 2026-09-01 the Doc road is the DEFAULT for filters** (step 3
   above). The local-road paragraphs below describe the fallback the
   author must ask for by name.

   A run takes ONE road and keeps it: a push makes it a doc run, a
   settle with no prior push makes it a local run, and switching is
   resolve-then-rerun rather than a flag. Only ONE producer's forms can
   be in an essay's tab at a time — a second filter's `filter push` on
   the same essay is refused by name, because two producers' forms in
   one tab cannot be told apart at resolve. Local stays the DEFAULT and
   the recommendation, because its whole state is described by the bytes
   on disk and the Doc road's is not — a crash between `filter unmark
   --force`'s withdraw and its rebuild can leave forms in a tab that
   nothing on disk records, and `filter status` says so rather than
   letting its byte scan read as a clean bill of health. The documented
   recovery is `doc push <essay>`, which rebuilds the tab from the
   (pristine) local file and is permitted the moment no forms are out.

   **`filter unmark <essay> --force` rebuilds the WHOLE TAB from the
   local file.** Never describe it as "taking the changes back out": it
   destroys everything the author has typed into that tab since the
   push, the rewordings AND any unrelated edit they made elsewhere in
   the essay. If they may have edited outside the marked passages, say
   so and offer `doc pull <essay>` first — it brings that edit down to
   the file and leaves the tab and the forms alone, after which the
   unmark costs only the green halves.

   `filter push` is CLI-only, like every other loop verb — `gdocs` is
   deliberately not reachable from MCP, so an agent-driven call can
   never pop an OAuth consent window. `list_filter_edits` reports the
   run's `mode` and `forms_out`, so a chat session never offers to apply
   what is already sitting in the author's Doc.

Say the attribution note ONCE, not every run: nothing a filter does is
filed against any of the author's goals. A duplicate-word sweep is not
work toward the Becker rewrite, and recording it as if it were would make
that goal's completion report say something untrue. Explained rejections
still reach belief learning — that runs off the evidence stream and the
author's words, not off an episode.

`filter status` shows the run history per (filter, file) with tallies, so
a filter whose proposals never settle down is visible. A filter firing on
nearly every unit is almost always a PROMPT fault: the remedy is
`filter show <name>` and an edit to the artifact, not another run.

### Protected terms — the payload carries the author's vocabulary

Every filter payload now carries **PROTECTED TERMS**: every name and
every alternate name of every concept, figure, construct and proper name
the book uses, derived from the author's own graph, plus every term the
pronunciation dictionary carries. It is the authority, not a sample.
Never substitute a synonym for one, never re-word one, never change its
capitalization. A single-word name written there with a capital is the
term of art only where the text capitalizes it; a multi-word name, and
any name written there in lower case, is the term in any casing.

`filter record` warns by name when a replacement drops a protected term
the original carried. It **stages it anyway** — the harness supplies the
law and is not the editor. Read that warning to the author in plain
words when it fires; it is usually the proposal they most want to look
at.

### The pronunciation dictionary

`<manuscript>/pronunciations.md` is the author's table of how the book's
hard terms are said aloud. It is a SIDECAR: markdown, mirrored into the
master Doc as an ordinary tab so they can edit it there like an essay,
and excluded from the reading order, from concept scanning, from
summaries and from every export. It never ships inside the book, and no
filter, lens or critique pass runs on it.

**It appears on the first accepted row and never before**, and the ONLY
thing in the system that writes it is the author's own `proposal accept`.

The loop, when a filter declares a pronunciation prelude:

1. `authorlm filter prelude audio-friendly <essay>` (shell, no call).
   The payload carries HARD TERMS FOUND IN THIS ESSAY — computed, not
   judged: every term with a non-English LETTER in it that the
   dictionary does not already have. That is a floor, not a ceiling.
2. Propose how to say each one, as **plain respelling with the stressed
   syllable in capitals** — never IPA; the author reads this table and a
   notation they cannot check is one they cannot rule on. Pipe
   `{"pronunciations": [{"term": …, "say": …, "note": …}]}` back into
   the same command. Every term must be in the essay VERBATIM.
3. Each becomes an ordinary proposal. `list_proposals` /
   `resolve_proposal` carry them in chat; `proposal review` in the
   shell. Read them to the author as prose, one at a time.
4. Accepting writes the row. From the next window on, that term is in
   block A's PRONUNCIATION DICTIONARY and the audio filter never flags
   it again — the dictionary IS the fix.

**Say this out loud the first time: a term you propose is a term the
author will never be asked about again.** One proposal per term, ever, in
any state — a dismissal is final, and the remedy if they change their
mind is to write the row in `pronunciations.md` themselves. So propose
the ones a narrator would actually stumble over, and leave the rest.

## The audiobook (`authorlm audio …` — AuthorLM produces, audiostation generates)

Design: `docs/audiobook-pipeline-design.md`. AuthorLM writes the
manifests in `_audio/` (`audiobook.json`, `chapters/*.json`); the
**audiostation** desktop app (`audiostation/`) generates, stitches,
audits and packages. Nothing in chat generates chapter audio, and the only
ElevenLabs speech AuthorLM ever requests is an audition clip.

**Casting is three layers.** `voice = "key"` on a chapter in `toc.toml`
(the essay default; invisible in the Doc), rows in `_audio/cast.md`
(what each role sounds like), and rare full-line `[Voice: key |
speed=0.92]` tags in the prose for the exceptions. The tag goes to the
Doc like every other tag so the pull survives, and is stripped from
every reader output. The paragraph is the unit of audio: a speaker
change inside a paragraph is resolved by splitting the paragraph, never
by an inline marker. Keep the tag count low — the author edits in the
Doc with a screen reader.

**The audition protocol (voices → shortlist → the book's own words):**

1. **Free first.** `list_voices` (`audio voices --search "deep british
   narrator"`, `--library` for the shared library) downloads preview
   clips into `_audio/auditions/audition.html`. Publish that page as an
   artifact; the author listens and names a shortlist in chat.
2. **Then paid, on the author's words.** `audition_voices` with the
   shortlist and `file` + `paragraphs` (the paragraphs that will
   actually be generated) or `text`. Without `confirm=True` it returns
   the character cost — read it to the author and wait for a yes. Never
   audition on paid clips before a free pass has narrowed the field.
3. **One parameter per round.** Vary stability or speed, not both.
   Industry practice is one setting per voice for the whole book;
   drama comes from the prose.
4. **Settle on the author's word.** Propose the row as prose; `set_cast`
   only after they have heard the clip and said so.
5. **Cast the passages afterwards.** The casting filter (`cast = true`
   in its front matter; `filter run casting <file>`) proposes `[Voice:]`
   tags that name only real rows, through the ordinary filter loop.

**Pronunciations → ElevenLabs.** `push_pronunciations` (`audio
dictionary push`) shows the plan first — rows to add, readings that
changed, remote-only rules left alone — and applies only with
`confirm=True`. The dictionary is named in `audiobook.toml`; the model
there decides alias (multilingual v2 and most others) versus phoneme
(`eleven_flash_v2`, `eleven_v3`) rules. After a push, `export_audio`
writes the new version into `audiobook.json`.

**Settling a pronunciation by ear.** `say_term` (`audio say`) renders the
word's own sentence, in the voice of the section that says it, with each
respelling substituted inline as the alias rule would — no dictionary
push, about 150 characters per clip. `authorlm workbench -m <manuscript>`
(run in the background from Bash, like `triage-app`; it serves the page
on a loopback port and opens the browser) is the same loop as a local
page: every word, every voice that says it, a respelling box, Regenerate
and listen in one of three modes (the respelling as written; **the
voice's default with no rule at all**; via the pushed dictionary), Save
to `pronunciations.md`, **Remove from dictionary** (added 2026-09-04:
when the default sounds better, drops the row and exactly that one
remote rule, then Export), Push, Export. The sentence each voice reads
is editable there, and rendered clips persist across reloads (indexed in
`_audio/auditions/say/workbench-clips.json`). There is no MCP
tool for it since 2026-09-04: the MCP App road never rendered in the
author's client and was removed. You cannot hear the clips: propose
respellings from spelling and etymology (plain syllables, stressed one in
CAPS, never IPA), then act on what the author reports hearing. A row is
written only on the author's word (`settle`, or their Save).

**Readiness.** `audio_readiness` (`audio check`) is the one report from
the sources to audiostation's state: metadata, `about.md`, cover, cast
keys, unresolved tags, dictionary drift, retail sample length, stale
exports, and how much is generated per chapter. audiostation's ACX audit
is the last gate on the audio itself.

`audio init` (CLI only, once) seeds `audiobook.toml` and `cast.md`;
after that AuthorLM reads them and writes only a cast row on request.
Every text rule (gaps, heading voice, footnotes, inline math) is a key in
`audiobook.toml`, never a flag. Narrator, publisher, copyright year and
language are manuscript metadata (`set_manuscript_metadata`), set only
from the author's explicit words.

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

**Print geometry (2026-09-02).** Trim size and bleed are manuscript
properties beside the ISBNs — `authorlm manuscript set --trim-size 6x9
--bleed no` (inches; MCP `set_manuscript_metadata(trim_size=, bleed=)`).
`authorlm export pdf --profile book` builds the KDP interior at that trim:
book class, essays opening recto, mirrored margins with the gutter chosen
from the page count (two passes when the band moves), running heads (book
on the verso, essay on the recto), captions set small-italic under the
plates with no "Figure N" label, no review marks, and a ` - book` suffix on
the filename so it never overwrites the review copy. It REFUSES without a
trim size — that is the one number nothing can default — and WARNS (never
refuses) on KDP's asks: a non-standard trim, a missing paperback ISBN, a
page count under 24 or over 828. Paper means PDF; EPUB is for e-readers
only and no printer takes it.

**Captions.** `[Illustration: … | caption: text]` is the reader-facing
caption; it becomes the image alt text on export, which pandoc turns into
a figure with a caption on EVERY road (EPUB figcaption, PDF figure
caption). A slot without a caption gets a bare picture and no figure.
Obsidian never shows alt text and the Doc shows only the tag, so the
caption is visible locally only inside the tag — by the author's ruling
(2026-09-02) that is fine and no CSS snippet is wanted. The ⇢ ref comes
BEFORE ` | caption:` in the tag. Captions are book prose: the author
asked for them once in one sitting; propose, never rewrite silently.

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

### "Resolve all tabs" — one instruction, the whole Doc (ratified 2026-09-02)
Trigger phrases, in the author's words: *resolve all tabs*, *resolve
all in the google doc*, *resolve everything in the doc*, *settle the
doc*, *resolve the doc*. Any of them means: bring the Doc down and
settle EVERYTHING pending across every tab, in one pass, without asking
"shall I pull?" or "shall I resolve X?" along the way. Ask only where a
ratified rule already demands the author's word (rendering, a verdict
the margin keywords don't settle, a footnote draft's ruling). Run it in
this order — the order is load-bearing, because a pull that comes after
a resolve overwrites the resolve:

1. **Pull everything first, once.** `authorlm doc pull -m <manuscript>`
   (no file = all tabs). This alone executes margin verdicts, harvests
   comments to address, reconciles hand-made tabs, and reports new
   illustration slots and open `[Footnote:]` tags. Never pull again
   mid-pass; per-tab pulls are for a named tab only.
2. **Narrate what came down** — `authorlm diff`, prose changes apart
   from formatting churn — before touching anything.
3. **Forms.** `authorlm filter status`, `authorlm critique status`,
   `footnote list`, and `explain list` name the tabs carrying written
   forms. For each: `filter resolve <essay>` / `critique resolve
   <essay>` / `footnote resolve <essay>` / `explain resolve <essay>`
   (explicit, one at a time), then `doc push <essay>` — A RESOLVE IS
   NOT FINISHED UNTIL THE DOC HAS THE RESULT (the directive resolves
   push themselves). Refusals (broken join, no agreed base) are
   reported, not forced.
4. **Comments to address.** A text change implied → draft with the full
   machinery, register with `doc propose`; a question → answer in chat.
   Verdicts the author already gave in the margin were executed by
   step 1; process the modified-acceptance diffs for the learnings duty.
5. **Footnote and explain tags** (docs/footnote-directive-design.md,
   docs/explain-directive-design.md). Draft every open tag in chat per
   §3 of the footnote design, all at once, each from the claim before
   its anchor. The pull cleared the checkouts, so an apply here lands DIRECTLY —
   wait for the ruling in chat first. A file the author keeps checked
   out takes the Doc road instead: apply writes the forms into the tab
   and the ruling happens there. A declined tag stays open.
6. **Illustrations.** New or changed descriptions get the prompt-critique
   duty on the spot; unrendered slots and externalize offers are RELAYED
   and rendering is offered, never started — consent rule unchanged.
7. **Proposals and conflicts.** `list_proposals` and any tab-drift
   conflicts from step 1 are put to the author as questions.
8. **Close with one report**: what landed (per tab, pushed back), what
   awaits the author's word (renders, footnote rulings, open questions),
   and anything refused with the remedy verb. Confirm every tab that
   was resolved was also pushed.

What "resolve" never does: render an image, run a filter or critique
pass that has not already been triaged, or auto-answer a margin thread
whose reply was conversation rather than a verdict keyword. Those cost
the author's money or words, and the ratified rules for each still hold
inside this pass.

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

## Inline directives (`[Footnote: …]`, `[Explain: …]` — drafted in chat, landed by apply)
The author writes a request INTO the prose, in their shorthand, where
the result belongs: `…the vital lie.[Footnote: cite Becker ch. 2 |
label: RD]` (the superscript lands exactly there) or `…holds.[Explain:
what a vital lie is]` inline, or `[Explain: …]` alone on a line (a
paragraph of its own). Both tags are CONSUMED on apply. Collect and
`doc pull` REPORT the open tags per file (`directives` rows on the
collect report and on `get_status`; `list_footnote_tags` /
`list_explain_tags` on demand); no LLM runs and nothing is drafted
unasked (footnote design §2, ratified 2026-09-02; explain follows it —
`docs/explain-directive-design.md`).

**"Resolve the footnotes/explanations in <file>" is ONE instruction,
end to end.** Assemble with the tools you have: `get_style(file)` (the
citation, lexicon, and footnote laws are binding), `get_concepts`
scoped to the file then by name for the concepts the anchor paragraph
touches, the file's existing `[^X]:` definitions verbatim as voice
exemplars (footnotes), and the essay with the tag marked. Draft one
text per tag and show them in chat. **A footnote is constructed from
where it is requested: read the sentence before the anchor first — the
footnote supports THAT claim, and the gist says how** (house law,
ratified 2026-09-03). It carries what the sentence cannot — a source, an
etymology, a qualification — and never continues the argument (house
law). An explanation is the author's prose at that position, explaining
what the gist names and nothing more. The proposed citation rule
(verified sources only, unverified flags) was REJECTED by the author on
2026-09-03: the anchor claim governs the draft, not the source
discipline. The author reacts; build on the criticism and redraft in chat — no separate
LLM call, no revise verb. Their stated reasons are evidence
(`review_suggestion`) when they state a principle, never by habit.
**Companion footnote (ratified 2026-09-03):** while drafting an
explanation, apply the footnote structure law to the gist — a source,
an etymology, a qualification that would break the paragraph's stride
belongs in a footnote, not in the passage. When one is warranted, draft
it beside the passage — supporting the claim it hangs on — and pass it
as `footnote` in the same `apply_explain` item (CLI `--footnote
TAG=TEXT`): the passage lands with the superscript at its end and the
definition in the file's block, one review, no second `[Footnote:]`
round. Never invent a footnote the gist did not call for.
Then ONE `apply_footnote` / `apply_explain` per file carrying every
agreed text as `items: [{tag, text, footnote?}]` (`tag` is the ordinal in the
file — first open tag is 1 — or an unambiguous gist excerpt); the CLI
`footnote apply` / `explain apply <file> --tag N --text …` takes several
pairs at once. **Two roads, chosen by the file's checkout state
(ratified 2026-09-03):**
- **Not checked out** → direct apply. The tag becomes `[^label]` with
  `[^label]: …` appended after the file's last definition (series
  continues; a new file takes its stem's letter; `| label:` overrides),
  or the explain passage lands in place. Evidence row (`doc_threads`,
  origin `footnote`/`explain`), collected under no episode; `doc push`
  carries it later.
- **Checked out to the Doc** → the tab is the review surface, as for
  every other machine proposal. Apply writes `<<[Footnote: …]>>{{[^B1]}}`
  at the anchor and `{{[^B1]: …}}` after the last paragraph (explain:
  `<<[Explain: …]>>{{passage}}`); the LOCAL FILE STAYS PRISTINE. The
  author edits the green half or deletes the form in the Doc; then
  `resolve_footnotes` / `resolve_explains <file>` (CLI `footnote
  resolve`) reads the tab back, lands their edits, records evidence,
  collects under no episode, and pushes the tab clean. A deleted form is
  a decline: the tag stays open. While forms are out, a second apply on
  that file — either kind — is refused until it is resolved; `doc push`
  refuses too and names the resolve verb.
A declined draft leaves the tag open; nothing is deleted on a "no".
Exports strip an unresolved tag and warn.

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

## Mid-writing standing instructions (ratify, don't just obey)
When the author, during drafting or verdicts, states an instruction in the
form of a general rule rather than a one-off correction ("don't shorten
sentences if they lose meaning", "avoid em-dashes", "use simpler words"),
do all three in the same turn:
1. Apply it to the work at hand immediately.
2. Record it through the loop as usual (reject reason verbatim; `write
   learn` when it recurs) — knowing that writeup learnings are
   SESSION-LOCAL scaffolding that die with the writeup; they are never a
   substitute for ratification.
3. PROPOSE ratification as a style law in the same reply, with a concrete
   recommendation the author can approve with one word ("LGTM"): the
   aspect, the exact statement (the author's words preferred), and the
   SCOPE. Scope heuristic: the house style (root guide) when the
   instruction is about the author's prose generally; the part-level
   guide (e.g. "Part II essays — modern English") when it belongs to that
   register; a file-level element (`style add … --file <essay>`) only
   when it is about this one essay's voice. Name the recommended guide
   explicitly, and never ratify silently — the author's one-word answer
   is the ratification, upon which run
   `authorlm style add <aspect> "<statement>" --guide "<name>"` (or
   `--file <essay>`).

## Standing duties
- Style-law harvesting (ratified 2026-08-29): as the author's rulings
  accumulate in conversation — rejection reasons, rewordings, terminology
  decisions, ordering principles — proactively PROPOSE the generalizable
  ones as style elements (or belief candidates) at the moment they
  generalize: a single explicit terminology/structure ruling qualifies
  immediately; a wording preference qualifies at two independent
  instances. Do not wait for the author to ask for a scan. Propose,
  never silently insert: each proposal names the aspect, the scope
  (guide or file), and the author's own instances verbatim; the
  author's yes ratifies (`style add`), and their no is itself recorded
  evidence. A ruling that contradicts an existing ratified element is
  surfaced as an amendment question (retire + re-add), never left to
  drift.
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
