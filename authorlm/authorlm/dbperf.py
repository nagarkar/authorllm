"""Always-on database performance log.

Sponsor ruling (AN): *"configure the databases to by default log stuff and
make sure that we can later on see performance logs from the database
itself instead of us having to do one-off measurements … monitor the
performance over all time."*

`trace.jsonl` (see `tracelog`) is the **verb** timeline: one line per CLI
command or MCP tool call, with the wall time of the whole operation. It
cannot say *where* that time went. This is the layer underneath it: every
query through `Database.one/all/insert/update`, plus the `BEGIN
IMMEDIATE` and the `COMMIT` that bracket a transaction, timed with
`perf_counter` and aggregated **in memory** per normalized SQL shape.

Two kinds of line land in `<workspace>/.authorlm/logs/db-perf.jsonl`:

- **one aggregate line per invocation** — per CLI process, per MCP tool
  dispatch — carrying `{ts, invocation, client, shapes, grand_total_ms}`,
  where `shapes` maps the normalized SQL to `[n, total_ms, max_ms]`.
  Aggregating in memory and writing once is what keeps this affordable
  enough to ship on: a verb issuing 900 queries costs 900 dict updates
  and **one** line of I/O.
- **a slow line, immediately**, for any single query over `[db] slow_ms`
  (default 100), tagged `"slow": true` and carrying that one query's
  shape and duration. The aggregate already counts it; this line is the
  detail — when it happened, and under which client.

What it does **not** see, so the report is not read as more than it is:
the handful of call sites that reach past the class for `db.conn.execute`
directly (all of them one-shot DELETEs and metadata UPDATEs), and the
schema/migration statements `Database.__init__` issues the same way.
Route a new query through `one/all/insert/update` and it is measured;
that is the only rule.

`client` is the provenance join key from `clients.current()` (§15.15),
resolved **once per recorder** and reused, so the perf log joins straight
to `trace.jsonl`, to row metadata, and to the chat transcript.

**Never in the way.** Every write is wrapped and swallows its own
failures — a full disk, an unwritable directory, an unserializable value.
A performance log that can break a `collect` is worse than no performance
log. The log write is outside every timing window, so it never measures
itself, and a workspace that has been deleted is never resurrected by a
late flush. `perf_log = false` in `[db]` makes `Database` hold no recorder
at all: the cost then is one `is None` test per query.

Read it with `authorlm dbperf` (`report()` below).
"""

from __future__ import annotations

import atexit
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

FILENAME = "db-perf.jsonl"
MAX_BYTES = 5 * 1024 * 1024   # rotate beyond this, matching trace.jsonl
GENERATIONS = 2               # db-perf.jsonl.1, db-perf.jsonl.2
DEFAULT_SLOW_MS = 100
SHAPE_MAX = 120               # a normalized shape is truncated to this

# Synthetic shapes for the two costs that are real but are not a statement
# the caller wrote. Timing the whole `transaction()` body instead would
# double-count every query inside it and make `grand_total_ms` a lie.
BEGIN_SHAPE = "BEGIN IMMEDIATE"
COMMIT_SHAPE = "-- COMMIT"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(sql: Any) -> str:
    """The aggregation key: whitespace collapsed, truncated.

    Deliberately NOT a literal-stripping parser. Every hot query in this
    codebase is parameterized (`?`), so collapsing whitespace already
    folds the same statement written across three source lines into one
    shape; the handful of f-string-built statements differ by table name,
    which is exactly the distinction worth keeping."""
    return " ".join(str(sql).split())[:SHAPE_MAX]


# --------------------------------------------------------------- config

_SETTINGS: dict[str, tuple[bool, int]] = {}


def settings() -> tuple[bool, int]:
    """`(perf_log, slow_ms)` from config.toml's `[db]` section.

    Memoized on the resolved config path, so a test pointing
    `AUTHORLM_CONFIG` elsewhere gets a fresh read without a reset hook.
    Shipped ON: an absent file, an absent section and an unreadable
    config all mean the default, because a broken config must not be a
    silent off-switch."""
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
            section = parsed.get("db") or {}
        found = (bool(section.get("perf_log", True)),
                 int(section.get("slow_ms", DEFAULT_SLOW_MS)))
    except Exception:
        return (True, DEFAULT_SLOW_MS)
    _SETTINGS[key] = found
    return found


