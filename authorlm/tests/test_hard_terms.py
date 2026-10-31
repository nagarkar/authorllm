"""`filtering.is_hard_to_say` / `hard_terms` / `candidate_terms` are the
harness-computed pronunciation prelude signals (F1 / F2).

They decide which protected names the audio-friendly filter surfaces as
needing a dictionary row — no model in the loop. A silent drift here
either floods the author with English homophones (`Field`/`field`) or
misses Sanskrit / Greek terms that a narrator will stumble on. Existing
suites exercise `parse_pronunciations` and the dictionary parser, but
never these three pure predicates.
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.filtering import candidate_terms, hard_terms, is_hard_to_say
from authorlm.pronunciations import SEED


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _dict(*rows: tuple[str, str]) -> str:
    body = SEED
    for term, say in rows:
        body += f"| {term} | {say} | |\n"
    return body


def test_is_hard_to_say() -> None:
    for term in ("anattā", "Śūnyatā", "Bṛhadāraṇyaka", "Nāgārjuna", "οὐκ"):
        check(f"{term!r} is hard (non-ASCII letter)", is_hard_to_say(term))
    # Curly apostrophe / em-dash are punctuation — phonetic identity is
    # ASCII letters only, so Noether's Theorem is NOT hard to say.
    check("Noether’s Theorem is not hard (punctuation ≠ letter)",
          not is_hard_to_say("Noether’s Theorem"))
    check("plain ASCII Field is not hard", not is_hard_to_say("Field"))
    check("empty / None are not hard",
          not is_hard_to_say("") and not is_hard_to_say(None))
    # NFC contract: a decomposed form must still trip the letter check.
    nfd = unicodedata.normalize("NFD", "anattā")
    check("NFD anattā still hard after NFC normalize inside the helper",
          nfd != "anattā" and is_hard_to_say(nfd), repr(nfd))


def test_hard_terms() -> None:
    text = ("The Field is where anattā shows. A field of grass and "
            "Śūnyatā too. Absent never appears.")
    protected = ["Field", "anattā", "Śūnyatā", "Absent", "οὐκ"]
    got = hard_terms(text, protected, "")
    check("hard_terms keeps non-ASCII letters that occur, drops ASCII + absent",
          got == ["anattā", "Śūnyatā"], got)
    settled = hard_terms(text, protected, _dict(("anattā", "uh-NAT-tah")))
    check("a settled dictionary row is excluded",
          settled == ["Śūnyatā"], settled)
    check("sorted + deduped",
          hard_terms(text, ["Śūnyatā", "anattā", "anattā"], "")
          == ["anattā", "Śūnyatā"])


def test_candidate_terms() -> None:
    # Homophone firewall: single-word ASCII capital whose lowercase also
    # lives in the essay is NOT a pronunciation problem.
    both = "The Field is a field of practice."
    check("Field/field homophone is dropped from candidates",
          candidate_terms(both, ["Field"], "") == [])
    only_cap = "The Field is the whole."
    check("Field without lowercase prose stays a candidate",
          candidate_terms(only_cap, ["Field"], "") == ["Field"])
    text = "anattā and Noether's Theorem appear; Śūnyatā too."
    protected = ["anattā", "Noether's Theorem", "Śūnyatā", "Ghost"]
    got = candidate_terms(text, protected, "")
    check("candidates are occurring unprotected terms, sorted",
          got == ["Noether's Theorem", "anattā", "Śūnyatā"], got)
    settled = candidate_terms(
        text, protected, _dict(("Śūnyatā", "SHOON-yah-tah")))
    check("settled rows drop out of candidacy",
          settled == ["Noether's Theorem", "anattā"], settled)
    check("multi-word ASCII is never subject to the Field/field skip",
          candidate_terms("See Noether's Theorem here.",
                          ["Noether's Theorem"], "")
          == ["Noether's Theorem"])


def main() -> None:
    for t in (test_is_hard_to_say, test_hard_terms, test_candidate_terms):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
