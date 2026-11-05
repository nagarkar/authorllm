"""The git adapter: the only code in AuthorLM that runs git.

One `Repository` is one private git directory holding a manuscript's
version history. It has no remote and is never pushed. It knows commits,
refs and file bytes; it knows nothing of manuscripts, versions or the
database (`history.py` maps those onto it).

Commits are built from bytes handed in, never from the files on disk:
git's `fast-import` writes them and `cat-file --batch` reads them, so
nothing here goes through an index or a work tree, and none of git's
conversions (line endings, name normalization, ignore rules, filters)
touches the bytes or the names.

The repository is bare. The book folder is its work tree only when a
person names it on the command line, to look by hand:
`git --git-dir <repository> --work-tree <book folder> status`. It is
deliberately not stored in the repository's config: git changes into a
stored work tree before every command, so a book folder on an unmounted
drive would make every read of history fail. Nothing is ever written
inside the book folder.

Git runs with the user's and the system's git config switched off, so a
setting on this machine (signing, hooks, line endings) cannot change
what is stored.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

AUTHOR = "AuthorLM <authorlm@localhost>"
# Ignore rules for a `git status` run by hand: everything is left out
# except the names the caller says a version holds, and never a folder
# or file whose name starts with `.`, or a folder whose name starts with
# `_` (revisions.iter_manuscript_paths is the authority; these only keep
# a by-hand status readable).
EXCLUDE_HEAD = "# Written by AuthorLM. Mirrors what collect observes."
EXCLUDE_TAIL = (".*", "_*/")
# git's own rule of thumb for "enough loose objects to pack": more than
# 27 in one of the 256 object folders (gc.auto 6700 / 256).
LOOSE_SAMPLE_FOLDER = "17"
LOOSE_SAMPLE_LIMIT = 27


class GitError(RuntimeError):
    """A git command failed, or returned something this adapter cannot read."""


class GitUnavailable(GitError):
    """git is not installed on this machine."""


@dataclass(frozen=True, slots=True)
class Commit:
    """One commit to write. `changed` and `removed` are relative to
    `parent`; with no parent the commit starts from an empty tree."""
    ref: str
    parent: str | None
    message: str
    when: int                               # seconds since 1970, UTC
    changed: dict[str, bytes] = field(default_factory=dict)
    removed: tuple[str, ...] = ()


def blob_id(data: bytes) -> str:
    """The id git gives a file with these bytes."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


# Where macOS keeps the developer tools `/usr/bin/git` hands over to: the
# folder `xcode-select` chose, else the Command Line Tools.
MAC_LAUNCHER = "/usr/bin/git"
MAC_DEVELOPER_FOLDERS = ("/var/db/xcode_select_link",
                         "/Library/Developer/CommandLineTools")


@lru_cache(maxsize=1)
def git_program() -> str | None:
    """The git to run, or None when there is none.

    On macOS `/usr/bin/git` is a launcher that looks the real git up on
    every start (14 ms a start against 5 ms, measured 2026-11-04), and a
    collect starts git four times. So the real one is found once here,
    the way the launcher finds it."""
    found = shutil.which("git")
    if found == MAC_LAUNCHER and sys.platform == "darwin":
        for folder in MAC_DEVELOPER_FOLDERS:
            real = Path(os.path.realpath(folder)) / "usr" / "bin" / "git"
            if os.access(real, os.X_OK):
                return str(real)
    return found


