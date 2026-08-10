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

## First live run and the hardening pass (2026-08-10, same day)

The first manuscript-wide scan staged 38 proposals; 22 needed
rejection (58%). Diagnosis and the four fixes, ratified by the author
("clearly, we need to fix this"):

1. **Sermons zero-rule violated (6)** — the law WAS in the prompt; its
   exception clause ("threshold pieces are the only images…") parsed as
   an invitation and the lens proposed at every sermon boundary.
   Fixes: absolute per-file exclusions are now STRUCTURE, not prose —
   `illustrations = "none"` in toc.toml skips the file before any call
   (sermons.md carries it); the element was reworded to close the
   loophole (se-fb4bc → se-f0e00). Guarded again at arbitration: staged
   proposals on excluded files are cut deterministically.
2. **Duplicate metaphor homes (~8)** — per-chapter calls are blind to
   each other, so "concretized once" was unenforceable. Fix: the
   ARBITRATION PASS — one cheap-model call over all staged proposals
   after every scan, cutting duplicate homes (narrowing-auditor
   pattern). Arbiter/guard cuts carry their reason in row metadata and
   are NEVER author evidence.
3. **In-image text violations (5)** — the placement lens never saw the
   render law. Fix: the illustration-aspect law is composed into the
   placement prompt; descriptions are born renderable.
4. **Over-pacing (flex ceiling everywhere)** — fix: a DETERMINISTIC
   budget, arithmetic not judgment: per chapter,
   `max(1, words // 750) − existing tags − open proposals`; at zero the
   file is skipped without a call; the lens ranks strongest-first and
   staging truncates at the budget.

Also hardened: scan dedupes against proposals in EVERY state — what
the author rejected never resurrects.

Three of the four fixes are deterministic; only arbitration spends
tokens, exactly where cross-chapter judgment is genuinely needed (the
sweep framework's token-bill doctrine, applied).

## Status: SHIPPED 2026-08-10 (hardened same day)

placement.py (scan / arbitrate / sweep_stale / decide /
placement_law), the illus_proposals table, CLI + MCP surfaces, pick
evidence, and the verdict-matrix tests (stage, idempotence, budget
skip, toc exclusion, arbiter cuts without evidence, no-resurrect,
modified acceptance with diff-as-evidence, rejection verbatim, stale
rule).
