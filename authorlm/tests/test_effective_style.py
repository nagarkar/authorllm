"""`styles.effective_style` (and `guide_chain`) compose ratified law into
drafting prompts with no model in the loop. Wrong override / retire /
chain math injects or drops binding law.

`api.style_show` in test_api.py covers one-hop inheritance and a single
file-local override. This file pins the composition contract hermetically:
multi-hop nearest-first order, retired rows absent, override displacement
across the chain, unattached → root, and a parent cycle that must not
loop forever.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Hermetic: never pick up a live config / client stamp.
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import styles
from authorlm.db import Database


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _db(root: Path) -> Database:
    root.mkdir(parents=True, exist_ok=True)
    return Database(root / "authorlm.db")


def test_unattached_inherits_root(root: Path) -> None:
    db = _db(root / "unattached")
    mid = "ms-style"
    house = styles.create_guide(db, mid, "house")
    styles.add_element(db, mid, "register",
                       "Address the reader in second person.", guide=house)
    got = styles.effective_style(db, mid, "lonely.md")
    check("unattached file inherits the root guide",
          [e["statement"] for e in got]
          == ["Address the reader in second person."],
          [e["statement"] for e in got])
    chain = styles.guide_chain(db, mid, "lonely.md")
    check("guide_chain for an unattached file is just the root",
          [g["name"] for g in chain] == ["house"],
          [g["name"] for g in chain])


def test_multi_hop_nearest_first(root: Path) -> None:
    db = _db(root / "multihop")
    mid = "ms-style"
    house = styles.create_guide(db, mid, "house")
    part = styles.create_guide(db, mid, "part", parent_name="house")
    chapter = styles.create_guide(db, mid, "chapter", parent_name="part")
    styles.attach_file(db, mid, "ch.md", chapter)
    styles.add_element(db, mid, "register", "House register.", guide=house)
    styles.add_element(db, mid, "tone", "Part tone.", guide=part)
    styles.add_element(db, mid, "syntax", "Chapter syntax.", guide=chapter)
    styles.add_element(db, mid, "lexicon", "File lexicon.", file="ch.md")

    chain = styles.guide_chain(db, mid, "ch.md")
    check("guide_chain is nearest-first: chapter → part → house",
          [g["name"] for g in chain] == ["chapter", "part", "house"],
          [g["name"] for g in chain])

    statements = [e["statement"] for e in styles.effective_style(
        db, mid, "ch.md")]
    check("effective_style layers file then nearest guide then ancestors",
          statements == ["File lexicon.", "Chapter syntax.",
                         "Part tone.", "House register."],
          statements)


def test_override_displaces_across_chain(root: Path) -> None:
    db = _db(root / "override")
    mid = "ms-style"
    house = styles.create_guide(db, mid, "house")
    child = styles.create_guide(db, mid, "child", parent_name="house")
    styles.attach_file(db, mid, "c.md", child)
    house_tone = styles.add_element(
        db, mid, "tone", "Be grave.", guide=house)
    styles.add_element(db, mid, "tone", "Be clipped.", guide=child,
                       overrides=house_tone["id"])
    styles.add_element(db, mid, "register", "Second person.", guide=house)

    statements = {e["statement"] for e in styles.effective_style(
        db, mid, "c.md")}
    check("nearer override displaces the named ancestor element",
          statements == {"Be clipped.", "Second person."}, statements)
    check("the overridden house tone is gone",
          "Be grave." not in statements)


def test_retired_absent(root: Path) -> None:
    db = _db(root / "retired")
    mid = "ms-style"
    house = styles.create_guide(db, mid, "house")
    live = styles.add_element(db, mid, "tone", "Stay live.", guide=house)
    dead = styles.add_element(db, mid, "syntax", "Retire me.", guide=house)
    styles.retire_element(db, dead)
    statements = [e["statement"] for e in styles.effective_style(
        db, mid, "any.md")]
    check("retired elements are absent from effective_style",
          statements == ["Stay live."], statements)
    check("the live element id is unchanged",
          any(e["id"] == live["id"] for e in styles.effective_style(
              db, mid, "any.md")))


def test_guide_chain_cycle_safe(root: Path) -> None:
    db = _db(root / "cycle")
    mid = "ms-style"
    a = styles.create_guide(db, mid, "a")
    b = styles.create_guide(db, mid, "b", parent_name="a")
    styles.attach_file(db, mid, "x.md", b)
    # Corrupt the tree into a cycle: a → b → a.
    db.update("style_guides", a["id"], {"parent": b["id"]})
    chain = styles.guide_chain(db, mid, "x.md")
    names = [g["name"] for g in chain]
    check("a parent cycle still terminates",
          names == ["b", "a"], names)
    check("each guide appears at most once",
          len(names) == len(set(names)), names)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tests = [test_unattached_inherits_root, test_multi_hop_nearest_first,
                 test_override_displaces_across_chain, test_retired_absent,
                 test_guide_chain_cycle_safe]
        for t in tests:
            print(f"{t.__name__}:")
            t(root)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
