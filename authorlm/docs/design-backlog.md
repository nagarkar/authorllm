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

## Style drift detector (style v2)
The collect-hook analyzer the style system was designed around and then
deliberately deferred: detect (a) new style elements the author's prose
introduces, (b) incongruencies with ratified elements — per-aspect
comparison keyed by `style_elements.aspect`, guided by the free-text
`notes` (inspect/avoid hints). Findings arrive as proposals (accept =
ratify / accept-as-override = deliberate switch / reject = prose drifted,
fix the text). Build after living with the seeded guides long enough to
know what drift looks like.

