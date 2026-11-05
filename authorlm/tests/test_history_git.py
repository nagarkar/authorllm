"""The private git history: the adapter, the git store, replay and check.

`test_version_history.py` runs the whole history interface on both
backends. This file holds what is true of git alone: where the
repository lives and what it never touches, what a commit is built from,
what a crash between the commit and the row leaves, two records at once,
a workspace and a config that disagree, and `history replay` / `history
check` end to end.

Standalone, hermetic (needs git):  python3 tests/test_history_git.py
"""

from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"

import contextlib  # noqa: E402
import io  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import tomllib  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import api, history, history_replay  # noqa: E402
from authorlm import revisions as rev  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.db import Database  # noqa: E402
from authorlm.history_git import (Commit, GitError, Repository,  # noqa: E402
                                  blob_id)

PASSED = 0
MID = "ms-one"

# Names and bytes a careless git road would change: a decomposed accent
# (git on macOS rewrites those in names it is passed), a quote, a
# backslash, a tab, Windows line endings, no final newline.
ODD = {
    "part one/chapter 1.md": "# One\n\nFirst.\n",
    "café.md": "composed\n",
    "café.md": "decomposed\n",
    'say "hello".md': "quote\n",
    "back\\slash.md": "backslash\n",
    "tab\there.md": "tab\n",
    "crlf.md": "one\r\ntwo\r\n",
    "no-newline.md": "ends without one",
    "empty.md": "",
    "a/b/c/deep.md": "deep\n",
}


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASSED
    if not ok:
        raise AssertionError(f"FAILED: {label}\n{detail}")
    PASSED += 1
    print(f"  ok: {label}")


def raises(kind: type[Exception], call) -> str | None:
    try:
        call()
    except kind as err:
        return str(err)
    return None


def use_backend(root: Path, backend: str) -> None:
    config = root / f"{backend}.toml"
    config.write_text(f'[history]\nbackend = "{backend}"\n', encoding="utf-8")
    os.environ["AUTHORLM_CONFIG"] = str(config)


def workspace(root: Path, name: str) -> Database:
    folder = root / name / ".authorlm"
    folder.mkdir(parents=True)
    return Database(folder / "authorlm.db")


def tree(folder: Path) -> list[str]:
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))


def git(repository: Path, *args: str) -> str:
    return subprocess.run(["git", f"--git-dir={repository}", *args],
                          capture_output=True, text=True, check=True).stdout


