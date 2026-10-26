---
name: papers-philosophy
description: The philosophy field skill for the papers core, used with it. Adds persona referees (named thinkers arguing from their documented positions), fidelity checks against the primary text, web-verified citations, objections tagged answer or survive, and repairs staged as green <<old>>{{new}} forms on the Doc road. Covers journal papers in philosophy and commentaries on the book's own text (a recap, a chapter). Use when the author asks for referees, objections or a critical review of a philosophy section or commentary; load `papers` first.
---

# papers-philosophy: what philosophy adds to the core

Everything in `papers/SKILL.md` applies. This file lists only the differences. It was first run on SMSTTD's Sermon One recap (2026-10-18), three rounds, referees Leibniz, Spinoza and Nāgārjuna.

## Two kinds of text

| kind | the text | stages used | where repairs go |
|---|---|---|---|
| **paper** | a journal paper in philosophy | all of the core's | the paper's tab, as for physics |
| **commentary** | a chapter of the book itself (a recap, an essay) | B to G; no A, C2 or H | the chapter's own tab, as green forms (below) |

A commentary is judged against two sources at once: the primary text it comments on (for a recap, the sermon) and the book's formal account (the Metaphysic). The primary text decides what the commentary may attribute to it; the formal account is binding as the book's position, so a referee may object to it, but the commentary is not at fault for agreeing with it.

## Stages the core leaves to the field

| core stage | in philosophy |
|---|---|
| **C3. Field outline** | the argument's steps in order, each with the premise it rests on, and the positions it engages (by thinker and text), before any prose |
| **D. Field preparation** | citation verification: every work cited exists with the stated title, date, venue, edition, translator and section, and says what it is cited for. Each is VERIFIED, WRONG (with the correct detail and a source) or UNVERIFIABLE. An UNVERIFIABLE claim stays out of the text, or goes to the author as a question; it is never cited on trust. The author does not want Marx cited or used as a comparison in this book (2026-10-25); for "a product of man that comes to own him" reach for Jung's persona, Fromm's idolatry, Sartre's bad faith or Becker's hero-systems. |
| **E. Body** | one argument at a time; the strongest objection to it is stated at full force before the author's gate |

## Referees in use

**Personas.** A named thinker argues from their documented positions, with section, letter or verse references, as a serious journal referee would. They write in modern prose (no pastiche, no "thee"); where today's scholarship says the figure was wrong, the referee may note it in a bracket, and the objection still stands as theirs.

Picking them (core section 2): the author names some; propose the rest, each with one reason. A good persona attacks the text from a side the others leave open. The first run: the author named Leibniz and Spinoza, two Western rationalists of substance and sufficient reason. Nāgārjuna was proposed because the recap's ground is Nothing, in an Indic setting, and he attacks any ground at all, Nothing included, and could check the Buddhist and Vedāntic sources. Hegel was the runner-up (Being, Nothing, Becoming).

Give each persona a **citation slice**: the footnotes and attributions nearest their own field, so verification is split and nothing is checked twice.

Optional roles beside the personas: a line editor (house vocabulary, the same claim stated alike everywhere) and a fidelity reader (only the primary text against the commentary).

## The brief

One file in the session scratchpad, shared by every referee in the round, plus a few lines of persona per agent. It names, in reading order, the only files the referee may read: the primary text (for a recap, the sermon excerpt), the formal account's excerpt, and the text under review, with line numbers and the scope ("only from line 5 on"). Web search is allowed, for verification only.

Three duties, in this order of weight:

