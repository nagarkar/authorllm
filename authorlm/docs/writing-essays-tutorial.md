# Writing essays with the beat loop — a tutorial

*For the author. Two ways of working are covered: writing a new essay from an
idea, and rewriting an essay you already have. Everything in the main text is
what you say and what you see. Every command sits in a collapsed box below the
step it belongs to, so you can audit it, run it yourself, or check that nothing
is being invented — and never have to read it if you would rather not.*

---

## 0. How to read this

**You talk; the assistant does the rest.** You never hand-write JSON, never
memorise a flag, never type a heredoc. Each section leads with the conversation
— what you say, what comes back — and puts the machinery in a fold box marked
**Under the hood**.

Two things hold throughout, and neither is negotiable:

1. **Nothing enters your manuscript without your yes.** The essay file grows one
   accepted beat at a time, and only when you accept. There is no auto-accept
   anywhere, and the assistant never edits the essay file behind your back.
2. **Nothing can be made up.** A beat may stand only on your brief, on the
   compressed summaries of the essays around it, on your own ratified concept
   notes, on the style guide, and — in a rewrite — on the digest of the old
   essay. Anything a beat needs beyond those becomes a question to you, asked
   before the draft is written.

**Where the writing itself comes from.** Each beat is drafted in this
conversation, by the assistant you are talking to — but not out of thin air.
Before a word is written, the system assembles the beat's materials into a
numbered payload: the style law, your ratified concept notes, the frame, your
brief, the plan, the essay so far, and this beat's spec. The draft is written
against that payload and against nothing else, under a prompt you can read and
edit (`authorlm/prompts/beat-draft.md`) that spells out the grounding rules, the
word budget, and the self-check the draft has to pass before you see it.

Two things follow, and they are the reason it works this way. **Drafting the
beats adds nothing to your bill — it is covered by the subscription you already
pay for.** And the
grounding stays auditable: you can ask to see exactly what a beat was drafted
against, block by block with a checksum on each, before you read a line of prose
— so when a beat comes back wrong, the answer is a list you can check rather
than a conversation you would have to reconstruct.

There is also a mode where a pinned model drafts each beat instead, for a
strictly reproducible draft. It **bills the Anthropic API per beat**, it is
opt-in, and it is switched off in the configuration — deliberately, at your
instruction (2026-08-30). Section 10 says what turning it back on would cost and
where the switch is.

Other moments in the workflow also call out to a model, each to do a specific
job: finishing an essay, refreshing the summaries, closing a goal — and, the one
that happens most often, **turning the reason you gave into a rule you might
want.**
Every rejection you explain, and every reworded acceptance you explain, is read
by a model that tries to state the principle behind it. Section 10 lists them
all, says which model does which job, and says where each is configured.

**One workspace note.** This workspace holds more than one manuscript, so every
command carries the manuscript's name. The assistant adds it. If you are typing
yourself, add it.

---

## 1. Before you start — five things that must be true

You do not check these. The system does, and it stops you if any is missing.
They are listed so the refusals make sense when they arrive.

| What must be true | Why |
|---|---|
| A **goal is open** for this piece of work | The essay's beats land inside a recorded stretch of work rather than in the gap between sessions |
| The essay has a **style guide** attached | The rendered guide *is* the law the drafting follows |
| The **summaries of the surrounding essays are current** | Those summaries are what the drafting is conditioned on; an out-of-date one is a false statement about another essay |
| The essay is **not checked out to Google Docs** | While it is checked out the Doc is the working copy, and a local addition would be quietly thrown away by the next pull |
| **No other writeup is open on this same file** | One loop per essay |

There is no override for the summary check and none for the checkout check.
That is deliberate: you cannot consent past a false statement about your own
text.

A sixth, softer one: if the current work session was opened on a previous day,
the assistant should offer once to close it and start fresh. That is for
tidiness — a stretch of work that spans a week is hard to reason about
afterwards — and **not** because a long session costs you anything. It does not:
see §6.

<details>
<summary><b>Under the hood — checking the prerequisites yourself</b></summary>

```bash
authorlm summarize status -m SMSTTD     # free, no model call: current / stale / missing per essay
authorlm style guides -m SMSTTD         # guide names and per-file attachments
authorlm intent list -m SMSTTD          # active intents
authorlm doc list -m SMSTTD             # link + checkout status
authorlm write status -m SMSTTD         # is a writeup already open?
```
</details>

---

## 2. Writing a new essay

### Step 1 — say what you want, and settle the brief

> **You:** "I want a short essay on Weil's attention, sitting after the Becker
> essay in Connections."

The assistant reads your concept graph, the style guide and the surrounding
essays, and comes back with a **candidate one-paragraph brief**, the position it
inferred, and the style guide it proposes:

> **Assistant:** "After the Becker essay, so the Connections guide — that's what
> Becker itself uses. Here's a draft brief: …"

Both of those are proposals you say yes or no to, not questions you have to
answer from memory. The guide is never chosen silently — it is the law the
drafting follows, so it is named out loud and you agree to it — but you should
never have to go and look it up.

Then you argue with the brief's wording until it is yours.

That paragraph matters more than it looks. A brand-new essay has no old text to
model, so the brief is the *only* ground specific to this essay that every beat
is allowed to stand on. It is kept verbatim and read back to you every time you
come back to the work.

> **You:** "Closer. Cut 'faculty' — say 'attention is what makes the hard floor
> visible'. Otherwise yes."

There is no separate step for ratifying it. The conversation *is* the
ratification.

<details>
<summary><b>Under the hood — the exact command</b></summary>

```bash
authorlm intent declare "Write the essay on Weil's attention" -m SMSTTD
# → Declared intent [di-4b7e1]: …

authorlm write start weil.md --new --intent di-4b7e1 \
    --after becker.md --style "Connections essays" -m SMSTTD <<'EOF'
A short essay placing Simone Weil's attention alongside the Dharmic's tending:
attention as what makes the hard floor visible, and affliction as what happens
when the floor is removed by someone else's kindness.
EOF
```

Four things are required together and only together with `--new`:

- `--new` — name resolution is substring-based, so without the flag a typo would
  silently become a new essay. `--new` on a name that already resolves is
  refused.