def test_adapter(root: Path) -> None:
    calls: list[tuple[list[str], dict]] = []

    def run(command, **kwargs):
        calls.append((command, kwargs["env"]))
        return subprocess.run(command, **kwargs)

    def popen(command, **kwargs):
        calls.append((command, kwargs["env"]))
        return subprocess.Popen(command, **kwargs)

    repository = Repository(root / "adapter" / "one.git", run=run, popen=popen)
    check("a repository that was never made does not exist",
          not repository.exists())
    repository.create(["*.md"])
    data = {name: text.encode() for name, text in ODD.items()}
    written = repository.write([
        Commit("refs/versions/v1", None, "first", 1_700_000_000, data),
        Commit("refs/versions/v2", "refs/versions/v1", "second",
               1_700_000_060, {"empty.md": b"filled\n", "new.md": b"n\n"},
               ("tab\there.md",)),
    ])
    repository.follow("refs/heads/main", "refs/versions/v2")
    check("write reports how many commits it wrote", written == 2)
    later = {**data, "empty.md": b"filled\n", "new.md": b"n\n"}
    del later["tab\there.md"]
    with repository.reader() as reader:
        check("every name and every byte comes back as written",
              reader.files("refs/versions/v1") == data)
        check("a commit builds on its parent: changed, added and removed",
              reader.files("refs/versions/v2") == later)
        check("blob_id is the id git gives the same bytes",
              reader.blob_ids("refs/versions/v1")
              == {name: blob_id(payload) for name, payload in data.items()})
        check("one file reads alone; an absent file is None",
              reader.file("refs/versions/v1", "café.md") == b"decomposed\n"
              and reader.file("refs/versions/v1", "absent.md") is None)
        check("changes gives both sides of each differing file, in path order",
              reader.changes("refs/versions/v1", "refs/versions/v2")
              == {"empty.md": (b"", b"filled\n"), "new.md": (None, b"n\n"),
                  "tab\there.md": (b"tab\n", None)})
        check("a ref that names no commit is None, and reading it stops",
              reader.commit("refs/versions/nope") is None
              and raises(GitError,
                         lambda: reader.files("refs/versions/nope")) is not None)
    refs = repository.refs("refs/versions/")
    check("refs lists each version ref with its commit",
          sorted(refs) == ["refs/versions/v1", "refs/versions/v2"]
          and git(repository.git_dir, "rev-parse", "main").strip()
          == refs["refs/versions/v2"])
    check("a commit carries the time it was given",
          git(repository.git_dir, "log", "-1", "--format=%at",
              "refs/versions/v1").strip() == "1700000000")
    repository.pack()
    with repository.reader() as reader:
        check("packing loses nothing",
              reader.files("refs/versions/v2") == later)
    check("every git command names the repository and never a work tree, "
          "and runs without the machine's git config",
          all(Path(command[0]).name == "git"
              and command[1] == f"--git-dir={repository.git_dir}"
              and not any(arg.startswith("--work-tree") for arg in command)
              and env["GIT_CONFIG_GLOBAL"] == os.devnull
              and env["GIT_CONFIG_NOSYSTEM"] == "1"
              and "GIT_WORK_TREE" not in env for command, env in calls),
          calls[:3])
    check("the repository is bare, with no remote and no stored work tree",
          git(repository.git_dir, "remote") == ""
          and "worktree" not in (repository.git_dir / "config").read_text()
          and git(repository.git_dir, "rev-parse",
                  "--is-bare-repository").strip() == "true")


def test_store(root: Path) -> None:
    use_backend(root, "git")
    db = workspace(root, "store")
    book = root / "store" / "book"
    (book / "_audio").mkdir(parents=True)
    (book / "a.md").write_text(
        "# A\n\nText.\n\n[Illustration: a door]\n![](_illustrations/door.png)\n",
        encoding="utf-8")
    (book / "notes.pdf").write_text("not observed\n", encoding="utf-8")
    manuscript = api.register_manuscript(db, "store", str(book))
    before = tree(book)
    v1 = rev.collect_revision(db, manuscript, None, source="test")
    repository = history.history_root(db) / f"{manuscript['id']}.git"
    check("the repository is in the workspace, beside the database",
          repository == Path(db.path).resolve().parent / "history"
          / f"{manuscript['id']}.git" and (repository / "HEAD").is_file())
    check("nothing is written inside the book folder", tree(book) == before)
    check("the commit holds what collect observes, not the bytes on disk: "
          "the embed line is left out, the unobserved file is not there",
          rev.files_at(db, v1.id) == {"a.md": "# A\n\nText.\n\n[Illustration: a door]\n"}
          and rev.files_at(db, v1.id) == rev.read_manuscript_files(book))
    check("the row holds no text",
          db.one("SELECT files FROM manuscript_versions WHERE id = ?",
                 (v1.id,))["files"] == history.MOVED)
    check("the version id names its commit",
          history.version_ref(v1.id) == f"refs/versions/{v1.id}"
          and v1.id in git(repository, "log", "-1", "--format=%B",
                           history.version_ref(v1.id)))
    check("the commit is dated when the version was recorded",
          int(git(repository, "log", "-1", "--format=%at",
                  history.version_ref(v1.id))) == history.commit_time(v1.created_at))
    (book / "a.md").write_text("# A\n\nText, edited.\n", encoding="utf-8")
    v2 = rev.collect_revision(db, manuscript, None, source="test")
    check("the next version is a commit on top of the one before",
          git(repository, "rev-list", "main").split()
          == [git(repository, "rev-parse", history.version_ref(v.id)).strip()
              for v in (v2, v1)])
    status = subprocess.run(
        ["git", f"--git-dir={repository}", f"--work-tree={book}", "status",
         "--short"], capture_output=True, text=True, check=True).stdout
    check("by hand, with the book folder as work tree, git status is clean "
          "after a collect and ignores what collect ignores",
          status == "", status)
    check("still nothing inside the book folder", tree(book) == before)

    # A crash between the commit and the row.
    real_insert = db.insert

    def failing_insert(table, row):
        if table == "manuscript_versions":
            raise RuntimeError("crash before the row")
        return real_insert(table, row)

    (book / "a.md").write_text("# A\n\nLost in a crash.\n", encoding="utf-8")
    db.insert = failing_insert
    crashed = raises(RuntimeError,
                     lambda: rev.collect_revision(db, manuscript, None))
    db.insert = real_insert
    check("a record that dies after the commit leaves no version",
          crashed == "crash before the row"
          and rev.latest_version(db, manuscript["id"]) == v2
          and rev.count_versions(db, manuscript["id"]) == 2)
    v3 = rev.collect_revision(db, manuscript, None, source="test")
    check("the next record takes the number the crashed one would have had, "
          "and builds on the last version that exists",
          v3.version_no == 3
          and git(repository, "rev-parse", f"{history.version_ref(v3.id)}^").strip()
          == git(repository, "rev-parse", history.version_ref(v2.id)).strip()
          and rev.files_at(db, v3.id) == {"a.md": "# A\n\nLost in a crash.\n"})
    report = history_replay.check(db, manuscript)
    check("check reads the three versions back and names the commit the "
          "crash left", report["identical"] and report["versions"] == 3
          and len(report["unnamed"]) == 1, report)


