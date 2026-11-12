"""One watcher for the author's two review pages: Dev edit and Merge forms.

Emit one line per author action, and a COMPLETE line when a batch is done,
so an agent running this under a Monitor can act without waiting for chat.

    python3 -u authorlm/tools/author_watch.py --run <dev edit run folder>
    python3 -u authorlm/tools/author_watch.py --manuscript SMSTTD
    python3 -u authorlm/tools/author_watch.py --run <folder> --manuscript SMSTTD

Dev edit (`--run`), read from the run folder, the same lines as
devedit_watch.py:
    DECISION stage <st> #<n>: <answer>
    COMPLETE decisions stage <st>
    VERDICT <id>: <verdict>[ — reason: …][ — with the author's edited text …]
    COMPLETE items <file>

Merge forms (`--manuscript`), read from the database (the rulings, held on
each open outside form's row) and from the page server's merge queue
(`/api/forms`, method `queue`; the queue lives in the server's memory):
    FORMS STAGED <file>: <n> new open form(s)
    RULING <file> <form id>: accept | edit | decline | cleared
    COMPLETE rulings <file>          every open form in the file is ruled
    MERGING <file>
    MERGED <file>: <the server's message>
    MERGE STOPPED <file>: <why>
    FORMS SERVER unreachable: <error>   (once; and FORMS SERVER back)

The first pass only records what is already there; lines start with the
first change after it. Nothing here writes anywhere."""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

RULING_KEY = "page_ruling"          # forms_page.RULING_KEY
EXTERNAL = "external"               # api.EXTERNAL_ORIGIN


def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001 — a file mid-write reads again next pass
        return default


# ---------------------------------------------------------------- dev edit

class DevEditWatch:
    def __init__(self, run: Path):
        self.run = run
        self.seen_dec: dict = {}
        self.seen_ver: dict = {}
        self.done_files: set = set()
        self.done_stages: set = set()

    def poll(self, first: bool) -> list[str]:
        out: list[str] = []
        for d in sorted(self.run.glob("stages/*/decisions.json")):
            st = d.parent.name
            rows = _load(d, [])
            for r in rows:
                k = (st, r["n"])
                if r.get("answered") and self.seen_dec.get(k) != r["answered"]:
                    if not first:
                        out.append(f"DECISION stage {st} #{r['n']}: {r.get('answer')}")
                    self.seen_dec[k] = r["answered"]
            if rows and all(r.get("answered") for r in rows) \
                    and st not in self.done_stages:
                self.done_stages.add(st)
                out.append(f"COMPLETE decisions stage {st}")
        by: dict[str, list] = {}
        for i in _load(self.run / "queue.json", []):
            by.setdefault(i["file"], []).append(i)
            if i.get("verdict") and self.seen_ver.get(i["id"]) != i["verdict"]:
                if not first:
                    extra = ""
                    if i.get("reason_given"):
                        extra += f" — reason: {i['reason_given']}"
                    if i.get("author_text"):
                        extra += (" — with the author's edited text "
                                  "(author_text in queue.json)")
                    out.append(f"VERDICT {i['id']}: {i['verdict']}{extra}")
                self.seen_ver[i["id"]] = i["verdict"]
        for f, items in by.items():
            if all(i.get("verdict") and i["verdict"] != "defer" for i in items):
                if f not in self.done_files:
                    self.done_files.add(f)
                    if not first:
                        out.append(f"COMPLETE items {f}")
            else:
                self.done_files.discard(f)
        return out


# ------------------------------------------------------------- merge forms

def forms_snapshot(rows) -> dict[str, dict[str, str | None]]:
    """{file: {form id: verdict or None}} for the open outside forms, from
    doc_threads rows (id, file, metadata)."""
    snap: dict[str, dict[str, str | None]] = {}
    for r in rows:
        try:
            meta = json.loads(r["metadata"] or "{}")
        except ValueError:
            meta = {}
        ruling = meta.get(RULING_KEY) or {}
        snap.setdefault(r["file"], {})[r["id"]] = ruling.get("verdict")
    return snap


