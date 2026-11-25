"""The Filters and lenses page (`runs_page.dispatch`, `/api/runs`): browse
the filters and lenses a manuscript sees, save an edited artifact back to
its file, and ask for runs on chosen tabs.

Source: the owner's ask of 2026-11-25 ("a UI page to scan through filters
and lenses and apply them to one or more tabs (multiselect) to generate
forms. The UI should allow for editing of the filter/lens text"), and the
standing rulings it must keep: filter and lens replies are drafted by a
session, never by a model call the author did not start (config.toml,
`[filtering]` absent), so asking for a run writes a request and nothing
else; one producer's forms per tab; a tab checked out to the Doc is pulled
before it is run on (the checkout gate's own remedy); no lens runs on a
protected register.

Hermetic: a temp workspace and temp libraries; any Google or model call
fails the test.
Run: python3 tests/test_runs_page.py
"""
from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import contextlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import urllib.request  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import api, audiobook_server, gdocs, llm, runs_page  # noqa: E402
from authorlm import filters as flt  # noqa: E402
from authorlm import lenses  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.db import ko_fields  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: object = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


def _filter(title: str, klass: str = "sequential") -> str:
    return f'---\nclass = "{klass}"\n---\n\n# {title}\n\nThe prompt.\n'


LENS = "# Plain reader\n\nFlag what a plain reader would stumble on.\n"
ESSAY_A = "# Alpha\n\nFirst unit of alpha.\n\nSecond unit of alpha.\n"
ESSAY_B = "# Beta\n\nOnly unit of beta.\n"


@contextlib.contextmanager
def _world():
    """A workspace with two essays, one library filter, one filter and one
    lens of the book's own, and one library lens. Google and the model
    client fail the test if asked for."""
    root = Path(tempfile.mkdtemp(prefix="authorlm-runs-page-")).resolve()
    saved_env = {k: os.environ.get(k)
                 for k in (flt.LIBRARY_ENV, lenses.LIBRARY_ENV)}
    saved = (gdocs.get_service, gdocs.get_docs_service, llm.LLMClient)

    def boom(*_a, **_k):
        raise AssertionError("an outside service was asked for")
    try:
        (root / "flib").mkdir()
        (root / "llib").mkdir()
        (root / "flib" / "shared-pass.md").write_text(
            _filter("Shared pass", "global"))
        (root / "llib" / "shared-lens.md").write_text(LENS)
        os.environ[flt.LIBRARY_ENV] = str(root / "flib")
        os.environ[lenses.LIBRARY_ENV] = str(root / "llib")
        ws, ms = root / "ws", root / "ws" / "manuscript"
        ms.mkdir(parents=True)
        (ms / "a.md").write_text(ESSAY_A)
        (ms / "b.md").write_text(ESSAY_B)
        (ms / "_filters").mkdir()
        (ms / "_filters" / "own-pass.md").write_text(_filter("Own pass"))
        (ms / "_lenses").mkdir()
        (ms / "_lenses" / "own-lens.md").write_text(LENS)
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms)])
        gdocs.get_service = gdocs.get_docs_service = boom
        llm.LLMClient = boom
        db = api.open_db(str(ws))
        man = api.get_manuscript(db)
        # The manuscript's recorded path (init resolves it).
        yield root, ws, Path(man["path"]), db, man
    finally:
        gdocs.get_service, gdocs.get_docs_service, llm.LLMClient = saved
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(root, ignore_errors=True)


def _tables(db) -> dict:
    names = [r["name"] for r in db.all(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT "
        "LIKE 'sqlite_%'")]
    return {n: [tuple(r) for r in db.all(f"SELECT * FROM {n}")]
            for n in names}


