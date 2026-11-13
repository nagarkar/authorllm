"""The observation pipeline: `collect` takes a snapshot of the manuscript and
reports what changed, the transitions, the concept scans, the prerequisite
delta, and the illustration, verse, directive and media tags left open.

Invariant: no LLM runs here and nothing is drafted. A collect observes; the
only things it writes are the version row and what follows from it, plus the
machine-maintained excerpts and clip keys, which are kept current before the
snapshot. `api.collect` is this function, re-exported, so every surface keeps
calling it there; the modules below the surfaces import it from here.

For a manuscript whose `document_ids` setting is on (`docs.py`; off, none
of this runs and a collect is what it was before documents had ids), a
file renamed outside AuthorLM (Finder, Obsidian) is one document under a
new name, never a removal and an addition: before the snapshot, a collect
pairs each name that left with the name that arrived carrying its text
(`revisions.pair_renames`) and carries the document's records to the new
name (`docs.carry_rename`, the function behind `doc rename`), so the rename
heals here. After the snapshot every file has its document row
(`docs.ensure_documents`).

It sits in the editing area because `directives` and `media_road` collect after
landing an edit, and it reads only areas at or below its own.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import concepts as cg
from . import hygiene
from . import proposals as prop
from . import sessions as ses
from .db import Database, loads
from .guidance import compute_prerequisite_gaps
from .illus import slot_report as illus_slot_report
from .llm import LLMClient
from .revisions import (collect_revision, detect_transitions, is_placeholder,
                        iter_manuscript_paths, massive_deletions,
                        pair_renames, read_manuscript_files)
from .sessions import NO_EPISODE


def _media_not_ready(manuscript: dict) -> list[dict]:
    """Clips, collages and montages not ready (docs/video-clips-design.md
    §8e): drafts, broken field combinations, structural faults. Read
    only; a failure here must never break a collect or a status."""
    from . import media

    try:
        return media.not_ready(Path(manuscript["path"]))
    except (OSError, ValueError):
        return []


def _deprecate_departed_summaries(db: Database, manuscript: dict, sums) -> list[str]:
    """A summary whose file has left the toc is never deleted — it is
    the record of an essay that existed — but it must not linger
    ambiguously as if it were still live. `essay_summaries.status`
    ('current' | 'deprecated') marks it so nothing mistakes it for a
    stale current summary.

    While the file stays gone, sums.status()/before_after() never see
    the row at all: both are driven off the CURRENT toc reading order.
    The mark earns its keep when the file COMES BACK — possibly
    byte-identical, so its source_hash still matches. Both functions now
    read this column (summaries._state) and report 'deprecated' rather
    than 'fresh'; the drafting and edit gates refuse it, and a rebuild
    resurrects it to 'current'. Idempotent: only rows not already
    'deprecated' are touched."""
    current = {f for f, _ in sums.units(manuscript)}
    deprecated = []
    for file, row in sums.all_summaries(db, manuscript["id"]).items():
        if file not in current and row["status"] != "deprecated":
            db.update("essay_summaries", row["id"], {"status": "deprecated"})
            deprecated.append(file)
    return deprecated


def _carry_renames_on_disk(db: Database, manuscript: dict,
                           before) -> list[dict]:
    """Files renamed on disk since the last snapshot, each carried to its
    new name (`docs.carry_rename`); returns the carry reports. Reads the
    files only when a name left AND a name arrived, so a collect with no
    rename pays one directory listing."""
    from . import docs

    root = Path(manuscript["path"])
    if before is None or not root.is_dir():
        return []
    old_files: dict[str, str] = loads(before["files"], {})
    names = set(iter_manuscript_paths(root))
    if not (set(old_files) - names) or not (names - set(old_files)):
        return []
    pairs = pair_renames(old_files, read_manuscript_files(root))
    return [docs.carry_rename(db, manuscript, old, new)
            for old, new in sorted(pairs.items())]


def collect(db: Database, manuscript: dict, config: dict,
            auto: bool = False, source: str = "snapshot",
            episode=None) -> dict:
    """The observation pipeline: snapshot → transitions → episode →
    realization/co-occurrence scans → extract hint → prerequisite delta.
    Returns a structured report; {"staged": [...]} when the auto path held
    a suspicious deletion; {"unchanged": True} when nothing changed.

    `episode` names the episode the transitions attach to. The default —
    the session's most recently created open episode — is what every
    ambient caller wants and is byte for byte what this function did
    before the parameter existed. The beat loop passes the PRIMARY
    intent's episode instead: with two writeups open at once, the default
    attached both writeups' beats to whichever intent was declared last
    (design-intent-scope §0.3).

    `episode=NO_EPISODE` is the third case and it is not the same as
    `None`: it attaches the transitions to NOTHING. A settle that is
    hygiene rather than goal-work — a filter pass, a critique-pass
    resolve — has no goal to be filed under, and filing it under the
    open episode of whatever the author happened to be doing would make
    §15.17's completion analysis report that goal as served by an edit
    sweep. Refusing to guess is the doctrine (§15.19 rule 2); this is
    that refusal made mechanical."""
    from . import docs

    mid = manuscript["id"]
    # The manuscript's `document_ids` setting: off, no rename is paired
    # or carried and no document row is written.
    document_ids = docs.document_ids_enabled(db, manuscript)
    if auto:
        flagged = massive_deletions(db, manuscript, renames=document_ids)
        if flagged:
            return {"staged": [{"file": f, "removed_percent": p} for f, p in flagged]}

    # Ref-tag excerpts are machine-maintained BEFORE the snapshot, so
    # the collected version always holds canonical excerpts and a
    # discarded excerpt edit never masquerades as an authorial change.
    try:
        from .illus import maintain_excerpts

        excerpt_fixes = maintain_excerpts(Path(manuscript["path"]))
    except OSError:
        excerpt_fixes = []
    # Clip keys are minted, and clip excerpts maintained, at the same
    # moment and for the same reason (docs/video-clips-design.md §8a).
    try:
        from .media import maintain_keys

        media_keys = maintain_keys(Path(manuscript["path"]))
    except OSError:
        media_keys = []

    session = ses.active_session(db, mid)
    before = db.one(
        "SELECT * FROM manuscript_versions WHERE manuscript_id = ? "
        "ORDER BY version_no DESC LIMIT 1",
        (mid,),
    )
    # A rename made outside AuthorLM is carried BEFORE the snapshot, so
    # the version recorded here already holds toc.toml with the new name
    # and every scan below finds the document's records under it.
    carried = (_carry_renames_on_disk(db, manuscript, before)
               if document_ids else [])
    version = collect_revision(db, manuscript, session["id"] if session else None,
                               source=source)
    if version is None:
        return {"unchanged": True}

    transitions = detect_transitions(db, mid, dict(before) if before else None,
                                     version, renames=document_ids)
    docs.ensure_documents(db, manuscript)
    attached = False
    if episode is NO_EPISODE:
        # Filed under NO goal, deliberately (filter-pass design §1.8).
        # `episode=None` means AMBIENT — the session's most recently
        # created open episode, whatever goal it belongs to — which is
        # §15.17's mis-attribution. Hygiene work has no goal to be filed
        # under, so it is filed under none: the version history, the
        # evidence rows and the run row are the complete record.
        episode = None
    elif episode is not None:
        ses.attach_transitions(db, episode, transitions)
        attached = True
    elif session:
        episode = ses.current_episode(db, mid, dict(session))
        ses.attach_transitions(db, episode, transitions)
        attached = True
    # The concept scans are shown a BLINDED version: any file that holds
    # only the mid-rewrite placeholder is presented as empty, exactly as
    # it was before the placeholder existed. concepts._word_pattern is
    # case-insensitive, so a concept named "Being" — entirely plausible
    # in this manuscript — would otherwise match the placeholder's "being
    # presently rewritten" and be marked realized with introduced_in
    # pointing at a truncated file, silently rewriting the graph. Picking
    # "safe" words is not a fix: the author owns the concept vocabulary
    # and can add the colliding word tomorrow. The version ROW is
    # untouched; only the scans' input is blinded.
    def _blinded(files: dict) -> dict:
        return {f: ("" if is_placeholder(t) else t) for f, t in files.items()}

    _blind = _blinded(loads(version["files"], {}))
    _scan_version = {**dict(version), "files": json.dumps(_blind)}
    realized = cg.scan_realizations(db, mid, _scan_version)
    repointed, vanished = cg.rescan_primary_locations(db, mid, _scan_version)
    # Summaries: mark, don't cascade. A changed unit's own row is already
    # stale by source_hash; downstream rows get the tolerated flag.
    from . import summaries as sums

    # A rename alone changes no text: only the edits made with it count.
    changed_files = sorted({t["location"].split("#", 1)[0] for t in transitions
                            if t["kind"] != "file_renamed"})
    sums.mark_changed(db, manuscript, changed_files)
    deprecated_summaries = _deprecate_departed_summaries(db, manuscript, sums)
    vanished_proposals = []
    hypotheses_dropped = []
    for node in vanished:
        meta = loads(node.get("metadata"), {})
        if meta.get("origin") == "extracted" and not meta.get("confirmed"):
            # An unconfirmed hypothesis was never knowledge: when its text
            # vanishes it dies quietly — the author must not be asked to
            # adjudicate the disappearance of something they never
            # accepted (it-8f24c757d3c7).
            cg.retire_concept(db, mid, dict(node))
            hypotheses_dropped.append(node["name"])
        else:
            created = prop.create(
                db, mid, "vanished", node["id"],
                {"name": node["name"], "was_in": node["introduced_in"]},
            )
            if created:
                vanished_proposals.append(node["name"])

    new_paragraphs = 0
    for transition in transitions:
        detail = loads(transition["detail"], {})
        if transition["kind"] == "file_added":
            new_paragraphs += detail.get("paragraphs", 0)
        elif transition["kind"] in ("insert", "rewrite"):
            text = detail.get("new_text", "")
            new_paragraphs += len([p for p in re.split(r"\n\s*\n", text) if p.strip()])
    extract_hint = new_paragraphs >= 5 and LLMClient(config).enabled

    # BOTH sides are blinded, for the same reason the concept scans are
    # (`_blind`, above): this walks the same case-insensitive concept
    # patterns over the same file texts, so an unblinded placeholder could
    # close a prerequisite gap — "the concept now appears in this essay" —
    # on the strength of a word inside a marker.
    #
    # `before` needs it as much as `after` does, and it is easy to miss:
    # every collect DURING a writeup has a before-version that already
    # contains the placeholder (the writeup's own start collect put it
    # there). Blinding only one side would leave the marker deciding the
    # DELTA — gaps_resolved and gaps_new are set differences of these two
    # — which is the same defect wearing a subtraction sign.
    gaps_before = (
        compute_prerequisite_gaps(db, mid, _blinded(loads(before["files"], {})))
        if before else []
    )
    gaps_after = compute_prerequisite_gaps(db, mid, _blind)
    before_keys = {g["edge_id"] for g in gaps_before}
    after_keys = {g["edge_id"] for g in gaps_after}

    report = {
        "version_no": version["version_no"],
        "checksum": version["checksum"],
        "transitions": [
            {"kind": t["kind"], "location": t["location"], "summary": t["summary"]}
            for t in transitions
        ],
        "attached_to_episode": attached,
        "new_files": [t["location"] for t in transitions
                      if t["kind"] == "file_added"],
        "realized": [
            {"name": n["name"], "introduced_in": n["introduced_in"]} for n in realized
        ],
        "repointed": repointed,
        "vanished": vanished_proposals,
        "hypotheses_dropped": hypotheses_dropped,
        "new_paragraphs": new_paragraphs,
        "extract_hint": extract_hint,
        "gaps_before": gaps_before,
        "gaps_after": gaps_after,
        "gaps_resolved": [g for g in gaps_before if g["edge_id"] not in after_keys],
        "gaps_new": [g for g in gaps_after if g["edge_id"] not in before_keys],
    }

    # Hygiene: suggestions this revision made moot (the author wrote the
    # concept a bridge suggestion proposed; a prerequisite gap closed) are
    # marked stale so review never offers a no-op — a no-op the author
    # rejects would poison the belief evidence.
    staled = hygiene.stale_suggestions(
        db, mid,
        realized_ids={n["id"] for n in realized},
        resolved_edge_ids={g["edge_id"] for g in report["gaps_resolved"]},
    )
    if staled:
        report["suggestions_stale"] = staled
    if deprecated_summaries:
        report["summaries_deprecated"] = deprecated_summaries
    renamed = [loads(t["detail"], {}) for t in transitions
               if t["kind"] == "file_renamed"]
    if renamed:
        report["renamed"] = [{"from": r["from"], "to": r["to"]}
                             for r in renamed]
        left = [f"{c['to']}: {item}" for c in carried for item in c["left"]]
        if left:
            report["renamed_left"] = left
        stale = [c["to"] for c in carried if c["tab_title_stale"]]
        if stale:
            report["renamed_tabs"] = stale

    # Illustration slots are a scan-derived registry; a collect is the
    # moment the author learns about unrendered tags ("new illustrations
    # found") without having to remember to ask. Reporting only — collect
    # never renders anything. Exception: ref-tag excerpts are machine-
    # maintained here (regenerated from their canonical prompts file, so
    # drift is impossible; a discarded excerpt edit is called out).
    try:
        from .illus import externalize_offers

        slots = illus_slot_report(Path(manuscript["path"]))
        offers = externalize_offers(Path(manuscript["path"]))
    except OSError:
        slots, offers = None, []
    if slots and (slots["unrendered"] or slots["orphaned"]
                 or slots["malformed_refs"]):
        report["illustrations"] = slots
    if excerpt_fixes or offers:
        report.setdefault("illustrations", {})
        report["illustrations"]["excerpt_fixes"] = excerpt_fixes
        report["illustrations"]["externalize_offers"] = offers
    # Verse files: poem count at the declared heading level, and a warning
    # when a file marked verse has no poems there — caught on import, not
    # when a lens refuses (verse.py; poem-grain ruling 2026-09-25).
    try:
        from .verse import verse_report

        vr = verse_report(read_manuscript_files(Path(manuscript["path"])))
        if vr:
            report["verse"] = vr
    except Exception:  # observation must never die on a structural read
        pass

    # No LLM runs at collect. Concept extraction is LAZY (ruling
    # 2026-10-25, docs/extraction-redesign.md §0.9): the graph is mined
    # when someone asks for a fresh one — `authorlm extract --native` or
    # the MCP tool — never as a side effect of observing an edit. The
    # extract hint above is all a collect says about it.
    # Inline directives — [Footnote: …], [Explain: …] and [Suggestion: …]
    # — are REPORTED here, beside the illustration slots, and never
    # drafted (footnote design §2): no LLM runs at collect and nothing is
    # written unasked. Drafting is chat; `footnote apply` / `explain
    # apply` / `suggestion apply` land the author's ruling.
    try:
        from . import directive_tags

        open_tags = directive_tags.open_report(Path(manuscript["path"]))
    except OSError:
        open_tags = []
    if open_tags:
        report["directives"] = open_tags
    # Media tags that are not ready are reported the same way
    # (docs/video-clips-design.md §8e): no model runs, nothing is written.
    media_rows = _media_not_ready(manuscript)
    if media_rows:
        report["media"] = media_rows
    if media_keys:
        report["media_keys"] = media_keys
    return report
