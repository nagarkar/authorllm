# Writing essays with the beat loop — a tutorial

*For the author. Covers two workflows: writing a new essay from an idea (UC-A)
and rewriting an existing essay by modeling the old one (UC-B). Every command
and every quoted refusal is verified against `authorlm/authorlm/cli.py`
(argparse) and `authorlm/authorlm/api.py` as they stand in this tree.*

---

## 0. How to read this

**The primary path is conversation.** You talk; the agent runs the verbs. You
should never hand-author JSON, never memorize a flag, never type a heredoc.
Every section below leads with *what you say* and *what you get back*, and then
shows the machinery in a collapsed **Under the hood** block so you can audit it,
run it yourself in a terminal, or check that the agent is not inventing
commands.

Two things about that division are load-bearing and are not negotiable:

1. **The verbs are the state machine and the evidence channel.** The agent does
   the drafting; the CLI does the gating and the recording. **No drafting verb
   makes an LLM call**: `write start`, `plan`, `propose`, `accept`, `reject` and
   `learn` are pure gating, recording and deterministic collection — so if a
   beat is drafted, the agent drafted it in the conversation, and no beat was
   ever written by something you could not see happening.
   One verb in the loop *does* spend. **`write complete` runs the
   concept-extraction pass that was deferred through the whole loop** (it builds
   an `LLMClient` and, when one is configured, calls it once); the per-beat
   collects deliberately skip extraction so that spend lands once, at the end,
   instead of on every accept. See §2.6 and §8.
2. **Nothing machine-written enters the manuscript without your accept.** The
   loop appends only inside `write accept`. The agent never edits the essay file
   during a writeup, and there is no auto-accept anywhere in the system.

**Workspace note — `-m` is not optional here.** This workspace holds more than
one manuscript, so **every** command needs `-m SMSTTD`. It is a global flag, so
it may sit anywhere on the line; this tutorial puts it last throughout. The
simulated transcripts in `design-usecases.md` §5 omit it and would fail as
typed. The agent adds it; if you are typing yourself, add it.

---

## 1. Before you start — five prerequisites

You do not check these yourself. The agent does, and the loop refuses if any is
missing. They are listed so the refusals make sense when they arrive.

| Prerequisite | Why it exists | What checks it |
|---|---|---|
| An **active intent** | The writeup binds to it, so the beats land inside an episode instead of the unattributed gap between sessions | `write start --intent` |
| A **style guide** on the target file | The rendered effective guide *is* the drafting law | `write start` (or `--style` for a new file) |
| **Fresh summaries** across the whole before/after context | Those summaries are the drafting context; a stale one is a lie about another essay's text | `write start`, via `passes.summaries_ready` |
| The file **not checked out** to Google Docs | While checked out the Doc is the working copy; a local append would be silently discarded by the next pull | `write start`, `write propose`, `write accept` |
| **No other active writeup** on the file | One loop per file | `write start` |

There is **no `--force`** on the freshness gate and none on the checkout gate.
That is deliberate: you cannot consent past a lie about the text.

A sixth, softer one: if the current session was opened on a previous day, the
agent should offer once to close it and start fresh. Policy evidence only counts
once per session, so a week-long session structurally caps what the loop can
learn from your verdicts.

<details>
<summary><b>Under the hood — checking the prerequisites yourself</b></summary>

```bash
authorlm summarize status -m SMSTTD     # free, no LLM: fresh / stale / missing per essay
authorlm style guides -m SMSTTD         # guide names and per-file attachments
authorlm intent list -m SMSTTD          # active intents
authorlm doc list -m SMSTTD             # link + checkout status
authorlm write status -m SMSTTD         # is a writeup already open?
```
</details>

---

## 2. UC-A — write a new essay

### Step 1 — say what you want, and settle the brief

> **You:** "I want a short essay on Weil's attention, sitting after the Becker
> essay in Connections."

The agent reads the concept graph, the effective style guide and the drafting
context, and comes back with a **candidate one-paragraph brief**, the placement
it inferred, and the style guide it proposes:

