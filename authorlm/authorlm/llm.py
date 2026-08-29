"""Optional reasoning backend (RFC AuthorLM §23.2).

Configured in the project's config.toml (authorlm/paths.py); keys never
appear there — they live in .env and reach this module as environment
variables.

**Choosing a model is choosing a vendor.** LiteLLM's convention is that
the vendor is the model string's prefix, and each vendor reads its own
standard environment variable — so there is no separate `vendor` field to
keep in sync, and switching vendors is a one-line config change:

      model = "gemini/gemini-2.5-flash"    → GEMINI_API_KEY
      model = "openai/gpt-4o-mini"         → OPENAI_API_KEY
      model = "anthropic/claude-sonnet-5"  → ANTHROPIC_API_KEY

Two transports, selected by `provider`:

- "litellm" (default): routes through the LiteLLM SDK in-process, so any
  model LiteLLM supports works. Authentication is LiteLLM's job — it
  reads the vendor's env var itself, which also covers vendors whose auth
  is not a bare key (Vertex/Bedrock credentials).

      [llm]
      enabled = true
      model = "gemini/gemini-2.5-flash"

- "openai": any OpenAI-compatible HTTP endpoint (LiteLLM proxy, Ollama,
  LM Studio) via the standard library — no dependencies. Here the bearer
  token is ours to send, and `api_key_env` names the variable holding it
  (such endpoints use arbitrary tokens, so it cannot be derived):

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

# LiteLLM's model-string prefixes and the environment variable each
# vendor reads. Extend as vendors are adopted; a prefix that is absent
# here simply means "let litellm resolve it", which is the correct
# behavior for credential-file vendors (vertex_ai, bedrock).
VENDOR_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "groq": "GROQ_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


def vendor_of(model: str) -> str:
    """The vendor a litellm model string names, '' when unprefixed."""
    return model.split("/", 1)[0] if "/" in model else ""


def vendor_key(model: str, llm: dict) -> str:
    """The API key for a model, from the environment only.

    `api_key_env` wins when set — an OpenAI-compatible proxy uses an
    arbitrary token that no convention can derive. Otherwise the vendor
    prefix picks the standard variable."""
    named = llm.get("api_key_env", "")
    if named:
        return os.environ.get(named, "")
    return os.environ.get(VENDOR_KEY_ENV.get(vendor_of(model), ""), "")
TEMPERATURE = 0.2
RETRIES = 2  # transient-failure retries with exponential backoff (1s, 2s)


class LLMClient:
    def __init__(self, config: dict):
        self.config = config
        llm = config.get("llm", {})
        self.enabled = bool(llm.get("enabled"))
        self.provider = llm.get("provider", "litellm")
        self.model = llm.get("model", DEFAULT_MODEL)
        self.base_url = llm.get("base_url", "http://localhost:4000/v1").rstrip("/")
        # api_key is a property (below) — see its docstring for why it
        # cannot be resolved once here and cached.
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

    @property
    def api_key(self) -> str:
        """The vendor key for the CURRENT `self.model`, resolved fresh on
        every access — never cached. AE/key-by-model: `summarizer_llm`
        (summaries.py) and `editor_llm` (passes.py) construct an
        LLMClient from `[llm]` and then reassign `.model` to a
        cross-vendor `[critique]` override (e.g. `[llm] model` on Gemini,
        `summarizer_model`/`editor_model` on Anthropic). A key resolved
        once at __init__ time, before that reassignment, would keep
        naming the ORIGINAL model's vendor — handing the new vendor's
        API a foreign key (silent AuthenticationError on the litellm
        path, a bad bearer token on the raw-HTTP path). Keys come from
        the environment (.env loaded at config load), never from
        config.toml. On the litellm path this is only a courtesy
        override — litellm resolves the vendor's credentials itself; on
        the raw-HTTP path it is the bearer token."""
        return vendor_key(self.model, self.config.get("llm", {}))

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

    def complete(self, system: str, user: str,
                 thinking_budget: int | None = None) -> str | None:
        """Return the model's reply, or None if the LLM is disabled or
        unreachable — callers must treat None as 'reason without the LLM'.

        `thinking_budget` caps the reasoning-token budget for models that
        think by default (Gemini 2.5+): 0 disables thinking outright.
        Schema-driven callers (extraction) pass 0 — deliberation buys
        them nothing and thinking tokens are generated at decode speed
        (it-0ae002ed7321: 80% of a 4-minute extraction was thinking).
        None leaves the model's default behavior untouched (drafting and
        guidance may genuinely benefit). litellm provider only; the
        plain-OpenAI path ignores it."""
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
            result = self._complete_litellm(messages,
                                            thinking_budget=thinking_budget)
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

    def complete_json(self, system: str, user: str,
                      thinking_budget: int | None = None):
        """Like complete(), but parse the reply as JSON (tolerating markdown
        code fences). Returns the parsed value, or None on any failure."""
        reply = self.complete(system + " Reply with JSON only, no prose.", user,
                              thinking_budget=thinking_budget)
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

    def _complete_litellm(self, messages: list[dict],
                          thinking_budget: int | None = None
                          ) -> tuple[str, int, int] | None:
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
        # AE/temp-compat: some models (Claude 5 family — sonnet-5, fable-5)
        # reject any `temperature` but 1 and raise UnsupportedParamsError.
        # That rejection is deterministic — retrying the identical call
        # below would just fail the same way three times — so it must be
        # resolved before the call is ever attempted, not caught after.
        # `drop_params` is LiteLLM's own documented remedy for exactly this
        # message ("To drop unsupported params, set litellm.drop_params =
        # True"): it silently omits a param the target model doesn't
        # support while leaving it untouched for models that do, so the
        # backoff loop below only ever sees genuine transient failures.
        litellm.drop_params = True
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
                    **({"thinking": {"type": "enabled",
                                     "budget_tokens": thinking_budget}}
                       if thinking_budget is not None else {}),
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


# --------------------------------------------------------- image generation

DEFAULT_IMAGE_MODEL = "gemini/gemini-2.5-flash-image"


def resolve_image_setting(config: dict, illus_key: str, llm_key: str,
                          default):
    """Precedence for an illustration-rendering setting: `[illustrations]`
    is authoritative once a project has migrated to it; `[llm]` is the
    fallback for configs that haven't; `default` covers configs with
    neither. `illus_key`/`llm_key` differ because `[illustrations]` uses
    its own short name (`model`) where `[llm]` used the historical
    `image_model`/`image_size`. The single seam for this precedence —
    every reader of the illustration model/size goes through it."""
    illustrations = config.get("illustrations", {}) or {}
    if illus_key in illustrations:
        return illustrations[illus_key]
    llm = config.get("llm", {}) or {}
    return llm.get(llm_key, default)


def generate_image(config: dict, prompt: str,
                   input_png: bytes | None = None) -> bytes:
    """One rendered PNG for an illustration slot. The model comes from
    `[illustrations] model` (a litellm-style string), falling back to
    the legacy `[llm] image_model`, then the default gemini flash image
    model. gemini/* models call the Gemini REST API directly — the
    multimodal generateContent endpoint supports image output and
    image-conditioned editing (`--from` continuity), which
    litellm.image_generation cannot express; every other model string
    routes through litellm.image_generation. Unlike completions there is
    no silent degradation: rendering is an explicit act, so failures
    raise RuntimeError with a readable message."""
    from . import paths

    llm = config.get("llm", {}) or {}
    model = resolve_image_setting(config, "model", "image_model",
                                  DEFAULT_IMAGE_MODEL)
    # The image model is chosen independently of the text model, so its
    # key is resolved from ITS OWN vendor prefix — the text model's key
    # is the wrong key the moment the two vendors differ.
    key = vendor_key(model, {})
    timeout = llm.get("timeout_seconds", 120)
    if model.startswith("gemini/"):
        if not key:
            raise RuntimeError(
                "no API key for image generation — set GEMINI_API_KEY in "
                f"{paths.env_path()}")
        return _gemini_image(model.split("/", 1)[1], prompt, input_png,
                             key, timeout)
    if input_png is not None:
        raise RuntimeError(
            f"--from (image-conditioned render) needs a gemini/* image "
            f"model; '{model}' goes through litellm.image_generation, "
            "which is text-to-image only")
    try:
        import litellm
    except ImportError as err:
        raise RuntimeError(
            "provider 'litellm' needed for non-gemini image models "
            "(pip install litellm)") from err
    litellm.suppress_debug_info = True
    # Aspect ratio is ratified style law, not a per-render choice; the
    # OpenAI image API defaults to a square, so [illustrations] image_size
    # (falling back to the legacy [llm] image_size) carries the
    # manuscript's shape (gpt-image-* landscape 3:2 = 1536x1024).
    size = resolve_image_setting(config, "image_size", "image_size", "")
    try:
        response = litellm.image_generation(
            model=model, prompt=prompt, timeout=timeout,
            **({"size": size} if size else {}))
    except Exception as err:
        # Same contract as the gemini path: rendering is an explicit act,
        # so it fails with a sentence, not a provider traceback.
        raise RuntimeError(
            f"image model '{model}' failed: {str(err).strip()[:300]}"
        ) from err
    data = (response["data"] if isinstance(response, dict)
            else response.data)[0]
    b64 = data["b64_json"] if isinstance(data, dict) else data.b64_json
    if not b64:
        raise RuntimeError(f"image model '{model}' returned no image data")
    import base64
    return base64.b64decode(b64)


def _gemini_image(model: str, prompt: str, input_png: bytes | None,
                  key: str, timeout: int) -> bytes:
    import base64

    parts: list[dict] = [{"text": prompt}]
    if input_png is not None:
        parts.append({"inline_data": {
            "mime_type": "image/png",
            "data": base64.b64encode(input_png).decode("ascii")}})
    body = {"contents": [{"parts": parts}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}}
    request = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "x-goog-api-key": key},
    )
    try:
        try:
            with urllib.request.urlopen(request,
                                        timeout=timeout) as response:
                payload = json.loads(response.read().decode())
        except (TimeoutError, urllib.error.URLError) as err:
            if (isinstance(err, urllib.error.HTTPError)):
                raise
            # Image generations run long and stall transiently; one
            # fresh attempt recovers most timeouts (live: 2026-08-10).
            with urllib.request.urlopen(request,
                                        timeout=timeout) as response:
                payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as err:
        detail = err.read().decode(errors="replace")[:400]
        raise RuntimeError(
            f"Gemini image call failed ({err.code}): {detail}") from err
    except urllib.error.URLError as err:
        raise RuntimeError(f"Gemini image call failed: {err}") from err
    for candidate in payload.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            blob = part.get("inlineData") or part.get("inline_data")
            if blob and blob.get("data"):
                return base64.b64decode(blob["data"])
    finish = (payload.get("candidates") or [{}])[0].get("finishReason")
    raise RuntimeError(
        f"Gemini returned no image (finishReason={finish}) — the prompt "
        "may have been refused; reword and re-render")
