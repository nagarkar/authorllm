"""Refuse planting producer forms over open margin <<old>>{{new}}.

When an author_comment thread leaves `<<P>>{{margin}}` on the tab
(state=`proposed`, invisible to `forms_pending`), leveling takes the
surgical path. `strip_pending` collapses the form to `P`, so leveling
reports ops=0 and preserves the margin form. Substring locate then
finds `P` inside the wrapper; planting nests (`<<<<…>>{{…}}>>{{…}}`);
read-back still passes; resolve writes residual markers into the essay.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from authorlm import api, gdocs, threads as th
from authorlm.cli import main as cli_main
from authorlm.db import ko_fields

PASSED = 0

P = "And so the wall stands, and the Dead do not pass."
ESSAY = "\n\n".join([
    "# The Wall",
    "Alpha opens.",
    P,
    "Gamma follows.",
]) + "\n"


def check(label: str, cond: bool, detail="") -> None:
    global PASSED
    if cond:
        PASSED += 1
        print(f"  OK  {label}")
    else:
        print(f"  FAIL  {label}: {detail}")
        raise SystemExit(1)


class _TabFake:
    """Minimal Docs double: live tab text + insertText / deleteContentRange."""

    def __init__(self, tabs):
        import re as _re
        self._re = _re
        self.tabs = [{
            "id": f"tab-{i}",
            "title": title,
            "body": "".join(
                p + "\n"
                for p in (x.strip() for x in _re.split(r"\n\s*\n", md)
                          if x.strip())),
        } for i, (title, md) in enumerate(tabs, 1)]

    def _tab(self, tab_id):
        return next(t for t in self.tabs if t["id"] == tab_id)

    def tab_text(self, title):
        return next(t["body"] for t in self.tabs if t["title"] == title)

    def _content(self, body):
        out, index = [], 1
        for seg in body.splitlines(keepends=True):
            end = index + len(seg)
            out.append({
                "startIndex": index, "endIndex": end,
                "paragraph": {
                    "elements": [{
                        "startIndex": index, "endIndex": end,
                        "textRun": {"content": seg, "textStyle": {}},
                    }],
                },
            })
            index = end
        return out

    def documents(self):
        outer = self

        class _Req:
            def __init__(self, result):
                self._result = result

            def execute(self):
                return self._result

        class _Documents:
            def get(self, documentId=None, includeTabsContent=None):
                tabs = [{
                    "tabProperties": {"tabId": t["id"], "title": t["title"]},
                    "documentTab": {
                        "body": {"content": outer._content(t["body"])},
                    },
                    "childTabs": [],
                } for t in outer.tabs]
                return _Req({"tabs": tabs, "body": {"content": []}})

            def batchUpdate(self, documentId=None, body=None):
                for req in (body or {}).get("requests", []):
                    if "insertText" in req:
                        spec = req["insertText"]
                        tab = outer._tab(spec["location"]["tabId"])
                        at = spec["location"]["index"] - 1
                        tab["body"] = (tab["body"][:at] + spec["text"]
                                       + tab["body"][at:])
                    elif "deleteContentRange" in req:
                        rng = req["deleteContentRange"]["range"]
                        tab = outer._tab(rng["tabId"])
                        tab["body"] = (
                            tab["body"][:rng["startIndex"] - 1]
                            + tab["body"][rng["endIndex"] - 1:])
                return _Req({"replies": []})

        return _Documents()

    def files(self):
        class _Req:
            def execute(self):
                return {}

        class _Files:
            def create(self, **kw):
                return _Req()

            def delete(self, **kw):
                return _Req()

            def export(self, **kw):
                return _Req()

        return _Files()


def _workspace(tmp: Path):
    ws = tmp / "ws"
    ms = ws / "book"
    ms.mkdir(parents=True)
    (ms / "solo.md").write_text(ESSAY)
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms)])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, {})
    return db, manuscript, ms


def _map_tab(db, manuscript, fake):
    meta = gdocs._mapping(db, manuscript)
    links = meta.setdefault("gdocs", {})
    links["_master_id"] = "doc-fake"
    links["_container_tab"] = "tab-1"
    links["solo.md"] = {
        "tab_id": "tab-2", "checked_out": True, "pushed_hash": "x",
    }
    gdocs._save_mapping(db, manuscript, meta)
    return fake


def main() -> None:
    print("leveling equality hole (why ops=0 preserves the margin form):")
    form = th.render_pending(P, "margin rewrite")
    stripped, _ = th.strip_pending(form)
    check("strip_pending(<<P>>{{margin}}) == P", stripped == P, repr(stripped))
    check("has_replacement sees the live margin form",
          th.has_replacement(form))

    print("write_pending_forms refuses when a margin form is already on the tab:")
    with tempfile.TemporaryDirectory() as tmp:
        db, manuscript, _ms = _workspace(Path(tmp))
        fake = _map_tab(db, manuscript, _TabFake([("book", ""), ("solo.md", ESSAY)]))
        fake._tab("tab-2")["body"] = fake._tab("tab-2")["body"].replace(
            P + "\n", form + "\n", 1)
        check("_tab_has_replace_forms is True after margin plant",
              gdocs._tab_has_replace_forms(fake, "doc-fake", "tab-2"))

        frow = ko_fields("dt")
        frow.update(
            manuscript_id=manuscript["id"], origin_type="filter",
            origin_id="filt:1", file="solo.md", anchor_quote=P,
            proposed_old=P, proposed_new="filt rewrite", note="filter",
            state="accepted", our_reply_ids="[]", last_author_reply_id=None,
            scope_kind="file", scope_ref="solo.md",
            metadata=json.dumps({"anchor_paragraph": 3, "kind": "replace"}),
        )
        db.insert("doc_threads", frow)
        filt = dict(db.one("SELECT * FROM doc_threads WHERE id = ?",
                           (frow["id"],)))

        # Without the guard, planting nests and read-back stays green.
        # Prove the pre-fix corruption path, then that the guard fires.
        nested_before = None
        with patch.object(gdocs, "push_doc",
                          return_value={"mode": "diff", "ops": 0}):
            # Temporarily clear the guard by planting the way tip did:
            # call locate+mark directly to show what would land.
            span = gdocs._locate_in_tab(fake, "doc-fake", "tab-2", P, 0)
            check("locate finds P inside the margin wrapper",
                  span is not None, str(span))
            # Simulate the unguarded plant on a copy of the body.
            body = fake.tab_text("solo.md")
            start = body.find(P)
            end = start + len(P)
            nested_before = (body[:start] + "<<" + body[start:end]
                             + ">>{{filt rewrite}}" + body[end:])
        check("unguarded plant would nest as <<<<…>>{{…}}>>{{…}}",
              "<<<<" in nested_before
              and th.render_pending(P, "filt rewrite") in nested_before,
              nested_before)
        check("nested form still contains the filter form substring "
              "(read-back would pass)",
              th.render_pending(P, "filt rewrite") in nested_before)

        with patch.object(gdocs, "push_doc",
                          return_value={"mode": "diff", "ops": 0}):
            try:
                gdocs.write_pending_forms(
                    db, manuscript, "solo.md", [filt], fake, fake)
                raised = None
            except LookupError as err:
                raised = str(err)
        check("write_pending_forms raises LookupError naming nest risk",
              raised is not None and "nest" in raised.lower()
              and "pending forms" in raised.lower(),
              repr(raised))
        check("tab is unchanged after the refusal (no plant)",
              fake.tab_text("solo.md") == form + "\n"
              or form in fake.tab_text("solo.md")
              and "<<<<" not in fake.tab_text("solo.md"),
              fake.tab_text("solo.md"))

    print("propose_change also refuses a tab that already carries forms:")
    with tempfile.TemporaryDirectory() as tmp:
        db, manuscript, _ms = _workspace(Path(tmp))
        fake = _map_tab(db, manuscript, _TabFake([("book", ""), ("solo.md", ESSAY)]))
        filt_form = th.render_pending(P, "filt rewrite")
        fake._tab("tab-2")["body"] = fake._tab("tab-2")["body"].replace(
            P + "\n", filt_form + "\n", 1)
        crow = ko_fields("dc")
        crow.update(
            manuscript_id=manuscript["id"], comment_id="c-1",
            file="solo.md", location="solo.md", quoted=P,
            content="Can we make this stronger?", author="author",
            comment_created=None, replies="[]", state="ingested",
        )
        db.insert("doc_comments", crow)
        try:
            gdocs.propose_change(
                db, manuscript, "c-1", P, "margin rewrite", "why",
                service=fake, docs_service=fake)
            raised = None
        except LookupError as err:
            raised = str(err)
        check("propose_change refuses when filter forms are already out",
              raised is not None and "pending forms" in str(raised).lower()
              and "nest" in str(raised).lower(),
              repr(raised))
        check("propose_change left the filter form un-nested",
              "<<<<" not in fake.tab_text("solo.md")
              and filt_form in fake.tab_text("solo.md"),
              fake.tab_text("solo.md"))

    print(f"\nALL CHECKS PASSED ({PASSED})")


if __name__ == "__main__":
    main()