- `--after <file>` or `--after start` — a file that does not exist has no
  position to infer. Without it an unlisted file sorts to the *end* of the
  reading order and the entire book reads as settled context behind it.
- `--style "<guide>"` — `style attach` cannot run before the file exists, so the
  guide is named here and validated before anything is created. Forget it and
  the refusal lists the guides on record *and* names the guide the `--after`
  anchor uses.
- **the brief on stdin.**

Every gate runs before any write. A blocked start leaves the disk byte-identical.
</details>

### Step 2 — the frame you are handed

What comes back has three parts:

- a line confirming the file was created and the guide attached, and — read this
  one — a note that abandoning this piece of work will **delete** the new file
  again (nothing typed into it is lost; see §9).
- **your brief**, verbatim.
- the **frame**: everything settled *before* this essay in the book, compressed
  to a few sentences each (those concepts are available to you and must not be
  re-introduced), and everything still to come *after* it (those concepts are
  not available yet — a beat that needs one has to point forward to it).

**Read any line starting `!!`.** They are printed in yellow and they are the
only warnings in that output:

- a summary marked **stale, missing or deprecated** — that entry is not
  trustworthy. It cannot happen when you start; it can appear later, when you
  come back (§8).
- **coverage incomplete** — that essay's summary never mentioned some of its own
  paragraphs. Informational, not a block: treat that neighbour as possibly short
  a move.
- **an essay being written right now, in another writeup** — see §7.

### Step 3 — ratify the plan

The assistant turns the brief into a handful of beats and reads them back to
you. This is the single most important thing you do in the whole loop.

**Plan ratification is the fabrication guard.** What each beat is for is not
enough; what each beat will *claim* is the thing you are ruling on. That is not
pedantry — it is the fix for a measured failure. In one experiment the model was
given the style guide, the concept notes and the full surrounding context, asked
for two thousand words in one shot, and invented an attribution to a scholar it
had never been given, recording in its own notes: *"I do not know the real
essay's actual argument, so I built one."* The gap between "a claim is needed
here" and "I have no claim here" got closed silently, while drafting. The plan
moves that decision upstream, where you can see it.

So you are read a numbered plan, one entry per beat, four things each:

1. **what the beat is for** — said as a job: opens the essay, answers the
   objection, closes on the ground it started from;
2. **what it will claim**, in one sentence of ordinary English — the claim
   itself, not "develops the argument";
3. **which of your points, examples and concepts it carries** — by your own
   names for them; in a rewrite, which point from the current essay each one is;
4. **roughly how long it runs.**

And then you are asked, in these words:

> Approve as-is, or tell me in any words: reorder, strike a beat, change a
> budget, add a beat, or redirect one — I'll revise and re-present until you say
> it's right. Nothing is drafted until you approve.

Take that literally. Reply in plain words — "beat 2 is doing two jobs, split
it", "drop the biography line", "make beat 4 shorter" — and the whole revised
plan is read back to you again. Nothing is drafted until you say yes.

**Every proper name, citation or quotation a beat will use is named in the plan
before it appears in prose.** If it is not in the plan you approved, it does not
belong in the draft. Refuse a beat whose description is only "transitional".

<details>
<summary><b>Under the hood — the exact command</b></summary>

```bash
authorlm write plan -m SMSTTD < /tmp/weil-plan.json
# → Plan ratified: 3 beat(s) ahead.
#     • [n=1 | opener | concepts: Attention | budget ~140]: claims attention is …
```
Each beat is `{"role", "concepts", "budget", "notes"}`. Amend later with
`--replace`: written beats are kept, the remainder is replaced, and replacement
beats get fresh numbers automatically so recorded verdicts cannot be corrupted.
Running `write plan` again *without* `--replace` is refused with a message
naming the flag. See §3.7 for what `--replace` insists on first.

The CLI validates that the plan is a non-empty array of objects. It does **not**
check that the notes state a claim — it cannot. That guard is yours.
</details>

### Step 4 — the beats

Per beat, twice:

**You are shown a draft with its reasoning** — which concepts it realizes, which
precedent it follows, and, in a rewrite, which points of the old essay it
carries. That explanation is required: a draft without one cannot even be
registered.

**You give a verdict.** Three shapes, in plain words — see §4.

Then the next beat. Accepting is the only thing that touches the file: your text
is added, the revision is recorded, your verdict is stored as evidence, and the
loop moves on — all at once.

The draft is written here, in the conversation — but not from whatever the
conversation happens to be doing. The beat's materials are assembled first, into
a numbered payload with a checksum on each block, and the draft is written
against that and against nothing else, under a prompt you can read and change.
That matters for one reason: when a beat comes back wrong you can look at
exactly what it was drafted against, and the answer is a short list you can
check. It adds nothing to your bill.

**Sometimes it refuses**, and that is the feature working. A beat that cannot be
written without a fact nobody gave it — an attribution, a date, a source — comes
back as a question instead of an invention. Nothing is registered, nothing moves,
and the question is for you. Usually the answer is a correction to that beat's
plan. This is the one failure the whole loop exists to prevent, so when you see
it, answer it rather than working around it.

<details>
<summary><b>Under the hood — the exact commands</b></summary>

Three commands per beat: assemble, register, rule.

```bash
authorlm write draft --dry-run -m SMSTTD
# → Beat [n=1 | opener | concepts: Attention | budget ~140]: …
#   Payload for no [writing] model configured — draft this payload in the
#   conversation, then register it with 'write propose --why …' — no call made.
#   Prompt: authorlm/prompts/beat-draft.md
#   ───── block S — 7,057 chars — sha256 687c06f3… (the prompt, then the style law)
#   ───── block A — 9,213 chars — sha256 1f4ad0be… (frame, beliefs, plan, concepts)
#   ───── block B — 4,880 chars — sha256 c02e77a1… (the essay so far)
#   ───── block C — 1,204 chars — sha256 9b31de07… (this beat, lessons, verdict)
```
This is where the payload discipline comes from, and it is the step you should
never skip. `--dry-run` assembles the whole thing — the style law, your
validated rules, the drafting context, the brief, the digest, the plan, the notes
for every concept the plan names, the essay so far, this beat's spec, the lessons
and your last verdict — prints it with a checksum per block, and **makes no
model call**. The assistant drafts the beat against those blocks and nothing
else, under the prompt the last line names. The checksums are also how a caching
problem is found, if you ever turn the billed mode on: compare them across two
beats, and the block that moved is the culprit.

