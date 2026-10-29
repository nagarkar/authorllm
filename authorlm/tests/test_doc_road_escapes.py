"""Regression (#152): Doc-road escapes the pull left in local files.

Measured on SMSTTD 2026-10-05 from the raw markdown export:

- Google's exporter now escapes the math DELIMITERS too — `\\$A\\$`,
  `\\$\\$ \\\\mathbf{V}\\_{A,t} \\= … \\$\\$`. The escaped delimiters hid
  every span from map_math, so nothing inside was unescaped and the
  file landed with literal `\\$` (81 lines of mixing.md; LaTeX then
  failed on `\\mathbf` outside math mode).
- A list item ending in `\\` (literal before the next item) exports as
  `\\\\` plus two trailing spaces. _HARD_BREAK took the spaces for a
  stanza break because an ordered-list marker (`2.`) was not in its
  block-opener lookahead, so `law.\\` came back as `law.\\\\`.

Both broke the push→pull round trip and showed as false CONFLICTs.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.gdocs import normalize_markdown, unescape_export_math


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def pull(raw: str) -> str:
    return normalize_markdown(unescape_export_math(raw))


def main() -> None:
    print("escaped math delimiters:")
    raw = ("Consider populations \\$A\\$ and \\$B\\$, containing \\$N\\_A\\$ "
           "members, vectors \\$\\\\mathbf{P}\\_{A,t}\\$.\n\n"
           "\\$\\$ \\\\mathbf{V}\\_{A,t} \\= \\\\mathbf{P}\\_{A,t} \\- "
           "\\\\mathbf{P}\\_{A,t-1} \\$\\$\n")
    local = ("Consider populations $A$ and $B$, containing $N_A$ "
             "members, vectors $\\mathbf{P}_{A,t}$.\n\n"
             "$$ \\mathbf{V}_{A,t} = \\mathbf{P}_{A,t} - "
             "\\mathbf{P}_{A,t-1} $$\n")
    got = pull(raw)
    check("escaped inline and display spans pull back to the TeX",
          got == local, repr(got))
    check("the pulled text is a fixed point of normalize",
          normalize_markdown(got) == got)
    check("unescaped delimiters still work (older exporter)",
          pull("written $m$ and \\$\\$ x \\$\\$\n") == "written $m$ and $$ x $$\n",
          repr(pull("written $m$ and \\$\\$ x \\$\\$\n")))
    check("an escaped price is not math",
          pull("costs \\$5 and \\$10 today\n") == "costs \\$5 and \\$10 today\n",
          repr(pull("costs \\$5 and \\$10 today\n")))

    print("trailing backslash before a list item:")
    raw = ("1. **Determinism** — fate, prophecy, or law.\\\\  \n"
           "2. **Essentialism** — the named.\\\\  \n"
           "3. **Exceptionalism** — Nature.\n")
    local = ("1. **Determinism** — fate, prophecy, or law.\\\n"
             "2. **Essentialism** — the named.\\\n"
             "3. **Exceptionalism** — Nature.\n")
    got = pull(raw)
    check("a list item's literal backslash round-trips", got == local, repr(got))
    check("trailing spaces before an ordered item are not a hard break",
          normalize_markdown("one.  \n2. two\n") == "one.\n2. two\n",
          repr(normalize_markdown("one.  \n2. two\n")))
    check("stanza hard breaks still become backslash breaks",
          normalize_markdown("Line one,  \nLine two.\n")
          == "Line one,\\\nLine two.\n")
    check("a stanza line that starts with a year still breaks",
          normalize_markdown("In the year  \n1916 the dead came.\n")
          == "In the year\\\n1916 the dead came.\n",
          repr(normalize_markdown("In the year  \n1916 the dead came.\n")))
    print("all ok")


if __name__ == "__main__":
    main()
