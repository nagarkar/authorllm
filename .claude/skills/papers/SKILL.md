---
name: papers
description: Common core for writing papers and commentaries from a ratified manuscript, in any field. Stages and gates, rulings as a conversation, a visible claims inventory, referee mechanics, the master Google Doc as the record, the local and cloud split. A field skill (papers-physics, papers-philosophy) adds its own stages, referee roles and publish road. Use when the author says "paper", "referee", "abstract", "the series", "ruling", "publish the paper", or names a paper by number; load the field skill with it.
---

# papers: the common core

The standards of a paper are external, set by its field, so AuthorLM's lenses and filters play a small part. What carries the work is a staged process with gates the author passes in chat, a visible record of what was agreed, and referees that run as subagents. This file is the core every field shares; `papers-physics` and `papers-philosophy` list only their differences. Read `house-rules.md` beside this file before writing a sentence for the author: it is the repo's copy of the vocabulary rules a cloud session cannot read from the author's Mac, and every subagent brief quotes its block verbatim.

## 0. Sources and their rank

1. The ratified chapters of the manuscript. Every claim a paper makes about the theory cites a chapter sentence by page, and a term the chapters lack is marked as the series' own term.
2. The project's record of what the author has settled (the rulings tab, section 3). Quote it; never re-litigate it without saying that is what you are doing.
3. The author's ideas documents and chat notes: hints only. A drafting agent never sees the raw ideas document; it gets the claims inventory (section 4).

## 1. Stages and gates

Each stage ends at a gate the author passes in chat. Do not start the next stage early. The first project's cost went mostly into rewriting skeletons after bodies existed, and the bodies still have not been read in full; the lesson is that each layer closes before the next is written. A commentary on the book's own text (a recap, a chapter) skips the stages marked "paper only".

| stage | what is made | gate |
|---|---|---|
| **A. The set of papers** (paper only; optional: ask whether this is one paper or several) | the `papersequence` tab: the papers, their order, what each claims and what each leaves to the next | author agrees the list and the order, or says it is one paper |
| **B. Claims inventory** | the `claims_premises` tab: every claim the sources make on the topic, with source lines, contradictions kept, nothing resolved; a changelog at its head | author sees it in chat as a table (section 4) and accepts it as the drafter's input |
| **C1. Abstract and plain story** | the abstract (200 words or fewer) and the plain story, explained as if to a ten-year-old, 400 to 600 words, each sentence cited | author ratifies; a referee reads it for overclaim |
| **C2. Premises** (paper only) | the numbered premise list, each with what it costs and where it is used | author ratifies the wording; a referee reads it for hidden posits |
| **C3. Field outline** | what the field skill says: equations in outline for physics, the argument's steps and the positions engaged for philosophy | author ratifies; referees read it |
| **D. Field preparation** | what the field skill says: programs and probes for physics, citation verification for philosophy | the field skill's check |
| **E. Body, one argument at a time** | each argument or derivation drafted alone, shown in chat for the author's reading, sent to referees before and after the author's feedback, then placed in the text | author passes each; no body section exists that was not passed this way |
| **F. Final referee round** | the whole text to all referees in parallel; the round's ledger in the `referee` tab; a version row in the amendments section | no blocker open; author passes the title |
| **G. Grounding audit** | every claim tied to a chapter line or marked as the series' own | author |
| **H. Publish** (paper only) | the field skill's publish road; the AI-use note updated | version closed |

A reopened text re-enters at the stage its change touches. A change of claim re-enters at C1, C2 or C3, whichever it alters.

## 2. Referee mechanics

