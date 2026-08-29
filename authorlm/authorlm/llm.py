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

import copy
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
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

# ------------------------------------------------------------ drafting path
#
# `[writing]` (config.toml) governs `authorlm write draft` — the one call in
# this system that writes the author's prose (design-write-draft.md §2). It
# deliberately does NOT fall back to `[llm] model`: see WRITING_ABSENT below.

WRITING_DEFAULTS = {
    "max_tokens": 8000,
    "effort": "high",
    "timeout_seconds": 600,
    "cache": True,
}


class DraftError(RuntimeError):
    """Any reason `draft()` produced no beat. Unlike `complete()`, which
    returns None and lets the caller reason without the LLM, the drafting
    path has nothing to fall back on: a silent None here means no beat and
    a verb that printed a warning and did nothing (design §0 F1)."""


class LLMUnavailable(DraftError):
    """Disabled, unconfigured, or unreachable after retries — carrying the
    provider's own last message."""


class LLMRefused(DraftError):
    """The model declined to answer (Anthropic `stop_reason: refusal`,
    which litellm maps to `finish_reason: content_filter`). HTTP 200, so
    it must never be mistaken for an empty beat (design risk R-c)."""


class LLMTruncated(DraftError):
    """The reply hit `max_tokens` (`finish_reason: length`). A truncated
    beat is not a short beat — registering it would append half a
    sentence to the manuscript."""


@dataclass(frozen=True)
class DraftResult:
    text: str
    prompt_tokens: int          # NET of cached tokens — see draft()
    completion_tokens: int
    cache_read: int
    cache_write: int
    finish_reason: str
    model: str


WRITING_ABSENT = (
    "no [writing] section in {config} — beat drafting needs its own model,\n"
    "and it deliberately does not fall back to [llm] model (that is\n"
    "'gemini/gemini-2.5-flash', which is not what you want writing your "
    "prose).\nAdd:\n\n"
    '    [writing]\n    model = "anthropic/claude-fable-5"\n\n'
    "and put ANTHROPIC_API_KEY in the .env beside it. Or draft the beat\n"
    "yourself and register it with 'write propose --why …', which still "
    "works.")


def writing_llm(config: dict) -> LLMClient:
    """The drafting client: `[llm]`'s transport, `[writing]`'s model.

    Constructs the ordinary client (provider, base_url, cache_dir all come
    from `[llm]`) and then points it at the writing model, its own
    timeout, and its own max_tokens/effort/cache knobs.

    The key follows the MODEL, not the section: `LLMClient.api_key` is a
    property resolving `vendor_key(self.model, …)` on every access, so
    reassigning `.model` here is sufficient and the writing vendor's own
    variable is what gets sent (llm.py's api_key docstring; the bug
    `generate_image` avoided by resolving its own key).

    Raises LookupError when `[writing]` is absent or its model empty. The
    KEY refusal is not here: it lives in `draft()`, after the replay-cache
    check, because a replayed draft needs no key at all (a recorded
    fixture is the whole point of the record/replay suite). It still fires
    before any live call."""
    from . import paths

    writing = config.get("writing", {}) or {}
    model = (writing.get("model") or "").strip()
    if not model:
        raise LookupError(WRITING_ABSENT.format(config=paths.config_path()))
    client = LLMClient(config)
    client.model = model
    client.timeout = writing.get("timeout_seconds",
                                 WRITING_DEFAULTS["timeout_seconds"])
    client.max_tokens = writing.get("max_tokens",
                                    WRITING_DEFAULTS["max_tokens"])
    client.effort = writing.get("effort", WRITING_DEFAULTS["effort"])
    client.cache = bool(writing.get("cache", WRITING_DEFAULTS["cache"]))
    return client


def _without_cache_control(messages: list[dict]) -> list[dict]:
    """`messages` with every block-level `cache_control` key removed.

    The record/replay key is computed over the block TEXT only. Without
    this, flipping `[writing] cache` would re-record every replay fixture
    and §4's kill switch would be unusable in a test (design §5 change 4).
    A no-op for plain string content, so existing fixtures keep their
    hashes."""
    if not any(isinstance(m.get("content"), list) for m in messages):
        return messages
    stripped = copy.deepcopy(messages)
    for message in stripped:
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    block.pop("cache_control", None)
    return stripped


