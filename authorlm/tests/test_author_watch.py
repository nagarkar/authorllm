"""Offline checks for tools/author_watch.py: the one watcher for the Dev edit
and Merge forms pages. No server, no Google: a fake database and queue."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "author_watch", HERE.parent / "tools" / "author_watch.py")
aw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aw)

PASSED = 0


def check(label: str, condition: bool, context: object = "") -> None:
    global PASSED
    if not condition:
        print(f"  FAIL: {label}\n    {context}")
        sys.exit(1)
    PASSED += 1
    print(f"  ok: {label}")


class FakeDB:
    def __init__(self):
        self.rows: list[dict] = []

    def one(self, sql, params):
        return {"id": "ms-1"} if params == ("book",) else None

    def all(self, sql, params):
        return [r for r in self.rows if r["state"] == "written"]

    def add(self, fid, file, verdict=None):
        meta = {aw.RULING_KEY: {"verdict": verdict}} if verdict else {}
        self.rows.append({"id": fid, "file": file, "state": "written",
                          "metadata": json.dumps(meta)})

    def rule(self, fid, verdict):
        for r in self.rows:
            if r["id"] == fid:
                meta = {aw.RULING_KEY: {"verdict": verdict}} if verdict else {}
                r["metadata"] = json.dumps(meta)

    def close(self, fid):
        for r in self.rows:
            if r["id"] == fid:
                r["state"] = "cleaned"


def forms_watch() -> None:
    print("merge forms:")
    db = FakeDB()
    db.add("f1", "a.md")
    db.add("f2", "a.md", "accept")
    queue = {"pending": [], "running": None, "results": {}}
    up = {"ok": True}

    def fetch():
        if not up["ok"]:
            raise OSError("connection refused")
        return json.loads(json.dumps(queue))

    w = aw.FormsWatch("book", 8792, None, db=db, fetch_queue=fetch)
    check("the first pass reports nothing already there", w.poll(True) == [])
    db.rule("f1", "decline")
    out = w.poll(False)
    check("a ruling is reported, and the file's last ruling completes it",
          out == ["RULING a.md f1: decline", "COMPLETE rulings a.md"], out)
    check("nothing changed, nothing said", w.poll(False) == [])
    db.rule("f2", None)
    out = w.poll(False)
    check("an Undo is reported as cleared", out == ["RULING a.md f2: cleared"], out)
    db.rule("f2", "edit")
    out = w.poll(False)
    check("ruling it again completes the file again",
          out == ["RULING a.md f2: edit", "COMPLETE rulings a.md"], out)
    db.add("g1", "b.md")
    db.add("g2", "b.md")
    out = w.poll(False)
    check("newly staged forms are reported per file",
          out == ["FORMS STAGED b.md: 2 new open form(s)"], out)
    queue.update(running="a.md", pending=["b.md"])
    out = w.poll(False)
    check("a merge starting is reported", out == ["MERGING a.md"], out)
    db.close("f1")
    db.close("f2")
    queue.update(running=None, pending=[], results={"a.md": {
        "stopped": None, "message": "a.md: 1 accepted, 0 modified, 1 declined"}})
    out = w.poll(False)
    check("a finished merge is reported with the server's message",
          out == ["MERGED a.md: a.md: 1 accepted, 0 modified, 1 declined"], out)
    queue["results"]["b.md"] = {"stopped": "conflict",
                                "message": "changed on both sides"}
    out = w.poll(False)
    check("a stopped merge says why",
          out == ["MERGE STOPPED b.md: conflict — changed on both sides"], out)
    up["ok"] = False
    out = w.poll(False)
    check("an unreachable server is said once",
          len(out) == 1 and out[0].startswith("FORMS SERVER unreachable"), out)
    check("and not again while it stays down", w.poll(False) == [])
    up["ok"] = True
    out = w.poll(False)
    check("its return is reported", out == ["FORMS SERVER back"], out)


def devedit_watch() -> None:
    print("dev edit:")
    run = Path(tempfile.mkdtemp(prefix="watch-run-"))
    (run / "stages" / "1").mkdir(parents=True)
    dec = run / "stages" / "1" / "decisions.json"
    dec.write_text(json.dumps([{"n": 1}, {"n": 2}]))
    (run / "queue.json").write_text(json.dumps([{"id": "i1", "file": "x.md"}]))
    w = aw.DevEditWatch(run)
    check("the first pass reports nothing", w.poll(True) == [])
    dec.write_text(json.dumps([{"n": 1, "answered": "t1", "answer": "A"},
                               {"n": 2, "answered": "t2", "answer": "B"}]))
    (run / "queue.json").write_text(json.dumps(
        [{"id": "i1", "file": "x.md", "verdict": "accept", "reason_given": "fine"}]))
    out = w.poll(False)
    check("decisions, verdicts and both COMPLETE lines, as devedit_watch said them",
          out == ["DECISION stage 1 #1: A", "DECISION stage 1 #2: B",
                  "COMPLETE decisions stage 1", "VERDICT i1: accept — reason: fine",
                  "COMPLETE items x.md"], out)


def helper_edges() -> None:
    """Direct pins for the pure diff helpers FormsWatch composes.
    The FormsWatch road above covers the happy path; these catch the
    edges a poll-loop change can break without a live server."""
    print("helpers:")
    snap = aw.forms_snapshot([
        {"id": "f1", "file": "a.md", "metadata": '{"page_ruling":{"verdict":"accept"}}'},
        {"id": "f2", "file": "a.md", "metadata": "{not-json"},
        {"id": "f3", "file": "b.md", "metadata": None},
    ])
    check("snapshot groups by file and reads a ruling",
          snap == {"a.md": {"f1": "accept", "f2": None},
                   "b.md": {"f3": None}}, snap)

    prev = {"a.md": {"f1": None, "f2": "accept"}}
    cur = {"a.md": {"f1": "decline", "f2": "accept", "f3": None},
           "b.md": {"g1": None}}
    lines = aw.forms_lines(prev, cur)
    check("new forms, a ruling change, and no COMPLETE while one is open",
          lines == ["FORMS STAGED a.md: 1 new open form(s)",
                    "RULING a.md f1: decline",
                    "FORMS STAGED b.md: 1 new open form(s)"], lines)
    done = aw.forms_lines(
        {"a.md": {"f1": "accept", "f2": None}},
        {"a.md": {"f1": "accept", "f2": "edit"}})
    check("COMPLETE only when the file first becomes fully ruled",
          done == ["RULING a.md f2: edit", "COMPLETE rulings a.md"], done)
    again = aw.forms_lines(
        {"a.md": {"f1": "accept", "f2": "edit"}},
        {"a.md": {"f1": "accept", "f2": "edit"}})
    check("already-complete stays quiet", again == [], again)
    cleared = aw.forms_lines(
        {"a.md": {"f1": "accept"}},
        {"a.md": {"f1": None}})
    check("Undo reports as cleared",
          cleared == ["RULING a.md f1: cleared"], cleared)

    q_prev = {"running": None, "results": {}}
    q_cur = {"running": "a.md", "results": {
        "a.md": {"stopped": None, "message": "ok"},
        "b.md": {"stopped": "conflict", "message": "both sides"}}}
    q = aw.queue_lines(q_prev, q_cur)
    check("a start and both result shapes are reported once",
          q == ["MERGING a.md",
                "MERGED a.md: ok",
                "MERGE STOPPED b.md: conflict — both sides"], q)
    check("unchanged results stay quiet",
          aw.queue_lines(q_cur, q_cur) == [])


def main() -> None:
    forms_watch()
    devedit_watch()
    helper_edges()
    print(f"OK — {PASSED} watcher checks")


if __name__ == "__main__":
    main()
