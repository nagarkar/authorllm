"""The per-essay editing workflow (design: docs/plans/filter-lens-consolidation.md
§11.9 and §12 row 1).

- `core`: the record (workflows, instances, session turns, decisions,
  blurbs), the step gates, carry-over, switches, usage totals.
- `context`: `standing_context` and `instance_context`, what an engine is
  given for a turn.
- `contract`: the engine contract (`send(instance_context, message)`) and
  the registry the engines package fills.
- `cli`: `authorlm workflow …`.
- `landing`: the ruled units written into the essay file and pushed to its
  Doc tab through the Doc road (`gdocs.push_doc`, guarded). Rule: the
  rulings land when a step that rules text (construction, the wrap-up,
  the filters) ends by advance or skip, and on demand (`workflow land`,
  Land on the page); not on each decision, because units are reopened and
  redone within a step and a push per click would churn the file and the
  Doc. Each unit lands on its original text, never on a position; one
  whose original is gone from the file is not landed and waits for the
  author. Every landing run keeps the file before it; a reopen takes a
  landed unit back out.
- `roads`: the old roads (filter and lens runs, the beat loop, the Filters
  view's requests) as entry points that send to the workflow, or run with
  a skip of one sentence of reason, recorded.
- `history`: an essay's earlier (closed) instances, their letters, verdicts
  and discussion: `earlier_instances`, which the engines' read catalogue
  serves as the `earlier_instances` request.
"""


def earlier_instances(db, manuscript: dict, essay: str):
    """The essay's earlier workflow instances (workflow/history.py)."""
    from .history import earlier_instances as read
    return read(db, manuscript, essay)
