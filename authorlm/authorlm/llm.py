"""Optional reasoning backend (RFC AuthorLM §23.2).

Configured in the project's config.toml (authorlm/paths.py); keys never
appear there — they live in .env and reach this module as environment
variables.

**The transport is llmlm's** (nagarkar/llmlm#1): LiteLLM in process, the
retry rule (a 429 or 529, bounded), the verified model profiles, the price
cache, the replay cache and the strict cache key all live in
`llmlm.llm.Client`. This module keeps what is authorlm's own: the config
sections (`[llm]`, `[writing]`, `[filtering]`), the graceful degradation
(`complete()` returns None; `draft()` raises), the content-block drafting
payload, the replay road's `ReplayMiss`, and the usage counters its verbs
print. Nothing here sends a request.

**Choosing a model is choosing a vendor.** LiteLLM's convention is that
the vendor is the model string's prefix, and each vendor reads its own
standard environment variable — so there is no separate `vendor` field to
keep in sync, and switching vendors is a one-line config change:

      model = "gemini/gemini-2.5-flash"    → GEMINI_API_KEY
      model = "openai/gpt-4o-mini"         → OPENAI_API_KEY
      model = "anthropic/claude-sonnet-5"  → ANTHROPIC_API_KEY

Two transports, selected by `provider`:

- "litellm" (default): routes through llmlm in-process, so any model
  LiteLLM supports works. Authentication is LiteLLM's job — it reads the
  vendor's env var itself, which also covers vendors whose auth is not a
  bare key (Vertex/Bedrock credentials).

      [llm]
      enabled = true
      model = "gemini/gemini-2.5-flash"

- "openai": any OpenAI-compatible HTTP endpoint (LiteLLM proxy, Ollama,
  LM Studio), reached through llmlm as `openai/<model>` with `api_base`.
  The bearer token is ours to send, and `api_key_env` names the variable
  holding it (such endpoints use arbitrary tokens, so it cannot be
  derived):

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

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from llmlm import llm as shared
from llmlm.llm import (
    ANY,
    MODEL_PROFILES,
    UNKNOWN_PROFILE,
    VENDOR_DEFAULT_ONLY,
    ModelProfile,
    messages_sha256,
    without_cache_control,
)

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


# ------------------------------------------------------- model profiles
#
# The table is llmlm's (`llmlm.llm.MODEL_PROFILES`): a row per model whose
# request contract was verified live or by litellm's own map, never a
# glob. AuthorLM's rows moved there with this transport; add a new one
# there, with its comment block. What stays here is the dated-release
# rule — "anthropic/claude-sonnet-5-20260305" picks up its family's row —
# because authorlm's configs name dated models; llmlm's own lookup is
# exact, and a sibling model ("openai/gpt-5.6-terra" beside the
# "openai/gpt-5.6" and "-luna" rows) is never assumed to share a contract.

DATED = re.compile(r"-\d{8}$")


def model_profile(model: str) -> ModelProfile:
    """The profile for `model` — its own row, else its family's when it is a
    dated release (a trailing -YYYYMMDD), else UNKNOWN_PROFILE (the plain
    request: no temperature, no JSON mode)."""
    for candidate in (model, DATED.sub("", model)):
        for pattern, profile in MODEL_PROFILES:
            if candidate == pattern:
                return profile
    return UNKNOWN_PROFILE


UNKNOWN_MODEL_NOTE = (
    "note: no model profile for '{model}' — sending the plain request "
    "(no temperature, no JSON mode); a row in MODEL_PROFILES "
    "(llmlm/llm.py) says what the model accepts.")

# Keys already announced this process. A gap is a maintenance fact, not a
# per-call event: saying it once is informative, saying it on every call
# of a 24-essay rebuild is noise the author learns to scroll past.
_NOTED: set[str] = set()


def _note_once(key: str, message: str) -> None:
    if key in _NOTED:
        return
    _NOTED.add(key)
    print(message, file=sys.stderr)


TEMPERATURE = 0.2
# A model that wants NO temperature parameter at all (the Claude 5
# family, gpt-5.6-luna) has no numeric spelling for that — "the vendor's
# own default" is not a number this module can name. This string, in
# either config tier `resolve_temperature` reads, means exactly that:
# omit the `temperature` kwarg from the call entirely.
VENDOR_DEFAULT_TEMPERATURE = "vendor-default"

def resolve_temperature(config: dict, section: str):
    """The `temperature` sampling parameter for a call made on behalf of
    `section` (e.g. "critique" for summarizer_llm/editor_llm): `section`'s
    own `temperature` wins when set, falling back to `[llm]`'s, falling
    back to the historical TEMPERATURE constant (0.2) when NEITHER
    configures one — an unconfigured setup keeps sending exactly what it
    always sent, so its replay cache fixtures are untouched by this
    feature. AE-3, Sponsor-ruled ("Can we put these additional parameters
    in the config as well? temperature for instance."): deliberately NOT
    a general params-passthrough table (top_p, penalties, ...) — that
    invites unvalidated junk reaching providers; `temperature` earns a
    config key because two live model failures (rounds 1 and 3 of this
    same stream) turned on it specifically. Add another purpose-specific
    knob, the way `[writing]` already does, if a purpose actually needs
    one — not a generic passthrough."""
    llm = config.get("llm", {}) or {}
    scoped = config.get(section, {}) or {}
    if "temperature" in scoped:
        return scoped["temperature"]
    return llm.get("temperature", TEMPERATURE)

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


class ReplayMiss(LookupError):
    """A record/replay client (`cache_dir` set) has no recording for this
    exact request and no key to make one. Raised rather than returned as
    None: on the replay road a None quietly ran the no-LLM heuristic and
    the suite failed its assertion instead of saying what was missing.
    A LookupError, like `require_writing_key`'s, so it names the variable
    and the .env path the same way."""


# Every recording a record/replay client looked up this process, hit or
# miss — what the live suite keeps when it prunes (a recording no full run
# asks for is stale). REPLAY_MISSES holds the misses that had no key,
# so a caller that swallows ReplayMiss still cannot hide one.
TOUCHED_RECORDINGS: set[Path] = set()
REPLAY_MISSES: list[str] = []


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
    client.purpose = "writing"
    client.timeout = writing.get("timeout_seconds",
                                 WRITING_DEFAULTS["timeout_seconds"])
    client.max_tokens = writing.get("max_tokens",
                                    WRITING_DEFAULTS["max_tokens"])
    client.effort = writing.get("effort", WRITING_DEFAULTS["effort"])
    client.cache = bool(writing.get("cache", WRITING_DEFAULTS["cache"]))
    return client


# ------------------------------------------------------------ filtering path
#
# `[filtering]` (config.toml) governs `authorlm filter run --native` — the
# billed path of the filter pass (docs/filter-pass-design.md §3.4). It ships
# ABSENT, the third instance of the pattern `[writing]` and `[budget]`
# established: what is absent is what is off.

FILTERING_DEFAULTS = {
    "max_tokens": 4000,
    "timeout_seconds": 300,
    "cache": True,
}

FILTERING_ABSENT = (
    "no [filtering] section in {config} — the billed filter path needs its\n"
    "own model, and it deliberately does not fall back to [llm] model (a\n"
    "silent degradation to a different tier is a result you would attribute\n"
    "to the loop rather than to a line of configuration you never wrote).\n\n"
    "You almost certainly do not need it. `authorlm filter run <name> <file>`\n"
    "with NO flag makes no model call at all: it prints the payload and the\n"
    "conversation drafts the reply against it, which is the whole point of\n"
    "the filter pass. That is the default and it still works.\n\n"
    "To enable billed, model-pinned filtering, add:\n\n"
    '    [filtering]\n    model = "openai/gpt-5.6-luna"\n\n'
    "and put that vendor's key in the .env beside it.")


def filtering_llm(config: dict) -> LLMClient:
    """The filter client: `[llm]`'s transport, `[filtering]`'s model.

    Mirrors `writing_llm` exactly, including the key-follows-the-model
    property (`LLMClient.api_key` resolves `vendor_key(self.model, …)` on
    every access, so reassigning `.model` is sufficient).

    The recommended model is the CHEAP tier, which is the opposite of the
    drafting ruling, and deliberately: drafting's quality comes from the
    model's judgment, so economy there comes from caching and effort, not
    from a smaller model. A filter's quality comes from the assembled
    context and a prompt that fully specifies the check — the sweep
    framework's own criterion for running natively on the flash tier. A
    filter that needs a mind rather than a rule is a LENS."""
    from . import paths

    filtering = config.get("filtering", {}) or {}
    model = (filtering.get("model") or "").strip()
    if not model:
        raise LookupError(FILTERING_ABSENT.format(config=paths.config_path()))
    client = LLMClient(config)
    client.model = model
    client.purpose = "filtering"          # the usage ledger's key (§15.18)
    client.timeout = filtering.get("timeout_seconds",
                                   FILTERING_DEFAULTS["timeout_seconds"])
    client.max_tokens = filtering.get("max_tokens",
                                      FILTERING_DEFAULTS["max_tokens"])
    client.cache = bool(filtering.get("cache", FILTERING_DEFAULTS["cache"]))
    return client



_without_cache_control = without_cache_control

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


def caching_available(model: str, provider: str) -> bool:
    """Whether `cache_control` breakpoints are worth attaching.

    The transport must be litellm — the raw-HTTP path has no way to
    express a content block, and it is what every hermetic test uses.
    Past that, the model's PROFILE answers: `prompt_cache == "anthropic"`
    means block-level `cache_control` is copied verbatim into the
    provider request, which is the only surface where attaching
    breakpoints does anything.

    A model with no row keeps the pre-registry answer exactly — the
    vendor-prefix gate plus litellm's own cost map — so adding the table
    changed no model's caching behavior, only where the answer is
    written down for the ones we have verified."""
    if provider != "litellm":
        return False
    profile = model_profile(model)
    if profile is not UNKNOWN_PROFILE:
        return profile.prompt_cache == "anthropic"
    if vendor_of(model) != "anthropic":
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
        # WHICH CONFIG SECTION CHOSE THIS MODEL — the usage ledger's
        # aggregation key beside `model`, and the answer to "why am I
        # paying for Luna", which `model` alone cannot give once two
        # sections name the same string. A plain attribute, not a property:
        # unlike `api_key` and `profile()` it does not have to track a
        # `.model` reassignment — it IS the record of who did the
        # reassigning. Each factory below sets it beside its `.model` line.
        self.purpose = "llm"
        self.base_url = llm.get("base_url", "http://localhost:4000/v1").rstrip("/")
        # api_key is a property (below) — see its docstring for why it
        # cannot be resolved once here and cached.
        # AE-3: the `temperature` sampling parameter, [llm]'s own value
        # or the historical constant. `summarizer_llm`/`editor_llm`
        # (summaries.py / passes.py) reassign this — same pattern as
        # `.model` — to a `[critique]`-scoped value when one is set;
        # see `resolve_temperature`.
        self.temperature = llm.get("temperature", TEMPERATURE)
        self.timeout = llm.get("timeout_seconds", 120)
        # Cap on manuscript text sent per extraction call — cost control and
        # extraction quality (concept selection degrades on very long inputs).
        self.extraction_max_chars = llm.get("extraction_max_chars", 24000)
        # Record/replay cache (RFC §24.5 replay discipline). When set, each
        # unique payload (model + temperature + messages + knobs) is sent to
        # the LLM at most once; the reply is stored as a JSON file (llmlm's
        # cache format, `<sha256>.json`) and replayed on every subsequent
        # identical request. Any change to a prompt, source document, or
        # model re-records exactly the calls it affects. Unset, nothing
        # persists: the client keeps a scratch cache for its own lifetime.
        self.cache_dir = llm.get("cache_dir")
        # Usage accounting, reported after every step that touches the LLM.
        self.live_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.replays = 0
        # Drafting path only (`draft()`), so the ordinary stats line is
        # unchanged for every other caller. litellm's Anthropic transport
        # folds cache tokens INTO prompt_tokens; llmlm hands them back net,
        # with the cache counters beside, so a perfectly cached beat does
        # not read as a full re-read (design §1.8).
        self.draft_calls = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        # `[writing]` knobs, set by writing_llm(); harmless defaults here
        # so a plain client never has to be special-cased.
        self.max_tokens = WRITING_DEFAULTS["max_tokens"]
        self.effort = WRITING_DEFAULTS["effort"]
        self.cache = bool(WRITING_DEFAULTS["cache"])
        self._shared_client: shared.Client | None = None
        self._scratch: tempfile.TemporaryDirectory | None = None
        self._scratch_calls = 0
        # One quiet line on first construction of a client whose model
        # has no row (never a refusal — an unknown model still works).
        self.profile()

    def profile(self) -> ModelProfile:
        """The MODEL_PROFILES row for this client's CURRENT model.

        Resolved fresh on every call, never cached, for the same reason
        `api_key` is: `summarizer_llm`/`editor_llm`/`writing_llm` build a
        client from `[llm]` and then reassign `.model`, so a profile
        pinned at __init__ time would shape the request for the wrong
        model. Announces a missing row once per model per process."""
        profile = model_profile(self.model)
        if profile is UNKNOWN_PROFILE and self.enabled:
            _note_once(f"unknown-model:{self.model}",
                       UNKNOWN_MODEL_NOTE.format(model=self.model))
        return profile

    def send_temperature(self) -> bool:
        """Whether this call carries a `temperature` kwarg at all.

        Two independent ways to say no. The CONFIG can say so
        (`temperature = "vendor-default"`, resolve_temperature), and the
        PROFILE can say so — and the profile wins, because config cannot
        force a parameter the model is known to reject. When config
        actually asked for a number the profile overrides, that is worth
        exactly one line: it is a live discrepancy between what is
        written down and what the model accepts, and silence there is how
        a config grows settings that never had any effect."""
        profile = self.profile()
        if profile.temperature == VENDOR_DEFAULT_ONLY:
            if self.temperature != VENDOR_DEFAULT_TEMPERATURE and profile is not UNKNOWN_PROFILE:
                _note_once(
                    f"temperature-override:{self.model}",
                    f"note: config sets temperature={self.temperature!r} for "
                    f"'{self.model}', but its model profile "
                    f"({profile.name}) says the model takes no temperature "
                    f"parameter at all — omitting it (MODEL_PROFILES, "
                    f"llmlm/llm.py).")
            return False
        return self.temperature != VENDOR_DEFAULT_TEMPERATURE

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
        API a foreign key. Keys come from the environment (.env loaded at
        config load), never from config.toml. It is passed to llmlm with
        every call, so `[llm] api_key_env` (an OpenAI-compatible proxy's
        arbitrary token) is what is sent."""
        return vendor_key(self.model, self.config.get("llm", {}))

    def stats_line(self) -> str | None:
        """One brief line of LLM usage for this step, or None if the LLM
        was never consulted. Matches the per-verb usage-reporting
        convention gamelm/supplylm also follow — one trailing clause on
        the verb's own output, not a separate report command — but
        prices from `usage.estimate` (litellm's live cost map) rather
        than a hand-maintained table, so it never drifts stale."""
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
        from . import usage as _usage

        cost = _usage.estimate(self.model, self.input_tokens,
                               self.output_tokens, self.cache_read_tokens,
                               self.cache_write_tokens)
        # None (no price data for this model) is a different claim from
        # $0 and must never be shown as one (usage.py's own rule) — so a
        # cost clause is added only when a real estimate exists.
        if cost is not None:
            parts.append(f"~${cost:.4f}")
        if self.replays:
            parts.append(f"{self.replays} replayed from cache")
        if self.draft_calls:
            parts.append(f"model {self.model}")
        return " — ".join(parts)

    def _record_usage(self, prompt_tokens: int, completion_tokens: int,
                      cache_read: int = 0, cache_write: int = 0) -> None:
        """THE seam. It sits BELOW `stats_line()`, below `_report_llm`,
        below the CLI and below the MCP server, which is the whole reason
        the usage ledger is small and also its coverage argument: the two
        paths the spend audit found invisible — auto-collect extraction
        and the entire MCP surface — reach the ledger anyway, because they
        reach `complete_json` → `complete` → here like everything else.
        Sixteen of the seventeen audited call sites are covered with no
        call-site edit; `generate_image` is not a client and calls
        `usage.record_image` itself."""
        from . import usage

        self.live_calls += 1
        self.input_tokens += prompt_tokens
        self.output_tokens += completion_tokens
        self.cache_read_tokens += cache_read
        self.cache_write_tokens += cache_write
        usage.record(purpose=self.purpose, model=self.model,
                     input_tokens=prompt_tokens,
                     output_tokens=completion_tokens,
                     cache_read=cache_read, cache_write=cache_write)

    # ---------------------------------------------------------- the call

    def _shared(self) -> shared.Client:
        """The llmlm client behind this one. Its own ledger and run logs are
        off or in a scratch folder: authorlm's usage ledger (usage.py) is
        what this module still feeds, and the ledger carries no prose."""
        if self._shared_client is None:
            self._scratch = tempfile.TemporaryDirectory(prefix="authorlm-llm-")
            self._shared_client = shared.Client(
                Path(self._scratch.name),
                {"logs": {"usage": False, "trace": False}},
                cache_dir=Path(self.cache_dir) if self.cache_dir else None,
                verb="authorlm",
            )
        if not self.cache_dir:
            # No record/replay: every request goes out, as it always has. A
            # fresh scratch cache per call keeps llmlm's cache from serving
            # an identical payload twice (`regenerate`, a re-run).
            self._scratch_calls += 1
            self._shared_client.cache_dir = (
                Path(self._scratch.name) / f"call-{self._scratch_calls}")
        return self._shared_client

    def _wire_model(self) -> str:
        """The model string litellm gets. An OpenAI-compatible endpoint
        (a LiteLLM proxy, Ollama) is reached as `openai/<model>`: litellm
        strips the prefix and posts the rest to `base_url`, so a proxy
        that routes `anthropic/claude-fable-5` still gets exactly that."""
        if self.provider == "litellm":
            return self.model
        return f"openai/{self.model}"

    def _knobs(self, temperature) -> dict:
        """The per-call arguments every request carries."""
        knobs: dict = {"model": self._wire_model(), "timeout": self.timeout,
                       "api_key": self.api_key or None, "temperature": temperature}
        if self.provider != "litellm":
            knobs["api_base"] = self.base_url
            # a local endpoint (Ollama, LM Studio) needs no token, but the
            # OpenAI client behind litellm insists on a string
            knobs["api_key"] = self.api_key or "none"
        return knobs

    def _cache_path(self, messages: list[dict], temperature,
                    **params) -> Path | None:
        """The recording for this exact request. `params` are the other
        knobs that change the reply (`max_tokens`, `effort`,
        `thinking_budget`): each is keyed only when set, so a call that
        sends none of them keeps the key it always had. The key is
        llmlm's (`messages_sha256`), after the model's profile has had its
        say, so it is the file the call will read and write."""
        if not self.cache_dir:
            return None
        knobs = self._knobs(temperature)
        key = self._shared().messages_hash(
            self.purpose, messages, model=knobs["model"],
            temperature=knobs["temperature"],
            max_tokens=params.get("max_tokens"),
            reasoning_effort=params.get("effort"),
            thinking_budget=params.get("thinking_budget"))
        path = Path(self.cache_dir) / f"{key}.json"
        TOUCHED_RECORDINGS.add(path)
        return path

    def _missing_key_var(self) -> str | None:
        """The variable a LIVE call to this model needs and lacks, or None.

        Resolved through `vendor_key` — the same resolver the request
        itself uses — rather than by reading the vendor's environment
        variable directly. `[llm] api_key_env` wins there (an
        OpenAI-compatible proxy uses an arbitrary token no convention can
        derive), so checking the vendor variable alone would falsely refuse
        a perfectly configured proxy that had just been asked for an
        `anthropic/` model string. An unlisted vendor prefix means "let
        litellm resolve it", which is correct for credential-file vendors
        (vertex_ai, bedrock)."""
        llm_cfg = self.config.get("llm", {}) or {}
        vendor_var = VENDOR_KEY_ENV.get(vendor_of(self.model), "")
        if not vendor_var or vendor_key(self.model, llm_cfg):
            return None
        return llm_cfg.get("api_key_env") or vendor_var

    def _call(self, messages: list[dict], *, temperature, thinking_budget=None,
              max_tokens=None, effort=None, gate=False) -> shared.Reply:
        """One request through llmlm, counted. Raises llmlm's own errors
        (`VendorError`, `TruncatedReply`, `Refused`, `SpendCapReached`) for
        the caller to degrade or raise.

        The key gate sits after the replay check and before the request: a
        recording needs no key, a live request does. On the replay road
        (`cache_dir` set) a miss with no key is one `ReplayMiss`, never a
        silent None that runs the no-LLM heuristic; with `gate` (drafting,
        which has nothing to fall back on) it is a LookupError naming the
        variable."""
        # `thinking` goes only where litellm maps it (Gemini 2.5+, Anthropic);
        # a model that cannot take it would 400 the whole call, and the
        # budget only ever tuned speed and cost.
        if self.provider != "litellm" or vendor_of(self.model) not in (
                "gemini", "anthropic"):
            thinking_budget = None
        path = self._cache_path(messages, temperature, max_tokens=max_tokens,
                                effort=effort, thinking_budget=thinking_budget)
        named = self._missing_key_var()
        if named and not (path and path.exists()):
            if path:
                from . import paths

                REPLAY_MISSES.append(path.name)
                raise ReplayMiss(
                    f"no recording {path.name} for this {self.model} request "
                    f"and {named} is not set — add it to {paths.env_path()} "
                    f"to record it.")
            if gate:
                self.require_writing_key()
        reply = self._shared().complete_messages(
            self.purpose, messages,
            tag=(path.stem[:12] if path else
                 messages_sha256(self.model, None, messages)[:12]),
            max_tokens=max_tokens, reasoning_effort=effort,
            thinking_budget=thinking_budget, **self._knobs(temperature))
        from . import usage

        if reply.cached:
            self.replays += 1
            # Served from the record/replay cache: it did NOT spend, and
            # "what did the replay cache save me" is the only positive
            # number the ledger's report has.
            usage.record_replay(self.purpose, self.model)
        else:
            self._record_usage(reply.tokens_in, reply.tokens_out,
                               reply.cache_read, reply.cache_write)
        return reply

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
        guidance may genuinely benefit)."""
        if not self.enabled:
            return None
        # The enforcement seam, at the top of a transport-entering path,
        # before any cache check and before any request is built. Returns
        # None — i.e. permits — on every shipped configuration; see
        # budget.gate's docstring.
        from . import budget

        budget.gate(self.purpose, self.model)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        # AE-3: keyed on `self.temperature` (the CONFIGURED value —
        # [critique]/[llm]/constant, per resolve_temperature). Two clients
        # on the same model but different configured temperatures must
        # never collide on one cache file.
        temperature = self.temperature if self.send_temperature() else None
        try:
            return self._call(messages, temperature=temperature,
                              thinking_budget=thinking_budget).raw
        except ReplayMiss:
            raise
        except (shared.NotConfigured, shared.VendorError, shared.TruncatedReply,
                shared.Refused, shared.SpendCapReached) as err:
            print(f"warning: LLM call failed ({err}); continuing without LLM.",
                  file=sys.stderr)
            return None

    def complete_json(self, system: str, user: str,
                      thinking_budget: int | None = None):
        """Like complete(), but parse the reply as JSON (tolerating markdown
        code fences). Returns the parsed value, or None on any failure."""
        reply = self.complete(system + " Reply with JSON only, no prose.", user,
                              thinking_budget=thinking_budget)
        if reply is None:
            return None
        return _parse_json_reply(reply)

    def complete_json_blocks(self, system_blocks: list[str],
                             user_blocks: list[str]):
        """`complete_json` over CONTENT BLOCKS — the filter pass's native
        call (filter-pass design §3.4).

        Blocks rather than two strings for exactly one reason: so
        `cache_control` has somewhere to attach. Two breakpoints, both
        `ttl: 1h`, on the two stable layers — the end of the system block
        (harness prompt + the filter + style law) and the end of the
        first user block (intents, beliefs, graph, prior runs) — which is
        the same placement `_block_messages` uses on the drafting path,
        and it is the same function, so the two cannot drift.

        Say plainly what this buys today: `caching_available` requires
        litellm transport AND a profile whose `prompt_cache` is
        "anthropic". The recipe's recommended model for `[filtering]` is
        the cheap OpenAI tier, for which it is False — so the native path
        ships with no cache behaviour at all and these breakpoints are
        dead code until someone configures an Anthropic model.

        Returns the parsed JSON, or None on any failure — `complete`'s
        contract, not `draft`'s: the filter's native path has the chat
        path to fall back to, and its caller says so."""
        if not self.enabled:
            return None
        if self.provider != "litellm":
            # An OpenAI-compatible endpoint takes the blocks flattened and
            # no cache_control, as it always has.
            return self.complete_json("\n\n".join(system_blocks),
                                      "\n\n".join(user_blocks))
        from . import budget

        budget.gate(self.purpose, self.model)
        caching = bool(self.cache) and caching_available(self.model,
                                                         self.provider)
        messages = _block_messages(system_blocks, user_blocks, caching)
        temperature = self.temperature if self.send_temperature() else None
        try:
            reply = self._call(messages, temperature=temperature).raw
        except ReplayMiss:
            raise
        except (shared.NotConfigured, shared.VendorError, shared.TruncatedReply,
                shared.Refused, shared.SpendCapReached) as err:
            print(f"warning: LLM call failed ({err}); continuing without LLM.",
                  file=sys.stderr)
            return None
        return _parse_json_reply(reply)

    # ------------------------------------------------------------- drafting

    def require_writing_key(self) -> None:
        """Refuse a LIVE drafting call with no usable key, naming the exact
        variable and the exact .env path (`_missing_key_var` says which)."""
        from . import paths

        named = self._missing_key_var()
        if named is None:
            return
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
          (risk R-e). This path never passes one: an unconditional
          omission is the stronger guarantee than consulting the profile;
        - it RAISES instead of returning None, carrying the provider's
          own last message (design §0 F1).

        Everything else is reused: llmlm's retry rule, the record/replay
        `cache_dir`, the usage accounting.

        `prompt_tokens` on the result is NET of cached tokens (llmlm's
        `tokens_in`), so a perfectly cached beat does not look like a full
        re-read."""
        if not self.enabled:
            raise LLMUnavailable(
                "the LLM is disabled ([llm] enabled = false) — beat drafting "
                "is the one call that cannot degrade to a heuristic. Enable "
                "it, or draft the beat yourself and register it with "
                "'write propose --why …'.")
        from . import budget

        budget.gate(self.purpose, self.model)
        max_tokens = self.max_tokens if max_tokens is None else max_tokens
        effort = self.effort if effort is None else effort
        use_cache = self.cache if cache is None else cache
        caching = bool(use_cache) and caching_available(self.model,
                                                        self.provider)
        if self.provider == "litellm":
            messages = _block_messages(system_blocks, user_blocks, caching)
        else:
            # An OpenAI-compatible endpoint takes the blocks flattened and
            # no cache_control, as it always has.
            messages = [
                {"role": "system", "content": "\n\n".join(system_blocks)},
                {"role": "user", "content": "\n\n".join(user_blocks)},
            ]
        # `max_tokens` and `effort` ARE keyed: either one changes the reply.
        # The reasoning effort rides only where the profile allows it.
        effort_sent = effort if (effort and self.profile().reasoning_effort) else None
        # A live request needs a key; a recording does not. llmlm checks the
        # cache first, so the key gate below only fires on a true miss.
        try:
            reply = self._call(messages, temperature=None, gate=True,
                               max_tokens=max_tokens, effort=effort_sent)
        except ReplayMiss:
            raise
        except shared.NotConfigured as err:
            raise LLMUnavailable(str(err)) from err
        except shared.Refused as err:
            raise LLMRefused(
                f"{self.model} refused to answer (stop_reason: refusal). "
                f"Nothing was registered. The beat's subject matter, or the "
                f"context around it, tripped the provider's own guard.") from err
        except shared.TruncatedReply as err:
            raise LLMTruncated(
                f"{self.model} hit the {max_tokens:,}-token ceiling before "
                f"finishing (finish_reason: length). A truncated beat is not "
                f"a short beat, so nothing was registered — raise "
                f"[writing] max_tokens, or narrow the beat's budget.") from err
        except (shared.VendorError, shared.SpendCapReached) as err:
            raise LLMUnavailable(str(err)[:400]) from err
        self.draft_calls += 1
        if not (reply.raw or "").strip():
            raise LLMUnavailable(
                f"{self.model} returned an empty reply (finish_reason: "
                f"{reply.finish_reason}). Nothing was registered.")
        return DraftResult(text=reply.raw, prompt_tokens=reply.tokens_in,
                           completion_tokens=reply.tokens_out,
                           cache_read=reply.cache_read,
                           cache_write=reply.cache_write,
                           finish_reason=reply.finish_reason,
                           model=self.model)