```bash
authorlm write propose --why "realizes Attention; opener per the plan; grounded \
    in the brief and kindness.md's hard floor — no source cited beyond Weil's \
    name" -m SMSTTD < /tmp/beat1.md

authorlm write accept -m SMSTTD                        # as-is
authorlm write accept -m SMSTTD < /tmp/reworded.md     # your wording wins
authorlm write reject --reason "…" -m SMSTTD           # reason required
```
`write propose` registers the draft. It is the same verb used for a beat you
dictated and for wording you asked for mid-beat — one route, one kind of
evidence. `--why` is required, and it is what your verdict is recorded against.
A redraft supersedes the pending proposal — you never accumulate two live drafts
for one beat.

**The billed mode.** `authorlm write draft` *without* `--dry-run` sends that same
payload to the pinned `[writing]` model, which writes the WHY, the SELF-CHECK
and the prose itself and registers them through `write propose`'s path. It bills
the Anthropic API per beat. As shipped there is no `[writing]` section, so it
refuses and prints the TOML to paste; the restore recipe is also in
`authorlm/config.toml`'s own comments, and §10 says what it would cost. Only
that mode can answer `BLOCKED` in the machine's own voice — but the same rule
binds the conversational draft, which asks you the question instead of inventing
the fact.
</details>

### Step 5 — lessons, occasionally

When a pattern recurs *across* your verdicts — not on every verdict — the
assistant distils one line and records it. It rides forward into every later
draft in this piece of work, and is read back to you whenever you return.
Lessons are scaffolding for the session, not memory: they never write to your
style or policy stores on their own. The ones that keep recurring are promoted
later, through the ordinary ratification you already do.

You should hear about this when it happens — "you have stripped an unnamed
source twice now, shall I stop proposing them?" — not discover it afterwards.

<details>
<summary><b>Under the hood — the exact command</b></summary>

`write learn` is **stdin only**. `authorlm write learn "lesson"` silently drops
the argument and then errors for want of stdin — a known footgun.

```bash
authorlm write learn -m SMSTTD <<'EOF'
Author strips external sources the plan did not name — propose bare claims.
EOF
```
</details>

### Step 6 — finishing

> **You:** "That's the essay."

That sentence is the consent, and the assistant should act on it rather than ask
you again. It finishes the essay, refreshes the summaries and closes the goal,
then reports back in a couple of sentences. Ending the work session is offered,
not required.

Finishing does three things worth knowing:

- **It mines the finished prose for concepts.** This is a model call, and it is
  the one the whole loop has been saving up for: the per-beat steps deliberately
  skip it so the reading happens once, over the finished essay, instead of
  partially over every fragment. What it finds arrives as proposals for you to
  rule on, never as facts.
- **For a new essay, it registers the essay in the table of contents** at the
  position you declared. Without that entry the essay is finished, on disk, and
  structurally invisible — it can never be summarized in position, and the next
  piece of work near it would be refused for a file you believe is done. If the
  anchor cannot be found, the exact lines to paste are printed and nothing
  fails. Paste them; do not skip it.
- **It tells you the essay's own summary is now out of date** and leaves the
  refresh to you (or to the assistant). It does not refresh automatically,
  because refreshing one summary marks everything downstream of it as needing
  attention, and that is not a side effect finishing should have.

You decide when an essay is done. Beats you planned and never wrote are
reported, not enforced.

<details>
<summary><b>Under the hood — the three commands, and what each spends</b></summary>

```bash
authorlm write complete -m SMSTTD
authorlm summarize rebuild -m SMSTTD          # one summarizer call per stale unit
authorlm intent complete di-4b7e1 -m SMSTTD   # separate — runs episode analysis
```

All three make model calls, each for its own purpose: completion runs the
deferred concept-extraction pass (one call, plus one adjudication call when
two-step extraction is on); the rebuild writes one fresh summary per essay whose
text moved; `intent complete` reconstructs what you decided across the episode.
Completion's is the only one inside `authorlm write`.

They do NOT degrade alike with no model configured, and the difference matters:
`write complete` succeeds and simply defers the extraction, and
`intent complete` succeeds and skips the analysis — but `summarize rebuild`
**fails loudly**, because a summary is the thing it exists to produce and there
is no honest way to produce one without the model.
</details>

---

## 3. Rewriting an essay you already have

Everything in §2 applies. Three things are added: a **reading of the old essay**,
a **ruling on each part of it**, and a closing section that accounts for whatever
was dropped.

### Step 1 — which kind of rewrite

Before anything else you should be asked one question, once, in these terms:

> Two ways to do this:
> - **I first extract every point, example, and reference from the current
>   essay; you rule on what's kept or dropped, and the final essay accounts for
>   anything removed.** (Recommended when the old content matters.)
> - **The old essay is raw material; nothing is tracked point-by-point.**

That is a real fork, not a phrasing preference, which is why it is put to you in
those words rather than by name. The first is the right default whenever the old
essay carries anything you would mind losing — which is nearly always, for
anything already drafted. The second is right when you are starting over and the
old text is just a quarry.

The rest of this section describes the first. If you pick the second, skip
steps 2, 4b and 5 and follow §2 exactly.

### Step 2 — say what to keep

> **You:** "The Epictetus essay needs a rework. Keep the prohairesis/Dharma
> comparison and the divergence on the scope of choice. Move the biography down
> — open on the claim, not the man. Cut anything the discernment essay already
> owns."

That is the brief. In a rewrite it is optional, and this is exactly where it
earns its keep: "retain these points, reorder for flow" belongs here and nowhere
else.

**What happens to the file: it is cleared.** The old essay is not lost — it is
pinned in the database first, and that pinned copy is the only one the work will
read from here on. In its place the file gets a short visible note saying the
essay is mid-rewrite and how to get the old one back, so that opening the file
in Obsidian shows you an explanation rather than a blank page. That note is
never part of a finished essay: your first accepted beat replaces it outright,
and finishing is refused outright if it is still the only thing there.

A second safety net costs one command and is worth taking, because the
manuscript directory is not under version control — there is no git history for
the prose:

```bash
cp authorlm/manuscripts/SMSTTD/epictetus.md \
   authorlm/manuscripts/SMSTTD/_drafts/epictetus.pre-rework.md
```

