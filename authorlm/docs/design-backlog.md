# Design backlog — ratified in conversation, awaiting build

Deferred deliberately (YAGNI); each was designed far enough to know its
shape. Build only when the need is felt.

## Bulk exception triage (`concept triage --bulk`)
Ratified 2026-08-08. Author: triage "is a lot more work than I
anticipated… triage tends to be done in bulk and not after every
collect." Design principle (from the HCI evidence — recognition over
recall, GOMS action counts, email-triage batch behavior — and from the
live session that processed 245 candidates in one author message):
**design for the skewed verdict distribution** — real triage runs ~90%
one verdict (219 retire / 26 keep on 2026-08-08), so the interface must
cost one action per EXCEPTION plus one commit, never one action per
item.
Shape: `authorlm concept triage --bulk` prints the pending pile as a
NUMBERED list grouped by file (kind-tagged, like the sweep report),
then takes a single line: `keep 3 12 17, retire rest` (also
`retire 4 9, keep rest`, `confirm all-kind question`, ranges `5-12`).
Verdicts recorded as ordinary triage evidence (retirements feed the
never-extract exemplars). Same grammar for the edges lane. No new
dependencies, no state — it is a display+parser over the existing
triage machinery.
DEFERRED, with trigger: a minimal local web UI (`authorlm triage --ui`,
stdlib http.server, one HTML table with checkboxes + kind/file filters,
POSTing to the same api functions) — build ONLY if the steady-state
pile stays large after the recurrence bar + hygiene gates + quiet
drops have throttled the supply side; the 245-item pile was the old
regime's backlog, and the new-regime flow may be small enough that
chat + --bulk suffice. Anything heavier than a localhost page: never.

## Concept admission criteria (extraction quality)
Raised 2026-08-07 (author, verbatim: "These are just phrases that I'm
using is just normal language. We really need to work on how we define
and identify concepts and whether or not we find new ones. We seem to
be inventing new ones out of thin air.") — after extraction captured
'great treasure' (the Aladdin simile) and 'The Solution – is Choice'
(a sentence of the flash paragraph) as concept hypotheses. Grounding
hygiene cannot catch these: they ARE verbatim text; the failure is
SIGNIFICANCE, not grounding (the load-bearing-vs-incidental judgment
deliberately deferred in the sweep-framework design).
Already true and load-bearing: the author's retirements feed
triage_feedback ("never extract these" exemplars ride every future
extraction prompt), so today's two verdicts already teach the extractor.
GRILLED AND RATIFIED 2026-08-08 — ready to build:
- CRITERION (author, verbatim law for the prompt): "Concepts are words
  or groups of words that are used multiple times. A phrase is
  something that might occur once or twice, but isn't a continuing
  motif." ('The Problem – is Choice': twice in one paragraph → phrase.
  'Field of Choice': across many essays → concept.) The index test was
  considered and rejected (an index can hold a once-used phrase).
- BAR: occurrences in the same paragraph collapse to one context;
  admission needs ≥2 paragraph-contexts spanning ≥2 sections (headings)
  or ≥2 files. Essay-local structures (Persistence Theorem) pass via
  the section spread; same-paragraph doubles fail.
- KINDS: bar applies to concept | metaphor | example. Exempt:
  historical_reference, mathematical_construct, syllogism (one-shot by
  nature), question/objection (coined names cannot recur; only 44 nodes
  total — triage stays their gate, uncapped).
- NEWBORNS: hard drop below the bar, self-healing — the next mention
  arrives in changed text, extraction re-proposes, the full-corpus
  count then passes. Bar-drops are counted+named in the extract report,
  NEVER fed to triage_feedback (the bar rejected them, not the author),
  never banned. Author-declared concepts bypass the bar entirely.
