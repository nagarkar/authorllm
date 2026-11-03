"""write_accept must not duplicate a beat after a crash mid-accept.

Disk is written before the proposal/cursor commit. A killed attempt leaves
the file holding the beat while guidance_history stays `proposed`. Retry
must finish the DB half without appending again.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import api  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402


def _fixture():
    root = Path(tempfile.mkdtemp(prefix="authorlm-write-accept-"))
    ws = root / "ws"
    ms = ws / "manuscript"
    ms.mkdir(parents=True)
    (ms / "01-epictetus.md").write_text("# Epictetus\n\nThe old essay.\n")
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms)])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    api.define_style_guide(db, manuscript, "Connections")
    api.attach_style(db, manuscript, "01-epictetus.md", "Connections")
    intent = api.declare_intent(db, manuscript, "Rework Epictetus")["intent"]
    with contextlib.redirect_stdout(io.StringIO()):
        api.write_start(db, manuscript, {}, "01-epictetus.md",
                        intent["id"][:8])
    plan = [{"n": 1, "role": "opener", "budget": 40},
            {"n": 2, "role": "close", "budget": 40}]
    row = db.one("SELECT id FROM writeups")
    db.update("writeups", row["id"], {"plan": json.dumps(plan), "cursor": 0})
    return root, db, manuscript, ms / "01-epictetus.md"


def _land_disk_only(db, manuscript, path: Path, accepted: str) -> None:
    """Simulate the crash window: disk written, proposal still proposed."""
    existing = path.read_text(encoding="utf-8")
    if api.is_placeholder(existing):
        existing = ""
    prefix = existing.rstrip("\n") + "\n\n" if existing.strip() else ""
    path.write_text(prefix + accepted + "\n", encoding="utf-8")
    writeup = api._writeup(db, manuscript, None)
    proposal = api._beat_proposal(db, writeup, api._current_beat(writeup)["n"])
    assert proposal is not None and proposal["state"] == "proposed"


def test_retry_after_partial_first_beat_does_not_duplicate() -> None:
    root, db, manuscript, path = _fixture()
    try:
        beat = "Beat one lands here."
        api.write_propose(db, manuscript, beat, "why",
                          no_critic="author dictated it")
        _land_disk_only(db, manuscript, path, beat)
        assert path.read_text(encoding="utf-8") == beat + "\n"

        with contextlib.redirect_stdout(io.StringIO()):
            result = api.write_accept(db, manuscript, {})

        assert path.read_text(encoding="utf-8") == beat + "\n", (
            "retry duplicated the first beat")
        assert result["decision"] == "accepted"
        assert db.one("SELECT cursor FROM writeups")["cursor"] == 1
        assert db.one(
            "SELECT state FROM guidance_history WHERE batch_index = 1"
        )["state"] == "accepted"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_retry_after_partial_append_does_not_duplicate() -> None:
    root, db, manuscript, path = _fixture()
    try:
        first = "Beat one lands here."
        second = "Beat two follows."
        api.write_propose(db, manuscript, first, "why",
                          no_critic="author dictated it")
        with contextlib.redirect_stdout(io.StringIO()):
            api.write_accept(db, manuscript, {})
        api.write_propose(db, manuscript, second, "why two",
                          no_critic="author dictated it")
        _land_disk_only(db, manuscript, path, second)
        before = path.read_text(encoding="utf-8")
        assert before == first + "\n\n" + second + "\n"

        with contextlib.redirect_stdout(io.StringIO()):
            api.write_accept(db, manuscript, {})

        assert path.read_text(encoding="utf-8") == before, (
            "retry duplicated an appended beat")
        assert db.one("SELECT cursor FROM writeups")["cursor"] == 2
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_fresh_accept_still_appends() -> None:
    root, db, manuscript, path = _fixture()
    try:
        beat = "Fresh accept lands once."
        api.write_propose(db, manuscript, beat, "why",
                          no_critic="author dictated it")
        with contextlib.redirect_stdout(io.StringIO()):
            api.write_accept(db, manuscript, {})
        assert path.read_text(encoding="utf-8") == beat + "\n"
        assert db.one("SELECT cursor FROM writeups")["cursor"] == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test_retry_after_partial_first_beat_does_not_duplicate()
    test_retry_after_partial_append_does_not_duplicate()
    test_fresh_accept_still_appends()
    print("ok")
