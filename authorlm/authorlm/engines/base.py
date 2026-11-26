"""The engine interface: one turn in, one structured reply out.

An **engine** is the thing that makes a model turn for a per-essay workflow
instance (docs/plans/filter-lens-consolidation.md 11.9 Q6). The workflow
package (`authorlm.workflow`) owns the session record; an engine is fed from
that record and keeps nothing the record cannot rebuild:

    engine = get_engine("openai-api", model)        # engines/__init__.py
    reply = engine.send(instance_context, message)
    engine_state["session_ref"] = engine.session_ref  # persisted by the caller

The engines also register themselves with the workflow's registry
(`workflow.contract.register_engine`), so `contract.make_engine(name,
model)` returns them.

`instance_context` comes in either of two shapes (`Turn.of` reads both):

- the workflow's (`workflow.context.instance_context`):
  `{"instance": {"id", "file", "engine_state", ...}, "standing": [{"name",
  "text", ...}, ...], "essay": <the essay's text>, "turns": [...]}`; the
  session ref is read from `instance.engine_state["session_ref"]`, the
  manuscript from the instance's row;
- the plain one (`authorlm engine try`, tests): `{"standing": [str, ...],
  "turns": [...], "session_ref": str | None, "manuscript": name, "essay":
  file, "workspace": str | None}`.

The reply is a dict, the same for every engine (11.9 Q8b, Q16): the
reason, the proposed text and the suggested blurbs arrive as structured
output under a JSON schema (`REPLY_SCHEMA`), never parsed out of prose:

    {"text": str, "reason": str, "proposed": str | None,
     "suggested_blurbs": [str],
     "usage": {"input", "cached", "output", "dollars", "est_subscription"},
     "requests": [{"name", "args", "chars", "declined"}],
     "engine": name, "model": model}

`usage.input` counts input tokens not read from a cache (cache writes
included), `usage.cached` the ones read from it. `dollars` is what a billed
engine was charged (None when the price is unknown, never guessed);
`est_subscription` is a headless engine's API-list-price equivalent of the
turn, the estimate of subscription drawdown (11.9 "Cost against quality").

**The session ref is a speed-up, never the continuity** (Q5, Q7). A ref
names the engine, its session handle, a hash of the standing blocks, a hash
of the essay text and the number of turns the session has seen. A send:

- **rebuilds** from the context when there is no ref, it is another
  engine's, the standing blocks changed, or the record holds fewer turns
  than the session saw: the standing part first (so a provider's prompt
  cache reads it back), the essay, the earlier turns, the message;
- otherwise **resumes**, sending first what the session has not seen: the
  turns recorded since (accepts, rejects, edits made without a model call)
  and the essay as it now stands if it changed, then the message.

Terms:
- **standing**: the blocks that do not change from turn to turn.
- **request**: a read the model asks for through the catalogue
  (engines/catalogue.py); every one is logged on the reply.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROTOCOL_FILE = Path(__file__).resolve().parent.parent / "prompts" / "engine-protocol.md"

REPLY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "What you say to the author this turn: discussion, "
                           "questions, a finding. Plain prose."},
        "reason": {
            "type": "string",
            "description": "What changed and why, in plain words. Empty only "
                           "when nothing is proposed."},
        "proposed": {
            "type": ["string", "null"],
            "description": "The proposed text of the unit, whole, ready to "
                           "replace it; null when this turn proposes no text."},
        "suggested_blurbs": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short reject reasons (two or three words) the "
                           "author may want on the redo checklist."},
    },
    "required": ["text", "reason", "proposed", "suggested_blurbs"],
    "additionalProperties": False,
}

REPLY_KEYS = ("text", "reason", "proposed", "suggested_blurbs")


class EngineError(RuntimeError):
    """A turn failed: the engine could not be reached, or its reply did
    not match REPLY_SCHEMA. Never retried into a different engine."""


class NotConfigured(EngineError):
    """The engine cannot run here: no key, no binary, no model named."""


class NotBuilt(EngineError):
    """A registered engine name the design keeps room for, not built yet."""


def parse_reply(raw: Any) -> dict:
    """The structured reply, checked against REPLY_SCHEMA.

    `raw` is the provider's structured output: a dict, or the JSON text of
    one. Strict: a missing key, a wrong type or an extra key is an
    EngineError naming it; a blurb that is not a non-empty string is
    dropped (and blurbs are stripped and de-duplicated, order kept)."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as err:
            raise EngineError(f"the reply is not JSON ({err}): {raw[:200]!r}")
    if not isinstance(raw, dict):
        raise EngineError(f"the reply is not a JSON object: {raw!r:.200}")
    missing = [k for k in REPLY_KEYS if k not in raw]
    if missing:
        raise EngineError(f"the reply lacks {', '.join(missing)}: {raw!r:.300}")
    extra = sorted(set(raw) - set(REPLY_KEYS))
    if extra:
        raise EngineError(f"the reply has keys outside the schema: {extra}")
    for key in ("text", "reason"):
        if not isinstance(raw[key], str):
            raise EngineError(f"the reply's {key} is not a string")
    if raw["proposed"] is not None and not isinstance(raw["proposed"], str):
        raise EngineError("the reply's proposed is neither a string nor null")
    if not isinstance(raw["suggested_blurbs"], list):
        raise EngineError("the reply's suggested_blurbs is not a list")
    blurbs: list[str] = []
    for b in raw["suggested_blurbs"]:
        if isinstance(b, str) and b.strip() and b.strip() not in blurbs:
            blurbs.append(b.strip())
    return {"text": raw["text"], "reason": raw["reason"],
            "proposed": raw["proposed"], "suggested_blurbs": blurbs}


