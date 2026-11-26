"""The engine interface: one turn in, one structured reply out.

An **engine** is the thing that makes a model turn for a per-essay workflow
instance (docs/plans/filter-lens-consolidation.md 11.9 Q6). The workflow
package (`authorlm.workflow`, built separately) owns the session record; an
engine is fed from that record and keeps nothing the record cannot rebuild:

    engine = get_engine("openai-api", model)        # engines/__init__.py
    reply = engine.send(instance_context, message)
    instance.session_ref = engine.session_ref        # persisted by the caller

`instance_context` is a dict:

    {"standing":    [str, ...],   # stable blocks in stable order (charter,
                                  # role, style rules, exemplars, the essay)
     "turns":       [dict, ...],  # the instance's earlier turns
     "session_ref": str | None,   # what the last send left in session_ref
     "manuscript":  str,          # manuscript name
     "essay":       str,          # the essay's file name
     "workspace":   str | None}   # optional; default AUTHORLM_WORKSPACE

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

**The session ref is a speed-up, never the continuity** (Q5, Q7). Each
engine's ref names the engine, its own session handle, a hash of the
standing blocks and the number of turns it has seen. When any of those no
longer matches the context it is given (another engine's ref, standing
blocks changed, turns the session never saw), the engine rebuilds its
conversation from `instance_context`: the standing part first, so a
provider's prompt cache reads it back cheaply (11.3, 11.6).

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


def standing_sha(standing: list[str]) -> str:
    h = hashlib.sha256()
    for block in standing:
        h.update(block.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:12]


@dataclass(frozen=True)
class SessionRef:
    """`<engine>:<handle>:<standing sha>:<turns seen>`, the opaque string
    the caller persists. `turns` counts the turns the session holds after
    the send that wrote it (the caller appends that turn)."""

    engine: str
    handle: str
    sha: str
    turns: int

    def __str__(self) -> str:
        return f"{self.engine}:{self.handle}:{self.sha}:{self.turns}"

    @staticmethod
    def parse(ref: str | None) -> "SessionRef | None":
        if not ref:
            return None
        parts = ref.rsplit(":", 2)
        if len(parts) != 3:
            return None
        head, sha, turns = parts
        engine, _, handle = head.partition(":")
        if not engine or not handle or not turns.isdigit():
            return None
        return SessionRef(engine, handle, sha, int(turns))

    def fits(self, engine: str, context: dict) -> bool:
        """Whether this session can carry the next turn of `context`:
        same engine, same standing blocks, and it has seen every turn."""
        return (self.engine == engine
                and self.sha == standing_sha(context.get("standing") or [])
                and self.turns == len(context.get("turns") or []))


def turn_messages(turns: list[dict]) -> list[tuple[str, str]]:
    """The instance's earlier turns as (role, text) pairs, role "user"
    (the author's side) or "assistant" (the model's side).

    Lenient about the turn's shape so the workflow's record can feed it
    directly: a turn with `role` and `text`/`content`; or one carrying the
    author's words (`message`, `author`, `words`) and the model's
    `reply` (a dict with the reply keys, or a string). Any `decision`
    (accept, reject, redo, edit) is said on the author's side."""
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
        words = turn.get("message") or turn.get("author") or turn.get("words")
        decision = turn.get("decision")
        if decision:
            words = f"[{decision}] {words or ''}".strip()
        if words:
            out.append(("user", str(words)))
        reply = turn.get("reply")
        if reply:
            out.append(("assistant",
                        _reply_text(reply) if isinstance(reply, dict) else str(reply)))
    return out


def _reply_text(reply: dict) -> str:
    return json.dumps({k: reply.get(k) for k in REPLY_KEYS if k in reply},
                      ensure_ascii=False)


def rebuild_transcript(turns: list[dict]) -> str:
    """Earlier turns as one block of text, for an engine that rebuilds a
    lost session in a single message (claude-headless)."""
    lines = []
    for role, text in turn_messages(turns):
        who = "AUTHOR" if role == "user" else "YOU (earlier reply)"
        lines.append(f"--- {who}\n{text}")
    return "\n\n".join(lines)


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
