"""Version history has one door: the functions in `authorlm.revisions`.

A caller gets a `Version` (which one, when, how recorded) and asks for
text with `file_at`, `files_at` or `changes`; `record_version` is the only
writer. These checks pin what each of them answers, on a throwaway
database, once for each place the text can be kept (`[history] backend`:
the table, then git), and that no other module reads the
`manuscript_versions` table except the ones still waiting to be moved.

Standalone, hermetic:  python3 tests/test_version_history.py
"""

from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"

import dataclasses  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import revisions as rev  # noqa: E402
from authorlm.db import Database  # noqa: E402

PASSED = 0
MID = "ms-one"
OTHER = "ms-other"

# Names a book folder can really hold: a subfolder, a space, an accent
# (composed and decomposed), a quote, a backslash, an emoji.
ODD_NAMES = {
    "part one/chapter 1.md": "# One\n\nFirst.\n",
    "café.md": "composed accent\n",
    "café.md": "decomposed accent\n",
    'say "hello".md': "a quote in the name\n",
    "back\\slash.md": "a backslash in the name\n",
    "emoji 😀.md": "an emoji, and text with one 😀 too\n",
    "empty.md": "",
    "toc.yaml": "- part one/chapter 1.md\n",
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


def test_record_and_look_up(db: Database) -> None:
    check("no version before the first record",
          rev.latest_version(db, MID) is None
          and rev.latest_versions(db, MID, 2) == []
          and rev.list_versions(db, MID) == []
          and rev.count_versions(db, MID) == 0)

    v1 = rev.record_version(db, MID, {"a.md": "one\n"}, "se-1", "first")
    check("the first record is version 1, with what it was told",
          v1 is not None and v1.version_no == 1 and v1.manuscript_id == MID
          and v1.source == "first" and v1.session_id == "se-1"
          and v1.checksum == rev.checksum({"a.md": "one\n"}), v1)
    check("a version carries no text",
          {f.name for f in dataclasses.fields(v1)}
          == {"id", "manuscript_id", "version_no", "checksum", "source",
              "session_id", "created_at"})
    check("recording the same files again records nothing",
          rev.record_version(db, MID, {"a.md": "one\n"}, None) is None
          and rev.count_versions(db, MID) == 1)

    v2 = rev.record_version(db, MID, {"a.md": "two\n", "b.md": "new\n"}, None)
    v3 = rev.record_version(db, MID, {"b.md": "new\n"}, "se-1")
    check("each record takes the next number",
          (v2.version_no, v3.version_no) == (2, 3) and v2.source == "snapshot")
    check("latest_version is the highest number",
          rev.latest_version(db, MID) == v3)
    check("latest_versions is newest first and stops at the count",
          rev.latest_versions(db, MID, 2) == [v3, v2]
          and rev.latest_versions(db, MID, 9) == [v3, v2, v1])
    check("list_versions is oldest first",
          rev.list_versions(db, MID) == [v1, v2, v3])
    check("count_versions counts them", rev.count_versions(db, MID) == 3)
    check("version_by_id finds a version; None for an unknown or absent id",
          rev.version_by_id(db, v2.id) == v2
          and rev.version_by_id(db, "mv-nope") is None
          and rev.version_by_id(db, None) is None)
    check("version_by_number finds by number; None when there is none",
          rev.version_by_number(db, MID, 2) == v2
          and rev.version_by_number(db, MID, 7) is None)
    check("version_as_of is the latest version recorded by that moment",
          rev.version_as_of(db, MID, v2.created_at) in (v2, v3)
          and rev.version_as_of(db, MID, "9999") == v3
          and rev.version_as_of(db, MID, "0000") is None)
    check("last_recorded_at is the session's newest version time",
          rev.last_recorded_at(db, "se-1") == v3.created_at
          and rev.last_recorded_at(db, "se-none") is None)

    other = rev.record_version(db, OTHER, {"z.md": "elsewhere\n"}, None)
    check("another manuscript numbers its own versions",
          other.version_no == 1 and rev.latest_version(db, MID) == v3
          and rev.count_versions(db, MID) == 3
          and rev.list_versions(db, OTHER) == [other])


def test_text_at_a_version(db: Database) -> None:
    v1 = rev.record_version(db, MID, ODD_NAMES, None)
    check("files_at returns every file exactly as recorded",
          rev.files_at(db, v1.id) == ODD_NAMES)
    for name, text in ODD_NAMES.items():
        check(f"file_at reads {name!r} alone",
              rev.file_at(db, v1.id, name) == text,
              repr(rev.file_at(db, v1.id, name)))
    check("an empty file is empty text, not a missing file",
          rev.file_at(db, v1.id, "empty.md") == "")
    check("file_at is None for a file the version does not hold",
          rev.file_at(db, v1.id, "absent.md") is None
          and rev.file_at(db, v1.id, "part one") is None)
    check("file_at is None for an unknown or absent version",
          rev.file_at(db, "mv-nope", "café.md") is None
          and rev.file_at(db, None, "café.md") is None)
    check("files_at is empty for an unknown or absent version",
          rev.files_at(db, "mv-nope") == {} and rev.files_at(db, None) == {})

    v2 = rev.record_version(db, MID, {"a.md": "x\n"}, None)
    check("file_counts gives each version's number of files",
          rev.file_counts(db, MID) == {v1.id: len(ODD_NAMES), v2.id: 1})


def test_changes(db: Database) -> None:
    v1 = rev.record_version(
        db, MID, {"edited.md": "old\n", "same.md": "same\n",
                  "gone.md": "bye\n"}, None)
    v2 = rev.record_version(
        db, MID, {"edited.md": "new\n", "same.md": "same\n",
                  "added.md": "hi\n"}, None)
    got = rev.changes(db, v1.id, v2.id)
    check("changes lists the files that differ, in path order",
          list(got) == ["added.md", "edited.md", "gone.md"], list(got))
    check("an edited file has both texts",
          got["edited.md"] == rev.FileChange("old\n", "new\n"))
    check("an added file has no old text",
          got["added.md"] == rev.FileChange(None, "hi\n"))
    check("a removed file has no new text",
          got["gone.md"] == rev.FileChange("bye\n", None))
    check("the other direction swaps the sides",
          rev.changes(db, v2.id, v1.id)["gone.md"]
          == rev.FileChange(None, "bye\n"))
    check("against no older version every file reads as added",
          rev.changes(db, None, v1.id)
          == {"edited.md": rev.FileChange(None, "old\n"),
              "gone.md": rev.FileChange(None, "bye\n"),
              "same.md": rev.FileChange(None, "same\n")})
    check("a version has no changes against itself",
          rev.changes(db, v2.id, v2.id) == {})


def test_diff_pair(db: Database) -> None:
    check("no versions: nothing to compare",
          raises(LookupError, lambda: rev.diff_pair(db, MID, []))
          == "no versions collected yet")
    v1 = rev.record_version(db, MID, {"a.md": "1\n"}, None)
    check("one version, no number: it against nothing",
          rev.diff_pair(db, MID, []) == (None, v1))
    v2 = rev.record_version(db, MID, {"a.md": "2\n"}, None)
    v3 = rev.record_version(db, MID, {"a.md": "3\n"}, None)
    check("no number: the latest two", rev.diff_pair(db, MID, []) == (v2, v3))
    check("one number: it against the one just below",
          rev.diff_pair(db, MID, [2]) == (v1, v2)
          and rev.diff_pair(db, MID, [1]) == (None, v1))
    check("two numbers: lower against higher, in either order",
          rev.diff_pair(db, MID, [3, 1]) == (v1, v3)
          and rev.diff_pair(db, MID, [1, 3]) == (v1, v3))
    check("a number that is not a version says which exist",
          raises(LookupError, lambda: rev.diff_pair(db, MID, [1, 9]))
          == "no version v9 (have v1..v3)")


def test_collect_revision(root: Path, db: Database) -> None:
    book = root / "book"
    (book / "_audio").mkdir(parents=True)
    (book / "a.md").write_text("# A\n\nText.\n", encoding="utf-8")
    (book / "_audio" / "skip.md").write_text("not observed\n", encoding="utf-8")
    manuscript = {"id": MID, "path": str(book)}
    v1 = rev.collect_revision(db, manuscript, None, source="test")
    check("collect_revision records what observation reads from the folder",
          v1.version_no == 1 and v1.source == "test"
          and rev.files_at(db, v1.id) == {"a.md": "# A\n\nText.\n"})
    check("an unchanged folder records nothing",
          rev.collect_revision(db, manuscript, None) is None)
    (book / "a.md").write_text("# A\n\nText, edited.\n", encoding="utf-8")
    v2 = rev.collect_revision(db, manuscript, None)
    transitions = rev.detect_transitions(db, MID, v1, v2)
    check("detect_transitions reads both versions through the door",
          [(t["kind"], t["location"], t["version_before"], t["version_after"])
           for t in transitions] == [("rewrite", "a.md#A", v1.id, v2.id)],
          transitions)
    first = rev.detect_transitions(db, OTHER, None, v1)
    check("with no version before, every file is a new file",
          [(t["kind"], t["location"], t["version_before"]) for t in first]
          == [("file_added", "a.md", None)], first)
    check("a missing folder is never recorded as an empty version",
          raises(rev.ManuscriptRootUnreadable, lambda: rev.collect_revision(
              db, {"id": MID, "path": str(root / "nowhere")}, None))
          is not None and rev.count_versions(db, MID) == 2)


# Modules that still read the table themselves, each waiting on other
# work (authorllm#365): `gdocs.py` belongs to the Doc bridge tab split
# (#366); the rest may be removed by stage 1b (#364).
STILL_DIRECT = {"gdocs.py", "guidance.py", "briefing.py", "analysis.py",
                "plan.py"}
_TABLE_READ = re.compile(
    r"(?:FROM|JOIN|INTO|UPDATE)\s+manuscript_versions\b"
    r"|insert\(\s*[\"']manuscript_versions[\"']", re.IGNORECASE)


def test_one_door() -> None:
    package = Path(rev.__file__).resolve().parent
    direct = {path.name for path in package.glob("*.py")
              if _TABLE_READ.search(path.read_text(encoding="utf-8"))}
    direct -= {"revisions.py", "history.py", "mcp_server.py"}
    check("only revisions.py, its stores in history.py and the modules "
          "listed as waiting read the versions table", direct <= STILL_DIRECT,
          f"move behind authorlm.revisions: {sorted(direct - STILL_DIRECT)}")
    present = {name for name in STILL_DIRECT if (package / name).exists()}
    check("every module listed as waiting still reads the table",
          present <= direct,
          f"drop from STILL_DIRECT: {sorted(present - direct)}")


def use_backend(root: Path, backend: str) -> None:
    """Point this process at a config.toml that names `backend`."""
    config = root / f"{backend}.toml"
    config.write_text(f'[history]\nbackend = "{backend}"\n', encoding="utf-8")
    os.environ["AUTHORLM_CONFIG"] = str(config)


def main() -> None:
    backends = ["table", "git"]
    if shutil.which("git") is None:
        backends.remove("git")
        print("SKIP: the interface on the git backend, git is not installed")
    with tempfile.TemporaryDirectory() as tmp:
        for backend in backends:
            root = Path(tmp) / backend
            root.mkdir()
            use_backend(root, backend)
            for test in (test_record_and_look_up, test_text_at_a_version,
                         test_changes, test_diff_pair):
                print(f"{test.__name__} [{backend}]:")
                folder = root / test.__name__
                folder.mkdir()
                test(Database(folder / "authorlm.db"))
            print(f"test_collect_revision [{backend}]:")
            test_collect_revision(root, Database(root / "authorlm.db"))
    print("test_one_door:")
    test_one_door()
    print(f"All {PASSED} checks passed.")


if __name__ == "__main__":
    main()
