# Design backlog — ratified in conversation, awaiting build

Deferred deliberately (YAGNI); each was designed far enough to know its
shape. Build only when the need is felt.

## Ontology-pollution analyzer
A third analyzer on the collect hook (alongside extraction/aliases and,
later, style): diff prose against settled Concept Graph knowledge — notes
and edges — and propose incongruencies for the author's verdict. Reference
material: the "Metaphysical Guardrails" section of the author's style
profile (Fields possess zero qualities; Realm of Qualities is passive;
Nothingness and Dharma co-equal; redemption is seized, not received).
Jurisdiction rule: constrains what the text may CLAIM → graph, not style.

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

## Style drift detector (style v2)
The collect-hook analyzer the style system was designed around and then
deliberately deferred: detect (a) new style elements the author's prose
introduces, (b) incongruencies with ratified elements — per-aspect
comparison keyed by `style_elements.aspect`, guided by the free-text
`notes` (inspect/avoid hints). Findings arrive as proposals (accept =
ratify / accept-as-override = deliberate switch / reject = prose drifted,
fix the text). Build after living with the seeded guides long enough to
know what drift looks like.

