"""The per-essay workflow's briefs as library files: `authorlm/library/workflow/`.

Hermetic: reads only the files in the repo. Checks that every brief loads,
that `order.toml` names exactly the filter files present (and each pass's
role is in `roles.toml`), that each filter stays under 2,200 characters,
and that no file uses "ruling"/"ruled" for the author's working decisions
or names a retired style-law id.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / "library" / "workflow"
FILTERS = WORKFLOW / "filters"

FILTER_MAX = 2200
CHARTER_MAX = 2600

# Style laws marked RETIRE in docs/plans/style-law-sort-draft.md (2026-11-25),
# #84 retired by the owner's decision of 2026-11-26 (plan 11.9), and the laws
# that sort names as already retired. Test data only: nothing live is queried.
RETIRED_LAW_IDS = {
    "se-d4b55ecab50a",  # 3  Connections: section order
    "se-f16524361839",  # 7  coinage budget
    "se-0e615fb7a010",  # 13 objection protocol
    "se-707adbe9a006",  # 16 one question per essay
    "se-ef74bdc2560d",  # 17 humble voice
    "se-ed74bb53ea88",  # 28 commentary imagery
    "se-272240a67fbd",  # 30 commentary hand-off
    "se-c153dbb02a6f",  # 31 commentary openings
    "se-7ecbfd7957e0",  # 73 register of the file
    "se-e0791802da40",  # 74 good nonfiction writer
    "se-bd0b6b38d203",  # 75 reframe by the inverse
    "se-33747c87e22b",  # 84 prescriptions follow agency
    "se-895004d89deb",  # 83 dramatic premise
    "se-676bb90e47ef",  # 86 reader access points
    "se-e2b5a3db4269",  # 87 each kind's job
    "se-05d37f2c28b2",  # 92 simple without losing meaning
    "se-4392699c8966",  # 105 discernment.md tone
    "se-f8a1cde78a48",  # tone: challenging and unflinching
    "se-1dbb9891191d",  # tone (retired before the sort)
    "se-201235e9e162",  # bold claim after a heading
}

RULING = re.compile(r"\brul(?:ing|ings|ed)\b", re.IGNORECASE)


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _all_files() -> list[Path]:
    return sorted(p for p in WORKFLOW.rglob("*") if p.is_file())


def test_every_file_loads() -> None:
    expected = {"charter.md", "roles.toml", "letter.md", "construction.md",
                "order.toml", "admission-check.md", "filters/_shared.md",
                "protected-register.md"}
    present = {str(p.relative_to(WORKFLOW)) for p in _all_files()}
    check("every brief is present", expected <= present,
          sorted(expected - present))
    for path in _all_files():
        text = path.read_text(encoding="utf-8")
        check(f"{path.relative_to(WORKFLOW)} is not empty", text.strip() != "")
        if path.suffix == ".toml":
            tomllib.loads(text)
        else:
            check(f"{path.relative_to(WORKFLOW)} opens with a heading",
                  text.startswith("# "), text[:40])
    charter = (WORKFLOW / "charter.md").read_text(encoding="utf-8")
    check("the charter stays short", len(charter) <= CHARTER_MAX, len(charter))
    protected = (WORKFLOW / "protected-register.md").read_text(encoding="utf-8")
    check("the protected-register block is driven by the toc flag",
          'register = "protected"' in protected)
    for name in ("plain-reader", "concision", "audio-friendly"):
        check(f"the protected-register block stops {name}'s rewording",
              f"`{name}`" in protected)


def test_order_names_exactly_the_filters() -> None:
    order = tomllib.loads((WORKFLOW / "order.toml").read_text(encoding="utf-8"))
    roles = tomllib.loads((WORKFLOW / "roles.toml").read_text(encoding="utf-8"))
    names = [p["name"] for p in order["pass"]]
    files = {p.stem for p in FILTERS.glob("*.md") if not p.stem.startswith("_")}
    check("no pass listed twice", len(names) == len(set(names)), names)
    check("order.toml names exactly the filter files present",
          set(names) == files,
          {"no file": sorted(set(names) - files),
           "not in order": sorted(files - set(names))})
    for p in order["pass"]:
        check(f"{p['name']}: role is in roles.toml", p["role"] in roles,
              p["role"])
        check(f"{p['name']}: stage is known",
              p["stage"] in {"claims", "line", "copy"}, p["stage"])
        check(f"{p['name']}: default is known",
              p["default"] in {"ticked", "unticked", "audiobook"},
              p["default"])
    stages = [p["stage"] for p in order["pass"]]
    rank = {"claims": 0, "line": 1, "copy": 2}
    check("stages run claims, then line, then copy",
          stages == sorted(stages, key=rank.__getitem__), stages)
    check("copyedit is last", names[-1] == "copyedit", names)
    for step in ("letter", "construction", "line", "copyedit",
                 "admission-check"):
        check(f"roles.toml has a role for {step}",
              bool(roles.get(step, {}).get("role", "").strip()))


def test_filter_sizes() -> None:
    for path in sorted(FILTERS.glob("*.md")):
        if path.stem.startswith("_"):
            continue
        n = len(path.read_text(encoding="utf-8"))
        check(f"{path.name} under {FILTER_MAX} characters ({n})",
              n < FILTER_MAX, n)


def test_no_ruling_words_or_retired_laws() -> None:
    for path in _all_files():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(WORKFLOW)
        hits = RULING.findall(text)
        check(f"{rel}: no 'ruling'/'ruled'", not hits, hits)
        named = sorted(i for i in RETIRED_LAW_IDS if i in text)
        check(f"{rel}: names no retired style-law id", not named, named)


def main() -> int:
    tests = [test_every_file_loads, test_order_names_exactly_the_filters,
             test_filter_sizes, test_no_ruling_words_or_retired_laws]
    failed = 0
    for t in tests:
        print(t.__name__)
        try:
            t()
        except AssertionError:
            failed += 1
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
