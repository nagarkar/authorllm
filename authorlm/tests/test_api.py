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
from authorlm.db import loads  # noqa: E402

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

        # --- export settings: quotes/backslashes round-trip through TOML ---
        from authorlm.export import load_settings, set_setting

        set_setting(manuscript, "title", 'A "Great" Book')
        set_setting(manuscript, "reference_docx", "C:\\styles\\ref.docx")
        check("quotes and backslashes in settings round-trip through TOML",
              load_settings(manuscript)["title"] == 'A "Great" Book'
              and load_settings(manuscript)["reference_docx"]
              == "C:\\styles\\ref.docx")
        # Reset: the pandoc export test below runs on this same
        # manuscript and must not inherit the fake reference docx.
        set_setting(manuscript, "reference_docx", "")

        # --- trace log: shape, error truncation, rotation, never-raises ---
        import json as _tjson

        from authorlm import tracelog

        trace_ws = root / "trace-ws"
        tracelog.record("get_status", surface="cli", workspace=str(trace_ws),
                        manuscript="book", duration_ms=12, ok=True)
        trace_path = tracelog.log_dir(str(trace_ws)) / "trace.jsonl"
        entry = _tjson.loads(trace_path.read_text().splitlines()[0])
        check("record() writes one well-formed JSONL entry",
              entry["verb"] == "get_status" and entry["surface"] == "cli"
              and entry["manuscript"] == "book" and entry["duration_ms"] == 12
              and entry["ok"] is True and "ts" in entry, entry)

        tracelog.record("get_briefing", surface="mcp", workspace=str(trace_ws),
                        ok=False, error="boom" * 200)
        entries = [_tjson.loads(line)
                  for line in trace_path.read_text().splitlines()]
        check("errors are recorded and truncated to 500 chars",
              entries[-1]["ok"] is False and len(entries[-1]["error"]) == 500)

        trace_path.write_text("x" * (tracelog.MAX_BYTES + 1))
        tracelog.record("collect_revision", surface="cli",
                        workspace=str(trace_ws))
        rotated = trace_path.with_suffix(".jsonl.1")
        check("oversized trace rotates to .jsonl.1; a fresh file starts",
              rotated.exists() and rotated.stat().st_size > tracelog.MAX_BYTES
              and len(trace_path.read_text().splitlines()) == 1)

        blocked = root / "blocked-file"
        blocked.write_text("not a directory")
        tracelog.record("get_status", surface="cli",
                        workspace=str(blocked / "ws"))
        check("a broken trace destination never raises", True)
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

        # --- massive_deletions boundary conditions (auto-collect safety net) ---
        # Isolated manuscript: the shrink_ratio/min_chars math needs an exact,
        # uncluttered file history.
        md_root = root / "md-ws"
        md_ms = md_root / "manuscript"
        md_ms.mkdir(parents=True)
        big_text = "Paragraph. " * 200  # 2200 chars, well over min_chars
        (md_ms / "safety.md").write_text(big_text)
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(md_root), "init", "--name", "safety",
                      "--path", str(md_ms)])
        md_db = api.open_db(str(md_root))
        md_manuscript = api.get_manuscript(md_db)
        api.collect(md_db, md_manuscript, {})  # v1 baseline: 2200 chars

        (md_ms / "safety.md").write_text(big_text[:1760])  # 80% remains
        report = api.collect(md_db, md_manuscript, {}, auto=True)
        check("auto-collect proceeds under the shrink_ratio threshold",
              "staged" not in report)

        (md_ms / "safety.md").write_text(big_text[:50])  # ~97% removed vs. v2
        report = api.collect(md_db, md_manuscript, {}, auto=True)
        check("auto-collect stages a massive deletion instead of collecting",
              report.get("staged") == [{"file": "safety.md", "removed_percent": 97}])

        report = api.collect(md_db, md_manuscript, {}, auto=False)
        check("a manual collect proceeds through a massive deletion",
              "staged" not in report and "unchanged" not in report)

        (md_ms / "tiny.md").write_text("x" * 100)  # under min_chars
        api.collect(md_db, md_manuscript, {})
        (md_ms / "tiny.md").write_text("")
        report = api.collect(md_db, md_manuscript, {}, auto=True)
        check("files under min_chars are exempt from the deletion safety net",
              "staged" not in report)

        # --- history show/restore (MVP.md 'Deliberately deferred': version
        # access & restoration — implementable at any time, no schema change) ---
        try:
            api.get_version(md_db, md_manuscript, 99)
            check("get_version rejects an unknown version number", False)
        except LookupError as err:
            check("get_version rejects an unknown version number", "v99" in str(err))

        v1 = api.get_version(md_db, md_manuscript, 1)
        check("get_version returns the requested snapshot",
              loads(v1["files"], {})["safety.md"] == big_text)

        restored = api.restore_version(md_db, md_manuscript, 1, {})
        check("restore_version writes the old content back to disk",
              (md_ms / "safety.md").read_text() == big_text)
        check("restore_version deletes files absent from the restored version",
              not (md_ms / "tiny.md").exists())
        check("restoring advances history rather than rewinding it",
              restored["version_no"] == 6
              and restored["transitions"])
        latest = api.get_version(md_db, md_manuscript, 6)
        check("the restored snapshot is itself a new, real version",
              loads(latest["files"], {})["safety.md"] == big_text
              and "tiny.md" not in loads(latest["files"], {}))

        declared = api.declare_intent(db, manuscript, "Expand on gravity")
        check("declare_intent returns intent + preview",
              declared["intent"]["statement"] == "Expand on gravity"
              and declared["preview"]["matched"][0]["name"] == "Gravity")
        typo = api.intent_preview(db, manuscript, "Discuss gravety maybe")
        check("preview fuzzy-suggests on typos", "Gravity" in typo["suggestions"])

        # --- intent complete/abandon: active-state + lookup errors ---
        life = api.declare_intent(db, manuscript, "Lifecycle probe intent")
        life_id = life["intent"]["id"]
        finished = api.complete_intent(db, manuscript, life_id[:8], "wrapped up")
        check("complete_intent marks an active intent completed with outcome",
              finished["intent"]["id"] == life_id
              and db.one("SELECT status, outcome FROM declared_intents WHERE id = ?",
                         (life_id,))["status"] == "completed"
              and db.one("SELECT outcome FROM declared_intents WHERE id = ?",
                         (life_id,))["outcome"] == "wrapped up")
        try:
            api.complete_intent(db, manuscript, life_id[:8], "again")
            check("complete_intent rejects an already-completed intent", False)
        except ValueError as err:
            check("complete_intent rejects an already-completed intent",
                  "already completed" in str(err))
        check("rejected re-complete leaves the original outcome intact",
              db.one("SELECT outcome FROM declared_intents WHERE id = ?",
                     (life_id,))["outcome"] == "wrapped up")

        drop = api.declare_intent(db, manuscript, "Abandon-then-complete probe")
        drop_id = drop["intent"]["id"]
        api.abandon_intent(db, manuscript, drop_id[:8], "changed mind")
        try:
            api.complete_intent(db, manuscript, drop_id[:8], "should fail")
            check("complete_intent rejects an abandoned intent", False)
        except ValueError as err:
            check("complete_intent rejects an abandoned intent",
                  "already abandoned" in str(err))
        check("rejected complete-after-abandon preserves abandonment reason",
              db.one("SELECT status, outcome FROM declared_intents WHERE id = ?",
                     (drop_id,))["status"] == "abandoned"
              and db.one("SELECT outcome FROM declared_intents WHERE id = ?",
                         (drop_id,))["outcome"] == "changed mind")
        try:
            api.abandon_intent(db, manuscript, drop_id[:8], "again")
            check("abandon_intent rejects a non-active intent", False)
        except ValueError as err:
            check("abandon_intent rejects a non-active intent",
                  "already abandoned" in str(err))
        try:
            api.complete_intent(db, manuscript, "zzzzzzzz", None)
            check("complete_intent rejects an unknown prefix", False)
        except LookupError:
            check("complete_intent rejects an unknown prefix", True)

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

        # New chapter files must be acknowledged distinctly, not buried
        # in transitions (session start prints report["new_files"]).
        (ms / "late-arrival.md").write_text("# **Late**\n\nA new chapter.\n")
        late = api.collect(db, manuscript, {})
        check("collect surfaces new files distinctly",
              late.get("new_files") == ["late-arrival.md"],
              str(late.get("new_files")))
        (ms / "late-arrival.md").unlink()
        api.collect(db, manuscript, {})

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

        # --- idle session expiry ---
        check("expire_idle_session is a no-op with no active session",
              api.expire_idle_session(db, manuscript, {}) is None)
        idle_session, _ = api.ensure_session(db, manuscript)
        db.update("sessions", idle_session["id"],
                  {"started_at": "2020-01-01T00:00:00.000000Z"})
        check("expire_idle_session honors a configured idle_hours threshold",
              api.expire_idle_session(
                  db, manuscript, {"session": {"idle_hours": 999999}}) is None)
        expired = api.expire_idle_session(db, manuscript, {})
        check("expire_idle_session closes a session idle past the default threshold",
              expired is not None
              and expired["session"]["id"] == idle_session["id"]
              and expired["idle_hours"] > 3
              and api.status(db, manuscript)["session"] is None)

        idle_session2, _ = api.ensure_session(db, manuscript)
        db.update("sessions", idle_session2["id"], {"started_at": "not-a-date"})
        check("expire_idle_session tolerates an unparseable timestamp",
              api.expire_idle_session(db, manuscript, {}) is None)
        api.close_session(db, manuscript)

        # --- Google Docs bridge: normalizer + stub-service round trip ---
        from authorlm.gdocs import normalize_markdown, pull_doc, push_doc

        messy = ("# Title\r\n\r\n\r\n\r\nSome \\-escaped \\. text here.   \n"
                 "*  a bullet\n\nEnds with nbsp.")
        clean = normalize_markdown(messy)
        check("normalizer canonicalizes Docs-export quirks",
              clean == ("# Title\n\nSome -escaped . text here.\n"
                        "- a bullet\n\nEnds with nbsp.\n"), repr(clean))
        check("normalizer is idempotent", normalize_markdown(clean) == clean)

        # An empty heading paragraph in the Doc (e.g. a blank Subtitle
        # line) must be dropped — never merged into the next heading
        # ('## ##', it-7b127d3164ff).
        empty_heading = ("# **Title**\n\n## \n\n"
                         "## Septem Plus Sermones Ad Mortuos, 2026\n")
        check("normalizer drops empty headings instead of merging",
              normalize_markdown(empty_heading)
              == ("# **Title**\n\n"
                  "## Septem Plus Sermones Ad Mortuos, 2026\n"),
              repr(normalize_markdown(empty_heading)))

        # --- illustration slots: tag grammar + scan-derived registry ---
        from authorlm.illus import (desc_hash, parse_tag, slot_report,
                                    strip_dangling)

        tag = parse_tag(
            "[Illustration: a tracker kneeling | caption: The tracker]")
        check("illustration tag grammar parses prompt and caption",
              tag["prompt"] == "a tracker kneeling"
              and tag["caption"] == "The tracker", str(tag))
        check("caption and whitespace stay outside slot identity",
              desc_hash("a tracker kneeling")
              == desc_hash("a  tracker\tkneeling")
              and parse_tag("[Illustration: a tracker kneeling]")["caption"]
              is None
              and parse_tag("prose mentioning [Illustration: x] inline")
              is None)

        scratch = root / "illus-scratch"
        scratch.mkdir()
        (scratch / "ch.md").write_text(
            "# C\n\n[Illustration: two turns in opposite order]\n")
        rep = slot_report(scratch)
        check("scan reports an unrendered slot with file and line",
              rep["unrendered"] == [{"file": "ch.md", "line": 3,
                                     "prompt": "two turns in opposite order"}]
              and not rep["orphaned"], str(rep))
        ill_dir = scratch / "_illustrations"
        ill_dir.mkdir()
        h = desc_hash("two turns in opposite order")
        candidate = f"two-turns-in-opposite-{h}-0000-01.png"
        (ill_dir / candidate).write_bytes(b"")
        check("a matching candidate marks the slot rendered",
              not slot_report(scratch)["unrendered"])
        (scratch / "ch.md").write_text(
            "# C\n\n[Illustration: two turns, reworded]\n")
        rep = slot_report(scratch)
        check("editing the prompt un-renders the slot and orphans the file",
              rep["unrendered"][0]["prompt"] == "two turns, reworded"
              and rep["orphaned"] == [candidate], str(rep))

        dirty = ("Prose kept. ![][image1]\n\n"
                 "[image1]: <data:image/png;base64,abc>\n")
        clean, refs = strip_dangling(dirty)
        check("dangling Doc image refs strip cleanly and are reported",
              "image1" not in clean and "Prose kept." in clean
              and len(refs) == 2, repr((clean, refs)))

        # A collect surfaces unrendered slots so the author never has to
        # remember to ask ("new illustrations found").
        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text()
            + "\n[Illustration: choice as a forking path]\n")
        report = api.collect(db, manuscript, {})
        check("collect reports new illustration slots",
              report["illustrations"]["unrendered"][0]["prompt"]
              == "choice as a forking path"
              and api.compact_collect(report)["illustrations"]
              == report["illustrations"], str(report.get("illustrations")))

        # --- illus rendering: candidates, pinning, style law, prune ---
        import struct
        import zlib

        from authorlm import illus as illus_mod
        from authorlm.revisions import (read_manuscript_files
                                        as read_files_for_test,
                                        strip_embed_lines
                                        as strip_embed_lines_for_test)

        def tiny_png() -> bytes:
            def chunk(t, d):
                return (struct.pack(">I", len(d)) + t + d
                        + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF))
            return (b"\x89PNG\r\n\x1a\n"
                    + chunk(b"IHDR",
                            struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
                    + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
                    + chunk(b"IEND", b""))

        gen_calls = []

        def fake_gen(prompt, input_png):
            gen_calls.append((prompt, input_png))
            return tiny_png()

        slot = illus_mod.find_slot(ms, "forking path")[0]
        h = slot["desc_hash"]
        r1 = illus_mod.render_slot(db, manuscript, slot, {},
                                   generator=fake_gen)
        first = f"choice-as-a-forking-{h}-0000-01.png"
        choice_text = (ms / "01-choice.md").read_text()
        check("render writes candidate 01 and embeds the first render",
              r1["written"] == [first] and not r1["had_embed"]
              and f"![](_illustrations/{first})" in choice_text, str(r1))
        blob = (ms / "_illustrations" / first).read_bytes()
        check("candidate PNG carries the prompt as metadata",
              b"authorlm:prompt" in blob
              and "choice as a forking path".encode() in blob)
        check("embed lines are invisible to observation",
              "_illustrations" not in
              read_files_for_test(ms)["01-choice.md"]
              and api.collect(db, manuscript, {}).get("unchanged") is True)

        r2 = illus_mod.render_slot(db, manuscript, slot, {}, count=2,
                                   generator=fake_gen)
        check("re-render appends numbered candidates, embed stays pinned",
              [n[-6:-4] for n in r2["written"]] == ["02", "03"]
              and r2["had_embed"]
              and illus_mod.embed_target(
                  (ms / "01-choice.md").read_text(), h) == first, str(r2))

        api.add_style_element(db, manuscript, "illustration",
                              "woodcut, high-contrast linework",
                              file="01-choice.md")
        law = illus_mod.illustration_law(db, manuscript["id"], "01-choice.md")
        shash = illus_mod.style_hash(law)
        check("illustration law renders from illustration-aspect elements",
              "woodcut" in law and shash != "0000"
              and illus_mod.style_hash("") == "0000")
        status = [s for s in illus_mod.slot_status(db, manuscript)
                  if s["file"] == "01-choice.md"][0]
        check("a new illustration rule marks the embedded slot stale-style",
              status["state"] == "stale-style"
              and status["candidates"] == 3, str(status))

        r3 = illus_mod.render_slot(db, manuscript, slot, {}, from_n=1,
                                   generator=fake_gen)
        check("render --from passes the source image and the new law",
              gen_calls[-1][1] is not None
              and law in gen_calls[-1][0]
              and r3["written"] == [
                  f"choice-as-a-forking-{h}-{shash}-04.png"], str(r3))
        assembled = illus_mod.effective_prompt(db, manuscript, slot)
        check("effective_prompt is byte-identical to what the renderer "
              "sends",
              assembled["composed"] == gen_calls[-1][0]
              and assembled["style_hash"] == shash
              and assembled["law"] and assembled["law"] in
              assembled["composed"], str(assembled))

        illus_mod.set_embed(ms / "01-choice.md", h, r3["written"][0])
        removed = illus_mod.prune(manuscript)
        check("prune removes every unpicked candidate, keeps the pick",
              sorted(removed) == sorted([first,
                  f"choice-as-a-forking-{h}-0000-02.png",
                  f"choice-as-a-forking-{h}-0000-03.png"])
              and (ms / "_illustrations" / r3["written"][0]).exists(),
              str(removed))

        # In-memory Drive + Docs fake for the tabbed master-Doc model:
        # one object serves as both `service` and `docs_service`. Master
        # Doc state is tabs of literal markdown; the temp-import Doc is
        # modeled as one plain-text paragraph per line, and export renders
        # each tab under a '# **<title>**' heading — the same shape
        # split_tabbed_export expects from the real API.
        from authorlm.gdocs import FOLDER_MIME, doc_status

        class FakeRequest:
            def __init__(self, payload):
                self._payload = payload

            def execute(self):
                return self._payload

        class FakeFiles:
            def __init__(self, state):
                self.state = state

            def create(self, body=None, media_body=None, fields=None):
                self.state["counter"] += 1
                fid = f"doc-{self.state['counter']}"
                if media_body is not None:  # markdown import (temp/export)
                    self.state["uploads"][fid] = media_body.getbytes(
                        0, media_body.size()).decode("utf-8")
                elif (body or {}).get("mimeType") == FOLDER_MIME:
                    self.state["folders"].append(fid)
                else:  # a native Doc, born with the blank default tab
                    self.state["tab_counter"] += 1
                    tab_id = f"tab-{self.state['tab_counter']}"
                    self.state["docs"][fid] = [
                        {"id": tab_id, "title": "Tab 1", "text": ""}]
                return FakeRequest({"id": fid})

            def update(self, fileId=None, media_body=None):
                self.state["uploads"][fileId] = media_body.getbytes(
                    0, media_body.size()).decode("utf-8")
                return FakeRequest({})

            def delete(self, fileId=None):
                self.state["uploads"].pop(fileId, None)
                return FakeRequest({})

            def export(self, fileId=None, mimeType=None):
                def as_markdown(text):
                    # The real exporter separates doc paragraphs with
                    # blank lines; the fake stores one paragraph per
                    # line (empties tolerated for legacy set_tab text).
                    paras = [ln for ln in text.split("\n") if ln.strip()]
                    return "\n\n".join(paras) + ("\n" if paras else "")

                whole = "\n".join(
                    f"# **{t['title']}**\n\n{as_markdown(t['text'])}"
                    for t in self.state["docs"][fileId])
                return FakeRequest(whole.encode("utf-8"))

        class FakeDocuments:
            def __init__(self, state):
                self.state = state

            def get(self, documentId=None, includeTabsContent=None):
                if documentId in self.state["uploads"]:
                    content = [{"paragraph": {"elements": [
                        {"textRun": {"content": line + "\n"}}]}}
                        for line in
                        self.state["uploads"][documentId].splitlines()]
                    return FakeRequest({"body": {"content": content}})
                def paragraphs(text):
                    items, pos = [], 1
                    for line in text.split("\n"):
                        content = line + "\n"
                        items.append({
                            "startIndex": pos,
                            "endIndex": pos + len(content),
                            "paragraph": {"elements": [
                                {"startIndex": pos,
                                 "endIndex": pos + len(content),
                                 "textRun": {"content": content}}]}})
                        pos += len(content)
                    return items

                tabs = [{"tabProperties": {"tabId": t["id"],
                                           "title": t["title"]},
                         "documentTab": {"body": {"content":
                             paragraphs(t["text"]) if t["text"] else []}}}
                        for t in self.state["docs"][documentId]]
                return FakeRequest({"tabs": tabs})

            def batchUpdate(self, documentId=None, body=None):
                tabs = self.state["docs"][documentId]

                def tab(tid):
                    return next(t for t in tabs if t["id"] == tid)

                replies = []
                for req in (body or {}).get("requests", []):
                    if "addDocumentTab" in req:
                        self.state["tab_counter"] += 1
                        new = {"id": f"tab-{self.state['tab_counter']}",
                               "title": req["addDocumentTab"]
                               ["tabProperties"]["title"], "text": ""}
                        tabs.append(new)
                        replies.append({"addDocumentTab": {
                            "tabProperties": {"tabId": new["id"]}}})
                        continue
                    replies.append({})
                    if "deleteTab" in req:
                        tabs.remove(tab(req["deleteTab"]["tabId"]))
                    elif "updateDocumentTabProperties" in req:
                        props = req["updateDocumentTabProperties"][
                            "tabProperties"]
                        moved = tab(props["tabId"])
                        tabs.remove(moved)
                        tabs.insert(props["index"], moved)
                    elif "deleteContentRange" in req:
                        tab(req["deleteContentRange"]["range"]
                            ["tabId"])["text"] = ""
                    elif "insertText" in req:
                        target = tab(req["insertText"]["location"]["tabId"])
                        loc = req["insertText"]["location"]
                        if "index" in loc:
                            i = loc["index"] - 1
                            target["text"] = (target["text"][:i]
                                              + req["insertText"]["text"]
                                              + target["text"][i:])
                        else:
                            target["text"] += req["insertText"]["text"]
                return FakeRequest({"replies": replies})

        class FakeComments:
            def __init__(self, state):
                self.state = state

            def list(self, fileId=None, pageToken=None, includeDeleted=None,
                     fields=None):
                return FakeRequest({"comments": [
                    c for c in self.state["comments"].values()
                    if not c.get("resolved")]})

        class FakeReplies:
            def __init__(self, state):
                self.state = state

            def create(self, fileId=None, commentId=None, body=None,
                       fields=None):
                self.state["reply_counter"] += 1
                reply = {"id": f"r-{self.state['reply_counter']}",
                         "content": body["content"],
                         "author": {"displayName": "author"}}
                self.state["comments"][commentId]["replies"].append(reply)
                if body.get("action") == "resolve":
                    self.state["comments"][commentId]["resolved"] = True
                return FakeRequest({"id": reply["id"]})

        class FakeGoogle:
            def __init__(self):
                self.state = {"counter": 0, "tab_counter": 0,
                              "comments": {}, "reply_counter": 0,
                              "folders": [], "docs": {}, "uploads": {}}
                self._files = FakeFiles(self.state)
                self._documents = FakeDocuments(self.state)

            def files(self):
                return self._files

            def documents(self):
                return self._documents

            def comments(self):
                return FakeComments(self.state)

            def replies(self):
                return FakeReplies(self.state)

            def add_comment(self, comment_id, quoted, content):
                self.state["comments"][comment_id] = {
                    "id": comment_id, "content": content,
                    "quotedFileContent": {"value": quoted},
                    "resolved": False, "replies": [],
                    "author": {"displayName": "author"},
                    "createdTime": "2026-08-08T00:00:00Z"}

            def author_reply(self, comment_id, content):
                self.state["comments"][comment_id]["replies"].append(
                    {"content": content,
                     "author": {"displayName": "author"}})

            def set_tab(self, title, text):  # simulate an edit in Docs
                for tabs in self.state["docs"].values():
                    for t in tabs:
                        if t["title"] == title:
                            t["text"] = text

        stub = FakeGoogle()
        pushed = push_doc(db, manuscript, "01-choice.md",
                          service=stub, docs_service=stub)
        check("push creates folder, master Doc, and the file's tab",
              pushed["created"] and pushed["doc_id"] == "doc-2"
              and "tab=" in pushed["url"])
        manuscript = api.get_manuscript(db)  # refresh metadata
        status = doc_status(db, manuscript)
        check("push mapping persisted: checkout, folder, master, tab id",
              status["01-choice.md"]["checked_out"] is True
              and status["01-choice.md"]["tab_id"]
              and status["_folder_id"] == "doc-1"
              and status["_master_id"] == "doc-2")
        master = stub.state["docs"]["doc-2"]
        tab_text = {t["title"]: t["text"] for t in master}
        check("master carries the container tab, one tab per file, and "
              "the manifest; default tab retired",
              [t["title"] for t in master]
              == ["book", "01-choice.md", "manifest"],
              str([t["title"] for t in master]))
        check("push transplants the file's markdown into its tab, "
              "embed lines stripped (Doc shows only the tag)",
              tab_text["01-choice.md"]
              == strip_embed_lines_for_test(
                  (ms / "01-choice.md").read_text())
              and "_illustrations" not in tab_text["01-choice.md"]
              and "[Illustration:" in tab_text["01-choice.md"]
              and not stub.state["uploads"])  # temp import Doc deleted
        check("manifest tab names the manuscript and doc incarnation",
              "Manuscript: book" in tab_text["manifest"]
              and "Doc version: 1" in tab_text["manifest"])
        stub.set_tab("01-choice.md",
                     "# Title\n\nEdited in Docs \\- with escapes.\n")
        pulled = pull_doc(db, manuscript, "01-choice.md", service=stub)
        check("pull writes normalized export and clears checkout",
              pulled["changed"] == ["01-choice.md"]
              and (ms / "01-choice.md").read_text()
              == "# Title\n\nEdited in Docs - with escapes.\n"
              and doc_status(db, manuscript)["01-choice.md"]["checked_out"]
              is False)
        pushed2 = push_doc(db, manuscript, "01-choice.md",
                           service=stub, docs_service=stub)
        check("second push reuses the master Doc (no new folder or Doc)",
              not pushed2["created"] and pushed2["doc_id"] == "doc-2"
              and stub.state["folders"] == ["doc-1"]
              and list(stub.state["docs"]) == ["doc-2"])

        # Two-sided edit: local changed since push AND the tab differs
        # from what was pushed → conflict, skipped unless forced.
        (ms / "01-choice.md").write_text("# Title\n\nLocal divergence.\n")
        manuscript = api.get_manuscript(db)
        stub.set_tab("01-choice.md", "# Title\n\nEdited in Docs again.\n")
        report = pull_doc(db, manuscript, "01-choice.md", service=stub)
        check("two-sided edits conflict, file untouched",
              report["conflicts"] == ["01-choice.md"]
              and "Local divergence" in (ms / "01-choice.md").read_text(),
              str(report))
        forced = pull_doc(db, manuscript, "01-choice.md", service=stub,
                          force=True)
        check("pull --force takes the Doc's version",
              forced["changed"] == ["01-choice.md"]
              and "Edited in Docs again" in (ms / "01-choice.md").read_text())

        # A Doc-pasted image exports as a dangling ![][imageN] ref: the
        # pull strips it (the bridge cannot transport images) and warns.
        stub.set_tab("01-choice.md",
                     "# Title\n\nProse kept. ![][image9]\n\n"
                     "[image9]: <data:image/png;base64,abc>\n")
        pulled_img = pull_doc(db, manuscript, "01-choice.md", service=stub)
        check("pull strips Doc-pasted image refs and warns",
              pulled_img["dangling_images"]["01-choice.md"]
              and "image9" not in (ms / "01-choice.md").read_text()
              and "Prose kept." in (ms / "01-choice.md").read_text(),
              str(pulled_img))

        # Embed round trip: the embed line never reaches the Doc, and a
        # pull restores the pinned pick under its tag.
        picked_name = r3["written"][0]
        (ms / "01-choice.md").write_text(
            "# Title\n\n[Illustration: choice as a forking path]\n"
            f"![](_illustrations/{picked_name})\n\nProse below.\n")
        push_doc(db, manuscript, "01-choice.md",
                 service=stub, docs_service=stub)
        tab_now = next(t["text"] for t in stub.state["docs"]["doc-2"]
                       if t["title"] == "01-choice.md")
        check("push keeps the tag but never the embed line",
              "[Illustration: choice as a forking path]" in tab_now
              and "_illustrations" not in tab_now, tab_now)
        stub.set_tab("01-choice.md", tab_now.replace(
            "Prose below.", "Prose below, edited in the Doc."))
        pull_doc(db, manuscript, "01-choice.md", service=stub)
        round_tripped = (ms / "01-choice.md").read_text()
        check("pull re-inserts the pinned embed under its tag",
              f"[Illustration: choice as a forking path]\n"
              f"![](_illustrations/{picked_name})" in round_tripped
              and "edited in the Doc" in round_tripped, round_tripped)

        # --- session-start reconciliation: all four outcomes ---
        from authorlm.gdocs import reconcile

        manuscript = api.get_manuscript(db)
        report = reconcile(db, manuscript, stub)
        check("reconcile: in sync after a pull",
              report["in_sync"] == ["01-choice.md"] and not report["conflicts"],
              str(report))

        (ms / "01-choice.md").write_text("# Title\n\nLocal-only progress.\n")
        report = reconcile(db, manuscript, stub)
        check("reconcile: local-only change without a Docs service is pending",
              report["pending_push"] == ["01-choice.md"], str(report))
        report = reconcile(db, manuscript, stub, docs_service=stub)
        check("reconcile: only-local change auto-pushes",
              report["pushed"] == ["01-choice.md"]
              and next(t["text"] for t in stub.state["docs"]["doc-2"]
                       if t["title"] == "01-choice.md")
              == "# Title\n\nLocal-only progress.\n", str(report))
        # The tab now reflects the push; the next reconcile sees sync.
        report = reconcile(db, manuscript, stub)
        check("reconcile: after push, next start is in sync",
              report["in_sync"] == ["01-choice.md"], str(report))
        stub.set_tab("01-choice.md", "# Title\n\nDoc-only revision now.\n")
        report = reconcile(db, manuscript, stub)
        check("reconcile: only-Doc change auto-pulls",
              report["pulled"] == ["01-choice.md"]
              and "Doc-only revision" in (ms / "01-choice.md").read_text(),
              str(report))
        (ms / "01-choice.md").write_text("# Title\n\nBoth sides now differ.\n")
        stub.set_tab("01-choice.md", "# Title\n\nDoc went another way.\n")
        report = reconcile(db, manuscript, stub)
        check("reconcile: two-sided edits conflict, files untouched",
              report["conflicts"] == ["01-choice.md"]
              and "Both sides now differ" in (ms / "01-choice.md").read_text(),
              str(report))

        # --- margin threads: propose in-context; canonical stays old ---
        from authorlm import threads as th
        from authorlm.gdocs import propose_change

        strip_out = th.strip_pending("A <<old>>{{new}} B")
        check("pending grammar strips to canonical old; strays warn",
              strip_out == ("A old B", [])
              and th.strip_pending("stray << here")[1], str(strip_out))
        check("verdict grammar is deterministic, whole-reply only",
              th.classify_reply("Go ahead!") == "approve"
              and th.classify_reply("no") == "decline"
              and th.classify_reply("go ahead but soften it")
              == "conversation"
              and th.is_ours("AuthorLM: proposed — x")
              and not th.is_ours("looks wrong to me"))

        pull_doc(db, manuscript, "01-choice.md", service=stub, force=True)
        stub.add_comment("c-1", "Doc went another way",
                         "Can we make this stronger?")
        pulled_c = pull_doc(db, manuscript, "01-choice.md", service=stub)
        check("comments are ingested but never auto-resolved",
              any(c["comment_id"] == "c-1" for c in pulled_c["comments"])
              and not stub.state["comments"]["c-1"]["resolved"]
              and pulled_c["threads"]["counts"] == {},
              str(pulled_c.get("comments")))

        prop = propose_change(
            db, manuscript, "c-1", old="Doc went another way.",
            new="The Doc chose a firmer road.", note="strengthen per comment",
            service=stub, docs_service=stub)
        tab_now = next(t2["text"] for t2 in stub.state["docs"]["doc-2"]
                       if t2["title"] == "01-choice.md")
        check("propose wraps the anchor and inserts the proposal in place",
              "<<Doc went another way.>>{{The Doc chose a firmer road.}}"
              in tab_now
              and stub.state["comments"]["c-1"]["replies"][0]["content"]
              .startswith("AuthorLM: proposed")
              and th.get_thread(db, manuscript["id"], "c-1")["state"]
              == "proposed", tab_now)

        pulled_p = pull_doc(db, manuscript, "01-choice.md", service=stub)
        local_now = (ms / "01-choice.md").read_text()
        check("canonical local text stays OLD while the proposal pends",
              "01-choice.md" in pulled_p["unchanged"]
              and "Doc went another way." in local_now
              and "{{" not in local_now, local_now)
        check("re-pull is idempotent; the ledger reports the thread",
              not pulled_p["comments"]
              and pulled_p["threads"]["counts"].get("proposed") == 1,
              str(pulled_p["threads"]))

        stub.author_reply("c-1", "go ahead")
        pulled_v = pull_doc(db, manuscript, "01-choice.md", service=stub)
        check("author verdicts surface classified for the state machine",
              pulled_v["thread_replies"] == [
                  {"comment_id": "c-1", "state": "proposed",
                   "reply": "go ahead", "verdict": "approve"}],
              str(pulled_v.get("thread_replies")))

        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text()
            + "\nA new closing thought.\n")
        pushed_s = push_doc(db, manuscript, "01-choice.md",
                            service=stub, docs_service=stub)
        tab_after = next(t2["text"] for t2 in stub.state["docs"]["doc-2"]
                         if t2["title"] == "01-choice.md")
        check("surgical diff push edits around open threads",
              pushed_s.get("mode") == "diff" and pushed_s.get("ops") == 1
              and "A new closing thought." in tab_after
              and "<<Doc went another way.>>" in tab_after
              and not stub.state["comments"]["c-1"]["resolved"], tab_after)

        original_local = (ms / "01-choice.md").read_text()
        (ms / "01-choice.md").write_text(original_local.replace(
            "Doc went another way.", "Doc went a third way."))
        try:
            push_doc(db, manuscript, "01-choice.md",
                     service=stub, docs_service=stub)
            check("diff push refuses edits overlapping pending spans",
                  False)
        except LookupError as err:
            check("diff push refuses edits overlapping pending spans",
                  "pending margin thread" in str(err))
        (ms / "01-choice.md").write_text(original_local)

        # Drive entity-encodes quotes and bodies; fetch unescapes them
        # (it-49edf0c323d1).
        from authorlm.gdocs import fetch_open_comments
        stub.add_comment("c-ent", "one&#39;s own", "entities &#39;here&#39;")
        fetched = {c["id"]: c for c in fetch_open_comments(stub, "doc-2")}
        check("comment quotes and bodies are HTML-unescaped at fetch",
              fetched["c-ent"]["quotedFileContent"]["value"] == "one's own"
              and fetched["c-ent"]["content"] == "entities 'here'",
              str(fetched.get("c-ent")))
        stub.state["comments"]["c-ent"]["resolved"] = True

        # Export escaping survives the strip (markers arrive as \<\<
        # with ~~ strikethrough in the markdown export).
        exported = ("~~\\<\\<the old way.\\>\\>~~"
                    "{{the new way.}}")
        stripped_exp = th.strip_pending(normalize_markdown(exported))
        check("export-escaped pending spans strip to canonical old",
              stripped_exp == ("the old way.\n", []), str(stripped_exp))

        # Approve with the author's in-place edit: modified acceptance.
        stub.author_reply("c-1", "AuthorLM: proposed — courtesy")
        tab = next(t2 for t2 in stub.state["docs"]["doc-2"]
                   if t2["title"] == "01-choice.md")
        tab["text"] = tab["text"].replace(
            "{{The Doc chose a firmer road.}}",
            "{{The Doc took the firmer road.}}")
        stub.author_reply("c-1", "go ahead")
        pulled_a = pull_doc(db, manuscript, "01-choice.md", service=stub,
                            docs_service=stub)
        tab_after = next(t2["text"] for t2 in stub.state["docs"]["doc-2"]
                         if t2["title"] == "01-choice.md")
        thread_a = th.get_thread(db, manuscript["id"], "c-1")
        check("approve applies the author-edited version and closes",
              "The Doc took the firmer road." in tab_after
              and "<<" not in tab_after
              and thread_a["state"] == "cleaned"
              and thread_a["proposed_new"] == "The Doc took the firmer road."
              and stub.state["comments"]["c-1"]["resolved"]
              and any(a["action"] == "cleaned"
                      for a in pulled_a["thread_actions"]), tab_after)
        check("approved text lands in the local file",
              "The Doc took the firmer road."
              in (ms / "01-choice.md").read_text())

        # Decline reverts and closes.
        stub.add_comment("c-3", "firmer road", "hmm")
        pull_doc(db, manuscript, "01-choice.md", service=stub)
        propose_change(db, manuscript, "c-3",
                       old="The Doc took the firmer road.",
                       new="A road of iron.", note="try iron",
                       service=stub, docs_service=stub)
        stub.author_reply("c-3", "revert")
        pull_doc(db, manuscript, "01-choice.md", service=stub,
                 docs_service=stub)
        tab_after = next(t2["text"] for t2 in stub.state["docs"]["doc-2"]
                         if t2["title"] == "01-choice.md")
        check("decline reverts the span and closes the thread",
              "A road of iron." not in tab_after
              and "The Doc took the firmer road." in tab_after
              and "<<" not in tab_after
              and th.get_thread(db, manuscript["id"], "c-3")["state"]
              == "declined"
              and stub.state["comments"]["c-3"]["resolved"], tab_after)

        # Withdraw: author resolves a proposed thread without a verdict.
        stub.add_comment("c-4", "firmer road", "or gold?")
        pull_doc(db, manuscript, "01-choice.md", service=stub)
        propose_change(db, manuscript, "c-4",
                       old="The Doc took the firmer road.",
                       new="A road of gold.", note="try gold",
                       service=stub, docs_service=stub)
        stub.state["comments"]["c-4"]["resolved"] = True
        pull_doc(db, manuscript, "01-choice.md", service=stub,
                 docs_service=stub)
        tab_after = next(t2["text"] for t2 in stub.state["docs"]["doc-2"]
                         if t2["title"] == "01-choice.md")
        check("author-resolving a proposal withdraws and reverts it",
              "A road of gold." not in tab_after
              and "<<" not in tab_after
              and th.get_thread(db, manuscript["id"], "c-4")["state"]
              == "withdrawn", tab_after)

        # Step 4: terminal verdicts are the third evidence channel.
        ev = [dict(r) for r in db.all(
            "SELECT * FROM evidence WHERE manuscript_id = ? "
            "AND evidence_type = 'margin_thread'", (manuscript["id"],))]
        signals = sorted(e["signal"] for e in ev)
        check("margin verdicts land as evidence "
              "(modified/declined/withdrawn)",
              signals == ["declined", "modified", "withdrawn"]
              and any("became" in e["target"] for e in ev
                      if e["signal"] == "modified"), str(signals))

        # Chat door: an explained decline; without an LLM the guardrail
        # seeds nothing — the evidence still lands verbatim.
        import json as _mjson

        from authorlm.gdocs import decide_thread
        stub.add_comment("c-5", "firmer road", "colour?")
        pull_doc(db, manuscript, "01-choice.md", service=stub)
        propose_change(db, manuscript, "c-5",
                       old="The Doc took the firmer road.",
                       new="A road of copper.", note="try copper",
                       service=stub, docs_service=stub)
        decided = decide_thread(db, manuscript, "c-5", "decline",
                                reason="Copper is the wrong register here",
                                service=stub, docs_service=stub, llm=None)
        ev2 = db.one(
            "SELECT * FROM evidence WHERE manuscript_id = ? AND "
            "evidence_type='margin_thread' AND signal='declined' "
            "ORDER BY created_at DESC", (manuscript["id"],))
        check("chat-decided decline records the reason verbatim and "
              "seeds nothing without the distiller",
              decided["state"] == "declined"
              and _mjson.loads(ev2["metadata"] or "{}").get("explanation")
              == "Copper is the wrong register here"
              and stub.state["comments"]["c-5"]["resolved"]
              and db.one(
                  "SELECT COUNT(*) AS n FROM editorial_policies "
                  "WHERE manuscript_id = ? AND source = 'margin-thread'",
                  (manuscript["id"],))["n"] == 0, str(decided))

        # --- single-manuscript export (doc create-manuscript) ---
        from authorlm.export import combined_markdown, export_manuscript

        (ms / "00-intro.md").write_text("# Intro\n\nWelcome.\n")
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "01-choice.md"\n\n'
            '[[chapter]]\nfile = "00-intro.md"\n')
        manuscript = api.get_manuscript(db)
        text, order, unlisted = combined_markdown(manuscript)
        check("combined markdown follows toc.toml reading order",
              order == ["01-choice.md", "00-intro.md"] and not unlisted
              and text.index("firmer road") < text.index("Welcome"), text)

        local_only = export_manuscript(db, manuscript, service=None)
        export_path = ms / "_exports" / "book.md"
        check("export writes _exports/<name>.md even without Drive",
              local_only["doc_id"] is None and export_path.read_text() == text)

        exported = export_manuscript(db, manuscript, service=stub)
        check("export creates the manuscript Doc in the existing folder",
              exported["created"] and exported["doc_id"] is not None
              and stub.state["folders"] == ["doc-1"])
        exported2 = export_manuscript(db, manuscript, service=stub)
        check("re-export updates the same Doc (no new one)",
              not exported2["created"]
              and exported2["doc_id"] == exported["doc_id"])

        # A Doc deleted by hand in Drive is transient — recreated on export.
        class Gone(Exception):
            resp = type("R", (), {"status": 404})()

        original_update = stub._files.update
        stub._files.update = lambda fileId=None, media_body=None: (_ for _ in ()).throw(Gone())
        exported3 = export_manuscript(db, manuscript, service=stub)
        stub._files.update = original_update
        check("a hand-deleted export Doc is recreated",
              exported3["created"]
              and exported3["doc_id"] != exported["doc_id"])

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

        # --- publishing exports: variants, settings, local pandoc ---
        from authorlm.export import (export_published, load_settings,
                                     publish_markdown, set_setting)

        (ms / "00-intro.md").write_text(
            "# Intro\n\nWelcome.\n\n"
            "[Illustration: a winding path | caption: The path]\n\n"
            "[Illustration: an unrendered idea]\n")
        path_hash = illus_mod.desc_hash("a winding path")
        winding = f"a-winding-path-{path_hash}-0000-01.png"
        (ms / "_illustrations" / winding).write_bytes(tiny_png())
        text, order, warnings = publish_markdown(manuscript, "images")
        check("images variant embeds candidates with captions, keeps "
              "unrendered slots as notes and warns",
              f"![The path](_illustrations/{winding})" in text
              and "[Illustration: an unrendered idea]" in text
              and any("unrendered" in w for w in warnings), text)
        stripped_text, _, _ = publish_markdown(manuscript, "stripped")
        check("stripped variant removes every tag (audio-clean)",
              "[Illustration" not in stripped_text
              and "Welcome." in stripped_text)
        slots_text, _, _ = publish_markdown(manuscript, "slots")
        check("slots variant keeps tags verbatim as production notes",
              "[Illustration: a winding path | caption: The path]"
              in slots_text)

        set_setting(manuscript, "author", "Chitta Darshana")
        set_setting(manuscript, "variant", "slots")
        check("export settings persist in _exports/settings.toml",
              load_settings(manuscript)["author"] == "Chitta Darshana"
              and load_settings(manuscript)["variant"] == "slots"
              and (ms / "_exports" / "settings.toml").exists())
        try:
            set_setting(manuscript, "nope", "x")
            refused = False
        except LookupError:
            refused = True
        check("unknown export settings are refused", refused)

        import shutil as _shutil
        if _shutil.which("pandoc"):
            published = export_published(db, manuscript, fmt="docx",
                                         variant="images")
            docx = Path(published["docx"])
            check("pandoc docx export lands in _exports/ with images",
                  docx.exists() and docx.stat().st_size > 1000,
                  str(published))
        else:
            print("  note: pandoc not on PATH — docx conversion untested "
                  "in this run")

        # --- hygiene: deterministic filters + retroactive sweep ---
        import json as _json

        from authorlm import hygiene
        from authorlm.concepts import link_concepts

        ghost = api.add_concept(db, manuscript, "Spectral Machinery")
        db.update("concept_nodes", ghost["id"], {"metadata": _json.dumps(
            {"origin": "extracted", "confirmed": False})})
        link_concepts(db, manuscript["id"], "Spectral Machinery",
                      "creates", "Choice", status="inferred")
        hygiene_files = read_files_for_test(ms)
        swept = hygiene.sweep(db, manuscript, hygiene_files)
        check("sweep flags ungrounded extracted concepts and their edges",
              any(c["name"] == "Spectral Machinery"
                  for c in swept["ungrounded_concepts"])
              and any("Spectral Machinery" in e["unmentioned"]
                      for e in swept["ungrounded_edges"]), str(swept))
        check("sweep never flags grounded or author-confirmed material",
              not any(c["name"] == "Choice"
                      for c in swept["ungrounded_concepts"]))

        # Staleness: a bridge suggestion whose concept the author then
        # writes is marked stale at collect — review never offers a no-op.
        session, _ = api.ensure_session(db, manuscript)
        api.add_concept(db, manuscript, "Undertow")
        api.declare_intent(db, manuscript, "Introduce Undertow in ch1")
        guided = api.guide(db, manuscript, session)
        check("guide proposes the declared, unrealized concept",
              any("Undertow" in r["suggestion"]
                  for r in guided["suggestions"]), str(guided))
        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text()
            + "\n\nThe Undertow pulls every choice back toward habit.\n")
        report = api.collect(db, manuscript, {})
        check("collect marks the now-moot suggestion stale",
              any("Undertow" in s["suggestion"]
                  for s in report.get("suggestions_stale", []))
              and api.compact_collect(report)["suggestions_stale"]
              == report["suggestions_stale"], str(report.get(
                  "suggestions_stale")))
        row = db.one(
            "SELECT * FROM guidance_history WHERE manuscript_id = ? "
            "AND state = 'stale'", (manuscript["id"],))
        check("the stale state is persisted on the guidance row",
              row is not None and "Undertow" in row["suggestion"])

        # --- recurrence bar (ratified 2026-08-08): same-paragraph
        # collapse; >=2 contexts spanning >=2 sections or files ---
        bar_files = {
            "a.md": ("# One\n\nThe Undertow pulls. The Undertow drags.\n\n"
                     "Plain prose here.\n\n## Two\n\nMore prose.\n"),
            "b.md": "# Other\n\nNothing relevant.\n",
        }
        ok, stats = hygiene.passes_recurrence_bar(bar_files, ["Undertow"])
        check("twice in one paragraph is one context — fails the bar",
              not ok and stats["contexts"] == 1, str(stats))
        bar_files["a.md"] += "\nThe Undertow returns in section two.\n"
        ok, stats = hygiene.passes_recurrence_bar(bar_files, ["Undertow"])
        check("two contexts across two sections of one file pass the bar",
              ok and stats["contexts"] == 2 and stats["sections"] == 2,
              str(stats))
        bar_files["a.md"] = ("# One\n\nThe Undertow pulls.\n\n"
                             "The Undertow drags on, same section.\n")
        ok, stats = hygiene.passes_recurrence_bar(bar_files, ["Undertow"])
        check("two contexts in ONE section of one file fail the spread",
              not ok and stats["contexts"] == 2 and stats["sections"] == 1,
              str(stats))
        bar_files["b.md"] = "# Other\n\nThe Undertow crosses files.\n"
        ok, stats = hygiene.passes_recurrence_bar(bar_files, ["Undertow"])
        check("cross-file recurrence passes the bar",
              ok and stats["files"] == 2, str(stats))

        # The sweep reports unconfirmed gated nodes that fail the bar —
        # mentioned (not ungrounded) but single-context.
        motif = api.add_concept(db, manuscript, "Fleeting Motif",
                                kind="metaphor")
        db.update("concept_nodes", motif["id"], {"metadata": _json.dumps(
            {"origin": "extracted", "confirmed": False})})
        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text()
            + "\nA Fleeting Motif appears exactly once.\n")
        swept = hygiene.sweep(db, manuscript, read_files_for_test(ms))
        check("sweep separates below-bar from ungrounded",
              any(c["name"] == "Fleeting Motif" and c["contexts"] == 1
                  for c in swept["below_bar"])
              and not any(c["name"] == "Fleeting Motif"
                          for c in swept["ungrounded_concepts"])
              and not any(c["name"] == "Spectral Machinery"
                          for c in swept["below_bar"]), str(swept["below_bar"]))

        # A vanished UNCONFIRMED hypothesis dies quietly (retired +
        # reported); only author-confirmed knowledge earns a proposal
        # (it-8f24c757d3c7).
        fleeting = api.add_concept(db, manuscript, "Fleeting Phrase")
        db.update("concept_nodes", fleeting["id"], {"metadata": _json.dumps(
            {"origin": "extracted", "confirmed": False})})
        with_phrase = (ms / "01-choice.md").read_text()
        (ms / "01-choice.md").write_text(
            with_phrase + "\nThe Fleeting Phrase appears here once.\n")
        api.collect(db, manuscript, {})
        (ms / "01-choice.md").write_text(with_phrase)
        report = api.collect(db, manuscript, {})
        check("vanished unconfirmed hypotheses are dropped, not proposed",
              report["hypotheses_dropped"] == ["Fleeting Phrase"]
              and "Fleeting Phrase" not in report["vanished"]
              and db.one("SELECT status FROM concept_nodes WHERE id = ?",
                         (fleeting["id"],))["status"] == "retired"
              and db.one(
                  "SELECT id FROM knowledge_proposals WHERE kind = 'vanished' "
                  "AND target = ?", (fleeting["id"],)) is None,
              str(report.get("hypotheses_dropped")))

        # --- sweep framework vanguards: readiness, ontology, lens ---
        from authorlm import lenses, sweeps

        ready = sweeps.readiness(db, manuscript)
        checks_present = {i["check"] for i in ready["items"]}
        check("readiness sweep covers the registries deterministically",
              {"illustrations rendered", "proposals settled",
               "toc covers every file", "pandoc available",
               "export settings set"} <= checks_present
              and isinstance(ready["ready"], bool), str(ready))
        check("readiness flags the unrendered slot as a blocker",
              not ready["ready"]
              and "illustrations rendered" in ready["blocking"], str(ready))

        class FakeLLM:
            enabled = True

            def __init__(self, payload):
                self.payload = payload
                self.system = self.user = None

            def complete_json(self, system, user):
                self.system, self.user = system, user
                return self.payload

            def stats_line(self):
                return None

        # --- episode analysis: learn editorial judgment from observed edits ---
        from authorlm.analysis import MAX_DECISIONS, analyze_pending

        an_root = root / "analyze-ws"
        an_ms = an_root / "manuscript"
        an_ms.mkdir(parents=True)
        opening = ("# Opening\n\nChoice is the hinge of becoming.\n")
        (an_ms / "01-choice.md").write_text(opening)
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(an_root), "init", "--name", "analyze",
                      "--path", str(an_ms)])
        an_db = api.open_db(str(an_root))
        an_mscript = api.get_manuscript(an_db)
        api.collect(an_db, an_mscript, {})  # baseline version
        an_session, _ = api.ensure_session(an_db, an_mscript)
        an_declared = api.declare_intent(
            an_db, an_mscript, "Develop the notion of Becoming")
        an_intent_id = an_declared["intent"]["id"]
        sailor = ("Like a sailor tacking, becoming threads through what "
                  "choice has opened.")
        (an_ms / "01-choice.md").write_text(opening + f"\n\n{sailor}\n")
        (an_ms / "02-new.md").write_text(
            f"# New chapter\n\n{sailor} The fresh prose must reach the analyzer.\n")
        api.collect(an_db, an_mscript, {})

        progress_calls = []
        analysis_llm = FakeLLM({
            "decisions": (
                [{"action": "opened the section with a sailing metaphor",
                  "pattern": "Open concept introductions with a lived metaphor"}]
                + [{"action": f"local move {i}", "pattern": None}
                   for i in range(1, MAX_DECISIONS)]
                + [{"action": "ninth decision must be truncated",
                    "pattern": "Should not seed"}]
                + [{"action": "", "pattern": "empty action skipped"},
                   "not-a-dict"]
            ),
            "outcome": "Developed the opening metaphor",
        })
        completed = api.complete_intent(
            an_db, an_mscript, an_intent_id[:8], "done", llm=analysis_llm)
        summaries = completed["analysis"]
        check("complete_intent runs episode analysis over observed edits",
              len(summaries) == 1
              and summaries[0]["intent"] == "Develop the notion of Becoming"
              and summaries[0]["outcome"] == "Developed the opening metaphor"
              and len(summaries[0]["decisions"]) == MAX_DECISIONS
              and summaries[0]["decisions"][0]["pattern"]
              == "Open concept introductions with a lived metaphor",
              str(summaries))
        check("analysis prompt carries declared intent and file_added prose",
              "DECLARED INTENT: Develop the notion of Becoming"
              in analysis_llm.user
              and "NEW TEXT:" in analysis_llm.user
              and "fresh prose must reach the analyzer" in analysis_llm.user,
              analysis_llm.user[:800])
        seeded = an_db.one(
            "SELECT * FROM editorial_policies WHERE manuscript_id = ? "
            "AND statement = ?",
            (an_mscript["id"],
             "Open concept introductions with a lived metaphor"))
        check("pattern seeds a medium-weight episode_analysis candidate",
              seeded is not None and seeded["status"] == "candidate"
              and seeded["source"] == "episode-analysis"
              and an_db.one(
                  "SELECT weight, evidence_type FROM evidence "
                  "WHERE supports_policy = ?", (seeded["id"],)
              )["weight"] == "medium"
              and an_db.one(
                  "SELECT evidence_type FROM evidence "
                  "WHERE supports_policy = ?", (seeded["id"],)
              )["evidence_type"] == "episode_analysis")
        check("analysis is idempotent once the episode is marked analyzed",
              api.analyze(an_db, an_mscript, analysis_llm) == [])

        # Malformed decisions must not crash; episode stays pending for retry.
        an2 = api.declare_intent(an_db, an_mscript, "Second episode for retry")
        (an_ms / "01-choice.md").write_text(
            (an_ms / "01-choice.md").read_text()
            + "\n\nA second observed edit for the retry path.\n")
        api.collect(an_db, an_mscript, {})
        api.complete_intent(an_db, an_mscript, an2["intent"]["id"][:8],
                            "closed without llm")
        bad_llm = FakeLLM({"decisions": None, "outcome": "should not land"})
        # Force a pending episode: strip analysis metadata if complete wrote none
        # (no llm on complete above → episode closed but unanalyzed).
        pending_before = analyze_pending(an_db, an_mscript, bad_llm)
        check("null decisions do not crash and leave a completed analysis "
              "with no decisions",
              len(pending_before) == 1
              and pending_before[0]["decisions"] == []
              and pending_before[0]["outcome"] == "should not land",
              str(pending_before))

        an3 = api.declare_intent(an_db, an_mscript, "Third episode unavailable LLM")
        (an_ms / "01-choice.md").write_text(
            (an_ms / "01-choice.md").read_text()
            + "\n\nA third edit awaiting a usable LLM reply.\n")
        api.collect(an_db, an_mscript, {})
        api.complete_intent(an_db, an_mscript, an3["intent"]["id"][:8], None)
        none_llm = FakeLLM(None)
        progress_calls.clear()

        def _progress(index, total, statement):
            progress_calls.append((index, total, statement))

        stuck = analyze_pending(an_db, an_mscript, none_llm, progress=_progress)
        ep3 = an_db.one(
            "SELECT metadata FROM editorial_episodes WHERE intent_id = ?",
            (an3["intent"]["id"],))
        check("non-dict LLM reply leaves the episode pending for retry",
              stuck == []
              and not loads(ep3["metadata"], {}).get("analysis")
              and progress_calls
              and progress_calls[0][2] == "Third episode unavailable LLM",
              str({"stuck": stuck, "meta": ep3["metadata"],
                   "progress": progress_calls}))
        check("disabled LLM is a no-op over pending episodes",
              analyze_pending(an_db, an_mscript,
                              type("Off", (), {"enabled": False})()) == [])

        api.add_concept(db, manuscript, "Tremor",
                        notes="The Tremor is never caused; it causes.")
        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text()
            + "\n\nSome say the Tremor is caused by the collision of "
              "qualities, and that its cause can be measured precisely "
              "by any patient observer of the fields.\n")
        grounded_quote = ("Some say the Tremor is caused by the collision "
                         "of qualities")
        onto_llm = FakeLLM({"findings": [
            {"item": 1, "concept": "Tremor", "quote": grounded_quote,
             "claim": "The Tremor is never caused; it causes.",
             "why": "The text gives the Tremor a cause."},
            {"item": 1, "concept": "Tremor",
             "quote": "a sentence that is not in the paragraph",
             "claim": "x", "why": "hallucinated"},
        ]})
        onto = sweeps.ontology(db, manuscript, onto_llm, file="01-choice.md")
        check("ontology narrows deterministically and gates findings",
              onto["pairs"] >= 1 and onto["findings"] == 1
              and onto["dropped_ungrounded"] == 1
              and "SETTLED" in onto_llm.user
              and "never caused" in onto_llm.user, str(onto))
        from authorlm import proposals as props
        incongruence = [r for r in props.open_proposals(db, manuscript["id"])
                        if r["kind"] == "incongruence"]
        check("incongruence findings land as proposals with a verdict "
              "grammar",
              len(incongruence) == 1
              and "Tremor" in props.describe(incongruence[0])[0]
              and "Acknowledged" in props.adopt(db, manuscript["id"],
                                                incongruence[0]))

        lenses.add_lens(manuscript, "clarity",
                        "Flag sentences that assert a claim without "
                        "argument or example.")
        check("lens ratified into _lenses/ and listed",
              lenses.list_lenses(manuscript)[0]["name"] == "clarity")
        lens_llm = FakeLLM({"findings": [
            {"quote": grounded_quote, "note": "Asserted without argument."},
            {"quote": "nowhere text", "note": "hallucinated"},
        ]})
        session, _ = api.ensure_session(db, manuscript)
        run = lenses.run_lens(db, manuscript, session, "clarity",
                              "01-choice.md", lens_llm)
        check("lens run gates findings and stores its own batch",
              len(run["findings"]) == 1 and run["dropped_ungrounded"] == 1
              and run["findings"][0]["kind"] == "lens"
              and "THE LENS" in lens_llm.system
              and "never caused; it causes" in lens_llm.system, str(run))
        verdict = api.review(db, manuscript, session, 1, "rejected",
                             "Assertion is fine here — the sermon register "
                             "argues by declaration.", kinds=("lens",))
        check("lens verdicts flow through record_review as evidence",
              verdict["review"]["decision"] == "rejected"
              and db.one("SELECT state FROM guidance_history WHERE id = ?",
                         (run["findings"][0]["id"],))["state"] != "proposed")
        registered = lenses.register_findings(
            db, manuscript, session, "clarity", "01-choice.md",
            [{"quote": grounded_quote, "note": "External agent finding."},
             {"quote": "still nowhere", "note": "dropped"}])
        check("registration door hygiene-gates external findings into the "
              "same store",
              len(registered["findings"]) == 1
              and registered["dropped_ungrounded"] == 1
              and _json.loads(registered["findings"][0]["metadata"])[
                  "source"] == "external")

        # --- illustration placement pipeline: scan → stage → triage ---
        from authorlm import placement

        anchor_para = ("The road is a ladder laid flat, and every step "
                       "asks again.")
        filler = " ".join(["The road runs on and the walker keeps walking "
                           "toward what is not yet."] * 120)
        (ms / "04-road.md").write_text(
            f"# **The Road**\n\n{filler}\n\n{anchor_para}\n")
        api.collect(db, manuscript, {})
        api.add_style_element(db, manuscript, "illustration-placement",
                              "Concretize a recurring metaphor once, at "
                              "its strongest occurrence.",
                              file="04-road.md")
        spot_llm = FakeLLM({"proposals": [
            {"anchor": anchor_para,
             "description": "a ladder lying flat along a road, rungs "
                            "receding to the horizon",
             "criterion": "2", "rationale": "metaphor at its strongest",
             "revises": None},
            {"anchor": "no such text anywhere",
             "description": "dropped", "criterion": "2",
             "rationale": "bad anchor", "revises": None},
        ]})
        report = placement.scan(db, manuscript, spot_llm,
                                files=["04-road.md"])
        check("spot-finder stages verbatim-verified proposals and drops "
              "unverifiable anchors",
              len(report["staged"]) == 1
              and report["dropped_unverifiable"] == 1
              and "PLACEMENT LAW" in spot_llm.user
              and "BUDGET" in spot_llm.user
              and "strongest occurrence" in spot_llm.user, str(report))
        check("scan is idempotent against open proposals",
              placement.scan(db, manuscript, spot_llm,
                             files=["04-road.md"])["staged"] == [])
        staged = placement.open_proposals(db, manuscript["id"])[0]
        result = placement.decide(
            db, manuscript, staged["id"], "accept",
            revised_description="a ladder lying flat along an empty road")
        text_now = (ms / "04-road.md").read_text()
        check("modified acceptance writes the revised tag after the anchor",
              result["modified"]
              and ("[Illustration: a ladder lying flat along an empty "
                   "road]") in text_now
              and text_now.index(anchor_para)
              < text_now.index("[Illustration: a ladder"), text_now[-300:])
        ev_row = db.one(
            "SELECT * FROM evidence WHERE evidence_type = 'illus_triage' "
            "ORDER BY created_at DESC")
        check("triage revision lands as modified evidence with the diff",
              ev_row["signal"] == "modified"
              and "became" in ev_row["target"], str(dict(ev_row)))
        # Reject with a reason; then stale when the anchor vanishes.
        spot_llm2 = FakeLLM({"proposals": [
            {"anchor": anchor_para, "description": "second idea",
             "criterion": "2", "rationale": "r", "revises": None}]})
        placement.scan(db, manuscript, spot_llm2, files=["04-road.md"])
        p2 = placement.open_proposals(db, manuscript["id"])[0]
        placement.decide(db, manuscript, p2["id"], "reject",
                         reason="Too decorative for this essay.")
        check("rejection records the author's reason verbatim",
              _json.loads(db.one(
                  "SELECT metadata FROM evidence WHERE evidence_type = "
                  "'illus_triage' ORDER BY created_at DESC")["metadata"])
              ["explanation"] == "Too decorative for this essay.")
        check("a rejected proposal never resurrects on re-scan",
              placement.scan(db, manuscript, spot_llm2,
                             files=["04-road.md"])["staged"] == [])
        spot_llm3 = FakeLLM({"proposals": [
            {"anchor": anchor_para, "description": "third idea",
             "criterion": "2", "rationale": "r", "revises": None}]})
        placement.scan(db, manuscript, spot_llm3, files=["04-road.md"])
        (ms / "04-road.md").write_text(
            (ms / "04-road.md").read_text().replace(anchor_para,
                                                    "The road changed."))
        staled = placement.sweep_stale(db, manuscript)
        check("proposals whose anchor vanished go stale, never guessed",
              len(staled) == 1
              and not placement.open_proposals(db, manuscript["id"]))

        # Deterministic guards: toc exclusion + pacing budget + arbiter.
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "01-choice.md"\n\n'
            '[[chapter]]\nfile = "00-intro.md"\n'
            'illustrations = "none"\n\n'
            '[[chapter]]\nfile = "04-road.md"\n')
        report = placement.scan(db, manuscript, spot_llm2,
                                files=["00-intro.md"])
        check("toc illustrations=none files are never scanned",
              report["calls"] == 0 and report["files"] == [], str(report))
        (ms / "05-tiny.md").write_text(
            "# **Tiny**\n\nShort words here.\n\n"
            "[Illustration: existing emblem]\n")
        tiny_report = placement.scan(db, manuscript, spot_llm2,
                                     files=["05-tiny.md"])
        check("a chapter at its pacing budget is skipped deterministically",
              tiny_report["calls"] == 0
              and tiny_report["skipped_at_budget"] == ["05-tiny.md"],
              str(tiny_report))
        # Arbiter: two duplicate-metaphor proposals across files; the
        # arbiter keeps one, cuts the other with a reason — no evidence.
        anchor_choice = "And the second is like unto the first."
        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text() + f"\n\n{anchor_choice}\n")
        dup0 = FakeLLM({"proposals": [
            {"anchor": "The road changed.",
             "description": "a ladder to the clouds",
             "criterion": "2", "rationale": "r", "revises": None}]})
        placement.scan(db, manuscript, dup0, files=["04-road.md"])
        dup1 = FakeLLM({"proposals": [
            {"anchor": anchor_choice, "description": "a ladder to the sky",
             "criterion": "2", "rationale": "r", "revises": None}]})
        placement.scan(db, manuscript, dup1, files=["01-choice.md"])
        pid = [r["id"] for r in placement.open_proposals(db, manuscript["id"])
               if r["file"] == "01-choice.md"][0]
        ev_before = db.one("SELECT COUNT(*) AS n FROM evidence "
                           "WHERE evidence_type = 'illus_triage'")["n"]
        arb_llm = FakeLLM({"cut": [{"id": pid,
                                    "reason": "duplicate ladder home"}]})
        arb = placement.arbitrate(db, manuscript, arb_llm)
        cut_row = dict(db.one(
            "SELECT * FROM illus_proposals WHERE id = ?", (pid,)))
        check("arbiter cuts duplicates with a reason, never as evidence",
              [c["id"] for c in arb["arbiter_cut"]] == [pid]
              and cut_row["state"] == "rejected"
              and _json.loads(cut_row["metadata"])["by"] == "arbiter"
              and db.one("SELECT COUNT(*) AS n FROM evidence WHERE "
                         "evidence_type = 'illus_triage'")["n"] == ev_before,
              str(arb))

        # --- prerequisite-gap first mentions: terms of art, not casual words ---
        # Repro from improvement task it-e34cf5227223: 'wandered through time
        # and space' must not count as the first mention of concept 'Space'.
        from authorlm.guidance import _first_mentions
        gap_files = {"01.md": (
            "The Dead wandered through time and space in search of redemption.\n\n"
            "The mutable we name the realm of qualities.\n\n"
            "When ye discern difference in position and direction, ye name it Space.\n"
        )}
        gap_names = {"n-space": ["Space"], "n-realm": ["Realm of Qualities"]}
        mentions = _first_mentions(gap_files, gap_names)
        check("casual word use does not count as a concept mention",
              mentions["n-realm"] < mentions["n-space"],
              f"positions: {mentions}")
        check("multi-word names still match case-insensitively",
              "n-realm" in mentions)
        check("a concept only ever used casually has no first mention",
              "n-space" not in _first_mentions(
                  {"01.md": "They wandered through time and space.\n"},
                  {"n-space": ["Space"]}))
        heading_pos = _first_mentions(
            {"01.md": "## Discernment\n\nVirtue is chosen for effectiveness.\n",
             "02.md": "Discernment is the measure of distinctions.\n"},
            {"n-disc": ["Discernment"]})
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
            "resolve_improvement", "alias_concept", "merge_concepts",
            "retire_policy", "merge_policies", "convert_policy_to_style",
            "define_style_guide", "add_style_element", "retire_style_element",
            "attach_style", "get_style", "get_profile", "run_sweep",
            "get_illustration_prompt", "scan_illustrations",
            "triage_illustrations",
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
