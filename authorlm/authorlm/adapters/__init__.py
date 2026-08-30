"""Engine-specific hook adapters.

`authorlm.clients` owns the engine-agnostic contract (the `Client`
descriptor, the resolution layers, the marker file format). This package
holds the per-engine glue that a chat engine's own session hooks call
into — today only Claude Code's.

The marker schema is engine-agnostic on purpose:
`{engine, session_id, label, started_at, transcript_hint, host_pid, cwd,
source, updated_at}`. The reader does not care which engine wrote it, so
a second engine is a second module here plus one entry in
`clients.ADAPTERS` — no stamp-site change, no query-surface change, no
schema change, no migration."""

from . import claude_code

__all__ = ["claude_code"]
