# Illustration placement pipeline — spot-finding, staging, triage

Grilled and ratified 2026-08-10. The author's ask, condensed: seed
criteria for where illustrations belong, identify spots against them,
pull the guidance, propose tags/revisions — with human triage before
anything touches the text, sync after approval, and rendering engaged
without manual prompting.

## The law (ratified as style elements, aspect `illustration-placement`)

A new aspect, consumed ONLY by the spot-finder — never composed into
render prompts (the figure/illustration separation, extended: prose
law, image law, and placement law are three non-mixing streams).

- se-ef58a (house): three criteria — (1) spatial/structural arguments
  words serialize poorly; (2) a recurring metaphor concretized once at
  its strongest occurrence; (3) chapter-threshold pieces. The criterion
  invoked is recorded with every proposal.
- se-a25f5 (house): pacing — ~1 per 1,500 words average, flexing
  toward 2; no per-essay cap (author: long essays scale, no special
  exceptions).
- se-fb4bc (Sermons guide): zero inside the dramatic text.
- se-2ed8f (house): structural-only in Appendix/Metaphysic; decorative
  images acceptable in parable and narrative passages.

## Architecture: extraction's generation side + margin threads' approval side

- Spot-finder = cheap-model pipeline (one call per main-matter chapter;
  front matter never scanned; per-guide law rides in the prompt).
- Proposals stage in the `illus_proposals` TABLE — never in text, never
  in the Doc. The genuine novelty of this loop: it is the first where
  cheap-model output graduates into MANUSCRIPT TEXT — hence staging and
  explicit per-item triage, no bulk auto-apply.
- Anchor = the verbatim final sentence of the paragraph the image
  follows, validated deterministically at staging; unverifiable
  proposals are dropped and counted, never guessed. Anchors that later
  change mark the proposal `stale` (the margin exactness rule).
- Doc API constraint (checked 2026-08-10): third-party apps cannot
  create ANCHORED comments on Google Docs (kix anchors are internal),
  so in-Doc proposal visibility is off the table by design; the author
  declined unanchored breadcrumb comments.

## Surfaces

- CLI: `illus scan [file]` (stage), `illus triage` (list; verdicts via
  `--accept N…`, `--accept-all-except N…`, `--revise N "desc"`,
  `--reject N --reason "…"`). Numbers are positions in the current
  open-proposal listing.
- MCP: `scan_illustrations`, `triage_illustrations` — chat triage
  without shell round-trips. Chat stays sovereign: the collaborator may
  hand-draft proposals into the same staging table.
- On accept: tag written into the local file after its anchor (or the
  existing tag's description rewritten, for revision proposals); then
  the normal collect → push → render machinery runs WITHOUT prompting
  (skill duty; extends the ratified first-render-no-ask rule).

## The learning loop (the whole point)

Every verdict is evidence (`illus_triage`): accepted (weak confirm),
rejected + reason verbatim (strong negative), revised = modified
acceptance carrying the original → final diff (the richest). The render
side feeds the same stream (`illus_render`: picks; regenerate reasons).
Explanations flow to the SAME scoped, decline-by-default distiller as
margin threads — ≥2 independent instances before a candidate, narrowest
honest scope, author ratifies, and the ratified rule lands as
`illustration-placement` law (placement/description patterns) or
`illustration` law (visual patterns). The next scan pulls the updated
law, so a correction never needs repeating. Forming patterns are
surfaced at triage time unprompted (the margin-learnings duty,
extended).

## Status: SHIPPED 2026-08-10

placement.py (scan / sweep_stale / decide / placement_law), the
illus_proposals table, CLI + MCP surfaces, pick evidence, and the
verdict-matrix tests (stage, idempotence, modified acceptance with
diff-as-evidence, rejection verbatim, stale rule).
