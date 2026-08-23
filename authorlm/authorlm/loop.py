"""The proposal learning loop, once, for every triage queue.

Ratified 2026-08-20 (docs/design-backlog.md, "Proposal learning loop").

Before this module each stage of the loop existed exactly once, in a
different queue: the screen only in placement.py, the distiller only for
margin threads, generator feedback only in extraction.py, dedup only as an
exact content hash. Nothing was wired to `knowledge_proposals` at all, so
41 dismissals produced evidence that nothing ever read.

The four stages here are queue-agnostic. What varies per queue is small
enough to be data — how to render an item, and where its law lives — so it
is a frozen `LoopSpec` in a registry rather than a class hierarchy (651
module functions and 8 classes in this package; the seam matches the
grain). What accepting a proposal MEANS stays where it was: `adopt()` and
`describe()` in proposals.py are queue semantics, not loop semantics, and
this module never calls them.

Two invariants hold the loop honest:

1. **Machine cuts are never author evidence.** A belief that counted its
   own firings would raise its own confidence and cut harder — the loop
   would train on its own output. Cuts carry attribution in metadata only.

2. **Cuts fold, they never vanish.** Because of (1) a belief's
   `supporting` cannot move once it is screening, so the ONLY event that
   can ever increment `contradicting` is the author expanding a fold and
   adopting something the screen cut. Hide the folds and auto-promotion
   becomes irreversible in practice while still looking reversible in the
   schema.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .db import Database, loads

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Registered in prompt_registry as "triage-screen".
def screen_system() -> str:
    return (PROMPTS_DIR / "triage-screen.md").read_text(encoding="utf-8")


@dataclass(frozen=True)
class LoopSpec:
    """Everything the loop needs to know about one queue. `render` is the
    only callable: the firewall compares rendered text and the screen
    judges it, so a queue joins the loop by saying how its rows read."""

    key: str                       # registry key, e.g. "proposals/note_update"
    table: str                     # staging table holding the rows
    aspect: str                    # style_laws.aspect carrying ratified law
    source: str                    # editorial_beliefs.source for this queue
    evidence_type: str             # evidence.evidence_type for verdicts
    render: Callable[[dict], str]  # row -> one line, for the screen
    open_state: str = "open"
    cut_state: str = "dismissed"
    # Discriminator within `table` when several specs share it. Without it,
    # `folded` reported every screen cut in the manuscript under EVERY
    # proposal spec — the same group listed six times, each resolved against
    # a different aspect, so five of six rendered as "(law retired)".
    kind: str | None = None
    # Deterministic reconciliation against the world as it stands NOW.
    # (verdict, detail, rebased_payload); verdict is satisfied | orphan |
    # stale | live. A different axis from the firewall — see RECONCILE.
    reconcile: Callable[..., tuple] | None = None
    # Text the firewall compares. It must contain ONLY what distinguishes
    # two rows: `render` carries a summary line and the CURRENT note, which
    # are identical for every proposal against the same concept, and that
    # shared boilerplate floats every pair's overlap toward 1.0 — it scored
    # two unrelated re-definitions of 'The Dead' at 0.92. Defaults to
    # `render` for queues whose rendering is already all signal.
    dedupe_render: Callable[[dict], str] | None = None

    def dedupe_text(self, row: dict) -> str:
        return (self.dedupe_render or self.render)(row)


# --------------------------------------------------------------- ① firewall

_WORD = re.compile(r"[a-z0-9]+")
# Function words carry no signal about whether two proposals say the same
# thing, and leaving them in floats every pair's similarity toward a
# meaningless baseline.
_STOP = frozenset("""a an the and or but of to in on at by for from with as is
are was were be been being it its this that these those there here which who
whom whose what when where why how all any both each few more most other some
such no nor not only own same so than too very can will just should now into
""".split())


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOP}


def similarity(a: str, b: str) -> float:
    """Jaccard overlap of content words. Deterministic and free — the
    firewall runs on every extraction over the whole queue, so it must
    never cost a model call."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# Measured, not guessed: tools/simulate_loop.py replays the live corpus at
# every threshold and prints the pairs each one would collapse.
#
# At 0.72, 76/849 note_update and 8/91 alias proposals are suppressed (~9%),
# and every pair sampled in the 0.72-0.80 risk zone was a genuine restatement
# ("Unredeemed entities…" vs "Unredeemed souls…"; the same sentence differing
# by an em-dash). The first FALSE positive appears at 0.71 — "the spiritual
# realm, equally existent with the corporeal realm" against "…with the Realm
# of Qualities", which differ in exactly the noun phrase that matters. 0.72
# sits just above that. Raising it to 0.85 would lose 42 true restatements.
NEAR_DUPLICATE = 0.72


