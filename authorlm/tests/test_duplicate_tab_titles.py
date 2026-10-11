"""Duplicate Doc tab titles must not last-win over a mapped essay.

Drive exports every tab as `# **<title>**`. When two tabs share a title,
`split_tabbed_export` resets the same dict key — the later tab's body
wins. `classify_tabs` already reported the hand-made twin as ambiguous,
but pull/reconcile still wrote that later body onto the mapped file.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import api
from authorlm.cli import main as cli_main
from authorlm.db import loads
from authorlm.gdocs import (
    duplicate_tab_titles,
    normalize_markdown,
    pull_doc,
    split_tabbed_export,
)


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_duplicate_tab_titles_helper() -> None:
    print("duplicate_tab_titles:")
    check("empty order is empty",
          duplicate_tab_titles([]) == frozenset()
          and duplicate_tab_titles(None) == frozenset())
    check("unique titles are empty",
          duplicate_tab_titles(["a.md", "b.md"]) == frozenset())
    check("repeated title is reported",
          duplicate_tab_titles(["a.md", "b.md", "a.md"])
          == frozenset({"a.md"}))


def test_split_last_wins_on_duplicate_title() -> None:
    print("split_tabbed_export last-wins:")
    whole = (
        "# **essay.md**\nREAL essay body.\n"
        "# **notes.md**\nnotes body\n"
        "# **essay.md**\nTRASH from duplicate tab\n"
    )
    order = ["essay.md", "notes.md", "essay.md"]
    sections = split_tabbed_export(
        whole, {"essay.md", "notes.md"}, order=order)
    check("later twin replaces the mapped essay section",
          "TRASH" in sections["essay.md"]
          and "REAL" not in sections["essay.md"],
          sections["essay.md"])
    check("unique neighbor is intact",
          "notes body" in sections["notes.md"], sections["notes.md"])


def test_pull_doc_leaves_local_when_title_duplicated() -> None:
    """End-to-end: push a real essay, inject a same-titled twin tab with
    different body, pull — local must keep the real text and report
    duplicate_tabs."""
    print("pull_doc refuses duplicate-title overwrite:")

    class FakeRequest:
        def __init__(self, payload):
            self._payload = payload

        def execute(self):
            return self._payload

    class FakeGoogle:
        def __init__(self):
            self.state = {"counter": 0, "tab_counter": 0, "docs": {},
                          "uploads": {}, "folders": [], "comments": {},
                          "reply_counter": 0}

        def files(self):
            return self

        def documents(self):
            return self

        def comments(self):
            return self

        def replies(self):
            return self

        def create(self, body=None, media_body=None, fields=None):
            from authorlm.gdocs import FOLDER_MIME
            self.state["counter"] += 1
            fid = f"doc-{self.state['counter']}"
            if media_body is not None:
                self.state["uploads"][fid] = media_body.getbytes(
                    0, media_body.size()).decode("utf-8", "replace")
            elif (body or {}).get("mimeType") == FOLDER_MIME:
                self.state["folders"].append(fid)
            else:
                self.state["tab_counter"] += 1
                self.state["docs"][fid] = [
                    {"id": f"tab-{self.state['tab_counter']}",
                     "title": "Tab 1", "text": ""}]
            return FakeRequest({"id": fid})

        def update(self, fileId=None, media_body=None):
            self.state["uploads"][fileId] = media_body.getbytes(
                0, media_body.size()).decode("utf-8", "replace")
            return FakeRequest({})

        def delete(self, fileId=None):
            self.state["uploads"].pop(fileId, None)
            return FakeRequest({})

        def export(self, fileId=None, mimeType=None):
            flat = self.state["docs"][fileId]

            def dfs(parent_id):
                ordered = []
                for t in flat:
                    if t.get("parent") == parent_id:
                        ordered.append(t)
                        ordered.extend(dfs(t["id"]))
                return ordered

            def as_markdown(text):
                paras = [ln for ln in text.split("\n") if ln.strip()]
                return "\n\n".join(paras) + ("\n" if paras else "")

            whole = "\n".join(
                f"# **{t['title']}**\n\n{as_markdown(t['text'])}"
                for t in dfs(None))
            return FakeRequest(whole.encode("utf-8"))

        def get(self, documentId=None, includeTabsContent=None):
            if documentId in self.state["uploads"]:
                content = [{"paragraph": {"elements": [
                    {"textRun": {"content": line + "\n"}}]}}
                    for line in
                    self.state["uploads"][documentId].splitlines()]
                return FakeRequest({"body": {"content": content}})

            def paragraphs(text):
                items, pos = [], 1
                for line in text.split("\n"):
                    content = line + "\n"
                    items.append({
                        "startIndex": pos,
                        "endIndex": pos + len(content),
                        "paragraph": {"elements": [
                            {"startIndex": pos,
                             "endIndex": pos + len(content),
                             "textRun": {"content": content}}]}})
                    pos += len(content)
                return items

            flat = self.state["docs"][documentId]

            def node(t):
                return {"tabProperties": {"tabId": t["id"],
                                          "title": t["title"]},
                        "documentTab": {"body": {"content":
                            paragraphs(t["text"]) if t["text"] else []}},
                        "childTabs": [node(c) for c in flat
                                      if c.get("parent") == t["id"]]}

            return FakeRequest({"tabs": [node(t) for t in flat
                                         if not t.get("parent")]})

        def batchUpdate(self, documentId=None, body=None):
            tabs = self.state["docs"][documentId]
            replies = []
            for req in (body or {}).get("requests", []):
                if "addDocumentTab" in req:
                    self.state["tab_counter"] += 1
                    props = req["addDocumentTab"]["tabProperties"]
                    new = {"id": f"tab-{self.state['tab_counter']}",
                           "title": props["title"], "text": "",
                           "parent": props.get("parentTabId")}
                    tabs.append(new)
                    replies.append({"addDocumentTab": {
                        "tabProperties": {"tabId": new["id"]}}})
                    continue
                replies.append({})
                if "deleteContentRange" in req:
                    tid = req["deleteContentRange"]["range"]["tabId"]
                    next(t for t in tabs if t["id"] == tid)["text"] = ""
                elif "insertText" in req:
                    tid = req["insertText"]["location"]["tabId"]
                    target = next(t for t in tabs if t["id"] == tid)
                    loc = req["insertText"]["location"]
                    if "index" in loc:
                        i = loc["index"] - 1
                        target["text"] = (target["text"][:i]
                                          + req["insertText"]["text"]
                                          + target["text"][i:])
                    else:
                        target["text"] += req["insertText"]["text"]
            return FakeRequest({"replies": replies})

        def list(self, **kwargs):
            return FakeRequest({"comments": []})

        def create_reply(self, **kwargs):
            return FakeRequest({"id": "r-1"})

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        ms = ws / "ms"
        ms.mkdir()
        real = "# Essay\n\nREAL essay body the author spent years on.\n"
        (ms / "essay.md").write_text(real, encoding="utf-8")
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "essay.md"\n', encoding="utf-8")
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms), "--no-extract"])
        db = api.open_db(ws)
        manuscript = api.get_manuscript(db, "book")
        api.collect(db, manuscript, {})

        # Seed a master Doc with the real mapped tab plus a hand-made twin
        # of the same title (no push_doc — avoids googleapiclient).
        stub = FakeGoogle()
        stub.state["docs"]["doc-master"] = [
            {"id": "tab-container", "title": "book", "text": ""},
            {"id": "tab-real", "title": "essay.md",
             "text": real, "parent": "tab-container"},
            {"id": "tab-dup", "title": "essay.md",
             "text": "TRASH from duplicate tab\n",
             "parent": "tab-container"},
        ]
        import hashlib
        pushed_hash = hashlib.sha256(
            normalize_markdown(real).encode()).hexdigest()[:16]
        meta = loads(
            db.one("SELECT metadata FROM manuscripts WHERE id = ?",
                   (manuscript["id"],))["metadata"], {})
        meta["gdocs"] = {
            "_master_id": "doc-master",
            "_container_tab": "tab-container",
            "_folder_id": "folder-1",
            "essay.md": {"tab_id": "tab-real", "checked_out": False,
                         "pushed_hash": pushed_hash},
        }
        db.update("manuscripts", manuscript["id"],
                  {"metadata": json.dumps(meta)})
        manuscript = api.get_manuscript(db, "book")

        before = (ms / "essay.md").read_text(encoding="utf-8")
        report = pull_doc(db, manuscript, service=stub, docs_service=stub)
        after = (ms / "essay.md").read_text(encoding="utf-8")

        check("pull reports the duplicate title",
              "essay.md" in report.get("duplicate_tabs", []),
              report)
        check("pull does not write the later twin over local",
              after == before and "REAL" in after and "TRASH" not in after,
              after)
        check("essay is not listed as changed",
              "essay.md" not in report.get("changed", []),
              report)

        # Without the guard, split last-wins would have produced TRASH.
        whole = stub.files().export(fileId="doc-master").execute().decode()
        poisoned = split_tabbed_export(
            whole, {"essay.md"},
            order=["book", "essay.md", "essay.md"])["essay.md"]
        check("export split would have poisoned the essay (precondition)",
              "TRASH" in poisoned and "REAL" not in poisoned, poisoned)


if __name__ == "__main__":
    test_duplicate_tab_titles_helper()
    test_split_last_wins_on_duplicate_title()
    test_pull_doc_leaves_local_when_title_duplicated()
    print("all ok")
