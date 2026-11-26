"""Engines and the read catalogue (authorlm/authorlm/engines/; design
docs/plans/filter-lens-consolidation.md 11.9 Q6, Q8b, Q16, Q20, Q20a-c, U8).

Hermetic: a scratch workspace; the claude-headless engine runs on a fake
subprocess and the openai-api engine on a fake client replaying responses
shaped like the live ones of 2026-11-26. No model is called.

Run: python3 tests/test_engines.py
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import test_passes  # noqa: E402,F401  (pins config/.env away from the real ones)

from authorlm import engines  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.engines import base, catalogue as cat  # noqa: E402
from authorlm.engines.claude_headless import (ALLOWED, DISALLOWED,  # noqa: E402
                                              WRITE_VERBS, ClaudeHeadlessEngine)
from authorlm.engines.openai_api import OpenAIEngine  # noqa: E402

FAILS: list[str] = []
PASSES = 0


def check(label: str, cond: bool, ctx: object = "") -> None:
    global PASSES
    if cond:
        PASSES += 1
    else:
        FAILS.append(f"{label}: {ctx}")
        print(f"FAIL {label}: {ctx}")


def run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            cli_main(argv)
        except SystemExit as exc:
            code = 1 if isinstance(exc.code, str) else (exc.code or 0)
            if isinstance(exc.code, str):
                out.write(exc.code)
    return code, out.getvalue()


def fixture(tmp: Path) -> tuple[str, Path]:
    ws, ms = tmp / "ws", tmp / "ws" / "Book"
    (ms / "drafts").mkdir(parents=True)
    (ms / "_retired").mkdir()
    (ms / "one.md").write_text(
        "# Small Things\n\nA cup of tea is a small thing.\n\n"
        "## The Second Cup\n\nThe second cup is never as good as the first.\n",
        encoding="utf-8")
    (ms / "two.md").write_text("# Two\n\nAnother short essay.\n", encoding="utf-8")
    (ms / "loose.md").write_text("# Loose\n\nNot in the toc.\n", encoding="utf-8")
    (ms / "drafts" / "old.md").write_text("secret draft\n", encoding="utf-8")
    (ms / "_retired" / "gone.md").write_text("retired\n", encoding="utf-8")
    (ms / "toc.toml").write_text('[[chapter]]\nfile = "one.md"\n[[chapter]]\nfile = "two.md"\n',
                                 encoding="utf-8")
    code, out = run(["--workspace", str(ws), "init", "--name", "Book", "--path", str(ms),
                     "--extraction", "off"])
    assert code == 0, out
    return str(ws), ms


# ------------------------------------------------------------ structured reply

def check_parse_reply() -> None:
    good = {"text": "t", "reason": "r", "proposed": None,
            "suggested_blurbs": [" too wordy ", "", "too wordy", 3, "lost the point"]}
    got = base.parse_reply(json.dumps(good))
    check("reply: JSON text parsed", got["text"] == "t" and got["proposed"] is None, got)
    check("reply: blurbs stripped, de-duplicated, non-strings dropped",
          got["suggested_blurbs"] == ["too wordy", "lost the point"], got)
    for label, raw in [("missing key", {"text": "t", "reason": "r", "proposed": None}),
                       ("extra key", {**good, "verdict": "x"}),
                       ("proposed wrong type", {**good, "proposed": 3}),
                       ("reason wrong type", {**good, "reason": None}),
                       ("not JSON", "here is my reply"),
                       ("not an object", "[1, 2]")]:
        try:
            base.parse_reply(raw)
            check(f"reply: {label} raises", False, raw)
        except base.EngineError:
            check(f"reply: {label} raises", True)
    schema = base.REPLY_SCHEMA
    check("schema: strict, the four keys required",
          schema["additionalProperties"] is False
          and schema["required"] == ["text", "reason", "proposed", "suggested_blurbs"], schema)


def check_session_ref() -> None:
    T = base.Turn.of
    ctx = {"standing": ["a", "b"], "turns": [{"role": "user", "text": "x"}]}
    ref = base.SessionRef("openai-api", "resp_1:with:colons", base.standing_sha(["a", "b"]),
                          base.Turn.of(ctx).essay_sha(), 1)
    back = base.SessionRef.parse(str(ref))
    check("ref: round trip, handle may hold colons", back == ref, (str(ref), back))
    check("ref: resumable on same engine, standing, turns", ref.resumable("openai-api", T(ctx)))
    check("ref: another engine's ref is not", not ref.resumable("claude-headless", T(ctx)))
    check("ref: changed standing is not",
          not ref.resumable("openai-api", T({**ctx, "standing": ["a", "c"]})))
    check("ref: fewer turns than it saw is not",
          not ref.resumable("openai-api", T({**ctx, "turns": []})))
    more = T({**ctx, "turns": ctx["turns"] + [{"action": "accept", "author_text": "",
                                               "unit_label": "P2"}]})
    check("ref: turns recorded since are resumable and caught up",
          ref.resumable("openai-api", more)
          and "[accept P2]" in ref.catch_up(more, "next") and
          ref.catch_up(more, "next").endswith("next"), ref.catch_up(more, "next"))
    check("ref: nothing unseen sends the message as is",
          ref.catch_up(T(ctx), "next") == "next")
    check("ref: junk parses to None", base.SessionRef.parse("nonsense") is None)


def check_workflow_context(ws: str) -> None:
    """The workflow's own instance_context shape (workflow/context.py)."""
    ctx = {"instance": {"id": "wi-1", "file": "one.md",
                        "engine_state": {"session_ref": "claude-headless:s:x:y:0"}},
           "standing": [{"name": "charter", "text": "CHARTER"},
                        {"name": "filters", "text": ""},
                        {"name": "style-rules", "text": "RULES"}],
           "essay": "ESSAY TEXT",
           "turns": [{"seq": 1, "action": "start", "author_text": "write the letter",
                      "blurbs": [], "reply": "Here is the letter.", "reason": "",
                      "proposed": "", "unit_label": ""},
                     {"seq": 2, "action": "redo", "author_text": "shorter",
                      "blurbs": ["too wordy"], "reply": "", "reason": "",
                      "proposed": "", "unit_label": "P1"}]}
    t = base.Turn.of(ctx)
    check("workflow ctx: dict blocks rendered, empty ones skipped",
          t.standing == ["## charter\n\nCHARTER", "## style-rules\n\nRULES"], t.standing)
    check("workflow ctx: file from the instance, essay text beside it",
          t.essay_file == "one.md" and t.essay_text == "ESSAY TEXT"
          and "ESSAY TEXT" in t.essay_block())
    check("workflow ctx: session ref from engine_state",
          t.session_ref == "claude-headless:s:x:y:0")
    msgs = base.turn_messages(t.turns)
    check("workflow ctx: turn rows read (action, blurbs, reply)", msgs == [
        ("user", "[start] write the letter"),
        ("assistant", json.dumps({"text": "Here is the letter.", "reason": "",
                                  "proposed": None})),
        ("user", "[redo P1] shorter (ticked: too wordy)")], msgs)
    try:
        base.manuscript_of(t)
        check("workflow ctx: an unknown instance id raises", False)
    except Exception as err:  # noqa: BLE001
        check("workflow ctx: an unknown instance id raises",
              isinstance(err, base.EngineError), err)
    from authorlm.workflow import contract

    made = contract.make_engine("openai-api", "gpt-x")
    check("workflow registry: the engines are registered",
          isinstance(made, OpenAIEngine) and made.model == "gpt-x", made)
    try:
        contract.make_engine("codex-headless", "m")
        check("workflow registry: a planned engine says not built", False)
    except engines.NotBuilt:
        check("workflow registry: a planned engine says not built", True)