def check_catalog() -> None:
    with _world() as (root, _ws, ms, db, man):
        before = _tables(db)
        cat = runs_page.catalog(db, man, config={})
        check("reading the catalog writes no row in any table",
              _tables(db) == before)
        filters = {f["name"]: f for f in cat["filters"]}
        check("filters: the book's own and the library's, each with where "
              "it lives",
              filters["own-pass"]["source"] == "manuscript"
              and filters["own-pass"]["path"]
              == str(ms / "_filters" / "own-pass.md")
              and filters["shared-pass"]["source"] == "library"
              and filters["shared-pass"]["path"]
              == str(root / "flib" / "shared-pass.md"), filters)
        check("a filter row carries its class and one-line purpose",
              filters["own-pass"]["class"] == "sequential"
              and filters["own-pass"]["summary"] == "Own pass"
              and filters["shared-pass"]["class"] == "global")
        lens_rows = {item["name"]: item for item in cat["lenses"]}
        check("lenses: the book's own and the library's",
              lens_rows["own-lens"]["source"] == "manuscript"
              and lens_rows["shared-lens"]["source"] == "library"
              and lens_rows["own-lens"]["summary"] == "Plain reader",
              lens_rows)
        check("a filter never run has no last-run facts",
              filters["own-pass"]["last"] is None
              and filters["own-pass"]["runs"] == 0)
        check("tabs are the chapter files in reading order with their size",
              [(t["file"], t["title"], t["units"]) for t in cat["tabs"]]
              == [("a.md", "Alpha", 3), ("b.md", "Beta", 2)], cat["tabs"])
        check("with [filtering] absent the page says the billed filter road "
              "is off, and names a session as the drafter",
              cat["drafting"]["filter"]["road"] == "session"
              and cat["drafting"]["filter"]["native_model"] is None
              and "off" in cat["drafting"]["filter"]["note"]
              and cat["drafting"]["lens"]["road"] == "session")

        api.filter_run(db, man, {}, "own-pass", "a.md")
        last = {f["name"]: f for f in runs_page.catalog(
            db, man, config={})["filters"]}["own-pass"]
        check("after a run starts, the filter row shows it",
              last["runs"] == 1 and last["last"]["file"] == "a.md"
              and last["last"]["status"] == "active", last)

        (ms / "_filters" / "broken.md").write_text("no front matter\n")
        broken = {f["name"]: f for f in runs_page.catalog(
            db, man, config={})["filters"]}["broken"]
        check("a filter that does not parse is listed with its error",
              broken["error"] and "front matter" in broken["error"], broken)
        shown = runs_page.show(man, "filter", "broken")
        check("and its text can still be opened, to be mended",
              shown["text"] == "no front matter\n" and shown["error"])


