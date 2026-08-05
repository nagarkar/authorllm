"""Optional reasoning backend (RFC AuthorLM §23.2).

Two providers, selected in <workspace>/.authorlm/config.toml:

- "litellm" (default): routes through the LiteLLM SDK in-process, so any
  model LiteLLM supports works — e.g. Gemini with GEMINI_API_KEY exported,
  or with the key carried in the config itself (for shells that lack the
  provider env vars, e.g. sandboxed skill sessions):

      [llm]
      enabled = true
      model = "gemini/gemini-2.5-flash"
      api_key = "..."   # optional; the named env var wins when set

- "openai": any OpenAI-compatible HTTP endpoint (LiteLLM proxy, Ollama,
  LM Studio) via the standard library — no dependencies:

      [llm]
      enabled = true
      provider = "openai"
      base_url = "http://localhost:4000/v1"
      model = "gpt-4o-mini"
      api_key_env = "AUTHORLM_LLM_KEY"

Every capability that uses the LLM degrades gracefully: complete() returns
None when the LLM is disabled, unconfigured, or unreachable, and callers
fall back to evidence-based heuristics. Abstention stays a valid output
(§11.5) — the LLM enriches guidance; it never becomes load-bearing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_MODEL = "gemini/gemini-2.5-flash"
TEMPERATURE = 0.2
RETRIES = 2  # transient-failure retries with exponential backoff (1s, 2s)


class LLMClient:
    def __init__(self, config: dict):
        llm = config.get("llm", {})
        self.enabled = bool(llm.get("enabled"))
        self.provider = llm.get("provider", "litellm")
        self.model = llm.get("model", DEFAULT_MODEL)
        self.base_url = llm.get("base_url", "http://localhost:4000/v1").rstrip("/")
        # Key resolution: the named env var wins; an `api_key` field in
        # config.toml is the fallback. The config path exists because callers
        # like the Claude Code skill run the CLI from shells without the
        # provider env vars — a key in ~/.authorlm/config.toml makes the CLI
        # fully capable everywhere (chmod 600 applies). For litellm the key
        # is passed per-call; for openai it is the bearer header.
        self.api_key = (os.environ.get(llm.get("api_key_env", "AUTHORLM_LLM_KEY"), "")
                        or llm.get("api_key", ""))
        self.timeout = llm.get("timeout_seconds", 120)
        # Cap on manuscript text sent per extraction call — cost control and
        # extraction quality (concept selection degrades on very long inputs).
        self.extraction_max_chars = llm.get("extraction_max_chars", 24000)
        # Record/replay cache (RFC §24.5 replay discipline). When set, each
        # unique payload (model + temperature + messages) is sent to the LLM
        # at most once; the response is stored as a JSON file and replayed on
        # every subsequent identical request. Any change to a prompt, source
        # document, or model re-records exactly the calls it affects.
        self.cache_dir = llm.get("cache_dir")
        # Usage accounting, reported after every step that touches the LLM.
        self.live_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.replays = 0

    def stats_line(self) -> str | None:
        """One brief line of LLM usage for this step, or None if the LLM
        was never consulted."""
        if not (self.live_calls or self.replays):
            return None
        parts = [
            f"LLM: {self.live_calls} live call(s) "
            f"({self.input_tokens:,} in / {self.output_tokens:,} out tokens)"
        ]
        if self.replays:
            parts.append(f"{self.replays} replayed from cache")
        return " — ".join(parts)

    def _record_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.live_calls += 1
        self.input_tokens += prompt_tokens
        self.output_tokens += completion_tokens

    def complete(self, system: str, user: str) -> str | None:
        """Return the model's reply, or None if the LLM is disabled or
        unreachable — callers must treat None as 'reason without the LLM'."""
        if not self.enabled:
            return None
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        cache_path = self._cache_path(messages)
        if cache_path and cache_path.exists():
            self.replays += 1
            return json.loads(cache_path.read_text())["response"]
        if self.provider == "litellm":
            result = self._complete_litellm(messages)
        else:
            result = self._complete_openai(messages)
        if result is None:
            return None
        reply, prompt_tokens, completion_tokens = result
        self._record_usage(prompt_tokens, completion_tokens)
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(
                {
                    "model": self.model,
                    "temperature": TEMPERATURE,
                    "messages": messages,
                    "response": reply,
                    "usage": {
                        "input_tokens": prompt_tokens,
                        "output_tokens": completion_tokens,
                    },
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            ))
        return reply

    def _cache_path(self, messages: list[dict]) -> Path | None:
        if not self.cache_dir:
            return None
        payload = json.dumps(
            {"model": self.model, "temperature": TEMPERATURE, "messages": messages},
            sort_keys=True,
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()[:20]
        return Path(self.cache_dir) / f"{digest}.json"

    def complete_json(self, system: str, user: str):
        """Like complete(), but parse the reply as JSON (tolerating markdown
        code fences). Returns the parsed value, or None on any failure."""
        reply = self.complete(system + " Reply with JSON only, no prose.", user)
        if reply is None:
            return None
        text = reply.strip()
        fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            print("warning: LLM returned unparseable JSON; ignoring.", file=sys.stderr)
            return None

    def _complete_litellm(self, messages: list[dict]) -> tuple[str, int, int] | None:
        try:
            import litellm
        except ImportError:
            print(
                "warning: provider 'litellm' configured but the package is not "
                "installed (pip install litellm); continuing without LLM.",
                file=sys.stderr,
            )
            return None
        litellm.suppress_debug_info = True
        for attempt in range(RETRIES + 1):
            try:
                response = litellm.completion(
                    model=self.model,
                    messages=messages,
                    temperature=TEMPERATURE,
                    timeout=self.timeout,
                    # Explicit key (env var or config api_key) overrides
                    # litellm's own provider-env detection; absent, litellm
                    # reads GEMINI_API_KEY etc. itself as before.
                    **({"api_key": self.api_key} if self.api_key else {}),
                )
                usage = getattr(response, "usage", None)
                return (
                    response.choices[0].message.content,
                    (getattr(usage, "prompt_tokens", 0) or 0) if usage else 0,
                    (getattr(usage, "completion_tokens", 0) or 0) if usage else 0,
                )
            except Exception as err:  # LiteLLM raises many provider-specific types
                if attempt < RETRIES:
                    delay = 2 ** attempt
                    print(f"warning: LLM call failed ({err}); retrying in {delay}s "
                          f"({attempt + 1}/{RETRIES})…", file=sys.stderr)
                    time.sleep(delay)
                    continue
                print(f"warning: LLM call failed after {RETRIES + 1} attempts "
                      f"({err}); continuing without LLM.", file=sys.stderr)
                return None

    def _complete_openai(self, messages: list[dict]) -> tuple[str, int, int] | None:
        payload = {"model": self.model, "messages": messages, "temperature": TEMPERATURE}
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
            },
        )
        for attempt in range(RETRIES + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode())
                usage = body.get("usage", {})
                return (
                    body["choices"][0]["message"]["content"],
                    usage.get("prompt_tokens", 0) or 0,
                    usage.get("completion_tokens", 0) or 0,
                )
            except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as err:
                if attempt < RETRIES:
                    delay = 2 ** attempt
                    print(f"warning: LLM call failed ({err}); retrying in {delay}s "
                          f"({attempt + 1}/{RETRIES})…", file=sys.stderr)
                    time.sleep(delay)
                    continue
                print(f"warning: LLM call failed after {RETRIES + 1} attempts "
                      f"({err}); continuing without LLM.", file=sys.stderr)
                return None