# ------------------------------------------------------------ the catalogue

def check_catalogue(ws: str) -> None:
    reader = cat.BookReader(ws, "Book", refresh=False)
    text, entry = cat.run(reader, "essay_text", {"file": "one.md"})
    check("catalogue: an essay in the reading order is served",
          "The second cup" in text and entry["declined"] is None
          and entry["chars"] == len(text), entry)
    for name in ["drafts/old.md", "_retired/gone.md", "loose.md", "../Book/one.md",
                 "/etc/passwd", "toc.toml", ""]:
        text, entry = cat.run(reader, "essay_text", {"file": name})
        check(f"catalogue: outside the book declined ({name!r})",
              text.startswith(cat.DECLINED_PREFIX) and entry["declined"]
              and "secret" not in text and "retired\n" not in text, text)
    text, entry = cat.run(reader, "essay_section", {"file": "one.md", "section": 2})
    check("catalogue: a section by number", "## The Second Cup" in text
          and "small thing" not in text, text)
    _text, entry = cat.run(reader, "essay_section", {"file": "one.md", "section": 9})
    check("catalogue: a section that does not exist is declined", entry["declined"], entry)
    _text, entry = cat.run(reader, "read_file", {"path": "/etc/passwd"})
    check("catalogue: an unknown request is declined", entry["declined"]
          and "essay_text" in entry["declined"], entry)
    _text, entry = cat.run(reader, "essay_text", {})
    check("catalogue: a missing parameter is declined", entry["declined"], entry)
    _text, entry = cat.run(reader, "essay_text", {"file": "one.md", "path": "x"})
    check("catalogue: an unknown parameter is declined", entry["declined"], entry)
    _text, entry = cat.run(reader, "profile", {"key": "../secrets"})
    check("catalogue: a profile key with a path is declined", entry["declined"], entry)
    text, _entry = cat.run(reader, "essay_list", {})
    check("catalogue: the list is the reading order only",
          "one.md" in text and "two.md" in text and "loose.md" not in text
          and "old.md" not in text, text)
    text, entry = cat.run(reader, "essay_text", {"file": "one.md", "section": None})
    check("catalogue: null optional arguments are dropped (strict functions send them)",
          entry["declined"] is None or "section" in entry["declined"], entry)
    text, entry = cat.run(reader, "style_rules", {"file": "one.md"})
    check("catalogue: style rules for an essay", entry["declined"] is None
          and "STYLE RULES" in text, text[:200])
    text, entry = cat.run(reader, "essay_summaries", {"file": "two.md"})
    check("catalogue: summaries served (none on record) without a model",
          entry["declined"] is None and "ESSAY: (none)" in text, text)
    text, entry = cat.run(reader, "earlier_instances", {"file": "one.md"})
    check("catalogue: earlier instances degrade while the workflow lacks them",
          entry["declined"] is None and text, text)
    text, entry = cat.run(reader, "concept_notes", {"name": "no-such-thing"})
    check("catalogue: an unknown concept is declined", entry["declined"], entry)
    names = [r.name for r in cat.CATALOGUE]
    check("catalogue: the entries", names == [
        "essay_list", "essay_text", "essay_section", "essay_summaries", "style_rules",
        "concept_notes", "profile", "earlier_instances"], names)
    for r in cat.CATALOGUE:
        tool = r.function_tool()
        params = tool["parameters"]
        check(f"catalogue: {r.name} is a strict function, every property required",
              tool["strict"] and params["additionalProperties"] is False
              and sorted(params["required"]) == sorted(r.params), tool)
        check(f"catalogue: {r.name}'s verb is a read", r.verb.startswith(
            f"authorlm engine read {r.name}"), r.verb)
    listing = cat.listing()
    check("catalogue: listing names every entry", all(n in listing for n in names), listing)