def _parse_json_reply(reply: str | None):
    """The reply as JSON (tolerating markdown code fences), or None with a
    warning when it is not."""
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
                   input_png: bytes | None = None,
                   references: list[bytes] | None = None,
                   model: str | None = None,
                   size: str | None = None) -> bytes:
    """One rendered image for an illustration slot. `model` and `size`
    come from the manuscript's pin when the caller has one
    (illus.resolve_pin); absent that, from `[illustrations]`, then the
    legacy `[llm] image_model`, then the default gemini flash image
    model. `input_png` is the image to edit (`--from` continuity);
    `references` are the cast plates to hold a likeness to
    (verse-and-cast-design.md §6), attached after it in the order the
    prompt's reference block names them. The render itself is llmlm's
    (`llmlm.images`): gemini/* models take the images through
    generateContent; openai/* image models through the images EDITS
    endpoint (up to 16 inputs); every other model string routes through
    litellm.image_generation, which is text-to-image only and so declines
    any input image. Unlike completions there is no silent degradation:
    rendering is an explicit act, so failures raise RuntimeError
    (`llmlm.images.ImageError`) with a readable message."""
    from llmlm import images as shared_images

    from . import budget, paths, usage

    llm = config.get("llm", {}) or {}
    model = model or resolve_image_setting(config, "model", "image_model",
                                          DEFAULT_IMAGE_MODEL)
    if size is None:
        size = resolve_image_setting(config, "image_size", "image_size", "")
    inputs = ([input_png] if input_png is not None else []) + list(
        references or [])
    # The third enforcement seam, after model resolution and before the
    # key check. Permits on every shipped configuration.
    budget.gate("illustrations", model)
    # The image model is chosen independently of the text model, so its
    # key is resolved from ITS OWN vendor prefix — the text model's key
    # is the wrong key the moment the two vendors differ.
    key = vendor_key(model, {})
    needs_key = model.startswith("gemini/") or (
        bool(inputs) and model.startswith("openai/"))
    if needs_key and not key:
        raise RuntimeError(
            "no API key for image generation — set "
            f"{VENDOR_KEY_ENV[vendor_of(model)]} in {paths.env_path()}")
    png = shared_images.generate_image(
        model, prompt, images=inputs, size=size,
        timeout=llm.get("timeout_seconds", 120), key=key)
    # The 17th audited call site: `generate_image` is not an LLMClient, so
    # it records itself, once, on success. `est_cost` is NULL for image
    # renders — the API returns no image-token count and no flat per-image
    # price, so the line carries a count instead of a number we cannot
    # derive.
    usage.record_image(purpose="illustrations", model=model, images=1)
    return png