`_drafts/` is invisible to the system, so the copy costs your concept graph
nothing.

<details>
<summary><b>Under the hood — the exact command</b></summary>

```bash
authorlm intent declare "Rewrite the Epictetus essay: same crux, better flow" -m SMSTTD
authorlm write start epictetus.md --intent di-a301f -m SMSTTD < /tmp/brief.txt
# → Pinned v423 as raw material (8912 chars); file truncated.
# → BRIEF …  DRAFTING CONTEXT …
# → Raw material: write digest prints the pinned original.
```
No `--new`, no `--style` (the file already has an attachment — `--style` without
`--new` is refused), no `--after` unless the essay has no toc entry.
</details>

### Step 3 — the reading of the old essay, and your corrections

The assistant reads the pinned original back and pulls out four lists: every
**claim** it made, every **example**, every **reference** or attribution, and
every **inconsistency** — each of those naming the earlier essay it conflicts
with.

**You will never be shown the raw data.** You are read a numbered account, in the
old essay's own order, one sentence per claim, with each claim's example or
citation attached to it rather than listed off separately. Then — and only then
— the two or three the assistant is least sure of, plus every claimed
inconsistency, are put to you explicitly by number. You correct it in words, by
number.

That order is deliberate. **You correct the reading before it is recorded**, and
that correction is the entire reason it counts as your judgment rather than a
machine's. Eighteen lines of data get rubber-stamped, and a rubber-stamped
reading is exactly the machine reading the correction exists to prevent. Expect
to move things: a "claim" that was really two, an "inconsistency" that is not
one.

Once recorded, it becomes law in a specific and useful way:

> **The recorded reading is the sole authority on what the original said.**
> Anything not in it is not in the original.

That has a sharp consequence. During a rewrite, the compressed summaries of
*neighbouring* essays can still describe the **old** version of the essay you are
rewriting. Under this rule that leak is inert: nothing may be "retained" on the
strength of a neighbour's description of the essay being rewritten.

Reordering, compressing, merging and sharpening what the old essay said is the
*purpose* of this kind of rewrite and is fully permitted. Adding a claim, example
or citation that is in neither the reading nor the brief is a new commitment of
yours — it comes to you, at plan time.

<details>
<summary><b>Under the hood — four shapes, one verb</b></summary>

```bash
authorlm write digest -m SMSTTD                      # prints the pinned original
authorlm write digest -m SMSTTD < digest.json        # persists (refused if one exists)
authorlm write digest --replace -m SMSTTD < d.json   # replaces
authorlm write digest --dispositions -m SMSTTD < d.json
authorlm write digest --show -m SMSTTD               # stored digest + tally
```
The four lists are `points` (`p1`, …), `examples` (`x1`, …), `references`
(`r1`, …) and `inconsistencies` (`i1`, …). Ids must be unique across the *whole*
digest, and cross-references (`serves`, `points`) must resolve to real point
ids; `inconsistencies[].with` must name a real manuscript file. A dangling
reference is refused rather than stored — an accounting built on dangling ids is
decorative. Validation is all-or-nothing.

`--replace` may not drop an id that already carries a disposition; it names the
offenders and refuses.

`write digest` is refused outright on a writeup that created its file: "there is
no source essay to digest."
</details>

### Step 4 — plan, carrying the old essay's points

Same ratification as §2.3, plus: **each beat names which of the old essay's
points it carries**, and the plan's *ordering* is where "reorder for better flow"
actually happens. Put any footnotes beat *before* the closing accounting beat —
beats are added in order, so the last one planned lands last.

### Step 5 — the beats, then account for what happened

Run the beats exactly as in §2.4. After each stretch, every point of the old
essay has to be accounted for. There are two answers and only two:

- **kept** — **you are not asked for these.** The plan you approved already names
  which points each beat carries, every draft repeats them, and accepting records
  which beat landed — so the whole "kept" half is derived for you and recorded.
  It is stated back to you in one line ("p1, p3, p7 kept across beats 2–4") so
  you can contradict it, and that is the only attention it should cost you.
- **removed** — **a reason is required**, in your words, and this is the half
  that actually comes to you, one at a time. Same principle as an explained
  rejection: an unexplained removal teaches nothing and cannot be written up.
  The nuance goes in the reason, because the reason is what the closing section
  will quote.

There is no third answer. "Moved", "deferred", "merged into p3" all belong in the
reason.

Changing your mind is allowed and is *reported*, not hidden.

<details>
<summary><b>Under the hood — the exact command</b></summary>

```bash
authorlm write digest --dispositions -m SMSTTD <<'EOF'
{"p1": {"disposition": "kept", "beat": 3},
 "p6": {"disposition": "removed",
        "reason": "duplicates discernment.md's account of the contracted faculty (i1); discernment.md owns it and says it better"}}
EOF
# → Dispositions recorded: 2 (1 kept, 1 removed).
#   Accounting: 7 kept, 2 removed, 0 UNACCOUNTED.
```
</details>

### Step 6 — the section that accounts for what was dropped

The final beat, or two, is the section itself, headed **What Was Removed and
Why**.

It is **written from the reasons you already gave** — only the connective prose
is new. The judgments were made when you made them; they are not re-invented at
drafting time. It is proposed and accepted like any other beat.

It is ordinary manuscript prose: it will be collected, scanned, summarized,
pushed to Docs and exported like anything else. If you later decide it should not
ship, delete it — the durable record is kept separately from the prose.

### Step 7 — finishing, and the tally

```text
Removal accounting: 5 point(s) kept, 2 removed, 2 UNACCOUNTED.
  UNACCOUNTED — no disposition recorded, and no 'What Was Removed and Why'
  entry can be trusted while these are open:
    p8  "the Stoic's compatibilism draws the fence of freedom too narrowly"
    p9  "every choice ramifies outward; the scope is not confined to the interior"
  Record them: write digest --dispositions  (JSON on stdin).
```

**It warns; it does not stop you.** Finishing succeeds with points unaccounted,
by design, and the reasoning is worth knowing because you may want to overturn
it:

- An unaccounted point is the same kind of open item as an unwritten beat, and
  finishing already does not require every planned beat to be written.