def check_cli(ws: str) -> None:
    code, out = run(["engine", "read", "essay_text", "--arg", "file=drafts/old.md",
                     "-w", ws])
    check("cli: read outside the book exits 1 with DECLINED", code == 1
          and out.startswith(cat.DECLINED_PREFIX), (code, out))
    code, out = run(["engine", "read", "essay_section", "--arg", "file=one.md",
                     "--arg", "section=2", "-w", ws])
    check("cli: read a section", code == 0 and "Second Cup" in out, (code, out))
    code, out = run(["engine", "catalogue", "-w", ws])
    check("cli: catalogue lists the reads", code == 0 and "essay_summaries" in out, out)
    for name in ("claude-api", "codex-headless"):
        code, out = run(["engine", "try", name, "--essay", "one.md", "--message", "hi",
                         "-w", ws])
        check(f"cli: {name} is not built yet", code == 1 and "not built yet" in out, out)
    code, out = run(["engine", "try", "openai-api", "--essay", "drafts/old.md",
                     "--message", "hi", "-w", ws])
    check("cli: try outside the book refuses before any engine", code == 1
          and "reading order" in out, out)


def check_get_engine() -> None:
    for name in ("claude-api", "codex-headless"):
        try:
            engines.get_engine(name, "m")
            check(f"get_engine: {name} raises NotBuilt", False)
        except engines.NotBuilt:
            check(f"get_engine: {name} raises NotBuilt", True)
    try:
        engines.get_engine("gemini-api", "m")
        check("get_engine: unknown name raises", False)
    except LookupError:
        check("get_engine: unknown name raises", True)
    try:
        engines.get_engine("openai-api", None)
        check("get_engine: no model and no [engines] config", False)
    except engines.NotConfigured:
        check("get_engine: no model and no [engines] config", True)
    check("get_engine: [engines] supplies the default",
          engines.default_model("openai-api", {"engines": {"openai_model": "m1"}}) == "m1")
    e = engines.get_engine("claude-headless", "opus")
    check("get_engine: claude-headless", isinstance(e, ClaudeHeadlessEngine)
          and e.session_ref is None and e.model == "opus")
    e = engines.get_engine("openai-api", "openai/gpt-x")
    check("get_engine: openai-api strips the litellm prefix",
          isinstance(e, OpenAIEngine) and e.model == "gpt-x", e.model)


