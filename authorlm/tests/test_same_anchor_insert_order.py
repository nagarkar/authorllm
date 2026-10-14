"""Same-anchor multi-insert plant order must match compose/resolve FIFO.

`write_pending_forms` plants each insert at the end of its anchor. A
second Docs insertText at that index lands before the earlier plant
(LIFO). `compose_marked_text` and `final_text_from_marked` are FIFO in
caller order, so planting A then B swapped essay order and attributed
green edits to the wrong thread. `pending_write_order` reverses
same-anchor inserts so Docs LIFO yields FIFO visual order.
"""
from __future__ import annotations

import json
import unittest

from authorlm import gdocs, passes, threads as th


def _insert(tid: str, new: str, anchor: int) -> dict:
    return {
        "id": tid,
        "proposed_old": "",
        "proposed_new": new,
        "metadata": json.dumps({"kind": "insert", "anchor_paragraph": anchor}),
    }


def _replace(tid: str, old: str, new: str, anchor: int) -> dict:
    return {
        "id": tid,
        "proposed_old": old,
        "proposed_new": new,
        "metadata": json.dumps({"kind": "replace", "anchor_paragraph": anchor}),
    }


class SameAnchorInsertOrder(unittest.TestCase):
    def test_pending_write_order_reverses_same_anchor_inserts(self):
        order = gdocs.pending_write_order([
            _insert("a", "First", 1),
            _insert("b", "Second", 1),
            _replace("r", "Anchor.", "Rewritten.", 1),
            _insert("c", "Other", 2),
        ])
        self.assertEqual(
            [t["id"] for t in order],
            ["c", "b", "a", "r"],
            "higher anchors first; same-anchor inserts reversed "
            "before the replace",
        )

    def test_lifo_plant_of_reversed_order_matches_compose_fifo(self):
        """Simulate Docs same-index LIFO after pending_write_order."""
        threads = [
            _insert("t1", "First insert", 1),
            _insert("t2", "Second insert", 1),
        ]
        essay = "Anchor paragraph.\n"
        local = passes.compose_marked_text(essay, threads)

        # Plant in pending_write_order; each insert at a fixed index is LIFO.
        forms: list[str] = []
        for t in gdocs.pending_write_order(threads):
            forms.insert(0, th.render_insertion(t["proposed_new"]))
        doc_marked = "Anchor paragraph.\n\n" + "\n\n".join(forms) + "\n"

        self.assertEqual(
            local, doc_marked,
            "reversed plant + Docs LIFO must equal compose FIFO",
        )

        # Author edits the visual-first form; resolve must keep it on t1.
        edited = doc_marked.replace("{{First insert}}", "{{EDITED}}")
        final, matched = passes.final_text_from_marked(edited, written=threads)
        self.assertEqual(
            [m["new"] for m in matched], ["EDITED", "Second insert"])
        self.assertIn("EDITED", final)
        self.assertIn("Second insert", final)
        self.assertLess(final.index("EDITED"), final.index("Second insert"))


if __name__ == "__main__":
    unittest.main()