> **Agent:** "After `becker.md`, so `'Connections essays'` — that's what
> `becker.md` itself uses. Here's a draft brief: …"

Both of those are proposals you say yes or no to, not questions you have to
answer from memory. The guide is never chosen silently — the attachment is
drafting law, so it is named out loud and you agree to it — but you should never
have to go and look it up.

Then you argue with the brief's wording until it is yours.

This paragraph matters more than it looks. A brand-new file has no pinned raw
material — no old text to model. The brief is the *only* essay-specific ground
every beat is allowed to stand on. It is stored verbatim on the writeup and
reprinted on every resume.

> **You:** "Closer. Cut 'faculty' — say 'attention is what makes the hard floor
> visible'. Otherwise yes."

There is no `write brief` verb. The conversation *is* the ratification.

### Step 2 — the agent starts the writeup, and shows you the frame

The agent declares the intent, then starts. What comes back has three parts:

- a line confirming the file was created empty with the guide attached, and —
  read this one — **`Pinned v418 as the pre-writeup state (0 chars) — abandon
  deletes weil.md.`**
- **BRIEF — the author's, verbatim** — your paragraph, read back to you.
- **DRAFTING CONTEXT** — a `BEFORE` block (every settled essay ahead of your
  placement, compressed; their concepts are *available* and must not be
  re-introduced) and an `AFTER` block (upcoming essays; their concepts are *not*
  available — a beat that needs one must forward-reference it).

**Read any line starting `!!`.** They are printed in yellow and they are the
only warnings in this output:

- `!! summary STALE / MISSING / DEPRECATED` — that entry is not trustworthy.
  (At start this cannot happen; the gate refused it. It *can* appear later, on
  resume — see §6.)
- `!! coverage INCOMPLETE: ¶14 uncited` — that essay's summary never cited some
  of its own paragraphs. Informational, not a block: treat that neighbour's
  context as possibly short a move.

<details>
<summary><b>Under the hood</b></summary>

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

- `--new` — because name resolution is substring-based; without the flag a typo
  would silently become a new essay. `--new` on a name that already resolves is
  refused.
- `--after <file>` or `--after start` — a file that does not exist has no
  position to infer. Without it, an unlisted file would sort to the *end* of the
  reading order and the entire book would read as settled context behind it.
- `--style "<guide>"` — `style attach` cannot run before the file exists, so the
  guide is named here and validated before anything is created. Forget it and
  the refusal now lists the guides on record *and* names the guide `--after`'s
  anchor uses, so the lookup happens in the message rather than in another
  command.
- **the brief on stdin** — required with `--new`.

Every gate runs before any write. A blocked start leaves the disk byte-identical.
</details>

### Step 3 — ratify the beat plan

The agent expands the brief into leaf beats and shows them to you. This is the
single most important thing you do in the whole loop.

**Plan ratification is the fabrication guard.** A beat spec's notes must state
*the claim the beat will make*, not its rhetorical function. This is not
pedantry — it is the fix for a measured failure. In the §12.5 blind-redraft
experiment the model had the style guide, the concept slice and the full
before/after context, was asked for 2,244 words in one shot, and invented an
attribution to Rieff, recording in its own notes: *"I do not know the real
essay's actual argument, so I built one."* The gap between "a claim is needed
here" and "I have no claim here" got closed silently, at drafting time. The plan
moves that decision upstream, where you can see it.

So refuse a beat that says `"notes": "transitional"`. Accept one that says:

> `{"role": "development", "concepts": ["Hard floor", "Suffering"], "budget":
> 220, "notes": "claims affliction is what remains when another's kindness
> removes the floor; builds on kindness.md, cites nothing new"}`

**Every proper name, citation or quotation a beat will use is named in the spec
before it appears in prose.** If it is not in the ratified plan, it does not
belong in the draft.

The agent will show the beats back as a numbered list, not as JSON. You reply in
words: "beat 2 is doing two jobs, split it", "drop the Weil biography line".

