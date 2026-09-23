"""Regression: spaced CommonMark thematic breaks must survive normalize.

`push_doc` writes `normalize_markdown` back to disk. The bullet rule
`^(\\s*)\\*\\s+` used to eat the first `* ` of a `* * *` break and leave
`- * *` — a list item, not a rule — permanently corrupting section
breaks on every Doc push.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.gdocs import normalize_markdown


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def main() -> None:
    print("spaced thematic breaks:")
    out = normalize_markdown("Before.\n\n* * *\n\nAfter.\n")
    check("`* * *` becomes padded ---", out == "Before.\n\n---\n\nAfter.\n",
          repr(out))
    check("idempotent on the result", normalize_markdown(out) == out)

    for raw in ("_ _ _", "- - -", "***", "___", "*  *  *"):
        got = normalize_markdown(f"A\n\n{raw}\n\nB\n")
        check(f"{raw!r} becomes ---", got == "A\n\n---\n\nB\n", repr(got))

    print("bullets and emphasis still work:")
    bullets = normalize_markdown("List:\n\n* one\n* two\n")
    check("real `*` bullets still convert",
          bullets == "List:\n\n- one\n- two\n", repr(bullets))

    emph = normalize_markdown("Lead.\n\n*emph* still prose.\n")
    check("emphasis line is not treated as a break",
          "*emph*" in emph and "- emph" not in emph, repr(emph))

    table = normalize_markdown("| a | b |\n| --- | --- |\n| 1 | 2 |\n")
    check("table delimiter row survives",
          "| --- | --- |" in table, repr(table))

    print("\nAll normalize thematic-break checks passed.")


if __name__ == "__main__":
    main()
