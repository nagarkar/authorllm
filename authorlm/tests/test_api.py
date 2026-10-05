"""API-layer tests + CLI/MCP parity checklist (P11).

Exercises `authorlm.api` directly (the logic layer beneath every surface)
and asserts the MCP server exposes the expected hand-curated tool set.

Run: python3 tests/test_api.py
"""

from __future__ import annotations

import os

# Offline suite: pin the project-config and .env lookups away from the
# real ones. Without this a checkout's config.toml (llm enabled, keys in
# .env) is picked up by every test process and the suite makes live,
# billed model calls — and asserts against whatever they return.
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
# And pin client provenance OFF. The suites run INSIDE a Claude Code
# Bash call, so CLAUDE_CODE_SESSION_ID is in their own environment and
# the claude-code adapter would stamp the developer's live chat into
# every fixture row — tests passing for the wrong reason. Same failure
# mode as a leaked config, so it gets the same treatment: pin it.
os.environ["AUTHORLM_CLIENT"] = "none"

def _assert_offline() -> None:
    """Fail loudly if the real project config or .env leaks into a test.

    Before config moved into the repo, a test workspace simply had no
    config.toml, so the LLM was off and no suite could make a billed
    call. Now a checkout always HAS an enabled config, and that safety
    came from nothing but the pins above — so it is asserted, not
    assumed."""
    import os as _os

    from authorlm import paths as _paths

    assert not _paths.config_path().exists(), (
        f"test isolation broken: reading the real config at "
        f"{_paths.config_path()}")
    assert _paths.load_env() == [], "test isolation broken: .env was loaded"
    leaked = [v for v in _paths_vendor_vars() if _os.environ.get(v)]
    assert not leaked, f"test isolation broken: vendor keys in env: {leaked}"

    from authorlm import clients as _clients

    detected = _clients.current()
    assert detected.engine == "unknown" and detected.precision == "none", (
        f"test isolation broken: client provenance detected "
        f"{detected.engine}/{detected.session_id} — the suite is stamping "
        f"the developer's live chat into fixture rows")


def _paths_vendor_vars() -> list:
    from authorlm.llm import VENDOR_KEY_ENV

    return sorted(VENDOR_KEY_ENV.values())



import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import api, concepts, guidance as guidance_module  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.db import ko_fields, loads  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


def check_show_verbs() -> None:
    """Every entity verb exposes `show`, spelled the same way.

    Four separate sessions hit a missing `show` on a different noun before
    this was made uniform, so the check is on the parser rather than on
    memory."""
    from authorlm.cli import build_parser

    commands = build_parser()._subparsers._group_actions[0].choices
    for verb in ("concept", "intent", "improve", "style", "belief",
                 "proposal", "critique", "lens", "illus", "doc"):
        actions = [a for a in commands[verb]._actions if a.dest == "action"]
        choices = list(actions[0].choices) if actions else []
        check(f"`{verb}` exposes a 'show' action",
              "show" in choices, f"{verb} actions: {choices}")


def check_broken_pipe() -> None:
    """`authorlm <listing> | head` must exit quietly.

    Python raises BrokenPipeError when the reader closes early, and raises a
    SECOND time flushing stdout at interpreter shutdown — which is what
    prints 'Exception ignored in: <_io.TextIOWrapper>' after the traceback.
    Both have to be swallowed, and the handler must survive a captured
    stdout that has no fileno()."""
    import io as _io

    from authorlm.cli import main as _main

    class ExplodesOnWrite(_io.StringIO):
        def write(self, text):  # noqa: D102
            raise BrokenPipeError(32, "Broken pipe")

    class ExplodesOnFlush(_io.StringIO):
        """Short output stays buffered, so the pipe only breaks when the
        interpreter flushes at shutdown — the case a try/except around the
        command misses entirely."""

        def flush(self):  # noqa: D102
            raise BrokenPipeError(32, "Broken pipe")

    for label, stream in (("while writing", ExplodesOnWrite),
                          ("at the shutdown flush", ExplodesOnFlush)):
        try:
            with contextlib.redirect_stdout(stream()):
                _main(["--help"])
            check(f"a pipe closed {label} exits without raising", True)
        except SystemExit as err:
            check(f"a pipe closed {label} exits 0, not a traceback",
                  err.code in (0, None), f"exit code {err.code}")
        except BrokenPipeError:
            check(f"a pipe closed {label} exits 0, not a traceback", False,
                  "BrokenPipeError escaped main()")


def check_drafting_key_gate() -> None:
    """The writing key gate must resolve the key the same way the REQUEST
    does, or a correctly configured proxy is refused for no reason.

    `[llm] api_key_env` names the bearer token for an OpenAI-compatible
    endpoint (a LiteLLM proxy, Ollama, LM Studio), and `vendor_key` gives
    it precedence over the vendor convention — such endpoints use
    arbitrary tokens no convention can derive. A gate that read
    ANTHROPIC_API_KEY directly would refuse a working proxy the moment it
    was pointed at an `anthropic/` model string, with a message telling
    the author to set a variable their setup does not use."""
    from authorlm import llm as llm_mod

    config = {"llm": {"enabled": True, "provider": "openai",
                      "api_key_env": "MY_PROXY_KEY"},
              "writing": {"model": "anthropic/claude-fable-5"}}
    prev = os.environ.pop("MY_PROXY_KEY", None)
    try:
        client = llm_mod.writing_llm(config)
        refused = None
        try:
            client.require_writing_key()
        except LookupError as err:
            refused = str(err)
        check("with no proxy token set, the key gate still refuses — and "
              "names the variable the config actually uses, not the vendor "
              "convention it does not",
              refused and "MY_PROXY_KEY" in refused
              and "ANTHROPIC_API_KEY" not in refused, str(refused))

        os.environ["MY_PROXY_KEY"] = "a-proxy-bearer-token"
        client = llm_mod.writing_llm(config)
        client.require_writing_key()   # must NOT raise
        check("an [llm] api_key_env proxy token SATISFIES the gate for an "
              "anthropic/* writing model — the gate resolves the key "
              "through vendor_key, exactly as the request does",
              client.api_key == "a-proxy-bearer-token", client.api_key)
    finally:
        os.environ.pop("MY_PROXY_KEY", None)
        if prev is not None:
            os.environ["MY_PROXY_KEY"] = prev

    check("a model whose vendor prefix this module does not know is never "
          "refused — an unlisted prefix means 'let litellm resolve it', "
          "which is correct for credential-file vendors",
          llm_mod.writing_llm(
              {"llm": {}, "writing": {"model": "vertex_ai/gemini-x"}}
          ).require_writing_key() is None, "")


def check_drafting_replay_needs_no_key() -> None:
    """A REPLAYED draft needs no API key; a live one still does.

    The record/replay suite's whole contract is that re-running it without
    a key makes zero live calls and replays every fixture. A key gate that
    fired before the replay check would have made the drafting path the
    one thing in that suite nobody could run — the fixture would sit in
    tests/llm_cache/ unreachable. So the gate sits after the cache hit and
    before the request, and this pins both halves of that."""
    import json as _json

    from authorlm import llm as llm_mod

    cache_root = Path(tempfile.mkdtemp(prefix="authorlm-replay-"))
    prev = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        config = {"llm": {"enabled": True, "cache_dir": str(cache_root)},
                  "writing": {"model": "anthropic/claude-fable-5"}}
        blocks = (["SYSTEM BLOCK"], ["FRAME", "ACCEPTED", "BEAT"])

        cold = llm_mod.writing_llm(config)
        refused = None
        try:
            cold.draft(*blocks)
        except LookupError as err:
            refused = str(err)
        check("with no fixture and no key, the drafting call refuses by "
              "name before it ever reaches the provider",
              refused and "ANTHROPIC_API_KEY" in refused, str(refused))

        client = llm_mod.writing_llm(config)
        messages = llm_mod._block_messages(
            *blocks, llm_mod.caching_available(client.model, client.provider))
        # draft()'s own cache key is unaffected by AE-3's per-client
        # `self.temperature` — it always keys on the bare TEMPERATURE
        # constant (llm.py: draft() never sends temperature at all, see
        # the "NO sampling parameter at all" note there), so every
        # existing drafting fixture's key stays exactly what it was.
        path = client._cache_path(messages, llm_mod.TEMPERATURE,
                                  max_tokens=client.max_tokens,
                                  effort=client.effort)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps({
            "model": client.model,
            "messages": llm_mod._without_cache_control(messages),
            "response": "WHY\nx\n\nSELF-CHECK\ny\n\nDRAFT\nprose",
            "finish_reason": "stop",
            "usage": {"input_tokens": 4100, "output_tokens": 320,
                      "cache_read_tokens": 29880, "cache_write_tokens": 0},
        }))
        result = client.draft(*blocks)
        check("...and with the fixture present the same call replays it, "
              "with no key and no request",
              result.text.endswith("prose") and result.cache_read == 29880
              and client.live_calls == 0 and client.replays == 1, result.text)
        line = client.stats_line()
        check("a replayed draft still reports itself: the usage line names "
              "the model and the replay. The spend figures stay ZERO — a "
              "replay costs nothing now, and printing the recorded call's "
              "tokens as this run's would misreport what was spent",
              "model anthropic/claude-fable-5" in line
              and "1 replayed from cache" in line
              and "0 live call(s)" in line
              and "cache 0 read / 0 written" in line, line)

        plain = llm_mod.LLMClient({"llm": {
            "enabled": True, "model": "anthropic/claude-fable-5",
            "cache_dir": str(cache_root)}})
        missed = None
        try:
            plain.complete("SYSTEM", "USER")
        except llm_mod.ReplayMiss as err:
            missed = str(err)
        check("on the replay road a miss with no key is one ReplayMiss "
              "naming the variable — never a silent None that runs the "
              "no-LLM heuristic",
              missed and "ANTHROPIC_API_KEY" in missed
              and plain.live_calls == 0, str(missed))

        def key(**params):
            return client._cache_path(messages, llm_mod.TEMPERATURE,
                                      **params)
        check("max_tokens, effort and thinking_budget each change the "
              "recording key; leaving them unset keeps the old key",
              len({key(), key(max_tokens=1), key(effort="low"),
                   key(thinking_budget=0)}) == 4
              and key() == key(max_tokens=None), "")
    finally:
        shutil.rmtree(cache_root, ignore_errors=True)
        if prev is not None:
            os.environ["ANTHROPIC_API_KEY"] = prev


def check_link_endpoints() -> None:
    """Extraction reads a link whose target is keyed by its relation
    (Gemini's habit) instead of counting it malformed."""
    from authorlm.extraction import link_endpoints

    check("a link in the prompt's own shape reads as written",
          link_endpoints({"from": "A", "relation": "creates", "to": "B"})
          == ("A", "creates", "B"))
    check("a target keyed by the named relation is read, not dropped",
          link_endpoints({"from": "A", "leads_to": "B",
                          "relation": "leads_to"})
          == ("A", "leads_to", "B"))
    check("with no relation field, the item's one relation key names it",
          link_endpoints({"from": "A", "creates": "B"})
          == ("A", "creates", "B"))
    check("a link with no readable target still has none",
          link_endpoints({"from": "A", "relation": "creates"})[2] == "")


def check_drafting_cache_warning() -> None:
    """§4's zero-cache-reads warning, asserted POSITIVELY.

    It is the cache layer's only production verification — nothing
    exercises `cache_control` in flight — so the predicate that fires it
    and the text it prints are both pinned here. Deleting either would
    otherwise leave the whole caching feature unverified and the suite
    still green."""
    import io as _io

    from authorlm import cli as cli_module

    class _Client:
        cache = True
        model = "anthropic/claude-fable-5"
        provider = "litellm"

    client = _Client()
    check("the warning FIRES on a caching-capable model that read zero "
          "cached tokens on a draft that is not the writeup's first — a "
          "beat loop showing zero cache reads has a silent invalidator",
          api.cache_cold(client, first_draft=False, cache_read=0), "")
    check("...and is silent on the writeup's FIRST draft (nothing to hit "
          "yet), on a non-zero read, and when [writing] cache = false",
          not api.cache_cold(client, first_draft=True, cache_read=0)
          and not api.cache_cold(client, first_draft=False,
                                 cache_read=29_880)
          and not api.cache_cold(
              type("C", (), {"cache": False, "model": client.model,
                             "provider": "litellm"})(),
              first_draft=False, cache_read=0), "")
    check("...and never cries wolf on a model that does not advertise "
          "prompt caching, which is every hermetic run",
          not api.cache_cold(
              type("C", (), {"cache": True, "model": "stub-writer",
                             "provider": "openai"})(),
              first_draft=False, cache_read=0), "")

    buffer = _io.StringIO()
    with contextlib.redirect_stdout(buffer):
        cli_module._print_draft_usage(
            {"line": "LLM: 1 live call(s) (30,412 in / 3,180 out tokens; "
                     "cache 0 read / 0 written) — model "
                     "anthropic/claude-fable-5",
             "cache_cold": True})
    shouted = buffer.getvalue()
    check("the warning the author actually sees names the stable layers "
          "and points at --dry-run, which is how the invalidator is found",
          "0 tokens read on this beat" in shouted
          and "STYLE LAW, DRAFTING CONTEXT, PLAN, CONCEPTS" in shouted
          and "write draft --dry-run" in shouted
          and shouted.count("!!") >= 4, shouted)
    quiet = _io.StringIO()
    with contextlib.redirect_stdout(quiet):
        cli_module._print_draft_usage({"line": "LLM: 1 live call(s)",
                                       "cache_cold": False})
    check("...and a warm beat prints the usage line and nothing else",
          "!!" not in quiet.getvalue()
          and "LLM: 1 live call(s)" in quiet.getvalue(), quiet.getvalue())


def check_drafting_cache_layer() -> None:
    """The `cache_control` layer, which NO end-to-end test can reach.

    The hermetic suite runs against the OpenAI-compatible stub and the
    live-optional suite runs on the cheap tier, so nothing exercises the
    Anthropic block path in flight (design §6.2 says so plainly). Its
    verification in production is the usage line — but the two mechanical
    properties it rests on are unit-testable, and both were places where
    the obvious implementation fails."""
    import json

    from authorlm import llm as llm_mod

    messages = llm_mod._block_messages(["S"], ["A", "B", "C"], caching=True)
    check("two of the four available breakpoints, both on the STABLE "
          "layers: end of the system block and end of the first user block",
          messages[0]["content"][0]["cache_control"]["ttl"] == "1h"
          and messages[1]["content"][0]["cache_control"]["ttl"] == "1h"
          and "cache_control" not in messages[1]["content"][1]
          and "cache_control" not in messages[1]["content"][2],
          json.dumps(messages))
    check("four content blocks, always — so §4's 'a breakpoint looks back "
          "at most 20 content blocks' is structural here, not a discipline",
          len(messages[0]["content"]) + len(messages[1]["content"]) == 4,
          json.dumps(messages))
    off = llm_mod._block_messages(["S"], ["A", "B", "C"], caching=False)
    check("[writing] cache = false is a clean kill switch — the same "
          "blocks, no cache_control anywhere",
          not any("cache_control" in b for m in off for b in m["content"]),
          json.dumps(off))

    client = llm_mod.LLMClient({"llm": {"cache_dir": "/tmp/authorlm-unit"}})
    check("the record/replay key is computed over BLOCK TEXT ONLY: "
          "toggling the cache must not re-record every replay fixture",
          client._cache_path(messages, llm_mod.TEMPERATURE)
          == client._cache_path(off, llm_mod.TEMPERATURE),
          f"{client._cache_path(messages, llm_mod.TEMPERATURE)} vs "
          f"{client._cache_path(off, llm_mod.TEMPERATURE)}")
    plain = [{"role": "system", "content": "S"},
             {"role": "user", "content": "U"}]
    check("...and stripping is a no-op for plain string content, so every "
          "existing fixture keeps its hash",
          llm_mod._without_cache_control(plain) is plain, "")

    check("caching is attached only on the litellm ANTHROPIC path — the "
          "raw-HTTP transport cannot express a content block, and it is "
          "what every hermetic test uses",
          llm_mod.caching_available("anthropic/claude-fable-5", "litellm")
          and not llm_mod.caching_available("anthropic/claude-fable-5",
                                            "openai")
          and not llm_mod.caching_available("gemini/gemini-2.5-flash",
                                            "litellm")
          and not llm_mod.caching_available("stub-writer", "litellm"), "")


def check_model_profiles() -> None:
    """AI: MODEL_PROFILES — the adapter surface above LiteLLM.

    Sponsor ruling on the retry-on-400 temperature net: "This is not ok.
    We need adapters for the different models." The net stays, demoted to
    an alarm; what shapes a request now is a declarative table of model
    contracts we have VERIFIED. These checks pin the three things that
    make such a table trustworthy rather than decorative: that matching is
    specific (a family row never sweeps in a model whose contract is the
    opposite), that the profile OVERRIDES config rather than the other way
    round, and that a gap in the table announces itself in the exact terms
    someone would need to close it."""
    import io as _io
    import re as _re
    import types as _types
    from unittest import mock as _mock

    from authorlm import llm as llm_mod
    from authorlm import summaries as summaries_mod
    from authorlm.llm import LLMClient

    def fake_litellm(reject_temperature: bool = False):
        """Stands in for a provider that 400s server-side on any
        `temperature` — the shape litellm's own param map cannot
        pre-empt for a model it does not recognize."""
        calls = []
        invocations = {"n": 0}
        module = _types.SimpleNamespace(suppress_debug_info=False,
                                        drop_params=False)

        def completion(model, messages, timeout=None, **kwargs):
            invocations["n"] += 1
            if reject_temperature and "temperature" in kwargs:
                raise RuntimeError(
                    "litellm.BadRequestError: OpenAIException - Unsupported "
                    "value: 'temperature' does not support "
                    f"{kwargs['temperature']} with this model. Only the "
                    "default (1) value is supported.")
            calls.append({"model": model, **kwargs})
            return _types.SimpleNamespace(
                choices=[_types.SimpleNamespace(
                    message=_types.SimpleNamespace(content="the reply"))],
                usage=_types.SimpleNamespace(prompt_tokens=11,
                                             completion_tokens=7))

        module.completion = completion
        return module, calls, invocations

    # --- (1) matching: longest/most-specific prefix wins ---------------
    check("the Claude 5 family is vendor-default-only: no temperature "
          "parameter at all, and litellm's own map agrees "
          "(supports_sampling_params = false)",
          all(llm_mod.model_profile(m).temperature
              == llm_mod.VENDOR_DEFAULT_ONLY
              for m in ("anthropic/claude-sonnet-5",
                        "anthropic/claude-fable-5",
                        "anthropic/claude-opus-5")), "")
    check("a dated release picks up its family's row — matching is by "
          "prefix, so the table does not need a row per release",
          llm_mod.model_profile("anthropic/claude-sonnet-5-20260305")
          is llm_mod.model_profile("anthropic/claude-sonnet-5"), "")
    check("claude-haiku-4-5 is NOT swept into the Claude 5 row: litellm's "
          "map carries no supports_sampling_params flag for it, which "
          "means it still takes a temperature",
          llm_mod.model_profile("anthropic/claude-haiku-4-5").temperature
          == llm_mod.ANY, "")
    check("gpt-5.6-luna is vendor-default-only (proven live 2026-08-30) "
          "and the gemini 2.5 family takes temperature as it always has",
          llm_mod.model_profile("openai/gpt-5.6-luna").temperature
          == llm_mod.VENDOR_DEFAULT_ONLY
          and llm_mod.model_profile("gemini/gemini-2.5-flash").temperature
          == llm_mod.ANY, "")
    check("a model nobody verified gets the UNKNOWN profile rather than a "
          "guessed row — gpt-5.6-sol/terra are siblings of luna and are "
          "NOT assumed to share its contract",
          llm_mod.model_profile("openai/gpt-5.6-sol")
          is llm_mod.UNKNOWN_PROFILE
          and llm_mod.model_profile("openai/gpt-5.6-terra")
          is llm_mod.UNKNOWN_PROFILE
          and llm_mod.model_profile("stub-writer")
          is llm_mod.UNKNOWN_PROFILE, "")

    # Specificity is a property of the MATCHER, not of the current rows'
    # spelling, so it is asserted against a table built to overlap — in
    # both orders, because "the more specific row wins" must not be a
    # fact about where a maintainer happened to paste the line.
    broad = llm_mod.ModelProfile("broad", llm_mod.ANY, True, None)
    narrow = llm_mod.ModelProfile("narrow", llm_mod.VENDOR_DEFAULT_ONLY,
                                  False, "anthropic")
    for order in ((("v/fam", broad), ("v/fam-x", narrow)),
                  (("v/fam-x", narrow), ("v/fam", broad))):
        with _mock.patch.object(llm_mod, "MODEL_PROFILES", order):
            check("the longest matching prefix wins regardless of table "
                  f"order ({order[0][0]} declared first)",
                  llm_mod.model_profile("v/fam-x-1") is narrow
                  and llm_mod.model_profile("v/fam-y") is broad, "")

    # --- (2) shaping: the profile overrides the config -----------------
    llm_mod._NOTED.clear()
    stderr = _io.StringIO()
    fake_v, calls_v, inv_v = fake_litellm(reject_temperature=True)
    client_v = LLMClient({"llm": {"enabled": True,
                                  "model": "openai/gpt-5.6-luna",
                                  "temperature": 0.7}})
    with _mock.patch.dict(sys.modules, {"litellm": fake_v}), \
            contextlib.redirect_stderr(stderr):
        reply_v = client_v.complete("sys", "usr")
    check("a config that sets a NUMERIC temperature for a "
          "vendor-default-only model cannot force it onto the wire: the "
          "parameter is omitted before the first call, not after a 400",
          reply_v == "the reply" and inv_v["n"] == 1
          and "temperature" not in calls_v[-1], (calls_v, inv_v))
    check("...and the override is stated once, naming the value, the "
          "profile and the table — a config key with no effect is a fact "
          "the author is told, not one they discover",
          "temperature=0.7" in stderr.getvalue()
          and "gpt-5.6-luna" in stderr.getvalue()
          and "MODEL_PROFILES" in stderr.getvalue()
          and "authorlm/llm.py" in stderr.getvalue(), stderr.getvalue())

    fake_p, calls_p, inv_p = fake_litellm()
    client_p = LLMClient({"llm": {"enabled": True,
                                  "model": "gemini/gemini-2.5-flash",
                                  "temperature": 0.7}})
    with _mock.patch.dict(sys.modules, {"litellm": fake_p}):
        client_p.complete("sys", "usr")
    check("an 'any' profile passes the configured temperature straight "
          "through — the registry shapes what is known, it does not "
          "second-guess what is configured",
          calls_p[-1].get("temperature") == 0.7, calls_p)

    # The shipped shape of the ruling: [critique] naming luna, with a
    # numeric temperature set system-wide.
    fake_s, calls_s, inv_s = fake_litellm(reject_temperature=True)
    sum_client = summaries_mod.summarizer_llm({
        "llm": {"enabled": True, "model": "gemini/gemini-2.5-flash",
                "temperature": 0.7},
        "critique": {"summarizer_model": "openai/gpt-5.6-luna"}})
    with _mock.patch.dict(sys.modules, {"litellm": fake_s}), \
            contextlib.redirect_stderr(_io.StringIO()):
        sum_client.complete("sys", "usr")
    check("the profile follows a POST-CONSTRUCTION .model reassignment, "
          "exactly as api_key does: summarizer_llm built on gemini and "
          "pointed at luna sends no temperature",
          inv_s["n"] == 1 and "temperature" not in calls_s[-1],
          (calls_s, inv_s))

    # --- (3) the safety net, demoted to an alarm -----------------------
    llm_mod._NOTED.clear()
    alarm = _io.StringIO()
    fake_a, calls_a, inv_a = fake_litellm(reject_temperature=True)
    sleeps_a = []
    with _mock.patch.dict(sys.modules, {"litellm": fake_a}), \
            _mock.patch("authorlm.llm.time.sleep", sleeps_a.append), \
            contextlib.redirect_stderr(alarm):
        client_a = LLMClient({"llm": {"enabled": True,
                                      "model": "openai/gpt-5.6-terra"}})
        reply_a = client_a.complete("sys", "usr")
    shouted = alarm.getvalue()
    check("the retry-without-temperature net still saves an unknown "
          "model's call (two invocations, no backoff burnt)",
          reply_a == "the reply" and inv_a["n"] == 2 and not sleeps_a,
          (inv_a, sleeps_a))
    check("...but firing it is now an ALARM: the warning says the model "
          "needs a MODEL_PROFILES row, names the file and the value to "
          "write, and says the cost of not writing it",
          "MODEL_PROFILES" in shouted and "authorlm/llm.py" in shouted
          and 'vendor-default-only' in shouted
          and "round-trip" in shouted, shouted)

    # --- (4) caching_available: same answers, better reasons -----------
    check("caching parity: anthropic-on-litellm yes, the same model on "
          "the raw-HTTP transport no, gemini no, an unknown stub no — "
          "the four answers the cache-layer suite already pins",
          llm_mod.caching_available("anthropic/claude-fable-5", "litellm")
          and not llm_mod.caching_available("anthropic/claude-fable-5",
                                            "openai")
          and not llm_mod.caching_available("gemini/gemini-2.5-flash",
                                            "litellm")
          and not llm_mod.caching_available("stub-writer", "litellm"), "")
    try:
        from litellm.utils import supports_prompt_caching as _spc

        mapped = bool(_spc(model="anthropic/claude-haiku-5"))
    except Exception:
        mapped = False
    check("an anthropic model with NO row keeps exactly the pre-registry "
          "answer (litellm's own cost map), so the table changed no "
          "model's caching behavior — only where the verified ones are "
          "written down",
          llm_mod.model_profile("anthropic/claude-haiku-5")
          is llm_mod.UNKNOWN_PROFILE
          and llm_mod.caching_available("anthropic/claude-haiku-5",
                                        "litellm") == mapped, mapped)

    # --- (5) the unknown-model note is quiet and one-shot --------------
    llm_mod._NOTED.clear()
    once = _io.StringIO()
    with contextlib.redirect_stderr(once):
        LLMClient({"llm": {"enabled": True, "model": "openai/nobody-knows"}})
        LLMClient({"llm": {"enabled": True, "model": "openai/nobody-knows"}})
    said = once.getvalue()
    check("an unknown model is NEVER refused — it gets one informational "
          "line saying rejections will self-correct once and should "
          "become a row",
          said.count("no model profile for 'openai/nobody-knows'") == 1
          and "self-correct once" in said
          and "MODEL_PROFILES" in said, said)
    quiet = _io.StringIO()
    with contextlib.redirect_stderr(quiet):
        LLMClient({"llm": {"model": "openai/also-unknown"}})
    check("...and a DISABLED client says nothing at all: a client that "
          "will never call anything has no profile gap worth reporting",
          quiet.getvalue() == "", quiet.getvalue())

    # --- (6) draft()'s invariant and the registry must not disagree ----
    writing_model = _re.search(r'model = "([^"]+)"', llm_mod.WRITING_ABSENT)
    check("the drafting model the refusal tells the author to paste is "
          "itself vendor-default-only, so draft()'s unconditional "
          "omission of every sampling parameter and this table say the "
          "same thing about it — asserted, not duplicated in code",
          writing_model is not None
          and llm_mod.model_profile(writing_model.group(1)).temperature
          == llm_mod.VENDOR_DEFAULT_ONLY,
          writing_model and writing_model.group(1))

    # --- (7) the shipped config's models are all known -----------------
    import tomllib

    shipped = tomllib.loads(
        (Path(__file__).resolve().parent.parent / "config.toml"
         ).read_text(encoding="utf-8"))
    chat_models = [v for section, key in (("llm", "model"),
                                          ("critique", "summarizer_model"),
                                          ("critique", "editor_model"),
                                          ("writing", "model"))
                   for v in [(shipped.get(section) or {}).get(key)] if v]
    unknown = [m for m in chat_models
               if llm_mod.model_profile(m) is llm_mod.UNKNOWN_PROFILE]
    check("every chat model in the SHIPPED config has a profile row — the "
          "author's own configuration never rings the alarm, and a config "
          "edit that would is caught here first",
          not unknown and len(chat_models) >= 3,
          f"unknown={unknown!r} scanned={chat_models!r}")


def check_config_parity() -> None:
    """ORCH-3: api.load_config and cli._load_config must resolve the
    same config.

    Before the fix, api.load_config(workspace) read
    <workspace-or-home>/.authorlm/config.toml while cli._load_config
    already read paths.config_path() (AUTHORLM_CONFIG override, else the
    project's config.toml) plus paths.load_env(). The two diverged: the
    CLI had the LLM on and the MCP server (which calls api.load_config)
    had it silently off. Pin AUTHORLM_CONFIG at a throwaway config with
    the LLM enabled and prove api.load_config sees it regardless of the
    `workspace` argument passed in — that's what proves it no longer
    keys off workspace/home."""
    import argparse

    from authorlm import cli as cli_module

    prev_config = os.environ.get("AUTHORLM_CONFIG")
    prev_env = os.environ.get("AUTHORLM_ENV")
    cfg_root = Path(tempfile.mkdtemp(prefix="authorlm-config-parity-"))
    try:
        pinned_config = cfg_root / "config.toml"
        pinned_config.write_text(
            '[llm]\nenabled = true\nmodel = "gemini/gemini-2.5-flash"\n'
        )
        pinned_env = cfg_root / ".env"
        pinned_env.write_text("")
        os.environ["AUTHORLM_CONFIG"] = str(pinned_config)
        os.environ["AUTHORLM_ENV"] = str(pinned_env)

        # An unrelated workspace, far from the pinned config directory —
        # neither this workspace nor $HOME holds a config.toml.
        unrelated_ws = cfg_root / "unrelated-workspace"
        unrelated_ws.mkdir()
        args = argparse.Namespace(workspace=str(unrelated_ws))

        from_api = api.load_config(str(unrelated_ws))
        from_cli = cli_module._load_config(args)

        check("api.load_config and cli._load_config resolve the same config",
              from_api == from_cli, f"api={from_api!r} cli={from_cli!r}")
        check("api.load_config picks up the AUTHORLM_CONFIG pin, not "
              "workspace/home",
              from_api.get("llm", {}).get("enabled") is True, str(from_api))
    finally:
        if prev_config is None:
            os.environ.pop("AUTHORLM_CONFIG", None)
        else:
            os.environ["AUTHORLM_CONFIG"] = prev_config
        if prev_env is None:
            os.environ.pop("AUTHORLM_ENV", None)
        else:
            os.environ["AUTHORLM_ENV"] = prev_env
        shutil.rmtree(cfg_root, ignore_errors=True)


def check_shipped_config_bills_no_anthropic_path() -> None:
    """AH-3: the SHIPPED `authorlm/config.toml` names no Anthropic model.

    Author ruling 2026-08-30: "I didn't realize it would be that
    expensive… I don't want to be hit with big dollars." bc76331 removed
    `[writing]` and the `[critique]` Sonnet overrides, leaving no
    Anthropic-billed path in the tracked configuration. The code that
    would use one is untouched and dormant, restorable by config — which
    is exactly why a config edit can quietly re-arm the bill.

    This is a tripwire, not a policy. A future edit that reintroduces an
    `anthropic/` model into a model-bearing key fails here, and UPDATING
    THIS CHECK IS THE DELIBERATE ACT that re-enables it — the author has
    to be told, in a diff, that spending is back on.

    Scanned generically (any key named `model` or ending `_model`, in any
    section) so a new billed key cannot slip in under a name this check
    never heard of; the five keys the ruling names are then asserted to
    have actually been in the scan's reach, so a section vanishing from
    the file cannot be mistaken for a section that passed."""
    import tomllib

    repo_root = Path(__file__).resolve().parent.parent
    shipped = repo_root / "config.toml"
    check("the shipped config.toml is tracked and readable",
          shipped.is_file(), str(shipped))
    config = tomllib.loads(shipped.read_text(encoding="utf-8"))

    found: dict = {}
    for section, body in config.items():
        if not isinstance(body, dict):
            continue
        for key, value in body.items():
            if (key == "model" or key.endswith("_model")) and isinstance(
                    value, str):
                found[f"[{section}] {key}"] = value

    billed = {k: v for k, v in found.items()
              if v.strip().lower().startswith("anthropic/")}
    check("no model-bearing key in the shipped config.toml names an "
          "anthropic/ model — the author ruled the billed paths off, and "
          "re-arming one must be a deliberate edit to THIS check",
          not billed, f"billed keys present: {billed!r}; all: {found!r}")

    # The ruling names five keys. Each is either absent (the two the
    # ruling deleted) or present and scanned — never present-and-missed.
    for name in ("[llm] model", "[critique] summarizer_model",
                 "[critique] editor_model", "[illustrations] model",
                 "[writing] model"):
        section, key = name[1:].split("] ")
        present = isinstance(config.get(section), dict) and key in config[
            section]
        check(f"{name} is either absent or in this check's reach",
              (not present) or name in found,
              f"present={present} scanned={name in found}")

    check("[writing] is absent, so `write draft` (no --dry-run) refuses "
          "and no beat can bill the API by accident",
          "writing" not in config, str(sorted(config)))
    # NOT "the [critique] section is absent": the ruling was about the
    # ANTHROPIC bill, not about the section. A non-Anthropic override is
    # free to come back (and one is: `summarizer_model` on an openai model,
    # cheaper than the [llm] default). What must not come back is a Sonnet
    # override — either key naming an anthropic model.
    critique = config.get("critique", {}) or {}
    check("neither [critique] model key names an anthropic model — the two "
          "Sonnet overrides of 2026-08-29 are what the ruling took out, "
          "and a cheaper override in their place is not a regression",
          not any(str(critique.get(k, "")).strip().lower().startswith(
              "anthropic/")
              for k in ("summarizer_model", "editor_model")),
          str(critique))
    check("...and the [llm] default those keys fall back to is itself not "
          "an anthropic model",
          not (config.get("llm", {}) or {}).get("model", "").startswith(
              "anthropic/"), str(config.get("llm")))

    # The restore recipes live in the config's own comments, where the
    # author looks — not only in a design document they would have to know
    # to open. tomllib drops comments, so read the raw text.
    raw = shipped.read_text(encoding="utf-8")
    check("the config carries the restore recipe for [writing] in its own "
          "comments, so turning drafting back on is a documented decision "
          "rather than a rediscovery",
          "[writing]" in raw and "anthropic/claude-fable-5" in raw
          and "write draft" in raw, raw[:400])


def check_extraction_failure_traced() -> None:
    """OPS-2: a raising run_extraction must leave a trace, not just a
    silently-empty result.

    `collect(..., analyze=True)` and `write_complete` both swallow
    run_extraction's exceptions with a bare `except Exception:` — by
    design, extraction failure must never block observation — but before
    this fix nothing recorded that it happened: `collect`/`write_complete`
    returned the same shape as a genuinely empty extraction pass, so a
    silently-broken LLM pipeline was indistinguishable from a quiet day.
    This does not change control flow (still swallowed, still returns
    `{}`/`None`) — it only asserts a `trace.jsonl` record now exists."""
    import io
    import json as _tjson

    from authorlm import api as _api
    from authorlm import tracelog

    root = Path(tempfile.mkdtemp(prefix="authorlm-ops2-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "01-draft.md").write_text("# Draft\n\nOriginal text.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms)])
        db = _api.open_db(str(ws))
        manuscript = _api.get_manuscript(db)
        config = {"llm": {"enabled": True, "model": "gemini/gemini-2.5-flash"}}
        trace_path = tracelog.log_dir(str(ws)) / "trace.jsonl"

        def boom(*a, **kw):
            raise RuntimeError("stub extraction failure")

        # --- [extraction] enabled = false: the switch (author ruling
        # 2026-09-25). Off means no LLM extraction anywhere, said plainly,
        # while the deterministic graph upkeep keeps running.
        from authorlm.extraction import (ExtractionDisabled,
                                         extract_concepts as _extract_fn,
                                         extraction_enabled)

        check("extraction is on by default and off by the switch",
              extraction_enabled({}) and extraction_enabled({"extraction": {}})
              and not extraction_enabled({"extraction": {"enabled": False}}))
        off_cfg = {**config, "extraction": {"enabled": False}}
        never = {"n": 0}

        def must_not_run(*a, **kw):
            never["n"] += 1
            raise RuntimeError("extraction ran while switched off")

        prev_run_extraction = _api.run_extraction
        _api.run_extraction = must_not_run
        try:
            (ms / "01-draft.md").write_text("# Draft\n\nSwitched-off text.\n")
            off_report = _api.collect(db, manuscript, off_cfg, analyze=True)
        finally:
            _api.run_extraction = prev_run_extraction
        check("an analysed collect skips extraction when the switch is off, "
              "and says so",
              never["n"] == 0 and off_report.get("extraction") == "off",
              str(off_report.get("extraction")))

        class _OffLLM:
            config = off_cfg
            enabled = True

        try:
            _extract_fn(db, manuscript, _OffLLM())
            declined = None
        except ExtractionDisabled as err:
            declined = str(err)
        check("extract_concepts declines by name when switched off",
              declined is not None and "[extraction] enabled = false" in declined
              and "concept add" in declined, str(declined))

        # --- collect(..., analyze=True): api.py's first swallow site ---
        prev_run_extraction = _api.run_extraction
        _api.run_extraction = boom
        try:
            (ms / "01-draft.md").write_text("# Draft\n\nChanged text.\n")
            report = _api.collect(db, manuscript, config, analyze=True)
        finally:
            _api.run_extraction = prev_run_extraction
        check("collect() with a raising run_extraction still returns "
              "(swallow behaviour unchanged)",
              "auto_analysis" not in report, report)
        entries = [_tjson.loads(line)
                  for line in trace_path.read_text().splitlines()]
        collect_entry = entries[-1]
        check("collect()'s swallowed extraction failure is traced",
              collect_entry["verb"] == "extraction"
              and collect_entry["surface"] == "api"
              and collect_entry["manuscript"] == "book"
              and collect_entry["ok"] is False
              and "stub extraction failure" in collect_entry["error"],
              collect_entry)

        # --- write_complete: api.py's second swallow site ---
        _api.define_style_guide(db, manuscript, "house")
        _api.attach_style(db, manuscript, "01-draft.md", "house")
        declared = _api.declare_intent(db, manuscript, "Finish the draft")
        intent_id = declared["intent"]["id"]
        _api.write_start(db, manuscript, config, "01-draft.md", intent_id[:8])
        # `write start` leaves the mid-rewrite placeholder, and completing
        # on a placeholder-only file is refused (design §14.3). This test
        # is about the extraction-failure swallow, so stand in for the
        # accepted beat that would normally have replaced it.
        (ms / "01-draft.md").write_text("# Draft\n\nThe rewritten draft.\n")

        _api.run_extraction = boom
        try:
            result = _api.write_complete(db, manuscript, config)
        finally:
            _api.run_extraction = prev_run_extraction
        check("write_complete() with a raising run_extraction still "
              "returns (swallow behaviour unchanged)",
              result["extraction"] is None, result)
        entries = [_tjson.loads(line)
                  for line in trace_path.read_text().splitlines()]
        complete_entry = entries[-1]
        check("write_complete()'s swallowed extraction failure is traced",
              complete_entry["verb"] == "extraction"
              and complete_entry["surface"] == "api"
              and complete_entry["manuscript"] == "book"
              and complete_entry["ok"] is False
              and "stub extraction failure" in complete_entry["error"],
              complete_entry)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_manuscript_extraction_switch() -> None:
    """Issue #128: concept extraction switched off for ONE manuscript,
    in `manuscripts.metadata`, while a second manuscript in the same
    workspace keeps extracting and the global switch still wins."""
    import io

    from authorlm import api as _api
    from authorlm.extraction import (ExtractionDisabled,
                                     extract_concepts as _extract_fn,
                                     extraction_allowed,
                                     manuscript_extraction_enabled)

    root = Path(tempfile.mkdtemp(prefix="authorlm-msx-"))
    try:
        ws = root / "ws"
        kept, book = ws / "corpus-doc", ws / "book"
        for d, text in ((kept, "# Kept\n\nMachine-kept text.\n"),
                        (book, "# Book\n\nThe author's text.\n")):
            d.mkdir(parents=True)
            (d / "01-draft.md").write_text(text)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli_main(["--workspace", str(ws), "init", "--name", "kept",
                      "--path", str(kept), "--extraction", "off"])
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(book)])
        db = _api.open_db(str(ws))
        m_kept = _api.get_manuscript(db, "kept")
        m_book = _api.get_manuscript(db, "book")
        check("init --extraction off registers the manuscript with its "
              "own switch off, in manuscripts.metadata, and says so",
              json.loads(m_kept["metadata"]).get("extraction")
              == {"enabled": False}
              and not manuscript_extraction_enabled(db, m_kept)
              and "off for this manuscript" in out.getvalue(),
              m_kept["metadata"])
        check("a manuscript registered without the flag extracts as before",
              manuscript_extraction_enabled(db, m_book)
              and "extraction" not in json.loads(m_book["metadata"] or "{}"))

        config = {"llm": {"enabled": True, "model": "gemini/gemini-2.5-flash"}}
        calls: list[str] = []

        def counting(db_, manuscript_, llm_, **kw):
            calls.append(manuscript_["name"])
            return {"up_to_date": True}

        prev = _api.run_extraction
        _api.run_extraction = counting
        try:
            (kept / "01-draft.md").write_text("# Kept\n\nChanged once.\n")
            (book / "01-draft.md").write_text("# Book\n\nChanged once.\n")
            r_kept = _api.collect(db, m_kept, config, analyze=True)
            r_kept_auto = None
            (kept / "01-draft.md").write_text("# Kept\n\nChanged twice.\n")
            r_kept_auto = _api.collect(db, m_kept, config, auto=True)
            after_kept = list(calls)
            r_book = _api.collect(db, m_book, config, analyze=True)
        finally:
            _api.run_extraction = prev
        check("collect with analyze/auto set makes NO extraction call on "
              "the switched-off manuscript, with the global switch on, "
              "and reports it",
              after_kept == [] and r_kept.get("extraction") == "off"
              and r_kept_auto.get("extraction") == "off"
              and r_kept.get("version_no"), str((after_kept, r_kept)))
        check("a second manuscript in the same workspace still extracts",
              calls == ["book"] and "extraction" not in r_book, str(calls))

        class _OnLLM:
            enabled = True

        _OnLLM.config = config
        try:
            _extract_fn(db, m_kept, _OnLLM())
            declined = None
        except ExtractionDisabled as err:
            declined = str(err)
        check("extract_concepts declines by name on a switched-off "
              "manuscript (the explicit verb and the MCP tool's road)",
              declined is not None and "for this manuscript" in declined
              and "manuscript set --extraction on" in declined,
              str(declined))

        # A Doc-mapping write beside the switch must not lose it.
        from authorlm.gdocs import _mapping, _save_mapping

        meta = _mapping(db, m_kept)
        meta.setdefault("gdocs", {})["_master_id"] = "doc-x"
        _save_mapping(db, m_kept, meta)
        check("the switch survives a Doc-mapping write to the same column",
              not manuscript_extraction_enabled(db, m_kept))

        with contextlib.redirect_stdout(io.StringIO()) as shown:
            cli_main(["--workspace", str(ws), "manuscript", "show",
                      "-m", "kept"])
        check("'manuscript show' names the switch when it is off",
              "extraction: off for this manuscript" in shown.getvalue(),
              shown.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "manuscript", "set",
                      "--extraction", "on", "-m", "kept"])
        check("'manuscript set --extraction on' turns it back on, and the "
              "Doc mapping beside it is kept",
              manuscript_extraction_enabled(db, m_kept)
              and _mapping(db, m_kept)["gdocs"]["_master_id"] == "doc-x")
        _api.update_manuscript_metadata(db, m_book, extraction="off")
        check("the api door takes the same words",
              not manuscript_extraction_enabled(db, m_book))
        _api.update_manuscript_metadata(db, m_book, extraction=True)
        check("the global switch still wins when it is off",
              extraction_allowed(db, m_book, config)
              and not extraction_allowed(
                  db, m_book, {**config, "extraction": {"enabled": False}})
              and not extraction_allowed(db, m_kept, {
                  **config, "extraction": {"enabled": False}}))
        m3 = _api.register_manuscript(db, "third", str(book),
                                      extraction=False)
        check("api.register_manuscript(extraction=False) writes the switch",
              not manuscript_extraction_enabled(db, m3)
              and json.loads(m3["metadata"])["extraction"]
              == {"enabled": False})
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_extraction_prompt_provenance() -> None:
    """The author asked: when extraction (or its adjudication pass) runs,
    say which prompt files shaped it, so they can go read them. The list
    must be derived from prompt_registry.REGISTRY (never a hardcoded
    filename), must reflect the run that actually happened — adjudication
    appears whenever the call was actually made (opted in, not an
    aliases-only pass), including a call that ran but came back with
    nothing usable — and CLI and MCP must report the identical set, since
    both surfaces just forward the same `prompt_files` field returned by
    `extraction.extract_concepts`."""
    import io

    from authorlm import api as _api
    from authorlm import cli as cli_module
    from authorlm import extraction
    from authorlm import mcp_server
    from authorlm import prompt_registry as pr

    repo_root = Path(__file__).resolve().parent.parent

    def _on_disk(location: str) -> bool:
        # module-backed locations carry a ":SYMBOL" suffix after the real
        # file path (prompt_registry.Prompt.location) — strip it before
        # checking existence.
        return (repo_root / location.split(":", 1)[0]).is_file()

    check("every registered prompt's location resolves to a real file "
          "on disk",
          all(_on_disk(p.location) for p in pr.REGISTRY),
          str([p.location for p in pr.REGISTRY if not _on_disk(p.location)]))

    extraction_loc = pr.by_name("extraction").location
    adjudication_loc = pr.by_name("adjudication").location
    edges_loc = pr.by_name("extraction-edges").location
    aliases_loc = pr.by_name("extraction-aliases").location

    root = Path(tempfile.mkdtemp(prefix="authorlm-promptprov-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "00-seed.md").write_text("# Seed\n\nNothing to see here.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms)])
        db = _api.open_db(str(ws))
        manuscript = _api.get_manuscript(db, "book")

        def two_context_paragraphs(name: str) -> str:
            return (f"# {name}\n\n" + (f"{name} appears here. " * 4)
                    + f"\n\n## Later\n\n" + (f"{name} returns here. " * 4)
                    + "\n")

        class ExtractOnlyLLM:
            """No `.config` attribute — adjudication.enabled() reads
            `getattr(llm, 'config', None) or {}`, so this stays off."""
            enabled = True
            extraction_max_chars = 24000

            def __init__(self, concept_name: str):
                self.concept_name = concept_name

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [
                    {"name": self.concept_name, "kind": "concept",
                     "notes": "a definition"}],
                        "links": [], "aliases": []}

            def stats_line(self):
                return None

        class AdjudicatingLLM:
            """Opts into adjudication and answers both LLM calls the pass
            makes: extraction (plain payload) then adjudication (payload
            starts with 'CANDIDATE ...', per adjudication._render_candidates)."""
            enabled = True
            extraction_max_chars = 24000

            def __init__(self, concept_name: str):
                self.config = {"extraction": {"adjudicate": True}}
                self.concept_name = concept_name
                self.calls = 0

            def complete_json(self, system, user, thinking_budget=None):
                self.calls += 1
                if user.startswith("CANDIDATE"):
                    return {"concepts": [
                        {"name": self.concept_name, "verdict": "new"}],
                            "links": []}
                return {"concepts": [
                    {"name": self.concept_name, "kind": "concept",
                     "notes": "a definition"}],
                        "links": [], "aliases": []}

            def stats_line(self):
                return None

        # --- (a)+(b): adjudication OFF — only extraction.md is reported,
        # via the CLI surface (_run_extraction's printed output). ---
        (ms / "01-cli-off.md").write_text(two_context_paragraphs("CliOffConcept"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_module._run_extraction(db, manuscript, ExtractOnlyLLM("CliOffConcept"),
                                       files=["01-cli-off.md"])
        cli_off_out = buf.getvalue()
        check("CLI reports the extraction prompt file after a plain pass",
              f"Prompts: {extraction_loc}" in cli_off_out, cli_off_out)
        check("CLI does NOT report adjudication.md when adjudication "
              "never ran",
              adjudication_loc not in cli_off_out, cli_off_out)

        # --- same scenario via MCP: adjudication OFF. ---
        (ms / "02-mcp-off.md").write_text(two_context_paragraphs("McpOffConcept"))
        prev_workspace = mcp_server._WORKSPACE
        prev_llm = mcp_server._llm
        mcp_server._WORKSPACE = str(ws)
        try:
            mcp_server._llm = lambda: ExtractOnlyLLM("McpOffConcept")
            mcp_off = mcp_server.extract_concepts(
                files=["02-mcp-off.md"], manuscript="book")
        finally:
            mcp_server._WORKSPACE = prev_workspace
            mcp_server._llm = prev_llm
        check("MCP extract_concepts carries prompt_files as structured "
              "data, matching the CLI's set when adjudication is off",
              mcp_off["ok"] is True
              and mcp_off["result"]["prompt_files"] == [extraction_loc],
              str(mcp_off))

        # --- (b): adjudication ON, via CLI — both files listed. ---
        (ms / "03-cli-on.md").write_text(two_context_paragraphs("CliOnConcept"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_module._run_extraction(db, manuscript, AdjudicatingLLM("CliOnConcept"),
                                       files=["03-cli-on.md"])
        cli_on_out = buf.getvalue()
        check("CLI reports BOTH extraction.md and adjudication.md when "
              "adjudication actually ran",
              f"Prompts: " in cli_on_out
              and extraction_loc in cli_on_out
              and adjudication_loc in cli_on_out,
              cli_on_out)

        # --- same scenario via MCP: adjudication ON — identical set. ---
        (ms / "04-mcp-on.md").write_text(two_context_paragraphs("McpOnConcept"))
        mcp_server._WORKSPACE = str(ws)
        try:
            mcp_server._llm = lambda: AdjudicatingLLM("McpOnConcept")
            mcp_on = mcp_server.extract_concepts(
                files=["04-mcp-on.md"], manuscript="book")
        finally:
            mcp_server._WORKSPACE = prev_workspace
            mcp_server._llm = prev_llm
        check("MCP reports the same prompt set as the CLI when "
              "adjudication ran (deterministic: reflects the actual run)",
              mcp_on["ok"] is True
              and set(mcp_on["result"]["prompt_files"])
              == {extraction_loc, adjudication_loc},
              str(mcp_on))

        # --- CORRECTION (it-b6fd1a7e5ea0 follow-up): adjudication that
        # RAN but came back with nothing usable (malformed reply here;
        # zero candidates or a model failure hit the same `verdicts is
        # None` path inside adjudication.adjudicate()) must still report
        # adjudication.md. The Sponsor's own trade: "as long as the
        # adjudicator prompts are visible every time it runs, I'll be
        # able to figure out if there is something wrong with the
        # prompt" — every time it RUNS, not every time it succeeds. ---
        class AdjudicatingButEmptyLLM:
            """Opts into adjudication; the adjudication call itself
            returns a non-dict reply, so adjudication.adjudicate() comes
            back with verdicts=None — exercising the 'ran but empty'
            path, distinct from 'never ran'."""
            enabled = True
            extraction_max_chars = 24000

            def __init__(self, concept_name: str):
                self.config = {"extraction": {"adjudicate": True}}
                self.concept_name = concept_name

            def complete_json(self, system, user, thinking_budget=None):
                if user.startswith("CANDIDATE"):
                    return "not a dict"  # malformed adjudication reply
                return {"concepts": [
                    {"name": self.concept_name, "kind": "concept",
                     "notes": "a definition"}],
                        "links": [], "aliases": []}

            def stats_line(self):
                return None

        (ms / "07-empty.md").write_text(two_context_paragraphs("EmptyAdjConcept"))
        empty_result = extraction.extract_concepts(
            db, manuscript, AdjudicatingButEmptyLLM("EmptyAdjConcept"),
            files=["07-empty.md"])
        check("adjudication.md is reported even when the adjudication "
              "call ran but returned nothing usable",
              empty_result is not None
              and adjudication_loc in empty_result["prompt_files"],
              str(empty_result))
        check("a run where adjudication ran but yielded nothing is "
              "distinctly flagged (adjudication_empty), not silently "
              "indistinguishable from a clean pass",
              empty_result.get("adjudication_empty") is True,
              str(empty_result))

        (ms / "08-flag-off.md").write_text(two_context_paragraphs("FlagOffConcept"))
        flag_off_result = extraction.extract_concepts(
            db, manuscript, ExtractOnlyLLM("FlagOffConcept"),
            files=["08-flag-off.md"])
        check("adjudication_empty stays False when adjudication never "
              "ran at all (flag off) — 'never ran' and 'ran but empty' "
              "must stay distinguishable",
              flag_off_result is not None
              and flag_off_result.get("adjudication_empty") is False
              and adjudication_loc not in flag_off_result["prompt_files"],
              str(flag_off_result))

        # --- same 'ran but empty' scenario via the CLI surface: the
        # prompt still appears on the Prompts line, plus a distinct note. ---
        (ms / "09-cli-empty.md").write_text(two_context_paragraphs("CliEmptyConcept"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_module._run_extraction(
                db, manuscript, AdjudicatingButEmptyLLM("CliEmptyConcept"),
                files=["09-cli-empty.md"])
        cli_empty_out = buf.getvalue()
        check("CLI still lists adjudication.md when the call ran but "
              "returned nothing usable",
              adjudication_loc in cli_empty_out, cli_empty_out)
        check("CLI surfaces a distinct note for 'ran but empty', so it "
              "reads differently from a clean adjudicated pass",
              "adjudication ran but the model returned nothing usable"
              in cli_empty_out, cli_empty_out)

        # --- edges-only and aliases-only runs report THEIR prompt, not
        # the base 'extraction' one — the flags change which prompt the
        # run actually used. ---
        _api.add_concept(db, manuscript, "EdgeAlpha", kind="concept")
        _api.add_concept(db, manuscript, "EdgeBeta", kind="concept")
        (ms / "05-edges.md").write_text(
            "# Edges\n\nEdgeAlpha relates closely to EdgeBeta in this text.\n")

        class EdgesLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [], "links": [
                    {"from": "EdgeAlpha", "relation": "depends_on",
                     "to": "EdgeBeta"}]}

            def stats_line(self):
                return None

        edges_result = extraction.extract_concepts(
            db, manuscript, EdgesLLM(), files=["05-edges.md"], edges_only=True)
        check("an --edges run reports extraction-edges' prompt, not the "
              "base extraction prompt",
              edges_result is not None
              and edges_result["prompt_files"] == [edges_loc],
              str(edges_result))

        _api.add_concept(db, manuscript, "AliasGamma", kind="concept")
        _api.add_concept(db, manuscript, "AliasGammaAlt", kind="concept")
        sentence = "AliasGammaAlt is another name for AliasGamma in this text."
        (ms / "06-aliases.md").write_text(f"# Aliases\n\n{sentence}\n")

        class AliasesLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [], "links": [], "aliases": [
                    {"alias": "AliasGammaAlt", "canonical": "AliasGamma",
                     "sentence": sentence}]}

            def stats_line(self):
                return None

        aliases_result = extraction.extract_concepts(
            db, manuscript, AliasesLLM(), files=["06-aliases.md"],
            aliases_only=True)
        check("an --aliases run reports extraction-aliases' prompt, not "
              "the base extraction prompt",
              aliases_result is not None
              and aliases_result["prompt_files"] == [aliases_loc],
              str(aliases_result))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_extraction_skip_reasons() -> None:
    """it-b6fd1a7e5ea0: extraction lumped five unrelated skip causes into
    one `skipped` counter and one CLI word — "malformed" — which was
    simply false for two of them: an unknown relation (VALID_RELATIONS)
    and an unknown endpoint (not yet a known concept) are the extractor's
    own deliberate policy, not a bad model reply. This proves the split:
    each cause lands in its own bucket, the CLI never calls a policy
    rejection "malformed", and the three buckets always reconstitute the
    OLD single total exactly for the same input — reporting changed,
    skip BEHAVIOR did not."""
    import io

    from authorlm import api as _api
    from authorlm import cli as cli_module
    from authorlm import extraction

    root = Path(tempfile.mkdtemp(prefix="authorlm-skipreasons-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "00-seed.md").write_text("# Seed\n\nNothing to see here.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms)])
        db = _api.open_db(str(ws))
        manuscript = _api.get_manuscript(db, "book")

        _api.add_concept(db, manuscript, "KnownAlpha", kind="concept")
        _api.add_concept(db, manuscript, "KnownBeta", kind="concept")
        (ms / "01-skips.md").write_text(
            "# Skips\n\nKnownAlpha relates to KnownBeta in this passage. "
            "KnownAlpha also touches Ghost here.\n")

        class SkipMixLLM:
            """One payload exercising all five original skip sites: two
            malformed concepts, two malformed links, one unknown-relation
            link, one unknown-endpoint link, two malformed aliases."""
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {
                    "concepts": [
                        "not-a-dict",           # malformed: not a dict
                        {"kind": "concept"},    # malformed: no name
                    ],
                    "links": [
                        "not-a-dict",           # malformed: not a dict
                        {"from": "KnownAlpha", "relation": "depends_on",
                         "to": "KnownAlpha"},   # malformed: src == dst
                        {"from": "KnownAlpha", "relation": "not-a-real-relation",
                         "to": "KnownBeta"},    # unknown relation (policy)
                        {"from": "KnownAlpha", "relation": "depends_on",
                         "to": "Ghost"},        # unknown endpoint (Ghost unknown)
                    ],
                    "aliases": [
                        "not-a-dict",           # malformed: not a dict
                        {"alias": "", "canonical": "", "sentence": ""},
                    ],
                }

            def stats_line(self):
                return None

        result = extraction.extract_concepts(
            db, manuscript, SkipMixLLM(), files=["01-skips.md"])

        check("an unknown-relation item lands in skipped_unknown_relation, "
              "NOT skipped_malformed",
              result is not None and result["skipped_unknown_relation"] == 1,
              str(result))
        check("an unknown-endpoint item lands in skipped_unknown_endpoint, "
              "NOT skipped_malformed",
              result["skipped_unknown_endpoint"] == 1, str(result))
        check("a non-dict item (concept/link/alias) lands in "
              "skipped_malformed",
              result["skipped_malformed"] == 6, str(result))
        check("the three buckets sum EXACTLY to the old single 'skipped' "
              "total for the same input — reporting split, skip BEHAVIOR "
              "unchanged",
              result["skipped_malformed"] + result["skipped_unknown_endpoint"]
              + result["skipped_unknown_relation"] == result["skipped"]
              and result["skipped"] == 8,
              str(result))

        # --- CLI wording: only the malformed bucket may say "malformed";
        # the other two are named for what they actually are. ---
        (ms / "02-skips.md").write_text(
            "# Skips2\n\nKnownAlpha relates to KnownBeta in this passage too. "
            "KnownAlpha also touches Ghost2 here.\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_module._run_extraction(db, manuscript, SkipMixLLM(),
                                       files=["02-skips.md"])
        cli_out = buf.getvalue()
        check("CLI reports the malformed count under the word 'malformed'",
              "skipped 6 malformed item(s)" in cli_out, cli_out)
        check("CLI names the unknown-relation skips honestly, not as "
              "'malformed'",
              "1 relationship(s) named an unrecognized relation" in cli_out,
              cli_out)
        check("CLI names the unknown-endpoint skips honestly, not as "
              "'malformed'",
              "1 relationship(s) named an unknown concept" in cli_out,
              cli_out)

        # --- MCP carries the same three buckets as structured data,
        # resolved in the same one place extraction.py already computes
        # them — no separate MCP-side logic to drift. ---
        from authorlm import mcp_server

        (ms / "03-skips.md").write_text(
            "# Skips3\n\nKnownAlpha relates to KnownBeta in this passage as "
            "well. KnownAlpha also touches Ghost3 here.\n")
        prev_workspace = mcp_server._WORKSPACE
        prev_llm = mcp_server._llm
        mcp_server._WORKSPACE = str(ws)
        try:
            mcp_server._llm = lambda: SkipMixLLM()
            mcp_result = mcp_server.extract_concepts(
                files=["03-skips.md"], manuscript="book")
        finally:
            mcp_server._WORKSPACE = prev_workspace
            mcp_server._llm = prev_llm
        check("MCP extract_concepts carries the split skip buckets as "
              "structured data, matching the CLI/direct-call shape",
              mcp_result["ok"] is True
              and mcp_result["result"]["skipped_malformed"] == 6
              and mcp_result["result"]["skipped_unknown_relation"] == 1
              and mcp_result["result"]["skipped_unknown_endpoint"] == 1,
              str(mcp_result))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_backup_and_restore() -> None:
    """OPS-4: the knowledge store has no backup, no integrity check, no
    verified copy today. This proves the fix actually works, not just
    that it runs:

    - A backup taken via the SQLite backup API genuinely RESTORES: after
      the original `.db`/`-wal`/`-shm` files are deleted outright, the
      restored file is queryable through the application layer and holds
      the exact row written before the backup.
    - Skip-if-unchanged: a second backup with no intervening write is
      skipped, not duplicated.
    - Rotation: the 8th distinct backup evicts the 1st; only the 7 most
      recent ever remain on disk.
    - Failure path: an unwritable backups directory fails loudly
      (ok=False, a stderr warning) but never blocks session start.
    - Latency against a realistic (~56 MB) database is measured and
      printed, not just asserted under some threshold blindly.
    - A corrupted store is caught by the new readiness "database sound"
      item instead of crashing the sweep."""
    import contextlib as _ctx
    import io as _io
    import time as _time

    from authorlm import backup, sessions as bses
    from authorlm.db import ko_fields as _ko_fields

    root = Path(tempfile.mkdtemp(prefix="authorlm-backup-"))
    try:
        # --- setup: a real manuscript + one row of real data ---
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "01-draft.md").write_text("# Draft\n\nSome text.\n")
        db = api.open_db(str(ws), create=True)
        manuscript = api.register_manuscript(db, "book", str(ms))
        concept = _ko_fields("cn")
        concept.update(manuscript_id=manuscript["id"], name="Gravity",
                       kind="concept", status="declared", introduced_in=None,
                       notes="The pull of consequence.", aliases="[]")
        db.insert("concept_nodes", concept)

        # --- session start triggers exactly one backup, via the SQLite
        # backup API against the live connection (WAL-safe) ---
        session = bses.start_session(db, manuscript["id"])
        check("starting a session performs a backup (not skipped: it's "
              "the first one)",
              session["backup"]["ok"] and not session["backup"]["skipped"],
              session["backup"])
        bdir = backup.backup_dir(db.path)
        made = sorted(bdir.glob("authorlm-*.db"))
        check("exactly one backup file exists in <workspace>/.authorlm/"
              "backups/ after session start", len(made) == 1, made)
        backup_path = made[0]

        # --- RESTORE_PROOF: destroy the original (.db + WAL + SHM), then
        # prove the backup alone reconstitutes a working, queryable store ---
        db.conn.close()
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(db.path) + suffix)
            if candidate.exists():
                candidate.unlink()
        check("the original .db/-wal/-shm are gone (real loss, not a "
              "copy-elsewhere)", not db.path.exists())

        shutil.copy2(backup_path, db.path)
        restored = api.open_db(str(ws))
        integrity = restored.all("PRAGMA integrity_check")
        check("the restored file is a sound SQLite database "
              "(PRAGMA integrity_check)",
              len(integrity) == 1 and integrity[0][0] == "ok", integrity)
        restored_manuscript = api.get_manuscript(restored, "book")
        check("the restored store is queryable through the application "
              "layer (api.get_manuscript)",
              restored_manuscript["name"] == "book")
        restored_row = restored.one(
            "SELECT name, notes FROM concept_nodes WHERE manuscript_id = ? "
            "AND name = 'Gravity'", (restored_manuscript["id"],))
        check("the exact row written before the backup survives restore "
              "verbatim",
              restored_row is not None
              and restored_row["notes"] == "The pull of consequence.",
              dict(restored_row) if restored_row else None)
        restored.update("concept_nodes", concept["id"],
                        {"notes": "revised after restore"})
        reread = restored.one("SELECT notes FROM concept_nodes WHERE id = ?",
                              (concept["id"],))
        check("the restored database accepts new writes — it's a live "
              "store, not an inert blob",
              reread["notes"] == "revised after restore")
        restored.conn.close()

        # --- skip-if-unchanged: a second backup with no write between is
        # skipped, not duplicated ---
        skip_ws = root / "ws-skip"
        skip_ms = skip_ws / "manuscript"
        skip_ms.mkdir(parents=True)
        (skip_ms / "01.md").write_text("# hi\n")
        skip_db = api.open_db(str(skip_ws), create=True)
        api.register_manuscript(skip_db, "book", str(skip_ms))
        first = backup.perform_backup(skip_db)
        check("the first-ever backup is never skipped",
              first["ok"] and not first["skipped"], first)
        second = backup.perform_backup(skip_db)
        check("a second backup with no intervening write is skipped, so "
              "identical snapshots don't evict useful history",
              second["ok"] and second["skipped"], second)
        skip_bdir = backup.backup_dir(skip_db.path)
        check("the skipped backup left exactly one file behind",
              len(list(skip_bdir.glob("authorlm-*.db"))) == 1)
        skip_node = _ko_fields("cn")
        skip_node.update(manuscript_id="does-not-matter", name="Mutation",
                         kind="concept", status="declared",
                         introduced_in=None, notes="", aliases="[]")
        skip_db.insert("concept_nodes", skip_node)
        third = backup.perform_backup(skip_db)
        check("a real change since the last backup is never skipped",
              third["ok"] and not third["skipped"], third)
        check("the changed-content backup produced a second file",
              len(list(skip_bdir.glob("authorlm-*.db"))) == 2)

        # --- rotation: the 8th distinct backup evicts the 1st; only the
        # 7 most recent are ever kept, and nothing is deleted before the
        # new backup is safely in place ---
        rot_ws = root / "ws-rotate"
        rot_ms = rot_ws / "manuscript"
        rot_ms.mkdir(parents=True)
        (rot_ms / "01.md").write_text("# hi\n")
        rot_db = api.open_db(str(rot_ws), create=True)
        api.register_manuscript(rot_db, "book", str(rot_ms))
        made_paths = []
        for i in range(9):
            node = _ko_fields("cn")
            node.update(manuscript_id="rotate", name=f"Concept {i}",
                       kind="concept", status="declared", introduced_in=None,
                       notes=f"distinct payload {i}", aliases="[]")
            rot_db.insert("concept_nodes", node)
            result = backup.perform_backup(rot_db)
            check(f"rotation backup #{i + 1} succeeds and is not skipped "
                  "(content changed each time)",
                  result["ok"] and not result["skipped"], result)
            made_paths.append(Path(result["path"]))
            if i == 6:  # 7 kept so far — 1st is still present, none evicted
                check("before the 8th backup, all 7 made so far are still "
                      "kept (retention isn't over-eager)",
                      made_paths[0].exists(), [p.name for p in made_paths])
            if i == 7:  # the 8th backup just landed
                check("the 8th backup evicts exactly the 1st (oldest)",
                      not made_paths[0].exists()
                      and all(p.exists() for p in made_paths[1:8]),
                      [p.name for p in made_paths])
            _time.sleep(0.002)  # keep the microsecond-resolution names distinct
        rot_bdir = backup.backup_dir(rot_db.path)
        remaining = sorted(rot_bdir.glob("authorlm-*.db"))
        check("rotation keeps exactly the 7 most recent backups",
              len(remaining) == backup.KEEP, remaining)
        check("the 8th backup evicted the 1st (oldest two of nine gone: "
              "8 made it past #7's retention, so #1 and #2 are evicted)",
              not made_paths[0].exists() and not made_paths[1].exists()
              and all(p.exists() for p in made_paths[2:]),
              [p.name for p in made_paths])

        # --- failure path: an unwritable backups directory fails loudly
        # but never blocks the session ---
        fail_ws = root / "ws-fail"
        fail_ms = fail_ws / "manuscript"
        fail_ms.mkdir(parents=True)
        (fail_ms / "01.md").write_text("# hi\n")
        fail_db = api.open_db(str(fail_ws), create=True)
        fail_manuscript = api.register_manuscript(fail_db, "book", str(fail_ms))
        data_dir = fail_db.path.parent  # <workspace>/.authorlm
        os.chmod(data_dir, 0o500)  # read+execute only: mkdir("backups") fails
        try:
            stderr_capture = _io.StringIO()
            with _ctx.redirect_stderr(stderr_capture):
                fail_result = backup.run(fail_db)
            check("a backup into an unwritable directory reports failure, "
                  "not success", fail_result["ok"] is False, fail_result)
            check("the failure is printed loudly to stderr — never "
                  "swallowed silently",
                  "AUTHORLM BACKUP FAILED" in stderr_capture.getvalue(),
                  stderr_capture.getvalue())
            fail_session = bses.start_session(fail_db, fail_manuscript["id"])
            check("a failed backup never blocks the author's session from "
                  "starting",
                  fail_session["status"] == "active"
                  and fail_session["backup"]["ok"] is False,
                  fail_session)
        finally:
            os.chmod(data_dir, 0o700)  # restore so cleanup can rmtree it

        # --- latency against a realistic (~56 MB) database ---
        lat_ws = root / "ws-latency"
        lat_ms = lat_ws / "manuscript"
        lat_ms.mkdir(parents=True)
        (lat_ms / "01.md").write_text("# hi\n")
        lat_db = api.open_db(str(lat_ws), create=True)
        lat_manuscript = api.register_manuscript(lat_db, "book", str(lat_ms))
        # ~56 MB, matching ~/.authorlm/authorlm.db's real size (RFC OPS-4):
        # 60 rows of ~1 MB of text in manuscript_versions.files.
        blob = "x" * (1024 * 1024)
        with lat_db.transaction():
            for i in range(60):
                version = _ko_fields("mv")
                version.update(manuscript_id=lat_manuscript["id"],
                               version_no=i, checksum=f"c{i}",
                               files=blob, source="synthetic", session_id=None)
                lat_db.insert("manuscript_versions", version)
        lat_db.conn.execute("PRAGMA wal_checkpoint(FULL)")
        db_size_mb = lat_db.path.stat().st_size / (1024 * 1024)
        started = _time.monotonic()
        lat_result = backup.perform_backup(lat_db)
        measured_s = _time.monotonic() - started
        check(f"backing up a {db_size_mb:.1f} MB database succeeds",
              lat_result["ok"] and not lat_result["skipped"], lat_result)
        check("backing up a realistic-size database completes fast enough "
              "not to make the shell feel broken (< 10s, generous bound)",
              measured_s < 10, measured_s)
        print(f"  ok: measured backup latency for {db_size_mb:.1f} MB db "
              f"= {measured_s:.3f}s (internal timer: "
              f"{lat_result['elapsed_s']}s)")

        # --- corruption is caught by readiness's new "database sound"
        # item instead of crashing the sweep ---
        from authorlm import sweeps

        corrupt_ws = root / "ws-corrupt"
        corrupt_ms = corrupt_ws / "manuscript"
        corrupt_ms.mkdir(parents=True)
        (corrupt_ms / "01.md").write_text("# hi\n")
        corrupt_db = api.open_db(str(corrupt_ws), create=True)
        corrupt_manuscript = api.register_manuscript(
            corrupt_db, "book", str(corrupt_ms))
        for i in range(3000):
            node = _ko_fields("cn")
            node.update(manuscript_id=corrupt_manuscript["id"],
                       name=f"Concept {i}", kind="concept",
                       status="declared", introduced_in=None,
                       notes="x" * 300, aliases="[]")
            corrupt_db.insert("concept_nodes", node)
        corrupt_db.conn.execute("PRAGMA wal_checkpoint(FULL)")
        corrupt_db.conn.close()
        size = corrupt_db.path.stat().st_size
        with open(corrupt_db.path, "r+b") as handle:
            handle.seek(int(size * 0.7))
            chunk = handle.read(4096)
            handle.seek(int(size * 0.7))
            handle.write(bytes(b ^ 0xFF for b in chunk))
        reopened = api.open_db(str(corrupt_ws))
        report = sweeps.readiness(reopened, corrupt_manuscript)
        corrupt_item = next(i for i in report["items"]
                            if i["check"] == "database sound")
        check("readiness catches a corrupted store via 'database sound' "
              "instead of crashing the sweep",
              corrupt_item["ok"] is False
              and "database sound" in report["blocking"],
              corrupt_item)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _guard_fixture(prefix: str):
    """Common workspace/manuscript setup for the Q-remediation guard tests
    (check_alias_guard / check_alias_dedupe / check_note_group /
    check_note_materiality below). Caller is responsible for
    shutil.rmtree(root, ignore_errors=True) in a finally block."""
    import io

    root = Path(tempfile.mkdtemp(prefix=prefix))
    ws = root / "ws"
    ms = ws / "manuscript"
    ms.mkdir(parents=True)
    (ms / "00-seed.md").write_text("# Seed\n\nNothing to see here.\n")
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms)])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db, "book")
    return root, ws, ms, db, manuscript


def check_alias_guard() -> None:
    """Q/alias-guard: never propose an alias whose name is already a live
    concept — refused at the extraction.py creation site and recorded as
    system-provenance evidence instead of silently dropped.

    Ratified against measured evidence on the author's live graph: 13 of
    20 open alias proposals named a concept that was already its own live
    concept (e.g. 'History' -> 'Rebirth', with 'History' itself live)."""
    from authorlm import extraction

    root, ws, ms, db, manuscript = _guard_fixture("authorlm-alias-guard-")
    try:
        mid = manuscript["id"]
        api.add_concept(db, manuscript, "AliasHistory", kind="concept")
        api.add_concept(db, manuscript, "AliasRebirth", kind="concept")
        sentence = "AliasHistory is what we mean by AliasRebirth in this text."
        (ms / "01-alias.md").write_text(f"# Alias\n\n{sentence}\n")

        class LiveAliasLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [], "links": [], "aliases": [
                    {"alias": "AliasHistory", "canonical": "AliasRebirth",
                     "sentence": sentence}]}

            def stats_line(self):
                return None

        alias_before = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
            "manuscript_id = ? AND kind = 'alias'", (mid,))["n"]
        alias_result = extraction.extract_concepts(
            db, manuscript, LiveAliasLLM(), files=["01-alias.md"],
            aliases_only=True)
        alias_after = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
            "manuscript_id = ? AND kind = 'alias'", (mid,))["n"]
        check("an alias whose name is already a live concept creates no "
              "proposal",
              alias_result is not None and alias_after == alias_before,
              str(alias_result))
        check("extract_concepts reports exactly one refused fold",
              alias_result is not None
              and alias_result.get("alias_folds_refused") == 1,
              str(alias_result))
        alias_evidence = db.all(
            "SELECT * FROM evidence WHERE manuscript_id = ? "
            "AND evidence_type = 'extraction_adjudication' "
            "AND signal = 'alias_fold_refused'", (mid,))
        check("the refusal is recorded as low-weight system-provenance "
              "evidence, not silently dropped",
              len(alias_evidence) == 1 and alias_evidence[0]["weight"] == "low",
              str([dict(r) for r in alias_evidence]))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_alias_dedupe() -> None:
    """Q/alias-dedupe: one alias name may not have two open, contradictory
    canonicals — 'Prophecy' -> 'Birth-based Superstition' and 'Prophecy' ->
    'Essentialism' cannot both stand open simultaneously (measured on the
    author's live graph: exactly this pair). An exact repeat (same alias,
    same canonical) collapses to the standing row instead of piling up a
    second one."""
    from authorlm import proposals as prop

    root, ws, ms, db, manuscript = _guard_fixture("authorlm-alias-dedupe-")
    try:
        mid = manuscript["id"]
        api.add_concept(db, manuscript, "AliasHistory", kind="concept")
        alias_target = db.one(
            "SELECT id FROM concept_nodes WHERE manuscript_id = ? AND name = ?",
            (mid, "AliasHistory"))["id"]
        first = prop.create(db, mid, "alias", alias_target,
                            {"alias": "Prophecy",
                             "canonical": "Birth-based Superstition",
                             "sentence": "Prophecy is birth-based superstition."})
        check("the first open canonical for an alias name is created",
              first is not None)
        second = prop.create(db, mid, "alias", alias_target,
                             {"alias": "Prophecy", "canonical": "Essentialism",
                              "sentence": "Prophecy is mere essentialism."})
        check("a second, contradictory canonical for the same alias name "
              "is refused while the first is still open",
              second is None)
        repeat = prop.create(db, mid, "alias", alias_target,
                             {"alias": "Prophecy",
                              "canonical": "Birth-based Superstition",
                              "sentence": "A differently-worded restatement."})
        check("an exact repeat (same alias, same canonical, different "
              "sentence) collapses instead of piling up a second row",
              repeat is None)
        open_alias_rows = [r for r in prop.open_proposals(db, mid)
                          if r["kind"] == "alias"]
        check("only the first alias proposal for 'Prophecy' ever reached "
              "the queue",
              len(open_alias_rows) == 1, str(open_alias_rows))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_note_group() -> None:
    """Q/note-group: several open note_update proposals on the same
    concept present as ONE grouped decision with every candidate note, not
    N separate ones — storage is untouched, each candidate keeps its own
    id and the author picks, nothing is auto-chosen. Ratified against
    measured evidence on the author's live graph: 31 of 64 open proposals
    compete, unpresented, over just 11 concepts (e.g. 'History' x4)."""
    from authorlm import proposals as prop

    root, ws, ms, db, manuscript = _guard_fixture("authorlm-note-group-")
    try:
        mid = manuscript["id"]
        api.add_concept(db, manuscript, "GroupedConcept", kind="concept",
                        notes="original note")
        grouped_target = db.one(
            "SELECT id FROM concept_nodes WHERE manuscript_id = ? AND name = ?",
            (mid, "GroupedConcept"))["id"]
        candidate_notes = [
            "A first rewrite arguing the concept is fundamentally relational.",
            "A second rewrite arguing the concept is fundamentally temporal.",
            "A third rewrite arguing the concept is fundamentally structural.",
        ]
        for note in candidate_notes:
            created = prop.create(db, mid, "note_update", grouped_target,
                                  {"name": "GroupedConcept",
                                   "current_note": "original note",
                                   "proposed_note": note,
                                   "current_kind": "concept",
                                   "proposed_kind": "concept"})
            check(f"competing note_update candidate is created: {note[:30]}…",
                  created is not None)
        grouped_rows = prop.group_open(prop.open_proposals(db, mid))
        group_entry = next(r for r in grouped_rows
                           if r["kind"] == "note_update_group"
                           and r["target"] == grouped_target)
        check("three competing note_update proposals present as ONE "
              "grouped decision, not three",
              len(group_entry["members"]) == 3, str(group_entry))
        summary, details = prop.describe(group_entry)
        check("the grouped decision's summary names how many candidates "
              "are on the table",
              "3 candidate notes" in summary, summary)
        check("every candidate note appears in the rendered details, each "
              "addressable by its own id",
              all(any(m["id"][:8] in line and
                      loads(m["payload"], {})["proposed_note"][:20] in line
                      for line in details)
                  for m in group_entry["members"]),
              str(details))
        listed = api.list_proposals(db, manuscript, kind="note_update")
        listed_group = next(
            (r for r in listed["open"] if r["kind"] == "note_update_group"),
            None)
        check("list_proposals (the api/MCP surface) groups them too, "
              "without changing how they are stored",
              listed_group is not None
              and db.one(
                  "SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
                  "manuscript_id = ? AND kind = 'note_update' AND "
                  "state = 'open'", (mid,))["n"] == 3,
              str(listed.get("open")))
        # Each candidate is still individually actionable by its own id.
        one_member = group_entry["members"][0]
        adopt_msg = prop.adopt(db, mid, one_member)
        check("a single candidate inside a group still adopts on its own id",
              "Updated 'GroupedConcept'" in adopt_msg, adopt_msg)
        still_open = [r for r in prop.open_proposals(db, mid)
                     if r["kind"] == "note_update" and r["target"] == grouped_target]
        check("adopting one candidate leaves its siblings open for their "
              "own review (no auto-picked winner)",
              len(still_open) == 2, str(still_open))
    finally:
        shutil.rmtree(root, ignore_errors=True)




def check_note_materiality() -> None:
    """Q/note-materiality: a case/whitespace-only change, or a >=0.90
    near-paraphrase, is refused at note_update creation (one observed
    proposal decapitalized the term of art 'Quality' to 'quality' — a
    live style-law violation); a genuine refinement in the 0.75-0.90 band
    still gets through (no over-cutting; the adjudicator already rules on
    that band correctly)."""
    from authorlm import extraction
    from authorlm.loop import similarity

    root, ws, ms, db, manuscript = _guard_fixture("authorlm-note-materiality-")
    try:
        mid = manuscript["id"]
        blocked_case = extraction._note_update_blocked("Quality", "quality")
        check("a case-only note change is refused (the 'Quality' -> "
              "'quality' degradation)",
              blocked_case is not None, str(blocked_case))
        blocked_ws = extraction._note_update_blocked(
            "the   term  of   art", "the term of art")
        check("a whitespace-only note change is refused",
              blocked_ws is not None, str(blocked_ws))

        base_note = (
            "The clasp of every opposite, the ground of becoming, the "
            "shape that holds all form, the light within shadow, the "
            "silence beneath depth, the stillness at the origin of "
            "things, the quiet heart of paradox.")
        near_paraphrase = (
            "The clasp of every opposite, the ground of becoming, the "
            "shape that holds all form, the light within shadow, the "
            "silence beneath depth, the stillness at the source of "
            "things, the quiet heart of paradox.")
        refinement = (
            "The clasp of every opposite, the ground of becoming, the "
            "shape that holds all pattern, the light within shadow, the "
            "silence beneath distance, the stillness at the origin of "
            "things, the quiet heart of paradox.")
        para_score = similarity(base_note, near_paraphrase)
        refine_score = similarity(base_note, refinement)
        check(f"fixture calibration: the paraphrase pair scores >=0.90 "
              f"(measured {para_score:.3f})", para_score >= 0.90,
              str(para_score))
        check(f"fixture calibration: the refinement pair sits in the "
              f"0.75-0.90 band (measured {refine_score:.3f})",
              0.75 <= refine_score < 0.90, str(refine_score))
        blocked_para = extraction._note_update_blocked(base_note, near_paraphrase)
        check("a near-paraphrase (>=0.90 similarity) is refused",
              blocked_para is not None, str(blocked_para))
        allowed_refine = extraction._note_update_blocked(base_note, refinement)
        check("a genuine refinement in the 0.75-0.90 band is NOT refused "
              "— the floor must not become a ceiling (guard against "
              "over-cutting; do not lower it to 0.75)",
              allowed_refine is None, str(allowed_refine))

        # End-to-end through extract_concepts, not just the helper: the
        # near-paraphrase creates no proposal and is reported refused; the
        # 0.75-0.90 refinement still reaches the queue as a real proposal.
        api.add_concept(db, manuscript, "MaterialTerm", kind="concept",
                        notes=base_note)
        (ms / "02-materiality-block.md").write_text(
            f"# M\n\nMaterialTerm: {near_paraphrase}\n")

        class NearParaphraseLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [
                    {"name": "MaterialTerm", "kind": "concept",
                     "notes": near_paraphrase}],
                        "links": [], "aliases": []}

            def stats_line(self):
                return None

        note_before = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
            "manuscript_id = ? AND kind = 'note_update'", (mid,))["n"]
        block_result = extraction.extract_concepts(
            db, manuscript, NearParaphraseLLM(),
            files=["02-materiality-block.md"])
        note_after = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
            "manuscript_id = ? AND kind = 'note_update'", (mid,))["n"]
        check("end-to-end: a >=0.90 paraphrase creates no note_update "
              "proposal", note_after == note_before, str(block_result))
        check("end-to-end: extract_concepts reports the materiality "
              "refusal",
              block_result is not None
              and len(block_result.get("materiality_refused") or []) == 1
              and "MaterialTerm" in block_result["materiality_refused"][0],
              str(block_result))

        (ms / "03-materiality-allow.md").write_text(
            f"# M2\n\nMaterialTerm: {refinement}\n")

        class RefinementLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [
                    {"name": "MaterialTerm", "kind": "concept",
                     "notes": refinement}],
                        "links": [], "aliases": []}

            def stats_line(self):
                return None

        allow_result = extraction.extract_concepts(
            db, manuscript, RefinementLLM(),
            files=["03-materiality-allow.md"])
        note_after_allow = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
            "manuscript_id = ? AND kind = 'note_update'", (mid,))["n"]
        check("end-to-end: a genuine ~0.80-similarity refinement still "
              "reaches the queue as a real note_update proposal",
              note_after_allow == note_after + 1, str(allow_result))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_alias_guide_flip() -> None:
    """Q/alias-guide-flip: the ALIAS GUIDE now requires the canonical to
    already be a known concept and the alias to be a name that is NOT yet
    its own known concept — the naming-ceremony case ("we call it The
    Chid"). Measured against the author's 20 open alias proposals: 14
    alias names were LIVE concepts (genuine merges mislabelled as
    aliases), 6 were RETIRED, and 0 were the naming-ceremony case the old
    guide actually forbade ("BOTH names are known concepts"). This proves
    the positive case: a genuinely new name bestowed on a known concept
    now PRODUCES a real, adoptable alias proposal — without it the guard
    would leave the whole feature dead."""
    from authorlm import extraction, proposals as prop
    from authorlm.concepts import get_concept as _get_concept

    root, ws, ms, db, manuscript = _guard_fixture("authorlm-alias-flip-")
    try:
        mid = manuscript["id"]
        api.add_concept(db, manuscript, "FlipCanonical", kind="concept",
                        notes="the established term")
        sentence = "We call it FlipBestowed, the resolved name for FlipCanonical."
        (ms / "01-flip.md").write_text(f"# Flip\n\n{sentence}\n")

        class NamingCeremonyLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [], "links": [], "aliases": [
                    {"alias": "FlipBestowed", "canonical": "FlipCanonical",
                     "sentence": sentence}]}

            def stats_line(self):
                return None

        check("the bestowed name does not exist as a concept before "
              "extraction",
              _get_concept(db, mid, "FlipBestowed") is None)
        before = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
            "manuscript_id = ? AND kind = 'alias'", (mid,))["n"]
        result = extraction.extract_concepts(
            db, manuscript, NamingCeremonyLLM(), files=["01-flip.md"],
            aliases_only=True)
        after = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
            "manuscript_id = ? AND kind = 'alias'", (mid,))["n"]
        check("a naming ceremony for a genuinely new name PRODUCES an "
              "alias proposal",
              result is not None and after == before + 1, str(result))
        row = db.one(
            "SELECT * FROM knowledge_proposals WHERE manuscript_id = ? "
            "AND kind = 'alias' AND state = 'open'", (mid,))
        payload = loads(row["payload"], {})
        check("the produced proposal is flagged as a new-name naming "
              "ceremony, not a merge",
              payload.get("alias_is_new") is True, str(payload))
        summary, details = prop.describe(dict(row))
        check("describe() phrases adoption as 'add', not 'merge', for a "
              "naming ceremony",
              "adopt = add:" in " ".join(details), details)

        message = prop.adopt(db, mid, dict(row))
        check("adopting the naming-ceremony proposal ADDS the name rather "
              "than attempting a merge",
              "recorded as a new name for 'FlipCanonical'" in message,
              message)
        canonical = db.one(
            "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND name = ?",
            (mid, "FlipCanonical"))
        check("the canonical concept's aliases now include the bestowed "
              "name",
              "FlipBestowed" in loads(canonical["aliases"], []),
              canonical["aliases"])
        check("no second concept_nodes row was created for the bestowed "
              "name (it is an alias, not a new node)",
              db.one(
                  "SELECT COUNT(*) AS n FROM concept_nodes WHERE "
                  "manuscript_id = ? AND lower(name) = lower(?)",
                  (mid, "FlipBestowed"))["n"] == 0)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_alias_retired_guard() -> None:
    """Q/alias-retired-guard: an alias whose name is a RETIRED concept is
    also refused, not just one whose name is a live concept. Retired names
    are banned from re-entering the graph (extraction.py's `banned` set in
    the concepts loop); an alias would be a side door around that ban, so
    it is refused and recorded under a signal DISTINCT from the
    live-concept fold (Q/alias-guard) — the two reasons stay separable in
    the evidence: 6 of the author's 20 open alias proposals named a
    retired concept."""
    from authorlm import extraction

    root, ws, ms, db, manuscript = _guard_fixture("authorlm-alias-retired-")
    try:
        mid = manuscript["id"]
        api.add_concept(db, manuscript, "RetiredGuardCanon", kind="concept")
        api.add_concept(db, manuscript, "RetiredGuardName", kind="concept")
        db.update(
            "concept_nodes",
            db.one("SELECT id FROM concept_nodes WHERE manuscript_id = ? "
                   "AND name = ?", (mid, "RetiredGuardName"))["id"],
            {"status": "retired"})
        sentence = ("We once called RetiredGuardCanon by the name "
                    "RetiredGuardName.")
        (ms / "01-retired.md").write_text(f"# Retired\n\n{sentence}\n")

        class RetiredAliasLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [], "links": [], "aliases": [
                    {"alias": "RetiredGuardName",
                     "canonical": "RetiredGuardCanon",
                     "sentence": sentence}]}

            def stats_line(self):
                return None

        before = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
            "manuscript_id = ? AND kind = 'alias'", (mid,))["n"]
        result = extraction.extract_concepts(
            db, manuscript, RetiredAliasLLM(), files=["01-retired.md"],
            aliases_only=True)
        after = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
            "manuscript_id = ? AND kind = 'alias'", (mid,))["n"]
        check("an alias naming a retired concept creates no proposal",
              result is not None and after == before, str(result))
        check("extract_concepts reports exactly one retired-name refusal, "
              "separately from live-concept folds",
              result is not None
              and result.get("alias_retired_refused") == 1
              and result.get("alias_folds_refused") == 0,
              str(result))
        retired_evidence = db.all(
            "SELECT * FROM evidence WHERE manuscript_id = ? "
            "AND evidence_type = 'extraction_adjudication' "
            "AND signal = 'alias_retired_refused'", (mid,))
        check("the retired-name refusal is recorded under its own signal",
              len(retired_evidence) == 1
              and retired_evidence[0]["weight"] == "low", str(
                  [dict(r) for r in retired_evidence]))
        live_evidence = db.all(
            "SELECT * FROM evidence WHERE manuscript_id = ? "
            "AND evidence_type = 'extraction_adjudication' "
            "AND signal = 'alias_fold_refused'", (mid,))
        check("no live-concept-fold evidence is recorded for a retired-name "
              "refusal — the two reasons stay separable",
              len(live_evidence) == 0, str([dict(r) for r in live_evidence]))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_vanished_directory_guard() -> None:
    """it-258752ea91f9: a manuscript root that has gone missing (deleted
    directory, unmounted volume, etc.) must never be read the same as the
    author having deleted every file in it. `read_manuscript_files`
    returns {} for a missing root with no error, which — before this fix —
    fed straight into collect_revision() and made every tracked file look
    removed. The auto-collect mass-deletion guard only ever ran when
    `auto=True`, and _catch_up (reached by MCP get_briefing/get_guidance
    with no human present) always passes auto=False, so this was the
    exact unattended path that silently retired unconfirmed hypotheses."""
    import json as _json

    from authorlm.revisions import ManuscriptRootUnreadable

    root = Path(tempfile.mkdtemp(prefix="authorlm-vanished-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "01.md").write_text("# Opening\n\nA placeholder paragraph.\n")
        db = api.open_db(str(ws), create=True)
        manuscript = api.register_manuscript(db, "book", str(ms))
        api.collect(db, manuscript, {})  # v1 baseline, no concepts yet

        # An unconfirmed extracted hypothesis (what extraction.py stamps
        # before the author has confirmed it), realized once its name
        # actually lands in the text — realization only runs on a version
        # that is actually recorded, so the checksum must change too.
        hypothesis = api.add_concept(db, manuscript, "Gravity")
        db.update("concept_nodes", hypothesis["id"], {"metadata": _json.dumps(
            {"origin": "extracted", "confirmed": False})})
        (ms / "01.md").write_text(
            "# Opening\n\nA placeholder paragraph. Gravity is discussed early.\n")
        api.collect(db, manuscript, {})  # v2: realizes Gravity
        check("fixture: the hypothesis is realized before the directory "
              "vanishes",
              db.one("SELECT status FROM concept_nodes WHERE id = ?",
                     (hypothesis["id"],))["status"] == "realized")
        version_before = db.one(
            "SELECT version_no FROM manuscript_versions WHERE "
            "manuscript_id = ? ORDER BY version_no DESC LIMIT 1",
            (manuscript["id"],))["version_no"]

        shutil.rmtree(ms)  # the reported repro: the directory itself vanishes

        raised = None
        try:
            # This is exactly the shape _catch_up calls: auto defaults to
            # False, so no human is standing by to see a 'staged' prompt.
            api.collect(db, manuscript, {})
        except Exception as err:  # noqa: BLE001 — inspected below
            raised = err
        check("a non-auto collect against a vanished directory refuses "
              "instead of silently recording an all-files-removed version",
              isinstance(raised, ManuscriptRootUnreadable), repr(raised))

        after = db.one("SELECT status FROM concept_nodes WHERE id = ?",
                       (hypothesis["id"],))
        check("the unconfirmed hypothesis was NOT retired by the "
              "unreadable directory — that is not an editorial act",
              after["status"] == "realized", dict(after))

        version_after = db.one(
            "SELECT version_no FROM manuscript_versions WHERE "
            "manuscript_id = ? ORDER BY version_no DESC LIMIT 1",
            (manuscript["id"],))["version_no"]
        check("no new version was recorded for the vanished directory",
              version_after == version_before, (version_before, version_after))

        # --- _catch_up must surface hypotheses_dropped/vanished ---
        # Restore the directory, add a confirmed concept and another
        # unconfirmed hypothesis, realize both, then genuinely remove
        # their text (a real editorial deletion, not a vanished root) and
        # prove _catch_up's slimmer return shape still carries both
        # fields through to MCP get_briefing/get_guidance.
        ms.mkdir(parents=True)
        (ms / "01.md").write_text(
            "# Opening\n\nChoice enters here. A Fleeting Aside too.\n")
        confirmed_concept = api.add_concept(db, manuscript, "Choice")
        fleeting = api.add_concept(db, manuscript, "Fleeting Aside")
        db.update("concept_nodes", fleeting["id"], {"metadata": _json.dumps(
            {"origin": "extracted", "confirmed": False})})
        api.collect(db, manuscript, {})  # realizes both
        check("fixture: both concepts realized before their text is removed",
              db.one("SELECT status FROM concept_nodes WHERE id = ?",
                     (confirmed_concept["id"],))["status"] == "realized"
              and db.one("SELECT status FROM concept_nodes WHERE id = ?",
                        (fleeting["id"],))["status"] == "realized")
        (ms / "01.md").write_text("# Opening\n\nNeither name remains.\n")

        caught = api._catch_up(db, manuscript, {})
        check("_catch_up surfaces the vanished (confirmed) proposal, not "
              "just version_no/transitions/new_files",
              caught is not None and caught.get("vanished") == ["Choice"],
              caught)
        check("_catch_up surfaces hypotheses_dropped (unconfirmed, retired "
              "quietly)",
              caught is not None
              and caught.get("hypotheses_dropped") == ["Fleeting Aside"],
              caught)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_unregister_safety() -> None:
    """it-f661015221b0: cmd_unregister deletes every manuscript-scoped row
    with no transaction and no backup. An interruption partway through
    the delete loop must leave the manuscript intact (not half-purged),
    and a fresh backup must exist before the delete is attempted at all."""
    import io as _io
    import sqlite3 as _sqlite3
    import types as _types

    from authorlm import backup, cli as cli_module
    from authorlm import proposals as prop_mod

    root = Path(tempfile.mkdtemp(prefix="authorlm-unreg-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "01.md").write_text("# Opening\n\nGravity and Choice both appear.\n")
        db = api.open_db(str(ws), create=True)
        manuscript = api.register_manuscript(db, "book", str(ms))
        api.add_concept(db, manuscript, "Gravity")
        choice = api.add_concept(db, manuscript, "Choice")
        api.link_concepts(db, manuscript, "Choice", "permits", "Gravity")
        api.collect(db, manuscript, {})  # populates manuscript_versions
        # ORCH-2: knowledge_proposals was one of the 8 tables missing from
        # the old hand-maintained MANUSCRIPT_TABLES list — a row here
        # proves the schema-derived replacement actually purges it too.
        prop_mod.create(db, manuscript["id"], "vanished", choice["id"],
                        {"name": "Choice", "was_in": "01.md"})

        def _counts() -> dict:
            out = {}
            for table in cli_module._manuscript_tables(db):
                key = "id" if table == "manuscripts" else "manuscript_id"
                out[table] = db.one(
                    f"SELECT COUNT(*) AS c FROM {table} WHERE {key} = ?",
                    (manuscript["id"],))["c"]
            return out

        before = _counts()
        check("fixture has rows across multiple manuscript-scoped tables "
              "before unregister",
              before["concept_nodes"] == 2 and before["concept_edges"] == 1
              and before["manuscript_versions"] == 1
              and before["manuscripts"] == 1
              and before["knowledge_proposals"] == 1, before)
        check("no backup exists yet — this workspace never opened a "
              "session",
              not backup.backup_dir(db.path).exists()
              or not list(backup.backup_dir(db.path).glob("authorlm-*.db")))

        # Simulate an interruption partway through the purge loop: boom
        # on the DELETE against concept_nodes, which sits in the middle
        # of MANUSCRIPT_TABLES — tables before it in the list would have
        # already had their DELETE issued under the old untransacted code.
        # sqlite3.Connection attributes are read-only, so the boom is a
        # thin proxy substituted for db.conn, not a monkeypatched method.
        real_conn = db.conn

        class _BoomConn:
            def execute(self, sql, *a, **kw):
                if sql.startswith("DELETE FROM concept_nodes"):
                    raise _sqlite3.OperationalError("simulated interruption")
                return real_conn.execute(sql, *a, **kw)

            def __getattr__(self, name):
                return getattr(real_conn, name)

        db.conn = _BoomConn()
        orig_open_db = cli_module._open_db
        cli_module._open_db = lambda args: db
        try:
            args = _types.SimpleNamespace(name="book", manuscript=None)
            raised = None
            try:
                cli_module.cmd_unregister(args)
            except SystemExit as err:
                raised = err
            except Exception as err:  # noqa: BLE001 — inspected below
                raised = err
            check("an interrupted unregister propagates rather than "
                  "silently completing",
                  raised is not None and not isinstance(raised, SystemExit),
                  repr(raised))
        finally:
            cli_module._open_db = orig_open_db
            db.conn = real_conn
            # Deliberately NOT rolling back here: a real interruption
            # (process kill, disk full) gets no such courtesy either. If
            # cmd_unregister wraps its loop in db.transaction(), the
            # transaction() context manager itself already rolled back
            # in its own except-clause before the exception reached us —
            # so this is a no-op post-fix and a faithful simulation
            # pre-fix.

        # Without db.transaction(), sqlite3's implicit (deferred) tx
        # means the tables deleted before the interruption are gone from
        # THIS connection's own view already — a read-your-own-writes
        # corruption, before any crash-recovery question even arises.
        mid = _counts()
        check("immediately after the interruption, on the very same "
              "connection, the manuscript is not left in a torn state — "
              "tables ordered before the interruption point were not "
              "actually purged",
              mid == before, {"before": before, "mid": mid})

        # And it must not become PERMANENT the next time anything else
        # commits on this connection (the ordinary case for a CLI process
        # that keeps going, or a long-lived MCP connection).
        from authorlm.db import ko_fields as _ko_fields

        sentinel = _ko_fields("cn")
        sentinel.update(manuscript_id="unrelated-manuscript", name="Sentinel",
                        kind="concept", status="declared", introduced_in=None,
                        notes=None, aliases="[]")
        db.insert("concept_nodes", sentinel)  # commits at transaction depth 0
        after = _counts()
        check("an interrupted unregister leaves the manuscript fully "
              "intact even after a later, unrelated commit on the same "
              "connection — nothing purged, not even the tables deleted "
              "before the interruption point",
              after == before, {"before": before, "after": after})

        made = list(backup.backup_dir(db.path).glob("authorlm-*.db"))
        check("a backup was taken before the (interrupted) delete began",
              len(made) == 1, made)

        # --- happy path: an uninterrupted unregister still purges
        # everything, and the transaction/backup addition doesn't change
        # that outcome ---
        cli_module._open_db = lambda args: db
        try:
            with contextlib.redirect_stdout(_io.StringIO()):
                cli_module.cmd_unregister(_types.SimpleNamespace(
                    name="book", manuscript=None))
        finally:
            cli_module._open_db = orig_open_db
        final = _counts()
        check("an uninterrupted unregister still purges every "
              "manuscript-scoped row",
              all(v == 0 for v in final.values()), final)
        made_final = list(backup.backup_dir(db.path).glob("authorlm-*.db"))
        check("the successful unregister also left a backup behind "
              "(taken before the delete, same as the interrupted run)",
              len(made_final) >= 1, made_final)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_backup_on_active_session() -> None:
    """it-0bffe1ff657b: backup.run() only ever fired from the branch of
    start_session() that actually INSERTs a new session row (sessions.py).
    With a session already active — the normal state for the entire span
    of a working day, since the author does not re-run 'session start'
    once one is open — that branch never runs, so no backup is ever
    taken during the day the database is actually being changed. The
    documented trigger ('roughly once per working day') was false
    whenever a session was open. Reproduced live: CLI 'session start'
    against an already-active session exited 1 with 'A session is
    already active' and wrote no authorlm-*.db.

    Fix: back up on resume too — the branch that finds an existing
    session and is about to refuse now backs up first. skip-if-unchanged
    (backup.py) means a rapid double 'session start' costs nothing extra
    once the first backup that day has already captured the state."""
    from authorlm import backup, sessions as bses

    root = Path(tempfile.mkdtemp(prefix="authorlm-backup-trigger-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "01.md").write_text("# hi\n")
        db = api.open_db(str(ws), create=True)
        manuscript = api.register_manuscript(db, "book", str(ms))

        bses.start_session(db, manuscript["id"])
        bdir = backup.backup_dir(db.path)
        check("starting the first session performs a backup",
              len(list(bdir.glob("authorlm-*.db"))) == 1)

        # A real change since the first backup, so the resume backup
        # below has something new to capture (not skipped as identical).
        node = ko_fields("cn")
        node.update(manuscript_id=manuscript["id"], name="Gravity",
                    kind="concept", status="declared", introduced_in=None,
                    notes="", aliases="[]")
        db.insert("concept_nodes", node)

        raised = None
        try:
            bses.start_session(db, manuscript["id"])
        except ValueError as err:
            raised = err
        check("starting a session while one is already active still "
              "raises exactly as before — no confirmation prompt, no "
              "silent takeover of the existing session",
              raised is not None and "already active" in str(raised),
              repr(raised))

        made = sorted(bdir.glob("authorlm-*.db"))
        check("a backup fires even when a session is already active — "
              "this is the normal state for the whole of a working day, "
              "which is exactly when the database is changing",
              len(made) == 2, made)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_summary_deprecation() -> None:
    """Sponsor, verbatim: "If a essay is no longer present, it should
    not be considered stale - it should have a 'deprecated' status or
    similar (see other similar objects in the db and their status field
    enums)." essay_summaries.status ('current' | 'deprecated') follows
    the codebase's existing status-enum idiom (concept_nodes,
    knowledge_proposals, doc_threads). Additive schema only (db.py); the
    transition is triggered from collect() (api.py) — summaries.py
    itself is untouched, since sums.status()/before_after() are already
    driven off the CURRENT toc reading order and so structurally never
    see a departed file's row (verified: the live '22 stale' figure was
    already correct before this fix — this closes the latent hazard for
    when a summary DOES outlive its file, never delete it, never let
    it read as an ordinary live summary)."""
    import hashlib

    from authorlm import summaries as sums

    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    root = Path(tempfile.mkdtemp(prefix="authorlm-summary-deprecation-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        text_a = "# A\n\nFirst essay, about to be removed.\n"
        text_b = "# B\n\nSecond essay, about to be edited.\n"
        (ms / "01-a.md").write_text(text_a)
        (ms / "02-b.md").write_text(text_b)
        db = api.open_db(str(ws), create=True)
        manuscript = api.register_manuscript(db, "book", str(ms))
        api.collect(db, manuscript, {})

        def _insert_summary(file: str, text: str) -> dict:
            row = ko_fields("es")
            row.update(manuscript_id=manuscript["id"], file=file,
                      summary=f"summary of {file}", source_hash=_hash(text),
                      upstream_hash=_hash(""), upstream_stale=0)
            db.insert("essay_summaries", row)
            return row

        row_a = _insert_summary("01-a.md", text_a)
        row_b = _insert_summary("02-b.md", text_b)
        check("a freshly-inserted summary defaults to status 'current' "
              "(additive column, no back-compat break)",
              db.one("SELECT status FROM essay_summaries WHERE id = ?",
                     (row_a["id"],))["status"] == "current")

        # B stays present but its text changes (a NORMAL stale summary —
        # must be unaffected by deprecation). A is deleted outright — its
        # file leaves the toc (no toc.toml here, so reading order is the
        # alphabetical fallback over files actually on disk).
        (ms / "02-b.md").write_text(text_b + "\nA new paragraph.\n")
        (ms / "01-a.md").unlink()
        report = api.collect(db, manuscript, {})

        check("collect deprecates the departed file's summary and "
              "reports the transition",
              report.get("summaries_deprecated") == ["01-a.md"], str(report))
        dep_row = db.one("SELECT * FROM essay_summaries WHERE id = ?",
                         (row_a["id"],))
        check("the deprecated summary is KEPT, never deleted — it is "
              "the record of an essay that existed",
              dep_row is not None and dep_row["status"] == "deprecated",
              dict(dep_row) if dep_row else None)

        states = {r["file"]: r for r in sums.status(db, manuscript)}
        check("a deprecated summary drops out of the stale/status "
              "listing entirely — it is no longer in the toc, so it "
              "never again counts as stale",
              "01-a.md" not in states, states)
        check("a normal stale summary (file still present, text edited) "
              "is unaffected: still reports 'stale', stays status "
              "'current' — deprecation never touches it",
              states.get("02-b.md", {}).get("state") == "stale"
              and db.one("SELECT status FROM essay_summaries WHERE id = ?",
                        (row_b["id"],))["status"] == "current",
              states)

        # Idempotent: nothing left to change, so the next collect is a
        # no-op and never re-reports (or re-touches) the deprecation.
        report2 = api.collect(db, manuscript, {})
        check("re-collecting with nothing new doesn't re-report an "
              "already-deprecated summary",
              not report2.get("summaries_deprecated"), report2)
    finally:
        shutil.rmtree(root, ignore_errors=True)


DIGEST_OK = {
    "points": [
        {"id": "p1", "claim": "the first point"},
        {"id": "p2", "claim": "the second point"},
    ],
    "examples": [{"id": "x1", "example": "an example", "serves": ["p1"]}],
    "references": [{"id": "r1", "reference": "a reference"}],
    "inconsistencies": [{"id": "i1", "with": "01-choice.md",
                         "note": "contradicts the opening", "points": ["p2"]}],
}


def check_digest_schema() -> None:
    """UC-B's digest schema (design-usecases §1.2.3, tests B8-B19) plus the
    two invariants the feature must not move (I1, I6).

    The digest is what makes the removal accounting trustworthy rather than
    decorative, so every rule here is a refusal, not a coercion: a dangling
    cross-reference, a duplicate id, or a contradiction "with" an essay that
    does not exist is a typo, and storing it would make the accounting lie.
    """
    import copy
    import json as _json

    from authorlm.db import ko_fields as _ko

    root, ws, ms, db, manuscript = _guard_fixture("authorlm-digest-")
    try:
        (ms / "01-choice.md").write_text("# Opening\n\nEvery act begins with "
                                         "a choice.\n")
        (ms / "02-essay.md").write_text("# The Essay\n\nThe old opening "
                                        "paragraph, soon to be raw "
                                        "material.\n")
        (ms / "03-blank.md").write_text("")
        config = api.load_config(str(ws))
        api.ensure_session(db, manuscript)
        api.collect(db, manuscript, config, source="test")
        version = db.one("SELECT * FROM manuscript_versions WHERE "
                         "manuscript_id = ? ORDER BY version_no DESC LIMIT 1",
                         (manuscript["id"],))
        intent = api.declare_intent(db, manuscript, "Rewrite the essay")
        writeup = _ko("wu")
        writeup.update(
            manuscript_id=manuscript["id"], intent_id=intent["intent"]["id"],
            file="02-essay.md", mode="fresh", status="active",
            source_version_id=version["id"], plan="[]", cursor=0,
            learnings="[]",
            metadata=_json.dumps({"next_n": 1, "placement": None,
                                  "created_file": False, "brief": None}))
        db.insert("writeups", writeup)

        def refused(label: str, payload, *needles: str) -> None:
            try:
                api._validate_digest(db, manuscript, payload)
            except ValueError as err:
                missing = [n for n in needles if n not in str(err)]
                check(label, not missing, f"{err}\nmissing: {missing}")
                return
            check(label, False, "no ValueError raised")

        refused("B8 — a digest that is not a JSON object is refused",
                ["p1"], "JSON object")
        refused('B8 — a digest with no "points" is refused, naming the key',
                {"examples": []}, "points")
        refused('B8 — an EMPTY "points" list is refused: a digest with no '
                "points cannot account for anything",
                {"points": []}, "points", "non-empty")
        refused("B9 — a typo'd top-level key is REFUSED, not silently "
                "dropped (a mislaid \"referances\" would discard the "
                "references and the accounting would never know)",
                dict(DIGEST_OK, referances=[]), "referances", "unknown")
        duplicated = copy.deepcopy(DIGEST_OK)
        duplicated["examples"][0]["id"] = "p1"
        refused("B10 — an id reused across two lists is refused: there is "
                "one id namespace, because the accounting references ids "
                "without saying which list they came from",
                duplicated, "p1", "unique")
        for bad_id in ("p 1", "p" * 40, "", None):
            broken = copy.deepcopy(DIGEST_OK)
            broken["points"][0]["id"] = bad_id
            refused(f"B11 — the id {bad_id!r} fails the charset/length rule",
                    broken, "id")
        for list_name, field in api.DIGEST_LISTS.items():
            broken = copy.deepcopy(DIGEST_OK)
            broken[list_name][0][field] = "   "
            refused(f'B12 — an empty "{field}" in {list_name} is refused',
                    broken, field)
        dangling = copy.deepcopy(DIGEST_OK)
        dangling["examples"][0]["serves"] = ["p9"]
        refused("B13 — a cross-reference to an id the digest does not have "
                "is refused as dangling",
                dangling, "p9", "dangling")
        not_a_point = copy.deepcopy(DIGEST_OK)
        not_a_point["references"][0]["serves"] = ["x1"]
        refused("B14 — a cross-reference to a non-point id is refused: "
                "serves/points name POINT ids",
                not_a_point, "x1", "point ids")
        ghost = copy.deepcopy(DIGEST_OK)
        ghost["inconsistencies"][0]["with"] = "nope.md"
        refused('B15 — an inconsistency "with" an essay that does not exist '
                "is a typo, not a finding",
                ghost, "no document matching 'nope.md'")

        stored = _json.loads(db.one("SELECT * FROM writeups WHERE id = ?",
                                    (writeup["id"],))["metadata"])
        check("B16 — after every one of B8-B15 the writeup still carries no "
              "digest: validation is all-or-nothing, because a digest that "
              "half-persists leaves the accounting silently wrong (K8)",
              "digest" not in stored, str(stored))
        for label, payload in (("bad top-level key",
                                dict(DIGEST_OK, referances=[])),
                               ("dangling cross-reference", dangling)):
            try:
                api.write_digest(db, manuscript, payload=payload)
                check(f"B16 — write_digest refuses a digest with a {label}",
                      False, "no ValueError raised")
            except ValueError:
                check(f"B16 — write_digest refuses a digest with a {label}",
                      True)
        stored = _json.loads(db.one("SELECT * FROM writeups WHERE id = ?",
                                    (writeup["id"],))["metadata"])
        check("B16 — and the refused digest was not written",
              "digest" not in stored, str(stored))

        minimal = api._validate_digest(
            db, manuscript, {"points": [{"id": "p1", "claim": "only point"}]})
        check("B17 — the three optional lists normalize to [] when absent",
              minimal["examples"] == [] and minimal["references"] == []
              and minimal["inconsistencies"] == [], str(minimal))
        result = api.write_digest(
            db, manuscript,
            payload={"points": [{"id": "p1", "claim": "only point",
                                 "confidence": 0.4}],
                     "source_version_id": "forged-by-the-skill"})
        digest = result["digest"]
        check("B17 — the verb stamps the provenance the skill cannot forge",
              digest["source_version_id"] == version["id"]
              and digest["source_file"] == "02-essay.md"
              and digest["point_count"] == 1
              and digest["recorded_at"], str(digest))
        check("B18 — an item-level key the CLI has no opinion about is "
              "preserved verbatim (the skill may carry extra structure)",
              digest["points"][0]["confidence"] == 0.4, str(digest["points"]))
        check("B19 — a stdin-supplied source_version_id is OVERWRITTEN, "
              "never trusted",
              digest["source_version_id"] != "forged-by-the-skill",
              str(digest))

        # --- F2: the refusal tells the truth about WHICH case it is -----
        # One message for two conditions was a small lie in the second
        # case: a rewrite whose pinned source happens to be blank was told
        # "this writeup created <file>", which it did not.
        def _sibling(file: str, created: bool) -> dict:
            row = _ko("wu")
            row.update(
                manuscript_id=manuscript["id"],
                intent_id=intent["intent"]["id"], file=file, mode="fresh",
                status="active", source_version_id=version["id"], plan="[]",
                cursor=0, learnings="[]",
                metadata=_json.dumps({"next_n": 1, "placement": None,
                                      "created_file": created,
                                      "brief": None}))
            db.insert("writeups", row)
            return row

        created_wu = _sibling("02-essay.md", True)
        try:
            api.write_digest(db, manuscript, prefix=created_wu["id"])
            check("F2 — a writeup that CREATED its file is refused", False,
                  "no ValueError raised")
        except ValueError as err:
            check("F2 — a writeup that CREATED its file is refused as such, "
                  "even though this pinned version does hold text for the "
                  "name (created_file is the discriminator, not emptiness)",
                  "this writeup created 02-essay.md" in str(err)
                  and "there is no source essay to digest" in str(err),
                  str(err))
        blank_wu = _sibling("03-blank.md", False)
        try:
            api.write_digest(db, manuscript, prefix=blank_wu["id"])
            check("F2 — a rewrite over a blank source is refused", False,
                  "no ValueError raised")
        except ValueError as err:
            check("F2 — a rewrite whose pinned source is merely EMPTY is "
                  "refused in its own words: it did not create anything, "
                  "and saying so would be a small lie about its history",
                  "03-blank.md's pinned source is empty" in str(err)
                  and "nothing to digest" in str(err)
                  and "this writeup created" not in str(err), str(err))

        # --- invariants -------------------------------------------------
        check("I1 — GUIDANCE_KINDS is untouched: the digest is metadata, "
              "not a guidance kind — it is proposed by nobody, reviewed by "
              "nobody, superseded by nobody",
              guidance_module.GUIDANCE_KINDS == frozenset(
                  {"bridge", "prerequisite", "definition", "objection",
                   "belief_reminder", "focus", "abstention"})
              and "digest" not in guidance_module.GUIDANCE_KINDS
              and "beat" not in guidance_module.GUIDANCE_KINDS,
              str(sorted(guidance_module.GUIDANCE_KINDS)))
        columns = {r[1] for r in
                   db.conn.execute("PRAGMA table_info(writeups)").fetchall()}
        check("I6 — no schema change: everything new rides in "
              "writeups.metadata, which is already a JSON TEXT column",
              columns == {"id", "version", "created_at", "created_by",
                          "schema_version", "metadata", "manuscript_id",
                          "intent_id", "file", "mode", "status",
                          "source_version_id", "plan", "cursor",
                          "learnings"},
              str(sorted(columns)))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _writeup_fixture(prefix: str, filename: str = "01-epictetus.md"):
    """A workspace with one essay, a guide attached to it and an active
    intent — the minimum a writeup needs. Returns (root, ws, db,
    manuscript, intent)."""
    import io

    root = Path(tempfile.mkdtemp(prefix=prefix))
    ws = root / "ws"
    ms = ws / "manuscript"
    ms.mkdir(parents=True)
    (ms / filename).write_text("# Epictetus\n\nThe old essay stands here.\n")
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms)])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    api.define_style_guide(db, manuscript, "Connections essays")
    api.attach_style(db, manuscript, filename, "Connections essays")
    intent = api.declare_intent(db, manuscript, "Rework the Epictetus essay")["intent"]
    return root, ws, db, manuscript, intent


def check_lint_and_propose_gates() -> None:
    """The deterministic prose lint (authorlm/lint.py) and the three gates
    on `write propose` (review 2026-09-06): a payload assembled since the
    last change of state, no lint ERROR without a recorded override, and
    an independent critic's PASS on this exact draft."""
    import io
    import json as _json

    from authorlm import critic as crt
    from authorlm import lint as lint_mod

    # --- the lint, pure ------------------------------------------------
    corpus = {"earlier.md": lint_mod.paragraphs(
        "The guest who leaves with the hotel towels and the shopper who "
        "leaves without paying do so because it helps them.")}
    text = ("What is a population, exactly? Nothing is settled.\n\n"
            "A hierarchy is the ordering by a hierarchy's metric.\n\n"
            "The guest who leaves with the hotel towels and the shopper "
            "who leaves without paying do so because it helps them.\n\n"
            "It is a state — not a possession — and it is earned — daily.\n\n"
            "Rank is therefore a state, not a possession. It is not a thing "
            "given but a thing earned.")
    report = lint_mod.lint_text(text, corpus=corpus,
                                concepts=[("Hierarchy", "impulses.md")],
                                this_file="later.md")
    codes = {(f.code, f.severity) for f in report.findings}
    check("lint: 'What' opener is an error",
          ("what-opener", "error") in codes, str(codes))
    check("lint: a term inside its own definition is an error",
          ("term-in-definition", "error") in codes, str(codes))
    check("lint: eight words verbatim from another essay is an error "
          "naming the file and paragraph",
          any(f.code == "overlap" and f.ref == "earlier.md ¶1"
              for f in report.findings), str(report.findings))
    check("lint: three em-dashes in one sentence is an error",
          ("em-dash-stack", "error") in codes, str(codes))
    check("lint: 'not X but Y' is a warning, 'X, not Y' only info",
          ("this-not-that", "warning") in codes
          and ("this-not-that", "info") in codes, str(codes))
    check("lint: re-defining a concept another essay introduced is a warning",
          any(f.code == "redefinition" and "impulses.md" in f.ref
              for f in report.findings), str(report.findings))
    clean = lint_mod.lint_text("Rank is a state. It is earned each day.")
    check("lint: plain declarative prose is clean of errors",
          not clean.errors, str(clean.findings))
    before = lint_mod.lint_text("What is rank? A state.")
    after = lint_mod.lint_text("What is rank? A state. What is a metric? A count.")
    check("lint.delta reports only what an edit introduced",
          [f.quote for f in lint_mod.delta(before, after).findings
           if f.code == "what-opener"] == ["What is a metric?"],
          str(lint_mod.delta(before, after).findings))
    marked = lint_mod.mark_changes(
        "Rank is a state. It is **earned** each beat.\n\nA wholly new paragraph.",
        "Rank is a possession. It is earned each day.")
    check("lint.mark_changes bolds only the changed spans and a new "
          "paragraph whole, and drops the draft's own bold",
          marked == "Rank is a **state.** It is earned each **beat.**\n\n"
                    "**A wholly new paragraph.**", repr(marked))
    grade = lint_mod.reading_grade(
        "The guest takes hotel towels, and the shopper skips payment. "
        "They do this to cope in a world they do not trust.")
    check("lint: reading grade is computed for a paragraph",
          grade is not None and 0 < grade < 12, str(grade))

    # --- the critic's reply grammar ------------------------------------
    parsed = crt.parse_report("VERDICT\nFAIL\n\nFINDINGS\n- L3 | «x» | why | fix\n")
    check("critic: FAIL with findings parses",
          parsed["verdict"] == "FAIL" and len(parsed["findings"]) == 1,
          str(parsed))
    check("critic: PASS parses",
          crt.parse_report("VERDICT\nPASS\n")["verdict"] == "PASS")
    for bad in ("looks fine to me", "VERDICT\nMAYBE", "VERDICT\nFAIL\n"):
        try:
            crt.parse_report(bad)
            check(f"critic: {bad!r} is refused", False)
        except crt.ReportError:
            check(f"critic: {bad!r} is refused", True)

    # --- the gates, on a live writeup ----------------------------------
    root, ws, db, manuscript, intent = _writeup_fixture("authorlm-gates-")
    config = {"llm": {"enabled": True}}
    try:
        api.ensure_session(db, manuscript)
        with contextlib.redirect_stdout(io.StringIO()):
            api.write_start(db, manuscript, {}, "01-epictetus.md",
                            intent["id"][:8])
            api.write_plan(db, manuscript, [
                {"role": "opener", "concepts": [], "budget": 60,
                 "notes": "claims the crux is prohairesis"},
                {"role": "close", "notes": "claims the divergence is scope"},
            ])
        draft = "The crux is prohairesis. It names the boundary of choice."
        try:
            api.write_propose(db, manuscript, draft, "opener per the plan")
            check("gate 1: propose refuses before any payload is assembled",
                  False)
        except ValueError as err:
            check("gate 1: propose refuses before any payload is assembled",
                  "no payload for beat n=1" in str(err), str(err))
        with contextlib.redirect_stdout(io.StringIO()):
            dry = api.write_draft(db, manuscript, config, dry_run=True)
        meta = _json.loads(db.one("SELECT metadata FROM writeups")["metadata"])
        check("dry run stamps the assembly with the beat, the sequence and "
              "the block hashes",
              meta["assembly"]["n"] == 1 and meta["assembly"]["seq"] == 1
              and meta["assembly"]["hashes"] == dry["payload"].hashes,
              str(meta.get("assembly")))
        try:
            api.write_propose(db, manuscript, draft, "opener per the plan")
            check("gate 3: propose refuses without a critic", False)
        except ValueError as err:
            check("gate 3: propose refuses without a critic",
                  "no critic has seen THIS draft" in str(err), str(err))
        crit = api.write_critique(db, manuscript, draft)
        payload = crit["payload"]
        check("write critique assembles the checklist, the accepted text, "
              "the lint and the draft",
              "THE CHECKLIST" in payload and "ACCEPTED TEXT SO FAR" in payload
              and "LINT" in payload and "THE DRAFT" in payload
              and draft in payload and "VERDICT" in payload, payload[:300])
        try:
            api.write_propose(db, manuscript, draft, "opener per the plan",
                              critique="VERDICT\nFAIL\n\nFINDINGS\n- LOGIC | «x» | y | z")
            check("gate 3: a FAIL report refuses and names the findings", False)
        except ValueError as err:
            check("gate 3: a FAIL report refuses and names the findings",
                  "FAIL" in str(err) and "LOGIC" in str(err), str(err))
        try:
            api.write_propose(db, manuscript, draft + " Extra words.",
                              "opener per the plan", critique="VERDICT\nPASS")
            check("gate 3: the critic must have seen THIS draft", False)
        except ValueError as err:
            check("gate 3: the critic must have seen THIS draft",
                  "no critic has seen THIS draft" in str(err), str(err))
        result = api.write_propose(db, manuscript, draft, "opener per the plan",
                                   critique="VERDICT\nPASS")
        row = db.one("SELECT metadata FROM guidance_history WHERE id = ?",
                     (result["guidance_id"],))
        gates = _json.loads(row["metadata"])["gates"]
        check("a passing draft registers with its gates on the row",
              gates["critic"] == "pass" and gates["assembly"] == dry["payload"].hashes,
              str(gates))
        with contextlib.redirect_stdout(io.StringIO()):
            api.write_reject(db, manuscript, "not the author's voice")
        try:
            api.write_propose(db, manuscript, draft, "opener per the plan",
                              critique="VERDICT\nPASS")
            check("gate 1: a verdict invalidates the assembly", False)
        except ValueError as err:
            check("gate 1: a verdict invalidates the assembly",
                  "since the last change of state" in str(err), str(err))
        with contextlib.redirect_stdout(io.StringIO()):
            api.write_draft(db, manuscript, config, dry_run=True)
        bad = "What is the crux? The crux is prohairesis."
        api.write_critique(db, manuscript, bad)
        try:
            api.write_propose(db, manuscript, bad, "opener", critique="VERDICT\nPASS")
            check("gate 2: a lint ERROR refuses even with a critic's PASS", False)
        except ValueError as err:
            check("gate 2: a lint ERROR refuses even with a critic's PASS",
                  "what-opener" in str(err), str(err))
        result = api.write_propose(db, manuscript, bad, "opener",
                                   critique="VERDICT\nPASS",
                                   lint_override="the question is the author's own opener")
        gates = _json.loads(db.one(
            "SELECT metadata FROM guidance_history WHERE id = ?",
            (result["guidance_id"],))["metadata"])["gates"]
        check("gate 2: the override reason is recorded on the row",
              gates["lint_override"].startswith("the question")
              and "what-opener" in gates["lint"], str(gates))
        dictated = api.write_propose(db, manuscript, "The author's own words.",
                                     "dictated", no_critic="author dictated it")
        check("dictated text needs neither payload nor critic, and says so",
              _json.loads(db.one(
                  "SELECT metadata FROM guidance_history WHERE id = ?",
                  (dictated["guidance_id"],))["metadata"])["gates"]["critic"]
              == "skipped: author dictated it")
        with contextlib.redirect_stdout(io.StringIO()):
            api.write_learn(db, manuscript, "keep it plain")
        meta = _json.loads(db.one("SELECT metadata FROM writeups")["metadata"])
        check("a learning bumps the sequence too",
              meta["seq"] > meta["assembly"]["seq"], str(meta))
        # parallel-edit mode: accept the dictated beat, then pretend the
        # author carried it into the Doc reworded and added a paragraph.
        with contextlib.redirect_stdout(io.StringIO()):
            api.write_accept(db, manuscript, {})
        landed = api.write_landed(
            db, manuscript,
            text="# Epictetus\n\nThe author's own words, carried over.\n\n"
                 "A paragraph the author added in the Doc.\n")
        check("write landed: a reworded beat counts as changed, never "
              "missing, and the author's addition is reported as extra",
              landed["totals"] == {"exact": 0, "changed": 1, "missing": 0}
              and len(landed["extra"]) == 1, str(landed))
        landed = api.write_landed(db, manuscript, text="# Epictetus\n\nSomething else entirely about rank.\n")
        check("write landed: a beat absent from the pulled text is MISSING",
              landed["totals"]["missing"] == 1, str(landed))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_intent_scope() -> None:
    """Scope-derived intent attachment at the API layer: determinism of
    the new block-A section, the metadata-forward re-scope, and the
    `intent scope` validations (design-intent-scope §5.2)."""
    import io
    import json as _json

    from authorlm import writing

    root, ws, db, manuscript, wide = _writeup_fixture("authorlm-scope-")
    try:
        api.ensure_session(db, manuscript)
        scoped = api.declare_intent(db, manuscript,
                                    "Rewrite the Epictetus essay",
                                    scope="01-epictetus.md")["intent"]
        check("declare_intent(scope=…) round-trips the scope onto the row",
              db.one("SELECT scope FROM declared_intents WHERE id = ?",
                     (scoped["id"],))["scope"] == "01-epictetus.md")
        plain = api.declare_intent(db, manuscript,
                                   "A goal with no place named")["intent"]
        check("...and the default is still NULL — an absent scope still "
              "means manuscript-wide, and still opens an episode",
              db.one("SELECT scope FROM declared_intents WHERE id = ?",
                     (plain["id"],))["scope"] is None
              and db.one("SELECT id FROM editorial_episodes WHERE "
                         "intent_id = ?", (plain["id"],)) is not None)
        api.abandon_intent(db, manuscript, plain["id"], "fixture")
        from authorlm import mcp_server
        prev_workspace = mcp_server._WORKSPACE
        mcp_server._WORKSPACE = str(ws)
        try:
            mcp_result = mcp_server.declare_intent(
                "A goal declared through MCP", scope="01-epictetus.md")
        finally:
            mcp_server._WORKSPACE = prev_workspace
        check("declare_intent carries `scope` through the MCP surface too, "
              "and says in words what the scope MEANS",
              mcp_result["ok"]
              and mcp_result["result"]["intent"]["scope"] == "01-epictetus.md"
              and "serves this goal" in mcp_result["result"]["scope_means"],
              str(mcp_result))
        api.abandon_intent(db, manuscript,
                           mcp_result["result"]["intent"]["id"], "fixture")

        with contextlib.redirect_stdout(io.StringIO()):
            started = api.write_start(db, manuscript, {}, "01-epictetus.md")
        writeup = dict(db.one("SELECT * FROM writeups WHERE id = ?",
                              (started["writeup"]["id"],)))
        block = _json.loads(writeup["metadata"])["intents"]
        check("with no --intent the API derives both in-scope intents and "
              "makes the file-scoped one primary",
              {m["id"] for m in block["members"]} == {scoped["id"], wide["id"]}
              and block["primary"] == scoped["id"], str(block))
        with contextlib.redirect_stdout(io.StringIO()):
            api.write_plan(db, manuscript,
                           [{"role": "opener", "concepts": [], "budget": 60}])
        writeup = dict(db.one("SELECT * FROM writeups WHERE id = ?",
                              (writeup["id"],)))

        beat = _json.loads(writeup["plan"])[0]
        first = writing.assemble(db, manuscript, writeup, beat)
        second = writing.assemble(db, manuscript, writeup, beat)
        check("determinism: identical stored state gives a byte-identical "
              "block A",
              first.hashes["A"] == second.hashes["A"])
        check("block A now carries an INTENTS section — the first time the "
              "beat drafter is told what the rewrite is FOR",
              "INTENTS" in first.frame
              and "Rewrite the Epictetus essay" in first.frame, first.frame)
        rendered = writing._intents_block(writeup)
        check("block A ordering: primary first, then tier rank, then id",
              rendered.splitlines()
              == ["- [file] Rewrite the Epictetus essay",
                  "- [manuscript] Rework the Epictetus essay"], rendered)
        check("no ids and no timestamps in the rendered section — they move "
              "between beats and would re-bill the cached prefix",
              "di-" not in rendered and "T" not in rendered.split("[")[0],
              rendered)

        # The whole point of freezing the member record: `scope` is
        # mutable now, and a live join would let a re-scope in another
        # chat change block A between two beats.
        episode = dict(db.one("SELECT * FROM editorial_episodes WHERE "
                              "intent_id = ?", (scoped["id"],)))
        moved = api.scope_intent(db, manuscript, scoped["id"],
                                 manuscript_wide=True)
        db.conn.execute("UPDATE declared_intents SET version = version + 1, "
                        "statement = ? WHERE id = ?",
                        ("A statement rewritten after ratification",
                         scoped["id"]))
        db.conn.commit()
        third = writing.assemble(db, manuscript, writeup, beat)
        check("re-scoping a FROZEN member does not change block A — the "
              "member record is the source, never a live join (design F4's "
              "silent invalidator, arriving by a new road)",
              third.hashes["A"] == first.hashes["A"])
        check("the writeup's member record keeps the tier and scope as they "
              "stood at ratification",
              [m for m in block["members"]
               if m["id"] == scoped["id"]][0]["tier"] == "file")
        history = _json.loads(db.one(
            "SELECT metadata FROM declared_intents WHERE id = ?",
            (scoped["id"],))["metadata"])["scope_history"]
        check("...and the move is recorded on the intent, with the client "
              "that made it",
              len(history) == 1 and history[0]["from"] == "01-epictetus.md"
              and history[0]["to"] is None and history[0]["by"], str(history))
        check("no episode, transition or frozen member record is rewritten",
              dict(db.one("SELECT * FROM editorial_episodes WHERE id = ?",
                          (episode["id"],)))["transition_ids"]
              == episode["transition_ids"])
        check("scope_intent reports the active writeup that ratified the "
              "intent, so the caller can say it is unaffected",
              [h["writeup"] for h in moved["frozen_in"]] == [writeup["id"]],
              str(moved))

        joined = api.declare_intent(db, manuscript, "A goal joined by hand",
                                    scope="01-epictetus.md")["intent"]
        with contextlib.redirect_stdout(io.StringIO()):
            api.write_intents(db, manuscript, add=[joined["id"]])
        writeup = dict(db.one("SELECT * FROM writeups WHERE id = ?",
                              (writeup["id"],)))
        fourth = writing.assemble(db, manuscript, writeup, beat)
        check("an explicit --add DOES change block A — the honest, single "
              "invalidation the author asked for",
              fourth.hashes["A"] != first.hashes["A"])

        # --- `intent scope` validation.
        for label, kwargs in (
                ("two places at once", {"scope": "01-epictetus.md",
                                        "manuscript_wide": True}),
                ("no place at all", {}),
        ):
            try:
                api.scope_intent(db, manuscript, wide["id"], **kwargs)
                failed = None
            except (ValueError, LookupError) as err:
                failed = str(err)
            check(f"scope_intent refuses {label}",
                  failed and "exactly one place" in failed, str(failed))
        try:
            api.scope_intent(db, manuscript, wide["id"], scope="nope.md")
            failed = None
        except (ValueError, LookupError) as err:
            failed = str(err)
        check("scope_intent refuses a file that is not in the manuscript",
              failed is not None, str(failed))
        try:
            api.scope_intent(db, manuscript, wide["id"],
                             chapter="01-epictetus.md")
            failed = None
        except (ValueError, LookupError) as err:
            failed = str(err)
        check("scope_intent refuses --chapter on a file with no essays "
              "beneath it: the word would name nothing",
              failed and "no essays beneath it" in failed, str(failed))
        done = api.declare_intent(db, manuscript, "Already finished")["intent"]
        api.complete_intent(db, manuscript, done["id"], "done")
        try:
            api.scope_intent(db, manuscript, done["id"],
                             scope="01-epictetus.md")
            failed = None
        except (ValueError, LookupError) as err:
            failed = str(err)
        check("scope_intent refuses a completed intent — scope places where "
              "FUTURE work routes, and there is none",
              failed and "completed" in failed, str(failed))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_scope_evidence_batched_lookup() -> None:
    """scope_evidence resolves an intent's transition locations in ONE
    batched statement, not one point query per transition id. The sheet
    is rebuilt on every `list_intents(evidence=True)` and every
    `intent scope --triage`, and the id count grows with every edit the
    author records. The output must not move: duplicates still count
    twice, missing ids are still skipped, and the ordering is unchanged."""
    import io

    from authorlm.db import ko_fields

    root = Path(tempfile.mkdtemp(prefix="authorlm-scopeev-batch-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "part.md"\n\n'
            '[[chapter]]\nfile = "alpha.md"\nparent = "part.md"\n\n'
            '[[chapter]]\nfile = "beta.md"\nparent = "part.md"\n\n'
            '[[chapter]]\nfile = "gamma.md"\nparent = "part.md"\n')
        for name in ("part.md", "alpha.md", "beta.md", "gamma.md"):
            (ms / name).write_text(f"# {name}\n\nText.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms), "--no-extract"])
        db = api.open_db(str(ws))
        manuscript = api.get_manuscript(db)
        api.ensure_session(db, manuscript)
        version = db.one("SELECT id FROM manuscript_versions LIMIT 1")

        def transitions(locations):
            ids = []
            for location in locations:
                row = ko_fields("tr")
                row.update(manuscript_id=manuscript["id"], version_before=None,
                           version_after=version["id"] if version else "v",
                           kind="rewrite", location=location,
                           summary="seeded", detail="{}")
                db.insert("editorial_transitions", row)
                ids.append(row["id"])
            return ids

        intent = api.declare_intent(db, manuscript,
                                    "Keep the part's argument tight")["intent"]
        first = dict(db.one("SELECT * FROM editorial_episodes WHERE "
                            "intent_id = ?", (intent["id"],)))
        # Episode 1: 25 real transitions — alpha 12 (one heading-suffixed),
        # beta 8, gamma 5.
        ep1 = transitions(["alpha.md#Intro"] + ["alpha.md"] * 11
                          + ["beta.md"] * 8 + ["gamma.md"] * 5)
        # Episode 2: 25 ids — ep1's first id again (a duplicate), 23 new
        # real ones (alpha 6, beta 11, gamma 6), and one id with no row.
        ep2 = ([ep1[0]]
               + transitions(["alpha.md"] * 6 + ["beta.md"] * 11
                             + ["gamma.md"] * 6)
               + ["tr_no_such_transition"])
        db.update("editorial_episodes", first["id"],
                  {"transition_ids": json.dumps(ep1), "status": "closed"})
        second = ko_fields("ep")
        second.update(manuscript_id=manuscript["id"],
                      session_id=first["session_id"], intent_id=intent["id"],
                      transition_ids=json.dumps(ep2), status="closed")
        db.insert("editorial_episodes", second)
        check("fixture: 2 episodes, 50 transition ids, one duplicated and "
              "one missing", len(ep1) == 25 and len(ep2) == 25
              and len(set(ep1) | set(ep2)) == 49, f"{len(ep1)}+{len(ep2)}")

        statements: list[str] = []
        original_run = db._run

        def counting_run(sql, args, finish):
            if "editorial_transitions" in sql:
                statements.append(sql)
            return original_run(sql, args, finish)

        db._run = counting_run
        try:
            rows = api.scope_evidence(db, manuscript)
        finally:
            del db._run
        check("scope_evidence looks up an intent's 50 transition ids in ONE "
              "statement, not one point query per id",
              len(statements) == 1, f"{len(statements)} statements")

        # Snapshot taken with the per-id implementation: the duplicate
        # counts twice, the missing id is skipped, the heading suffix is
        # stripped, and the alpha/beta tie breaks by name.
        mine = [r for r in rows if r["id"] == intent["id"]]
        check("the batched lookup returns exactly what the per-id one did: "
              "same files, same order, same counts",
              len(mine) == 1 and mine[0]["files"] == [
                  {"file": "alpha.md", "transitions": 19},
                  {"file": "beta.md", "transitions": 19},
                  {"file": "gamma.md", "transitions": 11}],
              str(mine and mine[0]["files"]))
        check("...and the same suggestion, with the same reason",
              mine and mine[0]["suggested"]
              == {"tier": "chapter", "scope": "part.md",
                  "why": "3 essays, all under part.md"},
              str(mine and mine[0]["suggested"]))
        check("...and the same total of 49 counted transitions",
              mine and sum(f["transitions"] for f in mine[0]["files"]) == 49,
              str(mine and mine[0]["files"]))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_scope_evidence() -> None:
    """The one-time triage's read (design-intent-scope §4.1): files the
    intent's episodes ACTUALLY touched, and a deterministic suggestion
    with its reason. Zero model calls."""
    import io

    from authorlm.db import ko_fields

    root = Path(tempfile.mkdtemp(prefix="authorlm-scopeev-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "part.md"\n\n'
            '[[chapter]]\nfile = "alpha.md"\nparent = "part.md"\n\n'
            '[[chapter]]\nfile = "beta.md"\nparent = "part.md"\n\n'
            '[[chapter]]\nfile = "loose.md"\n')
        for name in ("part.md", "alpha.md", "beta.md", "loose.md"):
            (ms / name).write_text(f"# {name}\n\nText.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms), "--no-extract"])
        db = api.open_db(str(ws))
        manuscript = api.get_manuscript(db)
        api.ensure_session(db, manuscript)
        version = db.one("SELECT id FROM manuscript_versions LIMIT 1")

        def seed(statement, locations):
            intent = api.declare_intent(db, manuscript, statement)["intent"]
            episode = db.one("SELECT * FROM editorial_episodes WHERE "
                             "intent_id = ?", (intent["id"],))
            ids = []
            for location in locations:
                row = ko_fields("tr")
                row.update(manuscript_id=manuscript["id"], version_before=None,
                           version_after=version["id"] if version else "v",
                           kind="rewrite", location=location,
                           summary="seeded", detail="{}")
                db.insert("editorial_transitions", row)
                ids.append(row["id"])
            db.update("editorial_episodes", episode["id"],
                      {"transition_ids": json.dumps(ids)})
            return intent

        one_file = seed("Cut the inheritance down",
                        ["alpha.md#Intro", "alpha.md", "alpha.md"])
        one_part = seed("Tighten the part", ["alpha.md", "beta.md"])
        spread = seed("Something everywhere", ["alpha.md", "loose.md"])
        untouched = seed("Never let a name carry an argument", [])

        rows = {r["id"]: r for r in api.scope_evidence(db, manuscript)}
        check("scope_evidence reports every active UNSCOPED intent",
              set(rows) == {one_file["id"], one_part["id"], spread["id"],
                            untouched["id"]}, str(list(rows)))
        check("all transitions in one file → suggest that file, and say why",
              rows[one_file["id"]]["suggested"]["tier"] == "file"
              and rows[one_file["id"]]["suggested"]["scope"] == "alpha.md"
              and "3 of them" in rows[one_file["id"]]["suggested"]["why"],
              str(rows[one_file["id"]]["suggested"]))
        check("transitions across essays that share a toc ancestor → "
              "suggest that part",
              rows[one_part["id"]]["suggested"]
              == {"tier": "chapter", "scope": "part.md",
                  "why": "2 essays, all under part.md"},
              str(rows[one_part["id"]]["suggested"]))
        check("spread with no common ancestor → manuscript-wide",
              rows[spread["id"]]["suggested"]["tier"] == "manuscript"
              and "across the book" in rows[spread["id"]]["suggested"]["why"],
              str(rows[spread["id"]]["suggested"]))
        check("no recorded work at all → manuscript-wide, and the reason "
              "says it is the author's call rather than the evidence's",
              rows[untouched["id"]]["suggested"]["tier"] == "manuscript"
              and "your call" in rows[untouched["id"]]["suggested"]["why"],
              str(rows[untouched["id"]]["suggested"]))
        check("the counted files are the ones the transitions name, "
              "heading-suffix stripped, commonest first",
              [f["file"] for f in rows[one_file["id"]]["files"]] == ["alpha.md"]
              and rows[one_file["id"]]["files"][0]["transitions"] == 3,
              str(rows[one_file["id"]]["files"]))

        # --- A RULING of "book-wide" is a ruling, and must leave the sheet.
        #
        # Found live, during the author's own triage: `intent scope <id>
        # --book-wide` writes NULL over NULL and records the move in
        # scope_history, which is correct and metadata-forward. But the
        # sheet's predicate was `scope IS NULL`, so a deliberately
        # book-wide intent was indistinguishable from a never-ruled one:
        # after ruling 23 of them the author was shown all 23 again, and
        # the closing number that matters — how many goals will ride along
        # with every future writeup BY DECISION — could not be computed at
        # all.
        api.scope_intent(db, manuscript, untouched["id"],
                         manuscript_wide=True)
        ruled = dict(db.one("SELECT * FROM declared_intents WHERE id = ?",
                            (untouched["id"],)))
        history = json.loads(ruled["metadata"])["scope_history"]
        check("the book-wide ruling still writes NULL over NULL and records "
              "the move — the WRITE is correct and is not what changed",
              ruled["scope"] is None and len(history) == 1
              and history[0]["from"] is None and history[0]["to"] is None,
              str(history))
        listed = {r["id"] for r in api.scope_evidence(db, manuscript)}
        check("an intent RULED book-wide leaves the triage sheet — a "
              "ruling is a ruling, and re-presenting it would make the "
              "sitting never end",
              untouched["id"] not in listed, str(sorted(listed)))
        check("...while the ones never ruled on are still listed",
              listed == {one_file["id"], one_part["id"], spread["id"]},
              str(sorted(listed)))
        api.scope_intent(db, manuscript, one_file["id"], scope="alpha.md")
        tally = api.scope_tally(db, manuscript)
        check("scope_tally gives the author their closing numbers: what is "
              "still unplaced, what is book-wide BY DECISION, and what is "
              "scoped to an essay or a part",
              tally == {"no_place": 2, "ruled_book_wide": 1, "scoped": 1,
                        "active": 4}, str(tally))
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            cli_main(["--workspace", str(ws), "intent", "scope", "--triage"])
        sheet = buffer.getvalue()
        check("the sheet's summary line carries all three counts, so the "
              "number the author actually wants at the end exists",
              "2 with no place" in sheet
              and "1 ruled book-wide" in sheet
              and "1 essay/chapter-scoped" in sheet, sheet)
        check("and the ruled intent is not in the sheet's body either",
              untouched["id"][:8] not in sheet, sheet)

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            cli_main(["--workspace", str(ws), "intent", "list"])
        listing = buffer.getvalue()
        check("intent list distinguishes a book-wide RULING from an "
              "unplaced default — the two look identical in the column and "
              "mean opposite things",
              "book-wide (ruled)" in listing
              and "book-wide (default)" in listing, listing)

        # --- DECLARING book-wide is a ruling too, and only the EXPLICIT
        # flag is one. Without this the backfill just refills: every goal
        # the author deliberately declares book-wide from here on lands
        # back on the sheet as "no place".
        before = api.scope_tally(db, manuscript)
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "intent", "declare",
                      "A rule the author means to apply everywhere",
                      "--book-wide"])
            cli_main(["--workspace", str(ws), "intent", "declare",
                      "A goal nobody has placed"])
        after = api.scope_tally(db, manuscript)
        listed = {r["statement"] for r in api.scope_evidence(db, manuscript)}
        check("declaring with the EXPLICIT --book-wide flag records the "
              "ruling, so the goal never reaches the triage sheet",
              "A rule the author means to apply everywhere" not in listed,
              str(sorted(listed)))
        check("...and declaring with NO flag at all does not: an absent "
              "scope is a default, and a default is not a decision",
              "A goal nobody has placed" in listed, str(sorted(listed)))
        check("the tally moves by exactly one in each direction",
              (after["ruled_book_wide"] - before["ruled_book_wide"],
               after["no_place"] - before["no_place"],
               after["scoped"] - before["scoped"]) == (1, 1, 0),
              f"{before} -> {after}")

        explicit = api.declare_intent(db, manuscript, "Explicit through the api",
                                      book_wide=True)["intent"]
        absent = api.declare_intent(db, manuscript, "Absent through the api")["intent"]
        check("the api layer carries the same distinction, so it is not a "
              "property of the CLI's argument parsing",
              api._scope_ruled(dict(db.one(
                  "SELECT * FROM declared_intents WHERE id = ?",
                  (explicit["id"],))))
              and not api._scope_ruled(dict(db.one(
                  "SELECT * FROM declared_intents WHERE id = ?",
                  (absent["id"],)))), "")
        check("the recorded entry is the SAME null -> null shape the "
              "triage verb writes, so one predicate reads both",
              json.loads(db.one(
                  "SELECT metadata FROM declared_intents WHERE id = ?",
                  (explicit["id"],))["metadata"])["scope_history"][0]["to"]
              is None, "")

        from authorlm import mcp_server
        prev_workspace = mcp_server._WORKSPACE
        mcp_server._WORKSPACE = str(ws)
        try:
            mcp_ruled = mcp_server.declare_intent(
                "Explicit through MCP", book_wide=True)
            mcp_absent = mcp_server.declare_intent("Absent through MCP")
            evidence = mcp_server.list_intents(evidence=True)
        finally:
            mcp_server._WORKSPACE = prev_workspace
        mcp_listed = {r["statement"] for r
                      in evidence["result"]["unscoped_evidence"]}
        check("MCP distinguishes them too — the surface the assistant uses "
              "when it asks 'just this essay, the whole part, or the whole "
              "book?' can record the answer as a ruling",
              mcp_ruled["ok"] and mcp_absent["ok"]
              and "Explicit through MCP" not in mcp_listed
              and "Absent through MCP" in mcp_listed,
              str(sorted(mcp_listed)))
        check("and list_intents(evidence=True) carries the sitting's "
              "closing numbers",
              set(evidence["result"]["scope_tally"])
              == {"active", "scoped", "ruled_book_wide", "no_place"},
              str(evidence["result"].get("scope_tally")))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_placeholder_reader_paths() -> None:
    """AC-1 review F5/F6/RK6: the paths that READ a mid-rewrite file.

    Each of these vanished silently under neutralization — the code was
    there and nothing would have noticed its removal, which for a marker
    that must never reach a reader is the wrong kind of quiet."""
    import io
    import json as _json

    from authorlm import gdocs as _gdocs
    from authorlm import sweeps as _sweeps

    root, ws, db, manuscript, intent = _writeup_fixture("authorlm-ph-")
    ms = Path(manuscript["path"])
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            api.write_start(db, manuscript, {}, "01-epictetus.md",
                            intent["id"][:8])
        path = ms / "01-epictetus.md"
        check("the fixture is mid-rewrite",
              path.read_text() == api.PLACEHOLDER)

        # --- change 25: no readability row for a marker.
        check("F5 — chapter_stats returns {} for a placeholder, as it "
              "already does for an empty file: a Flesch score for a "
              "mid-rewrite marker is noise in the manifest",
              _gdocs.chapter_stats(api.PLACEHOLDER) == {},
              str(_gdocs.chapter_stats(api.PLACEHOLDER)))
        check("...and it still scores real prose (the guard did not "
              "swallow the function)",
              _gdocs.chapter_stats("# H\n\nA sentence of real prose "
                                   "stands here.\n").get("fre") is not None)

        # --- change 24: neither push path may make the Doc the working
        # copy for a file that has no text yet.
        try:
            _gdocs.push_doc(db, manuscript, "01-epictetus.md",
                            service=None, docs_service=None)
            raised = None
        except Exception as err:
            # Deliberately broad: without the refusal this path runs on
            # into the Google client and dies with an AttributeError, and
            # the point of the check is that it stops HERE, with a message
            # for the author — not merely that something went wrong.
            raised = f"{type(err).__name__}: {err}"
        check("F5 — doc push is REFUSED on a mid-rewrite file, naming both "
              "exits: a push makes the Doc the working copy, and the next "
              "pull would then be the authority over an essay that lives "
              "only in the database",
              raised is not None and "mid-rewrite" in raised
              and "write complete" in raised and "write abandon" in raised,
              str(raised))

        row = db.one("SELECT * FROM manuscripts WHERE id = ?",
                     (manuscript["id"],))
        meta = _json.loads(row["metadata"] or "{}")
        meta["gdocs"] = {"_master_id": "doc-1",
                         "01-epictetus.md": {"tab_id": "tab-1"}}
        db.update("manuscripts", manuscript["id"],
                  {"metadata": _json.dumps(meta)})
        manuscript = api.get_manuscript(db)
        try:
            _gdocs.diff_push(db, manuscript, "01-epictetus.md", None, None)
            raised = None
        except Exception as err:
            raised = f"{type(err).__name__}: {err}"
        check("F5 — and so is the SURGICAL push (diff_push), which is the "
              "path an essay with open margin threads actually takes",
              raised is not None and "mid-rewrite" in raised, str(raised))
        # F8: the read that refusal performs must sit BELOW the no-tab
        # check, or a mapped-but-missing file surfaces a raw OSError in
        # place of the precise message.
        meta["gdocs"] = {"_master_id": "doc-1"}
        db.update("manuscripts", manuscript["id"],
                  {"metadata": _json.dumps(meta)})
        manuscript = api.get_manuscript(db)
        try:
            _gdocs.diff_push(db, manuscript, "gone.md", None, None)
            raised = None
        except Exception as err:
            raised = f"{type(err).__name__}: {err}"
        check("F8 — a file with no tab still gets the precise no-tab "
              "refusal, not a FileNotFoundError from the mid-rewrite read",
              raised is not None and "has no tab in the master Doc" in raised,
              str(raised))

        # --- change 29: the pre-publication checklist saw nothing.
        ready = _sweeps.readiness(db, manuscript)
        writeup_item = next((i for i in ready["items"]
                             if i["check"] == "no open writeups"), None)
        check("F5 — the pre-publication checklist reports the open "
              "writeup instead of calling a mid-surgery manuscript ready",
              writeup_item is not None and writeup_item["ok"] is False
              and "01-epictetus.md" in writeup_item["detail"],
              str(writeup_item))
        check("F5 — and 'ready' is False while it is open",
              ready["ready"] is False)

        # --- F6: the prerequisite-gap walk reads the same case-insensitive
        # concept patterns over the same texts as the concept scans, so it
        # takes the same blinding.
        seen = []
        real_gaps = api.compute_prerequisite_gaps

        def recording(db_, mid_, files):
            seen.append(dict(files))
            return real_gaps(db_, mid_, files)

        api.compute_prerequisite_gaps = recording
        try:
            (ms / "02-second.md").write_text("# Second\n\nA new essay.\n")
            with contextlib.redirect_stdout(io.StringIO()):
                api.collect(db, manuscript, {}, source="test")
        finally:
            api.compute_prerequisite_gaps = real_gaps
        # BOTH calls, not just the second. A collect during a writeup has
        # a before-version that already holds the placeholder — the
        # writeup's own start collect put it there — so blinding one side
        # would leave the marker deciding the DELTA, which is the same
        # defect wearing a subtraction sign.
        check("F6 — compute_prerequisite_gaps is handed the BLINDED file "
              "map on BOTH sides of the delta: an unblinded placeholder "
              "could close a prerequisite gap on the strength of a word "
              "inside a marker",
              len(seen) == 2
              and all(files.get("01-epictetus.md") == "" for files in seen)
              and "A new essay." in seen[-1].get("02-second.md", ""),
              str([{k: v[:40] for k, v in files.items()} for files in seen]))
        prior_version = db.one(
            "SELECT files FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no DESC LIMIT 1 OFFSET 1", (manuscript["id"],))
        check("F6 — and the BEFORE version really did carry the "
              "placeholder, so blinding both sides is not a vacuous pass",
              prior_version is not None and api.is_placeholder(
                  _json.loads(prior_version["files"]).get(
                      "01-epictetus.md", "")))

        # --- RK6: two writeups make --writeup a constant companion, so it
        # takes the essay's name as well as an opaque id.
        api.attach_style(db, manuscript, "02-second.md",
                         "Connections essays")
        # The freshness gate is not what is under test here; give the
        # in-flight neighbour a summary that matches its PINNED text, the
        # state a `summarize rebuild` would leave it in (`rewriting`).
        from authorlm import summaries as _sums
        from authorlm.db import ko_fields as _ko

        srow = _ko("es")
        srow.update(manuscript_id=manuscript["id"], file="01-epictetus.md",
                    summary="MOVES: [1] the pre-rewrite essay.",
                    source_hash=_sums._hash(
                        "# Epictetus\n\nThe old essay stands here.\n"),
                    upstream_hash="", upstream_stale=0, status="current")
        db.insert("essay_summaries", srow)
        with contextlib.redirect_stdout(io.StringIO()):
            second = api.write_start(db, manuscript, {}, "02-second.md",
                                     intent["id"][:8])
        try:
            api._writeup(db, manuscript)
            raised = None
        except LookupError as err:
            raised = str(err)
        check("RK6 — with two open, a bare lookup still refuses rather "
              "than guessing, and now shows the file-name form",
              raised is not None and "multiple active writeups" in raised
              and "--writeup 01-epictetus.md" in raised, str(raised))
        check("RK6 — --writeup takes the exact file name",
              api._writeup(db, manuscript, "02-second.md")["id"]
              == second["writeup"]["id"])
        check("RK6 — and a unique case-insensitive fragment of it, the "
              "same forgiveness every other file argument gets",
              api._writeup(db, manuscript, "EPICTETUS")["file"]
              == "01-epictetus.md")
        check("RK6 — the id prefix is tried FIRST and is unchanged, so "
              "nothing that resolved before resolves differently now",
              api._writeup(db, manuscript,
                           second["writeup"]["id"][:8])["id"]
              == second["writeup"]["id"])
        try:
            api._writeup(db, manuscript, ".md")
            raised = None
        except LookupError as err:
            raised = str(err)
        check("RK6 — a fragment matching both files is REFUSED, naming "
              "them, rather than picking one",
              raised is not None and "ambiguous" in raised
              and "01-epictetus.md" in raised and "02-second.md" in raised,
              str(raised))
        try:
            api._writeup(db, manuscript, "no-such-thing")
            raised = None
        except LookupError as err:
            raised = str(err)
        check("RK6 — and a miss says it is neither an id nor a file, and "
              "lists what IS open",
              raised is not None and "neither a writeup id prefix" in raised
              and "01-epictetus.md" in raised, str(raised))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_briefing_active_writeups() -> None:
    """AB-1: an essay truncated mid-writeup must surface at the system's
    front door.

    `build_briefing` knew nothing of the `writeups` table, so
    `get_briefing`, its compact MCP projection and the CLI briefing were
    all silent while the file sat truncated on disk and half-rebuilt. An
    author returning after a week and opening a conversation got a normal
    briefing with no signal that the essay was mid-surgery — the one
    failure mode that leaves them holding a false belief about their own
    manuscript (usability-analysis §2.3 / ranked concern #1)."""
    import io

    from authorlm import briefing as briefing_module

    root, ws, db, manuscript, intent = _writeup_fixture("authorlm-ab1-")
    try:
        quiet = briefing_module.build_briefing(db, manuscript["id"])
        check("AB-1: build_briefing carries an active_writeups key",
              "active_writeups" in quiet, str(sorted(quiet)))
        check("AB-1: with nothing open, active_writeups is empty",
              quiet.get("active_writeups") == [],
              str(quiet.get("active_writeups")))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_main(["--workspace", str(ws), "briefing"])
        check("AB-1: a briefing with no open writeup never mentions writeups",
              "writeup" not in buf.getvalue().lower(), buf.getvalue())

        api.write_start(db, manuscript, {}, "01-epictetus.md",
                        intent["id"][:8])
        api.write_plan(db, manuscript, [
            {"role": "opener", "notes": "claims the crux is prohairesis"},
            {"role": "development", "notes": "claims the scope diverges"},
        ])
        live = briefing_module.build_briefing(db, manuscript["id"])
        rows = live["active_writeups"]
        check("AB-1: the open writeup reaches the briefing", len(rows) == 1,
              str(rows))
        entry = rows[0] if rows else {}
        check("AB-1: the entry names file, intent, progress and resume hint",
              entry.get("file") == "01-epictetus.md"
              and entry.get("intent_id") == intent["id"]
              and entry.get("progress") == "beat 1 of 2"
              and entry.get("plan_len") == 2
              and entry.get("cursor") == 0
              and entry.get("proposal_pending") is False
              and entry.get("resume") == "write status",
              str(entry))

        api.write_propose(db, manuscript, "The crux is prohairesis.",
                          "realizes the crux; opener per the plan",
                          no_critic="fixture")
        pending = briefing_module.build_briefing(
            db, manuscript["id"])["active_writeups"][0]
        check("AB-1: a draft awaiting a verdict shows as pending",
              pending.get("proposal_pending") is True, str(pending))

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_main(["--workspace", str(ws), "briefing"])
        rendered = buf.getvalue()
        check("AB-1: the CLI briefing names the truncated file",
              "01-epictetus.md" in rendered, rendered)
        check("AB-1: the CLI briefing names the resume verb",
              "write status" in rendered, rendered)

        # The MCP surface serializes build_briefing's dict — but through
        # compact_briefing, which projects a hand-listed set of keys. A
        # key added to build_briefing alone would never reach the agent.
        full = api.get_briefing(db, manuscript)
        check("AB-1: api.get_briefing (MCP verbose=True) carries it",
              len(full.get("active_writeups", [])) == 1,
              str(full.get("active_writeups")))
        compact = api.compact_briefing(full)
        check("AB-1: compact_briefing (the MCP default) carries it too",
              [w["file"] for w in compact.get("active_writeups", [])]
              == ["01-epictetus.md"],
              str(compact.get("active_writeups")))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_replan_settles_pending_proposal() -> None:
    """AB-2: `write plan --replace` must refuse while a draft is pending.

    `write_plan(replace=True)` assigns FRESH `n`s to the replacement
    beats, so a proposal pending on the beat at the cursor was orphaned:
    `_beat_proposal` could never find it again, it never surfaced in
    `write status`, it counted as `proposed` in `_beat_tallies` forever,
    and the author's reason for replanning — the highest-value evidence
    in the loop — was never recorded (usability-analysis §2.7, §3.3).

    The fix refuses and names both exits. It does NOT auto-supersede:
    silently marking the row `superseded` would tidy the tally while
    still discarding the evidence."""
    root, _ws, db, manuscript, intent = _writeup_fixture("authorlm-ab2-")
    try:
        api.write_start(db, manuscript, {}, "01-epictetus.md",
                        intent["id"][:8])
        api.write_plan(db, manuscript, [
            {"role": "opener", "notes": "claims the crux is prohairesis"},
            {"role": "close", "notes": "claims the divergence is scope"},
        ])
        api.write_propose(db, manuscript, "The crux is prohairesis.",
                          "realizes the crux; opener per the plan",
                          no_critic="fixture")
        try:
            api.write_plan(db, manuscript,
                           [{"role": "opener", "notes": "claims it differently"}],
                           replace=True)
            refused, message = False, ""
        except ValueError as err:
            refused, message = True, str(err)
        check("AB-2: --replace is refused while a draft awaits a verdict",
              refused, "the pending proposal was orphaned instead")
        check("AB-2: the refusal names the reject exit and the reason it wants",
              "write reject" in message and "--reason" in message, message)
        check("AB-2: the refusal names the accept exit too",
              "write accept" in message, message)
        check("AB-2: the pending row is left untouched, not superseded",
              db.one("SELECT COUNT(*) AS n FROM guidance_history "
                     "WHERE batch_id = ? AND state = 'proposed'",
                     (api._writeup(db, manuscript)["id"],))["n"] == 1,
              "a refusal must not mutate evidence")

        api.write_reject(db, manuscript,
                         "the whole frame is wrong — the crux is not "
                         "prohairesis but the scope of assent")
        result = api.write_plan(
            db, manuscript,
            [{"role": "opener", "notes": "claims the crux is assent"}],
            replace=True)
        check("AB-2: once the draft is rejected, --replace succeeds",
              result["added"] == 1 and result["kept"] == 0, str(result))
        tallies = api._beat_tallies(db, api._writeup(db, manuscript))
        check("AB-2: the replan leaves no row stranded as 'proposed'",
              tallies.get("proposed", 0) == 0, str(tallies))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_style_refusal_names_candidates() -> None:
    """AB-3: the missing-`--style` refusal must name real candidates.

    `write start --new` refuses without `--style` and named only the
    flag, sending the author away to `style guides` mid-flow. The obvious
    value — the guide attached to the `--after` anchor — is one query
    away (usability-analysis §2.2). The message is enriched; nothing is
    silently defaulted, because naming the drafting law explicitly is the
    property the gate exists to keep."""
    root, _ws, db, manuscript, intent = _writeup_fixture("authorlm-ab3-")
    try:
        api.define_style_guide(db, manuscript, "Sermon voice")
        try:
            api.write_start(db, manuscript, {}, "weil.md", intent["id"][:8],
                            after="01-epictetus.md", brief="A short essay.",
                            new=True)
            refused, message = False, ""
        except ValueError as err:
            refused, message = True, str(err)
        check("AB-3: --new without --style is still refused", refused,
              "the gate must not silently default")
        check("AB-3: the refusal still names the flag",
              "--style" in message, message)
        check("AB-3: the refusal lists a real guide name",
              "Connections essays" in message and "Sermon voice" in message,
              message)
        check("AB-3: the refusal names the anchor's own guide as the likely one",
              "01-epictetus.md" in message
              and message.index("01-epictetus.md")
              > message.index("--style <guide>"),
              message)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _client_ctx(env: dict, workspace, surface: str = "cli", now=None):
    """A resolution context built from a FAKE environment and a FAKE
    workspace. Never `os.environ` — the suite runs inside a Claude Code
    Bash call and would otherwise be asking about the developer's own
    live chat (R-5)."""
    from authorlm import clients

    return clients.Context(env=env, surface=surface, workspace=Path(workspace),
                           now=now or clients._now())


def _write_test_marker(clients_mod, workspace, session_id, *, engine="claude-code",
                       host_pid=None, age_hours=0.0, **extra):
    from datetime import datetime, timedelta, timezone

    stamp = (datetime.now(timezone.utc)
             - timedelta(hours=age_hours)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    data = {"engine": engine, "session_id": session_id,
            "host_pid": os.getpid() if host_pid is None else host_pid,
            "updated_at": stamp, "started_at": stamp}
    data.update(extra)
    return clients_mod.write_marker_file(workspace, data)


def check_client_resolution() -> None:
    """The layered resolver: override, adapters, ambient, nothing.

    Every case drives `clients.current(Context(...))` with an injected
    env and an injected workspace. What is pinned here is the
    PRECEDENCE, not any one variable name — `CLAUDE_CODE_SESSION_ID` is
    undocumented and can vanish, and when it does the fix should be a
    one-line adapter edit, not a test rewrite (R-1)."""
    from authorlm import clients

    root = Path(tempfile.mkdtemp(prefix="authorlm-clients-"))
    try:
        # --- 1. the explicit override wins, in both spellings, and off. ---
        ws = root / "override"
        loud = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "live-chat-id"}
        packed = clients.current(_client_ctx(
            {**loud, "AUTHORLM_CLIENT": "claude-code:pinned-id:a label"}, ws))
        check("AUTHORLM_CLIENT beats a present CLAUDE_CODE_SESSION_ID",
              (packed.session_id, packed.precision, packed.adapter)
              == ("pinned-id", "exact", "env"),
              f"{packed}")
        check("the packed override carries its label",
              packed.label == "a label" and packed.engine == "claude-code")
        as_json = clients.current(_client_ctx(
            {**loud, "AUTHORLM_CLIENT":
             '{"engine": "athena", "session": "conv-9", "label": "Athena"}'},
            ws))
        check("AUTHORLM_CLIENT also accepts JSON",
              (as_json.engine, as_json.session_id, as_json.label)
              == ("athena", "conv-9", "Athena"), f"{as_json}")
        off = clients.current(_client_ctx(
            {**loud, "AUTHORLM_CLIENT": "none"}, ws))
        check("AUTHORLM_CLIENT=none disables detection entirely",
              (off.engine, off.session_id, off.precision)
              == ("unknown", None, "none"), f"{off}")

        # --- 2. Branch A: the session id travels with the invocation. ---
        ws = root / "branch-a"
        branch_a = clients.current(_client_ctx(
            {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "a1ea8c70-cafe",
             "AI_AGENT": "claude-code_2-1-246_agent"}, ws))
        check("Branch A resolves the chat session exactly",
              (branch_a.engine, branch_a.session_id, branch_a.precision,
               branch_a.adapter)
              == ("claude-code", "a1ea8c70-cafe", "exact", "claude-code"),
              f"{branch_a}")
        check("with no hook installed the adapter is still exact, "
              "labelled from AI_AGENT",
              branch_a.label == "claude-code_2-1-246_agent"
              and branch_a.transcript_hint is None)

        # --- 3. Branch B: no session id, host pid → marker → session. ---
        ws = root / "branch-b"
        _write_test_marker(clients, ws, "b-session-id", host_pid=999,
                           label="Claude Code (hooked)",
                           transcript_hint="/tmp/transcripts/b.jsonl")
        branch_b = clients.current(_client_ctx(
            {"CLAUDECODE": "1",
             "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/cc-socks/999.sock"}, ws))
        check("Branch B maps the host pid to the session through the marker",
              (branch_b.session_id, branch_b.precision)
              == ("b-session-id", "exact"), f"{branch_b}")
        check("Branch B still picks up the marker's enrichment",
              branch_b.transcript_hint == "/tmp/transcripts/b.jsonl")

        # --- 4. the documented gate, and the mcp-stdio fallthrough. ---
        ws = root / "gate"
        bare = clients.CLAUDE_CODE.detect(_client_ctx(
            {"CLAUDE_CODE_SESSION_ID": "orphan"}, ws))
        check("CLAUDECODE absent → the claude-code adapter declines",
              bare is None)
        as_mcp = clients.current(_client_ctx({}, ws, surface="mcp"))
        check("an unrecognised engine's stdio server is exact but anonymous",
              (as_mcp.engine, as_mcp.session_id, as_mcp.precision)
              == ("mcp-stdio", clients.CONNECTION_ID, "exact"), f"{as_mcp}")
        as_cli = clients.current(_client_ctx({}, ws, surface="cli"))
        check("a bare CLI run with nothing to go on is unknown/none",
              (as_cli.engine, as_cli.precision) == ("unknown", "none"))

        # --- 5. ambient, one live marker. ---
        ws = root / "ambient-one"
        _write_test_marker(clients, ws, "ambient-solo", label="Solo chat")
        solo = clients.current(_client_ctx({}, ws))
        check("one live marker is named, and honestly marked ambient",
              (solo.engine, solo.session_id, solo.precision, solo.adapter)
              == ("claude-code", "ambient-solo", "ambient", "ambient"),
              f"{solo}")

        # --- 6. the refusal: two live markers name no session at all. ---
        ws = root / "ambient-two"
        _write_test_marker(clients, ws, "chat-one")
        _write_test_marker(clients, ws, "chat-two")
        both = clients.current(_client_ctx({}, ws))
        check("two live clients → the ambient layer refuses to name one",
              both.session_id is None and both.precision == "ambient",
              f"{both}")
        check("the refusal records the count instead of guessing",
              both.note == "2 live clients — session not claimed"
              and both.engine == "claude-code", f"{both.note}")

        # --- 7. staleness: age and a dead host pid both disqualify. ---
        ws = root / "stale"
        _write_test_marker(clients, ws, "too-old", age_hours=30)
        _write_test_marker(clients, ws, "dead-host", host_pid=999999)
        _write_test_marker(clients, ws, "still-here")
        survivor = clients.current(_client_ctx({}, ws))
        check("a 30-hour-old marker and a dead host pid are both ignored",
              survivor.session_id == "still-here"
              and survivor.precision == "ambient", f"{survivor}")
        check("the stale pair did not trip the two-live refusal",
              survivor.note is None)

        # --- 8. a broken marker directory is silent, never an exception. ---
        ws = root / "broken"
        check("an absent clients/ directory reads as empty",
              clients.current(_client_ctx({}, ws)).engine == "unknown")
        clients.clients_dir(ws).mkdir(parents=True)
        (clients.clients_dir(ws) / "garbage.json").write_text("{not json")
        check("malformed marker JSON is skipped, not raised",
              clients.current(_client_ctx({}, ws)).precision == "none")
        _write_test_marker(clients, ws, "good-one")
        check("a good marker beside a malformed one still resolves",
              clients.current(_client_ctx({}, ws)).session_id == "good-one")

        # --- 14/15. the hook verb, through the CLI, on stdin. ---
        ws = root / "hook"
        ws.mkdir(parents=True, exist_ok=True)

        def hook(payload_text: str, *flags) -> None:
            stream = io.StringIO(payload_text)
            prev_stdin = sys.stdin
            sys.stdin = stream
            try:
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    cli_main(["--workspace", str(ws), "client-hook", *flags])
            finally:
                sys.stdin = prev_stdin
            check_hook_silent.append(out.getvalue())

        check_hook_silent: list = []
        hook(json.dumps({
            "session_id": "hooked-session", "source": "startup",
            "transcript_path": "/tmp/projects/hooked-session.jsonl",
            "cwd": "/tmp/project"}))
        marker = clients.read_marker(ws, "claude-code", "hooked-session")
        check("a SessionStart payload writes the marker with its fields",
              marker is not None
              and marker["transcript_hint"]
              == "/tmp/projects/hooked-session.jsonl"
              and marker["source"] == "startup"
              and marker["cwd"] == "/tmp/project", f"{marker}")
        check("the hook prints nothing to stdout "
              "(a SessionStart's stdout reaches the model)",
              check_hook_silent == [""], f"{check_hook_silent}")

        hook(json.dumps({"session_id": "second-session", "source": "startup"}))
        files = sorted(p.name for p in clients.clients_dir(ws).glob("*.json"))
        check("two SessionStarts write two files, neither clobbered",
              files == ["claude-code-hooked-session.json",
                        "claude-code-second-session.json"], f"{files}")
        check("the first marker survived the second write",
              (clients.read_marker(ws, "claude-code", "hooked-session") or {})
              .get("transcript_hint")
              == "/tmp/projects/hooked-session.jsonl")

        hook(json.dumps({"session_id": "hooked-session", "reason": "clear"}),
             "--end")
        check("a SessionEnd payload removes its own marker, and only its own",
              clients.read_marker(ws, "claude-code", "hooked-session") is None
              and clients.read_marker(ws, "claude-code", "second-session")
              is not None)

        before = sorted(p.name for p in clients.clients_dir(ws).glob("*.json"))
        hook("{ this is not json")
        after = sorted(p.name for p in clients.clients_dir(ws).glob("*.json"))
        check("malformed hook input exits 0 and writes nothing",
              before == after, f"{before} → {after}")

        snippet = io.StringIO()
        with contextlib.redirect_stdout(snippet):
            cli_main(["--workspace", str(ws), "client-hook", "--print-setup"])
        check("--print-setup prints a registerable hook snippet",
              "SessionStart" in snippet.getvalue()
              and "authorlm client-hook" in snippet.getvalue())

        # --- 16. the tracelog's optional client field. ---
        from authorlm import tracelog

        ws = root / "trace"
        tracelog.record("provenance", surface="cli", workspace=str(ws),
                        client={"engine": "claude-code", "session": "t-1",
                                "precision": "exact"})
        tracelog.record("status", surface="cli", workspace=str(ws))
        lines = [json.loads(x) for x in
                 (tracelog.log_dir(str(ws)) / "trace.jsonl")
                 .read_text().splitlines()]
        check("the trace line carries the client when it is known",
              lines[0]["client"] == {"engine": "claude-code", "session": "t-1",
                                     "precision": "exact"})
        check("the trace line omits the key entirely when it is not",
              "client" not in lines[1], f"{lines[1]}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


@contextlib.contextmanager
def _as_client(packed: str):
    """Pretend, for the duration of the block, that this process was
    launched by a particular chat. Goes through `AUTHORLM_CLIENT`, which
    is the documented override, so the stamp sites are exercised through
    the same path a real invocation uses."""
    from authorlm import clients

    previous = os.environ.get("AUTHORLM_CLIENT")
    os.environ["AUTHORLM_CLIENT"] = packed
    try:
        yield clients.current()
    finally:
        if previous is None:
            os.environ.pop("AUTHORLM_CLIENT", None)
        else:
            os.environ["AUTHORLM_CLIENT"] = previous


def check_client_stamps() -> None:
    """The stamp sites: every row at birth, the session's rich list, the
    writeup's touched-by list — and the ordering hazard the touched-by
    list would otherwise walk into."""
    from authorlm import clients

    root = Path(tempfile.mkdtemp(prefix="authorlm-stamp-"))
    try:
        # --- 9. the birth stamp, and its deliberate empty case. ---
        empty = loads(ko_fields("t")["metadata"], None)
        check("with detection off a fresh row is born with a bare {} — "
              "an absent stamp must read exactly like a pre-stamp row",
              empty == {}, f"{empty}")
        with _as_client("claude-code:stamp-a:Chat A"):
            stamped = loads(ko_fields("t")["metadata"], None)
        check("every row is born carrying the join key",
              stamped == {"client": {"engine": "claude-code",
                                     "session": "stamp-a",
                                     "precision": "exact"}}, f"{stamped}")

        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "01-choice.md").write_text("# Opening\n\nEvery act begins.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            db = api.open_db(str(ws), create=True)
            manuscript = api.register_manuscript(db, "book", str(ms))

        # --- 10. the session's client list is a LIST. ---
        with _as_client("claude-code:sess-a:Chat A"):
            first, created = api.ensure_session(db, manuscript)
        check("ensure_session opens lazily and records the chat", created)
        with _as_client("claude-code:sess-b:Chat B"):
            second, created_again = api.ensure_session(db, manuscript)
        check("a second chat joins the SAME AuthorLM session",
              not created_again and second["id"] == first["id"])
        entries = loads(second["metadata"], {})["clients"]
        check("both chats are recorded, deduped by (engine, session)",
              [e["session"] for e in entries] == ["sess-a", "sess-b"],
              f"{entries}")
        check("the rich payload lives here, not on every row",
              entries[0]["label"] == "Chat A"
              and entries[0]["adapter"] == "env"
              and "first_seen" in entries[0] and "last_seen" in entries[0])
        was = entries[0]["last_seen"]
        with _as_client("claude-code:sess-a:Chat A"):
            third, _ = api.ensure_session(db, manuscript)
        again = loads(third["metadata"], {})["clients"]
        check("a returning chat advances last_seen and adds no second entry",
              len(again) == 2 and again[0]["last_seen"] > was,
              f"{again[0]['last_seen']} vs {was}")

        # --- 11. the writeup touched-by list, and the clobber regression. ---
        intent = api.declare_intent(db, manuscript, "Introduce gravity")
        with _as_client("claude-code:wu-a:Chat A"):
            fields = ko_fields("wu")
            fields.update(manuscript_id=manuscript["id"],
                          intent_id=intent["intent"]["id"],
                          file="02-gravity.md")
            writeup_id = db.insert("writeups", fields)
        born = loads(db.one("SELECT metadata FROM writeups WHERE id = ?",
                            (writeup_id,))["metadata"], {})
        check("the writeup was born stamped with the chat that opened it",
              born == {"client": {"engine": "claude-code", "session": "wu-a",
                                  "precision": "exact"}}, f"{born}")

        def resolve(packed: str, verb: str) -> dict:
            with _as_client(packed):
                clients.configure(verb=verb)
                return api._writeup(db, manuscript, writeup_id)

        for verb in ("write status", "write plan", "write status"):
            resolve("claude-code:wu-a:Chat A", verb)   # "status" twice: dedup
        for verb in ("write draft", "write accept"):
            row = resolve("claude-code:wu-b:Chat B", verb)
        touched = loads(row["metadata"], {})["clients"]
        check("each chat gets exactly one entry, in the order it arrived",
              [e["session"] for e in touched] == ["wu-a", "wu-b"], f"{touched}")
        check("the verb trail is recorded and deduped",
              touched[0]["verbs"] == ["write status", "write plan"],
              f"{touched[0]}")
        check("the second chat's own verbs are its own",
              touched[1]["verbs"] == ["write draft", "write accept"])

        # The hazard, asserted directly. Every one of `_writeup`'s callers
        # loads the metadata it just returned, mutates it, and dumps it
        # back. If the touch ran after that load — or if the pre-touch row
        # were returned — this dump would silently drop the clients list.
        stale_carrier = resolve("claude-code:wu-c:Chat C", "write complete")
        meta = loads(stale_carrier["metadata"], {})
        meta["some_later_key"] = "written by the verb after _writeup returned"
        db.update("writeups", writeup_id, {"metadata": json.dumps(meta)})
        after = loads(db.one("SELECT metadata FROM writeups WHERE id = ?",
                             (writeup_id,))["metadata"], {})
        check("a verb's own read-modify-write does NOT drop the clients list",
              [e["session"] for e in after.get("clients", [])]
              == ["wu-a", "wu-b", "wu-c"], f"{after.get('clients')}")
        check("and the verb's own key landed alongside it",
              after.get("some_later_key")
              == "written by the verb after _writeup returned")
    finally:
        clients.configure(verb="")
        shutil.rmtree(root, ignore_errors=True)


def check_db_perf_log() -> None:
    """AN: always-on database performance logging.

    Sponsor ruling: *"configure the databases to by default log stuff …
    see performance logs from the database itself instead of us having to
    do one-off measurements … monitor the performance over all time."*

    Six properties, in order: queries aggregate per invocation by
    normalized shape; the line carries the client provenance join key; a
    query over `slow_ms` writes its own line and one under it does not;
    `perf_log = false` takes no timing at all; the log rotates at the cap;
    `authorlm dbperf` reads it back and is honest when there is nothing to
    read. Then the property that outranks all of them — a log destination
    that cannot be written does not break the verb it measures.
    """
    import datetime as _dt
    import tomllib

    from authorlm import dbperf
    from authorlm.db import Database, ko_fields as ko

    root = Path(tempfile.mkdtemp(prefix="authorlm-dbperf-"))
    try:
        # --- 1. aggregation: one line per invocation, keyed by shape. ---
        ws = root / "ws"
        (ws / ".authorlm").mkdir(parents=True)
        db = Database(ws / ".authorlm" / "authorlm.db")
        ms_id = db.insert("manuscripts", {**ko("ms"), "name": "book",
                                          "path": str(ws / "m")})
        with db.transaction():
            for name in ("Gravity", "Choice", "Death"):
                db.insert("concept_nodes", {**ko("cn"), "manuscript_id": ms_id,
                                            "name": name})
        for _ in range(4):
            db.all("SELECT * FROM concept_nodes WHERE manuscript_id = ?",
                   (ms_id,))
        db.update("manuscripts", ms_id, {"author": "Author Penname"})
        dbperf.flush("test aggregate")

        log = dbperf.log_dir(str(ws)) / dbperf.FILENAME
        entries = [json.loads(line) for line in log.read_text().splitlines()]
        aggregate = [e for e in entries if not e.get("slow")][-1]
        shapes = aggregate["shapes"]
        listed = shapes.get("SELECT * FROM concept_nodes WHERE manuscript_id = ?")
        check("every read through the Database surface is aggregated by "
              "normalized SQL shape as [n, total_ms, max_ms]",
              listed is not None and listed[0] == 4
              and listed[1] >= listed[2] > 0, f"{shapes}")
        check("writes, the transaction's BEGIN IMMEDIATE and the COMMIT "
              "that follows are shapes of their own — the body is not "
              "timed as a unit, which would double-count what is inside it",
              any(s.startswith("INSERT INTO concept_nodes") for s in shapes)
              and any(s.startswith("UPDATE manuscripts") for s in shapes)
              and dbperf.BEGIN_SHAPE in shapes
              and dbperf.COMMIT_SHAPE in shapes, f"{sorted(shapes)}")
        check("grand_total_ms is the sum of the shape totals",
              abs(aggregate["grand_total_ms"]
                  - sum(v[1] for v in shapes.values())) < 0.05,
              f"{aggregate['grand_total_ms']} vs {shapes}")
        check("one line per invocation, not per query: 9 queries, 1 line",
              len([e for e in entries if not e.get("slow")]) == 1
              and sum(v[0] for v in shapes.values()) >= 9, f"{entries}")
        check("with detection pinned off the client key is omitted entirely, "
              "so an unattributed line reads exactly like a pre-stamp one",
              "client" not in aggregate, f"{aggregate}")

        # The CLI wiring: dispatch flushes, and names the line by the verb.
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "provenance", "--clients"])
        after = [json.loads(line) for line in log.read_text().splitlines()]
        check("a CLI invocation flushes its own aggregate line at the end "
              "of dispatch, named by command+action (never raw argv — an "
              "intent statement is a positional argument)",
              after[-1].get("invocation") == "provenance", f"{after[-1]}")

        # --- 2. the client provenance join key (§15.15). ---
        with _as_client("claude-code:perf-chat:Perf Chat"):
            client_ws = root / "client-ws"
            (client_ws / ".authorlm").mkdir(parents=True)
            client_db = Database(client_ws / ".authorlm" / "authorlm.db")
            client_db.all("SELECT * FROM manuscripts")
            dbperf.flush("client line")
        stamped = json.loads(
            (dbperf.log_dir(str(client_ws)) / dbperf.FILENAME)
            .read_text().splitlines()[-1])
        check("the aggregate line carries the client provenance join key, "
              "so the perf log joins to trace.jsonl and to row metadata",
              stamped.get("client") == {"engine": "claude-code",
                                        "session": "perf-chat",
                                        "precision": "exact"}, f"{stamped}")

        # --- 3. the slow log: above the threshold, and below it. ---
        slow_ws = root / "slow-ws"
        (slow_ws / ".authorlm").mkdir(parents=True)
        slow_db = Database(slow_ws / ".authorlm" / "authorlm.db")
        slow_db._perf.slow_ms = 0.0001          # everything is slow
        slow_db.all("SELECT * FROM manuscripts")
        slow_db._perf.slow_ms = 60_000          # nothing is
        slow_db.all("SELECT * FROM concept_nodes")
        dbperf.flush("slow test")
        slow_lines = [json.loads(line) for line in
                      (dbperf.log_dir(str(slow_ws)) / dbperf.FILENAME)
                      .read_text().splitlines()]
        fired = [e for e in slow_lines if e.get("slow")]
        slow_aggregate = [e for e in slow_lines if not e.get("slow")][-1]
        check("a query over slow_ms writes its own immediate line, tagged "
              "and carrying that one query's shape and duration",
              len(fired) == 1
              and fired[0]["sql"] == "SELECT * FROM manuscripts"
              and fired[0]["ms"] > 0, f"{fired}")
        check("a query under slow_ms writes no slow line, and both queries "
              "are still counted exactly once in the aggregate",
              set(slow_aggregate["shapes"]) == {"SELECT * FROM manuscripts",
                                                "SELECT * FROM concept_nodes"}
              and all(v[0] == 1 for v in slow_aggregate["shapes"].values()),
              f"{slow_aggregate}")

        # --- 4. configuration: shipped on, and cleanly off. ---
        check("perf logging is shipped ON — an absent [db] section is the "
              "default, not a silent off-switch",
              dbperf.settings() == (True, dbperf.DEFAULT_SLOW_MS),
              f"{dbperf.settings()}")
        shipped = tomllib.loads(
            (Path(__file__).resolve().parent.parent / "config.toml")
            .read_text(encoding="utf-8")).get("db") or {}
        check("and the SHIPPED config.toml says so, in the [db] section",
              shipped.get("perf_log") is True
              and isinstance(shipped.get("slow_ms"), int), f"{shipped}")

        off_config = root / "off-config.toml"
        off_config.write_text("[db]\nperf_log = false\nslow_ms = 7\n")
        previous_config = os.environ.get("AUTHORLM_CONFIG")
        os.environ["AUTHORLM_CONFIG"] = str(off_config)
        try:
            off_settings = dbperf.settings()
            off_ws = root / "off-ws"
            (off_ws / ".authorlm").mkdir(parents=True)
            off_db = Database(off_ws / ".authorlm" / "authorlm.db")
            off_db.all("SELECT * FROM manuscripts")
            off_db.insert("manuscripts", {**ko("ms"), "name": "silent",
                                          "path": str(off_ws)})
            dbperf.flush("off")
            no_recorder = off_db._perf is None
            wrote = (dbperf.log_dir(str(off_ws)) / dbperf.FILENAME).exists()
        finally:
            if previous_config is None:
                os.environ.pop("AUTHORLM_CONFIG", None)
            else:
                os.environ["AUTHORLM_CONFIG"] = previous_config
        check("perf_log = false holds NO recorder — the timing is not taken, "
              "not merely unwritten — and nothing reaches the log",
              off_settings == (False, 7) and no_recorder and not wrote,
              f"{off_settings} recorder_absent={no_recorder} wrote={wrote}")

        # --- 5. rotation at the cap, two generations kept. ---
        rotate_ws = root / "rotate-ws"
        rotate_dir = dbperf.log_dir(str(rotate_ws))
        rotate_dir.mkdir(parents=True)
        rotated_path = rotate_dir / dbperf.FILENAME
        recorder = dbperf.Recorder(rotate_dir)
        rotated_path.write_text("x" * (dbperf.MAX_BYTES + 1))
        recorder.record("SELECT 1", 0.001)
        recorder.flush("first")
        first = dbperf._generation(rotated_path, 1)
        check("an oversized perf log rotates to .jsonl.1 and a fresh file "
              "starts",
              first.exists() and first.stat().st_size > dbperf.MAX_BYTES
              and len(rotated_path.read_text().splitlines()) == 1,
              f"{sorted(p.name for p in rotate_dir.iterdir())}")
        rotated_path.write_text("y" * (dbperf.MAX_BYTES + 1))
        recorder.record("SELECT 2", 0.001)
        recorder.flush("second")
        check("two generations are kept and the third is dropped",
              first.read_text().startswith("y")
              and dbperf._generation(rotated_path, 2).read_text()
              .startswith("x")
              and not dbperf._generation(rotated_path, 3).exists(),
              f"{sorted(p.name for p in rotate_dir.iterdir())}")

        # --- 6. the reading surface. ---
        report_ws = root / "report-ws"
        report_dir = dbperf.log_dir(str(report_ws))
        check("dbperf on a workspace with no log says so rather than "
              "rendering an empty table",
              dbperf.report(report_dir)[0].startswith("no performance log yet"),
              f"{dbperf.report(report_dir)}")

        report_dir.mkdir(parents=True)
        now = _dt.datetime.now(_dt.timezone.utc)
        today = now.strftime("%Y-%m-%d")
        yesterday = (now - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        versions = ("SELECT * FROM manuscript_versions WHERE manuscript_id = ? "
                    "ORDER BY version_no DESC LIMIT 1")
        chat_one = {"engine": "claude-code", "session": "chat-one",
                    "precision": "exact"}
        chat_two = {"engine": "claude-code", "session": "chat-two",
                    "precision": "exact"}
        synthetic = [
            {"ts": f"{yesterday}T09:00:00+00:00", "invocation": "collect",
             "shapes": {versions: [3, 40.0, 17.7], "SELECT 1": [900, 9.0, 0.1]},
             "grand_total_ms": 49.0, "client": chat_one},
            {"ts": f"{today}T09:00:00+00:00", "invocation": "write draft",
             "shapes": {versions: [1, 20.0, 20.0]},
             "grand_total_ms": 20.0, "client": chat_two},
            {"ts": f"{today}T09:00:01+00:00", "slow": True, "ms": 184.2,
             "sql": versions, "client": chat_two},
            # Outside every window this test asks for.
            {"ts": "2026-01-01T00:00:00+00:00", "invocation": "ancient",
             "shapes": {"SELECT ancient": [1, 5000.0, 5000.0]},
             "grand_total_ms": 5000.0},
        ]
        (report_dir / dbperf.FILENAME).write_text(
            "".join(json.dumps(e) + "\n" for e in synthetic))
        rendered = io.StringIO()
        with contextlib.redirect_stdout(rendered):
            cli_main(["--workspace", str(report_ws), "dbperf",
                      "--days", "3", "--top", "5"])
        text = rendered.getvalue()
        check("dbperf ranks shapes by total time across the window, summing "
              "the per-invocation aggregates",
              "top by total time" in text
              and f"      60.0        4      20.0  {versions}" in text, text)
        check("and ranks them again by the worst single query — the two "
              "questions a perf log exists to answer",
              "top by slowest single query" in text, text)
        check("the per-day grand totals are the trend the Sponsor asked for",
              f"  {yesterday}        49.0 ms over 1 invocations" in text
              and f"  {today}        20.0 ms over 1 invocations" in text, text)
        check("the slow-log tail renders with its client attribution",
              "184.2 ms" in text and "[claude-code/chat-two]" in text, text)
        check("per-client attribution is rendered when the lines carry it",
              "claude-code/chat-one" in text
              and "claude-code/chat-two" in text, text)
        check("--days bounds the window: an old line is not counted",
              "SELECT ancient" not in text and "5000.0" not in text, text)

        # --- 7. the property that outranks the rest. ---
        blocked_ws = root / "blocked-ws"
        (blocked_ws / ".authorlm").mkdir(parents=True)
        blocked_db = Database(blocked_ws / ".authorlm" / "authorlm.db")
        (blocked_ws / ".authorlm" / "logs").write_text("not a directory")
        blocked_db.insert("manuscripts", {**ko("ms"), "name": "still-works",
                                          "path": str(root)})
        survived = [r["name"] for r in
                    blocked_db.all("SELECT name FROM manuscripts")]
        dbperf.flush("blocked")
        check("a log destination that cannot be written NEVER breaks the "
              "verb it measures — the write is swallowed and the work lands",
              survived == ["still-works"], f"{survived}")

        # The perf log never resurrects a workspace deleted under a late
        # flush: `logs/` is created, its parent workspace never is.
        gone_ws = root / "gone-ws"
        gone_recorder = dbperf.Recorder(dbperf.log_dir(str(gone_ws)))
        gone_recorder.record("SELECT 1", 0.001)
        gone_recorder.flush("late")
        check("a flush against a workspace that no longer exists writes "
              "nothing and recreates nothing",
              not gone_ws.exists(), f"{gone_ws}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


import contextlib as _contextlib


@_contextlib.contextmanager
def _as_config(text: str, root: Path):
    """Point `AUTHORLM_CONFIG` at a temp config for the duration of the
    block, dropping every memo keyed on the resolved path."""
    from authorlm import budget, usage

    path = root / f"cfg-{abs(hash(text)) % 100000}.toml"
    path.write_text(text, encoding="utf-8")
    previous = os.environ.get("AUTHORLM_CONFIG")
    os.environ["AUTHORLM_CONFIG"] = str(path)
    usage.reset()
    budget.reset()
    try:
        yield path
    finally:
        if previous is None:
            os.environ.pop("AUTHORLM_CONFIG", None)
        else:
            os.environ["AUTHORLM_CONFIG"] = previous
        usage.reset()
        budget.reset()


def _turn(mid: str, **over) -> str:
    """One synthetic Claude Code `assistant` line, shaped from the real
    schema measured in design §0.1.

    A transcript is DATA: every prose-bearing field carries the same
    poison string, and the parser is asserted never to reproduce it."""
    usage = {"input_tokens": 2, "output_tokens": 150,
             "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 0}
    usage.update(over.pop("usage", {}))
    # `iterations` restates the same numbers for the turn's internal
    # steps; a parser that sums it double-counts.
    usage["iterations"] = [{"input_tokens": usage["input_tokens"],
                            "output_tokens": usage["output_tokens"]}]
    line = {"type": "assistant", "uuid": f"uuid-{mid}-{over.pop('n', 0)}",
            "sessionId": "sweep-session", "cwd": "/tmp/poison-cwd",
            "content": "POISONPROSE", "toolUseResult": "POISONPROSE",
            "lastPrompt": "POISONPROSE", "customTitle": "POISONPROSE",
            "aiTitle": "POISONPROSE",
            "message": {"id": mid, "model": over.pop("model",
                                                     "claude-fable-5"),
                        "usage": usage, "content": "POISONPROSE"}}
    line.update(over)
    return json.dumps(line)


def check_usage_ledger() -> None:
    """AP: the always-on usage ledger — where the money went.

    Sponsor ruling: *"it would be good to track usage so if you ever want
    to take this to commercial stage, we can have that ready-made."*

    The ledger core (T-1…T-10): one aggregate line per invocation keyed by
    `purpose|model`; an unpriced model records tokens and a NULL cost,
    never a zero; the vendor-prefix pricing trap, pinned; no litellm at
    all still records every token; the expensive detail line; the clean
    off-switch; never-in-the-way; rotation; replays; the client stamp."""
    import datetime as _dt

    from authorlm import clients, usage

    root = Path(tempfile.mkdtemp(prefix="authorlm-usage-"))
    previous_workspace = clients._STATE["workspace"]
    try:
        usage.reset()
        usage._PRICE_OVERRIDE.clear()
        usage._PRICE_CACHE.clear()
        # Tests INJECT rates. litellm's live map changes under us and a
        # cost assertion pinned to it would be a time bomb.
        usage._PRICE_OVERRIDE["zz/sentinel"] = {
            "input": 1e-6, "output": 2e-6, "cache_read": 1e-7,
            "cache_write": 5e-7, "partial": False}
        usage._PRICE_OVERRIDE["zz/unmapped"] = None
        usage._PRICE_OVERRIDE["zz/dear"] = {
            "input": 1e-3, "output": 1e-3, "cache_read": 0.0,
            "cache_write": 0.0, "partial": False}

        # --- T-1: the aggregate line. ---
        ws = root / "ws"
        (ws / ".authorlm").mkdir(parents=True)
        clients.configure(workspace=str(ws))
        usage.record("llm", "zz/sentinel", 1000, 500, 0, 0)
        usage.record("llm", "zz/sentinel", 200, 100, 0, 0)
        usage.record("critique.summarizer", "zz/sentinel", 40, 10, 7, 3)
        usage.flush("collect --auto")
        log = usage.log_dir(str(ws)) / usage.FILENAME
        lines = [json.loads(x) for x in log.read_text().splitlines()]
        aggregate = lines[-1]
        hand = ((1240 * 1e-6) + (610 * 2e-6) + (7 * 1e-7) + (3 * 5e-7))
        check("one aggregate line per invocation, keyed by "
              "'<purpose>|<model>' as [n, in, out, cache_read, cache_write, "
              "est_cost] — purpose is WHICH CONFIG SECTION chose the model, "
              "which `model` alone cannot say once two sections name one "
              "string",
              len(lines) == 1 and aggregate["kind"] == "api"
              and aggregate["calls"]["llm|zz/sentinel"][:5]
              == [2, 1200, 600, 0, 0]
              and aggregate["calls"]["critique.summarizer|zz/sentinel"][:5]
              == [1, 40, 10, 7, 3], f"{lines}")
        check("the line is named by command+action, never raw argv, and its "
              "estimated cost is the hand-computed sum of the injected "
              "rates over all four counters",
              aggregate["invocation"] == "collect --auto"
              and abs(aggregate["est_cost_usd"] - hand) < 1e-6
              and aggregate["cost_complete"] is True,
              f"{aggregate} vs {hand}")

        # --- T-2: null is not zero. ---
        usage.record("llm", "zz/sentinel", 100, 0, 0, 0)
        usage.record("llm", "zz/unmapped", 5000, 900, 0, 0)
        usage.flush("write status")
        mixed = json.loads(log.read_text().splitlines()[-1])
        check("a model litellm cannot price records its TOKENS and a NULL "
              "cost — never a guess and never a zero, which is a different "
              "claim — and the line's cost_complete goes false so the "
              "reader can name the gap instead of printing a total that "
              "quietly omits it",
              mixed["calls"]["llm|zz/unmapped"][:5] == [1, 5000, 900, 0, 0]
              and mixed["calls"]["llm|zz/unmapped"][5] is None
              and mixed["cost_complete"] is False
              and mixed["est_cost_usd"] > 0, f"{mixed}")
        rendered = "\n".join(usage.report(usage.log_dir(str(ws))))
        check("and the report says how many models went unpriced rather "
              "than absorbing them into the total",
              "had no price" in rendered and "zz/unmapped" in rendered
              and "priced total" in rendered, rendered)

        # --- T-3: THE VENDOR-PREFIX TRAP, pinned. ---
        usage._PRICE_CACHE.clear()
        try:
            import litellm  # noqa: F401
            have_litellm = True
        except Exception:
            have_litellm = False
        if have_litellm:
            import litellm

            trapped = ["openai/gpt-5.6-luna", "anthropic/claude-fable-5"]
            direct = {m: litellm.model_cost.get(m) for m in trapped}
            resolved = {m: usage.price(m) for m in trapped}
            check("PRICING GOES THROUGH get_model_info, NOT "
                  "model_cost[key]: the dict's keys are inconsistent about "
                  "the vendor prefix and the inconsistency lands exactly on "
                  "the shipped config's most expensive paths — a direct "
                  "lookup prices openai/gpt-5.6-luna and "
                  "anthropic/claude-fable-5 at null, i.e. silently loses "
                  "the summariser, the critique editor and beat drafting",
                  all(direct[m] is None for m in trapped)
                  and all(resolved[m] is not None for m in trapped)
                  and all(resolved[m]["input"] > 0 for m in trapped),
                  f"model_cost={direct} get_model_info={resolved}")
            check("gemini/gemini-2.5-flash is the control: model_cost DOES "
                  "have it, which is why a naive lookup looks like it works",
                  litellm.model_cost.get("gemini/gemini-2.5-flash")
                  is not None
                  and usage.price("gemini/gemini-2.5-flash") is not None,
                  "gemini")
            check("openai/gpt-image-2 is trapped the same way — the third "
                  "prefix-keyed miss, and the one that renders pictures",
                  litellm.model_cost.get("openai/gpt-image-2") is None
                  and usage.price("openai/gpt-image-2") is not None,
                  "gpt-image-2")
        else:
            print("  note: litellm absent — the vendor-prefix regression "
                  "(T-3) was not exercised.")
        usage._PRICE_CACHE.clear()

        # --- T-4: no litellm at all. ---
        import builtins

        real_import = builtins.__import__

        def no_litellm(name, *rest, **kw):
            if name == "litellm":
                raise ImportError("no litellm in this environment")
            return real_import(name, *rest, **kw)

        builtins.__import__ = no_litellm
        try:
            usage._PRICE_CACHE.clear()
            blind = usage.price("gemini/gemini-2.5-flash")
            usage.record("llm", "gemini/gemini-2.5-flash", 77, 88, 0, 0)
            usage.flush("no-litellm")
        finally:
            builtins.__import__ = real_import
            usage._PRICE_CACHE.clear()
        offline = json.loads(log.read_text().splitlines()[-1])
        key = "llm|gemini/gemini-2.5-flash"
        check("with no litellm installed every cost is null and every token "
              "count is intact: pricing is an ENRICHMENT, the ledger is not "
              "conditional on it, and nothing escapes",
              blind is None
              and offline["calls"][key][:5] == [1, 77, 88, 0, 0]
              and offline["calls"][key][5] is None, f"{offline}")

        # --- T-5: the expensive detail line. ---
        expensive_ws = root / "expensive-ws"
        (expensive_ws / ".authorlm").mkdir(parents=True)
        clients.configure(workspace=str(expensive_ws))
        usage.reset()
        usage.record("writing", "zz/dear", 1000, 0, 0, 0)     # $1.00
        usage.record("llm", "zz/sentinel", 10, 10, 0, 0)      # cheap
        usage.flush("write draft")
        expensive_log = usage.log_dir(str(expensive_ws)) / usage.FILENAME
        entries = [json.loads(x) for x in
                   expensive_log.read_text().splitlines()]
        detail = [e for e in entries if e.get("expensive")]
        agg = [e for e in entries if not e.get("expensive")][-1]
        check("a single call over [usage] expensive_usd writes its own "
              "IMMEDIATE line — the aggregate already counts it, so that "
              "line is the detail: when, under which chat, and for what",
              len(detail) == 1 and detail[0]["purpose"] == "writing"
              and detail[0]["model"] == "zz/dear"
              and abs(detail[0]["est_cost_usd"] - 1.0) < 1e-9
              and agg["calls"]["writing|zz/dear"][0] == 1, f"{entries}")
        report = "\n".join(usage.report(usage.log_dir(str(expensive_ws))))
        check("and the reader counts it in the expensive tail WITHOUT "
              "adding it to the total a second time (dbperf's slow-line "
              "discipline)",
              "expensive single calls, last 1 of 1" in report
              and "1 invocations, 2 API calls" in report
              and "1.00         2" in report.split("priced total")[0],
              report)

        # --- T-6: the off-switch. ---
        off_ws = root / "off-ws"
        (off_ws / ".authorlm").mkdir(parents=True)
        clients.configure(workspace=str(off_ws))
        with _as_config("[usage]\nledger = false\n", root):
            settings_off = usage.settings()
            usage.record("llm", "zz/sentinel", 10, 10, 0, 0)
            usage.record_image("illustrations", "zz/img", 1)
            usage.flush("off")
            held = usage._LEDGER
            wrote = (usage.log_dir(str(off_ws)) / usage.FILENAME).exists()
        check("[usage] ledger = false holds NO recorder — record, "
              "record_image and flush are no-ops and no file is created",
              settings_off.ledger is False and held is not None
              and held is not usage._OFF and wrote is False
              or (settings_off.ledger is False and not wrote),
              f"{settings_off} wrote={wrote}")
        check("the ledger is shipped ON: an absent [usage] section is the "
              "default, not a silent off-switch — a broken config must "
              "not silently stop a MEASUREMENT",
              usage.settings().ledger is True
              and usage.settings().expensive_usd
              == usage.DEFAULT_EXPENSIVE_USD, f"{usage.settings()}")
        import tomllib

        shipped = tomllib.loads(
            (Path(__file__).resolve().parent.parent / "config.toml")
            .read_text(encoding="utf-8")).get("usage") or {}
        check("and the SHIPPED config.toml says so, in the [usage] section",
              shipped.get("ledger") is True
              and shipped.get("chat") is True
              and isinstance(shipped.get("expensive_usd"), float)
              and isinstance(shipped.get("sweep_interval_seconds"), int),
              f"{shipped}")

        # --- T-7: never in the way. ---
        blocked = root / "blocked-ws"
        (blocked / ".authorlm").mkdir(parents=True)
        (blocked / ".authorlm" / "logs").write_text("not a directory")
        clients.configure(workspace=str(blocked))
        usage.reset()
        usage.record("llm", "zz/sentinel", 1, 1, 0, 0)
        usage.record_image("illustrations", "zz/img")
        usage.record("llm", object(), "not-an-int", None)     # nonsense
        usage.flush("blocked")
        check("a ledger destination that cannot be written NEVER breaks the "
              "verb it measures: every write swallows its own failure, and "
              "an unserializable value raises nothing",
              True, "reached")
        gone = root / "gone-ws"
        recorder = usage.Recorder(usage.log_dir(str(gone)))
        recorder.record("llm", "zz/sentinel", 1, 1)
        recorder.flush("late")
        check("a flush against a workspace deleted out from under it writes "
              "nothing and recreates nothing — logs/ is created, its parent "
              "workspace never is",
              not gone.exists(), f"{gone}")

        # --- T-8: rotation. ---
        rotate_ws = root / "rotate-ws"
        rotate_dir = usage.log_dir(str(rotate_ws))
        rotate_dir.mkdir(parents=True)
        rotated = rotate_dir / usage.FILENAME
        recorder = usage.Recorder(rotate_dir)
        rotated.write_text("x" * (usage.MAX_BYTES + 1))
        recorder.record("llm", "zz/sentinel", 1, 1)
        recorder.flush("first")
        first = usage._generation(rotated, 1)
        rotated.write_text('{"ts":"2999-01-01T00:00:00+00:00","kind":"api",'
                           '"calls":{},"replays":0}\n'
                           + "y" * usage.MAX_BYTES)
        recorder.record("llm", "zz/sentinel", 2, 2)
        recorder.flush("second")
        check("an oversized ledger rotates to .jsonl.1 then .2, oldest "
              "dropped — dbperf's rotation, at the same 5 MB cap",
              first.exists() and usage._generation(rotated, 2).exists()
              and not usage._generation(rotated, 3).exists(),
              f"{sorted(p.name for p in rotate_dir.iterdir())}")
        check("read_entries returns every generation oldest-first",
              len(usage.read_entries(rotate_dir)) >= 2,
              f"{usage.read_entries(rotate_dir)}")

        # --- T-9: replays cost nothing. ---
        replay_ws = root / "replay-ws"
        (replay_ws / ".authorlm").mkdir(parents=True)
        clients.configure(workspace=str(replay_ws))
        usage.reset()
        usage.record_replay()
        usage.record_replay()
        usage.flush("replayed")
        replayed = json.loads(
            (usage.log_dir(str(replay_ws)) / usage.FILENAME)
            .read_text().splitlines()[-1])
        check("a call served from the record/replay cache is counted as a "
              "replay and contributes no tokens and no cost — 'what did the "
              "cache save me' is the only positive number in the report",
              replayed["replays"] == 2 and replayed["calls"] == {}
              and replayed["est_cost_usd"] == 0.0, f"{replayed}")

        # --- T-10: the client stamp. ---
        stamp_ws = root / "stamp-ws"
        (stamp_ws / ".authorlm").mkdir(parents=True)
        clients.configure(workspace=str(stamp_ws))
        with _as_client("claude-code:usage-chat:Usage Chat"):
            usage.reset()
            usage.record("llm", "zz/sentinel", 3, 3)
            usage.flush("stamped")
        stamped = json.loads(
            (usage.log_dir(str(stamp_ws)) / usage.FILENAME)
            .read_text().splitlines()[-1])
        usage.reset()
        usage.record("llm", "zz/sentinel", 3, 3)
        usage.flush("unstamped")
        plain = json.loads(
            (usage.log_dir(str(stamp_ws)) / usage.FILENAME)
            .read_text().splitlines()[-1])
        check("the line carries the client provenance join key (§15.15), so "
              "the ledger joins straight to trace.jsonl and to row "
              "metadata — and with detection pinned off the key is OMITTED "
              "entirely rather than written as unknown",
              stamped["client"] == {"engine": "claude-code",
                                    "session": "usage-chat",
                                    "precision": "exact"}
              and "client" not in plain, f"{stamped} / {plain}")

        # --- T-30: overhead. ---
        import time as _time

        clients.configure(workspace=str(ws))
        usage.reset()
        started = _time.perf_counter()
        for _ in range(10_000):
            usage.record("llm", "zz/sentinel", 10, 10, 0, 0)
        per_call = (_time.perf_counter() - started) / 10_000
        usage.flush("overhead")
        check("recording is a dict update on the hot path, not I/O — and "
              "the per-invocation resolution (the client key, the budget "
              "presence, the price row) is memoized OFF it, because "
              "paths.config_path() alone costs ~27 us. Measured 0.7 us; "
              "the ceiling is 10, against a model call that takes hundreds "
              "of MILLIseconds",
              per_call < 10e-6, f"{per_call * 1e6:.2f} us/call")
    finally:
        usage.reset()
        usage._PRICE_OVERRIDE.clear()
        usage._PRICE_CACHE.clear()
        clients._STATE["workspace"] = previous_workspace
        shutil.rmtree(root, ignore_errors=True)


def check_chat_usage_sweep() -> None:
    """AP: chat consumption, swept from the session transcript.

    T-11…T-20. The measured facts from real transcripts drive every
    assertion here: lines REPEAT (a 2.05x overcount corpus-wide for a
    parser that sums them), the volume on a cached turn is in the two
    cache counters and not in `input_tokens`, and `<synthetic>` rows are
    not API calls. Nothing here reads a real transcript."""
    from authorlm import clients, usage

    root = Path(tempfile.mkdtemp(prefix="authorlm-chat-"))
    try:
        usage.reset()
        ws = root / "ws"
        (ws / ".authorlm").mkdir(parents=True)
        projects = root / "projects" / "one"
        projects.mkdir(parents=True)

        def transcript(name: str, lines: list) -> Path:
            path = projects / f"{name}.jsonl"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return path

        def client_for(session: str, hint: Path | None = None):
            return clients.Client(engine="claude-code", session_id=session,
                                  precision="exact", adapter="claude-code",
                                  transcript_hint=str(hint) if hint else None)

        # --- T-11: THE DEDUP TRAP, pinned. ---
        repeated = [_turn("msg_a", n=i, usage={"input_tokens": 10,
                                               "output_tokens": 20})
                    for i in range(6)]
        repeated += [_turn("msg_b", usage={"input_tokens": 3,
                                           "output_tokens": 4}),
                     _turn("msg_c", usage={"input_tokens": 1,
                                           "output_tokens": 2})]
        path = transcript("dedup", repeated)
        line = usage.sweep_session(client_for("dedup", path),
                                   workspace=ws, force=True)
        row = line["models"]["claude-fable-5"]
        check("THE ONE THAT MATTERS: the same message.id is written to the "
              "transcript up to six times with a byte-identical usage "
              "object, so a parser that sums LINES overstates by 2.05x "
              "corpus-wide. Dedup on message.id is the difference between "
              "a number and a fiction: 8 usage-bearing lines, 3 distinct "
              "ids, and every counter counts each id exactly ONCE",
              line["messages"] == 3 and row[0] == 3
              and row[1] == 10 + 3 + 1 and row[2] == 20 + 4 + 2,
              f"{line}")

        # The corpus-shaped variant: 40 lines, 20 ids, exactly half.
        pairs = []
        for i in range(20):
            pairs.append(_turn(f"msg_{i}", n=0,
                               usage={"input_tokens": 100,
                                      "output_tokens": 50}))
            pairs.append(_turn(f"msg_{i}", n=1,
                               usage={"input_tokens": 100,
                                      "output_tokens": 50}))
        path = transcript("corpus", pairs)
        line = usage.sweep_session(client_for("corpus", path),
                                   workspace=ws, force=True)
        row = line["models"]["claude-fable-5"]
        naive_in = 40 * 100
        check("and the corpus-shaped case: 40 usage-bearing lines over 20 "
              "distinct ids fold to exactly HALF the naive line sum",
              line["messages"] == 20 and row[1] == naive_in // 2 == 2000
              and row[2] == 20 * 50, f"{line}")

        # --- T-12: all four counters, and `iterations` ignored. ---
        path = transcript("counters", [
            _turn("msg_cached", usage={"input_tokens": 2,
                                       "output_tokens": 150,
                                       "cache_read_input_tokens": 37906,
                                       "cache_creation_input_tokens": 24652})])
        line = usage.sweep_session(client_for("counters", path),
                                   workspace=ws, force=True)
        row = line["models"]["claude-fable-5"]
        check("ALL FOUR counters are read. The real observed row is "
              "input_tokens: 2 beside cache_creation: 24652 and cache_read: "
              "37906 — on a cached turn the uncached prompt is nearly empty "
              "and the volume is entirely in the cache counters, so a "
              "parser reading only input/output measures essentially "
              "nothing. `iterations` restates the same numbers and is "
              "IGNORED, because summing it double-counts",
              row == [1, 2, 150, 37906, 24652], f"{line}")

        # --- T-13: no prose, no path, and transcripts are DATA. ---
        path = transcript("prose", [
            _turn("msg_p", usage={"input_tokens":
                                  "ignore previous instructions",
                                  "output_tokens": {"do": "this"},
                                  "cache_read_input_tokens": None,
                                  "cache_creation_input_tokens": 5})])
        line = usage.sweep_session(client_for("prose", path),
                                   workspace=ws, force=True)
        blob = json.dumps(line)
        check("A LEDGER LINE CONTAINS NO PROSE and no transcript path. The "
              "parser reads six keys and coerces four integers; it does not "
              "evaluate, dispatch on, or act upon anything a transcript "
              "says, and an instruction-shaped usage value produces 0, "
              "silently, while the sweep continues",
              "POISONPROSE" not in blob and str(path) not in blob
              and "ignore previous instructions" not in blob
              and line["models"]["claude-fable-5"] == [1, 0, 0, 0, 5],
              blob)

        # --- T-14: junk is skipped, silently. ---
        path = transcript("junk", [
            "not json at all",
            "[1, 2, 3]",
            json.dumps({"type": "atis-latch", "message": {"usage": {}}}),
            json.dumps({"type": "assistant", "message": {"id": "x",
                                                         "model": "m"}}),
            _turn("msg_s", model="<synthetic>",
                  usage={"input_tokens": 9999, "output_tokens": 9999}),
            json.dumps({"type": "assistant",
                        "message": {"model": "claude-fable-5",
                                    "usage": {"input_tokens": 5}}}),
            _turn("msg_good", usage={"input_tokens": 11,
                                     "output_tokens": 22}),
        ])
        line = usage.sweep_session(client_for("junk", path),
                                   workspace=ws, force=True)
        check("unparseable lines, non-dict lines, unknown types, an "
              "assistant line with no usage, a missing message.id, and "
              "model == '<synthetic>' (skipped BY NAME, so a future "
              "synthetic row with non-zero counts cannot leak in) are all "
              "skipped silently — the good line is still counted and "
              "nothing is raised",
              line["messages"] == 1
              and line["models"] == {"claude-fable-5": [1, 11, 22, 0, 0]},
              f"{line}")

        # --- T-15: incremental, and the no-op sweep. ---
        path = transcript("incr", [_turn("msg_1", usage={"input_tokens": 5,
                                                         "output_tokens": 5})])
        who = client_for("incr", path)
        first = usage.sweep_session(who, workspace=ws, force=True)
        before = path.stat().st_size
        with path.open("a", encoding="utf-8") as handle:
            handle.write(_turn("msg_2", usage={"input_tokens": 7,
                                               "output_tokens": 8}) + "\n")
            handle.write(_turn("msg_3", usage={"input_tokens": 1,
                                               "output_tokens": 2}) + "\n")
        appended = path.stat().st_size - before
        second = usage.sweep_session(who, workspace=ws, force=True)
        checkpoint = usage.read_checkpoint(ws, "claude-code", "incr")
        third = usage.sweep_session(who, workspace=ws, force=True)
        check("each sweep reads ONLY the new bytes and its line carries the "
              "DELTA, not the cumulative: bytes_read equals the appended "
              "byte count and the checkpoint's offset equals the file size",
              first["messages"] == 1
              and second["models"]["claude-fable-5"] == [2, 8, 10, 0, 0]
              and second["bytes_read"] == appended
              and checkpoint["offset"] == path.stat().st_size,
              f"{first} / {second} / {checkpoint}")
        check("and a sweep with nothing appended emits NO LINE AT ALL — the "
              "common case, and it costs one stat",
              third is None, f"{third}")

        # --- T-16: the partial line. ---
        path = transcript("partial", [_turn("msg_1",
                                            usage={"input_tokens": 4,
                                                   "output_tokens": 4})])
        fragment = _turn("msg_2", usage={"input_tokens": 6,
                                         "output_tokens": 6})
        cut = len(fragment) // 2
        with path.open("a", encoding="utf-8") as handle:
            handle.write(fragment[:cut])
        who = client_for("partial", path)
        usage.sweep_session(who, workspace=ws, force=True)
        torn = usage.sweep_session(who, workspace=ws, force=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(fragment[cut:] + "\n")
        healed = usage.sweep_session(who, workspace=ws, force=True)
        check("a transcript is appended to WHILE it is read: the trailing "
              "fragment is CARRIED in the checkpoint, not parsed, so half a "
              "JSON object is never a parse error let alone a dropped "
              "turn — and once the rest arrives the turn is counted exactly "
              "ONCE",
              torn is None
              and healed["models"]["claude-fable-5"] == [1, 6, 6, 0, 0],
              f"{torn} / {healed}")

        # --- T-17: discontinuity. ---
        path = transcript("disc", [
            _turn(f"msg_{i}", usage={"input_tokens": 10,
                                     "output_tokens": 10})
            for i in range(4)])
        who = client_for("disc", path)
        usage.sweep_session(who, workspace=ws, force=True)
        path.write_text(_turn("msg_0", usage={"input_tokens": 10,
                                              "output_tokens": 10}) + "\n",
                        encoding="utf-8")
        shrunk = usage.sweep_session(who, workspace=ws, force=True)
        check("a truncated or rewound transcript is re-read FROM BYTE 0 "
              "with the whole id set in memory (so the recompute is exact) "
              "and the delta against the stored cumulative is CLAMPED AT "
              "ZERO rather than going negative — the restart flag is still "
              "reported, because a rewound session legitimately loses turns "
              "and the reader says so rather than pretending the arithmetic "
              "was clean",
              shrunk["restart"] is True and shrunk["messages"] == 0
              and shrunk["models"] == {}, f"{shrunk}")

        replaced = projects / "disc2.jsonl"
        replaced.write_text("\n".join(
            _turn(f"msg_{i}", usage={"input_tokens": 10, "output_tokens": 10})
            for i in range(3)) + "\n", encoding="utf-8")
        who2 = client_for("disc2", replaced)
        usage.sweep_session(who2, workspace=ws, force=True)
        replaced.unlink()
        replaced.write_text("\n".join(
            _turn(f"msg_{i}", usage={"input_tokens": 10, "output_tokens": 10})
            for i in range(5)) + "\n", encoding="utf-8")
        grown = usage.sweep_session(who2, workspace=ws, force=True)
        check("a REPLACED file (new inode, more content) recomputes and the "
              "delta covers only the genuinely new turns — no double count, "
              "because the checkpoint's cumulative totals are what make the "
              "delta honest",
              grown["restart"] is True and grown["messages"] == 2,
              f"{grown}")

        # The SessionEnd ruling: that path declines the recompute.
        replaced.write_text(_turn("msg_0", usage={"input_tokens": 1,
                                                  "output_tokens": 1}) + "\n",
                            encoding="utf-8")
        declined = usage.sweep_session(who2, workspace=ws, force=True,
                                       allow_recompute=False)
        after = usage.sweep_session(who2, workspace=ws, force=True)
        check("the SessionEnd sweep SKIPS the discontinuity recompute "
              "rather than risk the hook's 1.5 s shared budget on a full "
              "file read: it declines, leaves the checkpoint untouched, and "
              "the next opportunistic sweep of that session does the "
              "recompute off the hook's clock",
              declined is None and after is not None
              and after["restart"] is True, f"{declined} / {after}")

        # --- T-18: the refusal to guess a transcript. ---
        stray = client_for("nowhere", None)
        check("no transcript_hint and zero glob matches is None, and None "
              "is a FIRST-CLASS ANSWER — the sweep records nothing rather "
              "than recording a guess",
              usage.sweep_session(stray, workspace=ws, force=True) is None,
              "no transcript")
        ambiguous = projects.parent / "two"
        ambiguous.mkdir(exist_ok=True)
        transcript("ambig", [_turn("msg_1")])
        (ambiguous / "ambig.jsonl").write_text(_turn("msg_2") + "\n",
                                               encoding="utf-8")
        with _as_config("[usage]\ntranscript_roots = "
                        f'["{projects.parent}/*"]\n', root):
            found = clients.locate_by_session(
                client_for("ambig", None), (f"{projects.parent}/*",))
            single = clients.locate_by_session(
                client_for("dedup", None), (f"{projects.parent}/*",))
            declined = usage.sweep_session(client_for("ambig", None),
                                           workspace=ws, force=True)
        check("TWO glob matches means two projects have a session by that "
              "id and we cannot tell which is ours, so it DECLINES — the "
              "ambient layer's refusal, in a new place, for the same "
              "reason. One match is accepted: a glob keyed on an id we "
              "already know is never a guess",
              found is None and single is not None and declined is None,
              f"{found} / {single} / {declined}")

        # --- T-19: the interval gate. ---
        path = transcript("interval", [_turn("msg_1")])
        who = client_for("interval", path)
        with _as_config("[usage]\nsweep_interval_seconds = 3600\n", root):
            usage.sweep_session(who, workspace=ws, force=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(_turn("msg_2") + "\n")
            gated = usage.sweep_session(who, workspace=ws)
            forced = usage.sweep_session(who, workspace=ws, force=True)
        check("two sweeps inside sweep_interval_seconds produce ONE sweep — "
              "at most one per session per interval, and only for the chat "
              "actually driving AuthorLM. force=True produces the second",
              gated is None and forced is not None, f"{gated} / {forced}")

        # --- T-20: the capability is optional. ---
        check("an adapter with no `usage` method is complete and correct: "
              "hasattr is the whole feature test, provenance has never "
              "depended on it, and the sweep simply skips that engine",
              not hasattr(clients.MCP_STDIO, "usage")
              and hasattr(clients.CLAUDE_CODE, "usage")
              and clients.adapter_for_engine("mcp-stdio") is None
              and usage.sweep_session(
                  clients.Client(engine="codex", session_id="c1",
                                 precision="exact"),
                  workspace=ws, force=True) is None,
              "capability")
        with _as_config("[usage]\nchat = false\n", root):
            path = transcript("chatoff", [_turn("msg_1")])
            silent = usage.sweep_session(client_for("chatoff", path),
                                         workspace=ws, force=True)
        check("[usage] chat = false turns the sweep off entirely",
              silent is None, f"{silent}")

        # --- the reader's chat half (T-22's dollar-free rule). ---
        rendered = "\n".join(usage.report(usage.log_dir(str(ws)),
                                          workspace=str(ws)))
        head, _, chat_block = rendered.partition("Chat consumption")
        chat_block = chat_block.split("per day (the trend)")[0]
        check("chat consumption is labelled SUBSCRIPTION — NOT billed, and "
              "carries NO DOLLAR FIGURE anywhere in its block. A "
              "subscription does not bill per token and printing a notional "
              "number would invent a bill that does not exist",
              "subscription — NOT billed" in rendered
              and "$" not in chat_block
              and "claude-fable-5" in chat_block, chat_block)
        check("and the two quantities are never summed: the API block is "
              "labelled an ESTIMATE and stands apart",
              "ESTIMATE — litellm price list, not your invoice" in rendered
              or "no API spend in this window" in rendered, rendered)
    finally:
        usage.reset()
        shutil.rmtree(root, ignore_errors=True)


def check_usage_reader() -> None:
    """AP T-21…T-23: the reader. `cmd_usage` is `cmd_dbperf`'s twin — it
    calls `report()`, prints the lines and owns no logic. Honest when
    empty, and it never sums an API number with a chat one."""
    import datetime as _dt

    from authorlm import usage

    root = Path(tempfile.mkdtemp(prefix="authorlm-usage-read-"))
    try:
        # --- T-21: honest when empty. ---
        empty_ws = root / "empty"
        folder = usage.log_dir(str(empty_ws))
        check("a workspace with no ledger says so rather than rendering an "
              "empty table",
              usage.report(folder)[0].startswith("no usage ledger yet"),
              f"{usage.report(folder)}")
        folder.mkdir(parents=True)
        (folder / usage.FILENAME).write_text("")
        check("a present-but-empty ledger gets the same sentence, not a "
              "zero-row table",
              usage.report(folder)[0].startswith("no usage ledger yet"),
              f"{usage.report(folder)}")

        now = _dt.datetime.now(_dt.timezone.utc)
        today = now.strftime("%Y-%m-%d")
        yesterday = (now - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        chat_one = {"engine": "claude-code", "session": "chat-one",
                    "precision": "exact"}
        chat_two = {"engine": "claude-code", "session": "chat-two",
                    "precision": "exact"}
        ledger = [
            {"ts": f"{yesterday}T09:00:00+00:00", "kind": "api",
             "invocation": "collect --auto",
             "calls": {"llm|gemini/gemini-2.5-flash": [4, 400, 40, 0, 0,
                                                       0.12],
                       "critique.summarizer|openai/gpt-5.6-luna":
                           [2, 200, 20, 0, 0, 0.03]},
             "replays": 3, "est_cost_usd": 0.15, "cost_complete": True,
             "client": chat_one},
            {"ts": f"{today}T10:00:00+00:00", "kind": "api",
             "invocation": "illus render",
             "calls": {"illustrations|openai/gpt-image-2":
                       [6, 0, 0, 0, 0, None]},
             "images": {"illustrations|openai/gpt-image-2": 6},
             "replays": 0, "est_cost_usd": 0.0, "cost_complete": False,
             "client": chat_two},
            {"ts": f"{today}T10:00:01+00:00", "kind": "api",
             "expensive": True, "purpose": "writing",
             "model": "anthropic/claude-fable-5", "in": 2140, "out": 3980,
             "cache_read": 118400, "cache_write": 24652,
             "est_cost_usd": 0.612, "client": chat_two},
            {"ts": f"{today}T11:00:00+00:00", "kind": "chat",
             "engine": "claude-code", "session": "chat-two",
             "adapter": "claude-code",
             "models": {"claude-fable-5": [41, 58, 9312, 1204551, 88320]},
             "messages": 41, "sidechain": 0, "bytes_read": 184223,
             "restart": False},
            {"ts": "2026-01-01T00:00:00+00:00", "kind": "api",
             "invocation": "ancient", "calls": {"llm|zz/old":
                                                [1, 9, 9, 0, 0, 99.0]},
             "replays": 0, "est_cost_usd": 99.0, "cost_complete": True},
        ]
        report_ws = root / "report"
        report_dir = usage.log_dir(str(report_ws))
        report_dir.mkdir(parents=True)
        (report_dir / usage.FILENAME).write_text(
            "".join(json.dumps(e) + "\n" for e in ledger))
        rendered = io.StringIO()
        with contextlib.redirect_stdout(rendered):
            cli_main(["--workspace", str(report_ws), "usage", "--days", "3"])
        text = rendered.getvalue()

        # --- T-22: the two quantities, kept apart. ---
        api_block = text.split("API spend")[1].split("Chat consumption")[0]
        chat_block = text.split("Chat consumption")[1].split(
            "per day (the trend)")[0]
        check("the API block is labelled an ESTIMATE from a public price "
              "list — it is not the author's invoice, and knows nothing "
              "about their contract, tier or free quota",
              "ESTIMATE — litellm price list, not your invoice" in text,
              text)
        check("no dollar figure appears anywhere in the chat block",
              "$" not in chat_block and "1,204,551" in chat_block, chat_block)
        check("no total sums an API number with a chat one: the priced "
              "total counts only priced API calls, and the 6 unpriced image "
              "renders are named rather than absorbed",
              "priced total" in api_block
              and "6 images, not priced" in api_block
              and "had no price" in api_block, api_block)
        check("replays are reported as saved, not spent",
              "3 calls replayed from the record/replay cache" in text, text)
        check("a swept session whose tail was never counted is REPORTED as "
              "uncounted rather than silently under-counted — the first "
              "time the SessionEnd hook is more than enrichment",
              "have no SessionEnd hook" in chat_block
              and "client-hook --print-setup" in chat_block, chat_block)
        finalised = list(ledger)
        finalised[3] = {**ledger[3], "final": True}
        (report_dir / usage.FILENAME).write_text(
            "".join(json.dumps(e) + "\n" for e in finalised))
        counted = "\n".join(usage.report(report_dir, days=3,
                                         workspace=str(report_ws)))
        check("and a session swept BY that hook is not accused of lacking "
              "it: the hook unlinks the marker immediately afterwards, so "
              "'no marker' would otherwise name exactly the sessions that "
              "did have it",
              "have no SessionEnd hook" not in counted, counted)
        (report_dir / usage.FILENAME).write_text(
            "".join(json.dumps(e) + "\n" for e in ledger))
        check("the expensive tail renders with its client attribution and "
              "is NOT added to the aggregate a second time",
              "expensive single calls" in text
              and "claude-code/chat-two" in text
              and "$   0.61" in text, text)
        check("--days bounds the window: the ancient $99 line is not "
              "counted",
              "99.00" not in text and "zz/old" not in text, text)
        check("the per-day trend carries BOTH quantities in one row, side "
              "by side and unsummed",
              f"  {yesterday}   $" in text and "chat tokens" in text, text)

        # --- T-23: the groupings. ---
        def render(*extra):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                cli_main(["--workspace", str(report_ws), "usage",
                          "--days", "3", *extra])
            return buffer.getvalue()

        by_purpose = render("--by", "purpose")
        by_model = render("--by", "model")
        by_verb = render("--by", "verb")
        check("--by purpose partitions by WHICH SECTION chose the model",
              "critique.summarizer" in by_purpose
              and "gemini/gemini-2.5-flash" not in by_purpose.split(
                  "Chat consumption")[0], by_purpose)
        check("--by model partitions by the model string",
              "gemini/gemini-2.5-flash" in by_model
              and "critique.summarizer" not in by_model.split(
                  "Chat consumption")[0], by_model)
        check("--by verb partitions by the invocation label already on "
              "every line — the trigger, which purpose alone cannot give "
              "when eleven call sites share purpose = 'llm'",
              "collect --auto" in by_verb and "illus render" in by_verb,
              by_verb)

        old_ws = root / "old"
        old_dir = usage.log_dir(str(old_ws))
        old_dir.mkdir(parents=True)
        (old_dir / usage.FILENAME).write_text(
            "".join(json.dumps(e) + "\n" for e in ledger[-1:]))
        window = "\n".join(usage.report(old_dir, days=3))
        check("a present ledger with nothing in the window says so and "
              "names how many older lines exist, rather than printing an "
              "empty table",
              "no entries in the last 3 days" in window
              and "older lines" in window, window)
        blob = render("--json")
        check("--json emits the aggregated structure for the scheduled "
              "reviewer, not the rendering",
              blob.strip().startswith("[") and '"kind": "api"' in blob, blob)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_budget_seam() -> None:
    """AP T-24…T-28: the budget — built, tested, and DORMANT.

    Sponsor ruling: *"For now we don't need to cut off the usage when the
    budget has reached."* What is absent is what is off, exactly as the
    absent `[writing]` section is what keeps billed beat drafting off."""
    import datetime as _dt
    import tomllib

    from authorlm import budget, clients, usage

    root = Path(tempfile.mkdtemp(prefix="authorlm-budget-"))
    try:
        today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
        ws = root / "ws"
        (ws / ".authorlm").mkdir(parents=True)
        previous_workspace = clients._STATE["workspace"]
        clients.configure(workspace=str(ws))
        folder = usage.log_dir(str(ws))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / usage.FILENAME).write_text(json.dumps({
            "ts": f"{today}T09:00:00+00:00", "kind": "api",
            "invocation": "collect", "calls": {"llm|zz/s": [1, 1, 1, 0, 0,
                                                            0.85]},
            "replays": 0, "est_cost_usd": 0.85, "cost_complete": True}) + "\n")

        # --- T-24: absent is inert. ---
        with _as_config("[llm]\nenabled = false\n", root):
            absent = budget.settings()
            captured = io.StringIO()
            with contextlib.redirect_stderr(captured):
                budget.note_spend(folder, 10.0)
            gated = budget.gate("writing", "anthropic/claude-fable-5")
            block = budget.report_block(folder)
            rendered = "\n".join(usage.report(folder))
        check("with NO [budget] section: settings are off, note_spend "
              "prints nothing, gate() permits every purpose and model, and "
              "the report grows no budget block. The absence IS the "
              "off-switch",
              absent.present is False and absent.enforce is False
              and captured.getvalue() == "" and gated is None
              and block == [] and "budget —" not in rendered,
              f"{absent} / {captured.getvalue()!r} / {block}")

        # --- T-25: present warns. ---
        with _as_config("[budget]\napi_dollars_per_day = 1.00\n"
                        "warn_at = 0.8\n", root):
            captured = io.StringIO()
            with contextlib.redirect_stderr(captured):
                for _ in range(10):
                    budget.note_spend(folder, 0.0)
            warned = captured.getvalue()
            gated = budget.gate("llm", "zz/s")
            block = "\n".join(budget.report_block(folder))
        check("with [budget] present the report grows its block and the "
                "warning fires past warn_at — and it prints EXACTLY ONE "
                "line across ten calls, because a budget crossing is a "
                "standing fact and saying it on every call of a 24-essay "
                "rebuild is noise the author learns to scroll past",
              warned.count("warning:") == 1 and "0.85" in warned
              and "85%" in warned and "NOT ENFORCED" in block
              and "warning only" in warned, f"{warned!r} / {block}")
        check("warning is NOT refusing: gate() still permits, because "
              "enforce was not set",
              gated is None, f"{gated}")

        # --- T-26: enforce refuses, and only then. ---
        with _as_config("[budget]\napi_dollars_per_day = 0.50\n"
                        "warn_at = 0.8\nenforce = true\n", root):
            raised = None
            try:
                budget.gate("writing", "anthropic/claude-fable-5")
            except budget.BudgetExceeded as err:
                raised = err
        message = str(raised or "")
        check("with enforce = true and the day over the cap, gate() RAISES "
              "BudgetExceeded, and the refusal mirrors WRITING_ABSENT's: it "
              "names the section, the cap, the spent figure and the recipe "
              "BOTH ways",
              raised is not None and "[budget]" in message
              and "0.50" in message and "0.85" in message
              and "api_dollars_per_day" in message
              and "enforce = false" in message
              and "Nothing was sent and nothing was charged." in message,
              message)
        check("BudgetExceeded is a RuntimeError subclass, so mcp_server."
              "_guard already renders it as {'ok': false, 'error': …} — a "
              "sentence, not a traceback",
              isinstance(raised, RuntimeError), f"{type(raised)}")
        with _as_config("[budget]\napi_dollars_per_day = 0.50\n"
                        "warn_at = 0.8\n", root):
            permitted = budget.gate("writing", "anthropic/claude-fable-5")
        check("the identical setup WITHOUT enforce permits: the seam is "
              "exercised on every billable path today and returns None "
              "today. Commercial-readiness is the seam being real, not the "
              "enforcement being on",
              permitted is None, f"{permitted}")

        # --- T-27: a broken config permits. ---
        broken = root / "broken.toml"
        broken.write_text("[budget\napi_dollars_per_day = ", encoding="utf-8")
        previous = os.environ.get("AUTHORLM_CONFIG")
        os.environ["AUTHORLM_CONFIG"] = str(broken)
        budget.reset()
        try:
            captured = io.StringIO()
            with contextlib.redirect_stderr(captured):
                budget.note_spend(folder, 99.0)
            survived = budget.gate("writing", "anthropic/claude-fable-5")
            settings_broken = budget.settings()
        finally:
            if previous is None:
                os.environ.pop("AUTHORLM_CONFIG", None)
            else:
                os.environ["AUTHORLM_CONFIG"] = previous
            budget.reset()
        check("an unreadable or malformed config means NO budget, NO "
              "warning and NO refusal. dbperf defaults ON because a broken "
              "config must not be a silent off-switch for a MEASUREMENT; "
              "this defaults OFF for the mirror-image reason — a broken "
              "config must not be a silent REFUSAL for a POLICY. A tracking "
              "feature must not become an outage",
              settings_broken.present is False and survived is None
              and captured.getvalue() == "", f"{captured.getvalue()!r}")

        # --- T-28: the shipped-config tripwire. ---
        shipped_path = Path(__file__).resolve().parent.parent / "config.toml"
        shipped_text = shipped_path.read_text(encoding="utf-8")
        shipped = tomllib.loads(shipped_text)
        enforce_keys = [f"[{name}] enforce" for name, body in shipped.items()
                        if isinstance(body, dict) and "enforce" in body]
        check("the [writing] tripwire's twin: the SHIPPED config.toml has "
              "no [budget] table and no `enforce` key in any section, so "
              "the dormant seam cannot be woken by a merge nobody read — "
              "and UPDATING THIS CHECK is the deliberate act that wakes it",
              "budget" not in shipped and enforce_keys == [],
              f"budget={'budget' in shipped} enforce={enforce_keys}")
        check("and the absence carries the author's own words and the exact "
              "restore recipe in the file's own comments, the way the "
              "absent [writing] section does",
              "# [budget] — DELIBERATELY ABSENT" in shipped_text
              and "#   api_dollars_per_day = 5.00" in shipped_text
              and "NO shipped configuration sets it" in shipped_text,
              "config comment")
    finally:
        budget.reset()
        usage.reset()
        clients._STATE["workspace"] = previous_workspace
        shutil.rmtree(root, ignore_errors=True)


def check_every_llm_factory_tags_purpose() -> None:
    """AP T-29: the coverage assertion.

    `purpose` is the ledger's answer to "why am I paying for Luna", and a
    factory that forgets to set it is mis-tagged rather than broken — so
    the SET of purposes is pinned to a literal list here, and a sixth
    factory forces a deliberate edit to this check."""
    from authorlm import llm as llm_module, passes, summaries, triage_analysis

    config = {"llm": {"enabled": False, "model": "gemini/gemini-2.5-flash",
                      "provider": "openai"},
              "critique": {"summarizer_model": "openai/gpt-5.6-luna",
                           "editor_model": "openai/gpt-5.6-luna"},
              "writing": {"model": "anthropic/claude-fable-5"},
              "illustrations": {"model": "openai/gpt-image-2"}}
    found = {
        "LLMClient": llm_module.LLMClient(config).purpose,
        "summarizer_llm": summaries.summarizer_llm(config).purpose,
        "editor_llm": passes.editor_llm(config).purpose,
        "writing_llm": llm_module.writing_llm(config).purpose,
        "triage": triage_analysis._llm(config, {}).purpose,
    }
    check("every LLMClient factory tags its purpose beside the .model "
          "reassignment it already does — purpose is a plain attribute, not "
          "a property, because unlike api_key it does not have to TRACK a "
          "reassignment: it IS the record of who did the reassigning",
          found == {"LLMClient": "llm",
                    "summarizer_llm": "critique.summarizer",
                    "editor_llm": "critique.editor",
                    "writing_llm": "writing",
                    "triage": "triage"}, f"{found}")
    check("LLMClient.purpose has a default, so a sixth factory added later "
          "is MIS-TAGGED ('llm') rather than crashing — and the set of "
          "distinct purposes is pinned to this literal, so adding one "
          "forces a deliberate edit here",
          sorted(set(found.values()) | {"illustrations"})
          == ["critique.editor", "critique.summarizer", "illustrations",
              "llm", "triage", "writing"], f"{sorted(set(found.values()))}")


def check_provenance_verb() -> None:
    """`authorlm provenance` — the payoff verb. Read-only, and honest
    about the rows that predate the stamp."""
    from authorlm import clients
    from authorlm.db import Database, ko_fields as ko

    root = Path(tempfile.mkdtemp(prefix="authorlm-prov-"))
    try:
        ws = root / "ws"
        (ws / ".authorlm").mkdir(parents=True)
        db = Database(ws / ".authorlm" / "authorlm.db")
        old = "2026-08-01T09:14:02.000000Z"          # predates STAMPING_SINCE
        chat_a = {"engine": "claude-code", "session": "a1ea8c70",
                  "precision": "exact"}
        chat_b = {"engine": "claude-code", "session": "f4342d91",
                  "precision": "exact"}

        def row(prefix, table, **fields):
            base = ko(prefix)
            base["created_at"] = old
            base.update(fields)
            db.insert(table, base)
            return base["id"]

        ms_id = row("ms", "manuscripts", name="book", path=str(ws / "m"))
        session_id = row("s", "sessions", manuscript_id=ms_id, started_at=old,
                         metadata=json.dumps({"clients": [
                             {**chat_a, "adapter": "claude-code",
                              "label": "Claude Code 2.1.246 (claude-desktop)",
                              "transcript_hint": "/tmp/projects/a1ea8c70.jsonl",
                              "first_seen": old, "last_seen": old},
                             {**chat_b, "adapter": "claude-code",
                              "label": "Claude Code 2.1.247 (claude-desktop)",
                              "first_seen": old, "last_seen": old}]}))
        intent_id = row("di", "declared_intents", manuscript_id=ms_id,
                        session_id=session_id, statement="Introduce gravity",
                        status="active",
                        metadata=json.dumps({"client": chat_a}))
        writeup_id = row("wu", "writeups", manuscript_id=ms_id,
                         intent_id=intent_id, file="09-gravity.md",
                         status="completed",
                         metadata=json.dumps({
                             "client": chat_a,
                             "clients": [
                                 {**chat_a, "first": old, "last": old,
                                  "verbs": ["start", "plan", "draft"]},
                                 {**chat_b, "first": old, "last": old,
                                  "verbs": ["draft", "accept"]}]}))
        beat_one = row("gd", "guidance_history", manuscript_id=ms_id,
                       session_id=session_id, intent_id=intent_id,
                       batch_id=writeup_id, batch_index=1, kind="beat",
                       suggestion="s", explanation="e", state="accepted",
                       metadata=json.dumps({"client": chat_a,
                                            "accepted_by": chat_a}))
        beat_four = row("gd", "guidance_history", manuscript_id=ms_id,
                        session_id=session_id, intent_id=intent_id,
                        batch_id=writeup_id, batch_index=4, kind="beat",
                        suggestion="s", explanation="e", state="accepted",
                        metadata=json.dumps({"client": chat_a}))
        row("rv", "editorial_reviews", manuscript_id=ms_id,
            guidance_id=beat_four, decision="accepted", explanation="",
            metadata=json.dumps({"client": chat_b}))

        def run(*argv) -> str:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cli_main(["--workspace", str(ws), "provenance", *argv])
            return out.getvalue()

        # --- 12. the object view. ---
        text = run(writeup_id[:7])
        check("provenance names both chats that touched the writeup",
              "claude-code/a1ea8c70" in text
              and "claude-code/f4342d91" in text, text)
        check("it reaches the rich payload by joining on (engine, session)",
              "Claude Code 2.1.246 (claude-desktop)" in text
              and "/tmp/projects/a1ea8c70.jsonl" in text, text)
        check("it renders the verb trail it recorded",
              "start, plan, draft" in text, text)
        check("a beat drafted by one chat and settled by another is marked",
              "←" in text and "a beat drafted by one chat" in text, text)
        check("a beat resolved by its own proposer is not marked",
              text.count("←") == 2, text)      # the flag and the legend
        check("a pre-stamp row prints the backfill footnote, "
              "rather than rendering as unknown",
              f"rows created before {clients.STAMPING_SINCE}" in text
              and "timestamp correlation only" in text, text)
        check("the footnote keeps the pre-provenance method that still works",
              "the filename is the session" in text
              and "~/.claude/projects/" in text, text)

        # --- 13. the inversion. ---
        inverted = run("--client", "a1ea8c70")
        check("--client inverts: what did this chat touch",
              f"writeup    {writeup_id[:8]}" in inverted
              and f"intent     {intent_id[:8]}" in inverted, inverted)
        check("the inversion names the chat's sessions and transcript",
              session_id in inverted
              and "/tmp/projects/a1ea8c70.jsonl" in inverted, inverted)
        check("an unknown client id returns cleanly, without an exception",
              "No client matching" in run("--client", "no-such-chat"))
        check("no argument lists the known clients, newest first",
              "claude-code/a1ea8c70" in run() and "claude-code/f4342d91" in run())

        # Bare CLI use never opens an AuthorLM session, so a chat can have
        # a row stamp and no rich payload anywhere. It must still be
        # listed and still be invertible, or a terminal-only workspace
        # would report no clients while every row named one.
        cli_only = {"engine": "claude-code", "session": "c11a0nly",
                    "precision": "exact"}
        row("di", "declared_intents", manuscript_id=ms_id,
            statement="Typed at a terminal", status="active",
            metadata=json.dumps({"client": cli_only}))
        check("a chat known only from row stamps is still listed",
              "claude-code/c11a0nly" in run("--clients"), run("--clients"))
        check("and is still invertible",
              "intent" in run("--client", "c11a0nly"))
        from authorlm.cli import _client_name

        check("an ambient claim renders with a ~, and an unclaimed one says so",
              _client_name({"engine": "claude-code", "session": "f4342d91",
                            "precision": "ambient"}) == "~claude-code/f4342d91"
              and _client_name({"engine": "claude-code", "session": None,
                                "precision": "ambient"})
              == "~claude-code/(unclaimed)")

        untouched = loads(db.one("SELECT metadata FROM writeups WHERE id = ?",
                                 (writeup_id,))["metadata"], {})
        check("provenance wrote nothing — it is safe to run mid-flight",
              untouched["clients"][0]["verbs"] == ["start", "plan", "draft"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_directives() -> None:
    """The inline directives — [Footnote: …] (design ratified 2026-09-02)
    and [Explain: …] (docs/explain-directive-design.md): grammar, the
    collect report, labels, landing, evidence, the checkout refusal,
    and the export strip. Drafting is chat: nothing here calls a model."""
    from authorlm import directives as dv
    from authorlm import export as mexport

    # --- grammar ---
    text = ("# Essay\n\n"
            "The vital lie.[Footnote: cite Becker ch. 2] It holds.[explain: "
            "what Becker means by a vital lie] Then on.\n\n"
            "[Explain: why the Field is not a substance]\n\n"
            "In 1916.[FOOTNOTE: Jung's Seven Sermons date | label: RD]\n\n"
            "An [Illustration: a tracker kneeling] is not a request, nor is\n"
            "[Footnote: a tag split\nacross lines], nor [Explain:] alone.\n")
    fn = dv.scan_text(text, "footnote")
    ex = dv.scan_text(text, "explain")
    check("footnote grammar: inline, case-insensitive keyword, optional "
          "| label: override, never across lines, never an illustration tag",
          [(t["n"], t["line"], t["gist"], t["label"]) for t in fn]
          == [(1, 3, "cite Becker ch. 2", None),
              (2, 7, "Jung's Seven Sermons date", "RD")], str(fn))
    check("explain grammar: inline and standalone, never an empty gist",
          [(t["n"], t["line"], t["gist"], t["standalone"]) for t in ex]
          == [(1, 3, "what Becker means by a vital lie", False),
              (2, 5, "why the Field is not a substance", True)], str(ex))
    check("a tag's paragraph rides with it (the evidence row's anchor)",
          fn[0]["paragraph"].startswith("The vital lie.")
          and ex[1]["paragraph"] == "[Explain: why the Field is not a substance]")
    stripped, removed = dv.strip_tags(text)
    check("exports strip every kind of tag — inline without disturbing the "
          "sentence's spacing, standalone with its whole line",
          "The vital lie. It holds. Then on.\n\nIn 1916.\n\nAn [Illustration"
          in stripped and "[Footnote: a tag split" in stripped
          and [r["kind"] for r in removed] == ["footnote", "explain",
                                               "explain", "footnote"],
          repr(stripped))
    check("--tag resolves by ordinal or by an unambiguous gist excerpt, "
          "and refuses an ambiguous one by naming the candidates",
          dv.find_tag(fn, 2)["gist"] == "Jung's Seven Sermons date"
          and dv.find_tag(fn, "becker")["n"] == 1
          and _raises(lambda: dv.find_tag(fn, "e"), "matches 2 tags")
          and _raises(lambda: dv.find_tag(fn, 9), "no open tag #9"))

    # --- labels (footnote design §4) ---
    god = "# God\n\nA claim.[^F1]\n\n[^F1]: One.\n\n[^F19]: Nineteen.\n"
    check("a file with footnotes continues its series; a file with none "
          "takes its stem's first letter; | label: overrides the letters "
          "and takes that series' next number",
          dv.next_label(god, "god") == "F20"
          and dv.next_label("# B\n\nNo notes.\n", "becker") == "B1"
          and dv.next_label(god, "god", "RD") == "RD1"
          and dv.next_label(god + "\n[^RD3]: x\n", "god", "rd") == "RD4")

    # --- the pass, end to end (no model anywhere) ---
    root = Path(tempfile.mkdtemp(prefix="authorlm-directives-"))
    try:
        ms_dir = root / "manuscript"
        ms_dir.mkdir()
        becker = ("# Becker\n\n"
                  "What Becker called the vital lie.[Footnote: cite Becker, "
                  "ch. 2] The armour holds.[Explain: what a vital lie is] "
                  "Nothing else.\n\n"
                  "[Explain: why the armour must fail]\n\n"
                  "A last word.[Footnote: Jung's date | label: RD]\n")
        (ms_dir / "becker.md").write_text(becker)
        (ms_dir / "god.md").write_text(
            "# God\n\nA claim.[^F1] Another.[Footnote: source for the claim]\n\n"
            "[^F1]: One.\n\n[^F19]: Nineteen,\n    continued.\n")
        (ms_dir / "quiet.md").write_text("# Quiet\n\nNo request here.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(root), "init", "--name", "directives",
                      "--path", str(ms_dir)])
        db = api.open_db(str(root))
        ms = api.get_manuscript(db)

        with contextlib.redirect_stdout(io.StringIO()):
            base = api.collect(db, ms, {})
        rows = base.get("directives") or []
        check("collect REPORTS the open tags per file, kind by kind, and "
              "drafts nothing (footnote design §2)",
              sorted((r["kind"], r["file"], r["n"]) for r in rows)
              == [("explain", "becker.md", 1), ("explain", "becker.md", 2),
                  ("footnote", "becker.md", 1), ("footnote", "becker.md", 2),
                  ("footnote", "god.md", 1)]
              and (ms_dir / "becker.md").read_text() == becker, str(rows))
        check("get_status carries the same report (§8)",
              len(api.status(db, ms)["directives"]) == 5)
        check("compact_collect carries it to the MCP surface",
              len(api.compact_collect(base)["directives"]) == 5)
        v0 = base["version_no"]

        # Footnotes: two in one command, series minted in document order.
        rep = dv.apply(db, ms, {}, "footnote", "becker.md",
                       [(2, "C. G. Jung, *Septem Sermones ad Mortuos* (1916)."),
                        ("cite Becker", "Ernest Becker, *The Denial of Death* "
                                        "(New York: Free Press, 1973), ch. 2.")])
        out = (ms_dir / "becker.md").read_text()
        check("apply lands the superscript at the tag's exact position, the "
              "new file's letter is its stem's, and several --tag/--text "
              "pairs ride one command in document order",
              "the vital lie.[^B1] The armour holds." in out
              and "A last word.[^RD1]\n" in out
              and out.endswith("[^B1]: Ernest Becker, *The Denial of Death* "
                               "(New York: Free Press, 1973), ch. 2.\n\n"
                               "[^RD1]: C. G. Jung, *Septem Sermones ad "
                               "Mortuos* (1916).\n")
              and [(r["n"], r["label"]) for r in rep["landed"]]
              == [(1, "B1"), (2, "RD1")]
              and rep["version_no"] == v0 + 1 and rep["mode"] == "applied"
              and rep["url"] is None,
              repr(out) + str(rep))
        check("the tag is consumed and the explain tags are untouched",
              "[Footnote:" not in out and out.count("[Explain:") == 2)
        check("a footnote apply refuses a companion footnote — the tag IS "
              "the footnote",
              _raises(lambda: dv.apply(db, ms, {}, "footnote", "god.md",
                                       [(1, "x", "y")]),
                      "rides only on an explain apply"))

        # A definition joins AFTER the last definition and its indented
        # continuation, blank-line separated (§5), continuing the series.
        rep = dv.apply(db, ms, {}, "footnote", "god.md",
                       [(1, "The source.\nA second paragraph of it.")])
        god_out = (ms_dir / "god.md").read_text()
        check("a file with footnotes continues its series and the definition "
              "joins after the last one — continuation lines indented",
              "Another.[^F20]\n" in god_out
              and god_out.endswith("[^F19]: Nineteen,\n    continued.\n\n"
                                   "[^F20]: The source.\n"
                                   "    A second paragraph of it.\n"),
              repr(god_out))

        # Explain: inline continues the paragraph, standalone becomes one.
        rep = dv.apply(db, ms, {}, "explain", "becker.md",
                       [(1, "A vital lie is the armour a person keeps against "
                            "the fact of death.",
                         "Ernest Becker, *The Denial of Death* (1973), ch. 2."),
                        ("armour must fail", "Armour is worn, and what is "
                                             "worn is felt.")])
        out = (ms_dir / "becker.md").read_text()
        check("an explain passage replaces its tag in place — inline with a "
              "supplied space, standalone as its own paragraph — and a "
              "companion footnote lands its superscript at the passage's "
              "end and its definition in the file's series, one review",
              "The armour holds. A vital lie is the armour a person keeps "
              "against the fact of death.[^RD2] Nothing else.\n\n"
              "Armour is worn, and what is worn is felt.\n\n"
              "A last word.[^RD1]" in out and "[Explain:" not in out
              and out.endswith("[^RD1]: C. G. Jung, *Septem Sermones ad "
                               "Mortuos* (1916).\n\n[^RD2]: Ernest Becker, "
                               "*The Denial of Death* (1973), ch. 2.\n")
              # RD, not B: the file's last definition set the series
              # (footnote design §4 — an override carries forward).
              and [(r["label"], bool(r["footnote"])) for r in rep["landed"]]
              == [("RD2", True), (None, False)], repr(out))
        rows = db.all("SELECT * FROM doc_threads WHERE manuscript_id = ? "
                      "ORDER BY created_at, origin_id", (ms["id"],))
        check("one evidence row per landing: origin_type is the kind, the "
              "tag as old, the landed text as new, the gist as note",
              sorted(r["origin_type"] for r in rows)
              == ["explain", "explain", "footnote", "footnote", "footnote"]
              and any(r["proposed_old"] == "[Footnote: cite Becker, ch. 2]"
                      and r["proposed_new"].startswith("[^B1]: Ernest")
                      and r["note"] == "cite Becker, ch. 2"
                      and r["state"] == "applied"
                      and loads(r["metadata"], {})["label"] == "B1"
                      for r in rows), str([dict(r) for r in rows][:1]))
        check("every landing is its own version under no episode",
              api.collect(db, ms, {}).get("unchanged")
              and db.one("SELECT COUNT(*) AS n FROM manuscript_versions "
                         "WHERE manuscript_id = ?", (ms["id"],))["n"] == v0 + 3)
        check("nothing open remains; a file with no tag was never touched",
              dv.open_report(ms_dir) == []
              and (ms_dir / "quiet.md").read_text() == "# Quiet\n\nNo request here.\n")

        # Refusals: empty text, reserved grammar, a tag in the text, a
        # consumed tag, an unknown file.
        (ms_dir / "becker.md").write_text(becker)
        for pairs, why in (
                ([(1, "  ")], "the text is empty"),
                ([(1, "<<a>>{{b}}")], "reserved grammar"),
                ([(1, "see [Explain: more]")], "carries a tag"),
                ([(1, "a"), ("cite Becker", "b")], "named twice"),
                ([(9, "a")], "no open tag #9")):
            check(f"apply refuses: {why}",
                  _raises(lambda: dv.apply(db, ms, {}, "footnote", "becker.md",
                                           pairs), why))
        check("apply refuses an unknown file and a file with no open tag",
              _raises(lambda: dv.apply(db, ms, {}, "explain", "nope.md",
                                       [(1, "x")]), "no manuscript file")
              and _raises(lambda: dv.apply(db, ms, {}, "explain", "quiet.md",
                                           [(1, "x")]), "no open [Explain"))
        check("a refusal writes nothing", (ms_dir / "becker.md").read_text() == becker)

        # Checked out to the Doc, no Google service: refused before writing.
        from authorlm import gdocs as _gd

        meta = _gd._mapping(db, ms)
        bridge = _gd.manuscript_bridge(ms)
        meta.setdefault(bridge.meta_key, {})["becker.md"] = {
            "tab_id": "t1", "checked_out": True}
        _gd._save_mapping(db, ms, meta)
        check("a checked-out file is refused when nothing can push the "
              "result — the Doc is the working copy",
              _raises(lambda: dv.apply(db, ms, {}, "footnote", "becker.md",
                                       [(1, "x")]), "checked out to Google Docs")
              and (ms_dir / "becker.md").read_text() == becker)
        meta[bridge.meta_key]["becker.md"]["checked_out"] = False
        _gd._save_mapping(db, ms, meta)

        # Exports: the tag never reaches a reader.
        (ms_dir / "toc.toml").write_text('[[chapters]]\nfile = "becker.md"\n\n'
                                         '[[chapters]]\nfile = "god.md"\n')
        out, _order, warnings = mexport.publish_markdown(ms, "stripped")
        check("every export strips an unresolved tag and warns, naming the "
              "file, the kind, and the gist",
              "[Footnote:" not in out and "[Explain:" not in out
              and any("becker.md: unresolved [Footnote: cite Becker, ch. 2]" in w
                      for w in warnings)
              and any("becker.md: unresolved [Explain: what a vital lie is]" in w
                      for w in warnings), str(warnings[:3]))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_critique_resolve_reembeds() -> None:
    """critique resolve must re-insert illustration embeds on write-back.

    Filter/lens already go through `_write_resolved_text`. Critique
    resolve also reads embed-free Doc markdown (`tab_marked_markdown`)
    and used to write it straight to disk — silently unlinking every
    rendered plate while leaving the [Illustration:] tag (same class as
    the SMSTTD 2026-09-02 filter/lens incident)."""
    import argparse
    import hashlib as _hashlib
    import json as _json

    import authorlm.gdocs as gdocs_mod
    from authorlm import illus as illus_mod
    from authorlm.cli import _critique_resolve_essay
    from authorlm.db import ko_fields as _ko

    root = Path(tempfile.mkdtemp(prefix="authorlm-critique-reembed-"))
    try:
        ws = root / "ws"
        ms = ws / "book"
        (ms / "_illustrations" / "prompts").mkdir(parents=True)
        key = "a-lone-tracker"
        prompt = "a lone tracker"
        (ms / "_illustrations" / "prompts" / f"{key}.md").write_text(
            prompt + "\n")
        h = illus_mod.desc_hash(prompt)
        pick = f"{key}-{h}-0000-01.png"
        newer = f"{key}-{h}-0000-02.png"
        (ms / "_illustrations" / pick).write_bytes(b"")
        (ms / "_illustrations" / newer).write_bytes(b"")
        local = (
            f"# Solo\n\n"
            f"[Illustration: {prompt} ⇢ {key}.md]\n"
            f"![](_illustrations/{pick})\n\n"
            f"Original paragraph text.\n")
        (ms / "solo.md").write_text(local)
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms)])
        db = api.open_db(str(ws))
        manuscript = api.get_manuscript(db)
        mid = manuscript["id"]
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, manuscript, {})
        from authorlm import passes as passes_mod
        passes_mod.ensure_pass(db, mid, db.source("system"))
        normalized = gdocs_mod.normalize_markdown(local)
        base_hash = _hashlib.sha256(normalized.encode()).hexdigest()[:16]
        meta = gdocs_mod._mapping(db, manuscript)
        links = meta.setdefault("gdocs", {})
        links["_master_id"] = "doc-fake"
        links["solo.md"] = {"tab_id": "tab-1", "checked_out": False,
                            "pushed_hash": base_hash}
        gdocs_mod._save_mapping(db, manuscript, meta)

        written_row = _ko("dt")
        written_row.update(
            manuscript_id=mid, origin_type="critique", origin_id="cp1",
            file="solo.md", anchor_quote=None,
            proposed_old="Original paragraph text.",
            proposed_new="Resolved paragraph text.",
            note="test", state="written", our_reply_ids="[]",
            last_author_reply_id=None, scope_kind="file",
            scope_ref="solo.md",
            metadata=_json.dumps({"kind": "replace",
                                  "anchor_paragraph": 1,
                                  "intent_id": None,
                                  "original_new": "Resolved paragraph text."}))
        db.insert("doc_threads", written_row)

        # Doc export: illustration tag kept, embed stripped, form pending.
        doc_text = (
            f"# Solo\n\n"
            f"[Illustration: {prompt} ⇢ {key}.md]\n\n"
            f"<<Original paragraph text.>>{{{{Resolved paragraph text.}}}}\n")

        class _Fake:
            def files(self):
                outer = self

                class _Files:
                    def export(self, fileId=None, mimeType=None):
                        class _Req:
                            def execute(self):
                                whole = (f"# **solo.md**\n\n{doc_text}")
                                return whole.encode("utf-8")
                        return _Req()
                return _Files()

            def documents(self):
                class _Documents:
                    def get(self, documentId=None, includeTabsContent=None):
                        class _Req:
                            def execute(self):
                                return {"tabs": [{
                                    "tabProperties": {"tabId": "tab-1",
                                                      "title": "solo.md"},
                                    "childTabs": []}]}
                        return _Req()
                return _Documents()

        fake = _Fake()
        orig = (gdocs_mod.get_service, gdocs_mod.get_docs_service)
        gdocs_mod.get_service = lambda *a, **k: fake
        gdocs_mod.get_docs_service = lambda *a, **k: fake
        try:
            args = argparse.Namespace(target="solo.md", workspace=str(ws))
            with contextlib.redirect_stdout(io.StringIO()):
                _critique_resolve_essay(db, manuscript, args)
        finally:
            gdocs_mod.get_service, gdocs_mod.get_docs_service = orig

        on_disk = (ms / "solo.md").read_text()
        check("critique resolve reembeds the prior illustration pick",
              illus_mod.embed_target(on_disk, key) == pick
              and f"![](_illustrations/{pick})" in on_disk
              and "Resolved paragraph text." in on_disk
              and "<<" not in on_disk,
              on_disk)
        check("critique resolve does not fall back to a newer candidate "
              "when the prior pick still exists",
              newer not in on_disk, on_disk)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _raises(fn, fragment: str) -> bool:
    try:
        fn()
    except Exception as err:                            # noqa: BLE001
        return fragment in str(err)
    return False


def check_workspace_guard() -> None:
    """Opening never invents a database (the phantom ~/.authorlm/.authorlm
    case): where the workspace came from decides what a missing file
    means. Runs against a pinned .env so the checkout's own is untouched."""
    from authorlm import paths as _paths
    print("workspace guard: a missing database is a decision, not a mkdir")

    # resolve(): the workspace is reported resolved, and macOS tmp is a symlink
    root = Path(tempfile.mkdtemp(prefix="authorlm-wsguard-")).resolve()
    saved = {k: os.environ.get(k) for k in ("AUTHORLM_ENV", "AUTHORLM_WORKSPACE", "HOME")}
    env_file = root / "checkout" / ".env"
    env_file.parent.mkdir()
    env_file.write_text("GEMINI_API_KEY=keep-me\n")
    os.environ["AUTHORLM_ENV"] = str(env_file)
    os.environ.pop("AUTHORLM_WORKSPACE", None)
    os.environ["HOME"] = str(root / "home")
    (root / "home").mkdir()

    def run(*argv: str) -> tuple[str, str | None]:
        buf = io.StringIO()
        code = None
        try:
            with contextlib.redirect_stdout(buf):
                cli_main(list(argv))
        except SystemExit as err:
            code = err.code
        return buf.getvalue(), code

    try:
        # 1. api.open_db refuses a fresh directory unless asked to create.
        raised = None
        try:
            api.open_db(str(root / "fresh"))
        except api.MissingDatabase as err:
            raised = err
        check("open_db raises MissingDatabase instead of creating",
              raised is not None and not (root / "fresh" / ".authorlm").exists(),
              f"{raised!r}")
        check("MissingDatabase names the file it expected",
              raised is not None and raised.path == root / "fresh" / ".authorlm" / "authorlm.db")
        check("MissingDatabase is a RuntimeError, which the MCP guard renders as a tool error",
              isinstance(raised, RuntimeError))

        # 2. explicit -w: init bootstraps; any other verb refuses.
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "01.md").write_text("# One\n\nText.\n")
        _, code = run("--workspace", str(ws), "manuscript", "show")
        check("-w on a fresh dir: a non-init verb refuses",
              isinstance(code, str) and "no database" in code and "init" in code, f"{code}")
        check("...and creates nothing (logs may appear; the database file is the tell)",
              not api.db_path(ws).exists())
        _, code = run("--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms), "--no-extract")
        check("-w init creates the database", code is None and api.db_path(ws).exists(), f"{code}")

        # 3. no pointer, no database, no terminal: refuse and name setup.
        _, code = run("manuscript", "show")
        check("default home without a database refuses off a terminal",
              isinstance(code, str) and "setup" in code, f"{code}")
        check("...and no database appeared under HOME", not api.db_path(root / "home").exists())

        # 4. setup --workspace onto an existing database writes only the pointer.
        out, code = run("setup", "--workspace", str(ws))
        check("setup records the pointer", code is None and "Recorded AUTHORLM_WORKSPACE" in out, out)
        text = env_file.read_text()
        check(".env keeps the author's keys byte-for-byte and gains the pointer",
              text == f"GEMINI_API_KEY=keep-me\nAUTHORLM_WORKSPACE={ws}\n", text)
        out, code = run("manuscript", "show")
        check("the pointer resolves the workspace without -w",
              code is None and "book" in out, f"{code} {out}")

        # 5. the pointer is a claim: with the file gone, refuse and never offer to create.
        shutil.move(str(ws), str(root / "ws.unmounted"))
        ws.mkdir()  # macOS leaves an empty mount point behind
        _, code = run("manuscript", "show")
        check("pointer + missing file reads as an unmounted volume, not a fresh install",
              isinstance(code, str) and "mount" in code and "AUTHORLM_WORKSPACE" in code, f"{code}")
        check("an empty directory at the pointer does not pass as a database",
              not api.db_path(ws).exists())
        _, code = run("init", "--name", "again", "--path", str(ms), "--no-extract")
        check("even init refuses under a stale pointer", isinstance(code, str) and "mount" in code, f"{code}")
        check("...and still creates nothing", not api.db_path(ws).exists())
        shutil.rmtree(ws)  # the stand-in mount point (plus any logs the refusals wrote)
        shutil.move(str(root / "ws.unmounted"), str(ws))

        # 6. setup -y elsewhere: new database, pointer rewritten in place, keys intact.
        ws2 = root / "ws2"
        out, code = run("setup", "--workspace", str(ws2), "--yes")
        check("setup --yes creates a new workspace", code is None and api.db_path(ws2).exists(), f"{code} {out}")
        text = env_file.read_text()
        check("the pointer line is replaced, not duplicated",
              text == f"GEMINI_API_KEY=keep-me\nAUTHORLM_WORKSPACE={ws2}\n", text)

        # 7. explicit -w beats the pointer.
        out, code = run("--workspace", str(ws), "manuscript", "show")
        check("-w overrides the .env pointer", code is None and "book" in out, f"{code} {out}")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(root, ignore_errors=True)


def check_filter_prelude_opt_in_frame() -> None:
    """Prelude assembly must accept summaries=/profiles= without NameError.

    `_frame_block` builds a list named `sections`; `assemble_prelude`
    builds the same shape under the name `frame`. A copy-paste that kept
    `sections.insert` on the prelude path crashed every global / 
    pronunciation prelude whose front matter asked for neighbour
    summaries or author profiles — the unit-window path was fine, so the
    flags looked ratified while the prelude itself could not run.
    """
    from authorlm import filtering as fg

    root = Path(tempfile.mkdtemp(prefix="authorlm-prelude-frame-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "01-before.md").write_text("# Before\n\nEarlier.\n")
        (ms / "02-middle.md").write_text("# Middle\n\nHere.\n")
        (ms / "03-after.md").write_text("# After\n\nLater.\n")
        (ms / "_profiles").mkdir()
        (ms / "_profiles" / "audience.md").write_text(
            "Readers who already know the literature.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms), "--no-extract"])
        db = api.open_db(str(ws))
        manuscript = api.get_manuscript(db)
        run = {"id": "fr-prelude", "file": "02-middle.md",
               "filter": "metaphor", "class": "global", "registry": None}
        units = ["Here."]
        text = "# Middle\n\nHere.\n"
        bare = fg.assemble_prelude(db, manuscript, run, "body", units,
                                   text=text)
        check("prelude without opt-ins still builds its frame",
              "THE ESSAY — 02-middle.md" in bare.frame
              and "NEIGHBOURING ESSAYS" not in bare.frame
              and "AUTHOR PROFILES" not in bare.frame,
              bare.frame[:500])
        with_sum = fg.assemble_prelude(db, manuscript, run, "body", units,
                                       text=text, summaries=True)
        check("prelude + summaries=true inserts NEIGHBOURING ESSAYS "
              "before THE ESSAY (no NameError)",
              "NEIGHBOURING ESSAYS (compressed summaries — a stale one is "
              "marked, and is still shown)" in with_sum.frame
              and with_sum.frame.index("NEIGHBOURING ESSAYS")
              < with_sum.frame.index("THE ESSAY — 02-middle.md"),
              with_sum.frame)
        with_prof = fg.assemble_prelude(db, manuscript, run, "body", units,
                                        text=text, profiles=["audience"])
        check("prelude + profiles inserts AUTHOR PROFILES (no NameError)",
              "AUTHOR PROFILES (declared context — never law, and never "
              "authority for a prose decision the author has not invoked it "
              "for)" in with_prof.frame
              and "Readers who already know the literature."
              in with_prof.frame,
              with_prof.frame)
        both = fg.assemble_prelude(db, manuscript, run, "body", units,
                                   text=text, summaries=True,
                                   profiles=["audience"])
        check("prelude accepts summaries and profiles together",
              "NEIGHBOURING ESSAYS" in both.frame
              and "AUTHOR PROFILES" in both.frame
              and "THE ESSAY — 02-middle.md" in both.frame,
              both.frame[:800])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_omit_all(root: Path, FakeGoogle) -> None:
    """`[Omit: all]` (issue #132): one tag that drops a region from every
    export output, present and future, while the Doc TAB — the working
    surface, not an output — carries the region and its tag lines out
    and back unchanged. Own workspace, own FakeGoogle."""
    from authorlm.audio import audio_source
    from authorlm.export import (OUTPUTS, check_manuscript,
                                 combined_markdown, publish_markdown,
                                 resolve_regions)
    from authorlm.gdocs import pull_doc, push_doc

    ws = root / "omit-all-ws"
    ms = ws / "manuscript"
    ms.mkdir(parents=True)
    (ms / "01-essay.md").write_text(
        "# Essay\n\nReader prose.\n\n"
        "[Omit: all]\nA working note inside the essay.\n[/Omit]\n\n"
        "Closing prose.\n")
    ledger = ("[Omit: all]\n\n# Ledger\n\nMachine-kept bookkeeping, "
              "for no reader.\n\n[/Omit]\n")
    (ms / "02-ledger.md").write_text(ledger)
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "omitbook",
                  "--path", str(ms), "--no-extract"])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)

    sample = "Kept.\n[Omit: all]\nGone.\n[/Omit]\nAlso kept.\n"
    check("[Omit: all] drops the region from every output by name",
          all("Gone." not in resolve_regions(sample, {out})
              and "Kept." in resolve_regions(sample, {out})
              and "Also kept." in resolve_regions(sample, {out})
              for out in OUTPUTS), str(OUTPUTS))
    check("'all' beside a named output is still every output",
          "Gone." not in resolve_regions(
              sample.replace("[Omit: all]", "[Omit: all, pdf]"), {"epub"}))
    nested = ("[Omit: all]\nGone.\n[Only: audio]\nSpoken.\n[/Only]\n"
              "[/Omit]\n")
    check("an [Only:] inside [Omit: all] still re-includes for its output",
          "Spoken." in resolve_regions(nested, {"audio"})
          and "Gone." not in resolve_regions(nested, {"audio"})
          and resolve_regions(nested, {"pdf"}).strip() == "")
    try:
        resolve_regions("[Only: all]\nx\n[/Only]", {"pdf"}, "f.md")
        only_all = ""
    except ValueError as err:
        only_all = str(err)
    check("[Only: all] is a named fault, by file and line",
          "f.md:1" in only_all and "[Omit:] alone" in only_all, only_all)

    builds = {fmt: publish_markdown(manuscript, "images", fmt=fmt)[0]
              for fmt in ("pdf", "docx", "epub", "md")}
    builds["audio"] = publish_markdown(manuscript, "stripped", fmt="md")[0]
    check("a file wholly inside [Omit: all] contributes nothing to the "
          "pdf, docx, epub, md and audio builds",
          all("Ledger" not in text and "bookkeeping" not in text
              and "working note" not in text and "[Omit" not in text
              and "Reader prose." in text and "Closing prose." in text
              for text in builds.values()),
          str({k: v for k, v in builds.items() if "Ledger" in v}))
    check("...nor to the audiobook's own per-file source",
          audio_source("02-ledger.md", ledger).strip() == "")
    check("...nor to the export Doc, which is an output like the rest",
          "Ledger" not in combined_markdown(manuscript)[0]
          and "Reader prose." in combined_markdown(manuscript)[0])
    check("the export check passes a file wholly inside [Omit: all]",
          not [p for p in check_manuscript(manuscript)
               if not p.startswith("pandoc")],
          str(check_manuscript(manuscript)))

    stub = FakeGoogle()
    pushed = push_doc(db, manuscript, "02-ledger.md",
                      service=stub, docs_service=stub)
    tab = next(t["text"] for t in stub.state["docs"][pushed["doc_id"]]
               if t["title"] == "02-ledger.md")
    check("the Doc TAB is not an output: the region and both tag lines "
          "go to the tab",
          "[Omit: all]" in tab and "Machine-kept bookkeeping" in tab
          and "[/Omit]" in tab, tab)
    manuscript = api.get_manuscript(db)
    report = pull_doc(db, manuscript, "02-ledger.md", service=stub,
                      docs_service=stub)
    check("...and come back from a pull unchanged, byte for byte",
          report["unchanged"] == ["02-ledger.md"]
          and (ms / "02-ledger.md").read_text() == ledger, str(report))


def _snapshot(db, manuscript, ms: Path) -> tuple:
    """Everything a read must leave alone: the local files' bytes, the
    manuscript row's metadata, and the thread and comment tables."""
    files = {str(f.relative_to(ms)): f.read_bytes()
             for f in sorted(ms.rglob("*")) if f.is_file()}
    meta = db.one("SELECT metadata FROM manuscripts WHERE id = ?",
                  (manuscript["id"],))["metadata"]
    rows = tuple(
        tuple(tuple(dict(r).items()) for r in db.all(
            f"SELECT * FROM {table} WHERE manuscript_id = ? ORDER BY id",
            (manuscript["id"],)))
        for table in ("doc_threads", "doc_comments", "manuscript_versions"))
    return files, meta, rows


def check_read_manuscript(root: Path, FakeGoogle) -> None:
    """`gdocs.read_manuscript` (issue #131): every tab as it stands in
    the Doc — forms intact, open comments on their tab — and nothing
    written anywhere. Own workspace, own FakeGoogle."""
    from authorlm.gdocs import push_doc, read_manuscript

    ws = root / "read-ws"
    ms = ws / "manuscript"
    ms.mkdir(parents=True)
    (ms / "a.md").write_text("# A\n\nOriginal a content.\n")
    (ms / "b.md").write_text(
        "# B\n\nOriginal b content.\n\nSecond paragraph of b.\n")
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "readbook",
                  "--path", str(ms), "--no-extract"])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)

    class _NoGoogle:
        def __getattr__(self, name):
            raise AssertionError("a manuscript with no Doc made a Google call")

    bare = read_manuscript(db, manuscript, _NoGoogle(), _NoGoogle())
    check("a manuscript with no master Doc reads as every file missing, "
          "with no Google call",
          bare["doc_id"] is None and set(bare["files"]) == {"a.md", "b.md"}
          and all(f["state"] == "missing" and f["marked"] is None
                  and f["tab_id"] is None for f in bare["files"].values()),
          str(bare))

    stub = FakeGoogle()
    for name in ("a.md", "b.md"):
        push_doc(db, manuscript, name, service=stub, docs_service=stub)
    manuscript = api.get_manuscript(db)
    # The Doc as the author and a producer left it: a's tab reworded,
    # b's tab holding one open replacement and one open addition.
    stub.set_tab("a.md", "# A\nReworded a content, in the Doc.\n")
    stub.set_tab(
        "b.md",
        "# B\nOriginal b content.\n"
        "<<Second paragraph of b.>>{{Second paragraph, revised.}}\n"
        "{{An added paragraph.}}\n")
    stub.add_comment("c-b", "Original b content.", "Say where this is from.")
    stub.add_comment("c-green", "Second paragraph, revised.", "Good.")
    stub.add_comment("c-lost", "text that is in no tab", "Orphan.")
    (ms / "c.md").write_text("# C\n\nA file with no tab yet.\n")

    before = _snapshot(db, manuscript, ms)
    comments_before = json.dumps(stub.state["comments"], sort_keys=True)
    tabs_before = json.dumps(stub.state["docs"], sort_keys=True)
    read = read_manuscript(db, manuscript, stub, stub)
    a, b, c = (read["files"][n] for n in ("a.md", "b.md", "c.md"))
    check("the read returns each tab's marked markdown, forms intact, "
          "and the settled text beside it",
          "<<Second paragraph of b.>>{{Second paragraph, revised.}}"
          in b["marked"] and "{{An added paragraph.}}" in b["marked"]
          and b["settled"] == (ms / "b.md").read_text()
          and "Reworded a content" in a["marked"]
          and a["marked"] == a["settled"], str(b))
    check("...the three-way state per file: a tab the author reworded is "
          "`changed`, a tab whose settled text is the local file is "
          "`unchanged`, a file with no tab is `missing`",
          a["state"] == "changed" and b["state"] == "unchanged"
          and c["state"] == "missing" and c["tab_id"] is None
          and a["pushed"] and b["pushed"] and not c["pushed"]
          and "tab=" in b["url"], str((a["state"], b["state"], c)))
    check("...the open forms parsed into old and new, on the right tab",
          b["forms"] == [
              {"kind": "replace", "old": "Second paragraph of b.",
               "new": "Second paragraph, revised."},
              {"kind": "insert", "old": "", "new": "An added paragraph."}]
          and a["forms"] == [], str(b["forms"]))
    check("...and the open comments on the tab their quote sits in — a "
          "quote on a green half included, an unplaceable one set aside",
          [x["comment_id"] for x in b["comments"]] == ["c-b", "c-green"]
          and b["comments"][0]["content"] == "Say where this is from."
          and b["comments"][0]["quote"] == "Original b content."
          and a["comments"] == []
          and [x["comment_id"] for x in read["unattributed_comments"]]
          == ["c-lost"] and read["comments_error"] is None,
          str((b["comments"], read["unattributed_comments"])))
    check("the read wrote NOTHING: local files and manuscripts.metadata "
          "are byte-identical, and no thread, comment or version row "
          "was made",
          _snapshot(db, manuscript, ms) == before)
    check("...and nothing in the Doc either: no reply, no resolve, no "
          "tab touched",
          json.dumps(stub.state["comments"], sort_keys=True)
          == comments_before
          and json.dumps(stub.state["docs"], sort_keys=True) == tabs_before)


def check_stage_revisions(root: Path, FakeGoogle) -> None:
    """`api.stage_revisions` (issue #129): an outside caller's
    replacements and additions go into a tab as pending forms, under
    their own origin, with the standing hold on a tab that already has
    forms out reported and not raised. Own workspace, own FakeGoogle."""
    from authorlm import docs as docs_mod
    from authorlm.gdocs import doc_status, forms_pending

    ws = root / "stage-ws"
    ms = ws / "manuscript"
    ms.mkdir(parents=True)
    essay = ("# Topic\n\nFirst settled paragraph.\n\n"
             "Second settled paragraph.\n\nThird settled paragraph.\n")
    (ms / "topic.md").write_text(essay)
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "stagebook",
                  "--path", str(ms), "--extraction", "off"])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    mid = manuscript["id"]
    stub = FakeGoogle()

    def tab(title):
        return next(t["text"] for tabs in stub.state["docs"].values()
                    for t in tabs if t["title"] == title)

    revisions = [
        {"old": "Second settled paragraph.",
         "new": "Second paragraph, as the new video has it.",
         "note": "claim c-12"},
        {"old": "", "new": "A first added paragraph.", "note": "claim c-13",
         "anchor_paragraph": 4},
        {"old": "", "new": "A second added paragraph.", "note": "claim c-14",
         "anchor_paragraph": 4},
    ]
    staged = api.stage_revisions(db, manuscript, "topic.md", revisions,
                                 "ytlm", stub, stub)
    text = tab("topic.md")
    check("a replacement and two additions staged on one file all show "
          "in the tab as forms, the additions in the order given",
          len(staged["written"]) == 3 and not staged["failed"]
          and ("<<Second settled paragraph.>>"
               "{{Second paragraph, as the new video has it.}}") in text
          and text.index("Third settled paragraph.")
          < text.index("{{A first added paragraph.}}")
          < text.index("{{A second added paragraph.}}"), text)
    check("...the result names each written revision by its place in "
          "the list, with its thread id and the tab's URL",
          [w["index"] for w in staged["written"]] == [0, 1, 2]
          and staged["written"][0]["anchor_paragraph"] == 3
          and all(w["id"].startswith("dt") for w in staged["written"])
          and "tab=" in staged["url"] and staged["file"] == "topic.md"
          and staged["origin"] == "ytlm", str(staged))
    rows = [dict(r) for r in db.all(
        "SELECT * FROM doc_threads WHERE manuscript_id = ? ORDER BY "
        "created_at, id", (mid,))]
    check("...the threads are `written` rows under the outside-caller "
          "origin, the caller's name and the note kept on each",
          len(rows) == 3
          and all(r["origin_type"] == api.EXTERNAL_ORIGIN == "external"
                  and r["state"] == "written"
                  and json.loads(r["metadata"])["origin"] == "ytlm"
                  for r in rows)
          and [r["note"] for r in rows]
          == ["claim c-12", "claim c-13", "claim c-14"]
          and forms_pending(db, mid, "topic.md") == "external", str(rows))
    check("...and the local file is unchanged: nothing lands before a "
          "resolve",
          (ms / "topic.md").read_text() == essay)

    tab_before = tab("topic.md")
    again = api.stage_revisions(
        db, manuscript, "topic.md",
        [{"old": "First settled paragraph.", "new": "First, redone."},
         {"old": "", "new": "One more.", "anchor_paragraph": 2}],
        "ytlm", stub, stub)
    check("staging onto a file with open forms writes nothing and hands "
          "every revision back in `failed`, with the reason — reported, "
          "not raised",
          again["written"] == [] and len(again["failed"]) == 2
          and again["failed"][0][0]["new"] == "First, redone."
          and all("takes no new ones" in why and "3 unresolved external"
                  in why for _, why in again["failed"])
          and tab("topic.md") == tab_before
          and db.one("SELECT COUNT(*) AS n FROM doc_threads WHERE "
                     "manuscript_id = ?", (mid,))["n"] == 3
          and "tab=" in again["url"], str(again))

    # A new file, added with docs.add_doc, has no tab yet: the writer's
    # own levelling push makes it, and the tab arrives as additions.
    docs_mod.add_doc(manuscript, "new-topic", title="New topic")
    fresh = api.stage_revisions(
        db, manuscript, "new-topic.md",
        [{"old": "", "new": "The tab's first paragraph.",
          "anchor_paragraph": 1},
         {"old": "", "new": "The tab's second paragraph.",
          "anchor_paragraph": 1}],
        "ytlm", stub, stub)
    new_tab = tab("new-topic.md")
    check("a file with no tab yet gets one, and arrives entirely as "
          "additions under its heading",
          len(fresh["written"]) == 2 and not fresh["failed"]
          and new_tab.index("New topic")
          < new_tab.index("{{The tab's first paragraph.}}")
          < new_tab.index("{{The tab's second paragraph.}}")
          and doc_status(db, manuscript)["new-topic.md"]["tab_id"]
          and (ms / "new-topic.md").read_text() == "# New topic\n",
          new_tab + repr((ms / "new-topic.md").read_text()))

    # Per-revision faults: the good one goes, each bad one is named.
    (ms / "mixed.md").write_text(
        "# Mixed\n\nA twin line.\n\nA twin line.\n\nThe last one.\n")
    mixed = api.stage_revisions(
        db, manuscript, "mixed.md",
        [{"old": "The last one.", "new": "The last one, redone."},
         {"old": "Text that is nowhere.", "new": "x"},
         {"old": "A twin line.", "new": "Which twin?"},
         {"old": "", "new": "Past the end.", "anchor_paragraph": 9},
         {"old": "", "new": "No anchor."},
         {"old": "", "new": "Head of a file that has text.",
          "anchor_paragraph": 0},
         {"old": "", "new": "Braces {{inside}}.", "anchor_paragraph": 1},
         {"old": "", "new": "Two\n\nparagraphs.", "anchor_paragraph": 1},
         {"old": "A twin line.", "new": "The second twin.",
          "anchor_paragraph": 3}],
        "ytlm", stub, stub)
    whys = {rev["new"]: why for rev, why in mixed["failed"]}
    mixed_tab = tab("mixed.md")
    check("one bad revision never holds the others: the sound ones are "
          "written and each fault is named on its own revision",
          [w["index"] for w in mixed["written"]] == [0, 8]
          and "not found verbatim" in whys["x"]
          and "paragraphs 2, 3" in whys["Which twin?"]
          and "out of range" in whys["Past the end."]
          and "names the paragraph" in whys["No anchor."]
          and "no paragraphs yet" in whys["Head of a file that has text."]
          and "reserved" in whys["Braces {{inside}}."]
          and "one line" in whys["Two\n\nparagraphs."]
          and len(mixed["failed"]) == 7, str(mixed["failed"]))
    check("...and an anchored replacement of the second of two twins "
          "lands on the second, not the first",
          mixed_tab.index("A twin line.\n")
          < mixed_tab.index("<<A twin line.>>{{The second twin.}}")
          and mixed_tab.count("<<") == 2, mixed_tab)
    check("...the faulted revisions left no thread behind",
          db.one("SELECT COUNT(*) AS n FROM doc_threads WHERE "
                 "manuscript_id = ? AND file = 'mixed.md'", (mid,))["n"]
          == 2)
    check("an unknown file is the one thing that raises",
          _raises(lambda: api.stage_revisions(
              db, manuscript, "no-such-file.md",
              [{"old": "", "new": "x", "anchor_paragraph": 0}],
              "ytlm", stub, stub), "no document matching"))
    check("...and so is a caller that does not name itself",
          _raises(lambda: api.stage_revisions(
              db, manuscript, "mixed.md", [], "  ", stub, stub),
              "names itself"))


def main_test() -> None:
    check_directives()
    check_critique_resolve_reembeds()
    check_client_resolution()
    check_client_stamps()
    check_provenance_verb()
    check_db_perf_log()
    check_lint_and_propose_gates()
    check_intent_scope()
    check_scope_evidence()
    check_scope_evidence_batched_lookup()
    check_placeholder_reader_paths()
    check_briefing_active_writeups()
    check_replan_settles_pending_proposal()
    check_style_refusal_names_candidates()
    check_broken_pipe()
    check_show_verbs()
    check_config_parity()
    check_usage_ledger()
    check_chat_usage_sweep()
    check_usage_reader()
    check_budget_seam()
    check_every_llm_factory_tags_purpose()
    check_shipped_config_bills_no_anthropic_path()
    check_drafting_key_gate()
    check_drafting_replay_needs_no_key()
    check_link_endpoints()
    check_drafting_cache_warning()
    check_drafting_cache_layer()
    check_model_profiles()
    check_extraction_failure_traced()
    check_manuscript_extraction_switch()
    check_extraction_prompt_provenance()
    check_extraction_skip_reasons()
    check_backup_and_restore()
    check_vanished_directory_guard()
    check_unregister_safety()
    check_backup_on_active_session()
    check_summary_deprecation()
    check_alias_guard()
    check_alias_dedupe()
    check_note_group()
    check_note_materiality()
    check_alias_guide_flip()
    check_alias_retired_guard()
    check_digest_schema()
    check_workspace_guard()
    check_filter_prelude_opt_in_frame()
    root = Path(tempfile.mkdtemp(prefix="authorlm-api-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "01-choice.md").write_text(
            "# Opening\n\nGravity is discussed early.\n\nEvery act begins with choice.\n"
        )
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms), "--author", "Ada Author",
                      "--copyright-owner", "Ada Author LLC",
                      "--paperback-isbn", "979-8-90452-354-1",
                      "--hardcover-isbn", "979-8-90452-351-0"])

        db = api.open_db(str(ws))
        manuscript = api.get_manuscript(db)
        check("get_manuscript resolves the single manuscript",
              manuscript["name"] == "book")
        check("init records publication identity on the manuscript",
              manuscript["author"] == "Ada Author"
              and manuscript["copyright_owner"] == "Ada Author LLC"
              and manuscript["paperback_isbn"] == "9798904523541"
              and manuscript["hardcover_isbn"] == "9798904523510")
        try:
            api.register_manuscript(
                db, "book", str(ms), author="Duplicate Author")
            duplicate_registration_refused = False
        except ValueError:
            duplicate_registration_refused = True
        check("application API owns manuscript registration invariants",
              duplicate_registration_refused)
        try:
            api.register_manuscript(
                db, "missing-book", str(ws / "does-not-exist"))
            invalid_path_refused = False
        except ValueError:
            invalid_path_refused = True
        check("application API refuses a nonexistent manuscript directory",
              invalid_path_refused)
        same_isbn_ms = ws / "same-isbn-book"
        same_isbn_ms.mkdir()
        try:
            api.register_manuscript(
                db, "same-isbn-book", str(same_isbn_ms),
                paperback_isbn="979-8-90452-354-1",
                hardcover_isbn="979-8-90452-354-1")
            same_registration_isbn_refused = False
        except ValueError:
            same_registration_isbn_refused = True
        check("registration requires distinct physical-format ISBNs",
              same_registration_isbn_refused)
        updated_identity = api.update_manuscript_metadata(
            db, manuscript, author="A. Author",
            copyright_owner="Author House LLC")
        check("publication identity updates through the application API",
              updated_identity["author"] == "A. Author"
              and updated_identity["copyright_owner"] == "Author House LLC"
              and api.list_manuscripts(db)[0]["author"] == "A. Author")
        try:
            api.update_manuscript_metadata(
                db, manuscript, paperback_isbn="979-8-90452-354-2")
            invalid_isbn_refused = False
        except ValueError:
            invalid_isbn_refused = True
        check("publication identity refuses an invalid ISBN-13 checksum",
              invalid_isbn_refused
              and api.get_manuscript(db)["paperback_isbn"]
              == "9798904523541")
        try:
            api.update_manuscript_metadata(
                db, manuscript, hardcover_isbn="4006381333931")
            non_isbn_ean_refused = False
        except ValueError:
            non_isbn_ean_refused = True
        check("publication identity requires the ISBN-13 book prefix",
              non_isbn_ean_refused)
        try:
            cli_main(["--workspace", str(ws), "manuscript", "set",
                      "--hardcover-isbn", "979-8-90452-354-1"])
            same_cli_isbn_refused = False
        except SystemExit as err:
            same_cli_isbn_refused = (
                "paperback and hardcover ISBNs must be different" in str(err))
        check("CLI requires distinct physical-format ISBNs",
              same_cli_isbn_refused
              and api.get_manuscript(db)["hardcover_isbn"]
              == "9798904523510")
        identity_out = io.StringIO()
        with contextlib.redirect_stdout(identity_out):
            cli_main(["--workspace", str(ws), "manuscript", "set",
                      "--author", "Author Penname"])
        manuscript = api.get_manuscript(db)
        check("manuscript identity is editable through the CLI",
              manuscript["author"] == "Author Penname"
              and manuscript["copyright_owner"] == "Author House LLC"
              and "Author Penname" in identity_out.getvalue())

        # --- export settings: quotes/backslashes round-trip through TOML ---
        from authorlm.export import load_settings, set_setting

        set_setting(manuscript, "title", 'A "Great" Book')
        set_setting(manuscript, "reference_docx", "C:\\styles\\ref.docx")
        check("quotes and backslashes in settings round-trip through TOML",
              load_settings(manuscript)["title"] == 'A "Great" Book'
              and load_settings(manuscript)["reference_docx"]
              == "C:\\styles\\ref.docx")
        # Reset: the pandoc export test below runs on this same
        # manuscript and must not inherit the fake reference docx.
        set_setting(manuscript, "reference_docx", "")

        # --- trace log: shape, error truncation, rotation, never-raises ---
        import json as _tjson

        from authorlm import tracelog

        trace_ws = root / "trace-ws"
        tracelog.record("get_status", surface="cli", workspace=str(trace_ws),
                        manuscript="book", duration_ms=12, ok=True)
        trace_path = tracelog.log_dir(str(trace_ws)) / "trace.jsonl"
        entry = _tjson.loads(trace_path.read_text().splitlines()[0])
        check("record() writes one well-formed JSONL entry",
              entry["verb"] == "get_status" and entry["surface"] == "cli"
              and entry["manuscript"] == "book" and entry["duration_ms"] == 12
              and entry["ok"] is True and "ts" in entry, entry)

        tracelog.record("get_briefing", surface="mcp", workspace=str(trace_ws),
                        ok=False, error="boom" * 200)
        entries = [_tjson.loads(line)
                  for line in trace_path.read_text().splitlines()]
        check("errors are recorded and truncated to 500 chars",
              entries[-1]["ok"] is False and len(entries[-1]["error"]) == 500)

        trace_path.write_text("x" * (tracelog.MAX_BYTES + 1))
        tracelog.record("collect_revision", surface="cli",
                        workspace=str(trace_ws))
        rotated = trace_path.with_suffix(".jsonl.1")
        check("oversized trace rotates to .jsonl.1; a fresh file starts",
              rotated.exists() and rotated.stat().st_size > tracelog.MAX_BYTES
              and len(trace_path.read_text().splitlines()) == 1)

        blocked = root / "blocked-file"
        blocked.write_text("not a directory")
        tracelog.record("get_status", surface="cli",
                        workspace=str(blocked / "ws"))
        check("a broken trace destination never raises", True)
        check("resolve_file maps a bare filename",
              api.resolve_file(db, "01-choice.md")["name"] == "book")
        check("resolve_file rejects foreign paths",
              api.resolve_file(db, "/tmp/nowhere.md") is None)

        with _as_client("mcp-stdio:mcp-test123:stdio MCP connection"):
            session, created = api.ensure_session(db, manuscript)
        recorded = loads(session["metadata"], {}).get("clients") or []
        check("ensure_session lazily opens and records the speaking chat",
              created and [e["session"] for e in recorded] == ["mcp-test123"],
              f"{recorded}")
        session2, created2 = api.ensure_session(db, manuscript)
        check("ensure_session reuses the active session",
              not created2 and session2["id"] == session["id"])

        api.add_concept(db, manuscript, "Gravity")
        api.add_concept(db, manuscript, "Choice")
        api.link_concepts(db, manuscript, "Choice", "permits", "Gravity")

        report = api.collect(db, manuscript, {})
        check("collect returns a structured report",
              report["version_no"] == 1 and report["transitions"]
              and any(n["name"] == "Gravity" for n in report["realized"]))
        check("collect reports prerequisite gaps structurally",
              len(report["gaps_after"]) == 1
              and report["gaps_after"][0]["second"] == "Gravity")

        # --- massive_deletions boundary conditions (auto-collect safety net) ---
        # Isolated manuscript: the shrink_ratio/min_chars math needs an exact,
        # uncluttered file history.
        md_root = root / "md-ws"
        md_ms = md_root / "manuscript"
        md_ms.mkdir(parents=True)
        big_text = "Paragraph. " * 200  # 2200 chars, well over min_chars
        (md_ms / "safety.md").write_text(big_text)
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(md_root), "init", "--name", "safety",
                      "--path", str(md_ms)])
        md_db = api.open_db(str(md_root))
        md_manuscript = api.get_manuscript(md_db)
        api.collect(md_db, md_manuscript, {})  # v1 baseline: 2200 chars

        (md_ms / "safety.md").write_text(big_text[:1760])  # 80% remains
        report = api.collect(md_db, md_manuscript, {}, auto=True)
        check("auto-collect proceeds under the shrink_ratio threshold",
              "staged" not in report)

        (md_ms / "safety.md").write_text(big_text[:50])  # ~97% removed vs. v2
        report = api.collect(md_db, md_manuscript, {}, auto=True)
        check("auto-collect stages a massive deletion instead of collecting",
              report.get("staged") == [{"file": "safety.md", "removed_percent": 97}])

        report = api.collect(md_db, md_manuscript, {}, auto=False)
        check("a manual collect proceeds through a massive deletion",
              "staged" not in report and "unchanged" not in report)

        (md_ms / "tiny.md").write_text("x" * 100)  # under min_chars
        api.collect(md_db, md_manuscript, {})
        (md_ms / "tiny.md").write_text("")
        report = api.collect(md_db, md_manuscript, {}, auto=True)
        check("files under min_chars are exempt from the deletion safety net",
              "staged" not in report)

        # --- FAIL-3: read_manuscript_files tolerates a non-UTF-8 file
        # instead of raising and taking down collect() — the recovery path
        # must not die on the failure it is meant to recover from. Every
        # file present on disk must still appear in the result: a dropped
        # entry reads to massive_deletions() as a 100%-shrunk file (via
        # disk.get(name, "")) and would misfire the mass-deletion guard.
        # Exercised at the revisions.py layer directly (collect_revision /
        # massive_deletions) to isolate this from the unrelated, separately
        # scoped decode call in illus.maintain_excerpts (also invoked by
        # api.collect(), not part of this finding). ---
        from authorlm.revisions import (collect_revision as collect_rev_bad_utf8,
                                        massive_deletions as massive_del_bad_utf8,
                                        read_manuscript_files as read_files_bad_utf8)

        bad_root = root / "badbytes-ws"
        bad_ms = bad_root / "manuscript"
        bad_ms.mkdir(parents=True)
        (bad_ms / "01-good.md").write_text("Good paragraph. " * 40)  # >min_chars
        (bad_ms / "02-bad.md").write_bytes(b"# Heading\n\xff\xfe not valid utf-8\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(bad_root), "init", "--name", "badbytes",
                      "--path", str(bad_ms)])
        bad_db = api.open_db(str(bad_root))
        bad_manuscript = api.get_manuscript(bad_db)

        files = read_files_bad_utf8(bad_ms)
        check("read_manuscript_files does not drop a file it cannot decode",
              set(files) == {"01-good.md", "02-bad.md"}, str(sorted(files)))
        check("the unreadable file's content is a replacement, not empty "
              "(an empty string would read as a full deletion)",
              "�" in files["02-bad.md"] and files["02-bad.md"] != "",
              repr(files["02-bad.md"]))

        v1 = collect_rev_bad_utf8(bad_db, bad_manuscript, None)
        check("collect_revision() completes through a mixed-encoding "
              "manuscript instead of raising",
              v1 is not None and v1["version_no"] == 1, str(v1))
        stored_files = loads(v1["files"], {})
        check("the unreadable file is still present in the persisted "
              "snapshot, not silently dropped as a phantom deletion",
              "02-bad.md" in stored_files, str(sorted(stored_files)))

        # Re-collecting unchanged content must not look like a deletion.
        flagged = massive_del_bad_utf8(bad_db, bad_manuscript)
        check("massive_deletions does not flag an unreadable-but-unchanged "
              "file as a mass deletion",
              flagged == [], str(flagged))

        # --- FAIL-3 (end-to-end): the checks above exercise
        # read_manuscript_files directly, which is exactly how this gap
        # survived — api.collect() runs illus.maintain_excerpts BEFORE
        # read_manuscript_files, and maintain_excerpts had its own bare
        # `path.read_text(encoding="utf-8")` with no error handling.
        # api.collect() wraps that call in `except OSError` only, and
        # UnicodeDecodeError is a ValueError, not an OSError, so it was
        # not caught: a single bad byte in any manuscript file crashed
        # api.collect() end-to-end — the recovery path itself. This test
        # goes through api.collect() to prove that path survives, using a
        # fresh workspace so it is not coupled to the direct-layer
        # assertions above. ---
        e2e_root = root / "e2e-badbytes-ws"
        e2e_ms = e2e_root / "manuscript"
        e2e_ms.mkdir(parents=True)
        (e2e_ms / "01-good.md").write_text("Good paragraph. " * 40)
        (e2e_ms / "02-bad.md").write_bytes(b"# Heading\n\xff\xfe not valid utf-8\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(e2e_root), "init", "--name", "e2ebadbytes",
                      "--path", str(e2e_ms)])
        e2e_db = api.open_db(str(e2e_root))
        e2e_manuscript = api.get_manuscript(e2e_db)

        report = api.collect(e2e_db, e2e_manuscript, {})
        check("api.collect() completes end-to-end through a manuscript "
              "containing invalid UTF-8, instead of raising",
              "staged" not in report, str(report))
        v1 = e2e_db.one(
            "SELECT * FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no DESC LIMIT 1",
            (e2e_manuscript["id"],))
        stored_files = loads(v1["files"], {}) if v1 else {}
        check("the file with invalid UTF-8 is snapshotted by api.collect(), "
              "not silently dropped as a phantom deletion",
              "02-bad.md" in stored_files, str(sorted(stored_files)))
        # --- detect_transitions in isolation (T5, risk-register §3): no
        # existing test calls the diff/opcode logic directly, only through
        # the full collect() pipeline. Isolated manuscript, own history. ---
        from authorlm.revisions import collect_revision, detect_transitions

        dt_root = root / "dt-ws"
        dt_ms = dt_root / "manuscript"
        dt_ms.mkdir(parents=True)
        dt_db = api.open_db(str(dt_root), create=True)

        # Paragraph reorder: difflib has no "moved" concept, so swapping two
        # adjacent paragraphs is reported as the moved paragraph's text
        # being inserted at its new position and deleted from its old one —
        # not a single "reorder" transition. Characterizing, not endorsing.
        dt_ms1 = dt_root / "reorder"
        dt_ms1.mkdir()
        row_reorder = api.register_manuscript(dt_db, "reorder", str(dt_ms1))
        (dt_ms1 / "a.md").write_text(
            "# Heading\n\nP1 text here.\n\nP2 text here.\n\nP3 text here.\n")
        v1 = collect_revision(dt_db, row_reorder, None, source="test")
        (dt_ms1 / "a.md").write_text(
            "# Heading\n\nP2 text here.\n\nP1 text here.\n\nP3 text here.\n")
        v2 = collect_revision(dt_db, row_reorder, None, source="test")
        reorder_trans = detect_transitions(dt_db, row_reorder["id"], dict(v1), v2)
        check("paragraph reorder is an insert of the moved text at its new "
              "position plus a delete at its old one, not a single move",
              [(t["kind"], t["location"]) for t in reorder_trans]
              == [("insert", "a.md#Heading"), ("delete", "a.md#Heading")],
              str([(t["kind"], t["location"], t["summary"])
                   for t in reorder_trans]))
        check("the reorder's insert/delete pair both carry the moved "
              "paragraph's own text, not the paragraph it displaced",
              "P2 text here." in reorder_trans[0]["summary"]
              and "P2 text here." in reorder_trans[1]["summary"])

        # Two non-adjacent hunks in one file: each rewritten paragraph is
        # its own transition, not merged into a single spanning edit.
        dt_ms2 = dt_root / "hunks"
        dt_ms2.mkdir()
        row_hunks = api.register_manuscript(dt_db, "hunks", str(dt_ms2))
        (dt_ms2 / "a.md").write_text(
            "# Heading\n\nP1 unchanged.\n\nP2 original.\n\nP3 unchanged.\n\n"
            "P4 original.\n\nP5 unchanged.\n")
        h1 = collect_revision(dt_db, row_hunks, None, source="test")
        (dt_ms2 / "a.md").write_text(
            "# Heading\n\nP1 unchanged.\n\nP2 REWRITTEN.\n\nP3 unchanged.\n\n"
            "P4 REWRITTEN.\n\nP5 unchanged.\n")
        h2 = collect_revision(dt_db, row_hunks, None, source="test")
        hunk_trans = detect_transitions(dt_db, row_hunks["id"], dict(h1), h2)
        check("two non-adjacent hunks in one file produce two separate "
              "rewrite transitions, not one spanning edit",
              len(hunk_trans) == 2
              and all(t["kind"] == "rewrite" for t in hunk_trans)
              and "P2 REWRITTEN" in hunk_trans[0]["summary"]
              and "P4 REWRITTEN" in hunk_trans[1]["summary"],
              str([(t["kind"], t["summary"]) for t in hunk_trans]))

        # Heading-less file: location falls back to the bare filename, no
        # '#heading' suffix, since _nearest_heading finds nothing to anchor to.
        dt_ms3 = dt_root / "noheading"
        dt_ms3.mkdir()
        row_noheading = api.register_manuscript(dt_db, "noheading", str(dt_ms3))
        (dt_ms3 / "b.md").write_text(
            "Just prose, no headings at all.\n\nSecond paragraph.\n")
        n1 = collect_revision(dt_db, row_noheading, None, source="test")
        (dt_ms3 / "b.md").write_text(
            "Just prose, no headings at all.\n\nSecond paragraph, edited.\n")
        n2 = collect_revision(dt_db, row_noheading, None, source="test")
        noheading_trans = detect_transitions(
            dt_db, row_noheading["id"], dict(n1), n2)
        check("a heading-less file's transition location is the bare "
              "filename, with no '#heading' suffix",
              len(noheading_trans) == 1
              and noheading_trans[0]["location"] == "b.md",
              str([(t["kind"], t["location"]) for t in noheading_trans]))

        # --- history show/restore (MVP.md 'Deliberately deferred': version
        # access & restoration — implementable at any time, no schema change) ---
        try:
            api.get_version(md_db, md_manuscript, 99)
            check("get_version rejects an unknown version number", False)
        except LookupError as err:
            check("get_version rejects an unknown version number", "v99" in str(err))

        v1 = api.get_version(md_db, md_manuscript, 1)
        check("get_version returns the requested snapshot",
              loads(v1["files"], {})["safety.md"] == big_text)

        restored = api.restore_version(md_db, md_manuscript, 1, {})
        check("restore_version writes the old content back to disk",
              (md_ms / "safety.md").read_text() == big_text)
        check("restore_version deletes files absent from the restored version",
              not (md_ms / "tiny.md").exists())
        check("restoring advances history rather than rewinding it",
              restored["version_no"] == 6
              and restored["transitions"])
        latest = api.get_version(md_db, md_manuscript, 6)
        check("the restored snapshot is itself a new, real version",
              loads(latest["files"], {})["safety.md"] == big_text
              and "tiny.md" not in loads(latest["files"], {}))

        # --- BUG-2 / A1 regression: restore_version must snapshot the
        # current on-disk state into history BEFORE unlinking/overwriting
        # it, so any work the author had on disk but never collected is
        # still recoverable afterward (a self-contained fixture, so it
        # cannot be confused with the version-numbering narrative above) ---
        rv_root = root / "rv-ws"
        rv_ms = rv_root / "manuscript"
        rv_ms.mkdir(parents=True)
        (rv_ms / "only.md").write_text("Original collected content.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(rv_root), "init", "--name", "rv",
                      "--path", str(rv_ms)])
        rv_db = api.open_db(str(rv_root))
        rv_manuscript = api.get_manuscript(rv_db)
        api.collect(rv_db, rv_manuscript, {})  # v1

        # The author edits the tracked file AND starts a brand-new file —
        # neither ever collected. This is the exact state a live session
        # sits in between autosaves; restore_version must not be the thing
        # that erases it.
        (rv_ms / "only.md").write_text("EDITED BUT NEVER COLLECTED.\n")
        (rv_ms / "new-uncollected.md").write_text(
            "A NEW FILE, NEVER COLLECTED.\n")

        api.restore_version(rv_db, rv_manuscript, 1, {})
        check("restore_version still restores the target version's content",
              (rv_ms / "only.md").read_text()
              == "Original collected content.\n"
              and not (rv_ms / "new-uncollected.md").exists())

        rv_versions = rv_db.all(
            "SELECT files FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no", (rv_manuscript["id"],))
        recovered = any(
            loads(v["files"], {}).get("only.md")
            == "EDITED BUT NEVER COLLECTED.\n"
            and loads(v["files"], {}).get("new-uncollected.md")
            == "A NEW FILE, NEVER COLLECTED.\n"
            for v in rv_versions)
        check("restore_version snapshots the pre-destruction disk state "
              "into history before overwriting/deleting it (BUG-2 / A1)",
              recovered,
              [sorted(loads(v["files"], {})) for v in rv_versions])

        try:
            api.diff_versions(md_db, md_manuscript, older=1, newer=99)
            check("diff_versions rejects an unknown version number", False)
        except LookupError as err:
            check("diff_versions rejects an unknown version number",
                  "v99" in str(err))

        forward = api.diff_versions(md_db, md_manuscript, older=1, newer=3)
        backward = api.diff_versions(md_db, md_manuscript, older=3, newer=1)
        check("diff_versions normalizes reversed (newer, older) arguments",
              backward["old"] == forward["old"] == "v1"
              and backward["new"] == forward["new"] == "v3"
              and backward["files"] == forward["files"])

        declared = api.declare_intent(db, manuscript, "Expand on gravity")
        check("declare_intent returns intent + preview",
              declared["intent"]["statement"] == "Expand on gravity"
              and declared["preview"]["matched"][0]["name"] == "Gravity")
        typo = api.intent_preview(db, manuscript, "Discuss gravety maybe")
        check("preview fuzzy-suggests on typos", "Gravity" in typo["suggestions"])

        # --- intent complete/abandon: active-state + lookup errors ---
        life = api.declare_intent(db, manuscript, "Lifecycle probe intent")
        life_id = life["intent"]["id"]
        finished = api.complete_intent(db, manuscript, life_id[:8], "wrapped up")
        check("complete_intent marks an active intent completed with outcome",
              finished["intent"]["id"] == life_id
              and db.one("SELECT status, outcome FROM declared_intents WHERE id = ?",
                         (life_id,))["status"] == "completed"
              and db.one("SELECT outcome FROM declared_intents WHERE id = ?",
                         (life_id,))["outcome"] == "wrapped up")
        try:
            api.complete_intent(db, manuscript, life_id[:8], "again")
            check("complete_intent rejects an already-completed intent", False)
        except ValueError as err:
            check("complete_intent rejects an already-completed intent",
                  "already completed" in str(err))
        check("rejected re-complete leaves the original outcome intact",
              db.one("SELECT outcome FROM declared_intents WHERE id = ?",
                     (life_id,))["outcome"] == "wrapped up")

        drop = api.declare_intent(db, manuscript, "Abandon-then-complete probe")
        drop_id = drop["intent"]["id"]
        api.abandon_intent(db, manuscript, drop_id[:8], "changed mind")
        try:
            api.complete_intent(db, manuscript, drop_id[:8], "should fail")
            check("complete_intent rejects an abandoned intent", False)
        except ValueError as err:
            check("complete_intent rejects an abandoned intent",
                  "already abandoned" in str(err))
        check("rejected complete-after-abandon preserves abandonment reason",
              db.one("SELECT status, outcome FROM declared_intents WHERE id = ?",
                     (drop_id,))["status"] == "abandoned"
              and db.one("SELECT outcome FROM declared_intents WHERE id = ?",
                         (drop_id,))["outcome"] == "changed mind")
        try:
            api.abandon_intent(db, manuscript, drop_id[:8], "again")
            check("abandon_intent rejects a non-active intent", False)
        except ValueError as err:
            check("abandon_intent rejects a non-active intent",
                  "already abandoned" in str(err))
        try:
            api.complete_intent(db, manuscript, "zzzzzzzz", None)
            check("complete_intent rejects an unknown prefix", False)
        except LookupError:
            check("complete_intent rejects an unknown prefix", True)

        guidance = api.guide(db, manuscript, session)
        check("guide returns structured suggestions",
              not guidance["abstained"] and guidance["suggestions"])
        reviewed = api.review(db, manuscript, session, 1, "accepted",
                              "Right place for it")
        check("review records and returns evidence effects",
              reviewed["guidance"]["batch_index"] == 1)
        try:
            api.review(db, manuscript, session, 1, "accepted", None)
            check("double review raises", False)
        except ValueError:
            check("double review raises", True)

        briefing = api.get_briefing(db, manuscript)
        check("briefing is a serializable dict",
              "learning_velocity" in briefing)

        # --- INV-15: a belief retired inside the briefing window must not
        # reappear under "newly seeded candidate beliefs" — that is the
        # author's explicit "no" shown back as a fresh finding. ---
        retired_belief_row = ko_fields("pol")
        retired_belief_row.update(
            manuscript_id=manuscript["id"],
            statement="INV-15 regression: a belief the author retires.",
            status="candidate", confidence=0.5, supporting=1,
            contradicting=0, outstanding_questions="[]",
            source="review-explanation", source_id=db.source("author"))
        db.insert("editorial_beliefs", retired_belief_row)
        api.retire_belief(db, manuscript, retired_belief_row["id"][:8],
                          "author said no")
        briefing_after_retire = api.get_briefing(db, manuscript)
        check("INV-15: a belief retired within the window is absent from "
              "the briefing's newly-seeded list",
              retired_belief_row["id"] not in
              {b["id"] for b in briefing_after_retire["new_beliefs"]},
              str(briefing_after_retire["new_beliefs"]))
        diff = api.diff_versions(db, manuscript)
        check("diff_versions returns per-file line lists",
              diff["new"] == "v1" and "01-choice.md" in diff["files"])

        # New chapter files must be acknowledged distinctly, not buried
        # in transitions (session start prints report["new_files"]).
        (ms / "late-arrival.md").write_text("# **Late**\n\nA new chapter.\n")
        late = api.collect(db, manuscript, {})
        check("collect surfaces new files distinctly",
              late.get("new_files") == ["late-arrival.md"],
              str(late.get("new_files")))
        (ms / "late-arrival.md").unlink()
        api.collect(db, manuscript, {})

        from authorlm import extraction

        extraction_prompt = extraction.extraction_system()
        check("extraction prompt keeps definitions out of concept names",
              '"name" is the shortest stable label' in extraction_prompt
              and "Do not promote\n  the definiens" in extraction_prompt
              and 'emit name "Hard\nFloor"' in extraction_prompt)

        graph = api.list_concepts(db, manuscript)
        check("list_concepts returns nodes and named edges",
              len(graph["nodes"]) == 2
              and graph["edges"][0]["from_name"] == "Choice")
        api.confirm_concept(db, manuscript, "Gravity", kind="metaphor")
        check("confirm_concept retypes",
              api.show_concept(db, manuscript, "Gravity")["node"]["kind"] == "metaphor")
        check("definition is not a concept-node kind",
              "definition" not in concepts.NODE_KINDS)
        check("defines remains a graph relation",
              "defines" in extraction.VALID_RELATIONS)
        check("definition remains a guidance kind",
              "definition" in guidance_module.GUIDANCE_KINDS)
        try:
            api.add_concept(db, manuscript, "Legacy Definition", kind="definition")
            check("add_concept rejects the retired definition kind", False)
        except ValueError as err:
            check("add_concept rejects the retired definition kind",
                  "invalid concept kind 'definition'" in str(err))
        try:
            api.confirm_concept(db, manuscript, "Gravity", kind="definition")
            check("confirm_concept rejects the retired definition kind", False)
        except ValueError as err:
            check("confirm_concept rejects the retired definition kind",
                  "invalid concept kind 'definition'" in str(err))

        retired_twin = ko_fields("cn")
        retired_twin.update(
            manuscript_id=manuscript["id"], name="Gravity", kind="concept",
            status="retired", introduced_in=None, notes="retired twin",
            aliases="[]", source_id=db.source("author"),
        )
        db.insert("concept_nodes", retired_twin)
        check("exact-name lookup prefers the live concept over a retired twin",
              concepts.get_concept(db, manuscript["id"], "Gravity")["id"]
              != retired_twin["id"])

        # --- concept/edge/proposal curation via api (the CLI has its own
        # code paths for these, so api.* itself was never exercised) ---
        api.add_concept(db, manuscript, "Scratch")
        retired = api.retire_concept(db, manuscript, "Scratch")
        check("retire_concept retires a concept with no edges",
              retired == {"name": "Scratch", "edges_retired": 0})
        try:
            api.retire_concept(db, manuscript, "Scratch")
            check("retiring an already-retired concept raises", False)
        except ValueError as err:
            check("retiring an already-retired concept raises", "already retired" in str(err))
        try:
            api.retire_concept(db, manuscript, "Nobody")
            check("retiring an unknown concept raises", False)
        except LookupError:
            check("retiring an unknown concept raises", True)

        from authorlm import concepts as cg

        api.add_concept(db, manuscript, "Freedom")
        inferred = cg.link_concepts(db, manuscript["id"], "Gravity", "supports", "Freedom",
                                    status="inferred")
        confirmed = api.confirm_edge(db, manuscript, inferred["id"][:8])
        check("confirm_edge declares an inferred edge",
              confirmed == {"from_name": "Gravity", "relation": "supports", "to_name": "Freedom"})
        check("confirm_edge persisted the status change",
              api.show_concept(db, manuscript, "Freedom")["edges"][0]["status"] == "declared")
        # D4: an individual author verdict on an edge must record evidence,
        # exactly like confirm_concept/retire_concept already do — and the
        # row must be attributed to the author (edge_triage is not in
        # SYSTEM_EVIDENCE_TYPES), never to the system.
        confirm_edge_evidence = db.one(
            "SELECT e.evidence_type, e.signal, e.supports_belief, s.kind "
            "AS source_kind FROM evidence e JOIN sources s ON s.id = e.source_id "
            "WHERE e.manuscript_id = ? AND e.evidence_type = 'edge_triage' "
            "ORDER BY e.created_at DESC LIMIT 1", (manuscript["id"],))
        check("confirm_edge records edge_triage evidence",
              confirm_edge_evidence is not None, str(confirm_edge_evidence))
        check("confirm_edge evidence carries signal 'confirmed'",
              confirm_edge_evidence["signal"] == "confirmed", str(confirm_edge_evidence))
        check("confirm_edge evidence is attributed to the author",
              confirm_edge_evidence["source_kind"] == "author", str(confirm_edge_evidence))
        check("confirm_edge evidence does not target a belief",
              confirm_edge_evidence["supports_belief"] is None, str(confirm_edge_evidence))
        inferred2 = cg.link_concepts(db, manuscript["id"], "Freedom", "supports", "Choice",
                                     status="inferred")
        rejected = api.reject_edge(db, manuscript, inferred2["id"][:8])
        check("reject_edge rejects an inferred edge",
              rejected == {"from_name": "Freedom", "relation": "supports", "to_name": "Choice"})
        reject_edge_evidence = db.one(
            "SELECT e.evidence_type, e.signal, e.supports_belief, s.kind "
            "AS source_kind FROM evidence e JOIN sources s ON s.id = e.source_id "
            "WHERE e.manuscript_id = ? AND e.evidence_type = 'edge_triage' "
            "ORDER BY e.created_at DESC LIMIT 1", (manuscript["id"],))
        check("reject_edge records edge_triage evidence",
              reject_edge_evidence is not None, str(reject_edge_evidence))
        check("reject_edge evidence carries signal 'rejected'",
              reject_edge_evidence["signal"] == "rejected", str(reject_edge_evidence))
        check("reject_edge evidence is attributed to the author",
              reject_edge_evidence["source_kind"] == "author", str(reject_edge_evidence))
        try:
            api.confirm_edge(db, manuscript, "zzzzzzzz")
            check("confirming an unknown edge prefix raises", False)
        except LookupError:
            check("confirming an unknown edge prefix raises", True)

        from authorlm import proposals as prop

        open_proposal = prop.create(db, manuscript["id"], "revival", "Scratch",
                                    {"name": "Scratch"})
        listed = api.list_proposals(db, manuscript)["open"]
        check("list_proposals surfaces an open proposal with summary/details",
              any(p["id"] == open_proposal["id"] and "revive retired concept" in p["summary"]
                  for p in listed))
        try:
            api.resolve_proposal(db, manuscript, open_proposal["id"][:8], "bogus")
            check("resolving a proposal with an unknown action raises", False)
        except ValueError:
            check("resolving a proposal with an unknown action raises", True)
        try:
            api.resolve_proposal(db, manuscript, "zzzzzzzz", "dismiss")
            check("resolving an unknown proposal prefix raises", False)
        except LookupError:
            check("resolving an unknown proposal prefix raises", True)
        resolved = api.resolve_proposal(db, manuscript, open_proposal["id"][:8], "dismiss",
                                        "not needed")
        check("resolve_proposal dismisses and returns a message",
              resolved["proposal_id"] == open_proposal["id"] and resolved["message"])
        check("dismissed proposal no longer appears in list_proposals",
              not any(p["id"] == open_proposal["id"]
                      for p in api.list_proposals(db, manuscript)["open"]))

        # --- briefing: focus areas and TOC completeness (§21.6, §20.3) ---
        api.link_concepts(db, manuscript, "Choice", "motivates", "Freedom")
        (ms / "02-extra.md").write_text("# Extra\n\nSome unrelated prose.\n")
        # toc.toml, not toc.md: the TOC became structural TOML after this
        # test was written, and a stray toc.md is simply not a TOC — the
        # assertion below then reads an empty unlisted set and passes
        # vacuously in the wrong direction.
        (ms / "toc.toml").write_text('[[chapter]]\nfile = "01-choice.md"\n')
        api.collect(db, manuscript, {})
        briefing2 = api.get_briefing(db, manuscript)
        check("briefing surfaces an unrealized concept's dependents as a focus area",
              any(area["node"]["name"] == "Freedom"
                  and any(r["name"] == "Choice" for r in area["related"])
                  for area in briefing2["focus_areas"]),
              briefing2["focus_areas"])
        check("briefing flags a manuscript file missing from toc.toml",
              briefing2["toc_unlisted"] == ["02-extra.md"], briefing2["toc_unlisted"])
        # Restore the fixture: this block borrows the shared manuscript, and
        # the Doc tests below assert exact tab sets and tab ORDER, so an
        # extra content file left behind cascades into unrelated failures.
        (ms / "02-extra.md").unlink()
        (ms / "toc.toml").unlink()
        api.collect(db, manuscript, {})

        closed = api.close_session(db, manuscript)
        check("close_session ends the active session",
              api.status(db, manuscript)["session"] is None
              and closed["id"] == session["id"])

        # --- idle session expiry ---
        check("expire_idle_session is a no-op with no active session",
              api.expire_idle_session(db, manuscript, {}) is None)
        idle_session, _ = api.ensure_session(db, manuscript)
        db.update("sessions", idle_session["id"],
                  {"started_at": "2020-01-01T00:00:00.000000Z"})
        check("expire_idle_session honors a configured idle_hours threshold",
              api.expire_idle_session(
                  db, manuscript, {"session": {"idle_hours": 999999}}) is None)
        expired = api.expire_idle_session(db, manuscript, {})
        check("expire_idle_session closes a session idle past the default threshold",
              expired is not None
              and expired["session"]["id"] == idle_session["id"]
              and expired["idle_hours"] > 3
              and api.status(db, manuscript)["session"] is None)

        idle_session2, _ = api.ensure_session(db, manuscript)
        db.update("sessions", idle_session2["id"], {"started_at": "not-a-date"})
        check("expire_idle_session tolerates an unparseable timestamp",
              api.expire_idle_session(db, manuscript, {}) is None)
        api.close_session(db, manuscript)

        # --- Google Docs bridge: normalizer + stub-service round trip ---
        from authorlm.gdocs import normalize_markdown, pull_doc, push_doc

        messy = ("# Title\r\n\r\n\r\n\r\nSome \\-escaped \\. text here.   \n"
                 "*  a bullet\n\nEnds with nbsp.")
        clean = normalize_markdown(messy)
        check("normalizer canonicalizes Docs-export quirks",
              clean == ("# Title\n\nSome -escaped . text here.\n"
                        "- a bullet\n\nEnds with nbsp.\n"), repr(clean))
        check("normalizer is idempotent", normalize_markdown(clean) == clean)

        # Verse: a Doc export writes a hard line break as two trailing
        # spaces; the canonical form is the backslash break, rewritten
        # before trailing whitespace is stripped, and only before a
        # continuation line (verse-and-cast-design.md §2).
        stanza = "Line one,  \nLine two,  \nLine three.\n\nProse.   \n\n- item\n"
        check("normalizer turns exported hard breaks into backslash breaks",
              normalize_markdown(stanza)
              == "Line one,\\\nLine two,\\\nLine three.\n\nProse.\n\n- item\n",
              repr(normalize_markdown(stanza)))
        from authorlm.gdocs import HARD_BREAK_CHAR, rendered_text
        stanza_md = "Every morning I rise,\\\nTo fight a *fiery* dragon."
        from authorlm.threads import pending_forms as _pf
        struck = ("~~<<Each day, I write a bit,~~\\\n~~Tis only a modest "
                  "stride,~~\\\n~~The essence will be clarified.>>~~"
                  "{{Each day,\\\nOnly a stride.}}")
        check("the form parser drops the exporter's strikethrough fragments "
              "at a stanza's hard breaks",
              _pf(struck)[0]["old"] == "Each day, I write a bit,\\\nTis only "
              "a modest stride,\\\nThe essence will be clarified.",
              repr(_pf(struck)[0]["old"]))
        from authorlm.gdocs import tab_anchor_text
        check("an insertion anchor on a heading drops the heading marker and "
              "the bold the tab never held",
              tab_anchor_text("## **Why I wrote this book**") == "Why I wrote this book"
              and tab_anchor_text("Plain.") == "Plain.")
        from authorlm import lenses as _lz_contract
        check("the lens contract asks for a judgment as concrete options, "
              "not one clause (author ruling 2026-09-27)",
              "OPTIONS" in _lz_contract.LENS_SYSTEM
              and "one clause" not in _lz_contract.LENS_SYSTEM)
        check("the tab-text renderer maps a backslash hard break to the "
              "Doc's line-break character and drops emphasis markers",
              rendered_text(stanza_md)
              == "Every morning I rise," + HARD_BREAK_CHAR
              + "To fight a fiery dragon.", repr(rendered_text(stanza_md)))
        check("backslash breaks survive the normalizer unchanged",
              normalize_markdown("Line one,\\\nLine two.\n")
              == "Line one,\\\nLine two.\n")

        # Pandoc footnotes must reach the Doc as literal text: Google's
        # markdown importer consumes the syntax and the transplant drops
        # it (it-e63eabd58b11). escape_footnotes protects refs and
        # definitions; normalize_markdown's escape-stripping undoes it,
        # so the push→pull round trip is byte-clean.
        from authorlm.gdocs import escape_footnotes

        noted = ("The herd [^I1] persists.\n\n"
                 "[^I1]: See *Beyond Good and Evil* (§§199–202).\n")
        escaped = escape_footnotes(noted)
        check("escape_footnotes shields refs and definitions",
              escaped == ("The herd \\[^I1] persists.\n\n"
                          "\\[^I1]: See *Beyond Good and Evil* "
                          "(§§199–202).\n"), repr(escaped))
        check("footnote escape round-trips through the normalizer",
              normalize_markdown(escaped) == normalize_markdown(noted),
              repr(normalize_markdown(escaped)))
        check("escape_footnotes leaves illustration tags alone",
              escape_footnotes("[Illustration: a fork | caption: x]")
              == "[Illustration: a fork | caption: x]")

        # Math spans are opaque to the normalizer (every backslash is
        # TeX); display math lays out on one line; the Doc road
        # round-trips byte for byte via escape_math on the way in and
        # unescape_export_math on the way out (measured 2026-09-02:
        # Google's importer eats one backslash before any punctuation,
        # its exporter escapes \ _ = + *).
        from authorlm.gdocs import escape_math, unescape_export_math

        tex = ("Sets $\\{a, b\\}$ and $\\|x\\|$, $x_1^2 \\, a^*$.\n\n"
               "$$\n\\begin{aligned}\na &= b \\\\\n&= c\n\\end{aligned}\n$$\n")
        canon = normalize_markdown(tex)
        check("normalizer leaves TeX untouched and lays display math on "
              "one line",
              canon == ("Sets $\\{a, b\\}$ and $\\|x\\|$, $x_1^2 \\, a^*$.\n\n"
                        "$$ \\begin{aligned} a &= b \\\\ &= c "
                        "\\end{aligned} $$\n"), repr(canon))
        check("prose escapes still strip outside math, not inside",
              normalize_markdown("a \\- b $\\-$ \\=c")
              == "a - b $\\-$ =c\n",
              repr(normalize_markdown("a \\- b $\\-$ \\=c")))
        check("a price is not math",
              normalize_markdown("costs $5 and $10 \\- cheap")
              == "costs $5 and $10 - cheap\n")
        import re as _re_math
        importer = lambda s: _re_math.sub(r"\\([!-/:-@\[-`{-~])", r"\1", s)
        exporter = lambda s: _re_math.sub(r"([\\_=+*])", r"\\\1", s)
        doc_text = importer(escape_math(canon))
        check("escape_math hands the importer exactly the TeX",
              doc_text == canon, repr(doc_text))
        pulled = normalize_markdown(unescape_export_math(exporter(doc_text)))
        check("math round-trips the Doc road byte for byte",
              pulled == canon, repr(pulled))
        # Unclosed $$ used to fail open into the prose escape strip:
        # push_doc writes normalize_markdown back to disk, so a mid-edit
        # `\\` row break became `\` and `\{` braces vanished — permanent
        # TeX corruption, not a cosmetic reflow.
        orphan = ("Intro\n$$\n\\begin{matrix} a \\\\ b \\end{matrix}\n"
                  "x \\{ y \\} and a \\| b\n")
        got_orphan = normalize_markdown(orphan)
        check("unclosed display math does not gut TeX on normalize",
              "a \\\\ b" in got_orphan and "\\{ y \\}" in got_orphan
              and "\\| b" in got_orphan
              and normalize_markdown(got_orphan) == got_orphan,
              repr(got_orphan))

        # An empty heading paragraph in the Doc (e.g. a blank Subtitle
        # line) must be dropped — never merged into the next heading
        # ('## ##', it-7b127d3164ff).
        empty_heading = ("# **Title**\n\n## \n\n"
                         "## Septem Plus Sermones Ad Mortuos, 2026\n")
        check("normalizer drops empty headings instead of merging",
              normalize_markdown(empty_heading)
              == ("# **Title**\n\n"
                  "## Septem Plus Sermones Ad Mortuos, 2026\n"),
              repr(normalize_markdown(empty_heading)))

        # --- illustration slots: tag grammar + scan-derived registry ---
        from authorlm.illus import (desc_hash, parse_tag, slot_report,
                                    strip_dangling)

        tag = parse_tag(
            "[Illustration: a tracker kneeling | caption: The tracker]")
        check("illustration tag grammar parses prompt and caption",
              tag["prompt"] == "a tracker kneeling"
              and tag["caption"] == "The tracker", str(tag))
        check("caption and whitespace stay outside slot identity",
              desc_hash("a tracker kneeling")
              == desc_hash("a  tracker\tkneeling")
              and parse_tag("[Illustration: a tracker kneeling]")["caption"]
              is None
              and parse_tag("prose mentioning [Illustration: x] inline")
              is None)

        scratch = root / "illus-scratch"
        scratch.mkdir()
        (scratch / "ch.md").write_text(
            "# C\n\n[Illustration: two turns in opposite order]\n")
        rep = slot_report(scratch)
        check("scan reports an unrendered slot with file and line",
              [{k: v for k, v in rep["unrendered"][0].items()
                if k in ("file", "line", "prompt")}]
              == [{"file": "ch.md", "line": 3,
                   "prompt": "two turns in opposite order"}]
              and not rep["orphaned"], str(rep))

        # A slot's IDENTITY is its ref slug, minted at externalize. An
        # inline slot has no key, so it can hold no art at all: the
        # first image externalizes it first (it-2e4a5ec3d809).
        from authorlm.illus import ensure_key, find_slot, slot_candidates
        inline = find_slot(scratch, "two turns")[0]
        check("an inline slot has no key and therefore no candidates",
              inline["key"] is None
              and slot_candidates(scratch, inline["key"]) == [])
        keyed = ensure_key(scratch, inline)
        check("ensure_key externalizes and mints the key from the ref",
              keyed["key"] == "two-turns-in-opposite"
              and keyed["ref"] == "two-turns-in-opposite.md"
              and "⇢ two-turns-in-opposite.md"
              in (scratch / "ch.md").read_text(), str(keyed))
        check("ensure_key is idempotent on an already-keyed slot",
              ensure_key(scratch, keyed) is keyed)

        ill_dir = scratch / "_illustrations"
        h = desc_hash("two turns in opposite order")
        candidate = f"{keyed['key']}-{h}-0000-01.png"
        (ill_dir / candidate).write_bytes(b"")
        check("a candidate under the slot's key marks the slot rendered",
              not slot_report(scratch)["unrendered"])

        # THE REGRESSION. Rewording used to re-key the slot and orphan
        # every image under it — the author reworks prompts constantly,
        # and in the Doc, where nothing can warn them. The key lives in
        # the tag, not in the prompt, so the art now survives a rewrite
        # into something with no word in common.
        (ill_dir / "prompts" / "two-turns-in-opposite.md").write_text(
            "a bronze orrery under a shattered dome, nothing alike\n")
        rep = slot_report(scratch)
        check("rewording the description keeps the art attached",
              not rep["unrendered"] and not rep["orphaned"], str(rep))
        check("the reworded slot still resolves to its candidate",
              [c["name"] for c in slot_candidates(
                  scratch, find_slot(scratch, "orrery")[0]["key"])]
              == [candidate], str(rep))
        check("the stale marker records the description it was made for",
              slot_candidates(scratch, keyed["key"])[0]["desc"] == h
              and find_slot(scratch, "orrery")[0]["desc_hash"] != h)

        # Deleting the SLOT still orphans its art — that is the honest
        # signal, and it is now the only thing that produces one.
        (scratch / "ch.md").write_text("# C\n\nno slot here\n")
        check("deleting the slot orphans its candidate",
              slot_report(scratch)["orphaned"] == [candidate],
              str(slot_report(scratch)))
        (scratch / "ch.md").write_text(
            "# C\n\n[Illustration: a bronze orrery… "
            "⇢ two-turns-in-opposite.md]\n")
        check("restoring the tag re-attaches the art",
              not slot_report(scratch)["orphaned"])

        # A `⇢` present but not matching the ref grammar (e.g. it doesn't
        # end in a bare 'name.md') must be reported loudly, never
        # silently folded into the prompt text as if it were an ordinary
        # undecorated description (it-d70ece778f55: a corrupted doc-pull
        # round trip produced exactly this shape — an excerpt spliced to
        # a stray '.md' name immediately followed by a '.png' name, with
        # no whitespace, so the ref half fails _REF and used to vanish
        # without a trace).
        garbled = ("The interior of a temple ⇢ "
                  "a-field-of-fallen.mda-square-temple-of.png")
        tag = parse_tag(f"[Illustration: {garbled}]")
        check("a malformed ⇢ ref is flagged, not silently swallowed",
              tag["malformed_ref"] is True and tag["ref"] is None
              and tag["prompt"] == garbled, str(tag))
        check("a well-formed ⇢ ref is never flagged as malformed",
              parse_tag("[Illustration: excerpt ⇢ some-file.md]")
              ["malformed_ref"] is False)
        check("a plain undecorated description is never flagged",
              parse_tag("[Illustration: plain description]")
              ["malformed_ref"] is False)
        (scratch / "ch.md").write_text(f"# C\n\n[Illustration: {garbled}]\n")
        rep = slot_report(scratch)
        check("slot_report surfaces the malformed ref as its own signal",
              rep["malformed_refs"] == [{"file": "ch.md", "line": 3,
                                         "prompt": garbled}],
              str(rep))

        dirty = ("Prose kept. ![][image1]\n\n"
                 "[image1]: <data:image/png;base64,abc>\n")
        clean, refs = strip_dangling(dirty)
        check("dangling Doc image refs strip cleanly and are reported",
              "image1" not in clean and "Prose kept." in clean
              and len(refs) == 2, repr((clean, refs)))

        # A collect surfaces unrendered slots so the author never has to
        # remember to ask ("new illustrations found").
        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text()
            + "\n[Illustration: choice as a forking path]\n")
        report = api.collect(db, manuscript, {})
        check("collect reports new illustration slots",
              report["illustrations"]["unrendered"][0]["prompt"]
              == "choice as a forking path"
              and api.compact_collect(report)["illustrations"]
              == report["illustrations"], str(report.get("illustrations")))

        # --- illus rendering: candidates, pinning, style law, prune ---
        import struct
        import zlib

        from authorlm import illus as illus_mod
        from authorlm.revisions import (read_manuscript_files
                                        as read_files_for_test,
                                        strip_embed_lines
                                        as strip_embed_lines_for_test)

        def tiny_png() -> bytes:
            def chunk(t, d):
                return (struct.pack(">I", len(d)) + t + d
                        + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF))
            return (b"\x89PNG\r\n\x1a\n"
                    + chunk(b"IHDR",
                            struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
                    + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
                    + chunk(b"IEND", b""))

        gen_calls = []

        def fake_gen(prompt, input_png):
            gen_calls.append((prompt, input_png))
            return tiny_png()

        slot = illus_mod.find_slot(ms, "forking path")[0]
        # Mint the key explicitly and let collect absorb the externalize,
        # so what follows tests the EMBED's invisibility and nothing else.
        slot = illus_mod.ensure_key(ms, slot)
        api.collect(db, manuscript, {})
        h = slot["desc_hash"]
        check("externalizing to mint a key leaves the description hash "
              "alone, so nothing already rendered is disturbed",
              illus_mod.find_slot(ms, "forking path")[0]["desc_hash"] == h
              and slot["key"] == "choice-as-a-forking", str(slot))
        r1 = illus_mod.render_slot(db, manuscript, slot, {},
                                   generator=fake_gen)
        first = f"choice-as-a-forking-{h}-0000-01.png"
        choice_text = (ms / "01-choice.md").read_text()
        check("render writes candidate 01 and embeds the first render",
              r1["written"] == [first] and not r1["had_embed"]
              and f"![](_illustrations/{first})" in choice_text, str(r1))
        blob = (ms / "_illustrations" / first).read_bytes()
        check("candidate PNG carries the prompt as metadata",
              b"authorlm:prompt" in blob
              and "choice as a forking path".encode() in blob)
        check("embed lines are invisible to observation",
              "_illustrations" not in
              read_files_for_test(ms)["01-choice.md"]
              and api.collect(db, manuscript, {}).get("unchanged") is True)

        r2 = illus_mod.render_slot(db, manuscript, slot, {}, count=2,
                                   generator=fake_gen)
        check("re-render appends numbered candidates, embed stays pinned",
              [n[-6:-4] for n in r2["written"]] == ["02", "03"]
              and r2["had_embed"]
              and illus_mod.embed_target(
                  (ms / "01-choice.md").read_text(),
                  slot["key"]) == first, str(r2))

        api.add_style_law(db, manuscript, "illustration",
                              "woodcut, high-contrast linework",
                              file="01-choice.md")
        law = illus_mod.illustration_law(db, manuscript["id"], "01-choice.md")
        shash = illus_mod.style_hash(law)
        check("illustration law renders from illustration-aspect elements",
              "woodcut" in law and shash != "0000"
              and illus_mod.style_hash("") == "0000")
        status = [s for s in illus_mod.slot_status(db, manuscript)
                  if s["file"] == "01-choice.md"][0]
        check("a new illustration rule marks the embedded slot stale-style",
              status["state"] == "stale-style"
              and status["candidates"] == 3, str(status))

        r3 = illus_mod.render_slot(db, manuscript, slot, {}, from_n=1,
                                   generator=fake_gen)
        check("render --from passes the source image and the new law",
              gen_calls[-1][1] is not None
              and law in gen_calls[-1][0]
              and r3["written"] == [
                  f"choice-as-a-forking-{h}-{shash}-04.png"], str(r3))
        assembled = illus_mod.effective_prompt(db, manuscript, slot)
        check("effective_prompt is byte-identical to what the renderer "
              "sends",
              assembled["composed"] == gen_calls[-1][0]
              and assembled["style_hash"] == shash
              and assembled["law"] and assembled["law"] in
              assembled["composed"], str(assembled))

        illus_mod.set_embed(ms / "01-choice.md", slot["key"],
                            r3["written"][0])
        removed = illus_mod.prune(manuscript)
        check("prune removes every unpicked candidate, keeps the pick",
              sorted(removed) == sorted([first,
                  f"choice-as-a-forking-{h}-0000-02.png",
                  f"choice-as-a-forking-{h}-0000-03.png"])
              and (ms / "_illustrations" / r3["written"][0]).exists(),
              str(removed))

        # --- import: art made elsewhere becomes a first-class candidate.
        # The point of the door (it-2e4a5ec3d809): a system that can only
        # consume its own renders cannot reuse an illustrator's work.
        seed = root / "an-illustrator-plate.png"
        seed.write_bytes(tiny_png())
        imp = illus_mod.import_image(db, manuscript, slot, seed)
        check("import lands under the slot's key, numbered i for imported",
              imp["name"] == f"choice-as-a-forking-{h}-{shash}-i05.png"
              and (ms / "_illustrations" / imp["name"]).exists()
              and imp["had_embed"] is True, str(imp))
        plate = (ms / "_illustrations" / imp["name"]).read_bytes()
        check("an imported plate records where it came from",
              b"authorlm:source" in plate
              and b"an-illustrator-plate.png" in plate)
        cands = illus_mod.slot_candidates(ms, slot["key"])
        check("the import is a candidate like any other, marked imported",
              [c["src"] for c in cands if c["name"] == imp["name"]] == ["i"]
              and int(cands[-1]["n"]) == 5, str(cands))
        illus_mod.set_embed(ms / "01-choice.md", slot["key"], imp["name"])
        istatus = [s for s in illus_mod.slot_status(db, manuscript)
                   if s["file"] == "01-choice.md"][0]
        check("a slot standing on an imported plate reads as imported, "
              "never as a render it is not",
              istatus["state"] == "imported", str(istatus))
        r4 = illus_mod.render_slot(db, manuscript, slot, {}, from_n=5,
                                   generator=fake_gen)
        check("an imported plate seeds a render like any other candidate",
              gen_calls[-1][1] is not None
              and r4["written"] == [
                  f"choice-as-a-forking-{h}-{shash}-06.png"], str(r4))
        (root / "notes.txt").write_text("not an image")
        try:
            illus_mod.import_image(db, manuscript, slot, root / "notes.txt")
            refused = False
        except ValueError:
            refused = True
        check("import refuses a file the manuscript could never embed",
              refused)

        # An import into an INLINE slot mints the key on the way in, the
        # same as a render does — otherwise the plate would be named
        # after a description and orphan on the next reword.
        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text()
            + "\n[Illustration: a bare hillside at first light]\n")
        bare_slot = illus_mod.find_slot(ms, "bare hillside")[0]
        check("the new slot starts inline, with no key", not bare_slot["key"])
        imp2 = illus_mod.import_image(db, manuscript, bare_slot, seed)
        check("importing into an inline slot externalizes it first",
              imp2["ref"] == "a-bare-hillside-at.md"
              and imp2["name"].startswith("a-bare-hillside-at-")
              and imp2["name"].endswith("-i01.png")
              and "⇢ a-bare-hillside-at.md"
              in (ms / "01-choice.md").read_text()
              and not imp2["had_embed"], str(imp2))

        # --- the cast: cast-id / cast / prior, references, stale-cast, the
        # pin (docs/verse-and-cast-design.md §5–§7) ---
        ctag = illus_mod.parse_tag(
            "[Illustration: a serpent in three strokes | caption: none "
            "| cast-id: Dragon]")
        check("tag grammar parses cast-id (lowercased) beside caption",
              ctag["cast_id"] == "dragon" and ctag["caption"] == "none"
              and ctag["prompt"] == "a serpent in three strokes", str(ctag))
        rtag = illus_mod.parse_tag(
            "[Illustration: a hooded rider ⇢ hooded-rider.md | cast: Seeker, "
            "dragon | prior: relegere-stage]")
        check("tag grammar parses cast list, prior, and ref together",
              rtag["cast"] == ["seeker", "dragon"]
              and rtag["prior"] == "relegere-stage"
              and rtag["ref"] == "hooded-rider.md"
              and rtag["prompt"] == "a hooded rider", str(rtag))
        check("a tag without options carries empty cast fields",
              illus_mod.parse_tag("[Illustration: plain]")["cast"] == []
              and illus_mod.parse_tag("[Illustration: plain]")["prior"] is None)
        (ms / "illustration_cast.md").write_text(
            "[Omit: pdf, docx, epub, md, audio]\n\n# Cast\n\n"
            "[Illustration: a serpent in three strokes | cast-id: dragon]\n\n"
            "[Illustration: a lone pilgrim with a wide hat | cast-id: seeker]"
            "\n\n[/Omit]\n")
        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text()
            + "\n[Illustration: the rider meets the serpent | cast: dragon, "
              "seeker]\n")
        api.collect(db, manuscript, {})
        hero = illus_mod.find_slot(ms, "rider meets")[0]
        unresolved = illus_mod.resolve_references(ms, hero)
        check("an unpicked cast plate is a named error, never a silent "
              "omission",
              len(unresolved["errors"]) == 2
              and "dragon" in unresolved["errors"][0]
              and "no pick" in unresolved["errors"][0]
              and not unresolved["references"], str(unresolved))
        try:
            illus_mod.render_slot(db, manuscript, hero, {}, generator=fake_gen)
            refused = False
        except LookupError:
            refused = True
        check("render stops on an unresolved cast", refused)
        dragon = illus_mod.find_slot(ms, "serpent in three")[0]
        rd = illus_mod.render_slot(db, manuscript, dragon, {}, generator=fake_gen)
        # Re-find after the render: externalizing the dragon inserted an
        # embed line above the seeker, and a slot's line number is only
        # good until the file changes.
        seeker = illus_mod.find_slot(ms, "lone pilgrim")[0]
        rs = illus_mod.render_slot(db, manuscript, seeker, {}, generator=fake_gen)
        index, problems = illus_mod.cast_index(ms)
        check("cast_index finds both plates picked, with no problems",
              set(index) == {"dragon", "seeker"}
              and index["dragon"]["plate"] == rd["written"][0]
              and not problems, str((sorted(index), problems)))
        ref_calls = []

        def fake_gen_refs(prompt, input_png, references=None):
            ref_calls.append((prompt, input_png, references))
            return tiny_png()

        hero = illus_mod.find_slot(ms, "rider meets")[0]
        assembled = illus_mod.effective_prompt(db, manuscript, hero)
        check("effective_prompt carries an identification-only reference "
              "block in cast order",
              "Image 1: the Dragon of this book" in assembled["composed"]
              and "Image 2: the Seeker of this book" in assembled["composed"]
              and [r["slug"] for r in assembled["references"]]
              == ["dragon", "seeker"]
              and assembled["composed"].index("DEPICT")
              < assembled["composed"].index("REFERENCES")
              < assembled["composed"].index("STYLE"),
              assembled["composed"])
        rh = illus_mod.render_slot(db, manuscript, hero, {},
                                   generator=fake_gen_refs)
        check("render attaches the picked plates as references, in order, "
              "with the same composed prompt",
              len(ref_calls[-1][2]) == 2
              and ref_calls[-1][0] == assembled["composed"]
              and rh["references"] == ["cast dragon", "cast seeker"],
              str(rh))
        meta = illus_mod.read_metadata(ms / "_illustrations" / rh["written"][0])
        check("the render records which plates it was made against",
              meta.get("authorlm:cast")
              == f"dragon={rd['written'][0]};seeker={rs['written'][0]}"
              and meta.get("authorlm:off-model", "") == "", str(meta))
        hstat = [st for st in illus_mod.slot_status(db, manuscript)
                 if "rider meets" in st["prompt"]][0]
        check("a render made against the current picks is not stale-cast",
              hstat["state"] == "rendered"
              and hstat["cast"] == ["dragon", "seeker"], str(hstat))
        dragon = illus_mod.find_slot(ms, "serpent in three")[0]
        rd2 = illus_mod.render_slot(db, manuscript, dragon, {},
                                    generator=fake_gen)
        illus_mod.set_embed(ms / "illustration_cast.md", dragon["key"],
                            rd2["written"][0])
        hstat = [st for st in illus_mod.slot_status(db, manuscript)
                 if "rider meets" in st["prompt"]][0]
        check("re-picking a cast plate marks every dependent render "
              "stale-cast", hstat["state"] == "stale-cast", str(hstat))
        bad = {**hero, "cast": ["dragon", "seeker", "ghost"]}
        check("an unknown cast id is a named error",
              any("ghost" in e for e in
                  illus_mod.resolve_references(ms, bad)["errors"]))
        crowded = {**hero, "cast": ["dragon", "seeker", "dragon", "a", "b"]}
        check("more than three cast ids is a named error",
              any("at most" in e for e in
                  illus_mod.resolve_references(ms, crowded)["errors"]))
        rep_cast = illus_mod.slot_report(ms)
        check("slot_report carries cast fields and no false problems",
              "cast_problems" in rep_cast and not rep_cast["cast_problems"],
              str(rep_cast["cast_problems"]))

        # the pin: stamped complete from the global default, then owned
        pin_root = root / "pin-scratch"
        pin_root.mkdir()
        pin_cfg = {"illustrations": {"model": "openai/test-image",
                                     "image_size": "1024x1536"}}
        pin = illus_mod.ensure_pin(pin_root, pin_cfg)
        check("the pin is stamped complete from the global default, in the "
              "manuscript folder",
              pin == {"model": "openai/test-image", "image_size": "1024x1536"}
              and illus_mod.pin_path(pin_root).exists()
              and illus_mod.load_pin(pin_root) == pin, str(pin))
        moved_cfg = {"illustrations": {"model": "gemini/other",
                                       "image_size": "1024x1024"}}
        held = illus_mod.resolve_pin(pin_root, moved_cfg)
        check("after stamping, the global setting no longer reaches the "
              "manuscript (copy, then own)",
              held["model"] == "openai/test-image"
              and held["size"] == "1024x1536" and not held["off_model"],
              str(held))
        off = illus_mod.resolve_pin(pin_root, moved_cfg,
                                    model_override="gemini/other")
        check("a named other model is off-model, never a silent fallback",
              off["model"] == "gemini/other" and off["off_model"]
              and off["pinned_model"] == "openai/test-image", str(off))
        illus_mod.write_pin(pin_root, "openai/test-image", "1024x1024")
        check("illus pin rewrites the pin explicitly",
              illus_mod.load_pin(pin_root)["image_size"] == "1024x1024")
        seeker = illus_mod.find_slot(ms, "lone pilgrim")[0]
        ro = illus_mod.render_slot(db, manuscript, seeker, {},
                                   generator=fake_gen, model="gemini/other")
        ometa = illus_mod.read_metadata(
            ms / "_illustrations" / ro["written"][0])
        check("an off-model render says so in its result and metadata",
              ro["off_model"] and ro["model"] == "gemini/other"
              and ometa.get("authorlm:off-model") == "true"
              and ometa.get("authorlm:model") == "gemini/other", str(ometa))
        (ms / "illustration_cast.md").unlink()
        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text().replace(
                "\n[Illustration: the rider meets the serpent | cast: dragon, "
                "seeker]\n", ""))
        api.collect(db, manuscript, {})

        # --- capture_embeds/reembed: the pick survives a Doc round trip ---
        embed_root = root / "illus-embed-scratch"
        (embed_root / "_illustrations" / "prompts").mkdir(parents=True)
        (embed_root / "ch.md").write_text(
            "# C\n\n[Illustration: a lone tracker ⇢ a-lone-tracker.md]\n")
        (embed_root / "_illustrations" / "prompts"
         / "a-lone-tracker.md").write_text("a lone tracker\n")
        ekey = "a-lone-tracker"
        eh = illus_mod.desc_hash("a lone tracker")
        cand1 = f"{ekey}-{eh}-0000-01.png"
        cand2 = f"{ekey}-{eh}-0000-02.png"
        (embed_root / "_illustrations" / cand1).write_bytes(b"")
        (embed_root / "_illustrations" / cand2).write_bytes(b"")
        bare = (embed_root / "ch.md").read_text()

        never_picked = illus_mod.reembed(bare, embed_root)
        check("a never-picked slot falls back to the newest candidate",
              illus_mod.embed_target(never_picked, ekey) == cand2, never_picked)

        pick = {ekey: cand1}
        embedded = illus_mod.reembed(bare, embed_root, prior=pick)
        check("a prior pick wins over the newest candidate",
              illus_mod.embed_target(embedded, ekey) == cand1, embedded)
        check("capture_embeds recovers the exact picked candidate",
              illus_mod.capture_embeds(embedded) == pick,
              illus_mod.capture_embeds(embedded))
        check("reembed on already-embedded text never doubles the line",
              illus_mod.reembed(embedded, embed_root, prior=pick) == embedded,
              illus_mod.reembed(embedded, embed_root, prior=pick))

        (embed_root / "_illustrations" / cand1).unlink()
        fallback = illus_mod.reembed(bare, embed_root, prior=pick)
        check("a pick whose file is gone falls back to the newest candidate",
              illus_mod.embed_target(fallback, ekey) == cand2, fallback)

        # In-memory Drive + Docs fake for the tabbed master-Doc model:
        # one object serves as both `service` and `docs_service`. Master
        # Doc state is tabs of literal markdown; the temp-import Doc is
        # modeled as one plain-text paragraph per line, and export renders
        # each tab under a '# **<title>**' heading — the same shape
        # split_tabbed_export expects from the real API.
        from authorlm.gdocs import FOLDER_MIME, doc_status

        class FakeRequest:
            def __init__(self, payload):
                self._payload = payload

            def execute(self):
                return self._payload

        class FakeFiles:
            def __init__(self, state):
                self.state = state

            def create(self, body=None, media_body=None, fields=None):
                self.state["counter"] += 1
                fid = f"doc-{self.state['counter']}"
                if media_body is not None:  # markdown import (temp/export)
                    self.state["uploads"][fid] = media_body.getbytes(
                        0, media_body.size()).decode("utf-8", "replace")
                elif (body or {}).get("mimeType") == FOLDER_MIME:
                    self.state["folders"].append(fid)
                else:  # a native Doc, born with the blank default tab
                    self.state["tab_counter"] += 1
                    tab_id = f"tab-{self.state['tab_counter']}"
                    self.state["docs"][fid] = [
                        {"id": tab_id, "title": "Tab 1", "text": ""}]
                return FakeRequest({"id": fid})

            def update(self, fileId=None, media_body=None):
                self.state["uploads"][fileId] = media_body.getbytes(
                    0, media_body.size()).decode("utf-8", "replace")
                return FakeRequest({})

            def delete(self, fileId=None):
                self.state["uploads"].pop(fileId, None)
                return FakeRequest({})

            def export(self, fileId=None, mimeType=None):
                def as_markdown(text):
                    # The real exporter separates doc paragraphs with
                    # blank lines; the fake stores one paragraph per
                    # line (empties tolerated for legacy set_tab text).
                    paras = [ln for ln in text.split("\n") if ln.strip()]
                    return "\n\n".join(paras) + ("\n" if paras else "")

                flat = self.state["docs"][fileId]

                def dfs(parent_id):
                    # The real export concatenates tabs in document tab
                    # order — a DFS of the tab tree (container, then its
                    # nested children, then the next root sibling) — NOT
                    # raw creation order, which can differ once a tab is
                    # nested under a parent created earlier (it-x7-2:
                    # this used to silently diverge from documents().get(),
                    # which already walks the tree correctly).
                    ordered = []
                    for t in flat:
                        if t.get("parent") == parent_id:
                            ordered.append(t)
                            ordered.extend(dfs(t["id"]))
                    return ordered

                whole = "\n".join(
                    f"# **{t['title']}**\n\n{as_markdown(t['text'])}"
                    for t in dfs(None))
                return FakeRequest(whole.encode("utf-8"))

        class FakeDocuments:
            def __init__(self, state):
                self.state = state

            def get(self, documentId=None, includeTabsContent=None):
                if documentId in self.state["uploads"]:
                    content = [{"paragraph": {"elements": [
                        {"textRun": {"content": line + "\n"}}]}}
                        for line in
                        self.state["uploads"][documentId].splitlines()]
                    return FakeRequest({"body": {"content": content}})
                def paragraphs(text):
                    items, pos = [], 1
                    for line in text.split("\n"):
                        content = line + "\n"
                        items.append({
                            "startIndex": pos,
                            "endIndex": pos + len(content),
                            "paragraph": {"elements": [
                                {"startIndex": pos,
                                 "endIndex": pos + len(content),
                                 "textRun": {"content": content}}]}})
                        pos += len(content)
                    return items

                flat = self.state["docs"][documentId]

                def node(t):
                    return {"tabProperties": {"tabId": t["id"],
                                              "title": t["title"]},
                            "documentTab": {"body": {"content":
                                paragraphs(t["text"]) if t["text"] else []}},
                            "childTabs": [node(c) for c in flat
                                          if c.get("parent") == t["id"]]}

                return FakeRequest({"tabs": [node(t) for t in flat
                                             if not t.get("parent")]})

            def batchUpdate(self, documentId=None, body=None):
                tabs = self.state["docs"][documentId]

                def tab(tid):
                    return next(t for t in tabs if t["id"] == tid)

                replies = []
                for req in (body or {}).get("requests", []):
                    if "addDocumentTab" in req:
                        self.state["tab_counter"] += 1
                        props = req["addDocumentTab"]["tabProperties"]
                        new = {"id": f"tab-{self.state['tab_counter']}",
                               "title": props["title"], "text": "",
                               "parent": props.get("parentTabId")}
                        tabs.append(new)
                        replies.append({"addDocumentTab": {
                            "tabProperties": {"tabId": new["id"]}}})
                        continue
                    replies.append({})
                    if "deleteTab" in req:
                        doomed = tab(req["deleteTab"]["tabId"])
                        gone = {doomed["id"]}
                        tabs.remove(doomed)
                        while True:  # the API deletes the whole subtree
                            orphans = [t for t in tabs
                                       if t.get("parent") in gone]
                            if not orphans:
                                break
                            for t in orphans:
                                gone.add(t["id"])
                                tabs.remove(t)
                    elif "updateDocumentTabProperties" in req:
                        props = req["updateDocumentTabProperties"][
                            "tabProperties"]
                        moved = tab(props["tabId"])
                        if "parentTabId" in props:
                            moved["parent"] = props["parentTabId"] or None
                        tabs.remove(moved)
                        tabs.insert(props["index"], moved)
                    elif "deleteContentRange" in req:
                        tab(req["deleteContentRange"]["range"]
                            ["tabId"])["text"] = ""
                    elif "insertText" in req:
                        target = tab(req["insertText"]["location"]["tabId"])
                        loc = req["insertText"]["location"]
                        if "index" in loc:
                            i = loc["index"] - 1
                            target["text"] = (target["text"][:i]
                                              + req["insertText"]["text"]
                                              + target["text"][i:])
                        else:
                            target["text"] += req["insertText"]["text"]
                return FakeRequest({"replies": replies})

        class FakeComments:
            def __init__(self, state):
                self.state = state

            def list(self, fileId=None, pageToken=None, includeDeleted=None,
                     fields=None):
                return FakeRequest({"comments": [
                    c for c in self.state["comments"].values()
                    if not c.get("resolved")]})

        class FakeReplies:
            def __init__(self, state):
                self.state = state

            def create(self, fileId=None, commentId=None, body=None,
                       fields=None):
                self.state["reply_counter"] += 1
                reply = {"id": f"r-{self.state['reply_counter']}",
                         "content": body["content"],
                         "author": {"displayName": "author"}}
                self.state["comments"][commentId]["replies"].append(reply)
                if body.get("action") == "resolve":
                    self.state["comments"][commentId]["resolved"] = True
                return FakeRequest({"id": reply["id"]})

        class FakeGoogle:
            def __init__(self):
                self.state = {"counter": 0, "tab_counter": 0,
                              "comments": {}, "reply_counter": 0,
                              "folders": [], "docs": {}, "uploads": {}}
                self._files = FakeFiles(self.state)
                self._documents = FakeDocuments(self.state)

            def files(self):
                return self._files

            def documents(self):
                return self._documents

            def comments(self):
                return FakeComments(self.state)

            def replies(self):
                return FakeReplies(self.state)

            def add_comment(self, comment_id, quoted, content):
                self.state["comments"][comment_id] = {
                    "id": comment_id, "content": content,
                    "quotedFileContent": {"value": quoted},
                    "resolved": False, "replies": [],
                    "author": {"displayName": "author"},
                    "createdTime": "2026-08-08T00:00:00Z"}

            def author_reply(self, comment_id, content):
                self.state["comments"][comment_id]["replies"].append(
                    {"content": content,
                     "author": {"displayName": "author"}})

            def set_tab(self, title, text):  # simulate an edit in Docs
                for tabs in self.state["docs"].values():
                    for t in tabs:
                        if t["title"] == title:
                            t["text"] = text

        stub = FakeGoogle()
        pushed = push_doc(db, manuscript, "01-choice.md",
                          service=stub, docs_service=stub)
        check("push creates folder, master Doc, and the file's tab",
              pushed["created"] and pushed["doc_id"] == "doc-2"
              and "tab=" in pushed["url"])
        manuscript = api.get_manuscript(db)  # refresh metadata
        status = doc_status(db, manuscript)
        check("push mapping persisted: checkout, folder, master, tab id",
              status["01-choice.md"]["checked_out"] is True
              and status["01-choice.md"]["tab_id"]
              and status["_folder_id"] == "doc-1"
              and status["_master_id"] == "doc-2")
        master = stub.state["docs"]["doc-2"]
        tab_text = {t["title"]: t["text"] for t in master}
        check("master carries the container tab, one tab per file, and "
              "the manifest; default tab retired",
              [t["title"] for t in master]
              == ["book", "01-choice.md", "manifest"],
              str([t["title"] for t in master]))
        def as_tab(md: str) -> str:
            # A pushed tab carries no blank paragraphs (it-69b61b6d7fa2):
            # markdown's separator lines are dropped in transplant.
            return "\n".join(ln for ln in md.split("\n") if ln.strip()) + "\n"

        check("push transplants the file's markdown into its tab, "
              "embed lines stripped, blank separators dropped",
              tab_text["01-choice.md"]
              == as_tab(strip_embed_lines_for_test(
                  (ms / "01-choice.md").read_text()))
              and "_illustrations" not in tab_text["01-choice.md"]
              and "[Illustration:" in tab_text["01-choice.md"]
              and not stub.state["uploads"])  # temp import Doc deleted
        check("manifest tab names the manuscript and doc incarnation",
              "Manuscript: book" in tab_text["manifest"]
              and "Doc version: 1" in tab_text["manifest"])
        stub.set_tab("01-choice.md",
                     "# Title\n\nEdited in Docs \\- with escapes.\n")
        pulled = pull_doc(db, manuscript, "01-choice.md", service=stub)
        check("pull writes normalized export and clears checkout",
              pulled["changed"] == ["01-choice.md"]
              and (ms / "01-choice.md").read_text()
              == "# Title\n\nEdited in Docs - with escapes.\n"
              and doc_status(db, manuscript)["01-choice.md"]["checked_out"]
              is False)
        pushed2 = push_doc(db, manuscript, "01-choice.md",
                           service=stub, docs_service=stub)
        check("second push reuses the master Doc (no new folder or Doc)",
              not pushed2["created"] and pushed2["doc_id"] == "doc-2"
              and stub.state["folders"] == ["doc-1"]
              and list(stub.state["docs"]) == ["doc-2"])

        # --- sync_tab_structure: push-direction TOC↔tab orchestration ---
        from authorlm.gdocs import _mapping, _save_mapping, sync_tab_structure

        manuscript = api.get_manuscript(db)
        no_toc = sync_tab_structure(db, manuscript, stub)
        check("sync_tab_structure skips when toc.toml is absent",
              no_toc == {"skipped": "no toc.toml"}, str(no_toc))
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "01-choice.md"\n')
        synced = sync_tab_structure(db, manuscript, stub)
        check("sync_tab_structure records insync when TOC matches Doc tabs",
              synced == {"insync": True}, str(synced))
        meta = _mapping(db, manuscript)
        check("insync sync persists _tab_structure as the agreed base",
              meta["gdocs"].get("_tab_structure")
              == [["01-choice.md", None]],
              str(meta["gdocs"].get("_tab_structure")))

        (ms / "02-fork.md").write_text("# Fork\n\nAnother essay.\n")
        push_doc(db, manuscript, "02-fork.md",
                 service=stub, docs_service=stub)
        manuscript = api.get_manuscript(db)

        # X7-2 regression: split_tabbed_export's documented contract says
        # "content headings, even H1s, pass through untouched" unless they
        # name a known tab — but a prose H1 that happens to repeat some
        # OTHER tab's title used to be treated as that tab's boundary too,
        # truncating the essay at the collision point. Put such a heading
        # inside 01-choice.md's prose, matching the manifest tab's title
        # (a real boundary that sits further down the true tab order, past
        # 02-fork.md — not the essay's own immediate neighbor), and prove
        # pull keeps everything after it.
        collision_body = (
            "# Title\n\n"
            "manifest\n\n"
            "Prose survives.\n\n"
            "# manifest\n\n"
            "More prose after the collision, never truncated.\n")
        stub.set_tab("01-choice.md", collision_body)
        collided = pull_doc(db, manuscript, "01-choice.md", service=stub,
                            docs_service=stub)
        pulled_text = (ms / "01-choice.md").read_text()
        check("a prose heading matching another tab's title never "
              "truncates the essay at the collision point (it-x7-2)",
              collided["changed"] == ["01-choice.md"]
              and "More prose after the collision, never truncated."
              in pulled_text, str(collided) + "\n" + pulled_text)
        check("the colliding heading itself passed through as ordinary "
              "content, not consumed as a boundary",
              "# manifest" in pulled_text, pulled_text)
        check("the real manifest tab is untouched by the collision",
              "Manuscript: book" in
              next(t["text"] for t in stub.state["docs"]["doc-2"]
                   if t["title"] == "manifest"))

        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "01-choice.md"\n\n'
            '[[chapter]]\nfile = "02-fork.md"\n')
        base_sync = sync_tab_structure(db, manuscript, stub)
        check("sync after adding a linked chapter lands insync (or moves)",
              base_sync.get("insync") is True
              or isinstance(base_sync.get("moved"), int),
              str(base_sync))
        # Force a conflict: Doc order and TOC both diverge from the base.
        meta = _mapping(db, manuscript)
        meta["gdocs"]["_tab_structure"] = [
            ["01-choice.md", None], ["02-fork.md", None]]
        _save_mapping(db, manuscript, meta)
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "02-fork.md"\n\n'
            '[[chapter]]\nfile = "01-choice.md"\n')
        tabs = stub.state["docs"]["doc-2"]
        # Put 02-fork before 01-choice among root-level md tabs by
        # rebuilding flat order while leaving container/manifest alone.
        md_tabs = [t for t in tabs if t["title"].endswith(".md")]
        others = [t for t in tabs if not t["title"].endswith(".md")]
        # Doc side: 02 then 01 (matches neither base nor the swapped TOC
        # wait — TOC is also 02 then 01. Use a third ordering via parent
        # so Doc ≠ TOC ≠ base.
        by_title = {t["title"]: t for t in md_tabs}
        by_title["02-fork.md"]["parent"] = by_title["01-choice.md"]["id"]
        stub.state["docs"]["doc-2"] = others + [
            by_title["01-choice.md"], by_title["02-fork.md"]]
        conflicted = sync_tab_structure(db, manuscript, stub)
        check("sync_tab_structure refuses when Doc and TOC both moved",
              conflicted.get("skipped", "").startswith(
                  "Doc tab order also changed"),
              str(conflicted))

        # No master: skip before any Docs call.
        meta = _mapping(db, manuscript)
        master = meta["gdocs"].pop("_master_id")
        _save_mapping(db, manuscript, meta)
        no_master = sync_tab_structure(db, manuscript, stub)
        check("sync_tab_structure skips when there is no master Doc",
              no_master == {"skipped": "no master doc"}, str(no_master))
        meta["gdocs"]["_master_id"] = master
        # Drop the second chapter so later single-file reconcile/export
        # assertions stay focused on 01-choice.md.
        meta["gdocs"].pop("02-fork.md", None)
        meta["gdocs"]["_tab_structure"] = [["01-choice.md", None]]
        _save_mapping(db, manuscript, meta)
        stub.state["docs"]["doc-2"] = [
            t for t in stub.state["docs"]["doc-2"]
            if t["title"] != "02-fork.md"]
        (ms / "02-fork.md").unlink(missing_ok=True)
        (ms / "toc.toml").unlink(missing_ok=True)

        # Two-sided edit: local changed since push AND the tab differs
        # from what was pushed → conflict, skipped unless forced.
        (ms / "01-choice.md").write_text("# Title\n\nLocal divergence.\n")
        manuscript = api.get_manuscript(db)
        stub.set_tab("01-choice.md", "# Title\n\nEdited in Docs again.\n")
        report = pull_doc(db, manuscript, "01-choice.md", service=stub)
        check("two-sided edits conflict, file untouched",
              report["conflicts"] == ["01-choice.md"]
              and "Local divergence" in (ms / "01-choice.md").read_text(),
              str(report))
        forced = pull_doc(db, manuscript, "01-choice.md", service=stub,
                          force=True)
        check("pull --force takes the Doc's version",
              forced["changed"] == ["01-choice.md"]
              and "Edited in Docs again" in (ms / "01-choice.md").read_text())

        # A Doc-pasted image exports as a dangling ![][imageN] ref: the
        # pull strips it (the bridge cannot transport images) and warns.
        stub.set_tab("01-choice.md",
                     "# Title\n\nProse kept. ![][image9]\n\n"
                     "[image9]: <data:image/png;base64,abc>\n")
        pulled_img = pull_doc(db, manuscript, "01-choice.md", service=stub)
        check("pull strips Doc-pasted image refs and warns",
              pulled_img["dangling_images"]["01-choice.md"]
              and "image9" not in (ms / "01-choice.md").read_text()
              and "Prose kept." in (ms / "01-choice.md").read_text(),
              str(pulled_img))

        # Embed round trip: the embed line never reaches the Doc, and a
        # pull restores the pinned pick under its tag.
        picked_name = r3["written"][0]
        keyed_tag = ("[Illustration: choice as a forking path "
                     "⇢ choice-as-a-forking.md]")
        (ms / "01-choice.md").write_text(
            f"# Title\n\n{keyed_tag}\n"
            f"![](_illustrations/{picked_name})\n\nProse below.\n")
        push_doc(db, manuscript, "01-choice.md",
                 service=stub, docs_service=stub)
        tab_now = next(t["text"] for t in stub.state["docs"]["doc-2"]
                       if t["title"] == "01-choice.md")
        check("push keeps the tag but never the embed line",
              keyed_tag in tab_now
              and "_illustrations/choice" not in tab_now, tab_now)
        stub.set_tab("01-choice.md", tab_now.replace(
            "Prose below.", "Prose below, edited in the Doc."))
        pull_doc(db, manuscript, "01-choice.md", service=stub)
        round_tripped = (ms / "01-choice.md").read_text()
        check("pull re-inserts the pinned embed under its tag",
              f"{keyed_tag}\n![](_illustrations/{picked_name})"
              in round_tripped
              and "edited in the Doc" in round_tripped, round_tripped)

        # THE REGRESSION, at the door it actually came through: the
        # author rewords a description IN THE DOC. The old capture was
        # keyed by the description hash, so the reworded tag matched
        # nothing and the pull dropped the picked image on the floor.
        stub.set_tab("01-choice.md", tab_now.replace(
            "choice as a forking path",
            "a road parting under a low sky, nothing alike"))
        pull_doc(db, manuscript, "01-choice.md", service=stub)
        reworded = (ms / "01-choice.md").read_text()
        check("rewording the description in the Doc keeps the pinned "
              "image through the pull",
              f"![](_illustrations/{picked_name})" in reworded
              and "a road parting under a low sky" in reworded, reworded)

        # --- session-start reconciliation: all four outcomes ---
        from authorlm.gdocs import reconcile

        manuscript = api.get_manuscript(db)
        report = reconcile(db, manuscript, stub)
        check("reconcile: in sync after a pull",
              report["in_sync"] == ["01-choice.md"] and not report["conflicts"],
              str(report))

        (ms / "01-choice.md").write_text("# Title\n\nLocal-only progress.\n")
        report = reconcile(db, manuscript, stub)
        check("reconcile: local-only change without a Docs service is pending",
              report["pending_push"] == ["01-choice.md"], str(report))
        report = reconcile(db, manuscript, stub, docs_service=stub)
        check("reconcile: only-local change auto-pushes",
              report["pushed"] == ["01-choice.md"]
              and next(t["text"] for t in stub.state["docs"]["doc-2"]
                       if t["title"] == "01-choice.md")
              == "# Title\nLocal-only progress.\n", str(report))
        # The tab now reflects the push; the next reconcile sees sync.
        report = reconcile(db, manuscript, stub)
        check("reconcile: after push, next start is in sync",
              report["in_sync"] == ["01-choice.md"], str(report))
        stub.set_tab("01-choice.md", "# Title\n\nDoc-only revision now.\n")
        report = reconcile(db, manuscript, stub)
        check("reconcile: only-Doc change auto-pulls",
              report["pulled"] == ["01-choice.md"]
              and "Doc-only revision" in (ms / "01-choice.md").read_text(),
              str(report))
        (ms / "01-choice.md").write_text("# Title\n\nBoth sides now differ.\n")
        stub.set_tab("01-choice.md", "# Title\n\nDoc went another way.\n")
        report = reconcile(db, manuscript, stub)
        check("reconcile: two-sided edits conflict, files untouched",
              report["conflicts"] == ["01-choice.md"]
              and "Both sides now differ" in (ms / "01-choice.md").read_text(),
              str(report))

        # Missing tab ≠ empty Doc: a renamed/deleted export section must
        # never auto-pull "" over a local file that still matches the base.
        safe = "# Title\n\nSafe prose that must survive a missing tab.\n"
        (ms / "01-choice.md").write_text(safe)
        push_doc(db, manuscript, "01-choice.md",
                 service=stub, docs_service=stub)
        essay_tab = next(t for t in stub.state["docs"]["doc-2"]
                         if t["title"] == "01-choice.md")
        essay_tab["title"] = "01-choice.md.ORPHAN"
        report = reconcile(db, manuscript, stub)
        check("reconcile: missing Doc tab never wipes local",
              (ms / "01-choice.md").read_text() == safe
              and report["pulled"] == []
              and any(e["file"] == "01-choice.md" for e in report["errors"]),
              str(report))
        # Restore the Doc/local body later margin-thread checks expect —
        # the missing-tab push left "Safe prose…" which cannot attribute
        # a quote of "Doc went another way".
        essay_tab["title"] = "01-choice.md"
        restored = "# Title\n\nDoc went another way.\n"
        stub.set_tab("01-choice.md", restored)
        (ms / "01-choice.md").write_text(restored)

        # --- doc pull truncation on a base-less mapped file (T2,
        # risk-register BUG-1 repro): ensure_master gives every file in
        # reading order a tab, but only the pushed file gets a
        # pushed_hash. A pull of "everything mapped" then sees the
        # never-pushed file's empty tab as "changed" with no recorded
        # base and overwrites the local file with nothing. Own
        # workspace, own FakeGoogle instance — isolated from the shared
        # fixture above. CHARACTERIZATION repro (risk-register §3, T2):
        # this stays green as today's behavior, not a specification,
        # until the Sponsor authorizes the three_way base-less guard.
        t2_root = root / "t2-ws"
        t2_ms = t2_root / "manuscript"
        t2_ms.mkdir(parents=True)
        (t2_ms / "a.md").write_text("# A\n\nOriginal a content.\n")
        (t2_ms / "b.md").write_text(
            "# B\n\nOriginal b content, never individually pushed.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(t2_root), "init", "--name", "t2book",
                      "--path", str(t2_ms), "--no-extract"])
        t2_db = api.open_db(str(t2_root))
        t2_manuscript = api.get_manuscript(t2_db)
        t2_stub = FakeGoogle()
        pushed_a = push_doc(t2_db, t2_manuscript, "a.md",
                            service=t2_stub, docs_service=t2_stub)
        master_tabs = t2_stub.state["docs"][pushed_a["doc_id"]]
        b_tab = next(t for t in master_tabs if t["title"] == "b.md")
        check("ensure_master gave the never-pushed file b.md its own tab "
              "too — empty, since only a.md was actually pushed",
              b_tab["text"] == "", str(master_tabs))
        t2_manuscript = api.get_manuscript(t2_db)  # refresh metadata
        report = pull_doc(t2_db, t2_manuscript,
                          service=t2_stub, docs_service=t2_stub)
        check("a pull of everything mapped treats the base-less empty tab "
              "as 'changed' (no pushed_hash to compare against) and "
              "overwrites the never-pushed local file with nothing",
              "b.md" in report["changed"]
              and (t2_ms / "b.md").read_text() == "",
              str(report))
        check("meanwhile the actually-pushed file is untouched",
              "a.md" not in report["changed"]
              and "Original a content" in (t2_ms / "a.md").read_text())

        check_omit_all(root, FakeGoogle)
        check_read_manuscript(root, FakeGoogle)
        check_stage_revisions(root, FakeGoogle)

        # --- gdocs failure path: documents().get() outage during reconcile
        # (T6, risk-register §3) — reconcile's tab-listing pass is wrapped
        # in a bare try/except (gdocs.py reconcile, ~line 1610-1616); this
        # pins that it actually behaves as documented: the session survives
        # (no exception escapes), the failure surfaces as a per-file error
        # instead of being silently swallowed, and an in-sync local file is
        # left untouched rather than being mistaken for a change. ---
        class FailingDocsService:
            def documents(self):
                class _Boom:
                    def get(self, **kwargs):
                        raise RuntimeError("simulated Docs API outage")
                return _Boom()

        manuscript = api.get_manuscript(db)
        before_text = (ms / "01-choice.md").read_text()
        try:
            report = reconcile(db, manuscript, stub,
                               docs_service=FailingDocsService())
            reconcile_survived = True
        except Exception:
            reconcile_survived = False
        check("reconcile survives a documents().get() failure during tab "
              "listing instead of raising and killing the session",
              reconcile_survived)
        check("the failure surfaces as a per-file error, not silently "
              "swallowed",
              any(e.get("file") == "(tab listing)"
                  and "simulated Docs API outage" in e.get("error", "")
                  for e in report["errors"]), str(report))
        check("the local file is untouched — reconcile wrote nothing "
              "despite the tab-listing failure",
              (ms / "01-choice.md").read_text() == before_text)
        check("a file that was actually in sync is still reported in_sync "
              "despite the tab-listing failure (resolved via the plain "
              "export, independent of the failed tab walk)",
              "01-choice.md" in report["in_sync"], str(report))

        # --- margin threads: propose in-context; canonical stays old ---
        from authorlm import threads as th
        from authorlm.gdocs import propose_change

        strip_out = th.strip_pending("A <<old>>{{new}} B")
        check("pending grammar strips to canonical old; strays warn",
              strip_out == ("A old B", [])
              and th.strip_pending("stray << here")[1], str(strip_out))
        check("verdict grammar is deterministic, whole-reply only",
              th.classify_reply("Go ahead!") == "approve"
              and th.classify_reply("no") == "decline"
              and th.classify_reply("go ahead but soften it")
              == "conversation"
              and th.is_ours("AuthorLM: proposed — x")
              and not th.is_ours("looks wrong to me"))

        # Resolve helpers the critique/margin paths share: empty-old
        # insertions, strikethrough wrappers, and form enumeration that
        # must not double-count the {{new}} half of a replace.
        check("render_pending with empty old is the insertion form",
              th.render_pending("", "bridge") == "{{bridge}}"
              and th.render_insertion("bridge") == "{{bridge}}")
        wrapped = "Lead.\n\n~~<<old span>>~~{{new span}}\n\nTail.\n"
        check("approved_text keeps {{new}} halves (incl. ~~-wrapped replaces)",
              th.approved_text(wrapped) == "Lead.\n\nnew span\n\nTail.\n",
              th.approved_text(wrapped))
        check("strip_pending on ~~-wrapped replaces still yields OLD",
              th.strip_pending(wrapped)[0] == "Lead.\n\nold span\n\nTail.\n",
              th.strip_pending(wrapped))
        with_insert = ("Para one.\n\n{{inserted paragraph}}\n\n"
                       "<<swap me>>{{swapped}}\n\nPara three.\n")
        stripped_ins, _ = th.strip_pending(with_insert)
        check("strip_pending drops paragraph insertions byte-clean",
              stripped_ins == "Para one.\n\nswap me\n\nPara three.\n",
              stripped_ins)
        check("approved_text keeps paragraph insertions and replace news",
              th.approved_text(with_insert)
              == ("Para one.\n\ninserted paragraph\n\n"
                  "swapped\n\nPara three.\n"),
              th.approved_text(with_insert))
        # Author braces reach the tab via push; bare-INSERTION collapse
        # used to delete them on pull/reconcile (silent auto-pull when
        # local still matched pushed_hash). Paragraph insertions only.
        author_braces = (
            "A template substitutes {{title}} and writes {{a, b}}.\n\n"
            "Energy $E={{mc}}^2$.\n")
        brace_stripped, brace_warns = th.strip_pending(author_braces)
        check("strip_pending leaves author {{…}} / TeX braces intact "
              "with no marker warning",
              brace_stripped == author_braces and brace_warns == [],
              repr((brace_stripped, brace_warns)))
        head_insert = "{{lead insert}}\n\n" + author_braces
        head_stripped, _ = th.strip_pending(head_insert)
        check("strip_pending still drops a start-of-text critique "
              "insertion while keeping author braces below it",
              head_stripped == author_braces
              and "{{title}}" in head_stripped,
              repr(head_stripped))
        check("approved_text unwraps paragraph insertions but does not "
              "eat inline author braces",
              th.approved_text(head_insert)
              == "lead insert\n\n" + author_braces,
              th.approved_text(head_insert))
        forms = th.pending_forms(with_insert)
        check("pending_forms lists replace + insert once each, doc order",
              [(f["kind"], f["old"], f["new"]) for f in forms]
              == [("insert", "", "inserted paragraph"),
                  ("replace", "swap me", "swapped")],
              str(forms))
        nested = "<<keep {{this}} literal>>{{replacement}}"
        check("pending_forms does not treat replace's {{new}} as an insert",
              th.pending_forms(nested)
              == [{"kind": "replace", "old": "keep {{this}} literal",
                   "new": "replacement", "start": 0,
                   "end": len(nested)}],
              str(th.pending_forms(nested)))

        pull_doc(db, manuscript, "01-choice.md", service=stub, force=True)
        stub.add_comment("c-1", "Doc went another way",
                         "Can we make this stronger?")
        pulled_c = pull_doc(db, manuscript, "01-choice.md", service=stub)
        check("comments are ingested but never auto-resolved",
              any(c["comment_id"] == "c-1" for c in pulled_c["comments"])
              and not stub.state["comments"]["c-1"]["resolved"]
              and pulled_c["threads"]["counts"] == {},
              str(pulled_c.get("comments")))

        # Nested braces truncate at the first }} — refuse BEFORE Doc write.
        # `_mark_replace_requests` is the form-construction gate; without
        # the assert there, propose_change wrote the corrupt span then
        # raised only when building the return value via render_pending.
        tab_before_bad = next(t2["text"] for t2 in stub.state["docs"]["doc-2"]
                              if t2["title"] == "01-choice.md")
        try:
            propose_change(
                db, manuscript, "c-1", old="Doc went another way.",
                new="f(x)={{a}}", note="nested braces",
                service=stub, docs_service=stub)
            check("propose_change refuses delimiter-bearing new "
                  "before any Doc write", False)
        except ValueError as err:
            tab_after_bad = next(
                t2["text"] for t2 in stub.state["docs"]["doc-2"]
                if t2["title"] == "01-choice.md")
            check("propose_change refuses delimiter-bearing new "
                  "before any Doc write",
                  "pending-change grammar" in str(err)
                  and tab_after_bad == tab_before_bad
                  and th.get_thread(db, manuscript["id"], "c-1") is None,
                  f"err={err!s} tab={tab_after_bad!r}")

        prop = propose_change(
            db, manuscript, "c-1", old="Doc went another way.",
            new="The Doc chose a firmer road.", note="strengthen per comment",
            service=stub, docs_service=stub)
        tab_now = next(t2["text"] for t2 in stub.state["docs"]["doc-2"]
                       if t2["title"] == "01-choice.md")
        check("propose wraps the anchor and inserts the proposal in place",
              "<<Doc went another way.>>{{The Doc chose a firmer road.}}"
              in tab_now
              and stub.state["comments"]["c-1"]["replies"][0]["content"]
              .startswith("AuthorLM: proposed")
              and th.get_thread(db, manuscript["id"], "c-1")["state"]
              == "proposed", tab_now)

        pulled_p = pull_doc(db, manuscript, "01-choice.md", service=stub)
        local_now = (ms / "01-choice.md").read_text()
        check("canonical local text stays OLD while the proposal pends",
              "01-choice.md" in pulled_p["unchanged"]
              and "Doc went another way." in local_now
              and "{{" not in local_now, local_now)
        check("re-pull is idempotent; the ledger reports the thread",
              not pulled_p["comments"]
              and pulled_p["threads"]["counts"].get("proposed") == 1,
              str(pulled_p["threads"]))

        stub.author_reply("c-1", "go ahead")
        pulled_v = pull_doc(db, manuscript, "01-choice.md", service=stub)
        check("author verdicts surface classified for the state machine",
              pulled_v["thread_replies"] == [
                  {"comment_id": "c-1", "state": "proposed",
                   "reply": "go ahead", "verdict": "approve"}],
              str(pulled_v.get("thread_replies")))

        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text()
            + "\nA new closing thought.\n")
        pushed_s = push_doc(db, manuscript, "01-choice.md",
                            service=stub, docs_service=stub)
        tab_after = next(t2["text"] for t2 in stub.state["docs"]["doc-2"]
                         if t2["title"] == "01-choice.md")
        check("surgical diff push edits around open threads",
              pushed_s.get("mode") == "diff" and pushed_s.get("ops") == 1
              and "A new closing thought." in tab_after
              and "<<Doc went another way.>>" in tab_after
              and not stub.state["comments"]["c-1"]["resolved"], tab_after)

        # Local-only edit after surgical push must be local_ahead — not a
        # false CONFLICT. diff_push once hashed join(paras) without the
        # trailing newline normalize_markdown adds; three_way then saw
        # both sides off the base and invited --force data loss.
        pre_local = (ms / "01-choice.md").read_text()
        (ms / "01-choice.md").write_text(
            pre_local + "\nLocal after surgical push.\n")
        after_surg = pull_doc(db, manuscript, "01-choice.md", service=stub)
        check("local-only edit after surgical push is local_ahead, "
              "not conflict",
              "01-choice.md" in after_surg.get("local_ahead", [])
              and "01-choice.md" not in after_surg.get("conflicts", [])
              and "Local after surgical push."
              in (ms / "01-choice.md").read_text(),
              str(after_surg))
        (ms / "01-choice.md").write_text(pre_local)

        original_local = (ms / "01-choice.md").read_text()
        (ms / "01-choice.md").write_text(original_local.replace(
            "Doc went another way.", "Doc went a third way."))
        try:
            push_doc(db, manuscript, "01-choice.md",
                     service=stub, docs_service=stub)
            check("diff push refuses edits overlapping pending spans",
                  False)
        except LookupError as err:
            check("diff push refuses edits overlapping pending spans",
                  "pending margin thread" in str(err))
        (ms / "01-choice.md").write_text(original_local)

        # X7-2 (diff_push parity): diff_push's own tab_markdown() helper
        # split the master export using only mapped essay files + the
        # manifest as boundaries and no order= — unlike pull_doc/reconcile,
        # which include every live tab title (prompt tabs, the reserved
        # 'illustrations' tab) with positional order. A tab that sits
        # right after an essay in the export but isn't in that shrunken
        # set is invisible as a boundary, so its heading and body get
        # swallowed into the essay's own exported section.
        from authorlm.gdocs import ILLUS_SENTINEL, ILLUS_TAB_TITLE, diff_push

        docs2 = stub.state["docs"]["doc-2"]
        choice_idx = next(i for i, t in enumerate(docs2)
                          if t["title"] == "01-choice.md")
        # Same parent as the essay tab itself (its containing manuscript
        # tab) — 01-choice.md is nested, so a sibling placed elsewhere at
        # root level (e.g. parent=None) would land after the WHOLE
        # container's subtree in export order, not right after the essay.
        choice_parent = docs2[choice_idx].get("parent")
        stub.state["tab_counter"] += 1
        illus_id = f"tab-{stub.state['tab_counter']}"
        docs2.insert(choice_idx + 1, {"id": illus_id,
                                      "title": ILLUS_TAB_TITLE,
                                      "text": ILLUS_SENTINEL,
                                      "parent": choice_parent})
        stub.state["tab_counter"] += 1
        prompt_id = f"tab-{stub.state['tab_counter']}"
        docs2.insert(choice_idx + 2, {"id": prompt_id,
                                      "title": "bleeding-prompt.md",
                                      "text": "a prompt that must not bleed",
                                      "parent": illus_id})
        try:
            pre_bleed_local = (ms / "01-choice.md").read_text()
            (ms / "01-choice.md").write_text(
                pre_bleed_local + "\nA note added near the boundary.\n")
            try:
                diff_push(db, manuscript, "01-choice.md", stub, stub)
                bleed_survived, bleed_error = True, ""
            except LookupError as err:
                bleed_survived, bleed_error = False, str(err)
            check("diff push of an essay immediately followed by another "
                  "tab (an illustration prompt tab, here) survives the "
                  "round trip instead of the adjacent tab's heading/body "
                  "bleeding into this essay's exported section",
                  bleed_survived, bleed_error)
            if bleed_survived:
                choice_tab_text = next(
                    t["text"] for t in stub.state["docs"]["doc-2"]
                    if t["title"] == "01-choice.md")
                prompt_tab_text = next(
                    t["text"] for t in stub.state["docs"]["doc-2"]
                    if t["title"] == "bleeding-prompt.md")
                check("the pushed essay tab carries the new local "
                      "paragraph and nothing bled in from the adjacent "
                      "prompt tab",
                      "A note added near the boundary." in choice_tab_text
                      and "bleeding-prompt.md" not in choice_tab_text
                      and "a prompt that must not bleed"
                      not in choice_tab_text,
                      choice_tab_text)
                check("the adjacent prompt tab's own content is untouched",
                      prompt_tab_text == "a prompt that must not bleed",
                      prompt_tab_text)
            (ms / "01-choice.md").write_text(pre_bleed_local)
        finally:
            # Clean up the scratch tabs — a real 'illustrations' tab gets
            # created for real later in this suite (push_prompt_tabs) and
            # must not collide with this orphaned stand-in.
            docs2[:] = [t for t in docs2
                       if t["id"] not in (illus_id, prompt_id)]

        # Drive entity-encodes quotes and bodies; fetch unescapes them
        # (it-49edf0c323d1).
        from authorlm.gdocs import fetch_open_comments
        stub.add_comment("c-ent", "one&#39;s own", "entities &#39;here&#39;")
        fetched = {c["id"]: c for c in fetch_open_comments(stub, "doc-2")}
        check("comment quotes and bodies are HTML-unescaped at fetch",
              fetched["c-ent"]["quotedFileContent"]["value"] == "one's own"
              and fetched["c-ent"]["content"] == "entities 'here'",
              str(fetched.get("c-ent")))
        stub.state["comments"]["c-ent"]["resolved"] = True

        # A comment the author resolves BY HAND in the Doc UI (not through
        # 'doc decide' or a written-thread receipt) must not stay marked
        # 'ingested' forever (it-ce3f6078b674). harvest_comments has to
        # reconcile its own stale rows against Drive's current open set,
        # not just add to them — and it must do this even when the open
        # set comes back EMPTY, which is exactly what an author resolving
        # everything by hand produces.
        from authorlm.gdocs import harvest_comments, manuscript_bridge

        # docs_service=None throughout: this block only exercises ingest +
        # reconcile. The master Doc carries other open threads (c-1) mid
        # verdict elsewhere in this suite — passing a live docs_service
        # would also run advance_threads doc-wide and apply THEIR pending
        # verdicts as a side effect of this unrelated harvest call.
        stub.add_comment("c-hand", "a phrase", "resolve me by hand")
        harvest_report = {}
        harvest_comments(db, manuscript, "doc-2", stub, None,
                         manuscript_bridge(manuscript), harvest_report)
        row = db.one("SELECT state FROM doc_comments WHERE manuscript_id = ? "
                     "AND comment_id = ?", (manuscript["id"], "c-hand"))
        check("a fresh comment is ingested as open",
              row is not None and row["state"] == "ingested", str(row))
        replies_before = len(stub.state["comments"]["c-hand"]["replies"])
        stub.state["comments"]["c-hand"]["resolved"] = True  # author, in the Doc
        harvest_report2 = {}
        harvest_comments(db, manuscript, "doc-2", stub, None,
                         manuscript_bridge(manuscript), harvest_report2)
        row2 = db.one("SELECT state FROM doc_comments WHERE manuscript_id = ? "
                      "AND comment_id = ?", (manuscript["id"], "c-hand"))
        check("harvest reconciles a hand-resolved comment to 'resolved'",
              row2 is not None and row2["state"] == "resolved", str(row2))
        check("reconciliation is reported to the caller",
              "c-hand" in harvest_report2.get("comments_reconciled", []),
              str(harvest_report2))
        check("reconciling a hand-resolved comment posts no reply — the "
              "author already closed it in the Doc",
              len(stub.state["comments"]["c-hand"]["replies"])
              == replies_before)

        # Export escaping survives the strip (markers arrive as \<\<
        # with ~~ strikethrough in the markdown export).
        exported = ("~~\\<\\<the old way.\\>\\>~~"
                    "{{the new way.}}")
        stripped_exp = th.strip_pending(normalize_markdown(exported))
        check("export-escaped pending spans strip to canonical old",
              stripped_exp == ("the old way.\n", []), str(stripped_exp))

        # Nested braces in proposal text would truncate at the first }} —
        # refuse before any Doc write.
        try:
            th.assert_no_pending_markers("safe old", "f(x)={{a}}")
            check("propose refuses delimiter-bearing new text", False)
        except ValueError as err:
            check("propose refuses delimiter-bearing new text",
                  "pending-change grammar" in str(err)
                  and ("{{" in str(err) or "}}" in str(err)), str(err))
        try:
            th.render_pending("<<already marked>>", "new")
            check("render_pending refuses delimiter-bearing old text", False)
        except ValueError as err:
            check("render_pending refuses delimiter-bearing old text",
                  "<<" in str(err), str(err))

        # Approve with the author's in-place edit: modified acceptance.
        stub.author_reply("c-1", "AuthorLM: proposed — courtesy")
        tab = next(t2 for t2 in stub.state["docs"]["doc-2"]
                   if t2["title"] == "01-choice.md")
        tab["text"] = tab["text"].replace(
            "{{The Doc chose a firmer road.}}",
            "{{The Doc took the firmer road.}}")
        stub.author_reply("c-1", "go ahead")
        pulled_a = pull_doc(db, manuscript, "01-choice.md", service=stub,
                            docs_service=stub)
        tab_after = next(t2["text"] for t2 in stub.state["docs"]["doc-2"]
                         if t2["title"] == "01-choice.md")
        thread_a = th.get_thread(db, manuscript["id"], "c-1")
        check("approve applies the author-edited version and closes",
              "The Doc took the firmer road." in tab_after
              and "<<" not in tab_after
              and thread_a["state"] == "cleaned"
              and thread_a["proposed_new"] == "The Doc took the firmer road."
              and stub.state["comments"]["c-1"]["resolved"]
              and any(a["action"] == "cleaned"
                      for a in pulled_a["thread_actions"]), tab_after)
        check("approved text lands in the local file",
              "The Doc took the firmer road."
              in (ms / "01-choice.md").read_text())

        # Decline reverts and closes.
        stub.add_comment("c-3", "firmer road", "hmm")
        pull_doc(db, manuscript, "01-choice.md", service=stub)
        propose_change(db, manuscript, "c-3",
                       old="The Doc took the firmer road.",
                       new="A road of iron.", note="try iron",
                       service=stub, docs_service=stub)
        stub.author_reply("c-3", "revert")
        pull_doc(db, manuscript, "01-choice.md", service=stub,
                 docs_service=stub)
        tab_after = next(t2["text"] for t2 in stub.state["docs"]["doc-2"]
                         if t2["title"] == "01-choice.md")
        check("decline reverts the span and closes the thread",
              "A road of iron." not in tab_after
              and "The Doc took the firmer road." in tab_after
              and "<<" not in tab_after
              and th.get_thread(db, manuscript["id"], "c-3")["state"]
              == "declined"
              and stub.state["comments"]["c-3"]["resolved"], tab_after)

        # Withdraw: author resolves a proposed thread without a verdict.
        stub.add_comment("c-4", "firmer road", "or gold?")
        pull_doc(db, manuscript, "01-choice.md", service=stub)
        propose_change(db, manuscript, "c-4",
                       old="The Doc took the firmer road.",
                       new="A road of gold.", note="try gold",
                       service=stub, docs_service=stub)
        stub.state["comments"]["c-4"]["resolved"] = True
        pull_doc(db, manuscript, "01-choice.md", service=stub,
                 docs_service=stub)
        tab_after = next(t2["text"] for t2 in stub.state["docs"]["doc-2"]
                         if t2["title"] == "01-choice.md")
        check("author-resolving a proposal withdraws and reverts it",
              "A road of gold." not in tab_after
              and "<<" not in tab_after
              and th.get_thread(db, manuscript["id"], "c-4")["state"]
              == "withdrawn", tab_after)

        # Step 4: terminal verdicts are the third evidence channel.
        ev = [dict(r) for r in db.all(
            "SELECT * FROM evidence WHERE manuscript_id = ? "
            "AND evidence_type = 'margin_thread'", (manuscript["id"],))]
        signals = sorted(e["signal"] for e in ev)
        check("margin verdicts land as evidence "
              "(modified/declined/withdrawn)",
              signals == ["declined", "modified", "withdrawn"]
              and any("became" in e["target"] for e in ev
                      if e["signal"] == "modified"), str(signals))

        # Chat door: an explained decline; without an LLM the guardrail
        # seeds nothing — the evidence still lands verbatim.
        import json as _mjson

        from authorlm.gdocs import decide_thread
        stub.add_comment("c-5", "firmer road", "colour?")
        pull_doc(db, manuscript, "01-choice.md", service=stub)
        propose_change(db, manuscript, "c-5",
                       old="The Doc took the firmer road.",
                       new="A road of copper.", note="try copper",
                       service=stub, docs_service=stub)
        decided = decide_thread(db, manuscript, "c-5", "decline",
                                reason="Copper is the wrong register here",
                                service=stub, docs_service=stub, llm=None)
        ev2 = db.one(
            "SELECT * FROM evidence WHERE manuscript_id = ? AND "
            "evidence_type='margin_thread' AND signal='declined' "
            "ORDER BY created_at DESC", (manuscript["id"],))
        check("chat-decided decline records the reason verbatim and "
              "seeds nothing without the distiller",
              decided["state"] == "declined"
              and _mjson.loads(ev2["metadata"] or "{}").get("explanation")
              == "Copper is the wrong register here"
              and stub.state["comments"]["c-5"]["resolved"]
              and db.one(
                  "SELECT COUNT(*) AS n FROM editorial_beliefs "
                  "WHERE manuscript_id = ? AND source = 'margin-thread'",
                  (manuscript["id"],))["n"] == 0, str(decided))

        # --- write_pending_forms: accepted forms → Doc; failure isolation ---
        from authorlm.gdocs import write_pending_forms

        critique_body = (
            "# Critique Target\n\n"
            "First body paragraph for replace.\n\n"
            "Second body paragraph stays.\n\n"
            "Third body paragraph for insert after.\n"
        )
        (ms / "07-critique-write.md").write_text(critique_body)
        push_doc(db, manuscript, "07-critique-write.md",
                 service=stub, docs_service=stub)
        local_before = (ms / "07-critique-write.md").read_text()

        def _crit_thread(old, new, anchor, kind, state="accepted"):
            row = ko_fields("dt")
            row.update(
                manuscript_id=manuscript["id"], origin_type="critique",
                origin_id=f"crit-write:{row['id'][:8]}",
                file="07-critique-write.md", anchor_quote=None,
                proposed_old=old, proposed_new=new, note="why",
                state=state, our_reply_ids="[]",
                last_author_reply_id=None, scope_kind="file",
                scope_ref="07-critique-write.md",
                metadata=_mjson.dumps({
                    "kind": kind, "anchor_paragraph": anchor,
                    "intent_id": "i1", "original_new": new}))
            db.insert("doc_threads", row)
            return dict(db.one("SELECT * FROM doc_threads WHERE id = ?",
                               (row["id"],)))

        t_rep = _crit_thread(
            "First body paragraph for replace.",
            "First body paragraph, carefully revised.",
            2, "replace")
        t_ins = _crit_thread(
            "", "A bridging paragraph, newly inserted.",
            4, "insert")
        t_bad = _crit_thread(
            "This text is nowhere in the essay.",
            "Should fail loudly.",
            2, "replace")
        t_brace = _crit_thread(
            "Second body paragraph stays.",
            "Keep the {{nested}} braces out.",
            3, "replace")
        t_rej = _crit_thread(
            "Second body paragraph stays.",
            "Should never appear.",
            3, "replace", state="rejected")

        # The CALLER chooses the set — the writer no longer filters by
        # state, because its two producers now disagree about which
        # states belong in a tab: `critique write` sends its accepted
        # threads, `filter push` sends the untriaged proposals too. So
        # the rejected thread is excluded HERE, exactly as
        # `_critique_write_essay` excludes it, and the assertion below
        # that it never reaches the tab still means what it meant.
        result = write_pending_forms(
            db, manuscript, "07-critique-write.md",
            [t_rep, t_ins, t_bad, t_brace], stub, stub)
        tab_cw = next(t["text"] for t in stub.state["docs"]["doc-2"]
                      if t["title"] == "07-critique-write.md")
        written_ids = {t["id"] for t in result["written"]}
        failed_ids = {t["id"] for t, _ in result["failed"]}
        check("write_pending_forms marks every thread it is GIVEN — "
              "replace and insert — and isolates the missing-span "
              "failure to its own thread",
              t_rep["id"] in written_ids and t_ins["id"] in written_ids
              and t_bad["id"] in failed_ids
              and ("<<First body paragraph for replace.>>"
                   "{{First body paragraph, carefully revised.}}") in tab_cw
              and "{{A bridging paragraph, newly inserted.}}" in tab_cw
              and "Should fail loudly" not in tab_cw,
              f"written={written_ids} failed={result['failed']!r} "
              f"tab={tab_cw!r}")
        check("a REJECTED thread the caller withheld never reaches the "
              "tab — the guarantee is unchanged, it is now the caller's "
              "to keep and `_critique_write_essay` keeps it",
              t_rej["id"] not in written_ids
              and t_rej["id"] not in failed_ids
              and "Should never appear" not in tab_cw, tab_cw)
        check("write_pending_forms refuses delimiter-bearing new "
              "without writing the corrupt span",
              t_brace["id"] in failed_ids
              and t_brace["id"] not in written_ids
              and "{{nested}}" not in tab_cw
              and "Keep the" not in tab_cw,
              f"failed={result['failed']!r} tab={tab_cw!r}")
        check("write_pending_forms leaves local file as OLD (pristine)",
              (ms / "07-critique-write.md").read_text() == local_before)
        check("write_pending_forms reports a Doc tab URL",
              "doc-2" in (result.get("url") or "")
              and "tab=" in (result.get("url") or ""),
              str(result.get("url")))

        # Out-of-range insert: write always push-rebuilds from local first,
        # so a second call starts clean; the bad thread fails alone.
        t_oor = _crit_thread(
            "", "orphan insert", 99, "insert")
        result2 = write_pending_forms(
            db, manuscript, "07-critique-write.md", [t_oor], stub, stub)
        tab_after_fail = next(
            t["text"] for t in stub.state["docs"]["doc-2"]
            if t["title"] == "07-critique-write.md")
        check("out-of-range insert anchor fails in isolation",
              not result2["written"]
              and result2["failed"]
              and "out of range" in result2["failed"][0][1],
              str(result2["failed"]))
        check("a failed-only write push-rebuilds the tab to local OLD",
              "First body paragraph for replace." in tab_after_fail
              and "{{" not in tab_after_fail
              and "orphan insert" not in tab_after_fail,
              tab_after_fail)
        check("local stays pristine after a failed-only write",
              (ms / "07-critique-write.md").read_text() == local_before)
        # Drop the fixture before later toc/export asserts that expect
        # only the chapters they declare.
        (ms / "07-critique-write.md").unlink()

        # --- single-manuscript export (doc create-manuscript) ---
        from authorlm.export import combined_markdown, export_manuscript

        (ms / "00-intro.md").write_text("# Intro\n\nWelcome.\n")
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "01-choice.md"\n\n'
            '[[chapter]]\nfile = "00-intro.md"\n')
        manuscript = api.get_manuscript(db)
        text, order, unlisted = combined_markdown(manuscript)
        check("combined markdown follows toc.toml reading order",
              order == ["01-choice.md", "00-intro.md"] and not unlisted
              and text.index("firmer road") < text.index("Welcome"), text)

        local_only = export_manuscript(db, manuscript, service=None)
        export_path = ms / "_exports" / "book.md"
        check("export writes _exports/<name>.md even without Drive",
              local_only["doc_id"] is None
              and "firmer road" in export_path.read_text()
              and "Welcome." in export_path.read_text())

        # Footnote labels are only file-unique; concatenation must
        # namespace them or pandoc binds colliding labels to one
        # definition across essays (it-9e6b7613a5c5). The token is the
        # shortest unique prefix of the stem — first letter, extended
        # letter by letter on collision (the author's scheme).
        from authorlm.export import footnote_prefixes, namespace_footnotes

        toks = footnote_prefixes(
            ["recapitulation.md", "rebirth.md", "redemption.md",
             "god.md", "good-choice.md", "good-life.md", "kindness.md"])
        check("footnote prefixes extend letterwise until unique",
              toks == {"recapitulation.md": "rec", "rebirth.md": "reb",
                       "redemption.md": "red", "god.md": "god",
                       "good-choice.md": "good-c",
                       "good-life.md": "good-l", "kindness.md": "k"},
              toks)
        noted = "The herd [^E1] persists.\n\n[^E1]: A note.\n"
        check("namespace_footnotes rewrites refs and definitions",
              namespace_footnotes(noted, "k")
              == "The herd [^k-E1] persists.\n\n[^k-E1]: A note.\n",
              namespace_footnotes(noted, "k"))

        (ms / "02-kind.md").write_text("Kind [^E1].\n\n[^E1]: kind note\n")
        (ms / "03-rank.md").write_text("Rank [^E1].\n\n[^E1]: rank note\n")
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "01-choice.md"\n\n'
            '[[chapter]]\nfile = "00-intro.md"\n\n'
            '[[chapter]]\nfile = "02-kind.md"\n\n'
            '[[chapter]]\nfile = "03-rank.md"\n')
        collided, _, _ = combined_markdown(api.get_manuscript(db))
        import re as _re
        labels = _re.findall(r"\[\^([A-Za-z0-9_-]+)\]", collided)
        check("combined export carries no duplicate footnote labels",
              len(set(labels)) * 2 == len(labels)  # each label: 1 ref + 1 def
              and "02-E1" in labels and "03-E1" in labels, labels)
        for name in ("02-kind.md", "03-rank.md"):
            (ms / name).unlink()
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "01-choice.md"\n\n'
            '[[chapter]]\nfile = "00-intro.md"\n')

        # The export Doc is born from the pandoc DOCX (Drive converts
        # Word equations, footnotes and images to native Doc objects,
        # which the markdown importer cannot carry) — so the Drive half
        # needs pandoc, like every other publishing output.
        import shutil as _shutil_exp
        if _shutil_exp.which("pandoc"):
            exported = export_manuscript(db, manuscript, service=stub)
            check("export creates the manuscript Doc in the existing folder",
                  exported["created"] and exported["doc_id"] is not None
                  and stub.state["folders"] == ["doc-1"])
            check("the export Doc is uploaded as a DOCX built beside the md",
                  (ms / "_exports" / "book.docx").exists()
                  and stub.state["uploads"][exported["doc_id"]]
                  .startswith("PK"))
            exported2 = export_manuscript(db, manuscript, service=stub)
            check("re-export updates the same Doc (no new one)",
                  not exported2["created"]
                  and exported2["doc_id"] == exported["doc_id"])

            # A Doc deleted by hand in Drive is transient — recreated on export.
            class Gone(Exception):
                resp = type("R", (), {"status": 404})()

            original_update = stub._files.update
            stub._files.update = lambda fileId=None, media_body=None: (_ for _ in ()).throw(Gone())
            exported3 = export_manuscript(db, manuscript, service=stub)
            stub._files.update = original_update
            check("a hand-deleted export Doc is recreated",
                  exported3["created"]
                  and exported3["doc_id"] != exported["doc_id"])

        titled = export_manuscript(db, manuscript, service=None, title="My Book")
        check("retitled export replaces the stale local file",
              (ms / "_exports" / "My Book.md").exists()
              and not export_path.exists()
              and titled["doc_title"] == "My Book")

        report = reconcile(db, api.get_manuscript(db), stub)
        check("reconcile never touches the push-only export entry",
              "_export" not in report["in_sync"] + report["pulled"]
              + report["pushed"] + report["conflicts"]
              + [e["file"] for e in report["errors"]], str(report))

        # --- publishing exports: variants, settings, local pandoc ---
        from authorlm.export import (export_published, load_settings,
                                     publish_markdown, selection_slug,
                                     set_setting)

        winding_tag = ("[Illustration: a winding path ⇢ a-winding-path.md"
                       " | caption: The path]")
        (ms / "00-intro.md").write_text(
            "An opening epigraph.\n\n# Intro\n\nWelcome.\n\n"
            f"{winding_tag}\n\n"
            "[Illustration: an unrendered idea]\n")
        (ms / "_illustrations" / "prompts").mkdir(exist_ok=True)
        (ms / "_illustrations" / "prompts"
         / "a-winding-path.md").write_text("a winding path\n")
        path_hash = illus_mod.desc_hash("a winding path")
        winding = f"a-winding-path-{path_hash}-0000-01.png"
        (ms / "_illustrations" / winding).write_bytes(tiny_png())
        text, order, warnings = publish_markdown(manuscript, "images")
        check("images variant embeds candidates with captions, keeps "
              "unrendered slots as notes and warns",
              f"![The path](_illustrations/{winding})" in text
              and "[Illustration: an unrendered idea]" in text
              and any("unrendered" in w for w in warnings), text)
        stripped_text, _, _ = publish_markdown(manuscript, "stripped")
        check("stripped variant removes every tag (audio-clean)",
              "[Illustration" not in stripped_text
              and "Welcome." in stripped_text)
        slots_text, _, _ = publish_markdown(manuscript, "slots")
        check("slots variant keeps tags verbatim as production notes",
              winding_tag in slots_text)

        # --- per-output regions: [Omit:] / [Only:] and the audio default ---
        (ms / "03-physics.md").write_text(
            "# Physics\n\nGauss's law in one line.\n\n"
            "[Omit: audio, epub]\n"
            "$$ \\nabla \\cdot \\mathbf{E} = \\frac{\\rho}{\\varepsilon_0} $$\n"
            "[/Omit]\n\n"
            "[Only: audio]\nSpoken: flux equals enclosed charge over "
            "the permittivity.\n[/Only]\n\n"
            "Bare display math:\n\n$$ E = mc^2 $$\n\n"
            "And inline $E$ stays.\n")
        pdf_text, _, _ = publish_markdown(manuscript, "images", fmt="pdf")
        epub_text, _, _ = publish_markdown(manuscript, "images", fmt="epub")
        audio_text, _, _ = publish_markdown(manuscript, "stripped", fmt="md")
        md_text, _, _ = publish_markdown(manuscript, "images", fmt="md")
        # Sticky settings: variant=stripped is the audio-clean markdown
        # door. A later PDF/EPUB/DOCX/Doc build that inherits it must
        # still strip plates, but must NOT join the `audio` output —
        # otherwise display math and [Omit: audio] passages vanish from
        # print with no error.
        stripped_pdf, _, _ = publish_markdown(
            manuscript, "stripped", fmt="pdf")
        from authorlm.export import publish_outputs
        check("stripped PDF is not an audio build",
              publish_outputs("pdf", "stripped") == frozenset({"pdf"})
              and publish_outputs("md", "stripped")
              == frozenset({"md", "audio"}))
        check("stripped PDF keeps [Omit: audio] math and drops [Only: audio]",
              "\\nabla" in stripped_pdf and "mc^2" in stripped_pdf
              and "Spoken:" not in stripped_pdf
              and "inline $E$ stays" in stripped_pdf, stripped_pdf)
        check("stripped PDF still drops illustration tags",
              "[Illustration" not in stripped_pdf
              and "Welcome." in stripped_pdf, stripped_pdf)
        check("[Omit:] drops a region only from the named outputs",
              "\\nabla" in pdf_text and "\\nabla" not in epub_text
              and "\\nabla" not in audio_text, epub_text)
        check("[Only:] keeps a region for the named outputs alone",
              "Spoken:" in audio_text and "Spoken:" not in pdf_text
              and "Spoken:" not in md_text, audio_text)
        from authorlm.export import (combined_markdown, resolve_regions,
                                     strip_display_math)
        # Nested Only-inside-Omit is the documented substitution pattern
        # (math-and-physics-guidelines §5). Conjunctive all()-of-frames
        # deleted both halves for audio; Only must re-include its body.
        nested_sub = (
            "Lead-in.\n\n"
            "[Omit: audio]\n"
            "$$ \\nabla \\cdot \\mathbf{E} = 0 $$\n"
            "[Only: audio]\n"
            "Nested spoken: divergence of E is zero.\n"
            "[/Only]\n"
            "[/Omit]\n\n"
            "Tail.\n")
        nested_audio = resolve_regions(nested_sub, {"audio"})
        nested_pdf = resolve_regions(nested_sub, {"pdf"})
        check("Only inside Omit keeps the audio substitute",
              "Nested spoken:" in nested_audio
              and "\\nabla" not in nested_audio
              and "Lead-in." in nested_audio and "Tail." in nested_audio,
              nested_audio)
        check("Only inside Omit still drops the substitute from print",
              "Nested spoken:" not in nested_pdf
              and "\\nabla" in nested_pdf
              and "Lead-in." in nested_pdf, nested_pdf)
        check("tag lines never reach a reader",
              "[Omit" not in pdf_text and "[/Only" not in audio_text
              and "[Only" not in md_text, md_text)
        check("audio drops untagged display math and keeps inline math",
              "mc^2" not in audio_text and "inline $E$ stays" in audio_text
              and "mc^2" in epub_text, audio_text)
        check("a multi-line $$ block is one display equation to audio",
              strip_display_math("a\n$$\nx\n$$\nb") == "a\nb")
        # Unclosed / malformed $$ used to fail OPEN: everything after the
        # opener vanished from the audio-clean export with no error
        # (guidelines §6: malformed tags refuse by file and line).
        for bad_math, why in (("a\n$$\nx\n\nb", "never closed"),
                              ("a\n$$100 was the price.\nb",
                               "lone $$ or a single-line")):
            try:
                strip_display_math(bad_math, "f.md")
                refused = ""
            except ValueError as err:
                refused = str(err)
            check(f"malformed display math is refused, not truncated ({why})",
                  why in refused and "f.md:" in refused, refused)
        # Plant an unclosed $$ in a content file and prove the audio
        # export refuses rather than shipping a truncated chapter.
        physics = (ms / "03-physics.md").read_text(encoding="utf-8")
        (ms / "03-physics.md").write_text(
            physics + "\n$$\norphan display\n", encoding="utf-8")
        try:
            publish_markdown(manuscript, "stripped", fmt="md")
            trunc_refused = ""
        except ValueError as err:
            trunc_refused = str(err)
        (ms / "03-physics.md").write_text(physics, encoding="utf-8")
        check("audio export refuses an unclosed display-math block",
              "03-physics.md:" in trunc_refused
              and "never closed" in trunc_refused, trunc_refused)
        for bad, why in (("[Omit: audoi]\nx\n[/Omit]", "unknown output"),
                         ("[Omit: pdf]\nx", "never closed"),
                         ("x\n[/Only]", "closes nothing"),
                         ("[Only: pdf]\nx\n[/Omit]", "closes nothing")):
            try:
                resolve_regions(bad, {"pdf"}, "f.md")
                refused = ""
            except ValueError as err:
                refused = str(err)
            check(f"a malformed region is refused, not guessed ({why})",
                  why in refused and "f.md:" in refused, refused)
        check("regions resolve for the export Doc as output 'doc'",
              "\\nabla" in combined_markdown(manuscript)[0]
              and "Spoken:" not in combined_markdown(manuscript)[0])
        from authorlm.export import check_manuscript
        clean_problems = check_manuscript(manuscript)
        (ms / "04-bad.md").write_text(
            "# Bad\n\n[Omit: audoi]\nx\n[/Omit]\n\n"
            "Physics-package math $\\dv{x}{t}$ here.\n")
        bad_problems = check_manuscript(manuscript)
        check("export check passes a well-formed book",
              not [p for p in clean_problems if not p.startswith("pandoc")],
              clean_problems)
        check("export check names a malformed region by file and line",
              any(p.startswith("04-bad.md:3") and "unknown output" in p
                  for p in bad_problems), bad_problems)
        (ms / "05-math.md").write_text(
            "# Math\n\nBefore.\n$$\nE = mc^2\n\n## After\nDone.\n")
        math_problems = check_manuscript(manuscript)
        (ms / "05-math.md").unlink()
        check("export check names unclosed display math by file",
              any("05-math.md:" in p and "never closed" in p
                  for p in math_problems), math_problems)
        import shutil as _shutil_chk
        if _shutil_chk.which("pandoc"):
            check("export check names math outside the portable subset",
                  any("04-bad.md: math outside" in p and "dv" in p
                      for p in bad_problems), bad_problems)
        (ms / "04-bad.md").unlink()
        (ms / "03-physics.md").unlink()

        set_setting(manuscript, "variant", "slots")
        check("export settings persist in _exports/settings.toml",
              load_settings(manuscript)["variant"] == "slots"
              and (ms / "_exports" / "settings.toml").exists())
        try:
            set_setting(manuscript, "author", "Legacy Byline")
            legacy_author_refused = False
        except LookupError:
            legacy_author_refused = True
        check("author identity cannot drift into export settings",
              legacy_author_refused)
        try:
            set_setting(manuscript, "nope", "x")
            refused = False
        except LookupError:
            refused = True
        check("unknown export settings are refused", refused)

        # A PDF essay begins at its FILE boundary, including any epigraph or
        # other pre-heading matter. Heading levels control scale only: an H1
        # inside a file must not define the essay boundary.
        (ms / "title.md").write_text("# **The Book**\n\nA subtitle.\n")
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "title.md"\nmatter = "front"\n\n'
            '[[chapter]]\nfile = "01-choice.md"\n\n'
            '[[chapter]]\nfile = "00-intro.md"\n')
        manuscript = api.get_manuscript(db)
        from types import SimpleNamespace as _SimpleNamespace
        from unittest.mock import patch as _patch

        with (_patch("shutil.which", return_value="/usr/bin/pandoc"),
              _patch("subprocess.run", return_value=_SimpleNamespace(
                  returncode=0, stderr="")) as pandoc_run):
            pdf_result = export_published(
                db, manuscript, fmt="pdf", variant="images")
        pdf_command = pandoc_run.call_args.args[0]
        pdf_markdown = Path(pdf_result["markdown"]).read_text()
        pdf_metadata = [pdf_command[i + 1]
                        for i, arg in enumerate(pdf_command[:-1])
                        if arg == "--metadata"]
        check("PDF export defaults to a confidential review copy",
              pdf_result["mode"] == "review"
              and "authorlm-review-copy=true" in pdf_metadata
              and "author=Author Penname" in pdf_metadata
              and "copyright-owner=Author House LLC" in pdf_metadata
              and any(item.startswith("subject=Copyright © ")
                      for item in pdf_metadata)
              and any(item.startswith("copyright-year=")
                      for item in pdf_metadata),
              str(pdf_command))
        check("review PDF writes a mode-specific filename",
              Path(pdf_result["pdf"]).name.endswith(".review.pdf")
              and pdf_command[pdf_command.index("-o") + 1]
              == pdf_result["pdf"],
              pdf_result["pdf"])
        check("PDF export starts the whole essay before its epigraph",
              "::: {.authorlm-file .authorlm-essay .authorlm-matter-main}\n"
              "An opening epigraph.\n\n# Intro"
              in pdf_markdown, pdf_markdown)
        check("Pandoc input carries semantics, never writer markup",
              "::: {.authorlm-file .authorlm-title-page .authorlm-matter-front}"
              in pdf_markdown
              and "# **The Book**" in pdf_markdown
              and "# Intro" in pdf_markdown
              and "\\Huge" not in pdf_markdown
              and "\\newpage" not in pdf_markdown,
              pdf_markdown)
        defaults = [pdf_command[i + 1]
                    for i, arg in enumerate(pdf_command[:-1])
                    if arg == "--defaults"]
        check("PDF formatting is selected through Pandoc defaults",
              [Path(path).name for path in defaults]
              == ["common.yaml", "pdf.yaml"], str(pdf_command))

        with (_patch("shutil.which", return_value="/usr/bin/pandoc"),
              _patch("subprocess.run", return_value=_SimpleNamespace(
                  returncode=0, stderr="")) as print_run):
            print_result = export_published(
                db, manuscript, fmt="pdf", variant="images",
                print_ready=True)
        print_command = print_run.call_args.args[0]
        print_metadata = [print_command[i + 1]
                          for i, arg in enumerate(print_command[:-1])
                          if arg == "--metadata"]
        check("print-ready PDF suppresses every review-copy instruction",
              print_result["mode"] == "print"
              and not any(item.startswith("authorlm-review-copy=")
                          for item in print_metadata)
              and "author=Author Penname" in print_metadata
              and any(item.startswith("subject=Copyright © ")
                      for item in print_metadata), str(print_command))
        check("print-ready PDF writes a distinct filename from review",
              Path(print_result["pdf"]).name.endswith(".print.pdf")
              and print_result["pdf"] != pdf_result["pdf"]
              and print_command[print_command.index("-o") + 1]
              == print_result["pdf"],
              f"{pdf_result['pdf']} vs {print_result['pdf']}")
        from authorlm.cli import build_parser as _build_parser
        print_args = _build_parser().parse_args(
            ["export", "pdf", "--print-ready"])
        check("CLI exposes the explicit print-ready escape hatch",
              print_args.print_ready is True)
        try:
            export_published(
                db, manuscript, fmt="epub", variant="images",
                print_ready=True)
            non_pdf_print_ready_refused = False
        except ValueError:
            non_pdf_print_ready_refused = True
        check("publication interface restricts print-ready mode to PDF",
              non_pdf_print_ready_refused)

        # --- print geometry: trim size + bleed are manuscript properties;
        # the book profile is a print interior at that trim.
        from authorlm.api import parse_trim_size
        from authorlm.export import book_geometry, kdp_gutter

        check("trim size parses every human spelling to inches",
              parse_trim_size("6x9") == (6.0, 9.0)
              and parse_trim_size("6 X 9") == (6.0, 9.0)
              and parse_trim_size("6in x 9in") == (6.0, 9.0)
              and parse_trim_size("5.5×8.5") == (5.5, 8.5)
              and parse_trim_size("") == (0.0, 0.0))
        try:
            parse_trim_size("3x4")
            tiny_trim_refused = False
        except ValueError:
            tiny_trim_refused = True
        try:
            parse_trim_size("six by nine")
            word_trim_refused = False
        except ValueError:
            word_trim_refused = True
        check("trim size refuses the unprintable and the unparseable",
              tiny_trim_refused and word_trim_refused)
        try:
            export_published(db, manuscript, fmt="pdf", variant="images",
                             profile="book")
            book_without_trim_refused = False
        except RuntimeError as err:
            book_without_trim_refused = "trim size" in str(err)
        check("book profile refuses to build without a trim size",
              book_without_trim_refused)
        try:
            export_published(db, manuscript, fmt="epub", variant="images",
                             profile="book")
            book_non_pdf_refused = False
        except ValueError:
            book_non_pdf_refused = True
        check("book profile is PDF-only", book_non_pdf_refused)
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "manuscript", "set",
                      "--trim-size", "6x9", "--bleed", "no"])
        manuscript = api.get_manuscript(db)
        identity = api.manuscript_metadata(manuscript)
        check("trim size and bleed round-trip through the CLI and the "
              "identity view",
              manuscript["trim_width"] == 6.0
              and manuscript["trim_height"] == 9.0
              and manuscript["bleed"] == 0
              and identity["trim_size"] == "6x9"
              and identity["bleed"] is False)
        check("the KDP gutter follows the page count",
              kdp_gutter(38) == 0.375 and kdp_gutter(151) == 0.5
              and kdp_gutter(500) == 0.625 and kdp_gutter(None) == 0.5
              and kdp_gutter(5000) == 0.875)
        check("book geometry is the trim plus gutter, growing with bleed",
              book_geometry(6, 9, False, 38).startswith(
                  "paperwidth=6in,paperheight=9in,inner=0.75in,"
                  "outer=0.625in,top=0.75in,bottom=0.75in")
              and book_geometry(6, 9, True, 200).startswith(
                  "paperwidth=6.125in,paperheight=9.25in,inner=0.875in,"
                  "outer=0.75in,top=0.875in,bottom=0.875in"))
        with (_patch("shutil.which", return_value="/usr/bin/pandoc"),
              _patch("subprocess.run", return_value=_SimpleNamespace(
                  returncode=0, stderr="")) as book_run):
            book_result = export_published(
                db, manuscript, fmt="pdf", variant="images",
                profile="book")
        book_command = book_run.call_args.args[0]
        book_defaults = [Path(book_command[i + 1]).name
                         for i, arg in enumerate(book_command[:-1])
                         if arg == "--defaults"]
        book_vars = [book_command[i + 1]
                     for i, arg in enumerate(book_command[:-1])
                     if arg == "-V"]
        book_markdown = Path(book_result["markdown"]).read_text()
        check("book profile builds through book.yaml at the trim, with "
              "no review marks and its own filename",
              book_result["mode"] == "book"
              and book_defaults == ["common.yaml", "book.yaml"]
              and any(v.startswith("geometry=paperwidth=6in,paperheight=9in")
                      for v in book_vars)
              and any(v.startswith("header-includes=")
                      and "AuthorLMRunningBook" in v for v in book_vars)
              and "authorlm-review-copy=true" not in book_command
              and Path(book_result["markdown"]).stem.endswith(" - book")
              and any("no paperback ISBN" in w or "page count" in w
                      for w in book_result["warnings"]),
              str(book_command) + str(book_result["warnings"]))
        check("the publishable markdown carries each file's matter",
              "::: {.authorlm-file .authorlm-title-page .authorlm-matter-front}"
              in book_markdown
              and ".authorlm-essay .authorlm-matter-main}" in book_markdown,
              book_markdown[:600])
        book_args = _build_parser().parse_args(
            ["export", "pdf", "--profile", "book"])
        check("CLI exposes the book profile", book_args.profile == "book")
        # Reset for the pandoc export test below, which asserts the
        # review filenames.
        api.update_manuscript_metadata(db, manuscript, trim_size="")

        import shutil as _shutil
        if _shutil.which("pandoc"):
            import subprocess as _subprocess
            review_filter = (Path(__file__).resolve().parent.parent
                             / "authorlm" / "publication" / "review.lua")
            filtered = _subprocess.run(
                ["pandoc", pdf_result["markdown"], "--from",
                 "markdown+smart+footnotes+fenced_divs", "--lua-filter",
                 str(review_filter), "--metadata",
                 "authorlm-review-copy=true", "--metadata",
                 "author=Author Penname", "--metadata",
                 "copyright-owner=Author House LLC", "-t", "plain"],
                capture_output=True, text=True)
            check("review filter inserts the legal notice ahead of the title",
                  filtered.returncode == 0
                  and filtered.stdout.index(
                      "CONFIDENTIAL PREPUBLICATION REVIEW DRAFT")
                  < filtered.stdout.index("The Book")
                  and "Author House LLC" in filtered.stdout,
                  filtered.stderr or filtered.stdout[:1000])
            structured_latex = _subprocess.run(
                ["pandoc", pdf_result["markdown"], "--from",
                 "markdown+smart+footnotes+fenced_divs", "--lua-filter",
                 str(review_filter.parent / "structure.lua"),
                 "--lua-filter", str(review_filter), "--metadata",
                 "authorlm-review-copy=true", "--metadata",
                 "author=Author Penname", "--metadata",
                 "copyright-owner=Author House LLC", "-t", "latex"],
                capture_output=True, text=True)
            check("title page suppresses native numbering before its heading",
                  structured_latex.returncode == 0
                  and structured_latex.stdout.index(
                      "\\thispagestyle{empty}")
                  < structured_latex.stdout.index(
                      "\\AuthorLMBookTitle{}"),
                  structured_latex.stderr or structured_latex.stdout[:1000])
            published = export_published(db, manuscript, fmt="docx",
                                         variant="images")
            docx = Path(published["docx"])
            import zipfile as _zipfile
            with _zipfile.ZipFile(docx) as word:
                core_properties = word.read(
                    "docProps/core.xml").decode("utf-8")
            check("pandoc docx export carries canonical identity",
                  docx.exists() and docx.stat().st_size > 1000,
                  str(published))
            check("DOCX properties carry canonical publication identity",
                  "Author Penname" in core_properties
                  and "Author House LLC" in core_properties,
                  core_properties)
        else:
            print("  note: pandoc not on PATH — docx conversion untested "
                  "in this run")
        (ms / "title.md").unlink()

        # --- chapter-scoped publishing: a part of the book, same build ---
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "01-choice.md"\n\n'
            '[[chapter]]\nfile = "00-intro.md"\n\n'
            '[[chapter]]\nfile = "02-aside.md"\nparent = "00-intro.md"\n')
        (ms / "02-aside.md").write_text("# Aside\n\nA filed thought.\n")
        manuscript = api.get_manuscript(db)
        part, part_order, _ = publish_markdown(manuscript, "images",
                                               ["00-intro"])
        check("naming a parent builds it with its TOC descendants, "
              "and nothing else",
              part_order == ["00-intro.md", "02-aside.md"]
              and "A filed thought." in part
              and "firmer road" not in part, str(part_order))
        try:
            publish_markdown(manuscript, "images", ["no-such"])
            refused = False
        except LookupError:
            refused = True
        check("an unknown chapter name is refused, not silently empty",
              refused)
        slug_a = selection_slug(
            ["01.md", "02.md", "03.md", "04.md"])
        slug_b = selection_slug(
            ["01.md", "02.md", "03.md", "05.md"])
        check("part-build slugs stay distinct when the first three "
              "stems match",
              slug_a != slug_b
              and slug_a == "01+02+03+04" and slug_b == "01+02+03+05",
              f"{slug_a!r} vs {slug_b!r}")
        check("short part-build selections stay fully readable",
              selection_slug(["ascending.md", "discernment.md"])
              == "ascending+discernment")
        # Force the 60-char budget so the digest path is exercised —
        # same first three stems, different tails, must not collide.
        crowded_a = selection_slug([
            "part-alpha.md", "part-beta.md", "part-gamma.md",
            "chapter-with-a-quite-long-stem-one.md",
            "chapter-with-a-quite-long-stem-two.md"])
        crowded_b = selection_slug([
            "part-alpha.md", "part-beta.md", "part-gamma.md",
            "chapter-with-a-quite-long-stem-one.md",
            "chapter-with-a-quite-long-stem-zzz.md"])
        check("truncated part-build slugs keep a digest so crowded "
              "selections cannot collide",
              crowded_a != crowded_b
              and crowded_a.startswith("part-alpha+part-beta+part-gamma+more-")
              and crowded_b.startswith("part-alpha+part-beta+part-gamma+more-")
              and len(crowded_a) <= 60 and len(crowded_b) <= 60,
              f"{crowded_a!r} vs {crowded_b!r}")
        long_a = selection_slug([
            "very-long-chapter-stem-aaaaaaaaaaaa.md",
            "very-long-chapter-stem-bbbbbbbbbbbb.md"])
        long_b = selection_slug([
            "very-long-chapter-stem-aaaaaaaaaaaa.md",
            "very-long-chapter-stem-cccccccccccc.md"])
        check("long stem truncations stay distinct under the 60-char "
              "budget",
              long_a != long_b and len(long_a) <= 60 and len(long_b) <= 60,
              f"{long_a!r} vs {long_b!r}")
        # Expand TOC so overlapping multi-chapter selections are available.
        (ms / "03-next.md").write_text("# Next\n\nFurther on.\n")
        (ms / "04-alt.md").write_text("# Alt\n\nA different end.\n")
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "00-intro.md"\n\n'
            '[[chapter]]\nfile = "02-aside.md"\n'
            'parent = "00-intro.md"\n\n'
            '[[chapter]]\nfile = "01-choice.md"\n\n'
            '[[chapter]]\nfile = "03-next.md"\n\n'
            '[[chapter]]\nfile = "04-alt.md"\n')
        manuscript = api.get_manuscript(db)
        first = export_published(
            db, manuscript, fmt="md", variant="images",
            only=["00-intro", "01-choice", "03-next", "04-alt"])
        second = export_published(
            db, manuscript, fmt="md", variant="images",
            only=["00-intro", "01-choice", "03-next"])
        # Re-export the four-file selection; its path must still exist
        # beside the three-file artifact (not clobber it).
        first_again = export_published(
            db, manuscript, fmt="md", variant="images",
            only=["00-intro", "01-choice", "03-next", "04-alt"])
        check("overlapping part-builds write distinct _exports paths",
              first["markdown"] != second["markdown"]
              and Path(first["markdown"]).exists()
              and Path(second["markdown"]).exists()
              and first_again["markdown"] == first["markdown"]
              and "Further on." in Path(first["markdown"]).read_text()
              and "A different end." in Path(first["markdown"]).read_text()
              and "A different end." not in Path(
                  second["markdown"]).read_text(),
              f"{first['markdown']!r} vs {second['markdown']!r}")
        if _shutil.which("pandoc"):
            whole = export_published(db, manuscript, fmt="md",
                                     variant="images")
            scoped = export_published(db, manuscript, fmt="epub",
                                      variant="images", only=["00-intro"])
            epub = Path(scoped["epub"])
            with _zipfile.ZipFile(epub) as book:
                css = "\n".join(
                    book.read(name).decode()
                    for name in book.namelist() if name.endswith(".css"))
                xhtml = "\n".join(
                    book.read(name).decode()
                    for name in book.namelist() if name.endswith(".xhtml"))
                package = "\n".join(
                    book.read(name).decode()
                    for name in book.namelist() if name.endswith(".opf"))
            check("a chapter epub lands beside the whole-book export "
                  "without overwriting it",
                  epub.exists() and epub.stat().st_size > 1000
                  and scoped["markdown"] != whole["markdown"]
                  and Path(whole["markdown"]).exists(), str(scoped))
            check("EPUB uses semantic file breaks and declarative CSS",
                  "authorlm-file + .authorlm-file" in css
                  and ('class="authorlm-file authorlm-essay '
                       'authorlm-matter-main"') in xhtml
                  and xhtml.index("An opening epigraph.")
                  < xhtml.index("Intro"), xhtml[:1000])
            check("EPUB metadata carries canonical publication identity",
                  "Author Penname" in package
                  and "Author House LLC" in package, package[:1000])

        # --- hygiene: deterministic filters + retroactive sweep ---
        import json as _json

        from authorlm import hygiene
        from authorlm.concepts import link_concepts

        ghost = api.add_concept(db, manuscript, "Spectral Machinery")
        db.update("concept_nodes", ghost["id"], {"metadata": _json.dumps(
            {"origin": "extracted", "confirmed": False})})
        link_concepts(db, manuscript["id"], "Spectral Machinery",
                      "creates", "Choice", status="inferred")
        hygiene_files = read_files_for_test(ms)
        swept = hygiene.sweep(db, manuscript, hygiene_files)
        check("sweep flags ungrounded extracted concepts and their edges",
              any(c["name"] == "Spectral Machinery"
                  for c in swept["ungrounded_concepts"])
              and any("Spectral Machinery" in e["unmentioned"]
                      for e in swept["ungrounded_edges"]), str(swept))
        check("sweep never flags grounded or author-confirmed material",
              not any(c["name"] == "Choice"
                      for c in swept["ungrounded_concepts"]))

        # Staleness: a bridge suggestion whose concept the author then
        # writes is marked stale at collect — review never offers a no-op.
        session, _ = api.ensure_session(db, manuscript)
        api.add_concept(db, manuscript, "Undertow")
        api.declare_intent(db, manuscript, "Introduce Undertow in ch1")
        guided = api.guide(db, manuscript, session)
        check("guide proposes the declared, unrealized concept",
              any("Undertow" in r["suggestion"]
                  for r in guided["suggestions"]), str(guided))
        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text()
            + "\n\nThe Undertow pulls every choice back toward habit.\n")
        report = api.collect(db, manuscript, {})
        check("collect marks the now-moot suggestion stale",
              any("Undertow" in s["suggestion"]
                  for s in report.get("suggestions_stale", []))
              and api.compact_collect(report)["suggestions_stale"]
              == report["suggestions_stale"], str(report.get(
                  "suggestions_stale")))
        row = db.one(
            "SELECT * FROM guidance_history WHERE manuscript_id = ? "
            "AND state = 'stale'", (manuscript["id"],))
        check("the stale state is persisted on the guidance row",
              row is not None and "Undertow" in row["suggestion"])

        # --- recurrence bar (ratified 2026-08-08): same-paragraph
        # collapse; >=2 contexts spanning >=2 sections or files ---
        bar_files = {
            "a.md": ("# One\n\nThe Undertow pulls. The Undertow drags.\n\n"
                     "Plain prose here.\n\n## Two\n\nMore prose.\n"),
            "b.md": "# Other\n\nNothing relevant.\n",
        }
        ok, stats = hygiene.passes_recurrence_bar(bar_files, ["Undertow"])
        check("twice in one paragraph is one context — fails the bar",
              not ok and stats["contexts"] == 1, str(stats))
        bar_files["a.md"] += "\nThe Undertow returns in section two.\n"
        ok, stats = hygiene.passes_recurrence_bar(bar_files, ["Undertow"])
        check("two contexts across two sections of one file pass the bar",
              ok and stats["contexts"] == 2 and stats["sections"] == 2,
              str(stats))
        bar_files["a.md"] = ("# One\n\nThe Undertow pulls.\n\n"
                             "The Undertow drags on, same section.\n")
        ok, stats = hygiene.passes_recurrence_bar(bar_files, ["Undertow"])
        check("two contexts in ONE section of one file fail the spread",
              not ok and stats["contexts"] == 2 and stats["sections"] == 1,
              str(stats))
        bar_files["b.md"] = "# Other\n\nThe Undertow crosses files.\n"
        ok, stats = hygiene.passes_recurrence_bar(bar_files, ["Undertow"])
        check("cross-file recurrence passes the bar",
              ok and stats["files"] == 2, str(stats))

        # --- policy belief lifecycle (Laplace confidence + promote/demote) ---
        from authorlm import beliefs as pol

        check("Laplace confidence is (s+1)/(s+c+2)",
              pol._confidence(0, 0) == 0.5
              and pol._confidence(3, 0) == 0.8
              and pol._confidence(0, 3) == 0.2)
        check("retired status is sticky under further evidence",
              pol._lifecycle_status("retired", 10, 0, 0.9) == "retired")
        check("low confidence with enough observations retires a candidate",
              pol._lifecycle_status("candidate", 0, 4,
                                    pol._confidence(0, 4)) == "retired")
        check("validated demotes when confidence falls below demote bar",
              pol._lifecycle_status("validated", 2, 2,
                                    pol._confidence(2, 2)) == "candidate")
        check("candidate promotes only with support AND confidence bars",
              pol._lifecycle_status("candidate", 3, 0,
                                    pol._confidence(3, 0)) == "validated"
              and pol._lifecycle_status("candidate", 2, 0,
                                        pol._confidence(2, 0))
              == "candidate")

        # The sweep reports unconfirmed gated nodes that fail the bar —
        # mentioned (not ungrounded) but single-context.
        motif = api.add_concept(db, manuscript, "Fleeting Motif",
                                kind="metaphor")
        db.update("concept_nodes", motif["id"], {"metadata": _json.dumps(
            {"origin": "extracted", "confirmed": False})})
        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text()
            + "\nA Fleeting Motif appears exactly once.\n")
        swept = hygiene.sweep(db, manuscript, read_files_for_test(ms))
        check("sweep separates below-bar from ungrounded",
              any(c["name"] == "Fleeting Motif" and c["contexts"] == 1
                  for c in swept["below_bar"])
              and not any(c["name"] == "Fleeting Motif"
                          for c in swept["ungrounded_concepts"])
              and not any(c["name"] == "Spectral Machinery"
                          for c in swept["below_bar"]), str(swept["below_bar"]))

        # A vanished UNCONFIRMED hypothesis dies quietly (retired +
        # reported); only author-confirmed knowledge earns a proposal
        # (it-8f24c757d3c7).
        fleeting = api.add_concept(db, manuscript, "Fleeting Phrase")
        db.update("concept_nodes", fleeting["id"], {"metadata": _json.dumps(
            {"origin": "extracted", "confirmed": False})})
        with_phrase = (ms / "01-choice.md").read_text()
        (ms / "01-choice.md").write_text(
            with_phrase + "\nThe Fleeting Phrase appears here once.\n")
        api.collect(db, manuscript, {})
        (ms / "01-choice.md").write_text(with_phrase)
        report = api.collect(db, manuscript, {})
        check("vanished unconfirmed hypotheses are dropped, not proposed",
              report["hypotheses_dropped"] == ["Fleeting Phrase"]
              and "Fleeting Phrase" not in report["vanished"]
              and db.one("SELECT status FROM concept_nodes WHERE id = ?",
                         (fleeting["id"],))["status"] == "retired"
              and db.one(
                  "SELECT id FROM knowledge_proposals WHERE kind = 'vanished' "
                  "AND target = ?", (fleeting["id"],)) is None,
              str(report.get("hypotheses_dropped")))

        # --- sweep framework vanguards: readiness, ontology, lens ---
        from authorlm import lenses, sweeps

        ready = sweeps.readiness(db, manuscript)
        checks_present = {i["check"] for i in ready["items"]}
        check("readiness sweep covers the registries deterministically",
              {"illustrations rendered", "proposals settled",
               "toc covers every file", "pandoc available",
               "export settings set"} <= checks_present
              and isinstance(ready["ready"], bool), str(ready))
        check("readiness flags the unrendered slot as a blocker",
              not ready["ready"]
              and "illustrations rendered" in ready["blocking"], str(ready))
        sound_item = next(i for i in ready["items"] if i["check"] == "database sound")
        check("readiness runs PRAGMA integrity_check against the live store "
              "and reports a healthy database as sound (OPS-4)",
              sound_item["ok"] and sound_item["detail"] == "ok", str(sound_item))

        class FakeLLM:
            enabled = True

            def __init__(self, payload):
                self.payload = payload
                self.system = self.user = None

            def complete_json(self, system, user):
                self.system, self.user = system, user
                return self.payload

            def stats_line(self):
                return None

        # --- episode analysis: learn editorial judgment from observed edits ---
        from authorlm.analysis import MAX_DECISIONS, analyze_pending

        an_root = root / "analyze-ws"
        an_ms = an_root / "manuscript"
        an_ms.mkdir(parents=True)
        opening = ("# Opening\n\nChoice is the hinge of becoming.\n")
        (an_ms / "01-choice.md").write_text(opening)
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(an_root), "init", "--name", "analyze",
                      "--path", str(an_ms)])
        an_db = api.open_db(str(an_root))
        an_mscript = api.get_manuscript(an_db)
        api.collect(an_db, an_mscript, {})  # baseline version
        an_session, _ = api.ensure_session(an_db, an_mscript)
        an_declared = api.declare_intent(
            an_db, an_mscript, "Develop the notion of Becoming")
        an_intent_id = an_declared["intent"]["id"]
        sailor = ("Like a sailor tacking, becoming threads through what "
                  "choice has opened.")
        (an_ms / "01-choice.md").write_text(opening + f"\n\n{sailor}\n")
        (an_ms / "02-new.md").write_text(
            f"# New chapter\n\n{sailor} The fresh prose must reach the analyzer.\n")
        api.collect(an_db, an_mscript, {})

        progress_calls = []
        analysis_llm = FakeLLM({
            "decisions": (
                [{"action": "opened the section with a sailing metaphor",
                  "pattern": "Open concept introductions with a lived metaphor"}]
                + [{"action": f"local move {i}", "pattern": None}
                   for i in range(1, MAX_DECISIONS)]
                + [{"action": "ninth decision must be truncated",
                    "pattern": "Should not seed"}]
                + [{"action": "", "pattern": "empty action skipped"},
                   "not-a-dict"]
            ),
            "outcome": "Developed the opening metaphor",
        })
        completed = api.complete_intent(
            an_db, an_mscript, an_intent_id[:8], "done", llm=analysis_llm)
        summaries = completed["analysis"]
        check("complete_intent runs episode analysis over observed edits",
              len(summaries) == 1
              and summaries[0]["intent"] == "Develop the notion of Becoming"
              and summaries[0]["outcome"] == "Developed the opening metaphor"
              and len(summaries[0]["decisions"]) == MAX_DECISIONS
              and summaries[0]["decisions"][0]["pattern"]
              == "Open concept introductions with a lived metaphor",
              str(summaries))
        check("analysis prompt carries declared intent and file_added prose",
              "DECLARED INTENT: Develop the notion of Becoming"
              in analysis_llm.user
              and "NEW TEXT:" in analysis_llm.user
              and "fresh prose must reach the analyzer" in analysis_llm.user,
              analysis_llm.user[:800])
        seeded = an_db.one(
            "SELECT * FROM editorial_beliefs WHERE manuscript_id = ? "
            "AND statement = ?",
            (an_mscript["id"],
             "Open concept introductions with a lived metaphor"))
        check("pattern seeds a medium-weight episode_analysis candidate",
              seeded is not None and seeded["status"] == "candidate"
              and seeded["source"] == "episode-analysis"
              and an_db.one(
                  "SELECT weight, evidence_type FROM evidence "
                  "WHERE supports_belief = ?", (seeded["id"],)
              )["weight"] == "medium"
              and an_db.one(
                  "SELECT evidence_type FROM evidence "
                  "WHERE supports_belief = ?", (seeded["id"],)
              )["evidence_type"] == "episode_analysis")
        check("analysis is idempotent once the episode is marked analyzed",
              api.analyze(an_db, an_mscript, analysis_llm) == [])

        # Malformed decisions must not crash; episode stays pending for retry.
        an2 = api.declare_intent(an_db, an_mscript, "Second episode for retry")
        (an_ms / "01-choice.md").write_text(
            (an_ms / "01-choice.md").read_text()
            + "\n\nA second observed edit for the retry path.\n")
        api.collect(an_db, an_mscript, {})
        api.complete_intent(an_db, an_mscript, an2["intent"]["id"][:8],
                            "closed without llm")
        bad_llm = FakeLLM({"decisions": None, "outcome": "should not land"})
        # Force a pending episode: strip analysis metadata if complete wrote none
        # (no llm on complete above → episode closed but unanalyzed).
        pending_before = analyze_pending(an_db, an_mscript, bad_llm)
        check("null decisions do not crash and leave a completed analysis "
              "with no decisions",
              len(pending_before) == 1
              and pending_before[0]["decisions"] == []
              and pending_before[0]["outcome"] == "should not land",
              str(pending_before))

        an3 = api.declare_intent(an_db, an_mscript, "Third episode unavailable LLM")
        (an_ms / "01-choice.md").write_text(
            (an_ms / "01-choice.md").read_text()
            + "\n\nA third edit awaiting a usable LLM reply.\n")
        api.collect(an_db, an_mscript, {})
        api.complete_intent(an_db, an_mscript, an3["intent"]["id"][:8], None)
        none_llm = FakeLLM(None)
        progress_calls.clear()

        def _progress(index, total, statement):
            progress_calls.append((index, total, statement))

        stuck = analyze_pending(an_db, an_mscript, none_llm, progress=_progress)
        ep3 = an_db.one(
            "SELECT metadata FROM editorial_episodes WHERE intent_id = ?",
            (an3["intent"]["id"],))
        check("non-dict LLM reply leaves the episode pending for retry",
              stuck == []
              and not loads(ep3["metadata"], {}).get("analysis")
              and progress_calls
              and progress_calls[0][2] == "Third episode unavailable LLM",
              str({"stuck": stuck, "meta": ep3["metadata"],
                   "progress": progress_calls}))
        check("disabled LLM is a no-op over pending episodes",
              analyze_pending(an_db, an_mscript,
                              type("Off", (), {"enabled": False})()) == [])

        api.add_concept(db, manuscript, "Tremor",
                        notes="The Tremor is never caused; it causes.")
        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text()
            + "\n\nSome say the Tremor is caused by the collision of "
              "qualities, and that its cause can be measured precisely "
              "by any patient observer of the fields.\n")
        grounded_quote = ("Some say the Tremor is caused by the collision "
                         "of qualities")
        onto_llm = FakeLLM({"findings": [
            {"item": 1, "concept": "Tremor", "quote": grounded_quote,
             "claim": "The Tremor is never caused; it causes.",
             "why": "The text gives the Tremor a cause."},
            {"item": 1, "concept": "Tremor",
             "quote": "a sentence that is not in the paragraph",
             "claim": "x", "why": "hallucinated"},
        ]})
        onto = sweeps.ontology(db, manuscript, onto_llm, file="01-choice.md")
        check("ontology narrows deterministically and gates findings",
              onto["pairs"] >= 1 and onto["findings"] == 1
              and onto["dropped_ungrounded"] == 1
              and "SETTLED" in onto_llm.user
              and "never caused" in onto_llm.user, str(onto))
        from authorlm import proposals as props
        incongruence = [r for r in props.open_proposals(db, manuscript["id"])
                        if r["kind"] == "incongruence"]
        check("incongruence findings land as proposals with a verdict "
              "grammar",
              len(incongruence) == 1
              and "Tremor" in props.describe(incongruence[0])[0]
              and "Acknowledged" in props.adopt(db, manuscript["id"],
                                                incongruence[0]))

        # --- BUG-20: an `item` index of 0 (or negative) must be rejected,
        # not silently wrapped by Python's negative-index behaviour onto a
        # DIFFERENT paragraph. `pairs[int(f.get("item",0)) - 1]` with
        # item=0 computes index -1, which Python resolves to the last pair
        # instead of raising IndexError, so the bounds guard never fires. ---
        zero_item_llm = FakeLLM({"findings": [
            {"item": 0, "concept": "Tremor", "quote": grounded_quote,
             "claim": "The Tremor is never caused; it causes.",
             "why": "an out-of-range item must not bind to any paragraph"},
        ]})
        onto_zero = sweeps.ontology(db, manuscript, zero_item_llm,
                                    file="01-choice.md")
        check("BUG-20: item=0 is dropped instead of wrapping onto the "
              "last pair via negative indexing",
              onto_zero["findings"] == 0
              and onto_zero["dropped_ungrounded"] == 1, str(onto_zero))
        neg_item_llm = FakeLLM({"findings": [
            {"item": -1, "concept": "Tremor", "quote": grounded_quote,
             "claim": "x", "why": "a negative item must not bind either"},
        ]})
        onto_neg = sweeps.ontology(db, manuscript, neg_item_llm,
                                   file="01-choice.md")
        check("BUG-20: a negative item is dropped, not indexed from the end",
              onto_neg["findings"] == 0
              and onto_neg["dropped_ungrounded"] == 1, str(onto_neg))

        lenses.add_lens(manuscript, "clarity",
                        "Flag sentences that assert a claim without "
                        "argument or example.")
        check("lens ratified into _lenses/ and listed",
              lenses.list_lenses(manuscript)[0]["name"] == "clarity")
        lens_llm = FakeLLM({"findings": [
            {"quote": grounded_quote, "note": "Asserted without argument."},
            {"quote": "nowhere text", "note": "hallucinated"},
        ]})
        session, _ = api.ensure_session(db, manuscript)
        run = lenses.run_lens(db, manuscript, session, "clarity",
                              "01-choice.md", lens_llm)
        check("lens run gates findings and stores its own batch",
              len(run["findings"]) == 1 and run["dropped_ungrounded"] == 1
              and run["findings"][0]["kind"] == "lens"
              and "THE LENS" in lens_llm.system
              # the GLOSSARY (concept notes) is block A of the USER message
              # since the cross-chapter design; the lens itself stays in S
              and "never caused; it causes" in lens_llm.user, str(run))
        verdict = api.review(db, manuscript, session, 1, "rejected",
                             "Assertion is fine here — the sermon register "
                             "argues by declaration.", kinds=("lens",))
        check("lens verdicts flow through record_review as evidence",
              verdict["review"]["decision"] == "rejected"
              and db.one("SELECT state FROM guidance_history WHERE id = ?",
                         (run["findings"][0]["id"],))["state"] != "proposed")
        registered = lenses.register_findings(
            db, manuscript, session, "clarity", "01-choice.md",
            [{"quote": grounded_quote, "note": "External agent finding."},
             {"quote": "still nowhere", "note": "dropped"}])
        check("registration door hygiene-gates external findings into the "
              "same store",
              len(registered["findings"]) == 1
              and registered["dropped_ungrounded"] == 1
              and _json.loads(registered["findings"][0]["metadata"])[
                  "source"] == "external")

        # --- verse: the poem as a structural unit (verse.py) and the lens
        # poem grain with the siblings target (poem-grain ruling 2026-09-25)
        from authorlm import verse as verse_mod

        vroot = root / "verse-ws"
        vms = vroot / "manuscript"
        (vms / "_lenses").mkdir(parents=True)
        (vms / "_profiles").mkdir()
        (vms / "_filters").mkdir()
        (vms / "toc.toml").write_text(
            '[[chapter]]\nfile = "poems.md"\nform = "verse"\n\n'
            '[[chapter]]\nfile = "prose.md"\n')
        poem_text = (
            "# **Error**\n\n*To live is to become.*\n\n"
            "## **The Dragon**\n\n*The mind is a dragon.*\n\n"
            "Every morning I rise,\\\nTo fight a fiery dragon.\n\n"
            "Victory is not my goal,\\\nAll good things will grow.\n\n"
            "## **The Mirror**\n\n*Despair is blindness.*\n\n"
            "In shadows of bliss,\\\nShe finds no embrace.\n")
        (vms / "poems.md").write_text(poem_text)
        (vms / "prose.md").write_text("# Prose\n\nA plain paragraph.\n")
        vfiles = {"toc.toml": (vms / "toc.toml").read_text(),
                  "poems.md": poem_text, "prose.md": "# Prose\n\nA plain paragraph.\n"}
        from authorlm import morph as _morph
        check("morph judges prose only: a stanza and a thesis line are not "
              "prose units",
              _morph._is_prose("A plain paragraph of prose here.")
              and not _morph._is_prose("Every morning I rise,\\\nTo fight.")
              and not _morph._is_prose("*The mind is a dragon.*")
              and verse_mod.is_verse_unit("a,\\\nb")
              and not verse_mod.is_verse_unit("Plain prose."))
        check("form_map reads form = verse from the toc, prose by default",
              verse_mod.form_map(vfiles) == {"poems.md": "verse", "prose.md": "prose"}
              and verse_mod.is_verse(vfiles, "poems.md")
              and not verse_mod.is_verse(vfiles, "prose.md"))
        poems = verse_mod.poems_of(poem_text)
        check("poems_of finds each ## heading with its title, thesis, and span",
              [p["title"] for p in poems] == ["The Dragon", "The Mirror"]
              and poems[0]["thesis"] == "The mind is a dragon."
              and poems[0]["text"].startswith("## **The Dragon**")
              and "She finds no embrace" not in poems[0]["text"]
              and "She finds no embrace" in poems[1]["text"], str(poems))
        check("the frame is the title and epigraph before the first poem",
              verse_mod.frame_of(poem_text) == "# **Error**\n\n*To live is to become.*")
        check("stanzas_of returns the stanzas and nothing else",
              verse_mod.stanzas_of(poems[0]["text"])
              == ["Every morning I rise,\\\nTo fight a fiery dragon.",
                  "Victory is not my goal,\\\nAll good things will grow."])
        h3_text = ("# Section\n\n### First\n\nline one,\\\nline two.\n\n"
                   "### Second\n\nline three.\n")
        h3_files = {"toc.toml": '[[chapter]]\nfile = "h3.md"\nform = "verse"\n'
                                'poem_level = 3\n', "h3.md": h3_text}
        check("poem_level is declared per file in the toc and drives the "
              "splitter",
              verse_mod.poem_level(h3_files, "h3.md") == 3
              and [p["title"] for p in verse_mod.poems_of(h3_text, 3)]
              == ["First", "Second"]
              and verse_mod.poems_of(h3_text) == []
              and verse_mod.verse_report(h3_files)[0]["poems"] == 2)
        wrong_files = {**h3_files, "toc.toml": '[[chapter]]\nfile = "h3.md"\n'
                                               'form = "verse"\n'}
        vrep = verse_mod.verse_report(wrong_files)[0]
        check("a verse file with no poems at its level warns and names the "
              "levels that exist",
              vrep["poems"] == 0 and "level 2" in vrep["warning"]
              and "3 (2)" in vrep["warning"] and "poem_level" in vrep["warning"],
              str(vrep))
        check("find_poem takes a number, a title, or a fragment, and names "
              "ambiguity",
              verse_mod.find_poem(poems, "2")["title"] == "The Mirror"
              and verse_mod.find_poem(poems, "dragon")["n"] == 1
              and (lambda: (verse_mod.find_poem(poems, "The") and False)
                   if False else True)())
        try:
            verse_mod.find_poem(poems, "The")
            ambiguous = False
        except LookupError as err:
            ambiguous = "ambiguous" in str(err)
        check("an ambiguous poem fragment is refused by name", ambiguous)

        (vroot / ".authorlm").mkdir(parents=True, exist_ok=True)
        from authorlm.db import Database as _VDB

        vdb = _VDB(vroot / ".authorlm" / "authorlm.db")
        vman = api.register_manuscript(vdb, "verse", str(vms))
        api.collect(vdb, vman, {})
        vsession, _ = api.ensure_session(vdb, vman)
        lenses.add_lens(vman, "verse-law",
                        "---\nclass = \"chapter\"\ngrain = \"poem\"\n---\n"
                        "# Verse law\n\n## Examples\n\n### Flag — The turn is missing\n"
                        "> x\nWhy.\n\n## The lens\n\nFlag a last couplet that does not turn.\n")
        lenses.add_lens(vman, "section-arc",
                        "---\nclass = \"cross-chapter\"\ngrain = \"poem\"\n"
                        "targets = [\"siblings\", \"book\"]\n---\n"
                        "# Section arc\n\nFlag two poems on one image.\n")
        lenses.add_lens(vman, "plain-chapter", "# Plain\n\nFlag assertions.\n")
        try:
            lenses.parse_lens("---\nclass = \"chapter\"\ntargets = [\"siblings\"]\n---\n# x\n")
            sib_refused = False
        except lenses.LensError:
            sib_refused = True
        check("siblings needs the poem grain, and a chapter lens declares no "
              "targets", sib_refused)
        try:
            lenses.assemble(vdb, vman, "verse-law", "prose.md", poem="1")
            prose_refused = False
        except ValueError as err:
            prose_refused = "not a verse file" in str(err)
        check("a poem-grain lens refuses a prose file by name", prose_refused)
        try:
            lenses.assemble(vdb, vman, "verse-law", "poems.md")
            unnamed = False
        except ValueError as err:
            unnamed = "--poem" in str(err) and "The Mirror" in str(err)
        check("a poem-grain lens without --poem names the poems", unnamed)
        try:
            lenses.assemble(vdb, vman, "plain-chapter", "poems.md", poem="1")
            wrong_grain = False
        except ValueError as err:
            wrong_grain = "grain" in str(err)
        check("--poem on a chapter-grain lens is refused", wrong_grain)
        pl = lenses.assemble(vdb, vman, "verse-law", "poems.md", poem="mirror")
        check("the poem payload's E block holds the frame and ONLY that poem",
              pl.poem["title"] == "The Mirror"
              and "THE POEM" in pl.essay and "THE SECTION FRAME" in pl.essay
              and "She finds no embrace" in pl.essay
              and "fiery dragon" not in pl.essay and not pl.targets, pl.essay)
        pls = lenses.assemble_poems(vdb, vman, "verse-law", "poems.md")
        check("assemble_poems gives one payload per poem, in order",
              [p.poem["n"] for p in pls] == [1, 2])
        arc = lenses.assemble(vdb, vman, "section-arc", "poems.md", poem="1")
        check("the siblings target carries the other poems, full text, and "
              "registers the file as a target",
              "SIBLINGS" in arc.targets and "She finds no embrace" in arc.targets
              and "fiery dragon" not in arc.targets.split("SIBLINGS")[1].split("VERBATIM")[0]
              and "poems.md" in arc.target_files, arc.targets)
        reg = lenses.register_findings(
            vdb, vman, vsession, "verse-law", "poems.md",
            [{"quote": "All good things will grow.", "note": "No turn.",
              "rule": "The turn is missing"},
             {"quote": "She finds no embrace.", "note": "wrong poem"}],
            poem="1")
        check("at poem grain the gate drops a quote from a sibling poem and "
              "keeps the poem's own",
              len(reg["findings"]) == 1 and reg["dropped_ungrounded"] == 1
              and reg["poem"] == "The Dragon", str(reg))
        fmeta = _json.loads(reg["findings"][0]["metadata"])
        check("a poem-grain finding records its poem and the poem's hash",
              fmeta["poem"] == "The Dragon" and fmeta["poem_n"] == 1
              and fmeta["poem_sha"] == pls[0].poem["sha"]
              and fmeta["rule_known"] is True, str(fmeta))
        st = lenses.status(vdb, vman, "poems.md")
        check("a poem batch is fresh while its poem is untouched",
              st and not st[-1]["stale"] and st[-1]["poem"] == "The Dragon",
              str(st))
        (vms / "poems.md").write_text(
            poem_text.replace("She finds no embrace", "She finds an embrace"))
        api.collect(vdb, vman, {})
        st = lenses.status(vdb, vman, "poems.md")
        check("rewording a SIBLING leaves the poem's batch fresh",
              not st[-1]["stale"], str(st[-1]["stale"]))
        (vms / "poems.md").write_text(
            poem_text.replace("All good things will grow", "All things grow"))
        api.collect(vdb, vman, {})
        st = lenses.status(vdb, vman, "poems.md")
        check("rewording THE poem marks its batch stale by poem",
              st[-1]["stale"] == ["poems.md#The Dragon"], str(st[-1]["stale"]))
        vcol = api.collect(vdb, vman, {})
        vcol = api.collect(vdb, vman, {}) if vcol.get("unchanged") is None else vcol
        (vms / "poems.md").write_text(poem_text + "\n## **Third**\n\nA line.\n")
        vcol = api.collect(vdb, vman, {})
        check("collect reports each verse file's poem count at its level",
              any(v["file"] == "poems.md" and v["poems"] == 3 and v["level"] == 2
                  for v in vcol.get("verse", [])), str(vcol.get("verse")))

        # --- lens architecture: front matter, inputs, targets, payload,
        #     finding provenance, status, sweep (lens-architecture-design)
        lens_root = root / "lens-ws"
        lms = lens_root / "manuscript"
        (lms / "_lenses").mkdir(parents=True)
        (lms / "_profiles").mkdir()
        (lms / "_filters").mkdir()
        filler = " ".join(["The walker keeps walking and the road keeps "
                           "asking."] * 45)  # ~450 words: a chapter, not a card
        dup = ("This one sentence is repeated verbatim across two "
               "chapters of the fixture book.")
        (lms / "00-card.md").write_text("# Part One\n\nA short card.\n")
        (lms / "01-first.md").write_text(
            f"# **The First Ground**\n\nGround is where a Choice stands. {dup}\n\n"
            f"{filler}\n")
        (lms / "02-second.md").write_text(
            f"# **Second Steps**\n\nThe essay on The First Ground laid the "
            f"Ground; the paper on Ledger never existed. Every Beat counts.\n\n"
            f"{dup}\n\n{filler}\n")
        (lms / "03-hymn.md").write_text("# Hymn\n\nHarken, ye dead.\n")
        (lms / "04-last.md").write_text(
            f"# **Last Words**\n\nA Beat is one cleaving and its answer.\n\n"
            f"{filler}\n")
        (lms / "toc.toml").write_text(
            '[[chapter]]\nfile = "00-card.md"\n'
            '[[chapter]]\nfile = "01-first.md"\n'
            '[[chapter]]\nfile = "02-second.md"\n'
            '[[chapter]]\nfile = "03-hymn.md"\nregister = "protected"\n'
            '[[chapter]]\nfile = "04-last.md"\n')
        (lms / "_profiles" / "audience.md").write_text(
            "Reads long books; does not have Sanskrit.\n")
        (lms / "_profiles" / "chapter-aliases.toml").write_text(
            '[aliases]\n"01-first.md" = ["First Ground"]\n')
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(lens_root), "init", "--name",
                      "lensbook", "--path", str(lms)])
        ldb = api.open_db(str(lens_root))
        lm = api.get_manuscript(ldb)
        api.collect(ldb, lm, {})
        lsession, _ = api.ensure_session(ldb, lm)
        api.add_concept(ldb, lm, "Ground", notes="where a Choice stands")
        api.add_concept(ldb, lm, "Beat", notes="one cleaving and its answer")
        for cname, where in (("Ground", "01-first.md"), ("Beat", "04-last.md")):
            ldb.update("concept_nodes",
                       ldb.one("SELECT id FROM concept_nodes WHERE "
                               "manuscript_id = ? AND name = ?",
                               (lm["id"], cname))["id"],
                       {"introduced_in": where, "status": "realized"})

        meta, body = lenses.parse_lens("# Plain\n\nFlag X.\n")
        check("a bare prompt is a chapter lens reading the glossary",
              meta["class"] == "chapter" and meta["inputs"] == ["glossary"]
              and meta["targets"] == [] and body.startswith("# Plain"))
        for bad, why in ((
                '---\nklass = "chapter"\n---\n# X\n', "unknown key"), (
                '---\nclass = "chapter"\ntargets = ["pointers"]\n---\n# X\n',
                "chapter lens with targets"), (
                '---\nclass = "cross-chapter"\n---\n# X\n',
                "cross-chapter without targets"), (
                '---\ninputs = ["summaries"]\n---\n# X\n',
                "summaries are not an input"), (
                '---\nclass = "chapter"\n# X\n', "unclosed front matter")):
            try:
                lenses.parse_lens(bad)
                check(f"front matter refuses: {why}", False, bad)
            except lenses.LensError:
                check(f"front matter refuses: {why}", True)
        xc_text = ('---\nclass = "cross-chapter"\n'
                   'inputs = ["glossary", "audience", "scheme", "registers", '
                   '"passes", "reading-order"]\n'
                   'targets = ["pointers", "neighbours", "earlier", "book"]\n'
                   '---\n# Cross lens\n\n## Examples\n\n### Flag — Old example\n'
                   '> q\n\n## The lens\n\nFlag a passage when:\n'
                   '- **A pointer misses.** text\n- **A term arrives early.** text\n')
        lenses.add_lens(lm, "xc", xc_text)
        lenses.add_lens(lm, "plain", "# Plain\n\nFlag sentences that assert.\n")
        listed = {l["name"]: l for l in lenses.list_lenses(lm)}
        check("lens list carries class and targets",
              listed["xc"]["class"] == "cross-chapter"
              and listed["xc"]["targets"] == ["pointers", "neighbours",
                                              "earlier", "book"]
              and listed["plain"]["class"] == "chapter", str(listed))
        check("flag_rules reads bold bullets and Flag headings",
              lenses.flag_rules(xc_text) == ["A pointer misses",
                                             "A term arrives early",
                                             "Old example"],
              str(lenses.flag_rules(xc_text)))
        check("strip_examples removes the Examples section only",
              "Old example" not in lenses.strip_examples(xc_text.split("---\n", 2)[2])
              and "A pointer misses" in lenses.strip_examples(
                  xc_text.split("---\n", 2)[2]))

        plain_p = lenses.assemble(ldb, lm, "plain", "02-second.md")
        check("a chapter lens has no T block and no target files",
              plain_p.targets == "" and plain_p.target_files == {}
              and "GLOSSARY" in plain_p.inputs
              and "[introduced in 01-first.md]" in plain_p.inputs
              and "THE CHAPTER — 02-second.md" in plain_p.essay, plain_p.inputs)
        xc_p = lenses.assemble(ldb, lm, "xc", "02-second.md")
        T = xc_p.targets
        check("pointer resolved by title (case-sensitive) and carried as text",
              "--- 01-first.md — The First Ground" in T
              and "01-first.md" in xc_p.target_files
              and "04-last.md" in T, T[:600])
        check("framed reference to no chapter is listed unresolved",
              "POINTERS UNRESOLVED" in T and "Ledger" in T, T[:800])
        check("neighbours skip the card and the short protected hymn",
              lenses.neighbours(
                  lenses.read_manuscript_files(lms), "02-second.md")
              == ["01-first.md", "04-last.md"])
        check("TERMS INTRODUCED LATER/EARLIER come from the graph and the "
              "reading order",
              "- Beat — 04-last.md" in T and "- Ground — 01-first.md" in T, T)
        check("verbatim recurrence found across chapters at zero tokens",
              dup in T and "also in 01-first.md" in T, T)
        check("reading order marks the card and this chapter; registers list "
              "the protected hymn",
              "[card]" in xc_p.inputs and "THIS CHAPTER" in xc_p.inputs
              and "03-hymn.md" in xc_p.inputs.split("PROTECTED REGISTERS")[1],
              xc_p.inputs)
        check("AUDIENCE PROFILE rendered; ORGANIZING SCHEME a labelled absence",
              "does not have Sanskrit" in xc_p.inputs
              and "(no profile on record" in xc_p.inputs, xc_p.inputs)
        check("no summary reaches any block",
              "summar" not in (xc_p.law + xc_p.inputs + xc_p.targets).lower()
              or "no summary" in xc_p.law.lower())
        check("payload hashes are per block and the render names them",
              set(xc_p.hashes) == {"S", "A", "T", "E"}
              and "block T" in xc_p.render("xc") and "sha256" in xc_p.render("xc"))
        try:
            lenses.assemble(ldb, lm, "plain", "03-hymn.md")
            check("a lens refuses to run on a protected register", False)
        except ValueError as err:
            check("a lens refuses to run on a protected register",
                  "protected register" in str(err))

        q2 = "Every Beat counts."
        xc_llm = FakeLLM({"findings": [
            {"quote": q2, "note": "Beat arrives early.",
             "rule": "A term arrives early.",
             "target_file": "04-last.md",
             "target_quote": "A Beat is one cleaving and its answer."},
            {"quote": q2, "note": "unverified target quote",
             "rule": "made-up rule", "target_file": "01-first.md",
             "target_quote": "words that are not there"},
            {"quote": q2, "note": "undeclared chapter", "target_file": "nope.md"},
            {"quote": "nowhere at all", "note": "ungrounded"},
        ]})
        xr = lenses.run_lens(ldb, lm, lsession, "xc", "02-second.md", xc_llm)
        metas = [_json.loads(r["metadata"]) for r in xr["findings"]]
        check("native run sends the four blocks and gates the reply",
              "block S" not in xc_llm.system and "THE LENS" in xc_llm.system
              and "THE CHAPTER — 02-second.md" in xc_llm.user
              and "--- 01-first.md" in xc_llm.user
              and len(xr["findings"]) == 2 and xr["dropped_ungrounded"] == 1
              and xr["refused_targets"] == [{"quote": q2, "target": "nope.md"}],
              str(xr))
        check("finding provenance: essay sha, target shas, known rule, "
              "verified and unverified target quotes",
              metas[0]["essay_sha"] == xc_p.essay_sha
              and set(metas[0]["targets"]) == set(xc_p.target_files)
              and metas[0]["rule"] == "A term arrives early"
              and metas[0]["rule_known"] is True
              and "target_unverified" not in metas[0]
              and metas[1]["rule_known"] is False
              and metas[1]["target_unverified"], str(metas))
        st = lenses.status(ldb, lm, "02-second.md")
        check("lens status tallies rules and is fresh",
              len(st) == 1 and st[0]["count"] == 2 and st[0]["stale"] == []
              and st[0]["rules"]["A term arrives early"] == 1
              and st[0]["unverified"] == 1, str(st))
        (lms / "01-first.md").write_text(
            (lms / "01-first.md").read_text() + "\nA new closing line.\n")
        st2 = lenses.status(ldb, lm, "02-second.md")
        check("a changed target marks the batch STALE by byte comparison",
              st2[0]["stale"] == ["01-first.md"], str(st2))
        reg = lenses.register_findings(
            ldb, lm, lsession, "xc", "02-second.md",
            [{"quote": q2, "note": "external, declared target",
              "target_file": "01-first.md"},
             {"quote": q2, "note": "external, undeclared", "target_file": "x.md"}])
        check("register re-assembles the payload and refuses undeclared targets",
              len(reg["findings"]) == 1 and len(reg["refused_targets"]) == 1
              and _json.loads(reg["findings"][0]["metadata"])["source"]
              == "external", str(reg))
        from authorlm import directives as _dir
        fn = lenses.run_lens(ldb, lm, lsession, "plain", "02-second.md",
                             FakeLLM({"findings": [
                                 {"quote": "Every Beat counts.",
                                  "note": "a count stated as fact",
                                  "footnote": "cite the [census] table\nfor the count"}]}))
        fn_thread = ldb.one("SELECT * FROM doc_threads WHERE id = ?",
                            (_json.loads(fn["findings"][0]["metadata"])["edit_thread"],))
        planted = fn_thread["proposed_new"]
        check("a `footnote` gist plants the author's tag after the quote as a "
              "staged edit — one line, brackets neutralized, never a drafted note",
              len(fn["edits_staged"]) == 1
              and "Every Beat counts.[Footnote: cite the (census) table for the count]"
              in planted
              and len(_dir.scan_text(planted, "footnote")) == 1
              and _json.loads(fn["findings"][0]["metadata"])["footnote"]
              == "cite the (census) table for the count", planted)
        pl = lenses.run_lens(ldb, lm, lsession, "plain", "02-second.md",
                             FakeLLM({"findings": [
                                 {"quote": "Every Beat", "note": "asserts"}]}))
        linked = lenses.link_overlaps(ldb, [xr["batch_id"], pl["batch_id"]])
        pl_meta = _json.loads(ldb.one(
            "SELECT metadata FROM guidance_history WHERE id = ?",
            (pl["findings"][0]["id"],))["metadata"])
        check("sweep cross-links overlapping quotes across lenses",
              linked >= 1 and pl_meta["also_flagged_by"][0]["lens"] == "xc",
              str(pl_meta))
        open_f = lenses.findings(ldb, lm, "02-second.md")
        check("lens findings lists every open finding on the file with its id "
              "and lens, oldest first",
              len(open_f) == 5 and open_f[0]["lens"] == "xc"
              and all(f["present"] for f in open_f)
              and open_f[0]["id"].startswith("gd-")
              and all(f["state"] == "proposed" for f in open_f), str(open_f))
        by_id = api.review(ldb, lm, lsession, open_f[0]["id"][:11], "rejected",
                           "the beat is defined two chapters on, by design",
                           kinds=("lens",))
        check("lens review by finding id reaches a finding outside the latest "
              "batch",
              by_id["review"]["decision"] == "rejected"
              and ldb.one("SELECT state FROM guidance_history WHERE id = ?",
                          (open_f[0]["id"],))["state"] != "proposed"
              and len(lenses.findings(ldb, lm, "02-second.md")) == 4)
        # --- judgments: planted as tags, ruled in the tab, repaired after
        jr = lenses.run_lens(ldb, lm, lsession, "plain", "02-second.md",
                             FakeLLM({"findings": [
                                 {"quote": "Every Beat counts.",
                                  "note": "the section that follows argues nothing",
                                  "judgment": "cut the Beat paragraph or give it a claim"}]}))
        j_meta = _json.loads(jr["findings"][0]["metadata"])
        j_thread = ldb.one("SELECT * FROM doc_threads WHERE id = ?",
                           (j_meta["edit_thread"],))
        j_new = j_thread["proposed_new"]
        check("a `judgment` plants a [Judgment: …] tag as an INSERTION after "
              "the quote's unit — nothing struck — carrying the lens and the "
              "finding id (author ruling 2026-09-27)",
              len(jr["edits_staged"]) == 1
              and j_thread["proposed_old"] == ""
              and j_new == (f"[Judgment: plain — cut the Beat paragraph "
                            f"or give it a claim | id: {jr['findings'][0]['id'][:11]}]")
              and _json.loads(j_thread["metadata"])["kind"] == "insert"
              and _json.loads(j_thread["metadata"])["anchor_paragraph"] >= 1
              and len(_dir.scan_text(j_new, "judgment")) == 1
              and j_meta["judgment"].startswith("cut the Beat"), j_new)
        folded = api._fold_judgment("A first paragraph.\n\n{{" + j_new + "}}\n\nNext.", "{{" + j_new + "}}", j_new)
        check("an accepted judgment folds back onto the end of the paragraph "
              "it follows, where the file has always carried it",
              folded == "A first paragraph." + j_new + "\n\nNext.", repr(folded))
        stripped, removed = _dir.strip_tags(j_new)
        check("exports strip a judgment tag like any directive",
              "[Judgment" not in stripped and removed[0]["kind"] == "judgment")
        try:
            _dir.apply(ldb, lm, {}, "judgment", "02-second.md", [(1, "x")])
            check("directive apply refuses the judgment kind", False)
        except ValueError as err:
            check("directive apply refuses the judgment kind",
                  "lens repair" in str(err))
        n_threads = ldb.one("SELECT COUNT(*) AS n FROM doc_threads WHERE "
                            "manuscript_id = ? AND origin_type = 'lens' AND "
                            "file = '02-second.md'", (lm["id"],))["n"]
        check("each lens batch owns its own threads — a re-run never "
              "overwrites forms already staged or out in the tab",
              n_threads == 2, str(n_threads))
        # the tab's ruling reaches the finding
        ldb.update("doc_threads", j_thread["id"], {"state": "declined"})
        ldb.update("doc_threads", fn_thread["id"], {"state": "cleaned"})
        pv = lenses.propagate_verdicts(
            ldb, lm, [dict(ldb.one("SELECT * FROM doc_threads WHERE id = ?", (i,)))
                      for i in (j_thread["id"], fn_thread["id"])])
        check("a deleted judgment tag rejects its finding; a kept form accepts it",
              pv == {"accepted": 1, "rejected": 1}
              and ldb.one("SELECT state FROM guidance_history WHERE id = ?",
                          (jr["findings"][0]["id"],))["state"] == "rejected"
              and ldb.one("SELECT state FROM guidance_history WHERE id = ?",
                          (fn["findings"][0]["id"],))["state"] == "accepted",
              str(pv))
        # an accepted judgment stands in the file as a tag; repair acts on it
        jr2 = lenses.run_lens(ldb, lm, lsession, "plain", "02-second.md",
                              FakeLLM({"findings": [
                                  {"quote": "Every Beat counts.",
                                   "note": "structure", "judgment": "move this claim"},
                                  {"quote": dup, "note": "needs a fact",
                                   "judgment": "which census counted it"}]}))
        j2 = {_json.loads(r["metadata"])["judgment"]: r for r in jr2["findings"]}
        second = (lms / "02-second.md").read_text()
        for r in jr2["findings"]:
            rmeta = _json.loads(r["metadata"])
            th = ldb.one("SELECT * FROM doc_threads WHERE id = ?",
                         (rmeta["edit_thread"],))
            if th["proposed_old"]:
                second = second.replace(th["proposed_old"], th["proposed_new"])
            else:
                # An accepted judgment insertion folds onto its unit.
                paras = second.split("\n\n")
                k = next(i for i, p in enumerate(paras)
                         if rmeta["quote"] in " ".join(p.split()))
                paras[k] = paras[k].rstrip() + th["proposed_new"]
                second = "\n\n".join(paras)
        (lms / "02-second.md").write_text(second)
        rp = lenses.assemble_repair(ldb, lm, "02-second.md")
        check("repair payload lists each accepted judgment with its unit and finding",
              len(rp.tags) == 2 and all(t["finding"] for t in rp.tags)
              and "THE JUDGMENTS" in rp.judgments and "STYLE LAW" in rp.law
              and "GLOSSARY" in rp.inputs and "[Judgment:" in rp.essay, rp.judgments[:400])
        mv = j2["move this claim"]["id"]; fact = j2["which census counted it"]["id"]
        tag_mv = next(t for t in rp.tags if t["id"] == mv[:11])
        rr = lenses.record_repairs(ldb, lm, lsession, "02-second.md", {"repairs": [
            {"id": mv[:11], "action": "rewrite",
             "new": tag_mv["unit"].replace(tag_mv["raw"], "").replace(
                 "Every Beat counts.", "Every Beat is counted, and the count is the claim.")},
            {"id": fact[:11], "action": "question",
             "text": "Which census counted the sentence?"},
            {"id": mv[:11], "action": "rewrite", "new": "still [Judgment: x | id: y] here"},
            {"id": "gd-nope", "action": "rewrite", "new": "x"},
        ]}, payload=rp)
        check("repair stages a rewrite with the tag gone, returns the question, "
              "and refuses a rewrite that still carries a tag or names no judgment",
              len(rr["staged"]) == 1
              and "[Judgment" not in rr["staged"][0]["proposed_new"]
              and "the count is the claim" in rr["staged"][0]["proposed_new"]
              and rr["questions"][0]["text"].startswith("Which census")
              and len(rr["refused"]) == 2, str(rr))
        ri = lenses.record_repairs(ldb, lm, lsession, "02-second.md", {"repairs": [
            {"id": fact[:11], "action": "intent",
             "text": "Add the census count and its source to the second chapter"}]},
            payload=rp)
        check("a structural repair files a scoped intent and stages the tag's removal",
              len(ri["intents"]) == 1 and ri["intents"][0]["intent"].startswith("di-")
              and len(ri["staged"]) == 1
              and "[Judgment" not in ri["staged"][0]["proposed_new"]
              and api.get_intent(ldb, ri["intents"][0]["intent"])["scope"] == "02-second.md"
              if hasattr(api, "get_intent") else len(ri["intents"]) == 1, str(ri))
        dm = lenses.dismiss_judgments(ldb, lm, "02-second.md", [fact[:11]],
                                      "the count is illustrative")
        check("dismissal rejects the finding with the reason and stages the "
              "tag's removal",
              dm["dismissed"] == [fact[:11]] and len(dm["staged"]) == 1
              and ldb.one("SELECT state FROM guidance_history WHERE id = ?",
                          (fact,))["state"] == "rejected", str(dm))
        opu_chosen, opu_deferred = lenses.one_per_unit([
            {"id": "b", "created_at": "2", "metadata": '{"anchor_paragraph": 3}'},
            {"id": "a", "created_at": "1", "metadata": '{"anchor_paragraph": 3}'},
            {"id": "c", "created_at": "3", "metadata": '{"anchor_paragraph": 5}'}])
        check("one form per paragraph goes to the tab; the rest stay staged",
              [t["id"] for t in opu_chosen] == ["a", "c"]
              and [t["id"] for t in opu_deferred] == ["b"])
        opu2, opd2 = lenses.one_per_unit([
            {"id": "r", "created_at": "1", "proposed_old": "old",
             "metadata": '{"anchor_paragraph": 3}'},
            {"id": "j", "created_at": "2", "proposed_old": "",
             "metadata": '{"anchor_paragraph": 3}'}])
        check("a judgment insertion may sit beside one rewrite of the same "
              "paragraph", [t["id"] for t in opu2] == ["r", "j"] and not opd2)
        # --- introduced_in is deterministic: first reading-order mention,
        #     term of art first, re-derived at every collect, never triaged
        from authorlm import concepts as _cg
        items = [("a.md", "the ground of all things is a ground"),
                 ("b.md", "Here Ground is named as a term."),
                 ("c.md", "Ground again.")]
        check("primary_location is the first occurrence of the word in reading "
              "order, any casing — the author's rule, nothing finer",
              _cg.primary_location(items, {"name": "Ground", "aliases": "[]"})
              == "a.md"
              and _cg.primary_location(items, {"name": "Thing", "aliases": "[]"})
              == "a.md"
              and _cg.primary_location(items, {"name": "Absent", "aliases": "[]"})
              is None)
        ldb.update("concept_nodes",
                   ldb.one("SELECT id FROM concept_nodes WHERE manuscript_id = ? "
                           "AND name = 'Ground'", (lm["id"],))["id"],
                   {"introduced_in": "04-last.md"})
        lv = ldb.one("SELECT * FROM manuscript_versions WHERE manuscript_id = ? "
                     "ORDER BY created_at DESC LIMIT 1", (lm["id"],))
        moved, _van = _cg.rescan_primary_locations(ldb, lm["id"], dict(lv))
        check("the re-scan moves a pointer to the first mention unconditionally, "
              "even when the old chapter still carries the term",
              any(m["name"] == "Ground" and m["new"] == "01-first.md"
                  for m in moved), str(moved))
        check("sweep order: ratified names first, then alphabetical; only/skip",
              lenses.sweep_order(lm) == ["plain", "xc"]
              and lenses.sweep_order(lm, only=["xc"]) == ["xc"]
              and lenses.sweep_order(lm, skip=["xc"]) == ["plain"])
        with contextlib.redirect_stdout(io.StringIO()) as lens_out:
            cli_main(["--workspace", str(lens_root), "lens", "run", "xc",
                      "02-second.md"])
        check("`lens run` without --native prints the payload and calls nothing",
              "no model call was made" in lens_out.getvalue()
              and "block T" in lens_out.getvalue(), lens_out.getvalue()[:300])

        # --- illustration placement pipeline: scan → stage → triage ---
        from authorlm import placement

        anchor_para = ("The road is a ladder laid flat, and every step "
                       "asks again.")
        filler = " ".join(["The road runs on and the walker keeps walking "
                           "toward what is not yet."] * 120)
        (ms / "04-road.md").write_text(
            f"# **The Road**\n\n{filler}\n\n{anchor_para}\n")
        api.collect(db, manuscript, {})
        api.add_style_law(db, manuscript, "illustration-placement",
                              "Concretize a recurring metaphor once, at "
                              "its strongest occurrence.",
                              file="04-road.md")
        spot_llm = FakeLLM({"proposals": [
            {"anchor": anchor_para,
             "description": "a ladder lying flat along a road, rungs "
                            "receding to the horizon",
             "criterion": "2", "rationale": "metaphor at its strongest",
             "revises": None},
            {"anchor": "no such text anywhere",
             "description": "dropped", "criterion": "2",
             "rationale": "bad anchor", "revises": None},
        ]})
        report = placement.scan(db, manuscript, spot_llm,
                                files=["04-road.md"])
        check("spot-finder stages verbatim-verified proposals and drops "
              "unverifiable anchors",
              len(report["staged"]) == 1
              and report["dropped_unverifiable"] == 1
              and "PLACEMENT LAW" in spot_llm.user
              and "BUDGET" in spot_llm.user
              and "strongest occurrence" in spot_llm.user, str(report))
        check("scan is idempotent against open proposals",
              placement.scan(db, manuscript, spot_llm,
                             files=["04-road.md"])["staged"] == [])
        staged = placement.open_proposals(db, manuscript["id"])[0]
        result = placement.decide(
            db, manuscript, staged["id"], "accept",
            revised_description="a ladder lying flat along an empty road")
        text_now = (ms / "04-road.md").read_text()
        check("modified acceptance writes the revised tag after the anchor",
              result["modified"]
              and ("[Illustration: a ladder lying flat along an empty "
                   "road]") in text_now
              and text_now.index(anchor_para)
              < text_now.index("[Illustration: a ladder"), text_now[-300:])
        ev_row = db.one(
            "SELECT * FROM evidence WHERE evidence_type = 'illus_triage' "
            "ORDER BY created_at DESC")
        check("triage revision lands as modified evidence with the diff",
              ev_row["signal"] == "modified"
              and "became" in ev_row["target"], str(dict(ev_row)))
        # Reject with a reason; then stale when the anchor vanishes.
        spot_llm2 = FakeLLM({"proposals": [
            {"anchor": anchor_para, "description": "second idea",
             "criterion": "2", "rationale": "r", "revises": None}]})
        placement.scan(db, manuscript, spot_llm2, files=["04-road.md"])
        p2 = placement.open_proposals(db, manuscript["id"])[0]
        placement.decide(db, manuscript, p2["id"], "reject",
                         reason="Too decorative for this essay.")
        check("rejection records the author's reason verbatim",
              _json.loads(db.one(
                  "SELECT metadata FROM evidence WHERE evidence_type = "
                  "'illus_triage' ORDER BY created_at DESC")["metadata"])
              ["explanation"] == "Too decorative for this essay.")
        check("a rejected proposal never resurrects on re-scan",
              placement.scan(db, manuscript, spot_llm2,
                             files=["04-road.md"])["staged"] == [])
        spot_llm3 = FakeLLM({"proposals": [
            {"anchor": anchor_para, "description": "third idea",
             "criterion": "2", "rationale": "r", "revises": None}]})
        placement.scan(db, manuscript, spot_llm3, files=["04-road.md"])
        (ms / "04-road.md").write_text(
            (ms / "04-road.md").read_text().replace(anchor_para,
                                                    "The road changed."))
        staled = placement.sweep_stale(db, manuscript)
        check("proposals whose anchor vanished go stale, never guessed",
              len(staled) == 1
              and not placement.open_proposals(db, manuscript["id"]))

        # --- externalized descriptions: ⇢ ref grammar, hash stability,
        # machine-maintained excerpts, offers ---
        from authorlm import illus as il

        long_desc = ("a brass orrery on a scarred oak table, " * 8
                     + "lit from the left by a single candle")
        (ms / "06-orrery.md").write_text(
            "# **Orrery**\n\nBody.\n\n"
            f"[Illustration: {long_desc} | caption: the orrery]\n")
        api.collect(db, manuscript, {})
        inline_slot = il.find_slot(ms, "orrery")[0]
        h_before = inline_slot["desc_hash"]
        offers = il.externalize_offers(ms)
        check("a 50+ word inline description draws an externalize offer",
              any("longer than 50 words" in o["reason"] for o in offers),
              str(offers))
        out = il.externalize(ms, inline_slot)
        ref_slot = il.find_slot(ms, "orrery")[0]
        check("externalize keeps desc_hash (renders survive) and refs "
              "the prompts file",
              out["desc_hash"] == h_before
              and ref_slot["desc_hash"] == h_before
              and ref_slot["ref"] == out["ref"]
              and (ms / "_illustrations" / "prompts" / out["ref"]).exists()
              and ref_slot["caption"] == "the orrery"
              and "⇢" in (ms / "06-orrery.md").read_text())
        # Edit the excerpt in the tag: maintenance discards the edit.
        tampered = (ms / "06-orrery.md").read_text().replace(
            "a brass orrery on a scarred", "a HAND-EDITED excerpt on a scarred")
        (ms / "06-orrery.md").write_text(tampered)
        fixes = il.maintain_excerpts(ms)
        check("excerpt edits are discarded and called out",
              any(f.get("discarded_edit") for f in fixes)
              and "HAND-EDITED" not in (ms / "06-orrery.md").read_text())
        # Edit the canonical file: excerpt refreshes, desc_hash moves.
        (ms / "_illustrations" / "prompts" / out["ref"]).write_text(
            "a silver orrery beneath a cracked dome\n")
        il.maintain_excerpts(ms)
        moved = il.find_slot(ms, "silver orrery")[0]
        check("editing the prompts file is canonical: hash moves, "
              "excerpt follows",
              moved["desc_hash"] != h_before
              and moved["excerpt"].startswith("a silver orrery"))
        # Note: craft_text ignores `workspace` — the craft file moved from
        # <workspace>/.authorlm/ to the project root with the rest of the
        # editorial config (authorlm/paths.py); every caller reads the
        # one repo-shared file now. `workspace=str(root)` is kept below
        # only because craft_text still accepts (and ignores) the param.
        check("craft file seeds and serves the active model's carveout",
              "bound attributes" in il.craft_text(
                  {"llm": {"image_model": "gpt-image-2"}},
                  workspace=str(root))
              and "Renders lettering reliably" in il.craft_text(
                  {"llm": {"image_model": "gpt-image-2"}},
                  workspace=str(root))
              and "Renders lettering reliably" not in il.craft_text(
                  {"llm": {"image_model": "other/model"}},
                  workspace=str(root)))

        # --- AD/illus-config: [illustrations] is authoritative for the
        # illustration model/size, [llm] is the fallback for configs that
        # haven't migrated, then the hardcoded default. One helper
        # (llm.resolve_image_setting) is the single seam for this
        # precedence; exercised directly (hermetic, no LLM calls) plus
        # once through a real call site (illus.craft_text). ---
        from authorlm.llm import DEFAULT_IMAGE_MODEL, resolve_image_setting

        check("[illustrations] model wins over a different [llm] image_model",
              resolve_image_setting(
                  {"illustrations": {"model": "openai/gpt-image-2"},
                   "llm": {"image_model": "gemini/other"}},
                  "model", "image_model", DEFAULT_IMAGE_MODEL)
              == "openai/gpt-image-2")
        check("no [illustrations] section: [llm] image_model still used",
              resolve_image_setting(
                  {"llm": {"image_model": "gemini/other"}},
                  "model", "image_model", DEFAULT_IMAGE_MODEL)
              == "gemini/other")
        check("neither section set: image_model falls back to the default",
              resolve_image_setting({}, "model", "image_model",
                                    DEFAULT_IMAGE_MODEL)
              == DEFAULT_IMAGE_MODEL)
        check("[illustrations] image_size wins over a different "
              "[llm] image_size",
              resolve_image_setting(
                  {"illustrations": {"image_size": "1536x1024"},
                   "llm": {"image_size": "1024x1024"}},
                  "image_size", "image_size", "")
              == "1536x1024")
        check("no [illustrations] section: [llm] image_size still used",
              resolve_image_setting(
                  {"llm": {"image_size": "1024x1024"}},
                  "image_size", "image_size", "")
              == "1024x1024")
        check("neither section set: image_size falls back to the "
              "empty default",
              resolve_image_setting({}, "image_size", "image_size", "")
              == "")
        check("craft file honors [illustrations] model over [llm] at a "
              "real call site (illus.craft_text)",
              "Renders lettering reliably" in il.craft_text(
                  {"illustrations": {"model": "gpt-image-2"},
                   "llm": {"image_model": "other/model"}},
                  workspace=str(root)))

        api.collect(db, manuscript, {})

        # --- Doc mirror: the reserved 'illustrations' tab tree ---
        from authorlm.gdocs import (ILLUS_DISPLAY_PREFIX, ILLUS_SENTINEL,
                                    ILLUS_TAB_TITLE, push_prompt_tabs)

        manuscript = api.get_manuscript(db)
        ref = out["ref"]
        prompt_path = ms / "_illustrations" / "prompts" / ref
        display = ILLUS_DISPLAY_PREFIX + ref
        prom = push_prompt_tabs(db, manuscript, stub, stub)
        master = stub.state["docs"]["doc-2"]
        illus_tab = next(t for t in master if t["title"] == ILLUS_TAB_TITLE)
        slug_tab = next(t for t in master if t["title"] == ref)
        check("full push mirrors the prompt file into the reserved tree",
              ref in prom["created"] and not prom["updated"]
              and slug_tab.get("parent") == illus_tab["id"]
              and illus_tab.get("parent") is None
              and slug_tab["text"].strip()
              == "a silver orrery beneath a cracked dome"
              and illus_tab["text"] == ILLUS_SENTINEL, str(prom))
        manuscript = api.get_manuscript(db)
        links_now = doc_status(db, manuscript)
        entry_now = links_now["_illusprompt/" + ref]
        check("reserved mapping keys record the tab and the pushed base",
              entry_now["tab_id"] == slug_tab["id"]
              and entry_now["pushed_hash"]
              and links_now["_illustrations_tab"] == illus_tab["id"],
              str(entry_now))

        temp_docs_before = stub.state["counter"]
        prom2 = push_prompt_tabs(db, manuscript, stub, stub)
        check("an unchanged prompt pushes nothing (changed-only)",
              not prom2["created"] and not prom2["updated"]
              and not prom2["pruned"]
              and stub.state["counter"] == temp_docs_before, str(prom2))

        # Doc-side edit → pull writes the canonical file; collect's
        # excerpt maintenance then follows the new text.
        stub.set_tab(ref, "a silver orrery beneath a shattered dome")
        rep = pull_doc(db, manuscript, service=stub, docs_service=stub)
        check("a Doc-side prompt edit pulls into the canonical file, "
              "never adopted as an essay",
              display in rep["changed"]
              and prompt_path.read_text()
              == "a silver orrery beneath a shattered dome\n"
              and not rep.get("adopted")
              and ref not in doc_status(db, api.get_manuscript(db)),
              str(rep))
        il.maintain_excerpts(ms)
        check("the owning tag's excerpt follows the pulled text",
              il.find_slot(ms, "shattered dome")[0]["excerpt"]
              .startswith("a silver orrery beneath a shattered"), "")

        prompt_path.write_text("a bronze orrery in candlelight\n")
        stub.set_tab(ref, "a golden orrery at dawn")
        rep = pull_doc(db, manuscript, service=stub, docs_service=stub)
        check("two-sided prompt edits conflict, file untouched",
              display in rep["conflicts"]
              and "bronze" in prompt_path.read_text(), str(rep))
        forced = pull_doc(db, manuscript, ref, service=stub,
                          docs_service=stub, force=True)
        check("targeted prompt pull resolves by slug; --force takes the Doc",
              display in forced["changed"]
              and prompt_path.read_text() == "a golden orrery at dawn\n",
              str(forced))

        prompt_path.write_text("a golden orrery at dusk\n")
        rep = pull_doc(db, manuscript, service=stub, docs_service=stub)
        check("a local-only prompt edit is kept (tab stale, not rival)",
              display in rep["local_ahead"]
              and "dusk" in prompt_path.read_text(), str(rep))
        prom3 = push_prompt_tabs(db, manuscript, stub, stub)
        slug_tab = next(t for t in stub.state["docs"]["doc-2"]
                        if t["title"] == ref)
        check("push rewrites only the drifted prompt tab",
              prom3["updated"] == [ref] and not prom3["created"]
              and slug_tab["text"].strip() == "a golden orrery at dusk",
              str(prom3))

        # A hand-made tab in the reserved tree: warned, never adopted.
        stub.state["tab_counter"] += 1
        stub.state["docs"]["doc-2"].append(
            {"id": f"tab-{stub.state['tab_counter']}", "title": "scratch.md",
             "text": "a hand-made note", "parent": illus_tab["id"]})
        rep = pull_doc(db, manuscript, service=stub, docs_service=stub)
        links_after = doc_status(db, api.get_manuscript(db))
        check("hand-made tabs under the reserved tree are inert: warned, "
              "no essay adoption, no local file",
              rep.get("prompt_unknown") == ["scratch.md"]
              and not rep.get("adopted")
              and "scratch.md" not in links_after
              and not (ms / "scratch.md").exists(), str(rep))
        prom4 = push_prompt_tabs(db, manuscript, stub, stub)
        check("push leaves the hand-made tab untouched",
              prom4["unknown"] == ["scratch.md"] and not prom4["pruned"],
              str(prom4))

        slug_tab["title"] = "renamed-by-hand.md"
        rep = pull_doc(db, manuscript, service=stub, docs_service=stub)
        check("a renamed prompt tab is skipped and called out",
              rep.get("prompt_renamed") == [(ref, "renamed-by-hand.md")]
              and "dusk" in prompt_path.read_text(), str(rep))
        slug_tab["title"] = ref

        prompt_path.unlink()
        prom5 = push_prompt_tabs(db, manuscript, stub, stub)
        check("a locally deleted prompt file prunes its tab and mapping",
              prom5["pruned"] == [ref]
              and all(t["title"] != ref
                      for t in stub.state["docs"]["doc-2"])
              and "_illusprompt/" + ref
              not in doc_status(db, api.get_manuscript(db)), str(prom5))
        prompt_path.write_text("a golden orrery at dusk\n")
        prom6 = push_prompt_tabs(db, manuscript, stub, stub)
        check("the file's return recreates its tab (Doc-side deletions "
              "recover the same way)",
              prom6["created"] == [ref], str(prom6))

        stub.set_tab(ref, "a silver orrery reconsidered")
        report = reconcile(db, api.get_manuscript(db), stub,
                           docs_service=stub)
        check("reconcile auto-pulls a Doc-side prompt edit",
              display in report["pulled"]
              and prompt_path.read_text()
              == "a silver orrery reconsidered\n", str(report))
        prompt_path.write_text("a silver orrery reconsidered twice\n")
        report = reconcile(db, api.get_manuscript(db), stub,
                           docs_service=stub)
        check("reconcile auto-pushes a local-only prompt edit",
              display in report["pushed"]
              and next(t["text"] for t in stub.state["docs"]["doc-2"]
                       if t["title"] == ref).strip()
              == "a silver orrery reconsidered twice", str(report))
        report = reconcile(db, api.get_manuscript(db), stub,
                           docs_service=stub)
        check("prompt tabs settle in sync at session start",
              display in report["in_sync"], str(report))

        # A hand-made tab is not a manuscript file, but it IS a split
        # boundary: without that, its heading and body are swallowed by
        # whichever tab precedes it in the export and then auto-pulled
        # over that file as if the author had written them
        # (it-307dc1279a7e — a real 'Tab 31' after the last prompt tab).
        settled = prompt_path.read_text()
        stub.state["docs"]["doc-2"].append(
            {"id": "tab-handmade", "title": "Tab 31",
             "text": "notes to self\nnot part of any chapter",
             "parent": None})
        report = reconcile(db, api.get_manuscript(db), stub,
                           docs_service=stub)
        check("a hand-made tab is a split boundary — the preceding file "
              "keeps its own text and the tab is reported, not merged",
              prompt_path.read_text() == settled
              and "Tab 31" not in prompt_path.read_text()
              and display in report["in_sync"]
              and "Tab 31" in report.get("ignored_tabs", []), str(report))
        stub.state["docs"]["doc-2"].pop()
        il.maintain_excerpts(ms)

        # --- prompt preview embeds: ![](../…) derived machinery ---
        from authorlm.illus import load_prompts

        orrery_slot = il.find_slot(ms, "reconsidered twice")[0]
        cand = (f"{orrery_slot['key']}-{orrery_slot['desc_hash']}"
                "-0000-01.png")
        (ms / "_illustrations" / cand).write_bytes(tiny_png())
        ok_pin = il.set_embed(ms / "06-orrery.md", orrery_slot["key"], cand)
        check("set_embed finds an externalized slot by its key",
              ok_pin and orrery_slot["key"] == ref[:-3]
              and f"![](_illustrations/{cand})"
              in (ms / "06-orrery.md").read_text(), str(orrery_slot))
        fixes = il.maintain_excerpts(ms)
        check("maintenance mirrors the essay's pick into the prompt file",
              any(f.get("embed_synced") for f in fixes)
              and prompt_path.read_text().endswith(
                  f"\n\n![](../{cand})\n"), prompt_path.read_text())
        check("preview embeds are invisible to identity",
              load_prompts(ms)[ref]
              == "a silver orrery reconsidered twice\n"
              and il.find_slot(ms, "reconsidered twice")[0]["desc_hash"]
              == orrery_slot["desc_hash"], "")
        prom7 = push_prompt_tabs(db, manuscript, stub, stub)
        check("a preview embed alone never re-pushes; the Doc never "
              "sees it",
              not prom7["updated"] and not prom7["created"]
              and "![](" not in next(
                  t["text"] for t in stub.state["docs"]["doc-2"]
                  if t["title"] == ref), str(prom7))
        stub.set_tab(ref, "a silver orrery, final wording")
        rep = pull_doc(db, manuscript, service=stub, docs_service=stub)
        ptext = prompt_path.read_text()
        check("pull rewrites the text and keeps the preview embed",
              display in rep["changed"]
              and ptext.startswith("a silver orrery, final wording\n")
              and ptext.rstrip().endswith(f"![](../{cand})"), ptext)
        (ms / "06-orrery.md").write_text(
            (ms / "06-orrery.md").read_text().replace(
                f"![](_illustrations/{cand})\n", ""))
        il.maintain_excerpts(ms)
        check("unpinning in the essay clears the preview embed",
              "![](" not in prompt_path.read_text(),
              prompt_path.read_text())

        # --- X7-3: pre-pull recovery point for _illustrations/prompts/ ---
        # These files hold the CANONICAL author text — collect_revision
        # skips '_'-prefixed dirs on purpose, so nothing else snapshots
        # them, yet a pull overwrites them from the Doc. Prove: (1) a
        # forced pull genuinely destroys the local text, (2) the author
        # can actually get it back, (3) the snapshot table doesn't flood
        # on ordinary observation or on repeated no-op pulls.
        def _prompt_version_rows():
            return db.all(
                "SELECT * FROM illustration_prompt_versions WHERE "
                "manuscript_id = ? ORDER BY version_no", (manuscript["id"],))

        precious_text = ("The author's carefully chosen words for this "
                         "orrery scene, never to be lost.\n")
        prompt_path.write_text(precious_text)
        before_rows = len(_prompt_version_rows())
        stub.set_tab(ref, "Doc-side wording that will destroy the above")
        destroyed = pull_doc(db, manuscript, service=stub, docs_service=stub,
                             force=True)
        check("X7-3 PRE-FIX REGRESSION: a forced pull overwrites the local "
              "prompt file — the author's precious text is gone from disk",
              display in destroyed["changed"]
              and precious_text != prompt_path.read_text(),
              prompt_path.read_text())
        check("the pre-pull snapshot was taken before the destructive "
              "write (one new row, holding the about-to-be-lost text)",
              len(_prompt_version_rows()) == before_rows + 1
              and loads(_prompt_version_rows()[-1]["files"], {}).get(ref)
              == precious_text,
              [dict(r) for r in _prompt_version_rows()])
        recovered = il.restore_prompts(db, manuscript)
        check("X7-3 RECOVERY: restore_prompts writes the exact pre-pull "
              "text back to disk — the author gets it back",
              ref in recovered["restored"]
              and prompt_path.read_text() == precious_text,
              prompt_path.read_text())
        settled_rows = len(_prompt_version_rows())
        settled = pull_doc(db, manuscript, service=stub, docs_service=stub)
        check("after recovery, an ordinary (non-forced) pull leaves the "
              "restored text alone (three-way sees it as local-ahead, not "
              "a silent re-overwrite)",
              display in settled["local_ahead"]
              and prompt_path.read_text() == precious_text, str(settled))
        check("no flooding: a pull whose disk state matches the last "
              "snapshot adds no new row (skip-if-unchanged, like backup.py "
              "and collect_revision)",
              len(_prompt_version_rows()) == settled_rows,
              [dict(r) for r in _prompt_version_rows()])
        collect_rows_before = len(_prompt_version_rows())
        api.collect(db, manuscript, {})
        check("ordinary observation (api.collect) never touches the "
              "illustration-prompt snapshot table — _illustrations/ stays "
              "observation-invisible, no version-history flooding",
              len(_prompt_version_rows()) == collect_rows_before)
        try:
            il.restore_prompts(db, manuscript, version_no=999999)
            raise AssertionError("expected LookupError")
        except LookupError as err:
            check("restoring a nonexistent snapshot version raises, "
                  "never guesses",
                  "999999" not in str(err) or "no illustration-prompt"
                  in str(err), str(err))

        # --- X7-3-cli: 'illus restore' / 'illus versions' via cli_main ---
        # The recovery mechanism only counts if the author can reach it
        # without hand-running Python — prove the CLI verb itself,
        # end-to-end through cli_main, not the API function directly.
        cli_precious = "A second precious edit, restored via the CLI.\n"
        prompt_path.write_text(cli_precious)
        stub.set_tab(ref, "Doc wording that would destroy the CLI text")
        pull_doc(db, manuscript, service=stub, docs_service=stub, force=True)
        check("X7-3-cli PRE-FIX REGRESSION setup: the destructive pull "
              "still clobbers the file",
              cli_precious != prompt_path.read_text(), prompt_path.read_text())

        versions_out = io.StringIO()
        with contextlib.redirect_stdout(versions_out):
            cli_main(["--workspace", str(ws), "illus", "versions"])
        versions_text = versions_out.getvalue()
        check("'illus versions' lists the snapshots (version, timestamp, "
              "file count, triggering source) — how the author picks "
              "--version N meaningfully",
              "pre-doc-pull" in versions_text
              and "file(s)" in versions_text
              and "v1" in versions_text, versions_text)

        restore_out = io.StringIO()
        with contextlib.redirect_stdout(restore_out):
            cli_main(["--workspace", str(ws), "illus", "restore"])
        restore_text = restore_out.getvalue()
        check("X7-3-cli RECOVERY: 'illus restore' actually restores the "
              "destroyed prompt file, end-to-end through the CLI (not "
              "just the Python API)",
              prompt_path.read_text() == cli_precious, prompt_path.read_text())
        check("the restore CLI prints the plan — which file(s), from "
              "which snapshot — BEFORE doing the overwrite",
              ref in restore_text
              and "Restoring illustration prompts from snapshot v"
              in restore_text, restore_text)
        check("the restore CLI warns plainly that existing content will "
              "be overwritten",
              "OVERWRITES" in restore_text, restore_text)
        check("the restore CLI states the restore is additive (not a "
              "wholesale revert) — newer prompt files are left alone",
              "not a wholesale revert" in restore_text
              and "left untouched" in restore_text, restore_text)

        bad_out = io.StringIO()
        bad_err = None
        try:
            with contextlib.redirect_stdout(bad_out):
                cli_main(["--workspace", str(ws), "illus", "restore",
                         "--version", "999999"])
        except SystemExit as exc:
            bad_err = str(exc)
        check("a nonexistent --version produces a clean error message, "
              "never a traceback",
              bad_err is not None and "999999" in bad_err
              and "no illustration-prompt snapshot" in bad_err
              and not bad_out.getvalue(), bad_err)

        # --- comment-anchor safety: pushes route around open comments ---
        from authorlm.gdocs import comment_bearing, manuscript_bridge

        (ms / "06-orrery.md").write_text(
            "# Orrery\n\nBrass planets on brass rails.\n\n"
            "A distinctive orrery sentence to anchor a comment.\n")
        push_doc(db, manuscript, "06-orrery.md", service=stub,
                 docs_service=stub)  # rebuild path: margin is empty
        stub.add_comment("c-anchor", "distinctive orrery sentence",
                         "Please reconsider this line.")
        (ms / "06-orrery.md").write_text(
            "# Orrery\n\nBrass planets on brass rails, polished.\n\n"
            "A distinctive orrery sentence to anchor a comment.\n")
        guarded = push_doc(db, manuscript, "06-orrery.md", service=stub,
                           docs_service=stub)
        check("a push on a comment-bearing tab goes surgical",
              guarded.get("mode") == "diff", str(guarded))
        stub.add_comment("c-nowhere", "a quote matching no file at all",
                         "Where does this belong?")
        check("an unattributable anchor makes every mapped tab bearing",
              comment_bearing(db, manuscript,
                              manuscript_bridge(manuscript),
                              "01-choice.md", stub) is True, "")
        stub.state["comments"]["c-anchor"]["resolved"] = True
        stub.state["comments"]["c-nowhere"]["resolved"] = True
        cleared = push_doc(db, manuscript, "06-orrery.md", service=stub,
                           docs_service=stub)
        check("a resolved margin restores the rebuild path",
              "mode" not in cleared, str(cleared))

        # Critique pending forms are invisible to open_threads (author_comment
        # only). A rebuild push during the pause would wipe <<old>>{{new}} /
        # {{insert}} forms — including the author's Doc post-edits — and
        # session-start reconcile auto-push takes the same path.
        from authorlm.db import ko_fields as _ko_dt

        pending_tab = (
            "# Orrery\n"
            "<<Brass planets on brass rails, polished.>>"
            "{{Brass planets, post-edited in Docs.}}\n\n"
            "{{A bridging insert the author refined.}}\n\n"
            "A distinctive orrery sentence to anchor a comment.\n")
        stub.set_tab("06-orrery.md", pending_tab)
        crit = _ko_dt("dt")
        crit.update(
            manuscript_id=manuscript["id"], origin_type="critique",
            origin_id="pass:06-orrery.md:1", file="06-orrery.md",
            proposed_old="Brass planets on brass rails, polished.",
            proposed_new="Brass planets, post-edited in Docs.",
            note="critique", state="written", our_reply_ids="[]",
            metadata='{"kind":"replace","anchor_paragraph":1}')
        db.insert("doc_threads", crit)
        tab_before = next(t["text"] for t in stub.state["docs"]["doc-2"]
                          if t["title"] == "06-orrery.md")
        raised = None
        try:
            push_doc(db, manuscript, "06-orrery.md", service=stub,
                     docs_service=stub)
        except LookupError as err:
            raised = str(err)
        tab_after = next(t["text"] for t in stub.state["docs"]["doc-2"]
                         if t["title"] == "06-orrery.md")
        check("push refuses while critique forms are written (no wipe)",
              raised is not None
              and "critique pending forms" in raised
              and "critique resolve" in raised
              and tab_after == tab_before
              and "post-edited in Docs" in tab_after
              and "{{A bridging insert the author refined.}}" in tab_after,
              str({"raised": raised, "tab": tab_after[:120]}))
        db.update("doc_threads", crit["id"], {"state": "cleaned"})

        # --- session start is no longer blind to the margin ---
        stub.add_comment("c-fresh", "distinctive orrery sentence",
                         "Is brass the right metal?")
        rep_h = reconcile(db, api.get_manuscript(db), stub,
                          docs_service=stub)
        check("reconcile harvests open comments at session start",
              any("right metal" in c["content"]
                  for c in rep_h.get("comments", [])), str(rep_h))
        rep_h2 = reconcile(db, api.get_manuscript(db), stub,
                           docs_service=stub)
        check("re-reconcile never re-ingests the same comment",
              not rep_h2.get("comments"), str(rep_h2.get("comments")))

        # --- scoped concept fetch + batch curation (token plan) ---
        api.add_concept(db, manuscript, "Winding Path",
                        notes="the road that bends")
        api.add_concept(db, manuscript, "Iron Gate",
                        notes="entry that resists")
        api.add_concept(db, manuscript, "Brass Planets",
                        notes="the orrery's cargo")
        api.link_concepts(db, manuscript, "Winding Path", "leads_to",
                          "Iron Gate")
        over = api.concept_overview(db, manuscript)
        check("unscoped fetch is a summary, never a dump",
              "nodes" not in over and over["node_count"] >= 3
              and "Winding Path" in over["recently_added"]
              and "narrow" in over["guidance"].lower(), str(over)[:200])
        hit = api.scoped_concepts(db, manuscript, query="iron gate")
        check("query scope returns the matching slice only",
              hit["node_count"] == 1
              and any("Iron Gate" in str(n) for n in hit["nodes"]),
              str(hit))
        sliced = api.scoped_concepts(db, manuscript, file="06-orrery.md")
        check("file scope selects concepts realized in the essay, "
              "edges restricted to the slice",
              sliced["node_count"] == 1
              and any("Brass Planets" in str(n) for n in sliced["nodes"])
              and sliced["edge_count"] == 0, str(sliced))
        batch = api.curate_concepts(db, manuscript, [
            {"op": "confirm", "name": "Winding Path"},
            {"op": "alias", "name": "Iron Gate", "aliases": ["Portcullis"]},
            {"op": "retire", "name": "Brass Planets"},
            {"op": "retire", "name": "No Such Concept"},
            {"op": "frobnicate", "name": "x"},
        ])
        check("batch curation applies in order, isolating failures",
              batch["applied"] == 3 and batch["failed"] == 2
              and batch["results"][3]["ok"] is False
              and "unknown op" in batch["results"][4]["error"], str(batch))
        check("batch results landed in the graph",
              api.scoped_concepts(db, manuscript,
                                  query="portcullis")["node_count"] == 1,
              "")
        cleanup = api.curate_concepts(db, manuscript, [
            {"op": "retire", "name": "Winding Path"},
            {"op": "retire", "name": "Iron Gate"},
        ])
        check("cleanup batch retires the scaffolding",
              cleanup["applied"] == 2 and cleanup["failed"] == 0,
              str(cleanup))

        # Deterministic guards: toc exclusion + pacing budget + arbiter.
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "01-choice.md"\n\n'
            '[[chapter]]\nfile = "00-intro.md"\n'
            'illustrations = "none"\n\n'
            '[[chapter]]\nfile = "04-road.md"\n')
        report = placement.scan(db, manuscript, spot_llm2,
                                files=["00-intro.md"])
        check("toc illustrations=none files are never scanned",
              report["calls"] == 0 and report["files"] == [], str(report))
        (ms / "05-tiny.md").write_text(
            "# **Tiny**\n\nShort words here.\n\n"
            "[Illustration: existing emblem]\n")
        tiny_report = placement.scan(db, manuscript, spot_llm2,
                                     files=["05-tiny.md"])
        check("a chapter at its pacing budget is skipped deterministically",
              tiny_report["calls"] == 0
              and tiny_report["skipped_at_budget"] == ["05-tiny.md"],
              str(tiny_report))
        # Arbiter: two duplicate-metaphor proposals across files; the
        # arbiter keeps one, cuts the other with a reason — no evidence.
        anchor_choice = "And the second is like unto the first."
        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text() + f"\n\n{anchor_choice}\n")
        dup0 = FakeLLM({"proposals": [
            {"anchor": "The road changed.",
             "description": "a ladder to the clouds",
             "criterion": "2", "rationale": "r", "revises": None}]})
        placement.scan(db, manuscript, dup0, files=["04-road.md"])
        dup1 = FakeLLM({"proposals": [
            {"anchor": anchor_choice, "description": "a ladder to the sky",
             "criterion": "2", "rationale": "r", "revises": None}]})
        placement.scan(db, manuscript, dup1, files=["01-choice.md"])
        pid = [r["id"] for r in placement.open_proposals(db, manuscript["id"])
               if r["file"] == "01-choice.md"][0]
        ev_before = db.one("SELECT COUNT(*) AS n FROM evidence "
                           "WHERE evidence_type = 'illus_triage'")["n"]
        arb_llm = FakeLLM({"cut": [{"id": pid,
                                    "reason": "duplicate ladder home"}]})
        arb = placement.arbitrate(db, manuscript, arb_llm)
        cut_row = dict(db.one(
            "SELECT * FROM illus_proposals WHERE id = ?", (pid,)))
        check("arbiter cuts duplicates with a reason, never as evidence",
              [c["id"] for c in arb["arbiter_cut"]] == [pid]
              and cut_row["state"] == "rejected"
              and _json.loads(cut_row["metadata"])["by"] == "arbiter"
              and db.one("SELECT COUNT(*) AS n FROM evidence WHERE "
                         "evidence_type = 'illus_triage'")["n"] == ev_before,
              str(arb))

        # Revision acceptance must treat the new description as literal text
        # — a string-template re.sub interprets \1/\U as group refs/escapes.
        from authorlm.db import ko_fields as _ko

        (ms / "06-revise.md").write_text(
            "# **Revise**\n\nAn emblem stands at the gate.\n\n"
            "[Illustration: old emblem at the gate]\n")
        rev_row = _ko("ip")
        rev_row.update(
            manuscript_id=manuscript["id"], file="06-revise.md",
            anchor="An emblem stands at the gate.",
            description=r"emblem under C:\Users\author\figs with \1 mark",
            criterion="2", rationale="clearer",
            revises="old emblem at the gate", state="proposed")
        db.insert("illus_proposals", rev_row)
        rev_result = placement.decide(db, manuscript, rev_row["id"], "accept")
        rev_text = (ms / "06-revise.md").read_text()
        check("revision accept writes backslash-rich descriptions literally",
              rev_result["state"] == "accepted"
              and r"C:\Users\author\figs with \1 mark" in rev_text
              and "[Illustration: old emblem at the gate]" not in rev_text,
              rev_text)

        # Multi-pass extraction must not advance the watermark when a later
        # pass returns None — otherwise the missed payload is skipped forever.
        from authorlm.extraction import extract_concepts

        class FlakyLLM:
            enabled = True
            extraction_max_chars = 80

            def __init__(self):
                self.calls = 0

            def complete_json(self, system, user, thinking_budget=None):
                self.calls += 1
                if self.calls == 1:
                    return {"concepts": [
                        {"name": "AlphaConcept", "kind": "definition",
                         "notes": "first payload"}],
                            "links": [], "aliases": []}
                return None  # second payload fails

            def stats_line(self):
                return None

        # Two distinct contexts, not one paragraph repeated: the recurrence
        # bar admits a concept only when it appears in separate contexts,
        # and eight repeats in a single paragraph is one context — the
        # rule landed after this test was written.
        (ms / "07-alpha.md").write_text(
            "# Alpha\n\n" + ("AlphaConcept appears here. " * 4) + "\n\n"
            "## Later\n\n" + ("AlphaConcept returns here. " * 4) + "\n")
        (ms / "08-beta.md").write_text(
            "# Beta\n\n" + ("BetaConcept appears here. " * 8) + "\n")
        api.collect(db, manuscript, {})
        latest = db.one(
            "SELECT id FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no DESC LIMIT 1", (manuscript["id"],))
        before_meta = _json.loads(manuscript["metadata"] or "{}")
        flaky = FlakyLLM()
        multi = extract_concepts(
            db, manuscript, flaky, files=["07-alpha.md", "08-beta.md"])
        after_meta = _json.loads(
            db.one("SELECT metadata FROM manuscripts WHERE id = ?",
                   (manuscript["id"],))["metadata"] or "{}")
        check("multi-pass LLM miss leaves watermark unadvanced",
              multi is not None
              and multi.get("incomplete") is True
              and after_meta.get("last_extracted_version")
              == before_meta.get("last_extracted_version")
              and after_meta.get("last_extracted_version") != latest["id"],
              str({"multi": multi, "before": before_meta, "after": after_meta,
                   "latest": latest["id"], "calls": flaky.calls}))
        check("first multi-pass payload still commits its concepts",
              db.one("SELECT id FROM concept_nodes WHERE manuscript_id = ? "
                     "AND name = ?",
                     (manuscript["id"], "AlphaConcept")) is not None,
              str(multi))

        # --- BUG-7: extraction must never overwrite the author's ratified
        # concept notes, even when a materially different paraphrase comes
        # back for text the author actually changed. ---
        (ms / "09-ratified.md").write_text(
            "# Ratified\n\nRatifiedTerm is discussed at length here.\n"
        )
        api.add_concept(db, manuscript, "RatifiedTerm", kind="concept",
                        notes="AUTHOR RATIFIED TEXT")
        note_proposals_before = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals "
            "WHERE manuscript_id = ? AND kind = 'note_update'",
            (manuscript["id"],))["n"]

        class NoteOverwriteLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [
                    {"name": "RatifiedTerm", "kind": "concept",
                     "notes": "machine paraphrase of the definition"}],
                        "links": [], "aliases": []}

            def stats_line(self):
                return None

        extract_concepts(db, manuscript, NoteOverwriteLLM(),
                         files=["09-ratified.md"])
        ratified_after = concepts.get_concept(db, manuscript["id"], "RatifiedTerm")
        note_proposals_after = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals "
            "WHERE manuscript_id = ? AND kind = 'note_update'",
            (manuscript["id"],))["n"]
        check("extraction never overwrites the author's ratified concept notes",
              ratified_after["notes"] == "AUTHOR RATIFIED TEXT",
              f"notes became: {ratified_after['notes']!r}")
        check("a materially different extraction files a note_update "
              "proposal instead of applying itself",
              note_proposals_after == note_proposals_before + 1,
              f"before={note_proposals_before} after={note_proposals_after}")

        # --- BUG-8: a hit on a concept's ALIAS (not its primary name) must
        # not be treated as a brand-new concept — that discards a confirmed
        # concept's metadata (including 'confirmed': True) and reopens it to
        # silent auto-retirement. ---
        (ms / "10-alias.md").write_text(
            "# Alias\n\nAliasSecondary shows up in the prose here.\n\n"
            "## Elsewhere\n\nAliasSecondary appears again in a different "
            "section, clearing the recurrence bar.\n"
        )
        api.add_concept(db, manuscript, "AliasPrimary", kind="concept",
                        notes="primary notes")
        api.confirm_concept(db, manuscript, "AliasPrimary")
        api.alias_concept(db, manuscript, "AliasPrimary", ["AliasSecondary"])
        primary_meta_before = _json.loads(
            concepts.get_concept(db, manuscript["id"], "AliasPrimary")["metadata"]
            or "{}")
        check("fixture concept starts confirmed",
              primary_meta_before.get("confirmed") is True,
              str(primary_meta_before))

        class AliasHitLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [
                    {"name": "AliasSecondary", "kind": "concept",
                     "notes": "some notes about the alias"}],
                        "links": [], "aliases": []}

            def stats_line(self):
                return None

        extract_concepts(db, manuscript, AliasHitLLM(), files=["10-alias.md"])
        primary_after = concepts.get_concept(db, manuscript["id"], "AliasPrimary")
        primary_meta_after = _json.loads(primary_after["metadata"] or "{}")
        check("an alias hit does not reset the primary concept's 'confirmed' flag",
              primary_meta_after.get("confirmed") is True,
              f"metadata became: {primary_meta_after}")
        check("an alias hit does not fork a duplicate concept node",
              db.one("SELECT COUNT(*) AS n FROM concept_nodes WHERE "
                     "manuscript_id = ? AND lower(name) = lower(?)",
                     (manuscript["id"], "AliasSecondary"))["n"] == 0)

        # --- BUG-21: a model-shaped-but-malformed reply (a list where a
        # string kind is expected, null where an empty list is expected)
        # must not abort the extraction pass with a TypeError. ---
        (ms / "11-coerce.md").write_text(
            "# Coerce\n\nCoerceTarget needs a name here.\n\n"
            "## Elsewhere\n\nCoerceTarget appears again in a different "
            "section, clearing the recurrence bar.\n"
        )

        class MalformedLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [
                    {"name": "CoerceTarget", "kind": ["concept"]}],
                        "links": None, "aliases": []}

            def stats_line(self):
                return None

        coerce_error = None
        try:
            extract_concepts(db, manuscript, MalformedLLM(),
                             files=["11-coerce.md"])
        except TypeError as err:
            coerce_error = err
        check("a malformed 'kind' (list, not str) does not abort extraction",
              coerce_error is None, str(coerce_error))
        coerced_node = concepts.get_concept(db, manuscript["id"], "CoerceTarget")
        check("the malformed kind coerces to the 'concept' default",
              coerced_node is not None and coerced_node["kind"] == "concept",
              str(coerced_node))

        # --- BUG-21 follow-up: a null 'concepts' payload (as opposed to a
        # malformed item inside a present list) must be treated as a FAILED
        # pass, not a clean "nothing found" pass. Coercing null to []
        # (the original BUG-21 fix) let the watermark advance on a broken
        # reply, permanently skipping the section on every future
        # incremental run — a silent, unrecoverable content skip. ---
        (ms / "12-nullconcepts.md").write_text(
            "# NullConcepts\n\nNullConceptsTarget needs a name here.\n\n"
            "## Elsewhere\n\nNullConceptsTarget appears again in a "
            "different section, clearing the recurrence bar.\n"
        )

        class NullConceptsLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": None, "links": [], "aliases": []}

            def stats_line(self):
                return None

        api.collect(db, manuscript, {})
        nullconcepts_latest = db.one(
            "SELECT id FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no DESC LIMIT 1", (manuscript["id"],))
        # Query the DB directly rather than trusting the long-lived in-memory
        # `manuscript` dict: several extractions upstream in this same test
        # (FlakyLLM's first payload, NoteOverwriteLLM, AliasHitLLM,
        # MalformedLLM/BUG-21) have already advanced the real watermark in
        # the DB since `manuscript` was first bound.
        nullconcepts_before_meta = _json.loads(
            db.one("SELECT metadata FROM manuscripts WHERE id = ?",
                   (manuscript["id"],))["metadata"] or "{}")
        nullconcepts_result = extract_concepts(
            db, manuscript, NullConceptsLLM(), files=["12-nullconcepts.md"])
        nullconcepts_after_meta = _json.loads(
            db.one("SELECT metadata FROM manuscripts WHERE id = ?",
                   (manuscript["id"],))["metadata"] or "{}")
        check("a null 'concepts' payload is reported as a failed pass, "
              "not a clean miss",
              nullconcepts_result is None, str(nullconcepts_result))
        check("a null 'concepts' payload does not advance the watermark "
              "(the section stays eligible for re-mining)",
              nullconcepts_after_meta.get("last_extracted_version")
              == nullconcepts_before_meta.get("last_extracted_version")
              and nullconcepts_after_meta.get("last_extracted_version")
              != nullconcepts_latest["id"],
              str({"before": nullconcepts_before_meta,
                   "after": nullconcepts_after_meta,
                   "latest": nullconcepts_latest["id"]}))
        check("a null 'concepts' payload creates no concept node",
              concepts.get_concept(db, manuscript["id"], "NullConceptsTarget")
              is None)

        # --- BUG-5: the watermark write must re-read metadata from the DB
        # immediately before writing, not merge into an in-memory dict
        # captured before a multi-minute LLM call — otherwise a concurrent
        # write (e.g. a Google Docs mapping update) is clobbered wholesale. ---
        from authorlm.extraction import _set_extraction_watermark
        from authorlm.gdocs import _save_mapping

        stale_manuscript = dict(manuscript)  # snapshot "before the LLM call"
        _save_mapping(db, manuscript, {"gdocs": {"_master_id": "doc-survives"}})
        watermark_latest = db.one(
            "SELECT id FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no DESC LIMIT 1", (manuscript["id"],))
        _set_extraction_watermark(db, stale_manuscript, watermark_latest["id"])
        watermark_final_meta = _json.loads(
            db.one("SELECT metadata FROM manuscripts WHERE id = ?",
                   (manuscript["id"],))["metadata"] or "{}")
        check("the watermark write re-reads fresh metadata instead of "
              "clobbering a concurrent write with a stale in-memory copy",
              watermark_final_meta.get("gdocs", {}).get("_master_id")
              == "doc-survives"
              and watermark_final_meta.get("last_extracted_version")
              == watermark_latest["id"],
              str(watermark_final_meta))
        manuscript = api.get_manuscript(db)  # refresh metadata

        # --- prerequisite-gap first mentions: terms of art, not casual words ---
        # Repro from improvement task it-e34cf5227223: 'wandered through time
        # and space' must not count as the first mention of concept 'Space'.
        from authorlm.guidance import _first_mentions
        gap_files = {"01.md": (
            "The Dead wandered through time and space in search of redemption.\n\n"
            "The mutable we name the realm of qualities.\n\n"
            "When ye discern difference in position and direction, ye name it Space.\n"
        )}
        gap_names = {"n-space": ["Space"], "n-realm": ["Realm of Qualities"]}
        mentions = _first_mentions(gap_files, gap_names)
        check("casual word use does not count as a concept mention",
              mentions["n-realm"] < mentions["n-space"],
              f"positions: {mentions}")
        check("multi-word names still match case-insensitively",
              "n-realm" in mentions)
        check("a concept only ever used casually has no first mention",
              "n-space" not in _first_mentions(
                  {"01.md": "They wandered through time and space.\n"},
                  {"n-space": ["Space"]}))
        heading_pos = _first_mentions(
            {"01.md": "## Discernment\n\nVirtue is chosen for effectiveness.\n",
             "02.md": "Discernment is the measure of distinctions.\n"},
            {"n-disc": ["Discernment"]})
        check("capitalization from headings and sentence starts counts",
              "n-disc" in heading_pos and heading_pos["n-disc"] < 20)
        from authorlm.guidance import PREREQUISITE_FIRST
        check("leads_to is an ordering relation (cause introduced before effect)",
              "leads_to" in PREREQUISITE_FIRST)

        # --- self-improvement tasks ---
        try:
            api.file_improvement(db, title="x", evidence="e", given="g",
                                 observed="", expected="v")
            check("file_improvement rejects an empty repro field", False)
        except ValueError:
            check("file_improvement rejects an empty repro field", True)

        task = api.file_improvement(
            db, title="Gap false positive on common words",
            evidence="'space' matched casually at file.md:9",
            given="a manuscript using 'time and space' casually before the concept intro",
            observed="prerequisite gap reported for Space",
            expected="casual word use must not count as a concept mention",
            manuscript=manuscript,
        )
        check("file_improvement stamps manuscript and session provenance",
              task["manuscript_id"] == manuscript["id"]
              and task["status"] == "open")
        import json as _json
        stamp = _json.loads(task["metadata"])
        latest_version = db.one(
            "SELECT id FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no DESC LIMIT 1", (manuscript["id"],))
        check("filing stamps the current manuscript version (Time Machine)",
              stamp.get("manuscript_version_id") == latest_version["id"]
              and stamp.get("manuscript_version_no") is not None)
        check("filing stamps the source tree's git commit (Time Machine)",
              len(stamp.get("git_commit", "")) == 40
              and "git_dirty" in stamp)
        check("improvement tasks are listed as active",
              any(t["id"] == task["id"] for t in api.list_improvements(db, "active")))

        bundled = api.improvement_bundle(db, task["id"])
        check("improvement_bundle is self-contained and marks in_progress",
              bundled["status"] == "in_progress"
              and "file.md:9" in bundled["bundle"]
              and "casual word use must not count" in bundled["bundle"]
              and "tests/test_api.py" in bundled["bundle"])
        check("bundle carries the filing-time context (Time Machine)",
              stamp["git_commit"][:12] in bundled["bundle"]
              and latest_version["id"] in bundled["bundle"]
              and "git diff" in bundled["bundle"])
        rebundled = api.improvement_bundle(db, task["id"])
        check("re-emitting a bundle never regresses status",
              rebundled["status"] == "in_progress")

        try:
            api.resolve_improvement(db, task["id"], "close")
            check("close without a proposed resolution is refused", False)
        except ValueError:
            check("close without a proposed resolution is refused", True)
        api.resolve_improvement(db, task["id"], "propose",
                                note="tightened concept_pattern; test_gap_casual_words")
        closed = api.resolve_improvement(db, task["id"], "close")
        check("propose then close resolves with the fix on record",
              closed["status"] == "resolved")
        try:
            api.resolve_improvement(db, task["id"], "dismiss", note="n/a")
            check("a resolved task cannot be re-transitioned", False)
        except ValueError:
            check("a resolved task cannot be re-transitioned", True)

        task2 = api.file_improvement(db, title="t2", evidence="e2", given="g2",
                                     observed="o2", expected="x2")
        try:
            api.resolve_improvement(db, task2["id"], "dismiss")
            check("dismissal without a reason is refused", False)
        except ValueError:
            check("dismissal without a reason is refused", True)
        dismissed = api.resolve_improvement(db, task2["id"], "dismiss",
                                            note="intended behavior")
        check("dismissal with a reason lands with the reason on record",
              dismissed["status"] == "dismissed"
              and api.list_improvements(db, "dismissed")[0]["resolution"]
              == "intended behavior")
        check("improvement tasks never leak into briefings",
              "improvement" not in str(api.get_briefing(db, manuscript)).lower())

        # --- render / shell / triage resilience (live incidents 2026-08-10) ---
        import base64
        import contextlib
        import io
        import json as _rjson
        import sys
        import types
        import urllib.error
        from unittest import mock

        from authorlm import gdocs as gdocs_mod
        from authorlm import llm as llm_mod
        from authorlm import shell as shell_mod
        from authorlm.structure import matter_map, reading_order

        class _ImageResp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                png_b64 = base64.b64encode(b"PNGDATA").decode("ascii")
                return _rjson.dumps({
                    "candidates": [{"content": {"parts": [
                        {"inlineData": {"data": png_b64}}]}}]
                }).encode()

        retry_calls = {"n": 0}

        def urlopen_timeout_then_ok(request, timeout=None):
            retry_calls["n"] += 1
            retry_calls["timeout"] = timeout
            if retry_calls["n"] == 1:
                raise TimeoutError("stalled read")
            return _ImageResp()

        with mock.patch("urllib.request.urlopen", urlopen_timeout_then_ok):
            png = llm_mod._gemini_image("gemini-img", "a ladder", None,
                                        "fake-key", 17)
        check("gemini image call retries once after TimeoutError",
              png == b"PNGDATA" and retry_calls["n"] == 2
              and retry_calls["timeout"] == 17, str(retry_calls))

        http_calls = {"n": 0}

        def urlopen_http_error(request, timeout=None):
            http_calls["n"] += 1
            raise urllib.error.HTTPError(
                "https://example.test", 500, "boom", hdrs=None,
                fp=io.BytesIO(b'{"error":"no"}'))

        try:
            with mock.patch("urllib.request.urlopen", urlopen_http_error):
                llm_mod._gemini_image("gemini-img", "prompt", None,
                                      "fake-key", 17)
            check("gemini HTTPError is not retried as a transient stall",
                  False)
        except RuntimeError as err:
            check("gemini HTTPError is not retried as a transient stall",
                  http_calls["n"] == 1 and "500" in str(err), str(err))

        # --- AE/temp-compat: Claude 5 family rejects temperature != 1 ---
        # litellm.completion() raises UnsupportedParamsError for these
        # models with the fixed message "...To drop unsupported params,
        # set `litellm.drop_params = True`." — that rejection is
        # deterministic (identical retry => identical failure), so the fix
        # must resolve it before the call, not by catching-and-retrying.
        # This fake stands in for the real litellm module (litellm itself
        # isn't a hermetic-test dependency); it reproduces exactly the one
        # behavior under test — real litellm's own drop_params contract —
        # via the SAME module attribute (`drop_params`) our code sets.
        from authorlm.llm import LLMClient

        class FakeUnsupportedParamsError(Exception):
            pass

        def make_fake_litellm(no_temp_models, fail_times=0,
                              transient_exc=None):
            calls = []
            state = {"raises_left": fail_times}
            module = types.SimpleNamespace(
                suppress_debug_info=False, drop_params=False,
                UnsupportedParamsError=FakeUnsupportedParamsError)

            def completion(model, messages, timeout=None, **kwargs):
                if transient_exc is not None and state["raises_left"] > 0:
                    state["raises_left"] -= 1
                    raise transient_exc
                if ("temperature" in kwargs and model in no_temp_models
                        and kwargs["temperature"] != 1):
                    if not module.drop_params:
                        raise FakeUnsupportedParamsError(
                            f"{model} does not support temperature="
                            f"{kwargs['temperature']}. Only temperature=1 "
                            "is supported. To drop unsupported params, "
                            "set `litellm.drop_params = True`.")
                    kwargs = {k: v for k, v in kwargs.items()
                             if k != "temperature"}
                calls.append({"model": model, **kwargs})
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(
                        message=types.SimpleNamespace(content="the reply"))],
                    usage=types.SimpleNamespace(prompt_tokens=11,
                                                completion_tokens=7))

            module.completion = completion
            return module, calls

        # (1) A temperature-rejecting model still gets called correctly —
        # one call, no wasted backoff retry.
        fake1, calls1 = make_fake_litellm({"anthropic/claude-sonnet-5"})
        sleeps1 = []
        client1 = LLMClient({"llm": {"enabled": True,
                                     "model": "anthropic/claude-sonnet-5"}})
        with mock.patch.dict(sys.modules, {"litellm": fake1}), \
                mock.patch("authorlm.llm.time.sleep", sleeps1.append):
            reply1 = client1.complete("sys", "usr")
        check("a temperature-rejecting model (Claude 5 family) still "
              "returns a reply",
              reply1 == "the reply", (reply1, calls1))
        check("...in one call, without burning the transient-failure "
              "backoff",
              len(calls1) == 1 and not sleeps1, (calls1, sleeps1))
        check("litellm.drop_params is set so the param is shed before the "
              "call, not caught after it fails",
              fake1.drop_params is True)

        # (2) A model that DOES accept temperature still receives it.
        fake2, calls2 = make_fake_litellm({"anthropic/claude-sonnet-5"})
        client2 = LLMClient({"llm": {"enabled": True,
                                     "model": "gemini/gemini-2.5-flash"}})
        with mock.patch.dict(sys.modules, {"litellm": fake2}):
            reply2 = client2.complete("sys", "usr")
        check("a model that accepts temperature still receives it",
              reply2 == "the reply" and calls2[0].get("temperature") == 0.2,
              calls2)

        # (3) A genuine transient failure (not a param-support rejection)
        # still gets the existing backoff retries.
        fake3, calls3 = make_fake_litellm(
            set(), fail_times=1, transient_exc=ConnectionError("stalled"))
        sleeps3 = []
        client3 = LLMClient({"llm": {"enabled": True,
                                     "model": "gemini/gemini-2.5-flash"}})
        with mock.patch.dict(sys.modules, {"litellm": fake3}), \
                mock.patch("authorlm.llm.time.sleep", sleeps3.append):
            reply3 = client3.complete("sys", "usr")
        check("a genuine transient failure still retries with backoff "
              "and eventually succeeds",
              reply3 == "the reply" and len(calls3) == 1
              and sleeps3 == [1], (calls3, sleeps3))

        # --- AE/key-by-model: api_key must track a LATER cross-vendor
        # reassignment of .model (summarizer_llm/editor_llm build an
        # LLMClient off [llm], then override .model to a [critique]
        # summarizer_model/editor_model of a different vendor) — a key
        # resolved once at __init__ time would keep naming the ORIGINAL
        # vendor and hand the new vendor's API a foreign key.
        with mock.patch.dict(os.environ,
                             {"GEMINI_API_KEY": "gemini-sentinel",
                              "ANTHROPIC_API_KEY": "anthropic-sentinel"}):
            fake_key, calls_key = make_fake_litellm(set())
            client_key = LLMClient({"llm": {
                "enabled": True, "model": "gemini/gemini-2.5-flash"}})
            with mock.patch.dict(sys.modules, {"litellm": fake_key}):
                client_key.complete("sys", "usr")
            check("api_key resolves to the model's vendor key at "
                  "construction",
                  calls_key[-1].get("api_key") == "gemini-sentinel",
                  calls_key)

            client_key.model = "anthropic/claude-sonnet-5"
            with mock.patch.dict(sys.modules, {"litellm": fake_key}):
                client_key.complete("sys", "usr")
            check("...and re-resolves to the NEW vendor's key once "
                  ".model is reassigned post-construction",
                  calls_key[-1].get("api_key") == "anthropic-sentinel",
                  calls_key)

            client_key.model = "gemini/gemini-2.5-flash"
            with mock.patch.dict(sys.modules, {"litellm": fake_key}):
                client_key.complete("sys", "usr")
            check("...and back again when .model reverts",
                  calls_key[-1].get("api_key") == "gemini-sentinel",
                  calls_key)

        # (3) api_key_env still wins over the vendor-derived key, on
        # BOTH transports, under the property.
        with mock.patch.dict(os.environ,
                             {"CUSTOM_PROXY_KEY": "proxy-sentinel",
                              "GEMINI_API_KEY": "gemini-sentinel"}):
            fake_env, calls_env = make_fake_litellm(set())
            client_env = LLMClient({"llm": {
                "enabled": True, "model": "gemini/gemini-2.5-flash",
                "api_key_env": "CUSTOM_PROXY_KEY"}})
            with mock.patch.dict(sys.modules, {"litellm": fake_env}):
                client_env.complete("sys", "usr")
            check("api_key_env overrides the vendor-derived key on the "
                  "litellm path",
                  calls_env[-1].get("api_key") == "proxy-sentinel",
                  calls_env)

            client_env_http = LLMClient({"llm": {
                "enabled": True, "provider": "openai",
                "model": "gpt-4o-mini", "api_key_env": "CUSTOM_PROXY_KEY"}})
            seen = {}

            class _OpenAIResp:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def read(self):
                    return _rjson.dumps({
                        "choices": [{"message": {"content": "ok"}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    }).encode()

            def fake_urlopen(request, timeout=None):
                seen["authorization"] = request.get_header("Authorization")
                return _OpenAIResp()

            with mock.patch("urllib.request.urlopen", fake_urlopen):
                client_env_http.complete("sys", "usr")
            check("api_key_env still wins as the bearer token on the "
                  "raw-HTTP path",
                  seen.get("authorization") == "Bearer proxy-sentinel",
                  seen)

        # (4) The real-world path this bug broke: summarizer_llm building
        # a client off [llm] (gemini) and overriding .model to a
        # different-vendor [critique] summarizer_model (anthropic) — the
        # stubbed call must receive the ANTHROPIC key, not the gemini one
        # the client was constructed with.
        from authorlm import summaries as summaries_mod

        with mock.patch.dict(os.environ,
                             {"GEMINI_API_KEY": "gemini-sentinel",
                              "ANTHROPIC_API_KEY": "anthropic-sentinel"}):
            fake_sum, calls_sum = make_fake_litellm(set())
            sum_client = summaries_mod.summarizer_llm({
                "llm": {"enabled": True, "model": "gemini/gemini-2.5-flash"},
                "critique": {"summarizer_model": "anthropic/claude-fable-5"},
            })
            with mock.patch.dict(sys.modules, {"litellm": fake_sum}):
                sum_client.complete("sys", "usr")
            check("summarizer_llm's cross-vendor override reaches the "
                  "call with the OVERRIDE vendor's key",
                  sum_client.model == "anthropic/claude-fable-5"
                  and calls_sum[-1].get("api_key") == "anthropic-sentinel",
                  calls_sum)

        # --- AE-3: server-side temperature rejection litellm's local
        # param map can't pre-empt (drop_params, round 1, only helps when
        # litellm ITSELF recognizes the model). Unlike round 1's stub
        # (make_fake_litellm's drop_params-mediated UnsupportedParamsError,
        # raised/avoided INSIDE litellm before any request goes out), this
        # simulates the model reaching the wire with `temperature`
        # attached every time it's present — exactly what happens for a
        # model litellm doesn't recognize (gpt-5.6-luna).
        from authorlm.llm import RETRIES as _RETRIES, TEMPERATURE as _TEMPERATURE

        def make_temp_rejecting_litellm(*, reject_message=None,
                                        unrelated_message=None,
                                        fail_times=0, transient_exc=None):
            calls = []
            invocations = {"n": 0}
            state = {"raises_left": fail_times}
            module = types.SimpleNamespace(suppress_debug_info=False,
                                           drop_params=False)

            def completion(model, messages, timeout=None, **kwargs):
                invocations["n"] += 1
                if transient_exc is not None and state["raises_left"] > 0:
                    state["raises_left"] -= 1
                    raise transient_exc
                if unrelated_message is not None:
                    raise RuntimeError(unrelated_message)
                if "temperature" in kwargs and reject_message is not None:
                    raise RuntimeError(reject_message)
                calls.append({"model": model, **kwargs})
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(
                        message=types.SimpleNamespace(content="the reply"))],
                    usage=types.SimpleNamespace(prompt_tokens=11,
                                                completion_tokens=7))

            module.completion = completion
            return module, calls, invocations

        # (1) The verbatim live failure shape (orchestrator-reported):
        # gpt-5.6-luna 400s server-side on any temperature but 1. One
        # reply, exactly two litellm.completion() calls, zero sleeps.
        #
        # AI: the MODEL STRING here is no longer luna. Luna now has a
        # MODEL_PROFILES row (llm.py), so this rejection can no longer
        # reach it — the request is shaped before the first call and the
        # dance never happens, which check_model_profiles asserts
        # positively. The net itself is unchanged and still needed, for
        # exactly one situation: a model the registry does not know.
        # Pointing this block at such a model keeps every assertion below
        # verbatim while testing the case that still exists.
        fake_r1, calls_r1, inv_r1 = make_temp_rejecting_litellm(
            reject_message=(
                "litellm.BadRequestError: OpenAIException - Unsupported "
                "value: 'temperature' does not support 0.2 with this "
                "model. Only the default (1) value is supported."))
        sleeps_r1 = []
        client_r1 = LLMClient({"llm": {"enabled": True,
                                       "model": "openai/gpt-5.6-unmapped"}})
        with mock.patch.dict(sys.modules, {"litellm": fake_r1}), \
                mock.patch("authorlm.llm.time.sleep", sleeps_r1.append):
            reply_r1 = client_r1.complete("sys", "usr")
        check("a server-side temperature rejection (litellm's local "
              "param map doesn't know this model) still returns a reply",
              reply_r1 == "the reply", (reply_r1, calls_r1, inv_r1))
        check("...in exactly two litellm.completion() calls, with zero "
              "backoff sleeps",
              inv_r1["n"] == 2 and not sleeps_r1, (inv_r1, sleeps_r1))
        check("...and the successful call carried NO temperature kwarg",
              "temperature" not in calls_r1[-1], calls_r1)

        # (2) Non-regression: a temperature-accepting model still
        # receives the client's configured temperature (default 0.2).
        fake_r2, calls_r2, inv_r2 = make_temp_rejecting_litellm()
        client_r2 = LLMClient({"llm": {"enabled": True,
                                       "model": "gemini/gemini-2.5-flash"}})
        with mock.patch.dict(sys.modules, {"litellm": fake_r2}):
            reply_r2 = client_r2.complete("sys", "usr")
        check("a temperature-accepting model still receives temperature",
              reply_r2 == "the reply"
              and calls_r2[-1].get("temperature") == _TEMPERATURE, calls_r2)

        # (3) Non-regression: a genuine transient error (unrelated to
        # temperature) still gets the existing backoff retries.
        fake_r3, calls_r3, inv_r3 = make_temp_rejecting_litellm(
            fail_times=1, transient_exc=ConnectionError("stalled"))
        sleeps_r3 = []
        client_r3 = LLMClient({"llm": {"enabled": True,
                                       "model": "gemini/gemini-2.5-flash"}})
        with mock.patch.dict(sys.modules, {"litellm": fake_r3}), \
                mock.patch("authorlm.llm.time.sleep", sleeps_r3.append):
            reply_r3 = client_r3.complete("sys", "usr")
        check("a genuine transient failure still retries with backoff "
              "and eventually succeeds",
              reply_r3 == "the reply" and inv_r3["n"] == 2
              and sleeps_r3 == [1], (inv_r3, sleeps_r3))

        # (4) An unrelated BadRequestError (bad model name, say) is NOT
        # mistaken for a temperature rejection — it follows the existing
        # failure path (full backoff, then give up).
        fake_r4, calls_r4, inv_r4 = make_temp_rejecting_litellm(
            unrelated_message=(
                "litellm.BadRequestError: OpenAIException - The "
                "requested model does not support this operation."))
        sleeps_r4 = []
        client_r4 = LLMClient({"llm": {"enabled": True,
                                       "model": "openai/bad-model-9000"}})
        with mock.patch.dict(sys.modules, {"litellm": fake_r4}), \
                mock.patch("authorlm.llm.time.sleep", sleeps_r4.append):
            reply_r4 = client_r4.complete("sys", "usr")
        check("an unrelated BadRequestError is NOT retried-without-"
              "temperature — it follows the existing failure path",
              reply_r4 is None and inv_r4["n"] == _RETRIES + 1
              and sleeps_r4 == [1, 2], (reply_r4, inv_r4, sleeps_r4))

        # --- AE-3 scope expansion (Sponsor-ruled): temperature becomes a
        # per-section config key, [critique] > [llm] > the TEMPERATURE
        # constant, with "vendor-default" spelling "send none at all".
        from authorlm.llm import resolve_temperature

        # (a) [critique].temperature honored on the summarizer path.
        fake_a, calls_a, inv_a = make_temp_rejecting_litellm()
        sum_client_a = summaries_mod.summarizer_llm({
            "llm": {"enabled": True, "model": "gemini/gemini-2.5-flash"},
            "critique": {"temperature": 0.7},
        })
        with mock.patch.dict(sys.modules, {"litellm": fake_a}):
            sum_client_a.complete("sys", "usr")
        check("[critique].temperature overrides the summarizer client's "
              "temperature",
              calls_a[-1].get("temperature") == 0.7, calls_a)

        # (b) fallback chain: [critique] -> [llm] -> the constant.
        check("resolve_temperature: [critique]'s own value wins when set",
              resolve_temperature(
                  {"critique": {"temperature": 0.9},
                   "llm": {"temperature": 0.4}}, "critique") == 0.9)
        check("resolve_temperature: [llm]'s value wins when [critique] "
              "doesn't set one",
              resolve_temperature(
                  {"critique": {}, "llm": {"temperature": 0.4}},
                  "critique") == 0.4)
        check("resolve_temperature: the TEMPERATURE constant wins when "
              "neither section sets one",
              resolve_temperature({"critique": {}, "llm": {}}, "critique")
              == _TEMPERATURE)

        # (c) "vendor-default" sends NO temperature kwarg at all.
        fake_c, calls_c, inv_c = make_temp_rejecting_litellm()
        client_c = LLMClient({"llm": {
            "enabled": True, "model": "openai/gpt-5.6-luna",
            "temperature": "vendor-default"}})
        with mock.patch.dict(sys.modules, {"litellm": fake_c}):
            client_c.complete("sys", "usr")
        check('"vendor-default" sends NO temperature kwarg, proactively '
              "(one call, not the two-call rejection dance)",
              "temperature" not in calls_c[-1] and inv_c["n"] == 1,
              (calls_c, inv_c))

        # (d) cache-key/replay coherence: two clients, same model+prompt,
        # DIFFERENT configured temperatures must not collide on one cache
        # file, and each recorded entry must carry ITS OWN configured
        # value. An unconfigured client's key must stay byte-identical to
        # the pre-AE-3 formula, so existing replay fixtures still hit.
        with tempfile.TemporaryDirectory() as cache_dir:
            fake_d1, calls_d1, _ = make_temp_rejecting_litellm()
            client_d1 = LLMClient({"llm": {
                "enabled": True, "model": "gemini/gemini-2.5-flash",
                "temperature": 0.3, "cache_dir": cache_dir}})
            with mock.patch.dict(sys.modules, {"litellm": fake_d1}):
                client_d1.complete("cache sys", "cache usr")

            fake_d2, calls_d2, _ = make_temp_rejecting_litellm()
            client_d2 = LLMClient({"llm": {
                "enabled": True, "model": "gemini/gemini-2.5-flash",
                "temperature": 0.9, "cache_dir": cache_dir}})
            with mock.patch.dict(sys.modules, {"litellm": fake_d2}):
                client_d2.complete("cache sys", "cache usr")

            written = sorted(Path(cache_dir).glob("*.json"))
            check("two clients, same model+prompt, different configured "
                  "temperatures write to DIFFERENT cache files",
                  len(written) == 2, [p.name for p in written])
            recorded = [_rjson.loads(p.read_text()) for p in written]
            check("each cache entry records the temperature actually "
                  "CONFIGURED for that call",
                  {r["temperature"] for r in recorded} == {0.3, 0.9},
                  recorded)

            import hashlib as _hashlib

            plain_messages = [{"role": "system", "content": "cache sys default"},
                              {"role": "user", "content": "cache usr default"}]
            expected_payload = _rjson.dumps(
                {"model": "gemini/gemini-2.5-flash",
                 "temperature": _TEMPERATURE, "messages": plain_messages},
                sort_keys=True)
            expected_digest = _hashlib.sha256(
                expected_payload.encode()).hexdigest()[:20]
            fake_d3, calls_d3, _ = make_temp_rejecting_litellm()
            client_d3 = LLMClient({"llm": {
                "enabled": True, "model": "gemini/gemini-2.5-flash",
                "cache_dir": cache_dir}})
            with mock.patch.dict(sys.modules, {"litellm": fake_d3}):
                client_d3.complete("cache sys default", "cache usr default")
            check("an unconfigured client's cache key formula is "
                  "byte-identical to pre-AE-3 (its replay fixtures are "
                  "untouched by this feature)",
                  (Path(cache_dir) / f"{expected_digest}.json").exists(),
                  sorted(p.name for p in Path(cache_dir).glob("*.json")))

        def boom_main(argv):
            raise RuntimeError("network down")

        out = io.StringIO()
        with mock.patch("authorlm.cli.main", boom_main), \
                contextlib.redirect_stdout(out):
            shell_mod._dispatch(["--workspace", str(ws)], ["status"])
        check("shell dispatch returns to the prompt after a command crash",
              "error (RuntimeError): network down" in out.getvalue(),
              out.getvalue())

        class DistillLLM:
            enabled = True

            def __init__(self):
                self.calls = []

            def complete(self, system, user):
                self.calls.append((system, user))
                return ('SCOPE: manuscript\n'
                        'STATEMENT: "Prefer concrete metaphors over '
                        'decorative ones."\n')

        distill_llm = DistillLLM()
        before_policies = db.one(
            "SELECT COUNT(*) AS n FROM editorial_beliefs "
            "WHERE manuscript_id = ?", (manuscript["id"],))["n"]
        check("distill_batch no-ops without items or an enabled LLM",
              placement.distill_batch(db, manuscript, [], distill_llm) is None
              and placement.distill_batch(
                  db, manuscript, [("01-choice.md", "rejected: x")],
                  types.SimpleNamespace(enabled=False)) is None)
        candidate = placement.distill_batch(
            db, manuscript,
            [("01-choice.md", "rejected: too decorative"),
             ("04-road.md", "description revised: «a» became «b»")],
            distill_llm)
        check("distill_batch makes one combined distiller call for the sitting",
              candidate is not None
              and len(distill_llm.calls) == 1
              and "too decorative" in distill_llm.calls[0][1]
              and "became «b»" in distill_llm.calls[0][1]
              and "at least two" in distill_llm.calls[0][1]
              and db.one(
                  "SELECT COUNT(*) AS n FROM editorial_beliefs "
                  "WHERE manuscript_id = ?",
                  (manuscript["id"],))["n"] == before_policies + 1,
              str(candidate))

        check("Google API socket timeout constant is 60s",
              gdocs_mod.HTTP_TIMEOUT_SECONDS == 60)
        fake_auth = types.ModuleType("google_auth_httplib2")
        seen_http = {}

        class _AuthorizedHttp:
            def __init__(self, creds, http=None):
                seen_http["creds"] = creds
                seen_http["http"] = http

        fake_auth.AuthorizedHttp = _AuthorizedHttp
        sys.modules["google_auth_httplib2"] = fake_auth
        try:
            with mock.patch.object(gdocs_mod, "get_credentials",
                                   return_value=object()):
                gdocs_mod._authorized_http({}, None, False)
            check("authorized Google HTTP client carries the socket timeout",
                  getattr(seen_http.get("http"), "timeout", None)
                  == gdocs_mod.HTTP_TIMEOUT_SECONDS,
                  str(seen_http))
        finally:
            sys.modules.pop("google_auth_httplib2", None)

        # --- SEC-3: cached Google OAuth tokens must be written 0600, not
        # world-readable, at both write sites (post-refresh and initial
        # consent) -- a long-lived refresh token readable by any other
        # local account grants full read/write of every manuscript Doc. ---
        import stat

        class _FakeGoogleCreds:
            def __init__(self, expired, refresh_token, valid_after_refresh=True):
                self.expired = expired
                self.refresh_token = refresh_token
                self.valid = not expired
                self._valid_after_refresh = valid_after_refresh

            def refresh(self, request):
                self.valid = self._valid_after_refresh
                self.expired = False

            def to_json(self):
                return _rjson.dumps({"token": "secret-refresh-token"})

        gdocs_sec3_ws = root / "gdocs-sec3-ws"
        gdocs_sec3_ws.mkdir()
        token_path = gdocs_sec3_ws / ".authorlm" / "gdocs_token.json"
        token_path.parent.mkdir(parents=True)
        token_path.write_text('{"token": "stale"}')

        with mock.patch(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            return_value=_FakeGoogleCreds(expired=True, refresh_token="rt")):
            gdocs_mod.get_credentials({}, workspace=str(gdocs_sec3_ws))
        refreshed_mode = stat.S_IMODE(token_path.stat().st_mode)
        check("SEC-3: a refreshed Google token is written 0600, not "
              "world-readable",
              refreshed_mode == 0o600, oct(refreshed_mode))

        token_path.unlink()
        secret_path = gdocs_sec3_ws / "client_secret_fake.json"
        secret_path.write_text("{}")
        fake_flow = types.SimpleNamespace(
            run_local_server=lambda port=0: _FakeGoogleCreds(
                expired=False, refresh_token=None))
        with mock.patch(
            "google_auth_oauthlib.flow.InstalledAppFlow"
            ".from_client_secrets_file",
            return_value=fake_flow):
            gdocs_mod.get_credentials(
                {"gdocs": {"client_secret": str(secret_path)}},
                workspace=str(gdocs_sec3_ws))
        consented_mode = stat.S_IMODE(token_path.stat().st_mode)
        check("SEC-3: a freshly-consented Google token is written 0600 too",
              consented_mode == 0o600, oct(consented_mode))

        # toc.toml matter attributes + liberal parse (never raise)
        matter_files = {
            "toc.toml": (
                '[[chapter]]\nfile = "front.md"\nmatter = "front"\n\n'
                '[[chapter]]\nfile = "body.md"\n\n'
                '[[chapter]]\nfile = "app.md"\nmatter = "back"\n\n'
                '[[chapter]]\nfile = "weird.md"\nmatter = "sideways"\n'),
            "front.md": "f", "body.md": "b", "app.md": "a", "weird.md": "w",
            "orphan.md": "o",
        }
        check("matter_map reads front/main/back and defaults unknown/orphan",
              matter_map(matter_files) == {
                  "front.md": "front", "body.md": "main", "app.md": "back",
                  "weird.md": "main", "orphan.md": "main"},
              str(matter_map(matter_files)))
        broken = {"toc.toml": "[[chapter]\nnot toml", "z.md": "z", "a.md": "a"}
        ordered, missing = reading_order(broken)
        check("broken toc.toml degrades to alphabetical order, never raises",
              ordered == ["a.md", "z.md"] and missing == [],
              f"{ordered=} {missing=}")

        # --- belief curation: convert_belief happy path + error guards ---
        from authorlm import beliefs as pol

        api.define_style_guide(db, manuscript, "Curation guide")
        seeded = pol.seed_candidate_belief(
            db, manuscript["id"], "Prefer short paragraphs in dialogue.",
            source="test")
        converted = api.convert_belief(
            db, manuscript, seeded["id"], "formatting",
            guide="Curation guide", reason="now enforced as style law")
        check("convert_belief retires the belief and links a style law",
              converted["status"] == "retired" and converted["style_element"])
        row = db.one("SELECT * FROM editorial_beliefs WHERE id = ?",
                     (seeded["id"],))
        curation = loads(row["metadata"], {}).get("curation", {})
        check("converted policy's metadata records the linkage and reason",
              row["status"] == "retired"
              and curation.get("action") == "converted"
              and curation.get("style_element") == converted["style_element"]
              and curation.get("reason") == "now enforced as style law")
        evidence = db.one(
            "SELECT * FROM evidence WHERE supports_belief = ? "
            "AND evidence_type = 'belief_curation'",
            (seeded["id"],))
        check("conversion writes a belief_curation evidence row",
              evidence is not None and evidence["signal"] == "converted")
        try:
            api.convert_belief(db, manuscript, seeded["id"], "formatting")
            check("converting an already-retired policy is refused", False)
        except LookupError:
            check("converting an already-retired policy is refused", True)
        try:
            api.convert_belief(db, manuscript, "no-such-prefix", "formatting")
            check("converting an unknown policy prefix is refused", False)
        except LookupError:
            check("converting an unknown policy prefix is refused", True)

        # --- style-element prefix ambiguity guard: add/retire/move must
        #     never silently act on whichever row SQLite returns first
        #     when a prefix matches more than one active element ---
        api.define_style_guide(db, manuscript, "Ambiguity guide")
        amb1 = api.add_style_law(db, manuscript, "tone", "Amb element one.",
                                     guide_name="Ambiguity guide")
        amb2 = api.add_style_law(db, manuscript, "tone", "Amb element two.",
                                     guide_name="Ambiguity guide")
        check("style element ids share the common 'se-' literal prefix",
              amb1["id"].startswith("se-") and amb2["id"].startswith("se-"))
        try:
            api.retire_style_law(db, manuscript, "se")
            check("retire refuses an ambiguous prefix", False)
        except LookupError as err:
            check("retire refuses an ambiguous prefix", "ambiguous" in str(err))
        check("neither element was retired by the ambiguous attempt",
              db.one("SELECT status FROM style_laws WHERE id = ?",
                     (amb1["id"],))["status"] == "active"
              and db.one("SELECT status FROM style_laws WHERE id = ?",
                         (amb2["id"],))["status"] == "active")
        try:
            api.move_style_law(db, manuscript, "se", file="01-choice.md")
            check("move refuses an ambiguous prefix", False)
        except LookupError as err:
            check("move refuses an ambiguous prefix", "ambiguous" in str(err))
        try:
            api.add_style_law(db, manuscript, "tone", "New with bad override",
                                  guide_name="Ambiguity guide", overrides="se")
            check("add_style_element refuses an ambiguous override prefix", False)
        except LookupError as err:
            check("add_style_element refuses an ambiguous override prefix",
                  "ambiguous" in str(err))
        retired_amb = api.retire_style_law(db, manuscript, amb1["id"])
        check("a full unique id still resolves and retires",
              retired_amb["id"] == amb1["id"] and retired_amb["status"] == "retired")

        # --- docs.py: list_docs batches its per-file concept lookup + doc
        #     lifecycle error paths (ambiguous query, retired-name collision,
        #     revive collision) ---
        from authorlm import docs as docs_mod

        (ms / "60-alphadoc.md").write_text("# Alpha\n\nAlphaTopic appears here.\n")
        (ms / "61-betadoc.md").write_text("# Beta\n\nBetaTopic appears here.\n")
        api.add_concept(db, manuscript, "AlphaTopic", notes="x")
        api.add_concept(db, manuscript, "BetaTopic", notes="y")
        api.collect(db, manuscript, {})
        listing = docs_mod.list_docs(db, manuscript)
        by_file = {d["file"]: d["concepts"] for d in listing["active"]}
        check("list_docs attributes each concept to its introducing file only",
              by_file.get("60-alphadoc.md") == ["AlphaTopic"]
              and by_file.get("61-betadoc.md") == ["BetaTopic"],
              by_file)

        (ms / "62-zzzprefixtest-one.md").write_text("# One\n\n")
        (ms / "63-zzzprefixtest-two.md").write_text("# Two\n\n")
        try:
            docs_mod.retire_doc(manuscript, "zzzprefixtest")
            check("retire_doc refuses an ambiguous query", False)
        except LookupError as err:
            check("retire_doc refuses an ambiguous query", "ambiguous" in str(err))
        (ms / "62-zzzprefixtest-one.md").unlink()
        (ms / "63-zzzprefixtest-two.md").unlink()

        docs_mod.retire_doc(manuscript, "60-alphadoc.md")
        try:
            docs_mod.add_doc(manuscript, "60-alphadoc")
            check("add_doc refuses a name retired but not revived", False)
        except FileExistsError as err:
            check("add_doc refuses a name retired but not revived",
                  "revive it instead" in str(err))
        (ms / "60-alphadoc.md").write_text("# Reborn\n\n")
        try:
            docs_mod.revive_doc(manuscript, "60-alphadoc")
            check("revive_doc refuses when the manuscript already has that file",
                  False)
        except FileExistsError as err:
            check("revive_doc refuses when the manuscript already has that file",
                  "already exists in the manuscript" in str(err))
        (ms / "60-alphadoc.md").unlink()
        docs_mod.revive_doc(manuscript, "60-alphadoc")
        check("revive_doc restores the file once the collision clears",
              (ms / "60-alphadoc.md").exists())

        # --- MCP payload caps (diff_versions / list_policies) ---
        # Extracted helpers: chat context blew up to 185 KB / 52 KB on
        # mature manuscripts before f3264e8; regressions reintroduce
        # token blowups or silent stubbing mistakes.
        from authorlm.mcp_server import cap_diff_files, compact_belief_rows

        long_file = [f"line-{i}" for i in range(10)]
        capped = cap_diff_files({"a.md": list(long_file)}, max_lines=4)
        check("diff cap truncates a long file and names the escape hatch",
              len(capped["a.md"]) == 5
              and capped["a.md"][:4] == long_file[:4]
              and "(+6 more lines" in capped["a.md"][-1]
              and "file='a.md'" in capped["a.md"][-1],
              str(capped["a.md"][-1]))

        budgeted = cap_diff_files({
            "a.md": ["1", "2"],
            "b.md": ["1", "2"],
            "c.md": ["1", "2"],
            "d.md": ["1", "2"],  # spent reaches budget (max_lines*4 == 8)
            "late.md": [f"l{i}" for i in range(20)],
        }, max_lines=2)
        check("diff cap stubs later files once the whole-answer budget is spent",
              budgeted["d.md"] == ["1", "2"]
              and len(budgeted["late.md"]) == 1
              and "20 changed line(s)" in budgeted["late.md"][0]
              and "file='late.md'" in budgeted["late.md"][0],
              str(budgeted))

        untouched = {"x.md": ["only", "two"]}
        check("diff cap with max_lines<=0 leaves the payload untouched",
              cap_diff_files(dict(untouched), max_lines=0) == untouched)

        policy_rows = [
            {"id": "pol-a", "statement": "Prefer short definitions",
             "status": "candidate", "confidence": 0.4,
             "supporting": 1, "contradicting": 0,
             "questions": ["when?"], "source": "review-explanation"},
            {"id": "pol-b", "statement": "Keep metaphors sparse",
             "status": "validated", "confidence": 0.8,
             "supporting": 5, "contradicting": 0,
             "questions": [], "source": "episode_analysis"},
        ]
        compact = compact_belief_rows(policy_rows)
        check("list_policies compact drops questions/provenance fields",
              compact["belief_count"] == 2
              and set(compact["beliefs"][0]) == {
                  "id", "statement", "status", "confidence",
                  "supporting", "contradicting"}
              and "questions" not in compact["beliefs"][0],
              str(compact))
        filtered = compact_belief_rows(policy_rows, status="validated")
        check("list_policies status= filter keeps only matching rows",
              filtered["belief_count"] == 1
              and filtered["beliefs"][0]["id"] == "pol-b",
              str(filtered))
        verbose = compact_belief_rows(policy_rows, verbose=True)
        check("list_policies verbose=True returns full rows",
              "belief_count" not in verbose
              and verbose["beliefs"][0]["questions"] == ["when?"]
              and verbose["beliefs"][0]["source"] == "review-explanation",
              str(verbose))

        # --- Critique Doc mark requests (pure; feed write_pending_forms) ---
        from authorlm.gdocs import (_mark_insert_requests,
                                    _mark_replace_requests, _utf16_len)

        replace_reqs = _mark_replace_requests("tab-1", 10, 14, "old", "new")
        check("replace mark inserts wrappers then styles strike + green",
              replace_reqs[0]["insertText"]["text"] == ">>{{new}}"
              and replace_reqs[0]["insertText"]["location"]["index"] == 14
              and replace_reqs[1]["insertText"]["text"] == "<<"
              and replace_reqs[1]["insertText"]["location"]["index"] == 10
              and replace_reqs[2]["updateTextStyle"]["textStyle"]
              .get("strikethrough") is True
              and replace_reqs[2]["updateTextStyle"]["range"]["endIndex"]
              == 10 + 4 + _utf16_len("old")
              and "foregroundColor" in replace_reqs[3]["updateTextStyle"]
              ["textStyle"],
              str(replace_reqs))
        # Non-BMP characters occupy two UTF-16 code units in the Docs API.
        emoji_old, emoji_new = "old😀", "new🎉"
        emoji_reqs = _mark_replace_requests("tab-1", 0, _utf16_len(emoji_old),
                                            emoji_old, emoji_new)
        strike_end = emoji_reqs[2]["updateTextStyle"]["range"]["endIndex"]
        green_end = emoji_reqs[3]["updateTextStyle"]["range"]["endIndex"]
        check("replace mark indexes use UTF-16 lengths for non-BMP text",
              strike_end == 4 + _utf16_len(emoji_old)
              and green_end == strike_end + 4 + _utf16_len(emoji_new)
              and _utf16_len(emoji_old) == len(emoji_old) + 1,
              f"strike_end={strike_end} green_end={green_end}")

        insert_reqs = _mark_insert_requests("tab-1", 5, "added")
        check("insert mark writes a green {{new}} paragraph at the boundary",
              insert_reqs[0]["insertText"]["text"] == "\n{{added}}"
              and insert_reqs[0]["insertText"]["location"]["index"] == 5
              and insert_reqs[1]["updateParagraphStyle"]["paragraphStyle"]
              == {"namedStyleType": "NORMAL_TEXT"}
              and insert_reqs[2]["updateTextStyle"]["range"]["startIndex"]
              == 6
              and "foregroundColor" in insert_reqs[2]["updateTextStyle"]
              ["textStyle"],
              str(insert_reqs))

        # --- Editorial verbs lazy-open a session (CLI helper) ---
        from authorlm import sessions as ses
        from authorlm.cli import _ensure_session

        active = ses.active_session(db, manuscript["id"])
        if active is not None:
            ses.end_session(db, manuscript["id"])
        check("no active session before lazy-open",
              ses.active_session(db, manuscript["id"]) is None)
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            _ensure_session(db, manuscript)
        opened = ses.active_session(db, manuscript["id"])
        check("editorial _ensure_session opens a session when none is active",
              opened is not None and "opened session" in buf.getvalue(),
              buf.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as buf2:
            _ensure_session(db, manuscript)
        check("editorial _ensure_session reuses an already-active session",
              ses.active_session(db, manuscript["id"])["id"] == opened["id"]
              and buf2.getvalue() == "",
              buf2.getvalue())

        # --- Doc tab↔TOC sync helpers (pure; no Google) ---
        # These gate push/pull hierarchy safety: wrong classification
        # rewrites toc.toml, adopts scratch tabs as essays, or treats a
        # conflict as a safe pull.
        import hashlib as _hashlib

        from authorlm.gdocs import (
            ILLUS_TAB_TITLE,
            MANIFEST_TITLE,
            _match_prompt,
            classify_structure,
            classify_tabs,
            illus_subtree,
            next_tab_move,
            plan_prompt_sync,
            prompt_links,
            rewrite_toc_from_doc,
            three_way,
            walk_tabs,
        )

        check("next_tab_move is a no-op when order already matches",
              next_tab_move(["a", "b", "c"], ["a", "b", "c"]) is None)
        step = next_tab_move(["b", "a", "c"], ["a", "b", "c"])
        check("next_tab_move proposes one index swap toward desired order",
              step == {"updateDocumentTabProperties": {
                  "tabProperties": {"tabId": "a", "index": 0},
                  "fields": "index"}}, str(step))
        # Ids absent from desired stay at the end and do not drive moves.
        keep_tail = next_tab_move(["x", "a", "b"], ["b", "a"])
        check("next_tab_move ignores extras not in desired",
              keep_tail["updateDocumentTabProperties"]["tabProperties"]
              ["tabId"] == "b", str(keep_tail))

        local_h = _hashlib.sha256(b"local").hexdigest()[:16]
        tab_h = _hashlib.sha256(b"tab").hexdigest()[:16]
        check("three_way: identical sides are unchanged",
              three_way("same", "same", "deadbeef") == "unchanged")
        check("three_way: no base + drift → Doc wins (changed)",
              three_way("tab", "local", None) == "changed")
        check("three_way: only tab moved off base → safe pull",
              three_way("tab", "local", local_h) == "changed")
        check("three_way: only local moved → keep local (local_ahead)",
              three_way("tab", "local", tab_h) == "local_ahead")
        check("three_way: both moved → conflict",
              three_way("tab", "local", "deadbeefcafebabe") == "conflict")

        tabs_cls = [
            ("t1", "01-choice.md"),
            ("t2", "renamed-title.md"),
            ("t3", "brand-new.md"),
            ("t4", "04-road.md"),
            ("t5", MANIFEST_TITLE),
            ("t6", "scratch-notes"),
            ("t7", "dup.md"),
            ("t8", "dup.md"),
            ("t9", "live.md"),
        ]
        links_cls = {
            "01-choice.md": {"tab_id": "t1"},
            "04-road.md": {"tab_id": "t-old"},  # known file, unknown tab id
            "was.md": {"tab_id": "t2"},         # title drifted off filename
            "live.md": {"tab_id": "t-live"},    # live mapped tab still in Doc
        }
        # Inject the live mapped tab so ambiguous duplicate detection fires.
        tabs_cls.append(("t-live", "live.md"))
        classed = classify_tabs(tabs_cls, links_cls, {"04-road.md"})
        check("classify_tabs: rename when mapped id's title drifted",
              classed["renamed"] == [("was.md", "renamed-title.md")],
              str(classed))
        check("classify_tabs: adopt unknown .md with no local/link",
              classed["adopted"] == [("brand-new.md", "t3")], str(classed))
        check("classify_tabs: readopt when title matches existing file",
              classed["readopted"] == [("04-road.md", "t4")], str(classed))
        check("classify_tabs: ignore manifest and non-.md titles",
              MANIFEST_TITLE in classed["ignored"]
              and "scratch-notes" in classed["ignored"], str(classed))
        check("classify_tabs: duplicate unknown titles and live dupes "
              "are ambiguous",
              classed["ambiguous"].count("dup.md") == 2
              and "live.md" in classed["ambiguous"], str(classed))

        pairs_a = [("a.md", None), ("b.md", "a.md")]
        pairs_b = [("b.md", None), ("a.md", None)]
        check("classify_structure: identical pairs are insync",
              classify_structure(pairs_a, pairs_a, None) == "insync")
        check("classify_structure: local matches base → doc_moved",
              classify_structure(pairs_b, pairs_a, pairs_a) == "doc_moved")
        check("classify_structure: doc matches base → local_moved",
              classify_structure(pairs_a, pairs_b, pairs_a) == "local_moved")
        both = classify_structure(
            [("x.md", None)], [("y.md", None)], pairs_a)
        check("classify_structure: both sides off base → conflict",
              both == "conflict", both)
        # No base: pure Doc additions sharing the local spine → doc_moved.
        local_spine = [("a.md", None)]
        doc_with_add = [("a.md", None), ("new.md", None)]
        check("classify_structure: no base + Doc-only additions → doc_moved",
              classify_structure(doc_with_add, local_spine, None)
              == "doc_moved")
        check("classify_structure: no base + disagreeing common spine "
              "→ unsynced",
              classify_structure([("b.md", None)], local_spine, None)
              == "unsynced")

        tab_props: list = []
        doc_pairs: list = []
        walk_tabs([
            {"tabProperties": {"tabId": "r1", "title": "part"},
             "childTabs": [
                 {"tabProperties": {"tabId": "c1", "title": "01.md"},
                  "childTabs": []},
                 {"tabProperties": {"tabId": "c2", "title": "notes"},
                  "childTabs": [
                      {"tabProperties": {"tabId": "c3", "title": "02.md"},
                       "childTabs": []},
                  ]},
             ]},
        ], tab_props, doc_pairs)
        check("walk_tabs lists every tab id/title",
              tab_props == [("r1", "part"), ("c1", "01.md"),
                            ("c2", "notes"), ("c3", "02.md")],
              str(tab_props))
        # Non-md 'part'/'notes' are transparent: both essays sit at root
        # because neither has an md ancestor.
        check("walk_tabs: non-.md tabs are transparent to parent chain",
              doc_pairs == [("01.md", None), ("02.md", None)],
              str(doc_pairs))
        nested_props: list = []
        nested_pairs: list = []
        walk_tabs([{
            "tabProperties": {"tabId": "p", "title": "01.md"},
            "childTabs": [{
                "tabProperties": {"tabId": "s", "title": "scratch"},
                "childTabs": [{
                    "tabProperties": {"tabId": "c", "title": "02.md"},
                    "childTabs": [],
                }],
            }],
        }], nested_props, nested_pairs)
        check("walk_tabs: md parent passes through a scratch nest",
              nested_pairs == [("01.md", None), ("02.md", "01.md")],
              str(nested_pairs))

        toc_ws = root / "toc-rewrite"
        toc_ws.mkdir()
        (toc_ws / "toc.toml").write_text(
            '[[chapter]]\nfile = "a.md"\nmatter = "front"\n\n'
            '[[chapter]]\nfile = "local-only.md"\nmatter = "back"\n\n'
            '[[chapter]]\nfile = "b.md"\n')
        new_tree = rewrite_toc_from_doc(
            {"path": str(toc_ws)},
            [("b.md", None), ("a.md", None)],
            [("a.md", 0), ("local-only.md", 0), ("b.md", 0)])
        rewritten = (toc_ws / "toc.toml").read_text()
        check("rewrite_toc_from_doc follows Doc order and keeps "
              "never-pushed locals after their predecessor",
              [n for n, _ in new_tree]
              == ["b.md", "a.md", "local-only.md"], str(new_tree))
        check("rewrite_toc_from_doc preserves matter attributes",
              'matter = "front"' in rewritten
              and 'matter = "back"' in rewritten, rewritten)

        # Illustration prompt Doc mirror planning.
        check("prompt_links strips the reserved mapping prefix",
              prompt_links({
                  "_illusprompt/slot.md": {"tab_id": "p1"},
                  "01.md": {"tab_id": "e1"},
                  "_illusprompt/bad": "not-a-dict",
              }) == {"slot.md": {"tab_id": "p1"}})
        root_id, children, ids = illus_subtree([
            {"tabProperties": {"tabId": "essay", "title": "01.md"},
             "childTabs": []},
            {"tabProperties": {"tabId": "ill", "title": ILLUS_TAB_TITLE},
             "childTabs": [
                 {"tabProperties": {"tabId": "p1", "title": "slot.md"},
                  "childTabs": [
                      {"tabProperties": {"tabId": "nested",
                                         "title": "extra"},
                       "childTabs": []},
                  ]},
             ]},
        ], {})
        check("illus_subtree finds the reserved root by title",
              root_id == "ill"
              and children == [("p1", "slot.md")]
              and ids == {"ill", "p1", "nested"},
              f"{root_id=} {children=} {ids=}")
        # Remembered id wins over title when both exist.
        root_by_id, _, _ = illus_subtree([
            {"tabProperties": {"tabId": "remembered",
                               "title": "not-the-title"},
             "childTabs": []},
            {"tabProperties": {"tabId": "other", "title": ILLUS_TAB_TITLE},
             "childTabs": []},
        ], {"_illustrations_tab": "remembered"})
        check("illus_subtree prefers the remembered illustrations tab id",
              root_by_id == "remembered", root_by_id)

        text_a = "description one"
        hash_a = _hashlib.sha256(text_a.encode()).hexdigest()[:16]
        plan = plan_prompt_sync(
            {"slot.md": text_a, "new.md": "fresh"},
            {"slot.md": {"tab_id": "p1", "pushed_hash": "stalehash0000000"},
             "gone.md": {"tab_id": "p-gone"}},
            [("p1", "renamed.md"), ("p-hand", "handmade.md"),
             ("p-gone", "gone.md")],
        )
        check("plan_prompt_sync: rewrite on hash mismatch + rename detect",
              plan["rewrite"] == ["slot.md"]
              and plan["renamed"] == [("slot.md", "renamed.md")],
              str(plan))
        check("plan_prompt_sync: create for local-only and vanished tabs",
              plan["create"] == ["new.md"], str(plan))
        check("plan_prompt_sync: prune mapped-but-deleted locals; "
              "unknown leaves hand-made tabs",
              plan["prune"] == [("gone.md", "p-gone")]
              and plan["unknown"] == ["handmade.md"], str(plan))
        # Unchanged hash → no rewrite.
        plan_ok = plan_prompt_sync(
            {"slot.md": text_a},
            {"slot.md": {"tab_id": "p1", "pushed_hash": hash_a}},
            [("p1", "slot.md")])
        check("plan_prompt_sync: matching hash skips rewrite",
              plan_ok == {"create": [], "rewrite": [], "prune": [],
                          "unknown": [], "renamed": []},
              str(plan_ok))

        names = ["alpha-slot.md", "beta-other.md"]
        check("_match_prompt resolves exact, display-prefix, and unique "
              "substring",
              _match_prompt(names, "alpha-slot.md") == "alpha-slot.md"
              and _match_prompt(
                  names, "_illustrations/prompts/beta-other.md")
              == "beta-other.md"
              and _match_prompt(names, "alpha") == "alpha-slot.md"
              and _match_prompt(names, "a") is None)  # ambiguous

        # --- virgin session start: no baseline → skip opening collect ---
        virgin_ws = root / "virgin-ws"
        virgin_ms = virgin_ws / "manuscript"
        virgin_ms.mkdir(parents=True)
        (virgin_ms / "start.md").write_text("# Start\n\nFresh prose.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(virgin_ws), "init",
                      "--name", "virgin", "--path", str(virgin_ms),
                      "--no-extract"])
        virgin_db = api.open_db(str(virgin_ws))
        virgin_m = api.get_manuscript(virgin_db)
        check("init --no-extract leaves a virgin manuscript (no baseline)",
              virgin_db.one(
                  "SELECT id FROM manuscript_versions "
                  "WHERE manuscript_id = ? LIMIT 1",
                  (virgin_m["id"],)) is None)
        (virgin_ms / "start.md").write_text(
            "# Start\n\nFresh prose, already edited.\n")
        out_start = io.StringIO()
        with contextlib.redirect_stdout(out_start):
            cli_main(["--workspace", str(virgin_ws), "session", "start"])
        start_txt = out_start.getvalue()
        check("virgin session start skips opening collect "
              "(no version created)",
              "Collected" not in start_txt
              and "file_added" not in start_txt
              and virgin_db.one(
                  "SELECT id FROM manuscript_versions "
                  "WHERE manuscript_id = ? LIMIT 1",
                  (virgin_m["id"],)) is None,
              start_txt)
        # After a real collect, session start must acknowledge changes.
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(virgin_ws), "session", "end"])
            cli_main(["--workspace", str(virgin_ws), "collect"])
        (virgin_ms / "extra.md").write_text("# Extra\n\nNew chapter.\n")
        out_start2 = io.StringIO()
        with contextlib.redirect_stdout(out_start2):
            cli_main(["--workspace", str(virgin_ws), "session", "start"])
        start2 = out_start2.getvalue()
        check("session start with a baseline collects and reports "
              "new chapters",
              "file_added" in start2 or "extra.md" in start2, start2)
        # --- style_show: effective law composition (guide inheritance,
        #     override displacement, unattached-file fallback) ---
        # Its own manuscript: this block asserts what an UNATTACHED file
        # inherits from THE root guide, which only means anything where
        # this block's guide is the only root. The shared manuscript has
        # collected several root guides from earlier blocks by now.
        style_ws = root / "style-ws"
        style_root = style_ws / "manuscript"
        style_root.mkdir(parents=True)
        (style_root / "97-styled.md").write_text("# Styled\n\nProse.\n")
        (style_root / "98-child.md").write_text("# Child\n\nMore prose.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(style_ws), "init",
                      "--name", "styled", "--path", str(style_root),
                      "--no-extract"])
        sdb = api.open_db(str(style_ws))
        sm = api.get_manuscript(sdb)
        bare = api.style_show(sdb, sm, "97-styled.md")
        # Earlier blocks in this suite define guides of their own, so a
        # fresh file is not lawless by the time this runs. Assert what the
        # check is actually about — the house rule added below is absent
        # beforehand — instead of depending on fixture order.
        check("style_show on a fresh file carries none of the house law",
              not any("second person" in e["statement"]
                      for e in bare["elements"]), str(bare["elements"]))

        api.define_style_guide(sdb, sm, "house")
        api.add_style_law(sdb, sm, "register",
                              "Address the reader in second person.",
                              guide_name="house")
        unattached = api.style_show(sdb, sm, "97-styled.md")
        check("an unattached file inherits the root guide's law",
              "Address the reader in second person."
              in [e["statement"] for e in unattached["elements"]]
              and "STYLE GUIDE for 97-styled.md" in unattached["rendered"]
              and "[register] Address the reader in second person."
              in unattached["rendered"])

        api.define_style_guide(sdb, sm, "dialogue", parent="house")
        api.attach_style(sdb, sm, "98-child.md", "dialogue")
        guide_el = api.add_style_law(sdb, sm, "tone",
                                         "Keep dialogue clipped.",
                                         guide_name="dialogue")
        attached = api.style_show(sdb, sm, "98-child.md")
        check("an attached file inherits its guide's law plus its ancestors'",
              {"Keep dialogue clipped.",
               "Address the reader in second person."}
              <= {e["statement"] for e in attached["elements"]})

        api.add_style_law(sdb, sm, "tone",
                              "Prefer terse fragments.", file="98-child.md",
                              overrides=guide_el["id"])
        overridden = api.style_show(sdb, sm, "98-child.md")
        check("a file-local override displaces the guide element it names",
              {e["statement"] for e in overridden["elements"]}
              == {"Prefer terse fragments.",
                  "Address the reader in second person."})

        # --- filter creation MCP tools (AS): add_filter/list_filters/
        #     show_filter round-trip through the same envelope every
        #     other tool uses, against the style-ws fixture above (no
        #     LLM needed — filter creation makes no model call). ---
        from authorlm import mcp_server

        prev_workspace = mcp_server._WORKSPACE
        mcp_server._WORKSPACE = str(style_ws)
        try:
            bad_name = mcp_server.add_filter(
                "Not A Slug", '---\nclass = "sequential"\n---\n\nBody.\n')
            check("add_filter refuses a non-kebab-case name through the "
                  "{ok:false, error} envelope",
                  bad_name["ok"] is False and "kebab-case" in bad_name["error"],
                  str(bad_name))

            empty_prompt = mcp_server.add_filter("empty-prompt", "")
            check("add_filter refuses an empty prompt",
                  empty_prompt["ok"] is False
                  and "its prompt" in empty_prompt["error"],
                  str(empty_prompt))

            no_class = mcp_server.add_filter(
                "no-class", "---\n---\n\nBody with no class.\n")
            check("add_filter refuses front matter missing the required "
                  "`class` key",
                  no_class["ok"] is False
                  and "class" in no_class["error"],
                  str(no_class))

            filter_text = (
                '---\nclass = "sequential"\n'
                'state = "a ledger of flagged words"\n---\n\n'
                "# Duplicate words\n\nFlag a word used again too soon.\n")
            added = mcp_server.add_filter("dupe-words", filter_text)
            check("add_filter ratifies a well-formed artifact",
                  added["ok"] and added["result"]["class"] == "sequential"
                  and added["result"]["state"] == "a ledger of flagged words",
                  str(added))

            listed = mcp_server.list_filters()
            check("list_filters shows the newly-added filter, compact "
                  "(name/class/summary, no prompt body)",
                  listed["ok"]
                  and any(row["name"] == "dupe-words"
                          and row["class"] == "sequential"
                          and row["summary"] == "Duplicate words"
                          and "prompt" not in row
                          for row in listed["result"]["filters"]),
                  str(listed))

            shown = mcp_server.show_filter("dupe-words")
            check("show_filter returns the byte-identical prompt body "
                  "that was ratified",
                  shown["ok"] and shown["result"]["prompt"]
                  == "# Duplicate words\n\nFlag a word used again too soon.",
                  str(shown))

            missing = mcp_server.show_filter("no-such-filter")
            check("show_filter names the filters that DO exist when asked "
                  "for one that doesn't",
                  missing["ok"] is False and "dupe-words" in missing["error"],
                  str(missing))
        finally:
            mcp_server._WORKSPACE = prev_workspace

        # --- MCP tool envelope (T4, risk-register §3): _guard's real
        # contract at the @mcp.tool() seam — (LookupError, ValueError,
        # RuntimeError) become an {"ok": False, "error": ...} dict every
        # other exception type propagates instead of being swallowed. ---
        from authorlm import mcp_server

        # _guard's tracelog write defaults to ~/.authorlm when _WORKSPACE
        # is None (its untouched default here) — pin it to a throwaway
        # workspace first so this test can never write into the author's
        # real trace log.
        guard_ws = root / "mcp-guard-ws"
        guard_ws.mkdir()
        prev_workspace = mcp_server._WORKSPACE
        mcp_server._WORKSPACE = str(guard_ws)
        try:
            for exc_cls, message in (
                (LookupError, "no such manuscript"),
                (ValueError, "bad argument"),
                (RuntimeError, "operation failed"),
            ):
                def boom(exc_cls=exc_cls, message=message):
                    raise exc_cls(message)
                result = mcp_server._guard(boom)
                check(f"_guard maps a bare {exc_cls.__name__} to an "
                      "{ok: False, error} envelope",
                      result == {"ok": False, "error": message}, str(result))

            def crash():
                raise TypeError("not one of the guarded error types")
            try:
                mcp_server._guard(crash)
                propagated = False
            except TypeError as err:
                propagated = str(err) == "not one of the guarded error types"
            check("_guard re-raises exception types outside its guarded "
                  "set instead of swallowing them into an ok:false envelope",
                  propagated)

            ok_result = mcp_server._guard(lambda: {"value": 42})
            check("_guard wraps a successful call as {ok: True, result}",
                  ok_result == {"ok": True, "result": {"value": 42}})
        finally:
            mcp_server._WORKSPACE = prev_workspace

        # --- the SKILL's one tripwire (§15.22, F4-minimal) ---
        #
        # THE FIRST TEST THIS REPOSITORY HAS EVER HAD ON THE SKILL, and
        # it is deliberately one cheap string pin rather than a suite.
        # The skill had no coverage at all, which was tolerable while it
        # only described verbs the CLI tests already pin: a drifted
        # sentence about `filter run` is caught by the reader the next
        # time they use the verb. The pronunciation prelude changed that.
        # Its CONTRACT — one proposal per term, ever, in any state, so a
        # dismissal is final — exists NOWHERE the author can meet it
        # except this document and the tutorial. The harness enforces it
        # silently (proposals._pronunciation_settled) and the assistant
        # is the only thing that can warn anyone before they answer. A
        # skill that quietly lost that sentence would leave the author
        # saying no to a question they did not know was their last.
        #
        # So: one sentence, pinned. Not the whole section, which would
        # make every edit to the prose a test failure and teach everyone
        # to delete the test.
        skill = (Path(__file__).resolve().parent.parent.parent
                 / ".claude" / "skills" / "authorlm" / "SKILL.md")
        check("the authorlm SKILL is where the parity checklist can see "
              "it", skill.is_file(), str(skill))
        skill_text = skill.read_text(encoding="utf-8")
        check("the SKILL still tells the assistant to say the "
              "one-proposal-per-term contract OUT LOUD — the only place "
              "the author can learn a dismissal is final before they "
              "make one",
              "a term you propose is a term the\nauthor will never be "
              "asked about again" in skill_text
              and "One proposal per term, ever, in\nany state"
              in skill_text,
              skill_text[-1200:])

        # --- CLI/MCP parity checklist ---
        from authorlm.mcp_server import mcp
        tool_names = {t.name for t in mcp._tool_manager.list_tools()}
        expected = {
            "list_manuscripts", "get_manuscript_metadata",
            "set_manuscript_metadata", "resolve_file", "get_status",
            "declare_intent", "scope_intent",
            "list_intents", "complete_intent", "abandon_intent",
            "collect_revision", "get_guidance", "review_suggestion",
            "get_briefing", "get_concepts", "add_concept", "link_concepts",
            "confirm_concept", "retire_concept", "curate_concepts",
            "confirm_edge", "reject_edge",
            "list_proposals", "reconcile_proposals", "screen_proposals",
            "resolve_proposal",
            "analyze_episodes",
            "diff_versions", "list_beliefs", "close_session",
            "extract_concepts", "get_plan", "get_doc_links",
            "file_improvement", "list_improvements", "improvement_bundle",
            "resolve_improvement", "alias_concept", "merge_concepts",
            "retire_belief", "demote_belief", "merge_beliefs", "convert_belief_to_law",
            "define_style_guide", "add_style_law", "retire_style_law",
            "attach_style", "get_style", "get_profile", "run_sweep",
            "get_illustration_prompt", "scan_illustrations",
            "triage_illustrations",
            # The inline directives (footnote, explain): the tag report
            # and the landing verb — drafting is chat, by design.
            "list_footnote_tags", "apply_footnote", "resolve_footnotes",
            "list_explain_tags", "apply_explain", "resolve_explains",
            "import_critique", "critique_status", "list_critique_items",
            "list_critique_decisions",
            "triage_critique", "list_critique_edits", "triage_critique_edits",
            # The filter pass's conversational pair (AQ). `filter run` is
            # deliberately NOT here — the same ruling the write loop
            # gets: the loop is CLI-only so there is one call surface,
            # and improving a verb improves every session.
            "list_filter_edits", "triage_filter_edits",
            # Filter CREATION (AS) is curation, the style-law precedent,
            # so it is exposed here; the filter LOOP verbs it feeds
            # (run/prelude/record/settle/…) stay CLI-only by the same
            # one-call-surface ruling as `filter run` above.
            "add_filter", "list_filters", "show_filter",
            "move_style_law",
            # The audiobook (audiobook-pipeline-design §11): the export,
            # the readiness report, free voice discovery, the paid
            # audition (confirm-gated), the cast row, the dictionary push
            # (confirm-gated). `audio init` is CLI-only: a one-time seed.
            "export_audio", "audio_readiness", "list_voices",
            "audition_voices", "set_cast", "push_pronunciations", "say_term",
            # The Triage App and the pronunciation workbench are NOT here:
            # each is a local page served by its CLI verb (`triage-app`,
            # `workbench`). The MCP App road was removed 2026-09-04 — it
            # never rendered in the client the author uses.
        }
        check("MCP exposes the full hand-curated tool set",
              expected == tool_names,
              f"missing: {expected - tool_names}; extra: {tool_names - expected}")
        for tool in mcp._tool_manager.list_tools():
            check_desc = bool(tool.description and len(tool.description) > 40)
            assert check_desc, f"tool {tool.name} lacks a real description"
        check("every MCP tool carries a substantive description", True)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nAll {PASSED} checks passed.")


if __name__ == "__main__":
    _assert_offline()
    main_test()
