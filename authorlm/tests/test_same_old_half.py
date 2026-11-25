"""`passes._same_old_half` pairs a pending form's old half with a
thread's proposed_old, including the Doc export's emphasis wrapping
(`<<*term*>>` for staged `term`).

A false negative leaves a form unmatched so settle collapses it to old
or fails to rule; a false positive pairs the wrong thread. Same
rendered_text rule as `api.pair_external_forms` /
`restore_emptied_forms`. Hermetic.
Run: python3 tests/test_same_old_half.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("AUTHORLM_CONFIG",
                      "/nonexistent/authorlm-same-old-half/config.toml")
os.environ.setdefault("AUTHORLM_ENV",
                      "/nonexistent/authorlm-same-old-half/.env")
os.environ.setdefault("AUTHORLM_CLIENT", "none")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.passes import _same_old_half  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_exact_and_emphasis_match() -> None:
    check("byte-identical halves match",
          _same_old_half("the claim", "the claim"))
    check("Doc-export italics around proposed_old still match",
          _same_old_half("*the claim*", "the claim"))
    check("bold wrapping also matches via rendered_text",
          _same_old_half("**the claim**", "the claim"))


def test_mismatch_refuses() -> None:
    check("different prose does not match",
          not _same_old_half("the claim", "another claim"))
    check("empty form_old only matches empty proposed_old",
          _same_old_half("", "") and not _same_old_half("", "x"))


def main() -> None:
    for fn in (test_exact_and_emphasis_match, test_mismatch_refuses):
        print(f"{fn.__name__}:")
        fn()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