def check_show_and_save() -> None:
    with _world() as (root, ws, ms, db, man):
        own = ms / "_filters" / "own-pass.md"
        shown = runs_page.show(man, "filter", "own-pass")
        check("show returns the whole file, front matter included",
              shown["text"] == _filter("Own pass")
              and shown["path"] == str(own) and shown["error"] is None)

        edited = _filter("Own pass, sharpened")
        out = runs_page.save(db, man, "filter", "own-pass", edited,
                             shown["sha"])
        check("save writes the edited text to the same file",
              own.read_text() == edited and out["changed"], out)
        backup = Path(out["backup"])
        check("the replaced text is copied to the workspace's backups, "
              "outside the manuscript",
              backup.read_text() == _filter("Own pass")
              and Path(db.path).parent / "prompt-backups" in backup.parents
              and ms not in backup.parents, backup)

        try:
            runs_page.save(db, man, "filter", "own-pass",
                           '---\nclass = "whole-file"\n---\n\n# X\n',
                           out["sha"])
            check("a text whose front matter does not parse is declined",
                  False)
        except ValueError as err:
            check("a text whose front matter does not parse is declined, "
                  "naming the legal classes",
                  "sequential" in str(err), err)
        check("and the file is left as it was", own.read_text() == edited)

        try:
            runs_page.save(db, man, "filter", "own-pass",
                           _filter("From a stale page"), shown["sha"])
            check("a save from a stale read is declined", False)
        except ValueError as err:
            check("a save against text that changed on disk is declined",
                  "changed on disk" in str(err), err)
        check("and the newer text is kept", own.read_text() == edited)

        for bad in ("../../etc/passwd", "a/b", "..", "Own_Pass", ""):
            try:
                runs_page.save(db, man, "filter", bad, _filter("Evil"), None)
                check(f"name {bad!r} is declined", False)
            except ValueError:
                check(f"name {bad!r} is declined", True)
        check("nothing was written outside the filter folders",
              sorted(p.name for p in root.rglob("*.md")
                     if "Evil" in p.read_text()) == [])

        outside = root / "elsewhere"
        outside.mkdir()
        (outside / "linked.md").write_text(_filter("Elsewhere"))
        (ms / "_filters" / "linked.md").symlink_to(outside / "linked.md")
        try:
            runs_page.save(db, man, "filter", "linked", _filter("Evil"),
                           None)
            check("a symlink leading out of the folder is declined", False)
        except ValueError as err:
            check("a symlink leading out of the folder is declined",
                  "outside" in str(err), err)
        check("and its target is untouched",
              (outside / "linked.md").read_text() == _filter("Elsewhere"))
        (ms / "_filters" / "linked.md").unlink()

        lib = root / "flib" / "shared-pass.md"
        lib_shown = runs_page.show(man, "filter", "shared-pass")
        runs_page.save(db, man, "filter", "shared-pass",
                       _filter("Shared pass, for this book", "global"),
                       lib_shown["sha"], to="manuscript")
        check("saving a library filter as the book's own leaves the "
              "library file alone",
              lib.read_text() == _filter("Shared pass", "global")
              and (ms / "_filters" / "shared-pass.md").read_text()
              == _filter("Shared pass, for this book", "global"))
        (ms / "_filters" / "shared-pass.md").unlink()
        out = runs_page.save(db, man, "filter", "shared-pass",
                             _filter("Shared pass, v2", "global"),
                             lib_shown["sha"])
        check("saving a library filter in place writes the library file",
              lib.read_text() == _filter("Shared pass, v2", "global")
              and out["source"] == "library"
              and "git" in out["recovery"], out)

        lens_shown = runs_page.show(man, "lens", "own-lens")
        runs_page.save(db, man, "lens", "own-lens",
                       "# Plain reader\n\nA sharper rule.\n",
                       lens_shown["sha"])
        check("a lens is saved to its own file",
              (ms / "_lenses" / "own-lens.md").read_text()
              == "# Plain reader\n\nA sharper rule.\n")
        try:
            runs_page.save(db, man, "lens", "own-lens",
                           '---\nclass = "book"\n---\n# X\n',
                           runs_page.show(man, "lens", "own-lens")["sha"])
            check("a lens with an unknown class is declined", False)
        except ValueError as err:
            check("a lens with an unknown class is declined",
                  "cross-chapter" in str(err), err)

        api.filter_run(db, man, {}, "own-pass", "a.md")
        out = runs_page.save(db, man, "filter", "own-pass",
                             _filter("Own pass, again"),
                             runs_page.show(man, "filter", "own-pass")["sha"])
        check("saving a filter with a live run says the run will read the "
              "new prompt", any("a.md" in w for w in out["warnings"]), out)


def _written_form(db, man, file: str, origin: str = "critique") -> None:
    row = ko_fields("dt")
    row.update(manuscript_id=man["id"], file=file, origin_type=origin,
               origin_id=f"x:{file}:1", state="written",
               proposed_old="a", proposed_new="b")
    cols = {r["name"] for r in db.all("PRAGMA table_info(doc_threads)")}
    db.insert("doc_threads", {k: v for k, v in row.items() if k in cols})