def near_duplicate(candidate: str, existing: list[tuple[str, str]],
                   threshold: float = NEAR_DUPLICATE) -> tuple[str, float] | None:
    """(id, score) of the most similar prior row above `threshold`, else
    None. `existing` is (id, rendered_text) pairs — open AND already
    settled, because the point is to stop re-asking a question the author
    has answered, in whatever words it comes back."""
    best: tuple[str, float] | None = None
    for row_id, text in existing:
        score = similarity(candidate, text)
        if score >= threshold and (best is None or score > best[1]):
            best = (row_id, score)
    return best


# --------------------------------------------- ①b reconcile with the world

# The firewall asks "have I been told this before?" — proposal against
# proposal. Reconciliation asks a different question: "is this still a
# question?" — proposal against the world it was raised against. An
# unreconciled queue actively lies: every row renders as "current: X →
# proposed: Y" using the X it captured when it was raised.
#
# `placement.sweep_stale` has always done this for illustration anchors.
# Nothing did it for knowledge proposals, so 42% of a live 769-item queue
# was already answered — notes since edited to exactly what was proposed,
# aliases whose endpoint had been retired, `vanished` proposals for
# concepts already retired.
#
# STALE is deliberately NOT a resolution. A stale proposal is re-based, not
# dismissed: only 19 of 347 collapse once compared against the current
# note, and among the rest are proposals that would REGRESS the note --
# they read as improvements only against the stale base they carry.

SATISFIED, ORPHAN, STALE, LIVE = "satisfied", "orphan", "stale", "live"


def _norm(text: str | None) -> str:
    return " ".join((text or "").split()).strip().lower()


def _node(db: Database, target: str) -> dict | None:
    row = db.one("SELECT * FROM concept_nodes WHERE id = ?", (target,))
    return dict(row) if row else None


def _by_name(db: Database, manuscript_id: str, name: str | None) -> dict | None:
    row = db.one(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? "
        "AND lower(name) = lower(?)", (manuscript_id, name or ""))
    return dict(row) if row else None


def _rc_note_update(db, mid, row, payload):
    node = _node(db, row["target"])
    if node is None:
        return ORPHAN, "the concept no longer exists", None
    if node["status"] == "retired":
        return ORPHAN, f"'{node['name']}' has been retired", None
    current = _norm(node["notes"])
    if _norm(payload.get("proposed_note")) == current:
        return SATISFIED, "the note already says exactly this", None
    if _norm(payload.get("current_note")) != current:
        return (STALE, "the note changed since this was raised",
                {**payload, "current_note": node["notes"]})
    return LIVE, None, None


def _rc_revival(db, mid, row, payload):
    node = _node(db, row["target"])
    if node is None:
        return ORPHAN, "the concept no longer exists", None
    if node["status"] != "retired":
        return SATISFIED, f"'{node['name']}' is live again", None
    return LIVE, None, None


def _rc_vanished(db, mid, row, payload):
    node = _node(db, row["target"])
    if node is None:
        return ORPHAN, "the concept no longer exists", None
    if node["status"] == "retired":
        return SATISFIED, f"'{node['name']}' is already retired", None
    return LIVE, None, None


def _rc_alias(db, mid, row, payload):
    duplicate = _node(db, row["target"])
    canonical = _by_name(db, mid, payload.get("canonical"))
    if duplicate is None or canonical is None:
        return ORPHAN, "one of the two concepts no longer exists", None
    if duplicate["status"] == "retired" or canonical["status"] == "retired":
        return ORPHAN, "one of the two concepts has been retired", None
    if duplicate["id"] == canonical["id"]:
        return SATISFIED, "they are already the same concept", None
    aliases = [a.lower() for a in loads(canonical.get("aliases"), [])]
    if _norm(payload.get("alias")) in aliases:
        return SATISFIED, (f"'{payload.get('alias')}' is already an alias of "
                           f"'{canonical['name']}'"), None
    return LIVE, None, None


def _rc_edge_reproposal(db, mid, row, payload):
    edge = db.one("SELECT * FROM concept_edges WHERE id = ?", (row["target"],))
    if edge is None:
        return ORPHAN, "the relationship no longer exists", None
    if edge["status"] in ("declared", "confirmed"):
        return SATISFIED, "the relationship has been restored", None
    for end in ("from_node", "to_node"):
        node = _node(db, edge[end])
        if node is None or node["status"] == "retired":
            return ORPHAN, "an endpoint concept has been retired", None
    return LIVE, None, None


