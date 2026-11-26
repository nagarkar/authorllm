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
                   SessionRef, empty_usage, parse_reply, protocol_text,
                   rebuild_transcript, standing_sha)

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

Runner = Callable[[list[str], str, Path, float], tuple[int, str, str]]


def _run(argv: list[str], stdin: str, cwd: Path, timeout: float) -> tuple[int, str, str]:
    proc = subprocess.run(argv, input=stdin, capture_output=True, text=True,
                          cwd=str(cwd), timeout=timeout)
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
        found = self._binary or shutil.which("claude")
        if not found:
            raise NotConfigured("the Claude Code CLI ('claude') is not on PATH")
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
    def first_message(context: dict, message: str) -> str:
        parts = ["STANDING CONTEXT (stable for this workflow instance):"]
        parts += list(context.get("standing") or [])
        earlier = rebuild_transcript(context.get("turns") or [])
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

        workspace = str(api.resolve_workspace(instance_context.get("workspace"))[0])
        manuscript = instance_context["manuscript"]
        cwd = self.workdir(workspace)
        ref = SessionRef.parse(instance_context.get("session_ref"))
        if ref and ref.fits(self.name, instance_context):
            argv = self.command(workspace=workspace, manuscript=manuscript,
                                session_id=ref.handle, resume=True)
            code, out, err = self._runner(argv, message, cwd, self.timeout)
            result = _result_event(out)
            if code == 0 and result and not result.get("is_error"):
                return self._reply(instance_context, ref.handle, out, result)
            # A lost or broken session: rebuild from the record below.
        session_id = str(uuid.uuid4())
        argv = self.command(workspace=workspace, manuscript=manuscript,
                            session_id=session_id, resume=False)
        code, out, err = self._runner(argv, self.first_message(instance_context, message),
                                      cwd, self.timeout)
        result = _result_event(out)
        if result is None or code != 0 or result.get("is_error"):
            detail = (result or {}).get("result") or err.strip()[-500:] or f"exit {code}"
            raise EngineError(f"claude-headless: the turn failed: {detail}")
        return self._reply(instance_context, session_id, out, result)

    def _reply(self, context: dict, session_id: str, out: str, result: dict) -> dict:
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
        self.session_ref = str(SessionRef(
            self.name, result.get("session_id") or session_id,
            standing_sha(context.get("standing") or []),
            len(context.get("turns") or []) + 1))
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