# ------------------------------------------------------------ claude-headless

def _stream(session: str, reply: dict, *, cost=0.01, error=False) -> str:
    events = [
        {"type": "system", "subtype": "init", "session_id": session},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "mcp__authorlm-read__essay_text",
             "input": {"file": "drafts/old.md"}},
            {"type": "tool_use", "id": "t2", "name": "WebSearch",
             "input": {"query": "Lotus Sutra burning house"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": [{"type": "text", "text": "DECLINED: not in the book"}]},
            {"type": "tool_result", "tool_use_id": "t2", "content": "results ..."}]}},
        {"type": "result", "subtype": "success", "is_error": error, "session_id": session,
         "structured_output": None if error else reply, "result": "",
         "total_cost_usd": cost,
         "usage": {"input_tokens": 10, "cache_creation_input_tokens": 100,
                   "cache_read_input_tokens": 5000, "output_tokens": 50}},
    ]
    return "\n".join(json.dumps(e) for e in events) + "\n"


def check_claude_headless(ws: str) -> None:
    reply = {"text": "t", "reason": "cut a clause", "proposed": "New text.",
             "suggested_blurbs": ["too wordy"]}
    calls: list[dict] = []
    script: list = []

    def runner(argv, stdin, cwd, timeout):
        calls.append({"argv": argv, "stdin": stdin, "cwd": cwd})
        return script.pop(0)

    eng = ClaudeHeadlessEngine("opus", runner=runner, binary="/bin/claude-fake")
    ctx = {"standing": ["CHARTER", "ESSAY one.md text"], "turns": [], "session_ref": None,
           "manuscript": "Book", "essay": "one.md", "workspace": ws}
    script.append((0, _stream("will-be-ignored", reply), ""))
    got = eng.send(ctx, "Work paragraph 1.")
    argv = calls[0]["argv"]

    def flag(name):
        i = argv.index(name)
        return argv[i + 1]

    def flag_list(name):
        i = argv.index(name) + 1
        out = []
        while i < len(argv) and not argv[i].startswith("--"):
            out.append(argv[i])
            i += 1
        return out

    check("headless: print mode, stream-json", argv[1] == "-p"
          and flag("--output-format") == "stream-json" and "--verbose" in argv, argv[:6])
    check("headless: the model", flag("--model") == "opus")
    check("headless: first turn names a new session id", "--session-id" in argv
          and "--resume" not in argv)
    check("headless: reply schema passed", json.loads(flag("--json-schema")) == base.REPLY_SCHEMA)
    check("headless: restricted mode, no safe-mode (it would drop the MCP server)",
          "--restricted" in argv and "--safe-mode" not in argv)
    check("headless: only the web tools are built in", flag("--tools") == "WebSearch,WebFetch")
    check("headless: dontAsk, prompts to nobody",
          flag("--permission-mode") == "dontAsk" and flag("--permission-prompts") == "none")
    check("headless: --strict-mcp-config", "--strict-mcp-config" in argv)
    mcp = json.loads(flag("--mcp-config"))["mcpServers"]
    server = mcp.get("authorlm-read") or {}
    check("headless: one MCP server, the read catalogue, for this manuscript",
          list(mcp) == ["authorlm-read"]
          and server["args"][:2] == ["-m", "authorlm.engines.mcp_read"]
          and server["args"][-2:] == ["-m", "Book"]
          and str(Path(ws).resolve()) in server["args"], mcp)
    allowed = flag_list("--allowedTools")
    check("headless: allowed is the catalogue and the web, nothing else",
          allowed == list(ALLOWED) == ["mcp__authorlm-read", "WebSearch", "WebFetch"], allowed)
    check("headless: no Bash, edit or file tool allowed",
          not any(a.split("(")[0] in ("Bash", "Edit", "Write", "Read", "Glob", "Grep",
                                      "NotebookEdit", "MultiEdit") for a in allowed), allowed)
    disallowed = flag_list("--disallowedTools")
    check("headless: disallowed carries the shell and file tools",
          all(t in disallowed for t in ("Bash", "Edit", "Write", "Read", "Glob", "Grep",
                                        "NotebookEdit")), disallowed)
    for verb in ("doc push", "style add", "style retire", "style edit", "revise",
                 "filter apply", "summarize rebuild", "profile set"):
        check(f"headless: write verb '{verb}' disallowed and never allowed",
              verb in WRITE_VERBS and f"Bash(authorlm {verb}:*)" in disallowed
              and not any(verb in a for a in allowed))
    check("headless: the protocol (with the catalogue) is in the system prompt",
          "essay_summaries" in flag("--append-system-prompt"))
    check("headless: the session lives in the workspace's engine folder",
          str(calls[0]["cwd"]).endswith(".authorlm/engines/claude-headless"), calls[0]["cwd"])
    stdin = calls[0]["stdin"]
    check("headless: first message carries standing in order, then the turn",
          stdin.index("CHARTER") < stdin.index("ESSAY one.md") < stdin.index("Work paragraph 1."),
          stdin)
    check("headless: structured reply returned", got["reason"] == "cut a clause"
          and got["proposed"] == "New text." and got["suggested_blurbs"] == ["too wordy"], got)
    check("headless: usage from the result event", got["usage"] == {
        "input": 110, "cached": 5000, "output": 50, "dollars": 0.0,
        "est_subscription": 0.01}, got["usage"])
    check("headless: requests logged from tool calls, declines included",
          got["requests"] == [
              {"name": "essay_text", "args": {"file": "drafts/old.md"},
               "chars": len("DECLINED: not in the book"), "declined": "not in the book"},
              {"name": "web_search", "args": {"query": "Lotus Sutra burning house"},
               "chars": len("results ..."), "declined": None}], got["requests"])
    ref = base.SessionRef.parse(eng.session_ref)
    check("headless: session ref names the session the result reported",
          ref and ref.handle == "will-be-ignored" and ref.turns == 1, eng.session_ref)

    # Second turn: the ref fits, so --resume with only the message.
    ctx2 = {**ctx, "turns": [{"message": "Work paragraph 1.", "reply": got}],
            "session_ref": eng.session_ref}
    script.append((0, _stream("will-be-ignored", reply), ""))
    eng.send(ctx2, "Next paragraph.")
    argv = calls[1]["argv"]
    check("headless: later turn resumes the session",
          argv[argv.index("--resume") + 1] == "will-be-ignored" and "--session-id" not in argv)
    check("headless: a resumed turn sends only the message", calls[1]["stdin"] == "Next paragraph.")

    # Third turn: the resume fails (session lost); rebuilt from the record.
    ctx3 = {**ctx2, "turns": ctx2["turns"] + [{"message": "Next paragraph.", "reply": got}],
            "session_ref": eng.session_ref}
    script.append((1, _stream("x", reply, error=True), "No conversation found"))
    script.append((0, _stream("fresh-session", reply), ""))
    eng.send(ctx3, "Third paragraph.")
    argv = calls[3]["argv"]
    check("headless: a lost session is rebuilt under a new id",
          "--session-id" in argv and "--resume" not in argv)
    stdin = calls[3]["stdin"]
    check("headless: the rebuild carries the standing, earlier turns and the message",
          "CHARTER" in stdin and "Work paragraph 1." in stdin and "cut a clause" in stdin
          and stdin.rstrip().endswith("Third paragraph."), stdin)
    check("headless: ref now names the new session",
          base.SessionRef.parse(eng.session_ref).handle == "fresh-session", eng.session_ref)

    # Standing changed (e.g. filters added): no resume, a rebuild.
    ctx4 = {**ctx3, "standing": ctx3["standing"] + ["FILTERS"], "session_ref": eng.session_ref,
            "turns": ctx3["turns"] + [{"message": "Third paragraph.", "reply": got}]}
    script.append((0, _stream("s4", reply), ""))
    eng.send(ctx4, "Apply concision.")
    check("headless: changed standing starts a rebuilt session",
          "--session-id" in calls[4]["argv"] and "FILTERS" in calls[4]["stdin"])

    # The workflow's own context: manuscript from the instance row, the
    # essay text beside the standing blocks, the ref from engine_state.
    from authorlm import api
    from authorlm.workflow import context as wf_context, core as wf_core

    db = api.open_db(ws)
    ms = dict(db.one("SELECT * FROM manuscripts WHERE name = 'Book'"))
    inst = wf_core.start(db, ms, "one", "refine", engine="claude-headless", model="opus")
    wctx = wf_context.instance_context(db, ms, inst)
    wctx["workspace"] = ws
    script.append((0, _stream("wf-session", reply), ""))
    n = len(calls)
    eng.send(wctx, "Write the letter.")
    argv, stdin = calls[n]["argv"], calls[n]["stdin"]
    mcp = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]["authorlm-read"]
    check("headless/workflow: manuscript found from the instance row",
          mcp["args"][-2:] == ["-m", "Book"], mcp["args"])
    check("headless/workflow: the essay text is sent after the standing blocks",
          "THE ESSAY BEING EDITED, one.md" in stdin and "The second cup" in stdin
          and stdin.rstrip().endswith("Write the letter."), stdin[-400:])
    # Next turn: the essay changed and an accept was recorded without a model.
    wctx2 = {**wctx, "essay": wctx["essay"] + "\nA new last line.\n",
             "turns": wctx["turns"] + [
                 {"action": "start", "author_text": "Write the letter.", "blurbs": [],
                  "reply": "t", "reason": "", "proposed": "", "unit_label": ""},
                 {"action": "accept", "author_text": "", "blurbs": [], "reply": "",
                  "reason": "", "proposed": "", "unit_label": "P1"}]}
    wctx2["instance"] = {**wctx["instance"],
                         "engine_state": {"session_ref": eng.session_ref}}
    script.append((0, _stream("wf-session", reply), ""))
    eng.send(wctx2, "Next unit.")
    argv, stdin = calls[n + 1]["argv"], calls[n + 1]["stdin"]
    check("headless/workflow: resumes from engine_state's ref",
          argv[argv.index("--resume") + 1] == "wf-session")
    check("headless/workflow: catches up the unseen accept and the changed essay",
          "[accept P1]" in stdin and "A new last line." in stdin
          and "Write the letter." not in stdin.split("TURNS RECORDED SINCE")[0]
          and stdin.endswith("Next unit."), stdin)

    script.append((1, "", "boom"))
    try:
        eng.send({**ctx, "session_ref": None}, "x")
        check("headless: a failed turn raises EngineError", False)
    except base.EngineError as err:
        check("headless: a failed turn raises EngineError", "boom" in str(err), err)