def _rc_variant_of_retired(db, mid, row, payload):
    existing = _by_name(db, mid, payload.get("proposed_name"))
    if existing and existing["status"] != "retired":
        return SATISFIED, (f"'{payload.get('proposed_name')}' already exists "
                           "as a distinct concept"), None
    return LIVE, None, None


def reconcile_queue(db: Database, manuscript_id: str, spec: LoopSpec,
                    rows: list[dict], apply: bool = True) -> dict:
    """Settle what the world has already answered; re-base what it moved.

    Like screen cuts, these resolutions are pipeline hygiene and are NEVER
    recorded as author evidence — nobody judged anything here, the question
    simply stopped being a question."""
    report = {SATISFIED: [], ORPHAN: [], STALE: [], LIVE: 0}
    if spec.reconcile is None:
        report[LIVE] = len(rows)
        return report
    for row in rows:
        payload = loads(row.get("payload"), {}) or {}
        verdict, detail, rebased = spec.reconcile(db, manuscript_id, row,
                                                  payload)
        if verdict == LIVE:
            report[LIVE] += 1
            continue
        report[verdict].append({"id": row["id"], "detail": detail})
        if not apply:
            continue
        meta = loads(row.get("metadata"), {}) or {}
        if verdict == STALE:
            meta.update(rebased=meta.get("rebased", 0) + 1,
                        rebased_from=payload.get("current_note"))
            db.update(spec.table, row["id"],
                      {"payload": json.dumps(rebased),
                       "metadata": json.dumps(meta)})
            continue
        meta.update(by="reconcile", verdict=verdict, reason=detail)
        db.update(spec.table, row["id"],
                  {"state": spec.cut_state, "metadata": json.dumps(meta)})
    return report


# ----------------------------------------------------------------- ② the law

def active_law(db: Database, manuscript_id: str, spec: LoopSpec) -> list[dict]:
    """What the screen is allowed to cut on, in one list: beliefs the
    machine promoted on evidence (`validated`, unblessed — they ACT but are
    not law), plus rules the author ratified for this aspect (they are).
    Each carries `accepted` so every surface can show which is which."""
    law = [
        {"id": r["id"], "statement": r["statement"], "accepted": False,
         "confidence": r["confidence"],
         "support": f"{r['supporting']}+/{r['contradicting']}-"}
        for r in db.all(
            "SELECT * FROM editorial_beliefs WHERE manuscript_id = ? "
            "AND source = ? AND status = 'validated' ORDER BY supporting DESC",
            (manuscript_id, spec.source))
    ]
    law += [
        {"id": r["id"], "statement": r["statement"], "accepted": True,
         "confidence": None, "support": "ratified"}
        for r in db.all(
            "SELECT * FROM style_laws WHERE manuscript_id = ? AND aspect = ? "
            "AND status = 'active' ORDER BY created_at",
            (manuscript_id, spec.aspect))
    ]
    return law


def law_text(law: list[dict]) -> str:
    if not law:
        return ""
    return "ACTIVE LAW:\n" + "\n".join(
        f"  {e['id']} | {e['statement']}"
        + ("" if e["accepted"] else f"  (belief, {e['support']})")
        for e in law)


# ---------------------------------------------------------------- ③ screen

# Rows per screening call. One call over the whole queue is not viable: 305
# note_update rows, each rendering both the current and the proposed note,
# builds a prompt large enough to time out before a single cut is recorded.
# Batching also bounds the blast radius — a confused reply costs one batch,
# not the queue.
SCREEN_BATCH = 25
SCREEN_ROW_CHARS = 400


