"""The per-essay editing workflow (design: docs/plans/filter-lens-consolidation.md
§11.9 and §12 row 1).

- `core`: the record (workflows, instances, session turns, decisions,
  blurbs), the step gates, carry-over, switches, usage totals.
- `context`: `standing_context` and `instance_context`, what an engine is
  given for a turn.
- `contract`: the engine contract (`send(instance_context, message)`) and
  the registry the engines package fills.
- `cli`: `authorlm workflow …`.
"""
