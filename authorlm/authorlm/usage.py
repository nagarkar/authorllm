"""The always-on usage ledger — where the money went.

Sponsor ruling (2026-08-30): *"building a termination and budget feature
first class would make sense. For now we don't need to cut off the usage
when the budget has reached, but it would be good to track usage so if
you ever want to take this to commercial stage, we can have that
ready-made."*

`trace.jsonl` (`tracelog`) knows which verbs ran. `db-perf.jsonl`
(`dbperf`) knows where their time went. Nothing knew where their *money*
went — the spend audit found seventeen billable call sites, two of them
on paths that print no usage line at all, so a doc claim was the only
thing standing between the author and the bill. This is the layer that
makes the answer recoverable instead of asserted.

**The architecture is `dbperf`'s, because the argument that won there
wins here.** Same directory, same 5 MB / two-generation rotation, same
one-aggregate-line-per-invocation shape, same `[section] enabled`
off-switch, same never-in-the-way swallowing, same read-only reader verb
that does not open the database.

Two kinds of quantity land in
`<workspace>/.authorlm/logs/usage.jsonl`, discriminated by `kind`, and
they are **never summed into one number** anywhere:

- **`kind: "api"` — billable.** Folded in at `LLMClient._record_usage`,
  one seam sitting *below* `stats_line()`, `_report_llm`, the CLI and the
  MCP server, so the two currently-silent auto paths are covered with no
  call-site edit. Sixteen of the seventeen audited sites reach it that
  way; `generate_image` is not a client and calls `record_image` itself.
  Tokens are exact (the provider reported them); `est_cost_usd` is an
  ESTIMATE from llmlm's price cache and is labelled as one
  everywhere it is printed.
- **`kind: "chat"` — consumption.** Tokens the author's Claude Code
  subscription absorbed, swept incrementally from the session transcript
  through the optional `clients.UsageCapable` adapter capability. Exact
  counts, **no dollar figure, ever**: a subscription does not bill per
  token and printing a number next to it invents a bill that does not
  exist.

**Pricing goes through llmlm's price cache (`llmlm.prices`), never a
vendor map read directly.** The cache is a machine-wide state file
(`~/.llmlm/prices.json`), refreshed weekly from litellm's published map,
with hand-verified overrides for what the map lacks. The map's keys are
inconsistent about the vendor prefix and the inconsistency lands
precisely on the shipped config's most expensive paths: it has
`gemini/gemini-2.5-flash` but not `openai/gpt-5.6-luna`,
`anthropic/claude-fable-5` or `openai/gpt-image-2`. A direct lookup
would have recorded `cost: null` for the summariser, the critique editor,
illustration rendering and beat drafting — silently, and exactly where
the money is. llmlm's lookup tries the exact key, then the bare name
after the vendor prefix, and resolves all four. Pinned by
`check_usage_price_resolves_vendor_prefix`.

A model the map does not know records **tokens with a null cost, never a
guess**; `cost_complete` then goes false and the report names the gap in
its own total rather than absorbing it.

**The ledger carries no prose.** Counts, model strings, config-section
names, the `command + action` verb label `trace.jsonl` already permits,
and the provenance join key. No prompt, no completion, no filename, no
transcript path.

**The ledger is llmlm's** (nagarkar/llmlm#1, step 7). An API call is one
`llmlm.ledger` usage line (`stage` = the purpose, `model`, tokens, the cost
authorlm computed above or null, `invocation` = the verb label, `run` = one
id per invocation, `client` = the provenance key), validated against
llmlm's `usage_line` schema, rotated at 5 MB with two generations, and so
read by the family's one usage board (`llmlm.usage.model_spend`, served by
corpuslm) like every other tool's. A replay is a `cached` line, an image
render a zero-token line with a null cost. The chat sweeps stay authorlm's
own `kind: "chat"` lines in the same file (llmlm counts them and never sums
them). Lines written before this move — one aggregate per invocation, with
`calls` keyed `<purpose>|<model>` — are still read, by `api_records`.

Read it with `authorlm usage [--days N] [--by …]` (`report()` below).
"""

from __future__ import annotations

import atexit
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from llmlm import ledger as model_ledger
from llmlm import prices
from llmlm import usage as model_usage

FILENAME = "usage.jsonl"
MAX_BYTES = model_ledger.ROTATE_BYTES   # rotate beyond this: 5 MB, matching trace.jsonl
GENERATIONS = model_ledger.GENERATIONS  # usage.jsonl.1, usage.jsonl.2
DEFAULT_EXPENSIVE_USD = 0.50
DEFAULT_SWEEP_INTERVAL = 300

# Checkpoints get their own directory, DELIBERATELY not
# `<workspace>/.authorlm/clients/` beside the markers: `clients.sweep_markers`
# globs `*.json` there and unlinks anything over 24 h old, so a live-but-idle
# chat's checkpoint would be swept and the next sweep would re-read its
# transcript from byte 0.
CHECKPOINT_DIRNAME = "usage-checkpoints"
CHECKPOINT_PRUNE_DAYS = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_dir(workspace: str | Path | None = None) -> Path:
    """Where the ledger lives, from a workspace — the reader's entry
    point. The same directory `tracelog` and `dbperf` resolve: three
    logs, one directory, one `--workspace` flag, one thing to back up."""
    base = Path(workspace).resolve() if workspace else Path.home()
    return base / ".authorlm" / "logs"


