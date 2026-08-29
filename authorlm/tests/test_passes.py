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

from authorlm import api, critique, gdocs, passes, summaries as sums  # noqa: E402
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

        print("stale summary genuinely blocks the edit pass (source_hash "
              "mismatch — 'a lie about the text' — not just a missing row):")
        beta_row = db.one(
            "SELECT * FROM essay_summaries WHERE manuscript_id = ? AND "
            "file = ?", (mid, "beta.md"))
        db.update("essay_summaries", beta_row["id"], {"source_hash": "deadbeef"})
        check("beta.md now reads stale by source_hash",
              next(r for r in sums.status(db, manuscript) if r["file"] == "beta.md")
              ["state"] == "stale")
        try:
            passes.build_context(db, manuscript, "alpha.md", p)
            blocked, err_msg = False, ""
        except RuntimeError as err:
            blocked, err_msg = True, str(err)
        check("build_context refuses to run: the guard is real, not "
              "decorative — a stale summary in the before/after context "
              "blocks the pass exactly as a missing one does",
              blocked and "stale summaries" in err_msg and "beta.md" in err_msg,
              err_msg)
        sums.rebuild_one(db, manuscript, "beta.md", sums.summarizer_llm(config))
        ctx_after_fix = passes.build_context(db, manuscript, "alpha.md", p)
        check("once the stale summary is rebuilt, the guard clears and the "
              "pass runs again",
              ctx_after_fix["after"][0]["state"] == "fresh")

        print("an essay with no toc.toml entry (§12.4 item 3) — the edit "
              "pass cannot place it either, so it refuses rather than "
              "reading the whole book as settled context behind it:")
        (ms / "zeta.md").write_text("# Zeta\n\nNobody put this in the toc.\n")
        sums.rebuild(db, manuscript, sums.summarizer_llm(config))
        try:
            passes.build_context(db, manuscript, "zeta.md", p)
            blocked, err_msg = False, ""
        except LookupError as err:
            blocked, err_msg = True, str(err)
        check("build_context on the unlisted essay REFUSES — the appended "
              "position is a fallback, not a declaration, and the edit "
              "pass reads the same before/after split the drafting gate "
              "does",
              blocked and "no toc.toml entry" in err_msg, err_msg)
        check("...naming the universally applicable remedy (a toc.toml "
              "entry) FIRST, and marking the write-start remedy as "
              "drafting-only — a critique-pass user must not be handed "
              "advice for a different verb",
              err_msg.index("Add it to toc.toml")
              < err_msg.index("write start")
              and "for that writeup only" in err_msg, err_msg)
        ctx_listed = passes.build_context(db, manuscript, "alpha.md", p)
        check("a LISTED essay still builds its context with the unlisted "
              "file sitting on disk — the refusal is about the target, "
              "not a manuscript-wide freeze",
              [e["file"] for e in ctx_listed["before"]] == ["part.md"]
              and [e["file"] for e in ctx_listed["after"]]
              == ["beta.md", "zeta.md"],
              str(([e["file"] for e in ctx_listed["before"]],
                   [e["file"] for e in ctx_listed["after"]])))
        (ms / "zeta.md").unlink()
        sums.rebuild(db, manuscript, sums.summarizer_llm(config))

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

        print("critique write order (same-anchor insert before replace):")
        # A replace of paragraph n wraps it as <<old>>{{new}}. Writing the
        # replace first makes the subsequent insert locate `old` inside that
        # wrapped form and plant {{insert}} between old and >>, corrupting
        # the Doc. Inserts at the same anchor must precede replaces.
        order = gdocs.critique_write_order([
            {"id": "rep6", "proposed_old": "p6", "metadata":
             json.dumps({"anchor_paragraph": 6})},
            {"id": "ins5", "proposed_old": "", "metadata":
             json.dumps({"anchor_paragraph": 5})},
            {"id": "rep5", "proposed_old": "p5", "metadata":
             json.dumps({"anchor_paragraph": 5})},
            {"id": "ins4", "proposed_old": "", "metadata":
             json.dumps({"anchor_paragraph": 4})},
        ])
        check("higher anchors first; inserts before replaces at same anchor",
              [t["id"] for t in order] == ["rep6", "ins5", "rep5", "ins4"],
              str([t["id"] for t in order]))

        print("resolution (author post-edits win):")
        for t in threads:
            if t["state"] == "accepted":
                db.update("doc_threads", t["id"], {"state": "written"})
        written = passes.staged_threads(db, mid, "alpha.md",
                                        states=("written",))
        # The author edits the insertion's {{new}} half in the Doc.
        edited = marked.replace("{{A bridging paragraph, new.}}",
                                "{{A bridging paragraph, in my voice.}}")
        # A foreign margin-thread form on the same tab must NOT be approved.
        polluted = edited.replace(
            "Third paragraph closes the essay.",
            "<<Third paragraph closes the essay.>>{{Foreign margin rewrite.}}")
        final, forms = passes.final_text_from_marked(polluted, written=written)
        check("final text = critique forms' current {{new}}; foreign forms "
              "stay OLD",
              "stated with care." in final
              and "A bridging paragraph, in my voice." in final
              and "Third paragraph closes the essay." in final
              and "Foreign margin rewrite" not in final
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

        print("staging re-run replaces cleanly (no UNIQUE crash):")
        again = passes.stage(db, manuscript, p, "alpha.md", result)
        check("re-run stages without IntegrityError and resets to proposed",
              len(again["staged"]) == 3
              and all(t["state"] == "proposed" for t in again["staged"]))

        # #24 x #26: #26 made stage() replace in place; #24 gates push_doc
        # on threads still 'written'. A re-run that reset 'written' to
        # 'proposed' would quietly lift that gate while the Doc still
        # carries the form — the two fixes are individually right and
        # leave this hole between them.
        pending = passes.staged_threads(db, mid, "alpha.md")[0]
        db.update("doc_threads", pending["id"], {"state": "written"})
        try:
            passes.stage(db, manuscript, p, "alpha.md", result)
            refused = False
        except ValueError as err:
            refused = "already written" in str(err)
        after = db.one("SELECT state FROM doc_threads WHERE id = ?",
                       (pending["id"],))["state"]
        check("re-staging over a form already written to the Doc is "
              "refused, and the form stays written so the push gate holds",
              refused and after == "written", f"refused={refused} state={after}")
        db.update("doc_threads", pending["id"], {"state": "proposed"})

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

        print("recovery: critique rollback snapshots uncollected edits "
              "(BUG-2 / A1):")
        # Self-contained fixture — a fresh manuscript, independent of the
        # alpha/beta/part narrative above, so the destructive write under
        # test can't be confused with anything already staged on 'book'.
        import argparse
        from authorlm.cli import _critique_rollback

        rb_ws = root / "rollback-ws"
        rb_ms = rb_ws / "book"
        rb_ms.mkdir(parents=True)
        (rb_ms / "solo.md").write_text("# Solo\n\nOriginal pinned content.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(rb_ws), "init", "--name", "book",
                      "--path", str(rb_ms)])
        rb_db = api.open_db(str(rb_ws))
        rb_manuscript = api.get_manuscript(rb_db)
        rb_mid = rb_manuscript["id"]
        api.collect(rb_db, rb_manuscript, {})  # v1: the version to pin
        rb_p = passes.ensure_pass(rb_db, rb_mid, rb_db.source("system"))
        v1_row = rb_db.one(
            "SELECT id FROM manuscript_versions WHERE manuscript_id = ? "
            "AND version_no = 1", (rb_mid,))
        passes.pin_version(rb_db, rb_p, "solo.md", v1_row["id"])

        # The author edits the file after the pin but never collects — the
        # exact state 'critique rollback' must not silently destroy.
        UNCOLLECTED_ROLLBACK = ("# Solo\n\nUNCOLLECTED AUTHOR EDIT, "
                                "NEVER COLLECTED.\n")
        (rb_ms / "solo.md").write_text(UNCOLLECTED_ROLLBACK)

        rb_args = argparse.Namespace(target="solo.md", workspace=str(rb_ws))
        with contextlib.redirect_stdout(io.StringIO()):
            _critique_rollback(rb_db, rb_manuscript, rb_args)
        check("rollback restores the pinned content",
              (rb_ms / "solo.md").read_text()
              == "# Solo\n\nOriginal pinned content.\n")
        rb_versions = rb_db.all(
            "SELECT files FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no", (rb_mid,))
        recovered = any(
            loads(v["files"], {}).get("solo.md") == UNCOLLECTED_ROLLBACK
            for v in rb_versions)
        check("critique rollback snapshots the uncollected edit into "
              "history before overwriting it (BUG-2 / A1)", recovered)

        print("recovery/it-x7-1v2: critique resolve reads the Doc tab as "
              "MARKDOWN (export + split_tabbed_export, never the textRun "
              "walk) and three-ways it against local:")
        import json as _json

        import authorlm.gdocs as _gdocs_mod
        from authorlm.cli import _critique_resolve_essay
        from authorlm.db import ko_fields as _ko

        class _ResolveDocFake:
            """Minimal Drive+Docs stub for the real critique-resolve
            flow. Only files().export() (whole-Doc markdown, forms
            intact — the same shape the real exporter produces) and
            documents().get(includeTabsContent=True) (tab titles/order,
            what walk_tabs needs) are implemented. There is no
            batchUpdate at all: if resolve ever tried to push, it would
            raise AttributeError instead of silently succeeding — that
            absence IS this suite's 'no push' proof."""

            def __init__(self, tabs):
                self.tabs = [{"id": f"tab-{i}", "title": t, "text": x}
                             for i, (t, x) in enumerate(tabs, 1)]

            def set_tab(self, title, text):
                for t in self.tabs:
                    if t["title"] == title:
                        t["text"] = text

            def files(self):
                outer = self

                class _Files:
                    def export(self, fileId=None, mimeType=None):
                        def as_markdown(text):
                            paras = [ln for ln in text.split("\n")
                                    if ln.strip()]
                            return ("\n\n".join(paras)
                                    + ("\n" if paras else ""))
                        whole = "\n".join(
                            f"# **{t['title']}**\n\n{as_markdown(t['text'])}"
                            for t in outer.tabs)

                        class _Req:
                            def execute(self):
                                return whole.encode("utf-8")
                        return _Req()
                return _Files()

            def documents(self):
                outer = self

                class _Documents:
                    def get(self, documentId=None,
                           includeTabsContent=None):
                        tabs = [{"tabProperties": {"tabId": t["id"],
                                                   "title": t["title"]},
                                "childTabs": []} for t in outer.tabs]

                        class _Req:
                            def execute(self):
                                return {"tabs": tabs}
                        return _Req()
                return _Documents()

        def _resolve_fixture(subdir):
            """A fresh workspace with 'solo.md' pushed (pristine, richly
            formatted) and its base hash recorded, exactly as a real
            push would leave it before 'critique write' marks any
            spans."""
            import hashlib as _hashlib

            pristine = ("# Solo\n\n"
                       "A **bold** claim opens the essay.\n\n"
                       "Original paragraph text.\n\n"
                       "- a list item\n\n"
                       "[see the source](https://example.com/source)\n")
            ws = root / subdir
            ms = ws / "book"
            ms.mkdir(parents=True)
            (ms / "solo.md").write_text(pristine)
            with contextlib.redirect_stdout(io.StringIO()):
                cli_main(["--workspace", str(ws), "init", "--name", "book",
                          "--path", str(ms)])
            db_ = api.open_db(str(ws))
            manuscript_ = api.get_manuscript(db_)
            mid_ = manuscript_["id"]
            api.collect(db_, manuscript_, {})  # v1
            passes.ensure_pass(db_, mid_, db_.source("system"))
            normalized = _gdocs_mod.normalize_markdown(pristine)
            base_hash = _hashlib.sha256(
                normalized.encode()).hexdigest()[:16]
            meta = _gdocs_mod._mapping(db_, manuscript_)
            links = meta.setdefault("gdocs", {})
            links["_master_id"] = "doc-fake"
            links["solo.md"] = {"tab_id": "tab-1", "checked_out": False,
                                "pushed_hash": base_hash}
            _gdocs_mod._save_mapping(db_, manuscript_, meta)
            return db_, manuscript_, ms, mid_, pristine

        def _stage_written(db_, mid_, proposed_new):
            written_row = _ko("dt")
            written_row.update(
                manuscript_id=mid_, origin_type="critique", origin_id="cp1",
                file="solo.md", anchor_quote=None,
                proposed_old="Original paragraph text.",
                proposed_new=proposed_new,
                note="test", state="written", our_reply_ids="[]",
                last_author_reply_id=None, scope_kind="file",
                scope_ref="solo.md",
                metadata=_json.dumps({"kind": "replace",
                                      "anchor_paragraph": 1,
                                      "intent_id": None,
                                      "original_new": proposed_new}))
            db_.insert("doc_threads", written_row)
            return written_row

        # --- Scenario 1: the real flow — pristine local, forms in the Doc
        # tab, an author post-edit to the {{new}} half (modified
        # acceptance), and an unrelated edit made ELSEWHERE in the tab.
        # Proves: heading/bold/list/link survive, the elsewhere edit
        # lands, and the modified acceptance is recorded as evidence.
        db1, ms1, msdir1, mid1, pristine1 = _resolve_fixture("resolve-ws-1")
        _stage_written(db1, mid1, "RESOLVED PARAGRAPH TEXT.")
        doc_text_1 = (
            "# Solo\n\n"
            "A **bold** claim opens the essay.\n\n"
            "<<Original paragraph text.>>{{RESOLVED PARAGRAPH TEXT, "
            "POST-EDITED IN THE DOC.}}\n\n"
            "- a list item\n\n"
            "[see the source](https://example.com/source)\n\n"
            "A sentence added only in the Doc, elsewhere in the tab, "
            "never sent locally.\n")
        fake1 = _ResolveDocFake([("solo.md", doc_text_1)])
        _orig1 = (_gdocs_mod.get_service, _gdocs_mod.get_docs_service)
        _gdocs_mod.get_service = lambda *a, **k: fake1
        _gdocs_mod.get_docs_service = lambda *a, **k: fake1
        out1 = io.StringIO()
        try:
            args1 = argparse.Namespace(target="solo.md", workspace=str(
                root / "resolve-ws-1"))
            with contextlib.redirect_stdout(out1):
                _critique_resolve_essay(db1, ms1, args1)
        finally:
            _gdocs_mod.get_service, _gdocs_mod.get_docs_service = _orig1
        resolved1 = (msdir1 / "solo.md").read_text()
        check("resolve keeps the heading, bold, list marker, and link "
              "URL intact — the Doc-as-markdown path never flattens "
              "structure the way the textRun walk did (it-x7-1v2)",
              resolved1.startswith("# Solo\n\n")
              and "**bold**" in resolved1
              and "- a list item" in resolved1
              and "[see the source](https://example.com/source)"
              in resolved1
              and "<<" not in resolved1 and "{{" not in resolved1,
              resolved1)
        check("the author's post-edit to the {{new}} half in the Doc wins",
              "RESOLVED PARAGRAPH TEXT, POST-EDITED IN THE DOC." in
              resolved1, resolved1)
        check("an edit made elsewhere in the tab (outside any pending "
              "form) lands in the local file",
              "A sentence added only in the Doc, elsewhere in the tab, "
              "never sent locally." in resolved1, resolved1)
        check("the modified acceptance is recorded as evidence (proposal "
              "→ final diff, the learnings feedstock)",
              "1 modified acceptance(s) recorded" in out1.getvalue(),
              out1.getvalue())
        row1 = db1.one("SELECT * FROM doc_threads WHERE manuscript_id = ? "
                       "AND file = 'solo.md'", (mid1,))
        check("the written thread closed (cleaned) once resolved",
              row1["state"] == "cleaned", dict(row1))

        # --- Scenario 2: a GENUINE two-sided conflict — local drifted
        # AND the Doc's settled (form-collapsed) content drifted too, in
        # unrelated ways. Resolve must stop and surface it, never pick a
        # side silently.
        db2, ms2, msdir2, mid2, pristine2 = _resolve_fixture("resolve-ws-2")
        _stage_written(db2, mid2, "RESOLVED PARAGRAPH TEXT.")
        LOCAL_DRIFT = pristine2.replace(
            "- a list item", "- a DIFFERENTLY edited list item, local only")
        (msdir2 / "solo.md").write_text(LOCAL_DRIFT)
        doc_text_2 = (
            "# Solo\n\n"
            "A **very** different claim, edited only in the Doc.\n\n"
            "<<Original paragraph text.>>{{RESOLVED PARAGRAPH TEXT.}}\n\n"
            "- a list item\n\n"
            "[see the source](https://example.com/source)\n")
        fake2 = _ResolveDocFake([("solo.md", doc_text_2)])
        _orig2 = (_gdocs_mod.get_service, _gdocs_mod.get_docs_service)
        _gdocs_mod.get_service = lambda *a, **k: fake2
        _gdocs_mod.get_docs_service = lambda *a, **k: fake2
        conflict_err = None
        try:
            args2 = argparse.Namespace(target="solo.md", workspace=str(
                root / "resolve-ws-2"))
            with contextlib.redirect_stdout(io.StringIO()):
                _critique_resolve_essay(db2, ms2, args2)
        except SystemExit as exc:
            conflict_err = str(exc)
        finally:
            _gdocs_mod.get_service, _gdocs_mod.get_docs_service = _orig2
        check("a genuine two-sided edit is surfaced as a conflict, not "
              "silently resolved",
              conflict_err is not None
              and "changed both locally and in the Doc" in conflict_err,
              conflict_err)
        check("the local file is untouched by a surfaced conflict",
              (msdir2 / "solo.md").read_text() == LOCAL_DRIFT,
              (msdir2 / "solo.md").read_text())
        row2 = db2.one("SELECT * FROM doc_threads WHERE manuscript_id = ? "
                       "AND file = 'solo.md'", (mid2,))
        check("a surfaced conflict leaves the written thread untouched",
              row2["state"] == "written", dict(row2))

        # --- Scenario 3 (BUG-2 / A1, re-founded on the real flow): local
        # drifted (an uncollected edit outside the critique/Doc flow) but
        # the Doc's settled content did NOT — 'local_ahead', not a
        # conflict. Resolve still applies the Doc's resolution (the
        # explicit act the author invoked), snapshotting the local drift
        # into version history first so it is never silently lost.
        db3, ms3, msdir3, mid3, pristine3 = _resolve_fixture("resolve-ws-3")
        _stage_written(db3, mid3, "RESOLVED PARAGRAPH TEXT.")
        UNCOLLECTED_RESOLVE = pristine3 + (
            "\nUNCOLLECTED PARAGRAPH ADDED LOCALLY, NEVER SENT TO THE "
            "DOC.\n")
        (msdir3 / "solo.md").write_text(UNCOLLECTED_RESOLVE)
        doc_text_3 = pristine3.replace(
            "Original paragraph text.",
            "<<Original paragraph text.>>{{RESOLVED PARAGRAPH TEXT.}}")
        fake3 = _ResolveDocFake([("solo.md", doc_text_3)])
        _orig3 = (_gdocs_mod.get_service, _gdocs_mod.get_docs_service)
        _gdocs_mod.get_service = lambda *a, **k: fake3
        _gdocs_mod.get_docs_service = lambda *a, **k: fake3
        try:
            args3 = argparse.Namespace(target="solo.md", workspace=str(
                root / "resolve-ws-3"))
            with contextlib.redirect_stdout(io.StringIO()):
                _critique_resolve_essay(db3, ms3, args3)
        finally:
            _gdocs_mod.get_service, _gdocs_mod.get_docs_service = _orig3
        resolved3 = (msdir3 / "solo.md").read_text()
        check("resolve still applies the Doc's resolution over a "
              "local-only drift ('local_ahead', not a conflict)",
              "RESOLVED PARAGRAPH TEXT." in resolved3
              and "UNCOLLECTED PARAGRAPH" not in resolved3, resolved3)
        res_versions = db3.all(
            "SELECT files FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no", (mid3,))
        recovered = any(
            loads(v["files"], {}).get("solo.md") == UNCOLLECTED_RESOLVE
            for v in res_versions)
        check("critique resolve snapshots the uncollected local edit into "
              "history before overwriting the file (BUG-2 / A1)", recovered)


        print("recovery: doc pull snapshots uncollected edits before "
              "overwriting (BUG-1 / A2):")
        # three_way() unconditionally prefers the Doc's tab over local
        # content whenever the file has no recorded base_hash (a
        # base-less mapped pull) — that mechanism is stubbed directly
        # here rather than through a fake tabbed export, since whether a
        # real Google export triggers it is a separate, unresolved
        # question (register C7) that A2's snapshot-first fix does not
        # depend on either way.
        dp_ws = root / "docpull-ws"
        dp_ms = dp_ws / "book"
        dp_ms.mkdir(parents=True)
        (dp_ms / "solo.md").write_text("Original collected content.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(dp_ws), "init", "--name", "book",
                      "--path", str(dp_ms)])
        dp_db = api.open_db(str(dp_ws))
        dp_manuscript = api.get_manuscript(dp_db)
        api.collect(dp_db, dp_manuscript, {})  # v1

        UNCOLLECTED_PULL = ("UNCOLLECTED PARAGRAPH, NEVER COLLECTED, ABOUT "
                            "TO BE PULLED OVER.\n")
        (dp_ms / "solo.md").write_text(UNCOLLECTED_PULL)

        def _fake_service2(config, workspace=None, interactive=False):
            return object()

        def _fake_pull_doc(db, manuscript, query=None, service=None,
                           force=False, with_comments=True,
                           docs_service=None, bridge=None):
            (Path(manuscript["path"]) / "solo.md").write_text(
                "PULLED FROM DOC.\n")
            return {"changed": ["solo.md"], "unchanged": [], "conflicts": [],
                    "local_ahead": [], "missing": [], "doc_id": "doc-1"}

        _orig2 = (_gdocs_mod.get_service, _gdocs_mod.get_docs_service,
                 _gdocs_mod.pull_doc)
        _gdocs_mod.get_service = _fake_service2
        _gdocs_mod.get_docs_service = _fake_service2
        _gdocs_mod.pull_doc = _fake_pull_doc
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                cli_main(["--workspace", str(dp_ws), "doc", "pull",
                          "solo.md"])
        finally:
            (_gdocs_mod.get_service, _gdocs_mod.get_docs_service,
             _gdocs_mod.pull_doc) = _orig2

        check("pull overwrote the file with the Doc's content",
              (dp_ms / "solo.md").read_text() == "PULLED FROM DOC.\n")
        dp_versions = dp_db.all(
            "SELECT files FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no", (dp_manuscript["id"],))
        recovered = any(
            loads(v["files"], {}).get("solo.md") == UNCOLLECTED_PULL
            for v in dp_versions)
        check("doc pull snapshots the uncollected local edit into history "
              "before the Doc overwrites it (BUG-1 / A2)", recovered)
    finally:
        server.shutdown()
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    _assert_offline()
    main_test()
