""".env parse and rewrite (`paths.load_env` / `paths.set_env_value`).

Hand-rolled on purpose: quote strip, `export ` prefix, comments, and
"real env wins" so a stale file cannot override a key the shell already
exported. `set_env_value` must replace one name while keeping every other
line byte-for-byte — the file holds API keys. Isolation suites only
assert `load_env() == []`; nothing pins these branches.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import paths


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


class _EnvFile:
    """Point AUTHORLM_ENV at a temp .env and scrub names we touch."""

    def __init__(self, body: str | None = None):
        self._td = tempfile.TemporaryDirectory()
        self.path = Path(self._td.name) / ".env"
        if body is not None:
            self.path.write_text(body, encoding="utf-8")
        self._prev_env = os.environ.get("AUTHORLM_ENV")
        self._cleared: list[tuple[str, str | None]] = []

    def __enter__(self):
        os.environ["AUTHORLM_ENV"] = str(self.path)
        return self

    def __exit__(self, *exc):
        for name, prior in self._cleared:
            if prior is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = prior
        if self._prev_env is None:
            os.environ.pop("AUTHORLM_ENV", None)
        else:
            os.environ["AUTHORLM_ENV"] = self._prev_env
        self._td.cleanup()

    def track(self, name: str) -> None:
        """Remember `name` so __exit__ restores its prior value."""
        if any(n == name for n, _ in self._cleared):
            return
        self._cleared.append((name, os.environ.get(name)))


def test_load_quoted_and_export_prefix() -> None:
    body = (
        "# comment\n"
        "export QUOTED_DOUBLE=\"hello world\"\n"
        "QUOTED_SINGLE='one'\n"
        "BARE=plain\n"
        "export SPACED =  spaced  \n"
        "NOEQUALS\n"
        "\n"
    )
    with _EnvFile(body) as env:
        for name in ("QUOTED_DOUBLE", "QUOTED_SINGLE", "BARE", "SPACED"):
            env.track(name)
            os.environ.pop(name, None)
        loaded = paths.load_env()
        check("loads every well-formed name",
              set(loaded) == {"QUOTED_DOUBLE", "QUOTED_SINGLE", "BARE",
                              "SPACED"},
              loaded)
        check("double quotes are stripped",
              os.environ["QUOTED_DOUBLE"] == "hello world")
        check("single quotes are stripped",
              os.environ["QUOTED_SINGLE"] == "one")
        check("bare values keep their text", os.environ["BARE"] == "plain")
        check("spaces around name/value are trimmed",
              os.environ["SPACED"] == "spaced")


def test_load_skips_already_set() -> None:
    with _EnvFile("KEEP_ME=from-file\n") as env:
        env.track("KEEP_ME")
        os.environ["KEEP_ME"] = "from-shell"
        loaded = paths.load_env()
        check("already-set names are not reported as loaded",
              loaded == [], loaded)
        check("real environment wins over .env",
              os.environ["KEEP_ME"] == "from-shell")


def test_load_missing_file() -> None:
    with _EnvFile(body=None) as env:
        # Ensure the path does not exist.
        check("fixture starts without a file", not env.path.exists())
        check("missing .env returns []", paths.load_env() == [])


def test_set_env_preserves_siblings() -> None:
    body = (
        "# keep me\n"
        "export OLD_KEY=first\n"
        "OTHER=leave-me\n"
        "  spaced_name = before  \n"
    )
    with _EnvFile(body) as env:
        env.track("OLD_KEY")
        env.track("NEW_KEY")
        os.environ.pop("OLD_KEY", None)
        os.environ.pop("NEW_KEY", None)
        paths.set_env_value("OLD_KEY", "second")
        paths.set_env_value("NEW_KEY", "added")
        text = env.path.read_text(encoding="utf-8")
        check("comment line is preserved byte-for-byte",
              text.splitlines()[0] == "# keep me", text)
        check("unrelated key line is preserved",
              "OTHER=leave-me" in text.splitlines(), text)
        check("matching export line is replaced, not duplicated",
              text.count("OLD_KEY=") == 1 and "OLD_KEY=second" in text, text)
        check("new name is appended", "NEW_KEY=added" in text, text)
        check("process env sees the write",
              os.environ["OLD_KEY"] == "second"
              and os.environ["NEW_KEY"] == "added")
        # The spaced_name line is kept even though we never touched it.
        check("unmatched lines keep their original spacing",
              "  spaced_name = before  " in text.splitlines(), text)


def test_set_env_creates_mode_0600() -> None:
    with _EnvFile(body=None) as env:
        env.track("ONLY")
        os.environ.pop("ONLY", None)
        check("starts without a file", not env.path.exists())
        out = paths.set_env_value("ONLY", "secret")
        check("returns the env path", out == env.path)
        check("creates the file", env.path.exists())
        mode = stat.S_IMODE(env.path.stat().st_mode)
        check("new file is mode 0600", mode == 0o600, oct(mode))
        check("contents are the single assignment",
              env.path.read_text(encoding="utf-8") == "ONLY=secret\n")


def test_set_env_skips_commented_name() -> None:
    with _EnvFile("# HIDDEN=old\nHIDDEN=live\n") as env:
        env.track("HIDDEN")
        os.environ.pop("HIDDEN", None)
        paths.set_env_value("HIDDEN", "new")
        lines = env.path.read_text(encoding="utf-8").splitlines()
        check("commented assignment is left alone",
              lines[0] == "# HIDDEN=old", lines)
        check("live assignment is the one replaced",
              lines[1] == "HIDDEN=new", lines)


def main() -> None:
    tests = [
        test_load_quoted_and_export_prefix,
        test_load_skips_already_set,
        test_load_missing_file,
        test_set_env_preserves_siblings,
        test_set_env_creates_mode_0600,
        test_set_env_skips_commented_name,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