CACHE_CONTROL = {"type": "ephemeral", "ttl": "1h"}


def _block_messages(system_blocks: list[str], user_blocks: list[str],
                    caching: bool) -> list[dict]:
    """The four-block drafting payload as litellm messages.

    Two of the four available breakpoints, both `ttl: 1h`, on the two
    stable layers: the end of the system block (prompt file + style law)
    and the end of the first user block (the chapter frame). The accepted
    text and the beat-local layer follow uncached (design §4.1, D1 —
    the rolling tail is reserved, not built).

    Four content blocks in total, always, so §4's "a breakpoint looks
    back at most 20 content blocks" is a structural guarantee here rather
    than a discipline."""
    system = [{"type": "text", "text": text} for text in system_blocks]
    user = [{"type": "text", "text": text} for text in user_blocks]
    if caching:
        if system:
            system[-1]["cache_control"] = dict(CACHE_CONTROL)
        if user:
            user[0]["cache_control"] = dict(CACHE_CONTROL)
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def _usage_int(holder, name: str) -> int:
    """One usage counter off litellm's object (or a plain dict), 0 when
    absent — every provider populates a different subset."""
    if holder is None:
        return 0
    value = (holder.get(name) if isinstance(holder, dict)
             else getattr(holder, name, 0))
    return int(value or 0)