def checkpoint_dir(workspace: str | Path | None = None) -> Path:
    return log_dir(workspace) / CHECKPOINT_DIRNAME


# --------------------------------------------------------------- config


@dataclass(frozen=True)
class Settings:
    ledger: bool = True
    expensive_usd: float = DEFAULT_EXPENSIVE_USD
    chat: bool = True
    sweep_interval_seconds: int = DEFAULT_SWEEP_INTERVAL
    # The ONLY way a transcript outside the engine's own declared root is
    # ever read. Empty by default.
    transcript_roots: tuple[str, ...] = ()


DEFAULTS = Settings()

_SETTINGS: dict[str, Settings] = {}


def settings() -> Settings:
    """`[usage]` from config.toml, memoized on the resolved config path
    so a test pointing `AUTHORLM_CONFIG` elsewhere gets a fresh read
    without a reset hook.

    Shipped ON, for `dbperf.settings`'s reason: an absent file, an absent
    section and an unreadable config all mean the default, because a
    broken config must not be a silent off-switch for a MEASUREMENT.
    (`budget.settings` inverts this deliberately — see its docstring.)"""
    key = ""
    try:
        from . import paths

        key = str(paths.config_path())
        cached = _SETTINGS.get(key)
        if cached is not None:
            return cached
        section: dict = {}
        path = Path(key)
        if path.exists():
            import tomllib

            parsed = tomllib.loads(path.read_text(encoding="utf-8"))
            section = parsed.get("usage") or {}
        roots = section.get("transcript_roots") or []
        found = Settings(
            ledger=bool(section.get("ledger", True)),
            expensive_usd=float(section.get("expensive_usd",
                                            DEFAULT_EXPENSIVE_USD)),
            chat=bool(section.get("chat", True)),
            sweep_interval_seconds=int(section.get(
                "sweep_interval_seconds", DEFAULT_SWEEP_INTERVAL)),
            transcript_roots=tuple(str(r) for r in roots
                                   if isinstance(r, str)),
        )
    except Exception:
        return DEFAULTS
    _SETTINGS[key] = found
    return found


# -------------------------------------------------------------- pricing

# The price table. None means llmlm's machine-wide cache
# (`llmlm.prices.default()`); tests set a `prices.Prices` built on a
# temporary file and a fake fetch, because the live map changes under us
# and a cost assertion pinned to it would be a time bomb.
PRICES: prices.Prices | None = None
_PRICE_CACHE: dict[str, dict | None] = {}

_RATE_KEYS = (("input", "input_per_m"),
              ("output", "output_per_m"),
              ("cache_read", "cache_read_per_m"),
              ("cache_write", "cache_write_per_m"))


def price(model: str) -> dict | None:
    """The four per-token rates for `model`, or `None` when the price
    cache does not know it. Memoized per model per process — a verb
    making 24 summariser calls does one lookup.

    NEVER guesses: an unmapped model prices to `None` and the line
    records `cost: null`, which is a different claim from `0.0` and is
    kept apart from it all the way to the report.

    The lookup is llmlm's (`Prices.rates`: exact key, then the bare name
    after the vendor prefix — see the module docstring), and llmlm's
    rates are dollars per MILLION tokens; this returns dollars per token,
    the unit `estimate()` and the report always used. A rate llmlm has
    as 0 on an otherwise-priced model (the map carried no figure for it)
    contributes 0 to the sum and sets `partial`, which the report names;
    a row whose every rate is 0 is not a price."""
    if model in _PRICE_CACHE:
        return _PRICE_CACHE[model]
    found: dict | None = None
    try:
        table = PRICES if PRICES is not None else prices.default()
        row = table.rates(model)
        if row is not None:
            rates: dict = {"partial": False}
            for name, key in _RATE_KEYS:
                value = float(getattr(row, key) or 0.0) / 1e6
                if value == 0.0:
                    rates["partial"] = True
                rates[name] = value
            # A row with no usable rate at all is not a price.
            if any(rates[name] for name, _ in _RATE_KEYS):
                found = rates
    except Exception:
        # The cache file cannot be read or fetched, or a model it does not
        # map. Pricing is an ENRICHMENT: the ledger records every token
        # either way.
        found = None
    _PRICE_CACHE[model] = found
    return found


def estimate(model: str, input_tokens: int, output_tokens: int,
             cache_read: int, cache_write: int) -> float | None:
    rates = price(model)
    if rates is None:
        return None
    return (input_tokens * rates["input"]
            + output_tokens * rates["output"]
            + cache_read * rates["cache_read"]
            + cache_write * rates["cache_write"])


# ------------------------------------------------------------- recorder


class _Ledger(model_ledger.Ledger):
    """llmlm's ledger that never resurrects a workspace deleted out from
    under a late flush: it creates `logs/`, not its parent."""

    def _write(self, name: str, event: dict) -> None:
        if not self.directory.exists() and not self.directory.parent.is_dir():
            return
        super()._write(name, event)