def log_dir(workspace: str | Path | None = None) -> Path:
    """Where the log lives, from a workspace — the reader's entry point.
    Same shape as `tracelog.log_dir`, and the same directory."""
    base = Path(workspace).resolve() if workspace else Path.home()
    return base / ".authorlm" / "logs"


# -------------------------------------------------------------- recorder

# Recorders holding data not yet written. A recorder adds itself on its
# first sample and drops itself on flush, so this never grows past the
# number of databases with pending samples — which matters: the MCP
# server opens a fresh `Database` per tool call and lives for days.
_PENDING: list[Recorder] = []


class Recorder:
    """One database's in-memory aggregation. Lives on the `Database`."""

    __slots__ = ("directory", "slow_ms", "shapes", "_listed",
                 "_client", "_client_resolved")

    def __init__(self, directory: Path, slow_ms: int = DEFAULT_SLOW_MS):
        self.directory = directory
        self.slow_ms = slow_ms
        self.shapes: dict[str, list] = {}
        self._listed = False
        self._client: dict | None = None
        self._client_resolved = False

    # -- writing side ----------------------------------------------------

    def record(self, sql: Any, elapsed: float) -> None:
        """Fold one measured query in. Called on the hot path: a dict
        lookup and three arithmetic ops, no I/O unless the query was
        slow."""
        shape = normalize(sql)
        row = self.shapes.get(shape)
        if row is None:
            self.shapes[shape] = [1, elapsed, elapsed]
        else:
            row[0] += 1
            row[1] += elapsed
            if elapsed > row[2]:
                row[2] = elapsed
        if not self._listed:
            self._listed = True
            _PENDING.append(self)
        ms = elapsed * 1000.0
        if self.slow_ms > 0 and ms > self.slow_ms:
            self._write({"ts": _now(), "slow": True, "sql": shape,
                         "ms": round(ms, 3)})

    def flush(self, invocation: str | None = None) -> None:
        """Write this invocation's one aggregate line and start over."""
        shapes, self.shapes = self.shapes, {}
        if self._listed:
            self._listed = False
            try:
                _PENDING.remove(self)
            except ValueError:
                pass
        if not shapes:
            return
        self._write({
            "ts": _now(),
            "invocation": invocation or "",
            "shapes": {shape: [n, round(total * 1000.0, 3),
                               round(worst * 1000.0, 3)]
                       for shape, (n, total, worst) in shapes.items()},
            "grand_total_ms": round(
                sum(v[1] for v in shapes.values()) * 1000.0, 3),
        })

    def client(self) -> dict | None:
        """The provenance join key (§15.15), resolved once per recorder —
        i.e. once per CLI process and once per MCP dispatch, which is what
        "resolved once per invocation" means. Omitted entirely when
        detection is off, exactly as `tracelog` omits it."""
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
        try:
            who = self.client()
            if who:
                entry["client"] = who
            if not self.directory.exists():
                # Never resurrect a workspace that has been deleted out
                # from under a late flush: create `logs/`, not its parent.
                if not self.directory.parent.is_dir():
                    return
                self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / FILENAME
            if path.exists() and path.stat().st_size > MAX_BYTES:
                _rotate(path)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
        except Exception:
            pass  # a perf log must never break the verb it measures


def _generation(path: Path, n: int) -> Path:
    return path.with_name(f"{path.name}.{n}")


def _rotate(path: Path) -> None:
    """`db-perf.jsonl` → `.1` → `.2`, oldest dropped. Two generations of
    history at the 5 MB cap, the same cap `trace.jsonl` uses."""
    try:
        _generation(path, GENERATIONS).unlink()
    except OSError:
        pass
    for n in range(GENERATIONS - 1, 0, -1):
        older = _generation(path, n)
        if older.exists():
            older.replace(_generation(path, n + 1))
    path.replace(_generation(path, 1))


def recorder_for(db_path: str | Path) -> Recorder | None:
    """The recorder for a database file, or `None` when `[db] perf_log`
    is false — in which case `Database` pays one `is None` test per query
    and nothing else."""
    try:
        enabled, slow_ms = settings()
        if not enabled:
            return None
        return Recorder(Path(db_path).resolve().parent / "logs", slow_ms)
    except Exception:
        return None