def test_two_at_once(root: Path) -> None:
    use_backend(root, "git")
    workspace(root, "race").conn.close()
    path = root / "race" / ".authorlm" / "authorlm.db"
    results: list[object] = []

    def record(files: dict[str, str]) -> None:
        results.append(rev.record_version(Database(path), MID, files, None))

    distinct = [threading.Thread(target=record, args=({"a.md": f"{n}\n"},))
                for n in range(6)]
    for thread in distinct:
        thread.start()
    for thread in distinct:
        thread.join()
    db = Database(path)
    versions = rev.list_versions(db, MID)
    check("six records at once become six versions, numbered 1 to 6",
          [v.version_no for v in versions] == [1, 2, 3, 4, 5, 6]
          and all(isinstance(r, history.Version) for r in results), results)
    check("each has its own text, and each builds on the one before",
          sorted(rev.file_at(db, v.id, "a.md") for v in versions)
          == [f"{n}\n" for n in range(6)]
          and history_replay.check(db, {"id": MID, "name": "race"})["identical"])
    results.clear()
    same = [threading.Thread(target=record, args=({"a.md": "same\n"},))
            for _ in range(4)]
    for thread in same:
        thread.start()
    for thread in same:
        thread.join()
    check("four records of the same files at once become one version",
          sum(1 for r in results if r is not None) == 1
          and rev.count_versions(db, MID) == 7, results)


def test_mismatch(root: Path) -> None:
    use_backend(root, "table")
    db = workspace(root, "mismatch")
    v1 = rev.record_version(db, MID, {"a.md": "one\n"}, None)
    use_backend(root, "git")
    message = raises(history.HistoryMismatch,
                     lambda: rev.file_at(db, v1.id, "a.md"))
    check("the git backend stops on a version only the table holds, and "
          "names the fix", message is not None
          and message.endswith("Run: authorlm history replay"), message)
    check("and records nothing on top of it",
          raises(history.HistoryMismatch, lambda: rev.record_version(
              db, MID, {"a.md": "two\n"}, None)) is not None
          and rev.count_versions(db, MID) == 1)
    history_replay.replay(db, {"id": MID, "name": "mismatch"})
    v2 = rev.record_version(db, MID, {"a.md": "two\n"}, None)
    check("after a replay the git backend reads it and records on top",
          rev.file_at(db, v1.id, "a.md") == "one\n" and v2.version_no == 2)
    use_backend(root, "table")
    message = raises(history.HistoryMismatch, lambda: rev.files_at(db, v2.id))
    check("the table backend stops on a version only git holds, and names "
          "the setting", message is not None
          and '[history] backend = "git"' in message, message)
    check("one file of it, and a record on top of it, stop the same way",
          raises(history.HistoryMismatch,
                 lambda: rev.file_at(db, v2.id, "a.md")) is not None
          and raises(history.HistoryMismatch, lambda: rev.record_version(
              db, MID, {"a.md": "three\n"}, None)) is not None
          and rev.count_versions(db, MID) == 2)
    bad = root / "bad.toml"
    bad.write_text('[history]\nbackend = "gti"\n', encoding="utf-8")
    os.environ["AUTHORLM_CONFIG"] = str(bad)
    message = raises(ValueError, history.backend)
    check("a backend that is neither is an error naming the file and the "
          "choices", message is not None and str(bad) in message
          and "table, git" in message, message)


