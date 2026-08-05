# Design record — ratified and BUILT

Designs that graduated from the backlog. The rationale is kept
here because the decisions (jurisdictions, rejected alternatives)
outlive the build. Implementation status of the co-writing loop
lives in autoregressive-writing-design.md §11.

## Tab hierarchy and reordering from toc.md (gdocs)
`reading_order` (structure.py) parses toc.md as a flat filename list —
indentation never enters the parse — and `ensure_master` (gdocs.py) sends
`addDocumentTab` with only a title, never `parentTabId`. Consequences:
nested toc entries push as flat sibling tabs, and toc order is honored
only at tab-creation time — reordering toc.md later never repositions
existing tabs. Design: toc.md indentation defines tab hierarchy (nested
entries become child tabs via `parentTabId`); `ensure_master` diffs the
Doc's tab tree against the toc tree on every push and repositions /
re-parents existing tabs to match, rather than only appending missing
ones. Ratified 2026-07-27; build when a manuscript actually nests its
chapters.


## Autoregressive paragraph-level co-writing loop
Full design in `autoregressive-writing-design.md` (ratified 2026-08-01).
Beat-by-beat chapter writing: author supplies outline + ≥1 opening
paragraph + TOC placement; system expands to a ratified beat plan
(DOC-style leaf beats), then proposes one unit at a time conditioned on
style law + Concept Graph + validated policies + accepted text, with the
author gating every beat and each verdict/rewrite captured as evidence
(beat-level granularity: ~15 recorded judgments per chapter vs 1 for a
one-shot draft). Economics rest on Common Core P2 via prompt caching:
layered payload ordered by volatility, breakpoints at law/frame/tail,
1h TTL on stable layers — ~85% saving on re-read tokens across a
30-beat chapter. Adopts Re³'s recursive payload assembly and DOC's
detailed outliner; replaces their controllers/rerankers with the
auditors and the author. Build after the auditors (they are the loop's
inner critics); the manual conversational version of the loop is
available today on request.


