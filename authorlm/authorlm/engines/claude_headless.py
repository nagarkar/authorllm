"""`claude-headless`: a workflow turn on Claude Code without a window.

Decided 2026-11-25 (docs/plans/filter-lens-consolidation.md 11.9 Q6): Claude
Code's print mode (`claude -p`) on the owner's subscription, its session id
kept on the instance and resumed across days. Q20a/Q20b/Q20c: it may search
and read the web, may read the book through AuthorLM's read commands, never
reads files on its own, and has no command that changes the manuscript, the
Doc or the style laws ("Read commands only. Changes through forms").

**The flags** (Claude Code 2.1.329, checked with `claude --help` and live):

- `-p --output-format stream-json --verbose`: print mode, every event on
  stdout, so the read requests it made can be logged from its tool calls.
- `--json-schema <REPLY_SCHEMA>`: the reply comes back as
  `structured_output` on the result event, not parsed from prose.
- `--session-id <uuid>` on the first turn, `--resume <uuid>` after.
- `--restricted`: drops the code-running tools and ignores the user's,
  project's and local settings files, so no allow rule, hook or plugin
  there widens what the session may do (seen live: no skills, only Claude
  Code's built-in plugins). Not `--safe-mode`: it also turns off the
  `--mcp-config` server, leaving the session with no way to read the book
  (seen live 2026-11-26). The owner's global `~/.claude/CLAUDE.md` is
  still read; the protocol and standing context follow it.
- `--disable-slash-commands`: no skills or commands.
- `--tools WebSearch,WebFetch`: the only built-in tools offered.
- `--mcp-config` + `--strict-mcp-config`: one MCP server,
  engines/mcp_read.py, serving the read catalogue and nothing else.
- `--allowedTools mcp__authorlm-read WebSearch WebFetch` with
  `--permission-mode dontAsk` and `--permission-prompts none`: anything
  else is denied, never asked.
- `--disallowedTools`: the file and shell tools and AuthorLM's write verbs,
  named, as a second wall (DISALLOWED).

Why not Bash restricted to `authorlm` read verbs, as first planned: tried
live 2026-11-26 with `--allowedTools "Bash(authorlm engine read:*)"` under
`dontAsk`, and `echo hello` still ran: Claude Code approves commands it
judges read-only (echo, cat, ls, grep ...) without an allow rule, which
would let the session read any file on disk — what Q20a rules out. The
catalogue as MCP tools gives the same reads with no shell at all.

The session's working directory is fixed per workspace
(`<workspace>/.authorlm/engines/claude-headless`), because Claude Code
files sessions by directory and `--resume` looks there. A resume that
fails (session lost, expired, standing blocks changed, turns it never saw)
starts a new session rebuilt from the instance context: protocol in the
system prompt, then the standing blocks, the earlier turns and the message
in one first message.

Usage: Claude Code reports tokens and `total_cost_usd`, the API list-price
equivalent of the turn; that is `est_subscription`. `dollars` is 0: the
subscription is not billed per call.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Callable

from . import catalogue as cat
from .base import (REPLY_SCHEMA, Engine, EngineError, NotConfigured,
                   SessionRef, Turn, empty_usage, manuscript_of, parse_reply,
                   protocol_text, rebuild_transcript)

MCP_NAME = "authorlm-read"
MCP_PREFIX = f"mcp__{MCP_NAME}__"
ALLOWED = (f"mcp__{MCP_NAME}", "WebSearch", "WebFetch")
BUILTIN_TOOLS = "WebSearch,WebFetch"

# AuthorLM verbs that change the manuscript, the Doc, the style laws or the
# record. Bash is not offered at all; naming them is the second wall, and
# the test asserts none of them is ever allowed.
WRITE_VERBS = (
    "doc push", "doc pull", "doc checkout", "doc checkin", "style add",
    "style retire", "style unretire", "style edit", "style move", "style copy",
    "style attach", "revise", "filter apply", "filter run", "write",
    "collect", "summarize put", "summarize rebuild", "profile set",
    "profile push", "profile pull", "concept add", "concept retire",
    "concept edit", "concept merge", "history restore", "devedit",
    "proposal accept", "book", "export",
)
DISALLOWED = ("Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "Read",
              "Glob", "Grep", "Task", "Agent") + tuple(
    f"Bash(authorlm {v}:*)" for v in WRITE_VERBS)

Runner = Callable[[list[str], str, Path, float, dict], tuple[int, str, str]]

# Variables that make Claude Code bill or route elsewhere than its own
# sign-in. authorlm loads authorlm/.env into os.environ, and that file sets
# ANTHROPIC_API_KEY (and ANTHROPIC_BASE_URL) for LiteLLM's API calls; passed
# on, `claude -p` uses that key instead of the subscription login and the
# turn fails ("Invalid API key", seen live 2026-11-26). So the child gets
# no ANTHROPIC_* variable at all, nor the switches to Bedrock, Vertex or
# Foundry: headless Claude runs on the CLI's own sign-in, as designed.
AUTH_PREFIX = "ANTHROPIC_"
AUTH_SWITCHES = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                 "CLAUDE_CODE_USE_FOUNDRY")


def child_env(environ: dict | None = None) -> dict:
    """The environment for the `claude` child: ours without AUTH_PREFIX
    variables and AUTH_SWITCHES."""
    source = os.environ if environ is None else environ
    return {k: v for k, v in source.items()
            if not k.startswith(AUTH_PREFIX) and k not in AUTH_SWITCHES}


def _run(argv: list[str], stdin: str, cwd: Path, timeout: float,
         env: dict) -> tuple[int, str, str]:
    proc = subprocess.run(argv, input=stdin, capture_output=True, text=True,
                          cwd=str(cwd), timeout=timeout, env=env)
    return proc.returncode, proc.stdout, proc.stderr


def mcp_config(workspace: str, manuscript: str) -> dict:
    package_root = str(Path(__file__).resolve().parents[2])
    env = {"PYTHONPATH": package_root}
    if os.environ.get("AUTHORLM_CONFIG"):
        env["AUTHORLM_CONFIG"] = os.environ["AUTHORLM_CONFIG"]
    return {"mcpServers": {MCP_NAME: {
        "command": sys.executable,
        "args": ["-m", "authorlm.engines.mcp_read", "-w", workspace, "-m", manuscript],
        "env": env}}}


# Claude Code's installer puts `claude` here. A launch agent's PATH need not
# include it (the served app's did not, 2026-11-26), so it is tried after PATH.
DEFAULT_BINARY = "~/.local/bin/claude"
CONFIG_BINARY_KEY = "claude_binary"
DOCTOR_NAME = "engine: claude-headless binary"


def _executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def find_binary(config: dict | None = None,
                which: Callable[[str], str | None] = shutil.which,
                default: str = DEFAULT_BINARY) -> tuple[str | None, list[str]]:
    """The `claude` to run (None when none is found) and every place looked,
    in order: `[engines] claude_binary` in authorlm/config.toml when set,
    and then only there (a wrong setting is reported, never passed over);
    else PATH; else DEFAULT_BINARY."""
    if config is None:
        from . import _config

        config = _config()
    setting = str((config.get("engines") or {}).get(CONFIG_BINARY_KEY) or "").strip()
    if setting:
        path = os.path.expanduser(setting)
        where = f"[engines] {CONFIG_BINARY_KEY} = {setting} in authorlm/config.toml"
        return (path if _executable(path) else None), [where]
    found = which("claude")
    if found:
        return found, ["PATH"]
    path = os.path.expanduser(default)
    return (path if _executable(path) else None), ["PATH", path]


def not_found_message(looked: list[str]) -> str:
    return ("the Claude Code CLI ('claude') was not found; looked in: "
            + "; ".join(looked) + f". Install it, or set [engines] "
            f"{CONFIG_BINARY_KEY} in authorlm/config.toml to its full path")


def doctor_check(config: dict | None = None,
                 which: Callable[[str], str | None] = shutil.which,
                 default: str = DEFAULT_BINARY) -> dict:
    """`authorlm doctor`'s check that claude-headless finds `claude`:
    {name, ok, state, detail, fix}, as web_pages.doctor_checks."""
    found, looked = find_binary(config, which, default)
    if found:
        return {"name": DOCTOR_NAME, "ok": True, "state": "found",
                "detail": found, "fix": ""}
    return {"name": DOCTOR_NAME, "ok": False, "state": "missing",
            "detail": not_found_message(looked),
            "fix": ("install Claude Code; for the served app put the folder of "
                    "`command -v claude` on its PATH (rerun "
                    "scripts/install-launch-agent.sh <manuscript>, then launchctl "
                    "kickstart -k gui/$(id -u)/com.nagarkar.authorlm.serve), or set "
                    f"[engines] {CONFIG_BINARY_KEY} in authorlm/config.toml")}


class ClaudeHeadlessEngine(Engine):
    name = "claude-headless"

    def __init__(self, model: str, *, runner: Runner | None = None,
                 binary: str | None = None, timeout: float = 900.0):
        super().__init__(model)
        self._runner = runner or _run
        self._binary = binary
        self.timeout = timeout

    @property
    def binary(self) -> str:
        if self._binary:
            return self._binary
        found, looked = find_binary()
        if not found:
            raise NotConfigured(not_found_message(looked))
        return found

    def command(self, *, workspace: str, manuscript: str, session_id: str,
                resume: bool) -> list[str]:
        """The full `claude` command line for one turn (the message goes on
        stdin)."""
        argv = [self.binary, "-p",
                "--output-format", "stream-json", "--verbose",
                "--model", self.model,
                "--resume" if resume else "--session-id", session_id,
                "--json-schema", json.dumps(REPLY_SCHEMA),
                "--append-system-prompt", protocol_text(cat.listing()),
                "--restricted",
                "--tools", BUILTIN_TOOLS,
                "--mcp-config", json.dumps(mcp_config(workspace, manuscript)),
                "--strict-mcp-config",
                "--permission-mode", "dontAsk",
                "--permission-prompts", "none",
                "--disable-slash-commands",
                "--allowedTools", *ALLOWED,
                "--disallowedTools", *DISALLOWED]
        return argv

    @staticmethod
    def first_message(turn: Turn, message: str) -> str:
        parts = ["STANDING CONTEXT (stable for this workflow instance):"]
        parts += list(turn.standing)
        essay = turn.essay_block()
        if essay:
            parts.append(essay)
        earlier = rebuild_transcript(turn.turns)
        if earlier:
            parts.append("EARLIER TURNS OF THIS INSTANCE (rebuilt from the record):\n\n"
                         + earlier)
        parts.append("THIS TURN:\n\n" + message)
        return "\n\n=====\n\n".join(parts)

    def workdir(self, workspace: str) -> Path:
        path = Path(workspace) / ".authorlm" / "engines" / "claude-headless"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def send(self, instance_context: dict, message: str) -> dict:
        from .. import api

        turn = Turn.of(instance_context)
        workspace, manuscript = manuscript_of(turn)
        workspace = str(api.resolve_workspace(workspace)[0])
        cwd = self.workdir(workspace)
        ref = SessionRef.parse(turn.session_ref)
        if ref and ref.resumable(self.name, turn):
            argv = self.command(workspace=workspace, manuscript=manuscript,
                                session_id=ref.handle, resume=True)
            code, out, err = self._runner(argv, ref.catch_up(turn, message), cwd,
                                          self.timeout, child_env())
            result = _result_event(out)
            if code == 0 and result and not result.get("is_error"):
                return self._reply(turn, ref.handle, out, result)
            # A lost or broken session: rebuild from the record below.
        session_id = str(uuid.uuid4())
        argv = self.command(workspace=workspace, manuscript=manuscript,
                            session_id=session_id, resume=False)
        code, out, err = self._runner(argv, self.first_message(turn, message),
                                      cwd, self.timeout, child_env())
        result = _result_event(out)
        if result is None or code != 0 or result.get("is_error"):
            detail = (result or {}).get("result") or err.strip()[-500:] or f"exit {code}"
            raise EngineError(f"claude-headless: the turn failed: {detail}")
        return self._reply(turn, session_id, out, result)

    def _reply(self, turn: Turn, session_id: str, out: str, result: dict) -> dict:
        raw = result.get("structured_output")
        if raw is None:
            raw = result.get("result")
        parsed = parse_reply(raw)
        usage = empty_usage()
        u = result.get("usage") or {}
        usage["input"] = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
        usage["cached"] = u.get("cache_read_input_tokens") or 0
        usage["output"] = u.get("output_tokens") or 0
        usage["dollars"] = 0.0
        usage["est_subscription"] = float(result.get("total_cost_usd") or 0.0)
        self.session_ref = str(SessionRef.after(
            self.name, result.get("session_id") or session_id, turn))
        return self._finish(parsed, usage, requests_from_stream(out))


def _events(out: str):
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _result_event(out: str) -> dict | None:
    found = None
    for event in _events(out):
        if event.get("type") == "result":
            found = event
    return found


def requests_from_stream(out: str) -> list[dict]:
    """Every read request and web call the session made, from its
    tool_use events, with the size of what it got back."""
    uses: dict[str, dict] = {}
    order: list[str] = []
    for event in _events(out):
        if event.get("type") not in ("assistant", "user"):
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = block.get("name") or ""
                if name.startswith(MCP_PREFIX):
                    entry = {"name": name[len(MCP_PREFIX):],
                             "args": block.get("input") or {}, "chars": 0,
                             "declined": None}
                elif name == "WebSearch":
                    entry = {"name": "web_search",
                             "args": {"query": (block.get("input") or {}).get("query")},
                             "chars": 0, "declined": None}
                elif name == "WebFetch":
                    entry = {"name": "web_fetch",
                             "args": {"url": (block.get("input") or {}).get("url")},
                             "chars": 0, "declined": None}
                else:
                    continue
                uses[block.get("id")] = entry
                order.append(block.get("id"))
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in uses:
                text = _content_text(block.get("content"))
                entry = uses[block["tool_use_id"]]
                entry["chars"] = len(text)
                if text.startswith(cat.DECLINED_PREFIX):
                    entry["declined"] = text[len(cat.DECLINED_PREFIX):]
                elif block.get("is_error"):
                    entry["declined"] = text[:300] or "the tool call failed"
    return [uses[i] for i in order]


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""
