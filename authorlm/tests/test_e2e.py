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
          "(abandoned) A dead-end objective" in out, out)
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


class StubLLMHandler(http.server.BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible /chat/completions endpoint with canned
    replies keyed on the system prompt."""

    # Every request the stub has served, ever. The write path's design
    # invariant (§13, I4) is that the new verbs add NO model call, and
    # the only way to assert "zero calls" is to count them.
    REQUESTS = 0

    def do_POST(self):
        StubLLMHandler.REQUESTS += 1
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        system = body["messages"][0]["content"]
        user = body["messages"][-1]["content"]
        if "sole task is to find aliasing statements" in system:
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
                     "sentence": "We call it Elective Ground, the settled "
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
            "choices": [{"message": {"content": content}}],
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
              "of shapes. We call it Elective Ground, the settled name for "
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
        out = run(wg, "plan", "--draft")
        check("plan --draft writes stubs into _drafts/",
              "Draft stub written" in out
              and (mg / "_drafts" / "alpha.md").exists(), out)
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
        check("file is empty on disk after start",
              (ms / "02-essay.md").read_text() == "", out)
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
        from authorlm import api as _api
        fake_node = {"name": "N", "kind": "concept", "status": "declared",
                     "introduced_in": None, "notes": "", "aliases": "[]"}
        fake = {"since": "", "belief_changes": [], "new_beliefs": [],
                "realized_concepts": [], "contradictions": [],
                "outstanding_questions": [], "active_intents": [],
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
        check("second writeup truncated its file",
              (ms / "01-choice.md").read_text() == "", "")

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
        check("B1 — and the file on disk is still empty (the old essay lives "
              "in the pinned version, never on disk)",
              (ms / "02-essay.md").read_text() == "", out)
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
        scenario_write_new_and_digest(root)
        scenario_doc_comments(root)
        scenario_shell_watch_obsidian(root)
        scenario_watcher_guard(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nAll {PASSED} checks passed.")


if __name__ == "__main__":
    _assert_offline()
    main_test()
