"""docs/generated/ matches the code: layer 3 of metalm guidelines/design.md#generated-docs.

The pre-commit hook and the docs workflow regenerate it on every commit and PR;
this test fails when both were skipped. Fix: run `metalm-gendocs` at the repo
root and commit docs/generated/. metalm-gendocs is in the `dev` extra
(authorlm/pyproject.toml).

Run: python3 tests/test_generated_docs.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_generated_docs_are_current() -> None:
    try:
        from metalm_gendocs.generate import stale
    except ImportError:
        raise AssertionError(
            "metalm-gendocs is not installed: pip install -e '.[dev]' in authorlm/")
    files = stale(ROOT)
    assert files == [], (
        f"stale generated docs: {files}; run metalm-gendocs at the repo root "
        "and commit docs/generated/")


if __name__ == "__main__":
    try:
        test_generated_docs_are_current()
    except AssertionError as err:
        print(f"FAIL: {err}")
        sys.exit(1)
    print("all checks passed (1)")
