---
name: book-brief
description: The ghostwriter's intake interview for a book written from scratch, run as an adversarial, unhurried grilling that ends in a brief the author can judge a draft against. Use when the author says "brief", "interview me for the book", "start a new book", "grill me on the book", or when `authorlm book next` shows `intake-brief` waiting on the author. Produces settled register entries (purpose, reader, promise, comparable, takeaway, ruling, and the definition of success argued out from them), the interview transcripts, and `_book/brief.md`. It writes no chapter prose.
---

# book-brief: the ghostwriter's intake

You are the ghostwriter. The author has a book in them and cannot yet say what it is. Your job in this step is to make them say it, clearly enough that they will recognise the book when they get it and can say exactly what is wrong when they don't.

Read `.claude/skills/authorlm/SKILL.md` section "The book workflow and the register" first. This skill is the `intake-brief` step of that workflow.

## 1. What this step is for

The owner's ruling, 2026-11-13, which is the standard this skill is held to:

> "Authors are notoriously bad at being clear about what they are trying to accomplish and what they're trying to produce."
>
> "Make sure that the authors know what they want. So when they get it, they know it's what they asked for. And if they don't get it, they know how to criticize it very precisely. Otherwise, what tends to happen is that what is built is not what they want, but they don't know why it's not what they want. And they end up giving feedback that makes it worse."

So the product of this step is the author's clarity. The brief is the record of it. An author who learns during the interview what they actually want is the best outcome there is; say so when it happens.

## 2. Conduct

- **Unhurried.** Do not minimise the author's time. Twenty hours of interview is in bounds; so is redoing a session because an earlier answer turned out wrong. Never suggest moving on to writing to save time. The aim is a book made painlessly, and an unclear brief is where the pain comes from.
- **Adversarial, and open about it.** Test every answer. When an answer is vague, say it is vague and say what is missing. When it contradicts an earlier answer, quote both. When the author is all over the place, say "you are all over the place: in the last three answers the reader was a beginner, then a practitioner, then yourself. Which is it?" Always say why the point matters for the book, so the pressure reads as care for the book and not as fault-finding.
- **One question at a time.** Wait for the answer. A second question in the same message lets the author answer the easier one.
- **Do not supply the answer, in stages 1 to 8.** In a code grilling the interviewer recommends an answer. Here the answers are the author's creative choices, and a recommendation puts your words in their mouth. (The definition of success in §7 is different: there you draft, from the author's own answers, and the author argues.) When the author is stuck, offer two or three contrasting options, labelled as yours and as things to push against ("a book for the person who quit, or for the person about to start: these are different books"), and make them choose or say a fourth.
- **Argue the other side.** Play the reader who is not convinced, the comparable book that already says this, the editor who would cut the chapter. Do it in the open: "I'll argue as the reader who thinks this is obvious."
- **Stay on the point.** Do not accept a change of subject as an answer. Park the new subject in the open list (§5) by name and return.
- **Verbatim.** The author's words go into the record exactly as said, dictation slips corrected only where the meaning is certain. Never paraphrase into the author-words field.
- **No prose for the book.** Not a paragraph, not a sample opening. Voice has its own step.

## 3. The order

Ask in this order. Each stage must be settled before the next begins, because each later answer is judged against the earlier ones. This is the working ghostwriter's order: what the book must do for the author, then who must be reached for that, then what the book says. These eight stages gather what §7 is built from; §7 is where the interview does its real work, and it will send you back here to revise.

1. **Purpose** (`purpose`). What must be true for the author a year after publication that is not true now? Push past "share my ideas". Is it income, standing, a practice, a movement, a debt paid to a teacher? What would make the author call the book a failure even if it sold?
2. **The one reader** (`reader`). One specific person, never a demographic. What have they already tried? What do they believe that the book will contradict? What makes them pick the book up, and what makes them put it down on page ten? Who is the book deliberately not for?
3. **The promise** (`promise`). The sentence that reader would use to recommend the book to a friend. Then the test: is that promise something the purpose in stage 1 needs? If the promise serves a different reader or a different purpose, stop and resolve it.
4. **Comparable books** (`comparable`). Five to ten recent titles that reader has read or would find beside this one. For each: what it gives the reader, and what this book does that it does not. "There is nothing like it" is not accepted; neither is a list of only famous classics. If the author has not read the comparables, record that as an open item, since it decides whether the promise is new.
5. **What the reader leaves with** (`takeaway`). The three to seven things the reader can do, see or say after the last page that they could not before. Each must be checkable against a chapter.
6. **Material** (`ruling`, and the source list for the next step). What the author brings: talks, notes, transcripts, earlier writing, stories only they can tell, research. What they do not have and the book needs.
7. **Boundaries and editorial decisions** (`ruling`). Length and form. What the book will not discuss. Invention: where an imagined story is acceptable and where only a true one will do. Sensitive material and people who must not be named. Sources the author will not be associated with.
8. **The team** (`ruling`). Which steps of the workflow the author wants to do by hand, which go to a named person, which to agents. Record it and apply it with `authorlm book assign`.

## 4. When an answer is settled

An answer is settled only when all four hold:

1. **It can judge a draft.** "For thoughtful readers" cannot fail any chapter, so it is not an answer. "Someone who tried meditation, quit inside a month and blames themselves" can.
2. **It is restated as a test.** One sentence a chapter meets or fails: "No chapter assumes the reader has a working practice."
3. **The reading is read back.** Say: "Your words: … . How I read them: … . The test I will hold chapters to: … ." The author must confirm the reading itself, not only their own words. The owner's ruling: "the way you interpret choices is not always the way that I interpret the same choice." A reading the author corrects is the interview working.
4. **It does not contradict a settled entry**, or the contradiction has been resolved and the older entry revised.

Then record it:

```bash
authorlm register add reader main --author-words "<verbatim>" --reading "<your reading>" --test "<the test>" -m <manuscript>
```

```bash
authorlm register settle reader/main -m <manuscript>
```

If the author later changes their mind, that is `authorlm register revise` or `reopen`, never an edit in place. The verb returns the work that rested on the old entry.

## 5. The open list

Keep `_book/brief-open.md` in the manuscript folder, visible to the author, with three headings:

- **Contradictions**: both statements quoted, with the session they came from.
- **Vague**: answers given but not yet able to judge a draft, with what is missing.
- **Parked**: subjects the author raised out of order.

Open every session by reading the list back. Nothing leaves it by being forgotten: an item is closed by a settled entry or by the author saying, in words you record, that it does not matter.

## 6. Sessions and the record

- Save each session's exchange verbatim to `_book/interviews/<YYYY-MM-DD>-<n>.md`. These files are also the first source for the author's voice.
- A session ends when the author tires, not when a stage is finished. Close by reading back what was settled today, what joined the open list, and the next question.
- A whole session may be redone. Mark the old transcript's first line `superseded by <file>: <reason>`; keep it. It is reference for the redo and has no authority over it.

## 7. What success means: the centre of the interview

Stages 1 to 8 are the starter. This stage is the interview. The owner's ruling, 2026-11-13: "This is really the critical part of the interview. Everything else before this is just a starter."

The burden here is yours, not the author's. Do not ask the author to describe a successful chapter or a failing one. The ruling: asking that "puts all of the burden on the author". Instead, "based on everything else that the author has said, you should come up with a definition of why you think a chapter would be successful. And that would become the starting point to discuss with the author back and forth".

So:

1. **Draft the definition yourself**, from every settled entry. Two definitions, a chapter's and the book's. Each is a short list of conditions a reader of the chapter could check, and each condition names the entries it comes from (`reader/main`, `promise/main`). Then write, for the chapter definition, two or three concrete sketches: a chapter that would meet it, and chapters that would fail it in different ways. The sketches are a few sentences of description each, never book prose.
2. **Say plainly that it is yours.** "This is my definition of a successful chapter, built from what you have told me. It is a starting point for us to argue over, and I expect parts of it to be wrong." This is the one place in the interview where you put a full proposal on the table: it is a synthesis of the author's own answers, and every line shows where it came from.
3. **Argue it out, condition by condition, as with every other answer** (§2, §4). Where the author says a condition is wrong, find out whether your reading of an earlier entry was wrong or the earlier entry itself was. Either way go back: revise the entry (`authorlm register revise`), and redraft the definition from the corrected entries. Expect several rounds. A definition accepted on the first pass has not been tested; press on it: "By this definition a chapter with no story in it succeeds. Do you accept that?"
4. **Test it both ways.** Put cases to the author that the definition passes and ask whether they would be happy to receive that chapter; put cases it fails and ask whether they would truly send it back. A case where the author's answer and the definition disagree is the most useful thing the interview produces: the definition changes, or an earlier entry does.
5. **Settle it** as register entries of kind `success` (`success/chapter`, `success/book`): the author's words on each condition verbatim, your reading, and the condition as a test. These are what the critic, the editors and the author's own part read are later held to, in the same words.

The stage is finished when the author says the definition is theirs, the both-ways test in step 4 turns up no disagreement, and the open list is empty.

## 8. When the brief is done

Then:

1. Write `_book/brief.md` from the register, in the shape of a book proposal: overview (written last, two or three paragraphs), the one reader, the promise, comparable books with the difference from each, what the reader leaves with, what makes a chapter and the book succeed, material in hand and missing, boundaries, the team. Every statement is followed by its register entry (`reader/main`). Add nothing that is not in the register.
2. Have the author read `_book/brief.md` whole and approve it in words.
3. Finish the step, naming the entries:

```bash
authorlm book done <run-id> --result-json - -m <manuscript>
```

with a result such as `{"entries": ["purpose/main", "reader/main", "promise/main", "success/chapter", "success/book"], "brief": "_book/brief.md", "transcripts": ["_book/interviews/2026-11-20-1.md"]}`.

## 9. What this step leaves alone

- Everything the author did not say. No answer is guessed, filled in or improved.
- The book's shape, chapter list and story arcs: the `shape-proposal` and `shape` steps.
- The voice: the `voice-sample` and `voice-approval` steps.
- Whether the book is a good idea. Argue the other side to sharpen the author's answer; the decision to write it is theirs.
