"""The live TEST BENCH: one manuscript that exists to be experimented on.

Sponsor ruling (2026-08-31): "We need an automated test to make sure that
rewrite placeholder is written in obsidian. You could do this on a test
manuscript. […] You should probably have one test manuscript in the
database that you use exclusively for testing these kinds of things."

Some properties are only true of a LIVE workspace — the real database,
the real config, the real CLI, a real file on disk that a real Obsidian
vault would open. The hermetic suites cannot see them, and the author's
manuscripts (SMSTTD, on-becoming) must never be the thing a smoke check
truncates. So there is a third place: a permanent, disposable manuscript
called `testbench`, three tiny essays of fixed text, living in the
gitignored `manuscripts/` tree.

    python3 tools/testbench.py --setup              # idempotent; the only LLM spend
    python3 tools/testbench.py --check placeholder  # zero LLM calls, restores itself
    python3 tools/testbench.py --list-checks

`--setup` creates what is missing and says so; on a healthy bench it is a
no-op that says THAT. It is the one step that may call a model (three
essay summaries, on the configured cheap summarizer) and it prints the
cost line. Everything after it runs with the provider keys scrubbed out
of the environment.

THE SCRIPT IS THE SOURCE OF TRUTH FOR THE BENCH TEXT. `manuscripts/` is
gitignored, so the essays cannot be committed; the constants below are
what is committed, and `--setup` writes any file that is missing. A bench
file that exists but differs is reported, never overwritten — an
experiment in progress is not the script's to discard.

SAFETY RAIL, NON-NEGOTIABLE: every mutating step re-asserts that the
manuscript it is about to touch is named `testbench` and lives in a
directory named `testbench`. The name is a constant, not a flag. There is
no code path here that can reach another manuscript's row or another
manuscript's files.

Adding a check: write `check_<name>(workspace, manuscript_dir) -> int`
(returning the number of FAILED assertions), register it in CHECKS, and
it joins the `--check` flag surface. The standing rule is that live smoke
checks land here as named checks rather than as one-off scripts.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import paths  # noqa: E402
from authorlm import summaries as sums  # noqa: E402
from authorlm.api import MARKER, PLACEHOLDER  # noqa: E402
from authorlm.db import Database  # noqa: E402
from authorlm.llm import VENDOR_KEY_ENV  # noqa: E402

# ------------------------------------------------------------ constants

# Load-bearing. The rail this whole script leans on is that nothing here
# resolves a manuscript by argument, so make it impossible to configure.
BENCH = "testbench"
BENCH_STYLE = "Bench Plain"
BENCH_INTENT = "Test bench: exercise the write path against fixed text."

# The essay a check rewrites. Deliberately the MIDDLE one, so the
# before/after drafting context has a neighbour on each side and the
# summary-freshness gate is exercised rather than skirted.
TARGET = "02-ground.md"

TOC = """# Reading order for the test bench (see tools/testbench.py).

[[chapter]]
file = "01-figure.md"

[[chapter]]
file = "02-ground.md"

[[chapter]]
file = "03-frame.md"
"""

ESSAYS = {
    "01-figure.md": """# Figure

A figure is whatever stands forward. Nothing stands forward on its own;
it is stood forward, by an eye that has already decided where to look.

The decision is not usually noticed. That is what makes it a decision
rather than a report.
""",
    "02-ground.md": """# Ground

The ground is what the figure was taken out of. It has no shape of its
own, which is why it is so easy to mistake for nothing at all.

Every ground was once a figure, and will be again. The two are not two
kinds of thing; they are two offices, and the same thing holds them in
turn.

Say it plainly: there is no such thing as a background. There is only
what is not, at this moment, being attended to.
""",
    "03-frame.md": """# Frame

A frame does not add anything to what it surrounds. It subtracts
everything else, and the subtraction is felt as emphasis.

