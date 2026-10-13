"""`proposals._content_hash` / `_alias_canonical_conflict` gate alias
proposals before they reach the author's queue.

One alias name may not hold two open, competing canonicals (Q/alias-dedupe):
silently superseding the standing proposal would let a later pass overwrite
a question the author has not reached. Exact content-hash repeats are the
companion refuse. Neither helper had a dedicated hermetic pin.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.db import Database, ko_fields
from authorlm.proposals import _alias_canonical_conflict, _content_hash


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_content_hash_is_order_insensitive() -> None:
    a = _content_hash({"alias": "Prophecy", "canonical": "Essentialism"})
    b = _content_hash({"canonical": "Essentialism", "alias": "Prophecy"})
    check("sort_keys makes key order irrelevant", a == b, (a, b))
    check("hash is a 16-char hex digest",
          len(a) == 16 and all(c in "0123456789abcdef" for c in a), a)
    check("different payloads diverge",
          a != _content_hash({"alias": "Prophecy",
                              "canonical": "Birth-based Superstition"}))


def _db_with_open_alias(alias: str, canonical: str) -> tuple[Database, str]:
    root = Path(tempfile.mkdtemp(prefix="authorlm-alias-"))
    db = Database(root / "authorlm.db")
    ms = ko_fields("ms")
    ms.update(name="alias-ms", path=str(root / "ms"))
    db.insert("manuscripts", ms)
    row = ko_fields("pr")
    payload = {"alias": alias, "canonical": canonical}
    row.update(
        manuscript_id=ms["id"], kind="alias", target=alias,
        payload=json.dumps(payload), content_hash=_content_hash(payload),
        source="test", state="open",
    )
    db.insert("knowledge_proposals", row)
    return db, ms["id"]


def test_conflict_returns_standing_canonical() -> None:
    db, mid = _db_with_open_alias("Prophecy", "Birth-based Superstition")
    got = _alias_canonical_conflict(db, mid, "Prophecy", "Essentialism")
    check("competing canonical returns the standing one",
          got == "Birth-based Superstition", got)


def test_conflict_is_case_and_space_insensitive() -> None:
    db, mid = _db_with_open_alias("Prophecy", "Essentialism")
    got = _alias_canonical_conflict(db, mid, "  prophecy ", "Other")
    check("alias match folds case and whitespace",
          got == "Essentialism", got)


def test_exact_repeat_also_matches() -> None:
    db, mid = _db_with_open_alias("Prophecy", "Essentialism")
    got = _alias_canonical_conflict(db, mid, "Prophecy", "Essentialism")
    check("exact repeat collapses into the standing row (same guard)",
          got == "Essentialism", got)


def test_different_alias_is_free() -> None:
    db, mid = _db_with_open_alias("Prophecy", "Essentialism")
    got = _alias_canonical_conflict(db, mid, "Oracle", "Essentialism")
    check("a different alias name does not conflict", got is None, got)


def test_dismissed_does_not_block() -> None:
    db, mid = _db_with_open_alias("Prophecy", "Essentialism")
    row = db.one(
        "SELECT id FROM knowledge_proposals WHERE manuscript_id = ?",
        (mid,))
    db.update("knowledge_proposals", row["id"], {"state": "dismissed"})
    got = _alias_canonical_conflict(db, mid, "Prophecy", "Other")
    check("only open aliases participate in the conflict guard",
          got is None, got)


def main() -> None:
    tests = [
        test_content_hash_is_order_insensitive,
        test_conflict_returns_standing_canonical,
        test_conflict_is_case_and_space_insensitive,
        test_exact_repeat_also_matches,
        test_different_alias_is_free,
        test_dismissed_does_not_block,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