- ENFORCEMENT: prompt-as-advice, gate-as-law. The deterministic gate
  lives in extraction post-processing (like the edge-grounding gate)
  and counts against the WHOLE current manuscript (incremental payloads
  can't see recurrence; the model cannot count — the system does). No
  index: on-demand regex scan, ~20-50ms per pass (<1% of the LLM call),
  against the same in-memory files the extractor mined. A persisted
  n-gram index is deferred until full-vocabulary statistics or 10x
  corpus growth demand it.
- RETROACTIVE: `sweep hygiene` gains a "below the recurrence bar"
  section over unconfirmed extracted concept/metaphor/example nodes —
  report-only; `--apply` bulk-retires (recorded as bar-retirements,
  not author rejections). Confirmed concepts untouched.

## Sweep framework: auditors, lenses, hygiene, registration door
Designed 2026-08-05 from the EditorLLM research
(docs/editorllm-research.md) run through the chat-first filter: AuthorLM's
verdict surface is chat, so app affordances (anchored margin suggestions,
job consoles, badges, agent frameworks) die; what survives is exactly the
set of deterministic sweep verbs writing findings into the existing
stores, with chat as the triage surface.

TAXONOMY (ratified in conversation 2026-08-05):
- PURE AUDITORS — zero tokens. Deterministic checks over the DB and
  text: mention grounding, orphaned terminology (alias used, concept
  retired), toc-unlisted, dangling image refs, export readiness
  (`export --check`). The prerequisite-gap engine already is one.
- NARROWING AUDITORS — deterministic-first, cheap-model core. The
  pipeline extracts every input deterministically (changed paragraphs ×
  concepts mentioned in them × their ratified notes/edges/style
  elements) and calls the LLM (gemini flash tier, record/replay cached)
  ONLY for the final semantic judgment on assembled (passage, claim)
  pairs, tight schema, never "here's the file, find problems."
  Token-efficiency rule (author): ask the model only for what we CANNOT
  get deterministically. Instances: ontology-pollution analyzer, style
  drift detector (entries below).
- LENSES — prompt-first, judgment/world-knowledge sweeps. A ratified
  prompt stored in AuthorLM + context from the DB; run on demand, not on
  the collect hook. Deep ones (tether, "Kantian objections") run as
  Claude subagents that read context via MCP and register findings
  through the door below. The auditor/lens boundary is the reference
  standard: auditors check against a reference maintained IN AuthorLM;
  a lens's reference lives only in its prompt.

HYGIENE FILTERS — BUILT 2026-08-05 (hygiene.py; edge-grounding gate in
extraction.py; staleness at collect in api.py; `authorlm sweep hygiene
[--apply]` in the CLI). One design refinement discovered at build time:
extracted CONCEPTS are deliberately NOT gated at admission — an
unmentioned concept staying a declared hypothesis for triage is ratified
behavior (the author sometimes confirms paraphrase-named abstractions,
e.g. coined question/objection names; e2e scenario E encodes this), so
concept groundedness is a sweep-report concern, while EDGES are gated at
the door. Original design for reference:
(gates at every LLM→store boundary — protect author
attention and evidence purity; an ungrounded suggestion the author
rejects teaches the policy learner something false):
- Run at GENERATION TIME, not periodically: filter guide output before
  suggestions are stored, extraction output before hypotheses are
  created, episode analysis before decisions are recorded. Plus one
  retroactive `authorlm sweep hygiene` pass over the existing piles, and
  a staleness check at collect (suggestion conditioned on since-changed
  text → marked stale, not shown).
- The core filters are DETERMINISTIC (string ops, zero tokens): quoted
  text must exist verbatim (whitespace/case-normalized) in the
  manuscript; no-op detection (suggested introduction/rewrite already
  present); concept/alias mention grounding (word-boundary scan — the
  realization scanner already does this); edge co-occurrence check;
  inferred episode decisions must reference real transitions. Dropped
  counts are logged as a prompt-quality signal.
- NOT deterministic (deferred; stays with triage/chat): paraphrase
  grounding, semantic no-ops, load-bearing-vs-incidental judgment.
REGISTRATION DOOR: one verb (CLI + MCP) through which findings from
outside AuthorLM (Claude subagent sweeps) enter the SAME guidance/
proposal stores, passing the same hygiene validation — one evidence
loop, no forks. Routing by jurisdiction: prose advice → guidance
suggestions (review loop); conflicts with settled knowledge → proposals;
law candidates → style/policy proposals.

## Ontology-pollution analyzer (narrowing auditor)
A third analyzer on the collect hook (alongside extraction/aliases and,
later, style): diff prose against settled Concept Graph knowledge — notes
and edges — and propose incongruencies for the author's verdict. Reference
material: the "Metaphysical Guardrails" section of the author's style
profile (Fields possess zero qualities; Realm of Qualities is passive;
Nothingness and Dharma co-equal; redemption is seized, not received).
Jurisdiction rule: constrains what the text may CLAIM → graph, not style.
Pipeline (2026-08-05): deterministic narrowing — changed paragraphs only
(collect knows them), concepts mentioned in each (realization scanner),
their confirmed notes + refutes/depends_on edges — then one cheap-model
call per batch judging (passage, ratified claims) pairs: consistent |
incongruent + verbatim quote; grounding-filtered; findings → proposals.

## Tether — the External Anchor (lens; Claude subagent)
From the EditorLLM research (its most philosophy-native feature; role
prompt at EditorLLM src/prompts/agents/tether-system-role.md is worth
reading before building). Checks the manuscript against the EXTERNAL
scholarly/historical/scientific record — the opposite reference corpus
from the ontology analyzer. Constitutionally forbidden to correct the
author's framework from outside. Finding taxonomy: ERROR (a factual
misstatement about a cited source/figure — e.g. what Epictetus actually
said), CONTROVERSIAL (internally consistent but contested externally —
flag for awareness, never recommend removal), ALIGNMENT (scholarship
that supports or parallels the author's position — the half a
philosophy author actually wants: citations they didn't know they had).
Runs on demand per file as a Claude subagent (needs world knowledge +
judgment; too expensive and too rare for the collect hook): reads the
file + the ratified concept notes for concepts the file touches (so
claims are checked against the author's settled definitions, which
EditorLLM structurally could not do) + the market profile for audience
stakes; registers findings through the registration door with kind
tether:{error|controversial|alignment}, each with a verbatim quote
(hygiene-gated) and a source. Verdicts flow through the ordinary review
loop; an accepted ALIGNMENT naturally becomes a historical_reference
concept + citation candidate.

## Version-delta queries and re-proposal guards
Goal (author, 2026-07-31): after each collect, see what the version change
introduced, and stop the extractor re-proposing already-judged material.
Grilled to a minimal scope; Time-Machine ambitions deliberately cut.
Build scope: (1) `graph_changes(from_v, to_v)` in api.py, exposed as an
MCP tool and `authorlm history changes` in the CLI — compact delta of
births between two manuscript versions: concepts realized (via
metadata.realized_version, carried by 341/447 nodes), concepts created,
edges created, proposals opened (created_at joined against
manuscript_versions timestamps). Responses are compact projections —
{name, kind, introduced_in} for concepts, "From —relation→ To" strings
for edges, plus a counts block; never raw rows (row boilerplate is ~half
of every record; a full get_briefing hit 622KB / collect_revision 61KB
this session). (2) Widen the re-proposal guards where the v45/v46 audit
found the real leaks (47 proposals / 88 edges audited; zero verbatim
resurrections — the content_hash guard and triage_feedback
(extraction.py:147) both already work): (a) proposal dedup key widened
from verbatim content_hash to (kind, target), surfaced at triage as "a
similar proposal was dismissed before"; (b) rejected edge *pairs* (not
just exact triples) fed into triage_feedback, and edge-triage items
annotated "this pair was previously rejected as <relation>".
Cut, with reasons: the append-only graph_events change log and
concept_provenance(name) — history-of-state solves a problem the author
doesn't have; churn prevention lives at extraction time, not query time.
If ever revived, the load-bearing finding: name↔node is NOT stable across
time (add_concept revives retired names in place, wiping introduced_in —
concepts.py:23; merge_concepts remaps a name to a different node as an
alias), so provenance must narrate a NAME's lifetimes (born / retired /
merged / revived, each with node-id handles), not a node's — and pre-log
history is only partially reconstructible, from triage evidence records.
Also cut: point-in-time file text (manuscript_versions.files already
stores full snapshots; `authorlm diff` covers it) and growth-curve
reports (never asked for twice). Ratified 2026-07-31; build when the
churn annoys again.

