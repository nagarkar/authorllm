"""Terminal styling helpers.

ANSI colors apply only when stdout is a real TTY and NO_COLOR is unset, so
piped output, captured test output, and color-averse environments all get
plain text. Style is semantic: headers bold-cyan, positive facts green,
warnings yellow, metadata/hints dim.
"""

from __future__ import annotations

import os
import sys


def _on() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _wrap(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _on() else text


def bold(text: str) -> str:
    return _wrap("1", text)


def dim(text: str) -> str:
    return _wrap("2", text)


def cyan(text: str) -> str:
    return _wrap("36", text)


def green(text: str) -> str:
    return _wrap("32", text)


def yellow(text: str) -> str:
    return _wrap("33", text)


def header(text: str) -> str:
    return _wrap("1;36", text)


def is_tty() -> bool:
    return sys.stdout.isatty()


def link(url: str, text: str | None = None) -> str:
    """OSC-8 terminal hyperlink (clickable in iTerm2, VS Code, kitty,
    WezTerm; terminals without support show the plain URL — in stock macOS
    Terminal, Cmd+double-click opens it)."""
    label = text or url
    if _on():
        return f"\033]8;;{url}\033\\{label}\033]8;;\033\\"
    return url if text is None else f"{label} ({url})"


def shorten(text: str, limit: int = 70) -> str:
    """Truncate at a word boundary with an ellipsis."""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"
