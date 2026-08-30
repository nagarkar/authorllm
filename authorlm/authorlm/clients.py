"""Client provenance — *which chat* did this.

The `sources` registry (db.PROVENANCE_TABLES, db.source) already records
*whose judgment* originated a knowledge object. That is a different
question from the one this module answers: which conversation the author
was having when the row was born. With one chat open the distinction is
idle. With three Claude Code chats against one `~/.authorlm`, a writeup
that any of them may resume, and an AuthorLM session that is a shared
ambient work period by design, it is the difference between a history
that can be reconstructed and one that can only be guessed at.

Hence the module name: `provenance` is taken, and means the other thing.

**Everything here is additive metadata.** No schema change, no index, no
migration. The stamp gates nothing — no verb refuses, warns or branches
on it — and `current()` never raises: every layer is wrapped and a
failure falls through to the next one, exactly as `tracelog.record`
swallows its own failures. A broken provenance stamp must not break the
verb it describes.

Resolution is layered, once per invocation, and each layer is honest
about what its answer is worth (`Client.precision`):

1. `AUTHORLM_CLIENT` — an explicit statement by whoever launched the
   process (`exact`). `AUTHORLM_CLIENT=none` disables detection
   entirely: the test suites' pin, and the author's off-switch.
2. `ADAPTERS`, in order — the first `detect()` that returns non-`None`
   (`exact`, where the engine's own environment carries the identity of
   *this* process).
3. Ambient workspace markers under `<workspace>/.authorlm/clients/`
   (`ambient`) — may misattribute under parallel chats, and says so. With
   two or more live it declines to name a session at all.
4. Nothing (`none`, `engine="unknown"`). Old rows with no stamp read
   identically, which is why no back-compat shim is needed.

Probe evidence (Claude Code 2.1.246, macOS, 2026-08-29), from a Bash tool
subprocess of this repo's own session::

    CLAUDECODE=1
    CLAUDE_CODE_SESSION_ID=a1ea8c70-63e3-4524-af0e-e1a7852a990f
    CLAUDE_CODE_CHILD_SESSION=1
    CLAUDE_PID=7402
    CLAUDE_CODE_MESSAGING_SOCKET=/tmp/cc-socks/7402.sock
    AI_AGENT=claude-code_2-1-246_agent

`CLAUDE_CODE_SESSION_ID` equals the transcript filename under
`~/.claude/projects/<cwd-with-dashes>/`, so it is the join key to the
conversation itself. It is undocumented, so it is never leaned on alone:
the documented `CLAUDECODE=1` gates the adapter, and the host-pid path
(Branch B, `CLAUDE_CODE_MESSAGING_SOCKET` → marker) is the fallback that
keeps the answer `exact` if the variable ever disappears.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

# The merge date of the stamp. Rows created before it carry no client
# stamp and `authorlm provenance` says so rather than rendering them as
# `unknown` and inviting the inference (§6.4). Nothing is backfilled.
STAMPING_SINCE = "2026-08-30"

MARKER_TTL_HOURS = 12       # a marker older than this is not "live"
MARKER_SWEEP_HOURS = 24     # SessionStart unlinks markers older than this
MAX_CLIENTS = 32            # cap on a writeup's touched-by list
MAX_VERBS = 12              # cap on the verb trail per client

# One stdio MCP process serves exactly one client for its whole life, so
# a per-process uuid is an exact — if anonymous — connection identity.
# `mcp_server.CONNECTION_ID` re-exports this, unchanged in shape.
CONNECTION_ID = f"mcp-{uuid.uuid4().hex[:12]}"

_TS_FORMATS = ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
               "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    for fmt in _TS_FORMATS:
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


# ------------------------------------------------------------ descriptor


@dataclass(frozen=True)
class Client:
    """One chat's identity, as far as this invocation can honestly claim.

    `engine` + `session_id` are the join key. `label`, `started_at` and
    `transcript_hint` are the reconstruction payload. `precision` and
    `adapter` are the audit trail *of the audit trail*."""

    engine: str
    session_id: str | None = None
    label: str | None = None
    started_at: str | None = None
    transcript_hint: str | None = None
    precision: str = "none"          # "exact" | "ambient" | "none"
    adapter: str = "none"
    note: str | None = None

    def key(self) -> dict:
        """~70 bytes — the join key, goes on EVERY row."""
        return {"engine": self.engine, "session": self.session_id,
                "precision": self.precision}

    def full(self) -> dict:
        """~250 bytes — the reconstruction payload, written ONCE per
        (client × AuthorLM session). Keeping the transcript path off the
        per-row stamp is the size argument and the privacy one at once."""
        out = self.key()
        out["adapter"] = self.adapter
        for name in ("label", "started_at", "transcript_hint", "note"):
            value = getattr(self, name)
            if value:
                out[name] = value
        return out


UNKNOWN = Client(engine="unknown", precision="none", adapter="none")


@dataclass(frozen=True)
class Context:
    """Everything a detector may read. Injectable, so the tests never
    touch the real environment — the suite itself runs inside a Claude
    Code Bash call and would otherwise detect the developer's own chat."""

    env: Mapping[str, str]
    surface: str                 # "cli" | "mcp" | "api"
    workspace: Path
    now: str