- **Pick the referees first.** The author names some; propose the rest with one reason each, and wait for the author's yes. Roles (physicist, math verifier, line editor) and personas (a named thinker arguing from documented positions) are both referees; the field skill lists which it uses.
- **One shared brief per round.** It gives the text (a file path, or the tab's text pasted when the session is in the cloud), the sections in scope, what the previous round established, the house rules block quoted verbatim, and "do not edit; report numbered findings ranked blocker / should-fix / nit, each with the quoted sentence and a concrete fix".
- **Run in parallel, apply in one pass.** Background subagents, one per referee; apply all reports together so overlapping sentences are edited once.
- **A ledger per round.** Round N+1's brief carries round N's findings each marked MET, PARTLY, NOT, or WITHDRAWN under a ruling, with the ruling quoted.
- **A blocker is a change of claim**, not a wording fix; it re-enters the stage it alters.
- **A referee's objection to something the author settled** goes to the author as a question, never applied over the author's word.
- **"Talk to the referees"** means messaging the same agents for options, not spawning new ones.
- Reports and ledgers go into the text's `referee` tab, headed by round and referee. The tab is permanent: it is the response-to-referees document.

## 3. Rulings are a conversation, not a transcript

The author thinks aloud in chat. A thought is not a ruling. The first project recorded thoughts as rulings and the author found two faults: they could not see what had been written without opening another file, and they wanted a discussion, not acceptance.

- **Discuss first.** When the author offers a thought, answer it: what it changes, what it costs, where it conflicts with the chapters or with earlier agreements, and your recommendation. Raise the objection a referee would raise. Only then ask whether it should be settled.
- **Settle only on the author's word.** Something becomes a ruling when the author says so after the discussion ("rule it", "yes, settle that", or an answer to a direct question). A settled ruling is carried into the text, as a premise, a term, an amendment or a boundary, and leaves the tab.
- **Show what you wrote.** When a line is added to the rulings tab, the chat message carries the line itself, verbatim, with a link, so the author can read it without opening anything. A ruling row has: id, date, the author's words quoted, your reading of them, how it was applied, and what remains open.
- **Three kinds of entry:** ruling (settled by the author), working position (your reading, applied provisionally and marked so in the text), open question (nothing applied). A working position is revisited at the next gate.

## 4. The claims inventory is visible and revisable

The author never saw the first project's inventories and could not judge them. From now on:

- When an inventory is made, its summary appears in chat as a table: claim, source lines, status (canon, hint, contradicted), and which paper and section will carry it.
- Every later change to it is shown in chat as added, removed, changed, with the reason, and is written to the changelog at the head of the tab.
- The inventory is the drafter's input and the author's to change at any time; a claim the author strikes is kept in the changelog with the date, not deleted.

## 5. Where the record lives: the Doc, not a folder

The author's design (2026-10-19): the content of a project, claims, premises, arguments, abstract, lives nowhere but in the texts at the end. While the work is open, the master Google Doc is the store, as tabs whose names say what they hold. Nothing of the content goes into git. The local manuscript folder is the file mirror the Doc bridge works from, and it stays local.

Tab names, `<project>_<what><number or stem>.md`, the qualifier omitted for what is common to the project:

| tab | holds | lifetime |
|---|---|---|
| `<project>_paper0.md` | the paper (for a commentary, the chapter's own tab) | permanent |
| `<project>_papersequence.md` | the set of papers, order, what each claims and leaves to the next; the Doc ids | until the last paper closes |
| `<project>_claims_premises0.md` | claims and premises not yet in the text, with the changelog | until empty, then deleted |
| `<project>_rulings.md` | rulings, working positions and open questions not yet carried by a text | until each is carried, then deleted |
| `<project>_referee0.md` | every referee round for the text, each headed by round and referee, with its ledger and response | permanent beside the text |

The text carries everything in the end: its premises list every posit, its appendices carry what the field skill says they carry, its amendments section carries what it asks of the book, and its boundaries carry what it leaves open. From the text alone the claims, premises and outline can be reconstructed. Only what is not yet used lives outside it, in its own tab, temporarily.

The `.md` in the names is the Doc bridge's convention, which names a tab after the file it mirrors; a local session has those files in the manuscript folder, a cloud session has none and needs none. The names are kept the same in both so that the bridge, when it runs locally, finds its tabs.

In a session, claims, premises and open questions are managed in context. They are flushed to their tab at every gate, at every ruling, and at the end of the session, not only at the end: context is lost at compaction, and a flush at a gate costs one push.

## 6. Local and cloud

A cloud session has the repo and the connectors, not the author's Mac: no `~/.claude`, no AuthorLM workspace, no manuscript folder. It reads this skill and `house-rules.md` from the repo. Everything else comes from the master Doc, and the road to the Doc is the AuthorLM CLI, the same `doc pull` and `doc push` the Mac runs, per tab, with the same conflict check. The Drive connector is for reading only, when the CLI is not set up.

The first cloud test (2026-10-19, report: [papers-cloud-test-2026-10-19](https://docs.google.com/document/d/1xCAeMM23h4oFsc6ekbI9juRkynZhi1NOkkgs645435g/edit)) settled the connector's limits. It returns the whole master Doc as one text with no tab parameter; tabs appear as H1 headings of the form `# **papers/0-axioms.md**`, so a tab can be read by slicing between two such headings. It cannot write a tab or change any Doc's content, only create new Docs and edit metadata. The CLI installed and ran, but stopped at the missing workspace. Referees and numpy probes ran fine.

What the CLI needs in the cloud, and where each piece comes from:

| piece | source | where it goes |
|---|---|---|
| the Google token | the Mac's `~/.authorlm/gdocs_token.json`; its refresh token renews without a browser, and it carries the client id and secret, so no client file is needed | the environment's secret variable `AUTHORLM_GDOCS_TOKEN`, the file's contents as one line. The CLI reads it when the file is absent and keeps it in memory: a cloud session may not write a credential to disk, and the second cloud test (2026-10-19) found the setup script did not either |
| a workspace | `authorlm setup --workspace ~/authorlm-ws --yes` in the session's first turn | disposable; the Doc is the source of truth |
| the manuscript | `authorlm init --name <Manuscript> --path ~/authorlm-ws/<Manuscript> --extraction off`, then `authorlm doc bind <masterDocId> -m <Manuscript>`, then `authorlm doc pull -m <Manuscript>`, which adopts every `.md` tab as a local file | the local cache of the tabs, with per-tab state |

`doc bind` (added 2026-10-19) records an existing master Doc on a fresh workspace and writes nothing to the Doc; without it the first `doc push` would create a new Doc beside the real one. `cloud-test-2.md` beside this skill tests the road end to end. Until that test passes, a cloud session reads through the connector and writes nothing to the Doc; what it drafts goes to a new Doc in the master's folder named exactly as the tab would be, and the local session folds it into the tab.

Rules for a cloud session on the CLI road:

- Pull once at the start of the session and once before every push. A tab the author or a local session changed since the pull is a conflict; the pull reports it and the push stops. Resolve it by pulling that tab with `--force` and redoing the edit on the fresh text, never by forcing the push.
- Push only the tabs this session edited, by name, one `doc push <file>` each. No whole-manuscript push from the cloud.
- After each push, pull the pushed tab and confirm the round trip; then check that no other tab changed (`doc list -m <Manuscript>` shows nothing checked out that was not before).
- The workspace dies with the session and the token never touches its disk. Nothing of them is committed.
- Exports, the rendered-Doc refresh, `doc auth` and `drive_docx.py` stay local.

An expired Google token stops a push; `authorlm doc auth` opens a consent page and runs only on the author's word. Every message that deals with a manuscript carries its master Doc link.
