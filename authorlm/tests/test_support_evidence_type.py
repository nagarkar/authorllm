"""`beliefs._support_evidence_type` is the INV-2 door between machine
inference and author-originated evidence.

Episode-analysis events must land as `episode_analysis` so
`SYSTEM_EVIDENCE_TYPES` excludes them from `_derive_supporting`. Every
other source is author-originated and must be logged as `author_review`.
A silent flip either inflates confidence from pure machine seed or
hides genuine author endorsements. The loop suite covers the full
lifecycle; this file pins the mapping itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.beliefs import _support_evidence_type


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_episode_analysis_is_machine() -> None:
    got = _support_evidence_type("episode-analysis")
    check("episode-analysis maps to episode_analysis",
          got == "episode_analysis", repr(got))


def test_author_sources() -> None:
    for source in ("accepted", "modified", "declared", "manual",
                   "filter", None, ""):
        got = _support_evidence_type(source)
        check(f"{source!r} maps to author_review",
              got == "author_review", repr(got))


def test_near_miss_spellings_are_author() -> None:
    # Underscore / casing variants are not the episode-analysis source
    # string the rest of the module writes; they must not accidentally
    # inherit the machine flag.
    for source in ("episode_analysis", "Episode-analysis",
                   "episode-analysis ", " episode-analysis"):
        got = _support_evidence_type(source)
        check(f"near-miss {source!r} stays author_review",
              got == "author_review", repr(got))


def main() -> None:
    tests = [test_episode_analysis_is_machine, test_author_sources,
             test_near_miss_spellings_are_author]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
