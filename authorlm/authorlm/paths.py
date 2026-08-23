"""Where AuthorLM's files live.

Three kinds of file, three homes, deliberately not mixed:

- **Project config** — `config.toml` and `illustration-craft.md`. These
  are *decisions* (which model, which craft rules), so they belong in the
  repo beside the code that reads them, versioned and reviewable.
- **Workspace state** — the SQLite database, the Google OAuth token
  cache, backups, logs. Mutable, large, machine-local; it stays in
  `~/.authorlm` (or wherever `--workspace` points) and out of git.
- **Secrets** — API keys, in `.env` beside the config, gitignored and
  never committed. Config files name environment variables; they never
  carry a key themselves.

Environment overrides (`AUTHORLM_CONFIG`, `AUTHORLM_CRAFT`, `AUTHORLM_ENV`)
exist so a test or a second checkout can point elsewhere without moving
anything.
"""

from __future__ import annotations

import os
from pathlib import Path

CONFIG_FILENAME = "config.toml"
CRAFT_FILENAME = "illustration-craft.md"
ENV_FILENAME = ".env"


def project_dir() -> Path:
    """The directory holding pyproject.toml — the package's parent."""
    return Path(__file__).resolve().parent.parent


def _resolve(env_var: str, filename: str) -> Path:
    override = os.environ.get(env_var, "")
    return Path(override).expanduser().resolve() if override else (
        project_dir() / filename)


def config_path() -> Path:
    return _resolve("AUTHORLM_CONFIG", CONFIG_FILENAME)


def craft_path() -> Path:
    return _resolve("AUTHORLM_CRAFT", CRAFT_FILENAME)


def env_path() -> Path:
    return _resolve("AUTHORLM_ENV", ENV_FILENAME)


def load_env() -> list[str]:
    """Read `.env` into the environment and return the names it set.

    A real environment variable always wins — `.env` is the fallback for
    shells that were not exported into, never an override, so a key
    exported for one command cannot be silently replaced by a stale file.
    Deliberately hand-rolled: the format is four lines of parsing and the
    package otherwise has no runtime dependencies.
    """
    path = env_path()
    if not path.exists():
        return []
    loaded: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, sep, value = line.partition("=")
        if not sep:
            continue
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name and name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return loaded
