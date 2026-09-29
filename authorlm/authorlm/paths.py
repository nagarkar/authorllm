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

`.env` also holds the one thing that cannot live in the workspace: the
pointer to it. `AUTHORLM_WORKSPACE=<dir>` there names the directory whose
`.authorlm/` holds this checkout's database, so a checkout is a tenant and
the database can sit on another volume. The pointer stays in the checkout
because a file inside the workspace cannot tell you where the workspace
is. `set_env_value` writes it; `authorlm setup` is the verb that asks.

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
WORKSPACE_ENV = "AUTHORLM_WORKSPACE"


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


def set_env_value(name: str, value: str) -> Path:
    """Write `name=value` into `.env`, replacing an existing line for the
    same name or appending one. Every other line is kept byte-for-byte:
    the file holds the author's API keys, and a rewrite that reflowed
    them would be a worse bug than the one this function fixes. Creates
    the file (mode 0600) when there is none. Also sets the process
    environment so the caller sees its own write."""
    path = env_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    new_line = f"{name}={value}"
    replaced = False
    for i, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key = line.partition("=")[0].strip()
        if key == name and not line.startswith("#"):
            lines[i] = new_line
            replaced = True
            break
    if not replaced:
        lines.append(new_line)
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not existed:
        path.chmod(0o600)
    os.environ[name] = value
    return path