def flush(invocation: str | None = None) -> None:
    """Write the aggregate line for every database with pending samples.

    Called by the CLI at the end of dispatch (which is reached on the
    `sys.exit` paths too) and by the MCP server after each tool call;
    also registered with `atexit` as the backstop for any path that
    reaches neither."""
    for recorder in list(_PENDING):
        try:
            recorder.flush(invocation)
        except Exception:
            pass


atexit.register(flush)


# ------------------------------------------------------------- reporting

def read_entries(directory: str | Path) -> list[dict]:
    """Every line in the log and its rotated generations, oldest first."""
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


def _client_name(entry: dict) -> str | None:
    who = entry.get("client")
    if not isinstance(who, dict):
        return None
    session = str(who.get("session") or "-")
    return f"{who.get('engine', '?')}/{session[:12]}"


def report(directory: str | Path, days: int = 7, top: int = 10) -> list[str]:
    """The rendered report, as lines. Read-only; honest about an empty or
    absent log rather than printing an empty table."""
    folder = Path(directory)
    entries = read_entries(folder)
    if not entries:
        return [f"no performance log yet  ({folder / FILENAME})"]
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=max(days, 0))).strftime("%Y-%m-%d")
    window = [e for e in entries if str(e.get("ts") or "")[:10] >= cutoff]
    if not window:
        return [f"no entries in the last {days} days "
                f"({len(entries)} older lines in {folder / FILENAME})"]

    shapes: dict[str, list] = {}
    per_day: dict[str, list] = {}
    per_client: dict[str, list] = {}
    slow: list[dict] = []
    invocations = 0
    for entry in window:
        if entry.get("slow"):
            # Already counted inside its invocation's aggregate; this line
            # is the detail record, not a second sample.
            slow.append(entry)
            continue
        invocations += 1
        day = str(entry.get("ts") or "")[:10]
        total = float(entry.get("grand_total_ms") or 0.0)
        bucket = per_day.setdefault(day, [0, 0.0])
        bucket[0] += 1
        bucket[1] += total
        name = _client_name(entry)
        if name:
            seen = per_client.setdefault(name, [0, 0.0])
            seen[0] += 1
            seen[1] += total
        for shape, stats in (entry.get("shapes") or {}).items():
            try:
                n, shape_total, worst = (int(stats[0]), float(stats[1]),
                                         float(stats[2]))
            except Exception:
                continue
            row = shapes.setdefault(shape, [0, 0.0, 0.0])
            row[0] += n
            row[1] += shape_total
            if worst > row[2]:
                row[2] = worst

    out = [f"db performance — last {days} days   "
           f"{invocations} invocations, {len(slow)} slow, "
           f"{len(shapes)} query shapes",
           f"  {folder / FILENAME}"]

    def table(title: str, ranked: list[tuple[str, list]]) -> None:
        out.append("")
        out.append(title)
        out.append(f"  {'total_ms':>10}  {'n':>7}  {'max_ms':>8}  query")
        for shape, (n, total, worst) in ranked:
            out.append(f"  {total:>10.1f}  {n:>7}  {worst:>8.1f}  {shape}")

    if shapes:
        table("top by total time",
              sorted(shapes.items(), key=lambda kv: -kv[1][1])[:top])
        table("top by slowest single query",
              sorted(shapes.items(), key=lambda kv: -kv[1][2])[:top])

    out.append("")
    out.append("per day (the trend)")
    for day in sorted(per_day):
        count, total = per_day[day]
        out.append(f"  {day}  {total:>10.1f} ms over {count} invocations")

    out.append("")
    if slow:
        out.append(f"slow queries (over the configured threshold), "
                   f"last {min(len(slow), top)} of {len(slow)}")
        for entry in slow[-top:]:
            who = _client_name(entry)
            tail = f"  [{who}]" if who else ""
            out.append(f"  {entry.get('ts', '?')}  "
                       f"{float(entry.get('ms') or 0.0):>8.1f} ms  "
                       f"{entry.get('sql', '?')}{tail}")
    else:
        out.append("no slow queries in the window")

    if per_client:
        out.append("")
        out.append("by client")
        for name in sorted(per_client, key=lambda k: -per_client[k][1]):
            count, total = per_client[name]
            out.append(f"  {name:<28} {count:>6} invocations, "
                       f"{total:>10.1f} ms")
    return out
