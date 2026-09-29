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
import hashlib
import http.server
import io
import json
import re
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import (  # noqa: E402
    api, cli, triage, triage_analysis, triage_rules, triage_server,
    triage_transport,
)
from authorlm.db import Database, ko_fields  # noqa: E402
from authorlm.extraction import triage_feedback  # noqa: E402


def manuscript_fixture(root: Path):
    manuscript_path = root / "book"
    manuscript_path.mkdir()
    (manuscript_path / "chapter.md").write_text(
        "# Choice\n\nChoice makes distinction possible.\n\n"
        "A field permits further choices.\n",
        encoding="utf-8",
    )
    db = Database(root / "authorlm.db")
    manuscript = ko_fields("ms")
    manuscript.update(name="book", path=str(manuscript_path))
    db.insert("manuscripts", manuscript)
    system = db.source("system")

    def concept(name: str):
        row = ko_fields("cn")
        row.update(manuscript_id=manuscript["id"], name=name, kind="concept",
                   status="realized", introduced_in="chapter.md", notes=f"{name} notes",
                   aliases="[]", source_id=system,
                   metadata=json.dumps({"origin": "extracted"}))
        db.insert("concept_nodes", row)
        return row

    choice = concept("Choice")
    field = concept("Field")
    edge = ko_fields("ce")
    edge.update(manuscript_id=manuscript["id"], from_node=choice["id"],
                relation="permits", to_node=field["id"], status="inferred",
                support=1, evidence="[]", source_id=system)
    db.insert("concept_edges", edge)
    return db, manuscript, choice, field, edge


class FakeLLM:
    enabled = True
    model = "test-model"

    def complete_json(self, system, user, thinking_budget=None):
        if system == triage_analysis.ONTOLOGY_SYSTEM:
            return {"thesis": "Choice creates fields", "argument_arc": [],
                    "central_concepts": [], "distinctions": [], "tensions": []}
        payload = json.loads(user)
        return {"results": [
            {"object_id": item["object_id"], "score": 88, "confidence": 81,
             "why": "The manuscript uses it as part of the central argument.",
             "evidence": ([{"passage_id": item["passages"][0]["id"],
                            "reason": "Direct use"}]
                          if item["passages"] else [])}
            for item in payload["objects"]
        ]}

    def stats_line(self):
        return "LLM: test"


class TriageAppTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="authorlm-triage-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self, root: Path | None = None):
        result = manuscript_fixture(root or self.root)
        self.addCleanup(result[0].conn.close)
        return result

    def settle_fixture_concepts(self, db, *nodes):
        for node in nodes:
            metadata = json.loads(node["metadata"])
            metadata["confirmed"] = True
            db.update("concept_nodes", node["id"],
                      {"metadata": json.dumps(metadata)})

    def add_pending_concept(self, db, manuscript, name, *, kind="concept"):
        row = ko_fields("cn")
        row.update(
            manuscript_id=manuscript["id"], name=name, kind=kind,
            status="realized", introduced_in=None, notes=f"{name} notes",
            aliases="[]", source_id=db.source("system"),
            metadata=json.dumps({"origin": "extracted"}),
        )
        db.insert("concept_nodes", row)
        return row

    def add_inferred_edge(self, db, manuscript, source, target, relation="permits"):
        row = ko_fields("ce")
        row.update(
            manuscript_id=manuscript["id"], from_node=source["id"],
            relation=relation, to_node=target["id"], status="inferred",
            support=0, evidence="[]", source_id=db.source("system"),
        )
        db.insert("concept_edges", row)
        return row

    def test_critique_tab_lists_stages_and_applies_verdicts(self):
        from authorlm import critique as crit

        db, manuscript, *_ = self.fixture()
        guide = ko_fields("sg")
        guide.update(manuscript_id=manuscript["id"], name="house", parent=None)
        db.insert("style_guides", guide)
        crit.import_manifest(db, manuscript["id"], {
            "source": {"name": "App Review"},
            "items": [
                {"kind": "intent", "unit": "Recapitulation", "ordinal": 1,
                 "text": "Cut the section by half.",
                 "scope": "recapitulation.md"},
                {"kind": "intent", "unit": "Global", "ordinal": 1,
                 "text": "Build a glossary.", "scope": None},
                {"kind": "style_element", "unit": "Rules", "ordinal": 1,
                 "text": "Separate claim levels.", "aspect": "rhetoric",
                 "guide": "house"},
            ]})

        rows = triage.list_rows(db, manuscript, "critique")
        self.assertEqual(len(rows), 3)
        by_statement = {row["statement"]: row for row in rows}
        self.assertEqual(
            by_statement["Build a glossary."]["scope_label"], "manuscript-wide")
        self.assertEqual(
            by_statement["Separate claim levels."]["kind"], "style element")
        # manuscript-wide sitting sorts first — the global sitting comes
        # before any essay's just-in-time pile
        self.assertEqual(rows[0]["scope_label"], "manuscript-wide")
        self.assertTrue(all(row["pending"] for row in rows))

        # snapshot works with no analyzer profile (proposals precedent)
        snap = triage.snapshot(db, manuscript, "critique")
        self.assertIsNone(snap["profile"])
        self.assertEqual(len(snap["rows"]), 3)

        intent = by_statement["Cut the section by half."]
        element = by_statement["Separate claim levels."]
        glossary = by_statement["Build a glossary."]
        triage.stage_decisions(db, manuscript, "critique", [
            {"object_id": intent["id"], "action": "reject",
             "reason": "The length is deliberate."},
            {"object_id": element["id"], "action": "accept"},
            {"object_id": glossary["id"], "action": "revise",
             "parameters": {"text": "Build a glossary of capitalized terms."}},
        ])
        result = triage.apply_selected(
            db, manuscript, "critique",
            [intent["id"], element["id"], glossary["id"]])
        self.assertEqual(result["count"], 3)
        self.assertEqual(
            db.one("SELECT status, outcome FROM declared_intents WHERE id = ?",
                   (intent["id"],))["status"], "rejected")
        self.assertEqual(
            db.one("SELECT status FROM style_laws WHERE id = ?",
                   (element["id"],))["status"], "active")
        revised = db.one("SELECT * FROM declared_intents WHERE id = ?",
                         (glossary["id"],))
        self.assertEqual(revised["status"], "active")
        self.assertEqual(revised["statement"],
                         "Build a glossary of capitalized terms.")
        self.assertEqual(revised["source_id"], db.source("author"))
        self.assertEqual(triage.list_rows(db, manuscript, "critique"), [])

    def test_critique_reject_without_reason_refuses(self):
        from authorlm import critique as crit

        db, manuscript, *_ = self.fixture()
        crit.import_manifest(db, manuscript["id"], {
            "source": {"name": "App Review"},
            "items": [{"kind": "intent", "unit": "U", "ordinal": 1,
                       "text": "Do the thing.", "scope": None}]})
        row = triage.list_rows(db, manuscript, "critique")[0]
        with self.assertRaises(ValueError):
            triage.apply_action(db, manuscript, "critique", row, "reject")
        self.assertEqual(
            db.one("SELECT status FROM declared_intents WHERE id = ?",
                   (row["id"],))["status"], "proposed")

    def test_schema_and_profiles_are_shared(self):
        db, manuscript, *_ = self.fixture()
        profile = triage.resolve_profile(manuscript, "concepts")
        result = triage.snapshot(db, manuscript, "concepts")
        self.assertEqual(result["schema"]["help"], triage.CONCEPT_TRIAGE_HELP)
        self.assertEqual(profile["id"], "concept-keepability")
        self.assertEqual(
            [field["id"] for field in result["schema"]["analysis_columns"]],
            ["score", "confidence", "why", "evidence"])
        retire = next(action for action in result["schema"]["actions"]
                      if action["id"] == "retire")
        self.assertFalse(retire["reason"]["required"])
        concept_actions = {action["id"]: action
                           for action in result["schema"]["actions"]}
        self.assertNotIn("reason", concept_actions["keep"])
        self.assertIn("reason", concept_actions["retype"])
        self.assertIn("reason", concept_actions["alias"])
        retype_kinds = concept_actions["retype"]["parameter"]["options"]
        self.assertNotIn("definition", retype_kinds)
        edge_actions = {action["id"]: action for action in
                        triage.snapshot(db, manuscript, "edges")["schema"]["actions"]}
        self.assertNotIn("reason", edge_actions["keep"])
        self.assertNotIn("reason", edge_actions["reject"])
        self.assertNotIn("reason", edge_actions["flip"])
        self.assertIn("reason", edge_actions["retype"])
        self.assertIn("reason", edge_actions["alias"])

    def test_staging_discards_reasons_for_actions_that_do_not_request_one(self):
        db, manuscript, *_ = self.fixture()
        concept = self.add_pending_concept(db, manuscript, "Keep without explanation")
        triage.stage_decisions(db, manuscript, "concepts", [{
            "object_id": concept["id"],
            "action": "keep",
            "reason": "This must not be stored",
        }])
        row = next(item for item in triage.snapshot(db, manuscript, "concepts")["rows"]
                   if item["id"] == concept["id"])
        self.assertIsNone(row["draft"]["reason"])

    def test_retype_reason_is_recorded_as_author_evidence(self):
        db, manuscript, *_ = self.fixture()
        concept = self.add_pending_concept(db, manuscript, "Retype with explanation")
        triage.stage_decisions(db, manuscript, "concepts", [{
            "object_id": concept["id"],
            "action": "retype",
            "parameters": {"kind": "metaphor"},
            "reason": "This is a sustained image.",
        }])
        triage.apply_selected(db, manuscript, "concepts", [concept["id"]])
        evidence = db.one(
            "SELECT target FROM evidence WHERE manuscript_id = ? "
            "AND evidence_type = 'concept_triage' ORDER BY created_at DESC LIMIT 1",
            (manuscript["id"],),
        )
        self.assertIn("reason: This is a sustained image.", evidence["target"])

    def test_retype_rejects_retired_definition_kind(self):
        db, manuscript, *_ = self.fixture()
        concept = self.add_pending_concept(db, manuscript, "Legacy kind")
        with self.assertRaisesRegex(ValueError, "invalid concept kind 'definition'"):
            triage.stage_decisions(db, manuscript, "concepts", [{
                "object_id": concept["id"],
                "action": "retype",
                "parameters": {"kind": "definition"},
            }])

    def test_shared_help_preserves_the_existing_cli_text(self):
        self.assertEqual(
            triage.CONCEPT_TRIAGE_HELP,
            "Keys: [k]eep  [r]etire  [s]kip (or Enter)  [n] reword notes  "
            "[a] alias of another concept  [x] quit — or retype: [c]oncept "
            "[o]bjection [e]xample [m]etaphor [q]uestion "
            "[h]istorical_reference [t] mathematical_construct")
        self.assertEqual(
            triage.EDGE_TRIAGE_HELP,
            "Keys: [k]onfirm as-is  [r]eject  [f]lip direction  "
            "[a] these are one concept (alias-merge)  [s]kip (or Enter)  "
            "[x] quit — or retype the relation by number, name, or unique prefix")

    def test_cli_parser_exposes_deterministic_dry_run_and_apply(self):
        args = cli.build_parser().parse_args([
            "concept", "triage", "--deterministic", "--edges", "--apply",
        ])
        self.assertTrue(args.deterministic)
        self.assertTrue(args.edges)
        self.assertTrue(args.apply)

    def test_cli_parser_rejects_retired_definition_kind(self):
        parser = cli.build_parser()
        for option in ("--kind", "--all-kind"):
            with self.subTest(option=option):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        parser.parse_args([
                            "concept", "add", "Legacy", option, "definition",
                        ])

    def test_deterministic_plan_is_pure_and_uses_system_provenance(self):
        db, manuscript, choice, field, _ = self.fixture()
        self.settle_fixture_concepts(db, choice, field)
        ghost = self.add_pending_concept(db, manuscript, "Spectral Machinery")
        edge = self.add_inferred_edge(db, manuscript, ghost, choice)
        files = {"chapter.md": (Path(manuscript["path"]) / "chapter.md").read_text()}

        plan = triage_rules.plan(db, manuscript, files)

        self.assertEqual(
            {item["rule"] for item in plan["decisions"]},
            {"concept_unmentioned", "edge_unmentioned_endpoint"})
        self.assertNotEqual(db.one(
            "SELECT status FROM concept_nodes WHERE id = ?", (ghost["id"],)
        )["status"], "retired")
        self.assertEqual(db.one(
            "SELECT status FROM concept_edges WHERE id = ?", (edge["id"],)
        )["status"], "inferred")

        result = triage.apply_deterministic(db, manuscript, plan["decisions"])

        self.assertEqual(result["count"], 2)
        self.assertEqual(db.one(
            "SELECT status FROM concept_nodes WHERE id = ?", (ghost["id"],)
        )["status"], "retired")
        self.assertEqual(db.one(
            "SELECT status FROM concept_edges WHERE id = ?", (edge["id"],)
        )["status"], "rejected")
        evidence = db.all(
            "SELECT e.evidence_type, s.kind FROM evidence e "
            "JOIN sources s ON s.id = e.source_id ORDER BY e.created_at")
        self.assertEqual([(row["evidence_type"], row["kind"]) for row in evidence],
                         [("deterministic_triage", "system")] * 2)
        self.assertNotIn("Spectral Machinery", triage_feedback(db, manuscript["id"]))

    def test_recommendation_transport_stages_without_applying(self):
        db, manuscript, choice, field, _ = self.fixture()
        self.settle_fixture_concepts(db, choice, field)
        ghost = self.add_pending_concept(db, manuscript, "Transport Ghost")

        plan = triage_transport.dispatch(
            "recommendations",
            {"manuscript": manuscript["name"], "triage_type": "concepts"},
            db=db,
        )
        self.assertEqual([item["object_id"] for item in plan["decisions"]],
                         [ghost["id"]])
        self.assertFalse(db.all("SELECT * FROM triage_drafts"))

        result = triage_transport.dispatch(
            "stage_recommendations",
            {"manuscript": manuscript["name"], "triage_type": "concepts",
             "object_ids": [ghost["id"]]},
            db=db,
        )
        self.assertEqual(result["staged"], [ghost["id"]])
        draft = db.one("SELECT * FROM triage_drafts WHERE object_id = ?",
                       (ghost["id"],))
        self.assertEqual(draft["action"], "retire")
        self.assertNotEqual(db.one(
            "SELECT status FROM concept_nodes WHERE id = ?", (ghost["id"],)
        )["status"], "retired")
        self.assertFalse(db.all("SELECT * FROM evidence"))

    def test_recommendation_transport_rechecks_before_staging(self):
        db, manuscript, choice, field, _ = self.fixture()
        self.settle_fixture_concepts(db, choice, field)
        ghost = self.add_pending_concept(
            db, manuscript, "Newly Grounded", kind="objection"
        )
        chapter = Path(manuscript["path"]) / "chapter.md"
        chapter.write_text(chapter.read_text() + "\nNewly Grounded now appears.\n")

        with self.assertRaisesRegex(ValueError, "no longer current"):
            triage_transport.dispatch(
                "stage_recommendations",
                {"manuscript": manuscript["name"], "triage_type": "concepts",
                 "object_ids": [ghost["id"]]},
                db=db,
            )
        self.assertFalse(db.all("SELECT * FROM triage_drafts"))

    def test_deterministic_plan_merges_known_alias_and_rejects_self_edge(self):
        db, manuscript, choice, field, _ = self.fixture()
        self.settle_fixture_concepts(db, choice, field)
        db.update("concept_nodes", field["id"],
                  {"aliases": json.dumps(["Spectral Machinery"])})
        duplicate = self.add_pending_concept(db, manuscript, "Spectral Machinery")
        self_edge = self.add_inferred_edge(db, manuscript, choice, choice)
        files = {"chapter.md": (Path(manuscript["path"]) / "chapter.md").read_text()}

        plan = triage_rules.plan(db, manuscript, files)
        self.assertEqual(
            {item["rule"] for item in plan["decisions"]},
            {"concept_known_alias", "edge_self_reference"})

        triage.apply_deterministic(db, manuscript, plan["decisions"])
        self.assertEqual(db.one(
            "SELECT status FROM concept_nodes WHERE id = ?", (duplicate["id"],)
        )["status"], "retired")
        self.assertEqual(db.one(
            "SELECT status FROM concept_edges WHERE id = ?", (self_edge["id"],)
        )["status"], "rejected")

    def test_deterministic_plan_protects_staged_and_author_connected_concepts(self):
        db, manuscript, choice, field, _ = self.fixture()
        self.settle_fixture_concepts(db, choice, field)
        staged = self.add_pending_concept(db, manuscript, "Staged Ghost")
        connected = self.add_pending_concept(db, manuscript, "Connected Ghost")
        self.add_inferred_edge(db, manuscript, staged, choice)
        declared = self.add_inferred_edge(db, manuscript, connected, field)
        db.update("concept_edges", declared["id"], {"status": "declared"})
        triage.stage_decisions(db, manuscript, "concepts", [{
            "object_id": staged["id"], "action": "keep",
        }])
        files = {"chapter.md": (Path(manuscript["path"]) / "chapter.md").read_text()}

        plan = triage_rules.plan(
            db, manuscript, files, include_concepts=True, include_edges=False)

        self.assertFalse(plan["decisions"])
        self.assertEqual({item["object_id"] for item in plan["protected"]},
                         {staged["id"], connected["id"]})

    def test_deterministic_apply_rejects_stale_plan_atomically(self):
        db, manuscript, choice, field, _ = self.fixture()
        self.settle_fixture_concepts(db, choice, field)
        first = self.add_pending_concept(db, manuscript, "First Ghost")
        second = self.add_pending_concept(db, manuscript, "Second Ghost")
        files = {"chapter.md": (Path(manuscript["path"]) / "chapter.md").read_text()}
        plan = triage_rules.plan(
            db, manuscript, files, include_concepts=True, include_edges=False)
        db.update("concept_nodes", second["id"], {"notes": "changed"})

        with self.assertRaises(triage.TriageConflict):
            triage.apply_deterministic(db, manuscript, plan["decisions"])

        self.assertNotEqual(db.one(
            "SELECT status FROM concept_nodes WHERE id = ?", (first["id"],)
        )["status"], "retired")
        self.assertNotEqual(db.one(
            "SELECT status FROM concept_nodes WHERE id = ?", (second["id"],)
        )["status"], "retired")

    def test_hygiene_apply_reuses_deterministic_mutation_and_provenance(self):
        db, manuscript, choice, field, _ = self.fixture()
        self.settle_fixture_concepts(db, choice, field)
        ghost = self.add_pending_concept(db, manuscript, "Sweep Ghost")
        args = SimpleNamespace(action="hygiene", apply=True)

        with mock.patch.object(cli, "_open_db", return_value=db), \
                mock.patch.object(cli, "_manuscript", return_value=manuscript), \
                contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_sweep(args)

        self.assertEqual(db.one(
            "SELECT status FROM concept_nodes WHERE id = ?", (ghost["id"],)
        )["status"], "retired")
        evidence = db.one(
            "SELECT evidence_type FROM evidence WHERE "
            "json_extract(metadata, '$.object_id') = ?", (ghost["id"],))
        self.assertEqual(evidence["evidence_type"], "deterministic_triage")

    def test_cli_concept_alias_triage_keeps_raw_database_rows(self):
        db, manuscript, choice, field, _ = self.fixture()
        with mock.patch("builtins.input", side_effect=["a", "Field", "x"]), \
                contextlib.redirect_stdout(io.StringIO()):
            cli._run_triage(db, manuscript["id"])
        self.assertEqual(
            db.one("SELECT status FROM concept_nodes WHERE id = ?", (choice["id"],))["status"],
            "retired")
        aliases = json.loads(db.one(
            "SELECT aliases FROM concept_nodes WHERE id = ?", (field["id"],))["aliases"])
        self.assertIn("Choice", aliases)

    def test_selected_decisions_apply_offscreen_as_one_batch(self):
        db, manuscript, choice, field, _ = self.fixture()
        triage.stage_decisions(db, manuscript, "concepts", [
            {"object_id": choice["id"], "action": "keep"},
            {"object_id": field["id"], "action": "retire",
             "reason": "The manuscript uses this only as scaffolding."},
        ])
        result = triage.apply_selected(
            db, manuscript, "concepts", [choice["id"], field["id"]])
        self.assertEqual(result["count"], 2)
        choice_meta = json.loads(db.one(
            "SELECT metadata FROM concept_nodes WHERE id = ?", (choice["id"],))["metadata"])
        self.assertTrue(choice_meta["confirmed"])
        self.assertEqual(db.one("SELECT status FROM concept_nodes WHERE id = ?",
                                (field["id"],))["status"], "retired")
        evidence = db.one(
            "SELECT target FROM evidence WHERE manuscript_id = ? AND signal = 'rejected'",
            (manuscript["id"],))
        self.assertIn("The manuscript uses this only as scaffolding.", evidence["target"])
        self.assertFalse(db.all("SELECT * FROM triage_drafts"))

    def test_multiple_alias_drafts_apply_safely_one_at_a_time(self):
        db, manuscript, choice, field, _ = self.fixture()
        agency = ko_fields("cn")
        agency.update(
            manuscript_id=manuscript["id"], name="Agency", kind="concept",
            status="realized", introduced_in="chapter.md", notes="Agency notes",
            aliases="[]", source_id=choice["source_id"],
            metadata=json.dumps({"origin": "extracted"}),
        )
        db.insert("concept_nodes", agency)
        triage.stage_decisions(db, manuscript, "concepts", [
            {"object_id": choice["id"], "action": "alias",
             "parameters": {"canonical_id": agency["id"]}},
            {"object_id": field["id"], "action": "alias",
             "parameters": {"canonical_id": agency["id"]}},
        ])

        with self.assertRaisesRegex(ValueError, "one row at a time"):
            triage.apply_selected(
                db, manuscript, "concepts", [choice["id"], field["id"]])
        triage.apply_selected(db, manuscript, "concepts", [choice["id"]])
        triage.apply_selected(db, manuscript, "concepts", [field["id"]])

        self.assertEqual(db.one(
            "SELECT status FROM concept_nodes WHERE id = ?", (choice["id"],)
        )["status"], "retired")
        self.assertEqual(db.one(
            "SELECT status FROM concept_nodes WHERE id = ?", (field["id"],)
        )["status"], "retired")
        aliases = json.loads(db.one(
            "SELECT aliases FROM concept_nodes WHERE id = ?", (agency["id"],)
        )["aliases"])
        self.assertEqual(set(aliases), {"Choice", "Field"})
        self.assertFalse(db.all("SELECT * FROM triage_drafts"))

    def test_single_and_staged_edge_mutations_have_identical_results(self):
        single_root = self.root / "single"
        staged_root = self.root / "staged"
        single_root.mkdir()
        staged_root.mkdir()
        single_db, single_manuscript, *_, single_edge = self.fixture(single_root)
        staged_db, staged_manuscript, *_, staged_edge = self.fixture(staged_root)

        triage.apply_one(
            single_db, single_manuscript, "edges", single_edge, "retype",
            {"relation": "depends_on"})
        triage.stage_decisions(staged_db, staged_manuscript, "edges", [{
            "object_id": staged_edge["id"], "action": "retype",
            "parameters": {"relation": "depends_on"},
        }])
        triage.apply_selected(
            staged_db, staged_manuscript, "edges", [staged_edge["id"]])

        single_row = single_db.one(
            "SELECT relation, status FROM concept_edges WHERE id = ?",
            (single_edge["id"],))
        staged_row = staged_db.one(
            "SELECT relation, status FROM concept_edges WHERE id = ?",
            (staged_edge["id"],))
        self.assertEqual(tuple(single_row), tuple(staged_row))
        single_evidence = single_db.one(
            "SELECT signal, target FROM evidence WHERE evidence_type = 'edge_triage'")
        staged_evidence = staged_db.one(
            "SELECT signal, target FROM evidence WHERE evidence_type = 'edge_triage'")
        self.assertEqual(tuple(single_evidence), tuple(staged_evidence))

    def test_edge_retype_preserves_legacy_cli_evidence_format(self):
        db, manuscript, *_, edge = self.fixture()
        triage.apply_one(
            db, manuscript, "edges", edge, "flip",
            {"relation": "depends_on"})
        row = db.one("SELECT * FROM concept_edges WHERE id = ?", (edge["id"],))
        self.assertEqual(row["from_node"], edge["to_node"])
        self.assertEqual(row["to_node"], edge["from_node"])
        evidence = db.one(
            "SELECT signal, target FROM evidence WHERE evidence_type = 'edge_triage'")
        self.assertEqual(evidence["signal"], "retyped")
        self.assertEqual(
            evidence["target"],
            "Choice —permits→ Field ⇒ Field —depends_on→ Choice")

    def test_legacy_one_shot_edge_commands_add_triage_evidence(self):
        """D4: confirm_edge/reject_edge are an author's individual verdict on
        a relationship, exactly like confirm_concept/retire_concept are for
        a concept — so, like their concept-side siblings, they now record
        evidence rather than silently mutating the graph."""
        db, manuscript, *_, edge = self.fixture()
        api.confirm_edge(db, manuscript, edge["id"][:8], "depends_on")
        api.reject_edge(db, manuscript, edge["id"][:8])
        self.assertEqual(
            db.one("SELECT status FROM concept_edges WHERE id = ?", (edge["id"],))["status"],
            "rejected")
        evidence = db.all(
            "SELECT e.evidence_type, e.signal, s.kind AS source_kind "
            "FROM evidence e JOIN sources s ON s.id = e.source_id "
            "ORDER BY e.created_at")
        self.assertEqual(
            [(row["evidence_type"], row["signal"], row["source_kind"]) for row in evidence],
            [("edge_triage", "retyped", "author"), ("edge_triage", "rejected", "author")])

    def test_changed_row_prevents_the_whole_batch(self):
        db, manuscript, choice, field, _ = self.fixture()
        triage.stage_decisions(db, manuscript, "concepts", [
            {"object_id": choice["id"], "action": "keep"},
            {"object_id": field["id"], "action": "keep"},
        ])
        db.update("concept_nodes", field["id"], {"notes": "changed elsewhere"})
        with self.assertRaises(triage.TriageConflict):
            triage.apply_selected(
                db, manuscript, "concepts", [choice["id"], field["id"]])
        choice_meta = json.loads(db.one(
            "SELECT metadata FROM concept_nodes WHERE id = ?", (choice["id"],))["metadata"])
        self.assertNotIn("confirmed", choice_meta)
        self.assertEqual(len(db.all("SELECT * FROM triage_drafts")), 2)

    def test_deselect_clears_pending_decision(self):
        db, manuscript, choice, *_ = self.fixture()
        triage.stage_decisions(db, manuscript, "concepts", [
            {"object_id": choice["id"], "action": "keep"},
        ])
        triage.unstage_decisions(db, manuscript, "concepts", [choice["id"]])
        self.assertFalse(db.all("SELECT * FROM triage_drafts"))

    def test_analysis_persists_batches_and_marks_old_results_outdated(self):
        db, manuscript, choice, field, _ = self.fixture()
        with mock.patch.object(triage_analysis, "_llm",
                               lambda config, profile: FakeLLM()):
            run = triage_analysis.start_run(
                db, manuscript, {"llm": {"enabled": True}}, "concepts",
                [choice["id"], field["id"]])
            first = triage_analysis.analyze_batch(
                db, manuscript, {}, run["run_id"], [choice["id"]])
            self.assertEqual(first["status"], "incomplete")
            self.assertEqual(first["completed"], 1)
            second = triage_analysis.analyze_batch(
                db, manuscript, {}, run["run_id"], [field["id"]])
        self.assertEqual(second["status"], "complete")
        snap = triage.snapshot(db, manuscript, "concepts")
        self.assertTrue(all(row["analysis"]["state"] == "current"
                            for row in snap["rows"]))
        (Path(manuscript["path"]) / "chapter.md").write_text(
            "# Choice\n\nChoice now makes a different distinction.\n", encoding="utf-8")
        dirty = triage.snapshot(db, manuscript, "concepts")
        self.assertTrue(dirty["manuscript_version"]["local_dirty"])
        self.assertTrue(all(row["analysis"]["state"] == "outdated"
                            for row in dirty["rows"]))
        api.collect(db, manuscript, {}, analyze=False)
        stale = triage.snapshot(db, manuscript, "concepts")
        self.assertTrue(all(row["analysis"]["state"] == "outdated"
                            for row in stale["rows"]))

    def test_analyzer_cannot_cite_another_objects_packet(self):
        profile = {"output_fields": [
            {"id": "score"}, {"id": "confidence"}, {"id": "why"},
            {"id": "evidence"},
        ]}
        raw = {"results": [
            {"object_id": "one", "score": 50, "confidence": 50, "why": "x",
             "evidence": [{"passage_id": "p-two", "reason": "wrong packet"}]},
            {"object_id": "two", "score": 50, "confidence": 50, "why": "x",
             "evidence": []},
        ]}
        passages = {
            "one": {"p-one": {"id": "p-one", "file": "a.md", "heading": "",
                                "text": "one"}},
            "two": {"p-two": {"id": "p-two", "file": "b.md", "heading": "",
                                "text": "two"}},
        }
        with self.assertRaises(RuntimeError):
            triage_analysis._validate_results(profile, ["one", "two"], raw, passages)

    def test_run_pins_the_graph_row_version(self):
        db, manuscript, choice, *_ = self.fixture()
        with mock.patch.object(triage_analysis, "_llm",
                               lambda config, profile: FakeLLM()):
            run = triage_analysis.start_run(
                db, manuscript, {"llm": {"enabled": True}}, "concepts", [choice["id"]])
            pinned_version = choice["version"]
            db.update("concept_nodes", choice["id"], {"notes": "changed after run start"})
            triage_analysis.analyze_batch(
                db, manuscript, {}, run["run_id"], [choice["id"]])
        assessment = db.one(
            "SELECT object_version FROM triage_assessments WHERE run_id = ?",
            (run["run_id"],))
        self.assertEqual(assessment["object_version"], pinned_version)
        row = triage.snapshot(db, manuscript, "concepts")["rows"][0]
        self.assertEqual(row["analysis"]["state"], "outdated")