def screen(db: Database, manuscript_id: str, spec: LoopSpec,
           rows: list[dict], llm, batch_size: int = SCREEN_BATCH,
           on_batch=None) -> list[dict]:
    """Cut open proposals that violate active law. Returns the cut records.

    Cuts RESOLVE the row (it leaves the open queue) and carry attribution,
    but never write evidence — see invariant 1 in the module docstring."""
    if not rows or not llm or not getattr(llm, "enabled", False):
        return []
    law = active_law(db, manuscript_id, spec)
    if not law:
        return []
    valid_law = {e["id"] for e in law}
    header = law_text(law)
    cuts: list[dict] = []
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        listing = "\n".join(
            f"  {n}. {spec.render(r)[:SCREEN_ROW_CHARS]}"
            for n, r in enumerate(batch, 1))
        try:
            reply = llm.complete_json(
                screen_system(), f"{header}\n\nPROPOSALS:\n{listing}")
        except Exception:  # noqa: BLE001 - one bad batch must not lose the rest
            reply = None
        for item in (reply or {}).get("cut", []) or []:
            try:
                n = int(item.get("n"))
            except (TypeError, ValueError):
                continue
            law_id = str(item.get("law") or "").strip()
            # A cut with no nameable law is exactly the taste-based cut the
            # prompt forbids; drop it rather than let it through unattributed.
            if not 1 <= n <= len(batch) or law_id not in valid_law:
                continue
            row = batch[n - 1]
            meta = loads(row.get("metadata"), {}) or {}
            meta.update(by="screen", law=law_id,
                        reason=str(item.get("reason") or "")[:200])
            db.update(spec.table, row["id"],
                      {"state": spec.cut_state, "metadata": json.dumps(meta)})
            cuts.append({"id": row["id"], "law": law_id,
                         "reason": meta["reason"]})
        if on_batch:
            on_batch(min(start + batch_size, len(rows)), len(rows), len(cuts))
    return cuts


def folded(db: Database, manuscript_id: str, spec: LoopSpec) -> list[dict]:
    """Screen cuts grouped by the law that made them — the compact form the
    author scans, and the handle for expanding one. A wrong belief is
    caught by its damage, so this must stay cheap enough to always show."""
    sql = (f"SELECT * FROM {spec.table} WHERE manuscript_id = ? AND state = ? "
           "AND json_extract(metadata, '$.by') = 'screen'")
    args: list = [manuscript_id, spec.cut_state]
    if spec.kind:
        sql += " AND kind = ?"
        args.append(spec.kind)
    rows = [dict(r) for r in db.all(sql, tuple(args))]
    groups: dict[str, dict] = {}
    for row in rows:
        meta = loads(row.get("metadata"), {}) or {}
        law_id = meta.get("law") or "?"
        g = groups.setdefault(law_id, {"law": law_id, "count": 0,
                                       "sample": [], "ids": []})
        g["count"] += 1
        g["ids"].append(row["id"])
        if len(g["sample"]) < 3:
            g["sample"].append(spec.render(row)[:90])
    law = {e["id"]: e for e in active_law(db, manuscript_id, spec)}
    for law_id, g in groups.items():
        entry = law.get(law_id)
        g["statement"] = entry["statement"] if entry else "(law retired)"
        g["accepted"] = bool(entry and entry["accepted"])
    return sorted(groups.values(), key=lambda g: -g["count"])


# --------------------------------------------------------------- ④ distil

DISTIL_MIN_BATCH = 2


def pending_explanations(db: Database, manuscript_id: str,
                         spec: LoopSpec) -> list[dict]:
    """Verdict evidence carrying an author explanation that has not been
    distilled yet, oldest first."""
    rows = []
    for r in db.all(
            "SELECT * FROM evidence WHERE manuscript_id = ? "
            "AND evidence_type = ? ORDER BY created_at",
            (manuscript_id, spec.evidence_type)):
        meta = loads(r["metadata"], {}) or {}
        if meta.get("explanation") and not meta.get("distilled"):
            rows.append({**dict(r), "explanation": meta["explanation"]})
    return rows


def distil_pending(db: Database, manuscript: dict, spec: LoopSpec,
                   llm) -> dict | None:
    """Distil accumulated explanations once a sitting has produced at least
    two, then mark them consumed.

    The ">= 2 across independent instances" rule is the author's guardrail
    against learning a rule by force of habit. Holding it HERE — over
    persisted evidence, rather than inside one caller's loop — is what
    makes it hold on every surface. It previously lived in a CLI batch
    call that the MCP path simply never made, so MCP seeded one belief per
    verdict and produced six paraphrases of "be specific", each stranded at
    supporting=1."""
    pending = pending_explanations(db, manuscript["id"], spec)
    if len(pending) < DISTIL_MIN_BATCH:
        return None
    seeded = distil(db, manuscript, spec,
                    [r["explanation"] for r in pending], llm)
    if seeded is None:
        return None
    for r in pending:
        meta = loads(r["metadata"], {}) or {}
        meta["distilled"] = True
        db.update("evidence", r["id"], {"metadata": json.dumps(meta)})
    return seeded