class Recorder:
    """One invocation's in-memory aggregation.

    Module-level rather than per-object: `LLMClient`s are constructed ad
    hoc all over the codebase and several may be alive within one verb,
    but the unit of aggregation is the INVOCATION — the same unit
    `dbperf` uses and the same unit the flush points already delimit.

    Each call is kept as the llmlm usage line it will become and written
    at the flush, once the verb's name is known; the running totals beside
    them feed the budget seam and `pending()`."""

    __slots__ = ("directory", "expensive_usd", "calls", "images",
                 "replays", "events", "_client", "_client_resolved",
                 "budgeted")

    def __init__(self, directory: Path,
                 expensive_usd: float = DEFAULT_EXPENSIVE_USD,
                 budgeted: bool = False):
        self.directory = directory
        self.expensive_usd = expensive_usd
        # Resolved ONCE per invocation, beside the client key and for the
        # same reason: `paths.config_path()` costs ~27 us and asking it
        # per model call would dominate a hot path that is otherwise half
        # a microsecond.
        self.budgeted = budgeted
        # "<purpose>|<model>" -> [n, in, out, cache_read, cache_write,
        #                         est_cost|None]
        self.calls: dict[str, list] = {}
        self.images: dict[str, int] = {}
        self.replays = 0
        self.events: list[dict] = []
        self._client: dict | None = None
        self._client_resolved = False

    # -- writing side ----------------------------------------------------

    def record(self, purpose: str, model: str, input_tokens: int,
               output_tokens: int, cache_read: int = 0,
               cache_write: int = 0) -> None:
        """Hot path: a dict update and a list append, no I/O."""
        key = f"{purpose}|{model}"
        cost = estimate(model, input_tokens, output_tokens,
                        cache_read, cache_write)
        row = self.calls.get(key)
        if row is None:
            self.calls[key] = [1, input_tokens, output_tokens,
                               cache_read, cache_write, cost]
        else:
            row[0] += 1
            row[1] += input_tokens
            row[2] += output_tokens
            row[3] += cache_read
            row[4] += cache_write
            if cost is not None:
                row[5] = (cost if row[5] is None else row[5] + cost)
        event = {"stage": purpose, "prompt_id": "authorlm", "model": model,
                 "tokens_in": input_tokens, "tokens_out": output_tokens,
                 "cost_usd": None if cost is None else round(cost, 6),
                 "kind": "api", "cached": False, "tag": "", "outcome": "ok"}
        if cache_read:
            event["cache_read"] = cache_read
        if cache_write:
            event["cache_write"] = cache_write
        self.events.append(event)

    def record_image(self, purpose: str, model: str, images: int = 1) -> None:
        """`generate_image` has no client and no token usage.

        `get_model_info` gives per-IMAGE-TOKEN rates and no flat
        per-image price, and the API returns no image-token count, so
        the cost is null for image renders and the line is a count
        (`llmlm.images.usage_line`'s shape). More honest than a number
        derived from a rate card we cannot apply."""
        key = f"{purpose}|{model}"
        row = self.calls.get(key)
        if row is None:
            self.calls[key] = [images, 0, 0, 0, 0, None]
        else:
            row[0] += images
            row[5] = None            # an image render is never priced
        self.images[key] = self.images.get(key, 0) + images
        for _ in range(images):
            self.events.append(
                {"stage": purpose, "prompt_id": "image", "model": model,
                 "tokens_in": 0, "tokens_out": 0, "cost_usd": None,
                 "kind": "api", "cached": False, "tag": "",
                 "outcome": "ok"})

    def record_replay(self, purpose: str = "llm", model: str = "") -> None:
        """A call served from the record/replay cache: a `cached` line with
        no tokens and no cost."""
        self.replays += 1
        self.events.append(
            {"stage": purpose, "prompt_id": "authorlm", "model": model or "replay",
             "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "kind": "api",
             "cached": True, "tag": "", "outcome": "ok"})

    def totals(self) -> dict:
        priced = 0.0
        complete = True
        calls = 0
        counters = [0, 0, 0, 0]
        for row in self.calls.values():
            calls += row[0]
            for i in range(4):
                counters[i] += row[i + 1]
            if row[5] is None:
                complete = False
            else:
                priced += row[5]
        return {"calls": calls, "input_tokens": counters[0],
                "output_tokens": counters[1], "cache_read": counters[2],
                "cache_write": counters[3], "replays": self.replays,
                "images": sum(self.images.values()),
                "est_cost_usd": round(priced, 6),
                "cost_complete": complete}

    def flush(self, invocation: str | None = None) -> None:
        """Write this invocation's lines, then start over."""
        events, self.events = self.events, []
        self.calls, self.images, self.replays = {}, {}, 0
        if not events:
            return
        run = uuid.uuid4().hex[:12]
        who = self.client()
        ledger = _Ledger(self.directory)
        for event in events:
            event["invocation"] = invocation or ""
            event["run"] = run
            if who:
                event["client"] = who
            try:
                ledger.usage(event)
            except Exception:
                pass          # a usage log that can break a verb is worse than none

    def client(self) -> dict | None:
        """The provenance join key (§15.15), resolved once per recorder —
        i.e. once per invocation. Omitted entirely when detection is off,
        exactly as `dbperf._write` and `tracelog.record` omit it."""
        if not self._client_resolved:
            self._client_resolved = True
            try:
                from . import clients

                found = clients.current()
                self._client = (found.key() if found.precision != "none"
                                else None)
            except Exception:
                self._client = None
        return self._client

    def _write(self, entry: dict) -> None:
        _write_line(self.directory, entry, self.client())


