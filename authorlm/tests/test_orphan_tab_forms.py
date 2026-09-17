"""Hermetic regression: orphan Doc-tab forms must not be wiped by rebuild.

Timeout-after-commit desync: batchUpdate planted <<old>>{{new}} on the
tab, but documents.get timed out (or the process died) before threads
were marked `written`. forms_pending is blind; without the tab-side
orphan guard, ordinary push_doc / reconcile auto-push rebuilds from
local OLD and silently destroys any author rewording of the green half.

Run: python3 tests/test_orphan_tab_forms.py
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

from authorlm.db import Database, ko_fields
from authorlm import api, gdocs, threads


class _Req:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class FakeDocs:
    """Minimal Docs/Drive surface for the orphan-guard path."""

    def __init__(self):
        self.tabs: dict[str, tuple[str, str]] = {}  # title -> (tab_id, text)
        self.rewrites = 0

    def documents(self):
        return self

    def files(self):
        return self

    def get(self, documentId=None, includeTabsContent=None):
        tabs = []
        for title, (tid, text) in self.tabs.items():
            tabs.append({
                "tabProperties": {"tabId": tid, "title": title},
                "documentTab": {"body": {"content": [
                    {"paragraph": {"elements": [
                        {"startIndex": 1,
                         "textRun": {"content": text}}]}}
                ]}},
                "childTabs": [],
            })
        return _Req({"tabs": tabs})

    def batchUpdate(self, documentId=None, body=None):
        self.rewrites += 1
        return _Req({"replies": []})

    def create(self, body=None, media_body=None, fields=None):
        return _Req({"id": "temp-1"})

    def delete(self, fileId=None):
        return _Req({})

    def export(self, fileId=None, mimeType=None):
        return _Req(b"# essay\n")

    def comments(self):
        return self

    def list(self, fileId=None, fields=None, pageToken=None,
             includeDeleted=None):
        # Empty margin — so comment_bearing does not force the surgical
        # path and the orphan-form rebuild guard is the one under test.
        return _Req({"comments": []})

    def set_tab(self, title: str, tab_id: str, text: str) -> None:
        self.tabs[title] = (tab_id, text)


def main() -> None:
    fails: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        if ok:
            print(f"  ok: {label}")
        else:
            print(f"  FAIL: {label} — {detail}")
            fails.append(label)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "essay.md").write_text(
            "Brass planets on brass rails, polished.\n\n"
            "A later paragraph.\n", encoding="utf-8")
        db = Database(root / "authorlm.db")
        ms = api.register_manuscript(db, "orphan-probe", str(root))

        # Prior successful plant: mapping points at a live tab whose
        # text still carries the pending form, but NO written row.
        bridge = gdocs.manuscript_bridge(ms)
        meta = gdocs._mapping(db, ms)
        meta[bridge.meta_key] = {
            "_master_id": "master-1",
            "essay.md": {
                "tab_id": "tab-essay",
                "pushed_hash": "deadbeefdeadbeef",
                "checked_out": True,
            },
        }
        gdocs._save_mapping(db, ms, meta)

        pending = threads.render_pending(
            "Brass planets on brass rails, polished.",
            "Brass planets, post-edited in Docs.")
        fake = FakeDocs()
        fake.set_tab("essay.md", "tab-essay",
                     pending + "\n\nA later paragraph.\n")

        check("forms_pending is blind without written rows",
              gdocs.forms_pending(db, ms["id"], "essay.md") is None)

        check("tab detector sees the orphan replace form",
              gdocs._tab_has_replace_forms(fake, "master-1", "tab-essay"))

        bare = FakeDocs()
        bare.set_tab("essay.md", "tab-essay",
                     "Use {{title}} in the template.\n")
        check("bare {{title}} prose is not an orphan form",
              not gdocs._tab_has_replace_forms(bare, "master-1", "tab-essay"))

        raised = None
        before = fake.rewrites
        try:
            gdocs.push_doc(db, ms, "essay.md", service=fake,
                           docs_service=fake)
        except LookupError as err:
            raised = str(err)
        check("ordinary push refuses orphan tab forms",
              raised is not None
              and "none are recorded as written" in (raised or "")
              and "rewording" in (raised or ""),
              raised or "(no raise)")
        check("refuse did not rebuild the tab",
              fake.rewrites == before, f"rewrites={fake.rewrites}")

        # Leveling for write_pending_forms must not hit the orphan guard.
        try:
            gdocs.push_doc(db, ms, "essay.md", service=fake,
                           docs_service=fake, leveling=True)
            leveled_err = None
        except LookupError as err:
            leveled_err = str(err)
        except Exception as err:
            # Thin fake may fail later in rebuild; that is fine so long
            # as it is not the orphan refuse.
            leveled_err = None if "none are recorded as written" not in str(err) \
                else str(err)
        check("leveling=True is not blocked by the orphan guard",
              leveled_err is None or "none are recorded as written" not in leveled_err,
              leveled_err or "")

        # Eager written arms forms_pending (the other half of the fix).
        th = ko_fields("dt")
        th.update(
            manuscript_id=ms["id"], origin_type="filter",
            origin_id="f:essay.md:1", file="essay.md",
            proposed_old="Brass planets on brass rails, polished.",
            proposed_new="Brass planets, post-edited in Docs.",
            note="", state="written", our_reply_ids="[]",
            metadata="{}")
        db.insert("doc_threads", th)
        check("written row arms forms_pending",
              gdocs.forms_pending(db, ms["id"], "essay.md") == "filter")

        raised2 = None
        try:
            gdocs.push_doc(db, ms, "essay.md", service=fake,
                           docs_service=fake)
        except LookupError as err:
            raised2 = str(err)
        check("written rows refuse via forms_pending (names filter settle)",
              raised2 is not None
              and "filter pending forms" in (raised2 or "")
              and "filter resolve" in (raised2 or ""),
              raised2 or "(no raise)")

    if fails:
        raise SystemExit(f"{len(fails)} check(s) failed: {fails}")
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
