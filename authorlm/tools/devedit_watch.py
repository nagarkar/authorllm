"""Emit one line per author action on the dev edit page, and a COMPLETE line
when a batch is done: every decision of a stage answered, or every queued
item of a file decided."""
import json, sys, time
from pathlib import Path
run = Path(sys.argv[1])
def load(p, d):
    try: return json.loads(p.read_text())
    except Exception: return d
seen_dec, seen_ver, done_files, done_stages = {}, {}, set(), set()
first = True
while True:
    for d in sorted(run.glob("stages/*/decisions.json")):
        st = d.parent.name; rows = load(d, [])
        for r in rows:
            k = (st, r["n"])
            if r.get("answered") and seen_dec.get(k) != r["answered"]:
                if not first: print(f"DECISION stage {st} #{r['n']}: {r.get('answer')}", flush=True)
                seen_dec[k] = r["answered"]
        if rows and all(r.get("answered") for r in rows) and st not in done_stages:
            done_stages.add(st)
            print(f"COMPLETE decisions stage {st}", flush=True)
    q = load(run / "queue.json", [])
    by = {}
    for i in q:
        by.setdefault(i["file"], []).append(i)
        if i.get("verdict") and seen_ver.get(i["id"]) != i["verdict"]:
            if not first:
                extra = ""
                if i.get("reason_given"):
                    extra += f" — reason: {i['reason_given']}"
                if i.get("author_text"):
                    extra += " — with the author's edited text (author_text in queue.json)"
                print(f"VERDICT {i['id']}: {i['verdict']}{extra}", flush=True)
            seen_ver[i["id"]] = i["verdict"]
    for f, items in by.items():
        if all(i.get("verdict") and i["verdict"] != "defer" for i in items):
            if f not in done_files:
                done_files.add(f)
                if not first: print(f"COMPLETE items {f}", flush=True)
        else:
            done_files.discard(f)
    first = False
    time.sleep(2)