- Stopping you would reward the cheapest escape — marking a dropped point as
  kept — turning a visible gap into an invisible lie.
- The tally is not a one-shot: it is printed every single time you come back. An
  author who ignores it at the end has ignored it repeatedly.

Nor is there any check that the removal section actually exists in the file. That
would be a judgment about content, and content judgment lives with the critique
pass, not here.

### Step 8 — changing the plan mid-rewrite

Say a beat's draft is wrong in a way that means the *plan* is wrong, not the
prose. The remedy keeps the beats already written and replans the rest;
replacement beats are renumbered, which is precisely what makes it impossible for
a replan to corrupt a verdict you already gave. Accepted text is never at risk.

**Settle the pending draft first.** Replanning is refused while a draft awaits
your verdict:

```text
a draft is still awaiting your verdict on beat n=7 — replacing the plan would
strand it as 'proposed' forever and lose the reason you are replanning for.
Settle it first: 'write reject --reason "<why the plan is wrong>"' (that reason
IS the evidence for the replan), or 'write accept' to keep the draft.
```

Almost always the right exit is the rejection, because the sentence you would
give as the reason *is* the reason you are replanning at all — and that is the
single most valuable thing the loop can record. The refusal exists because the
old behaviour silently orphaned that draft: it vanished from view, counted in
every tally forever, and took your reason with it.

---

## 4. Saying yes, no, and "almost"

Three shapes. Say them in plain words.

| You say | Recorded as | What happens |
|---|---|---|
| "yes, that's it" | accepted | the draft is added verbatim, and the loop moves on |
| "almost — make it *this*" (you give the text) | modified | **your** text is added; the difference between the draft and your wording is kept as evidence |
| "no, because …" | rejected | nothing is added, the beat does **not** advance, your reason is stored word for word, and the redraft is written with it in view |

**Why the reason on a rejection matters.** It is the highest-value thing the
system can be told. A bare "no" teaches nothing; "you cited *Gravity and Grace*
— the plan said no citation beyond her name and I meant it" teaches a rule, and
you will see it come back:

```text
Beat n=1 rejected — reason recorded verbatim. Redraft with the reason in context.
Seeded candidate belief: Do not introduce a source the ratified plan did not name.
```

**That line is where a model call happens, and it is the one you will meet most
often:** your reason, read by a model whose whole job is to say what principle
it implies — or to say that it implies none, when the reason was about this beat
and nothing else. It never invents the rule from the draft; it works from the
sentence you actually wrote. A reason is required on a rejection, so **every
rejection spends**, and so does a reworded acceptance when you explain it. A
plain "yes" costs nothing. If no model is configured your reason is still
recorded and still seeds a candidate — just verbatim, unpolished.

A candidate belief is not a rule yet. It becomes one only on several
**independent** observations — different verdicts about different drafts. Two
records collapse into one only when they are literally the same observation
twice (same session, same draft), which is there so a replay cannot inflate the
count; two real verdicts always count as two, even in one sitting. A
machine-authored conjecture can seed a candidate but can never validate one.

A reason is required on a rejection. On an acceptance it is optional but welcome:
an explained *yes* is evidence too. If you accept bare, you should be asked once
— gently, once, never during bulk work — whether there was anything behind it.
"Just record it" ends the asking.

**A reword is not a lesser yes.** It is the richest signal in the loop: the
difference between what was proposed and what you wrote is exactly the shape of
the correction. Reword freely.

---

## 5. When it says no

Every refusal names its remedy, and none of them writes anything: a blocked start
leaves your files byte for byte as they were. What can stop you, in plain terms:

- **The new essay has no position in the book.** Say where it goes — after some
  essay, or at the very start.
- **No style guide is attached.** The message lists the guides on record and, if
  it can tell, names the one the neighbouring essay uses.
- **The name already exists** (you meant a rewrite), or **does not exist** (you
  meant a new essay), or is a typo.
- **A new essay has no brief yet.** This one fires last on purpose: every other
  refusal names something you can add to the line you just typed, and this one
  sends you away to compose a paragraph.
- **A summary somewhere in the surrounding context is out of date.** Refresh the
  summaries. There is no override, because that summary is what the drafting
  would be conditioned on.
- **The file is checked out to Google Docs.** Pull it back first.
- **A writeup is already open on that essay.** Resume it, or put the old essay
  back.
- **You planned twice**, or **proposed before planning**, or **gave a verdict
  with nothing pending** — each says exactly which.
- **A draft is still awaiting your verdict** and you tried to replan. Settle it
  first, ideally by rejecting with the reason that made you replan (§3.8).
- **The essay you asked to critique is mid-rewrite.** An edit pass over it would
  be editing a placeholder. Finish the rewrite or put the old essay back.

**One rough edge, honestly.** Typing a new-essay start yourself at a terminal,
you can hit four of these one at a time — position, then guide, then guide-exists,
then brief. The ordering is deliberate but the experience is a parade. In
conversation all four are pre-checked, so you see at most one round trip.

<details>
<summary><b>Under the hood — the messages, verbatim</b></summary>

