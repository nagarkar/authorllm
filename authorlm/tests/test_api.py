"""API-layer tests + CLI/MCP parity checklist (P11).

Exercises `authorlm.api` directly (the logic layer beneath every surface)
and asserts the MCP server exposes the expected hand-curated tool set.

Run: python3 tests/test_api.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import api  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


def main_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-api-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "01-choice.md").write_text(
            "# Opening\n\nGravity is discussed early.\n\nEvery act begins with choice.\n"
        )
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms)])

        db = api.open_db(str(ws))
        manuscript = api.get_manuscript(db)
        check("get_manuscript resolves the single manuscript",
              manuscript["name"] == "book")
        check("resolve_file maps a bare filename",
              api.resolve_file(db, "01-choice.md")["name"] == "book")
        check("resolve_file rejects foreign paths",
              api.resolve_file(db, "/tmp/nowhere.md") is None)

        session, created = api.ensure_session(db, manuscript, client_id="mcp-test123")
        check("ensure_session lazily opens and stamps client id", created
              and "mcp-test123" in (session["metadata"] or ""))
        session2, created2 = api.ensure_session(db, manuscript)
        check("ensure_session reuses the active session",
              not created2 and session2["id"] == session["id"])

        api.add_concept(db, manuscript, "Gravity")
        api.add_concept(db, manuscript, "Choice")
        api.link_concepts(db, manuscript, "Choice", "permits", "Gravity")

        report = api.collect(db, manuscript, {})
        check("collect returns a structured report",
              report["version_no"] == 1 and report["transitions"]
              and any(n["name"] == "Gravity" for n in report["realized"]))
        check("collect reports prerequisite gaps structurally",
              len(report["gaps_after"]) == 1
              and report["gaps_after"][0]["second"] == "Gravity")

        declared = api.declare_intent(db, manuscript, "Expand on gravity")
        check("declare_intent returns intent + preview",
              declared["intent"]["statement"] == "Expand on gravity"
              and declared["preview"]["matched"][0]["name"] == "Gravity")
        typo = api.intent_preview(db, manuscript, "Discuss gravety maybe")
        check("preview fuzzy-suggests on typos", "Gravity" in typo["suggestions"])

        guidance = api.guide(db, manuscript, session)
        check("guide returns structured suggestions",
              not guidance["abstained"] and guidance["suggestions"])
        reviewed = api.review(db, manuscript, session, 1, "accepted",
                              "Right place for it")
        check("review records and returns evidence effects",
              reviewed["guidance"]["batch_index"] == 1)
        try:
            api.review(db, manuscript, session, 1, "accepted", None)
            check("double review raises", False)
        except ValueError:
            check("double review raises", True)

        briefing = api.get_briefing(db, manuscript)
        check("briefing is a serializable dict",
              "learning_velocity" in briefing)
        diff = api.diff_versions(db, manuscript)
        check("diff_versions returns per-file line lists",
              diff["new"] == "v1" and "01-choice.md" in diff["files"])

        graph = api.list_concepts(db, manuscript)
        check("list_concepts returns nodes and named edges",
              len(graph["nodes"]) == 2
              and graph["edges"][0]["from_name"] == "Choice")
        api.confirm_concept(db, manuscript, "Gravity", kind="definition")
        check("confirm_concept retypes",
              api.show_concept(db, manuscript, "Gravity")["node"]["kind"] == "definition")

        # --- concept/edge/proposal curation via api (the CLI has its own
        # code paths for these, so api.* itself was never exercised) ---
        api.add_concept(db, manuscript, "Scratch")
        retired = api.retire_concept(db, manuscript, "Scratch")
        check("retire_concept retires a concept with no edges",
              retired == {"name": "Scratch", "edges_retired": 0})
        try:
            api.retire_concept(db, manuscript, "Scratch")
            check("retiring an already-retired concept raises", False)
        except ValueError as err:
            check("retiring an already-retired concept raises", "already retired" in str(err))
        try:
            api.retire_concept(db, manuscript, "Nobody")
            check("retiring an unknown concept raises", False)
        except LookupError:
            check("retiring an unknown concept raises", True)

        from authorlm import concepts as cg

        api.add_concept(db, manuscript, "Freedom")
        inferred = cg.link_concepts(db, manuscript["id"], "Gravity", "supports", "Freedom",
                                    status="inferred")
        confirmed = api.confirm_edge(db, manuscript, inferred["id"][:8])
        check("confirm_edge declares an inferred edge",
              confirmed == {"from_name": "Gravity", "relation": "supports", "to_name": "Freedom"})
        check("confirm_edge persisted the status change",
              api.show_concept(db, manuscript, "Freedom")["edges"][0]["status"] == "declared")
        inferred2 = cg.link_concepts(db, manuscript["id"], "Freedom", "supports", "Choice",
                                     status="inferred")
        rejected = api.reject_edge(db, manuscript, inferred2["id"][:8])
        check("reject_edge rejects an inferred edge",
              rejected == {"from_name": "Freedom", "relation": "supports", "to_name": "Choice"})
        try:
            api.confirm_edge(db, manuscript, "zzzzzzzz")
            check("confirming an unknown edge prefix raises", False)
        except LookupError:
            check("confirming an unknown edge prefix raises", True)

        from authorlm import proposals as prop

        open_proposal = prop.create(db, manuscript["id"], "revival", "Scratch",
                                    {"name": "Scratch"})
        listed = api.list_proposals(db, manuscript)
        check("list_proposals surfaces an open proposal with summary/details",
              any(p["id"] == open_proposal["id"] and "revive retired concept" in p["summary"]
                  for p in listed))
        try:
            api.resolve_proposal(db, manuscript, open_proposal["id"][:8], "bogus")
            check("resolving a proposal with an unknown action raises", False)
        except ValueError:
            check("resolving a proposal with an unknown action raises", True)
        try:
            api.resolve_proposal(db, manuscript, "zzzzzzzz", "dismiss")
            check("resolving an unknown proposal prefix raises", False)
        except LookupError:
            check("resolving an unknown proposal prefix raises", True)
        resolved = api.resolve_proposal(db, manuscript, open_proposal["id"][:8], "dismiss",
                                        "not needed")
        check("resolve_proposal dismisses and returns a message",
              resolved["proposal_id"] == open_proposal["id"] and resolved["message"])
        check("dismissed proposal no longer appears in list_proposals",
              not any(p["id"] == open_proposal["id"] for p in api.list_proposals(db, manuscript)))

        closed = api.close_session(db, manuscript)
        check("close_session ends the active session",
              api.status(db, manuscript)["session"] is None
              and closed["id"] == session["id"])

        # --- Google Docs bridge: normalizer + stub-service round trip ---
        from authorlm.gdocs import normalize_markdown, pull_doc, push_doc

        messy = ("# Title\r\n\r\n\r\n\r\nSome \\-escaped \\. text here.   \n"
                 "*  a bullet\n\nEnds with nbsp.")
        clean = normalize_markdown(messy)
        check("normalizer canonicalizes Docs-export quirks",
              clean == ("# Title\n\nSome -escaped . text here.\n"
                        "- a bullet\n\nEnds with nbsp.\n"), repr(clean))
        check("normalizer is idempotent", normalize_markdown(clean) == clean)

        class StubFiles:
            def __init__(self):
                self.docs = {}
                self.counter = 0

            def create(self, body=None, media_body=None, fields=None):
                self.counter += 1
                doc_id = f"doc-{self.counter}"
                self.docs[doc_id] = media_body
                return type("R", (), {"execute": lambda s, d=doc_id: {"id": d}})()

            def update(self, fileId=None, media_body=None):
                self.docs[fileId] = media_body
                return type("R", (), {"execute": lambda s: {}})()

            def __init_export__(self):
                pass

            export_payload = b"# Title\n\nEdited in Docs \\- with escapes.\r\n"

            def export(self, fileId=None, mimeType=None):
                payload = self.export_payload
                return type("R", (), {"execute": lambda s: payload})()

        class StubDrive:
            def __init__(self):
                self._files = StubFiles()

            def files(self):
                return self._files

        stub = StubDrive()
        pushed = push_doc(db, manuscript, "01-choice.md", service=stub)
        check("push creates a per-manuscript folder, then the Doc",
              pushed["created"] and pushed["doc_id"] == "doc-2")
        manuscript = api.get_manuscript(db)  # refresh metadata
        from authorlm.gdocs import doc_status
        check("push mapping persisted with checkout and folder id",
              doc_status(db, manuscript)["01-choice.md"]["checked_out"] is True
              and doc_status(db, manuscript)["_folder_id"] == "doc-1")
        pulled = pull_doc(db, manuscript, "01-choice.md", service=stub)
        check("pull writes normalized export and clears checkout",
              pulled["changed"]
              and (ms / "01-choice.md").read_text()
              == "# Title\n\nEdited in Docs - with escapes.\n"
              and doc_status(db, manuscript)["01-choice.md"]["checked_out"] is False)
        pushed2 = push_doc(db, manuscript, "01-choice.md", service=stub)
        check("second push updates the same Doc (no new folder)",
              not pushed2["created"] and pushed2["doc_id"] == "doc-2"
              and stub._files.counter == 2)

        # Two-sided edit: local changed since push AND Doc differs → conflict.
        (ms / "01-choice.md").write_text("# Title\n\nLocal divergence.\n")
        manuscript = api.get_manuscript(db)
        try:
            pull_doc(db, manuscript, "01-choice.md", service=stub)
            check("two-sided edits raise a conflict", False)
        except ValueError as err:
            check("two-sided edits raise a conflict", "conflict" in str(err))
        forced = pull_doc(db, manuscript, "01-choice.md", service=stub, force=True)
        check("pull --force takes the Doc's version",
              forced["changed"]
              and "Edited in Docs" in (ms / "01-choice.md").read_text())

        # --- session-start reconciliation: all four outcomes ---
        from authorlm.gdocs import reconcile

        manuscript = api.get_manuscript(db)
        report = reconcile(db, manuscript, stub)
        check("reconcile: in sync after a pull",
              report["in_sync"] == ["01-choice.md"] and not report["conflicts"],
              str(report))

        (ms / "01-choice.md").write_text("# Title\n\nLocal-only progress.\n")
        report = reconcile(db, manuscript, stub)
        check("reconcile: only-local change auto-pushes",
              report["pushed"] == ["01-choice.md"], str(report))
        # Drive now reflects the push; the next reconcile sees sync.
        stub._files.export_payload = b"# Title\n\nLocal-only progress.\n"
        report = reconcile(db, manuscript, stub)
        check("reconcile: after push, next start is in sync",
              report["in_sync"] == ["01-choice.md"], str(report))
        stub._files.export_payload = b"# Title\n\nDoc-only revision now.\n"
        report = reconcile(db, manuscript, stub)
        check("reconcile: only-Doc change auto-pulls",
              report["pulled"] == ["01-choice.md"]
              and "Doc-only revision" in (ms / "01-choice.md").read_text(),
              str(report))
        (ms / "01-choice.md").write_text("# Title\n\nBoth sides now differ.\n")
        stub._files.export_payload = b"# Title\n\nDoc went another way.\n"
        report = reconcile(db, manuscript, stub)
        check("reconcile: two-sided edits conflict, files untouched",
              report["conflicts"] == ["01-choice.md"]
              and "Both sides now differ" in (ms / "01-choice.md").read_text(),
              str(report))

        # --- single-manuscript export (doc create-manuscript) ---
        from authorlm.export import combined_markdown, export_manuscript

        (ms / "00-intro.md").write_text("# Intro\n\nWelcome.\n")
        (ms / "toc.md").write_text("- 01-choice\n- 00-intro\n")
        manuscript = api.get_manuscript(db)
        text, order, unlisted = combined_markdown(manuscript)
        check("combined markdown follows toc.md reading order",
              order == ["01-choice.md", "00-intro.md"] and not unlisted
              and text.index("Both sides") < text.index("Welcome"), text)

        local_only = export_manuscript(db, manuscript, service=None)
        export_path = ms / "_exports" / "book.md"
        check("export writes _exports/<name>.md even without Drive",
              local_only["doc_id"] is None and export_path.read_text() == text)

        exported = export_manuscript(db, manuscript, service=stub)
        check("export creates the manuscript Doc in the existing folder",
              exported["created"] and exported["doc_id"] == "doc-3"
              and stub._files.counter == 3)
        exported2 = export_manuscript(db, manuscript, service=stub)
        check("re-export updates the same Doc (no new one)",
              not exported2["created"] and exported2["doc_id"] == "doc-3"
              and stub._files.counter == 3)

        # A Doc deleted by hand in Drive is transient — recreated on export.
        class Gone(Exception):
            resp = type("R", (), {"status": 404})()

        original_update = stub._files.update
        stub._files.update = lambda fileId=None, media_body=None: (_ for _ in ()).throw(Gone())
        exported3 = export_manuscript(db, manuscript, service=stub)
        stub._files.update = original_update
        check("a hand-deleted export Doc is recreated",
              exported3["created"] and exported3["doc_id"] == "doc-4")

        titled = export_manuscript(db, manuscript, service=None, title="My Book")
        check("retitled export replaces the stale local file",
              (ms / "_exports" / "My Book.md").exists()
              and not export_path.exists()
              and titled["doc_title"] == "My Book")

        report = reconcile(db, api.get_manuscript(db), stub)
        check("reconcile never touches the push-only export entry",
              "_export" not in report["in_sync"] + report["pulled"]
              + report["pushed"] + report["conflicts"]
              + [e["file"] for e in report["errors"]], str(report))

        # --- prerequisite-gap first mentions: terms of art, not casual words ---
        # Repro from improvement task it-e34cf5227223: 'wandered through time
        # and space' must not count as the first mention of concept 'Space'.
        from authorlm.guidance import _first_mentions
        gap_files = {"01.md": (
            "The Dead wandered through time and space in search of redemption.\n\n"
            "The mutable we name the realm of qualities.\n\n"
            "When ye discern difference in position and direction, ye name it Space.\n"
        )}
        gap_names = {"n-space": "Space", "n-realm": "Realm of Qualities"}
        mentions = _first_mentions(gap_files, gap_names)
        check("casual word use does not count as a concept mention",
              mentions["n-realm"] < mentions["n-space"],
              f"positions: {mentions}")
        check("multi-word names still match case-insensitively",
              "n-realm" in mentions)
        check("a concept only ever used casually has no first mention",
              "n-space" not in _first_mentions(
                  {"01.md": "They wandered through time and space.\n"},
                  {"n-space": "Space"}))
        heading_pos = _first_mentions(
            {"01.md": "## Discernment\n\nVirtue is chosen for effectiveness.\n",
             "02.md": "Discernment is the measure of distinctions.\n"},
            {"n-disc": "Discernment"})
        check("capitalization from headings and sentence starts counts",
              "n-disc" in heading_pos and heading_pos["n-disc"] < 20)
        from authorlm.guidance import PREREQUISITE_FIRST
        check("leads_to is an ordering relation (cause introduced before effect)",
              "leads_to" in PREREQUISITE_FIRST)

        # --- self-improvement tasks ---
        try:
            api.file_improvement(db, title="x", evidence="e", given="g",
                                 observed="", expected="v")
            check("file_improvement rejects an empty repro field", False)
        except ValueError:
            check("file_improvement rejects an empty repro field", True)

        task = api.file_improvement(
            db, title="Gap false positive on common words",
            evidence="'space' matched casually at file.md:9",
            given="a manuscript using 'time and space' casually before the concept intro",
            observed="prerequisite gap reported for Space",
            expected="casual word use must not count as a concept mention",
            manuscript=manuscript,
        )
        check("file_improvement stamps manuscript and session provenance",
              task["manuscript_id"] == manuscript["id"]
              and task["status"] == "open")
        import json as _json
        stamp = _json.loads(task["metadata"])
        latest_version = db.one(
            "SELECT id FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no DESC LIMIT 1", (manuscript["id"],))
        check("filing stamps the current manuscript version (Time Machine)",
              stamp.get("manuscript_version_id") == latest_version["id"]
              and stamp.get("manuscript_version_no") is not None)
        check("filing stamps the source tree's git commit (Time Machine)",
              len(stamp.get("git_commit", "")) == 40
              and "git_dirty" in stamp)
        check("improvement tasks are listed as active",
              any(t["id"] == task["id"] for t in api.list_improvements(db, "active")))

        bundled = api.improvement_bundle(db, task["id"])
        check("improvement_bundle is self-contained and marks in_progress",
              bundled["status"] == "in_progress"
              and "file.md:9" in bundled["bundle"]
              and "casual word use must not count" in bundled["bundle"]
              and "tests/test_api.py" in bundled["bundle"])
        check("bundle carries the filing-time context (Time Machine)",
              stamp["git_commit"][:12] in bundled["bundle"]
              and latest_version["id"] in bundled["bundle"]
              and "git diff" in bundled["bundle"])
        rebundled = api.improvement_bundle(db, task["id"])
        check("re-emitting a bundle never regresses status",
              rebundled["status"] == "in_progress")

        try:
            api.resolve_improvement(db, task["id"], "close")
            check("close without a proposed resolution is refused", False)
        except ValueError:
            check("close without a proposed resolution is refused", True)
        api.resolve_improvement(db, task["id"], "propose",
                                note="tightened concept_pattern; test_gap_casual_words")
        closed = api.resolve_improvement(db, task["id"], "close")
        check("propose then close resolves with the fix on record",
              closed["status"] == "resolved")
        try:
            api.resolve_improvement(db, task["id"], "dismiss", note="n/a")
            check("a settled task cannot be re-transitioned", False)
        except ValueError:
            check("a settled task cannot be re-transitioned", True)

        task2 = api.file_improvement(db, title="t2", evidence="e2", given="g2",
                                     observed="o2", expected="x2")
        try:
            api.resolve_improvement(db, task2["id"], "dismiss")
            check("dismissal without a reason is refused", False)
        except ValueError:
            check("dismissal without a reason is refused", True)
        dismissed = api.resolve_improvement(db, task2["id"], "dismiss",
                                            note="intended behavior")
        check("dismissal with a reason lands with the reason on record",
              dismissed["status"] == "dismissed"
              and api.list_improvements(db, "dismissed")[0]["resolution"]
              == "intended behavior")
        check("improvement tasks never leak into briefings",
              "improvement" not in str(api.get_briefing(db, manuscript)).lower())

        # --- CLI/MCP parity checklist ---
        from authorlm.mcp_server import mcp
        tool_names = {t.name for t in mcp._tool_manager.list_tools()}
        expected = {
            "list_manuscripts", "resolve_file", "get_status", "declare_intent",
            "list_intents", "complete_intent", "abandon_intent",
            "collect_revision", "get_guidance", "review_suggestion",
            "get_briefing", "get_concepts", "add_concept", "link_concepts",
            "confirm_concept", "retire_concept", "confirm_edge", "reject_edge",
            "list_proposals", "resolve_proposal", "analyze_episodes",
            "diff_versions", "list_policies", "close_session",
            "extract_concepts", "get_plan", "get_doc_links",
            "file_improvement", "list_improvements", "improvement_bundle",
            "resolve_improvement",
        }
        check("MCP exposes the full hand-curated tool set",
              expected == tool_names,
              f"missing: {expected - tool_names}; extra: {tool_names - expected}")
        for tool in mcp._tool_manager.list_tools():
            check_desc = bool(tool.description and len(tool.description) > 40)
            assert check_desc, f"tool {tool.name} lacks a real description"
        check("every MCP tool carries a substantive description", True)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nAll {PASSED} checks passed.")


if __name__ == "__main__":
    main_test()