def test_replay(root: Path) -> None:
    use_backend(root, "table")
    db = workspace(root, "replay")
    manuscript = {"id": MID, "name": "replay"}
    texts = [
        ODD,
        {**ODD, "empty.md": "filled\n", "emoji 😀.md": "an emoji 😀\n"},
        {name: text for name, text in ODD.items() if not name.startswith("a/")},
        {},
        {"back.md": "after an empty version\n"},
    ]
    versions = [rev.record_version(db, MID, files, None, source=f"s{n}")
                for n, files in enumerate(texts)]
    rows_before = [tuple(r) for r in db.all(
        "SELECT * FROM manuscript_versions ORDER BY version_no")]
    root_before = tree(Path(db.path).parent)

    plan = history_replay.replay(db, manuscript, dry_run=True)
    check("a dry run says what it would copy and writes nothing",
          (plan["to_replay"], plan["already_in_history"], plan["replayed"])
          == (5, 0, 0) and tree(Path(db.path).parent) == root_before, plan)
    missing = history_replay.check(db, manuscript)
    check("before a replay, check says every version is missing",
          not missing["identical"] and missing["missing"] == [1, 2, 3, 4, 5])

    report = history_replay.replay(db, manuscript)
    check("replay copies every version", report["replayed"] == 5, report)
    checked = history_replay.check(db, manuscript)
    check("every file of every version reads back byte-identical",
          checked["identical"] and checked["versions"] == 5
          and checked["files_compared"] == sum(len(t) for t in texts)
          and not checked["unnamed"], checked)
    check("the table is exactly as it was",
          [tuple(r) for r in db.all(
              "SELECT * FROM manuscript_versions ORDER BY version_no")]
          == rows_before)
    repository = Path(report["repository"])
    check("each commit is dated when its version was recorded, oldest first",
          [int(t) for t in git(repository, "log", "--reverse", "--format=%at",
                               "main").split()]
          == [history.commit_time(v.created_at) for v in versions])
    lines = Path(report["map"]).read_text(encoding="utf-8").splitlines()
    check("the map names the commit of each version",
          lines[0] == "version_no\tversion_id\tcommit\trecorded_at"
          and [line.split("\t")[:3] for line in lines[1:]]
          == [[str(v.version_no), v.id,
               git(repository, "rev-parse", history.version_ref(v.id)).strip()]
              for v in versions], lines)

    again = history_replay.replay(db, manuscript)
    check("a second replay copies nothing",
          (again["replayed"], again["already_in_history"]) == (0, 5), again)
    v6 = rev.record_version(db, MID, {"back.md": "recorded to the table\n"}, None)
    more = history_replay.replay(db, manuscript)
    check("after another table version, replay copies only that one, on top",
          (more["replayed"], more["already_in_history"]) == (1, 5)
          and git(repository, "rev-parse", "main").strip()
          == git(repository, "rev-parse", history.version_ref(v6.id)).strip()
          and history_replay.check(db, manuscript)["identical"], more)

    table_text = {v.id: rev.files_at(db, v.id) for v in [*versions, v6]}
    use_backend(root, "git")
    check("switched to git, every version reads what the table read",
          {v.id: rev.files_at(db, v.id) for v in [*versions, v6]} == table_text)
    check("changes across the empty version read the same from git",
          rev.changes(db, versions[3].id, versions[4].id)
          == {"back.md": history.FileChange(None, "after an empty version\n")}
          and set(rev.changes(db, versions[2].id, versions[3].id)) == set(texts[2]))
    v7 = rev.record_version(db, MID, {"back.md": "recorded to git\n"}, None)
    final = history_replay.check(db, manuscript)
    check("a version recorded to git joins the same history and checks out",
          v7.version_no == 7 and final["identical"] and final["versions"] == 7,
          final)

    # A commit that does not hold what the row says. Git itself will not
    # replace a version's commit, so the ref is removed first.
    clobber = raises(GitError, lambda: Repository(repository).write(
        [Commit(history.version_ref(v7.id), history.version_ref(v6.id),
                "tampered", 1, {"back.md": b"tampered\n"})]))
    check("a version's commit cannot be written over",
          clobber is not None and history_replay.check(db, manuscript)["identical"],
          clobber)
    Repository(repository).delete_ref(history.version_ref(v7.id))
    Repository(repository).write(
        [Commit(history.version_ref(v7.id), history.version_ref(v6.id),
                "tampered", 1, {"back.md": b"tampered\n"})])
    broken = history_replay.check(db, manuscript)
    check("check names a version whose commit differs from its row",
          not broken["identical"]
          and broken["differing"] == [{"version_no": 7, "files": ["(checksum)"]}],
          broken)


