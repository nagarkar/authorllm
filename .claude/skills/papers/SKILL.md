---
name: papers
description: Write journal papers from a ratified manuscript, one paper or a related set, with staged gates, conversational rulings, a visible claims inventory and subagent referees. Use when the author says "paper", "referee", "abstract", "the series", "ruling", "publish the paper", or names a paper by number. Works in a cloud session: the rules are in the repo, the content is in the master Google Doc's tabs.
---

# papers: from a ratified manuscript to a refereed paper

The standards of a journal paper are external, set by its field, so AuthorLM's lenses and filters play a small part. What carries the work is a staged process with gates the author passes in chat, a visible record of what was agreed, and referees that run as subagents. This skill is generic; everything particular to one paper project lives in the project's tabs of the master Google Doc (section 5). Read `house-rules.md` beside this file before writing a sentence for the author: it is the copy of the vocabulary rules a cloud session cannot read from the author's Mac.

## 0. Sources and their rank

1. The ratified chapters of the manuscript. Every claim a paper makes about the theory cites a chapter sentence by page, and a term the chapters lack is marked as the series' own term.
2. The project's record of what the author has settled (the rulings tab, section 3). Quote it; never re-litigate it without saying that is what you are doing.
3. The author's ideas documents and chat notes: hints only. A drafting agent never sees the raw ideas document; it gets the claims inventory (section 4).

## 1. Stages and gates

Each stage ends at a gate the author passes in chat. Do not start the next stage early. The first project's cost went mostly into rewriting skeletons after bodies existed, and the bodies still have not been read in full; the lesson is that each layer closes before the next is written.

| stage | what is made | gate |
|---|---|---|
| **A. The set of papers** (optional: ask whether this is one paper or several) | the `papersequence` tab: the papers, their order, what each claims and what each leaves to the next | author agrees the list and the order, or says it is one paper |
| **B. Claims inventory** | the `claims_premises` tab: every claim the sources make on the paper's topic, with source lines, contradictions kept, nothing resolved; a changelog at its head | author sees it in chat as a table (section 4) and accepts it as the drafter's input |
| **C1. Abstract and plain story** | the abstract (200 words or fewer) and the plain story, explained as if to a ten-year-old, 400 to 600 words, each sentence cited | author ratifies; a referee reads it for overclaim |
| **C2. Premises** | the numbered premise list, each with what it costs and where it is used | author ratifies the wording; a referee reads it for hidden posits |
| **C3. Equations in outline** | each equation with a one-line justification and the premise it rests on; no derivations yet | author ratifies; referees (physicist, math) read it |
| **D. Programs and numbers** | the program that runs the paper's model and the probes that produce every number the paper will quote; their listings and printed output are appendices, verbatim | the math verifier confirms every number against an independent computation |
| **E. Body, one derivation at a time** | each derivation drafted alone, shown in chat for the author's reading, sent to referees before and after the author's feedback, then placed in the paper | author passes each derivation; no body section exists that was not passed this way |
| **F. Final referee round** | the whole paper to all referees in parallel; reports and replies in the `referee` tab; a version row in the paper's amendments section | no blocker open; author passes the title |
| **G. Grounding audit** | every claim tied to a chapter line or marked as the series' own | author |
| **H. Publish** | the Doc tab, the exports, the rendered Doc, the PDF sent to the author; the AI-use note updated | version closed |

A reopened paper re-enters at the stage its change touches. A change of claim re-enters at C1, C2 or C3, whichever it alters.

## 2. Referees as subagents

Run them in parallel in the background, one brief each, and apply all reports in one pass so overlapping sentences are edited once. A brief gives the text (a file path, or the Doc text pasted when the session is in the cloud), the sections in scope, what the previous round established, the house rules for quotes, and "do not edit; report numbered findings ranked blocker / should-fix / nit, each with the quoted sentence and a concrete fix".

| role | checks | must do |
|---|---|---|
| physicist | derivations, hidden posits, overclaims, sign conventions, boundary conditions, whether "derived" is derived | rerun the probes it doubts; separate what is proven from what is read |
| math verifier | every identity symbolically; every printed number by an independent method sharing no code; listings byte for byte against the files | a table: claim, its value, the paper's value, verdict |
| line editor | house vocabulary, sentence length, duplication inside a section, citation form, terms marked as the series' own, the same claim stated consistently wherever it appears | ready-to-apply replacements |
| philosopher | grounding in the chapters, what the book does and does not say, amendments the paper implies | quote the chapter |

Reports go into the paper's `referee` tab, headed by role and round. A referee's blocker is treated as a change of claim, not a wording fix. A referee's objection to something the author settled goes to the author as a question, never applied over the author's word.

## 3. Rulings are a conversation, not a transcript

The author thinks aloud in chat. A thought is not a ruling. The first project recorded thoughts as rulings and the author found two faults: they could not see what had been written without opening another file, and they wanted a discussion, not acceptance.