## Style drift detector (style v2; narrowing auditor)
The collect-hook analyzer the style system was designed around and then
deliberately deferred: detect (a) new style elements the author's prose
introduces, (b) incongruencies with ratified elements — per-aspect
comparison keyed by `style_elements.aspect`, guided by the free-text
`notes` (inspect/avoid hints). Findings arrive as proposals (accept =
ratify / accept-as-override = deliberate switch / reject = prose drifted,
fix the text). Build after living with the seeded guides long enough to
know what drift looks like.
Pipeline (2026-08-05, same shape as the ontology analyzer): deterministic
narrowing — changed paragraphs only, the file's effective style elements
(statements + notes) grouped per aspect — then one cheap-model call
judging (paragraph, aspect law) pairs: conforming | drift + verbatim
quote + which element; grounding-filtered. Incremental like extraction
(only files changed since last run). This SUPERSEDES the EditorLLM-style
"regenerate the whole guide" idea (style propose): per-aspect drift
findings as proposals beat overwrite-then-review — never auto-ratify.
Note: this subsumes evidence the author's hand-edits already carry;
detected drift the author *rejects* (fixes the prose) is style-law
reinforcement evidence for free.



## Profile version capture
Extracted from the (built) profiles design when it graduated to the
design record: profiles live as files in _profiles/ and are unversioned
when the manuscript dir isn't a git repo (the usual case — manuscript
dirs generally aren't repos; author, 2026-08-04). If losing profile
history ever hurts, build a small snapshot-on-change capture (on
`profile set` and on pull-changed) into AuthorLM's own store.