class HelpTabTest(unittest.TestCase):
    """AB-6: the triage app's Help tab renders the canonical tutorial.

    The tab imports `docs/writing-essays-tutorial.md` at build time
    (vite `?raw`), so there is exactly one copy of the document and the
    tab cannot drift from it. These checks are string checks against the
    doc and the built single-file bundle — no browser, no npm."""

    REPO = Path(__file__).resolve().parent.parent
    DOC = REPO / "docs" / "writing-essays-tutorial.md"
    DIST = REPO / "authorlm" / "triage_dist" / "index.html"
    TITLE = "Writing essays with the beat loop — a tutorial"

    def test_canonical_tutorial_exists_and_is_substantial(self):
        self.assertTrue(self.DOC.is_file(), f"missing {self.DOC}")
        text = self.DOC.read_text(encoding="utf-8")
        self.assertGreater(len(text), 4000, "the tutorial is a stub")
        self.assertIn(self.TITLE, text)

    def test_built_dist_carries_the_tutorial(self):
        self.assertTrue(self.DIST.is_file(), f"missing {self.DIST}")
        built = self.DIST.read_text(encoding="utf-8")
        # `assertTrue`, not `assertIn`: the haystack is an 800 kB
        # single-file bundle and assertIn would print all of it.
        self.assertTrue(self.TITLE in built,
                        "the dist was not rebuilt after the doc changed "
                        "(cd web/triage-app && npm run build)")
        # A distinctive line from deep inside the tutorial: proves the
        # whole document was inlined, not just its title.
        self.assertTrue("Plan ratification is the fabrication guard" in built,
                        "only part of the tutorial reached the dist")
        # Two more, further apart in the document and from the sections
        # the author asked for by name, so a partial inline is caught
        # wherever it truncates.
        self.assertTrue("Sessions, intents, writeups — who owns what" in built,
                        "the ownership section did not reach the dist")
        self.assertTrue("Two essays at once" in built,
                        "the parallel-writeups section did not reach the dist")

    def test_dist_checksum_matches_the_doc(self):
        """AK: a content checksum of the embedded tutorial, structural
        rather than line-pinned.

        The three tripwires above catch a truncated or never-rebuilt dist
        only insofar as they happen to touch the lines those tripwires
        pin. A tutorial rewrite that keeps '# Writing essays with the beat
        loop' and the three quoted lines verbatim, but changes everything
        else, would sail past them on a stale dist. The vite build
        (web/triage-app/vite.config.ts, `tutorial-checksum` plugin) hashes
        the doc's raw bytes at BUILD time and stamps
        `<!-- tutorial-sha256:<hex> -->` into the emitted HTML. Recomputing
        that same hash here and requiring an exact match makes ANY edit to
        the doc without a rebuild fail this test, regardless of which
        lines moved — no line list to keep in sync."""
        self.assertTrue(self.DIST.is_file(), f"missing {self.DIST}")
        built = self.DIST.read_text(encoding="utf-8")
        doc_hash = hashlib.sha256(self.DOC.read_bytes()).hexdigest()
        expected = f"<!-- tutorial-sha256:{doc_hash} -->"
        self.assertIn(expected, built,
                      "the dist's tutorial checksum does not match the doc "
                      "— rebuild with (cd web/triage-app && npm run build)")

    def test_dist_checksum_catches_a_one_byte_doc_edit(self):
        """Negative evidence for the guard above: perturb the doc's hash by
        exactly one byte's worth of content and confirm the dist's
        checksum — computed from the REAL doc at build time — no longer
        matches. This is what 'structurally detectable' cashes out to: the
        comparison fails on ANY edit, not just ones that happen to touch a
        pinned line."""
        self.assertTrue(self.DIST.is_file(), f"missing {self.DIST}")
        built = self.DIST.read_text(encoding="utf-8")
        real_hash = hashlib.sha256(self.DOC.read_bytes()).hexdigest()
        perturbed = hashlib.sha256(
            self.DOC.read_bytes() + b"x").hexdigest()
        self.assertNotEqual(real_hash, perturbed)
        self.assertNotIn(f"<!-- tutorial-sha256:{perturbed} -->", built,
                         "a one-byte-perturbed hash must not match the "
                         "dist's real checksum")
        self.assertIn(f"<!-- tutorial-sha256:{real_hash} -->", built)

    def _flat(self) -> str:
        """The whole document with runs of whitespace collapsed to one
        space.

        A claim that spans a line break is still one claim. Asserting it
        against the raw text pins the author's line wrapping as well as
        their meaning, so an unrelated reflow of the paragraph fails a
        truth guard and the fix is to re-wrap rather than to think. Every
        multi-line claim below is checked against this."""
        return " ".join(self.DOC.read_text(encoding="utf-8").split())

    def _narrative(self) -> str:
        """The tutorial's MAIN FLOW: everything outside a `<details>` fold,
        minus the blocks that quote program output verbatim.

        A ```text block is quoted output — precisely 'what the author
        SEES', which belongs in the main flow and legitimately contains
        verb names, because that is what the program printed. Every OTHER
        fenced block is something the author would TYPE, so it is scanned
        like prose: the one command block the main flow carries (the `cp`
        backup in §3.2) must keep earning its place there rather than
        hiding behind a blanket exemption."""
        out, depth, quoted = [], 0, False
        for line in self.DOC.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("```"):
                if quoted:
                    quoted = False
                    continue
                if stripped == "```text":
                    quoted = True
                    continue
            if quoted:
                continue
            if stripped.startswith("<details"):
                depth += 1
            if depth == 0:
                out.append(line)
            if stripped.startswith("</details>"):
                depth = max(0, depth - 1)
        return "\n".join(out)

    def test_the_one_command_in_the_main_flow_is_the_deliberate_one(self):
        """The main flow carries exactly one thing the author types: the
        `cp` backup before a rewrite, which is there because the
        manuscript directory has no version control. Asserted by name so
        it cannot be quietly joined by others, and so `_narrative`'s
        output-block exemption cannot be widened into a hiding place."""
        body = self._narrative()
        fences = [line for line in body.splitlines()
                  if line.strip().startswith("```")]
        self.assertEqual(len(fences), 2,
                         f"expected one command block in the main flow, "
                         f"found {len(fences) // 2}: {fences}")
        self.assertIn("cp authorlm/manuscripts/", body)
        self.assertIn("_drafts/", body)

    def test_the_tutorial_keeps_the_machinery_out_of_the_main_flow(self):
        """The author's audience rule: the main flow is what they SAY and
        SEE, in layperson terms; every command lives in a collapsed
        `<details>` box below the step it belongs to. Checked structurally
        rather than by eye, because prose is the one thing that rots back
        toward jargon on every edit."""
        body = self._narrative()
        for pattern, token in (
                (r"\bauthorlm \w", "authorlm <verb>"),
                (r"(?<![\w-])--[a-z]", "a --flag"),
                (r"\bwrite (start|plan|propose|accept|reject|complete"
                 r"|abandon|status|digest|learn)\b", "a write verb"),
                (r"\bsummarize (rebuild|status|show)\b", "a summarize verb"),
                (r"\bintent (declare|complete|list|abandon)\b",
                 "an intent verb"),
                # "style guide" is ordinary English and stays; `style
                # attach` is the verb.
                (r"\bstyle attach\b", "a style verb")):
            hit = re.search(pattern, body)
            self.assertIsNone(
                hit,
                f"{token} appears in the tutorial's MAIN FLOW "
                f"({hit.group(0)!r} at ...{body[max(0, hit.start() - 70):hit.end() + 40]!r}) "
                f"— commands and flags belong inside an 'Under the hood' "
                f"<details> box" if hit else "")
        self.assertGreater(
            self.DOC.read_text(encoding="utf-8").count("<summary>"), 10,
            "the tutorial lost its Under-the-hood boxes")

    def test_llm_mentions_are_framed_by_purpose(self):
        """The author's second audience rule: a model call is described by
        the JOB it does ('a model call happens here, to do X'), never by
        mechanism-negation ('no drafting verb makes an LLM call'), which
        told them nothing they could use."""
        text = self.DOC.read_text(encoding="utf-8")
        for banned in ("No drafting verb makes an LLM call",
                       "makes no LLM call",
                       "zero LLM"):
            self.assertNotIn(banned, text,
                             f"{banned!r}: mechanism-negation, not purpose")
        for claim in ("Reading the finished prose for concepts",
                      "Writing each essay's compressed summary"):
            self.assertIn(claim, text)
        # AH-2: the summaries and the critique editor ran on Sonnet until
        # the billing ruling of 2026-08-30 removed the two [critique]
        # overrides. The doc may still NAME an Anthropic model in the
        # history of that ruling, or as the recipe for restoring the billed
        # drafting mode; what it may not do is present one as a model any
        # job uses NOW. Pinned structurally — no row of the model table may
        # carry an anthropic model in its Model column — so this survives
        # every future change of which cheap model does which job, and
        # still catches the one thing the ruling was about.
        self.assertNotIn("| `anthropic/", text,
                         "a model table row names an anthropic model as a "
                         "current setting; the ruling took those out")

    def test_the_model_table_states_the_ACTIVE_critique_models(self):
        """AI: the fold described a configuration that had already moved.

        It said the `summarizer_model` line was "commented out while a
        temperature fix lands in the model client", that the summaries
        therefore ran on the `[llm]` default "at the higher of the two
        prices", and that "the critique editor has no override at all".
        All three stopped being true when `[critique]` was activated on
        2026-08-30 — the author was reading a pending state as a current
        one, on the page that exists to tell them which model does what,
        and being quoted the wrong price for a job they run constantly.

        Same defect class as the spend count below, and pinned the same
        way: the doc does not get to describe a configuration the
        configuration does not have. Both stale halves fail here — a
        pending-state sentence, and an editor row on the general default
        while `[critique] editor_model` names something else."""
        text = self.DOC.read_text(encoding="utf-8")
        flat = self._flat()
        for stale in ("commented out",
                      "until then the summaries run on the",
                      "The critique editor has no override at all",
                      "Uncommenting one line"):
            self.assertNotIn(stale, flat,
                             f"{stale!r} describes a configuration that was "
                             "activated on 2026-08-30")
        self.assertIn("| The critique editor pass | `openai/gpt-5.6-luna` | "
                      "`[critique] editor_model` |", text,
                      "the editor row still shows the general default")
        self.assertIn("both are **live since 2026-08-30**", flat)
        # The temperature key explained by what it DOES, not by the client
        # bug it once worked around.
        self.assertIn('`temperature = "vendor-default"`', text)
        # AI: and the registry that makes it belt-and-braces rather than
        # load-bearing, named where the author would go looking.
        self.assertIn("MODEL_PROFILES", text)
        self.assertIn("every request is shaped from it before it is sent",
                      flat)
        # An unknown model is not a refusal — the sentence that keeps the
        # author from reading the table as a whitelist.
        self.assertIn("A model the table has never seen still works", flat)

    def test_the_spending_moments_are_counted_truthfully(self):
        """Truth-in-instrument. The doc used to claim THREE spending
        moments and omit the most frequent one: `write reject` passes an
        LLMClient into `record_review`, which distils every explained
        rejection into a candidate belief — and `--reason` is mandatory on
        a rejection, so every rejection spends. A reworded acceptance the
        author explains does the same. A tutorial that undercounts what an
        instrument costs is the defect, not the wording.

        The count went to FIVE when `write draft` landed: drafting the beat
        was a model call, and the largest one the system made.

        AH-2 returns it to FOUR, and for the opposite reason to the one
        that made four wrong before. The billing ruling of 2026-08-30
        deleted `[writing]` from the shipped config, so beat drafting bills
        nothing: what remains is verdict distillation, completion
        extraction, the summary rebuilds and intent completion, all on the
        cheap `[llm]` default. Both a stale FIVE (which invoices the author
        for drafting they are not being charged for) and a stale THREE
        (which still omits verdict distillation) fail here. The count
        tracks the shipped configuration; it is not a number the document
        gets to round."""
        text = self.DOC.read_text(encoding="utf-8")
        flat = self._flat()
        self.assertNotIn("Three moments", text,
                         "the three-moment count omits verdict distillation")
        self.assertNotIn("Five moments", text,
                         "the five-moment count bills for beat drafting, "
                         "which the shipped config does not do")
        self.assertIn("Four moments call out to a model", text)
        # Beat drafting named as EXCLUDED, in as many words — an author who
        # is told a count but not what fell out of it cannot check it.
        self.assertIn("Drafting the beats is not one of them", flat)
        self.assertIn("only if you turn the pinned drafting model back on",
                      flat)
        # The billed mode still named, still by its price, so restoring it
        # is a decision made with the number in view rather than a switch
        # flipped blind. Present tense ("makes") is banned: it would tell
        # the author they are paying for it right now.
        self.assertNotIn("the largest single call the system makes", text)
        self.assertIn("the largest single call the system can make", flat)
        self.assertIn("[writing] model", text)
        # The frequent one, named as such and framed by its purpose.
        self.assertIn("Every rejection, and every reworded acceptance you "
                      "explain", flat)
        self.assertIn("every rejection spends", text)
        # ...and the plain acceptance that does NOT spend, so the reader
        # can tell the two apart. Both of the old sentences priced in a
        # billed draft — the acceptance "on top of the draft", and the
        # rejection paying "twice" because the redraft was a second call.
        # Neither is true when the redraft is written in the conversation.
        self.assertNotIn("A plain acceptance adds nothing on top of the "
                         "draft", text)
        self.assertNotIn("A rejection costs twice", text)
        self.assertIn("A plain acceptance costs nothing", text)
        self.assertIn("A rejection is the one verdict that spends, and it "
                      "spends once", flat)

    def test_the_doc_says_where_the_writing_comes_from(self):
        """The tutorial's §0 told the author, in its own words, that the
        drafting step 'has no model setting of its own: there is nothing to
        configure, because the conversation is the drafting'. That was true
        when written and became false the day `write draft` landed — and it
        is the single most load-bearing sentence in the document, because
        it is what the author reasons about cost, reproducibility and blame
        from. A stale answer here is worse than no answer: it tells them to
        look in the wrong place when a beat comes back wrong.

        AH-2 moves it a second time, back to the conversation — but NOT
        back to that original sentence. The billing ruling of 2026-08-30
        removed `[writing]` from the shipped config, so the conversation
        drafts by default again; what stayed false throughout is 'there is
        nothing to configure'. There IS: a registered prompt the author can
        read and edit, a payload they can print, and a `[writing]` section
        that turns a billed pinned-model mode on. Those bans stand. What
        the doc must now say instead is where the draft comes from (here),
        what it costs (nothing beyond the subscription), what disciplines
        it (the printed payload and the prompt), and that the billed mode
        exists, is off, and is restorable."""
        text = self.DOC.read_text(encoding="utf-8")
        flat = self._flat()
        for stale in ("there is nothing to configure",
                      "because the conversation is the drafting",
                      "the drafting step has no model setting",
                      "nothing to set: the drafting is the conversation",
                      "Drafting the beats costs nothing",
                      # ...and the claim AH-2 supersedes: a pinned model of
                      # its own is exactly what a beat no longer has.
                      "Each beat is drafted by a model of its"):
            self.assertNotIn(stale, text, f"{stale!r} is no longer true")
        self.assertIn("Each beat is drafted in this conversation", flat)
        self.assertIn("authorlm/prompts/beat-draft.md", text)
        # The cost, qualified. A bare "costs nothing" is banned above; the
        # subscription is what makes the qualified claim true, and it is
        # the thing the author is actually paying.
        self.assertIn("covered by the subscription you already pay for",
                      flat)
        # The discipline, so "drafted in the conversation" cannot be read
        # as "drafted from whatever the conversation was doing".
        self.assertIn("against that payload and against nothing else", flat)
        # The billed mode: named, priced, and stated to be OFF. A doc that
        # simply stopped mentioning it would leave the author unable to
        # restore it, and unable to explain a bill if someone else did.
        self.assertIn("bills the Anthropic API per beat", text)
        self.assertIn("switched off in the configuration", text)
        self.assertIn("restore recipe", text)
        # F3: the end-of-essay calls do NOT degrade alike.
        self.assertIn("fails loudly", text)
        self.assertNotIn("all three still succeed", text)

    def test_the_session_claim_matches_the_actual_dedup(self):
        """F4: the doc claimed a long session 'quietly caps what the system
        can learn'. It does not — `beliefs._derive_supporting` groups
        evidence by (session, target), so two verdicts on two different
        beats are two different targets and both count, however long the
        session runs. Two sections drew opposite conclusions from the same
        false premise."""
        text = self.DOC.read_text(encoding="utf-8")
        for banned in ("quietly caps what",
                       "counted once per session",
                       "only on independent evidence\nfrom separate sessions",
                       "chopping a session in half is the"):
            self.assertNotIn(banned, text, f"{banned!r} is not true")
        self.assertIn("both count, however long the session runs", text)
        self.assertIn("does not cap what the loop learns", text)

    def test_the_help_tab_renders_details_as_real_folds(self):
        """The author's rule 3 has to hold where they actually read it.
        The renderer used to DISCARD the <details>/<summary> tags, so the
        Help tab showed every command expanded inline — the document was
        arranged one way and displayed the other."""
        src = (self.REPO / "web" / "triage-app" / "src"
               / "markdown.tsx").read_text(encoding="utf-8")
        self.assertNotIn(
            'if (trimmed === "<details>" || trimmed === "</details>") '
            "{ flush(); continue; }", src,
            "the renderer is discarding <details> again")
        self.assertIn("<details className=\"doc-fold\"", src,
                      "the renderer no longer builds a real <details>")
        built = self.DIST.read_text(encoding="utf-8")
        self.assertTrue("doc-fold" in built,
                        "the fold class did not reach the dist — rebuild "
                        "(cd web/triage-app && npm run build)")

    def test_built_dist_carries_the_help_tab_itself(self):
        built = self.DIST.read_text(encoding="utf-8")
        self.assertTrue("help-doc" in built,
                        "the Help tab's markup is missing from the dist")
        self.assertTrue("AUTHORLM / HELP" in built,
                        "the Help tab's kicker is missing from the dist")


