"""The critique pass (design §5–§7): preflight gate, context package,
output contract + echo validation, staging as critique threads, verdicts
with free undo, marked-text composition (pending-form grammar incl.
insertions), resolution matching (author post-edits win), rollback pin.
Drive is not touched: the pure functions the Doc verbs compose are what's
tested here.

Run: python3 tests/test_passes.py
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

from authorlm import api, critique, passes, summaries as sums  # noqa: E402
from authorlm import threads as th  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.db import loads  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


class Stub(http.server.BaseHTTPRequestHandler):
    """Summarizer → canned; editor → scripted responses (a queue so a
    contract violation can be followed by a corrected reply)."""
    editor_replies: list[dict] = []
    editor_calls: list[str] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        system = body["messages"][0]["content"]
        user = body["messages"][-1]["content"]
        if "THE UNIT: " in user:
            unit = user.split("THE UNIT: ", 1)[1].split("\n", 1)[0]
            content = f"MOVES: summary of {unit}."
        elif "Essay editor" in system:
            Stub.editor_calls.append(user)
            content = json.dumps(Stub.editor_replies.pop(0))
        else:
            content = json.dumps({"concepts": [], "links": [], "aliases": []})
        payload = json.dumps({
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


ESSAY = ("# Alpha\n\nFirst paragraph of alpha, plainly stated.\n\n"
         "Second paragraph carries the argument forward.\n\n"
         "Third paragraph closes the essay.\n")


def good_reply(paragraphs):
    return {
        "paragraphs": [
            {"n": 1, "echo": passes.echo_of(paragraphs[0]), "action": "keep"},
            {"n": 2, "echo": passes.echo_of(paragraphs[1]), "action": "replace",
             "new": "First paragraph of alpha, stated with care.",
             "why": "serves the clarity intent", "intent_id": "di-x"},
            {"n": 3, "echo": passes.echo_of(paragraphs[2]), "action": "keep"},
            {"n": 4, "echo": passes.echo_of(paragraphs[3]), "action": "replace",
             "new": "Third paragraph closes the essay decisively.",
             "why": "the ending should land", "intent_id": None},
        ],
        "insertions": [{"after": 2, "new": "A bridging paragraph, new.",
                        "why": "the intent asks for a bridge",
                        "intent_id": "di-x"}],
        "suggestions": [{"kind": "glossary", "text": "Add 'alpha' to a glossary.",
                         "intent_id": None}],
    }


def main_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-passes-"))
    server = http.server.HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ws = root / "ws"
        ms = ws / "book"
        ms.mkdir(parents=True)
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "part.md"\n\n'
            '[[chapter]]\nfile = "alpha.md"\nparent = "part.md"\n\n'
            '[[chapter]]\nfile = "beta.md"\nparent = "part.md"\n')
        (ms / "part.md").write_text("# Part\n\nThe part opener.\n")
        (ms / "alpha.md").write_text(ESSAY)
        (ms / "beta.md").write_text("# Beta\n\nBeta text.\n")
        (ws / ".authorlm").mkdir()
        (ws / ".authorlm" / "config.toml").write_text(
            "[llm]\nenabled = true\nprovider = \"openai\"\n"
            f"base_url = \"http://127.0.0.1:{server.server_port}/v1\"\n"
            "model = \"general\"\n\n"
            "[critique]\neditor_model = \"frontier\"\n"
            "summarizer_model = \"cheap\"\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms)])
        db = api.open_db(str(ws))
        manuscript = api.get_manuscript(db)
        mid = manuscript["id"]
        import tomllib
        config = tomllib.loads((ws / ".authorlm" / "config.toml").read_text())

        print("scope chain & gate:")
        check("scope chain = file, toc ancestors, manuscript-wide",
              passes.scope_chain(manuscript, "alpha.md")
              == ["alpha.md", "part.md", None])
        crit_src = db.source("critic", "Test Critic", "test")
        critique.import_manifest(db, mid, {
            "source": {"name": "Test Critic", "detail": "test"},
            "items": [
                {"kind": "intent", "unit": "Alpha", "ordinal": 1,
                 "text": "Clarify the first paragraph.", "scope": "alpha.md"},
                {"kind": "intent", "unit": "Part", "ordinal": 1,
                 "text": "Tighten every essay in the part.", "scope": "part.md"},
                {"kind": "intent", "unit": "Beta", "ordinal": 1,
                 "text": "Beta-only work.", "scope": "beta.md"},
                {"kind": "intent", "unit": "Global", "ordinal": 1,
                 "text": "Book-wide rule.", "scope": None},
            ]})
        gate = passes.preflight(db, manuscript, "alpha.md")
        check("gate sees proposed intents in the scope chain, not siblings",
              not gate["clear"]
              and {i["statement"] for i in gate["proposed_intents"]}
              == {"Clarify the first paragraph.",
                  "Tighten every essay in the part.", "Book-wide rule."})
        p = passes.ensure_pass(db, mid, crit_src)
        try:
            passes.build_context(db, manuscript, "alpha.md", p)
            blocked = False
        except RuntimeError as err:
            blocked = "preflight" in str(err)
        check("build_context refuses while the gate is dirty", blocked)
        # Accept the alpha + part items, reject the global one → gate clear.
        pend = critique.pending(db, mid)["intents"]
        for it in pend:
            if it["scope"] in ("alpha.md", "part.md"):
                critique.accept_intent(db, mid, it)
            elif it["scope"] is None:
                critique.reject_intent(db, mid, it, "not now")
        check("gate clears once this essay's chain is triaged (beta's item "
              "is irrelevant)",
              passes.preflight(db, manuscript, "alpha.md")["clear"])

        print("summaries gate:")
        try:
            passes.build_context(db, manuscript, "alpha.md", p)
            blocked = False
        except RuntimeError as err:
            blocked = "missing summaries" in str(err)
        check("build_context refuses when summaries are missing", blocked)
        sums.rebuild(db, manuscript, sums.summarizer_llm(config))
        ctx = passes.build_context(db, manuscript, "alpha.md", p)
        check("context: 4 paragraphs, 2 active intents in scope, before/after",
              len(ctx["paragraphs"]) == 4 and len(ctx["intents"]) == 2
              and [e["file"] for e in ctx["before"]] == ["part.md"]
              and [e["file"] for e in ctx["after"]] == ["beta.md"])
        msg = passes.render_user_message(ctx)
        check("user message numbers paragraphs and carries the contract blocks",
              "[1] # Alpha" in msg and "=== INTENTS ===" in msg
              and "Tighten every essay" in msg and "Beta-only work" not in msg)
        # Force semantics: dirty gate + force → runs, but without the items.
        critique.import_manifest(db, mid, {
            "source": {"name": "Test Critic", "detail": "test"},
            "items": [{"kind": "intent", "unit": "Alpha", "ordinal": 2,
                       "text": "A late unconfirmed item.", "scope": "alpha.md"}]})
        forced = passes.build_context(db, manuscript, "alpha.md", p, force=True)
        check("--force runs WITHOUT unconfirmed items (never with them)",
              forced["forced"] and all(
                  i["statement"] != "A late unconfirmed item."
                  for i in forced["intents"]))
        late = [i for i in critique.pending(db, mid)["intents"]][0]
        critique.reject_intent(db, mid, late, "no")

        print("output contract:")
        paras = ctx["paragraphs"]
        ok = passes.validate_output(good_reply(paras), paras)
        check("valid reply → 2 edits (verbatim old from source), 1 insertion, "
              "1 suggestion",
              len(ok["edits"]) == 2 and ok["edits"][0]["old"] == paras[1]
              and len(ok["insertions"]) == 1 and len(ok["suggestions"]) == 1)
        bad = good_reply(paras)
        bad["paragraphs"][1]["echo"] = "wrong words here now"
        try:
            passes.validate_output(bad, paras)
            raised = False
        except passes.ContractError as err:
            raised = "echo mismatch" in str(err)
        check("echo mismatch discards the whole response", raised)
        short = good_reply(paras)
        short["paragraphs"].pop()
        try:
            passes.validate_output(short, paras)
            raised = False
        except passes.ContractError:
            raised = True
        check("wrong paragraph count is rejected", raised)

        print("run_editor retries on contract violation:")
        Stub.editor_replies = [bad, good_reply(paras)]
        llm = passes.editor_llm(config)
        check("editor model comes from [critique] editor_model",
              llm.model == "frontier")
        result = passes.run_editor(llm, ctx)
        check("first (bad) reply rejected, corrected reply accepted",
              len(Stub.editor_calls) == 2
              and "PREVIOUS RESPONSE WAS REJECTED" in Stub.editor_calls[1]
              and len(result["edits"]) == 2)

        print("staging:")
        out = passes.stage(db, manuscript, p, "alpha.md", result)
        staged = passes.staged_threads(db, mid, "alpha.md")
        check("3 critique threads staged in document order (¶2, insert after "
              "¶2, ¶4)",
              len(staged) == 3
              and [loads(t["metadata"], {})["anchor_paragraph"] for t in staged]
              == [2, 2, 4]
              and staged[1]["proposed_old"] == ""
              and all(t["origin_type"] == "critique" for t in staged))
        check("suggestion filed as a proposed, system-sourced intent with lineage",
              len(out["suggestions"]) == 1
              and out["suggestions"][0]["status"] == "proposed"
              and out["suggestions"][0]["source_id"] == db.source("system")
              and loads(out["suggestions"][0]["metadata"], {})["lineage"]["file"]
              == "alpha.md")
        check("essay state → proposed", passes.essay_state(p, "alpha.md")
              == "proposed")
        check("author-comment open_threads never sees critique threads",
              th.open_threads(db, mid) == [])

        print("verdicts + undo:")
        t_edit, t_ins, t_end = staged
        passes.verdict(db, mid, t_edit, "accept")
        passes.verdict(db, mid, t_ins, "revise", "A bridging paragraph, mine.")
        passes.verdict(db, mid, t_end, "reject", "the ending is fine")
        st = {t["id"]: t for t in passes.staged_threads(db, mid, "alpha.md")}
        check("accept / revise / reject land",
              st[t_edit["id"]]["state"] == "accepted"
              and st[t_ins["id"]]["state"] == "accepted"
              and st[t_ins["id"]]["proposed_new"] == "A bridging paragraph, mine."
              and st[t_end["id"]]["state"] == "rejected")
        passes.verdict(db, mid, st[t_end["id"]], "undo")
        passes.verdict(db, mid, st[t_ins["id"]], "undo")
        st = {t["id"]: t for t in passes.staged_threads(db, mid, "alpha.md")}
        check("undo is free: rejected → proposed; revised → proposed with the "
              "original wording restored",
              st[t_end["id"]]["state"] == "proposed"
              and st[t_ins["id"]]["state"] == "proposed"
              and st[t_ins["id"]]["proposed_new"] == "A bridging paragraph, new.")
        passes.verdict(db, mid, st[t_ins["id"]], "accept")
        evs = db.all("SELECT signal FROM evidence WHERE manuscript_id = ? AND "
                     "evidence_type = 'critique_edit'", (mid,))
        check("every verdict (incl. undo) is evidence",
              {e["signal"] for e in evs}
              >= {"accepted", "revised", "rejected", "undone"})

        print("marked text (diff-write composition):")
        threads = passes.staged_threads(db, mid, "alpha.md")
        marked = passes.compose_marked_text(ESSAY, threads)
        check("accepted replace renders <<old>>{{new}}; insertion renders "
              "{{new}} after its anchor; rejected/proposed untouched",
              "<<First paragraph of alpha, plainly stated.>>{{First paragraph "
              "of alpha, stated with care.}}" in marked
              and "{{A bridging paragraph, new.}}" in marked
              and "Third paragraph closes the essay.\n" in marked
              and "decisively" not in marked)
        drifted = ESSAY.replace("plainly stated", "PLAINLY stated")
        try:
            passes.compose_marked_text(drifted, threads)
            raised = False
        except ValueError as err:
            raised = "drifted" in str(err)
        check("drifted paragraph fails loudly before any Doc write", raised)
        check("strip_pending on the marked text gives back the pristine essay",
              th.strip_pending(marked)[0].strip() == ESSAY.strip())

        print("resolution (author post-edits win):")
        for t in threads:
            if t["state"] == "accepted":
                db.update("doc_threads", t["id"], {"state": "written"})
        # The author edits the insertion's {{new}} half in the Doc.
        edited = marked.replace("{{A bridging paragraph, new.}}",
                                "{{A bridging paragraph, in my voice.}}")
        final, forms = passes.final_text_from_marked(edited)
        check("final text = every form's current {{new}}",
              "stated with care." in final
              and "A bridging paragraph, in my voice." in final
              and "<<" not in final and "{{" not in final)
        diffs = passes.record_resolution(db, mid, "alpha.md", forms)
        check("modified acceptance recorded as a proposal→final diff",
              len(diffs) == 1
              and diffs[0]["proposal"] == "A bridging paragraph, new."
              and diffs[0]["final"] == "A bridging paragraph, in my voice.")
        states = {t["state"] for t in passes.staged_threads(
            db, mid, "alpha.md", states=("cleaned", "declined", "written"))}
        check("written threads closed (cleaned)", states == {"cleaned"})

        print("pin & rollback bookkeeping:")
        passes.pin_version(db, p, "alpha.md", "mv-test")
        p2 = passes.active_pass(db, mid)
        check("pinned version recorded on the pass row",
              passes.pinned_version(p2, "alpha.md") == "mv-test")

        print("CLI surface (no Drive):")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_main(["--workspace", str(ws), "critique", "status"])
        check("critique status shows the pass table",
              "Edit pass" in buf.getvalue() and "alpha.md" in buf.getvalue())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_main(["--workspace", str(ws), "critique", "edits", "alpha.md"])
        listing = buf.getvalue()
        check("critique edits lists only the still-open edit (the undone one), "
              "with old/new/why rendered",
              "decisively" in listing and "why: the ending should land" in listing
              and "stated with care" not in listing)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_main(["--workspace", str(ws), "critique", "show", "--query",
                      "first paragraph"])
        check("critique show --query finds settled items with their status",
              "Clarify the first paragraph" in buf.getvalue()
              and "active" in buf.getvalue())
        rejected = db.one("SELECT id FROM declared_intents WHERE statement = "
                          "'Book-wide rule.'")["id"]
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "critique", "reason",
                      rejected[:11], "--text", "the real why"])
        check("critique reason amends a rejected item's reason",
              db.one("SELECT outcome FROM declared_intents WHERE id = ?",
                     (rejected,))["outcome"] == "the real why")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "critique", "reopen",
                      rejected[:11]])
        check("critique reopen sends it back to proposed",
              db.one("SELECT status FROM declared_intents WHERE id = ?",
                     (rejected,))["status"] == "proposed")

        print("concept revive (inverse of retire):")
        from authorlm import concepts as cg
        cg.add_concept(db, mid, "Gravity", notes="pulls")
        cg.add_concept(db, mid, "Mass", notes="has weight")
        cg.add_concept(db, mid, "Void", notes="empty")
        cg.link_concepts(db, mid, "Gravity", "depends_on", "Mass")
        cg.link_concepts(db, mid, "Gravity", "contrasts_with", "Void")
        g = dict(cg.get_concept(db, mid, "Gravity"))
        db.update("concept_nodes", g["id"],
                  {"status": "realized", "introduced_in": "alpha.md"})
        g = dict(cg.get_concept(db, mid, "Gravity"))
        # An unrelated retirement first: Void goes down, taking its edge —
        # that edge must NOT come back when Gravity is revived.
        cg.retire_concept(db, mid, dict(cg.get_concept(db, mid, "Void")))
        n_edges = cg.retire_concept(db, mid, g)
        check("retire records prior status and takes down live edges",
              n_edges == 1 and db.one("SELECT status FROM concept_nodes WHERE "
                                      "id = ?", (g["id"],))["status"]
              == "retired")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "concept", "revive", "Gravity"])
        g2 = dict(cg.get_concept(db, mid, "Gravity"))
        edges = {(e["relation"], e["status"]) for e in db.all(
            "SELECT * FROM concept_edges WHERE from_node = ?", (g2["id"],))}
        check("revive restores realized + introduced_in and only ITS "
              "collateral edge (Void's stays retired)",
              g2["status"] == "realized" and g2["introduced_in"] == "alpha.md"
              and ("depends_on", "declared") in edges
              and ("contrasts_with", "retired") in edges)
        out = api.curate_concepts(db, manuscript,
                                  [{"op": "revive", "name": "Nope"}])
        check("curate_concepts revive op reports a missing concept per-op",
              out["results"][0]["ok"] is False)

        print("style move:")
        from authorlm import styles
        house = styles.create_guide(db, mid, "house")
        part_guide = styles.create_guide(db, mid, "part", parent_name="house")
        el = styles.add_element(db, mid, "tone", "Be grave.", guide=house)
        moved = api.move_style_law(db, manuscript, el["id"][:10],
                                       guide_name="part")
        check("move re-scopes an element to another guide by name",
              moved["guide"] == "part"
              and db.one("SELECT guide_id FROM style_laws WHERE id = ?",
                         (el["id"],))["guide_id"] == part_guide["id"])
        moved = api.move_style_law(db, manuscript, el["id"][:10],
                                       file="alpha.md")
        check("move to a file clears the guide",
              moved["file"] == "alpha.md"
              and db.one("SELECT guide_id, file FROM style_laws WHERE id "
                         "= ?", (el["id"],))["guide_id"] is None)
        try:
            api.move_style_law(db, manuscript, el["id"][:10],
                                   guide_name="nope")
            raised = False
        except LookupError:
            raised = True
        check("move to an unknown guide is a loud error", raised)

        print("prompt registry:")
        from authorlm import prompt_registry as pr
        check("editor prompt registered as a file prompt",
              pr.by_name("editor").file == "editor.md"
              and "echo" in pr.by_name("editor").text())
    finally:
        server.shutdown()
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    _assert_offline()
    main_test()