| Refusal (abridged) | What to do |
|---|---|
| `weil.md does not exist yet, so it has no place in the reading order — say where it goes: --after <file it follows> \| --after start` | `--after start` opens the book. |
| `weil.md has no attached style guide … With --new, name it: --style <guide>.` — followed by `Guides on record: …` and, when `--after` names an anchor with an attachment, `becker.md uses 'Connections essays' — likely the one you want.` | Pick from the names in the message. Nothing is defaulted for you. |
| `no style guide named 'Nope'` | Typo, or the guide does not exist yet (`style guide <name>` creates one). |
| `becker.md already exists — drop --new to rewrite it` | You meant a rewrite. |
| `--style is only for --new; an existing file's guide is attached with 'style attach becker.md …'` | One way to do each thing. |
| `write start --new requires the one-paragraph brief on stdin` | Compose the brief. |
| `missing/stale/deprecated summaries: … — run 'summarize rebuild' first (the drafting pass will not read a lie about the text)` | Run `summarize rebuild`. No `--force` exists. |
| `epictetus.md is checked out to Google Docs — the Doc is the working copy. Run 'doc pull epictetus.md' first.` | Pull first. Fires on start, propose and accept. |
| `writeup wu-… is already active on epictetus.md` | Resume it (`write status`) or abandon it. |
| `a plan exists — pass --replace to amend the remaining (unwritten) beats` | You planned twice. |
| `a draft is still awaiting your verdict on beat n=7 — … Settle it first: 'write reject --reason "…"' … or 'write accept' …` | Reject with the reason that made you replan, or accept. |
| `no ratified beat plan — set one: write plan (JSON on stdin)` | You proposed before planning. |
| `nothing proposed for beat 3 — write propose first` | You gave a verdict with no draft pending. |
| `--why is required: which concepts this draft realizes, which precedent it follows` | Verdict evidence hangs off it. |
| `--reason is required: the author's why, verbatim` | On reject, always. |
| `a digest is already recorded for this writeup — pass --replace` | And `--replace` still refuses to drop ids carrying dispositions. |
| `this writeup created weil.md; there is no source essay to digest.` | Digests model rewrites only. |
| `epictetus.md still holds only the mid-rewrite placeholder — … Put the old essay back with 'write abandon', or draft a beat first.` | You finished before accepting anything. |
| `epictetus.md is being rewritten right now by an active writeup — … Finish the writeup ('write complete') or put the old essay back ('write abandon').` | A critique pass over a mid-rewrite essay. |
| `all planned beats are done — write complete (or extend the plan: write plan --replace)` | Either finish or plan more. |
</details>

---

## 6. Sessions, intents, writeups — who owns what

Three words get used constantly and mean three different things. They nest, and
knowing how they nest removes most of the confusion about what to restart and
what to leave alone.

**A session is the ambient stretch of work.** It starts by itself the moment you
do anything, and it closes by itself when you have been away long enough. It is
the container for everything below, and **you never need to restart it.**

It is worth being exact about what a session does and does not affect, because
it is easy to assume the wrong thing in either direction. When the system counts
how much evidence stands behind a candidate rule, two records collapse into one
**only when they are the same observation twice** — the same session *and* the
same thing observed. That is there so replaying an analysis, or your typing the
identical explanation twice in one sitting, does not look like two independent
findings. Two different verdicts on two different beats are two different
things observed, so **both count, however long the session runs.** A long
session does not cap what the loop learns from you, and chopping one in half
does not help it. Close one when it has stopped describing a coherent stretch of
work, and for no other reason.

**An intent is a goal you declared.** "Rewrite the Epictetus essay." "Write the
Weil essay." Several can be open at once, and they are closed one at a time when
the goal is met — closing one is what triggers the reconstruction of what you
decided while pursuing it.

**A writeup is one essay being written or rewritten, beat by beat.** It belongs
to exactly one intent. It holds the pinned original, the plan you approved, the
running verdicts, the lessons, and the accounting. It survives everything:
closing your laptop, days away, a crashed terminal. You come back and it is
exactly where you left it, with the frame recomputed fresh.

So: **two parallel rewrites are two intents, each with its own writeup, all
inside one session.** Nothing needs restarting, nothing needs closing first, and
the session is not a thing you manage.

<details>
<summary><b>Under the hood</b></summary>

A session is a `sessions` row, auto-created by `ensure_session` on any state
change and retroactively closed once it exceeds `idle_hours`. The dedup above is
`beliefs._derive_supporting`, which groups evidence by
`(session_id or evidence id, target)` — so same session AND same target
collapses, and nothing else does; a beat's `target` is its own draft text, so two
beats never collide. An intent is a
`declared_intents` row (`declare` / `complete` / `abandon`); `intent complete`
runs episode analysis. A writeup is a `writeups` row bound to one intent by
`intent_id`, holding `source_version_id` (the pinned original), `plan`,
`cursor`, `learnings` and its metadata. With more than one open, every `write`
verb wants `--writeup <id-prefix>` rather than guessing.
</details>

---

## 7. Two essays at once

You can have two rewrites going at the same time, and nothing stops you. There
is one thing worth understanding about it, in the same terms you put it in
yourself: **it is slightly suboptimal, and you should know that it is.**

Here is why. While an essay is being rewritten, its file on disk holds a short
visible note saying so rather than the essay. Every other essay's frame is built
from the essays around it — so what should the *second* rewrite be told about
the first?

The answer is: **the version from before the rewrite started**, pulled out of the
database, never the placeholder on disk. That is the honest thing to serve, and
it is the useful one. But it is a version of the essay that is on its way out,
and you are told so, at the top of the frame and again on the entry itself:

```text
!! 1 essay in this context is being written right now, in another writeup:
!!   01-choice.md — mid-rewrite. What you get below is the PRE-REWRITE essay,
!!     summarized from that writeup's pinned source version, not from the file
!!     on disk (which currently holds a placeholder).
!! This is slightly suboptimal and it is worth knowing: whatever those
!! writeups change, this draft will not know about, and this draft's essay
!! will not be in their context either. Finish or abandon them first if that
!! matters more than starting now.
```

That is the whole trade. Each rewrite is drafted against the other's *old*
version, and neither can see what the other is currently doing. If that matters
for the pair of essays in front of you — if one is being rewritten precisely
because of what the other is becoming — finish one first. If it does not, carry
on; you have been told.

Two smaller things follow from the same rule:

- **A brand-new essay in flight contributes nothing** rather than blocking
  everything. There is no earlier version of it to summarize, so its entry says
  exactly that instead of pretending or refusing.
- **A critique pass will not run over an essay that is mid-rewrite.** What is on
  disk is the placeholder, so the edits would be edits to a note. Finish the
  rewrite, or put the old essay back, and then run it.

The warning cannot be switched off. That is on purpose, and it is the same
reasoning as the summary check: a warning you can silence is a warning that will
be silenced on the day it mattered.

**And the timing cannot bite you.** Refreshing the summaries takes minutes, and
it is entirely possible for another session to clear an essay halfway through
that run. It cannot corrupt anything: every essay is read into memory once,
before the first call, and the whole run works from that one reading. An essay
cleared underneath it is still summarized from the text that was there when the
run began — which is exactly the text the other session pinned, so the two agree
rather than disagreeing. The same rule holds for every check the system makes
before letting you start: the text it approves is the text it then hands you,
because it only looks once.

<details>
<summary><b>Under the hood</b></summary>

