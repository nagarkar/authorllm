"""The per-essay editing workflow (design: docs/plans/filter-lens-consolidation.md
§11.9 and §12 row 1).

- `core`: the record (workflows, instances, session turns, decisions,
  blurbs), the step gates, carry-over, switches, usage totals.
- `context`: `standing_context` and `instance_context`, what an engine is
  given for a turn.
- `contract`: the engine contract (`send(instance_context, message)`) and
  the registry the engines package fills.
- `cli`: `authorlm workflow …`.
- `roads`: the old roads (filter and lens runs, the beat loop, the Filters
  view's requests) as entry points that send to the workflow, or run with
  a skip of one sentence of reason, recorded.
"""
