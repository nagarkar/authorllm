"""`book._fenced` is the JSON fence every pack embeds for structured
payloads. Wrong fencing breaks the role's parse of what it was handed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.book import _fenced


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_fenced_wraps_indented_json() -> None:
    value = {"a": 1, "note": "café"}
    out = _fenced(value)
    check("opens with a json fence", out.startswith("```json\n"), out[:20])
    check("closes with a fence on its own line", out.endswith("\n```"), out[-5:])
    inner = out[len("```json\n"):-len("\n```")]
    check("body is indented JSON round-trippable",
          json.loads(inner) == value and "  " in inner, inner)
    check("non-ascii is kept (ensure_ascii=False)",
          "café" in out, out)


def main() -> None:
    print("test_fenced_wraps_indented_json:")
    test_fenced_wraps_indented_json()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