def distil(db: Database, manuscript: dict, spec: LoopSpec,
           explanations: list[str], llm) -> dict | None:
    """One distiller call for a whole sitting, never one per verdict.

    Batching is the guardrail, not an optimisation: the decline-by-default
    distiller can only see whether a pattern spans two instances if it is
    shown both at once. Calling it per verdict — which the MCP illustration
    path used to do — produced six paraphrases of "be specific", each stuck
    at supporting=1, none ever able to reach a threshold."""
    if not explanations or not llm or not getattr(llm, "enabled", False):
        return None
    from .beliefs import seed_candidate_belief

    combined = (
        f"{len(explanations)} triage verdict(s) from one sitting on "
        f"{spec.key} (a belief needs a pattern across at least two):\n"
        + "\n".join(f"- {e}" for e in explanations))
    return seed_candidate_belief(db, manuscript["id"], combined,
                                 source=spec.source, llm=llm)


# ------------------------------------------------------------- ⑤ feedback

def generator_feedback(db: Database, manuscript_id: str,
                       spec: LoopSpec) -> str:
    """Active law, phrased for injection into the prompt that GENERATES
    these proposals — stopping them at the source is cheaper than screening
    them, and unlike a recency window over raw evidence it does not forget."""
    law = active_law(db, manuscript_id, spec)
    if not law:
        return ""
    return ("\n\nThe author's settled judgments about "
            f"{spec.key} (follow these; they were learned from their own "
            "verdicts):\n"
            + "\n".join(f"- {e['statement']}" for e in law))


# --------------------------------------------------------------- registry

def _render_proposal(row: dict) -> str:
    from .proposals import describe

    summary, details = describe(row)
    return " · ".join([summary, *details])


def _payload(row: dict) -> dict:
    return loads(row.get("payload"), {}) or {}


def _dedupe_note_update(row: dict) -> str:
    p = _payload(row)
    return f"{p.get('proposed_note', '')} {p.get('proposed_kind', '')}"


def _dedupe_alias(row: dict) -> str:
    p = _payload(row)
    return (f"{p.get('alias', '')} {p.get('canonical', '')} "
            f"{p.get('sentence', '')}")


REGISTRY: dict[str, LoopSpec] = {
    "proposals/note_update": LoopSpec(
        key="proposals/note_update", table="knowledge_proposals",
        kind="note_update",
        aspect="concept-note", source="triage-note_update",
        evidence_type="proposal_review", render=_render_proposal,
        dedupe_render=_dedupe_note_update, reconcile=_rc_note_update),
    "proposals/alias": LoopSpec(
        key="proposals/alias", table="knowledge_proposals",
        kind="alias",
        aspect="concept-identity", source="triage-alias",
        evidence_type="proposal_review", render=_render_proposal,
        dedupe_render=_dedupe_alias, reconcile=_rc_alias),
    # The four below join for RECONCILIATION only. Registering them costs
    # nothing and settles 42% of the live queue; their screen half stays
    # inert because `active_law` finds no belief for these sources, and no
    # distiller is wired to them. They graduate to the full loop when the
    # remaining volume actually hurts (docs/design-backlog.md).
    "proposals/vanished": LoopSpec(
        key="proposals/vanished", table="knowledge_proposals",
        kind="vanished",
        aspect="concept-lifecycle", source="triage-vanished",
        evidence_type="proposal_review", render=_render_proposal,
        reconcile=_rc_vanished),
    "proposals/revival": LoopSpec(
        key="proposals/revival", table="knowledge_proposals",
        kind="revival",
        aspect="concept-lifecycle", source="triage-revival",
        evidence_type="proposal_review", render=_render_proposal,
        reconcile=_rc_revival),
    "proposals/edge_reproposal": LoopSpec(
        key="proposals/edge_reproposal", table="knowledge_proposals",
        kind="edge_reproposal",
        aspect="concept-relation", source="triage-edge_reproposal",
        evidence_type="proposal_review", render=_render_proposal,
        reconcile=_rc_edge_reproposal),
    "proposals/variant_of_retired": LoopSpec(
        key="proposals/variant_of_retired", table="knowledge_proposals",
        kind="variant_of_retired",
        aspect="concept-identity", source="triage-variant_of_retired",
        evidence_type="proposal_review", render=_render_proposal,
        reconcile=_rc_variant_of_retired),
    "illustrations": LoopSpec(
        key="illustrations", table="illus_proposals",
        aspect="illustration-placement", source="margin-thread",
        evidence_type="illus_triage",
        render=lambda r: (f"{r['file']}: [{r['description']}] "
                          f"after «{r['anchor'][:60]}»"),
        dedupe_render=lambda r: r["description"],
        open_state="proposed", cut_state="rejected"),
}


def spec_for(key: str) -> LoopSpec:
    if key not in REGISTRY:
        raise LookupError(f"no loop spec '{key}' "
                          f"(have: {', '.join(sorted(REGISTRY))})")
    return REGISTRY[key]
