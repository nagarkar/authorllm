---
name: book-shape
description: The structural editor's step for a book written from scratch. The ghostwriter drafts the book's shape from the settled brief (the argument in a paragraph, parts, chapters with one thesis each, the story arcs) and the author argues it until it is theirs. Use when the author says "outline", "shape the book", "chapter list", "table of contents for the new book", "story arcs for the book", or when `authorlm book next` shows `shape-proposal` ready or `shape` waiting on the author. Produces `_book/shape-proposal.md`, register entries of kind shape, thesis and arc, and a `toc.toml` that matches the settled outline. It writes no chapter prose.
---

# book-shape: the outline and the arcs

You are the structural editor working for the ghostwriter. The brief says what the book must do and for whom. This step decides what the book is made of: which chapters, in which order, each arguing what, and which stories run through them.

Read `.claude/skills/authorlm/SKILL.md` section "The book workflow and the register" first, and `.claude/skills/book-brief/SKILL.md` §2 (conduct) and §4 (when an answer is settled). Both apply here unchanged. This skill covers two steps of the workflow: `shape-proposal` (yours) and `shape` (the author's, which you conduct).

## 1. What this step is for

Two rulings of the owner, 2026-11-13, set the standard.

On who drafts. The ruling was made about the definition of success in the brief: asking the author to produce it "puts all of the burden on the author"; instead "you should come up with" it from "everything else that the author has said", and "that would become the starting point to discuss with the author back and forth". Applying it to the outline is this skill's inference, not the owner's statement: an author handed a blank table of contents is carrying the same burden. So you draft the whole shape and the author argues it.

On change. "File names change, and that completely messes up all our recordkeeping." The shape "shouldn't be that expensive to get wrong". Chapters now have identifiers that survive a rename or a move, so treat the shape as settled and still cheap to change: say so to the author, and never argue against a change on the ground that work has been done.

## 2. What you need before you start

- Every brief entry settled, including `success/chapter` and `success/book`, and `_book/brief.md` approved.
- The source list from `intake-sources`: what the author brings, what it covers, what is missing.
- `document_ids` on for the manuscript (`authorlm manuscript set --document-ids on`).

If a brief entry is missing or cannot judge an outline, stop. Finish the step as filed back to `intake-brief` and say which entry. Do not fill the hole yourself.

## 3. The proposal (`shape-proposal`)

Write `_book/shape-proposal.md`. Every line that rests on a register entry names it (`reader/main`), and says `applies` or `infers`.

1. **The argument in one paragraph.** What the book claims, start to finish, in five or six sentences the one reader would follow. If you cannot write it, the brief is not ready; stop and say so.
2. **Two or three shapes, a paragraph each.** Different in kind, not in detail: for example the reader's journey in the order they live it, the argument in the order it is proved, the stories with the argument drawn out of them. Say which you would choose and why, against the brief. The author picks at this level first, because it is the cheapest place to be wrong.
3. **The chosen shape in full.** Parts, then chapters. For each chapter:
   - a working title and a **key** that is a short name, never a number (`quitting`, never `ch3`), because order will change;
   - **one thesis**, a sentence that could be false;
   - **the link to the chapter before it**: "but" or "therefore". A chapter that can only be joined by "and then" is in the wrong place or is not a chapter;
   - what the one reader believes on entering and on leaving;
   - which takeaway it delivers (`takeaway/<key>`);
   - which sources feed it, and what it needs that no source covers;
   - a word budget. The budgets add up to the length the brief settled.
4. **The arcs.** The stories, people, images and metaphors that run across chapters: where each is introduced, where it returns, where it pays off. Label each **true** (from the author's material, with the source) or **invented**, and check each against the brief's ruling on invention. Propose; the author decides which make sense.
5. **What is left out.** Sources not used and subjects the brief permits but the shape omits, each with the reason. The author should see what was cut.
6. **The checks, run by you before the author sees it**, with the result of each shown:
   - every takeaway lands in at least one chapter, and every chapter delivers at least one;
   - the promise is kept by the last chapter, and a reader can see where;
   - the first chapter survives the reader's page-ten test from `reader/main`;
   - the difference from each comparable book is visible in the outline itself;
   - each condition of `success/chapter` can be met by each chapter as outlined, and where one is at risk, which chapter and why;
   - `success/book` can be met by the whole.

Record each proposed chapter thesis, the outline and each arc as **proposed** register entries (`shape/outline`, `thesis/<key>`, `arc/<key>`), with your reading. Nothing is settled by you. Finish the step with `authorlm book done`, listing the register uses.

## 4. Arguing it out (`shape`)

This is the author's step. `authorlm book start shape` marks it waiting; conduct it in the conversation.

- **Say whose it is.** "This is my outline, built from the brief. It is a starting point to argue over, and I expect parts of it to be wrong."
- **Shape before chapters.** Settle which of the two or three shapes first. Then the parts. Then chapter by chapter, in reading order.
- **One question at a time**, adversarial and open about it, as in the brief.
- **Press every chapter with the same three questions:**
  - the cut: "If this chapter were gone, what would the reader be unable to do at the end?" No answer means it goes or merges;
  - the swap: "Why does this come before the next one and not after?"
  - the thesis: "Who would disagree with this sentence?" A thesis nobody would dispute is a topic.
- **Press every arc:** what the reader would lose without it, whether it is true, and whether the author is willing to have it carry that much of the book.
- **A new idea is the author's.** When the author adds a chapter, a story or a turn you did not propose, that is the creative choice this step exists to collect. Record it in their words. Then do your part: run the §3 checks again and say what the addition does to the rest.
- **When an objection is really to the brief**, say so: "You are objecting to chapter four, and the reason you give contradicts `reader/main`. Which is wrong?" If the brief entry is wrong, revise it (`authorlm register revise`), and redraft the affected part of the outline from the corrected entry. Expect this to happen; it is the outline doing its work.
- **Redraft between rounds.** After each round rewrite `_book/shape-proposal.md` whole, with what changed and why at the top. Keep earlier rounds in `_book/shape-rounds/`; they are reference with no authority.

Save each session verbatim in `_book/interviews/`, as the brief does.

## 5. When the shape is settled

The shape is settled when the author says the outline is theirs, the §3 checks pass on the final version, and every thesis and arc has been read back and confirmed as in the brief's §4 (their words, your reading, the test).

Then:

1. Settle the entries: `authorlm register settle shape/outline`, each `thesis/<key>`, each `arc/<key>`. Drop the ones the author rejected, with their reason recorded.
2. Create the chapter files, each holding its title and nothing else, and write `toc.toml` to match the outline, parts included. Follow the house skill for registering a new file and its place in the reading order.
3. Have the author read the final `_book/shape-proposal.md` whole and approve it in words.
4. `authorlm book done <run-id> --result-json -` with the entries, the proposal file and the transcripts.

## 6. Changing the shape later

The shape will change once chapters exist. That is expected and cheap.

- A renamed chapter: `authorlm doc rename <file> <new-name>`. Its records follow it.
- A changed thesis, a moved chapter, a dropped arc: `authorlm register revise` on the entry, in the author's words. The verb returns the work that rested on the old entry; tell the author what is returned before they confirm.
- A change large enough that the outline no longer argues the paragraph in §3.1: `authorlm book restart shape-proposal --reason '…'`. The old outline and the chapters drafted under it are handed to the rerun as reference only.

## 7. What this step leaves alone

- The brief. A shape that needs the brief changed stops and says so.
- Chapter prose. Not a paragraph, not an opening line.
- The voice: its own step.
- What each chapter says in detail: the chapter interview settles that, chapter by chapter.
- The author's additions. A chapter or story the author brings is checked for what it does to the rest and is never argued away for the outline's convenience.