def _environment() -> dict[str, str]:
    env = {name: value for name, value in os.environ.items()
           if not name.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    return env


def _quoted(path: str) -> bytes:
    """`path` as fast-import's quoted form."""
    out = bytearray(b'"')
    for byte in path.encode("utf-8", "surrogatepass"):
        if byte in (0x22, 0x5C):
            out += b"\\" + bytes([byte])
        elif byte < 0x20 or byte == 0x7F:
            out += b"\\%03o" % byte
        else:
            out.append(byte)
    return bytes(out + b'"')


def _data(payload: bytes) -> bytes:
    return b"data %d\n" % len(payload) + payload + b"\n"


def _commit_stream(commit: Commit, mark: int, parent: bytes | None) -> bytes:
    stamp = b"%s %d +0000\n" % (AUTHOR.encode(), commit.when)
    parts = [b"commit %s\n" % commit.ref.encode(), b"mark :%d\n" % mark,
             b"author " + stamp, b"committer " + stamp,
             _data(commit.message.encode("utf-8", "surrogatepass"))]
    if parent:
        parts.append(b"from " + parent + b"\n")
    for path in commit.removed:
        parts.append(b"D " + _quoted(path) + b"\n")
    for path, payload in commit.changed.items():
        parts.append(b"M 100644 inline " + _quoted(path) + b"\n")
        parts.append(_data(payload))
    parts.append(b"\n")
    return b"".join(parts)


class Reader:
    """One `git cat-file --batch` process, kept open for many reads.
    Trees are remembered by id, so reading version after version costs
    only what changed between them."""

    def __init__(self, process: subprocess.Popen):
        self._process = process
        self._trees: dict[str, dict[str, str]] = {}

    def _object(self, name: str) -> tuple[str, str, bytes] | None:
        """`(id, type, bytes)` of the object `name`, or None if there is
        no such object."""
        stdin, stdout = self._process.stdin, self._process.stdout
        try:
            stdin.write(name.encode("utf-8", "surrogatepass") + b"\n")
            stdin.flush()
            header = stdout.readline().split()
            if len(header) == 2 and header[1] == b"missing":
                return None
            if len(header) != 3:
                raise GitError(
                    f"unreadable git cat-file answer for {name}: {header}")
            size = int(header[2])
            body = stdout.read(size + 1)[:size]
        except OSError as err:
            raise GitError(f"git cat-file stopped: {err}") from err
        if len(body) != size:
            raise GitError(f"git cat-file answer for {name} was cut short")
        return header[0].decode(), header[1].decode(), body

    def _commit(self, rev: str) -> tuple[str, str] | None:
        """`(commit id, tree id)` for `rev`, or None when it names no commit."""
        found = self._object(f"{rev}^{{commit}}")
        if found is None:
            return None
        first = found[2].split(b"\n", 1)[0].split()
        if len(first) != 2 or first[0] != b"tree":
            raise GitError(f"unreadable commit {rev}")
        return found[0], first[1].decode()

    def commit(self, rev: str) -> str | None:
        """The commit id `rev` names, or None when there is no such commit."""
        found = self._commit(rev)
        return found[0] if found else None

    def _tree(self, tree_id: str) -> dict[str, str]:
        """`relative path -> blob id` for every file under a tree."""
        known = self._trees.get(tree_id)
        if known is not None:
            return known
        found = self._object(tree_id)
        if found is None or found[1] != "tree":
            raise GitError(f"the history is missing tree {tree_id}")
        body, files, at = found[2], {}, 0
        while at < len(body):
            space = body.index(b" ", at)
            nul = body.index(b"\0", space)
            mode = body[at:space]
            name = body[space + 1:nul].decode("utf-8", "surrogatepass")
            entry = body[nul + 1:nul + 21].hex()
            at = nul + 21
            if mode == b"40000":
                for below, blob in self._tree(entry).items():
                    files[f"{name}/{below}"] = blob
            else:
                files[name] = entry
        self._trees[tree_id] = files
        return files

    def blob_ids(self, rev: str) -> dict[str, str]:
        """`relative path -> blob id` for every file in commit `rev`."""
        found = self._commit(rev)
        if found is None:
            raise GitError(f"no commit {rev} in the history")
        return self._tree(found[1])

    def blob(self, blob: str) -> bytes:
        found = self._object(blob)
        if found is None or found[1] != "blob":
            raise GitError(f"the history is missing file contents {blob}")
        return found[2]

    def files(self, rev: str) -> dict[str, bytes]:
        """Every file in commit `rev`, by relative path."""
        return {path: self.blob(blob)
                for path, blob in self.blob_ids(rev).items()}

    def file(self, rev: str, path: str) -> bytes | None:
        """One file in commit `rev`; None when the commit has no such file."""
        blob = self.blob_ids(rev).get(path)
        return None if blob is None else self.blob(blob)

    def changes(self, older: str | None,
                newer: str) -> dict[str, tuple[bytes | None, bytes | None]]:
        """`path -> (old bytes, new bytes)` for the files that differ
        between two commits, in path order; None on a side means the file
        is not in that commit. `older=None` compares against no files."""
        old = self.blob_ids(older) if older else {}
        new = self.blob_ids(newer)
        return {
            path: (self.blob(old[path]) if path in old else None,
                   self.blob(new[path]) if path in new else None)
            for path in sorted(set(old) | set(new))
            if old.get(path) != new.get(path)
        }


class Repository:
    def __init__(self, git_dir: Path, run=subprocess.run,
                 popen=subprocess.Popen):
        self.git_dir = Path(git_dir)
        self._run = run
        self._popen = popen

    def _command(self, *args: str) -> list[str]:
        program = git_program()
        if program is None:
            raise GitUnavailable(
                "git is not installed. Install it (xcode-select --install), "
                "or set [history] backend = \"table\" in config.toml")
        # Names are stored as given: git on macOS would otherwise rewrite
        # decomposed accents in names it is passed.
        return [program, f"--git-dir={self.git_dir}",
                "-c", "core.precomposeunicode=false", *args]

    def _git(self, *args: str, stdin: bytes | None = None) -> bytes:
        done = self._run(self._command(*args), input=stdin,
                         capture_output=True, env=_environment())
        if done.returncode != 0:
            raise GitError(
                f"git {args[0]} failed in {self.git_dir}: "
                f"{done.stderr.decode('utf-8', 'replace').strip()}")
        return done.stdout

    def exists(self) -> bool:
        return (self.git_dir / "HEAD").is_file()

    def create(self, observed: Iterable[str] = ()) -> None:
        """Make the empty repository. Nothing is written outside it.
        `observed` are the name patterns a version holds (`*.md`, …); a
        by-hand `git status` ignores everything else."""
        self.git_dir.parent.mkdir(parents=True, exist_ok=True)
        self._git("init", "--quiet", "--bare", "--initial-branch=main",
                  "--template=", str(self.git_dir))
        rules = [EXCLUDE_HEAD, "*", "!*/", *(f"!{name}" for name in observed),
                 *EXCLUDE_TAIL, ""]
        (self.git_dir / "info").mkdir(exist_ok=True)
        (self.git_dir / "info" / "exclude").write_text(
            "\n".join(rules), encoding="utf-8")

    def follow(self, branch: str, rev: str) -> None:
        """Move `branch` and the index to commit `rev`, so a by-hand
        `git log`, `git status` or `git diff` (with `--work-tree`) starts
        from it. Touches no file in the book folder."""
        self._git("update-ref", branch, rev)
        self._git("read-tree", branch)

    @contextmanager
    def reader(self) -> Iterator[Reader]:
        process = self._popen(
            self._command("cat-file", "--batch"), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=_environment())
        try:
            yield Reader(process)
        finally:
            process.stdin.close()
            process.stdout.close()
            process.wait()

    def write(self, commits: Iterable[Commit]) -> int:
        """Write `commits` in order, each under its own ref. A commit
        whose `parent` is the ref of an earlier commit in the same call
        builds on that one. Returns how many were written."""
        process = self._popen(
            self._command("fast-import", "--quiet", "--date-format=raw",
                          "--done"),
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, env=_environment())
        marks: dict[str, int] = {}
        try:
            for commit in commits:
                mark = len(marks) + 1
                if commit.parent in marks:
                    parent = b":%d" % marks[commit.parent]
                else:
                    parent = commit.parent.encode() if commit.parent else None
                process.stdin.write(_commit_stream(commit, mark, parent))
                marks[commit.ref] = mark
            process.stdin.write(b"done\n")
            process.stdin.close()
        except BrokenPipeError:
            pass
        errors = process.stderr.read().decode("utf-8", "replace").strip()
        process.stderr.close()
        if process.wait() != 0:
            raise GitError(f"git fast-import failed in {self.git_dir}: {errors}")
        return len(marks)

    def refs(self, prefix: str) -> dict[str, str]:
        """`ref name -> commit id` for every ref under `prefix`."""
        out = self._git("for-each-ref", "--format=%(refname) %(objectname)",
                        prefix)
        return dict(line.split(" ", 1) for line in out.decode().splitlines())

    def delete_ref(self, ref: str) -> None:
        self._git("update-ref", "-d", ref)

    def pack(self, *, thorough: bool = False) -> None:
        """Pack loose objects and refs into git's compact form.
        `thorough` also recomputes how already-packed files are stored
        against each other: `fast-import` packs a long history loosely
        (the main book's 792 versions: 7.0 MB as written, 1.5 MB after)."""
        self._git("gc", "--quiet", *(["--aggressive"] if thorough else []))

    def pack_if_loose(self) -> bool:
        """Pack once enough loose objects have gathered; True if it did."""
        sample = self.git_dir / "objects" / LOOSE_SAMPLE_FOLDER
        try:
            loose = sum(1 for _ in sample.iterdir())
        except FileNotFoundError:
            return False
        if loose <= LOOSE_SAMPLE_LIMIT:
            return False
        self.pack()
        return True

    def size(self) -> int:
        """Bytes the repository takes on disk."""
        return sum(path.stat().st_size
                   for path in self.git_dir.rglob("*") if path.is_file())