class TriageServerWireContractTest(unittest.TestCase):
    """T3 (risk-register §3): triage_server.make_handler had zero test
    references anywhere in the suite. Pins the actual HTTP wire contract a
    live triage_server process exposes: GET /health, the POST /api/triage
    envelope shape, and the 409 status the handler maps a TriageConflict
    onto — as opposed to calling triage.py's functions directly, which
    every other test in this file does and none of which exercises
    triage_server.py itself."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="authorlm-triage-http-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # api.open_db (which the handler calls per request via
        # triage_transport.dispatch) resolves to <workspace>/.authorlm/
        # authorlm.db — a different path than manuscript_fixture's flat
        # root/authorlm.db used elsewhere in this file, so this fixture is
        # built directly against that convention instead.
        self.db = api.open_db(str(self.root))
        self.addCleanup(self.db.conn.close)
        manuscript_path = self.root / "book"
        manuscript_path.mkdir()
        (manuscript_path / "chapter.md").write_text(
            "# Choice\n\nChoice makes distinction possible.\n",
            encoding="utf-8")
        self.manuscript = api.register_manuscript(
            self.db, "book", str(manuscript_path))

        self.server = http.server.HTTPServer(
            ("127.0.0.1", 0),
            triage_server.make_handler(str(self.root), "book"))
        threading.Thread(target=self.server.serve_forever,
                         daemon=True).start()
        # LIFO: shutdown (stop serve_forever's loop) must run before
        # server_close (close the socket), so register close first.
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def _post(self, method: str, params: dict):
        req = urllib.request.Request(
            f"{self.base_url}/api/triage",
            data=json.dumps({"method": method, "params": params}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def test_health(self):
        with urllib.request.urlopen(f"{self.base_url}/health") as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(json.loads(resp.read()), {"ok": True})

    def test_triage_envelope_on_success(self):
        status, body = self._post("snapshot", {"triage_type": "concepts"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"], body)
        self.assertIn("result", body)
        self.assertIn("rows", body["result"])

    def test_triage_conflict_maps_to_409(self):
        concept = ko_fields("cn")
        concept.update(
            manuscript_id=self.manuscript["id"], name="Choice",
            kind="concept", status="realized", introduced_in="chapter.md",
            notes="Choice notes", aliases="[]",
            source_id=self.db.source("system"),
            metadata=json.dumps({"origin": "extracted"}))
        self.db.insert("concept_nodes", concept)
        draft = ko_fields("td")
        draft.update(
            manuscript_id=self.manuscript["id"], triage_type="concepts",
            object_id=concept["id"], object_version=concept["version"],
            action="confirm", parameters="{}", reason=None,
            updated_at=concept["created_at"])
        self.db.insert("triage_drafts", draft)
        # Someone else changed the concept after the draft was staged,
        # bumping its version — the staged draft is now stale.
        self.db.update("concept_nodes", concept["id"],
                       {"notes": "changed elsewhere"})

        status, body = self._post(
            "apply", {"triage_type": "concepts",
                     "object_ids": [concept["id"]]})
        self.assertEqual(status, 409, body)
        self.assertFalse(body["ok"])
        self.assertIn("conflicts", body)
        self.assertEqual(body["conflicts"][0]["object_id"], concept["id"])


if __name__ == "__main__":
    _assert_offline()
    unittest.main()
