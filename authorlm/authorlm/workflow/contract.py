"""The engine contract the workflow codes against.

An engine is anything with

    send(instance_context: dict, message: str) -> dict

returning a reply of the shape `REPLY_KEYS`: `text` (the model's reply),
`reason` (what changed and why, design §11.9 Q16), `proposed` (the proposed
text, the form; "" when nothing is proposed), `suggested_blurbs` (short
reject reasons for the redo checklist, Q8b), `usage` (input, cached,
output, dollars, est_subscription; Q7 "cost against quality") and
`requests` (what the model asked for through the read catalogue, each
`{name, args, chars}`, Q20). `usage.dollars` is None when the engine does
not know what the turn cost (absent counts as unknown); it is never turned
into $0.

After a send, an engine may expose `session_ref`, the handle its next send
resumes from; `core.send_turn` stores it in the instance's
`engine_state["session_ref"]`, in the same transaction as the turn.

`instance_context` is built by `workflow.context.instance_context`: the
standing blocks first, in a stable order so an engine can cache them, then
the essay as it stands and the turns so far. The engines themselves (the
`engines` package, build plan §12 row 2) register a factory here with
`register_engine`; the workflow never imports them.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

REPLY_KEYS = ("text", "reason", "proposed", "suggested_blurbs", "usage",
              "requests")
USAGE_KEYS = ("input", "cached", "output", "dollars", "est_subscription")


class Engine(Protocol):
    name: str
    model: str

    def send(self, instance_context: dict, message: str) -> dict: ...


_FACTORIES: dict[str, Callable[[str], Engine]] = {}


def register_engine(name: str, factory: Callable[[str], Engine]) -> None:
    """Make engine `name` available; `factory(model)` returns an Engine."""
    _FACTORIES[name] = factory


def make_engine(name: str, model: str) -> Engine:
    if name not in _FACTORIES:
        raise LookupError(
            f"engine '{name}' is not available in this build (registered: "
            f"{', '.join(sorted(_FACTORIES)) or 'none'})")
    return _FACTORIES[name](model)


def normalize_reply(reply: Any) -> dict:
    """Check an engine's reply against the contract and fill defaults, so a
    turn is always recorded in one shape. Raises ValueError on a reply that
    is not a dict or has a field of the wrong type."""
    if not isinstance(reply, dict):
        raise ValueError(f"engine reply is not a dict: {type(reply).__name__}")
    out = {
        "text": reply.get("text") or "",
        "reason": reply.get("reason") or "",
        "proposed": reply.get("proposed") or "",
        "suggested_blurbs": list(reply.get("suggested_blurbs") or []),
        "usage": dict(reply.get("usage") or {}),
        "requests": list(reply.get("requests") or []),
    }
    for key in ("text", "reason", "proposed"):
        if not isinstance(out[key], str):
            raise ValueError(f"engine reply: {key} must be text")
    if not all(isinstance(b, str) for b in out["suggested_blurbs"]):
        raise ValueError("engine reply: suggested_blurbs must be text")
    usage = {k: out["usage"].get(k) or 0 for k in USAGE_KEYS}
    usage["dollars"] = out["usage"].get("dollars")  # None: cost unknown
    for key, value in usage.items():
        if key == "dollars" and value is None:
            continue
        if not isinstance(value, (int, float)):
            raise ValueError(f"engine reply: usage.{key} must be a number")
    out["usage"] = usage
    requests = []
    for r in out["requests"]:
        if not isinstance(r, dict) or not r.get("name"):
            raise ValueError("engine reply: each request needs a name")
        requests.append({"name": r["name"], "args": r.get("args") or {},
                         "chars": int(r.get("chars") or 0)})
    out["requests"] = requests
    return out
