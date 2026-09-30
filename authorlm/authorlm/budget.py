"""The budget seam — built, tested, and dormant.

Sponsor ruling (2026-08-30): *"For now we don't need to cut off the usage
when the budget has reached, but it would be good to track usage so if
you ever want to take this to commercial stage, we can have that
ready-made."*

`[budget]` is a section the shipped `config.toml` does not have, the same
way `[writing]` is a section the shipped config does not have, and for a
related reason: **what is absent is what is off.** Three tiers, and only
the absence keeps them dormant:

| Tier | Behaviour | On when |
|---|---|---|
| Report | `authorlm usage` prints the budget block | `[budget]` present |
| Warn   | one stderr line per process past `warn_at` | `[budget]` present |
| Enforce| `gate()` REFUSES the call | `[budget]` **and** `enforce = true` |

`settings()` is `dbperf.settings()`'s twin — memoized on the resolved
config path, `try/except` around everything — with **one inverted default
that matters**. `dbperf` defaults ON because a broken config must not be
a silent off-switch for a MEASUREMENT. This defaults OFF for the
mirror-image reason: a broken config must not be a silent REFUSAL for a
POLICY. An unreadable config means no budget, no warning, no gate. The
asymmetry is a decision, not an inconsistency.

**Why a `gate()` that returns `None` on every shipped configuration is
worth shipping.** "Commercial-ready" is a claim about a seam, and a seam
that has never been exercised is a plan, not a seam. The gate is called
on every billable path today and permits every call today;
`check_budget_gate_refuses_only_when_enforcing` sets `enforce = true` in
a temp config and proves it would refuse. The day someone wants a hard
cap, the change is a config edit, not a code change — which is precisely
what the ruling asked for.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class BudgetExceeded(RuntimeError):
    """Subclasses `RuntimeError`, which `mcp_server._guard` already catches
    into `{"ok": false, "error": …}` and which the CLI verbs already
    surface. Enforcement, if ever switched on, degrades the way every
    other refusal in this system degrades — a sentence, not a
    traceback."""


# The refusal mirrors `WRITING_ABSENT`: it names the section, states the
# consequence, and gives the exact recipe BOTH ways.
BUDGET_EXCEEDED = (
    "refused: today's estimated API spend is ${spent:.2f}, at or over the "
    "${cap:.2f}/day cap in [budget] ({config}).\n"
    "Nothing was sent and nothing was charged.\n"
    "Raise api_dollars_per_day, or set enforce = false to go back to "
    "warnings only, or wait — the cap is per calendar day (UTC).\n"
    "The estimate is litellm's public price list, not your invoice: "
    "`authorlm usage --days 1` shows what it counted.")

WARNING = (
    "warning: today's estimated API spend is ${spent:.2f} — {pct:.0f}% of "
    "the ${cap:.2f}/day budget ([budget] api_dollars_per_day, "
    "config.toml). Nothing is being refused; this is a warning only.")

DEFAULT_WARN_AT = 0.8


@dataclass(frozen=True)
class Settings:
    present: bool = False
    api_dollars_per_day: float = 0.0
    warn_at: float = DEFAULT_WARN_AT
    enforce: bool = False


OFF = Settings()

_SETTINGS: dict[str, Settings] = {}
# One line per process: a budget crossing is a standing fact, and saying
# it on every call of a 24-essay rebuild is noise the author learns to
# scroll past (`llm._note_once`'s idiom).
_NOTED: set = set()
_DAY_BASE: dict[tuple, float] = {}


def settings() -> Settings:
    key = ""
    try:
        from . import paths

        key = str(paths.config_path())
        cached = _SETTINGS.get(key)
        if cached is not None:
            return cached
        path = Path(key)
        if not path.exists():
            found = OFF
        else:
            import tomllib

            section = tomllib.loads(
                path.read_text(encoding="utf-8")).get("budget")
            if not isinstance(section, dict):
                found = OFF
            else:
                found = Settings(
                    present=True,
                    api_dollars_per_day=float(
                        section.get("api_dollars_per_day") or 0.0),
                    warn_at=float(section.get("warn_at", DEFAULT_WARN_AT)),
                    enforce=bool(section.get("enforce", False)))
    except Exception:
        # A tracking feature must not become an outage.
        return OFF
    _SETTINGS[key] = found
    return found


def reset() -> None:
    """Tests only."""
    _SETTINGS.clear()
    _NOTED.clear()
    _DAY_BASE.clear()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def day_base(directory: Path | str) -> float:
    """Today's estimated spend already ON DISK, memoized per (directory,
    day) so the common (no `[budget]`) path never reads the file at all
    and a long-lived MCP process reads it once per day."""
    day = _today()
    key = (str(directory), day)
    if key in _DAY_BASE:
        return _DAY_BASE[key]
    total = 0.0
    try:
        from . import usage

        for entry in usage.read_entries(directory):
            if entry.get("kind") != "api" or entry.get("expensive"):
                continue
            if str(entry.get("ts") or "")[:10] != day:
                continue
            total += float(entry.get("est_cost_usd") or 0.0)
    except Exception:
        total = 0.0
    _DAY_BASE[key] = total
    return total


def note_flushed(directory: Path | str, amount: float) -> None:
    """Keep the memoized day base in step with an aggregate line just
    written to disk. Without this a long-lived process (the MCP server
    flushes per tool call) would count only the base read at first use
    plus the current pending aggregate, dropping every earlier flush.
    Only touches an entry that already exists, so the no-`[budget]` path
    and a not-yet-read base stay untouched. Never raises."""
    try:
        key = (str(directory), _today())
        if key in _DAY_BASE:
            _DAY_BASE[key] += max(float(amount or 0.0), 0.0)
    except Exception:
        pass


def day_total(directory: Path | str, pending: float = 0.0) -> float:
    """The ledger's own lines for today plus the un-flushed in-memory
    aggregate."""
    return day_base(directory) + max(pending, 0.0)


def note_spend(directory: Path | str, pending: float = 0.0) -> None:
    """At most one stderr line per process, past `warn_at · cap`.
    A no-op — and a cheap one — with no `[budget]`."""
    try:
        config = settings()
        if not config.present or config.api_dollars_per_day <= 0:
            return
        if "budget-warning" in _NOTED:
            return
        spent = day_total(directory, pending)
        if spent < config.warn_at * config.api_dollars_per_day:
            return
        _NOTED.add("budget-warning")
        print(WARNING.format(spent=spent, cap=config.api_dollars_per_day,
                             pct=100.0 * spent / config.api_dollars_per_day),
              file=sys.stderr)
    except Exception:
        pass


def gate(purpose: str, model: str) -> None:
    """Refuse a call that would exceed the configured daily cap.

    Returns `None` — i.e. permits — unless `[budget]` is present AND
    `enforce = true` AND the day's estimated spend is already at or over
    `api_dollars_per_day`. **NO SHIPPED CONFIGURATION SETS `enforce`**;
    this is the commercial-readiness seam, built and tested and off.

    Never raises anything but `BudgetExceeded`: a failure to READ the
    budget PERMITS the call. A tracking feature must not become an
    outage."""
    try:
        config = settings()
        if not (config.present and config.enforce
                and config.api_dollars_per_day > 0):
            return None
        from . import clients, paths, usage

        directory = usage.log_dir(clients.default_workspace())
        spent = day_total(directory,
                          float(usage.pending().get("est_cost_usd") or 0.0))
        if spent < config.api_dollars_per_day:
            return None
        message = BUDGET_EXCEEDED.format(spent=spent,
                                         cap=config.api_dollars_per_day,
                                         config=paths.config_path())
    except BudgetExceeded:
        raise
    except Exception:
        return None
    raise BudgetExceeded(message)


def report_block(directory: Path | str) -> list[str]:
    """The report's budget block — present iff `[budget]` is."""
    config = settings()
    if not config.present or config.api_dollars_per_day <= 0:
        return []
    spent = day_base(directory)
    fraction = spent / config.api_dollars_per_day
    filled = max(0, min(20, int(round(fraction * 20))))
    bar = "█" * filled + "░" * (20 - filled)
    mark = ("  ⚠ over the "
            f"{config.warn_at:.0%} warning mark"
            if fraction >= config.warn_at else "  — no warning")
    out = [f"budget — ${config.api_dollars_per_day:.2f}/day "
           f"(api_dollars_per_day), warn at {config.warn_at:.0%}",
           f"  today  ${spent:.2f}  {bar} {fraction:>4.0%} {mark}"]
    if not config.enforce:
        out.append("  NOT ENFORCED. No call is refused at any level; this "
                   "is a tracking threshold. See [budget] in config.toml.")
    else:
        out.append("  ENFORCED: calls past the cap are refused "
                   "([budget] enforce = true).")
    return out