def empty_usage() -> dict:
    return {"input": 0, "cached": 0, "output": 0, "dollars": 0.0,
            "est_subscription": 0.0}


def protocol_text(catalogue_listing: str) -> str:
    """The engine protocol (prompts/engine-protocol.md) with the read
    catalogue's listing in place. Shared by every engine, first in the
    standing part."""
    text = PROTOCOL_FILE.read_text(encoding="utf-8")
    return text.replace("{catalogue}", catalogue_listing)


def _sha(parts: list[str]) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:12]


def standing_sha(standing: list[str]) -> str:
    return _sha(list(standing))


# ------------------------------------------------------------ the context

@dataclass
class Turn:
    """One send's view of the instance context, whichever shape it came
    in (see the module docstring)."""

    standing: list[str]
    essay_file: str
    essay_text: str | None
    turns: list[dict]
    session_ref: str | None
    manuscript: str | None
    instance_id: str | None
    workspace: str | None

    @staticmethod
    def of(context: dict) -> "Turn":
        instance = context.get("instance")
        standing = []
        for block in context.get("standing") or []:
            if isinstance(block, dict):
                text = block.get("text") or ""
                if text.strip():
                    standing.append(f"## {block.get('name', 'block')}\n\n{text}")
            elif str(block).strip():
                standing.append(str(block))
        if isinstance(instance, dict):
            state = instance.get("engine_state") or {}
            if isinstance(state, str):
                try:
                    state = json.loads(state)
                except json.JSONDecodeError:
                    state = {}
            return Turn(standing, instance.get("file") or "", context.get("essay"),
                        list(context.get("turns") or []),
                        context.get("session_ref") or state.get("session_ref"),
                        context.get("manuscript"), instance.get("id"),
                        context.get("workspace"))
        return Turn(standing, context.get("essay") or "", None,
                    list(context.get("turns") or []), context.get("session_ref"),
                    context.get("manuscript"), None, context.get("workspace"))

    def essay_block(self) -> str | None:
        if self.essay_text is None:
            return None
        return f"THE ESSAY BEING EDITED, {self.essay_file}, as it now stands:\n\n{self.essay_text}"

    def essay_sha(self) -> str:
        return _sha([self.essay_text or ""])


@dataclass(frozen=True)
class SessionRef:
    """`<engine>:<handle>:<standing sha>:<essay sha>:<turns seen>`, the
    opaque string the caller persists. `turns` counts the turns the
    session holds after the send that wrote it (the caller records that
    turn)."""

    engine: str
    handle: str
    sha: str
    essay: str
    turns: int

    def __str__(self) -> str:
        return f"{self.engine}:{self.handle}:{self.sha}:{self.essay}:{self.turns}"

    @staticmethod
    def parse(ref: str | None) -> "SessionRef | None":
        if not ref:
            return None
        parts = ref.rsplit(":", 3)
        if len(parts) != 4:
            return None
        head, sha, essay, turns = parts
        engine, _, handle = head.partition(":")
        if not engine or not handle or not turns.isdigit():
            return None
        return SessionRef(engine, handle, sha, essay, int(turns))

    @staticmethod
    def after(engine: str, handle: str, turn: Turn) -> "SessionRef":
        return SessionRef(engine, handle, standing_sha(turn.standing),
                          turn.essay_sha(), len(turn.turns) + 1)

    def resumable(self, engine: str, turn: Turn) -> bool:
        """Whether this session can carry the next turn: same engine, same
        standing blocks, and the record holds every turn it saw."""
        return (self.engine == engine
                and self.sha == standing_sha(turn.standing)
                and self.turns <= len(turn.turns))

    def catch_up(self, turn: Turn, message: str) -> str:
        """The message for a resumed session: what it has not seen yet
        (turns recorded since, the essay if it changed), then the message."""
        parts = []
        unseen = rebuild_transcript(turn.turns[self.turns:])
        if unseen:
            parts.append("TURNS RECORDED SINCE YOUR LAST REPLY:\n\n" + unseen)
        if turn.essay_text is not None and self.essay != turn.essay_sha():
            parts.append("THE ESSAY HAS CHANGED; IT NOW STANDS AS:\n\n" + turn.essay_text)
        if not parts:
            return message
        parts.append("THIS TURN:\n\n" + message)
        return "\n\n=====\n\n".join(parts)