An in-flight file contributes its **pinned** text — `writeups.source_version_id`
→ `manuscript_versions.files` → that file's text — to everyone else's context.
Two states join the freshness list: `rewriting` (a stored summary matches the
pinned text; served, labelled) and `unwritten` (the writeup created the file, so
there is nothing to serve). Both are accepted by the drafting gate and the
critique gate, which remain one predicate.

`summarize rebuild` on an in-flight file summarizes the **pinned** text, not the
placeholder — so the remedy the refusals print actually works — and the entry
settles at `rewriting` afterwards, never `fresh`. A file created by
`write start --new` has no pre-rewrite text at all: the full rebuild skips it and
says so, and the single-file rebuild refuses. Design: §14 of
`docs/autoregressive-writing-design.md`.
</details>

---

## 8. Coming back after a break

**You are told, without having to ask.** An open writeup means the essay is
mid-surgery on disk, so it is manuscript state, not knowledge, and it is printed
first and in yellow whenever you open a conversation or ask for a briefing:

```text
Open writeups — these files are truncated on disk and rebuilding one accepted
beat at a time:
  • epictetus.md — beat 4 of 9 · a draft awaits your verdict [wu-55ce0 · intent di-b814d]
  → resume: write status   ·   restore the old text: write abandon
```

**Then one command reprints everything the loop knows:**

- which essay, how far along, and the current beat's full description
- whether a draft is **waiting on your verdict**
- the tally of your verdicts, and every lesson recorded
- your **brief**, verbatim
- for a rewrite: the reading of the old essay and the running accounting —
  yellow whenever anything is unaccounted for
- the full **frame**, recomputed live

That last point matters after a long gap. The frame is not stored — it is rebuilt
from the current summaries every time you look, so if a neighbouring essay
changed while you were away, the resume view says so in yellow rather than
quietly serving you the false statement the start gate would have refused.
**Read the `!!` lines before accepting anything on resume.** The resume view
cannot refuse — it has to keep working — so it warns loudly instead.

One thing it will not do for you: **a pending draft survives indefinitely, and
accepting it after a long gap accepts something written against the world as it
was.** If the context moved, ask for a redraft rather than accepting the stale
one.

If the file has left the disk entirely, the resume view degrades rather than
dying: the frame is replaced by a one-line note and everything else still shows.

<details>
<summary><b>Under the hood — the exact commands</b></summary>

```bash
authorlm briefing -m SMSTTD
authorlm write status -m SMSTTD
authorlm write status --writeup wu-3d80b -m SMSTTD    # a specific writeup
authorlm write digest --show -m SMSTTD                # re-read the stored digest
authorlm write digest -m SMSTTD                       # re-read the pinned original
```
With several writeups open the `write` verbs refuse ambiguously (`multiple
active writeups (a.md, b.md) — pass --writeup`) rather than guessing. The
briefing lists them all.
</details>

---

## 9. Bailing out

Abandoning ends the piece of work. What it does depends on which kind you are in,
and the difference is severe enough to state plainly.

**Rewriting an existing essay:** the file is restored from the pinned copy,
exactly as it was, byte for byte.

```text
Writeup [wu-3d80b6e2] abandoned; file restored from the pinned source version.
```

**A new essay:** the thing to restore it to is *nonexistence*, so **the file is
deleted**, and the style attachment created along with it is removed too.

```text
Writeup [wu-9c2f4a1b] abandoned; weil.md deleted — the writeup created it, so
the restore target is nonexistence.
Its text is preserved in v421 (nothing typed into it was lost).
```

**Nothing you wrote is destroyed.** A snapshot is taken *first* — that snapshot
is the recovery point, and it captures even half-typed text that was never
recorded. The version number in the second line is where the prose lives. The
history then honestly reads create → beats → remove: the prose is unpublished,
not erased.

You should be told the file will be deleted before it happens. There is no
confirmation prompt in the command itself, so if you are typing it yourself, be
sure which kind you are in.

Expect some honest noise afterwards: any concept whose home was the deleted file
raises a question for you to settle. That wants triaging, not fixing.

<details>
<summary><b>Under the hood — recovering the text</b></summary>

```bash
authorlm write abandon -m SMSTTD
authorlm history show v421 -m SMSTTD     # the version the abandon message named
```
</details>

---

## 10. What it costs, and which model does what

Four moments call out to a model, each for a job worth naming. **Drafting the
beats is not one of them** — the beats are written in this conversation, and
that is covered by the subscription you already pay for. What follows is the
whole of what the system spends on its own:

| Moment | What the model is for |
|---|---|
| **Every rejection, and every reworded acceptance you explain** | Reading the reason you gave and trying to state the principle behind it, so it can be offered back to you later as a candidate rule. This is the frequent one — a reason is required on a rejection, so **every rejection spends** — and it is the one the loop learns from. It may also decide your reason was too situation-specific to generalise and propose nothing. |
| **Finishing an essay** | Reading the finished prose for concepts and relationships you might want to add to the graph. Deferred through the whole loop deliberately, so it happens once over a finished essay rather than partially over every fragment. |
| **Refreshing summaries** | Writing each essay's compressed summary — the few sentences every other essay's frame is built from. One call per essay whose text moved. |
| **Closing a goal** | Reconstructing what you decided across the episode, so recurring patterns can be offered to you as candidate rules. |

All four are billed API calls, and all four run on the cheap models named in the
box below — none of them touches the Anthropic API. Beat drafting bills anything
at all **only if you turn the pinned drafting model back on**, which is a
deliberate edit to a configuration file and is described below.

A plain acceptance costs nothing, with or without a reason: only a rejection or
a reworded acceptance carries a correction, and a correction is what there is a
principle to extract from. A rejection is the one verdict that spends, and it
spends once — the redraft that follows it is written here and adds nothing.

Two others sit next to the loop rather than inside it: the critique pass, which
proposes line edits over one essay at a time, and illustration rendering.

<details>
<summary><b>Under the hood — which model, and where it is set</b></summary>

Everything is in `authorlm/config.toml`, versioned with the code. Keys live in
`.env` beside it.