# ------------------------------------------------------------ openai-api

def _response(rid, output, *, text=None, tin=1000, cached=0, out=50):
    return SimpleNamespace(
        id=rid, status="completed", output=output, output_text=text,
        incomplete_details=None,
        usage=SimpleNamespace(input_tokens=tin, output_tokens=out,
                              input_tokens_details=SimpleNamespace(cached_tokens=cached)))


class FakeResponses:
    def __init__(self, script):
        self.script = script
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def check_openai(ws: str) -> None:
    reply = {"text": "Done.", "reason": "split the sentence",
             "proposed": "The second cup is never as good. That is its point.",
             "suggested_blurbs": ["too terse"]}
    script = [
        _response("resp_1", [
            SimpleNamespace(type="web_search_call",
                            action=SimpleNamespace(query="second cup proverb")),
            SimpleNamespace(type="function_call", name="essay_text", call_id="c1",
                            arguments=json.dumps({"file": "drafts/old.md"})),
            SimpleNamespace(type="function_call", name="essay_section", call_id="c2",
                            arguments=json.dumps({"file": "one.md", "section": 2}))],
            tin=6000, cached=0, out=40),
        _response("resp_2", [SimpleNamespace(type="message", content=[])],
                  text=json.dumps(reply), tin=6200, cached=5800, out=160),
    ]
    fake = FakeResponses(script)
    priced: list = []

    def pricer(model, fresh, cached, out):
        priced.append((model, fresh, cached, out))
        return 0.0123

    eng = OpenAIEngine("gpt-test", client=SimpleNamespace(responses=fake),
                       reader_factory=lambda turn: cat.BookReader(ws, turn.manuscript,
                                                                 refresh=False),
                       pricer=pricer)
    ctx = {"standing": ["CHARTER", "ESSAY one.md text"], "turns": [], "session_ref": None,
           "manuscript": "Book", "essay": "one.md", "workspace": ws}
    got = eng.send(ctx, "Tighten section 2.")
    first, second = fake.calls
    roles = [(i.get("role"), i.get("content")[:20]) for i in first["input"]]
    check("openai: rebuilt input is protocol, standing in order, then the message",
          [r for r, _ in roles] == ["developer", "developer", "developer", "user"]
          and first["input"][1]["content"] == "CHARTER"
          and first["input"][2]["content"] == "ESSAY one.md text"
          and first["input"][3]["content"] == "Tighten section 2."
          and "HOW THIS SESSION WORKS" in first["input"][0]["content"], roles)
    check("openai: no previous_response_id on a fresh session",
          "previous_response_id" not in first)
    tools = first["tools"]
    check("openai: the provider's web search tool offered", tools[0] == {"type": "web_search"})
    check("openai: function tools generated from the catalogue",
          [t["name"] for t in tools[1:]] == [r.name for r in cat.CATALOGUE]
          and all(t["strict"] for t in tools[1:]))
    fmt = first["text"]["format"]
    check("openai: strict json_schema reply", fmt["type"] == "json_schema" and fmt["strict"]
          and fmt["schema"] == base.REPLY_SCHEMA, fmt)
    check("openai: a cache key per essay", first["prompt_cache_key"] == "authorlm:Book:one.md")
    check("openai: the function outputs go back chained to the response",
          second["previous_response_id"] == "resp_1"
          and [i["call_id"] for i in second["input"]] == ["c1", "c2"]
          and second["input"][0]["output"].startswith(cat.DECLINED_PREFIX)
          and "Second Cup" in second["input"][1]["output"], second["input"])
    check("openai: structured reply", got["reason"] == "split the sentence"
          and got["suggested_blurbs"] == ["too terse"] and got["engine"] == "openai-api"
          and got["model"] == "gpt-test", got)
    names = [(r["name"], bool(r["declined"])) for r in got["requests"]]
    check("openai: every request logged, the decline visible",
          names == [("web_search", False), ("essay_text", True), ("essay_section", False)]
          and got["requests"][2]["chars"] > 0, got["requests"])
    check("openai: usage summed over rounds, cache reads apart",
          got["usage"]["input"] == 6000 + 400 and got["usage"]["cached"] == 5800
          and got["usage"]["output"] == 200 and got["usage"]["dollars"] == 0.0123
          and got["usage"]["est_subscription"] == 0.0, got["usage"])
    check("openai: priced on net input, cached and output", priced == [
        ("gpt-test", 6400, 5800, 200)], priced)
    ref = base.SessionRef.parse(eng.session_ref)
    check("openai: ref names the last response", ref and ref.handle == "resp_2"
          and ref.turns == 1, eng.session_ref)

    # Next turn chains on the ref with only the new message.
    fake.script.append(_response("resp_3", [], text=json.dumps(reply)))
    ctx2 = {**ctx, "turns": [{"message": "Tighten section 2.", "reply": got}],
            "session_ref": eng.session_ref}
    eng.send(ctx2, "[redo] too terse")
    call = fake.calls[2]
    check("openai: a fitting ref sends only the new message, chained",
          call["previous_response_id"] == "resp_2"
          and call["input"] == [{"role": "user", "content": "[redo] too terse"}], call)

    # A lost chain is rebuilt from the record.
    fake.script.extend([RuntimeError("Previous response with id 'resp_3' not found."),
                        _response("resp_4", [], text=json.dumps(reply))])
    ctx3 = {**ctx2, "turns": ctx2["turns"] + [{"message": "[redo] too terse", "reply": got}],
            "session_ref": eng.session_ref}
    eng.send(ctx3, "Next.")
    call = fake.calls[4]
    roles = [i["role"] for i in call["input"]]
    check("openai: a lost chain is rebuilt with the earlier turns",
          "previous_response_id" not in call
          and roles == ["developer"] * 3 + ["user", "assistant", "user", "assistant", "user"],
          roles)

    # A reply that breaks the schema is an error, not a guess.
    fake.script.append(_response("resp_5", [], text="Sure! Here is my edit."))
    try:
        eng.send({**ctx, "session_ref": None}, "x")
        check("openai: prose instead of the schema raises", False)
    except base.EngineError:
        check("openai: prose instead of the schema raises", True)


