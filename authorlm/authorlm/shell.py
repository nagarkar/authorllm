"""Interactive authoring shell and manuscript watcher.

The shell IS the authoring session (RFC AuthorLM §20.2): it opens with the
learning briefing, commands are typed without the `authorlm` prefix, and a
background watcher collects a revision whenever the manuscript files settle
after a change — observation becomes invisible (execution Level 0), and the
author interacts only at the meaningful moments: declaring intent and
reviewing guidance.
"""

from __future__ import annotations

import shlex
import sys
import threading
import time
from pathlib import Path

from .revisions import iter_manuscript_paths

PROMPT = "authorlm> "  # overridden per-manuscript in run_shell
BLOCKED_IN_SHELL = {"shell", "watch"}


class Watcher:
    """Debounced change detector over the manuscript directory. poll()
    returns True exactly once per settled burst of edits."""

    def __init__(self, root: Path, debounce: float = 3.0):
        self.root = root
        self.debounce = debounce
        self.last_signature = self._signature()
        self.dirty_at: float | None = None

    def _signature(self) -> dict:
        signature = {}
        for rel, path in iter_manuscript_paths(self.root).items():
            try:
                stat = path.stat()
            except OSError:
                continue
            signature[rel] = (stat.st_mtime_ns, stat.st_size)
        return signature

    def poll(self) -> bool:
        signature = self._signature()
        if signature != self.last_signature:
            self.last_signature = signature
            self.dirty_at = time.monotonic()
            return False
        if self.dirty_at is not None and time.monotonic() - self.dirty_at >= self.debounce:
            self.dirty_at = None
            return True
        return False


