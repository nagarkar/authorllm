"""`openai-api`: a workflow turn on OpenAI's Responses API, billed.

Decided 2026-11-25 (docs/plans/filter-lens-consolidation.md 11.9 Q6): the
model is chosen per workflow instance; its cost is shown, never hidden.

Why the OpenAI SDK and not llmlm/LiteLLM: llmlm's client sends one request
and parses one reply. A turn here is a loop — the model calls read requests
(function calls generated from engines/catalogue.py), the workflow answers
them, the model goes on — plus the provider's own web search tool and a
strict JSON-schema reply. The Responses API does all three in one shape.
The key is the repo's existing one: `OPENAI_API_KEY` from `authorlm/.env`.

One send:
1. If the session ref fits (base.SessionRef.fits), only the new message
   goes, chained with `previous_response_id`; otherwise the conversation is
   rebuilt: the protocol and the standing blocks first (one developer
   message each, unchanged from turn to turn, so OpenAI's automatic prompt
   cache reads them back; `prompt_cache_key` keeps an essay's turns on one
   cache), then the earlier turns, then the message.
2. Every `function_call` in the output is served by the catalogue and sent
   back as `function_call_output`; at most MAX_ROUNDS rounds.
3. The final output text is the structured reply (REPLY_SCHEMA, strict).

Usage: input tokens net of cache reads, cached tokens, output tokens, summed
over the rounds; dollars from llmlm's dated price cache (None when it does
not know the model). Web searches are logged as requests named
`web_search`; their per-call fee is not in `dollars` (the price cache has no
row for it).
"""

from __future__ import annotations

import json
import os
from typing import Any

from . import catalogue as cat
from .base import (REPLY_SCHEMA, Engine, EngineError, NotConfigured,
                   SessionRef, empty_usage, parse_reply, protocol_text,
                   standing_sha, turn_messages)

MAX_ROUNDS = 8


def _get(obj: Any, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def price_usd(model: str, fresh_in: int, cached: int, out: int) -> float | None:
    from llmlm import prices

    book = prices.default()
    for name in (f"openai/{model}", model, f"global.openai.{model}"):
        rates = book.rates(name)
        if rates is not None:
            return rates.usd(fresh_in, out, cache_read=cached)
    return None


class OpenAIEngine(Engine):
    name = "openai-api"

    def __init__(self, model: str, *, client: Any = None, reader_factory=None,
                 pricer=price_usd):
        super().__init__(model.split("/", 1)[1] if model.startswith("openai/") else model)
        self._client = client
        self._reader_factory = reader_factory
        self._pricer = pricer

    @property
    def client(self):
        if self._client is None:
            if not os.environ.get("OPENAI_API_KEY"):
                raise NotConfigured("OPENAI_API_KEY is not set (authorlm/.env)")
            import openai

            self._client = openai.OpenAI()
        return self._client

    def tools(self) -> list[dict]:
        return [{"type": "web_search"}] + [r.function_tool() for r in cat.CATALOGUE]

    def first_input(self, context: dict, message: str) -> list[dict]:
        """The rebuilt conversation, stable part first."""
        items = [{"role": "developer", "content": protocol_text(cat.listing())}]
        for block in context.get("standing") or []:
            items.append({"role": "developer", "content": block})
        for role, text in turn_messages(context.get("turns") or []):
            items.append({"role": role, "content": text})
        items.append({"role": "user", "content": message})
        return items

    def _reader(self, context: dict):
        if self._reader_factory:
            return self._reader_factory(context)
        from .. import paths

        return cat.BookReader(context.get("workspace"), context["manuscript"],
                              config=_project_config(paths))

    def send(self, instance_context: dict, message: str) -> dict:
        ref = SessionRef.parse(instance_context.get("session_ref"))
        previous = ref.handle if ref and ref.fits(self.name, instance_context) else None
        try:
            return self._send(instance_context, message, previous)
        except EngineError:
            raise
        except Exception as err:  # noqa: BLE001 - one rebuild on a lost chain
            if previous and "previous response" in str(err).lower().replace("_", " "):
                return self._send(instance_context, message, None)
            raise EngineError(f"openai-api: {type(err).__name__}: {err}") from err

    def _send(self, context: dict, message: str, previous: str | None) -> dict:
        reader = None
        usage = empty_usage()
        usage["dollars"] = 0.0
        requests: list[dict] = []
        if previous:
            inputs: list[dict] = [{"role": "user", "content": message}]
        else:
            inputs = self.first_input(context, message)
        cache_key = f"authorlm:{context.get('manuscript')}:{context.get('essay')}"[:64]
        response = None
        for _round in range(MAX_ROUNDS + 1):
            kwargs = dict(model=self.model, input=inputs, tools=self.tools(),
                          text={"format": {"type": "json_schema", "name": "editor_reply",
                                           "schema": REPLY_SCHEMA, "strict": True}},
                          prompt_cache_key=cache_key, store=True)
            if previous:
                kwargs["previous_response_id"] = previous
            response = self.client.responses.create(**kwargs)
            self._add_usage(usage, _get(response, "usage"))
            calls = []
            for item in _get(response, "output") or []:
                kind = _get(item, "type")
                if kind == "function_call":
                    calls.append(item)
                elif kind == "web_search_call":
                    action = _get(item, "action") or {}
                    requests.append({"name": "web_search",
                                     "args": {"query": _get(action, "query")},
                                     "chars": 0, "declined": None})
            previous = _get(response, "id")
            if not calls:
                break
            if _round == MAX_ROUNDS:
                raise EngineError(f"openai-api: still asking for material after "
                                  f"{MAX_ROUNDS} rounds; stopped")
            if reader is None:
                reader = self._reader(context)
            inputs = []
            for call in calls:
                try:
                    args = json.loads(_get(call, "arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                text, entry = cat.run(reader, _get(call, "name"), args)
                requests.append(entry)
                inputs.append({"type": "function_call_output",
                               "call_id": _get(call, "call_id"), "output": text})
        status = _get(response, "status")
        if status and status != "completed":
            raise EngineError(f"openai-api: the response is {status}: "
                              f"{_get(response, 'incomplete_details')}")
        parsed = parse_reply(_output_text(response))
        usage["dollars"] = self._pricer(self.model, usage["input"], usage["cached"],
                                        usage["output"])
        self.session_ref = str(SessionRef(
            self.name, str(previous), standing_sha(context.get("standing") or []),
            len(context.get("turns") or []) + 1))
        return self._finish(parsed, usage, requests)

    @staticmethod
    def _add_usage(usage: dict, raw: Any) -> None:
        if raw is None:
            return
        total_in = _get(raw, "input_tokens", 0) or 0
        cached = _get(_get(raw, "input_tokens_details") or {}, "cached_tokens", 0) or 0
        usage["input"] += total_in - cached
        usage["cached"] += cached
        usage["output"] += _get(raw, "output_tokens", 0) or 0


def _output_text(response: Any) -> str:
    text = _get(response, "output_text")
    if text:
        return text
    parts = []
    for item in _get(response, "output") or []:
        if _get(item, "type") == "message":
            for c in _get(item, "content") or []:
                if _get(c, "type") == "output_text":
                    parts.append(_get(c, "text") or "")
    if not parts:
        raise EngineError("openai-api: the response carries no output text")
    return "".join(parts)


def _project_config(paths) -> dict:
    import tomllib

    paths.load_env()
    path = paths.config_path()
    if path.exists():
        try:
            return tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError:
            return {}
    return {}
