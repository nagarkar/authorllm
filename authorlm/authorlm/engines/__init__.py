"""Engines: who makes a per-essay workflow's model turns.

    get_engine(name, model) -> Engine
    Engine.send(instance_context, message) -> reply dict
    Engine.session_ref                        # persisted by the caller

The interface, the reply and the instance context are in engines/base.py;
the read requests every engine may make in engines/catalogue.py. Built:
`claude-headless` (claude_headless.py, subscription) and `openai-api`
(openai_api.py, billed). Registered for later, raising NotBuilt:
`claude-api`, `codex-headless` (docs/plans/filter-lens-consolidation.md
11.9 Q6: "In the future, we can add Claude API and Codex headless").

The model: the caller's (chosen per workflow instance), else the project
config's `[engines]` section (`claude_model`, `openai_model`). Neither
names one → NotConfigured; no model is named in code.
"""

from __future__ import annotations

from .base import (REPLY_SCHEMA, Engine, EngineError, NotBuilt, NotConfigured,
                   parse_reply)

ENGINES = ("claude-headless", "openai-api", "claude-api", "codex-headless")
BUILT = ("claude-headless", "openai-api")
CONFIG_KEY = {"claude-headless": "claude_model", "openai-api": "openai_model"}

__all__ = ["ENGINES", "BUILT", "REPLY_SCHEMA", "Engine", "EngineError",
           "NotBuilt", "NotConfigured", "get_engine", "parse_reply"]


def _config() -> dict:
    from .. import paths
    from .openai_api import _project_config

    return _project_config(paths)


def default_model(name: str, config: dict | None = None) -> str | None:
    key = CONFIG_KEY.get(name)
    if not key:
        return None
    section = (config if config is not None else _config()).get("engines") or {}
    return section.get(key) or None


def get_engine(name: str, model: str | None) -> Engine:
    if name not in ENGINES:
        raise LookupError(f"no engine named '{name}'; engines: {', '.join(ENGINES)}")
    if name not in BUILT:
        raise NotBuilt(f"the '{name}' engine is not built yet "
                       f"(built: {', '.join(BUILT)})")
    model = model or default_model(name)
    if not model:
        raise NotConfigured(f"no model for '{name}': pass one, or set "
                            f"[engines] {CONFIG_KEY[name]} in authorlm/config.toml")
    if name == "openai-api":
        from .openai_api import OpenAIEngine

        return OpenAIEngine(model)
    from .claude_headless import ClaudeHeadlessEngine

    return ClaudeHeadlessEngine(model)
