"""Regression: split_tabbed_export must not treat an in-turn content H1
that equals the *next* tab's title as that tab's boundary.

X7-2 covered the out-of-turn case (collision heading names a tab further
down the order). The immediate-neighbor case — e.g. `# manifest` / 
`# **manifest**` inside the essay that sits just before the manifest
tab — still opened the next section early under first-match walking.
Docs bolds every heading on export, so the collision and the real tab
title are byte-identical; only last-before-next placement keeps the
essay's tail. Pull/reconcile then three-way as 'changed' and auto-pull
would otherwise truncate the local file.
"""

from __future__ import annotations

import hashlib
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault(
    "AUTHORLM_CONFIG", "/nonexistent/authorlm-test/config.toml")
os.environ.setdefault(
    "AUTHORLM_ENV", "/nonexistent/authorlm-test/.env")
os.environ.setdefault("AUTHORLM_CLIENT", "none")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.gdocs import (  # noqa: E402
    normalize_markdown, split_tabbed_export, three_way,
)


ORDER = ["01-choice.md", "02-fork.md", "manifest"]
KNOWN = set(ORDER)


def _whole(fork_body: str) -> str:
    return (
        "# **01-choice.md**\n\n"
        "Choice body.\n\n"
        "# **02-fork.md**\n\n"
        f"{fork_body.rstrip()}\n\n"
        "# **manifest**\n\n"
        "Manuscript: book\n"
    )


FORK_LOCAL = (
    "# Fork\n\n"
    "Prose before the content heading.\n\n"
    "# manifest\n\n"
    "Tail of the fork essay that must stay in 02-fork.\n"
)


class InTurnNeighborSplit(unittest.TestCase):
    def test_plain_content_h1_keeps_tail(self):
        parts = split_tabbed_export(
            _whole(FORK_LOCAL), KNOWN, order=ORDER)
        self.assertIn("Tail of the fork essay that must stay in 02-fork.",
                      parts["02-fork.md"])
        self.assertIn("# manifest", parts["02-fork.md"])
        self.assertEqual(parts["manifest"].strip(), "Manuscript: book")

    def test_bold_content_h1_keeps_tail(self):
        # Real Docs export bolds every heading — collision == tab title.
        body = FORK_LOCAL.replace("# manifest", "# **manifest**")
        parts = split_tabbed_export(_whole(body), KNOWN, order=ORDER)
        self.assertIn("Tail of the fork essay that must stay in 02-fork.",
                      parts["02-fork.md"])
        self.assertIn("# **manifest**", parts["02-fork.md"])
        self.assertEqual(parts["manifest"].strip(), "Manuscript: book")

    def test_out_of_turn_collision_still_safe(self):
        # X7-2 shape: # manifest inside 01-choice, not the neighbor.
        whole = (
            "# **01-choice.md**\n\n"
            "Prose.\n\n"
            "# manifest\n\n"
            "Still choice.\n\n"
            "# **02-fork.md**\n\n"
            "Fork body.\n\n"
            "# **manifest**\n\n"
            "Manuscript: book\n"
        )
        parts = split_tabbed_export(whole, KNOWN, order=ORDER)
        self.assertIn("# manifest", parts["01-choice.md"])
        self.assertIn("Still choice.", parts["01-choice.md"])
        self.assertEqual(parts["02-fork.md"].strip(), "Fork body.")
        self.assertEqual(parts["manifest"].strip(), "Manuscript: book")

    def test_reconcile_would_not_auto_pull_truncate(self):
        body = FORK_LOCAL.replace("# manifest", "# **manifest**")
        parts = split_tabbed_export(_whole(body), KNOWN, order=ORDER)
        tab = normalize_markdown(parts["02-fork.md"])
        local = normalize_markdown(body)
        base = hashlib.sha256(local.encode()).hexdigest()[:16]
        self.assertEqual(three_way(tab, local, base), "unchanged")


if __name__ == "__main__":
    unittest.main()