This is why a frame cannot be neutral. Choosing the edge is the whole
of the composition; the rest is arrangement.
""",
}

FILES = [*ESSAYS, "toc.toml"]

PROJECT = paths.project_dir()


# ------------------------------------------------------------ the rails

class BenchRefusal(RuntimeError):
    """The script declined to act. Never a bug — always a state the
    operator has to resolve by hand."""


def _guard_dir(manuscript_dir: Path) -> Path:
    manuscript_dir = Path(manuscript_dir).expanduser().resolve()
    if manuscript_dir.name != BENCH:
        raise BenchRefusal(
            f"refusing to operate on {manuscript_dir} — the bench directory "
            f"must be named '{BENCH}'. This script has no other mode.")
    return manuscript_dir


def _open(workspace: Path) -> Database | None:
    db_path = Path(workspace) / ".authorlm" / "authorlm.db"
    return Database(db_path) if db_path.exists() else None


def _bench_row(db: Database | None) -> dict | None:
    if db is None:
        return None
    row = db.one("SELECT * FROM manuscripts WHERE name = ?", (BENCH,))
    return dict(row) if row else None


def _guard(db: Database, manuscript_dir: Path) -> dict:
    """Re-assert, immediately before a mutating step, that the row about
    to be touched is the bench. Called every time rather than once at the
    top: a long-running setup can be racing another process, and the cost
    of one SELECT is nothing against the cost of being wrong."""
    row = _bench_row(db)
    if row is None:
        raise BenchRefusal(
            f"no manuscript named '{BENCH}' is registered — run --setup.")
    if row["name"] != BENCH:                          # belt and braces
        raise BenchRefusal(f"resolved '{row['name']}', not '{BENCH}'")
    on_disk = Path(row["path"]).resolve()
    if on_disk != manuscript_dir:
        raise BenchRefusal(
            f"'{BENCH}' is registered at {on_disk}, but this run points at "
            f"{manuscript_dir}. Refusing to guess which one you meant — "
            f"pass --manuscript-dir {on_disk}, or unregister the stale row "
            f"(authorlm -m {BENCH} unregister {BENCH}).")
    return row


# ------------------------------------------------------- driving the CLI

def cli(workspace: Path, *argv: str, scrub_keys: bool = False,
        allow_fail: bool = False) -> str:
    """One real `authorlm` invocation, in its own process.

    A subprocess and not an in-process `cli.main()` call for two reasons:
    it is the surface the author actually types, and it is the only way
    to hand a command an environment with no provider keys in it at all."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(PROJECT), env.get("PYTHONPATH", "")) if p)
    if scrub_keys:
        # Belt: the vendor variables themselves. Braces: `.env`, which
        # paths.load_env() would otherwise put them straight back from.
        for var in VENDOR_KEY_ENV.values():
            env.pop(var, None)
        env["AUTHORLM_ENV"] = "/nonexistent/authorlm-testbench/.env"
    argv = ("--workspace", str(workspace), *argv)
    proc = subprocess.run(
        [sys.executable, str(PROJECT / "main.py"), *argv],
        cwd=str(PROJECT), env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    if proc.returncode and not allow_fail:
        raise BenchRefusal(
            "authorlm " + " ".join(argv) + f" exited {proc.returncode}:\n{out}")
    return out


# --------------------------------------------------------------- --setup

def _summary_states(db: Database, manuscript: dict) -> dict[str, str]:
    return {r["file"]: r["state"] for r in sums.status(db, manuscript)}


def _needs_summaries(states: dict[str, str]) -> list[str]:
    """`upstream_stale` is deliberately NOT here. It is tolerated by the
    drafting gate (passes.summaries_ready), so treating it as work would
    make every --setup buy three summaries it does not need — and an
    idempotent step that spends money is not idempotent."""
    return sorted(f for f, s in states.items()
                  if s in ("missing", "stale", "deprecated"))


def setup(workspace: Path, manuscript_dir: Path) -> int:
    """Ensure the bench exists and is fit to check against. Idempotent.

    Returns the number of steps performed — 0 means "already healthy",
    which is the answer every run after the first should give."""
    manuscript_dir = _guard_dir(manuscript_dir)
    workspace = Path(workspace).expanduser().resolve()

    db = _open(workspace)
    row = _bench_row(db)
    missing_files = [f for f in FILES if not (manuscript_dir / f).exists()]
    drifted = [f for f in ESSAYS
               if (manuscript_dir / f).exists()
               and (manuscript_dir / f).read_text(encoding="utf-8") != ESSAYS[f]]

    todo: list[str] = []
    if missing_files:
        todo.append(f"write {len(missing_files)} bench file(s) into "
                    f"{manuscript_dir}: {', '.join(missing_files)}")
    if row is None:
        todo.append(f"register the manuscript '{BENCH}' at {manuscript_dir} "
                    f"(no concept extraction)")
    guides = attachments = states = None
    if row is not None:
        guides = {g["name"] for g in db.all(
            "SELECT name FROM style_guides WHERE manuscript_id = ?",
            (row["id"],))}
        attachments = {a["file"] for a in db.all(
            "SELECT file FROM style_attachments WHERE manuscript_id = ?",
            (row["id"],))}
        states = _summary_states(db, row)
    if not guides:
        todo.append(f"define the style guide '{BENCH_STYLE}' and one law")
    unattached = sorted(set(ESSAYS) - (attachments or set()))
    if unattached:
        todo.append(f"attach '{BENCH_STYLE}' to {', '.join(unattached)}")
    stale = _needs_summaries(states) if states else sorted(ESSAYS)
    if stale:
        todo.append(f"build summaries for {', '.join(stale)} "
                    f"(the ONLY step that calls a model)")

    if drifted:
        print(f"note: {', '.join(drifted)} differ(s) from the text in "
              f"tools/testbench.py. The script is the source of truth, but "
              f"an experiment in progress is not its to discard — delete the "
              f"file(s) and re-run --setup to reset them.")
    if not todo:
        print(f"'{BENCH}' is healthy — {len(ESSAYS)} essays at "
              f"{manuscript_dir}, style '{BENCH_STYLE}' attached, summaries "
              f"fresh. Nothing to do.")
        return 0

    print(f"--setup will, in {workspace}:")
    for item in todo:
        print(f"  • {item}")
    print()

    done = 0
    if missing_files:
        manuscript_dir.mkdir(parents=True, exist_ok=True)
        for name in missing_files:
            body = TOC if name == "toc.toml" else ESSAYS[name]
            (manuscript_dir / name).write_text(body, encoding="utf-8")
        print(f"wrote {', '.join(missing_files)}")
        done += 1

    if row is None:
        # --no-extract: the bench is a fixture for the WRITE path, and a
        # concept graph over it would only add proposals to triage and
        # LLM calls to a step that must stay free after the first run.
        print(cli(workspace, "init", "--name", BENCH,
                  "--path", str(manuscript_dir), "--no-extract").strip())
        db = _open(workspace)
        done += 1
    row = _guard(db, manuscript_dir)

    if not guides:
        print(cli(workspace, "-m", BENCH, "style", "guide", BENCH_STYLE).strip())
        _guard(db, manuscript_dir)
        print(cli(workspace, "-m", BENCH, "style", "add", "register",
                  "Plain declarative prose; no ornament.",
                  "--guide", BENCH_STYLE).strip())
        done += 1
    for name in unattached:
        _guard(db, manuscript_dir)
        print(cli(workspace, "-m", BENCH, "style", "attach",
                  name, BENCH_STYLE).strip())
        done += 1

    states = _summary_states(db, row)
    if _needs_summaries(states):
        _guard(db, manuscript_dir)
        # No --all: `summarize rebuild` reuses fresh units, so a partial
        # bench costs only the units that are actually missing.
        print(cli(workspace, "-m", BENCH, "summarize", "rebuild").strip())
        done += 1

    states = _summary_states(db, row)
    if _needs_summaries(states):
        raise BenchRefusal(
            "summaries are still not fresh after a rebuild: "
            + ", ".join(f"{f}={s}" for f, s in sorted(states.items()))
            + " — the bench is not fit to check against.")
    print(f"\n'{BENCH}' is ready. Next: "
          f"python3 tools/testbench.py --check placeholder")
    return done


# ------------------------------------------------- markdown visibility
#
# The property the Sponsor asked for is not "the marker is in the file".
# It is "a person who opens this file in Obsidian SEES it". Those differ
# by exactly the constructs below: a markdown reader renders none of an
# HTML comment, none of YAML front matter, and none of a fenced or
# indented code block as prose. So the test is a parse: strip what a
# reader would not show, and assert the marker survives — as an ordinary
# paragraph line, not a heading, list item, quote or raw HTML block.

FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\n.*?\n---[ \t]*(?:\n|\Z)", re.DOTALL)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
FENCE_RE = re.compile(r"\A(?P<fence>```+|~~~+)")
# A markdown line that is NOT plain paragraph prose. Up to three leading
# spaces still counts as the same block (four makes it a code block,
# handled separately).
BLOCK_RE = re.compile(r"\A {0,3}(#{1,6}\s|>|[-*+]\s|\d+[.)]\s|\||<)")


def front_matter(text: str) -> str:
    m = FRONT_MATTER_RE.match(text)
    return m.group(0) if m else ""


def html_comments(text: str) -> str:
    """Every region a markdown reader treats as an HTML comment —
    including an UNTERMINATED one, which swallows the rest of the file
    and is the sneakiest way to hide a marker in plain sight."""
    parts = HTML_COMMENT_RE.findall(text)
    rest = HTML_COMMENT_RE.sub("", text)
    if "<!--" in rest:
        parts.append(rest[rest.index("<!--"):])
    return "\n".join(parts)


def code_blocks(text: str) -> str:
    """Fenced (``` / ~~~) and indented (4-space / tab) code regions."""
    body = HTML_COMMENT_RE.sub("", FRONT_MATTER_RE.sub("", text))
    out: list[str] = []
    fence: str | None = None
    for line in body.splitlines():
        stripped = line.lstrip()
        if fence is None:
            m = FENCE_RE.match(stripped)
            if m:
                fence = m.group("fence")[:3]
                continue
            if line.startswith("    ") or line.startswith("\t"):
                out.append(line)
            continue
        if stripped.startswith(fence):
            fence = None
            continue
        out.append(line)
    return "\n".join(out)


def visible_body(text: str) -> str:
    """What a markdown reader renders as prose."""
    body = FRONT_MATTER_RE.sub("", text)
    body = HTML_COMMENT_RE.sub("", body)
    if "<!--" in body:
        body = body[:body.index("<!--")]
    out: list[str] = []
    fence: str | None = None
    for line in body.splitlines():
        stripped = line.lstrip()
        if fence is None:
            m = FENCE_RE.match(stripped)
            if m:
                fence = m.group("fence")[:3]
                continue
            if line.startswith("    ") or line.startswith("\t"):
                continue
            out.append(line)
            continue
        if stripped.startswith(fence):
            fence = None
    return "\n".join(out)


def paragraph_lines(text: str) -> list[str]:
    """Visible lines that render as ordinary paragraph prose."""
    return [ln for ln in visible_body(text).splitlines()
            if ln.strip() and not BLOCK_RE.match(ln)]


# --------------------------------------------------- --check placeholder

class _Report:
    """A check reports EVERY assertion, then fails. A check that stops at
    the first failure tells an operator one thing about a broken bench
    when it could have told them four."""

    def __init__(self) -> None:
        self.failed = 0

    def __call__(self, label: str, ok: bool, context: str = "") -> None:
        if ok:
            print(f"  ok: {label}")
            return
        self.failed += 1
        print(f"  FAIL: {label}")
        if context:
            for line in str(context).splitlines():
                print(f"        {line}")


def _active_intent(db: Database, manuscript: dict) -> dict | None:
    row = db.one(
        "SELECT * FROM declared_intents WHERE manuscript_id = ? "
        "AND status = 'active' ORDER BY created_at DESC LIMIT 1",
        (manuscript["id"],))
    return dict(row) if row else None


def check_placeholder(workspace: Path, manuscript_dir: Path, *,
                      _corrupt=None) -> int:
    """the mid-rewrite placeholder lands on disk as VISIBLE markdown.

    `write start` must leave a marker the author can SEE in Obsidian, and
    `write abandon` must put the essay back byte for byte — with no model
    call anywhere in between.

    `_corrupt` is a test seam and nothing else: a callable applied to the
    on-disk text right after `write start`, used by the hermetic suite to
    prove this check DISCRIMINATES (a marker hidden inside an HTML
    comment must make it fail). Production has no caller for it."""
    manuscript_dir = _guard_dir(manuscript_dir)
    workspace = Path(workspace).expanduser().resolve()
    db = _open(workspace)
    if db is None:
        raise BenchRefusal(f"no workspace database under {workspace} — "
                           f"run --setup first.")
    manuscript = _guard(db, manuscript_dir)

    target = manuscript_dir / TARGET
    if not target.exists():
        raise BenchRefusal(f"{target} is missing — run --setup.")

    active = db.one(
        "SELECT * FROM writeups WHERE manuscript_id = ? AND file = ? "
        "AND status = 'active'", (manuscript["id"], TARGET))
    if active:
        raise BenchRefusal(
            f"writeup [{active['id'][:11]}] is already active on {TARGET} "
            f"(intent {active['intent_id'][:11]}). This check would fight it "
            f"for the file. Settle it first — 'authorlm -m {BENCH} write "
            f"status' to see it, then 'authorlm -m {BENCH} write abandon "
            f"--writeup {TARGET}' if it is a leftover.")

    intent = _active_intent(db, manuscript)
    if intent is None:
        _guard(db, manuscript_dir)
        cli(workspace, "-m", BENCH, "intent", "declare", BENCH_INTENT,
            scrub_keys=True)
        intent = _active_intent(db, manuscript)
        if intent is None:
            raise BenchRefusal("could not declare a bench intent")

    original = target.read_bytes()
    report = _Report()
    started = False
    try:
        _guard(db, manuscript_dir)
        start_out = cli(workspace, "-m", BENCH, "write", "start", TARGET,
                        "--intent", intent["id"][:11], scrub_keys=True)
        started = True
        if _corrupt is not None:
            target.write_text(_corrupt(target.read_text(encoding="utf-8")),
                              encoding="utf-8")

        disk = target.read_text(encoding="utf-8")
        report("write start reported the pin and the truncation",
               "Pinned v" in start_out and "truncated" in start_out, start_out)
        report("the on-disk file holds EXACTLY the placeholder, byte for "
               "byte — nothing else",
               disk == PLACEHOLDER, repr(disk))
        report("the marker is NOT inside an HTML comment (the one place a "
               "reader would never show it)",
               MARKER not in html_comments(disk), html_comments(disk))
        report("the marker is NOT inside YAML front matter",
               MARKER not in front_matter(disk), front_matter(disk))
        report("the marker is NOT inside a code fence or an indented code "
               "block",
               MARKER not in code_blocks(disk), code_blocks(disk))
        report("the marker survives the strip and renders as a plain "
               "paragraph — this is what makes it visible in Obsidian, or "
               "in any markdown reader",
               any(MARKER in ln for ln in paragraph_lines(disk)),
               visible_body(disk))
        report("the placeholder explains itself in visible prose too, not "
               "just in the marker line",
               "write abandon" in visible_body(disk), visible_body(disk))
    finally:
        if started:
            _guard(db, manuscript_dir)
            abandon_out = cli(workspace, "-m", BENCH, "write", "abandon",
                              "--writeup", TARGET, scrub_keys=True,
                              allow_fail=True)
            print(abandon_out.strip())
            report("write abandon restored the essay byte for byte",
                   target.read_bytes() == original,
                   f"{len(original)} bytes before, "
                   f"{len(target.read_bytes())} after")
            report("no live model call anywhere in the check — it passes "
                   "with the provider keys scrubbed out of the environment",
                   "live call" not in (start_out + abandon_out),
                   start_out + abandon_out)
    return report.failed


CHECKS = {"placeholder": check_placeholder}


# ------------------------------------------------------------------ main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="testbench",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="checks: " + ", ".join(sorted(CHECKS)))
    parser.add_argument(
        "-w", "--workspace", default=str(Path.home()),
        help="the directory holding .authorlm/ (default: your home "
             "directory, i.e. the live ~/.authorlm)")
    parser.add_argument(
        "--manuscript-dir", default=str(PROJECT / "manuscripts" / BENCH),
        help=f"where the bench essays live (default: the gitignored "
             f"manuscripts/{BENCH}/ beside the code). Its basename must be "
             f"'{BENCH}'; the hermetic suite points this at a temp tree.")
    parser.add_argument("--setup", action="store_true",
                        help="create anything the bench is missing "
                             "(idempotent; the only step that spends LLM)")
    parser.add_argument("--check", nargs="?", const="placeholder",
                        choices=sorted(CHECKS), metavar="NAME",
                        help="run a named live check (default: placeholder)")
    parser.add_argument("--list-checks", action="store_true",
                        dest="list_checks", help="print the check names")
    args = parser.parse_args(argv)

    if args.list_checks:
        for name, fn in sorted(CHECKS.items()):
            summary = (fn.__doc__ or "").strip().splitlines()[0]
            print(f"  {name:<14} {summary}")
        return 0
    if not args.setup and not args.check:
        parser.error("nothing to do — pass --setup, --check <name>, or both")

    try:
        if args.setup:
            setup(args.workspace, args.manuscript_dir)
        if args.check:
            print(f"check: {args.check}")
            failed = CHECKS[args.check](Path(args.workspace),
                                        Path(args.manuscript_dir))
            if failed:
                print(f"\n{failed} assertion(s) FAILED.")
                return 1
            print("\nall assertions passed.")
    except BenchRefusal as err:
        print(f"testbench: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