<details>
<summary><b>Under the hood</b></summary>

```bash
authorlm write plan -m SMSTTD < /tmp/weil-plan.json
# → Plan ratified: 3 beat(s) ahead.
#     • [n=1 | opener | concepts: Attention | budget ~140]: claims attention is …
```
Amend later with `--replace`: written beats are kept, the remainder is replaced,
and replacement beats get fresh `n`s automatically so recorded verdicts cannot
be corrupted. Running `write plan` again *without* `--replace` is refused with a
message naming the flag. See §3.7 for what `--replace` now insists on first.

Note: the CLI validates that the plan is a non-empty array of objects. It does
**not** check that `notes` states a claim — it cannot. That guard is yours.
</details>

### Step 4 — the beat loop

Per beat, twice:

**The agent proposes.** You are shown the draft *with its grounding* — which
concepts it realizes, which precedent it follows, and (in a rewrite) which
digest points it carries, by id. That explanation is mandatory at the tool
level; a draft without one cannot be registered.

**You give a verdict.** Three shapes, in plain words — see §4.

Then the next beat. Accept is the only thing that touches the file: it appends
the accepted text, collects the revision, records the verdict as evidence, and
advances the cursor, atomically.

<details>
<summary><b>Under the hood</b></summary>

```bash
authorlm write propose --why "realizes Attention; opener per the plan; grounded \
    in the brief and kindness.md's hard floor — no source cited beyond Weil's \
    name" -m SMSTTD < /tmp/beat1.md
# → Beat [n=1 | opener | concepts: Attention | budget ~140]: …
#   Draft registered [gd-77b0e4c1] — author verdict: accept / accept with
#   reworded stdin / reject --reason.

authorlm write accept -m SMSTTD                        # as-is
authorlm write accept -m SMSTTD < /tmp/reworded.md     # your wording wins
authorlm write reject --reason "…" -m SMSTTD           # reason required
```
A redraft supersedes the pending proposal — you never accumulate two live drafts
for one beat.
</details>

### Step 5 — learnings (occasional)

When a pattern recurs *across* verdicts — not on every verdict — the agent
distills one line and records it. It rides forward in the drafting context and
`write status` reprints it. Learnings are session-local scaffolding, not memory:
they never write to the style or policy stores. Recurring ones get promoted
through the ordinary ratification path at episode analysis.