## Doc-comments ingestion (`doc pull` comments by default)
Google Docs comments are author reactions — evidence, not Doc state to
preserve. Push rebuilds a tab wholesale (deleteContentRange, gdocs.py:491),
so anchors die on every round trip; anchor preservation (diff-aware
transplant) was considered and rejected as deep bridge work solving the
wrong problem. Design, ratified in conversation 2026-08-03:
(1) **Harvest on pull, by default.** `doc pull` lists open comments
(Drive v3 comments.list; the existing drive.file token suffices — verified
live on the master Doc) and its output carries a compact "Comments to
address (N)" section: location, clamped quote, the author's words
VERBATIM (clamp the anchor quote, never the comment — R6.3 discipline).
Effect: a chat agent running the pull sees the comments in-band and
processes them unprompted, the same mechanism as the briefing's caught_up.
Skill rule to add at build: address every comment in pull output; when
acting on one, record the author's comment verbatim as the evidence/reason.
`--no-comments` as escape hatch.
(2) **Store per comment:** content verbatim; quotedFileContent (the
anchor); location in the transition convention `file#nearest-heading`,
derived by matching the quoted text against the pulled tabs (the master
Doc is ONE Drive file — comments.list is file-level, so tab/essay
attribution comes from the text match; ambiguous matches stored
unattributed and surfaced); the file association; comment id, author
identity, createdTime, replies (the id powers the resolve round-trip).
Storage home (guidance-like reviewable kind vs. evidence rows) decided at
build.
(3) **Resolve-with-receipt, never delete.** After ingestion, reply via
replies.create with action=resolved and a receipt ("Ingested into AuthorLM
as review evidence — <date>"). Resolved comments collapse out of the
margin (no orphan bloat after the next push) yet stay reopenable and
auditable in the Doc. Deletion rejected: irreversible destruction of the
author's words if ingestion ever has a bug.
(4) **No addressing prefix.** Process ALL open comments. The author
invented an "Authorlm:" prefix and omitted it on 2 of 5 comments in the
first live session — a prefix gate silently drops exactly the comments
written in deepest engagement. Future multi-collaborator filtering keys on
the comment's author identity (already in the API payload), not a textual
convention; classifying question/directive/reaction is the ingestion
step's job, not the author's.
First live harvest: 5 comments, 2026-08-03, ingested manually mid-session
(all five processed into the good-life.md revision batch, v70–v71).
**Built 2026-08-03** (same day — the friction was immediate): doc_comments
table (db.py); fetch_open_comments / locate_quote / ingest_comments /
resolve_comments_with_receipt (gdocs.py); pull harvests by default with a
"Comments to address" block and --no-comments escape (cli.py); skill rule
added; e2e Scenario DC covers location, verbatim storage, clamping,
dedupe, and the resolve round-trip against a fake Drive service.


## Tab reconciliation on pull (adopt / re-adopt hand-made Doc tabs)
Ratified and **built 2026-08-04**, prompted by the author drafting a new
essay directly as a Doc tab (redemption.md) — which the bridge could not
see: pulls only processed mapped tabs, unknown tabs risked being swallowed
into the preceding tab's section, and a later push would have created a
duplicate. Design: the Docs API (documents.get with tab content) is the
authority on what tabs exist; the stable key is the tab ID (survives
renames), titles are display names. On every pull, classify_tabs
(gdocs.py) reconciles: unknown '*.md' tab with no local file → ADOPTED
(new essay materializes on that same pull; output tells the author to
place it in toc.md); unknown '*.md' tab matching an existing file or link
→ RE-ADOPTED (relinked; the push-base is dropped, so content drift
surfaces as a CONFLICT — never the no-base Doc-wins default, because when
both sides hold content, choosing is the author's call); known id with a
changed title → renamed warning (titles must stay exact filenames);
duplicate of a live mapped tab, or two unknown tabs sharing a name →
ambiguous, left untouched; manifest/non-md tabs ignored. Every tab title
becomes a split boundary, killing the swallow hazard entirely.
Reconciliation is best-effort: a pull must survive a failed tab listing
(tabs_error warning). e2e: Scenario DC classify_tabs checks (6).


## Manuscript profiles + workspace Doc (declared context, second bridge)
Ratified via grilling and **built 2026-08-04**. Jurisdiction: profiles are
STATED CONTEXT about the project (market intelligence, positioning,
ambition) — neither prose (never observed: they live in `_profiles/`,
invisible to collect/extraction by the underscore rule), nor style law,
nor learned policy. Storage is files, not DB (the doc-editing requirement
made the bridge the dominant consumer, and the bridge speaks files); git
history is NOT assumed (manuscript dirs generally aren't repos).
Build: DocBridge descriptor generalizes the gdocs bridge — (meta_key,
root, doc_name, toc_sync, rich_manifest) — manuscript bridge = ("gdocs",
ms root, TOC sync on, rich manifest) vs workspace bridge =
("gdocs_workspace", _profiles/, toc sync OFF (tab order is free-form),
minimal manifest, "_profiles/" comment-location prefix). One code path
for push/pull/three-way/comments/adoption on both Docs; the workspace
Doc is created lazily on first `profile push`, in the same Drive folder.
Surfaces: CLI `profile list|show|set|push|pull` (set = whole-file
overwrite from stdin — profiles are declarations); MCP `get_profile`
(READ-ONLY, content verbatim — never compacted); one briefing line
(profiles + word counts). Skill rule: consult profiles FIRST for
positioning/audience/publisher questions; never binding on prose — the
path from insight to law is conversation → ratified style element or
policy (profiles inform the conversation that creates law, never bypass
ratification).
Deferred (author, 2026-08-04): capturing profile edit history in
AuthorLM's own version store — files are unversioned when the manuscript
dir isn't a git repo; build a small profile_versions capture (snapshot on
set/pull-change) if losing profile history ever hurts.

## Container tab (protected root identity, nested-everything)
Designed and built 2026-08-05, prompted by the live API finding that
root-level tabs reject ALL tabProperties updates with a server 500
(nested tabs accept everything — verified by matrix test; likely a
Google-side defect, undocumented). Design: one protected root tab per
bridge, titled exactly the manuscript's DB name (the identity of an
AuthorLM-managed Doc; renames are detected on pull and reported — the
API cannot fix a root tab). Body is a fixed sentinel ("Automatically
generated by AuthorLM. Do not delete."), rewritten on every push. All
content tabs nest under it — born movable — and ensure_master creates
new tabs there. The one-time migration of pre-existing root tabs is
manual (same root-tab bug); pull output lists the needed drags. The
walk/classify/toc-sync machinery needed zero changes: non-md tabs are
transparent to hierarchy by design. Depth note: Docs tabs nest 3 levels;
container + parts + essays consumes all three (author accepts,
2026-08-05). Manifest stays at true root (unmovable, harmless).