| Job | Model | Set in |
|---|---|---|
| Concept extraction, and the adjudication step that screens its candidates | `gemini/gemini-2.5-flash` | `[llm] model` — the default for anything without its own setting |
| Turning a verdict's reason into a candidate rule (`record_review` → the belief distiller, on every explained rejection and every explained reword) | `gemini/gemini-2.5-flash` | same default |
| Episode analysis, guidance | `gemini/gemini-2.5-flash` | same default |
| Essay summaries | `openai/gpt-5.6-luna` | `[critique] summarizer_model` |
| The critique editor pass | `gemini/gemini-2.5-flash` | same default |
| Illustration rendering | `openai/gpt-image-2`, 1536×1024 | `[illustrations] model`, `[illustrations] image_size` |
| **Drafting the beats** | none — the conversation drafts them | nothing set, deliberately; `[writing] model` would turn the billed mode on |

**Nothing in the shipped file names an Anthropic model.** That is your ruling of
2026-08-30, after the first bill: the `[writing]` section and the two
`[critique]` Sonnet overrides were deleted, and with them the last paths that
could bill the Anthropic API. Both deletions leave their **restore recipe in the
file's own comments**, so turning either back on is one edit in one place that
shows up in a diff. A test also fails if an `anthropic/` model reappears in any
model setting — updating that test is part of turning spending back on, which is
what stops it happening by accident.

**The summarizer is the one job with its own model, and it is cheaper than the
default, not dearer.** GPT-5.6 Luna reads compressed context into editorial
judgment for about **$0.26 per full rebuild of the book, against roughly $0.49**
on the `[llm]` default — so the setting that exists to buy quality also happens
to save money on the most repeated call in the system. The `summarizer_model`
line is written into `authorlm/config.toml` but **commented out** while a
temperature fix lands in the model client; until then the summaries run on the
`[llm]` default above, at the higher of the two prices. Uncommenting one line
switches them over. The critique editor has no override at all and stays on the
default.

**If you restore `[writing]`.** `write draft` without `--dry-run` then sends the
assembled payload to `anthropic/claude-fable-5`, which is the largest single call
the system can make and would be the bulk of a chapter's bill — a rejected beat
pays for it again. `max_tokens`, `effort`, `timeout_seconds` and `cache` sit
beside the model; caching the stable half of the payload roughly halves the bill,
and the usage line after every draft says whether the cache is working
(`cache = false` turns it off for bisecting, at roughly three times the input
cost per chapter). It needs `ANTHROPIC_API_KEY` in `.env`.

`[writing]` deliberately does **not** fall back to `[llm] model`. Without the
section the billed verb refuses and prints the lines to paste, rather than
quietly writing your prose on the cheap default — a register you would blame on
the loop rather than on a line of configuration you never wrote. That refusal is
now doing a second job as the spending switch. `--dry-run` is **not** gated on
it: assembling and printing the payload costs nothing, so it works whether or not
the billed mode is on, and it is what the conversational drafting reads.

Without any model configured at all, the summary rebuild fails loudly rather
than quietly degrading — see §11's fourth entry.
</details>

---

## 11. Rough edges, stated plainly

Things that are true today and that you will meet:

1. **Every command needs the manuscript name** in this workspace, because it
   holds more than one manuscript. Worth knowing if you ever read the design
   documents: the simulated transcripts in `design-usecases.md` §5 leave it out
   and **would fail exactly as typed**. Nothing else in the tree says so.
2. **A lesson has to arrive on stdin.** Passing it as an argument silently drops
   it and then errors asking for stdin.
3. **The claim-in-the-plan rule is not enforced by code.** A beat described only
   as "transitional" is accepted without complaint. The guard is entirely your
   ratification, and this is the one place where slackness costs you a fabricated
   attribution.
4. **Refreshing the summaries is not automatic.** It is needed both before a
   start that the gate refuses and after every finish, and it costs one call per
   essay whose text moved (roughly twenty-four for the whole book). It **fails
   outright** with no model configured rather than skipping quietly — the only
   step that does, now that the beats are drafted here — see §10.
5. **Two candidate drafts per beat are not supported.** If you find yourself
   wishing for a second option, say so — that is the trigger for a deliberately
   deferred feature, and the count is the evidence for building it.
6. **The removal section is real prose.** It gets summarized, exported and pushed
   like anything else. Decide whether it ships.
7. **Replanning still resends the whole remaining plan.** In conversation that is
   fine — you say "split beat 8 and drop beat 10" and it is rebuilt. Typed by
   hand it is unpleasant, but that is not the primary path.
8. **Two parallel rewrites each see the other's old version** (§7). Told, not
   fixed — fixing it would mean drafting against beats you have not ruled on yet.

Entries that used to live on this list and are now fixed, described above
instead: an open writeup announces itself in the briefing (§8), replanning
refuses rather than orphaning a pending draft (§3.8), and a mid-rewrite essay
leaves a visible explanation in place of a blank file (§3.2).

---

<details>
<summary><b>Command reference (verified against argparse)</b></summary>

All take `-m <manuscript>` and an optional `--writeup <id-prefix>`.

```
authorlm write start <file> --intent <id>
        [--new] [--after <file>|start] [--style <guide>]        [brief on stdin]
authorlm write plan [--replace]                        [JSON array on stdin]
authorlm write status
authorlm write digest [--replace | --dispositions | --show]  [JSON on stdin]
authorlm write draft [--dry-run]                                   [no stdin]
authorlm write propose --why "<grounding>"                    [draft on stdin]
authorlm write accept [--reason "<why>"]            [reworded text on stdin]
authorlm write reject --reason "<verbatim why>"
authorlm write learn                                          [lesson on stdin]
authorlm write complete
authorlm write abandon
```

Surrounding verbs:

```
authorlm briefing
authorlm intent declare "<statement>" | intent complete <id-prefix> | intent list
authorlm summarize status | summarize rebuild [<file>] [--all]
authorlm style guides | style guide <name> | style attach <file> <guide>
authorlm doc list | doc push <file> | doc pull <file>
authorlm history list | history show v<N>
authorlm session start | session end
```

`write` is CLI-only — there are no MCP tools for the beat loop, by design. The
assistant runs these over Bash, with prose and JSON on stdin via heredocs. The
verbs are the state machine and the evidence channel: they gate, record and
collect. `write draft --dry-run` assembles and prints the payload the beat is
drafted against and makes no call; `write propose` registers the beat drafted in
the conversation against it. `write draft` without the flag is the opt-in billed
mode, and refuses while `[writing]` is absent — see §10.
</details>