def check_find_binary(tmp: Path) -> None:
    """Where claude-headless looks for `claude`, and doctor's check of it
    (the served app's PATH lacked ~/.local/bin, 2026-11-26)."""
    from authorlm.engines import claude_headless as ch

    fake = tmp / "bin" / "claude"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    missing = str(tmp / "nowhere" / "claude")
    none = lambda _name: None  # noqa: E731
    on_path = lambda _name: "/usr/bin/claude-on-path"  # noqa: E731

    check("find_binary: PATH first",
          ch.find_binary({}, on_path, str(fake)) == ("/usr/bin/claude-on-path", ["PATH"]))
    check("find_binary: then the installer's default",
          ch.find_binary({}, none, str(fake)) == (str(fake), ["PATH", str(fake)]))
    found, looked = ch.find_binary({}, none, missing)
    check("find_binary: neither -> None, naming both places",
          found is None and looked == ["PATH", missing], (found, looked))
    cfg = {"engines": {"claude_binary": str(fake)}}
    check("find_binary: [engines] claude_binary wins over PATH",
          ch.find_binary(cfg, on_path, missing)[0] == str(fake))
    found, looked = ch.find_binary({"engines": {"claude_binary": missing}}, on_path, str(fake))
    check("find_binary: a wrong claude_binary is reported, not passed over",
          found is None and "claude_binary" in looked[0], (found, looked))

    eng = ClaudeHeadlessEngine("opus")
    orig = ch.find_binary
    ch.find_binary = lambda: orig({}, none, missing)  # type: ignore[assignment]
    try:
        eng.binary
        check("binary: none found raises NotConfigured", False)
    except engines.NotConfigured as exc:
        check("binary: NotConfigured names where it looked",
              "PATH" in str(exc) and missing in str(exc)
              and "claude_binary" in str(exc), str(exc))
    finally:
        ch.find_binary = orig

    ok = ch.doctor_check({}, none, str(fake))
    check("doctor_check: found", ok["ok"] and ok["detail"] == str(fake)
          and ok["name"] == ch.DOCTOR_NAME, ok)
    bad = ch.doctor_check({}, none, missing)
    check("doctor_check: missing, with the fix",
          not bad["ok"] and bad["state"] == "missing" and missing in bad["detail"]
          and "install-launch-agent.sh" in bad["fix"]
          and "claude_binary" in bad["fix"], bad)


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    try:
        ws, _ms = fixture(tmp)
        check_parse_reply()
        check_session_ref()
        check_workflow_context(ws)
        check_catalogue(ws)
        check_cli(ws)
        check_get_engine()
        check_claude_headless(ws)
        check_find_binary(tmp)
        check_openai(ws)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILS:
        print(f"{len(FAILS)} FAILED, {PASSES} passed")
        return 1
    print(f"ALL CHECKS PASSED ({PASSES})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