def print_command_help(topic: str | None = None, in_shell: bool = False) -> None:
    """`help` lists every command with its one-liner; `help <command>`
    prints that command's full argparse usage and options. Used by both the
    shell and the `authorlm help` CLI command."""
    import argparse

    from .cli import build_parser

    parser = build_parser()
    sub_action = next(
        action for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    hidden = BLOCKED_IN_SHELL if in_shell else set()
    if topic:
        subparser = sub_action.choices.get(topic)
        if subparser is None or topic in hidden:
            print(f"error: unknown command '{topic}'. Type 'help' for the list.")
            return
        print(subparser.format_help().rstrip())
        return

    print("Commands (type 'help <command>' for options):")
    for choice in sub_action._choices_actions:
        if choice.dest in hidden or choice.dest.startswith("_"):
            continue
        print(f"  {choice.dest:<16} {choice.help or ''}")
    if in_shell:
        print(
            "\nShell notes:\n"
            "  help [command]   this help / a command's options\n"
            "  review N accept  shorthand for review N --accept (also reject/modify/defer)\n"
            "  exit, quit       leave the shell (the session stays active unless\n"
            "                   you ran 'session end')\n"
            "  Edits you save are collected automatically while the watcher is on."
        )
    else:
        print(
            "\nGlobal options: -m/--manuscript <name>, -w/--workspace <dir> "
            "(before or after the command).\n"
            "Tip: 'authorlm shell' opens the interactive authoring session."
        )


# Backwards-compatible alias for the shell loop.
def print_shell_help(topic: str | None = None) -> None:
    print_command_help(topic, in_shell=True)


def translate_line(tokens: list[str]) -> list[str]:
    """Shell conveniences: 'review 2 accept' → 'review 2 --accept';
    'concept help' → 'help concept'."""
    if (
        len(tokens) >= 3
        and tokens[0] == "review"
        and tokens[2] in ("accept", "reject", "modify", "defer")
    ):
        tokens = [tokens[0], tokens[1], f"--{tokens[2]}", *tokens[3:]]
    if len(tokens) == 2 and tokens[1] in ("help", "--help", "-h"):
        tokens = ["help", tokens[0]]
    return tokens


def _dispatch(base_argv: list[str], tokens: list[str]) -> None:
    """Run one CLI command in-process; argparse/command errors print and
    return to the prompt instead of killing the shell."""
    from .cli import main as cli_main  # late import; cli imports this module

    try:
        cli_main([*base_argv, *tokens])
    except SystemExit as err:
        if isinstance(err.code, str):
            print(err.code)
    except KeyboardInterrupt:
        print("(interrupted)")
    except Exception as err:  # noqa: BLE001 — the REPL must survive
        # A command's crash (network timeout, bug) returns to the prompt;
        # it must never take the whole shell session down with it.
        print(f"error ({type(err).__name__}): {err}")


def _collect_via_watcher(base_argv: list[str], lock: threading.Lock, prompt: str) -> None:
    with lock:
        print()  # break out of the prompt line
        _dispatch(base_argv, ["collect", "--auto"])
        print(prompt, end="", flush=True)


def shell_candidates(db, manuscript: dict, buffer: str) -> list[str]:
    """Completion candidates for the shell line so far: commands, actions,
    flags, option values, and live ids/names (intents, beliefs, concepts,
    edges, docs)."""
    from .cli import list_ids
    from .completion import DYNAMIC_POSITIONALS, SIMPLE_POSITIONALS, collect_words

    commands, command_words, value_choices = collect_words()
    visible = [c for c in commands if c not in BLOCKED_IN_SHELL]
    try:
        tokens = shlex.split(buffer)
    except ValueError:
        tokens = buffer.split()
    completing_new = buffer.endswith(" ") or not buffer
    if not tokens or (len(tokens) == 1 and not completing_new):
        return visible + ["help", "exit", "quit"]
    cmd = tokens[0]
    if cmd in ("help", "?"):
        return visible
    if cmd not in command_words:
        return []
    choice_map = {opt: values for opt, values in value_choices[cmd] if values}
    prev = tokens[-1] if completing_new else (tokens[-2] if len(tokens) >= 2 else "")
    if prev in choice_map:
        return choice_map[prev]
    position = len(tokens) if completing_new else len(tokens) - 1
    action = tokens[1] if len(tokens) > 1 else ""
    kind = DYNAMIC_POSITIONALS.get(cmd, {}).get(action)
    words = list(command_words[cmd])
    if cmd in SIMPLE_POSITIONALS and position >= 1:
        words = (list_ids(db, manuscript, SIMPLE_POSITIONALS[cmd])
                 + [w for w in words if w.startswith("--")])
    elif kind and position >= 2:
        words = list_ids(db, manuscript, kind) + [w for w in words if w.startswith("--")]
    return words


def _install_completer(db, manuscript: dict) -> None:
    """Wire shell_candidates into readline. Multiword names are inserted
    quoted so they parse as one argument."""
    try:
        import readline
    except ImportError:
        return

    def complete(text: str, state: int):
        buffer = readline.get_line_buffer()
        matches = []
        for cand in shell_candidates(db, manuscript, buffer):
            if cand.lower().startswith(text.lower()):
                matches.append(f'"{cand}"' if " " in cand else cand)
        return matches[state] if state < len(matches) else None

    readline.set_completer(complete)
    readline.set_completer_delims(" \t\n")
    if "libedit" in (getattr(readline, "__doc__", "") or ""):
        readline.parse_and_bind("bind ^I rl_complete")  # macOS libedit
    else:
        readline.parse_and_bind("tab: complete")


HISTORY_LIMIT = 1000


def _install_history(workspace: str):
    """Load persistent cross-run history (~/.authorlm/shell_history) and
    return the readline module, or None when readline is unavailable.
    Only main-prompt commands are meant to persist; run_shell trims the
    entries that nested prompts (triage keystrokes, reject reasons) add
    during a command, so up-arrow and the file stay meaningful."""
    try:
        import readline
    except ImportError:
        return None
    path = Path(workspace) / ".authorlm" / "shell_history"
    try:
        readline.read_history_file(str(path))
    except (FileNotFoundError, OSError):
        pass
    readline.set_history_length(HISTORY_LIMIT)
    return readline


def _save_history(readline_mod, workspace: str) -> None:
    if readline_mod is None:
        return
    path = Path(workspace) / ".authorlm" / "shell_history"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        readline_mod.write_history_file(str(path))
    except OSError:
        pass  # history is a convenience — never let it break exit


def _trim_nested_history(readline_mod, baseline: int) -> None:
    """Drop history entries added by prompts inside the command that just
    ran (triage answers and the like). Degrades silently where libedit
    lacks remove_history_item."""
    if readline_mod is None or not hasattr(readline_mod, "remove_history_item"):
        return
    try:
        while readline_mod.get_current_history_length() > baseline:
            readline_mod.remove_history_item(
                readline_mod.get_current_history_length() - 1)
    except (ValueError, OSError):
        pass


def run_shell(args, db, manuscript: dict) -> None:
    from . import sessions as ses
    from .cli import _print_briefing  # late import (see _dispatch)

    _install_completer(db, manuscript)
    readline_mod = _install_history(args.workspace)

    base_argv = ["--workspace", args.workspace, "--manuscript", manuscript["name"]]
    lock = threading.Lock()
    prompt = f"{manuscript['name']}› "

    session = ses.active_session(db, manuscript["id"])
    if session:
        print(f"Resuming active session {session['id']} for '{manuscript['name']}'.\n")
        _print_briefing(db, manuscript)
    else:
        session = ses.start_session(db, manuscript["id"])
        print(f"Session {session['id']} started for '{manuscript['name']}'.\n")
        backup_result = session.get("backup") or {}
        if not backup_result.get("ok", True):
            from . import ui
            print(ui.yellow(f"warning: knowledge-store backup failed "
                             f"({backup_result.get('error')}) — no fresh "
                             "backup was taken this session."))
        _print_briefing(db, manuscript)

    # Catch up on edits made while the shell was closed, then watch.
    print()
    _dispatch(base_argv, ["collect"])

    stop = threading.Event()
    if not args.no_watch:
        watcher = Watcher(Path(manuscript["path"]), debounce=args.debounce)

        def watch_loop():
            while not stop.wait(1.0):
                try:
                    fired = watcher.poll()
                except Exception as err:  # noqa: BLE001 — poll() must not kill the loop unnoticed
                    # A dead watcher thread leaves the "Watching…" banner
                    # lying — the author keeps writing believing revisions
                    # are being observed. Say so instead of vanishing.
                    with lock:
                        print(f"\nwatcher stopped: {err} — restart the shell "
                              f"to resume automatic collection.")
                        print(prompt, end="", flush=True)
                    return
                if fired:
                    _collect_via_watcher(base_argv, lock, prompt)

        threading.Thread(target=watch_loop, daemon=True).start()
        print(f"Watching {manuscript['path']} — revisions collect automatically "
              f"({args.debounce:.0f}s after edits settle).")
    print("Type commands without a prefix (e.g. intent declare \"...\", guide, "
          "review 1 accept). 'help' lists commands; 'exit' to leave.\n")

    try:
        while True:
            try:
                line = input(prompt)
            except EOFError:
                print()
                break
            line = line.strip()
            if not line:
                continue
            if line in ("exit", "quit"):
                break
            try:
                tokens = translate_line(shlex.split(line))
            except ValueError as err:
                print(f"error: {err}")
                continue
            if tokens[0] in ("help", "?"):
                print_shell_help(tokens[1] if len(tokens) > 1 else None)
                continue
            if tokens[0] in BLOCKED_IN_SHELL:
                print(f"error: '{tokens[0]}' cannot run inside the shell.")
                continue
            baseline = (readline_mod.get_current_history_length()
                        if readline_mod else 0)
            with lock:
                _dispatch(base_argv, tokens)
            _trim_nested_history(readline_mod, baseline)
    finally:
        stop.set()
        _save_history(readline_mod, args.workspace)

    still_active = ses.active_session(db, manuscript["id"])
    if still_active:
        print(f"Session {still_active['id']} is still active — it will resume "
              f"next time you open the shell ('session end' closes it).")


def run_watch(args, manuscript: dict) -> None:
    """Standalone watcher: auto-collect until Ctrl-C."""
    base_argv = ["--workspace", args.workspace, "--manuscript", manuscript["name"]]
    _dispatch(base_argv, ["collect"])  # catch up first
    watcher = Watcher(Path(manuscript["path"]), debounce=args.debounce)
    print(f"Watching {manuscript['path']} (Ctrl-C to stop)…")
    try:
        while True:
            time.sleep(args.interval)
            try:
                fired = watcher.poll()
            except Exception as err:  # noqa: BLE001 — poll() must not kill the loop unnoticed
                # A dead watcher here stops collection outright with no one
                # in the shell to notice the silence — say so and exit
                # instead of vanishing behind the "Watching…" banner.
                print(f"\nwatcher stopped: {err} — restart 'authorlm watch' "
                      f"to resume.")
                sys.exit(1)
            if fired:
                _dispatch(base_argv, ["collect", "--auto"])
    except KeyboardInterrupt:
        print("\nStopped watching.")
        sys.exit(0)