def _write_line(directory: Path, entry: dict,
                who: dict | None = None) -> None:
    """Append one line. Swallows everything — a usage log that can break
    a `collect` is worse than no usage log."""
    try:
        if who:
            entry["client"] = who
        if not directory.exists():
            # Never resurrect a workspace deleted out from under a late
            # flush: create `logs/`, not its parent.
            if not directory.parent.is_dir():
                return
            directory.mkdir(parents=True, exist_ok=True)
        path = directory / FILENAME
        if path.exists() and path.stat().st_size > MAX_BYTES:
            _rotate(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _generation(path: Path, n: int) -> Path:
    return path.with_name(f"{path.name}.{n}")


def _rotate(path: Path) -> None:
    """`usage.jsonl` → `.1` → `.2`, oldest dropped — `dbperf`'s rotation,
    unchanged."""
    try:
        _generation(path, GENERATIONS).unlink()
    except OSError:
        pass
    for n in range(GENERATIONS - 1, 0, -1):
        older = _generation(path, n)
        if older.exists():
            older.replace(_generation(path, n + 1))
    path.replace(_generation(path, 1))


# ------------------------------------------------------- the one ledger

_OFF = object()

# The invocation's recorder: `None` before it is resolved, `_OFF` when
# `[usage] ledger = false`, a Recorder otherwise. The off-switch therefore
# costs one identity test per LLM call — a test against something that
# takes hundreds of milliseconds.
_LEDGER: Any = None


def _ledger() -> Recorder | None:
    global _LEDGER
    if _LEDGER is _OFF:
        return None
    if _LEDGER is not None:
        return _LEDGER
    try:
        config = settings()
        if not config.ledger:
            _LEDGER = _OFF
            return None
        from . import clients

        from . import budget

        _LEDGER = Recorder(log_dir(clients.default_workspace()),
                           config.expensive_usd,
                           budgeted=budget.settings().present)
        return _LEDGER
    except Exception:
        _LEDGER = _OFF
        return None


def reset() -> None:
    """Tests only: drop the invocation's recorder and the settings memo so
    the next `record` re-resolves both."""
    global _LEDGER
    _LEDGER = None
    _SETTINGS.clear()


def record(purpose: str, model: str, input_tokens: int, output_tokens: int,
           cache_read: int = 0, cache_write: int = 0) -> None:
    """Fold one live model call into the invocation's aggregate. Never
    raises."""
    try:
        led = _ledger()
        if led is None:
            return
        led.record(purpose, model, int(input_tokens), int(output_tokens),
                   int(cache_read), int(cache_write))
        if led.budgeted:
            # The common path — no [budget] — never reaches here at all:
            # the day total is not computed and the ledger file is not
            # read.
            from . import budget

            budget.note_spend(led.directory, led.totals()["est_cost_usd"])
    except Exception:
        pass


def record_image(purpose: str, model: str, images: int = 1) -> None:
    """The seventeenth call site. `generate_image` is not an `LLMClient`,
    so it records itself, in one line, on success."""
    try:
        led = _ledger()
        if led is None:
            return
        led.record_image(purpose, model, int(images))
    except Exception:
        pass


def record_replay(purpose: str = "llm", model: str = "") -> None:
    """A call served from the record/replay cache: it did NOT spend.

    Counted because "what did the replay cache save me" is free to answer
    and is the only positive number in the report (Q1)."""
    try:
        led = _ledger()
        if led is None:
            return
        led.record_replay(purpose, model)
    except Exception:
        pass


def pending() -> dict:
    """The un-flushed totals.

    Exists for `it-462f742f2bae` ("usage lines on silent auto paths"),
    which is a DIFFERENT remedy for the same finding: that item is about
    the author seeing a printed line in the moment; this module is about
    the spend being recoverable after the fact. Neither subsumes the
    other, and this design deliberately adds no printed lines — that
    stays that item's remit. What it contributes is this seam: the
    printed line can be sourced from here instead of from a per-verb
    `stats_line()` the auto path has no client handle for."""
    try:
        led = _ledger()
        if led is None:
            return {}
        return led.totals()
    except Exception:
        return {}


def flush(invocation: str | None = None) -> None:
    """Write this invocation's one aggregate line and start over.

    Called from `cli._trace`, `mcp_server._guard` and `atexit` — the
    three points `dbperf.flush` already uses."""
    global _LEDGER
    led = _LEDGER
    _LEDGER = None
    if led is None or led is _OFF:
        return
    try:
        led.flush(invocation)
    except Exception:
        pass


atexit.register(flush)


# ----------------------------------------------------------- chat sweep


def _checkpoint_path(workspace: str | Path | None, engine: str,
                     session_id: str) -> Path:
    safe = str(session_id).replace("/", "_")
    return checkpoint_dir(workspace) / f"{engine}-{safe}.json"


def read_checkpoint(workspace: str | Path | None, engine: str,
                    session_id: str) -> dict | None:
    try:
        data = json.loads(_checkpoint_path(workspace, engine, session_id)
                          .read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def write_checkpoint(workspace: str | Path | None, engine: str,
                     session_id: str, data: dict) -> bool:
    """Atomic (`tmp` + `os.replace`), one file per session — exactly as
    `clients.write_marker_file` does, so two chats write two files and
    never touch each other's: no lock, no last-writer-wins."""
    import os

    try:
        path = _checkpoint_path(workspace, engine, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def prune_checkpoints(workspace: str | Path | None,
                      days: int = CHECKPOINT_PRUNE_DAYS) -> int:
    """Bound the directory without a daemon: checkpoints get their own
    30-day prune, run from the same sweep."""
    removed = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(days, 0))
    try:
        paths = list(checkpoint_dir(workspace).glob("*.json"))
    except Exception:
        return 0
    for path in paths:
        try:
            if datetime.fromtimestamp(path.stat().st_mtime,
                                      timezone.utc) > cutoff:
                continue
            path.unlink()
            removed += 1
        except Exception:
            pass
    return removed


def _due(workspace: str | Path | None, engine: str, session_id: str,
         interval: int) -> bool:
    """The interval gate: one `stat` in the common case, no parse."""
    try:
        stamp = _checkpoint_path(workspace, engine, session_id).stat().st_mtime
    except Exception:
        return True                      # never swept: due
    import time

    return (time.time() - stamp) >= max(interval, 0)


def sweep_session(client, *, workspace: str | Path | None = None,
                  force: bool = False, allow_recompute: bool = True,
                  final: bool = False) -> dict | None:
    """Sweep one chat session's transcript and, if anything new was
    consumed, write its `kind: "chat"` ledger line.

    Returns the emitted entry, or `None` — and `None` is a first-class
    answer, exactly as `clients.detect()` returning `None` is: no
    transcript, an ambiguous transcript, an unreadable one, or simply
    nothing new. The sweep records nothing rather than recording a
    guess."""
    try:
        from . import clients

        config = settings()
        if not config.chat:
            return None
        engine = getattr(client, "engine", None)
        session_id = getattr(client, "session_id", None)
        if not engine or not session_id:
            return None
        adapter = clients.adapter_for_engine(str(engine))
        if adapter is None or not hasattr(adapter, "usage"):
            return None
        ws = workspace if workspace is not None \
            else clients.default_workspace()
        if not force and not _due(ws, str(engine), str(session_id),
                                  config.sweep_interval_seconds):
            return None
        checkpoint = read_checkpoint(ws, str(engine), str(session_id))
        try:
            answer = adapter.usage(client, checkpoint,
                                   allow_recompute=allow_recompute,
                                   extra_roots=config.transcript_roots)
        except Exception:
            answer = None                # an adapter that raises is a None
        if answer is None:
            return None
        chat, new_checkpoint = answer
        write_checkpoint(ws, str(engine), str(session_id), new_checkpoint)
        if not chat.models and not chat.restart:
            return None                  # nothing new: no line at all
        entry = {
            "ts": _now(), "kind": "chat", "engine": str(engine),
            "session": str(session_id),
            "adapter": getattr(adapter, "name", str(engine)),
            "models": chat.models, "messages": chat.messages,
            "sidechain": chat.sidechain, "bytes_read": chat.bytes_read,
            "restart": chat.restart,
        }
        if final:
            # This sweep came from the SessionEnd hook, so this session's
            # TAIL is counted. The reader needs it: the hook UNLINKS the
            # marker straight afterwards, so "no marker" would otherwise
            # accuse exactly the sessions that did have the hook.
            entry["final"] = True
        if chat.dedup_warning:
            # R-a's standing watch item: `messages` may never exceed the
            # number of usage-bearing lines actually read. If it does, the
            # dedup has failed — say so and continue.
            entry["dedup_warning"] = True
        # NO transcript path: it is a filesystem location tied to the
        # author's project layout and does not belong in a telemetry log
        # that may be pasted into an issue. It lives in the marker and the
        # checkpoint. Same argument as `Client.full()`'s.
        _write_line(log_dir(ws), entry, None)
        return entry
    except Exception:
        return None


def sweep_opportunistic(workspace: str | Path | None = None) -> dict | None:
    """The trigger. **No daemon, no thread, no cron, no new process.**

    Runs at the flush point that already exists (`cli._trace`,
    `mcp_server._guard`) and does nothing at all unless every one of:
    `[usage] chat = true`; `clients.current()` resolved `exact` WITH a
    session id — the chat driving AuthorLM right now, never a scan of
    every session on the machine; that engine's adapter implements the
    capability; and that session's checkpoint is older than
    `sweep_interval_seconds`. So: at most one sweep per session per five
    minutes, and only for the chat actually working. Between sweeps the
    cost is one `stat`."""
    try:
        from . import clients

        who = clients.current()
        if who.precision != "exact" or not who.session_id:
            return None
        return sweep_session(who, workspace=workspace)
    except Exception:
        return None


def sweep_all(workspace: str | Path | None = None,
              force: bool = True) -> list[dict]:
    """`authorlm usage --sweep`: close the books before reading the
    report. Sweeps every LIVE marker, ignoring the interval. The plain
    reader never does this — a report that silently changes what it is
    reporting on is not a report."""
    out: list[dict] = []
    try:
        from . import clients

        ws = workspace if workspace is not None \
            else clients.default_workspace()
        for marker in clients.live_markers(ws):
            who = clients.Client(
                engine=str(marker.get("engine") or "unknown"),
                session_id=marker.get("session_id"),
                transcript_hint=marker.get("transcript_hint"),
                precision="exact", adapter="marker")
            entry = sweep_session(who, workspace=ws, force=force)
            if entry is not None:
                out.append(entry)
        prune_checkpoints(ws)
    except Exception:
        pass
    return out


# ------------------------------------------------------------- reporting


def read_entries(directory: str | Path) -> list[dict]:
    """Every line in the ledger and its rotated generations, oldest
    first."""
    folder = Path(directory)
    names = [f"{FILENAME}.{n}" for n in range(GENERATIONS, 0, -1)] + [FILENAME]
    entries: list[dict] = []
    for name in names:
        try:
            text = (folder / name).read_text(encoding="utf-8")
        except Exception:
            continue
        for line in text.splitlines():
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def _day(entry: dict) -> str:
    """The UTC day of a line: `ts` on the lines written before llmlm's
    ledger, `at` on the ones after."""
    return str(entry.get("ts") or entry.get("at") or "")[:10]


def _client_name(entry: dict) -> str | None:
    who = entry.get("client")
    if not isinstance(who, dict):
        return None
    session = str(who.get("session") or "-")
    return f"{who.get('engine', '?')}/{session[:12]}"


def api_records(entries: list[dict], expensive_usd: float = 0.0) -> list[dict]:
    """The API lines of `entries` as one shape, whichever ledger wrote them.

    A record is `{day, run, verb, client, purpose, model, n, cin, cout,
    cr, cw, cost, replays, image, expensive, ts}`: `cost` is the total for
    its `n` calls (None when unpriced), `run` names the invocation (every
    llmlm line carries one; an old aggregate line is its own run). New
    lines are one call each; old aggregates expand to one record per
    `<purpose>|<model>` key. A single call over `expensive_usd` is marked
    `expensive` (old ledgers wrote a separate detail line for it; that line
    is kept as `detail`, never counted)."""
    records: list[dict] = []
    for index, entry in enumerate(entries):
        if entry.get("kind") != "api":
            continue
        base = {"day": _day(entry), "ts": entry.get("ts") or entry.get("at"),
                "verb": str(entry.get("invocation") or "(unnamed)"),
                "client": _client_name(entry),
                "run": entry.get("run") or f"legacy-{index}"}
        if entry.get("expensive"):
            records.append({**base, "detail": True,
                            "purpose": entry.get("purpose", "?"),
                            "model": entry.get("model", "?"),
                            "cost": entry.get("est_cost_usd")})
            continue
        if "calls" in entry:                       # the aggregate format
            for key, stats in (entry.get("calls") or {}).items():
                try:
                    purpose, _, model = str(key).partition("|")
                    records.append({
                        **base, "purpose": purpose, "model": model,
                        "n": int(stats[0]), "cin": int(stats[1]),
                        "cout": int(stats[2]), "cr": int(stats[3]),
                        "cw": int(stats[4]),
                        "cost": None if stats[5] is None else float(stats[5]),
                        "image": False})
                except Exception:
                    continue
            for key, count in (entry.get("images") or {}).items():
                try:
                    purpose, _, model = str(key).partition("|")
                    records.append({**base, "purpose": purpose, "model": model,
                                    "images": int(count)})
                except Exception:
                    continue
            if entry.get("replays"):
                records.append({**base, "replays": int(entry["replays"])})
            if not entry.get("calls") and not entry.get("replays"):
                records.append({**base, "empty": True})
            continue
        try:                                       # one llmlm line, one call
            if entry.get("cached"):
                records.append({**base, "replays": 1})
                continue
            if entry.get("prompt_id") == "image":
                records.append({**base, "purpose": entry["stage"],
                                "model": entry["model"], "images": 1})
                continue
            cost = entry.get("cost_usd")
            record = {
                **base, "purpose": entry["stage"], "model": entry["model"],
                "n": 1, "cin": int(entry.get("tokens_in") or 0),
                "cout": int(entry.get("tokens_out") or 0),
                "cr": int(entry.get("cache_read") or 0),
                "cw": int(entry.get("cache_write") or 0),
                "cost": None if cost is None else float(cost), "image": False}
            if (record["cost"] is not None and expensive_usd > 0
                    and record["cost"] > expensive_usd):
                record["expensive"] = True
            records.append(record)
        except Exception:
            continue
    return records


def api_cost_on(entry: dict, day: str) -> float:
    """What one ledger line adds to the estimated API spend of `day` (the
    budget seam's day total): an old aggregate its `est_cost_usd`, a new
    line its `cost_usd`, a replay or a detail line nothing."""
    if entry.get("kind") != "api" or entry.get("expensive"):
        return 0.0
    if _day(entry) != day or entry.get("cached"):
        return 0.0
    if "calls" in entry:
        return float(entry.get("est_cost_usd") or 0.0)
    return float(entry.get("cost_usd") or 0.0)


def _money(value: float | None) -> str:
    return f"{'–':>10}" if value is None else f"{value:>10.2f}"


def report(directory: str | Path, days: int = 7, top: int = 10,
           by: str = "purpose|model", workspace: str | Path | None = None
           ) -> list[str]:
    """The rendered report, as lines.

    Read-only, no network, no LLM, no writes — and it does not open the
    database, so it is safe against a live workspace mid-flight. Honest
    about an empty or absent log rather than printing an empty table.

    The API half is counted the way llmlm's board counts (`llmlm.usage`):
    a call with no known price is `cost_unknown`, never a zero, and a chat
    line is counted but never summed with billed ones."""
    folder = Path(directory)
    entries = read_entries(folder)
    if not entries:
        return [f"no usage ledger yet  ({folder / FILENAME})"]
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=max(days, 0))).strftime("%Y-%m-%d")
    window = [e for e in entries if _day(e) >= cutoff]
    if not window:
        return [f"no entries in the last {days} days "
                f"({len(entries)} older lines in {folder / FILENAME})"]

    # group -> llmlm's spend row, plus the cache counters it does not keep
    groups: dict[str, dict] = {}
    cache_counters: dict[str, list] = {}
    images: dict[str, int] = {}
    # The "priced total" row sums ONLY calls that actually priced, so a
    # group mixing priced and unpriced calls cannot smuggle its unpriced
    # half into a number the reader will read as complete.
    priced_total = 0.0
    priced_calls = 0
    priced_counters = [0, 0, 0, 0]
    per_day: dict[str, list] = {}         # -> [invocations, cost, chat_tokens]
    day_runs: dict[str, set] = {}
    per_client: dict[str, list] = {}      # -> [calls, cost]
    expensive: list[dict] = []
    chat_models: dict[str, list] = {}     # -> [n, in, out, cr, cw]
    sessions: set = set()
    finalised: set = set()
    restarts = 0
    runs: set = set()
    calls = 0
    replays = 0
    unpriced_models: set = set()

    def group_of(record: dict) -> str:
        if by == "purpose":
            return record["purpose"]
        if by == "model":
            return record["model"]
        if by == "verb":
            return record["verb"]
        if by == "day":
            return record["day"]
        if by == "client":
            return record["client"] or "(unattributed)"
        return f"{record['purpose']}|{record['model']}"

    for entry in window:
        if entry.get("kind") != "chat":
            continue
        bucket = per_day.setdefault(_day(entry), [0, 0.0, 0])
        sessions.add((entry.get("engine"), entry.get("session")))
        if entry.get("final"):
            finalised.add((entry.get("engine"), entry.get("session")))
        if entry.get("restart"):
            restarts += 1
        for model, stats in (entry.get("models") or {}).items():
            try:
                row = chat_models.setdefault(str(model), [0, 0, 0, 0, 0])
                for i in range(5):
                    row[i] += int(stats[i])
                bucket[2] += (int(stats[1]) + int(stats[2])
                              + int(stats[3]) + int(stats[4]))
            except Exception:
                continue

    for record in api_records(window, settings().expensive_usd):
        if record.get("detail"):
            # Already counted inside its invocation's aggregate; this line
            # is the detail record, not a second sample. (`dbperf.report`'s
            # exact discipline.)
            expensive.append(record)
            continue
        if record.get("expensive"):
            expensive.append(record)
        runs.add(record["run"])
        day = record["day"]
        bucket = per_day.setdefault(day, [0, 0.0, 0])
        day_runs.setdefault(day, set()).add(record["run"])
        replays += int(record.get("replays") or 0)
        if record.get("images"):
            key = group_of(record)
            images[key] = images.get(key, 0) + record["images"]
            continue
        if "n" not in record:
            continue
        n, cost = record["n"], record["cost"]
        calls += n
        counters = [record["cin"], record["cout"], record["cr"], record["cw"]]
        key = group_of(record)
        row = groups.setdefault(key, model_usage.empty_row())
        cache = cache_counters.setdefault(key, [0, 0, 0, 0])
        for i in range(4):
            cache[i] += counters[i]
        row["calls"] += n
        if cost is None:
            unpriced_models.add(record["model"])
            row["cost_unknown"] += n
        else:
            bucket[1] += cost
            priced_total += cost
            priced_calls += n
            for i in range(4):
                priced_counters[i] += counters[i]
            row["cost_usd"] = round(row["cost_usd"] + cost, 6)
        if record["client"]:
            seen = per_client.setdefault(record["client"], [0, 0.0])
            seen[0] += n
            seen[1] += cost or 0.0
    for day, who in day_runs.items():
        per_day[day][0] = len(who)

    total_images = sum(images.values())
    out = [f"usage — last {days} days   {len(runs)} invocations, "
           f"{calls} API calls, {total_images} images",
           f"  {folder / FILENAME}"]

    budget_block = _budget_block(folder)
    if budget_block:
        out.append("")
        out.extend(budget_block)

    # --- the API half -------------------------------------------------
    out.append("")
    if not groups:
        out.append("no API spend in this window — every model call was "
                   "replayed or the LLM is off.")
        if replays:
            out.append(f"  {replays} calls replayed from the record/replay "
                       f"cache — not billed, not counted above.")
    else:
        out.append("API spend (ESTIMATE — litellm price list, not your "
                   "invoice)")
        out.append(f"  {'est $':>10} {'calls':>9} {'in':>9} {'out':>10} "
                   f"{'cache r/w':>11}   {by}")
        ranked = sorted(groups.items(), key=lambda kv: -kv[1]["cost_usd"])[:top]
        for key, row in ranked:
            # `null` and `0.0` are different claims and stay apart: a
            # group with nothing priced prints a dash, never a zero.
            cost = (None if (row["cost_unknown"] and not row["cost_usd"])
                    else row["cost_usd"])
            note = ""
            count = images.get(key)
            if count:
                note = f"  ({count} images, not priced)"
            cin, cout, cread, cwrite = cache_counters[key]
            out.append(f"  {_money(cost)} {row['calls']:>9} {cin:>9,} "
                       f"{cout:>10,} {cread:>5,} / {cwrite:<3,}  "
                       f"{key}{note}")
        out.append("  " + "-" * 64)
        out.append(f"  {priced_total:>10.2f} {priced_calls:>9} "
                   f"{priced_counters[0]:>9,} {priced_counters[1]:>10,} "
                   f"{priced_counters[2]:>5,} / {priced_counters[3]:<3,}  "
                   f"priced total")
        if replays:
            out.append(f"  {replays} calls replayed from the record/replay "
                       f"cache — not billed, not counted above.")
        if unpriced_models:
            named = ", ".join(sorted(unpriced_models))
            out.append(f"  {len(unpriced_models)} model(s) had no price "
                       f"({named}): the total omits them.")

    # --- the chat half ------------------------------------------------
    out.append("")
    if not chat_models:
        out.append("no chat consumption recorded — no engine adapter "
                   "measured a session in this window.")
    else:
        out.append("Chat consumption (subscription — NOT billed, no dollar "
                   "figure)")
        out.append(f"  {'msgs':>10} {'in':>9} {'out':>11} "
                   f"{'cache read':>14} {'cache write':>14}   model")
        for model in sorted(chat_models, key=lambda m: -sum(chat_models[m][1:])):
            n, cin, cout, cread, cwrite = chat_models[model]
            out.append(f"  {n:>10,} {cin:>9,} {cout:>11,} {cread:>14,} "
                       f"{cwrite:>14,}   {model}")
        out.append(f"  {len(sessions)} session(s) swept.")
        if restarts:
            out.append(f"  {restarts} sweep(s) followed a discontinuity "
                       f"(the transcript was replaced, truncated or "
                       f"rewound): a rewound session legitimately loses "
                       f"turns and the delta is clamped at zero rather "
                       f"than going negative.")
        out.extend(_hookless_note(workspace if workspace is not None
                                  else folder.parent.parent,
                                  sessions - finalised))

    # --- the trend ----------------------------------------------------
    out.append("")
    out.append("per day (the trend)")
    for day in sorted(per_day):
        count, cost, chat_tokens = per_day[day]
        out.append(f"  {day}   ${cost:>8.2f} {count:>6} invocations "
                   f"{chat_tokens:>14,} chat tokens")

    if per_client:
        out.append("")
        out.append("top consumers (by est. $)")
        for name in sorted(per_client, key=lambda k: -per_client[k][1])[:top]:
            count, cost = per_client[name]
            out.append(f"  {name:<28} ${cost:>8.2f} {count:>6} calls")

    out.append("")
    if expensive:
        out.append(f"expensive single calls, last "
                   f"{min(len(expensive), top)} of {len(expensive)}")
        for record in expensive[-top:]:
            who = record["client"]
            tail = f"   [{who}]" if who else ""
            out.append(f"  {record.get('ts') or '?'}  "
                       f"${float(record.get('cost') or 0.0):>7.2f}  "
                       f"{record.get('purpose', '?')} | "
                       f"{record.get('model', '?')}{tail}")
    else:
        out.append("no single call crossed the expensive threshold in the "
                   "window")
    return out


def _hookless_note(workspace: str | Path, swept: set) -> list[str]:
    """The first time the SessionEnd hook is more than enrichment: without
    it a chat's final turns — often the largest part of a long session —
    are never counted. Stated rather than assumed.

    `swept` has already had the sessions whose tail WAS counted (a chat
    line carrying `final`) removed."""
    try:
        from . import clients

        hooked = {(m.get("engine"), m.get("session_id"))
                  for m in clients.all_markers(workspace)
                  if m.get("transcript_hint")}
    except Exception:
        return []
    # A marker carrying a hook-written `transcript_hint` is the evidence
    # that the SessionStart/SessionEnd pair is registered. A swept session
    # with no such marker was measured WITHOUT the hook, so its tail is
    # uncounted — which is worth saying, not assuming.
    missing = len([key for key in swept if key not in hooked])
    if not missing:
        return []
    return [f"  {missing} chat session(s) have no SessionEnd hook — their "
            f"final turns are not counted. "
            f"`authorlm client-hook --print-setup`."]


def _budget_block(directory: Path) -> list[str]:
    try:
        from . import budget

        return budget.report_block(directory)
    except Exception:
        return []