## Constructed manuscript export (.docx / .epub for publishing + listening)
Grilled 2026-08-05. Goal: constructed, write-only single-manuscript
artifacts importable directly into Vellum/Atticus/KDP, and an EPUB for
continuous screen-reader listening (Apple Books Read-All / Voice Dream).
DECIDED: build on export.py's create-manuscript (machinery-free by
construction — it concatenates local file CONTENTS in TOC order, so no
filename headings, no manifest/container pollution). docx and epub are
generated LOCALLY via pandoc (installed 2026-08-05) from that
concatenation; illustrations embed natively from _illustrations/
relative paths — single image source on disk, no Drive copies, export
works offline. The Google Doc plays no part in the publishing path
(a Drive-export engine was considered and rejected: it would duplicate
every image across disk and Drive; author accepted a local publishing
toolchain instead). One-time verification still needed: pandoc's docx
imports into Vellum/Atticus with clean H1 chapter splits.
DECIDED (Q2, author 2026-08-05: "start with simple export shape as
proposed"): no heading transformation (files' H1=chapter grammar already
matches Vellum/Atticus H1-split; part intros import as short chapters
converted to Part breaks in-tool); title.md leads as front matter; no
inline TOC.
DECIDED (command surface): a NEW `authorlm export` verb family, not an
extension of create-manuscript ("new verb sounds good"). Settings are
deterministic tooling, not chat state (author: "we should have some
deterministic tooling in cli/mcp to deal with export settings and
commands so we can use the chat interface here without burning too many
tokens"): per-manuscript settings file `_exports/settings.toml`
(observation-invisible, author-editable), managed via
`authorlm export set <key> <value>` / `export show`, consumed by
`authorlm export docx|epub|md`. Settings: title, author byline,
illustration variant (slots|images|stripped), reference-docx path, epub
metadata (language, cover), output dir. An MCP read-only mirror
(get_export_settings) only if chat ever needs it.
DECIDED (author 2026-08-05): `_assets/` in the manuscript root holds
author-supplied publishing artifacts — cover image, back image, a
reference.docx when one exists. Observation-invisible (underscore),
never generated, never pruned; the split from _illustrations/
(machine-generated candidates) and _exports/ (regenerable outputs) is
inputs vs outputs. Settings reference them root-relative
(cover_image = "_assets/cover.jpeg").
OPEN: pandoc reference-docx template (fonts/margins) — generate into
_assets/ and restyle in Word only if the Vellum import test warrants.
DECIDED (author 2026-08-23, "ebooks and pdfs ... restricted to specified
chapters, not the whole manuscript"): `authorlm export <fmt> --chapters
a,b` builds a part of the book through the same pipeline as the whole —
same variant, same illustration resolution, images embedded exactly as
in the full build. Naming a parent names the part it heads: the chapter
carries its TOC descendants (`--chapters ascending` = ascending.md plus
the seven chapters filed under it). A part-build's filename carries the
selection (`<Title> - ascending+discernment+good-choice+more.pdf`) so it
lands beside the whole-book artifacts and never overwrites them. An
unknown chapter name raises rather than exporting an empty book. `pdf`
joins md/docx/epub, engine `xelatex` (settings: pdf_engine, pdf_font) —
pdflatex dies on the manuscript's arrows and diacritics.
DECIDED (author 2026-08-23; declarative-profile refactor 2026-08-24):
PDF and EPUB boundaries come from manuscript files, not H1 headings. The
transient Pandoc input wraps each TOC-ordered file in a semantic div, so an
essay's pre-heading epigraph moves with the essay. Packaged Pandoc profiles
under `authorlm/publication/` own presentation: shared Lua maps semantic
roles, LaTeX sets PDF title size and page breaks, and CSS sets EPUB title size
and reflowable breaks. H1/H2/H3 remain hierarchy only; no writer-specific
markup is injected by Python.

## Illustration pipeline (LLM-generated images: disk, Obsidian, export)
Grilled 2026-08-05. Motivating failure: images pasted in the Doc are
silently dropped by the markdown bridge (metaphysic.md's dangling
![][image1]/![][image2] refs, found on the 2026-08-05 pull).
DECIDED: the `[Illustration: prompt]` tag IS the spec — plain prose,
survives push/pull, never consumed or replaced; the registry is derived
by scanning the manuscript (no new DB state). The working Doc stays
text-only — no images pushed; art is reviewed in Obsidian and in the
exported docx/epub (author 2026-08-05: "I am ok if I can't see the
images in google docs"). New `authorlm illus` verb family:
list / render <fragment> / pick / prune. Rendering goes through
litellm.image_generation (llm.py already routes litellm; v1.92.0 has
the API) with a config `image_model` string — start with Gemini flash
image; provider swap is a config change; if litellm lags a model, a
direct-REST call hides behind the same interface (llm.py's existing
"direct" provider pattern). Illustration style = `figure`-aspect style
elements in the EXISTING guide tree — inheritance, file-local
overrides, `overrides` displacement all apply unchanged; render
prepends the effective figure law to the prompt. Output lands in
_illustrations/ (observation-invisible); prompt, figure elements,
model, and date are stamped into PNG metadata so files stay
self-describing (author: keep the prompt, don't lose it).
DECIDED (candidates + staleness): a slot holds MANY candidates, not one
image per prompt — filename `<slug>-<deschash8>-<stylehash4>-<NN>.png`.
Re-rendering the same prompt appends candidate NN+1; nothing is ever
overwritten; `pick` chooses which candidate the embed points at;
`prune` deletes unpicked candidates. Staleness is filename-hash
comparison only (no image parsing): the desc-hash catches prompt edits,
the style-hash catches new or changed ratified figure rules — adding an
illustration rule marks every slot stale (advisory: the picked image
keeps working until re-rendered). The stylehash is the hash of the
EXACT effective-figure-law text prepended to the render prompt for that
file (ratified figure elements after guide-tree resolution, statements
plus prompt-feeding notes, in resolution order) — "hash what the model
sees", so a reworded rule changes it and a candidate rule does not.
PNG metadata is a human-facing copy, never the mechanism.
DECIDED (approval = continuity): an explicit `pick` is the author's
approval verdict and PINS the candidate — later renders (including
after a style change) add candidates but NEVER auto-swap a pinned pick;
the author compares and re-picks. Only a never-picked slot defaults to
the newest render. `illus list` distinguishes "stale (pinned under
older style law)" from "stale (unreviewed)". Old candidates are never
deleted except by explicit `prune`.
DECIDED (author 2026-08-05: "Yes on embed-line machinery, pull warning
on dangling images"): embed line `![](_illustrations/….png)` on the
line after the tag (Obsidian renders it natively) as DERIVED
machinery — stripped on push, re-inserted deterministically after pull
by rescanning slots; collect never acts on tags (rendering is an
explicit CLI act, observation stays pure). Pull strips Google's
dangling ![][imageN] refs and warns, so Doc-pasted images can't
corrupt local files again.
DECIDED (author 2026-08-06): pull/collect output includes an
`illustrations` section listing NEW (unrendered) and newly-STALE slots
found in the collected text — "new illustrations found: <tags>" — so
the conversational surface sees it and asks the author whether to
render, instead of the author having to remember to ask (author: "I'd
rather not ask for you to render the images (I will have to
remember)"). Rendering itself stays an explicit, consented act; collect
still never generates anything.
DECIDED (author 2026-08-06): `illus render --from <N>` for continuity
ACROSS a style change — image-conditioned generation (Gemini flash
image supports image+text editing): the liked candidate is passed as
the input image with the new effective figure law, so the re-render
evolves the approved composition instead of starting fresh. Plain
`render` stays text-only (fresh take). Candidates are numbered per
slot (01, 02, …); `--from 2` names candidate 2.
DECIDED (author 2026-08-06): optional caption syntax —
`[Illustration: prompt | caption: reader-facing text]`. The caption
(only) prints under the image in exports; slots without one get no
caption. The desc-hash covers the prompt part only, so editing a
caption never marks a slot stale.
DECIDED (author 2026-08-06): image shape/aspect is a illustration-aspect
style rule (e.g. "illustrations are landscape 3:2") — inheritable,
overridable, folded into the stylehash; model default until declared.
Build-time details (assistant-decided, veto welcome): PNG metadata via
raw tEXt chunk (zero deps); tags are single-line paragraphs;
_illustrations/ is observation-ignored like _concepts/; exports land
in _exports/ only; pandoc default styling until one Vellum import
verification. Obsidian needs no machinery: the manuscript folder IS
the vault (export-obsidian's design), so relative embeds render as-is.
Future, deliberately deferred: pick/reject verdicts as evidence
seeding figure-style policies, same loop as prose.

## Proposal learning loop — remaining queues
The loop shipped 2026-08-20 for `note_update`, `alias`, and illustrations
(docs/design-record.md). These queues have no acute pain and register a
`LoopSpec` when they do — no loop changes needed, just a registry entry with
a `render`, a `dedupe_render`, an aspect and a source:
`vanished` (54 open), `revival` (21), `edge_reproposal` (11),
`variant_of_retired` (5), `incongruence`, `belief_revival`; critique items,
critique staged edits, improvements, and concept/edge triage.

Also deferred, per the author: recursion beyond level 1 — distilling beliefs
ABOUT beliefs. It is a fixed point rather than a regress (the belief store
conforms to the same queue interface), so depth is chosen purely by which
distillers are wired; level 1 today means no distiller attached to the belief
queue. Author, verbatim: "I can keep going, 'I am that I am that I am… and so
on recursively' and so far we don't need that sort of infinite
self-referential recursion, but we do need the ability to have that recursion
and build new levels of recursion in the future."

Also cut, with a standing reason: a VOLUME-ADAPTIVE promotion threshold.
Measurement (below) showed belief count already grows logarithmically once
matching works, and an adaptive bar would mask a matching regression —
lowering the bar exactly when volume rose is how that bug becomes permanent.
