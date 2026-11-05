"""`beliefs.seed_margin_candidate` — the margin-thread distiller door.

Unlike the triage door (`parse_distiller`), margin NEW replies carry
SCOPE + STATEMENT and may omit EXAMPLE; an invalid or missing scope
declines rather than writing an unscoped law-shaped belief. Without an
LLM the explanation stays evidence only. A silent change here either
blocks legitimate learning or lets overreach become a candidate.

Run: python3 tests/test_seed_margin_candidate.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Offline suite: pin config/.env and client provenance away from a live
# checkout (same treatment as test_loop.py).
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import beliefs as bel  # noqa: E402
from authorlm.db import Database, ko_fields, loads  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


class ScriptedLLM:
    """Replies in order; records what it was asked."""

    enabled = True

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system, user):
        self.prompts.append((system, user))
        return self.replies.pop(0) if self.replies else None


def _fixture() -> tuple[Database, dict]:
    root = Path(tempfile.mkdtemp(prefix="authorlm-margin-seed-"))
    db = Database(root / "t.db")
    ms = ko_fields("ms")
    ms.update(name="t", path=str(root))
    db.insert("manuscripts", ms)
    return db, ms


def test_seed_margin_candidate() -> None:
    db, ms = _fixture()
    guides = [{"id": "g-nearest", "name": "Nearest Guide"}]

    class OffLLM:
        enabled = False

        def complete(self, system, user):
            raise AssertionError("disabled LLM must not be called")

    check("no LLM leaves margin feedback as evidence only",
          bel.seed_margin_candidate(
              db, ms["id"], "Too abstract.", "essay.md", guides, None) is None)
    check("a disabled LLM never seeds from margin feedback",
          bel.seed_margin_candidate(
              db, ms["id"], "Too abstract.", "essay.md", guides,
              OffLLM()) is None)

    llm = ScriptedLLM("NONE")
    check("NONE declines without seeding",
          bel.seed_margin_candidate(
              db, ms["id"], "One-off nit.", "essay.md", guides, llm) is None)
    check("the margin prompt names file, guide chain, and verbatim feedback",
          "FILE: essay.md" in llm.prompts[0][1]
          and "Nearest Guide (g-nearest)" in llm.prompts[0][1]
          and "AUTHOR FEEDBACK (verbatim): One-off nit." in llm.prompts[0][1]
          and "none yet" in llm.prompts[0][1],
          llm.prompts[0][1] if llm.prompts else "")

    llm = ScriptedLLM("SCOPE: chapter\nSTATEMENT: Prefer concrete cases.\n"
                      "EXAMPLE: the opening paragraph")
    check("an unknown SCOPE declines (scope is the overreach gate)",
          bel.seed_margin_candidate(
              db, ms["id"], "Too abstract.", "essay.md", guides, llm) is None)

    llm = ScriptedLLM("SCOPE: file\nEXAMPLE: the opening paragraph")
    check("SCOPE without STATEMENT declines",
          bel.seed_margin_candidate(
              db, ms["id"], "Too abstract.", "essay.md", guides, llm) is None)

    llm = ScriptedLLM('SCOPE: file\nSTATEMENT: "Keep metaphysics concrete."')
    seeded = bel.seed_margin_candidate(
        db, ms["id"], "Too abstract here.", "essay.md", guides, llm)
    check("SCOPE file + STATEMENT seeds a margin-thread candidate",
          seeded is not None and seeded["source"] == "margin-thread"
          and seeded["statement"] == "Keep metaphysics concrete.",
          str(seeded))
    meta = loads(db.one(
        "SELECT metadata FROM editorial_beliefs WHERE id = ?",
        (seeded["id"],))["metadata"], {}) or {}
    check("file scope records the file as scope_ref and keeps the "
          "verbatim explanation; EXAMPLE is optional on this door",
          meta.get("scope_kind") == "file"
          and meta.get("scope_ref") == "essay.md"
          and meta.get("original_explanation") == "Too abstract here."
          and "example" not in meta,
          str(meta))

    llm = ScriptedLLM("SCOPE: guide\nSTATEMENT: Open with a lived example.\n"
                      "EXAMPLE: the first sentence of essay.md")
    guided = bel.seed_margin_candidate(
        db, ms["id"], "Needs an example up front.", "essay.md", guides, llm)
    guided_meta = loads(db.one(
        "SELECT metadata FROM editorial_beliefs WHERE id = ?",
        (guided["id"],))["metadata"], {}) or {}
    check("SCOPE guide pins scope_ref to the nearest guide id and stores "
          "EXAMPLE when present",
          guided_meta.get("scope_kind") == "guide"
          and guided_meta.get("scope_ref") == "g-nearest"
          and guided_meta.get("example") == "the first sentence of essay.md",
          str(guided_meta))

    llm = ScriptedLLM("SCOPE: manuscript\nSTATEMENT: Prefer lived examples "
                      "manuscript-wide.\nEXAMPLE: e")
    book = bel.seed_margin_candidate(
        db, ms["id"], "Same issue elsewhere.", "essay.md", guides, llm)
    book_meta = loads(db.one(
        "SELECT metadata FROM editorial_beliefs WHERE id = ?",
        (book["id"],))["metadata"], {}) or {}
    check("SCOPE manuscript pins scope_ref to the manuscript id",
          book_meta.get("scope_kind") == "manuscript"
          and book_meta.get("scope_ref") == ms["id"],
          str(book_meta))

    llm = ScriptedLLM("SCOPE: guide\nSTATEMENT: Empty-chain fallback rule.\n"
                      "EXAMPLE: e")
    fallback = bel.seed_margin_candidate(
        db, ms["id"], "No nearest guide.", "essay.md", [], llm)
    fallback_meta = loads(db.one(
        "SELECT metadata FROM editorial_beliefs WHERE id = ?",
        (fallback["id"],))["metadata"], {}) or {}
    check("SCOPE guide with an empty chain falls back to manuscript id",
          fallback_meta.get("scope_ref") == ms["id"],
          str(fallback_meta))

    llm = ScriptedLLM("MATCH: B1")
    matched = bel.seed_margin_candidate(
        db, ms["id"], "Same idea again.", "other.md", guides, llm)
    check("MATCH B1 reinforces the first live margin-thread belief",
          matched is not None and matched["id"] == seeded["id"]
          and matched["supporting"] == 2,
          str(matched))

    llm = ScriptedLLM("MATCH: B99")
    check("MATCH on an unknown label declines",
          bel.seed_margin_candidate(
              db, ms["id"], "Unknown match.", "essay.md", guides, llm) is None)


def main() -> None:
    print("test_seed_margin_candidate:")
    test_seed_margin_candidate()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
