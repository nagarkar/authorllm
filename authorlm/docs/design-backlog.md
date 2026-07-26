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

## Style drift detector (style v2)
The collect-hook analyzer the style system was designed around and then
deliberately deferred: detect (a) new style elements the author's prose
introduces, (b) incongruencies with ratified elements — per-aspect
comparison keyed by `style_elements.aspect`, guided by the free-text
`notes` (inspect/avoid hints). Findings arrive as proposals (accept =
ratify / accept-as-override = deliberate switch / reject = prose drifted,
fix the text). Build after living with the seeded guides long enough to
know what drift looks like.