def caching_available(model: str, provider: str) -> bool:
    """Whether `cache_control` breakpoints are worth attaching.

    Three conditions, all structural. The transport must be litellm (the
    raw-HTTP path has no way to express a content block, and it is what
    every hermetic test uses). The vendor must be anthropic — that is the
    litellm surface whose block-level `cache_control` is copied verbatim
    into the provider request. And the model's own cost map must
    advertise prompt caching."""
    if provider != "litellm" or vendor_of(model) != "anthropic":
        return False
    try:
        import litellm
        from litellm.utils import supports_prompt_caching

        litellm.suppress_debug_info = True
        return bool(supports_prompt_caching(model=model))
    except Exception:
        # An unknown model, or no litellm at all. Not an error: it means
        # "no caching here", and drafting works without it.
        return False


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
        # Drafting path only (`draft()`), so the ordinary stats line is
        # unchanged for every other caller. litellm's Anthropic transport
        # folds cache tokens INTO prompt_tokens, so a perfectly cached
        # beat would otherwise read as a full re-read (design §1.8).
        self.draft_calls = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        # `[writing]` knobs, set by writing_llm(); harmless defaults here
        # so a plain client never has to be special-cased.
        self.max_tokens = WRITING_DEFAULTS["max_tokens"]
        self.effort = WRITING_DEFAULTS["effort"]
        self.cache = bool(WRITING_DEFAULTS["cache"])

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
        core = (f"LLM: {self.live_calls} live call(s) "
                f"({self.input_tokens:,} in / {self.output_tokens:,} out "
                f"tokens")
        if self.draft_calls:
            # Always printed on the drafting path, zeros included: §4's
            # verification clause is only trustworthy if the author sees
            # the number every time rather than only when it is good.
            core += (f"; cache {self.cache_read_tokens:,} read / "
                     f"{self.cache_write_tokens:,} written")
        core += ")"
        parts = [core]
        if self.replays:
            parts.append(f"{self.replays} replayed from cache")
        if self.draft_calls:
            parts.append(f"model {self.model}")
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
            {"model": self.model, "temperature": TEMPERATURE,
             "messages": _without_cache_control(messages)},
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

    # ------------------------------------------------------------- drafting

    def require_writing_key(self) -> None:
        """Refuse a LIVE drafting call with no usable key, naming the exact
        variable and the exact .env path.

        The condition is resolved through `vendor_key` — the same resolver
        the request itself uses — rather than by reading the vendor's
        environment variable directly. `[llm] api_key_env` wins there (an
        OpenAI-compatible proxy uses an arbitrary token no convention can
        derive), so checking the vendor variable alone would falsely refuse
        a perfectly configured proxy that had just been asked for an
        `anthropic/` model string.

        The guard still only fires for a vendor prefix this module knows:
        an unlisted prefix means "let litellm resolve it", which is correct
        for credential-file vendors (vertex_ai, bedrock)."""
        from . import paths

        llm_cfg = self.config.get("llm", {}) or {}
        vendor_var = VENDOR_KEY_ENV.get(vendor_of(self.model), "")
        if not vendor_var or vendor_key(self.model, llm_cfg):
            return
        named = llm_cfg.get("api_key_env") or vendor_var
        raise LookupError(
            f"[writing] model is '{self.model}', which needs {named} — it "
            f"is not set. Add it to {paths.env_path()} (one variable per "
            f"vendor; see VENDOR_KEY_ENV in llm.py).")

    def draft(self, system_blocks: list[str], user_blocks: list[str], *,
              max_tokens: int | None = None, effort: str | None = None,
              cache: bool | None = None) -> DraftResult:
        """One beat of the author's prose. Differs from `complete()` in
        exactly four ways and no others (design §1.4):

        - content BLOCKS rather than two strings, so `cache_control` has
          somewhere to attach;
        - `max_tokens`, `reasoning_effort` and `timeout` from `[writing]`;
        - NO sampling parameter at all. The Claude 5 family accepts only
          `temperature=1` and `[llm]`'s 0.2 is dropped rather than sent
          (risk R-e: relying on the drop would also silently drop
          `output_config` for a model whose map lacks it — so this path
          never passes one);
        - it RAISES instead of returning None, carrying the provider's
          own last message (design §0 F1).

        Everything else is reused: the retry/backoff loop, the
        record/replay `cache_dir`, the usage accounting.

        `prompt_tokens` on the result is NET of cached tokens: litellm's
        Anthropic path sets prompt_tokens = raw + cache_creation +
        cache_read, so reporting it raw would make a perfectly cached
        beat look like a full re-read."""
        if not self.enabled:
            raise LLMUnavailable(
                "the LLM is disabled ([llm] enabled = false) — beat drafting "
                "is the one call that cannot degrade to a heuristic. Enable "
                "it, or draft the beat yourself and register it with "
                "'write propose --why …'.")
        max_tokens = self.max_tokens if max_tokens is None else max_tokens
        effort = self.effort if effort is None else effort
        use_cache = self.cache if cache is None else cache
        caching = bool(use_cache) and caching_available(self.model,
                                                        self.provider)
        if self.provider == "litellm":
            messages = _block_messages(system_blocks, user_blocks, caching)
        else:
            # The raw-HTTP path has no way to express a content block, so
            # the blocks flatten to two strings and no cache_control is
            # emitted. This is what every hermetic test uses, which is why
            # no test depends on caching being available.
            messages = [
                {"role": "system", "content": "\n\n".join(system_blocks)},
                {"role": "user", "content": "\n\n".join(user_blocks)},
            ]
        cache_path = self._cache_path(messages)
        if cache_path and cache_path.exists():
            self.replays += 1
            # A replayed draft still reports itself. Without this the
            # usage line would drop its cache clause and its model name
            # on exactly the runs the replay suite is made of, and the
            # author would see a different report for the same work.
            self.draft_calls += 1
            stored = json.loads(cache_path.read_text())
            usage = stored.get("usage", {})
            return DraftResult(
                text=stored["response"],
                prompt_tokens=usage.get("input_tokens", 0),
                completion_tokens=usage.get("output_tokens", 0),
                cache_read=usage.get("cache_read_tokens", 0),
                cache_write=usage.get("cache_write_tokens", 0),
                finish_reason=stored.get("finish_reason", "stop"),
                model=self.model)
        # Past the replay cache, so this WILL be a live call. The key gate
        # sits exactly here: a fixture needs no key, a request does.
        self.require_writing_key()
        if self.provider == "litellm":
            text, raw_in, out, cache_read, cache_write, finish = \
                self._draft_litellm(messages, max_tokens, effort)
        else:
            text, raw_in, out, cache_read, cache_write, finish = \
                self._draft_openai(messages, max_tokens)
        net_in = max(0, raw_in - cache_read - cache_write)
        self.draft_calls += 1
        self._record_usage(net_in, out)
        self.cache_read_tokens += cache_read
        self.cache_write_tokens += cache_write
        if finish == "content_filter":
            raise LLMRefused(
                f"{self.model} refused to answer (stop_reason: refusal). "
                f"Nothing was registered. The beat's subject matter, or the "
                f"context around it, tripped the provider's own guard.")
        if finish == "length":
            raise LLMTruncated(
                f"{self.model} hit the {max_tokens:,}-token ceiling before "
                f"finishing (finish_reason: length). A truncated beat is not "
                f"a short beat, so nothing was registered — raise "
                f"[writing] max_tokens, or narrow the beat's budget.")
        if not (text or "").strip():
            raise LLMUnavailable(
                f"{self.model} returned an empty reply (finish_reason: "
                f"{finish}). Nothing was registered.")
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(
                {
                    "model": self.model,
                    "temperature": TEMPERATURE,
                    "messages": _without_cache_control(messages),
                    "response": text,
                    "finish_reason": finish,
                    "usage": {
                        "input_tokens": net_in,
                        "output_tokens": out,
                        "cache_read_tokens": cache_read,
                        "cache_write_tokens": cache_write,
                    },
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            ))
        return DraftResult(text=text, prompt_tokens=net_in,
                           completion_tokens=out, cache_read=cache_read,
                           cache_write=cache_write, finish_reason=finish,
                           model=self.model)

    def _draft_litellm(self, messages: list[dict], max_tokens: int,
                       effort: str) -> tuple[str, int, int, int, int, str]:
        try:
            import litellm
        except ImportError as err:
            raise LLMUnavailable(
                "provider 'litellm' is configured but the package is not "
                "installed (pip install litellm)") from err
        litellm.suppress_debug_info = True
        last = ""
        for attempt in range(RETRIES + 1):
            try:
                response = litellm.completion(
                    model=self.model,
                    messages=messages,
                    max_tokens=max_tokens,
                    # litellm emits BOTH thinking:{"type":"adaptive"} and
                    # output_config:{"effort": …} for the Claude 5 family
                    # from this one parameter. Never hand-roll
                    # thinking:{"budget_tokens": N} — Fable 5 rejects it.
                    **({"reasoning_effort": effort} if effort else {}),
                    timeout=self.timeout,
                    **({"api_key": self.api_key} if self.api_key else {}),
                )
                choice = response.choices[0]
                usage = getattr(response, "usage", None)
                details = getattr(usage, "prompt_tokens_details", None)
                return (
                    choice.message.content or "",
                    _usage_int(usage, "prompt_tokens"),
                    _usage_int(usage, "completion_tokens"),
                    _usage_int(details, "cached_tokens"),
                    _usage_int(details, "cache_creation_tokens"),
                    getattr(choice, "finish_reason", "stop") or "stop",
                )
            except Exception as err:  # litellm raises provider-specific types
                last = str(err).strip()
                if attempt < RETRIES:
                    delay = 2 ** attempt
                    print(f"warning: drafting call failed ({last}); retrying "
                          f"in {delay}s ({attempt + 1}/{RETRIES})…",
                          file=sys.stderr)
                    time.sleep(delay)
                    continue
        raise LLMUnavailable(
            f"{self.model} failed after {RETRIES + 1} attempts: {last[:400]}")

    def _draft_openai(self, messages: list[dict], max_tokens: int
                      ) -> tuple[str, int, int, int, int, str]:
        payload = {"model": self.model, "messages": messages,
                   "max_tokens": max_tokens}
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {self.api_key}"}
                   if self.api_key else {}),
            },
        )
        last = ""
        for attempt in range(RETRIES + 1):
            try:
                with urllib.request.urlopen(request,
                                            timeout=self.timeout) as response:
                    body = json.loads(response.read().decode())
                usage = body.get("usage", {})
                choice = body["choices"][0]
                details = usage.get("prompt_tokens_details", {}) or {}
                return (
                    choice["message"]["content"] or "",
                    usage.get("prompt_tokens", 0) or 0,
                    usage.get("completion_tokens", 0) or 0,
                    details.get("cached_tokens", 0) or 0,
                    details.get("cache_creation_tokens", 0) or 0,
                    choice.get("finish_reason") or "stop",
                )
            except (urllib.error.URLError, KeyError, json.JSONDecodeError,
                    TimeoutError) as err:
                last = str(err).strip()
                if attempt < RETRIES:
                    delay = 2 ** attempt
                    print(f"warning: drafting call failed ({last}); retrying "
                          f"in {delay}s ({attempt + 1}/{RETRIES})…",
                          file=sys.stderr)
                    time.sleep(delay)
                    continue
        raise LLMUnavailable(
            f"{self.model} failed after {RETRIES + 1} attempts: {last[:400]}")

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