- **Discuss first.** When the author offers a thought, answer it: what it changes, what it costs, where it conflicts with the chapters or with earlier agreements, and your recommendation. Raise the objection a referee would raise. Only then ask whether it should be settled.
- **Settle only on the author's word.** Something becomes a ruling when the author says so after the discussion ("rule it", "yes, settle that", or an answer to a direct question). A settled ruling is carried into the paper, as a premise, a term, an amendment or a boundary, and leaves the tab. Until then it is an open question or a working position, and the record says which.
- **Show what you wrote.** When a line is added to the rulings tab, the chat message carries the line itself, verbatim, with a link to the file and the line, so the author can read it without opening anything. A ruling row has: id, date, the author's words quoted, your reading of them, how it was applied, and what remains open.
- **Three kinds of entry:** ruling (settled by the author), working position (your reading, applied provisionally, marked so in the paper), open question (nothing applied). A working position is revisited at the next gate.

## 4. The claims inventory is visible and revisable

The author never saw the first project's inventories and could not judge them. From now on:

- When an inventory is made, its summary appears in chat as a table: claim, source lines, status (canon, hint, contradicted), and which paper and section will carry it.
- Every later change to it is shown in chat as added, removed, changed, with the reason, and is written to the changelog at the head of the tab.
- The inventory is the drafter's input and the author's to change at any time; a claim the author strikes is kept in the changelog with the date, not deleted.

## 5. Where the record lives: the Doc, not a folder

The author's design (2026-10-19): the content of a paper project, claims, premises, equations, abstract, lives nowhere but in the papers at the end. While the work is open, the master Google Doc is the store, as tabs whose names say what they hold. Nothing of the content goes into git. The local manuscript folder is the file mirror the Doc bridge works from, and it stays local.

Tab names, `<project>_<what><paper number>.md`, number omitted for what is common to all papers:

| tab | holds | lifetime |
|---|---|---|
| `<project>_paper0.md` | the paper | permanent; in the steady state only this and the referee tab remain |
| `<project>_papersequence.md` | the set of papers, order, what each claims and leaves to the next; the Doc ids | until the last paper closes |
| `<project>_claims_premises0.md` | the claims inventory and the premises not yet in the paper, with the changelog | until every item is in the paper or struck; then deleted |
| `<project>_rulings.md` | rulings, working positions and open questions not yet carried by a paper | until each is in a paper's premises or amendments; then deleted |
| `<project>_referee0.md` | every referee round for the paper, each headed by round and role, with the response to it | permanent beside the paper: it is the response-to-referees document a journal asks for |

The paper carries everything in the end: its premises list every posit, its appendices carry every program and probe verbatim with its printed output, its amendments section carries what it asks of the book, and its boundaries carry what it leaves open. From the paper alone the claims, premises and equations can be reconstructed. Only what is not yet used lives outside it, in its own tab, temporarily.

The `.md` in the names is the Doc bridge's convention, which names a tab after the file it mirrors; a local session has those files in the manuscript folder, a cloud session has none and needs none. The names are kept the same in both so that the bridge, when it runs locally, finds its tabs.

In a session, claims, premises and open questions are managed in context. They are flushed to their tab at every gate, at every ruling, and at the end of the session, not only at the end: context is lost at compaction, and a flush at a gate costs one push.

## 6. Local and cloud

A cloud session has the repo and the connectors, not the author's Mac: no `~/.claude`, no AuthorLM workspace, no manuscript folder. It reads this skill and `house-rules.md` from the repo and everything else from the Doc through the Drive connector.

| local session | cloud session |
|---|---|
| the AuthorLM CLI and MCP; `doc push` and `doc pull` between the manuscript folder and the tabs; exports; the rendered-Doc refresh; probes in the local Python environment | reads the tabs; runs referees on their text; drafts, line-edits and manages the record in context; writes the result back to the Doc at each gate |

The cloud test in `cloud-test.md` beside this skill settles what a cloud session can do; run it once and record its result here. Until then, writing a tab from the cloud is unverified: the Drive connector reads and updates whole Docs, and whether it can address one tab is to be tested on the first cloud session. If it cannot, the cloud session writes a separate Doc in the same folder named exactly as the tab would be, and the local session folds it into the tab with `doc pull` and `doc push`. The name is the key either way.

Publish road, local only:

```bash
authorlm doc push papers/<paper>.md -m <Manuscript>
authorlm export pdf --file papers/<paper>.md -m <Manuscript>
authorlm export docx --file papers/<paper>.md -m <Manuscript>
python3 tools/drive_docx.py manuscripts/<Manuscript>/_exports/<paper>.docx --update <renderedDocId>
```

An expired Google token stops the push; `authorlm doc auth` opens a consent page and runs only on the author's word. Every message that deals with a manuscript carries its master Doc link.
