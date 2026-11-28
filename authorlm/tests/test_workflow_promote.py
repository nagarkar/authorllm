"""Promoting a workflow decision to a style law (authorlm/workflow/promote.py,
nagarkar/authorllm#553): one chosen principle or ruling becomes a law with
its wording editable, through the style-law add path; the decision carries
the law's id and is not promoted again; the CLI's list and --dry-run write
nothing; the Workflows page promotes one decision at a time; doctor finds a
promoted decision whose law is gone. Test data only.

    python3 tests/test_workflow_promote.py
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_workflow import _cli, _tables, check, raises, world  # noqa: E402

from authorlm import api, styles, workflow_page  # noqa: E402
from authorlm.workflow import core, promote  # noqa: E402


def _setup(db, ms):
    inst = core.start(db, ms, "one", "refine")
    p = core.decide(db, inst, "principle", "never open with a question",
                    reason="candidate style rule, ticked at the wrap-up")
    r = core.decide(db, inst, "ruling", "Sanskrit terms stay in italics")
    core.skip(db, inst, "Testing promotion.")
    return core.get_instance(db, ms, inst["id"]), p, r


def test_promote_through_the_cli() -> None:
    with world() as (db, ms, ws):
        inst, p, r = _setup(db, ms)
        iid = inst["id"]
        check("without a root guide there is nowhere to put the law",
              "no style guide" in raises(
                  lambda: promote.plan(db, ms, inst, p["id"], aspect="rhetoric"),
                  LookupError))
        api.define_style_guide(db, ms, "house")
        api.define_style_guide(db, ms, "sermons", parent="house")
        before = _tables(db)
        out = _cli(ws, "promote", iid)
        check("promote without a decision lists principles and rulings",
              p["id"] in out and r["id"] in out and "not promoted" in out
              and "skip" not in out, out)
        out = _cli(ws, "promote", iid, p["id"], "--aspect", "rhetoric",
                   "--dry-run")
        check("--dry-run shows the law, its guide and the notes",
              "would add a rhetoric law to house" in out
              and "never open with a question" in out and iid in out, out)
        check("the list and --dry-run write nothing", _tables(db) == before)
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                _cli(ws, "promote", iid, p["id"])
                raise AssertionError("expected exit")
            except SystemExit as e:
                check("an aspect is required", "aspect" in str(e.code), e.code)
        skip = next(d for d in core.decisions(db, inst) if d["kind"] == "skip")
        check("a skip is not promoted", "not promoted to law" in raises(
            lambda: promote.plan(db, ms, inst, skip["id"], aspect="tone")))
        data = json.loads(_cli(
            ws, "promote", iid, p["id"][:9], "--aspect", "rhetoric",
            "--statement", "Do not open an essay with a question.", "--json"))
        law = db.one("SELECT * FROM style_laws WHERE id = ?", (data["law"]["id"],))
        check("the edited wording is the law, in the root guide",
              law["statement"] == "Do not open an essay with a question."
              and law["aspect"] == "rhetoric" and law["status"] == "active"
              and law["guide_id"] == styles.root_guide(db, ms["id"])["id"], dict(law))
        check("its notes quote the decision, its reason and the instance",
              '"never open with a question"' in law["notes"]
              and iid in law["notes"] and p["id"] in law["notes"]
              and "one.md" in law["notes"] and "ticked at the wrap-up" in law["notes"],
              law["notes"])
        hist = styles.law_history(db, ms["id"], law["id"])
        check("the law has its add history row", [h["event"] for h in hist] == ["add"])
        row = db.one("SELECT law_id FROM workflow_decisions WHERE id = ?", (p["id"],))
        check("the decision carries the law's id", row["law_id"] == law["id"])
        check("a promoted decision is not promoted again", "already promoted" in raises(
            lambda: promote.promote(db, ms, inst, p["id"], aspect="rhetoric")))
        out = _cli(ws, "show", iid)
        check("show names the law on the promoted decision",
              f"→ law {law['id']}" in out, out)
        _cli(ws, "promote", iid, r["id"], "--aspect", "formatting",
             "--guide", "sermons")
        rows = promote.promotable(db, inst)
        check("a ruling goes to the guide named; each decision shows its law",
              [d["law"]["guide"] for d in rows] == ["house", "sermons"], rows)
        check("doctor finds every law", promote.doctor_check(db.path)["ok"])


def test_promote_on_the_page_and_doctor() -> None:
    with world() as (db, ms, ws):
        inst, p, r = _setup(db, ms)
        api.define_style_guide(db, ms, "house")
        v = workflow_page.dispatch("instance", {"manuscript": "MS", "id": inst["id"]},
                                   workspace=str(ws))
        pr = v["promotions"]
        check("the page lists the principles and rulings with the form's choices",
              [d["id"] for d in pr["decisions"]] == [p["id"], r["id"]]
              and pr["root"] == "house" and pr["guides"] == ["house"]
              and "rhetoric" in pr["aspects"]
              and all(d["law"] is None for d in pr["decisions"]), pr)
        v = workflow_page.dispatch("promote", {
            "manuscript": "MS", "id": inst["id"], "decision": r["id"],
            "statement": "Sanskrit terms are italic.", "aspect": "formatting",
            "guide": "house"}, workspace=str(ws))
        got = {d["id"]: d["law"] for d in v["promotions"]["decisions"]}
        check("the page promotes the one decision it names, with its wording",
              got[p["id"]] is None and got[r["id"]]["statement"]
              == "Sanskrit terms are italic.", got)
        law_id = got[r["id"]]["id"]
        hist = styles.law_history(db, ms["id"], law_id)
        check("the page's road is recorded", hist[0]["actor"] == "web", hist)
        raises(lambda: workflow_page.dispatch("promote", {
            "manuscript": "MS", "id": inst["id"], "decision": r["id"],
            "aspect": "formatting"}, workspace=str(ws)), ValueError, "already promoted")
        # A law removed behind authorlm's back (authorlm never deletes one).
        db.conn.execute("PRAGMA foreign_keys = OFF")
        db.conn.execute("DELETE FROM edit_events WHERE item_id = ?", (law_id,))
        db.conn.execute("DELETE FROM style_laws WHERE id = ?", (law_id,))
        db.conn.commit()
        c = promote.doctor_check(db.path)
        check("doctor names a promoted decision whose law is gone",
              not c["ok"] and r["id"] in c["detail"] and law_id in c["detail"], c)
        check("the page says the law is missing",
              promote.promotable(db, inst)[1]["law"].get("missing") is True)


def test_migration_adds_law_id() -> None:
    with world() as (db, _ms, ws):
        cols = {r[1]: r for r in db.conn.execute("PRAGMA table_info(workflow_decisions)")}
        check("workflow_decisions has a nullable law_id", "law_id" in cols
              and cols["law_id"][3] == 0)
        fks = [tuple(r)[2:4] for r in db.conn.execute(
            "PRAGMA foreign_key_list(workflow_decisions)")]
        check("law_id references style_laws(id)", ("style_laws", "law_id") in fks, fks)
        # An older database: drop the column, reopen, and it comes back.
        # (Rebuilt rather than DROP COLUMN, which SQLite declines for a
        # column with a REFERENCES clause.)
        db.conn.executescript(
            "CREATE TABLE wd_old AS SELECT id, version, created_at, created_by, "
            "schema_version, metadata, instance_id, kind, reason, words, detail "
            "FROM workflow_decisions; DROP TABLE workflow_decisions; "
            "ALTER TABLE wd_old RENAME TO workflow_decisions;")
        check("doctor passes before the migration",
              promote.doctor_check(db.path)["state"] == "not-migrated")
        again = api.open_db(str(ws))
        check("opening the workspace adds it", "law_id" in {
            r[1] for r in again.conn.execute("PRAGMA table_info(workflow_decisions)")})


def main() -> None:
    tests = [test_promote_through_the_cli, test_promote_on_the_page_and_doctor,
             test_migration_adds_law_id]
    for fn in tests:
        print(fn.__name__)
        fn()
    print(f"OK — {len(tests)} promotion cases")


if __name__ == "__main__":
    main()
