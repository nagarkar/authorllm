"""Fenced code must survive normalize_markdown byte-for-byte.

push_doc writes normalize_markdown back to disk whenever the bytes
move. The prose rules (_ESCAPE, _BULLET, _TABLE_DELIM, …) used to run
straight through fence bodies, so a documentation example of markdown
tables, star bullets, or backslash-escapes was silently rewritten on
every Doc push — and a fence-only table delimiter forced the table
rebuild path, orphaning open margin-thread anchors.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.gdocs import _TABLE_DELIM, _lift_fences, normalize_markdown


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def main() -> None:
    print("fences are opaque to prose rules:")
    src = "Demo:\n\n```md\n* one\n* two\n```\n\nProse.\n"
    out = normalize_markdown(src)
    check("`*` bullets inside a fence stay `*`", out == src, repr(out))
    check("idempotent", normalize_markdown(out) == out)

    esc = "Demo:\n\n```md\nUse \\* and \\# and \\[x\\]\n```\n\nProse.\n"
    check("backslash escapes inside a fence stay escaped",
          normalize_markdown(esc) == esc, repr(normalize_markdown(esc)))

    table_fence = (
        "# Essay\n\n"
        "```markdown\n"
        "| left | right |\n"
        "| :--- | ---: |\n"
        "| a | b |\n"
        "```\n\n"
        "That is the point.\n"
    )
    got = normalize_markdown(table_fence)
    check("aligned table delimiter inside a fence is preserved",
          "| :--- | ---: |" in got
          and "```markdown\n| left | right |\n| :--- | ---: |\n| a | b |\n```"
          in got,
          repr(got))

    tilde = "Ex:\n\n~~~\n* star\n| :--- |\n~~~\n\nDone.\n"
    check("tilde fences are preserved too",
          normalize_markdown(tilde) == tilde, repr(normalize_markdown(tilde)))

    print("prose rules still apply outside fences:")
    prose = (
        "Prose list:\n\n"
        "* one\n\n"
        "Real table:\n\n"
        "| a | b |\n"
        "| :--- | ---: |\n"
        "| c | d |\n"
    )
    out = normalize_markdown(prose)
    check("real `*` bullets still convert", out.startswith("Prose list:\n\n- one\n"),
          repr(out))
    check("real table delimiters still canonicalize",
          "| --- | --- |" in out and "| :--- | ---: |" not in out, repr(out))

    print("has_table ignores fence-only delimiters:")
    fence_only = table_fence
    prose_only, _ = _lift_fences(fence_only)
    check("raw fence-only text still matches _TABLE_DELIM",
          bool(_TABLE_DELIM.search(fence_only)))
    check("lifted prose does not", _TABLE_DELIM.search(prose_only) is None)
    with_real = fence_only + "\n| h |\n| --- |\n| v |\n"
    check("a real table outside the fence is still detected",
          bool(_TABLE_DELIM.search(_lift_fences(with_real)[0])))

    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