def forms_lines(prev: dict, cur: dict) -> list[str]:
    out: list[str] = []
    for file in sorted(cur):
        before, now = prev.get(file, {}), cur[file]
        new = [i for i in now if i not in before]
        if new:
            out.append(f"FORMS STAGED {file}: {len(new)} new open form(s)")
        for fid, verdict in now.items():
            if fid in before and before[fid] != verdict:
                out.append(f"RULING {file} {fid}: {verdict or 'cleared'}")
        was_done = bool(before) and all(before.values())
        if now and all(now.values()) and not was_done:
            out.append(f"COMPLETE rulings {file}")
    return out


def queue_lines(prev: dict, cur: dict) -> list[str]:
    out: list[str] = []
    if cur.get("running") and cur["running"] != prev.get("running"):
        out.append(f"MERGING {cur['running']}")
    old = prev.get("results") or {}
    for file, r in sorted((cur.get("results") or {}).items()):
        if old.get(file) == r:
            continue
        if r.get("stopped"):
            out.append(f"MERGE STOPPED {file}: {r.get('stopped')} — "
                       f"{r.get('message') or ''}".rstrip(" —"))
        else:
            out.append(f"MERGED {file}: {r.get('message') or ''}")
    return out


class FormsWatch:
    def __init__(self, manuscript: str, port: int, workspace: str | None,
                 db=None, fetch_queue=None):
        self.manuscript = manuscript
        self.url = f"http://127.0.0.1:{port}/api/forms"
        self.workspace = workspace
        self._db = db
        self._fetch_queue = fetch_queue or self._http_queue
        self.forms: dict = {}
        self.queue: dict = {}
        self.server_down = False
        self._mid: str | None = None

    def db(self):
        if self._db is None:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from authorlm import api
            self._db = api.open_db(self.workspace)
        return self._db

    def mid(self) -> str:
        if self._mid is None:
            row = self.db().one("SELECT id FROM manuscripts WHERE name = ?",
                                (self.manuscript,))
            if row is None:
                raise SystemExit(f"no manuscript named {self.manuscript!r}")
            self._mid = row["id"]
        return self._mid

    def _http_queue(self) -> dict:
        body = json.dumps({"method": "queue",
                           "params": {"manuscript": self.manuscript}}).encode()
        req = urllib.request.Request(self.url, data=body, headers={
            "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp).get("result") or {}

    def poll(self, first: bool) -> list[str]:
        rows = self.db().all(
            "SELECT id, file, metadata FROM doc_threads WHERE manuscript_id = ? "
            "AND origin_type = ? AND state = 'written'", (self.mid(), EXTERNAL))
        snap = forms_snapshot(rows)
        out = [] if first else forms_lines(self.forms, snap)
        self.forms = snap
        try:
            q = self._fetch_queue()
        except Exception as err:  # noqa: BLE001 — the server may be restarting
            if not self.server_down:
                self.server_down = True
                out.append(f"FORMS SERVER unreachable: {err}")
            return out
        if self.server_down:
            self.server_down = False
            out.append("FORMS SERVER back")
        if not first:
            out += queue_lines(self.queue, q)
        self.queue = q
        return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", help="the dev edit run folder to watch")
    ap.add_argument("--manuscript", "-m", help="watch Merge forms for this manuscript")
    ap.add_argument("--port", type=int, default=8792,
                    help="the page server's local port (default 8792, the live one)")
    ap.add_argument("--workspace", "-w", default=None)
    ap.add_argument("--interval", type=float, default=2.0)
    args = ap.parse_args(argv)
    watches: list = []
    if args.run:
        watches.append(DevEditWatch(Path(args.run)))
    if args.manuscript:
        watches.append(FormsWatch(args.manuscript, args.port, args.workspace))
    if not watches:
        ap.error("name --run, --manuscript, or both")
    first = True
    while True:
        for w in watches:
            for line in w.poll(first):
                print(line, flush=True)
        first = False
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
