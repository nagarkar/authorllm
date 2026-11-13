"""Doc export can wrap a struck old half in emphasis (`<<*term*>>` for a
staged `term`). pair_external_forms already pairs those; landing must too —
otherwise Accept collapses to the emphasized OLD and records declined.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from authorlm import api, form_grammar, passes
from authorlm.api import _rule_external_marked
from authorlm.db import Database, ko_fields


MARKED = (
    "# T\n\n"
    "Lead in.\n\n"
    "~~<<*He mistaketh affliction*>>~~{{He mistaketh it}}*, and so.*\n"
)
THREAD = {
    "id": "em",
    "proposed_old": "He mistaketh affliction",
    "proposed_new": "He mistaketh it",
}


class EmphasizedOldLanding(unittest.TestCase):
    def test_accept_keeps_the_green_half(self):
        ruled, _ = api.rule_forms_in_marked(
            MARKED, [THREAD], {"em": "He mistaketh it"})
        after, _ = _rule_external_marked(ruled, [THREAD], [])
        final, matched = passes.final_text_from_marked(
            after, written=[THREAD], kinds=("replace",))
        self.assertEqual(len(matched), 1)
        self.assertIn("He mistaketh it", final)
        self.assertNotIn("*He mistaketh affliction*", final)
        self.assertNotIn("<<", final)

    def test_decline_restores_the_staged_old(self):
        ruled, _ = api.rule_forms_in_marked(MARKED, [THREAD], {"em": ""})
        after, _ = _rule_external_marked(ruled, [THREAD], [])
        self.assertEqual(form_grammar.pending_forms(after), [])
        final, matched = passes.final_text_from_marked(
            after, written=[THREAD], kinds=("replace",))
        self.assertEqual(matched, [])
        self.assertIn("He mistaketh affliction", final)
        self.assertNotIn("*He mistaketh affliction*", final)
        self.assertNotIn("He mistaketh it", final)

    def test_record_resolution_matches_emphasized_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "ws.sqlite"
            db = Database(db_path)
            ms = ko_fields("ms")
            ms.update(name="book", path=tmp)
            mid = db.insert("manuscripts", ms)
            row = ko_fields("dt")
            row.update(
                manuscript_id=mid, origin_type="harvest",
                origin_id="h:solo.md:1", file="solo.md",
                proposed_old=THREAD["proposed_old"],
                proposed_new=THREAD["proposed_new"],
                note="n", state="written", our_reply_ids="[]",
                metadata=json.dumps({"index": 1}))
            tid = db.insert("doc_threads", row)
            form = {
                "kind": "replace",
                "old": "*He mistaketh affliction*",
                "new": "He mistaketh it",
                "start": 0, "end": 1,
            }
            thread = dict(db.one("SELECT * FROM doc_threads WHERE id = ?",
                                 (tid,)))
            passes.record_resolution(
                db, mid, "solo.md", [form],
                origin_type="harvest", evidence_type="external_edit",
                final_text="Lead in.\n\nHe mistaketh it, and so.\n",
                threads=[thread])
            got = db.one("SELECT state FROM doc_threads WHERE id = ?",
                         (tid,))
            self.assertEqual(got["state"], "cleaned")


if __name__ == "__main__":
    unittest.main()