def test_verbs(root: Path) -> None:
    use_backend(root, "table")
    home = root / "verbs"
    book = home / "book"
    book.mkdir(parents=True)
    (book / "a.md").write_text("# A\n\nOne.\n", encoding="utf-8")

    def run(*argv: str) -> tuple[str, object]:
        out = io.StringIO()
        code: object = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                cli_main(["--workspace", str(home), *argv])
            except SystemExit as stop:
                code = stop.code
        return out.getvalue(), code

    run("init", "--name", "demo", "--path", str(book))
    run("collect")
    (book / "a.md").write_text("# A\n\nTwo.\n", encoding="utf-8")
    run("collect")
    out, code = run("history", "replay", "--dry-run")
    check("history replay --dry-run prints the count and writes nothing",
          code == 0 and "demo: 2 version(s), 0 already in the git history, "
          "would replay 2" in out and "Nothing was written" in out
          and not (home / ".authorlm" / "history").exists(), out)
    out, code = run("history", "check")
    check("history check before a replay exits 1 and names the fix",
          code not in (0, None) and "authorlm history replay" in str(code), code)
    out, code = run("history", "replay", "--all")
    check("history replay copies them and names the next step",
          code == 0 and "replayed 2" in out
          and "next: authorlm history check --all" in out, out)
    out, code = run("history", "check", "--all")
    check("history check reads them back, identical",
          code == 0 and "demo: 2 version(s), 2 file(s) read back, identical"
          in out, out)
    out, code = run("history", "list")
    check("history list is unchanged by a replay",
          code == 0 and len(out.strip().splitlines()) == 2, out)


def test_shipped_setting() -> None:
    """The live switch waits for the owner, and for the modules that still
    read the table themselves: with git chosen they would read a version
    as having no text."""
    from test_version_history import STILL_DIRECT

    package = Path(history.__file__).resolve().parent
    shipped = tomllib.loads(
        (package.parent / "config.toml").read_text(encoding="utf-8"))
    waiting = sorted(n for n in STILL_DIRECT if (package / n).exists())
    check("the shipped config.toml records to the table while any module "
          "still reads the table directly",
          shipped["history"]["backend"] == "table" or not waiting, waiting)


def main() -> None:
    if shutil.which("git") is None:
        print("SKIP: the private git history, git is not installed")
        return
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for test in (test_adapter, test_store, test_two_at_once,
                     test_mismatch, test_replay, test_verbs):
            print(f"{test.__name__}:")
            test(root)
    print("test_shipped_setting:")
    test_shipped_setting()
    print(f"All {PASSED} checks passed.")


if __name__ == "__main__":
    main()
