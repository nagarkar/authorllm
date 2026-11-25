"""`api.restore_emptied_forms` turns an emptied green half back into the
old text so settle reads a decline, not a delete (#506's Doc-road twin).

`<<old>>{{}}`, whitespace greens, and a bare `<<old>>` must restore; an
emphasis-wrapped export (`<<*old*>>{{}}`) must still match proposed_old.
A form that still carries new text must be left alone. Hermetic — no DB,
no Google, no model.
Run: python3 tests/test_restore_emptied_forms.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("AUTHORLM_CONFIG",
                      "/nonexistent/authorlm-restore-emptied/config.toml")
os.environ.setdefault("AUTHORLM_ENV",
                      "/nonexistent/authorlm-restore-emptied/.env")
os.environ.setdefault("AUTHORLM_CLIENT", "none")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.api import restore_emptied_forms  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


REPLACES = [{"proposed_old": "the claim"}]


def test_emptied_greens_restore_old_text() -> None:
    for label, marked in (
        ("empty braces", "Lead <<the claim>>{{}} Tail"),
        ("whitespace green", "Lead <<the claim>>{{  }} Tail"),
        ("bare strike markers", "Lead <<the claim>> Tail"),
        ("struck emptied", "Lead ~~<<the claim>>~~{{}} Tail"),
    ):
        got = restore_emptied_forms(marked, REPLACES)
        check(f"{label} → old text, no markers",
              got == "Lead the claim Tail", got)


def test_emphasis_wrapped_old_still_restores() -> None:
    # Export wraps proposed_old in emphasis; exact regex misses it, so the
    # pending-forms pass must still restore and decline.
    got = restore_emptied_forms("Lead <<*the claim*>>{{}} Tail", REPLACES)
    check("<<*proposed_old*>>{{}} restores proposed_old",
          got == "Lead the claim Tail", got)


def test_kept_new_and_unmatched_are_untouched() -> None:
    kept = "Lead <<the claim>>{{edited}} Tail"
    check("non-empty green half is left alone",
          restore_emptied_forms(kept, REPLACES) == kept, kept)

    other = "Lead <<other>>{{}} Tail"
    check("emptied form whose old is not in replaces stays marked",
          restore_emptied_forms(other, REPLACES) == other, other)

    check("empty replaces list is a no-op",
          restore_emptied_forms(kept, []) == kept)


def main() -> None:
    for fn in (test_emptied_greens_restore_old_text,
               test_emphasis_wrapped_old_still_restores,
               test_kept_new_and_unmatched_are_untouched):
        print(f"{fn.__name__}:")
        fn()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