You should see this surfaced to you unprompted when it happens ("you have
stripped an unnamed source twice now — shall I stop proposing them?"), not
discovered later.

<details>
<summary><b>Under the hood</b></summary>

`write learn` is **stdin only**. `authorlm write learn "lesson"` silently drops
the argument and then errors for want of stdin — a known footgun.

```bash
authorlm write learn -m SMSTTD <<'EOF'
Author strips external sources the plan did not name — propose bare claims.
EOF
```
</details>

### Step 6 — complete

> **You:** "That's the essay."

That sentence is the consent, and the agent should act on it rather than ask you
again. It runs completion, the rebuild and the intent close in sequence and
reports back in a couple of sentences; it *offers* `session end` rather than
requiring it.

Completion closes the writeup, runs the concept-extraction pass that was
deferred during the loop, and — for a created file — **inserts the toc.toml
stanza** at your declared placement, carrying the anchor's `parent`. Without
that stanza the essay is finished, on disk, and structurally invisible: it can
never be summarized in position, and the next `critique run` or `write start`
near it is refused for a file you believe is done.

If the anchor cannot be found, completion prints the exact stanza and does not
fail. Paste it (or have the agent paste it) — do not skip it.

Completion also reminds you that the summary is now stale. It deliberately does
not rebuild: `rebuild_one` marks everything downstream `upstream_stale`, a side
effect completion was not asked for. The three commands that follow are the
agent's to run, not yours to remember:

```bash
authorlm write complete -m SMSTTD
authorlm summarize rebuild -m SMSTTD          # ~1 cheap-tier call per stale unit
authorlm intent complete di-4b7e1 -m SMSTTD   # separate — runs episode analysis
```

Two of those three spend real model calls, and both are worth hearing about
aloud: `write complete` runs the deferred concept-extraction pass (one call,
when an LLM is configured — this is the only LLM call anywhere in the beat
loop), and `summarize rebuild` costs roughly one cheap-tier call per stale unit.
`intent complete` runs episode analysis, which spends too. If you are working
with the LLM disabled, completion still succeeds; the extraction is simply
skipped.

`write complete` does **not** require the plan to be exhausted. You decide when
it is done; unwritten beats are reported, not blocked.

---

## 3. UC-B — rewrite an essay by modeling the old one

Everything in §2 applies. Three things are added: a **digest** of the original,
**dispositions** on every point in it, and a **What Was Removed and Why** section.

### Step 1 — start (and say what to retain)

> **You:** "The Epictetus essay needs a rework. Keep the prohairesis/Dharma
> comparison and the divergence on the scope of choice. Move the biography down
> — open on the claim, not the man. Cut anything discernment.md already owns."

That is the brief. In a rewrite it is optional, and this is exactly where it
earns its keep: "retain these points, reorder for flow" belongs here and nowhere
else.

**What `write start` does to the file: it truncates it.** The old essay is not
lost — it is pinned as `source_version_id` before the truncation, and it is the
only copy the loop will read. From this moment the file on disk is empty and
grows one accepted beat at a time.

A second safety net costs one command and is worth taking, because
`authorlm/manuscripts/` is gitignored — there is no git history for the prose:

```bash
cp authorlm/manuscripts/SMSTTD/epictetus.md \
   authorlm/manuscripts/SMSTTD/_drafts/epictetus.pre-rework.md
```

`_drafts/` is invisible to observation (directories starting with `_` are
skipped), so the copy costs the concept graph nothing.

<details>
<summary><b>Under the hood</b></summary>

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

### Step 2 — the digest, and your review of it

The agent reads the pinned original back and extracts four lists:

- **points** (`p1`, `p2`, …) — every claim the original made
- **examples** (`x1`, …) — with the point ids each serves
- **references** (`r1`, …) — epigraphs, citations, attributions
- **inconsistencies** (`i1`, …) — each naming the TOC-**earlier** essay it
  contradicts

**You will never be shown the JSON.** The agent reads the digest back to you as
a numbered prose reading, in the original's own order, one sentence per point,
with each point's example or reference attached to it rather than listed
separately. Then — and only then — it puts the two or three it is least sure
about, plus every claimed inconsistency, to you explicitly by number. You
correct it in words, by number; the agent re-composes the payload.

That order is deliberate. **You correct the digest before it is persisted**, and
that review is the entire reason the digest counts as evidence rather than a
machine reading — the same principle as plan ratification. Shown as eighteen
lines of data it gets rubber-stamped, which produces exactly the machine reading
the review exists to prevent. Expect to move things: a "point" that was really
two, an "inconsistency" that is not one.

Once persisted, the digest becomes law in a specific and useful way:

> **The digest is the sole authority on what the original said.** A point not in
> the digest is not in the original.

This has a sharp consequence. During a rewrite, the compressed summaries of
*neighbouring* essays can still describe the **old** version of the essay you
are rewriting — this was observed in the §12.5 runs. Under this rule that leak
is inert: the agent may not "retain" a point on the strength of a neighbour's
description of the essay currently being rewritten.

Reordering, compressing, merging and sharpening the digest's points is the
*purpose* of a modeled rewrite and is fully permitted. Adding a point, example
or citation that is in neither the digest nor the brief is a new authorial
commitment — it comes to you, at plan time.

<details>
<summary><b>Under the hood — four shapes, one verb</b></summary>

```bash
authorlm write digest -m SMSTTD                      # prints the pinned original
authorlm write digest -m SMSTTD < digest.json        # persists (refused if one exists)
authorlm write digest --replace -m SMSTTD < d.json   # replaces
authorlm write digest --dispositions -m SMSTTD < d.json
authorlm write digest --show -m SMSTTD               # stored digest + tally
```
Ids must be unique across the *whole* digest, and cross-references (`serves`,
`points`) must resolve to real point ids; `inconsistencies[].with` must name a
real manuscript file. A dangling reference is refused rather than stored —
an accounting built on dangling ids is decorative. Validation is all-or-nothing.

`--replace` may not drop an id that already carries a disposition; it names the
offenders and refuses.

`write digest` is refused outright on a writeup that created its file: "there is
no source essay to digest."
</details>

### Step 3 — plan, with point ids

Same ratification as UC-A, plus: **each beat names the digest point ids it
carries**, and the plan's *ordering* is where "reorder for better flow"
actually happens. Order any footnotes beat **before** the removal beat — the
loop appends, so the last beat lands last, and that is all "after the footnotes"
means here.

### Step 4 — beats, then account for what happened

Run the beat loop exactly as in §2.4. After each stretch of beats, the digest's
points have to be accounted for. There are two values and only two:

- **kept** — optionally with the beat number that carried it. **You are not
  asked for these.** The ratified plan already names the point ids per beat, the
  `--why` on each proposal repeats them by id, and accept records which beat
  landed — so the agent derives the whole `kept` half and records it. It reports
  the result in one line ("p1, p3, p7 kept across beats 2–4") so you can
  contradict it, and that is the only attention it should cost you.
- **removed** — **a reason is required**, in your words, and this is the half
  that actually comes to you. Same principle as `write reject --reason`: an
  unexplained removal teaches nothing and cannot be written up. The nuance goes
  in the reason, because the reason is what the removal section will quote.

There is no third value. "Moved", "deferred", "merged into p3" all belong in the
reason text.

Changing your mind is allowed and is *reported*, not hidden:
`p4: removed → kept (overwritten)`.

<details>
<summary><b>Under the hood</b></summary>

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

### Step 5 — the What Was Removed and Why section

The final beat (or two) is the section itself, headed
`## **What Was Removed and Why**`.

It is **drafted from the recorded reasons** — the agent writes only the
connective prose. The judgments were made when they were recorded; they are not
re-invented at drafting time. It is proposed and accepted like any other beat,
so it is evidence like any other beat.

It is ordinary manuscript prose: it will be collected, scanned for concept
realizations, summarized, pushed to Docs and exported. If you later decide it
should not ship, delete it — the durable record of the accounting is the writeup
metadata, not the prose.

### Step 6 — complete, and the accounting report

```
Removal accounting: 5 point(s) kept, 2 removed, 2 UNACCOUNTED.
  UNACCOUNTED — no disposition recorded, and no 'What Was Removed and Why'
  entry can be trusted while these are open:
    p8  "the Stoic's compatibilism draws the fence of freedom too narrowly"
    p9  "every choice ramifies outward; the scope is not confined to the interior"
  Record them: write digest --dispositions  (JSON on stdin).
```

**It warns; it does not block.** Completion succeeds with points unaccounted,
by design, and the reasoning is worth knowing because you may want to overturn
it:

- An unaccounted point is the same kind of open item as an unwritten beat, and
  `write complete` already refuses to require an exhausted plan.
- Blocking would reward the cheapest escape — a bare `kept` on a point that was
  in fact dropped — converting a visible gap into an invisible lie.
- The tally is not a one-shot: `write status` prints it on *every* resume. An
  author who ignores it at completion has ignored it repeatedly.

Completion also does **not** check that the removal section exists in the file.
That would be content lint, and `write propose` deliberately carries none so the
auditors have one home.

### Step 7 — changing the plan mid-rewrite

Say beat 7's draft is wrong in a way that means the *plan* is wrong, not the
prose. The remedy is `write plan --replace`, which keeps the beats already
written and renumbers the rest — replacement beats get fresh `n`s, which is
precisely what makes it impossible for a replan to corrupt a recorded verdict.
Accepted text is never at risk.

**Settle the pending draft first.** `write plan --replace` refuses while a draft
awaits your verdict:

```
a draft is still awaiting your verdict on beat n=7 — replacing the plan would
strand it as 'proposed' forever and lose the reason you are replanning for.
Settle it first: 'write reject --reason "<why the plan is wrong>"' (that reason
IS the evidence for the replan), or 'write accept' to keep the draft.
```

Almost always the right exit is the reject, because the sentence you would type
into `--reason` is the reason you are replanning at all — and that is the single
most valuable thing the loop can record. The refusal exists because the old
behaviour silently orphaned that draft: it vanished from `write status`, counted
as `proposed` in every tally forever, and took your reason with it.

---

## 4. The verdict vocabulary

Three shapes. Say them in plain words; the agent maps them.

| You say | Recorded as | What happens |
|---|---|---|
| "yes, that's it" | `accepted` | draft appended verbatim, collected, cursor advances |
| "almost — make it *this*" (you give the text) | `modified` | **your** text is appended; the draft→final diff is kept as evidence |
| "no, because …" | `rejected` | nothing is appended, the cursor does **not** move, your reason is stored verbatim, the agent redrafts with it in context |

**Why the reason on a rejection matters.** It is the highest-value evidence the
system can receive. A bare "no" teaches nothing; "you cited *Gravity and Grace*
— the plan said no citation beyond her name and I meant it" teaches a rule, and
you will see it come back as a seeded candidate belief:

```
Beat n=1 rejected — reason recorded verbatim. Redraft with the reason in context.
Seeded candidate belief: Do not introduce a source the ratified plan did not name.
```

A candidate belief is not a rule yet. It becomes one only on independent
evidence from distinct sessions — a machine-authored conjecture can seed a
candidate but can never validate one.

`--reason` is required at the tool level on reject. On accept it is optional but
welcome: an explained *acceptance* is evidence too. If you accept bare, the
agent is instructed to ask once — gently, once, never during bulk work — whether
there was anything behind it. "Just record it" ends the asking.

**A reword is not a lesser accept.** It is the richest signal in the loop: the
diff between what was proposed and what you wrote is exactly the shape of the
correction. Reword freely.

---

## 5. Gate refusals — what each one means

Every refusal names its remedy. None of them writes anything; a blocked start
leaves the disk byte-identical.

| Refusal (abridged) | What to do |
|---|---|
| `weil.md does not exist yet, so it has no place in the reading order — say where it goes: --after <file it follows> \| --after start` | Say where in the book it goes. `--after start` opens the book. |
| `weil.md has no attached style guide … With --new, name it: --style <guide>.` — followed by `Guides on record: …` and, when `--after` names an anchor with an attachment, `becker.md uses 'Connections essays' — likely the one you want.` | Pick from the names in the message. Nothing is ever defaulted for you: the guide is drafting law, so attaching it stays your explicit act. |
| `no style guide named 'Nope'` | Typo, or the guide does not exist yet (`style guide <name>` creates one). |
| `becker.md already exists — drop --new to rewrite it` | You meant a rewrite, or you fat-fingered the name. |
| `--style is only for --new; an existing file's guide is attached with 'style attach becker.md …'` | One way to do each thing. |
| `write start --new requires the one-paragraph brief on stdin` | Compose the brief. This gate fires **last** among the flag gates on purpose: every other refusal names a flag you can append to the command you just typed, but this one sends you away to write a paragraph, and it would otherwise mask all the others. |
| `missing/stale/deprecated summaries: … — run 'summarize rebuild' first (the drafting pass will not read a lie about the text)` | Run `summarize rebuild`. No `--force` exists. |
| `epictetus.md is checked out to Google Docs — the Doc is the working copy. Run 'doc pull epictetus.md' first.` | Pull first. Fires on start, propose and accept. |
| `writeup wu-… is already active on epictetus.md` | Resume it (`write status`) or abandon it. |
| `a plan exists — pass --replace to amend the remaining (unwritten) beats` | You planned twice. |
| `a draft is still awaiting your verdict on beat n=7 — … Settle it first: 'write reject --reason "…"' … or 'write accept' …` | Reject the pending draft with the reason that made you replan (see §3.7), or accept it. |
| `no ratified beat plan — set one: write plan (JSON on stdin)` | You proposed before planning. |
| `nothing proposed for beat 3 — write propose first` | You gave a verdict with no draft pending. |
| `--why is required: which concepts this draft realizes, which precedent it follows` | Verdict evidence hangs off it. |
| `--reason is required: the author's why, verbatim` | On reject, always. |
| `a digest is already recorded for this writeup — pass --replace` | And `--replace` will still refuse to drop ids that carry dispositions. |
| `this writeup created weil.md; there is no source essay to digest.` | Digests model rewrites only. |
| `all planned beats are done — write complete (or extend the plan: write plan --replace)` | Either finish or plan more. |

**Rough edge, honestly.** Typing `write start --new` yourself at a terminal, you
can hit four of these one at a time — placement, then style, then guide-exists,
then brief. The ordering is deliberate (see the brief row above) but the
experience is a parade. In conversation the agent should pre-check all four
before running anything, so you see at most one round trip.

---

## 6. Resume after an interruption

**The briefing tells you a writeup is open.** Since an open writeup means the
essay is *truncated on disk and half-rebuilt*, that is manuscript state, not
knowledge, and it is printed first and in yellow:

```
Open writeups — these files are truncated on disk and rebuilding one accepted
beat at a time:
  • epictetus.md — beat 4 of 9 · a draft awaits your verdict [wu-55ce0 · intent di-b814d]
  → resume: write status   ·   restore the old text: write abandon
```

It reaches every surface that serves the briefing: `authorlm briefing`, the
session-open briefing, and the MCP `get_briefing` the agent calls when a
conversation opens. So the ordinary act of coming back — open a conversation,
get the briefing — is now enough to learn that `epictetus.md` is mid-surgery.
You no longer have to know to ask.

**`write status` is still the resume entry point.** One command reprints
everything the loop knows:

- which writeup, which file, `beat 4/6`, and the current beat's full spec
- whether a proposal is **pending a verdict** (with its id)
- the verdict tallies and every recorded learning
- your **brief**, verbatim
- for a rewrite: the digest counts and the running **accounting** tally —
  yellow whenever anything is UNACCOUNTED
- the full **DRAFTING CONTEXT**, recomputed live

That last point matters after a long gap. The context is not stored — it is
rebuilt from the current summaries on every read, so if a neighbouring essay
changed while you were away, the resume view says so with a yellow
`!! summary STALE` line rather than silently serving you the lie the start gate
had refused. **Read the `!!` lines before accepting anything on resume.**
`write status` cannot refuse — it is the resume entry point and must keep
working — so it warns loudly instead.

One thing `write status` will not do for you: **a pending proposal survives
indefinitely, and accepting it after a long gap accepts a draft conditioned on
the world as it was.** If the context moved, have the agent re-propose rather
than accepting the stale draft. (The rule is explicit for Google Docs round
trips; it applies just as well to a week of elapsed time.)

If the file has left the disk entirely, `write status` degrades rather than
dying: it replaces the context with a one-line note and still shows the plan,
cursor, pending proposal and tallies.

<details>
<summary><b>Under the hood</b></summary>

```bash
authorlm briefing -m SMSTTD
authorlm write status -m SMSTTD
authorlm write status --writeup wu-3d80b -m SMSTTD    # a specific writeup
authorlm write digest --show -m SMSTTD                # re-read the stored digest
authorlm write digest -m SMSTTD                       # re-read the pinned original
```
With several writeups open, the `write` verbs refuse ambiguously (`multiple
active writeups (a.md, b.md) — pass --writeup`) rather than guessing. The
briefing lists them all.
</details>

---

## 7. Bailing out

`authorlm write abandon` ends the writeup. What it does depends on which flow
you are in, and the difference is severe enough to be worth stating plainly.

**Rewrite (UC-B, or any writeup on a file that already existed):** the file is
restored from the pinned source version. The old essay comes back exactly as it
was.

```
Writeup [wu-3d80b6e2] abandoned; file restored from the pinned source version.
```

**New essay (UC-A):** the restore target is *nonexistence*, so **the file is
deleted**, and the style attachment the writeup created is removed with it.

```
Writeup [wu-9c2f4a1b] abandoned; weil.md deleted — the writeup created it, so
the restore target is nonexistence.
Its text is preserved in v421 (nothing typed into it was lost).
```

**Nothing you wrote is destroyed.** Abandon takes a snapshot *first* — that
collect is the recovery point, and it captures even half-typed text you never
collected. The version number in the second line is where the prose lives. The
version history then honestly reads create → beats → remove: the prose is
unpublished, not erased.

The agent is instructed to tell you the file will be deleted before running it.
There is no confirmation prompt in the CLI itself, so if you are typing it
yourself, be sure which flow you are in.

Expect some honest noise afterwards: any concept whose primary location was the
deleted file raises a `vanished` proposal. That is the same noise a
truncate-and-rebuild produces, and it wants triaging, not fixing.

<details>
<summary><b>Under the hood — recovering the text</b></summary>

```bash
authorlm write abandon -m SMSTTD
authorlm history show v421 -m SMSTTD     # the version the abandon message named
```
</details>

---

## 8. Rough edges, stated plainly

Things that are true today and that you will meet:

1. **Every command needs `-m SMSTTD`** in this workspace, and none of the
   simulated transcripts in the design doc show it.
2. **`write learn "lesson"` silently ignores the argument** and then errors
   asking for stdin. The lesson must arrive on stdin.
3. **The claim-in-the-notes rule is not enforced by code.** `write plan` accepts
   `{"role": "development", "notes": "transitional"}` without complaint. The
   guard is entirely your ratification. This is the one place where slackness
   costs you a fabricated attribution.
4. **`summarize rebuild` costs cheap-tier LLM calls** (one per stale unit, ~24
   for `--all`) and is needed both before a start that the gate refuses and
   after every completion. It is not automatic.
5. **Finishing an essay spends, in three places.** The drafting verbs
   (`start`, `plan`, `propose`, `accept`, `reject`, `learn`) make no LLM call at
   all — but `write complete` runs the concept-extraction pass deferred through
   the whole loop, `summarize rebuild` costs the calls in item 4, and
   `intent complete` runs episode analysis. Each is skipped silently when no
   LLM is configured, so a run with the LLM off finishes but learns nothing from
   the prose. Only completion's own call is inside `authorlm write`.
6. **Two candidate drafts per beat are not supported.** If you find yourself
   wishing you could see a second option, note it — that is the trigger for a
   deliberately deferred feature, and the count is the evidence for building it.
7. **The removal section is real prose.** It gets summarized, exported and
   pushed like anything else. Decide whether it ships.
8. **`write plan --replace` still makes you resend the whole remaining plan** as
   JSON. In conversation that is fine — you say "split beat 8 and drop beat 10"
   and the agent rebuilds it. Typed by hand it is unpleasant, but that is not
   the primary path.

Two entries that used to live on this list are now fixed, and are described
above instead: an open writeup is announced by the briefing (§6), and
`write plan --replace` refuses rather than orphaning a pending draft (§3.7).

---

## 9. Command reference (verified against argparse)

All take `-m <manuscript>` and an optional `--writeup <id-prefix>`.

```
authorlm write start <file> --intent <id>
        [--new] [--after <file>|start] [--style <guide>]        [brief on stdin]
authorlm write plan [--replace]                        [JSON array on stdin]
authorlm write status
authorlm write digest [--replace | --dispositions | --show]  [JSON on stdin]
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
agent runs these over Bash, with prose and JSON on stdin via heredocs.
