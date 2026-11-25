#!/usr/bin/env python3
"""Build the spell check's word lists (authorlm/authorlm/spelling/) from
two Hunspell dictionaries, one American and one British.

    python3 tools/build_spelling_wordlists.py \
        --us-dic en_US-large.dic --us-aff en_US-large.aff \
        --gb-dic en_GB-large.dic --gb-aff en_GB-large.aff

Run once, by hand, when the word lists are to be renewed; `book lint` only
ever reads the built files, so nothing here runs at lint time and nothing
here touches the network. It writes three gzipped lists, one word per
line, sorted, and PROVENANCE.md beside them:

    en-common.txt.gz    words both dictionaries spell the same way
    en-us-only.txt.gz   words only the American dictionary has (color)
    en-gb-only.txt.gz   words only the British dictionary has (colour)

`en-extra.txt` in the same folder is kept by hand and is not touched.

The affix rules are expanded here, in plain Python, so the lists hold
every inflected form (walk, walks, walked, walking) and the lint needs no
Hunspell. Possessives ('s) are left out: the lint strips them. The gzip
header carries no time, so the same input gives the same bytes.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import re
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "authorlm" / "spelling"


def read_aff(path: Path) -> dict[str, dict]:
    """{flag: {"kind": PFX|SFX, "cross": bool, "rules": [(strip, add,
    condition regex)]}} for the affix classes of a Hunspell .aff file."""
    classes: dict[str, dict] = {}
    for line in path.read_text(encoding="latin-1").splitlines():
        parts = line.split("#")[0].split()
        if len(parts) < 4 or parts[0] not in ("PFX", "SFX"):
            continue
        kind, flag = parts[0], parts[1]
        if flag not in classes:           # the header: PFX A Y 1
            classes[flag] = {"kind": kind, "cross": parts[2] == "Y",
                             "rules": []}
            continue
        strip = "" if parts[2] == "0" else parts[2]
        add = "" if parts[3] == "0" else parts[3]
        condition = parts[4] if len(parts) > 4 else "."
        pattern = (re.compile(condition + "$") if kind == "SFX"
                   else re.compile("^" + condition))
        classes[flag]["rules"].append((strip, add, pattern))
    return classes


def _apply(word: str, kind: str, rule: tuple) -> str | None:
    strip, add, pattern = rule
    if not pattern.search(word):
        return None
    if kind == "SFX":
        if strip and not word.endswith(strip):
            return None
        return word[:len(word) - len(strip)] + add
    if strip and not word.startswith(strip):
        return None
    return add + word[len(strip):]


def expand(dic: Path, aff: Path) -> set[str]:
    """Every form the dictionary accepts: each stem, each stem with one
    prefix, with one suffix, and with both where both classes allow the
    cross product. Possessives and the digit-compound entries (ordinals)
    are left out."""
    classes = read_aff(aff)
    out: set[str] = set()
    lines = dic.read_text(encoding="latin-1").splitlines()[1:]
    for line in lines:
        stem, _, flags = line.strip().partition("/")
        if not stem or not stem[0].isalpha():
            continue
        out.add(stem)
        prefixed, suffixed = [], []
        for flag in flags:
            cls = classes.get(flag)
            if cls is None or flag == "M":
                continue
            for rule in cls["rules"]:
                form = _apply(stem, cls["kind"], rule)
                if form is None:
                    continue
                (prefixed if cls["kind"] == "PFX" else suffixed).append(
                    (form, flag, cls["cross"], rule))
        for form, *_ in prefixed + suffixed:
            out.add(form)
        for pform, _pflag, pcross, _prule in prefixed:
            for _sform, sflag, scross, srule in suffixed:
                if pcross and scross:
                    both = _apply(pform, "SFX", srule)
                    if both:
                        out.add(both)
    return {w for w in out if "'" not in w or not w.endswith("'s")}


def write_list(path: Path, words: set[str]) -> str:
    body = ("\n".join(sorted(words)) + "\n").encode("utf-8")
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0,
                       filename="") as handle:
        handle.write(body)
    path.write_bytes(buffer.getvalue())
    return hashlib.sha256(buffer.getvalue()).hexdigest()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    for name in ("us-dic", "us-aff", "gb-dic", "gb-aff"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--source-note", default="",
                        help="where the four files came from, for "
                             "PROVENANCE.md")
    parser.add_argument("--date", required=True,
                        help="the build date, YYYY-MM-DD, for PROVENANCE.md")
    args = parser.parse_args()
    us = expand(args.us_dic, args.us_aff)
    gb = expand(args.gb_dic, args.gb_aff)
    OUT.mkdir(exist_ok=True)
    lists = {"en-common.txt.gz": us & gb, "en-us-only.txt.gz": us - gb,
             "en-gb-only.txt.gz": gb - us}
    rows = []
    for name, words in lists.items():
        digest = write_list(OUT / name, words)
        rows.append(f"| `{name}` | {len(words)} | `{digest}` |")
    sources = [f"| `{p.name}` | `{_sha(p)}` |" for p in (
        args.us_dic, args.us_aff, args.gb_dic, args.gb_aff)]
    (OUT / "PROVENANCE.md").write_text("\n".join([
        "# Spell-check word lists: provenance",
        "",
        "Built files; do not edit by hand. Rebuild with "
        "`python3 tools/build_spelling_wordlists.py` (its docstring has "
        "the arguments).",
        "",
        f"- Built: {args.date}, producer: deterministic "
        "(`tools/build_spelling_wordlists.py`, affix expansion in plain "
        "Python).",
        f"- Source: {args.source_note or 'UNKNOWN'}",
        "- Upstream: SCOWL (Spell Checker Oriented Word Lists), Kevin "
        "Atkinson, http://wordlist.aspell.net/ ; the `-large` Hunspell "
        "dictionaries are SCOWL's size-70 lists. SCOWL release: UNKNOWN "
        "(the source folder carried no README or version file).",
        "- Licence: SCOWL's own permissive notice (use, copy, modify, "
        "distribute, with the copyright notice kept). The notice file "
        "did not travel with the source files, so its exact text is not "
        "reproduced here; it is at "
        "http://wordlist.aspell.net/scowl-readme/ .",
        "",
        "| source file | sha256 |",
        "|---|---|",
        *sources,
        "",
        "| built file | words | sha256 |",
        "|---|---|---|",
        *rows,
        "",
        "`en-extra.txt` is not built: it is a short list kept by hand "
        "(producer: human and agent edits, reviewed in git) of general "
        "English and scholarly words the lists above lack, each met in "
        "a real manuscript. First entries: 2026-11-25, from the agent's "
        "own knowledge of their spelling, not checked against a "
        "published dictionary.",
        "",
    ]), encoding="utf-8")
    for row in rows:
        print(row)


if __name__ == "__main__":
    main()
