"""End-to-end test of every MVP use case, driven through the real CLI.

Includes a hermetic LLM scenario: a stub OpenAI-compatible HTTP server
stands in for LiteLLM/Gemini, so extraction, belief distillation, and
bridge drafting are tested without network or keys.

Follows the RFC Appendix A shape: declare intent → briefing → guidance with
explanations → author review (accept / reject with explanation) → revisions
observed → transitions → episode → evidence → belief learning → next
session's briefing reflects the learning.

Run: python3 tests/test_e2e.py
"""

from __future__ import annotations

import os

# Offline suite: pin the project-config and .env lookups away from the
# real ones. Without this a checkout's config.toml (llm enabled, keys in
# .env) is picked up by every test process and the suite makes live,
# billed model calls — and asserts against whatever they return.
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
# And pin client provenance OFF. The suites run INSIDE a Claude Code
# Bash call, so CLAUDE_CODE_SESSION_ID is in their own environment and
# the claude-code adapter would stamp the developer's live chat into
# every fixture row — tests passing for the wrong reason. Same failure
# mode as a leaked config, so it gets the same treatment: pin it.
os.environ["AUTHORLM_CLIENT"] = "none"

def _assert_offline() -> None:
    """Fail loudly if the real project config or .env leaks into a test.

    Before config moved into the repo, a test workspace simply had no
    config.toml, so the LLM was off and no suite could make a billed
    call. Now a checkout always HAS an enabled config, and that safety
    came from nothing but the pins above — so it is asserted, not
    assumed."""
    import os as _os

    from authorlm import paths as _paths

    assert not _paths.config_path().exists(), (
        f"test isolation broken: reading the real config at "
        f"{_paths.config_path()}")
    assert _paths.load_env() == [], "test isolation broken: .env was loaded"
    leaked = [v for v in _paths_vendor_vars() if _os.environ.get(v)]
    assert not leaked, f"test isolation broken: vendor keys in env: {leaked}"


def _paths_vendor_vars() -> list:
    from authorlm.llm import VENDOR_KEY_ENV

    return sorted(VENDOR_KEY_ENV.values())



import contextlib
import http.server
import io
import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import api as _api  # noqa: E402
from authorlm.cli import main  # noqa: E402

PASSED = 0


def _pin_config(workspace: Path) -> None:
    """Point the config lookup at this workspace's config.toml.

    Config is a project file in production (authorlm/paths.py), but the
    suite drives many independent workspaces in one process and each
    needs its own — a stub LLM endpoint here, an idle_hours threshold
    there. AUTHORLM_CONFIG is the supported override for exactly this;
    a missing file still yields {}, as the no-config tests expect."""
    os.environ["AUTHORLM_CONFIG"] = str(workspace / ".authorlm" / "config.toml")


def run(workspace: Path, *argv: str, expect_exit: bool = False) -> str:
    _pin_config(workspace)
    buffer = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(buffer):
            main(["--workspace", str(workspace), *argv])
    except SystemExit as err:
        code = 0 if err.code in (0, None) else 1
        if isinstance(err.code, str):
            buffer.write(err.code + "\n")
            code = 1
    output = buffer.getvalue()
    if expect_exit:
        assert code != 0, f"expected failure but succeeded: {argv}\n{output}"
    else:
        assert code == 0, f"command failed: {argv}\n{output}"
    return output


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


CH1 = """# Chapter 1 — Choice

Every act of thought begins with a choice. Choice is the operation by which
anything comes to be for us.

To choose is to distinguish. Distinction is the visible form choice takes.
"""

CH2 = """# Chapter 2 — Fields

Each distinction creates a space of further possible distinctions: a field.

A field is generative, not a container. Each position within a field is a
distinction that opens further fields, linking choice to structure.
"""

CH3 = """# Chapter 3 — Trajectories

A field opens possibilities; a trajectory is the path actually taken through
them. Where choice and distinction are instantaneous and the field is
structural, only the trajectory unfolds in time.

A trajectory is an ordered series of distinctions, each made from within the
situation the previous choices created. It is the minimal unit of becoming.
"""


def scenario_editorial_loop(root: Path) -> None:
    print("Scenario A — full editorial loop (Appendix A shape)")
    ws = root / "a"
    ms = ws / "manuscript"
    write(ms / "01-choice.md", CH1)
    write(ms / "02-fields.md", CH2)

    out = run(ws, "init", "--name", "book", "--path", str(ms))
    check("init registers manuscript", "Registered manuscript" in out, out)

    out = run(ws, "session", "start")
    check("first briefing is fresh", "First session — nothing learned yet" in out, out)

    for name in ("Choice", "Distinction", "Field", "Trajectory", "History"):
        run(ws, "concept", "add", name)
    run(ws, "concept", "link", "Choice", "distinguishes", "Distinction")
    run(ws, "concept", "link", "Distinction", "creates", "Field")
    run(ws, "concept", "link", "Field", "permits", "Trajectory")
    run(ws, "concept", "link", "Trajectory", "defines", "History")

    out = run(ws, "intent", "declare", "Introduce trajectories")
    check("intent declared", "Declared intent" in out, out)
    intent_id = out.split("[")[1].split("]")[0]

    out = run(ws, "collect")
    check("collect detects new files", "file_added" in out, out)
    check("declared concepts realize on collect", "Concept realized: 'Choice'" in out, out)
    check("unwritten concept stays declared", "Concept realized: 'Trajectory'" not in out, out)

    out = run(ws, "guide")
    check("guidance proposes introducing Trajectory (plural intent matched)",
          "Introduce 'Trajectory'" in out, out)
    check("guidance explains itself from the graph",
          "Field —permits→ Trajectory" in out, out)

    out = run(ws, "review", "1", "--accept", "--explain", "Bridge from fields is right")
    check("accept recorded", "accepted" in out, out)

    out = run(ws, "review", "1", "--accept", expect_exit=True)
    check("double review rejected", "already reviewed" in out, out)

    write(ms / "03-trajectories.md", CH3)
    out = run(ws, "collect")
    check("Trajectory realized after writing", "Concept realized: 'Trajectory'" in out, out)
    check("no extract hint without an LLM", "mine them" not in out, out)

    # Inline diff between collected versions.
    out = run(ws, "diff")
    check("diff shows the new chapter as additions",
          "v2/03-trajectories.md" in out
          and "+# Chapter 3 — Trajectories" in out, out)
    out = run(ws, "diff", "v2", "choice")
    check("diff accepts version and file filter",
          "No differences" in out, out)

    out = run(ws, "intent", "complete", intent_id, "--outcome", "Trajectories introduced")
    check("intent completed", "Intent completed" in out, out)

    out = run(ws, "session", "end")
    check("velocity counts evidence", "evidence: 1" in out, out)
    check("velocity counts realized concepts", "concepts realized: 4" in out, out)

    out = run(ws, "session", "start")
    check("second briefing reports realized concept",
          "Trajectory (introduced in 03-trajectories.md)" in out, out)
    check("second briefing suggests remaining focus", "Introduce 'History'" in out, out)

    out = run(ws, "guide")
    check("no active intent and no gaps → abstention", "abstains" in out, out)

    run(ws, "intent", "declare", "Introduce histories")
    out = run(ws, "guide")
    check("new intent matches History via plural", "Introduce 'History'" in out, out)

    out = run(
        ws, "review", "1", "--reject",
        "--explain", "Contrast a temporal concept with its static counterpart first",
    )
    check("rejection seeds candidate belief", "seeded a candidate belief" in out, out)

    out = run(ws, "guide")
    check("rejected suggestion is not re-proposed", "Introduce 'History'" not in out, out)
    check("candidate belief surfaces as reminder", "candidate belief" in out, out)

    out = run(ws, "review", "1", "--accept")
    out = run(ws, "guide")
    check("belief reminder deduped within a session", "abstains" in out, out)

    # Support must accumulate across independent sessions (§11.2). The
    # reminder must recur; whether it is still a candidate depends on the
    # per-source bar (review-explanation promotes at 2 — an explanation the
    # author typed, not a pattern guessed from a diff).
    run(ws, "session", "end")
    run(ws, "session", "start")
    out = run(ws, "guide")
    check("belief reminder recurs in a new session",
          "your belief" in out.lower(), out)
    out = run(ws, "belief", "list")
    check("review-explanation belief validated at 2 supports",
          "(validated" in out, out)
    out = run(ws, "review", "1", "--accept")
    out = run(ws, "belief", "list")
    check("further support keeps it validated", "(validated" in out, out)

    run(ws, "session", "end")
    run(ws, "session", "start")
    out = run(ws, "guide")
    check("validated belief now reminds", "apply your belief" in out, out)
    out = run(ws, "review", "1", "--reject", "--explain", "Not while drafting an example")
    check("rejecting validated belief recorded", "Recorded: [1] rejected" in out, out)

    out = run(ws, "briefing")
    check("briefing shows belief deltas", "Beliefs strengthened/weakened" in out, out)
    check("briefing shows contradiction", "Contradictions" in out, out)
    check("briefing shows outstanding question", "Outstanding questions" in out, out)

    belief_prefix = None
    current = None
    for line in run(ws, "belief", "list").splitlines():
        if line.strip().startswith("["):
            current = line.split("[")[1].split("]")[0]
        if "Q1:" in line:
            belief_prefix = current
    check("found belief with question via numbered Q lines", belief_prefix is not None)
    out = run(ws, "belief", "answer", belief_prefix, "Belief holds except inside examples")
    check("question answered as declared evidence", "Answer recorded" in out, out)

    # --- belief curation: retire / merge / convert-to-style ---
    from authorlm import beliefs as _cbel
    from authorlm.db import Database as _CDB
    _cdb = _CDB(ws / ".authorlm" / "authorlm.db")
    _cmid = _cdb.one("SELECT id FROM manuscripts WHERE name = 'book'")["id"]
    dup = _cbel.seed_candidate_belief(
        _cdb, _cmid, "Trim throat-clearing openers.", source="test")
    canon = _cbel.seed_candidate_belief(
        _cdb, _cmid, "Cut redundant opening phrases.", source="test")
    out = run(ws, "belief", "merge", canon["id"], canon["id"], expect_exit=True)
    check("merging a belief into itself is rejected",
          "same belief" in out, out)
    out = run(ws, "belief", "merge", dup["id"], canon["id"])
    check("belief merge folds duplicate into canonical",
          'Merged "Trim throat-clearing openers."' in out and "2+ / 0-" in out,
          out)
    out = run(ws, "belief", "list")
    check("merged duplicate leaves the belief list",
          "Trim throat-clearing openers." not in out
          and "Cut redundant opening phrases." in out, out)
    run(ws, "style", "guide", "Curation guide")
    out = run(ws, "belief", "convert", canon["id"], "--aspect", "formatting",
              "--guide", "Curation guide")
    check("belief converts to a style element",
          "converted to style element" in out, out)
    out = run(ws, "belief", "list")
    check("converted belief leaves the belief list",
          "Cut redundant opening phrases." not in out, out)
    out = run(ws, "style", "guides")
    check("converted element lives in its guide",
          "Curation guide" in out and "1 element(s)" in out, out)
    victim = _cbel.seed_candidate_belief(
        _cdb, _cmid, "Always use semicolons.", source="test")
    out = run(ws, "belief", "retire", victim["id"],
              "--reason", "author rejects this rule")
    check("belief retire records author verdict",
          "banned from re-seeding" in out, out)
    reseed = _cbel.seed_candidate_belief(
        _cdb, _cmid, "Always use semicolons.", source="test")
    check("retired statement re-seeds as revival proposal, not a new belief",
          reseed.get("kind") == "revival_proposal", str(reseed))

    # --- belief demote (Sponsor: "Build demote, then use it.") ---
    from authorlm.db import ko_fields as _ko_fields
    demotee = _ko_fields("pol")
    demotee.update(manuscript_id=_cmid,
                   statement="Demote me: validated, Sponsor no longer stands behind it.",
                   status="validated", confidence=_cbel._confidence(4, 0),
                   supporting=4, contradicting=0, outstanding_questions="[]",
                   source="review-explanation", source_id=_cdb.source("author"))
    _cdb.insert("editorial_beliefs", demotee)
    out = run(ws, "belief", "demote", demotee["id"], expect_exit=True)
    check("belief demote refuses with no reason — a demote with no reason "
          "would be a silent status edit",
          "reason" in out.lower(), out)
    out = run(ws, "belief", "demote", demotee["id"],
              "--reason", "Sponsor override: no longer stands behind this rule.")
    check("belief demote records the author verdict",
          "demoted" in out.lower(), out)
    demoted_row = _cdb.one("SELECT * FROM editorial_beliefs WHERE id = ?",
                           (demotee["id"],))
    check("status is 'candidate', not 'retired' — a demoted belief may "
          "re-validate on real future evidence",
          demoted_row["status"] == "candidate", str(dict(demoted_row)))
    demote_ev = _cdb.one(
        "SELECT * FROM evidence WHERE supports_belief = ? "
        "AND signal = 'demoted'", (demotee["id"],))
    check("the demotion is recorded as auditable evidence, reason verbatim",
          demote_ev is not None
          and "Sponsor override" in demote_ev["target"], str(demote_ev))
    out = run(ws, "belief", "list")
    check("a demoted belief still surfaces as a live (candidate) belief, "
          "unlike retire which removes it from the list",
          "Demote me:" in out, out)

    # X7-13's seam: the floor must not resurrect a demote. A subsequent
    # reinforce_belief call, with no real supporting evidence anywhere for
    # this belief, must not bounce it straight back to 'validated'.
    resurrection_check = _cbel.reinforce_belief(_cdb, demotee["id"], "accepted")
    check("a demoted belief stays demoted across a subsequent "
          "reinforce_belief — the floor cannot resurrect it (X7-13/demote seam)",
          resurrection_check["status"] == "candidate",
          str(resurrection_check))

    # --- compact MCP projections: delta-only, no row boilerplate ---
    from authorlm import api as _capi
    _cms = _capi.get_manuscript(_cdb, "book")
    compact = _capi.compact_briefing(_capi.get_briefing(_cdb, _cms))
    blob = json.dumps(compact)
    check("compact briefing drops row boilerplate",
          "created_by" not in blob and "schema_version" not in blob, blob[:400])
    check("compact briefing carries counts",
          isinstance(compact["unconfirmed_concepts"]["count"], int)
          and isinstance(compact["proposals"]["count"], int)
          and isinstance(compact["inferred_edges"]["count"], int), str(compact)[:400])
    gap = {"edge_id": "ce-x", "relation": "depends_on", "status": "declared",
           "from_name": "A", "to_name": "B", "first": "A", "second": "B",
           "text": "'B' first appears before its prerequisite 'A' is introduced."}
    synth = {"version_no": 1, "checksum": "x", "transitions": [],
             "attached_to_episode": False, "realized": [], "repointed": [],
             "vanished": [], "new_paragraphs": 0, "extract_hint": False,
             "gaps_before": [gap], "gaps_after": [], "gaps_resolved": [gap],
             "gaps_new": []}
    ccollect = _capi.compact_collect(synth)
    check("compact collect swaps before/after lists for counts",
          "gaps_before" not in ccollect
          and ccollect["gaps"] == {"before": 1, "after": 0}
          and ccollect["gaps_resolved"] == [
              {"edge_id": "ce-x", "text": gap["text"], "status": "declared"}],
          str(ccollect))
    check("compact collect passes non-version reports through",
          _capi.compact_collect({"unchanged": True}) == {"unchanged": True})
    cgraph = _capi.compact_concepts(_capi.list_concepts(_cdb, _cms))
    check("compact concept listing projects to name/kind/status",
          cgraph["node_count"] == len(cgraph["nodes"])
          and all(set(n) <= {"name", "kind", "status", "introduced_in",
                             "notes", "aliases"} for n in cgraph["nodes"]),
          str(cgraph)[:400])

    # Inferred edges now come only from the extractor — plant its style of
    # hypothesis directly so confirmation and edge triage stay covered.
    from authorlm.db import Database as _IDB, ko_fields as _iko
    _idb = _IDB(ws / ".authorlm" / "authorlm.db")
    _imid = _idb.one("SELECT id FROM manuscripts WHERE name = 'book'")["id"]

    def _plant(from_name: str, relation: str, to_name: str) -> None:
        ids = {
            nm: _idb.one(
                "SELECT id FROM concept_nodes WHERE manuscript_id = ? AND name = ?",
                (_imid, nm))["id"]
            for nm in (from_name, to_name)
        }
        row = _iko("ce")
        row.update(manuscript_id=_imid, from_node=ids[from_name],
                   relation=relation, to_node=ids[to_name],
                   status="inferred", support=0, evidence="[]")
        _idb.insert("concept_edges", row)

    _plant("Choice", "elaborates", "Field")
    edge_prefix = None
    for line in run(ws, "concept", "list").splitlines():
        if "elaborates" in line:
            edge_prefix = line.split("[")[1].split("]")[0]
            break
    check("found inferred edge", edge_prefix is not None)
    out = run(ws, "concept", "confirm", edge_prefix, "elaborates")
    check("inferred edge confirmed to declared relation", "Confirmed:" in out, out)

    out = run(ws, "collect")
    check("collect with no changes is a no-op", "No changes" in out, out)

    # --- edge triage: bulk review of inferred relationships ---
    _plant("Choice", "elaborates", "History")
    _plant("Distinction", "foreshadows", "History")
    import subprocess as _sp
    result = _sp.run(
        [sys.executable, "main.py", "--workspace", str(ws),
         "concept", "triage", "--edges"],
        input="depends_on\nr\nx\n", capture_output=True, text=True, timeout=60,
        cwd=Path(__file__).resolve().parent.parent,
    )
    check("edge triage walks inferred relationships",
          "inferred relationship(s)." in result.stdout, result.stdout)
    check("edge triage retypes by relation name",
          "(→ " in result.stdout and "depends_on" in result.stdout, result.stdout)
    check("edge triage summary reports decisions",
          "Edge triage: confirmed 0, retyped 1, rejected 1, merged 0, skipped 0."
          in result.stdout,
          result.stdout)
    out = run(ws, "concept", "list")
    check("retyped edge is declared with new relation",
          "—depends_on→" in out and "(declared" in out, out)
    check("rejected edge left the graph view", out.count("foreshadows") == 0, out)

    # Edge triage decisions feed the next extraction prompt.
    from authorlm.db import Database as _EDB
    from authorlm.extraction import triage_feedback as _tf
    _edb = _EDB(ws / ".authorlm" / "authorlm.db")
    _emid = _edb.one("SELECT id FROM manuscripts WHERE name = 'book'")["id"]
    feedback = _tf(_edb, _emid)
    check("edge rejections enter the extraction prompt",
          "Relationships REJECTED" in feedback, feedback)
    check("relation corrections enter the extraction prompt",
          "⇒" in feedback and "depends_on" in feedback, feedback)

    out = run(ws, "intent", "declare", "A dead-end objective")
    dead_id = out.split("[")[1].split("]")[0]
    out = run(ws, "intent", "retire", dead_id, "--outcome", "changed direction")
    check("intent abandon works (retire alias)", "Intent abandoned" in out, out)
    out = run(ws, "intent", "list")
    check("abandoned intent shown with status",
          "(abandoned · book-wide (default)) A dead-end objective" in out, out)
    out = run(ws, "intent", "abandon", dead_id, expect_exit=True)
    check("double abandon blocked", "already abandoned" in out, out)

    out = run(ws, "session", "end")
    out = run(ws, "history")
    check("history lists both versions", "v1" in out and "v2" in out, out)
    out = run(ws, "log")
    check("log shows transitions", "file_added" in out, out)

    # State-reading API entry points catch up on fresh edits themselves —
    # a briefing must never serve stale text (only the shell has a watcher).
    from authorlm import api as _capi
    from authorlm.db import Database as _CDB
    write(ms / "04-late.md", "# Late\n\nHistory bends every Trajectory.\n")
    _cdb = _CDB(ws / ".authorlm" / "authorlm.db")
    _cms = dict(_cdb.one("SELECT * FROM manuscripts WHERE name = 'book'"))
    briefing = _capi.get_briefing(_cdb, _cms, config={})
    check("briefing catch-up collects fresh edits",
          bool(briefing.get("caught_up", {}).get("transitions")),
          json.dumps(briefing.get("caught_up")))
    briefing = _capi.get_briefing(_cdb, _cms, config={})
    check("briefing catch-up is idempotent", "caught_up" not in briefing,
          json.dumps(briefing.get("caught_up")))


def scenario_prerequisite_gap(root: Path) -> None:
    print("Scenario B — prerequisite gap detection")
    ws = root / "b"
    ms = ws / "manuscript"
    write(ms / "01-intro.md", "# Intro\n\nGravity bends every Trajectory we draw.\n")

    run(ws, "init", "--name", "book", "--path", str(ms))
    run(ws, "session", "start")
    run(ws, "concept", "add", "Trajectory")
    run(ws, "concept", "add", "Gravity")
    run(ws, "concept", "link", "Trajectory", "permits", "Gravity")
    out = run(ws, "collect")
    check("push lint reports gap delta on collect",
          "Prerequisite gaps: 0 → 1" in out, out)
    out = run(ws, "guide")
    check("intent-less guide still flags structural gaps",
          "prerequisite" in out.lower(), out)
    # Both realized in one file, but Gravity is mentioned before Trajectory.
    check("gap explains text order vs graph order",
          "first appears before its prerequisite" in out, out)

    # Fuzzy intent feedback: typo → did-you-mean; match → deterministic preview.
    out = run(ws, "intent", "declare", "Introduce trajektories properly")
    check("typo intent warns with fuzzy suggestion",
          "names no concept" in out and "Did you mean: 'Trajectory'" in out, out)
    out = run(ws, "intent", "declare", "Expand on gravity next")
    check("matched intent shows deterministic preview",
          "Preview:" in out and "Gravity — realized" in out, out)

    # Citations are not prerequisites: a historical_reference node must not
    # generate ordering suggestions even when the graph links it.
    run(ws, "concept", "add", "Maupertuis", "--kind", "historical_reference")
    run(ws, "concept", "link", "Maupertuis", "permits", "Trajectory")
    out = run(ws, "guide")
    check("historical references never drive prerequisite gaps",
          "Maupertuis" not in out, out)

    # Guidance ordering must only ever use relations the shared vocabulary
    # accepts — every surface (extraction, triage, guidance) reads one set.
    from authorlm.extraction import VALID_RELATIONS
    from authorlm.guidance import PREREQUISITE_FIRST, PREREQUISITE_SECOND
    check("ordering relations all belong to the shared relation vocabulary",
          (PREREQUISITE_FIRST | PREREQUISITE_SECOND) <= VALID_RELATIONS,
          str((PREREQUISITE_FIRST | PREREQUISITE_SECOND) - VALID_RELATIONS))
    run(ws, "session", "end")


def scenario_objection(root: Path) -> None:
    print("Scenario C — unanswered objection")
    ws = root / "c"
    ms = ws / "manuscript"
    write(ms / "01.md", "# One\n\nBeing is becoming.\n")
    run(ws, "init", "--name", "book", "--path", str(ms))
    run(ws, "session", "start")
    run(ws, "concept", "add", "Parmenides objection", "--kind", "objection",
        "--notes", "If being is one, becoming is illusion.")
    run(ws, "collect")
    out = run(ws, "guide")
    check("open objection surfaces", "Parmenides objection" in out, out)
    check("objection notes carried into explanation", "illusion" in out, out)
    run(ws, "concept", "add", "Reply to Parmenides")
    run(ws, "concept", "link", "Reply to Parmenides", "answers", "Parmenides objection")
    out = run(ws, "guide")
    check("answered objection no longer surfaces",
          "Address the open objection" not in out, out)

    # Retire ("delete") a concept: it disappears from list and reasoning,
    # its edges go with it, and re-adding revives it.
    out = run(ws, "concept", "retire", "Parmenides objection")
    check("retire reports edges retired", "1 related edge(s)" in out, out)
    out = run(ws, "concept", "list")
    check("retired concept hidden from list", "Parmenides objection" not in out, out)
    out = run(ws, "concept", "list", "--all")
    check("retired concept visible with --all",
          "Parmenides objection (objection, retired" in out, out)
    out = run(ws, "guide")
    check("retired objection never surfaces in guidance",
          "Parmenides objection" not in out, out)
    out = run(ws, "concept", "retire", "Parmenides objection", expect_exit=True)
    check("double retire blocked", "already retired" in out, out)
    out = run(ws, "concept", "add", "Parmenides objection")
    check("re-adding revives as declared", "declared" in out, out)

    # Adding an existing concept with notes refines them in place; adding
    # without notes must never clobber what's there.
    run(ws, "concept", "add", "Parmenides objection",
        "--notes", "Reframed: becoming needs no defense.")
    out = run(ws, "concept", "show", "Parmenides objection")
    check("re-add with notes updates the existing concept",
          "becoming needs no defense" in out, out)
    run(ws, "concept", "add", "Parmenides objection")
    out = run(ws, "concept", "show", "Parmenides objection")
    check("re-add without notes leaves notes untouched",
          "becoming needs no defense" in out, out)
    run(ws, "session", "end")


def _stub_draft_reply(user: str) -> str:
    """The canned `write draft` replies, one per shape the parser must
    handle. The scenario selects a shape by putting a sentinel in the
    beat spec's notes, which travels into block C."""
    import re as _re

    if "STUB-BLOCKED" in user:
        return ("BLOCKED\nThe beat needs an attribution that is in neither "
                "the brief nor the digest.\n\nQUESTION\nWhose account of "
                "therapeutic culture did you have in mind here?")
    if "STUB-NO-DRAFT" in user:
        return "WHY\nI realize Choice.\n\nSELF-CHECK\nbeat spec: ok"
    if "STUB-EMPTY-WHY" in user:
        return "WHY\n\nSELF-CHECK\nbeat spec: ok\n\nDRAFT\nSome prose."
    if "STUB-EMPTY-DRAFT" in user:
        return "WHY\nI realize Choice.\n\nSELF-CHECK\nbeat spec: ok\n\nDRAFT\n   \n  "
    if "STUB-LABEL-IN-PROSE" in user:
        # A beat whose PROSE contains a bare BLOCKED line. It is
        # manuscript text, not a refusal — the label only refuses when it
        # comes first.
        return ("WHY\nI realize Choice.\n\nSELF-CHECK\nbeat spec: ok\n\n"
                "DRAFT\nThe author's own word for the state was this:\n"
                "BLOCKED\nand the sentence continues past it.")
    if "STUB-MARKERS" in user:
        return ("WHY\nI realize Choice.\n\nSELF-CHECK\nbeat spec: ok\n\n"
                "DRAFT\nA beat carrying <<a reserved marker>> in its prose.")
    match = _re.search(r'"n": (\d+)', user)
    n = match.group(1) if match else "?"
    return (f"WHY\nThe stub realizes the beat's concepts and follows the "
            f"plan for n={n}.\n\n"
            f"SELF-CHECK\nbeat spec: makes the claim\nstyle law: obeyed\n"
            f"graph: the author's terms\ngrounding: nothing outside the "
            f"payload\nbudget: within\n\n"
            f"DRAFT\nThe stub's drafted prose for beat n={n}.")


class StubLLMHandler(http.server.BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible /chat/completions endpoint with canned
    replies keyed on the system prompt."""

    # Every request the stub has served, ever. The write path's design
    # invariant (§13, I4) is that the new verbs add NO model call, and
    # the only way to assert "zero calls" is to count them.
    REQUESTS = 0

    # Every (system, user) turn the stub has been sent. Counting calls
    # proves "no new model call"; keeping the payloads proves what was —
    # and was not — put in front of a model (Scenario W3/E9: the
    # mid-rewrite marker must never be MINED AS PROSE).
    PAYLOADS: list[tuple[str, str]] = []

    def do_POST(self):
        StubLLMHandler.REQUESTS += 1
        finish = "stop"
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        system = body["messages"][0]["content"]
        user = body["messages"][-1]["content"]
        StubLLMHandler.PAYLOADS.append((system, user))
        if "You are drafting ONE beat" in system:
            # `write draft` (design-write-draft.md). Keyed on the
            # registered prompt's opening line; the failure shapes are
            # keyed on sentinels the scenario plants in a beat spec, so
            # every branch is reachable without a network or a key.
            if "STUB-500" in user:
                self.send_response(500)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            # A provider refusal and a truncation both arrive as HTTP 200
            # with a body — the finish_reason is the ONLY thing that
            # distinguishes them from a beat (design risk R-c: this
            # manuscript is a plausible refusal target).
            if "STUB-REFUSED" in user:
                finish = "content_filter"
            elif "STUB-TRUNCATED" in user:
                finish = "length"
            else:
                finish = "stop"
            content = _stub_draft_reply(user)
        elif "sole task is to find aliasing statements" in system:
            content = json.dumps({
                "aliases": [
                    {"alias": "Distinction", "canonical": "Choice",
                     "sentence": "What ye call Distinction is the choice "
                                 "of qualities parted."},
                    {"alias": "Persistence", "canonical": "Becoming",
                     "sentence": "What ye call Persistence is the becoming "
                                 "of shapes."},
                    {"alias": "Field", "canonical": "Ghost",
                     "sentence": "A sentence naming an unknown concept."},
                    # Q/alias-guide-flip: a genuine naming ceremony — the
                    # bestowed name is not yet its own concept anywhere in
                    # this test — the positive case the flipped ALIAS GUIDE
                    # exists to admit.
                    {"alias": "Elective Ground", "canonical": "Choice",
                     "sentence": "We call it Elective Ground, the resolved "
                                 "name for Choice."},
                    # Q/alias-retired-guard: the bestowed name is a RETIRED
                    # concept — a side door around the retired-name ban.
                    {"alias": "RetiredAliasCandidate", "canonical": "Choice",
                     "sentence": "We once called Choice by the name "
                                 "RetiredAliasCandidate."},
                ],
            })
        elif ("LOAD-BEARING units of thought" in system
                or "ONLY relationships among the known concepts" in system):
            content = json.dumps({
                "concepts": [
                    {"name": "Choice", "kind": "concept", "notes": "primitive act"},
                    {"name": "Distinction", "kind": "concept", "notes": ""},
                    {"name": "Field", "kind": "concept", "notes": "space of moves"},
                    {"name": "Becoming", "kind": "weird-kind", "notes": "coerced"},
                    {"name": "Basilides", "kind": "historical_reference", "notes": ""},
                ],
                "links": [
                    {"from": "Choice", "relation": "distinguishes", "to": "Distinction"},
                    {"from": "Distinction", "relation": "creates", "to": "Field"},
                    {"from": "Field", "relation": "not-a-relation", "to": "Choice"},
                    {"from": "Ghost", "relation": "permits", "to": "Choice"},
                ],
            })
        elif "editorial analyst reconstructing" in system:
            content = json.dumps({
                "decisions": [
                    {"action": "opened the section with a sailing metaphor "
                               "before the formal treatment",
                     "pattern": "Open concept introductions with a lived "
                                "metaphor before formal definition."},
                    {"action": "linked becoming back to choice",
                     "pattern": None},
                ],
                "outcome": "Becoming developed through the sailing metaphor.",
            })
        elif "editor's working memory" in system:
            # The essay summarizer (authorlm/prompts/summarizer.md). Cites
            # paragraph [1] only, so the drafting context's coverage
            # reporting has something real to be loud about.
            unit = user.split("THE UNIT: ", 1)[1].split("\n", 1)[0]
            content = f"MOVES: [1] the stub's canned summary of {unit}."
        elif "distill" in system.lower():
            # Stands in for semantic matching: if an equivalent belief is
            # already on the menu the stub MATCHes it (exercising the
            # reinforce path), otherwise it proposes one as NEW. The real
            # distiller decides by principle; the stub decides by substring,
            # which is enough to drive both branches deterministically.
            statement = "Introduce intuition before formalism."
            menu_id = next(
                (line.split("|")[0].strip() for line in user.splitlines()
                 if "|" in line and "intuition before formalism" in line.lower()),
                None)
            if "one-off" in user:
                content = "NONE"
            elif menu_id:
                content = f"MATCH: {menu_id}"
            elif "SCOPE:" in system:
                content = (f"NEW\nSCOPE: manuscript\nSTATEMENT: {statement}\n"
                           f"EXAMPLE: the stub's canned case")
            else:
                # EXAMPLE is required: a belief with no grounding case is
                # refused as a platitude.
                content = f"NEW: {statement}\nEXAMPLE: the stub's canned case"
        else:
            content = "A drafted bridge paragraph from the stub."
        payload = json.dumps({
            "choices": [{"message": {"content": content},
                         "finish_reason": finish}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 45},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep test output clean
        pass


def scenario_llm_and_unregister(root: Path) -> None:
    print("Scenario E — LLM features (stub server) and unregister")
    server = http.server.HTTPServer(("127.0.0.1", 0), StubLLMHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ws = root / "e"
        ms = ws / "manuscript"
        write(ms / "01-choice.md", CH1)
        write(ms / "02-fields.md", CH2)
        # CH3 gives 'Field' cross-file recurrence; 'Becoming' stays a
        # single lowercase mention — below the ratified recurrence bar.
        write(ms / "03-trajectories.md", CH3)
        stub_config = (
            "# AuthorLM test configuration (TOML — native comments)\n"
            "[llm]\n"
            "enabled = true\n"
            'provider = "openai"\n'
            f'base_url = "http://127.0.0.1:{server.server_port}/v1"\n'
            'model = "stub"\n'
        )
        write(ws / ".authorlm" / "config.toml", stub_config)

        out = run(ws, "init", "--name", "book", "--path", str(ms))
        check("init auto-collects before extraction", "Collected revision v1" in out, out)
        check("init extracts concepts", "Extracted 4 new concept(s)" in out, out)
        check("the recurrence bar drops the single-context candidate",
              "1 below the recurrence bar" in out and "'Becoming'" in out, out)
        check("valid links kept; unknown relation and unknown concept dropped "
              "— honestly labeled, not lumped into 'malformed'",
              "2 inferred relationship(s)" in out
              and "1 relationship(s) named an unrecognized relation" in out
              and "1 relationship(s) named an unknown concept" in out
              and "malformed" not in out, out)
        check("step reports live calls and token usage",
              "LLM: 1 live call(s) (120 in / 45 out tokens)" in out, out)

        out = run(ws, "concept", "list")
        check("extracted concept realized against text",
              "Choice (concept, realized" in out, out)
        check("below-bar concept was never admitted",
              "Becoming" not in out, out)
        check("exempt-kind unmentioned concept stays a declared hypothesis",
              "Basilides (historical_reference, declared" in out, out)
        check("historical_reference kind preserved",
              "Basilides (historical_reference" in out, out)
        check("extracted nodes marked unconfirmed",
              "Choice (concept, realized, unconfirmed" in out, out)
        check("extracted links are inferred hypotheses",
              "Choice —distinguishes→ Distinction (inferred" in out, out)

        out = run(ws, "briefing")
        check("briefing lists extracted concepts for triage",
              "Extracted concepts awaiting your confirmation (4)" in out, out)
        out = run(ws, "concept", "confirm", "Basilides", "--kind", "question")
        check("confirm retypes and confirms a node",
              "Confirmed concept 'Basilides' (question)" in out, out)
        out = run(ws, "briefing")
        check("confirmed node leaves the triage list",
              "awaiting your confirmation (3)" in out, out)

        out = run(ws, "extract")
        check("incremental re-extraction is a no-op without changes",
              "Nothing new since the last extraction" in out, out)
        out = run(ws, "extract", "--full")
        check("full re-extraction is idempotent",
              "Extracted 0 new concept(s)" in out and "full manuscript" in out, out)

        # --- triage: one-keystroke decisions over unconfirmed nodes ---
        import subprocess
        long_note = ("a definition long enough that any preview shortening would cut it: "
                     + "clause after clause of qualifying detail, " * 3).strip().rstrip(",")
        run(ws, "concept", "edit", "Distinction", "--notes", long_note)
        result = subprocess.run(
            [sys.executable, "main.py", "--workspace", str(ws), "concept", "triage"],
            input="n\nthe primal act of cutting\nk\nh\nr\nx\n",
            capture_output=True, text=True, timeout=60,
            cwd=Path(__file__).resolve().parent.parent,
        )
        check("triage reword updates notes in place",
              "(notes updated)" in result.stdout
              and "the primal act of cutting" in result.stdout,
              result.stdout)
        check("triage walks unconfirmed nodes", "[1/3] Choice" in result.stdout,
              result.stdout)
        flat_stdout = " ".join(result.stdout.split())
        check("triage shows full untruncated notes",
              " ".join(long_note.split()) in flat_stdout, result.stdout)
        check("triage summary reports decisions",
              "Triage: kept 1, retyped 1, retired 1, merged 0, skipped 0."
              in result.stdout,
              result.stdout)
        check("triage echoes each decision",
              "(kept)" in result.stdout and "(retired)" in result.stdout
              and "(→ historical_reference)" in result.stdout,
              result.stdout)
        out = run(ws, "concept", "list")
        check("triage retype applied", "Distinction (historical_reference" in out, out)
        check("triage retire applied", "Field" not in out, out)

        out = run(ws, "concept", "confirm", "--all")
        check("bulk confirm sweeps the rest (nothing left — triage settled all)",
              "Confirmed 0 concept(s)." in out, out)
        out = run(ws, "briefing")
        check("nothing left to triage after bulk confirm",
              "Extracted concepts awaiting your confirmation" not in out, out)

        # --- the learning loop: triage feedback reaches the next extraction ---
        from authorlm.db import Database as _DB
        from authorlm.extraction import triage_feedback
        _db = _DB(ws / ".authorlm" / "authorlm.db")
        _mid = _db.one("SELECT id FROM manuscripts WHERE name = 'book'")["id"]
        feedback = triage_feedback(_db, _mid)
        check("rejections enter the extraction prompt",
              "REJECTED" in feedback and "Field" in feedback, feedback)
        check("retype precedents enter the extraction prompt",
              "Distinction: concept → historical_reference" in feedback, feedback)
        check("confirmed exemplars enter the extraction prompt",
              "Choice (concept)" in feedback, feedback)
        out = run(ws, "extract", "--full")
        check("full audit re-creates nothing directly",
              "Extracted 0 new concept(s)" in out, out)
        check("full audit surfaces conflicts as proposals instead",
              "2 proposal(s) against settled knowledge" in out, out)
        out = run(ws, "concept", "list", "--all")
        check("retired concept stays retired after re-extraction",
              "Field (concept, retired" in out, out)

        write(ms / "03-new-material.md",
              "# New\n\n" + "\n\n".join(f"Paragraph {i} of fresh prose." for i in range(6)))
        out = run(ws, "collect")
        check("substantial new material hints at extract",
              "mine them for new concepts with 'extract'" in out, out)
        out = run(ws, "extract")
        check("incremental extraction targets only the changed file",
              "1 changed file(s)" in out, out)
        out = run(ws, "extract", "03-new-material.md")
        check("per-file extraction works",
              "1 selected file(s)" in out, out)

        # A configurable size cap: shrink it and expect a truncation warning.
        config_path = ws / ".authorlm" / "config.toml"
        config_path.write_text(
            stub_config + "# tiny cap to force truncation\nextraction_max_chars = 50\n"
        )
        out = run(ws, "extract", "--full")
        check("configured cap batches sections instead of truncating",
              "truncated" not in out
              and ("hierarchical" in out or "pass(es)" in out), out)

        config_path.write_text(stub_config)

        # --- proposals against settled knowledge (diff-gated) ---
        edge_id = None
        for line in run(ws, "concept", "list").splitlines():
            if "—distinguishes→" in line:
                edge_id = line.split("[")[1].split("]")[0]
        run(ws, "concept", "reject-edge", edge_id)

        # Edit text that names Choice, Field, and Distinction — only these
        # may generate proposals.
        with open(ms / "01-choice.md", "a") as fh:
            fh.write("\n\nChoice is the field where every act of distinction begins.\n")
        run(ws, "collect")
        out = run(ws, "extract")
        # The note_update and revival proposals from the --full audit are
        # still open with identical content, so only the edge is new.
        check("open proposals are not duplicated; new conflict adds one",
              "1 proposal(s) against settled knowledge" in out, out)

        out = run(ws, "briefing")
        check("briefing lists open proposals",
              "Proposals against settled knowledge (3)" in out, out)

        proposal_ids = {}
        for line in run(ws, "proposal", "list").splitlines():
            if "] (" in line:
                pid = line.split("[")[1].split("]")[0]
                kind = line.split("(")[1].split(")")[0]
                proposal_ids[kind] = pid
        check("all three proposal kinds present",
              set(proposal_ids) == {"note_update", "revival", "edge_reproposal"},
              str(proposal_ids))

        out = run(ws, "proposal", "accept", proposal_ids["note_update"])
        check("adopting a note update applies it", "Updated 'Choice'" in out, out)
        out = run(ws, "concept", "show", "Choice")
        check("adopted definition replaced the old note", "primitive act" in out, out)

        out = run(ws, "proposal", "dismiss", proposal_ids["revival"],
                  "--why", "Field stays retired; the term is incidental here")
        check("dismissing a revival keeps it retired", "Dismissed" in out, out)
        out = run(ws, "concept", "list", "--all")
        check("dismissed revival left concept retired",
              "Field (concept, retired" in out, out)

        out = run(ws, "proposal", "accept", proposal_ids["edge_reproposal"])
        check("adopting an edge re-proposal declares it",
              "Relationship restored" in out, out)
        out = run(ws, "concept", "list")
        check("edge back as declared",
              "Choice —distinguishes→ Distinction (declared" in out, out)

        # Churn guard: an edit that names no settled concept generates
        # nothing, and dismissed proposals never return verbatim.
        with open(ms / "01-choice.md", "a") as fh:
            fh.write("\nMore prose entirely without graph vocabulary.\n")
        run(ws, "collect")
        out = run(ws, "extract")
        check("no proposals without attention hits (churn guard)",
              "against settled knowledge" not in out, out)

        # --- edges-only extraction and unconfirming an edge ---
        out = run(ws, "extract", "--full", "--edges-only")
        check("edges-only extracts no concepts",
              "Extracted 0 new concept(s)" in out and "edges only" in out, out)
        # Undo a confirmed edge: back to hypothesis for re-triage.
        edge_id = None
        for line in run(ws, "concept", "list").splitlines():
            if "—distinguishes→" in line and "(declared" in line:
                edge_id = line.split("[")[1].split("]")[0]
        out = run(ws, "concept", "unconfirm", edge_id)
        check("unconfirm returns edge to hypothesis", "Back to hypothesis" in out, out)
        out = run(ws, "concept", "list")
        check("unconfirmed edge is inferred again",
              "—distinguishes→ Distinction (inferred" in out, out)

        run(ws, "session", "start")
        # The author declares Becoming — declaration bypasses the
        # recurrence bar (declaration is ratification, ratified 2026-08-08).
        out = run(ws, "concept", "add", "Becoming")
        check("author declaration bypasses the recurrence bar",
              "Becoming" in out, out)
        out = run(ws, "intent", "declare", "Develop the notion of Becoming")
        becoming_id = out.split("[")[1].split("]")[0]
        out = run(ws, "guide")
        check("bridge suggested for the declared concept", "Introduce 'Becoming'" in out, out)
        check("LLM drafts bridge text", "A drafted bridge paragraph from the stub" in out, out)

        out = run(ws, "review", "1", "--reject",
                  "--explain", "Ground every abstraction in a concrete case first")
        check("explanation distilled into normative belief",
              'seeded a candidate belief: "Introduce intuition before formalism."' in out, out)

        out = run(ws, "guide")
        out = run(ws, "review", "1", "--reject",
                  "--explain", "This was a one-off exception for this chapter")
        check("LLM declines to generalize a one-off (NONE path)",
              "seeded a candidate belief" not in out
              and "Explanation recorded as high-weight evidence" in out, out)

        # --- episode analysis: learn from the author's actual edits ---
        with open(ms / "01-choice.md", "a") as fh:
            fh.write("\n\nLike a sailor tacking, becoming threads through what "
                     "choice has opened.\n")
        run(ws, "collect")  # transitions attach to the Becoming episode
        out = run(ws, "intent", "complete", becoming_id, "--outcome", "done")
        check("intent completion triggers episode analysis",
              "Analyzed episode 'Develop the notion of Becoming'" in out, out)
        check("analysis reports inferred decisions",
              "opened the section with a sailing metaphor" in out, out)
        check("analysis seeds a candidate belief from the pattern",
              "candidate seeded" in out, out)
        out = run(ws, "belief", "list")
        check("behavior-derived belief in the belief list",
              "Open concept introductions with a lived metaphor" in out, out)
        out = run(ws, "analyze")
        check("analysis is idempotent", "No episodes awaiting analysis" in out, out)

        # --- episodic retrieval: analyzed episodes become precedents ---
        run(ws, "concept", "add", "Persistence")
        run(ws, "concept", "link", "Becoming", "permits", "Persistence")
        run(ws, "intent", "declare", "Introduce persistence")
        out = run(ws, "guide")
        check("guidance retrieves a graph-guided precedent",
              "Precedent — when you worked on 'Becoming'" in out, out)
        check("precedent carries the analyzed decision sequence",
              "opened the section with a sailing metaphor" in out, out)

        # --- alias detection: the ALIAS GUIDE was flipped (Q/alias-guide-
        # flip) — an alias must bestow a genuinely NEW name on a known
        # concept (the naming-ceremony case); it is no longer enough for
        # both names to already be known concepts. Two ways a candidate can
        # still fail: the bestowed name is already its own LIVE concept
        # ('Distinction', 'Persistence' — a merge mislabelled as an alias,
        # Q/alias-guard) or it names a RETIRED concept (a side door around
        # the retired-name ban, Q/alias-retired-guard). Only a truly new
        # name ('Elective Ground', never a concept anywhere in this test)
        # produces a proposal. ---
        run(ws, "concept", "add", "RetiredAliasCandidate")
        run(ws, "concept", "retire", "RetiredAliasCandidate")
        write(ms / "03-names.md",
              "# Names\n\nWhat ye call Distinction is the choice of "
              "qualities parted. What ye call Persistence is the becoming "
              "of shapes. We call it Elective Ground, the resolved name for "
              "Choice. We once called Choice by the name "
              "RetiredAliasCandidate.\n")
        out = run(ws, "extract", "--aliases")
        check("aliases pass reports its scope", "aliases only" in out, out)
        check("both live-concept naming sentences are refused as folds",
              "refused 2 alias proposal(s) naming an already-live concept"
              in out, out)
        check("the retired-name naming sentence is refused separately",
              "refused 1 alias proposal(s) naming a retired concept" in out,
              out)
        check("the genuinely new naming ceremony PRODUCES a proposal — "
              "the guide flip did not leave the feature dead",
              "1 proposal(s) against settled knowledge" in out, out)
        out = run(ws, "proposal", "list")
        check("no live-concept-fold or retired-name alias proposal "
              "reaches the queue",
              "'Distinction' with 'Choice'" not in out
              and "'Persistence' with 'Becoming'" not in out
              and "'RetiredAliasCandidate' with 'Choice'" not in out, out)
        check("the new naming ceremony DOES reach the queue",
              "'Elective Ground' with 'Choice'" in out, out)

        # Refused is not silently dropped: the extractor's read is recorded
        # as system-provenance evidence, exactly like what adjudication.py
        # screens out — auditable without becoming queue. The two refusal
        # reasons stay separable by signal.
        alias_guard_evidence = _db.all(
            "SELECT * FROM evidence WHERE manuscript_id = ? "
            "AND evidence_type = 'extraction_adjudication' "
            "AND signal = 'alias_fold_refused'", (_mid,))
        check("the live-concept folds are recorded as system-provenance "
              "evidence",
              len(alias_guard_evidence) == 2
              and all(row["weight"] == "low" for row in alias_guard_evidence),
              str([dict(r) for r in alias_guard_evidence]))
        alias_retired_evidence = _db.all(
            "SELECT * FROM evidence WHERE manuscript_id = ? "
            "AND evidence_type = 'extraction_adjudication' "
            "AND signal = 'alias_retired_refused'", (_mid,))
        check("the retired-name fold is recorded under a DISTINCT signal, "
              "separable from the live-concept fold",
              len(alias_retired_evidence) == 1
              and alias_retired_evidence[0]["weight"] == "low",
              str([dict(r) for r in alias_retired_evidence]))

        # The produced proposal must be genuinely adoptable, not merely
        # created: adopting it ADDS the bestowed name to the canonical
        # concept (not a merge — there was no second node to merge).
        elective_id = next(line.split("[")[1].split("]")[0]
                           for line in out.splitlines()
                           if "'Elective Ground'" in line)
        out = run(ws, "proposal", "accept", elective_id)
        check("adopting a naming-ceremony alias ADDS the name, phrased as "
              "'add', not 'merge'",
              "'Elective Ground' recorded as a new name for 'Choice'" in out,
              out)
        out = run(ws, "concept", "show", "Choice")
        check("the canonical concept now answers to the bestowed name too",
              "aliases: Elective Ground" in out
              and "We call it Elective Ground" in out, out)

        # proposals.adopt()/demote_to_edge() still know how to settle an
        # alias proposal once one actually exists (e.g. the legacy queue
        # this guard is forward-only and does not touch) — exercised
        # directly here, since extraction itself can no longer manufacture
        # a MERGE-mode proposal for a pair of already-live concepts.
        from authorlm import proposals as _prop

        distinction_row = _db.one(
            "SELECT id FROM concept_nodes WHERE manuscript_id = ? AND name = ?",
            (_mid, "Distinction"))
        persistence_row = _db.one(
            "SELECT id FROM concept_nodes WHERE manuscript_id = ? AND name = ?",
            (_mid, "Persistence"))
        _prop.create(_db, _mid, "alias", distinction_row["id"],
                    {"alias": "Distinction", "canonical": "Choice",
                     "sentence": "What ye call Distinction is the choice "
                                 "of qualities parted."})
        _prop.create(_db, _mid, "alias", persistence_row["id"],
                    {"alias": "Persistence", "canonical": "Becoming",
                     "sentence": "What ye call Persistence is the becoming "
                                 "of shapes."})
        out = run(ws, "proposal", "list")
        check("a manually-raised alias proposal still lists with both names",
              "'Distinction' with 'Choice'" in out
              and "'Persistence' with 'Becoming'" in out, out)
        persistence_id = next(line.split("[")[1].split("]")[0]
                              for line in out.splitlines() if "'Persistence'" in line)
        distinction_id = next(line.split("[")[1].split("]")[0]
                              for line in out.splitlines() if "'Distinction'" in line)
        out = run(ws, "proposal", "edge", persistence_id)
        check("alias proposal demotes to a generalizes edge",
              "Becoming —generalizes→ Persistence" in out, out)
        out = run(ws, "proposal", "accept", distinction_id)
        check("alias proposal adopts as a merge",
              "Merged 'Distinction' into 'Choice'" in out, out)
        out = run(ws, "concept", "show", "Distinction")
        # Choice already carries the 'Elective Ground' alias from the
        # naming-ceremony proposal adopted above, so the aliases list is
        # order-sensitive — check membership, not a fixed prefix.
        alias_line = next((l for l in out.splitlines() if "aliases:" in l), "")
        check("merged alias resolves to canonical with the sentence absorbed",
              "Distinction" in [a.strip() for a in alias_line.split(":", 1)[-1].split(",")]
              and "What ye call Distinction" in out, out)

        # A merged-away name is an alias now, not a banned retiree — the
        # extractor re-proposing it must resolve, never suggest revival.
        run(ws, "extract", "--full")
        out = run(ws, "proposal", "list")
        check("no revival proposal for a name living on as an alias",
              "revive retired concept 'Distinction'" not in out, out)

        # An oversized aliases sweep must fall back to file-by-file passes —
        # a full sweep is full, never a silently truncated prefix — and the
        # alias guard must hold across every pass of that hierarchical sweep,
        # not just a single payload.
        ws2 = root / "e2"
        ms2 = ws2 / "manuscript"
        write(ms2 / "01-choice.md",
              "# A\n\nWhat ye call Distinction is the choice of qualities "
              "parted.\n")
        write(ms2 / "02-shapes.md",
              "# B\n\nWhat ye call Persistence is the becoming of shapes.\n")
        write(ws2 / ".authorlm" / "config.toml",
              stub_config + "extraction_max_chars = 130\n")
        run(ws2, "init", "--name", "book2", "--path", str(ms2))
        # This tiny two-file world leaves the stub's concepts below the
        # recurrence bar; the aliasing test needs them live, so the
        # author declares them (declaration bypasses the bar).
        for name in ("Persistence", "Choice", "Distinction", "Becoming"):
            run(ws2, "concept", "add", name)
        out = run(ws2, "extract", "--aliases")
        check("oversized aliases sweep goes hierarchical",
              "hierarchical" in out and "aliases only" in out, out)
        check("the alias guard holds across every pass of the hierarchical "
              "sweep — both folds refused, none queued",
              "refused 2 alias proposal(s) naming an already-live concept"
              in out
              and "proposal(s) against settled knowledge" not in out, out)

        # --- collect --auto (the watcher's path) runs the analyzers itself ---
        write(ms / "04-auto.md", "# Auto\n\nBecoming continues apace.\n")
        out = run(ws, "collect", "--auto")
        check("auto collect runs incremental analysis",
              "Auto-analysis:" in out, out)

        run(ws, "session", "end")

        out = run(ws, "unregister", "book")
        check("unregister reports purged rows", "Unregistered 'book'" in out, out)
        check("unregister leaves files alone", "not touched" in out, out)
        out = run(ws, "status", expect_exit=True)
        check("no manuscript remains after unregister", "no manuscript" in out, out)

        out = run(ws, "init", "--name", "book", "--path", str(ms), "--no-extract")
        check("re-init after unregister works, --no-extract skips LLM",
              "Registered manuscript" in out and "Extract" not in out, out)
        out = run(ws, "concept", "list")
        check("clean slate after unregister", "Concept Graph is empty" in out, out)

        # ============ Scenario G — TOC authority, precedence, plan ============
        wg = root / "g"
        mg = wg / "manuscript"
        write(mg / "z-first.md", "# One\n\nAlpha opens everything here.\n")
        write(mg / "a-second.md", "# Two\n\nBeta rests on alpha throughout.\n")
        write(mg / "toc.toml",
              '[[chapter]]\nfile = "z-first.md"\n\n'
              '[[chapter]]\nfile = "a-second.md"\n# Contents\n')
        write(wg / ".authorlm" / "config.toml", stub_config)
        run(wg, "init", "--name", "book", "--path", str(mg), "--no-extract")
        run(wg, "concept", "add", "Alpha")
        run(wg, "concept", "add", "Beta")
        run(wg, "concept", "add", "Contents")  # appears only in toc.toml
        run(wg, "concept", "link", "Beta", "depends_on", "Alpha")
        out = run(wg, "collect")
        check("TOC order suppresses the false alphabetical gap",
              "Prerequisite gaps" not in out, out)
        check("toc.toml is structure, not content (no realization from it)",
              "Concept realized: 'Contents'" not in out, out)
        out = run(wg, "concept", "list")
        check("definition precedence follows reading order",
              "Alpha (concept, realized, introduced in z-first.md" in out, out)

        write(mg / "b-extra.md", "# Extra\n\nUnlisted prose.\n")
        run(wg, "collect")
        out = run(wg, "briefing")
        check("briefing flags files missing from toc.toml",
              "missing from toc.toml" in out and "b-extra.md" in out, out)

        # Primary location re-points when introducing text is deleted.
        write(mg / "z-first.md", "# One\n\nAn opening without the old term.\n")
        out = run(wg, "collect")
        check("primary location re-pointed after deletion",
              "Primary location of 'Alpha' moved: z-first.md → a-second.md" in out,
              out)

        # Vanished concept → author decides (dismiss = placeholder).
        write(mg / "a-second.md", "# Two\n\nBeta rests on nothing now.\n")
        out = run(wg, "collect")
        check("vanished concept surfaced for decision",
              "'Alpha' no longer appear" in out and "proposal review" in out, out)
        pid = None
        for line in run(wg, "proposal", "list").splitlines():
            if "(vanished)" in line:
                pid = line.split("[")[1].split("]")[0]
        out = run(wg, "proposal", "dismiss", pid)
        check("dismissed vanished concept becomes a placeholder",
              "declared placeholder" in out, out)
        out = run(wg, "concept", "list")
        check("placeholder is declared again",
              "Alpha (concept, declared" in out, out)

        # Adopt path: a vanished concept the author lets go is retired.
        write(mg / "b-extra.md", "# Extra\n\nGamma stands briefly here.\n")
        run(wg, "concept", "add", "Gamma")
        run(wg, "collect")
        write(mg / "b-extra.md", "# Extra\n\nNothing remains.\n")
        run(wg, "collect")
        gid = None
        for line in run(wg, "proposal", "list").splitlines():
            if "(vanished)" in line and "Gamma" in line:
                gid = line.split("[")[1].split("]")[0]
        out = run(wg, "proposal", "accept", gid)
        check("adopted vanished concept is retired", "Retired 'Gamma'" in out, out)

        # --- plan: placement from graph + TOC ---
        # State here: Beta realized in a-second.md; Alpha is a declared
        # placeholder linked Beta depends_on Alpha.
        run(wg, "intent", "declare", "Reintroduce alpha properly")
        out = run(wg, "plan")
        check("plan places concept near realized neighbor",
              "Introduce 'Alpha'" in out
              and "in or just after a-second.md (near 'Beta')" in out, out)
        check("plan cites the matching intent",
              'serves your active intent: "Reintroduce alpha properly"' in out, out)
        run(wg, "concept", "add", "Delta")
        out = run(wg, "plan")
        check("unconnected concept gets standalone placement",
              "Introduce 'Delta'" in out and "new standalone document" in out, out)
        # _drafts/ is a generic underscore-prefixed directory, invisible to
        # observation regardless of what wrote into it (iter_manuscript_paths,
        # revisions.py) — write into it directly rather than via a drafting
        # feature.
        write(mg / "_drafts" / "alpha.md", "Scratch notes, never observed.\n")
        out = run(wg, "collect")
        check("_drafts is invisible to observation", "No changes" in out, out)

        # Prerequisite ordering between two UNREALIZED concepts — the plan's
        # core "what to write first" promise. The one existing plan scenario
        # above only links a realized concept to an unrealized one, which
        # never populates write_first (realized nodes are filtered out
        # before prerequisites_of runs).
        run(wg, "concept", "add", "Epsilon")
        run(wg, "concept", "add", "Zeta")
        run(wg, "concept", "link", "Epsilon", "leads_to", "Zeta")
        out = run(wg, "plan")
        check("dependent concept names its unrealized prerequisite",
              "write first: Epsilon" in out, out)
        lines = out.splitlines()
        epsilon_at = next(i for i, l in enumerate(lines)
                          if "Introduce 'Epsilon'" in l)
        zeta_at = next(i for i, l in enumerate(lines)
                       if "Introduce 'Zeta'" in l)
        check("the prerequisite is listed ahead of its dependent",
              epsilon_at < zeta_at, out)

        # --- hierarchical extraction under a small cap ---
        write(wg / ".authorlm" / "config.toml",
              stub_config + "extraction_max_chars = 60\n")
        out = run(wg, "extract", "--full")
        check("oversized full extraction goes hierarchical",
              "hierarchical:" in out and "in reading order" in out, out)
        write(wg / ".authorlm" / "config.toml", stub_config)

        out = run(ws, "unregister", "-m", "book")
        check("unregister accepts -m instead of positional",
              "Unregistered 'book'" in out, out)
        out = run(ws, "unregister", expect_exit=True)
        check("unregister without any name errors cleanly",
              "usage: unregister" in out, out)
    finally:
        server.shutdown()


def run_stdin(workspace: Path, stdin_text: str, *argv: str,
              expect_exit: bool = False) -> str:
    """run() with stdin replaced — the write loop's prose and plan JSON
    travel over stdin. io.StringIO reports isatty() False, matching a pipe."""
    old = sys.stdin
    sys.stdin = io.StringIO(stdin_text)
    try:
        return run(workspace, *argv, expect_exit=expect_exit)
    finally:
        sys.stdin = old


ESSAY = """# The Essay

The old opening paragraph, soon to be raw material.

The old second paragraph, about fields and their positions.
"""

BEAT_PLAN = json.dumps([
    {"role": "opener", "concepts": ["Choice"], "budget": 60,
     "notes": "open with the primal act"},
    {"role": "development", "concepts": ["Distinction"], "budget": 80},
    {"role": "close", "concepts": ["Field"], "budget": 60},
])

DRAFT_1 = ("Every act of thought begins with a choice; the essay begins "
           "there too, deliberately.")
DRAFT_2 = ("Distinction is the visible form the choice takes, considered "
           "abstractly.")
DRAFT_2B = ("To choose is to distinguish: watch any morning decision and "
            "the distinction is already there.")
DRAFT_2B_REWORDED = ("To choose is to distinguish: watch any morning "
                     "decision closely and the distinction is already there, "
                     "unasked for.")
DRAFT_4 = "A field of further choices opens from each distinction made."


def scenario_write_loop(root: Path) -> None:
    print("Scenario W — beat-by-beat write loop (autoregressive writeup)")
    server = http.server.HTTPServer(("127.0.0.1", 0), StubLLMHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ws = root / "w"
        ms = ws / "manuscript"
        write(ms / "01-choice.md", CH1)
        write(ms / "02-essay.md", ESSAY)
        write(ws / ".authorlm" / "config.toml",
              "[llm]\nenabled = true\nprovider = \"openai\"\n"
              f"base_url = \"http://127.0.0.1:{server.server_port}/v1\"\n"
              "model = \"stub\"\n")
        run(ws, "init", "--name", "book", "--path", str(ms))
        run(ws, "session", "start")
        out = run(ws, "intent", "declare", "Rewrite the essay on Choice")
        intent_id = out.split("[")[1].split("]")[0]

        # --- gates -------------------------------------------------------
        out = run(ws, "intent", "declare", "Abandoned side-quest")
        abandoned_id = out.split("[")[1].split("]")[0]
        run(ws, "intent", "abandon", abandoned_id, "--outcome", "changed plans")
        out = run_stdin(ws, "", "write", "start", "02-essay.md",
                        "--intent", abandoned_id, expect_exit=True)
        check("start blocked on an intent that is not active",
              "not active" in out, out)

        out = run_stdin(ws, "", "write", "start", "02-essay.md",
                        "--intent", intent_id, expect_exit=True)
        check("start blocked without a style attachment",
              "no attached style guide" in out, out)
        run(ws, "style", "guide", "House")
        run(ws, "style", "attach", "02-essay.md", "House")

        # A checked-out file blocks initiation (the Doc is the working copy).
        from authorlm.db import Database as _DB
        _db = _DB(ws / ".authorlm" / "authorlm.db")
        _row = _db.one("SELECT * FROM manuscripts WHERE name = 'book'")
        _meta = json.loads(_row["metadata"] or "{}")
        _meta["gdocs"] = {"02-essay.md": {"doc_id": "stub", "checked_out": True}}
        _db.update("manuscripts", _row["id"], {"metadata": json.dumps(_meta)})
        out = run_stdin(ws, "", "write", "start", "02-essay.md",
                        "--intent", intent_id, expect_exit=True)
        check("start blocked while checked out to Docs",
              "checked out to Google Docs" in out, out)
        _meta["gdocs"]["02-essay.md"]["checked_out"] = False
        _db.update("manuscripts", _row["id"], {"metadata": json.dumps(_meta)})

        # --- summary freshness gate (design §12.4 item 2) -----------------
        # Once summaries are drafting input, a stale one is a lie about the
        # text here exactly as it is in the critique pass — same predicate
        # (passes.summaries_ready), same refusal, and no --force.
        out = run_stdin(ws, "", "write", "start", "02-essay.md",
                        "--intent", intent_id, expect_exit=True)
        check("start blocked while a before/after summary is MISSING, "
              "naming the file and the one command that fixes it",
              "missing summaries: 01-choice.md" in out
              and "summarize rebuild" in out
              and "will not read a lie about the text" in out, out)
        check("a blocked start leaves the manuscript file untouched — the "
              "gate runs before the pin/truncate/collect sequence",
              (ms / "02-essay.md").read_text() == ESSAY, "")
        check("no --force escape hatch is offered (parity with the critique "
              "pass, which offers none for summaries)",
              "--force" not in out, out)
        run(ws, "summarize", "rebuild", "--all")

        # A summary that has gone stale blocks just as hard as a missing one.
        write(ms / "01-choice.md", CH1 + "\nA paragraph added after the "
                                         "summary was built.\n")
        run(ws, "collect")
        out = run_stdin(ws, "", "write", "start", "02-essay.md",
                        "--intent", intent_id, expect_exit=True)
        check("start blocked while a before/after summary is STALE "
              "(the text moved under it)",
              "stale summaries (text changed): 01-choice.md" in out, out)
        check("the stale-blocked start also left the file untouched",
              (ms / "02-essay.md").read_text() == ESSAY, "")
        write(ms / "01-choice.md", CH1)
        run(ws, "collect")

        out = run_stdin(ws, "", "write", "start", "02-essay.md",
                        "--intent", intent_id)
        check("writeup starts: pins the source and truncates",
              "Pinned v" in out and "truncated" in out, out)
        check("the truncated file holds the mid-rewrite PLACEHOLDER on disk "
              "— byte for byte, nothing else — so another session that "
              "opens it finds an explanation rather than a blank essay "
              "(design §14.3)",
              (ms / "02-essay.md").read_text() == _api.PLACEHOLDER,
              repr((ms / "02-essay.md").read_text()))
        check("write start prints the L1 drafting context — the "
              "before/after essay summaries the beat loop conditions on "
              "(design §12.4 item 1; nothing on the write path read them "
              "before)",
              "DRAFTING CONTEXT — 02-essay.md" in out
              and "BEFORE" in out and "AFTER" in out
              and "[01-choice.md]" in out, out)
        check("the printed context carries the real stored summary and its "
              "freshness state, and reports incomplete paragraph coverage "
              "LOUDLY (design §12.4 item 4's default: informational, never "
              "blocking — the stub summary cites only ¶1 of three)",
              "[01-choice.md] (fresh)" in out
              and "the stub's canned summary of 01-choice.md" in out
              and "coverage INCOMPLETE: ¶2, ¶3 uncited" in out, out)
        out = run_stdin(ws, "", "write", "status")
        check("write status — the resume entry point — reprints it, "
              "recomputed from the summaries as they stand now",
              "DRAFTING CONTEXT — 02-essay.md" in out
              and "[01-choice.md]" in out, out)
        out = run_stdin(ws, "", "write", "start", "02-essay.md",
                        "--intent", intent_id, expect_exit=True)
        check("second writeup on the same file blocked",
              "already active" in out, out)

        # --- plan --------------------------------------------------------
        out = run_stdin(ws, DRAFT_1, "write", "propose", "--why", "opener",
                        expect_exit=True)
        check("propose before a plan is blocked",
              "no ratified beat plan" in out, out)
        out = run_stdin(ws, BEAT_PLAN, "write", "plan")
        check("plan ratified with three beats", "3 beat(s) ahead" in out, out)
        out = run_stdin(ws, "[]", "write", "plan", "--replace", expect_exit=True)
        check("empty beat list rejected",
              "non-empty JSON array" in out, out)
        out = run_stdin(ws, "[1, 2]", "write", "plan", "--replace", expect_exit=True)
        check("non-object beat spec rejected",
              "must be a JSON object" in out, out)
        out = run_stdin(ws, BEAT_PLAN, "write", "plan", expect_exit=True)
        check("re-planning without --replace blocked",
              "pass --replace" in out, out)

        # --- beat 1: accept as proposed -----------------------------------
        out = run_stdin(ws, DRAFT_1, "write", "propose", expect_exit=True)
        check("propose without --why blocked", "--why is required" in out, out)
        out = run_stdin(ws, DRAFT_1, "write", "propose",
                        "--why", "realizes Choice; opener per the plan")
        check("draft registered for beat 1",
              "n=1" in out and "Draft registered" in out, out)
        # A mid-writeup guidance run must not clobber the pending proposal
        # (GUIDANCE_KINDS allowlist).
        run(ws, "guide")
        out = run_stdin(ws, "", "write", "status")
        check("pending proposal survives a guidance run",
              "Pending proposal" in out, out)
        out = run_stdin(ws, "", "write", "accept")
        check("beat 1 accepted as proposed", "n=1 accepted" in out, out)
        check("accepted text landed in the file",
              DRAFT_1 in (ms / "02-essay.md").read_text(), out)

        # --- learnings: stdin-only (a positional after -m trips argparse) ---
        out = run_stdin(ws, "author tightens openers — propose tighter",
                        "write", "learn")
        check("learning recorded from stdin", "Learning recorded (1" in out, out)
        out = run_stdin(ws, "", "write", "learn", expect_exit=True)
        check("learn without stdin errors cleanly",
              "expects the lesson on stdin" in out, out)
        out = run_stdin(ws, "", "write", "status")
        check("status shows recorded learnings",
              "learning: author tightens openers" in out, out)

        # --- beat 2: reject with reason, redraft, accept reworded ---------
        run_stdin(ws, DRAFT_2, "write", "propose",
                  "--why", "realizes Distinction")
        out = run_stdin(ws, "", "write", "reject", expect_exit=True)
        check("reject without a reason blocked at the tool level",
              "--reason is required" in out, out)
        out = run_stdin(ws, "", "write", "reject",
                        "--reason", "Too abstract; ground it in a lived moment")
        check("rejection recorded with cursor unchanged",
              "n=2 rejected" in out and "Redraft" in out, out)
        check("explained rejection seeds a candidate belief",
              "Seeded candidate belief" in out
              and "Introduce intuition before formalism." in out, out)
        run_stdin(ws, DRAFT_2B, "write", "propose",
                  "--why", "redraft: grounded in a lived moment")
        out = run_stdin(ws, DRAFT_2B_REWORDED, "write", "accept")
        check("reworded accept records a modified decision",
              "n=2 modified" in out, out)
        check("the author's rewording is what landed in the file",
              DRAFT_2B_REWORDED in (ms / "02-essay.md").read_text()
              and DRAFT_2B not in (ms / "02-essay.md").read_text(), out)

        # --- replan: replace the remaining beat ---------------------------
        out = run_stdin(ws, json.dumps([{"role": "close",
                                         "concepts": ["Field"],
                                         "budget": 60}]),
                        "write", "plan", "--replace")
        check("replace keeps written beats and renumbers ahead",
              "kept 2 written" in out and "1 beat(s) ahead" in out, out)
        out = run_stdin(ws, "", "write", "status")
        check("replacement beat has a fresh stable n (never reused)",
              "n=4" in out, out)

        # --- beat 4: accept and complete -----------------------------------
        run_stdin(ws, DRAFT_4, "write", "propose", "--why", "realizes Field")
        out = run_stdin(ws, "", "write", "accept")
        check("plan reports complete after the last beat",
              "Plan complete" in out, out)
        out = run_stdin(ws, "", "write", "complete")
        check("complete closes the writeup with verdict tallies",
              "completed — 3 beat(s)" in out
              and "accepted 2" in out and "modified 1" in out
              and "rejected 1" in out, out)
        check("complete points at intent completion",
              "intent complete" in out, out)
        text = (ms / "02-essay.md").read_text()
        check("final file is the three beats in order, old text gone",
              text.index(DRAFT_1) < text.index(DRAFT_2B_REWORDED) < text.index(DRAFT_4)
              and "old opening paragraph" not in text, text)

        # --- reconstruction: beats and verdicts are one query --------------
        wu = _db.one("SELECT * FROM writeups WHERE status = 'completed'")
        rows = _db.all(
            "SELECT gh.batch_index, gh.state FROM guidance_history gh "
            "WHERE gh.batch_id = ? ORDER BY gh.batch_index, gh.created_at",
            (wu["id"],),
        )
        states = [(r["batch_index"], r["state"]) for r in rows]
        check("writeup history reconstructs from guidance rows",
              states == [(1, "accepted"), (2, "rejected"), (2, "modified"),
                         (4, "accepted")], repr(states))

        # --- compact briefing stays bounded under a huge backlog -----------
        fake_node = {"name": "N", "kind": "concept", "status": "declared",
                     "introduced_in": None, "notes": "", "aliases": "[]"}
        fake = {"since": "", "belief_changes": [], "new_beliefs": [],
                "realized_concepts": [], "contradictions": [],
                "outstanding_questions": [], "active_intents": [],
                "active_writeups": [],
                "focus_areas": [], "toc_unlisted": [], "learning_velocity": {},
                "proposals": [],
                "unconfirmed_concepts": [dict(fake_node, name=f"N{i}")
                                         for i in range(300)],
                "inferred_edges": []}
        compact = _api.compact_briefing(fake)
        uc = compact["unconfirmed_concepts"]
        check("compact briefing bounds huge backlogs",
              uc["count"] == 300 and len(uc["items"]) == _api.BRIEFING_HEAD
              and "285 more" in uc.get("more", ""),
              json.dumps(uc)[:300])

        # --- abandon restores the pinned source ----------------------------
        run(ws, "style", "attach", "01-choice.md", "House")
        # The completed writeup rewrote 02-essay.md, so its summary is now
        # stale — and 02-essay.md sits in 01-choice.md's AFTER block. The
        # freshness gate (item 2) correctly refuses until it is rebuilt.
        out = run_stdin(ws, "", "write", "start", "01-choice.md",
                        "--intent", intent_id, expect_exit=True)
        check("a writeup's own output staling a NEIGHBOUR's summary blocks "
              "the next writeup — the gate reads the whole before/after "
              "context, not just what this writeup touched",
              "stale summaries (text changed): 02-essay.md" in out, out)
        run(ws, "summarize", "rebuild", "--all")
        run_stdin(ws, "", "write", "start", "01-choice.md",
                  "--intent", intent_id)
        check("second writeup truncated its file, leaving the placeholder",
              (ms / "01-choice.md").read_text() == _api.PLACEHOLDER,
              repr((ms / "01-choice.md").read_text()))

        # BUG-2 / A1 regression: the author may type fresh draft text into
        # the truncated file before it is ever collected — write_abandon
        # must not destroy that draft without a recovery point.
        UNCOLLECTED_DRAFT = ("Draft text typed after truncation, "
                             "never collected.")
        (ms / "01-choice.md").write_text(UNCOLLECTED_DRAFT)

        out = run_stdin(ws, "", "write", "abandon")
        check("abandon restores the file", "file restored" in out, out)
        check("restored content matches the pinned version",
              (ms / "01-choice.md").read_text() == CH1, "")

        from authorlm.db import loads as _loads
        versions = _db.all(
            "SELECT files FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no", (_row["id"],))
        recovered = any(
            _loads(v["files"], {}).get("01-choice.md") == UNCOLLECTED_DRAFT
            for v in versions)
        check("abandon snapshots the uncollected draft into history "
              "before overwriting it with the restored content "
              "(BUG-2 / A1)", recovered)

        # --- placement: an essay with no toc entry yet (§12.4 item 3) -----
        # A real toc.toml now names 01/02 only, so 03-bridge.md is genuinely
        # UNLISTED: structure.reading_order appends it after 02-essay.md,
        # which would read as "the whole book is behind me". Placed after
        # 01-choice.md instead, 02-essay.md is forward-reference material.
        run(ws, "collect")
        write(ms / "03-bridge.md", "# Bridge\n\nA new essay, unplaced.\n")
        write(ms / "toc.toml",
              '[[chapter]]\nfile = "01-choice.md"\n\n'
              '[[chapter]]\nfile = "02-essay.md"\n')
        run(ws, "collect")
        run(ws, "style", "attach", "03-bridge.md", "House")
        run(ws, "summarize", "rebuild", "--all")
        out = run_stdin(ws, "", "write", "start", "03-bridge.md",
                        "--intent", intent_id, expect_exit=True)
        check("an essay with no toc.toml entry is REFUSED when it declares "
              "no placement — appending it to the end is a fallback, not a "
              "declaration, and taking it at face value inverts §7's "
              "continuity contract silently",
              "no toc.toml entry" in out and "--after start" in out
              and "--after <file it follows>" in out, out)
        check("that refusal, like the freshness gate, left the file "
              "untouched",
              (ms / "03-bridge.md").read_text() != "", "")
        out = run_stdin(ws, "", "write", "start", "03-bridge.md",
                        "--intent", intent_id, "--after", "toc.toml",
                        expect_exit=True)
        check("--after a file that EXISTS but is not a content unit "
              "(toc.toml is structure, not prose) is refused by the "
              "PLACEMENT check itself, not merely by name resolution",
              "placement 'toc.toml' is not in the manuscript's reading "
              "order" in out
              and (ms / "03-bridge.md").read_text() != "", out)
        out = run_stdin(ws, "", "write", "start", "03-bridge.md",
                        "--intent", intent_id, "--after", "nope.md",
                        expect_exit=True)
        check("--after a name matching no file at all is refused by name "
              "resolution, also before anything is truncated",
              "no document matching 'nope.md'" in out
              and (ms / "03-bridge.md").read_text() != "", out)
        out = run_stdin(ws, "", "write", "start", "03-bridge.md",
                        "--intent", intent_id, "--after", "01-choice.md")
        head, tail = out.split("AFTER", 1)
        check("--after places the new essay: the essay it follows is "
              "settled context, the one it now precedes is forward-"
              "reference material — not 'everything is behind me'",
              "placed after 01-choice.md" in out
              and "[01-choice.md]" in head and "[02-essay.md]" in tail, out)
        out = run_stdin(ws, "", "write", "status")
        check("the placement is persisted, so write status recomputes the "
              "same context on resume (a new process, no flag repeated)",
              "placed after 01-choice.md" in out
              and "[02-essay.md]" in out.split("AFTER", 1)[1], out)
        # F3: the resume entry point degrades, it does not die, when the
        # context can no longer be computed — here because the placement
        # target left the disk. Everything else about the writeup must
        # still render, or the author loses the way out (write abandon).
        choice_text = (ms / "01-choice.md").read_text()
        (ms / "01-choice.md").unlink()
        out = run_stdin(ws, "", "write", "status")
        check("write status survives a context it can no longer compute: a "
              "one-line note replaces the block and points at write "
              "abandon, and the writeup itself still renders",
              "DRAFTING CONTEXT unavailable" in out
              and "write abandon" in out
              and "Writeup [" in out and "03-bridge.md" in out, out)
        write(ms / "01-choice.md", choice_text)
        run_stdin(ws, "", "write", "abandon")

        # --- profiles: declared context, observation-invisible ------------
        run(ws, "collect")  # settle any pending changes first
        out = run_stdin(ws, "# Market\n\nRational seekers, audio-first.",
                        "profile", "set", "market")
        check("profile set stores the declaration",
              "Profile 'market' stored" in out, out)
        out = run(ws, "profile", "list")
        check("profile list shows keys with word counts",
              "market — " in out, out)
        out = run(ws, "profile", "show", "market")
        check("profile show returns content verbatim",
              "Rational seekers, audio-first." in out, out)
        out = run(ws, "collect")
        check("profiles are invisible to observation",
              "No changes" in out, out)
        from authorlm import api as _papi
        _pm = {"path": str(ms), "name": "book"}
        got = _papi.get_profile(_pm, "market")
        check("api.get_profile returns verbatim content",
              "Rational seekers" in got["content"], str(got))
        listing = _papi.get_profile(_pm)
        check("api.get_profile lists profiles",
              listing["profiles"][0]["key"] == "market", str(listing))
        from authorlm.gdocs import workspace_bridge
        wb = workspace_bridge(_pm)
        check("workspace bridge: own mapping key, no toc sync, lean manifest",
              wb.meta_key == "gdocs_workspace" and not wb.toc_sync
              and not wb.rich_manifest
              and wb.root.name == "_profiles", str(vars(wb)))
        run(ws, "session", "end")
    finally:
        server.shutdown()


# --- Scenario W2 fixtures (UC-A / UC-B, design-usecases §3) ---------------
#
# A separate workspace from Scenario W: UC-A needs a manuscript whose
# toc.toml is committed and whose summaries are fresh, plus a NAME THAT IS
# NOT ON DISK — Scenario W's workspace has an unlisted 03-bridge.md and a
# half-finished toc by the time it ends, so the create-path gates could not
# be asserted there without rewriting its state.

TOC_W2 = ('# Table of contents — hand-maintained; this comment must survive.\n'
          '\n'
          '[[chapter]]\n'
          'file = "01-choice.md"\n'
          '\n'
          '[[chapter]]\n'
          'file = "02-essay.md"\n')

BRIEF_A = ("A short essay placing attention beside choice: attention is the "
           "faculty that makes a field visible before anything is chosen "
           "within it.")

NEW_PLAN = json.dumps([
    {"role": "opener", "concepts": ["Choice"], "budget": 60,
     "notes": "claims attention precedes choice; no source beyond the brief"},
    {"role": "close", "concepts": ["Field"], "budget": 60,
     "notes": "claims the field is what attention makes visible"},
])

NEW_BEAT_1 = ("Attention is the faculty that makes a field visible before "
              "anything within it is chosen.")
NEW_BEAT_2 = ("What attention holds open, choice then cuts: the field is the "
              "standing possibility attention keeps in view.")

# UC-B: the digest of 02-essay.md's pinned original (ESSAY, above).
DIGEST_JSON = json.dumps({
    "points": [
        {"id": "p1", "load_bearing": True, "where": "¶1",
         "claim": "The old opening paragraph names the essay's ground."},
        {"id": "p2", "where": "¶2",
         "claim": "Fields have positions, and positions are distinctions."},
        {"id": "p3", "where": "¶2",
         "claim": "The essay states the choice/distinction pairing a "
                  "third time."},
    ],
    "examples": [{"id": "x1", "example": "the morning decision",
                  "serves": ["p1"], "where": "¶1"}],
    "references": [{"id": "r1", "reference": "Chapter 1 — Choice",
                    "kind": "internal", "serves": ["p2"]}],
    "inconsistencies": [
        {"id": "i1", "with": "01-choice.md", "points": ["p3"],
         "note": "01-choice.md already owns the choice/distinction pairing"}],
})

REWRITE_BRIEF = ("Keep the field/position pairing and cut anything "
                 "01-choice.md already owns. Open on the claim.")

REWRITE_PLAN = json.dumps([
    {"role": "opener", "concepts": ["Choice"], "budget": 60,
     "notes": "carries p1 — opens on the claim, not the ground"},
    {"role": "development", "concepts": ["Field"], "budget": 80,
     "notes": "carries p2 — the field and its positions"},
    {"role": "apparatus", "budget": 80,
     "notes": "the What Was Removed and Why section, drafted from the "
              "recorded removals only"},
])

REWRITE_BEAT_1 = "The ground of this essay is stated first, not approached."
REWRITE_BEAT_2 = ("A field is its positions; each position is a distinction "
                  "already made.")
REMOVAL_SECTION = """## **What Was Removed and Why**

Two arguments from the earlier version of this essay are not here. The
first duplicated the opening chapter's account of choice and distinction,
which that chapter owns and states better. The second repeated it a third
time, which bought emphasis at the cost of saying one thing twice."""


DRAFT_CONFIG_HEAD = (
    "[llm]\nenabled = true\nprovider = \"openai\"\n"
    "base_url = \"http://127.0.0.1:{port}/v1\"\nmodel = \"stub\"\n")


def _draft_config(ws: Path, port: int, writing: str = "") -> None:
    write(ws / ".authorlm" / "config.toml",
          DRAFT_CONFIG_HEAD.format(port=port) + writing)


def _block(out: str, name: str) -> str:
    """One --dry-run block's text, by its header line."""
    head = f"───── block {name} — "
    body = out.split(head, 1)[1].split("\n", 1)[1]
    return body.split("───── block ", 1)[0]


def _block_hash(out: str, name: str) -> str:
    return out.split(f"───── block {name} — ", 1)[1].split(
        "sha256 ", 1)[1].split("\n", 1)[0].strip()


def scenario_write_draft(root: Path) -> None:
    print("Scenario WD — write draft (programmatic beat generation)")
    server = http.server.HTTPServer(("127.0.0.1", 0), StubLLMHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ws = root / "wd"
        ms = ws / "manuscript"
        write(ms / "01-choice.md", CH1)
        write(ms / "02-essay.md", ESSAY)
        _draft_config(ws, server.server_port)
        run(ws, "init", "--name", "book", "--path", str(ms))
        run(ws, "session", "start")
        out = run(ws, "intent", "declare", "Rewrite the essay on Choice")
        intent_id = out.split("[")[1].split("]")[0]
        run(ws, "style", "guide", "House")
        run(ws, "style", "attach", "02-essay.md", "House")
        run(ws, "style", "add", "register",
            "Plain declarative sentences; no rhetorical questions.",
            "--guide", "House")
        run(ws, "summarize", "rebuild", "--all")
        run_stdin(ws, "The essay is about the primacy of choice.",
                  "write", "start", "02-essay.md", "--intent", intent_id)

        from authorlm.db import Database as _DB
        _db = _DB(ws / ".authorlm" / "authorlm.db")

        def cursor() -> int:
            return _db.one("SELECT * FROM writeups WHERE status = 'active'"
                           )["cursor"]

        def beat_rows() -> list:
            wu = _db.one("SELECT * FROM writeups WHERE status = 'active'")
            return [dict(r) for r in _db.all(
                "SELECT * FROM guidance_history WHERE batch_id = ? "
                "ORDER BY created_at, id", (wu["id"],))]

        # --- T10: gate parity with propose, all before any model call ----
        before = StubLLMHandler.REQUESTS
        out = run(ws, "write", "draft", expect_exit=True)
        check("draft without a ratified plan refuses exactly as propose does "
              "— plan ratification is the fabrication guard (design §13.3), "
              "so drafting without one is impossible by construction",
              "no ratified beat plan" in out, out)
        check("...and it made no model call",
              StubLLMHandler.REQUESTS == before,
              f"{before} -> {StubLLMHandler.REQUESTS}")
        # AH-0(d): the gates that sit ABOVE the dry-run return still refuse
        # a dry run. Moving the [writing]/key gates below it must not have
        # carried the fabrication guard down with them — a dry run on an
        # unplanned writeup must refuse, not print an empty payload.
        out = run(ws, "write", "draft", "--dry-run", expect_exit=True)
        check("--dry-run refuses without a ratified plan too — the gates "
              "above the dry-run return still run, so the payload is never "
              "printed for a beat that does not exist",
              "no ratified beat plan" in out
              and "───── block " not in out, out)

        plan = json.dumps([
            {"role": "opener", "concepts": ["Choice"], "budget": 60,
             "notes": "open with the primal act"},
            {"role": "development", "concepts": ["Distinction", "Choice"],
             "budget": 80, "notes": "the visible form of the act"},
            {"role": "close", "concepts": ["Ghostly Absent Concept"],
             "budget": 60, "notes": "close on the field"},
        ])
        run_stdin(ws, plan, "write", "plan")

        # --- T2: no [writing] section -------------------------------------
        before = StubLLMHandler.REQUESTS
        out = run(ws, "write", "draft", expect_exit=True)
        check("with no [writing] section the draft is REFUSED — it does not "
              "fall back to [llm] model, and the refusal names the section, "
              "the TOML to paste, and 'write propose' as the escape hatch",
              "no [writing] section" in out
              and 'model = "anthropic/claude-fable-5"' in out
              and "write propose" in out
              and "does not fall back to [llm] model" in out, out)
        check("a refused draft costs nothing: no model call, no guidance "
              "row, cursor unchanged",
              StubLLMHandler.REQUESTS == before and not beat_rows()
              and cursor() == 0,
              f"{before} -> {StubLLMHandler.REQUESTS}; {beat_rows()}")
        # AH-0(a): --dry-run is NOT gated on [writing]. The shipped config
        # has no [writing] (author ruling 2026-08-30) and the dry run is the
        # DEFAULT drafting flow under it: it hands the conversation the
        # exact payload and the registered prompt to draft against. It
        # makes no call, so gating it on the billed path's configuration
        # only withheld the audit instrument along with the call.
        before = StubLLMHandler.REQUESTS
        out = run(ws, "write", "draft", "--dry-run")
        check("with no [writing] section --dry-run still prints the whole "
              "payload and names the registered prompt — it makes no call, "
              "so it is not gated on the billed path's model, and it says "
              "so instead of naming one",
              "───── block S — " in out and "───── block A — " in out
              and "───── block C — " in out
              and "no [writing] model configured" in out
              and "write propose" in out
              and "no call made" in out
              and "Prompt: " in out and "beat-draft.md" in out, out[:900])
        check("...and it cost nothing and changed nothing: no model call, "
              "no guidance row, cursor unchanged",
              StubLLMHandler.REQUESTS == before and not beat_rows()
              and cursor() == 0,
              f"{before} -> {StubLLMHandler.REQUESTS}; {beat_rows()}")

        _draft_config(ws, server.server_port, '[writing]\nmodel = ""\n')
        out = run(ws, "write", "draft", expect_exit=True)
        check("an empty [writing] model is the same refusal (a section that "
              "is present but says nothing is not a configuration)",
              "no [writing] section" in out, out)

        # --- T9: the key refusal names the WRITING model's own vendor -----
        _draft_config(ws, server.server_port,
                      '[writing]\nmodel = "anthropic/claude-fable-5"\n')
        assert not os.environ.get("ANTHROPIC_API_KEY")
        os.environ["GEMINI_API_KEY"] = "not-the-right-vendor"
        try:
            before = StubLLMHandler.REQUESTS
            out = run(ws, "write", "draft", expect_exit=True)
        finally:
            del os.environ["GEMINI_API_KEY"]
        check("an anthropic writing model with no ANTHROPIC_API_KEY refuses "
              "by name — and a GEMINI_API_KEY sitting in the environment "
              "does NOT satisfy it (design F2: the key follows the model's "
              "own vendor, not the [llm] section's)",
              "ANTHROPIC_API_KEY" in out and "claude-fable-5" in out
              and "GEMINI" not in out, out)
        check("the key refusal also made no model call",
              StubLLMHandler.REQUESTS == before,
              f"{before} -> {StubLLMHandler.REQUESTS}")
        # AH-0(c): the same config the billed path refuses on — a writing
        # model whose vendor key is absent — still yields a dry run. The
        # payload is what the author audits and what the conversation
        # drafts against; neither needs a key.
        before = StubLLMHandler.REQUESTS
        out = run(ws, "write", "draft", "--dry-run")
        check("--dry-run succeeds with [writing] configured but no vendor "
              "key, and REPORTS the configured model (reading a name is "
              "not a gate) — the key gate stays on the billed path",
              "───── block S — " in out
              and "Payload for anthropic/claude-fable-5" in out
              and "ANTHROPIC_API_KEY" not in out, out[:600])
        check("...and that dry run made no model call either",
              StubLLMHandler.REQUESTS == before and not beat_rows()
              and cursor() == 0,
              f"{before} -> {StubLLMHandler.REQUESTS}; {beat_rows()}")

        _draft_config(ws, server.server_port,
                      '[writing]\nmodel = "stub-writer"\n')

        # --- T1/T11: payload determinism, and the placeholder ------------
        # Two validated beliefs whose CONFIDENCE order is the reverse of
        # their STATEMENT order — the payload must print them by statement.
        from authorlm.db import ko_fields as _ko

        wu_id = _db.one("SELECT * FROM writeups WHERE status = 'active'")["id"]
        mid = _db.one("SELECT * FROM manuscripts WHERE name = 'book'")["id"]
        for statement, confidence in (("Aardvark first, by statement.", 0.10),
                                      ("Zebra last, by statement.", 0.90)):
            row = _ko("bl")
            row.update(manuscript_id=mid,
                       statement=statement, status="validated",
                       confidence=confidence, supporting=0, contradicting=0,
                       outstanding_questions="[]")
            _db.insert("editorial_beliefs", row)

        before = StubLLMHandler.REQUESTS
        first = run(ws, "write", "draft", "--dry-run")
        second = run(ws, "write", "draft", "--dry-run")
        check("--dry-run makes NO model call — it is the audit instrument, "
              "not a cheap draft",
              StubLLMHandler.REQUESTS == before,
              f"{before} -> {StubLLMHandler.REQUESTS}")
        check("identical stored state gives a BYTE-IDENTICAL payload — the "
              "requirement the whole cache design rests on (design §3)",
              first == second, "")
        frame = _block(first, "A")
        check("validated beliefs are printed sorted by STATEMENT, and the "
              "confidence number is not printed at all",
              frame.index("Aardvark first") < frame.index("Zebra last")
              and "0.9" not in frame and "confidence" not in frame,
              frame[:600])
        check("concept notes come from the PLAN's declared concepts, not "
              "from the file slice (which is empty during a writeup), and a "
              "name the graph does not have is shown rather than dropped",
              "- Ghostly Absent Concept — (not in the graph)" in frame,
              frame[:900])
        law = _block(first, "S")
        check("block S is the registered prompt file plus the effective "
              "style law, in that order — the least volatile layer, and the "
              "system message",
              law.startswith("# Beat drafter")
              and "STYLE LAW" in law
              and "no rhetorical questions" in law, law[:300])
        accepted = _block(first, "B")
        check("the mid-rewrite PLACEHOLDER is never sent as prose: block B "
              "renders '(nothing accepted yet)' and the marker text appears "
              "NOWHERE in the payload",
              "(nothing accepted yet)" in accepted
              and _api.MARKER not in first, accepted)
        beat_block = _block(first, "C")
        check("block C carries this beat, the learnings, the last verdict "
              "and the output contract",
              '"n": 1' in beat_block and "LEARNINGS" in beat_block
              and "LAST VERDICT" in beat_block
              and "OUTPUT CONTRACT" in beat_block, beat_block[:400])

        # F4's guard, as a test: confidence DRIFTS on every explained
        # verdict. If the payload printed or sorted on it, block A would
        # move between every pair of beats and forfeit the cache prefix.
        _db.conn.execute("UPDATE editorial_beliefs SET confidence = 0.99 "
                         "WHERE statement = 'Aardvark first, by statement.'")
        _db.conn.commit()
        third = run(ws, "write", "draft", "--dry-run")
        check("a belief's confidence moving does NOT change block A's hash "
              "— the silent cache invalidator, closed before it shipped "
              "(design F4)",
              _block_hash(third, "A") == _block_hash(first, "A"),
              f"{_block_hash(first, 'A')} -> {_block_hash(third, 'A')}")
        run_stdin(ws, json.dumps([
            {"role": "opener", "concepts": ["Choice"], "budget": 60,
             "notes": "open with the primal act"},
            {"role": "development", "concepts": ["Distinction", "Choice"],
             "budget": 80, "notes": "the visible form of the act"},
            {"role": "close", "concepts": ["Choice"], "budget": 60,
             "notes": "close on the field"},
        ]), "write", "plan", "--replace")
        fourth = run(ws, "write", "draft", "--dry-run")
        check("a real state change — write plan --replace — DOES move block "
              "A's hash: the payload tracks stored state, it is not frozen",
              _block_hash(fourth, "A") != _block_hash(first, "A"), "")

        # --- T3/T6: the draft registers, and the usage line reports -------
        out = run(ws, "write", "draft")
        check("the verb prints the beat spec, a before-the-call line naming "
              "the model, the model's WHY, its SELF-CHECK, the prose, and "
              "the prompt-file provenance",
              "Drafting beat n=4 on stub-writer" in out
              and "WHY" in out and "SELF-CHECK" in out
              and "The stub's drafted prose for beat n=4." in out
              and "authorlm/prompts/beat-draft.md" in out
              and "Draft registered" in out, out)
        check("the usage line reports live calls, in/out tokens, cache "
              "read/write and the model — cache tokens are broken out "
              "because litellm folds them into prompt_tokens (design F5)",
              "LLM: 1 live call(s)" in out
              and "cache 0 read / 0 written" in out
              and "model stub-writer" in out, out)
        check("the zero-cache-reads warning does NOT cry wolf on a model "
              "that does not advertise prompt caching",
              "0 tokens read on this beat" not in out, out)
        rows = beat_rows()
        check("the registered row is indistinguishable in shape from one "
              "'write propose' makes: kind='beat', batch_id=writeup.id, "
              "batch_index=beat.n, state='proposed', explanation non-empty",
              len(rows) == 1 and rows[0]["kind"] == _api.BEAT_KIND
              and rows[0]["batch_id"] == wu_id
              and rows[0]["batch_index"] == 4
              and rows[0]["state"] == "proposed"
              and rows[0]["explanation"].strip(), json.dumps(rows, default=str))
        check("the --why recorded IS the model's own WHY paragraph — the "
              "evidence the verdict hangs off is the drafter's stated "
              "grounding, exactly as when the skill drafts",
              "for n=4" in rows[0]["explanation"], rows[0]["explanation"])
        out = run_stdin(ws, "", "write", "accept")
        check("the verdict path is untouched: accept appends, collects, "
              "records the review and advances the cursor",
              "n=4 accepted" in out and cursor() == 1
              and "The stub's drafted prose for beat n=4."
              in (ms / "02-essay.md").read_text(), out)
        check("the accepted beat replaced the placeholder rather than "
              "appending after it",
              _api.MARKER not in (ms / "02-essay.md").read_text(),
              (ms / "02-essay.md").read_text())

        # --- T4: supersede on redraft, both entry points -------------------
        run(ws, "write", "draft")
        run(ws, "write", "draft")
        rows = [r for r in beat_rows() if r["batch_index"] == 5]
        check("a redraft supersedes the pending proposal through the "
              "existing UPDATE — exactly one 'proposed', exactly one "
              "'superseded'",
              [r["state"] for r in rows].count("proposed") == 1
              and [r["state"] for r in rows].count("superseded") == 1,
              json.dumps([r["state"] for r in rows]))
        run_stdin(ws, "A beat the author wrote by hand.", "write", "propose",
                  "--why", "hand-drafted; the two entry points interchange")
        rows = [r for r in beat_rows() if r["batch_index"] == 5]
        check("'write propose' after 'write draft' supersedes the same way "
              "— the two entry points are interchangeable",
              [r["state"] for r in rows].count("proposed") == 1
              and [r["state"] for r in rows].count("superseded") == 2,
              json.dumps([r["state"] for r in rows]))
        out = run_stdin(ws, "", "write", "accept")
        check("accept takes the NEWEST proposal",
              "n=5 accepted" in out
              and "A beat the author wrote by hand."
              in (ms / "02-essay.md").read_text(), out)

        # --- T5: the model changed mid-writeup ----------------------------
        out = run_stdin(ws, "", "write", "status")
        check("write status shows what drafted this writeup",
              "Drafted by stub-writer." in out, out)
        _draft_config(ws, server.server_port,
                      '[writing]\nmodel = "stub-writer-2"\n')
        out = run(ws, "write", "draft")
        check("a changed [writing] model warns LOUDLY, naming both models "
              "and the beat where the seam falls",
              "DRAFTING MODEL CHANGED" in out and "stub-writer," in out
              and "stub-writer-2" in out and "seam at beat n=6" in out, out)
        check("...and it does NOT block — the draft still registers",
              "Draft registered" in out
              and [r for r in beat_rows()
                   if r["batch_index"] == 6 and r["state"] == "proposed"],
              out)
        out = run_stdin(ws, "", "write", "status")
        check("the seam stays visible on every resume, not only in the "
              "scrollback of the beat where it happened",
              "MORE THAN ONE model" in out
              and "stub-writer, stub-writer-2" in out, out)

        # --- T8: BLOCKED leaves nothing ------------------------------------
        run_stdin(ws, "", "write", "reject", "--reason",
                  "Replanning — this beat needs a different claim")
        run_stdin(ws, json.dumps([{"role": "close", "concepts": ["Choice"],
                                   "budget": 60,
                                   "notes": "STUB-BLOCKED: needs an "
                                            "ungrounded attribution"}]),
                  "write", "plan", "--replace")
        rows_before = len(beat_rows())
        cursor_before = cursor()
        disk_before = (ms / "02-essay.md").read_text()
        out = run(ws, "write", "draft", expect_exit=True)
        check("a BLOCKED reply is a legal refusal to invent: the question "
              "is printed, NOTHING is registered, the cursor does not move "
              "(design §13.3 made mechanical)",
              "BLOCKED" in out and "QUESTION" in out
              and "therapeutic culture" in out
              and "nothing was registered" in out
              and len(beat_rows()) == rows_before
              and cursor() == cursor_before, out)
        check("the BLOCKED path still reports its usage — the call happened "
              "and was billed",
              "LLM: 1 live call(s)" in out, out)

        # --- T7: every failure leaves nothing ------------------------------
        # A provider refusal and a truncation both arrive as HTTP 200 with a
        # body, so finish_reason is the only thing separating them from a
        # beat — and they must not be mistaken for each other either: one
        # says the model declined, the other says the ceiling was hit, and
        # the remedies are nothing alike.
        failures = [
            ("STUB-500: provider error", "failed after 3 attempts"),
            ("STUB-REFUSED: stop_reason refusal", "refused to answer"),
            ("STUB-TRUNCATED: hit the ceiling", "before finishing"),
            ("STUB-NO-DRAFT: no draft line", "missing DRAFT"),
            ("STUB-EMPTY-WHY: empty why", "WHY is empty"),
            ("STUB-EMPTY-DRAFT: whitespace only",
             "nothing follows the DRAFT line"),
            ("STUB-MARKERS: reserved grammar", "reserved grammar"),
        ]
        said: dict[str, str] = {}
        for notes, expected in failures:
            run_stdin(ws, json.dumps([{"role": "close",
                                       "concepts": ["Choice"],
                                       "budget": 60, "notes": notes}]),
                      "write", "plan", "--replace")
            out = run(ws, "write", "draft", expect_exit=True)
            said[notes.split(":")[0]] = out
            check(f"a failed draft says what happened ({expected!r}) and "
                  f"leaves NO pending proposal — write_propose is the last "
                  f"statement of the success path",
                  expected in out
                  and len(beat_rows()) == rows_before
                  and cursor() == cursor_before
                  and (ms / "02-essay.md").read_text() == disk_before, out)
        check("a refusal and a truncation get DISTINCT messages: one names "
              "stop_reason refusal, the other names the max_tokens ceiling "
              "and says to raise it or narrow the beat's budget",
              "refused to answer" in said["STUB-REFUSED"]
              and "refused to answer" not in said["STUB-TRUNCATED"]
              and "max_tokens" in said["STUB-TRUNCATED"]
              and "truncated beat is not a short beat"
              in said["STUB-TRUNCATED"]
              and "max_tokens" not in said["STUB-REFUSED"],
              said["STUB-REFUSED"] + "\n---\n" + said["STUB-TRUNCATED"])
        check("an unparseable reply also shows the first 400 characters of "
              "what came back, so the author can see it",
              "characters of what came back" in out, out)

        # --- a BLOCKED line INSIDE the prose is prose, not a refusal ------
        run_stdin(ws, json.dumps([{"role": "close", "concepts": ["Choice"],
                                   "budget": 60,
                                   "notes": "STUB-LABEL-IN-PROSE: the "
                                            "label appears after DRAFT"}]),
                  "write", "plan", "--replace")
        out = run(ws, "write", "draft")
        check("a bare BLOCKED line AFTER the DRAFT label is manuscript "
              "text, not a refusal — everything after DRAFT is prose, and "
              "reading a plausible bare word as a refusal would silently "
              "discard a good draft",
              "Draft registered" in out
              and "nothing was registered" not in out
              and "and the sentence continues past it." in out, out)
        row = [r for r in beat_rows() if r["state"] == "proposed"][-1]
        check("...and the BLOCKED line is registered as part of the beat, "
              "verbatim",
              "\nBLOCKED\n" in row["suggestion"]
              and row["suggestion"].endswith("continues past it."),
              row["suggestion"])
        run_stdin(ws, "", "write", "reject", "--reason",
                  "Not the close I wanted; back to the plan")

        # --- T10 (cont.): the checkout gate, and the end of the plan -------
        _row = _db.one("SELECT * FROM manuscripts WHERE name = 'book'")
        _meta = json.loads(_row["metadata"] or "{}")
        _meta["gdocs"] = {"02-essay.md": {"doc_id": "stub",
                                          "checked_out": True}}
        _db.update("manuscripts", _row["id"], {"metadata": json.dumps(_meta)})
        before = StubLLMHandler.REQUESTS
        out = run(ws, "write", "draft", expect_exit=True)
        check("draft while the file is checked out to Google Docs gives the "
              "same refusal propose gives, BEFORE any call",
              "checked out to Google Docs" in out
              and StubLLMHandler.REQUESTS == before, out)
        _meta["gdocs"]["02-essay.md"]["checked_out"] = False
        _db.update("manuscripts", _row["id"], {"metadata": json.dumps(_meta)})

        run_stdin(ws, json.dumps([{"role": "close", "concepts": ["Choice"],
                                   "budget": 60, "notes": "close it out"}]),
                  "write", "plan", "--replace")
        run(ws, "write", "draft")
        run_stdin(ws, "", "write", "accept")
        before = StubLLMHandler.REQUESTS
        out = run(ws, "write", "draft", expect_exit=True)
        check("past the last beat the draft refuses with the existing "
              "message, and makes no call",
              "all planned beats are done" in out
              and StubLLMHandler.REQUESTS == before, out)

        out = run_stdin(ws, "", "write", "complete")
        check("write complete reports what drafted the essay — a completed "
              "essay's record should say what wrote it",
              "MORE THAN ONE model" in out
              and "stub-writer, stub-writer-2" in out, out)
    finally:
        server.shutdown()


def scenario_write_new_and_digest(root: Path) -> None:
    """Scenario W2 — the two new author use cases on the beat loop:
    UC-A (a new essay from a one-paragraph brief) and UC-B (a modeled
    rewrite with a digest and removal accounting). Design:
    .ai-productionization/authorllm/design-usecases.md §3."""
    print("Scenario W2 — UC-A new essay from a brief; UC-B modeled rewrite")
    server = http.server.HTTPServer(("127.0.0.1", 0), StubLLMHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ws = root / "wn"
        ms = ws / "manuscript"
        write(ms / "01-choice.md", CH1)
        write(ms / "02-essay.md", ESSAY)
        write(ms / "toc.toml", TOC_W2)
        write(ws / ".authorlm" / "config.toml",
              "[llm]\nenabled = true\nprovider = \"openai\"\n"
              f"base_url = \"http://127.0.0.1:{server.server_port}/v1\"\n"
              "model = \"stub\"\n")
        run(ws, "init", "--name", "book", "--path", str(ms))
        run(ws, "session", "start")
        out = run(ws, "intent", "declare", "Write the essay on attention")
        intent_id = out.split("[")[1].split("]")[0]
        run(ws, "style", "guide", "House")
        run(ws, "style", "attach", "01-choice.md", "House")
        run(ws, "style", "attach", "02-essay.md", "House")
        run(ws, "summarize", "rebuild", "--all")

        from authorlm.db import Database as _DB
        from authorlm.db import loads as _loads
        _db = _DB(ws / ".authorlm" / "authorlm.db")
        _row = _db.one("SELECT * FROM manuscripts WHERE name = 'book'")

        # ---- A16-A26: the gate parade. Every refusal must leave the
        # disk exactly as it was (RISK K1) — asserted as `not exists()`,
        # never merely on the error text.
        target = ms / "03-new.md"
        gate_outs = []

        out = run_stdin(ws, BRIEF_A, "write", "start", "03-new.md", "--new",
                        "--intent", intent_id, "--style", "House",
                        expect_exit=True)
        gate_outs.append(out)
        check("A16 — --new without --after is refused with a message written "
              "for a file that is not in the reading order at all, naming "
              "both remedies",
              "does not exist yet" in out
              and "--after <file it follows>" in out
              and "--after start" in out, out)
        check("A16 — and nothing was created", not target.exists())

        out = run_stdin(ws, "", "write", "start", "03-new.md", "--new",
                        "--intent", intent_id, "--after", "01-choice.md",
                        "--style", "House", expect_exit=True)
        gate_outs.append(out)
        check("A17 — --new without a brief is refused, naming stdin and why "
              "(a brand-new file has no pinned raw material at all)",
              "brief" in out and "stdin" in out, out)
        check("A17 — and nothing was created", not target.exists())

        # F1 — the ORDER of the refusals, PINNED. With both the placement
        # and the brief missing, the PLACEMENT refusal must win: that is
        # the sequence the author signed off on (design §5.5 / MT-5), and
        # a gate parade typed at a terminal never has stdin, so a
        # brief-first order would mask every other refusal behind "give me
        # a brief" and the author would fix them one round trip at a time.
        out = run_stdin(ws, "", "write", "start", "03-new.md", "--new",
                        "--intent", intent_id, "--style", "House",
                        expect_exit=True)
        gate_outs.append(out)
        check("F1 — placement and brief both missing: the PLACEMENT refusal "
              "wins",
              "does not exist yet" in out
              and "--after <file it follows>" in out
              and "one-paragraph brief" not in out, out)
        check("F1 — and nothing was created", not target.exists())
        out = run_stdin(ws, "", "write", "start", "03-new.md", "--new",
                        "--intent", intent_id, "--after", "01-choice.md",
                        expect_exit=True)
        gate_outs.append(out)
        check("F1 — style and brief both missing: the STYLE refusal wins",
              "no attached style guide" in out
              and "one-paragraph brief" not in out, out)
        check("F1 — and nothing was created", not target.exists())

        out = run_stdin(ws, BRIEF_A, "write", "start", "03-new.md", "--new",
                        "--intent", intent_id, "--after", "01-choice.md",
                        expect_exit=True)
        gate_outs.append(out)
        check("A18 — --new without --style is refused by the style gate, "
              "with the message naming the flag (style attach cannot run "
              "first: _validate_file requires the file on disk)",
              "no attached style guide" in out and "--style <guide>" in out, out)
        check("A18 — and nothing was created", not target.exists())

        out = run_stdin(ws, BRIEF_A, "write", "start", "03-new.md", "--new",
                        "--intent", intent_id, "--after", "01-choice.md",
                        "--style", "Nope", expect_exit=True)
        gate_outs.append(out)
        check("A19 — an unknown guide is refused before anything is created",
              "no style guide named 'Nope'" in out, out)
        check("A19 — and nothing was created", not target.exists())

        out = run_stdin(ws, BRIEF_A, "write", "start", "02-essay.md", "--new",
                        "--intent", intent_id, "--after", "01-choice.md",
                        "--style", "House", expect_exit=True)
        check("A20 — --new on a name that already resolves is refused "
              "(docs._match is substring-based; a silent create-on-typo is "
              "the failure mode --new exists to prevent)",
              "already exists" in out and "drop --new" in out, out)
        check("A20 — the existing essay still holds its text",
              (ms / "02-essay.md").read_text() == ESSAY)

        out = run_stdin(ws, "", "write", "start", "02-essay.md",
                        "--intent", intent_id, "--style", "House",
                        expect_exit=True)
        check("A21 — --style without --new is refused: one way to do each "
              "thing",
              "--style is only for --new" in out, out)

        out = run_stdin(ws, "", "write", "start", "ghost.md",
                        "--intent", intent_id, expect_exit=True)
        check("A22 — without --new an unmatched name is the unchanged "
              "resolution error",
              "no document matching 'ghost.md'" in out, out)

        write(ms / "01-choice.md", CH1 + "\nA paragraph added after the "
                                         "summary was built.\n")
        run(ws, "collect")
        out = run_stdin(ws, BRIEF_A, "write", "start", "03-new.md", "--new",
                        "--intent", intent_id, "--after", "01-choice.md",
                        "--style", "House", expect_exit=True)
        gate_outs.append(out)
        check("A23 — --new still honours the summary freshness gate over the "
              "whole before/after context",
              "stale summaries (text changed): 01-choice.md" in out, out)
        check("A23 — and nothing was created", not target.exists())
        write(ms / "01-choice.md", CH1)
        run(ws, "collect")

        _meta = json.loads(_row["metadata"] or "{}")
        _meta["gdocs"] = {"03-new.md": {"doc_id": "stub", "checked_out": True}}
        _db.update("manuscripts", _row["id"], {"metadata": json.dumps(_meta)})
        out = run_stdin(ws, BRIEF_A, "write", "start", "03-new.md", "--new",
                        "--intent", intent_id, "--after", "01-choice.md",
                        "--style", "House", expect_exit=True)
        check("A24 — --new still honours the Google-Docs checkout gate",
              "checked out to Google Docs" in out, out)
        check("A24 — and nothing was created", not target.exists())
        _meta["gdocs"]["03-new.md"]["checked_out"] = False
        _db.update("manuscripts", _row["id"], {"metadata": json.dumps(_meta)})

        out = run(ws, "intent", "declare", "Abandoned side-quest")
        dead_id = out.split("[")[1].split("]")[0]
        run(ws, "intent", "abandon", dead_id, "--outcome", "changed plans")
        out = run_stdin(ws, BRIEF_A, "write", "start", "03-new.md", "--new",
                        "--intent", dead_id, "--after", "01-choice.md",
                        "--style", "House", expect_exit=True)
        check("A25 — --new still honours the active-intent gate",
              "not active" in out, out)
        check("A25 — and nothing was created", not target.exists())

        check("A26 — none of the new refusals offers a --force escape hatch",
              all("--force" not in o for o in gate_outs),
              "\n---\n".join(gate_outs))

        # ---- A1-A5: the create path itself.
        before_requests = StubLLMHandler.REQUESTS
        out = run_stdin(ws, BRIEF_A, "write", "start", "03-new.md", "--new",
                        "--intent", intent_id, "--after", "01-choice.md",
                        "--style", "House")
        check("A1 — --new creates the file empty and attaches the guide",
              "Created 03-new.md (empty)" in out
              and "style guide 'House' attached" in out, out)
        check("A1 — the file is on disk and empty",
              target.exists() and target.read_text() == "", out)
        attachment = _db.one(
            "SELECT sa.*, sg.name AS guide FROM style_attachments sa "
            "JOIN style_guides sg ON sg.id = sa.guide_id WHERE sa.file = ?",
            ("03-new.md",))
        check("A1 — the attachment is an ordinary style_attachments row, "
              "identical to one written by 'style attach'",
              attachment is not None and attachment["guide"] == "House",
              str(dict(attachment) if attachment else None))
        check("A2 — the brief is echoed at start, verbatim",
              "BRIEF" in out and BRIEF_A.split(":")[0] in out, out)
        wu_row = _db.one("SELECT * FROM writeups WHERE file = '03-new.md'")
        wu_meta = _loads(wu_row["metadata"], {})
        check("A2 — the brief and the created_file flag are persisted on the "
              "writeup (metadata JSON only — no schema change)",
              wu_meta.get("brief") == BRIEF_A
              and wu_meta.get("created_file") is True
              and wu_meta.get("placement") == "01-choice.md", str(wu_meta))
        check("A1 — the pin is the pre-writeup state, which does not contain "
              "the file at all: that is what makes 'restore to nonexistence' "
              "honest",
              "abandon deletes 03-new.md" in out, out)
        out_status = run_stdin(ws, "", "write", "status")
        check("A3 — write status reprints the brief on resume",
              "BRIEF" in out_status and BRIEF_A.split(":")[0] in out_status,
              out_status)
        head, tail = out.split("AFTER", 1)
        check("A4 — the declared placement drives the split for a file that "
              "did not exist: the essay it follows is settled context, the "
              "one it now precedes is forward-reference material",
              "placed after 01-choice.md" in out
              and "[01-choice.md]" in head and "[02-essay.md]" in tail, out)
        check("A5 — the created file needs no summary of its own: it is "
              "excluded from its own before/after context, so the freshness "
              "gate passes with every OTHER summary fresh",
              "03-new.md" not in head.split("BEFORE", 1)[1], out)
        check("I4 — write start --new, write status: zero LLM requests "
              "(no llm.py call is added to the write path)",
              StubLLMHandler.REQUESTS == before_requests,
              f"{before_requests} -> {StubLLMHandler.REQUESTS}")

        # ---- B7: a digest models a REWRITE. A writeup that created its
        # own file can never grow one, in any of the four shapes, so it can
        # never be asked to account for points that do not exist.
        for argv, label in (
                (("write", "digest"), "bare"),
                (("write", "digest", "--show"), "--show"),
        ):
            out = run_stdin(ws, "", *argv, expect_exit=True)
            check(f"B7 — write digest ({label}) is refused on a writeup that "
                  f"created its file",
                  "there is no source essay to digest" in out
                  and "03-new.md" in out, out)
        out = run_stdin(ws, DIGEST_JSON, "write", "digest", expect_exit=True)
        check("B7 — and so is the JSON form",
              "there is no source essay to digest" in out, out)
        out = run_stdin(ws, '{"p1": {"disposition": "kept"}}',
                        "write", "digest", "--dispositions", expect_exit=True)
        check("B7 — and so is the --dispositions form",
              "there is no source essay to digest" in out, out)

        # ---- A11-A15: abandon deletes what the writeup created.
        run_stdin(ws, NEW_PLAN, "write", "plan")
        run_stdin(ws, NEW_BEAT_1, "write", "propose",
                  "--why", "realizes Choice; carries the brief's first clause")
        run_stdin(ws, "", "write", "accept")
        check("A11 — the accepted beat landed in the created file",
              NEW_BEAT_1 in target.read_text())
        # RISK K2: the pre-collect must stay the FIRST statement in
        # write_abandon. Text typed into the file and never collected has
        # no other recovery point, and abandon is about to delete the file.
        UNCOLLECTED = (NEW_BEAT_1 + "\n\nA half-typed sentence, never "
                                    "collected, about to be deleted.")
        target.write_text(UNCOLLECTED)
        before_requests = StubLLMHandler.REQUESTS
        out = run_stdin(ws, "", "write", "abandon")
        check("A11 — abandon DELETES a file the writeup created, and says "
              "why: the restore target is nonexistence",
              "deleted" in out and "restore target is nonexistence" in out, out)
        check("A11 — the file is gone from disk", not target.exists(), out)
        check("I4 — write abandon makes zero LLM requests",
              StubLLMHandler.REQUESTS == before_requests,
              f"{before_requests} -> {StubLLMHandler.REQUESTS}")
        versions = _db.all(
            "SELECT files FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no", (_row["id"],))
        check("A12 — the accepted prose is preserved in version history: the "
              "essay is unpublished, not destroyed",
              any(NEW_BEAT_1 in (_loads(v["files"], {}).get("03-new.md") or "")
                  for v in versions))
        check("A12 / K2 — the never-collected text is preserved too. This is "
              "the order-swap regression: if the unlink ever moved above "
              "write_abandon's pre-collect, this text would be gone with no "
              "recovery point at all",
              any(_loads(v["files"], {}).get("03-new.md") == UNCOLLECTED
                  for v in versions))
        check("A11 — abandon reports the version the text survives in",
              "preserved in v" in out, out)
        out = run(ws, "collect")
        check("A13 — abandon is honest about the deletion: its own collect "
              "already recorded the removal, so nothing is pending",
              "No changes" in out, out)

        # A15: the author deleted the file by hand first. Abandon must
        # still close the writeup cleanly rather than traceback.
        run(ws, "summarize", "rebuild", "--all")
        run_stdin(ws, BRIEF_A, "write", "start", "04-extra.md", "--new",
                  "--intent", intent_id, "--after", "02-essay.md",
                  "--style", "House")
        (ms / "04-extra.md").unlink()
        out = run_stdin(ws, "", "write", "abandon")
        check("A15 — abandon on a created file the author already deleted "
              "succeeds and still reports the deletion",
              "deleted" in out and "04-extra.md" in out, out)
        check("A15 — and the file is still absent",
              not (ms / "04-extra.md").exists())

        # ---- F5: "restore to nonexistence" includes the attachment the
        # writeup itself wrote at start. Leaving it behind orphans a
        # style_attachments row pointing at a file that is not on disk —
        # the exact integrity rule api._validate_file exists to keep.
        def _attachment(file: str):
            return _db.one("SELECT * FROM style_attachments WHERE file = ?",
                           (file,))

        check("F5 — abandoning a CREATED writeup takes its style attachment "
              "with the file: the writeup wrote that row at start, so "
              "unwinding the writeup unwrites it",
              _attachment("04-extra.md") is None and _attachment("03-new.md")
              is None,
              str([dict(_attachment(f)) if _attachment(f) else None
                   for f in ("03-new.md", "04-extra.md")]))
        # ...and a plain rewrite's PRE-EXISTING attachment is untouched: it
        # was not the writeup's to remove.
        before_attachment = dict(_attachment("02-essay.md"))
        run_stdin(ws, "", "write", "start", "02-essay.md",
                  "--intent", intent_id)
        out = run_stdin(ws, "", "write", "abandon")
        check("F5 — a plain rewrite's abandon restores the file and leaves "
              "the attachment it did NOT create alone",
              "file restored" in out
              and _attachment("02-essay.md") is not None
              and dict(_attachment("02-essay.md")) == before_attachment,
              out)
        check("F5 — and the rewrite's file came back",
              (ms / "02-essay.md").read_text() == ESSAY)

        # ---- A6-A9: the full UC-A loop through to completion.
        run(ws, "summarize", "rebuild", "--all")
        run_stdin(ws, BRIEF_A, "write", "start", "03-new.md", "--new",
                  "--intent", intent_id, "--after", "01-choice.md",
                  "--style", "House")
        run_stdin(ws, NEW_PLAN, "write", "plan")
        run_stdin(ws, NEW_BEAT_1, "write", "propose", "--why", "opener")
        run_stdin(ws, "", "write", "accept")
        run_stdin(ws, NEW_BEAT_2, "write", "propose", "--why", "close")
        run_stdin(ws, "", "write", "accept")
        out = run_stdin(ws, "", "write", "complete")
        check("A6 — the UC-A loop completes",
              "completed — 2 beat(s)" in out, out)
        text = target.read_text()
        check("A6 — the file holds both beats in order",
              text.index(NEW_BEAT_1) < text.index(NEW_BEAT_2), text)
        check("A7 — complete registers the essay in toc.toml at the declared "
              "placement (without it the essay is finished, on disk, and "
              "structurally invisible)",
              "Registered 03-new.md in toc.toml after 01-choice.md" in out, out)
        toc_now = (ms / "toc.toml").read_text()
        check("A7 — the toc entry is there",
              'file = "03-new.md"' in toc_now, toc_now)
        check("A7 — the hand-written comment survived byte-for-byte (RISK "
              "K3: a serialize_toc_tree round trip would have dropped it)",
              "# Table of contents — hand-maintained; this comment must "
              "survive." in toc_now, toc_now)
        from authorlm.structure import reading_order as _reading_order
        order, _unlisted = _reading_order(
            {"toc.toml": toc_now, "01-choice.md": "", "02-essay.md": "",
             "03-new.md": ""})
        check("A7 — the reading order now places it between its neighbours",
              order == ["01-choice.md", "03-new.md", "02-essay.md"],
              str(order))
        check("A8 — complete reminds that the summary is now missing/stale",
              "summarize rebuild" in out, out)
        check("B31 — a writeup with no digest gets no accounting block at all",
              "UNACCOUNTED" not in out and "Removal accounting" not in out, out)
        out = run_stdin(ws, "", "write", "start", "02-essay.md",
                        "--intent", intent_id, expect_exit=True)
        check("A9 — a registered new essay then blocks its neighbour's next "
              "start until the summary is rebuilt: the toc entry is what "
              "makes the hole visible instead of silent",
              "missing summaries: 03-new.md" in out, out)
        run(ws, "summarize", "rebuild", "--all")

        # ---- F8: an essay placed with the `start` SENTINEL. "after start"
        # is not a file name and reads as one; the sentinel is the one
        # position `--after <file>` cannot express, and the report should
        # say what actually happened.
        run_stdin(ws, BRIEF_A, "write", "start", "00-front.md", "--new",
                  "--intent", intent_id, "--after", "start",
                  "--style", "House")
        run_stdin(ws, json.dumps([{"role": "opener", "budget": 40}]),
                  "write", "plan")
        run_stdin(ws, "An opening beat, before everything else.",
                  "write", "propose", "--why", "opens the book")
        run_stdin(ws, "", "write", "accept")
        out = run_stdin(ws, "", "write", "complete")
        check("F8 — the start sentinel reads as a position, not as a file "
              "named 'start'",
              "Registered 00-front.md in toc.toml at the start." in out
              and "after start" not in out, out)
        toc_now = (ms / "toc.toml").read_text()
        order, _unlisted = _reading_order(
            {"toc.toml": toc_now, "00-front.md": "", "01-choice.md": "",
             "02-essay.md": "", "03-new.md": ""})
        check("F8 — and it really does open the book",
              order[0] == "00-front.md", str(order))
        run(ws, "summarize", "rebuild", "--all")

        # ---- UC-B: the modeled rewrite of 02-essay.md.
        out = run(ws, "intent", "declare", "Rewrite the essay, same crux")
        rewrite_id = out.split("[")[1].split("]")[0]
        out = run_stdin(ws, REWRITE_BRIEF, "write", "start", "02-essay.md",
                        "--intent", rewrite_id)
        check("UC-B — a rewrite takes the brief too (the 'additional "
              "guidance to retain key points, reorder for flow')",
              "BRIEF" in out and "Keep the field/position pairing" in out, out)
        check("UC-B — and start points at the pinned original",
              "Raw material: write digest prints the pinned original." in out,
              out)
        before_requests = StubLLMHandler.REQUESTS
        out = run_stdin(ws, "", "write", "digest")
        check("B1 — bare write digest prints the pinned original verbatim, "
              "under a header naming the version and char count",
              "PINNED SOURCE — 02-essay.md" in out
              and "The old opening paragraph, soon to be raw material." in out
              and "(v" in out, out)
        check("B1 — and the file on disk still holds only the mid-rewrite "
              "placeholder (the old essay lives in the pinned version, "
              "never on disk)",
              (ms / "02-essay.md").read_text() == _api.PLACEHOLDER,
              repr((ms / "02-essay.md").read_text()))
        out = run_stdin(ws, DIGEST_JSON, "write", "digest")
        check("B2 — the digest persists and reports its counts",
              "Digest recorded: 3 point(s), 1 example(s), 1 reference(s), "
              "1 inconsistency(ies)." in out, out)
        check("B2 — the inconsistencies print one per line: they are "
              "findings the author must see",
              "i1 · vs 01-choice.md" in out, out)
        check("B2 — and the accounting starts at zero",
              "Accounting: 0/3 points dispositioned." in out, out)
        check("I4 — write digest (print and persist) makes zero LLM requests",
              StubLLMHandler.REQUESTS == before_requests,
              f"{before_requests} -> {StubLLMHandler.REQUESTS}")
        wu_b = _db.one("SELECT * FROM writeups WHERE file = '02-essay.md' "
                       "AND status = 'active'")
        check("I2 — the digest is NOT a guidance row: it is proposed by "
              "nobody, reviewed by nobody, superseded by nobody",
              _db.one("SELECT COUNT(*) AS n FROM guidance_history "
                      "WHERE batch_id = ? AND kind != 'beat'",
                      (wu_b["id"],))["n"] == 0)
        out = run_stdin(ws, "", "write", "status")
        check("B3 — the digest survives resume",
              "Digest: 3 point(s), 1 example(s), 1 reference(s), "
              "1 inconsistency(ies)." in out
              and "UNACCOUNTED (p1, p2, p3)" in out, out)
        out = run_stdin(ws, DIGEST_JSON, "write", "digest", expect_exit=True)
        check("B4 — a second digest without --replace is refused, naming "
              "the flag",
              "--replace" in out, out)
        before_requests = StubLLMHandler.REQUESTS
        out = run_stdin(ws, "", "write", "digest", "--show")
        check("B6 — --show prints the stored digest and the tally",
              '"p1"' in out and "3 UNACCOUNTED" in out, out)

        # ---- B20-B27: dispositions.
        out = run_stdin(ws, '{"p1": {"disposition": "kept", "beat": 1}}',
                        "write", "digest", "--dispositions", expect_exit=True)
        check("B25 — a beat that is not an n in this writeup's plan is "
              "refused (there is no plan yet)",
              "is not an n in this writeup's plan" in out, out)
        run_stdin(ws, REWRITE_PLAN, "write", "plan")
        out = run_stdin(ws, '{"p1": {"disposition": "kept", "beat": 99}}',
                        "write", "digest", "--dispositions", expect_exit=True)
        check("B25 — and so is a beat number outside the ratified plan",
              "beat 99 is not an n in this writeup's plan" in out, out)
        out = run_stdin(ws, '{"p2": {"disposition": "removed"}}',
                        "write", "digest", "--dispositions", expect_exit=True)
        check("B21 — a removal with no reason is refused, on the same "
              "principle as write reject --reason",
              "a removal needs a reason" in out, out)
        out = run_stdin(ws, '{"p1": {"disposition": "deferred"}}',
                        "write", "digest", "--dispositions", expect_exit=True)
        check("B22 — an unknown disposition value is refused, naming the "
              "two that exist",
              "kept, removed" in out, out)
        out = run_stdin(ws, '{"p9": {"disposition": "kept"}}',
                        "write", "digest", "--dispositions", expect_exit=True)
        check("B23 — a disposition on an id the digest does not have is "
              "refused, naming it",
              "'p9' is not a point" in out, out)
        out = run_stdin(ws, '{"x1": {"disposition": "kept"}}',
                        "write", "digest", "--dispositions", expect_exit=True)
        check("B24 — a disposition on a non-point id is refused: the "
              "accounting accounts for points",
              "'x1' is not a point id" in out, out)
        out = run_stdin(ws, '{"p1": {"disposition": "kept", "beat": 1}}',
                        "write", "digest", "--dispositions")
        check("B20 — dispositions record and tally",
              "Dispositions recorded: 1 (1 kept, 0 removed)." in out
              and "Accounting: 1 kept, 0 removed, 2 UNACCOUNTED (p2, p3)."
              in out, out)
        out = run_stdin(ws, json.dumps({
            "p1": {"disposition": "removed",
                   "reason": "changed my mind — it duplicates 01-choice.md"},
            "p3": {"disposition": "removed",
                   "reason": "duplicates 01-choice.md's choice/distinction "
                             "pairing (i1); 01-choice.md owns it"}}),
            "write", "digest", "--dispositions")
        check("B26 — merge, not replace, and the change of mind is REPORTED "
              "rather than hidden",
              "Dispositions recorded: 2 (0 kept, 2 removed); 1 overwritten."
              in out and "p1: kept → removed" in out, out)
        check("B26 — both ids are present after the merge",
              "Accounting: 0 kept, 2 removed, 1 UNACCOUNTED (p2)." in out, out)
        # B5 / RISK K7: --replace is the only way to lose recorded work.
        shrunk = json.loads(DIGEST_JSON)
        shrunk["points"] = [p for p in shrunk["points"] if p["id"] != "p3"]
        shrunk["inconsistencies"] = []
        out = run_stdin(ws, json.dumps(shrunk), "write", "digest",
                        "--replace", expect_exit=True)
        check("B5 — --replace is refused when it would drop an id that "
              "already carries a disposition",
              "refusing to replace: p3" in out, out)
        check("I4 — write digest --show / --dispositions / --replace and "
              "write plan make zero LLM requests between them: the verbs are "
              "deterministic state, gates and evidence, and every piece of "
              "model work stays in the conversation",
              StubLLMHandler.REQUESTS == before_requests,
              f"{before_requests} -> {StubLLMHandler.REQUESTS}")

        # ---- the loop, ending in the authored removal section.
        run_stdin(ws, REWRITE_BEAT_1, "write", "propose",
                  "--why", "carries p1; realizes Choice")
        run(ws, "guide")
        out = run_stdin(ws, "", "write", "status")
        check("I3 — a mid-writeup guidance run still cannot clobber a "
              "pending beat, digest or no digest",
              "Pending proposal" in out, out)
        run_stdin(ws, "", "write", "accept")
        run_stdin(ws, REWRITE_BEAT_2, "write", "propose",
                  "--why", "carries p2; realizes Field")
        run_stdin(ws, "", "write", "accept")
        run_stdin(ws, REMOVAL_SECTION, "write", "propose",
                  "--why", "the removal section; quotes the recorded reasons "
                           "for p1 and p3; introduces no new claim")
        run_stdin(ws, "", "write", "accept")

        out = run_stdin(ws, "", "write", "complete")
        check("B28 — complete WARNS and does not block on unaccounted "
              "points: an unaccounted point is the same kind of open item "
              "as an unwritten beat, and blocking would reward a fake 'kept'",
              "Removal accounting: 0 point(s) kept, 2 removed, "
              "1 UNACCOUNTED." in out
              and "UNACCOUNTED — no disposition recorded" in out
              and "p2" in out, out)
        check("B28 — the warning names the id AND its claim, so the author "
              "can act on it without going back to the digest",
              "Fields have positions" in out, out)
        wu_b = _db.one("SELECT * FROM writeups WHERE id = ?", (wu_b["id"],))
        check("B28 — and the writeup is nonetheless completed",
              wu_b["status"] == "completed", wu_b["status"])
        check("B29 — the tally is persisted, not merely printed",
              _loads(wu_b["metadata"], {}).get("accounting_at_complete")
              == {"kept": 0, "removed": 2, "unaccounted": ["p2"]},
              str(_loads(wu_b["metadata"], {}).get("accounting_at_complete")))
        text = (ms / "02-essay.md").read_text()
        check("B32 — the 'What Was Removed and Why' section is ordinary "
              "authored prose, and being the last beat is what puts it last",
              "## **What Was Removed and Why**" in text
              and text.index("## **What Was Removed and Why**")
              > text.index(REWRITE_BEAT_2), text)
        rows = _db.all(
            "SELECT batch_index, state FROM guidance_history "
            "WHERE batch_id = ? ORDER BY batch_index, created_at",
            (wu_b["id"],))
        check("B32 / I5 — the verdict pathway is unchanged: the beat order "
              "reconstructs from guidance_history by batch_id",
              [(r["batch_index"], r["state"]) for r in rows]
              == [(1, "accepted"), (2, "accepted"), (3, "accepted")],
              str([(r["batch_index"], r["state"]) for r in rows]))

        # ---- B30: fully accounted, no warning.
        run(ws, "summarize", "rebuild", "--all")
        run_stdin(ws, "", "write", "start", "02-essay.md",
                  "--intent", rewrite_id)
        run_stdin(ws, json.dumps({"points": [
            {"id": "q1", "claim": "the sole point of the second rewrite"}]}),
            "write", "digest")
        run_stdin(ws, json.dumps({"q1": {"disposition": "kept"}}),
                  "write", "digest", "--dispositions")
        run_stdin(ws, json.dumps([{"role": "opener", "budget": 40}]),
                  "write", "plan")
        run_stdin(ws, "A single beat for the second rewrite.",
                  "write", "propose", "--why", "carries q1")
        run_stdin(ws, "", "write", "accept")
        out = run_stdin(ws, "", "write", "complete")
        check("B30 — a fully accounted rewrite reports 0 UNACCOUNTED and "
              "prints no warning block",
              "Removal accounting: 1 point(s) kept, 0 removed, "
              "0 UNACCOUNTED." in out
              and "UNACCOUNTED — no disposition recorded" not in out, out)

        run(ws, "session", "end")
    finally:
        server.shutdown()


# --- Scenario W3 fixtures (parallel writeups, design §14) -----------------
#
# The author's own case: essay A comes AFTER essay B in reading order and
# both rewrites are open at once. Before this design, `write start B` left
# B empty on disk, B's stored summary read `stale`, and starting A was
# refused — with a remedy (`summarize rebuild`) that would have summarized
# the empty file and stored it as fresh.

TOC_W3 = ('[[chapter]]\nfile = "01-choice.md"\n\n'
          '[[chapter]]\nfile = "02-essay.md"\n\n'
          '[[chapter]]\nfile = "03-third.md"\n\n'
          '[[chapter]]\nfile = "04-unattached.md"\n')

W3_PLAN = json.dumps([
    {"role": "opener", "concepts": ["Choice"], "budget": 60,
     "notes": "opens on the claim"},
    {"role": "close", "concepts": ["Choice"], "budget": 60,
     "notes": "closes on the same ground"},
])
W3_BEAT_1 = "Choice is the operation by which anything comes to be for us."
W3_BEAT_2 = "To choose is to distinguish, and the distinction is the record."


def scenario_parallel_writeups(root: Path) -> None:
    """Scenario W3 — two writeups open at once (design §14). The pinned
    version is the essay; the placeholder never reaches a finished one."""
    print("Scenario W3 — parallel writeups, in-flight context")
    server = http.server.HTTPServer(("127.0.0.1", 0), StubLLMHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ws = root / "w3"
        ms = ws / "manuscript"
        write(ms / "01-choice.md", CH1)
        write(ms / "02-essay.md", CH2)
        write(ms / "03-third.md", CH3)
        write(ms / "04-unattached.md",
              "# Chapter 4 — Unattached\n\nThis chapter has no style "
              "guide attached, deliberately.\n")
        write(ms / "toc.toml", TOC_W3)
        write(ws / ".authorlm" / "config.toml",
              "[llm]\nenabled = true\nprovider = \"openai\"\n"
              f"base_url = \"http://127.0.0.1:{server.server_port}/v1\"\n"
              "model = \"stub\"\n")
        run(ws, "init", "--name", "book", "--path", str(ms))
        payload_mark = len(StubLLMHandler.PAYLOADS)
        run(ws, "session", "start")
        out = run(ws, "intent", "declare", "Rewrite two essays at once")
        intent_id = out.split("[")[1].split("]")[0]
        run(ws, "style", "guide", "House")
        for name in ("01-choice.md", "02-essay.md", "03-third.md"):
            run(ws, "style", "attach", name, "House")
        run(ws, "summarize", "rebuild", "--all")

        from authorlm import passes as _passes
        from authorlm import summaries as _sums
        from authorlm.db import Database as _DB
        _db = _DB(ws / ".authorlm" / "authorlm.db")
        _ms_row = dict(_db.one("SELECT * FROM manuscripts WHERE name = 'book'"))
        # E9's tripwire: a concept whose NAME collides with the
        # placeholder's own words. concepts._word_pattern is
        # case-insensitive, so without the blinding in api.collect this
        # would be marked realized in a truncated essay.
        run(ws, "concept", "add", "Being", "--notes",
            "the bare fact of a thing standing in the field")

        # ---- E7: RISK K1, unchanged. Every refused start leaves the disk
        # byte-identical — asserted on the BYTES, never on the error text,
        # which is also what proves no placeholder is written by a
        # refused start.
        before_bytes = {name: (ms / name).read_bytes()
                        for name in ("01-choice.md", "02-essay.md",
                                     "03-third.md", "04-unattached.md")}
        new_target = ms / "05-new.md"

        out = run_stdin(ws, "A brief for an essay that is never created.",
                        "write", "start", "05-new.md", "--new",
                        "--intent", intent_id, "--after", "nonsense.md",
                        "--style", "House", expect_exit=True)
        check("E7 — a bad --after is refused", "nonsense.md" in out, out)
        check("E7 — and nothing was created", not new_target.exists())

        out = run_stdin(ws, "A brief.", "write", "start", "05-new.md",
                        "--new", "--intent", intent_id, "--style", "House",
                        expect_exit=True)
        check("E7 — --new with no placement is refused",
              "does not exist yet" in out, out)
        check("E7 — and nothing was created", not new_target.exists())

        out = run_stdin(ws, "", "write", "start", "05-new.md", "--new",
                        "--intent", intent_id, "--after", "01-choice.md",
                        "--style", "House", expect_exit=True)
        check("E7 — --new with no brief is refused",
              "brief" in out and "stdin" in out, out)
        check("E7 — and nothing was created", not new_target.exists())

        out = run_stdin(ws, "", "write", "start", "04-unattached.md",
                        "--intent", intent_id, expect_exit=True)
        check("E7 — a rewrite with no attached style guide is refused",
              "no attached style guide" in out, out)
        check("E7 — and EVERY existing file is byte-identical to before "
              "the gate parade: a refused start writes no placeholder, "
              "because the write still sits after the last refusal",
              all((ms / name).read_bytes() == data
                  for name, data in before_bytes.items()),
              str({name: (ms / name).read_bytes()[:60]
                   for name in before_bytes}))

        # ---- E1: the author's exact scenario. B (earlier) first, then A.
        out = run_stdin(ws, "", "write", "start", "01-choice.md",
                        "--intent", intent_id)
        b_id = out.split("[")[1].split("]")[0]
        check("E4 — after write start the file is byte-equal to the "
              "PLACEHOLDER, nothing else",
              (ms / "01-choice.md").read_text() == _api.PLACEHOLDER,
              repr((ms / "01-choice.md").read_text()))

        being = _db.one("SELECT * FROM concept_nodes WHERE manuscript_id = ? "
                        "AND name = 'Being'", (_ms_row["id"],))
        check("E9 — the collect that recorded the truncation is BLIND to "
              "the placeholder: 'Being' is not marked realized in a file "
              "that holds only a marker, and its primary location is not "
              "silently repointed there",
              being["status"] == "declared"
              and being["introduced_in"] is None, str(dict(being)))

        # E9, the half that matters: an extraction that ACTUALLY RUNS
        # while the file is mid-rewrite. The payloads this module builds
        # are `=== <name> ===` labelled concatenations, so a whole-payload
        # `is_placeholder` check can never fire — the guard has to be per
        # FILE or it is dead code that reads like a guard.
        #
        # There are THREE per-file guards, on three routes into
        # `_section_payloads`, and each one has to be exercised on its own
        # route: a guard nothing reaches is how F1 happened in the first
        # place. This one is the INCREMENTAL route, which never touches
        # `_manuscript_text` at all — it builds its units from the changed
        # SECTIONS of each file, and a truncation is a changed file whose
        # "sections" are the placeholder's own paragraphs.
        mark = len(StubLLMHandler.PAYLOADS)
        write(ms / "03-third.md", CH3 + "\nA paragraph added while the "
                                        "other essay is mid-rewrite.\n")
        run(ws, "collect")
        out = run(ws, "extract")          # no --full: changed files only
        incremental = [u for s, u in StubLLMHandler.PAYLOADS[mark:]
                       if "LOAD-BEARING units of thought" in s]
        check("E9 — the INCREMENTAL path (changed sections, no --full) "
              "drops the in-flight file and still mines the essay that "
              "really changed. Its units never pass through "
              "_manuscript_text, so guard 1 cannot cover it",
              incremental and not any(_api.MARKER in u for u in incremental)
              and any("mid-rewrite" in u for u in incremental),
              next((u[:400] for u in incremental if _api.MARKER in u),
                   f"{len(incremental)} payload(s)\n{out}"))

        # And the BATCHING route: explicit files whose combined text
        # exceeds one payload, which builds its units straight off disk.
        write(ws / ".authorlm" / "config.toml",
              "[llm]\nenabled = true\nprovider = \"openai\"\n"
              f"base_url = \"http://127.0.0.1:{server.server_port}/v1\"\n"
              "model = \"stub\"\nextraction_max_chars = 300\n")
        write(ms / "03-third.md", CH3 + "\n" + ("\n".join(
            f"## Section {n}\n\nA paragraph of real prose about "
            f"trajectories, numbered {n}, long enough that the batching "
            f"path has something to batch.\n" for n in range(1, 6))))
        run(ws, "collect")
        mark = len(StubLLMHandler.PAYLOADS)
        out = run(ws, "extract", "01-choice.md", "03-third.md")
        batched = [u for s, u in StubLLMHandler.PAYLOADS[mark:]
                   if "LOAD-BEARING units of thought" in s]
        check("E9 — the BATCHING path (explicit files over the payload "
              "cap) drops it too, and still batches the essay that has "
              "real text: more than one payload, none of them the marker",
              len(batched) > 1 and not any(_api.MARKER in u for u in batched)
              and any("trajectories" in u for u in batched),
              next((u[:400] for u in batched if _api.MARKER in u),
                   f"{len(batched)} payload(s)\n{out}"))
        write(ws / ".authorlm" / "config.toml",
              "[llm]\nenabled = true\nprovider = \"openai\"\n"
              f"base_url = \"http://127.0.0.1:{server.server_port}/v1\"\n"
              "model = \"stub\"\n")
        write(ms / "03-third.md", CH3)
        run(ws, "collect")

        mark = len(StubLLMHandler.PAYLOADS)
        out = run(ws, "extract", "01-choice.md", "--full")
        check("E9 — extracting the in-flight file specifically mines "
              "NOTHING: it is dropped per file, so the payload is empty "
              "and the pass ends rather than sending a marker to a model",
              not [u for s, u in StubLLMHandler.PAYLOADS[mark:]
                   if _api.MARKER in u],
              next((u[:400] for s, u in StubLLMHandler.PAYLOADS[mark:]
                    if _api.MARKER in u), out))

        mark = len(StubLLMHandler.PAYLOADS)
        write(ms / "03-third.md", CH3 + "\nA paragraph added during the "
                                        "rewrite window.\n")
        run(ws, "collect")
        run(ws, "extract", "--full")
        during = [u for s, u in StubLLMHandler.PAYLOADS[mark:]
                  if "LOAD-BEARING units of thought" in s]
        check("E9 — and a FULL extraction during the window mines the "
              "other essays and skips only the one in flight",
              during and not any(_api.MARKER in u for u in during)
              and any("trajectory" in u for u in during),
              next((u[:400] for u in during if _api.MARKER in u),
                   f"{len(during)} payload(s)"))
        write(ms / "03-third.md", CH3)
        run(ws, "collect")

        out = run_stdin(ws, "", "write", "complete", "--writeup", b_id,
                        expect_exit=True)
        check("E5 — completing a writeup whose file is STILL only the "
              "placeholder is refused, naming write abandon: recording a "
              "marker as the finished essay would leave the real one only "
              "in the database",
              "placeholder" in out and "write abandon" in out, out)
        check("E5 — and the file is unchanged",
              (ms / "01-choice.md").read_text() == _api.PLACEHOLDER)

        out = run_stdin(ws, "", "write", "start", "02-essay.md",
                        "--intent", intent_id)
        a_id = out.split("[")[1].split("]")[0]
        check("E1 — a SECOND writeup, on a later essay, now starts while "
              "the first is still open. Before this it was refused: the "
              "truncated neighbour's summary read 'stale' and the gate "
              "would not pass a lie about the text",
              "Writeup [" in out and "02-essay.md" in out, out)
        check("E1 — and its drafting context opens by naming the essay "
              "that is being written right now, in the author's own terms",
              "1 essay in this context is being written right now" in out
              and "01-choice.md — mid-rewrite" in out
              and "slightly suboptimal" in out, out)
        check("E1 — the BEFORE entry serves the PRE-REWRITE summary, "
              "loudly labelled, rather than refusing or serving nothing",
              "[01-choice.md] (rewriting)" in out
              and "MID-REWRITE" in out
              and "canned summary of 01-choice.md" in out, out)

        # ---- E6: abandon is a byte-for-byte round trip. Done here so the
        # critique checks below have a target that is NOT itself in
        # flight — an edit pass over an in-flight file is E3's refusal.
        run_stdin(ws, "", "write", "abandon", "--writeup", a_id)
        check("E6 — abandon restores the pinned text BYTE FOR BYTE, not "
              "merely 'restored: True'",
              (ms / "02-essay.md").read_text() == CH2,
              repr((ms / "02-essay.md").read_text()))
        _row_a = _sums.all_summaries(_db, _ms_row["id"])["02-essay.md"]
        check("E6 — and the hash round trip CLOSES: the stored summary's "
              "source_hash matches the restored text again, so the entry "
              "stops reading 'stale' and both gates accept it with nothing "
              "to rebuild (it is only upstream_stale, which is tolerated: "
              "its own text did not move, its conditioning did)",
              _row_a["source_hash"] == _sums._hash(CH2)
              and {r["file"]: r["state"]
                   for r in _sums.status(_db, _ms_row)}["02-essay.md"]
              == "upstream_stale"
              and _passes.summaries_ready(_db, _ms_row, "03-third.md")["ok"],
              str(_sums.status(_db, _ms_row)))

        # ---- E2: the critique side of the same context.
        p = _passes.ensure_pass(_db, _ms_row["id"], _db.source("system"))
        # P3 (sponsor addendum) — ONE capture per invocation, counted:
        # the in-flight check, the summaries gate and the paragraph list
        # the editor model edits must all read the same snapshot, or a
        # parallel session can change an essay between the gate approving
        # it and the context being assembled from it.
        _real = {name: getattr(_sums, name)
                 for name in ("_context_units", "units")}
        _captures = []

        def _counter(name):
            def wrapper(*args, **kwargs):
                _captures.append(name)
                return _real[name](*args, **kwargs)
            return wrapper

        for _name in _real:
            setattr(_sums, _name, _counter(_name))
        try:
            ctx = _passes.build_context(_db, _ms_row, "02-essay.md", p)
        finally:
            for _name, _fn in _real.items():
                setattr(_sums, _name, _fn)
        check("E2/P3 — build_context reads the manuscript from disk ONCE, "
              "and the in-flight check, the summaries gate and the "
              "paragraph list the editor model actually edits all read "
              "that same snapshot. It used to take a separate pass for "
              "the paragraphs, which is a window in which a parallel "
              "session can change the essay AFTER the gate approved it",
              _captures == ["_context_units"], str(_captures))
        check("E2 — the edit pass over A PROCEEDS with B mid-rewrite, and "
              "reports which essays are in flight",
              ctx["inflight"] == ["01-choice.md"], str(ctx["inflight"]))
        rendered = _passes.render_user_message(ctx)
        before_block = rendered.split("BOOK CONTEXT — BEFORE", 1)[1] \
            .split("BOOK CONTEXT — AFTER", 1)[0]
        check("E2 — and the BEFORE block the editor model actually reads "
              "carries B's PRE-REWRITE summary, not a placeholder and not "
              "a blank",
              "canned summary of 01-choice.md" in before_block,
              before_block)
        out = run(ws, "critique", "run", "02-essay.md", expect_exit=True)
        check("E2 — the CLI says so too, before the model call (the run "
              "then stops on the stub's non-contract reply, which is "
              "beside the point here)",
              "being written right now" in out and "01-choice.md" in out
              and "PRE-REWRITE essay" in out
              and "Slightly suboptimal" in out, out)

        # ---- E3: and the pass over the in-flight file ITSELF is refused.
        out = run(ws, "critique", "run", "01-choice.md", expect_exit=True)
        check("E3 — an edit pass over the essay being rewritten is refused "
              "outright: what is on disk is a marker, and the editor model "
              "would propose edits to it. Both exits are named",
              "being rewritten right now" in out
              and "write complete" in out and "write abandon" in out, out)

        # ---- E8: the export path, where being wrong is unrecoverable.
        out = run(ws, "export", "md")
        check("E8 — the export NAMES the mid-rewrite essay it omitted. It "
              "used to drop a truncated essay in silence",
              "01-choice.md: mid-rewrite" in out
              and "omitted from the export" in out, out)
        exported = next((ms / "_exports").glob("*.md")).read_text()
        check("E8 — and the exported book contains neither the marker nor "
              "the pre-rewrite text: a mid-rewrite essay is not shipped, "
              "and a marker never reaches a reader",
              _api.MARKER not in exported
              and "Every act of thought begins with a choice" not in exported
              and "a trajectory is the path actually taken" in exported,
              exported[:400])

        # ---- E4: the placeholder lifecycle, asserted as a PROPERTY at
        # every stage rather than on one path.
        run_stdin(ws, W3_PLAN, "write", "plan", "--writeup", b_id)
        run_stdin(ws, W3_BEAT_1, "write", "propose", "--writeup", b_id,
                  "--why", "opens on the claim, realizing Choice")
        run_stdin(ws, "", "write", "accept", "--writeup", b_id)
        text = (ms / "01-choice.md").read_text()
        check("E4 — the FIRST accepted beat REPLACES the placeholder: the "
              "file is the beat alone, with no marker prepended to it",
              text == W3_BEAT_1 + "\n" and _api.MARKER not in text,
              repr(text))
        run_stdin(ws, W3_BEAT_2, "write", "propose", "--writeup", b_id,
                  "--why", "closes on the same ground")
        run_stdin(ws, "", "write", "accept", "--writeup", b_id)
        text = (ms / "01-choice.md").read_text()
        check("E4 — the second beat appends normally, and there is still "
              "no marker",
              W3_BEAT_1 in text and W3_BEAT_2 in text
              and _api.MARKER not in text, repr(text))
        out = run_stdin(ws, "", "write", "complete", "--writeup", b_id)
        text = (ms / "01-choice.md").read_text()
        check("E4 — and the finished essay does not contain it. Guaranteed "
              "twice over: the first accept removed it, and complete is an "
              "independent check",
              "completed" in out and _api.MARKER not in text, repr(text))

        # Every extraction call this scenario made, by its system prompt.
        # (An episode-analysis payload legitimately quotes the truncation
        # transition — that is a record of what happened on disk, not the
        # extractor being asked to mine a marker for concepts.)
        def _mined(since: int) -> list[str]:
            return [user for system, user
                    in StubLLMHandler.PAYLOADS[since:]
                    if "LOAD-BEARING units of thought" in system
                    or "ONLY relationships among the known concepts" in system
                    or "sole task is to find aliasing statements" in system]

        mined = _mined(payload_mark)
        check("E9 — and no EXTRACTION payload in this scenario was ever "
              "handed the marker as prose",
              mined and not any(_api.MARKER in u for u in mined),
              next((u[:400] for u in mined if _api.MARKER in u),
                   f"{len(mined)} extraction payload(s)"))
        check("E9 — and the run did extract, so that is not vacuous",
              len(mined) >= 1, f"{len(mined)} extraction payload(s)")
    finally:
        server.shutdown()


def scenario_doc_comments(root: Path) -> None:
    """Doc-comments ingestion: quote location, verbatim storage, dedupe,
    and the resolve round-trip against a fake Drive service."""
    print("Scenario DC — doc comments ingestion and resolution")
    from authorlm.db import Database, ko_fields
    from authorlm.gdocs import (ingest_comments, locate_quote,
                                resolve_comments_with_receipt)

    ws = root / "dc" / ".authorlm"
    ws.mkdir(parents=True)
    db = Database(ws / "authorlm.db")
    manuscript = {"id": "ms-test", "path": str(root / "dc")}
    files = {
        "one.md": ("# **Essay One**\n\nAlpha paragraph about choice.\n\n"
                   "## **The Middle**\n\nBeta paragraph, quite distinctive "
                   "text here.\n"),
        "two.md": "# **Essay Two**\n\nGamma paragraph about choice.\n",
    }

    rel, heading = locate_quote(files, "quite distinctive text")
    check("quote located to file and nearest heading",
          rel == "one.md" and heading == "**The Middle**", f"{rel}#{heading}")
    rel, heading = locate_quote(files, "about choice")
    check("ambiguous quote stays unattributed", rel is None, f"{rel}")
    rel, heading = locate_quote(files, "no such text anywhere")
    check("missing quote stays unattributed", rel is None, f"{rel}")

    long_comment = "Please rework this — " + "with care, " * 30
    comments = [
        {"id": "c1", "content": long_comment,
         "quotedFileContent": {"value": "Beta paragraph, quite distinctive "
                                        "text here."},
         "author": {"displayName": "Author"},
         "createdTime": "2026-08-03T00:00:00Z", "replies": []},
        {"id": "c2", "content": "Which essay is better?",
         "quotedFileContent": {"value": "about choice"},
         "author": {"displayName": "Author"},
         "createdTime": "2026-08-03T00:01:00Z", "replies": []},
    ]
    projection = ingest_comments(db, manuscript, comments, files)
    check("ingestion stores one row per comment",
          db.one("SELECT COUNT(*) AS n FROM doc_comments")["n"] == 2, "")
    check("comment content is stored and projected verbatim",
          projection[0]["content"] == long_comment
          and db.one("SELECT content FROM doc_comments WHERE comment_id='c1'")
          ["content"] == long_comment, projection[0]["content"])
    check("anchor quote is clamped in the projection",
          len(projection[0]["quote"]) <= 60, projection[0]["quote"])
    check("located comment carries transition-style location",
          projection[0]["location"] == "one.md#**The Middle**",
          projection[0]["location"])
    check("ambiguous comment reported unattributed",
          projection[1]["location"] == "(unattributed)",
          projection[1]["location"])
    ingest_comments(db, manuscript, comments, files)
    check("re-ingestion dedupes on comment id",
          db.one("SELECT COUNT(*) AS n FROM doc_comments")["n"] == 2, "")

    # --- tab reconciliation: adopt / re-adopt / rename / ambiguity --------
    from authorlm.gdocs import MANIFEST_TITLE, classify_tabs
    links2 = {"a.md": {"tab_id": "t.a"}, "b.md": {"tab_id": "t.b"}}
    tabs = [("t.a", "a.md"), ("t.b", "b-renamed.md"),
            ("t.m", MANIFEST_TITLE), ("t.new", "new.md"),
            ("t.re", "c.md"), ("t.dup", "a.md"), ("t.x", "notes")]
    cls = classify_tabs(tabs, links2, {"a.md", "b.md", "c.md"})
    check("unknown md tab with no local file → adopted",
          cls["adopted"] == [("new.md", "t.new")], str(cls))
    check("unknown md tab matching a local file → readopted",
          cls["readopted"] == [("c.md", "t.re")], str(cls))
    check("known id with changed title → renamed",
          cls["renamed"] == [("b.md", "b-renamed.md")], str(cls))
    check("duplicate of a live mapped tab → ambiguous, never relinked",
          cls["ambiguous"] == ["a.md"], str(cls))
    check("manifest and non-md tabs ignored",
          MANIFEST_TITLE in cls["ignored"] and "notes" in cls["ignored"],
          str(cls))
    twins = classify_tabs([("t.1", "x.md"), ("t.2", "x.md")], {}, set())
    check("two unknown tabs with the same name → both ambiguous",
          twins["ambiguous"] == ["x.md", "x.md"] and not twins["adopted"],
          str(twins))

    # --- illustration prompt mirror: pure planner + subtree finder -------
    import hashlib as _hh

    from authorlm.gdocs import illus_subtree, plan_prompt_sync

    local = {"a.md": "alpha", "b.md": "beta", "c.md": "gamma",
             "e.md": "epsilon"}
    mapped = {"b.md": {"tab_id": "t.b",
                       "pushed_hash":
                       _hh.sha256(b"beta").hexdigest()[:16]},
              "c.md": {"tab_id": "t.c", "pushed_hash": "stale"},
              "d.md": {"tab_id": "t.d", "pushed_hash": "x"},
              "e.md": {"tab_id": "t.gone", "pushed_hash": "y"}}
    children = [("t.b", "b.md"), ("t.c", "c-renamed.md"), ("t.d", "d.md"),
                ("t.h", "hand.md")]
    plan = plan_prompt_sync(local, mapped, children)
    check("plan: new file and vanished tab → create",
          plan["create"] == ["a.md", "e.md"], str(plan))
    check("plan: hash drift → rewrite; matching hash → skip",
          plan["rewrite"] == ["c.md"], str(plan))
    check("plan: mapped tab with no local file → prune",
          plan["prune"] == [("d.md", "t.d")], str(plan))
    check("plan: hand-made tab → unknown, untouched",
          plan["unknown"] == ["hand.md"], str(plan))
    check("plan: renamed tab detected",
          plan["renamed"] == [("c.md", "c-renamed.md")], str(plan))

    tree = [{"tabProperties": {"tabId": "r1", "title": "book"},
             "childTabs": [{"tabProperties": {"tabId": "e1",
                                              "title": "a.md"}}]},
            {"tabProperties": {"tabId": "il", "title": "illustrations"},
             "childTabs": [{"tabProperties": {"tabId": "p1",
                                              "title": "x.md"},
                            "childTabs": [{"tabProperties": {
                                "tabId": "p2", "title": "deep.md"}}]}]}]
    root_id, kids, ids = illus_subtree(tree, {})
    check("illus_subtree finds the reserved tab by title, with direct "
          "children and every subtree id",
          root_id == "il" and kids == [("p1", "x.md")]
          and ids == {"il", "p1", "p2"}, str((root_id, kids, ids)))
    root_id2, _, _ = illus_subtree(tree, {"_illustrations_tab": "r1"})
    check("the remembered id outranks the reserved title", root_id2 == "r1",
          str(root_id2))
    missing = illus_subtree(tree[:1], {})
    check("no reserved tab → (None, [], ∅)",
          missing == (None, [], set()), str(missing))

    from authorlm.illus import join_prompt_embeds, split_prompt_embeds

    body, embeds = split_prompt_embeds("text line\n\n![](../c.png)\n")
    check("preview embeds split from canonical text",
          body == "text line\n" and embeds == ["![](../c.png)"],
          str((body, embeds)))
    check("join restores the canonical layout",
          join_prompt_embeds(body, embeds)
          == "text line\n\n![](../c.png)\n", "")
    check("join without embeds is the body alone",
          join_prompt_embeds(body, []) == body, "")
    check("essay-form embed lines don't match the prompt-file grammar",
          split_prompt_embeds("t\n![](_illustrations/c.png)\n")
          == ("t\n![](_illustrations/c.png)\n", []), "")

    # --- manifest inventory: chapters, word counts, hierarchy, bars ------
    from authorlm.gdocs import manifest_text
    inv = [("part1.md", 0, 100), ("intro.md", 1, 400), ("notesish.md", 1, 0)]
    m = manifest_text("book", 3, inventory=inv, pushed_at="2026-08-04T00:00Z")
    check("manifest lists chapters with word counts",
          "part1.md — 100 words" in m and "intro.md — 400 words" in m, m)
    check("manifest totals and counts chapters",
          "3 chapter(s), 500 words" in m, m)
    check("manifest shows hierarchy by indentation",
          "\n    intro.md" in m and "\npart1.md" in m, m)
    check("manifest bars scale to the largest chapter",
          m.count("█") == 20 + 5 + 0, m)
    check("manifest declares itself write-only",
          "Write-only" in m and "rewritten on every push" in m, m)

    from authorlm.gdocs import chapter_stats
    sample = ("# Heading\n\nThe cat sat on the mat. It was warm. "
              "Everything considered, the philosophical implications "
              "remained extraordinarily complicated.\n")
    cs = chapter_stats(sample)
    check("chapter stats compute ASL/AWL/FRE",
          5 < cs["asl"] < 7 and 3 < cs["awl"] < 7 and 0 <= cs["fre"] <= 100,
          str(cs))
    check("headings are excluded from readability prose",
          chapter_stats("# Just A Heading\n") == {}, "")
    m2 = manifest_text("book", 3, inventory=inv,
                       stats={"intro.md": {"asl": 15.2, "awl": 4.61,
                                           "fre": 62.4}},
                       pushed_at="2026-08-04T00:00Z")
    check("manifest carries per-chapter readability codes",
          "intro.md — 400 words · ASL 15 · AWL 4.6 · FRE 62" in m2, m2)
    check("manifest explains the codes",
          "Codes: ASL avg sentence length" in m2, m2)
    check("manifest summary block computes pages and read time",
          "Statistics: 500 words total" in m2 and "pages @275" in m2
          and "h read" in m2, m2)

    # --- toc tree parse/serialize and structure classification -----------
    from authorlm.gdocs import classify_structure, walk_tabs
    from authorlm.structure import (parse_toc_tree, parents_to_tree,
                                    serialize_toc_tree, tree_to_parents)
    toc_text = (
        '[[chapter]]\nfile = "a.md"\n\n'
        '[[chapter]]\nfile = "b.md"\n\n'
        '[[chapter]]\nfile = "c.md"\nparent = "b.md"\n\n'
        '[[chapter]]\nfile = "d.md"\nparent = "c.md"\nmatter = "back"\n\n'
        '[[chapter]]\nfile = "e.md"\nparent = "b.md"\n\n'
        '[[chapter]]\nfile = "f.md"\n')
    tree = parse_toc_tree(toc_text)
    check("toc tree parses names and depths",
          tree == [("a.md", 0), ("b.md", 0), ("c.md", 1), ("d.md", 2),
                   ("e.md", 1), ("f.md", 0)], str(tree))
    from authorlm.structure import toc_attrs
    attrs = toc_attrs(toc_text)
    round_trip = serialize_toc_tree(tree, attrs)
    check("toc tree serializes back losslessly, attributes riding through",
          parse_toc_tree(round_trip) == tree
          and toc_attrs(round_trip).get("d.md") == {"matter": "back"}, round_trip)
    pairs = tree_to_parents(tree)
    check("tree_to_parents computes DFS parents",
          pairs == [("a.md", None), ("b.md", None), ("c.md", "b.md"),
                    ("d.md", "c.md"), ("e.md", "b.md"), ("f.md", None)],
          str(pairs))
    check("parents_to_tree inverts tree_to_parents",
          parents_to_tree(pairs) == tree, str(parents_to_tree(pairs)))

    # A10 — insert_toc_entry: the registration `write complete` performs for
    # an essay the loop created. PURE TEXT INSERTION (RISK K3): a
    # serialize_toc_tree round trip would silently drop hand comments and
    # any attribute the serializer does not know about.
    from authorlm.structure import insert_toc_entry
    commented = ('# hand comment, must survive\n'
                 '\n'
                 '[[chapter]]\n'
                 'file = "a.md"\n'
                 'illustrations = "3"\n'
                 '\n'
                 '[[chapter]]\n'
                 'file = "b.md"\n'
                 '\n'
                 '[[chapter]]\n'
                 'file = "c.md"\n'
                 'parent = "b.md"\n')
    inserted, stanza = insert_toc_entry(commented, "new.md", "c.md")
    check("A10 — the stanza lands after the anchor and carries the anchor's "
          "parent (structural depth is deterministic); it never guesses "
          "matter or any other semantic attribute",
          'file = "new.md"' in stanza and 'parent = "b.md"' in stanza
          and "matter" not in stanza, stanza)
    check("A10 — the insertion puts it in the right reading position",
          [n for n, _ in parse_toc_tree(inserted)]
          == ["a.md", "b.md", "c.md", "new.md"],
          str(parse_toc_tree(inserted)))
    check("A10 — the hand comment and the unknown attribute survive "
          "byte-for-byte",
          "# hand comment, must survive" in inserted
          and 'illustrations = "3"' in inserted, inserted)
    check("A10 — every line that was there before is still there, verbatim",
          all(line in inserted.splitlines()
              for line in commented.splitlines() if line.strip()),
          inserted)
    at_start, stanza = insert_toc_entry(commented, "new.md", "start")
    check("A10 — the PLACEMENT_START sentinel inserts before the first "
          "[[chapter]], and there is no anchor whose parent to carry",
          [n for n, _ in parse_toc_tree(at_start)]
          == ["new.md", "a.md", "b.md", "c.md"]
          and "parent" not in stanza, at_start)
    check("A10 — an anchor the toc does not name returns None: the caller "
          "warns and prints the stanza rather than guessing a position",
          insert_toc_entry(commented, "new.md", "nope.md") is None)
    same, stanza = insert_toc_entry(commented, "a.md", "c.md")
    check("A10 — registering a file the toc already lists is idempotent: "
          "the text comes back unchanged",
          same == commented and stanza == "", same)

    # F3 — a comment sitting in the gap belongs to the table BELOW it. The
    # walk-back to the insertion point must skip comment lines as well as
    # blanks, or the new stanza lands between the comment and the table it
    # annotates: the comment is silently re-attributed to the new essay,
    # and the new stanza is glued to its neighbour with no blank line.
    gap_comment = ('[[chapter]]\n'
                   'file = "a.md"\n'
                   '\n'
                   '# this comment annotates b.md, not whatever precedes it\n'
                   '[[chapter]]\n'
                   'file = "b.md"\n')
    inserted, _stanza = insert_toc_entry(gap_comment, "new.md", "a.md")
    lines = inserted.splitlines()
    b_at = lines.index('file = "b.md"')
    comment_at = lines.index(
        '# this comment annotates b.md, not whatever precedes it')
    new_at = lines.index('file = "new.md"')
    check("F3 — the comment stays with the table it annotates: the new "
          "stanza goes ABOVE it, not between it and b.md",
          new_at < comment_at < b_at, inserted)
    check("F3 — and the new stanza is not glued to its neighbour: a blank "
          "line separates it on both sides",
          lines[new_at - 2].strip() == ""
          and lines[new_at + 1].strip() == "", repr(lines))
    check("F3 — the reading order is still right",
          [n for n, _ in parse_toc_tree(inserted)]
          == ["a.md", "new.md", "b.md"], str(parse_toc_tree(inserted)))
    check("F3 — every original line survives verbatim",
          all(line in lines
              for line in gap_comment.splitlines() if line.strip()),
          inserted)

    # F4 — K3's purity is a claim about BYTES. A toc written on Windows (or
    # round-tripped through a tool that writes CRLF) must not be silently
    # relaid in LF: that is a rewrite of every line the insertion never
    # touched, which is exactly what "never rewrites anything it did not
    # add" forbids.
    lf_result, lf_stanza = insert_toc_entry(commented, "new.md", "c.md")
    crlf_result, crlf_stanza = insert_toc_entry(
        commented.replace("\n", "\r\n"), "new.md", "c.md")
    check("F4 — a CRLF toc comes back CRLF, with no lone LF anywhere",
          "\r\n" in crlf_result
          and crlf_result.replace("\r\n", "").count("\n") == 0,
          repr(crlf_result))
    check("F4 — and the stanza is spelled in the file's own line ending",
          "\r\n" in crlf_stanza and "\r" not in lf_stanza,
          repr(crlf_stanza))
    check("F4 — the CRLF insertion is otherwise identical to the LF one, "
          "so nothing but the separator differs",
          crlf_result.replace("\r\n", "\n") == lf_result, repr(crlf_result))
    check("F4 — an LF toc stays LF (no \\r introduced)",
          "\r" not in lf_result, repr(lf_result))
    no_trailing = commented.rstrip("\n")
    grown, _s = insert_toc_entry(no_trailing, "new.md", "c.md")
    check("F4 — a file with no trailing newline does not grow one: the "
          "insertion adds its stanza and nothing else",
          not grown.endswith("\n") and 'file = "new.md"' in grown,
          repr(grown[-60:]))

    doc_tabs = [{"tabProperties": {"tabId": "t.a", "title": "a.md"},
                 "childTabs": [
                     {"tabProperties": {"tabId": "t.s", "title": "scratch"},
                      "childTabs": [
                          {"tabProperties": {"tabId": "t.c",
                                             "title": "c.md"}}]}]}]
    props, dpairs = [], []
    walk_tabs(doc_tabs, props, dpairs)
    check("non-md tabs are transparent to md hierarchy",
          dpairs == [("a.md", None), ("c.md", "a.md")], str(dpairs))

    base = [("a.md", None), ("b.md", None)]
    moved = [("b.md", None), ("a.md", None)]
    check("structure: equal → insync",
          classify_structure(base, base, base) == "insync", "")
    check("structure: doc moved → doc_moved",
          classify_structure(moved, base, base) == "doc_moved", "")
    check("structure: local moved → local_moved",
          classify_structure(base, moved, base) == "local_moved", "")
    check("structure: both moved → conflict",
          classify_structure(moved, [("b.md", None)], base) == "conflict", "")
    check("structure: no base, additions only → doc_moved",
          classify_structure(base + [("x.md", "a.md")], base, None)
          == "doc_moved", "")
    check("structure: no base, real disagreement → unsynced",
          classify_structure(moved, base, None) == "unsynced", "")

    # --- three-way pull classification (the stale-tab false conflict) ---
    import hashlib as _hashlib

    from authorlm.gdocs import three_way
    base_text = "# One\n\nOriginal paragraph.\n"
    base_hash = _hashlib.sha256(base_text.encode()).hexdigest()[:16]
    check("three-way: identical → unchanged",
          three_way(base_text, base_text, base_hash) == "unchanged", "")
    check("three-way: only tab moved → changed (safe pull)",
          three_way("# One\n\nDoc edit.\n", base_text, base_hash) == "changed", "")
    check("three-way: only local moved → local_ahead, never conflict",
          three_way(base_text, "# One\n\nLocal edit.\n", base_hash)
          == "local_ahead", "")
    check("three-way: both moved → conflict",
          three_way("# One\n\nDoc edit.\n", "# One\n\nLocal edit.\n",
                    base_hash) == "conflict", "")
    check("three-way: no recorded base + drift → changed (Doc wins)",
          three_way("# One\n\nDoc edit.\n", base_text, None) == "changed", "")

    class FakeReplies:
        calls: list = []

        def create(self, fileId, commentId, body, fields):
            FakeReplies.calls.append((commentId, body))

            class R:
                @staticmethod
                def execute():
                    if commentId == "c2":
                        raise RuntimeError("boom")
                    return {"id": "r1"}
            return R()

    class FakeService:
        def replies(self):
            return FakeReplies()

    resolved = resolve_comments_with_receipt(
        db, FakeService(), "doc-id", "ms-test", ["c1", "c2"])
    check("resolve sends receipt replies with action=resolve",
          FakeReplies.calls[0][1]["action"] == "resolve"
          and "Ingested into AuthorLM" in FakeReplies.calls[0][1]["content"],
          repr(FakeReplies.calls[0]))
    check("successful resolve marks the row resolved",
          db.one("SELECT state FROM doc_comments WHERE comment_id='c1'")
          ["state"] == "resolved", "")
    check("failed resolve leaves the row ingested for the next pull",
          resolved == 1
          and db.one("SELECT state FROM doc_comments WHERE comment_id='c2'")
          ["state"] == "ingested", f"resolved={resolved}")


def scenario_alias_and_syllogism(root: Path) -> None:
    print("Scenario A2 — aliases and syllogism kind")
    ws = root / "alias"
    ms = ws / "manuscript"
    write(ms / "01.md", "# One\n\nBeing precedes thought.\n")
    run(ws, "init", "--name", "book", "--path", str(ms))
    run(ws, "session", "start")
    run(ws, "concept", "add", "Field of Choice")
    out = run(ws, "concept", "alias", "Field of Choice", "Chid", "Sanatana")
    check("alias records alternate names", "Chid" in out and "Sanatana" in out, out)
    out = run(ws, "concept", "show", "Chid")
    check("lookup by alias resolves to the canonical concept",
          "Field of Choice" in out, out)
    out = run(ws, "concept", "add", "Chid")
    check("adding by an alias never duplicates the concept",
          "Field of Choice" in out, out)
    write(ms / "01.md",
          "# One\n\nBeing precedes thought. The Chid sunders every quality.\n")
    run(ws, "collect")
    out = run(ws, "concept", "show", "Field of Choice")
    check("alias mention in text realizes the canonical concept",
          "realized" in out, out)
    run(ws, "concept", "add", "Pleroma")
    out = run(ws, "concept", "alias", "Pleroma", "Chid", expect_exit=True)
    check("alias colliding with another concept is refused",
          "already names" in out, out)
    run(ws, "concept", "retire", "Pleroma")
    out = run(ws, "concept", "alias", "Field of Choice", "Pleroma")
    check("aliasing a retired name absorbs it", "Pleroma" in out, out)
    out = run(ws, "concept", "show", "Pleroma")
    check("absorbed name resolves to the canonical concept",
          "Field of Choice" in out, out)
    out = run(ws, "concept", "add", "Entailment of becoming", "--kind", "syllogism")
    check("syllogism is a valid node kind", "syllogism" in out, out)
    run(ws, "concept", "link", "Entailment of becoming", "depends_on", "Field of Choice")
    run(ws, "concept", "link", "Entailment of becoming", "leads_to", "Becoming")
    out = run(ws, "concept", "show", "Entailment of becoming")
    check("syllogism premises and conclusion link with existing relations",
          "depends_on" in out and "leads_to" in out, out)

    # Merge: the duplicate's edges re-point to the canonical (already-present
    # edges retire), its name becomes an alias, the node retires.
    run(ws, "concept", "add", "The Becoming")
    run(ws, "concept", "link", "The Becoming", "contrasts_with", "Stasis")
    run(ws, "concept", "link", "Entailment of becoming", "leads_to", "The Becoming")
    out = run(ws, "concept", "merge", "Becoming", "The Becoming")
    check("merge re-points and retires edges",
          "1 edge(s) re-pointed, 1 retired" in out, out)
    out = run(ws, "concept", "show", "The Becoming")
    check("merged name is an alias of the canonical",
          "aliases: The Becoming" in out, out)
    check("duplicate's edges now live on the canonical", "Stasis" in out, out)
    out = run(ws, "concept", "merge", "No Such Canonical", "The Becoming",
              expect_exit=True)
    check("merge refuses an unknown canonical name",
          "no concept named 'No Such Canonical'" in out, out)
    out = run(ws, "concept", "merge", "Becoming", "No Such Duplicate",
              expect_exit=True)
    check("merge refuses an unknown duplicate name",
          "no concept named 'No Such Duplicate'" in out, out)

    # The [a] key in edge triage: an inferred edge between two names the
    # author declares identical merges them and settles the edge.
    run(ws, "concept", "add", "Ground")
    run(ws, "concept", "add", "Foundation")
    from authorlm.db import Database as _ADB, ko_fields as _ako
    _adb = _ADB(ws / ".authorlm" / "authorlm.db")
    _amid = _adb.one("SELECT id FROM manuscripts WHERE name = 'book'")["id"]
    _aids = {
        nm: _adb.one(
            "SELECT id FROM concept_nodes WHERE manuscript_id = ? AND name = ?",
            (_amid, nm))["id"]
        for nm in ("Ground", "Foundation")
    }
    _arow = _ako("ce")
    _arow.update(manuscript_id=_amid, from_node=_aids["Ground"],
                 relation="elaborates", to_node=_aids["Foundation"],
                 status="inferred", support=0, evidence="[]")
    _adb.insert("concept_edges", _arow)
    import subprocess as _sp
    result = _sp.run(
        [sys.executable, "main.py", "--workspace", str(ws),
         "concept", "triage", "--edges"],
        input="a\n1\n", capture_output=True, text=True, timeout=60,
        cwd=Path(__file__).resolve().parent.parent,
    )
    check("edge triage alias-merge merges the pair",
          "(merged '" in result.stdout, result.stdout)
    check("edge triage summary counts the merge",
          "merged 1" in result.stdout, result.stdout)
    out = run(ws, "concept", "show", "Ground")
    check("one node now answers to both names",
          "aliases:" in out and "Ground" in out and "Foundation" in out, out)

    # Keystrokes recorded by readline inside triage must be popped on exit
    # so the shell's up-arrow history stays clean.
    try:
        import readline
    except ImportError:
        readline = None
    if readline is not None:
        from authorlm.cli import _ephemeral_history
        base = readline.get_current_history_length()
        with _ephemeral_history():
            readline.add_history("k")
            readline.add_history("r")
        check("triage keystrokes leave no readline history",
              readline.get_current_history_length() == base,
              f"history grew: {base} → {readline.get_current_history_length()}")
    run(ws, "session", "end")


def scenario_errors(root: Path) -> None:
    print("Scenario D — guard rails")
    ws = root / "d"
    ms = ws / "manuscript"
    write(ms / "01.md", "# One\n\nText.\n")
    out = run(ws, "status", expect_exit=True)
    check("status without init errors cleanly", "no manuscript" in out, out)
    run(ws, "init", "--name", "book", "--path", str(ms))
    out = run(ws, "guide", expect_exit=True)
    check("guide without session errors cleanly", "no active session" in out, out)
    run(ws, "session", "start")
    out = run(ws, "session", "start", expect_exit=True)
    check("double session start blocked", "already active" in out, out)
    out = run(ws, "review", "1", "--accept", expect_exit=True)
    check("review before guide blocked", "no guidance" in out, out)
    out = run(ws, "guide")
    check("empty graph → abstention", "abstains" in out, out)
    out = run(ws, "review", "1", "--accept", expect_exit=True)
    check("cannot review an abstention", "abstention" in out, out)
    out = run(ws, "collect")
    out = run(ws, "session", "end")

    # improve: bad task ids error cleanly instead of raising a raw traceback.
    out = run(ws, "improve", "show", "bogus-id", expect_exit=True)
    check("improve show on an unknown id errors cleanly",
          "no improvement task matching" in out and "Traceback" not in out, out)
    out = run(ws, "improve", "run", "bogus-id", expect_exit=True)
    check("improve run on an unknown id errors cleanly",
          "no improvement task matching" in out and "Traceback" not in out, out)
    out = run(ws, "improve", "dismiss", "bogus-id", "--note", "n/a", expect_exit=True)
    check("improve dismiss on an unknown id errors cleanly",
          "no improvement task matching" in out and "Traceback" not in out, out)

    out = run(ws, "improve", "add", "--title", "CLI gap", "--evidence", "e",
             "--given", "g", "--observed", "o", "--expected", "x")
    check("improve add files a task", "Filed improvement task" in out, out)
    task_id = out.split("Filed improvement task ", 1)[1].split(":", 1)[0]
    out = run(ws, "improve", "list")
    check("improve list shows the filed task", task_id in out, out)
    out = run(ws, "improve", "show", task_id)
    check("improve show prints the task detail", "CLI gap" in out, out)


def scenario_shell_watch_obsidian(root: Path) -> None:
    print("Scenario F — Obsidian export, observation-ignore, shell, watcher")
    import subprocess
    import time as time_mod

    from authorlm.shell import Watcher, translate_line

    ws = root / "f"
    ms = ws / "manuscript"
    write(ms / "01-choice.md", CH1)
    write(ms / "02-fields.md", CH2)
    run(ws, "init", "--name", "book", "--path", str(ms))
    run(ws, "concept", "add", "Trajectory")
    run(ws, "concept", "add", "Field", "--notes", "structured openness")
    run(ws, "concept", "link", "Field", "permits", "Trajectory")
    run(ws, "collect")

    # --- Obsidian export ---
    out = run(ws, "export-obsidian")
    check("export reports notes and links",
          "Exported 2 concept note(s) with 1 link(s)" in out, out)
    field_note = (ms / "_concepts" / "Field.md").read_text()
    check("stub note has marker and wikilink",
          "authorlm: exported" in field_note and "permits [[Trajectory]]" in field_note,
          field_note)
    check("stub note carries notes and status",
          "structured openness" in field_note and "status: realized" in field_note,
          field_note)
    out = run(ws, "collect")
    check("_concepts is invisible to observation", "No changes" in out, out)
    run(ws, "concept", "retire", "Trajectory")
    out = run(ws, "export-obsidian")
    check("re-export drops retired concepts",
          not (ms / "_concepts" / "Trajectory.md").exists(), out)

    # Two names that sanitize to the same note filename must not silently
    # overwrite each other's exported note.
    run(ws, "concept", "add", "Choice/Freedom")
    run(ws, "concept", "add", "Choice:Freedom")
    out = run(ws, "export-obsidian")
    stub_files = sorted(p.name for p in (ms / "_concepts").glob("Choice-Freedom*.md"))
    check("colliding note names produce two distinct files",
          len(stub_files) == 2, stub_files)
    contents = {(ms / "_concepts" / f).read_text() for f in stub_files}
    check("both colliding notes keep their own concept name",
          any("# Choice/Freedom" in c for c in contents)
          and any("# Choice:Freedom" in c for c in contents),
          contents)

    # --- concept edit updates notes/kind ---
    out = run(ws, "concept", "edit", "Field", "--notes", "the horizon of possible moves")
    check("edit updates notes", "horizon of possible moves" in out, out)
    out = run(ws, "concept", "list")
    check("list shows updated notes", "horizon of possible moves" in out, out)
    out = run(ws, "concept", "edit", "Field", expect_exit=True)
    check("edit without changes errors cleanly", "usage: concept edit" in out, out)
    long_note = "a deliberately verbose definition " * 6  # > display shortening
    run(ws, "concept", "edit", "Field", "--notes", long_note.strip())
    out = run(ws, "concept", "show", "Field")
    check("show prints full untruncated notes", long_note.strip() in out, out)
    check("show prints kind/status and edges",
          "kind: concept" in out, out)

    # --- doc management: mechanical chapter operations ---
    out = run(ws, "doc", "add", "03 gravity", "--title", "Gravity")
    check("doc add scaffolds a chapter",
          (ms / "03-gravity.md").exists()
          and "# Gravity" in (ms / "03-gravity.md").read_text(), out)
    check("doc add records the observation", "file_added" in out, out)
    out = run(ws, "doc", "list")
    check("doc list shows chapters and their concepts",
          "01-choice.md" in out and "introduces:" in out, out)
    out = run(ws, "doc", "retire", "gravity")
    check("doc retire archives file and records removal",
          "file_removed" in out and (ms / "_retired" / "03-gravity.md").exists(), out)
    out = run(ws, "doc", "list")
    check("doc list shows retired documents",
          "Retired:" in out and "03-gravity.md" in out, out)
    out = run(ws, "doc", "revive", "gravity")
    check("doc revive restores the file",
          "file_added" in out and (ms / "03-gravity.md").exists(), out)
    out = run(ws, "doc", "add", "03 gravity", expect_exit=True)
    check("doc add refuses an existing name", "already exists" in out, out)

    # --- shell line translation ---
    check("review sugar translated",
          translate_line(["review", "2", "accept"]) == ["review", "2", "--accept"])
    check("non-review lines untouched",
          translate_line(["intent", "declare", "x"]) == ["intent", "declare", "x"])

    # --- watcher debounce logic ---
    watcher = Watcher(ms, debounce=0.0)
    check("quiet manuscript does not trigger", watcher.poll() is False)
    write(ms / "01-choice.md", CH1 + "\nA new closing thought.\n")
    check("edit marks dirty, no immediate trigger", watcher.poll() is False)
    time_mod.sleep(0.01)
    check("settled edit triggers exactly once", watcher.poll() is True)
    check("no re-trigger after collection", watcher.poll() is False)

    # --- scripted shell session (no watcher thread) ---
    result = subprocess.run(
        [sys.executable, "main.py", "--workspace", str(ws), "shell", "--no-watch"],
        input="help\nhelp guide\nhelp shell\nstatus\nreview 9 accept\nexit\n",
        capture_output=True, text=True, timeout=60,
        cwd=Path(__file__).resolve().parent.parent,
    )
    check("shell help lists commands with one-liners",
          "help <command>" in result.stdout
          and "generate editorial guidance" in result.stdout
          and "review N accept" in result.stdout,
          result.stdout)
    check("shell help <command> shows options",
          "usage: authorlm guide" in result.stdout, result.stdout)
    check("shell help hides shell-only commands",
          "unknown command 'shell'" in result.stdout, result.stdout)
    check("shell help hides internal commands",
          "_manuscripts" not in result.stdout, result.stdout)

    # --- shell tab completion candidates (readline-side) ---
    from authorlm.db import Database as _ShellDB
    from authorlm.shell import shell_candidates
    _sdb = _ShellDB(ws / ".authorlm" / "authorlm.db")
    _sms = dict(_sdb.one("SELECT * FROM manuscripts WHERE name = 'book'"))
    check("shell completion offers commands",
          "guide" in shell_candidates(_sdb, _sms, "") and
          "shell" not in shell_candidates(_sdb, _sms, ""))
    check("shell completion offers actions",
          "triage" in shell_candidates(_sdb, _sms, "concept "))
    check("shell completion offers option values",
          "historical_reference" in shell_candidates(_sdb, _sms, "concept confirm X --kind "))
    check("shell completion offers live concept names",
          "Field" in shell_candidates(_sdb, _sms, "concept retire "))
    check("shell completion offers live doc names",
          any(c.endswith(".md") for c in shell_candidates(_sdb, _sms, "doc retire ")))

    # --- bash completion generated from the parser ---
    import os
    script = run(ws, "completion")
    check("completion script registers the completer",
          "complete -o filenames -F _authorlm_complete authorlm" in script, script)
    comp = subprocess.run(
        ["bash", "-c",
         'eval "$SCRIPT"; COMP_WORDS=(authorlm concept tri); COMP_CWORD=2; '
         '_authorlm_complete; echo "${COMPREPLY[*]}"'],
        env={**os.environ, "SCRIPT": script},
        capture_output=True, text=True, timeout=30,
    )
    check("completion resolves subcommand actions",
          comp.stdout.strip() == "triage", comp.stdout + comp.stderr)
    comp = subprocess.run(
        ["bash", "-c",
         'eval "$SCRIPT"; COMP_WORDS=(authorlm concept confirm --kind hist); '
         'COMP_CWORD=4; _authorlm_complete; echo "${COMPREPLY[*]}"'],
        env={**os.environ, "SCRIPT": script},
        capture_output=True, text=True, timeout=30,
    )
    check("completion resolves option values",
          comp.stdout.strip() == "historical_reference", comp.stdout + comp.stderr)
    comp = subprocess.run(
        ["bash", "-c",
         'eval "$SCRIPT"; COMP_WORDS=(authorlm help st); COMP_CWORD=2; '
         '_authorlm_complete; echo "${COMPREPLY[*]}"'],
        env={**os.environ, "SCRIPT": script},
        capture_output=True, text=True, timeout=30,
    )
    check("completion resolves help topics",
          set(comp.stdout.split()) == {"status", "style"},
          comp.stdout + comp.stderr)
    comp = subprocess.run(
        ["bash", "-c",
         'eval "$SCRIPT"; COMP_WORDS=(authorlm init --path manuscr); COMP_CWORD=3; '
         '_authorlm_complete; echo "${COMPREPLY[*]}"'],
        env={**os.environ, "SCRIPT": script},
        capture_output=True, text=True, timeout=30, cwd=str(ws),
    )
    check("completion resolves directories for --path",
          comp.stdout.strip() == "manuscript", comp.stdout + comp.stderr)

    # --- global home database: without -w, data lives in $HOME/.authorlm ---
    fake_home = root / "fake-home"
    fake_home.mkdir()
    home_result = subprocess.run(
        [sys.executable, "main.py", "status"],
        env={**os.environ, "HOME": str(fake_home)},
        capture_output=True, text=True, timeout=60,
        cwd=Path(__file__).resolve().parent.parent,
    )
    check("default workspace is the home directory",
          "no manuscript registered" in home_result.stderr + home_result.stdout
          and (fake_home / ".authorlm" / "authorlm.db").exists(),
          home_result.stdout + home_result.stderr)

    # --- multiple manuscripts: tailored error; the shell asks instead ---
    ms2 = ws / "second"
    write(ms2 / "01.md", "# Two\n\nText.\n")
    run(ws, "init", "--name", "zeta", "--path", str(ms2))
    out = run(ws, "status", expect_exit=True)
    check("ambiguity error names the real command",
          "authorlm status -m" in out, out)
    picker_result = subprocess.run(
        [sys.executable, "main.py", "--workspace", str(ws), "shell", "--no-watch"],
        input="1\nstatus\nexit\n",
        capture_output=True, text=True, timeout=60,
        cwd=Path(__file__).resolve().parent.parent,
    )
    check("shell offers a manuscript picker",
          "Which manuscript?" in picker_result.stdout
          and "[1] book" in picker_result.stdout and "[2] zeta" in picker_result.stdout,
          picker_result.stdout)
    check("picker selection drives the session",
          "Manuscript: book" in picker_result.stdout, picker_result.stdout)

    # --- WAL mode active on the shared database ---
    from authorlm.db import Database as _WDB
    _wdb = _WDB(ws / ".authorlm" / "authorlm.db")
    check("SQLite runs in WAL mode",
          _wdb.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal")

    # --- deletion guard: auto-collect stages, manual collect proceeds ---
    write(ms / "04-pad.md", "# Pad\n\n" + ("substantial prose here. " * 40))
    run(ws, "-m", "book", "collect")
    write(ms / "04-pad.md", "# Pad\n\ntiny\n")
    out = run(ws, "-m", "book", "collect", "--auto")
    check("watcher-path collect stages massive deletions",
          "Large deletion detected in 04-pad.md" in out
          and "Snapshot NOT collected" in out, out)
    out = run(ws, "-m", "book", "history")
    versions_before = out.count("\n")
    out = run(ws, "-m", "book", "collect")
    check("manual collect confirms the deletion",
          "Collected revision" in out, out)

    # --- lazy idle expiry (threshold 0 → any active session expires) ---
    write(ws / ".authorlm" / "config.toml", "[session]\nidle_hours = 0\n")
    out = run(ws, "-m", "book", "session", "start")
    check("idle session closed retroactively before new one starts",
          "idle for" in out and "closed it retroactively" in out
          and "started" in out, out)
    (ws / ".authorlm" / "config.toml").unlink()
    run(ws, "-m", "book", "session", "end")
    check("shell starts a session with briefing",
          "Session" in result.stdout and "Learning Briefing" in result.stdout,
          result.stdout)
    check("shell catches up on collection at start",
          "Collected revision" in result.stdout or "No changes" in result.stdout,
          result.stdout)
    check("shell runs commands without prefix",
          "Manuscript: book" in result.stdout, result.stdout)
    check("shell survives a command error",
          "error:" in result.stdout, result.stdout)
    check("shell notes the still-active session on exit",
          "still active" in result.stdout, result.stdout)
    check("shell exits cleanly", result.returncode == 0, result.stderr)


def scenario_watcher_guard(root: Path) -> None:
    """OPS-3: watcher.poll() failure must be visible, not swallowed.

    Both watch loops (the shell's background thread and the standalone
    `authorlm watch` command) called `watcher.poll()` unguarded. An
    OSError out of the unguarded `rglob` in
    `revisions.iter_manuscript_paths` (e.g. a transient FS error on a
    synced/unmounted folder) silently killed the watcher while the
    "Watching…" banner kept claiming to observe — the author keeps
    writing, believing revisions are recorded, while nothing is. The fix
    catches the failure and prints a visible warning; it does not change
    what happens to the loop otherwise (both still stop, as they did
    before — the finding is about silence, not about stopping)."""
    print("Scenario J — a dying watcher reports itself instead of vanishing")
    import argparse
    import subprocess
    import textwrap
    import time as time_mod

    from authorlm import shell as _shell

    ws = root / "j"
    ms = ws / "manuscript"
    write(ms / "01-choice.md", CH1)
    run(ws, "init", "--name", "book", "--path", str(ms))

    class ExplodingWatcher:
        """Stands in for shell.Watcher — poll() always raises, exactly
        the shape an unguarded rglob failure takes."""

        def __init__(self, *_a, **_kw):
            pass

        def poll(self):
            raise OSError("simulated watcher failure")

    # --- run_watch: the standalone `authorlm watch` loop ---
    prev_watcher = _shell.Watcher
    _shell.Watcher = ExplodingWatcher
    try:
        watch_args = argparse.Namespace(workspace=str(ws), debounce=0.0, interval=0.01)
        buf = io.StringIO()
        exit_code = None
        try:
            with contextlib.redirect_stdout(buf):
                _shell.run_watch(watch_args, {"name": "book", "path": str(ms)})
        except SystemExit as err:
            exit_code = err.code
        output = buf.getvalue()
        check("run_watch exits cleanly (not an unhandled traceback) when "
              "watcher.poll() raises",
              exit_code == 1, output)
        check("run_watch prints a visible 'watcher stopped' warning instead "
              "of dying silently",
              "watcher stopped" in output and "simulated watcher failure" in output,
              output)
    finally:
        _shell.Watcher = prev_watcher

    # --- the interactive shell's background watch_loop thread ---
    pkg_root = Path(__file__).resolve().parent.parent
    driver = ws.parent / "watcher_guard_driver.py"
    driver.write_text(textwrap.dedent(f'''\
        import sys
        sys.path.insert(0, {str(pkg_root)!r})
        from authorlm import shell as _shell

        class ExplodingWatcher:
            def __init__(self, *_a, **_kw):
                pass
            def poll(self):
                raise OSError("simulated shell watcher failure")

        _shell.Watcher = ExplodingWatcher

        from authorlm.cli import main
        main(["--workspace", {str(ws)!r}, "--manuscript", "book", "shell"])
    '''))
    proc = subprocess.Popen(
        [sys.executable, str(driver)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, cwd=str(pkg_root),
    )
    try:
        # The shell's watch_loop polls on a fixed 1s cadence
        # (`stop.wait(1.0)`); give it margin to tick at least once and
        # hit the monkeypatched poll() before driving the REPL.
        time_mod.sleep(2.0)
        proc.stdin.write("status\n")
        proc.stdin.flush()
        proc.stdin.write("exit\n")
        proc.stdin.close()
        shell_output = proc.stdout.read()
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    check("the shell's background watcher thread reports its failure "
          "instead of dying silently behind the 'Watching…' banner",
          "watcher stopped: simulated shell watcher failure" in shell_output
          and "restart the shell to resume automatic collection" in shell_output,
          shell_output)
    check("the shell REPL itself survives a dead watcher thread and keeps "
          "answering commands afterward",
          "Manuscript: book" in shell_output and proc.returncode == 0,
          shell_output)


def scenario_style(root: Path) -> None:
    print("Scenario S — style guides: composition, inheritance, overrides")
    ws = root / "style"
    ms = ws / "manuscript"
    write(ms / "01-sermon.md", "# Sermon\n\nHarken unto the ground of things.\n")
    write(ms / "02-essay.md", "# Essay\n\nPlain modern words.\n")
    run(ws, "init", "--name", "book", "--path", str(ms))
    run(ws, "style", "guide", "House")
    run(ws, "style", "guide", "Sermons", "--parent", "House")
    run(ws, "style", "guide", "Essays", "--parent", "House")
    run(ws, "style", "attach", "01-sermon.md", "Sermons")
    run(ws, "style", "attach", "02-essay.md", "Essays")
    run(ws, "style", "add", "lexicon", "Terms of art are capitalized.",
        "--guide", "House")
    out = run(ws, "style", "add", "tone", "Challenging and unflinching.",
              "--guide", "House")
    tone_id = out.split("[")[1].split("]")[0]
    run(ws, "style", "add", "register", "Early Modern English, inviolable.",
        "--guide", "Sermons")
    run(ws, "style", "add", "register", "Contemporary English, expository.",
        "--guide", "Essays")
    run(ws, "style", "add", "tone", "Patient and guiding.",
        "--file", "02-essay.md", "--overrides", tone_id)
    out = run(ws, "style", "show", "01-sermon.md")
    check("sermon file inherits its guide plus the root",
          "Early Modern English" in out and "capitalized" in out
          and "Challenging" in out, out)
    check("sibling guide never bleeds across", "Contemporary" not in out, out)
    out = run(ws, "style", "show", "02-essay.md")
    check("file-local override displaces the inherited element",
          "Patient and guiding" in out and "Challenging" not in out, out)
    check("essay keeps root law and its own register",
          "capitalized" in out and "Contemporary" in out, out)
    out = run(ws, "style", "show", "03-unattached.md")
    check("unattached file falls back to the root guide",
          "capitalized" in out and "Early Modern" not in out, out)
    out = run(ws, "style", "add", "vibe", "Nope.", "--guide", "House",
              expect_exit=True)
    check("unknown aspect refused", "unknown aspect" in out, out)
    out = run(ws, "style", "attach", "02-esay.md", "Essays", expect_exit=True)
    check("attach validates filenames with a suggestion",
          "unknown file" in out and "02-essay.md" in out, out)
    out = run(ws, "style", "add", "tone", "Nope.", "--file", "02-esay.md",
              expect_exit=True)
    check("file-local elements validate filenames too",
          "unknown file" in out, out)
    out = run(ws, "style", "guides")
    check("overview lists guides and attachments",
          "Sermons ← House" in out and "02-essay.md → Essays" in out, out)
    run(ws, "style", "retire", tone_id)
    out = run(ws, "style", "show", "01-sermon.md")
    check("retired element leaves every composition",
          "Challenging" not in out, out)
    out = run(ws, "style", "retire", tone_id, expect_exit=True)
    check("retiring an already-retired element fails loudly",
          "no active style element matching" in out, out)
    out = run(ws, "style", "retire", "notaprefix", expect_exit=True)
    check("retiring an unknown prefix fails loudly",
          "no active style element matching" in out, out)

    # Aliases are reversible: --remove withdraws one without touching others.
    run(ws, "concept", "add", "Field of Choice")
    run(ws, "concept", "alias", "Field of Choice", "Chid", "Sanatana")
    out = run(ws, "concept", "alias", "Field of Choice", "--remove", "Chid")
    check("remove withdraws exactly the named alias",
          "aliases now: Sanatana" in out, out)
    out = run(ws, "concept", "show", "Chid", expect_exit=True)
    check("a removed alias no longer resolves", "matches" in out.lower()
          or "no concept" in out.lower(), out)
    out = run(ws, "concept", "alias", "Field of Choice", "--remove", "Chid",
              expect_exit=True)
    check("removing an absent alias fails loudly",
          "has no alias" in out, out)


def scenario_transplant() -> None:
    print("Scenario T — push-to-tab transplant emitter (captured Google JSON)")
    from authorlm.gdocs import transplant_requests

    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "tempdoc_import.json").read_text())
    reqs = transplant_requests(fixture, "t.target")

    inserts = [r["insertText"] for r in reqs if "insertText" in r]
    rebuilt = "".join(i["text"] for i in inserts)
    check("transplant rebuilds the full text in order",
          rebuilt.startswith("The Probe Sermon\n")
          and "Harken: qualities are the measurable shadows." in rebuilt
          and "He loveth Man" in rebuilt
          and "$x^2$" in rebuilt, rebuilt)
    check("every request targets the destination tab",
          all(("t.target" in json.dumps(r)) for r in reqs), str(reqs[:2]))

    # Index arithmetic: each insert lands exactly where the previous ended.
    cursor = 1
    ordered = True
    for i in inserts:
        ordered = ordered and i["location"]["index"] == cursor
        cursor += len(i["text"])
    check("insert cursor arithmetic is gapless", ordered, str(inserts))

    heading = [r for r in reqs if "updateParagraphStyle" in r]
    check("heading style carried over",
          any(r["updateParagraphStyle"]["paragraphStyle"]["namedStyleType"]
              == "HEADING_1" for r in heading), str(heading))
    para_styles = [r["updateParagraphStyle"]["paragraphStyle"]
                   ["namedStyleType"] for r in heading]
    check("every paragraph carries an explicit style — prose resets to "
          "NORMAL_TEXT (residual-style contagion, it-641d1aa4e3c0)",
          len(para_styles) == len(inserts)
          and para_styles.count("NORMAL_TEXT") >= 3, str(para_styles))
    styles = [r["updateTextStyle"] for r in reqs if "updateTextStyle" in r]
    styled_words = {
        rebuilt[s["range"]["startIndex"] - 1:s["range"]["endIndex"] - 1]:
        s["textStyle"] for s in styles}
    check("italic and bold ranges cover the right words",
          styled_words.get("qualities", {}).get("italic")
          and styled_words.get("measurable", {}).get("bold"), str(styled_words))
    # Inserted text inherits the tab's residual character style (the italic
    # snowball): every insertion must carry an explicit reset BEFORE the
    # true run styles are applied.
    resets = [s for s in styles
              if s["textStyle"] == {"bold": False, "italic": False,
                                    "underline": False}]
    check("every insertion is followed by an explicit style reset",
          len(resets) == len(inserts),
          f"{len(resets)} resets / {len(inserts)} inserts")
    first_style_idx = next(i for i, r in enumerate(reqs)
                           if "updateTextStyle" in r)
    check("reset precedes the first true run style",
          reqs[first_style_idx]["updateTextStyle"]["textStyle"]
          == {"bold": False, "italic": False, "underline": False},
          str(reqs[first_style_idx]))
    bullets = [r for r in reqs if "createParagraphBullets" in r]
    check("numbered list becomes numbered bullets",
          len(bullets) == 2 and all(
              b["createParagraphBullets"]["bulletPreset"]
              == "NUMBERED_DECIMAL_ALPHA_ROMAN" for b in bullets), str(bullets))

    # Horizontal rules import as text-less elements — they must survive as
    # literal --- paragraphs, not vanish.
    hr_doc = {"body": {"content": [
        {"paragraph": {"elements": [{"textRun": {"content": "Above.\n"}}]}},
        {"paragraph": {"elements": [{"horizontalRule": {}},
                                    {"textRun": {"content": "\n"}}]}},
        {"paragraph": {"elements": [{"textRun": {"content": "Below.\n"}}]}},
    ]}}
    hr_reqs = transplant_requests(hr_doc, "t.x")
    hr_text = "".join(r["insertText"]["text"] for r in hr_reqs
                      if "insertText" in r)
    check("horizontal rules survive transplant as --- paragraphs",
          hr_text == "Above.\n---\nBelow.\n", hr_text)

    # Tables (it-08b8b0a0c737): a Docs table element must be rebuilt with
    # insertTable and its cells filled last-to-first at the empty-table
    # indices; text after the table must land after the whole table.
    def _cell(text, **style):
        return {"content": [{"paragraph": {"elements": [
            {"textRun": {"content": text, "textStyle": style}}]}}]}
    tbl_doc = {"body": {"content": [
        {"paragraph": {"elements": [{"textRun": {"content": "Before.\n"}}]}},
        {"table": {"rows": 2, "columns": 2, "tableRows": [
            {"tableCells": [_cell("A\n", bold=True), _cell("B\n")]},
            {"tableCells": [_cell("c\n"), _cell("dd\n")]}]}},
        {"paragraph": {"elements": [{"textRun": {"content": "After.\n"}}]}},
    ]}}
    tbl_reqs = transplant_requests(tbl_doc, "t.x")
    tables = [r["insertTable"] for r in tbl_reqs if "insertTable" in r]
    check("a table element becomes one insertTable at the cursor",
          tables == [{"rows": 2, "columns": 2,
                      "location": {"tabId": "t.x", "index": 9}}], str(tables))
    tbl_inserts = [(r["insertText"]["location"]["index"], r["insertText"]["text"])
                   for r in tbl_reqs if "insertText" in r]
    # empty 2x2 table: newline(1) + table start/end(2) + 2 rows x (1 + 2 cells x 2) = 13
    check("cells fill last-to-first at empty-table indices, prose follows the table",
          tbl_inserts == [(1, "Before.\n"), (20, "dd"), (18, "c"), (15, "B"),
                          (13, "A"), (27, "After.\n")], str(tbl_inserts))
    bolds = [r["updateTextStyle"]["range"] for r in tbl_reqs
             if "updateTextStyle" in r
             and r["updateTextStyle"]["textStyle"].get("bold") is True]
    check("cell run styles are applied inside the cell",
          bolds == [{"tabId": "t.x", "startIndex": 13, "endIndex": 14}], str(bolds))

    # Tab reordering: iterated single moves must converge to TOC order
    # under remove-then-insert semantics, and no-op once matched.
    from authorlm.gdocs import next_tab_move

    def settle(current, desired, budget=20):
        current = list(current)
        while budget:
            move = next_tab_move(current, desired)
            if move is None:
                return current
            props = move["updateDocumentTabProperties"]["tabProperties"]
            current.remove(props["tabId"])
            current.insert(props["index"], props["tabId"])
            budget -= 1
        return current

    check("tab moves converge to the desired order",
          settle(["a", "b", "c", "d"], ["c", "a", "d", "b"])
          == ["c", "a", "d", "b"], "")
    check("tab moves converge on the live drift shape",
          settle(["p", "s", "r", "d", "ch", "i", "re", "m", "gc", "gl"],
                 ["p", "s", "r", "d", "gc", "ch", "i", "re", "gl", "m"])
          == ["p", "s", "r", "d", "gc", "ch", "i", "re", "gl", "m"], "")
    check("reorder is a no-op when order already matches",
          next_tab_move(["a", "b", "c"], ["a", "b", "c"]) is None, "")
    from authorlm.gdocs import manifest_text
    manifest = manifest_text("SMSTTD", 2)
    check("manifest names the manuscript and doc version",
          "Manuscript: SMSTTD" in manifest and "Doc version: 2" in manifest,
          manifest)
    check("unknown desired ids are ignored",
          settle(["a", "b"], ["b", "x", "a"]) == ["b", "a"], "")

    # The pull side: splitting a whole-master export on tab-title headings.
    from authorlm.gdocs import split_tabbed_export
    export = "\n".join([
        "# **Tab 1**", "",
        "# **preface.md**", "", "Intro prose.", "",
        "# **sermons.md**", "",
        "# **The First Sermon**", "", "Harken.", "",
        "# **discernment.md**", "", "Plain words.",
    ])
    parts = split_tabbed_export(
        export, {"preface.md", "sermons.md", "discernment.md"})
    check("split keys every known tab",
          set(parts) == {"preface.md", "sermons.md", "discernment.md"},
          str(parts))
    check("content H1 in export style is never a boundary",
          "# **The First Sermon**" in parts["sermons.md"]
          and "Harken." in parts["sermons.md"], str(parts))
    check("unknown leading tab is ignored, sections stay clean",
          parts["preface.md"].strip() == "Intro prose."
          and parts["discernment.md"].strip() == "Plain words.", str(parts))


def scenario_ephemeral_history() -> None:
    print("Scenario H — triage input stays out of readline history")
    try:
        import readline
    except ImportError:
        print("  (readline unavailable — skipped)")
        return
    from authorlm.cli import _ephemeral_history
    base = readline.get_current_history_length()
    with _ephemeral_history():
        readline.add_history("k")
        readline.add_history("r")
    check("triage-scoped input leaves no readline history",
          readline.get_current_history_length() == base,
          f"history length {readline.get_current_history_length()} != {base}")


# --- Scenario WS fixtures (scope-derived intent attachment) ---------------
#
# A real toc PARENT CHAIN, which no other e2e workspace has: book.md is a
# grandparent opener, part.md its child opener, alpha.md and beta.md the
# essays beneath. That is what makes `chapter` a tier with two levels in
# it rather than a synonym for "the one parent".

TOC_WS = ('[[chapter]]\nfile = "book.md"\n\n'
          '[[chapter]]\nfile = "part.md"\nparent = "book.md"\n\n'
          '[[chapter]]\nfile = "alpha.md"\nparent = "part.md"\n\n'
          '[[chapter]]\nfile = "beta.md"\nparent = "part.md"\n\n'
          '[[chapter]]\nfile = "gamma.md"\nparent = "part.md"\n\n'
          '[[chapter]]\nfile = "orphan.md"\n')

WS_BOOK = "# The Book\n\nThe book opener, which sits above every part.\n"
WS_PART = "# The Part\n\nThe part opener, which sits above its essays.\n"
WS_ALPHA = ("# Alpha\n\nThe old alpha opening, soon to be raw material.\n\n"
            "The old alpha second paragraph, about fields.\n")
WS_BETA = ("# Beta\n\nThe old beta opening, soon to be raw material.\n\n"
           "The old beta second paragraph, about trajectories.\n")
WS_GAMMA = ("# Gamma\n\nThe old gamma opening, soon to be raw material.\n\n"
            "The old gamma second paragraph, about ties.\n")
WS_ORPHAN = "# Orphan\n\nAn essay no part claims and no intent covers.\n"

WS_FILES = {"book.md": WS_BOOK, "part.md": WS_PART, "alpha.md": WS_ALPHA,
            "beta.md": WS_BETA, "gamma.md": WS_GAMMA, "orphan.md": WS_ORPHAN}

WS_PLAN = json.dumps([{"role": "opener", "concepts": ["Choice"],
                       "budget": 60, "notes": "open on the claim"}])
WS_PLAN_2 = json.dumps([{"role": "opener", "concepts": ["Choice"],
                         "budget": 60, "notes": "open on the claim"},
                        {"role": "close", "concepts": ["Choice"],
                         "budget": 60, "notes": "close on the same ground"}])


def _ws_workspace(root: Path, server, name: str = "ws") -> tuple:
    """The Scenario WS fixture: the parent chain above, a style guide on
    every essay, and fresh summaries. Returns (ws, ms, db, manuscript)."""
    from authorlm.db import Database as _DB

    ws = root / name
    ms = ws / "manuscript"
    for filename, body in WS_FILES.items():
        write(ms / filename, body)
    write(ms / "toc.toml", TOC_WS)
    write(ws / ".authorlm" / "config.toml",
          "[llm]\nenabled = true\nprovider = \"openai\"\n"
          f"base_url = \"http://127.0.0.1:{server.server_port}/v1\"\n"
          "model = \"stub\"\n")
    run(ws, "init", "--name", "book", "--path", str(ms))
    run(ws, "style", "guide", "House")
    for filename in WS_FILES:
        run(ws, "style", "attach", filename, "House")
    run(ws, "summarize", "rebuild", "--all")
    db = _DB(ws / ".authorlm" / "authorlm.db")
    manuscript = dict(db.one("SELECT * FROM manuscripts WHERE name = 'book'"))
    return ws, ms, db, manuscript


def _full_id(db, table: str, prefix: str) -> str:
    """The CLI prints id PREFIXES; the database is keyed on full ids."""
    row = db.one(f"SELECT id FROM {table} WHERE id LIKE ?", (f"{prefix}%",))
    assert row is not None, f"no {table} row for '{prefix}'"
    return row["id"]


def _episode_of(db, intent_id: str) -> dict:
    row = db.one("SELECT * FROM editorial_episodes WHERE intent_id LIKE ? "
                 "ORDER BY created_at DESC LIMIT 1", (f"{intent_id}%",))
    return dict(row) if row else {}


def _episode_locations(db, episode: dict) -> list[str]:
    ids = json.loads(episode.get("transition_ids") or "[]")
    out = []
    for tid in ids:
        row = db.one("SELECT location FROM editorial_transitions WHERE id = ?",
                     (tid,))
        if row:
            out.append(row["location"].split("#", 1)[0])
    return out


def _review_episodes(db, writeup_id: str) -> set:
    """The episodes this writeup's beat verdicts were recorded against.
    `beliefs.record_review` carries the episode on the evidence row it
    writes, and that row names the suggestion text — which is how a
    verdict is tied back to the beat it settled."""
    texts = {r["suggestion"][:120] for r in db.all(
        "SELECT suggestion FROM guidance_history WHERE batch_id LIKE ?",
        (f"{writeup_id}%",))}
    return {row["episode_id"] for row in db.all(
        "SELECT episode_id, target FROM evidence "
        "WHERE evidence_type = 'author_review'")
        if row["target"] in texts}


def scenario_writeup_scope(root: Path) -> None:
    """Scenario WS — scope-derived intent attachment (design
    findings/design-intent-scope.md)."""
    print("Scenario WS — writeup scope: derived intents and episode attribution")
    server = http.server.HTTPServer(("127.0.0.1", 0), StubLLMHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ws, ms, db, manuscript = _ws_workspace(root, server)
        run(ws, "session", "start")

        # ---- WS-11: episode single-attribution, the parallel case.
        # Two writeups open at once (design §14) under two DIFFERENT
        # intents. Before this design `collect` attached every transition
        # to `sessions.current_episode` — the session's most recently
        # CREATED open episode, whatever intent it belonged to — so both
        # writeups' beats landed on whichever intent was declared last.
        out = run(ws, "intent", "declare", "Rewrite alpha")
        intent_alpha = out.split("[")[1].split("]")[0]
        out = run(ws, "intent", "declare", "Rewrite beta")
        intent_beta = out.split("[")[1].split("]")[0]

        out = run_stdin(ws, "", "write", "start", "alpha.md",
                        "--intent", intent_alpha)
        wu_alpha = out.split("[")[1].split("]")[0]
        out = run_stdin(ws, "", "write", "start", "beta.md",
                        "--intent", intent_beta)
        wu_beta = out.split("[")[1].split("]")[0]
        run_stdin(ws, WS_PLAN_2, "write", "plan", "--writeup", "alpha.md")
        run_stdin(ws, WS_PLAN, "write", "plan", "--writeup", "beta.md")

        run_stdin(ws, "Alpha beat one: the claim, stated plainly.",
                  "write", "propose", "--writeup", "alpha.md",
                  "--why", "opens on the claim")
        run_stdin(ws, "", "write", "accept", "--writeup", "alpha.md")
        run_stdin(ws, "Beta beat one: the same ground, differently held.",
                  "write", "propose", "--writeup", "beta.md",
                  "--why", "opens on the claim")
        run_stdin(ws, "", "write", "accept", "--writeup", "beta.md")

        ep_alpha = _episode_of(db, intent_alpha)
        ep_beta = _episode_of(db, intent_beta)
        check("WS-11 — the alpha writeup's accepted beat attaches to "
              "ALPHA's own episode, not to whichever intent was declared "
              "last (design §0.3: the live mis-attribution)",
              "alpha.md" in _episode_locations(db, ep_alpha),
              f"alpha episode {ep_alpha.get('id')} holds "
              f"{_episode_locations(db, ep_alpha)}")
        check("WS-11 — and the beta writeup's beat attaches to BETA's "
              "episode",
              "beta.md" in _episode_locations(db, ep_beta),
              f"beta episode {ep_beta.get('id')} holds "
              f"{_episode_locations(db, ep_beta)}")
        check("WS-11 — no fan-out: alpha's episode holds NOTHING from "
              "beta.md",
              "beta.md" not in _episode_locations(db, ep_alpha),
              str(_episode_locations(db, ep_alpha)))
        check("WS-11 — the beat VERDICT is recorded against the same "
              "episode as the transitions (write_accept passes one row to "
              "both collect and record_review)",
              _review_episodes(db, wu_alpha) == {ep_alpha.get("id")},
              f"{_review_episodes(db, wu_alpha)} != {ep_alpha.get('id')}")
        check("WS-11 — and beta's verdict likewise",
              _review_episodes(db, wu_beta) == {ep_beta.get("id")},
              f"{_review_episodes(db, wu_beta)} != {ep_beta.get('id')}")
        # ---- WS-11r: `write reject` is its own site, and is covered as
        # one. It takes a different path from `write_accept` — no collect,
        # one `record_review` — so a routing fix applied to accept alone
        # would leave every rejection filed against whichever intent was
        # declared last, and rejections are the highest-value evidence the
        # loop receives.
        before_beta = _episode_of(db, intent_beta)["transition_ids"]
        before_verdicts = len(_episodes_referenced(
            db, {ep_alpha["id"], ep_beta["id"]}))
        run_stdin(ws, "Alpha beat two: a flat sentence the author turns down.",
                  "write", "propose", "--writeup", "alpha.md",
                  "--why", "closes on the same ground")
        run_stdin(ws, "", "write", "reject", "--writeup", "alpha.md",
                  "--reason", "too flat, and it restates the opener")
        check("WS-11r — a REJECTED beat on alpha is recorded against "
              "ALPHA's episode, while beta's is the session's newest",
              _review_episodes(db, wu_alpha) == {ep_alpha.get("id")},
              f"{_review_episodes(db, wu_alpha)} != {ep_alpha.get('id')}")
        check("WS-11r — the rejection really was recorded (the assertion "
              "above is not passing on an absence)",
              len(_episodes_referenced(db, {ep_alpha["id"], ep_beta["id"]}))
              > before_verdicts,
              f"{before_verdicts} verdict rows before")
        check("WS-11r — and beta's episode is untouched by alpha's "
              "rejection",
              _episode_of(db, intent_beta)["transition_ids"] == before_beta,
              _episode_of(db, intent_beta)["transition_ids"])
        run_stdin(ws, "Alpha beat two: the closing turn, in the author's "
                      "own rhythm.",
                  "write", "propose", "--writeup", "alpha.md",
                  "--why", "closes on the same ground")
        run_stdin(ws, "", "write", "accept", "--writeup", "alpha.md")
        ep_alpha = _episode_of(db, intent_alpha)
        ep_beta = _episode_of(db, intent_beta)
        del wu_beta

        # ---- WS-11b: the writeup's LAST collect belongs to the primary
        # too. Between the final accepted beat and `write complete` the
        # file can genuinely move — a doc pull, or the author's own hand
        # edit — and `write complete`'s own collect is what records it.
        # "Usually unchanged" is not never, and when it IS changed those
        # transitions went the same wrong way the per-beat ones did.
        before_alpha = set(json.loads(ep_alpha["transition_ids"]))
        before_beta = set(json.loads(ep_beta["transition_ids"]))
        (ms / "alpha.md").write_text(
            (ms / "alpha.md").read_text()
            + "\nA paragraph the author typed by hand, after the last beat "
              "and before completing.\n")
        run(ws, "write", "complete", "--writeup", "alpha.md")
        ep_alpha = _episode_of(db, intent_alpha)
        ep_beta = _episode_of(db, intent_beta)
        gained_alpha = set(json.loads(ep_alpha["transition_ids"])) - before_alpha
        gained_beta = set(json.loads(ep_beta["transition_ids"])) - before_beta
        check("WS-11b — a hand edit between the last beat and completion is "
              "collected onto the PRIMARY's episode, not onto whichever "
              "intent was declared last",
              gained_alpha and all(
                  f == "alpha.md" for f in _episode_locations(
                      db, {"transition_ids": json.dumps(list(gained_alpha))})),
              str(_episode_locations(
                  db, {"transition_ids": json.dumps(list(gained_alpha))})))
        check("WS-11b — and beta's episode gains nothing from alpha's "
              "completion: the final collect fans out no further than the "
              "per-beat ones do",
              not gained_beta,
              str(_episode_locations(
                  db, {"transition_ids": json.dumps(list(gained_beta))})))
    finally:
        server.shutdown()


def _episodes_referenced(db, episode_ids: set) -> list:
    """Every evidence row pointing at one of these episodes. The reviews
    themselves carry no episode column — `beliefs.record_review` puts it
    on the evidence row it writes — so this is where a verdict filed
    against the wrong intent becomes visible."""
    return [dict(r) for r in db.all(
        "SELECT id, episode_id, evidence_type, target FROM evidence "
        "WHERE episode_id IS NOT NULL")
        if r["episode_id"] in episode_ids]


def _newest_open_episode(db, manuscript_id: str) -> dict:
    """What `sessions.current_episode` would return: the session's most
    recently created open episode, whatever intent it belongs to."""
    row = db.one(
        "SELECT e.* FROM editorial_episodes e JOIN sessions s "
        "ON s.id = e.session_id WHERE e.manuscript_id = ? "
        "AND e.status = 'open' AND s.status = 'active' "
        "ORDER BY e.created_at DESC LIMIT 1", (manuscript_id,))
    return dict(row) if row else {}


def scenario_intent_no_fanout(root: Path) -> None:
    """Scenario WS4 — design-intent-scope §5.1 item 11, as specified: a
    DERIVED writeup with a primary and true secondaries, where fan-out is
    the failure case and is asserted as an explicit ABSENCE."""
    print("Scenario WS4 — no fan-out: the secondaries' episodes stay empty")
    server = http.server.HTTPServer(("127.0.0.1", 0), StubLLMHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ws, ms, db, manuscript = _ws_workspace(root, server, name="ws4")
        run(ws, "session", "start")
        # The book-wide one is declared LAST, so ITS episode is the
        # session's most recently created open one — which is precisely
        # what `current_episode` would hand every collect on this writeup.
        i_file = _declare(ws, "Rewrite alpha itself", "--scope", "alpha.md")
        i_part = _declare(ws, "Tighten every essay in the part",
                          "--chapter", "part.md")
        i_wide = _declare(ws, "A standing rule for the whole book")

        out = run_stdin(ws, "", "write", "start", "alpha.md")
        wu = out.split("[")[1].split("]")[0]
        ep_file = _episode_of(db, i_file)
        ep_part = _episode_of(db, i_part)
        ep_wide = _episode_of(db, i_wide)
        check("WS4 — the file-scoped intent is the primary; the other two "
              "are true secondaries",
              _block_of(db, wu)["primary"].startswith(i_file)
              and {i[:8] for i in _member_ids(_block_of(db, wu))}
              == {i_file, i_part, i_wide}, str(_block_of(db, wu)))
        check("WS4 — the writeup's OWN TRUNCATION is filed against the "
              "primary's episode. It is the writeup's first recorded act, "
              "and before this it went wherever current_episode pointed — "
              "which here is the book-wide secondary declared last",
              "alpha.md" in _episode_locations(db, ep_file),
              f"primary episode holds {_episode_locations(db, ep_file)}; "
              f"the book-wide secondary holds "
              f"{_episode_locations(db, ep_wide)}")

        plan = json.dumps([{"role": "opener", "concepts": [], "budget": 60},
                           {"role": "close", "concepts": [], "budget": 60}])
        run_stdin(ws, plan, "write", "plan", "--writeup", "alpha.md")
        for n in (1, 2):
            run_stdin(ws, f"Alpha beat {n}, a sentence of the author's prose.",
                      "write", "propose", "--writeup", "alpha.md",
                      "--why", "the beat")
            run_stdin(ws, "", "write", "accept", "--writeup", "alpha.md")

        ep_file = _episode_of(db, i_file)
        ep_part = _episode_of(db, i_part)
        ep_wide = _episode_of(db, i_wide)
        check("WS4 — the primary's episode holds every transition of the "
              "writeup",
              len(json.loads(ep_file["transition_ids"])) >= 3,
              str(_episode_locations(db, ep_file)))
        for label, episode in (("chapter", ep_part), ("book-wide", ep_wide)):
            check(f"WS4 — the {label} SECONDARY's episode transition_ids is "
                  f"EXACTLY [] — one authorial act is never mined twice, "
                  f"and the absence is what says so",
                  json.loads(episode["transition_ids"]) == [],
                  f"{episode['id']} holds "
                  f"{_episode_locations(db, episode)}")
        secondaries = {ep_part["id"], ep_wide["id"]}
        check("WS4 — and no review reaches a secondary either: not one "
              "evidence row references either secondary episode",
              _episodes_referenced(db, secondaries) == [],
              str(_episodes_referenced(db, secondaries)))
        check("WS4 — every verdict of this writeup is filed against the "
              "primary's episode and nowhere else",
              _review_episodes(db, wu) == {ep_file["id"]},
              f"{_review_episodes(db, wu)} != {ep_file['id']}")
        run(ws, "write", "complete", "--writeup", "alpha.md")

        # ---- The tie: the machine must NOT guess an episode either.
        run(ws, "summarize", "rebuild")
        tie_one = _declare(ws, "Rewrite gamma around the refrain",
                           "--scope", "gamma.md")
        tie_two = _declare(ws, "Cut gamma's inheritance to a paragraph",
                           "--scope", "gamma.md")
        # Declared LAST and NOT a member of gamma's set, so it owns the
        # session episode: that is what "ambient" looks like from outside.
        bystander = _declare(ws, "Work on beta later", "--scope", "beta.md")
        ambient = _newest_open_episode(db, manuscript["id"])
        check("WS4 — the bystander's episode is the session's newest, so it "
              "is what current_episode would hand the truncation",
              ambient["intent_id"].startswith(bystander), str(ambient))

        run_stdin(ws, "", "write", "start", "gamma.md")
        ep_one = _episode_of(db, tie_one)
        ep_two = _episode_of(db, tie_two)
        check("WS4 — with the primary UNSETTLED the truncation stays "
              "AMBIENT: real work is never attributed to a placeholder "
              "candidate the author has not chosen",
              "gamma.md" in _episode_locations(
                  db, _episode_of(db, bystander)),
              str(_episode_locations(db, _episode_of(db, bystander))))
        for label, episode in (("first", ep_one), ("second", ep_two)):
            check(f"WS4 — and the {label} tied candidate's episode holds "
                  f"nothing: refusing to guess means refusing to file",
                  json.loads(episode["transition_ids"]) == [],
                  f"{episode['id']} holds "
                  f"{_episode_locations(db, episode)}")
        # ---- The drift line is worded for the state the set is in. This
        # writeup is still PROPOSED — nothing has been ratified — so
        # "newly in scope since ratification" would name a moment that has
        # not happened, and would imply a freeze the author can still edit
        # their way out of.
        late = _declare(ws, "A third goal for gamma", "--scope", "gamma.md")
        out = run(ws, "write", "status", "--writeup", "gamma.md")
        check("WS4 — while PROPOSED the drift line says 'in scope, but not "
              "on this writeup' and never claims a ratification",
              "in scope, but not on this writeup" in out
              and late in out
              and "since ratification" not in out, out)
        check("WS4 — and it does not offer the cache-rebill warning, which "
              "is only true of a join after the freeze",
              "re-bills the cached prefix" not in out, out)
        run(ws, "write", "abandon", "--writeup", "gamma.md")
    finally:
        server.shutdown()


def _block_of(db, writeup_prefix: str) -> dict:
    row = db.one("SELECT metadata FROM writeups WHERE id LIKE ?",
                 (f"{writeup_prefix}%",))
    return json.loads(row["metadata"] or "{}").get("intents") or {}


def _member_ids(block: dict) -> set:
    return {m["id"] for m in block.get("members") or []}


def _tier_of(block: dict, intent_id: str) -> str | None:
    for member in block.get("members") or []:
        if member["id"].startswith(intent_id):
            return member["tier"]
    return None


def _declare(ws, statement: str, *flags) -> str:
    out = run(ws, "intent", "declare", statement, *flags)
    return out.split("[")[1].split("]")[0]


def scenario_intent_scope(root: Path) -> None:
    """Scenario WS2 — derive → ratify → freeze, and the completion
    dispositions (design-intent-scope §5.1)."""
    print("Scenario WS2 — scope-derived intent sets: derive, ratify, freeze")
    server = http.server.HTTPServer(("127.0.0.1", 0), StubLLMHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ws, ms, db, manuscript = _ws_workspace(root, server, name="ws2")
        run(ws, "session", "start")

        # ---- WS-3: an essay no active intent covers is a writeup with no
        # goal. It refuses, names BOTH exits, and — RISK K1 — leaves the
        # disk exactly as it was.
        before = (ms / "orphan.md").read_bytes()
        out = run_stdin(ws, "", "write", "start", "orphan.md",
                        expect_exit=True)
        check("WS-3 — an empty derived set REFUSES rather than starting a "
              "writeup with no goal",
              "no active intent covers orphan.md" in out, out)
        check("WS-3 — and the refusal names both exits: declare one scoped "
              "to this file, or place an existing one",
              "intent declare" in out and "--scope orphan.md" in out
              and "intent scope" in out, out)
        check("WS-3 — never auto-declared",
              "declared intent" not in out.lower(), out)
        check("WS-3 — the refused start left the file byte-identical and "
              "wrote no writeup row (the gate order of RISK K1 holds)",
              (ms / "orphan.md").read_bytes() == before
              and db.one("SELECT id FROM writeups WHERE file = 'orphan.md'")
              is None, out)

        i_file = _declare(ws, "Rewrite alpha around the door refrain",
                          "--scope", "alpha.md")
        i_part = _declare(ws, "Tighten every essay in the part",
                          "--chapter", "part.md")
        i_book = _declare(ws, "Cut the throat-clearing everywhere in the book",
                          "--chapter", "book.md")
        i_wide = _declare(ws, "Never let a thinker's name carry an argument")
        i_beta = _declare(ws, "Beta-only work", "--scope", "beta.md")

        out = run(ws, "intent", "list")
        check("WS — intent list prints the TIER, not just the column",
              f"({'active'} · file alpha.md)" in out
              and "· chapter part.md)" in out
              and "· book-wide (default))" in out, out)

        # ---- WS-1 / WS-2: derivation by tier, including a GRANDPARENT
        # opener, which is what makes `chapter` a tier with depth.
        out = run_stdin(ws, "", "write", "start", "alpha.md")
        wu = out.split("[")[1].split("]")[0]
        block = _block_of(db, wu)
        derived = {i[:8] for i in _member_ids(block)}
        check("WS-1 — with no --intent the set is DERIVED: every active "
              "intent whose scope covers this essay, and only those",
              derived == {i_file, i_part, i_book, i_wide},
              f"{sorted(derived)} (beta-scoped {i_beta} must be absent)")
        check("WS-1 — the tiers are named: file, chapter, manuscript",
              (_tier_of(block, i_file), _tier_of(block, i_part),
               _tier_of(block, i_wide)) == ("file", "chapter", "manuscript"),
              json.dumps(block["members"], indent=1))
        check("WS-2 — an intent scoped to a GRANDPARENT opener is derived "
              "too, and its tier is chapter, not manuscript",
              _tier_of(block, i_book) == "chapter", str(block["members"]))
        check("WS-1 — the most specific tier is primary",
              block["primary"].startswith(i_file), block["primary"])
        check("WS-1 — and the start output says so, grouped by tier",
              "Intents in scope" in out and "← primary" in out
              and "manuscript " in out, out)
        check("WS-6 — the set is PROPOSED at start; nothing is ratified yet",
              block["state"] == "proposed" and block["frozen_at"] is None,
              str(block))

        # ---- WS-9: the proposed window is the editable one.
        run(ws, "write", "intents", "--primary", i_part, "--writeup", "alpha.md")
        check("WS-9 — --primary re-points freely while the set is proposed",
              _block_of(db, wu)["primary"].startswith(i_part), "")
        run(ws, "write", "intents", "--primary", i_file, "--writeup", "alpha.md")
        out = run(ws, "write", "intents", "--remove", i_book,
                  "--writeup", "alpha.md")
        check("WS-9 — --remove succeeds while proposed",
              i_book not in {i[:8] for i in _member_ids(_block_of(db, wu))},
              out)

        # ---- WS-6: the plan is the ratification gate; the set freezes on it.
        plan = json.dumps([{"role": "opener", "concepts": ["Choice"],
                            "budget": 60, "intents": [i_file]}])
        out = run_stdin(ws, plan, "write", "plan", "--writeup", "alpha.md")
        block = _block_of(db, wu)
        frozen_at = block["frozen_at"]
        check("WS-6 — 'Plan ratified' is visibly a ratification of plan AND "
              "intents: the frozen set prints above the beats",
              "Intents — 3, FROZEN" in out, out)
        check("WS-6 — the stored state is frozen and stamped",
              block["state"] == "frozen" and frozen_at, str(block))
        check("WS-8 — a beat tag is stored RESOLVED to the full member id "
              "(the digest's `serves` precedent)",
              json.loads(db.one("SELECT plan FROM writeups WHERE id LIKE ?",
                                (f"{wu}%",))["plan"])[0]["intents"][0]
              .startswith(i_file), "")

        replan = json.dumps([
            {"role": "opener", "concepts": ["Choice"], "budget": 60,
             "intents": [i_file]},
            {"role": "close", "concepts": ["Choice"], "budget": 60,
             "intents": [i_file]}])
        run_stdin(ws, replan, "write", "plan", "--replace",
                  "--writeup", "alpha.md")
        after = _block_of(db, wu)
        check("WS-6 — a --replace replan replans BEATS only: membership and "
              "frozen_at are untouched",
              after["frozen_at"] == frozen_at
              and _member_ids(after) == _member_ids(block), str(after))

        # ---- WS-9: the frozen window is narrower, and each refusal names
        # the one command that clears it.
        out = run(ws, "write", "intents", "--remove", i_part,
                  "--writeup", "alpha.md", expect_exit=True)
        check("WS-9 — --remove is REFUSED after the freeze, because a "
              "ratified set that quietly loses a member makes the "
              "completion report a fiction — and it names --defer",
              "ratified" in out and "--defer" in out, out)
        run(ws, "write", "intents", "--primary", i_part,
            "--writeup", "alpha.md")
        check("WS-9 — --primary is still legal after the freeze while "
              "cursor == 0: nothing has been attributed yet",
              _block_of(db, wu)["primary"].startswith(i_part), "")
        run(ws, "write", "intents", "--primary", i_file,
            "--writeup", "alpha.md")

        run_stdin(ws, "Alpha's first beat, plainly said.", "write", "propose",
                  "--writeup", "alpha.md", "--why", "opens on the claim")
        run_stdin(ws, "", "write", "accept", "--writeup", "alpha.md")
        out = run(ws, "write", "intents", "--primary", i_part,
                  "--writeup", "alpha.md", expect_exit=True)
        check("WS-9 — and it is REFUSED once a beat is accepted: "
              "re-pointing would split one writeup's transitions across two "
              "episodes, which is the fan-out the design forbids",
              "already accepted" in out and "write abandon" in out, out)

        # ---- WS-10: newly in scope is flagged, NEVER joined.
        i_join = _declare(ws, "Make the debt explicit in alpha",
                          "--scope", "alpha.md")
        out = run(ws, "write", "status", "--writeup", "alpha.md")
        check("WS-10 — an intent declared mid-writeup is reported as newly "
              "in scope, with both exits named",
              "newly in scope since ratification" in out
              and "--add" in out and "--ignore" in out, out)
        check("WS-10 — and it has NOT joined: the member list is unchanged",
              i_join not in {i[:8] for i in _member_ids(_block_of(db, wu))},
              str(_block_of(db, wu)))
        out = run(ws, "write", "intents", "--add", i_join,
                  "--writeup", "alpha.md")
        joined = _block_of(db, wu)
        check("WS-9 — --add stays legal after the freeze as an explicit "
              "JOIN, recorded with the beat it happened at",
              any(a["id"].startswith(i_join) and a.get("beat") == 1
                  for a in joined["adds"]), str(joined["adds"]))
        check("WS-9 — and the CLI says plainly that block A changed, so the "
              "next beat re-bills the cached prefix once",
              "re-bills the cached prefix" in out, out)

        i_ignore = _declare(ws, "A goal for alpha the author will not take up",
                            "--scope", "alpha.md")
        run(ws, "write", "intents", "--ignore", i_ignore,
            "--writeup", "alpha.md")
        out = run(ws, "write", "status", "--writeup", "alpha.md")
        check("WS-10 — --ignore stops the flag recurring without joining "
              "or refusing anything",
              i_ignore not in out
              and i_ignore not in {i[:8] for i in
                                   _member_ids(_block_of(db, wu))}, out)

        # A RE-SCOPED existing intent behaves identically to a new one.
        run(ws, "intent", "scope", i_beta, "--scope", "alpha.md")
        out = run(ws, "write", "status", "--writeup", "alpha.md")
        check("WS-10 — an intent RE-SCOPED onto this essay lands in the "
              "same flag, and still does not join",
              i_beta in out and "newly in scope" in out
              and i_beta not in {i[:8] for i in
                                 _member_ids(_block_of(db, wu))}, out)
        run(ws, "write", "intents", "--ignore", i_beta, "--writeup", "alpha.md")

        # ---- WS-14: --defer's two refusals.
        primary_id = _block_of(db, wu)["primary"]
        out = run(ws, "write", "intents", "--defer", primary_id[:8],
                  "--reason", "not this time", "--writeup", "alpha.md",
                  expect_exit=True)
        check("WS-14 — --defer REFUSES the primary: it owns the attribution "
              "for every beat already accepted",
              "is the primary" in out, out)
        out = run(ws, "write", "intents", "--defer", i_part,
                  "--writeup", "alpha.md", expect_exit=True)
        check("WS-14 — --defer requires a reason, on the same ground as "
              "'write reject --reason': an unexplained deferral teaches "
              "nothing",
              "--reason is required" in out, out)
        run(ws, "write", "intents", "--defer", i_part, "--reason",
            "this essay barely names anyone; nothing to do here",
            "--writeup", "alpha.md")

        run_stdin(ws, "Alpha's closing beat, plainly said.", "write",
                  "propose", "--writeup", "alpha.md", "--why", "closes")
        run_stdin(ws, "", "write", "accept", "--writeup", "alpha.md")

        # ---- WS-12 / WS-13: dispositions, the warning, the cross-reference.
        out = run(ws, "write", "complete", "--writeup", "alpha.md")
        check("WS-12 — a member every accepted beat is tagged for is SERVED",
              f"served    [{i_file}" in out, out)
        check("WS-12 — a deferred member reports its reason VERBATIM",
              "DEFERRED" in out
              and "this essay barely names anyone" in out, out)
        check("WS-12 — a member no accepted beat serves is UNSERVED, named, "
              "and given both exits",
              "UNSERVED" in out and f"[{i_wide}" in out
              and "--defer" in out and "intent complete" in out, out)
        check("WS-12 — and the completion SUCCEEDS anyway (§15.13: warn, "
              "never block)",
              "completed — 2 beat(s)" in out, out)
        for intent_id, expected in ((i_file, "served"), (i_part, "deferred"),
                                    (i_wide, "unserved"), (i_join, "unserved")):
            row = db.one("SELECT metadata FROM declared_intents WHERE id LIKE ?",
                         (f"{intent_id}%",))
            served = json.loads(row["metadata"] or "{}").get("served_by") or []
            check(f"WS-13 — {intent_id} carries one served_by entry on its "
                  f"OWN row, with the disposition ({expected}) and the role",
                  len(served) == 1 and served[0]["file"] == "alpha.md"
                  and served[0]["disposition"] == expected
                  and served[0]["role"] in ("primary", "secondary"),
                  json.dumps(served))

        # ---- WS-4 / WS-5: explicit --intent overrides derivation entirely.
        run(ws, "summarize", "rebuild")   # alpha's rewrite made it stale
        out = run_stdin(ws, "", "write", "start", "beta.md",
                        "--intent", i_wide, "--intent", i_beta)
        wu_beta = out.split("[")[1].split("]")[0]
        block = _block_of(db, wu_beta)
        check("WS-4 — --intent flags override derivation ENTIRELY: the "
              "members are exactly the named ones",
              {i[:8] for i in _member_ids(block)} == {i_wide, i_beta},
              str(block["members"]))
        check("WS-4 — and the block records that it was manual",
              block["manual"] is True, str(block))
        check("WS-4 — the author's FIRST flag is the primary, even when the "
              "second is more specific",
              block["primary"].startswith(i_wide), block["primary"])
        check("WS-4 — an intent whose scope does not cover this file is "
              "KEPT and noted, never refused — reaching outside the tier is "
              "a legitimate authorial act",
              _tier_of(block, i_beta) == "outside"
              and "scoped outside this essay" in out, out)
        run(ws, "write", "abandon", "--writeup", "beta.md")
        run(ws, "summarize", "rebuild")

        out = run_stdin(ws, "", "write", "start", "gamma.md",
                        "--intent", i_wide)
        wu_gamma = out.split("[")[1].split("]")[0]
        row = db.one("SELECT intent_id FROM writeups WHERE id LIKE ?",
                     (f"{wu_gamma}%",))
        check("WS-5 — the single-flag command that existed before this "
              "design still binds writeups.intent_id to that intent and "
              "carries it as the one member",
              row["intent_id"].startswith(i_wide)
              and {i[:8] for i in _member_ids(_block_of(db, wu_gamma))}
              == {i_wide}, row["intent_id"])
        run_stdin(ws, WS_PLAN, "write", "plan", "--writeup", "gamma.md")
        run_stdin(ws, "Gamma's only beat.", "write", "propose",
                  "--writeup", "gamma.md", "--why", "opens")
        run_stdin(ws, "", "write", "accept", "--writeup", "gamma.md")
        out = run(ws, "write", "complete", "--writeup", "gamma.md")
        check("WS-12 — an UNTAGGED beat serves every member",
              f"served    [{i_wide}" in out, out)
        served = json.loads(db.one(
            "SELECT metadata FROM declared_intents WHERE id LIKE ?",
            (f"{i_wide}%",))["metadata"])["served_by"]
        check("WS-13 — a second writeup APPENDS a second served_by entry, "
              "deduped by writeup id",
              len(served) == 2
              and {s["file"] for s in served} == {"alpha.md", "gamma.md"},
              json.dumps(served))
    finally:
        server.shutdown()


def scenario_intent_tie(root: Path) -> None:
    """Scenario WS3 — the tie, and the plan's two other new refusals."""
    print("Scenario WS3 — the primary tie and the plan-time refusals")
    server = http.server.HTTPServer(("127.0.0.1", 0), StubLLMHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ws, ms, db, manuscript = _ws_workspace(root, server, name="ws3")
        run(ws, "session", "start")
        i_one = _declare(ws, "Rewrite gamma around the refrain",
                         "--scope", "gamma.md")
        i_two = _declare(ws, "Cut the inheritance down to one paragraph",
                         "--scope", "gamma.md")
        i_dead = _declare(ws, "A goal that will be closed mid-writeup",
                          "--chapter", "part.md")

        # ---- WS-7: the tie is ANNOUNCED at start (refusing the start
        # would be a gate-parade regression — the author would lose the
        # truncation they came for) and REFUSED at the plan.
        out = run_stdin(ws, "", "write", "start", "gamma.md")
        wu = out.split("[")[1].split("]")[0]
        check("WS-7 — write start proceeds and names the tied candidates "
              "rather than sending the author away to look them up",
              "equally specific" in out and i_one in out and i_two in out
              and "write intents --primary" in out, out)
        check("WS-7 — the stored primary is null while the tie is unsettled",
              _block_of(db, wu)["primary"] is None, str(_block_of(db, wu)))
        out = run_stdin(ws, WS_PLAN, "write", "plan", "--writeup", "gamma.md",
                        expect_exit=True)
        check("WS-7 — write plan REFUSES while the primary is unsettled, "
              "repeating both candidates and the one command that clears it",
              "tied for primary" in out and i_one in out and i_two in out
              and "write intents --primary" in out, out)
        run(ws, "write", "intents", "--primary", i_two, "--writeup", "gamma.md")

        # ---- WS-8: a dead member, then a beat tag naming a non-member.
        run(ws, "intent", "abandon", i_dead, "--outcome", "changed plans")
        out = run_stdin(ws, WS_PLAN, "write", "plan", "--writeup", "gamma.md",
                        expect_exit=True)
        check("WS-8 — write plan REFUSES a member that died between start "
              "and plan: a silently changed set is not a ratified set",
              "abandoned, not active" in out
              and f"write intents --remove {i_dead}" in out, out)
        run(ws, "write", "intents", "--remove", i_dead, "--writeup", "gamma.md")

        stranger = _declare(ws, "An intent scoped to another essay entirely",
                            "--scope", "beta.md")
        bad = json.dumps([{"role": "opener", "concepts": ["Choice"],
                           "budget": 60, "intents": [stranger]}])
        out = run_stdin(ws, bad, "write", "plan", "--writeup", "gamma.md",
                        expect_exit=True)
        check("WS-8 — and it REFUSES a beat tag naming a non-member, "
              "listing the members (the digest's dangling-reference rule)",
              stranger in out and "not a member" in out
              and "write intents --add" in out, out)

        out = run_stdin(ws, WS_PLAN, "write", "plan", "--writeup", "gamma.md")
        check("WS-7 — with the tie settled and the dead member dropped, the "
              "plan ratifies and prints the frozen set",
              "Plan ratified" in out and "FROZEN" in out
              and "← primary" in out, out)
        check("WS-7 — and writeups.intent_id now holds the chosen primary",
              db.one("SELECT intent_id FROM writeups WHERE id LIKE ?",
                     (f"{wu}%",))["intent_id"].startswith(i_two), "")
    finally:
        server.shutdown()


def _load_testbench():
    """tools/testbench.py as a module. `tools/` is a directory of scripts,
    not a package, so it is loaded by path rather than imported — the same
    file the operator runs, with no packaging invented for the test."""
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "tools" / "testbench.py"
    spec = importlib.util.spec_from_file_location("testbench", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scenario_testbench(root: Path) -> None:
    """The live test bench (tools/testbench.py), driven hermetically.

    The bench itself exists to run against ~/.authorlm; this scenario
    proves the DRIVER — setup idempotence, the placeholder check, and
    above all that the visibility assertions DISCRIMINATE — against a
    temp workspace and the stub model server, so the suite never opens
    the author's database and never spends a live call."""
    import subprocess

    print("Scenario TB — the live test bench, driven against a temp workspace")
    tb = _load_testbench()
    server = http.server.HTTPServer(("127.0.0.1", 0), StubLLMHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ws = root / "tb"
        msdir = root / "tb-manuscripts" / tb.BENCH
        cfg = ws / ".authorlm" / "config.toml"
        write(cfg, "[llm]\nenabled = true\nprovider = \"openai\"\n"
                   f"base_url = \"http://127.0.0.1:{server.server_port}/v1\"\n"
                   "model = \"stub\"\n")
        env = dict(os.environ)
        env["AUTHORLM_CONFIG"] = str(cfg)
        env["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
        env["AUTHORLM_CLIENT"] = "none"   # inherited already; stated anyway

        def bench(*argv, expect_exit: int = 0) -> str:
            proc = subprocess.run(
                [sys.executable,
                 str(Path(__file__).resolve().parent.parent / "tools"
                     / "testbench.py"),
                 "--workspace", str(ws), "--manuscript-dir", str(msdir),
                 *argv],
                env=env, capture_output=True, text=True)
            out = proc.stdout + proc.stderr
            assert proc.returncode == expect_exit, (
                f"testbench {argv} exited {proc.returncode}, expected "
                f"{expect_exit}\n{out}")
            return out

        # --- the safety rail, before anything exists ---------------------
        proc = subprocess.run(
            [sys.executable,
             str(Path(__file__).resolve().parent.parent / "tools"
                 / "testbench.py"),
             "--workspace", str(ws),
             "--manuscript-dir", str(root / "tb-manuscripts" / "smsttd"),
             "--setup"],
            env=env, capture_output=True, text=True)
        check("the bench refuses any directory not named 'testbench' — the "
              "name is the rail, and it is a constant, not a flag",
              proc.returncode == 2
              and "must be named 'testbench'" in proc.stderr,
              proc.stdout + proc.stderr)
        check("the refusal wrote nothing", not (root / "tb-manuscripts"
                                                / "smsttd").exists())

        # --- setup -------------------------------------------------------
        before = StubLLMHandler.REQUESTS
        out = bench("--setup")
        check("--setup prints what it will create BEFORE creating it",
              "--setup will, in" in out
              and "register the manuscript 'testbench'" in out
              and "the ONLY step that calls a model" in out, out)
        check("--setup writes the three bench essays and a toc from the "
              "constants in the script (manuscripts/ is gitignored, so the "
              "script is the source of truth)",
              all((msdir / f).read_text() == tb.FILE_TEXT[f]
                  for f in tb.FILES), sorted(p.name for p in msdir.iterdir()))
        check("the bench toc carries a real PARENT CHAIN, which is what "
              "gives it a chapter tier at all",
              'parent = "01-figure.md"' in (msdir / "toc.toml").read_text(),
              (msdir / "toc.toml").read_text())
        check("--setup builds the summaries and prints the cost line — the "
              "one place the bench spends money",
              "built 3" in out and "LLM: 3 live call(s)" in out, out)
        check("exactly three model calls: one summary per essay, and no "
              "concept extraction (init runs --no-extract)",
              StubLLMHandler.REQUESTS - before == 3,
              f"{StubLLMHandler.REQUESTS - before} calls")

        # --- idempotence --------------------------------------------------
        before = StubLLMHandler.REQUESTS
        out = bench("--setup")
        check("a second --setup on a healthy bench is a no-op that SAYS so",
              "is healthy" in out and "Nothing to do" in out
              and "will, in" not in out, out)
        check("the no-op run spends nothing",
              StubLLMHandler.REQUESTS == before,
              f"{StubLLMHandler.REQUESTS - before} calls")

        # --- the Sponsor's check -------------------------------------------
        original = (msdir / tb.TARGET).read_bytes()
        before = StubLLMHandler.REQUESTS
        out = bench("--check", "placeholder")
        check("the placeholder check passes on a healthy bench",
              "all assertions passed" in out and "FAIL" not in out, out)
        check("it asserts the on-disk file is EXACTLY the placeholder",
              "byte for byte" in out, out)
        check("it asserts the marker is visible markdown — not an HTML "
              "comment, not front matter, not a code fence, and a plain "
              "paragraph in the rendered body",
              "NOT inside an HTML comment" in out
              and "NOT inside YAML front matter" in out
              and "NOT inside a code fence" in out
              and "renders as a plain paragraph" in out, out)
        check("it asserts write abandon restored the essay byte for byte",
              "restored the essay byte for byte" in out, out)
        check("the check leaves the bench exactly as it found it",
              (msdir / tb.TARGET).read_bytes() == original,
              (msdir / tb.TARGET).read_text())
        check("ZERO live model calls during the check — it runs with the "
              "provider keys scrubbed out of the environment and passes "
              "with no keys at all",
              StubLLMHandler.REQUESTS == before,
              f"{StubLLMHandler.REQUESTS - before} calls")

        # --- the intent-scope check ----------------------------------------
        before = StubLLMHandler.REQUESTS
        out = bench("--check", "intent-scope")
        check("the intent-scope check passes on a healthy bench",
              "all assertions passed" in out and "FAIL" not in out, out)
        check("it drives the real derivation: start with NO --intent, all "
              "three tiers labelled, the file-scoped one primary",
              "derives the set rather than refusing" in out
              and "labelled file" in out and "labelled chapter" in out
              and "labelled manuscript" in out
              and "the file-scoped intent is the primary" in out, out)
        check("it proves the freeze at write plan, in the output AND in the "
              "stored state",
              "RATIFIES the set" in out and "really is 'frozen'" in out, out)
        check("it proves an intent declared after ratification is flagged "
              "and does NOT join",
              "newly in scope" in out and "did NOT join" in out, out)
        check("the check restores the essay and leaves no intents behind, "
              "so the bench is idempotent for the next run",
              (msdir / tb.TARGET).read_bytes() == original
              and "left no bench intents behind" in out
              and "FAIL" not in out, out)
        check("ZERO live model calls: the whole derive → freeze → flag "
              "cycle is deterministic",
              StubLLMHandler.REQUESTS == before,
              f"{StubLLMHandler.REQUESTS - before} calls")
        check("--list-checks names it",
              "intent-scope" in bench("--list-checks"), "")


        # --- the check must DISCRIMINATE ------------------------------------
        # A visibility test that passes on a HIDDEN marker is worse than no
        # test: it would have signed off on exactly the failure the Sponsor
        # asked about. So the same check is run against a bench whose file
        # has been rewritten, after `write start`, into an HTML comment.
        _pin_config(ws)
        before = StubLLMHandler.REQUESTS
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            failed = tb.check_placeholder(
                ws, msdir, _corrupt=lambda t: "<!--\n" + t + "\n-->\n")
        negative = buffer.getvalue()
        check("the check FAILS when the marker is hidden inside an HTML "
              "comment — the discrimination the whole test rests on",
              failed >= 1, negative)
        check("and it fails on the VISIBILITY assertions by name, not "
              "merely on the byte-equality one",
              "FAIL: the marker is NOT inside an HTML comment" in negative
              and "FAIL: the marker survives the strip" in negative, negative)
        check("the try/finally holds: a failed check still abandons the "
              "writeup and restores the essay byte for byte",
              (msdir / tb.TARGET).read_bytes() == original
              and "ok: write abandon restored" in negative, negative)
        check("the discriminating run spent nothing either",
              StubLLMHandler.REQUESTS == before,
              f"{StubLLMHandler.REQUESTS - before} calls")

        # The other two hiding places, at the level of the parser itself.
        hidden_front = f"---\ntitle: x\nmarker: {tb.MARKER}\n---\n\nProse.\n"
        hidden_fence = f"Prose.\n\n```\n{tb.MARKER}\n```\n"
        check("front matter hides a marker from the rendered body",
              tb.MARKER not in tb.visible_body(hidden_front)
              and tb.MARKER in tb.front_matter(hidden_front), hidden_front)
        check("a code fence hides a marker from the rendered body",
              tb.MARKER not in tb.visible_body(hidden_fence)
              and tb.MARKER in tb.code_blocks(hidden_fence), hidden_fence)
        check("a heading or a list item is visible but is NOT a plain "
              "paragraph — the parser distinguishes the two",
              not any(tb.MARKER in ln
                      for ln in tb.paragraph_lines(f"# {tb.MARKER}\n"))
              and any(tb.MARKER in ln
                      for ln in tb.paragraph_lines(f"{tb.MARKER}\n")), "")
        check("an UNTERMINATED HTML comment swallows the rest of the file, "
              "and the parser knows it",
              tb.MARKER not in tb.visible_body(f"<!--\nnote\n{tb.MARKER}\n"),
              "")

        # --- refusal: a writeup is already active on the bench file ---------
        out = run(ws, "-m", tb.BENCH, "intent", "declare", "A second intent")
        intent_id = out.split("[")[1].split("]")[0]
        run_stdin(ws, "", "-m", tb.BENCH, "write", "start", tb.TARGET,
                  "--intent", intent_id)
        out = bench("--check", "placeholder", expect_exit=2)
        check("the check refuses to run while a writeup is already active "
              "on the bench file, naming the writeup and the way out",
              "already active on" in out and "write abandon --writeup" in out,
              out)
        run_stdin(ws, "", "-m", tb.BENCH, "write", "abandon",
                  "--writeup", tb.TARGET)
        check("and the bench is checkable again once it is settled",
              "all assertions passed"
              in bench("--check", "placeholder"), "")

        # --- the filter check ----------------------------------------------
        # LAST, deliberately: it settles the essay and rebuilds its
        # summary, which leaves that summary stale against the text the
        # rollback puts back. Every check above needs a fresh summary to
        # get past the drafting gate, so this one runs after them.
        before = StubLLMHandler.REQUESTS
        out = bench("--check", "filter")
        check("the filter check passes on a healthy bench",
              "all assertions passed" in out and "FAIL" not in out, out)
        check("it proves the payload is deterministic and made no call",
              "made NO live model call" in out
              and "byte-identical prefix" in out, out)
        check("it asserts the ECHO refusal ON THE DATABASE — no thread "
              "row, cursor still 0 — rather than on the message alone",
              "asserted ON THE DATABASE" in out, out)
        check("it asserts proposed_old came from DISK, not from the reply",
              "READ FROM DISK" in out, out)
        check("it asserts the verdict evidence carries no episode",
              "episode_id IS NULL" in out, out)
        check("it asserts the marked form is VISIBLE markdown — the same "
              "four hiding places --check placeholder tests, and for the "
              "same reason: the author reads this in Obsidian",
              "NOT inside an HTML comment" in out
              and "NOT inside YAML front matter" in out
              and "NOT inside a code fence" in out
              and "renders as a plain paragraph" in out, out)
        check("it asserts the canonicalization LIVE: while marked, every "
              "read path still reports the original text",
              "ORIGINAL text byte for byte" in out, out)
        check("it asserts the finalized bytes and the identical-text "
              "refusal",
              "exactly the original with that one unit replaced" in out
              and "refuses and names the prior run" in out, out)
        check("it asserts the DOC transport's refusals too — the honest, "
              "partial answer §6 rules for it: every one reads the run's "
              "own state, so none can reach the network even on a fully "
              "authorized bridge",
              "refuses 'filter push' by name" in out
              and "refuses 'filter resolve --pause' by name" in out
              and "prints the run's transport" in out
              and "names 'filter resolve'" in out, out)
        check("...and each of those is PAIRED with the same verb at the "
              "other mode, so the section is proved to discriminate "
              "rather than to pass whatever it is handed",
              "the discrimination without which the refusal below proves "
              "nothing" in out and "says the OTHER thing" in out, out)
        check("the check leaves the bench exactly as it found it",
              (msdir / tb.TARGET).read_bytes() == original,
              (msdir / tb.TARGET).read_text())
        check("and it removes the filter artifact it created — the "
              "author's own _filters/ is never touched",
              not (msdir / "_filters" / "tb-dupes.md").exists())
        check("the ONLY model call in the whole filter check is the "
              "settle's summary rebuild — the filter path itself makes "
              "none, which is the design's claim stated exactly rather "
              "than loosely. On the author's own bench the keys are "
              "scrubbed, so that one call fails and the resolve warns "
              "soft: zero live spend there, one stub call here",
              StubLLMHandler.REQUESTS - before == 1,
              f"{StubLLMHandler.REQUESTS - before} calls")
        check("--list-checks names it",
              "filter " in bench("--list-checks"), "")

        # The filter check must DISCRIMINATE too: a visibility assertion
        # that passes on a HIDDEN form would sign off on exactly the
        # failure it exists to catch (§14.8).
        _pin_config(ws)
        before = StubLLMHandler.REQUESTS
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            failed = tb.check_filter(
                ws, msdir, _corrupt=lambda x: "<!--\n" + x + "\n-->\n")
        negative = buffer.getvalue()
        check("the filter check FAILS when the marked forms are hidden "
              "inside an HTML comment",
              failed >= 1, negative)
        check("and it fails on the VISIBILITY assertions by name",
              "FAIL: the form is NOT inside an HTML comment" in negative
              and "FAIL: the form survives the strip" in negative, negative)
        check("the try/finally holds: a failed filter check still rolls "
              "back and restores the essay byte for byte",
              (msdir / tb.TARGET).read_bytes() == original
              and "restored the essay byte for byte" in negative, negative)
        check("the discriminating run spends no more than that one "
              "rebuild either",
              StubLLMHandler.REQUESTS - before <= 1,
              f"{StubLLMHandler.REQUESTS - before} calls")

        # --- the pronunciations check (§15.22) ---------------------------
        _pin_config(ws)
        # WITH NO DICTIONARY AT ALL, first, and that ordering is the
        # point. The first cut of this check asserted only "the protected
        # set is non-empty" against a bench registered `--no-extract`,
        # whose graph is empty by design — so it could never pass on its
        # own. It passed here only because the test wrote a
        # pronunciations.md moments earlier and list 3 (the dictionary's
        # own terms) made the set non-empty. The check was green for a
        # reason that had nothing to do with the derivation.
        check("the bench has no dictionary yet — the case the check used "
              "to be unable to pass",
              not (msdir / "pronunciations.md").exists())
        before = StubLLMHandler.REQUESTS
        out = bench("--check", "pronunciations")
        check("the pronunciations check passes on a healthy bench with NO "
              "dictionary, because --setup seeds the graph the derivation "
              "runs over, and it asserts the seeded names BY NAME rather "
              "than merely counting them",
              "FAIL" not in out
              and "carries the bench's vocabulary kinds BY NAME" in out
              and "F1 admits all 16 non-ASCII-LETTER names" in out
              and "F1 refuses the two whose only non-ASCII character is a "
                  "curly apostrophe" in out, out)
        check("...and the two assertions that used to pass VACUOUSLY over "
              "empty sets now assert the bench really has a retired name "
              "and a label-kind node for them to be about",
              "the bench really HAS a retired name" in out
              and "the bench really HAS a label-kind node" in out, out)
        check("...and F1 runs over a name the DERIVATION produced, not "
              "only over the recorded literals",
              "F1 runs over a name the DERIVATION produced" in out, out)
        (msdir / "pronunciations.md").write_text(
            "# Pronunciations\n\nHow they are said.\n\n"
            "| Term | Say it | Note |\n| --- | --- | --- |\n"
            "| Nothing | NUH-thing | the book's own word |\n",
            encoding="utf-8")
        out = bench("--check", "pronunciations")
        check("and it passes with a dictionary too, whose terms join the "
              "protected set through list 3",
              "FAIL" not in out, out)
        check("...and it is READ-ONLY: not one model call, and no verb "
              "that writes",
              StubLLMHandler.REQUESTS == before,
              f"{before} -> {StubLLMHandler.REQUESTS}")
        check("--list-checks names it",
              "pronunciations " in bench("--list-checks"), "")
        # The check must DISCRIMINATE, and the corruption is the real
        # failure mode rather than an invented one: a Docs export that
        # turned the table into bullet lines. (The design named an HTML
        # comment; a line-oriented pipe-table parser does not care about
        # one, and asserting that it does would assert a behaviour that
        # does not exist. The mangled table is what the pull guard exists
        # for, so it is the corruption worth catching here too.)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            failed = tb.check_pronunciations(
                ws, msdir, _corrupt=lambda x: x.replace(
                    "| Nothing | NUH-thing | the book's own word |",
                    "- Nothing NUH-thing the book's own word"))
        negative = buffer.getvalue()
        check("the pronunciations check FAILS BY NAME on a dictionary "
              "whose table has been mangled into bullet lines — it never "
              "reads a lost table as an empty one",
              failed >= 1
              and "FAIL: pronunciations.md parses with no warnings"
              in negative, negative)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            failed_dup = tb.check_pronunciations(
                ws, msdir, _corrupt=lambda x: x
                + "| nothing | NAW-thing | a second, lower case |\n")
        dup_out = buffer.getvalue()
        check("...and on a dictionary carrying two rows for one term, "
              "which the parser reports and never deletes",
              failed_dup >= 1
              and "appears more than once" in dup_out, dup_out)
        (msdir / "pronunciations.md").unlink()

    finally:
        server.shutdown()



# ==================================================================
# Scenario F — the filter pass (docs/filter-pass-design.md §7.1)
#
# Zero model calls except the ONE summary rebuild each settle makes, and
# every one of those is asserted against the stub's own counter rather
# than assumed.
# ==================================================================

FILTER_ESSAY = "\n\n".join([
    "# The Wall",
    "Moreover the wall stands. Moreover it stands again, and the wall is "
    "what the Dead cannot pass.",
    "The ground is what the figure was taken out of. It has no shape of "
    "its own, which is why it is so easy to mistake for nothing at all.",
    "Moreover the ledger counts. The ledger counts, and the ledger counts "
    "again, and nothing in it is an accusation.",
    "Fire is what the Qualities do to a life. It is always the agent and "
    "never a thing a person holds.",
    "A frame does not add anything to what it surrounds. It subtracts "
    "everything else, and the subtraction is felt as emphasis.",
    "Every ground was once a figure and will be again. The two are two "
    "offices, not two kinds of thing.",
    "And so the wall is not stone. And so it is not anything a hand could "
    "test by pushing.",
    "The count itself is neutral. Neither friend nor accuser, only the "
    "count, kept where a life can read it.",
    "Choosing the edge is the whole of the composition. The rest is "
    "arrangement, and arrangement is not composition.",
    "A listener cannot look back. They hold about one sentence in mind at "
    "a time, and everything else follows from that.",
    "The Test is not a test. The capitalization does all the work on the "
    "page and none of it aloud.",
    "There is no such thing as a background. There is only what is not, "
    "at this moment, being attended to.",
    "The wall stands where it stood. Nothing has moved it and nothing "
    "will, which is the whole of the difficulty.",
]) + "\n"

SEQ_FILTER = (
    '---\nclass = "sequential"\n'
    'state = "a ledger of every word already flagged as repeated"\n---\n\n'
    "# Duplicate words and phrases\n\n"
    "Flag a word or phrase used again too soon, and propose the wording "
    "that removes the repetition without removing anything else.\n")

GLOBAL_FILTER = (
    '---\nclass = "global"\n'
    'state = "not used — the registry is this filter\'s coordination object"\n'
    "---\n\n"
    "# Metaphor consistency\n\n"
    "The book's figures are a system, not decoration. Return a registry "
    "naming every motif family and what it is doing HERE.\n")


def _filter_units(text: str) -> list[str]:
    from authorlm.passes import paragraphs_of

    return paragraphs_of(text)


def _filter_reply(units, window, replaces=None, state="ledger: (empty)",
                  echo_override=None, drop=None, extra=None):
    """A contract-valid reply for `window`, with named perturbations.

    Every anchoring test differs from the valid reply in exactly one
    respect, which is what makes each refusal attributable."""
    from authorlm.passes import echo_of

    replaces = replaces or {}
    entries = []
    for n in range(window[0], window[1] + 1):
        if drop is not None and n == drop:
            continue
        echo = echo_of(units[n - 1])
        if echo_override and n in echo_override:
            echo = echo_override[n]
        if n in replaces:
            entries.append({"n": n, "echo": echo, "action": "replace",
                            "new": replaces[n], "why": "the stub's reason",
                            "ref": "moreover"})
        else:
            entries.append({"n": n, "echo": echo, "action": "keep"})
    if extra:
        entries.extend(extra)
    body = {"units": entries}
    if state is not None:
        body["state"] = state
    return json.dumps(body)


def api_filter_edit_count(db, mid: str, file: str) -> int:
    return db.one(
        "SELECT COUNT(*) AS n FROM doc_threads WHERE manuscript_id = ? "
        "AND origin_type = 'filter' AND file = ? AND state = 'proposed'",
        (mid, file))["n"]


def scenario_filter(root: Path) -> None:
    print("Scenario FP — the filter pass, unit by unit (design §7.1's Scenario F)")
    import authorlm.gdocs as _gd
    from authorlm import api as _fapi
    from authorlm.db import Database as _DB, loads as _loads

    server = http.server.HTTPServer(("127.0.0.1", 0), StubLLMHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ws = root / "flt"
        ms = ws / "manuscript"
        write(ms / "01-open.md", CH1)
        write(ms / "02-wall.md", FILTER_ESSAY)
        write(ms / "03-close.md", CH3)
        _draft_config(ws, server.server_port)
        run(ws, "init", "--name", "book", "--path", str(ms))
        run(ws, "session", "start")
        # A declared goal with an OPEN episode, from the start: the
        # settle's attribution assertions are worthless unless there is
        # something for the resolve to be mis-attributed TO (§14.8).
        run(ws, "intent", "declare", "Foreground the wall as a refrain")
        db = _DB(ws / ".authorlm" / "authorlm.db")
        manuscript = _fapi.get_manuscript(db)
        mid = manuscript["id"]
        run(ws, "collect")
        units = _filter_units(FILTER_ESSAY)
        check("the bench essay really has the units the window tests need",
              len(units) == 14, len(units))

        # ---------------- F1-F6: the artifact and its class -----------
        out = run_stdin(ws, SEQ_FILTER, "filter", "add", "duplicate-words")
        check("F1 filter add writes the artifact and reports its class",
              "_filters/duplicate-words.md" in out
              and "class = sequential" in out, out)
        artifact = (ms / "_filters" / "duplicate-words.md").read_text()
        check("F1 the artifact round-trips verbatim",
              artifact == SEQ_FILTER.strip() + "\n", repr(artifact[:80]))

        out = run_stdin(ws, '---\nstate = "x"\n---\n\n# No class\n\nBody.\n',
                        "filter", "add", "no-class", expect_exit=True)
        check("F2 front matter with no class is refused, naming BOTH legal "
              "values — a filter is never silently defaulted into a class",
              "'sequential'" in out and "'global'" in out, out)
        check("F2 and the refused artifact was never written",
              not (ms / "_filters" / "no-class.md").exists())

        out = run_stdin(ws, '---\nclass = "wholefile"\n---\n\n# X\n\nBody.\n',
                        "filter", "add", "bad-class", expect_exit=True)
        check("F3 an unknown class is refused, naming both legal values "
              "and saying a third class is code, not configuration",
              "wholefile" in out and "'sequential'" in out
              and "'global'" in out and "is code" in out, out)

        out = run_stdin(ws, '---\nclass = "sequential"\nklass = "x"\n---\n\n'
                        "# X\n\nBody.\n", "filter", "add", "typo",
                        expect_exit=True)
        check("F4 an unknown front-matter key is refused BY NAME, at the "
              "moment it is written rather than at the first run",
              "klass" in out, out)

        out = run_stdin(ws, SEQ_FILTER, "filter", "add", "Not A Slug",
                        expect_exit=True)
        check("F5 a name failing the kebab-case rule is refused",
              "kebab-case" in out, out)

        run_stdin(ws, GLOBAL_FILTER, "filter", "add", "metaphor")
        out = run(ws, "filter", "list")
        check("F6 filter list prints each artifact's first PROSE line as "
              "its summary, with its class — and the front matter never "
              "appears in it",
              "duplicate-words [sequential]: Duplicate words and phrases"
              in out and "metaphor [global]: Metaphor consistency" in out
              and "class =" not in out, out)

        # ---------------- F7-F12: payload and determinism -------------
        before = StubLLMHandler.REQUESTS
        out1 = run(ws, "filter", "run", "duplicate-words", "02-wall.md")
        check("F7 filter run makes ZERO model calls — the default, and "
              "the opposite of write draft's",
              StubLLMHandler.REQUESTS == before,
              f"{before} -> {StubLLMHandler.REQUESTS}")
        check("F7 and it prints all four blocks with their hashes",
              all(f"───── block {b} — " in out1 for b in "SABC"), out1)
        check("F7 the output says the polarity inversion out loud",
              "opposite of 'write draft'" in out1, out1)
        out2 = run(ws, "filter", "run", "duplicate-words", "02-wall.md")
        dry = run(ws, "filter", "run", "duplicate-words", "02-wall.md",
                  "--dry-run")
        check("--dry-run is a SILENT NO-OP alias, kept for muscle memory "
              "from `write draft`: identical output, and still no call",
              _block_hash(dry, "S") == _block_hash(out2, "S")
              and _block_hash(dry, "C") == _block_hash(out2, "C")
              and StubLLMHandler.REQUESTS == before,
              f"{_block_hash(dry, 'C')} vs {_block_hash(out2, 'C')}")
        check("F8 two consecutive runs with no state change produce "
              "BYTE-IDENTICAL blocks S and A (the write path's "
              "silent-invalidator check, reused)",
              _block_hash(out1, "S") == _block_hash(out2, "S")
              and _block_hash(out1, "A") == _block_hash(out2, "A"),
              f"S {_block_hash(out1, 'S')} vs {_block_hash(out2, 'S')}; "
              f"A {_block_hash(out1, 'A')} vs {_block_hash(out2, 'A')}")
        check("F10 an absent section renders (none) rather than "
              "disappearing — the block's shape never changes",
              "FILTER STATE (carry it forward and return its updated "
              "value)\n(none)" in _block(out1, "C"), _block(out1, "C"))
        check("F12 a SEQUENTIAL run's block A carries no MOTIF REGISTRY "
              "and no whole-essay pin — class resolution is observable in "
              "the bytes, not just in the code",
              "MOTIF REGISTRY" not in _block(out1, "A")
              and "THE ESSAY —" not in _block(out1, "A"),
              _block(out1, "A"))

        out = run(ws, "filter", "run", "metaphor", "02-wall.md",
                  expect_exit=True)
        check("F11 a global filter refuses before its prelude and names "
              "the verb that supplies one",
              "filter prelude metaphor 02-wall.md" in out, out)
        # run_stdin with an empty payload: `filter prelude` reads the
        # registry off stdin when one is piped in, the same idiom
        # `lens add` and `write plan` use, so a harness that leaves stdin
        # an open pipe would otherwise block here.
        pre = run_stdin(ws, "", "filter", "prelude", "metaphor",
                        "02-wall.md")
        check("F11 the prelude payload is printed with no call made",
              "no call made" in pre and "THE PRELUDE" in pre, pre)
        run_stdin(ws, json.dumps({"registry": "wall — the limit of a life "
                                              "lived without the Test."}),
                  "filter", "prelude", "metaphor", "02-wall.md")
        g1 = run(ws, "filter", "run", "metaphor", "02-wall.md")
        g2 = run(ws, "filter", "run", "metaphor", "02-wall.md")
        check("F11 after the prelude every unit payload carries the SAME "
              "registry bytes — it is frozen for the life of the run",
              _block_hash(g1, "A") == _block_hash(g2, "A")
              and "wall — the limit of a life" in _block(g1, "A"),
              _block_hash(g1, "A"))
        check("F12 a GLOBAL run's payload carries no FILTERED PREFIX "
              "content, and says why in as many words",
              "not applicable" in _block(g1, "B")
              and "MOTIF REGISTRY" in _block(g1, "A"), _block(g1, "B"))
        run(ws, "filter", "abandon", "metaphor", "02-wall.md")

        # ------------- F13-F21: anchoring refusal, nothing staged -----
        def staged_now() -> int:
            return len(db.all(
                "SELECT id FROM doc_threads WHERE manuscript_id = ? AND "
                "origin_type = 'filter' AND file = '02-wall.md'", (mid,)))

        def cursor_now() -> int:
            return db.one(
                "SELECT cursor FROM filter_runs WHERE manuscript_id = ? "
                "AND filter = 'duplicate-words' AND status = 'active'",
                (mid,))["cursor"]

        window = (1, 14)
        for label, reply, needle in [
            ("F13 an echo mismatch on ONE unit refuses the WHOLE reply",
             _filter_reply(units, window,
                           echo_override={7: "Not the real echo at all"}),
             "echo mismatch"),
            # A NEAR MISS, not a wild one: the real echo with a single
            # word changed. This is the case a well-meaning fuzzy
            # matcher would wave through, and the whole anchoring law is
            # that there is no fuzzy match and no closest paragraph. A
            # casefold-prefix or edit-distance matcher passes every
            # other test in this loop and fails only this one.
            ("the anchoring law admits NO fuzzy match: the real echo "
             "with one word changed is still refused, and the expected "
             "and received echoes are both printed",
             _filter_reply(units, window, echo_override={
                 7: " ".join(units[6].split()[:4] + ["stone"])}),
             "echo mismatch"),
            ("...and a case-only difference is refused too",
             _filter_reply(units, window, echo_override={
                 7: " ".join(units[6].split()[:5]).upper()}),
             "echo mismatch"),
            ("F14 a missing unit entry refuses the whole reply, naming "
             "the missing n",
             _filter_reply(units, window, drop=9), "missing n=9"),
            ("F15 an n outside the window refuses",
             _filter_reply(units, window,
                           extra=[{"n": 99, "echo": "x", "action": "keep"}]),
             "not in this window: n=99"),
            ("F16 a replacement containing '<<' refuses, naming the marker",
             _filter_reply(units, window,
                           replaces={4: "The ledger <<counts>> once."}),
             "'<<'"),
            ("F16 likewise '{{'",
             _filter_reply(units, window,
                           replaces={4: "The ledger {{counts}} once."}),
             "'{{'"),
            ("F17 an empty replacement refuses — 'delete this paragraph' "
             "is not a filter's judgment to make",
             _filter_reply(units, window, replaces={4: "   "}),
             "not a filter's judgment"),
            ("F19 action 'insert' is refused, saying a filter never ADDS "
             "a unit",
             json.dumps({"units": [
                 {"n": n, "echo": " ".join(units[n - 1].split()[:5]),
                  "action": "insert" if n == 4 else "keep"}
                 for n in range(1, 15)], "state": "x"}),
             "never ADDS a unit"),
            ("a sequential reply with no state refuses — the next window "
             "is conditioned on it",
             _filter_reply(units, window, state=None),
             "must return its updated `state`"),
            ("the 4,000-character state cap refuses rather than "
             "truncating: a silently truncated ledger lies",
             _filter_reply(units, window, state="x" * 4001),
             "over the 4,000 cap"),
        ]:
            out = run_stdin(ws, reply, "filter", "record", "02-wall.md",
                            expect_exit=True)
            check(label, needle in out, out)
            check(f"...{label.split()[0]}: nothing was staged and the "
                  f"cursor did not move",
                  staged_now() == 0 and cursor_now() == 0,
                  f"{staged_now()} staged, cursor {cursor_now()}")

        # F18 / F21: the two entries that are NOT refusals.
        out = run_stdin(
            ws, _filter_reply(units, window, replaces={
                6: units[5],                      # F18: new == old
                2: "The wall stands. It stands again, and the wall is what "
                   "the Dead cannot pass.",
                4: "The ledger counts.\n\nNothing in it is an accusation.",
            }),
            "filter", "record", "02-wall.md")
        check("F18 a replace whose text equals the unit is silently a "
              "KEEP and stages nothing", staged_now() == 2,
              f"{staged_now()} staged")
        check("F21 a replacement containing a blank line IS accepted",
              "2 proposal(s)" in out, out)

        # ---------------- F22-F27: the door ---------------------------
        rows = [dict(r) for r in db.all(
            "SELECT * FROM doc_threads WHERE manuscript_id = ? AND "
            "origin_type = 'filter' ORDER BY origin_id", (mid,))]
        run_row = dict(db.one(
            "SELECT * FROM filter_runs WHERE manuscript_id = ? AND "
            "filter = 'duplicate-words' AND status = 'active'", (mid,)))
        check("F22 a staged filter edit's proposed_old is BYTE-EQUAL to "
              "the source unit — read from the file, never from the reply",
              [r["proposed_old"] for r in rows] == [units[1], units[3]],
              [r["proposed_old"][:40] for r in rows])
        check("F22 and its origin_id is keyed on the UNIT "
              "({owner}:{file}:{n}), so a later window cannot recycle "
              "ordinals 1..k and overwrite earlier proposals",
              [r["origin_id"] for r in rows]
              == [f"{run_row['id']}:02-wall.md:2",
                  f"{run_row['id']}:02-wall.md:4"],
              [r["origin_id"] for r in rows])
        from authorlm import passes as _passes
        check("F23 passes.staged_threads with the DEFAULT origin_type "
              "does not return filter rows — the critique pass cannot see "
              "them and its own tests are untouched",
              _passes.staged_threads(db, mid, "02-wall.md") == [])

        out = run_stdin(ws, _filter_reply(units, window, replaces={
            2: "The wall stands. It stands again, and the wall is what the "
               "Dead cannot pass.",
            4: "The ledger counts.\n\nNothing in it is an accusation.",
        }), "filter", "record", "02-wall.md")
        after = [dict(r) for r in db.all(
            "SELECT * FROM doc_threads WHERE manuscript_id = ? AND "
            "origin_type = 'filter' ORDER BY origin_id", (mid,))]
        check("F27 a re-run over the same units REPLACES the same "
              "origin_id rows in place rather than colliding on the "
              "UNIQUE constraint",
              len(after) == 2
              and [r["id"] for r in after] == [r["id"] for r in rows],
              [r["origin_id"] for r in after])

        # A re-record with FEWER proposals must SUPERSEDE, not accumulate.
        run_stdin(ws, _filter_reply(units, window, replaces={
            2: "The wall stands. It stands again, and the wall is what the "
               "Dead cannot pass.",
        }), "filter", "record", "02-wall.md")
        open_after = [dict(r) for r in db.all(
            "SELECT * FROM doc_threads WHERE manuscript_id = ? AND "
            "origin_type = 'filter' AND state = 'proposed' "
            "ORDER BY origin_id", (mid,))]
        check("re-recording a window with FEWER proposals SUPERSEDES the "
              "ones it dropped: the reply is the authoritative answer for "
              "its window, so an ordinal it did not reach is a proposal "
              "that no longer exists. Left standing, the stale row would "
              "appear in the next `filter edits` list under a number the "
              "author reads as current, and could be accepted into a "
              "settle it was never part of",
              len(open_after) == 1
              and open_after[0]["proposed_old"] == units[1],
              [(r["origin_id"], r["state"], r["proposed_old"][:30])
               for r in open_after])
        withdrawn = db.all(
            "SELECT origin_id FROM doc_threads WHERE manuscript_id = ? "
            "AND origin_type = 'filter' AND state = 'withdrawn'", (mid,))
        check("...and the dropped one is WITHDRAWN rather than deleted — "
              "the row is history, and origin_id is UNIQUE",
              len(withdrawn) == 1, [r["origin_id"] for r in withdrawn])
        # Put the two-proposal state back for the triage that follows.
        run_stdin(ws, _filter_reply(units, window, replaces={
            2: "The wall stands. It stands again, and the wall is what the "
               "Dead cannot pass.",
            4: "The ledger counts.\n\nNothing in it is an accusation.",
        }), "filter", "record", "02-wall.md")

        beliefs_before = db.one(
            "SELECT COUNT(*) AS n FROM editorial_beliefs WHERE "
            "manuscript_id = ?", (mid,))["n"]
        # Reject the EARLIER proposal (unit 2) and accept the later one
        # (unit 4), so that §1.3's derived falsified-prefix warning has
        # downstream work to name. A rejection with nothing after it
        # would make that assertion pass vacuously.
        run(ws, "filter", "triage", "02-wall.md", "--accept", "2",
            "--reject", "1", "--reason",
            "the repetition there is the point — it is a refrain")
        ev = [dict(r) for r in db.all(
            "SELECT * FROM evidence WHERE manuscript_id = ? AND "
            "evidence_type = 'filter_edit' ORDER BY created_at", (mid,))]
        check("F24 triage verdicts write evidence rows with "
              "evidence_type='filter_edit'", len(ev) == 2, len(ev))
        check("F24 ...and EVERY one carries episode_id IS NULL: hygiene "
              "work is never filed against a declared goal",
              all(r["episode_id"] is None for r in ev),
              [r["episode_id"] for r in ev])
        rejected = next(r for r in ev if r["signal"] == "rejected")
        check("F25 the explained rejection reaches the belief machinery — "
              "asserted positively, not assumed. That path runs off the "
              "evidence stream and the author's WORDS, not off an episode",
              _loads(rejected["metadata"], {}).get("explanation")
              == "the repetition there is the point — it is a refrain"
              and db.one("SELECT COUNT(*) AS n FROM editorial_beliefs "
                         "WHERE manuscript_id = ?", (mid,))["n"]
              >= beliefs_before,
              rejected["metadata"])
        check("the author's reason is ALSO kept on the thread verbatim — "
              "that is what block A's PRIOR RUNS section renders",
              any("refrain" in (_loads(r["metadata"], {}) or {}).get(
                  "author_reason", "") for r in db.all(
                      "SELECT metadata FROM doc_threads WHERE "
                      "manuscript_id = ? AND origin_type = 'filter'",
                      (mid,))))

        # ---------------- F26 + the resolve ----------------------------
        episodes_before = {r["id"]: r["transition_ids"] for r in db.all(
            "SELECT id, transition_ids FROM editorial_episodes WHERE "
            "manuscript_id = ?", (mid,))}
        check("there IS an open episode for the resolve to be "
              "mis-attributed to (§14.8: make the guarded thing happen)",
              any(r["status"] == "open" for r in db.all(
                  "SELECT status FROM editorial_episodes WHERE "
                  "manuscript_id = ?", (mid,))))
        calls_before = StubLLMHandler.REQUESTS
        out = run(ws, "filter", "resolve", "02-wall.md")
        settled_text = (ms / "02-wall.md").read_text()
        check("the resolve applied the one accepted edit — and F21's "
              "blank-line replacement resolved to TWO paragraphs, which "
              "is the whole reason a split is allowed",
              "The ledger counts.\n\nNothing in it is an accusation."
              in settled_text
              and "Moreover the wall stands" in settled_text,
              settled_text[:300])
        check("the resolve's ONE model call is the summary rebuild, and it "
              "is the only call the whole chat-mode path makes",
              StubLLMHandler.REQUESTS == calls_before + 1,
              f"{calls_before} -> {StubLLMHandler.REQUESTS}")
        episodes_after = {r["id"]: r["transition_ids"] for r in db.all(
            "SELECT id, transition_ids FROM editorial_episodes WHERE "
            "manuscript_id = ?", (mid,))}
        grew = {k: (episodes_before[k], episodes_after[k])
                for k in episodes_before
                if episodes_after.get(k) != episodes_before[k]}
        check("F26 the resolve's collect attaches its transitions to NO "
              "episode: every open episode's transition_ids is unchanged "
              "across the resolve",
              not grew, grew)
        check("F26 ...and no NEW episode was opened to hold them either",
              not any(_loads(episodes_after[k], []) for k in
                      set(episodes_after) - set(episodes_before)),
              sorted(set(episodes_after) - set(episodes_before)))
        check("the resolve says the attribution out loud, once",
              out.count("filed against any of your goals") == 1, out)
        check("F42 rejecting unit 2 prints the downstream list and the "
              "--from remedy — derived, never stored, and deliberately "
              "not automatic: re-running the tail would discard the "
              "author's verdicts on those units",
              "n=2 rejected" in out and "Units 4" in out
              and "may have assumed it" in out
              and "--from 2" in out, out)
        check("F42 ...and it re-ran nothing: the run is settled, not "
              "reopened",
              db.one("SELECT status FROM filter_runs WHERE "
                     "manuscript_id = ? AND filter = 'duplicate-words' AND "
                     "file = '02-wall.md' ORDER BY created_at DESC LIMIT 1",
                     (mid,))["status"] == "settled")

        # ---------------- F37-F40: approximate idempotency ------------
        out = run(ws, "filter", "run", "duplicate-words", "02-wall.md",
                  expect_exit=True)
        check("F37 M1: a resolved run on this exact text refuses and names "
              "the prior run and its tallies",
              "already ran on this exact text" in out
              and "1 accepted / 1 rejected" in out and "--again" in out, out)
        again = run(ws, "filter", "run", "duplicate-words", "02-wall.md",
                    "--again")
        check("F38 --again proceeds, and block A carries the prior run's "
              "rejection reason VERBATIM — M2, in the CACHED layer",
              "the repetition there is the point — it is a refrain"
              in _block(again, "A"), _block(again, "A"))
        check("F39 the prior run's STATE is NOT carried into the new "
              "run's payload — a prior ledger described a prior text, and "
              "re-loading it would make run two condition on a fiction",
              "FILTER STATE (carry it forward and return its updated "
              "value)\n(none)" in _block(again, "C"), _block(again, "C"))
        check("F40 the PRIOR RUNS block excludes the CURRENT run",
              _block(again, "A").count("proposed,") == 1,
              _block(again, "A"))

        # Departure #3, pinned directly. A settled run's rejected
        # proposal must not appear in the NEW run's triage list: it lives
        # in PRIOR RUNS above, where its reason is law, and listing it
        # here would let `--accept 1` resurrect the very thing the author
        # refused — into a resolve it was never part of.
        settled_rejection = dict(db.one(
            "SELECT * FROM doc_threads WHERE manuscript_id = ? AND "
            "origin_type = 'filter' AND state = 'rejected' "
            "ORDER BY created_at DESC LIMIT 1", (mid,)))
        check("the resolved run's rejected proposal is still ON RECORD — "
              "it is evidence, and nothing withdrew it",
              settled_rejection["state"] == "rejected",
              settled_rejection["origin_id"])
        now_units = _filter_units((ms / "02-wall.md").read_text())
        run_stdin(ws, _filter_reply(now_units, (1, len(now_units)),
                                    replaces={6: "A brand-new proposal on "
                                                 "an entirely different "
                                                 "unit."}),
                  "filter", "record", "02-wall.md")
        listed = run(ws, "filter", "edits", "02-wall.md")
        check("...and it is NOT in the new run's numbered triage list",
              "A brand-new proposal" in listed
              and settled_rejection["proposed_new"][:40] not in listed
              and listed.count("¶") == 1, listed)
        run(ws, "filter", "triage", "02-wall.md", "--accept", "1")
        fresh_state = db.one(
            "SELECT state FROM doc_threads WHERE id = ?",
            (settled_rejection["id"],))["state"]
        check("...so `--accept 1` targets the NEW run's proposal and "
              "leaves the resolved rejection exactly as the author left "
              "it (departure #3)",
              fresh_state == "rejected"
              and db.one("SELECT state FROM doc_threads WHERE "
                         "manuscript_id = ? AND origin_type = 'filter' AND "
                         "proposed_new LIKE 'A brand-new proposal%'",
                         (mid,))["state"] == "accepted",
              fresh_state)

        # ---------------- F41/F43: warn, never block ------------------
        run(ws, "filter", "abandon", "duplicate-words", "02-wall.md")
        fresh = _filter_units((ms / "02-wall.md").read_text())
        w1 = run(ws, "filter", "run", "duplicate-words", "02-wall.md",
                 "--again", "--window", "5")
        out = run_stdin(ws, _filter_reply(fresh, (1, 5), replaces={
            2: "The wall stands once, and the Dead cannot pass it."}),
            "filter", "record", "02-wall.md")
        check("a windowed run reports what is left",
              f"{len(fresh) - 5} unit(s) left" in out, out)
        w2 = run(ws, "filter", "run", "duplicate-words", "02-wall.md",
                 "--window", "5")
        check("F9 recording a window changes blocks B and C and leaves S "
              "and A BYTE IDENTICAL — the two cached layers survive the "
              "window boundary, which is the whole point of the ordering",
              _block_hash(w1, "S") == _block_hash(w2, "S")
              and _block_hash(w1, "A") == _block_hash(w2, "A")
              and _block_hash(w1, "B") != _block_hash(w2, "B")
              and _block_hash(w1, "C") != _block_hash(w2, "C"),
              f"S {_block_hash(w1, 'S')}/{_block_hash(w2, 'S')} "
              f"A {_block_hash(w1, 'A')}/{_block_hash(w2, 'A')} "
              f"B {_block_hash(w1, 'B')}/{_block_hash(w2, 'B')}")
        check("F9 ...and block B carries the run's OWN version of the "
              "units before the window, not the original — conditioning "
              "on the run's proposed prefix is what makes the pass "
              "autoregressive rather than N independent judgments",
              "The wall stands once, and the Dead cannot pass it."
              in _block(w2, "B")
              and "Moreover the wall stands" not in _block(w2, "B"),
              _block(w2, "B"))
        check("F9 ...and the carried STATE is rendered into block C for "
              "the next window",
              "ledger: (empty)" in _block(w2, "C"), _block(w2, "C"))
        # Record window 2 against the window filter_run just opened.
        # Earlier-window proposals must SURVIVE: the pre-fix door keyed
        # origin_id on reply ordinal, so this call overwrote :1 with the
        # new edit and withdrew :2.
        w2_bounds = tuple(_loads(db.one(
            "SELECT metadata FROM filter_runs WHERE id = ?",
            (db.one("SELECT id FROM filter_runs WHERE manuscript_id = ? "
                    "AND filter = 'duplicate-words' AND status = 'active'",
                    (mid,))["id"],))["metadata"], {})["window"])
        run_stdin(ws, _filter_reply(fresh, w2_bounds, replaces={
            w2_bounds[0]: "A later-window rewrite that must not erase "
                          "earlier ones."}),
            "filter", "record", "02-wall.md")
        open_after_w2 = [dict(r) for r in db.all(
            "SELECT proposed_new, state, origin_id FROM doc_threads "
            "WHERE manuscript_id = ? AND origin_type = 'filter' AND "
            "state = 'proposed' ORDER BY origin_id", (mid,))]
        news = {r["proposed_new"] for r in open_after_w2}
        check("recording a LATER window keeps earlier windows' proposals "
              "(unit-keyed origin_id + window-scoped supersede) — without "
              "this, --window N silently destroyed staged triage work",
              "The wall stands once, and the Dead cannot pass it." in news
              and "A later-window rewrite that must not erase earlier ones."
              in news
              and len(open_after_w2) == 2,
              [(r["origin_id"], r["proposed_new"][:50]) for r in open_after_w2])
        # Fresh one-window run for the F41 settle assertion below.
        run(ws, "filter", "abandon", "duplicate-words", "02-wall.md")
        run(ws, "filter", "run", "duplicate-words", "02-wall.md",
            "--again", "--window", "5")
        run_stdin(ws, _filter_reply(fresh, (1, 5), replaces={
            2: "The wall stands once, and the Dead cannot pass it."}),
            "filter", "record", "02-wall.md")
        run(ws, "filter", "triage", "02-wall.md", "--accept", "1")
        out = run(ws, "filter", "resolve", "02-wall.md")
        check(f"F41 a run that covered 5 of {len(fresh)} units settles, "
              f"WARNS naming the uncovered range, and does not refuse — "
              f"completion is the author's call, exactly as an unwritten "
              f"beat and an unaccounted digest point are (§15.13)",
              f"units 6–{len(fresh)} of {len(fresh)} were never processed"
              in out and "ordinary open state" in out, out)

        # ---------------- F28-F36: the local transport ----------------
        run(ws, "filter", "run", "duplicate-words", "02-wall.md", "--again")
        now = _filter_units((ms / "02-wall.md").read_text())
        original = (ms / "02-wall.md").read_bytes()
        run_stdin(ws, _filter_reply(now, (1, len(now)), replaces={
            4: "The ledger counts, and nothing in it is an accusation.",
            8: "And so the wall is not stone, nor anything a hand could "
               "test by pushing.",
        }), "filter", "record", "02-wall.md")
        run(ws, "filter", "triage", "02-wall.md", "--accept", "1", "2")
        out = run(ws, "filter", "resolve", "02-wall.md", "--pause")
        marked = (ms / "02-wall.md").read_text()
        check("F28 --pause writes MARKED text: the file's bytes carry "
              "the <<old>>{{new}} form for every accepted edit, with the "
              "OLD half byte-equal to the unit it replaces",
              all(f"<<{now[n - 1]}>>{{{{" in marked for n in (4, 8))
              and marked.count("<<") == 2, marked[:400])
        check("F28 the form is a PLAIN paragraph in the file — visible in "
              "Obsidian, which is the whole argument for a local "
              "transport rather than a hidden marker",
              any(ln.startswith("<<") for ln in marked.split("\n")),
              marked[:400])
        from authorlm.revisions import read_manuscript_files as _rmf
        check("F29 while marked, read_manuscript_files returns the "
              "ORIGINAL text byte for byte — the canonicalization "
              "property, and the check that discriminates step 4",
              _rmf(ms)["02-wall.md"] == original.decode(),
              repr(_rmf(ms)["02-wall.md"][:120]))
        versions_before = db.one(
            "SELECT COUNT(*) AS n FROM manuscript_versions WHERE "
            "manuscript_id = ?", (mid,))["n"]
        run(ws, "collect")
        check("F30 while marked, collect records NO new version and no "
              "transition — a marker can never enter version history",
              db.one("SELECT COUNT(*) AS n FROM manuscript_versions WHERE "
                     "manuscript_id = ?", (mid,))["n"] == versions_before
              and not db.all("SELECT id FROM editorial_transitions WHERE "
                             "manuscript_id = ? AND detail LIKE '%<<%'",
                             (mid,)))
        check("F31b the pre-existing embed-line stripping is not "
              "displaced by the new pending-form stripping",
              "![](" not in _rmf(ms)["02-wall.md"])
        # F31 (a) and (b): both push guards, separately, no network.
        meta = _gd._mapping(db, manuscript)
        links = meta.setdefault("gdocs", {})
        links["_master_id"] = "doc-fake"
        links["02-wall.md"] = {"tab_id": "tab-1", "checked_out": False}
        _gd._save_mapping(db, manuscript, meta)
        raised_a = None
        try:
            _gd.push_doc(db, manuscript, "02-wall.md", service=None,
                         docs_service=None)
        except LookupError as err:
            raised_a = str(err)
        check("F31(a) with the run row present, forms_pending refuses the "
              "push and names 'filter resolve'",
              raised_a is not None and "filter pending forms" in raised_a
              and "filter resolve 02-wall.md" in raised_a, raised_a)
        written_ids = [r["id"] for r in db.all(
            "SELECT id FROM doc_threads WHERE manuscript_id = ? AND "
            "origin_type = 'filter' AND state = 'written'", (mid,))]
        for tid in written_ids:
            db.conn.execute("UPDATE doc_threads SET state = 'stale' "
                            "WHERE id = ?", (tid,))
        db.conn.commit()
        raised_b = None
        try:
            _gd.push_doc(db, manuscript, "02-wall.md", service=None,
                         docs_service=None)
        except LookupError as err:
            raised_b = str(err)
        check("F31(b) with the run's threads gone from under it, the "
              "BYTE-level is_marked check still refuses and names "
              "'filter unmark' — asserting only (a) would leave this "
              "guard free to delete with the suite green",
              raised_b is not None and "mid-settle" in raised_b
              and "filter unmark 02-wall.md" in raised_b, raised_b)
        for tid in written_ids:
            db.conn.execute("UPDATE doc_threads SET state = 'written' "
                            "WHERE id = ?", (tid,))
        db.conn.commit()
        # F32/F33: the author post-edits one {{new}} half during the
        # pause. F34: they delete the OTHER form outright, which is how a
        # rejection made in the file itself reads.
        edited = marked.replace(
            "{{The ledger counts, and nothing in it is an accusation.}}",
            "{{The ledger counts. Nothing in it accuses.}}")
        form8 = f"<<{now[7]}>>{{{{And so the wall is not stone, nor " \
                f"anything a hand could test by pushing.}}}}"
        check("the second form is in the file to be deleted (§14.8)",
              form8 in edited, edited[:600])
        (ms / "02-wall.md").write_text(edited.replace(form8, now[7]))
        out = run(ws, "filter", "resolve", "02-wall.md")
        final = (ms / "02-wall.md").read_text()
        check("F32 the finalized text carries the author's post-edit, not "
              "the proposal — their words win",
              "The ledger counts. Nothing in it accuses." in final
              and "<<" not in final and "{{" not in final, final[:300])
        check("F33 the post-edited half records a REVISED evidence row "
              "carrying the proposal→final pair",
              "modified" in out.lower() or "→" in out, out)
        revised = [dict(r) for r in db.all(
            "SELECT * FROM evidence WHERE manuscript_id = ? AND "
            "evidence_type = 'filter_edit' AND signal = 'revised'", (mid,))]
        check("F33 ...and it is a filter_edit row with no episode",
              revised and all(r["episode_id"] is None for r in revised),
              len(revised))
        declined = [dict(r) for r in db.all(
            "SELECT * FROM evidence WHERE manuscript_id = ? AND "
            "evidence_type = 'filter_edit' AND signal = 'declined'",
            (mid,))]
        check("F34 a form DELETED outright during the pause records a "
              "declined verdict — the author rejecting in the file is "
              "still a verdict, and it is still evidence",
              len(declined) == 1
              and declined[0]["episode_id"] is None, len(declined))
        check("F34 ...and that unit kept its original text",
              now[7] in final and "nor anything a hand" not in final,
              final[:600])

        # F35: unmark restores the original bytes.
        run(ws, "filter", "run", "duplicate-words", "02-wall.md", "--again")
        now2 = _filter_units((ms / "02-wall.md").read_text())
        before_unmark = (ms / "02-wall.md").read_bytes()
        run_stdin(ws, _filter_reply(now2, (1, len(now2)), replaces={
            2: "The wall stands, and the Dead cannot pass it."}),
            "filter", "record", "02-wall.md")
        run(ws, "filter", "triage", "02-wall.md", "--accept", "1")
        run(ws, "filter", "resolve", "02-wall.md", "--pause")
        out = run(ws, "filter", "rollback", "02-wall.md", expect_exit=True)
        check("filter rollback REFUSES while forms are out, naming both "
              "exits — a rollback mid-pause destroys post-edits the "
              "author may already have made, with nothing left to "
              "recover them from. The DB guard is what answers here, "
              "above the byte check, because in DOC mode the local file "
              "is clean and only the rows can know (§2.5); the byte "
              "guard stays separately reachable and is asserted at the "
              "seam, with the rows deleted from under it",
              "still out in the file on disk" in out
              and "filter resolve 02-wall.md" in out
              and "filter unmark 02-wall.md" in out, out)
        check("...and it left the marked bytes untouched",
              "<<" in (ms / "02-wall.md").read_text())
        out = run(ws, "filter", "unmark", "02-wall.md")
        check("F35 filter unmark restores the original text BYTE FOR BYTE "
              "and returns the forms to ACCEPTED — it undoes the marking, "
              "never the triage; the author chooses whether the verdicts "
              "then apply, reopen, or go",
              (ms / "02-wall.md").read_bytes() == before_unmark
              and "1 form(s) returned to 'accepted'" in out, out)
        check("...so the verdicts really did survive the recovery",
              db.one("SELECT COUNT(*) AS n FROM doc_threads WHERE "
                     "manuscript_id = ? AND origin_type = 'filter' AND "
                     "state = 'accepted'", (mid,))["n"] == 1)

        # F36: rollback restores the pin and keeps the verdicts.
        ev_before = db.one(
            "SELECT COUNT(*) AS n FROM evidence WHERE manuscript_id = ? "
            "AND evidence_type = 'filter_edit'", (mid,))["n"]
        run(ws, "filter", "resolve", "02-wall.md")
        changed = (ms / "02-wall.md").read_bytes()
        pin = dict(db.one(
            "SELECT source_version_id FROM filter_runs WHERE "
            "manuscript_id = ? AND file = '02-wall.md' "
            "ORDER BY created_at DESC LIMIT 1", (mid,)))
        pinned_text = _loads(db.one(
            "SELECT files FROM manuscript_versions WHERE id = ?",
            (pin["source_version_id"],))["files"], {})["02-wall.md"]
        out = run(ws, "filter", "rollback", "02-wall.md")
        check("F36 filter rollback restores the run's PINNED version, "
              "byte for byte",
              (ms / "02-wall.md").read_bytes() != changed
              and (ms / "02-wall.md").read_text() == pinned_text,
              (ms / "02-wall.md").read_text()[:200])
        check("F36 ...and the verdicts STAY as evidence: putting text "
              "back does not un-record what the author decided",
              db.one("SELECT COUNT(*) AS n FROM evidence WHERE "
                     "manuscript_id = ? AND evidence_type = 'filter_edit'",
                     (mid,))["n"] >= ev_before, ev_before)

        # ---------------- F43: the prompt-fault warning ---------------
        run(ws, "filter", "run", "duplicate-words", "02-wall.md", "--again")
        many = _filter_units((ms / "02-wall.md").read_text())
        out = run_stdin(ws, _filter_reply(many, (1, len(many)), replaces={
            n: f"A rewritten unit {n} that the filter insisted on."
            for n in range(2, len(many) + 1)}),
            "filter", "record", "02-wall.md")
        check("F43 proposals on nearly every unit print the prompt-fault "
              "warning — a filter that fires everywhere is almost always "
              "the PROMPT, not an essay that bad — and name the remedy",
              "PROMPT fault" in out and "filter show duplicate-words" in out
              and "Nothing is blocked" in out, out)
        staged_n = api_filter_edit_count(db, mid, "02-wall.md")
        run(ws, "filter", "triage", "02-wall.md",
            "--accept", "1",
            "--reject", *[str(i) for i in range(2, staged_n + 1)],
            "--reason", "all of these are the filter overreaching")
        out = run(ws, "filter", "resolve", "02-wall.md")
        check("F43 ...and it SETTLES anyway: the warning blocks nothing, "
              "and the remedy (edit the artifact) is the author's",
              "Applied: 1 change(s) made final" in out, out)

        # ---------------- F45-F50: refusals from doctrine -------------
        out = run(ws, "filter", "run", "duplicate-words", "01-open.md",
                  "--window", "2")
        check("a second essay runs independently — one file per run",
              "01-open.md" in out, out[:200])
        out = run(ws, "filter", "run", "metaphor", "01-open.md",
                  expect_exit=True)
        check("a global filter with no run at all refuses before creating "
              "one — a run whose registry was never written has not "
              "stalled, it has not begun",
              "filter prelude metaphor 01-open.md" in out, out)
        check("...and it left no half-made run row behind",
              db.one("SELECT COUNT(*) AS n FROM filter_runs WHERE "
                     "manuscript_id = ? AND filter = 'metaphor' AND "
                     "file = '01-open.md'", (mid,))["n"] == 0)
        out = run_stdin(ws, json.dumps(
            {"registry": "choice — the primal act."}),
            "filter", "prelude", "metaphor", "01-open.md")
        check("F50 a SECOND filter on the same file WARNS by name, names "
              "the collision it can cause, and does NOT refuse — "
              "refusing would be the machine deciding the author's work "
              "order",
              "duplicate-words already has an active run" in out
              and "drift check" in out and "Registry frozen" in out, out)
        out = run(ws, "filter", "run", "metaphor", "01-open.md")
        check("F50 ...and both runs are live on the one file",
              db.one("SELECT COUNT(*) AS n FROM filter_runs WHERE "
                     "manuscript_id = ? AND file = '01-open.md' AND "
                     "status = 'active'", (mid,))["n"] == 2)
        out = run(ws, "filter", "run", "duplicate-words", "01-open.md",
                  "--again", expect_exit=True)
        check("F50 --again while a run of the SAME filter is active is "
              "refused, naming both exits",
              "already active" in out and "filter resolve" in out
              and "filter abandon" in out, out)
        check("F50 ...and there is still exactly ONE active run of that "
              "filter on that file",
              db.one("SELECT COUNT(*) AS n FROM filter_runs WHERE "
                     "manuscript_id = ? AND filter = 'duplicate-words' AND "
                     "file = '01-open.md' AND status = 'active'",
                     (mid,))["n"] == 1)
        run(ws, "filter", "abandon", "metaphor", "01-open.md")
        run(ws, "filter", "abandon", "duplicate-words", "01-open.md")

        # F49: the native path's refusal, and the chat path succeeding on
        # the SAME config — the §15.10 reorder lesson, built in from the
        # start rather than discovered.
        calls_before = StubLLMHandler.REQUESTS
        out = run(ws, "filter", "run", "duplicate-words", "01-open.md",
                  "--native", expect_exit=True)
        check("F49 --native with [filtering] absent refuses and names the "
              "chat flow",
              "no [filtering] section" in out
              and "makes no model call at all" in out, out)
        check("F49 ...and made no call in the process",
              StubLLMHandler.REQUESTS == calls_before,
              f"{calls_before} -> {StubLLMHandler.REQUESTS}")
        out = run(ws, "filter", "run", "duplicate-words", "01-open.md")
        check("F49 the SAME config runs fine with no flag — the payload "
              "is never gated on the billed path's configuration",
              "───── block S — " in out, out[:300])
        run(ws, "filter", "abandon", "duplicate-words", "01-open.md")

        # F47/F48: the checkout gate, on run AND on record.
        meta = _gd._mapping(db, manuscript)
        meta["gdocs"]["02-wall.md"] = {"tab_id": "tab-1",
                                       "checked_out": True}
        _gd._save_mapping(db, manuscript, meta)
        out = run(ws, "filter", "run", "duplicate-words", "02-wall.md",
                  "--again", expect_exit=True)
        check("F47 filter run on a checked-out file refuses, naming "
              "doc pull", "doc pull 02-wall.md" in out, out)
        meta["gdocs"]["02-wall.md"]["checked_out"] = False
        _gd._save_mapping(db, manuscript, meta)
        run(ws, "filter", "run", "duplicate-words", "02-wall.md", "--again")
        now3 = _filter_units((ms / "02-wall.md").read_text())
        meta["gdocs"]["02-wall.md"]["checked_out"] = True
        _gd._save_mapping(db, manuscript, meta)
        out = run_stdin(ws, _filter_reply(now3, (1, len(now3))),
                        "filter", "record", "02-wall.md", expect_exit=True)
        check("F48 filter record on a checked-out file refuses too — the "
              "push→pull window is closed on BOTH halves of the turn",
              "doc pull 02-wall.md" in out, out)
        meta["gdocs"]["02-wall.md"]["checked_out"] = False
        _gd._save_mapping(db, manuscript, meta)

        # F20: pin drift between assembly and registration.
        (ms / "02-wall.md").write_text(
            (ms / "02-wall.md").read_text()
            + "\nA paragraph added in another chat, after the run pinned "
              "the essay.\n")
        run(ws, "collect")
        out = run_stdin(ws, _filter_reply(now3, (1, len(now3))),
                        "filter", "record", "02-wall.md", expect_exit=True)
        check("F20 a reply recorded after the file changed under the run "
              "is refused, naming --again",
              "changed since this run pinned it" in out and "--again" in out,
              out)
        run(ws, "filter", "abandon", "duplicate-words", "02-wall.md")

        # F45/F46: an active writeup owns the file.
        run(ws, "summarize", "rebuild", "--all")
        run(ws, "style", "guide", "House")
        run(ws, "style", "attach", "02-wall.md", "House")
        out = run(ws, "intent", "declare", "Rewrite the wall essay")
        intent_id = out.split("[")[1].split("]")[0]
        run_stdin(ws, "The essay is about the wall.", "write", "start",
                  "02-wall.md", "--intent", intent_id)
        out = run(ws, "filter", "run", "duplicate-words", "02-wall.md",
                  "--again", expect_exit=True)
        check("F45/F46 filter run on a file an active writeup holds "
              "refuses, naming the writeup's BOTH exits — what is on disk "
              "is a placeholder, not the essay",
              "being rewritten right now" in out
              and "write complete" in out and "write abandon" in out, out)
        run(ws, "write", "abandon", "--writeup", "02-wall.md")

        # F44: a surviving marker warns and does not block.
        from authorlm.api import MARKER
        text = (ms / "02-wall.md").read_text()
        (ms / "02-wall.md").write_text(text.replace(
            "# The Wall", f"# The Wall\n\n{MARKER}"))
        out = run(ws, "filter", "run", "duplicate-words", "02-wall.md",
                  "--again")
        check("F44 a marker surviving inside a hand-edited file WARNS and "
              "does not block", "still carries the mid-rewrite marker"
              in out and "───── block S — " in out, out[:600])
        run(ws, "filter", "abandon", "duplicate-words", "02-wall.md")

        # --- filter status: the class drift and the orphaned mark ------
        run(ws, "filter", "run", "duplicate-words", "01-open.md")
        run_stdin(ws, SEQ_FILTER.replace('class = "sequential"',
                                         'class = "global"'),
                  "filter", "add", "duplicate-words")
        out = run(ws, "filter", "status", "01-open.md")
        check("filter status reports a run whose ARTIFACT class changed "
              "under it, and says the run will finish as what it started "
              "— the class is frozen so a mid-run edit cannot change a "
              "live run's mechanics",
              "the artifact's class is now 'global'" in out
              and "will finish as one" in out, out)
        run_stdin(ws, SEQ_FILTER, "filter", "add", "duplicate-words")
        run(ws, "filter", "abandon", "duplicate-words", "01-open.md")

        # A crash between "compose" and "write threads" leaves a marked
        # file with no written rows. The bytes fully describe that state,
        # so `filter status` can find it and `filter unmark` can undo it.
        orphan = (ms / "01-open.md")
        kept = orphan.read_bytes()
        first = _filter_units(orphan.read_text())[0]
        orphan.write_text(orphan.read_text().replace(
            first, f"<<{first}>>{{{{An orphaned proposal.}}}}"))
        out = run(ws, "filter", "status", "01-open.md")
        check("filter status detects an ORPHANED mark — pending forms on "
              "disk with no matching staged edit — and names the two-line "
              "recovery",
              "carries pending forms on disk with no matching staged edit"
              in out and "filter unmark 01-open.md" in out, out)
        run(ws, "filter", "unmark", "01-open.md")
        check("filter unmark recovers it byte for byte, with no run row "
              "and no thread to consult — the state was fully described "
              "by the bytes",
              orphan.read_bytes() == kept, orphan.read_text()[:200])
    finally:
        server.shutdown()


# ==================================================================
# Scenario PR — protected terms and the pronunciation dictionary
# (docs/autoregressive-writing-design.md §15.22)
#
# Zero model calls: every reply the pass needs travels on stdin, and the
# stub's own counter is asserted rather than assumed.
# ==================================================================

PRON_ESSAY = "\n\n".join([
    "# Terms of art",
    "The Chid is what looks out. It is not the Field, and the field it "
    "stands in is not the Field of Choice either.",
    "Nāgārjuna wrote that anattā is not a thing a person has. Noether’s "
    "Theorem is a different kind of statement altogether.",
    "The Bṛhadāraṇyaka says one thing and the Chid says another. Between "
    "them is the whole of the difficulty.",
    "A ledger counts. A ledger counts again, and nothing in it is an "
    "accusation, which is the point of keeping one. Some transliterate "
    "the word as chid, without its capital.",
]) + "\n"

PRON_FILTER = (
    '---\nclass = "sequential"\n'
    'prelude = "pronunciations"\n'
    'state = "the voice note: what has already been heard aloud"\n---\n\n'
    "# Audio friendly\n\n"
    "Read each unit aloud in your head and flag what a listener cannot "
    "follow.\n")

DUP_FILTER = (
    '---\nclass = "sequential"\n'
    'state = "a ledger of every word already flagged as repeated"\n---\n\n'
    "# Duplicate words\n\n"
    "Flag a word used again too soon.\n")

GLOBAL_PRON_FILTER = (
    '---\nclass = "global"\n'
    'state = "not used"\n---\n\n'
    "# Metaphor consistency\n\nReturn a registry of the motif families.\n")

PRON_SEED_ROWS = (
    "# Pronunciations\n\n"
    "How the terms in this book are said aloud.\n\n"
    "| Term | Say it | Note |\n"
    "| --- | --- | --- |\n"
    "| anattā | uh-NUT-taa | Pali |\n"
    "| Nāgārjuna | naa-GAAR-ju-na |  |\n"
    "| Ereignis | er-EYE-gnis | German; no concept node carries it |\n")


def _pron_section(payload_block: str, header_start: str) -> str:
    """One named section out of a printed block, up to the next section."""
    body = payload_block.split(header_start, 1)[1]
    for nxt in ("\nTHE BOOK'S LEXICON", "\nPRONUNCIATION DICTIONARY",
                "\nPRIOR RUNS", "\nTHE ESSAY —", "\nHARD TERMS",
                "\nOUTPUT CONTRACT"):
        if nxt in body:
            body = body.split(nxt, 1)[0]
    return body


def scenario_pronunciations(root: Path) -> None:
    print("Scenario PR — protected terms and the pronunciation dictionary "
          "(§15.22)")
    import subprocess

    from authorlm import api as _api
    from authorlm import filtering as _fg
    from authorlm import pronunciations as _pron
    from authorlm.db import Database as _DB, loads as _loads

    server = http.server.HTTPServer(("127.0.0.1", 0), StubLLMHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ws = root / "pron"
        ms = ws / "manuscript"
        write(ms / "01-open.md", CH1)
        write(ms / "02-terms.md", PRON_ESSAY)
        write(ms / "toc.toml",
              '[[chapter]]\nfile = "01-open.md"\n\n'
              '[[chapter]]\nfile = "02-terms.md"\n')
        _draft_config(ws, server.server_port)
        run(ws, "init", "--name", "book", "--path", str(ms))
        run(ws, "session", "start")
        db = _DB(ws / ".authorlm" / "authorlm.db")
        manuscript = _api.get_manuscript(db)
        mid = manuscript["id"]
        run(ws, "collect")
        dict_path = ms / _pron.FILENAME

        # ---------- the fixture graph: every kind, every status --------
        _api.add_concept(db, manuscript, "Chid", kind="concept",
                         notes="what looks out")
        _api.add_concept(db, manuscript, "Field of Choice", kind="concept",
                         notes="where choosing happens")
        _api.alias_concept(db, manuscript, "Field of Choice",
                           ["the Field"])
        _api.add_concept(db, manuscript, "Field", kind="concept",
                         notes="the ordinary word, capitalized")
        _api.add_concept(db, manuscript, "anattā", kind="concept",
                         notes="no self")
        _api.add_concept(db, manuscript, "Nāgārjuna",
                         kind="historical_reference", notes="the man")
        _api.add_concept(db, manuscript, "Bṛhadāraṇyaka",
                         kind="historical_reference", notes="the upaniṣad")
        _api.add_concept(db, manuscript, "Noether’s Theorem",
                         kind="mathematical_construct", notes="a symmetry")
        _api.add_concept(db, manuscript, "The Ledger", kind="metaphor",
                         notes="the count a life can read")
        for kind, name in (("objection", "Objection to Man as beast"),
                           ("example", "The Herdsman’s lantern"),
                           ("question", "Question about Supreme God"),
                           ("syllogism", "Syllogism of the wall")):
            _api.add_concept(db, manuscript, name, kind=kind, notes="a move")
        _api.add_concept(db, manuscript, "Abandoned Wording", kind="concept",
                         notes="deliberately given up")
        _api.retire_concept(db, manuscript, "Abandoned Wording")
        _api.add_concept(db, manuscript, "Chid-consciousness", kind="concept",
                         notes="a duplicate of Chid")
        _api.merge_concepts(db, manuscript, "Chid", "Chid-consciousness")
        run(ws, "collect")

        out = run_stdin(ws, PRON_FILTER, "filter", "add", "audio-friendly")
        check("the `prelude` front-matter key is accepted on a SEQUENTIAL "
              "filter and reported, so the author sees what they ratified",
              "prelude = pronunciations" in out, out)
        run_stdin(ws, DUP_FILTER, "filter", "add", "duplicate-words")
        run_stdin(ws, GLOBAL_PRON_FILTER, "filter", "add", "metaphor")

        out = run_stdin(ws, GLOBAL_PRON_FILTER.replace(
            'state = "not used"',
            'prelude = "pronunciations"\nstate = "not used"'),
            "filter", "add", "both", expect_exit=True)
        check("a GLOBAL filter declaring a prelude is refused — its "
              "prelude is the registry, and two outputs for one call is "
              "two preludes",
              "two outputs for one call is two preludes" in out, out)
        out = run_stdin(ws, PRON_FILTER.replace(
            '"pronunciations"', '"phonetics"'),
            "filter", "add", "typo-prelude", expect_exit=True)
        check("an unknown prelude value is refused BY NAME, with the one "
              "legal value spelled out",
              "'phonetics'" in out and "'pronunciations'" in out, out)
        # run_stdin, not run: `filter prelude` reads its reply off stdin
        # before it dispatches, so a harness leaving stdin an open pipe
        # would block here — the same idiom the F11 checks use.
        out = run_stdin(ws, "", "filter", "prelude", "duplicate-words",
                        "02-terms.md", expect_exit=True)
        check("`filter prelude` on a sequential filter that declares NO "
              "prelude refuses and names the front-matter key that would "
              "give it one",
              "declares no prelude" in out
              and 'prelude = "pronunciations"' in out, out)

        # ================= T2 — the derivation ======================
        out1 = run(ws, "filter", "run", "duplicate-words", "02-terms.md")
        block_a = _block(out1, "A")
        in_essay = _pron_section(block_a, "IN THIS ESSAY\n")
        lexicon = _pron_section(block_a, "allusion)\n")
        check("T2 the four VOCABULARY kinds are protected — concept, "
              "metaphor, mathematical_construct, historical_reference",
              all(f"- {n}" in lexicon for n in
                  ("Chid", "The Ledger", "Noether’s Theorem", "Nāgārjuna")),
              lexicon)
        check("T2 the four LABEL kinds are NOT — their names are "
              "SENTENCES, and a list that protects the ordinary English "
              "inside them protects nothing",
              not any(n in block_a for n in
                      ("Objection to Man as beast", "Herdsman’s lantern",
                       "Question about Supreme God", "Syllogism of the wall")),
              block_a)
        check("T2 a RETIRED name is not protected — it is vocabulary the "
              "author deliberately abandoned, and freezing it would freeze "
              "exactly the wording a recast should be free to move",
              "Abandoned Wording" not in block_a, block_a)
        check("T2 a DECLARED concept IS protected: the author ratified the "
              "name, and realization is a scan that may not have caught up",
              "- Bṛhadāraṇyaka" in lexicon, lexicon)
        check("T2 aliases ride on their node's line after ' · ', never on "
              "lines of their own — the grouping is information the "
              "homophone rule can use",
              "- Field of Choice · the Field" in in_essay, in_essay)
        check("T2 a synonym that survived retirement through "
              "merge_concepts is protected THROUGH THE CANONICAL",
              "Chid · Chid-consciousness" in in_essay
              or "- Chid" in in_essay and "Chid-consciousness" in lexicon,
              in_essay + "|" + lexicon)

        # ================= T3 — the rule is prose ===================
        check("T3 a single-word LOWER-CASE name is in the list — a rule "
              "that only protected capitalized names would drop every "
              "borrowed term from the protection they most need",
              "- anattā" in in_essay, in_essay)
        check("T3 and a single-word CAPITALIZED name is too",
              "- Field\n" in in_essay, in_essay)
        check("T3 the capitalization rule appears in the header exactly "
              "ONCE, as prose",
              block_a.count("is ordinary English") == 1, block_a)
        check("T3 and NOWHERE as a per-line annotation — the list is "
              "rendered unmarked",
              "(capitalized only)" not in block_a
              and "[capitalized]" not in block_a, block_a)

        # ================= T1 — byte-stable protection ===============
        out2 = run(ws, "filter", "run", "duplicate-words", "02-terms.md")
        check("T1 two assemblies on identical stored state produce a "
              "BYTE-IDENTICAL block A",
              _block_hash(out1, "A") == _block_hash(out2, "A"),
              f"{_block_hash(out1, 'A')} vs {_block_hash(out2, 'A')}")
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = "12345"
        env["AUTHORLM_CONFIG"] = str(ws / ".authorlm" / "config.toml")
        env["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
        env["AUTHORLM_CLIENT"] = "none"
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
        proc = subprocess.run(
            [sys.executable,
             str(Path(__file__).resolve().parent.parent / "main.py"),
             "--workspace", str(ws),
             "filter", "run", "duplicate-words", "02-terms.md"],
            env=env, capture_output=True, text=True)
        check("T1 ...in a SEPARATE PROCESS with a different "
              "PYTHONHASHSEED too — the derivation walks sets, and a "
              "sorted() that was forgotten would show up here and "
              "nowhere else",
              proc.returncode == 0
              and _block_hash(proc.stdout, "A") == _block_hash(out1, "A"),
              proc.stdout[-800:] + proc.stderr[-400:])

        # ================= T4/T5 — the dictionary in block A =========
        check("T4 an absent dictionary renders (none) rather than the "
              "section disappearing — a section that comes and goes is a "
              "shape change, and shape changes are cache invalidations",
              "PRONUNCIATION DICTIONARY (settled by the author" in block_a
              and _pron_section(block_a, "the dictionary IS the fix)\n")
              .strip() == "(none)",
              _pron_section(block_a, "the dictionary IS the fix)\n"))
        dict_path.write_text(PRON_SEED_ROWS, encoding="utf-8")
        out3 = run(ws, "filter", "run", "duplicate-words", "02-terms.md")
        block_a3 = _block(out3, "A")
        check("T5 adding a dictionary row changes block A and NOTHING "
              "else — an honest invalidation, because the suppression set "
              "genuinely changed",
              _block_hash(out3, "A") != _block_hash(out1, "A")
              and _block_hash(out3, "S") == _block_hash(out1, "S")
              and _block_hash(out3, "B") == _block_hash(out1, "B")
              and _block_hash(out3, "C") == _block_hash(out1, "C"),
              f"S {_block_hash(out3, 'S')}/{_block_hash(out1, 'S')} "
              f"A {_block_hash(out3, 'A')}/{_block_hash(out1, 'A')}")
        rendered = _pron_section(block_a3, "the dictionary IS the fix)\n")
        check("T4 rows render sorted by term key, note in parentheses, "
              "omitted when empty",
              rendered.strip().splitlines() == [
                  "- anattā — uh-NUT-taa (Pali)",
                  "- Ereignis — er-EYE-gnis (German; no concept node "
                  "carries it)",
                  "- Nāgārjuna — naa-GAAR-ju-na"], repr(rendered))
        check("T4 a dictionary term with NO concept node is in the "
              "protected set — a term the author ruled on aloud is by "
              "construction a term of art",
              "- Ereignis" in _pron_section(block_a3, "allusion)\n"),
              _pron_section(block_a3, "allusion)\n"))
        run_stdin(ws, json.dumps({"registry": "ledger — the count."}),
                  "filter", "prelude", "metaphor", "02-terms.md")
        gout = run(ws, "filter", "run", "metaphor", "02-terms.md")
        check("T4 a GLOBAL filter's block A carries both sections too — a "
              "motif family's own name is a protected term, and a "
              "`replace` that swaps it for a synonym is precisely the harm",
              "PROTECTED TERMS" in _block(gout, "A")
              and "- anattā — uh-NUT-taa (Pali)" in _block(gout, "A"),
              _block(gout, "A")[:400])
        run(ws, "filter", "abandon", "metaphor", "02-terms.md")

        # ================= T6 — protected_loss =======================
        protected = ["Field", "Chid", "anattā", "Field of Choice"]
        check("T6 'Field' → 'the field of choosing' IS a loss: the "
              "capitalization rule is mention_pattern's, so the "
              "lower-case survivor does not count as the term",
              _fg.protected_loss("The Field is where it happens.",
                                 "The field of choosing is where it "
                                 "happens.", protected) == ["Field"], "")
        check("T6 'field' → 'meadow' is NOT a loss — the ordinary word "
              "was never the term",
              _fg.protected_loss("a field of grass", "a meadow of grass",
                                 protected) == [], "")
        check("T6 dropping ONE of two mentions is NOT a loss — which is "
              "exactly why this warns rather than refusing",
              _fg.protected_loss("The Chid and the Chid again.",
                                 "The Chid, once.", protected) == [], "")
        before_calls = StubLLMHandler.REQUESTS
        _fg.protected_loss("anattā here", "nothing here", protected)
        check("T6 the function is pure and makes no model call",
              StubLLMHandler.REQUESTS == before_calls, "")

        # ================= E1 — protection reaches the door ==========
        units = _filter_units(PRON_ESSAY)
        out = run(ws, "filter", "run", "duplicate-words", "02-terms.md",
                  "--window", "5")
        check("E1 the payload carries the essay's own terms under "
              "PROTECTED TERMS · IN THIS ESSAY",
              "- Chid" in _pron_section(_block(out, "A"), "IN THIS ESSAY\n"),
              _block(out, "A"))
        window = _loads(db.one(
            "SELECT metadata FROM filter_runs WHERE manuscript_id = ? AND "
            "filter = 'duplicate-words' AND status = 'active'",
            (mid,))["metadata"], {})["window"]
        losing = _filter_reply(
            units, tuple(window),
            replaces={2: "The mind is what looks out. It is not the Field, "
                         "and the field it stands in is not the Field of "
                         "Choice either."})
        rec = run_stdin(ws, losing, "filter", "record", "02-terms.md")
        check("E1 filter record WARNS by name when a replacement drops a "
              "protected term the original carried",
              "'Chid'" in rec and "PROTECTED TERMS" in rec, rec)
        check("E1 ...and STAGES it anyway — the harness supplies the law, "
              "it is not the editor",
              api_filter_edit_count(db, mid, "02-terms.md") == 1
              and "Staged anyway" in rec, rec)
        run(ws, "filter", "abandon", "duplicate-words", "02-terms.md")
        run(ws, "filter", "unmark", "02-terms.md") \
            if "<<" in (ms / "02-terms.md").read_text() else None
        for row in db.all("SELECT id FROM doc_threads WHERE "
                          "manuscript_id = ? AND origin_type = 'filter'",
                          (mid,)):
            db.update("doc_threads", row["id"], {"state": "withdrawn"})
        out = run(ws, "filter", "run", "duplicate-words", "02-terms.md",
                  "--window", "5")
        keeping = _filter_reply(units, tuple(window))
        rec2 = run_stdin(ws, keeping, "filter", "record", "02-terms.md")
        check("E1 a reply that keeps the term produces NO warning",
              "PROTECTED TERMS" not in rec2, rec2)
        run(ws, "filter", "abandon", "duplicate-words", "02-terms.md")

        # ================= T7 — the prelude reply's refusals ==========
        def pron_rows() -> int:
            return db.one("SELECT COUNT(*) AS n FROM knowledge_proposals "
                          "WHERE manuscript_id = ? AND kind = "
                          "'pronunciation'", (mid,))["n"]

        def refuse(label: str, body, expect: str) -> None:
            before = pron_rows()
            out = run_stdin(ws, body if isinstance(body, str)
                            else json.dumps(body),
                            "filter", "prelude", "audio-friendly",
                            "02-terms.md", expect_exit=True)
            check(f"T7 {label}", expect in out, out)
            check(f"T7 ...and nothing was created ({label})",
                  pron_rows() == before, f"{before} -> {pron_rows()}")

        refuse("a reply that is not a JSON object",
               "[]", "not a JSON object")
        refuse("a reply with no `pronunciations` list",
               {"terms": []}, "must return a `pronunciations` list")
        refuse("a term that is not in the essay verbatim — the anchoring "
               "law, applied to the prelude",
               {"pronunciations": [{"term": "Kierkegaard", "say": "KEER"}]},
               "does not appear in the essay verbatim")
        refuse("an entry with no pronunciation",
               {"pronunciations": [{"term": "anattā", "say": ""}]},
               "has no pronunciation")
        refuse("a reserved marker in any field",
               {"pronunciations": [{"term": "Chid", "say": "chit",
                                    "note": "see <<this>>"}]},
               "reserved grammar")
        refuse("a newline in any field — a row is ONE LINE",
               {"pronunciations": [{"term": "Chid", "say": "chit\nchit"}]},
               "carries a newline")
        refuse("a PIPE in any field — the table's own column separator, "
               "which no escape can carry across the Doc bridge",
               {"pronunciations": [{"term": "Chid", "say": "chit|kid"}]},
               "carries a '|'")
        refuse("two entries that are the same term under key()",
               {"pronunciations": [{"term": "Chid", "say": "chit"},
                                   {"term": "chid", "say": "kid"}]},
               "are the same term")
        refuse("a term the dictionary already carries — additions only",
               {"pronunciations": [{"term": "anattā", "say": "AN-at-ta"}]},
               "already in PRONUNCIATION DICTIONARY")
        refuse("more than the cap in one reply — refused, never truncated",
               {"pronunciations": [{"term": "Chid", "say": f"c{n}"}
                                   for n in range(61)]},
               "over the 60 cap")

        # ================= E2 — the loop closes ======================
        before_calls = StubLLMHandler.REQUESTS
        pre = run_stdin(ws, "", "filter", "prelude", "audio-friendly",
                        "02-terms.md")
        check("E2 the prelude payload prints with no call made, and its "
              "block C is the pronunciation contract rather than the "
              "registry's",
              "no call made" in pre
              and "HARD TERMS FOUND IN THIS ESSAY" in _block(pre, "C")
              and '{"pronunciations"' in _block(pre, "C")
              and "registry" not in _block(pre, "C"), _block(pre, "C"))
        hard = _pron_section(_block(pre, "C"),
                             "already has it)\n")
        check("E2 HARD TERMS is F1 and only F1: a non-ASCII LETTER, never "
              "a curly apostrophe — Bṛhadāraṇyaka is in, Noether’s "
              "Theorem is out, and a term the dictionary already carries "
              "is out too",
              hard.strip().splitlines() == ["- Bṛhadāraṇyaka"],
              repr(hard))
        check("E2 the prelude makes ZERO model calls on the chat path",
              StubLLMHandler.REQUESTS == before_calls, "")
        out = run_stdin(ws, json.dumps({"pronunciations": [
            {"term": "Bṛhadāraṇyaka", "say": "bri-ha-DAA-ran-ya-ka",
             "note": "Sanskrit"}]}),
            "filter", "prelude", "audio-friendly", "02-terms.md")
        check("E2 one proposal is filed on the ORDINARY queue",
              "1 pronunciation(s) proposed" in out
              and "Bṛhadāraṇyaka" in out, out)
        listed = run(ws, "proposal", "list")
        check("E2 it reads on the queue with no new surface — "
              "kind-agnostic list, describe's own summary",
              "pronounce 'Bṛhadāraṇyaka' — bri-ha-DAA-ran-ya-ka" in listed,
              listed)
        pid = db.one("SELECT id FROM knowledge_proposals WHERE "
                     "manuscript_id = ? AND kind = 'pronunciation' AND "
                     "state = 'open'", (mid,))["id"]
        acc = run(ws, "proposal", "accept", pid)
        check("E2 accepting writes the row into pronunciations.md — the "
              "adopt path, deterministically, never the model",
              "written into pronunciations.md" in acc
              and {"term": "Bṛhadāraṇyaka", "say": "bri-ha-DAA-ran-ya-ka",
                   "note": "Sanskrit"}
              in _pron.parse(dict_path.read_text())[0], acc)
        check("E2 and it rewrote no line it did not add",
              dict_path.read_text().startswith(PRON_SEED_ROWS),
              dict_path.read_text())
        pre2 = run_stdin(ws, "", "filter", "prelude", "audio-friendly",
                         "02-terms.md", "--replace")
        hard2 = _pron_section(_block(pre2, "C"), "already has it)\n")
        check("E2 THE LOOP CLOSES: re-running the prelude flags nothing — "
              "the term is settled, and the dictionary is both this "
              "pass's output channel and its suppression list",
              hard2.strip() == "(none)", repr(hard2))
        check("E2 ...and the term is now in block A's PRONUNCIATION "
              "DICTIONARY, told to the filter as settled",
              "- Bṛhadāraṇyaka — bri-ha-DAA-ran-ya-ka (Sanskrit)"
              in _block(pre2, "A"), _block(pre2, "A"))
        aud = run(ws, "filter", "run", "audio-friendly", "02-terms.md")
        check("E2 and `filter run audio-friendly` judges its units against "
              "a payload that carries it",
              "- Bṛhadāraṇyaka — bri-ha-DAA-ran-ya-ka (Sanskrit)"
              in _block(aud, "A"), _block(aud, "A"))
        check("E2 a second prelude ALSO re-proposes nothing rather than "
              "asking again",
              "0 pronunciation(s) proposed" in run_stdin(
                  ws, json.dumps({"pronunciations": []}), "filter",
                  "prelude", "audio-friendly", "02-terms.md", "--replace"),
              "")

        # ================= E8 — the prelude's own idempotency ========
        out = run_stdin(ws, json.dumps({"pronunciations": []}), "filter",
                        "prelude", "audio-friendly", "02-terms.md",
                        expect_exit=True)
        check("E8 a second prelude on the same active run REFUSES without "
              "--replace, mirroring the registry's refusal",
              "already ran its pronunciation prelude" in out
              and "--replace" in out, out)

        # ================= E4 — rejected never re-proposed ===========
        out = run_stdin(ws, json.dumps({"pronunciations": [
            {"term": "Chid", "say": "chit", "note": "Sanskrit"}]}),
            "filter", "prelude", "audio-friendly", "02-terms.md",
            "--replace")
        check("E4 a term above the F1 floor can still be proposed — the "
              "list is a floor, not a ceiling",
              "1 pronunciation(s) proposed" in out, out)
        pid = db.one("SELECT id FROM knowledge_proposals WHERE "
                     "manuscript_id = ? AND kind = 'pronunciation' AND "
                     "state = 'open'", (mid,))["id"]
        run(ws, "proposal", "dismiss", pid, "--why",
            "everyone in this book's audience can say Chid")
        out = run_stdin(ws, json.dumps({"pronunciations": [
            {"term": "Chid", "say": "SHEED", "note": "a different guess"}]}),
            "filter", "prelude", "audio-friendly", "02-terms.md",
            "--replace")
        check("E4 a DIFFERENT respelling of a dismissed term is not "
              "re-proposed — the guard is on the TERM, not the spelling, "
              "which the content hash alone would have let straight past",
              "0 pronunciation(s) proposed, 1 suppressed" in out
              and "already settled, not re-asked: Chid" in out, out)
        check("E4 ...and no second row was written",
              db.one("SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
                     "manuscript_id = ? AND kind = 'pronunciation' AND "
                     "target = 'chid'", (mid,))["n"] == 1, "")
        from authorlm import proposals as _prop
        check("E4 ...and the guard that does it is DETERMINISTIC and "
              "kind-specific: a prior row in ANY state settles the term. "
              "The near-duplicate firewall would also catch this pair, "
              "but only because its dedupe text is the term alone — it "
              "is a similarity THRESHOLD, and this is not",
              _prop._pronunciation_settled(db, mid, _pron.key("Chid"))
              and _prop._pronunciation_settled(db, mid, _pron.key("chid"))
              and not _prop._pronunciation_settled(
                  db, mid, _pron.key("Līlā")), "")
        ev = db.one(
            "SELECT metadata FROM evidence WHERE manuscript_id = ? AND "
            "evidence_type = 'proposal_review' AND signal = 'dismissed' "
            "ORDER BY created_at DESC LIMIT 1", (mid,))
        check("E4 the dismissal reason is on the evidence stream VERBATIM",
              _loads(ev["metadata"], {}).get("explanation")
              == "everyone in this book's audience can say Chid",
              str(ev["metadata"]))

        # ================= E3 — immutability across every path =======
        # The Sponsor asked that no pass overwrite a validated row. It is
        # not implemented as a rule: it is a CONSEQUENCE of there being
        # exactly one writer (`proposals.adopt`). Asserted by outcome,
        # against the file's BYTES, after every verb of the whole pass.
        frozen = dict_path.read_bytes()
        essay_before = (ms / "02-terms.md").read_bytes()

        def untouched(verb: str) -> None:
            check(f"E3 {verb} leaves pronunciations.md byte for byte",
                  dict_path.read_bytes() == frozen,
                  dict_path.read_text()[-200:])

        run_stdin(ws, json.dumps({"pronunciations": []}), "filter",
                  "prelude", "audio-friendly", "02-terms.md", "--replace")
        untouched("filter prelude")
        out = run(ws, "filter", "run", "audio-friendly", "02-terms.md")
        untouched("filter run")
        units = _filter_units((ms / "02-terms.md").read_text())
        window = _loads(db.one(
            "SELECT metadata FROM filter_runs WHERE manuscript_id = ? AND "
            "filter = 'audio-friendly' AND status = 'active'",
            (mid,))["metadata"], {})["window"]
        run_stdin(ws, _filter_reply(units, tuple(window), replaces={
            5: "A ledger counts, and nothing in it is an accusation, "
               "which is the point of keeping one. Some transliterate "
               "the word as chid, without its capital."},
            state="the voice note: read once"),
            "filter", "record", "02-terms.md")
        untouched("filter record")
        run(ws, "filter", "triage", "02-terms.md", "--accept", "1")
        untouched("filter triage")
        run(ws, "filter", "resolve", "02-terms.md", "--pause")
        untouched("filter resolve --pause")
        marked = (ms / "02-terms.md").read_text()
        (ms / "02-terms.md").write_text(
            marked.replace("which is the point of keeping one",
                           "which is why one is kept"))
        untouched("a hand post-edit of the marked essay")
        run(ws, "filter", "resolve", "02-terms.md")
        untouched("filter resolve")
        run(ws, "filter", "rollback", "02-terms.md")
        untouched("filter rollback")
        (ms / "02-terms.md").write_bytes(essay_before)
        run(ws, "collect")
        out = run(ws, "filter", "run", "audio-friendly", "02-terms.md",
                  "--again")
        window = _loads(db.one(
            "SELECT metadata FROM filter_runs WHERE manuscript_id = ? AND "
            "filter = 'audio-friendly' AND status = 'active'",
            (mid,))["metadata"], {})["window"]
        units = _filter_units((ms / "02-terms.md").read_text())
        run_stdin(ws, _filter_reply(units, tuple(window), replaces={
            5: "A ledger counts, and nothing in it is an accusation."},
            state="the voice note: read twice"),
            "filter", "record", "02-terms.md")
        run(ws, "filter", "triage", "02-terms.md", "--accept", "1")
        run(ws, "filter", "resolve", "02-terms.md", "--pause")
        run(ws, "filter", "unmark", "02-terms.md")
        untouched("filter unmark")
        run(ws, "filter", "abandon", "audio-friendly", "02-terms.md")
        untouched("filter abandon")
        # The TENTH verb, and the only one that could actually rewrite
        # the file: `doc push` normalizes its text and WRITES THE
        # NORMALIZED BYTES BACK to disk (gdocs.push_doc), which makes it
        # a second writer of anything normalize_markdown is not a no-op
        # on. Driven at the seam — what is under test is the write-back,
        # not Drive.
        from authorlm.gdocs import normalize_markdown as _norm
        pushed = _norm(dict_path.read_text(encoding="utf-8"))
        check("E3 `doc push` normalizes and writes back, so its "
              "normalizer must be a NO-OP on the dictionary — otherwise "
              "the push is a SECOND WRITER of a file whose whole point "
              "is that only the author's verdict writes it",
              pushed.encode("utf-8") == frozen,
              repr(pushed[-160:]))
        check("E3 ten verbs, and NOTHING in the system writes that file "
              "except the author's own verdict at `proposal accept`",
              dict_path.read_bytes() == frozen)
        (ms / "02-terms.md").write_bytes(essay_before)
        run(ws, "collect")

        # ================= E6 — the exclusions, one each =============
        check("E6 no essay_summaries row for the dictionary — it is not a "
              "unit of the book",
              db.one("SELECT COUNT(*) AS n FROM essay_summaries WHERE "
                     "manuscript_id = ? AND file = ?",
                     (mid, _pron.FILENAME))["n"] == 0, "")
        run(ws, "export", "md")
        combined = next((ms / "_exports").glob("*.md")).read_text()
        check("E6 absent from the exported book — the dictionary must "
              "NEVER ship inside it",
              "uh-NUT-taa" not in combined
              and "# Pronunciations" not in combined, combined[:400])
        _api.add_concept(db, manuscript, "Ereignis", kind="concept",
                         notes="only ever named in the dictionary")
        run(ws, "collect")
        node = db.one("SELECT status, introduced_in FROM concept_nodes "
                      "WHERE manuscript_id = ? AND name = 'Ereignis'",
                      (mid,))
        check("E6 a concept whose name appears ONLY in the dictionary does "
              "not realize — the ratified toc.toml assertion, re-run for "
              "the sidecar (otherwise the extractor mines a pronunciation "
              "table for concepts)",
              node["status"] == "declared" and node["introduced_in"] is None,
              f"{node['status']} / {node['introduced_in']}")
        out = run(ws, "filter", "run", "duplicate-words",
                  _pron.FILENAME, expect_exit=True)
        check("E6 `filter run` on the dictionary refuses BY NAME rather "
              "than through the generic reading-order refusal",
              "is the pronunciation dictionary, not an essay" in out
              and "proposal review" in out, out)
        run_stdin(ws, "Find the loose ends.\n", "lens", "add", "loose")
        out = run(ws, "lens", "run", "loose", _pron.FILENAME,
                  expect_exit=True)
        check("E6 `lens run` refuses by name too — the site that accepts "
              "toc.toml today, sharing one helper so the two cannot drift",
              "is the pronunciation dictionary, not an essay" in out, out)
        out = run(ws, "intent", "declare", "Say the Sanskrit right",
                  "--scope", _pron.FILENAME, expect_exit=True)
        check("E6 an intent scoped to the dictionary refuses — there is no "
              "writing to route there",
              "is the pronunciation dictionary, not an essay" in out, out)
        brief = run(ws, "briefing")
        check("E6 the briefing never reports it as missing from toc.toml — "
              "`reading_order`'s unlisted list only covers CONTENT files",
              _pron.FILENAME not in brief, brief)
        version = db.one(
            "SELECT files FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no DESC LIMIT 1", (mid,))
        check("E6 BUT it IS in manuscript_versions.files — structure is "
              "not content, and it is still the author's: versioned, "
              "checksummed, diffable and rollback-able like any file",
              _pron.FILENAME in _loads(version["files"], {}), "")
        dict_path.write_text(
            dict_path.read_text() + "| Līlā | LEE-laa | play |\n",
            encoding="utf-8")
        run(ws, "collect")
        check("E6 ...and a hand edit of it produces an "
              "editorial_transitions row — an added pronunciation is a "
              "real editorial act",
              db.one("SELECT COUNT(*) AS n FROM editorial_transitions "
                     "WHERE manuscript_id = ? AND location LIKE ?",
                     (mid, _pron.FILENAME + "%"))["n"] >= 1,
              [r["location"] for r in db.all(
                  "SELECT location FROM editorial_transitions WHERE "
                  "manuscript_id = ?", (mid,))])
        # ================= E9 — the pointers resolve =================
        # §6.0's genericity boundary is only real if the thing the
        # prompts POINT AT is actually in the payload. Two halves: the
        # aspect tags reach block S, and the three artifacts name no word
        # of this book.
        run(ws, "style", "guide", "Book Law")
        run(ws, "style", "add", "figure",
            "Draw from the established motif families: walls/chains, "
            "fire/light.", "--guide", "Book Law")
        run(ws, "style", "add", "lexicon",
            "'Test' names the two questions; 'measure' is the universal "
            "count. Never interchange them.", "--guide", "Book Law")
        run(ws, "style", "attach", "02-terms.md", "Book Law")
        out = run(ws, "filter", "run", "duplicate-words", "02-terms.md")
        block_s = _block(out, "S")
        check("E9 STYLE LAW prints one element per line with its aspect "
              "in brackets, so a prompt that says \"the `[figure]` "
              "elements of STYLE LAW\" names something actually in the "
              "payload",
              "- [figure] Draw from the established motif families"
              in block_s
              and "- [lexicon] 'Test' names the two questions" in block_s,
              block_s[-600:])
        check("E9 ...and the harness prompt makes exactly that pointer, "
              "so a renderer change that dropped the tag fails a test "
              "rather than silently turning three prompt pointers into "
              "references to nothing",
              "`[figure]`" in block_s and "`[lexicon]`" in block_s,
              block_s[:2000])
        run(ws, "filter", "abandon", "duplicate-words", "02-terms.md")

        # The template check. A listed-exceptions test, not a bare "no
        # book words": an artifact may keep the worked example §6.0
        # ratifies, and a bare assertion would fail on the first ordinary
        # English collision.
        design = (Path(__file__).resolve().parent.parent / "docs"
                  / "filter-pass-design.md").read_text(encoding="utf-8")
        appendix = design.split(
            "## Appendix — the three filters, in full", 1)[1]
        # The NORMATIVE prose only. The indented blocks are worked
        # examples of a FORM — a state ledger's shape, a registry's shape
        # — and §6.4 labels them as such in as many words. An
        # enumeration in a normative bullet is a lexicon the model treats
        # as the set; a labelled example is not, and rewriting the
        # ratified examples was not part of the ruling.
        normative = "\n".join(line for line in appendix.split("\n")
                               if not line.startswith("    "))
        this_book = [
            "Nothingness", "Qualities", "The Chid", "the Chid",
            "Field of Choice", "The Dharma", "the Dead", "the Dharma",
        ]
        named = [t for t in this_book if t in normative]
        check("E9 the three artifacts name NO term of this book in "
              "normative prose — the vocabulary comes from PROTECTED "
              "TERMS, so a second manuscript adopts them unedited",
              not named, ", ".join(named))
        # Not only the two removed lists: the metaphor artifact's
        # per-unit bullets used to teach this book's families in passing
        # ("fire is the agent", "a wall that catches light", "since the
        # wall is stone"), which is the same enumeration wearing an
        # example's clothes.
        families = ["Fire, wall, stone, chain, ledger, ladder, tremor",
                    "Fire, wall, stone, chain, ledger, ladder, tremor, "
                    "field,", "walls/chains, fire/light",
                    "the registry says fire is", "carries his fire",
                    "a wall that\n  catches light", "a ledger that burns",
                    "a path that strikes", "since the wall is stone",
                    "fire, wall, stone"]
        listed = [f for f in families if f in normative]
        check("E9 ...and no motif-family list either — the `[figure]` "
              "elements of STYLE LAW are the authority, and a copy in a "
              "prompt is a copy that can disagree with the law",
              not listed, ", ".join(listed))
        check("E9 the worked examples that DO carry this book's words "
              "are labelled as illustrations of the FORM, never of the "
              "set — which is the whole difference between an example "
              "and an enumeration",
              "worked illustration of the FORM, not the list" in appendix,
              "")
        check("E9 the worked example §6.0 keeps IS still there, with the "
              "law named as the authority it only illustrates — a "
              "template needs an example, and must not pretend the "
              "example is the law",
              '"test" and "measure" are one' in appendix
              and "`[lexicon]` elements are\n  the authority" in appendix,
              appendix[:200])
        check("E9 and the doctrine stays, in the author's words, because "
              "that is what a per-manuscript artifact is FOR",
              "**A refrain.**" in appendix
              and "Cut, don't substitute." in appendix
              and "precision wins" in appendix
              and "A listener cannot look back" in appendix, "")

    finally:
        server.shutdown()


def _pron_tab_tree(pairs: list[tuple[str, str]]) -> dict:
    """A Docs tab tree: [(tab_id, title)] flat under one container."""
    return {"tabs": [{
        "tabProperties": {"tabId": "root", "title": "book"},
        "childTabs": [{"tabProperties": {"tabId": tid, "title": title},
                       "childTabs": []} for tid, title in pairs]}]}


class _PronDocsService:
    """The Docs API's read half: the tab tree, and nothing else."""

    def __init__(self, tree):
        self.tree = tree

    def documents(self):
        outer = self

        class Docs:
            @staticmethod
            def get(documentId, includeTabsContent=False):
                class R:
                    @staticmethod
                    def execute():
                        return outer.tree
                return R()
        return Docs()


class _PronDriveService:
    """The Drive API's export half: one whole-master markdown blob."""

    def __init__(self, export: str):
        self.export = export

    def files(self):
        outer = self

        class Files:
            @staticmethod
            def export(fileId, mimeType):
                class R:
                    @staticmethod
                    def execute():
                        return outer.export.encode("utf-8")
                return R()
        return Files()


def scenario_pronunciation_bridge(root: Path) -> None:
    """E5 and E7 — the Doc bridge (§15.22 §2.5 rows 19, 22-25).

    Driven at the seam against recording doubles rather than through the
    CLI: what is under test is `sync_tab_structure`, the pull's toc sync,
    `rewrite_toc_from_doc` and the zero-rows guard, and each of those is
    a function that takes a tab tree and a markdown export."""
    print("Scenario PB — the pronunciation dictionary across the Doc bridge")
    import json as _json

    from authorlm import api as _api
    from authorlm import gdocs as _gd
    from authorlm import pronunciations as _pron
    from authorlm.db import Database as _DB
    from authorlm.structure import parse_toc_tree

    ws = root / "pbridge"
    ms = ws / "manuscript"
    write(ms / "01-open.md", "# One\n\nThe first essay.\n")
    write(ms / "02-next.md", "# Two\n\nThe second essay.\n")
    write(ms / "toc.toml",
          '[[chapter]]\nfile = "01-open.md"\n\n'
          '[[chapter]]\nfile = "02-next.md"\n')
    write(ms / _pron.FILENAME, PRON_SEED_ROWS)
    run(ws, "init", "--name", "book", "--path", str(ms))
    db = _DB(ws / ".authorlm" / "authorlm.db")
    manuscript = _api.get_manuscript(db)

    bridge = _gd.manuscript_bridge(manuscript)
    order = _gd._reading_order_files(bridge)
    check("E7 the dictionary IS in the tab list — the ONE inversion — and "
          "it is APPENDED LAST, after every essay",
          order == ["01-open.md", "02-next.md", _pron.FILENAME], order)

    # The mapping a push would have left behind: one tab per file,
    # dictionary included.
    tabs = [("t1", "01-open.md"), ("t2", "02-next.md"),
            ("t3", _pron.FILENAME)]
    links = {"_master_id": "master-1", "_container_tab": "root"}
    for tid, title in tabs:
        links[title] = {"tab_id": tid, "checked_out": False}
    _gd._save_mapping(db, manuscript, {"gdocs": links})
    docs = _PronDocsService(_pron_tab_tree(tabs))

    # ---- E7 / site 23: sync_tab_structure -------------------------
    state = _gd.sync_tab_structure(db, manuscript, docs)
    check("E7 site 23: sync_tab_structure reports INSYNC with a mapped "
          "dictionary tab present. Without the guard `linked` holds a "
          "file `parse_toc_tree` never can, the two lists can never "
          "agree, and tab-order sync stops working forever",
          state == {"insync": True}, state)
    saved = _gd._mapping(db, manuscript)["gdocs"].get("_tab_structure")
    check("E7 ...and the recorded base carries the essays only, so the "
          "next comparison starts from a list the toc can match",
          [tuple(x) for x in saved] == [("01-open.md", None),
                                        ("02-next.md", None)], saved)

    # Site 23 has TWO halves and only the doc_pairs half is exercised
    # above, because toc.toml cannot normally name a sidecar. It CAN if
    # the author hand-lists it — or if a pre-guard build already wrote it
    # there, which is exactly the state guard 25 exists to prevent and
    # therefore exactly the state a repair must survive. With the desired
    # side unfiltered, `desired` would then hold the dictionary while the
    # base recorded above does not, and the sync would report movement
    # that never happened.
    toc_path_pre = ms / "toc.toml"
    hand_listed = toc_path_pre.read_bytes()
    toc_path_pre.write_text(hand_listed.decode()
                            + f'\n[[chapter]]\nfile = "{_pron.FILENAME}"\n')
    state = _gd.sync_tab_structure(db, manuscript, docs)
    check("E7 site 23, the DESIRED half: a toc.toml that names the "
          "dictionary — hand-listed, or left by a pre-guard build — "
          "still reports insync. Filtering only the doc side would leave "
          "the repair path reporting movement that never happened",
          state == {"insync": True}, state)
    toc_path_pre.write_bytes(hand_listed)

    # ---- E7 / site 25: rewrite_toc_from_doc -----------------------
    toc_path = ms / "toc.toml"
    before = toc_path.read_bytes()
    _gd.rewrite_toc_from_doc(
        manuscript, [("01-open.md", None), (_pron.FILENAME, None),
                     ("02-next.md", None)],
        parse_toc_tree(before.decode()))
    entries = [n for n, _ in parse_toc_tree(toc_path.read_text())]
    check("E7 site 25: rewrite_toc_from_doc NEVER writes the dictionary "
          "into toc.toml, even handed a doc list that names it. That is "
          "the one that does the most damage: a dictionary in toc.toml is "
          "a CHAPTER, and every exclusion unravels at once",
          _pron.FILENAME not in entries
          and entries == ["01-open.md", "02-next.md"], entries)
    toc_path.write_bytes(before)

    # ---- E7 / site 24 + E5: the pull ------------------------------
    def export_of(files: dict[str, str]) -> str:
        # The container tab is the first boundary in the Docs export,
        # exactly as walk_tabs reports it — split_tabbed_export matches
        # positionally, so an export missing it splits into nothing.
        return "# **book**\n\n" + "".join(
            f"# **{name}**\n\n{text}\n" for name, text in files.items())

    live = {"01-open.md": "# One\n\nThe first essay.\n",
            "02-next.md": "# Two\n\nThe second essay.\n",
            _pron.FILENAME: PRON_SEED_ROWS}
    report = _gd.pull_doc(db, manuscript, service=_PronDriveService(
        export_of(live)), docs_service=docs, with_comments=False)
    check("E5 a clean round trip: the Docs export of the dictionary tab "
          "parses back to exactly the rows that went out",
          _pron.parse((ms / _pron.FILENAME).read_text())[0]
          == _pron.parse(PRON_SEED_ROWS)[0]
          and _pron.FILENAME not in report["conflicts"]
          and _pron.FILENAME not in report["missing"]
          and _pron.FILENAME in (report["changed"] + report["unchanged"]),
          report)
    check("E7 site 24: a pull whose Doc tab order is unchanged does NOT "
          "rewrite toc.toml, and does not report a conflict — the same "
          "mismatched comparison as site 23, on the pull side",
          not report.get("toc_updated") and not report.get("toc_conflict")
          and toc_path.read_bytes() == before, report)
    check("E7 ...and toc.toml still has no pronunciations.md chapter "
          "after a full pull",
          _pron.FILENAME not in
          [n for n, _ in parse_toc_tree(toc_path.read_text())],
          toc_path.read_text())

    # A Docs export that mangled the table into bullet lines: the row
    # count goes to ZERO, and this is the pull that would destroy the
    # dictionary.
    frozen = (ms / _pron.FILENAME).read_bytes()
    mangled = ("# Pronunciations\n\nHow the terms in this book are said "
               "aloud.\n\n- anattā uh-NUT-taa Pali\n- Nāgārjuna "
               "naa-GAAR-ju-na\n- Ereignis er-EYE-gnis German\n")
    report = _gd.pull_doc(db, manuscript, service=_PronDriveService(
        export_of({**live, _pron.FILENAME: mangled})),
        docs_service=docs, with_comments=False)
    check("E5 the zero-rows guard: a mangled export is SKIPPED and "
          "reported by name",
          report.get("sidecar_unparsable") == [_pron.FILENAME], report)
    check("E5 ...and the local bytes are untouched — asserted by outcome, "
          "the way push_doc's is_marked guard is",
          (ms / _pron.FILENAME).read_bytes() == frozen,
          (ms / _pron.FILENAME).read_text()[:200])
    report = _gd.pull_doc(db, manuscript, service=_PronDriveService(
        export_of({**live, _pron.FILENAME: mangled})),
        docs_service=docs, force=True, with_comments=False)
    check("E5 ...and --force DOES NOT REACH PAST IT. 'Mostly "
          "unreachable' is not a guard, and neither is a guard with a "
          "flag that turns it off",
          report.get("sidecar_unparsable") == [_pron.FILENAME]
          and (ms / _pron.FILENAME).read_bytes() == frozen, report)

    # FEWER rows is the author deleting a row in the Doc, which is
    # legitimate and must work.
    shorter = ("# Pronunciations\n\nHow the terms in this book are said "
               "aloud.\n\n| Term | Say it | Note |\n| --- | --- | --- |\n"
               "| anattā | uh-NUT-taa | Pali |\n")
    report = _gd.pull_doc(db, manuscript, service=_PronDriveService(
        export_of({**live, _pron.FILENAME: shorter})),
        docs_service=docs, with_comments=False)
    check("E5 a pull that parses to FEWER rows is not guarded — that is "
          "an author deleting a row in the Doc, and it must work",
          not report.get("sidecar_unparsable")
          and [r["term"] for r in
               _pron.parse((ms / _pron.FILENAME).read_text())[0]]
          == ["anattā"],
          (ms / _pron.FILENAME).read_text())

    # And the Docs shape itself: colons, bold, padding, escapes.
    docsy = ("# Pronunciations\n\nHow the terms in this book are said "
             "aloud.\n\n| **Term** | **Say it** | **Note** |\n"
             "| :--- | :---: | --- |\n"
             "|  anattā  |  uh-NUT-taa  | Pali |\n"
             "| Śūnyatā | shoon-yuh-TAA | Sanskrit |\n"
             "|  |  |  |\n")
    _gd.pull_doc(db, manuscript, service=_PronDriveService(
        export_of({**live, _pron.FILENAME: docsy})),
        docs_service=docs, with_comments=False)
    check("E5 a Docs-SHAPED export round-trips to the rows it means — "
          "the parser's tolerance list is written against what Docs "
          "actually emits, and normalize_markdown runs before it",
          _pron.parse((ms / _pron.FILENAME).read_text())[0]
          == [{"term": "anattā", "say": "uh-NUT-taa", "note": "Pali"},
              {"term": "Śūnyatā", "say": "shoon-yuh-TAA",
               "note": "Sanskrit"}],
          (ms / _pron.FILENAME).read_text())


def main_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-e2e-"))
    try:
        scenario_editorial_loop(root)
        scenario_style(root)
        scenario_transplant()
        scenario_ephemeral_history()
        scenario_prerequisite_gap(root)
        scenario_objection(root)
        scenario_alias_and_syllogism(root)
        scenario_errors(root)
        scenario_llm_and_unregister(root)
        scenario_write_loop(root)
        scenario_write_draft(root)
        scenario_write_new_and_digest(root)
        scenario_parallel_writeups(root)
        scenario_writeup_scope(root)
        scenario_intent_no_fanout(root)
        scenario_intent_scope(root)
        scenario_intent_tie(root)
        scenario_doc_comments(root)
        scenario_shell_watch_obsidian(root)
        scenario_watcher_guard(root)
        scenario_testbench(root)
        scenario_filter(root)
        scenario_pronunciations(root)
        scenario_pronunciation_bridge(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nAll {PASSED} checks passed.")


if __name__ == "__main__":
    _assert_offline()
    main_test()
