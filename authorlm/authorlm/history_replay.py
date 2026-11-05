"""Copy a manuscript's version history from the table into git, and check
that git gives back what was put in.

`replay` turns every version whose text is in its `manuscript_versions`
row into a commit, oldest first, each under the ref named after its
version id and dated when the version was recorded. It never changes a
row: the table is left exactly as it was, and running it again copies
only the versions git does not have yet. `check` reads every file of
every version back from git and compares it, byte for byte, with the
row's text and with the row's checksum.

Neither depends on `[history] backend`: a workspace is replayed and
checked while it still records to the table, and only then switched.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from .db import Database
from .history import (BRANCH, VERSION_REFS, GitStore, TableStore, Version,
                      commit_message, commit_time, decode, encode,
                      observed_names, version_ref)
from .history_git import Commit, blob_id
from .revisions import checksum, list_versions

MAP_FILENAME = "replay-map.tsv"


def _commits(versions: list[Version], have: set[str], table: TableStore,
             git: GitStore) -> Iterator[Commit]:
    """A commit for each version git lacks, as a change against the
    version before it."""
    before: Version | None = None
    before_data: dict[str, bytes] | None = None
    for version in versions:
        if version_ref(version.id) in have:
            before, before_data = version, None
            continue
        if before is not None and before_data is None:
            before_data = {name: encode(text)
                           for name, text in git.files(before.id).items()}
        data = {name: encode(text)
                for name, text in table.files(version.id).items()}
        base = before_data or {}
        yield Commit(
            ref=version_ref(version.id),
            parent=version_ref(before.id) if before else None,
            message=commit_message(version),
            when=commit_time(version.created_at),
            changed={name: payload for name, payload in data.items()
                     if base.get(name) != payload},
            removed=tuple(name for name in base if name not in data),
        )
        before, before_data = version, data


def replay(db: Database, manuscript: dict, *, dry_run: bool = False) -> dict:
    """Copy `manuscript`'s versions into its git history. With `dry_run`
    it reports what it would copy and writes nothing."""
    git, table = GitStore(db), TableStore(db)
    repository = git.repository(manuscript["id"])
    versions = list_versions(db, manuscript["id"])
    have = set(repository.refs(VERSION_REFS)) if repository.exists() else set()
    todo = [v for v in versions if version_ref(v.id) not in have]
    report = {
        "manuscript": manuscript["name"], "versions": len(versions),
        "already_in_history": len(versions) - len(todo),
        "to_replay": len(todo), "replayed": 0, "dry_run": dry_run,
        "repository": str(repository.git_dir), "map": None, "bytes": None,
    }
    if dry_run or not versions:
        return report
    with git.recording(manuscript["id"]):
        if not repository.exists():
            repository.create(observed_names())
        if todo:
            report["replayed"] = repository.write(
                _commits(versions, have, table, git))
            repository.pack(thorough=True)
            repository.follow(BRANCH, version_ref(versions[-1].id))
        report["map"] = str(_write_map(repository.git_dir, versions,
                                       repository.refs(VERSION_REFS)))
    report["bytes"] = repository.size()
    return report


def _write_map(git_dir: Path, versions: list[Version],
               refs: dict[str, str]) -> Path:
    """The version-to-commit map as the replay left it, one line a
    version. The refs in the repository are the map that is used; this
    file is the record of the replay."""
    lines = ["version_no\tversion_id\tcommit\trecorded_at"]
    lines += [f"{v.version_no}\t{v.id}\t{refs.get(version_ref(v.id), '')}"
              f"\t{v.created_at}" for v in versions]
    path = git_dir / MAP_FILENAME
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def check(db: Database, manuscript: dict) -> dict:
    """Read every file of every version back from git and compare.

    A version passes when git holds exactly the files its row's checksum
    was computed over and, where the row still has the text, exactly the
    row's bytes for each file. Reports the versions git lacks
    (`missing`), the ones that differ (`differing`, with the file names),
    and commits no version names (`unnamed`)."""
    git, table = GitStore(db), TableStore(db)
    repository = git.repository(manuscript["id"])
    versions = list_versions(db, manuscript["id"])
    report = {
        "manuscript": manuscript["name"], "versions": len(versions),
        "files_compared": 0, "missing": [], "differing": [], "unnamed": [],
        "repository": str(repository.git_dir), "bytes": None,
        "identical": False,
    }
    if not repository.exists():
        report["missing"] = [v.version_no for v in versions]
        report["identical"] = not versions
        return report
    read_back: set[str] = set()     # blob ids already compared byte for byte
    with repository.reader() as reader:
        for version in versions:
            ref = version_ref(version.id)
            if reader.commit(ref) is None:
                report["missing"].append(version.version_no)
                continue
            held = reader.blob_ids(ref)
            wrong: set[str] = set()
            if table.has_text(version.id):
                data = {name: encode(text)
                        for name, text in table.files(version.id).items()}
                wrong |= set(held) ^ set(data)
                for name in set(held) & set(data):
                    if held[name] != blob_id(data[name]):
                        wrong.add(name)
                    elif held[name] not in read_back:
                        if reader.blob(held[name]) != data[name]:
                            wrong.add(name)
                        read_back.add(held[name])
                texts = {name: decode(payload)
                         for name, payload in data.items()}
            else:
                texts = {name: decode(payload)
                         for name, payload in reader.files(ref).items()}
            report["files_compared"] += len(held)
            if wrong or checksum(texts) != version.checksum:
                report["differing"].append(
                    {"version_no": version.version_no,
                     "files": sorted(wrong) or ["(checksum)"]})
    named = {version_ref(v.id) for v in versions}
    report["unnamed"] = sorted(set(repository.refs(VERSION_REFS)) - named)
    report["bytes"] = repository.size()
    report["identical"] = not report["missing"] and not report["differing"]
    return report
