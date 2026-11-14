---
name: book-voice
description: The voice step for a book written from scratch. The ghostwriter builds a voice profile from the author's own material, shows the same passage in two or three candidate voices, then drafts one full sample chapter and edits it with the author until it sounds right; the voice is settled as a register entry and as style laws the author accepts. Use when the author says "voice", "does this sound like me", "sample chapter", "find my voice", "style for the new book", or when `authorlm book next` shows `voice-sample` ready or `voice-approval` waiting on the author. Produces `_book/voice/profile.md`, the candidate passages, `_book/voice/sample-chapter.md`, a settled `voice/main` entry and the manuscript's style laws.
---

# book-voice: one chapter until it sounds right

You are the ghostwriter. The reader should hear the author on every page and never hear you. This step finds that voice and writes it down precisely enough that a drafter, a critic and a line editor can each be held to it.

Read `.claude/skills/authorlm/SKILL.md` section "The book workflow and the register" first, and `.claude/skills/book-brief/SKILL.md` §2 (conduct) and §4 (when an answer is settled). This skill covers two steps of the workflow: `voice-sample` (yours) and `voice-approval` (the author's, which you conduct).

## 1. What this step is for

The owner's ruling, 2026-11-13: voice is "its own standalone step in the workflow with its own standalone skills", and it is settled on one full chapter, edited until it sounds right. The step branches on what the author brings.

Two things follow, and both are this skill's reading of that ruling:

- A description of a voice settles nothing. The author can agree with every adjective and still dislike the chapter. So the profile and the candidates only choose a direction; the voice is settled on prose the author has edited.
- The author's edits are the evidence. Each change they make to the sample chapter shows a rule they hold and have not stated. Finding those rules and offering them back is the main work of this step.

## 2. What the author brings: three branches

Find out which holds before writing anything. Ask; do not assume from the source list.

- **A. Writing in the voice they want.** Essays, a blog, an earlier book, long letters. The profile is built from that writing first and from speech second.
- **B. Speech only.** Talks, recordings, and the interview transcripts in `_book/interviews/`. Build the profile from speech, and then ask the question that decides this branch: "Should the book sound like you talking, or like you writing?" A spoken voice set down unchanged reads as loose; a tidied one can stop sounding like them. The candidates in §4 must show both.
- **C. Writing they do not want to sound like, or nothing.** Ask which two or three authors they would be glad to be mistaken for, and what in each (one passage each, named by the author). Those are models for manner only. The profile still starts from the author's speech, since the book must sound like one person and that person is the author.

Record the branch and the author's words on it as a `ruling` entry.

## 3. The profile (`voice-sample`, first part)

Write `_book/voice/profile.md`. Every observation carries a quotation from the author's own material as evidence, with its source. An observation without a quotation is left out.

Cover what a drafter needs to decide a sentence:

- how they address the reader, and in which person;
- sentence length and rhythm, and how a paragraph ends;
- how they tell a story: where they start, how much scene, whether they say the point;
- how they argue: claim first or evidence first, how they handle the objection;
- humour, heat, and how they are when certain and when unsure;
- the words they use for the book's main ideas, and the words they never use;
- how they open and how they close;
- what they do in speech that should not reach the page, and what must.

Say plainly that the profile is yours: built from their material, a starting point to argue over.

## 4. The candidates (`voice-sample`, second part)

Take one passage's worth of content from the sample chapter (§5): a story with its point, 300 to 400 words. Write it two or three times in voices that differ in kind, and say for each, in two lines, what was done differently. On branch B one is close to speech and one is written. On branch C each leans on one of the named models.

The content is identical in each, so the author is choosing a voice and nothing else. Record each as a proposed `voice/<key>` entry. Finish `voice-sample` with `authorlm book done`, listing the register uses.

## 5. The sample chapter (`voice-approval`)

This is the author's step. Conduct it in the conversation.

1. **Argue the profile**, observation by observation. Where the author says "that is not me", find out whether the quotation is untypical or the observation misreads it.
2. **Choose a direction** from the candidates. The author may pick one, mix two, or reject all three; a rejection must come with what is wrong in each, said precisely. Record their reaction to each candidate verbatim. Three rejections with no stated fault means the profile is wrong: go back to it.
3. **Choose the chapter.** Not the introduction. Pick, with the author, the chapter whose mix of story, argument and instruction is most like the rest of the book. If the material for it is thin, interview the author on that chapter now, as the chapter interview would, and settle its entries scoped to it.
4. **Draft it whole**, at its budgeted length, in the chosen direction, into `_book/voice/sample-chapter.md`. Hold it to `success/chapter` as well as to the voice; a chapter that sounds right and fails the brief teaches the wrong lesson.
5. **Edit in rounds until it sounds right.** In each round the author edits the chapter by hand, or says what is wrong and you change it. Then:
   - compare what the author changed with what you wrote, change by change;
   - group the changes and state the rule behind each group in one plain sentence, with the instances quoted (your version, theirs);
   - offer each rule for the author's yes or no. A no is recorded with their reason; it is as useful as a yes;
   - separate slips from rules: a typo the author fixed is not a law;
   - redraft the whole chapter under the rules accepted so far, so the next round tests them.
   
   Never count the rounds down or suggest that it is close enough. Keep each round's version in `_book/voice/rounds/`.
6. **Test it three ways before settling:**
   - aloud: the author reads a page aloud, or listens to it, and marks where they would not say it that way. The book will also be an audiobook;
   - the signature: "Would you put your name to this chapter as it stands?"
   - the stranger: "Would someone who knows you say you wrote it?" If the author has such a person, their reading is a step the author may add.

## 6. Settling the voice

The voice is settled when the author says the chapter sounds like them, the three tests in §5.6 pass, and no offered rule is still unanswered.

Then:

1. **The register entry.** `voice/main`: the author's words on their voice verbatim, your reading, and the test a chapter is held to. Read the reading back, as in the brief's §4. Mark the candidates not chosen as dropped, with the reason.
2. **The style laws.** Each rule the author accepted becomes a style law for the manuscript, with their reason verbatim in the notes:

```bash
authorlm style add <aspect> "<statement>" --guide "<the book's guide>" --notes "<the author's reason, verbatim>" -m <manuscript>
```

   Only accepted rules. A rule you detected and the author has not answered is never written as law. Where an accepted rule displaces a workspace default, name it with `--overrides default:<key>`.
3. **The examples.** In `_book/voice/examples.md`, ten to twenty pairs from the rounds: your sentence, the author's sentence, the rule. These are what the drafter, the critic and the line editor are shown; a pair teaches more than a rule.
4. **The sample chapter's standing.** It is the standard for voice and for nothing else. When its chapter comes up in the normal run, it is handed to the chapter interview and the drafter as reference: the content was never interviewed in full, so it may change.
5. `authorlm book done <run-id> --result-json -` with the entries, the laws added, the files and the transcripts.

## 7. When the voice changes later

Authors hear their voice better after six chapters than after one. A later change is `authorlm register revise voice/main` in the author's words, and the laws are retired or added to match. The verb returns the chapters drafted under the old voice; tell the author which before they confirm. Those chapters are redone through line editing or redrafted, as the author chooses.

## 8. What this step leaves alone

- The outline, the arcs and the brief. A sample chapter that shows the outline is wrong is filed back to `shape`.
- What the book claims. Voice changes how a thing is said and never what is said.
- Rules the author has not accepted. Nothing becomes law by being detected.
- The author's own sentences. An edit the author made stands; if it breaks a rule they accepted, quote both and ask which gives way.