def check_queue() -> None:
    with _world() as (_root, _ws, ms, db, man):
        items = [{"kind": "filter", "name": "own-pass"},
                 {"kind": "lens", "name": "own-lens"}]
        before = _tables(db)
        preview = runs_page.enqueue(db, man, items, ["a.md", "b.md"],
                                    dry_run=True)
        check("a preview names the four runs and writes nothing",
              len(preview["queued"]) == 4 and _tables(db) == before, preview)

        out = runs_page.enqueue(db, man, items, ["a.md", "b.md"])
        check("asking for 2 artifacts on 2 tabs writes 4 requests",
              len(out["queued"]) == 4 and not out["blocked"]
              and len(db.all("SELECT * FROM run_requests")) == 4, out)
        check("asking writes nothing but the requests: no filter run, no "
              "thread, no finding",
              all(_tables(db)[t] == before[t] for t in before
                  if t != "run_requests"))
        check("each request waits as 'queued' and carries its verbs",
              all(r["state"] == "queued" for r in out["requests"])
              and any("filter apply own-pass a.md -m book" in c
                      for r in out["requests"] for c in r["commands"])
              and any("lens register own-lens b.md" in c
                      for r in out["requests"] for c in r["commands"]),
              out["requests"])

        again = runs_page.enqueue(db, man, items[:1], ["a.md"])
        check("asking again for a waiting run adds no second request",
              again["already"] == [{"kind": "filter", "name": "own-pass",
                                    "file": "a.md"}]
              and len(db.all("SELECT * FROM run_requests")) == 4, again)

        shared = runs_page.enqueue(
            db, man, [{"kind": "filter", "name": "shared-pass"}], ["a.md"])
        check("a global filter's request starts with its prelude",
              shared["requests"][-1]["commands"][0]
              == "authorlm filter prelude shared-pass a.md -m book",
              shared["requests"][-1])

        # The session's side: the run's own rows move the request's state.
        api.filter_run(db, man, {}, "own-pass", "a.md")
        state = {(r["name"], r["file"]): r
                 for r in runs_page.requests(db, man)}
        check("once a session starts the run, the request reads 'drafting' "
              "with the units recorded",
              state[("own-pass", "a.md")]["state"] == "drafting"
              and "0 of 3" in state[("own-pass", "a.md")]["detail"]
              and state[("own-pass", "b.md")]["state"] == "queued", state)
        from authorlm.passes import echo_of, paragraphs_of
        units = paragraphs_of(ESSAY_A)
        reply = json.dumps({"units": [
            {"n": 1, "echo": echo_of(units[0]), "action": "keep"},
            {"n": 2, "echo": echo_of(units[1]), "action": "replace",
             "new": "First unit of alpha, tightened.", "why": "tighter"},
            {"n": 3, "echo": echo_of(units[2]), "action": "keep"}],
            "state": "ledger"})
        try:
            api.filter_record(db, man, {}, "a.md", reply, name="own-pass")
            recorded = True
        except ValueError as err:           # the reply contract moved
            recorded, reply_err = False, err
        check("the fixture reply is one the harness records", recorded,
              "" if recorded else reply_err)
        state = {(r["name"], r["file"]): r
                 for r in runs_page.requests(db, man)}
        check("once the reply is recorded, the request reads 'staged' with "
              "the number of proposals",
              state[("own-pass", "a.md")]["state"] == "staged"
              and "1 proposal" in state[("own-pass", "a.md")]["detail"],
              state[("own-pass", "a.md")])

        victim = next(r for r in runs_page.requests(db, man)
                      if (r["name"], r["file"]) == ("own-lens", "b.md"))
        runs_page.cancel(db, man, victim["id"])
        check("a cancelled request leaves the list and keeps its row",
              victim["id"] not in [r["id"]
                                   for r in runs_page.requests(db, man)]
              and db.one("SELECT status FROM run_requests WHERE id = ?",
                         (victim["id"],))["status"] == "cancelled")


