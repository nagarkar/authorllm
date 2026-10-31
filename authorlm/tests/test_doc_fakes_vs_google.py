"""The Doc-road fake checked against Google: every snapshot recorded
live by tests/doc_road_live.py (a full documents.get and the markdown
export of the same revision) is rebuilt in `_GoogleFake` from the
recorded tab content, exported by the fake, and compared, tab by tab,
with what Google exported. A difference means the fake no longer renders
the way Google does: re-record (`python3 tests/doc_road_live.py
--record`) and bring `_GoogleFake` back in line.

Also checks the recorded masked reads carry every field the code reads,
and that every recorded write came back with a new revision.

Reads tests/doc_cache/ only while tests/doc_cache/fake.log is under 7
days old; otherwise it re-records live first (see _doc_freshness.py).
Run: python3 tests/test_doc_fakes_vs_google.py
"""
from __future__ import annotations

import difflib
import gzip
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from _doc_freshness import ensure_fresh  # noqa: E402
from test_doc_road_verbs import _GoogleFake  # noqa: E402

from authorlm import gdocs  # noqa: E402

CACHE = HERE / "doc_cache"
PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


def recordings() -> dict[str, list[dict]]:
    out = {}
    for p in sorted(CACHE.glob("*.json.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            out[p.name.removesuffix(".json.gz")] = json.load(f)
    return out


def snapshots(calls: list[dict]):
    """(label, full documents.get, export) for each recorded snapshot."""
    by: dict[str, dict] = {}
    for c in calls:
        label = c["scenario"]
        if ":snapshot:" not in label or "response" not in c:
            continue
        slot = by.setdefault(label, {})
        if "/export" in c["uri"]:
            slot["export"] = c["response"]
        elif ("includeTabsContent=true" in c["uri"]
              and "fields=" not in c["uri"] and c["method"] == "GET"):
            slot["doc"] = c["response"]
    for label, slot in by.items():
        if "doc" in slot and "export" in slot:
            yield label, slot["doc"], slot["export"]


def walk(tabs):
    for t in tabs:
        yield t
        yield from walk(t.get("childTabs", []))


def rebuild(doc: dict) -> _GoogleFake:
    """The recorded tabs as the fake's own state: text, emphasis and
    strike runs, and each paragraph's spacing."""
    tabs = [t for t in walk(doc.get("tabs", []))]
    fake = _GoogleFake([(t["tabProperties"]["title"], "") for t in tabs])
    fake.styled = []
    for mine, live in zip(fake.tabs, tabs):
        body, flags, levels = "", [], []
        for item in live.get("documentTab", {}).get("body", {}).get(
                "content", []):
            para = item.get("paragraph")
            if not para:
                continue
            style = para.get("paragraphStyle", {})
            named = style.get("namedStyleType", "NORMAL_TEXT")
            level = int(named[-1]) if named.startswith("HEADING_") else 0
            spaced = gdocs._spaced(style, gdocs.DOC_SPACING)
            for e in para.get("elements", []):
                run = e.get("textRun")
                if not run:
                    continue
                text = run.get("content", "")
                ts = run.get("textStyle", {})
                start = len(body) + 1
                core = text.rstrip("\n")
                for key, mark in (("bold", "**"), ("italic", "*"),
                                  ("strikethrough", "~~")):
                    if not (ts.get(key) and core.strip()):
                        continue
                    # Runs split by colour alone are one span to the
                    # export: continue the open range of this mark.
                    last = next((r for r in reversed(fake.styled)
                                 if r["tab"] == mine["id"]
                                 and r["mark"] == mark), None)
                    if last is not None and last["end"] == start:
                        last["end"] = start + len(core)
                    else:
                        fake.styled.append({"tab": mine["id"],
                                            "start": start,
                                            "end": start + len(core),
                                            "mark": mark})
                body += text
                flags += [spaced or named != "NORMAL_TEXT"] * len(text)
                levels += [level] * len(text)
        mine["body"] = body
        fake.sp[mine["id"]] = flags
        fake.hd[mine["id"]] = levels
    return fake, tabs


def plain_state(doc: dict) -> dict[str, tuple[str, list[bool]]]:
    """Each tab's text and per-character paragraph spacing (on the
    paragraph's characters), from a full or masked read."""
    out = {}
    for t in walk(doc.get("tabs", [])):
        body, flags = "", []
        for item in t.get("documentTab", {}).get("body", {}).get(
                "content", []):
            para = item.get("paragraph")
            if not para:
                continue
            style = para.get("paragraphStyle", {})
            spaced = gdocs._spaced(style, gdocs.DOC_SPACING)
            for e in para.get("elements", []):
                text = e.get("textRun", {}).get("content", "")
                body += text
                flags += [spaced] * len(text)
        out[t["tabProperties"]["tabId"]] = (body, flags)
    return out


def replay(recs) -> tuple[int, list[str]]:
    """Start the fake from each read, apply the writes Google answered
    next, and compare with Google's read at the revision they left.
    Spacing is compared on NORMAL_TEXT paragraphs' newlines only."""
    replayed, wrong = 0, []
    for name, calls in recs.items():
        docs = [(i, c["response"]) for i, c in enumerate(calls)
                if isinstance(c.get("response"), dict)
                and "tabs" in c["response"]
                and c["response"].get("revisionId")]
        for i, c in enumerate(calls):
            if ":batchUpdate" not in c["uri"] or not isinstance(
                    c.get("response"), dict):
                continue
            after = c["response"]["writeControl"]["requiredRevisionId"]
            before = [d for j, d in docs if j < i]
            nxt = [d for j, d in docs if j > i and d["revisionId"] == after]
            if not before or not nxt:
                continue
            # The read just before must be the state the write met.
            prior = before[-1]
            writes_between = [x for x in calls[[j for j, d in docs
                                                 if d is prior][0] + 1:i]
                              if ":batchUpdate" in x["uri"]]
            if writes_between:
                continue
            state = plain_state(prior)
            fake = _GoogleFake([("x", "")])
            fake.tabs, fake.sp, fake.styled = [], {}, []
            fake.hd = {}
            for tid, (body, flags) in state.items():
                fake.tabs.append({"id": tid, "title": tid, "body": body})
                fake.sp[tid] = list(flags)
            touched = set()
            for req in (c.get("body") or {}).get("requests", []):
                spec = next(iter(req.values()))
                rng = spec.get("range") or spec.get("location") or {}
                if any(k in req for k in ("addDocumentTab", "deleteTab",
                                          "updateDocumentTabProperties",
                                          "createParagraphBullets",
                                          "insertTable")):
                    touched = None
                    break
                touched.add(rng.get("tabId"))
                fake._apply(req)
            if touched is None:
                continue
            want = plain_state(nxt[0])
            replayed += 1
            for tid in touched:
                tab = fake._tab(tid)
                body, flags = want.get(tid, ("", []))
                if tab["body"] != body:
                    wrong.append(f"[{name} #{i}] {tid} text differs")
                    continue
                mine = fake._flags(tab)
                for n, ch in enumerate(body):
                    if ch == "\n" and mine[n] != flags[n]:
                        wrong.append(f"[{name} #{i}] {tid} spacing of the "
                                     f"paragraph ending at {n + 1}: google "
                                     f"{flags[n]}, fake {mine[n]}")
                        break
    return replayed, wrong


def sections(export: str, doc: dict, titles: list[str]) -> dict[str, str]:
    links = {t: {"tab_id": t} for t in titles}
    tab_props: list = []
    gdocs.walk_tabs(doc.get("tabs", []), tab_props, [])
    order = [t for _, t in tab_props if t]
    return gdocs.split_tabbed_export(
        gdocs.unescape_export_math(export), set(order) | set(links),
        order=order, anchors=gdocs.tab_anchors(doc))


def main_test() -> None:
    # First, how old the recordings are (tests/_doc_freshness.py): a log
    # under 7 days old costs nothing; an old or missing one re-records
    # live, or fails if the fixture Doc shows a recent recording.
    print("0. the recordings are fresh:")
    print(f"   {ensure_fresh()}")
    recs = recordings()
    check("there are recordings to check against", bool(recs),
          "run: python3 tests/doc_road_live.py --record")
    print("A. the fake's export renders each recorded tab as Google did:")
    diverged: list[str] = []
    compared = 0
    for name, calls in recs.items():
        for label, doc, export in snapshots(calls):
            fake, tabs = rebuild(doc)
            titles = [t["tabProperties"]["title"] for t in tabs
                      if t["tabProperties"]["title"].endswith(".md")]
            live = sections(export, doc, titles)
            mine = sections(fake.export_text(), doc, titles)
            for title in titles:
                compared += 1
                if live.get(title) != mine.get(title):
                    diverged.append(f"[{label}] {title}\n" + "".join(
                        difflib.unified_diff(
                            (live.get(title) or "").splitlines(True),
                            (mine.get(title) or "").splitlines(True),
                            "google", "fake")))
    print(f"   {compared} tab sections compared")
    check("no tab section differs between Google's export and the fake's",
          compared and not diverged, "\n".join(diverged[:6]))

    print("B. the fake applies each recorded write as Google did:")
    replayed, wrong = replay(recs)
    print(f"   {replayed} writes replayed")
    check("text and paragraph spacing after each write match Google's "
          "next read at that revision", replayed and not wrong,
          "\n".join(wrong[:6]))

    print("C. masked reads and writes:")
    masked = 0
    for calls in recs.values():
        for c in calls:
            r = c.get("response")
            if (isinstance(r, dict) and "tabs" in r
                    and (c["method"] == "POST" or "fields=" in c["uri"])):
                masked += 1
                for t in walk(r["tabs"]):
                    props = t.get("tabProperties", {})
                    assert props.get("tabId") and "title" in props, props
                    for item in t.get("documentTab", {}).get(
                            "body", {}).get("content", []):
                        assert "endIndex" in item, item
    check(f"every masked read ({masked}) kept tab ids, titles and each "
          "element's end index", masked > 0)
    writes = [c for calls in recs.values() for c in calls
              if ":batchUpdate" in c["uri"] and isinstance(
                  c.get("response"), dict)]
    check(f"every write ({len(writes)}) answered with a new revision",
          writes and all(
              (w["response"].get("writeControl") or {}).get(
                  "requiredRevisionId")
              and (w["response"]["writeControl"]["requiredRevisionId"]
                   != ((w.get("body") or {}).get("writeControl") or {}).get(
                       "requiredRevisionId"))
              for w in writes))
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    main_test()
