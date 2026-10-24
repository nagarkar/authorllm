"""`gdocs._sidecar_would_be_emptied` refuses a Doc push that would wipe
every pronunciation row.

The guard is scoped to `pronunciations.md` and consults that file's own
parser: a rewrite that leaves the table with zero rows while the current
file still has rows must never land. Mis-firing empties the dictionary
on push; never firing lets a Doc round-trip destroy settled terms.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import pronunciations as pron
from authorlm.gdocs import _sidecar_would_be_emptied


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


FILLED = (pron.SEED
          + "| Kant | /kɑnt/ | |\n"
          + "| Hegel | /ˈheɪɡəl/ | German |\n")
EMPTY_TABLE = pron.SEED
HEADER_ONLY = f"{pron.TITLE}\n\n{pron.HEADER}\n{pron.DELIMITER}\n"


def test_wiping_rows_is_emptied() -> None:
    check("SEED over a filled dictionary would empty it",
          _sidecar_would_be_emptied(pron.FILENAME, EMPTY_TABLE, FILLED)
          is True)
    check("header-only over filled rows would empty it",
          _sidecar_would_be_emptied(pron.FILENAME, HEADER_ONLY, FILLED)
          is True)


def test_keeping_or_growing_rows_is_safe() -> None:
    check("same content is not emptied",
          _sidecar_would_be_emptied(pron.FILENAME, FILLED, FILLED) is False)
    grown = FILLED + "| Spinoza | /spɪˈnoʊzə/ | |\n"
    check("adding a row is not emptied",
          _sidecar_would_be_emptied(pron.FILENAME, grown, FILLED) is False)
    check("SEED over SEED (already empty) is not emptied",
          _sidecar_would_be_emptied(pron.FILENAME, EMPTY_TABLE, EMPTY_TABLE)
          is False)


def test_empty_or_blank_current_never_trips() -> None:
    check("empty current cannot be emptied",
          _sidecar_would_be_emptied(pron.FILENAME, EMPTY_TABLE, "") is False)
    check("whitespace-only current cannot be emptied",
          _sidecar_would_be_emptied(pron.FILENAME, EMPTY_TABLE, "  \n\t")
          is False)


def test_scoped_to_pronunciations_filename() -> None:
    check("a chapter path is never judged by the pronunciation parser",
          _sidecar_would_be_emptied("01-intro.md", EMPTY_TABLE, FILLED)
          is False)
    check("a near-miss dictionary name is out of scope",
          _sidecar_would_be_emptied("pronunciation.md", EMPTY_TABLE, FILLED)
          is False)


def main() -> None:
    tests = [test_wiping_rows_is_emptied, test_keeping_or_growing_rows_is_safe,
             test_empty_or_blank_current_never_trips,
             test_scoped_to_pronunciations_filename]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