def check_blocked() -> None:
    with _world() as (_root, _ws, ms, db, man):
        both = [{"kind": "filter", "name": "own-pass"},
                {"kind": "lens", "name": "own-lens"}]
        _written_form(db, man, "a.md")
        out = runs_page.enqueue(db, man, both, ["a.md", "b.md"])
        check("a tab with forms out takes no new run of either kind; the "
              "other tab's are queued",
              sorted((b["kind"], b["file"]) for b in out["blocked"])
              == [("filter", "a.md"), ("lens", "a.md")]
              and sorted((q["kind"], q["file"]) for q in out["queued"])
              == [("filter", "b.md"), ("lens", "b.md")], out)
        check("the reason names the forms and where to settle them",
              "1 form(s) are already in this tab" in out["blocked"][0]
              ["reason"] and "critique" in out["blocked"][0]["reason"]
              and "Merge forms" in out["blocked"][0]["reason"],
              out["blocked"][0])
        tab = {t["file"]: t for t in runs_page.catalog(
            db, man, config={})["tabs"]}
        check("the tab list shows the same reason before anything is asked",
              tab["a.md"]["blocked"] == out["blocked"][0]["reason"]
              and tab["b.md"]["blocked"] is None)

    with _world() as (_root, _ws, ms, db, man):
        saved = gdocs.doc_status
        gdocs.doc_status = lambda _db, _m: {"b.md": {"checked_out": True}}
        try:
            out = runs_page.enqueue(
                db, man, [{"kind": "filter", "name": "own-pass"}],
                ["a.md", "b.md"])
            tab = {t["file"]: t for t in runs_page.catalog(
                db, man, config={})["tabs"]}
        finally:
            gdocs.doc_status = saved
        by_file = {r["file"]: r for r in out["requests"]}
        check("a tab checked out to the Doc is queued with the pull as its "
              "first verb; the other tab's request has no pull",
              out["blocked"] == []
              and by_file["b.md"]["commands"][0]
              == "authorlm doc pull b.md -m book"
              and "pulled first" in by_file["b.md"]["detail"]
              and not any("doc pull" in c
                          for c in by_file["a.md"]["commands"]), out)
        check("the tab list says what the checked-out tab needs",
              "doc pull b.md" in tab["b.md"]["needs"]
              and tab["b.md"]["blocked"] is None
              and tab["a.md"]["needs"] is None, tab)

        (ms / "_filters" / "broken.md").write_text("no front matter\n")
        out = runs_page.enqueue(
            db, man, [{"kind": "filter", "name": "broken"}], ["a.md"])
        check("a filter that does not parse is not queued",
              out["queued"] == [] and "does not parse"
              in out["blocked"][0]["reason"], out)

        for items, files in (([], ["a.md"]),
                             ([{"kind": "filter", "name": "own-pass"}], [])):
            try:
                runs_page.enqueue(db, man, items, files)
                check("an empty choice is declined", False)
            except ValueError:
                check("an empty choice is declined", True)
        for items, files, label in (
                ([{"kind": "filter", "name": "no-such"}], ["a.md"],
                 "an unknown filter"),
                ([{"kind": "filter", "name": "own-pass"}], ["zzz.md"],
                 "an unknown tab")):
            try:
                runs_page.enqueue(db, man, items, files)
                check(f"{label} is declined", False)
            except LookupError:
                check(f"{label} is declined", True)


def check_server_and_cli() -> None:
    with _world() as (_root, ws, ms, db, man):
        from authorlm import audiobook
        saved = dict(audiobook._SESSIONS)
        audiobook._SESSIONS.clear()
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), audiobook_server.make_handler(str(ws), "book"))
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def post(method: str, params: dict) -> dict:
            req = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/runs",
                data=json.dumps({"method": method,
                                 "params": params}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read())["result"]
        try:
            cat = post("catalog", {})
            check("POST /api/runs catalog answers for the server's "
                  "manuscript",
                  cat["manuscript"] == "book"
                  and [f["name"] for f in cat["filters"]]
                  == ["own-pass", "shared-pass"], cat.get("error"))
            shown = post("show", {"kind": "lens", "name": "own-lens"})
            check("show over HTTP returns the file's text",
                  shown["text"] == LENS)
            bad = post("save", {"kind": "filter", "name": "own-pass",
                                "text": "nothing", "seen": "x"})
            check("a declined save over HTTP is an error message, and the "
                  "file is unchanged",
                  "front matter" in bad["error"]
                  and (ms / "_filters" / "own-pass.md").read_text()
                  == _filter("Own pass"), bad)
            out = post("queue", {"items": [{"kind": "filter",
                                            "name": "own-pass"}],
                                 "files": ["a.md"]})
            check("queue over HTTP writes the request",
                  len(out["queued"]) == 1 and out["requests"][0]["state"]
                  == "queued", out)
            check("an unknown method is an error, not a crash",
                  "unknown method" in post("nope", {})["error"])
        finally:
            server.shutdown()
            server.server_close()
            audiobook._SESSIONS.clear()
            audiobook._SESSIONS.update(saved)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_main(["--workspace", str(ws), "requests", "list"])
        text = buf.getvalue()
        check("`authorlm requests` lists the waiting request with the verbs "
              "that carry it out",
              "1 run request(s) waiting" in text
              and "filter own-pass on a.md: queued" in text
              and "authorlm filter run own-pass a.md -m book" in text, text)
        rid = runs_page.requests(db, man)[0]["id"]
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "requests", "cancel", rid])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_main(["--workspace", str(ws), "requests"])
        check("`authorlm requests cancel` takes it off the list",
              "0 run request(s) waiting" in buf.getvalue(), buf.getvalue())


def main() -> None:
    for t in (check_catalog, check_show_and_save, check_queue,
              check_blocked, check_server_and_cli):
        print(f"{t.__name__}:")
        t()
    print(f"ALL CHECKS PASSED ({PASSED})")


if __name__ == "__main__":
    main()