# --------------------------------------------------------- marker files


def clients_dir(workspace: str | Path) -> Path:
    return Path(workspace) / ".authorlm" / "clients"


def marker_path(workspace: str | Path, engine: str, session_id: str) -> Path:
    safe = str(session_id).replace("/", "_")
    return clients_dir(workspace) / f"{engine}-{safe}.json"


def _read_marker_file(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def read_marker(workspace: str | Path, engine: str,
                session_id: str) -> dict | None:
    """The enrichment lookup, keyed by an id we already know — never a
    guess, so enrichment cannot introduce ambiguity."""
    return _read_marker_file(marker_path(workspace, engine, session_id))


def all_markers(workspace: str | Path) -> list[dict]:
    try:
        paths = sorted(clients_dir(workspace).glob("*.json"))
    except Exception:
        return []
    out = []
    for path in paths:
        data = _read_marker_file(path)
        if data and data.get("session_id"):
            out.append(data)
    return out


def _pid_alive(pid: Any) -> bool:
    """`os.kill(pid, 0)` failing for any reason *other than* the process
    being gone means "assume live" and let age decide — a PermissionError
    is a live process we do not own, and an unsupported platform is not
    evidence of death."""
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return True                       # no pid recorded: age decides
    if value <= 0:
        return True
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except Exception:
        return True
    return True


def marker_is_live(marker: dict, now: str | None = None,
                   ttl_hours: int = MARKER_TTL_HOURS,
                   check_pid: bool = True) -> bool:
    stamped = _parse_ts(marker.get("updated_at") or marker.get("started_at"))
    if stamped is None:
        return False
    reference = _parse_ts(now) or datetime.now(timezone.utc)
    if reference - stamped > timedelta(hours=ttl_hours):
        return False
    if check_pid and not _pid_alive(marker.get("host_pid")):
        return False
    return True


def live_markers(workspace: str | Path, now: str | None = None) -> list[dict]:
    return [m for m in all_markers(workspace) if marker_is_live(m, now)]


def marker_for_host(workspace: str | Path, engine: str, host: str,
                    now: str | None = None) -> str | None:
    """Branch B: map the Claude host pid — which reaches both the tool
    subprocess and the stdio MCP server — to the session id the hook
    recorded beside it.

    Deliberately does NOT apply the pid liveness filter: the host pid
    here is the pid of the process that spawned *this* invocation, so it
    is alive by construction, and asking `os.kill` about it would only
    add a way to be wrong. Age still applies."""
    for marker in all_markers(workspace):
        if marker.get("engine") != engine:
            continue
        if str(marker.get("host_pid")) != str(host):
            continue
        if not marker_is_live(marker, now, check_pid=False):
            continue
        return marker.get("session_id")
    return None


def write_marker_file(workspace: str | Path, data: dict) -> Path:
    """Atomic (`tmp` + `os.replace`), one file per session. Two chats
    write two files and never touch each other's, so there is no lock,
    no last-writer-wins and no clobbering."""
    path = marker_path(workspace, data["engine"], data["session_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return path


def clear_marker_file(workspace: str | Path, engine: str,
                      session_id: str) -> bool:
    try:
        marker_path(workspace, engine, session_id).unlink()
        return True
    except Exception:
        return False


def sweep_markers(workspace: str | Path, now: str | None = None,
                  max_hours: int = MARKER_SWEEP_HOURS) -> int:
    """Bound the directory without a daemon or a cron entry: each
    SessionStart unlinks markers older than 24 h."""
    removed = 0
    reference = _parse_ts(now) or datetime.now(timezone.utc)
    try:
        paths = list(clients_dir(workspace).glob("*.json"))
    except Exception:
        return 0
    for path in paths:
        data = _read_marker_file(path)
        stamped = _parse_ts((data or {}).get("updated_at"))
        if stamped is not None and reference - stamped <= timedelta(hours=max_hours):
            continue
        try:
            path.unlink()
            removed += 1
        except Exception:
            pass
    return removed


# -------------------------------------------------------------- adapters


class Adapter(Protocol):
    name: str
    setup: str | None

    def detect(self, ctx: Context) -> Client | None: ...


def _host_id(env: Mapping[str, str], keys: tuple[str, ...]) -> str | None:
    for name in keys:
        raw = (env.get(name) or "").strip()
        if not raw:
            continue
        value = Path(raw).name if ("/" in raw or "\\" in raw) else raw
        if value.endswith(".sock"):
            value = value[:-len(".sock")]
        if value:
            return value
    return None


@dataclass(frozen=True)
class SimpleAdapter:
    """One entry per engine, declarative — the same shape `MODEL_PROFILES`
    uses. Adding an engine is one tuple entry plus a documented hook
    snippet; an adapter that is not sure returns `None` rather than a
    half-descriptor, so ordering is the only precedence rule."""

    name: str
    engine: str
    gate: Callable[[Mapping[str, str]], bool]
    session_keys: tuple[str, ...] = ()
    host_keys: tuple[str, ...] = ()
    label_keys: tuple[str, ...] = ()
    setup: str | None = None

    def detect(self, ctx: Context) -> Client | None:
        if not self.gate(ctx.env):
            return None
        session_id = None
        for name in self.session_keys:            # Branch A key
            value = (ctx.env.get(name) or "").strip()
            if value:
                session_id = value
                break
        if not session_id and self.host_keys:     # Branch B key
            host = _host_id(ctx.env, self.host_keys)
            if host:
                session_id = marker_for_host(ctx.workspace, self.engine,
                                             host, ctx.now)
        if not session_id:
            return None                           # fall through to ambient
        marker = read_marker(ctx.workspace, self.engine, session_id) or {}
        label = marker.get("label")
        if not label:
            for name in self.label_keys:
                if ctx.env.get(name):
                    label = ctx.env[name]
                    break
        return Client(engine=self.engine, session_id=session_id,
                      precision="exact", adapter=self.name, label=label,
                      started_at=marker.get("started_at"),
                      transcript_hint=marker.get("transcript_hint"))


@dataclass(frozen=True)
class McpStdioAdapter:
    """Last-resort *engine identification* for a chat engine we do not
    recognise. The id is exact — one process, one client, for its whole
    life — but it names a CONNECTION, not a chat, so it cannot be joined
    to a transcript. What is missing is not precision but identity, and
    `engine` is what says so."""

    name: str = "mcp-stdio"
    setup: str | None = None

    def detect(self, ctx: Context) -> Client | None:
        if ctx.surface != "mcp":
            return None
        return Client(engine="mcp-stdio", session_id=CONNECTION_ID,
                      precision="exact", adapter=self.name,
                      label="stdio MCP connection (engine unidentified)")


CLAUDE_CODE_SETUP = (
    "Register the SessionStart/SessionEnd hooks so a chat records its "
    "label, start time and transcript path:  authorlm client-hook "
    "--print-setup  (paste into .claude/settings.json). The hook only "
    "ENRICHES — a chat without it is still attributed exactly."
)

CLAUDE_CODE = SimpleAdapter(
    name="claude-code",
    engine="claude-code",
    # CLAUDECODE=1 is the DOCUMENTED gate, set in both Bash-tool
    # subprocesses and stdio MCP servers. The session-id variable behind
    # it is not documented, so the gate is what the adapter is pinned to.
    gate=lambda env: env.get("CLAUDECODE") == "1",
    session_keys=("CLAUDE_CODE_SESSION_ID",),
    host_keys=("CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_PID"),
    label_keys=("AI_AGENT",),
    setup=CLAUDE_CODE_SETUP,
)

MCP_STDIO = McpStdioAdapter()

# Ordering IS the precedence rule. `claude-code` first, so a Claude Code
# MCP server resolves to the real chat session and only an unrecognised
# engine's server falls through to `mcp-stdio` — which therefore stays
# last, always.
ADAPTERS: tuple[Adapter, ...] = (CLAUDE_CODE, MCP_STDIO)


# ------------------------------------------------------------ resolution


_STATE: dict[str, Any] = {"surface": "api", "workspace": None, "verb": None}
_CACHE: dict[tuple, Client] = {}

# Every environment variable layers 1–2 read. The memo is keyed on their
# values, so it is a memo on the actual inputs rather than a bet that
# `os.environ` never changes — which the test suite needs it not to be.
_ENV_KEYS: tuple[str, ...] = (
    "AUTHORLM_CLIENT", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_PID", "AI_AGENT",
)


def configure(*, surface: str | None = None, workspace: str | None = None,
              verb: str | None = None) -> None:
    """Called once per invocation by the surface that owns it (CLI
    dispatch, MCP `_guard`). This is what "resolved once per invocation"
    means — not sniffing the call stack later."""
    if surface is not None:
        _STATE["surface"] = surface
    if workspace is not None:
        _STATE["workspace"] = str(workspace)
    if verb is not None:
        _STATE["verb"] = verb


def current_verb() -> str | None:
    return _STATE["verb"]


def default_workspace() -> Path:
    return Path(_STATE["workspace"] or os.environ.get("AUTHORLM_WORKSPACE")
                or Path.home())


def default_context() -> Context:
    return Context(env=os.environ, surface=str(_STATE["surface"]),
                   workspace=default_workspace(), now=_now())


def _from_override(raw: str) -> Client | None:
    """`AUTHORLM_CLIENT`: JSON, or `engine:session:label`, or `none`."""
    text = raw.strip()
    if not text:
        return None
    if text.lower() == "none":
        return replace(UNKNOWN, adapter="env")
    if text.startswith("{"):
        data = json.loads(text)
        if not isinstance(data, dict):
            return None
        return Client(engine=str(data.get("engine") or "unknown"),
                      session_id=data.get("session") or data.get("session_id"),
                      label=data.get("label"),
                      started_at=data.get("started_at"),
                      transcript_hint=data.get("transcript_hint"),
                      precision=str(data.get("precision") or "exact"),
                      adapter="env", note=data.get("note"))
    parts = text.split(":", 2)
    engine = parts[0].strip() or "unknown"
    session_id = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
    label = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
    return Client(engine=engine, session_id=session_id, label=label,
                  precision="exact", adapter="env")


def _exact(ctx: Context) -> Client | None:
    """Layers 1 and 2. Env-only (plus keyed marker reads), so memoizable."""
    try:
        raw = ctx.env.get("AUTHORLM_CLIENT")
        if raw:
            found = _from_override(raw)
            if found is not None:
                return found
    except Exception:
        pass
    for adapter in ADAPTERS:
        try:
            found = adapter.detect(ctx)
        except Exception:
            continue
        if found is not None:
            return found
    return None


def _ambient(ctx: Context) -> Client | None:
    """Layer 3. Not memoized — it is the layer that changes."""
    try:
        markers = live_markers(ctx.workspace, ctx.now)
    except Exception:
        return None
    if not markers:
        return None
    if len(markers) == 1:
        marker = markers[0]
        return Client(engine=str(marker.get("engine") or "unknown"),
                      session_id=marker.get("session_id"),
                      label=marker.get("label"),
                      started_at=marker.get("started_at"),
                      transcript_hint=marker.get("transcript_hint"),
                      precision="ambient", adapter="ambient")
    # The refusal. A stamp that says "one of three chats" is recoverable;
    # one that confidently names the wrong chat corrupts the very
    # reconstruction this exists for, and nothing downstream can detect it.
    engines = {str(m.get("engine") or "unknown") for m in markers}
    return Client(engine=engines.pop() if len(engines) == 1 else "unknown",
                  session_id=None, precision="ambient", adapter="ambient",
                  note=f"{len(markers)} live clients — session not claimed")


def current(ctx: Context | None = None) -> Client:
    """The one entry point. Never raises."""
    try:
        if ctx is None:
            ctx = default_context()
        cache_key = (ctx.surface, str(ctx.workspace),
                     tuple(ctx.env.get(k) for k in _ENV_KEYS))
        cached = _CACHE.get(cache_key)
        if cached is not None:
            return cached
        found = _exact(ctx)
        if found is not None:
            _CACHE[cache_key] = found
            return found
        return _ambient(ctx) or UNKNOWN
    except Exception:
        return UNKNOWN


def reset_cache() -> None:
    """Tests only. Layers 1–2 are memoized on their inputs, so this is
    belt-and-braces rather than load-bearing."""
    _CACHE.clear()


# ----------------------------------------------------------- stamp sites


def row_stamp() -> dict:
    """The metadata dict every new row is born with. An unresolved client
    contributes NOTHING — an absent stamp and an old pre-stamp row must
    read identically (§1.3), which is why there is no back-compat shim."""
    try:
        client = current()
        return {"client": client.key()} if client.precision != "none" else {}
    except Exception:
        return {}


def _load_meta(db, table: str, row_id: str, fallback: Any = None) -> dict:
    row = db.one(f"SELECT metadata FROM {table} WHERE id = ?", (row_id,))
    raw = (row["metadata"] if row is not None else fallback) or "{}"
    try:
        meta = json.loads(raw)
    except Exception:
        meta = {}
    return meta if isinstance(meta, dict) else {}


def _write_meta(db, table: str, row_id: str, meta: dict) -> str:
    blob = json.dumps(meta)
    # A metadata-only UPDATE, deliberately not `db.update`: a provenance
    # stamp is not an evolution of the knowledge object, and `_writeup`
    # resolves on READ verbs too, so bumping `version` on every `write
    # status` would make the version number mean something else.
    db.conn.execute(f"UPDATE {table} SET metadata = ? WHERE id = ?",
                    (blob, row_id))
    return blob


def _find(entries: list, engine: str, session_id: str | None) -> dict | None:
    for entry in entries:
        if (isinstance(entry, dict) and entry.get("engine") == engine
                and entry.get("session") == session_id):
            return entry
    return None


def touch(db, table: str, row: Any, verb: str | None = None,
          client: Client | None = None, now: str | None = None) -> None:
    """Record that this client touched this row, deduped by
    `(engine, session)`, with the verb trail — the same dedup-append
    shape `metadata.drafting_models` already uses, and for the same
    reason: a writeup resumed by a second chat is the ordinary case and
    the seam should be visible, with no schema change and no index.

    Wrapped in `db.transaction()` (BEGIN IMMEDIATE) so two parallel chats
    cannot lose an entry to a read-modify-write race. Never raises."""
    try:
        who = client if client is not None else current()
        if who.precision == "none":
            return
        name = verb if verb is not None else current_verb()
        stamp = now or _now()
        row_id = row["id"]
        with db.transaction():
            meta = _load_meta(db, table, row_id, _row_metadata(row))
            entries = meta.get("clients")
            if not isinstance(entries, list):
                entries = []
            entry = _find(entries, who.engine, who.session_id)
            if entry is None:
                if len(entries) >= MAX_CLIENTS:
                    # A writeup touched by more than 32 distinct chat
                    # sessions is itself the finding; record the overflow
                    # rather than growing without bound.
                    meta["clients_truncated"] = int(
                        meta.get("clients_truncated") or 0) + 1
                    meta["clients"] = entries
                    _write_meta(db, table, row_id, meta)
                    return
                entry = {**who.key(), "first": stamp, "last": stamp,
                         "verbs": []}
                entries.append(entry)
            entry["last"] = stamp
            if name:
                verbs = entry.get("verbs")
                if not isinstance(verbs, list):
                    verbs = []
                if name not in verbs and len(verbs) < MAX_VERBS:
                    verbs.append(name)
                entry["verbs"] = verbs
            meta["clients"] = entries
            _write_meta(db, table, row_id, meta)
    except Exception:
        pass


def record_session_client(db, session: Any, now: str | None = None) -> dict:
    """The rich record, written once per client per AuthorLM session.

    An AuthorLM session is a shared ambient work period, so a LIST is the
    only honest shape — several chats legitimately share one. This is
    where `transcript_hint` lives: one copy per client per session, never
    one per row."""
    row = dict(session)
    try:
        who = current()
        if who.precision == "none":
            return row
        stamp = now or _now()
        with db.transaction():
            meta = _load_meta(db, "sessions", row["id"], row.get("metadata"))
            entries = meta.get("clients")
            if not isinstance(entries, list):
                entries = []
            entry = _find(entries, who.engine, who.session_id)
            if entry is None:
                if len(entries) >= MAX_CLIENTS:
                    return row
                entry = who.full()
                entry["first_seen"] = stamp
                entries.append(entry)
            entry["last_seen"] = stamp
            meta["clients"] = entries
            blob = _write_meta(db, "sessions", row["id"], meta)
        return {**row, "metadata": blob}
    except Exception:
        return row


def _row_metadata(row: Any) -> str | None:
    try:
        return row["metadata"]
    except Exception:
        return None