def turn_messages(turns: list[dict]) -> list[tuple[str, str]]:
    """The instance's earlier turns as (role, text) pairs, role "user"
    (the author's side) or "assistant" (the model's side).

    Reads the workflow's turn rows (`action`, `author_text`, `reply` as
    text with `reason` and `proposed` beside it) as well as `{role, text}`
    and `{message, reply: {...}, decision}`. The action (accept, reject,
    redo, edit, reply) is said on the author's side."""
    out: list[tuple[str, str]] = []
    for turn in turns or []:
        if not isinstance(turn, dict):
            out.append(("user", str(turn)))
            continue
        role = turn.get("role")
        if role:
            text = turn.get("text", turn.get("content", ""))
            if isinstance(text, dict):
                text = _reply_text(text)
            side = "assistant" if role in ("assistant", "model", "engine") else "user"
            out.append((side, str(text)))
            continue
        words = (turn.get("message") or turn.get("author_text") or turn.get("author")
                 or turn.get("words") or "")
        action = turn.get("decision") or turn.get("action")
        blurbs = turn.get("blurbs") or []
        if blurbs:
            words = f"{words} (ticked: {', '.join(map(str, blurbs))})".strip()
        if action:
            label = turn.get("unit_label")
            words = f"[{action}{' ' + label if label else ''}] {words}".strip()
        if words:
            out.append(("user", str(words)))
        reply = turn.get("reply")
        if isinstance(reply, dict):
            out.append(("assistant", _reply_text(reply)))
        elif reply or turn.get("proposed") or turn.get("reason"):
            out.append(("assistant", _reply_text({
                "text": reply or "", "reason": turn.get("reason") or "",
                "proposed": turn.get("proposed") or None})))
    return out


def _reply_text(reply: dict) -> str:
    return json.dumps({k: reply.get(k) for k in REPLY_KEYS if k in reply},
                      ensure_ascii=False)


def rebuild_transcript(turns: list[dict]) -> str:
    """Turns as one block of text, for a message that carries them."""
    lines = []
    for role, text in turn_messages(turns):
        who = "AUTHOR" if role == "user" else "YOU (earlier reply)"
        lines.append(f"--- {who}\n{text}")
    return "\n\n".join(lines)


def manuscript_of(turn: Turn) -> tuple[str | None, str]:
    """(workspace, manuscript name) for the turn's book: the context's own,
    or the workflow instance's row."""
    if turn.manuscript:
        return turn.workspace, turn.manuscript
    if not turn.instance_id:
        raise EngineError("the instance context names no manuscript")
    from .. import api

    db = api.open_db(turn.workspace)
    row = db.one("SELECT m.name FROM workflow_instances i JOIN manuscripts m "
                 "ON m.id = i.manuscript_id WHERE i.id = ?", (turn.instance_id,))
    if not row:
        raise EngineError(f"no workflow instance '{turn.instance_id}'")
    return turn.workspace, row["name"]


class Engine:
    """Base class. Subclasses set `name`, implement `send`, and leave the
    ref the next send should resume from in `session_ref`."""

    name = "engine"

    def __init__(self, model: str):
        self.model = model
        self.session_ref: str | None = None

    def send(self, instance_context: dict, message: str) -> dict:  # pragma: no cover
        raise NotImplementedError

    def _finish(self, parsed: dict, usage: dict, requests: list[dict]) -> dict:
        return {**parsed, "usage": usage, "requests": requests,
                "engine": self.name, "model": self.model}