1. **Verify.** Fidelity: each claim the text attributes to the primary text, quoted beside the line it claims (UNFAITHFUL when it adds, softens or distorts). Citations in the slice (stage D). Internal consistency: a term used before it is defined, two sentences that contradict, a claim the formal account contradicts.
2. **Object.** The strongest objections the persona would raise, each tagged **answer** (the text owes a sentence or a footnote) or **survive** (the book's position, held on purpose). The referee does not ask the book to adopt their philosophy.
3. **Repair.** For each answer-tagged objection or error: target line, `OLD:` a short verbatim span, `NEW:` its replacement, `WHY:` one line. Grounded only in the primary text, the formal account or a verified source; never an invented quotation, citation or position for the author. Minimal, in the text's own register, under the house style (no sentence opening with "What", no "this, not that" contrasts, terms of art capitalized).

Report format: Verdict (accept / minor / major, plus the single most valuable change), a table of citation and fidelity checks, numbered objections with the quoted text, numbered repairs. 1,200 to 2,500 words in round 1; 800 to 1,500 after.

Every brief quotes the commentary rule from the adjudication section below (no restating the primary text) so repairs arrive right the first time. **Round 2 and later** add to the brief the referee's own previous report and the author's rulings, verbatim and binding: a referee may say a ruling was carried out badly, never that it was wrong. The report then carries a ledger of its previous items (MET / PARTLY / NOT / WITHDRAWN under a ruling), the new problems the revision introduced (new citations included), one remaining strongest objection, and few repairs: later rounds are for convergence, not a second rewrite.

## The parent's adjudication

The referees advise; the session decides what reaches the author, and checks before it stages:

- **Merge** repairs that touch the same span into one; where referees disagree, pick and say why in the form's note.
- **Check the cross-references** a repair leans on (the formal account's line, an essay's name) in the files, not from the report.
- **Re-verify** any citation a referee was unsure of, or that two referees read differently. If neither can verify it, it stays out (the first run: a Jung quotation in the text that no one could find in the cited book was replaced with what the book does say; a claimed link between two modern authors, from the author's dictation, was left out and the author was told).
- **Leave survive-tagged objections out** of the forms, and list them for the author in one line each.
- **A known-wrong claim is fixed in the text**, never qualified in a footnote; footnotes carry sources, etymologies and comparisons only.
- **A commentary adds a perspective; it never restates the primary text.** The author's rule (2026-10-25, after a round that "coverage"-repaired a recap by pasting the sermon's own lines and images back in): "you're repeating the same language from the sermons. This is not appropriate. You should be placing a new perspective." So a referee's "the sermon says X, the recap says Y" is a reason to make Y compatible with X, never to replace Y with X's words; a coverage gap is filled with the commentary's own reading of the missed move, in the commentary's register; a quotation of the primary text appears only where the commentary is about those words. Fidelity means compatibility, not echo. Screen every form for primary-text paraphrase before the author sees the round, and rewrite or drop those. Mechanical repairs (citations, house punctuation, banned words) are unaffected.
- **Imagery** of the commentary's own (a throne, a costume, a stopped clock) is welcome where it is clear and idiomatic, and must not beg the question (author, 2026-10-25).

## Repairs on the Doc road (commentary)

The forms are the review: `<<old>>{{new}}`, old struck through, new in green, in the chapter's tab.

```bash
authorlm doc pull <file> -m <Manuscript>
authorlm revise stage <file> --origin referees-r<N> -m <Manuscript> < repairs.json
#   [{"old": "exact span", "new": "replacement", "note": "Referee rN: why"}]; old "" + anchor_paragraph adds a paragraph
authorlm revise resolve <file> --origin referees-r<N> -m <Manuscript>
```

- One origin per round. The note names the referee and the reason, so the author reads the case beside the change.
- Untouched = accepted, emptied green half = declined, reworded = the author's words win. "Resolve the rest yourself" means untouched forms land as accepted.
- **A tab with forms out takes no new forms, its own producer's included.** Before restaging: pull, confirm a clean round trip (the author has not touched the forms), withdraw the forms in place (no verb yet: `api.withdraw_revisions` then `gdocs.settle_forms_in_tab` with every form kept `old`; a `revise withdraw` verb is proposed), and stage again. Never rebuild a tab to clear forms: a rebuild orphans margin comments.
- A form from another session, already settled in the Doc but never resolved, blocks staging too; `revise resolve --origin <its origin>` clears it.
- After every resolve, check that math and footnote labels survived the round trip.

## Rulings in philosophy

The core's section 3 applies. Two patterns from the first run:

- **No victory the argument has not won.** The text states the opponent's strongest reply. It claims the reply defeated only when the book's argument actually defeats it, and when it does, it says how (the first run: a determinism that admits an unpredictable future "has given up the point it set out to defend").
- **The commentary may state the book's position where the primary text is silent.** The author's ruling: the sermons need not name compatibilism; it is not their role. A referee's "the sermon never says this" is a fidelity finding only when the commentary attributes the point to the sermon.

A ruling that fixes a word across the book (the first run: "ground" reserved for The Dharma, the Ground of Creation; Nothing as "the groundless ground" or "the first principle") becomes a house-style law, with `add_style_law` on the author's word, then a sweep staged as forms per tab. The uses the sweep leaves to the author's judgment go into the text as `[Explain: …]` tags carrying the suggestion.

## What the referees caught in the first run

A drafter alone had missed all of these: a book cited under a title that does not exist; a source cited for a claim it does not make; the wrong wheel named for Samsara; a quotation attributed to Jung that is not in the cited book; a footnote contradicting the sentence it hangs on; a footnote label lost in the round trip; a test stated broadly enough to convict the book's own first principle; two different things both called "the ground"; a theory of evil rejected in one paragraph and adopted two paragraphs later; and a reply to the determinist given at half strength.

## Publish road

A commentary: `revise resolve` (it pushes), then `authorlm export pdf --file <file> -m <Manuscript> --print-ready --upload` for a copy to share (`--upload` puts it on Drive and replaces it in place on every later export, so the link stays the same); without `--print-ready` the PDF carries the confidential-review notice, footer and watermark. A paper follows the core's stage H with the same export verbs.

When the author asks for the text "here, to read", paste the plain body in chat (no footnotes, markers or preamble); send a file only when asked for a file.

## Rulings: the link standard

- Every mention of a ruling in a message links inline to the document that records it: a markdown link whose target is the absolute path of the rulings file (for a recap, `<manuscript>/_drafts/<project>-rulings.md`, the scratch record of the core's section 7, frozen at close-out), or the full URL of its Doc tab, on the ruling's id. A ruling named without its link is a claim the owner cannot check. Standard for every repo skill: metalm `guidelines/requirements.md`, "Capturing owner input" (owner).
